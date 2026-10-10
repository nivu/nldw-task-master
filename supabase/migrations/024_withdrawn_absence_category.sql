-- 024_withdrawn_absence_category.sql
--
-- FR-BACK-11: an admin removing a day wrongly marked absent keeps the row for
-- history and sets it to `withdrawn` rather than deleting it. A mark-absent
-- row is `unrecognised` with no category (spec A-18), so the withdrawn row
-- still has no category — and the original bookings_category_required
-- (001: CHECK (status = 'unrecognised' OR category IS NOT NULL)) rejected
-- that UPDATE, making "remove absence" fail with a server error.
--
-- A withdrawn row consumes nothing and is never re-opened (only
-- flag_unrecognised creates an uncategorised row), so letting it keep a NULL
-- category loses nothing. Every other status still requires a category.

ALTER TABLE bookings DROP CONSTRAINT bookings_category_required;

ALTER TABLE bookings
    ADD CONSTRAINT bookings_category_required
        CHECK (status IN ('unrecognised', 'withdrawn') OR category IS NOT NULL);
