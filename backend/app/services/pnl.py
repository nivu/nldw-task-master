"""Monthly profit and the allocation timeline — spec 005.

Gathers periods, phases, hours and allocations and hands the arithmetic to
`domain/pnl.py`. Everything here is manager/admin-only by the routes that
call it; nothing here decides who may see it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain import pnl
from app.domain.calendar import is_weekend, today_in_company_tz
from app.services import supabase as db
from app.services.timesheets import holidays_between

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _currency() -> str:
    from app.services import settings_store

    return str(settings_store.get("currency_code", "INR"))


def periods_by_user(rows: list[dict]) -> dict[str, list[pnl.Period]]:
    out: dict[str, list[pnl.Period]] = defaultdict(list)
    for row in rows:
        out[row["user_id"]].append(
            pnl.Period(
                user_id=row["user_id"],
                annual_ctc=Decimal(str(row["annual_ctc"])),
                starts_on=date.fromisoformat(row["starts_on"]),
                ends_on=date.fromisoformat(row["ends_on"]) if row.get("ends_on") else None,
            )
        )
    return out


class RateBook:
    """The cost of a person on a given day — spec 005 §3.1.

    Answers from the CTC period covering the day and the working days of that
    day's month. Built once per request; the month calendar is memoised.
    """

    def __init__(self, periods: dict[str, list[pnl.Period]], holidays: set[date]):
        self._periods = periods
        self._holidays = holidays
        self._month_days: dict[tuple[int, int], int] = {}

    def month_working_days(self, year: int, month: int) -> int:
        key = (year, month)
        if key not in self._month_days:
            first, last = pnl.month_bounds(year, month)
            self._month_days[key] = pnl.working_days(first, last, self._holidays)
        return self._month_days[key]

    def period_on(self, user_id: str, day: date) -> pnl.Period | None:
        return pnl.period_on(self._periods.get(user_id, []), day)

    def hourly(self, user_id: str, day: date) -> Decimal | None:
        period = self.period_on(user_id, day)
        if period is None:
            return None
        return pnl.hourly_cost(period, self.month_working_days(day.year, day.month))

    def daily(self, user_id: str, day: date) -> Decimal | None:
        period = self.period_on(user_id, day)
        if period is None:
            return None
        return pnl.daily_cost(period, self.month_working_days(day.year, day.month))

    def monthly_now(self, user_id: str, today: date) -> Decimal | None:
        period = self.period_on(user_id, today)
        return None if period is None else period.monthly


def rate_book(start: date, end: date) -> RateBook:
    """Rates for any day in a range. Holidays are loaded once for the range;
    a wider window is padded so month working-day counts are exact."""
    first = start.replace(day=1)
    last = pnl.month_bounds(end.year, end.month)[1]
    return RateBook(periods_by_user(db.list_cost_periods()), holidays_between(first, last))


# ---------------------------------------------------------------------------
# Monthly profit — FR-PNL
# ---------------------------------------------------------------------------


def _parse_month(value: str) -> tuple[int, int]:
    year, month = value.split("-")
    return int(year), int(month)


def monthly(start: str | None, end: str | None) -> dict[str, Any]:
    today = today_in_company_tz()
    if start is None or end is None:
        this = today.replace(day=1)
        start_y, start_m = pnl.months_between(this - timedelta(days=1), this)[0]
        # Three months back to five ahead by default — enough to see the
        # last quarter closed and the next one planned.
        s = date(start_y, start_m, 1)
        for _ in range(2):
            s = (s - timedelta(days=1)).replace(day=1)
        e = this
        for _ in range(5):
            e = pnl.month_bounds(e.year, e.month)[1] + timedelta(days=1)
        start = start or f"{s.year:04d}-{s.month:02d}"
        end = end or f"{e.year:04d}-{e.month:02d}"

    sy, sm = _parse_month(start)
    ey, em = _parse_month(end)
    months = pnl.months_between(date(sy, sm, 1), date(ey, em, 1))
    if not months or len(months) > 24:
        months = months[:24]
    range_first = date(months[0][0], months[0][1], 1)
    range_last = pnl.month_bounds(*months[-1])[1]

    people = sorted(db.list_profiles(active_only=True), key=lambda p: p["display_name"])
    names = {p["id"]: p["display_name"] for p in db.list_profiles()}
    projects = db.list_projects(include_archived=True)
    phases = db.list_phases()
    allocations = db.list_allocations()
    entries = [
        e for e in db.list_time_entries(start=range_first, end=range_last) if e.get("project_id")
    ]
    rates = rate_book(range_first, range_last)
    holidays = holidays_between(range_first, range_last)
    milestones_by_project: dict[str, list[dict]] = defaultdict(list)
    for m in db.list_milestones():
        milestones_by_project[m["project_id"]].append(m)

    windows: dict[str, tuple[date, date] | None] = {}
    for project in projects:
        mine = [
            (date.fromisoformat(ph["starts_on"]), date.fromisoformat(ph["ends_on"]))
            for ph in phases
            if ph["project_id"] == project["id"]
        ]
        windows[project["id"]] = pnl.project_window(mine)

    month_meta = []
    person_cells: dict[str, list[dict]] = {p["id"]: [] for p in people}
    project_cells: dict[str, list[dict]] = {p["id"]: [] for p in projects}
    totals = []
    unrated_ids: set[str] = set()

    for year, month in months:
        first, last = pnl.month_bounds(year, month)
        basis = pnl.basis_for(first, last, today)
        wd = pnl.working_days(first, last, holidays)
        month_meta.append(
            {
                "period": f"{year:04d}-{month:02d}",
                "first": first.isoformat(),
                "last": last.isoformat(),
                "working_days": wd,
                "basis": basis,
            }
        )

        person_revenue: dict[str, Decimal] = defaultdict(lambda: ZERO)
        unattributed_total = ZERO

        for project in projects:
            pid = project["id"]
            revenue = (
                Decimal(str(project["revenue"])) if project.get("revenue") is not None else None
            )
            mine = milestones_by_project.get(pid, [])
            if mine:
                # Spec 006 FR-MILE-02 — milestones override the even spread.
                rev_m = sum(
                    (
                        Decimal(str(m["amount"]))
                        for m in mine
                        if first.isoformat() <= m["due_on"] <= last.isoformat()
                    ),
                    ZERO,
                )
            else:
                rev_m = pnl.month_revenue(revenue, windows[pid], first, last, holidays)

            # Weights for attribution — hours (actual) or allocation-days (planned).
            weights: dict[str, Decimal] = defaultdict(lambda: ZERO)
            cost_m: Decimal | None = ZERO
            if basis == "actual":
                for e in entries:
                    if e["project_id"] != pid:
                        continue
                    day = date.fromisoformat(e["date"])
                    if not (first <= day <= last):
                        continue
                    hours = Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"]))
                    weights[e["user_id"]] += hours
                    rate = rates.hourly(e["user_id"], day)
                    if rate is None:
                        cost_m = None
                        unrated_ids.add(e["user_id"])
                    elif cost_m is not None:
                        cost_m += hours * rate
            else:
                for a in allocations:
                    if a["project_id"] != pid:
                        continue
                    window = pnl.overlap(
                        date.fromisoformat(a["starts_on"]),
                        date.fromisoformat(a["ends_on"]),
                        first,
                        last,
                    )
                    if window is None:
                        continue
                    share = Decimal(str(a["percent"])) / HUNDRED
                    day = window[0]
                    while day <= window[1]:
                        if not is_weekend(day) and day not in holidays:
                            weights[a["user_id"]] += share
                            daily = rates.daily(a["user_id"], day)
                            if daily is None:
                                cost_m = None
                                unrated_ids.add(a["user_id"])
                            elif cost_m is not None:
                                cost_m += share * daily
                        day += timedelta(days=1)

            shares = pnl.shares(dict(weights))
            unattributed = False
            if rev_m is not None and rev_m > 0:
                if shares:
                    for uid, fraction in shares.items():
                        person_revenue[uid] += rev_m * fraction
                else:
                    unattributed = True
                    unattributed_total += rev_m

            has_activity = bool(weights)
            # A month with revenue but nobody on the project is not 100%
            # profit; it is revenue nobody earned yet. Flagged, not celebrated.
            cell = pnl.Cell(
                revenue=rev_m or ZERO,
                cost=cost_m if has_activity else ZERO,
                basis=basis,
                complete=(cost_m is not None)
                and (revenue is None or windows[pid] is not None)
                and not unattributed,
            )
            project_cells[pid].append(
                {
                    **cell.as_dict(),
                    "unattributed": unattributed,
                    "no_timeline": revenue is not None and windows[pid] is None and not mine,
                }
            )

        total_revenue = unattributed_total
        total_cost: Decimal | None = ZERO
        for person in people:
            uid = person["id"]
            mc = pnl.month_cost(rates._periods.get(uid, []), first, last, holidays)
            cost = None if mc.unknown else mc.cost
            if mc.unknown or not mc.complete:
                unrated_ids.add(uid)
            cell = pnl.Cell(
                revenue=person_revenue.get(uid, ZERO),
                cost=cost,
                basis=basis,
                complete=mc.complete,
            )
            person_cells[uid].append(
                {**cell.as_dict(), "unrated_days": mc.working_days - mc.covered_days}
            )
            total_revenue += cell.revenue
            if cost is None:
                total_cost = None
            elif total_cost is not None:
                total_cost += cost

        total = pnl.Cell(
            revenue=total_revenue,
            cost=total_cost,
            basis=basis,
            complete=total_cost is not None,
        )
        totals.append({**total.as_dict(), "unattributed": str(pnl.money(unattributed_total))})

    return {
        "currency": _currency(),
        "start": month_meta[0]["period"],
        "end": month_meta[-1]["period"],
        "today": today.isoformat(),
        "months": month_meta,
        # Sorted by name, never by money (spec 005 FR-PNL-03).
        "people": [
            {"user_id": p["id"], "display_name": p["display_name"], "cells": person_cells[p["id"]]}
            for p in people
        ],
        "projects": sorted(
            (
                {
                    "project_id": p["id"],
                    "project_name": p["name"],
                    "is_archived": p["is_archived"],
                    "has_timeline": windows[p["id"]] is not None,
                    "revenue": str(p["revenue"]) if p.get("revenue") is not None else None,
                    # Spec 006 FR-MILE-03 — milestones that do not add up.
                    **_milestone_summary(p, milestones_by_project.get(p["id"], [])),
                    "cells": project_cells[p["id"]],
                }
                for p in projects
            ),
            key=lambda r: r["project_name"],
        ),
        "totals": totals,
        "unrated": sorted(
            ({"user_id": uid, "display_name": names.get(uid, "—")} for uid in unrated_ids),
            key=lambda r: r["display_name"],
        ),
    }


# ---------------------------------------------------------------------------
# The timeline — FR-TL
# ---------------------------------------------------------------------------


def timeline(start: date, end: date) -> dict[str, Any]:
    """Who is on what, as bars. The frontend lays them out; this returns the
    allocations that touch the range, with one colour index per project."""
    people = sorted(db.list_profiles(active_only=True), key=lambda p: p["display_name"])
    projects = {p["id"]: p for p in db.list_projects(include_archived=True)}
    allocations = db.list_allocations()

    touched: dict[str, int] = {}
    rows = []
    for person in people:
        bars = []
        peak = ZERO
        daily: dict[date, Decimal] = defaultdict(lambda: ZERO)
        for a in allocations:
            if a["user_id"] != person["id"]:
                continue
            a_start = date.fromisoformat(a["starts_on"])
            a_end = date.fromisoformat(a["ends_on"])
            if pnl.overlap(a_start, a_end, start, end) is None:
                continue
            if a["project_id"] not in touched:
                touched[a["project_id"]] = len(touched)
            pct = Decimal(str(a["percent"]))
            bars.append(
                {
                    "id": a["id"],
                    "project_id": a["project_id"],
                    "project_name": projects.get(a["project_id"], {}).get("name", "—"),
                    "colour": touched[a["project_id"]],
                    "starts_on": a["starts_on"],
                    "ends_on": a["ends_on"],
                    "percent": str(pct),
                }
            )
            window = pnl.overlap(a_start, a_end, start, end)
            day = window[0]
            while day <= window[1]:
                daily[day] += pct
                peak = max(peak, daily[day])
                day += timedelta(days=1)
        rows.append(
            {
                "user_id": person["id"],
                "display_name": person["display_name"],
                "allocations": sorted(bars, key=lambda b: (b["starts_on"], b["project_name"])),
                "peak_percent": str(peak),
                "over": peak > HUNDRED,
            }
        )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "projects": sorted(
            (
                {
                    "project_id": pid,
                    "name": projects.get(pid, {}).get("name", "—"),
                    "colour": colour,
                }
                for pid, colour in touched.items()
            ),
            key=lambda r: r["colour"],
        ),
        "people": rows,
    }


def _milestone_summary(project: dict, milestones: list[dict]) -> dict[str, str | None]:
    if not milestones:
        return {"milestones_total": None, "milestone_gap": None}
    total = sum((Decimal(str(m["amount"])) for m in milestones), ZERO)
    revenue = Decimal(str(project["revenue"])) if project.get("revenue") is not None else None
    return {
        "milestones_total": str(pnl.money(total)),
        "milestone_gap": None if revenue is None else str(pnl.money(revenue - total)),
    }
