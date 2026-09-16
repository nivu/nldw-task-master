"""The Home page — one call that answers "what needs me today".

Composed from services that already exist; nothing here decides anything.
Each block is gated by the same capabilities the navigation uses, and every
route the blocks summarise still re-checks the role on its own.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain.calendar import period_of, today_in_company_tz
from app.domain.rules import CATEGORY_LABELS
from app.services import analytics, balances, settings_store, utilisation
from app.services import compoff as compoff_service
from app.services import supabase as db
from app.services import timesheets as timesheet_service
from app.services.confirmations import monday_of


def _mine(user_id: str, today: date) -> dict[str, Any]:
    monday = monday_of(today)
    week = timesheet_service.week_for(user_id, monday)
    todays = db.list_bookings(
        user_ids=[user_id], start=today, end=today, statuses=["pending", "approved"]
    )
    pending = db.list_bookings(
        user_ids=[user_id], start=today, end=today + timedelta(days=365), statuses=["pending"]
    )
    upcoming = db.list_bookings(
        user_ids=[user_id],
        start=today + timedelta(days=1),
        end=today + timedelta(days=30),
        statuses=["approved", "pending"],
    )
    return {
        "today": None
        if not todays
        else {
            "category": todays[0]["category"],
            "label": CATEGORY_LABELS.get(todays[0]["category"], todays[0]["category"]),
            "status": todays[0]["status"],
            "duration": str(todays[0]["duration"]),
        },
        "balances": balances.summary_for(user_id, period=period_of(today)),
        "compoff_available": str(compoff_service.available(user_id, today)),
        "pending_requests": len(pending),
        "upcoming": [
            {
                "date": b["date"],
                "label": CATEGORY_LABELS.get(b["category"], b["category"]),
                "status": b["status"],
            }
            for b in upcoming[:5]
        ],
        "week": {
            "week_start": monday.isoformat(),
            "total": week["total"],
            "days": [
                {
                    "date": d["date"],
                    "total": d["total"],
                    "is_today": d["is_today"],
                    "holiday": d["holiday"],
                    "on_leave": d["on_leave"],
                    "locked": d["locked"],
                }
                for d in week["days"]
            ],
        },
        "logged_today": any(d["is_today"] and Decimal(d["total"]) > 0 for d in week["days"]),
    }


def _lead(user, today: date) -> dict[str, Any]:  # noqa: ANN001
    from app.api.team import _population

    people = _population(user)
    ids = [p["id"] for p in people]
    approvals = db.list_bookings(user_ids=ids, statuses=["pending"])
    out_today = db.list_bookings(
        user_ids=ids, start=today, end=today, statuses=["approved", "pending"]
    )
    names = {p["id"]: p["display_name"] for p in people}
    monday = monday_of(today)
    cov = analytics.coverage(ids, monday, today) if ids else {"people": []}
    claims = [c for c in db.list_compoff_credits(user_ids=ids, statuses=["pending"])]
    return {
        "reports": len(people),
        "approvals_waiting": len(approvals),
        "compoff_claims_waiting": len(claims),
        "out_today": [
            {
                "display_name": names.get(b["user_id"], "—"),
                "label": CATEGORY_LABELS.get(b["category"], b["category"]),
                "duration": str(b["duration"]),
            }
            for b in out_today
        ],
        "gaps_this_week": [
            {"display_name": p["display_name"], "missing": len(p["missing_days"])}
            for p in cov["people"]
            if p["missing_days"]
        ],
    }


def _manager(today: date) -> dict[str, Any]:
    from app.services.health import all_projects_health

    monday = monday_of(today)
    forecast = analytics.forecast(monday, monday + timedelta(days=6))
    bench = utilisation.bench(weeks=4)
    money = analytics.people_financials()
    return {
        "health": all_projects_health(),
        "over_allocated": [
            {"display_name": p["display_name"], "peak_percent": p["peak_percent"]}
            for p in forecast["over_allocated"]
        ],
        "bench": [p["display_name"] for p in bench["people"] if p["bench_weeks"] >= 2],
        "unrated": [u["display_name"] for u in money["unrated"]],
    }


def _admin() -> dict[str, Any]:
    from app.config import settings
    from app.services import checklists

    open_lists = [c for c in checklists.listing() if not c["closed_at"]]
    return {
        "open_checklists": [
            {
                "display_name": c["display_name"],
                "kind": c["kind"],
                "done": c["done"],
                "total": c["total"],
            }
            for c in open_lists
        ],
        "slack_configured": settings.slack_enabled,
        "slack_out_channel": str(settings_store.get("slack_out_channel", "") or ""),
    }


def summary(user) -> dict[str, Any]:  # noqa: ANN001
    today = today_in_company_tz()
    out: dict[str, Any] = {
        "today": today.isoformat(),
        "display_name": user.display_name,
        "mine": _mine(user.id, today),
    }
    if user.is_lead:
        out["lead"] = _lead(user, today)
    if user.is_manager:
        out["manager"] = _manager(today)
    if user.is_admin:
        out["admin"] = _admin()
    return out
