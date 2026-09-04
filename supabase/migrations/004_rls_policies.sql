-- 004_rls_policies.sql
--
-- Row-Level Security. Every table is deny-by-default once RLS is enabled;
-- access is granted only by the policies below.
--
-- Rule for this project: NEVER inline a `SELECT ... FROM profiles` subquery
-- into a policy. Call the helpers from 003_rls_helpers.sql. See that file.
--
-- WHAT THESE POLICIES ARE FOR. The FastAPI backend uses the service-role key,
-- which bypasses RLS entirely, and it is the only writer in this system. These
-- policies therefore guard the *browser's* anon-key connection. They are
-- defence in depth, not the primary authorisation mechanism — the backend
-- enforces its own rules in app/api/deps.py. Both layers must agree; if you
-- change one, change the other.
--
-- Consequently the browser is granted SELECT and nothing else. Every write
-- goes through the backend, which can enforce the rules RLS cannot express:
-- the lock window (§6.3), allowance sufficiency (FR-BOOK-05), and the state
-- machine (§6.4).

ALTER TABLE profiles     ENABLE ROW LEVEL SECURITY;
ALTER TABLE holidays     ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings     ENABLE ROW LEVEL SECURITY;
ALTER TABLE allowances   ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log    ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- profiles
--
-- The self-select policy MUST stay non-recursive — a plain `id = auth.uid()`
-- comparison with no subquery. It resolves the chicken-and-egg problem of
-- "finding out who I am requires reading profiles".
-- ============================================================
DROP POLICY IF EXISTS "profiles_self_select" ON profiles;
CREATE POLICY "profiles_self_select" ON profiles
    FOR SELECT TO authenticated
    USING (id = auth.uid());

-- A lead can see their own reports; an admin can see everyone. Needed for the
-- team view (FR-LEAD-01) and the admin panel (FR-ADMIN-05).
DROP POLICY IF EXISTS "profiles_lead_select" ON profiles;
CREATE POLICY "profiles_lead_select" ON profiles
    FOR SELECT TO authenticated
    USING (public.is_lead_of(id) OR public.is_admin());

-- Note there is no profiles_self_update policy. Self-service profile editing
-- is an explicit non-goal for V1 (spec §2.2), and role/lead_id must never be
-- self-assignable — that would be privilege escalation. Password changes go
-- through Supabase Auth (FR-AUTH-05), not this table.

-- ============================================================
-- holidays — FR-HOL-02: they apply to everyone, so everyone can read them.
-- FR-HOL-07: only an admin may modify, and modification happens through the
-- backend, so no write policy is granted to the browser at all.
-- ============================================================
DROP POLICY IF EXISTS "holidays_all_select" ON holidays;
CREATE POLICY "holidays_all_select" ON holidays
    FOR SELECT TO authenticated
    USING (true);

-- ============================================================
-- bookings
--
-- FR-AUTH-04: a user MUST NOT read another user's bookings or reasons.
-- NFR-05: reasons are personal data — readable by the person, their lead, and
-- admins, and nobody else.
--
-- Row-level access is the same for all three parties; the narrower rule in
-- Q-06 (a lead sees categories in the team list but not reasons) is a
-- column-level concern the API enforces by not selecting `reason` for that
-- view. RLS grants the row; the API decides which columns leave the building.
-- ============================================================
DROP POLICY IF EXISTS "bookings_self_select" ON bookings;
CREATE POLICY "bookings_self_select" ON bookings
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS "bookings_lead_select" ON bookings;
CREATE POLICY "bookings_lead_select" ON bookings
    FOR SELECT TO authenticated
    USING (public.is_lead_of(user_id) OR public.is_admin());

-- ============================================================
-- allowances — a user sees their own and the organisation defaults, because
-- FR-BOOK-12 requires showing the remaining allowance before confirming and
-- the default row is part of that sum. Admins see all rows (FR-ADMIN-01).
-- ============================================================
DROP POLICY IF EXISTS "allowances_self_select" ON allowances;
CREATE POLICY "allowances_self_select" ON allowances
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR user_id IS NULL);

DROP POLICY IF EXISTS "allowances_lead_select" ON allowances;
CREATE POLICY "allowances_lead_select" ON allowances
    FOR SELECT TO authenticated
    USING (public.is_lead_of(user_id) OR public.is_admin());

-- ============================================================
-- app_settings — readable by any signed-in user so the UI can reflect the
-- active policy (for example whether over-booking is blocked). Writes are
-- admin-only and go through the backend.
-- ============================================================
DROP POLICY IF EXISTS "app_settings_select" ON app_settings;
CREATE POLICY "app_settings_select" ON app_settings
    FOR SELECT TO authenticated
    USING (true);

-- ============================================================
-- audit_log — admin read only.
--
-- No policy grants INSERT to the browser: entries are written by the backend
-- under the service role. No policy grants UPDATE or DELETE to anyone,
-- including admins — 005_audit_triggers.sql makes that impossible even for
-- the service role (NFR-06).
-- ============================================================
DROP POLICY IF EXISTS "audit_log_admin_select" ON audit_log;
CREATE POLICY "audit_log_admin_select" ON audit_log
    FOR SELECT TO authenticated
    USING (public.is_admin());
