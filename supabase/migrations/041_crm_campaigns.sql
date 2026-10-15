-- 041_crm_campaigns.sql
--
-- Spec 007 (sales CRM) FR-CRM-CAMP-01 to -07, FR-CRM-PER-02, FR-CRM-MIG-01,
-- FR-CRM-AUTO-01 (automation 8), FR-CRM-VIEW-07, -11, FR-CRM-SRCH-01,
-- FR-CRM-DUP-03, FR-CRM-DEL-01, FR-CRM-ACC-11; Q-49, Q-61; data-model
-- §3.4.1, §3.9, §3.15, §3.23, §5.4, §5.7, §5.8, §5.9, §7.2, §7.5.
--
-- Campaigns and their members (1b).
--
-- 1. `crm_select_options` takes three more lists: `campaign.type` and
--    `campaign.status` (admin-editable) and `campaign_member.status` (fixed,
--    its keys are the member's `status` column, as task status is).
-- 2. `crm_campaigns` (audited): name, type, status, channel, Entity (Labs
--    only, one, Q-61; refused elsewhere in Python), owner, dates, goal and a
--    budget in INR, AUD or USD stored with its INR value at the month's rate,
--    held by the same FX CHECKs as a deal's amount (§3.9). The budget and
--    its INR value and rate are amounts (FR-CRM-ACC-11). Converted deals,
--    pipeline created, won value and cost per converted member are derived
--    on read from the members' converted deals, never stored.
-- 3. `crm_campaign_members` (audited): a company, a person or both on a
--    campaign, once (`NULLS NOT DISTINCT`), with a status from the fixed
--    list, last touch and notes, and the deal it converted to. Every
--    reference is a composite FK on (workspace_id, id), so a member of one
--    workspace can never point at a campaign, company, person or deal of
--    another. A member whose status is not Converted has no deal; one that
--    is Converted gets its deal from the conversion (Python). Purging that
--    deal later leaves the member Converted with the link cleared, so the
--    conversion stays in its history.
-- 4. `crm_opportunities.source_campaign_id`: the campaign a deal came from,
--    written by the conversion (automation 8), cleared if the campaign is
--    purged.
-- 5. Seeds (data-model §7.2, §7.5): the three lists in every workspace; the
--    registry rows of campaigns and members (`budget`, `budget_in_inr` and
--    `budget_fx_rate` with `is_amount`); the seeded shared view "Members by
--    status" (a kanban by status; the campaign page narrows it to one
--    campaign, FR-CRM-VIEW-11).
-- 6. `crm_query_rank()` replaced (038's, plus the member statuses after the
--    task statuses in one array, since the two lists share the column name
--    `status` and no key, and campaign, person and deal relations by name);
--    `crm_query()` replaced (034's, plus the two tables and their `q`).
-- 7. `crm_search()` replaced (035's, plus campaigns by name; no amount
--    column is read for anyone).
-- 8. `crm_merge_companies()` replaced (039's, plus moving campaign members
--    to the survivor; a membership the survivor already has in the same
--    campaign keeps the survivor's).
-- 9. `crm_purge()` replaced (039's, plus campaigns): purging a campaign
--    removes its links and favourites, and its members with it (FK
--    CASCADE); purging a company or person first keeps any membership that
--    still names the other one (the person, or the company), and the rest
--    go with it.
--
-- `crm_check_refs()` (027) and the link tables' record-type CHECKs (029,
-- 030) already name `campaign`, so tasks, notes and Drive links on a
-- campaign need no change there (data-model §3.23).
--
-- EXECUTE on the functions is revoked from PUBLIC, anon and authenticated
-- (data-model §5.12).
--
-- Reversible: restore 038's crm_query_rank(), 034's crm_query(), 035's
-- crm_search() and 039's crm_merge_companies() and crm_purge(); DROP
-- CONSTRAINT crm_opportunities_source_campaign, DROP COLUMN
-- source_campaign_id; DROP TABLE crm_campaign_members, crm_campaigns (their
-- audit rows stay); DELETE the seeded view, the registry rows and the three
-- lists' options (audited as purges), and restore 026's list_key CHECK.

-- ---------------------------------------------------------------------------
-- 1. The campaign lists — data-model §3.4.1
-- ---------------------------------------------------------------------------

-- Load pg_trgm into this session before any function below declares
-- `SET pg_trgm.*`: until the library is loaded those are unknown settings,
-- which only a superuser may attach to a function. A whole `db push` loads
-- it in 029, but a push resumed on a fresh connection would stop here.
SELECT extensions.similarity('', '');

ALTER TABLE crm_select_options DROP CONSTRAINT IF EXISTS crm_select_options_list_key_check;
ALTER TABLE crm_select_options ADD CONSTRAINT crm_select_options_list_key_check
    CHECK (list_key IN (
        'company.lead_stage', 'opportunity.stage', 'task.status', 'task.bucket',
        'company.account_type', 'company.relationship_status', 'channel',
        'company.industry', 'company.institution_type', 'company.size', 'source',
        'opportunity.deal_type', 'opportunity.engagement_model',
        'opportunity.programme', 'opportunity.lost_reason',
        'opportunity.contact_role', 'task.category',
        'campaign.type', 'campaign.status', 'campaign_member.status'
    ));

-- ---------------------------------------------------------------------------
-- 2. crm_campaigns — data-model §3.23 (audited)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_campaigns (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    name           text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 200),
    type_id        uuid,
    status_id      uuid,
    channel_id     uuid,
    -- Labs only, one entity (Q-61); refused elsewhere in Python.
    entity         text CHECK (entity IN ('nunnari_labs', 'deepweaver')),
    owner_id       uuid NOT NULL REFERENCES profiles(id),
    starts_on      date,
    ends_on        date,
    goal           text CHECK (char_length(goal) <= 20000),
    budget         numeric(14,2) CHECK (budget >= 0),
    budget_currency text CHECK (budget_currency IN ('INR', 'AUD', 'USD')),
    budget_in_inr  numeric(14,2) CHECK (budget_in_inr >= 0),
    budget_fx_rate numeric(12,6) CHECK (budget_fx_rate > 0),
    twenty_id      text,
    position       numeric NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at     timestamptz,
    deleted_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group  uuid,
    CONSTRAINT crm_campaigns_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_campaigns_type FOREIGN KEY (workspace_id, type_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_campaigns_status FOREIGN KEY (workspace_id, status_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_campaigns_channel FOREIGN KEY (workspace_id, channel_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_campaigns_dates CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on),
    -- The budget's currency and INR value travel with it.
    CONSTRAINT crm_campaigns_budget_currency CHECK ((budget IS NULL) = (budget_currency IS NULL)),
    CONSTRAINT crm_campaigns_budget_in_inr CHECK ((budget IS NULL) = (budget_in_inr IS NULL)),
    -- FX integrity, as a deal's amount (030, FR-CRM-FX-03).
    CONSTRAINT crm_campaigns_budget_fx_rate CHECK ((budget IS NULL) = (budget_fx_rate IS NULL)),
    CONSTRAINT crm_campaigns_budget_fx_inr
        CHECK (budget IS NULL OR budget_in_inr = round(budget * budget_fx_rate, 2)),
    CONSTRAINT crm_campaigns_budget_inr_at_one
        CHECK (budget_currency IS NULL OR (budget_currency = 'INR') = (budget_fx_rate = 1))
);

-- FR-CRM-MIG-07: a re-import matches its Twenty id.
CREATE UNIQUE INDEX IF NOT EXISTS crm_campaigns_twenty
    ON crm_campaigns (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_campaigns_updated
    ON crm_campaigns (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_campaigns_name_trgm
    ON crm_campaigns USING gin (lower(name) extensions.gin_trgm_ops)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_campaigns_trash
    ON crm_campaigns (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. crm_campaign_members — data-model §3.23 (audited)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_campaign_members (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id             uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    campaign_id              uuid NOT NULL,
    company_id               uuid,
    person_id                uuid,
    status                   text NOT NULL DEFAULT 'targeted' CHECK (status IN (
                                 'targeted', 'invited', 'registered', 'attended', 'engaged',
                                 'meeting_booked', 'converted', 'not_interested',
                                 'unsubscribed'
                             )),
    last_touch_on            date,
    notes                    text CHECK (char_length(notes) <= 20000),
    converted_opportunity_id uuid,
    twenty_id                text,
    position                 numeric NOT NULL DEFAULT 0,
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    updated_by               uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at               timestamptz,
    deleted_by               uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group            uuid,
    CONSTRAINT crm_campaign_members_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_campaign_members_campaign FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES crm_campaigns (workspace_id, id) ON DELETE CASCADE,
    -- crm_purge keeps a membership that still names the other one first.
    CONSTRAINT crm_campaign_members_company FOREIGN KEY (workspace_id, company_id)
        REFERENCES crm_companies (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_campaign_members_person FOREIGN KEY (workspace_id, person_id)
        REFERENCES crm_people (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_campaign_members_opportunity
        FOREIGN KEY (workspace_id, converted_opportunity_id)
        REFERENCES crm_opportunities (workspace_id, id)
        ON DELETE SET NULL (converted_opportunity_id),
    -- FR-CRM-CAMP-01: a company, a person, or both.
    CONSTRAINT crm_campaign_members_someone CHECK (company_id IS NOT NULL OR person_id IS NOT NULL),
    -- Only a converted member has a deal (FR-CRM-CAMP-04).
    CONSTRAINT crm_campaign_members_converted
        CHECK (converted_opportunity_id IS NULL OR status = 'converted'),
    CONSTRAINT crm_campaign_members_once
        UNIQUE NULLS NOT DISTINCT (campaign_id, company_id, person_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_campaign_members_twenty
    ON crm_campaign_members (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;
-- The member board: one campaign, by status, in kanban order.
CREATE INDEX IF NOT EXISTS crm_campaign_members_board
    ON crm_campaign_members (workspace_id, campaign_id, status, position)
    WHERE deleted_at IS NULL;
-- A company's or person's campaign memberships (FR-CRM-CO-02, REC-03).
CREATE INDEX IF NOT EXISTS crm_campaign_members_company
    ON crm_campaign_members (workspace_id, company_id) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_campaign_members_person
    ON crm_campaign_members (workspace_id, person_id) WHERE person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_campaign_members_opportunity
    ON crm_campaign_members (workspace_id, converted_opportunity_id)
    WHERE converted_opportunity_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 4. crm_opportunities.source_campaign_id — data-model §3.9
-- ---------------------------------------------------------------------------

ALTER TABLE crm_opportunities ADD COLUMN IF NOT EXISTS source_campaign_id uuid;
ALTER TABLE crm_opportunities DROP CONSTRAINT IF EXISTS crm_opportunities_source_campaign;
ALTER TABLE crm_opportunities ADD CONSTRAINT crm_opportunities_source_campaign
    FOREIGN KEY (workspace_id, source_campaign_id)
    REFERENCES crm_campaigns (workspace_id, id) ON DELETE SET NULL (source_campaign_id);
CREATE INDEX IF NOT EXISTS crm_opportunities_source_campaign
    ON crm_opportunities (workspace_id, source_campaign_id)
    WHERE source_campaign_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Triggers, RLS, privileges — data-model §2
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS crm_campaigns_touch ON crm_campaigns;
CREATE TRIGGER crm_campaigns_touch BEFORE UPDATE ON crm_campaigns
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_campaign_members_touch ON crm_campaign_members;
CREATE TRIGGER crm_campaign_members_touch BEFORE UPDATE ON crm_campaign_members
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS crm_campaigns_crm_audit ON crm_campaigns;
CREATE TRIGGER crm_campaigns_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_campaigns
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_campaign_members_crm_audit ON crm_campaign_members;
CREATE TRIGGER crm_campaign_members_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_campaign_members
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_campaigns        ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_campaign_members ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_campaigns, crm_campaign_members FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. Seeds — data-model §7.2, §7.5
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    palette text[] := ARRAY['gray', 'red', 'orange', 'amber', 'green', 'teal', 'blue',
                            'indigo', 'purple', 'pink'];
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- Spec §6.6, as 026 seeds its lists: 'system_key|Label', or '|Label'
    -- for an option no rule reads. A campaign's status is admin-editable
    -- but keyed, so an import can name it; a member's is fixed (Q-30).
    INSERT INTO crm_select_options
        (workspace_id, list_key, system_key, label, color, position, is_default, is_fixed)
    SELECT w.id,
           l.list_key,
           nullif(split_part(i.item, '|', 1), ''),
           split_part(i.item, '|', 2),
           palette[((i.pos - 1) % 10) + 1],
           i.pos,
           coalesce(split_part(i.item, '|', 1) = l.default_key, false),
           l.fixed
    FROM (VALUES
        ('campaign.type', false, NULL, ARRAY[
            '|Webinar', '|Email sequence', '|Event or roundtable', '|LinkedIn or content',
            '|Partner co-sell', '|College outreach', '|Newsletter']),
        ('campaign.status', false, 'planned', ARRAY[
            'planned|Planned', 'live|Live', 'completed|Completed', 'cancelled|Cancelled']),
        ('campaign_member.status', true, 'targeted', ARRAY[
            'targeted|Targeted', 'invited|Invited', 'registered|Registered',
            'attended|Attended', 'engaged|Engaged', 'meeting_booked|Meeting booked',
            'converted|Converted', 'not_interested|Not interested',
            'unsubscribed|Unsubscribed'])
    ) AS l(list_key, fixed, default_key, items)
    CROSS JOIN LATERAL unnest(l.items) WITH ORDINALITY AS i(item, pos)
    CROSS JOIN crm_workspaces w
    ON CONFLICT DO NOTHING;
END;
$$;

-- The registry: (object, field, column_sql, type, is_amount, filterable,
-- sortable, groupable, aggregatable, workspace_scope), as 034.
INSERT INTO crm_object_fields (object, field, column_sql, type, is_amount, filterable, sortable,
                               groupable, aggregatable, workspace_scope)
VALUES
    ('campaign', 'name', 'name', 'text', false, true, true, false, false, 'all'),
    ('campaign', 'type_id', 'type_id', 'select', false, true, true, true, false, 'all'),
    ('campaign', 'status_id', 'status_id', 'select', false, true, true, true, false, 'all'),
    ('campaign', 'channel_id', 'channel_id', 'select', false, true, true, true, false, 'all'),
    ('campaign', 'entity', 'entity', 'fixed_select', false, true, true, true, false, 'tva_linked'),
    ('campaign', 'owner_id', 'owner_id', 'person', false, true, true, true, false, 'all'),
    ('campaign', 'starts_on', 'starts_on', 'date', false, true, true, false, false, 'all'),
    ('campaign', 'ends_on', 'ends_on', 'date', false, true, true, false, false, 'all'),
    ('campaign', 'goal', 'goal', 'long_text', false, true, false, false, false, 'all'),
    ('campaign', 'budget', 'budget', 'currency', true, true, true, false, true, 'all'),
    ('campaign', 'budget_currency', 'budget_currency', 'fixed_select', false, true, true, true, false, 'all'),
    ('campaign', 'budget_in_inr', 'budget_in_inr', 'currency', true, true, true, false, true, 'all'),
    ('campaign', 'budget_fx_rate', 'budget_fx_rate', 'number', true, true, true, false, false, 'all'),
    ('campaign', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('campaign', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all'),
    ('campaign', 'position', 'position', 'number', false, false, true, false, false, 'all'),
    ('campaign_member', 'campaign_id', 'campaign_id', 'relation', false, true, true, true, false, 'all'),
    ('campaign_member', 'company_id', 'company_id', 'relation', false, true, true, true, false, 'all'),
    ('campaign_member', 'person_id', 'person_id', 'relation', false, true, true, true, false, 'all'),
    ('campaign_member', 'status', 'status', 'fixed_select', false, true, true, true, false, 'all'),
    ('campaign_member', 'last_touch_on', 'last_touch_on', 'date', false, true, true, false, false, 'all'),
    ('campaign_member', 'notes', 'notes', 'long_text', false, true, false, false, false, 'all'),
    ('campaign_member', 'converted_opportunity_id', 'converted_opportunity_id', 'relation', false, true, true, true, false, 'all'),
    ('campaign_member', 'created_at', 'created_at', 'datetime', false, true, true, false, false, 'all'),
    ('campaign_member', 'updated_at', 'updated_at', 'datetime', false, true, true, false, false, 'all'),
    ('campaign_member', 'position', 'position', 'number', false, false, true, false, false, 'all')
ON CONFLICT (object, field) DO NOTHING;

-- FR-CRM-VIEW-11 (1b): Members by status, as 034's rows are written.
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
    ('members_by_status', 'campaign_member', 'Members by status', 'kanban', 'kanban', true, 1,
     '{"filters": {"op": "and", "items": []}, "sorts": [{"field": "position", "dir": "asc"}], "kanban_field": "status", "card_fields": ["company_id", "person_id", "last_touch_on"]}')
    ) AS v(system_key, object, name, icon, kind, is_default, position, config)
    CROSS JOIN crm_workspaces w
    ON CONFLICT DO NOTHING;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_query_rank, crm_query — data-model §5.8 (038's and 034's, plus
--    campaigns and members)
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
        -- share the column name and no key, so one array orders both.
        WHEN p_type = 'fixed_select' AND p_column_sql = 'status' THEN format(
            'array_position(''{todo,in_progress,done,targeted,invited,registered,attended,'
            'engaged,meeting_booked,converted,not_interested,unsubscribed}''::text[], %s)',
            p_expr)
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
-- 7. crm_search — data-model §5.9, second form (035's, plus campaigns)
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
-- 8. crm_merge_companies — data-model §5.7, third form (039's, plus campaign
--    members)
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
    FOR r IN
        SELECT * FROM crm_campaign_members
        WHERE workspace_id = ws AND company_id = gone_id
        ORDER BY id
    LOOP
        IF EXISTS (SELECT 1 FROM crm_campaign_members
                   WHERE campaign_id = r.campaign_id AND company_id = keep_id
                     AND person_id IS NOT DISTINCT FROM r.person_id) THEN
            DELETE FROM crm_campaign_members WHERE id = r.id;
        ELSE
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
-- 9. crm_purge — data-model §5.4, sixth form (039's, plus campaigns)
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
            DELETE FROM crm_task_links
            WHERE workspace_id = ws AND record_type = rtype AND record_id = target;
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
        IF tbl = 'crm_companies' THEN
            DELETE FROM crm_campaign_members m
            WHERE m.workspace_id = ws AND m.company_id = target AND m.person_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM crm_campaign_members o
                          WHERE o.campaign_id = m.campaign_id AND o.company_id IS NULL
                            AND o.person_id = m.person_id);
            UPDATE crm_campaign_members SET company_id = NULL
            WHERE workspace_id = ws AND company_id = target AND person_id IS NOT NULL;
        ELSIF tbl = 'crm_people' THEN
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
-- Grants — data-model §5.12
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_query_rank(text, text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_query(uuid, text, jsonb, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_search(uuid, text, integer, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_merge_companies(jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_query_rank(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_query(uuid, text, jsonb, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_search(uuid, text, integer, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_merge_companies(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
