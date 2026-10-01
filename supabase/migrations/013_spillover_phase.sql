-- 013_spillover_phase.sql
--
-- Spec 002 §3.2, spec 005 §3.2 — a fourth phase, `spillover`: delivery work
-- that overran the agreed timeline. Revenue is not spread into it.

ALTER TABLE project_phases DROP CONSTRAINT IF EXISTS project_phases_phase_check;
ALTER TABLE project_phases
    ADD CONSTRAINT project_phases_phase_check
    CHECK (phase IN ('pre', 'delivery', 'support', 'spillover'));
