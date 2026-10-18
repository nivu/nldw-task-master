-- 051_task_import_runs.sql
--
-- Spec 008 (tasks) FR-TASK-IMP-01 to -08; TP-10, TP-17; data-model §2.3.
--
-- The Life OS import's run record. One row per dry run, marked applied (or
-- failed) when the real run uses it. The real run checks the file against
-- `file_sha256` and the dry run's age against `dry_run_at` (24 hours,
-- TP-17), and reads the owner mapping the owner confirmed from
-- `owner_mapping` (FR-TASK-IMP-04).
--
-- The CSV is parsed in memory and never stored (FR-TASK-IMP-08): the row
-- keeps counts, the owner mapping, the fixed status and priority mapping as
-- applied to the file, and the file hash. `report` holds unmapped values and
-- ambiguous `D/M/YYYY` dates by row number, never a cell's text other than
-- the owner, status or priority value being mapped.
--
-- Unlike `crm_import_runs` (036, a log), the run is audited: it is written
-- only through `task_apply` (049), which already accepts this table once it
-- carries `task_import_runs_crm_audit`, and re-checks that the actor is the
-- run's uploader. The audit rows have no workspace and are left out of the
-- TVA audit by `services/supabase.list_audit` (FR-TASK-VIS-05,
-- FR-TASK-CHG-07). RLS on, no policy, no grant to anon or authenticated: the
-- backend reads it.
--
-- Reversible: DROP TABLE task_import_runs.

CREATE TABLE IF NOT EXISTS task_import_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    uploader_id   uuid NOT NULL REFERENCES profiles(id),
    file_sha256   text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    -- {sheet value: email | null}; "(blank)" is the key of a blank cell.
    owner_mapping jsonb NOT NULL CHECK (jsonb_typeof(owner_mapping) = 'object'),
    -- {status: {sheet value: key | null}, priority: {...}}: a record of the
    -- fixed mapping, not editable (Journey 4.1).
    value_mapping jsonb NOT NULL,
    status        text NOT NULL DEFAULT 'dry_run'
                  CHECK (status IN ('dry_run', 'applied', 'failed')),
    -- {rows, open, waiting, done, cancelled, skipped, existing, repeated, created}
    counts        jsonb NOT NULL,
    report        jsonb NOT NULL DEFAULT '{}',
    dry_run_at    timestamptz NOT NULL DEFAULT now(),
    applied_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS task_import_runs_recent
    ON task_import_runs (uploader_id, created_at DESC);

DROP TRIGGER IF EXISTS task_import_runs_touch ON task_import_runs;
CREATE TRIGGER task_import_runs_touch BEFORE UPDATE ON task_import_runs
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS task_import_runs_crm_audit ON task_import_runs;
CREATE TRIGGER task_import_runs_crm_audit AFTER INSERT OR UPDATE OR DELETE ON task_import_runs
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE task_import_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON task_import_runs FROM anon, authenticated;
