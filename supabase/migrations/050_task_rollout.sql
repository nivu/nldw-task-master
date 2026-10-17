-- 050_task_rollout.sql
--
-- Spec 008 (tasks) FR-TASK-ROLL-01, -02, -05; C6; TP-11; data-model §2.4.
--
-- Each phase of tasks is switched on per person (C6) as a cumulative level
-- 0-4: level n switches on phases 1 to n (TP-11).
--
-- 1. `profiles.tasks_level`: a person's own level, or null to follow the
--    global default. Changed only by a TVA admin through
--    `PATCH /admin/users/{id}`, audited `user.tasks_level` (FR-TASK-ROLL-02).
-- 2. `app_settings.tasks_default_level`: the level of everyone without their
--    own, starting at 0, so every task route answers 403 after a fresh
--    database until an admin raises someone's level (FR-TASK-ROLL-01). Set
--    through `update_setting`. CRM task behaviour never reads either
--    (FR-TASK-ROLL-05).
--
-- No grant to `authenticated`: the backend reads both.
--
-- Reversible: DELETE FROM app_settings WHERE key = 'tasks_default_level';
-- ALTER TABLE profiles DROP COLUMN tasks_level.

ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS tasks_level smallint
    CHECK (tasks_level BETWEEN 0 AND 4);

INSERT INTO app_settings (key, value, description) VALUES
    (
        'tasks_default_level',
        '0',
        'Phase of 008 tasks switched on for everyone without their own level '
        '(0 = off, 1–4 cumulative).'
    )
ON CONFLICT (key) DO NOTHING;
