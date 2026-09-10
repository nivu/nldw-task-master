-- 009_management.sql
--
-- Spec 003 — the manager role, project financials, non-project activities.
--
-- This migration reverses spec 002's Q-05 ("hours only, no money") by adding
-- cost rates and revenue. It does so by adding a VISIBILITY TIER above lead,
-- not by exposing rates to leads. Read spec 003 §1.1 and §4.3 before touching
-- any policy below.

-- ============================================================
-- Roles — FR-ROLE-01
--
-- `manager` sits between lead and admin: runs projects, sees all projects and
-- all money, manages no people. Postgres names an inline CHECK constraint
-- <table>_<column>_check, which is what 001's `role IN (...)` produced.
-- ============================================================
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_role_check;
ALTER TABLE profiles
    ADD CONSTRAINT profiles_role_check CHECK (role IN ('user', 'lead', 'manager', 'admin'));

-- ============================================================
-- Cost rate — FR-FIN-01
--
-- A fully-loaded hourly cost the company chooses to attribute to a person.
-- It is NOT salary and must never be labelled as such (spec 003 §9). Set by an
-- admin; readable by managers and admins only (RLS below withholds it from
-- everyone else, including the person it describes — spec 003 Q-01).
-- ============================================================
ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS cost_rate_hourly numeric(10,2)
        CHECK (cost_rate_hourly IS NULL OR cost_rate_hourly >= 0);

-- ============================================================
-- Revenue — FR-FIN-02
--
-- What the project is worth: contract value or internal budget. A number a
-- manager types in; nothing is invoiced from it. numeric, never float — this
-- is money that will be quoted in a budget conversation.
-- ============================================================
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS revenue numeric(14,2)
        CHECK (revenue IS NULL OR revenue >= 0);

-- ============================================================
-- Activities — FR-ACT
--
-- A time entry is for a project OR an activity, never both, never neither.
-- Learning, internal work and admin are real days that must count as logged
-- (002 FR-ANALYTICS-05) without belonging to any project's effort.
-- ============================================================
ALTER TABLE time_entries ALTER COLUMN project_id DROP NOT NULL;

ALTER TABLE time_entries
    ADD COLUMN IF NOT EXISTS activity text
        CHECK (activity IS NULL OR activity IN ('learning', 'internal', 'admin', 'other'));

ALTER TABLE time_entries DROP CONSTRAINT IF EXISTS time_entries_project_xor_activity;
ALTER TABLE time_entries
    ADD CONSTRAINT time_entries_project_xor_activity
        CHECK ((project_id IS NULL) <> (activity IS NULL));

-- The single unique index from 008 assumed project_id was always present.
-- Replace it with one per kind so the "one line per thing per day" rule holds
-- for activities too, without a NULL project_id defeating it.
DROP INDEX IF EXISTS time_entries_one_per_project_day;

CREATE UNIQUE INDEX IF NOT EXISTS time_entries_one_per_project_day
    ON time_entries (user_id, date, project_id)
    WHERE project_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS time_entries_one_per_activity_day
    ON time_entries (user_id, date, activity)
    WHERE activity IS NOT NULL;

-- ============================================================
-- Cost rate snapshot — FR-FIN-03, spec 003 Q-05
--
-- Captured onto the entry when it is saved. COGS is computed from THIS, never
-- from the person's current rate, so a rate change next year cannot silently
-- re-price last year's project. Same reasoning as 008 storing phase_id.
-- NULL for activities (FR-ACT-05) and for anyone unrated at the time.
-- ============================================================
ALTER TABLE time_entries
    ADD COLUMN IF NOT EXISTS cost_rate_snapshot numeric(10,2)
        CHECK (cost_rate_snapshot IS NULL OR cost_rate_snapshot >= 0);

-- ============================================================
-- RLS helpers — the new tier
-- ============================================================
CREATE OR REPLACE FUNCTION public.is_manager()
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM profiles
    WHERE id = auth.uid() AND role IN ('manager', 'admin') AND is_active = true
  );
$$;

-- A manager needs the team view too (who is available today), so the lead
-- helper widens. This does NOT let a manager approve leave: can_decide in the
-- backend still requires lead_id, and RLS grants no writes to the browser.
CREATE OR REPLACE FUNCTION public.is_lead()
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM profiles
    WHERE id = auth.uid() AND role IN ('lead', 'manager', 'admin') AND is_active = true
  );
$$;

REVOKE ALL ON FUNCTION public.is_manager() FROM public;
GRANT EXECUTE ON FUNCTION public.is_manager() TO authenticated;

-- ============================================================
-- RLS — spec 003 §4.3: nobody below manager can see money
--
-- The browser talks to Supabase directly for auth and could, in principle,
-- read these tables with the anon key under RLS. The backend is the real
-- boundary (it selects columns per caller), but the browser connection must
-- not be a second way in. Column-level privileges are how RLS withholds a
-- COLUMN rather than a row.
--
-- THE POSTGRES TRAP, so nobody "simplifies" this back: `REVOKE SELECT (col)`
-- only removes a column-level grant. It cannot carve a column out of a
-- table-level SELECT — and Supabase grants table-level SELECT on every public
-- table to `authenticated` and `anon`. The first version of this migration did
-- exactly that revoke and withheld nothing; a test that read the column as the
-- browser role succeeded silently. The only working form is: revoke the table,
-- then grant the permitted columns by name. A consequence worth knowing: a
-- `select *` on these tables now fails for the browser role, which is the
-- desired failure — loud, not a leak.
-- ============================================================
REVOKE SELECT ON profiles FROM authenticated, anon;
GRANT SELECT (id, email, display_name, role, lead_id, is_active, created_at, updated_at)
    ON profiles TO authenticated;

REVOKE SELECT ON projects FROM authenticated, anon;
GRANT SELECT (id, name, client, is_archived, created_at, updated_at, created_by)
    ON projects TO authenticated;

REVOKE SELECT ON time_entries FROM authenticated, anon;
GRANT SELECT (id, user_id, date, project_id, phase_id, activity, hours_office, hours_home,
              note, created_at, updated_at)
    ON time_entries TO authenticated;

-- Managers read all allocations and all time entries (FR-ROLE-05). The
-- individual-timesheet rule (002 Q-08) gains managers, matching spec 003 §7.
DROP POLICY IF EXISTS "allocations_lead_select" ON allocations;
CREATE POLICY "allocations_lead_select" ON allocations
    FOR SELECT TO authenticated
    USING (public.is_lead_of(user_id) OR public.is_manager());

DROP POLICY IF EXISTS "time_entries_lead_select" ON time_entries;
CREATE POLICY "time_entries_lead_select" ON time_entries
    FOR SELECT TO authenticated
    USING (public.is_lead_of(user_id) OR public.is_manager());

-- A manager sees everyone's profile (to allocate them). Money column is
-- already withheld above, so this exposes name and role only.
DROP POLICY IF EXISTS "profiles_lead_select" ON profiles;
CREATE POLICY "profiles_lead_select" ON profiles
    FOR SELECT TO authenticated
    USING (public.is_lead_of(id) OR public.is_manager());

-- ============================================================
-- Settings — spec 003 Q-07
-- ============================================================
INSERT INTO app_settings (key, value, description) VALUES
    (
        'currency_code',
        '"INR"',
        'Spec 003 Q-07. The single currency revenue and cost rates are in. '
        'Formatting only — no conversion happens anywhere.'
    )
ON CONFLICT (key) DO NOTHING;
