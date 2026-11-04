-- 076_additional_leave.sql
--
-- 001 FR-BOOK-13: Additional leave, a fifth booking category with no
-- allowance. It is for leave taken once the person's own has run out (sick
-- leave for today, casual leave for a future day); the application enforces
-- that. A reason is required, which bookings_reason_required (012) already
-- does for every category but wfh and compoff.

ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_category_check;
ALTER TABLE bookings ADD CONSTRAINT bookings_category_check
    CHECK (category IN ('wfh', 'casual', 'sick', 'compoff', 'additional'));
