-- 053_task_recurrence.sql
--
-- Spec 008 (tasks) FR-TASK-RRULE-01 to -04; TP-18; T9; data-model §2.1
-- (phase 3), §2.8, §5.2.
--
-- Recurring tasks (phase 3):
--
-- 1. `tasks.recurrence` (an RRULE body such as `FREQ=WEEKLY;BYDAY=MO,TH`, at
--    most 200 characters; Python accepts only the four forms of
--    FR-TASK-RRULE-01: daily, weekly on given days, monthly on a day,
--    yearly), `tasks.series_id` (the series an occurrence belongs to: the
--    first occurrence's id) and `tasks.recurrence_spawned` (this occurrence
--    has created its next one). A recurring task has a due date
--    (`tasks_recurrence_due`, FR-TASK-RRULE-01).
-- 2. Completing or cancelling a recurring task (TP-18) inserts the next
--    occurrence in the same `task_apply` call that closes the current one
--    and sets its `recurrence_spawned`; its links are copied in that call.
--    The partial unique index `tasks_series_due` on `(series_id, due_on)`
--    of live rows refuses a second next occurrence in a race
--    (FR-TASK-RRULE-03); the task store maps it to 409 "This recurring task
--    already has its next occurrence.". Removing `recurrence` from an
--    occurrence ends the series (FR-TASK-RRULE-04); nothing here is needed
--    for that.
-- 3. `task_apply()` replaced (052's, one change): an insert into `tasks`
--    may name `created_by` only when that person created a task of the
--    insert's `series_id`, so the next occurrence keeps the series'
--    creator and with it the task's kind and who sees it ("same fields",
--    FR-TASK-RRULE-02); every other write still takes the actor. Not one of
--    the functions 009 replaces (data-model §1).
--
-- Reversible: re-run 052's `CREATE OR REPLACE FUNCTION task_apply`;
-- DROP INDEX tasks_series_due; ALTER TABLE tasks DROP CONSTRAINT
-- tasks_recurrence_due, DROP COLUMN recurrence_spawned, DROP COLUMN
-- series_id, DROP COLUMN recurrence.

-- ---------------------------------------------------------------------------
-- 1. tasks.recurrence, series_id, recurrence_spawned — data-model §2.1 (phase 3)
-- ---------------------------------------------------------------------------

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS recurrence text
        CONSTRAINT tasks_recurrence_length CHECK (char_length(recurrence) <= 200),
    ADD COLUMN IF NOT EXISTS series_id uuid,
    ADD COLUMN IF NOT EXISTS recurrence_spawned boolean NOT NULL DEFAULT false;

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_recurrence_due;
ALTER TABLE tasks ADD CONSTRAINT tasks_recurrence_due
    CHECK (recurrence IS NULL OR due_on IS NOT NULL);

-- ---------------------------------------------------------------------------
-- 2. One next occurrence per due date — data-model §2.8 (FR-TASK-RRULE-03)
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS tasks_series_due ON tasks (series_id, due_on)
    WHERE series_id IS NOT NULL AND deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 3. task_apply — data-model §5.2 (052's, with the change above)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.task_apply(p_actor jsonb, p_ops jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    who      uuid := (p_actor ->> 'user_id')::uuid;
    akind    text := p_actor ->> 'kind';
    op       jsonb;
    tbl      text;
    kind     text;
    vals     jsonb;
    cols     text[];
    keys     text[];
    k        text;
    v        jsonb;
    refs     jsonb := '{}';
    results  jsonb := '[]';
    row_j    jsonb;
    old_j    jsonb;
    subject  jsonb;
    task_j   jsonb;
    target   uuid;
    collist  text;
    setlist  text;
    allowed  boolean;
BEGIN
    IF coalesce(akind, '') NOT IN ('user', 'system', 'import')
       OR (akind <> 'system' AND who IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A task change needs an actor.';
    END IF;
    IF jsonb_typeof(p_ops) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    FOR op IN SELECT value FROM jsonb_array_elements(p_ops) LOOP
        tbl  := op ->> 'table';
        kind := op ->> 'op';
        IF tbl IS NULL
           OR tbl NOT IN ('tasks', 'task_links', 'task_comments', 'task_import_runs',
                          'inbox_items')
           OR to_regclass(format('public.%I', tbl)) IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgrelid = to_regclass(format('public.%I', tbl))
                 AND tgname = tbl || '_crm_audit'
           ) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That is not a task table.';
        END IF;
        IF kind IS NULL OR kind NOT IN ('insert', 'update', 'soft_delete', 'restore', 'delete') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Unknown task operation.';
        END IF;
        -- 052: a comment is written once and never edited; its author may
        -- only delete it (FR-TASK-DEL-04).
        IF tbl = 'task_comments' AND kind NOT IN ('insert', 'soft_delete') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A comment cannot be changed.';
        END IF;

        SELECT array_agg(attname::text) INTO cols
        FROM pg_attribute
        WHERE attrelid = to_regclass(format('public.%I', tbl)) AND attnum > 0
          AND NOT attisdropped;

        -- Values: resolve "$ref:<name>" to an id an earlier op created, and
        -- refuse unknown or protected columns.
        vals := '{}';
        FOR k, v IN SELECT key, value FROM jsonb_each(coalesce(op -> 'values', '{}')) LOOP
            IF NOT k = ANY (cols)
               OR k IN ('id', 'created_at')
               -- 053: only the next occurrence of a series may name its
               -- creator (checked below); every other write takes the actor.
               OR (k = 'created_by' AND NOT (tbl = 'tasks' AND kind = 'insert'))
               OR (k = 'workspace_id' AND tbl NOT IN ('tasks', 'task_links', 'task_comments')) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = format('%s cannot be set here.', k);
            END IF;
            IF jsonb_typeof(v) = 'string' AND (v #>> '{}') LIKE '$ref:%' THEN
                v := refs -> substr(v #>> '{}', 6);
                IF v IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Unknown reference.';
                END IF;
            END IF;
            vals := vals || jsonb_build_object(k, v);
        END LOOP;

        PERFORM set_config('crm.action', coalesce(op ->> 'action', ''), true);
        target := (op ->> 'id')::uuid;
        row_j := NULL;
        old_j := NULL;

        IF kind = 'insert' THEN
            IF tbl = 'tasks' AND vals ? 'created_by' THEN
                -- 053 (FR-TASK-RRULE-02): the next occurrence keeps the
                -- series' creator, so who sees it does not change; the
                -- creator must be the creator of a task of that series.
                IF NOT EXISTS (
                    SELECT 1 FROM tasks t
                    WHERE t.series_id = (vals ->> 'series_id')::uuid
                      AND t.created_by = (vals ->> 'created_by')::uuid
                ) THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22',
                        MESSAGE = 'created_by cannot be set here.';
                END IF;
            ELSIF 'created_by' = ANY (cols) THEN
                vals := vals || jsonb_build_object('created_by', who);
            END IF;
            IF 'updated_by' = ANY (cols) THEN
                vals := vals || jsonb_build_object('updated_by', who);
            END IF;
            subject := vals;
        ELSE
            IF target IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Which task?';
            END IF;
            EXECUTE format('SELECT to_jsonb(t.*) FROM %I t WHERE t.id = $1 FOR UPDATE', tbl)
            INTO old_j USING target;
            IF old_j IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such task.';
            END IF;
            subject := old_j;
        END IF;

        -- The caller's right to the row as stored (as written, for an
        -- insert); a link or comment answers to its task.
        IF tbl IN ('tasks', 'task_links', 'task_comments') THEN
            IF tbl = 'tasks' THEN
                task_j := subject;
            ELSE
                SELECT to_jsonb(t.*) INTO task_j FROM tasks t
                WHERE t.id = (subject ->> 'task_id')::uuid;
                IF task_j IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such task.';
                END IF;
            END IF;
            IF task_j ->> 'workspace_id' IS NOT NULL THEN
                allowed := EXISTS (
                    SELECT 1 FROM crm_memberships
                    WHERE workspace_id = (task_j ->> 'workspace_id')::uuid AND user_id = who
                );
            ELSE
                allowed := akind IN ('system', 'import')
                    OR task_j ->> 'created_by' = who::text
                    OR task_j ->> 'assignee_id' = who::text;
            END IF;
        ELSIF tbl = 'task_import_runs' THEN
            allowed := subject ->> 'uploader_id' = who::text;
        ELSE
            allowed := subject ->> 'owner_id' = who::text;
        END IF;
        IF allowed IS NOT TRUE THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such task.';
        END IF;

        IF kind = 'insert' THEN
            SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(vals) AS key;
            SELECT string_agg(format('%I', c), ', ') INTO collist FROM unnest(keys) AS c;
            EXECUTE format(
                'INSERT INTO %I (%s) SELECT %s FROM jsonb_populate_record(NULL::%I, $1) '
                'RETURNING to_jsonb(%I.*)', tbl, collist, collist, tbl, tbl)
            INTO row_j USING vals;
        ELSE
            IF op ? 'expect_updated_at' AND op ->> 'expect_updated_at' IS NOT NULL
               AND (old_j ->> 'updated_at')::timestamptz
                   IS DISTINCT FROM (op ->> 'expect_updated_at')::timestamptz THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM09',
                    MESSAGE = 'This task changed while you were editing it; reload and try again.';
            END IF;

            IF kind = 'delete' THEN
                EXECUTE format('DELETE FROM %I t WHERE t.id = $1 RETURNING to_jsonb(t.*)', tbl)
                INTO row_j USING target;
            ELSE
                IF kind = 'update' AND vals = '{}' THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
                END IF;
                IF kind IN ('soft_delete', 'restore') THEN
                    IF NOT 'deleted_at' = ANY (cols) THEN
                        RAISE EXCEPTION USING ERRCODE = 'CRM22',
                            MESSAGE = 'This kind of row cannot be deleted.';
                    END IF;
                    vals := jsonb_build_object(
                        'deleted_at', CASE WHEN kind = 'soft_delete' THEN to_jsonb(now()) END,
                        'deleted_by', CASE WHEN kind = 'soft_delete' THEN to_jsonb(who) END);
                    IF 'deleted_group' = ANY (cols) THEN
                        vals := vals || jsonb_build_object(
                            'deleted_group',
                            CASE WHEN kind = 'soft_delete' THEN op -> 'deleted_group' END);
                    END IF;
                END IF;
                IF 'updated_by' = ANY (cols) AND NOT vals ? 'updated_by' THEN
                    vals := vals || jsonb_build_object('updated_by', who);
                END IF;
                SELECT string_agg(format('%1$I = r.%1$I', key), ', ') INTO setlist
                FROM jsonb_object_keys(vals) AS key;
                EXECUTE format('UPDATE %1$I t SET %2$s FROM jsonb_populate_record(NULL::%1$I, $1) r '
                               'WHERE t.id = $2 RETURNING to_jsonb(t.*)', tbl, setlist)
                INTO row_j USING vals, target;
            END IF;
        END IF;

        IF op ? 'ref' AND row_j IS NOT NULL THEN
            refs := refs || jsonb_build_object(op ->> 'ref', row_j -> 'id');
        END IF;
        results := results || jsonb_build_array(jsonb_build_object(
            'ref', op -> 'ref', 'id', coalesce(row_j -> 'id', op -> 'id'), 'row', row_j));
        PERFORM set_config('crm.action', '', true);
    END LOOP;

    RETURN results;
END;
$$;

REVOKE ALL ON FUNCTION public.task_apply(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.task_apply(jsonb, jsonb) TO service_role;
