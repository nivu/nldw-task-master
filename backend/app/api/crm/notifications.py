"""The bell — spec 007 contracts §2.10; FR-CRM-NOTIF-01, FR-CRM-WS-02,
FR-CRM-ACC-10.

HTTP only. The bell is about the person, across their workspaces, so these
routes take no `workspace` and sit on `AnyAccountDep`; the service lists and
counts only the workspaces they are a member of now. The web app polls the
count every 60 seconds and on focus through the proxy: no Realtime and no
browser grant (FR-CRM-ACC-10).
"""

from __future__ import annotations

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
    before: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """`[{id, workspace_key, kind, title, record_type, record_id, created_at,
    read_at}]`, newest first; `before` is the last one's `created_at`."""
    return service.list_for(user.id, unread_only=unread_only, before=before, limit=limit)


@router.get("/unread-count")
def unread_count(user: AnyAccountDep) -> dict:
    return {"count": service.unread_for(user.id)}


@router.post("/read")
def mark_read(payload: NotificationsRead, user: AnyAccountDep) -> dict:
    if payload.all == (payload.ids is not None):
        raise ProblemDetail(422, "Give the notifications to mark read, or all.")
    return {"updated": service.mark_read(user.id, None if payload.all else payload.ids)}
