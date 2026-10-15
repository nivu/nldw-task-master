-- 038_crm_flags.sql
--
-- Spec 007 (sales CRM) FR-CRM-FLAG-01 to -05, FR-CRM-VIEW-11, FR-CRM-REC-01,
-- FR-CRM-AUTO-01 (automation 4); Q-20; data-model §3.21, §7.5; plan §5.11.
--
-- Follow-up and deal-health flags, stored on the records they are about, so
-- a list filters and sorts by them on the server like any other field.
--
-- 1. `flag_level` (none, amber, red: the worst flag, FR-CRM-FLAG-03),
--    `flags` (each flag with its kind, level, since and detail) and
--    `flags_computed_at` (shown beside the dot, FR-CRM-FLAG-02) on
--    companies, people, deals and tasks, with a partial index for the
--    flagged rows. The values are computed in Python
--    (domain/crm/flags.py) and written by `crm_set_flags` below, after a
--    relevant write and by the 30-minute job.
-- 2. `crm_touch_updated_at()`: these four tables' BEFORE UPDATE trigger now
--    leaves `updated_at` alone when only the flag columns change. Flags are
--    derived state: a recompute is not an edit, must not reorder "recently
--    updated" lists, and must not make a person's open edit stale (CRM09).
--    026's audit trigger already writes no row for such an update and needs
--    no actor for it.
-- 3. `crm_set_flags(workspace, table, rows, at)`: writes computed flags for
--    rows of one of the four tables, in the workspace only. Returns how many
--    rows' flags changed.
-- 4. `crm_flag_facts(workspace, deal_ids, company_ids)`: what the flags need
--    that PostgREST cannot express in one request: each deal's last activity
--    (stage change, note or task; email arrives in 1b) for Stale deal, and
--    which companies have an open task, for Loose end (spec §6.2). Ids
--    travel in the request body, never the URL.
-- 5. `crm_query_rank()` and `crm_query_rank_type()` replaced (034's, plus one
--    branch each): a flag sorts none, amber, red, so "red first" is a
--    descending sort (FR-CRM-FLAG-05, -VIEW-11).
-- 6. Seeds (data-model §7.5): `flag_level` in the view registry for the four
--    objects, and the shared "Follow-ups owed" companies view in each
--    workspace (FR-CRM-VIEW-11): amber or red, red first.
--
-- EXECUTE on the functions is revoked from PUBLIC, anon and authenticated
-- (data-model §5.12).
--
-- Reversible: restore 034's crm_query_rank() and crm_query_rank_type() and
-- `touch_updated_at()` on the four tables; DELETE the follow_ups_owed views
-- and the flag_level registry rows; DROP FUNCTION crm_flag_facts(uuid, uuid[],
-- uuid[]), crm_set_flags(uuid, text, jsonb, timestamptz),
-- crm_touch_updated_at(); DROP INDEX the four `*_flagged`; ALTER TABLE …
-- DROP COLUMN flag_level, flags, flags_computed_at on the four tables.

-- ---------------------------------------------------------------------------
-- 1. Flag columns — data-model §3.21
-- ---------------------------------------------------------------------------

ALTER TABLE crm_companies
    ADD COLUMN IF NOT EXISTS flag_level text NOT NULL DEFAULT 'none'
        CHECK (flag_level IN ('none', 'amber', 'red')),
    ADD COLUMN IF NOT EXISTS flags jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(flags) = 'array'),
    ADD COLUMN IF NOT EXISTS flags_computed_at timestamptz;
ALTER TABLE crm_people
    ADD COLUMN IF NOT EXISTS flag_level text NOT NULL DEFAULT 'none'
        CHECK (flag_level IN ('none', 'amber', 'red')),
    ADD COLUMN IF NOT EXISTS flags jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(flags) = 'array'),
    ADD COLUMN IF NOT EXISTS flags_computed_at timestamptz;
ALTER TABLE crm_opportunities
    ADD COLUMN IF NOT EXISTS flag_level text NOT NULL DEFAULT 'none'
        CHECK (flag_level IN ('none', 'amber', 'red')),
    ADD COLUMN IF NOT EXISTS flags jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(flags) = 'array'),
    ADD COLUMN IF NOT EXISTS flags_computed_at timestamptz;
ALTER TABLE crm_tasks
    ADD COLUMN IF NOT EXISTS flag_level text NOT NULL DEFAULT 'none'
        CHECK (flag_level IN ('none', 'amber', 'red')),
    ADD COLUMN IF NOT EXISTS flags jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(flags) = 'array'),
    ADD COLUMN IF NOT EXISTS flags_computed_at timestamptz;

CREATE INDEX IF NOT EXISTS crm_companies_flagged
    ON crm_companies (workspace_id, flag_level) WHERE deleted_at IS NULL AND flag_level <> 'none';
CREATE INDEX IF NOT EXISTS crm_people_flagged
    ON crm_people (workspace_id, flag_level) WHERE deleted_at IS NULL AND flag_level <> 'none';
CREATE INDEX IF NOT EXISTS crm_opportunities_flagged
    ON crm_opportunities (workspace_id, flag_level)
    WHERE deleted_at IS NULL AND flag_level <> 'none';
CREATE INDEX IF NOT EXISTS crm_tasks_flagged
    ON crm_tasks (workspace_id, flag_level) WHERE deleted_at IS NULL AND flag_level <> 'none';

-- ---------------------------------------------------------------------------
-- 2. crm_touch_updated_at — a recompute is not an edit
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['flag_level', 'flags', 'flags_computed_at', 'updated_at'])
       = (to_jsonb(OLD) - ARRAY['flag_level', 'flags', 'flags_computed_at', 'updated_at']) THEN
        NEW.updated_at := OLD.updated_at;
    ELSE
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS crm_companies_touch ON crm_companies;
CREATE TRIGGER crm_companies_touch BEFORE UPDATE ON crm_companies
    FOR EACH ROW EXECUTE FUNCTION public.crm_touch_updated_at();
DROP TRIGGER IF EXISTS crm_people_touch ON crm_people;
CREATE TRIGGER crm_people_touch BEFORE UPDATE ON crm_people
    FOR EACH ROW EXECUTE FUNCTION public.crm_touch_updated_at();
DROP TRIGGER IF EXISTS crm_opportunities_touch ON crm_opportunities;
CREATE TRIGGER crm_opportunities_touch BEFORE UPDATE ON crm_opportunities
    FOR EACH ROW EXECUTE FUNCTION public.crm_touch_updated_at();
DROP TRIGGER IF EXISTS crm_tasks_touch ON crm_tasks;
CREATE TRIGGER crm_tasks_touch BEFORE UPDATE ON crm_tasks
    FOR EACH ROW EXECUTE FUNCTION public.crm_touch_updated_at();

-- ---------------------------------------------------------------------------
-- 3. crm_set_flags — the one writer of the flag columns
-- ---------------------------------------------------------------------------

-- `p_rows`: [{id, flag_level, flags}]. Rows of another workspace, and ids
-- that do not exist, are not touched. Every row given gets `p_at` as its
-- computed time; the count returned is of the rows whose flags changed.
CREATE OR REPLACE FUNCTION public.crm_set_flags(
    p_workspace uuid, p_table text, p_rows jsonb, p_at timestamptz
)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    changed integer;
BEGIN
    IF p_table NOT IN ('crm_companies', 'crm_people', 'crm_opportunities', 'crm_tasks') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That table has no flags.';
    END IF;
    IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Flags come as a list.';
    END IF;
    EXECUTE format(
        'SELECT count(*) FROM %I t
         JOIN jsonb_to_recordset($1) AS r(id uuid, flag_level text, flags jsonb) ON r.id = t.id
         WHERE t.workspace_id = $2
           AND (t.flag_level, t.flags) IS DISTINCT FROM (r.flag_level, r.flags)',
        p_table)
    INTO changed USING p_rows, p_workspace;
    EXECUTE format(
        'UPDATE %I t
         SET flag_level = r.flag_level, flags = r.flags, flags_computed_at = $3
         FROM jsonb_to_recordset($1) AS r(id uuid, flag_level text, flags jsonb)
         WHERE t.workspace_id = $2 AND t.id = r.id',
        p_table)
    USING p_rows, p_workspace, p_at;
    RETURN changed;
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_flag_facts — Stale deal and Loose end (spec §6.2)
-- ---------------------------------------------------------------------------

-- `{activity: {deal id: last activity}, open_task_companies: [company id]}`
-- for the given live deals and companies, or every one in the workspace when
-- a list is NULL. A deal's last activity is the latest of its creation, its
-- last stage change, a note on it (a logged call or meeting on the day it
-- happened) and a task on it being added or done.
CREATE OR REPLACE FUNCTION public.crm_flag_facts(
    p_workspace uuid, p_deal_ids uuid[], p_company_ids uuid[]
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
        ), '[]'::jsonb)
    );
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_query_rank, crm_query_rank_type — 034's, plus flags
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_query_rank(p_type text, p_column_sql text, p_expr text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, extensions
AS $$
BEGIN
    RETURN CASE
        WHEN p_type = 'select' THEN format(
            '(SELECT o.position FROM crm_select_options o WHERE o.workspace_id = '
            't.workspace_id AND o.id = %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql = 'lead_stage' THEN format(
            'array_position(''{new,contacted,nurturing,qualified,deal_open,customer,partner,'
            'disqualified}''::text[], %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql IN ('stage', 'lost_from_stage') THEN format(
            'array_position(''{discovery,qualification,proposal,negotiation,closed_won,'
            'closed_lost}''::text[], %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql = 'status' THEN format(
            'array_position(''{todo,in_progress,done}''::text[], %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql = 'bucket' THEN format(
            'array_position(''{today,this_week,this_month,later}''::text[], %s)', p_expr)
        -- 038: none, amber, red, so red first is a descending sort.
        WHEN p_type = 'flag' THEN format(
            'array_position(''{none,amber,red}''::text[], %s)', p_expr)
        WHEN p_type = 'person' THEN format(
            '(SELECT lower(p.display_name) FROM profiles p WHERE p.id = %s)', p_expr)
        WHEN p_type = 'relation' AND p_column_sql = 'company_id' THEN format(
            '(SELECT lower(c.name) FROM crm_companies c WHERE c.workspace_id = '
            't.workspace_id AND c.id = %s)', p_expr)
        WHEN p_type = 'relation' AND p_column_sql = 'point_of_contact_id' THEN format(
            '(SELECT lower(btrim(c.first_name || '' '' || coalesce(c.last_name, ''''))) FROM '
            'crm_people c WHERE c.workspace_id = t.workspace_id AND c.id = %s)', p_expr)
        WHEN p_type IN ('text', 'long_text') THEN 'lower(' || p_expr || ')'
        WHEN p_type = 'relation' THEN '(' || p_expr || ')::text'
        ELSE '(' || p_expr || ')'
    END;
END;
$$;

CREATE OR REPLACE FUNCTION public.crm_query_rank_type(p_type text, p_column_sql text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public, extensions
AS $$
    SELECT CASE
        WHEN p_type = 'select' THEN 'integer'
        WHEN p_type = 'fixed_select'
             AND p_column_sql IN ('lead_stage', 'stage', 'lost_from_stage', 'status', 'bucket')
            THEN 'integer'
        WHEN p_type = 'flag' THEN 'integer'
        WHEN p_type IN ('number', 'currency') THEN 'numeric'
        WHEN p_type = 'date' THEN 'date'
        WHEN p_type = 'datetime' THEN 'timestamptz'
        WHEN p_type = 'boolean' THEN 'boolean'
        ELSE 'text'
    END;
$$;

REVOKE ALL ON FUNCTION public.crm_touch_updated_at() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_set_flags(uuid, text, jsonb, timestamptz)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_flag_facts(uuid, uuid[], uuid[])
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query_rank(text, text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query_rank_type(text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_touch_updated_at() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_set_flags(uuid, text, jsonb, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_flag_facts(uuid, uuid[], uuid[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query_rank(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query_rank_type(text, text) TO service_role;

-- ---------------------------------------------------------------------------
-- 6. Seeds — data-model §7.5
-- ---------------------------------------------------------------------------

-- The registry: (object, field, column_sql, type, is_amount, filterable,
-- sortable, groupable, aggregatable, workspace_scope), as 034.
INSERT INTO crm_object_fields (object, field, column_sql, type, is_amount, filterable, sortable,
                               groupable, aggregatable, workspace_scope)
VALUES
    ('company', 'flag_level', 'flag_level', 'flag', false, true, true, true, false, 'all'),
    ('person', 'flag_level', 'flag_level', 'flag', false, true, true, true, false, 'all'),
    ('opportunity', 'flag_level', 'flag_level', 'flag', false, true, true, true, false, 'all'),
    ('task', 'flag_level', 'flag_level', 'flag', false, true, true, true, false, 'all')
ON CONFLICT (object, field) DO NOTHING;

-- FR-CRM-VIEW-11 (1b): Follow-ups owed, as 034's rows are written.
DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    INSERT INTO crm_saved_views
        (workspace_id, object, name, icon, kind, visibility, config, system_key, is_default,
         position)
    SELECT w.id, v.object, v.name, v.icon, v.kind, 'shared', v.config::jsonb, v.system_key,
           v.is_default, v.position
    FROM (VALUES
    ('follow_ups_owed', 'company', 'Follow-ups owed', 'bell-ring', 'table', false, 5,
     '{"filters": {"op": "and", "items": [{"field": "flag_level", "operator": "any_of", "value": ["amber", "red"]}]}, "sorts": [{"field": "flag_level", "dir": "desc"}, {"field": "name", "dir": "asc"}], "columns": [{"field": "name", "width": 240}, {"field": "flag_level", "width": 150}, {"field": "lead_stage", "width": 140}, {"field": "owner_id", "width": 140}, {"field": "last_contact_at", "width": 150}]}')
    ) AS v(system_key, object, name, icon, kind, is_default, position, config)
    CROSS JOIN crm_workspaces w
    ON CONFLICT DO NOTHING;
END;
$$;
