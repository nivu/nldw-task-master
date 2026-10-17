"""The viewer's facts — spec 008 plan §5.4.

Loaded once per request, in at most three reads (memberships, allocations,
the global phase default; the profile is the signed-in user's own), and
handed to `domain/tasks/visibility`.
"""

from __future__ import annotations

from typing import Any

from app.domain.tasks import rollout
from app.domain.tasks.visibility import Viewer
from app.services.tasks import store


def load(user: Any) -> Viewer:
    """The signed-in person as a task viewer: their CRM memberships, every
    project they are or were allocated to (none for a `crm_only` account),
    their TVA role and their effective phase level."""
    memberships = {m["workspace_id"]: m["role"] for m in store.memberships_of(user.id)}
    allocated = frozenset() if user.is_crm_only else frozenset(store.allocated_project_ids(user.id))
    return Viewer(
        id=user.id,
        account_type=user.account_type,
        tva_role=user.role,
        memberships=memberships,
        allocated_project_ids=allocated,
        tasks_level=rollout.level_for(user.profile, store.default_level()),
    )
