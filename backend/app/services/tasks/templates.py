"""Starter templates — spec 008 FR-TASK-RRULE-05; contracts §2
`POST /tasks/templates/{key}`, `GET /tasks/settings` (phase 3).

Each template is one recurring task, added to the person's own My Tasks only
when they choose it: monthly invoice follow-ups, the weekly pipeline review,
the statutory dates and the monthly payroll check. The statutory dates need
the owner's list of GST and statutory dates (spec §15.1); until it exists the
template is listed as not available and adding it is refused.

A template is created through `create_task`, like any task (a private task
of the caller's, source Manual), and then given its recurrence through
`update_task`, so the recurrence has the same checks as one set by hand.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

from app.domain.tasks import recurrence, rules, service
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import TaskActor
from app.schemas.tasks import TaskCreate, TaskPatch

NO_SUCH_TEMPLATE: Final = "No such template."
STATUTORY_NOT_SET: Final = "The statutory dates are not set up yet."

#: FR-TASK-RRULE-05, in the spec's order: key → (title, recurrence). The
#: statutory dates have no recurrence until the owner supplies them.
TEMPLATES: Final[dict[str, tuple[str, str | None]]] = {
    "invoice_follow_ups": ("Follow up unpaid invoices", "FREQ=MONTHLY;BYMONTHDAY=5"),
    "pipeline_review": ("Review the sales pipeline", "FREQ=WEEKLY;BYDAY=MO"),
    "statutory_dates": ("GST and statutory dates", None),
    "payroll_check": ("Check this month's payroll", "FREQ=MONTHLY;BYMONTHDAY=25"),
}


def offered() -> list[dict[str, Any]]:
    """The templates as My Tasks settings list them."""
    return [
        {
            "key": key,
            "title": title,
            "repeats": recurrence.label(rule) if rule else None,
            "available": rule is not None,
        }
        for key, (title, rule) in TEMPLATES.items()
    ]


def add(actor: TaskActor, key: str, starts_on: date | None = None) -> dict[str, Any]:
    """Adds the template's recurring task, due on its first date on or after
    `starts_on` (the caller's today when not given); returns it as `TaskOut`."""
    if key not in TEMPLATES:
        raise TaskRefused(404, NO_SUCH_TEMPLATE)
    title, rule = TEMPLATES[key]
    if rule is None:
        raise TaskRefused(422, STATUTORY_NOT_SET)
    today = rules.day_of(service._now(), actor.user.profile.get("timezone"))
    try:
        due = recurrence.first_due(rule, starts_on or today)
    except ValueError as exc:
        raise TaskRefused(422, str(exc)) from None
    task = service.create_task(actor, TaskCreate(title=title, due_on=due, source="manual"))
    return service.update_task(actor, task["id"], TaskPatch(recurrence=rule))
