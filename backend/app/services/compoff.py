"""Comp-off — spec 006 FR-COMP.

Claim → lead decides → credit valid for `compoff_valid_days` → consumed by a
`compoff` booking → returned if that booking is withdrawn, rejected or
released → lapses nightly once expired.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.domain import compoff as rules
from app.domain.approval import Person, can_decide
from app.domain.calendar import is_weekend, today_in_company_tz
from app.services import audit, settings_store
from app.services import supabase as db


class CompoffRefused(Exception):
    def __init__(self, detail: str, status: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _now() -> str:
    return datetime.now(UTC).isoformat()


def valid_days() -> int:
    return int(settings_store.get("compoff_valid_days", 90))


def _holiday_name_for(user_id: str, day: date) -> str | None:
    from app.services.holidays import holiday_on_for_user

    holiday = holiday_on_for_user(user_id, day)
    return holiday["name"] if holiday else None


def claim(*, user_id: str, worked_on: date, days: Decimal, note: str | None) -> dict[str, Any]:
    today = today_in_company_tz()
    refusal = rules.check_claim(
        worked_on,
        today=today,
        is_weekend=is_weekend(worked_on),
        holiday_name=_holiday_name_for(user_id, worked_on),
        days=days,
    )
    if refusal:
        raise CompoffRefused(refusal)
    try:
        created = db.insert_compoff_credit(
            {
                "user_id": user_id,
                "worked_on": worked_on.isoformat(),
                "days": str(days),
                "note": note,
            }
        )
    except Exception as exc:  # noqa: BLE001
        if "compoff_one_claim_per_day" in str(exc):
            raise CompoffRefused(
                f"You already have a comp-off claim for {worked_on.isoformat()}."
            ) from exc
        raise
    audit.record(
        action="compoff.claimed",
        target_table="compoff_credits",
        target_id=created["id"],
        actor_id=user_id,
        after={"worked_on": worked_on.isoformat(), "days": str(days)},
    )
    return created


def decide(*, credit_id: str, actor: Person, approve: bool, note: str | None) -> dict[str, Any]:
    credit = db.get_compoff_credit(credit_id)
    if credit is None:
        raise CompoffRefused("No such comp-off claim.", status=404)
    if credit["status"] != "pending":
        raise CompoffRefused("That claim has already been decided.", status=409)
    subject = db.get_profile(credit["user_id"])
    if subject is None or not can_decide(
        actor, Person(**{k: subject[k] for k in ("id", "role", "lead_id", "is_active")})
    ):
        raise CompoffRefused(
            "Only this person's lead or an admin can decide a comp-off claim.", status=403
        )
    if not approve and not (note or "").strip():
        raise CompoffRefused("Say why when rejecting a claim; the person will read it.")

    today = today_in_company_tz()
    changes: dict[str, Any] = {
        "status": "approved" if approve else "rejected",
        "decided_by": actor.id,
        "decided_at": _now(),
        "decision_note": (note or "").strip() or None,
    }
    if approve:
        changes["expires_on"] = rules.expiry(today, valid_days()).isoformat()
    updated = db.update_compoff_credit(credit_id, changes)
    audit.record(
        action="compoff.approved" if approve else "compoff.rejected",
        target_table="compoff_credits",
        target_id=credit_id,
        actor_id=actor.id,
        before={"status": "pending"},
        after={k: changes[k] for k in ("status", "expires_on") if k in changes},
    )
    return updated


def available(user_id: str, on: date | None = None) -> Decimal:
    on = on or today_in_company_tz()
    credits = db.list_compoff_credits(user_ids=[user_id], statuses=["approved"])
    return sum((Decimal(str(c["days"])) for c in credits if rules.usable(c, on)), Decimal("0"))


def consume(*, user_id: str, days: Decimal, booking_id: str, on: date) -> None:
    """Mark the oldest usable credits as used by a booking (FR-COMP-03)."""
    credits = db.list_compoff_credits(user_ids=[user_id], statuses=["approved"])
    chosen = rules.pick_credits(credits, days, on)
    if chosen is None:
        raise CompoffRefused("Not enough comp-off available for that booking.")
    for credit in chosen:
        db.update_compoff_credit(credit["id"], {"status": "used", "booking_id": booking_id})


def release_for_booking(booking_id: str) -> int:
    """A withdrawn, rejected or released comp-off booking hands its credits back."""
    used = [
        c for c in db.list_compoff_credits(statuses=["used"]) if c.get("booking_id") == booking_id
    ]
    for credit in used:
        db.update_compoff_credit(credit["id"], {"status": "approved", "booking_id": None})
    return len(used)


def lapse_expired(today: date | None = None) -> int:
    today = today or today_in_company_tz()
    lapsed = 0
    for credit in db.list_compoff_credits(statuses=["approved"]):
        if credit.get("expires_on") and date.fromisoformat(credit["expires_on"]) < today:
            db.update_compoff_credit(credit["id"], {"status": "lapsed"})
            audit.record(
                action="compoff.lapsed",
                target_table="compoff_credits",
                target_id=credit["id"],
                actor_id=None,
                before={"status": "approved"},
                after={"status": "lapsed", "expires_on": credit["expires_on"]},
            )
            lapsed += 1
    return lapsed


def present(credit: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": credit["id"],
        "user_id": credit["user_id"],
        "display_name": names.get(credit["user_id"], "—"),
        "worked_on": credit["worked_on"],
        "days": str(credit["days"]),
        "note": credit.get("note"),
        "status": credit["status"],
        "expires_on": credit.get("expires_on"),
        "decision_note": credit.get("decision_note"),
        "decided_at": credit.get("decided_at"),
    }
