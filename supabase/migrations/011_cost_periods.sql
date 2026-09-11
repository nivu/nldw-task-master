-- 011_cost_periods.sql
--
-- Spec 005 — CTC (cost to company) as a dated history per person. Past,
-- current and upcoming figures coexist; every calculation uses the one in
-- force on the day. Replaces 003's single hourly cost rate (retired, not
-- dropped — see spec 005 §3.1).

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS cost_periods (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    -- Entered annually (spec 005 Q-03); the backend shows it monthly.
    annual_ctc  numeric(14,2) NOT NULL CHECK (annual_ctc >= 0),
    starts_on   date NOT NULL,
    -- NULL = until further notice. FR-CTC-03: adding a later period closes it.
    ends_on     date,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT cost_periods_dates CHECK (ends_on IS NULL OR ends_on >= starts_on),
    -- FR-CTC-02: one figure per person per day. Enforced by the database,
    -- because "the API checks" is one forgotten branch away from two salaries
    -- on the same day and a doubled COGS nobody notices.
    CONSTRAINT cost_periods_no_overlap EXCLUDE USING gist (
        user_id WITH =,
        daterange(starts_on, COALESCE(ends_on, 'infinity'::date), '[]') WITH &&
    )
);

CREATE INDEX IF NOT EXISTS cost_periods_user ON cost_periods (user_id, starts_on);

-- FR-CTC-05: the most sensitive number in the system. Backend only.
ALTER TABLE cost_periods ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON cost_periods FROM authenticated, anon;
