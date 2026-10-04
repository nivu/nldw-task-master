"""Hours by project category, month by month — spec 002 FR-ANALYTICS-08.

Pure: hand it the logged lines, the allocations and each person's calendar,
get back logged and planned hours per category per month. Time logged against
no project (an activity — learning, internal work, admin, other) is a row of
its own rather than folded into a category: it is real work, and hiding it
inside "internal" would make the internal projects look bigger than they are.

Hours only. The money by category is spec 005 FR-PNL-04 and lives with the
profit table, behind the manager guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain import pnl
from app.domain import timesheets as rules

ZERO = Decimal("0.00")

#: The row for time logged against no project.
NOT_ON_A_PROJECT = "activity"
NOT_ON_A_PROJECT_LABEL = "Not on a project"


@dataclass(frozen=True)
class Logged:
    day: date
    project_id: str | None
    hours: Decimal


@dataclass(frozen=True)
class Cell:
    logged: Decimal
    # None for activities: nobody is allocated to learning, so there is no
    # plan to compare against — not a plan of zero.
    planned: Decimal | None

    def as_dict(self) -> dict:
        return {
            "logged_hours": str(self.logged),
            "planned_hours": None if self.planned is None else str(self.planned),
        }


def by_category(
    months: list[tuple[int, int]],
    *,
    logged: list[Logged],
    allocations: list[rules.Allocation],
    categories: dict[str, str],
    holidays: dict[str, set[date]],
    leave: dict[str, dict[date, Decimal]],
) -> dict[str, list[Cell]]:
    """One row per category in fixed order, then the activities row.

    Planned hours are the allocations' hours (`timesheets.allocated_hours`):
    working days in the month less that person's holidays and leave, times
    the percent — the same arithmetic as the capacity forecast, so the two
    pages never disagree. A project with no category counts as `client`, the
    default (FR-PROJ-06).
    """
    keys = (*rules.PROJECT_CATEGORIES, NOT_ON_A_PROJECT)
    out: dict[str, list[Cell]] = {key: [] for key in keys}

    for year, month in months:
        first, last = pnl.month_bounds(year, month)
        logged_m = {key: ZERO for key in keys}
        planned_m = {key: ZERO for key in rules.PROJECT_CATEGORIES}

        for line in logged:
            if not (first <= line.day <= last):
                continue
            key = (
                (categories.get(line.project_id) or "client")
                if line.project_id
                else NOT_ON_A_PROJECT
            )
            logged_m[key] += line.hours

        for a in allocations:
            window = rules.overlap(a.starts_on, a.ends_on, first, last)
            if window is None:
                continue
            planned_m[categories.get(a.project_id) or "client"] += rules.allocated_hours(
                a.percent,
                window[0],
                window[1],
                holidays=holidays.get(a.user_id, set()),
                leave_days=leave.get(a.user_id, {}),
            )

        for key in keys:
            out[key].append(Cell(logged=logged_m[key], planned=planned_m.get(key)))

    return out


def totals(rows: dict[str, list[Cell]]) -> list[Cell]:
    """Every row summed per month. Planned sums the categories only, since the
    activities row has no plan."""
    if not rows:
        return []
    n = len(next(iter(rows.values())))
    return [
        Cell(
            logged=sum((cells[i].logged for cells in rows.values()), ZERO),
            planned=sum(
                (cells[i].planned for cells in rows.values() if cells[i].planned is not None),
                ZERO,
            ),
        )
        for i in range(n)
    ]
