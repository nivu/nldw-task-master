-- 049_task_write_path.sql
--
-- Spec 008 (tasks) FR-TASK-DATA-05, -10, -11, -15; TQ-02; TP-02, TP-12;
-- NFR-TASK-03; plan §4 CT-3, §5.3; data-model §5.2, §5.3.
--
-- The task write path (TQ-02). The CRM writer requires a workspace and only
-- writes `crm_*` tables; a task with no workspace needs a sibling that keeps
-- both of its guarantees: the actor is set in the same transaction as the
-- write, so `crm_audit_row()` audits every change with actor, kind and
-- channel (FR-TASK-DATA-10), and the caller's right to the row is re-checked
-- in the database as a backstop to the Python rules.
--
-- 1. task_apply(actor, ops): every task write of the task routes and
--    `create_task`, as a list of operations applied in order in one
--    transaction (insert, update, soft_delete, restore, delete), in the shape
--    of `crm_apply`'s: `values`, `id`, `expect_updated_at` (CRM09 when
--    stale), `action`, `deleted_group`, `ref` and "$ref:<name>" values. The
--    actor is `{user_id, kind: user|system|import, channel: web|mcp|null}`,
--    plus `suggestion_id` when the caller set one; it has no workspace: each
--    row is audited with its own (for a link, its task's). Only the task
--    tables are written, each required to carry its audit trigger; `id`,
--    `created_at` and `created_by` are never taken from the caller, and
--    `workspace_id` only on `tasks` and `task_links` (the deferred
--    `tasks_workspace_check` of 047 validates it at commit). Per op, the
--    actor's right is re-checked against the row as stored (for an insert,
--    as written; for a link, its task): a row with a workspace needs the
--    actor's membership of it (the role is decided in Python); a task with
--    no workspace needs the actor to be its creator or assignee, or kind
--    system or import; an import run needs its uploader; an inbox item its
--    owner. Anything else is CRM04 "No such task.", so a row the caller may
--    not see is not disclosed. SQL is built only with format('%I') against
--    pg_attribute and values travel through jsonb_populate_record with USING
--    (CT-3).
-- 2. task_purge_expired(actor, before): the daily purge of tasks with no
--    workspace deleted before `before` (90 days, TP-12; FR-TASK-DATA-11).
--    Their links cascade. Each is audited `task.purged` under the system
--    actor. CRM tasks are never touched here: their Trash has `crm_purge`.
--
-- EXECUTE is revoked from PUBLIC, anon and authenticated and granted to
-- service_role.
--
-- Reversible: DROP FUNCTION task_purge_expired(jsonb, timestamptz),
-- task_apply(jsonb, jsonb).

-- ---------------------------------------------------------------------------
-- 1. task_apply — data-model §5.2
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

        SELECT array_agg(attname::text) INTO cols
        FROM pg_attribute
        WHERE attrelid = to_regclass(format('public.%I', tbl)) AND attnum > 0
          AND NOT attisdropped;

        -- Values: resolve "$ref:<name>" to an id an earlier op created, and
        -- refuse unknown or protected columns.
        vals := '{}';
        FOR k, v IN SELECT key, value FROM jsonb_each(coalesce(op -> 'values', '{}')) LOOP
            IF NOT k = ANY (cols)
               OR k IN ('id', 'created_at', 'created_by')
               OR (k = 'workspace_id' AND tbl NOT IN ('tasks', 'task_links')) THEN
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
            IF 'created_by' = ANY (cols) THEN
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

-- ---------------------------------------------------------------------------
-- 2. task_purge_expired — data-model §5.3
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.task_purge_expired(p_actor jsonb, p_before timestamptz)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    purged integer := 0;
BEGIN
    IF coalesce(p_actor ->> 'kind', '') <> 'system' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A task change needs an actor.';
    END IF;
    IF p_before IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Purge before when?';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);
    PERFORM set_config('crm.action', '', true);

    DELETE FROM tasks
    WHERE workspace_id IS NULL AND deleted_at IS NOT NULL AND deleted_at < p_before;
    GET DIAGNOSTICS purged = ROW_COUNT;
    RETURN purged;
END;
$$;

REVOKE ALL ON FUNCTION public.task_apply(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.task_purge_expired(jsonb, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.task_apply(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.task_purge_expired(jsonb, timestamptz) TO service_role;
