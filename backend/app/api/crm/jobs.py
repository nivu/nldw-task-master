"""The job log for a workspace's Admins — spec 007 contracts §2.10
`GET /crm/jobs`; FR-CRM-JOB-01, -03.

HTTP only. A workspace's own runs, with their `error` (its Admins are its
members, so a record named there is theirs to see). A run with no workspace
(the digest tick spans workspaces) is never listed here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmAdminDep
from app.services.crm import jobs, store

router = APIRouter(prefix="/crm", tags=["crm"])

JobName = Literal["email_sync", "flags", "digests", "reminders", "trash_purge", "automations"]


@router.get("/jobs")
def list_jobs(
    ctx: CrmAdminDep,
    job: JobName | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """`{runs: [...], stale_jobs: [...], jobs: [{job, label, every_minutes}]}`."""
    rows = store.list_job_runs(ctx.workspace_id, job=job, limit=limit)
    return {
        "runs": [
            jobs.present_run(r, with_error=True)
            for r in rows
            if r.get("workspace_id") == ctx.workspace_id
        ],
        "stale_jobs": jobs.stale_for(ctx),
        "jobs": [
            {
                "job": name,
                "label": spec.label,
                "every_minutes": int(spec.every.total_seconds() // 60),
            }
            for name, spec in jobs.JOBS.items()
            if name != "digests"
        ],
    }
