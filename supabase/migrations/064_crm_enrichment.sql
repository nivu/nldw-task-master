-- 064_crm_enrichment.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-ENR-01 to -08, FR-CRMAI-CHG-11
-- (enrichment); NFR-CRMAI-01; P-3, P-5; Q-32, Q-33; D-2; data-model §3.5,
-- §4, §5, §6; plan §5.7.
--
-- A company with a website or a domain is enriched in the background from
-- its own site: what the AI read waits beside the person's own fields, never
-- in them (FR-CRMAI-ENR-03). Only the worker reads the site; the database
-- only records what is queued, so an import or a web request does no
-- enrichment work beyond that (FR-CRMAI-ENR-07).
--
-- 1. `crm_company_enrichment` (log): one row per company, its normalised
--    site (`https://host/`, the idempotency key with the company, Q-33), its
--    status (queued, done or failed), a failure reason from the fixed list
--    (FR-CRMAI-ENR-06 and FR-CRMAI-LLM-03's sentences, D-2), the enriched
--    values with their provenance, and the Admin who asked again (Enrich
--    selected), if one did. Status lives here and never on `crm_companies`,
--    so enrichment neither moves the company's `updated_at` nor writes an
--    audit row (FR-CRMAI-ENR-06). A log, not a record: no audit trigger; the
--    values are generated text in small fields, never page text (P-3).
--    Purging the company deletes its row (FK CASCADE, data-model §4).
-- 2. `crm_queue_enrichment()` (trigger function), on `crm_companies` (insert,
--    website change) and on `crm_company_domains` (insert, primary or
--    domain change): the company's site is its website's host, else its
--    primary domain. A company in the Trash, or with neither, is not
--    queued. A new row is queued; a row whose site differs is queued again
--    with the new site, its old values cleared (Q-32); the same site changes
--    nothing (FR-CRMAI-ENR-08). It runs whatever the phase switch says:
--    queuing is free and the job checks the switch.
-- 3. `crm_merge_companies()` replaced: the merged company's enrichment
--    moves to the survivor unless the survivor has its own, which wins
--    (FR-CRMAI-CHG-11, data-model §4).
--
-- Replaced function, copied from its latest definition in the rebased tree
-- (`grep -l "CREATE OR REPLACE FUNCTION public.crm_merge_companies("
-- supabase/migrations/*.sql | sort | tail -1`): 062_crm_email_summaries.sql,
-- with only the lines marked "064" added. 008 has no migration after 050
-- that replaces it (T099).
--
-- RLS is on and the browser roles have no grant on the table; EXECUTE on
-- both functions is revoked from PUBLIC, anon and authenticated and granted
-- to service_role (data-model §5).
--
-- Reversible: DROP TRIGGER crm_companies_queue_enrichment ON crm_companies;
-- DROP TRIGGER crm_company_domains_queue_enrichment ON crm_company_domains;
-- DROP FUNCTION crm_queue_enrichment(); DROP TABLE crm_company_enrichment;
-- re-run 062's crm_merge_companies, with its grants.

-- ---------------------------------------------------------------------------
-- 1. crm_company_enrichment — data-model §3.5 (log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_company_enrichment (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    company_id     uuid NOT NULL,
    -- The normalised site enriched or queued, `https://host/` (Q-33).
    website        text NOT NULL CHECK (char_length(website) <= 500),
    status         text NOT NULL CHECK (status IN ('queued', 'done', 'failed')),
    -- FR-CRMAI-ENR-06 and FR-CRMAI-LLM-03, nothing else (D-2): a host the
    -- URL policy refuses is recorded 'Website did not respond'.
    failure_reason text CHECK (failure_reason IS NULL OR failure_reason IN (
                       'Website did not respond', 'No company information found',
                       'Output did not match the schema', 'Model call failed',
                       'Model call timed out', 'AI budget reached'
                   )),
    -- {industry, sub_industry, employee_band, city, country, description,
    -- linkedin, products: []}: generated strings, masked for money in Python
    -- before they are written (FR-CRMAI-LLM-05); never page text.
    "values"       jsonb NOT NULL DEFAULT '{}'
                   CHECK (jsonb_typeof("values") = 'object' AND char_length("values"::text) <= 4000),
    model          text CHECK (char_length(model) <= 100),
    generated_at   timestamptz,
    queued_at      timestamptz NOT NULL DEFAULT now(),
    -- The Admin of Enrich selected; NULL when the trigger queued it.
    requested_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crm_company_enrichment_company FOREIGN KEY (workspace_id, company_id)
        REFERENCES crm_companies (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_company_enrichment_one UNIQUE (workspace_id, company_id),
    CONSTRAINT crm_company_enrichment_reason CHECK (
        (status = 'failed') = (failure_reason IS NOT NULL)
    )
);

-- The queue, oldest first (data-model §6).
CREATE INDEX IF NOT EXISTS crm_company_enrichment_queued
    ON crm_company_enrichment (workspace_id, status, queued_at) WHERE status = 'queued';

ALTER TABLE crm_company_enrichment ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_company_enrichment FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. crm_queue_enrichment — data-model §3.5 (FR-CRMAI-ENR-01, -07, -08; Q-32)
-- ---------------------------------------------------------------------------

-- The site is computed as `domain/crm/enrichment.site()` computes it in
-- Python (the Admin's Enrich selected): the website's host, lower-cased,
-- without a user, port or path, when it is a valid domain name; else the
-- primary domain. A test holds the two to the same answer.
CREATE OR REPLACE FUNCTION public.crm_queue_enrichment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws      uuid := NEW.workspace_id;
    company uuid;
    url     text;
    host    text;
BEGIN
    IF TG_TABLE_NAME = 'crm_companies' THEN
        company := NEW.id;
        IF NEW.deleted_at IS NOT NULL THEN
            RETURN NULL;
        END IF;
        url := NEW.website -> 'primary' ->> 'url';
    ELSE
        company := NEW.company_id;
        SELECT c.website -> 'primary' ->> 'url' INTO url
        FROM crm_companies c
        WHERE c.id = company AND c.workspace_id = ws AND c.deleted_at IS NULL;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
    END IF;

    host := rtrim(lower(substring(btrim(coalesce(url, ''))
                  FROM '^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/?#]*@)?([^/:?#]+)')), '.');
    IF host IS NULL
       OR host !~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$' THEN
        host := NULL;
        SELECT d.domain INTO host
        FROM crm_company_domains d
        WHERE d.company_id = company AND d.workspace_id = ws AND d.is_primary;
    END IF;
    IF host IS NULL THEN
        RETURN NULL;
    END IF;

    -- Queued, or queued again for a new site (Q-32); the same site changes
    -- nothing (FR-CRMAI-ENR-08). The company row itself is not touched.
    INSERT INTO crm_company_enrichment (workspace_id, company_id, website, status)
    VALUES (ws, company, 'https://' || host || '/', 'queued')
    ON CONFLICT (workspace_id, company_id) DO UPDATE
        SET website = EXCLUDED.website, status = 'queued', failure_reason = NULL,
            "values" = '{}', model = NULL, generated_at = NULL, queued_at = now(),
            requested_by = NULL
        WHERE crm_company_enrichment.website IS DISTINCT FROM EXCLUDED.website;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS crm_companies_queue_enrichment ON crm_companies;
CREATE TRIGGER crm_companies_queue_enrichment
    AFTER INSERT OR UPDATE OF website ON crm_companies
    FOR EACH ROW EXECUTE FUNCTION public.crm_queue_enrichment();

DROP TRIGGER IF EXISTS crm_company_domains_queue_enrichment ON crm_company_domains;
CREATE TRIGGER crm_company_domains_queue_enrichment
    AFTER INSERT OR UPDATE OF is_primary, domain ON crm_company_domains
    FOR EACH ROW EXECUTE FUNCTION public.crm_queue_enrichment();

-- ---------------------------------------------------------------------------
-- 3. crm_merge_companies — data-model §4 (FR-CRMAI-CHG-11)
-- ---------------------------------------------------------------------------

-- 062's crm_merge_companies, with the merged company's enrichment moved (064).

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
    -- 061: a review item's dedupe key, before and after the move.
    old_head   text;
    new_key    text;
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

    -- 062 (009 FR-CRMAI-CHG-11): email summary links, as email links; a
    -- summary linked to both keeps the survivor's. A log, not audited.
    FOR r IN
        SELECT * FROM crm_email_summary_links
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
        ORDER BY id
    LOOP
        IF NOT EXISTS (SELECT 1 FROM crm_email_summary_links WHERE summary_id = r.summary_id
                       AND record_type = 'company' AND record_id = keep_id) THEN
            INSERT INTO crm_email_summary_links (workspace_id, summary_id, record_type,
                                                 record_id)
            VALUES (ws, r.summary_id, 'company', keep_id);
        END IF;
        DELETE FROM crm_email_summary_links WHERE id = r.id;
    END LOOP;

    -- 061 (009 FR-CRMAI-CHG-11): the merged company's open Review Queue
    -- items move to the survivor, their dedupe key's target recomputed
    -- (`<source_kind>|<source_key>|<type>|company:<id>…`, data-model §3.2).
    -- One whose new key is already open on the survivor is expired instead,
    -- with the audit row an expiry writes. Decided items stay where they
    -- are. A log, not audited.
    FOR r IN
        SELECT * FROM crm_review_items
        WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id
          AND status = 'open'
        ORDER BY created_at, id
    LOOP
        old_head := r.source_kind || '|' || r.source_key || '|' || r.type || '|company:'
                    || gone_id::text;
        new_key := CASE
            WHEN left(r.dedupe_key, char_length(old_head)) = old_head THEN
                r.source_kind || '|' || r.source_key || '|' || r.type || '|company:'
                || keep_id::text || substr(r.dedupe_key, char_length(old_head) + 1)
            ELSE r.dedupe_key
        END;
        IF EXISTS (SELECT 1 FROM crm_review_items
                   WHERE workspace_id = ws AND dedupe_key = new_key AND status = 'open'
                     AND id <> r.id) THEN
            UPDATE crm_review_items SET status = 'expired', decided_at = now()
            WHERE id = r.id;
            INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                   target_table, target_id, before, after, suggestion_id)
            VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.expired',
                    'crm_review_items', r.id::text, jsonb_build_object('status', 'open'),
                    jsonb_build_object('status', 'expired'), r.id);
        ELSE
            UPDATE crm_review_items SET record_id = keep_id, dedupe_key = new_key
            WHERE id = r.id;
        END IF;
    END LOOP;

    -- 064 (009 FR-CRMAI-CHG-11): enrichment. The survivor's own row wins and
    -- the merged company's goes; else the merged company's moves to the
    -- survivor. A survivor's row that only the chosen website queued above,
    -- for the merged company's site, is not its own: it goes and the merged
    -- company's moves, so that site is not read again (FR-CRMAI-ENR-08).
    -- A log, not audited.
    DELETE FROM crm_company_enrichment k
    USING crm_company_enrichment g
    WHERE k.workspace_id = ws AND k.company_id = keep_id
      AND g.workspace_id = ws AND g.company_id = gone_id
      AND k.status = 'queued' AND k.requested_by IS NULL AND k.website = g.website;
    IF EXISTS (SELECT 1 FROM crm_company_enrichment
               WHERE workspace_id = ws AND company_id = keep_id) THEN
        DELETE FROM crm_company_enrichment WHERE workspace_id = ws AND company_id = gone_id;
    ELSE
        UPDATE crm_company_enrichment SET company_id = keep_id
        WHERE workspace_id = ws AND company_id = gone_id;
    END IF;

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
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_queue_enrichment() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_queue_enrichment() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
