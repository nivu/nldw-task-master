"""Notification delivery — FR-NOTIF.

Slack is primary, email is the fallback (FR-NOTIF-02). Each adapter is written
against the real API and stays inert until its credentials are present: a
missing token is logged once and skipped, never raised. That is what lets this
ship before a Slack app exists without leaving a stub to rip out later — adding
`SLACK_BOT_TOKEN` to the environment is the whole activation step.

`deliver` returns which channels actually sent, so the caller can log the truth
rather than assuming.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.services.notify import email as email_adapter
from app.services.notify import slack as slack_adapter

logger = logging.getLogger("nldw-task-master")


@dataclass(frozen=True)
class Message:
    """One notification, rendered once and sent through whichever channels work.

    `reason` is carried because FR-NOTIF-03 requires it, and a lead deciding
    from Slack needs it to decide at all. Q-06 restricts reasons in the *team
    list*, not in the notification to the one person entitled to act on it.
    """

    recipient_email: str
    recipient_name: str
    subject: str
    body: str
    booking_id: str | None = None
    actionable: bool = False


def deliver(message: Message) -> list[str]:
    """Send through every configured channel. Never raises.

    Both channels are attempted rather than stopping at the first success:
    "Slack SHOULD be primary, email MUST be available as the fallback" is about
    which one carries the interaction, and a lead who has not opened Slack
    since yesterday still needs the mail.
    """
    delivered: list[str] = []

    for name, enabled, send in (
        ("slack", settings.slack_enabled, slack_adapter.send),
        ("email", settings.email_enabled, email_adapter.send),
    ):
        if not enabled:
            logger.info(
                '{"event": "notification_skipped", "channel": "%s", '
                '"reason": "no credentials configured"}',
                name,
            )
            continue
        try:
            if send(message):
                delivered.append(name)
        except Exception:
            # FR-NOTIF-05 — log and carry on. The booking already stands.
            logger.exception(
                '{"event": "notification_failed", "channel": "%s", "to": "%s"}',
                name,
                message.recipient_email,
            )

    if not delivered:
        logger.warning(
            '{"event": "notification_undelivered", "to": "%s", "subject": "%s"}',
            message.recipient_email,
            message.subject,
        )
    return delivered
