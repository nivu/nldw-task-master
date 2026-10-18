"""Fireflies, raw HTTP — spec 009 FR-CRMAI-MEET-01, -02, NFR-CRMAI-01, -04,
-06, -08; C5, Q-42; plan §2.1, §5.6.

Two GraphQL reads on `POST https://api.fireflies.ai/graphql` with the key as
a bearer token:

- `list_since(cursor)`: the transcripts updated after the cursor, **metadata
  only** (id, title, date, last update, attendee emails, transcript link).
  Matching is done on this alone (FR-CRMAI-MEET-02). Fireflies filters its
  list by meeting date, so the query reaches `LOOKBACK` before the cursor and
  keeps what was updated after it, which also finds a transcript of an older
  meeting updated since (FR-CRMAI-MEET-07).
- `transcript(id)`: the content of one transcript (its sentences and
  Fireflies' own summary), asked for only after a match in exactly one
  workspace whose phase 2 is on. Returned in memory for
  `services/crm/meetings.py` to hand to Claude: never stored, logged or
  queued (P-3).

No vendor SDK, as the Gmail and Claude clients (Q-42). Timeouts: 5 s to
connect, 30 s to read. The key (`FIREFLIES_API_KEY`) is set on
`worker-crm-ai` only and this module refuses to run without it. Every call
logs one JSON line with the job (`meetings`, the only caller), the
workspace a transcript is fetched for (`for_workspace`; `-` for the poll,
which serves every workspace), the operation, the status and the latency,
never a title, an address or the content (NFR-CRMAI-06, amended in S5). No exception raised here
carries a response body, the query's variables or the key: messages name
the operation and the status or the exception type only.

Field names follow Fireflies' published GraphQL schema; they live here
alone, so the owner's first real run can correct them in one place (plan
§11).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from app.config import settings

logger = logging.getLogger("nldw-task-master")

API: Final = "https://api.fireflies.ai/graphql"
TIMEOUT: Final = httpx.Timeout(30.0, connect=5.0)
#: Transcripts asked for per page, and the most pages one poll reads.
PAGE: Final = 50
MAX_PAGES: Final = 10
#: The one job that calls Fireflies (NFR-CRMAI-06).
JOB: Final = "meetings"
#: The workspace key the running call is for; "-" for the poll.
_workspace: ContextVar[str] = ContextVar("crm_fireflies_workspace", default="-")
#: How far before the cursor the list reaches, for transcripts updated after
#: their meeting; the first poll reads this far back from now.
LOOKBACK: Final = timedelta(days=7)

LIST_QUERY: Final = """
query Transcripts($fromDate: DateTime, $limit: Int, $skip: Int) {
  transcripts(fromDate: $fromDate, limit: $limit, skip: $skip) {
    id
    title
    date
    dateUpdated
    transcript_url
    organizer_email
    participants
    meeting_attendees { email }
  }
}
"""

TRANSCRIPT_QUERY: Final = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    sentences { speaker_name text }
    summary { overview action_items }
  }
}
"""


class FirefliesError(Exception):
    """A Fireflies call failed. The message names the operation and the
    status or exception type only, never content or the key."""


class NotConfigured(FirefliesError):
    """No Fireflies key in this process."""


def _http() -> httpx.Client:
    """The HTTP client; tests give it a mock transport."""
    return httpx.Client(timeout=TIMEOUT)


def configured() -> bool:
    secret = settings.FIREFLIES_API_KEY
    return bool(secret and secret.get_secret_value().strip())


@contextmanager
def for_workspace(key: str) -> Iterator[None]:
    """The calls made inside are logged with the workspace `key`."""
    token = _workspace.set(key)
    try:
        yield
    finally:
        _workspace.reset(token)


def _log(op: str, status: Any, started: float) -> None:
    logger.info(
        '{"event": "crm_fireflies_call", "job": "%s", "workspace": "%s", "op": "%s", '
        '"status": "%s", "ms": %d}',
        JOB,
        _workspace.get(),
        op,
        status,
        int((time.monotonic() - started) * 1000),
    )


def _post(op: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """One GraphQL request; its `data`, or a `FirefliesError`."""
    secret = settings.FIREFLIES_API_KEY
    key = secret.get_secret_value().strip() if secret else ""
    if not key:
        raise NotConfigured("The Fireflies key is not set in this process.")
    headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    started = time.monotonic()
    failed: str | None = None
    try:
        with _http() as http:
            response = http.post(
                API, json={"query": query, "variables": variables}, headers=headers, timeout=TIMEOUT
            )
    except httpx.HTTPError as exc:
        failed = type(exc).__name__
    if failed is not None:
        # Raised outside the handler, so the original exception (whose text
        # may name the request) is not kept as the context either.
        _log(op, failed, started)
        raise FirefliesError(f"Fireflies {op} failed ({failed}).")
    _log(op, response.status_code, started)
    try:
        answer = response.json() if response.status_code == 200 else {}
    except ValueError:
        answer = {}
    if response.status_code != 200 or not isinstance(answer, dict):
        raise FirefliesError(f"Fireflies {op} failed ({response.status_code}).")
    if answer.get("errors") or not isinstance(answer.get("data"), dict):
        # GraphQL errors can repeat the variables; only their presence is told.
        raise FirefliesError(f"Fireflies {op} failed (GraphQL error).")
    return answer["data"]


def _when(value: Any) -> str | None:
    """A Fireflies time (milliseconds since the epoch, or ISO 8601) as ISO
    8601 in UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _emails(row: dict[str, Any]) -> list[str]:
    found = [row.get("organizer_email")]
    found += list(row.get("participants") or [])
    found += [a.get("email") for a in row.get("meeting_attendees") or [] if isinstance(a, dict)]
    out = {str(e).strip().lower() for e in found if e and "@" in str(e)}
    return sorted(out)


def _meta(row: dict[str, Any]) -> dict[str, Any] | None:
    """A listed transcript's metadata: `{id, title, date, updated_at,
    attendees, transcript_url}`; None without an id or a date."""
    date = _when(row.get("date"))
    if not row.get("id") or date is None:
        return None
    return {
        "id": str(row["id"])[:150],
        "title": str(row.get("title") or "")[:300],
        "date": date,
        "updated_at": _when(row.get("dateUpdated")) or date,
        "attendees": _emails(row),
        "transcript_url": row.get("transcript_url") or None,
    }


def list_since(cursor: str | None, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """The transcripts updated after `cursor` (ISO 8601; None for a first
    poll), oldest update first, metadata only."""
    since = datetime.fromisoformat(cursor) if cursor else (now or datetime.now(UTC))
    from_date = (since - LOOKBACK).astimezone(UTC).isoformat().replace("+00:00", "Z")
    found: dict[str, dict[str, Any]] = {}
    for page in range(MAX_PAGES):
        data = _post(
            "list", LIST_QUERY, {"fromDate": from_date, "limit": PAGE, "skip": page * PAGE}
        )
        rows = data.get("transcripts") or []
        for row in rows:
            meta = _meta(row) if isinstance(row, dict) else None
            if meta is not None and (cursor is None or _after(meta["updated_at"], cursor)):
                found[meta["id"]] = meta
        if len(rows) < PAGE:
            break
    return sorted(found.values(), key=lambda m: (m["updated_at"], m["id"]))


def _after(at: str, cursor: str) -> bool:
    return datetime.fromisoformat(at) > datetime.fromisoformat(cursor)


def transcript(transcript_id: str) -> dict[str, Any]:
    """One transcript's content, in memory: `{sentences: [{speaker, text}],
    overview, action_items}`. Never stored, logged or queued."""
    data = _post("transcript", TRANSCRIPT_QUERY, {"id": transcript_id})
    found = data.get("transcript") or {}
    summary = found.get("summary") or {}
    return {
        "sentences": [
            {"speaker": str(s.get("speaker_name") or ""), "text": str(s.get("text") or "")}
            for s in found.get("sentences") or []
            if isinstance(s, dict)
        ],
        "overview": _text(summary.get("overview")),
        "action_items": _text(summary.get("action_items")),
    }


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(v) for v in value if v)
    return str(value or "")
