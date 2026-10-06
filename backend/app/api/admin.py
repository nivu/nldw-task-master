"""The admin panel — FR-ADMIN, FR-HOL, FR-AUTH-03/06.

Every route here is guarded by `AdminDep`, and every mutation writes to the
audit log (FR-ADMIN-06). Vinita is the only person who reaches this.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query

from app.api.deps import AdminDep, LeadDep
from app.api.errors import ProblemDetail
from app.domain import holidays as holiday_rules
from app.domain import timesheets as rules
from app.domain.audit import actor_name
from app.domain.calendar import period_of, today_in_company_tz
from app.schemas import (
    AllocationIn,
    AllocationUpdate,
    AllowanceIn,
    BackfillIn,
    CtcPeriodIn,
    HolidayBulkIn,
    HolidayIn,
    PhaseIn,
    ProjectIn,
    ProjectUpdate,
    SettingUpdate,
    UserCreate,
    UserUpdate,
)
from app.services import audit, balances, settings_store
from app.services import bookings as booking_service
from app.services import pnl as pnl_service
from app.services import supabase as db

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Users — FR-AUTH-03, FR-ADMIN-02/03, FR-AUTH-06
# ---------------------------------------------------------------------------


@router.get("/users")
def list_users(admin: AdminDep) -> list[dict]:
    today = today_in_company_tz()
    rates = pnl_service.rate_book(today, today)
    return [
        {
            "id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "role": row["role"],
            "lead_id": row["lead_id"],
            "is_active": row["is_active"],
            # Spec 002 FR-ANALYTICS-07 — false: left out of coverage and nudges.
            "logs_time": rules.logs_time(row),
            # Spec 002 FR-ANALYTICS-08 — no time is expected outside these.
            "joined_on": row.get("joined_on"),
            "left_on": row.get("left_on"),
            # Spec 005 — the CTC in force today, shown monthly. Set through
            # the CTC routes below; never called salary.
            "ctc_monthly_now": _m(rates.monthly_now(row["id"], today)),
            # Spec 003 FR-DASH — read-only here; only the owner changes access,
            # and the owner flag itself is never changed through the app.
            "dashboard_access": row.get("dashboard_access") is True,
            "is_owner": row.get("is_owner") is True,
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

    if problem := rules.check_employment_dates(payload.joined_on, payload.left_on):
        raise ProblemDetail(422, problem)

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
                "logs_time": payload.logs_time,
                "joined_on": _iso(payload.joined_on),
                "left_on": _iso(payload.left_on),
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
        after={
            "email": payload.email,
            "role": payload.role,
            "lead_id": payload.lead_id,
            "logs_time": payload.logs_time,
            "joined_on": _iso(payload.joined_on),
            "left_on": _iso(payload.left_on),
        },
    )
    return {k: profile.get(k) for k in _USER_FIELDS}


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

    # Spec 003 FR-DASH-03 — the owner alone grants and revokes dashboard
    # access. Being an admin is not enough; that is the point of the flag.
    if "dashboard_access" in changes:
        if not admin.is_owner:
            raise ProblemDetail(403, "Only the owner can grant dashboard access.")
        if changes["dashboard_access"] is None:
            raise ProblemDetail(422, "Dashboard access is either granted or not.")

    # FR-DASH-03 — the owner grants through this admin route, so another admin
    # demoting or deactivating the owner would leave every grant beyond recall.
    if existing.get("is_owner") is True and not admin.is_owner:
        if any(k in changes and changes[k] != existing.get(k) for k in ("role", "is_active")):
            raise ProblemDetail(403, "Only the owner can change the owner's role or status.")

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

    for key in ("joined_on", "left_on"):
        if key in changes:
            changes[key] = _iso(changes[key])
    # Checked against the stored date for whichever one is not changing.
    joined_on, left_on = rules.employment({**existing, **changes})
    if problem := rules.check_employment_dates(joined_on, left_on):
        raise ProblemDetail(422, problem)

    updated = db.update_profile(user_id, changes)
    # FR-DASH-03 — access changes get their own audit action, so who was let
    # in, by whom and when can be read without sifting every profile edit.
    access = {k: changes.pop(k) for k in ("dashboard_access",) if k in changes}
    if access:
        audit.record(
            action="user.dashboard_access",
            target_table="profiles",
            target_id=user_id,
            actor_id=admin.id,
            before={"dashboard_access": existing.get("dashboard_access") is True},
            after=access,
        )
    if changes:
        audit.record(
            action="user.updated",
            target_table="profiles",
            target_id=user_id,
            actor_id=admin.id,
            before={k: existing.get(k) for k in changes},
            after=changes,
        )
    return {k: updated.get(k) for k in _USER_FIELDS}


_USER_FIELDS = (
    "id",
    "email",
    "display_name",
    "role",
    "lead_id",
    "is_active",
    "logs_time",
    "joined_on",
    "left_on",
    "dashboard_access",
    "is_owner",
)


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


# ---------------------------------------------------------------------------
# CTC periods — spec 005 FR-CTC. Admin only: the most sensitive number here.
# ---------------------------------------------------------------------------


def _m(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.01")))


def _present_period(row: dict) -> dict:
    annual = Decimal(str(row["annual_ctc"]))
    # FR-CTC-06 — a row from before 017 has no hours: it was full time.
    hours = Decimal(str(row.get("hours_per_week", 40)))
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "annual_ctc": _m(annual),
        "monthly_ctc": _m(annual / Decimal("12")),
        "starts_on": row["starts_on"],
        "ends_on": row.get("ends_on"),
        "hours_per_week": str(hours.quantize(Decimal("0.1"))),
        "created_at": row.get("created_at"),
    }


@router.get("/users/{user_id}/ctc")
def list_ctc(user_id: str, admin: AdminDep) -> dict:
    """Every CTC period for one person, oldest first, and the one in force today."""
    if db.get_profile(user_id) is None:
        raise ProblemDetail(404, "No such user.")
    today = today_in_company_tz().isoformat()
    rows = db.list_cost_periods(user_id)
    current = next(
        (
            r
            for r in rows
            if r["starts_on"] <= today and (r.get("ends_on") is None or today <= r["ends_on"])
        ),
        None,
    )
    return {
        "periods": [_present_period(r) for r in rows],
        "current": _present_period(current) if current else None,
    }


@router.post("/users/{user_id}/ctc", status_code=201)
def add_ctc(user_id: str, payload: CtcPeriodIn, admin: AdminDep) -> dict:
    """Add a period — past, current or upcoming. FR-CTC-03: an open-ended
    period that started earlier is closed the day before this one starts,
    which is how "CTC changes next month" is entered. Any other overlap is
    refused by the database (FR-CTC-02)."""
    if db.get_profile(user_id) is None:
        raise ProblemDetail(404, "No such user.")
    if payload.ends_on is not None and payload.ends_on < payload.starts_on:
        raise ProblemDetail(422, "The end date is before the start date.")

    closed = None
    for row in db.list_cost_periods(user_id):
        if row.get("ends_on") is None and row["starts_on"] < payload.starts_on.isoformat():
            closed = row
            db.close_cost_period(row["id"], (payload.starts_on - timedelta(days=1)).isoformat())

    try:
        created = db.insert_cost_period(
            {
                "user_id": user_id,
                "annual_ctc": str(payload.annual_ctc),
                "starts_on": payload.starts_on.isoformat(),
                "ends_on": payload.ends_on.isoformat() if payload.ends_on else None,
                "hours_per_week": str(payload.hours_per_week),
                "created_by": admin.id,
            }
        )
    except Exception as exc:  # noqa: BLE001
        if closed is not None:
            db.close_cost_period(closed["id"], None)  # type: ignore[arg-type]
        if "cost_periods_no_overlap" in str(exc):
            raise ProblemDetail(
                422, "That overlaps an existing CTC period. Remove or shorten it first."
            ) from exc
        raise

    audit.record(
        action="ctc.added",
        target_table="cost_periods",
        target_id=created["id"],
        actor_id=admin.id,
        after={
            "user_id": user_id,
            "annual_ctc": str(payload.annual_ctc),
            "starts_on": payload.starts_on.isoformat(),
            "ends_on": payload.ends_on.isoformat() if payload.ends_on else None,
            "hours_per_week": str(payload.hours_per_week),
            "closed_previous": closed["id"] if closed else None,
        },
    )
    return _present_period(created)


@router.delete("/ctc/{period_id}")
def remove_ctc(period_id: str, admin: AdminDep) -> dict:
    """Remove a period. Cost for the days it covered becomes unknown again —
    the figures will say so — which is the honest outcome of deleting history."""
    row = db.get_cost_period(period_id)
    if row is None:
        raise ProblemDetail(404, "No such CTC period.")
    db.delete_cost_period(period_id)
    audit.record(
        action="ctc.removed",
        target_table="cost_periods",
        target_id=period_id,
        actor_id=admin.id,
        before={
            k: row.get(k)
            for k in ("user_id", "annual_ctc", "starts_on", "ends_on", "hours_per_week")
        },
    )
    return {"status": "removed"}


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
    locations = {loc["id"]: loc["name"] for loc in db.list_locations()}
    return [
        {
            "id": row["id"],
            "date": row["date"],
            "name": row["name"],
            # Spec 006 FR-LOC-02 — None applies everywhere.
            "location_id": row.get("location_id"),
            "location_name": locations.get(row.get("location_id") or "", None),
        }
        for row in db.list_holidays()
    ]


@router.post("/holidays", status_code=201)
def declare_holiday(payload: HolidayIn, admin: AdminDep) -> dict:
    """FR-HOL-01/02/05/06.

    Declaring a holiday over dates people have already booked releases those
    bookings and returns the days (FR-HOL-05), and tells the people affected
    (FR-HOL-06). Doing it silently would leave someone's casual leave charged
    for a day the whole company had off.
    """
    if holiday_rules.clash(
        db.list_holidays(payload.date, payload.date), payload.date, payload.location_id
    ):
        raise ProblemDetail(409, f"{payload.date.isoformat()} is already a holiday.")
    if payload.location_id and not any(
        loc["id"] == payload.location_id for loc in db.list_locations()
    ):
        raise ProblemDetail(422, "No such location.")
    return _declare(payload.date, payload.name, payload.location_id, admin.id)


@router.post("/holidays/bulk")
def declare_holidays(payload: HolidayBulkIn, admin: AdminDep) -> dict:
    """Spec 001 FR-HOL-08 — a list of holidays, each declared exactly as one.

    Every location is checked before anything is written, so a typo refuses
    the whole list rather than half of it. Dates already declared for that
    location are skipped and reported, never renamed. The rest go through
    `_declare`, so bookings on those days are released and people told just as
    for a single holiday (FR-HOL-05/06).
    """
    known = {loc["id"] for loc in db.list_locations()}
    for n, entry in enumerate(payload.holidays, start=1):
        if entry.location_id and entry.location_id not in known:
            raise ProblemDetail(422, f"Holiday {n} ({entry.name}): no such location.")

    days = [entry.date for entry in payload.holidays]
    declare, skipped = holiday_rules.plan_bulk(
        [entry.model_dump() for entry in payload.holidays],
        db.list_holidays(min(days), max(days)),
    )
    created = [
        _declare(entry["date"], entry["name"], entry["location_id"], admin.id) for entry in declare
    ]
    return {
        "created": created,
        "skipped": skipped,
        "released_bookings": sum(c["released_bookings"] for c in created),
    }


def _declare(day: date, name: str, location_id: str | None, actor_id: str) -> dict:
    """Write one holiday, release what it overlaps, audit it — FR-HOL-05/06."""
    row = db.insert_holiday(
        {
            "date": day.isoformat(),
            "name": name,
            "created_by": actor_id,
            "location_id": location_id,
        }
    )
    released = booking_service.release_for_holiday(
        day=day,
        holiday_name=name,
        actor_id=actor_id,
        location_id=location_id,
    )
    audit.record(
        action="holiday.declared",
        target_table="holidays",
        target_id=row["id"],
        actor_id=actor_id,
        after={
            "date": day.isoformat(),
            "name": name,
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

    value = payload.value
    if key == "portal_start_date":
        # `002` FR-ANALYTICS-07. Empty means no start date; stored as "" rather
        # than null because app_settings.value is NOT NULL.
        value = "" if value is None else str(value).strip()
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ProblemDetail(
                    422, "portal_start_date must be a date (YYYY-MM-DD), or empty for none."
                ) from exc
    if key == "invoice_payment_terms_days":
        # `006` FR-MILE-06. A whole number of days; the reader would otherwise
        # fall back to 30 in silence, and a negative one makes every invoice late.
        if isinstance(value, bool) or not str(value).strip().isdigit() or int(value) > 365:
            raise ProblemDetail(
                422, "invoice_payment_terms_days must be a whole number of days from 0 to 365."
            )
        value = int(value)

    row = db.update_setting(key, value, admin.id)
    settings_store.invalidate()
    audit.record(
        action="setting.updated",
        target_table="app_settings",
        target_id=key,
        actor_id=admin.id,
        after={"key": key, "value": value},
    )
    return {"key": row["key"], "value": row["value"]}


@router.get("/audit")
def read_audit(admin: AdminDep, limit: int = Query(default=200, le=1000)) -> list[dict]:
    """NFR-06 — readable, and by construction not writable."""
    names = {p["id"]: p["display_name"] for p in db.list_profiles()}
    return [
        {**row, "actor": actor_name(row.get("actor_id"), names)} for row in db.list_audit(limit)
    ]


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


# ---------------------------------------------------------------------------
# Projects, phases and allocations — spec 002 §5.1, §5.2; spec 003 FR-ROLE-02
#
# Lead, manager or admin (spec 003 lifted 002's FR-PROJ-05 admin-only rule;
# FR-ROLE-07/08 opened it to leads). A lead never sets or sees revenue and
# allocates only their own reports. Every mutation writes to the append-only
# audit log. Everything ABOVE this line in the file stays admin-only: people,
# allowances, holidays, settings, backfill (FR-ROLE-03). The split is a section boundary on purpose — a role swap that
# touched the whole file would hand managers the user directory.
# ---------------------------------------------------------------------------


@router.get("/projects")
def list_projects(user: LeadDep) -> list[dict]:
    projects = db.list_projects(include_archived=True)
    phases: dict[str, list[dict]] = {}
    for phase in db.list_phases():
        phases.setdefault(phase["project_id"], []).append(
            {
                "id": phase["id"],
                "phase": phase["phase"],
                "starts_on": phase["starts_on"],
                "ends_on": phase["ends_on"],
                "budget_hours": str(phase["budget_hours"])
                if phase["budget_hours"] is not None
                else None,
            }
        )
    names = {p["id"]: p["display_name"] for p in db.list_profiles()}
    return [_present_project(p, user, phases.get(p["id"], []), names) for p in projects]


def _present_project(
    row: dict,
    user,  # noqa: ANN001
    phases: list[dict] | None = None,
    names: dict[str, str] | None = None,
) -> dict:
    """Money — FR-FIN-02. Revenue goes to managers and admins only; a lead
    gets the project without the key (FR-ROLE-07).

    `names` maps profile id to display name for the lead (spec 002
    FR-PROJ-07); without it the one lead is looked up."""
    lead_id = row.get("lead_id")
    if lead_id and names is None:
        lead = db.get_profile(lead_id)
        names = {lead_id: lead["display_name"]} if lead else {}
    out = {
        "id": row["id"],
        "name": row["name"],
        "client": row.get("client"),
        "is_archived": row["is_archived"],
        "category": row.get("category", "client"),
        "lead_id": lead_id,
        "lead_name": (names or {}).get(lead_id) if lead_id else None,
        # Spec 002 FR-PROJ-08 — pipeline, and the chance it is won. Not money.
        "status": row.get("status", "confirmed"),
        "probability": row.get("probability"),
    }
    if user.is_manager:
        out["revenue"] = str(row["revenue"]) if row.get("revenue") is not None else None
    if phases is not None:
        out["phases"] = phases
    return out


@router.post("/projects", status_code=201)
def create_project(payload: ProjectIn, user: LeadDep) -> dict:
    """FR-PROJ-01, FR-ROLE-07."""
    if payload.revenue is not None and not user.is_manager:
        raise ProblemDetail(403, "Only a manager or admin can set revenue.")
    if any(
        p["name"].strip().lower() == payload.name.strip().lower()
        for p in db.list_projects(include_archived=True)
    ):
        raise ProblemDetail(409, f"A project called {payload.name!r} already exists.")
    if payload.lead_id is not None:
        refusal = rules.project_lead_refusal(db.get_profile(payload.lead_id))
        if refusal:
            raise ProblemDetail(422, refusal)

    row = db.insert_project(
        {
            "name": payload.name.strip(),
            "client": (payload.client or "").strip() or None,
            "revenue": str(payload.revenue) if payload.revenue is not None else None,
            "category": payload.category,
            "lead_id": payload.lead_id,
            "status": payload.status,
            "probability": payload.probability,
            "created_by": user.id,
        }
    )
    audit.record(
        action="project.created",
        target_table="projects",
        target_id=row["id"],
        actor_id=user.id,
        after={
            "name": row["name"],
            "client": row.get("client"),
            "revenue": str(payload.revenue) if payload.revenue is not None else None,
            "category": payload.category,
            "lead_id": payload.lead_id,
            "status": payload.status,
            "probability": payload.probability,
        },
    )
    return _present_project(row, user, [])


@router.patch("/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate, user: LeadDep) -> dict:
    """FR-PROJ-04 — archiving is the only removal.

    Effort logged against a finished project is exactly the history the
    analytics exist to report on, so there is no delete.
    """
    existing = db.get_project(project_id)
    if existing is None:
        raise ProblemDetail(404, "No such project.")

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("category", "") is None:
        del changes["category"]  # a category cannot be cleared, only changed
    if changes.get("status", "") is None:
        del changes["status"]  # nor a status; probability can be cleared
    if not changes:
        raise ProblemDetail(422, "Nothing to change.")
    if "revenue" in changes and not user.is_manager:
        raise ProblemDetail(403, "Only a manager or admin can set revenue.")
    if "revenue" in changes and changes["revenue"] is not None:
        changes["revenue"] = str(changes["revenue"])
    if changes.get("lead_id") is not None:  # null clears the lead (FR-PROJ-07)
        refusal = rules.project_lead_refusal(db.get_profile(changes["lead_id"]))
        if refusal:
            raise ProblemDetail(422, refusal)
    if changes.get("status") == "tentative" and not rules.is_tentative(existing):
        refusal = rules.make_tentative_refusal(
            project_name=existing["name"],
            has_time_entries=db.has_time_entries(project_id),
            has_invoices=any(m.get("invoiced_on") for m in db.list_milestones(project_id)),
        )
        if refusal:
            raise ProblemDetail(422, refusal)

    row = db.update_project(project_id, changes)
    audit.record(
        action="project.updated",
        target_table="projects",
        target_id=project_id,
        actor_id=user.id,
        before={k: existing.get(k) for k in changes},
        after=changes,
    )
    return _present_project(row, user)


@router.put("/projects/{project_id}/phases")
def set_phase(project_id: str, payload: PhaseIn, user: LeadDep) -> dict:
    """FR-PROJ-02/03.

    Moving a phase's dates does NOT re-file existing time entries. Each entry
    stores the phase it was logged against (migration 008), so history keeps
    saying what it always said — otherwise editing a date would silently
    reassign past effort between phases and change a budget conversation
    retroactively.
    """
    if db.get_project(project_id) is None:
        raise ProblemDetail(404, "No such project.")
    if payload.ends_on < payload.starts_on:
        raise ProblemDetail(422, "A phase cannot end before it starts.")

    row = db.upsert_phase(
        {
            "project_id": project_id,
            "phase": payload.phase,
            "starts_on": payload.starts_on.isoformat(),
            "ends_on": payload.ends_on.isoformat(),
            "budget_hours": str(payload.budget_hours) if payload.budget_hours is not None else None,
        }
    )
    audit.record(
        action="project.phase_set",
        target_table="project_phases",
        target_id=row["id"],
        actor_id=user.id,
        after={
            "project_id": project_id,
            "phase": payload.phase,
            "starts_on": payload.starts_on.isoformat(),
            "ends_on": payload.ends_on.isoformat(),
            "budget_hours": str(payload.budget_hours) if payload.budget_hours is not None else None,
        },
    )
    return {
        "id": row["id"],
        "phase": row["phase"],
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
        "budget_hours": str(row["budget_hours"]) if row["budget_hours"] is not None else None,
    }


@router.get("/allocations")
def list_allocations(user: LeadDep) -> list[dict]:
    """A lead sees only their reports' allocations — FR-ROLE-08."""
    people = {p["id"]: p["display_name"] for p in db.list_profiles()}
    user_ids = (
        None if user.is_manager else [p["id"] for p in db.list_reports(user.id, active_only=False)]
    )
    projects = {p["id"]: p["name"] for p in db.list_projects(include_archived=True)}
    return [
        {
            "id": a["id"],
            "project_id": a["project_id"],
            "project_name": projects.get(a["project_id"], "—"),
            "user_id": a["user_id"],
            "display_name": people.get(a["user_id"], "—"),
            "starts_on": a["starts_on"],
            "ends_on": a["ends_on"],
            "percent": str(a["percent"]),
        }
        for a in db.list_allocations(user_ids=user_ids)
    ]


@router.post("/allocations", status_code=201)
def create_allocation(payload: AllocationIn, user: LeadDep) -> dict:
    """FR-ALLOC-01/02/03.

    Concurrent allocations past 100% are permitted and reported, not refused
    (FR-ALLOC-04). Over-allocation is a real thing an admin does mid-crunch,
    and a product that cannot record it cannot warn about it either.
    """
    project = db.get_project(payload.project_id)
    if project is None:
        raise ProblemDetail(404, "No such project.")
    person = db.get_profile(payload.user_id)
    if person is None:
        raise ProblemDetail(404, "No such person.")
    if not rules.may_allocate(
        actor_id=user.id, actor_is_manager=user.is_manager, person_lead_id=person.get("lead_id")
    ):
        raise ProblemDetail(403, "A lead can only allocate their own reports.")
    if payload.ends_on < payload.starts_on:
        raise ProblemDetail(422, "An allocation cannot end before it starts.")
    refusal = rules.archived_allocation_refusal(
        project_name=project["name"],
        is_archived=project["is_archived"],
        new=(payload.starts_on, payload.ends_on),
    )
    if refusal:
        raise ProblemDetail(422, refusal)

    row = db.insert_allocation(
        {
            "project_id": payload.project_id,
            "user_id": payload.user_id,
            "starts_on": payload.starts_on.isoformat(),
            "ends_on": payload.ends_on.isoformat(),
            "percent": str(payload.percent),
            "created_by": user.id,
        }
    )
    audit.record(
        action="allocation.created",
        target_table="allocations",
        target_id=row["id"],
        actor_id=user.id,
        after={
            "project_id": payload.project_id,
            "user_id": payload.user_id,
            "percent": str(payload.percent),
            "starts_on": payload.starts_on.isoformat(),
            "ends_on": payload.ends_on.isoformat(),
        },
    )
    return {"id": row["id"], "percent": str(row["percent"])}


@router.delete("/allocations/{allocation_id}")
def remove_allocation(allocation_id: str, user: LeadDep) -> dict:
    """FR-ALLOC-05 — removing intent never removes recorded fact.

    Time already logged against the project stays. An allocation says what was
    planned; a time entry says what happened, and deleting the plan must not
    erase the history.
    """
    existing = db.get_allocation(allocation_id)
    if existing is None:
        raise ProblemDetail(404, "No such allocation.")
    if not user.is_manager:
        mine = [p["id"] for p in db.list_reports(user.id, active_only=False)]
        if allocation_id not in {a["id"] for a in db.list_allocations(user_ids=mine)}:
            raise ProblemDetail(403, "A lead can only remove their own reports' allocations.")
    db.delete_allocation(allocation_id)
    audit.record(
        action="allocation.deleted",
        target_table="allocations",
        target_id=allocation_id,
        actor_id=user.id,
        before=_allocation_audit(existing),
    )
    return {"status": "deleted"}


@router.patch("/allocations/{allocation_id}")
def update_allocation(allocation_id: str, payload: AllocationUpdate, user: LeadDep) -> dict:
    """FR-ALLOC-06 — change an allocation's dates or percent.

    Same rule as allocating: managers and admins any allocation, a lead only
    their own reports'. On an archived project an edit may shorten an
    allocation but not extend it (FR-PROJ-04).
    """
    existing = db.get_allocation(allocation_id)
    if existing is None:
        raise ProblemDetail(404, "No such allocation.")
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise ProblemDetail(422, "Nothing to change.")
    person = db.get_profile(existing["user_id"])
    if not rules.may_allocate(
        actor_id=user.id,
        actor_is_manager=user.is_manager,
        person_lead_id=person.get("lead_id") if person else None,
    ):
        raise ProblemDetail(403, "A lead can only change their own reports' allocations.")

    old = (date.fromisoformat(existing["starts_on"]), date.fromisoformat(existing["ends_on"]))
    starts_on = changes.get("starts_on", old[0])
    ends_on = changes.get("ends_on", old[1])
    if ends_on < starts_on:
        raise ProblemDetail(422, "An allocation cannot end before it starts.")
    project = db.get_project(existing["project_id"])
    if project is not None:
        refusal = rules.archived_allocation_refusal(
            project_name=project["name"],
            is_archived=project["is_archived"],
            new=(starts_on, ends_on),
            old=old,
        )
        if refusal:
            raise ProblemDetail(422, refusal)

    data = {k: v.isoformat() if isinstance(v, date) else str(v) for k, v in changes.items()}
    row = db.update_allocation(allocation_id, data)
    audit.record(
        action="allocation.updated",
        target_table="allocations",
        target_id=allocation_id,
        actor_id=user.id,
        before=_allocation_audit(existing),
        after=_allocation_audit(row),
    )
    return {
        "id": row["id"],
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
        "percent": str(row["percent"]),
    }


def _allocation_audit(row: dict) -> dict:
    return {
        "project_id": row["project_id"],
        "user_id": row["user_id"],
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
        "percent": str(row["percent"]),
    }
