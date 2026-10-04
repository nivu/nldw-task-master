"""The CEO dashboard — spec 003 FR-DASH.

One call that answers "how is the company doing", assembled from the services
behind the detail pages so every figure here is the figure there: the monthly
profit (spec 005), coverage (`002` FR-ANALYTICS-05), utilisation, health and
invoicing (spec 006). Nothing here re-derives money; it picks months out of
what those services return and hands the judgements to `domain/dashboard.py`.

Who may call it is decided by the route (`DashboardDep`), never here; how
much money it breaks down is the caller's role (FR-DASH-12).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain import dashboard as rules
from app.domain import pnl
from app.domain import timesheets as ts
from app.domain import utilisation as calc
from app.domain.calendar import is_weekend, today_in_company_tz
from app.domain.invoicing import OVERDUE, PAYMENT_OVERDUE
from app.domain.rules import OCCUPYING_STATES
from app.services import analytics, settings_store, utilisation
from app.services import health as health_service
from app.services import invoicing as invoice_service
from app.services import pnl as pnl_service
from app.services import supabase as db
from app.services.timesheets import holidays_between

ZERO = Decimal("0")
TREND_MONTHS = 12


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _trend_months(today: date) -> list[tuple[int, int]]:
    """Twelve months ending with today's."""
    first = today.replace(day=1)
    for _ in range(TREND_MONTHS - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    return pnl.months_between(first, today.replace(day=1))


def summary(breakdown: bool) -> dict[str, Any]:
    """`breakdown` — the viewer is a manager or admin. Without it, money stays
    at company level: per-category and per-project cost come from a handful of
    people's CTC, which nobody below manager sees (`deps.require_manager`)."""
    today = today_in_company_tz()
    months = _trend_months(today)
    periods = [_period(y, m) for y, m in months]
    now, prev = len(months) - 1, len(months) - 2

    profiles = db.list_profiles()
    active = sorted((p for p in profiles if p["is_active"]), key=lambda p: p["display_name"])
    active_ids = [p["id"] for p in active]
    # FR-DASH-08 — headcount is the people who keep a timesheet and are
    # employed today; they are the population of every people figure.
    headcount = [p for p in active if ts.logs_time(p) and ts.employed_on(p, today)]
    projects = db.list_projects(include_archived=True)
    tentative_ids = {p["id"] for p in projects if ts.is_tentative(p)}
    allocations = db.list_allocations()

    # -- Today ---------------------------------------------------------------
    holiday = db.get_holiday_on(today)
    today_bookings = db.list_bookings(
        user_ids=active_ids, start=today, end=today, statuses=sorted(OCCUPYING_STATES)
    )
    pending = db.list_bookings(user_ids=active_ids, statuses=["pending"])
    claims = db.list_compoff_credits(user_ids=active_ids, statuses=["pending"])
    today_block = {
        "date": today.isoformat(),
        # The Team page's rule: on a weekend or holiday the counts say nothing.
        "is_weekend": is_weekend(today),
        "holiday": holiday["name"] if holiday else None,
        "status": rules.day_status(active_ids, today_bookings),
        "pending_approvals": len(pending),
        "pending_compoff_claims": len(claims),
    }

    # -- Data health ---------------------------------------------------------
    last_day = rules.last_working_day(today, holidays_between(today - timedelta(days=21), today))
    monday = today - timedelta(days=today.weekday())
    month_windows = [pnl.month_bounds(y, m) for y, m in months]
    windows = [(last_day, last_day), (monday, today), (today.replace(day=1), today)]
    cov = analytics.coverage_windows(active_ids, windows + month_windows)
    portal_start = settings_store.portal_start_date()

    def _cov(c: dict) -> dict:
        return {k: c[k] for k in ("start", "end", "expected_days", "logged_days", "coverage")}

    data_health = {
        "last_working_day": _cov(cov[0]),
        "week_to_date": _cov(cov[1]),
        "month_to_date": _cov(cov[2]),
        "missing_last_working_day": [
            {"user_id": r["user_id"], "display_name": r["display_name"]}
            for r in cov[0]["people"]
            if r["missing_days"]
        ],
        "portal_start_date": None if portal_start is None else portal_start.isoformat(),
    }
    # FR-DASH-07 — a month wholly before the portal was in use has no
    # coverage to speak of: null, not 0%.
    month_coverage = [
        None if portal_start is not None and last < portal_start else c["coverage"]
        for (_, last), c in zip(month_windows, cov[3:], strict=True)
    ]

    # -- Money ---------------------------------------------------------------
    report = pnl_service.monthly(periods[0], periods[-1])
    currency = report["currency"]

    def _month(i: int) -> dict:
        return {"period": periods[i], **report["totals"][i]}

    unrated = sorted(
        {
            p["display_name"]
            for p in report["people"]
            for i in (prev, now)
            if not p["cells"][i]["complete"]
        }
    )
    project_cells = {p["project_id"]: p for p in report["projects"]}
    money = {
        "currency": currency,
        "breakdown": breakdown,
        "this_month": _month(now),
        "last_month": _month(prev),
        "categories": [
            {"category": c["category"], "label": c["label"], **c["cells"][now]}
            for c in report["categories"]
            if breakdown
        ],
        # FR-PNL-05 — pipeline, never profit: outside every figure above.
        "pipeline": {
            "label": report["pipeline"]["label"],
            "period": periods[now],
            **report["pipeline"]["totals"][now],
        },
        # FR-FIN-06 — who and why, for this month and last.
        "incomplete": {
            "unrated": [{"display_name": n} for n in unrated],
            "no_timeline": [
                {"project_id": p["project_id"], "project_name": p["project_name"]}
                for p in report["projects"]
                if any(p["cells"][i]["no_timeline"] for i in (prev, now))
            ],
        },
        # Revenue this month that nobody is allocated to earn (spec 005 §3.3).
        "unattributed": [
            {"project_id": p["project_id"], "project_name": p["project_name"]}
            for p in report["projects"]
            if p["cells"][now]["unattributed"]
        ],
    }

    # -- Cash ----------------------------------------------------------------
    invoices = invoice_service.listing()
    cash = {
        "currency": invoices["currency"],
        "payment_terms_days": invoices["payment_terms_days"],
        "totals": invoices["totals"],
        "overdue_count": sum(1 for i in invoices["invoices"] if i["status"] == PAYMENT_OVERDUE),
        "uninvoiced_overdue_count": sum(1 for i in invoices["invoices"] if i["status"] == OVERDUE),
    }

    # -- Delivery ------------------------------------------------------------
    health = [
        h
        for h in (health_service.project_health(p["id"]) for p in projects if not p["is_archived"])
        if h
    ]
    counts = {"green": 0, "amber": 0, "red": 0}
    for h in health:
        counts[h["overall"]] += 1

    def _flagged(colour: str) -> list[dict]:
        return sorted(
            (
                {
                    "project_id": h["project_id"],
                    "project_name": h["project_name"],
                    "reasons": [d["detail"] for d in h["dimensions"] if d["colour"] == colour],
                }
                for h in health
                if h["overall"] == colour
            ),
            key=lambda r: r["project_name"],
        )

    burning = []
    for h in health:
        burn = next(d for d in h["dimensions"] if d["key"] == "burn")
        pct = burn["inputs"].get("burn_pct")
        if pct is not None and Decimal(pct) > rules.BURN_WATCH_PCT:
            burning.append(
                {
                    "project_id": h["project_id"],
                    "project_name": h["project_name"],
                    "burn_pct": pct,
                    "logged_hours": burn["inputs"]["logged_hours"],
                    "budget_hours": burn["inputs"]["budget_hours"],
                }
            )

    names = {p["id"]: p["name"] for p in projects}
    spillover = []
    for phase in db.list_phases():
        pid = phase["project_id"]
        if (
            phase["phase"] == "spillover"
            and phase["starts_on"] <= today.isoformat() <= phase["ends_on"]
            and pid in project_cells
            and not project_cells[pid]["is_archived"]
        ):
            cell = project_cells[pid]["cells"][now]
            spillover.append(
                {
                    "project_id": pid,
                    "project_name": names.get(pid, "—"),
                    "starts_on": phase["starts_on"],
                    "ends_on": phase["ends_on"],
                    "cost": cell["cost"] if breakdown else None,
                    "revenue": cell["revenue"] if breakdown else None,
                    "complete": cell["complete"],
                }
            )

    delivery = {
        "counts": counts,
        "red": _flagged("red"),
        "amber": _flagged("amber"),
        "spillover": sorted(spillover, key=lambda r: r["project_name"]),
        "burn_over_80": sorted(burning, key=lambda r: r["project_name"]),
    }

    # -- People --------------------------------------------------------------
    headcount_ids = [p["id"] for p in headcount]
    util = (
        utilisation.monthly(headcount_ids, periods[0], periods[-1])
        if headcount_ids
        else {"people": [], "target_pct": str(utilisation.target())}
    )
    month_util = []
    for i, (_, last) in enumerate(month_windows):
        billable = sum((Decimal(p["months"][i]["billable"]) for p in util["people"]), ZERO)
        capacity = sum((Decimal(p["months"][i]["capacity"]) for p in util["people"]), ZERO)
        # FR-DASH-07 — as coverage: nobody logged before the portal, so null.
        pct = (
            None
            if portal_start is not None and last < portal_start
            else calc.pct(billable, capacity)
        )
        month_util.append(
            {
                "billable": str(billable),
                "capacity": str(capacity),
                "utilisation_pct": None if pct is None else str(pct),
            }
        )
    forecast = analytics.forecast(today, today + timedelta(days=rules.ENDING_WITHIN_DAYS - 1))
    people = {
        "headcount": len(headcount),
        "utilisation": {
            "period": periods[now],
            "target_pct": util["target_pct"],
            **month_util[now],
        },
        "unallocated": rules.unallocated(headcount, allocations, today, tentative_ids),
        "ending": rules.allocations_ending(headcount, allocations, today, tentative_ids),
        "over_allocated": [
            {"display_name": o["display_name"], "peak_percent": o["peak_percent"]}
            for o in forecast["over_allocated"]
        ],
    }

    # -- Trends --------------------------------------------------------------
    trends = [
        {
            "period": periods[i],
            "basis": report["months"][i]["basis"],
            "revenue": report["totals"][i]["revenue"],
            "cost": report["totals"][i]["cost"],
            "profit_pct": report["totals"][i]["profit_pct"],
            "complete": report["totals"][i]["complete"],
            "utilisation_pct": month_util[i]["utilisation_pct"],
            "coverage": month_coverage[i],
        }
        for i in range(len(months))
    ]

    out = {
        "today": today_block,
        "data_health": data_health,
        "money": money,
        "cash": cash,
        "delivery": delivery,
        "people": people,
        "trends": trends,
    }
    out["attention"] = rules.attention(out)
    return out
