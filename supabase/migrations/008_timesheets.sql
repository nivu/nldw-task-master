-- 008_timesheets.sql
--
-- Spec 002 — projects, phased timelines, allocations and daily time entries.
--
-- Shares people, roles, timezone and the audit log with 001. Nothing here
-- forks any of them: `profiles` is still the only notion of a person, and
-- `audit_log` is still the only record of what changed.

-- ============================================================
-- projects — FR-PROJ
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (btrim(name) <> ''),
    client      text,
    -- FR-PROJ-04: archived, never deleted. Effort logged against a finished
    -- project is exactly the history the analytics exist to report on.
    is_archived boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES profiles(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS projects_name_unique ON projects (lower(btrim(name)));

-- ============================================================
-- project_phases — spec §3.2
--
-- Effort is logged against a phase, not merely a project. Without the split,
-- "we spent 400 hours on this" cannot distinguish a project that overran from
-- one that has sat in unbudgeted support for a year — which is the distinction
-- a budget conversation actually turns on.
-- ============================================================
CREATE TABLE IF NOT EXISTS project_phases (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase         text NOT NULL CHECK (phase IN ('pre', 'delivery', 'support')),
    starts_on     date NOT NULL,
    ends_on       date NOT NULL,
    -- Q-05: budget is HOURS, never money. Money would need per-person rates,
    -- which is salary-adjacent data in a system every lead can read.
    budget_hours  numeric(8,2) CHECK (budget_hours IS NULL OR budget_hours >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_phases_dates CHECK (ends_on >= starts_on),
    UNIQUE (project_id, phase)
);

CREATE INDEX IF NOT EXISTS project_phases_project_idx ON project_phases (project_id);
CREATE INDEX IF NOT EXISTS project_phases_range_idx   ON project_phases (starts_on, ends_on);

-- ============================================================
-- allocations — FR-ALLOC
--
-- INTENT, as opposed to the fact recorded in time_entries. Comparing the two
-- is the entire point of the analytics, so they are never conflated: an
-- allocation is never updated to match reality, and a time entry never implies
-- an allocation.
--
-- Q-02: intent is a PERCENTAGE OF CAPACITY, where capacity is working days
-- minus approved leave and declared holidays — all of which 001 already knows.
-- Over-allocation (FR-ALLOC-04) is then simply concurrent percentages summing
-- past 100, which needs no extra bookkeeping.
-- ============================================================
CREATE TABLE IF NOT EXISTS allocations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    starts_on   date NOT NULL,
    ends_on     date NOT NULL,
    -- Above 100 is allowed at the row level on purpose: it is a real thing an
    -- admin may do mid-crunch, and the product's job is to SURFACE it
    -- (FR-ALLOC-04), not to make it unrecordable.
    percent     numeric(5,2) NOT NULL CHECK (percent > 0 AND percent <= 100),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES profiles(id) ON DELETE SET NULL,
    CONSTRAINT allocations_dates CHECK (ends_on >= starts_on)
);

CREATE INDEX IF NOT EXISTS allocations_user_idx    ON allocations (user_id, starts_on, ends_on);
CREATE INDEX IF NOT EXISTS allocations_project_idx ON allocations (project_id);

-- ============================================================
-- time_entries — FR-TIME
--
-- `date` is a calendar date, not a timestamp — 001 NFR-03, and the same
-- Asia/Kolkata trap applies.
--
-- Hours are numeric, never float. 001 §6.2 made that point for half-days of
-- leave; here the sums are far larger and quoted in budget conversations.
-- ============================================================
CREATE TABLE IF NOT EXISTS time_entries (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    date          date NOT NULL,
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    -- Stored, not derived at read time. FR-PROJ-03 lets phase dates move, and
    -- a historical entry must keep saying which phase it was logged against —
    -- otherwise editing a date silently reassigns past effort.
    phase_id      uuid REFERENCES project_phases(id) ON DELETE SET NULL,
    hours_office  numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_office >= 0),
    hours_home    numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_home >= 0),
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    -- An entry that records no hours is not a record of anything.
    CONSTRAINT time_entries_some_hours CHECK (hours_office + hours_home > 0),

    -- FR-TIME-05 is a per-DAY limit and is enforced in the service layer,
    -- which can see the whole day. This is the per-ENTRY sanity bound: no
    -- single line may exceed a day.
    CONSTRAINT time_entries_entry_bound CHECK (hours_office + hours_home <= 24)
);

-- One line per person, per day, per project. Logging twice against the same
-- project on the same day is a correction, not a second fact — the service
-- layer updates the existing row rather than accumulating duplicates that
-- would double-count in every total.
CREATE UNIQUE INDEX IF NOT EXISTS time_entries_one_per_project_day
    ON time_entries (user_id, date, project_id);

CREATE INDEX IF NOT EXISTS time_entries_user_date_idx ON time_entries (user_id, date);
CREATE INDEX IF NOT EXISTS time_entries_project_idx   ON time_entries (project_id, date);
CREATE INDEX IF NOT EXISTS time_entries_date_idx      ON time_entries (date);

-- ============================================================
-- updated_at maintenance — reuses the trigger function from 001
-- ============================================================
CREATE TRIGGER projects_touch       BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
CREATE TRIGGER project_phases_touch BEFORE UPDATE ON project_phases
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
CREATE TRIGGER allocations_touch    BEFORE UPDATE ON allocations
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
CREATE TRIGGER time_entries_touch   BEFORE UPDATE ON time_entries
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- ============================================================
-- Row-Level Security
--
-- As in 001: the backend uses the service-role key and bypasses all of this,
-- so these policies guard the browser's own connection and are defence in
-- depth. The backend enforces the real rules.
--
-- Q-08: an individual's timesheet is readable by the person, their lead, and
-- admins — the same rule as a leave reason (001 NFR-05). Project analytics
-- aggregate across everyone, but a named colleague's day is not browsable.
-- ============================================================
ALTER TABLE projects       ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_phases ENABLE ROW LEVEL SECURITY;
ALTER TABLE allocations    ENABLE ROW LEVEL SECURITY;
ALTER TABLE time_entries   ENABLE ROW LEVEL SECURITY;

-- Projects and phases are not secret: everyone needs the list to log against.
DROP POLICY IF EXISTS "projects_all_select" ON projects;
CREATE POLICY "projects_all_select" ON projects
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "project_phases_all_select" ON project_phases;
CREATE POLICY "project_phases_all_select" ON project_phases
    FOR SELECT TO authenticated USING (true);

-- Allocations: your own, your reports', or everything if you are an admin.
DROP POLICY IF EXISTS "allocations_self_select" ON allocations;
CREATE POLICY "allocations_self_select" ON allocations
    FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS "allocations_lead_select" ON allocations;
CREATE POLICY "allocations_lead_select" ON allocations
    FOR SELECT TO authenticated
    USING (public.is_lead_of(user_id) OR public.is_admin());

-- Time entries: the same three parties, and nobody else.
DROP POLICY IF EXISTS "time_entries_self_select" ON time_entries;
CREATE POLICY "time_entries_self_select" ON time_entries
    FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS "time_entries_lead_select" ON time_entries;
CREATE POLICY "time_entries_lead_select" ON time_entries
    FOR SELECT TO authenticated
    USING (public.is_lead_of(user_id) OR public.is_admin());

-- ============================================================
-- Audit — FR-TIME-09
--
-- The safety net, mirroring 001's bookings trigger: the backend writes richer
-- entries of its own, and this catches anything it forgets. The note is NOT
-- copied. It is something a colleague wrote about their own day, the audit log
-- is admin-readable and long-lived, and the log's job is to record that a
-- change happened rather than to archive what somebody said.
-- ============================================================
CREATE OR REPLACE FUNCTION public.log_time_entry_change()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    INSERT INTO audit_log (actor_id, actor_label, action, target_table, target_id, before, after)
    VALUES (
        COALESCE(NEW.user_id, OLD.user_id),
        'user',
        'time_entry.' || lower(TG_OP),
        'time_entries',
        COALESCE(NEW.id, OLD.id)::text,
        CASE WHEN TG_OP <> 'INSERT' THEN jsonb_build_object(
            'date', OLD.date, 'project_id', OLD.project_id,
            'hours_office', OLD.hours_office, 'hours_home', OLD.hours_home) END,
        CASE WHEN TG_OP <> 'DELETE' THEN jsonb_build_object(
            'date', NEW.date, 'project_id', NEW.project_id,
            'hours_office', NEW.hours_office, 'hours_home', NEW.hours_home) END
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER time_entries_audit
    AFTER INSERT OR UPDATE OR DELETE ON time_entries
    FOR EACH ROW EXECUTE FUNCTION public.log_time_entry_change();

-- ============================================================
-- Settings — the two numbers spec 002 leaves configurable
-- ============================================================
INSERT INTO app_settings (key, value, description) VALUES
    (
        'max_hours_per_day',
        '16',
        'Spec 002 Q-06. The most hours one person may log for one date. A '
        'sanity check against a mistyped 80, not a position on overwork.'
    ),
    (
        'timesheet_grace_days',
        '7',
        'Spec 002 Q-01. Days after the END of an entry''s week during which it '
        'stays editable; afterwards it is locked like a leave booking (001 '
        '§6.3). A same-day lock would guarantee a permanently incomplete '
        'timesheet, which FR-ANALYTICS-05 says is worse than none.'
    )
ON CONFLICT (key) DO NOTHING;
