"""Company merge — spec 007 contracts §2.8; FR-CRM-DUP-03, -04.

HTTP only, for Admins and Members. Mounted before the record routes, so
`/crm/companies/merge-preview` is never read as a company id. The same
routes serve the merge dialog and the `crm_merge` MCP tool.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmWriteDep
from app.schemas.crm import MergeIn
from app.services.crm import merge as service

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/companies/merge-preview")
def merge_preview(
    ctx: CrmWriteDep,
    a: str = Query(max_length=64),
    b: str = Query(max_length=64),
) -> dict:
    """`{a, b, differing_fields: [{field, label, a, b, a_display, b_display,
    default}], moves: {people, deals, tasks, notes, links, projects}}`; `a`
    survives."""
    return service.preview(ctx, a, b)


@router.post("/companies/merge")
def merge(payload: MergeIn, ctx: CrmWriteDep) -> dict:
    """The survivor. The merged company goes to the Trash and its id opens
    the survivor from now on."""
    return service.merge(ctx, payload)
