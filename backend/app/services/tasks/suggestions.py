"""Day-log suggestions — spec 008 FR-TASK-LOG-01 to -03, -05, -07; plan §5.9;
contracts §3 `GET /timesheet/day`.

Gathers what `domain/tasks/suggestions` needs for one person's day: their
Done tasks completed that day in Asia/Kolkata, through the viewer filter
(FR-TASK-VIS-01: a CRM task only while they are a member), with their links
and TVA projects, each project only as they may see it (FR-TASK-VIS-07). Read only, called by `timesheets.day_for` alone, so no one
else's week, team week or sign-off ever carries them (FR-TASK-LOG-07).
Nothing is stored (TP-09).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.domain.tasks import suggestions
from app.services.tasks import listing, store, viewer


def for_day(
    user_id: str,
    day: date,
    *,
    level: int,
    account_type: str,
    can_log: bool,
    notes: list[str | None],
) -> list[dict[str, Any]]:
    """The day's suggestions for the person, `[]` when none are offered."""
    if not suggestions.offered(level, account_type, can_log):
        return []
    profiles = store.get_profiles([user_id])
    if not profiles:
        return []
    me = viewer.of_profile(profiles[0])
    start, end = suggestions.day_bounds(day)
    rows = listing._seen(me, store.done_between(user_id, start.isoformat(), end.isoformat()))
    if not rows:
        return []
    links = store.list_links([r["id"] for r in rows])
    project_ids = [lk["record_id"] for lk in links if lk["record_type"] == "tva_project"]
    projects = {p["id"]: p for p in store.get_projects(project_ids)}
    return suggestions.suggestions(me, rows, links, projects, day, notes)
