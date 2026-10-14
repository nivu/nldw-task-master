"""The TVA Admin "CRM workspaces" panel — spec 007 contracts §2.4;
FR-CRM-ACC-01, -05, -06, -13, FR-CRM-CHG-11; plan §4 CT-3.

TVA admins administer every workspace without joining one: its name, its
members, invitations, removals, the domain allow-list and account types. That
is account administration, not CRM records (Q-69), so no route here reads a
CRM record table, and the audit it shows carries no record content.

Every route is on TVA's `AdminDep` (an active staff admin; a CRM-only
account is refused before it), never on the CRM guard: a membership grants
nothing here, and none is needed. The job-run metadata of contracts §2.4
arrives with the job log in 1b.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import AdminDep, CurrentUser
from app.api.errors import ProblemDetail
from app.domain.audit import actor_name
from app.domain.crm import invites as rules
from app.schemas.crm import AccountTypeUpdate, AllowedDomainsIn, TvaInvitationIn
from app.services import supabase as db
from app.services.crm import members, store

router = APIRouter(prefix="/crm/tva-admin", tags=["crm"])

NO_SUCH_WORKSPACE = "No such workspace."


def _actor(admin: CurrentUser, workspace_id: str | None, channel: str = "web") -> dict[str, Any]:
    """The `p_actor` of the CRM functions, for a TVA admin acting from TVA.
    `crm_convert_account` writes TVA rows only and takes no workspace."""
    return {"user_id": admin.id, "kind": "user", "channel": channel, "workspace_id": workspace_id}


def _workspace(workspace_id: str) -> dict[str, Any]:
    found = next((w for w in store.list_workspaces() if workspace_id in (w["id"], w["key"])), None)
    if found is None:
        raise ProblemDetail(404, NO_SUCH_WORKSPACE)
    return found


@router.get("/workspaces")
def list_workspaces(admin: AdminDep) -> list[dict]:
    """The allow-list is included: this panel is where it is edited."""
    return [
        {
            "id": w["id"],
            "key": w["key"],
            "name": w["name"],
            "member_count": len(store.list_members(w["id"])),
            "allowed_domains": w.get("allowed_domains") or [],
        }
        for w in store.list_workspaces()
    ]


@router.get("/workspaces/{workspace_id}/members")
def list_members(workspace_id: str, admin: AdminDep) -> list[dict]:
    return members.present_members(_workspace(workspace_id)["id"], with_email=True)


@router.put("/workspaces/{workspace_id}/allowed-domains")
def set_allowed_domains(workspace_id: str, payload: AllowedDomainsIn, admin: AdminDep) -> dict:
    """FR-CRM-ACC-06, amended — lower-cased, de-duplicated, no public webmail.
    `crm_apply` re-checks that the actor is a TVA admin."""
    ws = _workspace(workspace_id)
    domains, why = rules.allowed_domains(payload.domains)
    if why:
        raise ProblemDetail(422, why)
    if domains != list(ws.get("allowed_domains") or []):
        store.apply(
            _actor(admin, ws["id"]),
            [
                {
                    "op": "update",
                    "table": "crm_workspaces",
                    "id": ws["id"],
                    "values": {"allowed_domains": domains},
                    "action": "workspace.allowed_domains",
                }
            ],
        )
    return {"id": ws["id"], "key": ws["key"], "allowed_domains": domains}


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def invite(workspace_id: str, payload: TvaInvitationIn, admin: AdminDep) -> dict:
    """FR-CRM-ACC-05 — into any workspace; the allow-list may be bypassed for
    an existing staff account."""
    ws = _workspace(workspace_id)
    return members.invite(
        _actor(admin, ws["id"]),
        ws,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
        timezone=payload.timezone,
        by_tva_admin=True,
        bypass_allow_list=payload.bypass_allow_list,
    )


@router.delete("/memberships/{membership_id}")
def remove_membership(membership_id: str, admin: AdminDep) -> dict:
    membership = store.find_membership(membership_id)
    if membership is None:
        raise ProblemDetail(404, "No such membership.")
    return members.remove(_actor(admin, membership["workspace_id"]), membership_id)


@router.patch("/accounts/{user_id}")
def change_account_type(user_id: str, payload: AccountTypeUpdate, admin: AdminDep) -> dict:
    """Staff ↔ CRM-only, in one transaction under the profile lock. The
    refusals (the owner, someone who leads, leads a project or holds an
    unreleased allocation) come from `crm_convert_account`, re-checked inside
    that lock."""
    if db.get_profile(user_id) is None:
        raise ProblemDetail(404, "No such user.")
    if user_id == admin.id and payload.account_type == "crm_only":
        raise ProblemDetail(422, "You cannot make your own account CRM-only.")
    done = store.convert_account(_actor(admin, None), user_id, payload.account_type)
    profile = db.get_profile(user_id) or {}
    return {
        "id": user_id,
        "display_name": profile.get("display_name"),
        "account_type": profile.get("account_type", done.get("account_type")),
        "role": profile.get("role"),
        "logs_time": profile.get("logs_time"),
        "lead_id": profile.get("lead_id"),
        "dashboard_access": profile.get("dashboard_access"),
        "changed": bool(done.get("changed")),
    }


@router.get("/audit")
def read_audit(
    admin: AdminDep,
    workspace_id: str | None = None,
    before: int | None = Query(default=None, description="An entry id; older entries follow."),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """Contracts §2.4 — who changed whose access, never what a record says.
    `before` and `after` are read only to name the person and the role."""
    workspaces = {w["id"]: w for w in store.list_workspaces()}
    scope = _workspace(workspace_id)["id"] if workspace_id else None
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    rows = []
    for row in store.list_account_audit(workspace_id=scope, before=before, limit=limit):
        after = row.get("after") or {}
        before_ = row.get("before") or {}
        # An account created by a TVA admin for staff is TVA's own business;
        # only the CRM-only accounts invitations create are shown here.
        if row["action"] == "user.created" and after.get("account_type") != "crm_only":
            continue
        rows.append(
            {
                "id": row["id"],
                "at": row["at"],
                "workspace_key": workspaces.get(row.get("workspace_id"), {}).get("key"),
                "actor_name": actor_name(row.get("actor_id"), names, row.get("actor_label")),
                "action": row["action"],
                "target_name": _target_name(row, names, workspaces),
                "role": after.get("role") or before_.get("role"),
            }
        )
    return rows


def _target_name(row: dict, names: dict[str, str], workspaces: dict[str, dict]) -> str | None:
    if row["target_table"] == "crm_workspaces":
        return workspaces.get(row.get("target_id"), {}).get("name")
    if row["target_table"] == "crm_memberships":
        user_id = (row.get("after") or {}).get("user_id") or (row.get("before") or {}).get(
            "user_id"
        )
        if user_id is None:
            found = store.find_membership(row.get("target_id") or "")
            user_id = found["user_id"] if found else None
        return names.get(user_id) if user_id else None
    return names.get(row.get("target_id"))
