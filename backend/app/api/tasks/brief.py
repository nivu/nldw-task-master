"""The morning brief, phase 2 — spec 008 contracts §2 `GET /tasks/brief`;
FR-TASK-BRIEF-01 to -03.

HTTP only: the phase-2 guard (`TasksDep2`) and `services/tasks/brief`. A page
and a tool (`task_brief`), never a pushed message (TP-08). This router is
listed in `ROUTERS` before the `tasks.py` router, so `/tasks/brief` is never
read as `/tasks/{id}` (contracts §1 "Route order").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.tasks.deps import TasksDep2
from app.services.tasks import brief

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/brief")
def get_brief(actor: TasksDep2) -> dict[str, Any]:
    """Today's and overdue tasks, waiting ones due a nudge, and each
    workspace's CRM Today sections."""
    return brief.build(actor)
