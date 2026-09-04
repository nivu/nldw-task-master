"""Timesheet and project operations — spec 002.

Where the pure rules in `app.domain.timesheets` meet stored rows. Validation
happens here before any write, and every refusal carries a message meant for
the person who tripped it.

Reads leave and holidays from `001`'s tables rather than duplicating them.
Capacity without leave is a number that looks right and is not (spec 002 §6).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain import timesheets as rules
from app.domain.calendar import today_in_company_tz
from app.domain.rules import CONSUMING_STATES
from app.services import audit, settings_store
from app.services import supabase as db

logger = logging.getLogger("nldw-task-master")

ZERO = Decimal("0.00")


class TimesheetRefused(Exception):
    """A rule said no. The message is safe to show the person."""

    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _grace_days() -> int:
    return int(settings_store.get("timesheet_grace_days", rules.DEFAULT_GRACE_DAYS))


def _max_hours() -> Decimal:
    return Decimal(str(settings_store.get("max_hours_per_day", "16")))


# ---------------------------------------------------------------------------
# Leave and holidays — read from 001, never duplicated
# ---------------------------------------------------------------------------


def leave_days_for(user_ids: list[str], start: date, end: date) -> dict[str, dict[date, Decimal]]:
    """Approved and pending leave per person per day, as fractions of a day.

    Only consuming states count (`001` §6.4). A withdrawn or rejected booking
    never removed any capacity, so counting it would understate the team.
    """
    rows = db.list_bookings(
        user_ids=user_ids,
        start=start,
        end=end,
        statuses=sorted(CONSUMING_STATES),
        columns="user_id,date,duration,category,status",
    )
    out: dict[str, dict[date, Decimal]] = {}
    for row in rows:
        out.setdefault(row["user_id"], {})[date.fromisoformat(row["date"])] = Decimal(
            str(row["duration"])
        )
    return out


def holidays_between(start: date, end: date) -> set[date]:
    return {date.fromisoformat(h["date"]) for h in db.list_holidays(start, end)}


# ---------------------------------------------------------------------------
# Logging time — FR-TIME
# ---------------------------------------------------------------------------


def day_for(user_id: str, day: date) -> dict[str, Any]:
    """Everything the "log today" screen needs, in one call.

    NFR-02: the form pre-fills from current allocations, so the common case is
    adjusting numbers rather than hunting for projects in a list.
    """
    today = today_in_company_tz()
    grace = _grace_days()

    entries = db.list_time_entries(user_ids=[user_id], start=day, end=day)
    allocations = [
        a
        for a in db.list_allocations(user_ids=[user_id])
        if date.fromisoformat(a["starts_on"]) <= day <= date.fromisoformat(a["ends_on"])
    ]
    projects = {p["id"]: p for p in db.list_projects(include_archived=True)}

    # Projects to offer: the ones allocated for this date, plus any already
    # logged (so an entry made before an allocation ended stays editable).
    offered_ids = {a["project_id"] for a in allocations} | {e["project_id"] for e in entries}

    booking = db.find_booking_on(user_id, day, sorted(CONSUMING_STATES))

    return {
        "date": day.isoformat(),
        "today": today.isoformat(),
        "locked": rules.is_locked(day, today, grace_days=grace),
        "locks_on": rules.entry_locks_on(day, grace_days=grace).isoformat(),
        "can_log": rules.check_can_log(day, today=today, grace_days=grace) is None,
        "refusal": rules.check_can_log(day, today=today, grace_days=grace),
        # Q-03 / FR-TIME-10 — a warning, never a refusal.
        "leave_warning": rules.leave_warning(
            booking["category"] if booking else None,
            str(booking["duration"]) if booking else None,
        ),
        "max_hours": str(_max_hours()),
        "entries": [_present_entry(e, projects) for e in entries],
        "projects": [
            {
                "id": pid,
                "name": projects[pid]["name"],
                "client": projects[pid].get("client"),
                "allocated": pid in {a["project_id"] for a in allocations},
            }
            for pid in sorted(
                offered_ids, key=lambda i: projects[i]["name"] if i in projects else ""
            )
            if pid in projects
        ],
        "total": str(
            sum(
                (Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"])) for e in entries),
                ZERO,
            )
        ),
    }


def save_day(
    *, user_id: str, day: date, lines: list[dict[str, Any]], actor_id: str
) -> dict[str, Any]:
    """Replace a day's entries with what was submitted.

    Whole-day rather than per-line because FR-TIME-05 is a limit on the *day*,
    and a per-line endpoint cannot enforce it without the caller sending the
    day anyway. It also makes removing a line the same operation as changing
    one, which is how the screen behaves.
    """
    today = today_in_company_tz()
    grace = _grace_days()

    refusal = rules.check_can_log(day, today=today, grace_days=grace)
    if refusal:
        raise TimesheetRefused(refusal, status=409)

    parsed: list[rules.DayEntry] = []
    for line in lines:
        office = Decimal(str(line.get("hours_office") or 0))
        home = Decimal(str(line.get("hours_home") or 0))
        problem = rules.check_hours(office, home)
        if problem:
            raise TimesheetRefused(problem)
        parsed.append(
            rules.DayEntry(
                project_id=str(line["project_id"]),
                hours_office=office,
                hours_home=home,
                note=(line.get("note") or "").strip() or None,
            )
        )

    if len({e.project_id for e in parsed}) != len(parsed):
        raise TimesheetRefused("The same project appears twice. Combine those lines.")

    problem = rules.check_day_total(rules.day_total(parsed), max_hours=_max_hours())
    if problem:
        raise TimesheetRefused(problem)

    known = {p["id"] for p in db.list_projects(include_archived=True)}
    for entry in parsed:
        if entry.project_id not in known:
            raise TimesheetRefused("That project does not exist.", status=404)

    # Q-07: logging against a project you are not allocated to is allowed. The
    # person who helped out for an afternoon is exactly the effort a budget
    # conversation misses, and refusing it pushes that work into nothing.

    phases_by_project = _phases_by_project()
    existing = {
        e["project_id"]: e for e in db.list_time_entries(user_ids=[user_id], start=day, end=day)
    }

    saved = []
    for entry in parsed:
        saved.append(
            db.upsert_time_entry(
                {
                    "user_id": user_id,
                    "date": day.isoformat(),
                    "project_id": entry.project_id,
                    "phase_id": rules.phase_for(day, phases_by_project.get(entry.project_id, [])),
                    "hours_office": str(entry.hours_office),
                    "hours_home": str(entry.hours_home),
                    "note": entry.note,
                }
            )
        )

    # Lines the person removed from the day.
    submitted = {e.project_id for e in parsed}
    for project_id, row in existing.items():
        if project_id not in submitted:
            db.delete_time_entry(row["id"])

    audit.record(
        action="timesheet.saved",
        target_table="time_entries",
        target_id=f"{user_id}:{day.isoformat()}",
        actor_id=actor_id,
        after={
            "date": day.isoformat(),
            "lines": len(parsed),
            "total_hours": str(rules.day_total(parsed)),
        },
    )
    return {"date": day.isoformat(), "entries": len(saved), "total": str(rules.day_total(parsed))}


def week_for(user_id: str, monday: date) -> dict[str, Any]:
    """Seven days of one person's timesheet — the review screen."""
    sunday = monday + timedelta(days=6)
    today = today_in_company_tz()
    grace = _grace_days()

    entries = db.list_time_entries(user_ids=[user_id], start=monday, end=sunday)
    projects = {p["id"]: p for p in db.list_projects(include_archived=True)}
    holidays = holidays_between(monday, sunday)
    leave = leave_days_for([user_id], monday, sunday).get(user_id, {})

    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        on_day = [e for e in entries if e["date"] == day.isoformat()]
        days.append(
            {
                "date": day.isoformat(),
                "is_today": day == today,
                "locked": rules.is_locked(day, today, grace_days=grace),
                "holiday": day in holidays,
                "on_leave": str(leave.get(day)) if day in leave else None,
                "entries": [_present_entry(e, projects) for e in on_day],
                "total": str(
                    sum(
                        (
                            Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"]))
                            for e in on_day
                        ),
                        ZERO,
                    )
                ),
            }
        )

    return {
        "week_start": monday.isoformat(),
        "days": days,
        "total": str(
            sum(
                (Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"])) for e in entries),
                ZERO,
            )
        ),
    }


# ---------------------------------------------------------------------------


def _phases_by_project() -> dict[str, list[tuple[str, date, date]]]:
    out: dict[str, list[tuple[str, date, date]]] = {}
    for phase in db.list_phases():
        out.setdefault(phase["project_id"], []).append(
            (
                phase["id"],
                date.fromisoformat(phase["starts_on"]),
                date.fromisoformat(phase["ends_on"]),
            )
        )
    return out


def _present_entry(row: dict[str, Any], projects: dict[str, dict]) -> dict[str, Any]:
    project = projects.get(row["project_id"], {})
    office = Decimal(str(row["hours_office"]))
    home = Decimal(str(row["hours_home"]))
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "project_name": project.get("name", "—"),
        "phase_id": row.get("phase_id"),
        "hours_office": str(office),
        "hours_home": str(home),
        "total": str(office + home),
        "note": row.get("note"),
    }
