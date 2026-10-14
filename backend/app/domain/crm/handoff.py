"""The Closed Won handoff — spec 007 FR-CRM-TVA-07 to -10, FR-CRM-FX-05,
FR-CRM-AUTO-01 (automation 10); owner decision D8; Q-09, Q-57, Q-59;
contracts §2.6; plan §5.6.

What the handoff offers (pre-filled from the deal and the project's existing
phases), what it accepts, and who gets which task when it is confirmed.
Pure: the service reads the deal, the project, the workspace's admins and
members and the FX rates, and passes them in.

Revenue and milestones are INR whatever the deal's currency (TVA is
single-currency, `003` Q-07), and are written only for a confirming person
who is an active staff account with TVA role manager or admin. Anyone else
confirms the project without them, and the Labs admins get a task to set
them in TVA (FR-CRM-TVA-08). TVA shows revenue to managers and admins alone,
so the pre-fill carries the project's existing revenue and milestone amounts
only for such a person.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Final

from app.domain import timesheets as tva_rules
from app.domain.crm import fx

#: FR-CRM-TVA-07 — the TVA phases the handoff maps onto; never `spillover`.
PHASE_KINDS: Final = ("pre", "delivery", "support")
#: The engagement model whose deals are offered a `pre` phase.
DISCOVERY_PHASE: Final = "discovery_phase"

REVENUE_TASK: Final = "Confirm revenue and milestones"
DELIVERY_CATEGORY: Final = "delivery_handoff"


def can_set_revenue(profile: dict[str, Any] | None) -> bool:
    """FR-CRM-TVA-08, D8 — an active staff account with TVA role manager or
    admin. A CRM-only seller never is, whatever their stored role."""
    return bool(
        profile
        and profile.get("is_active")
        and (profile.get("account_type") or "staff") == "staff"
        and profile.get("role") in ("manager", "admin")
    )


def offer_pre_phase(engagement_model_key: str | None) -> bool:
    """FR-CRM-TVA-07 — a `pre` phase is offered for a Discovery phase deal."""
    return engagement_model_key == DISCOVERY_PHASE


def lead_refusal(profile: dict[str, Any] | None) -> str | None:
    """The lead is an active staff account (FR-CRM-TVA-07, FR-CRM-CHG-02)."""
    return tva_rules.project_lead_refusal(profile)


def phases_refusal(phases: dict[str, dict[str, date] | None]) -> str | None:
    """A delivery phase is required; every phase ends on or after it starts;
    only pre, delivery and support (FR-CRM-TVA-07)."""
    given = {k: v for k, v in phases.items() if v is not None}
    unknown = sorted(set(given) - set(PHASE_KINDS))
    if unknown:
        return "The handoff sets only pre, delivery and support phases."
    if "delivery" not in given:
        return "The handoff needs the delivery phase's start and end dates."
    for kind in PHASE_KINDS:
        span = given.get(kind)
        if span and span["ends_on"] < span["starts_on"]:
            label = tva_rules.PHASE_LABELS[kind]
            return f"The {label.lower()} phase cannot end before it starts."
    return None


def existing_phases(phases: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """The project's phases the handoff pre-fills, by kind; spillover is not
    the CRM's to set, so it is not offered."""
    return {
        p["phase"]: {"starts_on": str(p["starts_on"]), "ends_on": str(p["ends_on"])}
        for p in phases
        if p["phase"] in PHASE_KINDS
    }


def won_fx(
    deal: dict[str, Any], rates: list[dict[str, Any]], on: date, workspace: str
) -> dict[str, Any] | str | None:
    """FR-CRM-FX-05 — the deal's amount in INR at the rate in force on the
    won date (same fallback and refusal as FR-CRM-FX-03): the columns the
    deal stores, or the refusal naming the missing rate. A deal without an
    amount (an imported one) has nothing to convert: None."""
    if deal.get("amount") is None or not deal.get("currency"):
        return None
    found = fx.rate_for(rates, deal["currency"], on)
    if found is None:
        return fx.missing_rate(deal["currency"], on, workspace)
    rate = format(found.rate.normalize(), "f")
    return {
        "amount_in_inr": format(fx.to_inr(deal["amount"], found.rate), "f"),
        "fx_rate_used": rate,
        "fx_month": found.month.isoformat(),
        "won_fx_rate": rate,
        "won_fx_month": found.month.isoformat(),
    }


def prefill(
    *,
    deal: dict[str, Any],
    project: dict[str, Any] | None,
    phases: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    won: dict[str, Any] | str | None,
    may_set_revenue: bool,
    engagement_model_key: str | None,
    leads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Contracts §2.6 `GET …/handoff`. `existing_tva_revenue` and the
    milestones' amounts are there only for someone who may set revenue; for
    anyone else the key is absent (TVA shows revenue to managers and admins
    alone, `003`). Each milestone carries its id: the confirm treats the list
    it is sent as the project's full set, matched by id."""
    revenue: dict[str, Any] = {
        "suggested_inr": won["amount_in_inr"] if isinstance(won, dict) else None,
        "won_fx_rate": won["won_fx_rate"] if isinstance(won, dict) else None,
        "won_fx_month": won["won_fx_month"] if isinstance(won, dict) else None,
    }
    if isinstance(won, str):
        revenue["message"] = won
    if may_set_revenue and project is not None and project.get("revenue") is not None:
        revenue["existing_tva_revenue"] = _money(project["revenue"])
    shown = (
        [
            {
                "id": m["id"],
                "name": m["name"],
                "due_on": str(m["due_on"]),
                "amount": _money(m["amount"]),
            }
            for m in milestones
        ]
        if may_set_revenue
        else []
    )
    out_project: dict[str, Any] = {
        "name": project["name"] if project else deal["name"],
        "category": (
            project.get("category") if project else (deal.get("project_category") or "client")
        ),
        "lead_id": project.get("lead_id") if project else None,
        "phases": existing_phases(phases),
    }
    if project:
        out_project["id"] = project["id"]
        out_project["status"] = project.get("status")
    return {
        "project": out_project,
        "revenue": revenue,
        "milestones": shown,
        "can_set_revenue": may_set_revenue,
        "offer_pre_phase": offer_pre_phase(engagement_model_key),
        "leads": leads,
    }


def milestone_difference(revenue: Decimal | None, milestones: list[dict[str, Any]]) -> str | None:
    """FR-CRM-TVA-07 — milestones add up to revenue, or the difference (in
    INR, revenue less the milestones) is shown. None when there is no
    revenue to compare with."""
    if revenue is None:
        return None
    total = sum((Decimal(str(m["amount"])) for m in milestones), Decimal(0))
    return _money(revenue - total)


def revenue_task_recipients(admins: list[dict[str, Any]]) -> list[str]:
    """FR-CRM-TVA-08, Q-09 — the Labs workspace admins who are TVA managers
    or admins, or every Labs admin when none is. `admins` are their profiles."""
    able = [a["id"] for a in admins if can_set_revenue(a)]
    return able or [a["id"] for a in admins]


def delivery_assignee(lead_id: str, members: set[str], owner_id: str) -> tuple[str, bool]:
    """FR-CRM-TVA-09, Q-57 — the project lead when they are a Labs member of
    any role (a Viewer may mark it done), else the deal owner. Returns the
    assignee and whether the task must name the lead."""
    if lead_id in members:
        return lead_id, False
    return owner_id, True


def revenue_task(project_name: str, deal_id: str, assignee_id: str) -> dict[str, Any]:
    """The task a confirm without revenue leaves each recipient (D8)."""
    return {
        "title": REVENUE_TASK,
        "body": (
            f"{project_name} was confirmed without revenue or milestones. A TVA manager or "
            "admin sets them in TVA Projects, in INR."
        ),
        "assignee_id": assignee_id,
        "bucket": "this_week",
        "links": [{"record_type": "opportunity", "record_id": deal_id}],
    }


def delivery_task(
    project_name: str, lead_name: str | None, deal_id: str, assignee_id: str, name_lead: bool
) -> dict[str, Any]:
    """Automation 10 — schedule the delivery kickoff (FR-CRM-TVA-09)."""
    body = f"Schedule the delivery kickoff for {project_name}."
    if name_lead:
        body += f" {lead_name or 'The project lead'} leads it; arrange the kickoff with them."
    return {
        "title": "Delivery handoff: schedule the kickoff",
        "body": body,
        "assignee_id": assignee_id,
        "category_key": DELIVERY_CATEGORY,
        "bucket": "this_week",
        "links": [{"record_type": "opportunity", "record_id": deal_id}],
    }


def _money(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")
