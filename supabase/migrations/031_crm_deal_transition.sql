-- 031_crm_deal_transition.sql
--
-- Spec 007 (sales CRM) FR-CRM-PIPE-04, -05, -09, -11 to -15, FR-CRM-OPP-02,
-- FR-CRM-AUTO-01 (automation 9), FR-CRM-WS-02; Q-21, Q-31, Q-33, Q-41,
-- Q-60, Q-64, Q-68; data-model §5.5; plan §5.3.
--
-- `crm_deal_transition(actor, plan)`: a stage move and every side effect it
-- has, in one transaction (FR-CRM-PIPE-15). The rules are decided in Python
-- (domain/crm/pipeline.py builds the plan); this function writes it, after
-- re-checking what a concurrent request could have changed and every id the
-- plan names, none of which it trusts (Q-68):
--
-- 1. The deal is locked `FOR UPDATE` in the actor's workspace, and so is its
--    company, found from the deal's own `company_id` (a company id in the
--    plan must equal it). Missing, trashed or in another workspace: CRM04.
--    The deal must still be at `expect_stage` and `expect_updated_at`, and
--    the company at its `expect_updated_at`, or nothing is written (CRM09).
--    A move to a closed stage carries `expect_siblings`, the company's other
--    open deals and whether one is won, from which the company's change was
--    worked out (FR-CRM-PIPE-05); they are counted again under the company's
--    lock, so two of its deals closed at once cannot both miss the last-open
--    rule (CRM09).
-- 2. Every task to close must be a live task of this workspace linked to
--    this deal; every task to create must have an assignee who is a member
--    of the workspace and link only to records of it. Else CRM04. A move to
--    Closed Lost must close every open task on the deal: one added since the
--    plan was made refuses the move (CRM09).
-- 3. A plan with a TVA project action is refused here: this first form has
--    the CRM side only, and 033 replaces it with the TVA branches
--    (FR-CRM-TVA). Until then the backend refuses a Labs deal's move into
--    Proposal, Negotiation and Closed Won, so no Labs deal reaches Proposal
--    without its project.
--
-- Then it updates the deal (audited as `opportunity.stage_changed` when the
-- stage moves), writes the stage history row (FR-CRM-OPP-02, -PIPE-14),
-- updates the company's lead stage and account type (FR-CRM-PIPE-04, -05),
-- closes the deal's open tasks and leaves a note saying why (automation 9,
-- FR-CRM-PIPE-09), and creates any tasks the plan carries. A refusal
-- anywhere rolls all of it back, audit rows included.
--
-- Errors: CRM04 (404), CRM09 (409), CRM22 (422), each one sentence.
--
-- Reversible: DROP FUNCTION crm_deal_transition(jsonb, jsonb).

CREATE OR REPLACE FUNCTION public.crm_deal_transition(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    opp_id      uuid := (p_plan ->> 'opportunity_id')::uuid;
    opp         crm_opportunities%ROWTYPE;
    comp        crm_companies%ROWTYPE;
    vals        jsonb;
    cols        text[];
    setlist     text;
    opp_j       jsonb;
    comp_j      jsonb;
    t           jsonb;
    l           jsonb;
    new_task    uuid;
    new_note    uuid;
    category    uuid;
    created     jsonb := '[]';
    closed_n    integer := 0;
    close_ids   uuid[];
    target      text;
    hit         boolean;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system', 'import', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    -- 3. No TVA side yet (033).
    IF p_plan ? 'project' AND jsonb_typeof(p_plan -> 'project') = 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'This move needs the TVA link, which is not built yet.';
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

    RETURN jsonb_build_object(
        'opportunity', opp_j,
        'company', comp_j,
        'tasks_created', created,
        'tasks_closed', closed_n,
        'note_id', new_note
    );
END;
$$;

REVOKE ALL ON FUNCTION public.crm_deal_transition(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_deal_transition(jsonb, jsonb) TO service_role;
