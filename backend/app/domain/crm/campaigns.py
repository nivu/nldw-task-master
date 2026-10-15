"""Campaigns' rules — spec 007 FR-CRM-CAMP-01 to -07, FR-CRM-PER-02,
FR-CRM-AUTO-01 (automation 8), FR-CRM-FX-03, FR-CRM-ACC-11; Q-61;
data-model §3.23.

Pure. Who may be added to a campaign (a company or a person, never someone
marked Do not contact, and once), how a member moves through the statuses
(Converted only through a conversion, which names the deal), what a
conversion writes (the member's deal and, with automation 8 on, the deal's
source campaign), the budget in INR at the month's rate, and the figures a
campaign page shows, derived from its members' converted deals.

Sends happen outside the CRM (FR-CRM-CAMP-07): nothing here sends.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from app.domain.crm import fx
from app.domain.crm.objects import MEMBER_STATUSES

CENT: Final = Decimal("0.01")

#: Spec §6.6, for a label the workspace's list does not have.
STATUS_LABELS: Final = {
    "targeted": "Targeted",
    "invited": "Invited",
    "registered": "Registered",
    "attended": "Attended",
    "engaged": "Engaged",
    "meeting_booked": "Meeting booked",
    "converted": "Converted",
    "not_interested": "Not interested",
    "unsubscribed": "Unsubscribed",
}

NEEDS_SOMEONE: Final = "A campaign member needs a company or a person."
USE_CONVERT: Final = "Move a member to Converted by converting it, which names its deal."
STAYS_CONVERTED: Final = "A converted member stays Converted; its deal keeps the campaign."
ALREADY_CONVERTED: Final = "This member is already converted."
NAME_ONE_DEAL: Final = "Name an existing deal or the new one's fields, not both."
NAME_A_DEAL: Final = "Name the deal: an existing one, or the new one's fields."
#: Members added in one request (contracts §2.10; a view is read up to it).
MAX_ADD: Final = 2000
TOO_MANY: Final = f"Add at most {MAX_ADD:,} members at a time; narrow the view first."


def do_not_contact(name: str) -> str:
    """FR-CRM-PER-02 — refused, never silently dropped, on its own."""
    return f"{name} is marked Do not contact, so they are not added to campaigns."


def already_member(name: str) -> str:
    return f"{name} is already in this campaign."


def dates_refusal(starts_on: Any, ends_on: Any) -> str | None:
    """Spec §6.6 — a campaign ends on or after it starts."""
    if starts_on and ends_on and str(ends_on) < str(starts_on):
        return "A campaign's end date cannot be before its start date."
    return None


def status_refusal(current: str, wanted: str) -> str | None:
    """FR-CRM-CAMP-02, -04 — the board moves a member between statuses, but
    Converted is reached only by converting (it needs the deal), and a
    converted member stays so: its deal carries the campaign."""
    if wanted not in MEMBER_STATUSES:
        return "That is not a member status."
    if wanted == current:
        return None
    if wanted == "converted":
        return USE_CONVERT
    if current == "converted":
        return STAYS_CONVERTED
    return None


@dataclass(frozen=True)
class Addition:
    """What adding these companies and people would do (FR-CRM-CAMP-03)."""

    #: (company_id, person_id) pairs to insert, in the order asked.
    add: list[tuple[str | None, str | None]]
    #: People skipped for Do not contact (FR-CRM-PER-02), by id.
    do_not_contact: list[str]
    #: Records already in the campaign, by id.
    existing: list[str]


def plan_additions(
    company_ids: Iterable[str],
    people: Iterable[dict[str, Any]],
    members: Iterable[dict[str, Any]],
) -> Addition:
    """A company joins as itself and a person as themselves, each once per
    campaign; a person marked Do not contact is skipped, and the history of
    their earlier memberships is untouched (FR-CRM-PER-02)."""
    have = {(m.get("company_id"), m.get("person_id")) for m in members}
    add: list[tuple[str | None, str | None]] = []
    existing: list[str] = []
    skipped: list[str] = []
    for company_id in dict.fromkeys(company_ids):
        pair = (company_id, None)
        if pair in have:
            existing.append(company_id)
        else:
            have.add(pair)
            add.append(pair)
    for person in {p["id"]: p for p in people}.values():
        pair = (None, person["id"])
        if person.get("do_not_contact"):
            skipped.append(person["id"])
        elif pair in have:
            existing.append(person["id"])
        else:
            have.add(pair)
            add.append(pair)
    return Addition(add=add, do_not_contact=skipped, existing=existing)


def conversion(
    member: dict[str, Any], deal: dict[str, Any], *, attribution: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """FR-CRM-CAMP-04, automation 8 — the member's changes and the deal's.
    The member is Converted with its deal; with automation 8 on, the deal
    records the campaign as its source (FR-CRM-AUTO-02: off leaves the deal
    alone)."""
    member_changes = {"status": "converted", "converted_opportunity_id": deal["id"]}
    deal_changes = (
        {"source_campaign_id": member["campaign_id"]}
        if attribution and deal.get("source_campaign_id") != member["campaign_id"]
        else {}
    )
    return member_changes, deal_changes


def budget_values(
    rates: list[dict[str, Any]], budget: Any, currency: str | None, on: date, workspace: str
) -> dict[str, Any] | str:
    """FR-CRM-FX-03 for a budget: its INR value at the rate for the month of
    the save, or the latest earlier one; the refusal names a missing rate.
    No budget clears the currency and conversion with it."""
    if budget is None:
        if currency:
            return "A budget currency needs a budget."
        return {
            "budget": None,
            "budget_currency": None,
            "budget_in_inr": None,
            "budget_fx_rate": None,
        }
    if not currency:
        return "A budget needs a currency."
    found = fx.converted(rates, budget, currency, on, workspace)
    if isinstance(found, str):
        return found
    return {
        "budget": found["amount"],
        "budget_currency": found["currency"],
        "budget_in_inr": found["amount_in_inr"],
        "budget_fx_rate": found["fx_rate_used"],
    }


def _money(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def figures(
    members: list[dict[str, Any]],
    deals: dict[str, dict[str, Any]],
    budget_in_inr: Any,
) -> dict[str, Any]:
    """FR-CRM-CAMP-05 — member counts by status, conversion rate (converted
    members over all, as a percentage with one decimal), converted deals,
    pipeline created (the INR value of every live deal its members
    converted to), won value (those Closed Won) and, with a budget, cost per
    converted member. `deals` are the converted deals by id; one in the
    Trash is not counted. Money is INR, decimal strings; the amounts are
    redacted for a Viewer by the presenter (FR-CRM-ACC-11)."""
    live = [m for m in members if not m.get("deleted_at")]
    counts = dict.fromkeys(MEMBER_STATUSES, 0)
    for m in live:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    converted = counts["converted"]
    deal_ids = {
        m["converted_opportunity_id"]
        for m in live
        if m.get("converted_opportunity_id")
        and m["converted_opportunity_id"] in deals
        and not deals[m["converted_opportunity_id"]].get("deleted_at")
    }
    pipeline = Decimal(0)
    won = Decimal(0)
    for deal_id in deal_ids:
        deal = deals[deal_id]
        amount = deal.get("amount_in_inr")
        value = Decimal(str(amount)) if amount is not None else Decimal(0)
        pipeline += value
        if deal.get("stage") == "closed_won":
            won += value
    rate = (
        format((Decimal(converted) * 100 / Decimal(len(live))).quantize(Decimal("0.1")), "f")
        if live
        else None
    )
    cost = (
        _money(Decimal(str(budget_in_inr)) / converted)
        if budget_in_inr is not None and converted
        else None
    )
    return {
        "member_count": len(live),
        "member_counts": counts,
        "converted_members": converted,
        "conversion_rate": rate,
        "converted_deals": len(deal_ids),
        "pipeline_created": _money(pipeline),
        "won_value": _money(won),
        "cost_per_converted": cost,
    }
