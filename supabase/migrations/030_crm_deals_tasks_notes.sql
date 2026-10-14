-- 030_crm_deals_tasks_notes.sql
--
-- Spec 007 (sales CRM) FR-CRM-OPP-01 to -03, FR-CRM-TASK-01, -02,
-- FR-CRM-NOTE-01 to -03, FR-CRM-LINK-01, FR-CRM-FX-01, -03, -04,
-- FR-CRM-PIPE-07, -10, -14, FR-CRM-DEL-01, -02, FR-CRM-WS-02; Q-27, Q-31,
-- Q-53, Q-57, Q-59, Q-63; owner decision D7; data-model §2, §3.9–§3.13,
-- §5.4.
--
-- Deals, their contacts and stage history, tasks and notes. Stage moves and
-- their side effects arrive in 031 (`crm_deal_transition`); the TVA side of
-- a Labs deal in 032 and 033.
--
-- 1. Eight tables, each following data-model §2: RLS on with no policy and
--    no browser grant (FR-CRM-ACC-10); `UNIQUE (workspace_id, id)` on every
--    table another one references; every reference between CRM tables,
--    select options included, a composite foreign key on
--    (workspace_id, x_id), so nothing in one workspace points into another
--    (FR-CRM-WS-02).
--    - `crm_opportunities`: a deal. The pipeline rules are decided in Python
--      (domain/crm/pipeline.py) and written through 031; the CHECKs below
--      are the backstop that makes them hold "through every channel"
--      (FR-CRM-PIPE-10): an open deal has a next step and its date, a lost
--      one a reason, and a deal at Proposal or later an amount and an
--      expected close date (imported deals excepted, which the importer's
--      dry run reports instead, FR-CRM-MIG-04). `amount_in_inr` is stored on
--      every amount or currency save at the month's rate (FR-CRM-FX-03,
--      Q-59), and three FX CHECKs keep it equal to `round(amount *
--      fx_rate_used, 2)`, so a path that sets an amount without converting
--      it cannot leave a wrong INR value for every total to add up. There is
--      no TVA project column: S5 links projects to deals from the TVA side.
--    - `crm_opportunity_contacts`: other people on the deal, each with their
--      Role in deal, which lives on this link only, so one person can hold a
--      different role on each deal.
--    - `crm_stage_history`: every stage a deal entered, when and by whom
--      (FR-CRM-OPP-02, -PIPE-14). A log, not a record: no audit trigger.
--      Written only by `crm_apply` (a deal's first stage) and
--      `crm_deal_transition` (every move).
--    - `crm_tasks`, `crm_task_links`: a task links to at least one company,
--      person, deal or (1b) campaign (FR-CRM-TASK-01). The link is
--      polymorphic, so `crm_check_refs()` (027) checks that its target is in
--      the link's own workspace; "at least one" is checked in Python and
--      again by `crm_apply` for task inserts. `completed_at` is set exactly
--      when the status is Done.
--    - `crm_notes`, `crm_note_links`, `crm_note_mentions`: a note in plain
--      text with markdown (Q-27), on any number of records; a typed note
--      (call, meeting, WhatsApp, other) is a logged activity and carries the
--      date it happened (FR-CRM-NOTE-03, Q-53). Mentions name members of the
--      workspace, checked in Python (Q-63); record mentions are note links.
--
-- 2. `crm_audit_row()` is replaced to record one more referential action as
--    the system: a note mention removed because the mentioned profile was
--    deleted, like a membership in 026. Without it, deleting a mentioned
--    profile would be refused as an unaudited CRM change.
--
-- 3. `crm_purge()`, second form (data-model §5.4): it purges deals, tasks and
--    notes too, and removes the task and note links that point at a purged
--    record, besides its Drive links. A purged task or note takes its own
--    links and mentions with it (ON DELETE CASCADE); a purged deal its
--    contacts and history. A company's deals are purged before it.
--
-- Reversible: restore 029's crm_purge() and 026's crm_audit_row(); DROP
-- TABLE crm_note_mentions, crm_note_links, crm_notes, crm_task_links,
-- crm_tasks, crm_stage_history, crm_opportunity_contacts, crm_opportunities.
-- Their audit rows stay: history is append-only (005).

-- ---------------------------------------------------------------------------
-- 1. crm_opportunities — data-model §3.9
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_opportunities (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    name                text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 200),
    company_id          uuid NOT NULL,
    -- Required from Qualification (Python, FR-CRM-PIPE-07).
    point_of_contact_id uuid,
    -- Labs only (FR-CRM-WS-06), refused elsewhere in Python.
    entity              text CHECK (entity IN ('nunnari_labs', 'deepweaver')),
    stage               text NOT NULL DEFAULT 'discovery' CHECK (stage IN (
                            'discovery', 'qualification', 'proposal', 'negotiation',
                            'closed_won', 'closed_lost'
                        )),
    stage_entered_at    timestamptz NOT NULL DEFAULT now(),
    amount              numeric(14,2) CHECK (amount >= 0),
    currency            text CHECK (currency IN ('INR', 'AUD', 'USD')),
    amount_in_inr       numeric(14,2) CHECK (amount_in_inr >= 0),
    fx_rate_used        numeric(12,6) CHECK (fx_rate_used > 0),
    fx_month            date CHECK (extract(day FROM fx_month) = 1),
    -- FR-CRM-FX-05 (S5): the rate in force on the won date.
    won_fx_rate         numeric(12,6) CHECK (won_fx_rate > 0),
    won_fx_month        date CHECK (extract(day FROM won_fx_month) = 1),
    probability         smallint NOT NULL CHECK (probability BETWEEN 0 AND 100),
    expected_close_on   date,
    next_step           text CHECK (char_length(next_step) <= 500),
    next_step_on        date,
    owner_id            uuid NOT NULL REFERENCES profiles(id),
    channel_id          uuid,
    deal_type_id        uuid,
    engagement_model_id uuid,
    -- Academy only (Q-40).
    programme_id        uuid,
    lost_reason_id      uuid,
    -- Labs only; carried into the TVA project (S5).
    project_category    text CHECK (project_category IN ('client', 'poc', 'product')),
    proposal_sent_on    date,
    lost_notes          text CHECK (char_length(lost_notes) <= 20000),
    -- FR-CRM-PIPE-12: a reopen from Lost goes back here.
    lost_from_stage     text CHECK (lost_from_stage IN (
                            'discovery', 'qualification', 'proposal', 'negotiation'
                        )),
    -- System-written.
    won_on              date,
    lost_on             date,
    twenty_id           text,
    position            numeric NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at          timestamptz,
    deleted_by          uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group       uuid,
    CONSTRAINT crm_opportunities_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_opportunities_company FOREIGN KEY (workspace_id, company_id)
        REFERENCES crm_companies (workspace_id, id),
    CONSTRAINT crm_opportunities_point_of_contact
        FOREIGN KEY (workspace_id, point_of_contact_id)
        REFERENCES crm_people (workspace_id, id) ON DELETE SET NULL (point_of_contact_id),
    CONSTRAINT crm_opportunities_channel FOREIGN KEY (workspace_id, channel_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_opportunities_deal_type FOREIGN KEY (workspace_id, deal_type_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_opportunities_engagement_model
        FOREIGN KEY (workspace_id, engagement_model_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_opportunities_programme FOREIGN KEY (workspace_id, programme_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_opportunities_lost_reason FOREIGN KEY (workspace_id, lost_reason_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_opportunities_amount_currency CHECK ((amount IS NULL) = (currency IS NULL)),
    CONSTRAINT crm_opportunities_amount_in_inr CHECK ((amount IS NULL) = (amount_in_inr IS NULL)),
    -- Backstops for the pipeline rules (FR-CRM-PIPE-07, -10).
    CONSTRAINT crm_opportunities_open_next_step CHECK (
        deleted_at IS NOT NULL OR stage IN ('closed_won', 'closed_lost')
        OR (next_step IS NOT NULL AND btrim(next_step) <> '' AND next_step_on IS NOT NULL)
    ),
    CONSTRAINT crm_opportunities_lost_reason_required
        CHECK (stage <> 'closed_lost' OR lost_reason_id IS NOT NULL),
    CONSTRAINT crm_opportunities_proposal_fields CHECK (
        stage NOT IN ('proposal', 'negotiation', 'closed_won') OR deleted_at IS NOT NULL
        OR (amount IS NOT NULL AND expected_close_on IS NOT NULL) OR twenty_id IS NOT NULL
    ),
    -- FX integrity (FR-CRM-FX-03). round(numeric, 2) rounds half away from
    -- zero, which is Python's half-up for these non-negative amounts.
    CONSTRAINT crm_opportunities_fx_rate CHECK ((amount IS NULL) = (fx_rate_used IS NULL)),
    CONSTRAINT crm_opportunities_fx_inr
        CHECK (amount IS NULL OR amount_in_inr = round(amount * fx_rate_used, 2)),
    CONSTRAINT crm_opportunities_fx_inr_at_one
        CHECK (currency IS NULL OR (currency = 'INR') = (fx_rate_used = 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_opportunities_twenty
    ON crm_opportunities (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_stage
    ON crm_opportunities (workspace_id, stage, position) WHERE deleted_at IS NULL;
-- Today §2: open deals by owner and next-step date.
CREATE INDEX IF NOT EXISTS crm_opportunities_next_step
    ON crm_opportunities (workspace_id, owner_id, next_step_on)
    WHERE stage NOT IN ('closed_won', 'closed_lost') AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_company
    ON crm_opportunities (workspace_id, company_id);
CREATE INDEX IF NOT EXISTS crm_opportunities_close
    ON crm_opportunities (workspace_id, expected_close_on) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_won
    ON crm_opportunities (workspace_id, won_on) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_lost
    ON crm_opportunities (workspace_id, lost_on) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_updated
    ON crm_opportunities (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_point_of_contact
    ON crm_opportunities (workspace_id, point_of_contact_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_name_trgm
    ON crm_opportunities USING gin (lower(name) extensions.gin_trgm_ops)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_opportunities_trash
    ON crm_opportunities (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. crm_opportunity_contacts — data-model §3.10
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_opportunity_contacts (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    opportunity_id uuid NOT NULL,
    person_id      uuid NOT NULL,
    role_id        uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_opportunity_contacts_opportunity FOREIGN KEY (workspace_id, opportunity_id)
        REFERENCES crm_opportunities (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_opportunity_contacts_person FOREIGN KEY (workspace_id, person_id)
        REFERENCES crm_people (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_opportunity_contacts_role FOREIGN KEY (workspace_id, role_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_opportunity_contacts_pair UNIQUE (opportunity_id, person_id)
);

CREATE INDEX IF NOT EXISTS crm_opportunity_contacts_person
    ON crm_opportunity_contacts (workspace_id, person_id);

-- ---------------------------------------------------------------------------
-- 3. crm_stage_history — data-model §3.11 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_stage_history (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    opportunity_id uuid NOT NULL,
    from_stage     text,
    to_stage       text NOT NULL,
    at             timestamptz NOT NULL DEFAULT now(),
    "by"           uuid REFERENCES profiles(id) ON DELETE SET NULL,
    actor_kind     text CHECK (actor_kind IN ('user', 'system', 'import', 'sync')),
    CONSTRAINT crm_stage_history_opportunity FOREIGN KEY (workspace_id, opportunity_id)
        REFERENCES crm_opportunities (workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS crm_stage_history_opportunity
    ON crm_stage_history (workspace_id, opportunity_id, at);

-- ---------------------------------------------------------------------------
-- 4. crm_tasks, crm_task_links — data-model §3.12
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_tasks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    title         text NOT NULL CHECK (btrim(title) <> '' AND char_length(title) <= 200),
    body          text CHECK (char_length(body) <= 20000),
    status        text NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'in_progress', 'done')),
    due_on        date,
    bucket        text CHECK (bucket IN ('today', 'this_week', 'this_month', 'later')),
    category_id   uuid,
    assignee_id   uuid NOT NULL REFERENCES profiles(id),
    completed_at  timestamptz,
    position      numeric NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at    timestamptz,
    deleted_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group uuid,
    CONSTRAINT crm_tasks_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_tasks_category FOREIGN KEY (workspace_id, category_id)
        REFERENCES crm_select_options (workspace_id, id),
    CONSTRAINT crm_tasks_completed CHECK ((status = 'done') = (completed_at IS NOT NULL))
);

-- Today §3: a person's open tasks by due date.
CREATE INDEX IF NOT EXISTS crm_tasks_assignee
    ON crm_tasks (workspace_id, assignee_id, status, due_on) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_tasks_bucket
    ON crm_tasks (workspace_id, bucket, position) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_tasks_updated
    ON crm_tasks (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_tasks_trash
    ON crm_tasks (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_task_links (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    task_id      uuid NOT NULL,
    record_type  text NOT NULL
                 CHECK (record_type IN ('company', 'person', 'opportunity', 'campaign')),
    record_id    uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_task_links_task FOREIGN KEY (workspace_id, task_id)
        REFERENCES crm_tasks (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_task_links_target UNIQUE (task_id, record_type, record_id)
);

CREATE INDEX IF NOT EXISTS crm_task_links_record
    ON crm_task_links (workspace_id, record_type, record_id);

-- ---------------------------------------------------------------------------
-- 5. crm_notes, crm_note_links, crm_note_mentions — data-model §3.13
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_notes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    body          text NOT NULL CHECK (btrim(body) <> '' AND char_length(body) <= 20000),
    kind          text NOT NULL DEFAULT 'note'
                  CHECK (kind IN ('note', 'call', 'meeting', 'whatsapp', 'other')),
    -- FR-CRM-NOTE-03: a logged activity says when it happened.
    occurred_on   date,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_at    timestamptz,
    deleted_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
    deleted_group uuid,
    CONSTRAINT crm_notes_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_notes_activity_date CHECK (kind = 'note' OR occurred_on IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS crm_notes_created
    ON crm_notes (workspace_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_notes_updated
    ON crm_notes (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_notes_trash
    ON crm_notes (workspace_id, deleted_at) WHERE deleted_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_note_links (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    note_id      uuid NOT NULL,
    record_type  text NOT NULL
                 CHECK (record_type IN ('company', 'person', 'opportunity', 'campaign')),
    record_id    uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_note_links_note FOREIGN KEY (workspace_id, note_id)
        REFERENCES crm_notes (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_note_links_target UNIQUE (note_id, record_type, record_id)
);

CREATE INDEX IF NOT EXISTS crm_note_links_record
    ON crm_note_links (workspace_id, record_type, record_id);

CREATE TABLE IF NOT EXISTS crm_note_mentions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    note_id      uuid NOT NULL,
    member_id    uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_note_mentions_note FOREIGN KEY (workspace_id, note_id)
        REFERENCES crm_notes (workspace_id, id) ON DELETE CASCADE,
    CONSTRAINT crm_note_mentions_pair UNIQUE (note_id, member_id)
);

CREATE INDEX IF NOT EXISTS crm_note_mentions_member
    ON crm_note_mentions (workspace_id, member_id);

-- ---------------------------------------------------------------------------
-- 6. Triggers, RLS, privileges — data-model §2
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS crm_opportunities_touch ON crm_opportunities;
CREATE TRIGGER crm_opportunities_touch BEFORE UPDATE ON crm_opportunities
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_tasks_touch ON crm_tasks;
CREATE TRIGGER crm_tasks_touch BEFORE UPDATE ON crm_tasks
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS crm_notes_touch ON crm_notes;
CREATE TRIGGER crm_notes_touch BEFORE UPDATE ON crm_notes
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- Polymorphic links (data-model §5.14).
DROP TRIGGER IF EXISTS crm_task_links_check_refs ON crm_task_links;
CREATE TRIGGER crm_task_links_check_refs BEFORE INSERT OR UPDATE ON crm_task_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();
DROP TRIGGER IF EXISTS crm_note_links_check_refs ON crm_note_links;
CREATE TRIGGER crm_note_links_check_refs BEFORE INSERT OR UPDATE ON crm_note_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();

DROP TRIGGER IF EXISTS crm_opportunities_crm_audit ON crm_opportunities;
CREATE TRIGGER crm_opportunities_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_opportunities
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_opportunity_contacts_crm_audit ON crm_opportunity_contacts;
CREATE TRIGGER crm_opportunity_contacts_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_opportunity_contacts
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_tasks_crm_audit ON crm_tasks;
CREATE TRIGGER crm_tasks_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_tasks
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_task_links_crm_audit ON crm_task_links;
CREATE TRIGGER crm_task_links_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_task_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_notes_crm_audit ON crm_notes;
CREATE TRIGGER crm_notes_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_notes
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_note_links_crm_audit ON crm_note_links;
CREATE TRIGGER crm_note_links_crm_audit AFTER INSERT OR UPDATE OR DELETE ON crm_note_links
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();
DROP TRIGGER IF EXISTS crm_note_mentions_crm_audit ON crm_note_mentions;
CREATE TRIGGER crm_note_mentions_crm_audit
    AFTER INSERT OR UPDATE OR DELETE ON crm_note_mentions
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE crm_opportunities        ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_opportunity_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_stage_history        ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_tasks                ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_task_links           ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_notes                ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_note_links           ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_note_mentions        ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON crm_opportunities, crm_opportunity_contacts, crm_stage_history, crm_tasks,
    crm_task_links, crm_notes, crm_note_links, crm_note_mentions
    FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 7. crm_audit_row — 026's function, plus the note-mention cascade
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
    -- the note mentions naming it, and clears created_by on what it authored,
    -- with no actor set. Those referential actions are recorded as the
    -- system, not refused, so a TVA account can still be deleted. They are
    -- told apart by the profile being gone already: a direct write about a
    -- person who still exists is not one.
    IF actor IS NULL
       AND ((TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_memberships'
             AND NOT EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_note_mentions'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'member_id')::uuid))
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

REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 8. crm_purge — data-model §5.4, second form (replaced in 033, 034, 039)
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

REVOKE ALL ON FUNCTION public.crm_purge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_purge(jsonb, jsonb) TO service_role;
