"""Effort analytics and forecasting — spec 002 §5.4.

The numbers here get quoted in budget conversations, so two properties matter
more than any feature:

* **FR-ANALYTICS-05.** Days nobody logged are reported as *missing*, never as
  zero. A total computed over a partly-filled timesheet is not imprecise, it is
  biased low — and it will be read as "this project used few hours" rather than
  "we do not know".

* **NFR-04.** Every figure decomposes to the entries behind it. A total nobody
  can take apart will not be believed, and should not be.

Spec 002 §10 also governs what is *not* computed here: no per-person ranking,
no efficiency metric, no comparison of individuals. `001` §10 committed that
anything in this territory ships as "a small cultural shift so the team knows
who is working on what — explicitly not a credibility or accountability
system", and a timesheet is where that stops being decorative.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain import timesheets as rules
from app.domain.calendar import is_weekend, today_in_company_tz
from app.services import supabase as db
from app.services.timesheets import holidays_between, leave_days_for

ZERO = Decimal("0.00")


def project_effort(project_id: str) -> dict[str, Any]:
    """Effort against one project, split by phase — FR-ANALYTICS-02/03.

    This is the budget conversation, so the phase split is not decoration:
    without it, "400 hours" cannot distinguish a delivery that overran from one
    that has sat in unbudgeted support for a year (spec 002 §3.2).
    """
    project = db.get_project(project_id)
    if project is None:
        return {}

    phases = db.list_phases(project_id)
    entries = db.list_time_entries(project_id=project_id)
    people = {p["id"]: p["display_name"] for p in db.list_profiles()}

    by_phase: dict[str | None, list[dict]] = {}
    for entry in entries:
        by_phase.setdefault(entry.get("phase_id"), []).append(entry)

    phase_rows = []
    for phase in phases:
        rows = by_phase.get(phase["id"], [])
        office, home = _split(rows)
        budget = Decimal(str(phase["budget_hours"])) if phase["budget_hours"] is not None else None
        logged = office + home
        phase_rows.append(
            {
                "id": phase["id"],
                "phase": phase["phase"],
                "label": rules.PHASE_LABELS[phase["phase"]],
                "starts_on": phase["starts_on"],
                "ends_on": phase["ends_on"],
                "budget_hours": str(budget) if budget is not None else None,
                "logged_hours": str(logged),
                "hours_office": str(office),
                "hours_home": str(home),
                "over_by": str(logged - budget) if budget is not None and logged > budget else None,
                "people": _by_person(rows, people),
            }
        )

    # Effort logged outside every phase window, and effort against a project
    # somebody was not allocated to (Q-07). Both are real work and both are
    # invisible unless named — which is the overrun the analytics exist to show.
    unphased = by_phase.get(None, [])
    office, home = _split(unphased)

    total_office, total_home = _split(entries)
    total_budget = sum(
        (Decimal(str(p["budget_hours"])) for p in phases if p["budget_hours"] is not None),
        ZERO,
    )

    return {
        "project": {
            "id": project["id"],
            "name": project["name"],
            "client": project.get("client"),
            "is_archived": project["is_archived"],
        },
        "phases": phase_rows,
        "outside_any_phase": {
            "logged_hours": str(office + home),
            "hours_office": str(office),
            "hours_home": str(home),
            "people": _by_person(unphased, people),
        },
        "total": {
            "budget_hours": str(total_budget) if total_budget else None,
            "logged_hours": str(total_office + total_home),
            "hours_office": str(total_office),
            "hours_home": str(total_home),
        },
    }


def coverage(user_ids: list[str], start: date, end: date) -> dict[str, Any]:
    """Which working days have no timesheet entry at all — FR-ANALYTICS-05.

    The most important number on any analytics page, and the least interesting
    to look at. Every effort total elsewhere is only as trustworthy as this.

    A day is only "missing" if it was a working day for that person: weekends,
    declared holidays and full days of approved leave are not gaps.
    """
    holidays = holidays_between(start, end)
    leave = leave_days_for(user_ids, start, end)
    entries = db.list_time_entries(user_ids=user_ids, start=start, end=end)
    people = {p["id"]: p["display_name"] for p in db.list_profiles()}

    logged: dict[str, set[date]] = {}
    for entry in entries:
        logged.setdefault(entry["user_id"], set()).add(date.fromisoformat(entry["date"]))

    today = today_in_company_tz()
    rows = []
    for user_id in user_ids:
        mine_leave = leave.get(user_id, {})
        expected: list[date] = []
        day = start
        while day <= min(end, today):
            full_leave = mine_leave.get(day, ZERO) >= Decimal("1")
            if not is_weekend(day) and day not in holidays and not full_leave:
                expected.append(day)
            day += timedelta(days=1)

        have = logged.get(user_id, set())
        missing = [d for d in expected if d not in have]
        rows.append(
            {
                "user_id": user_id,
                "display_name": people.get(user_id, "—"),
                "expected_days": len(expected),
                "logged_days": len(expected) - len(missing),
                "missing_days": [d.isoformat() for d in missing],
            }
        )

    total_expected = sum(r["expected_days"] for r in rows)
    total_logged = sum(r["logged_days"] for r in rows)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "people": rows,
        "expected_days": total_expected,
        "logged_days": total_logged,
        # Deliberately not rounded away: 0.83 and 1.0 mean very different
        # things about whether the effort totals can be relied on.
        "coverage": str((Decimal(total_logged) / Decimal(total_expected)).quantize(Decimal("0.01")))
        if total_expected
        else None,
    }


def forecast(start: date, end: date) -> dict[str, Any]:
    """Capacity the current allocations imply — FR-ANALYTICS-06, Q-02.

    Excludes weekends, declared holidays and approved leave. A forecast over
    raw calendar days tells you a team of three has sixty days next month while
    two of them are away for a fortnight, and the plan built on it is wrong
    before anybody starts.
    """
    allocations = db.list_allocations()
    if not allocations:
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "projects": [],
            "over_allocated": [],
        }

    people = {p["id"]: p["display_name"] for p in db.list_profiles()}
    projects = {p["id"]: p for p in db.list_projects(include_archived=True)}
    holidays = holidays_between(start, end)
    user_ids = sorted({a["user_id"] for a in allocations})
    leave = leave_days_for(user_ids, start, end)

    per_project: dict[str, dict[str, Any]] = {}
    typed: list[rules.Allocation] = []

    for allocation in allocations:
        a_start = date.fromisoformat(allocation["starts_on"])
        a_end = date.fromisoformat(allocation["ends_on"])
        typed.append(
            rules.Allocation(
                user_id=allocation["user_id"],
                project_id=allocation["project_id"],
                starts_on=a_start,
                ends_on=a_end,
                percent=Decimal(str(allocation["percent"])),
            )
        )

        window = rules.overlap(a_start, a_end, start, end)
        if window is None:
            continue

        hours = rules.allocated_hours(
            Decimal(str(allocation["percent"])),
            window[0],
            window[1],
            holidays=holidays,
            leave_days=leave.get(allocation["user_id"], {}),
        )
        bucket = per_project.setdefault(
            allocation["project_id"],
            {
                "project_id": allocation["project_id"],
                "project_name": projects.get(allocation["project_id"], {}).get("name", "—"),
                "capacity_hours": ZERO,
                "people": [],
            },
        )
        bucket["capacity_hours"] += hours
        bucket["people"].append(
            {
                "user_id": allocation["user_id"],
                "display_name": people.get(allocation["user_id"], "—"),
                "percent": str(allocation["percent"]),
                "hours": str(hours),
            }
        )

    flagged = rules.over_allocations(typed, start, end)
    # Collapsed to a run per person: an admin needs "Sriram is over-allocated
    # for three weeks", not sixty rows saying the same thing once per day.
    over = {}
    for user_id, day, total in flagged:
        entry = over.setdefault(
            user_id,
            {
                "user_id": user_id,
                "display_name": people.get(user_id, "—"),
                "days": 0,
                "first": day.isoformat(),
                "last": day.isoformat(),
                "peak_percent": str(total),
            },
        )
        entry["days"] += 1
        entry["last"] = day.isoformat()
        if Decimal(entry["peak_percent"]) < total:
            entry["peak_percent"] = str(total)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "projects": [
            {**bucket, "capacity_hours": str(bucket["capacity_hours"])}
            for bucket in sorted(per_project.values(), key=lambda b: b["project_name"])
        ],
        "over_allocated": sorted(over.values(), key=lambda o: o["display_name"]),
    }


def current_work(user_ids: list[str], *, days: int = 7) -> list[dict[str, Any]]:
    """What each person has been working on lately — FR-ANALYTICS-04, G-6.

    Recent entries rather than allocations, because the question is what they
    are *doing*, and an allocation is only what somebody intended.
    """
    today = today_in_company_tz()
    start = today - timedelta(days=days)
    entries = db.list_time_entries(user_ids=user_ids, start=start, end=today)
    projects = {p["id"]: p["name"] for p in db.list_projects(include_archived=True)}
    people = {p["id"]: p["display_name"] for p in db.list_profiles()}

    by_user: dict[str, dict[str, Decimal]] = {}
    latest: dict[str, str] = {}
    for entry in entries:
        hours = Decimal(str(entry["hours_office"])) + Decimal(str(entry["hours_home"]))
        by_user.setdefault(entry["user_id"], {})
        by_user[entry["user_id"]][entry["project_id"]] = (
            by_user[entry["user_id"]].get(entry["project_id"], ZERO) + hours
        )
        if entry.get("note"):
            latest[entry["user_id"]] = entry["note"]

    return [
        {
            "user_id": user_id,
            "display_name": people.get(user_id, "—"),
            "projects": sorted(
                (
                    {
                        "project_id": pid,
                        "project_name": projects.get(pid, "—"),
                        "hours": str(hours),
                    }
                    for pid, hours in (by_user.get(user_id) or {}).items()
                ),
                key=lambda p: Decimal(p["hours"]),
                reverse=True,
            ),
            "total": str(sum((by_user.get(user_id) or {}).values(), ZERO)),
            "latest_note": latest.get(user_id),
        }
        for user_id in user_ids
    ]


# ---------------------------------------------------------------------------


def _split(rows: list[dict]) -> tuple[Decimal, Decimal]:
    office = sum((Decimal(str(r["hours_office"])) for r in rows), ZERO)
    home = sum((Decimal(str(r["hours_home"])) for r in rows), ZERO)
    return office, home


def _by_person(rows: list[dict], people: dict[str, str]) -> list[dict[str, Any]]:
    """Who contributed the hours, so a total can be decomposed (NFR-04).

    Sorted by name, never by hours. Spec 002 §10: this must not become a
    leaderboard, and ordering by contribution is how a table becomes one
    without anybody deciding it should.
    """
    totals: dict[str, Decimal] = {}
    for row in rows:
        hours = Decimal(str(row["hours_office"])) + Decimal(str(row["hours_home"]))
        totals[row["user_id"]] = totals.get(row["user_id"], ZERO) + hours
    return sorted(
        (
            {"user_id": uid, "display_name": people.get(uid, "—"), "hours": str(hours)}
            for uid, hours in totals.items()
        ),
        key=lambda p: p["display_name"],
    )
