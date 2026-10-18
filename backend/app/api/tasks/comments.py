"""Task comments, phase 2 — spec 008 contracts §2; FR-TASK-DEL-04.

HTTP only: each route takes its `TaskActor` from the phase-2 guard
(`TasksDep2`) and hands the rest to `app.domain.tasks.service`. Comments are
read by whoever may see the task, never edited, and deleted by their author
only. `{id}` and `{comment_id}` are typed `UUID` (contracts §1 "Route
order"); this router is listed in `ROUTERS` before the `tasks.py` router.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.api.tasks.deps import TasksDep2
from app.domain.tasks import service
from app.schemas.tasks import TaskCommentIn

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{id}/comments")
def list_comments(actor: TasksDep2, id: UUID) -> list[dict[str, Any]]:
    """The task's comments, oldest first."""
    return service.comments(actor, str(id))


@router.post("/{id}/comments")
def add_comment(actor: TasksDep2, id: UUID, body: TaskCommentIn) -> dict[str, Any]:
    """Adds a comment."""
    return service.add_comment(actor, str(id), body.body)


@router.delete("/{id}/comments/{comment_id}")
def delete_comment(actor: TasksDep2, id: UUID, comment_id: UUID) -> dict[str, Any]:
    """Deletes the caller's own comment."""
    return service.delete_comment(actor, str(id), str(comment_id))
