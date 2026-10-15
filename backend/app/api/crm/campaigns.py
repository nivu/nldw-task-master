"""Campaign members — spec 007 contracts §2.10; FR-CRM-CAMP-02 to -05,
FR-CRM-PER-02.

HTTP only. A campaign itself is a record route (`/crm/campaigns`, in
`records.py`); these are its members: the board's list (any member of the
workspace), and adding, moving and converting them (Admins and Members).
The same routes serve the campaign page and the `crm_campaign_members` MCP
tool (FR-CRM-MCP-07).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.api.crm.deps import CrmReadDep, CrmWriteDep
from app.schemas.crm import CampaignMembersIn, CampaignMemberUpdate, ConvertIn
from app.services.crm import campaigns as service

router = APIRouter(prefix="/crm", tags=["crm"])

MemberStatus = Literal[
    "targeted",
    "invited",
    "registered",
    "attended",
    "engaged",
    "meeting_booked",
    "converted",
    "not_interested",
    "unsubscribed",
]


@router.get("/campaigns/{campaign_id}/members")
def list_members(campaign_id: str, ctx: CrmReadDep, status: MemberStatus | None = None) -> dict:
    """`{campaign_id, member_counts, rows}` — the board, in board order."""
    return service.members(ctx, campaign_id, status=status)


@router.post("/campaigns/{campaign_id}/members")
def add_members(campaign_id: str, payload: CampaignMembersIn, ctx: CrmWriteDep) -> dict:
    """`{added, skipped_do_not_contact, skipped_existing, skipped}`."""
    return service.add_members(ctx, campaign_id, payload)


@router.patch("/campaign-members/{member_id}")
def update_member(member_id: str, payload: CampaignMemberUpdate, ctx: CrmWriteDep) -> dict:
    """The member. `status=converted` is refused here: convert it."""
    return service.update_member(ctx, member_id, payload)


@router.post("/campaign-members/{member_id}/convert")
def convert_member(member_id: str, payload: ConvertIn, ctx: CrmWriteDep) -> dict:
    """`{member, opportunity}`."""
    return service.convert(ctx, member_id, payload)
