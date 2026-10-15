-- 037_crm_jobs_notifications.sql
--
-- Spec 007 (sales CRM) FR-CRM-JOB-01 to -03, FR-CRM-NOTIF-01 to -05,
-- FR-CRM-AUTO-01, -02, FR-CRM-LIST-06, FR-CRM-ACC-11, -12, FR-CRM-WS-02;
-- Q-18, Q-19, Q-69, Q-72, Q-73; data-model §3.18 to §3.20, §5.3, §7.3;
-- plan §5.11.
--
-- Background work becomes visible: every run is logged, the bell has
-- something to show, and digests are sent exactly once.
--
-- 1. `crm_job_runs`: one row per run of a CRM job in a workspace (flags,
--    reminders, trash purge, automations; email sync in 1b), and one row
--    with no workspace per digest tick, which spans workspaces. A log, not a
--    record: no audit trigger, written by the backend's job wrapper
--    (services/crm/jobs.py). `error` may name records and is shown to the
--    workspace's members only; `error_public` never does, and is all a TVA
--    admin who is not a member, or anyone at all for a row with no
--    workspace, ever sees (FR-CRM-JOB-01, Q-69). `alerted` marks the run
--    whose third consecutive failure alerted the admins, so a streak alerts
--    once (FR-CRM-JOB-02).
-- 2. `crm_notifications`: the bell (FR-CRM-NOTIF-01). Its (workspace,
--    person) pair is a foreign key to `crm_memberships` with ON DELETE
--    CASCADE, so a notification can only be addressed to a member, and a
--    removed member keeps none of the workspace's titles (FR-CRM-WS-02).
--    Written only by `crm_notify` below, which skips non-members and
--    duplicates (`dedupe_key`, e.g. one overdue reminder per task per day).
--    Titles carry no amount (FR-CRM-ACC-11); the backend writes them.
-- 3. `crm_digest_sends`: the sent markers that make each digest go out once
--    (FR-CRM-NOTIF-03, -04). `crm_digest_claim` inserts the marker first and
--    returns its id only when it was new, so two ticks in one window send
--    once.
-- 4. Settings seeds (data-model §7.3): `flag_thresholds` (FR-CRM-FLAG-01) and
--    `automations`, the switches of automations 2, 5, 6, 7 and 8
--    (FR-CRM-AUTO-02). Written under the system actor, as 026's seeds.
-- 5. `crm_remove_membership()` replaced (027's, plus one statement): the
--    removed person's notifications for the workspace are deleted with the
--    membership (data-model §5.3). The foreign key would cascade them anyway;
--    the explicit delete says so where the removal is written.
-- 6. `crm_job_latest()`: the latest run of each job in each workspace (and
--    the digest tick's), for the staleness check made on read
--    (FR-CRM-JOB-03): one indexed lookup per pair, however long the log.
--
-- EXECUTE on the functions is revoked from PUBLIC, anon and authenticated
-- (data-model §5.12).
--
-- Reversible: restore 027's crm_remove_membership(); DROP FUNCTION
-- crm_job_latest(), crm_digest_claim(uuid, text, date, uuid, text[]),
-- crm_notify(jsonb); DROP TABLE crm_digest_sends, crm_notifications,
-- crm_job_runs; DELETE FROM crm_workspace_settings WHERE key IN
-- ('flag_thresholds', 'automations') (audited as purges).

-- ---------------------------------------------------------------------------
-- 1. crm_job_runs — data-model §3.18 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_job_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL only for the digest tick, which spans workspaces.
    workspace_id uuid REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    job          text NOT NULL CHECK (job IN (
                     'email_sync', 'flags', 'digests', 'reminders', 'trash_purge', 'automations'
                 )),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    outcome      text NOT NULL DEFAULT 'running'
                 CHECK (outcome IN ('running', 'ok', 'failed', 'skipped')),
    counts       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(counts) = 'object'),
    error        text CHECK (char_length(error) <= 2000),
    error_public text CHECK (char_length(error_public) <= 500),
    alerted      boolean NOT NULL DEFAULT false,
    CONSTRAINT crm_job_runs_finished CHECK ((outcome = 'running') = (finished_at IS NULL)),
    CONSTRAINT crm_job_runs_digest_only_unscoped
        CHECK (workspace_id IS NOT NULL OR job = 'digests')
);

CREATE INDEX IF NOT EXISTS crm_job_runs_recent
    ON crm_job_runs (job, workspace_id, started_at DESC);
CREATE INDEX IF NOT EXISTS crm_job_runs_workspace
    ON crm_job_runs (workspace_id, started_at DESC);

ALTER TABLE crm_job_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_job_runs FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. crm_notifications — data-model §3.19 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_notifications (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    user_id      uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN (
                     'assigned', 'mentioned', 'stage_changed', 'reply_owed_red',
                     'handoff_waiting', 'revenue_confirm', 'task_due', 'task_overdue',
                     'stale_deal', 'job_failed', 'invited'
                 )),
    record_type  text CHECK (record_type IN (
                     'company', 'person', 'opportunity', 'task', 'note', 'campaign'
                 )),
    record_id    uuid,
    title        text NOT NULL CHECK (btrim(title) <> '' AND char_length(title) <= 200),
    dedupe_key   text NOT NULL CHECK (char_length(dedupe_key) <= 200),
    created_at   timestamptz NOT NULL DEFAULT now(),
    read_at      timestamptz,
    CONSTRAINT crm_notifications_dedupe UNIQUE (user_id, dedupe_key),
    -- Only a member can be notified, and removing the membership removes
    -- what they were told about the workspace (FR-CRM-WS-02).
    CONSTRAINT crm_notifications_member
        FOREIGN KEY (workspace_id, user_id)
        REFERENCES crm_memberships (workspace_id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS crm_notifications_inbox
    ON crm_notifications (user_id, read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS crm_notifications_workspace_user
    ON crm_notifications (workspace_id, user_id);

ALTER TABLE crm_notifications ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_notifications FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. crm_digest_sends — data-model §3.20 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_digest_sends (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    -- NULL for the daily digest (one message, a section per workspace); the
    -- workspace of a weekly admin digest.
    workspace_id uuid REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    kind         text NOT NULL CHECK (kind IN ('daily', 'weekly')),
    local_date   date NOT NULL,
    sent_at      timestamptz NOT NULL DEFAULT now(),
    channels     text[] NOT NULL DEFAULT '{}',
    CONSTRAINT crm_digest_sends_weekly_scoped CHECK ((kind = 'weekly') = (workspace_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_digest_sends_once
    ON crm_digest_sends (
        user_id, kind, local_date,
        coalesce(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid)
    );

ALTER TABLE crm_digest_sends ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_digest_sends FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. Settings seeds — data-model §7.3
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- FR-CRM-FLAG-01: spec §6.2's thresholds.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'flag_thresholds',
           '{"reply_owed_red_hours": 24, "reply_owed_amber_hours": 12,
             "silent_red_days": 3, "silent_amber_days": 2, "close_amber_days": 7,
             "stale_red_days": 21, "stale_amber_days": 14}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;

    -- FR-CRM-AUTO-02: automations 2, 5, 6, 7 and 8, all on.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'automations',
           '{"email_records": true, "stale_notice": true, "digests": true,
             "task_reminders": true, "campaign_conversion": true}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_notify — the one writer of the bell
-- ---------------------------------------------------------------------------

-- `p_rows`: [{workspace_id, user_id, kind, record_type?, record_id?, title,
-- dedupe_key}]. A row for someone who is not a member of its workspace is
-- skipped, as is a row whose (person, dedupe_key) exists already. Returns
-- the rows that were added, so the caller sends an out-of-app message only
-- for a new one.
CREATE OR REPLACE FUNCTION public.crm_notify(p_rows jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    added jsonb;
BEGIN
    IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Notifications come as a list.';
    END IF;
    WITH wanted AS (
        SELECT DISTINCT ON (r.user_id, r.dedupe_key) r.*
        FROM jsonb_to_recordset(p_rows) AS r(
            workspace_id uuid, user_id uuid, kind text, record_type text, record_id uuid,
            title text, dedupe_key text
        )
        JOIN crm_memberships m ON m.workspace_id = r.workspace_id AND m.user_id = r.user_id
        ORDER BY r.user_id, r.dedupe_key
    ), ins AS (
        INSERT INTO crm_notifications (workspace_id, user_id, kind, record_type, record_id,
                                       title, dedupe_key)
        SELECT workspace_id, user_id, kind, record_type, record_id, left(title, 200), dedupe_key
        FROM wanted
        ON CONFLICT (user_id, dedupe_key) DO NOTHING
        RETURNING to_jsonb(crm_notifications.*) AS j
    )
    SELECT coalesce(jsonb_agg(j), '[]'::jsonb) INTO added FROM ins;
    RETURN added;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_digest_claim — exactly once (FR-CRM-NOTIF-03, -04)
-- ---------------------------------------------------------------------------

-- Inserts the sent marker and returns its id, or NULL when this digest was
-- claimed already: the sender sends only on an id.
CREATE OR REPLACE FUNCTION public.crm_digest_claim(
    p_user uuid, p_kind text, p_local_date date, p_workspace uuid, p_channels text[]
)
RETURNS uuid
LANGUAGE sql
SET search_path = public, extensions
AS $$
    INSERT INTO crm_digest_sends (user_id, kind, local_date, workspace_id, channels)
    VALUES (p_user, p_kind, p_local_date, p_workspace, coalesce(p_channels, '{}'))
    ON CONFLICT (user_id, kind, local_date,
                 coalesce(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid))
    DO NOTHING
    RETURNING id;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_remove_membership — data-model §5.3 (027's, plus the notifications)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_remove_membership(p_actor jsonb, p_membership_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    m           crm_memberships%ROWTYPE;
    person      profiles%ROWTYPE;
    tokens_n    integer := 0;
    oauth_n     integer := 0;
    deactivated boolean := false;
BEGIN
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- Admins first, in id order, then the row: the same order crm_apply
    -- takes, so concurrent removals and demotions queue rather than deadlock.
    PERFORM 1 FROM crm_memberships
    WHERE workspace_id = ws AND role = 'admin' ORDER BY id FOR UPDATE;
    SELECT * INTO m FROM crm_memberships
    WHERE id = p_membership_id AND workspace_id = ws FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such member in this workspace.';
    END IF;
    IF m.role = 'admin' AND NOT EXISTS (
        SELECT 1 FROM crm_memberships
        WHERE workspace_id = ws AND role = 'admin' AND id <> m.id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A workspace needs at least one admin.';
    END IF;

    SELECT * INTO person FROM profiles WHERE id = m.user_id FOR UPDATE;
    IF (person.is_owner OR (person.role = 'admin' AND person.account_type = 'staff'))
       AND NOT crm_is_tva_admin(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Ask a TVA admin to change this membership.';
    END IF;

    -- 037: what the bell told them about this workspace goes with them
    -- (FR-CRM-WS-02). Notifications are a log, so this writes no audit row.
    DELETE FROM crm_notifications WHERE workspace_id = ws AND user_id = m.user_id;

    PERFORM set_config('crm.action', 'membership.removed', true);
    DELETE FROM crm_memberships WHERE id = m.id;
    PERFORM set_config('crm.action', '', true);

    -- FR-CRM-ACC-12, Q-44: a CRM-only person with no workspace left has no
    -- reason to sign in. Staff keep their TVA account.
    IF person.account_type = 'crm_only' AND person.is_active
       AND NOT EXISTS (SELECT 1 FROM crm_memberships WHERE user_id = person.id) THEN
        UPDATE profiles
        SET is_active = false, deactivated_reason = 'crm_no_membership'
        WHERE id = person.id;
        UPDATE api_tokens SET revoked_at = now()
        WHERE user_id = person.id AND revoked_at IS NULL;
        GET DIAGNOSTICS tokens_n = ROW_COUNT;
        UPDATE oauth_refresh_tokens SET revoked_at = now()
        WHERE user_id = person.id AND revoked_at IS NULL;
        GET DIAGNOSTICS oauth_n = ROW_COUNT;
        deactivated := true;

        -- TVA rows (no workspace): the account and its tokens are TVA's. The
        -- admin who removed the membership did this themselves, so the rows
        -- carry their own kind: a `system` row would read "(via CRM deal)".
        INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                               target_id, before, after)
        VALUES (who, coalesce(p_actor ->> 'kind', 'user'), p_actor ->> 'channel',
                'user.deactivated', 'profiles',
                person.id::text, jsonb_build_object('is_active', true),
                jsonb_build_object('is_active', false,
                                   'deactivated_reason', 'crm_no_membership'));
        IF tokens_n + oauth_n > 0 THEN
            INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                                   target_id, after)
            VALUES (who, coalesce(p_actor ->> 'kind', 'user'), p_actor ->> 'channel',
                    'token.revoked', 'api_tokens',
                    person.id::text,
                    jsonb_build_object('personal_tokens', tokens_n, 'oauth_tokens', oauth_n));
        END IF;
    END IF;

    RETURN jsonb_build_object('deactivated', deactivated);
END;
$$;

-- ---------------------------------------------------------------------------
-- 8. crm_job_latest — the staleness check on read (FR-CRM-JOB-03)
-- ---------------------------------------------------------------------------

-- The latest run of every job in every workspace, and of the digest tick
-- (no workspace): [{job, workspace_id, started_at, finished_at, outcome}].
-- Never `error` or `counts`: this feeds /health/jobs too.
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
        FROM unnest(ARRAY['email_sync', 'flags', 'reminders', 'trash_purge', 'automations'])
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

REVOKE ALL ON FUNCTION public.crm_notify(jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_digest_claim(uuid, text, date, uuid, text[])
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_remove_membership(jsonb, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_job_latest() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_notify(jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_digest_claim(uuid, text, date, uuid, text[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_remove_membership(jsonb, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_job_latest() TO service_role;
