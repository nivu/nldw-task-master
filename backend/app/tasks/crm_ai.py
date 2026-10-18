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
| `crm_ai.enrichment` | every 15 minutes | starts `enrichment` (a run or a skip per workspace) |
| `crm_ai.enrichment_batch` | enqueued, a minute apart | ends its workspace's `enrichment` run |
| `crm_ai.ai_fields` | every 15 minutes | `ai_fields` |
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.services.crm import ai_fields, enrichment, jobs, meetings, review, summaries
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


def _enrichment(now: datetime | None = None) -> dict:
    """FR-CRMAI-ENR-07: per workspace, a skip while phase 2 is off, an
    empty run, or a run whose batches follow (plan §5.1)."""
    return enrichment.tick(now)


@celery_app.task(name="crm_ai.enrichment", ignore_result=True)
def enrich_companies() -> dict:
    """Every 15 minutes. Idempotent: a company enriched from the same site is
    not queued, and a running run holds its workspace's lock."""
    result = _enrichment(datetime.now(UTC))
    logger.info('{"event": "crm_enrichment_tick_done", "failed": %d}', len(result["failed"]))
    return result


@celery_app.task(name="crm_ai.enrichment_batch", ignore_result=True)
def enrichment_batch(args: dict) -> dict:
    """At most 10 companies of one workspace's run, then the next batch a
    minute later or the run's end (FR-CRMAI-ENR-07). `args` holds ids, the
    run's start and counts, never text."""
    return enrichment.batch(args)


@jobs.crm_job("ai_fields")
def _ai_fields(workspace: dict, now: datetime) -> dict:
    """FR-CRMAI-FIELD-03 to -05: the workspace's AI field values computed
    where their inputs changed, at most once an hour per record, behind
    phase 3 (Q-38)."""
    return ai_fields.run(workspace, now)


@celery_app.task(name="crm_ai.ai_fields", ignore_result=True)
def compute_ai_fields() -> dict:
    """Every 15 minutes, per workspace. Idempotent: a value whose inputs did
    not change and that nobody marked is not computed again."""
    result = _ai_fields(datetime.now(UTC))
    logger.info('{"event": "crm_ai_fields_done", "failed": %d}', len(result["failed"]))
    return result
