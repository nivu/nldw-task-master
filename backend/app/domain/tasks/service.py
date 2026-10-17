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
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID, uuid4

from app.api.deps import CurrentUser
from app.domain.tasks import rollout, rules, visibility
from app.domain.tasks.errors import TaskRefused
from app.schemas.tasks import TaskCreate, TaskLinkIn, TaskPatch
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
READ_ONLY = "You can read this task but not change it."
VIEWER_STATUS = "Viewers may change only the status of a task assigned to them."
LAST_LINK = crm_records.LAST_LINK
NO_LINK_LEFT = crm_records.NO_LINK_LEFT
NO_SUCH_LINK = "No such link."
NOT_IN_TRASH = "No such task in the Trash."
DELETE_CRM = "Only a workspace admin or the task's creator can delete this task."
DELETE_OWN = "Only the task's creator can delete it."
RESTORE_CRM = "Only a workspace admin can restore this task."
RESTORE_OWN = "Only the task's creator can restore it."


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


def _check_parent(me: visibility.Viewer, parent_id: str, task_id: str | None = None) -> None:
    """FR-TASK-DATA-09 — a live parent the person may see (404 otherwise)
    with no parent of its own; a task with subtasks takes no parent."""
    parent = store.get_task(parent_id)
    if parent is None or parent.get("deleted_at"):
        raise TaskRefused(404, NO_SUCH_TASK)
    if visibility.sight(me, present.facts(parent, store.list_links([parent_id]))) == "none":
        raise TaskRefused(404, NO_SUCH_TASK)
    if parent.get("parent_id") or parent_id == task_id:
        raise TaskRefused(422, NO_SUBTASKS)
    if task_id is not None and store.subtask_counts([task_id]).get(task_id):
        raise TaskRefused(422, NO_SUBTASKS)


def _check_category(ws_id: str | None, category_id: str) -> None:
    """A category is only for a CRM task, from its workspace's active list."""
    if ws_id is None:
        raise TaskRefused(422, CATEGORY_CRM_ONLY)
    if not any(
        o["id"] == category_id and o["list_key"] == "task.category" and o["is_active"]
        for o in crm_store.list_options(ws_id)
    ):
        raise TaskRefused(422, NOT_A_CATEGORY)


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
        _check_parent(me, parent_id)

    # 2. The category, CRM tasks only.
    category_id = str(payload.category_id) if payload.category_id else None
    if category_id is not None:
        _check_category(ws_id, category_id)

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


# ---------------------------------------------------------------------------
# Changing a task — contracts §1; spec §3.3; FR-TASK-DATA-05, -06, -09, -11,
# -13; TP-02
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Loaded:
    me: visibility.Viewer
    row: dict[str, Any]
    links: list[dict[str, Any]]
    facts: visibility.TaskFacts

    @property
    def crm_links(self) -> list[dict[str, Any]]:
        return [lk for lk in self.links if lk["record_type"] != "tva_project"]


def _load(actor: TaskActor, task_id: str, *, deleted: bool = False) -> _Loaded:
    """The task as the actor may see it: 404 "No such task." for one that
    does not exist, is (or, for a restore, is not) in the Trash, or that the
    actor may not see (FR-TASK-VIS-02)."""
    me = viewer.load(actor.user)
    row = store.get_task(task_id)
    if row is None or bool(row.get("deleted_at")) != deleted:
        raise TaskRefused(404, NOT_IN_TRASH if deleted else NO_SUCH_TASK)
    links = store.list_links([task_id])
    facts = present.facts(row, links)
    if visibility.sight(me, facts) == "none":
        raise TaskRefused(404, NOT_IN_TRASH if deleted else NO_SUCH_TASK)
    return _Loaded(me, row, links, facts)


def _may_change(task: _Loaded, fields: set[str]) -> None:
    """Spec §3.3: full sight changes anything; a Viewer assigned a CRM task
    changes its status only (`007` Q-57); read-only sight changes nothing
    (TP-02)."""
    seen = visibility.sight(task.me, task.facts)
    if seen == "edit":
        return
    if seen == "status_only":
        if fields - {"status"}:
            raise TaskRefused(403, VIEWER_STATUS)
        return
    raise TaskRefused(403, READ_ONLY)


def _today(actor: TaskActor) -> Any:
    return rules.day_of(_now(), actor.user.profile.get("timezone"))


def _after_write(
    actor: TaskActor,
    task: _Loaded,
    row: dict[str, Any],
    *,
    links: list[dict[str, Any]] | None = None,
    assigned: str | None = None,
) -> dict:
    """A CRM task keeps the CRM's flags and its bell notice (as today); the
    task is returned as the actor now sees it."""
    ws_id = row.get("workspace_id")
    if ws_id is not None:
        targets = [
            {"record_type": lk["record_type"], "record_id": lk["record_id"]}
            for lk in (task.crm_links if links is None else links)
            if lk["record_type"] != "tva_project"
        ]
        crm_records.reflag(ws_id, "task", [row], links=targets)
        if assigned is not None:
            notifications.quietly(
                notifications.assigned(
                    SimpleNamespace(user=actor.user, workspace_id=ws_id),
                    "task",
                    row["id"],
                    row["title"],
                    assigned,
                )
            )
    return present.present(viewer.load(actor.user), [row], today=_today(actor))[0]


def _write(actor: TaskActor, op: dict[str, Any]) -> dict[str, Any]:
    return store.apply(actor.as_rpc(), [op])[0]["row"]


#: Fields a change may not clear.
_REQUIRED = ("title", "status", "priority", "tags", "assignee_id")


def update_task(actor: TaskActor, task_id: str, patch: TaskPatch) -> dict:
    """Contracts §1 `PATCH /tasks/{id}` — only the fields sent change. Status
    sets or clears `completed_at` (FR-TASK-DATA-03); an open or waiting task
    left with no bucket gets the default (FR-TASK-DATA-08); changing the
    bucket never changes the due date (FR-TASK-LIST-04)."""
    sent = set(patch.model_fields_set) - {"expect_updated_at"}
    task = _load(actor, task_id)
    _may_change(task, sent)
    data = patch.model_dump(include=sent)
    cleared = [name for name in _REQUIRED if name in data and data[name] is None]
    if cleared:
        raise TaskRefused(422, rules.missing_sentence(cleared))
    row, ws_id = task.row, task.row.get("workspace_id")
    default = store.default_level()
    values: dict[str, Any] = {}
    for name, value in data.items():
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, date):
            value = value.isoformat()
        values["body" if name == "notes" else name] = value

    if "tags" in values:
        try:
            values["tags"] = rules.normalise_tags(values["tags"])
        except ValueError as exc:
            raise TaskRefused(422, str(exc)) from None
    # Waiting on is one label (data-model §2): a person picked names it, free
    # text replaces the person, and removing the person removes their name.
    if "waiting_on_id" in values:
        if values["waiting_on_id"] is None:
            values.setdefault("waiting_on_text", None)
        elif values["waiting_on_id"] == row.get("waiting_on_id"):
            values["waiting_on_text"] = row.get("waiting_on_text")
    elif "waiting_on_text" in values:
        values["waiting_on_id"] = None
    # Only what changes is checked: a field sent as it is stored (a full-form
    # save) is not refused for a rule that has since changed around it.
    values = {k: v for k, v in values.items() if row.get(k) != v}
    if "assignee_id" in values:
        _check_person(
            values["assignee_id"],
            actor.user.id,
            workspace_id=ws_id,
            has_project=task.facts.project_id is not None,
            default=default,
        )
    if values.get("parent_id"):
        _check_parent(task.me, values["parent_id"], task_id)
    if values.get("category_id"):
        _check_category(ws_id, values["category_id"])
    if values.get("waiting_on_id"):
        person = _check_person(
            values["waiting_on_id"],
            actor.user.id,
            workspace_id=ws_id,
            has_project=task.facts.project_id is not None,
            default=default,
        )
        values["waiting_on_text"] = person.get("display_name") or values.get("waiting_on_text")
    if "status" in values:
        values.update(rules.status_values(values["status"], row["status"], _now()))
    after = {**row, **values}
    if after.get("status") == "waiting" and not (after.get("waiting_on_text") or "").strip():
        raise TaskRefused(422, WAITING)
    if after.get("status") in rules.OPEN and not after.get("bucket"):
        values["bucket"] = rules.default_bucket(after.get("due_on"), _today(actor))

    changes = {k: v for k, v in values.items() if row.get(k) != v}
    if not changes:
        return present.present(task.me, [row], today=_today(actor))[0]
    op: dict[str, Any] = {"op": "update", "table": "tasks", "id": task_id, "values": changes}
    if patch.expect_updated_at is not None:
        op["expect_updated_at"] = patch.expect_updated_at.isoformat()
    written = _write(actor, op)
    assigned = changes.get("assignee_id")
    return _after_write(actor, task, written, assigned=assigned)


def complete_task(actor: TaskActor, task_id: str) -> dict:
    """Contracts §1 `POST /tasks/{id}/complete` — Done, with `completed_at`
    (FR-TASK-LIST-05). A parent's subtasks stay as they are (FR-TASK-DATA-09)."""
    task = _load(actor, task_id)
    _may_change(task, {"status"})
    if task.row["status"] == "done":
        return present.present(task.me, [task.row], today=_today(actor))[0]
    values = rules.status_values("done", task.row["status"], _now())
    written = _write(actor, {"op": "update", "table": "tasks", "id": task_id, "values": values})
    return _after_write(actor, task, written)


def reopen_task(actor: TaskActor, task_id: str) -> dict:
    """Contracts §1 `POST /tasks/{id}/reopen` — back to Open, `completed_at`
    cleared, a bucket by FR-TASK-DATA-08 when it has none."""
    task = _load(actor, task_id)
    _may_change(task, {"status"})
    if task.row["status"] in rules.OPEN:
        return present.present(task.me, [task.row], today=_today(actor))[0]
    values = rules.status_values("open", task.row["status"], _now())
    if not task.row.get("bucket"):
        values["bucket"] = rules.default_bucket(task.row.get("due_on"), _today(actor))
    written = _write(actor, {"op": "update", "table": "tasks", "id": task_id, "values": values})
    return _after_write(actor, task, written)


def add_link(actor: TaskActor, task_id: str, link: TaskLinkIn) -> dict:
    """Contracts §1 `POST /tasks/{id}/links`. A CRM record needs the actor to
    be an Admin or Member of its workspace (404 otherwise, FR-TASK-DATA-13);
    the first CRM link sets the task's workspace in the same write
    (FR-TASK-DATA-15). A project: one at most, one the actor can see, not
    archived, and only on a Labs task (FR-TASK-DATA-06)."""
    task = _load(actor, task_id)
    _may_change(task, {"links"})
    pair = (link.record_type, str(link.record_id))
    ws_id = task.row.get("workspace_id")
    ops: list[dict[str, Any]] = []

    if link.record_type == "tva_project":
        # The project first, then the task's own: a caller who may not see
        # the task's project learns nothing of it (FR-TASK-VIS-07).
        project = next(iter(store.get_projects([pair[1]])), None)
        if project is None or not visibility.can_see_project(task.me, pair[1]):
            raise TaskRefused(404, NO_SUCH_PROJECT)
        if task.facts.project_id is not None:
            if visibility.project_field(task.me, task.facts) == "absent":
                raise TaskRefused(404, NO_SUCH_PROJECT)
            if task.facts.project_id == pair[1]:
                raise TaskRefused(422, LINKED_TWICE)
            raise TaskRefused(422, ONE_PROJECT)
        if project.get("is_archived"):
            raise TaskRefused(422, ARCHIVED)
        if ws_id is not None:
            workspace = next(iter(store.get_workspaces([ws_id])), None)
            if not (workspace or {}).get("tva_linked"):
                raise TaskRefused(422, LABS_ONLY)
        else:
            assignee = next(iter(store.get_profiles([task.row["assignee_id"]])), None) or {}
            if (assignee.get("account_type") or "staff") == "crm_only":
                raise TaskRefused(422, CRM_ONLY_PROJECT)
        values = {"task_id": task_id, "record_type": "tva_project", "record_id": pair[1]}
        ops.append(
            {"op": "insert", "table": "task_links", "values": {**values, "project_id": pair[1]}}
        )
    else:
        if any((lk["record_type"], lk["record_id"]) == pair for lk in task.links):
            raise TaskRefused(422, LINKED_TWICE)
        found = _workspace_of_links(task.me, [link])
        if ws_id is not None and found != ws_id:
            raise TaskRefused(422, TWO_WORKSPACES)
        if ws_id is None:
            if task.facts.project_id is not None:
                workspace = next(iter(store.get_workspaces([found])), None)
                if not (workspace or {}).get("tva_linked"):
                    raise TaskRefused(422, LABS_ONLY)
            if task.row["assignee_id"] not in store.member_ids(found):
                raise TaskRefused(422, NOT_A_MEMBER)
            ops.append(
                {"op": "update", "table": "tasks", "id": task_id, "values": {"workspace_id": found}}
            )
        ops.append(
            {
                "op": "insert",
                "table": "task_links",
                "values": {
                    "task_id": task_id,
                    "workspace_id": found,
                    "record_type": pair[0],
                    "record_id": pair[1],
                },
            }
        )
    store.apply(actor.as_rpc(), ops)
    written = store.get_task(task_id) or task.row
    return _after_write(
        actor, task, written, links=[*task.links, {"record_type": pair[0], "record_id": pair[1]}]
    )


def remove_link(actor: TaskActor, task_id: str, link_id: str) -> dict:
    """Contracts §1 `DELETE /tasks/{id}/links/{link_id}`. A CRM task never
    loses its last CRM link (FR-TASK-DATA-05). A project link is named by
    the project's id too, the only one `TaskOut` gives; one the caller may
    not see is no link (FR-TASK-VIS-07)."""
    task = _load(actor, task_id)
    _may_change(task, {"links"})
    link = next(
        (
            lk
            for lk in task.links
            if lk["id"] == link_id
            or (lk["record_type"] == "tva_project" and lk["record_id"] == link_id)
        ),
        None,
    )
    if link is not None and (
        link["record_type"] == "tva_project"
        and visibility.project_field(task.me, task.facts) == "absent"
    ):
        link = None
    if link is None:
        raise TaskRefused(404, NO_SUCH_LINK)
    if link["record_type"] != "tva_project" and len(task.crm_links) <= 1:
        raise TaskRefused(422, LAST_LINK)
    store.apply(actor.as_rpc(), [{"op": "delete", "table": "task_links", "id": link["id"]}])
    written = store.get_task(task_id) or task.row
    return _after_write(actor, task, written)


def delete_task(actor: TaskActor, task_id: str) -> dict:
    """Contracts §1 `DELETE /tasks/{id}` — soft. A CRM task follows `007`'s
    rules and goes to the workspace Trash; any other task is deleted by its
    creator only and kept 90 days (FR-TASK-DATA-11)."""
    task = _load(actor, task_id)
    if not visibility.may_delete(task.me, task.facts):
        raise TaskRefused(403, DELETE_CRM if task.facts.workspace_id else DELETE_OWN)
    written = _write(
        actor,
        {"op": "soft_delete", "table": "tasks", "id": task_id, "deleted_group": str(uuid4())},
    )
    if written.get("workspace_id") is not None:
        crm_records.reflag(
            written["workspace_id"],
            "task",
            [written],
            links=[
                {"record_type": lk["record_type"], "record_id": lk["record_id"]}
                for lk in task.crm_links
            ],
        )
    return {"deleted": True}


def restore_task(actor: TaskActor, task_id: str) -> dict:
    """Contracts §1 `POST /tasks/{id}/restore` — a task with no workspace by
    its creator only, never its assignee; a CRM task by a workspace Admin
    (`007` FR-CRM-DEL-01), and only while it has a CRM link left
    (FR-TASK-DATA-05)."""
    task = _load(actor, task_id, deleted=True)
    if not visibility.may_restore(task.me, task.facts):
        raise TaskRefused(403, RESTORE_CRM if task.facts.workspace_id else RESTORE_OWN)
    if task.facts.workspace_id is not None and not task.crm_links:
        raise TaskRefused(422, NO_LINK_LEFT)
    written = _write(actor, {"op": "restore", "table": "tasks", "id": task_id})
    return _after_write(actor, task, written)
