-- 033_crm_lifecycle.sql
--
-- Spec 007 (sales CRM) FR-CRM-TVA-01 to -18, FR-CRM-PIPE-09, -13, -15,
-- FR-CRM-FX-05, FR-CRM-DEL-02, -03, FR-CRM-AUD-04, FR-CRM-WS-05,
-- FR-CRM-AUTO-01 (automation 10); owner decisions D5, D8, D14, D15; Q-21,
-- Q-33, Q-42, Q-43, Q-64, Q-65; data-model §5.4, §5.5, §5.6; plan §5.6.
--
-- The TVA side of a Labs deal, in the same transaction as the deal's move
-- (FR-CRM-PIPE-15). Python decides what happens (services/crm/tva.py,
-- domain/crm/handoff.py) and these functions write it, trusting no id the
-- plan names (Q-68).
--
-- 1. `crm_deal_transition` is replaced: 031's CRM side unchanged, plus a
--    `project` action. Before anything is written it re-checks:
--    3. a project action needs the actor's workspace to be `tva_linked`
--       (FR-CRM-WS-05), else CRM22;
--    4. the project is always the deal's own, `projects WHERE
--       source_opportunity_id = deal`, locked; a `project_id` in the plan is
--       read only as the target of `link`;
--    5. `link` needs the actor to be an active staff profile with TVA role
--       manager or admin (FR-CRM-TVA-05): linking lets a later Closed Lost
--       archive that project and release its people;
--    6. revenue or milestones in a `confirm` need the same (FR-CRM-TVA-08,
--       D8): Python leaves them out for anyone else, and this is the backstop
--       against a new caller.
--    The actions (data-model §5.5):
--    - `create`: a tentative project named after the deal, with its company
--      and probability, no revenue (FR-CRM-TVA-04). A deal that has one
--      already keeps it. A name clash is CRM09 naming the project (TVA-05).
--    - `link`: adopt an unlinked, tentative, unarchived project (TVA-05).
--    - `sync_probability`: the linked tentative project follows the deal's
--      probability (TVA-06).
--    - `confirm`: the handoff (TVA-07 to -10): create the project when there
--      is none, else update it, to confirmed with its name, category, lead
--      and phases (one per kind, never spillover), and revenue and milestones
--      in INR when the plan carries them. The deal gets a `handoff` timeline
--      entry.
--    - `archive_release`: archive the project and release every unreleased
--      allocation on it, recording when, by whom and why (TVA-12, D5).
--    - `unarchive_unrelease`: undo exactly that: unarchive only a project
--      the loss archived, un-release only the rows this deal released
--      (TVA-14). A row now overlapping other work is restored as it was.
--    - `retentative`: a reopened won deal's project goes back to tentative,
--      revenue and milestones kept; refused while it has logged time or an
--      invoiced milestone (FR-CRM-PIPE-13).
--    Each TVA write leaves a TVA audit row (`workspace_id` NULL, so it stays
--    off the CRM audit and on TVA's, FR-CRM-AUD-04) with `actor_label
--    'system'` naming the person who moved the deal (FR-CRM-TVA-03, D8).
--
-- 2. `crm_link_project` (FR-CRM-TVA-18): Link TVA project, by a Labs
--    workspace admin; linking an existing project also needs a staff TVA
--    manager or admin. An open deal at Proposal or later with no project is
--    linked to an unlinked, unarchived tentative project, or gets one
--    created; a Closed Won deal with no project is linked to an unlinked
--    confirmed project, whose status, revenue, phases and milestones are left
--    alone. Audited on both sides.
--
-- 3. `crm_purge`, third form (data-model §5.4): purging a company clears
--    `projects.crm_company_id` (TVA keeps the free-text client), and a deal
--    with a project is never purged (FR-CRM-DEL-03 blocks its soft delete
--    already, so this is a backstop).
--
-- Errors: CRM04 (404), CRM09 (409), CRM22 (422), each one sentence.
--
-- Reversible: restore 031's crm_deal_transition() and 030's crm_purge();
-- DROP FUNCTION crm_link_project(jsonb, jsonb), crm_tva_audit(jsonb, text,
-- text, text, jsonb, jsonb), crm_tva_manager(uuid), crm_tva_create_project(
-- jsonb, uuid, text, jsonb, text).

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- A TVA audit row written for a person by the CRM (FR-CRM-TVA-03, AUD-04).
CREATE OR REPLACE FUNCTION public.crm_tva_audit(
    p_actor jsonb, p_action text, p_table text, p_id text, p_before jsonb, p_after jsonb
) RETURNS void
LANGUAGE sql
SET search_path = public, extensions
AS $$
    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after)
    VALUES ((p_actor ->> 'user_id')::uuid, 'system', p_actor ->> 'channel', NULL, p_action,
            p_table, p_id, p_before, p_after);
$$;

-- Re-checks 5 and 6: an active staff account with TVA role manager or admin,
-- read from profiles, never from the plan.
CREATE OR REPLACE FUNCTION public.crm_tva_manager(p_user uuid) RETURNS boolean
LANGUAGE sql STABLE
SET search_path = public, extensions
AS $$
    SELECT EXISTS (
        SELECT 1 FROM profiles
        WHERE id = p_user AND is_active AND account_type = 'staff'
          AND role IN ('manager', 'admin')
    );
$$;

-- A project for a deal (FR-CRM-TVA-04, -10, -18): `p_spec` carries name,
-- client, category, probability and, for a confirmed one, lead_id. A name
-- that clashes with any project, archived included (008), is CRM09 naming it.
CREATE OR REPLACE FUNCTION public.crm_tva_create_project(
    p_actor jsonb, p_opp uuid, p_company text, p_spec jsonb, p_status text
) RETURNS projects
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    nm      text := btrim(coalesce(p_spec ->> 'name', ''));
    clash   text;
    made    projects%ROWTYPE;
BEGIN
    IF nm = '' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A project needs a name.';
    END IF;
    SELECT name INTO clash FROM projects WHERE lower(btrim(name)) = lower(nm) LIMIT 1;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = format('A project called “%s” already exists.', clash);
    END IF;
    BEGIN
        INSERT INTO projects (name, client, category, status, probability, lead_id,
                              crm_company_id, source_opportunity_id, created_by)
        VALUES (nm, nullif(btrim(coalesce(p_spec ->> 'client', '')), ''),
                coalesce(p_spec ->> 'category', 'client'), p_status,
                (p_spec ->> 'probability')::smallint, (p_spec ->> 'lead_id')::uuid,
                p_company::uuid, p_opp, (p_actor ->> 'user_id')::uuid)
        RETURNING * INTO made;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = format('A project called “%s” already exists.', nm);
    END;
    PERFORM crm_tva_audit(p_actor, 'project.created', 'projects', made.id::text, NULL,
        jsonb_build_object('name', made.name, 'client', made.client,
                           'category', made.category, 'status', made.status,
                           'probability', made.probability, 'lead_id', made.lead_id,
                           'source_opportunity_id', made.source_opportunity_id));
    RETURN made;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. crm_deal_transition — data-model §5.5, with the TVA branches
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
    IF act = 'link' AND NOT crm_tva_manager(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Ask a TVA manager to link it.';
    END IF;
    IF act = 'confirm' AND (pa ? 'revenue' OR jsonb_array_length(coalesce(pa -> 'milestones', '[]')) > 0)
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
        IF act = 'link' THEN
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
            SELECT 1 FROM crm_tasks k
            JOIN crm_task_links tl ON tl.task_id = k.id AND tl.workspace_id = ws
            WHERE k.id = c.id AND k.workspace_id = ws AND k.deleted_at IS NULL
              AND tl.record_type = 'opportunity' AND tl.record_id = opp.id
        )
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such task on this deal.';
    END IF;
    IF p_plan -> 'opportunity' ->> 'stage' = 'closed_lost' AND EXISTS (
        SELECT 1 FROM crm_tasks k
        JOIN crm_task_links tl ON tl.task_id = k.id AND tl.workspace_id = ws
        WHERE k.workspace_id = ws AND k.deleted_at IS NULL AND k.status <> 'done'
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

    -- Automation 9: Closed Lost closes the deal's open tasks, with a note.
    IF cardinality(close_ids) > 0 THEN
        UPDATE crm_tasks
        SET status = 'done', completed_at = now(), updated_by = who
        WHERE id = ANY (close_ids) AND workspace_id = ws AND status <> 'done';
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
        INSERT INTO crm_tasks (workspace_id, title, body, assignee_id, due_on, bucket,
                               category_id, created_by, updated_by)
        VALUES (ws, t ->> 'title', t ->> 'body', (t ->> 'assignee_id')::uuid,
                (t ->> 'due_on')::date, t ->> 'bucket', category, who, who)
        RETURNING id INTO new_task;
        IF jsonb_array_length(coalesce(t -> 'links', '[]')) = 0 THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'A task must be linked to at least one record.';
        END IF;
        INSERT INTO crm_task_links (workspace_id, task_id, record_type, record_id, created_by)
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
        FOR m IN SELECT value FROM jsonb_array_elements(coalesce(pa -> 'milestones', '[]')) LOOP
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
-- 2. crm_link_project — data-model §5.6, FR-CRM-TVA-18
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_link_project(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws       uuid := (p_actor ->> 'workspace_id')::uuid;
    who      uuid := (p_actor ->> 'user_id')::uuid;
    mode     text := p_plan ->> 'mode';
    opp      crm_opportunities%ROWTYPE;
    comp     crm_companies%ROWTYPE;
    proj     projects%ROWTYPE;
    before_p jsonb;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF coalesce(mode, '') NOT IN ('link', 'create') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Link or create a project.';
    END IF;
    -- 3. The TVA-linked workspace only.
    IF NOT EXISTS (SELECT 1 FROM crm_workspaces WHERE id = ws AND tva_linked) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'This workspace is not connected to TVA projects.';
    END IF;
    -- A Labs workspace admin; linking an existing project, also a staff TVA
    -- manager or admin (re-check 5).
    IF NOT EXISTS (SELECT 1 FROM crm_memberships
                   WHERE workspace_id = ws AND user_id = who AND role = 'admin') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Only a workspace admin can link a TVA project.';
    END IF;
    IF mode = 'link' AND NOT crm_tva_manager(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Ask a TVA manager to link it.';
    END IF;
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- 1. The deal, locked in the actor's workspace, and its company.
    SELECT * INTO opp FROM crm_opportunities
    WHERE id = (p_plan ->> 'opportunity_id')::uuid AND workspace_id = ws AND deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such opportunity.';
    END IF;
    IF p_plan ->> 'expect_updated_at' IS NOT NULL
       AND opp.updated_at IS DISTINCT FROM (p_plan ->> 'expect_updated_at')::timestamptz THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'This record changed while you were editing it; reload and try again.';
    END IF;
    SELECT * INTO comp FROM crm_companies
    WHERE id = opp.company_id AND workspace_id = ws AND deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such company.';
    END IF;
    IF EXISTS (SELECT 1 FROM projects WHERE source_opportunity_id = opp.id) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09', MESSAGE = 'This deal already has a TVA project.';
    END IF;
    IF opp.stage NOT IN ('proposal', 'negotiation', 'closed_won') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Only a deal at Proposal or later, or a won deal, gets a TVA project.';
    END IF;
    IF opp.stage = 'closed_won' AND mode = 'create' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A won deal is linked to its existing confirmed project.';
    END IF;

    IF mode = 'create' THEN
        proj := crm_tva_create_project(p_actor, opp.id, comp.id::text,
            jsonb_build_object(
                'name', coalesce(nullif(btrim(p_plan ->> 'name'), ''), opp.name),
                'client', comp.name,
                'category', coalesce(p_plan ->> 'category', opp.project_category, 'client'),
                'probability', opp.probability),
            'tentative');
    ELSE
        SELECT * INTO proj FROM projects WHERE id = (p_plan ->> 'project_id')::uuid FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such project.';
        END IF;
        IF proj.source_opportunity_id IS NOT NULL OR proj.is_archived
           OR proj.status <> (CASE WHEN opp.stage = 'closed_won' THEN 'confirmed'
                                   ELSE 'tentative' END) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM09', MESSAGE = format(
                '“%s” cannot be linked: it must be unlinked, unarchived and %s.', proj.name,
                CASE WHEN opp.stage = 'closed_won' THEN 'confirmed' ELSE 'tentative' END);
        END IF;
        before_p := jsonb_build_object('source_opportunity_id', NULL,
            'crm_company_id', proj.crm_company_id, 'client', proj.client);
        -- A won deal's project keeps its status, revenue, phases and
        -- milestones; an open deal's follows its probability (TVA-06).
        UPDATE projects
        SET source_opportunity_id = opp.id, crm_company_id = comp.id, client = comp.name,
            probability = CASE WHEN opp.stage = 'closed_won' THEN probability
                               ELSE opp.probability END
        WHERE id = proj.id
        RETURNING * INTO proj;
        PERFORM crm_tva_audit(p_actor, 'project.updated', 'projects', proj.id::text, before_p,
            jsonb_build_object('source_opportunity_id', proj.source_opportunity_id,
                'crm_company_id', proj.crm_company_id, 'client', proj.client));
    END IF;

    -- The deal's side of the audit (FR-CRM-TVA-18).
    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after)
    VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'opportunity.project_linked',
            'crm_opportunities', opp.id::text, NULL,
            jsonb_build_object('project_id', proj.id, 'project_name', proj.name, 'mode', mode));

    RETURN jsonb_build_object('opportunity', to_jsonb(opp), 'project', to_jsonb(proj));
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. crm_purge — data-model §5.4, third form (replaced in 034, 039)
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
        END IF;
        EXECUTE format('DELETE FROM %I WHERE id = $1 AND workspace_id = $2', tbl)
        USING target, ws;
        purged := purged + 1;
    END LOOP;

    RETURN jsonb_build_object('purged', purged);
END;
$$;

REVOKE ALL ON FUNCTION public.crm_tva_audit(jsonb, text, text, text, jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_tva_manager(uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_tva_create_project(jsonb, uuid, text, jsonb, text)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_deal_transition(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_link_project(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_tva_audit(jsonb, text, text, text, jsonb, jsonb)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_tva_manager(uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_tva_create_project(jsonb, uuid, text, jsonb, text)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_deal_transition(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_link_project(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
