"""Slack delivery — FR-NOTIF-02, FR-NOTIF-04.

Written against Slack's real Web API. Two calls are involved: `users.lookupByEmail`
to turn a Nunnari email into a Slack user id, then `chat.postMessage` to open a
DM. Both are plain HTTPS with a bot token, so this needs no SDK — the constitution's
"replace a dependency with fewer than 50 lines of clear code" rule applies.

Scopes the bot token needs: `chat:write` and `users:read.email`.

FR-NOTIF-04 asks that a lead be able to approve or reject without leaving Slack.
The message below carries Block Kit buttons whose `action_id` and `value` are
read back by `POST /api/v1/slack/interactions`. Interactivity must be enabled in
the Slack app with that URL as the request URL.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings

logger = logging.getLogger("nldw-task-master")

SLACK_API = "https://slack.com/api"
_TIMEOUT_SECONDS = 10


def send(message) -> bool:  # noqa: ANN001 - app.services.notify.Message, avoids a cycle
    """Post a direct message. Returns True if Slack accepted it."""
    token = settings.SLACK_BOT_TOKEN.get_secret_value() if settings.SLACK_BOT_TOKEN else ""
    if not token:
        return False

    user_id = _lookup_user(token, message.recipient_email)
    if user_id is None:
        # A Nunnari email with no matching Slack account is normal for interns
        # who have not been invited yet. Email carries it instead.
        logger.info('{"event": "slack_user_not_found", "email": "%s"}', message.recipient_email)
        return False

    payload = {
        "channel": user_id,
        "text": message.subject,  # fallback for notifications and screen readers
        "blocks": _blocks(message),
    }
    response = _post("chat.postMessage", token, payload)
    return bool(response.get("ok"))


def _blocks(message) -> list[dict]:  # noqa: ANN001
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{message.subject}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": message.body}},
    ]

    if message.actionable and message.booking_id:
        blocks.append(
            {
                "type": "actions",
                "block_id": f"booking:{message.booking_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "booking_approve",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "value": message.booking_id,
                    },
                    {
                        "type": "button",
                        "action_id": "booking_reject",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "value": message.booking_id,
                    },
                ],
            }
        )
    return blocks


def _lookup_user(token: str, email: str) -> str | None:
    query = urllib.parse.urlencode({"email": email})
    response = _get(f"users.lookupByEmail?{query}", token)
    if not response.get("ok"):
        return None
    return response.get("user", {}).get("id")


def _post(method: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    return _execute(request, method)


def _get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{SLACK_API}/{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    return _execute(request, path)


def _execute(request: urllib.request.Request, label: str) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning('{"event": "slack_call_failed", "method": "%s", "error": "%s"}', label, exc)
        return {"ok": False}

    if not body.get("ok"):
        # Slack returns HTTP 200 with ok:false for application errors, so this
        # is the only place a bad token or missing scope becomes visible.
        logger.warning(
            '{"event": "slack_error", "method": "%s", "error": "%s"}',
            label,
            body.get("error"),
        )
    return body
