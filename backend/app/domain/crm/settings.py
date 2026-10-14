"""Workspace settings and select lists — spec 007 FR-CRM-LIST-01 to -06, -09,
FR-CRM-PIPE-11, FR-CRM-FX-02.

Pure. Each function returns None when a change is allowed, or one sentence
naming what is wrong. The lists themselves are rows of `crm_select_options`,
seeded per workspace by migration 026; this module knows which lists exist,
which are fixed and where each one applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

Scope = Literal["all", "tva_linked", "not_tva_linked"]

#: The fixed opportunity stages, in pipeline order (FR-CRM-LIST-02, Q-71).
STAGES: Final = (
    "discovery",
    "qualification",
    "proposal",
    "negotiation",
    "closed_won",
    "closed_lost",
)
STAGE_LABELS: Final = {
    "discovery": "Discovery",
    "qualification": "Qualification",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closed_won": "Closed Won",
    "closed_lost": "Closed Lost",
}

#: Mapped to CSS tokens in the frontend (data-model §3.4).
COLORS: Final = (
    "gray",
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "indigo",
    "purple",
    "pink",
)

#: Currencies with a monthly rate. INR converts at 1 and has none (FR-CRM-FX-01).
FX_CURRENCIES: Final = ("AUD", "USD")


@dataclass(frozen=True)
class ListSpec:
    label: str
    fixed: bool = False
    scope: Scope = "all"


#: Data-model §3.4.1. Campaign lists arrive with campaigns (1b).
LISTS: Final[dict[str, ListSpec]] = {
    "company.lead_stage": ListSpec("Lead stage", fixed=True),
    "opportunity.stage": ListSpec("Stage", fixed=True),
    "task.status": ListSpec("Task status", fixed=True),
    "task.bucket": ListSpec("Priority bucket", fixed=True),
    "company.account_type": ListSpec("Account type"),
    "company.relationship_status": ListSpec("Relationship status"),
    "channel": ListSpec("Channel"),
    "company.industry": ListSpec("Industry"),
    # Q-40: colleges and programmes are Academy's.
    "company.institution_type": ListSpec("Institution type", scope="not_tva_linked"),
    "company.size": ListSpec("Size"),
    "source": ListSpec("Source"),
    "opportunity.deal_type": ListSpec("Deal type"),
    "opportunity.engagement_model": ListSpec("Engagement model"),
    "opportunity.programme": ListSpec("Programme", scope="not_tva_linked"),
    "opportunity.lost_reason": ListSpec("Lost reason"),
    "opportunity.contact_role": ListSpec("Role in deal"),
    "task.category": ListSpec("Task category"),
}


def lists_for(*, tva_linked: bool) -> list[str]:
    """The list keys a workspace has, in the order above."""
    return [key for key, spec in LISTS.items() if _applies(spec, tva_linked)]


def _applies(spec: ListSpec, tva_linked: bool) -> bool:
    if spec.scope == "tva_linked":
        return tva_linked
    if spec.scope == "not_tva_linked":
        return not tva_linked
    return True


def list_refusal(list_key: str, *, tva_linked: bool, workspace_name: str) -> str | None:
    """A list this workspace does not have is refused like an unknown one."""
    spec = LISTS.get(list_key)
    if spec is None:
        return "No such list."
    if not _applies(spec, tva_linked):
        return f"{spec.label} is not used in {workspace_name}."
    return None


def add_option_refusal(list_key: str) -> str | None:
    """FR-CRM-LIST-02, Q-30 — a fixed list takes no new option."""
    spec = LISTS[list_key]
    if spec.fixed:
        return f"{spec.label} is a fixed list; its options can be renamed and recoloured only."
    return None


def option_change_refusal(option: dict[str, Any], changes: dict[str, Any]) -> str | None:
    """FR-CRM-LIST-01, -02, -09 — what may change on an existing option.

    A fixed list's option is renamed and recoloured, nothing else (Q-30). An
    option a rule reads (it has a system key) may be anything but switched
    off (Q-71).
    """
    if option["is_fixed"] and set(changes) - {"label", "color"}:
        return "Stages, lead stages, task status and bucket can be renamed and recoloured only."
    if option.get("system_key") and changes.get("is_active") is False:
        return (
            f"“{option['label']}” is used by a rule; it can be renamed and recoloured "
            "but not deactivated."
        )
    # A deactivated option leaves the pickers, so it cannot be what a new
    # record is pre-filled with.
    is_default = changes.get("is_default", option["is_default"])
    is_active = changes.get("is_active", option["is_active"])
    if is_default and not is_active:
        if option["is_default"]:
            return (
                f"“{option['label']}” is this list's default; choose another default "
                "before deactivating it."
            )
        return f"“{option['label']}” is inactive; reactivate it before making it the default."
    return None


def reorder(options: list[dict[str, Any]], option_id: str, position: int) -> dict[str, int]:
    """FR-CRM-LIST-01 — move one option to `position` (1 is first) and number
    the whole list 1, 2, 3… again. Returns the new position of every option
    whose position changes."""
    ordered = sorted(options, key=lambda o: o["position"])
    moving = next(o for o in ordered if o["id"] == option_id)
    ordered.remove(moving)
    ordered.insert(min(max(position, 1), len(ordered) + 1) - 1, moving)
    return {o["id"]: n for n, o in enumerate(ordered, start=1) if o["position"] != n}


def stage_probabilities_refusal(value: Any) -> str | None:
    """FR-CRM-PIPE-11 — a whole number from 0 to 100 for each of the six stages."""
    if not isinstance(value, dict):
        return "Stage probabilities must give a number for each stage."
    unknown = sorted(set(value) - set(STAGES))
    if unknown:
        return f"Unknown stage: {', '.join(unknown)}."
    missing = [STAGE_LABELS[s] for s in STAGES if s not in value]
    if missing:
        return f"Stage probabilities need {_and(missing)}."
    bad = [
        STAGE_LABELS[s]
        for s in STAGES
        if isinstance(value[s], bool) or not isinstance(value[s], int) or not 0 <= value[s] <= 100
    ]
    if bad:
        return f"The probability for {_and(bad)} must be a whole number from 0 to 100."
    return None


#: Keys a workspace admin edits in Settings, each with its validator. Later
#: slices add flag thresholds, automations and email sync (1b).
VALIDATORS: Final = {"stage_probabilities": stage_probabilities_refusal}


def setting_refusal(key: str, value: Any) -> str | None:
    validate = VALIDATORS.get(key)
    if validate is None:
        return "No such setting."
    return validate(value)


def month_start(month: str) -> date:
    """'2026-10' → 2026-10-01. The caller has already checked the shape."""
    year, mon = month.split("-")
    return date(int(year), int(mon), 1)


def _and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"
