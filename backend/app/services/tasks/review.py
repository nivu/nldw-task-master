"""The weekly review — spec 008 FR-TASK-REVIEW-01; TP-08; contracts §2
`GET /tasks/review`, `POST /tasks/review/dismiss`.

Gathers the caller's rows for `domain/tasks/review`: their open and waiting
tasks through the viewer filter and `present` (FR-TASK-VIS-01), and their
unprocessed inbox items (private to them, FR-TASK-INBOX-01). Read in the
app, never sent (TP-08): nothing here enqueues a message.
"""

from __future__ import annotations

from typing import Any

from app.domain.tasks import review, rules, service
from app.domain.tasks.service import TaskActor
from app.services.tasks import inbox, listing, present, store, viewer


def _today(actor: TaskActor) -> tuple[Any, str]:
    zone = actor.user.profile.get("timezone") or rules.DEFAULT_ZONE
    return rules.day_of(service._now(), zone), zone


def build(actor: TaskActor) -> dict[str, Any]:
    """`{show, week_start, this_week, this_month, waiting_over_7_days,
    inbox}` for the caller; `show` is true on their Monday unless they
    dismissed the panel for the week."""
    today, zone = _today(actor)
    me = viewer.load(actor.user)
    mine = listing._seen(me, store.assigned_tasks(actor.user.id, rules.OPEN))
    aged = review.waiting_over(mine, today, zone)
    waiting = present.present(me, [row for row, _ in aged], today=today)
    for task, (_, days) in zip(waiting, aged, strict=True):
        task["age_days"] = days
    return {
        "show": review.show(today, actor.user.profile.get("tasks_review_dismissed_week")),
        "week_start": review.week_start(today).isoformat(),
        "this_week": present.present(me, review.in_section(mine, "this_week", today), today=today),
        "this_month": present.present(
            me, review.in_section(mine, "this_month", today), today=today
        ),
        "waiting_over_7_days": waiting,
        "inbox": inbox.unprocessed(actor),
    }


def dismiss(actor: TaskActor) -> dict[str, str]:
    """FR-TASK-REVIEW-01 — hides the panel until next Monday."""
    today, _ = _today(actor)
    week = review.week_start(today).isoformat()
    store.set_review_dismissed(actor.user.id, week)
    return {"dismissed_week": week}
