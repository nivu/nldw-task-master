-- 015_logs_time.sql
--
-- Spec 002 FR-ANALYTICS-07 — who is expected to log time, and from when.
--
-- `logs_time` false: the person is never counted as missing time — not in
-- coverage, the missing-days lists, the nudges or weekly sign-off. Everyone
-- starts as true, which is what every existing row meant.
--
-- No column grant to `authenticated`: the browser never reads profiles
-- directly (all reads go through the backend proxy), so the 009 grant list
-- is left as it is and the column stays backend-only.

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS logs_time boolean NOT NULL DEFAULT true;

-- The first day anyone is expected to have logged. "" = no start date (every
-- working day counts); app_settings.value is NOT NULL, so empty rather than
-- null, as slack_out_channel does.
INSERT INTO app_settings (key, value, description) VALUES
    (
        'portal_start_date',
        '""',
        'Spec 002 FR-ANALYTICS-07. The day the company started logging time in '
        'the portal, e.g. "2026-10-01". Days before it are never missing in '
        'coverage, nudges or sign-off. Empty = no start date.'
    )
ON CONFLICT (key) DO NOTHING;
