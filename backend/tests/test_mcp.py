"""The MCP endpoint — spec 004 FR-MCP.

What these protect is not the tools' arithmetic (they have none — every tool
is a route call) but the two properties that make the endpoint safe to hand a
model: every operation is covered, and the route's answer reaches the model
unchanged — reasons included, since the routes decide who may read them.
"""

from __future__ import annotations

import asyncio

import httpx

from app.main import app
from app.mcp import server

# Routes that deliberately have no tool: the health probe and the Slack
# webhook, which Slack calls and no person performs (spec 004 §2.2).
EXCLUDED = {("GET", "/health"), ("POST", "/api/v1/slack/interactions")}
# Spec 007 FR-CRM-JOB-03: the CRM job health is for an uptime monitor.
EXCLUDED |= {("GET", "/health/jobs")}
# Token management is session-only (FR-TOK-04) and so has no tool either.
EXCLUDED |= {
    ("GET", "/api/v1/me/tokens"),
    ("POST", "/api/v1/me/tokens"),
    ("DELETE", "/api/v1/me/tokens/{token_id}"),
}
# Spec 006: the calendar feed address is a second long-lived credential, so it
# is session-only like tokens; the OAuth consent routes are the browser's.
EXCLUDED |= {
    ("GET", "/api/v1/me/feed"),
    ("POST", "/api/v1/me/feed/rotate"),
}


def _operations() -> set[tuple[str, str]]:
    ops = set()
    for path, methods in app.openapi()["paths"].items():
        for method in methods:
            ops.add((method.upper(), path))
    return ops


def _tools():
    return asyncio.run(server.mcp.list_tools())


class TestCoverage:
    def test_every_operation_has_a_tool(self):
        """FR-MCP-01 — one tool per API operation. If a route is added and no
        tool follows, this fails, which is the point: 'all APIs' is a promise
        that has to be re-kept every time the API grows.

        Spec 007 FR-CRM-CHG-07 amends FR-MCP-01: CRM routes are covered by the
        `crm_*` tools (tests/test_crm_mcp.py), not one tool per route. Both
        sides are filtered, so TVA tools still equal TVA operations while
        `crm_*` tools exist.

        Spec 008 FR-TASK-CHG-03 amends it again: task routes are covered by
        the `task_*` and `inbox_add` tools (tests/test_task_mcp.py), so both
        are left out too, `/api/v1/tasks` operations by prefix."""
        tva_tools = [
            t
            for t in _tools()
            if not t.name.startswith(("crm_", "task_")) and t.name != "inbox_add"
        ]
        tva_operations = {
            op
            for op in _operations() - EXCLUDED
            if not op[1].startswith(("/api/v1/crm/", "/api/v1/tasks"))
        }
        assert len(tva_tools) == len(tva_operations)

    def test_the_crm_exception_is_the_specs(self):
        """FR-MCP-01 as amended by `007` FR-CRM-CHG-07: the test above leaves
        out every `/api/v1/crm/` operation by prefix, and exactly what the
        amended text exempts."""
        from pathlib import Path

        spec = Path(__file__).resolve().parents[2] / "specs" / "004-mcp" / "spec.md"
        [row] = [line for line in spec.read_text().splitlines() if line.startswith("| FR-MCP-01 |")]
        assert "except CRM routes, which are covered by the `crm_*` tools of `007`" in row
        crm_operations = {op for op in _operations() if op[1].startswith("/api/v1/crm/")}
        assert crm_operations and not crm_operations & EXCLUDED
        assert {t.name for t in _tools() if t.name.startswith("crm_")}

    def test_every_tool_is_described_and_annotated(self):
        for tool in _tools():
            assert tool.description and len(tool.description) > 20, tool.name
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is not None, tool.name

    def test_every_write_tool_asks_for_confirmation(self):
        """FR-MCP-04."""
        for tool in _tools():
            if tool.annotations and not tool.annotations.read_only_hint:
                assert "CONFIRM" in tool.description, tool.name


class TestReasonsPassThrough:
    """FR-MCP-03 (amended 2026-10-05) — the tool returns what the route
    returns. Who may read a reason is the route's rule (`001` NFR-05), tested
    in test_api_permissions.TestLeaveReasons."""

    class Ctx:
        headers = {"authorization": "Bearer nunp_test"}

    @staticmethod
    def _route_answers(monkeypatch, payload):
        class FakeClient:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def request(self, *_, **__):
                return httpx.Response(200, json=payload)

        monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)

    def test_there_is_no_redaction_step(self):
        assert not hasattr(server, "_scrub")

    def test_a_reason_reaches_the_model_unchanged(self, monkeypatch):
        calendar = {"period": "2026-10", "weeks": [[{"booking": {"reason": "Dentist"}}, None]]}
        self._route_answers(monkeypatch, calendar)
        out = asyncio.run(server._api(self.Ctx(), "GET", "/me/calendar"))
        assert out == calendar

    def test_the_team_day_drops_a_reason_the_setting_adds(self, monkeypatch):
        """Q-06 — with lead_view_shows_reason on, the route adds `reason` to
        the roster, but the portal's Team page never shows it, so neither
        does the tool."""
        roster = {
            "date": "2026-12-15",
            "people": [{"user_id": "u-mine", "category": "sick", "reason": "Dentist"}],
        }
        self._route_answers(monkeypatch, roster)
        out = asyncio.run(server.team_day(self.Ctx(), day="2026-12-15"))
        assert out["people"] == [{"user_id": "u-mine", "category": "sick"}]
