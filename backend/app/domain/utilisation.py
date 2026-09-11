"""Utilisation, bench and the hiring signal — spec 006 FR-UTIL, FR-HIRE.

Pure arithmetic. Billable is decided by the caller (Q-04: projects with a
client); this module only sums. Nothing here ranks anybody.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

HOURS_PER_DAY = Decimal("8")
HUNDRED = Decimal("100")
ZERO = Decimal("0")


def pct(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole <= 0:
        return None
    return (part / whole * HUNDRED).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MonthUtilisation:
    billable: Decimal
    internal: Decimal
    activity: Decimal
    capacity: Decimal  # hours: (working days − leave) × 8

    @property
    def logged(self) -> Decimal:
        return self.billable + self.internal + self.activity

    @property
    def utilisation_pct(self) -> Decimal | None:
        return pct(self.billable, self.capacity)

    def as_dict(self, target: Decimal) -> dict:
        u = self.utilisation_pct
        return {
            "billable": str(self.billable),
            "internal": str(self.internal),
            "activity": str(self.activity),
            "logged": str(self.logged),
            "capacity": str(self.capacity),
            "utilisation_pct": None if u is None else str(u),
            "below_target": u is not None and u < target,
        }


def capacity_hours(working_days: Decimal) -> Decimal:
    """Working days already net of leave (`timesheets.working_days`)."""
    return working_days * HOURS_PER_DAY


@dataclass(frozen=True)
class HiringMonth:
    period: str
    demand_hours: Decimal
    supply_hours: Decimal  # capacity × target utilisation
    hours_per_fte: Decimal  # one person's capacity × target

    @property
    def shortfall_hours(self) -> Decimal:
        return max(ZERO, self.demand_hours - self.supply_hours)

    @property
    def fte_needed(self) -> Decimal:
        if self.hours_per_fte <= 0:
            return ZERO
        return (self.shortfall_hours / self.hours_per_fte).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )

    def cost_at(self, annual_ctc: Decimal) -> Decimal:
        return (self.fte_needed * annual_ctc / Decimal("12")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def as_dict(self, annual_ctc: Decimal) -> dict:
        return {
            "period": self.period,
            "demand_hours": str(self.demand_hours.quantize(Decimal("0.1"))),
            "supply_hours": str(self.supply_hours.quantize(Decimal("0.1"))),
            "shortfall_hours": str(self.shortfall_hours.quantize(Decimal("0.1"))),
            "fte_needed": str(self.fte_needed),
            "monthly_cost_at_ctc": str(self.cost_at(annual_ctc)),
        }
