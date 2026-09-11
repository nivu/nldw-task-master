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

from app.domain import financials as fin
from app.domain import timesheets as rules
from app.domain.calendar import is_weekend, today_in_company_tz
from app.services import pnl as pnl_service
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
                        "project_name": projects.get(pid, "—")
                        if pid
                        else "Activities (no project)",
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


# ---------------------------------------------------------------------------
# Financials — spec 003 §4. MANAGER AND ADMIN ONLY.
#
# Nothing in this section may be reached from a route guarded by less than
# ManagerDep. The arithmetic lives in app.domain.financials; this assembles the
# inputs, and the one thing it must get right is WHICH rate it hands over: the
# snapshot captured on each entry, never the person's current rate.
# ---------------------------------------------------------------------------


def _efforts_for(entries: list[dict], rates: pnl_service.RateBook) -> list[fin.Effort]:
    """Time entries → efforts priced at the CTC in force on each entry's date.

    Spec 005 §3.1 replaced 003's captured snapshot with a dated CTC history:
    the rate is looked up from the period covering the entry's date, so a
    raise next month prices next month's hours and nothing before them.
    Activity rows (no project) never reach here.
    """
    out = []
    for e in entries:
        if not e.get("project_id"):
            continue
        hours = Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"]))
        out.append(
            fin.Effort(
                user_id=e["user_id"],
                hours=hours,
                rate=rates.hourly(e["user_id"], date.fromisoformat(e["date"])),
            )
        )
    return out


def _rates_for(entries: list[dict]) -> pnl_service.RateBook:
    days = [date.fromisoformat(e["date"]) for e in entries] or [today_in_company_tz()]
    return pnl_service.rate_book(min(days), max(days))


def _currency() -> str:
    from app.services import settings_store

    return str(settings_store.get("currency_code", "INR"))


def project_financials(project_id: str) -> dict[str, Any]:
    """Revenue, COGS, margin and per-person attribution — FR-FIN-04/06."""
    project = db.get_project(project_id)
    if project is None:
        return {}

    entries = db.list_time_entries(project_id=project_id)
    people = {p["id"]: p["display_name"] for p in db.list_profiles()}
    revenue = Decimal(str(project["revenue"])) if project.get("revenue") is not None else None
    result = fin.project_financials(revenue, _efforts_for(entries, _rates_for(entries)))

    return {
        "project_id": project_id,
        "currency": _currency(),
        "revenue": _m(result.revenue),
        "total_hours": str(result.total_hours),
        "cogs": _m(result.cogs),
        "cogs_partial": _m(result.cogs_partial),
        "margin": _m(result.margin),
        "margin_pct": _m(result.margin_pct),
        "complete": result.complete,
        # FR-FIN-06 — named, so an incomplete figure says exactly who to rate.
        "unrated": [
            {"user_id": uid, "display_name": people.get(uid, "—")}
            for uid in result.unrated_user_ids
        ],
        # Sorted by name, never by money (spec 003 §9).
        "people": sorted(
            (
                {
                    "user_id": line.user_id,
                    "display_name": people.get(line.user_id, "—"),
                    "hours": str(line.hours),
                    "cost_rate": _m(fin.money(line.rate)) if line.rate is not None else None,
                    "cogs": _m(line.cogs),
                    "attributed_revenue": _m(line.attributed_revenue),
                }
                for line in result.people
            ),
            key=lambda r: r["display_name"],
        ),
    }


def people_financials() -> dict[str, Any]:
    """Each person across every project — FR-FIN-05.

    Deliberately NOT sortable by any money column from here: the API returns
    rows by name and the UI keeps that order. Spec 003 §9.
    """
    projects = db.list_projects(include_archived=True)
    people = {p["id"]: p for p in db.list_profiles()}
    all_entries = [e for e in db.list_time_entries() if e.get("project_id")]
    rates = _rates_for(all_entries)
    today = today_in_company_tz()

    per_project = []
    for project in projects:
        entries = [e for e in all_entries if e["project_id"] == project["id"]]
        revenue = Decimal(str(project["revenue"])) if project.get("revenue") is not None else None
        per_project.append(fin.project_financials(revenue, _efforts_for(entries, rates)))

    totals = fin.person_totals(per_project)
    rows = [
        {
            "user_id": t.user_id,
            "display_name": people.get(t.user_id, {}).get("display_name", "—"),
            # Spec 005 — the CTC in force today, monthly. Never called salary.
            "current_monthly_ctc": _m(rates.monthly_now(t.user_id, today)),
            "hours": str(t.hours),
            "cogs": _m(t.cogs),
            "attributed_revenue": _m(t.attributed_revenue),
            "projects": t.projects,
            "complete": t.complete,
        }
        for t in totals
    ]
    return {
        "currency": _currency(),
        "people": sorted(rows, key=lambda r: r["display_name"]),
        # Active people with no CTC in force today — "who is unrated" from
        # one screen. Unrated on a PAST date shows per project instead.
        "unrated": sorted(
            (
                {"user_id": pid, "display_name": p["display_name"]}
                for pid, p in people.items()
                if p.get("is_active") and rates.period_on(pid, today) is None
            ),
            key=lambda r: r["display_name"],
        ),
    }


def _m(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Resource timeline — spec 003 FR-RES. Manager and admin.
# ---------------------------------------------------------------------------


def resources_timeline(start: date, end: date) -> dict[str, Any]:
    """Per person, per week: allocated %, leave, and whether they are over.

    Weeks rather than days because that is how allocation conversations
    happen — "Sriram is on Acme until the end of the month" — and a day grid
    over a quarter is unreadable. Over-allocation is still computed daily
    (FR-ALLOC-04) and the week reports its peak.
    """
    people = [p for p in db.list_profiles(active_only=True)]
    allocations = db.list_allocations()
    projects = {p["id"]: p["name"] for p in db.list_projects(include_archived=True)}
    holidays = holidays_between(start, end)
    leave = leave_days_for([p["id"] for p in people], start, end)

    # Monday-aligned week starts covering the range.
    first_monday = start - timedelta(days=start.weekday())
    weeks: list[date] = []
    cursor = first_monday
    while cursor <= end:
        weeks.append(cursor)
        cursor += timedelta(days=7)

    rows = []
    for person in people:
        mine = [a for a in allocations if a["user_id"] == person["id"]]
        my_leave = leave.get(person["id"], {})
        cells = []
        for monday in weeks:
            sunday = monday + timedelta(days=6)
            segments: dict[str, Decimal] = {}
            peak = ZERO
            working = 0
            leave_days = ZERO
            day = monday
            while day <= sunday:
                if not is_weekend(day) and day not in holidays:
                    working += 1
                    leave_days += min(my_leave.get(day, ZERO), Decimal("1"))
                    total = ZERO
                    for a in mine:
                        if (
                            date.fromisoformat(a["starts_on"])
                            <= day
                            <= date.fromisoformat(a["ends_on"])
                        ):
                            pct = Decimal(str(a["percent"]))
                            total += pct
                            segments[a["project_id"]] = max(
                                segments.get(a["project_id"], ZERO), pct
                            )
                    peak = max(peak, total)
                day += timedelta(days=1)
            cells.append(
                {
                    "week_start": monday.isoformat(),
                    "allocated_pct": str(peak),
                    "over": peak > Decimal("100"),
                    "leave_days": str(leave_days),
                    "working_days": working,
                    "projects": [
                        {
                            "project_id": pid,
                            "project_name": projects.get(pid, "—"),
                            "percent": str(pct),
                        }
                        for pid, pct in sorted(
                            segments.items(), key=lambda kv: projects.get(kv[0], "")
                        )
                    ],
                }
            )
        rows.append(
            {
                "user_id": person["id"],
                "display_name": person["display_name"],
                "role": person["role"],
                "weeks": cells,
            }
        )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "weeks": [w.isoformat() for w in weeks],
        "people": sorted(rows, key=lambda r: r["display_name"]),
    }
