"""Reading the audit log — FR-APPR-07. Pure."""

from __future__ import annotations

SYSTEM = "System"


def actor_name(actor_id: str | None, names: dict[str, str]) -> str:
    """Who an entry is attributed to. No actor means the system did it (Q-04)."""
    if actor_id is None:
        return SYSTEM
    return names.get(actor_id, actor_id)
