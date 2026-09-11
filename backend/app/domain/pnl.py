"""CTC, monthly cost, monthly revenue and profit — spec 005 §3.

Pure arithmetic over plain values. The service layer gathers periods, phases,
hours and allocations; everything money-shaped is derived here and nothing is
stored.

Three properties matter more than any formula:

* **The figure in force on the day.** A person's cost on a date is the CTC
  period covering that date — past entries at past rates, upcoming plans at
  upcoming rates. No snapshot, no "current rate applied to history".

* **Incompleteness is loud** (`003` FR-FIN-06, unchanged). A day with no
  period is unknown cost, never zero, and every result says so.

* **No ranking.** Per-person results are keyed by id and sorted by name by
  the caller. Nothing here orders by a money column.

Money is `Decimal`, quantised to the cent at the edges.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.domain.calendar import is_weekend

ZERO = Decimal("0")
CENT = Decimal("0.01")
MONTHS_PER_YEAR = Decimal("12")
HOURS_PER_WORKING_DAY = Decimal("8")


def money(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Months and working days
# ---------------------------------------------------------------------------


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) from start's month to end's month inclusive."""
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def working_days(start: date, end: date, holidays: set[date]) -> int:
    """Weekdays that are not declared holidays, inclusive. Leave is NOT removed:
    a person's monthly cost does not fall because they took a day off."""
    n = 0
    day = start
    while day <= end:
        if not is_weekend(day) and day not in holidays:
            n += 1
        day += timedelta(days=1)
    return n


def overlap(
    a_start: date, a_end: date | None, b_start: date, b_end: date
) -> tuple[date, date] | None:
    start = max(a_start, b_start)
    end = min(a_end, b_end) if a_end is not None else b_end
    return (start, end) if start <= end else None


# ---------------------------------------------------------------------------
# CTC periods — §3.1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    user_id: str
    annual_ctc: Decimal
    starts_on: date
    ends_on: date | None  # None = until further notice

    @property
    def monthly(self) -> Decimal:
        return self.annual_ctc / MONTHS_PER_YEAR

    def covers(self, day: date) -> bool:
        return self.starts_on <= day and (self.ends_on is None or day <= self.ends_on)


def period_on(periods: list[Period], day: date) -> Period | None:
    for period in periods:
        if period.covers(day):
            return period
    return None


def daily_cost(period: Period, month_working_days: int) -> Decimal | None:
    """Monthly CTC spread over the working days of the month the day is in."""
    if month_working_days <= 0:
        return None
    return period.monthly / Decimal(month_working_days)


def hourly_cost(period: Period, month_working_days: int) -> Decimal | None:
    daily = daily_cost(period, month_working_days)
    return None if daily is None else daily / HOURS_PER_WORKING_DAY


@dataclass(frozen=True)
class MonthCost:
    cost: Decimal  # what the covered days cost
    covered_days: int
    working_days: int

    @property
    def complete(self) -> bool:
        return self.working_days > 0 and self.covered_days == self.working_days

    @property
    def unknown(self) -> bool:
        """No period covers any working day — cost is unknown, never zero."""
        return self.working_days > 0 and self.covered_days == 0


def month_cost(periods: list[Period], first: date, last: date, holidays: set[date]) -> MonthCost:
    """A person's cost for one month, pro-rated by the working days each
    period covers — FR-CTC, spec 005 §3.4."""
    total_days = working_days(first, last, holidays)
    cost = ZERO
    covered = 0
    for period in periods:
        window = overlap(period.starts_on, period.ends_on, first, last)
        if window is None:
            continue
        days = working_days(window[0], window[1], holidays)
        covered += days
        if total_days:
            cost += period.monthly * Decimal(days) / Decimal(total_days)
    return MonthCost(cost=cost, covered_days=covered, working_days=total_days)


# ---------------------------------------------------------------------------
# Revenue by month — §3.2
# ---------------------------------------------------------------------------


def project_window(phases: list[tuple[date, date]]) -> tuple[date, date] | None:
    """Earliest phase start to latest phase end. None when there are no phases."""
    if not phases:
        return None
    return min(p[0] for p in phases), max(p[1] for p in phases)


def month_revenue(
    revenue: Decimal | None,
    window: tuple[date, date] | None,
    first: date,
    last: date,
    holidays: set[date],
) -> Decimal | None:
    """The project's revenue for one month: spread evenly over the working
    days of its timeline. None when there is no revenue or no timeline."""
    if revenue is None or window is None:
        return None
    total = working_days(window[0], window[1], holidays)
    if total == 0:
        return None
    inside = overlap(window[0], window[1], first, last)
    if inside is None:
        return ZERO
    return revenue * Decimal(working_days(inside[0], inside[1], holidays)) / Decimal(total)


def shares(weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Each key's fraction of the total. Empty when the total is zero — the
    caller reports that month as unattributed rather than inventing shares."""
    total = sum(weights.values(), ZERO)
    if total <= 0:
        return {}
    return {key: weight / total for key, weight in weights.items() if weight > 0}


# ---------------------------------------------------------------------------
# A cell of the monthly table — §3.4
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    revenue: Decimal
    cost: Decimal | None  # None = unknown (unrated)
    basis: str  # "actual" | "planned"
    complete: bool

    @property
    def profit(self) -> Decimal | None:
        return None if self.cost is None else self.revenue - self.cost

    @property
    def profit_pct(self) -> Decimal | None:
        if self.profit is None or self.revenue <= 0:
            return None
        return self.profit / self.revenue * Decimal("100")

    def as_dict(self) -> dict:
        return {
            "revenue": str(money(self.revenue)),
            "cost": None if self.cost is None else str(money(self.cost)),
            "profit": None if self.profit is None else str(money(self.profit)),
            "profit_pct": None if self.profit_pct is None else str(money(self.profit_pct)),
            "basis": self.basis,
            "complete": self.complete,
        }


def basis_for(first: date, last: date, today: date) -> str:
    """Past months are actual; the current and future months are planned (Q-05)."""
    return "actual" if last < today.replace(day=1) else "planned"
