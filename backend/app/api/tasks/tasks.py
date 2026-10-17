"""Task routes, phase 1 — spec 008 contracts §1; FR-TASK-LIST-01 to -07,
FR-TASK-VIS-01 to -08, FR-TASK-DATA-05 to -13, FR-TASK-ROLL-03, -04.

HTTP only: each route takes its `TaskActor` from the phase guard
(`TasksDep`, level 1) and hands the rest to `app.domain.tasks.service` (every
write, and `create_task`, the 008/009 interface) or to `services/tasks`
(reads). The same routes serve the web app and the `task_*` MCP tools.

Route order (contracts §1): the fixed paths are declared before
`/tasks/{id}`, and `{id}` and `{link_id}` are typed `UUID`, so a fixed
segment such as `trash` is never read as an id.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.errors import ProblemDetail
from app.api.tasks.deps import TasksDep
from app.domain.tasks import service
from app.schemas.tasks import TaskAudienceIn, TaskCreate, TaskIn, TaskLinkIn, TaskPatch
from app.services.tasks import listing, people

router = APIRouter(prefix="/tasks", tags=["tasks"])

LinkType = Literal["company", "person", "opportunity", "campaign", "tva_project"]
Priority = Literal["urgent", "high", "normal", "low"]


# ---------------------------------------------------------------------------
# Fixed paths — before /tasks/{id}
# ---------------------------------------------------------------------------


@router.get("")
def list_tasks(
    actor: TasksDep,
    view: Literal["mine"] = "mine",
    tag: str | None = None,
    priority: Priority | None = None,
    link_type: LinkType | None = None,
    link_id: UUID | None = None,
    q: str | None = None,
    closed_limit: int = Query(default=20, ge=1, le=100),
    closed_before: str | None = None,
) -> dict[str, Any]:
    """My Tasks: open and waiting tasks by section, and a page of closed ones."""
    return listing.my_tasks(
        actor,
        tag=tag,
        priority=priority,
        link_type=link_type,
        link_id=str(link_id) if link_id else None,
        q=q,
        closed_limit=closed_limit,
        closed_before=closed_before,
    )


@router.post("", status_code=201)
def create(actor: TasksDep, body: TaskIn) -> dict[str, Any]:
    """Adds a task through `create_task`; `source` follows the channel."""
    fields = body.model_dump(exclude={"assignee"}, exclude_unset=True)
    if body.assignee is not None:
        if body.assignee_id is not None:
            raise ProblemDetail(422, "Give the assignee once, by id or by name.")
        fields["assignee_id"] = people.resolve(actor, body.assignee)
    payload = TaskCreate(**fields, source="nldw" if actor.channel == "mcp" else "manual")
    return service.create_task(actor, payload)


@router.post("/audience")
def audience(actor: TasksDep, body: TaskAudienceIn) -> dict[str, str]:
    """Who will see a draft (FR-TASK-VIS-08)."""
    return people.audience(actor, body)


@router.get("/people")
def assignable(
    actor: TasksDep,
    q: str | None = None,
    workspace_id: UUID | None = None,
    project_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """People the caller may assign for a draft."""
    return people.people(
        actor,
        q=q,
        workspace_id=str(workspace_id) if workspace_id else None,
        project_id=str(project_id) if project_id else None,
    )


@router.get("/linkable")
def linkable(actor: TasksDep, q: str = "") -> dict[str, Any]:
    """CRM records and TVA projects the caller may link."""
    return people.linkable(actor, q)


@router.get("/trash")
def trash(actor: TasksDep) -> list[dict[str, Any]]:
    """The caller's deleted tasks with no workspace, with days left."""
    return listing.trash(actor)


# ---------------------------------------------------------------------------
# One task — /tasks/{id}
# ---------------------------------------------------------------------------


@router.get("/{id}")
def get_task(actor: TasksDep, id: UUID) -> dict[str, Any]:
    """The task, its subtasks and its history."""
    return listing.detail(actor, str(id))


@router.patch("/{id}")
def update(actor: TasksDep, id: UUID, body: TaskPatch) -> dict[str, Any]:
    """Changes the fields sent."""
    return service.update_task(actor, str(id), body)


@router.delete("/{id}")
def delete(actor: TasksDep, id: UUID) -> dict[str, Any]:
    """Moves the task to the Trash."""
    return service.delete_task(actor, str(id))


@router.post("/{id}/complete")
def complete(actor: TasksDep, id: UUID) -> dict[str, Any]:
    """Marks the task done."""
    return service.complete_task(actor, str(id))


@router.post("/{id}/reopen")
def reopen(actor: TasksDep, id: UUID) -> dict[str, Any]:
    """Back to Open."""
    return service.reopen_task(actor, str(id))


@router.post("/{id}/links")
def add_link(actor: TasksDep, id: UUID, body: TaskLinkIn) -> dict[str, Any]:
    """Links a CRM record or a TVA project."""
    return service.add_link(actor, str(id), body)


@router.delete("/{id}/links/{link_id}")
def remove_link(actor: TasksDep, id: UUID, link_id: UUID) -> dict[str, Any]:
    """Removes a link."""
    return service.remove_link(actor, str(id), str(link_id))


@router.post("/{id}/restore")
def restore(actor: TasksDep, id: UUID) -> dict[str, Any]:
    """Brings the task back from the Trash."""
    return service.restore_task(actor, str(id))
