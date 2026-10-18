"""My Tasks and reading one task — spec 008 FR-TASK-LIST-01 to -07,
FR-TASK-VIS-02, -05, FR-TASK-DATA-09, -11, FR-TASK-CHG-06; TP-06, TP-15,
TP-16; contracts §1 `GET /tasks`, `GET /tasks/{id}`, `GET /tasks/trash`;
plan §5.6. Phase 2 (contracts §2): Waiting on and Assigned by me
(FR-TASK-VIEW-01, -02) and a TVA project's task panel (FR-TASK-VIEW-03).

One query plan per read: the caller's open and waiting tasks in one paged
read of `(assignee_id, status)`, the closed page in one more, then each task
through the viewer filter and `present` (FR-TASK-VIS-01). Sections are the
effective bucket in the caller's own zone (TP-06, FR-TASK-CHG-06); nothing is
rewritten.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from app.domain.tasks import rules, service, visibility
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import TaskActor
from app.services.crm.records import task_parents
from app.services.crm.store import _literal
from app.services.tasks import present, store, viewer

SECTIONS: Final = ("today", "this_week", "this_month", "later")
CLOSED: Final = ("done", "cancelled")
#: FR-TASK-DATA-11 (TP-12): a deleted task with no workspace is kept this long.
KEPT_DAYS: Final = 90
TOO_SHORT: Final = "Type at least two characters."
BAD_CURSOR: Final = "closed_before must be the next_closed of the page before."
#: The audit's bookkeeping columns: never shown as a change.
_BOOKKEEPING: Final = frozenset(
    {
        "id",
        "workspace_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "position",
        "deleted_at",
        "deleted_by",
        "deleted_group",
        "flag_level",
        "flags",
        "flags_computed_at",
    }
)
_TASK_TABLES: Final = ("tasks", "crm_tasks")


def _zone(actor: TaskActor) -> str:
    return actor.user.profile.get("timezone") or rules.DEFAULT_ZONE


def _seen(me: visibility.Viewer, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The viewer filter. Every row here is assigned to the viewer, so sight
    needs no project: a task with no workspace is theirs, a CRM task theirs
    only while they are a member (spec §3.1)."""
    return [r for r in rows if visibility.sight(me, present.facts(r, [])) != "none"]


def _cursor(text: str) -> tuple[str, str]:
    """`next_closed` read back: the last task's `updated_at` and id. The
    pair, not the time alone, so tasks changed in one transaction (one
    `now()`) are never skipped at a page's edge."""
    at, _, task_id = text.rpartition(",")
    try:
        return datetime.fromisoformat(at).isoformat(), str(UUID(task_id))
    except ValueError:
        raise TaskRefused(422, BAD_CURSOR) from None


def my_tasks(
    actor: TaskActor,
    *,
    tag: str | None = None,
    priority: str | None = None,
    link_type: str | None = None,
    link_id: str | None = None,
    q: str | None = None,
    closed_limit: int = 20,
    closed_before: str | None = None,
) -> dict[str, Any]:
    """Contracts §1 `GET /tasks` (`view=mine`): open and waiting tasks
    assigned to the caller by effective bucket, each section by due date
    (none last), priority and title (FR-TASK-LIST-01, -02); one page of done
    and cancelled ones, newest first; filtered by tag, priority, link and
    text (FR-TASK-LIST-07). `closed_before` is the `next_closed` of the
    page before: `<updated_at>,<id>` of its last task."""
    zone = _zone(actor)
    today = rules.day_of(service._now(), zone)
    term = " ".join(q.split()) if q is not None else None
    # The length of what is matched: the characters PostgREST cannot take
    # are dropped first, so "()" is not an empty filter.
    if term is not None and len(_literal(term).replace("\\", "")) < 2:
        raise TaskRefused(422, TOO_SHORT)
    if tag:
        try:
            [tag] = rules.normalise_tags([tag])
        except ValueError as exc:
            raise TaskRefused(422, str(exc)) from None
    before = _cursor(closed_before) if closed_before else None
    ids = store.linked_task_ids(link_type, link_id) if link_type and link_id else None
    me = viewer.load(actor.user)
    narrow = {"tag": tag, "priority": priority, "ids": ids, "q": term}

    open_rows = _seen(me, store.assigned_tasks(actor.user.id, rules.OPEN, **narrow))
    page = store.assigned_tasks(
        actor.user.id,
        CLOSED,
        **narrow,
        before=before,
        limit=closed_limit + 1,
    )
    more = len(page) > closed_limit
    page = page[:closed_limit]
    closed_rows = _seen(me, page)

    shown = present.present(me, sorted(open_rows, key=rules.sort_key), today=today)
    sections: dict[str, list[dict[str, Any]]] = {s: [] for s in SECTIONS}
    for task in shown:
        sections.setdefault(task["effective_bucket"] or "later", []).append(task)
    return {
        "as_of": today.isoformat(),
        "timezone": zone,
        "sections": sections,
        "closed": present.present(me, closed_rows, today=today),
        "next_closed": f"{page[-1]['updated_at']},{page[-1]['id']}" if more and page else None,
    }


def _day_of(value: Any, zone: str) -> date:
    return rules.day_of(datetime.fromisoformat(str(value)), zone)


def waiting(actor: TaskActor) -> dict[str, Any]:
    """Contracts §2 `GET /tasks?view=waiting` (FR-TASK-VIEW-01) — the
    caller's waiting tasks grouped by whom or what they wait on, groups by
    label, each task by due date, priority and title with its age in days
    (since it was created, in the caller's own day)."""
    zone = _zone(actor)
    today = rules.day_of(service._now(), zone)
    me = viewer.load(actor.user)
    rows = sorted(_seen(me, store.assigned_tasks(actor.user.id, ("waiting",))), key=rules.sort_key)
    groups: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for row, task in zip(rows, present.present(me, rows, today=today), strict=True):
        task["age_days"] = max(0, (today - _day_of(row["created_at"], zone)).days)
        on = task["waiting_on"] or {"id": None, "label": ""}
        groups.setdefault((on["id"], on["label"] or ""), []).append(task)
    return {
        "groups": [
            {"waiting_on": {"id": key[0], "label": key[1]}, "tasks": tasks}
            for key, tasks in sorted(
                groups.items(),
                # A picked person and a free-text label can share a label: no
                # id first, then by id, so None never meets a uuid.
                key=lambda g: (g[0][1].casefold(), g[0][0] is not None, g[0][0] or "", g[0][1]),
            )
        ]
    }


def _open_then_closed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open and waiting first, by due date, priority and title; then done
    and cancelled, most recently changed first."""
    live = sorted((r for r in rows if r["status"] in rules.OPEN), key=rules.sort_key)
    closed = sorted(
        (r for r in rows if r["status"] not in rules.OPEN),
        key=lambda r: (datetime.fromisoformat(str(r["updated_at"])), r["id"]),
        reverse=True,
    )
    return live + closed


def assigned_by_me(actor: TaskActor) -> dict[str, Any]:
    """Contracts §2 `GET /tasks?view=assigned_by_me` (FR-TASK-VIEW-02) — the
    live tasks the caller created for someone else, with their assignee and
    status, that the caller may still see."""
    today = rules.day_of(service._now(), _zone(actor))
    me = viewer.load(actor.user)
    rows = store.created_tasks(actor.user.id, rules.OPEN + CLOSED, others_only=True)
    return {"tasks": present.present(me, _open_then_closed(_seen(me, rows)), today=today)}


def project_tasks(
    actor: TaskActor, project_id: str, *, include_closed: bool = False
) -> dict[str, Any]:
    """Contracts §2 `GET /tasks/project/{project_id}` (FR-TASK-VIEW-03) — the
    project's live tasks the caller may see (§3.1), each checked on its own:
    a CRM task linking it only for that workspace's members. 404 a project
    the caller cannot see."""
    me = viewer.load(actor.user)
    project = next(iter(store.get_projects([project_id])), None)
    if project is None or not visibility.can_see_project(me, project_id):
        raise TaskRefused(404, service.NO_SUCH_PROJECT)
    wanted = rules.OPEN + (CLOSED if include_closed else ())
    rows = [
        r
        for r in store.get_tasks(store.linked_task_ids("tva_project", project_id))
        if not r.get("deleted_at") and r["status"] in wanted
    ]
    links = store.list_links([r["id"] for r in rows])
    seen = [
        r
        for r in rows
        if visibility.sight(me, present.facts(r, [lk for lk in links if lk["task_id"] == r["id"]]))
        != "none"
    ]
    today = rules.day_of(service._now(), _zone(actor))
    return {
        "project": {"id": project["id"], "name": project["name"]},
        "tasks": present.present(me, _open_then_closed(seen), today=today),
    }


def _changes(
    row: dict[str, Any], parents: dict[str, Any], *, newcomer: bool = False
) -> list[dict[str, Any]]:
    """A task row's changed fields, Notes by its name, a parent as the reader
    may see it (FR-TASK-DATA-09). Creation, deletion and link rows say what
    happened in their action alone. For a `newcomer` (made assignee by this
    private-era row) only the assignee keeps its `before` value: the rest
    belong to an era they were not part of (FR-TASK-VIS-05)."""
    before, after = row.get("before"), row.get("after")
    if row["target_table"] not in _TASK_TABLES or not before or not after:
        return []
    out = []
    for name in sorted(set(before) | set(after)):
        if name in _BOOKKEEPING:
            continue
        old, new = before.get(name), after.get(name)
        if name == "parent_id":
            old, new = parents.get(old) if old else None, parents.get(new) if new else None
        if newcomer and name != "assignee_id":
            old = None
        out.append({"field": "notes" if name == "body" else name, "before": old, "after": new})
    return out


def history(
    actor: TaskActor, me: visibility.Viewer, task: visibility.TaskFacts
) -> list[dict[str, Any]]:
    """FR-TASK-LIST-06, FR-TASK-VIS-05 (TP-16) — the task's audit rows, oldest
    first. A row written while the task had no workspace is shown only to its
    creator and to whoever was its assignee at that row, replayed from the
    rows in order; one that made the reader assignee shows them no earlier
    values but the assignee's; a project link row only to a reader who may
    see the project (FR-TASK-VIS-07)."""
    rows = store.task_history(task.id)
    era: dict[str, Any] = {}
    kept: list[tuple[dict[str, Any], bool]] = []
    parent_ids: set[str] = set()
    for row in rows:
        side = row.get("after") or row.get("before") or {}
        earlier = dict(era)
        if row["target_table"] in _TASK_TABLES:
            era.update({k: side[k] for k in ("created_by", "assignee_id") if k in side})
        private = row.get("workspace_id") is None
        if private and not visibility.history_visible(me, row, era):
            continue
        newcomer = (
            private
            and bool(row.get("before"))
            and row["target_table"] in _TASK_TABLES
            and not visibility.history_visible(me, row, earlier)
        )
        if (
            side.get("record_type") == "tva_project"
            and visibility.project_field(me, task) == "absent"
        ):
            continue
        for part in (row.get("before") or {}, row.get("after") or {}):
            if row["target_table"] in _TASK_TABLES and part.get("parent_id"):
                parent_ids.add(part["parent_id"])
        kept.append((row, newcomer))
    parents = task_parents(actor.user, parent_ids) if parent_ids else {}
    names = {
        p["id"]: p["display_name"]
        for p in store.get_profiles(sorted({r["actor_id"] for r, _ in kept if r.get("actor_id")}))
    }
    return [
        {
            "at": row["at"],
            "actor_name": names.get(row.get("actor_id")),
            "action": row["action"],
            "changes": _changes(row, parents, newcomer=newcomer),
        }
        for row, newcomer in kept
    ]


def detail(actor: TaskActor, task_id: str) -> dict[str, Any]:
    """Contracts §1 `GET /tasks/{id}` — the task, its subtasks the caller may
    see (each checked on its own, FR-TASK-DATA-09) and its history."""
    me = viewer.load(actor.user)
    row = store.get_task(task_id)
    if row is None or row.get("deleted_at"):
        raise TaskRefused(404, service.NO_SUCH_TASK)
    facts = present.facts(row, store.list_links([task_id]))
    if visibility.sight(me, facts) == "none":
        raise TaskRefused(404, service.NO_SUCH_TASK)
    today = rules.day_of(service._now(), _zone(actor))
    children = store.subtasks([task_id])
    links = store.list_links([c["id"] for c in children])
    seen = [
        c
        for c in children
        if visibility.sight(me, present.facts(c, [lk for lk in links if lk["task_id"] == c["id"]]))
        != "none"
    ]
    out = present.present(me, [row], today=today)[0]
    out["subtasks"] = present.present(me, seen, today=today)
    out["history"] = history(actor, me, facts)
    return out


def trash(actor: TaskActor) -> list[dict[str, Any]]:
    """Contracts §1 `GET /tasks/trash` — the caller's own deleted tasks with no
    workspace, newest deleted first, with the days left before the purge
    (FR-TASK-DATA-11)."""
    me = viewer.load(actor.user)
    today = rules.day_of(service._now(), _zone(actor))
    rows = store.deleted_personal_tasks(actor.user.id)
    out = []
    for row, task in zip(rows, present.present(me, rows, today=today), strict=True):
        deleted = rules.day_of(datetime.fromisoformat(str(row["deleted_at"])), _zone(actor))
        task["deleted_at"] = row["deleted_at"]
        task["days_left"] = max(0, KEPT_DAYS - (today - deleted).days)
        out.append(task)
    return out
