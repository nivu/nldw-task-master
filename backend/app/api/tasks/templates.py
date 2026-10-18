"""Starter templates, phase 3 — spec 008 contracts §2
`POST /tasks/templates/{key}`; FR-TASK-RRULE-05.

HTTP only: the phase-3 guard (`TasksDep3`) and `services/tasks/templates`.
Web only (contracts §5: no tool). This router is listed in `ROUTERS` before
the `tasks.py` router, so `/tasks/templates/...` is never read as
`/tasks/{id}` (contracts §1 "Route order").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.tasks.deps import TasksDep3
from app.schemas.tasks import TaskTemplateIn
from app.services.tasks import templates

router = APIRouter(prefix="/tasks/templates", tags=["tasks"])


@router.post("/{key}", status_code=201)
def add_template(actor: TasksDep3, key: str, body: TaskTemplateIn | None = None) -> dict[str, Any]:
    """Adds the template's recurring task to the caller's My Tasks."""
    return templates.add(actor, key, body.starts_on if body else None)
