-- 047_tasks_shared_table.sql
--
-- Spec 008 (tasks) FR-TASK-DATA-01 to -15, FR-TASK-CHG-01, -11, §8.2;
-- T1, T2, T3; TQ-01, TQ-08, TQ-09, TQ-17; TP-04, TP-13, TP-14;
-- NFR-TASK-03; data-model §2.1, §2.2, §3, §4, §5.1.
--
-- The CRM's task table becomes the portal's one task table (T1), and its
-- link table the shared task link table (T3). One transaction:
--
-- 1. Renames. `crm_tasks` -> `tasks`, `crm_task_links` -> `task_links`, with
--    every constraint, index and trigger whose name starts with the old
--    table name (so `crm_tasks_crm_audit` becomes `tasks_crm_audit`, which
--    the writers require, and `crm_task_links_target` becomes
--    `task_links_target`). Every row and id is kept. Guarded by `to_regclass`
--    so a re-run does nothing.
-- 2. `task_tags_ok()`: at most 10 tags, each 1-40 characters after trim
--    (TP-14). Nothing stricter: case and spelling are the person's.
-- 3. New columns on `tasks` (data-model §2.1): priority, tags, parent,
--    waiting on (a person and/or the label shown), source and source
--    reference. `workspace_id` becomes NULL-able on both tables: a task with
--    no CRM link has no workspace. Category stays CRM-only.
-- 4. Statuses (T2, TP-04): the CHECK becomes open|waiting|done|cancelled;
--    To do and In progress become Open, and a former In progress task gains
--    the tag "in progress". Then every open or waiting row with no bucket,
--    live or deleted, gets Today when due today or earlier (Asia/Kolkata),
--    else Later (FR-TASK-DATA-08), before `tasks_bucket_when_open` is added.
--    Rows with a Twenty id get source Import, the rest Manual. Done rows,
--    including those a Lost move closed before 008, stay Done
--    (FR-TASK-CHG-04). Every change is written under the system actor, so it
--    is audited.
-- 5. Links (FR-TASK-DATA-06, -15; TP-13): a `tva_project` record type with a
--    real foreign key to `projects` and no workspace; a plain
--    `task_id -> tasks(id) ON DELETE CASCADE` on every row (the composite
--    workspace key is kept for CRM rows and skipped when the workspace is
--    null); at most one project per task.
-- 6. The workspace invariant (FR-TASK-DATA-05): `tasks_workspace_check`, a
--    deferred constraint trigger on both tables, checks at commit that a
--    live task has a workspace exactly when it has a CRM link, equal to the
--    workspace of every CRM link, and that only a task of a TVA-linked
--    workspace (Nunnari Labs) links a project. Deferred, because the CRM
--    writer and the deal transition insert a task before its links.
--    `tasks_workspace_fixed` lets a workspace change only from empty to a
--    value, so a CRM task never becomes personal. `tasks_parent_check`
--    keeps subtasks one level deep (FR-TASK-DATA-09).
-- 7. Indexes for My Tasks, the creator, tags, the text filter, subtasks, the
--    Life OS import key (FR-TASK-DATA-12), the 90-day purge and the day-log
--    read (data-model §4).
-- 8. The task status list of each workspace (FR-TASK-CHG-11, data-model
--    §3.1): Open, Waiting and Cancelled join Done as fixed options; To do
--    and In progress are deactivated and kept for history, the one stated
--    exception to "a keyed option is never switched off". A label an Admin
--    gave Done survives; a To do or In progress option an Admin renamed to
--    one of the new labels is suffixed " (old)"; any other clash stops the
--    migration.
-- 9. Saved views (spec §8.2, data-model §3.2): every task view's status
--    filters are rewritten, seeded or not: todo and in_progress become open;
--    "is not Done" and "none of Done" become "none of Done, Cancelled".
-- 10. The field registry gains `task.priority` and `task.source` (owner
--    decision of 18 October 2026, TQ-17, data-model §3.3).
--
-- RLS stays on with no policy and no browser grant (FR-TASK-VIS-03): a
-- rename keeps both.
--
-- Reversible (disaster recovery only: it loses personal tasks and the
-- waiting/cancelled distinction): delete rows with no workspace; map open
-- and waiting to todo and cancelled to done (completed_at = now()); drop the
-- triggers, functions, indexes, columns and constraints added here; make
-- workspace_id NOT NULL again; restore the todo|in_progress|done CHECK and
-- the 'todo' default; rename back; restore 026's
-- crm_select_options_keyed_active, reactivate todo/in_progress (todo the
-- default), delete the open/waiting/cancelled options; delete the two
-- registry rows. Rewritten saved views keep their new filters.

-- ---------------------------------------------------------------------------
-- 1. Renames
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    r record;
BEGIN
    IF to_regclass('public.crm_tasks') IS NOT NULL THEN
        ALTER TABLE crm_tasks RENAME TO tasks;
    END IF;
    IF to_regclass('public.crm_task_links') IS NOT NULL THEN
        ALTER TABLE crm_task_links RENAME TO task_links;
    END IF;

    -- Constraints (their indexes follow), then the other indexes, then the
    -- triggers.
    FOR r IN
        SELECT c.conrelid::regclass::text AS tbl, c.conname
        FROM pg_constraint c
        WHERE c.conrelid IN ('public.tasks'::regclass, 'public.task_links'::regclass)
          AND (c.conname LIKE 'crm\_tasks%' OR c.conname LIKE 'crm\_task\_links%')
    LOOP
        EXECUTE format('ALTER TABLE %s RENAME CONSTRAINT %I TO %I',
                       r.tbl, r.conname, regexp_replace(r.conname, '^crm_', ''));
    END LOOP;
    FOR r IN
        SELECT i.relname
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        WHERE x.indrelid IN ('public.tasks'::regclass, 'public.task_links'::regclass)
          AND (i.relname LIKE 'crm\_tasks%' OR i.relname LIKE 'crm\_task\_links%')
    LOOP
        EXECUTE format('ALTER INDEX %I RENAME TO %I',
                       r.relname, regexp_replace(r.relname, '^crm_', ''));
    END LOOP;
    FOR r IN
        SELECT t.tgrelid::regclass::text AS tbl, t.tgname
        FROM pg_trigger t
        WHERE t.tgrelid IN ('public.tasks'::regclass, 'public.task_links'::regclass)
          AND NOT t.tgisinternal
          AND (t.tgname LIKE 'crm\_tasks%' OR t.tgname LIKE 'crm\_task\_links%')
    LOOP
        EXECUTE format('ALTER TRIGGER %I ON %s RENAME TO %I',
                       r.tgname, r.tbl, regexp_replace(r.tgname, '^crm_', ''));
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. task_tags_ok — TP-14
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.task_tags_ok(p_tags text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = public, extensions
AS $$
    SELECT coalesce(cardinality(p_tags), 0) <= 10
       AND NOT EXISTS (
           SELECT 1 FROM unnest(p_tags) AS t(tag)
           WHERE tag IS NULL OR char_length(btrim(tag)) NOT BETWEEN 1 AND 40
       );
$$;

-- ---------------------------------------------------------------------------
-- 3. Columns — data-model §2.1, §2.2
-- ---------------------------------------------------------------------------

ALTER TABLE tasks ALTER COLUMN workspace_id DROP NOT NULL;
ALTER TABLE task_links ALTER COLUMN workspace_id DROP NOT NULL;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'normal'
        CONSTRAINT tasks_priority CHECK (priority IN ('urgent', 'high', 'normal', 'low')),
    ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}'
        CONSTRAINT tasks_tags CHECK (task_tags_ok(tags)),
    ADD COLUMN IF NOT EXISTS parent_id uuid REFERENCES tasks(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS waiting_on_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS waiting_on_text text
        CONSTRAINT tasks_waiting_on_text CHECK (char_length(waiting_on_text) <= 200),
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'manual'
        CONSTRAINT tasks_source CHECK (source IN (
            'manual', 'nldw', 'voice', 'email_commitment', 'meeting_action', 'rule', 'import'
        )),
    ADD COLUMN IF NOT EXISTS source_ref text
        CONSTRAINT tasks_source_ref CHECK (char_length(source_ref) <= 500);

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_not_own_parent;
ALTER TABLE tasks ADD CONSTRAINT tasks_not_own_parent CHECK (parent_id IS DISTINCT FROM id);
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_category_crm_only;
ALTER TABLE tasks ADD CONSTRAINT tasks_category_crm_only
    CHECK (workspace_id IS NOT NULL OR category_id IS NULL);

-- ---------------------------------------------------------------------------
-- 4. Statuses and their data — T2, TP-04, FR-TASK-DATA-08, data-model §2.1
-- ---------------------------------------------------------------------------

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status;
ALTER TABLE tasks ALTER COLUMN status SET DEFAULT 'open';

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- 1. To do and In progress become Open; In progress keeps a tag.
    UPDATE tasks SET status = 'open', tags = tags || ARRAY['in progress']
    WHERE status = 'in_progress';
    UPDATE tasks SET status = 'open' WHERE status = 'todo';

    -- 2. Every open or waiting row, deleted ones included, has a bucket.
    UPDATE tasks
    SET bucket = CASE
        WHEN due_on <= (now() AT TIME ZONE 'Asia/Kolkata')::date THEN 'today'
        ELSE 'later'
    END
    WHERE status IN ('open', 'waiting') AND bucket IS NULL;

    -- 3. Where each row came from.
    UPDATE tasks SET source = 'import' WHERE twenty_id IS NOT NULL AND source <> 'import';
END;
$$;

ALTER TABLE tasks ADD CONSTRAINT tasks_status
    CHECK (status IN ('open', 'waiting', 'done', 'cancelled'));
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_bucket_when_open;
ALTER TABLE tasks ADD CONSTRAINT tasks_bucket_when_open
    CHECK (status NOT IN ('open', 'waiting') OR bucket IS NOT NULL);
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_waiting_on;
ALTER TABLE tasks ADD CONSTRAINT tasks_waiting_on
    CHECK (status <> 'waiting' OR btrim(coalesce(waiting_on_text, '')) <> '');

-- ---------------------------------------------------------------------------
-- 5. Links — FR-TASK-DATA-06, -15; TP-13; data-model §2.2
-- ---------------------------------------------------------------------------

ALTER TABLE task_links
    ADD COLUMN IF NOT EXISTS project_id uuid
        REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE task_links DROP CONSTRAINT IF EXISTS task_links_record_type_check;
ALTER TABLE task_links DROP CONSTRAINT IF EXISTS task_links_record_type;
ALTER TABLE task_links ADD CONSTRAINT task_links_record_type
    CHECK (record_type IN ('company', 'person', 'opportunity', 'campaign', 'tva_project'));
ALTER TABLE task_links DROP CONSTRAINT IF EXISTS task_links_workspace_kind;
ALTER TABLE task_links ADD CONSTRAINT task_links_workspace_kind
    CHECK ((record_type = 'tva_project') = (workspace_id IS NULL));
ALTER TABLE task_links DROP CONSTRAINT IF EXISTS task_links_project;
ALTER TABLE task_links ADD CONSTRAINT task_links_project
    CHECK ((record_type = 'tva_project') = (project_id IS NOT NULL)
           AND (project_id IS NULL OR project_id = record_id));
ALTER TABLE task_links DROP CONSTRAINT IF EXISTS task_links_task_id;
ALTER TABLE task_links ADD CONSTRAINT task_links_task_id
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS task_links_one_project
    ON task_links (task_id) WHERE record_type = 'tva_project';
CREATE INDEX IF NOT EXISTS task_links_project_idx
    ON task_links (project_id) WHERE project_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 6. The workspace invariant and subtasks — FR-TASK-DATA-05, -09;
--    data-model §5.1, §5.2
-- ---------------------------------------------------------------------------

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

CREATE OR REPLACE FUNCTION public.tasks_workspace_fixed()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    IF OLD.workspace_id IS NOT NULL
       AND NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A CRM task cannot leave its workspace.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.tasks_parent_check()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    IF NEW.parent_id IS NOT NULL
       AND (EXISTS (SELECT 1 FROM tasks WHERE id = NEW.parent_id AND parent_id IS NOT NULL)
            OR EXISTS (SELECT 1 FROM tasks WHERE parent_id = NEW.id)) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A subtask cannot have subtasks.';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tasks_workspace_check ON tasks;
CREATE CONSTRAINT TRIGGER tasks_workspace_check
    AFTER INSERT OR UPDATE ON tasks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.tasks_workspace_check();
DROP TRIGGER IF EXISTS task_links_workspace_check ON task_links;
CREATE CONSTRAINT TRIGGER task_links_workspace_check
    AFTER INSERT OR UPDATE OR DELETE ON task_links
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.tasks_workspace_check();
DROP TRIGGER IF EXISTS tasks_workspace_fixed ON tasks;
CREATE TRIGGER tasks_workspace_fixed BEFORE UPDATE OF workspace_id ON tasks
    FOR EACH ROW EXECUTE FUNCTION public.tasks_workspace_fixed();
DROP TRIGGER IF EXISTS tasks_parent_check ON tasks;
CREATE TRIGGER tasks_parent_check BEFORE INSERT OR UPDATE OF parent_id ON tasks
    FOR EACH ROW EXECUTE FUNCTION public.tasks_parent_check();

-- ---------------------------------------------------------------------------
-- 7. Indexes — data-model §4 (NFR-TASK-01)
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS tasks_mine
    ON tasks (assignee_id, status, due_on) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS tasks_created_by
    ON tasks (created_by, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS tasks_tags ON tasks USING gin (tags);
CREATE INDEX IF NOT EXISTS tasks_title_trgm
    ON tasks USING gin (lower(title) extensions.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tasks_parent ON tasks (parent_id) WHERE parent_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tasks_lifeos_key
    ON tasks (source_ref) WHERE source = 'import' AND source_ref LIKE 'lifeos:%';
CREATE INDEX IF NOT EXISTS tasks_personal_trash
    ON tasks (deleted_at) WHERE workspace_id IS NULL AND deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_done_on
    ON tasks (assignee_id, completed_at) WHERE status = 'done' AND deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 8. The task status list — FR-TASK-CHG-11, data-model §3.1
-- ---------------------------------------------------------------------------

ALTER TABLE crm_select_options DROP CONSTRAINT IF EXISTS crm_select_options_keyed_active;
ALTER TABLE crm_select_options ADD CONSTRAINT crm_select_options_keyed_active
    CHECK (system_key IS NULL OR is_active
           OR (list_key = 'task.status' AND system_key IN ('todo', 'in_progress')));

DO $$
DECLARE
    w       record;
    n       record;
    clash   record;
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- 2. Off and not the default, so Open can be the one default.
    UPDATE crm_select_options
    SET is_default = false, is_active = false
    WHERE list_key = 'task.status' AND system_key IN ('todo', 'in_progress')
      AND (is_default OR is_active);

    FOR w IN SELECT id, name FROM crm_workspaces ORDER BY key LOOP
        FOR n IN
            SELECT * FROM (VALUES
                ('open', 'Open', 1, 'gray', true),
                ('waiting', 'Waiting', 2, 'red', false),
                ('cancelled', 'Cancelled', 4, 'amber', false)
            ) AS v(system_key, label, position, color, is_default)
        LOOP
            -- 3. A label already taken in this list.
            SELECT id, system_key INTO clash
            FROM crm_select_options
            WHERE workspace_id = w.id AND list_key = 'task.status'
              AND lower(label) = lower(n.label)
              AND system_key IS DISTINCT FROM n.system_key;
            IF FOUND THEN
                IF clash.system_key IN ('todo', 'in_progress') THEN
                    PERFORM set_config('crm.action', 'option.updated', true);
                    UPDATE crm_select_options SET label = label || ' (old)' WHERE id = clash.id;
                    PERFORM set_config('crm.action', '', true);
                ELSE
                    RAISE EXCEPTION
                        'Workspace "%": the task status list already has an option labelled "%".',
                        w.name, n.label;
                END IF;
            END IF;

            -- 4. The new option.
            INSERT INTO crm_select_options
                (workspace_id, list_key, system_key, label, color, position, is_default,
                 is_fixed)
            VALUES (w.id, 'task.status', n.system_key, n.label, n.color, n.position,
                    n.is_default, true)
            ON CONFLICT (workspace_id, list_key, system_key) WHERE system_key IS NOT NULL
            DO NOTHING;
        END LOOP;
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------------
-- 9. Saved views — spec §8.2, data-model §3.2
-- ---------------------------------------------------------------------------

-- One filter node, rewritten: a group's items recursively; a status
-- condition's old keys. A temporary function, gone at the end of the session.
CREATE OR REPLACE FUNCTION pg_temp.task_status_filter(p_node jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    val  jsonb;
    vals jsonb;
BEGIN
    IF jsonb_typeof(p_node) <> 'object' THEN
        RETURN p_node;
    END IF;
    IF p_node ? 'items' AND jsonb_typeof(p_node -> 'items') = 'array' THEN
        RETURN jsonb_set(p_node, '{items}', coalesce(
            (SELECT jsonb_agg(pg_temp.task_status_filter(i.value) ORDER BY i.ord)
             FROM jsonb_array_elements(p_node -> 'items') WITH ORDINALITY AS i(value, ord)),
            '[]'::jsonb));
    END IF;
    IF p_node ->> 'field' IS DISTINCT FROM 'status' THEN
        RETURN p_node;
    END IF;

    val := p_node -> 'value';
    IF jsonb_typeof(val) = 'string' AND val #>> '{}' IN ('todo', 'in_progress') THEN
        p_node := jsonb_set(p_node, '{value}', '"open"');
    ELSIF jsonb_typeof(val) = 'array' THEN
        -- Old keys become open, each value kept once, in first-seen order.
        SELECT coalesce(jsonb_agg(to_jsonb(x.v) ORDER BY x.first), '[]'::jsonb)
        INTO vals
        FROM (
            SELECT CASE WHEN e.v IN ('todo', 'in_progress') THEN 'open' ELSE e.v END AS v,
                   min(e.ord) AS first
            FROM jsonb_array_elements_text(val) WITH ORDINALITY AS e(v, ord)
            GROUP BY 1
        ) x;
        p_node := jsonb_set(p_node, '{value}', vals);
    END IF;

    IF p_node ->> 'operator' = 'is_not' AND p_node ->> 'value' = 'done' THEN
        p_node := p_node || '{"operator": "none_of", "value": ["done", "cancelled"]}';
    ELSIF p_node ->> 'operator' = 'none_of' AND p_node -> 'value' = '["done"]'::jsonb THEN
        p_node := jsonb_set(p_node, '{value}', '["done", "cancelled"]');
    END IF;
    RETURN p_node;
END;
$$;

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );
    PERFORM set_config('crm.action', 'view.updated', true);

    UPDATE crm_saved_views v
    SET config = jsonb_set(v.config, '{filters}', pg_temp.task_status_filter(v.config -> 'filters'))
    WHERE v.object = 'task' AND v.config ? 'filters'
      AND pg_temp.task_status_filter(v.config -> 'filters') IS DISTINCT FROM v.config -> 'filters';

    PERFORM set_config('crm.action', '', true);
END;
$$;

DROP FUNCTION pg_temp.task_status_filter(jsonb);

-- ---------------------------------------------------------------------------
-- 10. Field registry — data-model §3.3 (TQ-17), as 034's rows are written
-- ---------------------------------------------------------------------------

INSERT INTO crm_object_fields (object, field, column_sql, type, is_amount, filterable, sortable,
                               groupable, aggregatable, workspace_scope)
VALUES
    ('task', 'priority', 'priority', 'fixed_select', false, true, true, true, false, 'all'),
    ('task', 'source', 'source', 'fixed_select', false, true, true, true, false, 'all')
ON CONFLICT (object, field) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Privileges — data-model §5 (RLS and table grants are kept by the rename)
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.task_tags_ok(text[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.tasks_workspace_check() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.tasks_workspace_fixed() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.tasks_parent_check() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.task_tags_ok(text[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.tasks_workspace_check() TO service_role;
GRANT EXECUTE ON FUNCTION public.tasks_workspace_fixed() TO service_role;
GRANT EXECUTE ON FUNCTION public.tasks_parent_check() TO service_role;
