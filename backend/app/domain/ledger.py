"""The balance ledger — FR-BAL, §7.3.

Balances are **derived, never stored**. There is no `remaining_days` column
anywhere in this system, and adding one would be a bug: a counter drifts the
moment any code path forgets to decrement it, and the drift is invisible until
somebody's leave is wrongly refused. Recomputing from grants and bookings costs
nothing at this scale (NFR-07: tens of users) and can always be audited.

This module is pure arithmetic over plain values. It performs no I/O, which is
what lets the carry-forward policy — spec Q-02, still formally open — be
exercised in both directions by a unit test instead of by a migration.

Everything is `Decimal`. §6.2 requires half-day granularity, and binary floats
cannot represent 0.5 sums reliably across many additions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.domain.calendar import periods_between, previous_period

ZERO = Decimal("0.0")

#: Q-02. Two candidate behaviours were stated on the call and neither has been
#: chosen. Both are implemented; `app_settings.carry_forward_policy` selects.
ROLLING = "rolling"
POOLING = "pooling"


@dataclass(frozen=True)
class Grant:
    """A row of the `allowances` table — what was *granted*, never what is left.

    `user_id is None` marks the organisation default for that period, which is
    what an admin sets in the normal case (FR-ADMIN-01). A row with a user_id
    overrides the default for that one person.
    """

    period: str
    category: str
    days: Decimal
    user_id: str | None


@dataclass(frozen=True)
class Consumption:
    """A booking that draws down an allowance.

    Only `pending` and `approved` bookings become one of these (§6.4); the
    caller filters. Keeping the status out of this type makes it impossible to
    accidentally count a withdrawn day.
    """

    period: str
    category: str
    duration: Decimal


@dataclass(frozen=True)
class Balance:
    """What FR-BAL-06 requires the product to display, per category."""

    category: str
    period: str
    opening: Decimal
    allowance: Decimal
    used: Decimal
    remaining: Decimal


def resolve_allowance(grants: Iterable[Grant], period: str, category: str) -> Decimal:
    """How many days were granted for this person, period and category.

    Resolution order (spec A-12):

    1. a row for this exact person and period — a personal override;
    2. the organisation default for this exact period;
    3. **the most recent organisation default from any earlier period**;
    4. zero.

    Step 3 is the part worth pausing on. Without it, every month begins with a
    zero allowance until an admin explicitly sets one, so on the 1st nobody can
    book anything and nobody can book *next* month at all. With it, an admin
    setting an allowance establishes a standing policy that continues until
    they change it — which is how the people using this expect it to behave.

    Later grants for a period never disturb a closed one (FR-BAL-07): this
    reads the grant for the period being asked about and no other.
    """
    grants = list(grants)

    for grant in grants:
        if grant.user_id is not None and grant.period == period and grant.category == category:
            return grant.days

    for grant in grants:
        if grant.user_id is None and grant.period == period and grant.category == category:
            return grant.days

    earlier = [
        grant
        for grant in grants
        if grant.user_id is None and grant.category == category and grant.period < period
    ]
    if earlier:
        return max(earlier, key=lambda grant: grant.period).days

    return ZERO


def consumed(entries: Iterable[Consumption], period: str, category: str) -> Decimal:
    """Days drawn down in a period. Half-days included, hence Decimal."""
    return sum(
        (
            entry.duration
            for entry in entries
            if entry.period == period and entry.category == category
        ),
        ZERO,
    )


def opening_balance(
    grants: Iterable[Grant],
    entries: Iterable[Consumption],
    period: str,
    category: str,
    *,
    policy: str,
    tracking_start: str,
) -> Decimal:
    """What carried into this period from earlier ones — FR-BAL-05, Q-02.

    Unused allowance is carried forward rather than forfeited at month end. The
    two candidate readings of "carried forward" differ only in where the
    accumulation starts:

    * ``rolling`` — from the beginning of tracking, never reset. February's
      leftover is still available in November.
    * ``pooling`` — from January of the period's own year, so the pool resets
      annually and a person draws a longer holiday from what the year has
      accrued so far.

    Both reduce to the same sum over a different window, which is exactly why
    §7.3 insists this be a function over the ledger and not a stored column:
    answering Q-02 changes one bound, not the schema.
    """
    grants, entries = list(grants), list(entries)

    start = tracking_start
    if policy == POOLING:
        start = max(start, f"{period[:4]}-01")

    return sum(
        (
            resolve_allowance(grants, earlier, category) - consumed(entries, earlier, category)
            for earlier in periods_between(start, previous_period(period))
        ),
        ZERO,
    )


def balance_for(
    grants: Iterable[Grant],
    entries: Iterable[Consumption],
    period: str,
    category: str,
    *,
    policy: str = ROLLING,
    tracking_start: str | None = None,
) -> Balance:
    """The full picture for one category in one period — FR-BAL-06."""
    grants, entries = list(grants), list(entries)
    start = tracking_start or earliest_period(grants, entries, default=period)

    opening = opening_balance(
        grants, entries, period, category, policy=policy, tracking_start=start
    )
    allowance = resolve_allowance(grants, period, category)
    used = consumed(entries, period, category)

    return Balance(
        category=category,
        period=period,
        opening=opening,
        allowance=allowance,
        used=used,
        remaining=opening + allowance - used,
    )


def earliest_period(
    grants: Iterable[Grant], entries: Iterable[Consumption], *, default: str
) -> str:
    """The first period this person has any ledger history in.

    Where carry-forward starts accumulating. Falling back to `default` (the
    period being asked about) gives a new joiner an opening balance of zero
    rather than one inherited from months before they existed.
    """
    periods = [grant.period for grant in grants] + [entry.period for entry in entries]
    return min(periods) if periods else default


def year_history(
    entries: Iterable[Consumption], year: str, categories: Iterable[str]
) -> dict[str, dict[str, Decimal]]:
    """Consumption per period per category for one year — FR-BAL-08.

    Shaped as ``{period: {category: days}}`` with every month of the year
    present, so the frontend renders a complete year without having to invent
    the empty months itself.
    """
    categories = list(categories)
    months = [f"{year}-{month:02d}" for month in range(1, 13)]
    entries = list(entries)
    return {
        period: {category: consumed(entries, period, category) for category in categories}
        for period in months
    }
