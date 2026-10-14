"""Weekly supply and demand — spec 007 FR-CRM-CHG-17, FR-CRM-CAP-01 to -03.

One calculation behind two screens: TVA's forecast (`weeks`) and the CRM
capacity check. If each computed its own, they would disagree the first time
somebody changed one of them, and a seller would be told there is room that
the delivery lead can see is not there (FR-CRM-CAP-03).

Pure: the caller reads people, allocations, holidays, leave and contracted
hours, and this module only adds them up.

- **Supply** is each person's contracted hours (spec 005 FR-CTC-06) on the
  working days between their joining and leaving dates, less the holidays
  that apply to them and their approved leave (pending leave is not yet
  time off). A person contributes to a week when their supply there is
  above zero.
- **Demand** is what the allocations passed in imply, at the same rule as the
  forecast (`timesheets.allocated_hours`, Q-02): a share of the person's
  capacity, which shrinks with their leave. Confirmed and tentative are kept
  apart, because pipeline work is not yet anybody's (spec 002 FR-PROJ-08).
  The caller passes only allocations that count: not released, not on an
  archived project (FR-CRM-CHG-03, -04). Demand covers the same people and
  days as supply: an allocation held by someone not in `people`, or running
  past their leaving date, counts only where they supply hours.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain import timesheets as rules

ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Person:
    user_id: str
    joined_on: date | None = None
    left_on: date | None = None


@dataclass(frozen=True)
class Week:
    week_start: date
    supply_hours: Decimal
    confirmed_hours: Decimal
    tentative_hours: Decimal
    contributors: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "week_start": self.week_start.isoformat(),
            "supply_hours": str(self.supply_hours),
            "confirmed_hours": str(self.confirmed_hours),
            "tentative_hours": str(self.tentative_hours),
            "contributors": self.contributors,
        }


def week_starts(start: date, end: date) -> list[date]:
    """The Mondays of every week that touches `start`..`end`."""
    monday = start - timedelta(days=start.weekday())
    out = []
    while monday <= end:
        out.append(monday)
        monday += timedelta(days=7)
    return out


def _clip(
    lo: date, hi: date, joined_on: date | None, left_on: date | None
) -> tuple[date, date] | None:
    lo = max(lo, joined_on) if joined_on else lo
    hi = min(hi, left_on) if left_on else hi
    return (lo, hi) if lo <= hi else None


def weekly(
    start: date,
    end: date,
    *,
    people: list[Person],
    allocations: list[rules.Allocation],
    holidays: dict[str, set[date]],
    leave: dict[str, dict[date, Decimal]],
    approved_leave: dict[str, dict[date, Decimal]],
    hours_on: Callable[[str], Callable[[date], Decimal]],
) -> list[Week]:
    """Per week touching `start`..`end`. Each week counts only its days inside
    the range, so a forecast that starts on a Wednesday does not count the
    Monday and Tuesday already gone.

    `approved_leave` reduces supply; `leave` (approved and pending) reduces
    demand, as the forecast's allocated hours do."""
    by_id = {person.user_id: person for person in people}
    out = []
    for monday in week_starts(start, end):
        lo, hi = max(monday, start), min(monday + timedelta(days=6), end)

        supply = ZERO
        contributors = 0
        for person in people:
            window = _clip(lo, hi, person.joined_on, person.left_on)
            if window is None:
                continue
            hours = rules.available_hours(
                window[0],
                window[1],
                holidays=holidays.get(person.user_id, set()),
                leave_days=approved_leave.get(person.user_id, {}),
                hours_on=hours_on(person.user_id),
            )
            if hours > ZERO:
                supply += hours
                contributors += 1

        confirmed = ZERO
        tentative = ZERO
        for allocation in allocations:
            person = by_id.get(allocation.user_id)
            if person is None:
                continue
            window = rules.overlap(allocation.starts_on, allocation.ends_on, lo, hi)
            if window is not None:
                window = _clip(*window, person.joined_on, person.left_on)
            if window is None:
                continue
            hours = rules.allocated_hours(
                allocation.percent,
                window[0],
                window[1],
                holidays=holidays.get(allocation.user_id, set()),
                leave_days=leave.get(allocation.user_id, {}),
                hours_on=hours_on(allocation.user_id),
            )
            if allocation.tentative:
                tentative += hours
            else:
                confirmed += hours

        out.append(
            Week(
                week_start=monday,
                supply_hours=supply.quantize(CENT),
                confirmed_hours=confirmed.quantize(CENT),
                tentative_hours=tentative.quantize(CENT),
                contributors=contributors,
            )
        )
    return out
