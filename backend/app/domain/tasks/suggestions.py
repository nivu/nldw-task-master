"""Day-log suggestions — spec 008 FR-TASK-LOG-01 to -03, -05; G-6; Journey
4.9; TP-09, TP-19; TQ-16; plan §5.9.

Which tasks Log a day offers: the person's own, Done, completed on that date
in Asia/Kolkata (the day log's calendar, TQ-16), never imported ones (TP-19),
only with phase 3 on, never for a `crm_only` account, and only on a day that
can be logged. Each carries its TVA project only when the person may see it
in full (FR-TASK-VIS-07) and that project is confirmed and not archived (a
tentative or archived project takes no new time entries, `002` FR-PROJ-04,
FR-PROJ-08). A suggestion whose title is already an item of a note that day
shows as added (TP-09).

Accept, Ignore and the merge into a line's note are client state on the Log
a day page (plan §5.9); nothing here merges or writes. Pure: the service
passes the rows, the day and that day's notes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain import timesheets
from app.domain.tasks import rules, visibility
from app.domain.tasks.visibility import TaskFacts, Viewer

#: FR-TASK-LOG-02 — the phase that switches suggestions on.
LEVEL: Final = 3
#: TQ-16 — the day log's calendar.
ZONE: Final = "Asia/Kolkata"


def offered(level: int, account_type: str, can_log: bool) -> bool:
    """FR-TASK-LOG-02, -03 — whether the day's read carries suggestions."""
    return level >= LEVEL and account_type != "crm_only" and can_log


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The day in Asia/Kolkata, as `[start, end)` aware datetimes."""
    start = datetime.combine(day, time(), ZoneInfo(ZONE))
    return start, start + timedelta(days=1)


def completed_on(task: dict[str, Any], day: date) -> bool:
    """FR-TASK-LOG-01 — Done, completed on `day` in Asia/Kolkata, and not
    imported (TP-19)."""
    if task.get("status") != "done" or not task.get("completed_at"):
        return False
    if task.get("source") == "import" or task.get("deleted_at"):
        return False
    return rules.day_of(datetime.fromisoformat(str(task["completed_at"])), ZONE) == day


def loggable_project(
    viewer: Viewer,
    task: dict[str, Any],
    links: list[dict[str, Any]],
    projects: dict[str, dict[str, Any]],
) -> dict[str, str] | None:
    """FR-TASK-LOG-01 — the task's TVA project as `{id, name}`, only when the
    viewer may see it in full (FR-TASK-VIS-07: no id or name on a CRM task,
    the name alone on a task with no workspace, neither of which can be
    logged against) and it is confirmed and not archived."""
    link = next((lk for lk in links if lk["record_type"] == "tva_project"), None)
    project = projects.get(link["record_id"]) if link else None
    if project is None or project.get("is_archived") or timesheets.is_tentative(project):
        return None
    facts = TaskFacts(
        id=task["id"],
        workspace_id=task.get("workspace_id"),
        created_by=task.get("created_by"),
        assignee_id=task.get("assignee_id"),
        project_id=project["id"],
    )
    if visibility.project_field(viewer, facts) != "full":
        return None
    return {"id": project["id"], "name": project["name"]}


def already_added(title: str, notes: Iterable[str | None]) -> bool:
    """FR-TASK-LOG-05 — the title is already a whole item of a note that day:
    the note itself, or one of its `; `-separated items as Accept merges
    them, never part of a longer word or phrase."""
    item = f"; {title.strip()}; "
    return any(item in f"; {note.strip()}; " for note in notes if note)


def suggestions(
    viewer: Viewer,
    tasks: list[dict[str, Any]],
    links: list[dict[str, Any]],
    projects: dict[str, dict[str, Any]],
    day: date,
    notes: list[str | None],
) -> list[dict[str, Any]]:
    """Contracts §3 `GET /timesheet/day` — `{task_id, title, project, added}`
    per task completed on `day`, in the order they were completed."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        by_task.setdefault(link["task_id"], []).append(link)
    done = sorted(
        (t for t in tasks if completed_on(t, day)),
        key=lambda t: (datetime.fromisoformat(str(t["completed_at"])), t["id"]),
    )
    return [
        {
            "task_id": t["id"],
            "title": t["title"],
            "project": loggable_project(viewer, t, by_task.get(t["id"], []), projects),
            "added": already_added(t["title"], notes),
        }
        for t in done
    ]
