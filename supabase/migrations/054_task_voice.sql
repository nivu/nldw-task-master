-- 054_task_voice.sql
--
-- Spec 008 (tasks) FR-TASK-VOICE-01 to -05; T10, TP-22; data-model §2.9.
--
-- A voice note's upload row and the private bucket that holds its audio
-- until it is transcribed. The audio lives in `task-voice` under
-- `<user_id>/<id>` and is deleted as soon as the worker has transcribed it
-- (or failed to), and in any case within 24 hours by `tasks.voice_sweep`
-- (FR-TASK-VOICE-02). The transcript is held only here, in the caller's own
-- row, and cleared when the last candidate is confirmed or dropped, or by
-- the same sweep (FR-TASK-VOICE-05).
--
-- Not audited: a transcript must never reach `audit_log`. Written directly
-- by `services/tasks/voice.py` and the worker with the service role. RLS on,
-- no policy, no grant to anon or authenticated. The bucket has no
-- `storage.objects` policy, so only the service role reaches it.
--
-- A candidate is marked handled by `voice_candidate_mark`, one conditional
-- UPDATE, never by writing back an array read earlier: two confirms of one
-- candidate cannot both pass, two requests on one note cannot undo each
-- other's flag, and a note the sweep has cleared is never written again.
-- `voice_upload_finish` clears the transcript once every candidate is
-- handled (FR-TASK-VOICE-04, -05).
--
-- Reversible: DROP FUNCTION voice_upload_finish(uuid), voice_candidate_mark(uuid,
-- integer, boolean); DROP TABLE voice_uploads; DELETE FROM storage.buckets
-- WHERE id = 'task-voice' (once it is empty).

CREATE TABLE IF NOT EXISTS voice_uploads (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    status         text NOT NULL DEFAULT 'uploaded'
                   CHECK (status IN ('uploaded', 'transcribing', 'transcribed', 'failed', 'done')),
    -- `<user_id>/<id>`; null once the audio is deleted.
    object_path    text,
    transcript     text CHECK (transcript IS NULL OR char_length(transcript) <= 20000),
    -- [{index, kind, text, handled}]: the split candidates, nothing more of
    -- the transcript.
    candidates     jsonb,
    -- A sentence safe to show, never a Groq response body.
    error_public   text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    transcribed_at timestamptz
);

CREATE INDEX IF NOT EXISTS voice_uploads_sweep
    ON voice_uploads (created_at)
    WHERE transcript IS NOT NULL OR object_path IS NOT NULL;

ALTER TABLE voice_uploads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON voice_uploads FROM anon, authenticated;

-- Sets candidate `p_index`'s `handled` to `p_handled`, only while the note
-- still holds its transcript and the flag is still the other way; false
-- when nothing changed (already handled, released, or swept).
CREATE OR REPLACE FUNCTION public.voice_candidate_mark(
    p_upload uuid, p_index integer, p_handled boolean
)
RETURNS boolean
LANGUAGE sql
SET search_path = public
AS $$
    WITH marked AS (
        UPDATE voice_uploads u
           SET candidates = (
                   SELECT jsonb_agg(
                              CASE WHEN (e.c ->> 'index')::integer = p_index
                                   THEN e.c || jsonb_build_object('handled', p_handled)
                                   ELSE e.c END
                              ORDER BY e.o)
                     FROM jsonb_array_elements(u.candidates) WITH ORDINALITY AS e(c, o))
         WHERE u.id = p_upload
           AND u.status = 'transcribed'
           AND u.transcript IS NOT NULL
           AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(u.candidates) AS e(c)
                    WHERE (e.c ->> 'index')::integer = p_index
                      AND (e.c ->> 'handled')::boolean = NOT p_handled
                      AND e.c ->> 'text' IS NOT NULL)
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM marked);
$$;

-- Once every candidate is handled: status `done`, the transcript and the
-- candidates' text cleared. False when a candidate is still open.
CREATE OR REPLACE FUNCTION public.voice_upload_finish(p_upload uuid)
RETURNS boolean
LANGUAGE sql
SET search_path = public
AS $$
    WITH finished AS (
        UPDATE voice_uploads u
           SET status = 'done',
               transcript = NULL,
               candidates = (
                   SELECT jsonb_agg(e.c || '{"text": null}'::jsonb ORDER BY e.o)
                     FROM jsonb_array_elements(u.candidates) WITH ORDINALITY AS e(c, o))
         WHERE u.id = p_upload
           AND u.status = 'transcribed'
           AND NOT EXISTS (
                   SELECT 1 FROM jsonb_array_elements(u.candidates) AS e(c)
                    WHERE NOT (e.c ->> 'handled')::boolean)
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM finished);
$$;

REVOKE ALL ON FUNCTION public.voice_candidate_mark(uuid, integer, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.voice_upload_finish(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.voice_candidate_mark(uuid, integer, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.voice_upload_finish(uuid) TO service_role;

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'task-voice',
    'task-voice',
    false,
    4194304,
    ARRAY['audio/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/x-m4a']
)
ON CONFLICT DO NOTHING;
