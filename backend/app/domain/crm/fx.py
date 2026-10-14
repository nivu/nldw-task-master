"""Deal currency — spec 007 FR-CRM-FX-01, -03, -04; owner decision D7; Q-59;
data-model §3.5, §3.9.

A deal's amount is in INR, AUD or USD. On every save that sets or changes it,
the deal stores its amount in INR at the rate for the month of the save, or
the latest earlier month that has one; INR converts at 1. With no such rate
at all, the save is refused, naming the currency, the month and the
workspace. A rate edited later never recomputes what was stored. Pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Final

#: FR-CRM-FX-01.
CURRENCIES: Final = ("INR", "AUD", "USD")
CENT: Final = Decimal("0.01")


@dataclass(frozen=True)
class Rate:
    """INR per one unit, and the first day of the month it is for."""

    rate: Decimal
    month: date


def month_of(on: date) -> date:
    return on.replace(day=1)


def rate_for(rates: list[dict[str, Any]], currency: str, on: date) -> Rate | None:
    """The rate for `on`'s month, or the latest earlier month with one
    (FR-CRM-FX-03). `rates` are the workspace's `crm_fx_rates` rows. INR is
    always 1, for the month of `on`."""
    if currency == "INR":
        return Rate(Decimal(1), month_of(on))
    wanted = month_of(on)
    found = [
        (date.fromisoformat(str(r["month"])[:10]), r["rate"])
        for r in rates
        if r["currency"] == currency and date.fromisoformat(str(r["month"])[:10]) <= wanted
    ]
    if not found:
        return None
    month, rate = max(found, key=lambda pair: pair[0])
    return Rate(Decimal(str(rate)), month)


def to_inr(amount: Decimal | str, rate: Decimal | str) -> Decimal:
    """`round(amount * rate, 2)` exactly as Postgres computes it for these
    non-negative numerics: the product is exact, then rounded half up
    (data-model §3.9, the FX integrity CHECK)."""
    with localcontext() as ctx:
        ctx.prec = 50
        product = Decimal(str(amount)) * Decimal(str(rate))
        return product.quantize(CENT, rounding=ROUND_HALF_UP)


def missing_rate(currency: str, on: date, workspace: str) -> str:
    """FR-CRM-FX-03 — the refusal names the missing rate."""
    return (
        f"No {currency} rate for {on:%Y-%m} or any earlier month in {workspace}; "
        "a workspace admin sets it in Settings."
    )


def converted(
    rates: list[dict[str, Any]], amount: Any, currency: str, on: date, workspace: str
) -> dict[str, Any] | str:
    """The stored columns for an amount saved on `on`, or the refusal.

    Money and rates are decimal strings, as on the wire."""
    found = rate_for(rates, currency, on)
    if found is None:
        return missing_rate(currency, on, workspace)
    value = Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "amount": format(value, "f"),
        "currency": currency,
        "amount_in_inr": format(to_inr(value, found.rate), "f"),
        "fx_rate_used": format(found.rate.normalize(), "f"),
        "fx_month": found.month.isoformat(),
    }


#: The columns an amount carries with it; clearing the amount clears them.
CLEARED: Final = {
    "amount": None,
    "currency": None,
    "amount_in_inr": None,
    "fx_rate_used": None,
    "fx_month": None,
}
