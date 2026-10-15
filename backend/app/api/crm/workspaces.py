"""Workspaces, the CRM's start-up call and display preferences — spec 007
contracts §2.1; FR-CRM-WS-01 to -04, FR-CRM-ACC-15.

`/crm/workspaces`, `/use` and `/me/preferences` take no `workspace`: they are
about the person, not one workspace, so they sit on `AnyAccountDep` directly.
`/crm/bootstrap` is the one call a CRM page makes on load; from 1b it also
carries the bell's unread count and, for Admins, the background jobs that
are late.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.crm.deps import NO_SUCH_WORKSPACE, CrmReadDep
from app.api.crm.settings import present_lists
from app.api.deps import AnyAccountDep, CurrentUser
from app.api.errors import ProblemDetail
from app.schemas.crm import PreferencesUpdate
from app.services import supabase as db
from app.services.crm import email_sync, jobs, notifications, store
from app.services.crm.context import workspaces_of

router = APIRouter(prefix="/crm", tags=["crm"])

_PUBLIC_WORKSPACE = ("id", "key", "name", "tva_linked", "has_entity")


@router.get("/workspaces")
def list_workspaces(user: AnyAccountDep) -> list[dict]:
    """FR-CRM-WS-03, -04 — only the caller's own workspaces, with their role in
    each and which one they used last (the default for reads, D13)."""
    return [
        {**{k: w[k] for k in _PUBLIC_WORKSPACE}, "role": w["role"], "last_used": w["last_used"]}
        for w in workspaces_of(user.id)
    ]


@router.post("/workspaces/{key}/use")
def use_workspace(key: str, user: AnyAccountDep) -> dict:
    """FR-CRM-WS-04 — remembered server-side, so the next visit from any device
    (and an MCP read that names no workspace) opens it."""
    mine = next((w for w in workspaces_of(user.id) if key in (w["key"], w["id"])), None)
    if mine is None:
        raise ProblemDetail(404, NO_SUCH_WORKSPACE)
    store.touch_last_used(mine["membership_id"], datetime.now(UTC))
    return {"ok": True}


@router.get("/bootstrap")
def bootstrap(ctx: CrmReadDep) -> dict:
    """Everything a CRM page needs before its own data, in one call."""
    settings = {row["key"]: row["value"] for row in store.list_settings(ctx.workspace_id)}
    return {
        "workspace": {k: ctx.workspace[k] for k in _PUBLIC_WORKSPACE},
        "role": ctx.role,
        "me": {
            "id": ctx.user.id,
            "display_name": ctx.user.display_name,
            "account_type": ctx.user.account_type,
            **_preferences(ctx.user),
        },
        "lists": present_lists(ctx),
        "settings_public": {"stage_probabilities": settings.get("stage_probabilities")},
        # FR-CRM-TODAY-02: Gmail sync is live here (switch on, credential
        # configured); until it is, the email sections say it is off.
        "email_sync_on": email_sync.live(settings),
        # 1b: the bell's count, and for an Admin the jobs that are late
        # (FR-CRM-NOTIF-01, FR-CRM-JOB-03): staleness is checked on read.
        "unread": notifications.unread_for(ctx.user.id),
        "stale_jobs": jobs.stale_for(ctx),
    }


@router.get("/me/preferences")
def get_preferences(user: AnyAccountDep) -> dict:
    """FR-CRM-ACC-15."""
    return _preferences(user)


@router.patch("/me/preferences")
def update_preferences(payload: PreferencesUpdate, user: AnyAccountDep) -> dict:
    """FR-CRM-ACC-15, FR-CRM-CHG-12 — CRM display and CRM digests only."""
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        return _preferences(user)
    return _preferences(CurrentUser(db.update_profile(user.id, changes)))


def _preferences(user: CurrentUser) -> dict:
    return {
        "timezone": user.profile.get("timezone") or "Asia/Kolkata",
        "number_format": user.profile.get("number_format") or "indian_short",
    }
