"""Reading the audit log — FR-APPR-07. Pure."""

from __future__ import annotations

SYSTEM = "System"


def actor_name(actor_id: str | None, names: dict[str, str], actor_label: str | None = None) -> str:
    """Who an entry is attributed to. No actor means the system did it (Q-04).

    Spec 007 FR-CRM-TVA-03, D8: a TVA change the CRM made on someone's behalf
    (a deal move by a person who could not edit the project themselves) is a
    `system` row that still names that person, and must not read as their
    own edit.
    """
    if actor_id is None:
        return SYSTEM
    name = names.get(actor_id, actor_id)
    if actor_label == "system":
        return f"{name} (via CRM deal)"
    return name
