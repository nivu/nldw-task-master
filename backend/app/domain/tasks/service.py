"""The task service and the 008/009 interface — spec 008 §14, contracts §4;
FR-TASK-DATA-05 to -09, -13, -15, FR-TASK-ROLL-04; plan §9 rule 2, §4 CT-2.

`create_task(actor, payload)` is the one way a task is created: `POST /tasks`
calls it, quick add and the Life OS import call it, and `009`'s Review Queue
calls it, so every task gets the same validation, visibility and audit. Its
shape (`TaskActor`, `TaskCreate`) is fixed in slice S1a and pinned by
`tests/test_task_create_interface.py`; nobody changes it after S1a.

The one `domain/tasks` module that does IO (CT-2): it reads through
`app.services.tasks.store`, decides with the pure modules beside it, writes
through `task_apply` in one call and returns the task as `TaskOut`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID

from app.api.deps import CurrentUser
from app.domain.tasks import rollout, rules, visibility
from app.domain.tasks.errors import TaskRefused
from app.schemas.tasks import TaskCreate
from app.services.crm import notifications
from app.services.crm import records as crm_records
from app.services.crm import store as crm_store
from app.services.tasks import present, store, viewer

#: A CRM link's record type, as a person says it.
_NOUNS = {"company": "company", "person": "person", "opportunity": "deal", "campaign": "campaign"}

NEEDS_LINK = "A CRM task needs at least one linked record."
TWO_WORKSPACES = "A task cannot link records of two workspaces."
LINKED_TWICE = "The same record is linked twice."
ONE_PROJECT = "A task can link only one TVA project."
ARCHIVED = "That project is archived."
LABS_ONLY = "Only Nunnari Labs tasks can link a TVA project."
NO_SUBTASKS = "A subtask cannot have subtasks."
NO_SUCH_TASK = "No such task."
NO_SUCH_PROJECT = "No such project."
CATEGORY_CRM_ONLY = "A category is only for CRM tasks."
NOT_A_CATEGORY = "That is not a task category in this workspace."
INACTIVE = "The assignee must be an active account."
NOT_A_MEMBER = "The assignee must be a member of this workspace."
CRM_ONLY_PROJECT = "A CRM-only account cannot be given a project task."
WAITING = "Waiting needs who or what you are waiting on."
NEW_IS_OPEN = "A new task is open or waiting."
COMPLETED_AT = "Only an imported Done task can carry a completed time."


@dataclass(frozen=True)
class TaskActor:
    user: CurrentUser  # app.api.deps.CurrentUser (staff or crm_only)
    kind: Literal["user", "system", "import"] = "user"
    channel: Literal["web", "mcp"] | None = None
    suggestion_id: UUID | None = None  # set only by 009's Review Queue approval

    def as_rpc(self) -> dict:
        """{"user_id", "kind", "channel"} plus "suggestion_id" when set; no workspace_id."""
        out: dict[str, Any] = {"user_id": self.user.id, "kind": self.kind, "channel": self.channel}
        if self.suggestion_id is not None:
            out["suggestion_id"] = str(self.suggestion_id)
        return out


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _workspace_of_links(viewer: visibility.Viewer, links: list[Any]) -> str | None:
    """Contracts §4 steps 1-2: the one workspace of the CRM links, each a live
    record the actor may link as an Admin or Member there (else 404)."""
    found: set[str] = set()
    for link in links:
        record = store.record_workspace(link.record_type, str(link.record_id))
        noun = _NOUNS[link.record_type]
        if record is None or record.get("deleted_at"):
            raise TaskRefused(404, f"No such {noun}.")
        if viewer.memberships.get(record["workspace_id"]) not in ("admin", "member"):
            raise TaskRefused(404, f"No such {noun}.")
        found.add(record["workspace_id"])
    if len(found) > 1:
        raise TaskRefused(422, TWO_WORKSPACES)
    return next(iter(found), None)


def _check_person(
    person_id: str, actor_id: str, *, workspace_id: str | None, has_project: bool, default: int
) -> dict[str, Any]:
    """Contracts §4 step 3 (FR-TASK-DATA-07, FR-TASK-ROLL-04): someone the
    task may go to, or wait on. The actor's own level is not checked here:
    routes and tools check it."""
    found = next(iter(store.get_profiles([person_id])), None)
    if found is None or not found.get("is_active"):
        raise TaskRefused(422, INACTIVE)
    if workspace_id is not None:
        if person_id not in store.member_ids(workspace_id):
            raise TaskRefused(422, NOT_A_MEMBER)
        return found
    if has_project and (found.get("account_type") or "staff") == "crm_only":
        raise TaskRefused(422, CRM_ONLY_PROJECT)
    why = person_id != actor_id and rollout.assignee_refusal(
        found.get("display_name") or "That person", rollout.level_for(found, default)
    )
    if why:
        raise TaskRefused(422, why)
    return found


def create_task(actor: TaskActor, payload: TaskCreate) -> dict:
    """Contracts §4 — creates one task and its links in one `task_apply` call,
    audited with the actor's kind and channel, and returns it as `TaskOut`
    presented for the actor. Refuses with `TaskRefused`: 422 for a rule, 404
    for a record, project or parent the actor cannot reach; never 403."""
    me = viewer.load(actor.user)
    now = _now()
    today = rules.day_of(now, actor.user.profile.get("timezone"))
    default = store.default_level()

    # 2. Repeated links, before anything is read or written.
    pairs = [(link.record_type, str(link.record_id)) for link in payload.links]
    if len(set(pairs)) != len(pairs):
        raise TaskRefused(422, LINKED_TWICE)
    crm_links = [link for link in payload.links if link.record_type != "tva_project"]
    project_links = [link for link in payload.links if link.record_type == "tva_project"]

    # 1. Workspace, from the CRM links.
    ws_id = _workspace_of_links(me, crm_links)
    if payload.workspace_id is not None:
        if ws_id is None:
            raise TaskRefused(422, NEEDS_LINK)
        if str(payload.workspace_id) != ws_id:
            raise TaskRefused(422, TWO_WORKSPACES)

    # 2. The project link.
    project_id: str | None = None
    if len(project_links) > 1:
        raise TaskRefused(422, ONE_PROJECT)
    if project_links:
        project_id = str(project_links[0].record_id)
        project = next(iter(store.get_projects([project_id])), None)
        if project is None or not visibility.can_see_project(me, project_id):
            raise TaskRefused(404, NO_SUCH_PROJECT)
        if project.get("is_archived"):
            raise TaskRefused(422, ARCHIVED)
        if ws_id is not None:
            workspace = next(iter(store.get_workspaces([ws_id])), None)
            if not (workspace or {}).get("tva_linked"):
                raise TaskRefused(422, LABS_ONLY)

    # 2. The parent.
    parent_id = str(payload.parent_id) if payload.parent_id else None
    if parent_id is not None:
        parent = store.get_task(parent_id)
        if parent is None or parent.get("deleted_at"):
            raise TaskRefused(404, NO_SUCH_TASK)
        if visibility.sight(me, present.facts(parent, store.list_links([parent_id]))) == "none":
            raise TaskRefused(404, NO_SUCH_TASK)
        if parent.get("parent_id"):
            raise TaskRefused(422, NO_SUBTASKS)

    # 2. The category, CRM tasks only.
    category_id = str(payload.category_id) if payload.category_id else None
    if category_id is not None:
        if ws_id is None:
            raise TaskRefused(422, CATEGORY_CRM_ONLY)
        if not any(
            o["id"] == category_id and o["list_key"] == "task.category" and o["is_active"]
            for o in crm_store.list_options(ws_id)
        ):
            raise TaskRefused(422, NOT_A_CATEGORY)

    # 2. Tags (TP-14).
    try:
        tags = rules.normalise_tags(payload.tags)
    except ValueError as exc:
        raise TaskRefused(422, str(exc)) from None

    # 3. The assignee, by default the actor.
    assignee_id = str(payload.assignee_id) if payload.assignee_id else actor.user.id
    _check_person(
        assignee_id,
        actor.user.id,
        workspace_id=ws_id,
        has_project=project_id is not None,
        default=default,
    )

    # 4. Status, completed at and bucket.
    if payload.status in ("done", "cancelled") and actor.kind != "import":
        raise TaskRefused(422, NEW_IS_OPEN)
    if payload.completed_at is not None and not (
        actor.kind == "import" and payload.status == "done"
    ):
        raise TaskRefused(422, COMPLETED_AT)
    waiting_on_id = str(payload.waiting_on_id) if payload.waiting_on_id else None
    waiting_on_text = payload.waiting_on_text or None
    if waiting_on_id is not None:
        person = _check_person(
            waiting_on_id,
            actor.user.id,
            workspace_id=ws_id,
            has_project=project_id is not None,
            default=default,
        )
        waiting_on_text = person.get("display_name") or waiting_on_text
    if payload.status == "waiting" and not (waiting_on_text or "").strip():
        raise TaskRefused(422, WAITING)
    bucket = payload.bucket
    if bucket is None and payload.status in rules.OPEN:
        bucket = rules.default_bucket(payload.due_on, today)

    values: dict[str, Any] = {
        "title": payload.title,
        "body": payload.notes,
        "status": payload.status,
        "due_on": payload.due_on.isoformat() if payload.due_on else None,
        "bucket": bucket,
        "priority": payload.priority,
        "assignee_id": assignee_id,
        "tags": tags,
        "parent_id": parent_id,
        "waiting_on_id": waiting_on_id,
        "waiting_on_text": waiting_on_text,
        "category_id": category_id,
        "source": payload.source,
        "source_ref": payload.source_ref,
        "completed_at": (
            (payload.completed_at or now).isoformat() if payload.status == "done" else None
        ),
    }
    if ws_id is not None:
        values["workspace_id"] = ws_id

    # 5. One write: the task, then its links.
    ops: list[dict[str, Any]] = [
        {"op": "insert", "table": "tasks", "ref": "task", "values": values}
    ]
    ops += [
        {
            "op": "insert",
            "table": "task_links",
            "values": {
                "task_id": "$ref:task",
                "workspace_id": ws_id,
                "record_type": link.record_type,
                "record_id": str(link.record_id),
            },
        }
        for link in crm_links
    ]
    if project_id is not None:
        ops.append(
            {
                "op": "insert",
                "table": "task_links",
                "values": {
                    "task_id": "$ref:task",
                    "record_type": "tva_project",
                    "record_id": project_id,
                    "project_id": project_id,
                },
            }
        )
    row = store.apply(actor.as_rpc(), ops)[0]["row"]

    # 6. A CRM task keeps the CRM's flags and its bell notice (as today).
    if ws_id is not None:
        targets = [{"record_type": t, "record_id": i} for t, i in pairs if t != "tva_project"]
        crm_records.reflag(ws_id, "task", [row], links=targets)
        notifications.quietly(
            notifications.assigned(
                SimpleNamespace(user=actor.user, workspace_id=ws_id),
                "task",
                row["id"],
                row["title"],
                assignee_id,
            )
        )

    # 7. The task as the actor sees it.
    return present.present(me, [row], today=today)[0]
