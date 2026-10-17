"""The CRM intelligence layer's background work — spec 009 FR-CRMAI-JOB-01,
-02, -04; plan §5.1.

Every task here is named `crm_ai.*`, so `app/worker.py` routes it to the
`crm-ai` queue, served only by `worker-crm-ai` (concurrency 1). Each stores
no result (`ignore_result=True`), and its arguments are ids at most, never
text (FR-CRMAI-JOB-02). Each periodic job records one run per workspace
through `@crm_job` (services/crm/jobs.py).

| Task | Beat | Jobs it records |
|---|---|---|
| `crm_ai.review_expiry` | 01:30 daily | `review_expiry` |
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.services.crm import jobs, review
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")


@jobs.crm_job("review_expiry")
def _review_expiry(workspace: dict, now: datetime) -> dict:
    """FR-CRMAI-REV-07: untouched items expire at 14 days, decided ones are
    reduced to dedupe tombstones at 90. Not behind a phase switch: items of
    a switched-off phase still expire (FR-CRMAI-FLAG-04)."""
    return review.expire(workspace["id"], now)


@celery_app.task(name="crm_ai.review_expiry", ignore_result=True)
def review_expiry() -> dict:
    """Daily, per workspace, as the system. Idempotent: a second run finds
    nothing left to expire or tombstone."""
    result = _review_expiry(datetime.now(UTC))
    logger.info('{"event": "crm_review_expiry_done", "failed": %d}', len(result["failed"]))
    return result
