-- 056_task_link_removal_lock.sql
--
-- Spec 008 (tasks) FR-TASK-DATA-05; data-model §5.1; a fix to 047.
--
-- `tasks_workspace_check` judges a task at commit, under READ COMMITTED.
-- Two transactions that each remove a different CRM link of the same task
-- never blocked each other: each counted the other's link as still there,
-- both committed, and the task was left with a workspace and no CRM link.
-- A link removal now locks its task row (FOR UPDATE) before the links are
-- counted, so the second waits for the first to commit and then counts what
-- it left. Only removals lock: an insert or update can only add links.
--
-- The rest of the function is 047's, unchanged.
--
-- Reversible: re-run 047's `CREATE OR REPLACE FUNCTION tasks_workspace_check`.

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
        PERFORM 1 FROM tasks WHERE id = tid FOR UPDATE;
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
