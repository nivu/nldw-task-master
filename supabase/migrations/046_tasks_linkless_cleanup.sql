-- 046_tasks_linkless_cleanup.sql
--
-- Spec 008 (tasks) §8.2 "Before the rename", FR-TASK-DATA-05, NFR-TASK-03;
-- data-model §1.
--
-- 047 adds a deferred check that a live task has a workspace exactly when it
-- has a CRM link (FR-TASK-DATA-05). Before 008, `crm_purge` removed the links
-- that pointed at a purged record but left their tasks, so a live workspace
-- task with no link can exist. Each one is moved to its workspace Trash
-- first: a soft delete (`deleted_at = now()`, one `deleted_group` per task),
-- audited under the system actor with the action `task.deleted`, so the
-- check never meets one and nothing is lost (an admin can still see it in the
-- Trash). The count is raised as a NOTICE, to be reported before the
-- production apply (deployment.md).
--
-- Guarded by `to_regclass`, so a re-run after 047's rename does nothing.
--
-- Reversible: for the audit rows this migration wrote (action `task.deleted`,
-- actor kind `system`, at the migration's time), set the tasks' `deleted_at`,
-- `deleted_by` and `deleted_group` back to null under the system actor. They
-- are linkless again, so do it only after 047's reverse.

DO $$
DECLARE
    moved integer := 0;
BEGIN
    IF to_regclass('public.crm_tasks') IS NULL THEN
        RAISE NOTICE '046: crm_tasks is already renamed; nothing to clean up.';
        RETURN;
    END IF;

    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );
    PERFORM set_config('crm.action', 'task.deleted', true);

    UPDATE crm_tasks t
    SET deleted_at = now(), deleted_by = NULL, deleted_group = gen_random_uuid()
    WHERE t.deleted_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM crm_task_links l WHERE l.task_id = t.id);
    GET DIAGNOSTICS moved = ROW_COUNT;

    PERFORM set_config('crm.action', '', true);
    RAISE NOTICE '046: % live task(s) with no linked record moved to their workspace Trash.',
        moved;
END;
$$;
