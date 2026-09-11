"""Project health — spec 006 FR-HEALTH.

Three dimensions, each a colour with its inputs beside it so a red can be
argued with (FR-HEALTH-03). Pure: hand it the numbers, get the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

GREEN, AMBER, RED = "green", "amber", "red"
RANK = {GREEN: 0, AMBER: 1, RED: 2}
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Dimension:
    key: str
    colour: str
    detail: str
    inputs: dict


def burn(logged: Decimal, budget: Decimal | None, elapsed_pct: Decimal | None) -> Dimension:
    """Hours spent against hours budgeted, judged against how far along the
    timeline is. Spending 60% of the budget 40% of the way through is amber;
    spending it all is red regardless of the calendar."""
    inputs = {
        "logged_hours": str(logged),
        "budget_hours": None if budget is None else str(budget),
        "elapsed_pct": None if elapsed_pct is None else str(elapsed_pct),
    }
    if budget is None or budget <= 0:
        return Dimension("burn", AMBER, "No hours budget set, so burn cannot be judged.", inputs)
    burn_pct = logged / budget * HUNDRED
    inputs["burn_pct"] = str(burn_pct.quantize(Decimal("0.1")))
    if burn_pct > HUNDRED:
        return Dimension("burn", RED, "Over budget.", inputs)
    if elapsed_pct is None:
        return Dimension("burn", AMBER, "No timeline to judge burn against.", inputs)
    gap = burn_pct - elapsed_pct
    if gap <= 10:
        return Dimension("burn", GREEN, "Burn is in step with the timeline.", inputs)
    if gap <= 25:
        return Dimension("burn", AMBER, "Burning faster than the timeline.", inputs)
    return Dimension("burn", RED, "Burning far faster than the timeline.", inputs)


def margin(to_date_pct: Decimal | None, planned_pct: Decimal | None, complete: bool) -> Dimension:
    inputs = {
        "margin_to_date_pct": None if to_date_pct is None else str(to_date_pct),
        "planned_margin_pct": None if planned_pct is None else str(planned_pct),
        "complete": complete,
    }
    if to_date_pct is None or planned_pct is None:
        return Dimension(
            "margin", AMBER, "Not enough to judge: revenue, CTC or a timeline is missing.", inputs
        )
    if not complete:
        return Dimension(
            "margin", AMBER, "Cost is incomplete — somebody has no CTC for these days.", inputs
        )
    gap = to_date_pct - planned_pct
    if gap >= -5:
        return Dimension("margin", GREEN, "Margin to date is on plan.", inputs)
    if gap >= -15:
        return Dimension("margin", AMBER, "Margin to date is below plan.", inputs)
    return Dimension("margin", RED, "Margin to date is well below plan.", inputs)


def schedule(elapsed_pct: Decimal | None, burn_pct: Decimal | None, archived: bool) -> Dimension:
    inputs = {
        "elapsed_pct": None if elapsed_pct is None else str(elapsed_pct),
        "burn_pct": None if burn_pct is None else str(burn_pct),
    }
    if elapsed_pct is None:
        return Dimension("schedule", AMBER, "No phases, so no timeline.", inputs)
    if archived:
        return Dimension("schedule", GREEN, "Archived.", inputs)
    if elapsed_pct >= HUNDRED and (burn_pct is None or burn_pct < 90):
        return Dimension(
            "schedule", RED, "Past the timeline end with budget unspent — is it finished?", inputs
        )
    if elapsed_pct >= HUNDRED:
        return Dimension("schedule", AMBER, "Past the timeline end.", inputs)
    return Dimension("schedule", GREEN, "Inside the timeline.", inputs)


def overall(dimensions: list[Dimension]) -> str:
    return max((d.colour for d in dimensions), key=lambda c: RANK[c], default=AMBER)
