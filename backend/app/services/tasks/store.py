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
from typing import Any

from postgrest.exceptions import APIError

from app.services import settings_store
from app.services import supabase as db
from app.services.crm.store import refusal_for

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


def _in_batches(query_for, column: str, values: list[str]) -> list[dict[str, Any]]:
    wanted = sorted(set(values))
    rows: list[dict[str, Any]] = []
    for i in range(0, len(wanted), _ID_BATCH):
        rows += query_for().in_(column, wanted[i : i + _ID_BATCH]).execute().data or []
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


# ---------------------------------------------------------------------------
# People, memberships, projects and workspaces
# ---------------------------------------------------------------------------


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
