-- 001_initial_schema.sql
--
-- Nunnari Employee Portal — core schema for the V1 leave calendar.
--
-- Shape note: Nunnari is one company, so there is no tenant dimension. The
-- starter template's `orgs` / `team_members` model was removed rather than
-- extended (spec A-11) — carrying an `org_id` that is always the same value on
-- every table and in every policy buys nothing V1 needs, and the constitution's
-- YAGNI principle forbids designing for a hypothetical second tenant.
--
-- Authorisation instead hangs off two things on `profiles`: a `role`
-- (user/lead/admin) and a `lead_id` reporting line. See 003_rls_helpers.sql.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- profiles — one row per auth.users row
--
-- FR-AUTH-02 forbids self-registration, so rows here are created by an admin
-- through the backend (which also creates the auth.users row). There is
-- deliberately NO trigger auto-creating a profile from auth.users: an account
-- with no role and no lead is not a usable account, and a trigger would let
-- one exist.
-- ============================================================
CREATE TABLE IF NOT EXISTS profiles (
    id            uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email         text NOT NULL UNIQUE,
    display_name  text NOT NULL,
    role          text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'lead', 'admin')),
    -- Who approves this person's leave. NULL means "falls to an admin"
    -- (spec Q-05) — which is the case for leads and for the admin themselves.
    lead_id       uuid REFERENCES profiles(id) ON DELETE SET NULL,
    -- FR-AUTH-06: deactivation preserves history. Never delete a profile.
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profiles_lead_not_self CHECK (lead_id IS NULL OR lead_id <> id)
);

CREATE INDEX IF NOT EXISTS profiles_lead_idx   ON profiles (lead_id);
CREATE INDEX IF NOT EXISTS profiles_active_idx ON profiles (is_active);

-- ============================================================
-- holidays — FR-HOL
--
-- Company-wide by definition (FR-HOL-02): there is no per-person holiday, so
-- there is no user_id here. A holiday consumes no allowance (FR-HOL-03), which
-- is expressed by the balance ledger simply never counting one.
-- ============================================================
CREATE TABLE IF NOT EXISTS holidays (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    date        date NOT NULL UNIQUE,
    name        text NOT NULL CHECK (btrim(name) <> ''),
    created_by  uuid REFERENCES profiles(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- bookings — FR-BOOK, §6.4
--
-- `date` is a calendar date, NOT a timestamp. NFR-03: storing it as a UTC
-- timestamp shifts the day across midnight for Asia/Kolkata (+05:30) and the
-- whole product is a calendar, so that error would be silent and constant.
--
-- `duration` is numeric(2,1) because §6.2 requires half-day granularity.
-- Integer-day arithmetic is wrong here.
-- ============================================================
CREATE TABLE IF NOT EXISTS bookings (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    date           date NOT NULL,
    category       text CHECK (category IN ('wfh', 'casual', 'sick')),
    duration       numeric(2,1) NOT NULL CHECK (duration IN (0.5, 1.0)),
    reason         text,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected',
                                     'withdrawn', 'released', 'unrecognised')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    decided_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    decided_at     timestamptz,
    decision_note  text,
    updated_at     timestamptz NOT NULL DEFAULT now(),

    -- An `unrecognised` row records "this person was absent and never booked"
    -- (FR-LEAD-03). The lead flagging it genuinely may not know which category
    -- it was, so category is nullable for that status only (spec A-18).
    CONSTRAINT bookings_category_required
        CHECK (status = 'unrecognised' OR category IS NOT NULL),

    -- FR-BOOK-03 / Q-07: a reason is required for casual and sick, optional
    -- for work from home.
    CONSTRAINT bookings_reason_required
        CHECK (
            status = 'unrecognised'
            OR category = 'wfh'
            OR (reason IS NOT NULL AND btrim(reason) <> '')
        ),

    -- FR-APPR-03: a rejection carries a note from the lead.
    CONSTRAINT bookings_rejection_note
        CHECK (status <> 'rejected' OR (decision_note IS NOT NULL AND btrim(decision_note) <> ''))
);

-- FR-BOOK-06 — a day holds at most one booking, enforced in the database
-- rather than only in application code.
--
-- The set here is "occupies the day", which is deliberately wider than
-- "consumes allowance" (§6.4: only pending and approved consume). An
-- `unrecognised` day costs nothing but still must not sit alongside a real
-- booking for the same date. Withdrawn/rejected/released rows are history and
-- are excluded so the day can be re-booked.
CREATE UNIQUE INDEX IF NOT EXISTS bookings_one_per_day
    ON bookings (user_id, date)
    WHERE status IN ('pending', 'approved', 'unrecognised');

CREATE INDEX IF NOT EXISTS bookings_user_date_idx ON bookings (user_id, date);
CREATE INDEX IF NOT EXISTS bookings_date_idx      ON bookings (date);
CREATE INDEX IF NOT EXISTS bookings_status_idx    ON bookings (status);

-- ============================================================
-- allowances — FR-BAL
--
-- What was GRANTED. Never what remains: FR-BAL / §7.3 requires balances to be
-- derived from this plus the booking ledger. A stored "remaining" counter
-- drifts silently and cannot be audited; a ledger can be recomputed.
--
-- user_id NULL means "the organisation default for this period".
-- ============================================================
CREATE TABLE IF NOT EXISTS allowances (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    period      text NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    category    text NOT NULL CHECK (category IN ('wfh', 'casual', 'sick')),
    days        numeric(3,1) NOT NULL CHECK (days >= 0),
    user_id     uuid REFERENCES profiles(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES profiles(id) ON DELETE SET NULL
);

-- NULLS NOT DISTINCT (PostgreSQL 15+) so the organisation-default row — the
-- one with user_id IS NULL — is also unique per (period, category). Without
-- it, two conflicting defaults for the same month could both exist.
CREATE UNIQUE INDEX IF NOT EXISTS allowances_scope
    ON allowances (period, category, user_id) NULLS NOT DISTINCT;

CREATE INDEX IF NOT EXISTS allowances_lookup_idx ON allowances (category, period);

-- ============================================================
-- app_settings — policy switches
--
-- The spec's open questions (§11) are answered by rows here rather than by
-- constants in code, so answering one is a settings change and not a
-- migration. Defaults are inserted in 006_settings_defaults.sql.
-- ============================================================
CREATE TABLE IF NOT EXISTS app_settings (
    key          text PRIMARY KEY,
    value        jsonb NOT NULL,
    description  text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL
);

-- ============================================================
-- audit_log — FR-APPR-07, FR-ADMIN-06, NFR-06
--
-- Append-only. That is enforced in 005_audit_triggers.sql at the database
-- level, not by convention — NFR-06 says it MUST NOT be editable from the
-- application, and "we agreed not to" is not an enforcement mechanism.
--
-- actor_id is NULL for system actions (the nightly auto-approve sweep);
-- actor_label says which.
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id      uuid REFERENCES profiles(id) ON DELETE SET NULL,
    actor_label   text NOT NULL DEFAULT 'user' CHECK (actor_label IN ('user', 'system')),
    action        text NOT NULL,
    target_table  text NOT NULL,
    target_id     text,
    before        jsonb,
    after         jsonb,
    at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_target_idx ON audit_log (target_table, target_id);
CREATE INDEX IF NOT EXISTS audit_log_at_idx     ON audit_log (at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx  ON audit_log (actor_id);

-- ============================================================
-- updated_at maintenance
-- ============================================================
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER profiles_touch   BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
CREATE TRIGGER holidays_touch   BEFORE UPDATE ON holidays
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
CREATE TRIGGER bookings_touch   BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
CREATE TRIGGER allowances_touch BEFORE UPDATE ON allowances
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
