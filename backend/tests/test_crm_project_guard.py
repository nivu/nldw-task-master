"""TVA's side of a CRM-linked project — spec 007 FR-CRM-CHG-05, -06, -16,
FR-CRM-TVA-02, -15, -17; owner decision D15; spec §11 AC "The Projects page
and `update_project` refuse status and probability changes on a linked
project, and archive changes while it is tentative; a won linked project
archives in TVA as today."

`PATCH /admin/projects/{id}` (and the `update_project` MCP tool, which calls
it) refuses the fields a deal controls. `GET /admin/projects` and
`GET /analytics/projects/{id}` say a project is deal-managed, and name its
company and deal to members of the Labs workspace only, never an amount,
reading the CRM a fixed number of times whatever the number of projects.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import OUTSIDER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import server
from app.services import supabase as db
from app.services.crm import store


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def tva(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, _ = two_workspaces(fake)
    http = crm_client(monkeypatch, fake=fake)
    projects = fake.rows["projects"]
    audit: list[dict] = []

    def update_project(project_id, data):
        projects[project_id].update(data)
        return dict(projects[project_id])

    for name, fn in {
        "list_projects": lambda *, include_archived=False: [
            dict(p) for p in projects.values() if include_archived or not p["is_archived"]
        ],
        "get_project": lambda pid: dict(projects[pid]) if pid in projects else None,
        "update_project": update_project,
        "list_phases": lambda project_id=None: [],
        "has_time_entries": lambda pid: False,
        "list_milestones": lambda project_id=None: [],
        "insert_audit": audit.append,
        "list_time_entries": lambda **k: [],
    }.items():
        monkeypatch.setattr(db, name, fn)
    company = fake.add(
        "crm_companies", workspace_id=labs["id"], name="Acme Corp", lead_stage="customer"
    )
    deal = fake.add(
        "crm_opportunities",
        workspace_id=labs["id"],
        name="Acme - Phase 1",
        company_id=company["id"],
        stage="proposal",
        owner_id=OWNER["id"],
        won_on=None,
        amount="10000",
        amount_in_inr="840000",
        deleted_at=None,
    )
    project = fake.add_project(
        "Acme - Phase 1",
        client="Acme Corp",
        probability=50,
        revenue="840000",
        source_opportunity_id=deal["id"],
        crm_company_id=company["id"],
    )
    return http, fake, project, deal, audit


def _patch(http, project_id, body, who=OWNER["id"]):
    return http.patch(f"/api/v1/admin/projects/{project_id}", json=body, headers=as_(who))


class TestRefusals:
    """FR-CRM-CHG-05, -TVA-15, D15."""

    @pytest.mark.parametrize(
        "change", [{"status": "confirmed"}, {"probability": 80}, {"is_archived": True}]
    )
    def test_a_linked_tentative_project_refuses_what_the_deal_controls(self, tva, change):
        http, fake, project, *_ = tva
        r = _patch(http, project["id"], change)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Managed by CRM deal Acme - Phase 1.",
        )
        assert project["status"] == "tentative" and project["probability"] == 50

    def test_the_deal_name_only_for_a_labs_member(self, tva):
        http, fake, project, *_ = tva
        r = _patch(http, project["id"], {"probability": 80}, who=OUTSIDER["id"])
        assert (r.status_code, r.json()["detail"]) == (422, "Managed by a CRM deal.")

    def test_the_mcp_tool_refuses_too(self, tva):
        http, fake, project, *_ = tva
        with pytest.raises(server.ToolError) as refused:
            asyncio.run(
                server.update_project(Ctx(OWNER["id"]), project["id"], {"status": "confirmed"})
            )
        assert str(refused.value) == "422: Managed by CRM deal Acme - Phase 1."

    def test_the_mcp_tool_names_the_crm_controlled_fields(self):
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        assert "Managed by CRM deal" in tools["update_project"].description

    def test_other_fields_stay_editable(self, tva):
        http, fake, project, *_ = tva
        r = _patch(http, project["id"], {"client": "Acme Corp Pvt Ltd", "revenue": "900000"})
        assert r.status_code == 200, r.text
        assert project["client"] == "Acme Corp Pvt Ltd"

    def test_the_same_value_sent_back_is_not_a_change(self, tva):
        http, fake, project, *_ = tva
        r = _patch(http, project["id"], {"status": "tentative", "probability": 50, "name": "X"})
        assert r.status_code == 200, r.text

    def test_a_won_linked_project_archives_and_unarchives_as_today(self, tva):
        http, fake, project, *_ = tva
        project["status"] = "confirmed"
        assert _patch(http, project["id"], {"is_archived": True}).status_code == 200
        assert _patch(http, project["id"], {"is_archived": False}).status_code == 200
        r = _patch(http, project["id"], {"status": "tentative"})
        assert r.status_code == 422

    def test_an_unlinked_project_is_unaffected(self, tva):
        http, fake, *_ = tva
        plain = fake.add_project("Internal tools", status="tentative")
        r = _patch(http, plain["id"], {"status": "confirmed", "probability": 90})
        assert r.status_code == 200, r.text


class TestWhatTvaShows:
    """FR-CRM-TVA-02, -17."""

    def _listed(self, http, who, project_id):
        rows = http.get("/api/v1/admin/projects", headers=as_(who)).json()
        return next(p for p in rows if p["id"] == project_id)

    def test_a_labs_member_sees_the_company_and_deal(self, tva):
        http, fake, project, deal, _ = tva
        row = self._listed(http, VIEWER["id"], project["id"])
        assert row["crm_managed"] is True
        assert row["crm"]["company"]["name"] == "Acme Corp"
        assert row["crm"]["opportunity"] == {
            "id": deal["id"],
            "name": "Acme - Phase 1",
            "owner_name": "Owner",
            "won_on": None,
        }

    def test_anyone_else_sees_only_that_a_deal_manages_it(self, tva):
        http, fake, project, *_ = tva
        row = self._listed(http, OUTSIDER["id"], project["id"])
        assert row["crm_managed"] is True
        assert "crm" not in row

    def test_the_project_page_alike(self, tva):
        http, fake, project, *_ = tva
        mine = http.get(
            f"/api/v1/analytics/projects/{project['id']}", headers=as_(VIEWER["id"])
        ).json()
        assert mine["project"]["crm_managed"] is True
        assert mine["project"]["crm"]["opportunity"]["name"] == "Acme - Phase 1"
        theirs = http.get(
            f"/api/v1/analytics/projects/{project['id']}", headers=as_(OUTSIDER["id"])
        ).json()
        assert theirs["project"]["crm_managed"] is True
        assert "crm" not in theirs["project"]

    def test_never_an_amount_from_the_crm(self, tva):
        http, fake, project, *_ = tva
        row = self._listed(http, OWNER["id"], project["id"])

        def keys(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    yield k
                    yield from keys(v)

        crm_keys = set(keys(row["crm"]))
        assert not crm_keys & {"amount", "amount_in_inr", "currency", "weighted_amount"}
        # The project's own revenue is TVA's, for a manager, as before.
        assert row["revenue"] == "840000"

    def test_list_projects_reads_the_crm_a_fixed_number_of_times(self, tva, monkeypatch):
        http, fake, project, deal, _ = tva
        calls: list[str] = []
        for name in ("memberships_for_user", "list_records", "get_record"):
            real = getattr(store, name)

            def counted(*a, _real=real, _name=name, **k):
                calls.append(_name)
                return _real(*a, **k)

            monkeypatch.setattr(store, name, counted)

        http.get("/api/v1/admin/projects", headers=as_(VIEWER["id"]))
        few = len(calls)
        for i in range(8):
            fake.add_project(
                f"Acme {i}",
                source_opportunity_id=deal["id"] if i == 0 else f"d-{i}",
                crm_company_id=project["crm_company_id"],
            )
        calls.clear()
        http.get("/api/v1/admin/projects", headers=as_(VIEWER["id"]))
        assert len(calls) == few == 3
