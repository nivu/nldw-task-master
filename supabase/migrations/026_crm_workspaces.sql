-- 026_crm_workspaces.sql
--
-- Spec 007 (sales CRM) FR-CRM-WS-01 to -04, FR-CRM-ACC-02, -10, FR-CRM-AUD-01,
-- -02, -06, FR-CRM-LIST-01 to -06, -09, FR-CRM-FX-02, FR-CRM-CHG-09, -15;
-- Q-21, Q-22, Q-25, Q-30, Q-35, Q-71; owner decisions D3, D7, D13.
--
-- The CRM's foundation: workspaces, who belongs to them and with which role,
-- their settings, their select lists and their monthly FX rates. Records
-- (companies, people, deals…) arrive in later migrations, each in its own
-- file, all following data-model §2.
--
-- 1. Five tables. Every one has RLS on, no policy and no browser grant: the
--    browser reads nothing from Supabase (FR-CRM-ACC-10); the backend, with
--    the service role, is the only reader and enforces the roles of spec
--    §3.3 in one place (api/crm/deps.py).
--    - `crm_workspaces`: two rows, seeded below. `key` is the URL segment
--      and the MCP `workspace` argument; `account` is refused because
--      `/crm/account` is a static page. At most one workspace is connected to
--      TVA projects (FR-CRM-WS-05). `allowed_domains` limits whom a
--      workspace admin may invite; only a TVA admin edits it (S1c).
--    - `crm_memberships`: one CRM role per person per workspace
--      (FR-CRM-WS-03). A TVA role grants none (FR-CRM-ACC-02).
--      `last_used_at` is the workspace a person opens by default and the one
--      an MCP read without a workspace uses (FR-CRM-WS-04, D13).
--    - `crm_workspace_settings`: per-workspace configuration, one jsonb value
--      per key, validated in Python (domain/crm/settings.py). Not
--      `app_settings`, which is global and TVA-admin-only.
--    - `crm_select_options`: every admin-editable list, per workspace. Fixed
--      lists (stages, lead stages, task status and bucket) can be renamed and
--      recoloured only (Q-30); options a rule reads carry a `system_key` and
--      can never be deactivated (FR-CRM-LIST-09, Q-71). Options are never
--      deleted, so old records keep their value.
--    - `crm_fx_rates`: AUD and USD to INR, one rate per month per workspace
--      (FR-CRM-FX-02, D7). INR converts at 1 and has no row.
--
-- 2. `audit_log` gains `workspace_id` (NULL for every TVA row, so the TVA
--    audit page can leave CRM rows out, FR-CRM-AUD-04), `channel` ('web' or
--    'mcp', Q-22) and the actor kinds 'import' and 'sync' beside 'user' and
--    'system' (FR-CRM-CHG-09, Q-35). The append-only triggers of 005 are
--    untouched: adding a column updates no row.
--
-- 3. `crm_audit_row()`, an AFTER trigger on every audited CRM table, writes
--    the audit row in the same transaction as the change, from the actor the
--    write function set with `set_config('crm.actor', …, true)`. A write with
--    no actor is refused (SQLSTATE CRM22), so a CRM change cannot land
--    unaudited (FR-CRM-AUD-06, Q-21). Three kinds of update need no actor and
--    write no row: a membership's `last_used_at` (switching workspace), a
--    kanban `position` and the follow-up flag columns (1b), which are
--    ordering and derived state, not data. A select option's `position` is
--    not among them: reordering a list is a settings change. The one write
--    with no actor that is still recorded, as the system, is a referential
--    action from deleting a profile (a membership cascading away, an author
--    column set to NULL), so that delete is not refused.
--
-- 4. Seeds (data-model §7), inside a DO block that first sets the system
--    actor, so the trigger records them as system rows rather than refusing
--    them: the two workspaces, their select lists exactly as FR-CRM-LIST-02
--    to -05, the default stage probabilities (FR-CRM-PIPE-11), and the owner
--    as Admin of both (FR-CRM-CHG-15). On a fresh local `db reset` no owner
--    profile exists yet (seed.sql runs after the migrations), so that insert
--    adds nothing there; seed.sql adds the local memberships.
--
-- Reversible only in part. The five tables and two functions can be dropped
-- (DROP TABLE crm_fx_rates, crm_select_options, crm_workspace_settings,
-- crm_memberships, crm_workspaces CASCADE; DROP FUNCTION crm_audit_row()),
-- but `audit_log.workspace_id` cannot: dropping it means deleting the CRM's
-- audit rows, which the append-only rule forbids. Rows written by the CRM are
-- history.

-- ---------------------------------------------------------------------------
-- 1. Tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_workspaces (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key             text NOT NULL UNIQUE
                    CHECK (key ~ '^[a-z][a-z0-9_]{1,30}$' AND key NOT IN ('account')),
    name            text NOT NULL UNIQUE
                    CHECK (btrim(name) <> '' AND char_length(name) <= 80),
    tva_linked      boolean NOT NULL DEFAULT false,
    has_entity      boolean NOT NULL DEFAULT false,
    allowed_domains text[] NOT NULL DEFAULT '{}'
                    CHECK (cardinality(allowed_domains) <= 50),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid REFERENCES profiles(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_workspaces_one_tva_linked
    ON crm_workspaces (tva_linked) WHERE tva_linked;

CREATE TABLE IF NOT EXISTS crm_memberships (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    user_id      uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    role         text NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    last_used_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_memberships_workspace_user UNIQUE (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS crm_memberships_user ON crm_memberships (user_id);

CREATE TABLE IF NOT EXISTS crm_workspace_settings (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    key          text NOT NULL CHECK (
                     key IN ('stage_probabilities', 'flag_thresholds', 'automations', 'email_sync')
                 ),
    value        jsonb NOT NULL CHECK (jsonb_typeof(value) = 'object'),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_workspace_settings_workspace_key UNIQUE (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS crm_select_options (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    list_key     text NOT NULL CHECK (list_key IN (
                     'company.lead_stage', 'opportunity.stage', 'task.status', 'task.bucket',
                     'company.account_type', 'company.relationship_status', 'channel',
                     'company.industry', 'company.institution_type', 'company.size', 'source',
                     'opportunity.deal_type', 'opportunity.engagement_model',
                     'opportunity.programme', 'opportunity.lost_reason',
                     'opportunity.contact_role', 'task.category'
                 )),
    system_key   text CHECK (system_key ~ '^[a-z][a-z0-9_]*$'),
    label        text NOT NULL CHECK (btrim(label) <> '' AND char_length(label) <= 60),
    color        text CHECK (color IN (
                     'gray', 'red', 'orange', 'amber', 'green', 'teal', 'blue', 'indigo',
                     'purple', 'pink'
                 )),
    position     integer NOT NULL,
    is_default   boolean NOT NULL DEFAULT false,
    is_active    boolean NOT NULL DEFAULT true,
    is_fixed     boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    -- Every reference to an option is a composite FK on this pair, so a
    -- record in one workspace cannot point at another's option (data-model §2).
    CONSTRAINT crm_select_options_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_select_options_fixed_keyed CHECK (NOT is_fixed OR system_key IS NOT NULL),
    -- FR-CRM-LIST-09: an option a rule reads is never switched off.
    CONSTRAINT crm_select_options_keyed_active CHECK (system_key IS NULL OR is_active)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_select_options_system_key
    ON crm_select_options (workspace_id, list_key, system_key) WHERE system_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS crm_select_options_label
    ON crm_select_options (workspace_id, list_key, lower(label));
CREATE UNIQUE INDEX IF NOT EXISTS crm_select_options_one_default
    ON crm_select_options (workspace_id, list_key) WHERE is_default;

CREATE TABLE IF NOT EXISTS crm_fx_rates (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    currency     text NOT NULL CHECK (currency IN ('AUD', 'USD')),
    month        date NOT NULL CHECK (extract(day FROM month) = 1),
    -- INR per one unit of `currency`.
    rate         numeric(12, 6) NOT NULL CHECK (rate > 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    -- Also serves the lookup "month <= $m ORDER BY month DESC LIMIT 1".
    CONSTRAINT crm_fx_rates_workspace_currency_month UNIQUE (workspace_id, currency, month)
);

DROP TRIGGER IF EXISTS crm_workspaces_touch ON crm_workspaces;
CREATE TRIGGER crm_workspaces_touch BEFORE UPDATE ON crm_workspaces
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_memberships_touch ON crm_memberships;
CREATE TRIGGER crm_memberships_touch BEFORE UPDATE ON crm_memberships
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_workspace_settings_touch ON crm_workspace_settings;
CREATE TRIGGER crm_workspace_settings_touch BEFORE UPDATE ON crm_workspace_settings
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_select_options_touch ON crm_select_options;
CREATE TRIGGER crm_select_options_touch BEFORE UPDATE ON crm_select_options
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_fx_rates_touch ON crm_fx_rates;
CREATE TRIGGER crm_fx_rates_touch BEFORE UPDATE ON crm_fx_rates
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

ALTER TABLE crm_workspaces         ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_memberships        ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_workspace_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_select_options     ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_fx_rates           ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_workspaces, crm_memberships, crm_workspace_settings, crm_select_options,
    crm_fx_rates FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. audit_log — FR-CRM-AUD-02, -04, FR-CRM-CHG-09
-- ---------------------------------------------------------------------------

ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS workspace_id uuid REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS channel text;

ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_actor_label_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_actor_label_check
    CHECK (actor_label IN ('user', 'system', 'import', 'sync'));
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_channel_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_channel_check
    CHECK (channel IS NULL OR channel IN ('web', 'mcp'));

CREATE INDEX IF NOT EXISTS audit_log_workspace_target
    ON audit_log (workspace_id, target_table, target_id, at DESC) WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS audit_log_workspace_at
    ON audit_log (workspace_id, at DESC) WHERE workspace_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. crm_audit_row() — FR-CRM-AUD-01, -02, -06
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
        IF changed <@ ARRAY['last_used_at']
           OR changed <@ ARRAY['flag_level', 'flags', 'flags_computed_at']
           OR (changed <@ ARRAY['position'] AND TG_TABLE_NAME <> 'crm_select_options') THEN
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
    -- clears created_by on what it authored, with no actor set. Those
    -- referential actions are recorded as the system, not refused, so a TVA
    -- account can still be deleted. They are told apart by the profile being
    -- gone already: a direct write about a person who still exists is not one.
    IF actor IS NULL
       AND ((TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_memberships'
             AND NOT EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
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

REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;

DROP TRIGGER IF EXISTS crm_workspaces_crm_audit ON crm_workspaces;
CREATE TRIGGER crm_workspaces_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_workspaces
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_memberships_crm_audit ON crm_memberships;
CREATE TRIGGER crm_memberships_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_memberships
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_workspace_settings_crm_audit ON crm_workspace_settings;
CREATE TRIGGER crm_workspace_settings_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_workspace_settings
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_select_options_crm_audit ON crm_select_options;
CREATE TRIGGER crm_select_options_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_select_options
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_fx_rates_crm_audit ON crm_fx_rates;
CREATE TRIGGER crm_fx_rates_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_fx_rates
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

-- ---------------------------------------------------------------------------
-- 4. Seeds — data-model §7.1 to §7.4
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    palette text[] := ARRAY['gray', 'red', 'orange', 'amber', 'green', 'teal', 'blue',
                            'indigo', 'purple', 'pink'];
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    INSERT INTO crm_workspaces (key, name, tva_linked, has_entity, allowed_domains)
    VALUES
        ('labs', 'Nunnari Labs', true, true, ARRAY['nunnarilabs.com', 'deepweaver.ai']),
        ('academy', 'Nunnari Academy B2B', false, false,
         ARRAY['nunnari.academy', 'nunnarilabs.com'])
    ON CONFLICT (key) DO NOTHING;

    -- One row per list per workspace scope. Each item is 'system_key|Label',
    -- or '|Label' for an option no rule reads. Position is the item's place
    -- in the array; colours follow the palette in that order. `fixed` lists
    -- (Q-30) and rule-bearing options carry their keys (FR-CRM-LIST-09).
    -- A fixed list's default is the column default of the field it feeds.
    INSERT INTO crm_select_options
        (workspace_id, list_key, system_key, label, color, position, is_default, is_fixed)
    SELECT w.id,
           l.list_key,
           nullif(split_part(i.item, '|', 1), ''),
           split_part(i.item, '|', 2),
           palette[((i.pos - 1) % 10) + 1],
           i.pos,
           coalesce(split_part(i.item, '|', 1) = l.default_key, false),
           l.fixed
    FROM (VALUES
        -- FR-CRM-LIST-02: fixed lists, both workspaces.
        ('both', 'company.lead_stage', true, 'new', ARRAY[
            'new|New', 'contacted|Contacted', 'nurturing|Nurturing', 'qualified|Qualified',
            'deal_open|Deal open', 'customer|Customer', 'partner|Partner',
            'disqualified|Disqualified']),
        ('both', 'opportunity.stage', true, 'discovery', ARRAY[
            'discovery|Discovery', 'qualification|Qualification', 'proposal|Proposal',
            'negotiation|Negotiation', 'closed_won|Closed Won', 'closed_lost|Closed Lost']),
        ('both', 'task.status', true, 'todo', ARRAY[
            'todo|To do', 'in_progress|In progress', 'done|Done']),
        ('both', 'task.bucket', true, NULL, ARRAY[
            'today|Today', 'this_week|This week', 'this_month|This month', 'later|Later']),
        -- FR-CRM-LIST-03: Labs.
        ('labs', 'channel', false, NULL, ARRAY[
            '|Oracle', '|Qualcomm', '|Anthropic', '|DeepWeaver', '|Skillda', '|Forge',
            '|CODISSIA', '|SICCI', '|GIBA', '|Guidance TN', '|Infosys', '|Referral',
            '|Inbound', '|Event', '|Direct outbound']),
        ('labs', 'company.account_type', false, NULL, ARRAY[
            '|Prospect', 'client|Client', 'technology_partner|Technology partner',
            'channel_partner|Channel partner', 'training_partner|Training partner',
            '|Vendor']),
        ('labs', 'task.category', false, NULL, ARRAY[
            '|Lead', '|Sales', '|Partner', '|Campaign', 'delivery_handoff|Delivery handoff',
            '|Operations']),
        -- FR-CRM-LIST-04: Academy. Programme starts empty (Q-40).
        ('academy', 'channel', false, NULL, ARRAY[
            '|Inbound', '|Referral', '|Event', '|Direct', '|Webinar', '|Forge',
            '|Anthropic', '|Skillda', '|Guidance TN', '|Infosys', '|Nunnari Labs',
            '|LinkedIn']),
        ('academy', 'company.account_type', false, NULL, ARRAY[
            '|Prospect', 'client|Client', 'training_partner|Training partner',
            'technology_partner|Technology partner', 'channel_partner|Channel partner',
            '|Vendor']),
        ('academy', 'task.category', false, NULL, ARRAY[
            '|College', '|Corporate training', '|Lead', '|Sales', '|Campaign',
            '|Marketing', '|Curriculum', '|Customer', '|Operations']),
        ('academy', 'company.institution_type', false, NULL, ARRAY[
            '|College', '|University', '|Corporate', '|Training partner', '|Other']),
        -- FR-CRM-LIST-05: common to both, from spec §5.6 to §5.9. Industry
        -- starts empty.
        ('both', 'company.relationship_status', false, NULL, ARRAY[
            '|Active', '|Warm', '|Cold', '|Dormant', '|Churned']),
        ('both', 'company.size', false, NULL, ARRAY[
            '|1-10', '|11-50', '|51-200', '|201-1000', '|1000+']),
        ('both', 'source', false, NULL, ARRAY[
            'manual|Manual', 'import|Import', 'email_sync|Email sync', 'campaign|Campaign',
            'web_form|Web form', 'api|API']),
        ('both', 'opportunity.deal_type', false, NULL, ARRAY[
            '|New business', '|Expansion', '|Renewal', '|Phase follow-on',
            '|Support extension']),
        ('both', 'opportunity.engagement_model', false, NULL, ARRAY[
            'discovery_phase|Discovery phase', '|Fixed scope', '|Milestone based',
            '|Forward deployed engineering', '|Dedicated team', '|Staff augmentation',
            '|AIOps annuity', '|Training']),
        ('both', 'opportunity.lost_reason', false, NULL, ARRAY[
            'price|Price', 'timing|Timing', 'competitor|Competitor', 'no_budget|No budget',
            'no_response|No response', 'scope_mismatch|Scope mismatch',
            'build_in_house|Chose to build in-house', 'other|Other']),
        ('both', 'opportunity.contact_role', false, NULL, ARRAY[
            '|Decision maker', '|Champion', '|Influencer', '|Technical evaluator',
            '|Procurement', '|User', '|Other'])
    ) AS l(scope, list_key, fixed, default_key, items)
    CROSS JOIN LATERAL unnest(l.items) WITH ORDINALITY AS i(item, pos)
    JOIN crm_workspaces w ON l.scope IN ('both', w.key)
    ON CONFLICT DO NOTHING;

    -- FR-CRM-PIPE-11.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'stage_probabilities',
           '{"discovery": 10, "qualification": 25, "proposal": 50, "negotiation": 75,
             "closed_won": 100, "closed_lost": 0}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;

    -- FR-CRM-CHG-15: the owner administers both workspaces.
    INSERT INTO crm_memberships (workspace_id, user_id, role)
    SELECT w.id, p.id, 'admin'
    FROM crm_workspaces w CROSS JOIN profiles p
    WHERE p.is_owner IS TRUE
    ON CONFLICT (workspace_id, user_id) DO NOTHING;
END;
$$;
