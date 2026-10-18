"""The morning brief — spec 008 FR-TASK-BRIEF-01 to -03, FR-TASK-DEL-06;
Journey 4.8; TP-08; contracts §2 `GET /tasks/brief`.

The task part of the brief, in the person's own day (FR-TASK-CHG-06):

- **Today**: their open and waiting tasks whose effective bucket is Today
  and that are not overdue;
- **Overdue**: those due before today, oldest due first;
- **Nudge?**: the waiting tasks they created that nobody has touched for
  their nudge days or more (`task_nudge_days`, 1-30, default 5), counted
  from `updated_at` in their own day.

The brief is read, never pushed (TP-08). Pure: the service passes the rows,
`today` and the zone.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.domain.tasks import rules


def today_and_overdue(
    rows: list[dict[str, Any]], today: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """FR-TASK-BRIEF-01 — the rows for Today and for Overdue, each sorted as
    a My Tasks section (due date, priority, title). A task is in one of the
    two at most."""
    live = sorted(
        (r for r in rows if rules.is_open(r) and not r.get("deleted_at")), key=rules.sort_key
    )
    overdue = [r for r in live if rules.days_overdue(r, today) > 0]
    on_today = [
        r
        for r in live
        if rules.days_overdue(r, today) == 0 and rules.effective_bucket(r, today) == "today"
    ]
    return on_today, overdue


def idle_days(task: dict[str, Any], today: date, zone: str) -> int:
    """Whole days since the task last changed, in the person's own day."""
    changed = rules.day_of(datetime.fromisoformat(str(task["updated_at"])), zone)
    return max(0, (today - changed).days)


def nudges(
    rows: list[dict[str, Any]], today: date, zone: str, nudge_days: int
) -> list[tuple[dict[str, Any], int]]:
    """FR-TASK-DEL-06 — the waiting rows untouched for `nudge_days` or more,
    with their idle days, longest idle first."""
    due = [
        (r, idle_days(r, today, zone))
        for r in rows
        if r.get("status") == "waiting" and not r.get("deleted_at")
    ]
    kept = [(r, days) for r, days in due if days >= nudge_days]
    return sorted(kept, key=lambda pair: (-pair[1], rules.sort_key(pair[0])))
