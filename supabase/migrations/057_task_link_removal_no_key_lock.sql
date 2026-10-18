-- 057_task_link_removal_no_key_lock.sql
--
-- Spec 008 (tasks) FR-TASK-DATA-05; data-model §5.1; a fix to 056 (a spare
-- number, data-model §1).
--
-- 056 made a link removal lock its task row FOR UPDATE at commit, so two
-- removals of the same task's links are judged one after the other. But a
-- transaction that also inserted a link holds FOR KEY SHARE on that task
-- row (the foreign key check of `task_links.task_id`), and FOR UPDATE
-- conflicts with FOR KEY SHARE. Two transactions that each insert one link
-- and remove another of the same task (a company merge moving a link, and
-- a person editing the task's links) each waited at commit for the other's
-- KEY SHARE: a deadlock (40P01), where both used to commit.
--
-- The removal now takes FOR NO KEY UPDATE, which still serialises removals
-- against each other and against updates of the task, but does not
-- conflict with the KEY SHARE locks of link inserts.
--
-- The rest of the function is 056's, unchanged; only the line marked "057"
-- differs.
--
-- Reversible: re-run 056's tasks_workspace_check.

CREATE OR REPLACE FUNCTION public.tasks_workspace_check()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    tid         uuid;
    t_ws        uuid;
    t_deleted   timestamptz;
    crm_ws      uuid[];
    has_project boolean;
BEGIN
    IF TG_TABLE_NAME = 'tasks' THEN
        tid := NEW.id;
    ELSIF TG_OP = 'DELETE' THEN
        tid := OLD.task_id;
    ELSE
        tid := NEW.task_id;
    END IF;

    IF TG_TABLE_NAME = 'task_links' AND TG_OP = 'DELETE' THEN
        PERFORM 1 FROM tasks WHERE id = tid FOR NO KEY UPDATE;  -- 057
    END IF;

    SELECT workspace_id, deleted_at INTO t_ws, t_deleted FROM tasks WHERE id = tid;
    -- Gone (purged in this transaction) or in the Trash: nothing to check.
    IF NOT FOUND OR t_deleted IS NOT NULL THEN
        RETURN NULL;
    END IF;

    SELECT coalesce(array_agg(DISTINCT l.workspace_id)
                        FILTER (WHERE l.record_type <> 'tva_project'), '{}'),
           coalesce(bool_or(l.record_type = 'tva_project'), false)
    INTO crm_ws, has_project
    FROM task_links l
    WHERE l.task_id = tid;

    IF cardinality(crm_ws) = 0 AND t_ws IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A CRM task needs at least one linked record; delete it instead.';
    END IF;
    IF cardinality(crm_ws) > 0 AND crm_ws IS DISTINCT FROM ARRAY[t_ws] THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A task cannot link records of two workspaces.';
    END IF;
    IF has_project AND t_ws IS NOT NULL
       AND NOT (SELECT tva_linked FROM crm_workspaces WHERE id = t_ws) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Only Nunnari Labs tasks can link a TVA project.';
    END IF;
    RETURN NULL;
END;
$$;
