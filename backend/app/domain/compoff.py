"""Comp-off credits — spec 006 FR-COMP.

A credit is earned by working a weekend or a holiday, approved by a lead,
valid for a fixed number of days, and consumed by a `compoff` booking. Pure
checks; the service supplies the calendar facts.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

VALID_DAYS = (Decimal("0.5"), Decimal("1"))


def check_claim(
    worked_on: date, *, today: date, is_weekend: bool, holiday_name: str | None, days: Decimal
) -> str | None:
    if worked_on > today:
        return "You can claim comp-off for a day you have worked, not one still to come."
    if not (is_weekend or holiday_name):
        return f"{worked_on.isoformat()} is a working day. Comp-off is for weekends and holidays."
    if days not in VALID_DAYS:
        return "A comp-off claim is a full day (1) or a half day (0.5)."
    return None


def expiry(approved_on: date, valid_days: int) -> date:
    return approved_on + timedelta(days=valid_days)


def usable(credit: dict, on: date) -> bool:
    """Approved, not yet used, not lapsed as of `on`."""
    if credit.get("status") != "approved":
        return False
    expires = credit.get("expires_on")
    if isinstance(expires, str):
        expires = date.fromisoformat(expires)
    return expires is None or on <= expires


def pick_credits(credits: list[dict], needed: Decimal, on: date) -> list[dict] | None:
    """Oldest usable credits covering `needed` days, or None if short."""
    chosen: list[dict] = []
    total = Decimal("0")
    for credit in sorted(
        (c for c in credits if usable(c, on)), key=lambda c: str(c.get("expires_on"))
    ):
        chosen.append(credit)
        total += Decimal(str(credit["days"]))
        if total >= needed:
            return chosen
    return None
