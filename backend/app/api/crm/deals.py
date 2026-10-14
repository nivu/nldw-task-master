"""Deals — spec 007 contracts §2.5 (contacts), §2.6 (stage moves, the
handoff, Link TVA project); FR-CRM-PIPE-02 to -15, FR-CRM-OPP-02,
FR-CRM-TVA-03 to -18.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/deals.py` does the rest. The same routes serve the web app's
stage, handoff and link dialogs and the `crm_move_stage` and `crm_handoff`
MCP tools (FR-CRM-MCP-07).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.api.crm.deps import CrmAdminDep, CrmWriteDep
from app.schemas.crm import ContactIn, HandoffIn, LinkProjectIn, StageMoveIn, StagePreviewIn
from app.services.crm import deals as service

router = APIRouter(prefix="/crm/opportunities", tags=["crm"])
projects_router = APIRouter(prefix="/crm/projects", tags=["crm"])


@router.post("/{deal_id}/stage/preview")
def preview_stage(deal_id: str, payload: StagePreviewIn, ctx: CrmWriteDep) -> dict:
    """What the move would do, writing nothing: `{allowed, missing, message,
    would}`; in Labs `would.project` is its TVA project effect, with a name
    clash and whether the caller may link to it."""
    return service.preview(ctx, deal_id, payload)


@router.post("/{deal_id}/stage")
def move_stage(deal_id: str, payload: StageMoveIn, ctx: CrmWriteDep) -> dict:
    """The move and every side effect, CRM and TVA, in one transaction
    (FR-CRM-PIPE-15). 422 names every missing field in one sentence; 409 a
    stale deal or a project name clash. Out of Closed Lost: a workspace admin
    or the deal's owner; out of Closed Won: a workspace admin (the service
    checks). Closed Won in Labs goes through the handoff."""
    return service.move(ctx, deal_id, payload)


@router.get("/{deal_id}/handoff")
def handoff_preview(deal_id: str, ctx: CrmWriteDep) -> dict:
    """FR-CRM-TVA-07 — the handoff pre-filled. Existing TVA revenue only for a
    staff TVA manager or admin."""
    return service.handoff_preview(ctx, deal_id)


@router.post("/{deal_id}/handoff")
def handoff_confirm(deal_id: str, payload: HandoffIn, ctx: CrmWriteDep) -> dict:
    """FR-CRM-TVA-07 to -10 — Closed Won and the project confirmed, in one
    transaction. Revenue from anyone but a staff TVA manager or admin is
    ignored: `revenue_written: false` and the Labs admins get a task."""
    return service.handoff_confirm(ctx, deal_id, payload)


@router.post("/{deal_id}/link-project")
def link_project(deal_id: str, payload: LinkProjectIn, ctx: CrmAdminDep) -> dict:
    """FR-CRM-TVA-18 — Labs admins; `link` mode also a staff TVA manager or
    admin (else 409 "Ask a TVA manager to link it.")."""
    return service.link_project(ctx, deal_id, payload)


@router.post("/{deal_id}/contacts", status_code=201)
def add_contact(deal_id: str, payload: ContactIn, ctx: CrmWriteDep) -> dict:
    return service.add_contact(ctx, deal_id, payload)


@router.delete("/{deal_id}/contacts/{person_id}")
def remove_contact(deal_id: str, person_id: str, ctx: CrmWriteDep) -> dict:
    return service.remove_contact(ctx, deal_id, person_id)


@projects_router.get("/linkable")
def linkable_projects(
    ctx: CrmAdminDep, status: Literal["tentative", "confirmed"] | None = None
) -> list[dict]:
    """FR-CRM-TVA-05, -18 — unlinked, unarchived TVA projects: Labs admins who
    are staff TVA managers or admins only (else 403)."""
    return service.linkable(ctx, status)
