"""OAuth 2.1 authorization server for MCP clients — spec 006 FR-OAUTH.

Implements the SDK's provider protocol on top of the portal's own tables.
Approving a client issues an ordinary personal token (004) tagged with the
client, so it appears in the Account page's list and is revoked there like
any other; a refresh token is kept, hashed, to renew it.

The person authorises in the portal itself: /authorize stores the request as
a transaction and sends them to the web app, which is already signed in with
Google. The web app posts the transaction back with the session bearer
(`/api/v1/oauth/approve`), and only then is a code minted for that person.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config import settings
from app.domain import tokens
from app.services import audit
from app.services import supabase as db

CODE_TTL = 600
TXN_TTL = 900
REFRESH_TTL = timedelta(days=90)
SCOPES = ["portal"]


def _now() -> datetime:
    return datetime.now(UTC)


class PortalOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    # -- clients -------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = db.get_oauth_client(client_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(row["metadata"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        db.insert_oauth_client(
            {
                "client_id": client_info.client_id,
                "client_secret": client_info.client_secret,
                "metadata": client_info.model_dump(mode="json"),
            }
        )

    # -- authorisation ---------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        txn = secrets.token_urlsafe(24)
        db.insert_oauth_transaction(
            {
                "txn": txn,
                "client_id": client.client_id,
                "params": {
                    "state": params.state,
                    "scopes": params.scopes or SCOPES,
                    "code_challenge": params.code_challenge,
                    "redirect_uri": str(params.redirect_uri),
                    "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                    "resource": params.resource,
                    "client_name": client.client_name or client.client_id,
                },
                "expires_at": (_now() + timedelta(seconds=TXN_TTL)).isoformat(),
            }
        )
        # The person decides in the portal (FR-OAUTH-02).
        return f"{settings.FRONTEND_URL.rstrip('/')}/auth/connect?txn={txn}"

    def approve(self, *, txn: str, user_id: str, approved: bool) -> str:
        """Called by the portal with a signed-in session. Returns where to send
        the browser next: the client's redirect with a code, or with an error."""
        row = db.pop_oauth_transaction(txn)
        if row is None or datetime.fromisoformat(row["expires_at"]) < _now():
            raise AuthorizeError(
                error="invalid_request",
                error_description="That connection request has expired. Start again from Claude.",
            )
        params = row["params"]
        if not approved:
            return construct_redirect_uri(
                params["redirect_uri"], error="access_denied", state=params.get("state")
            )
        code = secrets.token_urlsafe(32)
        db.insert_oauth_code(
            {
                "code": code,
                "client_id": row["client_id"],
                "user_id": user_id,
                "params": params,
                "expires_at": (_now() + timedelta(seconds=CODE_TTL)).isoformat(),
            }
        )
        return construct_redirect_uri(params["redirect_uri"], code=code, state=params.get("state"))

    def transaction(self, txn: str) -> dict[str, Any] | None:
        rows = (
            db.supabase.table("oauth_transactions")
            .select("*")
            .eq("txn", txn)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    # -- codes -----------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = db.pop_oauth_code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        p = row["params"]
        return AuthorizationCode(
            code=authorization_code,
            scopes=p.get("scopes") or SCOPES,
            expires_at=datetime.fromisoformat(row["expires_at"]).timestamp(),
            client_id=row["client_id"],
            code_challenge=p["code_challenge"],
            redirect_uri=p["redirect_uri"],
            redirect_uri_provided_explicitly=bool(p.get("redirect_uri_provided_explicitly")),
            resource=p.get("resource"),
            subject=row["user_id"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if authorization_code.expires_at < time.time():
            raise TokenError(error="invalid_grant", error_description="The code has expired.")
        return self._issue(client, authorization_code.subject or "", authorization_code.scopes)

    def _issue(
        self, client: OAuthClientInformationFull, user_id: str, scopes: list[str]
    ) -> OAuthToken:
        plaintext, row = tokens.generate()
        name = f"{client.client_name or 'MCP client'} (OAuth)"
        created = db.insert_token(
            {**row, "user_id": user_id, "name": name, "client_id": client.client_id}
        )
        refresh = secrets.token_urlsafe(32)
        db.insert_oauth_refresh(
            {
                "token_hash": tokens.hash_token(refresh),
                "client_id": client.client_id,
                "user_id": user_id,
                "api_token_id": created["id"],
                "scopes": scopes,
                "expires_at": (_now() + REFRESH_TTL).isoformat(),
            }
        )
        audit.record(
            action="token.created",
            target_table="api_tokens",
            target_id=created["id"],
            actor_id=user_id,
            after={
                "name": name,
                "prefix": row["prefix"],
                "via": "oauth",
                "client_id": client.client_id,
            },
        )
        return OAuthToken(
            access_token=plaintext,
            token_type="Bearer",
            expires_in=int(tokens.LIFETIME.total_seconds()),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    # -- refresh -----------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = db.get_oauth_refresh(tokens.hash_token(refresh_token))
        if row is None or row.get("revoked_at") or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row.get("scopes") or SCOPES,
            expires_at=int(datetime.fromisoformat(row["expires_at"]).timestamp()),
            subject=row["user_id"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        if refresh_token.expires_at and refresh_token.expires_at < time.time():
            raise TokenError(
                error="invalid_grant", error_description="The refresh token has expired."
            )
        digest = tokens.hash_token(refresh_token.token)
        row = db.get_oauth_refresh(digest)
        db.revoke_oauth_refresh(digest, _now().isoformat())
        if row and row.get("api_token_id"):
            db.revoke_token(row["api_token_id"], row["user_id"], _now().isoformat())
        from app.api.deps import forget_personal_tokens

        forget_personal_tokens()
        return self._issue(client, refresh_token.subject or "", scopes or refresh_token.scopes)

    # -- access ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        if not tokens.is_token(token):
            return None
        row = db.get_token_by_hash(tokens.hash_token(token))
        if row is None or tokens.refusal(row):
            return None
        return AccessToken(
            token=token,
            client_id=str(row.get("client_id") or "personal"),
            scopes=SCOPES,
            expires_at=int(datetime.fromisoformat(row["expires_at"]).timestamp()),
            subject=row["user_id"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        now = _now().isoformat()
        if isinstance(token, RefreshToken):
            digest = tokens.hash_token(token.token)
            row = db.get_oauth_refresh(digest)
            db.revoke_oauth_refresh(digest, now)
            if row and row.get("api_token_id"):
                db.revoke_token(row["api_token_id"], row["user_id"], now)
        else:
            row = db.get_token_by_hash(tokens.hash_token(token.token))
            if row:
                db.revoke_token(row["id"], row["user_id"], now)
        from app.api.deps import forget_personal_tokens

        forget_personal_tokens()


provider = PortalOAuthProvider()
