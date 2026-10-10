-- 023_sick_allowance_default.sql
--
-- Q-01: the company sick-leave default.
--
-- Production had no sick allowance row at all, so resolve_allowance (spec
-- A-12) fell through to zero and every same-day sick booking (§4.2) was
-- refused with "0 days remaining". 1.0 day a month (12 a year, carried
-- forward under FR-BAL-05) is the figure spec Q-01 and seed.sql already use.
--
-- It starts in 2026-10 rather than earlier so nobody is credited a carried-
-- forward day for a month in which sick leave was never offered. As a company
-- default it continues into later months until an admin changes it.
--
-- Like 006, this is production data, not a development fixture. Changing the
-- figure later is done in Admin > Allowances (or the set_allowance tool), not
-- with a new migration. ON CONFLICT DO NOTHING (the unique index on
-- period, category, user_id treats NULLs as equal) means an admin-set row for
-- the same month is left alone and re-running this is harmless.

INSERT INTO allowances (period, category, days, user_id)
VALUES ('2026-10', 'sick', 1.0, NULL)
ON CONFLICT DO NOTHING;
