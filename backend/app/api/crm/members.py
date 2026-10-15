"""Members and invitations — spec 007 contracts §2.2; FR-CRM-ACC-04 to -09,
-12, -15; owner decision D11.

A workspace admin invites into, and changes memberships of, the workspace
the CRM guard resolved: their own, by construction (FR-CRM-ACC-05). There is
no pending invitation: inviting adds the membership, and revoking it is
removing the membership (FR-CRM-ACC-04, -12).

The last-admin rule is enforced by the database under row locks, so two
admins demoting each other at once cannot both succeed. The protected
memberships (the owner's, a TVA admin's) are checked here first for a clear
403 (FR-CRM-ACC-09, plan §5.13). A removal is checked again by
`crm_remove_membership` under the profile lock; a role change is not:
`crm_apply` holds only the last-admin rule, so a profile made owner or TVA
admin between this check and the write can still have its role changed by a
workspace admin. Plan §5.13 records that narrow window.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.crm.deps import CrmAdminDep, CrmContext, CrmReadDep
from app.api.errors import ProblemDetail
from app.domain.crm import invites as rules
from app.schemas.crm import InvitationIn, MembershipUpdate
from app.services import supabase as db
from app.services.crm import members, store
from app.services.crm.context import actor

router = APIRouter(prefix="/crm", tags=["crm"])

NO_SUCH_MEMBER = "No such member in this workspace."


def is_tva_admin(ctx_user) -> bool:  # noqa: ANN001 - CurrentUser
    """Plan §5.13 — a TVA admin is an active staff profile with role admin."""
    return ctx_user.is_admin and not ctx_user.is_crm_only


@router.get("/members")
def list_members(ctx: CrmReadDep) -> list[dict]:
    return members.present_members(ctx.workspace_id, with_email=ctx.may("administer"))


@router.post("/invitations", status_code=201)
def invite(payload: InvitationIn, ctx: CrmAdminDep) -> dict:
    return members.invite(
        actor(ctx),
        ctx.workspace,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
        timezone=payload.timezone,
        by_tva_admin=is_tva_admin(ctx.user),
    )


@router.patch("/members/{membership_id}")
def change_role(membership_id: str, payload: MembershipUpdate, ctx: CrmAdminDep) -> dict:
    membership = _protected_check(ctx, membership_id)
    if membership["role"] == payload.role:
        return _present(membership)
    done = store.apply(
        actor(ctx),
        [
            {
                "op": "update",
                "table": "crm_memberships",
                "id": membership_id,
                "values": {"role": payload.role},
                "action": "membership.role_changed",
            }
        ],
    )
    return _present({**membership, **(done[0].get("row") or {}), "role": payload.role})


@router.delete("/members/{membership_id}")
def remove(membership_id: str, ctx: CrmAdminDep) -> dict:
    _protected_check(ctx, membership_id)
    return members.remove(actor(ctx), membership_id)


def _protected_check(ctx: CrmContext, membership_id: str) -> dict:
    membership = store.get_membership(ctx.workspace_id, membership_id)
    if membership is None:
        raise ProblemDetail(404, NO_SUCH_MEMBER)
    why = rules.membership_change_refusal(
        db.get_profile(membership["user_id"]), by_tva_admin=is_tva_admin(ctx.user)
    )
    if why:
        raise ProblemDetail(403, why)
    return membership


def _present(membership: dict) -> dict:
    return {
        "membership_id": membership["id"],
        "user_id": membership["user_id"],
        "role": membership["role"],
    }
