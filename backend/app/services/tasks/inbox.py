"""The Ideas and Reading inbox — spec 008 FR-TASK-INBOX-01 to -03; contracts
§2 inbox routes; data-model §2.10.

An inbox item is always private to its owner, admins included
(FR-TASK-INBOX-01): every read and write here takes the caller's own items
only, and anyone else's is 404, as an item that does not exist.

- **Add** (`POST /tasks/inbox`, `inbox_add`, and a confirmed voice idea):
  one `task_apply` insert, audited with the caller as actor.
- **Make it a task**: the task is created through `create_task` from the
  item (title, tags, and its link and notes as the task's notes), any fields
  sent replacing only what they name; in that same `task_apply` call the item
  records the task it became and is marked processed, with its `updated_at`
  as read, so two requests cannot both make a task of it and the task never
  exists without the item linked to it (FR-TASK-INBOX-02).
- **Archive**: `processed_at` and `archived`.

Logs carry ids only, never a title, link or note (NFR-TASK-04).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from app.domain.tasks import labels, rules, service
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import TaskActor
from app.schemas.tasks import InboxIn, TaskCreate, TaskIn
from app.services.tasks import people, store

logger = logging.getLogger("nldw-task-master")

NO_SUCH_ITEM: Final = "No such inbox item."
PROCESSED: Final = "This inbox item has already been made a task or archived."
#: `TaskCreate.notes`' limit.
MAX_NOTES: Final = 20_000


def out(row: dict[str, Any]) -> dict[str, Any]:
    """An item as returned, its kind with a label beside it (contracts §0)."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "kind_label": labels.INBOX_KIND.get(row["kind"], row["kind"]),
        "title": row["title"],
        "url": row.get("url"),
        "notes": row.get("notes"),
        "tags": list(row.get("tags") or []),
        "created_at": row["created_at"],
        "processed_at": row.get("processed_at"),
        "archived": bool(row.get("archived")),
        "task_id": row.get("task_id"),
    }


def _own(actor: TaskActor, item_id: str) -> dict[str, Any]:
    """The caller's own item; anyone else's is 404."""
    row = store.get_inbox_item(item_id)
    if row is None or row["owner_id"] != actor.user.id:
        raise TaskRefused(404, NO_SUCH_ITEM)
    return row


def _write(actor: TaskActor, op: dict[str, Any]) -> dict[str, Any]:
    return store.apply(actor.as_rpc(), [{"table": "inbox_items", **op}])[0]["row"]


def unprocessed(actor: TaskActor) -> list[dict[str, Any]]:
    """Contracts §2 `GET /tasks/inbox` — the caller's unprocessed items,
    newest first."""
    return [out(r) for r in store.unprocessed_inbox(actor.user.id)]


def add(actor: TaskActor, body: InboxIn) -> dict[str, Any]:
    """FR-TASK-INBOX-01, -03 — one item in the caller's own inbox."""
    try:
        tags = rules.normalise_tags(body.tags)
    except ValueError as exc:
        raise TaskRefused(422, str(exc)) from None
    row = _write(
        actor,
        {
            "op": "insert",
            "values": {
                "owner_id": actor.user.id,
                "kind": body.kind,
                "title": body.title,
                "url": body.url,
                "notes": body.notes,
                "tags": tags,
            },
        },
    )
    logger.info('{"event": "tasks_inbox_added", "item_id": "%s"}', row["id"])
    return out(row)


def _from_item(row: dict[str, Any]) -> dict[str, Any]:
    """The task's fields from the item: its title and tags, and its link and
    notes as the task's notes."""
    notes = "\n\n".join(p for p in (row.get("url"), row.get("notes")) if p) or None
    return {
        "title": row["title"],
        "notes": notes[:MAX_NOTES] if notes else None,
        "tags": list(row.get("tags") or []),
    }


def make_task(actor: TaskActor, item_id: str, draft: TaskIn | None) -> dict[str, Any]:
    """FR-TASK-INBOX-02 — `{item, task}`: a task made from the item (the
    fields sent override its own), recorded on the item, which is marked
    processed."""
    row = _own(actor, item_id)
    if row.get("processed_at"):
        raise TaskRefused(409, PROCESSED)
    fields = _from_item(row)
    if draft is not None:
        fields |= draft.model_dump(exclude={"assignee"}, exclude_unset=True)
        if draft.assignee is not None:
            if draft.assignee_id is not None:
                raise TaskRefused(422, "Give the assignee once, by id or by name.")
            fields["assignee_id"] = people.resolve(actor, draft.assignee)
    payload = TaskCreate(**fields, source="nldw" if actor.channel == "mcp" else "manual")
    task = service.create_task_with(
        actor,
        payload,
        [
            {
                "op": "update",
                "table": "inbox_items",
                "id": row["id"],
                "values": {"processed_at": service._now().isoformat(), "task_id": "$ref:task"},
                "expect_updated_at": row["updated_at"],
            }
        ],
    )
    done = store.get_inbox_item(row["id"])
    logger.info(
        '{"event": "tasks_inbox_made_task", "item_id": "%s", "task_id": "%s"}',
        row["id"],
        task["id"],
    )
    return {"item": out(done), "task": task}


def archive(actor: TaskActor, item_id: str) -> dict[str, Any]:
    """FR-TASK-INBOX-02 — the item, processed and archived."""
    row = _own(actor, item_id)
    if row.get("processed_at"):
        raise TaskRefused(409, PROCESSED)
    done = _write(
        actor,
        {
            "op": "update",
            "id": row["id"],
            "values": {"processed_at": service._now().isoformat(), "archived": True},
            "expect_updated_at": row["updated_at"],
        },
    )
    logger.info('{"event": "tasks_inbox_archived", "item_id": "%s"}', row["id"])
    return out(done)
