-- 066_crm_follow_up_rules.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-RULE-01 to -08,
-- FR-CRMAI-JOB-01, -05, FR-CRMAI-CHG-05, -06, -08; Q-14 to Q-16, Q-29, Q-39,
-- Q-40; D-4; data-model §3.7, §3.8, §3.9, §3.11, §5; plan §5.9.
--
-- The four follow-up rules (R-1 No reply after outreach, R-2 Stuck in stage,
-- R-3 Lead gone cold, R-4 Campaign follow-through) are extensions of 007's
-- flags and automations, each behind its own switch, seeded off, and phase 3.
--
-- 1. Settings (FR-CRMAI-RULE-01, data-model §3.7). The four rule switches
--    `no_reply_draft`, `stuck_notice`, `lead_cold` and
--    `campaign_followthrough` are merged, off, into the `automations` row of
--    EVERY workspace, and the six whole-day parameters into its
--    `flag_thresholds` row: `defaults || value`, so a key a row already has
--    keeps its stored value. Every row is updated, not only the seeds,
--    because 037's seeds are `ON CONFLICT DO NOTHING` and `automation_on`
--    reads a missing key as on. A workspace with no row gets one carrying
--    every key. Written under the system actor, as 037's seeds are.
-- 2. `crm_workspaces_seed_ai()` (060's) replaced: besides `ai`, a workspace
--    created later gets `automations` (037's defaults and the four rule
--    switches off) and `flag_thresholds` (037's defaults and the six
--    parameters), `ON CONFLICT DO NOTHING`, so every rule reads off there.
-- 3. `crm_campaign_members.status_changed_at` (FR-CRMAI-RULE-06, Q-16): when
--    a member's status last changed, from which R-4 measures its 7 days.
--    Backfilled from the latest audit row whose after-values carry the
--    member's status, else `updated_at`, with the table's two row triggers
--    (`crm_campaign_members_touch`, `crm_campaign_members_crm_audit`, 041)
--    disabled for the backfill only: otherwise `touch_updated_at()` would
--    move every member's `updated_at` and `crm_audit_row` would write one
--    `campaign_member.updated` row per member. The migration is one
--    transaction, so the triggers cannot stay disabled. Then NOT NULL with
--    DEFAULT now(), and the BEFORE UPDATE OF status trigger
--    `crm_campaign_members_status_changed`, which moves it only when the
--    status changes: every write path (PATCH, convert, import, merge) is
--    covered without touching them.
-- 4. `crm_notifications.kind` CHECK widened with `stuck_in_stage`, R-2's
--    bell event (FR-CRMAI-RULE-04, FR-CRMAI-CHG-08).
-- 5. `crm_job_runs.job` CHECK widened with `followup_drafts`, the R-1 drafts'
--    job (FR-CRMAI-JOB-01, D-4), and `crm_job_latest()` replaced so it
--    reports it (a job that never ran is still not reported, 007
--    FR-CRM-JOB-03).
-- 6. `crm_lead_activity(p_workspace, p_company_ids)` (R-3, Q-15): per
--    company, the latest of its last contact, a note linked to it, a task
--    linked to it created or completed, a lead stage change, and activity on
--    any of its deals (their update, stage entry and stage history, linked
--    notes and tasks). Tasks are 008's shared `tasks` and `task_links`
--    (047).
-- 7. `crm_no_reply_candidates(p_workspace, p_before)` (R-1): outbound counted
--    messages (not internal, not an auto-reply or bounce) sent before
--    `p_before` that are the latest counted message of their thread and
--    have no later inbound on any linked live person or company, with those
--    people and companies. Ids only.
--
-- The two CHECKs are the inline column CHECKs 060 last rebuilt, dropped by
-- name and re-added under the same names with the new value added (060's
-- lists; checked against the rebased tree in T134: no 008 migration, 046 to
-- 050 and 056, rebuilds either). `crm_job_latest()` and
-- `crm_workspaces_seed_ai()` are copied from their latest definitions,
-- 060_crm_ai_foundations.sql (`grep -l "CREATE OR REPLACE FUNCTION
-- public.<name>(" supabase/migrations/*.sql | sort | tail -1`), with only
-- the lines marked "066" added.
--
-- EXECUTE on every function here is revoked from PUBLIC, anon and
-- authenticated and granted to service_role (data-model §5).
--
-- Reversible: remove the merged keys from `automations` and
-- `flag_thresholds` (`value - '{no_reply_draft, stuck_notice, lead_cold,
-- campaign_followthrough}'::text[]` and the six parameters, under a system
-- actor); re-run 060's crm_workspaces_seed_ai(); DROP TRIGGER
-- crm_campaign_members_status_changed, DROP FUNCTION
-- crm_campaign_members_status_changed(), DROP COLUMN status_changed_at;
-- restore 060's kind and job CHECKs (after deleting rows using the new
-- values) and 060's crm_job_latest(); DROP FUNCTION crm_lead_activity(uuid,
-- uuid[]), crm_no_reply_candidates(uuid, timestamptz).

-- ---------------------------------------------------------------------------
-- 1. Settings — data-model §3.7 (FR-CRMAI-RULE-01)
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    rule_switches jsonb := '{"no_reply_draft": false, "stuck_notice": false,
                             "lead_cold": false, "campaign_followthrough": false}';
    rule_days     jsonb := '{"stuck_discovery_days": 14, "stuck_qualification_days": 21,
                             "stuck_proposal_days": 14, "stuck_negotiation_days": 21,
                             "no_reply_days": 5, "lead_cold_days": 30}';
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- Every row: missing keys added, stored values kept. A row that has
    -- every key already is left alone, so a re-run changes nothing.
    UPDATE crm_workspace_settings
    SET value = rule_switches || value
    WHERE key = 'automations'
      AND NOT value ?& ARRAY['no_reply_draft', 'stuck_notice', 'lead_cold',
                             'campaign_followthrough'];
    UPDATE crm_workspace_settings
    SET value = rule_days || value
    WHERE key = 'flag_thresholds'
      AND NOT value ?& ARRAY['stuck_discovery_days', 'stuck_qualification_days',
                             'stuck_proposal_days', 'stuck_negotiation_days',
                             'no_reply_days', 'lead_cold_days'];

    -- A workspace with no row gets every key: 037's defaults and the new
    -- ones off.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'automations',
           '{"email_records": true, "stale_notice": true, "digests": true,
             "task_reminders": true, "campaign_conversion": true}'::jsonb || rule_switches
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'flag_thresholds',
           '{"reply_owed_red_hours": 24, "reply_owed_amber_hours": 12,
             "silent_red_days": 3, "silent_amber_days": 2, "close_amber_days": 7,
             "stale_red_days": 21, "stale_amber_days": 14}'::jsonb || rule_days
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. crm_workspaces_seed_ai — 060's, also seeding the two settings
-- ---------------------------------------------------------------------------

-- A workspace created later gets the same rows, written as the system: the
-- actor (and action) that created the workspace are put back afterwards.
CREATE OR REPLACE FUNCTION public.crm_workspaces_seed_ai()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    was_actor  text := current_setting('crm.actor', true);
    was_action text := current_setting('crm.action', true);
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );
    PERFORM set_config('crm.action', '', true);
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'ai',
            '{"budget_usd": 0, "phases": {"1": false, "2": false, "3": false, "4": false}}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    -- 066 (FR-CRMAI-RULE-01): 037's switches and thresholds, with the four
    -- rule switches off and the six rule parameters.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'automations',
            '{"email_records": true, "stale_notice": true, "digests": true,
              "task_reminders": true, "campaign_conversion": true,
              "no_reply_draft": false, "stuck_notice": false, "lead_cold": false,
              "campaign_followthrough": false}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'flag_thresholds',
            '{"reply_owed_red_hours": 24, "reply_owed_amber_hours": 12,
              "silent_red_days": 3, "silent_amber_days": 2, "close_amber_days": 7,
              "stale_red_days": 21, "stale_amber_days": 14,
              "stuck_discovery_days": 14, "stuck_qualification_days": 21,
              "stuck_proposal_days": 14, "stuck_negotiation_days": 21,
              "no_reply_days": 5, "lead_cold_days": 30}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    PERFORM set_config('crm.actor', coalesce(was_actor, ''), true);
    PERFORM set_config('crm.action', coalesce(was_action, ''), true);
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. crm_campaign_members.status_changed_at — data-model §3.9
-- ---------------------------------------------------------------------------

ALTER TABLE crm_campaign_members ADD COLUMN IF NOT EXISTS status_changed_at timestamptz;

-- The backfill is not an edit: no `updated_at` moves and no audit row is
-- written (the triggers come back on before this transaction ends).
ALTER TABLE crm_campaign_members
    DISABLE TRIGGER crm_campaign_members_touch,
    DISABLE TRIGGER crm_campaign_members_crm_audit;

UPDATE crm_campaign_members m
SET status_changed_at = coalesce(
    (
        SELECT max(a.at)
        FROM audit_log a
        WHERE a.target_table = 'crm_campaign_members'
          AND a.target_id = m.id::text
          AND a.after ? 'status'
    ),
    m.updated_at
)
WHERE m.status_changed_at IS NULL;

ALTER TABLE crm_campaign_members
    ENABLE TRIGGER crm_campaign_members_touch,
    ENABLE TRIGGER crm_campaign_members_crm_audit;

ALTER TABLE crm_campaign_members
    ALTER COLUMN status_changed_at SET DEFAULT now(),
    ALTER COLUMN status_changed_at SET NOT NULL;

-- Moved only by a status change, whichever path made it.
CREATE OR REPLACE FUNCTION public.crm_campaign_members_status_changed()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_changed_at := now();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS crm_campaign_members_status_changed ON crm_campaign_members;
CREATE TRIGGER crm_campaign_members_status_changed
    BEFORE UPDATE OF status ON crm_campaign_members
    FOR EACH ROW EXECUTE FUNCTION public.crm_campaign_members_status_changed();

-- ---------------------------------------------------------------------------
-- 4. Notification kind — data-model §3.8 (FR-CRMAI-RULE-04, -CHG-08)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_notifications DROP CONSTRAINT IF EXISTS crm_notifications_kind_check;
ALTER TABLE crm_notifications ADD CONSTRAINT crm_notifications_kind_check
    CHECK (kind IN (
        'assigned', 'mentioned', 'stage_changed', 'reply_owed_red',
        'handoff_waiting', 'revenue_confirm', 'task_due', 'task_overdue',
        'stale_deal', 'job_failed', 'invited', 'ai_budget_reached',
        'stuck_in_stage'
    ));

-- ---------------------------------------------------------------------------
-- 5. Job name — data-model §3.8 (FR-CRMAI-JOB-01, D-4)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_job_runs DROP CONSTRAINT IF EXISTS crm_job_runs_job_check;
ALTER TABLE crm_job_runs ADD CONSTRAINT crm_job_runs_job_check
    CHECK (job IN (
        'email_sync', 'flags', 'digests', 'reminders', 'trash_purge', 'automations',
        'summaries', 'meetings', 'enrichment', 'ai_fields', 'review_expiry',
        'followup_drafts'
    ));

-- 060's function, with `followup_drafts` added to its array.
CREATE OR REPLACE FUNCTION public.crm_job_latest()
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'job', last.job, 'workspace_id', last.workspace_id,
               'started_at', last.started_at, 'finished_at', last.finished_at,
               'outcome', last.outcome)
           ORDER BY last.job, last.workspace_id), '[]'::jsonb)
    FROM (
        SELECT j.job, w.id AS workspace_id
        FROM unnest(ARRAY['email_sync', 'flags', 'reminders', 'trash_purge', 'automations',
                          'summaries', 'meetings', 'enrichment', 'ai_fields', 'review_expiry',
                          -- 066 (D-4)
                          'followup_drafts'])
             AS j(job)
        CROSS JOIN crm_workspaces w
        UNION ALL
        SELECT 'digests', NULL
    ) AS pair
    CROSS JOIN LATERAL (
        SELECT r.job, r.workspace_id, r.started_at, r.finished_at, r.outcome
        FROM crm_job_runs r
        WHERE r.job = pair.job AND r.workspace_id IS NOT DISTINCT FROM pair.workspace_id
        ORDER BY r.started_at DESC
        LIMIT 1
    ) AS last;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_lead_activity — data-model §3.11 (R-3, Q-15)
-- ---------------------------------------------------------------------------

-- {company id: the latest activity time, or null when there is none}, for
-- some live companies of the workspace, or (NULL) all of them.
CREATE OR REPLACE FUNCTION public.crm_lead_activity(p_workspace uuid, p_company_ids uuid[])
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    WITH companies AS (
        SELECT c.id, c.last_contact_at
        FROM crm_companies c
        WHERE c.workspace_id = p_workspace AND c.deleted_at IS NULL
          AND (p_company_ids IS NULL OR c.id = ANY (p_company_ids))
    ), deals AS (
        SELECT o.id, o.company_id, greatest(o.updated_at, o.stage_entered_at) AS at
        FROM crm_opportunities o
        WHERE o.workspace_id = p_workspace AND o.deleted_at IS NULL
          AND o.company_id IN (SELECT id FROM companies)
    ), note_at AS (
        SELECT l.record_type, l.record_id,
               max(CASE WHEN n.kind <> 'note' AND n.occurred_on IS NOT NULL
                        THEN n.occurred_on::timestamptz ELSE n.created_at END) AS at
        FROM crm_note_links l
        JOIN crm_notes n ON n.workspace_id = l.workspace_id AND n.id = l.note_id
        WHERE l.workspace_id = p_workspace AND n.deleted_at IS NULL
          AND ((l.record_type = 'company' AND l.record_id IN (SELECT id FROM companies))
            OR (l.record_type = 'opportunity' AND l.record_id IN (SELECT id FROM deals)))
        GROUP BY l.record_type, l.record_id
    ), task_at AS (
        SELECT l.record_type, l.record_id, max(greatest(t.created_at, t.completed_at)) AS at
        FROM task_links l
        JOIN tasks t ON t.workspace_id = l.workspace_id AND t.id = l.task_id
        WHERE l.workspace_id = p_workspace AND t.deleted_at IS NULL
          AND ((l.record_type = 'company' AND l.record_id IN (SELECT id FROM companies))
            OR (l.record_type = 'opportunity' AND l.record_id IN (SELECT id FROM deals)))
        GROUP BY l.record_type, l.record_id
    ), history_at AS (
        SELECT h.opportunity_id AS id, max(h.at) AS at
        FROM crm_stage_history h
        WHERE h.workspace_id = p_workspace AND h.opportunity_id IN (SELECT id FROM deals)
        GROUP BY h.opportunity_id
    ), stage_at AS (
        SELECT a.target_id, max(a.at) AS at
        FROM audit_log a
        WHERE a.target_table = 'crm_companies' AND a.action = 'company.updated'
          AND a.after ? 'lead_stage'
          AND a.target_id IN (SELECT id::text FROM companies)
        GROUP BY a.target_id
    ), deal_at AS (
        SELECT d.company_id AS id, max(greatest(d.at, h.at, n.at, t.at)) AS at
        FROM deals d
        LEFT JOIN history_at h ON h.id = d.id
        LEFT JOIN note_at n ON n.record_type = 'opportunity' AND n.record_id = d.id
        LEFT JOIN task_at t ON t.record_type = 'opportunity' AND t.record_id = d.id
        GROUP BY d.company_id
    )
    SELECT coalesce(jsonb_object_agg(
               c.id, greatest(c.last_contact_at, n.at, t.at, s.at, d.at)), '{}'::jsonb)
    FROM companies c
    LEFT JOIN note_at n ON n.record_type = 'company' AND n.record_id = c.id
    LEFT JOIN task_at t ON t.record_type = 'company' AND t.record_id = c.id
    LEFT JOIN stage_at s ON s.target_id = c.id::text
    LEFT JOIN deal_at d ON d.id = c.id;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_no_reply_candidates — data-model §3.11 (R-1)
-- ---------------------------------------------------------------------------

-- [{mailbox_id, thread_id, gmail_message_id, sent_at, people: [id],
-- companies: [id]}], oldest first. Ids only: no subject, address or body.
CREATE OR REPLACE FUNCTION public.crm_no_reply_candidates(p_workspace uuid, p_before timestamptz)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    WITH counted AS (
        SELECT m.id, m.mailbox_id, m.thread_id, m.gmail_message_id, m.sent_at, m.direction
        FROM crm_email_messages m
        WHERE m.workspace_id = p_workspace AND NOT m.is_internal AND NOT m.is_auto
    ), latest AS (
        SELECT DISTINCT ON (c.mailbox_id, c.thread_id) c.*
        FROM counted c
        ORDER BY c.mailbox_id, c.thread_id, c.sent_at DESC, c.gmail_message_id DESC
    ), outbound AS (
        SELECT l.* FROM latest l WHERE l.direction = 'outbound' AND l.sent_at < p_before
    ), linked AS (
        SELECT o.id AS message_id, o.sent_at, el.record_type, el.record_id
        FROM outbound o
        JOIN crm_email_links el ON el.workspace_id = p_workspace AND el.message_id = o.id
        WHERE (el.record_type = 'company' AND EXISTS (
                  SELECT 1 FROM crm_companies c
                  WHERE c.workspace_id = p_workspace AND c.id = el.record_id
                    AND c.deleted_at IS NULL))
           OR (el.record_type = 'person' AND EXISTS (
                  SELECT 1 FROM crm_people p
                  WHERE p.workspace_id = p_workspace AND p.id = el.record_id
                    AND p.deleted_at IS NULL))
    ), answered AS (
        SELECT DISTINCT lk.message_id
        FROM linked lk
        JOIN crm_email_links el ON el.workspace_id = p_workspace
                               AND el.record_type = lk.record_type
                               AND el.record_id = lk.record_id
        JOIN counted m ON m.id = el.message_id
        WHERE m.direction = 'inbound' AND m.sent_at > lk.sent_at
    )
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'mailbox_id', o.mailbox_id,
               'thread_id', o.thread_id,
               'gmail_message_id', o.gmail_message_id,
               'sent_at', o.sent_at,
               'people', coalesce((
                   SELECT jsonb_agg(lk.record_id ORDER BY lk.record_id)
                   FROM linked lk WHERE lk.message_id = o.id AND lk.record_type = 'person'
               ), '[]'::jsonb),
               'companies', coalesce((
                   SELECT jsonb_agg(lk.record_id ORDER BY lk.record_id)
                   FROM linked lk WHERE lk.message_id = o.id AND lk.record_type = 'company'
               ), '[]'::jsonb))
           ORDER BY o.sent_at, o.mailbox_id, o.thread_id), '[]'::jsonb)
    FROM outbound o
    WHERE EXISTS (SELECT 1 FROM linked lk WHERE lk.message_id = o.id)
      AND NOT EXISTS (SELECT 1 FROM answered a WHERE a.message_id = o.id);
$$;

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_workspaces_seed_ai() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_campaign_members_status_changed()
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_job_latest() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_lead_activity(uuid, uuid[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_no_reply_candidates(uuid, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_workspaces_seed_ai() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_campaign_members_status_changed() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_job_latest() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_lead_activity(uuid, uuid[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_no_reply_candidates(uuid, timestamptz) TO service_role;
