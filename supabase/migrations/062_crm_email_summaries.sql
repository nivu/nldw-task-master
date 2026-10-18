-- 062_crm_email_summaries.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-SUM-01 to -12, FR-CRMAI-CHG-01,
-- -11 (summaries); NFR-CRMAI-01, -05; Q-06, Q-07, Q-25, Q-34; data-model
-- §3.3, §4, §5; plan §5.5.
--
-- Each labelled client thread gets a short AI summary: what was said, the
-- other side's asks, Nunnari's commitments, a suggested next step and the
-- sentiment. The email text itself is never stored (P-3): only what the
-- model wrote, in small columns with length CHECKs, so a mistake that tried
-- to store a body fails loudly.
--
-- 1. `crm_email_summaries` (log): one latest summary per thread, a thread
--    being a mailbox and a Gmail thread id (Q-06), with its watermark, the
--    Gmail id and time of the latest qualifying message it covered
--    (FR-CRMAI-SUM-02), and its provenance. Deleting the mailbox deletes its
--    summaries. A log, not a record: no audit trigger, because its text must
--    be removable (Q-34) and it is rewritten on every new message.
-- 2. `crm_email_summary_links` (log): the thread's companies, people and
--    every open deal its messages are linked to (FR-CRMAI-SUM-05, Q-07),
--    held to the link's own workspace by `crm_check_refs()`.
-- 3. `crm_pending_threads(workspace, limit)`: the threads due a summary,
--    oldest first, ids only: threads of active mailbox (FR-CRMAI-SUM-10)
--    holding a qualifying message (not internal-only, not an auto-reply or
--    bounce, linked to a company or person, FR-CRMAI-SUM-01), at least one
--    of them stored after the mailbox's backfill finished (FR-CRMAI-SUM-11,
--    Q-25), whose latest qualifying message is not the summary's watermark,
--    or that have no summary. Each comes with its qualifying messages'
--    Gmail ids in order (with each one's direction) and the records they
--    are linked to.
-- 4. `crm_store_summary(actor, plan)`: in one transaction, re-checks that
--    the plan's watermark is still the thread's latest qualifying message
--    (else CRM09: the next tick summarises it again), upserts the summary,
--    replaces its links and stores its Review Queue items through
--    `crm_review_upsert` (061), leaving out a commitment already suggested
--    on the thread under another target (FR-CRMAI-SUM-07). Actor kind `sync`.
-- 5. `crm_remove_emails()` replaced (039's): a thread of the mailbox left
--    with no stored message loses its summary in the same transaction
--    (FR-CRMAI-SUM-02).
-- 6. `crm_purge()` also deletes the summary links of a purged record, and
--    `crm_merge_companies()` moves the merged company's summary links to the
--    survivor (FR-CRMAI-CHG-11, data-model §4).
--
-- Replaced functions, copied from their latest definition in the rebased
-- tree (`grep -l "CREATE OR REPLACE FUNCTION public.<name>("
-- supabase/migrations/*.sql | sort | tail -1`): crm_remove_emails from
-- 039_crm_email.sql, crm_purge and crm_merge_companies from
-- 061_crm_review_queue.sql, with only the lines marked "062" added. 008 has
-- no migration after 050 that replaces any of them (T061).
--
-- RLS is on and the browser roles have no grant on either table; EXECUTE on
-- every function is revoked from PUBLIC, anon and authenticated and granted
-- to service_role (data-model §5).
--
-- Reversible: DROP FUNCTION crm_pending_threads(uuid, integer),
-- crm_store_summary(jsonb, jsonb); DROP TABLE crm_email_summary_links,
-- crm_email_summaries; re-run 039's crm_remove_emails and 061's crm_purge
-- and crm_merge_companies, with their grants.

-- ---------------------------------------------------------------------------
-- 1. crm_email_summaries — data-model §3.3 (log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_email_summaries (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id         uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    mailbox_id           uuid NOT NULL,
    thread_id            text NOT NULL CHECK (char_length(thread_id) <= 200),
    -- 2 to 3 plain sentences (FR-CRMAI-SUM-04); never a body.
    summary              text NOT NULL
                             CHECK (summary = btrim(summary)
                                    AND char_length(summary) BETWEEN 1 AND 600),
    -- Strings of at most 200 characters each (checked in Python). The byte
    -- caps leave room for ten of the longest the schema allows in any
    -- script (200 characters of up to 4 UTF-8 bytes each), so a valid
    -- answer is never refused here.
    asks                 jsonb NOT NULL DEFAULT '[]'
                             CHECK (jsonb_typeof(asks) = 'array'
                                    AND jsonb_array_length(asks) <= 10
                                    AND pg_column_size(asks) <= 16384),
    -- [{text, owner_id, owner_unknown, due_on}] (checked in Python).
    commitments          jsonb NOT NULL DEFAULT '[]'
                             CHECK (jsonb_typeof(commitments) = 'array'
                                    AND jsonb_array_length(commitments) <= 10
                                    AND pg_column_size(commitments) <= 16384),
    next_step            text CHECK (char_length(next_step) <= 500),
    next_step_on         date,
    sentiment            text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'concern')),
    -- FR-CRMAI-SUM-02: the latest qualifying message this summary covered.
    watermark_message_id text NOT NULL CHECK (char_length(watermark_message_id) <= 64),
    watermark_at         timestamptz NOT NULL,
    -- Provenance (P-2).
    model                text NOT NULL CHECK (char_length(model) <= 100),
    generated_at         timestamptz NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_email_summaries_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_email_summaries_mailbox FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES crm_mailboxes (workspace_id, id) ON DELETE CASCADE,
    -- Q-06: one latest summary per thread.
    CONSTRAINT crm_email_summaries_thread UNIQUE (mailbox_id, thread_id),
    CONSTRAINT crm_email_summaries_next_step
        CHECK (next_step_on IS NULL OR next_step IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS crm_email_summaries_recent
    ON crm_email_summaries (workspace_id, updated_at DESC);

ALTER TABLE crm_email_summaries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_email_summaries FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. crm_email_summary_links — data-model §3.3 (log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_email_summary_links (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    summary_id   uuid NOT NULL,
    record_type  text NOT NULL CHECK (record_type IN ('company', 'person', 'opportunity')),
    record_id    uuid NOT NULL,
    CONSTRAINT crm_email_summary_links_summary FOREIGN KEY (workspace_id, summary_id)
        REFERENCES crm_email_summaries (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_email_summary_links_target UNIQUE (summary_id, record_type, record_id)
);

CREATE INDEX IF NOT EXISTS crm_email_summary_links_record
    ON crm_email_summary_links (workspace_id, record_type, record_id);

-- A link's record must be in the link's own workspace (data-model §2).
DROP TRIGGER IF EXISTS crm_email_summary_links_check_refs ON crm_email_summary_links;
CREATE TRIGGER crm_email_summary_links_check_refs
    BEFORE INSERT OR UPDATE ON crm_email_summary_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

ALTER TABLE crm_email_summary_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_email_summary_links FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. crm_pending_threads — data-model §3.3 (FR-CRMAI-SUM-01, -02, -10, -11)
-- ---------------------------------------------------------------------------

-- `[{mailbox_id, thread_id, message_ids: [gmail ids, oldest first],
-- messages: [{gmail_id, direction, sent_at}] (the same, with who sent each,
-- so the prompt can tell the other side's asks from Nunnari's commitments),
-- latest: {gmail_id, sent_at}, links: [{record_type, record_id}]}]`, the threads
-- whose latest qualifying message is newer than their summary (or that have
-- none), oldest first, at most `p_limit`. Ids only.
CREATE OR REPLACE FUNCTION public.crm_pending_threads(p_workspace uuid, p_limit integer)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    WITH qualifying AS (
        SELECT m.id, m.mailbox_id, m.thread_id, m.gmail_message_id, m.sent_at, m.direction,
               mb.backfilled_at IS NOT NULL AND m.created_at > mb.backfilled_at AS after_backfill
        FROM crm_email_messages m
        JOIN crm_mailboxes mb ON mb.workspace_id = m.workspace_id AND mb.id = m.mailbox_id
        WHERE m.workspace_id = p_workspace AND mb.is_active
          AND NOT m.is_internal AND NOT m.is_auto
          AND EXISTS (
              SELECT 1 FROM crm_email_links l
              WHERE l.workspace_id = m.workspace_id AND l.message_id = m.id
                AND l.record_type IN ('company', 'person')
          )
    ), threads AS (
        SELECT q.mailbox_id, q.thread_id,
               jsonb_agg(q.gmail_message_id ORDER BY q.sent_at, q.gmail_message_id)
                   AS message_ids,
               jsonb_agg(jsonb_build_object('gmail_id', q.gmail_message_id,
                                            'direction', q.direction, 'sent_at', q.sent_at)
                         ORDER BY q.sent_at, q.gmail_message_id) AS messages,
               (array_agg(q.gmail_message_id ORDER BY q.sent_at DESC, q.gmail_message_id DESC))[1]
                   AS latest_id,
               max(q.sent_at) AS latest_at,
               bool_or(q.after_backfill) AS after_backfill
        FROM qualifying q
        GROUP BY q.mailbox_id, q.thread_id
    ), pending AS (
        SELECT t.*
        FROM threads t
        LEFT JOIN crm_email_summaries s
            ON s.mailbox_id = t.mailbox_id AND s.thread_id = t.thread_id
        WHERE t.after_backfill
          AND (s.id IS NULL
               OR (t.latest_id <> s.watermark_message_id AND t.latest_at >= s.watermark_at))
        ORDER BY t.latest_at, t.mailbox_id, t.thread_id
        LIMIT greatest(coalesce(p_limit, 0), 0)
    )
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'mailbox_id', p.mailbox_id,
               'thread_id', p.thread_id,
               'message_ids', p.message_ids,
               'messages', p.messages,
               'latest', jsonb_build_object('gmail_id', p.latest_id, 'sent_at', p.latest_at),
               'links', coalesce((
                   SELECT jsonb_agg(DISTINCT jsonb_build_object(
                              'record_type', l.record_type, 'record_id', l.record_id))
                   FROM qualifying q
                   JOIN crm_email_links l
                       ON l.workspace_id = p_workspace AND l.message_id = q.id
                   WHERE q.mailbox_id = p.mailbox_id AND q.thread_id = p.thread_id
               ), '[]'::jsonb)
           ) ORDER BY p.latest_at, p.mailbox_id, p.thread_id), '[]'::jsonb)
    FROM pending p;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_store_summary — data-model §3.3 (FR-CRMAI-SUM-02, -04 to -07)
-- ---------------------------------------------------------------------------

-- `p_plan`: {mailbox_id, thread_id, summary, asks, commitments, next_step,
-- next_step_on, sentiment, watermark: {gmail_id, sent_at}, model,
-- generated_at, links: [{record_type, record_id}], items: [review item]}.
-- Returns `{id, items: [{id, created}]}`.
CREATE OR REPLACE FUNCTION public.crm_store_summary(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws      uuid := (p_actor ->> 'workspace_id')::uuid;
    mailbox uuid;
    thread  text := p_plan ->> 'thread_id';
    latest  text;
    made    uuid;
    link    jsonb;
    items   jsonb;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('sync', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    BEGIN
        mailbox := (p_plan ->> 'mailbox_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such mailbox.';
    END;
    IF mailbox IS NULL OR thread IS NULL
       OR NOT EXISTS (SELECT 1 FROM crm_mailboxes WHERE id = mailbox AND workspace_id = ws) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such mailbox.';
    END IF;

    -- FR-CRMAI-SUM-02: the watermark must still be the thread's latest
    -- qualifying message; one stored or removed meanwhile is summarised
    -- again by the next tick. The row is locked FOR SHARE so a concurrent
    -- crm_remove_emails either waits for this commit (and then deletes the
    -- summary of the emptied thread) or has removed it first (CRM09 here).
    SELECT m.gmail_message_id INTO latest
    FROM crm_email_messages m
    WHERE m.workspace_id = ws AND m.mailbox_id = mailbox AND m.thread_id = thread
      AND NOT m.is_internal AND NOT m.is_auto
      AND EXISTS (
          SELECT 1 FROM crm_email_links l
          WHERE l.workspace_id = m.workspace_id AND l.message_id = m.id
            AND l.record_type IN ('company', 'person')
      )
    ORDER BY m.sent_at DESC, m.gmail_message_id DESC
    LIMIT 1
    FOR SHARE OF m;
    IF latest IS NULL OR latest IS DISTINCT FROM p_plan #>> '{watermark,gmail_id}' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'This thread changed while it was summarised.';
    END IF;

    INSERT INTO crm_email_summaries AS s (
        workspace_id, mailbox_id, thread_id, summary, asks, commitments, next_step,
        next_step_on, sentiment, watermark_message_id, watermark_at, model, generated_at
    )
    VALUES (
        ws, mailbox, thread,
        p_plan ->> 'summary',
        coalesce(p_plan -> 'asks', '[]'),
        coalesce(p_plan -> 'commitments', '[]'),
        p_plan ->> 'next_step',
        (p_plan ->> 'next_step_on')::date,
        p_plan ->> 'sentiment',
        latest,
        (p_plan #>> '{watermark,sent_at}')::timestamptz,
        p_plan ->> 'model',
        (p_plan ->> 'generated_at')::timestamptz
    )
    ON CONFLICT (mailbox_id, thread_id) DO UPDATE
    SET summary = EXCLUDED.summary,
        asks = EXCLUDED.asks,
        commitments = EXCLUDED.commitments,
        next_step = EXCLUDED.next_step,
        next_step_on = EXCLUDED.next_step_on,
        sentiment = EXCLUDED.sentiment,
        watermark_message_id = EXCLUDED.watermark_message_id,
        watermark_at = EXCLUDED.watermark_at,
        model = EXCLUDED.model,
        generated_at = EXCLUDED.generated_at,
        updated_at = now()
    RETURNING s.id INTO made;

    -- FR-CRMAI-SUM-05: the links are replaced, each held to this workspace
    -- by crm_check_refs (CRM04).
    DELETE FROM crm_email_summary_links WHERE summary_id = made;
    FOR link IN SELECT value FROM jsonb_array_elements(coalesce(p_plan -> 'links', '[]')) LOOP
        INSERT INTO crm_email_summary_links (workspace_id, summary_id, record_type, record_id)
        VALUES (ws, made, link ->> 'record_type', (link ->> 'record_id')::uuid)
        ON CONFLICT (summary_id, record_type, record_id) DO NOTHING;
    END LOOP;

    -- FR-CRMAI-SUM-06, -07: the suggestions, through the Review Queue. A
    -- commitment already suggested on this thread (an item of any status with
    -- its fingerprint) under another target, because the thread's records
    -- changed, is not suggested again.
    SELECT coalesce(jsonb_agg(i.value ORDER BY i.n), '[]')
    INTO items
    FROM jsonb_array_elements(coalesce(p_plan -> 'items', '[]')) WITH ORDINALITY AS i(value, n)
    WHERE NOT (
        i.value ->> 'type' = 'task_from_commitment'
        AND EXISTS (
            SELECT 1 FROM crm_review_items r
            WHERE r.workspace_id = ws
              AND r.type = 'task_from_commitment'
              AND r.source_kind = i.value ->> 'source_kind'
              AND r.source_key = i.value ->> 'source_key'
              AND r.source_version = i.value ->> 'source_version'
              AND r.dedupe_key <> i.value ->> 'dedupe_key'
        )
    );
    items := crm_review_upsert(p_actor, items);
    RETURN jsonb_build_object('id', made, 'items', items);
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_remove_emails — 039's, plus the summary of an emptied thread
-- ---------------------------------------------------------------------------

-- FR-CRM-SYNC-09: the stored copies of messages whose label was removed (or
-- that were deleted in Gmail). Returns `{removed, records: [{record_type,
-- record_id}]}`: what they were linked to, read before the links cascade.
CREATE OR REPLACE FUNCTION public.crm_remove_emails(
    p_workspace uuid, p_mailbox uuid, p_ids text[]
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    records jsonb;
    removed integer;
    -- 062: the threads the removed messages were on.
    threads text[];
BEGIN
    SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object(
               'record_type', l.record_type, 'record_id', l.record_id)), '[]')
    INTO records
    FROM crm_email_links l
    JOIN crm_email_messages m ON m.workspace_id = l.workspace_id AND m.id = l.message_id
    WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
      AND m.gmail_message_id = ANY (coalesce(p_ids, '{}'));

    SELECT coalesce(array_agg(DISTINCT m.thread_id), '{}')
    INTO threads
    FROM crm_email_messages m
    WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
      AND m.gmail_message_id = ANY (coalesce(p_ids, '{}'));

    DELETE FROM crm_email_messages
    WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox
      AND gmail_message_id = ANY (coalesce(p_ids, '{}'));
    GET DIAGNOSTICS removed = ROW_COUNT;

    -- 062 (009 FR-CRMAI-SUM-02): a thread left with no stored message loses
    -- its summary (its links and nothing else go with it, by CASCADE).
    DELETE FROM crm_email_summaries s
    WHERE s.workspace_id = p_workspace AND s.mailbox_id = p_mailbox
      AND s.thread_id = ANY (threads)
      AND NOT EXISTS (
          SELECT 1 FROM crm_email_messages m
          WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
            AND m.thread_id = s.thread_id
      );

    RETURN jsonb_build_object('removed', removed, 'records', records);
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. Record lifecycle — data-model §4 (FR-CRMAI-CHG-11)
-- ---------------------------------------------------------------------------

-- 061's crm_purge, with the summary links of a purged record deleted (062).

CREATE OR REPLACE FUNCTION public.crm_purge(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws     uuid := (p_actor ->> 'workspace_id')::uuid;
    item   jsonb;
    tbl    text;
    target uuid;
    rtype  text;
    gone   uuid;
    purged integer := 0;
    unlinked uuid[];
    p      record;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    FOR item IN SELECT value FROM jsonb_array_elements(coalesce(p_plan -> 'items', '[]')) LOOP
        tbl := item ->> 'table';
        target := (item ->> 'id')::uuid;
        -- The record tables that exist so far, and the record type their
        -- links name.
        rtype := CASE tbl
            WHEN 'crm_companies' THEN 'company'
            WHEN 'crm_people' THEN 'person'
            WHEN 'crm_opportunities' THEN 'opportunity'
            WHEN 'tasks' THEN 'task'
            WHEN 'crm_notes' THEN 'note'
            WHEN 'crm_campaigns' THEN 'campaign'
            WHEN 'crm_drive_links' THEN NULL
            ELSE 'unknown'
        END;
        IF rtype = 'unknown' OR target IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That cannot be purged.';
        END IF;

        -- Only from the Trash of the actor's own workspace.
        EXECUTE format('SELECT id FROM %I WHERE id = $1 AND workspace_id = $2 '
                       'AND deleted_at IS NOT NULL FOR UPDATE', tbl)
        INTO gone USING target, ws;
        IF gone IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such record in the Trash.';
        END IF;

        -- FR-CRM-DEL-03: a deal with a TVA project is never deleted.
        IF tbl = 'crm_opportunities'
           AND EXISTS (SELECT 1 FROM projects WHERE source_opportunity_id = target) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This deal has a TVA project; mark it Closed Lost instead.';
        END IF;
        -- FR-CRM-DEL-02: the company's TVA projects keep their free-text
        -- client and lose the link, as a TVA row.
        IF tbl = 'crm_companies' THEN
            FOR p IN
                UPDATE projects SET crm_company_id = NULL
                WHERE crm_company_id = target
                RETURNING id
            LOOP
                PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', p.id::text,
                    jsonb_build_object('crm_company_id', target),
                    jsonb_build_object('crm_company_id', NULL));
            END LOOP;
        END IF;

        IF rtype IS NOT NULL THEN
            DELETE FROM crm_drive_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 008 FR-TASK-DATA-14: a task of this workspace left with no CRM
            -- link goes too, live or in the Trash; it never becomes personal.
            SELECT coalesce(array_agg(task_id), '{}') INTO unlinked
            FROM task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            DELETE FROM task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            DELETE FROM tasks k
            WHERE k.workspace_id = ws AND k.id = ANY (unlinked)
              AND NOT EXISTS (
                  SELECT 1 FROM task_links l
                  WHERE l.task_id = k.id AND l.record_type <> 'tva_project'
              );
            DELETE FROM crm_note_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 034: the favourites that pin it.
            DELETE FROM crm_favourites
            WHERE workspace_id = ws AND target_type = 'record' AND record_type = rtype
              AND target_id = target;
            -- 039: the stored emails' links to it (the messages stay, linked
            -- to whatever else they name).
            DELETE FROM crm_email_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 061 (009 FR-CRMAI-CHG-11): the Review Queue items that target
            -- it, whatever their status. A purged deal's place among other
            -- items' deal choices goes by its foreign key (CASCADE).
            DELETE FROM crm_review_items
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 062 (009 FR-CRMAI-CHG-11): the email summaries' links to it (the
            -- summary stays, linked to whatever else its thread names).
            DELETE FROM crm_email_summary_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
        END IF;
        -- 041: a membership naming both this and a person (or a company)
        -- stays with the other one, unless that one already has its own in
        -- the campaign; the rest go with the record (FK CASCADE). A
        -- campaign's members go with it.
        -- 042: when the shared membership is the Converted one and the other
        -- one's own is not, the other one's goes instead, so the campaign
        -- keeps its conversion (FR-CRM-CAMP-05).
        IF tbl = 'crm_companies' THEN
            DELETE FROM crm_campaign_members o
            USING crm_campaign_members m
            WHERE m.workspace_id = ws AND m.company_id = target AND m.person_id IS NOT NULL
              AND m.status = 'converted'
              AND o.campaign_id = m.campaign_id AND o.company_id IS NULL
              AND o.person_id = m.person_id AND o.status <> 'converted';
            DELETE FROM crm_campaign_members m
            WHERE m.workspace_id = ws AND m.company_id = target AND m.person_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM crm_campaign_members o
                          WHERE o.campaign_id = m.campaign_id AND o.company_id IS NULL
                            AND o.person_id = m.person_id);
            UPDATE crm_campaign_members SET company_id = NULL
            WHERE workspace_id = ws AND company_id = target AND person_id IS NOT NULL;
        ELSIF tbl = 'crm_people' THEN
            DELETE FROM crm_campaign_members o
            USING crm_campaign_members m
            WHERE m.workspace_id = ws AND m.person_id = target AND m.company_id IS NOT NULL
              AND m.status = 'converted'
              AND o.campaign_id = m.campaign_id AND o.person_id IS NULL
              AND o.company_id = m.company_id AND o.status <> 'converted';
            DELETE FROM crm_campaign_members m
            WHERE m.workspace_id = ws AND m.person_id = target AND m.company_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM crm_campaign_members o
                          WHERE o.campaign_id = m.campaign_id AND o.person_id IS NULL
                            AND o.company_id = m.company_id);
            UPDATE crm_campaign_members SET person_id = NULL
            WHERE workspace_id = ws AND person_id = target AND company_id IS NOT NULL;
        END IF;
        EXECUTE format('DELETE FROM %I WHERE id = $1 AND workspace_id = $2', tbl)
        USING target, ws;
        purged := purged + 1;
    END LOOP;

    RETURN jsonb_build_object('purged', purged);
END;
$$;


-- 061's crm_merge_companies, with the merged company's summary links moved (062).

CREATE OR REPLACE FUNCTION public.crm_merge_companies(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws         uuid := (p_actor ->> 'workspace_id')::uuid;
    who        uuid := (p_actor ->> 'user_id')::uuid;
    keep_id    uuid;
    gone_id    uuid;
    keep       crm_companies%ROWTYPE;
    gone       crm_companies%ROWTYPE;
    changes    jsonb := coalesce(p_plan -> 'survivor_changes', '{}');
    -- The fields a person chooses between (FR-CRM-DUP-03); everything else
    -- is the system's or moves on its own.
    choosable  text[] := ARRAY[
        'name', 'entities', 'lead_stage', 'account_type_id', 'relationship_status_id',
        'channel_ids', 'owner_id', 'next_touch_on', 'industry_id', 'institution_type_id',
        'size_id', 'country', 'city', 'address', 'website', 'linkedin', 'description',
        'source_id'
    ];
    cols       text[];
    setlist    text;
    wanted     text := nullif(btrim(coalesce(p_plan ->> 'primary_domain', '')), '');
    r          record;
    s          crm_campaign_members%ROWTYPE;
    n_people   integer := 0;
    n_deals    integer := 0;
    n_tasks    integer := 0;
    n_notes    integer := 0;
    n_links    integer := 0;
    n_domains  integer := 0;
    n_projects integer := 0;
    n_emails   integer := 0;
    n_members  integer := 0;
    -- 061: a review item's dedupe key, before and after the move.
    old_head   text;
    new_key    text;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    BEGIN
        keep_id := (p_plan ->> 'survivor_id')::uuid;
        gone_id := (p_plan ->> 'merged_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END;
    IF keep_id IS NULL OR gone_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Name the two companies to merge.';
    END IF;
    IF keep_id = gone_id THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Pick two different companies to merge.';
    END IF;
    IF jsonb_typeof(changes) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'The chosen values are not valid.';
    END IF;

    -- Both companies, locked in id order (so two merges of the same pair
    -- queue instead of deadlocking), in the actor's workspace only.
    PERFORM 1 FROM crm_companies
    WHERE id IN (keep_id, gone_id) AND workspace_id = ws
    ORDER BY id
    FOR UPDATE;
    SELECT * INTO keep FROM crm_companies WHERE id = keep_id AND workspace_id = ws;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END IF;
    SELECT * INTO gone FROM crm_companies WHERE id = gone_id AND workspace_id = ws;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END IF;
    IF keep.merged_into_id IS NOT NULL OR gone.merged_into_id IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
            '“%s” was already merged into another company.',
            CASE WHEN keep.merged_into_id IS NOT NULL THEN keep.name ELSE gone.name END);
    END IF;
    IF keep.deleted_at IS NOT NULL OR gone.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
            '“%s” is in the Trash; restore it before merging.',
            CASE WHEN keep.deleted_at IS NOT NULL THEN keep.name ELSE gone.name END);
    END IF;
    IF ((p_plan -> 'expect' ->> 'survivor_updated_at') IS NOT NULL
        AND keep.updated_at IS DISTINCT FROM
            (p_plan -> 'expect' ->> 'survivor_updated_at')::timestamptz)
       OR ((p_plan -> 'expect' ->> 'merged_updated_at') IS NOT NULL
        AND gone.updated_at IS DISTINCT FROM
            (p_plan -> 'expect' ->> 'merged_updated_at')::timestamptz) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'One of these companies changed meanwhile; reload and try again.';
    END IF;
    SELECT array_agg(k ORDER BY k) INTO cols FROM jsonb_object_keys(changes) AS k;
    IF cols IS NOT NULL AND NOT cols <@ choosable THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Only a company''s own fields can be chosen in a merge.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- 1. The chosen values.
    IF cols IS NOT NULL THEN
        SELECT string_agg(format('%1$I = r.%1$I', c), ', ') INTO setlist FROM unnest(cols) AS c;
        EXECUTE format('UPDATE crm_companies t SET %s, updated_by = $4 '
                       'FROM jsonb_populate_record(NULL::crm_companies, $1) r '
                       'WHERE t.id = $2 AND t.workspace_id = $3', setlist)
        USING changes, keep_id, ws, who;
    END IF;

    -- The projects linked to the merged company, before its deals move theirs.
    SELECT count(*) INTO n_projects FROM projects WHERE crm_company_id = gone_id;

    -- 2. People and deals, Trash included. A deal's project follows it to the
    --    survivor (033's crm_opportunities_project_company trigger).
    UPDATE crm_people SET company_id = keep_id, updated_by = who
    WHERE workspace_id = ws AND company_id = gone_id;
    GET DIAGNOSTICS n_people = ROW_COUNT;
    UPDATE crm_opportunities SET company_id = keep_id, updated_by = who
    WHERE workspace_id = ws AND company_id = gone_id;
    GET DIAGNOSTICS n_deals = ROW_COUNT;

    -- Task and note links: an addition on the survivor, a removal from the
    -- merged company; one already linked to both keeps the survivor's.
    FOR r IN
        SELECT * FROM task_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM task_links WHERE task_id = r.task_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO task_links (workspace_id, task_id, record_type, record_id,
                                        created_at, created_by)
            VALUES (ws, r.task_id, 'company', keep_id, r.created_at, r.created_by);
        END IF;
        DELETE FROM task_links WHERE id = r.id;
        n_tasks := n_tasks + 1;
    END LOOP;
    FOR r IN
        SELECT * FROM crm_note_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_note_links WHERE note_id = r.note_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_note_links (workspace_id, note_id, record_type, record_id,
                                        created_at, created_by)
            VALUES (ws, r.note_id, 'company', keep_id, r.created_at, r.created_by);
        END IF;
        DELETE FROM crm_note_links WHERE id = r.id;
        n_notes := n_notes + 1;
    END LOOP;

    -- 039: email links, as task and note links; a message linked to both
    -- keeps the survivor's. A log, not audited (data-model §3.22).
    FOR r IN
        SELECT * FROM crm_email_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_email_links WHERE message_id = r.message_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_email_links (workspace_id, message_id, record_type, record_id,
                                         created_at)
            VALUES (ws, r.message_id, 'company', keep_id, r.created_at);
        END IF;
        DELETE FROM crm_email_links WHERE id = r.id;
        n_emails := n_emails + 1;
    END LOOP;

    -- 062 (009 FR-CRMAI-CHG-11): email summary links, as email links; a
    -- summary linked to both keeps the survivor's. A log, not audited.
    FOR r IN
        SELECT * FROM crm_email_summary_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_email_summary_links WHERE summary_id = r.summary_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_email_summary_links (workspace_id, summary_id, record_type,
                                                 record_id)
            VALUES (ws, r.summary_id, 'company', keep_id);
        END IF;
        DELETE FROM crm_email_summary_links WHERE id = r.id;
    END LOOP;

    -- 061 (009 FR-CRMAI-CHG-11): the merged company's open Review Queue
    -- items move to the survivor, their dedupe key's target recomputed
    -- (`<source_kind>|<source_key>|<type>|company:<id>…`, data-model §3.2).
    -- One whose new key is already open on the survivor is expired instead,
    -- with the audit row an expiry writes. Decided items stay where they
    -- are. A log, not audited.
    FOR r IN
        SELECT * FROM crm_review_items
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
          AND status = 'open'
        ORDER BY created_at, id
    LOOP
        old_head := r.source_kind || '|' || r.source_key || '|' || r.type || '|company:'
                    || gone_id::text;
        new_key := CASE
            WHEN left(r.dedupe_key, char_length(old_head)) = old_head THEN
                r.source_kind || '|' || r.source_key || '|' || r.type || '|company:'
                || keep_id::text || substr(r.dedupe_key, char_length(old_head) + 1)
            ELSE r.dedupe_key
        END;
        IF EXISTS (SELECT 1 FROM crm_review_items
                   WHERE workspace_id = ws AND dedupe_key = new_key AND status = 'open'
                     AND id <> r.id) THEN
            UPDATE crm_review_items SET status = 'expired', decided_at = now()
            WHERE id = r.id;
            INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                   target_table, target_id, before, after, suggestion_id)
            VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.expired',
                    'crm_review_items', r.id::text, jsonb_build_object('status', 'open'),
                    jsonb_build_object('status', 'expired'), r.id);
        ELSE
            UPDATE crm_review_items SET record_id = keep_id, dedupe_key = new_key
            WHERE id = r.id;
        END IF;
    END LOOP;

    -- 041: campaign memberships, Trash included. One the survivor already
    -- has in the same campaign (with the same person, or none) keeps the
    -- survivor's; the merged company's goes, as a duplicate link does.
    -- 042: unless the merged company's is the Converted one and the
    -- survivor's is not: then the survivor's goes and the converted one
    -- moves, so the campaign keeps its conversion (FR-CRM-CAMP-05).
    FOR r IN
        SELECT * FROM crm_campaign_members
        WHERE workspace_id = ws AND company_id = gone_id
        ORDER BY id
    LOOP
        SELECT * INTO s FROM crm_campaign_members
        WHERE campaign_id = r.campaign_id AND company_id = keep_id
          AND person_id IS NOT DISTINCT FROM r.person_id;
        IF FOUND AND (s.status = 'converted' OR r.status <> 'converted') THEN
            DELETE FROM crm_campaign_members WHERE id = r.id;
        ELSE
            IF FOUND THEN
                DELETE FROM crm_campaign_members WHERE id = s.id;
            END IF;
            UPDATE crm_campaign_members SET company_id = keep_id, updated_by = who
            WHERE id = r.id;
        END IF;
        n_members := n_members + 1;
    END LOOP;

    -- Drive links are records of their own: they keep their id.
    UPDATE crm_drive_links SET record_id = keep_id, updated_by = who
    WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id;
    GET DIAGNOSTICS n_links = ROW_COUNT;

    -- 3. Domains, each unique in the workspace already, so none can clash.
    FOR r IN
        SELECT * FROM crm_company_domains
        WHERE workspace_id = ws AND company_id = gone_id
        ORDER BY domain
    LOOP
        DELETE FROM crm_company_domains WHERE id = r.id;
        INSERT INTO crm_company_domains (workspace_id, company_id, domain, is_primary,
                                         created_at, created_by)
        VALUES (ws, keep_id, r.domain, false, r.created_at, r.created_by);
        n_domains := n_domains + 1;
    END LOOP;
    IF wanted IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM crm_company_domains
                       WHERE company_id = keep_id AND domain = wanted) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'The primary domain must be one of the two companies'' domains.';
        END IF;
        -- The old primary steps down before the new one steps up (one
        -- primary per company).
        FOR r IN
            SELECT * FROM crm_company_domains
            WHERE company_id = keep_id AND is_primary AND domain <> wanted
        LOOP
            DELETE FROM crm_company_domains WHERE id = r.id;
            INSERT INTO crm_company_domains (workspace_id, company_id, domain, is_primary,
                                             created_at, created_by)
            VALUES (ws, keep_id, r.domain, false, r.created_at, r.created_by);
        END LOOP;
        FOR r IN
            SELECT * FROM crm_company_domains
            WHERE company_id = keep_id AND domain = wanted AND NOT is_primary
        LOOP
            DELETE FROM crm_company_domains WHERE id = r.id;
            INSERT INTO crm_company_domains (workspace_id, company_id, domain, is_primary,
                                             created_at, created_by)
            VALUES (ws, keep_id, r.domain, true, r.created_at, r.created_by);
        END LOOP;
    END IF;

    -- 4. TVA projects linked to the merged company directly: only the link
    --    changes (FR-CRM-DUP-04), as a TVA row.
    FOR r IN
        UPDATE projects SET crm_company_id = keep_id
        WHERE crm_company_id = gone_id
        RETURNING id
    LOOP
        PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', r.id::text,
            jsonb_build_object('crm_company_id', gone_id),
            jsonb_build_object('crm_company_id', keep_id));
    END LOOP;

    -- 5. The merged company resolves to the survivor and goes to the Trash.
    PERFORM set_config('crm.action', 'company.merged', true);
    UPDATE crm_companies
    SET merged_into_id = keep_id, deleted_at = now(), deleted_by = who,
        deleted_group = gen_random_uuid(), updated_by = who
    WHERE id = gone_id AND workspace_id = ws;
    PERFORM set_config('crm.action', '', true);

    -- The survivor's side of the audit: which company merged into it.
    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after)
    VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'company.merged',
            'crm_companies', keep_id::text, NULL,
            jsonb_build_object('merged_id', gone_id, 'merged_name', gone.name));

    SELECT * INTO keep FROM crm_companies WHERE id = keep_id;
    RETURN jsonb_build_object(
        'survivor', to_jsonb(keep),
        'moved', jsonb_build_object(
            'people', n_people, 'deals', n_deals, 'tasks', n_tasks, 'notes', n_notes,
            'links', n_links, 'domains', n_domains, 'projects', n_projects,
            'emails', n_emails, 'campaign_members', n_members));
END;
$$;


-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_pending_threads(uuid, integer) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_store_summary(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_remove_emails(uuid, uuid, text[])
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_pending_threads(uuid, integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_store_summary(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_remove_emails(uuid, uuid, text[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
