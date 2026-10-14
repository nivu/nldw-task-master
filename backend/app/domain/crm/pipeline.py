"""The pipeline rules — spec 007 FR-CRM-PIPE-03 to -15, FR-CRM-OPP-02, -03,
FR-CRM-AUTO-01 (automation 9); Q-31, Q-33, Q-41, Q-60, Q-64; plan §5.3, §5.4.

Every rule a stage move obeys, decided here and nowhere else, so the web app,
the API and MCP are held to the same ones (FR-CRM-PIPE-10). `plan_move`
turns a request into the plan `crm_deal_transition` (031) writes in one
transaction, or into a refusal that names every missing field in one
sentence (plan §5.4). Pure: the service gathers the deal, its company and
the workspace's settings and passes them in.

The TVA side of a Labs deal (a project at Proposal, the handoff at Closed
Won, the release at Closed Lost) is S5's; this module plans the CRM side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from app.domain.crm.settings import STAGE_LABELS, STAGES

__all__ = [
    "CLOSED",
    "OPEN",
    "STAGES",
    "STAGE_LABELS",
    "Move",
    "company_effect_on_close",
    "company_effect_on_open",
    "default_probability",
    "may_move",
    "missing_sentence",
    "plan_move",
    "skipped_stages",
    "stage_requirements",
]

OPEN: Final = ("discovery", "qualification", "proposal", "negotiation")
CLOSED: Final = ("closed_won", "closed_lost")

#: FR-CRM-PIPE-11 — used when a workspace's setting lacks a stage.
DEFAULT_PROBABILITIES: Final = {
    "discovery": 10,
    "qualification": 25,
    "proposal": 50,
    "negotiation": 75,
    "closed_won": 100,
    "closed_lost": 0,
}

#: FR-CRM-PIPE-07 — what entering each stage requires. Closed Won in Labs
#: also needs the handoff (S5).
ENTRY: Final[dict[str, tuple[str, ...]]] = {
    "discovery": ("company_id", "owner_id", "next_step", "next_step_on"),
    "qualification": ("point_of_contact_id",),
    "proposal": ("amount", "currency", "expected_close_on"),
    "negotiation": ("proposal_sent_on",),
    "closed_won": (),
    "closed_lost": ("lost_reason_id",),
}
#: FR-CRM-PIPE-08 — a skipped stage's requirements apply only up to Proposal.
SKIPPABLE: Final = ("discovery", "qualification", "proposal")
#: FR-CRM-PIPE-10 — every open deal has both.
NEXT_STEP: Final = ("next_step", "next_step_on")

#: How a missing field is named in a refusal.
FIELD_NAMES: Final = {
    "company_id": "company",
    "owner_id": "owner",
    "next_step": "next step",
    "next_step_on": "next step date",
    "point_of_contact_id": "point of contact",
    "amount": "amount",
    "currency": "currency",
    "expected_close_on": "expected close date",
    "proposal_sent_on": "proposal sent date",
    "lost_reason_id": "lost reason",
}

#: FR-CRM-PIPE-04 — the lead stages a deal may be opened on.
OPENABLE: Final = ("qualified", "deal_open", "customer", "partner")
#: FR-CRM-PIPE-05 — what a person may choose when a loss closes the last deal.
ON_LOSS: Final = ("nurturing", "disqualified")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def skipped_stages(current: str, target: str) -> list[str]:
    """The stages a forward move passes over (FR-CRM-PIPE-08). A move
    backwards, out of a closed stage or into Closed Lost skips nothing."""
    if current not in OPEN or target == "closed_lost":
        return []
    order = STAGES[:5]
    start, end = order.index(current), order.index(target)
    return list(order[start + 1 : end]) if end > start else []


def stage_requirements(target: str, deal: dict[str, Any], skipped: list[str]) -> list[str]:
    """The fields `deal` (as it would be after the move) lacks to enter
    `target`, in the order a refusal names them (FR-CRM-PIPE-07, -08, -10).

    Closed Lost needs only its reason, whatever it skips (Q-31)."""
    if target == "closed_lost":
        wanted = list(ENTRY["closed_lost"])
    else:
        wanted = [f for s in skipped if s in SKIPPABLE for f in ENTRY[s]]
        wanted += ENTRY[target]
        if target in OPEN:
            wanted += NEXT_STEP
    seen: list[str] = []
    for name in wanted:
        if name not in seen and _blank(deal.get(name)):
            seen.append(name)
    return seen


def missing_sentence(stage: str, fields: list[str]) -> str:
    """'Proposal needs amount, currency and expected close date.' — every
    missing field, in one sentence (plan §5.4)."""
    names = [FIELD_NAMES.get(f, f.replace("_", " ")) for f in fields]
    return f"{STAGE_LABELS.get(stage, stage)} needs {_and(names)}."


def may_move(
    current: str,
    target: str,
    role: str,
    is_owner: bool,
    *,
    lost_from: str | None = None,
) -> str | None:
    """Whether this person may move the deal from `current` to `target`
    (spec §3.3, FR-CRM-PIPE-12, -13). None, or the refusal."""
    if target not in STAGES:
        return f"“{target}” is not a stage."
    if target == current:
        return f"The deal is already at {STAGE_LABELS[current]}."
    if role not in ("admin", "member"):
        return "Viewers cannot move deals."
    if current == "closed_won":
        if role != "admin":
            return "Only a workspace admin can reopen a won deal."
        if target != "negotiation":
            return "A won deal reopens to Negotiation."
    if current == "closed_lost":
        if role != "admin" and not is_owner:
            return "Only a workspace admin or the deal's owner can reopen a lost deal."
        if target not in OPEN:
            return "A lost deal reopens to an open stage."
        if lost_from and target != lost_from:
            return f"A lost deal reopens to {STAGE_LABELS[lost_from]}, the stage it was lost from."
    return None


def default_probability(stage: str, settings: dict[str, Any] | None) -> int:
    """FR-CRM-PIPE-11 — the workspace's default for the stage."""
    value = (settings or {}).get(stage)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
        return value
    return DEFAULT_PROBABILITIES[stage]


def company_effect_on_open(company: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """FR-CRM-PIPE-04, Q-60 — opening a deal needs the company at Qualified,
    Deal open, Customer or Partner. Only Qualified moves (to Deal open); a
    Customer or Partner keeps its stage. Returns (changes, refusal)."""
    stage = company.get("lead_stage")
    name = company.get("name", "The company")
    label = company.get("lead_stage_label") or str(stage).replace("_", " ").capitalize()
    if stage == "disqualified":
        return {}, f"{name} is Disqualified; a disqualified company takes no deal."
    if stage not in OPENABLE:
        return {}, f"A deal needs its company at Qualified or later; {name} is at {label}."
    return ({"lead_stage": "deal_open"} if stage == "qualified" else {}), None


def company_effect_on_close(
    company: dict[str, Any],
    outcome: str,
    other_open_deals: int,
    has_won: bool,
    choice: str | None,
    *,
    client_option_id: str | None = None,
) -> dict[str, Any]:
    """FR-CRM-PIPE-05, Q-60 — what the company becomes when this deal closes.

    Only the last open deal moves the company. Won: Customer and account
    type Client, except a Partner, which keeps both. Lost: a company at Deal
    open goes to Nurturing, or Disqualified if the person chose it, unless
    it has a won deal, which makes it a Customer; Customer and Partner are
    unchanged."""
    if other_open_deals > 0:
        return {}
    stage = company.get("lead_stage")
    if outcome == "closed_won":
        if stage == "partner":
            return {}
        changes: dict[str, Any] = {}
        if stage != "customer":
            changes["lead_stage"] = "customer"
        if client_option_id and company.get("account_type_id") != client_option_id:
            changes["account_type_id"] = client_option_id
        return changes
    if stage in ("customer", "partner"):
        return {}
    if has_won:
        return {"lead_stage": "customer"}
    if stage == "deal_open":
        return {"lead_stage": choice if choice in ON_LOSS else "nurturing"}
    return {}


@dataclass
class Move:
    """A planned move: the plan for 031, or why not."""

    allowed: bool
    message: str | None = None
    missing: list[str] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    would: dict[str, Any] = field(default_factory=dict)


def plan_move(
    *,
    deal: dict[str, Any],
    company: dict[str, Any],
    target: str,
    changes: dict[str, Any],
    role: str,
    user_id: str,
    probabilities: dict[str, Any] | None,
    other_open_deals: int,
    has_won: bool,
    company_on_loss: str | None,
    open_task_ids: list[str],
    lost_reason_label: str | None,
    today: date,
    client_option_id: str | None = None,
    expect_updated_at: str | None = None,
) -> Move:
    """FR-CRM-PIPE-07 to -15 — the move from the deal's stage to `target`,
    with the fields `changes` sets in the same save.

    `changes` holds stored columns: an amount arrives already converted to
    INR (domain/crm/fx.py)."""
    current = deal["stage"]
    why = may_move(
        current,
        target,
        role,
        deal.get("owner_id") == user_id,
        lost_from=deal.get("lost_from_stage"),
    )
    if why:
        return Move(False, why)
    if target == "closed_lost" and company_on_loss not in (None, *ON_LOSS):
        return Move(False, "After a loss the company goes to Nurturing or Disqualified.")
    if "lost_reason_id" in changes and target != "closed_lost":
        return Move(False, "A lost reason is set only by a move to Closed Lost.")

    after = {**deal, **changes}
    missing = stage_requirements(target, after, skipped_stages(current, target))
    if current == "closed_lost":
        # FR-CRM-PIPE-12: a reopen from Lost carries its next step and date in
        # the same save, whatever the deal had before it was lost.
        missing += [f for f in NEXT_STEP if _blank(changes.get(f)) and f not in missing]
    probability = changes.get("probability", default_probability(target, probabilities))

    values: dict[str, Any] = {
        **changes,
        "stage": target,
        "stage_entered_at": "now",
        "probability": probability,
    }
    if target == "closed_lost":
        values.update(lost_on=today.isoformat(), lost_from_stage=current)
    if target == "closed_won":
        values["won_on"] = today.isoformat()
    if current == "closed_lost":
        values.update(lost_on=None, lost_reason_id=None, lost_from_stage=None)
    if current == "closed_won":
        values["won_on"] = None

    company_changes: dict[str, Any] = {}
    if target in CLOSED:
        company_changes = company_effect_on_close(
            company,
            target,
            other_open_deals,
            has_won,
            company_on_loss,
            client_option_id=client_option_id,
        )
    closing = list(open_task_ids) if target == "closed_lost" else []
    would = {
        "probability": probability,
        "company_lead_stage": company_changes.get("lead_stage", company.get("lead_stage")),
        "tasks_closed": len(closing),
        "project": None,
    }
    if missing:
        return Move(False, missing_sentence(target, missing), missing, None, would)

    plan: dict[str, Any] = {
        "opportunity_id": deal["id"],
        "expect_stage": current,
        "expect_updated_at": expect_updated_at,
        "opportunity": values,
        "history": {"from": current, "to": target},
    }
    if company_changes:
        plan["company"] = {
            "id": company["id"],
            "expect_updated_at": company.get("updated_at"),
            "changes": company_changes,
        }
    if closing:
        plan["close_tasks"] = closing
        plan["close_note"] = {"body": f"Closed: deal lost ({lost_reason_label or 'no reason'})."}
    return Move(True, None, [], plan, would)


def _and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"
