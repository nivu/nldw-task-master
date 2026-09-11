"""Personal access tokens — spec 004 FR-TOK.

Issued by a person, for themselves, from a signed-in browser session. A token
cannot reach any of these routes (`SessionDep`), which is what stops a leaked
token from renewing itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps import SessionDep, forget_personal_tokens
from app.api.errors import ProblemDetail
from app.config import settings
from app.domain import tokens
from app.schemas import TokenCreate
from app.services import audit
from app.services import supabase as db

router = APIRouter(prefix="/me/tokens", tags=["tokens"])


def _present(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_used_at": row.get("last_used_at"),
        "revoked_at": row.get("revoked_at"),
        "active": tokens.refusal(row) is None,
    }


@router.get("")
def list_my_tokens(user: SessionDep) -> dict:
    """Every token this person has issued, live or not, and where to point a client."""
    return {
        # Spec 004 §6 — returned to a signed-in person, never in the bundle.
        "mcp_url": settings.MCP_PUBLIC_URL or None,
        "tokens": [_present(row) for row in db.list_tokens(user.id)],
    }


@router.post("", status_code=201)
def issue_token(payload: TokenCreate, user: SessionDep) -> dict:
    """FR-TOK-02 — the plaintext is in this response and nowhere else, ever."""
    plaintext, row = tokens.generate()
    created = db.insert_token({**row, "user_id": user.id, "name": payload.name.strip()})
    audit.record(
        action="token.created",
        target_table="api_tokens",
        target_id=created["id"],
        actor_id=user.id,
        after={
            "name": payload.name.strip(),
            "prefix": row["prefix"],
            "expires_at": row["expires_at"],
        },
    )
    return {**_present(created), "token": plaintext}


@router.delete("/{token_id}")
def revoke_my_token(token_id: str, user: SessionDep) -> dict:
    """FR-TOK-03 — takes effect on the next request (the auth cache is short)."""
    row = db.revoke_token(token_id, user.id, datetime.now(UTC).isoformat())
    if row is None:
        raise ProblemDetail(404, "No such live token.")
    forget_personal_tokens()
    audit.record(
        action="token.revoked",
        target_table="api_tokens",
        target_id=token_id,
        actor_id=user.id,
        after={"prefix": row["prefix"]},
    )
    return _present(row)
