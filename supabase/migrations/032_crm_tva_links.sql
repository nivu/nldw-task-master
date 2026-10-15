-- 032_crm_tva_links.sql
--
-- Spec 007 (sales CRM) FR-CRM-TVA-01, -12, -14, FR-CRM-CHG-10; owner
-- decisions D5, D14, D15; data-model §4.3, §4.4.
--
-- A TVA project can name the CRM company it is for and the one Labs deal that
-- created (or was linked to) it. The deal stores no copy: its project is
-- `projects WHERE source_opportunity_id = deal.id` (FR-CRM-TVA-01).
--
-- 1. `projects` gains three columns, NULL on every existing project (existing
--    projects stay unlinked, FR-CRM-CHG-10):
--    - `crm_company_id`: the company. Purging the company clears it (033's
--      `crm_purge` does it first, with a TVA audit row; the foreign key's SET
--      NULL is the backstop). The project's free-text `client` stays.
--    - `source_opportunity_id`: the deal. Unique, so one deal has at most one
--      project and a project at most one deal. A deal with a project cannot
--      be deleted (FR-CRM-DEL-03), so SET NULL is only a backstop too.
--    - `crm_archived_at`: set when a Closed Lost archived the project, so a
--      reopen unarchives only what the loss archived (FR-CRM-TVA-14).
--    `projects_name_unique` (008) is unchanged: the CRM refuses a clash before
--    it would be raised (FR-CRM-TVA-05).
--
-- 2. `allocations.released_by_opportunity_id` (028) gets its foreign key now
--    that `crm_opportunities` exists (030).
--
-- 3. No grant: 025 revoked every browser privilege on `projects` and
--    `allocations`, and the backend (service role) is their only reader.
--
-- Reversible: drop the two indexes, the three columns and the foreign key.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS crm_company_id uuid REFERENCES crm_companies(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_opportunity_id uuid
        REFERENCES crm_opportunities(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS crm_archived_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS projects_source_opportunity_unique
    ON projects (source_opportunity_id) WHERE source_opportunity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS projects_crm_company
    ON projects (crm_company_id) WHERE crm_company_id IS NOT NULL;

ALTER TABLE allocations DROP CONSTRAINT IF EXISTS allocations_released_by_opportunity_fk;
ALTER TABLE allocations
    ADD CONSTRAINT allocations_released_by_opportunity_fk
    FOREIGN KEY (released_by_opportunity_id) REFERENCES crm_opportunities(id) ON DELETE SET NULL;

-- Un-releasing by deal reads this column (033 `unarchive_unrelease`).
CREATE INDEX IF NOT EXISTS allocations_released_by_opportunity
    ON allocations (released_by_opportunity_id) WHERE released_by_opportunity_id IS NOT NULL;
