"""Booking operations — create, change, withdraw, decide, release.

This is where the rules in `app.domain` meet stored rows. Every function here
validates before it writes and raises `BookingRefused` with a message meant for
the person who tripped the rule; the API layer turns that into RFC 7807.

Two things this module is careful about:

* **The lock check is never delegated to the caller.** NFR-04 requires it
  server-side, and every mutating path below re-derives today from the server
  clock rather than trusting anything in the request.
* **Notifications never fail an operation.** FR-NOTIF-05 — the booking stands
  and the failure is logged. Enqueueing is wrapped accordingly, because a
  Redis outage must not stop someone marking themselves sick.
"""

from __future__ import annotations

import logging
from datetime import UTC, date
from decimal import Decimal
from typing import Any

from app.domain import rules
from app.domain.approval import Person, can_decide
from app.domain.calendar import period_of, today_in_company_tz
from app.services import audit, balances, settings_store
from app.services import supabase as db

logger = logging.getLogger("nldw-task-master")


class BookingRefused(Exception):
    """A rule said no. The message is safe to show the requester."""

    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Creating and changing — FR-BOOK
# ---------------------------------------------------------------------------


def create_or_replace(
    *,
    user_id: str,
    day: date,
    category: str,
    duration: Decimal,
    reason: str | None,
    actor_id: str,
) -> dict[str, Any]:
    """Book a day, replacing whatever the person had booked for it.

    FR-BOOK-06 — a day holds at most one booking, and changing the category
    replaces the existing one. "Replace" here means withdrawing the old row and
    inserting a new one rather than mutating in place: the old row's history is
    part of the audit trail, and rewriting it would lose the fact that the
    person changed their mind.
    """
    today = today_in_company_tz()
    existing = db.find_booking_on(user_id, day, sorted(rules.OCCUPYING_STATES))

    if existing and existing["status"] == "unrecognised":
        raise BookingRefused(
            f"{day.isoformat()} is flagged as an unrecognised absence. "
            "Ask your lead to clear it before booking."
        )

    # An existing booking's own cost must be handed back before the new one is
    # tested, or changing 1.0 to 0.5 on the same day fails for lack of an
    # allowance the change itself releases.
    give_back: Decimal | None = None
    if existing:
        if rules.is_locked(day, today):
            raise BookingRefused(
                f"{day.isoformat()} has passed and is locked. "
                "Past bookings cannot be changed or removed.",
                status=409,
            )
        if existing["category"] == category:
            give_back = Decimal(str(existing["duration"]))

    holiday = db.get_holiday_on(day)
    remaining = balances.remaining_for(
        user_id,
        period_of(day),
        category,
        exclude_booking_duration=give_back,
    )

    refusal = rules.validate_booking(
        rules.BookingRequest(day=day, category=category, duration=duration, reason=reason),
        holiday_name=holiday["name"] if holiday else None,
        remaining=remaining,
        today=today,
        max_future_days=settings_store.max_future_booking_days(),
        allow_excess=settings_store.allow_excess_booking(),
    )
    if refusal:
        raise BookingRefused(refusal)

    if existing:
        db.update_booking(
            existing["id"],
            {"status": "withdrawn", "decided_by": actor_id, "decided_at": _now()},
        )
        audit.record(
            action="booking.replaced",
            target_table="bookings",
            target_id=existing["id"],
            actor_id=actor_id,
            before={"status": existing["status"], "category": existing["category"]},
            after={"status": "withdrawn"},
        )

    created = db.insert_booking(
        {
            "user_id": user_id,
            "date": day.isoformat(),
            "category": category,
            "duration": str(duration),
            "reason": (reason or "").strip() or None,
            "status": "pending",
            "created_by": actor_id,
        }
    )

    _notify("booking_created", created["id"])
    return created


def withdraw(*, booking_id: str, actor_id: str) -> dict[str, Any]:
    """Remove a booking inside its edit window — FR-BOOK-07, FR-BOOK-11.

    The returned allowance needs no bookkeeping: the ledger derives balances
    from consuming states, and `withdrawn` is not one, so the day is back the
    moment the status changes.
    """
    booking = _require(booking_id)

    if booking["user_id"] != actor_id:
        raise BookingRefused("You can only withdraw your own bookings.", status=403)

    day = date.fromisoformat(booking["date"])
    if rules.is_locked(day, today_in_company_tz()):
        raise BookingRefused(
            f"{booking['date']} has passed and is locked. "
            "Bookings can only be removed on or before the day they apply to.",
            status=409,
        )

    refusal = rules.check_transition(booking["status"], "withdrawn")
    if refusal:
        raise BookingRefused(refusal, status=409)

    updated = db.update_booking(
        booking_id, {"status": "withdrawn", "decided_by": actor_id, "decided_at": _now()}
    )
    audit.record(
        action="booking.withdrawn",
        target_table="bookings",
        target_id=booking_id,
        actor_id=actor_id,
        before={"status": booking["status"]},
        after={"status": "withdrawn"},
    )
    return updated


# ---------------------------------------------------------------------------
# Deciding — FR-APPR
# ---------------------------------------------------------------------------


def decide(
    *,
    booking_id: str,
    approve: bool,
    note: str | None,
    actor: Person,
) -> dict[str, Any]:
    """Approve or reject a report's booking — FR-APPR-02/03/05/06."""
    booking = _require(booking_id)
    subject_row = db.get_profile(booking["user_id"])
    if subject_row is None:
        raise BookingRefused("That booking's owner no longer exists.", status=404)

    subject = Person(
        id=subject_row["id"],
        role=subject_row["role"],
        lead_id=subject_row["lead_id"],
        is_active=subject_row["is_active"],
    )
    if not can_decide(actor, subject):
        # FR-APPR-05. Deliberately 403 with a plain message rather than 404:
        # the requester is a lead who legitimately exists, and pretending the
        # booking is missing would send them hunting for a bug.
        raise BookingRefused(
            "You can only approve or reject bookings for your own reports.", status=403
        )

    target = "approved" if approve else "rejected"
    refusal = rules.check_transition(booking["status"], target)
    if refusal:
        raise BookingRefused(refusal, status=409)

    if not approve and not (note or "").strip():
        raise BookingRefused("A rejection needs a note explaining why.")

    updated = db.update_booking(
        booking_id,
        {
            "status": target,
            "decided_by": actor.id,
            "decided_at": _now(),
            "decision_note": (note or "").strip() or None,
        },
    )
    audit.record(
        action=f"booking.{target}",
        target_table="bookings",
        target_id=booking_id,
        actor_id=actor.id,
        before={"status": booking["status"]},
        after={"status": target},
    )
    _notify("booking_decided", booking_id)
    return updated


def flag_unrecognised(
    *, user_id: str, day: date, note: str | None, actor: Person
) -> dict[str, Any]:
    """Record that someone was absent without booking — FR-LEAD-03.

    V1 has no automatic detector; nothing tells the system somebody was away.
    A lead sets this by hand after the fact, which is what "we can track it
    back" meant on the call.

    Costs no allowance (§6.4) and is restricted to past dates (A-15) — a future
    unrecognised absence is a contradiction.
    """
    subject_row = db.get_profile(user_id)
    if subject_row is None:
        raise BookingRefused("No such person.", status=404)

    subject = Person(
        id=subject_row["id"],
        role=subject_row["role"],
        lead_id=subject_row["lead_id"],
        is_active=subject_row["is_active"],
    )
    if not can_decide(actor, subject):
        raise BookingRefused("You can only flag absences for your own reports.", status=403)

    today = today_in_company_tz()
    if day >= today:
        raise BookingRefused("An unrecognised absence can only be flagged for a past date.")

    if db.find_booking_on(user_id, day, sorted(rules.OCCUPYING_STATES)):
        raise BookingRefused(f"{day.isoformat()} already has a booking.", status=409)

    created = db.insert_booking(
        {
            "user_id": user_id,
            "date": day.isoformat(),
            "category": None,
            "duration": "1.0",
            "status": "unrecognised",
            "decision_note": (note or "").strip() or None,
            "created_by": actor.id,
            "decided_by": actor.id,
            "decided_at": _now(),
        }
    )
    audit.record(
        action="booking.unrecognised",
        target_table="bookings",
        target_id=created["id"],
        actor_id=actor.id,
        after={"status": "unrecognised", "date": day.isoformat(), "user_id": user_id},
    )
    return created


def release_for_holiday(*, day: date, holiday_name: str, actor_id: str) -> list[dict[str, Any]]:
    """Cancel bookings a newly declared holiday has made redundant.

    FR-HOL-05/06 — the allowance goes back (again, automatically: `released` is
    not a consuming state) and the affected people are told, because otherwise
    a day they had planned around silently changes meaning.
    """
    affected = db.list_bookings(start=day, end=day, statuses=sorted(rules.CONSUMING_STATES))
    released: list[dict[str, Any]] = []
    for booking in affected:
        updated = db.update_booking(
            booking["id"],
            {
                "status": "released",
                "decided_by": actor_id,
                "decided_at": _now(),
                "decision_note": f"Released — {day.isoformat()} declared as {holiday_name}.",
            },
        )
        audit.record(
            action="booking.released",
            target_table="bookings",
            target_id=booking["id"],
            actor_id=actor_id,
            before={"status": booking["status"]},
            after={"status": "released", "holiday": holiday_name},
        )
        _notify("booking_released", booking["id"])
        released.append(updated)
    return released


# ---------------------------------------------------------------------------


def _require(booking_id: str) -> dict[str, Any]:
    booking = db.get_booking(booking_id)
    if booking is None:
        raise BookingRefused("No such booking.", status=404)
    return booking


def _now() -> str:
    from datetime import datetime

    return datetime.now(tz=UTC).isoformat()


def _notify(event: str, booking_id: str) -> None:
    """Hand a notification to Celery without ever letting it fail the caller.

    FR-NOTIF-05: notification delivery failure MUST NOT fail the booking. The
    failure mode this guards is not a rejected Slack message — the task handles
    that — but an unreachable broker, where `.delay()` itself raises. Someone
    marking themselves sick at 08:00 must not be blocked because Redis is down.
    """
    try:
        from app.tasks import notifications

        notifications.dispatch.delay(event, booking_id)
    except Exception:
        logger.exception(
            '{"event": "notification_enqueue_failed", "notification": "%s", '
            '"booking_id": "%s", "impact": "booking stands, lead not notified"}',
            event,
            booking_id,
        )
