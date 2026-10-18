-- 067_crm_contact_recency.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-REL-01 to -03; Q-17, Q-21;
-- data-model §3.10; plan §5.10.
--
-- Contact recency (phase 4): each company and person is Recent, Fading or
-- Lapsed, a derived fact about recent contact, not AI and not the person-set
-- Relationship status (Q-21). It is computed in Python
-- (domain/crm/recency.py) with the flags job, from what `crm_contact_facts`
-- reads, and stored so a view filters by it on the server.
--
-- 1. `contact_recency` on `crm_companies` and `crm_people`: recent, fading
--    or lapsed, NULL until the first recompute.
-- 2. `crm_touch_updated_at()` (038's) replaced: an update whose changed
--    columns are within the flag columns and `contact_recency` leaves
--    `updated_at` alone (FR-CRMAI-REL-02).
-- 3. `crm_audit_row()` (061's) replaced: such an update writes no audit row
--    and needs no actor.
-- 4. `crm_set_contact_recency(workspace, table, rows)`: the only writer,
--    modelled on 038's `crm_set_flags`. It updates `contact_recency` and
--    nothing else, for rows of that workspace, so `updated_by` is never
--    touched and the two skip lists above keep `updated_at` and the audit
--    as they were. It is not written through `crm_apply`, which sets
--    `updated_by` from the actor on every update. Returns how many rows'
--    level changed.
-- 5. `crm_contact_facts(workspace, now)`: per live company and person, its
--    latest counted inbound and outbound email (stored, not internal, not an
--    auto-reply or bounce) and its latest meeting note (kind `meeting`, not
--    deleted, by `occurred_on`), within the last 91 days of `now`; a company
--    also counts its live people's (FR-CRMAI-REL-01). Every live company
--    and person is in the answer, with nulls when nothing counts, so each
--    gets a level. Python decides the 30 and 90 days in whole Asia/Kolkata
--    days. Ids, times and dates only.
-- 6. `crm_object_fields`: `contact_recency` for companies and people, a
--    `fixed_select`, filterable only (not sortable, not groupable, not an
--    amount), in every workspace. `crm_query()`'s filter path
--    (`crm_query_cond`, 034) compares a `fixed_select` by its key with no
--    rank, and only a sort or a grouping calls `crm_query_rank()`, so
--    `crm_query_rank()` is not replaced (T156).
--
-- Replaced functions, copied from their latest definition in the rebased
-- tree (`grep -l "CREATE OR REPLACE FUNCTION public.<name>("
-- supabase/migrations/*.sql | sort | tail -1`): crm_touch_updated_at from
-- 038_crm_flags.sql and crm_audit_row from 061_crm_review_queue.sql, with
-- only the lines marked "067" added. No 008 migration (046 to 051 and 056)
-- replaces either after those (T155).
--
-- EXECUTE on every function here is revoked from PUBLIC, anon and
-- authenticated and granted to service_role (data-model §5).
--
-- Reversible: DELETE FROM crm_object_fields WHERE field = 'contact_recency';
-- re-run 038's crm_touch_updated_at() and 061's crm_audit_row(), with their
-- grants; DROP FUNCTION crm_set_contact_recency(uuid, text, jsonb),
-- crm_contact_facts(uuid, timestamptz); ALTER TABLE crm_companies,
-- crm_people DROP COLUMN contact_recency.

-- ---------------------------------------------------------------------------
-- 1. contact_recency — data-model §3.10
-- ---------------------------------------------------------------------------

ALTER TABLE crm_companies ADD COLUMN IF NOT EXISTS contact_recency text
    CHECK (contact_recency IN ('recent', 'fading', 'lapsed'));
ALTER TABLE crm_people ADD COLUMN IF NOT EXISTS contact_recency text
    CHECK (contact_recency IN ('recent', 'fading', 'lapsed'));

-- ---------------------------------------------------------------------------
-- 2. crm_touch_updated_at — 038's, contact recency skipped
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    -- 067 (009 FR-CRMAI-REL-02): contact recency is derived state too.
    IF (to_jsonb(NEW) - ARRAY['flag_level', 'flags', 'flags_computed_at', 'contact_recency',
                              'updated_at'])
       = (to_jsonb(OLD) - ARRAY['flag_level', 'flags', 'flags_computed_at', 'contact_recency',
                                'updated_at']) THEN
        NEW.updated_at := OLD.updated_at;
    ELSE
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. crm_audit_row — 061's, contact recency skipped
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
-- 4. crm_set_contact_recency — the only writer (FR-CRMAI-REL-02)
-- ---------------------------------------------------------------------------

-- p_rows: [{id, level}]. Only rows whose level changes are updated.
CREATE OR REPLACE FUNCTION public.crm_set_contact_recency(
    p_workspace uuid, p_table text, p_rows jsonb
)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    changed integer;
BEGIN
    IF p_table NOT IN ('crm_companies', 'crm_people') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That table has no contact recency.';
    END IF;
    IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Contact recency comes as a list.';
    END IF;
    EXECUTE format(
        'UPDATE %I t
         SET contact_recency = r.level
         FROM jsonb_to_recordset($1) AS r(id uuid, level text)
         WHERE t.workspace_id = $2 AND t.id = r.id
           AND t.contact_recency IS DISTINCT FROM r.level',
        p_table)
    USING p_rows, p_workspace;
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_contact_facts — data-model §3.10 (FR-CRMAI-REL-01)
-- ---------------------------------------------------------------------------

-- {company: {id: {in, out, meeting}}, person: {id: {in, out, meeting}}}:
-- the latest counted inbound and outbound times and the latest meeting date
-- within the last 91 days (a day's margin on the 90, so the whole-day count
-- is Python's), else null.
CREATE OR REPLACE FUNCTION public.crm_contact_facts(p_workspace uuid, p_now timestamptz)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    WITH people AS (
        SELECT p.id, p.company_id
        FROM crm_people p
        WHERE p.workspace_id = p_workspace AND p.deleted_at IS NULL
    ), companies AS (
        SELECT c.id
        FROM crm_companies c
        WHERE c.workspace_id = p_workspace AND c.deleted_at IS NULL AND c.merged_into_id IS NULL
    ), contacts AS (
        SELECT el.record_type, el.record_id,
               CASE m.direction WHEN 'inbound' THEN m.sent_at END AS inbound,
               CASE m.direction WHEN 'outbound' THEN m.sent_at END AS outbound,
               NULL::date AS meeting
        FROM crm_email_messages m
        JOIN crm_email_links el ON el.workspace_id = p_workspace AND el.message_id = m.id
        WHERE m.workspace_id = p_workspace AND NOT m.is_internal AND NOT m.is_auto
          AND m.sent_at >= p_now - interval '91 days' AND m.sent_at <= p_now
          AND el.record_type IN ('company', 'person')
        UNION ALL
        SELECT nl.record_type, nl.record_id, NULL, NULL, n.occurred_on
        FROM crm_notes n
        JOIN crm_note_links nl ON nl.workspace_id = p_workspace AND nl.note_id = n.id
        WHERE n.workspace_id = p_workspace AND n.kind = 'meeting' AND n.deleted_at IS NULL
          AND n.occurred_on >= (p_now - interval '91 days')::date
          AND n.occurred_on <= (p_now AT TIME ZONE 'Asia/Kolkata')::date
          AND nl.record_type IN ('company', 'person')
    ), person_facts AS (
        SELECT p.id, p.company_id, max(c.inbound) AS inbound, max(c.outbound) AS outbound,
               max(c.meeting) AS meeting
        FROM people p
        LEFT JOIN contacts c ON c.record_type = 'person' AND c.record_id = p.id
        GROUP BY p.id, p.company_id
    ), company_facts AS (
        SELECT co.id, max(x.inbound) AS inbound, max(x.outbound) AS outbound,
               max(x.meeting) AS meeting
        FROM companies co
        LEFT JOIN (
            SELECT c.record_id AS company_id, c.inbound, c.outbound, c.meeting
            FROM contacts c WHERE c.record_type = 'company'
            UNION ALL
            SELECT pf.company_id, pf.inbound, pf.outbound, pf.meeting
            FROM person_facts pf WHERE pf.company_id IS NOT NULL
        ) x ON x.company_id = co.id
        GROUP BY co.id
    )
    SELECT jsonb_build_object(
        'company', coalesce((
            SELECT jsonb_object_agg(f.id, jsonb_build_object(
                       'in', f.inbound, 'out', f.outbound, 'meeting', f.meeting))
            FROM company_facts f
        ), '{}'::jsonb),
        'person', coalesce((
            SELECT jsonb_object_agg(f.id, jsonb_build_object(
                       'in', f.inbound, 'out', f.outbound, 'meeting', f.meeting))
            FROM person_facts f
        ), '{}'::jsonb)
    );
$$;

-- ---------------------------------------------------------------------------
-- 6. The view registry — data-model §3.10 (FR-CRMAI-REL-02)
-- ---------------------------------------------------------------------------

-- (object, field, column_sql, type, is_amount, filterable, sortable,
-- groupable, aggregatable, workspace_scope), as 034.
INSERT INTO crm_object_fields (object, field, column_sql, type, is_amount, filterable, sortable,
                               groupable, aggregatable, workspace_scope)
VALUES
    ('company', 'contact_recency', 'contact_recency', 'fixed_select', false, true, false, false, false, 'all'),
    ('person', 'contact_recency', 'contact_recency', 'fixed_select', false, true, false, false, false, 'all')
ON CONFLICT (object, field) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_touch_updated_at() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_set_contact_recency(uuid, text, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_contact_facts(uuid, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_touch_updated_at() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_set_contact_recency(uuid, text, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_contact_facts(uuid, timestamptz) TO service_role;
