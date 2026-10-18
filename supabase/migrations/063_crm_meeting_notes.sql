-- 063_crm_meeting_notes.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-MEET-04 to -08; NFR-CRMAI-01;
-- P-5; Q-11, Q-41; data-model §3.4, §5, §6; plan §5.6.
--
-- A client call recorded in Fireflies becomes a meeting note on the right
-- company (and deal). The note is an ordinary note of kind `meeting`
-- (007 FR-CRM-NOTE-03): its title, attendees, summary, decisions, action
-- items and transcript link live in its plain-text body (Q-11). The
-- transcript itself is never stored (P-3).
--
-- 1. `crm_notes` gains two columns and nothing else (Q-11):
--    `source_ref` (`fireflies:<transcript id>`), unique per workspace by a
--    partial index, so a re-poll never makes a second note (FR-CRMAI-MEET-07,
--    P-5); and `ai_provenance` (`{source_kind, model, generated_at,
--    transcript_updated_at, transcript_url}`), whose transcript link is the
--    only URL stored (FR-CRMAI-LLM-04).
-- 2. `crm_store_meeting_note(actor, plan)`: in one transaction, as the
--    `sync` actor (FR-CRMAI-MEET-05), writes the note through the audited
--    `crm_notes` insert path `crm_apply` uses (so its audit row is
--    `note.created` with actor kind `sync`) with its links to the matched
--    companies, people and the one deal FR-CRMAI-MEET-04 found; or, for a
--    transcript updated since, rewrites its body and provenance only while
--    no person has edited the note (an audit row on it by a `user` other
--    than its creation), else changes nothing (Q-41). Then it stores the
--    meeting's Review Queue items through `crm_review_upsert` (061), each
--    pointing at the note, a `deal_link` item targeting it
--    (FR-CRMAI-MEET-04, -06).
--
-- No function 007 or 008 defined is replaced. `crm_notes` keeps its audit
-- trigger, RLS and grants; the new columns hold no source text. EXECUTE on
-- the new function is revoked from PUBLIC, anon and authenticated and
-- granted to service_role (data-model §5).
--
-- Reversible: DROP FUNCTION crm_store_meeting_note(jsonb, jsonb); DROP INDEX
-- crm_notes_source_ref; ALTER TABLE crm_notes DROP COLUMN ai_provenance,
-- DROP COLUMN source_ref (the notes stay as plain notes).

-- ---------------------------------------------------------------------------
-- 1. crm_notes columns — data-model §3.4
-- ---------------------------------------------------------------------------

ALTER TABLE crm_notes
    ADD COLUMN IF NOT EXISTS source_ref text CHECK (char_length(source_ref) <= 200),
    ADD COLUMN IF NOT EXISTS ai_provenance jsonb
        CHECK (ai_provenance IS NULL OR jsonb_typeof(ai_provenance) = 'object');

-- P-5, FR-CRMAI-MEET-07: one note per transcript per workspace.
CREATE UNIQUE INDEX IF NOT EXISTS crm_notes_source_ref
    ON crm_notes (workspace_id, source_ref) WHERE source_ref IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. crm_store_meeting_note — data-model §3.4 (FR-CRMAI-MEET-04 to -07)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_store_meeting_note(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws       uuid := (p_actor ->> 'workspace_id')::uuid;
    ref      text := p_plan ->> 'source_ref';
    existing crm_notes%ROWTYPE;
    made     uuid;
    created  boolean := false;
    updated  boolean := false;
    link     jsonb;
    items    jsonb;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('sync', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF ref IS NULL OR ref NOT LIKE 'fireflies:_%' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A meeting note needs its transcript.';
    END IF;
    -- The audit trigger records the note under this actor (FR-CRMAI-MEET-05).
    PERFORM set_config('crm.actor', p_actor::text, true);
    PERFORM set_config('crm.action', '', true);

    SELECT * INTO existing FROM crm_notes
    WHERE workspace_id = ws AND source_ref = ref
    FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO crm_notes (workspace_id, body, kind, occurred_on, source_ref, ai_provenance)
        VALUES (
            ws,
            p_plan ->> 'body',
            'meeting',
            (p_plan ->> 'occurred_on')::date,
            ref,
            p_plan -> 'ai_provenance'
        )
        RETURNING id INTO made;
        -- Each link held to this workspace by crm_check_refs (CRM04).
        FOR link IN SELECT value FROM jsonb_array_elements(coalesce(p_plan -> 'links', '[]')) LOOP
            INSERT INTO crm_note_links (workspace_id, note_id, record_type, record_id)
            VALUES (ws, made, link ->> 'record_type', (link ->> 'record_id')::uuid)
            ON CONFLICT (note_id, record_type, record_id) DO NOTHING;
        END LOOP;
        created := true;
    ELSE
        made := existing.id;
        -- FR-CRMAI-MEET-07: an unchanged transcript changes nothing.
        IF (p_plan #>> '{ai_provenance,transcript_updated_at}')::timestamptz
           <= coalesce((existing.ai_provenance ->> 'transcript_updated_at')::timestamptz,
                       '-infinity'::timestamptz) THEN
            RETURN jsonb_build_object('note_id', made, 'created', false, 'updated', false,
                                      'items', '[]'::jsonb);
        END IF;
        -- Q-41: once a person has edited the note (or moved it to the Trash),
        -- AI updates stop.
        IF EXISTS (
            SELECT 1 FROM audit_log a
            WHERE a.workspace_id = ws
              AND a.target_table = 'crm_notes'
              AND a.target_id = made::text
              AND a.actor_label = 'user'
              AND a.action <> 'note.created'
        ) THEN
            RETURN jsonb_build_object('note_id', made, 'created', false, 'updated', false,
                                      'edited_by_person', true, 'items', '[]'::jsonb);
        END IF;
        UPDATE crm_notes
        SET body = p_plan ->> 'body',
            ai_provenance = p_plan -> 'ai_provenance'
        WHERE id = made;
        updated := true;
    END IF;

    -- FR-CRMAI-MEET-04, -06: the meeting's suggestions point at the note; a
    -- deal_link item targets it ("$note" in its key stands for its id).
    SELECT coalesce(jsonb_agg(
               CASE WHEN i.value ->> 'type' = 'deal_link' THEN
                   i.value || jsonb_build_object(
                       'record_type', 'note',
                       'record_id', made,
                       'suggestion', jsonb_build_object('note_id', made),
                       'dedupe_key', replace(i.value ->> 'dedupe_key', '$note', made::text))
               ELSE i.value END
               || jsonb_build_object(
                   'source_link', jsonb_build_object('record_type', 'note', 'record_id', made))
               ORDER BY i.n), '[]')
    INTO items
    FROM jsonb_array_elements(coalesce(p_plan -> 'items', '[]')) WITH ORDINALITY AS i(value, n);
    items := crm_review_upsert(p_actor, items);
    RETURN jsonb_build_object('note_id', made, 'created', created, 'updated', updated,
                              'items', items);
END;
$$;

-- ---------------------------------------------------------------------------
-- Grants — data-model §5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_store_meeting_note(jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_store_meeting_note(jsonb, jsonb) TO service_role;
