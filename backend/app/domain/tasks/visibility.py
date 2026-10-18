"""Who sees, changes and deletes a task — spec 008 §3, FR-TASK-VIS-01, -02,
-05, -07, -08, FR-TASK-DATA-09; T4, T5; TP-01, TP-02; plan §5.4.

The one place these rules live (FR-TASK-VIS-01): every task route, every CRM
route that returns or changes a task, every task tool and `create_task` pass
through it. A task the caller may not see is answered 404, never 403
(FR-TASK-VIS-02); that is the callers' job, on `sight(...) == "none"`.

Two fact bundles, loaded once per request by `services/tasks/viewer.py` and
the task store: the viewer and the task. Pure.

| Task | Sight |
|---|---|
| CRM (has a workspace) | its active members only, by CRM role (`007` §3.3): Admin and Member edit; a Viewer reads, or changes the status of a task assigned to them (Q-57). Creator and assignee get nothing more (§3.1) |
| No workspace | its creator and current assignee edit; a project task is read only to everyone else who can see the project (TP-02) |

"Can see a TVA project" (§3.1): a TVA lead, manager or admin sees every
project; a TVA user those they are or were allocated to (TP-01); a
`crm_only` account none. A project's lead gives no sight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

Sight = Literal["none", "read_only", "status_only", "edit"]
ProjectField = Literal["full", "name_only", "absent"]

#: TVA roles that see every project (§3.1).
PROJECT_ROLES: Final = frozenset({"lead", "manager", "admin"})


@dataclass(frozen=True)
class Viewer:
    """Spec §3 — who is looking. `memberships` holds active ones only, by
    workspace id, with the CRM role; `allocated_project_ids` every project
    the person is or was allocated to (TP-01)."""

    id: str
    account_type: str = "staff"
    tva_role: str = "user"
    memberships: dict[str, str] = field(default_factory=dict)
    allocated_project_ids: frozenset[str] = frozenset()
    tasks_level: int = 0


@dataclass(frozen=True)
class TaskFacts:
    """The facts of one task the rules read; `project_id` from its
    `tva_project` link."""

    id: str
    workspace_id: str | None
    created_by: str | None
    assignee_id: str | None
    project_id: str | None = None
    deleted_at: str | None = None
    parent_id: str | None = None


def can_see_project(viewer: Viewer, project_id: str | None) -> bool:
    """Spec §3.1 — for tasks only; gives no project page or read."""
    if project_id is None or viewer.account_type == "crm_only":
        return False
    if viewer.tva_role in PROJECT_ROLES:
        return True
    return project_id in viewer.allocated_project_ids


def _mine(viewer: Viewer, task: TaskFacts) -> bool:
    return viewer.id in (task.created_by, task.assignee_id)


def sight(viewer: Viewer, task: TaskFacts) -> Sight:
    """What the viewer may do with the task (§3.1, §3.3)."""
    if task.workspace_id is not None:
        role = viewer.memberships.get(task.workspace_id)
        if role in ("admin", "member"):
            return "edit"
        if role == "viewer":
            return "status_only" if task.assignee_id == viewer.id else "read_only"
        return "none"
    if _mine(viewer, task):
        return "edit"
    if task.project_id is not None and can_see_project(viewer, task.project_id):
        return "read_only"
    return "none"


def may_delete(viewer: Viewer, task: TaskFacts) -> bool:
    """CRM task: `007` FR-CRM-DEL-04 (an Admin, or a Member who created it;
    a task has no owner, Q-36). Otherwise the creator only (§3.3)."""
    if task.workspace_id is not None:
        role = viewer.memberships.get(task.workspace_id)
        return role == "admin" or (role == "member" and task.created_by == viewer.id)
    return task.created_by == viewer.id


def may_restore(viewer: Viewer, task: TaskFacts) -> bool:
    """CRM task: a workspace Admin (`007` FR-CRM-DEL-01). Otherwise the
    creator only, never the assignee (§3.3)."""
    if task.workspace_id is not None:
        return viewer.memberships.get(task.workspace_id) == "admin"
    return task.created_by == viewer.id


def project_field(viewer: Viewer, task: TaskFacts) -> ProjectField:
    """FR-TASK-VIS-07 — CRM links win: on a CRM task a member who cannot see
    the project gets no project at all; on a task with no workspace, a
    creator or assignee who cannot see it gets its name only."""
    if task.project_id is None:
        return "absent"
    if can_see_project(viewer, task.project_id):
        return "full"
    if task.workspace_id is None and _mine(viewer, task):
        return "name_only"
    return "absent"


def parent_field(
    viewer: Viewer, parent: TaskFacts | None, title: str | None
) -> dict[str, Any] | None:
    """FR-TASK-DATA-09 — the parent, or "a private task" with no id or title
    when the viewer may not see it."""
    if parent is None:
        return None
    if sight(viewer, parent) == "none":
        return {"private": True}
    return {"id": parent.id, "title": title}


def audience(task_draft: dict[str, Any], names: dict[str, str]) -> str:
    """FR-TASK-VIS-08 — who will see the task, for the person looking at it
    (`viewer_id`). The draft gives `created_by`, `assignee_id`, and the
    `workspace_name` of its CRM links or the `project_name` of its project
    link; `names` gives people's display names by id."""
    if task_draft.get("workspace_name"):
        return f"Visible to: {task_draft['workspace_name']} members"
    if task_draft.get("project_name"):
        return (
            "Visible to: leads, managers, admins and people allocated to project "
            f"{task_draft['project_name']}"
        )
    viewer_id = task_draft.get("viewer_id")
    others = [
        p
        for p in dict.fromkeys((task_draft.get("created_by"), task_draft.get("assignee_id")))
        if p and p != viewer_id
    ]
    if not others:
        return "Visible to: only you"
    return f"Visible to: you and {names.get(others[0]) or 'one other person'}"


def history_visible(viewer: Viewer, row: dict[str, Any], era: dict[str, Any]) -> bool:
    """FR-TASK-VIS-05 — an audit row written while the task had no workspace
    (its `workspace_id` is null; 048 gives a link row its task's) is shown
    only to the task's creator and to whoever was its assignee at that row
    (`era`: `created_by`, `assignee_id`, from replaying the task's rows in
    order). A row written with a workspace follows the task's own sight."""
    if row.get("workspace_id") is not None:
        return True
    return viewer.id in (era.get("created_by"), era.get("assignee_id"))
