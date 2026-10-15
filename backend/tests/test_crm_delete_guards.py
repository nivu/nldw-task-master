"""Deleting what has a TVA project — spec 007 FR-CRM-DEL-02, -03; Q-32;
spec §11 AC "Deleting a company with a deal linked to a TVA project is
refused, naming the deal."

A deal linked to a TVA project, tentative or confirmed, is never deleted:
it is marked Closed Lost instead. A company with such a deal is refused
whatever was chosen for its people and deals, and nothing is partly deleted.
Purging a company clears its projects' company link (033), as a TVA row.
"""

from __future__ import annotations

import pytest
from crm_fake import OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

LABS = "?workspace=labs"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, _ = two_workspaces(fake)
    fake.add_deal_lists(labs)
    http = crm_client(monkeypatch, fake=fake)
    company = fake.add(
        "crm_companies",
        workspace_id=labs["id"],
        name="Acme Corp",
        lead_stage="deal_open",
        owner_id=OWNER["id"],
        deleted_at=None,
        deleted_group=None,
    )
    fake.add(
        "crm_people",
        workspace_id=labs["id"],
        first_name="Priya",
        last_name=None,
        company_id=company["id"],
        owner_id=OWNER["id"],
        deleted_at=None,
    )

    def deal(name, **values):
        return fake.add(
            "crm_opportunities",
            workspace_id=labs["id"],
            name=name,
            company_id=company["id"],
            stage="proposal",
            probability=50,
            amount=None,
            currency=None,
            amount_in_inr=None,
            fx_rate_used=None,
            twenty_id="tw",
            next_step="Call",
            next_step_on="2026-10-20",
            lost_reason_id=None,
            owner_id=OWNER["id"],
            deleted_at=None,
            deleted_group=None,
            **values,
        )

    return http, fake, labs, company, deal


def _delete(http, kind, record_id, **body):
    return http.request(
        "DELETE",
        f"/api/v1/crm/{kind}/{record_id}{LABS}",
        json=body or None,
        headers=as_(OWNER["id"]),
    )


def _untouched(fake):
    return all(
        not r.get("deleted_at")
        for table in ("crm_companies", "crm_people", "crm_opportunities")
        for r in fake.rows[table].values()
    )


class TestDeal:
    @pytest.mark.parametrize("status", ["tentative", "confirmed"])
    def test_a_deal_with_a_project_is_refused(self, crm, status):
        http, fake, labs, company, deal = crm
        d = deal("Acme - Phase 1")
        fake.add_project("Acme - Phase 1", status=status, source_opportunity_id=d["id"])
        r = _delete(http, "opportunities", d["id"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "This deal has a TVA project; mark it Closed Lost instead.",
        )
        assert _untouched(fake)

    def test_a_deal_without_one_is_deleted(self, crm):
        http, fake, labs, company, deal = crm
        d = deal("Acme - Phase 2")
        assert _delete(http, "opportunities", d["id"]).status_code == 200
        assert fake.rows["crm_opportunities"][d["id"]]["deleted_at"]


class TestCompany:
    @pytest.mark.parametrize(
        "choice",
        [
            {"people": "delete", "deals": "delete"},
            {"people": "unlink", "deals": "delete"},
            {},
        ],
    )
    def test_a_company_with_a_linked_deal_is_refused_naming_it(self, crm, choice):
        http, fake, labs, company, deal = crm
        first = deal("Acme - Phase 1")
        deal("Acme - Phase 2")
        third = deal("Acme - Support")
        fake.add_project("P1", source_opportunity_id=first["id"])
        fake.add_project("P3", status="confirmed", source_opportunity_id=third["id"])
        r = _delete(http, "companies", company["id"], **choice)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Acme Corp cannot be deleted: “Acme - Phase 1” and “Acme - Support” have a TVA "
            "project. Mark the deal Closed Lost instead.",
        )
        assert _untouched(fake)

    def test_a_company_whose_deals_have_no_project_is_deleted(self, crm):
        http, fake, labs, company, deal = crm
        deal("Acme - Phase 2")
        r = _delete(http, "companies", company["id"], people="unlink", deals="delete")
        assert r.status_code == 200, r.text

    def test_purging_a_company_clears_its_projects_link(self, crm):
        http, fake, labs, company, deal = crm
        project = fake.add_project(
            "Old Acme work", client="Acme Corp", crm_company_id=company["id"]
        )
        assert (
            _delete(http, "companies", company["id"], people="unlink", deals="delete").status_code
            == 200
        )
        r = http.delete(
            f"/api/v1/crm/companies/{company['id']}/permanent{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert fake.rows["projects"][project["id"]]["crm_company_id"] is None
        assert fake.rows["projects"][project["id"]]["client"] == "Acme Corp"
        [row] = [a for a in fake.audit if a["target_table"] == "projects"]
        assert (row["actor_label"], row["workspace_id"]) == ("system", None)
