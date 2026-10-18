"""Task data access — spec 008 plan §5.2, §5.3; data-model §5.2, §5.3.

Thin named functions over the same service-role client as
`services/supabase.py` (read from that module at call time, so whatever
points it at a database points this store there too), so every query here
bypasses RLS. The CRM store's
invariant is that every read takes a workspace first; this one's is the
opposite: its reads cross workspaces and people, and none of them reaches a
route without passing `domain/tasks/visibility` (FR-TASK-VIS-01).

Writes go only through `apply()` (`task_apply`, 049) and `purge_expired()`
(`task_purge_expired`), which set the actor in the same transaction so the
audit trigger records every change (FR-TASK-DATA-10). `rpc()` maps the
database's refusals exactly as the CRM's does.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from postgrest.exceptions import APIError

from app.services import settings_store
from app.services import supabase as db
from app.services.crm.store import _literal, refusal_for

logger = logging.getLogger("nldw-task-master")

#: A CRM link's record type → its table.
RECORD_TABLES = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "campaign": "crm_campaigns",
}
_PROFILE_COLUMNS = "id, email, display_name, role, is_active, account_type, tasks_level, timezone"
_ID_BATCH = 100
#: A Life OS key is 71 characters, quoted and percent-encoded in the URL: 100
#: of them pass the gateway's 8 KB request line (414), 40 stay well inside it.
_KEY_BATCH = 40
_PAGE = 1000
#: The audit's names of a task's rows, before and after the rename (FR-TASK-CHG-07).
_TASK_TABLES = ("tasks", "crm_tasks")
_LINK_TABLES = ("task_links", "crm_task_links")


def rpc(fn: str, params: dict[str, Any]) -> Any:
    """Call a task database function, mapping its refusals as the CRM does."""
    try:
        return db.supabase.rpc(fn, params).execute().data
    except APIError as exc:
        refused = refusal_for(exc.code, exc.message)
        if refused is not None:
            raise refused from None
        logger.error('{"event": "task_rpc_failed", "fn": "%s", "code": "%s"}', fn, exc.code)
        raise


def apply(actor: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Data-model §5.2 — every task write, in one transaction. Returns one
    `{ref, id, row}` per op, in order."""
    return rpc("task_apply", {"p_actor": actor, "p_ops": ops}) or []


def purge_expired(actor: dict[str, Any], before: str) -> int:
    """Data-model §5.3 — purges tasks with no workspace deleted before
    `before`; returns how many."""
    return int(rpc("task_purge_expired", {"p_actor": actor, "p_before": before}) or 0)


def _in_batches(
    query_for, column: str, values: list[str], batch: int = _ID_BATCH
) -> list[dict[str, Any]]:
    wanted = sorted(set(values))
    rows: list[dict[str, Any]] = []
    for i in range(0, len(wanted), batch):
        rows += query_for().in_(column, wanted[i : i + batch]).execute().data or []
    return rows


# ---------------------------------------------------------------------------
# Tasks and their links
# ---------------------------------------------------------------------------


def get_task(task_id: str) -> dict[str, Any] | None:
    """One task, in the Trash or not; the caller decides."""
    rows = db.supabase.table("tasks").select("*").eq("id", task_id).execute().data
    return rows[0] if rows else None


def get_tasks(task_ids: list[str]) -> list[dict[str, Any]]:
    """These tasks, in the Trash or not."""
    if not task_ids:
        return []
    return _in_batches(lambda: db.supabase.table("tasks").select("*"), "id", task_ids)


def list_links(task_ids: list[str]) -> list[dict[str, Any]]:
    """Every link of these tasks, CRM and project alike, oldest first."""
    if not task_ids:
        return []
    rows = _in_batches(lambda: db.supabase.table("task_links").select("*"), "task_id", task_ids)
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def subtask_counts(task_ids: list[str]) -> dict[str, int]:
    """How many live subtasks each of these tasks has."""
    if not task_ids:
        return {}
    rows = _in_batches(
        lambda: db.supabase.table("tasks").select("parent_id").is_("deleted_at", "null"),
        "parent_id",
        task_ids,
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["parent_id"]] = counts.get(row["parent_id"], 0) + 1
    return counts


def _array_literal(value: str) -> str:
    """One text as a Postgres array literal, quoted, so a comma, brace or
    quote in it stays part of the value."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{{"{escaped}"}}'


def assigned_tasks(
    user_id: str,
    statuses: tuple[str, ...],
    *,
    tag: str | None = None,
    priority: str | None = None,
    ids: list[str] | None = None,
    q: str | None = None,
    before: tuple[str, str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Contracts §1 `GET /tasks` — the live tasks assigned to the person with
    one of `statuses`, narrowed by a tag, a priority, a set of ids (a link
    filter, read `_ID_BATCH` ids per request) and text in the title or notes,
    matched literally. With `limit`, one page, most recently changed first
    (then by id), after the `(updated_at, id)` cursor `before`; without, every
    row, page by page."""
    if ids is not None and not ids:
        return []
    wanted = sorted(set(ids)) if ids is not None else None
    chunks = (
        [wanted[i : i + _ID_BATCH] for i in range(0, len(wanted), _ID_BATCH)]
        if wanted is not None
        else [None]
    )

    def query_for(chunk: list[str] | None):
        query = (
            db.supabase.table("tasks")
            .select("*")
            .eq("assignee_id", user_id)
            .in_("status", list(statuses))
            .is_("deleted_at", "null")
        )
        if tag:
            query = query.contains("tags", _array_literal(tag))
        if priority:
            query = query.eq("priority", priority)
        if chunk is not None:
            query = query.in_("id", chunk)
        term = _literal(q or "")
        if term:
            query = query.or_(f"title.ilike.%{term}%,body.ilike.%{term}%")
        if before:
            at, last_id = before
            query = query.or_(f'updated_at.lt."{at}",and(updated_at.eq."{at}",id.gt.{last_id})')
        return query

    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        if limit is not None:
            rows += (
                query_for(chunk)
                .order("updated_at", desc=True)
                .order("id")
                .limit(limit)
                .execute()
                .data
                or []
            )
            continue
        offset = 0
        while True:
            page = (
                query_for(chunk).order("id").range(offset, offset + _PAGE - 1).execute().data or []
            )
            rows += page
            if len(page) < _PAGE:
                break
            offset += _PAGE
    if limit is None:
        return sorted(rows, key=lambda r: r["id"])
    rows.sort(key=lambda r: r["id"])
    rows.sort(key=lambda r: datetime.fromisoformat(str(r["updated_at"])), reverse=True)
    return rows[:limit]


def created_tasks(
    user_id: str, statuses: tuple[str, ...], *, others_only: bool = False
) -> list[dict[str, Any]]:
    """Contracts §2 — the live tasks the person created with one of
    `statuses` (`tasks_created_by`), every row, page by page; with
    `others_only`, only those assigned to someone else (Assigned by me,
    FR-TASK-VIEW-02)."""

    def query_for():
        query = (
            db.supabase.table("tasks")
            .select("*")
            .eq("created_by", user_id)
            .in_("status", list(statuses))
            .is_("deleted_at", "null")
        )
        return query.neq("assignee_id", user_id) if others_only else query

    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = query_for().order("id").range(offset, offset + _PAGE - 1).execute().data or []
        rows += page
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


def done_between(user_id: str, start: str, end: str) -> list[dict[str, Any]]:
    """Plan §5.9 — the live Done tasks assigned to the person, completed in
    `[start, end)` (`tasks_done_on`, 047), oldest first."""
    return (
        db.supabase.table("tasks")
        .select("*")
        .eq("assignee_id", user_id)
        .eq("status", "done")
        .is_("deleted_at", "null")
        .gte("completed_at", start)
        .lt("completed_at", end)
        .order("completed_at")
        .order("id")
        .execute()
        .data
        or []
    )


def linked_task_ids(record_type: str, record_id: str) -> list[str]:
    """The tasks linking this record or project (contracts §1 `link_type`)."""
    rows = (
        db.supabase.table("task_links")
        .select("task_id")
        .eq("record_type", record_type)
        .eq("record_id", record_id)
        .execute()
        .data
        or []
    )
    return sorted({r["task_id"] for r in rows})


def subtasks(parent_ids: list[str]) -> list[dict[str, Any]]:
    """The live tasks whose parent is one of these, oldest first."""
    if not parent_ids:
        return []
    rows = _in_batches(
        lambda: db.supabase.table("tasks").select("*").is_("deleted_at", "null"),
        "parent_id",
        parent_ids,
    )
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def task_history(task_id: str) -> list[dict[str, Any]]:
    """FR-TASK-LIST-06, FR-TASK-CHG-07 — the task's audit rows and its
    links', under the old and new table names, oldest first."""
    parts = [f"and(target_table.eq.{t},target_id.eq.{task_id})" for t in _TASK_TABLES] + [
        f"and(target_table.eq.{t},or(after->>task_id.eq.{task_id},before->>task_id.eq.{task_id}))"
        for t in _LINK_TABLES
    ]
    rows = (
        db.supabase.table("audit_log").select("*").or_(",".join(parts)).order("id").execute().data
        or []
    )
    return sorted(rows, key=lambda r: r["id"])


def deleted_personal_tasks(user_id: str) -> list[dict[str, Any]]:
    """FR-TASK-DATA-11 — the person's own deleted tasks with no workspace,
    newest deleted first."""
    rows = (
        db.supabase.table("tasks")
        .select("*")
        .eq("created_by", user_id)
        .is_("workspace_id", "null")
        .not_.is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    return sorted(rows, key=lambda r: (r["deleted_at"], r["id"]), reverse=True)


def existing_import_keys(keys: list[str]) -> set[str]:
    """FR-TASK-IMP-05 — which of these Life OS keys a task already carries,
    in the Trash or not (`tasks_lifeos_key` covers both)."""
    if not keys:
        return set()
    rows = _in_batches(
        lambda: db.supabase.table("tasks").select("source_ref").eq("source", "import"),
        "source_ref",
        keys,
        _KEY_BATCH,
    )
    return {r["source_ref"] for r in rows}


def get_import_run(run_id: str) -> dict[str, Any] | None:
    """One Life OS import run (051); the caller checks its uploader."""
    rows = db.supabase.table("task_import_runs").select("*").eq("id", run_id).execute().data
    return rows[0] if rows else None


def latest_import_run_id(uploader_id: str) -> str | None:
    """The uploader's last dry run (051 `task_import_runs_recent`), which is
    the only run the real run may use (FR-TASK-IMP-04)."""
    rows = (
        db.supabase.table("task_import_runs")
        .select("id")
        .eq("uploader_id", uploader_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------------------
# Comments and messages — FR-TASK-DEL-04, -07 (052)
# ---------------------------------------------------------------------------


def list_comments(task_id: str) -> list[dict[str, Any]]:
    """The task's comments that are not deleted, oldest first."""
    rows = (
        db.supabase.table("task_comments")
        .select("*")
        .eq("task_id", task_id)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def get_comment(comment_id: str) -> dict[str, Any] | None:
    """One comment, deleted or not; the caller decides."""
    rows = db.supabase.table("task_comments").select("*").eq("id", comment_id).execute().data
    return rows[0] if rows else None


def claim_notice(task_id: str, event: str, recipient_id: str, occurred_at: str) -> str | None:
    """Data-model §2.6 — the sent marker's id when this message was not sent
    yet (INSERT ... ON CONFLICT DO NOTHING RETURNING id), else None."""
    rows = (
        db.supabase.table("task_notice_sends")
        .upsert(
            {
                "task_id": task_id,
                "event": event,
                "recipient_id": recipient_id,
                "occurred_at": occurred_at,
            },
            on_conflict="task_id,event,recipient_id,occurred_at",
            ignore_duplicates=True,
        )
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


def mark_notice_sent(notice_id: str, sent_at: str, channels: list[str]) -> None:
    """Records when a claimed message went, and through which channels."""
    db.supabase.table("task_notice_sends").update({"sent_at": sent_at, "channels": channels}).eq(
        "id", notice_id
    ).execute()


def set_nudge_days(user_id: str, days: int) -> dict[str, Any]:
    """FR-TASK-DEL-06 — the person's own `profiles.task_nudge_days` (052)."""
    return (
        db.supabase.table("profiles")
        .update({"task_nudge_days": days})
        .eq("id", user_id)
        .execute()
        .data[0]
    )


# ---------------------------------------------------------------------------
# People, memberships, projects and workspaces
# ---------------------------------------------------------------------------


def active_profiles() -> list[dict[str, Any]]:
    """Every active account, staff and CRM-only, with what the task rules
    read about them."""
    return (
        db.supabase.table("profiles").select(_PROFILE_COLUMNS).eq("is_active", True).execute().data
        or []
    )


def project_people(project_id: str) -> set[str]:
    """The people who are or were allocated to a project (TP-01)."""
    rows = (
        db.supabase.table("allocations")
        .select("user_id")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    return {r["user_id"] for r in rows}


def projects_named(q: str) -> list[dict[str, Any]]:
    """Unarchived TVA projects whose name holds `q`, matched literally."""
    term = _literal(q)
    if not term:
        return []
    return (
        db.supabase.table("projects")
        .select("id, name, status, is_archived")
        .eq("is_archived", False)
        .ilike("name", f"%{term}%")
        .order("name")
        .execute()
        .data
        or []
    )


def get_profiles(user_ids: list[str]) -> list[dict[str, Any]]:
    """These people, with what the task rules read about them."""
    if not user_ids:
        return []
    return _in_batches(
        lambda: db.supabase.table("profiles").select(_PROFILE_COLUMNS), "id", user_ids
    )


def memberships_of(user_id: str) -> list[dict[str, Any]]:
    """The person's CRM memberships: `{workspace_id, role}`."""
    return (
        db.supabase.table("crm_memberships")
        .select("workspace_id, role")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )


def member_ids(workspace_id: str) -> set[str]:
    """The people who are members of a workspace."""
    rows = (
        db.supabase.table("crm_memberships")
        .select("user_id")
        .eq("workspace_id", workspace_id)
        .execute()
        .data
        or []
    )
    return {r["user_id"] for r in rows}


def allocated_project_ids(user_id: str) -> set[str]:
    """Every project the person is or was allocated to, released rows
    included (TP-01: "are or were")."""
    rows = (
        db.supabase.table("allocations").select("project_id").eq("user_id", user_id).execute().data
        or []
    )
    return {r["project_id"] for r in rows}


def get_projects(project_ids: list[str]) -> list[dict[str, Any]]:
    """These TVA projects: id, name, status and whether archived."""
    if not project_ids:
        return []
    return _in_batches(
        lambda: db.supabase.table("projects").select("id, name, status, is_archived"),
        "id",
        project_ids,
    )


def get_workspaces(workspace_ids: list[str]) -> list[dict[str, Any]]:
    """These CRM workspaces: id, key, name and whether TVA-linked."""
    if not workspace_ids:
        return []
    return _in_batches(
        lambda: db.supabase.table("crm_workspaces").select("id, key, name, tva_linked"),
        "id",
        workspace_ids,
    )


def record_workspace(record_type: str, record_id: str) -> dict[str, Any] | None:
    """A CRM record's `{id, workspace_id, deleted_at}`, in any workspace, or
    None. The caller checks the workspace against the person's."""
    table = RECORD_TABLES.get(record_type)
    if table is None:
        return None
    rows = (
        db.supabase.table(table)
        .select("id, workspace_id, deleted_at")
        .eq("id", record_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def default_level() -> int:
    """`app_settings.tasks_default_level` (050), 0 when unreadable."""
    try:
        return int(settings_store.get("tasks_default_level", 0))
    except (TypeError, ValueError):
        return 0
