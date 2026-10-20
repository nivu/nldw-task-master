-- 073_crm_capture_live.sql
--
-- Spec 010 (automatic email capture) FR-CAP-DIS-03, -11, FR-CAP-REV-01 to
-- -04, FR-CAP-RPT-04, FR-CAP-CHG-04; data-model §3.3, §3.6, §3.7, §5; plan
-- §5.8, §5.9, PD-15, PD-21. Slice S3: live discovery, new leads, the ignore
-- list and applying a dry run.
--
-- 1. `crm_capture_ignores`: the workspace's ignore list (an address or a
--    domain, lower case), audited configuration (`crm_audit_row`, noun
--    `capture_ignore`) with the touch trigger. `from_item_id` names the
--    `new_lead` whose dismissal added it, set NULL when that item is
--    purged. RLS on, no policies, no browser grants. Written only through
--    `crm_apply`.
-- 2. `crm_review_items`: `record_type` and `record_id` may be NULL, for a
--    `new_lead` only (`crm_review_items_target`: both or neither, and
--    neither only for `new_lead`); `crm_review_items_type_check` widened
--    with `new_lead`.
-- 3. `crm_capture_apply(actor, run)`: marks the workspace's latest dry run
--    applied (finished within 7 days, not cancelled, not applied before),
--    turns its candidates into live decisions (`dry_run = false`) and sets
--    `capture` to `{"mode": "live"}` (audited `setting.updated`). The
--    items are upserted by Python before this call (plan §5.8). It first
--    takes the workspace's capture lock (072), so a dry run started at the
--    same moment is either seen as the latest (and this one refused) or
--    starts after this one is applied.
-- 4. `crm_capture_purge(workspace, before)`: deletes every decided
--    candidate (any status but `pending`, findings included) last updated
--    before `before`; the nightly purge passes now - 365 days (FR-CAP-DIS-11,
--    E12, PD-15). Returns the rows deleted.
-- 5. `crm_capture_supersede(actor, source_key, keep)`: expires the open
--    capture items of one conversation (`new_lead`, and a `deal_signal`
--    whose evidence says `origin: discovery`; a deal document's signal is
--    the sync's) whose dedupe key is not `keep` (one `review.expired` audit
--    row each), so a conversation keeps one open capture item when a later
--    duplicate check links it to a company, no longer does, or turns the
--    lead into a deal signal; with a `keep` that matches nothing (`''`),
--    when a later judgement makes no item (FR-CAP-DIS-06, -08; spec §13 S3
--    line 1). Returns how many.
--
-- The type CHECK is dropped by name and re-added under the same name with
-- `new_lead` added (name confirmed with pg_constraint, tasks T301:
-- crm_review_items_type_check, last re-added by 071).
--
-- No function is replaced. crm_review_upsert (069), crm_purge and
-- crm_merge_companies (065) and crm_check_refs (048) already pass a NULL
-- record_id through (data-model §1).
--
-- EXECUTE on every function here is revoked from PUBLIC, anon and
-- authenticated and granted to service_role (data-model §5).
--
-- Reversible: DROP FUNCTION crm_capture_apply(jsonb, uuid),
-- crm_capture_purge(uuid, timestamptz), crm_capture_supersede(jsonb, text,
-- text); DROP TABLE crm_capture_ignores;
-- DELETE FROM crm_review_items WHERE type = 'new_lead'; ALTER TABLE
-- crm_review_items DROP CONSTRAINT crm_review_items_target, ALTER COLUMN
-- record_type SET NOT NULL, ALTER COLUMN record_id SET NOT NULL; re-add
-- 071's crm_review_items_type_check.

-- ---------------------------------------------------------------------------
-- 1. crm_capture_ignores — data-model §3.6 (audited configuration)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_capture_ignores (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    kind         text NOT NULL CHECK (kind IN ('address', 'domain')),
    value        text NOT NULL
                 CHECK (char_length(value) BETWEEN 3 AND 320 AND value = lower(value)),
    from_item_id uuid,
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_capture_ignores_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_capture_ignores_once UNIQUE (workspace_id, kind, value),
    -- A domain has no `@`; an address has one after its first character.
    CONSTRAINT crm_capture_ignores_domain CHECK (kind <> 'domain' OR position('@' in value) = 0),
    CONSTRAINT crm_capture_ignores_address CHECK (kind <> 'address' OR position('@' in value) > 1),
    CONSTRAINT crm_capture_ignores_item
        FOREIGN KEY (workspace_id, from_item_id)
        REFERENCES crm_review_items (workspace_id, id) ON DELETE SET NULL (from_item_id)
);

DROP TRIGGER IF EXISTS crm_capture_ignores_touch ON crm_capture_ignores;
CREATE TRIGGER crm_capture_ignores_touch BEFORE UPDATE ON crm_capture_ignores
    FOR EACH ROW EXECUTE FUNCTION public.crm_touch_updated_at();

DROP TRIGGER IF EXISTS crm_capture_ignores_crm_audit ON crm_capture_ignores;
CREATE TRIGGER crm_capture_ignores_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_capture_ignores
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_capture_ignores ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_capture_ignores FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. crm_review_items — data-model §3.3 (a `new_lead` may have no target)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_review_items
    ALTER COLUMN record_type DROP NOT NULL,
    ALTER COLUMN record_id DROP NOT NULL;

ALTER TABLE crm_review_items DROP CONSTRAINT IF EXISTS crm_review_items_target;
ALTER TABLE crm_review_items ADD CONSTRAINT crm_review_items_target CHECK (
    (record_type IS NULL) = (record_id IS NULL)
    AND (record_id IS NOT NULL OR type = 'new_lead')
);

ALTER TABLE crm_review_items DROP CONSTRAINT IF EXISTS crm_review_items_type_check;
ALTER TABLE crm_review_items ADD CONSTRAINT crm_review_items_type_check CHECK (type IN (
    'next_step_change', 'task_from_commitment', 'task_from_meeting', 'deal_link',
    'enrichment_conflict', 'rule_suggestion', 'deal_signal', 'new_lead'
));

-- ---------------------------------------------------------------------------
-- 3. crm_capture_apply — FR-CAP-RPT-04 (plan §5.8)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_apply(p_actor jsonb, p_run uuid)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws        uuid := (p_actor ->> 'workspace_id')::uuid;
    run       crm_capture_dry_runs%ROWTYPE;
    latest_id uuid;
    applied   timestamptz;
BEGIN
    IF p_actor ->> 'kind' IS DISTINCT FROM 'user' OR ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A dry run is applied by a person.';
    END IF;
    -- The workspace's capture lock (072), before any check: a start that
    -- committed meanwhile is then seen by the latest-run check below.
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
    SELECT * INTO run FROM crm_capture_dry_runs
    WHERE workspace_id = ws AND id = p_run
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such dry run.';
    END IF;
    SELECT r.id INTO latest_id FROM crm_capture_dry_runs r
    WHERE r.workspace_id = ws
    ORDER BY r.started_at DESC, r.id DESC
    LIMIT 1;
    -- The window counts from the report's end, not the start: a run paused
    -- by the monthly budget may finish late (data-model §3.7).
    IF run.finished_at IS NULL OR run.cancelled OR run.applied_at IS NOT NULL
       OR latest_id IS DISTINCT FROM run.id
       OR run.finished_at < now() - interval '7 days' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'This dry run cannot be applied.';
    END IF;

    UPDATE crm_capture_dry_runs
    SET applied_at = now(), applied_by = (p_actor ->> 'user_id')::uuid
    WHERE workspace_id = ws AND id = p_run
    RETURNING applied_at INTO applied;

    -- Its decisions are live ones now: a new dry run no longer resets them.
    UPDATE crm_capture_candidates SET dry_run = false, updated_at = now()
    WHERE workspace_id = ws AND dry_run;

    PERFORM set_config('crm.actor', p_actor::text, true);
    PERFORM set_config('crm.action', '', true);
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (ws, 'capture', '{"mode": "live"}'::jsonb)
    ON CONFLICT (workspace_id, key) DO UPDATE SET value = EXCLUDED.value;

    RETURN jsonb_build_object('applied_at', applied);
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_capture_purge — FR-CAP-DIS-11 (E12, PD-15)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_purge(p_workspace uuid, p_before timestamptz)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    gone integer;
BEGIN
    IF p_workspace IS NULL OR p_before IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A purge needs a workspace and a time.';
    END IF;
    DELETE FROM crm_capture_candidates
    WHERE workspace_id = p_workspace AND updated_at < p_before AND status <> 'pending';
    GET DIAGNOSTICS gone = ROW_COUNT;
    RETURN gone;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_capture_supersede — FR-CAP-DIS-08 (one open new lead a conversation)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_supersede(
    p_actor jsonb, p_source_key text, p_keep text
)
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
    IF p_source_key IS NULL OR p_keep IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;

    FOR r IN
        UPDATE crm_review_items SET status = 'expired', decided_at = now()
        WHERE workspace_id = ws AND status = 'open'
          AND (type = 'new_lead'
               OR (type = 'deal_signal' AND suggestion -> 'evidence' ->> 'origin' = 'discovery'))
          AND source_kind = 'email_thread' AND source_key = p_source_key
          AND dedupe_key <> p_keep
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

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_capture_apply(jsonb, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_purge(uuid, timestamptz)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_supersede(jsonb, text, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_capture_apply(jsonb, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_purge(uuid, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_supersede(jsonb, text, text) TO service_role;
