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
    "crm_handoff": (
        "WORKSPACE ADMINS AND MEMBERS, LABS (link mode: WORKSPACE ADMINS WHO ARE TVA\n"
        "MANAGERS OR ADMINS, LABS). CONFIRM FIRST"
    ),
    "crm_list": "ANY MEMBER OF THE WORKSPACE (Admin, Member, Viewer).",
    "crm_today": (
        "ANY MEMBER OF THE WORKSPACE for their own list; another person's: WORKSPACE\nADMINS ONLY."
    ),
    "crm_search": "ANY MEMBER OF THE WORKSPACE (Admin, Member, Viewer).",
    "crm_merge": "WORKSPACE ADMINS AND MEMBERS. CONFIRM FIRST: preview first",
    "crm_import": (
        "WORKSPACE ADMINS ONLY. CONFIRM FIRST, after showing the dry run's counts\nand conflicts."
    ),
}
READS = {"crm_workspaces", "crm_get", "crm_list", "crm_today", "crm_search"}
DESTRUCTIVE = {"crm_move_stage", "crm_handoff", "crm_merge"}


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
            "closed_won is done with crm_handoff",
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


class TestHandoffTool:
    """FR-CRM-MCP-01 (S5: crm_handoff), -02; contracts §3 tool 10."""

    PHASES = {"delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}}

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (
                lambda: crm.crm_handoff(Ctx("u"), D1, workspace="labs"),
                ("GET", f"/crm/opportunities/{D1}/handoff", {"workspace": "labs"}, None),
            ),
            (
                lambda: crm.crm_handoff(
                    Ctx("u"),
                    D1,
                    "confirm",
                    lead_id="u-lead",
                    phases=TestHandoffTool.PHASES,
                    revenue="1500000.00",
                    milestones=[{"name": "Kickoff", "due_on": "2026-11-02", "amount": "500000"}],
                ),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/handoff",
                    {"workspace": None},
                    {
                        "lead_id": "u-lead",
                        "phases": PHASES,
                        "revenue": "1500000.00",
                        "milestones": [
                            {"name": "Kickoff", "due_on": "2026-11-02", "amount": "500000"}
                        ],
                    },
                ),
            ),
            (
                lambda: crm.crm_handoff(Ctx("u"), D1, "link", link_project_id="p-1"),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/link-project",
                    {"workspace": None},
                    {"mode": "link", "project_id": "p-1"},
                ),
            ),
            (
                lambda: crm.crm_move_stage(
                    Ctx("u"), D1, "proposal", project_name="Acme - Phase 2", link_project_id="p-1"
                ),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/stage",
                    {"workspace": None},
                    {
                        "stage": "proposal",
                        "changes": {},
                        "project_name": "Acme - Phase 2",
                        "link_project_id": "p-1",
                    },
                ),
            ),
            (
                # The preview shows the link the move would make.
                lambda: crm.crm_move_stage(
                    Ctx("u"), D1, "proposal", preview=True, link_project_id="p-1"
                ),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/stage/preview",
                    {"workspace": None},
                    {"stage": "proposal", "changes": {}, "link_project_id": "p-1"},
                ),
            ),
            (
                lambda: crm.crm_handoff(
                    Ctx("u"),
                    D1,
                    "confirm",
                    lead_id="u-lead",
                    phases=TestHandoffTool.PHASES,
                    link_project_id="p-1",
                ),
                (
                    "POST",
                    f"/crm/opportunities/{D1}/handoff",
                    {"workspace": None},
                    {"lead_id": "u-lead", "phases": PHASES, "link_project_id": "p-1"},
                ),
            ),
        ],
    )
    def test_each_makes_exactly_one_api_call(self, monkeypatch, call, expected):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        asyncio.run(call())
        assert recorder.calls == [expected]

    @pytest.mark.parametrize(
        "call",
        [
            lambda: crm.crm_handoff(Ctx("u"), D1, "link"),
            lambda: crm.crm_handoff(Ctx("u"), D1, "confirm", lead_id="u-lead"),
            lambda: crm.crm_handoff(Ctx("u"), "../members", "preview"),
        ],
    )
    def test_an_incomplete_call_reaches_no_route(self, monkeypatch, call):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        with pytest.raises(server.ToolError):
            asyncio.run(call())
        assert recorder.calls == []

    def test_amounts_are_described_as_inr(self):
        assert "Revenue and milestone amounts are INR" in _tools()["crm_handoff"].description


class TestListTool:
    """FR-CRM-MCP-01 (S6a: crm_list), -07, -10; contracts §3 tool 11."""

    def test_by_view_name_in_one_call(self, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        asyncio.run(crm.crm_list(Ctx("u"), "opportunity", view="Pipeline", workspace="labs"))
        assert recorder.calls == [
            (
                "POST",
                "/crm/query",
                {"workspace": "labs"},
                {"object": "opportunity", "spec": {"limit": 25}, "view": "Pipeline"},
            )
        ]

    def test_filters_and_sort_in_one_call(self, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        filters = [{"field": "stage", "operator": "is", "value": "proposal"}]
        asyncio.run(
            crm.crm_list(
                Ctx("u"),
                "opportunity",
                filters=filters,
                sort=[{"field": "name", "dir": "asc"}],
                limit=10,
                after="abc",
            )
        )
        assert recorder.calls == [
            (
                "POST",
                "/crm/query",
                {"workspace": None},
                {
                    "object": "opportunity",
                    "spec": {
                        "limit": 10,
                        "filters": {"op": "and", "items": filters},
                        "sorts": [{"field": "name", "dir": "asc"}],
                        "after": "abc",
                    },
                },
            )
        ]

    def test_a_seeded_view_through_the_bridge(self, crm_store):
        labs = next(w for w in crm_store.rows["crm_workspaces"].values() if w["key"] == "labs")
        crm_store.add_deal_lists(labs)
        asyncio.run(crm.crm_create(Ctx(OWNER["id"]), "company", {"name": "Acme"}, workspace="labs"))
        got = asyncio.run(
            crm.crm_list(Ctx(OWNER["id"]), "company", view="companies_all", workspace="labs")
        )
        assert [r["name"] for r in got["rows"]] == ["Acme"]
        assert got["view"]["system_key"] == "companies_all"

    def test_a_read_naming_no_workspace_uses_the_last_used(self, crm_store):
        """D13 — crm_list reads where crm_get would, though it is a POST."""
        got = asyncio.run(crm.crm_list(Ctx(MEMBER["id"]), "company"))
        assert got["rows"] == []

    def test_amounts_are_absent_for_a_viewer(self, crm_store):
        labs = next(w for w in crm_store.rows["crm_workspaces"].values() if w["key"] == "labs")
        crm_store.add_deal_lists(labs)
        crm_store.add(
            "crm_fx_rates", workspace_id=labs["id"], currency="INR", month="2026-10-01", rate=1
        )
        company = asyncio.run(
            crm.crm_create(Ctx(OWNER["id"]), "company", {"name": "Acme"}, workspace="labs")
        )
        crm_store.rows["crm_companies"][company["id"]]["lead_stage"] = "qualified"
        deal = asyncio.run(
            crm.crm_create(
                Ctx(OWNER["id"]),
                "opportunity",
                {
                    "name": "Acme deal",
                    "company_id": company["id"],
                    "amount": "1000",
                    "currency": "INR",
                    "next_step": "Call",
                    "next_step_on": "2026-10-20",
                },
                workspace="labs",
            )
        )
        assert deal["amount"] == "1000.00"
        got = asyncio.run(
            crm.crm_list(Ctx(VIEWER["id"]), "opportunity", view="pipeline", workspace="labs")
        )
        [row] = got["rows"]
        assert "amount" not in row and "amount_in_inr" not in row
        assert "sum_amount_in_inr" not in got["groups"][0]["aggregates"]
        with pytest.raises(Exception) as refused:
            asyncio.run(
                crm.crm_list(
                    Ctx(VIEWER["id"]),
                    "opportunity",
                    sort=[{"field": "amount", "dir": "desc"}],
                    workspace="labs",
                )
            )
        assert "Viewers cannot filter, sort or total by amount." in str(refused.value)


class TestTodaySearchMergeTools:
    """FR-CRM-MCP-01 (S7: crm_today, crm_search, crm_merge), -07, -11;
    contracts §3 tools 12 to 14."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (
                lambda: crm.crm_today(Ctx("u")),
                ("GET", "/crm/today", {"person": None, "workspace": None}, None),
            ),
            (
                lambda: crm.crm_today(Ctx("u"), person_id="u-member", workspace="labs"),
                ("GET", "/crm/today", {"person": "u-member", "workspace": "labs"}, None),
            ),
            (
                lambda: crm.crm_search(Ctx("u"), "acme", workspace="labs"),
                ("GET", "/crm/search", {"q": "acme", "workspace": "labs"}, None),
            ),
            (
                lambda: crm.crm_merge(Ctx("u"), C1, P1, workspace="labs"),
                (
                    "GET",
                    "/crm/companies/merge-preview",
                    {"workspace": "labs", "a": C1, "b": P1},
                    None,
                ),
            ),
            (
                lambda: crm.crm_merge(
                    Ctx("u"), C1, P1, choices={"name": "b"}, preview=False, workspace="labs"
                ),
                (
                    "POST",
                    "/crm/companies/merge",
                    {"workspace": "labs"},
                    {"survivor_id": C1, "merged_id": P1, "choices": {"name": "b"}},
                ),
            ),
            (
                lambda: crm.crm_merge(
                    Ctx("u"),
                    C1,
                    P1,
                    preview=False,
                    survivor_updated_at="2026-10-01T00:00:00+00:00",
                    merged_updated_at="2026-10-02T00:00:00+00:00",
                    workspace="labs",
                ),
                (
                    "POST",
                    "/crm/companies/merge",
                    {"workspace": "labs"},
                    {
                        "survivor_id": C1,
                        "merged_id": P1,
                        "choices": {},
                        "expect": {
                            "survivor_updated_at": "2026-10-01T00:00:00+00:00",
                            "merged_updated_at": "2026-10-02T00:00:00+00:00",
                        },
                    },
                ),
            ),
        ],
    )
    def test_each_makes_exactly_one_api_call(self, monkeypatch, call, expected):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        asyncio.run(call())
        assert recorder.calls == [expected]

    @pytest.mark.parametrize("bad", ["../members", "not-a-uuid"])
    def test_merge_ids_that_are_not_uuids_reach_no_route(self, monkeypatch, bad):
        recorder = Recorder()
        monkeypatch.setattr(crm, "_api", recorder)
        with pytest.raises(server.ToolError):
            asyncio.run(crm.crm_merge(Ctx("u"), bad, C1, preview=False))
        assert recorder.calls == []

    def test_the_merge_description_says_the_trash(self):
        assert "the merged company goes to Trash" in _tools()["crm_merge"].description

    def test_a_viewer_gets_no_amounts_through_search_and_today(self, crm_store):
        """Contracts §3.1 `test_viewer_gets_no_amounts_through_tools`, for the
        S7 tools."""
        labs = next(w for w in crm_store.rows["crm_workspaces"].values() if w["key"] == "labs")
        crm_store.add_deal_lists(labs)
        crm_store.add(
            "crm_fx_rates", workspace_id=labs["id"], currency="INR", month="2026-10-01", rate=1
        )
        company = asyncio.run(
            crm.crm_create(Ctx(OWNER["id"]), "company", {"name": "Acme"}, workspace="labs")
        )
        crm_store.rows["crm_companies"][company["id"]]["lead_stage"] = "qualified"
        asyncio.run(
            crm.crm_create(
                Ctx(OWNER["id"]),
                "opportunity",
                {
                    "name": "Acme renewal",
                    "company_id": company["id"],
                    "amount": "1000",
                    "currency": "INR",
                    "next_step": "Call",
                    "next_step_on": "2026-10-01",
                },
                workspace="labs",
            )
        )
        found = asyncio.run(crm.crm_search(Ctx(VIEWER["id"]), "acme", workspace="labs"))
        [deal] = found["opportunities"]
        assert deal["name"] == "Acme renewal"
        assert "amount" not in deal and "amount_in_inr" not in deal
        admin = asyncio.run(crm.crm_search(Ctx(OWNER["id"]), "acme", workspace="labs"))
        assert admin["opportunities"][0]["amount"] == "1000.00"
        mine = asyncio.run(crm.crm_today(Ctx(VIEWER["id"]), workspace="labs"))
        assert "amount" not in str(mine)
