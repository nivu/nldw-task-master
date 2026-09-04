"""Authentication and role guards — FR-AUTH-01, FR-AUTH-04, FR-APPR-05.

The backend holds the service-role key and therefore bypasses RLS entirely.
That makes this module the real authorisation boundary: if a check is missing
here, no database policy will catch it. The policies in 004_rls_policies.sql
guard the browser's own connection and are defence in depth, not a substitute
for what follows.

**How a token is verified.** Supabase now signs access tokens with rotating
asymmetric keys (ES256), so there is no shared secret to verify against. The
token is handed to GoTrue, which is authoritative about both its signature and
its revocation. That is a network hop per request, cached briefly — see
`Settings.AUTH_CACHE_SECONDS`. Deliberately chosen over local JWKS
verification: at tens of users (NFR-07) the hop is free, and asking the auth
server means a deactivated session stops working within seconds rather than
whenever a cached key expires.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Annotated, Any

from fastapi import Depends, Request

from app.api.errors import ProblemDetail
from app.config import settings
from app.domain.approval import Person
from app.services import supabase as db

logger = logging.getLogger("nldw-task-master")

_lock = threading.Lock()
_token_cache: dict[str, tuple[float, str]] = {}


class CurrentUser(Person):
    """The signed-in person, as both an identity and an authorisation subject.

    Subclasses the domain's `Person` so it can be passed straight into
    `can_decide` / `can_view_reason` without the API layer reimplementing those
    rules or the domain importing FastAPI.
    """

    def __init__(self, profile: dict[str, Any]) -> None:
        super().__init__(
            id=profile["id"],
            role=profile["role"],
            lead_id=profile["lead_id"],
            is_active=profile["is_active"],
        )
        self.email: str = profile["email"]
        self.display_name: str = profile["display_name"]
        self.profile = profile

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_lead(self) -> bool:
        return self.role in ("lead", "admin")


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ProblemDetail(401, "Sign in to continue.")
    return token.strip()


def _verify(token: str) -> str:
    """Resolve an access token to a Supabase user id, or refuse."""
    now = time.monotonic()
    with _lock:
        cached = _token_cache.get(token)
        if cached and cached[0] > now:
            return cached[1]

    try:
        from app.services.supabase import supabase

        response = supabase.auth.get_user(token)
    except Exception:
        # An invalid or expired token raises rather than returning None.
        raise ProblemDetail(401, "Your session has expired. Sign in again.") from None

    user = getattr(response, "user", None)
    if user is None or not getattr(user, "id", None):
        raise ProblemDetail(401, "Your session has expired. Sign in again.")

    user_id = str(user.id)
    with _lock:
        _token_cache[token] = (now + settings.AUTH_CACHE_SECONDS, user_id)
        if len(_token_cache) > 512:
            # Unbounded growth is the only real risk here; tokens are short
            # lived and the cache is per-process. Drop everything expired.
            for key, (expires, _) in list(_token_cache.items()):
                if expires <= now:
                    _token_cache.pop(key, None)
    return user_id


def current_user(request: Request) -> CurrentUser:
    """FR-AUTH-01 — every route depends on this.

    A valid token is not enough. The account must also have a profile and still
    be active: FR-AUTH-06 lets an admin deactivate someone without deleting
    their history, and an unexpired token in that person's browser must stop
    working immediately rather than at expiry.
    """
    user_id = _verify(_bearer_token(request))
    profile = db.get_profile(user_id)

    if profile is None:
        # Authenticated against Supabase but unknown to the portal. This is
        # what an account created outside the admin flow looks like, which
        # FR-AUTH-02 forbids — refuse rather than silently provisioning one.
        logger.warning('{"event": "auth_no_profile", "user_id": "%s"}', user_id)
        raise ProblemDetail(403, "This account has not been set up for the portal.")

    if not profile["is_active"]:
        raise ProblemDetail(403, "This account has been deactivated.")

    # Picked up by the request-logging middleware in main.py. Constitution,
    # Observability: every request logs the authenticated user's ID — and only
    # the ID, never their name, email or anything they wrote.
    request.state.user_id = profile["id"]

    return CurrentUser(profile)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require_lead(user: CurrentUserDep) -> CurrentUser:
    """Guards the team view and the approval queue — FR-LEAD, FR-APPR-02."""
    if not user.is_lead:
        raise ProblemDetail(403, "Only a lead can see the team view.")
    return user


def require_admin(user: CurrentUserDep) -> CurrentUser:
    """Guards the admin panel — FR-ADMIN, FR-HOL-07."""
    if not user.is_admin:
        raise ProblemDetail(403, "Only an admin can do that.")
    return user


LeadDep = Annotated[CurrentUser, Depends(require_lead)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]
