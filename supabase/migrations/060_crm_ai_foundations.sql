-- 060_crm_ai_foundations.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-FLAG-01, -02, FR-CRMAI-LLM-06,
-- -07, -08, -10, FR-CRMAI-JOB-01, FR-CRMAI-CHG-07, -08; Q-02, Q-03, Q-04,
-- Q-23; data-model §3.1, §3.7, §3.8, §5; plan §5.1, §5.3.
--
-- The foundations every AI job stands on. Nothing here calls a model.
--
-- 1. The `ai` setting (FR-CRMAI-FLAG-01): `{"budget_usd": 0, "phases":
--    {"1": false, "2": false, "3": false, "4": false}}`. The key CHECK of
--    `crm_workspace_settings` is widened with `ai`, an `ai` row is inserted
--    in every workspace, every phase off and a zero budget, and the trigger
--    `crm_workspaces_seed_ai` inserts the same for a workspace created
--    later (FR-CRMAI-FLAG-02). Off, because `automation_on` reads a missing
--    key as on (Q-04); a zero budget means no AI work runs (Q-02). Seeds are
--    written under the system actor, as 037's are.
-- 2. `crm_ai_usage`: the AI usage log (FR-CRMAI-LLM-06): workspace, job,
--    model, tokens, estimated cost, outcome, time. No prompt or output text
--    and no record id. A log, not a record: no audit trigger, written by the
--    backend's service role (services/crm/ai.py). Read only through
--    `crm_ai_spend()` and the Admin-only `GET /crm/ai` (FR-CRMAI-LLM-10).
-- 3. `crm_ai_spend(p_workspace, p_from)`: the estimated spend since
--    `p_from` (the start of the Asia/Kolkata month, computed in Python,
--    FR-CRMAI-LLM-07, Q-23), in total and per job, as decimal strings.
--    PostgREST aggregates are off, hence the function.
-- 4. `crm_job_runs.job` CHECK widened with the AI job names `summaries`,
--    `meetings`, `enrichment`, `ai_fields` and `review_expiry`
--    (FR-CRMAI-JOB-01, FR-CRMAI-CHG-07), and `crm_job_latest()` replaced so
--    it reports them (a job that never ran is still not reported, 007
--    FR-CRM-JOB-03).
-- 5. `crm_notifications.kind` CHECK widened with `ai_budget_reached`
--    (FR-CRMAI-LLM-08, FR-CRMAI-CHG-08).
--
-- The three CHECKs are unnamed inline column CHECKs, so they are dropped by
-- their auto-generated names and re-added under the same names, as 041:76
-- did for `crm_select_options_list_key_check`. Checked against the rebased
-- tree (T004): no migration after 026 and 037 rebuilds any of them, and
-- 008 has committed none that does.
--   crm_workspace_settings_key_check (026:110-112):
--       key IN ('stage_probabilities', 'flag_thresholds', 'automations', 'email_sync')
--   crm_job_runs_job_check (037:60):
--       job IN ('email_sync', 'flags', 'digests', 'reminders', 'trash_purge', 'automations')
--   crm_notifications_kind_check (037:92):
--       kind IN ('assigned', 'mentioned', 'stage_changed', 'reply_owed_red',
--                'handoff_waiting', 'revenue_confirm', 'task_due', 'task_overdue',
--                'stale_deal', 'job_failed', 'invited')
-- `crm_job_latest()` is copied from its latest definition,
-- 037_crm_jobs_notifications.sql (`grep -l "CREATE OR REPLACE FUNCTION
-- public.crm_job_latest(" supabase/migrations/*.sql | sort | tail -1`),
-- with the five names added to its array and nothing else changed.
--
-- EXECUTE on the functions is revoked from PUBLIC, anon and authenticated
-- (data-model §5).
--
-- Reversible: DROP TRIGGER crm_workspaces_seed_ai ON crm_workspaces; DROP
-- FUNCTION crm_workspaces_seed_ai(); DELETE FROM crm_workspace_settings
-- WHERE key = 'ai' (under a system actor); restore the key CHECK; DROP
-- FUNCTION crm_ai_spend(uuid, timestamptz); DROP TABLE crm_ai_usage;
-- restore the job and kind CHECKs (after deleting rows using the new
-- values) and 037's crm_job_latest().

-- ---------------------------------------------------------------------------
-- 1. The `ai` setting — data-model §3.7
-- ---------------------------------------------------------------------------

ALTER TABLE crm_workspace_settings DROP CONSTRAINT IF EXISTS crm_workspace_settings_key_check;
ALTER TABLE crm_workspace_settings ADD CONSTRAINT crm_workspace_settings_key_check
    CHECK (key IN ('stage_probabilities', 'flag_thresholds', 'automations', 'email_sync', 'ai'));

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- FR-CRMAI-FLAG-02: every phase off, no budget (Q-02, Q-04).
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'ai',
           '{"budget_usd": 0, "phases": {"1": false, "2": false, "3": false, "4": false}}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- A workspace created later gets the same row, written as the system: the
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
    PERFORM set_config('crm.actor', coalesce(was_actor, ''), true);
    PERFORM set_config('crm.action', coalesce(was_action, ''), true);
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS crm_workspaces_seed_ai ON crm_workspaces;
CREATE TRIGGER crm_workspaces_seed_ai AFTER INSERT ON crm_workspaces
    FOR EACH ROW EXECUTE FUNCTION public.crm_workspaces_seed_ai();

-- ---------------------------------------------------------------------------
-- 2. crm_ai_usage — data-model §3.1 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_ai_usage (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    job           text NOT NULL CHECK (job IN (
                      'summaries', 'meetings', 'enrichment', 'ai_fields', 'followup_drafts'
                  )),
    model         text NOT NULL CHECK (char_length(model) <= 100),
    input_tokens  integer NOT NULL CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL CHECK (output_tokens >= 0),
    -- Estimated from the rate table at call time (plan §2.2).
    cost_usd      numeric(12, 6) NOT NULL CHECK (cost_usd >= 0),
    outcome       text NOT NULL CHECK (outcome IN ('ok', 'invalid_output', 'failed', 'timed_out')),
    at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS crm_ai_usage_month
    ON crm_ai_usage (workspace_id, at DESC);

ALTER TABLE crm_ai_usage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_ai_usage FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. crm_ai_spend — month-to-date estimated spend (FR-CRMAI-LLM-07, -09)
-- ---------------------------------------------------------------------------

-- {"total_usd": "<decimal>", "by_job": {job: "<decimal>"}} over the
-- workspace's usage rows at or after `p_from`. A job with no row this month
-- is absent from `by_job`.
CREATE OR REPLACE FUNCTION public.crm_ai_spend(p_workspace uuid, p_from timestamptz)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT jsonb_build_object(
        'total_usd', coalesce(sum(per.cost), 0)::text,
        'by_job', coalesce(jsonb_object_agg(per.job, per.cost::text)
                           FILTER (WHERE per.job IS NOT NULL), '{}'::jsonb)
    )
    FROM (
        SELECT u.job, sum(u.cost_usd) AS cost
        FROM crm_ai_usage u
        WHERE u.workspace_id = p_workspace AND u.at >= p_from
        GROUP BY u.job
    ) AS per;
$$;

-- ---------------------------------------------------------------------------
-- 4. Job names — data-model §3.8 (FR-CRMAI-JOB-01)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_job_runs DROP CONSTRAINT IF EXISTS crm_job_runs_job_check;
ALTER TABLE crm_job_runs ADD CONSTRAINT crm_job_runs_job_check
    CHECK (job IN (
        'email_sync', 'flags', 'digests', 'reminders', 'trash_purge', 'automations',
        'summaries', 'meetings', 'enrichment', 'ai_fields', 'review_expiry'
    ));

-- 037's function, with the five AI job names added to its array.
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
                          'summaries', 'meetings', 'enrichment', 'ai_fields', 'review_expiry'])
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
-- 5. Notification kind — data-model §3.8 (FR-CRMAI-LLM-08, -CHG-08)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_notifications DROP CONSTRAINT IF EXISTS crm_notifications_kind_check;
ALTER TABLE crm_notifications ADD CONSTRAINT crm_notifications_kind_check
    CHECK (kind IN (
        'assigned', 'mentioned', 'stage_changed', 'reply_owed_red',
        'handoff_waiting', 'revenue_confirm', 'task_due', 'task_overdue',
        'stale_deal', 'job_failed', 'invited', 'ai_budget_reached'
    ));

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_workspaces_seed_ai() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_ai_spend(uuid, timestamptz) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_job_latest() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_workspaces_seed_ai() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_ai_spend(uuid, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_job_latest() TO service_role;
