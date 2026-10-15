"""Gmail, read-only — spec 007 FR-CRM-SYNC-03, -04, NFR-CRM-02, -08; Q-23;
plan §2.1, §2.2.

A Google Workspace service account with domain-wide delegation reads the
listed mailboxes with the `gmail.readonly` scope and nothing else. The token
exchange is the documented two-legged OAuth flow, done here on `pyjwt` and
`httpx` rather than a Google client library (plan §2.1): sign an RS256 JWT
naming the key's `client_email`, the mailbox (`sub`) and the scope, and
POST it to the key's token endpoint.

The key (`GOOGLE_SERVICE_ACCOUNT_JSON`) is set on the `worker-crm-sync`
service only, the one process that calls this module; the API, beat and the
shared worker read the non-secret `CRM_GMAIL_CONFIGURED` flag instead
(plan §2.2). Access tokens live in this process's memory only, never in
Redis, the database or a Celery argument. Neither the key nor a token is
ever logged; every call logs its operation, status and latency (NFR-CRM-08)
and nothing that names a mailbox's content.

Only metadata is read (`format=metadata` with the headers the rules need):
no message body is ever fetched (Q-29).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Final

import httpx
import jwt

from app.config import settings
from app.domain.crm.email_rules import HEADERS

logger = logging.getLogger("nldw-task-master")

SCOPE: Final = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URI: Final = "https://oauth2.googleapis.com/token"
API: Final = "https://gmail.googleapis.com/gmail/v1/users"
#: Google accepts an assertion valid for at most an hour.
ASSERTION_SECONDS: Final = 3600
#: A token is renewed this long before Google says it expires.
EARLY: Final = 120
TIMEOUT: Final = httpx.Timeout(20.0, connect=5.0)


class GmailError(Exception):
    """A Gmail or token call failed. The message names the operation and
    the HTTP status only, never a mailbox's content or a credential."""


class HistoryGone(GmailError):
    """The stored history id is too old for Gmail (404): read the labels
    afresh."""


class NotConfigured(GmailError):
    """No service-account key in this process."""


#: Access tokens by mailbox: (token, expires at, monotonic seconds).
_tokens: dict[str, tuple[str, float]] = {}


def _key() -> dict[str, Any]:
    """The service-account key, raw JSON or base64 of it (plan §2.2)."""
    secret = settings.GOOGLE_SERVICE_ACCOUNT_JSON
    raw = secret.get_secret_value().strip() if secret else ""
    if not raw:
        raise NotConfigured("The Gmail service-account key is not set in this process.")
    try:
        text = raw if raw.startswith("{") else base64.b64decode(raw).decode("utf-8")
        key = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        raise NotConfigured("The Gmail service-account key is not valid JSON.") from None
    if not key.get("client_email") or not key.get("private_key"):
        raise NotConfigured("The Gmail service-account key lacks client_email or private_key.")
    return key


def claims(key: dict[str, Any], mailbox: str, now: int) -> dict[str, Any]:
    """The JWT's claims: the service account acting as `mailbox`, read-only."""
    return {
        "iss": key["client_email"],
        "sub": mailbox,
        "scope": SCOPE,
        "aud": key.get("token_uri") or TOKEN_URI,
        "iat": now,
        "exp": now + ASSERTION_SECONDS,
    }


def assertion(key: dict[str, Any], mailbox: str, now: int) -> str:
    return jwt.encode(
        claims(key, mailbox, now),
        key["private_key"],
        algorithm="RS256",
        headers={"kid": key["private_key_id"]} if key.get("private_key_id") else None,
    )


def _log(op: str, status: int | str, started: float) -> None:
    logger.info(
        '{"event": "crm_gmail_call", "op": "%s", "status": "%s", "ms": %d}',
        op,
        status,
        int((time.monotonic() - started) * 1000),
    )


def token(mailbox: str) -> str:
    """An access token for `mailbox`, from this process's cache while it is
    good."""
    cached = _tokens.get(mailbox)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    key = _key()
    started = time.monotonic()
    try:
        response = httpx.post(
            key.get("token_uri") or TOKEN_URI,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion(key, mailbox, int(time.time())),
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _log("token", type(exc).__name__, started)
        raise GmailError(f"The Gmail token request failed ({type(exc).__name__}).") from None
    _log("token", response.status_code, started)
    if response.status_code != 200:
        # Google's error body names the reason (for example unauthorized_client
        # when delegation is missing); it carries no secret.
        reason = ""
        try:
            reason = str(response.json().get("error") or "")[:60]
        except ValueError:
            pass
        raise GmailError(f"The Gmail token request was refused ({response.status_code} {reason}).")
    body = response.json()
    expires = time.monotonic() + max(int(body.get("expires_in", 3600)) - EARLY, 60)
    _tokens[mailbox] = (body["access_token"], expires)
    return body["access_token"]


def forget(mailbox: str | None = None) -> None:
    """Drops cached tokens (one mailbox's, or all)."""
    if mailbox is None:
        _tokens.clear()
    else:
        _tokens.pop(mailbox, None)


def _get(mailbox: str, op: str, path: str, params: Any = None) -> dict[str, Any]:
    """One GET as `mailbox`; a 401 renews the token once."""
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            response = httpx.get(
                f"{API}/me{path}",
                params=params,
                headers={"Authorization": f"Bearer {token(mailbox)}"},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            _log(op, type(exc).__name__, started)
            raise GmailError(f"Gmail {op} failed ({type(exc).__name__}).") from None
        _log(op, response.status_code, started)
        if response.status_code == 401 and attempt == 1:
            forget(mailbox)
            continue
        if response.status_code == 404 and op == "history":
            raise HistoryGone("Gmail no longer has this mailbox's history from that point.")
        if response.status_code != 200:
            raise GmailError(f"Gmail {op} failed ({response.status_code}).")
        return response.json()
    raise GmailError(f"Gmail {op} failed (401).")


# ---------------------------------------------------------------------------
# The reads the sync makes
# ---------------------------------------------------------------------------


def profile(mailbox: str) -> dict[str, Any]:
    """`{emailAddress, historyId, …}`: the history id a backfill starts from."""
    return _get(mailbox, "profile", "/profile")


def labels(mailbox: str) -> list[dict[str, Any]]:
    """The mailbox's labels, `[{id, name, type}]`."""
    return _get(mailbox, "labels", "/labels").get("labels") or []


def list_messages(
    mailbox: str, label_id: str, *, after: int, page_token: str | None, page_size: int
) -> dict[str, Any]:
    """One page of the messages carrying a label, received after `after`
    (seconds since the epoch): `{messages: [{id, threadId}], nextPageToken}`."""
    params: dict[str, Any] = {
        "labelIds": label_id,
        "q": f"after:{after}",
        "maxResults": page_size,
        "includeSpamTrash": "false",
    }
    if page_token:
        params["pageToken"] = page_token
    return _get(mailbox, "list", "/messages", params)


def history(mailbox: str, start: str, *, page_token: str | None) -> dict[str, Any]:
    """One page of what changed since `start`: `{history: [...],
    nextPageToken, historyId}`. A 404 is `HistoryGone`."""
    params: list[tuple[str, Any]] = [
        ("startHistoryId", start),
        ("maxResults", 500),
        ("historyTypes", "messageAdded"),
        ("historyTypes", "messageDeleted"),
        ("historyTypes", "labelAdded"),
        ("historyTypes", "labelRemoved"),
    ]
    if page_token:
        params.append(("pageToken", page_token))
    return _get(mailbox, "history", "/history", params)


def message(mailbox: str, message_id: str) -> dict[str, Any]:
    """A message's metadata only — `{id, threadId, labelIds, internalDate,
    headers: [{name, value}]}`; never its body (Q-29). A message deleted
    meanwhile (404) answers `{}`."""
    params = [("format", "metadata"), *(("metadataHeaders", h) for h in HEADERS)]
    try:
        found = _get(mailbox, "message", f"/messages/{message_id}", params)
    except GmailError as exc:
        if "(404)" in str(exc):
            return {}
        raise
    return {
        "id": found.get("id"),
        "threadId": found.get("threadId"),
        "labelIds": found.get("labelIds") or [],
        "internalDate": found.get("internalDate"),
        "headers": (found.get("payload") or {}).get("headers") or [],
    }
