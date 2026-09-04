"""The signed-in person's own view — profile, calendar, balances, password.

Route handlers are declared `def`, not `async def`, throughout this package.
The Supabase client is synchronous, so an `async def` handler calling it would
block the event loop for every other request; a plain `def` is run by Starlette
in a worker thread instead.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep
from app.api.errors import ProblemDetail
from app.domain.calendar import (
    is_weekend,
    month_matrix,
    period_bounds,
    period_of,
    today_in_company_tz,
)
from app.domain.rules import OCCUPYING_STATES, is_locked
from app.schemas import PasswordChange
from app.services import balances
from app.services import supabase as db

router = APIRouter(prefix="/me", tags=["me"])

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.get("")
def whoami(user: CurrentUserDep) -> dict:
    """Who am I and what may I see?

    The frontend uses `capabilities` to decide which navigation to render. It
    is a convenience, never a control: every guarded route re-checks the role
    server-side, because a client that lies about its capabilities must gain
    nothing by it.
    """
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "lead_id": user.lead_id,
        "capabilities": {
            "team_view": user.is_lead,
            "admin_panel": user.is_admin,
        },
    }


@router.get("/calendar")
def my_calendar(
    user: CurrentUserDep,
    period: str = Query(default="", pattern=f"({PERIOD_PATTERN})|^$"),
) -> dict:
    """One month of this person's calendar — FR-CAL-01 through FR-CAL-08.

    Returns everything a day cell needs to render itself, already decided
    server-side: whether it is a weekend, whether it is a holiday and which
    one, whether it is locked, and what is booked. The browser makes no
    judgements about dates — NFR-04 puts the lock decision on the server, and
    the simplest way to keep it there is to never send the client anything it
    could use to guess differently.
    """
    today = today_in_company_tz()
    resolved = period or period_of(today)
    first, last = period_bounds(resolved)

    holidays = {row["date"]: row["name"] for row in db.list_holidays(first, last)}
    bookings = {
        row["date"]: row
        for row in db.list_bookings(
            user_ids=[user.id],
            start=first,
            end=last,
            statuses=sorted(OCCUPYING_STATES),
        )
    }

    weeks = []
    for week in month_matrix(resolved):
        cells = []
        for day in week:
            cells.append(None if day is None else _day_cell(day, today, holidays, bookings))
        weeks.append(cells)

    return {
        "period": resolved,
        "today": today.isoformat(),
        "weeks": weeks,
        "balances": balances.summary_for(user.id, period=resolved),
    }


def _day_cell(day: date, today: date, holidays: dict, bookings: dict) -> dict:
    key = day.isoformat()
    holiday = holidays.get(key)
    booking = bookings.get(key)
    weekend = is_weekend(day)
    locked = is_locked(day, today)

    return {
        "date": key,
        "is_today": day == today,
        "is_weekend": weekend,
        "holiday": holiday,
        "locked": locked,
        # FR-CAL-05 and FR-CAL-08 rolled into one flag the UI can act on
        # without re-deriving any of the reasoning behind it.
        "bookable": not (weekend or holiday or locked),
        "booking": None
        if booking is None
        else {
            "id": booking["id"],
            "category": booking["category"],
            "duration": str(booking["duration"]),
            "status": booking["status"],
            "reason": booking.get("reason"),
            # FR-BOOK-07: editable only while the day is unlocked. An
            # unrecognised flag is a lead's record and is never self-editable.
            "can_edit": not locked and booking["status"] != "unrecognised",
        },
    }


@router.get("/balances")
def my_balances(
    user: CurrentUserDep,
    period: str = Query(default="", pattern=f"({PERIOD_PATTERN})|^$"),
) -> list[dict]:
    """FR-BAL-06, and the number FR-BOOK-12 shows before someone confirms."""
    resolved = period or period_of(today_in_company_tz())
    return balances.summary_for(user.id, period=resolved)


@router.get("/history")
def my_history(
    user: CurrentUserDep,
    year: str = Query(default="", pattern=r"(^\d{4}$)|^$"),
) -> dict:
    """FR-BAL-08 — consumption across a calendar year."""
    resolved = year or str(today_in_company_tz().year)
    return {"year": resolved, "months": balances.year_history_for(user.id, resolved)}


@router.post("/password")
def change_password(payload: PasswordChange, user: CurrentUserDep) -> dict:
    """FR-AUTH-05, FR-AUTH-07.

    Delegated to Supabase Auth, which salts and hashes with bcrypt. This
    product never sees, stores or logs a password — the plaintext exists only
    for the length of this call.
    """
    from app.services.supabase import supabase

    try:
        supabase.auth.admin.update_user_by_id(user.id, {"password": payload.new_password})
    except Exception as exc:  # noqa: BLE001 - message is normalised below
        raise ProblemDetail(422, "That password was rejected. Try a longer one.") from exc

    return {"status": "updated"}
