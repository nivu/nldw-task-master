-- 012_ops.sql
--
-- Spec 006 — org operations: locations and comp-off, sign-off, milestones,
-- checklists, quarterly reviews, OAuth for MCP clients, and the settings the
-- nudges and digest read. Every new table is backend-only (RLS on, no browser
-- grants), as 010 and 011 are.

-- ============================================================
-- Locations — FR-LOC
-- ============================================================
CREATE TABLE IF NOT EXISTS locations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (btrim(name) <> ''),
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS locations_name_unique ON locations (lower(btrim(name)));
CREATE UNIQUE INDEX IF NOT EXISTS locations_one_default ON locations (is_default) WHERE is_default;

INSERT INTO locations (name, is_default)
SELECT 'Head office', true
WHERE NOT EXISTS (SELECT 1 FROM locations);

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES locations(id) ON DELETE SET NULL;
-- NULL = applies everywhere (FR-LOC-02).
ALTER TABLE holidays ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES locations(id) ON DELETE CASCADE;
-- 001 keyed holidays by date alone; a date may now carry one row per location.
ALTER TABLE holidays DROP CONSTRAINT IF EXISTS holidays_date_key;
CREATE UNIQUE INDEX IF NOT EXISTS holidays_date_location ON holidays (date, location_id) NULLS NOT DISTINCT;

-- Browser read of the new column (RLS from 004 still scopes rows).
GRANT SELECT (location_id) ON profiles TO authenticated;

-- FR-FEED-02: rotating the salt rotates the person's calendar feed key.
-- Backend-only column: not in the browser's column grant.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS feed_salt text NOT NULL DEFAULT '';

-- ============================================================
-- Comp-off — FR-COMP. A fourth booking category with no allowance: it draws
-- on credits earned by working a weekend or holiday.
-- ============================================================
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_category_check;
ALTER TABLE bookings ADD CONSTRAINT bookings_category_check
    CHECK (category IN ('wfh', 'casual', 'sick', 'compoff'));
-- 001 Q-07 required a reason for anything but WFH. Comp-off is time already
-- earned; it needs no justification either.
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_reason_required;
ALTER TABLE bookings ADD CONSTRAINT bookings_reason_required
    CHECK (
        status = 'unrecognised'
        OR category IN ('wfh', 'compoff')
        OR (reason IS NOT NULL AND btrim(reason) <> '')
    );

CREATE TABLE IF NOT EXISTS compoff_credits (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    worked_on      date NOT NULL,
    days           numeric(2,1) NOT NULL CHECK (days IN (0.5, 1.0)),
    note           text,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'used', 'lapsed')),
    -- FR-COMP-02: set at approval, from compoff_valid_days.
    expires_on     date,
    booking_id     uuid REFERENCES bookings(id) ON DELETE SET NULL,
    decided_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    decided_at     timestamptz,
    decision_note  text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS compoff_one_claim_per_day
    ON compoff_credits (user_id, worked_on) WHERE status IN ('pending', 'approved', 'used');
CREATE INDEX IF NOT EXISTS compoff_user ON compoff_credits (user_id, status);

-- ============================================================
-- Weekly sign-off — FR-SIGN
-- ============================================================
CREATE TABLE IF NOT EXISTS timesheet_confirmations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    week_start    date NOT NULL,
    status        text NOT NULL CHECK (status IN ('confirmed', 'auto')),
    confirmed_by  uuid REFERENCES profiles(id) ON DELETE SET NULL,
    confirmed_at  timestamptz NOT NULL DEFAULT now(),
    note          text,
    UNIQUE (user_id, week_start)
);

-- ============================================================
-- Milestones — FR-MILE
-- ============================================================
CREATE TABLE IF NOT EXISTS project_milestones (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name         text NOT NULL CHECK (btrim(name) <> ''),
    due_on       date NOT NULL,
    amount       numeric(14,2) NOT NULL CHECK (amount >= 0),
    invoiced_on  date,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS project_milestones_project ON project_milestones (project_id, due_on);

-- ============================================================
-- Checklists — FR-CHK
-- ============================================================
CREATE TABLE IF NOT EXISTS checklist_templates (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind      text NOT NULL CHECK (kind IN ('onboarding', 'offboarding')),
    position  int NOT NULL,
    label     text NOT NULL CHECK (btrim(label) <> '')
);

INSERT INTO checklist_templates (kind, position, label)
SELECT * FROM (VALUES
    ('onboarding', 1, 'Portal account created with the right role and approver'),
    ('onboarding', 2, 'Google Workspace account and groups'),
    ('onboarding', 3, 'Slack invited and in the team channels'),
    ('onboarding', 4, 'Laptop issued and asset recorded'),
    ('onboarding', 5, 'Access to client systems for their first project'),
    ('onboarding', 6, 'CTC recorded under Admin → People'),
    ('onboarding', 7, 'Allocated to a project'),
    ('onboarding', 8, 'Introduced to their lead; first-week plan agreed'),
    ('offboarding', 1, 'Handover document written and reviewed'),
    ('offboarding', 2, 'Client system access removed'),
    ('offboarding', 3, 'Laptop and assets returned'),
    ('offboarding', 4, 'Google Workspace suspended; mail forwarded'),
    ('offboarding', 5, 'Slack deactivated'),
    ('offboarding', 6, 'Final leave balance settled'),
    ('offboarding', 7, 'Portal account deactivated (separate, deliberate action)')
) AS t(kind, position, label)
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates);

CREATE TABLE IF NOT EXISTS checklists (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('onboarding', 'offboarding')),
    created_by  uuid REFERENCES profiles(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    closed_at   timestamptz
);

CREATE TABLE IF NOT EXISTS checklist_items (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    checklist_id  uuid NOT NULL REFERENCES checklists(id) ON DELETE CASCADE,
    position      int NOT NULL,
    label         text NOT NULL,
    owner_id      uuid REFERENCES profiles(id) ON DELETE SET NULL,
    due_on        date,
    done_at       timestamptz,
    done_by       uuid REFERENCES profiles(id) ON DELETE SET NULL
);

-- ============================================================
-- Quarterly self-summary — FR-REV
-- ============================================================
CREATE TABLE IF NOT EXISTS reviews (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    quarter       text NOT NULL CHECK (quarter ~ '^[0-9]{4}-Q[1-4]$'),
    summary       text NOT NULL DEFAULT '',
    submitted_at  timestamptz,
    closed_by     uuid REFERENCES profiles(id) ON DELETE SET NULL,
    closed_at     timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, quarter)
);

-- ============================================================
-- OAuth for MCP clients — FR-OAUTH. Tokens issued this way are ordinary
-- api_tokens (004) tagged with the client that holds them.
-- ============================================================
ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS client_id text;

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id      text PRIMARY KEY,
    client_secret  text,
    metadata       jsonb NOT NULL,
    registered_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_transactions (
    txn         text PRIMARY KEY,
    client_id   text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    params      jsonb NOT NULL,
    expires_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code            text PRIMARY KEY,
    client_id       text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    params          jsonb NOT NULL,
    expires_at      timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token_hash    text PRIMARY KEY,
    client_id     text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    api_token_id  uuid REFERENCES api_tokens(id) ON DELETE CASCADE,
    scopes        jsonb NOT NULL DEFAULT '[]'::jsonb,
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);

-- ============================================================
-- Settings the nudges, digest, bench and comp-off read
-- ============================================================
INSERT INTO app_settings (key, value, description) VALUES
    ('utilisation_target', '80', 'Spec 006 FR-UTIL-02. Billable hours as a percentage of capacity that counts as fully utilised.'),
    ('bench_threshold', '60', 'Spec 006 FR-UTIL-03. Allocated percent below which a week counts as bench.'),
    ('nudges_enabled', 'true', 'Spec 006 FR-NUDGE. Whether the daily, weekly and Monday nudges are sent at all.'),
    ('nudge_hour', '18', 'Spec 006 FR-NUDGE-01. Hour (Asia/Kolkata, 0-23) of the "nothing logged today" nudge.'),
    ('slack_out_channel', '""', 'Spec 006 FR-FEED-03. Slack channel for the morning who-is-out post, e.g. "#general". Empty = off.'),
    ('compoff_valid_days', '90', 'Spec 006 FR-COMP-02. Days an approved comp-off credit stays usable.'),
    ('digest_enabled', 'true', 'Spec 006 FR-DIGEST. Whether the Monday leadership digest is sent.')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- Backend-only, all of it.
-- ============================================================
ALTER TABLE locations               ENABLE ROW LEVEL SECURITY;
ALTER TABLE compoff_credits         ENABLE ROW LEVEL SECURITY;
ALTER TABLE timesheet_confirmations ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_milestones      ENABLE ROW LEVEL SECURITY;
ALTER TABLE checklist_templates     ENABLE ROW LEVEL SECURITY;
ALTER TABLE checklists              ENABLE ROW LEVEL SECURITY;
ALTER TABLE checklist_items         ENABLE ROW LEVEL SECURITY;
ALTER TABLE reviews                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_clients           ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_transactions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_codes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_refresh_tokens    ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON locations, compoff_credits, timesheet_confirmations, project_milestones,
    checklist_templates, checklists, checklist_items, reviews,
    oauth_clients, oauth_transactions, oauth_codes, oauth_refresh_tokens
    FROM authenticated, anon;
