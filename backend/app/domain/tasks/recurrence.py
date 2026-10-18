"""Recurring tasks — spec 008 FR-TASK-RRULE-01 to -03, -06; T9; data-model
§2.1 (phase 3), §2.8.

A recurrence is an iCalendar RRULE body, stored as text. Only the four forms
the picker offers are accepted (FR-TASK-RRULE-01), each written one way:

- `FREQ=DAILY`
- `FREQ=WEEKLY;BYDAY=MO,TH` — one or more days, Monday first
- `FREQ=MONTHLY;BYMONTHDAY=31` — a day of the month, 1 to 31
- `FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29` — a day of the year

A day the month does not have is its last day: "monthly on the 31st" falls
on 30 April and 28 February, and "yearly on 29 February" on 28 February in a
year that is not a leap year, without drifting afterwards. The dates are
computed with `dateutil.rrule` (FR-TASK-RRULE-06).

Pure: the service passes the rule and the current due date. The writes
that close a recurring task (the task service's and the CRM's) take the next
occurrence's ops from `next_occurrence_ops`, so both create the same one.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from dateutil import rrule

from app.domain.tasks import rules

INVALID: Final = (
    "Choose how the task repeats: daily, weekly on chosen days, monthly on a day or yearly."
)
NEEDS_DUE: Final = "A recurring task needs a due date."

#: RRULE day codes, Monday first (the order a weekly rule is written in).
DAYS: Final = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_DAY_NAMES: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
#: The most days each month can have (February in a leap year).
_MONTH_DAYS: Final = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_FREQS: Final = {
    "DAILY": rrule.DAILY,
    "WEEKLY": rrule.WEEKLY,
    "MONTHLY": rrule.MONTHLY,
    "YEARLY": rrule.YEARLY,
}
#: The parts each form takes, exactly.
_PARTS: Final = {
    "DAILY": {"FREQ"},
    "WEEKLY": {"FREQ", "BYDAY"},
    "MONTHLY": {"FREQ", "BYMONTHDAY"},
    "YEARLY": {"FREQ", "BYMONTH", "BYMONTHDAY"},
}


@dataclass(frozen=True)
class Rule:
    freq: str
    days: tuple[str, ...] = ()
    month: int | None = None
    month_day: int | None = None

    @property
    def text(self) -> str:
        """The rule as stored."""
        if self.freq == "WEEKLY":
            return f"FREQ=WEEKLY;BYDAY={','.join(self.days)}"
        if self.freq == "MONTHLY":
            return f"FREQ=MONTHLY;BYMONTHDAY={self.month_day}"
        if self.freq == "YEARLY":
            return f"FREQ=YEARLY;BYMONTH={self.month};BYMONTHDAY={self.month_day}"
        return "FREQ=DAILY"


def _number(value: str, low: int, high: int) -> int:
    if not value.isdigit() or not low <= int(value) <= high:
        raise ValueError(INVALID)
    return int(value)


def parse(text: str) -> Rule:
    """FR-TASK-RRULE-01 — one of the four allowed forms, else ValueError with
    a sentence safe to show. Parts may come in any order and case."""
    parts: dict[str, str] = {}
    for piece in (text or "").strip().upper().split(";"):
        name, sep, value = piece.strip().partition("=")
        if not sep or not value or name in parts:
            raise ValueError(INVALID)
        parts[name] = value.strip()
    freq = parts.get("FREQ")
    if freq not in _PARTS or set(parts) != _PARTS[freq]:
        raise ValueError(INVALID)
    if freq == "WEEKLY":
        days = [d.strip() for d in parts["BYDAY"].split(",")]
        if not days or any(d not in DAYS for d in days) or len(set(days)) != len(days):
            raise ValueError(INVALID)
        return Rule(freq, days=tuple(d for d in DAYS if d in days))
    if freq == "MONTHLY":
        return Rule(freq, month_day=_number(parts["BYMONTHDAY"], 1, 31))
    if freq == "YEARLY":
        month = _number(parts["BYMONTH"], 1, 12)
        return Rule(
            freq, month=month, month_day=_number(parts["BYMONTHDAY"], 1, _MONTH_DAYS[month - 1])
        )
    return Rule(freq)


def normalise(text: str) -> str:
    """The rule as stored: its one written form (ValueError when not allowed)."""
    return parse(text).text


def _rule(rule: Rule, start: date) -> rrule.rrule:
    kwargs: dict = {"dtstart": datetime(start.year, start.month, start.day)}
    if rule.freq == "WEEKLY":
        kwargs["byweekday"] = [DAYS.index(d) for d in rule.days]
    if rule.month_day is not None:
        # A day past 28 is "that day, or the month's last day when it has
        # fewer": the last of 28..day the month has.
        kwargs["bymonthday"] = list(range(min(rule.month_day, 28), rule.month_day + 1))
        kwargs["bysetpos"] = -1
    if rule.month is not None:
        kwargs["bymonth"] = rule.month
    return rrule.rrule(_FREQS[rule.freq], **kwargs)


def _after(text: str, start: date, *, inc: bool) -> date:
    """The rule's first date after `start` (or on it, `inc`); ValueError
    (INVALID) when the calendar ends first."""
    rule = parse(text)
    try:
        found = _rule(rule, start).after(datetime(start.year, start.month, start.day), inc=inc)
    except OverflowError:
        found = None
    if found is None:
        raise ValueError(INVALID)
    return found.date()


def next_due(text: str, current_due: date) -> date:
    """FR-TASK-RRULE-02 — the first date of the rule after `current_due`."""
    return _after(text, current_due, inc=False)


def first_due(text: str, on_or_after: date) -> date:
    """The first date of the rule on or after `on_or_after` (a starter
    template's first occurrence)."""
    return _after(text, on_or_after, inc=True)


def check(text: str | None, due_on: Any) -> None:
    """FR-TASK-RRULE-01 — a recurring task has a due date, and one the rule
    has a next date after; ValueError with a sentence safe to show."""
    if not text:
        return
    if not due_on:
        raise ValueError(NEEDS_DUE)
    next_due(text, date.fromisoformat(str(due_on)[:10]))


#: FR-TASK-RRULE-02 — what the next occurrence copies from the one closed.
COPIED: Final = (
    "workspace_id",
    "title",
    "body",
    "priority",
    "tags",
    "assignee_id",
    "parent_id",
    "category_id",
    "source",
    "source_ref",
    "recurrence",
    "series_id",
    "assigned_by",
    "accepted_at",
)


def next_occurrence_ops(
    before: dict[str, Any], after: dict[str, Any], links: list[dict[str, Any]], today: date
) -> list[dict[str, Any]]:
    """FR-TASK-RRULE-02, -03; TP-18 — the `task_apply` ops that insert the
    next occurrence of a recurring task a write completes or cancels
    (`before` as stored, `after` as written), with its `links`; none when the
    task does not recur, is not being closed, or has already created its
    next one. The next occurrence has the same fields and links, the next due
    date after this one, the bucket of FR-TASK-DATA-08 and status Open; it
    keeps the series' creator, so who sees it is unchanged. ValueError
    (INVALID) when the rule has no next date."""
    if (
        before.get("status") not in rules.OPEN
        or after.get("status") not in ("done", "cancelled")
        or not after.get("recurrence")
        or before.get("recurrence_spawned")
    ):
        return []
    due = next_due(after["recurrence"], date.fromisoformat(str(after["due_on"])[:10]))
    values: dict[str, Any] = {name: after.get(name) for name in COPIED}
    if values["workspace_id"] is None:
        del values["workspace_id"]
    if values["source"] == "import":
        # A Life OS key names one imported row (FR-TASK-DATA-12).
        values["source_ref"] = None
    if before.get("created_by"):
        values["created_by"] = before["created_by"]
    values.update(status="open", due_on=due.isoformat(), bucket=rules.default_bucket(due, today))
    ops: list[dict[str, Any]] = [
        {"op": "insert", "table": "tasks", "ref": "next", "values": values}
    ]
    for lk in links:
        link = {
            "task_id": "$ref:next",
            "record_type": lk["record_type"],
            "record_id": lk["record_id"],
        }
        if lk["record_type"] == "tva_project":
            link["project_id"] = lk["record_id"]
        else:
            link["workspace_id"] = lk["workspace_id"]
        ops.append({"op": "insert", "table": "task_links", "values": link})
    return ops


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def label(text: str) -> str:
    """The rule in words, as the picker says it."""
    rule = parse(text)
    if rule.freq == "WEEKLY":
        names = [_DAY_NAMES[DAYS.index(d)] for d in rule.days]
        joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        return f"Every week on {joined}"
    if rule.freq == "MONTHLY":
        return f"Every month on the {_ordinal(rule.month_day or 1)}"
    if rule.freq == "YEARLY":
        return f"Every year on {rule.month_day} {calendar.month_name[rule.month or 1]}"
    return "Every day"
