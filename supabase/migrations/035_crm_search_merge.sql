-- 035_crm_search_merge.sql
--
-- Spec 007 (sales CRM) FR-CRM-SRCH-01 to -03, FR-CRM-DUP-03, -04,
-- FR-CRM-WS-02, FR-CRM-ACC-11, FR-CRM-AUD-01, -04, -06; data-model §5.7,
-- §5.9; plan §4 CT-1, §5.3, §5.8.
--
-- 1. `crm_search(workspace, q, limit, redact)`: the Cmd/Ctrl+K search, one
--    union across companies (name and every domain), people (full name,
--    primary and additional emails), deals (name), tasks (title) and notes
--    (the first 200 characters of the body), at most `limit` (≤ 8) of each.
--    A hit is a trigram match (`%`, or `<%` for a word inside a longer
--    text, such as a surname or a word of a note) or a prefix match (`LIKE`,
--    with `%`, `_` and `\` in the query escaped first, so they are matched
--    literally). Ranked by similarity (the best of the whole text, a word
--    in it and, for a company, a domain, averaged with the whole text's, so
--    an exact name outranks a longer one containing it), then most recently
--    changed. Only the workspace's live records: never the Trash, never a
--    merged company (FR-CRM-WS-02, FR-CRM-DEL-01). With `p_redact` (a Viewer) no amount
--    column is read at all (FR-CRM-ACC-11; defence in depth, Python redacts
--    too). As `crm_similar` (029) does, it prices the per-row trigram
--    comparison honestly so the planner uses the trigram indexes
--    (NFR-CRM-01). The 040 version adds campaigns.
--
-- 2. `crm_merge_companies(actor, plan)`: FR-CRM-DUP-03, -04. Python builds
--    the plan (domain/crm/merge.py); this function trusts no id in it.
--    Before anything is written it locks both companies in id order, in the
--    ACTOR's workspace, and refuses unless both exist there (CRM04), the two
--    ids differ, neither is in the Trash and neither was merged already
--    (CRM22), and both still carry the `updated_at` the plan was built from
--    (CRM09). Then, in one transaction:
--    1. the survivor takes the chosen values (`survivor_changes`, only the
--       fields a person can choose between);
--    2. every person, deal (Trash included, so a restore lands on the
--       survivor), task link, note link and Drive link moves to the
--       survivor. A task or note linked to both keeps one link. Moving a
--       deal moves its TVA project's company through 033's trigger;
--    3. the domains move, and the survivor's primary is the one the plan
--       names. Links and domains are moved as a removal and an addition, so
--       each timeline says what left and what arrived;
--    4. every other TVA project linked to the merged company is linked to
--       the survivor, with a TVA audit row (`workspace_id` NULL,
--       FR-CRM-AUD-04); the projects are otherwise unchanged (DUP-04);
--    5. the merged company gets `merged_into_id` and goes to the Trash, so
--       its id resolves to the survivor (`GET /crm/companies/{id}`).
--    Audited as `company.merged` on both ids. The 039 version also moves
--    email links, the 040 version campaign members.
--
-- Errors: CRM04 (404), CRM09 (409), CRM22 (422), each one sentence.
--
-- EXECUTE is revoked from PUBLIC, anon and authenticated: PostgREST exposes
-- every function in `public`, and the anon key is public (data-model §5.12).
--
-- Reversible: DROP FUNCTION crm_search(uuid, text, integer, boolean),
-- crm_merge_companies(jsonb, jsonb). A merge itself is history: its audit
-- rows stay (005).

-- ---------------------------------------------------------------------------
-- 1. crm_search — data-model §5.9
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
        FROM crm_tasks t
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

    RETURN found;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. crm_merge_companies — data-model §5.7
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
    n_people   integer := 0;
    n_deals    integer := 0;
    n_tasks    integer := 0;
    n_notes    integer := 0;
    n_links    integer := 0;
    n_domains  integer := 0;
    n_projects integer := 0;
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
        SELECT * FROM crm_task_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_task_links WHERE task_id = r.task_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_task_links (workspace_id, task_id, record_type, record_id,
                                        created_at, created_by)
            VALUES (ws, r.task_id, 'company', keep_id, r.created_at, r.created_by);
        END IF;
        DELETE FROM crm_task_links WHERE id = r.id;
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
            'links', n_links, 'domains', n_domains, 'projects', n_projects));
END;
$$;

-- Grants — data-model §5.12
REVOKE ALL ON FUNCTION public.crm_search(uuid, text, integer, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_search(uuid, text, integer, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
