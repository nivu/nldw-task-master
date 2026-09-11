"""Nudges, the morning post and the Monday digest — spec 006 FR-NUDGE,
FR-FEED-03, FR-DIGEST. Composes messages; delivery is `notify`."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain.calendar import is_weekend, today_in_company_tz
from app.domain.rules import CATEGORY_LABELS
from app.services import analytics, settings_store, utilisation
from app.services import supabase as db
from app.services.holidays import holidays_by_person
from app.services.notify import Message, deliver
from app.services.notify import slack as slack_adapter
from app.services.timesheets import leave_days_for


def _portal(path: str = "") -> str:
    from app.config import settings

    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


def _send(person: dict, subject: str, body: str) -> list[str]:
    return deliver(
        Message(
            recipient_email=person["email"],
            recipient_name=person["display_name"],
            subject=subject,
            body=body,
        )
    )


# ---------------------------------------------------------------------------
# FR-NUDGE-01 — nothing logged today
# ---------------------------------------------------------------------------


def nothing_logged_today(today: date | None = None) -> dict[str, Any]:
    if not settings_store.get("nudges_enabled", True):
        return {"sent": 0, "skipped": "nudges_enabled is false"}
    today = today or today_in_company_tz()
    if is_weekend(today):
        return {"sent": 0, "skipped": "weekend"}
    people = db.list_profiles(active_only=True)
    ids = [p["id"] for p in people]
    holidays = holidays_by_person(ids, today, today)
    leave = leave_days_for(ids, today, today)
    logged = {e["user_id"] for e in db.list_time_entries(user_ids=ids, start=today, end=today)}
    sent = 0
    for person in people:
        if today in holidays.get(person["id"], set()):
            continue
        if leave.get(person["id"], {}).get(today, Decimal("0")) >= 1:
            continue
        if person["id"] in logged:
            continue
        if _send(
            person,
            "Nothing logged for today yet",
            f"Take a minute to log what you worked on today: {_portal('/timesheet')}",
        ):
            sent += 1
    return {"sent": sent, "date": today.isoformat()}


# ---------------------------------------------------------------------------
# FR-NUDGE-02 — a lead's reports' gaps this week
# ---------------------------------------------------------------------------


def weekly_gaps(today: date | None = None) -> dict[str, Any]:
    if not settings_store.get("nudges_enabled", True):
        return {"sent": 0, "skipped": "nudges_enabled is false"}
    today = today or today_in_company_tz()
    monday = today - timedelta(days=today.weekday())
    sent = 0
    for lead in (
        p for p in db.list_profiles(active_only=True) if p["role"] in ("lead", "manager", "admin")
    ):
        reports = db.list_reports(lead["id"], active_only=True)
        if not reports:
            continue
        cov = analytics.coverage([r["id"] for r in reports], monday, today)
        gaps = [(p["display_name"], p["missing_days"]) for p in cov["people"] if p["missing_days"]]
        if not gaps:
            continue
        lines = [
            f"• {name}: {len(days)} day(s) — {', '.join(d[5:] for d in days)}"
            for name, days in gaps
        ]
        if _send(
            lead,
            "Timesheet gaps this week",
            "\n".join(lines) + f"\n\nConfirm weeks under Team: {_portal('/team')}",
        ):
            sent += 1
    return {"sent": sent, "week_start": monday.isoformat()}


# ---------------------------------------------------------------------------
# FR-NUDGE-03 — over-allocation next week, to managers
# ---------------------------------------------------------------------------


def over_allocation_next_week(today: date | None = None) -> dict[str, Any]:
    if not settings_store.get("nudges_enabled", True):
        return {"sent": 0, "skipped": "nudges_enabled is false"}
    today = today or today_in_company_tz()
    monday = today - timedelta(days=today.weekday())
    forecast = analytics.forecast(monday, monday + timedelta(days=6))
    over = forecast["over_allocated"]
    if not over:
        return {"sent": 0, "over": 0}
    lines = [
        f"• {p['display_name']}: peaks at {p['peak_percent']}% ({p['first']} → {p['last']})"
        for p in over
    ]
    sent = 0
    for manager in (
        p for p in db.list_profiles(active_only=True) if p["role"] in ("manager", "admin")
    ):
        if _send(
            manager,
            "Over-allocated this week",
            "\n".join(lines) + f"\n\nAdjust under Projects: {_portal('/projects')}",
        ):
            sent += 1
    return {"sent": sent, "over": len(over)}


# ---------------------------------------------------------------------------
# FR-FEED-03 — who is out today, to a channel
# ---------------------------------------------------------------------------


def morning_out_post(today: date | None = None) -> dict[str, Any]:
    channel = str(settings_store.get("slack_out_channel", "") or "").strip()
    if not channel:
        return {"posted": False, "skipped": "slack_out_channel is empty"}
    today = today or today_in_company_tz()
    if is_weekend(today):
        return {"posted": False, "skipped": "weekend"}
    people = db.list_profiles(active_only=True)
    names = {p["id"]: p["display_name"] for p in people}
    bookings = db.list_bookings(
        user_ids=list(names), start=today, end=today, statuses=["approved", "pending"]
    )
    groups: dict[str, list[str]] = {}
    for b in bookings:
        label = CATEGORY_LABELS.get(b["category"], b["category"])
        who = names[b["user_id"]] + (" (half day)" if str(b["duration"]) == "0.5" else "")
        groups.setdefault(label, []).append(who)
    if not groups:
        text = f"*{today.strftime('%A %-d %B')}* — everyone is in."
    else:
        parts = [f"*{label}:* {', '.join(sorted(who))}" for label, who in sorted(groups.items())]
        text = f"*{today.strftime('%A %-d %B')}*\n" + "\n".join(parts)
    ok = slack_adapter.post_to_channel(channel, text)
    return {"posted": ok, "channel": channel, "groups": {k: len(v) for k, v in groups.items()}}


# ---------------------------------------------------------------------------
# FR-DIGEST — Monday leadership digest
# ---------------------------------------------------------------------------


def weekly_digest(today: date | None = None) -> dict[str, Any]:
    if not settings_store.get("digest_enabled", True):
        return {"sent": 0, "skipped": "digest_enabled is false"}
    today = today or today_in_company_tz()
    monday = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(days=7)
    everyone = [p["id"] for p in db.list_profiles(active_only=True)]

    cov = analytics.coverage(everyone, last_monday, monday - timedelta(days=1))
    over = analytics.forecast(monday, monday + timedelta(days=6))["over_allocated"]
    money = analytics.people_financials()
    bench = utilisation.bench(weeks=4)
    from app.services.health import all_projects_health

    health = all_projects_health()

    lines = [
        f"*Last week's timesheet coverage:* {int(Decimal(cov['coverage']) * 100) if cov['coverage'] else 0}% "
        f"({cov['logged_days']} of {cov['expected_days']} working days)",
    ]
    if over:
        lines.append(
            "*Over-allocated this week:* "
            + ", ".join(f"{p['display_name']} ({p['peak_percent']}%)" for p in over)
        )
    else:
        lines.append("*Over-allocated this week:* nobody")
    if health:
        lines.append(
            "*Project health:* " + ", ".join(f"{h['project_name']} {h['overall']}" for h in health)
        )
    benched = [p["display_name"] for p in bench["people"] if p["bench_weeks"] >= 2]
    lines.append(
        "*Bench (2+ of the next 4 weeks under threshold):* "
        + (", ".join(benched) if benched else "nobody")
    )
    if money["unrated"]:
        lines.append("*No CTC recorded:* " + ", ".join(u["display_name"] for u in money["unrated"]))
    body = "\n".join(lines) + f"\n\nDetails: {_portal('/analytics')}"

    sent = 0
    for person in (
        p for p in db.list_profiles(active_only=True) if p["role"] in ("manager", "admin")
    ):
        if _send(person, f"Week of {monday.strftime('%-d %B')} — leadership digest", body):
            sent += 1
    return {"sent": sent, "week_start": monday.isoformat(), "body": body}


def test_message(person: dict) -> list[str]:
    """FR-NUDGE-04 — prove the token works, to the admin who asked."""
    return _send(
        person,
        "Test from the Nunnari portal",
        "If you can read this, Slack notifications are working.",
    )
