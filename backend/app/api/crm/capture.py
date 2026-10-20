"""Settings, Email capture — spec 010 contracts §2.1 to §2.5; FR-CAP-DIS-01,
-03, -10, FR-CAP-RPT-01 to -04.

HTTP only, workspace Admins only: a Member or Viewer is refused 403 and a
non-member 404, as every Admin route. The routes read the report and open
a dry run, apply a finished one, and edit the ignore list; the work itself is done by `crm_ai.capture` on `worker-crm-ai`,
since the API holds neither the Gmail nor the Claude key. The mode is
changed through `PATCH /crm/settings/capture`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.crm.deps import CrmAdminDep
from app.schemas.crm import CaptureIgnoreIn
from app.services.crm import capture as service

router = APIRouter(prefix="/crm/capture", tags=["crm"])


@router.get("")
def read_capture(ctx: CrmAdminDep) -> dict:
    """The mode, the mailboxes, the ignore list and the latest dry run's
    report: counts, estimated cost, findings and the recall check."""
    return service.overview(ctx)


@router.post("/dry-run", status_code=202)
def start_dry_run(ctx: CrmAdminDep) -> dict:
    """`{dry_run: {id, started_at}}`; 422 with no *Primary and Sent*
    mailbox or with email sync off; 409 while one is running."""
    return service.start_dry_run(ctx)


@router.post("/dry-runs/{run_id}/apply")
def apply_dry_run(run_id: str, ctx: CrmAdminDep) -> dict:
    """`{applied_at, items_created, mode: "live"}`: the run's high- and
    medium-confidence findings become Review Queue items with no Claude
    call. 404, 409 when applied already, 422 when not finished, stopped,
    not the latest or finished more than 7 days ago."""
    return service.apply(ctx, run_id)


@router.post("/ignores", status_code=201)
def add_ignore(payload: CaptureIgnoreIn, ctx: CrmAdminDep) -> dict:
    """`{ignore}`; 422 for a malformed value, a public webmail domain or one
    of the workspace's own; 409 when listed already."""
    return service.add_ignore(ctx, payload)


@router.delete("/ignores/{ignore_id}", status_code=204)
def remove_ignore(ignore_id: str, ctx: CrmAdminDep) -> Response:
    """404 "No such ignore entry."."""
    service.remove_ignore(ctx, ignore_id)
    return Response(status_code=204)
