-- 007_booking_backfill.sql
--
-- Spec A-21 — the admin backfill.
--
-- WHY THIS EXISTS, AND WHY IT IS UNCOMFORTABLE.
--
-- §6.3 is the integrity rule the whole product rests on: a booking is locked
-- once its own date has passed, so nobody can take a work-from-home day and
-- then delete the record to reclaim the allowance. This migration adds the one
-- sanctioned way past that rule.
--
-- It exists because go-live happens partway through a month, and the leave
-- people have already taken that month has to be recorded somehow. The
-- alternative — quietly reducing everyone's allowance to net it off — loses
-- which days were taken, and a leave system that cannot say which days were
-- taken is the tracking-that-stops-describing-reality problem from §1.
--
-- The hole is kept as small as it can be:
--
--   * Only an admin can do it, and only for a date STRICTLY IN THE PAST.
--     Today and the future are ordinary bookings the person makes themselves.
--   * Every backfilled row is MARKED as one. This is not a hidden capability
--     that produces rows indistinguishable from real ones — `backfilled_by`
--     is visible in the API, on the calendar, and in the team view.
--   * A note is REQUIRED, so the record says why it was entered by hand.
--   * The undo path (see app/services/bookings.py) can only touch rows where
--     `backfilled_by IS NOT NULL`. An admin can correct their own typo; they
--     can never reach a booking somebody genuinely made themselves. That
--     boundary is what keeps §6.3 true for real records.
--
-- Every backfill and every undo is written to the audit log, which is
-- append-only (005_audit_triggers.sql) — so the exception to the integrity
-- rule is itself permanently recorded.

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS backfilled_by  uuid REFERENCES profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS backfilled_at  timestamptz,
    ADD COLUMN IF NOT EXISTS backfill_note  text;

-- A backfilled row must say who and why. Half a provenance record is worse
-- than none, because it looks like a complete one.
ALTER TABLE bookings
    DROP CONSTRAINT IF EXISTS bookings_backfill_complete;
ALTER TABLE bookings
    ADD CONSTRAINT bookings_backfill_complete CHECK (
        backfilled_by IS NULL
        OR (backfilled_at IS NOT NULL AND backfill_note IS NOT NULL AND btrim(backfill_note) <> '')
    );

-- Finding what an admin entered by hand is a question that gets asked during a
-- go-live and then rarely again, but it is asked across the whole table.
CREATE INDEX IF NOT EXISTS bookings_backfilled_idx
    ON bookings (backfilled_by)
    WHERE backfilled_by IS NOT NULL;
