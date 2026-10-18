"""Tasks' background work — spec 008 plan §5.8.

Named `task_jobs`, not `tasks`, so Celery's autodiscovery convention is never
triggered by accident; imported in `app/tasks/__init__.py`.

| Task | Beat | Notes |
|---|---|---|
| `tasks.purge_deleted` | 01:20 daily | tasks with no workspace deleted more than 90 days ago (TP-12) |
| `tasks.send_notice` | enqueued on the event | ids only; text built at send; once per task, event, recipient and time (FR-TASK-DEL-07) |
| `tasks.transcribe_voice` | enqueued on upload | `ignore_result=True`; writes the transcript to the upload row and returns nothing; deletes the audio in `finally` (FR-TASK-VOICE-02, -05) |
| `tasks.voice_sweep` | every 30 min | deletes audio and transcripts before they are 24 hours old, failed ones included (FR-TASK-VOICE-02, -05) |
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.services.tasks import notices, store, voice
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")

#: FR-TASK-DATA-11, TP-12.
KEEP_DAYS = 90
#: The purge runs as the system (data-model §5.3).
SYSTEM = {"user_id": None, "kind": "system", "channel": None}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


@celery_app.task(name="tasks.purge_deleted")
def purge_deleted() -> int:
    """FR-TASK-DATA-11 — permanently removes tasks with no workspace that have
    been deleted for 90 days, as the system; their links go with them. CRM
    tasks keep the CRM Trash and its purge. Idempotent: a missed night is
    caught up by the next one."""
    purged = store.purge_expired(SYSTEM, (_now() - timedelta(days=KEEP_DAYS)).isoformat())
    logger.info('{"event": "tasks_purge_done", "purged": %d}', purged)
    return purged


@celery_app.task(name="tasks.send_notice")
def send_notice(task_id: str, event: str, recipient_id: str, occurred_at: str) -> dict:
    """FR-TASK-DEL-01, -05, -07 — one delegation message, from ids only; the
    text is built when it is sent, and a retry sends nothing twice."""
    return notices.send(task_id, event, recipient_id, occurred_at)


@celery_app.task(name="tasks.transcribe_voice", ignore_result=True)
def transcribe_voice(upload_id: str) -> None:
    """FR-TASK-VOICE-02, -05 — transcribes one voice note into its upload
    row, from the id only; returns nothing, so no transcript reaches the
    result backend. The audio is deleted whatever happens."""
    voice.transcribe(upload_id)


@celery_app.task(name="tasks.voice_sweep")
def voice_sweep() -> int:
    """FR-TASK-VOICE-02, -05 — deletes every voice note's audio and
    transcript before it is 24 hours old, failed ones included."""
    return voice.sweep()
