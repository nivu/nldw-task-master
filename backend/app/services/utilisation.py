"""Utilisation, bench and the hiring signal — spec 006 FR-UTIL, FR-HIRE."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain import pnl
from app.domain import timesheets as rules
from app.domain import utilisation as calc
from app.domain.calendar import is_weekend, today_in_company_tz
from app.services import settings_store
from app.services import supabase as db
from app.services.holidays import holidays_by_person
from app.services.timesheets import leave_days_for

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def target() -> Decimal:
    return Decimal(str(settings_store.get("utilisation_target", 80)))


def bench_threshold() -> Decimal:
    return Decimal(str(settings_store.get("bench_threshold", 60)))


def monthly(user_ids: list[str], start: str, end: str) -> dict[str, Any]:
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    months = pnl.months_between(date(sy, sm, 1), date(ey, em, 1))[:24]
    first = date(months[0][0], months[0][1], 1)
    last = pnl.month_bounds(*months[-1])[1]

    people = sorted(
        (p for p in db.list_profiles(active_only=True) if p["id"] in set(user_ids)),
        key=lambda p: p["display_name"],
    )
    projects = {p["id"]: p for p in db.list_projects(include_archived=True)}
    entries = db.list_time_entries(user_ids=user_ids, start=first, end=last)
    leave = leave_days_for(user_ids, first, last)
    holidays = holidays_by_person(user_ids, first, last)
    goal = target()

    rows = []
    for person in people:
        uid = person["id"]
        cells = []
        for year, month in months:
            m_first, m_last = pnl.month_bounds(year, month)
            billable = internal = activity = ZERO
            for e in entries:
                if e["user_id"] != uid or not (
                    m_first.isoformat() <= e["date"] <= m_last.isoformat()
                ):
                    continue
                hours = Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"]))
                if not e.get("project_id"):
                    activity += hours
                elif projects.get(e["project_id"], {}).get("client"):
                    billable += hours
                else:
                    internal += hours
            capacity = calc.capacity_hours(
                rules.working_days(
                    m_first,
                    m_last,
                    holidays=holidays.get(uid, set()),
                    leave_days=leave.get(uid, {}),
                )
            )
            cells.append(
                {
                    "period": f"{year:04d}-{month:02d}",
                    **calc.MonthUtilisation(billable, internal, activity, capacity).as_dict(goal),
                }
            )
        rows.append({"user_id": uid, "display_name": person["display_name"], "months": cells})

    return {
        "start": f"{months[0][0]:04d}-{months[0][1]:02d}",
        "end": f"{months[-1][0]:04d}-{months[-1][1]:02d}",
        "target_pct": str(goal),
        "people": rows,
    }


def bench(weeks: int = 8) -> dict[str, Any]:
    today = today_in_company_tz()
    monday = today - timedelta(days=today.weekday())
    starts = [monday + timedelta(weeks=n) for n in range(weeks)]
    people = sorted(db.list_profiles(active_only=True), key=lambda p: p["display_name"])
    allocations = db.list_allocations()
    threshold = bench_threshold()

    rows = []
    for person in people:
        mine = [a for a in allocations if a["user_id"] == person["id"]]
        cells = []
        for start in starts:
            end = start + timedelta(days=6)
            peak = ZERO
            day = start
            while day <= end:
                if not is_weekend(day):
                    total = sum(
                        (
                            Decimal(str(a["percent"]))
                            for a in mine
                            if date.fromisoformat(a["starts_on"])
                            <= day
                            <= date.fromisoformat(a["ends_on"])
                        ),
                        ZERO,
                    )
                    peak = max(peak, total)
                day += timedelta(days=1)
            cells.append(
                {
                    "week_start": start.isoformat(),
                    "allocated_pct": str(peak),
                    "bench": peak < threshold,
                }
            )
        rows.append(
            {
                "user_id": person["id"],
                "display_name": person["display_name"],
                "weeks": cells,
                "bench_weeks": sum(1 for c in cells if c["bench"]),
            }
        )
    return {
        "weeks": [s.isoformat() for s in starts],
        "threshold_pct": str(threshold),
        "people": rows,
    }


def hiring(months: int, annual_ctc: Decimal, start: str | None = None) -> dict[str, Any]:
    today = today_in_company_tz()
    this = today.replace(day=1)
    if start:
        sy, sm = (int(x) for x in start.split("-"))
        this = date(sy, sm, 1)
    periods = []
    cursor = this
    for _ in range(max(1, min(months, 24))):
        periods.append((cursor.year, cursor.month))
        cursor = pnl.month_bounds(cursor.year, cursor.month)[1] + timedelta(days=1)
    first = date(periods[0][0], periods[0][1], 1)
    last = pnl.month_bounds(*periods[-1])[1]

    people = db.list_profiles(active_only=True)
    user_ids = [p["id"] for p in people]
    allocations = db.list_allocations()
    leave = leave_days_for(user_ids, first, last)
    holidays = holidays_by_person(user_ids, first, last)
    goal = target() / HUNDRED

    out = []
    for year, month in periods:
        m_first, m_last = pnl.month_bounds(year, month)
        supply = ZERO
        per_person = []
        for person in people:
            wd = rules.working_days(
                m_first,
                m_last,
                holidays=holidays.get(person["id"], set()),
                leave_days=leave.get(person["id"], {}),
            )
            hours = calc.capacity_hours(wd)
            per_person.append(hours)
            supply += hours * goal
        demand = ZERO
        for a in allocations:
            window = pnl.overlap(
                date.fromisoformat(a["starts_on"]),
                date.fromisoformat(a["ends_on"]),
                m_first,
                m_last,
            )
            if window is None:
                continue
            wd = rules.working_days(
                window[0], window[1], holidays=holidays.get(a["user_id"], set())
            )
            demand += calc.capacity_hours(wd) * Decimal(str(a["percent"])) / HUNDRED
        typical = (
            (sum(per_person, ZERO) / Decimal(len(per_person)))
            if per_person
            else calc.capacity_hours(Decimal("21"))
        )
        out.append(
            calc.HiringMonth(
                period=f"{year:04d}-{month:02d}",
                demand_hours=demand,
                supply_hours=supply,
                hours_per_fte=typical * goal,
            ).as_dict(annual_ctc)
        )
    return {"target_pct": str(target()), "annual_ctc": str(annual_ctc), "months": out}
