"""Who is acting, and in which workspaces — spec 007 plan §5.1, §5.3.

`actor()` is the only way a CRM write learns who made it: built from the
request's CRM context, never from the request body (plan §5.3).
"""

from __future__ import annotations

from typing import Any

from app.services.crm import store


def actor(ctx: Any, kind: str = "user") -> dict[str, Any]:
    """The `p_actor` every CRM database function takes (data-model §5.2).

    `channel` is the audit label `deps._authenticate` set: 'mcp' only for the
    in-process MCP bridge, else 'web' (Q-22). It decides nothing.

    Spec 009 FR-CRMAI-REV-05: an approved suggestion's change also carries
    `suggestion_id`, the item's id, which `crm_audit_row` writes on the audit
    row. It is there only when the Review Queue set it on the context; every
    other actor is unchanged.
    """
    out = {
        "user_id": ctx.user.id,
        "kind": kind,
        "channel": ctx.channel,
        "workspace_id": ctx.workspace["id"],
    }
    suggestion_id = getattr(ctx, "suggestion_id", None)
    if suggestion_id:
        out["suggestion_id"] = suggestion_id
    return out


def workspaces_of(user_id: str) -> list[dict[str, Any]]:
    """The person's workspaces in name order, each with their role and
    whether it is the last used (FR-CRM-WS-03, -04).

    The last used is the membership with the latest `last_used_at`; someone
    who has never switched gets the first by name, so exactly one is marked
    whenever there are any. D13: a read that names no workspace uses it.
    """
    rows = sorted(store.memberships_for_user(user_id), key=lambda m: m["workspace"]["name"])
    used = [m for m in rows if m.get("last_used_at")]
    last = max(used, key=lambda m: m["last_used_at"]) if used else (rows[0] if rows else None)
    return [
        {
            **m["workspace"],
            "membership_id": m["id"],
            "role": m["role"],
            "last_used": m is last,
        }
        for m in rows
    ]
