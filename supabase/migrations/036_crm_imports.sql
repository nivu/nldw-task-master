-- 036_crm_imports.sql
--
-- Spec 007 (sales CRM) FR-CRM-MIG-01 to -08, FR-CRM-DUP-02, FR-CRM-WS-02;
-- Q-46, Q-55; data-model §3.17; plan §5.3, risk "PostgREST rpc statement
-- timeout on big imports".
--
-- The Twenty importer. Every import starts as a dry run; the real run needs
-- a dry run of the same workspace, kind and file (by SHA-256) from the last
-- 24 hours (FR-CRM-MIG-04), and writes through crm_apply (027) in chunks,
-- upserting on `twenty_id`, so a second run of the same file changes nothing
-- and writes no audit rows (FR-CRM-MIG-07).
--
-- 1. `crm_import_runs`: one row per dry run, marked applied when the real
--    run uses it. A log, not a record (plan §5.3): no audit trigger, written
--    only by `crm_record_import` below. The report holds counts, derived
--    lead stages, conflicts and attachments to re-link, never the file's
--    content.
--
-- 2. `twenty_id` on `crm_tasks` and `crm_notes`, unique per workspace when
--    set, as companies, people and deals have had since 029 and 030. Without
--    it a re-run could not match an imported task or note, and would add it
--    again (FR-CRM-MIG-07). Data-model §3.12 and §3.13 gain the column.
--
-- 3. `crm_similar_many(workspace, object, names, limit)`: `crm_similar`
--    (029) for many names in one call, so the dry run checks every new
--    company and person for possible duplicates (FR-CRM-DUP-02, -MIG-04)
--    without one request per row. Each name is answered by `crm_similar`
--    itself, so the threshold and the plan are the same.
--
-- 4. `crm_record_import(actor, run)`: adds a dry run, or marks one applied
--    or failed, and writes its audit row (`import.dry_run`,
--    `import.applied`, `import.failed`) with the counts and no row content,
--    in one transaction, in the actor's workspace only. Data-model §3.17
--    names crm_apply's `audit_only` op for these rows, but crm_apply accepts
--    only the audited table's own columns as values, so it cannot carry the
--    counts; this function writes the run and its audit row together
--    instead. The rows the import writes go through crm_apply as planned.
--
-- EXECUTE on the functions is revoked from PUBLIC, anon and authenticated
-- (data-model §5.12).
--
-- Reversible: DROP FUNCTION crm_record_import(jsonb, jsonb),
-- crm_similar_many(uuid, text, text[], integer);
-- DROP INDEX crm_notes_twenty, crm_tasks_twenty; ALTER TABLE crm_notes,
-- crm_tasks DROP COLUMN twenty_id; DROP TABLE crm_import_runs.

-- ---------------------------------------------------------------------------
-- 1. crm_import_runs — data-model §3.17 (log; no audit trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crm_import_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
    kind         text NOT NULL CHECK (kind IN ('twenty', 'csv')),
    -- NULL for a whole Twenty export; the object of a one-object file.
    object       text,
    file_sha256  text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    status       text NOT NULL DEFAULT 'dry_run'
                 CHECK (status IN ('dry_run', 'applied', 'failed')),
    report       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(report) = 'object'),
    options      jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(options) = 'object'),
    created_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    applied_at   timestamptz
);

CREATE INDEX IF NOT EXISTS crm_import_runs_recent
    ON crm_import_runs (workspace_id, created_at DESC);

ALTER TABLE crm_import_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON crm_import_runs FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. twenty_id on tasks and notes — FR-CRM-MIG-02, -07
-- ---------------------------------------------------------------------------

ALTER TABLE crm_tasks ADD COLUMN IF NOT EXISTS twenty_id text;
ALTER TABLE crm_notes ADD COLUMN IF NOT EXISTS twenty_id text;

CREATE UNIQUE INDEX IF NOT EXISTS crm_tasks_twenty
    ON crm_tasks (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS crm_notes_twenty
    ON crm_notes (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. crm_similar_many — FR-CRM-DUP-02 for the importer's dry run
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_similar_many(
    p_workspace uuid, p_object text, p_names text[], p_limit integer
)
RETURNS TABLE (idx integer, id uuid, name text, similarity real)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT n.idx::integer, s.id, s.name, s.similarity
    FROM unnest(p_names) WITH ORDINALITY AS n(name, idx)
    CROSS JOIN LATERAL public.crm_similar(p_workspace, p_object, n.name, p_limit) AS s
    WHERE btrim(coalesce(n.name, '')) <> ''
    ORDER BY n.idx, s.similarity DESC, s.name;
$$;

REVOKE ALL ON FUNCTION public.crm_similar_many(uuid, text, text[], integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_similar_many(uuid, text, text[], integer) TO service_role;

-- ---------------------------------------------------------------------------
-- 4. crm_record_import — the run log and its audit row (FR-CRM-MIG-04, -07)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_record_import(p_actor jsonb, p_run jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws     uuid := (p_actor ->> 'workspace_id')::uuid;
    who    uuid := (p_actor ->> 'user_id')::uuid;
    v_status text := coalesce(p_run ->> 'status', 'dry_run');
    row_j  jsonb;
BEGIN
    IF ws IS NULL OR coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'import') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'An import needs a workspace and an actor.';
    END IF;

    IF p_run ->> 'id' IS NULL THEN
        INSERT INTO crm_import_runs (workspace_id, kind, object, file_sha256, status, report,
                                     options, created_by)
        VALUES (ws, p_run ->> 'kind', p_run ->> 'object', p_run ->> 'file_sha256', 'dry_run',
                coalesce(p_run -> 'report', '{}'), coalesce(p_run -> 'options', '{}'), who)
        RETURNING to_jsonb(crm_import_runs.*) INTO row_j;
        v_status := 'dry_run';
    ELSE
        IF v_status NOT IN ('applied', 'failed') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A run is applied or failed.';
        END IF;
        UPDATE crm_import_runs r
        SET status = v_status,
            report = r.report || jsonb_build_object('applied', coalesce(p_run -> 'report', '{}')),
            options = coalesce(p_run -> 'options', r.options),
            applied_at = CASE WHEN v_status = 'applied' THEN now() ELSE r.applied_at END
        WHERE r.id = (p_run ->> 'id')::uuid AND r.workspace_id = ws
        RETURNING to_jsonb(r.*) INTO row_j;
        IF row_j IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such import run.';
        END IF;
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, after)
    VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'import.' || v_status,
            'crm_import_runs', row_j ->> 'id',
            jsonb_build_object('kind', row_j -> 'kind', 'object', row_j -> 'object',
                               'file_sha256', row_j -> 'file_sha256',
                               'counts', coalesce(p_run #> '{report,counts}', '{}')));
    RETURN row_j;
END;
$$;

REVOKE ALL ON FUNCTION public.crm_record_import(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_record_import(jsonb, jsonb) TO service_role;
