-- 020_project_pipeline.sql
--
-- Spec 002 FR-PROJ-07, spec 005 FR-PNL-05 — work that is planned but not yet
-- won. A `tentative` project can have phases, revenue and allocations, takes
-- no time entries, and stays out of the confirmed monthly profit; it is
-- reported separately as pipeline. Existing projects are confirmed, which is
-- what every existing row meant.
--
-- `probability` is the chance (0–100) the work is won, used to weight the
-- pipeline's revenue. Null = not estimated. It is not money, so leads may set
-- it.
--
-- No column grant to `authenticated`: the browser never reads projects
-- directly (all reads go through the backend proxy).

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'tentative'));

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS probability smallint
        CHECK (probability BETWEEN 0 AND 100);
