"""A workspace's audit — spec 007 contracts §2.3; FR-CRM-AUD-03.

Workspace admins only, their own workspace only. A TVA admin sees a
workspace's CRM audit only by being an Admin of it (Q-22): TVA rows
(`workspace_id` NULL) are never read here, and CRM rows never appear in the
TVA audit (`GET /admin/audit`, FR-CRM-AUD-04).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmAdminDep
from app.domain.audit import actor_name
from app.domain.tasks import visibility as task_visibility
from app.services import supabase as db
from app.services.crm import store
from app.services.crm.records import task_parents
from app.services.tasks import viewer as task_viewer

router = APIRouter(prefix="/crm", tags=["crm"])

#: The record a row is about, by the table its change landed in. Record
#: tables join as their slices add them.
RECORD_TABLES = {
    "workspace": "crm_workspaces",
    "membership": "crm_memberships",
    "setting": "crm_workspace_settings",
    "option": "crm_select_options",
    "fx_rate": "crm_fx_rates",
    "company": "crm_companies",
    "company_domain": "crm_company_domains",
    "person": "crm_people",
    "drive_link": "crm_drive_links",
}
RECORD_TYPES = {table: kind for kind, table in RECORD_TABLES.items()}

RecordType = Literal[
    "workspace",
    "membership",
    "setting",
    "option",
    "fx_rate",
    "company",
    "company_domain",
    "person",
    "drive_link",
]


@router.get("/audit")
def read_audit(
    ctx: CrmAdminDep,
    record_type: RecordType | None = None,
    record_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = Query(default=None, max_length=100),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    before: int | None = Query(default=None, description="An entry id; older entries follow."),
) -> list[dict]:
    """Newest first. `from` and `to` are whole days in the admin's own time
    zone (FR-CRM-ACC-15); `to` is inclusive."""
    zone = ZoneInfo(ctx.user.profile.get("timezone") or "Asia/Kolkata")
    rows = store.list_audit(
        ctx.workspace_id,
        target_table=RECORD_TABLES[record_type] if record_type else None,
        target_id=record_id,
        actor_id=actor_id,
        action=action,
        since=datetime.combine(from_, time(), zone) if from_ else None,
        until=datetime.combine(to + timedelta(days=1), time(), zone) if to else None,
        before=before,
        limit=limit,
    )
    rows = _task_rows_shown(ctx.user, rows)
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    return [
        {
            "id": row["id"],
            "at": row["at"],
            "actor_name": actor_name(row.get("actor_id"), names),
            "actor_kind": row.get("actor_label"),
            "channel": row.get("channel"),
            "action": row["action"],
            "record_type": RECORD_TYPES.get(row["target_table"], row["target_table"]),
            "record_id": row.get("target_id"),
            "before": row.get("before"),
            "after": row.get("after"),
        }
        for row in rows
    ]


def _task_rows_shown(user: Any, rows: list[dict]) -> list[dict]:
    """008 FR-TASK-VIS-07, FR-TASK-DATA-09 — an Admin who cannot see a task's
    TVA project gets no row of that project link (no id, no name), and a
    task's parent they cannot see is left out of its before and after."""
    tasks = [r for r in rows if r["target_table"] in ("tasks", "crm_tasks")]
    links = [
        r
        for r in rows
        if r["target_table"] == "task_links"
        and ((r.get("after") or r.get("before") or {}).get("record_type") == "tva_project")
    ]
    if not tasks and not links:
        return rows
    me = task_viewer.load(user) if links else None
    hidden = {
        r["id"]
        for r in links
        if me is None
        or not task_visibility.can_see_project(
            me, (r.get("after") or r.get("before") or {}).get("record_id")
        )
    }
    parents = task_parents(
        user,
        {(r.get(side) or {}).get("parent_id") or "" for r in tasks for side in ("before", "after")},
    )
    private = {pid for pid, shown in parents.items() if not shown or shown.get("private")}
    out = []
    for row in rows:
        if row["id"] in hidden:
            continue
        if row["target_table"] in ("tasks", "crm_tasks") and private:
            row = {
                **row,
                **{
                    side: {
                        k: v
                        for k, v in row[side].items()
                        if not (k == "parent_id" and v in private)
                    }
                    for side in ("before", "after")
                    if isinstance(row.get(side), dict)
                },
            }
        out.append(row)
    return out
