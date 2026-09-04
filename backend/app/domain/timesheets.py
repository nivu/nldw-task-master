"""Timesheet rules — spec 002 §5.3, and the capacity arithmetic behind §5.4.

Pure functions over plain values, like `app.domain.rules`. Nothing here reads a
database, which is what lets the two rules most likely to produce quietly wrong
numbers — the edit window and capacity — be tested exhaustively.

Hours are `Decimal` throughout. Spec 002 §8 and `001` §6.2: these sums get
quoted in budget conversations, and binary floats accumulate error across
thousands of rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.calendar import is_weekend, today_in_company_tz

ZERO = Decimal("0.00")

PHASES = ("pre", "delivery", "support")
PHASE_LABELS = {
    "pre": "Pre-project",
    "delivery": "Delivery",
    "support": "Post-delivery support",
}

#: Q-06 default. A sanity check against a mistyped 80, not a position on
#: overwork. Overridable via `app_settings.max_hours_per_day`.
DEFAULT_MAX_HOURS_PER_DAY = Decimal("16")

#: Q-01 default. Days after the END of an entry's week during which it stays
#: editable. Overridable via `app_settings.timesheet_grace_days`.
DEFAULT_GRACE_DAYS = 7

#: A full working day, used to turn an allocation percentage into hours.
HOURS_PER_WORKING_DAY = Decimal("8")


# ---------------------------------------------------------------------------
# The edit window — Q-01, FR-TIME-08
# ---------------------------------------------------------------------------


def week_end(day: date) -> date:
    """The Sunday of the week `day` falls in (Monday-first weeks)."""
    return day + timedelta(days=6 - day.weekday())


def entry_locks_on(day: date, *, grace_days: int = DEFAULT_GRACE_DAYS) -> date:
    """The first date on which an entry for `day` can no longer be changed."""
    return week_end(day) + timedelta(days=grace_days + 1)


def is_locked(
    day: date, today: date | None = None, *, grace_days: int = DEFAULT_GRACE_DAYS
) -> bool:
    """Has the window for logging or correcting `day` closed?

    Deliberately NOT the same rule as a leave booking. `001` §6.3 locks a
    booking the moment its own date passes, and that is right for leave: the
    record exists before the day, and letting somebody delete it afterwards
    would let them reclaim the allowance.

    A timesheet is the opposite shape. The record is written *after* the day,
    and people genuinely forget Friday until Monday. A same-day lock would
    guarantee a permanently incomplete timesheet — and FR-ANALYTICS-05 is
    explicit that an incomplete timesheet is worse than none, because effort
    totals over it are not merely imprecise but biased low, and they get quoted
    as though they were complete.

    So the window runs to the end of the entry's own week plus a grace period.
    Still bounded: a project's recorded effort cannot be rewritten months later
    to change the conversation it already fed.

    `today` is injectable for tests. Application code passes nothing, so the
    server clock is the only one that decides (`001` NFR-04).
    """
    return (today or today_in_company_tz()) >= entry_locks_on(day, grace_days=grace_days)


# ---------------------------------------------------------------------------
# Logging rules — FR-TIME
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayEntry:
    """One line of a day: a project, and how the hours were split."""

    project_id: str
    hours_office: Decimal
    hours_home: Decimal
    note: str | None = None

    @property
    def total(self) -> Decimal:
        return self.hours_office + self.hours_home


def day_total(entries: list[DayEntry]) -> Decimal:
    return sum((e.total for e in entries), ZERO)


def check_hours(hours_office: Decimal, hours_home: Decimal) -> str | None:
    """FR-TIME-01/04 — the shape of a single line."""
    if hours_office < 0 or hours_home < 0:
        return "Hours cannot be negative."
    total = hours_office + hours_home
    if total <= 0:
        return "Enter some hours, or remove the line."
    if total % Decimal("0.25") != 0:
        # Quarter-hour granularity. FR-TIME-04 asks for half-hours at minimum;
        # quarters cost nothing and stop 1.3-hour entries that no one intended.
        return "Hours go in quarter-hour steps — 0.25, 0.5, 0.75, 1 and so on."
    if total > 24:
        return "A single line cannot exceed 24 hours."
    return None


def check_day_total(
    total: Decimal, *, max_hours: Decimal = DEFAULT_MAX_HOURS_PER_DAY
) -> str | None:
    """FR-TIME-05, Q-06."""
    if total > max_hours:
        return (
            f"That day totals {_hours(total)}, over the {_hours(max_hours)} limit. "
            "Check for a mistyped number."
        )
    return None


def check_can_log(
    day: date,
    *,
    today: date,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> str | None:
    """When a day may be logged or corrected — FR-TIME-08."""
    if day > today:
        # Logging future hours is not recording work, it is predicting it.
        # Forecasting is what allocations are for.
        return "You cannot log hours for a day that has not happened yet."
    if is_locked(day, today, grace_days=grace_days):
        locks = entry_locks_on(day, grace_days=grace_days)
        return (
            f"{day.isoformat()} closed for editing on {locks.isoformat()}. "
            "Ask an admin if something needs correcting."
        )
    return None


def leave_warning(category: str | None, duration: str | None) -> str | None:
    """Q-03, FR-TIME-10 — warn on a leave day, never refuse.

    People do work on a sick day, and on a half day. Refusing would make the
    data clean and the humans lie, and that effort would vanish from the
    project entirely. Warning keeps the record honest and leaves the
    inconsistency visible so somebody can ask about it.
    """
    if not category:
        return None
    from app.domain.rules import CATEGORY_LABELS

    label = CATEGORY_LABELS.get(category, category)
    length = "half day" if str(duration) in ("0.5", "0.50") else "full day"
    return (
        f"This day is recorded as {label.lower()} ({length}). "
        "You can still log hours — it will be flagged for review."
    )


# ---------------------------------------------------------------------------
# Capacity and forecast — Q-02, FR-ANALYTICS-06
# ---------------------------------------------------------------------------


def working_days(
    start: date,
    end: date,
    *,
    holidays: set[date],
    leave_days: dict[date, Decimal] | None = None,
) -> Decimal:
    """Days actually available between two dates, inclusive.

    Weekends and declared holidays are removed, then approved leave. A half day
    of leave removes half a day of capacity, which is why this returns Decimal
    rather than an int.

    Excluding leave is what makes a forecast worth reading. A capacity figure
    computed over raw calendar days says a team of three has 60 days next month
    when two of them are away for a fortnight, and the project plan built on it
    is wrong before anybody starts.
    """
    leave_days = leave_days or {}
    total = ZERO
    day = start
    while day <= end:
        if not is_weekend(day) and day not in holidays:
            total += Decimal("1") - min(leave_days.get(day, ZERO), Decimal("1"))
        day += timedelta(days=1)
    return total


def allocated_hours(
    percent: Decimal,
    start: date,
    end: date,
    *,
    holidays: set[date],
    leave_days: dict[date, Decimal] | None = None,
    hours_per_day: Decimal = HOURS_PER_WORKING_DAY,
) -> Decimal:
    """What an allocation implies, in hours, over a date range.

    Q-02: a percentage of *capacity*, not of the calendar — so it shrinks when
    somebody is on leave without anybody adjusting the allocation.
    """
    days = working_days(start, end, holidays=holidays, leave_days=leave_days)
    return (days * hours_per_day * percent / Decimal("100")).quantize(Decimal("0.01"))


def overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> tuple[date, date] | None:
    """The intersection of two date ranges, or None."""
    start, end = max(a_start, b_start), min(a_end, b_end)
    return (start, end) if start <= end else None


@dataclass(frozen=True)
class Allocation:
    user_id: str
    project_id: str
    starts_on: date
    ends_on: date
    percent: Decimal


def over_allocations(
    allocations: list[Allocation], start: date, end: date
) -> list[tuple[str, date, Decimal]]:
    """Days where somebody's concurrent allocations exceed 100% — FR-ALLOC-04.

    Returns (user_id, day, total_percent). Reported per day rather than per
    allocation because that is the question worth answering: "on which days is
    this person promised to more work than exists", not "which two rows
    overlap".
    """
    flagged: list[tuple[str, date, Decimal]] = []
    users = {a.user_id for a in allocations}

    for user_id in sorted(users):
        mine = [a for a in allocations if a.user_id == user_id]
        day = start
        while day <= end:
            if not is_weekend(day):
                total = sum((a.percent for a in mine if a.starts_on <= day <= a.ends_on), ZERO)
                if total > Decimal("100"):
                    flagged.append((user_id, day, total))
            day += timedelta(days=1)
    return flagged


# ---------------------------------------------------------------------------


def phase_for(day: date, phases: list[tuple[str, date, date]]) -> str | None:
    """Which phase a date falls in — FR-TIME-07.

    `phases` is (phase_id, starts_on, ends_on). Where windows overlap, the
    earliest-starting one wins, which keeps the answer stable rather than
    dependent on row order.
    """
    matches = [(pid, s) for pid, s, e in phases if s <= day <= e]
    if not matches:
        return None
    return min(matches, key=lambda m: m[1])[0]


def _hours(value: Decimal) -> str:
    normalised = value.normalize()
    text = format(normalised, "f")
    return f"{text} hour" if normalised == 1 else f"{text} hours"
