"""Contact recency — spec 009 FR-CRMAI-REL-01 to -03; Q-17, Q-21;
data-model §3.10; plan §5.10.

Pure. Each company and person has exactly one level, in this order:

- **Recent**: in the last 30 days there was both an inbound and an outbound
  email, or a meeting note;
- **Fading**: else, any of those (an inbound or outbound email, or a meeting
  note) in the last 90 days;
- **Lapsed**: else.

Derived, not AI. The facts (`crm_contact_facts`, 067) are a record's latest
counted inbound and outbound email (stored, not internal, not an auto-reply
or bounce) and its latest meeting note; a company's include its people's.
Days are whole Asia/Kolkata days: a contact 30 days ago is within the last
30 days, one 31 days ago is not. With Gmail sync off for the workspace only
meeting notes count, and the chip says "Meetings only" (FR-CRMAI-REL-03).

Shown as **Contact recency**, not warm or cold, because the company's
Relationship status already uses Warm and Cold (Q-21).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.crm import today as today_rules

#: Phase 4 (FR-CRMAI-FLAG-01).
PHASE: Final = "4"
#: The levels, in order, with their labels (Q-21).
LEVELS: Final = ("recent", "fading", "lapsed")
LABELS: Final = {"recent": "Recent", "fading": "Fading", "lapsed": "Lapsed"}
#: FR-CRMAI-REL-03 — what the chip says when only meeting notes count.
MEETINGS_ONLY: Final = "Meetings only"
#: Q-17's two windows, whole days.
RECENT_DAYS: Final = 30
FADING_DAYS: Final = 90
#: The record kinds with a level, and their tables.
TABLES: Final = {"company": "crm_companies", "person": "crm_people"}

_ZONE: Final = ZoneInfo(today_rules.DEFAULT_ZONE)


def _day(value: Any) -> date | None:
    """The Asia/Kolkata day of a time, or a date as it is."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(_ZONE).date()
    if isinstance(value, date):
        return value
    text = str(value)
    if len(text) == 10:
        return date.fromisoformat(text)
    return datetime.fromisoformat(text).astimezone(_ZONE).date()


def _within(now: datetime, value: Any, days: int) -> bool:
    """Whether `value` fell in the last `days` whole days of `now`, today
    included; a later one does not count."""
    day = _day(value)
    return day is not None and 0 <= (now.astimezone(_ZONE).date() - day).days <= days


def level(facts: Mapping[str, Any] | None, *, now: datetime, meetings_only: bool = False) -> str:
    """FR-CRMAI-REL-01 — `facts` is `{in, out, meeting}`, each the latest or
    None; with `meetings_only` the emails are not read (FR-CRMAI-REL-03)."""
    facts = facts or {}
    inbound = None if meetings_only else facts.get("in")
    outbound = None if meetings_only else facts.get("out")
    meeting = facts.get("meeting")
    if (_within(now, inbound, RECENT_DAYS) and _within(now, outbound, RECENT_DAYS)) or _within(
        now, meeting, RECENT_DAYS
    ):
        return "recent"
    if any(_within(now, v, FADING_DAYS) for v in (inbound, outbound, meeting)):
        return "fading"
    return "lapsed"
