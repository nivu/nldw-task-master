"""Gmail sync — spec 007 contracts §2.10; FR-CRM-SYNC-01, -04, -10,
FR-CRM-REC-03, FR-CRM-LIST-06; Q-62; plan §5.13.

HTTP only. Mailboxes are Admin settings, and so is running the sync now,
because a run reads every listed mailbox (Q-62). Each mailbox change goes
through `crm_apply`, so it is audited (`mailbox.*`). A record's emails are
read by any member of its workspace, and by nobody else (FR-CRM-SYNC-10).

The API never holds the Gmail key: it reads `CRM_GMAIL_CONFIGURED`, the
non-secret flag that says the key exists on `worker-crm-sync` (plan §2.2).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmAdminDep, CrmContext, CrmReadDep
from app.api.crm.members import is_tva_admin
from app.api.errors import ProblemDetail
from app.config import settings
from app.domain.crm import email_rules as rules
from app.domain.crm import today as today_rules
from app.schemas.crm import MailboxIn, MailboxUpdate
from app.services import supabase as db
from app.services.crm import email_sync, store
from app.services.crm.context import actor

router = APIRouter(prefix="/crm", tags=["crm"])

NO_SUCH_MAILBOX = "No such mailbox."
#: Contracts §2.10 — the objects with an Emails tab.
EMAIL_OBJECTS = {"companies": "company", "people": "person", "opportunities": "opportunity"}


def _present(row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "owner_id": row["owner_id"],
        "owner_name": names.get(row["owner_id"]),
        "labels": row["labels"],
        "is_active": row["is_active"],
        "backfilled_at": row.get("backfilled_at"),
        "last_synced_at": row.get("last_synced_at"),
        "updated_at": row.get("updated_at"),
    }


def _names() -> dict[str, str]:
    return {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}


@router.get("/mailboxes")
def list_mailboxes(ctx: CrmAdminDep) -> dict:
    """The workspace's mailboxes, and whether sync can run: the switch, and
    the credential flag (never the key)."""
    names = _names()
    settings_by_key = {r["key"]: r["value"] for r in store.list_settings(ctx.workspace_id)}
    return {
        "mailboxes": [_present(m, names) for m in store.list_mailboxes(ctx.workspace_id)],
        "sync": {
            "enabled": today_rules.email_sync_on(settings_by_key),
            "configured": settings.CRM_GMAIL_CONFIGURED,
            "live": email_sync.live(settings_by_key),
        },
        "gmail_domains": list(rules.GMAIL_DOMAINS),
    }


def _check(
    ctx: CrmContext,
    *,
    email: str,
    owner_id: str,
    labels: list[str],
    mailbox_id: str | None = None,
) -> None:
    """FR-CRM-SYNC-01, amended — each rule a 422, then the address once (409)."""
    members = {m["user_id"] for m in store.list_members(ctx.workspace_id)}
    owner = db.get_profile(owner_id) if owner_id in members else None
    why = rules.mailbox_refusal(
        email=email,
        owner_email=(owner or {}).get("email"),
        owner_is_member=owner is not None,
        by_tva_admin=is_tva_admin(ctx.user),
        labels=labels,
    )
    if why:
        raise ProblemDetail(422, why)
    taken = store.find_mailbox_by_email(email)
    if taken and taken["id"] != mailbox_id:
        raise ProblemDetail(409, rules.TAKEN)


@router.post("/mailboxes", status_code=201)
def add_mailbox(payload: MailboxIn, ctx: CrmAdminDep) -> dict:
    email = str(payload.email).lower()
    owner_id = str(payload.owner_id)
    labels = rules.clean_labels(payload.labels)
    _check(ctx, email=email, owner_id=owner_id, labels=labels)
    done = store.apply(
        actor(ctx),
        [
            {
                "op": "insert",
                "table": "crm_mailboxes",
                "values": {
                    "email": email,
                    "owner_id": owner_id,
                    "labels": labels,
                    "is_active": payload.is_active,
                },
            }
        ],
    )
    return _present(done[0]["row"], _names())


def _mailbox(ctx: CrmContext, mailbox_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(mailbox_id)
    except ValueError:
        raise ProblemDetail(404, NO_SUCH_MAILBOX) from None
    found = store.get_mailbox(ctx.workspace_id, mailbox_id)
    if found is None:
        raise ProblemDetail(404, NO_SUCH_MAILBOX)
    return found


@router.patch("/mailboxes/{mailbox_id}")
def update_mailbox(mailbox_id: str, payload: MailboxUpdate, ctx: CrmAdminDep) -> dict:
    """A changed address or label set reads the labels afresh: the cursor is
    reset, and messages stored already are not stored twice."""
    row = _mailbox(ctx, mailbox_id)
    sent = payload.model_dump(exclude_unset=True)
    for key in ("email", "owner_id", "labels", "is_active"):
        if key in sent and sent[key] is None:
            raise ProblemDetail(422, f"{key.replace('_', ' ').capitalize()} cannot be empty.")
    email = str(sent.get("email", row["email"])).lower()
    owner_id = str(sent.get("owner_id", row["owner_id"]))
    labels = rules.clean_labels(sent["labels"]) if "labels" in sent else row["labels"]
    _check(ctx, email=email, owner_id=owner_id, labels=labels, mailbox_id=row["id"])
    values: dict[str, Any] = {}
    if email != row["email"]:
        values["email"] = email
    if owner_id != row["owner_id"]:
        values["owner_id"] = owner_id
    if labels != row["labels"]:
        values["labels"] = labels
    if "is_active" in sent and sent["is_active"] != row["is_active"]:
        values["is_active"] = sent["is_active"]
    if not values:
        return _present(row, _names())
    if "email" in values or "labels" in values:
        values.update(history_id=None, page_token=None, backfilled_at=None)
    done = store.apply(
        actor(ctx), [{"op": "update", "table": "crm_mailboxes", "id": row["id"], "values": values}]
    )
    return _present(done[0]["row"], _names())


@router.delete("/mailboxes/{mailbox_id}")
def delete_mailbox(mailbox_id: str, ctx: CrmAdminDep) -> dict:
    """Removes the mailbox and the messages stored from it (039 cascades);
    the next flag run clears what they raised."""
    row = _mailbox(ctx, mailbox_id)
    store.apply(actor(ctx), [{"op": "delete", "table": "crm_mailboxes", "id": row["id"]}])
    return {"removed": True}


@router.post("/email-sync/run")
def run_email_sync(ctx: CrmAdminDep) -> dict:
    """Q-62 — Admins only; `crm_run_email_sync` calls this."""
    return email_sync.run_now(ctx)


def _register(plural: str, kind: str) -> None:
    def emails(
        record_id: str,
        ctx: CrmReadDep,
        before: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict:
        """FR-CRM-REC-03 — the record's threads; no bodies (Q-29)."""
        return email_sync.threads(ctx, kind, record_id, before=before, limit=limit)

    router.add_api_route(
        f"/{plural}/{{record_id}}/emails", emails, methods=["GET"], name=f"{plural}_emails"
    )


for _plural, _kind in EMAIL_OBJECTS.items():
    _register(_plural, _kind)
