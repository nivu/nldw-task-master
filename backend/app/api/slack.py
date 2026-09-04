"""Slack interactivity — FR-NOTIF-04.

"A Slack notification SHOULD allow approve and reject without leaving Slack."

This endpoint receives the button presses from the Block Kit message in
`app.services.notify.slack`. It is the only unauthenticated route in the
application, which makes signature verification the whole of its security:
without it, anyone who learned the URL could approve their own leave.

Slack's scheme (v0) is an HMAC-SHA256 over `v0:{timestamp}:{raw body}` keyed by
the app's signing secret. The raw body matters — re-serialising the parsed form
changes the bytes and the signature will not match.

The Slack user is mapped back to a portal account by email, and the resulting
decision runs through exactly the same `bookings.decide` path as the web UI, so
FR-APPR-05 applies identically. Slack is a transport, not a trust boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse

from fastapi import APIRouter, Request

from app.api.errors import ProblemDetail
from app.config import settings
from app.domain.approval import Person
from app.services import bookings as booking_service
from app.services import supabase as db

logger = logging.getLogger("nldw-task-master")
router = APIRouter(prefix="/slack", tags=["slack"])

#: Slack's own guidance. A request older than this is a replay.
_MAX_SKEW_SECONDS = 60 * 5


@router.post("/interactions")
async def interactions(request: Request) -> dict:
    """Handle an approve/reject button press from Slack."""
    if not settings.SLACK_SIGNING_SECRET:
        # Refusing outright is the safe default: an unconfigured secret would
        # otherwise mean an unauthenticated endpoint that mutates bookings.
        raise ProblemDetail(503, "Slack interactivity is not configured.")

    raw = await request.body()
    _verify_signature(request, raw)

    payload = _parse(raw)
    action = (payload.get("actions") or [{}])[0]
    action_id = action.get("action_id")
    booking_id = action.get("value")

    if action_id not in ("booking_approve", "booking_reject") or not booking_id:
        raise ProblemDetail(400, "Unrecognised Slack action.")

    actor = _resolve_actor(payload)
    approve = action_id == "booking_approve"

    try:
        booking_service.decide(
            booking_id=booking_id,
            approve=approve,
            # A Slack button carries no free text. FR-APPR-03 requires a note
            # on rejection, so one is supplied that says exactly what happened;
            # a lead wanting to say more can open the portal.
            note=None if approve else "Rejected from Slack without a note.",
            actor=actor,
        )
    except booking_service.BookingRefused as exc:
        # Replying in-channel rather than erroring: an HTTP error to Slack
        # renders as an unhelpful "something went wrong" the lead cannot act on.
        return {"response_type": "ephemeral", "replace_original": False, "text": exc.message}

    verdict = "Approved" if approve else "Rejected"
    logger.info(
        '{"event": "slack_decision", "booking_id": "%s", "verdict": "%s", "actor": "%s"}',
        booking_id,
        verdict.lower(),
        actor.id,
    )
    return {"response_type": "in_channel", "replace_original": True, "text": f"{verdict}."}


def _verify_signature(request: Request, raw: bytes) -> None:
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")

    if not timestamp or not signature:
        raise ProblemDetail(401, "Missing Slack signature headers.")

    try:
        age = abs(time.time() - int(timestamp))
    except ValueError as exc:
        raise ProblemDetail(401, "Malformed Slack timestamp.") from exc

    if age > _MAX_SKEW_SECONDS:
        raise ProblemDetail(401, "Slack request is too old.")

    secret = settings.SLACK_SIGNING_SECRET.get_secret_value().encode()
    basestring = b"v0:" + timestamp.encode() + b":" + raw
    expected = "v0=" + hmac.new(secret, basestring, hashlib.sha256).hexdigest()

    # compare_digest, not ==, so the comparison does not leak the signature
    # one byte at a time through its timing.
    if not hmac.compare_digest(expected, signature):
        logger.warning('{"event": "slack_bad_signature"}')
        raise ProblemDetail(401, "Bad Slack signature.")


def _parse(raw: bytes) -> dict:
    form = urllib.parse.parse_qs(raw.decode())
    blob = (form.get("payload") or [""])[0]
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ProblemDetail(400, "Malformed Slack payload.") from exc


def _resolve_actor(payload: dict) -> Person:
    """Map the Slack user who pressed the button to a portal account.

    Matched on email, which is why the bot needs `users:read.email`. A Slack
    account with no portal profile is refused — the button must not become a
    way to act without an account.
    """
    email = (payload.get("user") or {}).get("email")
    if not email:
        # Slack omits the email unless the app has the scope. Fail closed.
        raise ProblemDetail(
            403, "Could not identify you. The Slack app needs the users:read.email scope."
        )

    profile = db.get_profile_by_email(email)
    if profile is None or not profile["is_active"]:
        raise ProblemDetail(403, "That Slack account is not linked to an active portal user.")

    return Person(
        id=profile["id"],
        role=profile["role"],
        lead_id=profile["lead_id"],
        is_active=profile["is_active"],
    )
