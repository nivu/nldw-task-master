-- 028_allocation_release.sql
--
-- Spec 007 (sales CRM) FR-CRM-CHG-03, FR-CRM-TVA-12, -14; owner decision D5.
--
-- An allocation can be marked released — when, by whom and why — and
-- un-released, without being deleted. A Closed Lost deal (S5) releases the
-- allocations of its tentative project; a reopen un-releases exactly those
-- rows, so nothing pencilled in is lost by a deal that comes back.
--
-- 1. Four columns, all NULL on every existing row (nothing is released):
--    - `released_at`, `release_reason`: set together or not at all.
--    - `released_by`: the person whose action released it (a CRM deal move
--      made by a CRM-only seller still names them; D8).
--    - `released_by_opportunity_id`: the deal that released it. Its foreign
--      key to `crm_opportunities` arrives in 032, with that table's sibling
--      links; the table does not exist yet.
--    `allocations_release_shape` keeps the columns consistent: a reason
--    exactly when released, and no deal on a row that is not released.
--
-- 2. Releasing never deletes, so the existing CHECKs (`percent > 0`,
--    `ends_on >= starts_on`) still hold. Every TVA allocation reader counts
--    only rows with `released_at IS NULL` on projects that are not archived
--    (`services/supabase.list_counting_allocations`, FR-CRM-CHG-03, -04);
--    the partial index below serves those reads. The Admin allocations list
--    still shows every row, released ones marked, and refuses to edit or
--    remove a released row (api/admin.py, D5).
--
-- 3. No grant: 025 revoked every browser privilege on `allocations`, and the
--    backend (service role) is its only reader.
--
-- Reversible: drop the constraint, the index and the four columns.

ALTER TABLE allocations
    ADD COLUMN IF NOT EXISTS released_at timestamptz,
    ADD COLUMN IF NOT EXISTS released_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS release_reason text,
    ADD COLUMN IF NOT EXISTS released_by_opportunity_id uuid;

ALTER TABLE allocations DROP CONSTRAINT IF EXISTS allocations_release_shape;
ALTER TABLE allocations ADD CONSTRAINT allocations_release_shape
    CHECK ((released_at IS NULL) = (release_reason IS NULL)
           AND (released_at IS NOT NULL OR released_by_opportunity_id IS NULL));

CREATE INDEX IF NOT EXISTS allocations_counting
    ON allocations (user_id, starts_on, ends_on) WHERE released_at IS NULL;
