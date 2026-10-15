"""Export — spec 007 contracts §2.10 `POST /crm/export/view`, `GET
/crm/export/workspace`; FR-CRM-IO-04, -05, FR-CRM-VIEW-07.

HTTP only: the file goes back with its type and name
(`Content-Disposition: attachment`), which the portal's proxy keeps
(FR-CRM-CHG-08). Admins and Members export a view; the whole workspace is
the Admins'. `services/crm/export.py` decides, caps at 5 MB and audits.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.api.crm.deps import CrmAdminDep, CrmReadDep
from app.schemas.crm import ExportViewIn
from app.services.crm import export as service

router = APIRouter(prefix="/crm/export", tags=["crm"])


def _attachment(name: str, data: bytes, media: str) -> Response:
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/view")
def export_view(payload: ExportViewIn, ctx: CrmReadDep) -> Response:
    """The view as CSV with its filters and columns; Viewers 403. It is
    audited, so a member of two workspaces names the one (D13)."""
    name, data = service.view_csv(ctx, payload)
    return _attachment(name, data, "text/csv; charset=utf-8")


@router.get("/workspace")
def export_workspace(
    ctx: CrmAdminDep, object: Annotated[str | None, Query(max_length=40)] = None
) -> Response:
    """Every object of the workspace as JSON, or one (`object`)."""
    name, data = service.workspace_json(ctx, object)
    return _attachment(name, data, "application/json")
