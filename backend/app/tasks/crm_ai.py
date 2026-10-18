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
| `crm_ai.summaries` | every 15 minutes | `summaries` |
| `crm_ai.meetings` | every 15 minutes | `meetings` (one Fireflies poll, one run per workspace) |
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.services.crm import jobs, meetings, review, summaries
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


@jobs.crm_job("summaries")
def _summaries(workspace: dict, now: datetime) -> dict:
    """FR-CRMAI-SUM-01 to -12: the workspace's pending threads summarised,
    behind phase 1 and live Gmail sync."""
    return summaries.run(workspace, now)


@celery_app.task(name="crm_ai.summaries", ignore_result=True)
def summarise_threads() -> dict:
    """Every 15 minutes, per workspace (NFR-CRMAI-05). Idempotent: a thread
    with no new message is not pending, so a second run makes no call."""
    result = _summaries(datetime.now(UTC))
    logger.info('{"event": "crm_summaries_done", "failed": %d}', len(result["failed"]))
    return result


def _meetings(now: datetime | None = None) -> dict:
    """FR-CRMAI-MEET-01 to -08: one Fireflies poll for every workspace, one
    `meetings` run per workspace, behind phase 2 (plan §5.6)."""
    return meetings.tick(now)


@celery_app.task(name="crm_ai.meetings", ignore_result=True)
def capture_meetings() -> dict:
    """Every 15 minutes (NFR-CRMAI-05: a note within 30 minutes of the
    transcript being ready). Idempotent: a transcript already noted is not
    fetched again, and a note is unique per transcript and workspace."""
    result = _meetings(datetime.now(UTC))
    logger.info('{"event": "crm_meetings_done", "failed": %d}', len(result["failed"]))
    return result
