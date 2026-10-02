-- 014_project_category.sql
--
-- Spec 002 FR-PROJ-06, spec 005 FR-PNL-04 — every project has a category, so
-- time and money can be reported by kind of work. Existing projects are paid
-- client engagements.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'client'
        CHECK (category IN ('client', 'poc', 'product', 'internal'));
