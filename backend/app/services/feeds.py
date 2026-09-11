"""Private calendar feeds — spec 006 FR-FEED-01/02."""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.domain import feeds as render
from app.domain.calendar import today_in_company_tz
from app.domain.rules import CATEGORY_LABELS
from app.services import supabase as db


def _secret() -> str:
    # Derived from the service key so no new secret has to be provisioned;
    # never equal to it, and the key itself never leaves the process.
    base = settings.FEED_SECRET or settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
    return hashlib.sha256(f"feed:{base}".encode()).hexdigest()


def key_for(profile: dict[str, Any]) -> str:
    return render.feed_key(profile["id"], profile.get("feed_salt") or "", _secret())


def rotate(user_id: str) -> str:
    db.update_profile(user_id, {"feed_salt": secrets.token_urlsafe(8)})
    return key_for(db.get_profile(user_id) or {"id": user_id, "feed_salt": ""})


def resolve(key: str) -> dict[str, Any] | None:
    """Which person a key belongs to. Linear over active profiles — tens of
    people, and the alternative is storing a second secret per person."""
    for profile in db.list_profiles(active_only=True):
        if key_for(profile) == key:
            return profile
    return None


def ics_for(profile: dict[str, Any]) -> str:
    from app.api.deps import CurrentUser

    user = CurrentUser(profile)
    today = today_in_company_tz()
    start, end = today - timedelta(days=60), today + timedelta(days=180)
    if user.is_manager:
        people = db.list_profiles(active_only=True)
    elif user.is_lead:
        people = [profile, *db.list_reports(user.id, active_only=True)]
    else:
        people = [profile]
    names = {p["id"]: p["display_name"] for p in people}
    bookings = db.list_bookings(user_ids=list(names), start=start, end=end, statuses=["approved"])
    events = [
        {
            "uid": b["id"],
            "day": date.fromisoformat(b["date"]),
            "summary": f"{names[b['user_id']]} — {CATEGORY_LABELS.get(b['category'], b['category'])}"
            + (" (half day)" if str(b["duration"]) in ("0.5",) else ""),
        }
        for b in bookings
    ]
    for h in db.list_holidays(start, end):
        events.append(
            {
                "uid": f"holiday-{h['id']}",
                "day": date.fromisoformat(h["date"]),
                "summary": f"Holiday — {h['name']}",
            }
        )
    return render.render(events, calendar_name="Nunnari leave")
