"""The admin panel — FR-ADMIN, FR-HOL, FR-AUTH-03/06.

Every route here is guarded by `AdminDep`, and every mutation writes to the
audit log (FR-ADMIN-06). Vinita is the only person who reaches this.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import AdminDep
from app.api.errors import ProblemDetail
from app.domain.calendar import period_of, today_in_company_tz
from app.schemas import (
    AllowanceIn,
    BackfillIn,
    HolidayIn,
    SettingUpdate,
    UserCreate,
    UserUpdate,
)
from app.services import audit, balances, settings_store
from app.services import bookings as booking_service
from app.services import supabase as db

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Users — FR-AUTH-03, FR-ADMIN-02/03, FR-AUTH-06
# ---------------------------------------------------------------------------


@router.get("/users")
def list_users(admin: AdminDep) -> list[dict]:
    return [
        {
            "id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "role": row["role"],
            "lead_id": row["lead_id"],
            "is_active": row["is_active"],
        }
        for row in db.list_profiles()
    ]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, admin: AdminDep) -> dict:
    """FR-AUTH-03 — the only way an account comes into existence.

    Two rows must be created together: the Supabase auth user and the portal
    profile. If the profile insert fails, the auth user is deleted rather than
    left orphaned — an auth account with no profile can sign in and then be
    refused by `deps.current_user`, which looks exactly like a broken login and
    is miserable to diagnose.

    No password is set (FR-AUTH-08). The account exists but cannot be signed
    into until the person authenticates with Google, whose identity is then
    attached to this same auth user. Nothing is issued that could be shared.
    """
    from app.services.supabase import supabase

    if db.get_profile_by_email(payload.email):
        raise ProblemDetail(409, f"{payload.email} already has an account.")

    if payload.lead_id and db.get_profile(payload.lead_id) is None:
        raise ProblemDetail(422, "That lead does not exist.")

    try:
        created = supabase.auth.admin.create_user(
            {
                "email": payload.email,
                # Confirmed on creation: admin-created, so there is nobody to
                # send a confirmation to, and Google is the only way in anyway.
                "email_confirm": True,
                "user_metadata": {"display_name": payload.display_name},
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise ProblemDetail(422, "Supabase refused that account.") from exc

    auth_user_id = str(created.user.id)

    try:
        profile = db.insert_profile(
            {
                "id": auth_user_id,
                "email": payload.email,
                "display_name": payload.display_name,
                "role": payload.role,
                "lead_id": payload.lead_id,
            }
        )
    except Exception as exc:  # noqa: BLE001
        try:
            supabase.auth.admin.delete_user(auth_user_id)
        except Exception:  # noqa: BLE001 - best effort; the original error matters more
            pass
        raise ProblemDetail(422, "Could not create the portal profile.") from exc

    audit.record(
        action="user.created",
        target_table="profiles",
        target_id=auth_user_id,
        actor_id=admin.id,
        after={"email": payload.email, "role": payload.role, "lead_id": payload.lead_id},
    )
    return {k: profile[k] for k in ("id", "email", "display_name", "role", "lead_id", "is_active")}


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, admin: AdminDep) -> dict:
    """FR-ADMIN-02/03, FR-AUTH-06.

    Deactivating preserves history — no booking is deleted, and the person's
    consumption stays in the ledger. It does not cancel their future bookings
    (A-16): that is a separate decision nobody has made, and silently releasing
    someone's approved leave on the way out would be a surprising thing for a
    role change to do.
    """
    existing = db.get_profile(user_id)
    if existing is None:
        raise ProblemDetail(404, "No such user.")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ProblemDetail(422, "Nothing to change.")

    if changes.get("lead_id") == user_id:
        raise ProblemDetail(422, "Somebody cannot be their own lead.")

    if changes.get("lead_id") and db.get_profile(changes["lead_id"]) is None:
        raise ProblemDetail(422, "That lead does not exist.")

    if user_id == admin.id and changes.get("role") and changes["role"] != "admin":
        # Removing your own admin rights locks the panel for everyone if you
        # are the only admin, and there is no self-service way back in.
        raise ProblemDetail(422, "You cannot remove your own admin role.")

    if user_id == admin.id and changes.get("is_active") is False:
        raise ProblemDetail(422, "You cannot deactivate your own account.")

    updated = db.update_profile(user_id, changes)
    audit.record(
        action="user.updated",
        target_table="profiles",
        target_id=user_id,
        actor_id=admin.id,
        before={k: existing.get(k) for k in changes},
        after=changes,
    )
    return {k: updated[k] for k in ("id", "email", "display_name", "role", "lead_id", "is_active")}


# ---------------------------------------------------------------------------
# Allowances — FR-ADMIN-01, FR-BAL-02
# ---------------------------------------------------------------------------


@router.get("/allowances")
def list_allowances(admin: AdminDep) -> list[dict]:
    return [
        {
            "id": row["id"],
            "period": row["period"],
            "category": row["category"],
            "days": str(row["days"]),
            "user_id": row["user_id"],
        }
        for row in db.list_all_allowances()
    ]


@router.put("/allowances")
def set_allowance(payload: AllowanceIn, admin: AdminDep) -> dict:
    """FR-ADMIN-01, FR-BAL-07.

    Writes the grant for the period it is set for and touches no other, so a
    change cannot retroactively invalidate bookings already approved in a
    closed period. `user_id` omitted sets the organisation default.
    """
    if payload.user_id and db.get_profile(payload.user_id) is None:
        raise ProblemDetail(422, "No such user.")

    row = db.upsert_allowance(
        {
            "period": payload.period,
            "category": payload.category,
            "days": str(payload.days),
            "user_id": payload.user_id,
            "created_by": admin.id,
        }
    )
    audit.record(
        action="allowance.set",
        target_table="allowances",
        target_id=row["id"],
        actor_id=admin.id,
        after={
            "period": payload.period,
            "category": payload.category,
            "days": str(payload.days),
            "user_id": payload.user_id,
        },
    )
    return {
        "id": row["id"],
        "period": row["period"],
        "category": row["category"],
        "days": str(row["days"]),
        "user_id": row["user_id"],
    }


# ---------------------------------------------------------------------------
# Holidays — FR-HOL
# ---------------------------------------------------------------------------


@router.get("/holidays")
def list_holidays(admin: AdminDep) -> list[dict]:
    return [
        {"id": row["id"], "date": row["date"], "name": row["name"]} for row in db.list_holidays()
    ]


@router.post("/holidays", status_code=201)
def declare_holiday(payload: HolidayIn, admin: AdminDep) -> dict:
    """FR-HOL-01/02/05/06.

    Declaring a holiday over dates people have already booked releases those
    bookings and returns the days (FR-HOL-05), and tells the people affected
    (FR-HOL-06). Doing it silently would leave someone's casual leave charged
    for a day the whole company had off.
    """
    if db.get_holiday_on(payload.date):
        raise ProblemDetail(409, f"{payload.date.isoformat()} is already a holiday.")

    row = db.insert_holiday(
        {"date": payload.date.isoformat(), "name": payload.name, "created_by": admin.id}
    )
    released = booking_service.release_for_holiday(
        day=payload.date, holiday_name=payload.name, actor_id=admin.id
    )
    audit.record(
        action="holiday.declared",
        target_table="holidays",
        target_id=row["id"],
        actor_id=admin.id,
        after={
            "date": payload.date.isoformat(),
            "name": payload.name,
            "released_bookings": len(released),
        },
    )
    return {
        "id": row["id"],
        "date": row["date"],
        "name": row["name"],
        "released_bookings": len(released),
    }


@router.patch("/holidays/{holiday_id}")
def edit_holiday(holiday_id: str, payload: HolidayIn, admin: AdminDep) -> dict:
    """FR-HOL-04.

    Moving a holiday to a new date releases bookings on the new date, but does
    NOT restore bookings released from the old one. Un-releasing would have to
    guess whether the person still wants leave they were told was cancelled,
    and guessing wrong silently spends their allowance.
    """
    existing = next((h for h in db.list_holidays() if h["id"] == holiday_id), None)
    if existing is None:
        raise ProblemDetail(404, "No such holiday.")

    clash = db.get_holiday_on(payload.date)
    if clash and clash["id"] != holiday_id:
        raise ProblemDetail(409, f"{payload.date.isoformat()} is already a holiday.")

    row = db.update_holiday(holiday_id, {"date": payload.date.isoformat(), "name": payload.name})
    released = []
    if existing["date"] != payload.date.isoformat():
        released = booking_service.release_for_holiday(
            day=payload.date, holiday_name=payload.name, actor_id=admin.id
        )

    audit.record(
        action="holiday.updated",
        target_table="holidays",
        target_id=holiday_id,
        actor_id=admin.id,
        before={"date": existing["date"], "name": existing["name"]},
        after={"date": payload.date.isoformat(), "name": payload.name},
    )
    return {
        "id": row["id"],
        "date": row["date"],
        "name": row["name"],
        "released_bookings": len(released),
    }


@router.delete("/holidays/{holiday_id}")
def remove_holiday(holiday_id: str, admin: AdminDep) -> dict:
    """FR-HOL-04. Bookings released by this holiday stay released — see above."""
    existing = next((h for h in db.list_holidays() if h["id"] == holiday_id), None)
    if existing is None:
        raise ProblemDetail(404, "No such holiday.")

    db.delete_holiday(holiday_id)
    audit.record(
        action="holiday.deleted",
        target_table="holidays",
        target_id=holiday_id,
        actor_id=admin.id,
        before={"date": existing["date"], "name": existing["name"]},
    )
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Backfill — spec A-21
#
# The one sanctioned way past the lock in §6.3, for recording leave already
# taken when the portal goes live partway through a month. Every route here is
# admin-only and writes to the append-only audit log.
# ---------------------------------------------------------------------------


@router.post("/backfill", status_code=201)
def backfill_booking(payload: BackfillIn, admin: AdminDep) -> dict:
    """Record leave somebody already took, on a date that is already locked.

    Enters as `approved` and is permanently marked as backfilled. Deliberately
    NOT checked against the remaining allowance: this records what happened,
    and a balance that goes negative because more was taken than granted is a
    true statement about the month, which the ledger reports rather than hides.
    """
    try:
        created = booking_service.backfill(
            user_id=payload.user_id,
            day=payload.date,
            category=payload.category,
            duration=payload.duration,
            reason=payload.reason,
            note=payload.note,
            actor=admin,
        )
    except booking_service.BookingRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc

    return {
        "id": created["id"],
        "user_id": created["user_id"],
        "date": created["date"],
        "category": created["category"],
        "duration": str(created["duration"]),
        "status": created["status"],
        "backfilled_by": created["backfilled_by"],
    }


@router.delete("/backfill/{booking_id}")
def undo_backfill(booking_id: str, admin: AdminDep) -> dict:
    """Reverse a backfill. Refuses anything that was not itself a backfill."""
    try:
        updated = booking_service.undo_backfill(booking_id=booking_id, actor=admin)
    except booking_service.BookingRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc

    return {"id": updated["id"], "status": updated["status"]}


@router.get("/backfill")
def list_backfills(admin: AdminDep) -> list[dict]:
    """Everything entered by hand, so it can be reviewed as a set.

    A go-live produces a burst of these. Being able to look at them together —
    rather than hunting them one date at a time on individual calendars — is
    what makes a mistyped entry findable.
    """
    people = {row["id"]: row["display_name"] for row in db.list_profiles()}
    rows = [row for row in db.list_bookings() if row.get("backfilled_by")]
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "display_name": people.get(row["user_id"], "—"),
            "date": row["date"],
            "category": row["category"],
            "duration": str(row["duration"]),
            "status": row["status"],
            "note": row.get("backfill_note"),
            "entered_by": people.get(row["backfilled_by"], "—"),
        }
        for row in sorted(rows, key=lambda r: r["date"], reverse=True)
    ]


# ---------------------------------------------------------------------------
# Organisation view and settings — FR-ADMIN-05
# ---------------------------------------------------------------------------


@router.get("/consumption")
def org_consumption(admin: AdminDep, period: str = Query(default="")) -> dict:
    """FR-ADMIN-05 — the organisation-wide equivalent of the lead view."""
    resolved = period or period_of(today_in_company_tz())
    return {
        "period": resolved,
        "people": [
            {
                "user_id": person["id"],
                "display_name": person["display_name"],
                "role": person["role"],
                "balances": balances.summary_for(person["id"], period=resolved),
            }
            for person in db.list_profiles(active_only=True)
        ],
    }


@router.get("/settings")
def list_settings(admin: AdminDep) -> list[dict]:
    """The policy switches for the spec's open questions (§11)."""
    return [
        {"key": row["key"], "value": row["value"], "description": row["description"]}
        for row in sorted(db.list_settings(), key=lambda r: r["key"])
    ]


@router.put("/settings/{key}")
def update_setting(key: str, payload: SettingUpdate, admin: AdminDep) -> dict:
    known = {row["key"] for row in db.list_settings()}
    if key not in known:
        raise ProblemDetail(404, f"No such setting {key!r}.")

    if key == "sandwich_rule" and payload.value is True:
        # Q-09's `true` branch is deliberately unimplemented (YAGNI). Refusing
        # here means an admin is told, rather than believing weekends are being
        # counted while the ledger quietly ignores them.
        raise ProblemDetail(
            422,
            "The sandwich rule has no implementation yet (spec Q-09 is unanswered). "
            "Answer Q-09 and implement app.domain.cost.bridging_days first.",
        )

    row = db.update_setting(key, payload.value, admin.id)
    settings_store.invalidate()
    audit.record(
        action="setting.updated",
        target_table="app_settings",
        target_id=key,
        actor_id=admin.id,
        after={"key": key, "value": payload.value},
    )
    return {"key": row["key"], "value": row["value"]}


@router.get("/audit")
def read_audit(admin: AdminDep, limit: int = Query(default=200, le=1000)) -> list[dict]:
    """NFR-06 — readable, and by construction not writable."""
    return db.list_audit(limit)


@router.post("/lock-sweep")
def run_lock_sweep(admin: AdminDep) -> dict:
    """Run the Q-04 auto-approval sweep now, rather than waiting for 00:05 IST.

    Exists because a scheduler that has never been observed working is a
    scheduler nobody trusts. Idempotent, so pressing it twice is harmless.
    """
    from app.tasks.lock_sweep import sweep

    result = sweep()
    audit.record(
        action="lock_sweep.manual",
        target_table="bookings",
        target_id=None,
        actor_id=admin.id,
        after=result,
    )
    return result


@router.get("/holidays/upcoming")
def upcoming_holidays(admin: AdminDep) -> list[dict]:
    today = today_in_company_tz()
    return [
        {"id": row["id"], "date": row["date"], "name": row["name"]}
        for row in db.list_holidays(start=today, end=date(today.year + 1, 12, 31))
    ]
