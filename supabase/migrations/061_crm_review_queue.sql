-- 061_crm_review_queue.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-REV-01 to -10, FR-CRMAI-CHG-09,
-- -11 (review items); Q-07, Q-09, Q-34; data-model §3.2, §3.12, §4, §5;
-- plan §5.4.
--
-- Every AI suggestion waits as a Review Queue item until a person decides
-- it. Nothing here makes a suggestion or changes a record by itself.
--
-- 1. `crm_review_items`: one item per suggestion, routed to one person
--    (`owner_id`; NULL means the workspace's Admins, FR-CRMAI-REV-02),
--    with what it would change (`suggestion`), the value of each field it
--    would replace at creation (`current_values`, the per-field staleness
--    check of FR-CRMAI-REV-04), its source, a dedupe key and its
--    provenance. At most one open item per dedupe key
--    (`crm_review_items_open_once`, FR-CRMAI-REV-08). A log with explicit
--    audit rows, not a record: no audit trigger, because its text must be
--    removable at 90 days (Q-34), which the append-only audit_log would
--    defeat. People's decisions are audited: `crm_review_claim` and
--    `crm_review_expire` write their own audit rows with no suggestion text,
--    and the change an approval makes is audited by the path a manual edit
--    takes. Its target is held to its workspace by `crm_check_refs()`.
-- 2. `crm_review_item_deals`: an item's deal choices (the deal picker of
--    Q-07), a child table and not a uuid[] column, because crm_check_refs
--    checks array elements against select options. Composite foreign keys;
--    a purged deal's rows go by CASCADE.
-- 3. `audit_log.suggestion_id` (FR-CRMAI-REV-05): the item an approved
--    change was made for. No foreign key: audit_log is append-only (005),
--    so ON DELETE SET NULL would be an UPDATE it refuses, and the items of
--    a purged record are deleted. `crm_audit_row()` fills it from the
--    `crm.actor` session setting, which the backend sets only on the
--    approval path (a CRM change's actor, and 008's task actor for an
--    approved task, so the task's own audit rows carry it too).
-- 4. `crm_review_upsert`, `crm_review_claim`, `crm_review_release`,
--    `crm_review_expire`, `crm_review_tombstone` (data-model §3.2.3).
-- 5. `crm_purge()` also deletes the items that target a purged record, and
--    `crm_merge_companies()` moves the merged company's open items to the
--    survivor, recomputing their dedupe keys (FR-CRMAI-CHG-11, data-model §4).
--
-- Replaced functions, copied from their latest definition in the rebased
-- tree, 048_tasks_crm_functions.sql (`grep -l "CREATE OR REPLACE FUNCTION
-- public.<name>(" supabase/migrations/*.sql | sort | tail -1` for each of
-- crm_audit_row, crm_purge and crm_merge_companies), with only the lines
-- marked "061" added. 008 has no migration after 050 that replaces any of
-- them (T031).
--
-- RLS is on and the browser roles have no grant on either table; EXECUTE on
-- every function is revoked from PUBLIC, anon and authenticated and granted
-- to service_role (data-model §5).
--
-- Reversible: `audit_log.suggestion_id` cannot be dropped in practice (it is
-- append-only history). DROP FUNCTION crm_review_upsert(jsonb, jsonb),
-- crm_review_claim(jsonb, uuid, text, text), crm_review_release(jsonb, uuid,
-- text, jsonb), crm_review_expire(jsonb, timestamptz),
-- crm_review_tombstone(jsonb, timestamptz); DROP TABLE crm_review_item_deals,
-- crm_review_items; re-run 048's crm_audit_row, crm_purge and
-- crm_merge_companies, with their grants.

-- ---------------------------------------------------------------------------
-- 1. crm_review_items — data-model §3.2 (log, explicit audit rows)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_review_items (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    type           text NOT NULL CHECK (type IN (
                       'next_step_change', 'task_from_commitment', 'task_from_meeting',
                       'deal_link', 'enrichment_conflict', 'rule_suggestion'
                   )),
    status         text NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'approved', 'dismissed', 'expired')),
    -- NULL: the workspace's Admins (FR-CRMAI-REV-02), resolved at creation.
    owner_id       uuid REFERENCES profiles(id) ON DELETE SET NULL,
    record_type    text NOT NULL
                       CHECK (record_type IN ('company', 'person', 'opportunity', 'note')),
    record_id      uuid NOT NULL,
    -- R-4 only (FR-CRMAI-RULE-06).
    member_id      uuid,
    suggestion     jsonb NOT NULL DEFAULT '{}'
                       CHECK (jsonb_typeof(suggestion) = 'object'
                              AND pg_column_size(suggestion) <= 8192),
    current_values jsonb NOT NULL DEFAULT '{}'
                       CHECK (jsonb_typeof(current_values) = 'object'),
    source_kind    text NOT NULL
                       CHECK (source_kind IN ('email_thread', 'meeting', 'enrichment', 'rule')),
    source_key     text NOT NULL CHECK (char_length(source_key) <= 300),
    source_version text NOT NULL CHECK (char_length(source_version) <= 300),
    -- What to open; never a URL except the Fireflies transcript link.
    source_link    jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_link) = 'object'),
    dedupe_key     text NOT NULL CHECK (char_length(dedupe_key) <= 400),
    -- Provenance (P-2); NULL for the deterministic rules' items.
    model          text CHECK (char_length(model) <= 100),
    generated_at   timestamptz,
    refusal        text CHECK (char_length(refusal) <= 500),
    -- Expiry counts from here (14 days, FR-CRMAI-REV-07).
    created_at     timestamptz NOT NULL DEFAULT now(),
    decided_at     timestamptz,
    decided_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    -- What approval produced, ids only.
    result         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(result) = 'object'),
    tombstoned_at  timestamptz,
    CONSTRAINT crm_review_items_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_review_items_member FOREIGN KEY (workspace_id, member_id)
        REFERENCES crm_campaign_members (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_review_items_decided CHECK ((status = 'open') = (decided_at IS NULL)),
    CONSTRAINT crm_review_items_tombstone CHECK (tombstoned_at IS NULL OR status <> 'open'),
    CONSTRAINT crm_review_items_rule CHECK (type <> 'rule_suggestion' OR source_kind = 'rule')
);

-- FR-CRMAI-REV-08: never a second open item with the same key.
CREATE UNIQUE INDEX IF NOT EXISTS crm_review_items_open_once
    ON crm_review_items (workspace_id, dedupe_key) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS crm_review_items_mine
    ON crm_review_items (workspace_id, owner_id, status, created_at);
CREATE INDEX IF NOT EXISTS crm_review_items_queue
    ON crm_review_items (workspace_id, status, created_at);
CREATE INDEX IF NOT EXISTS crm_review_items_target
    ON crm_review_items (workspace_id, record_type, record_id);

-- The target must be in the item's own workspace (data-model §2). On
-- insert, and on an update of the columns it checks only, so a decided item
-- stays writable after its record is gone.
DROP TRIGGER IF EXISTS crm_review_items_check_refs ON crm_review_items;
CREATE TRIGGER crm_review_items_check_refs
    BEFORE INSERT OR UPDATE OF workspace_id, record_type, record_id ON crm_review_items
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

ALTER TABLE crm_review_items ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_review_items FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. crm_review_item_deals — the deal choices (log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_review_item_deals (
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    item_id        uuid NOT NULL,
    opportunity_id uuid NOT NULL,
    PRIMARY KEY (item_id, opportunity_id),
    CONSTRAINT crm_review_item_deals_item FOREIGN KEY (workspace_id, item_id)
        REFERENCES crm_review_items (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_review_item_deals_deal FOREIGN KEY (workspace_id, opportunity_id)
        REFERENCES crm_opportunities (workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS crm_review_item_deals_deal
    ON crm_review_item_deals (workspace_id, opportunity_id);

ALTER TABLE crm_review_item_deals ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_review_item_deals FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. audit_log.suggestion_id — data-model §3.12 (FR-CRMAI-REV-05)
-- ---------------------------------------------------------------------------

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS suggestion_id uuid;
CREATE INDEX IF NOT EXISTS audit_log_suggestion ON audit_log (suggestion_id)
    WHERE suggestion_id IS NOT NULL;

-- 048's crm_audit_row, with the item id written from the actor (061).

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
-- 4. The Review Queue's functions — data-model §3.2.3
-- ---------------------------------------------------------------------------

-- Inserts or refreshes items, all in the actor's workspace (actor kind
-- `sync`, the jobs' own). An open item with the same dedupe key is
-- refreshed instead of a second one added (FR-CRMAI-REV-08); an item whose
-- key was decided (approved, dismissed or expired) from the same source
-- version is not re-created, and comes back as `{id: null, created:
-- false}`. Each item's deal choices are replaced from its `deal_choices`,
-- each an open deal of the workspace. Returns `[{id, created}]` in order.
CREATE OR REPLACE FUNCTION public.crm_review_upsert(p_actor jsonb, p_items jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws      uuid := (p_actor ->> 'workspace_id')::uuid;
    item    jsonb;
    deal    jsonb;
    made    uuid;
    created boolean;
    results jsonb := '[]';
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('sync', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF jsonb_typeof(p_items) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(p_items) LOOP
        IF EXISTS (
            SELECT 1 FROM crm_review_items
            WHERE workspace_id = ws AND dedupe_key = item ->> 'dedupe_key'
              AND status <> 'open' AND source_version = item ->> 'source_version'
        ) THEN
            results := results || jsonb_build_array(
                jsonb_build_object('id', NULL, 'created', false));
            CONTINUE;
        END IF;

        INSERT INTO crm_review_items AS t (
            workspace_id, type, owner_id, record_type, record_id, member_id, suggestion,
            current_values, source_kind, source_key, source_version, source_link, dedupe_key,
            model, generated_at
        )
        VALUES (
            ws,
            item ->> 'type',
            (item ->> 'owner_id')::uuid,
            item ->> 'record_type',
            (item ->> 'record_id')::uuid,
            (item ->> 'member_id')::uuid,
            coalesce(item -> 'suggestion', '{}'),
            coalesce(item -> 'current_values', '{}'),
            item ->> 'source_kind',
            item ->> 'source_key',
            item ->> 'source_version',
            coalesce(item -> 'source_link', '{}'),
            item ->> 'dedupe_key',
            item ->> 'model',
            (item ->> 'generated_at')::timestamptz
        )
        ON CONFLICT (workspace_id, dedupe_key) WHERE status = 'open' DO UPDATE
        SET suggestion = EXCLUDED.suggestion,
            current_values = EXCLUDED.current_values,
            source_version = EXCLUDED.source_version,
            model = EXCLUDED.model,
            generated_at = EXCLUDED.generated_at
        RETURNING t.id, (t.xmax = 0) INTO made, created;

        -- Q-07: the deal picker, each choice an open deal of the workspace.
        DELETE FROM crm_review_item_deals WHERE item_id = made;
        FOR deal IN SELECT value FROM jsonb_array_elements(coalesce(item -> 'deal_choices', '[]'))
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM crm_opportunities o
                WHERE o.workspace_id = ws AND o.id = (deal #>> '{}')::uuid
                  AND o.deleted_at IS NULL
                  AND o.stage IN ('discovery', 'qualification', 'proposal', 'negotiation')
            ) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM04',
                    MESSAGE = 'No such open deal in this workspace.';
            END IF;
            INSERT INTO crm_review_item_deals (workspace_id, item_id, opportunity_id)
            VALUES (ws, made, (deal #>> '{}')::uuid)
            ON CONFLICT DO NOTHING;
        END LOOP;

        results := results || jsonb_build_array(
            jsonb_build_object('id', made, 'created', created));
    END LOOP;
    RETURN results;
END;
$$;

-- Decides an open item: `approved` (the approval path then makes the
-- change) or `dismissed`. Only one claim wins (the row is updated only while
-- it is still `p_expect_status`); the other is CRM09. One audit row, with no
-- suggestion text, carries the item id. Returns the item.
CREATE OR REPLACE FUNCTION public.crm_review_claim(
    p_actor jsonb, p_item_id uuid, p_status text, p_expect_status text DEFAULT 'open'
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws    uuid := (p_actor ->> 'workspace_id')::uuid;
    who   uuid := (p_actor ->> 'user_id')::uuid;
    row_j jsonb;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF p_status IS NULL OR p_status NOT IN ('approved', 'dismissed') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A suggestion is approved or dismissed.';
    END IF;

    UPDATE crm_review_items t
    SET status = p_status, decided_at = now(), decided_by = who, refusal = NULL
    WHERE t.id = p_item_id AND t.workspace_id = ws AND t.status = p_expect_status
    RETURNING to_jsonb(t) INTO row_j;
    IF row_j IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM crm_review_items
                       WHERE id = p_item_id AND workspace_id = ws) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'This suggestion was already decided.';
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after, suggestion_id)
    VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.' || p_status,
            'crm_review_items', p_item_id::text, jsonb_build_object('status', p_expect_status),
            jsonb_build_object('status', p_status), p_item_id);
    RETURN row_j;
END;
$$;

-- After an approval's change: with a refusal, the claimed item opens again
-- and shows it (FR-CRMAI-REV-06), audited `review.reopened`; without one,
-- the item records what the approval produced (`result`, ids only).
CREATE OR REPLACE FUNCTION public.crm_review_release(
    p_actor jsonb, p_item_id uuid, p_refusal text, p_result jsonb
)
RETURNS void
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws  uuid := (p_actor ->> 'workspace_id')::uuid;
    who uuid := (p_actor ->> 'user_id')::uuid;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;

    IF p_refusal IS NOT NULL THEN
        UPDATE crm_review_items
        SET status = 'open', decided_at = NULL, decided_by = NULL,
            refusal = left(p_refusal, 500)
        WHERE id = p_item_id AND workspace_id = ws AND status = 'approved';
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
        INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                               target_table, target_id, before, after, suggestion_id)
        VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.reopened',
                'crm_review_items', p_item_id::text, jsonb_build_object('status', 'approved'),
                jsonb_build_object('status', 'open'), p_item_id);
    ELSE
        UPDATE crm_review_items SET result = coalesce(p_result, '{}')
        WHERE id = p_item_id AND workspace_id = ws AND status <> 'open';
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
    END IF;
END;
$$;

-- FR-CRMAI-REV-07: the workspace's open items created before `p_before`
-- (14 days ago) expire, one `review.expired` audit row each. Returns how
-- many.
CREATE OR REPLACE FUNCTION public.crm_review_expire(p_actor jsonb, p_before timestamptz)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws uuid := (p_actor ->> 'workspace_id')::uuid;
    r  record;
    n  integer := 0;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('system', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;

    FOR r IN
        UPDATE crm_review_items SET status = 'expired', decided_at = now()
        WHERE workspace_id = ws AND status = 'open' AND created_at < p_before
        RETURNING id
    LOOP
        INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                               target_table, target_id, before, after, suggestion_id)
        VALUES ((p_actor ->> 'user_id')::uuid, p_actor ->> 'kind', p_actor ->> 'channel', ws,
                'review.expired', 'crm_review_items', r.id::text,
                jsonb_build_object('status', 'open'), jsonb_build_object('status', 'expired'),
                r.id);
        n := n + 1;
    END LOOP;
    RETURN n;
END;
$$;

-- FR-CRMAI-REV-07, Q-34: decided items decided before `p_before` (90 days
-- ago) become dedupe tombstones: no suggestion text, current values, source
-- link or refusal (which also removes a stored R-1 draft). The dedupe key,
-- source version and status stay, so FR-CRMAI-REV-08 still holds. Returns
-- how many.
CREATE OR REPLACE FUNCTION public.crm_review_tombstone(p_actor jsonb, p_before timestamptz)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws uuid := (p_actor ->> 'workspace_id')::uuid;
    n  integer := 0;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('system', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;

    UPDATE crm_review_items
    SET suggestion = '{}', current_values = '{}', source_link = '{}', refusal = NULL,
        tombstoned_at = now()
    WHERE workspace_id = ws AND status <> 'open' AND decided_at < p_before
      AND tombstoned_at IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. Record lifecycle — data-model §4 (FR-CRMAI-CHG-11)
-- ---------------------------------------------------------------------------

-- 048's crm_purge, with the items of a purged record deleted (061).

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

-- 048's crm_merge_companies, with the merged company's open items moved (061).

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

REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_upsert(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_claim(jsonb, uuid, text, text)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_release(jsonb, uuid, text, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_expire(jsonb, timestamptz)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_tombstone(jsonb, timestamptz)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_upsert(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_claim(jsonb, uuid, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_release(jsonb, uuid, text, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_expire(jsonb, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_tombstone(jsonb, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
