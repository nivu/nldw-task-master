"""Quarterly self-summary — spec 006 FR-REV. The person's own words about
their own work; the lead reads, never rates."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.domain.calendar import today_in_company_tz
from app.services import audit
from app.services import supabase as db


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def quarter_bounds(quarter: str) -> tuple[date, date]:
    year, q = quarter.split("-Q")
    start_month = (int(q) - 1) * 3 + 1
    first = date(int(year), start_month, 1)
    end_month = start_month + 2
    last = date(int(year), end_month + 1, 1) if end_month < 12 else date(int(year) + 1, 1, 1)
    from datetime import timedelta

    return first, last - timedelta(days=1)


def figures(user_id: str, quarter: str) -> dict[str, Any]:
    first, last = quarter_bounds(quarter)
    entries = db.list_time_entries(user_ids=[user_id], start=first, end=last)
    projects = {p["id"]: p["name"] for p in db.list_projects(include_archived=True)}
    labels = {
        "learning": "Learning",
        "internal": "Internal work",
        "admin": "Admin",
        "other": "Other",
    }
    hours: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    notes: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in entries:
        key = (
            projects.get(e["project_id"], "—")
            if e.get("project_id")
            else labels.get(e.get("activity") or "", "Activity")
        )
        h = Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"]))
        hours[key] += h
        if e.get("note"):
            notes[key][e["date"][:7]].append(
                {"date": e["date"], "note": e["note"], "hours": str(h)}
            )
    return {
        "quarter": quarter,
        "first": first.isoformat(),
        "last": last.isoformat(),
        "hours": [
            {"name": k, "hours": str(v)} for k, v in sorted(hours.items(), key=lambda kv: kv[0])
        ],
        "total_hours": str(sum(hours.values(), Decimal("0"))),
        "notes": [
            {
                "name": name,
                "months": [
                    {"period": m, "entries": entries} for m, entries in sorted(months.items())
                ],
            }
            for name, months in sorted(notes.items())
        ],
    }


def get(user_id: str, quarter: str | None = None) -> dict[str, Any]:
    quarter = quarter or quarter_of(today_in_company_tz())
    row = db.get_review(user_id, quarter) or {}
    return {
        **figures(user_id, quarter),
        "summary": row.get("summary", ""),
        "submitted_at": row.get("submitted_at"),
        "closed_at": row.get("closed_at"),
    }


def save(user_id: str, quarter: str, summary: str, submit: bool) -> dict[str, Any]:
    existing = db.get_review(user_id, quarter)
    if existing and existing.get("closed_at"):
        raise ValueError("That quarter's review has been closed by your lead.")
    row = db.upsert_review(
        {
            "user_id": user_id,
            "quarter": quarter,
            "summary": summary,
            "submitted_at": datetime.now(UTC).isoformat()
            if submit
            else (existing or {}).get("submitted_at"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    if submit:
        audit.record(
            action="review.submitted",
            target_table="reviews",
            target_id=row["id"],
            actor_id=user_id,
            after={"quarter": quarter},
        )
    return row


def close(*, user_id: str, quarter: str, actor_id: str) -> None:
    existing = db.get_review(user_id, quarter)
    now = datetime.now(UTC).isoformat()
    db.upsert_review(
        {
            "user_id": user_id,
            "quarter": quarter,
            "summary": (existing or {}).get("summary", ""),
            "submitted_at": (existing or {}).get("submitted_at"),
            "closed_by": actor_id,
            "closed_at": now,
            "updated_at": now,
        }
    )
    audit.record(
        action="review.closed",
        target_table="reviews",
        target_id=None,
        actor_id=actor_id,
        after={"user_id": user_id, "quarter": quarter},
    )
