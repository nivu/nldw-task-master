"""The TVA Admin "CRM workspaces" panel — spec 007 contracts §2.4;
FR-CRM-ACC-01, -05, -06, -13, FR-CRM-CHG-11; plan §4 CT-3.

TVA admins administer every workspace without joining one: its name, its
members, invitations, removals, the domain allow-list and account types. That
is account administration, not CRM records (Q-69), so no route here reads a
CRM record table, and the audit it shows carries no record content.

Every route is on TVA's `AdminDep` (an active staff admin; a CRM-only
account is refused before it), never on the CRM guard: a membership grants
nothing here, and none is needed. The job-run metadata of contracts §2.4
(1b) is shown with `error_public` only: a run's `error` may name records,
which a TVA admin sees only as a member of that workspace, in its own Jobs
tab (FR-CRM-JOB-01, Q-69).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import AdminDep, CurrentUser
from app.api.errors import ProblemDetail
from app.domain.audit import actor_name
from app.domain.crm import invites as rules
from app.schemas.crm import (
    AccountTypeUpdate,
    AllowedDomainsIn,
    MembershipUpdate,
    TvaInvitationIn,
)
from app.services import supabase as db
from app.services.crm import jobs, members, store

router = APIRouter(prefix="/crm/tva-admin", tags=["crm"])

NO_SUCH_WORKSPACE = "No such workspace."
#: The sentences `crm_convert_account` (045) also raises.
PENDING_LEAVE = "This person has leave waiting for a decision; decide or withdraw it first."
PENDING_COMPOFF = "This person has a comp-off claim waiting for a decision; decide it first."


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


def _membership(membership_id: str) -> dict[str, Any]:
    membership = store.find_membership(membership_id)
    if membership is None:
        raise ProblemDetail(404, "No such membership.")
    return membership


@router.patch("/memberships/{membership_id}")
def change_role(membership_id: str, payload: MembershipUpdate, admin: AdminDep) -> dict:
    """FR-CRM-ACC-09 — the owner's and a TVA admin's membership included,
    which only a TVA admin may change. `crm_apply` still holds the last-admin
    rule under its row locks."""
    membership = _membership(membership_id)
    if membership["role"] != payload.role:
        store.apply(
            _actor(admin, membership["workspace_id"]),
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
    return {
        "membership_id": membership["id"],
        "user_id": membership["user_id"],
        "role": payload.role,
    }


@router.delete("/memberships/{membership_id}")
def remove_membership(membership_id: str, admin: AdminDep) -> dict:
    membership = _membership(membership_id)
    return members.remove(_actor(admin, membership["workspace_id"]), membership_id)


@router.patch("/accounts/{user_id}")
def change_account_type(user_id: str, payload: AccountTypeUpdate, admin: AdminDep) -> dict:
    """Staff ↔ CRM-only, in one transaction under the profile lock. The
    refusals (the owner, someone who leads, leads a project, holds an
    unreleased allocation, or has leave or a comp-off claim waiting for a
    decision) come from `crm_convert_account`, re-checked inside that lock;
    the waiting ones are also answered here first."""
    person = db.get_profile(user_id)
    if person is None:
        raise ProblemDetail(404, "No such user.")
    if user_id == admin.id and payload.account_type == "crm_only":
        raise ProblemDetail(422, "You cannot make your own account CRM-only.")
    if payload.account_type == "crm_only" and person.get("account_type") != "crm_only":
        # A CRM-only account drops out of every approval queue, so what is
        # waiting would never be decided.
        waiting = db.has_pending_decisions(user_id)
        if waiting["leave"]:
            raise ProblemDetail(422, PENDING_LEAVE)
        if waiting["compoff"]:
            raise ProblemDetail(422, PENDING_COMPOFF)
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


@router.get("/jobs")
def list_jobs(
    admin: AdminDep,
    job: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Contracts §2.4 — every workspace's runs and the digest tick's (no
    workspace), newest first: metadata and `error_public`, never `error`;
    and the jobs that are late anywhere (FR-CRM-JOB-03)."""
    keys = {w["id"]: w["key"] for w in store.list_workspaces()}
    return {
        "runs": [
            jobs.present_run(r, with_error=False, keys=keys)
            for r in store.list_all_job_runs(job=job, limit=limit)
        ],
        "stale_jobs": [
            {
                **{k: v for k, v in s.items() if k != "workspace_id"},
                "workspace": keys.get(s.get("workspace_id") or ""),
            }
            for s in jobs.stale()
        ],
    }


@router.get("/audit")
def read_audit(
    admin: AdminDep,
    workspace_id: str | None = None,
    before: int | None = Query(default=None, description="An entry id; older entries follow."),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """Contracts §2.4 — who changed whose access, never what a record says.
    `before` and `after` are read only to name the person and the role; the
    role is a membership's CRM role, so only a membership row carries one (a
    profile row's `role` is the TVA role)."""
    workspaces = {w["id"]: w for w in store.list_workspaces()}
    scope = _workspace(workspace_id)["id"] if workspace_id else None
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    rows = []
    for row in store.list_account_audit(workspace_id=scope, before=before, limit=limit):
        after = row.get("after") or {}
        before_ = row.get("before") or {}
        is_membership = row["target_table"] == "crm_memberships"
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
                "role": (after.get("role") or before_.get("role")) if is_membership else None,
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
