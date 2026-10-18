"""The Ideas and Reading inbox, phase 4 — spec 008 contracts §2 inbox
routes; FR-TASK-INBOX-01 to -03.

HTTP only: the phase-4 guard (`TasksDep4`) and `services/tasks/inbox`. An
item is its owner's alone, admins included. `{id}` is typed `UUID`, and this
router is listed in `ROUTERS` before the `tasks.py` router, so
`/tasks/inbox...` is never read as `/tasks/{id}` (contracts §1 "Route
order").
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.api.tasks.deps import TasksDep4
from app.schemas.tasks import InboxIn, TaskIn
from app.services.tasks import inbox

router = APIRouter(prefix="/tasks/inbox", tags=["tasks"])


@router.get("")
def list_inbox(actor: TasksDep4) -> list[dict[str, Any]]:
    """The caller's unprocessed items, newest first."""
    return inbox.unprocessed(actor)


@router.post("", status_code=201)
def add(actor: TasksDep4, body: InboxIn) -> dict[str, Any]:
    """Adds an idea, link or note to the caller's own inbox."""
    return inbox.add(actor, body)


@router.post("/{id}/make-task")
def make_task(actor: TasksDep4, id: UUID, body: TaskIn | None = None) -> dict[str, Any]:
    """`{item, task}`: a task made from the item, or from the fields sent;
    the item records it and is marked processed."""
    return inbox.make_task(actor, str(id), body)


@router.post("/{id}/archive")
def archive(actor: TasksDep4, id: UUID) -> dict[str, Any]:
    """The item, processed and archived."""
    return inbox.archive(actor, str(id))
