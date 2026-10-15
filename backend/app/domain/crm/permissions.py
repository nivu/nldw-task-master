"""Who may do what in a CRM workspace — spec 007 §3.3, FR-CRM-ACC-10.

One table, read by the CRM guard (`api/crm/deps.py`) and by the services, so
the web app, the API and every `crm_*` MCP tool are held to the same cells
(D3). A CRM role is independent of the TVA role (FR-CRM-ACC-02): nothing here
reads one.

A cell is True (allowed), False (refused), OWN (allowed on records the
person owns or created, Q-36) or ASSIGNEE (allowed on a task assigned to
them, status only, Q-57).
"""

from __future__ import annotations

from typing import Final, Literal

Role = Literal["admin", "member", "viewer"]
ROLES: Final = ("admin", "member", "viewer")

OWN: Final = "own"
ASSIGNEE: Final = "assignee"

#: Spec §3.3, one row per capability, in the spec's order.
MATRIX: Final[dict[str, dict[str, bool | str]]] = {
    # Viewers read every record but receive no amount field (FR-CRM-ACC-11).
    "read_records": {"admin": True, "member": True, "viewer": True},
    "edit_records": {"admin": True, "member": True, "viewer": False},
    "soft_delete": {"admin": True, "member": OWN, "viewer": False},
    "restore_or_purge": {"admin": True, "member": False, "viewer": False},
    "see_amounts": {"admin": True, "member": True, "viewer": False},
    "manage_shared_views": {"admin": True, "member": True, "viewer": False},
    # Q-37: a Viewer keeps their own private views.
    "manage_private_views": {"admin": True, "member": True, "viewer": True},
    # Settings, select lists, FX rates, members, invitations, mailboxes.
    "administer": {"admin": True, "member": False, "viewer": False},
    "import": {"admin": True, "member": False, "viewer": False},
    "export": {"admin": True, "member": True, "viewer": False},
    "complete_task": {"admin": True, "member": True, "viewer": ASSIGNEE},
    # Proposal, Closed Won, Closed Lost. Revenue is confirmed only by a TVA
    # manager or admin (§5.20), which is a TVA rule, not a CRM cell.
    "move_deal": {"admin": True, "member": True, "viewer": False},
}

ACTIONS: Final = tuple(MATRIX)


def may(role: str, action: str, *, owns: bool = False, is_assignee: bool = False) -> bool:
    """Whether a CRM role may take an action. An unknown role or action is a no."""
    cell = MATRIX.get(action, {}).get(role, False)
    if cell == OWN:
        return owns
    if cell == ASSIGNEE:
        return is_assignee
    return cell is True
