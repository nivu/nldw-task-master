"""The CRM guard — spec 007 FR-CRM-ACC-10, FR-CRM-WS-02, -04, FR-CRM-MCP-03,
-04; owner decisions D3, D13; plan §5.1.

Every `/crm` route depends on one of the guards below, and every `crm_*` MCP
tool calls a `/crm` route, so the web app, the API and MCP pass through the
same check (D3). It runs on `any_account`, not `current_user`: a CRM-only
seller is the CRM's main user. A TVA role grants nothing here (FR-CRM-ACC-02);
only a membership does.

The workspace comes from the `workspace` query parameter, a key such as
`labs` or an id. Without it (D13):
- a read uses the caller's last-used workspace;
- any other request from a member of more than one workspace is refused 422,
  naming them, so a write never lands in a workspace the person did not
  mean (FR-CRM-MCP-04). A member of one workspace writes there.

A workspace the caller is not a member of is "No such workspace." (404), the
same answer as one that does not exist, so its existence is not disclosed
(FR-CRM-WS-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query, Request

from app.api.deps import AnyAccountDep, CurrentUser
from app.api.errors import ProblemDetail
from app.domain.crm.permissions import may
from app.services.crm.context import workspaces_of

NO_SUCH_WORKSPACE = "No such workspace."
READ_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class CrmContext:
    """The caller, the workspace they are acting in and their role there."""

    user: CurrentUser
    workspace: dict[str, Any]
    role: str
    channel: str

    @property
    def workspace_id(self) -> str:
        return self.workspace["id"]

    def may(self, action: str, *, owns: bool = False, is_assignee: bool = False) -> bool:
        return may(self.role, action, owns=owns, is_assignee=is_assignee)


def crm_context(
    request: Request,
    user: AnyAccountDep,
    workspace: Annotated[
        str | None,
        Query(description="Workspace key (such as labs) or id. Reads default to the last used."),
    ] = None,
) -> CrmContext:
    mine = workspaces_of(user.id)
    if workspace:
        chosen = next((w for w in mine if workspace in (w["key"], w["id"])), None)
    elif request.method in READ_METHODS or len(mine) == 1:
        chosen = next((w for w in mine if w["last_used"]), None)
    else:
        if len(mine) > 1:
            named = ", ".join(f"{w['name']} ({w['key']})" for w in mine)
            raise ProblemDetail(422, f"Name a workspace: {named}.")
        chosen = None
    if chosen is None:
        raise ProblemDetail(404, NO_SUCH_WORKSPACE)

    ws = {k: v for k, v in chosen.items() if k not in ("role", "last_used", "membership_id")}
    return CrmContext(
        user=user,
        workspace=ws,
        role=chosen["role"],
        channel=getattr(request.state, "channel", "web"),
    )


CrmReadDep = Annotated[CrmContext, Depends(crm_context)]


def crm_write(ctx: CrmReadDep) -> CrmContext:
    """Spec §3.3 "Create and edit records" — admins and members."""
    if not ctx.may("edit_records"):
        raise ProblemDetail(403, "Viewers cannot change records.")
    return ctx


def crm_admin(ctx: CrmReadDep) -> CrmContext:
    """Spec §3.3 "Settings, select lists, FX rates, members, invitations"."""
    if not ctx.may("administer"):
        raise ProblemDetail(403, "Only a workspace admin can do that.")
    return ctx


def crm_labs(ctx: CrmReadDep) -> CrmContext:
    """FR-CRM-WS-05 — only the TVA-linked workspace has the TVA side."""
    if not ctx.workspace.get("tva_linked"):
        raise ProblemDetail(422, f"{ctx.workspace['name']} is not connected to TVA projects.")
    return ctx


def crm_capacity(ctx: CrmReadDep) -> CrmContext:
    """FR-CRM-CAP-02, Q-39 — the capacity check is built from staff
    allocations, leave and holidays, so only Labs admins and staff members see
    it: not Viewers, not CRM-only accounts. Outside Labs there is no capacity
    check at all, so it is not found there."""
    if not ctx.workspace.get("tva_linked"):
        raise ProblemDetail(404, "There is no capacity check in this workspace.")
    if ctx.user.is_crm_only or ctx.role not in ("admin", "member"):
        raise ProblemDetail(403, "Only Labs admins and staff members can see capacity.")
    return ctx


CrmWriteDep = Annotated[CrmContext, Depends(crm_write)]
CrmAdminDep = Annotated[CrmContext, Depends(crm_admin)]
CrmLabsDep = Annotated[CrmContext, Depends(crm_labs)]
CrmCapacityDep = Annotated[CrmContext, Depends(crm_capacity)]
