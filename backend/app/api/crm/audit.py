"""A workspace's audit — spec 007 contracts §2.3; FR-CRM-AUD-03.

Workspace admins only, their own workspace only. A TVA admin sees a
workspace's CRM audit only by being an Admin of it (Q-22): TVA rows
(`workspace_id` NULL) are never read here, and CRM rows never appear in the
TVA audit (`GET /admin/audit`, FR-CRM-AUD-04).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmAdminDep
from app.domain.audit import actor_name
from app.services import supabase as db
from app.services.crm import store

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
