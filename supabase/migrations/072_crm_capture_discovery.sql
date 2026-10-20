-- 072_crm_capture_discovery.sql
--
-- Spec 010 (automatic email capture) FR-CAP-DIS-01, -06, -07, -10,
-- FR-CAP-RPT-01 to -03, NFR-CAP-01, -04; data-model §3.4, §3.5, §5, §7;
-- plan §5.6, §5.7, PD-13, PD-14. Slice S2: the discovery dry run.
--
-- 1. `crm_workspace_settings_key_check` widened with `capture`, and a
--    `capture` row `{"mode": "off"}` seeded in every workspace under the
--    system actor (audited `setting.created`, as 060's `ai` seed).
-- 2. `crm_workspaces_seed_ai()` replaced: a workspace created later is
--    seeded `capture` off too. The trigger itself is unchanged.
-- 3. `crm_capture_dry_runs`: a log, one row per dry run: who started it,
--    when it finished, whether the mode left Dry run before it finished
--    (`cancelled`), the recall check (`[{mailbox_id, thread_id, latest_at,
--    fate}]`, ids and times only, up to 500 rows: 256 KB), the report's
--    counts as they stood when it closed (`counts`) and, from S3, when it
--    was applied. At most one run of a workspace is open. RLS on, no
--    policies, no browser grants, no audit trigger.
-- 4. `crm_capture_record(workspace, candidate, message id, values)`: writes
--    a candidate's decision only while the row is still `pending` on that
--    latest message, so a message that arrived meanwhile wins and a retry
--    never judges a row twice (FR-CAP-DIS-06). No actor (a log).
-- 5. `crm_capture_start_dry_run(actor)`: in one transaction, refuses a
--    second open run (CRM09), opens a run, returns every candidate a dry run
--    judged to `pending`, and sets `capture` to `{"mode": "dry_run"}`
--    (audited `setting.updated`). It, `crm_capture_set_mode` and 073's
--    `crm_capture_apply` first take the workspace's capture lock
--    (`pg_advisory_xact_lock(hashtext('crm_capture:' || workspace))`), so
--    a start, a mode change and an apply racing each other run one after
--    another and each sees what the other committed.
-- 6. `crm_capture_set_mode(actor, workspace, mode)`: the settings route's
--    write for key `capture`: `off` or `live` (`live` only once a dry run of
--    the workspace was applied), audited, and an open dry run is cancelled
--    in the same transaction (PD-13), its counts kept as they stood and
--    its partial recall cleared. `live` also returns every candidate a dry
--    run judged (and nobody applied) to `pending`, so the live job judges
--    it again and a lead found then is not lost.
-- 7. `crm_capture_finish_dry_run(workspace, run, recall)`: the run's end,
--    its recall and its counts; false for a run already finished or
--    cancelled. `crm_capture_recall_add(workspace, run, rows)`: the recall
--    check's progress, one fate at a time, kept on the open run so a run
--    that runs out of time resumes where it stopped.
-- 8. `crm_capture_report(workspace, run)`: the report's counts, computed
--    live while the run is open and as they stood once it closed
--    (FR-CAP-RPT-02). Counts only.
-- 9. `crm_job_runs_job_check` widened with `capture`, and `crm_job_latest()`
--    replaced so it reports it; `crm_ai_usage_job_check` widened with
--    `capture` (FR-CAP-DIS-05, NFR-CAP-01).
--
-- The three CHECKs are dropped by name and re-added under the same names
-- with `capture` added (names confirmed with pg_constraint, tasks T201:
-- crm_workspace_settings_key_check and crm_ai_usage_job_check from 060,
-- crm_job_runs_job_check from 066).
--
-- Replaced functions, copied from their latest definitions in the tree
-- (`grep -l "CREATE OR REPLACE FUNCTION public.<name>("
-- supabase/migrations/*.sql | sort | tail -1`): crm_workspaces_seed_ai and
-- crm_job_latest from 066_crm_follow_up_rules.sql, with only the lines
-- marked "072" added.
--
-- EXECUTE on every function here is revoked from PUBLIC, anon and
-- authenticated and granted to service_role (data-model §5).
--
-- Reversible: DROP FUNCTION crm_capture_record(uuid, uuid, text, jsonb),
-- crm_capture_start_dry_run(jsonb), crm_capture_set_mode(jsonb, uuid,
-- text), crm_capture_finish_dry_run(uuid, uuid, jsonb),
-- crm_capture_recall_add(uuid, uuid, jsonb), crm_capture_report(uuid, uuid); DROP TABLE crm_capture_dry_runs; delete
-- the `capture` settings rows (under a system actor) and re-add 060's key
-- CHECK; re-run 066's crm_workspaces_seed_ai() and crm_job_latest(), with
-- their grants; delete `capture` job runs and usage rows and re-add 066's
-- job CHECK and 060's usage job CHECK.

-- ---------------------------------------------------------------------------
-- 1. The `capture` setting — data-model §3.4 (FR-CAP-DIS-01)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_workspace_settings DROP CONSTRAINT IF EXISTS crm_workspace_settings_key_check;
ALTER TABLE crm_workspace_settings ADD CONSTRAINT crm_workspace_settings_key_check
    CHECK (key IN ('stage_probabilities', 'flag_thresholds', 'automations', 'email_sync', 'ai',
                   'capture'));

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- Discovery is off everywhere until an Admin starts a dry run.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'capture', '{"mode": "off"}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. crm_workspaces_seed_ai — 066's, also seeding `capture`
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
    -- 072 (010 FR-CAP-DIS-01): email capture's discovery, off.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'capture', '{"mode": "off"}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    PERFORM set_config('crm.actor', coalesce(was_actor, ''), true);
    PERFORM set_config('crm.action', coalesce(was_action, ''), true);
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. crm_capture_dry_runs — data-model §3.5 (a log)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_capture_dry_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    started_at   timestamptz NOT NULL DEFAULT now(),
    started_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    finished_at  timestamptz,
    -- The mode left Dry run before the run finished (PD-13): never applicable.
    cancelled    boolean NOT NULL DEFAULT false,
    -- [{mailbox_id, thread_id, latest_at, fate}]: ids and times only. A row
    -- is about 165 bytes, so the 500 the job keeps (RECALL_CAP) fit with
    -- room to spare.
    recall       jsonb NOT NULL DEFAULT '[]'::jsonb
                 CHECK (jsonb_typeof(recall) = 'array' AND pg_column_size(recall) <= 262144),
    -- crm_capture_report as it stood when the run finished or was
    -- cancelled; NULL while it is open (its counts are then live).
    counts       jsonb CHECK (counts IS NULL OR jsonb_typeof(counts) = 'object'),
    applied_at   timestamptz,
    applied_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT crm_capture_dry_runs_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT crm_capture_dry_runs_applied_finished
        CHECK (applied_at IS NULL OR finished_at IS NOT NULL),
    CONSTRAINT crm_capture_dry_runs_cancelled
        CHECK (NOT cancelled OR (finished_at IS NOT NULL AND applied_at IS NULL))
);

-- One open run per workspace.
CREATE UNIQUE INDEX IF NOT EXISTS crm_capture_dry_runs_one_open
    ON crm_capture_dry_runs (workspace_id) WHERE finished_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_capture_dry_runs_latest
    ON crm_capture_dry_runs (workspace_id, started_at DESC);

ALTER TABLE crm_capture_dry_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_capture_dry_runs FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. crm_capture_record — a candidate's decision (FR-CAP-DIS-06, -07)
-- ---------------------------------------------------------------------------

-- p_values: {status, outcome?, confidence?, classifier_version?, finding?,
-- dry_run, attempts?}. Written only while the row is `pending` on
-- `p_message_id`; `judged` stamps the message judged and the time.
CREATE OR REPLACE FUNCTION public.crm_capture_record(
    p_workspace uuid, p_candidate uuid, p_message_id text, p_values jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    st text := p_values ->> 'status';
BEGIN
    IF st IS NULL OR st NOT IN ('pending', 'skipped', 'ignored', 'known', 'judged', 'failed') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Not a candidate status.';
    END IF;
    UPDATE crm_capture_candidates c SET
        status = st,
        attempts = coalesce((p_values ->> 'attempts')::smallint, c.attempts),
        outcome = CASE WHEN st = 'judged' THEN p_values ->> 'outcome' END,
        confidence = CASE WHEN st = 'judged' THEN p_values ->> 'confidence' END,
        classifier_version = p_values ->> 'classifier_version',
        judged_message_id = CASE WHEN st = 'judged' THEN p_message_id END,
        judged_at = CASE WHEN st = 'judged' THEN now() END,
        finding = CASE WHEN st = 'judged' AND jsonb_typeof(p_values -> 'finding') = 'object'
                       THEN p_values -> 'finding' END,
        dry_run = coalesce((p_values ->> 'dry_run')::boolean, false),
        updated_at = now()
    WHERE c.workspace_id = p_workspace
      AND c.id = p_candidate
      AND c.latest_message_id = p_message_id
      AND c.status = 'pending';
    RETURN FOUND;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_capture_start_dry_run — FR-CAP-DIS-10, RPT-01 (PD-13)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_start_dry_run(p_actor jsonb)
RETURNS uuid
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws     uuid := (p_actor ->> 'workspace_id')::uuid;
    run_id uuid;
BEGIN
    IF p_actor ->> 'kind' IS DISTINCT FROM 'user' OR ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A dry run is started by a person.';
    END IF;
    -- The workspace's capture lock, shared with set_mode and 073's apply.
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
    IF EXISTS (
        SELECT 1 FROM crm_capture_dry_runs WHERE workspace_id = ws AND finished_at IS NULL
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM09', MESSAGE = 'A dry run is already running.';
    END IF;

    INSERT INTO crm_capture_dry_runs (workspace_id, started_by)
    VALUES (ws, (p_actor ->> 'user_id')::uuid)
    RETURNING id INTO run_id;

    -- What an earlier dry run judged is judged again in this one.
    UPDATE crm_capture_candidates SET
        status = 'pending', attempts = 0, outcome = NULL, confidence = NULL,
        judged_at = NULL, judged_message_id = NULL, classifier_version = NULL,
        finding = NULL, dry_run = false, updated_at = now()
    WHERE workspace_id = ws AND dry_run;

    PERFORM set_config('crm.actor', p_actor::text, true);
    PERFORM set_config('crm.action', '', true);
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (ws, 'capture', '{"mode": "dry_run"}'::jsonb)
    ON CONFLICT (workspace_id, key) DO UPDATE SET value = EXCLUDED.value;
    RETURN run_id;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_capture_set_mode — the settings route for `capture` (PD-13)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_set_mode(
    p_actor jsonb, p_workspace uuid, p_mode text
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    cancelled_id uuid;
BEGIN
    IF p_actor ->> 'kind' IS DISTINCT FROM 'user'
       OR (p_actor ->> 'workspace_id')::uuid IS DISTINCT FROM p_workspace THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such workspace.';
    END IF;
    IF p_mode = 'dry_run' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Start a dry run from Email capture.';
    END IF;
    IF p_mode IS NULL OR p_mode NOT IN ('off', 'live') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Email capture is off, dry run or live.';
    END IF;
    -- The workspace's capture lock, shared with start_dry_run and 073's apply.
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || p_workspace::text));
    -- E6: Live is first entered through Apply.
    IF p_mode = 'live' AND NOT EXISTS (
        SELECT 1 FROM crm_capture_dry_runs
        WHERE workspace_id = p_workspace AND applied_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Run and apply a dry run first.';
    END IF;

    PERFORM set_config('crm.actor', p_actor::text, true);
    PERFORM set_config('crm.action', '', true);
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (p_workspace, 'capture', jsonb_build_object('mode', p_mode))
    ON CONFLICT (workspace_id, key) DO UPDATE SET value = EXCLUDED.value;

    -- A run left open would refuse every later start (the partial index).
    -- Its counts are kept as they stood (contracts §2.1); a partial recall
    -- means nothing, so it is left `[]` (plan §5.7).
    UPDATE crm_capture_dry_runs r SET
        finished_at = now(), cancelled = true, recall = '[]'::jsonb,
        counts = public.crm_capture_report(p_workspace, r.id)
    WHERE r.workspace_id = p_workspace AND r.finished_at IS NULL
    RETURNING r.id INTO cancelled_id;

    -- Live judges again what a dry run judged and nobody applied (after the
    -- cancelled run's counts are kept above): the live job reads only
    -- pending candidates, so a lead found by that run would be lost.
    IF p_mode = 'live' THEN
        UPDATE crm_capture_candidates SET
            status = 'pending', attempts = 0, outcome = NULL, confidence = NULL,
            judged_at = NULL, judged_message_id = NULL, classifier_version = NULL,
            finding = NULL, dry_run = false, updated_at = now()
        WHERE workspace_id = p_workspace AND dry_run;
    END IF;

    RETURN jsonb_build_object('mode', p_mode, 'cancelled_run_id', cancelled_id);
END;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_capture_finish_dry_run — FR-CAP-RPT-03
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_finish_dry_run(
    p_workspace uuid, p_run uuid, p_recall jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    UPDATE crm_capture_dry_runs SET
        finished_at = now(),
        recall = coalesce(p_recall, '[]'::jsonb),
        counts = public.crm_capture_report(p_workspace, p_run)
    WHERE workspace_id = p_workspace AND id = p_run
      AND finished_at IS NULL AND NOT cancelled;
    RETURN FOUND;
END;
$$;

-- The recall check's progress (plan §5.7): each fate is appended to the
-- open run as it is decided, a thread already there is left as it was, so
-- the next tick skips what this one judged. False once the run finished
-- or was cancelled.
CREATE OR REPLACE FUNCTION public.crm_capture_recall_add(
    p_workspace uuid, p_run uuid, p_rows jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    IF p_rows IS NULL OR jsonb_typeof(p_rows) <> 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Not a recall.';
    END IF;
    UPDATE crm_capture_dry_runs r SET
        recall = r.recall || coalesce((
            SELECT jsonb_agg(n.value ORDER BY n.ord)
            FROM jsonb_array_elements(p_rows) WITH ORDINALITY AS n(value, ord)
            WHERE NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(r.recall) AS o(value)
                WHERE o.value ->> 'mailbox_id' = n.value ->> 'mailbox_id'
                  AND o.value ->> 'thread_id' = n.value ->> 'thread_id'
            )
        ), '[]'::jsonb)
    WHERE r.workspace_id = p_workspace AND r.id = p_run
      AND r.finished_at IS NULL AND NOT r.cancelled;
    RETURN FOUND;
END;
$$;

-- ---------------------------------------------------------------------------
-- 8. crm_capture_report — FR-CAP-RPT-02 (counts only)
-- ---------------------------------------------------------------------------

-- {considered, pending, skipped_rules, ignored, known, judged, failed,
-- filed_known_threads, by_outcome: {outcome: {high, medium, low}}}, over the
-- candidates of the workspace's Primary and Sent mailboxes since each one's
-- capture start date (00:00 IST), and the distinct threads those mailboxes
-- have stored since then. A run that finished or was cancelled answers
-- with the counts it stored then: the sync keeps marking candidates
-- afterwards, which are not the run's.
CREATE OR REPLACE FUNCTION public.crm_capture_report(p_workspace uuid, p_run uuid)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path = public, extensions
AS $$
DECLARE
    out    jsonb;
    stored jsonb;
BEGIN
    SELECT r.counts INTO stored
    FROM crm_capture_dry_runs r WHERE r.workspace_id = p_workspace AND r.id = p_run;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such dry run.';
    END IF;
    IF stored IS NOT NULL THEN
        RETURN stored;
    END IF;

    WITH boxes AS (
        SELECT m.id, (m.capture_start_date::timestamp AT TIME ZONE 'Asia/Kolkata') AS since
        FROM crm_mailboxes m
        WHERE m.workspace_id = p_workspace AND m.capture_source = 'primary_sent'
    ), cands AS (
        SELECT c.status, c.outcome, c.confidence
        FROM crm_capture_candidates c
        JOIN boxes b ON b.id = c.mailbox_id
        WHERE c.workspace_id = p_workspace AND c.latest_message_at >= b.since
    ), grid AS (
        SELECT o.outcome, k.confidence
        FROM unnest(ARRAY['new_lead', 'deal_signal', 'other_business', 'not_relevant'])
             AS o(outcome)
        CROSS JOIN unnest(ARRAY['high', 'medium', 'low']) AS k(confidence)
    ), by_outcome AS (
        SELECT g.outcome, jsonb_object_agg(g.confidence, (
                   SELECT count(*) FROM cands c
                   WHERE c.status = 'judged' AND c.outcome = g.outcome
                     AND c.confidence = g.confidence)) AS counts
        FROM grid g
        GROUP BY g.outcome
    )
    SELECT jsonb_build_object(
        'considered', (SELECT count(*) FROM cands),
        'pending', (SELECT count(*) FROM cands WHERE status = 'pending'),
        'skipped_rules', (SELECT count(*) FROM cands WHERE status = 'skipped'),
        'ignored', (SELECT count(*) FROM cands WHERE status = 'ignored'),
        'known', (SELECT count(*) FROM cands WHERE status = 'known'),
        'judged', (SELECT count(*) FROM cands WHERE status = 'judged'),
        'failed', (SELECT count(*) FROM cands WHERE status = 'failed'),
        'filed_known_threads', (
            SELECT count(*) FROM (
                SELECT DISTINCT e.mailbox_id, e.thread_id
                FROM crm_email_messages e
                JOIN boxes b ON b.id = e.mailbox_id
                WHERE e.workspace_id = p_workspace AND e.sent_at >= b.since
            ) AS t),
        'by_outcome', (SELECT jsonb_object_agg(outcome, counts) FROM by_outcome)
    ) INTO out;
    RETURN out;
END;
$$;

-- ---------------------------------------------------------------------------
-- 9. Job and usage names — data-model §3.5 (NFR-CAP-01, -04)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_job_runs DROP CONSTRAINT IF EXISTS crm_job_runs_job_check;
ALTER TABLE crm_job_runs ADD CONSTRAINT crm_job_runs_job_check
    CHECK (job IN (
        'email_sync', 'flags', 'digests', 'reminders', 'trash_purge', 'automations',
        'summaries', 'meetings', 'enrichment', 'ai_fields', 'review_expiry',
        'followup_drafts', 'capture'
    ));

-- 066's function, with `capture` added to its array.
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
                          'followup_drafts',
                          -- 072 (010 NFR-CAP-04)
                          'capture'])
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

ALTER TABLE crm_ai_usage DROP CONSTRAINT IF EXISTS crm_ai_usage_job_check;
ALTER TABLE crm_ai_usage ADD CONSTRAINT crm_ai_usage_job_check
    CHECK (job IN (
        'summaries', 'meetings', 'enrichment', 'ai_fields', 'followup_drafts', 'capture'
    ));

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_workspaces_seed_ai() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_record(uuid, uuid, text, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_start_dry_run(jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_set_mode(jsonb, uuid, text)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_finish_dry_run(uuid, uuid, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_recall_add(uuid, uuid, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_report(uuid, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_job_latest() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_workspaces_seed_ai() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_record(uuid, uuid, text, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_start_dry_run(jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_set_mode(jsonb, uuid, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_finish_dry_run(uuid, uuid, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_recall_add(uuid, uuid, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_report(uuid, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_job_latest() TO service_role;
