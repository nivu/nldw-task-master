"""Gmail, read-only — spec 007 FR-CRM-SYNC-03, -04, NFR-CRM-02, -08; Q-23;
plan §2.1, §2.2.

A Google Workspace service account with domain-wide delegation reads the
listed mailboxes with the `gmail.readonly` scope and nothing else. The token
exchange is the documented two-legged OAuth flow, done here on `pyjwt` and
`httpx` rather than a Google client library (plan §2.1): sign an RS256 JWT
naming the key's `client_email`, the mailbox (`sub`) and the scope, and
POST it to the key's token endpoint.

The key (`GOOGLE_SERVICE_ACCOUNT_JSON`) is set on the `worker-crm-sync`
service, and on `worker-crm-ai` for `message_text()` (spec 009), the only
processes that call this module; the API, beat and the shared worker read the non-secret `CRM_GMAIL_CONFIGURED` flag instead
(plan §2.2). Access tokens live in this process's memory only, never in
Redis, the database or a Celery argument. Neither the key nor a token is
ever logged; every call logs its operation, status and latency (NFR-CRM-08)
and nothing that names a mailbox's content.

Metadata only for sync: the sync reads `format=metadata` with the headers
the rules need, and no message body is ever fetched for it (Q-29).
`message_text()` exists for the summariser alone (spec 009 FR-CRMAI-CHG-01):
on `worker-crm-ai`, it reads the text parts of one message the sync stored,
in memory, for `services/crm/summaries.py` to strip and hand to Claude. The
text is never stored, logged or queued, and no other module calls it.

Spec 010 (plan §5.5, §5.13): `search()` lists the ids a Gmail search finds,
for the sync's deal-document check (ids only, never Spam or Trash); and
every read waits and tries again on Gmail's rate limit (429) and its own
faults (5xx), at most three tries in all.

Spec 010 FR-CAP-CHG-05 (plan §5.6, CT-1): email capture's discovery, on
`worker-crm-ai`, reads a candidate conversation's metadata (`thread_meta`)
and then the text of only the messages that passed its rules
(`thread_text`). So two modules read message text, each with its own
reader: `services/crm/summaries.py` (`message_text`, stored messages only)
and `services/crm/capture.py` (`thread_text`, the surviving messages of an
unstored conversation). Neither text is stored, logged or queued, and no
other module calls either reader.
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
#: Spec 010 plan §5.13 — a 429 or a 5xx: at most three tries in all, waiting
#: `Retry-After` (capped) or else these seconds between them.
TRIES: Final = 3
RETRY_WAITS: Final = (1.0, 4.0)
RETRY_AFTER_CAP: Final = 30.0


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


def _retryable(status: int) -> bool:
    """Spec 010 NFR-CAP-03 (plan §5.13): Gmail's rate limit and its own
    faults are worth a wait; every other status is an answer."""
    return status == 429 or status >= 500


def _wait(response: Any, tries: int) -> float:
    """`Retry-After` in seconds, capped at 30, else 1 s then 4 s."""
    given = (getattr(response, "headers", None) or {}).get("Retry-After")
    try:
        return min(max(float(str(given).strip()), 0.0), RETRY_AFTER_CAP)
    except (TypeError, ValueError):
        return RETRY_WAITS[min(tries, len(RETRY_WAITS)) - 1]


def _get(mailbox: str, op: str, path: str, params: Any = None) -> dict[str, Any]:
    """One GET as `mailbox`; a 401 renews the token once; a 429 or a 5xx is
    tried again, at most `TRIES` times in all (spec 010 plan §5.13)."""
    renewed = False
    tries = 0
    while True:
        tries += 1
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
        if response.status_code == 401:
            if renewed:
                raise GmailError(f"Gmail {op} failed (401).")
            renewed = True
            tries -= 1
            forget(mailbox)
            continue
        if _retryable(response.status_code) and tries < TRIES:
            time.sleep(_wait(response, tries))
            continue
        if response.status_code == 404 and op == "history":
            raise HistoryGone("Gmail no longer has this mailbox's history from that point.")
        if response.status_code != 200:
            raise GmailError(f"Gmail {op} failed ({response.status_code}).")
        return response.json()


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


def search(mailbox: str, q: str, *, page_token: str | None, page_size: int) -> dict[str, Any]:
    """Spec 010 FR-CAP-SRC-06 — one page of the ids of the messages a Gmail
    search finds, in no label in particular and never in Spam or Trash:
    `{messages: [{id, threadId}], nextPageToken}`. Ids only; the deal
    document search reads each one's metadata after."""
    params: dict[str, Any] = {"q": q, "maxResults": page_size, "includeSpamTrash": "false"}
    if page_token:
        params["pageToken"] = page_token
    return _get(mailbox, "search", "/messages", params)


def history(mailbox: str, start: str, *, page_token: str | None, page_size: int) -> dict[str, Any]:
    """One page of what changed since `start`, at most `page_size` records:
    `{history: [...], nextPageToken, historyId}`. A 404 is `HistoryGone`."""
    params: list[tuple[str, Any]] = [
        ("startHistoryId", start),
        ("maxResults", page_size),
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


# ---------------------------------------------------------------------------
# The summariser's read — spec 009 FR-CRMAI-CHG-01, FR-CRMAI-SUM-03
# ---------------------------------------------------------------------------


def _text_parts(part: dict[str, Any]) -> list[str]:
    """The decoded `text/plain` parts of a message payload, in order; HTML,
    attachments and every other part are left out."""
    found: list[str] = []
    if part.get("mimeType") == "text/plain" and not part.get("filename"):
        data = (part.get("body") or {}).get("data")
        if data:
            raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            found.append(raw.decode("utf-8", errors="replace"))
    for sub in part.get("parts") or []:
        found += _text_parts(sub)
    return found


def message_text(mailbox: str, message_id: str) -> str:
    """The plain text of one message (`format=full`, decoded text parts
    only), for the summariser alone. Returned in memory: never stored,
    logged or queued. A message deleted meanwhile (404) answers ""."""
    try:
        found = _get(mailbox, "message_text", f"/messages/{message_id}", [("format", "full")])
    except GmailError as exc:
        if "(404)" in str(exc):
            return ""
        raise
    return "\n\n".join(_text_parts(found.get("payload") or {}))


# ---------------------------------------------------------------------------
# Email capture's reads — spec 010 FR-CAP-DIS-02 to -04, FR-CAP-CHG-05
# ---------------------------------------------------------------------------


def thread_meta(mailbox: str, thread_id: str) -> dict[str, Any]:
    """A conversation's messages as metadata only (`threads.get
    format=metadata`, the sync's headers): `{id, messages: [{id, threadId,
    labelIds, internalDate, headers}]}`, oldest first as Gmail gives them.
    A conversation deleted meanwhile (404) answers `{}`."""
    params = [("format", "metadata"), *(("metadataHeaders", h) for h in HEADERS)]
    try:
        found = _get(mailbox, "thread", f"/threads/{thread_id}", params)
    except GmailError as exc:
        if "(404)" in str(exc):
            return {}
        raise
    return {
        "id": found.get("id"),
        "messages": [
            {
                "id": m.get("id"),
                "threadId": m.get("threadId"),
                "labelIds": m.get("labelIds") or [],
                "internalDate": m.get("internalDate"),
                "headers": (m.get("payload") or {}).get("headers") or [],
            }
            for m in found.get("messages") or []
        ],
    }


def _filenames(part: dict[str, Any]) -> list[str]:
    """The names of a payload's attachments, in order."""
    found = [str(part["filename"])] if part.get("filename") else []
    for sub in part.get("parts") or []:
        found += _filenames(sub)
    return found


def thread_text(mailbox: str, message_ids: list[str]) -> list[dict[str, Any]]:
    """The plain text and attachment names of these messages only, each read
    with `format=full` as `message_text` reads one (decoded `text/plain`
    parts): `[{id, text, attachments}]`, in the order given. For email
    capture's discovery alone (FR-CAP-CHG-05), with the ids that passed its
    rules; returned in memory, never stored, logged or queued. A message
    deleted meanwhile (404) is left out."""
    out: list[dict[str, Any]] = []
    for message_id in message_ids:
        try:
            found = _get(mailbox, "thread_text", f"/messages/{message_id}", [("format", "full")])
        except GmailError as exc:
            if "(404)" in str(exc):
                continue
            raise
        payload = found.get("payload") or {}
        out.append(
            {
                "id": message_id,
                "text": "\n\n".join(_text_parts(payload)),
                "attachments": _filenames(payload),
            }
        )
    return out
