"""Timesheet rules — spec 002 §5.3, and the capacity arithmetic behind §5.4.

Pure functions over plain values, like `app.domain.rules`. Nothing here reads a
database, which is what lets the two rules most likely to produce quietly wrong
numbers — the edit window and capacity — be tested exhaustively.

Hours are `Decimal` throughout. Spec 002 §8 and `001` §6.2: these sums get
quoted in budget conversations, and binary floats accumulate error across
thousands of rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.calendar import is_weekend, today_in_company_tz

ZERO = Decimal("0.00")

PHASES = ("pre", "delivery", "support", "spillover")
PHASE_LABELS = {
    "pre": "Pre-project",
    "delivery": "Delivery",
    "support": "Post-delivery support",
    "spillover": "Spill-over",
}

# Spec 002 FR-PROJ-06 — what kind of work a project is. Order is report order.
PROJECT_CATEGORIES = ("client", "poc", "product", "internal")
PROJECT_CATEGORY_LABELS = {
    "client": "Paid client engagement",
    "poc": "Client POC / general",
    "product": "Nunnari product development",
    "internal": "Internal tools / applications / website",
}


def is_tentative(project: dict) -> bool:
    """Spec 002 FR-PROJ-08 — pipeline: planned, not yet won. A row without a
    status predates migration 020 and is confirmed."""
    return project.get("status", "confirmed") == "tentative"


#: Q-06 default. A sanity check against a mistyped 80, not a position on
#: overwork. Overridable via `app_settings.max_hours_per_day`.
DEFAULT_MAX_HOURS_PER_DAY = Decimal("16")

#: Q-01 default. Days after the END of an entry's week during which it stays
#: editable. Overridable via `app_settings.timesheet_grace_days`.
DEFAULT_GRACE_DAYS = 7

#: A full-time working day, used to turn an allocation percentage into hours
#: for anyone not contracted for fewer (spec 005 FR-CTC-06).
HOURS_PER_WORKING_DAY = Decimal("8")

#: Spec 003 FR-ACT-02 — time that is real work but belongs to no project. A
#: fixed set, on purpose: a free-text activity becomes a second, unmanaged
#: project list within a month.
ACTIVITIES = ("learning", "internal", "admin", "other")
ACTIVITY_LABELS = {
    "learning": "Learning",
    "internal": "Internal work",
    "admin": "Admin",
    "other": "Other",
}


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


def week_closed(week_start: date, today: date, *, grace_days: int = DEFAULT_GRACE_DAYS) -> bool:
    """Has the edit window closed for the whole week starting `week_start`?

    `006` FR-SIGN: a week can be reopened only while this is False, and is
    auto-confirmed once it is True. Its Sunday is the last day to lock, so the
    week stays open until that day's window closes.
    """
    return is_locked(week_end(week_start), today, grace_days=grace_days)


# ---------------------------------------------------------------------------
# Who is expected to log, and on which days — FR-ANALYTICS-07
# ---------------------------------------------------------------------------


def logs_time(profile: dict) -> bool:
    """FR-ANALYTICS-07 — is this person expected to keep a timesheet at all?

    Absent counts as yes, so a row read before migration 015 still behaves as
    it did.
    """
    return profile.get("logs_time") is not False


def _as_date(value: date | str | None) -> date | None:
    if not value:
        return None
    return value if isinstance(value, date) else date.fromisoformat(value)


def employment(profile: dict) -> tuple[date | None, date | None]:
    """FR-ANALYTICS-08 — the day this person joined and the day they left.

    None = not recorded, so that end is open; a row read before migration 019
    has neither and behaves as it did.
    """
    return _as_date(profile.get("joined_on")), _as_date(profile.get("left_on"))


def employed_on(profile: dict, day: date) -> bool:
    """FR-ANALYTICS-08 — is `day` within this person's joining and leaving dates?"""
    joined_on, left_on = employment(profile)
    return (joined_on is None or day >= joined_on) and (left_on is None or day <= left_on)


def employed_in_week(profile: dict, week_start: date) -> bool:
    """FR-ANALYTICS-08 — does any day of the week starting `week_start` fall
    within this person's joining and leaving dates?"""
    joined_on, left_on = employment(profile)
    return week_expected(week_start, None, joined_on=joined_on, left_on=left_on)


def check_employment_dates(joined_on: date | None, left_on: date | None) -> str | None:
    """FR-ANALYTICS-08 — mirrors the CHECK in migration 019, as a sentence."""
    if joined_on and left_on and left_on < joined_on:
        return "Someone cannot leave before the day they joined."
    return None


def expected_log_days(
    start: date,
    end: date,
    *,
    today: date,
    holidays: set[date],
    leave_days: dict[date, Decimal] | None = None,
    portal_start: date | None = None,
    joined_on: date | None = None,
    left_on: date | None = None,
) -> list[date]:
    """The days in `start..end` someone should have logged — FR-ANALYTICS-05/07/08.

    Working days only, up to and including today: weekends, declared holidays
    and full days of leave are not gaps, and neither is any day before the
    company started using the portal (`portal_start_date`; None = no such day),
    before the person joined or after they left (None = not recorded).
    """
    leave_days = leave_days or {}
    first = max(d for d in (start, portal_start, joined_on) if d)
    last = min(d for d in (end, today, left_on) if d)
    days = []
    day = first
    while day <= last:
        full_leave = leave_days.get(day, ZERO) >= Decimal("1")
        if not is_weekend(day) and day not in holidays and not full_leave:
            days.append(day)
        day += timedelta(days=1)
    return days


def week_expected(
    week_start: date,
    portal_start: date | None,
    *,
    joined_on: date | None = None,
    left_on: date | None = None,
) -> bool:
    """`006` FR-SIGN-05 — does a week fall (at least partly) on or after the
    portal start, and within the person's joining and leaving dates
    (FR-ANALYTICS-08), so that sign-off is expected for it?"""
    last = week_end(week_start)
    return (
        (portal_start is None or last >= portal_start)
        and (joined_on is None or last >= joined_on)
        and (left_on is None or week_start <= left_on)
    )


# ---------------------------------------------------------------------------
# Logging rules — FR-TIME
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayEntry:
    """One line of a day: a project OR an activity, and how the hours split.

    Exactly one of `project_id` / `activity` is set (spec 003 FR-ACT-01). The
    database enforces the same rule with a CHECK; this mirrors it so a bad line
    is refused with a sentence rather than a constraint name.
    """

    # Field order is load-bearing: 002 code and tests construct this
    # positionally as (project_id, hours_office, hours_home). `activity` is
    # appended, and a project line passes None for it.
    project_id: str | None
    hours_office: Decimal
    hours_home: Decimal
    note: str | None = None
    activity: str | None = None

    @property
    def total(self) -> Decimal:
        return self.hours_office + self.hours_home

    @property
    def key(self) -> str:
        """What this line is *for* — the identity a day may hold once."""
        return self.project_id if self.project_id else f"activity:{self.activity}"


def check_target(project_id: str | None, activity: str | None) -> str | None:
    """FR-ACT-01/02 — a line is for a project or an activity, never both."""
    if bool(project_id) == bool(activity):
        return "Each line must be for either a project or an activity — not both, not neither."
    if activity and activity not in ACTIVITIES:
        return f"Unknown activity {activity!r}."
    return None


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


def available_hours(
    start: date,
    end: date,
    *,
    holidays: set[date],
    leave_days: dict[date, Decimal] | None = None,
    hours_on: Callable[[date], Decimal] | None = None,
) -> Decimal:
    """`working_days`, in hours: each available day weighted by the hours the
    person is contracted for that day — spec 005 FR-CTC-06.

    `hours_on` gives a day's contracted hours (2 for someone on 10 h a week);
    None means full time throughout. A part-timer counted at 8 h a day looks
    like four times the capacity they are, and the plan built on it is wrong.
    """
    leave_days = leave_days or {}
    total = ZERO
    day = start
    while day <= end:
        if not is_weekend(day) and day not in holidays:
            share = Decimal("1") - min(leave_days.get(day, ZERO), Decimal("1"))
            total += share * (HOURS_PER_WORKING_DAY if hours_on is None else hours_on(day))
        day += timedelta(days=1)
    return total


def allocated_hours(
    percent: Decimal,
    start: date,
    end: date,
    *,
    holidays: set[date],
    leave_days: dict[date, Decimal] | None = None,
    hours_on: Callable[[date], Decimal] | None = None,
) -> Decimal:
    """What an allocation implies, in hours, over a date range.

    Q-02: a percentage of *capacity*, not of the calendar — so it shrinks when
    somebody is on leave without anybody adjusting the allocation. Capacity is
    the person's contracted hours (FR-CTC-06), so 100% of a 10 h-a-week
    contractor is 10 hours a week, not 40.
    """
    hours = available_hours(start, end, holidays=holidays, leave_days=leave_days, hours_on=hours_on)
    return (hours * percent / Decimal("100")).quantize(Decimal("0.01"))


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
    # Spec 002 FR-PROJ-08 — on a tentative project: planned, not promised.
    tentative: bool = False


def over_allocations(
    allocations: list[Allocation], start: date, end: date, *, include_tentative: bool = False
) -> list[tuple[str, date, Decimal]]:
    """Days where somebody's concurrent allocations exceed 100% — FR-ALLOC-04.

    Returns (user_id, day, total_percent). Reported per day rather than per
    allocation because that is the question worth answering: "on which days is
    this person promised to more work than exists", not "which two rows
    overlap".

    Tentative allocations are left out unless `include_tentative` (FR-PROJ-08):
    work not yet won does not over-commit anybody, but a manager pencilling
    it in still wants to see who it would.
    """
    flagged: list[tuple[str, date, Decimal]] = []
    counted = [a for a in allocations if include_tentative or not a.tentative]
    users = {a.user_id for a in counted}

    for user_id in sorted(users):
        mine = [a for a in counted if a.user_id == user_id]
        day = start
        while day <= end:
            if not is_weekend(day):
                total = sum((a.percent for a in mine if a.starts_on <= day <= a.ends_on), ZERO)
                if total > Decimal("100"):
                    flagged.append((user_id, day, total))
            day += timedelta(days=1)
    return flagged


def may_allocate(*, actor_id: str, actor_is_manager: bool, person_lead_id: str | None) -> bool:
    """Whether someone may allocate or deallocate a person — spec 003
    FR-ROLE-02, FR-ROLE-08. Managers and admins: anyone. A lead: only the
    people whose `lead_id` is theirs."""
    return actor_is_manager or person_lead_id == actor_id


def project_lead_refusal(person: dict | None) -> str | None:
    """Spec 002 FR-PROJ-07 — may this person be named a project's lead?

    `person` is their profile, or None when the id matches nobody. Any role
    will do — the lead is a label, not a permission — but they must exist and
    still be active: a project led by someone who has left is led by nobody.
    """
    if person is None:
        return "That lead does not exist."
    if not person.get("is_active"):
        return f"{person.get('display_name') or 'That person'} is not active."
    return None


def archived_allocation_refusal(
    *,
    project_name: str,
    is_archived: bool,
    new: tuple[date, date],
    old: tuple[date, date] | None = None,
) -> str | None:
    """FR-PROJ-04 — an archived project takes no new planned time.

    `old` is the allocation's current (starts_on, ends_on), or None when it is
    being created. On an archived project creating is refused, and so is an
    edit that reaches outside the current dates; shortening one is allowed.
    """
    if not is_archived:
        return None
    if old is None or new[0] < old[0] or new[1] > old[1]:
        return f"{project_name} is archived."
    return None


def archived_entry_refusal(
    *, project_name: str, is_archived: bool, already_logged: bool
) -> str | None:
    """FR-PROJ-04 — an archived project takes no new time entries.

    A line already logged against it that day stays saveable, so re-saving the
    day (the whole day is submitted at once) does not fail over history.
    """
    if is_archived and not already_logged:
        return f"{project_name} is archived."
    return None


def tentative_entry_refusal(
    *, project_name: str, is_tentative: bool, already_logged: bool
) -> str | None:
    """FR-PROJ-08 — a tentative project takes no time entries: nobody works on
    work that has not been won. As with archiving, a line already logged that
    day (before the project was made tentative) stays saveable."""
    if is_tentative and not already_logged:
        return f"{project_name} is tentative."
    return None


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
