"""Campaigns and their members — spec 007 FR-CRM-CAMP-01 to -07,
FR-CRM-PER-02, FR-CRM-MIG-01 (campaigns and members), FR-CRM-AUTO-01
(automation 8), FR-CRM-VIEW-07 (add to campaign), -11 (Members by status),
FR-CRM-FX-03 (the budget), FR-CRM-CO-02 (memberships on a company); spec
§11 AC journey 4.9 "converts one college to a deal; the deal carries the
campaign as its source"; contracts §2.10.

Routes run against `tests/crm_fake.py`, whose `crm_apply` keeps 041's
CHECKs (a member names a company or a person; only a converted member has a
deal; the budget's FX integrity). The Twenty fixture `campaigns.json` is
hand-written from contracts §2.9's declared shape.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import campaigns as rules
from app.services.crm import deals, importer, records

ACADEMY = "?workspace=academy"
LABS = "?workspace=labs"
API = "/api/v1/crm"
FIXTURE = (Path(__file__).parent / "fixtures" / "twenty" / "campaigns.json").read_text()


@pytest.fixture
def crm(monkeypatch):
    """Academy with a qualified college, two of its people (one marked Do not
    contact), a second college, and a USD rate for October."""
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    for ws in (labs, academy):
        fake.add_deal_lists(ws)
        fake.add_campaign_lists(ws)
        fake.add(
            "crm_fx_rates", workspace_id=ws["id"], currency="USD", month="2026-10-01", rate="84"
        )
    fake.add_member(academy, VIEWER["id"], "viewer")
    for module in (records, deals, importer):
        monkeypatch.setattr(module, "today_in_company_tz", lambda: date(2026, 10, 15))
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    college = _post(http, "companies", {"name": "PSG Tech"})
    _patch(http, "companies", college["id"], {"lead_stage": "qualified"})
    other = _post(http, "companies", {"name": "KCT College"})
    priya = _post(http, "people", {"first_name": "Priya", "company_id": college["id"]})
    ravi = _post(
        http, "people", {"first_name": "Ravi", "company_id": college["id"], "do_not_contact": True}
    )
    webinar = http.post(
        f"{API}/campaigns{ACADEMY}",
        json={"name": "Saturday webinars", "type_id": _option(fake, academy, "Webinar")},
        headers=me,
    )
    assert webinar.status_code == 201, webinar.text
    return {
        "http": http,
        "fake": fake,
        "labs": labs,
        "academy": academy,
        "college": college,
        "other": other,
        "priya": priya,
        "ravi": ravi,
        "campaign": webinar.json(),
    }


def _post(http, plural, body, who=OWNER["id"], query=ACADEMY):
    r = http.post(f"{API}/{plural}{query}", json=body, headers=as_(who))
    assert r.status_code == 201, r.text
    return r.json()


def _patch(http, plural, record_id, body, who=OWNER["id"], query=ACADEMY):
    r = http.patch(f"{API}/{plural}/{record_id}{query}", json=body, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def _option(fake, ws, label):
    return next(
        o["id"]
        for o in fake.rows["crm_select_options"].values()
        if o["workspace_id"] == ws["id"] and o["label"] == label
    )


def _add(made, who=OWNER["id"], **body):
    return made["http"].post(
        f"{API}/campaigns/{made['campaign']['id']}/members{ACADEMY}", json=body, headers=as_(who)
    )


def _members(made, who=OWNER["id"]):
    r = made["http"].get(
        f"{API}/campaigns/{made['campaign']['id']}/members{ACADEMY}", headers=as_(who)
    )
    assert r.status_code == 200, r.text
    return r.json()


def _member_of(made, **match):
    return next(m for m in _members(made)["rows"] if all(m[k] == v for k, v in match.items()))


class TestFields:
    """FR-CRM-CAMP-01, FR-CRM-WS-06, Q-61, FR-CRM-FX-03."""

    def test_a_campaign_has_the_fields_of_spec_6_6(self, crm):
        http = crm["http"]
        body = {
            "name": "Roundtable",
            "type_id": _option(crm["fake"], crm["academy"], "Webinar"),
            "status_id": _option(crm["fake"], crm["academy"], "Live"),
            "starts_on": "2026-10-03",
            "ends_on": "2027-01-30",
            "goal": "Saturdays 19:00 to 21:00",
            "budget": "500",
            "budget_currency": "USD",
        }
        made = _post(http, "campaigns", body)
        assert made["type_label"] == "Webinar"
        assert made["status_label"] == "Live"
        assert made["owner_id"] == OWNER["id"]
        assert (made["budget"], made["budget_currency"]) == ("500.00", "USD")
        assert (made["budget_in_inr"], made["budget_fx_rate"]) == ("42000.00", "84")
        assert made["member_counts"]["targeted"] == 0
        assert made["conversion_rate"] is None

    def test_entity_is_labs_only(self, crm):
        http = crm["http"]
        r = http.post(
            f"{API}/campaigns{ACADEMY}",
            json={"name": "X", "entity": "deepweaver"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert "Entity is not used in Nunnari Academy B2B" in r.json()["detail"]
        made = _post(http, "campaigns", {"name": "CODISSIA", "entity": "deepweaver"}, query=LABS)
        assert made["entity_label"] == "DeepWeaver"

    @pytest.mark.parametrize(
        ("body", "detail"),
        [
            ({"name": "X", "starts_on": "2026-11-01", "ends_on": "2026-10-01"}, "end date"),
            ({"name": "X", "budget": "10"}, "A budget needs a currency."),
            ({"name": "X", "budget_in_inr": "10"}, "written by the system"),
            ({"name": "X", "budget": "10", "budget_currency": "AUD"}, "No AUD rate"),
            ({"type_id": None}, "A campaign needs name."),
        ],
    )
    def test_refusals(self, crm, body, detail):
        r = crm["http"].post(f"{API}/campaigns{ACADEMY}", json=body, headers=as_(OWNER["id"]))
        assert r.status_code == 422
        assert detail in r.json()["detail"]

    def test_an_option_of_another_list_is_refused(self, crm):
        r = crm["http"].post(
            f"{API}/campaigns{ACADEMY}",
            json={"name": "X", "status_id": _option(crm["fake"], crm["academy"], "Webinar")},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422

    def test_a_viewer_cannot_create_one(self, crm):
        r = crm["http"].post(
            f"{API}/campaigns{ACADEMY}", json={"name": "X"}, headers=as_(VIEWER["id"])
        )
        assert r.status_code == 403

    def test_drive_links_tasks_and_notes_hang_on_a_campaign(self, crm):
        """FR-CRM-LINK-01, FR-CRM-TASK-01, FR-CRM-NOTE-01 (1b)."""
        http, cid = crm["http"], crm["campaign"]["id"]
        target = {"record_type": "campaign", "record_id": cid}
        r = http.post(
            f"{API}/links{ACADEMY}",
            json={**target, "url": "https://drive.google.com/x", "label": "Deck"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201, r.text
        _post(http, "tasks", {"title": "Book the venue", "links": [target]})
        _post(http, "notes", {"body": "Good turnout", "links": [target]})
        page = http.get(f"{API}/campaigns/{cid}{ACADEMY}", headers=as_(OWNER["id"])).json()
        assert [link["label"] for link in page["links"]] == ["Deck"]
        assert [t["title"] for t in page["tasks"]] == ["Book the venue"]
        assert [n["body"] for n in page["notes"]] == ["Good turnout"]
        assert page["tasks"][0]["links"][0]["name"] == "Saturday webinars"

    def test_trash_and_restore(self, crm):
        http, cid = crm["http"], crm["campaign"]["id"]
        assert (
            http.delete(f"{API}/campaigns/{cid}{ACADEMY}", headers=as_(OWNER["id"])).status_code
            == 200
        )
        assert (
            http.get(f"{API}/campaigns/{cid}{ACADEMY}", headers=as_(OWNER["id"])).status_code == 404
        )
        trash = http.get(f"{API}/trash{ACADEMY}&object=campaigns", headers=as_(OWNER["id"])).json()
        assert [r["name"] for r in trash["rows"]] == ["Saturday webinars"]
        r = http.post(f"{API}/campaigns/{cid}/restore{ACADEMY}", headers=as_(OWNER["id"]))
        assert r.status_code == 200


class TestMembers:
    """FR-CRM-CAMP-01 to -03, FR-CRM-PER-02."""

    def test_companies_and_people_join(self, crm):
        r = _add(crm, company_ids=[crm["other"]["id"]], person_ids=[crm["priya"]["id"]])
        assert r.status_code == 200, r.text
        assert r.json()["added"] == 2
        rows = _members(crm)["rows"]
        assert {m["name"] for m in rows} == {"KCT College", "Priya"}
        assert {m["status"] for m in rows} == {"targeted"}
        assert _members(crm)["member_counts"]["targeted"] == 2

    def test_a_member_needs_a_company_or_a_person(self, crm):
        r = _add(crm)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Choose the companies or people to add.",
        )
        assert rules.plan_additions([], [], []).add == []

    def test_do_not_contact_is_skipped_and_named(self, crm):
        r = _add(crm, person_ids=[crm["priya"]["id"], crm["ravi"]["id"]])
        body = r.json()
        assert (body["added"], body["skipped_do_not_contact"]) == (1, 1)
        assert body["skipped"] == [
            {
                "id": crm["ravi"]["id"],
                "name": "Ravi",
                "reason": "Ravi is marked Do not contact, so they are not added to campaigns.",
            }
        ]

    def test_marking_do_not_contact_later_keeps_the_history(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        _patch(crm["http"], "people", crm["priya"]["id"], {"do_not_contact": True})
        member = _member_of(crm, person_id=crm["priya"]["id"])
        assert member["do_not_contact"] is True
        assert member["status"] == "targeted"

    def test_a_record_joins_once(self, crm):
        _add(crm, company_ids=[crm["other"]["id"]])
        body = _add(crm, company_ids=[crm["other"]["id"]]).json()
        assert (body["added"], body["skipped_existing"]) == (0, 1)
        assert len(_members(crm)["rows"]) == 1

    def test_a_record_of_another_workspace_is_no_such(self, crm):
        stranger = _post(crm["http"], "companies", {"name": "Elsewhere"}, query=LABS)
        r = _add(crm, company_ids=[stranger["id"]])
        assert (r.status_code, r.json()["detail"]) == (404, "No such company.")

    def test_from_a_saved_view(self, crm):
        """FR-CRM-CAMP-03 "by a saved filter"."""
        http = crm["http"]
        view = http.post(
            f"{API}/views{ACADEMY}",
            json={
                "object": "person",
                "name": "PSG people",
                "visibility": "private",
                "config": {
                    "filters": {
                        "op": "and",
                        "items": [
                            {"field": "company_id", "operator": "is", "value": crm["college"]["id"]}
                        ],
                    }
                },
            },
            headers=as_(OWNER["id"]),
        ).json()
        body = _add(crm, view_id=view["id"]).json()
        assert (body["added"], body["skipped_do_not_contact"]) == (1, 1)

    def test_bulk_add_to_campaign(self, crm):
        """FR-CRM-VIEW-07 — each record on its own; Do not contact refused."""
        r = crm["http"].post(
            f"{API}/bulk{ACADEMY}",
            json={
                "object": "people",
                "ids": [crm["priya"]["id"], crm["ravi"]["id"]],
                "action": "add_to_campaign",
                "values": {"campaign_id": crm["campaign"]["id"]},
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] == [crm["priya"]["id"]]
        assert r.json()["failed"] == [
            {
                "id": crm["ravi"]["id"],
                "reason": "Ravi is marked Do not contact, so they are not added to campaigns.",
            }
        ]

    def test_bulk_add_names_a_live_campaign(self, crm):
        r = crm["http"].post(
            f"{API}/bulk{ACADEMY}",
            json={
                "object": "companies",
                "ids": [crm["other"]["id"]],
                "action": "add_to_campaign",
                "values": {},
            },
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "Name the campaign: campaign_id.")

    def test_a_company_page_lists_its_memberships(self, crm):
        """FR-CRM-CO-02 (1b), REC-03 Related."""
        _add(crm, company_ids=[crm["other"]["id"]])
        page = (
            crm["http"]
            .get(f"{API}/companies/{crm['other']['id']}{ACADEMY}", headers=as_(OWNER["id"]))
            .json()
        )
        assert [m["campaign_name"] for m in page["related"]["campaign_members"]] == [
            "Saturday webinars"
        ]

    def test_members_by_status_is_a_seeded_kanban(self, crm):
        """FR-CRM-VIEW-11 (1b)."""
        _add(crm, company_ids=[crm["other"]["id"]], person_ids=[crm["priya"]["id"]])
        r = crm["http"].post(
            f"{API}/query{ACADEMY}",
            json={
                "object": "campaign_member",
                "view": "members_by_status",
                "spec": {
                    "filters": {
                        "op": "and",
                        "items": [
                            {
                                "field": "campaign_id",
                                "operator": "is",
                                "value": crm["campaign"]["id"],
                            }
                        ],
                    }
                },
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["group_by"] == "status"
        assert [(g["key"], g["label"], g["count"]) for g in body["groups"]] == [
            ("targeted", "Targeted", 2)
        ]


class TestStatuses:
    """FR-CRM-CAMP-02, -04."""

    def test_drag_through_statuses_with_last_touch_and_notes(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        r = crm["http"].patch(
            f"{API}/campaign-members/{member['id']}{ACADEMY}",
            json={
                "status": "registered",
                "last_touch_on": "2026-10-10",
                "notes": "Asked for slides",
            },
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200, r.text
        assert (r.json()["status_label"], r.json()["last_touch_on"]) == ("Registered", "2026-10-10")

    def test_converted_is_reached_only_by_converting(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        r = crm["http"].patch(
            f"{API}/campaign-members/{member['id']}{ACADEMY}",
            json={"status": "converted"},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, rules.USE_CONVERT)

    def test_a_viewer_cannot_move_a_member(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        r = crm["http"].patch(
            f"{API}/campaign-members/{member['id']}{ACADEMY}",
            json={"status": "invited"},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 403

    def test_the_rules(self):
        assert rules.status_refusal("targeted", "converted") == rules.USE_CONVERT
        assert rules.status_refusal("converted", "engaged") == rules.STAYS_CONVERTED
        assert rules.status_refusal("invited", "attended") is None


class TestConversion:
    """FR-CRM-CAMP-04, FR-CRM-AUTO-01 automation 8; journey 4.9."""

    def _convert(self, crm, member_id, body, who=OWNER["id"]):
        return crm["http"].post(
            f"{API}/campaign-members/{member_id}/convert{ACADEMY}", json=body, headers=as_(who)
        )

    def test_to_a_new_deal_sets_both_links(self, crm):
        _add(crm, company_ids=[crm["college"]["id"]])
        member = _member_of(crm, company_id=crm["college"]["id"])
        r = self._convert(
            crm,
            member["id"],
            {
                "new": {
                    "name": "PSG Tech - AI course",
                    "amount": "300000",
                    "currency": "INR",
                    "next_step": "Send the proposal",
                    "next_step_on": "2026-10-22",
                }
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        deal = body["opportunity"]
        assert body["member"]["status"] == "converted"
        assert body["member"]["converted_opportunity_id"] == deal["id"]
        assert deal["company_id"] == crm["college"]["id"]
        assert deal["source_campaign_id"] == crm["campaign"]["id"]
        assert deal["source_campaign_name"] == "Saturday webinars"
        page = (
            crm["http"]
            .get(f"{API}/opportunities/{deal['id']}{ACADEMY}", headers=as_(OWNER["id"]))
            .json()
        )
        assert page["record"]["source_campaign_name"] == "Saturday webinars"

    def test_to_an_existing_deal_sets_both_links(self, crm):
        deal = _post(
            crm["http"],
            "opportunities",
            {
                "name": "PSG Tech - bootcamp",
                "company_id": crm["college"]["id"],
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
        )
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        r = self._convert(crm, member["id"], {"opportunity_id": deal["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["member"]["converted_opportunity_name"] == "PSG Tech - bootcamp"
        assert r.json()["opportunity"]["source_campaign_id"] == crm["campaign"]["id"]

    def test_a_new_deal_takes_the_persons_company_and_contact(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        deal = self._convert(
            crm,
            member["id"],
            {"new": {"name": "Priya's course", "next_step": "Call", "next_step_on": "2026-10-20"}},
        ).json()["opportunity"]
        assert deal["company_id"] == crm["college"]["id"]
        assert deal["point_of_contact_id"] == crm["priya"]["id"]

    def test_the_deal_rules_still_hold(self, crm):
        """A new deal opens only on a qualified company (FR-CRM-PIPE-04), and
        nothing is written when it is refused."""
        _add(crm, company_ids=[crm["other"]["id"]])
        member = _member_of(crm, company_id=crm["other"]["id"])
        r = self._convert(
            crm,
            member["id"],
            {"new": {"name": "KCT deal", "next_step": "Call", "next_step_on": "2026-10-20"}},
        )
        assert r.status_code == 422
        assert _member_of(crm, company_id=crm["other"]["id"])["status"] == "targeted"

    def test_name_one_deal(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        r = self._convert(crm, member["id"], {})
        assert (r.status_code, r.json()["detail"]) == (422, rules.NAME_A_DEAL)

    def test_automation_8_off_leaves_the_deal_alone(self, crm):
        """FR-CRM-AUTO-02 — the switch."""
        crm["fake"].add(
            "crm_workspace_settings",
            workspace_id=crm["academy"]["id"],
            key="automations",
            value={
                "email_records": True,
                "stale_notice": True,
                "digests": True,
                "task_reminders": True,
                "campaign_conversion": False,
            },
        )
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        body = self._convert(
            crm,
            member["id"],
            {"new": {"name": "Off", "next_step": "Call", "next_step_on": "2026-10-20"}},
        ).json()
        assert body["member"]["status"] == "converted"
        assert body["opportunity"]["source_campaign_id"] is None

    def test_a_converted_member_stays_converted(self, crm):
        _add(crm, person_ids=[crm["priya"]["id"]])
        member = _member_of(crm, person_id=crm["priya"]["id"])
        self._convert(
            crm,
            member["id"],
            {"new": {"name": "Deal", "next_step": "Call", "next_step_on": "2026-10-20"}},
        )
        r = crm["http"].patch(
            f"{API}/campaign-members/{member['id']}{ACADEMY}",
            json={"status": "engaged"},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, rules.STAYS_CONVERTED)
        again = self._convert(crm, member["id"], {"opportunity_id": member["id"]})
        assert (again.status_code, again.json()["detail"]) == (422, rules.ALREADY_CONVERTED)


class TestFigures:
    """FR-CRM-CAMP-05 — counts by status, conversion rate, pipeline created,
    won value and cost per converted member."""

    def test_derived_on_the_campaign_page(self, crm):
        http = crm["http"]
        _patch(
            http, "campaigns", crm["campaign"]["id"], {"budget": "60000", "budget_currency": "INR"}
        )
        _add(
            crm,
            company_ids=[crm["college"]["id"], crm["other"]["id"]],
            person_ids=[crm["priya"]["id"]],
        )
        member = _member_of(crm, company_id=crm["college"]["id"])
        http.post(
            f"{API}/campaign-members/{member['id']}/convert{ACADEMY}",
            json={
                "new": {
                    "name": "PSG Tech - AI course",
                    "amount": "300000",
                    "currency": "INR",
                    "next_step": "Send the proposal",
                    "next_step_on": "2026-10-22",
                }
            },
            headers=as_(OWNER["id"]),
        )
        page = http.get(
            f"{API}/campaigns/{crm['campaign']['id']}{ACADEMY}", headers=as_(OWNER["id"])
        ).json()
        record = page["record"]
        assert record["member_count"] == 3
        assert record["member_counts"]["converted"] == 1
        assert record["member_counts"]["targeted"] == 2
        assert record["conversion_rate"] == "33.3"
        assert record["converted_deals"] == 1
        assert record["pipeline_created"] == "300000.00"
        assert record["won_value"] == "0.00"
        assert record["cost_per_converted"] == "60000.00"
        assert [d["name"] for d in page["related"]["opportunities"]] == ["PSG Tech - AI course"]

    def test_the_rule(self):
        members = [
            {"status": "converted", "converted_opportunity_id": "d1"},
            {"status": "converted", "converted_opportunity_id": "d2"},
            {"status": "attended", "converted_opportunity_id": None},
            {"status": "targeted", "converted_opportunity_id": None, "deleted_at": "x"},
        ]
        deals_ = {
            "d1": {"amount_in_inr": "1000.50", "stage": "closed_won"},
            "d2": {"amount_in_inr": 500, "stage": "proposal"},
        }
        found = rules.figures(members, deals_, "3000")
        assert found["member_count"] == 3
        assert found["conversion_rate"] == "66.7"
        assert found["pipeline_created"] == "1500.50"
        assert found["won_value"] == "1000.50"
        assert found["cost_per_converted"] == "1500.00"
        assert rules.figures([], {}, None)["cost_per_converted"] is None


class TestTwentyImport:
    """FR-CRM-MIG-01 (1b), FR-CRM-CAMP-06; Q-49."""

    def _run(self, crm):
        http = crm["http"]
        dry = http.post(
            f"{API}/imports/dry-run{ACADEMY}",
            json={"format": "twenty_json", "content": FIXTURE},
            headers=as_(OWNER["id"]),
        )
        assert dry.status_code == 200, dry.text
        applied = http.post(
            f"{API}/imports/{dry.json()['run_id']}/apply{ACADEMY}",
            json={"format": "twenty_json", "content": FIXTURE},
            headers=as_(OWNER["id"]),
        )
        assert applied.status_code == 200, applied.text
        return dry.json(), applied.json()

    @pytest.fixture
    def imported(self, crm):
        crm["fake"].add(
            "crm_people",
            workspace_id=crm["academy"]["id"],
            first_name="Meera",
            last_name=None,
            twenty_id="p-meera",
            do_not_contact=True,
            company_id=None,
            primary_email=None,
            additional_emails=[],
            phones=[],
            owner_id=None,
            created_at="2026-10-01T00:00:00+00:00",
            deleted_at=None,
            position=0,
        )
        return self._run(crm)

    def test_counts_and_conflicts(self, crm, imported):
        dry, applied = imported
        assert dry["counts"]["campaigns"] == {
            "in_file": 2,
            "create": 2,
            "update": 0,
            "unchanged": 0,
            "skipped": 0,
        }
        assert dry["counts"]["campaign_members"]["create"] == 4
        assert dry["counts"]["campaign_members"]["skipped"] == 2
        kinds = {(c["object"], c["kind"]) for c in dry["conflicts"]}
        assert ("campaign_members", "do_not_contact") in kinds
        assert ("campaign_members", "missing_campaign") in kinds
        assert ("campaign_members", "no_deal") in kinds

    def test_campaigns_members_and_attribution(self, crm, imported):
        fake = crm["fake"]
        campaigns = {c["twenty_id"]: c for c in fake.rows["crm_campaigns"].values()}
        webinars, mou = campaigns["cmp-webinars"], campaigns["cmp-mou"]
        assert webinars["budget_in_inr"] == "50000.00"
        assert webinars["status_id"] == _option(fake, crm["academy"], "Live")
        members = {m["twenty_id"]: m for m in fake.rows["crm_campaign_members"].values()}
        assert set(members) == {"m-arun", "m-kct", "m-psg", "m-kct-mou"}
        assert members["m-arun"]["status"] == "registered"
        assert members["m-arun"]["last_touch_on"] == "2026-10-10"
        assert members["m-kct-mou"]["status"] == "engaged"
        deal = next(
            d for d in fake.rows["crm_opportunities"].values() if d["twenty_id"] == "o-psg-mou"
        )
        assert members["m-psg"]["status"] == "converted"
        assert members["m-psg"]["converted_opportunity_id"] == deal["id"]
        assert deal["source_campaign_id"] == mou["id"]

    def test_a_second_run_changes_nothing(self, crm, imported):
        audit = len(crm["fake"].audit)
        dry, _ = self._run(crm)
        for obj in ("campaigns", "campaign_members"):
            assert dry["counts"][obj]["create"] == 0, obj
            assert dry["counts"][obj]["update"] == 0, obj
        assert dry["counts"]["opportunities"]["update"] == 0
        assert all(r["action"].startswith("import") for r in crm["fake"].audit[audit:])


def test_the_fixture_is_valid_json():
    assert set(json.loads(FIXTURE)) >= {"campaigns", "campaignMembers"}
