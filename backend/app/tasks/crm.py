"""The CRM's background work — spec 007 plan §5.11.

S3 adds the Trash purge; 1b adds the follow-up flags, digests and email sync,
with the job log of FR-CRM-JOB-01 (`crm_job_runs`, migration 037) around
them all.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.services.crm import records, store
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")


@celery_app.task(name="crm.purge_trash")
def purge_trash() -> dict:
    """FR-CRM-DEL-01 — permanently deletes what has been in a Trash for 90
    days, one pass per workspace, as the system.

    A workspace that fails is logged and the others still run. Idempotent: a
    second run finds nothing older than 90 days, so a missed night is caught
    up by the next one.
    """
    now = datetime.now(UTC)
    purged: dict[str, int] = {}
    failed: list[str] = []
    for workspace in store.list_workspaces():
        try:
            purged[workspace["key"]] = records.purge_expired(workspace["id"], now)
        except Exception:
            logger.exception('{"event": "crm_purge_failed", "workspace": "%s"}', workspace["key"])
            failed.append(workspace["key"])
    logger.info('{"event": "crm_purge_done", "purged": %d}', sum(purged.values()))
    return {"purged": purged, "failed": failed}
