-- 003_rls_helpers.sql
--
-- SECURITY DEFINER authorisation helpers.
--
-- WHY THIS PATTERN EXISTS — read this before writing any policy in this
-- project:
--
-- Every authorisation question here ("am I an admin?", "is this person my
-- report?") is answered by reading `profiles`. A policy ON profiles that
-- itself queries profiles recurses. PostgreSQL does not raise a clear error
-- for this; it returns NULL, which RLS treats as "deny". In practice that
-- presents as login silently breaking and rows mysteriously disappearing —
-- very hard to diagnose after the fact.
--
-- SECURITY DEFINER functions run as the function owner and do NOT re-enter
-- RLS, which breaks the recursion. Route every role or reporting-line check
-- through a helper below instead of inlining a subquery in a policy.
--
-- `SET search_path = public` is a SECURITY REQUIREMENT, not style. Without it
-- a SECURITY DEFINER function is vulnerable to search-path hijacking.
--
-- Naming note: `current_app_role`, not `current_role` — `current_role` is a
-- reserved SQL keyword in PostgreSQL and cannot be used as a function name.

-- The signed-in person's application role, or NULL if they have no profile
-- or have been deactivated. A deactivated user (FR-AUTH-06) resolves to NULL
-- here, so every policy below denies them without needing its own is_active
-- clause.
CREATE OR REPLACE FUNCTION public.current_app_role()
RETURNS text LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
  SELECT role FROM profiles WHERE id = auth.uid() AND is_active = true;
$$;

CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM profiles
    WHERE id = auth.uid() AND role = 'admin' AND is_active = true
  );
$$;

CREATE OR REPLACE FUNCTION public.is_lead()
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM profiles
    WHERE id = auth.uid() AND role IN ('lead', 'admin') AND is_active = true
  );
$$;

-- Is p_user_id one of my direct reports?
--
-- FR-APPR-05: a lead MUST NOT act on bookings outside their own reports. This
-- is the function that sentence turns into. An admin is not automatically the
-- lead of everyone — policies grant admins access with a separate is_admin()
-- clause, so the two ideas stay separable.
CREATE OR REPLACE FUNCTION public.is_lead_of(p_user_id uuid)
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM profiles
    WHERE id = p_user_id AND lead_id = auth.uid()
  );
$$;

REVOKE ALL ON FUNCTION public.current_app_role() FROM public;
REVOKE ALL ON FUNCTION public.is_admin()         FROM public;
REVOKE ALL ON FUNCTION public.is_lead()          FROM public;
REVOKE ALL ON FUNCTION public.is_lead_of(uuid)   FROM public;

GRANT EXECUTE ON FUNCTION public.current_app_role() TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_admin()         TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_lead()          TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_lead_of(uuid)   TO authenticated;
