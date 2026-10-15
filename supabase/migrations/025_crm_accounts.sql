-- 025_crm_accounts.sql
--
-- Spec 007 (sales CRM) FR-CRM-ACC-01, -03, FR-CRM-CHG-01, -02, -12;
-- constitution v1.2.0 §I; owner decisions D1, D2.
--
-- A CRM-only account is a seller who signs in with Google and uses the CRM
-- and nothing else. They live in `profiles`, marked by `account_type`, so
-- sign-in, personal tokens and Slack delivery work for them unchanged. The
-- backend refuses them on every TVA route (`deps.current_user`) and leaves
-- them out of every TVA people population (`list_profiles`'s default). This
-- file adds the marker and the database's own backstops. No CRM table exists
-- yet; nothing here changes what TVA does for a staff account.
--
-- 1. `profiles` gains:
--    - `account_type` 'staff' | 'crm_only'. Every existing row becomes
--      'staff' by the default, so TVA behaviour is unchanged.
--    - `timezone` and `number_format`, which affect CRM display and CRM
--      digests only (FR-CRM-CHG-12). TVA rules stay in Asia/Kolkata.
--    - `deactivated_reason`: why an inactive account is inactive.
--      'crm_no_membership' (set when a CRM-only person's last workspace
--      membership goes, S1b) or 'admin' (set by Admin → People). A workspace
--      admin's invitation may reactivate only the first, so it cannot undo a
--      TVA admin's offboarding (FR-CRM-ACC-04). Existing inactive rows keep
--      NULL, which counts as 'admin'.
--    - `profiles_crm_only_shape`: a CRM-only account has the plain `user`
--      role, logs no time, has no lead, is not the owner and has no dashboard
--      access. Python refuses each of these first (admin.update_user).
--
-- 2. `tva_staff_only()` refuses to point TVA work at a CRM-only account:
--    allocating one, naming one a project's lead, logging time as one, or
--    naming one anyone's lead. Python refuses first with the same sentences;
--    this is the backstop for any path that forgets. The profile is read
--    `FOR SHARE`, so the trigger waits for an account conversion holding that
--    row's lock (crm_convert_account, S1b) and sees its result. SQLSTATE
--    CRM22 lets the backend map the error to its sentence.
--
-- 3. The browser loses every table privilege in `public`. A CRM-only seller
--    holds a valid session JWT and the anon key is public, so the policies
--    still in force (`projects_all_select`, `project_phases_all_select` of
--    008 with the column grants of 009, `holidays_all_select` and
--    `app_settings_select` of 004, the `*_self_select` policies) would let
--    them read TVA data straight from PostgREST. The frontend makes no table
--    read at all (no `.from(` or `.rpc(` in frontend/): every read goes
--    through the backend proxy. So the grants go, outright, for tables and
--    sequences, now and by default for tables created later. RLS stays
--    enabled and the policies stay in place, harmless without a grant. The
--    RLS helper functions keep their EXECUTE grants. `REVOKE ALL ON TABLE`
--    also removes the column-level grants of 009 and 012.
--    A db test (tests/db/test_crm_browser_lockdown_db.py) signs a JWT for a
--    CRM-only profile and reads every table in `public`, enumerated from
--    information_schema, so a later table cannot reopen this unnoticed.
--
-- Reversible: drop the triggers, the function, the constraints, the index and
-- the four columns; re-grant what 004, 008, 009 and 012 granted to
-- `authenticated` (and the Supabase defaults to `anon`).

-- ---------------------------------------------------------------------------
-- 1. profiles
-- ---------------------------------------------------------------------------

ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS account_type text NOT NULL DEFAULT 'staff',
    ADD COLUMN IF NOT EXISTS timezone text NOT NULL DEFAULT 'Asia/Kolkata',
    ADD COLUMN IF NOT EXISTS number_format text NOT NULL DEFAULT 'indian_short',
    ADD COLUMN IF NOT EXISTS deactivated_reason text;

ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_deactivated_reason_check;
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_account_type_check;
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_timezone_check;
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_number_format_check;
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_crm_only_shape;

ALTER TABLE profiles
    ADD CONSTRAINT profiles_deactivated_reason_check CHECK (
        deactivated_reason IS NULL OR (
            is_active = false
            AND deactivated_reason IN ('crm_no_membership', 'admin')
        )
    ),
    ADD CONSTRAINT profiles_account_type_check
        CHECK (account_type IN ('staff', 'crm_only')),
    ADD CONSTRAINT profiles_timezone_check
        CHECK (timezone IN ('Asia/Kolkata', 'Australia/Sydney')),
    ADD CONSTRAINT profiles_number_format_check
        CHECK (number_format IN ('indian_short', 'full')),
    ADD CONSTRAINT profiles_crm_only_shape CHECK (
        account_type = 'staff' OR (
            role = 'user'
            AND logs_time = false
            AND lead_id IS NULL
            AND is_owner IS NOT TRUE
            AND dashboard_access IS NOT TRUE
        )
    );

CREATE INDEX IF NOT EXISTS profiles_account_type
    ON profiles (account_type) WHERE account_type = 'crm_only';

-- ---------------------------------------------------------------------------
-- 2. tva_staff_only() — FR-CRM-CHG-02
--
-- TG_ARGV[0] names the column holding the profile id, TG_ARGV[1] is the
-- sentence. One function, four triggers.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.tva_staff_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    person uuid := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
    kind text;
BEGIN
    IF person IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT account_type INTO kind FROM profiles WHERE id = person FOR SHARE;
    IF kind = 'crm_only' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = TG_ARGV[1];
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.tva_staff_only() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.tva_staff_only() TO service_role;

DROP TRIGGER IF EXISTS allocations_tva_staff_only ON allocations;
CREATE TRIGGER allocations_tva_staff_only
    BEFORE INSERT OR UPDATE OF user_id ON allocations
    FOR EACH ROW
    EXECUTE FUNCTION public.tva_staff_only('user_id', 'A CRM-only account cannot be allocated.');

DROP TRIGGER IF EXISTS projects_tva_staff_only ON projects;
CREATE TRIGGER projects_tva_staff_only
    BEFORE INSERT OR UPDATE OF lead_id ON projects
    FOR EACH ROW
    EXECUTE FUNCTION public.tva_staff_only('lead_id', 'A CRM-only account cannot lead a project.');

DROP TRIGGER IF EXISTS time_entries_tva_staff_only ON time_entries;
CREATE TRIGGER time_entries_tva_staff_only
    BEFORE INSERT OR UPDATE OF user_id ON time_entries
    FOR EACH ROW
    EXECUTE FUNCTION public.tva_staff_only('user_id', 'A CRM-only account cannot log time.');

DROP TRIGGER IF EXISTS profiles_tva_staff_only ON profiles;
CREATE TRIGGER profiles_tva_staff_only
    BEFORE INSERT OR UPDATE OF lead_id ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.tva_staff_only('lead_id', 'A CRM-only account cannot be anyone''s lead.');

-- ---------------------------------------------------------------------------
-- 3. Browser privileges — constitution §I, D1, D2
-- ---------------------------------------------------------------------------

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
