-- 017_contracted_hours.sql
--
-- Spec 005 FR-CTC-06 — the hours a CTC period pays for. Until now everyone
-- was costed and planned at 8 h a day, so a contractor on 10 h a week was
-- costed at about a quarter of what an hour of theirs really costs.
--
-- Per period, not per person: going part-time is a change of contract, and
-- past months must keep the hours that were in force then.
--
-- Every existing row was full time, which is what the default says. No grant
-- to `authenticated`: cost_periods is backend-only (011).

ALTER TABLE cost_periods
    ADD COLUMN IF NOT EXISTS hours_per_week numeric(4,1) NOT NULL DEFAULT 40
    CHECK (hours_per_week > 0 AND hours_per_week <= 60);
