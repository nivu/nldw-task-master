-- 065_crm_ai_fields.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-FIELD-01 to -06, FR-CRMAI-CHG-11
-- (AI field values); FR-CRMAI-LLM-05; Q-12, Q-13, Q-27, Q-28, Q-38; D-3;
-- data-model §3.6, §4, §5; plan §5.8.
--
-- An AI field is a short generated answer about a company or a deal, shown
-- read-only on its record page and nowhere else (Q-28): ICP fit, Account
-- brief, Deal outlook (renamed from "Deal health", which already means a
-- deal's worst flag, Q-13), What is blocking, and the fields an Admin
-- defines. A value never feeds a stage, probability, flag, colour or
-- automation (FR-CRMAI-FIELD-05).
--
-- 1. `crm_ai_fields` (audited configuration, written through `crm_apply` by
--    Admins, FR-CRMAI-FIELD-01): per workspace, a key, a name, the record
--    type, the Admin's prompt (an instruction, never source text), the output
--    type (text, single select with 2 to 12 options, or a number 0 to 100,
--    Q-27) and whether it is enabled. `max_lines`, `depends_on`,
--    `with_reason` and `position` are set by this migration's seeds only and
--    are not editable through the API (D-3); a field an Admin creates keeps
--    their defaults.
-- 2. `crm_ai_field_values` (log): one value per field and record, in its own
--    table and never on `crm_companies` or `crm_opportunities`
--    (FR-CRMAI-FIELD-05), with the hash of the inputs it was computed from,
--    its provenance, the hour cap (`next_allowed_at`) and the trailing
--    recompute mark (`dirty_at`, Q-38). A log, not a record: no audit
--    trigger; generated text in small columns (P-3), masked for money in
--    Python before it is written (FR-CRMAI-LLM-05). Its record is held to
--    its workspace by `crm_check_refs()`; disabling or deleting a field
--    keeps or deletes its values (FK CASCADE).
-- 3. Seeds, every workspace, as the system: Account brief, Deal outlook and
--    What is blocking; ICP fit in Labs only (Q-12).
-- 4. `crm_purge()` replaced: a purged company's or deal's AI field values go
--    with it (data-model §4).
-- 5. `crm_merge_companies()` replaced: the merged company's values move to
--    the survivor, marked for recompute, unless the survivor has its own
--    value of the same field, which wins (FR-CRMAI-CHG-11, data-model §4).
--
-- Replaced functions, copied from their latest definition in the rebased
-- tree (`grep -l "CREATE OR REPLACE FUNCTION public.<name>("
-- supabase/migrations/*.sql | sort | tail -1`): crm_purge from
-- 062_crm_email_summaries.sql and crm_merge_companies from
-- 064_crm_enrichment.sql, with only the lines marked "065" added. 008 has no
-- migration after 050 that replaces either (T118: 056 replaces only
-- tasks_workspace_check).
--
-- RLS is on and the browser roles have no grant on either table; EXECUTE on
-- both functions is revoked from PUBLIC, anon and authenticated and granted
-- to service_role (data-model §5).
--
-- Reversible: DROP TABLE crm_ai_field_values, crm_ai_fields (their audit
-- rows stay); re-run 062's crm_purge and 064's crm_merge_companies, with
-- their grants.

-- ---------------------------------------------------------------------------
-- 1. crm_ai_fields — data-model §3.6 (audited configuration)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_ai_fields (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    key          text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,40}$'),
    name         text NOT NULL CHECK (name = btrim(name) AND char_length(name) BETWEEN 1 AND 60),
    record_type  text NOT NULL CHECK (record_type IN ('company', 'opportunity')),
    -- The Admin's instruction to the model; never source text.
    prompt       text NOT NULL CHECK (btrim(prompt) <> '' AND char_length(prompt) <= 2000),
    output_type  text NOT NULL CHECK (output_type IN ('text', 'single_select', 'number')),
    options      text[] NOT NULL DEFAULT '{}',
    -- Seeds only (D-3): a text field's lines, the field and values it is
    -- computed under, and Deal outlook's one-line reason.
    max_lines    smallint CHECK (max_lines BETWEEN 1 AND 10),
    depends_on   jsonb CHECK (depends_on IS NULL OR jsonb_typeof(depends_on) = 'object'),
    with_reason  boolean NOT NULL DEFAULT false,
    enabled      boolean NOT NULL DEFAULT true,
    position     numeric NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_ai_fields_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_ai_fields_key UNIQUE (workspace_id, key),
    -- FR-CRMAI-FIELD-01: a single select has its options, 2 to 12.
    CONSTRAINT crm_ai_fields_options
        CHECK ((output_type = 'single_select') = (cardinality(options) BETWEEN 2 AND 12))
);

DROP TRIGGER IF EXISTS crm_ai_fields_touch ON crm_ai_fields;
CREATE TRIGGER crm_ai_fields_touch BEFORE UPDATE ON crm_ai_fields
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS crm_ai_fields_crm_audit ON crm_ai_fields;
CREATE TRIGGER crm_ai_fields_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_ai_fields
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_ai_fields ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_ai_fields FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. crm_ai_field_values — data-model §3.6 (log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_ai_field_values (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    field_id        uuid NOT NULL,
    record_type     text NOT NULL CHECK (record_type IN ('company', 'opportunity')),
    record_id       uuid NOT NULL,
    -- Generated strings, masked for money in Python (FR-CRMAI-LLM-05).
    value_text      text CHECK (char_length(value_text) <= 600),
    value_number    smallint CHECK (value_number BETWEEN 0 AND 100),
    reason          text CHECK (char_length(reason) <= 200),
    -- FR-CRMAI-FIELD-04: the hash of the inputs it was computed from.
    inputs_hash     text NOT NULL CHECK (char_length(inputs_hash) <= 100),
    -- Provenance (P-2); none for a value cleared without a call.
    model           text CHECK (char_length(model) <= 100),
    generated_at    timestamptz,
    -- Q-38: an input changed inside the hour; recompute at its end.
    dirty_at        timestamptz,
    next_allowed_at timestamptz NOT NULL,
    CONSTRAINT crm_ai_field_values_field FOREIGN KEY (workspace_id, field_id)
        REFERENCES crm_ai_fields (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_ai_field_values_once UNIQUE (field_id, record_id)
);

CREATE INDEX IF NOT EXISTS crm_ai_field_values_record
    ON crm_ai_field_values (workspace_id, record_type, record_id);
CREATE INDEX IF NOT EXISTS crm_ai_field_values_dirty
    ON crm_ai_field_values (workspace_id, dirty_at) WHERE dirty_at IS NOT NULL;

-- A value's record must be in the value's own workspace (data-model §2).
DROP TRIGGER IF EXISTS crm_ai_field_values_check_refs ON crm_ai_field_values;
CREATE TRIGGER crm_ai_field_values_check_refs
    BEFORE INSERT OR UPDATE ON crm_ai_field_values
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

ALTER TABLE crm_ai_field_values ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_ai_field_values FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. Seeds — data-model §3.6, §7 (FR-CRMAI-FIELD table; Q-12, Q-13)
-- ---------------------------------------------------------------------------

-- The same four fields as `domain/crm/ai_fields.DEFAULT_FIELDS`; a test
-- holds the two to the same answer.
DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    INSERT INTO crm_ai_fields (workspace_id, key, name, record_type, prompt, output_type,
                               options, max_lines, depends_on, with_reason, position)
    SELECT w.id, s.key, s.name, s.record_type, s.prompt, s.output_type, s.options,
           s.max_lines, s.depends_on, s.with_reason, s.position
    FROM crm_workspaces w
    CROSS JOIN (VALUES
        ('icp_fit', 'ICP fit', 'company',
         'Which of Nunnari Labs'' lines of business this company fits best, judged from what '
         'it does and what it has discussed with us. Answer None when nothing on the record '
         'shows a fit.',
         'single_select', ARRAY['Factory Brain', 'Academy', 'Services', 'DeepWeaver', 'None'],
         NULL::smallint, NULL::jsonb, false, 1, true),
        ('account_brief', 'Account brief', 'company',
         'A brief on this account for a seller about to open it: who the company is, where '
         'the relationship stands and what happens next. At most 3 short lines.',
         'text', '{}'::text[], 3::smallint, NULL::jsonb, false, 2, false),
        ('deal_outlook', 'Deal outlook', 'opportunity',
         'How this deal is going, judged from its emails, meetings, next step dates and time '
         'in its stage: On track, At risk or Stalled, with a one-line reason.',
         'single_select', ARRAY['On track', 'At risk', 'Stalled'],
         NULL::smallint, NULL::jsonb, true, 3, false),
        ('what_is_blocking', 'What is blocking', 'opportunity',
         'What is holding this deal back, in one short line.',
         'text', '{}'::text[], 1::smallint,
         '{"field": "deal_outlook", "values": ["At risk", "Stalled"]}'::jsonb, false, 4, false)
    ) AS s (key, name, record_type, prompt, output_type, options, max_lines, depends_on,
            with_reason, position, labs_only)
    WHERE NOT s.labs_only OR w.key = 'labs'
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. Record lifecycle — data-model §4 (FR-CRMAI-CHG-11)
-- ---------------------------------------------------------------------------

-- 062's crm_purge, with the AI field values of a purged record deleted (065).

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
            -- 061 (009 FR-CRMAI-CHG-11): the Review Queue items that target
            -- it, whatever their status. A purged deal's place among other
            -- items' deal choices goes by its foreign key (CASCADE).
            DELETE FROM crm_review_items
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 062 (009 FR-CRMAI-CHG-11): the email summaries' links to it (the
            -- summary stays, linked to whatever else its thread names).
            DELETE FROM crm_email_summary_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
            -- 065 (009 FR-CRMAI-CHG-11): its AI field values (a log).
            DELETE FROM crm_ai_field_values
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
-- 5. crm_merge_companies — data-model §4 (FR-CRMAI-CHG-11)
-- ---------------------------------------------------------------------------

-- 064's crm_merge_companies, with the merged company's AI field values moved
-- (065).

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

    -- 065 (009 FR-CRMAI-CHG-11): AI field values. The survivor's own value
    -- of a field wins and the merged company's goes; else the merged
    -- company's moves to the survivor, marked for recompute (its inputs are
    -- now the survivor's). A log, not audited.
    DELETE FROM crm_ai_field_values g
    USING crm_ai_field_values k
    WHERE g.workspace_id = ws AND g.record_type = 'company' AND g.record_id = gone_id
      AND k.workspace_id = ws AND k.record_type = 'company' AND k.record_id = keep_id
      AND k.field_id = g.field_id;
    UPDATE crm_ai_field_values SET record_id = keep_id, dirty_at = now()
    WHERE workspace_id = ws AND record_type = 'company' AND record_id = gone_id;

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

REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
