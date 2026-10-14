-- 027_crm_write_functions.sql
--
-- Spec 007 (sales CRM) FR-CRM-AUD-06, FR-CRM-ACC-04, -10, -12, -14,
-- FR-CRM-WS-02, FR-CRM-CHG-02; Q-21, Q-44, Q-68, Q-75; owner decisions D3,
-- D11; plan §4 CT-1 and CT-5, §5.3, §5.13.
--
-- The CRM writes only through these functions. Each sets the actor (person,
-- kind, channel, workspace) as a transaction-local setting before it writes,
-- so 026's trigger records every change in the same transaction, and a
-- change that fails takes its audit row with it (FR-CRM-AUD-06). The rules
-- are decided in Python first (domain/crm/…); these functions write an
-- already-validated plan, re-checking only what a concurrent request could
-- change, or what isolation must never trust a caller with (Q-68).
--
-- 1. crm_apply(actor, ops): every ordinary create, update, soft delete,
--    restore, purge, upsert by twenty_id and link insert, as a list of
--    operations applied in order in one transaction. It refuses (CRM22) a
--    table that is not `crm_%` or has no audit trigger, and an unknown or
--    protected column. It stamps `workspace_id` from the actor on insert, and
--    every update, delete and restore matches `workspace_id` too, so a row of
--    another workspace answers "not found" (CRM04) whatever the caller sent
--    (FR-CRM-WS-02). `expect_updated_at` refuses a stale edit (CRM09). A
--    role change that would leave a workspace with no admin is refused under
--    a lock on the workspace's admin memberships, so two admins demoting each
--    other at once cannot both succeed.
--    SQL is built only with format('%I') identifiers checked against
--    pg_attribute, and values travel through jsonb_populate_record with
--    USING: no caller text is ever concatenated into a statement (CT-1).
-- 2. crm_check_refs(): the trigger for references that cannot be foreign
--    keys (polymorphic task, note, Drive and email links, favourites, and
--    uuid[] option arrays). Each creating migration attaches it; none of
--    026's tables needs it.
-- 3. crm_remove_membership(actor, membership): FR-CRM-ACC-12. Removing a
--    CRM-only person's last membership deactivates the account and revokes
--    its personal and OAuth tokens in the same transaction (Q-44).
-- 4. crm_invite(actor, plan): FR-CRM-ACC-04. Called after the sign-in
--    identity exists (CT-5): creates or reactivates the profile and adds the
--    membership in one transaction. A workspace admin may reactivate only an
--    account whose last membership was removed, never one a TVA admin
--    deactivated (Q-75).
-- 5. crm_convert_account(actor, user, to): FR-CRM-ACC-14, FR-CRM-CHG-02. The
--    staff → crm_only change, under a lock on the profile that
--    tva_staff_only() (025) waits for, so an allocation or lead assignment
--    racing it is either seen by the re-check or refused.
--
-- Errors: SQLSTATE CRM04 (not found, 404), CRM09 (changed meanwhile, 409),
-- CRM22 (a rule, 422), each with one sentence safe to show; the backend maps
-- them (services/crm/store.py).
--
-- EXECUTE is revoked from PUBLIC, anon and authenticated: PostgREST exposes
-- every function in `public`, and the anon key is public (data-model §5.12).
--
-- Reversible: DROP FUNCTION crm_convert_account(jsonb, uuid, text),
-- crm_invite(jsonb, jsonb), crm_remove_membership(jsonb, uuid),
-- crm_check_refs(), crm_apply(jsonb, jsonb), crm_is_tva_admin(uuid).

-- ---------------------------------------------------------------------------
-- Helper: is this person an active staff TVA admin?
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_is_tva_admin(p_user_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM profiles
        WHERE id = p_user_id AND role = 'admin' AND account_type = 'staff' AND is_active
    );
$$;

-- ---------------------------------------------------------------------------
-- 1. crm_apply — data-model §5.2
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_apply(p_actor jsonb, p_ops jsonb)
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
        IF tbl IS NULL OR tbl !~ '^crm_[a-z_]+$' OR to_regclass(format('public.%I', tbl)) IS NULL
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
        IF tbl = 'crm_memberships' AND kind = 'delete' THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Remove a member through the members page.';
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
            IF tbl = 'crm_tasks' AND kind = 'insert' THEN
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

    -- FR-CRM-TASK-01: a task links to at least one record. Checked after
    -- every op, since its links are later ops of the same call.
    IF cardinality(tasks) > 0 THEN
        EXECUTE 'SELECT bool_and(EXISTS (SELECT 1 FROM crm_task_links l WHERE l.task_id = t)) '
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
-- 2. crm_check_refs — data-model §5.14
--
-- For a link row: `record_type` (or a favourite's `target_type`) names the
-- target table, and `record_id` (or `target_id`) must be a row of it in the
-- row's own workspace. For a row with option arrays (TG_ARGV names the
-- columns), every element must be an option of the same workspace.
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
    IF rid IS NOT NULL AND rtype IS NOT NULL THEN
        target := CASE rtype
            WHEN 'company' THEN 'crm_companies'
            WHEN 'person' THEN 'crm_people'
            WHEN 'opportunity' THEN 'crm_opportunities'
            WHEN 'task' THEN 'crm_tasks'
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
-- 3. crm_remove_membership — data-model §5.3 (replaced in 037)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_remove_membership(p_actor jsonb, p_membership_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    m           crm_memberships%ROWTYPE;
    person      profiles%ROWTYPE;
    tokens_n    integer := 0;
    oauth_n     integer := 0;
    deactivated boolean := false;
BEGIN
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- Admins first, in id order, then the row: the same order crm_apply
    -- takes, so concurrent removals and demotions queue rather than deadlock.
    PERFORM 1 FROM crm_memberships
    WHERE workspace_id = ws AND role = 'admin' ORDER BY id FOR UPDATE;
    SELECT * INTO m FROM crm_memberships
    WHERE id = p_membership_id AND workspace_id = ws FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such member in this workspace.';
    END IF;
    IF m.role = 'admin' AND NOT EXISTS (
        SELECT 1 FROM crm_memberships
        WHERE workspace_id = ws AND role = 'admin' AND id <> m.id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A workspace needs at least one admin.';
    END IF;

    SELECT * INTO person FROM profiles WHERE id = m.user_id FOR UPDATE;
    IF (person.is_owner OR (person.role = 'admin' AND person.account_type = 'staff'))
       AND NOT crm_is_tva_admin(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Ask a TVA admin to change this membership.';
    END IF;

    PERFORM set_config('crm.action', 'membership.removed', true);
    DELETE FROM crm_memberships WHERE id = m.id;
    PERFORM set_config('crm.action', '', true);

    -- FR-CRM-ACC-12, Q-44: a CRM-only person with no workspace left has no
    -- reason to sign in. Staff keep their TVA account.
    IF person.account_type = 'crm_only' AND person.is_active
       AND NOT EXISTS (SELECT 1 FROM crm_memberships WHERE user_id = person.id) THEN
        UPDATE profiles
        SET is_active = false, deactivated_reason = 'crm_no_membership'
        WHERE id = person.id;
        UPDATE api_tokens SET revoked_at = now()
        WHERE user_id = person.id AND revoked_at IS NULL;
        GET DIAGNOSTICS tokens_n = ROW_COUNT;
        UPDATE oauth_refresh_tokens SET revoked_at = now()
        WHERE user_id = person.id AND revoked_at IS NULL;
        GET DIAGNOSTICS oauth_n = ROW_COUNT;
        deactivated := true;

        -- TVA rows (no workspace): the account and its tokens are TVA's.
        INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                               target_id, before, after)
        VALUES (who, 'system', p_actor ->> 'channel', 'user.deactivated', 'profiles',
                person.id::text, jsonb_build_object('is_active', true),
                jsonb_build_object('is_active', false,
                                   'deactivated_reason', 'crm_no_membership'));
        IF tokens_n + oauth_n > 0 THEN
            INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                                   target_id, after)
            VALUES (who, 'system', p_actor ->> 'channel', 'token.revoked', 'api_tokens',
                    person.id::text,
                    jsonb_build_object('personal_tokens', tokens_n, 'oauth_tokens', oauth_n));
        END IF;
    END IF;

    RETURN jsonb_build_object('deactivated', deactivated);
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_invite — data-model §5.13
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_invite(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    uid         uuid := (p_plan ->> 'user_id')::uuid;
    person      profiles%ROWTYPE;
    created     boolean := false;
    reactivated boolean := false;
    membership  uuid;
    inactive    constant text := 'This person''s portal account is inactive; ask a TVA admin.';
BEGIN
    PERFORM set_config('crm.actor', p_actor::text, true);

    IF coalesce((p_plan ->> 'create_profile')::boolean, false) THEN
        INSERT INTO profiles (id, email, display_name, role, account_type, logs_time, timezone)
        VALUES (uid, lower(p_plan ->> 'email'), p_plan ->> 'display_name', 'user', 'crm_only',
                false, coalesce(p_plan ->> 'timezone', 'Asia/Kolkata'));
        created := true;
        INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                               target_id, after)
        VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', 'user.created', 'profiles',
                uid::text, jsonb_build_object('account_type', 'crm_only',
                                              'email', lower(p_plan ->> 'email')));
    ELSE
        SELECT * INTO person FROM profiles WHERE id = uid FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such person.';
        END IF;
        IF NOT person.is_active THEN
            -- Q-75: a workspace admin may undo only the deactivation that
            -- losing the last membership caused; a staff account, or one a
            -- TVA admin switched off, stays off unless a TVA admin acts —
            -- and an inactive staff account is never reopened from the CRM.
            IF NOT coalesce((p_plan ->> 'reactivate')::boolean, false)
               OR person.account_type <> 'crm_only'
               OR (person.deactivated_reason IS DISTINCT FROM 'crm_no_membership'
                   AND NOT crm_is_tva_admin(who)) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = inactive;
            END IF;
            UPDATE profiles SET is_active = true, deactivated_reason = NULL WHERE id = uid;
            reactivated := true;
            INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                                   target_id, before, after)
            VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', 'user.reactivated',
                    'profiles', uid::text,
                    jsonb_build_object('is_active', false,
                                       'deactivated_reason', person.deactivated_reason),
                    jsonb_build_object('is_active', true));
        END IF;
    END IF;

    PERFORM set_config('crm.action', 'membership.invited', true);
    INSERT INTO crm_memberships (workspace_id, user_id, role, created_by, updated_by)
    VALUES (ws, uid, p_plan ->> 'role', who, who)
    RETURNING id INTO membership;
    PERFORM set_config('crm.action', '', true);

    RETURN jsonb_build_object('membership_id', membership, 'user_id', uid,
                              'created_profile', created, 'reactivated', reactivated);
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_convert_account — data-model §5.15
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_convert_account(p_actor jsonb, p_user_id uuid, p_to text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    who      uuid := (p_actor ->> 'user_id')::uuid;
    person   profiles%ROWTYPE;
    after_p  profiles%ROWTYPE;
    holds    boolean;
BEGIN
    IF p_to IS NULL OR p_to NOT IN ('staff', 'crm_only') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Unknown account type.';
    END IF;
    -- The lock tva_staff_only() waits for (025): an allocation, project lead
    -- or reporting line written meanwhile either committed before this point
    -- and is seen below, or waits and is then refused by that trigger.
    SELECT * INTO person FROM profiles WHERE id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such person.';
    END IF;

    IF p_to = 'crm_only' AND person.account_type <> 'crm_only' THEN
        IF person.is_owner THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'The owner''s account cannot be CRM-only.';
        END IF;
        IF EXISTS (SELECT 1 FROM profiles WHERE lead_id = person.id) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person is someone''s lead; give their reports another lead first.';
        END IF;
        IF EXISTS (SELECT 1 FROM projects WHERE lead_id = person.id) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person leads a project; give it another lead first.';
        END IF;
        -- Released allocations (028) do not count. The column is looked up so
        -- this function stays right before and after 028.
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM allocations WHERE user_id = $1'
                || CASE WHEN EXISTS (
                       SELECT 1 FROM pg_attribute
                       WHERE attrelid = 'public.allocations'::regclass
                         AND attname = 'released_at' AND NOT attisdropped)
                   THEN ' AND released_at IS NULL' ELSE '' END
                || ')'
        INTO holds USING person.id;
        IF holds THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person is allocated to a project; remove the allocation first.';
        END IF;
        UPDATE profiles
        SET account_type = 'crm_only', role = 'user', lead_id = NULL, logs_time = false,
            dashboard_access = false
        WHERE id = person.id
        RETURNING * INTO after_p;
    ELSIF p_to = 'staff' AND person.account_type <> 'staff' THEN
        UPDATE profiles SET account_type = 'staff' WHERE id = person.id
        RETURNING * INTO after_p;
    ELSE
        RETURN jsonb_build_object('id', person.id, 'account_type', person.account_type,
                                  'changed', false);
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table, target_id,
                           before, after)
    VALUES (who, coalesce(p_actor ->> 'kind', 'user'), p_actor ->> 'channel',
            'user.account_type', 'profiles', person.id::text,
            jsonb_build_object('account_type', person.account_type, 'role', person.role,
                               'lead_id', person.lead_id, 'logs_time', person.logs_time,
                               'dashboard_access', person.dashboard_access),
            jsonb_build_object('account_type', after_p.account_type, 'role', after_p.role,
                               'lead_id', after_p.lead_id, 'logs_time', after_p.logs_time,
                               'dashboard_access', after_p.dashboard_access));

    RETURN jsonb_build_object('id', after_p.id, 'account_type', after_p.account_type,
                              'role', after_p.role, 'changed', true);
END;
$$;

-- ---------------------------------------------------------------------------
-- Grants — data-model §5.12
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_is_tva_admin(uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_apply(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_check_refs() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_remove_membership(jsonb, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_invite(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_convert_account(jsonb, uuid, text)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.crm_is_tva_admin(uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_apply(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_check_refs() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_remove_membership(jsonb, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_invite(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_convert_account(jsonb, uuid, text) TO service_role;
