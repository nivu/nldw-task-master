"""Booking endpoints — FR-BOOK, FR-APPR.

Thin. Every rule lives in `app.domain.rules` and every write in
`app.services.bookings`; this layer only converts between HTTP and those, and
turns a `BookingRefused` into RFC 7807.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep
from app.api.errors import ProblemDetail
from app.domain.calendar import today_in_company_tz
from app.domain.rules import is_locked
from app.schemas import BookingCreate, BookingDecision
from app.services import bookings as booking_service
from app.services import supabase as db

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("", status_code=201)
def create_booking(payload: BookingCreate, user: CurrentUserDep) -> dict:
    """Book a day for yourself — FR-BOOK-01 through FR-BOOK-12.

    A person books only for themselves. There is no `user_id` in the payload,
    which is not an omission: accepting one would make "book leave on someone
    else's behalf" a request-shaped decision rather than a product decision
    nobody has made.
    """
    try:
        created = booking_service.create_or_replace(
            user_id=user.id,
            day=payload.date,
            category=payload.category,
            duration=payload.duration,
            reason=payload.reason,
            actor_id=user.id,
        )
    except booking_service.BookingRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc

    return _present(created, user_id=user.id)


@router.delete("/{booking_id}")
def withdraw_booking(booking_id: str, user: CurrentUserDep) -> dict:
    """Clear a day inside its edit window — FR-BOOK-07, FR-BOOK-11, §6.3."""
    try:
        updated = booking_service.withdraw(booking_id=booking_id, actor_id=user.id)
    except booking_service.BookingRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc

    return _present(updated, user_id=user.id)


@router.post("/{booking_id}/decision")
def decide_booking(booking_id: str, payload: BookingDecision, user: CurrentUserDep) -> dict:
    """Approve or reject — FR-APPR-02/03/05/06.

    Authorisation is `can_decide` in the domain layer, not a role check here:
    being a lead is necessary but not sufficient, since it must be a lead *of
    this person* (FR-APPR-05), and nobody may decide their own.
    """
    try:
        updated = booking_service.decide(
            booking_id=booking_id,
            approve=payload.approve,
            note=payload.note,
            actor=user,
        )
    except booking_service.BookingRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc

    return _present(updated, user_id=user.id)


@router.get("/{booking_id}")
def get_booking(booking_id: str, user: CurrentUserDep) -> dict:
    """One booking, with its reason only if the viewer is entitled to it."""
    booking = db.get_booking(booking_id)
    if booking is None:
        raise ProblemDetail(404, "No such booking.")

    owner = db.get_profile(booking["user_id"])
    if owner is None:
        raise ProblemDetail(404, "No such booking.")

    from app.domain.approval import Person, can_view_reason

    subject = Person(
        id=owner["id"], role=owner["role"], lead_id=owner["lead_id"], is_active=owner["is_active"]
    )
    if not can_view_reason(user, subject):
        # NFR-05. 404 rather than 403: confirming a booking exists for a
        # colleague on a given date already leaks that they were absent.
        raise ProblemDetail(404, "No such booking.")

    return _present(booking, user_id=user.id, include_reason=True)


def _present(booking: dict, *, user_id: str, include_reason: bool = True) -> dict:
    """Shape a booking row for the wire.

    `locked` and `can_edit` are computed here rather than in the browser
    (NFR-04). The client is told the answer, never the ingredients.
    """
    from datetime import date as date_type

    day = date_type.fromisoformat(booking["date"])
    locked = is_locked(day, today_in_company_tz())

    out = {
        "id": booking["id"],
        "user_id": booking["user_id"],
        "date": booking["date"],
        "category": booking["category"],
        "duration": str(booking["duration"]),
        "status": booking["status"],
        "decided_by": booking.get("decided_by"),
        "decided_at": booking.get("decided_at"),
        "decision_note": booking.get("decision_note"),
        "locked": locked,
        "backfilled": bool(booking.get("backfilled_by")),
        "can_edit": (
            not locked
            and booking["user_id"] == user_id
            and booking["status"] in ("pending", "approved")
        ),
    }
    if include_reason:
        out["reason"] = booking.get("reason")
    return out
