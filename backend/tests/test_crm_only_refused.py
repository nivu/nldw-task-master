"""A CRM-only account sees only the CRM — spec 007 FR-CRM-CHG-01; AC "A
CRM-only account sees only the CRM"; contracts §1.

Walks `app.routes` — every `APIRoute` and Starlette `Route`, the
`include_in_schema=False` ones and the routes `main._mount_mcp` appends to
`app.router.routes` included — never `app.openapi()`, which leaves those out.
FastAPI holds included routers lazily, so the walk goes through
`fastapi.routing.iter_route_contexts`, which yields each route at its mounted
path.
Each route outside `/api/v1/crm/` is either on the shared allow-list, on the
unauthenticated list, or refused. A route this file cannot classify fails the
test, so a new route has to be placed deliberately.

Every refused route is called as a CRM-only seller through the real
dependency chain (GoTrue and the profile read replaced at the function
boundary) and must answer 403 "This account is for the CRM only." MCP tools
call these same routes in-process, so the tools are covered too.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import Route

from app.api import deps
from app.main import app
from app.services import supabase as db

#: Contracts §1 — exactly these, reachable by a CRM-only account.
SHARED_ALLOW_LIST = {
    ("GET", "/api/v1/me"),
    ("GET", "/api/v1/me/tokens"),
    ("POST", "/api/v1/me/tokens"),
    ("DELETE", "/api/v1/me/tokens/{token_id}"),
    ("GET", "/api/v1/oauth/transaction/{txn}"),
    ("POST", "/api/v1/oauth/approve"),
}

#: Contracts §1 — no session at all. The two that act as a person refuse a
#: CRM-only profile themselves (tests/test_crm_accounts.py,
#: tests/test_crm_only_excluded.py::TestFeed). `/health/jobs` arrives in 1b.
UNAUTHENTICATED = {
    "/health",
    "/health/jobs",
    "/api/v1/slack/interactions",
    "/api/v1/feed/{key}.ics",
    "/mcp",
    # The OAuth authorization server (spec 006 FR-OAUTH), mounted when
    # MCP_PUBLIC_URL is set.
    "/authorize",
    "/token",
    "/register",
    "/revoke",
}
UNAUTHENTICATED_PREFIXES = ("/.well-known/oauth-",)

#: FastAPI's own pages, mounted only when API_DOCS is on (never in production).
DOCS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}

CRM_PREFIX = "/api/v1/crm/"
CRM_ONLY = "This account is for the CRM only."

SELLER = {
    "id": "u-crm",
    "email": "seller@example.com",
    "display_name": "Seller",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "logs_time": False,
    "account_type": "crm_only",
}


def _calls(dependant) -> set:
    """Every dependency callable in a route's tree."""
    found = set()
    for sub in dependant.dependencies:
        found.add(sub.call)
        found |= _calls(sub)
    return found


def _all_routes() -> list[tuple[str, object]]:
    """(mounted path, original route) for everything the app serves."""
    return [(rc.path or "", rc.original_route) for rc in iter_route_contexts(app.routes)]


def _api_routes() -> list[tuple[str, str, APIRoute]]:
    return [
        (method, path, route)
        for path, route in _all_routes()
        if isinstance(route, APIRoute)
        for method in sorted(route.methods)
    ]


def _classify(path: str, route) -> str:
    if path.startswith(CRM_PREFIX):
        return "crm"
    if path in UNAUTHENTICATED or path.startswith(UNAUTHENTICATED_PREFIXES):
        return "unauthenticated"
    if path in DOCS:
        return "docs"
    if isinstance(route, APIRoute):
        calls = _calls(route.dependant)
        if deps.current_user in calls:
            return "refused"
        if deps.any_account in calls:
            return "shared"
    return "unclassified"


class TestClassification:
    def test_every_route_is_classified(self):
        unclassified = [
            path for path, route in _all_routes() if _classify(path, route) == "unclassified"
        ]
        assert unclassified == []

    def test_the_walk_sees_routes_outside_the_schema(self):
        routes = _all_routes()
        paths = {path for path, _ in routes}
        assert {"/mcp", "/api/v1/feed/{key}.ics", "/api/v1/oauth/approve"} <= paths
        assert any(isinstance(r, Route) and not isinstance(r, APIRoute) for _, r in routes)

    def test_the_allow_list_is_exactly_contracts_section_1(self):
        shared = {
            (method, path)
            for method, path, route in _api_routes()
            if _classify(path, route) == "shared"
        }
        assert shared == SHARED_ALLOW_LIST

    def test_unauthenticated_routes_take_no_session(self):
        for _, path, route in _api_routes():
            if _classify(path, route) == "unauthenticated":
                calls = _calls(route.dependant)
                assert deps.any_account not in calls and deps.current_user not in calls, path


@pytest.fixture
def seller(monkeypatch) -> TestClient:
    monkeypatch.setattr(deps, "_verify", lambda token: SELLER["id"])
    monkeypatch.setattr(
        db, "get_profile", lambda uid: dict(SELLER) if uid == SELLER["id"] else None
    )
    return TestClient(app)


REFUSED = [
    (method, path) for method, path, route in _api_routes() if _classify(path, route) == "refused"
]


def test_there_are_refused_routes_to_check():
    assert len(REFUSED) > 50


@pytest.mark.parametrize(("method", "path"), REFUSED, ids=[f"{m} {p}" for m, p in REFUSED])
def test_crm_only_is_refused(seller, method, path):
    url = re.sub(r"\{[^}]+\}", "x", path)
    r = seller.request(method, url, headers={"Authorization": "Bearer a-session"})
    assert r.status_code in (403, 404)
    assert r.json()["detail"] == CRM_ONLY
