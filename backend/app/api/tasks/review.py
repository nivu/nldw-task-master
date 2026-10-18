"""The weekly review, phase 4 — spec 008 contracts §2 `GET /tasks/review`,
`POST /tasks/review/dismiss`; FR-TASK-REVIEW-01.

HTTP only: the phase-4 guard (`TasksDep4`) and `services/tasks/review`. A
panel in the app, never a pushed message (TP-08). This router is listed in
`ROUTERS` before the `tasks.py` router, so `/tasks/review` is never read as
`/tasks/{id}` (contracts §1 "Route order").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.tasks.deps import TasksDep4
from app.services.tasks import review

router = APIRouter(prefix="/tasks/review", tags=["tasks"])


@router.get("")
def get_review(actor: TasksDep4) -> dict[str, Any]:
    """This week and This month items, waiting items over 7 days and the
    unprocessed inbox; `show` is true on the caller's Monday unless
    dismissed for the week."""
    return review.build(actor)


@router.post("/dismiss")
def dismiss(actor: TasksDep4) -> dict[str, str]:
    """Hides the review panel for the rest of the week."""
    return review.dismiss(actor)
