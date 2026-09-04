"""Notification tasks — FR-NOTIF-01, FR-NOTIF-03, FR-NOTIF-04.

Runs off the request path on purpose. A lead's Slack lookup taking three
seconds must not be three seconds a person stands in their kitchen waiting for
a sick-leave form to submit.

The task reads the booking fresh rather than taking a rendered message as an
argument. Celery arguments are serialised into Redis, and a booking's `reason`
is health information for sick leave (NFR-05) — passing only an id keeps it out
of the queue entirely.
"""

from __future__ import annotations

import logging
from datetime import date

from app.domain.rules import CATEGORY_LABELS
from app.services import notify
from app.services import supabase as db
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")


@celery_app.task(name="notifications.dispatch", bind=True, max_retries=3)
def dispatch(self, event: str, booking_id: str) -> dict:  # noqa: ANN001
    """Route one booking event to the people who need to know about it."""
    booking = db.get_booking(booking_id)
    if booking is None:
        logger.warning('{"event": "notify_booking_missing", "booking_id": "%s"}', booking_id)
        return {"delivered": [], "reason": "booking not found"}

    subject_profile = db.get_profile(booking["user_id"])
    if subject_profile is None:
        return {"delivered": [], "reason": "profile not found"}

    handlers = {
        "booking_created": _created,
        "booking_decided": _decided,
        "booking_released": _released,
    }
    handler = handlers.get(event)
    if handler is None:
        logger.warning('{"event": "notify_unknown_event", "name": "%s"}', event)
        return {"delivered": [], "reason": "unknown event"}

    delivered = handler(booking, subject_profile)
    logger.info(
        '{"event": "notification_dispatched", "name": "%s", "booking_id": "%s", "channels": %s}',
        event,
        booking_id,
        delivered,
    )
    return {"delivered": delivered}


def _created(booking: dict, subject: dict) -> list[str]:
    """FR-NOTIF-01 — tell the assigned lead a request has arrived.

    Where a person has no lead (a lead themselves, or the admin — Q-05), this
    goes to every admin, so a request never lands nowhere.
    """
    recipients = _approvers_for(subject)
    if not recipients:
        logger.warning('{"event": "notify_no_approver", "user_id": "%s"}', subject["id"])
        return []

    delivered: list[str] = []
    for recipient in recipients:
        delivered += notify.deliver(
            notify.Message(
                recipient_email=recipient["email"],
                recipient_name=recipient["display_name"],
                subject=f"{subject['display_name']} requested {_describe(booking)}",
                body=_detail(booking, subject),
                booking_id=booking["id"],
                # FR-NOTIF-04 — approve/reject without leaving Slack.
                actionable=True,
            )
        )
    return delivered


def _decided(booking: dict, subject: dict) -> list[str]:
    """FR-APPR-04 — tell the requester what was decided."""
    verdict = booking["status"]
    body = f"Your {_describe(booking)} was {verdict}."
    if verdict == "rejected" and booking.get("decision_note"):
        body += f"\n\nNote from your lead: {booking['decision_note']}"

    return notify.deliver(
        notify.Message(
            recipient_email=subject["email"],
            recipient_name=subject["display_name"],
            subject=f"Your leave request was {verdict}",
            body=body,
            booking_id=booking["id"],
        )
    )


def _released(booking: dict, subject: dict) -> list[str]:
    """FR-HOL-06 — tell someone their booking was cancelled by a holiday."""
    return notify.deliver(
        notify.Message(
            recipient_email=subject["email"],
            recipient_name=subject["display_name"],
            subject="A leave day was released — that date is now a holiday",
            body=(
                f"{booking['decision_note'] or 'A holiday was declared on that date.'}\n\n"
                f"Your {_describe(booking)} has been cancelled and the days "
                "returned to your allowance. You do not need to do anything."
            ),
            booking_id=booking["id"],
        )
    )


def _approvers_for(subject: dict) -> list[dict]:
    if subject.get("lead_id"):
        lead = db.get_profile(subject["lead_id"])
        return [lead] if lead and lead["is_active"] else []
    return [
        profile
        for profile in db.list_profiles(active_only=True)
        if profile["role"] == "admin" and profile["id"] != subject["id"]
    ]


def _describe(booking: dict) -> str:
    category = CATEGORY_LABELS.get(booking["category"], booking["category"] or "absence")
    length = "half day" if str(booking["duration"]) in ("0.5", "0.50") else "full day"
    when = date.fromisoformat(booking["date"]).strftime("%a %d %b %Y")
    return f"{category.lower()} ({length}) on {when}"


def _detail(booking: dict, subject: dict) -> str:
    """FR-NOTIF-03 — person, date, category, duration and reason."""
    lines = [
        f"*Who:* {subject['display_name']}",
        f"*When:* {date.fromisoformat(booking['date']).strftime('%A %d %B %Y')}",
        f"*What:* {CATEGORY_LABELS.get(booking['category'], booking['category'])}",
        f"*Length:* {'Half day' if str(booking['duration']) in ('0.5', '0.50') else 'Full day'}",
    ]
    if booking.get("reason"):
        lines.append(f"*Reason:* {booking['reason']}")
    return "\n".join(lines)
