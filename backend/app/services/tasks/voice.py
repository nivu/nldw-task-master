"""Voice capture — spec 008 FR-TASK-VOICE-01 to -05; T10, TP-22; Journey
4.10; contracts §2 voice routes; data-model §2.9; plan §5.10.

1. **Upload** (≤ 4 MB of audio): a `voice_uploads` row (status `uploaded`)
   and the audio in the private `task-voice` bucket under
   `<user_id>/<upload_id>`, then `tasks.transcribe_voice(upload_id)` is
   enqueued with the id only.
2. **Transcribe** (the worker): Groq turns the audio into text; the
   transcript and its candidates (`voice_split.split`) are written to the
   row and the job returns nothing; the audio is deleted in a `finally`,
   whether transcription worked or not (FR-TASK-VOICE-02).
3. **Read**: the caller's own row, each candidate not yet handled with its
   quick-add draft and questions, computed in the caller's zone today.
4. **Confirm** or **drop** one candidate. Nothing is saved before a
   confirm; a confirmed task is created through `create_task` with source
   `voice` (FR-TASK-VOICE-04). The candidate is marked handled first, by one
   conditional update (`voice_candidate_mark`, 054), so two confirms of it
   cannot both create a task, and released again if `create_task` refuses.
   When the last candidate is handled the transcript is cleared
   (`voice_upload_finish`), and the candidates' text with it, since the
   lines are the transcript (FR-TASK-VOICE-05).
5. **Sweep** (every 30 minutes): any audio or transcript older than 23 h
   30 min is deleted, failed ones included, so nothing outlives 24 hours.

The row is written directly with the service role, not through
`task_apply`, and is not audited: a transcript must never reach
`audit_log`. Logs carry the upload id only, never the transcript or a
candidate's text (NFR-TASK-04).

Confirming an inbox candidate needs the Ideas and Reading inbox (S4b); until
it exists the confirm is refused and the candidate stays to be dropped.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.config import settings
from app.domain.tasks import quick_add, rules, service, voice_split
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import TaskActor
from app.schemas.tasks import TaskCreate, TaskIn
from app.services import supabase as db
from app.services.tasks import groq, people

logger = logging.getLogger("nldw-task-master")

BUCKET: Final = "task-voice"
#: FR-TASK-VOICE-01; the bucket's `file_size_limit` (054).
MAX_BYTES: Final = 4 * 1024 * 1024
#: The bucket's `allowed_mime_types` (054).
AUDIO_TYPES: Final = (
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-m4a",
)
#: Data-model §2.9: `voice_uploads.transcript` holds at most this much.
MAX_TRANSCRIPT: Final = 20_000
#: `TaskCreate.title`'s limit.
MAX_TITLE: Final = 200
#: FR-TASK-VOICE-02, -05 — with the sweep every 30 minutes, anything older
#: than this goes before it is 24 hours old.
KEEP: Final = timedelta(hours=23, minutes=30)

NOT_SET_UP: Final = "Voice capture is not set up yet."
TOO_BIG: Final = "The voice note is larger than 4 MB."
NOT_AUDIO: Final = "Send the voice note as WebM, Ogg, MP3, MP4, WAV or M4A audio."
NO_SUCH_UPLOAD: Final = "No such voice note."
NOT_READY: Final = "The voice note has not been transcribed yet."
NO_SUCH_CANDIDATE: Final = "No such candidate."
ALREADY_HANDLED: Final = "That candidate has already been confirmed or dropped."
QUESTION_OPEN: Final = "Answer this candidate's question, or edit it, before confirming it."
NO_TITLE: Final = "This candidate has no title left; edit it before confirming it."
TOO_LONG: Final = (
    f"This candidate has a title over {MAX_TITLE} characters or more than "
    f"{rules.MAX_TAGS} tags; edit it before confirming it."
)
INBOX_NOT_YET: Final = "The Ideas and Reading inbox is not set up yet."
FAILED: Final = "The voice note could not be transcribed. Try again, or type it."
NOTHING_HEARD: Final = "No words were heard in the voice note."
EXPIRED: Final = "The voice note expired before it was transcribed."


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# The row and the bucket — the service role, no audit (data-model §2.9)
# ---------------------------------------------------------------------------


def put_audio(path: str, content: bytes, content_type: str) -> None:
    db.supabase.storage.from_(BUCKET).upload(path, content, {"content-type": content_type})


def get_audio(path: str) -> bytes:
    return db.supabase.storage.from_(BUCKET).download(path)


def delete_audio(path: str) -> None:
    db.supabase.storage.from_(BUCKET).remove([path])


def insert_upload(row: dict[str, Any]) -> dict[str, Any]:
    return db.supabase.table("voice_uploads").insert(row).execute().data[0]


def get_upload(upload_id: str) -> dict[str, Any] | None:
    rows = db.supabase.table("voice_uploads").select("*").eq("id", upload_id).execute().data
    return rows[0] if rows else None


def update_upload(upload_id: str, values: dict[str, Any]) -> None:
    db.supabase.table("voice_uploads").update(values).eq("id", upload_id).execute()


def mark_candidate(upload_id: str, index: int, handled: bool) -> bool:
    """`voice_candidate_mark` (054): false when nothing changed."""
    params = {"p_upload": upload_id, "p_index": index, "p_handled": handled}
    return db.supabase.rpc("voice_candidate_mark", params).execute().data is True


def finish_upload(upload_id: str) -> bool:
    """`voice_upload_finish` (054): true when the transcript was cleared."""
    params = {"p_upload": upload_id}
    return db.supabase.rpc("voice_upload_finish", params).execute().data is True


def expired_uploads(before: str) -> list[dict[str, Any]]:
    """Rows created before `before` that still hold audio or a transcript."""
    return (
        db.supabase.table("voice_uploads")
        .select("id, status, object_path, candidates")
        .lt("created_at", before)
        .or_("transcript.not.is.null,object_path.not.is.null")
        .execute()
        .data
        or []
    )


def enqueue(upload_id: str) -> None:
    """One `tasks.transcribe_voice` job, the id only."""
    from app.services.dispatch import fire_and_forget
    from app.tasks import task_jobs

    fire_and_forget(task_jobs.transcribe_voice, upload_id, label="tasks:transcribe_voice")


# ---------------------------------------------------------------------------
# Upload, read, confirm, drop — the routes
# ---------------------------------------------------------------------------


def configured() -> None:
    """503 while the worker holds no Groq key (plan §2.2)."""
    if not settings.TASKS_VOICE_CONFIGURED:
        raise TaskRefused(503, NOT_SET_UP)


def too_big() -> TaskRefused:
    refused = TaskRefused(413, TOO_BIG)
    refused.title = "Content Too Large"
    return refused


def upload(actor: TaskActor, content: bytes, content_type: str | None) -> dict[str, Any]:
    """FR-TASK-VOICE-01, -02 — holds the audio until it is transcribed and
    enqueues the transcription; `{upload_id, status}`."""
    configured()
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if media not in AUDIO_TYPES:
        raise TaskRefused(415, NOT_AUDIO)
    if len(content) > MAX_BYTES:
        raise too_big()
    upload_id = str(uuid.uuid4())
    path = f"{actor.user.id}/{upload_id}"
    insert_upload(
        {"id": upload_id, "user_id": actor.user.id, "status": "uploaded", "object_path": path}
    )
    put_audio(path, content, media)
    enqueue(upload_id)
    logger.info('{"event": "tasks_voice_uploaded", "upload_id": "%s"}', upload_id)
    return {"upload_id": upload_id, "status": "uploaded"}


def _own(actor: TaskActor, upload_id: str) -> dict[str, Any]:
    """The caller's own row; anyone else's is 404."""
    row = get_upload(upload_id)
    if row is None or row["user_id"] != actor.user.id:
        raise TaskRefused(404, NO_SUCH_UPLOAD)
    return row


def _today(actor: TaskActor) -> tuple[Any, str]:
    zone = actor.user.profile.get("timezone") or rules.DEFAULT_ZONE
    return rules.day_of(service._now(), zone), zone


def _task_draft(actor: TaskActor, parsed: quick_add.Parsed) -> dict[str, Any]:
    """The quick-add preview's `draft` (contracts §1)."""
    assignee = parsed.assignee or {"id": actor.user.id, "name": actor.user.display_name}
    return {
        "title": parsed.title,
        "due_on": parsed.due_on.isoformat() if parsed.due_on else None,
        "due_weekday": quick_add.weekday_name(parsed.due_on) if parsed.due_on else None,
        "bucket": parsed.bucket,
        "tags": list(parsed.tags),
        "assignee": assignee,
        "links": [],
    }


def read(actor: TaskActor, upload_id: str) -> dict[str, Any]:
    """Contracts §2 `GET /tasks/voice/{upload_id}` — the status, and each
    candidate with its draft and questions while it is not handled."""
    row = _own(actor, upload_id)
    today, zone = _today(actor)
    offered: list[dict[str, Any]] | None = None
    out = []
    for c in row.get("candidates") or []:
        item: dict[str, Any] = {
            "index": c["index"],
            "kind": c["kind"],
            "text": c["text"],
            "handled": c["handled"],
        }
        if not c["handled"] and c["text"]:
            if offered is None:
                offered = people.people(actor)
            if c["kind"] == "task":
                parsed = voice_split.task_draft(c["text"], today, offered, zone)
                item["draft"] = _task_draft(actor, parsed)
                item["questions"] = [
                    {
                        "kind": q.kind,
                        "prompt": q.prompt,
                        "options": [{"value": o.value, "label": o.label} for o in q.options],
                    }
                    for q in parsed.questions
                ]
            else:
                item["draft"] = voice_split.inbox_draft(c["text"], today, offered, zone)
        out.append(item)
    return {"status": row["status"], "candidates": out, "error": row.get("error_public")}


def _candidate(row: dict[str, Any], index: int) -> dict[str, Any]:
    """One candidate still to be handled: 409 before the transcript, 404 one
    that does not exist (or was swept), 409 one already handled."""
    if row["status"] in ("uploaded", "transcribing"):
        raise TaskRefused(409, NOT_READY)
    found = next((c for c in row.get("candidates") or [] if c["index"] == index), None)
    if found is None or (not found["handled"] and not found["text"]):
        raise TaskRefused(404, NO_SUCH_CANDIDATE)
    if found["handled"]:
        raise TaskRefused(409, ALREADY_HANDLED)
    return found


def _claim(upload_id: str, index: int) -> None:
    """Marks one candidate handled, or 409 when another request (or the
    sweep) got there first."""
    if not mark_candidate(upload_id, index, True):
        raise TaskRefused(409, ALREADY_HANDLED)


def _finish(upload_id: str) -> None:
    """On the last candidate the transcript and the candidates' text are
    cleared (FR-TASK-VOICE-05)."""
    if finish_upload(upload_id):
        logger.info('{"event": "tasks_voice_done", "upload_id": "%s"}', upload_id)


def confirm(actor: TaskActor, upload_id: str, index: int, draft: TaskIn | None) -> dict[str, Any]:
    """FR-TASK-VOICE-04 — saves one task candidate, as drafted or as edited
    (`draft`, the fields of `POST /tasks`), with source `voice`."""
    row = _own(actor, upload_id)
    candidate = _candidate(row, index)
    if candidate["kind"] != "task":
        raise TaskRefused(422, INBOX_NOT_YET)
    if draft is None:
        today, zone = _today(actor)
        parsed = voice_split.task_draft(candidate["text"], today, people.people(actor), zone)
        if parsed.questions:
            raise TaskRefused(422, QUESTION_OPEN)
        if not parsed.title:
            raise TaskRefused(422, NO_TITLE)
        if len(parsed.title) > MAX_TITLE or len(parsed.tags) > rules.MAX_TAGS:
            raise TaskRefused(422, TOO_LONG)
        fields: dict[str, Any] = {
            "title": parsed.title,
            "due_on": parsed.due_on,
            "bucket": parsed.bucket,
            "tags": list(parsed.tags),
            "assignee_id": parsed.assignee["id"] if parsed.assignee else None,
        }
    else:
        fields = draft.model_dump(exclude={"assignee"}, exclude_unset=True)
        if draft.assignee is not None:
            if draft.assignee_id is not None:
                raise TaskRefused(422, "Give the assignee once, by id or by name.")
            fields["assignee_id"] = people.resolve(actor, draft.assignee)
    payload = TaskCreate(**fields, source="voice")
    _claim(row["id"], index)
    try:
        task = service.create_task(actor, payload)
    except BaseException:
        mark_candidate(row["id"], index, False)
        raise
    _finish(row["id"])
    return task


def drop(actor: TaskActor, upload_id: str, index: int) -> dict[str, bool]:
    """FR-TASK-VOICE-04 — drops one candidate; nothing is saved."""
    row = _own(actor, upload_id)
    _candidate(row, index)
    _claim(row["id"], index)
    _finish(row["id"])
    return {"handled": True}


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


def transcribe(upload_id: str) -> None:
    """FR-TASK-VOICE-02, -03, -05 — the transcript and its candidates are
    written to the row; the audio is deleted in every case. Returns nothing,
    so no transcript reaches the Celery result backend."""
    row = get_upload(upload_id)
    if row is None or row["status"] != "uploaded" or not row.get("object_path"):
        return
    path = row["object_path"]
    update_upload(upload_id, {"status": "transcribing"})
    try:
        text = groq.transcribe(get_audio(path))[:MAX_TRANSCRIPT]
        candidates = [
            {"index": c.index, "kind": c.kind, "text": c.text, "handled": False}
            for c in voice_split.split(text)
        ]
        if not candidates:
            update_upload(upload_id, {"status": "failed", "error_public": NOTHING_HEARD})
        else:
            update_upload(
                upload_id,
                {
                    "status": "transcribed",
                    "transcript": text,
                    "candidates": candidates,
                    "transcribed_at": _now().isoformat(),
                },
            )
        logger.info(
            '{"event": "tasks_voice_transcribed", "upload_id": "%s", "candidates": %d}',
            upload_id,
            len(candidates),
        )
    except Exception as exc:
        reason = str(exc) if isinstance(exc, groq.TranscriptionFailed) else type(exc).__name__
        logger.warning(
            '{"event": "tasks_voice_failed", "upload_id": "%s", "reason": "%s"}',
            upload_id,
            reason,
        )
        update_upload(upload_id, {"status": "failed", "error_public": FAILED})
    finally:
        delete_audio(path)
        update_upload(upload_id, {"object_path": None})


def sweep() -> int:
    """FR-TASK-VOICE-02, -05 — deletes the audio and transcript of every
    note older than `KEEP`, failed ones included; the number swept."""
    before = (_now() - KEEP).isoformat()
    swept = 0
    for row in expired_uploads(before):
        if row.get("object_path"):
            delete_audio(row["object_path"])
        values: dict[str, Any] = {
            "object_path": None,
            "transcript": None,
            "candidates": [{**c, "text": None} for c in row.get("candidates") or []],
        }
        if row["status"] in ("uploaded", "transcribing"):
            values |= {"status": "failed", "error_public": EXPIRED}
        elif row["status"] == "transcribed":
            values["status"] = "done"
        update_upload(row["id"], values)
        swept += 1
    logger.info('{"event": "tasks_voice_sweep_done", "swept": %d}', swept)
    return swept
