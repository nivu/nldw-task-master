-- 018_project_lead.sql
--
-- Spec 002 FR-PROJ-07 — a project may name the person who leads it (e.g.
-- Devansh leads Hearsight). Optional; set by whoever may edit projects. It is
-- a label, not a permission: who may change a project or allocate to it is
-- unchanged (spec 003 FR-ROLE-02/07/08).
--
-- ON DELETE SET NULL: profiles are deactivated, not deleted, but if one ever
-- is, the project simply has no lead rather than blocking the delete.
--
-- No column grant to `authenticated`: the browser never reads projects
-- directly (all reads go through the backend proxy).

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS lead_id uuid NULL REFERENCES profiles(id) ON DELETE SET NULL;
