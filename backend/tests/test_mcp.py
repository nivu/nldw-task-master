"""The MCP endpoint — spec 004 FR-MCP.

What these protect is not the tools' arithmetic (they have none — every tool
is a route call) but the two properties that make the endpoint safe to hand a
model: every operation is covered, and every reason is withheld.
"""

from __future__ import annotations

import asyncio

from app.main import app
from app.mcp import server

# Routes that deliberately have no tool: the health probe and the Slack
# webhook, which Slack calls and no person performs (spec 004 §2.2).
EXCLUDED = {("GET", "/health"), ("POST", "/api/v1/slack/interactions")}
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
        that has to be re-kept every time the API grows."""
        expected = len(_operations() - EXCLUDED)
        assert len(_tools()) == expected

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


class TestReasonsAreWithheld:
    """FR-MCP-03 — at any depth, without dropping the fact that one exists."""

    def test_a_reason_is_replaced_not_returned(self):
        out = server._scrub({"id": "b1", "reason": "chemotherapy", "category": "sick"})
        assert "chemotherapy" not in str(out)
        assert out["reason"].startswith("(withheld")
        assert out["category"] == "sick"

    def test_an_empty_reason_is_simply_dropped(self):
        assert "reason" not in server._scrub({"reason": None, "x": 1})

    def test_nested_in_lists_and_calendars(self):
        payload = {"weeks": [[{"booking": {"reason": "Dentist"}}, None]]}
        assert "Dentist" not in str(server._scrub(payload))

    def test_notes_are_not_reasons(self):
        """A timesheet note is what somebody worked on; it is returned."""
        assert server._scrub({"note": "Built the export"})["note"] == "Built the export"
