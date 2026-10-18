-- 048_tasks_crm_functions.sql
--
-- Spec 008 (tasks) §8.2 "Database functions", FR-TASK-CHG-01, -04, -07,
-- -11, FR-TASK-DATA-04, -05, -10, -14, FR-TASK-VIS-05; TQ-17;
-- data-model §5.4.
--
-- Every CRM function that names the task tables or tests "not done" is
-- replaced from its latest version, changing only what data-model §5.4
-- lists. Replace-only DDL: no data changes.
--
-- 1. `crm_apply` (027) is dropped and created again with a third parameter,
--    `p_task_needs_link boolean DEFAULT true`: the CRM's "a task needs a
--    linked record" becomes a parameter of the writer (FR-TASK-CHG-01), and
--    only CRM links count. Its table guard also accepts `tasks` and
--    `task_links`, still requiring their audit trigger. Inserts still stamp
--    the actor's workspace, so it cannot write a project link (the link's
--    kind CHECK refuses one).
-- 2. `crm_check_refs` (027): the `task` record type names `tasks`; a
--    `tva_project` link is left to its foreign key.
-- 3. `crm_deal_transition` (033): table names; open is Open or Waiting;
--    Closed Lost cancels the deal's open and waiting tasks instead of
--    completing them (FR-TASK-CHG-04, TP-07); a created task is Open, Normal,
--    with source Rule.
-- 4. `crm_purge` (042): table names; purging a record also purges, in the
--    same transaction, every task of the workspace it leaves with no CRM
--    link, live or in the Trash (FR-TASK-DATA-14).
-- 5. `crm_merge_companies` (042), `crm_search` (041), `crm_query` (041),
--    `crm_set_flags` (038): table names.
-- 6. `crm_query_rank` (041): the shared status array becomes the task
--    statuses of FR-TASK-CHG-11 followed by the campaign members' (kept in
--    their order); a task's priority ranks most urgent first. With
--    `crm_query_rank_type` (038) taking `priority` as a ranked fixed select
--    (TQ-17, data-model §3.3).
-- 7. `crm_flag_facts` (039, four-argument): table names; an open task is
--    Open or Waiting (FR-TASK-DATA-04).
-- 8. `crm_audit_row` (039): a `task_links` row with no workspace (a TVA
--    project link) is audited with its task's workspace, so a Labs task's
--    project-link history stays in the workspace and a personal task's stays
--    out of it; and a task's Waiting on person or assigner cleared, or an
--    inbox item removed, because the profile it names was deleted, is
--    recorded as the system instead of refused, as 039 does for the other
--    referential actions. The `assigned_by` column and `inbox_items` arrive
--    in 052 and 055; the trigger reads rows as jsonb, so it needs no later
--    replacement. Everything else is 039's body.
--
-- EXECUTE is revoked from PUBLIC, anon and authenticated and granted to
-- service_role, as 027 to 042 do.
--
-- Reversible: after 047's reverse, DROP FUNCTION crm_apply(jsonb, jsonb,
-- boolean) and re-run 027's crm_apply; re-run the CREATE OR REPLACE bodies
-- of 027 (crm_check_refs), 033 (crm_deal_transition), 042 (crm_purge,
-- crm_merge_companies), 041 (crm_search, crm_query, crm_query_rank), 038
-- (crm_query_rank_type, crm_set_flags) and 039 (crm_flag_facts,
-- crm_audit_row), with their grants.

-- ---------------------------------------------------------------------------
-- 1. crm_apply — data-model §5.4 (027's, with the link rule as a parameter)
-- ---------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.crm_apply(jsonb, jsonb);

CREATE OR REPLACE FUNCTION public.crm_apply(
    p_actor jsonb, p_ops jsonb, p_task_needs_link boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    op          jsonb;
    tbl         text;
    kind        text;
    vals        jsonb;
    cols        text[];
    keys        text[];
    k           text;
    v           jsonb;
    refs        jsonb := '{}';
    results     jsonb := '[]';
    tasks       uuid[] := '{}';
    row_j       jsonb;
    old_j       jsonb;
    target      uuid;
    scope       text;
    collist     text;
    setlist     text;
    linked      boolean;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system', 'import', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF jsonb_typeof(p_ops) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    FOR op IN SELECT value FROM jsonb_array_elements(p_ops) LOOP
        tbl  := op ->> 'table';
        kind := op ->> 'op';
        -- 048: the two shared task tables are CRM tables here too.
        IF tbl IS NULL OR NOT (tbl ~ '^crm_[a-z_]+$' OR tbl IN ('tasks', 'task_links'))
           OR to_regclass(format('public.%I', tbl)) IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgrelid = to_regclass(format('public.%I', tbl))
                 AND tgname = tbl || '_crm_audit'
           ) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That is not a CRM table.';
        END IF;
        IF kind IS NULL OR kind NOT IN
           ('insert', 'update', 'soft_delete', 'restore', 'delete', 'upsert', 'audit_only') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Unknown CRM operation.';
        END IF;

        SELECT array_agg(attname::text) INTO cols
        FROM pg_attribute
        WHERE attrelid = to_regclass(format('public.%I', tbl)) AND attnum > 0
          AND NOT attisdropped;
        -- crm_workspaces is the one table keyed by its own id.
        scope := CASE WHEN tbl = 'crm_workspaces' THEN 'id' ELSE 'workspace_id' END;

        -- Values: resolve "$ref:<name>" to an id an earlier op created, and
        -- refuse unknown or protected columns.
        vals := '{}';
        FOR k, v IN SELECT key, value FROM jsonb_each(coalesce(op -> 'values', '{}')) LOOP
            IF NOT k = ANY (cols)
               OR k IN ('id', 'workspace_id', 'created_at', 'created_by') THEN
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

        IF tbl = 'crm_opportunities' AND kind IN ('update', 'upsert') AND vals ? 'stage' THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Use a stage move.';
        END IF;
        -- Access is granted and taken away only by crm_invite and
        -- crm_remove_membership, which hold Q-75 and FR-CRM-ACC-12 (Q-68).
        IF tbl = 'crm_memberships' AND kind = 'delete' THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Remove a member through the members page.';
        END IF;
        IF tbl = 'crm_memberships' AND (kind IN ('insert', 'upsert') OR vals ? 'user_id') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Invite a member through the members page.';
        END IF;
        -- A workspace's key, TVA link and entity are set by migration; its
        -- allow-list limits whom a workspace admin may invite, so only a TVA
        -- admin changes it (FR-CRM-ACC-06).
        IF tbl = 'crm_workspaces' AND kind = 'delete' THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Workspaces are created and removed by migration.';
        END IF;
        IF tbl = 'crm_workspaces' AND kind = 'update' THEN
            IF EXISTS (SELECT 1 FROM jsonb_object_keys(vals) AS c
                       WHERE c NOT IN ('name', 'allowed_domains')) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = 'Only a workspace''s name and allow-list can change here.';
            END IF;
            IF vals ? 'allowed_domains' AND NOT crm_is_tva_admin(who) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = 'Only a TVA admin can change the domain allow-list.';
            END IF;
        END IF;

        IF kind = 'audit_only' THEN
            INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                   target_table, target_id, after)
            VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws,
                    coalesce(op ->> 'action', 'audit'), tbl, op ->> 'id', op -> 'values');

        ELSIF kind IN ('insert', 'upsert') THEN
            IF tbl = 'crm_workspaces' THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = 'Workspaces are created by migration.';
            END IF;
            vals := vals || jsonb_build_object('workspace_id', ws);
            IF 'created_by' = ANY (cols) THEN
                vals := vals || jsonb_build_object('created_by', who);
            END IF;
            IF 'updated_by' = ANY (cols) THEN
                vals := vals || jsonb_build_object('updated_by', who);
            END IF;
            SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(vals) AS key;
            SELECT string_agg(format('%I', c), ', ') INTO collist FROM unnest(keys) AS c;

            IF kind = 'insert' THEN
                EXECUTE format(
                    'INSERT INTO %I (%s) SELECT %s FROM jsonb_populate_record(NULL::%I, $1) '
                    'RETURNING to_jsonb(%I.*)', tbl, collist, collist, tbl, tbl)
                INTO row_j USING vals;
            ELSE
                -- Importer idempotence (FR-CRM-MIG-07): an unchanged row is not
                -- rewritten, so a second run writes no audit rows.
                IF NOT (vals ? 'twenty_id') OR vals ->> 'twenty_id' IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'An upsert needs twenty_id.';
                END IF;
                SELECT string_agg(format('%1$I = EXCLUDED.%1$I', c), ', ') INTO setlist
                FROM unnest(keys) AS c
                WHERE c NOT IN ('workspace_id', 'twenty_id', 'created_by');
                EXECUTE format(
                    'INSERT INTO %1$I AS t (%2$s) SELECT %2$s FROM jsonb_populate_record(NULL::%1$I, $1) '
                    'ON CONFLICT (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL '
                    'DO UPDATE SET %3$s WHERE (%4$s) IS DISTINCT FROM (%5$s) '
                    'RETURNING to_jsonb(t.*)',
                    tbl, collist, setlist,
                    (SELECT string_agg(format('t.%I', c), ', ') FROM unnest(keys) AS c
                     WHERE c NOT IN ('workspace_id', 'twenty_id', 'created_by', 'updated_by')),
                    (SELECT string_agg(format('EXCLUDED.%I', c), ', ') FROM unnest(keys) AS c
                     WHERE c NOT IN ('workspace_id', 'twenty_id', 'created_by', 'updated_by')))
                INTO row_j USING vals;
                IF row_j IS NULL THEN
                    EXECUTE format('SELECT to_jsonb(t.*) FROM %I t '
                                   'WHERE workspace_id = $1 AND twenty_id = $2', tbl)
                    INTO row_j USING ws, vals ->> 'twenty_id';
                END IF;
            END IF;

            IF tbl = 'crm_opportunities' AND kind = 'insert' THEN
                EXECUTE 'INSERT INTO crm_stage_history '
                        '(workspace_id, opportunity_id, from_stage, to_stage, "by", actor_kind) '
                        'VALUES ($1, $2, NULL, $3, $4, $5)'
                USING ws, (row_j ->> 'id')::uuid, row_j ->> 'stage', who, p_actor ->> 'kind';
            END IF;
            IF tbl = 'tasks' AND kind = 'insert' THEN
                tasks := tasks || (row_j ->> 'id')::uuid;
            END IF;

        ELSE
            IF target IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Which record?';
            END IF;
            -- A role change locks the workspace's admin memberships first, in
            -- id order, so two concurrent demotions queue instead of
            -- deadlocking, and the second sees the first.
            IF tbl = 'crm_memberships' AND vals ? 'role' THEN
                PERFORM 1 FROM crm_memberships
                WHERE workspace_id = ws AND role = 'admin' ORDER BY id FOR UPDATE;
            END IF;
            EXECUTE format('SELECT to_jsonb(t.*) FROM %I t WHERE t.id = $1 AND t.%I = $2 '
                           'FOR UPDATE', tbl, scope)
            INTO old_j USING target, ws;
            IF old_j IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such record in this workspace.';
            END IF;
            IF op ? 'expect_updated_at' AND op ->> 'expect_updated_at' IS NOT NULL
               AND (old_j ->> 'updated_at')::timestamptz
                   IS DISTINCT FROM (op ->> 'expect_updated_at')::timestamptz THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM09',
                    MESSAGE = 'This record changed while you were editing it; reload and try again.';
            END IF;
            IF tbl = 'crm_memberships' AND vals ? 'role' AND old_j ->> 'role' = 'admin'
               AND vals ->> 'role' <> 'admin'
               AND NOT EXISTS (
                   SELECT 1 FROM crm_memberships
                   WHERE workspace_id = ws AND role = 'admin' AND id <> target
               ) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = 'A workspace needs at least one admin.';
            END IF;

            IF kind = 'delete' THEN
                EXECUTE format('DELETE FROM %I t WHERE t.id = $1 AND t.%I = $2 '
                               'RETURNING to_jsonb(t.*)', tbl, scope)
                INTO row_j USING target, ws;
            ELSE
                IF kind = 'update' AND vals = '{}' THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
                END IF;
                IF kind IN ('soft_delete', 'restore') THEN
                    IF NOT 'deleted_at' = ANY (cols) THEN
                        RAISE EXCEPTION USING ERRCODE = 'CRM22',
                            MESSAGE = 'This kind of record cannot be deleted.';
                    END IF;
                    vals := jsonb_build_object(
                        'deleted_at', CASE WHEN kind = 'soft_delete' THEN to_jsonb(now()) END,
                        'deleted_by', CASE WHEN kind = 'soft_delete' THEN to_jsonb(who) END,
                        'deleted_group',
                        CASE WHEN kind = 'soft_delete' THEN op -> 'deleted_group' END);
                END IF;
                IF 'updated_by' = ANY (cols) AND NOT vals ? 'updated_by' THEN
                    vals := vals || jsonb_build_object('updated_by', who);
                END IF;
                SELECT string_agg(format('%1$I = r.%1$I', key), ', ') INTO setlist
                FROM jsonb_object_keys(vals) AS key;
                EXECUTE format('UPDATE %1$I t SET %2$s FROM jsonb_populate_record(NULL::%1$I, $1) r '
                               'WHERE t.id = $2 AND t.%3$I = $3 RETURNING to_jsonb(t.*)',
                               tbl, setlist, scope)
                INTO row_j USING vals, target, ws;
            END IF;
        END IF;

        IF op ? 'ref' AND row_j IS NOT NULL THEN
            refs := refs || jsonb_build_object(op ->> 'ref', row_j -> 'id');
        END IF;
        results := results || jsonb_build_array(jsonb_build_object(
            'ref', op -> 'ref', 'id', coalesce(row_j -> 'id', op -> 'id'), 'row', row_j));
        PERFORM set_config('crm.action', '', true);
    END LOOP;

    -- FR-CRM-TASK-01, as amended by 008 FR-TASK-CHG-01: a task created
    -- through the CRM links to at least one CRM record, when the caller asks
    -- for the rule. Checked after every op, since its links are later ops of
    -- the same call; a project link does not count.
    IF p_task_needs_link AND cardinality(tasks) > 0 THEN
        EXECUTE 'SELECT bool_and(EXISTS (SELECT 1 FROM task_links l WHERE l.task_id = t '
                'AND l.record_type <> ''tva_project'')) '
                'FROM unnest($1::uuid[]) AS t'
        INTO linked USING tasks;
        IF linked IS NOT TRUE THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A task must be linked to at least one record.';
        END IF;
    END IF;

    RETURN results;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. crm_check_refs (027's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_check_refs()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    r      jsonb := to_jsonb(NEW);
    rtype  text := coalesce(r ->> 'record_type',
                            CASE r ->> 'target_type' WHEN 'view' THEN 'view' END);
    rid    uuid := coalesce(r ->> 'record_id', r ->> 'target_id')::uuid;
    target text;
    found  boolean;
    col    text;
BEGIN
    -- 048: a TVA project link has no workspace; its foreign key checks it.
    IF rtype = 'tva_project' THEN
        RETURN NEW;
    END IF;
    IF rid IS NOT NULL AND rtype IS NOT NULL THEN
        target := CASE rtype
            WHEN 'company' THEN 'crm_companies'
            WHEN 'person' THEN 'crm_people'
            WHEN 'opportunity' THEN 'crm_opportunities'
            WHEN 'task' THEN 'tasks'
            WHEN 'note' THEN 'crm_notes'
            WHEN 'campaign' THEN 'crm_campaigns'
            WHEN 'view' THEN 'crm_saved_views'
        END;
        IF target IS NULL OR to_regclass(format('public.%I', target)) IS NULL THEN
            found := false;
        ELSE
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 AND workspace_id = $2)',
                           target)
            INTO found USING rid, (r ->> 'workspace_id')::uuid;
        END IF;
        IF NOT found THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04',
                MESSAGE = format('No such %s in this workspace.', coalesce(rtype, 'record'));
        END IF;
    END IF;

    -- TG_ARGV is NULL, not empty, when the trigger has no arguments.
    FOREACH col IN ARRAY coalesce(TG_ARGV, '{}') LOOP
        IF EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(coalesce(r -> col, '[]')) AS e(option_id)
            WHERE NOT EXISTS (
                SELECT 1 FROM crm_select_options o
                WHERE o.id = e.option_id::uuid AND o.workspace_id = (r ->> 'workspace_id')::uuid
            )
        ) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such option in this workspace.';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. crm_deal_transition (033's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_deal_transition(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    opp_id      uuid := (p_plan ->> 'opportunity_id')::uuid;
    pa          jsonb := CASE WHEN jsonb_typeof(p_plan -> 'project') = 'object'
                              THEN p_plan -> 'project' END;
    act         text := p_plan -> 'project' ->> 'action';
    opp         crm_opportunities%ROWTYPE;
    comp        crm_companies%ROWTYPE;
    proj        projects%ROWTYPE;
    has_proj    boolean := false;
    target_proj projects%ROWTYPE;
    vals        jsonb;
    cols        text[];
    setlist     text;
    opp_j       jsonb;
    comp_j      jsonb;
    proj_j      jsonb;
    t           jsonb;
    l           jsonb;
    ph          record;
    m           jsonb;
    a           record;
    new_task    uuid;
    new_note    uuid;
    category    uuid;
    created     jsonb := '[]';
    closed_n    integer := 0;
    released_n  integer := 0;
    restored_n  integer := 0;
    close_ids   uuid[];
    target      text;
    hit         boolean;
    before_p    jsonb;
    prob        smallint;
    tva_id      uuid;
    adopt       boolean := false;
    old_m       project_milestones%ROWTYPE;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system', 'import', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF pa IS NOT NULL AND coalesce(act, '') NOT IN (
        'create', 'link', 'sync_probability', 'confirm', 'archive_release',
        'unarchive_unrelease', 'retentative'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That is not a TVA project action.';
    END IF;
    -- 3. Only the TVA-linked workspace has a TVA side (FR-CRM-WS-05).
    IF pa IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM crm_workspaces WHERE id = ws AND tva_linked
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'This workspace is not connected to TVA projects.';
    END IF;
    -- 5, 6. Linking a project, and revenue or milestones, are a staff TVA
    -- manager's or admin's alone.
    adopt := act = 'link' OR (act = 'confirm' AND pa ? 'project_id');
    IF adopt AND NOT crm_tva_manager(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Ask a TVA manager to link it.';
    END IF;
    IF act = 'confirm' AND (pa ? 'revenue' OR pa ? 'milestones')
       AND NOT crm_tva_manager(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Only a TVA manager or admin can set revenue and milestones.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- 1. The deal and its own company, locked in the actor's workspace.
    SELECT * INTO opp FROM crm_opportunities
    WHERE id = opp_id AND workspace_id = ws AND deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such opportunity.';
    END IF;
    IF p_plan ->> 'expect_stage' IS NOT NULL AND opp.stage <> p_plan ->> 'expect_stage' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'This deal moved while you were editing it; reload and try again.';
    END IF;
    IF p_plan ->> 'expect_updated_at' IS NOT NULL
       AND opp.updated_at IS DISTINCT FROM (p_plan ->> 'expect_updated_at')::timestamptz THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'This record changed while you were editing it; reload and try again.';
    END IF;

    SELECT * INTO comp FROM crm_companies
    WHERE id = opp.company_id AND workspace_id = ws AND deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END IF;
    IF jsonb_typeof(p_plan -> 'company') = 'object' THEN
        IF (p_plan -> 'company' ->> 'id')::uuid IS DISTINCT FROM comp.id THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
        END IF;
        IF p_plan -> 'company' ->> 'expect_updated_at' IS NOT NULL
           AND comp.updated_at IS DISTINCT FROM
               (p_plan -> 'company' ->> 'expect_updated_at')::timestamptz THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM09',
                MESSAGE = 'The company changed meanwhile; reload and try again.';
        END IF;
    END IF;

    IF jsonb_typeof(p_plan -> 'expect_siblings') = 'object' AND (
        (SELECT count(*) FROM crm_opportunities
         WHERE workspace_id = ws AND company_id = comp.id AND id <> opp.id
           AND deleted_at IS NULL
           AND stage IN ('discovery', 'qualification', 'proposal', 'negotiation'))
            IS DISTINCT FROM (p_plan -> 'expect_siblings' ->> 'open')::integer
        OR EXISTS (
            SELECT 1 FROM crm_opportunities
            WHERE workspace_id = ws AND company_id = comp.id AND id <> opp.id
              AND deleted_at IS NULL AND stage = 'closed_won')
            IS DISTINCT FROM (p_plan -> 'expect_siblings' ->> 'won')::boolean
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'The company''s deals changed meanwhile; reload and try again.';
    END IF;

    -- 4. The deal's own project, never one the plan names.
    IF pa IS NOT NULL THEN
        SELECT * INTO proj FROM projects WHERE source_opportunity_id = opp.id FOR UPDATE;
        has_proj := FOUND;
        IF adopt THEN
            IF has_proj THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM09',
                    MESSAGE = 'This deal already has a TVA project.';
            END IF;
            SELECT * INTO target_proj FROM projects
            WHERE id = (pa ->> 'project_id')::uuid FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such project.';
            END IF;
            IF target_proj.source_opportunity_id IS NOT NULL OR target_proj.is_archived
               OR target_proj.status <> 'tentative' THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM09', MESSAGE = format(
                    '“%s” cannot be linked: only an unlinked, unarchived tentative project can.',
                    target_proj.name);
            END IF;
        END IF;
        IF act = 'retentative' AND has_proj THEN
            IF EXISTS (SELECT 1 FROM time_entries WHERE project_id = proj.id) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
                    '%s has time logged against it, so it cannot be made tentative.', proj.name);
            END IF;
            IF EXISTS (SELECT 1 FROM project_milestones
                       WHERE project_id = proj.id AND invoiced_on IS NOT NULL) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
                    '%s has invoiced milestones, so it cannot be made tentative.', proj.name);
            END IF;
        END IF;
    END IF;

    -- 2. Tasks to close: live tasks of this workspace, linked to this deal.
    SELECT coalesce(array_agg(value::uuid), '{}') INTO close_ids
    FROM jsonb_array_elements_text(coalesce(p_plan -> 'close_tasks', '[]'));
    IF EXISTS (
        SELECT 1 FROM unnest(close_ids) AS c(id)
        WHERE NOT EXISTS (
            SELECT 1 FROM tasks k
            JOIN task_links tl ON tl.task_id = k.id AND tl.workspace_id = ws
            WHERE k.id = c.id AND k.workspace_id = ws AND k.deleted_at IS NULL
              AND tl.record_type = 'opportunity' AND tl.record_id = opp.id
        )
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such task on this deal.';
    END IF;
    IF p_plan -> 'opportunity' ->> 'stage' = 'closed_lost' AND EXISTS (
        SELECT 1 FROM tasks k
        JOIN task_links tl ON tl.task_id = k.id AND tl.workspace_id = ws
        WHERE k.workspace_id = ws AND k.deleted_at IS NULL AND k.status IN ('open', 'waiting')
          AND tl.record_type = 'opportunity' AND tl.record_id = opp.id
          AND NOT k.id = ANY (close_ids)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'The deal''s tasks changed meanwhile; reload and try again.';
    END IF;

    -- Tasks to create: assignees are members, links are records of this
    -- workspace (crm_check_refs checks the links again on insert).
    FOR t IN SELECT value FROM jsonb_array_elements(coalesce(p_plan -> 'create_tasks', '[]')) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM crm_memberships
            WHERE workspace_id = ws AND user_id = (t ->> 'assignee_id')::uuid
        ) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04',
                MESSAGE = 'A task''s assignee must be a member of this workspace.';
        END IF;
        FOR l IN SELECT value FROM jsonb_array_elements(coalesce(t -> 'links', '[]')) LOOP
            target := CASE l ->> 'record_type'
                WHEN 'company' THEN 'crm_companies'
                WHEN 'person' THEN 'crm_people'
                WHEN 'opportunity' THEN 'crm_opportunities'
            END;
            IF target IS NULL THEN
                hit := false;
            ELSE
                EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 '
                               'AND workspace_id = $2 AND deleted_at IS NULL)', target)
                INTO hit USING (l ->> 'record_id')::uuid, ws;
            END IF;
            IF NOT hit THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM04',
                    MESSAGE = 'No such record in this workspace.';
            END IF;
        END LOOP;
    END LOOP;

    -- The deal. Only its own columns, never its identity, workspace or
    -- trash state; `stage_entered_at: "now"` is the database's clock.
    vals := coalesce(p_plan -> 'opportunity', '{}');
    SELECT array_agg(attname::text) INTO cols
    FROM pg_attribute
    WHERE attrelid = 'public.crm_opportunities'::regclass AND attnum > 0 AND NOT attisdropped;
    IF EXISTS (
        SELECT 1 FROM jsonb_object_keys(vals) AS k
        WHERE NOT k = ANY (cols)
           OR k IN ('id', 'workspace_id', 'created_at', 'created_by', 'updated_at',
                    'deleted_at', 'deleted_by', 'deleted_group', 'twenty_id')
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That cannot be set by a stage move.';
    END IF;
    IF vals ->> 'stage_entered_at' = 'now' THEN
        vals := vals || jsonb_build_object('stage_entered_at', now());
    END IF;
    vals := vals || jsonb_build_object('updated_by', who);
    IF jsonb_typeof(p_plan -> 'history') = 'object' THEN
        PERFORM set_config('crm.action', 'opportunity.stage_changed', true);
    END IF;
    SELECT string_agg(format('%1$I = r.%1$I', k), ', ') INTO setlist
    FROM jsonb_object_keys(vals) AS k;
    EXECUTE format('UPDATE crm_opportunities t SET %s '
                   'FROM jsonb_populate_record(NULL::crm_opportunities, $1) r '
                   'WHERE t.id = $2 AND t.workspace_id = $3 RETURNING to_jsonb(t.*)', setlist)
    INTO opp_j USING vals, opp.id, ws;
    PERFORM set_config('crm.action', '', true);

    -- FR-CRM-OPP-02, -PIPE-14: the move's own history row.
    IF jsonb_typeof(p_plan -> 'history') = 'object' THEN
        INSERT INTO crm_stage_history
            (workspace_id, opportunity_id, from_stage, to_stage, at, "by", actor_kind)
        VALUES (ws, opp.id, p_plan -> 'history' ->> 'from', p_plan -> 'history' ->> 'to',
                (opp_j ->> 'stage_entered_at')::timestamptz, who, p_actor ->> 'kind');
    END IF;

    -- FR-CRM-PIPE-04, -05: the company's lead stage and account type only.
    IF jsonb_typeof(p_plan -> 'company' -> 'changes') = 'object'
       AND p_plan -> 'company' -> 'changes' <> '{}' THEN
        IF EXISTS (
            SELECT 1 FROM jsonb_object_keys(p_plan -> 'company' -> 'changes') AS k
            WHERE k NOT IN ('lead_stage', 'account_type_id')
        ) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A stage move changes only a company''s lead stage and account type.';
        END IF;
        UPDATE crm_companies c
        SET lead_stage = coalesce(r.lead_stage, c.lead_stage),
            account_type_id = CASE WHEN p_plan -> 'company' -> 'changes' ? 'account_type_id'
                                   THEN r.account_type_id ELSE c.account_type_id END,
            updated_by = who
        FROM jsonb_populate_record(NULL::crm_companies, p_plan -> 'company' -> 'changes') r
        WHERE c.id = comp.id AND c.workspace_id = ws
        RETURNING to_jsonb(c.*) INTO comp_j;
    END IF;

    -- Automation 9: Closed Lost cancels the deal's open and waiting tasks,
    -- with a note (008 FR-TASK-CHG-04: Cancelled, not Done).
    IF cardinality(close_ids) > 0 THEN
        UPDATE tasks
        SET status = 'cancelled', completed_at = NULL, updated_by = who
        WHERE id = ANY (close_ids) AND workspace_id = ws AND status IN ('open', 'waiting');
        GET DIAGNOSTICS closed_n = ROW_COUNT;
    END IF;
    IF jsonb_typeof(p_plan -> 'close_note') = 'object' THEN
        INSERT INTO crm_notes (workspace_id, body, kind, created_by, updated_by)
        VALUES (ws, p_plan -> 'close_note' ->> 'body', 'note', who, who)
        RETURNING id INTO new_note;
        INSERT INTO crm_note_links (workspace_id, note_id, record_type, record_id, created_by)
        VALUES (ws, new_note, 'opportunity', opp.id, who);
    END IF;

    FOR t IN SELECT value FROM jsonb_array_elements(coalesce(p_plan -> 'create_tasks', '[]')) LOOP
        category := NULL;
        IF t ->> 'category_key' IS NOT NULL THEN
            SELECT id INTO category FROM crm_select_options
            WHERE workspace_id = ws AND list_key = 'task.category'
              AND system_key = t ->> 'category_key';
        END IF;
        INSERT INTO tasks (workspace_id, title, body, status, priority, source, assignee_id,
                           due_on, bucket, category_id, created_by, updated_by)
        VALUES (ws, t ->> 'title', t ->> 'body', 'open', 'normal', 'rule',
                (t ->> 'assignee_id')::uuid, (t ->> 'due_on')::date, t ->> 'bucket', category,
                who, who)
        RETURNING id INTO new_task;
        IF jsonb_array_length(coalesce(t -> 'links', '[]')) = 0 THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A task must be linked to at least one record.';
        END IF;
        INSERT INTO task_links (workspace_id, task_id, record_type, record_id, created_by)
        SELECT ws, new_task, x.link ->> 'record_type', (x.link ->> 'record_id')::uuid, who
        FROM jsonb_array_elements(t -> 'links') AS x(link);
        created := created || to_jsonb(new_task);
    END LOOP;

    -- -----------------------------------------------------------------------
    -- The TVA side (FR-CRM-TVA). Every row written here is a TVA row.
    -- -----------------------------------------------------------------------
    prob := (pa ->> 'probability')::smallint;

    IF act = 'create' AND NOT has_proj THEN
        proj := crm_tva_create_project(p_actor, opp.id, comp.id::text,
            pa || jsonb_build_object('client', comp.name), 'tentative');
        has_proj := true;

    ELSIF act = 'link' THEN
        before_p := jsonb_build_object('source_opportunity_id', NULL,
            'crm_company_id', target_proj.crm_company_id, 'client', target_proj.client,
            'probability', target_proj.probability);
        UPDATE projects
        SET source_opportunity_id = opp.id, crm_company_id = comp.id, client = comp.name,
            probability = coalesce(prob, probability)
        WHERE id = target_proj.id
        RETURNING * INTO proj;
        has_proj := true;
        PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text, before_p,
            jsonb_build_object('source_opportunity_id', proj.source_opportunity_id,
                'crm_company_id', proj.crm_company_id, 'client', proj.client,
                'probability', proj.probability));

    ELSIF act = 'confirm' THEN
        IF adopt THEN
            -- FR-CRM-TVA-10 with -05: a name clash at the handoff, resolved
            -- by adopting that project (re-checks 4 and 5 above).
            before_p := jsonb_build_object('source_opportunity_id', NULL,
                'crm_company_id', target_proj.crm_company_id, 'client', target_proj.client);
            UPDATE projects
            SET source_opportunity_id = opp.id, crm_company_id = comp.id, client = comp.name
            WHERE id = target_proj.id
            RETURNING * INTO proj;
            has_proj := true;
            PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text,
                before_p, jsonb_build_object('source_opportunity_id', proj.source_opportunity_id,
                    'crm_company_id', proj.crm_company_id, 'client', proj.client));
        END IF;
        IF NOT has_proj THEN
            -- FR-CRM-TVA-10: straight to Closed Won, created confirmed.
            proj := crm_tva_create_project(p_actor, opp.id, comp.id::text,
                pa || jsonb_build_object('client', comp.name), 'confirmed');
            has_proj := true;
        ELSE
            IF EXISTS (SELECT 1 FROM projects
                       WHERE id <> proj.id
                         AND lower(btrim(name)) = lower(btrim(coalesce(pa ->> 'name', proj.name)))) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM09', MESSAGE = format(
                    'A project called “%s” already exists.', btrim(pa ->> 'name'));
            END IF;
            before_p := jsonb_build_object('name', proj.name, 'category', proj.category,
                'status', proj.status, 'probability', proj.probability, 'lead_id', proj.lead_id);
            -- Qualified: `category` is also this function's variable.
            UPDATE projects p
            SET name = btrim(coalesce(pa ->> 'name', p.name)),
                category = coalesce(pa ->> 'category', p.category),
                status = 'confirmed',
                probability = coalesce(prob, p.probability),
                lead_id = (pa ->> 'lead_id')::uuid
            WHERE p.id = proj.id
            RETURNING p.* INTO proj;
            PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text, before_p,
                jsonb_build_object('name', proj.name, 'category', proj.category,
                    'status', proj.status, 'probability', proj.probability,
                    'lead_id', proj.lead_id));
        END IF;
        -- One phase per kind; the CRM never sets spillover (FR-CRM-TVA-07).
        FOR ph IN
            SELECT key AS phase, value AS span
            FROM jsonb_each(coalesce(pa -> 'phases', '{}'))
            WHERE jsonb_typeof(value) = 'object'
        LOOP
            IF ph.phase NOT IN ('pre', 'delivery', 'support') THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = 'The handoff sets only pre, delivery and support phases.';
            END IF;
            IF (ph.span ->> 'ends_on')::date < (ph.span ->> 'starts_on')::date THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22',
                    MESSAGE = 'A phase cannot end before it starts.';
            END IF;
            INSERT INTO project_phases (project_id, phase, starts_on, ends_on)
            VALUES (proj.id, ph.phase, (ph.span ->> 'starts_on')::date,
                    (ph.span ->> 'ends_on')::date)
            ON CONFLICT (project_id, phase)
            DO UPDATE SET starts_on = EXCLUDED.starts_on, ends_on = EXCLUDED.ends_on
            RETURNING id INTO tva_id;
            PERFORM crm_tva_audit(p_actor, 'project.phase_set', 'project_phases',
                tva_id::text, NULL,
                jsonb_build_object('project_id', proj.id, 'phase', ph.phase,
                    'starts_on', ph.span ->> 'starts_on', 'ends_on', ph.span ->> 'ends_on'));
        END LOOP;
        -- FR-CRM-TVA-08: revenue and milestones, in INR, from a manager only
        -- (re-check 6 above).
        IF pa ? 'revenue' THEN
            before_p := jsonb_build_object('revenue', proj.revenue);
            UPDATE projects SET revenue = (pa ->> 'revenue')::numeric WHERE id = proj.id
            RETURNING * INTO proj;
            PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text, before_p,
                jsonb_build_object('revenue', proj.revenue));
        END IF;
        -- The plan's milestones are the full set, matched by id: the
        -- pre-fill sent back unchanged adds nothing (TVA-07). Invoiced ones
        -- are never changed or removed.
        IF pa ? 'milestones' THEN
            FOR old_m IN
                SELECT * FROM project_milestones
                WHERE project_id = proj.id
                  AND id::text NOT IN (
                      SELECT v ->> 'id' FROM jsonb_array_elements(pa -> 'milestones') AS x(v)
                      WHERE v ->> 'id' IS NOT NULL)
                FOR UPDATE
            LOOP
                IF old_m.invoiced_on IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
                        '“%s” is invoiced, so the handoff cannot remove it.', old_m.name);
                END IF;
                DELETE FROM project_milestones WHERE id = old_m.id;
                PERFORM crm_tva_audit(p_actor, 'milestone.removed', 'project_milestones',
                    old_m.id::text,
                    jsonb_build_object('project_id', proj.id, 'name', old_m.name,
                        'due_on', old_m.due_on, 'amount', old_m.amount), NULL);
            END LOOP;
        END IF;
        FOR m IN SELECT value FROM jsonb_array_elements(coalesce(pa -> 'milestones', '[]')) LOOP
            IF m ->> 'id' IS NOT NULL THEN
                SELECT * INTO old_m FROM project_milestones
                WHERE id::text = m ->> 'id' AND project_id = proj.id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM04',
                        MESSAGE = 'No such milestone on this project.';
                END IF;
                IF (old_m.name, old_m.due_on, old_m.amount) IS NOT DISTINCT FROM
                   (btrim(m ->> 'name'), (m ->> 'due_on')::date, (m ->> 'amount')::numeric) THEN
                    CONTINUE;
                END IF;
                IF old_m.invoiced_on IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
                        '“%s” is invoiced, so the handoff cannot change it.', old_m.name);
                END IF;
                UPDATE project_milestones
                SET name = btrim(m ->> 'name'), due_on = (m ->> 'due_on')::date,
                    amount = (m ->> 'amount')::numeric
                WHERE id = old_m.id;
                PERFORM crm_tva_audit(p_actor, 'milestone.updated', 'project_milestones',
                    old_m.id::text,
                    jsonb_build_object('name', old_m.name, 'due_on', old_m.due_on,
                        'amount', old_m.amount),
                    jsonb_build_object('name', btrim(m ->> 'name'), 'due_on', m ->> 'due_on',
                        'amount', m ->> 'amount'));
                CONTINUE;
            END IF;
            INSERT INTO project_milestones (project_id, name, due_on, amount)
            VALUES (proj.id, btrim(m ->> 'name'), (m ->> 'due_on')::date,
                    (m ->> 'amount')::numeric)
            RETURNING id INTO tva_id;
            PERFORM crm_tva_audit(p_actor, 'milestone.added', 'project_milestones',
                tva_id::text, NULL,
                jsonb_build_object('project_id', proj.id, 'name', btrim(m ->> 'name'),
                    'due_on', m ->> 'due_on', 'amount', m ->> 'amount'));
        END LOOP;
        -- FR-CRM-TVA-09: the handoff on the deal's timeline.
        INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                               target_table, target_id, before, after)
        VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'opportunity.handoff',
                'crm_opportunities', opp.id::text, NULL,
                jsonb_build_object('project_id', proj.id, 'project_name', proj.name,
                    'lead_id', proj.lead_id, 'revenue_written', pa ? 'revenue'));

    ELSIF act = 'sync_probability' AND has_proj AND proj.status = 'tentative'
          AND prob IS DISTINCT FROM proj.probability THEN
        before_p := jsonb_build_object('probability', proj.probability);
        UPDATE projects SET probability = prob WHERE id = proj.id RETURNING * INTO proj;
        PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text, before_p,
            jsonb_build_object('probability', proj.probability));

    ELSIF act = 'archive_release' AND has_proj THEN
        -- FR-CRM-TVA-12, -13: re-check 4 made `proj` this deal's own project.
        IF NOT proj.is_archived THEN
            UPDATE projects
            SET is_archived = true, crm_archived_at = now(),
                probability = coalesce(prob, probability)
            WHERE id = proj.id
            RETURNING * INTO proj;
            PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text,
                jsonb_build_object('is_archived', false),
                jsonb_build_object('is_archived', true, 'crm_archived_at', proj.crm_archived_at));
        END IF;
        FOR a IN
            UPDATE allocations
            SET released_at = now(), released_by = who,
                release_reason = coalesce(nullif(btrim(pa ->> 'reason'), ''), 'Deal lost'),
                released_by_opportunity_id = opp.id
            WHERE project_id = proj.id AND released_at IS NULL
            RETURNING id, released_at, release_reason
        LOOP
            released_n := released_n + 1;
            PERFORM crm_tva_audit(p_actor, 'allocation.released', 'allocations', a.id::text,
                jsonb_build_object('released_at', NULL),
                jsonb_build_object('released_at', a.released_at, 'release_reason',
                    a.release_reason, 'released_by_opportunity_id', opp.id));
        END LOOP;

    ELSIF act = 'unarchive_unrelease' AND has_proj THEN
        -- FR-CRM-TVA-14: only what this deal's loss did.
        IF proj.crm_archived_at IS NOT NULL THEN
            UPDATE projects
            SET is_archived = false, crm_archived_at = NULL,
                probability = CASE WHEN status = 'tentative' THEN coalesce(prob, probability)
                                   ELSE probability END
            WHERE id = proj.id
            RETURNING * INTO proj;
            PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text,
                jsonb_build_object('is_archived', true),
                jsonb_build_object('is_archived', false));
        ELSIF proj.status = 'tentative' AND prob IS DISTINCT FROM proj.probability THEN
            UPDATE projects SET probability = prob WHERE id = proj.id RETURNING * INTO proj;
        END IF;
        FOR a IN
            UPDATE allocations
            SET released_at = NULL, released_by = NULL, release_reason = NULL,
                released_by_opportunity_id = NULL
            WHERE released_by_opportunity_id = opp.id
            RETURNING id
        LOOP
            restored_n := restored_n + 1;
            PERFORM crm_tva_audit(p_actor, 'allocation.unreleased', 'allocations', a.id::text,
                NULL, jsonb_build_object('released_at', NULL));
        END LOOP;

    ELSIF act = 'retentative' AND has_proj THEN
        -- FR-CRM-PIPE-13: revenue and milestones are kept.
        before_p := jsonb_build_object('status', proj.status, 'probability', proj.probability);
        UPDATE projects
        SET status = 'tentative', probability = coalesce(prob, probability)
        WHERE id = proj.id
        RETURNING * INTO proj;
        PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text, before_p,
            jsonb_build_object('status', proj.status, 'probability', proj.probability));
    END IF;

    IF has_proj THEN
        proj_j := to_jsonb(proj);
    END IF;

    RETURN jsonb_build_object(
        'opportunity', opp_j,
        'company', comp_j,
        'project', proj_j,
        'tasks_created', created,
        'tasks_closed', closed_n,
        'note_id', new_note,
        'allocations_released', released_n,
        'allocations_restored', restored_n
    );
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_purge (042's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_purge(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws     uuid := (p_actor ->> 'workspace_id')::uuid;
    item   jsonb;
    tbl    text;
    target uuid;
    rtype  text;
    gone   uuid;
    purged integer := 0;
    unlinked uuid[];
    p      record;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    FOR item IN SELECT value FROM jsonb_array_elements(coalesce(p_plan -> 'items', '[]')) LOOP
        tbl := item ->> 'table';
        target := (item ->> 'id')::uuid;
        -- The record tables that exist so far, and the record type their
        -- links name.
        rtype := CASE tbl
            WHEN 'crm_companies' THEN 'company'
            WHEN 'crm_people' THEN 'person'
            WHEN 'crm_opportunities' THEN 'opportunity'
            WHEN 'tasks' THEN 'task'
            WHEN 'crm_notes' THEN 'note'
            WHEN 'crm_campaigns' THEN 'campaign'
            WHEN 'crm_drive_links' THEN NULL
            ELSE 'unknown'
        END;
        IF rtype = 'unknown' OR target IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That cannot be purged.';
        END IF;

        -- Only from the Trash of the actor's own workspace.
        EXECUTE format('SELECT id FROM %I WHERE id = $1 AND workspace_id = $2 '
                       'AND deleted_at IS NOT NULL FOR UPDATE', tbl)
        INTO gone USING target, ws;
        IF gone IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such record in the Trash.';
        END IF;

        -- FR-CRM-DEL-03: a deal with a TVA project is never deleted.
        IF tbl = 'crm_opportunities'
           AND EXISTS (SELECT 1 FROM projects WHERE source_opportunity_id = target) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This deal has a TVA project; mark it Closed Lost instead.';
        END IF;
        -- FR-CRM-DEL-02: the company's TVA projects keep their free-text
        -- client and lose the link, as a TVA row.
        IF tbl = 'crm_companies' THEN
            FOR p IN
                UPDATE projects SET crm_company_id = NULL
                WHERE crm_company_id = target
                RETURNING id
            LOOP
                PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', p.id::text,
                    jsonb_build_object('crm_company_id', target),
                    jsonb_build_object('crm_company_id', NULL));
            END LOOP;
        END IF;

        IF rtype IS NOT NULL THEN
            DELETE FROM crm_drive_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 008 FR-TASK-DATA-14: a task of this workspace left with no CRM
            -- link goes too, live or in the Trash; it never becomes personal.
            SELECT coalesce(array_agg(task_id), '{}') INTO unlinked
            FROM task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            DELETE FROM task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            DELETE FROM tasks k
            WHERE k.workspace_id = ws AND k.id = ANY (unlinked)
              AND NOT EXISTS (
                  SELECT 1 FROM task_links l
                  WHERE l.task_id = k.id AND l.record_type <> 'tva_project'
              );
            DELETE FROM crm_note_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 034: the favourites that pin it.
            DELETE FROM crm_favourites
            WHERE workspace_id = ws AND target_type = 'record' AND record_type = rtype
              AND target_id = target;
            -- 039: the stored emails' links to it (the messages stay, linked
            -- to whatever else they name).
            DELETE FROM crm_email_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
        END IF;
        -- 041: a membership naming both this and a person (or a company)
        -- stays with the other one, unless that one already has its own in
        -- the campaign; the rest go with the record (FK CASCADE). A
        -- campaign's members go with it.
        -- 042: when the shared membership is the Converted one and the other
        -- one's own is not, the other one's goes instead, so the campaign
        -- keeps its conversion (FR-CRM-CAMP-05).
        IF tbl = 'crm_companies' THEN
            DELETE FROM crm_campaign_members o
            USING crm_campaign_members m
            WHERE m.workspace_id = ws AND m.company_id = target AND m.person_id IS NOT NULL
              AND m.status = 'converted'
              AND o.campaign_id = m.campaign_id AND o.company_id IS NULL
              AND o.person_id = m.person_id AND o.status <> 'converted';
            DELETE FROM crm_campaign_members m
            WHERE m.workspace_id = ws AND m.company_id = target AND m.person_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM crm_campaign_members o
                          WHERE o.campaign_id = m.campaign_id AND o.company_id IS NULL
                            AND o.person_id = m.person_id);
            UPDATE crm_campaign_members SET company_id = NULL
            WHERE workspace_id = ws AND company_id = target AND person_id IS NOT NULL;
        ELSIF tbl = 'crm_people' THEN
            DELETE FROM crm_campaign_members o
            USING crm_campaign_members m
            WHERE m.workspace_id = ws AND m.person_id = target AND m.company_id IS NOT NULL
              AND m.status = 'converted'
              AND o.campaign_id = m.campaign_id AND o.person_id IS NULL
              AND o.company_id = m.company_id AND o.status <> 'converted';
            DELETE FROM crm_campaign_members m
            WHERE m.workspace_id = ws AND m.person_id = target AND m.company_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM crm_campaign_members o
                          WHERE o.campaign_id = m.campaign_id AND o.person_id IS NULL
                            AND o.company_id = m.company_id);
            UPDATE crm_campaign_members SET person_id = NULL
            WHERE workspace_id = ws AND person_id = target AND company_id IS NOT NULL;
        END IF;
        EXECUTE format('DELETE FROM %I WHERE id = $1 AND workspace_id = $2', tbl)
        USING target, ws;
        purged := purged + 1;
    END LOOP;

    RETURN jsonb_build_object('purged', purged);
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_merge_companies (042's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_merge_companies(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws         uuid := (p_actor ->> 'workspace_id')::uuid;
    who        uuid := (p_actor ->> 'user_id')::uuid;
    keep_id    uuid;
    gone_id    uuid;
    keep       crm_companies%ROWTYPE;
    gone       crm_companies%ROWTYPE;
    changes    jsonb := coalesce(p_plan -> 'survivor_changes', '{}');
    -- The fields a person chooses between (FR-CRM-DUP-03); everything else
    -- is the system's or moves on its own.
    choosable  text[] := ARRAY[
        'name', 'entities', 'lead_stage', 'account_type_id', 'relationship_status_id',
        'channel_ids', 'owner_id', 'next_touch_on', 'industry_id', 'institution_type_id',
        'size_id', 'country', 'city', 'address', 'website', 'linkedin', 'description',
        'source_id'
    ];
    cols       text[];
    setlist    text;
    wanted     text := nullif(btrim(coalesce(p_plan ->> 'primary_domain', '')), '');
    r          record;
    s          crm_campaign_members%ROWTYPE;
    n_people   integer := 0;
    n_deals    integer := 0;
    n_tasks    integer := 0;
    n_notes    integer := 0;
    n_links    integer := 0;
    n_domains  integer := 0;
    n_projects integer := 0;
    n_emails   integer := 0;
    n_members  integer := 0;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    BEGIN
        keep_id := (p_plan ->> 'survivor_id')::uuid;
        gone_id := (p_plan ->> 'merged_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END;
    IF keep_id IS NULL OR gone_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Name the two companies to merge.';
    END IF;
    IF keep_id = gone_id THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Pick two different companies to merge.';
    END IF;
    IF jsonb_typeof(changes) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'The chosen values are not valid.';
    END IF;

    -- Both companies, locked in id order (so two merges of the same pair
    -- queue instead of deadlocking), in the actor's workspace only.
    PERFORM 1 FROM crm_companies
    WHERE id IN (keep_id, gone_id) AND workspace_id = ws
    ORDER BY id
    FOR UPDATE;
    SELECT * INTO keep FROM crm_companies WHERE id = keep_id AND workspace_id = ws;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END IF;
    SELECT * INTO gone FROM crm_companies WHERE id = gone_id AND workspace_id = ws;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END IF;
    IF keep.merged_into_id IS NOT NULL OR gone.merged_into_id IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
            '“%s” was already merged into another company.',
            CASE WHEN keep.merged_into_id IS NOT NULL THEN keep.name ELSE gone.name END);
    END IF;
    IF keep.deleted_at IS NOT NULL OR gone.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = format(
            '“%s” is in the Trash; restore it before merging.',
            CASE WHEN keep.deleted_at IS NOT NULL THEN keep.name ELSE gone.name END);
    END IF;
    IF ((p_plan -> 'expect' ->> 'survivor_updated_at') IS NOT NULL
        AND keep.updated_at IS DISTINCT FROM
            (p_plan -> 'expect' ->> 'survivor_updated_at')::timestamptz)
       OR ((p_plan -> 'expect' ->> 'merged_updated_at') IS NOT NULL
        AND gone.updated_at IS DISTINCT FROM
            (p_plan -> 'expect' ->> 'merged_updated_at')::timestamptz) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'One of these companies changed meanwhile; reload and try again.';
    END IF;
    SELECT array_agg(k ORDER BY k) INTO cols FROM jsonb_object_keys(changes) AS k;
    IF cols IS NOT NULL AND NOT cols <@ choosable THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Only a company''s own fields can be chosen in a merge.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- 1. The chosen values.
    IF cols IS NOT NULL THEN
        SELECT string_agg(format('%1$I = r.%1$I', c), ', ') INTO setlist FROM unnest(cols) AS c;
        EXECUTE format('UPDATE crm_companies t SET %s, updated_by = $4 '
                       'FROM jsonb_populate_record(NULL::crm_companies, $1) r '
                       'WHERE t.id = $2 AND t.workspace_id = $3', setlist)
        USING changes, keep_id, ws, who;
    END IF;

    -- The projects linked to the merged company, before its deals move theirs.
    SELECT count(*) INTO n_projects FROM projects WHERE crm_company_id = gone_id;

    -- 2. People and deals, Trash included. A deal's project follows it to the
    --    survivor (033's crm_opportunities_project_company trigger).
    UPDATE crm_people SET company_id = keep_id, updated_by = who
    WHERE workspace_id = ws AND company_id = gone_id;
    GET DIAGNOSTICS n_people = ROW_COUNT;
    UPDATE crm_opportunities SET company_id = keep_id, updated_by = who
    WHERE workspace_id = ws AND company_id = gone_id;
    GET DIAGNOSTICS n_deals = ROW_COUNT;

    -- Task and note links: an addition on the survivor, a removal from the
    -- merged company; one already linked to both keeps the survivor's.
    FOR r IN
        SELECT * FROM task_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM task_links WHERE task_id = r.task_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO task_links (workspace_id, task_id, record_type, record_id,
                                        created_at, created_by)
            VALUES (ws, r.task_id, 'company', keep_id, r.created_at, r.created_by);
        END IF;
        DELETE FROM task_links WHERE id = r.id;
        n_tasks := n_tasks + 1;
    END LOOP;
    FOR r IN
        SELECT * FROM crm_note_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_note_links WHERE note_id = r.note_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_note_links (workspace_id, note_id, record_type, record_id,
                                        created_at, created_by)
            VALUES (ws, r.note_id, 'company', keep_id, r.created_at, r.created_by);
        END IF;
        DELETE FROM crm_note_links WHERE id = r.id;
        n_notes := n_notes + 1;
    END LOOP;

    -- 039: email links, as task and note links; a message linked to both
    -- keeps the survivor's. A log, not audited (data-model §3.22).
    FOR r IN
        SELECT * FROM crm_email_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_email_links WHERE message_id = r.message_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_email_links (workspace_id, message_id, record_type, record_id,
                                         created_at)
            VALUES (ws, r.message_id, 'company', keep_id, r.created_at);
        END IF;
        DELETE FROM crm_email_links WHERE id = r.id;
        n_emails := n_emails + 1;
    END LOOP;

    -- 041: campaign memberships, Trash included. One the survivor already
    -- has in the same campaign (with the same person, or none) keeps the
    -- survivor's; the merged company's goes, as a duplicate link does.
    -- 042: unless the merged company's is the Converted one and the
    -- survivor's is not: then the survivor's goes and the converted one
    -- moves, so the campaign keeps its conversion (FR-CRM-CAMP-05).
    FOR r IN
        SELECT * FROM crm_campaign_members
        WHERE workspace_id = ws AND company_id = gone_id
        ORDER BY id
    LOOP
        SELECT * INTO s FROM crm_campaign_members
        WHERE campaign_id = r.campaign_id AND company_id = keep_id
          AND person_id IS NOT DISTINCT FROM r.person_id;
        IF FOUND AND (s.status = 'converted' OR r.status <> 'converted') THEN
            DELETE FROM crm_campaign_members WHERE id = r.id;
        ELSE
            IF FOUND THEN
                DELETE FROM crm_campaign_members WHERE id = s.id;
            END IF;
            UPDATE crm_campaign_members SET company_id = keep_id, updated_by = who
            WHERE id = r.id;
        END IF;
        n_members := n_members + 1;
    END LOOP;

    -- Drive links are records of their own: they keep their id.
    UPDATE crm_drive_links SET record_id = keep_id, updated_by = who
    WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id;
    GET DIAGNOSTICS n_links = ROW_COUNT;

    -- 3. Domains, each unique in the workspace already, so none can clash.
    FOR r IN
        SELECT * FROM crm_company_domains
        WHERE workspace_id = ws AND company_id = gone_id
        ORDER BY domain
    LOOP
        DELETE FROM crm_company_domains WHERE id = r.id;
        INSERT INTO crm_company_domains (workspace_id, company_id, domain, is_primary,
                                         created_at, created_by)
        VALUES (ws, keep_id, r.domain, false, r.created_at, r.created_by);
        n_domains := n_domains + 1;
    END LOOP;
    IF wanted IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM crm_company_domains
                       WHERE company_id = keep_id AND domain = wanted) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'The primary domain must be one of the two companies'' domains.';
        END IF;
        -- The old primary steps down before the new one steps up (one
        -- primary per company).
        FOR r IN
            SELECT * FROM crm_company_domains
            WHERE company_id = keep_id AND is_primary AND domain <> wanted
        LOOP
            DELETE FROM crm_company_domains WHERE id = r.id;
            INSERT INTO crm_company_domains (workspace_id, company_id, domain, is_primary,
                                             created_at, created_by)
            VALUES (ws, keep_id, r.domain, false, r.created_at, r.created_by);
        END LOOP;
        FOR r IN
            SELECT * FROM crm_company_domains
            WHERE company_id = keep_id AND domain = wanted AND NOT is_primary
        LOOP
            DELETE FROM crm_company_domains WHERE id = r.id;
            INSERT INTO crm_company_domains (workspace_id, company_id, domain, is_primary,
                                             created_at, created_by)
            VALUES (ws, keep_id, r.domain, true, r.created_at, r.created_by);
        END LOOP;
    END IF;

    -- 4. TVA projects linked to the merged company directly: only the link
    --    changes (FR-CRM-DUP-04), as a TVA row.
    FOR r IN
        UPDATE projects SET crm_company_id = keep_id
        WHERE crm_company_id = gone_id
        RETURNING id
    LOOP
        PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', r.id::text,
            jsonb_build_object('crm_company_id', gone_id),
            jsonb_build_object('crm_company_id', keep_id));
    END LOOP;

    -- 5. The merged company resolves to the survivor and goes to the Trash.
    PERFORM set_config('crm.action', 'company.merged', true);
    UPDATE crm_companies
    SET merged_into_id = keep_id, deleted_at = now(), deleted_by = who,
        deleted_group = gen_random_uuid(), updated_by = who
    WHERE id = gone_id AND workspace_id = ws;
    PERFORM set_config('crm.action', '', true);

    -- The survivor's side of the audit: which company merged into it.
    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after)
    VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'company.merged',
            'crm_companies', keep_id::text, NULL,
            jsonb_build_object('merged_id', gone_id, 'merged_name', gone.name));

    SELECT * INTO keep FROM crm_companies WHERE id = keep_id;
    RETURN jsonb_build_object(
        'survivor', to_jsonb(keep),
        'moved', jsonb_build_object(
            'people', n_people, 'deals', n_deals, 'tasks', n_tasks, 'notes', n_notes,
            'links', n_links, 'domains', n_domains, 'projects', n_projects,
            'emails', n_emails, 'campaign_members', n_members));
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_search (041's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_search(
    p_workspace uuid, p_q text, p_limit integer, p_redact boolean
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path = public, extensions
SET pg_trgm.similarity_threshold = 0.3
SET pg_trgm.word_similarity_threshold = 0.6
SET cpu_operator_cost = 0.25
AS $$
DECLARE
    q      text := lower(btrim(coalesce(p_q, '')));
    lim    integer := least(greatest(coalesce(p_limit, 8), 1), 8);
    pat    text;
    hidden boolean := coalesce(p_redact, true);
    found  jsonb := '{}';
    part   jsonb;
BEGIN
    IF p_workspace IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A search needs a workspace.';
    END IF;
    IF char_length(q) < 2 THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Type at least two characters.';
    END IF;
    -- The query is matched literally: its LIKE wildcards are escaped.
    pat := replace(replace(replace(q, '\', '\\'), '%', '\%'), '_', '\_') || '%';

    SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.score DESC, h.updated_at DESC, h.id), '[]')
    INTO part
    FROM (
        SELECT c.id, c.name, c.lead_stage, c.owner_id, c.updated_at,
               (SELECT d.domain FROM crm_company_domains d
                WHERE d.company_id = c.id AND d.is_primary) AS domain,
               (greatest(
                   similarity(lower(c.name), q),
                   word_similarity(q, lower(c.name)),
                   coalesce((SELECT max(similarity(d.domain, q)) FROM crm_company_domains d
                             WHERE d.company_id = c.id), 0)
               ) + similarity(lower(c.name), q)) / 2 AS score
        FROM crm_companies c
        WHERE c.workspace_id = p_workspace AND c.deleted_at IS NULL AND c.merged_into_id IS NULL
          AND (lower(c.name) % q OR q <% lower(c.name) OR lower(c.name) LIKE pat ESCAPE '\'
               OR c.id IN (SELECT d.company_id FROM crm_company_domains d
                           WHERE d.workspace_id = p_workspace
                             AND (d.domain % q OR d.domain LIKE pat ESCAPE '\')))
        ORDER BY score DESC, c.updated_at DESC, c.id
        LIMIT lim
    ) h;
    found := found || jsonb_build_object('companies', part);

    SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.score DESC, h.updated_at DESC, h.id), '[]')
    INTO part
    FROM (
        SELECT p.id, p.first_name, p.last_name, p.primary_email, p.company_id,
               co.name AS company_name, p.updated_at,
               (greatest(
                   similarity(lower(p.first_name || ' ' || coalesce(p.last_name, '')), q),
                   word_similarity(q, lower(p.first_name || ' ' || coalesce(p.last_name, ''))),
                   similarity(coalesce(p.primary_email, ''), q)
               ) + similarity(lower(p.first_name || ' ' || coalesce(p.last_name, '')), q)) / 2
               AS score
        FROM crm_people p
        LEFT JOIN crm_companies co ON co.id = p.company_id
        WHERE p.workspace_id = p_workspace AND p.deleted_at IS NULL
          AND (lower(p.first_name || ' ' || coalesce(p.last_name, '')) % q
               OR q <% lower(p.first_name || ' ' || coalesce(p.last_name, ''))
               OR lower(p.first_name || ' ' || coalesce(p.last_name, '')) LIKE pat ESCAPE '\'
               OR p.primary_email % q OR p.primary_email LIKE pat ESCAPE '\'
               OR EXISTS (SELECT 1 FROM unnest(p.additional_emails) AS e
                          WHERE e LIKE pat ESCAPE '\'))
        ORDER BY score DESC, p.updated_at DESC, p.id
        LIMIT lim
    ) h;
    found := found || jsonb_build_object('people', part);

    -- A Viewer's hits never read an amount column (FR-CRM-ACC-11).
    SELECT coalesce(jsonb_agg(
               CASE WHEN hidden THEN to_jsonb(h) - 'amount' - 'amount_in_inr'
                    ELSE to_jsonb(h) END
               ORDER BY h.score DESC, h.updated_at DESC, h.id), '[]')
    INTO part
    FROM (
        SELECT o.id, o.name, o.stage, o.company_id, co.name AS company_name, o.owner_id,
               o.currency, o.updated_at,
               CASE WHEN hidden THEN NULL ELSE o.amount END AS amount,
               CASE WHEN hidden THEN NULL ELSE o.amount_in_inr END AS amount_in_inr,
               (greatest(similarity(lower(o.name), q), word_similarity(q, lower(o.name)))
                + similarity(lower(o.name), q)) / 2 AS score
        FROM crm_opportunities o
        LEFT JOIN crm_companies co ON co.id = o.company_id
        WHERE o.workspace_id = p_workspace AND o.deleted_at IS NULL
          AND (lower(o.name) % q OR q <% lower(o.name) OR lower(o.name) LIKE pat ESCAPE '\')
        ORDER BY score DESC, o.updated_at DESC, o.id
        LIMIT lim
    ) h;
    found := found || jsonb_build_object('opportunities', part);

    SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.score DESC, h.updated_at DESC, h.id), '[]')
    INTO part
    FROM (
        SELECT t.id, t.title, t.status, t.due_on, t.assignee_id, t.updated_at,
               (greatest(similarity(lower(t.title), q), word_similarity(q, lower(t.title)))
                + similarity(lower(t.title), q)) / 2 AS score
        FROM tasks t
        WHERE t.workspace_id = p_workspace AND t.deleted_at IS NULL
          AND (lower(t.title) % q OR q <% lower(t.title) OR lower(t.title) LIKE pat ESCAPE '\')
        ORDER BY score DESC, t.updated_at DESC, t.id
        LIMIT lim
    ) h;
    found := found || jsonb_build_object('tasks', part);

    SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.score DESC, h.updated_at DESC, h.id), '[]')
    INTO part
    FROM (
        SELECT n.id, n.kind, left(n.body, 200) AS snippet, n.created_at, n.updated_at,
               (greatest(similarity(lower(left(n.body, 200)), q),
                         word_similarity(q, lower(left(n.body, 200))))
                + similarity(lower(left(n.body, 200)), q)) / 2 AS score
        FROM crm_notes n
        WHERE n.workspace_id = p_workspace AND n.deleted_at IS NULL
          AND (lower(left(n.body, 200)) % q OR q <% lower(left(n.body, 200))
               OR lower(left(n.body, 200)) LIKE pat ESCAPE '\')
        ORDER BY score DESC, n.updated_at DESC, n.id
        LIMIT lim
    ) h;
    found := found || jsonb_build_object('notes', part);

    -- 041: campaigns by name. No budget is read, for anyone: a hit is the
    -- campaign's name, status and dates (FR-CRM-ACC-11).
    SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.score DESC, h.updated_at DESC, h.id), '[]')
    INTO part
    FROM (
        SELECT c.id, c.name, c.status_id, c.starts_on, c.ends_on, c.owner_id, c.updated_at,
               (greatest(similarity(lower(c.name), q), word_similarity(q, lower(c.name)))
                + similarity(lower(c.name), q)) / 2 AS score
        FROM crm_campaigns c
        WHERE c.workspace_id = p_workspace AND c.deleted_at IS NULL
          AND (lower(c.name) % q OR q <% lower(c.name) OR lower(c.name) LIKE pat ESCAPE '\')
        ORDER BY score DESC, c.updated_at DESC, c.id
        LIMIT lim
    ) h;
    found := found || jsonb_build_object('campaigns', part);

    RETURN found;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_query (041's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_query(
    p_workspace uuid, p_object text, p_spec jsonb, p_redact boolean
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path = public, extensions
AS $$
DECLARE
    tbl      text := CASE p_object
                        WHEN 'company' THEN 'crm_companies'
                        WHEN 'person' THEN 'crm_people'
                        WHEN 'opportunity' THEN 'crm_opportunities'
                        WHEN 'task' THEN 'tasks'
                        WHEN 'note' THEN 'crm_notes'
                        WHEN 'campaign' THEN 'crm_campaigns'
                        WHEN 'campaign_member' THEN 'crm_campaign_members'
                    END;
    spec     jsonb := coalesce(p_spec, '{}');
    linked   boolean;
    fields   jsonb;
    hidden   text[];
    today    date;
    tz       text;
    lim      integer;
    cond     text;
    rowcond  text;
    pat      text;
    f        jsonb;
    item     jsonb;
    typ      text;
    sexpr    text;
    gexpr    text;
    grank    text;
    keys     text[] := '{}';
    types    text[] := '{}';
    dirs     text[] := '{}';
    orders   text[] := '{}';
    after    jsonb;
    lit      text;
    eq       text;
    parts    text[] := '{}';
    fn       text;
    aggs     text[] := '{}';
    aggobj   text;
    r        record;
    n        integer := 0;
    rows_out jsonb := '[]';
    next_key jsonb;
    total    integer;
    groups   jsonb;
    totals   jsonb;
    i        integer;
BEGIN
    IF tbl IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'There is no such list.';
    END IF;
    SELECT w.tva_linked INTO linked FROM crm_workspaces w WHERE w.id = p_workspace;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such workspace.';
    END IF;

    -- The object's registry, less the fields of the other workspace kind.
    SELECT jsonb_object_agg(o.field, jsonb_build_object(
               'expr', crm_query_expr(p_object, o.column_sql),
               'column', o.column_sql,
               'type', o.type,
               'is_amount', o.is_amount,
               'filterable', o.filterable,
               'sortable', o.sortable,
               'groupable', o.groupable,
               'aggregatable', o.aggregatable)),
           array_agg(o.column_sql) FILTER (WHERE o.is_amount)
    INTO fields, hidden
    FROM crm_object_fields o
    WHERE o.object = p_object
      AND crm_query_expr(p_object, o.column_sql) IS NOT NULL
      AND (o.workspace_scope = 'all' OR (o.workspace_scope = 'tva_linked') = linked);
    fields := coalesce(fields, '{}');

    BEGIN
        today := coalesce((spec ->> 'today')::date, current_date);
        -- The person's time zone (Python sends it); an unknown one is refused.
        tz := coalesce(spec ->> 'tz', 'UTC');
        PERFORM now() AT TIME ZONE tz;
        lim := least(greatest(coalesce((spec ->> 'limit')::integer, 50), 1), 200);
    EXCEPTION WHEN data_exception THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That list request is not valid.';
    END;

    -- Where: the workspace's live rows (a merged company is gone), then the
    -- filters and the search.
    cond := format('t.workspace_id = %L::uuid AND t.deleted_at IS NULL', p_workspace);
    IF p_object = 'company' THEN
        cond := cond || ' AND t.merged_into_id IS NULL';
    END IF;
    IF jsonb_typeof(spec -> 'filters') = 'object' THEN
        cond := cond || ' AND ' || crm_query_cond(fields, spec -> 'filters', today, tz, p_redact, 1);
    END IF;
    IF coalesce(spec ->> 'q', '') <> '' THEN
        -- `q` arrives with LIKE's wildcards escaped (domain/crm/views.py).
        pat := '%' || lower(spec ->> 'q') || '%';
        cond := cond || ' AND ' || CASE p_object
            WHEN 'company' THEN format(
                '(lower(t.name) LIKE %1$L ESCAPE ''\'' OR EXISTS (SELECT 1 FROM '
                'crm_company_domains d WHERE d.workspace_id = t.workspace_id AND '
                'd.company_id = t.id AND d.domain LIKE %1$L ESCAPE ''\''))', pat)
            WHEN 'person' THEN format(
                '(lower(t.first_name || '' '' || coalesce(t.last_name, '''')) LIKE %1$L '
                'ESCAPE ''\'' OR lower(coalesce(t.primary_email, '''')) LIKE %1$L '
                'ESCAPE ''\'')', pat)
            WHEN 'opportunity' THEN format('lower(t.name) LIKE %L ESCAPE ''\''', pat)
            WHEN 'task' THEN format('lower(t.title) LIKE %L ESCAPE ''\''', pat)
            WHEN 'campaign' THEN format('lower(t.name) LIKE %L ESCAPE ''\''', pat)
            -- 041: a member by its company's or person's name.
            WHEN 'campaign_member' THEN format(
                '(EXISTS (SELECT 1 FROM crm_companies c WHERE c.workspace_id = '
                't.workspace_id AND c.id = t.company_id AND lower(c.name) LIKE %1$L '
                'ESCAPE ''\'') OR EXISTS (SELECT 1 FROM crm_people p WHERE '
                'p.workspace_id = t.workspace_id AND p.id = t.person_id AND '
                'lower(p.first_name || '' '' || coalesce(p.last_name, '''')) LIKE %1$L '
                'ESCAPE ''\''))', pat)
            ELSE format('lower(t.body) LIKE %L ESCAPE ''\''', pat)
        END;
    END IF;

    -- Columns and card fields only show; a Viewer may not ask for an amount.
    FOR item IN
        SELECT value FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(spec -> 'columns') = 'array' THEN spec -> 'columns'
                 ELSE '[]' END)
    LOOP
        f := fields -> coalesce(item ->> 'field', CASE jsonb_typeof(item)
                                                      WHEN 'string' THEN item #>> '{}' END);
        IF p_redact AND (f ->> 'is_amount')::boolean THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Viewers cannot filter, sort or total by amount.';
        END IF;
    END LOOP;

    -- Group: rows come grouped in the order of the groups list (a select's
    -- option order, a stage's place in the pipeline, a name), then by the
    -- key itself, so a page never splits a group out of order.
    IF coalesce(spec ->> 'group_by', '') <> '' THEN
        f := fields -> (spec ->> 'group_by');
        IF p_redact AND (f ->> 'is_amount')::boolean THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Viewers cannot filter, sort or total by amount.';
        END IF;
        IF f IS NULL OR NOT (f ->> 'groupable')::boolean THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That field cannot group a list.';
        END IF;
        gexpr := CASE WHEN f ->> 'type' IN ('select', 'person', 'relation')
                      THEN '(' || (f ->> 'expr') || ')::text'
                      ELSE '(' || (f ->> 'expr') || ')' END;
        grank := crm_query_rank(f ->> 'type', f ->> 'column', f ->> 'expr');
        keys := keys || grank || gexpr;
        types := types || crm_query_rank_type(f ->> 'type', f ->> 'column')
                       || CASE f ->> 'type' WHEN 'boolean' THEN 'boolean' ELSE 'text' END;
        dirs := dirs || '{asc,asc}'::text[];
    END IF;

    -- Sorts, newest first by default; then the id, so the order is total.
    FOR item IN
        SELECT value FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(spec -> 'sorts') = 'array'
                      AND jsonb_array_length(spec -> 'sorts') > 0 THEN spec -> 'sorts'
                 ELSE '[{"field": "updated_at", "dir": "desc"}]' END)
    LOOP
        f := fields -> (item ->> 'field');
        IF f IS NULL OR NOT (f ->> 'sortable')::boolean THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That field cannot be sorted on.';
        END IF;
        IF p_redact AND (f ->> 'is_amount')::boolean THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Viewers cannot filter, sort or total by amount.';
        END IF;
        IF coalesce(item ->> 'dir', 'asc') NOT IN ('asc', 'desc') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A sort is asc or desc.';
        END IF;
        -- In the list's own order: a stage by the pipeline, an owner by name.
        typ := f ->> 'type';
        sexpr := crm_query_rank(typ, f ->> 'column', f ->> 'expr');
        keys := keys || sexpr;
        types := types || crm_query_rank_type(typ, f ->> 'column');
        dirs := dirs || coalesce(item ->> 'dir', 'asc');
    END LOOP;
    IF cardinality(keys) > (CASE WHEN grank IS NULL THEN 10 ELSE 12 END) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Sort by at most ten fields.';
    END IF;
    FOR i IN 1 .. cardinality(keys) LOOP
        orders := orders || format('%s %s NULLS LAST', keys[i], upper(dirs[i]));
    END LOOP;
    orders := orders || 't.id ASC'::text;

    -- Keyset paging: `after` is the last row's keys and id. A row comes
    -- after it when the keys before one are equal and that one is further
    -- along (nulls last), or every key is equal and the id is greater.
    rowcond := cond;
    after := spec -> 'after';
    IF after IS NOT NULL AND after <> 'null'::jsonb THEN
        IF jsonb_typeof(after) <> 'array' OR jsonb_array_length(after) <> cardinality(keys) + 1
        THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'That page does not belong to this list.';
        END IF;
        eq := 'true';
        BEGIN
            FOR i IN 1 .. cardinality(keys) LOOP
                IF after -> (i - 1) = 'null'::jsonb THEN
                    lit := 'NULL';
                ELSE
                    lit := format('%L::%s', after ->> (i - 1), types[i]);
                    EXECUTE 'SELECT ' || lit;
                    parts := parts || format('(%s AND (%s %s %s OR %s IS NULL))', eq, keys[i],
                                             CASE dirs[i] WHEN 'asc' THEN '>' ELSE '<' END,
                                             lit, keys[i]);
                END IF;
                eq := eq || format(' AND %s IS NOT DISTINCT FROM %s', keys[i], lit);
            END LOOP;
            lit := format('%L::uuid', after ->> cardinality(keys));
            EXECUTE 'SELECT ' || lit;
        EXCEPTION WHEN data_exception THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'That page does not belong to this list.';
        END;
        parts := parts || format('(%s AND t.id > %s)', eq, lit);
        rowcond := cond || ' AND (' || array_to_string(parts, ' OR ') || ')';
    END IF;

    -- Totals: count, sum, average, minimum, maximum (FR-CRM-VIEW-08).
    FOR item IN
        SELECT value FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(spec -> 'aggregates') = 'array' THEN spec -> 'aggregates'
                 ELSE '[]' END)
    LOOP
        fn := item ->> 'fn';
        IF fn IS NULL OR fn NOT IN ('count', 'sum', 'avg', 'min', 'max') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A total is a count, sum, average, minimum or maximum.';
        END IF;
        IF item ->> 'field' IS NULL THEN
            IF fn <> 'count' THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Which field is totalled?';
            END IF;
            aggs := aggs || '''count'', count(*)'::text;
            CONTINUE;
        END IF;
        f := fields -> (item ->> 'field');
        IF p_redact AND (f ->> 'is_amount')::boolean THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Viewers cannot filter, sort or total by amount.';
        END IF;
        IF f IS NULL OR NOT (fn = 'count' OR (f ->> 'aggregatable')::boolean) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That field cannot be totalled.';
        END IF;
        aggs := aggs || format('%L, %s(%s)', fn || '_' || (item ->> 'field'), fn, f ->> 'expr');
    END LOOP;
    IF cardinality(aggs) > 20 THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Ask for at most twenty totals.';
    END IF;
    aggobj := CASE WHEN cardinality(aggs) > 0
                   THEN 'jsonb_build_object(' || array_to_string(aggs, ', ') || ')'
                   ELSE '''{}''::jsonb' END;

    -- The page: one row more than asked shows whether there is a next.
    FOR r IN EXECUTE format(
        'SELECT %s AS j, jsonb_build_array(%s) AS k FROM %I t WHERE %s ORDER BY %s LIMIT %s',
        CASE WHEN p_redact THEN format('to_jsonb(t.*) - %L::text[]', coalesce(hidden, '{}'))
             ELSE 'to_jsonb(t.*)' END,
        array_to_string(keys || 't.id'::text, ', '),
        tbl, rowcond, array_to_string(orders, ', '), lim + 1)
    LOOP
        n := n + 1;
        EXIT WHEN n > lim;
        rows_out := rows_out || jsonb_build_array(r.j);
        next_key := r.k;
    END LOOP;
    IF n <= lim THEN
        next_key := NULL;
    END IF;

    -- Counting stops past 10,000, so a large list stays fast (plan §5.8).
    EXECUTE format('SELECT count(*) FROM (SELECT 1 FROM %I t WHERE %s LIMIT 10001) c', tbl, cond)
    INTO total;

    IF cardinality(aggs) > 0 THEN
        EXECUTE format('SELECT %s FROM %I t WHERE %s', aggobj, tbl, cond) INTO totals;
    END IF;
    IF gexpr IS NOT NULL THEN
        EXECUTE format(
            'SELECT coalesce(jsonb_agg(jsonb_build_object(''key'', g.k, ''count'', g.n, '
            '''aggregates'', g.a) ORDER BY g.r NULLS LAST, g.k NULLS LAST), ''[]'') FROM '
            '(SELECT %s AS k, %s AS r, count(*) AS n, %s AS a FROM %I t WHERE %s '
            'GROUP BY 1, 2) g',
            gexpr, grank, aggobj, tbl, cond)
        INTO groups;
    END IF;

    RETURN jsonb_build_object(
        'rows', rows_out,
        'total', CASE WHEN total > 10000 THEN NULL ELSE total END,
        'groups', groups,
        'aggregates', totals,
        'next', next_key
    );
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_set_flags (038's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_set_flags(
    p_workspace uuid, p_table text, p_rows jsonb, p_at timestamptz
)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    changed integer;
BEGIN
    IF p_table NOT IN ('crm_companies', 'crm_people', 'crm_opportunities', 'tasks') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That table has no flags.';
    END IF;
    IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Flags come as a list.';
    END IF;
    EXECUTE format(
        'SELECT count(*) FROM %I t
         JOIN jsonb_to_recordset($1) AS r(id uuid, flag_level text, flags jsonb) ON r.id = t.id
         WHERE t.workspace_id = $2
           AND (t.flag_level, t.flags) IS DISTINCT FROM (r.flag_level, r.flags)',
        p_table)
    INTO changed USING p_rows, p_workspace;
    EXECUTE format(
        'UPDATE %I t
         SET flag_level = r.flag_level, flags = r.flags, flags_computed_at = $3
         FROM jsonb_to_recordset($1) AS r(id uuid, flag_level text, flags jsonb)
         WHERE t.workspace_id = $2 AND t.id = r.id',
        p_table)
    USING p_rows, p_workspace, p_at;
    RETURN changed;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_query_rank (041's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_query_rank(p_type text, p_column_sql text, p_expr text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, extensions
AS $$
BEGIN
    RETURN CASE
        WHEN p_type = 'select' THEN format(
            '(SELECT o.position FROM crm_select_options o WHERE o.workspace_id = '
            't.workspace_id AND o.id = %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql = 'lead_stage' THEN format(
            'array_position(''{new,contacted,nurturing,qualified,deal_open,customer,partner,'
            'disqualified}''::text[], %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql IN ('stage', 'lost_from_stage') THEN format(
            'array_position(''{discovery,qualification,proposal,negotiation,closed_won,'
            'closed_lost}''::text[], %s)', p_expr)
        -- 041: a task's statuses, then a campaign member's. The two lists
        -- share the column name and no key, so one array orders both (008:
        -- the task statuses of FR-TASK-CHG-11).
        WHEN p_type = 'fixed_select' AND p_column_sql = 'status' THEN format(
            'array_position(''{open,waiting,done,cancelled,targeted,invited,registered,'
            'attended,engaged,meeting_booked,converted,not_interested,unsubscribed}''::text[], %s)',
            p_expr)
        -- 008: a task's priority, most urgent first (data-model §3.3).
        WHEN p_type = 'fixed_select' AND p_column_sql = 'priority' THEN format(
            'array_position(''{urgent,high,normal,low}''::text[], %s)', p_expr)
        WHEN p_type = 'fixed_select' AND p_column_sql = 'bucket' THEN format(
            'array_position(''{today,this_week,this_month,later}''::text[], %s)', p_expr)
        -- 038: none, amber, red, so red first is a descending sort.
        WHEN p_type = 'flag' THEN format(
            'array_position(''{none,amber,red}''::text[], %s)', p_expr)
        WHEN p_type = 'person' THEN format(
            '(SELECT lower(p.display_name) FROM profiles p WHERE p.id = %s)', p_expr)
        WHEN p_type = 'relation' AND p_column_sql = 'company_id' THEN format(
            '(SELECT lower(c.name) FROM crm_companies c WHERE c.workspace_id = '
            't.workspace_id AND c.id = %s)', p_expr)
        WHEN p_type = 'relation' AND p_column_sql IN ('point_of_contact_id', 'person_id') THEN format(
            '(SELECT lower(btrim(c.first_name || '' '' || coalesce(c.last_name, ''''))) FROM '
            'crm_people c WHERE c.workspace_id = t.workspace_id AND c.id = %s)', p_expr)
        -- 041: a member's campaign and converted deal, by name.
        WHEN p_type = 'relation' AND p_column_sql = 'campaign_id' THEN format(
            '(SELECT lower(c.name) FROM crm_campaigns c WHERE c.workspace_id = '
            't.workspace_id AND c.id = %s)', p_expr)
        WHEN p_type = 'relation' AND p_column_sql = 'converted_opportunity_id' THEN format(
            '(SELECT lower(o.name) FROM crm_opportunities o WHERE o.workspace_id = '
            't.workspace_id AND o.id = %s)', p_expr)
        WHEN p_type IN ('text', 'long_text') THEN 'lower(' || p_expr || ')'
        WHEN p_type = 'relation' THEN '(' || p_expr || ')::text'
        ELSE '(' || p_expr || ')'
    END;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_query_rank_type (038's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_query_rank_type(p_type text, p_column_sql text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public, extensions
AS $$
    SELECT CASE
        WHEN p_type = 'select' THEN 'integer'
        WHEN p_type = 'fixed_select'
             AND p_column_sql IN ('lead_stage', 'stage', 'lost_from_stage', 'status', 'bucket',
                                  'priority')
            THEN 'integer'
        WHEN p_type = 'flag' THEN 'integer'
        WHEN p_type IN ('number', 'currency') THEN 'numeric'
        WHEN p_type = 'date' THEN 'date'
        WHEN p_type = 'datetime' THEN 'timestamptz'
        WHEN p_type = 'boolean' THEN 'boolean'
        ELSE 'text'
    END;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_flag_facts (039's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_flag_facts(
    p_workspace uuid, p_deal_ids uuid[], p_company_ids uuid[], p_person_ids uuid[]
)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    WITH deals AS (
        SELECT o.id, o.created_at, o.stage_entered_at
        FROM crm_opportunities o
        WHERE o.workspace_id = p_workspace AND o.deleted_at IS NULL
          AND (p_deal_ids IS NULL OR o.id = ANY (p_deal_ids))
    ), note_at AS (
        SELECT l.record_id AS id,
               max(CASE WHEN n.kind <> 'note' AND n.occurred_on IS NOT NULL
                        THEN n.occurred_on::timestamptz ELSE n.created_at END) AS at
        FROM crm_note_links l
        JOIN crm_notes n ON n.workspace_id = l.workspace_id AND n.id = l.note_id
        WHERE l.workspace_id = p_workspace AND l.record_type = 'opportunity'
          AND n.deleted_at IS NULL AND l.record_id IN (SELECT id FROM deals)
        GROUP BY l.record_id
    ), task_at AS (
        SELECT l.record_id AS id, max(greatest(t.created_at, t.completed_at)) AS at
        FROM task_links l
        JOIN tasks t ON t.workspace_id = l.workspace_id AND t.id = l.task_id
        WHERE l.workspace_id = p_workspace AND l.record_type = 'opportunity'
          AND t.deleted_at IS NULL AND l.record_id IN (SELECT id FROM deals)
        GROUP BY l.record_id
    ), mail AS (
        SELECT l.record_type, l.record_id,
               max(m.sent_at) FILTER (WHERE m.direction = 'inbound') AS last_in,
               max(m.sent_at) FILTER (WHERE m.direction = 'outbound') AS last_out
        FROM crm_email_links l
        JOIN crm_email_messages m ON m.workspace_id = l.workspace_id AND m.id = l.message_id
        WHERE l.workspace_id = p_workspace AND NOT m.is_internal AND NOT m.is_auto
          AND ((l.record_type = 'opportunity'
                AND (p_deal_ids IS NULL OR l.record_id = ANY (p_deal_ids)))
            OR (l.record_type = 'company'
                AND (p_company_ids IS NULL OR l.record_id = ANY (p_company_ids)))
            OR (l.record_type = 'person'
                AND (p_person_ids IS NULL OR l.record_id = ANY (p_person_ids))))
        GROUP BY l.record_type, l.record_id
    )
    SELECT jsonb_build_object(
        'activity', coalesce((
            SELECT jsonb_object_agg(d.id, greatest(d.created_at, d.stage_entered_at, na.at, ta.at))
            FROM deals d
            LEFT JOIN note_at na ON na.id = d.id
            LEFT JOIN task_at ta ON ta.id = d.id
        ), '{}'::jsonb),
        'open_task_companies', coalesce((
            SELECT jsonb_agg(DISTINCT l.record_id)
            FROM task_links l
            JOIN tasks t ON t.workspace_id = l.workspace_id AND t.id = l.task_id
            WHERE l.workspace_id = p_workspace AND l.record_type = 'company'
              AND t.status IN ('open', 'waiting') AND t.deleted_at IS NULL
              AND (p_company_ids IS NULL OR l.record_id = ANY (p_company_ids))
        ), '[]'::jsonb),
        'emails', coalesce((
            SELECT jsonb_object_agg(per.record_type, per.records)
            FROM (
                SELECT record_type,
                       jsonb_object_agg(record_id,
                                        jsonb_build_object('in', last_in, 'out', last_out))
                           AS records
                FROM mail
                GROUP BY record_type
            ) per
        ), '{}'::jsonb)
    );
$$;

-- ---------------------------------------------------------------------------
-- 8. crm_audit_row (039's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_audit_row()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    -- NEW is unassigned on DELETE and OLD on INSERT, so read rows as jsonb.
    new_j    jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    old_j    jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    r        jsonb := coalesce(new_j, old_j);
    changed  text[];
    before_j jsonb;
    after_j  jsonb;
    actor    jsonb;
    act      text;
    noun     text;
BEGIN
    -- Skip first, so a skippable write never needs an actor.
    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(array_agg(n.key ORDER BY n.key), '{}')
        INTO changed
        FROM jsonb_each(new_j) AS n
        WHERE n.key NOT IN ('updated_at', 'updated_by')
          AND n.value IS DISTINCT FROM old_j -> n.key;
        -- A select option's position is not kanban order: reordering a list
        -- is an admin settings change and is audited (FR-CRM-LIST-01, AUD-01).
        -- 039: a mailbox's sync cursor is the sync's own bookkeeping, written
        -- every page; the settings an admin changes are still audited.
        IF changed <@ ARRAY['last_used_at']
           OR changed <@ ARRAY['flag_level', 'flags', 'flags_computed_at']
           OR (changed <@ ARRAY['position'] AND TG_TABLE_NAME <> 'crm_select_options')
           OR (TG_TABLE_NAME = 'crm_mailboxes'
               AND changed <@ ARRAY['history_id', 'page_token', 'backfilled_at',
                                    'last_synced_at']) THEN
            RETURN NULL;
        END IF;
        SELECT jsonb_object_agg(k, old_j -> k), jsonb_object_agg(k, new_j -> k)
        INTO before_j, after_j
        FROM unnest(changed) AS k;
    ELSIF TG_OP = 'INSERT' THEN
        after_j := new_j;
    ELSE
        before_j := old_j;
    END IF;

    actor := nullif(current_setting('crm.actor', true), '')::jsonb;
    -- Deleting a profile (or its auth user) cascades to its memberships and
    -- the note mentions naming it, its favourites and private views, and
    -- clears created_by on what it authored, with no actor set. Those referential actions are recorded as the
    -- system, not refused, so a TVA account can still be deleted. They are
    -- told apart by the profile being gone already: a direct write about a
    -- person who still exists is not one.
    IF actor IS NULL
       AND ((TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_memberships'
             AND NOT EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_note_mentions'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'member_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_favourites'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_saved_views'
                AND old_j ->> 'owner_id' IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'owner_id')::uuid))
            OR (TG_OP = 'UPDATE' AND changed <@ ARRAY['created_by']
                AND new_j ->> 'created_by' IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'created_by')::uuid))
            -- 008: a task's Waiting on person or assigner, cleared because
            -- that profile was deleted (ON DELETE SET NULL), and an inbox
            -- item removed with its owner (ON DELETE CASCADE).
            OR (TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'tasks'
                AND changed <@ ARRAY['waiting_on_id', 'assigned_by']
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(changed) AS c
                    WHERE new_j ->> c IS NOT NULL
                       OR EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> c)::uuid)))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'inbox_items'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'owner_id')::uuid))) THEN
        actor := '{"kind": "system", "user_id": null, "channel": null}';
    END IF;
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A CRM change must be made through the CRM.';
    END IF;

    act := nullif(current_setting('crm.action', true), '');
    IF act IS NULL THEN
        noun := CASE TG_TABLE_NAME
            WHEN 'crm_companies' THEN 'company'
            WHEN 'crm_people' THEN 'person'
            WHEN 'crm_opportunities' THEN 'opportunity'
            WHEN 'crm_workspace_settings' THEN 'setting'
            WHEN 'crm_select_options' THEN 'option'
            WHEN 'crm_mailboxes' THEN 'mailbox'
            ELSE regexp_replace(regexp_replace(TG_TABLE_NAME, '^crm_', ''), 's$', '')
        END;
        act := noun || '.' || CASE
            WHEN TG_OP = 'INSERT' THEN 'created'
            WHEN TG_OP = 'DELETE' THEN 'purged'
            WHEN old_j ->> 'deleted_at' IS NULL AND new_j ->> 'deleted_at' IS NOT NULL
                THEN 'deleted'
            WHEN old_j ->> 'deleted_at' IS NOT NULL AND new_j ->> 'deleted_at' IS NULL
                THEN 'restored'
            ELSE 'updated'
        END;
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after)
    VALUES (
        (actor ->> 'user_id')::uuid,
        actor ->> 'kind',
        actor ->> 'channel',
        -- crm_workspaces has no workspace_id column: NEW.workspace_id would
        -- raise there, so the workspace is read from the row as jsonb.
        -- 008: a task link with no workspace (a TVA project link) takes its
        -- task's, null for a personal task or one already gone.
        coalesce(r ->> 'workspace_id',
                 CASE WHEN TG_TABLE_NAME = 'crm_workspaces' THEN r ->> 'id' END,
                 CASE WHEN TG_TABLE_NAME = 'task_links' THEN (
                     SELECT k.workspace_id::text FROM tasks k
                     WHERE k.id = (r ->> 'task_id')::uuid
                 ) END)::uuid,
        act,
        TG_TABLE_NAME,
        r ->> 'id',
        before_j,
        after_j
    );
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- Privileges
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_apply(jsonb, jsonb, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_check_refs() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_deal_transition(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_search(uuid, text, integer, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query(uuid, text, jsonb, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_set_flags(uuid, text, jsonb, timestamptz) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query_rank(text, text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query_rank_type(text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_flag_facts(uuid, uuid[], uuid[], uuid[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_apply(jsonb, jsonb, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_check_refs() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_deal_transition(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_search(uuid, text, integer, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query(uuid, text, jsonb, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_set_flags(uuid, text, jsonb, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query_rank(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query_rank_type(text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_flag_facts(uuid, uuid[], uuid[], uuid[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;
