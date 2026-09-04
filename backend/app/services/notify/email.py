"""Email delivery — FR-NOTIF-02's required fallback.

Plain SMTP through the standard library. Inert until `SMTP_HOST` is set.

Email is the fallback rather than the primary channel, and §1 is explicit about
why: leave arriving by email is the problem this product exists to solve. What
makes this different is that the mail is a *notification of a recorded fact*,
not the record itself — the booking already exists in the portal whether or not
this message is ever read.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("nldw-task-master")

_TIMEOUT_SECONDS = 15


def send(message) -> bool:  # noqa: ANN001 - app.services.notify.Message, avoids a cycle
    if not settings.SMTP_HOST:
        return False

    mail = EmailMessage()
    mail["Subject"] = message.subject
    mail["From"] = settings.SMTP_FROM
    mail["To"] = message.recipient_email
    mail.set_content(f"{message.body}\n\n— Nunnari Employee Portal\n{settings.FRONTEND_URL}")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=_TIMEOUT_SECONDS) as server:
        server.ehlo()
        # Opportunistic TLS: upgrade when the server offers it. Port 25 relays
        # inside a private network often do not, and refusing to send at all
        # would mean the fallback channel silently never works.
        if server.has_extn("starttls"):
            server.starttls()
            server.ehlo()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD.get_secret_value())
        server.send_message(mail)

    logger.info('{"event": "email_sent", "to": "%s"}', message.recipient_email)
    return True
