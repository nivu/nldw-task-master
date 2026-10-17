"""Tasks' background work — spec 008 plan §5.8.

Named `task_jobs`, not `tasks`, so Celery's autodiscovery convention is never
triggered by accident; imported in `app/tasks/__init__.py`.

| Task | Beat | Notes |
|---|---|---|
| `tasks.purge_deleted` | 01:20 daily | tasks with no workspace deleted more than 90 days ago (TP-12) |
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.services.tasks import store
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
