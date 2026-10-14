"""Deals — spec 007 contracts §2.5 (contacts), §2.6 (stage moves);
FR-CRM-PIPE-02 to -15 (CRM side), FR-CRM-OPP-02.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/deals.py` does the rest. The same routes serve the web app's
stage dialog and the `crm_move_stage` MCP tool (FR-CRM-MCP-07). The handoff
and project links arrive with the TVA side (S5).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.crm.deps import CrmWriteDep
from app.schemas.crm import ContactIn, StageMoveIn, StagePreviewIn
from app.services.crm import deals as service

router = APIRouter(prefix="/crm/opportunities", tags=["crm"])


@router.post("/{deal_id}/stage/preview")
def preview_stage(deal_id: str, payload: StagePreviewIn, ctx: CrmWriteDep) -> dict:
    """What the move would do, writing nothing: `{allowed, missing, message,
    would}`."""
    return service.preview(ctx, deal_id, payload)


@router.post("/{deal_id}/stage")
def move_stage(deal_id: str, payload: StageMoveIn, ctx: CrmWriteDep) -> dict:
    """The move and every side effect in one transaction (FR-CRM-PIPE-15).
    422 names every missing field in one sentence; 409 a stale deal. Out of
    Closed Lost: a workspace admin or the deal's owner; out of Closed Won: a
    workspace admin (the service checks)."""
    return service.move(ctx, deal_id, payload)


@router.post("/{deal_id}/contacts", status_code=201)
def add_contact(deal_id: str, payload: ContactIn, ctx: CrmWriteDep) -> dict:
    return service.add_contact(ctx, deal_id, payload)


@router.delete("/{deal_id}/contacts/{person_id}")
def remove_contact(deal_id: str, person_id: str, ctx: CrmWriteDep) -> dict:
    return service.remove_contact(ctx, deal_id, person_id)
