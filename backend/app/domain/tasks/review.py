"""The weekly review — spec 008 FR-TASK-REVIEW-01; TP-08; contracts §2
`GET /tasks/review`, `POST /tasks/review/dismiss`.

On Mondays, in the person's own day (FR-TASK-CHG-06), My Tasks opens with a
review panel unless they dismissed it for the week:

- **This week** and **This month**: their open and waiting tasks whose
  effective bucket is that section, sorted as My Tasks sorts a section;
- **Waiting over 7 days**: their waiting tasks created more than 7 days
  ago (age as the Waiting on view counts it), oldest first;
- **Inbox**: their unprocessed inbox items (the service reads them).

Dismissing stores the Monday of the current week; the panel stays hidden
until the next Monday. Shown in the app, never sent (TP-08).

Pure: the service passes the rows, `today` and the zone.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final

from app.domain.tasks import rules

#: FR-TASK-REVIEW-01 — a waiting task older than this many days is listed.
WAITING_DAYS: Final = 7


def week_start(today: date) -> date:
    """The Monday of the person's current week."""
    return rules.week_of(today)[0]


def show(today: date, dismissed_week: Any) -> bool:
    """FR-TASK-REVIEW-01 — true on the person's Monday unless they dismissed
    the panel for this week."""
    if today.weekday() != 0:
        return False
    if dismissed_week is None:
        return True
    return date.fromisoformat(str(dismissed_week)[:10]) != week_start(today)


def in_section(rows: list[dict[str, Any]], section: str, today: date) -> list[dict[str, Any]]:
    """The live open and waiting rows whose effective bucket is `section`,
    by due date, priority and title (FR-TASK-LIST-01)."""
    return sorted(
        (
            r
            for r in rows
            if rules.is_open(r)
            and not r.get("deleted_at")
            and rules.effective_bucket(r, today) == section
        ),
        key=rules.sort_key,
    )


def age_days(task: dict[str, Any], today: date, zone: str) -> int:
    """Whole days since the task was created, in the person's own day."""
    created = rules.day_of(datetime.fromisoformat(str(task["created_at"])), zone)
    return max(0, (today - created).days)


def waiting_over(
    rows: list[dict[str, Any]], today: date, zone: str
) -> list[tuple[dict[str, Any], int]]:
    """The live waiting rows older than `WAITING_DAYS`, with their age,
    oldest first."""
    aged = [
        (r, age_days(r, today, zone))
        for r in rows
        if r.get("status") == "waiting" and not r.get("deleted_at")
    ]
    kept = [(r, days) for r, days in aged if days > WAITING_DAYS]
    return sorted(kept, key=lambda pair: (-pair[1], rules.sort_key(pair[0])))
