-- 029_crm_companies_people.sql
--
-- Spec 007 (sales CRM) FR-CRM-CO-01 to -03, FR-CRM-PER-01, FR-CRM-LIST-07,
-- -08, FR-CRM-LINK-01 to -03, FR-CRM-DUP-01, -02, FR-CRM-DEL-01, -02,
-- FR-CRM-PIPE-01, FR-CRM-WS-02; Q-36, Q-54; owner decision D4; data-model
-- §2, §3.6–§3.8, §3.14, §5.4.
--
-- The first CRM records: companies, their email domains, people and Google
-- Drive links. Opportunities, tasks and notes follow in 030.
--
-- 1. `pg_trgm`, in the `extensions` schema as Supabase keeps it, for the
--    possible-duplicate warnings (FR-CRM-DUP-02) and, later, search.
--
-- 2. Four tables, each following data-model §2: RLS on with no policy and no
--    browser grant (FR-CRM-ACC-10); `UNIQUE (workspace_id, id)` on every
--    table another one references; and every reference between CRM tables,
--    select options included, is a composite foreign key on
--    (workspace_id, x_id), so a row in one workspace can never point at a
--    row of another (FR-CRM-WS-02).
--    - `crm_companies`: a new company starts at New (FR-CRM-PIPE-01). The
--      `last_*` contact columns are written by email sync and the importer
--      only; the backend refuses them on any manual or MCP write
--      (FR-CRM-CO-03). Channels are a uuid[] of options, which cannot be a
--      foreign key, so 027's `crm_check_refs()` checks them.
--    - `crm_company_domains`: the primary domain and the additional ones in
--      one table, so one unique index refuses a clash among all of them
--      (FR-CRM-DUP-01). A trashed company keeps its domains, so a clash with
--      it names it "(in Trash)" rather than letting a second company take
--      the domain while the first can still be restored.
--    - `crm_people`: the primary email is unique per workspace, trashed
--      people included, for the same reason.
--    - `crm_drive_links`: a URL and a label on any record (D4). `https://`
--      only; no file content, preview or Drive permission is ever stored
--      (FR-CRM-LINK-02). `record_type` / `record_id` is polymorphic, so
--      `crm_check_refs()` checks that the record exists in the link's own
--      workspace.
--    Records are soft-deleted (`deleted_at`, `deleted_by`, and a
--    `deleted_group` shared by everything one action deleted, so a restore
--    brings it all back, FR-CRM-DEL-02). List indexes skip the trash.
--
-- 3. `crm_purge(actor, plan)`, first form: permanently deletes records that
--    are already in the Trash of the actor's workspace, with the Drive links
--    that point at them (FR-CRM-DEL-01). Later migrations replace it as more
--    tables point at records (030 task and note links, 033 TVA projects, 034
--    favourites, 039 email links). A company's domains go with it (ON DELETE
--    CASCADE); its people lose the link (ON DELETE SET NULL).
--
-- 4. `crm_similar(workspace, object, name, limit)`: the possible duplicates
--    by name similarity (pg_trgm, threshold 0.4) among live records, served
--    by the trigram indexes below. PostgREST cannot express the `%`
--    operator, so the backend calls this instead. pg_trgm declares `%` as
--    cheap as `=`, so with a workspace filter beside it the planner would
--    rather read every company of the workspace and compare each name than
--    use the trigram index; measured, that is the slower plan. The function
--    raises `cpu_operator_cost` for its own query only, which prices the
--    per-row comparison honestly and lets the index win (NFR-CRM-01).
--
-- Reversible: DROP FUNCTION crm_similar(uuid, text, text, integer),
-- crm_purge(jsonb, jsonb); DROP TABLE crm_drive_links, crm_people,
-- crm_company_domains, crm_companies. Their audit rows stay: history is
-- append-only (005).

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;

-- ---------------------------------------------------------------------------
-- 1. crm_companies — data-model §3.6
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_companies (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id           uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    name                   text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 200),
    -- Labs only; refused elsewhere in Python (FR-CRM-WS-06).
    entities               text[] NOT NULL DEFAULT '{}'
                           CHECK (entities <@ ARRAY['nunnari_labs', 'deepweaver']::text[]),
    lead_stage             text NOT NULL DEFAULT 'new' CHECK (lead_stage IN (
                               'new', 'contacted', 'nurturing', 'qualified', 'deal_open',
                               'customer', 'partner', 'disqualified'
                           )),
    account_type_id        uuid,
    relationship_status_id uuid,
    channel_ids            uuid[] NOT NULL DEFAULT '{}',
    owner_id               uuid NOT NULL REFERENCES profiles(id),
    next_touch_on          date,
    industry_id            uuid,
    institution_type_id    uuid,
    size_id                uuid,
    source_id              uuid,
    country                text CHECK (char_length(country) <= 100),
    city                   text CHECK (char_length(city) <= 100),
    address                jsonb CHECK (address IS NULL OR jsonb_typeof(address) = 'object'),
    website                jsonb CHECK (website IS NULL OR jsonb_typeof(website) = 'object'),
    linkedin               jsonb CHECK (linkedin IS NULL OR jsonb_typeof(linkedin) = 'object'),
    description            text CHECK (char_length(description) <= 20000),
    -- System-written (FR-CRM-CO-03).
    last_inbound_at        timestamptz,
    last_outbound_at       timestamptz,
    last_contact_at        timestamptz,
    -- FR-CRM-DUP-03 (S7): set on the company merged away.
    merged_into_id         uuid,
    twenty_id              text,
    position               numeric NOT NULL DEFAULT 0,
    created_at             timestamptz NOT NULL DEFAULT now(),
    created_by             uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    updated_by             uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at             timestamptz,
    deleted_by             uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group          uuid,
    CONSTRAINT crm_companies_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_companies_account_type FOREIGN KEY (workspace_id, account_type_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_companies_relationship_status
        FOREIGN KEY (workspace_id, relationship_status_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_companies_industry FOREIGN KEY (workspace_id, industry_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_companies_institution_type FOREIGN KEY (workspace_id, institution_type_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_companies_size FOREIGN KEY (workspace_id, size_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_companies_source FOREIGN KEY (workspace_id, source_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_companies_merged_into FOREIGN KEY (workspace_id, merged_into_id)
        REFERENCES crm_companies (workspace_id, id) ON DELETE SET NULL (merged_into_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_companies_twenty
    ON crm_companies (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_companies_name
    ON crm_companies (workspace_id, lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_companies_name_trgm
    ON crm_companies USING gin (lower(name) extensions.gin_trgm_ops) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_companies_lead_stage
    ON crm_companies (workspace_id, lead_stage, position) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_companies_owner
    ON crm_companies (workspace_id, owner_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_companies_updated
    ON crm_companies (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_companies_channels
    ON crm_companies USING gin (channel_ids) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_companies_trash
    ON crm_companies (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. crm_company_domains — data-model §3.7
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_company_domains (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    company_id   uuid NOT NULL,
    domain       text NOT NULL CHECK (
                     domain = lower(domain)
                     AND domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
                 ),
    is_primary   boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_company_domains_company FOREIGN KEY (workspace_id, company_id)
        REFERENCES crm_companies (workspace_id, id) ON DELETE CASCADE,
    -- FR-CRM-DUP-01: one company per domain, primary or additional, trashed
    -- companies included.
    CONSTRAINT crm_company_domains_workspace_domain UNIQUE (workspace_id, domain)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_company_domains_one_primary
    ON crm_company_domains (company_id) WHERE is_primary;
CREATE INDEX IF NOT EXISTS crm_company_domains_company
    ON crm_company_domains (workspace_id, company_id);
CREATE INDEX IF NOT EXISTS crm_company_domains_trgm
    ON crm_company_domains USING gin (domain extensions.gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 3. crm_people — data-model §3.8
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_people (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    first_name        text NOT NULL
                      CHECK (btrim(first_name) <> '' AND char_length(first_name) <= 100),
    last_name         text CHECK (char_length(last_name) <= 100),
    company_id        uuid,
    primary_email     text CHECK (primary_email = lower(primary_email)),
    additional_emails text[] NOT NULL DEFAULT '{}',
    phones            jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(phones) = 'array'),
    job_title         text CHECK (char_length(job_title) <= 200),
    city              text CHECK (char_length(city) <= 100),
    linkedin          jsonb CHECK (linkedin IS NULL OR jsonb_typeof(linkedin) = 'object'),
    owner_id          uuid REFERENCES profiles(id) ON DELETE SET NULL,
    -- System-written (FR-CRM-CO-03 applies to people too, spec §5.7).
    last_inbound_at   timestamptz,
    last_outbound_at  timestamptz,
    last_contact_at   timestamptz,
    do_not_contact    boolean NOT NULL DEFAULT false,
    twenty_id         text,
    position          numeric NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at        timestamptz,
    deleted_by        uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group     uuid,
    CONSTRAINT crm_people_workspace_id UNIQUE (workspace_id, id),
    -- Purging a company leaves its people without one (FR-CRM-DEL-02); the
    -- column list keeps workspace_id, which is NOT NULL.
    CONSTRAINT crm_people_company FOREIGN KEY (workspace_id, company_id)
        REFERENCES crm_companies (workspace_id, id) ON DELETE SET NULL (company_id)
);

-- FR-CRM-DUP-01: trashed people included, so a restore can never clash.
CREATE UNIQUE INDEX IF NOT EXISTS crm_people_workspace_email
    ON crm_people (workspace_id, primary_email) WHERE primary_email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS crm_people_twenty
    ON crm_people (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_people_name_trgm
    ON crm_people USING gin (
        lower(first_name || ' ' || coalesce(last_name, '')) extensions.gin_trgm_ops
    ) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_people_email_trgm
    ON crm_people USING gin (primary_email extensions.gin_trgm_ops) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_people_additional_emails
    ON crm_people USING gin (additional_emails) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_people_company
    ON crm_people (workspace_id, company_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_people_owner
    ON crm_people (workspace_id, owner_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_people_updated
    ON crm_people (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_people_trash
    ON crm_people (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 4. crm_drive_links — data-model §3.14
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_drive_links (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    record_type   text NOT NULL CHECK (record_type IN (
                      'company', 'person', 'opportunity', 'task', 'note', 'campaign'
                  )),
    record_id     uuid NOT NULL,
    -- FR-CRM-LINK-02: a link, never content.
    url           text NOT NULL CHECK (url ~ '^https://[^\s]+$' AND char_length(url) <= 2000),
    label         text NOT NULL CHECK (btrim(label) <> '' AND char_length(label) <= 200),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at    timestamptz,
    deleted_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group uuid,
    CONSTRAINT crm_drive_links_workspace_id UNIQUE (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS crm_drive_links_record
    ON crm_drive_links (workspace_id, record_type, record_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_drive_links_trash
    ON crm_drive_links (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 5. Triggers, RLS, privileges — data-model §2
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS crm_companies_touch ON crm_companies;
CREATE TRIGGER crm_companies_touch BEFORE UPDATE ON crm_companies
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_people_touch ON crm_people;
CREATE TRIGGER crm_people_touch BEFORE UPDATE ON crm_people
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_drive_links_touch ON crm_drive_links;
CREATE TRIGGER crm_drive_links_touch BEFORE UPDATE ON crm_drive_links
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- References that cannot be foreign keys (data-model §5.14).
DROP TRIGGER IF EXISTS crm_companies_check_refs ON crm_companies;
CREATE TRIGGER crm_companies_check_refs BEFORE INSERT OR UPDATE ON crm_companies
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs('channel_ids');
DROP TRIGGER IF EXISTS crm_drive_links_check_refs ON crm_drive_links;
CREATE TRIGGER crm_drive_links_check_refs BEFORE INSERT OR UPDATE ON crm_drive_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

DROP TRIGGER IF EXISTS crm_companies_crm_audit ON crm_companies;
CREATE TRIGGER crm_companies_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_companies
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_company_domains_crm_audit ON crm_company_domains;
CREATE TRIGGER crm_company_domains_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_company_domains
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_people_crm_audit ON crm_people;
CREATE TRIGGER crm_people_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_people
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_drive_links_crm_audit ON crm_drive_links;
CREATE TRIGGER crm_drive_links_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_drive_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_companies       ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_company_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_people          ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_drive_links     ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_companies, crm_company_domains, crm_people, crm_drive_links
    FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 6. crm_purge — data-model §5.4, first form (replaced in 030, 033, 034, 039)
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
        -- Drive links name.
        rtype := CASE tbl
            WHEN 'crm_companies' THEN 'company'
            WHEN 'crm_people' THEN 'person'
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

        IF rtype IS NOT NULL THEN
            DELETE FROM crm_drive_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
        END IF;
        EXECUTE format('DELETE FROM %I WHERE id = $1 AND workspace_id = $2', tbl)
        USING target, ws;
        purged := purged + 1;
    END LOOP;

    RETURN jsonb_build_object('purged', purged);
END;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_similar — possible duplicates by name, FR-CRM-DUP-02
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_similar(
    p_workspace uuid, p_object text, p_name text, p_limit integer
)
RETURNS TABLE (id uuid, name text, similarity real)
LANGUAGE plpgsql
STABLE
SET search_path = public, extensions
SET pg_trgm.similarity_threshold = 0.4
SET cpu_operator_cost = 0.25
AS $$
BEGIN
    IF p_object = 'company' THEN
        RETURN QUERY
        SELECT c.id, c.name, extensions.similarity(lower(c.name), lower(p_name))
        FROM crm_companies c
        WHERE c.workspace_id = p_workspace AND c.deleted_at IS NULL
          AND lower(c.name) % lower(p_name)
        ORDER BY 3 DESC, c.name
        LIMIT least(coalesce(p_limit, 5), 20);
    ELSIF p_object = 'person' THEN
        RETURN QUERY
        SELECT p.id, btrim(p.first_name || ' ' || coalesce(p.last_name, '')),
               extensions.similarity(lower(p.first_name || ' ' || coalesce(p.last_name, '')),
                                     lower(p_name))
        FROM crm_people p
        WHERE p.workspace_id = p_workspace AND p.deleted_at IS NULL
          AND lower(p.first_name || ' ' || coalesce(p.last_name, '')) % lower(p_name)
        ORDER BY 3 DESC, 2
        LIMIT least(coalesce(p_limit, 5), 20);
    ELSE
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Unknown kind of record.';
    END IF;
END;
$$;

-- Grants — data-model §5.12
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_similar(uuid, text, text, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_similar(uuid, text, text, integer) TO service_role;
