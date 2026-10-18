"""Delegation messages — spec 008 FR-TASK-DEL-01, -05, -07; TQ-12;
data-model §2.6; plan §5.8.

Assigning a task to someone else, and someone else completing or handing
back the creator's task, sends one message through the portal's existing
channel (`notify.deliver`: Slack DM and email). A CRM task's in-app bell
notice stays where it was, in `services/crm/notifications.py`.

- `enqueue` hands the job ids and the event's time only (FR-TASK-DEL-07),
  off the request thread (`dispatch.fire_and_forget`), so a slow broker
  never slows the write and a failure never fails it.
- `after_write` is the one place a task write (the task service's and the
  CRM's task routes alike) turns into those jobs, when the actor has phase 2
  switched on.
- `send`, run by `tasks.send_notice`, sends nothing to a person who can no
  longer see the task, or who is no longer its assignee for `assigned`
  (FR-TASK-VIS-04). It claims the sent marker first
  (`task_notice_sends`, one per task, event, recipient and time of the
  event) and sends only when the claim was new, so a retried job sends
  nothing twice. The text is built here, from the task as it is now.
- Logs carry ids and outcomes only, never a title (NFR-TASK-04).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.domain.tasks import delegation, visibility
from app.services import notify
from app.services.tasks import store, viewer

logger = logging.getLogger("nldw-task-master")


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def enqueue(task_id: str, event: str, recipient_id: str, occurred_at: str) -> None:
    """One `tasks.send_notice` job, ids and the event's time only."""
    from app.services.dispatch import fire_and_forget
    from app.tasks import task_jobs

    fire_and_forget(
        task_jobs.send_notice,
        task_id,
        event,
        recipient_id,
        occurred_at,
        label=f"tasks:notice:{event}",
    )


def after_write(
    level: int,
    actor_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    handed_back: bool = False,
) -> None:
    """FR-TASK-DEL-01, -05 — one job per message this write sends, when the
    actor's phase `level` is 2 or more: `before` is the task as it was (None
    for a new task), `after` as written."""
    if level < 2:
        return
    for event, recipient in delegation.notice_events(
        before, after, actor_id, handed_back=handed_back
    ):
        enqueue(after["id"], event, recipient, str(after["updated_at"]))


def _sees(task: dict[str, Any], event: str, person: dict[str, Any]) -> bool:
    """FR-TASK-VIS-04 — the person may still see the task, and for
    `assigned` is still its assignee."""
    # `present` reads `services/crm/records`, which imports this module.
    from app.services.tasks import present

    if event == "assigned" and task.get("assignee_id") != person["id"]:
        return False
    facts = present.facts(task, store.list_links([task["id"]]))
    return visibility.sight(viewer.of_profile(person), facts) != "none"


def _name(person_id: str | None) -> str:
    found = next(iter(store.get_profiles([person_id])), None) if person_id else None
    return (found or {}).get("display_name") or "Someone"


def _text(event: str, task: dict[str, Any]) -> tuple[str, str]:
    """The subject and body of one message, from the task as it is now."""
    title = task["title"]
    if event == "assigned":
        who = _name(task.get("assigned_by") or task.get("created_by"))
        return f"{who} assigned you a task", f"“{title}”"
    if event == "completed":
        return f"{_name(task.get('updated_by'))} completed your task", f"“{title}”"
    who = _name(task.get("assigned_by"))
    return f"{who} handed back your task", f"“{title}”\n\nTheir note is on the task."


def send(task_id: str, event: str, recipient_id: str, occurred_at: str) -> dict[str, Any]:
    """FR-TASK-DEL-07 — sends one message at most once. Returns
    `{sent, channels}`; `sent` is false when it was sent before, when the
    task or the person is gone, or when the person may not see it now."""
    task = store.get_task(task_id)
    person = next(iter(store.get_profiles([recipient_id])), None)
    if (
        task is None
        or task.get("deleted_at")
        or not person
        or not person.get("is_active")
        or not _sees(task, event, person)
    ):
        logger.info(
            '{"event": "task_notice_skipped", "task_id": "%s", "notice": "%s"}', task_id, event
        )
        return {"sent": False, "channels": []}
    claimed = store.claim_notice(task_id, event, recipient_id, occurred_at)
    if claimed is None:
        logger.info(
            '{"event": "task_notice_already_sent", "task_id": "%s", "notice": "%s"}',
            task_id,
            event,
        )
        return {"sent": False, "channels": []}
    subject, body = _text(event, task)
    path = "/crm" if (person.get("account_type") or "staff") == "crm_only" else "/tasks"
    channels = notify.deliver(
        notify.Message(
            recipient_email=person["email"],
            recipient_name=person.get("display_name") or person["email"],
            subject=subject,
            body=f"{body}\n\n{settings.FRONTEND_URL.rstrip('/')}{path}",
        )
    )
    store.mark_notice_sent(claimed, _now().isoformat(), channels)
    logger.info(
        '{"event": "task_notice_sent", "task_id": "%s", "notice": "%s", "channels": %d}',
        task_id,
        event,
        len(channels),
    )
    return {"sent": True, "channels": channels}
