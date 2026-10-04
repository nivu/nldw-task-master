-- 019_people_dates.sql
--
-- Spec 002 FR-ANALYTICS-08 — the day a person joined and the day they left.
--
-- Nobody is expected to log time before `joined_on` or after `left_on`: those
-- days are never missing in coverage, the missing-days lists, the nudges or
-- weekly sign-off. NULL = not recorded, so that end is open, which is what
-- every existing row meant. Money is not touched: cost still comes from the
-- CTC periods (spec 005), which carry their own dates.
--
-- No column grant to `authenticated`: the browser never reads profiles
-- directly (all reads go through the backend proxy), as with 015.

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS joined_on date;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS left_on date;

ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_left_after_joined;
ALTER TABLE profiles ADD CONSTRAINT profiles_left_after_joined
    CHECK (left_on IS NULL OR joined_on IS NULL OR left_on >= joined_on);
