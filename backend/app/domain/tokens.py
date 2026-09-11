"""Personal access tokens — spec 004 FR-TOK.

Pure functions over plain values. Generating a token, hashing it, and deciding
whether a stored one is still good. Nothing here reads a clock it was not
handed or a database at all.

The plaintext is shown once and only its hash is stored. A database dump
therefore contains nothing that signs in, and "show me my token again" has
exactly one honest answer: issue a new one.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

PREFIX = "nunp_"
LIFETIME = timedelta(days=90)  # FR-TOK-03, Q-04
DISPLAY_PREFIX_CHARS = 12


def is_token(value: str) -> bool:
    """Does this bearer value look like one of ours rather than a session JWT?"""
    return value.startswith(PREFIX)


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate(now: datetime | None = None) -> tuple[str, dict[str, object]]:
    """A fresh token: the plaintext (show once) and the row to store."""
    issued = now or datetime.now(UTC)
    plaintext = PREFIX + secrets.token_urlsafe(32)
    row = {
        "token_hash": hash_token(plaintext),
        "prefix": plaintext[:DISPLAY_PREFIX_CHARS],
        "expires_at": (issued + LIFETIME).isoformat(),
    }
    return plaintext, row


def refusal(row: dict, now: datetime | None = None) -> str | None:
    """Why this stored token may not be used, or None if it may."""
    if row.get("revoked_at"):
        return "That token has been revoked."
    expires = row.get("expires_at")
    if expires is None:
        return "That token has no expiry and cannot be trusted."
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires)
    if expires <= (now or datetime.now(UTC)):
        return "That token has expired. Issue a new one from the Account page."
    return None
