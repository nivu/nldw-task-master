-- 052_task_delegation.sql
--
-- Spec 008 (tasks) FR-TASK-DEL-01 to -07; TQ-12; data-model §2.1 (phase 2),
-- §2.5, §2.6, §2.7, §5.2, §5.4; plan §5.8.
--
-- Handing work over (phase 2):
--
-- 1. `tasks.assigned_by` (who set the current assignee; null for a
--    self-assignment and for rows from before this migration) and
--    `tasks.accepted_at` (FR-TASK-DEL-02). "New" is computed on read:
--    `assigned_by IS NOT NULL AND assigned_by <> assignee_id AND accepted_at
--    IS NULL`. `assigned_by` is `ON DELETE SET NULL`; 048's
--    `crm_audit_row()` already records that referential update as the
--    system, so deleting a profile named there succeeds.
-- 2. `task_comments` (FR-TASK-DEL-03, -04): short texts on a task, at most
--    2,000 characters, `comment` or the note of a `hand_back`. Never edited;
--    soft-deleted by their author only (decided in Python). `workspace_id` is
--    the task's when the comment is written, set by Python and checked by
--    `task_comments_workspace`, so a CRM task's comments are in its workspace
--    audit and a comment written while the task had no workspace stays
--    private-era (FR-TASK-VIS-05). Audited through `task_apply`; RLS on, no
--    grants.
-- 3. `task_notice_sends` (FR-TASK-DEL-07): the sent marker of each message,
--    one per (task, event, recipient, time of the event). The sender claims
--    it with INSERT ... ON CONFLICT DO NOTHING RETURNING id before sending,
--    so a retried job sends nothing twice. A log, as `crm_digest_sends`
--    (037): written directly by the backend, no audit trigger; RLS on, no
--    grants.
-- 4. `profiles.task_nudge_days` (FR-TASK-DEL-06): how many idle days make a
--    waiting task a "nudge?" in the person's morning brief, 1-30, default 5;
--    changed by the person through `PATCH /tasks/settings`.
-- 5. `task_apply()` replaced (049's, two changes): `workspace_id` may be set
--    on `task_comments` too, and a comment takes only `insert` and
--    `soft_delete` (CRM22 "A comment cannot be changed."). Not one of the
--    functions 009 replaces (data-model §1).
--
-- Reversible: re-run 049's `CREATE OR REPLACE FUNCTION task_apply`;
-- ALTER TABLE profiles DROP COLUMN task_nudge_days; DROP TABLE
-- task_notice_sends, task_comments; DROP FUNCTION task_comments_workspace();
-- ALTER TABLE tasks DROP COLUMN accepted_at, DROP COLUMN assigned_by.

-- ---------------------------------------------------------------------------
-- 1. tasks.assigned_by, tasks.accepted_at — data-model §2.1 (phase 2)
-- ---------------------------------------------------------------------------

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS assigned_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS accepted_at timestamptz;

-- ---------------------------------------------------------------------------
-- 2. task_comments — data-model §2.5
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_comments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id      uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    -- The task's workspace when written (task_comments_workspace).
    workspace_id uuid REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    author_id    uuid NOT NULL REFERENCES profiles(id),
    body         text NOT NULL CHECK (btrim(body) <> '' AND char_length(body) <= 2000),
    kind         text NOT NULL DEFAULT 'comment' CHECK (kind IN ('comment', 'hand_back')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,
    deleted_by   uuid REFERENCES profiles(id)
);

CREATE INDEX IF NOT EXISTS task_comments_task ON task_comments (task_id, created_at);

-- A comment carries its task's workspace when it is written, and keeps it.
CREATE OR REPLACE FUNCTION public.task_comments_workspace()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.workspace_id IS DISTINCT FROM (
            SELECT workspace_id FROM tasks WHERE id = NEW.task_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A comment belongs to its task''s workspace.';
        END IF;
    ELSIF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A comment belongs to its task''s workspace.';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS task_comments_workspace ON task_comments;
CREATE TRIGGER task_comments_workspace BEFORE INSERT OR UPDATE ON task_comments
    FOR EACH ROW EXECUTE FUNCTION public.task_comments_workspace();
DROP TRIGGER IF EXISTS task_comments_crm_audit ON task_comments;
CREATE TRIGGER task_comments_crm_audit AFTER INSERT OR UPDATE OR DELETE ON task_comments
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE task_comments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON task_comments FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. task_notice_sends — data-model §2.6 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_notice_sends (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id      uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    event        text NOT NULL CHECK (event IN ('assigned', 'completed', 'handed_back')),
    recipient_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    -- The time of the event (the task's `updated_at` as that write left it).
    occurred_at  timestamptz NOT NULL,
    sent_at      timestamptz,
    channels     text[],
    CONSTRAINT task_notice_sends_once UNIQUE (task_id, event, recipient_id, occurred_at)
);

ALTER TABLE task_notice_sends ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON task_notice_sends FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. profiles.task_nudge_days — data-model §2.7
-- ---------------------------------------------------------------------------

ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS task_nudge_days smallint NOT NULL DEFAULT 5
        CHECK (task_nudge_days BETWEEN 1 AND 30);

-- ---------------------------------------------------------------------------
-- 5. task_apply — data-model §5.2 (049's, with the two changes above)
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
               OR k IN ('id', 'created_at', 'created_by')
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

REVOKE ALL ON FUNCTION public.task_apply(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.task_comments_workspace() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.task_apply(jsonb, jsonb) TO service_role;
