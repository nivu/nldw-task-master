"""Settings, Email capture — spec 010 contracts §2.1, §2.2; FR-CAP-DIS-01,
-10, FR-CAP-RPT-01 to -03.

HTTP only, workspace Admins only: a Member or Viewer is refused 403 and a
non-member 404, as every Admin route. The routes read the report and open
a dry run; the work itself is done by `crm_ai.capture` on `worker-crm-ai`,
since the API holds neither the Gmail nor the Claude key. The mode is
changed through `PATCH /crm/settings/capture`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.crm.deps import CrmAdminDep
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
