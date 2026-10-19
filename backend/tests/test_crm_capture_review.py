"""Deciding the items capture makes — spec 010 FR-CAP-REV-01, -02, -04
(`deal_signal`), FR-CAP-CHG-04; contracts §1.3, §6.2; plan §5.9, PD-5; spec
§13 S3 line 4 ("a `deal_signal` that would enter Proposal asks for amount
and expected close and, in Labs, creates the tentative TVA project only
then").

A `deal_signal` is approved through the deal routes' own services
(`deals.create`, `deals.move`), so every pipeline rule applies: Proposal's
fields come from Edit and approve, Labs' tentative project is made by the
move, Closed Won in Labs is refused with the handoff's sentence, and a deal
already at or past the stage is refused before anything changes. Items are
made through `review.upsert` as the sync makes them. Routes run on
`tests/crm_fake.py`; no Gmail and no Claude are involved.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from crm_fake import OWNER, PEOPLE, SELLER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import capture
from app.domain.crm import review as review_rules
from app.services.crm import deals
from app.services.crm import review as service

LABS = "?workspace=labs"
NEXT = {"next_step": "Send the deck", "next_step_on": "2026-10-20"}
PROPOSAL = {"amount": "10000", "currency": "USD", "expected_close_on": "2026-12-15"}
SENT_AT = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="USD", month="2026-10-01", rate="84")
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    return crm_client(monkeypatch, PEOPLE, fake=fake), fake, labs


def _company(http, stage="qualified", name="Acme Corp"):
    made = http.post(
        f"/api/v1/crm/companies{LABS}", json={"name": name}, headers=as_(OWNER["id"])
    ).json()
    if stage != "new":
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"lead_stage": stage},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
    return made


def _deal(http, company, name="Acme - Phase 1"):
    person = http.post(
        f"/api/v1/crm/people{LABS}",
        json={"first_name": "Priya", "company_id": company["id"]},
        headers=as_(OWNER["id"]),
    ).json()
    r = http.post(
        f"/api/v1/crm/opportunities{LABS}",
        json={
            "name": name,
            "company_id": company["id"],
            "point_of_contact_id": person["id"],
            "owner_id": OWNER["id"],
            **NEXT,
        },
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _signal(fake, labs, company, deals_, *, stage="proposal", owner=OWNER["id"], **kw):
    """One `deal_signal`, as the sync's deal-document check makes it."""
    item = capture.deal_signal_item(
        mailbox_id="mb-1",
        thread_id=kw.pop("thread_id", "t-sow"),
        message_id="m-sow",
        company_id=company["id"],
        deals=deals_,
        stage=stage,
        kinds=["sow"],
        direction="inbound" if stage == "closed_won" else "outbound",
        message_at=SENT_AT,
        owner_id=owner,
        generated_at=SENT_AT.isoformat(),
    )
    item.update(kw)
    [made] = service.upsert(labs["id"], [item])
    return fake.rows["crm_review_items"][made["id"]]


def _approve(http, item, who=OWNER["id"], **body):
    return http.post(
        f"/api/v1/crm/review/{item['id']}/approve{LABS}",
        json={"version": item.get("revision") or 1, **body},
        headers=as_(who),
    )


def _projects(fake, deal):
    return [
        p for p in fake.rows["projects"].values() if p.get("source_opportunity_id") == deal["id"]
    ]


class TestOwner:
    """FR-CAP-REV-04; PD-5 — the mailbox owner's item."""

    def test_capture_a_deal_signal_is_in_the_mailbox_owners_queue(self, crm):
        http, fake, labs = crm
        company = _company(http)
        deal = _deal(http, company)
        item = _signal(fake, labs, company, [deal], owner=SELLER["id"])
        r = http.get(f"/api/v1/crm/review{LABS}", headers=as_(SELLER["id"]))
        assert r.status_code == 200, r.text
        [shown] = r.json()["items"]
        assert shown["id"] == item["id"]
        assert shown["type"] == "deal_signal" and shown["type_label"] == "Deal signal"
        assert shown["target"]["name"] == "Acme Corp"
        assert [d["id"] for d in shown["deal_choices"]] == [deal["id"]]
        assert shown["suggestion"]["evidence"]["origin"] == "deal_document"

    def test_capture_today_says_what_the_signal_would_do(self, crm):
        http, fake, labs = crm
        company = _company(http)
        _signal(fake, labs, company, [_deal(http, company)])
        [section] = service.today_section(labs["id"], OWNER["id"])
        [row] = section["items"]
        assert row["title"] == "Move to Proposal, proposal sent on 20 Oct"
        assert row["detail"] == "Acme Corp · SOW sent to the client."


class TestDealSignal:
    """FR-CAP-REV-02; spec §13 S3 line 4."""

    def test_capture_proposal_needs_amount_and_close(self, crm):
        http, fake, labs = crm
        company = _company(http)
        deal = _deal(http, company)
        item = _signal(fake, labs, company, [deal])

        r = _approve(http, item)
        assert r.status_code == 422
        assert r.json()["detail"] == "Proposal needs amount, currency and expected close date."
        row = fake.rows["crm_review_items"][item["id"]]
        assert row["status"] == "open" and row["refusal"] == r.json()["detail"]
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"
        assert _projects(fake, deal) == []  # Only once Proposal is entered.

        r = _approve(http, item, edits=PROPOSAL)
        assert r.status_code == 200, r.text
        moved = fake.rows["crm_opportunities"][deal["id"]]
        assert moved["stage"] == "proposal"
        assert str(moved["proposal_sent_on"]) == "2026-10-20"
        assert str(moved["amount"]) in ("10000", "10000.00")
        [project] = _projects(fake, deal)
        assert project["status"] == "tentative"
        row = fake.rows["crm_review_items"][item["id"]]
        assert row["status"] == "approved"
        assert row["result"]["opportunity_id"] == deal["id"]
        assert row["result"]["stage"] == "proposal"
        assert row["result"]["project_id"] == project["id"]
        # FR-CRMAI-REV-05: the move's audit names the approver and the item.
        moves = [
            a
            for a in fake.audit
            if a["target_id"] == deal["id"] and a.get("suggestion_id") == item["id"]
        ]
        assert moves and all(a["actor_id"] == OWNER["id"] for a in moves)

    def test_capture_edits_are_only_the_stage_fields(self, crm):
        http, fake, labs = crm
        company = _company(http)
        item = _signal(fake, labs, company, [_deal(http, company)])
        r = _approve(http, item, edits={**PROPOSAL, "stage": "negotiation"})
        assert r.status_code == 422
        assert r.json()["detail"] == "stage cannot be edited on this suggestion."
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"

    def test_capture_a_deal_at_or_past_the_stage_is_refused(self, crm):
        http, fake, labs = crm
        company = _company(http)
        deal = _deal(http, company)
        item = _signal(fake, labs, company, [deal])
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/stage{LABS}",
            json={"stage": "proposal", "changes": {**PROPOSAL, **NEXT}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        r = _approve(http, item, edits=PROPOSAL)
        assert r.status_code == 422
        assert r.json()["detail"] == "This deal is already at or past that stage."
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"

    def test_capture_closed_won_in_labs_needs_the_handoff(self, crm):
        http, fake, labs = crm
        company = _company(http)
        deal = _deal(http, company)
        item = _signal(fake, labs, company, [deal], stage="closed_won")
        r = _approve(http, item)
        assert r.status_code == 422
        assert r.json()["detail"] == "Closed Won in Nunnari Labs needs the handoff."
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"

    def test_capture_closed_won_outside_labs_takes_the_skipped_fields(self, crm):
        """A Discovery deal moved straight to Closed Won needs the skipped
        stages' fields, which Edit and approve sends (the card's form)."""
        http, fake, _ = crm
        academy = next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == "academy")
        where = "?workspace=academy"
        company = http.post(
            f"/api/v1/crm/companies{where}", json={"name": "Acme Corp"}, headers=as_(OWNER["id"])
        ).json()
        r = http.patch(
            f"/api/v1/crm/companies/{company['id']}{where}",
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        person = http.post(
            f"/api/v1/crm/people{where}",
            json={"first_name": "Priya", "company_id": company["id"]},
            headers=as_(OWNER["id"]),
        ).json()
        r = http.post(
            f"/api/v1/crm/opportunities{where}",
            json={
                "name": "Acme - Phase 1",
                "company_id": company["id"],
                "owner_id": OWNER["id"],
                **NEXT,
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201, r.text
        deal = r.json()
        item = _signal(fake, academy, company, [deal], stage="closed_won")

        def approve(**body):
            return http.post(
                f"/api/v1/crm/review/{item['id']}/approve{where}",
                json={"version": fake.rows["crm_review_items"][item["id"]]["revision"], **body},
                headers=as_(OWNER["id"]),
            )

        r = approve()
        assert r.status_code == 422
        assert r.json()["detail"].startswith("Closed Won needs")
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"
        r = approve(
            edits={
                "amount": "250000",
                "currency": "INR",
                "expected_close_on": "2026-11-30",
                "point_of_contact_id": person["id"],
            }
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "closed_won"
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "approved"

    def test_capture_several_deals_need_a_choice(self, crm):
        http, fake, labs = crm
        company = _company(http)
        first = _deal(http, company, "Acme - Phase 1")
        second = _deal(http, company, "Acme - Phase 2")
        item = _signal(fake, labs, company, [first, second])
        r = _approve(http, item, edits=PROPOSAL)
        assert r.status_code == 422
        assert r.json()["detail"] == review_rules.CHOOSE_DEAL
        r = _approve(http, item, edits=PROPOSAL, deal_id=second["id"])
        assert r.status_code == 200, r.text
        assert fake.rows["crm_opportunities"][second["id"]]["stage"] == "proposal"
        assert fake.rows["crm_opportunities"][first["id"]]["stage"] == "discovery"

    def test_capture_create_on_a_new_company_is_refused_by_the_pipeline(self, crm):
        http, fake, labs = crm
        company = _company(http, stage="new")
        item = _signal(
            fake,
            labs,
            company,
            [],
            stage="discovery",
            thread_id="t-new",
        )
        item["suggestion"] = {**item["suggestion"], "action": "create"}
        r = _approve(http, item, edits=NEXT)
        assert r.status_code == 422
        assert "Qualified" in r.json()["detail"]
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"
        assert [d for d in fake.rows["crm_opportunities"].values()] == []

    def test_capture_create_on_a_qualified_company_opens_a_discovery_deal(self, crm):
        http, fake, labs = crm
        company = _company(http)
        item = _signal(fake, labs, company, [], stage="discovery", thread_id="t-new")
        item["suggestion"] = {**item["suggestion"], "action": "create"}
        r = _approve(http, item)
        assert r.status_code == 422
        assert r.json()["detail"].startswith("Discovery needs")
        r = _approve(http, item, edits=NEXT)
        assert r.status_code == 200, r.text
        [deal] = fake.rows["crm_opportunities"].values()
        assert deal["stage"] == "discovery" and deal["company_id"] == company["id"]
        assert deal["name"] == "Acme Corp" and deal["owner_id"] == OWNER["id"]
        assert fake.rows["crm_review_items"][item["id"]]["result"] == {"opportunity_id": deal["id"]}

    def test_capture_dismissing_a_deal_signal_changes_nothing_else(self, crm):
        http, fake, labs = crm
        company = _company(http)
        deal = _deal(http, company)
        item = _signal(fake, labs, company, [deal])
        r = http.post(f"/api/v1/crm/review/{item['id']}/dismiss{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "dismissed"
        assert fake.rows["crm_opportunities"][deal["id"]]["stage"] == "discovery"
        # FR-CAP-REV-04: not made again for the same document.
        again = capture.deal_signal_item(
            mailbox_id="mb-1",
            thread_id="t-sow",
            message_id="m-sow",
            company_id=company["id"],
            deals=[deal],
            stage="proposal",
            kinds=["sow"],
            direction="outbound",
            message_at=SENT_AT,
            owner_id=OWNER["id"],
            generated_at=SENT_AT.isoformat(),
        )
        assert service.upsert(labs["id"], [again]) == [{"id": None, "created": False}]
