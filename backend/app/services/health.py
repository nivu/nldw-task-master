"""Project health — spec 006 FR-HEALTH. Combines effort, timeline and money."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain import health as rules
from app.domain import pnl
from app.domain.calendar import today_in_company_tz
from app.services import analytics
from app.services import pnl as pnl_service
from app.services import supabase as db
from app.services.timesheets import holidays_between

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    return None if whole <= 0 else (part / whole * HUNDRED).quantize(Decimal("0.1"))


def project_health(project_id: str) -> dict[str, Any] | None:
    project = db.get_project(project_id)
    if project is None:
        return None
    today = today_in_company_tz()
    effort = analytics.project_effort(project_id)
    money = analytics.project_financials(project_id)
    phases = db.list_phases(project_id)
    window = pnl.project_window(
        [(date.fromisoformat(p["starts_on"]), date.fromisoformat(p["ends_on"])) for p in phases]
    )

    logged = Decimal(effort["total"]["logged_hours"])
    budget = Decimal(effort["total"]["budget_hours"]) if effort["total"]["budget_hours"] else None
    elapsed = None
    if window:
        holidays = holidays_between(window[0], window[1])
        total_days = pnl.working_days(window[0], window[1], holidays)
        so_far = (
            pnl.working_days(window[0], min(window[1], today), holidays)
            if today >= window[0]
            else 0
        )
        elapsed = _pct(Decimal(so_far), Decimal(total_days)) if total_days else None

    # Planned margin: revenue against the cost of every allocation over the
    # timeline at each person's CTC on those days.
    planned_pct = None
    revenue = Decimal(str(project["revenue"])) if project.get("revenue") is not None else None
    if window and revenue and revenue > 0:
        rates = pnl_service.rate_book(window[0], window[1])
        holidays = holidays_between(window[0], window[1])
        planned_cost: Decimal | None = ZERO
        for a in db.list_allocations(project_id=project_id):
            overlap = pnl.overlap(
                date.fromisoformat(a["starts_on"]),
                date.fromisoformat(a["ends_on"]),
                window[0],
                window[1],
            )
            if overlap is None:
                continue
            share = Decimal(str(a["percent"])) / HUNDRED
            day = overlap[0]
            while day <= overlap[1]:
                if day.weekday() < 5 and day not in holidays:
                    daily = rates.daily(a["user_id"], day)
                    if daily is None:
                        planned_cost = None
                        break
                    planned_cost += daily * share
                day += timedelta(days=1)
            if planned_cost is None:
                break
        if planned_cost is not None:
            planned_pct = _pct(revenue - planned_cost, revenue)

    to_date_pct = Decimal(money["margin_pct"]) if money.get("margin_pct") else None
    burn = rules.burn(logged, budget, elapsed)
    burn_pct = Decimal(burn.inputs["burn_pct"]) if "burn_pct" in burn.inputs else None
    dims = [
        burn,
        rules.margin(to_date_pct, planned_pct, bool(money.get("complete"))),
        rules.schedule(elapsed, burn_pct, project["is_archived"]),
    ]
    return {
        "project_id": project_id,
        "project_name": project["name"],
        "overall": rules.overall(dims),
        "dimensions": [
            {"key": d.key, "colour": d.colour, "detail": d.detail, "inputs": d.inputs} for d in dims
        ],
        "timeline": None
        if not window
        else {"starts_on": window[0].isoformat(), "ends_on": window[1].isoformat()},
    }


def all_projects_health() -> list[dict[str, Any]]:
    out = []
    for project in db.list_projects(include_archived=False):
        h = project_health(project["id"])
        if h:
            out.append(
                {
                    "project_id": h["project_id"],
                    "project_name": h["project_name"],
                    "overall": h["overall"],
                }
            )
    return sorted(out, key=lambda r: r["project_name"])
