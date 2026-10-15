-- 043_crm_dashboards_io.sql
--
-- Spec 007 (sales CRM) FR-CRM-DASH-01 to -04, FR-CRM-IO-01 to -05,
-- FR-CRM-LIST-06 (bookings targets), FR-CRM-AUD-01; data-model §3.24;
-- contracts §2.10.
--
-- The dashboard's targets, CSV import's saved mappings, and the audit row
-- of an export (1b).
--
-- 1. `crm_bookings_targets` (audited): what a workspace means to book in a
--    month, a quarter or a financial year (April to March), for the whole
--    workspace (`entity` NULL) or for one entity of Labs. The bookings panel
--    colours a figure only against one of these (FR-CRM-DASH-02, "no
--    colour when no target"). A period starts on its first day: any month's
--    1st, a quarter's 1 April, 1 July, 1 October or 1 January, an FY's
--    1 April. One target per workspace, entity, kind and period.
-- 2. `crm_csv_mappings` (audited): a named column → field mapping per
--    object, so an import of the same kind of file maps itself
--    (FR-CRM-IO-01). The mapping holds header names and field names only,
--    never a row of the file.
-- 3. `crm_record_export(actor, detail)`: the `export.view` or
--    `export.workspace` audit row of an export (FR-CRM-IO-04 "every export
--    is audited"), with what was exported (the object, the columns, the row
--    count) and never a value of it. An export writes nothing else, so
--    `crm_apply`'s `audit_only`, which takes only the audited table's own
--    columns, cannot carry it.
--
-- EXECUTE on the function is revoked from PUBLIC, anon and authenticated
-- (data-model §5.12).
--
-- Reversible: DROP FUNCTION crm_record_export(jsonb, jsonb); DROP TABLE
-- crm_csv_mappings, crm_bookings_targets (their audit rows stay).

-- ---------------------------------------------------------------------------
-- 1. crm_bookings_targets — data-model §3.24 (audited)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_bookings_targets (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    -- NULL: the whole workspace. Entities are Labs's (FR-CRM-WS-06); that a
    -- workspace has them is checked in Python.
    entity       text CHECK (entity IN ('nunnari_labs', 'deepweaver')),
    period_kind  text NOT NULL CHECK (period_kind IN ('month', 'quarter', 'fy')),
    period_start date NOT NULL,
    amount_inr   numeric(14,2) NOT NULL CHECK (amount_inr >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_bookings_targets_workspace_id UNIQUE (workspace_id, id),
    -- A period starts on its first day; a quarter and an FY follow the
    -- financial year (April to March).
    CONSTRAINT crm_bookings_targets_period_start CHECK (
        extract(day FROM period_start) = 1
        AND (period_kind <> 'quarter' OR extract(month FROM period_start) IN (1, 4, 7, 10))
        AND (period_kind <> 'fy' OR extract(month FROM period_start) = 4)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_bookings_targets_period
    ON crm_bookings_targets (workspace_id, coalesce(entity, ''), period_kind, period_start);

-- ---------------------------------------------------------------------------
-- 2. crm_csv_mappings — data-model §3.24 (audited)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_csv_mappings (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    object       text NOT NULL CHECK (object IN ('company', 'person', 'opportunity')),
    name         text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 80),
    -- {"<CSV header>": "<field>"}; checked against the field registry in Python.
    mapping      jsonb NOT NULL CHECK (jsonb_typeof(mapping) = 'object'),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_csv_mappings_workspace_id UNIQUE (workspace_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_csv_mappings_name
    ON crm_csv_mappings (workspace_id, object, lower(name));

-- ---------------------------------------------------------------------------
-- Triggers, RLS, privileges — data-model §2
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS crm_bookings_targets_touch ON crm_bookings_targets;
CREATE TRIGGER crm_bookings_targets_touch BEFORE UPDATE ON crm_bookings_targets
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_csv_mappings_touch ON crm_csv_mappings;
CREATE TRIGGER crm_csv_mappings_touch BEFORE UPDATE ON crm_csv_mappings
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS crm_bookings_targets_crm_audit ON crm_bookings_targets;
CREATE TRIGGER crm_bookings_targets_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_bookings_targets
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_csv_mappings_crm_audit ON crm_csv_mappings;
CREATE TRIGGER crm_csv_mappings_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_csv_mappings
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_bookings_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_csv_mappings     ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_bookings_targets, crm_csv_mappings FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. crm_record_export — the audit row of an export (FR-CRM-IO-04)
-- ---------------------------------------------------------------------------

-- `p_detail`: {kind: 'view' | 'workspace', object?, table?, columns?, rows,
-- bytes}. The row names the exported table (the workspace itself for a
-- whole-workspace export) and carries the detail as `after`.
CREATE OR REPLACE FUNCTION public.crm_record_export(p_actor jsonb, p_detail jsonb)
RETURNS bigint
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws     uuid := (p_actor ->> 'workspace_id')::uuid;
    who    uuid := (p_actor ->> 'user_id')::uuid;
    v_kind text := p_detail ->> 'kind';
    v_tbl  text := coalesce(p_detail ->> 'table', 'crm_workspaces');
    new_id bigint;
BEGIN
    IF ws IS NULL OR who IS NULL OR coalesce(p_actor ->> 'kind', '') <> 'user' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'An export needs a workspace and a person.';
    END IF;
    IF v_kind IS NULL OR v_kind NOT IN ('view', 'workspace') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'An export is of a view or a workspace.';
    END IF;
    IF v_tbl !~ '^crm_[a-z_]+$' OR to_regclass(format('public.%I', v_tbl)) IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That is not a CRM table.';
    END IF;
    -- The exporter must be a member of the workspace now.
    IF NOT EXISTS (SELECT 1 FROM crm_memberships WHERE workspace_id = ws AND user_id = who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such workspace.';
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, after)
    VALUES (who, 'user', p_actor ->> 'channel', ws, 'export.' || v_kind, v_tbl,
            CASE WHEN v_tbl = 'crm_workspaces' THEN ws::text END,
            p_detail - 'table')
    RETURNING id INTO new_id;
    RETURN new_id;
END;
$$;

REVOKE ALL ON FUNCTION public.crm_record_export(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_record_export(jsonb, jsonb) TO service_role;
