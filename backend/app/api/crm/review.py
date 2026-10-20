"""The Review Queue — spec 009 contracts §2.2; FR-CRMAI-REV-01 to -08,
FR-CRMAI-FLAG-04.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/review.py` does the rest. Any member reads the queue (a Viewer
gets an empty one); Admins and Members decide, an Admin any item of the
workspace, a Member their own, and another Member's item is not found. The
same routes serve the web app's Review page and the `crm_review_list` and
`crm_review_decide` MCP tools.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmReadDep, CrmWriteDep
from app.schemas.crm import ReviewApproveIn, ReviewDismissIn
from app.services.crm import review as service

router = APIRouter(prefix="/crm/review", tags=["crm"])


@router.get("")
def list_review(
    ctx: CrmReadDep,
    scope: Literal["mine", "all"] = "mine",
    status: Literal["open", "decided"] = "open",
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    """`{items, total}`, oldest first for `open`. `scope=all` and
    `status=decided` (the last 90 days) are for workspace Admins."""
    return service.list_items(ctx, scope=scope, status=status, limit=limit)


@router.post("/{item_id}/approve")
def approve(item_id: str, payload: ReviewApproveIn, ctx: CrmWriteDep) -> dict:
    """Approve, or Edit and approve with `edits`: the change is made through
    the same service a manual edit uses, as the caller. 409 when a recorded
    value changed or the item was already decided; 422 the manual path's own
    sentence, a record in the Trash, or a deal still to choose."""
    return service.approve(ctx, item_id, payload)


@router.post("/{item_id}/dismiss")
def dismiss(item_id: str, ctx: CrmWriteDep, payload: ReviewDismissIn | None = None) -> dict:
    """`{item}`; 409 when it was already decided. Spec 010: a new lead's
    dismissal may add its sender or domain to the ignore list
    (`{"ignore": "address" | "domain"}`), then `{item, ignore}`."""
    return service.dismiss(ctx, item_id, payload)
