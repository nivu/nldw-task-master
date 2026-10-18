"""Handing work over — spec 008 FR-TASK-DEL-01 to -03, -05, -06; Journey
4.7; data-model §2.1 (phase 2), §2.6; contracts §2.

- **New** (FR-TASK-DEL-02): a task someone else assigned shows as New to its
  assignee until they accept, comment on, change or complete it. Computed on
  read from `assigned_by` and `accepted_at`; nothing is stored as "new".
- **Hand back** (FR-TASK-DEL-03): the current assignee, when not the
  creator, gives the task back; the creator is its assignee again and the
  note is kept as a comment.
- **Messages** (FR-TASK-DEL-01, -05): one to someone else made the assignee,
  one to the creator when someone else completes or hands back their task.
  Never one to the person who acted.
- **Waiting on** (FR-TASK-DEL-06): the person named must be someone the
  caller may assign; naming them gives them no sight of the task.

Pure: the service passes the rows, the actor's id and the people offered.
"""

from __future__ import annotations

from typing import Any, Final

#: FR-TASK-DEL-06 — idle days before a waiting task is a "nudge?", 1-30.
DEFAULT_NUDGE_DAYS: Final = 5
MIN_NUDGE_DAYS: Final = 1
MAX_NUDGE_DAYS: Final = 30

NOT_ASSIGNEE: Final = "Only the person this task is assigned to can do that."
OWN_TASK: Final = "This is your own task; there is no one to hand it back to."
WAITING_ON: Final = "You can wait only on someone you could assign this task to."


def is_new(task: dict[str, Any]) -> bool:
    """FR-TASK-DEL-02 — assigned by someone else and not yet accepted."""
    by = task.get("assigned_by")
    return by is not None and by != task.get("assignee_id") and task.get("accepted_at") is None


def accepting(task: dict[str, Any], actor_id: str, now: str) -> dict[str, Any]:
    """FR-TASK-DEL-02 — `accepted_at` to write when the assignee acts on a New
    task (a change, a comment, completing it); nothing otherwise."""
    if is_new(task) and task.get("assignee_id") == actor_id:
        return {"accepted_at": now}
    return {}


def assignment(assignee_id: str, actor_id: str) -> dict[str, Any]:
    """Data-model §2.1 — who set the assignee: the actor, or null when they
    assigned themselves; a new assignment is not accepted yet."""
    return {"assigned_by": None if assignee_id == actor_id else actor_id, "accepted_at": None}


def hand_back_plan(task: dict[str, Any], actor_id: str) -> dict[str, Any]:
    """FR-TASK-DEL-03 — the task's new values when `actor_id` hands it back:
    the creator is the assignee again, set by the person handing back.
    Raises ValueError with a sentence safe to show when the actor is not the
    current assignee, or is the creator."""
    if task.get("assignee_id") != actor_id:
        raise ValueError(NOT_ASSIGNEE)
    if task.get("created_by") in (None, actor_id):
        raise ValueError(OWN_TASK)
    return {"assignee_id": task["created_by"], "assigned_by": actor_id, "accepted_at": None}


def notice_events(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    actor: str,
    *,
    handed_back: bool = False,
) -> list[tuple[str, str]]:
    """FR-TASK-DEL-01, -05 — the messages one write sends, as `(event,
    recipient_id)`: `before` is the task as it was (None for a new task),
    `after` as written, `actor` the person who wrote it. A hand-back tells
    the creator that, and nothing else."""
    creator = after.get("created_by")
    if handed_back:
        return [("handed_back", creator)] if creator and creator != actor else []
    events: list[tuple[str, str]] = []
    assignee = after.get("assignee_id")
    if assignee and assignee != actor and assignee != (before or {}).get("assignee_id"):
        events.append(("assigned", assignee))
    if (
        before is not None
        and after.get("status") == "done"
        and before.get("status") != "done"
        and creator
        and creator != actor
    ):
        events.append(("completed", creator))
    return events


def waiting_on_refusal(person_id: str, assignable: set[str]) -> str | None:
    """FR-TASK-DEL-06 — None when the person named in Waiting on is one the
    caller may assign for this task (`assignable`, as `@name` offers)."""
    return None if person_id in assignable else WAITING_ON
