"""Project financials — spec 003 §4.

Pure arithmetic over plain values. COGS, margin and attributed revenue are
DERIVED here from time entries carrying their captured cost rate; nothing here
reads a database, and nothing money-shaped is ever stored.

Two properties matter more than any formula:

* **Incompleteness is loud.** A person with no cost rate produces COGS of
  *unknown*, not zero. A project total that silently omits an unrated person's
  hours reads as a better margin than the truth, and it will be quoted in a
  budget conversation as though it were the truth. Every result carries
  `complete` and names who is unrated.

* **Rates are the ones captured on each entry** (`cost_rate_snapshot`), never
  the person's current rate. A rate change next year must not re-price last
  year's project. This module is handed the snapshot and has no way to reach
  the live rate, which is the point.

Money is `Decimal`. Same reasoning as hours and half-days elsewhere: these
numbers get quoted, and binary floats accumulate error across thousands of
rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Effort:
    """One person's hours on one project, with the rate captured when logged.

    `rate` is None when the person had no cost rate at the time. That is a
    fact about the record, not a zero, and every calculation below treats it
    as such.
    """

    user_id: str
    hours: Decimal
    rate: Decimal | None


@dataclass(frozen=True)
class PersonLine:
    user_id: str
    hours: Decimal
    rate: Decimal | None
    cogs: Decimal | None  # None when unrated — never 0
    attributed_revenue: Decimal | None  # None when the project has no revenue


@dataclass(frozen=True)
class ProjectFinancials:
    revenue: Decimal | None
    total_hours: Decimal
    cogs: Decimal | None  # None when nobody is rated; partial sums are still reported as partial
    cogs_partial: Decimal  # what the rated people cost, regardless of completeness
    margin: Decimal | None  # only when revenue is known AND cogs is complete
    margin_pct: Decimal | None
    complete: bool
    unrated_user_ids: tuple[str, ...]
    people: tuple[PersonLine, ...] = field(default_factory=tuple)


def project_financials(revenue: Decimal | None, efforts: list[Effort]) -> ProjectFinancials:
    """Revenue, COGS and margin for one project — FR-FIN-04, FR-FIN-06.

    Attributed revenue is each person's share of the project's revenue in
    proportion to hours (spec 003 Q-06). It is a distribution of a known total,
    so it needs only hours, and is computed even when someone is unrated.

    COGS is a sum of costs, so an unrated person makes it incomplete. The
    partial figure is still returned — it is a true lower bound — but `cogs`
    proper is None and `margin` is not computed, because a margin built on a
    lower-bound cost is an upper-bound margin presented as a fact.
    """
    # Fold multiple efforts per person (several entries, several phases).
    hours_by_user: dict[str, Decimal] = {}
    rate_by_user: dict[str, Decimal | None] = {}
    cost_by_user: dict[str, Decimal | None] = {}
    for effort in efforts:
        hours_by_user[effort.user_id] = hours_by_user.get(effort.user_id, ZERO) + effort.hours
        # An entry with no snapshot marks the person as unrated for this project
        # even if other entries of theirs carry a rate: part of their cost is
        # genuinely unknown, and "mostly known" is not "known".
        if effort.rate is None:
            rate_by_user[effort.user_id] = None
            cost_by_user[effort.user_id] = None
        else:
            if effort.user_id not in rate_by_user:
                rate_by_user[effort.user_id] = effort.rate
                cost_by_user[effort.user_id] = ZERO
            if cost_by_user[effort.user_id] is not None:
                cost_by_user[effort.user_id] = (
                    cost_by_user[effort.user_id] + effort.hours * effort.rate
                )  # type: ignore[operator]

    total_hours = sum(hours_by_user.values(), ZERO)
    unrated = tuple(sorted(uid for uid, cost in cost_by_user.items() if cost is None))
    complete = not unrated and bool(efforts)

    people: list[PersonLine] = []
    for uid in sorted(hours_by_user):
        hours = hours_by_user[uid]
        cost = cost_by_user[uid]
        share = (
            money(revenue * hours / total_hours)
            if revenue is not None and total_hours > 0
            else None
        )
        people.append(
            PersonLine(
                user_id=uid,
                hours=hours,
                rate=rate_by_user[uid],
                cogs=money(cost) if cost is not None else None,
                attributed_revenue=share,
            )
        )

    cogs_partial = money(sum((c for c in cost_by_user.values() if c is not None), ZERO))
    cogs = cogs_partial if complete else None

    margin = money(revenue - cogs) if (revenue is not None and cogs is not None) else None
    margin_pct = (
        money(margin / revenue * Decimal("100"))
        if margin is not None and revenue and revenue > 0
        else None
    )

    return ProjectFinancials(
        revenue=money(revenue) if revenue is not None else None,
        total_hours=total_hours,
        cogs=cogs,
        cogs_partial=cogs_partial,
        margin=margin,
        margin_pct=margin_pct,
        complete=complete,
        unrated_user_ids=unrated,
        people=tuple(people),
    )


@dataclass(frozen=True)
class PersonTotals:
    user_id: str
    hours: Decimal
    cogs: Decimal | None
    attributed_revenue: Decimal
    projects: int
    complete: bool


def person_totals(per_project: list[ProjectFinancials]) -> list[PersonTotals]:
    """Each person across every project — FR-FIN-05.

    Sums the per-project lines. A person unrated on ANY project is incomplete
    here too, for the same reason: a total that quietly drops one project's
    cost is a better number than the truth.

    Sorted by user id, never by any money figure. Spec 003 §9: the same data
    answers "which employee is cheapest per hour", and ordering by a money
    column is how a table becomes that answer without anybody deciding it.
    """
    hours: dict[str, Decimal] = {}
    cogs: dict[str, Decimal | None] = {}
    revenue: dict[str, Decimal] = {}
    projects: dict[str, int] = {}

    for project in per_project:
        for line in project.people:
            uid = line.user_id
            hours[uid] = hours.get(uid, ZERO) + line.hours
            projects[uid] = projects.get(uid, 0) + 1
            revenue[uid] = revenue.get(uid, ZERO) + (line.attributed_revenue or ZERO)
            if line.cogs is None or cogs.get(uid, ZERO) is None:
                cogs[uid] = None
            else:
                cogs[uid] = cogs.get(uid, ZERO) + line.cogs  # type: ignore[operator]

    return [
        PersonTotals(
            user_id=uid,
            hours=hours[uid],
            cogs=money(cogs[uid]) if cogs[uid] is not None else None,
            attributed_revenue=money(revenue[uid]),
            projects=projects[uid],
            complete=cogs[uid] is not None,
        )
        for uid in sorted(hours)
    ]
