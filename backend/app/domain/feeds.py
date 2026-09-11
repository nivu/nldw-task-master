"""The iCal feed — spec 006 FR-FEED. Pure rendering and key derivation."""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, timedelta


def feed_key(user_id: str, salt: str, secret: str) -> str:
    """Unguessable, per person, rotatable by changing the salt (FR-FEED-02)."""
    return hmac.new(secret.encode(), f"{user_id}:{salt}".encode(), hashlib.sha256).hexdigest()[:40]


def _ical_date(day: date) -> str:
    return day.strftime("%Y%m%d")


def render(events: list[dict], *, calendar_name: str) -> str:
    """Events: {uid, day, summary}. All-day, category only — never a reason."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Nunnari Employee Portal//Leave//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{calendar_name}",
    ]
    for event in events:
        day: date = event["day"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['uid']}@nunnari-portal",
            f"DTSTAMP:{_ical_date(day)}T000000Z",
            f"DTSTART;VALUE=DATE:{_ical_date(day)}",
            f"DTEND;VALUE=DATE:{_ical_date(day + timedelta(days=1))}",
            f"SUMMARY:{event['summary']}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
