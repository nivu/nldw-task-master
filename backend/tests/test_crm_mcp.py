"""The CRM's MCP tools — spec 007 FR-CRM-MCP-01, -03, -05, -07, -09, -11;
FR-CRM-CHG-07; contracts §3.

Each slice asserts the tools it has added so far; S12 asserts all eighteen.
The tool runs through the real in-process bridge (`server._api`), so the
route's guard, D13 and the channel label apply exactly as in production.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OWNER, FakeCrmStore, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm, server

#: Contracts §3, the tools built so far, with their caller line (FR-CRM-MCP-11).
CALLER_LINES = {
    "crm_workspaces": "ANY MEMBER OF THE WORKSPACE.",
}
READS = {"crm_workspaces"}


def _tools() -> dict:
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


class TestTools:
    def test_the_crm_tools_so_far(self):
        assert {name for name in _tools() if name.startswith("crm_")} == set(CALLER_LINES)

    @pytest.mark.parametrize("name", sorted(CALLER_LINES))
    def test_caller_line(self, name):
        assert _tools()[name].description.startswith(CALLER_LINES[name])

    @pytest.mark.parametrize("name", sorted(CALLER_LINES))
    def test_annotation(self, name):
        hint = _tools()[name].annotations.read_only_hint
        assert hint is (name in READS)
        if not hint:
            assert "CONFIRM FIRST" in _tools()[name].description

    def test_crm_workspaces_takes_no_arguments(self):
        assert set(_tools()["crm_workspaces"].input_schema.get("properties", {})) == set()

    def test_the_module_imports_only_from_the_server(self):
        source = open(crm.__file__).read()
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert imports == [
            "from __future__ import annotations",
            "from app.mcp.server import READ, Context, _api, mcp",
        ]


class TestInstructions:
    """FR-CRM-MCP-09, FR-CRM-CHG-16."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "Nunnari Labs (key labs",
            "Nunnari Academy B2B (key academy)",
            "a viewer reads records but\nreceives no amounts",
            "must name it",
            "CONFIRM FIRST",
            "closed_won",
        ],
    )
    def test_the_crm_paragraph(self, phrase):
        assert phrase in server.INSTRUCTIONS


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm_store(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    two_workspaces(fake)
    crm_client(monkeypatch)
    return fake


class TestThroughTheBridge:
    def test_crm_workspaces_lists_the_callers_workspaces(self, crm_store):
        listed = asyncio.run(crm.crm_workspaces(Ctx(MEMBER["id"])))
        assert [(w["key"], w["role"], w["last_used"]) for w in listed] == [
            ("academy", "member", True),
            ("labs", "member", False),
        ]

    def test_a_write_through_the_bridge_is_labelled_mcp(self, crm_store):
        """Q-22 — the in-process bridge sends the per-process channel key."""
        asyncio.run(
            server._api(
                Ctx(OWNER["id"]),
                "PATCH",
                "/crm/settings/stage_probabilities",
                params={"workspace": "labs"},
                body={
                    "value": {
                        "discovery": 10,
                        "qualification": 25,
                        "proposal": 50,
                        "negotiation": 75,
                        "closed_won": 100,
                        "closed_lost": 0,
                    }
                },
            )
        )
        actor, _ = crm_store.applied[-1]
        assert actor["channel"] == "mcp"

    def test_a_write_naming_no_workspace_lists_them(self, crm_store):
        """FR-CRM-MCP-04 through the bridge, as a tool sees it."""
        with pytest.raises(Exception) as refused:
            asyncio.run(
                server._api(
                    Ctx(OWNER["id"]),
                    "PATCH",
                    "/crm/settings/stage_probabilities",
                    body={"value": {}},
                )
            )
        assert str(refused.value) == (
            "422: Name a workspace: Nunnari Academy B2B (academy), Nunnari Labs (labs)."
        )
