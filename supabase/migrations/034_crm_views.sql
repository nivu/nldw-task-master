-- 034_crm_views.sql
--
-- Spec 007 (sales CRM) FR-CRM-VIEW-02 to -05, -07, -08, -10, -11,
-- FR-CRM-ACC-11, FR-CRM-UI-02, FR-CRM-DEL-01, FR-CRM-WS-02; Q-37, Q-67;
-- data-model §3.15, §3.16, §5.4, §5.8, §7.5; plan §4 CT-1, §5.5, §5.8.
--
-- Every list the CRM shows is answered by one query function, so filters,
-- sorts, groups, totals and paging happen on the server and stay fast at
-- 50,000 records (FR-CRM-VIEW-10).
--
-- 1. `crm_object_fields`: the column registry. One row per field a view may
--    name, with its type, whether it is an amount (FR-CRM-ACC-11), and
--    whether it may be filtered, sorted, grouped or totalled. Not workspace
--    data and changed by migration only (038 and 040 add rows), so it has no
--    audit trigger. Mirrored in `domain/crm/objects.VIEW_FIELDS`; a test
--    holds the two equal.
-- 2. `crm_saved_views`: saved views, private to their owner or shared with
--    the workspace (FR-CRM-VIEW-05, Q-37). A private view has an owner and a
--    shared one has none, so a shared view outlives whoever made it
--    (`created_by` still says who). One shared default per object. The seeded
--    views carry a `system_key` (FR-CRM-VIEW-11), so MCP and links can name
--    them; they can be hidden (`hidden`) but not deleted (contracts §2.7).
--    `config` is validated in Python (`domain/crm/views.validate_spec`).
-- 3. `crm_favourites`: a person's pinned views and records (FR-CRM-UI-02).
--    The target is polymorphic, so `crm_check_refs()` (027) checks that it is
--    in the favourite's own workspace.
-- 4. `crm_audit_row()` is replaced to record two more referential actions as
--    the system: a person's favourites and private views removed because
--    their profile was deleted (as 030 did for note mentions).
-- 5. `crm_query(workspace, object, spec, redact)`: one page of a list, with
--    its total, groups and totals (data-model §5.8). SQL is built only from
--    the registry's column expressions, `format('%I')` and `format('%L')`:
--    no caller text is ever concatenated into a statement (CT-1). Every field
--    must be in the registry with the flag its use needs, else CRM22. With
--    `redact` (a Viewer), any amount field anywhere in the spec is CRM22 and
--    the rows carry no amount column (FR-CRM-ACC-11, defence in depth: Python
--    refused first). Rows are always of the workspace, never deleted, and a
--    merged company never appears. Paging is keyset on (sort keys…, id), with
--    nulls last. Python resolves `is_me` and the calendar operators, and
--    escapes LIKE's wildcards in `q` and in `contains` values, before the
--    call. Its helpers `crm_query_expr()`, `crm_query_value()` and
--    `crm_query_cond()` build the pieces.
-- 6. `crm_purge()`, fourth form (data-model §5.4): purging a record also
--    removes the favourites that pin it.
-- 7. Seeds (data-model §7.5): the registry, and the seeded shared views of
--    FR-CRM-VIEW-11 in each workspace, written under the system actor.
--
-- Errors: CRM04 (404), CRM22 (422), each one sentence.
--
-- Reversible: restore 033's crm_purge() and 030's crm_audit_row();
-- DROP FUNCTION crm_query(uuid, text, jsonb, boolean),
-- crm_query_cond(jsonb, jsonb, date, boolean, integer),
-- crm_query_value(text, jsonb), crm_query_expr(text, text);
-- DROP TABLE crm_favourites, crm_saved_views, crm_object_fields. Their audit
-- rows stay: history is append-only (005).

-- ---------------------------------------------------------------------------
-- 1. crm_object_fields — data-model §3.15
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_object_fields (
    object          text NOT NULL CHECK (object IN (
                        'company', 'person', 'opportunity', 'task', 'note', 'campaign',
                        'campaign_member'
                    )),
    field           text NOT NULL CHECK (field ~ '^[a-z_]+$'),
    -- A column of the object's table, or one expression of the closed set
    -- `crm_query_expr()` knows (weighted_amount, full_name, primary_domain).
    column_sql      text NOT NULL CHECK (column_sql ~ '^[a-z_]+$'),
    type            text NOT NULL CHECK (type IN (
                        'text', 'long_text', 'number', 'currency', 'date', 'datetime',
                        'boolean', 'select', 'multi_select', 'fixed_select', 'person',
                        'relation', 'flag'
                    )),
    is_amount       boolean NOT NULL DEFAULT false,
    filterable      boolean NOT NULL DEFAULT true,
    sortable        boolean NOT NULL DEFAULT true,
    groupable       boolean NOT NULL DEFAULT false,
    aggregatable    boolean NOT NULL DEFAULT false,
    workspace_scope text NOT NULL DEFAULT 'all'
                    CHECK (workspace_scope IN ('all', 'tva_linked', 'not_tva_linked')),
    PRIMARY KEY (object, field)
);

-- ---------------------------------------------------------------------------
-- 2. crm_saved_views — data-model §3.16
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_saved_views (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    object       text NOT NULL CHECK (object IN (
                     'company', 'person', 'opportunity', 'task', 'note', 'campaign',
                     'campaign_member'
                 )),
    name         text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 80),
    icon         text CHECK (char_length(icon) <= 40),
    kind         text NOT NULL DEFAULT 'table' CHECK (kind IN ('table', 'kanban')),
    visibility   text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared')),
    -- A private view's owner; a shared view belongs to the workspace.
    owner_id     uuid REFERENCES profiles(id) ON DELETE CASCADE,
    config       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(config) = 'object'),
    system_key   text CHECK (system_key ~ '^[a-z_]+$'),
    is_default   boolean NOT NULL DEFAULT false,
    hidden       boolean NOT NULL DEFAULT false,
    position     integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_saved_views_owner CHECK ((visibility = 'private') = (owner_id IS NOT NULL)),
    CONSTRAINT crm_saved_views_default_shared CHECK (NOT is_default OR visibility = 'shared'),
    CONSTRAINT crm_saved_views_seeded_shared CHECK (system_key IS NULL OR visibility = 'shared'),
    CONSTRAINT crm_saved_views_hidden_seeded CHECK (NOT hidden OR system_key IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_saved_views_name
    ON crm_saved_views (workspace_id, object, lower(name),
                        coalesce(owner_id, '00000000-0000-0000-0000-000000000000'));
CREATE UNIQUE INDEX IF NOT EXISTS crm_saved_views_one_default
    ON crm_saved_views (workspace_id, object) WHERE is_default AND visibility = 'shared';
CREATE UNIQUE INDEX IF NOT EXISTS crm_saved_views_system_key
    ON crm_saved_views (workspace_id, system_key) WHERE system_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_saved_views_list
    ON crm_saved_views (workspace_id, object, position);

-- ---------------------------------------------------------------------------
-- 3. crm_favourites — data-model §3.16
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_favourites (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    user_id      uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    target_type  text NOT NULL CHECK (target_type IN ('view', 'record')),
    record_type  text CHECK (record_type IN (
                     'company', 'person', 'opportunity', 'task', 'note', 'campaign'
                 )),
    target_id    uuid NOT NULL,
    position     integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_favourites_record_type
        CHECK ((target_type = 'record') = (record_type IS NOT NULL)),
    CONSTRAINT crm_favourites_target UNIQUE (workspace_id, user_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS crm_favourites_person
    ON crm_favourites (workspace_id, user_id, position);
CREATE INDEX IF NOT EXISTS crm_favourites_pointing
    ON crm_favourites (workspace_id, target_type, target_id);

-- ---------------------------------------------------------------------------
-- 4. Triggers, RLS, privileges — data-model §2
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS crm_saved_views_touch ON crm_saved_views;
CREATE TRIGGER crm_saved_views_touch BEFORE UPDATE ON crm_saved_views
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- A favourite's target (data-model §5.14).
DROP TRIGGER IF EXISTS crm_favourites_check_refs ON crm_favourites;
CREATE TRIGGER crm_favourites_check_refs BEFORE INSERT OR UPDATE ON crm_favourites
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

DROP TRIGGER IF EXISTS crm_saved_views_crm_audit ON crm_saved_views;
CREATE TRIGGER crm_saved_views_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_saved_views
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_favourites_crm_audit ON crm_favourites;
CREATE TRIGGER crm_favourites_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_favourites
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_object_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_saved_views   ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_favourites    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_object_fields, crm_saved_views, crm_favourites FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. crm_audit_row — 030's function, plus the favourite and private-view
--    cascades
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
        IF changed <@ ARRAY['last_used_at']
           OR changed <@ ARRAY['flag_level', 'flags', 'flags_computed_at']
           OR (changed <@ ARRAY['position'] AND TG_TABLE_NAME <> 'crm_select_options') THEN
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
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'created_by')::uuid))) THEN
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
        coalesce(r ->> 'workspace_id',
                 CASE WHEN TG_TABLE_NAME = 'crm_workspaces' THEN r ->> 'id' END)::uuid,
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
-- 6. crm_query and its helpers — data-model §5.8
-- ---------------------------------------------------------------------------

-- A registry `column_sql` as SQL on the row alias `t`: a column, or one of a
-- closed set of derived values. Anything else is NULL, which crm_query
-- treats as an unknown field.
CREATE OR REPLACE FUNCTION public.crm_query_expr(p_object text, p_column_sql text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, extensions
AS $$
BEGIN
    RETURN CASE
        -- Spec §5.8: amount in INR × probability ÷ 100, never stored.
        WHEN p_object = 'opportunity' AND p_column_sql = 'weighted_amount'
            THEN 'round(t.amount_in_inr * t.probability / 100.0, 2)'
        WHEN p_object = 'person' AND p_column_sql = 'full_name'
            THEN 'btrim(t.first_name || '' '' || coalesce(t.last_name, ''''))'
        WHEN p_object = 'company' AND p_column_sql = 'primary_domain'
            THEN '(SELECT d.domain FROM crm_company_domains d WHERE d.workspace_id = '
                 't.workspace_id AND d.company_id = t.id AND d.is_primary)'
        WHEN p_column_sql ~ '^[a-z_]+$' THEN format('t.%I', p_column_sql)
    END;
END;
$$;

-- A condition's value as a literal of the field's type, refused (CRM22)
-- when it is not one, so a wrong value is the caller's mistake rather than
-- a failed query.
CREATE OR REPLACE FUNCTION public.crm_query_value(p_type text, p_value jsonb)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = public, extensions
AS $$
DECLARE
    v   text := p_value #>> '{}';
    lit text;
BEGIN
    IF p_value IS NULL OR jsonb_typeof(p_value) NOT IN ('string', 'number', 'boolean') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A condition needs a value.';
    END IF;
    lit := CASE p_type
        WHEN 'number' THEN format('%L::numeric', v)
        WHEN 'currency' THEN format('%L::numeric', v)
        WHEN 'date' THEN format('%L::date', v)
        WHEN 'datetime' THEN format('%L::timestamptz', v)
        WHEN 'boolean' THEN format('%L::boolean', v)
        ELSE format('%L::text', v)
    END;
    BEGIN
        EXECUTE 'SELECT ' || lit;
    EXCEPTION WHEN data_exception THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A condition''s value does not fit its field.';
    END;
    RETURN lit;
END;
$$;

-- One filter node as SQL: a group `{op: and|or, items: [...]}` or a
-- condition `{field, operator, value}`. `p_fields` is the registry of the
-- object, as crm_query built it.
CREATE OR REPLACE FUNCTION public.crm_query_cond(
    p_fields jsonb, p_node jsonb, p_today date, p_redact boolean, p_depth integer
)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = public, extensions
AS $$
DECLARE
    joiner text;
    parts  text[] := '{}';
    item   jsonb;
    f      jsonb;
    op     text := p_node ->> 'operator';
    typ    text;
    lhs    text;
    val    text;
    arr    text;
    span   numeric;
    days   integer;
BEGIN
    -- Python allows three levels per part; a saved view and the request's
    -- own filters are joined under one more.
    IF p_depth > 8 THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Filters are nested too deeply.';
    END IF;
    IF jsonb_typeof(p_node) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A filter is a condition or a group of them.';
    END IF;

    IF p_node ? 'items' THEN
        joiner := coalesce(p_node ->> 'op', 'and');
        IF joiner NOT IN ('and', 'or') OR jsonb_typeof(p_node -> 'items') IS DISTINCT FROM 'array'
        THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A filter group joins its conditions with and or or.';
        END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(p_node -> 'items') LOOP
            parts := parts || crm_query_cond(p_fields, item, p_today, p_redact, p_depth + 1);
        END LOOP;
        IF cardinality(parts) = 0 THEN
            RETURN 'true';
        END IF;
        RETURN '(' || array_to_string(parts, CASE joiner WHEN 'and' THEN ' AND ' ELSE ' OR ' END)
               || ')';
    END IF;

    f := p_fields -> (p_node ->> 'field');
    IF f IS NULL OR NOT (f ->> 'filterable')::boolean THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That field cannot be filtered on.';
    END IF;
    IF p_redact AND (f ->> 'is_amount')::boolean THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Viewers cannot filter, sort or total by amount.';
    END IF;
    typ := f ->> 'type';
    lhs := CASE
        WHEN typ IN ('select', 'person', 'relation') THEN '(' || (f ->> 'expr') || ')::text'
        WHEN typ = 'multi_select' THEN '(' || (f ->> 'expr') || ')::text[]'
        ELSE '(' || (f ->> 'expr') || ')'
    END;

    IF op IN ('is_empty', 'is_not_empty') THEN
        val := CASE
            WHEN typ = 'multi_select' THEN format('coalesce(cardinality(%s), 0) = 0', lhs)
            WHEN typ IN ('text', 'long_text') THEN format('coalesce(%s, '''') = ''''', lhs)
            ELSE format('%s IS NULL', lhs)
        END;
        RETURN CASE op WHEN 'is_empty' THEN val ELSE format('NOT (%s)', val) END;
    END IF;

    IF op IN ('any_of', 'none_of') THEN
        IF typ NOT IN ('select', 'fixed_select', 'person', 'relation', 'multi_select', 'flag')
           OR jsonb_typeof(p_node -> 'value') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Any of and none of take a list of options.';
        END IF;
        arr := format('%L::text[]',
                      ARRAY(SELECT jsonb_array_elements_text(p_node -> 'value')));
        val := CASE typ
            WHEN 'multi_select' THEN format('coalesce(%s && %s, false)', lhs, arr)
            ELSE format('coalesce(%s = ANY (%s), false)', lhs, arr)
        END;
        RETURN CASE op WHEN 'any_of' THEN val ELSE format('NOT %s', val) END;
    END IF;

    IF op IN ('within_last_days', 'within_next_days') THEN
        IF typ NOT IN ('date', 'datetime')
           OR jsonb_typeof(p_node -> 'value') IS DISTINCT FROM 'number' THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Within takes a date field and a number of days from 0 to 3650.';
        END IF;
        span := (p_node ->> 'value')::numeric;
        IF span NOT BETWEEN 0 AND 3650 OR span <> trunc(span) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'Within takes a date field and a number of days from 0 to 3650.';
        END IF;
        days := span::integer;
        RETURN CASE op
            WHEN 'within_last_days'
                THEN format('(%1$s >= %2$L::date AND %1$s < %3$L::date)',
                            lhs, p_today - days, p_today + 1)
            ELSE format('(%1$s >= %2$L::date AND %1$s < %3$L::date)',
                        lhs, p_today, p_today + days + 1)
        END;
    END IF;

    IF typ = 'multi_select' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That operator does not fit the field.';
    END IF;

    IF op IN ('contains', 'not_contains') THEN
        IF typ NOT IN ('text', 'long_text') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'That operator does not fit the field.';
        END IF;
        -- The value arrives with LIKE's wildcards escaped (domain/crm/views.py).
        val := format('coalesce(lower(%s) LIKE ''%%'' || lower(%s) || ''%%'' ESCAPE ''\'', false)',
                      lhs, crm_query_value(typ, p_node -> 'value'));
        RETURN CASE op WHEN 'contains' THEN val ELSE format('NOT %s', val) END;
    END IF;

    IF op IN ('is', 'is_not') THEN
        IF typ IN ('date', 'datetime') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'That operator does not fit the field.';
        END IF;
        val := crm_query_value(typ, p_node -> 'value');
        IF typ IN ('text', 'long_text') THEN
            lhs := format('lower(%s)', lhs);
            val := format('lower(%s)', val);
        END IF;
        RETURN CASE op
            WHEN 'is' THEN format('coalesce(%s = %s, false)', lhs, val)
            ELSE format('%s IS DISTINCT FROM %s', lhs, val)
        END;
    END IF;

    IF op IN ('before', 'after', 'on') AND typ IN ('date', 'datetime') THEN
        -- Whole days: a timestamp is on a day from its midnight to the next.
        val := crm_query_value('date', p_node -> 'value');
        RETURN CASE op
            WHEN 'before' THEN format('%s < %s', lhs, val)
            WHEN 'after' THEN format('%s >= (%s + 1)', lhs, val)
            ELSE format('(%1$s >= %2$s AND %1$s < (%2$s + 1))', lhs, val)
        END;
    END IF;

    IF op IN ('gt', 'gte', 'lt', 'lte') AND typ IN ('number', 'currency', 'date', 'datetime') THEN
        val := crm_query_value(CASE typ WHEN 'datetime' THEN 'date' ELSE typ END,
                               p_node -> 'value');
        RETURN format('%s %s %s', lhs,
                      CASE op WHEN 'gt' THEN '>' WHEN 'gte' THEN '>=' WHEN 'lt' THEN '<'
                              ELSE '<=' END,
                      val);
    END IF;

    RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'That operator does not fit the field.';
END;
$$;

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
                        WHEN 'task' THEN 'crm_tasks'
                        WHEN 'note' THEN 'crm_notes'
                    END;
    spec     jsonb := coalesce(p_spec, '{}');
    linked   boolean;
    fields   jsonb;
    hidden   text[];
    today    date;
    lim      integer;
    cond     text;
    rowcond  text;
    pat      text;
    f        jsonb;
    item     jsonb;
    typ      text;
    sexpr    text;
    gexpr    text;
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
        cond := cond || ' AND ' || crm_query_cond(fields, spec -> 'filters', today, p_redact, 1);
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

    -- Group: rows come grouped, so a page never splits a group out of order.
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
        keys := keys || gexpr;
        types := types || CASE f ->> 'type' WHEN 'boolean' THEN 'boolean' ELSE 'text' END;
        dirs := dirs || 'asc'::text;
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
        typ := f ->> 'type';
        sexpr := CASE
            WHEN typ IN ('text', 'long_text') THEN 'lower(' || (f ->> 'expr') || ')'
            WHEN typ IN ('select', 'person', 'relation') THEN '(' || (f ->> 'expr') || ')::text'
            ELSE '(' || (f ->> 'expr') || ')'
        END;
        keys := keys || sexpr;
        types := types || CASE
            WHEN typ IN ('number', 'currency') THEN 'numeric'
            WHEN typ = 'date' THEN 'date'
            WHEN typ = 'datetime' THEN 'timestamptz'
            WHEN typ = 'boolean' THEN 'boolean'
            ELSE 'text'
        END;
        dirs := dirs || coalesce(item ->> 'dir', 'asc');
    END LOOP;
    IF cardinality(keys) > 11 THEN
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
            '''aggregates'', g.a) ORDER BY g.k NULLS LAST), ''[]'') FROM (SELECT %s AS k, '
            'count(*) AS n, %s AS a FROM %I t WHERE %s GROUP BY 1) g',
            gexpr, aggobj, tbl, cond)
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
-- 7. crm_purge — data-model §5.4, fourth form (replaced in 039)
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
            WHEN 'crm_tasks' THEN 'task'
            WHEN 'crm_notes' THEN 'note'
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
            DELETE FROM crm_task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            DELETE FROM crm_note_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 034: the favourites that pin it.
            DELETE FROM crm_favourites
            WHERE workspace_id = ws AND target_type = 'record' AND record_type = rtype
              AND target_id = target;
        END IF;
        EXECUTE format('DELETE FROM %I WHERE id = $1 AND workspace_id = $2', tbl)
        USING target, ws;
        purged := purged + 1;
    END LOOP;

    RETURN jsonb_build_object('purged', purged);
END;
$$;

-- ---------------------------------------------------------------------------
-- 8. Grants — data-model §5.12
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_query_expr(text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query_value(text, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query_cond(jsonb, jsonb, date, boolean, integer)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query(uuid, text, jsonb, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_query_expr(text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query_value(text, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query_cond(jsonb, jsonb, date, boolean, integer)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query(uuid, text, jsonb, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;

-- ---------------------------------------------------------------------------
-- 9. Seeds — data-model §7.5
-- ---------------------------------------------------------------------------

-- The registry: one row per line, (object, field, column_sql, type,
-- is_amount, filterable, sortable, groupable, aggregatable, workspace_scope).
INSERT INTO crm_object_fields (object, field, column_sql, type, is_amount, filterable, sortable,
                               groupable, aggregatable, workspace_scope)
VALUES
    ('company', 'name', 'name', 'text', false, true, true, false, false, 'all'),
    ('company', 'domain', 'primary_domain', 'text', false, true, true, false, false, 'all'),
    ('company', 'entities', 'entities', 'multi_select', false, true, false, false, false, 'tva_linked'),
    ('company', 'lead_stage', 'lead_stage', 'fixed_select', false, true, true, true, false, 'all'),
    ('company', 'account_type_id', 'account_type_id', 'select', false, true, true, true, false, 'all'),
    ('company', 'relationship_status_id', 'relationship_status_id', 'select', false, true, true, true, false, 'all'),
    ('company', 'channel_ids', 'channel_ids', 'multi_select', false, true, false, false, false, 'all'),
    ('company', 'owner_id', 'owner_id', 'person', false, true, true, true, false, 'all'),
    ('company', 'next_touch_on', 'next_touch_on', 'date', false, true, true, false, false, 'all'),
    ('company', 'industry_id', 'industry_id', 'select', false, true, true, true, false, 'all'),
    ('company', 'institution_type_id', 'institution_type_id', 'select', false, true, true, true, false, 'not_tva_linked'),
    ('company', 'size_id', 'size_id', 'select', false, true, true, true, false, 'all'),
    ('company', 'country', 'country', 'text', false, true, true, false, false, 'all'),
    ('company', 'city', 'city', 'text', false, true, true, false, false, 'all'),
    ('company', 'description', 'description', 'long_text', false, true, false, false, false, 'all'),
    ('company', 'source_id', 'source_id', 'select', false, true, true, true, false, 'all'),
    ('company', 'last_inbound_at', 'last_inbound_at', 'datetime', false, true, true, false, false, 'all'),
    ('company', 'last_outbound_at', 'last_outbound_at', 'datetime', false, true, true, false, false, 'all'),
    ('company', 'last_contact_at', 'last_contact_at', 'datetime', false, true, true, false, false, 'all'),
    ('company', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('company', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all'),
    ('company', 'position', 'position', 'number', false, false, true, false, false, 'all'),
    ('person', 'name', 'full_name', 'text', false, true, true, false, false, 'all'),
    ('person', 'first_name', 'first_name', 'text', false, true, true, false, false, 'all'),
    ('person', 'last_name', 'last_name', 'text', false, true, true, false, false, 'all'),
    ('person', 'company_id', 'company_id', 'relation', false, true, true, true, false, 'all'),
    ('person', 'primary_email', 'primary_email', 'text', false, true, true, false, false, 'all'),
    ('person', 'job_title', 'job_title', 'text', false, true, true, false, false, 'all'),
    ('person', 'city', 'city', 'text', false, true, true, false, false, 'all'),
    ('person', 'owner_id', 'owner_id', 'person', false, true, true, true, false, 'all'),
    ('person', 'do_not_contact', 'do_not_contact', 'boolean', false, true, true, true, false, 'all'),
    ('person', 'last_inbound_at', 'last_inbound_at', 'datetime', false, true, true, false, false, 'all'),
    ('person', 'last_outbound_at', 'last_outbound_at', 'datetime', false, true, true, false, false, 'all'),
    ('person', 'last_contact_at', 'last_contact_at', 'datetime', false, true, true, false, false, 'all'),
    ('person', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('person', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all'),
    ('person', 'position', 'position', 'number', false, false, true, false, false, 'all'),
    ('opportunity', 'name', 'name', 'text', false, true, true, false, false, 'all'),
    ('opportunity', 'company_id', 'company_id', 'relation', false, true, true, true, false, 'all'),
    ('opportunity', 'point_of_contact_id', 'point_of_contact_id', 'relation', false, true, true, true, false, 'all'),
    ('opportunity', 'entity', 'entity', 'fixed_select', false, true, true, true, false, 'tva_linked'),
    ('opportunity', 'stage', 'stage', 'fixed_select', false, true, true, true, false, 'all'),
    ('opportunity', 'amount', 'amount', 'currency', true, true, true, false, true, 'all'),
    ('opportunity', 'currency', 'currency', 'fixed_select', false, true, true, true, false, 'all'),
    ('opportunity', 'amount_in_inr', 'amount_in_inr', 'currency', true, true, true, false, true, 'all'),
    ('opportunity', 'weighted_amount', 'weighted_amount', 'currency', true, true, true, false, true, 'all'),
    ('opportunity', 'fx_rate_used', 'fx_rate_used', 'number', true, true, true, false, false, 'all'),
    ('opportunity', 'won_fx_rate', 'won_fx_rate', 'number', true, true, true, false, false, 'all'),
    ('opportunity', 'probability', 'probability', 'number', false, true, true, false, true, 'all'),
    ('opportunity', 'expected_close_on', 'expected_close_on', 'date', false, true, true, false, false, 'all'),
    ('opportunity', 'next_step', 'next_step', 'text', false, true, true, false, false, 'all'),
    ('opportunity', 'next_step_on', 'next_step_on', 'date', false, true, true, false, false, 'all'),
    ('opportunity', 'owner_id', 'owner_id', 'person', false, true, true, true, false, 'all'),
    ('opportunity', 'channel_id', 'channel_id', 'select', false, true, true, true, false, 'all'),
    ('opportunity', 'deal_type_id', 'deal_type_id', 'select', false, true, true, true, false, 'all'),
    ('opportunity', 'project_category', 'project_category', 'fixed_select', false, true, true, true, false, 'tva_linked'),
    ('opportunity', 'engagement_model_id', 'engagement_model_id', 'select', false, true, true, true, false, 'all'),
    ('opportunity', 'programme_id', 'programme_id', 'select', false, true, true, true, false, 'not_tva_linked'),
    ('opportunity', 'proposal_sent_on', 'proposal_sent_on', 'date', false, true, true, false, false, 'all'),
    ('opportunity', 'lost_reason_id', 'lost_reason_id', 'select', false, true, true, true, false, 'all'),
    ('opportunity', 'lost_notes', 'lost_notes', 'long_text', false, true, false, false, false, 'all'),
    ('opportunity', 'lost_from_stage', 'lost_from_stage', 'fixed_select', false, true, true, true, false, 'all'),
    ('opportunity', 'stage_entered_at', 'stage_entered_at', 'datetime', false, true, true, false, false, 'all'),
    ('opportunity', 'won_on', 'won_on', 'date', false, true, true, false, false, 'all'),
    ('opportunity', 'lost_on', 'lost_on', 'date', false, true, true, false, false, 'all'),
    ('opportunity', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('opportunity', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all'),
    ('opportunity', 'position', 'position', 'number', false, false, true, false, false, 'all'),
    ('task', 'title', 'title', 'text', false, true, true, false, false, 'all'),
    ('task', 'body', 'body', 'long_text', false, true, false, false, false, 'all'),
    ('task', 'status', 'status', 'fixed_select', false, true, true, true, false, 'all'),
    ('task', 'due_on', 'due_on', 'date', false, true, true, false, false, 'all'),
    ('task', 'bucket', 'bucket', 'fixed_select', false, true, true, true, false, 'all'),
    ('task', 'category_id', 'category_id', 'select', false, true, true, true, false, 'all'),
    ('task', 'assignee_id', 'assignee_id', 'person', false, true, true, true, false, 'all'),
    ('task', 'completed_at', 'completed_at', 'datetime', false, true, true, false, false, 'all'),
    ('task', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('task', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all'),
    ('task', 'position', 'position', 'number', false, false, true, false, false, 'all'),
    ('note', 'body', 'body', 'long_text', false, true, false, false, false, 'all'),
    ('note', 'kind', 'kind', 'fixed_select', false, true, true, true, false, 'all'),
    ('note', 'occurred_on', 'occurred_on', 'date', false, true, true, false, false, 'all'),
    ('note', 'created_by', 'created_by', 'person', false, true, true, true, false, 'all'),
    ('note', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('note', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all')
ON CONFLICT (object, field) DO NOTHING;

-- FR-CRM-VIEW-11: the seeded shared views of each workspace, one per row as
-- (system_key, object, name, icon, kind, is_default, position, config).
-- `is_me` and the calendar operators are resolved per request in Python.
DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    INSERT INTO crm_saved_views
        (workspace_id, object, name, icon, kind, visibility, config, system_key, is_default,
         position)
    SELECT w.id, v.object, v.name, v.icon, v.kind, 'shared', v.config::jsonb, v.system_key,
           v.is_default, v.position
    FROM (VALUES
    ('companies_all', 'company', 'All companies', 'building-2', 'table', true, 1,
     '{"filters": {"op": "and", "items": []}, "sorts": [{"field": "name", "dir": "asc"}], "columns": [{"field": "name", "width": 240}, {"field": "domain", "width": 180}, {"field": "lead_stage", "width": 140}, {"field": "account_type_id", "width": 160}, {"field": "owner_id", "width": 140}, {"field": "next_touch_on", "width": 130}]}'),
    ('lead_funnel', 'company', 'Lead funnel', 'filter', 'kanban', false, 2,
     '{"filters": {"op": "and", "items": [{"field": "lead_stage", "operator": "none_of", "value": ["partner"]}]}, "sorts": [{"field": "position", "dir": "asc"}], "kanban_field": "lead_stage", "card_fields": ["owner_id", "next_touch_on"]}'),
    ('partners_channels', 'company', 'Partners and channels', 'handshake', 'table', false, 3,
     '{"filters": {"op": "and", "items": [{"field": "channel_ids", "operator": "is_not_empty"}]}, "sorts": [{"field": "name", "dir": "asc"}], "group_by": "account_type_id", "columns": [{"field": "name", "width": 240}, {"field": "channel_ids", "width": 200}, {"field": "relationship_status_id", "width": 160}, {"field": "owner_id", "width": 140}]}'),
    ('my_accounts', 'company', 'My accounts', 'user', 'table', false, 4,
     '{"filters": {"op": "and", "items": [{"field": "owner_id", "operator": "is_me"}]}, "sorts": [{"field": "next_touch_on", "dir": "asc"}], "columns": [{"field": "name", "width": 240}, {"field": "lead_stage", "width": 140}, {"field": "next_touch_on", "width": 130}, {"field": "last_contact_at", "width": 150}]}'),
    ('pipeline', 'opportunity', 'Pipeline', 'kanban', 'kanban', true, 1,
     '{"filters": {"op": "and", "items": [{"field": "stage", "operator": "any_of", "value": ["discovery", "qualification", "proposal", "negotiation"]}]}, "sorts": [{"field": "position", "dir": "asc"}], "kanban_field": "stage", "aggregates": [{"fn": "count"}, {"field": "amount_in_inr", "fn": "sum"}, {"field": "weighted_amount", "fn": "sum"}], "card_fields": ["company_id", "amount", "next_step_on", "owner_id"]}'),
    ('closing_this_month', 'opportunity', 'Closing this month', 'calendar', 'table', false, 2,
     '{"filters": {"op": "and", "items": [{"field": "stage", "operator": "any_of", "value": ["discovery", "qualification", "proposal", "negotiation"]}, {"field": "expected_close_on", "operator": "this_month"}]}, "sorts": [{"field": "expected_close_on", "dir": "asc"}], "columns": [{"field": "name", "width": 240}, {"field": "company_id", "width": 200}, {"field": "stage", "width": 140}, {"field": "amount", "width": 140}, {"field": "expected_close_on", "width": 140}, {"field": "owner_id", "width": 140}], "aggregates": [{"fn": "count"}, {"field": "amount_in_inr", "fn": "sum"}]}'),
    ('won_lost_quarter', 'opportunity', 'Won and lost this quarter', 'trophy', 'table', false, 3,
     '{"filters": {"op": "and", "items": [{"field": "stage", "operator": "any_of", "value": ["closed_won", "closed_lost"]}, {"op": "or", "items": [{"field": "won_on", "operator": "this_quarter"}, {"field": "lost_on", "operator": "this_quarter"}]}]}, "sorts": [{"field": "updated_at", "dir": "desc"}], "group_by": "stage", "columns": [{"field": "name", "width": 240}, {"field": "company_id", "width": 200}, {"field": "amount", "width": 140}, {"field": "won_on", "width": 120}, {"field": "lost_on", "width": 120}, {"field": "lost_reason_id", "width": 160}], "aggregates": [{"fn": "count"}, {"field": "amount_in_inr", "fn": "sum"}]}'),
    ('open_tasks_by_owner', 'task', 'Open tasks by owner', 'list-checks', 'table', true, 1,
     '{"filters": {"op": "and", "items": [{"field": "status", "operator": "is_not", "value": "done"}]}, "sorts": [{"field": "due_on", "dir": "asc"}], "group_by": "assignee_id", "columns": [{"field": "title", "width": 280}, {"field": "due_on", "width": 120}, {"field": "bucket", "width": 120}, {"field": "status", "width": 120}, {"field": "category_id", "width": 140}]}'),
    ('my_week', 'task', 'My week', 'calendar-days', 'kanban', false, 2,
     '{"filters": {"op": "and", "items": [{"field": "assignee_id", "operator": "is_me"}]}, "sorts": [{"field": "position", "dir": "asc"}], "kanban_field": "bucket", "card_fields": ["due_on", "status"]}')
    ) AS v(system_key, object, name, icon, kind, is_default, position, config)
    CROSS JOIN crm_workspaces w
    ON CONFLICT DO NOTHING;
END;
$$;
