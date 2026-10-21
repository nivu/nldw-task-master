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
made through `review.capture_group_upsert` as the sync makes them (spec 010
R1; `review.upsert` before 074). Routes run on
`tests/crm_fake.py`; no Gmail and no Claude are involved.

S3 (tasks T315): FR-CAP-REV-01 to -03 for `new_lead`; plan §5.9, PD-6, PD-8;
spec §13 S3 lines 2 and 3. Approving a `new_lead` creates the company (at
New, then the suggested lead stage), its domain and the person through the
record routes' services, as the approver with the item's id on every audit
row, and queues `crm.capture_thread`; a refusal after the company exists
keeps it on the item for the retry. Dismissing one may add its sender or
domain to the ignore list. The end-to-end line (later mail filed with no
Claude call) runs the discovery job on `FakeGmailThread` and `FakeClaude`.

R1 (tasks T417): FR-CAP-GRP-04, -09 — approving a grouped item makes one
person per sender and files every conversation; a new conversation refuses
a form opened before it; plan R1.2, PD-35.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from crm_ai_fakes import CapturedCelery
from crm_fake import MEMBER, OWNER, PEOPLE, SELLER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from postgrest.exceptions import APIError
from test_crm_capture_job import LEAD, Capture
from test_crm_capture_job import NOW as JOB_NOW

from app.domain.crm import capture
from app.domain.crm import review as review_rules
from app.services.crm import capture as service_capture
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
    made = service.capture_group_upsert(labs["id"], item, item["source_link"]["threads"][0])
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
        made = service.capture_group_upsert(labs["id"], again, again["source_link"]["threads"][0])
        assert (made["id"], made["created"], made["skipped"]) == (None, False, True)


FINDING = {
    "company_name": "Acme Robotics",
    "domain": "acme-robotics.com",
    "domain_from_text": False,
    "person_name": "Priya Shah",
    "person_email": "priya@acme-robotics.com",
    "person_title": "Head of Ops",
    "lead_stage": "contacted",
    "deal_stage": None,
    "reason": "Asked for pricing; you replied.",
    "first_at": "2026-10-01T09:12:00+05:30",
    "latest_at": "2026-10-02T11:40:00+05:30",
    "message_count": 2,
    "owner_replied": True,
    "attachments": [],
    "duplicate_company_id": None,
}


def _lead(fake, labs, *, owner=OWNER["id"], company_id=None, thread="t-lead", **finding):
    """One `new_lead`, as the discovery job makes it."""
    item = capture.to_item(
        {**FINDING, **finding},
        outcome="new_lead",
        confidence="high",
        mailbox_id="mb-1",
        thread_id=thread,
        message_id="m-2",
        owner_id=owner,
        model="claude-sonnet-5-5",
        generated_at=SENT_AT.isoformat(),
        duplicate_company_id=company_id,
    )
    made = service.capture_group_upsert(labs["id"], item, item["source_link"]["threads"][0])
    return fake.rows["crm_review_items"][made["id"]]


def _dismiss(http, item, who=OWNER["id"], **body):
    return http.post(
        f"/api/v1/crm/review/{item['id']}/dismiss{LABS}",
        json=body or None,
        headers=as_(who),
    )


def _of(fake, table, ws):
    return [r for r in fake.rows[table].values() if r["workspace_id"] == ws["id"]]


@pytest.fixture
def lead(crm, monkeypatch):
    http, fake, labs = crm
    celery = CapturedCelery().install(monkeypatch)
    return http, fake, labs, celery


class TestNewLead:
    """FR-CAP-REV-01, -02; plan §5.9; spec §13 S3 line 2."""

    def test_capture_approving_a_lead_creates_the_company_and_the_person(self, lead):
        http, fake, labs, celery = lead
        item = _lead(fake, labs)
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["linked"] is False and result["filed"] == "queued"
        company = fake.rows["crm_companies"][result["company_id"]]
        assert (company["name"], company["lead_stage"], company["owner_id"]) == (
            "Acme Robotics",
            "contacted",
            OWNER["id"],
        )
        [domain] = [d for d in _of(fake, "crm_company_domains", labs)]
        assert (domain["domain"], domain["company_id"]) == ("acme-robotics.com", company["id"])
        person = fake.rows["crm_people"][result["person_id"]]
        assert (person["first_name"], person["last_name"], person["primary_email"]) == (
            "Priya",
            "Shah",
            "priya@acme-robotics.com",
        )
        assert person["job_title"] == "Head of Ops" and person["company_id"] == company["id"]
        [filed] = celery.named("crm.capture_thread")
        assert filed["args"] == [labs["id"], "mb-1", "t-lead"]
        assert filed["options"]["queue"] == "crm_sync"
        stored = fake.rows["crm_review_items"][item["id"]]
        assert stored["status"] == "approved" and stored["result"] == result
        # FR-CRMAI-REV-05: every change is the approver's, with the item's id.
        made = [
            a
            for a in fake.audit
            if a["target_table"] in ("crm_companies", "crm_people", "crm_company_domains")
        ]
        assert {a["action"] for a in made} >= {"company.created", "person.created"}
        assert {(a["actor_id"], a.get("suggestion_id")) for a in made} == {
            (OWNER["id"], item["id"])
        }

    def test_capture_a_never_contacted_lead_stays_new(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs, lead_stage="new", owner_replied=False)
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        assert fake.rows["crm_companies"][r.json()["result"]["company_id"]]["lead_stage"] == "new"

    def test_capture_edits_choose_the_stage_and_the_names(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs)
        r = _approve(
            http,
            item,
            edits={"company_name": "Acme Robotics Pvt Ltd", "lead_stage": "qualified"},
        )
        assert r.status_code == 200, r.text
        company = fake.rows["crm_companies"][r.json()["result"]["company_id"]]
        assert (company["name"], company["lead_stage"]) == ("Acme Robotics Pvt Ltd", "qualified")

    def test_capture_an_unknown_lead_stage_is_refused_and_nothing_made(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs)
        r = _approve(http, item, edits={"lead_stage": "won"})
        assert (r.status_code, r.json()["detail"]) == (422, capture.CHOOSE_LEAD_STAGE)
        assert _of(fake, "crm_companies", labs) == []
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"

    def test_capture_a_linked_lead_creates_only_the_person(self, lead):
        http, fake, labs, celery = lead
        company = _company(http, stage="new", name="Acme Robotics")
        item = _lead(fake, labs, company_id=company["id"])
        assert item["record_id"] == company["id"]
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["company_id"], result["linked"]) == (company["id"], True)
        assert len(_of(fake, "crm_companies", labs)) == 1
        assert fake.rows["crm_people"][result["person_id"]]["company_id"] == company["id"]
        assert len(celery.named("crm.capture_thread")) == 1

    def test_capture_a_person_holding_the_address_is_used(self, lead):
        http, fake, labs, _ = lead
        company = _company(http, stage="new", name="Acme Robotics")
        priya = http.post(
            f"/api/v1/crm/people{LABS}",
            json={
                "first_name": "Priya",
                "primary_email": "priya@acme-robotics.com",
                "company_id": company["id"],
            },
            headers=as_(OWNER["id"]),
        ).json()
        item = _lead(fake, labs, company_id=company["id"])
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["person_id"] == priya["id"]
        assert len(_of(fake, "crm_people", labs)) == 1

    def test_capture_a_retry_after_a_person_failure_reuses_the_company(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs)
        r = _approve(http, item, edits={"first_name": ""})
        assert r.status_code == 422, r.text
        [company] = _of(fake, "crm_companies", labs)
        stored = fake.rows["crm_review_items"][item["id"]]
        assert stored["status"] == "open" and stored["refusal"]
        assert stored["result"] == {"company_id": company["id"]}
        r = _approve(http, stored)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["company_id"] == company["id"]
        assert r.json()["result"]["linked"] is False
        assert len(_of(fake, "crm_companies", labs)) == 1

    def test_capture_a_webmail_lead_retried_makes_one_company(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs, domain="gmail.com", person_email="priya.shah@gmail.com")
        assert item["suggestion"]["domain"] is None
        r = _approve(http, item, edits={"first_name": ""})
        assert r.status_code == 422, r.text
        r = _approve(http, fake.rows["crm_review_items"][item["id"]])
        assert r.status_code == 200, r.text
        companies = _of(fake, "crm_companies", labs)
        assert [c["name"] for c in companies] == ["Acme Robotics"]
        assert _of(fake, "crm_company_domains", labs) == []

    def test_capture_a_fault_after_the_company_keeps_it_for_the_retry(self, lead, monkeypatch):
        # S3-C3: a timeout (not a refusal) once the company exists keeps its
        # id too, so a webmail lead, with no domain to find it by, makes one.
        http, fake, labs, _ = lead
        item = _lead(fake, labs, domain="gmail.com", person_email="priya.shah@gmail.com")
        real = service.base.create

        def person_times_out(approver, kind, values, **kwargs):
            if kind == "person":
                raise APIError({"code": "57014", "message": "canceling statement due to timeout"})
            return real(approver, kind, values, **kwargs)

        monkeypatch.setattr(service.base, "create", person_times_out)
        with pytest.raises(APIError):
            _approve(http, item)
        [company] = _of(fake, "crm_companies", labs)
        stored = fake.rows["crm_review_items"][item["id"]]
        assert (stored["status"], stored["refusal"]) == ("open", review_rules.NOT_MADE)
        assert stored["result"] == {"company_id": company["id"]}
        monkeypatch.setattr(service.base, "create", real)
        r = _approve(http, stored)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["company_id"] == company["id"]
        assert len(_of(fake, "crm_companies", labs)) == 1

    def test_capture_a_lead_in_another_members_queue_is_not_found(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs, owner=SELLER["id"])
        r = _approve(http, item, who=MEMBER["id"])
        assert r.status_code == 404, r.text

    def test_capture_a_lead_shows_with_no_target(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs)
        r = http.get(f"/api/v1/crm/review{LABS}", headers=as_(OWNER["id"]))
        [shown] = r.json()["items"]
        assert shown["id"] == item["id"] and shown["target"] is None
        assert shown["type_label"] == "New lead"
        [section] = service.today_section(labs["id"], OWNER["id"])
        [row] = section["items"]
        assert row["title"] == "New lead: Acme Robotics · Priya Shah"
        assert row["detail"] == "2 emails · Asked for pricing; you replied."


class TestDismissWithIgnore:
    """FR-CAP-REV-03; contracts §1.3; PD-6."""

    @pytest.mark.parametrize(
        ("ignore", "value"),
        [("address", "priya@acme-robotics.com"), ("domain", "acme-robotics.com")],
    )
    def test_capture_dismissing_adds_one_ignore_entry(self, lead, ignore, value):
        http, fake, labs, _ = lead
        item = _lead(fake, labs)
        r = _dismiss(http, item, ignore=ignore)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["item"]["status"] == "dismissed"
        [row] = _of(fake, "crm_capture_ignores", labs)
        assert (row["kind"], row["value"], row["from_item_id"]) == (ignore, value, item["id"])
        assert body["ignore"] == {"id": row["id"], "kind": ignore, "value": value}
        [audit] = [a for a in fake.audit if a["target_table"] == "crm_capture_ignores"]
        assert (audit["action"], audit["actor_id"]) == ("capture_ignore.created", OWNER["id"])

    def test_capture_an_entry_already_there_is_reused(self, lead):
        http, fake, labs, _ = lead
        first = _lead(fake, labs, thread="t-1")
        # Spec 010 R1: two conversations at one domain are one item, so the
        # second lead is keyed by Claude's name (no domain) at the same
        # address domain.
        second = _lead(fake, labs, thread="t-2", domain=None)
        assert second["id"] != first["id"]
        assert _dismiss(http, first, ignore="domain").status_code == 200
        r = _dismiss(http, second, ignore="domain")
        assert r.status_code == 200, r.text
        [row] = _of(fake, "crm_capture_ignores", labs)
        assert r.json()["ignore"]["id"] == row["id"]

    def test_capture_an_entry_added_meanwhile_is_reused(self, lead, monkeypatch):
        # S3-C6: two dismissals at once both find no entry; the second insert
        # meets crm_capture_ignores_once and reuses the first one's entry.
        http, fake, labs, _ = lead
        first = _lead(fake, labs, thread="t-1")
        # Spec 010 R1: two conversations at one domain are one item, so the
        # second lead is keyed by Claude's name (no domain) at the same
        # address domain.
        second = _lead(fake, labs, thread="t-2", domain=None)
        assert second["id"] != first["id"]
        assert _dismiss(http, first, ignore="domain").status_code == 200
        real, seen = service_capture.find_ignore, {"n": 0}

        def not_yet_the_first_time(*args):
            seen["n"] += 1
            return None if seen["n"] == 1 else real(*args)

        monkeypatch.setattr(service_capture, "find_ignore", not_yet_the_first_time)
        r = _dismiss(http, second, ignore="domain")
        assert r.status_code == 200, r.text
        [row] = _of(fake, "crm_capture_ignores", labs)
        assert r.json()["ignore"] == {"id": row["id"], "kind": "domain", "value": row["value"]}
        assert fake.rows["crm_review_items"][second["id"]]["status"] == "dismissed"

    def test_capture_a_webmail_domain_is_refused(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs, domain=None, person_email="priya.shah@gmail.com")
        r = _dismiss(http, item, ignore="domain")
        assert (r.status_code, r.json()["detail"]) == (422, capture.WEBMAIL_IGNORE)
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"
        assert _of(fake, "crm_capture_ignores", labs) == []
        assert _dismiss(http, item, ignore="address").status_code == 200

    def test_capture_ignore_on_another_type_is_refused(self, lead):
        http, fake, labs, _ = lead
        company = _company(http)
        item = _signal(fake, labs, company, [_deal(http, company)])
        r = _dismiss(http, item, ignore="address")
        assert (r.status_code, r.json()["detail"]) == (422, capture.ONLY_NEW_LEAD)
        assert fake.rows["crm_review_items"][item["id"]]["status"] == "open"

    def test_capture_a_member_owner_may_dismiss_with_ignore(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs, owner=SELLER["id"])
        r = _dismiss(http, item, who=SELLER["id"], ignore="domain")
        assert r.status_code == 200, r.text
        [row] = _of(fake, "crm_capture_ignores", labs)
        assert row["created_by"] == SELLER["id"]

    def test_capture_a_plain_dismissal_adds_nothing(self, lead):
        http, fake, labs, _ = lead
        item = _lead(fake, labs)
        r = _dismiss(http, item)
        assert r.status_code == 200, r.text
        # Contracts §1.3: `ignore` is always there, null with nothing added.
        assert set(r.json()) == {"item", "ignore"} and r.json()["ignore"] is None
        assert _of(fake, "crm_capture_ignores", labs) == []


@pytest.fixture
def job(monkeypatch) -> Capture:
    """The discovery job's Labs (test_crm_capture_job.py), in Live."""
    cap = Capture(monkeypatch)
    cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]].update(
        finished_at="2026-10-19T00:00:00+00:00", applied_at="2026-10-19T01:00:00+00:00"
    )
    cap.fake.set_capture(cap.labs, "live")
    return cap


class TestEndToEnd:
    """Spec §13 S3 lines 2 and 3, through the job, the queue and back."""

    def test_capture_approval_creates_and_files(self, job):
        job.conversation("t1")
        job.answer(LEAD)
        job.tick()
        [item] = job.fake.rows["crm_review_items"].values()
        r = job.http.post(
            f"/api/v1/crm/review/{item['id']}/approve?workspace=labs",
            json={"version": 1},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        [filed] = job.celery.named("crm.capture_thread")
        assert filed["args"] == [job.labs["id"], job.mailbox["id"], "t1"]
        assert any(
            a.get("suggestion_id") == item["id"] and a["action"] == "company.created"
            for a in job.fake.audit
        )
        assert result["company_id"] in job.fake.rows["crm_companies"]
        # Later mail from the approved domain is known: filed, never judged.
        job.answer()  # no script: a Claude call fails the test
        job.conversation("t2", sender="Ravi <ravi@acme-robotics.com>", at=JOB_NOW)
        got = job.tick()
        assert got["outcome"] == "ok", got
        assert job.candidate("t2")["status"] == "known"
        assert job.claude.calls == []
        assert [f["args"][2] for f in job.celery.named("crm.capture_thread")] == ["t1", "t2"]

    def test_capture_ignored_domain_from_a_dismissal_never_reaches_claude(self, job):
        job.conversation("t1")
        job.answer(LEAD)
        job.tick()
        [item] = job.fake.rows["crm_review_items"].values()
        r = job.http.post(
            f"/api/v1/crm/review/{item['id']}/dismiss?workspace=labs",
            json={"ignore": "domain"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        job.answer()  # no script: a Claude call fails the test
        job.conversation("t2", sender="ravi@acme-robotics.com", at=JOB_NOW)
        got = job.tick()
        assert got["outcome"] == "ok", got
        assert job.candidate("t2")["status"] == "ignored"
        assert job.gmail.text_reads == ["t1-m1"] and len(job.claude.calls) == 0


class TestGroupedApproval:
    """Spec 010 R1 FR-CAP-GRP-04, -09; plan R1.2, PD-35; spec §14.5 I1
    ("approving it queues three filings"; "two webmail senders grouped by
    Claude's company name both become people")."""

    def test_capture_a_merged_item_shows_what_it_was_merged_into(self, lead):
        """Contracts §7.4: a consolidated item is `expired` with
        `result.superseded_by`; the queue passes only that id on (plan R1
        Display: "Merged into another suggestion")."""
        http, fake, labs, _ = lead
        merged = _lead(fake, labs, thread="t-old")
        kept = _lead(fake, labs, thread="t-new", domain="other.example.com")
        fake.rows["crm_review_items"][merged["id"]].update(
            status="expired",
            decided_at=SENT_AT.isoformat(),
            result={"superseded_by": kept["id"], "reason": "Merged into one suggestion."},
        )
        r = http.get(f"/api/v1/crm/review{LABS}&scope=all&status=decided", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        [shown] = [i for i in r.json()["items"] if i["id"] == merged["id"]]
        assert shown["result"] == {"superseded_by": kept["id"]}
        opened = http.get(f"/api/v1/crm/review{LABS}", headers=as_(OWNER["id"])).json()["items"]
        assert [i["result"] for i in opened if i["id"] == kept["id"]] == [None]

    def test_capture_files_every_grouped_conversation(self, lead):
        http, fake, labs, celery = lead
        for n, who in enumerate(("priya", "ravi", "sam"), 1):
            item = _lead(
                fake,
                labs,
                thread=f"t-{n}",
                person_email=f"{who}@acme-robotics.com",
                person_name=f"{who.title()} Shah",
                latest_at=f"2026-10-0{n}T11:40:00+05:30",
            )
        assert len(item["source_link"]["threads"]) == 3
        # The queue shows every conversation (contracts §7.4).
        listed = http.get(f"/api/v1/crm/review{LABS}", headers=as_(OWNER["id"])).json()["items"]
        [shown] = [i for i in listed if i["id"] == item["id"]]
        assert len(shown["source"]["link"]["threads"]) == 3
        assert shown["source"]["link"]["thread_key"] == "mb-1:t-3"
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["people"], result["threads_filed"]) == (3, 3)
        assert sorted(f["args"][2] for f in celery.named("crm.capture_thread")) == [
            "t-1",
            "t-2",
            "t-3",
        ]
        people = {p["primary_email"]: p for p in _of(fake, "crm_people", labs)}
        assert set(people) == {
            "priya@acme-robotics.com",
            "ravi@acme-robotics.com",
            "sam@acme-robotics.com",
        }
        assert {p["company_id"] for p in people.values()} == {result["company_id"]}
        # The latest conversation's sender is the suggestion's person.
        assert fake.rows["crm_people"][result["person_id"]]["primary_email"] == (
            "sam@acme-robotics.com"
        )
        assert people["ravi@acme-robotics.com"]["first_name"] == "Ravi"

    def test_capture_webmail_senders_grouped_by_name_become_people_and_are_filed(self, lead):
        http, fake, labs, celery = lead
        for n, email in enumerate(("a.k@gmail.com", "b.r@gmail.com"), 1):
            item = _lead(
                fake,
                labs,
                thread=f"t-{n}",
                domain=None,
                company_name="Zeta Labs",
                person_email=email,
                person_name=None,
                latest_at=f"2026-10-0{n}T11:40:00+05:30",
            )
        assert item["source_key"] == "capture:name:zeta labs"
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["people"], result["threads_filed"]) == (2, 2)
        assert {p["primary_email"] for p in _of(fake, "crm_people", labs)} == {
            "a.k@gmail.com",
            "b.r@gmail.com",
        }
        assert len(celery.named("crm.capture_thread")) == 2

    def test_capture_an_address_already_a_person_is_used_not_made(self, lead):
        http, fake, labs, celery = lead
        known = http.post(
            f"/api/v1/crm/people{LABS}",
            json={"first_name": "Ravi", "primary_email": "ravi@acme-robotics.com"},
            headers=as_(OWNER["id"]),
        ).json()
        _lead(fake, labs, thread="t-1", person_email="ravi@acme-robotics.com")
        item = _lead(
            fake,
            labs,
            thread="t-2",
            person_email="priya@acme-robotics.com",
            latest_at="2026-10-05T11:40:00+05:30",
        )
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["people"] == 2
        ravis = [p for p in _of(fake, "crm_people", labs) if p.get("first_name") == "Ravi"]
        assert [p["id"] for p in ravis] == [known["id"]]
        # Linked to the company, not only reused (FR-CAP-GRP-04).
        company_id = r.json()["result"]["company_id"]
        assert fake.rows["crm_people"][known["id"]]["company_id"] == company_id

    def test_capture_a_person_of_another_company_stays_there(self, lead):
        """FR-CAP-GRP-04 (decided): approval never moves a person between
        companies; one under another company is reused as it is."""
        http, fake, labs, celery = lead
        other = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Elsewhere"}, headers=as_(OWNER["id"])
        ).json()
        known = http.post(
            f"/api/v1/crm/people{LABS}",
            json={
                "first_name": "Ravi",
                "primary_email": "ravi@acme-robotics.com",
                "company_id": other["id"],
            },
            headers=as_(OWNER["id"]),
        ).json()
        item = _lead(fake, labs, thread="t-1", person_email="ravi@acme-robotics.com")
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["person_id"] == known["id"]
        assert fake.rows["crm_people"][known["id"]]["company_id"] == other["id"]

    def test_capture_an_edited_address_makes_one_person(self, lead):
        """Plan R1.2: the newest conversation's sender is the suggestion's
        person, so Edit and approve correcting the address makes no second
        person under the old one."""
        http, fake, labs, celery = lead
        item = _lead(fake, labs, thread="t-1", person_email="info@acme-robotics.com")
        assert item["source_link"]["threads"][0]["person_email"] == "info@acme-robotics.com"
        r = _approve(http, item, edits={"email": "asha@acme-robotics.com", "first_name": "Asha"})
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["people"], result["threads_filed"]) == (1, 1)
        people = _of(fake, "crm_people", labs)
        assert [(p["primary_email"], p["first_name"]) for p in people] == [
            ("asha@acme-robotics.com", "Asha")
        ]

    def test_capture_an_edited_address_on_a_grouped_item_replaces_the_latest(self, lead):
        http, fake, labs, celery = lead
        _lead(fake, labs, thread="t-1", person_email="ravi@acme-robotics.com")
        item = _lead(
            fake,
            labs,
            thread="t-2",
            person_email="info@acme-robotics.com",
            latest_at="2026-10-05T11:40:00+05:30",
        )
        r = _approve(http, item, edits={"email": "asha@acme-robotics.com"})
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["people"], result["threads_filed"]) == (2, 2)
        assert {p["primary_email"] for p in _of(fake, "crm_people", labs)} == {
            "asha@acme-robotics.com",
            "ravi@acme-robotics.com",
        }

    def test_capture_an_edited_webmail_address_is_not_counted_filed(self, lead):
        """Review R1-X1 (FR-CAP-GRP-04): the edited-away address makes no
        second person, and nobody holds it after approval, so conversations
        from it are not counted as filed (webmail: no company domain
        matches either)."""
        http, fake, labs, celery = lead
        for n in (1, 2):
            item = _lead(
                fake,
                labs,
                thread=f"t-{n}",
                domain=None,
                company_name="Zeta Labs",
                person_email="bob@gmail.com",
                person_name="Bob",
                latest_at=f"2026-10-0{n}T11:40:00+05:30",
            )
        r = _approve(http, item, edits={"email": "robert@gmail.com"})
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["people"], result["threads_filed"]) == (1, 0)
        assert [p["primary_email"] for p in _of(fake, "crm_people", labs)] == ["robert@gmail.com"]
        assert len(celery.named("crm.capture_thread")) == 2

    def test_capture_an_edited_address_on_an_item_from_before_074(self, lead):
        http, fake, labs, celery = lead
        item = fake.add_review_item(
            labs,
            type="new_lead",
            record_type=None,
            record_id=None,
            owner_id=OWNER["id"],
            source_key="gmail:mb-1:t-old",
            source_version="m-1",
            source_link={"mailbox_id": "mb-1", "thread_id": "t-old"},
            suggestion={
                "company_name": "Acme Robotics",
                "domain": "acme-robotics.com",
                "lead_stage": "new",
                "first_name": "Ravi",
                "last_name": None,
                "email": "ravi@acme-robotics.com",
                "evidence": {"message_count": 1, "reason": "Asked."},
            },
        )
        r = _approve(http, item, edits={"email": "ravi.k@acme-robotics.com"})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["people"] == 1
        assert [p["primary_email"] for p in _of(fake, "crm_people", labs)] == [
            "ravi.k@acme-robotics.com"
        ]

    def test_capture_an_item_from_before_074_files_its_one_link(self, lead):
        http, fake, labs, celery = lead
        item = fake.add_review_item(
            labs,
            type="new_lead",
            record_type=None,
            record_id=None,
            owner_id=OWNER["id"],
            source_key="gmail:mb-1:t-old",
            source_version="m-1",
            source_link={"mailbox_id": "mb-1", "thread_id": "t-old"},
            suggestion={
                "company_name": "Acme Robotics",
                "domain": "acme-robotics.com",
                "lead_stage": "new",
                "first_name": "Priya",
                "last_name": None,
                "email": "priya@acme-robotics.com",
                "evidence": {"message_count": 1, "reason": "Asked."},
            },
        )
        r = _approve(http, item)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert (result["people"], result["threads_filed"]) == (1, 1)
        [filed] = celery.named("crm.capture_thread")
        assert filed["args"] == [labs["id"], "mb-1", "t-old"]

    def test_capture_a_new_conversation_refuses_a_form_opened_before_it(self, lead):
        http, fake, labs, celery = lead
        item = _lead(fake, labs, thread="t-1")
        shown = dict(item)
        _lead(fake, labs, thread="t-2", latest_at="2026-10-09T11:40:00+05:30")
        assert fake.rows["crm_review_items"][item["id"]]["revision"] == shown["revision"] + 1
        r = _approve(http, shown)
        assert (r.status_code, r.json()["detail"]) == (409, review_rules.SUGGESTION_CHANGED)
        assert celery.named("crm.capture_thread") == []


# ---------------------------------------------------------------------------
# Spec 010 R1 I3 — course enquiries approved in the Academy (tasks T447)
# ---------------------------------------------------------------------------

ACADEMY = "?workspace=academy"
BOX = "navaneeth@nunnarilabs.com"


def _academy(fake):
    return next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == "academy")


def _course(
    fake, academy, *, thread="c1", email="asha@college.example.edu", mailbox=BOX, **finding
):
    """One Academy `new_lead`, as the Academy step of the job makes it."""
    item = capture.academy_lead_item(
        {
            **FINDING,
            "company_name": "City College",
            "domain": "college.example.edu",
            "person_name": "Asha Kumar",
            "person_email": email,
            "person_title": None,
            "reason": "Asked about the AI course for final-year students, budget $5,000.",
            **finding,
        },
        mailbox_email=mailbox,
        thread_id=thread,
        message_id=f"{thread}-m2",
        outcome="new_lead",
        confidence="high",
        owner_id=OWNER["id"],
        model="claude-sonnet-5-5",
        generated_at=SENT_AT.isoformat(),
    )
    made = service.capture_group_upsert(academy["id"], item, item["source_link"]["threads"][0])
    return fake.rows["crm_review_items"][made["id"]]


class TestAcademyApproval:
    """FR-CAP-ACAD-05, -06; contracts §7.4; plan R1.4, PD-31."""

    def test_capture_academy_approval_records_a_note(self, lead):
        http, fake, labs, celery = lead
        academy = _academy(fake)
        labs_before = {
            t: [r for r in fake.rows[t].values() if r.get("workspace_id") == labs["id"]]
            for t in ("crm_companies", "crm_people", "crm_notes", "crm_review_items")
        }
        item = _course(fake, academy)
        r = http.post(
            f"/api/v1/crm/review/{item['id']}/approve{ACADEMY}",
            json={"version": item.get("revision") or 1},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert set(result) == {
            "company_id",
            "person_id",
            "people",
            "note_id",
            "linked",
            "threads_filed",
        }
        assert result["threads_filed"] == 0 and result["people"] == 1
        [company] = _of(fake, "crm_companies", academy)
        assert company["id"] == result["company_id"] and company["name"] == "City College"
        assert company["lead_stage"] == "contacted"
        [person] = _of(fake, "crm_people", academy)
        assert person["primary_email"] == "asha@college.example.edu"
        assert person["company_id"] == company["id"]
        [note] = _of(fake, "crm_notes", academy)
        assert note["id"] == result["note_id"] and note["kind"] == "note"
        assert note["body"].startswith(
            f"Email conversation in {BOX}: 2 emails, 1 Oct to 2 Oct 2026. "
            "Asked about the AI course for final-year students"
        )
        assert "5,000" not in note["body"]  # FR-CRMAI-LLM-05
        assert str(note["occurred_on"])[:10] == "2026-10-02"
        links = {
            (link["record_type"], link["record_id"])
            for link in fake.rows["crm_note_links"].values()
            if link["note_id"] == note["id"]
        }
        assert links == {("company", company["id"]), ("person", person["id"])}
        # Nothing to file: the Academy has no mailbox.
        assert celery.named("crm.capture_thread") == []
        assert fake.rows["crm_email_messages"] == {}
        # No Labs row changed.
        assert {
            t: [r for r in fake.rows[t].values() if r.get("workspace_id") == labs["id"]]
            for t in labs_before
        } == labs_before

    def test_capture_academy_approval_makes_a_person_per_sender(self, lead):
        http, fake, _, celery = lead
        academy = _academy(fake)
        _course(fake, academy, thread="c0", email="ravi@college.example.edu", person_name="Ravi")
        item = _course(fake, academy)
        assert len(item["source_link"]["threads"]) == 2
        r = http.post(
            f"/api/v1/crm/review/{item['id']}/approve{ACADEMY}",
            json={"version": item["revision"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["result"]["people"] == 2
        [note] = _of(fake, "crm_notes", academy)
        assert note["body"].count("Email conversation in") == 2
        linked = [
            link for link in fake.rows["crm_note_links"].values() if link["note_id"] == note["id"]
        ]
        assert sorted(link["record_type"] for link in linked) == ["company", "person", "person"]
        assert celery.named("crm.capture_thread") == []

    def test_capture_academy_note_names_each_conversation_s_mailbox(self, lead):
        # One college's enquiries reached two Labs mailboxes and were grouped
        # into one Academy item; the note names the right mailbox for each.
        http, fake, _, _ = lead
        academy = _academy(fake)
        _course(
            fake,
            academy,
            thread="c0",
            email="ravi@college.example.edu",
            person_name="Ravi",
            mailbox="sales@nunnarilabs.com",
        )
        item = _course(fake, academy)
        assert item["suggestion"]["evidence"]["mailbox_email"] == BOX
        r = http.post(
            f"/api/v1/crm/review/{item['id']}/approve{ACADEMY}",
            json={"version": item["revision"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        [note] = _of(fake, "crm_notes", academy)
        assert note["body"].count("Email conversation in sales@nunnarilabs.com:") == 1
        assert note["body"].count(f"Email conversation in {BOX}:") == 1

    def test_capture_academy_dismiss_writes_the_academy_ignore(self, lead):
        http, fake, labs, _ = lead
        academy = _academy(fake)
        item = _course(fake, academy)
        r = http.post(
            f"/api/v1/crm/review/{item['id']}/dismiss{ACADEMY}",
            json={"ignore": "domain"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        [row] = _of(fake, "crm_capture_ignores", academy)
        assert (row["kind"], row["value"]) == ("domain", "college.example.edu")
        assert _of(fake, "crm_capture_ignores", labs) == []
