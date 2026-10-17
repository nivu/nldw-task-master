"""Phase switches — spec 008 FR-TASK-ROLL-01, -03, -04; C6; TP-11;
data-model §2.4.

A person's level is their own (`profiles.tasks_level`) or, when they have
none, the global default (`tasks_default_level`, starting at 0). Level n
switches on phases 1 to n.
"""

from __future__ import annotations

from typing import Any, Final

#: FR-TASK-ROLL-03.
NOT_ON: Final = "Tasks are not switched on for you yet."


def level_for(profile: dict[str, Any], default: int) -> int:
    """The person's effective level: their own, else the global default."""
    own = profile.get("tasks_level")
    return int(own) if own is not None else int(default)


def refusal(level: int, needed: int) -> str | None:
    """FR-TASK-ROLL-03 — None when phase `needed` is switched on at `level`."""
    return None if level >= needed else NOT_ON


def assignee_refusal(name: str, level: int) -> str | None:
    """FR-TASK-ROLL-04 — a task with no workspace goes only to someone with
    phase 1 switched on."""
    return None if level >= 1 else f"{name} does not have My Tasks yet."
