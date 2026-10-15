"""Deals through the routes — spec 007 FR-CRM-OPP-01 to -03, FR-CRM-PIPE-04,
-05, -07, -09, -10, -12 to -14, FR-CRM-DEL-02 (deal choices), -04,
FR-CRM-AUTO-01 automation 9; contracts §2.5, §2.6; spec §11 AC pipeline
rules 1–5 and the history half of "Every stage change records its time".

Academy has no TVA side, so its deals go through every stage here; a Labs
deal's TVA side is tested in test_crm_tva_lifecycle.py. Routes run against
`tests/crm_fake.py`, whose `crm_deal_transition` keeps 031's re-checks and
whose deal rows keep 030's CHECKs: a route that let a broken deal through
would fail here with `CheckViolation`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from crm_fake import MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.services.crm import deals

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
NEXT = {"next_step": "Send the deck", "next_step_on": "2026-10-20"}


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add_member(academy, SELLER["id"], "member")
    fake.add_member(academy, VIEWER["id"], "viewer")
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    return crm_client(monkeypatch, fake=fake), fake, labs, academy


def _company(http, query=ACADEMY, stage="qualified", name="Acme College"):
    made = http.post(
        f"/api/v1/crm/companies{query}", json={"name": name}, headers=as_(OWNER["id"])
    ).json()
    if stage != "new":
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{query}",
            json={"lead_stage": stage},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
    return made


def _person(http, query=ACADEMY, name="Priya"):
    return http.post(
        f"/api/v1/crm/people{query}", json={"first_name": name}, headers=as_(OWNER["id"])
    ).json()


def _deal(http, company, query=ACADEMY, who=OWNER["id"], **fields):
    body = {"name": "Acme - AI course", "company_id": company["id"], **NEXT, **fields}
    return http.post(f"/api/v1/crm/opportunities{query}", json=body, headers=as_(who))


def _move(http, deal_id, stage, query=ACADEMY, who=OWNER["id"], **body):
    return http.post(
        f"/api/v1/crm/opportunities/{deal_id}/stage{query}",
        json={"stage": stage, **body},
        headers=as_(who),
    )


def _get(http, deal_id, query=ACADEMY, who=OWNER["id"]):
    return http.get(f"/api/v1/crm/opportunities/{deal_id}{query}", headers=as_(who)).json()


def _task(http, deal_id, query=ACADEMY, title="Send brochure"):
    r = http.post(
        f"/api/v1/crm/tasks{query}",
        json={"title": title, "links": [{"record_type": "opportunity", "record_id": deal_id}]},
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _reason(fake, ws, key="price"):
    return next(
        o["id"]
        for o in fake.rows["crm_select_options"].values()
        if o["workspace_id"] == ws["id"]
        and o["list_key"] == "opportunity.lost_reason"
        and o["system_key"] == key
    )


def _to_negotiation(http, deal_id):
    person = _person(http)
    r = _move(
        http,
        deal_id,
        "negotiation",
        changes={
            "point_of_contact_id": person["id"],
            "amount": "500000",
            "currency": "INR",
            "expected_close_on": "2026-12-15",
            "proposal_sent_on": "2026-10-10",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


class TestCreate:
    """FR-CRM-PIPE-04, -07, -10, -11; Q-60."""

    @pytest.mark.parametrize("stage", ["new", "contacted", "nurturing"])
    def test_a_company_below_qualified_takes_no_deal(self, crm, stage):
        http, fake, *_ = crm
        company = _company(http, stage=stage)
        r = _deal(http, company)
        assert r.status_code == 422
        assert r.json()["detail"].startswith("A deal needs its company at Qualified or later;")
        assert fake.rows["crm_opportunities"] == {}

    def test_a_disqualified_company_takes_no_deal(self, crm):
        http, *_ = crm
        r = _deal(http, _company(http, stage="disqualified"))
        assert r.status_code == 422

    def test_creating_one_moves_a_qualified_company_to_deal_open(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        r = _deal(http, company)
        assert r.status_code == 201, r.text
        body = r.json()
        assert (body["stage"], body["stage_label"], body["probability"]) == (
            "discovery",
            "Discovery",
            10,
        )
        assert body["owner_id"] == OWNER["id"] and body["company_name"] == "Acme College"
        assert fake.rows["crm_companies"][company["id"]]["lead_stage"] == "deal_open"

    def test_and_in_one_write(self, crm):
        """The gate and Deal open land together (one crm_apply call)."""
        http, fake, *_ = crm
        company = _company(http)
        before = len(fake.applied)
        _deal(http, company)
        assert len(fake.applied) == before + 1
        tables = [op["table"] for op in fake.applied[-1][1]]
        assert tables == ["crm_opportunities", "crm_companies"]

    @pytest.mark.parametrize("stage", ["customer", "partner"])
    def test_a_customer_or_partner_keeps_its_stage(self, crm, stage):
        http, fake, *_ = crm
        company = _company(http)
        fake.rows["crm_companies"][company["id"]]["lead_stage"] = stage
        assert _deal(http, company).status_code == 201
        assert fake.rows["crm_companies"][company["id"]]["lead_stage"] == stage

    def test_an_open_deal_needs_a_next_step_and_date(self, crm):
        """FR-CRM-PIPE-10, through POST."""
        http, *_ = crm
        company = _company(http)
        r = http.post(
            f"/api/v1/crm/opportunities{ACADEMY}",
            json={"name": "X", "company_id": company["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "Discovery needs next step and next step date."

    def test_required_fields(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/opportunities{ACADEMY}", json={**NEXT}, headers=as_(OWNER["id"])
        )
        assert (r.status_code, r.json()["detail"]) == (422, "A deal needs name and company.")

    def test_the_stage_is_not_chosen_on_create(self, crm):
        http, *_ = crm
        r = _deal(http, _company(http), stage="proposal")
        assert r.status_code == 422
        assert r.json()["detail"] == "A deal's stage and lost reason change through a stage move."

    def test_system_written_fields_are_refused(self, crm):
        http, *_ = crm
        r = _deal(http, _company(http), won_on="2026-10-01")
        assert r.status_code == 422
        assert r.json()["detail"] == "Won on is written by the system and cannot be set by hand."

    def test_entity_outside_labs_is_refused(self, crm):
        http, *_ = crm
        r = _deal(http, _company(http), entity="deepweaver")
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Entity is not used in Nunnari Academy B2B.",
        )

    def test_contacts_with_their_role(self, crm):
        http, fake, _, academy = crm
        person = _person(http)
        role = fake.option(academy, "Champion")
        deal = _deal(
            http, _company(http), contacts=[{"person_id": person["id"], "role_id": role["id"]}]
        ).json()
        page = _get(http, deal["id"])
        assert page["related"]["contacts"][0] | {"id": None} == {
            "id": None,
            "person_id": person["id"],
            "person_name": "Priya",
            "job_title": None,
            "role_id": role["id"],
            "role_label": "Champion",
        }
        assert page["related"]["project"] is None and page["record"]["project"] is None

    def test_the_owner_must_be_a_member(self, crm):
        http, *_ = crm
        r = _deal(http, _company(http), owner_id="u-outsider")
        assert r.json()["detail"] == "The owner must be a member of this workspace."


class TestEdit:
    """Contracts §2.5 PATCH — never the stage; FR-CRM-PIPE-10."""

    def test_the_stage_is_refused_here(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"stage": "qualification"},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "A deal's stage and lost reason change through a stage move.",
        )
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"

    def test_clearing_the_next_step_is_refused(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http)).json()
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"next_step": None},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "Discovery needs next step.")

    def test_a_stale_edit_is_refused(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http)).json()
        http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"name": "Renamed"},
            headers=as_(OWNER["id"]),
        )
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"name": "Again", "expect_updated_at": deal["updated_at"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 409

    def test_a_proposal_deal_keeps_its_amount(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http)).json()
        _to_negotiation(http, deal["id"])
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"amount": None},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "Negotiation needs amount and currency."

    def test_a_cleared_probability_is_refused(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"probability": None},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "Probability is required.")
        assert fake.rows["crm_opportunities"][deal["id"]]["probability"] == 10

    @pytest.mark.parametrize("stage", ["new", "disqualified"])
    def test_an_open_deal_does_not_move_onto_a_company_it_could_not_open_on(self, crm, stage):
        """FR-CRM-PIPE-04, -10 — through an edit as through a create."""
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        other = _company(http, stage=stage, name="Beta College")
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"company_id": other["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert fake.rows["crm_opportunities"][deal["id"]]["company_id"] == deal["company_id"]

    def test_moving_onto_a_qualified_company_opens_it(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        other = _company(http, name="Beta College")
        stamp = fake.rows["crm_companies"][other["id"]]["updated_at"]
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"company_id": other["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_companies"][other["id"]]["lead_stage"] == "deal_open"
        assert fake.applied[-1][1][-1]["expect_updated_at"] == stamp

    def test_a_closed_deal_moves_to_any_company(self, crm):
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        _move(http, deal["id"], "closed_lost", changes={"lost_reason_id": _reason(fake, academy)})
        other = _company(http, stage="new", name="Beta College")
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"company_id": other["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_companies"][other["id"]]["lead_stage"] == "new"


class TestMoves:
    """FR-CRM-PIPE-07 to -09, -14; AC pipeline rules."""

    def test_qualification_needs_a_point_of_contact(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http)).json()
        r = _move(http, deal["id"], "qualification")
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Qualification needs point of contact.",
        )

    def test_proposal_without_its_fields_names_all_three(self, crm):
        http, *_ = crm
        person = _person(http)
        deal = _deal(http, _company(http), point_of_contact_id=person["id"]).json()
        assert _move(http, deal["id"], "qualification").status_code == 200
        r = _move(http, deal["id"], "proposal")
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Proposal needs amount, currency and expected close date.",
        )

    def test_the_preview_says_the_same_and_writes_nothing(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        before = len(fake.applied)
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/stage/preview{ACADEMY}",
            json={"stage": "proposal"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200
        assert r.json() == {
            "allowed": False,
            "missing": ["point_of_contact_id", "amount", "currency", "expected_close_on"],
            "message": (
                "Proposal needs point of contact, amount, currency and expected close date."
            ),
            "would": {
                "probability": 50,
                "company_lead_stage": "deal_open",
                "company_lead_stage_label": "Deal open",
                "tasks_closed": 0,
                "project": None,
            },
        }
        assert len(fake.applied) == before

    def test_every_move_writes_history_with_its_time(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        moved = _to_negotiation(http, deal["id"])["opportunity"]
        history = sorted(
            (
                h
                for h in fake.rows["crm_stage_history"].values()
                if h["opportunity_id"] == deal["id"]
            ),
            key=lambda h: h["at"],
        )
        assert [(h["from_stage"], h["to_stage"]) for h in history] == [
            (None, "discovery"),
            ("discovery", "negotiation"),
        ]
        assert history[-1]["at"] == moved["stage_entered_at"]
        assert history[-1]["by"] == OWNER["id"] and history[-1]["actor_kind"] == "user"

    def test_academy_goes_through_every_stage_with_no_project(self, crm):
        """Journey 4.2's CRM half, in Academy."""
        http, fake, _, academy = crm
        company = _company(http)
        person = _person(http)
        deal = _deal(http, company).json()
        steps = [
            ("qualification", {"point_of_contact_id": person["id"]}),
            (
                "proposal",
                {"amount": "300000", "currency": "INR", "expected_close_on": "2026-12-01"},
            ),
            ("negotiation", {"proposal_sent_on": "2026-10-12"}),
            ("closed_won", {}),
        ]
        for stage, changes in steps:
            r = _move(http, deal["id"], stage, changes=changes)
            assert r.status_code == 200, (stage, r.text)
            assert r.json()["project"] is None
        won = _get(http, deal["id"])["record"]
        assert (won["stage"], won["probability"], won["won_on"]) == (
            "closed_won",
            100,
            "2026-10-14",
        )
        company_row = fake.rows["crm_companies"][company["id"]]
        assert company_row["lead_stage"] == "customer"
        assert company_row["account_type_id"] == fake.option(academy, "Client")["id"]

    def test_the_probability_follows_the_stage_unless_set(self, crm):
        http, *_ = crm
        person = _person(http)
        deal = _deal(http, _company(http), point_of_contact_id=person["id"]).json()
        r = _move(http, deal["id"], "qualification", changes={"probability": 35})
        assert r.json()["opportunity"]["probability"] == 35

    def test_a_null_probability_takes_the_stage_default(self, crm):
        http, *_ = crm
        person = _person(http)
        deal = _deal(http, _company(http), point_of_contact_id=person["id"]).json()
        r = _move(http, deal["id"], "qualification", changes={"probability": None})
        assert r.status_code == 200, r.text
        assert r.json()["opportunity"]["probability"] == 25

    def test_a_stale_move_is_refused_and_writes_nothing(self, crm):
        http, fake, *_ = crm
        person = _person(http)
        deal = _deal(http, _company(http), point_of_contact_id=person["id"]).json()
        http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"name": "Renamed"},
            headers=as_(OWNER["id"]),
        )
        r = _move(http, deal["id"], "qualification", expect_updated_at=deal["updated_at"])
        assert r.status_code == 409
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"

    def test_viewers_move_nothing(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http)).json()
        assert _move(http, deal["id"], "closed_lost", who=VIEWER["id"]).status_code == 403

    def test_the_move_is_audited_as_a_stage_change(self, crm):
        http, fake, *_ = crm
        person = _person(http)
        deal = _deal(http, _company(http), point_of_contact_id=person["id"]).json()
        _move(http, deal["id"], "qualification")
        row = [a for a in fake.audit if a["target_id"] == deal["id"]][-1]
        assert row["action"] == "opportunity.stage_changed"
        timeline = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/timeline{ACADEMY}", headers=as_(OWNER["id"])
        ).json()
        assert timeline[0]["type"] == "stage"
        assert timeline[0]["summary"] == "Moved from Discovery to Qualification"


class TestLost:
    """FR-CRM-PIPE-05, -07, -09; automation 9."""

    def test_lost_without_a_reason_is_refused(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http)).json()
        r = _move(http, deal["id"], "closed_lost")
        assert (r.status_code, r.json()["detail"]) == (422, "Closed Lost needs lost reason.")

    def test_lost_closes_open_tasks_with_a_note(self, crm):
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        open_one = _task(http, deal["id"])
        done_one = _task(http, deal["id"], title="Already done")
        http.post(f"/api/v1/crm/tasks/{done_one['id']}/complete{ACADEMY}", headers=as_(OWNER["id"]))
        r = _move(
            http,
            deal["id"],
            "closed_lost",
            changes={"lost_reason_id": _reason(fake, academy), "lost_notes": "Too dear."},
        )
        assert r.status_code == 200, r.text
        assert r.json()["tasks_closed"] == 1
        task = fake.rows["crm_tasks"][open_one["id"]]
        assert task["status"] == "done" and task["completed_at"]
        page = _get(http, deal["id"])
        assert [n["body"] for n in page["notes"]] == ["Closed: deal lost (Price)."]
        record = page["record"]
        assert (record["stage"], record["lost_on"], record["lost_from_stage"]) == (
            "closed_lost",
            "2026-10-14",
            "discovery",
        )
        assert record["lost_reason_label"] == "Price" and record["probability"] == 0

    def test_the_last_open_deal_moves_the_company_to_nurturing(self, crm):
        http, fake, _, academy = crm
        company = _company(http)
        deal = _deal(http, company).json()
        _move(http, deal["id"], "closed_lost", changes={"lost_reason_id": _reason(fake, academy)})
        assert fake.rows["crm_companies"][company["id"]]["lead_stage"] == "nurturing"

    def test_or_disqualified_when_chosen(self, crm):
        http, fake, _, academy = crm
        company = _company(http)
        deal = _deal(http, company).json()
        r = _move(
            http,
            deal["id"],
            "closed_lost",
            changes={"lost_reason_id": _reason(fake, academy)},
            company_on_loss="disqualified",
        )
        assert r.json()["company"]["lead_stage"] == "disqualified"

    def test_another_open_deal_keeps_the_company_at_deal_open(self, crm):
        http, fake, _, academy = crm
        company = _company(http)
        first = _deal(http, company).json()
        _deal(http, company, name="Acme - second")
        _move(http, first["id"], "closed_lost", changes={"lost_reason_id": _reason(fake, academy)})
        assert fake.rows["crm_companies"][company["id"]]["lead_stage"] == "deal_open"

    def test_a_sibling_closed_meanwhile_refuses_the_move(self, crm, monkeypatch):
        """FR-CRM-PIPE-05, -15 — the plan counted the other deal as open; it
        closed before the write, so the last-open rule would be missed."""
        http, fake, _, academy = crm
        company = _company(http)
        first = _deal(http, company).json()
        second = _deal(http, company, name="Acme - second").json()
        reason = _reason(fake, academy)
        write = deals.store.deal_transition

        def racing(actor, plan):
            fake.rows["crm_opportunities"][second["id"]].update(
                stage="closed_lost", lost_reason_id=reason
            )
            return write(actor, plan)

        monkeypatch.setattr(deals.store, "deal_transition", racing)
        r = _move(http, first["id"], "closed_lost", changes={"lost_reason_id": reason})
        assert (r.status_code, r.json()["detail"]) == (
            409,
            "The company's deals changed meanwhile; reload and try again.",
        )
        assert fake.rows["crm_opportunities"][first["id"]]["stage"] == "discovery"

    def test_a_task_added_meanwhile_refuses_the_move(self, crm, monkeypatch):
        """Automation 9 — every open task on the deal closes with it."""
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        write = deals.store.deal_transition

        def racing(actor, plan):
            monkeypatch.setattr(deals.store, "deal_transition", write)
            _task(http, deal["id"], title="Added meanwhile")
            return write(actor, plan)

        monkeypatch.setattr(deals.store, "deal_transition", racing)
        r = _move(
            http, deal["id"], "closed_lost", changes={"lost_reason_id": _reason(fake, academy)}
        )
        assert (r.status_code, r.json()["detail"]) == (
            409,
            "The deal's tasks changed meanwhile; reload and try again.",
        )
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"


class TestReopen:
    """FR-CRM-PIPE-12, -13 (CRM side)."""

    def _lost(self, http, fake, academy, who=OWNER["id"]):
        deal = _deal(http, _company(http), who=who).json()
        r = _move(
            http,
            deal["id"],
            "closed_lost",
            who=who,
            changes={"lost_reason_id": _reason(fake, academy)},
        )
        assert r.status_code == 200, r.text
        return deal

    def test_the_owner_reopens_to_the_stage_it_was_lost_from(self, crm):
        http, fake, _, academy = crm
        deal = self._lost(http, fake, academy, who=SELLER["id"])
        r = _move(
            http,
            deal["id"],
            "discovery",
            who=SELLER["id"],
            changes={"next_step": "Re-engage", "next_step_on": "2026-11-02"},
        )
        assert r.status_code == 200, r.text
        record = r.json()["opportunity"]
        assert (record["stage"], record["lost_on"], record["lost_reason_id"]) == (
            "discovery",
            None,
            None,
        )

    def test_another_member_may_not(self, crm):
        http, fake, _, academy = crm
        deal = self._lost(http, fake, academy, who=SELLER["id"])
        r = _move(http, deal["id"], "discovery", who=MEMBER["id"], changes=NEXT)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Only a workspace admin or the deal's owner can reopen a lost deal.",
        )

    def test_a_next_step_is_required_in_the_same_save(self, crm):
        http, fake, _, academy = crm
        deal = self._lost(http, fake, academy)
        r = _move(http, deal["id"], "discovery")
        assert r.json()["detail"] == "Discovery needs next step and next step date."

    def test_a_won_deal_is_reopened_by_an_admin_to_negotiation(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http), who=MEMBER["id"]).json()
        _to_negotiation(http, deal["id"])
        assert _move(http, deal["id"], "closed_won").status_code == 200
        r = _move(http, deal["id"], "negotiation", who=MEMBER["id"])
        assert r.json()["detail"] == "Only a workspace admin can reopen a won deal."
        r = _move(http, deal["id"], "negotiation")
        assert r.status_code == 200, r.text
        assert r.json()["opportunity"]["won_on"] is None

    def test_a_nurturing_company_goes_back_to_deal_open(self, crm):
        """FR-CRM-PIPE-04, -12 — the loss moved it to Nurturing."""
        http, fake, _, academy = crm
        deal = self._lost(http, fake, academy)
        assert fake.rows["crm_companies"][deal["company_id"]]["lead_stage"] == "nurturing"
        r = _move(http, deal["id"], "discovery", changes=NEXT)
        assert r.status_code == 200, r.text
        assert r.json()["company"]["lead_stage"] == "deal_open"

    def test_a_disqualified_company_takes_no_reopened_deal(self, crm):
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        _move(
            http,
            deal["id"],
            "closed_lost",
            changes={"lost_reason_id": _reason(fake, academy)},
            company_on_loss="disqualified",
        )
        r = _move(http, deal["id"], "discovery", changes=NEXT)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Acme College is Disqualified; a disqualified company takes no deal.",
        )
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "closed_lost"


class TestLabsClosedWonNeedsTheHandoff:
    """FR-CRM-PIPE-07, FR-CRM-TVA-07 — in Labs a deal is won through the
    handoff, never a plain move (the TVA side is in test_crm_tva_lifecycle)."""

    def test_refused(self, crm):
        http, fake, *_ = crm
        company = _company(http, query=LABS)
        person = _person(http, query=LABS)
        deal = _deal(http, company, query=LABS, point_of_contact_id=person["id"]).json()
        r = _move(
            http,
            deal["id"],
            "closed_won",
            query=LABS,
            changes={
                "amount": "10",
                "currency": "INR",
                "expected_close_on": "2026-12-01",
                "proposal_sent_on": "2026-10-01",
            },
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Closed Won in Nunnari Labs needs the handoff.",
        )
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"

    def test_up_to_qualification_and_lost_work(self, crm):
        http, fake, labs, _ = crm
        company = _company(http, query=LABS)
        person = _person(http, query=LABS)
        deal = _deal(http, company, query=LABS, entity="deepweaver").json()
        r = _move(
            http,
            deal["id"],
            "qualification",
            query=LABS,
            changes={"point_of_contact_id": person["id"]},
        )
        assert r.status_code == 200, r.text
        r = _move(
            http,
            deal["id"],
            "closed_lost",
            query=LABS,
            changes={"lost_reason_id": _reason(fake, labs)},
        )
        assert r.status_code == 200, r.text


class TestContacts:
    def test_added_and_removed(self, crm):
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        person = _person(http)
        role = fake.option(academy, "Decision maker")
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/contacts{ACADEMY}",
            json={"person_id": person["id"], "role_id": role["id"]},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 201, r.text
        assert r.json()["role_label"] == "Decision maker"
        again = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/contacts{ACADEMY}",
            json={"person_id": person["id"]},
            headers=as_(MEMBER["id"]),
        )
        assert (again.status_code, again.json()["detail"]) == (
            409,
            "That person is already a contact on this deal.",
        )
        r = http.delete(
            f"/api/v1/crm/opportunities/{deal['id']}/contacts/{person['id']}{ACADEMY}",
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200
        assert fake.rows["crm_opportunity_contacts"] == {}

    def test_a_role_of_another_list_is_refused(self, crm):
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        person = _person(http)
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/contacts{ACADEMY}",
            json={"person_id": person["id"], "role_id": fake.option(academy, "Price")["id"]},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 422


class TestCompanyDelete:
    """FR-CRM-DEL-02 (deal choices), -04; Q-54."""

    def test_the_deals_choice_is_required(self, crm):
        http, *_ = crm
        company = _company(http)
        _deal(http, company)
        r = http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"people": "unlink"},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Acme College has 1 deal; say whether to move them to another company or delete "
            "them too.",
        )

    def test_move_them_to_another_company(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        other = _company(http, name="Other College")
        deal = _deal(http, company).json()
        r = http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"deals": {"move_to": other["id"]}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_opportunities"][deal["id"]]["company_id"] == other["id"]
        assert fake.rows["crm_companies"][other["id"]]["lead_stage"] == "deal_open"

    @pytest.mark.parametrize("stage", ["new", "disqualified"])
    def test_open_deals_do_not_move_onto_a_company_they_could_not_open_on(self, crm, stage):
        """FR-CRM-PIPE-04."""
        http, fake, *_ = crm
        company = _company(http)
        other = _company(http, stage=stage, name="Other College")
        deal = _deal(http, company).json()
        r = http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"deals": {"move_to": other["id"]}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert fake.rows["crm_opportunities"][deal["id"]]["company_id"] == company["id"]
        assert not fake.rows["crm_companies"][company["id"]].get("deleted_at")

    def test_delete_them_too_and_restore_together(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        deal = _deal(http, company).json()
        r = http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"deals": "delete"},
            headers=as_(OWNER["id"]),
        )
        group = r.json()["deleted_group"]
        assert fake.rows["crm_opportunities"][deal["id"]]["deleted_group"] == group
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/restore{ACADEMY}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_companies"][company["id"]]["deleted_at"] is None

    def test_a_deal_does_not_come_back_without_its_company(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        deal = _deal(http, company).json()
        http.delete(f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={},
            headers=as_(OWNER["id"]),
        )
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/restore{ACADEMY}", headers=as_(OWNER["id"])
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "This deal's company, Acme College, is in the Trash; restore it first.",
        )

    def test_purging_the_company_purges_its_deals(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        deal = _deal(http, company).json()
        http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"deals": "delete"},
            headers=as_(OWNER["id"]),
        )
        r = http.delete(
            f"/api/v1/crm/companies/{company['id']}/permanent{ACADEMY}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert deal["id"] not in fake.rows["crm_opportunities"]
        assert fake.rows["crm_stage_history"] == {}

    def test_a_member_cannot_take_another_sellers_deals(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        fake.rows["crm_companies"][company["id"]]["owner_id"] = MEMBER["id"]
        _deal(http, company, who=SELLER["id"])
        r = http.request(
            "DELETE",
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"deals": "delete"},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 403

    def test_a_member_deletes_their_own_deal(self, crm):
        http, *_ = crm
        deal = _deal(http, _company(http), who=SELLER["id"]).json()
        r = http.delete(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}", headers=as_(MEMBER["id"])
        )
        assert r.status_code == 403
        r = http.delete(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}", headers=as_(SELLER["id"])
        )
        assert r.status_code == 200


class TestCompanyLeadStageFollowsDeals:
    """Contracts §2.5.1, FR-CRM-PIPE-04, -05 — not moved by hand while a
    deal is open."""

    @pytest.mark.parametrize("stage", ["new", "nurturing", "qualified", "disqualified"])
    def test_refused_while_a_deal_is_open(self, crm, stage):
        http, fake, *_ = crm
        company = _company(http)
        _deal(http, company)
        r = http.patch(
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"lead_stage": stage},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Acme College has open deals; its lead stage follows them.",
        )
        assert fake.rows["crm_companies"][company["id"]]["lead_stage"] == "deal_open"

    def test_allowed_once_no_deal_is_open(self, crm):
        http, fake, _, academy = crm
        company = _company(http)
        deal = _deal(http, company).json()
        _move(http, deal["id"], "closed_lost", changes={"lost_reason_id": _reason(fake, academy)})
        r = http.patch(
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text


class TestThroughMcp:
    """The same rules through the tools (FR-CRM-MCP-07)."""

    def test_crm_set_next_step(self, crm):
        http, fake, *_ = crm
        deal = _deal(http, _company(http)).json()
        moved = asyncio.run(
            tools.crm_set_next_step(
                Ctx(OWNER["id"]), deal["id"], "Demo", "2026-10-30", workspace="academy"
            )
        )
        assert (moved["next_step"], moved["next_step_on"]) == ("Demo", "2026-10-30")
        with pytest.raises(Exception) as refused:
            asyncio.run(
                tools.crm_set_next_step(
                    Ctx(OWNER["id"]), deal["id"], " ", "2026-10-30", workspace="academy"
                )
            )
        assert str(refused.value) == "422: Discovery needs next step."

    def test_crm_create_refuses_an_open_deal_without_a_next_step(self, crm):
        http, *_ = crm
        company = _company(http)
        with pytest.raises(Exception) as refused:
            asyncio.run(
                tools.crm_create(
                    Ctx(OWNER["id"]),
                    "opportunity",
                    {"name": "X", "company_id": company["id"]},
                    workspace="academy",
                )
            )
        assert str(refused.value) == "422: Discovery needs next step and next step date."

    def test_crm_move_stage_previews_then_moves(self, crm):
        http, fake, _, academy = crm
        deal = _deal(http, _company(http)).json()
        reason = _reason(fake, academy)
        preview = asyncio.run(
            tools.crm_move_stage(
                Ctx(OWNER["id"]),
                deal["id"],
                "closed_lost",
                changes={"lost_reason_id": reason},
                preview=True,
                workspace="academy",
            )
        )
        assert preview["allowed"] is True
        assert preview["would"]["company_lead_stage"] == "nurturing"
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"
        moved = asyncio.run(
            tools.crm_move_stage(
                Ctx(OWNER["id"]),
                deal["id"],
                "closed_lost",
                changes={"lost_reason_id": reason},
                workspace="academy",
            )
        )
        assert moved["opportunity"]["stage"] == "closed_lost"
        actor, _ = fake.applied[-1]
        assert actor["channel"] == "mcp"


class TestImportHoldsTheNextStepRule:
    """FR-CRM-PIPE-10 through import (S8) — spec §11 AC "an open deal without
    a next step is refused … through the web app, import, MCP or API"; the
    fix-up of Q-46 is the one way past it."""

    EXPORT = json.dumps(
        {
            "companies": [{"id": "c1", "name": "Acme College"}],
            "opportunities": [
                {"id": "o1", "name": "Acme bootcamp", "companyId": "c1", "stage": "NEW"}
            ],
        }
    )

    def _dry(self, http):
        r = http.post(
            f"/api/v1/crm/imports/dry-run{ACADEMY}",
            json={"format": "twenty_json", "content": self.EXPORT},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        return r.json()

    def _apply(self, http, run_id, fixup):
        return http.post(
            f"/api/v1/crm/imports/{run_id}/apply{ACADEMY}",
            json={"format": "twenty_json", "content": self.EXPORT, "fixup_next_step": fixup},
            headers=as_(OWNER["id"]),
        )

    def test_refused_without_the_fixup(self, crm):
        http, fake, _, _ = crm
        dry = self._dry(http)
        assert dry["fixup_candidates"] == 1
        r = self._apply(http, dry["run_id"], fixup=False)
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "The import cannot run yet: Acme bootcamp is open with no next step; "
            "add one in the file or choose the fix-up."
        )
        assert fake.rows["crm_opportunities"] == {}

    def test_the_fixup_lets_it_in_with_review_after_import(self, crm, monkeypatch):
        from app.services.crm import importer

        monkeypatch.setattr(importer, "today_in_company_tz", lambda: date(2026, 10, 14))
        http, fake, _, _ = crm
        r = self._apply(http, self._dry(http)["run_id"], fixup=True)
        assert (r.status_code, r.json()["fixups_used"]) == (200, 1)
        [deal] = fake.rows["crm_opportunities"].values()
        assert (deal["next_step"], deal["next_step_on"]) == ("Review after import", "2026-10-14")
