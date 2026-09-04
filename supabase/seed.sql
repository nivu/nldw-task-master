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
-- like a lead's, falls to an admin.
-- ============================================================
INSERT INTO profiles (id, email, display_name, role, lead_id, created_at) VALUES
    ('a0000000-0000-4000-8000-000000000001', 'vinita@nunnari.example', 'Vinita',  'admin', NULL, now() - interval '6 months'),
    ('a0000000-0000-4000-8000-000000000002', 'devansh.nl@gmail.com',  'Devansh', 'lead',  NULL, now() - interval '6 months'),
    ('a0000000-0000-4000-8000-000000000003', 'sriram.nl@gmail.com',   'Sriram',  'user',  'a0000000-0000-4000-8000-000000000002', now() - interval '6 months'),
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
ON CONFLICT (date) DO NOTHING;

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
