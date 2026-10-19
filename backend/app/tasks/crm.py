"""The CRM's background work — spec 007 plan §5.11.

S3 adds the Trash purge; 1b adds the follow-up flags, digests and email sync,
with the job log of FR-CRM-JOB-01 (`crm_job_runs`, migration 037) around
them all: every run is recorded per workspace through `@crm_job`
(services/crm/jobs.py), a failing workspace never stops the others, and
three failures in a row alert the admins.

| Task | Beat | Jobs it records |
|---|---|---|
| `crm.purge_trash` | 01:10 daily | `trash_purge` |
| `crm.recompute_flags` | every 30 min | `flags`, then `automations` (stale-deal notices, follow-up rules) |
| `crm.digest_tick` | every 15 min | `digests` (no workspace), then `reminders` |
| `crm.email_sync_tick` | every 15 min | `email_sync` (a run or a skip per workspace) |
| `crm.email_sync_page` | enqueued, `crm_sync` queue | ends its workspace's `email_sync` run |
| `crm.capture_thread` | enqueued, `crm_sync` queue | none (spec 010: files one conversation that became known) |
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.domain.crm.followups import SWITCHES as RULES
from app.services.crm import digests, email_sync, flags, followups, jobs, notifications, records
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")


@jobs.crm_job("trash_purge")
def _purge(workspace: dict, now: datetime) -> dict:
    return {"purged": records.purge_expired(workspace["id"], now)}


@celery_app.task(name="crm.purge_trash")
def purge_trash() -> dict:
    """FR-CRM-DEL-01 — permanently deletes what has been in a Trash for 90
    days, one pass per workspace, as the system.

    A workspace that fails is logged and the others still run. Idempotent: a
    second run finds nothing older than 90 days, so a missed night is caught
    up by the next one.
    """
    result = _purge(datetime.now(UTC))
    purged = {
        key: run["counts"].get("purged", 0)
        for key, run in result["runs"].items()
        if run["outcome"] == "ok"
    }
    logger.info('{"event": "crm_purge_done", "purged": %d}', sum(purged.values()))
    return {"purged": purged, "failed": result["failed"]}


# ---------------------------------------------------------------------------
# Flags, the stale-deal notice and the follow-up rules — automations 4, 5
# and 11 to 14 (spec 009 FR-CRMAI-CHG-06)
# ---------------------------------------------------------------------------


@jobs.crm_job("flags")
def _flags(workspace: dict, now: datetime) -> dict:
    """Automation 4 (FR-CRM-FLAG-02): not switchable."""
    return flags.recompute_workspace(workspace["id"], now)


@jobs.crm_job("automations")
def _automations(workspace: dict, now: datetime) -> dict:
    """Automation 5 and the follow-up rules R-1 to R-4 (spec 009
    FR-CRMAI-JOB-05), each behind its own switch (FR-CRM-AUTO-02); the run
    is skipped only when all five are off."""
    on = {rule: notifications.automation_on(workspace["id"], key) for rule, key in RULES.items()}
    stale = notifications.automation_on(workspace["id"], "stale_notice")
    if not stale and not any(on.values()):
        followups.idle(workspace, now, followups.R1_OFF)
        raise jobs.Skipped("The stale-deal notice and the follow-up rules are switched off.")
    counts = notifications.stale_deal_notices(workspace, now) if stale else {}
    return {**counts, **followups.run(workspace, now, on)}


@celery_app.task(name="crm.recompute_flags")
def recompute_flags() -> dict:
    """Every 30 minutes: every workspace's flags, then the stale-deal notices
    they raise and the follow-up rules (spec 009)."""
    now = datetime.now(UTC)
    return {"flags": _flags(now), "automations": _automations(now)}


# ---------------------------------------------------------------------------
# Digests and task reminders — automations 6 and 7
# ---------------------------------------------------------------------------


@jobs.crm_job("digests", per_workspace=False)
def _digests(workspace: None, now: datetime) -> dict:
    """Automation 6: each person's daily digest at 09:00 their time, and the
    weekly admin digest on Mondays, once each."""
    return digests.tick(workspace, now)


@jobs.crm_job("reminders")
def _reminders(workspace: dict, now: datetime) -> dict:
    """Automation 7, behind its switch."""
    if not notifications.automation_on(workspace["id"], "task_reminders"):
        raise jobs.Skipped("Task reminders are switched off.")
    return notifications.task_reminders(workspace, now)


@celery_app.task(name="crm.digest_tick")
def digest_tick() -> dict:
    """Every 15 minutes: digests for whoever is at 09:00 now, and task
    reminders."""
    now = datetime.now(UTC)
    return {"digests": _digests(now), "reminders": _reminders(now)}


# ---------------------------------------------------------------------------
# Gmail label sync — FR-CRM-SYNC-04, automations 2 and 3
# ---------------------------------------------------------------------------


@celery_app.task(name="crm.email_sync_tick")
def email_sync_tick() -> dict:
    """Every 15 minutes, on the shared worker: checks each workspace's switch
    and `CRM_GMAIL_CONFIGURED` (never the key), takes the single-flight lock
    and enqueues the first page onto `crm_sync`."""
    return email_sync.tick(datetime.now(UTC))


@celery_app.task(name="crm.email_sync_page", soft_time_limit=240, time_limit=300)
def email_sync_page(args: dict) -> dict:
    """One page of one mailbox, on `worker-crm-sync` only (the one process
    with the key), well inside the worker's 10-minute limit; it enqueues the
    next page itself and resumes from the cursor stored on the mailbox."""
    return email_sync.page(args, datetime.now(UTC))


@celery_app.task(name="crm.capture_thread", ignore_result=True, soft_time_limit=240, time_limit=300)
def capture_thread(workspace_id: str, mailbox_id: str, thread_id: str) -> None:
    """Spec 010 plan §5.1, §5.9 — one conversation that became known filed as
    known-contact capture files it, on `worker-crm-sync` only (the process
    with the Gmail key). Its arguments are ids, never text. Not a job of the
    job log: a failure is logged with the ids and the kind of error and not
    retried; the conversation is filed when its next message arrives
    (PD-10). Idempotent: a stored message is not stored twice."""
    try:
        counts = email_sync.file_thread(workspace_id, mailbox_id, thread_id)
    except Exception as exc:  # noqa: BLE001 - logged with ids only, PD-10
        logger.warning(
            '{"event": "crm_capture_thread_failed", "workspace_id": "%s", "mailbox_id": "%s", '
            '"thread_id": "%s", "error": "%s"}',
            workspace_id,
            mailbox_id,
            thread_id,
            type(exc).__name__,
        )
        return
    logger.info(
        '{"event": "crm_capture_thread_done", "workspace_id": "%s", "stored": %d}',
        workspace_id,
        counts.get("stored", 0),
    )
