"""The CRM's MCP tools — spec 007 FR-CRM-MCP-01, -03, -04, -05, -07, -09,
-10, -11; FR-CRM-CHG-07; contracts §3.

Each slice asserts the tools it has added so far; S12 asserts all eighteen.
The tool runs through the real in-process bridge (`server._api`), so the
route's guard, D13 and the channel label apply exactly as in production.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm, server

#: Contracts §3, the tools built so far, with their caller line (FR-CRM-MCP-11).
CALLER_LINES = {
    "crm_workspaces": "ANY MEMBER OF THE WORKSPACE.",
    "crm_get": "ANY MEMBER OF THE WORKSPACE (Admin, Member, Viewer).",
    "crm_create": "WORKSPACE ADMINS AND MEMBERS. CONFIRM FIRST.",
    "crm_update": "WORKSPACE ADMINS AND MEMBERS. CONFIRM FIRST.",
    "crm_set_next_step": "WORKSPACE ADMINS AND MEMBERS. CONFIRM FIRST.",
    "crm_add_task": "WORKSPACE ADMINS AND MEMBERS. CONFIRM FIRST.",
    "crm_complete_task": (
        "ADMINS AND MEMBERS, OR A VIEWER ON A TASK ASSIGNED TO THEM. CONFIRM FIRST."
    ),
    "crm_add_note": "WORKSPACE ADMINS AND MEMBERS. CONFIRM FIRST.",
    "crm_move_stage": (
        "WORKSPACE ADMINS AND MEMBERS (reopening a won deal: WORKSPACE ADMINS ONLY;\n"
        "reopening a lost deal: ADMINS OR THE DEAL'S OWNER). CONFIRM FIRST."
    ),
}
READS = {"crm_workspaces", "crm_get"}
DESTRUCTIVE = {"crm_move_stage"}


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
        annotations = _tools()[name].annotations
        assert annotations.read_only_hint is (name in READS)
        if not annotations.read_only_hint:
            assert "CONFIRM FIRST" in _tools()[name].description
            assert annotations.destructive_hint is (name in DESTRUCTIVE)

    def test_crm_workspaces_takes_no_arguments(self):
        assert set(_tools()["crm_workspaces"].input_schema.get("properties", {})) == set()

    def test_the_module_imports_only_from_the_server(self):
        source = open(crm.__file__).read()
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert imports == [
            "from __future__ import annotations",
            "import uuid",
            "from typing import Any, Literal",
            "from app.mcp.server import DESTRUCTIVE, READ, WRITE, Context, ToolError, _api, mcp",
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


class Recorder:
    """Stands in for `_api`, to count and inspect a tool's calls."""

    def __init__(self, answer=None) -> None:
        self.calls: list[tuple] = []
        self.answer = answer if answer is not None else {"ok": True}

    async def __call__(self, ctx, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        return self.answer


#: Record ids in the record-tool tests below.
P1 = "00000000-0000-4000-8000-0000000000a1"
C1 = "00000000-0000-4000-8000-0000000000c1"


class TestRecordTools:
    """FR-CRM-MCP-01 (S3: crm_get, crm_create, crm_update), -10; plan §5.10."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (
                lambda: crm.crm_get(Ctx("u"), "person", P1, workspace="labs"),
                ("GET", f"/crm/people/{P1}", {"workspace": "labs"}, None),
            ),
            (
                lambda: crm.crm_create(Ctx("u"), "company", {"name": "Acme"}),
                (
                    "POST",
                    "/crm/companies",
                    {"workspace": None, "check_duplicates": "true"},
                    {"name": "Acme"},
                ),
            ),
            (
                lambda: crm.crm_create(
                    Ctx("u"),
                    "drive_link",
                    {"record_type": "company", "record_id": "c1", "url": "https://x", "label": "L"},
                    workspace="labs",
                ),
                (
                    "POST",
                    "/crm/links",
                    {"workspace": "labs"},
                    {"record_type": "company", "record_id": "c1", "url": "https://x", "label": "L"},
                ),
            ),
            (
                lambda: crm.crm_update(Ctx("u"), "company", C1, {"city": "Pune"}),
                ("PATCH", f"/crm/companies/{C1}", {"workspace": None}, {"city": "Pune"}),
            ),
        ],
    )
    def test_each_makes_exactly_one_api_call(self, monkeypatch, call, expected):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        asyncio.run(call())
        assert recorder.calls == [expected]

    def test_crm_update_passes_the_callers_expect_updated_at_and_fetches_none(self, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        asyncio.run(
            crm.crm_update(
                Ctx("u"),
                "person",
                P1,
                {"city": "Pune"},
                expect_updated_at="2026-10-14T10:00:00+05:30",
                workspace="labs",
            )
        )
        assert recorder.calls == [
            (
                "PATCH",
                f"/crm/people/{P1}",
                {"workspace": "labs"},
                {"city": "Pune", "expect_updated_at": "2026-10-14T10:00:00+05:30"},
            )
        ]

    @pytest.mark.parametrize("bad", ["../members/x", f"{C1}/../../members/x", "c1", ""])
    def test_an_id_that_is_not_a_uuid_reaches_no_route(self, monkeypatch, bad):
        """An id is put into a path: `../members/…` must not walk out of the
        record's route into one the MCP never offers (FR-CRM-MCP-10)."""
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        with pytest.raises(server.ToolError, match="No such company"):
            asyncio.run(crm.crm_update(Ctx("u"), "company", bad, {"role": "admin"}))
        with pytest.raises(server.ToolError, match="No such company"):
            asyncio.run(crm.crm_get(Ctx("u"), "company", bad))
        assert recorder.calls == []

    def test_crm_create_returns_duplicates_before_creating(self, crm_store):
        first = asyncio.run(
            crm.crm_create(Ctx(OWNER["id"]), "company", {"name": "Acme"}, workspace="labs")
        )
        assert first["name"] == "Acme"
        again = asyncio.run(
            crm.crm_create(Ctx(OWNER["id"]), "company", {"name": "Acme Corp"}, workspace="labs")
        )
        assert again["created"] is False
        assert [s["name"] for s in again["duplicates"]["similar"]] == ["Acme"]
        assert [c["name"] for c in crm_store.rows["crm_companies"].values()] == ["Acme"]
        made = asyncio.run(
            crm.crm_create(
                Ctx(OWNER["id"]),
                "company",
                {"name": "Acme Corp"},
                workspace="labs",
                check_duplicates=False,
            )
        )
        assert made["name"] == "Acme Corp"

    def test_a_viewer_reads_through_crm_get(self, crm_store):
        made = asyncio.run(
            crm.crm_create(Ctx(OWNER["id"]), "person", {"first_name": "Deepa"}, workspace="labs")
        )
        got = asyncio.run(crm.crm_get(Ctx(VIEWER["id"]), "person", made["id"], workspace="labs"))
        assert got["record"]["name"] == "Deepa"
        assert got["timeline_head"][0]["channel"] == "mcp"

    def test_a_write_naming_no_workspace_lists_them(self, crm_store):
        """FR-CRM-MCP-04 for crm_create."""
        with pytest.raises(Exception) as refused:
            asyncio.run(crm.crm_create(Ctx(OWNER["id"]), "company", {"name": "Acme"}))
        assert str(refused.value) == (
            "422: Name a workspace: Nunnari Academy B2B (academy), Nunnari Labs (labs)."
        )


#: A deal id for the S4 tool tests.
D1 = "00000000-0000-4000-8000-0000000000d1"
T1 = "00000000-0000-4000-8000-0000000000e1"


class TestDealTools:
    """FR-CRM-MCP-01 (S4: crm_set_next_step, crm_add_task, crm_complete_task,
    crm_add_note, crm_move_stage), -04, -10."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (
                lambda: crm.crm_set_next_step(Ctx("u"), D1, "Demo", "2026-10-30"),
                (
                    "PATCH",
                    f"/crm/opportunities/{D1}",
                    {"workspace": None},
                    {"next_step": "Demo", "next_step_on": "2026-10-30"},
                ),
            ),
            (
                lambda: crm.crm_add_task(
                    Ctx("u"),
                    "Call",
                    [{"record_type": "opportunity", "record_id": D1}],
                    bucket="today",
                    category="Sales",
                    workspace="labs",
                ),
                (
                    "POST",
                    "/crm/tasks",
                    {"workspace": "labs"},
                    {
                        "title": "Call",
                        "links": [{"record_type": "opportunity", "record_id": D1}],
                        "bucket": "today",
                        "category": "Sales",
                    },
                ),
            ),
            (
                lambda: crm.crm_complete_task(Ctx("u"), T1, workspace="labs"),
                ("POST", f"/crm/tasks/{T1}/complete", {"workspace": "labs"}, None),
            ),
            (
                lambda: crm.crm_add_note(
                    Ctx("u"),
                    "Called.",
                    [{"record_type": "person", "record_id": P1}],
                    kind="call",
                    occurred_on="2026-10-14",
                ),
                (
                    "POST",
                    "/crm/notes",
                    {"workspace": None},
                    {
                        "body": "Called.",
                        "links": [{"record_type": "person", "record_id": P1}],
                        "kind": "call",
                        "occurred_on": "2026-10-14",
                    },
                ),
            ),
            (
                lambda: crm.crm_move_stage(Ctx("u"), D1, "qualification", preview=True),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/stage/preview",
                    {"workspace": None},
                    {"stage": "qualification", "changes": {}},
                ),
            ),
            (
                lambda: crm.crm_move_stage(
                    Ctx("u"),
                    D1,
                    "closed_lost",
                    changes={"lost_reason_id": "r"},
                    company_on_loss="disqualified",
                    workspace="academy",
                ),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/stage",
                    {"workspace": "academy"},
                    {
                        "stage": "closed_lost",
                        "changes": {"lost_reason_id": "r"},
                        "company_on_loss": "disqualified",
                    },
                ),
            ),
            (
                lambda: crm.crm_get(Ctx("u"), "opportunity", D1),
                ("GET", f"/crm/opportunities/{D1}", {"workspace": None}, None),
            ),
            (
                lambda: crm.crm_create(Ctx("u"), "note", {"body": "x"}, workspace="labs"),
                (
                    "POST",
                    "/crm/notes",
                    {"workspace": "labs", "check_duplicates": "true"},
                    {"body": "x"},
                ),
            ),
        ],
    )
    def test_each_makes_exactly_one_api_call(self, monkeypatch, call, expected):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        asyncio.run(call())
        assert recorder.calls == [expected]

    def test_crm_move_stage_is_destructive(self):
        assert _tools()["crm_move_stage"].annotations.destructive_hint is True

    @pytest.mark.parametrize("bad", ["../stage", f"{D1}/../../members", ""])
    def test_ids_that_are_not_uuids_reach_no_route(self, monkeypatch, bad):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        for call in (
            lambda: crm.crm_move_stage(Ctx("u"), bad, "discovery"),
            lambda: crm.crm_complete_task(Ctx("u"), bad),
            lambda: crm.crm_set_next_step(Ctx("u"), bad, "x", "2026-10-30"),
        ):
            with pytest.raises(server.ToolError):
                asyncio.run(call())
        assert recorder.calls == []

    def test_a_two_workspace_members_add_task_without_workspace_lists_both(self, crm_store):
        """FR-CRM-MCP-04, contracts §3.1 `test_write_without_workspace_lists_them`."""
        with pytest.raises(Exception) as refused:
            asyncio.run(
                crm.crm_add_task(
                    Ctx(MEMBER["id"]),
                    "Call",
                    [{"record_type": "company", "record_id": C1}],
                )
            )
        assert str(refused.value) == (
            "422: Name a workspace: Nunnari Academy B2B (academy), Nunnari Labs (labs)."
        )
