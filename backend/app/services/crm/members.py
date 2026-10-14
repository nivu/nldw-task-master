"""Invitations and membership removal — spec 007 FR-CRM-ACC-04 to -09, -12;
plan §4 CT-5, §5.13.

Shared by the workspace admin's routes (`api/crm/members.py`) and the TVA
admin panel (`api/crm/tva_admin.py`), so both are held to the same rules
(D11). The rules are `domain/crm/invites.py`; the writes are `crm_invite` and
`crm_remove_membership`.

An invitation is two steps, because GoTrue's admin API is outside any
database transaction (CT-5): the sign-in identity is pre-created first, as
`admin.create_user` does, then one `crm_invite` call writes the profile, the
membership and their audit rows. If that call fails, the auth user is deleted
— but only when this request created it. An existing GoTrue user is never
deleted.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.domain.crm import invites as rules
from app.services import notify
from app.services import supabase as db
from app.services.crm import CrmRefused, store

logger = logging.getLogger("nldw-task-master")


def invite(
    actor: dict[str, Any],
    workspace: dict[str, Any],
    *,
    email: str,
    display_name: str,
    role: str,
    timezone: str | None,
    by_tva_admin: bool,
    bypass_allow_list: bool = False,
) -> dict[str, Any]:
    """Contracts §2.2 `POST /crm/invitations`. `actor` carries the workspace."""
    email = email.strip().lower()
    person = db.get_profile_by_email_ignoring_case(email)
    already = person is not None and any(
        m["user_id"] == person["id"] for m in store.list_members(workspace["id"])
    )
    why = rules.invite_refusal(
        email=email,
        workspace=workspace,
        person=person,
        by_tva_admin=by_tva_admin,
        bypass_allow_list=bypass_allow_list,
        already_member=already,
    )
    if why:
        raise CrmRefused(why.status, why.detail)

    created_account = False
    if person is None:
        user_id = _create_sign_in(email, display_name)
        created_account = True
    else:
        user_id = person["id"]

    plan = rules.invite_plan(
        user_id=user_id,
        email=email,
        display_name=display_name,
        role=role,
        person=person,
        timezone=timezone,
    )
    try:
        done = store.invite(actor, plan)
    except Exception:
        if created_account:
            _delete_sign_in(user_id)
        raise

    sign_in_url = f"{settings.FRONTEND_URL.rstrip('/')}/auth/login?next=/crm/{workspace['key']}"
    name = (person or {}).get("display_name") or display_name.strip()
    delivered = notify.deliver(
        notify.Message(
            recipient_email=email,
            recipient_name=name,
            subject=f"You have been added to {workspace['name']} in the Nunnari CRM",
            body=(
                f"Sign in with Google as {email} to open it: {sign_in_url}\n"
                "If that address is not a Google account, ask whoever invited you."
            ),
        )
    )
    return {
        "membership_id": done["membership_id"],
        "user_id": done["user_id"],
        "created_account": bool(done.get("created_profile")),
        "reactivated": bool(done.get("reactivated")),
        "sign_in_url": sign_in_url,
        "delivered_via": delivered,
        "note": rules.GOOGLE_NOTE.format(email=email),
    }


def remove(actor: dict[str, Any], membership_id: str) -> dict[str, Any]:
    """Contracts §2.2 `DELETE /crm/members/{id}` (FR-CRM-ACC-12). The last
    admin and the protected memberships are refused by the function, under
    its locks; a deactivation drops the personal-token cache at once, so a
    revoked token stops on the next request (FR-TOK-03)."""
    from app.api.deps import forget_personal_tokens

    done = store.remove_membership(actor, membership_id) or {}
    deactivated = bool(done.get("deactivated"))
    if deactivated:
        forget_personal_tokens()
    return {"removed": True, "account_deactivated": deactivated}


def present_members(workspace_id: str, *, with_email: bool) -> list[dict[str, Any]]:
    """Contracts §2.2 `GET /crm/members` and §2.4 — by name. Email is shown to
    admins only."""
    people = {p["id"]: p for p in db.list_profiles(include_crm_only=True)}
    rows = []
    for m in store.list_members(workspace_id):
        person = people.get(m["user_id"], {})
        row = {
            "membership_id": m["id"],
            "user_id": m["user_id"],
            "display_name": person.get("display_name") or "Former member",
            "role": m["role"],
            "account_type": person.get("account_type", "staff"),
        }
        if with_email:
            row["email"] = person.get("email")
        rows.append(row)
    return sorted(rows, key=lambda r: r["display_name"].lower())


def _create_sign_in(email: str, display_name: str) -> str:
    """FR-CRM-ACC-04 — confirmed, no password: Google attaches to it at the
    person's first sign-in (D12)."""
    try:
        created = db.supabase.auth.admin.create_user(
            {
                "email": email,
                "email_confirm": True,
                "user_metadata": {"display_name": display_name.strip()},
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise CrmRefused(422, "Supabase refused that account.") from exc
    return str(created.user.id)


def _delete_sign_in(user_id: str) -> None:
    try:
        db.supabase.auth.admin.delete_user(user_id)
    except Exception:  # noqa: BLE001 - best effort; the original error matters more
        logger.error('{"event": "crm_invite_cleanup_failed", "user_id": "%s"}', user_id)
