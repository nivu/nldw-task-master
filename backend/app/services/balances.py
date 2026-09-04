"""Assembles the balance ledger from stored rows — FR-BAL.

The arithmetic lives in `app.domain.ledger` and is pure. This module's only job
is to fetch the right rows and hand them over in the right shape. Keeping the
split sharp is what lets the carry-forward policy (Q-02) be tested in both
directions without a database.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain import ledger
from app.domain.calendar import period_of
from app.domain.rules import CATEGORIES, CONSUMING_STATES
from app.services import settings_store
from app.services import supabase as db


def _grants_for(user_id: str) -> list[ledger.Grant]:
    return [
        ledger.Grant(
            period=row["period"],
            category=row["category"],
            days=Decimal(str(row["days"])),
            user_id=row["user_id"],
        )
        for row in db.list_allowances(user_id=user_id, include_defaults=True)
    ]


def _consumption_for(user_id: str) -> list[ledger.Consumption]:
    """Every booking that draws down an allowance, as ledger entries.

    Filtering on CONSUMING_STATES here rather than downstream means a
    withdrawn or rejected booking simply never becomes a `Consumption`, so no
    later code can accidentally count one.
    """
    rows = db.list_bookings(
        user_ids=[user_id],
        statuses=sorted(CONSUMING_STATES),
        columns="date,category,duration,status",
    )
    return [
        ledger.Consumption(
            period=row["date"][:7],
            category=row["category"],
            duration=Decimal(str(row["duration"])),
        )
        for row in rows
        if row["category"] is not None
    ]


def balances_for(
    user_id: str,
    period: str,
    *,
    exclude_booking_duration: Decimal | None = None,
    exclude_category: str | None = None,
) -> dict[str, ledger.Balance]:
    """Every category's balance for one person in one period.

    `exclude_booking_duration` adds back a booking that is about to be
    replaced. Without it, changing an existing full day to a half day on the
    same date would be refused for insufficient allowance — the person's own
    booking would be counted against the change that releases it.
    """
    grants = _grants_for(user_id)
    entries = _consumption_for(user_id)
    policy = settings_store.carry_forward_policy()
    tracking_start = ledger.earliest_period(grants, entries, default=period)

    result: dict[str, ledger.Balance] = {}
    for category in CATEGORIES:
        balance = ledger.balance_for(
            grants,
            entries,
            period,
            category,
            policy=policy,
            tracking_start=tracking_start,
        )
        if exclude_booking_duration and exclude_category == category:
            balance = ledger.Balance(
                category=balance.category,
                period=balance.period,
                opening=balance.opening,
                allowance=balance.allowance,
                used=balance.used - exclude_booking_duration,
                remaining=balance.remaining + exclude_booking_duration,
            )
        result[category] = balance
    return result


def remaining_for(
    user_id: str,
    period: str,
    category: str,
    *,
    exclude_booking_duration: Decimal | None = None,
) -> Decimal:
    """The single number FR-BOOK-05 tests a new booking against."""
    balances = balances_for(
        user_id,
        period,
        exclude_booking_duration=exclude_booking_duration,
        exclude_category=category,
    )
    return balances[category].remaining


def year_history_for(user_id: str, year: str) -> dict[str, dict[str, str]]:
    """FR-BAL-08 — this person's consumption across a calendar year."""
    entries = _consumption_for(user_id)
    history = ledger.year_history(entries, year, CATEGORIES)
    return {
        period: {category: str(days) for category, days in per_category.items()}
        for period, per_category in history.items()
    }


def serialise(balance: ledger.Balance) -> dict[str, Any]:
    """Balances cross the wire as strings.

    JSON has one number type and it is a float. Sending 0.5 as a float and
    reassembling it in a browser is fine once and wrong after enough additions;
    sending "0.5" keeps the decimal exact and makes the frontend's formatting
    choice explicit.
    """
    return {
        "category": balance.category,
        "period": balance.period,
        "opening": str(balance.opening),
        "allowance": str(balance.allowance),
        "used": str(balance.used),
        "remaining": str(balance.remaining),
    }


def summary_for(user_id: str, day: Any = None, period: str | None = None) -> list[dict[str, Any]]:
    """The per-category summary the calendar header and booking form show."""
    resolved = period or period_of(day) if (period or day) else None
    if resolved is None:
        raise ValueError("summary_for needs either a day or a period")
    return [serialise(balance) for balance in balances_for(user_id, resolved).values()]
