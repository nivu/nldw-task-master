"""The task as returned — spec 008 contracts §0.1 `TaskOut`; FR-TASK-VIS-07,
-08, FR-TASK-DATA-09, FR-TASK-CHG-11, FR-TASK-LIST-02, -03.

Every field a reader receives passes through `domain/tasks/visibility`: the
project as the viewer may see it (`project_field`), the parent or "a private
task" (`parent_field`), who can see the task (`audience`) and what the viewer
may do (`sight`, `may_delete`). A CRM task's status and bucket labels come
from its workspace's lists; every other label from `domain/tasks/labels`. No
hours, cost or day-log field ever (FR-TASK-LOG-08).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.domain.tasks import labels, rules, visibility
from app.domain.tasks.visibility import TaskFacts, Viewer
from app.services.crm import store as crm_store
from app.services.crm.records import link_names
from app.services.tasks import store


def facts(row: dict[str, Any], links: list[dict[str, Any]]) -> TaskFacts:
    """The facts `visibility` reads, from a task row and its links."""
    project = next((lk for lk in links if lk["record_type"] == "tva_project"), None)
    return TaskFacts(
        id=row["id"],
        workspace_id=row.get("workspace_id"),
        created_by=row.get("created_by"),
        assignee_id=row.get("assignee_id"),
        project_id=project["record_id"] if project else None,
        deleted_at=row.get("deleted_at"),
        parent_id=row.get("parent_id"),
    )


def _label(
    options: list[dict[str, Any]], list_key: str, key: str | None, fixed: dict
) -> str | None:
    if key is None:
        return None
    found = next(
        (o for o in options if o["list_key"] == list_key and o.get("system_key") == key), None
    )
    return found["label"] if found else fixed.get(key, key)


def present(viewer: Viewer, rows: list[dict[str, Any]], *, today: date) -> list[dict[str, Any]]:
    """`TaskOut` for each row, as `viewer` may see it."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    links = store.list_links(ids)
    links_of: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        links_of.setdefault(link["task_id"], []).append(link)

    parent_ids = sorted({r["parent_id"] for r in rows if r.get("parent_id")})
    parents = {p["id"]: p for p in store.get_tasks(parent_ids)}
    parent_links: dict[str, list[dict[str, Any]]] = {}
    for link in store.list_links(list(parents)):
        parent_links.setdefault(link["task_id"], []).append(link)

    project_ids = sorted({lk["record_id"] for lk in links if lk["record_type"] == "tva_project"})
    projects = {p["id"]: p for p in store.get_projects(project_ids)}
    ws_ids = sorted({r["workspace_id"] for r in rows if r.get("workspace_id")})
    workspaces = {w["id"]: w for w in store.get_workspaces(ws_ids)}
    options = {w: crm_store.list_options(w) for w in ws_ids}
    names_by_ws = {
        w: link_names(
            w,
            [
                lk
                for lk in links
                if lk.get("workspace_id") == w and lk["record_type"] != "tva_project"
            ],
        )
        for w in ws_ids
    }
    people = {
        p["id"]: p["display_name"]
        for p in store.get_profiles(
            sorted(
                {
                    pid
                    for r in rows
                    for pid in (r.get("assignee_id"), r.get("created_by"), r.get("waiting_on_id"))
                    if pid
                }
            )
        )
    }
    subtasks = store.subtask_counts(ids)

    out = []
    for row in rows:
        own = links_of.get(row["id"], [])
        task = facts(row, own)
        ws_id = row.get("workspace_id")
        ws_options = options.get(ws_id, []) if ws_id else []
        names = names_by_ws.get(ws_id, {}) if ws_id else {}
        project = projects.get(task.project_id or "")
        shown = visibility.project_field(viewer, task)
        parent = parents.get(row.get("parent_id") or "")
        sight = visibility.sight(viewer, task)
        workspace = workspaces.get(ws_id or "")
        out.append(
            {
                "id": row["id"],
                "kind": rules.kind(row, own),
                "title": row["title"],
                "notes": row.get("body"),
                "status": row["status"],
                "status_label": _label(ws_options, "task.status", row["status"], labels.STATUS),
                "bucket": row.get("bucket"),
                "bucket_label": _label(ws_options, "task.bucket", row.get("bucket"), labels.BUCKET),
                "effective_bucket": rules.effective_bucket(row, today),
                "due_on": row.get("due_on"),
                "days_overdue": rules.days_overdue(row, today),
                "priority": row.get("priority") or "normal",
                "priority_label": labels.PRIORITY.get(row.get("priority") or "normal"),
                "tags": row.get("tags") or [],
                "assignee": {
                    "id": row.get("assignee_id"),
                    "name": people.get(row.get("assignee_id")),
                },
                "created_by": {
                    "id": row.get("created_by"),
                    "name": people.get(row.get("created_by")),
                },
                "waiting_on": (
                    {"id": row.get("waiting_on_id"), "label": row.get("waiting_on_text")}
                    if row.get("waiting_on_id") or row.get("waiting_on_text")
                    else None
                ),
                "parent": visibility.parent_field(
                    viewer,
                    facts(parent, parent_links.get(parent["id"], [])) if parent else None,
                    parent["title"] if parent else None,
                ),
                "subtask_count": subtasks.get(row["id"], 0),
                "workspace": (
                    {"id": workspace["id"], "key": workspace["key"], "name": workspace["name"]}
                    if workspace
                    else None
                ),
                "category": (
                    {
                        "id": row["category_id"],
                        "label": next(
                            (o["label"] for o in ws_options if o["id"] == row["category_id"]), None
                        ),
                    }
                    if row.get("category_id")
                    else None
                ),
                "links": [
                    {
                        "id": lk["id"],
                        "record_type": lk["record_type"],
                        "record_id": lk["record_id"],
                        "name": names.get((lk["record_type"], lk["record_id"])),
                    }
                    for lk in own
                    if lk["record_type"] != "tva_project"
                ],
                "project": (
                    {"id": project["id"], "name": project["name"]}
                    if project and shown == "full"
                    else {"name": project["name"]}
                    if project and shown == "name_only"
                    else None
                ),
                "source": row.get("source") or "manual",
                "source_ref": row.get("source_ref"),
                "visible_to": visibility.audience(
                    {
                        "viewer_id": viewer.id,
                        "created_by": row.get("created_by"),
                        "assignee_id": row.get("assignee_id"),
                        "workspace_name": workspace["name"] if workspace else None,
                        "project_name": project["name"] if project else None,
                    },
                    people,
                ),
                "may": {
                    "edit": sight == "edit",
                    "status_only": sight == "status_only",
                    "delete": visibility.may_delete(viewer, task),
                },
                "completed_at": row.get("completed_at"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return out
