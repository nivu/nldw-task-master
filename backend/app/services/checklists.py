"""Onboarding and offboarding checklists — spec 006 FR-CHK."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services import audit
from app.services import supabase as db


def templates() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"onboarding": [], "offboarding": []}
    for row in db.list_checklist_templates():
        out[row["kind"]].append(row["label"])
    return out


def set_template(kind: str, labels: list[str], actor_id: str) -> None:
    db.replace_checklist_templates(kind, [label.strip() for label in labels if label.strip()])
    audit.record(
        action="checklist.template_set",
        target_table="checklist_templates",
        target_id=None,
        actor_id=actor_id,
        after={"kind": kind, "items": len(labels)},
    )


def start(*, user_id: str, kind: str, actor_id: str) -> dict[str, Any]:
    checklist = db.insert_checklist({"user_id": user_id, "kind": kind, "created_by": actor_id})
    db.insert_checklist_items(
        [
            {"checklist_id": checklist["id"], "position": i + 1, "label": label}
            for i, label in enumerate(templates()[kind])
        ]
    )
    audit.record(
        action="checklist.started",
        target_table="checklists",
        target_id=checklist["id"],
        actor_id=actor_id,
        after={"user_id": user_id, "kind": kind},
    )
    return checklist


def tick(
    *,
    item_id: str,
    done: bool,
    actor_id: str,
    owner_id: str | None = None,
    due_on: str | None = None,
) -> dict[str, Any] | None:
    changes: dict[str, Any] = {}
    if done:
        changes.update({"done_at": datetime.now(UTC).isoformat(), "done_by": actor_id})
    else:
        changes.update({"done_at": None, "done_by": None})
    if owner_id is not None:
        changes["owner_id"] = owner_id or None
    if due_on is not None:
        changes["due_on"] = due_on or None
    return db.update_checklist_item(item_id, changes)


def close(checklist_id: str, actor_id: str) -> None:
    db.update_checklist(checklist_id, {"closed_at": datetime.now(UTC).isoformat()})
    audit.record(
        action="checklist.closed",
        target_table="checklists",
        target_id=checklist_id,
        actor_id=actor_id,
    )


def listing() -> list[dict[str, Any]]:
    names = {p["id"]: p["display_name"] for p in db.list_profiles()}
    lists = db.list_checklists()
    items = db.list_checklist_items([c["id"] for c in lists])
    by_list: dict[str, list[dict]] = {}
    for item in items:
        by_list.setdefault(item["checklist_id"], []).append(
            {
                **{
                    k: item[k]
                    for k in ("id", "position", "label", "owner_id", "due_on", "done_at", "done_by")
                },
                "owner_name": names.get(item.get("owner_id") or "", None),
            }
        )
    return [
        {
            "id": c["id"],
            "user_id": c["user_id"],
            "display_name": names.get(c["user_id"], "—"),
            "kind": c["kind"],
            "created_at": c["created_at"],
            "closed_at": c.get("closed_at"),
            "items": by_list.get(c["id"], []),
            "done": sum(1 for i in by_list.get(c["id"], []) if i["done_at"]),
            "total": len(by_list.get(c["id"], [])),
        }
        for c in lists
    ]
