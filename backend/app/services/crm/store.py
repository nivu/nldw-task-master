"""CRM data access — spec 007 plan §5.2, §5.3, §5.4; data-model §5.

Thin named functions over the same service-role client as
`services/supabase.py`, so every query here bypasses RLS and must be scoped
by the caller's workspace. Every read of workspace data takes `workspace_id`
as its first argument and filters on it (FR-CRM-WS-02); the few functions
that read across workspaces are account-level and are listed in
`ACCOUNT_LEVEL` (a test checks both rules by introspection).

Writes never go through `.table(...).insert/update`: they go through
`apply()` (or another CRM database function), which sets the actor in the
same transaction so the audit trigger records the change (FR-CRM-AUD-06).
The one exception is `touch_last_used`, a column the trigger ignores.

Postgres messages never reach a client unmapped: `rpc()` turns the CRM's
SQLSTATEs into `CrmRefused` with their one-sentence message, a unique
violation into a sentence written here, and lets anything else surface as the
generic 500 after logging it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from postgrest.exceptions import APIError

from app.services.crm import CrmRefused
from app.services.supabase import supabase

logger = logging.getLogger("nldw-task-master")

#: Functions that do not take a workspace first: they read the caller's own
#: memberships (to find their workspaces), or write through a database
#: function that scopes by the actor's workspace itself.
ACCOUNT_LEVEL = frozenset(
    {
        "memberships_for_user",
        "touch_last_used",
        "apply",
        "rpc",
        # The membership functions scope by the actor's workspace themselves.
        "invite",
        "remove_membership",
        "convert_account",
        # The TVA admin panel (CT-3): account administration across
        # workspaces, never a CRM record table.
        "list_workspaces",
        "find_membership",
        "list_account_audit",
    }
)

#: SQLSTATE raised by the CRM's database functions → HTTP status.
STATUS_BY_CODE = {"CRM04": 404, "CRM09": 409, "CRM22": 422}

#: Unique constraints a person can trip, by index or constraint name.
UNIQUE_MESSAGES = {
    "crm_memberships_workspace_user": "This person is already a member of this workspace.",
    "crm_select_options_label": "This list already has an option with that name.",
    "crm_select_options_one_default": "This list's default changed meanwhile; reload and try again.",
    "crm_fx_rates_workspace_currency_month": (
        "A rate for that month was set meanwhile; reload and try again."
    ),
}

_CONSTRAINT = re.compile(r'unique constraint "([^"]+)"')


def refusal_for(code: str | None, message: str | None) -> CrmRefused | None:
    """The refusal a database error stands for, or None if it is a fault."""
    if code in STATUS_BY_CODE:
        return CrmRefused(STATUS_BY_CODE[code], message or "That change was refused.")
    if code == "23505":
        found = _CONSTRAINT.search(message or "")
        name = found.group(1) if found else ""
        return CrmRefused(409, UNIQUE_MESSAGES.get(name, "That already exists."))
    return None


def rpc(fn: str, params: dict[str, Any]) -> Any:
    """Call a CRM database function, mapping its refusals (plan §5.4)."""
    try:
        return supabase.rpc(fn, params).execute().data
    except APIError as exc:
        refused = refusal_for(exc.code, exc.message)
        if refused is not None:
            raise refused from None
        logger.error('{"event": "crm_rpc_failed", "fn": "%s", "code": "%s"}', fn, exc.code)
        raise


def apply(actor: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Data-model §5.2 — every ordinary CRM write, in one transaction.

    Returns one `{ref, id, row}` per op, in order.
    """
    return rpc("crm_apply", {"p_actor": actor, "p_ops": ops}) or []


# ---------------------------------------------------------------------------
# Workspaces and memberships — FR-CRM-WS
# ---------------------------------------------------------------------------

_WORKSPACE_COLUMNS = "id, key, name, tva_linked, has_entity, allowed_domains"


def memberships_for_user(user_id: str) -> list[dict[str, Any]]:
    """The person's memberships, each with its workspace under `workspace`."""
    rows = (
        supabase.table("crm_memberships")
        .select(f"id, role, last_used_at, workspace:crm_workspaces({_WORKSPACE_COLUMNS})")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows or []


def touch_last_used(membership_id: str, at: datetime) -> None:
    """FR-CRM-WS-04. The audit trigger skips a change to this column alone,
    so it is written directly, with no actor."""
    supabase.table("crm_memberships").update({"last_used_at": at.isoformat()}).eq(
        "id", membership_id
    ).execute()


# ---------------------------------------------------------------------------
# Members, invitations, account type — FR-CRM-ACC
# ---------------------------------------------------------------------------

_MEMBERSHIP_COLUMNS = "id, workspace_id, user_id, role, created_at"


def list_members(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_memberships")
        .select(_MEMBERSHIP_COLUMNS)
        .eq("workspace_id", workspace_id)
        .execute()
        .data
        or []
    )


def get_membership(workspace_id: str, membership_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("crm_memberships")
        .select(_MEMBERSHIP_COLUMNS)
        .eq("workspace_id", workspace_id)
        .eq("id", membership_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def find_membership(membership_id: str) -> dict[str, Any] | None:
    """For the TVA admin panel, which names a membership without a workspace."""
    rows = (
        supabase.table("crm_memberships")
        .select(_MEMBERSHIP_COLUMNS)
        .eq("id", membership_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def invite(actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Data-model §5.13 — profile (created or reactivated), membership and
    their audit rows in one transaction, after the GoTrue step (CT-5)."""
    return rpc("crm_invite", {"p_actor": actor, "p_plan": plan})


def remove_membership(actor: dict[str, Any], membership_id: str) -> dict[str, Any]:
    """Data-model §5.3 — `{deactivated}`; tokens are revoked with it (Q-44)."""
    return rpc("crm_remove_membership", {"p_actor": actor, "p_membership_id": membership_id})


def convert_account(actor: dict[str, Any], user_id: str, to: str) -> dict[str, Any]:
    """Data-model §5.15 — under the profile lock tva_staff_only() waits for."""
    return rpc("crm_convert_account", {"p_actor": actor, "p_user_id": user_id, "p_to": to})


def list_workspaces() -> list[dict[str, Any]]:
    return (
        supabase.table("crm_workspaces").select(_WORKSPACE_COLUMNS).order("name").execute().data
        or []
    )


#: Contracts §2.4 — account administration, never record content.
ACCOUNT_ACTIONS = (
    "user.created",
    "user.account_type",
    "user.deactivated",
    "user.reactivated",
    "workspace.allowed_domains",
)


def list_account_audit(
    *, workspace_id: str | None = None, before: int | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Membership events of every workspace and the account events around
    them, newest first (plan CT-3).

    The `user.*` rows are TVA rows, written with no workspace. Filtered to a
    workspace, they are included for the people who are or were its members,
    so removing someone's last membership shows the deactivation it caused.
    A person in two workspaces has their account events in both.
    """
    query = supabase.table("audit_log").select(
        "id, at, actor_id, actor_label, workspace_id, action, target_table, target_id, "
        "before, after"
    )
    events = f"action.like.membership.*,action.in.({','.join(ACCOUNT_ACTIONS)})"
    if workspace_id:
        own = f"and(workspace_id.eq.{workspace_id},or({events}))"
        people = _workspace_people(workspace_id)
        if people:
            user_actions = ",".join(a for a in ACCOUNT_ACTIONS if a.startswith("user."))
            query = query.or_(
                f"{own},and(workspace_id.is.null,action.in.({user_actions}),"
                f"target_id.in.({','.join(sorted(people))}))"
            )
        else:
            query = query.or_(own)
    else:
        query = query.or_(events)
    if before is not None:
        query = query.lt("id", before)
    return query.order("id", desc=True).limit(limit).execute().data or []


def _workspace_people(workspace_id: str) -> set[str]:
    """Everyone who is, or was, a member: the members now, and the people
    the workspace's membership events name (a removal keeps the user in
    `before`)."""
    rows = (
        supabase.table("audit_log")
        .select("after_user:after->>user_id, before_user:before->>user_id")
        .eq("workspace_id", workspace_id)
        .eq("target_table", "crm_memberships")
        .execute()
        .data
        or []
    )
    people = {r[k] for r in rows for k in ("after_user", "before_user") if r.get(k)}
    return people | {m["user_id"] for m in list_members(workspace_id)}


# ---------------------------------------------------------------------------
# Settings, select lists, FX rates — FR-CRM-LIST, FR-CRM-FX
# ---------------------------------------------------------------------------


def list_settings(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_workspace_settings")
        .select("id, key, value, updated_at")
        .eq("workspace_id", workspace_id)
        .execute()
        .data
        or []
    )


def list_options(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_select_options")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("list_key")
        .order("position")
        .execute()
        .data
        or []
    )


def get_option(workspace_id: str, option_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("crm_select_options")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("id", option_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_fx_rates(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_fx_rates")
        .select("id, currency, month, rate, updated_at")
        .eq("workspace_id", workspace_id)
        .order("month", desc=True)
        .order("currency")
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# Audit — FR-CRM-AUD-03
# ---------------------------------------------------------------------------


def list_audit(
    workspace_id: str,
    *,
    target_table: str | None = None,
    target_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The workspace's own audit rows, newest first. Never another
    workspace's, and never a TVA row (workspace_id NULL)."""
    query = supabase.table("audit_log").select("*").eq("workspace_id", workspace_id)
    if target_table:
        query = query.eq("target_table", target_table)
    if target_id:
        query = query.eq("target_id", target_id)
    if actor_id:
        query = query.eq("actor_id", actor_id)
    if action:
        query = query.eq("action", action)
    if since:
        query = query.gte("at", since.isoformat())
    if until:
        query = query.lt("at", until.isoformat())
    if before is not None:
        query = query.lt("id", before)
    return query.order("id", desc=True).limit(limit).execute().data or []
