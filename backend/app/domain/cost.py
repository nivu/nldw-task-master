"""What a booking costs — §6.2, and spec Q-09.

§6.2 is short: a full day costs 1.0, a half day 0.5, and holidays and
non-working days cost 0. The interesting part is Q-09.

Q-09 — the "sandwich" rule — asks whether leave days spanning a weekend consume
the weekend too. It had no provisional answer in the source specification and
nobody has asked for the behaviour. Our default is that weekends never consume,
which falls out for free: a weekend cannot be booked at all (FR-BOOK-04), so no
booking row exists for it and the ledger never sees it.

The `true` branch is deliberately **not implemented**. The constitution's YAGNI
principle forbids building for a policy nobody has chosen, and a half-built
bridging rule that silently miscounts is worse than an honest refusal. The
setting exists so the question is visible in the product rather than buried in a
document; turning it on is a small change here, not a migration.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.calendar import is_weekend

FULL_DAY = Decimal("1.0")
HALF_DAY = Decimal("0.5")
NO_COST = Decimal("0.0")


class SandwichRuleNotImplemented(NotImplementedError):
    """Raised when `app_settings.sandwich_rule` is switched on.

    Loud on purpose. Silently ignoring the setting would let an admin believe
    weekends were being counted when they were not, and the error only shows up
    as a balance that is quietly too generous.
    """

    def __init__(self) -> None:
        super().__init__(
            "sandwich_rule is enabled but no bridging behaviour is implemented "
            "(spec Q-09 is unanswered). Either answer Q-09 and implement "
            "app.domain.cost.bridging_days, or set app_settings.sandwich_rule "
            "back to false."
        )


def cost_of(day: date, duration: Decimal, *, holiday_name: str | None = None) -> Decimal:
    """The allowance a single booked day draws down.

    Holidays and weekends cost nothing (§6.2, FR-HOL-03). In practice a booking
    on one is refused before it reaches here (FR-BOOK-04); this stays correct
    anyway for the case that matters — an admin declaring a holiday over a date
    that was already booked (FR-HOL-05), where the day must stop costing
    without the booking row being deleted.
    """
    if holiday_name or is_weekend(day):
        return NO_COST
    return duration


def bridging_days(start: date, end: date, *, sandwich_rule: bool) -> list[date]:
    """Non-working days between two leave days that the sandwich rule would charge.

    Returns an empty list under the shipped default. Raises if the setting is
    switched on, because the behaviour it promises does not exist yet.
    """
    if not sandwich_rule:
        return []
    raise SandwichRuleNotImplemented()
