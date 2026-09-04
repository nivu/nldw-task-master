"""Reads `app_settings` — the policy switches for the spec's open questions.

Cached briefly. These are read on nearly every booking validation and change
perhaps a handful of times in the product's life, so re-fetching them per
request would be pure overhead; caching them forever would mean an admin's
change needing a restart to take effect. Thirty seconds is the compromise.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.services import supabase as db

logger = logging.getLogger("nldw-task-master")

_CACHE_SECONDS = 30
_lock = threading.Lock()
_cache: dict[str, Any] = {}
_fetched_at: float = 0.0

#: Used when the table cannot be read at all. These mirror
#: 006_settings_defaults.sql — if you change one, change both. Booking
#: validation must not fail open just because a settings read failed, so the
#: conservative values are the ones repeated here.
FALLBACKS: dict[str, Any] = {
    "carry_forward_policy": "rolling",
    "allow_excess_booking": False,
    "sandwich_rule": False,
    "auto_approve_at_lock": True,
    "lead_view_shows_reason": False,
    "max_future_booking_days": 365,
    "timezone": "Asia/Kolkata",
}


def all_settings() -> dict[str, Any]:
    global _fetched_at

    with _lock:
        if _cache and (time.monotonic() - _fetched_at) < _CACHE_SECONDS:
            return dict(_cache)

    try:
        rows = db.list_settings()
        fresh = {row["key"]: row["value"] for row in rows}
    except Exception:
        # A settings read failing must not take booking down with it.
        logger.exception('{"event": "settings_read_failed", "action": "using_fallbacks"}')
        return dict(FALLBACKS)

    with _lock:
        _cache.clear()
        _cache.update(fresh)
        _fetched_at = time.monotonic()
    return dict(fresh)


def get(key: str, default: Any = None) -> Any:
    value = all_settings().get(key)
    if value is None:
        return FALLBACKS.get(key, default)
    return value


def invalidate() -> None:
    """Drop the cache so the next read hits the database.

    Called after an admin writes a setting, so their own next request reflects
    the change rather than showing them the stale value they just replaced.
    """
    global _fetched_at
    with _lock:
        _cache.clear()
        _fetched_at = 0.0


# ---------------------------------------------------------------------------
# Typed accessors. Named after the question each one answers so that call
# sites read as the rule rather than as a dictionary lookup.
# ---------------------------------------------------------------------------


def carry_forward_policy() -> str:
    """Q-02 — `rolling` or `pooling`."""
    return str(get("carry_forward_policy", "rolling"))


def allow_excess_booking() -> bool:
    """Q-08 — may a booking take a balance below zero?"""
    return bool(get("allow_excess_booking", False))


def sandwich_rule() -> bool:
    """Q-09 — do weekends between leave days consume allowance?"""
    return bool(get("sandwich_rule", False))


def auto_approve_at_lock() -> bool:
    """Q-04 — is an un-actioned pending booking approved when its day closes?"""
    return bool(get("auto_approve_at_lock", True))


def lead_view_shows_reason() -> bool:
    """Q-06 — does the team list carry reasons, or categories only?"""
    return bool(get("lead_view_shows_reason", False))


def max_future_booking_days() -> int:
    """A-14 — how far ahead a booking may be made."""
    return int(get("max_future_booking_days", 365))
