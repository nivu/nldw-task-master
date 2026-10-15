"""A Labs deal's TVA project through the routes — spec 007 FR-CRM-TVA-01 to
-18, FR-CRM-FX-05, FR-CRM-PIPE-13, FR-CRM-WS-05; owner decisions D5, D8, D14;
spec §11 AC "TVA integration" 1–8 and 10; contracts §2.6.

Routes run against `tests/crm_fake.py`, whose `crm_deal_transition` and
`crm_link_project` keep 033's re-checks and TVA branches (the real functions
are in tests/db/test_crm_lifecycle_db.py). The cast: the owner (TVA admin,
Labs admin), Sriram (TVA manager, Labs member), Sanjeev (TVA user, Labs
admin), Deepika (TVA user, Labs member), Tamil (CRM-only, Labs member).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from crm_fake import MEMBER, OWNER, PEOPLE, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.services.crm import deals

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
NEXT = {"next_step": "Send the deck", "next_step_on": "2026-10-20"}
PROPOSAL = {"amount": "10000", "currency": "USD", "expected_close_on": "2026-12-15"}
SRIRAM = {
    "id": "u-sriram",
    "email": "sriram@nunnarilabs.com",
    "display_name": "Sriram",
    "role": "manager",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
SANJEEV = {
    "id": "u-sanjeev",
    "email": "sanjeev@nunnarilabs.com",
    "display_name": "Sanjeev",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
PRIYA = {
    "id": "u-priya",
    "email": "priya@nunnarilabs.com",
    "display_name": "Priya",
    "role": "lead",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
REOPEN = {**NEXT, "proposal_sent_on": "2026-10-12"}
DELIVERY = {"delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}}


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add_member(labs, SRIRAM["id"], "member")
    fake.add_member(labs, SANJEEV["id"], "admin")
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="USD", month="2026-10-01", rate="84")
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    people = {**PEOPLE, SRIRAM["id"]: SRIRAM, SANJEEV["id"]: SANJEEV, PRIYA["id"]: PRIYA}
    return crm_client(monkeypatch, people, fake=fake), fake, labs, academy


def _company(http, query=LABS, name="Acme Corp"):
    made = http.post(
        f"/api/v1/crm/companies{query}", json={"name": name}, headers=as_(OWNER["id"])
    ).json()
    r = http.patch(
        f"/api/v1/crm/companies/{made['id']}{query}",
        json={"lead_stage": "qualified"},
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 200, r.text
    return made


def _deal(http, query=LABS, name="Acme - Phase 1", who=OWNER["id"], company=None, **fields):
    company = company or _company(http, query)
    person = http.post(
        f"/api/v1/crm/people{query}", json={"first_name": "Priya"}, headers=as_(OWNER["id"])
    ).json()
    body = {
        "name": name,
        "company_id": company["id"],
        "point_of_contact_id": person["id"],
        "owner_id": who,
        **NEXT,
        **fields,
    }
    r = http.post(f"/api/v1/crm/opportunities{query}", json=body, headers=as_(OWNER["id"]))
    assert r.status_code == 201, r.text
    return r.json()


def _move(http, deal_id, stage, query=LABS, who=OWNER["id"], **body):
    return http.post(
        f"/api/v1/crm/opportunities/{deal_id}/stage{query}",
        json={"stage": stage, **body},
        headers=as_(who),
    )


def _preview(http, deal_id, stage, query=LABS, who=OWNER["id"], **body):
    return http.post(
        f"/api/v1/crm/opportunities/{deal_id}/stage/preview{query}",
        json={"stage": stage, **body},
        headers=as_(who),
    ).json()


def _at_proposal(http, who=MEMBER["id"], **deal_fields):
    deal = _deal(http, who=who, **deal_fields)
    r = _move(http, deal["id"], "proposal", who=who, changes=PROPOSAL)
    assert r.status_code == 200, r.text
    return r.json()


def _handoff(http, deal_id, who=OWNER["id"], query=LABS, **body):
    payload = {"lead_id": PRIYA["id"], "phases": DELIVERY, **body}
    return http.post(
        f"/api/v1/crm/opportunities/{deal_id}/handoff{query}", json=payload, headers=as_(who)
    )


def _projects(fake):
    return list(fake.rows["projects"].values())


def _reason(fake, ws, key="price"):
    return next(
        o["id"]
        for o in fake.rows["crm_select_options"].values()
        if o["workspace_id"] == ws["id"]
        and o["list_key"] == "opportunity.lost_reason"
        and o["system_key"] == key
    )


def _lose(http, fake, labs, deal_id, who=OWNER["id"]):
    r = _move(
        http, deal_id, "closed_lost", who=who, changes={"lost_reason_id": _reason(fake, labs)}
    )
    assert r.status_code == 200, r.text
    return r.json()


class TestProposal:
    """FR-CRM-TVA-04, -06; AC TVA integration 1."""

    def test_proposal_creates_exactly_one_tentative_project(self, crm):
        http, fake, *_ = crm
        moved = _at_proposal(http)
        [project] = _projects(fake)
        assert project["name"] == "Acme - Phase 1"
        assert project["client"] == "Acme Corp"
        assert project["crm_company_id"] == moved["opportunity"]["company_id"]
        assert project["source_opportunity_id"] == moved["opportunity"]["id"]
        assert project["category"] == "client"
        assert (project["status"], project["probability"]) == ("tentative", 50)
        assert project["revenue"] is None
        assert moved["project"]["name"] == "Acme - Phase 1"
        assert moved["opportunity"]["project"]["status"] == "tentative"

    def test_proposal_takes_the_deals_project_category(self, crm):
        http, fake, *_ = crm
        _at_proposal(http, project_category="poc")
        assert _projects(fake)[0]["category"] == "poc"

    def test_a_second_proposal_entry_does_nothing(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _move(http, deal["id"], "negotiation", changes={"proposal_sent_on": "2026-10-12"})
        assert r.status_code == 200, r.text
        r = _move(http, deal["id"], "proposal")
        assert r.status_code == 200, r.text
        assert len(_projects(fake)) == 1

    def test_probability_changes_sync_while_open(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{LABS}",
            json={"probability": 60},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200, r.text
        assert _projects(fake)[0]["probability"] == 60
        # Moved back out of Proposal, the project stays and keeps following.
        r = _move(http, deal["id"], "qualification")
        assert r.status_code == 200, r.text
        [project] = _projects(fake)
        assert (project["status"], project["probability"]) == ("tentative", 25)

    def test_the_preview_says_what_the_project_will_be(self, crm):
        http, fake, *_ = crm
        deal = _deal(http)
        would = _preview(http, deal["id"], "proposal", changes=PROPOSAL)
        assert would["allowed"] is True
        assert would["would"]["project"] == {
            "action": "create",
            "name": "Acme - Phase 1",
            "category": "client",
            "probability": 50,
            "clash": None,
        }
        assert _projects(fake) == []

    def test_a_project_less_imported_deal_moved_into_negotiation_gets_one(self, crm):
        http, fake, labs, _ = crm
        company = _company(http)
        deal = fake.add(
            "crm_opportunities",
            workspace_id=labs["id"],
            name="Imported - Flux",
            company_id=company["id"],
            stage="proposal",
            stage_entered_at="2026-09-01T00:00:00+00:00",
            probability=50,
            owner_id=OWNER["id"],
            twenty_id="tw-1",
            created_at="2026-09-01T00:00:00+00:00",
            created_by=OWNER["id"],
            updated_by=OWNER["id"],
            **{
                k: None
                for k in (
                    "point_of_contact_id",
                    "amount",
                    "currency",
                    "amount_in_inr",
                    "fx_rate_used",
                    "fx_month",
                    "expected_close_on",
                    "project_category",
                    "lost_reason_id",
                    "lost_from_stage",
                    "deleted_at",
                    "engagement_model_id",
                )
            },
            **NEXT,
        )
        r = _move(http, deal["id"], "negotiation", changes={"proposal_sent_on": "2026-10-01"})
        assert r.status_code == 200, r.text
        [project] = _projects(fake)
        assert project["source_opportunity_id"] == deal["id"]
        assert (project["status"], project["probability"]) == ("tentative", 75)


class TestNameClash:
    """FR-CRM-TVA-05, D14; AC TVA integration 2."""

    def test_a_clash_is_409_naming_it_for_a_tva_user(self, crm):
        http, fake, *_ = crm
        fake.add_project("ACME - Phase 1 ")
        deal = _deal(http, who=MEMBER["id"])
        r = _move(http, deal["id"], "proposal", who=MEMBER["id"], changes=PROPOSAL)
        assert (r.status_code, r.json()["detail"]) == (
            409,
            "A project called “ACME - Phase 1 ” already exists. Rename the deal, or ask a "
            "TVA manager to link it.",
        )
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"
        assert len(_projects(fake)) == 1

    def test_a_clash_offers_the_link_to_a_tva_manager(self, crm):
        http, fake, *_ = crm
        clash = fake.add_project("Acme - Phase 1", probability=30)
        deal = _deal(http)
        would = _preview(http, deal["id"], "proposal", changes=PROPOSAL)
        assert would["allowed"] is False
        assert would["message"] == (
            "A project called “Acme - Phase 1” already exists. Rename the deal or link it to "
            "that project."
        )
        assert would["would"]["project"]["clash"] == {
            "project_id": clash["id"],
            "name": "Acme - Phase 1",
            "linkable": True,
        }
        # The preview shows the link before it is made.
        would = _preview(
            http, deal["id"], "proposal", changes=PROPOSAL, link_project_id=clash["id"]
        )
        assert would["allowed"] is True
        assert (would["would"]["project"]["action"], would["would"]["project"]["name"]) == (
            "link",
            "Acme - Phase 1",
        )
        assert fake.rows["projects"][clash["id"]]["source_opportunity_id"] is None
        r = _move(http, deal["id"], "proposal", changes=PROPOSAL, link_project_id=clash["id"])
        assert r.status_code == 200, r.text
        linked = fake.rows["projects"][clash["id"]]
        assert linked["source_opportunity_id"] == deal["id"]
        assert (linked["client"], linked["probability"]) == ("Acme Corp", 50)
        assert len(_projects(fake)) == 1

    def test_a_clash_proceeds_by_renaming_the_project(self, crm):
        http, fake, *_ = crm
        fake.add_project("Acme - Phase 1")
        deal = _deal(http, who=MEMBER["id"])
        r = _move(
            http,
            deal["id"],
            "proposal",
            who=MEMBER["id"],
            changes=PROPOSAL,
            project_name="Acme - Phase 1 (2026)",
        )
        assert r.status_code == 200, r.text
        assert {p["name"] for p in _projects(fake)} == {"Acme - Phase 1", "Acme - Phase 1 (2026)"}

    @pytest.mark.parametrize(
        "shape",
        [
            {"status": "confirmed"},
            {"is_archived": True},
            {"source_opportunity_id": "someone-else"},
        ],
    )
    def test_linking_only_to_an_unlinked_tentative_unarchived_project(self, crm, shape):
        http, fake, *_ = crm
        clash = fake.add_project("Acme - Phase 1", **shape)
        deal = _deal(http)
        would = _preview(http, deal["id"], "proposal", changes=PROPOSAL)
        assert would["would"]["project"]["clash"]["linkable"] is False
        r = _move(http, deal["id"], "proposal", changes=PROPOSAL, link_project_id=clash["id"])
        assert r.status_code == 409
        assert r.json()["detail"].startswith("That project cannot be linked")

    @pytest.mark.parametrize("who", [MEMBER["id"], SELLER["id"]])
    def test_linking_through_a_clash_is_refused_to_anyone_but_a_tva_manager(self, crm, who):
        http, fake, *_ = crm
        clash = fake.add_project("Acme - Phase 1")
        deal = _deal(http, who=who)
        r = _move(
            http, deal["id"], "proposal", who=who, changes=PROPOSAL, link_project_id=clash["id"]
        )
        assert (r.status_code, r.json()["detail"]) == (409, "Ask a TVA manager to link it.")
        assert fake.rows["projects"][clash["id"]]["source_opportunity_id"] is None


class TestClosedWon:
    """FR-CRM-TVA-07 to -10, FR-CRM-FX-05, D8; AC TVA integration 3, 4."""

    def test_won_as_a_tva_user_confirms_without_revenue_and_tasks_the_admins(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(
            http,
            deal["id"],
            who=MEMBER["id"],
            revenue="840000",
            milestones=[{"name": "Kickoff", "due_on": "2026-11-02", "amount": "840000"}],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["revenue_written"] is False
        [project] = _projects(fake)
        assert (project["status"], project["lead_id"], project["revenue"]) == (
            "confirmed",
            PRIYA["id"],
            None,
        )
        assert fake.rows["project_milestones"] == {}
        assert [p["phase"] for p in fake.rows["project_phases"].values()] == ["delivery"]
        # Of the Labs admins (the owner, a TVA admin; Sanjeev, a TVA user),
        # only the TVA admin can set revenue, so only they get the task.
        tasks = {t["title"]: t for t in body["tasks_created"]}
        assert tasks["Confirm revenue and milestones"]["assignee"]["id"] == OWNER["id"]
        assert len([t for t in body["tasks_created"] if t["title"].startswith("Confirm")]) == 1
        assert body["opportunity"]["stage"] == "closed_won"

    def test_won_by_a_tva_user_with_no_manager_admin_tasks_every_labs_admin(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        fake.profiles[OWNER["id"]]["role"] = "user"
        r = _handoff(http, deal["id"], who=MEMBER["id"])
        assert r.status_code == 200, r.text
        got = sorted(
            t["assignee"]["id"]
            for t in r.json()["tasks_created"]
            if t["title"] == "Confirm revenue and milestones"
        )
        assert got == sorted([OWNER["id"], SANJEEV["id"]])

    def test_won_as_a_manager_writes_inr_revenue_and_milestones(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(
            http,
            deal["id"],
            who=SRIRAM["id"],
            revenue="840000.00",
            milestones=[
                {"name": "Kickoff", "due_on": "2026-11-02", "amount": "420000"},
                {"name": "Go-live", "due_on": "2027-01-29", "amount": "420000"},
            ],
        )
        assert r.status_code == 200, r.text
        assert r.json()["revenue_written"] is True
        [project] = _projects(fake)
        assert (project["status"], project["revenue"]) == ("confirmed", "840000.00")
        assert sorted(m["name"] for m in fake.rows["project_milestones"].values()) == [
            "Go-live",
            "Kickoff",
        ]
        stored = fake.rows["crm_opportunities"][deal["id"]]
        assert (stored["won_fx_rate"], stored["won_fx_month"]) == ("84", "2026-10-01")
        assert stored["amount_in_inr"] == "840000.00"
        assert not any(t["title"].startswith("Confirm") for t in r.json()["tasks_created"])

    def test_the_delivery_task_goes_to_the_lead_when_a_member(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(http, deal["id"], lead_id=SRIRAM["id"])
        assert r.status_code == 200, r.text
        [delivery] = [t for t in r.json()["tasks_created"] if t["title"].startswith("Delivery")]
        assert delivery["assignee"]["id"] == SRIRAM["id"]
        task = fake.rows["crm_tasks"][delivery["id"]]
        category = fake.rows["crm_select_options"][task["category_id"]]
        assert category["system_key"] == "delivery_handoff"

    def test_the_delivery_task_goes_to_the_owner_naming_a_non_member_lead(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(http, deal["id"])
        [delivery] = [t for t in r.json()["tasks_created"] if t["title"].startswith("Delivery")]
        assert delivery["assignee"]["id"] == MEMBER["id"]  # the deal's owner
        assert "Priya leads it" in fake.rows["crm_tasks"][delivery["id"]]["body"]

    def test_skipping_to_won_creates_the_project_confirmed(self, crm):
        http, fake, *_ = crm
        deal = _deal(http)
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{LABS}",
            json=PROPOSAL,
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        r = _handoff(http, deal["id"])
        assert r.status_code == 200, r.text
        [project] = _projects(fake)
        assert (project["status"], project["source_opportunity_id"]) == ("confirmed", deal["id"])

    def test_skipping_to_won_with_a_clash_is_refused(self, crm):
        http, fake, *_ = crm
        fake.add_project("Acme - Phase 1", status="confirmed")
        deal = _deal(http, **PROPOSAL)
        r = _handoff(http, deal["id"])
        assert r.status_code == 409
        assert r.json()["detail"].startswith("A project called “Acme - Phase 1” already exists.")
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"

    def test_a_crm_only_lead_is_refused(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(http, deal["id"], lead_id=SELLER["id"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "A CRM-only account cannot lead a project.",
        )
        assert _projects(fake)[0]["status"] == "tentative"

    def test_the_handoff_needs_the_delivery_dates(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(
            http,
            deal["id"],
            phases={"support": {"starts_on": "2027-02-01", "ends_on": "2027-03-31"}},
        )
        assert r.status_code == 422

    def test_the_preview_hides_tva_revenue_from_a_tva_user(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        _projects(fake)[0]["revenue"] = "700000"
        mine = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/handoff{LABS}", headers=as_(MEMBER["id"])
        ).json()
        assert "existing_tva_revenue" not in mine["revenue"]
        assert mine["can_set_revenue"] is False
        assert mine["revenue"]["suggested_inr"] == "840000.00"
        theirs = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/handoff{LABS}", headers=as_(SRIRAM["id"])
        ).json()
        assert theirs["revenue"]["existing_tva_revenue"] == "700000.00"
        assert PRIYA["id"] in {lead["id"] for lead in theirs["leads"]}
        assert SELLER["id"] not in {lead["id"] for lead in theirs["leads"]}

    def test_a_viewer_cannot_open_the_handoff(self, crm):
        http, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        r = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/handoff{LABS}", headers=as_(VIEWER["id"])
        )
        assert r.status_code == 403

    def test_the_handoff_through_mcp(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        out = asyncio.run(
            tools.crm_handoff(
                Ctx(OWNER["id"]),
                deal["id"],
                "confirm",
                lead_id=PRIYA["id"],
                phases=DELIVERY,
                revenue="840000",
                workspace="labs",
            )
        )
        assert out["revenue_written"] is True
        assert _projects(fake)[0]["status"] == "confirmed"


class TestClosedLostAndReopen:
    """FR-CRM-TVA-12 to -14, FR-CRM-PIPE-13, D5; AC TVA integration 5, 6."""

    def test_lost_archives_and_releases_with_reason_and_person(self, crm):
        http, fake, labs, _ = crm
        deal = _at_proposal(http)["opportunity"]
        project = _projects(fake)[0]
        a = fake.add_allocation(project, MEMBER["id"])
        b = fake.add_allocation(project, OWNER["id"])
        moved = _lose(http, fake, labs, deal["id"])
        assert moved["allocations_released"] == 2
        assert project["is_archived"] is True and project["crm_archived_at"]
        for row in (a, b):
            stored = fake.rows["allocations"][row["id"]]
            assert stored["released_by"] == OWNER["id"]
            assert stored["release_reason"] == "Deal lost: Price"
            assert stored["released_by_opportunity_id"] == deal["id"]

    def test_reopen_from_lost_restores_exactly_those_rows(self, crm):
        http, fake, labs, _ = crm
        deal = _at_proposal(http)["opportunity"]
        project = _projects(fake)[0]
        ours = fake.add_allocation(project, MEMBER["id"])
        _lose(http, fake, labs, deal["id"])
        # Released by something else: not this deal's to restore.
        theirs = fake.add_allocation(
            project,
            OWNER["id"],
            released_at="2026-10-01T00:00:00+00:00",
            release_reason="Other",
        )
        # Meanwhile the person was booked elsewhere at 100%: restored anyway.
        other = fake.add_project("Other work", status="confirmed")
        fake.add_allocation(other, MEMBER["id"], percent="100")
        r = _move(http, deal["id"], "proposal", changes={**NEXT})
        assert r.status_code == 200, r.text
        assert r.json()["allocations_restored"] == 1
        assert fake.rows["allocations"][ours["id"]]["released_at"] is None
        assert fake.rows["allocations"][theirs["id"]]["released_at"] is not None
        assert (project["is_archived"], project["crm_archived_at"]) == (False, None)
        assert (project["status"], project["probability"]) == ("tentative", 50)

    def test_a_project_archived_in_tva_first_stays_archived_on_reopen(self, crm):
        http, fake, labs, _ = crm
        deal = _at_proposal(http)["opportunity"]
        project = _projects(fake)[0]
        project["is_archived"] = True  # archived before the loss, not by it
        _lose(http, fake, labs, deal["id"])
        assert project["crm_archived_at"] is None
        _move(http, deal["id"], "proposal", changes={**NEXT})
        assert project["is_archived"] is True

    def _won(self, http, fake):
        deal = _at_proposal(http)["opportunity"]
        r = _handoff(http, deal["id"], who=OWNER["id"], revenue="840000")
        assert r.status_code == 200, r.text
        return deal, _projects(fake)[0]

    def test_reopen_won_refused_with_time_entries(self, crm):
        http, fake, *_ = crm
        deal, project = self._won(http, fake)
        fake.add("time_entries", project_id=project["id"], user_id=MEMBER["id"])
        r = _move(http, deal["id"], "negotiation", changes=REOPEN)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Acme - Phase 1 has time logged against it, so it cannot be made tentative.",
        )
        assert project["status"] == "confirmed"
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "closed_won"

    def test_reopen_won_refused_with_invoiced_milestones(self, crm):
        http, fake, *_ = crm
        deal, project = self._won(http, fake)
        fake.add(
            "project_milestones",
            project_id=project["id"],
            name="Kickoff",
            due_on="2026-11-02",
            amount="1",
            invoiced_on="2026-11-03",
        )
        r = _move(http, deal["id"], "negotiation", changes=REOPEN)
        assert r.status_code == 422
        assert "invoiced milestones" in r.json()["detail"]

    def test_reopen_won_returns_the_project_to_tentative_keeping_revenue(self, crm):
        http, fake, labs, _ = crm
        deal, project = self._won(http, fake)
        r = _move(http, deal["id"], "negotiation", changes=REOPEN)
        assert r.status_code == 200, r.text
        assert (project["status"], project["revenue"], project["probability"]) == (
            "tentative",
            "840000",
            75,
        )
        # A reopened-won deal can be lost like any open deal (FR-CRM-TVA-13).
        _lose(http, fake, labs, deal["id"])
        assert project["is_archived"] is True
        assert project["revenue"] == "840000"


class TestLinkProject:
    """FR-CRM-TVA-18, Q-65; AC TVA integration 8."""

    def _imported(self, fake, labs, company, stage):
        return fake.add(
            "crm_opportunities",
            workspace_id=labs["id"],
            name="Imported - ARB",
            company_id=company["id"],
            stage=stage,
            stage_entered_at="2026-09-01T00:00:00+00:00",
            probability=100 if stage == "closed_won" else 50,
            owner_id=OWNER["id"],
            twenty_id="tw-2",
            project_category=None,
            deleted_at=None,
            created_at="2026-09-01T00:00:00+00:00",
            **({} if stage == "closed_won" else NEXT),
        )

    def test_an_imported_won_deal_links_to_its_confirmed_project_unchanged(self, crm):
        http, fake, labs, _ = crm
        deal = self._imported(fake, labs, _company(http), "closed_won")
        project = fake.add_project("ARB", status="confirmed", revenue="900000", probability=None)
        fake.add(
            "project_milestones",
            project_id=project["id"],
            name="M1",
            due_on="2026-01-01",
            amount="900000",
            invoiced_on=None,
        )
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/link-project{LABS}",
            json={"mode": "link", "project_id": project["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        stored = fake.rows["projects"][project["id"]]
        assert stored["source_opportunity_id"] == deal["id"]
        assert (stored["status"], stored["revenue"], stored["probability"]) == (
            "confirmed",
            "900000",
            None,
        )
        assert len(fake.rows["project_milestones"]) == 1
        assert r.json()["opportunity"]["project"]["name"] == "ARB"

    def test_an_open_imported_deal_links_to_a_tentative_project(self, crm):
        http, fake, labs, _ = crm
        deal = self._imported(fake, labs, _company(http), "negotiation")
        project = fake.add_project("ARB")
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/link-project{LABS}",
            json={"mode": "link", "project_id": project["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert fake.rows["projects"][project["id"]]["probability"] == 50

    def test_an_open_imported_deal_gets_a_project_created(self, crm):
        http, fake, labs, _ = crm
        deal = self._imported(fake, labs, _company(http), "proposal")
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/link-project{LABS}",
            json={"mode": "create"},
            headers=as_(SANJEEV["id"]),
        )
        assert r.status_code == 200, r.text
        [project] = _projects(fake)
        assert (project["name"], project["status"]) == ("Imported - ARB", "tentative")

    def test_a_won_deal_cannot_link_a_tentative_project(self, crm):
        http, fake, labs, _ = crm
        deal = self._imported(fake, labs, _company(http), "closed_won")
        project = fake.add_project("ARB")
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/link-project{LABS}",
            json={"mode": "link", "project_id": project["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 409

    @pytest.mark.parametrize(
        ("who", "status"),
        [
            (SANJEEV["id"], 409),  # a Labs admin who is a TVA user
            (MEMBER["id"], 403),  # a Labs member: not an admin
            (SELLER["id"], 403),  # CRM-only
        ],
    )
    def test_link_mode_refused_to_anyone_but_a_tva_manager_admin(self, crm, who, status):
        http, fake, labs, _ = crm
        deal = self._imported(fake, labs, _company(http), "proposal")
        project = fake.add_project("ARB")
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/link-project{LABS}",
            json={"mode": "link", "project_id": project["id"]},
            headers=as_(who),
        )
        assert r.status_code == status
        if status == 409:
            assert r.json()["detail"] == "Ask a TVA manager to link it."
        assert fake.rows["projects"][project["id"]]["source_opportunity_id"] is None

    def test_link_mode_through_mcp_refused_to_a_tva_user_admin(self, crm):
        http, fake, labs, _ = crm
        deal = self._imported(fake, labs, _company(http), "proposal")
        project = fake.add_project("ARB")
        with pytest.raises(Exception) as refused:
            asyncio.run(
                tools.crm_handoff(
                    Ctx(SANJEEV["id"]),
                    deal["id"],
                    "link",
                    link_project_id=project["id"],
                    workspace="labs",
                )
            )
        assert "Ask a TVA manager to link it." in str(refused.value)

    @pytest.mark.parametrize("who", [SANJEEV["id"], MEMBER["id"], SELLER["id"]])
    def test_the_linkable_list_is_for_tva_managers_only(self, crm, who):
        http, fake, *_ = crm
        fake.add_project("ARB")
        r = http.get(f"/api/v1/crm/projects/linkable{LABS}", headers=as_(who))
        assert r.status_code == 403

    def test_the_linkable_list(self, crm):
        http, fake, *_ = crm
        fake.add_project("ARB", client="ARB Ltd")
        fake.add_project("Old", is_archived=True)
        fake.add_project("Taken", source_opportunity_id="d-x")
        fake.add_project("Won", status="confirmed")
        r = http.get(
            f"/api/v1/crm/projects/linkable{LABS}&status=tentative", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert [(p["name"], p["client"]) for p in r.json()] == [("ARB", "ARB Ltd")]


class TestAcademyAndAudit:
    """FR-CRM-WS-05, FR-CRM-TVA-03, FR-CRM-AUD-04; AC TVA integration 10."""

    def test_academy_never_touches_projects(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, query=ACADEMY)
        for stage, changes in (
            ("proposal", {**PROPOSAL, "currency": "INR"}),
            ("negotiation", {"proposal_sent_on": "2026-10-12"}),
            ("closed_won", {}),
        ):
            r = _move(http, deal["id"], stage, query=ACADEMY, changes=changes)
            assert r.status_code == 200, r.text
            assert r.json()["project"] is None
        assert _projects(fake) == []
        r = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/handoff{ACADEMY}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 422

    def test_tva_rows_carry_the_system_and_the_person(self, crm):
        http, fake, *_ = crm
        _at_proposal(http, who=SELLER["id"])
        [row] = [r for r in fake.audit if r["target_table"] == "projects"]
        assert (row["actor_label"], row["actor_id"], row["workspace_id"]) == (
            "system",
            SELLER["id"],
            None,
        )
        assert row["action"] == "project.created"

    def test_the_handoff_is_on_the_deals_timeline(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        _handoff(http, deal["id"])
        timeline = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/timeline{LABS}&types=handoff",
            headers=as_(VIEWER["id"]),
        ).json()
        assert timeline[0]["summary"].startswith(
            "Handed off to TVA: project “Acme - Phase 1” confirmed"
        )

    def test_every_role_sees_the_project_block_without_amounts(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        _projects(fake)[0]["revenue"] = "123"
        for who in (VIEWER["id"], SELLER["id"]):
            got = http.get(f"/api/v1/crm/opportunities/{deal['id']}{LABS}", headers=as_(who)).json()
            block = got["record"]["project"]
            assert set(block) == {"id", "name", "status", "is_archived", "phases"}
            assert got["related"]["project"] == block
        company = http.get(
            f"/api/v1/crm/companies/{deal['company_id']}{LABS}", headers=as_(VIEWER["id"])
        ).json()
        assert [p["name"] for p in company["related"]["projects"]] == ["Acme - Phase 1"]


class TestHandoffMilestonesLinkAndCompany:
    """Review of S5: the handoff's milestones are the project's full set
    (FR-CRM-TVA-07, -08), a won-without-Proposal deal may adopt a clashing
    project (FR-CRM-TVA-10 with -05), and a deal's project follows it to
    another company (FR-CRM-TVA-01, -02, FR-CRM-CO-02)."""

    def _with_milestones(self, http, fake):
        deal = _at_proposal(http)["opportunity"]
        [project] = _projects(fake)
        for name, due in (("Kickoff", "2026-11-02"), ("Go-live", "2027-01-29")):
            fake.add(
                "project_milestones",
                project_id=project["id"],
                name=name,
                due_on=due,
                amount="420000.00",
                invoiced_on=None,
            )
        return deal, project

    def _prefilled(self, http, deal_id, who=SRIRAM["id"]):
        r = http.get(f"/api/v1/crm/opportunities/{deal_id}/handoff{LABS}", headers=as_(who))
        assert r.status_code == 200, r.text
        return r.json()["milestones"]

    def test_confirming_the_prefilled_milestones_adds_none(self, crm):
        http, fake, *_ = crm
        deal, _ = self._with_milestones(http, fake)
        before = sorted(fake.rows["project_milestones"])
        shown = self._prefilled(http, deal["id"])
        assert len(shown) == 2 and all(m["id"] for m in shown)
        r = _handoff(http, deal["id"], who=SRIRAM["id"], revenue="840000", milestones=shown)
        assert r.status_code == 200, r.text
        assert sorted(fake.rows["project_milestones"]) == before
        assert not any(a["action"].startswith("milestone.") for a in fake.audit)

    def test_a_milestone_left_out_is_removed_and_a_changed_one_updated(self, crm):
        http, fake, *_ = crm
        deal, _ = self._with_milestones(http, fake)
        kickoff, _ = self._prefilled(http, deal["id"])
        r = _handoff(
            http,
            deal["id"],
            who=SRIRAM["id"],
            milestones=[
                {**kickoff, "amount": "500000.00"},
                {"name": "Support", "due_on": "2027-03-01", "amount": "340000"},
            ],
        )
        assert r.status_code == 200, r.text
        got = sorted(
            (m["name"], Decimal(str(m["amount"]))) for m in fake.rows["project_milestones"].values()
        )
        assert got == [("Kickoff", Decimal(500000)), ("Support", Decimal(340000))]
        actions = sorted(a["action"] for a in fake.audit if a["action"].startswith("milestone."))
        assert actions == ["milestone.added", "milestone.removed", "milestone.updated"]

    def test_an_invoiced_milestone_is_never_removed(self, crm):
        http, fake, *_ = crm
        deal, _ = self._with_milestones(http, fake)
        kickoff, golive = self._prefilled(http, deal["id"])
        fake.rows["project_milestones"][golive["id"]]["invoiced_on"] = "2026-10-01"
        r = _handoff(http, deal["id"], who=SRIRAM["id"], milestones=[kickoff])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "“Go-live” is invoiced, so the handoff cannot remove it.",
        )
        assert len(fake.rows["project_milestones"]) == 2
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "proposal"

    def test_a_milestone_of_another_project_is_refused(self, crm):
        http, fake, *_ = crm
        deal, _ = self._with_milestones(http, fake)
        other = fake.add_project("Other work")
        stray = fake.add(
            "project_milestones",
            project_id=other["id"],
            name="Stray",
            due_on="2026-12-01",
            amount="1.00",
            invoiced_on=None,
        )
        r = _handoff(
            http,
            deal["id"],
            who=SRIRAM["id"],
            milestones=[
                {"id": stray["id"], "name": "Stray", "due_on": "2026-12-01", "amount": "1"}
            ],
        )
        assert (r.status_code, r.json()["detail"]) == (404, "No such milestone on this project.")
        assert fake.rows["project_milestones"][stray["id"]]["project_id"] == other["id"]

    def test_milestones_left_unsent_are_kept(self, crm):
        http, fake, *_ = crm
        deal, _ = self._with_milestones(http, fake)
        r = _handoff(http, deal["id"], who=SRIRAM["id"], revenue="840000")
        assert r.status_code == 200, r.text
        assert len(fake.rows["project_milestones"]) == 2

    def test_a_reopened_won_deal_won_again_keeps_one_copy(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        milestones = [{"name": "Kickoff", "due_on": "2026-11-02", "amount": "840000"}]
        r = _handoff(http, deal["id"], who=SRIRAM["id"], revenue="840000", milestones=milestones)
        assert r.status_code == 200, r.text
        r = _move(http, deal["id"], "negotiation", changes=REOPEN)
        assert r.status_code == 200, r.text
        shown = self._prefilled(http, deal["id"])
        r = _handoff(http, deal["id"], who=SRIRAM["id"], milestones=shown)
        assert r.status_code == 200, r.text
        assert len(fake.rows["project_milestones"]) == 1

    def test_a_manager_adopts_a_clashing_project_when_skipping_to_won(self, crm):
        http, fake, *_ = crm
        clash = fake.add_project("Acme - Phase 1", probability=30)
        deal = _deal(http, **PROPOSAL)
        r = _handoff(http, deal["id"], who=SRIRAM["id"])
        assert (r.status_code, r.json()["detail"]) == (
            409,
            "A project called “Acme - Phase 1” already exists. Give the project another name "
            "or link the deal to it.",
        )
        r = _handoff(http, deal["id"], who=SRIRAM["id"], link_project_id=clash["id"])
        assert r.status_code == 200, r.text
        [project] = _projects(fake)
        assert project["id"] == clash["id"]
        assert (project["status"], project["source_opportunity_id"], project["client"]) == (
            "confirmed",
            deal["id"],
            "Acme Corp",
        )
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "closed_won"

    @pytest.mark.parametrize("who", [MEMBER["id"], SELLER["id"]])
    def test_only_a_tva_manager_adopts_at_the_handoff(self, crm, who):
        http, fake, *_ = crm
        clash = fake.add_project("Acme - Phase 1")
        deal = _deal(http, who=who, **PROPOSAL)
        r = _handoff(http, deal["id"], who=who, link_project_id=clash["id"])
        assert (r.status_code, r.json()["detail"]) == (409, "Ask a TVA manager to link it.")
        assert fake.rows["projects"][clash["id"]]["source_opportunity_id"] is None

    def test_a_deal_with_a_project_cannot_adopt_another(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        other = fake.add_project("Other work")
        r = _handoff(http, deal["id"], who=SRIRAM["id"], link_project_id=other["id"])
        assert (r.status_code, r.json()["detail"]) == (409, "This deal already has a TVA project.")

    def test_the_project_follows_its_deal_to_another_company(self, crm):
        http, fake, *_ = crm
        deal = _at_proposal(http)["opportunity"]
        old = deal["company_id"]
        new = _company(http, name="Globex")
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{LABS}",
            json={"company_id": new["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        [project] = _projects(fake)
        assert (project["crm_company_id"], project["client"]) == (new["id"], "Globex")
        moved = [
            a
            for a in fake.audit
            if a["target_table"] == "projects" and (a.get("after") or {}).get("client") == "Globex"
        ]
        assert moved and moved[0]["workspace_id"] is None

        def related(company_id):
            r = http.get(f"/api/v1/crm/companies/{company_id}{LABS}", headers=as_(OWNER["id"]))
            return [p["id"] for p in r.json()["related"]["projects"]]

        assert related(new["id"]) == [project["id"]]
        assert related(old) == []
        # The new company is held by the deal's project; the old one is free.
        r = http.request(
            "DELETE",
            f"/api/v1/crm/companies/{new['id']}{LABS}",
            json={"deals": "delete"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422 and "TVA project" in r.json()["detail"]
        r = http.request("DELETE", f"/api/v1/crm/companies/{old}{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
