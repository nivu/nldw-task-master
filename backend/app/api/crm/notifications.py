"""The bell — spec 007 contracts §2.10; FR-CRM-NOTIF-01, FR-CRM-WS-02,
FR-CRM-ACC-10.

HTTP only. The bell is about the person, across their workspaces, so these
routes take no `workspace` and sit on `AnyAccountDep`; the service lists and
counts only the workspaces they are a member of now. The web app polls the
count every 60 seconds and on focus through the proxy: no Realtime and no
browser grant (FR-CRM-ACC-10).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query

from app.api.deps import AnyAccountDep
from app.api.errors import ProblemDetail
from app.schemas.crm import NotificationsRead
from app.services.crm import notifications as service

router = APIRouter(prefix="/crm/notifications", tags=["crm"])


@router.get("")
def list_notifications(
    user: AnyAccountDep,
    unread_only: bool = False,
    before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """`[{id, workspace_key, kind, title, record_type, record_id, created_at,
    read_at}]`, newest first; `before` is the last one's `created_at` (a
    time with no zone is UTC)."""
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    return service.list_for(
        user.id,
        unread_only=unread_only,
        before=before.isoformat() if before else None,
        limit=limit,
    )


@router.get("/unread-count")
def unread_count(user: AnyAccountDep) -> dict:
    return {"count": service.unread_for(user.id)}


@router.post("/read")
def mark_read(payload: NotificationsRead, user: AnyAccountDep) -> dict:
    if payload.all == (payload.ids is not None):
        raise ProblemDetail(422, "Give the notifications to mark read, or all.")
    ids = None if payload.all else [str(i) for i in payload.ids or []]
    return {"updated": service.mark_read(user.id, ids)}
