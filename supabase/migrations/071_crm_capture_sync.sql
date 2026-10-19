-- 071_crm_capture_sync.sql
--
-- Spec 010 (automatic email capture) FR-CAP-SRC-01, -05, -06, -07, -08,
-- FR-CAP-REV-01, FR-CAP-CHG-02, -04; data-model §3.1 to §3.3; plan §5.3 to
-- §5.5, PD-3, PD-15. Slice S1: known-contact capture and deal documents.
--
-- 1. `crm_mailboxes` gains `capture_source` (`labels`, today's behaviour and
--    the default for every existing mailbox, or `primary_sent`),
--    `capture_start_date` (default 2026-04-01, the backfill start for
--    `primary_sent`), `docs_checked_at` (the deal-document search's cursor)
--    and `reconcile_after` (set when a mailbox goes back from
--    `primary_sent` to `labels`, or its start date moves later while
--    capturing, so the next reconcile looks back to the earliest start date
--    captured from; cleared when that reconcile finishes on `labels`). The ALTER fills every existing
--    row with the defaults and writes no audit row (DDL).
-- 2. `crm_capture_candidates`: a log, one row per mailbox and Gmail thread
--    that reached the sync through Primary or Sent with no known participant
--    and no configured label. Ids and times only; no body, subject or
--    address column. A finding is kept only for a lead or a deal signal
--    (S2/S3). RLS on, no policies, no browser grants, no audit trigger.
-- 3. `crm_capture_mark(workspace, mailbox, rows)`: upserts one candidate per
--    thread from the sync's page, advancing the latest message only forward
--    and then re-opening the decision (FR-CAP-DIS-06); messages older than
--    365 days are ignored (PD-15). Refuses another workspace's mailbox
--    (CRM04). Returns the rows inserted or advanced. No actor (a log).
-- 4. `crm_audit_row()` replaced: the mailbox skip list gains
--    `docs_checked_at` and `reconcile_after`, so the sync's bookkeeping
--    writes no audit row; an Admin's change of `capture_source` or
--    `capture_start_date` is still audited as `mailbox.updated`.
-- 5. `crm_review_items_type_check` widened with `deal_signal`.
--
-- Replaced function, copied from its latest definition in the tree (`grep
-- -l "CREATE OR REPLACE FUNCTION public.crm_audit_row("
-- supabase/migrations/*.sql | sort | tail -1`): crm_audit_row from
-- 067_crm_contact_recency.sql, with only the lines marked "071" added.
--
-- EXECUTE on every function here is revoked from PUBLIC, anon and
-- authenticated and granted to service_role (data-model §5).
--
-- Reversible: DELETE FROM crm_review_items WHERE type = 'deal_signal' and
-- re-add 061's crm_review_items_type_check; re-run 067's crm_audit_row(),
-- with its grants; DROP FUNCTION crm_capture_mark(uuid, uuid, jsonb); DROP
-- TABLE crm_capture_candidates; ALTER TABLE crm_mailboxes DROP COLUMN
-- reconcile_after, DROP COLUMN docs_checked_at, DROP COLUMN
-- capture_start_date, DROP COLUMN capture_source.

-- ---------------------------------------------------------------------------
-- 1. crm_mailboxes — data-model §3.1
-- ---------------------------------------------------------------------------

ALTER TABLE crm_mailboxes
    ADD COLUMN IF NOT EXISTS capture_source text NOT NULL DEFAULT 'labels'
        CONSTRAINT crm_mailboxes_capture_source_check
        CHECK (capture_source IN ('labels', 'primary_sent')),
    ADD COLUMN IF NOT EXISTS capture_start_date date NOT NULL DEFAULT '2026-04-01',
    ADD COLUMN IF NOT EXISTS docs_checked_at timestamptz,
    ADD COLUMN IF NOT EXISTS reconcile_after timestamptz;

-- ---------------------------------------------------------------------------
-- 2. crm_capture_candidates — data-model §3.2 (a log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_capture_candidates (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    mailbox_id         uuid NOT NULL,
    thread_id          text NOT NULL CHECK (char_length(thread_id) BETWEEN 1 AND 200),
    latest_message_id  text NOT NULL CHECK (char_length(latest_message_id) BETWEEN 1 AND 200),
    latest_message_at  timestamptz NOT NULL,
    status             text NOT NULL DEFAULT 'pending' CHECK (status IN (
                           'pending', 'skipped', 'ignored', 'known', 'judged', 'failed'
                       )),
    attempts           smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    judged_message_id  text CHECK (char_length(judged_message_id) <= 200),
    outcome            text CHECK (outcome IN (
                           'new_lead', 'deal_signal', 'other_business', 'not_relevant'
                       )),
    confidence         text CHECK (confidence IN ('high', 'medium', 'low')),
    classifier_version text CHECK (char_length(classifier_version) <= 40),
    judged_at          timestamptz,
    dry_run            boolean NOT NULL DEFAULT false,
    finding            jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_capture_candidates_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_capture_candidates_once UNIQUE (mailbox_id, thread_id),
    CONSTRAINT crm_capture_candidates_mailbox
        FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES crm_mailboxes (workspace_id, id) ON DELETE CASCADE,
    -- A decision is whole or absent: a judged row has all of it, any other
    -- row none of it.
    CONSTRAINT crm_capture_candidates_judged CHECK (
        (status = 'judged' AND outcome IS NOT NULL AND confidence IS NOT NULL
         AND judged_at IS NOT NULL AND judged_message_id IS NOT NULL)
        OR (status <> 'judged' AND outcome IS NULL AND confidence IS NULL
            AND judged_at IS NULL AND judged_message_id IS NULL)
    ),
    -- NFR-CAP-02: a name or an address is kept only for a lead or a deal
    -- signal, and never more than 4 KB of it. `outcome IS NOT NULL` first:
    -- a NULL outcome would make the IN NULL, which a CHECK lets through.
    CONSTRAINT crm_capture_candidates_finding CHECK (
        finding IS NULL
        OR (outcome IS NOT NULL
            AND outcome IN ('new_lead', 'deal_signal')
            AND jsonb_typeof(finding) = 'object'
            AND pg_column_size(finding) <= 4096)
    )
);

CREATE INDEX IF NOT EXISTS crm_capture_candidates_pending
    ON crm_capture_candidates (workspace_id, status, latest_message_at);
CREATE INDEX IF NOT EXISTS crm_capture_candidates_report
    ON crm_capture_candidates (workspace_id, dry_run, outcome);

ALTER TABLE crm_capture_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_capture_candidates FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. crm_capture_mark — the sync's candidates (FR-CAP-SRC-05, DIS-06)
-- ---------------------------------------------------------------------------

-- p_rows: [{thread_id, message_id, sent_at}]. Several messages of one thread
-- are folded to its latest first: one statement may not touch a row twice.
CREATE OR REPLACE FUNCTION public.crm_capture_mark(
    p_workspace uuid, p_mailbox uuid, p_rows jsonb
)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    n integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM crm_mailboxes WHERE id = p_mailbox AND workspace_id = p_workspace
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such mailbox.';
    END IF;

    WITH given AS (
        SELECT DISTINCT ON (r ->> 'thread_id')
               r ->> 'thread_id' AS thread_id,
               r ->> 'message_id' AS message_id,
               (r ->> 'sent_at')::timestamptz AS sent_at
        FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS r
        -- PD-15: a message older than the retention window is not marked,
        -- so a re-read does not bring back what the purge removed.
        WHERE (r ->> 'sent_at')::timestamptz >= now() - interval '365 days'
        ORDER BY r ->> 'thread_id', (r ->> 'sent_at')::timestamptz DESC, r ->> 'message_id' DESC
    ), done AS (
        INSERT INTO crm_capture_candidates AS c
            (workspace_id, mailbox_id, thread_id, latest_message_id, latest_message_at)
        SELECT p_workspace, p_mailbox, g.thread_id, g.message_id, g.sent_at
        FROM given g
        ON CONFLICT (mailbox_id, thread_id) DO UPDATE SET
            latest_message_id = EXCLUDED.latest_message_id,
            latest_message_at = EXCLUDED.latest_message_at,
            status = 'pending',
            attempts = 0,
            judged_message_id = NULL,
            outcome = NULL,
            confidence = NULL,
            classifier_version = NULL,
            judged_at = NULL,
            finding = NULL,
            dry_run = false,
            updated_at = now()
        -- A later message re-opens the decision (FR-CAP-DIS-06); an older
        -- or the same one changes nothing.
        WHERE EXCLUDED.latest_message_at > c.latest_message_at
        RETURNING 1
    )
    SELECT count(*) INTO n FROM done;
    RETURN n;
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_audit_row — 067's, the mailbox's capture cursors skipped
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_audit_row()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    -- NEW is unassigned on DELETE and OLD on INSERT, so read rows as jsonb.
    new_j    jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    old_j    jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    r        jsonb := coalesce(new_j, old_j);
    changed  text[];
    before_j jsonb;
    after_j  jsonb;
    actor    jsonb;
    act      text;
    noun     text;
BEGIN
    -- Skip first, so a skippable write never needs an actor.
    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(array_agg(n.key ORDER BY n.key), '{}')
        INTO changed
        FROM jsonb_each(new_j) AS n
        WHERE n.key NOT IN ('updated_at', 'updated_by')
          AND n.value IS DISTINCT FROM old_j -> n.key;
        -- A select option's position is not kanban order: reordering a list
        -- is an admin settings change and is audited (FR-CRM-LIST-01, AUD-01).
        -- 039: a mailbox's sync cursor is the sync's own bookkeeping, written
        -- every page; the settings an admin changes are still audited.
        IF changed <@ ARRAY['last_used_at']
           -- 067 (009 FR-CRMAI-REL-02): a contact recency recompute is not an
           -- edit either.
           OR changed <@ ARRAY['flag_level', 'flags', 'flags_computed_at', 'contact_recency']
           OR (changed <@ ARRAY['position'] AND TG_TABLE_NAME <> 'crm_select_options')
           -- 071 (010 FR-CAP-SRC-06, PD-3): the deal-document cursor and
           -- the switch-back reconcile point are the sync's bookkeeping too.
           OR (TG_TABLE_NAME = 'crm_mailboxes'
               AND changed <@ ARRAY['history_id', 'page_token', 'backfilled_at',
                                    'last_synced_at', 'docs_checked_at',
                                    'reconcile_after']) THEN
            RETURN NULL;
        END IF;
        SELECT jsonb_object_agg(k, old_j -> k), jsonb_object_agg(k, new_j -> k)
        INTO before_j, after_j
        FROM unnest(changed) AS k;
    ELSIF TG_OP = 'INSERT' THEN
        after_j := new_j;
    ELSE
        before_j := old_j;
    END IF;

    actor := nullif(current_setting('crm.actor', true), '')::jsonb;
    -- Deleting a profile (or its auth user) cascades to its memberships and
    -- the note mentions naming it, its favourites and private views, and
    -- clears created_by on what it authored, with no actor set. Those referential actions are recorded as the
    -- system, not refused, so a TVA account can still be deleted. They are
    -- told apart by the profile being gone already: a direct write about a
    -- person who still exists is not one.
    IF actor IS NULL
       AND ((TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_memberships'
             AND NOT EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_note_mentions'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'member_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_favourites'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_saved_views'
                AND old_j ->> 'owner_id' IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'owner_id')::uuid))
            OR (TG_OP = 'UPDATE' AND changed <@ ARRAY['created_by']
                AND new_j ->> 'created_by' IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'created_by')::uuid))
            -- 008: a task's Waiting on person or assigner, cleared because
            -- that profile was deleted (ON DELETE SET NULL), and an inbox
            -- item removed with its owner (ON DELETE CASCADE).
            OR (TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'tasks'
                AND changed <@ ARRAY['waiting_on_id', 'assigned_by']
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(changed) AS c
                    WHERE new_j ->> c IS NOT NULL
                       OR EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> c)::uuid)))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'inbox_items'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'owner_id')::uuid))) THEN
        actor := '{"kind": "system", "user_id": null, "channel": null}';
    END IF;
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A CRM change must be made through the CRM.';
    END IF;

    act := nullif(current_setting('crm.action', true), '');
    IF act IS NULL THEN
        noun := CASE TG_TABLE_NAME
            WHEN 'crm_companies' THEN 'company'
            WHEN 'crm_people' THEN 'person'
            WHEN 'crm_opportunities' THEN 'opportunity'
            WHEN 'crm_workspace_settings' THEN 'setting'
            WHEN 'crm_select_options' THEN 'option'
            WHEN 'crm_mailboxes' THEN 'mailbox'
            ELSE regexp_replace(regexp_replace(TG_TABLE_NAME, '^crm_', ''), 's$', '')
        END;
        act := noun || '.' || CASE
            WHEN TG_OP = 'INSERT' THEN 'created'
            WHEN TG_OP = 'DELETE' THEN 'purged'
            WHEN old_j ->> 'deleted_at' IS NULL AND new_j ->> 'deleted_at' IS NOT NULL
                THEN 'deleted'
            WHEN old_j ->> 'deleted_at' IS NOT NULL AND new_j ->> 'deleted_at' IS NULL
                THEN 'restored'
            ELSE 'updated'
        END;
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after, suggestion_id)
    VALUES (
        (actor ->> 'user_id')::uuid,
        actor ->> 'kind',
        actor ->> 'channel',
        -- crm_workspaces has no workspace_id column: NEW.workspace_id would
        -- raise there, so the workspace is read from the row as jsonb.
        -- 008: a task link with no workspace (a TVA project link) takes its
        -- task's, null for a personal task or one already gone.
        coalesce(r ->> 'workspace_id',
                 CASE WHEN TG_TABLE_NAME = 'crm_workspaces' THEN r ->> 'id' END,
                 CASE WHEN TG_TABLE_NAME = 'task_links' THEN (
                     SELECT k.workspace_id::text FROM tasks k
                     WHERE k.id = (r ->> 'task_id')::uuid
                 ) END)::uuid,
        act,
        TG_TABLE_NAME,
        r ->> 'id',
        before_j,
        after_j,
        -- 061 (009 FR-CRMAI-REV-05): the Review Queue item an approval made
        -- this change for; only the approval path sets it on the actor.
        (actor ->> 'suggestion_id')::uuid
    );
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_review_items.type — deal_signal (FR-CAP-REV-01, CHG-04)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_review_items DROP CONSTRAINT IF EXISTS crm_review_items_type_check;
ALTER TABLE crm_review_items ADD CONSTRAINT crm_review_items_type_check CHECK (type IN (
    'next_step_change', 'task_from_commitment', 'task_from_meeting', 'deal_link',
    'enrichment_conflict', 'rule_suggestion', 'deal_signal'
));

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_capture_mark(uuid, uuid, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_capture_mark(uuid, uuid, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;
