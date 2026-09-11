"""Holidays by location — spec 006 FR-LOC-02.

A holiday row with no location applies everywhere; one with a location applies
to the people there. A person with no location belongs to the default one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services import supabase as db


def location_of(user_id: str) -> str | None:
    profile = db.get_profile(user_id) or {}
    return profile.get("location_id") or db.default_location_id()


def holidays_for_user(user_id: str, start: date, end: date) -> dict[str, str]:
    """{iso date: name} for the holidays that apply to this person."""
    rows = db.list_holidays_for_location(start, end, location_of(user_id))
    return {row["date"]: row["name"] for row in rows}


def holiday_on_for_user(user_id: str, day: date) -> dict[str, Any] | None:
    rows = db.list_holidays_for_location(day, day, location_of(user_id))
    return rows[0] if rows else None


def holidays_by_person(user_ids: list[str], start: date, end: date) -> dict[str, set[date]]:
    """One query, then filtered per person by location."""
    rows = db.list_holidays(start, end)
    default = db.default_location_id()
    profiles = {p["id"]: p for p in db.list_profiles()}
    out: dict[str, set[date]] = {}
    for uid in user_ids:
        loc = profiles.get(uid, {}).get("location_id") or default
        out[uid] = {
            date.fromisoformat(r["date"]) for r in rows if r.get("location_id") in (None, loc)
        }
    return out
