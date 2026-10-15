-- seed.sql — DEVELOPMENT FIXTURES ONLY.
--
-- Run automatically by `npx supabase db reset`. Never applied to a hosted
-- project: this is not a migration and is not part of the deploy path.
--
-- Everything here is disposable demo data. The one thing that is NOT
-- disposable is the shape it demonstrates: FR-AUTH-02 forbids
-- self-registration, so a real account is always created by an admin through
-- POST /api/v1/admin/users, which creates the auth.users row and the profiles
-- row together. This file writes both by hand only because there is no admin
-- yet to bootstrap the first one.
--
-- Every password below is `portal123`. Obviously never do this anywhere real.

-- ============================================================
-- People (spec §3, and the names used in the §4 scenarios)
-- ============================================================
-- The four empty-string columns below are not decoration. GoTrue scans
-- confirmation_token, recovery_token, email_change and email_change_token_new
-- into non-nullable Go strings, and those columns have no database default. A
-- NULL there makes every sign-in fail with "Database error querying schema" —
-- a 500 that names neither the table nor the column. Seed them as ''.
INSERT INTO auth.users
    (id, instance_id, aud, role, email, encrypted_password,
     email_confirmed_at, created_at, updated_at,
     raw_app_meta_data, raw_user_meta_data,
     confirmation_token, recovery_token, email_change, email_change_token_new)
VALUES
    ('a0000000-0000-4000-8000-000000000001', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'vinita@nunnari.example',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '6 months', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Vinita"}', '', '', '', ''),
    ('a0000000-0000-4000-8000-000000000002', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'devansh.nl@gmail.com',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '6 months', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Devansh"}', '', '', '', ''),
    ('a0000000-0000-4000-8000-000000000003', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'sriram.nl@gmail.com',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '6 months', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Sriram"}', '', '', '', ''),
    ('a0000000-0000-4000-8000-000000000004', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'deepika.nl@gmail.com',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '6 months', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Deepika"}', '', '', '', ''),
    ('a0000000-0000-4000-8000-000000000005', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'tarun.nl@gmail.com',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '6 months', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Tarun"}', '', '', '', '');

-- GoTrue requires a matching identity row for email/password sign-in.
INSERT INTO auth.identities
    (id, provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at)
SELECT gen_random_uuid(), u.id::text, u.id,
       jsonb_build_object('sub', u.id::text, 'email', u.email, 'email_verified', true),
       'email', now(), now(), now()
FROM auth.users u
WHERE u.email LIKE '%@nunnari.example' OR u.email LIKE '%.nl@gmail.com';

-- ============================================================
-- Profiles and the reporting line
--
-- Devansh is the lead. Vinita is admin and has no lead — per Q-05 her leave,
-- like a lead's, falls to an admin. Sriram is a manager (spec 003): he runs
-- projects and sees money, but Devansh still approves his leave.
-- ============================================================
INSERT INTO profiles (id, email, display_name, role, lead_id, created_at) VALUES
    ('a0000000-0000-4000-8000-000000000001', 'vinita@nunnari.example', 'Vinita',  'admin', NULL, now() - interval '6 months'),
    ('a0000000-0000-4000-8000-000000000002', 'devansh.nl@gmail.com',  'Devansh', 'lead',  NULL, now() - interval '6 months'),
    ('a0000000-0000-4000-8000-000000000003', 'sriram.nl@gmail.com',   'Sriram',  'manager', 'a0000000-0000-4000-8000-000000000002', now() - interval '6 months'),
    ('a0000000-0000-4000-8000-000000000004', 'deepika.nl@gmail.com',  'Deepika', 'user',  'a0000000-0000-4000-8000-000000000002', now() - interval '6 months'),
    ('a0000000-0000-4000-8000-000000000005', 'tarun.nl@gmail.com',    'Tarun',   'user',  'a0000000-0000-4000-8000-000000000002', now() - interval '6 months');

-- ============================================================
-- Allowances — Q-01 PLACEHOLDERS.
--
-- Three non-reconciling figures were given on the call and none has been
-- confirmed. These numbers are invented so the product is usable in
-- development. They MUST be replaced by Vinita before the first live month.
--
-- Seeded as organisation defaults (user_id NULL) for the three months around
-- today, so carry-forward has some history to actually carry.
-- ============================================================
INSERT INTO allowances (period, category, days, user_id)
SELECT to_char(m, 'YYYY-MM'), c.category, c.days, NULL
FROM generate_series(
        date_trunc('month', now() AT TIME ZONE 'Asia/Kolkata') - interval '2 months',
        date_trunc('month', now() AT TIME ZONE 'Asia/Kolkata') + interval '1 month',
        interval '1 month') m
CROSS JOIN (VALUES ('wfh', 4.0), ('casual', 1.5), ('sick', 1.0)) AS c(category, days)
ON CONFLICT DO NOTHING;

-- ============================================================
-- Holidays — FR-HOL. Real Indian public holidays for 2026.
-- ============================================================
INSERT INTO holidays (date, name, created_by) VALUES
    ('2026-01-26', 'Republic Day',      'a0000000-0000-4000-8000-000000000001'),
    ('2026-08-15', 'Independence Day',  'a0000000-0000-4000-8000-000000000001'),
    ('2026-10-02', 'Gandhi Jayanti',    'a0000000-0000-4000-8000-000000000001'),
    ('2026-11-08', 'Diwali',            'a0000000-0000-4000-8000-000000000001')
ON CONFLICT (date, location_id) DO NOTHING;

-- ============================================================
-- A few bookings so the calendar and team view are not empty on first run.
-- Dates are relative to today so the fixture never goes stale.
-- ============================================================
INSERT INTO bookings (user_id, date, category, duration, reason, status, created_by, decided_by, decided_at)
VALUES
    -- Deepika's half-day, still awaiting Devansh (scenario §4.1)
    ('a0000000-0000-4000-8000-000000000004',
     (now() AT TIME ZONE 'Asia/Kolkata')::date + 3,
     'casual', 0.5, 'Dentist appointment', 'pending',
     'a0000000-0000-4000-8000-000000000004', NULL, NULL),
    -- Sriram working from home tomorrow, already agreed
    ('a0000000-0000-4000-8000-000000000003',
     (now() AT TIME ZONE 'Asia/Kolkata')::date + 1,
     'wfh', 1.0, NULL, 'approved',
     'a0000000-0000-4000-8000-000000000003',
     'a0000000-0000-4000-8000-000000000002', now()),
    -- Tarun's sick day last week, now locked (§6.3)
    ('a0000000-0000-4000-8000-000000000005',
     (now() AT TIME ZONE 'Asia/Kolkata')::date - 7,
     'sick', 1.0, 'Fever', 'approved',
     'a0000000-0000-4000-8000-000000000005',
     'a0000000-0000-4000-8000-000000000002', now() - interval '7 days');

-- ============================================================
-- Spec 007 — the sales CRM (quickstart §1). Local only: `db push` never runs
-- this file.
--
-- Two CRM-only sellers, made as a workspace admin's invitation would make
-- them (crm_invite, data-model §5.13): an auth user with an identity, and a
-- profile with no TVA role, no lead and no time logging. Tamil sells for
-- DeepWeaver from Sydney.
-- ============================================================
INSERT INTO auth.users
    (id, instance_id, aud, role, email, encrypted_password,
     email_confirmed_at, created_at, updated_at,
     raw_app_meta_data, raw_user_meta_data,
     confirmation_token, recovery_token, email_change, email_change_token_new)
VALUES
    ('a0000000-0000-4000-8000-000000000006', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'tamil.crm@deepweaver.example',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '1 month', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Tamil"}', '', '', '', ''),
    ('a0000000-0000-4000-8000-000000000007', '00000000-0000-0000-0000-000000000000',
     'authenticated', 'authenticated', 'ganesh.crm@academy.example',
     crypt('portal123', gen_salt('bf')), now(), now() - interval '1 month', now(),
     '{"provider":"email","providers":["email"]}', '{"display_name":"Ganesh"}', '', '', '', '');

INSERT INTO auth.identities
    (id, provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at)
SELECT gen_random_uuid(), u.id::text, u.id,
       jsonb_build_object('sub', u.id::text, 'email', u.email, 'email_verified', true),
       'email', now(), now(), now()
FROM auth.users u
WHERE u.email IN ('tamil.crm@deepweaver.example', 'ganesh.crm@academy.example');

INSERT INTO profiles (id, email, display_name, role, account_type, logs_time, timezone, created_at) VALUES
    ('a0000000-0000-4000-8000-000000000006', 'tamil.crm@deepweaver.example', 'Tamil',  'user', 'crm_only', false, 'Australia/Sydney', now() - interval '1 month'),
    ('a0000000-0000-4000-8000-000000000007', 'ganesh.crm@academy.example',   'Ganesh', 'user', 'crm_only', false, 'Asia/Kolkata',     now() - interval '1 month');

-- The owner. Migration 022 names the real one by email, which no seeded
-- person has, so a fresh local database would have nobody; Sriram stands in,
-- so the owner's CRM rules (FR-CRM-CHG-15, the protected membership) can be
-- seen locally. He also gets the owner's dashboard, as 022 gives it.
UPDATE profiles SET is_owner = true, dashboard_access = true
WHERE id = 'a0000000-0000-4000-8000-000000000003';

-- Memberships. Migration 026 seeds the owner's two admin memberships, but
-- migrations run before this file, when no profile exists yet (data-model
-- §7.4), so they are added here. The CRM's audit trigger refuses a write
-- with no actor, so this runs as the system actor, as 026's seeds do.
DO $$
BEGIN
    PERFORM set_config('crm.actor',
        '{"kind":"system","user_id":null,"channel":null,"workspace_id":null}', true);

    -- FR-CRM-CHG-15: the owner administers both workspaces.
    INSERT INTO crm_memberships (workspace_id, user_id, role)
    SELECT w.id, p.id, 'admin'
    FROM crm_workspaces w CROSS JOIN profiles p
    WHERE p.is_owner IS TRUE
    ON CONFLICT (workspace_id, user_id) DO NOTHING;

    -- Quickstart §1: Devansh views Labs, Deepika and Tamil sell in Labs,
    -- Ganesh sells in Academy. Vinita, the TVA admin, is in neither.
    INSERT INTO crm_memberships (workspace_id, user_id, role)
    SELECT w.id, m.user_id::uuid, m.role
    FROM (VALUES
        ('labs',    'a0000000-0000-4000-8000-000000000002', 'viewer'),
        ('labs',    'a0000000-0000-4000-8000-000000000004', 'member'),
        ('labs',    'a0000000-0000-4000-8000-000000000006', 'member'),
        ('academy', 'a0000000-0000-4000-8000-000000000007', 'member')
    ) AS m(workspace_key, user_id, role)
    JOIN crm_workspaces w ON w.key = m.workspace_key
    ON CONFLICT (workspace_id, user_id) DO NOTHING;
END;
$$;
