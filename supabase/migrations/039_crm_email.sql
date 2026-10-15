-- 039_crm_email.sql
--
-- Spec 007 (sales CRM) FR-CRM-SYNC-01 to -10, FR-CRM-FLAG-01, -04,
-- FR-CRM-PIPE-02, FR-CRM-DUP-03, FR-CRM-DEL-01, FR-CRM-REC-03; Q-23, Q-29,
-- Q-62, Q-74; data-model §3.22, §5.1, §5.4, §5.7, §5.14, §7.3; plan §2.2,
-- §5.11, §5.13.
--
-- Gmail label sync: labelled threads land on the right people and companies
-- as metadata only (no bodies, Q-29).
--
-- 1. `crm_mailboxes` (audited): the Gmail mailboxes a workspace syncs, each
--    with an owner who is a member (checked in Python) and its labels. The
--    address is globally unique, so a mailbox is registered in one
--    workspace only and its labelled mail can never land in the other; its
--    domain must be one the service account is delegated for. `history_id`
--    and `page_token` are the sync's resume point (Gmail's incremental
--    cursor, and the page a backfill or history read had reached),
--    `backfilled_at` says the 90-day backfill finished (FR-CRM-SYNC-08).
--    That the address is the owner's own (unless a TVA admin registers it)
--    and that no label is a Gmail system label are checked in Python
--    (plan §5.13). The owner reference has no ON DELETE action: a profile
--    that owns a mailbox cannot be deleted until the mailbox is removed or
--    handed to someone else, so no mailbox is ever left without an owner.
-- 2. `crm_email_messages` (log): one row per labelled message per mailbox,
--    `UNIQUE (mailbox_id, gmail_message_id)`, so a re-run never duplicates
--    (FR-CRM-SYNC-07). Subject, participants, direction and time only; no
--    body column. Deleting the mailbox deletes its messages.
-- 3. `crm_email_links` (log): which companies, people and deals a message is
--    about. `crm_check_refs()` (027) holds the target to the link's own
--    workspace (FR-CRM-SYNC-10).
-- 4. `email_sync` setting seed (data-model §7.3): `{"enabled": false}` in
--    every workspace. The sync is inert until an admin switches it on, and
--    the backend refuses that until the service-account credential exists
--    (`CRM_GMAIL_CONFIGURED`, plan §2.2, Q-23).
-- 5. `crm_audit_row()` replaced (034's, plus one skip): a mailbox's cursor
--    columns alone change on every page the sync reads; that is not an
--    admin's edit and writes no audit row. Every other mailbox change is
--    audited as `mailbox.*`.
-- 6. `crm_store_emails(workspace, mailbox, messages)`: inserts a page's
--    messages and their links in one transaction, skipping one already
--    stored (ON CONFLICT DO NOTHING), and returns the Gmail ids it stored.
--    `crm_remove_emails(workspace, mailbox, gmail_ids)`: deletes stored
--    copies whose label was removed (FR-CRM-SYNC-09) and returns the records
--    they were linked to, so their flags are recomputed.
-- 7. `crm_flag_facts()` replaced by a four-argument form: it also returns,
--    per company, person and deal, the time of the latest inbound and
--    outbound email that counts (never internal-only, never an auto-reply
--    or bounce, FR-CRM-FLAG-04), for Reply owed, Client silent and Stale
--    deal (spec §6.2). People's ids are a new argument, so the old
--    three-argument function is dropped.
-- 8. `crm_purge()` replaced (034's, plus email links), and
--    `crm_merge_companies()` replaced (035's, plus moving email links to
--    the survivor; data-model §5.4, §5.7).
--
-- EXECUTE on the functions is revoked from PUBLIC, anon and authenticated
-- (data-model §5.12).
--
-- Reversible: restore 034's crm_audit_row() and crm_purge(), 035's
-- crm_merge_companies() and 038's crm_flag_facts(uuid, uuid[], uuid[]);
-- DROP FUNCTION crm_flag_facts(uuid, uuid[], uuid[], uuid[]),
-- crm_remove_emails(uuid, uuid, text[]), crm_store_emails(uuid, uuid, jsonb);
-- DROP TABLE crm_email_links, crm_email_messages, crm_mailboxes (the
-- mailboxes' audit rows stay); DELETE FROM crm_workspace_settings WHERE key
-- = 'email_sync' (audited as a purge).

-- ---------------------------------------------------------------------------
-- 1. crm_mailboxes — data-model §3.22 (audited)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_mailboxes (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    email          text NOT NULL CHECK (
                       email = lower(email) AND char_length(email) <= 254
                       -- The Google Workspace domains the service account is
                       -- delegated for (FR-CRM-SYNC-03).
                       AND split_part(email, '@', 2) IN
                           ('nunnarilabs.com', 'deepweaver.ai', 'nunnari.academy')
                   ),
    owner_id       uuid NOT NULL REFERENCES profiles(id),
    labels         text[] NOT NULL CHECK (cardinality(labels) BETWEEN 1 AND 20),
    is_active      boolean NOT NULL DEFAULT true,
    -- The sync's cursor; written by the sync, never by a person.
    history_id     text CHECK (char_length(history_id) <= 40),
    page_token     text CHECK (char_length(page_token) <= 2000),
    backfilled_at  timestamptz,
    last_synced_at timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    -- One workspace per address (FR-CRM-SYNC-01, amended).
    CONSTRAINT crm_mailboxes_email UNIQUE (email),
    CONSTRAINT crm_mailboxes_workspace_id UNIQUE (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS crm_mailboxes_workspace ON crm_mailboxes (workspace_id);

-- ---------------------------------------------------------------------------
-- 2. crm_email_messages — data-model §3.22 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_email_messages (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    mailbox_id       uuid NOT NULL,
    gmail_message_id text NOT NULL CHECK (char_length(gmail_message_id) <= 64),
    thread_id        text NOT NULL CHECK (char_length(thread_id) <= 64),
    subject          text CHECK (char_length(subject) <= 500),
    sent_at          timestamptz NOT NULL,
    direction        text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_email       text CHECK (char_length(from_email) <= 254),
    -- [{email, name, role: from|to|cc}]
    participants     jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(participants) = 'array'),
    is_internal      boolean NOT NULL DEFAULT false,
    -- An auto-reply or bounce (FR-CRM-FLAG-04).
    is_auto          boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_email_messages_mailbox FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES crm_mailboxes (workspace_id, id) ON DELETE CASCADE,
    -- FR-CRM-SYNC-07: a re-run never duplicates a message.
    CONSTRAINT crm_email_messages_once UNIQUE (mailbox_id, gmail_message_id),
    CONSTRAINT crm_email_messages_workspace_id UNIQUE (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS crm_email_messages_thread
    ON crm_email_messages (workspace_id, thread_id);
CREATE INDEX IF NOT EXISTS crm_email_messages_sent
    ON crm_email_messages (workspace_id, sent_at DESC);

-- ---------------------------------------------------------------------------
-- 3. crm_email_links — data-model §3.22 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_email_links (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    message_id   uuid NOT NULL,
    record_type  text NOT NULL CHECK (record_type IN ('company', 'person', 'opportunity')),
    record_id    uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_email_links_message FOREIGN KEY (workspace_id, message_id)
        REFERENCES crm_email_messages (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_email_links_target UNIQUE (message_id, record_type, record_id)
);

CREATE INDEX IF NOT EXISTS crm_email_links_record
    ON crm_email_links (workspace_id, record_type, record_id);

-- ---------------------------------------------------------------------------
-- Triggers, RLS, privileges — data-model §2
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS crm_mailboxes_touch ON crm_mailboxes;
CREATE TRIGGER crm_mailboxes_touch BEFORE UPDATE ON crm_mailboxes
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_mailboxes_crm_audit ON crm_mailboxes;
CREATE TRIGGER crm_mailboxes_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_mailboxes
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

-- A link's record must be in the link's own workspace (data-model §5.14).
DROP TRIGGER IF EXISTS crm_email_links_check_refs ON crm_email_links;
CREATE TRIGGER crm_email_links_check_refs BEFORE INSERT OR UPDATE ON crm_email_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

ALTER TABLE crm_mailboxes      ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_email_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_email_links    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_mailboxes, crm_email_messages, crm_email_links FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. Settings seed — data-model §7.3
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- Q-23: off until the credential exists and an admin switches it on.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'email_sync', '{"enabled": false}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_audit_row — 034's function, plus the mailbox cursor skip
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
           OR changed <@ ARRAY['flag_level', 'flags', 'flags_computed_at']
           OR (changed <@ ARRAY['position'] AND TG_TABLE_NAME <> 'crm_select_options')
           OR (TG_TABLE_NAME = 'crm_mailboxes'
               AND changed <@ ARRAY['history_id', 'page_token', 'backfilled_at',
                                    'last_synced_at']) THEN
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
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'created_by')::uuid))) THEN
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
                           target_table, target_id, before, after)
    VALUES (
        (actor ->> 'user_id')::uuid,
        actor ->> 'kind',
        actor ->> 'channel',
        -- crm_workspaces has no workspace_id column: NEW.workspace_id would
        -- raise there, so the workspace is read from the row as jsonb.
        coalesce(r ->> 'workspace_id',
                 CASE WHEN TG_TABLE_NAME = 'crm_workspaces' THEN r ->> 'id' END)::uuid,
        act,
        TG_TABLE_NAME,
        r ->> 'id',
        before_j,
        after_j
    );
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_store_emails, crm_remove_emails — the one writer of the email log
-- ---------------------------------------------------------------------------

-- `p_messages`: [{gmail_message_id, thread_id, subject, sent_at, direction,
-- from_email, participants, is_internal, is_auto, links: [{record_type,
-- record_id}]}], one page of one mailbox. A message stored already is
-- skipped with its links (FR-CRM-SYNC-07). A link to a record that is not in
-- this workspace is refused by crm_check_refs (CRM04) and the page with it.
-- Returns the Gmail ids stored by this call.
CREATE OR REPLACE FUNCTION public.crm_store_emails(
    p_workspace uuid, p_mailbox uuid, p_messages jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    m      jsonb;
    l      jsonb;
    new_id uuid;
    stored jsonb := '[]';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM crm_mailboxes WHERE id = p_mailbox AND workspace_id = p_workspace)
    THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such mailbox.';
    END IF;
    IF jsonb_typeof(p_messages) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Messages come as a list.';
    END IF;

    FOR m IN SELECT value FROM jsonb_array_elements(p_messages) LOOP
        new_id := NULL;
        INSERT INTO crm_email_messages (
            workspace_id, mailbox_id, gmail_message_id, thread_id, subject, sent_at,
            direction, from_email, participants, is_internal, is_auto
        )
        VALUES (
            p_workspace, p_mailbox, m ->> 'gmail_message_id', m ->> 'thread_id',
            left(m ->> 'subject', 500), (m ->> 'sent_at')::timestamptz, m ->> 'direction',
            left(m ->> 'from_email', 254), coalesce(m -> 'participants', '[]'),
            coalesce((m ->> 'is_internal')::boolean, false),
            coalesce((m ->> 'is_auto')::boolean, false)
        )
        ON CONFLICT (mailbox_id, gmail_message_id) DO NOTHING
        RETURNING id INTO new_id;

        IF new_id IS NOT NULL THEN
            FOR l IN SELECT value FROM jsonb_array_elements(coalesce(m -> 'links', '[]')) LOOP
                INSERT INTO crm_email_links (workspace_id, message_id, record_type, record_id)
                VALUES (p_workspace, new_id, l ->> 'record_type', (l ->> 'record_id')::uuid)
                ON CONFLICT (message_id, record_type, record_id) DO NOTHING;
            END LOOP;
            stored := stored || to_jsonb(m ->> 'gmail_message_id');
        END IF;
    END LOOP;
    RETURN stored;
END;
$$;

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
BEGIN
    SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object(
               'record_type', l.record_type, 'record_id', l.record_id)), '[]')
    INTO records
    FROM crm_email_links l
    JOIN crm_email_messages m ON m.workspace_id = l.workspace_id AND m.id = l.message_id
    WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
      AND m.gmail_message_id = ANY (coalesce(p_ids, '{}'));

    DELETE FROM crm_email_messages
    WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox
      AND gmail_message_id = ANY (coalesce(p_ids, '{}'));
    GET DIAGNOSTICS removed = ROW_COUNT;

    RETURN jsonb_build_object('removed', removed, 'records', records);
END;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_flag_facts — 038's, plus email facts and people
-- ---------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.crm_flag_facts(uuid, uuid[], uuid[]);

-- `{activity: {deal id: last activity}, open_task_companies: [company id],
-- emails: {company|person|opportunity: {id: {in, out}}}}` for the given live
-- deals, companies and people, or every one in the workspace when a list is
-- NULL (an empty list means none). `in` and `out` are the latest inbound and
-- outbound message linked to the record that counts: internal-only messages
-- are never stored, and auto-replies and bounces are left out here
-- (FR-CRM-FLAG-04). A deal's email is part of its activity in Python
-- (domain/crm/flags.for_deal).
CREATE OR REPLACE FUNCTION public.crm_flag_facts(
    p_workspace uuid, p_deal_ids uuid[], p_company_ids uuid[], p_person_ids uuid[]
)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    WITH deals AS (
        SELECT o.id, o.created_at, o.stage_entered_at
        FROM crm_opportunities o
        WHERE o.workspace_id = p_workspace AND o.deleted_at IS NULL
          AND (p_deal_ids IS NULL OR o.id = ANY (p_deal_ids))
    ), note_at AS (
        SELECT l.record_id AS id,
               max(CASE WHEN n.kind <> 'note' AND n.occurred_on IS NOT NULL
                        THEN n.occurred_on::timestamptz ELSE n.created_at END) AS at
        FROM crm_note_links l
        JOIN crm_notes n ON n.workspace_id = l.workspace_id AND n.id = l.note_id
        WHERE l.workspace_id = p_workspace AND l.record_type = 'opportunity'
          AND n.deleted_at IS NULL AND l.record_id IN (SELECT id FROM deals)
        GROUP BY l.record_id
    ), task_at AS (
        SELECT l.record_id AS id, max(greatest(t.created_at, t.completed_at)) AS at
        FROM crm_task_links l
        JOIN crm_tasks t ON t.workspace_id = l.workspace_id AND t.id = l.task_id
        WHERE l.workspace_id = p_workspace AND l.record_type = 'opportunity'
          AND t.deleted_at IS NULL AND l.record_id IN (SELECT id FROM deals)
        GROUP BY l.record_id
    ), mail AS (
        SELECT l.record_type, l.record_id,
               max(m.sent_at) FILTER (WHERE m.direction = 'inbound') AS last_in,
               max(m.sent_at) FILTER (WHERE m.direction = 'outbound') AS last_out
        FROM crm_email_links l
        JOIN crm_email_messages m ON m.workspace_id = l.workspace_id AND m.id = l.message_id
        WHERE l.workspace_id = p_workspace AND NOT m.is_internal AND NOT m.is_auto
          AND ((l.record_type = 'opportunity'
                AND (p_deal_ids IS NULL OR l.record_id = ANY (p_deal_ids)))
            OR (l.record_type = 'company'
                AND (p_company_ids IS NULL OR l.record_id = ANY (p_company_ids)))
            OR (l.record_type = 'person'
                AND (p_person_ids IS NULL OR l.record_id = ANY (p_person_ids))))
        GROUP BY l.record_type, l.record_id
    )
    SELECT jsonb_build_object(
        'activity', coalesce((
            SELECT jsonb_object_agg(d.id, greatest(d.created_at, d.stage_entered_at, na.at, ta.at))
            FROM deals d
            LEFT JOIN note_at na ON na.id = d.id
            LEFT JOIN task_at ta ON ta.id = d.id
        ), '{}'::jsonb),
        'open_task_companies', coalesce((
            SELECT jsonb_agg(DISTINCT l.record_id)
            FROM crm_task_links l
            JOIN crm_tasks t ON t.workspace_id = l.workspace_id AND t.id = l.task_id
            WHERE l.workspace_id = p_workspace AND l.record_type = 'company'
              AND t.status <> 'done' AND t.deleted_at IS NULL
              AND (p_company_ids IS NULL OR l.record_id = ANY (p_company_ids))
        ), '[]'::jsonb),
        'emails', coalesce((
            SELECT jsonb_object_agg(per.record_type, per.records)
            FROM (
                SELECT record_type,
                       jsonb_object_agg(record_id,
                                        jsonb_build_object('in', last_in, 'out', last_out))
                           AS records
                FROM mail
                GROUP BY record_type
            ) per
        ), '{}'::jsonb)
    );
$$;

-- ---------------------------------------------------------------------------
-- 8. crm_purge — data-model §5.4, fifth form (034's, plus email links)
-- ---------------------------------------------------------------------------

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
            WHEN 'crm_tasks' THEN 'task'
            WHEN 'crm_notes' THEN 'note'
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
            DELETE FROM crm_task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
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
        END IF;
        EXECUTE format('DELETE FROM %I WHERE id = $1 AND workspace_id = $2', tbl)
        USING target, ws;
        purged := purged + 1;
    END LOOP;

    RETURN jsonb_build_object('purged', purged);
END;
$$;

-- ---------------------------------------------------------------------------
-- 9. crm_merge_companies — data-model §5.7, second form (035's, plus email
--    links; replaced again in 040)
-- ---------------------------------------------------------------------------

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
    n_people   integer := 0;
    n_deals    integer := 0;
    n_tasks    integer := 0;
    n_notes    integer := 0;
    n_links    integer := 0;
    n_domains  integer := 0;
    n_projects integer := 0;
    n_emails   integer := 0;
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
        SELECT * FROM crm_task_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_task_links WHERE task_id = r.task_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_task_links (workspace_id, task_id, record_type, record_id,
                                        created_at, created_by)
            VALUES (ws, r.task_id, 'company', keep_id, r.created_at, r.created_by);
        END IF;
        DELETE FROM crm_task_links WHERE id = r.id;
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
            'emails', n_emails));
END;
$$;

-- ---------------------------------------------------------------------------
-- Grants — data-model §5.12
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_store_emails(uuid, uuid, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_remove_emails(uuid, uuid, text[])
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_flag_facts(uuid, uuid[], uuid[], uuid[])
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_store_emails(uuid, uuid, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_remove_emails(uuid, uuid, text[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_flag_facts(uuid, uuid[], uuid[], uuid[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
