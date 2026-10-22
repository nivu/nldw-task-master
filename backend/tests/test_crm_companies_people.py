"""Companies and people — spec 007 FR-CRM-CO-01 to -03, FR-CRM-PER-01,
FR-CRM-DUP-01, -02, FR-CRM-PIPE-01, FR-CRM-LIST-07, -08; contracts §2.5.

Routes run against `tests/crm_fake.py`, whose `crm_apply` keeps the
database's unique indexes, so a race the route did not see still answers 409
as production does. The real constraints are in `tests/db/`.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import dedupe, objects
from app.mcp import crm as tools
from app.services.crm import store

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    return crm_client(monkeypatch, fake=fake), fake, labs, academy


def _company(http, who=OWNER["id"], query=LABS, **fields):
    body = {"name": "Acme", **fields}
    return http.post(f"/api/v1/crm/companies{query}", json=body, headers=as_(who))


def _person(http, who=OWNER["id"], query=LABS, **fields):
    body = {"first_name": "Deepa", **fields}
    return http.post(f"/api/v1/crm/people{query}", json=body, headers=as_(who))


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


class TestCompanyFields:
    """FR-CRM-CO-01, FR-CRM-LIST-07, -08."""

    def test_a_company_needs_a_name(self, crm):
        http, *_ = crm
        r = http.post(f"/api/v1/crm/companies{LABS}", json={"name": "  "}, headers=as_(OWNER["id"]))
        assert r.status_code == 422
        assert r.json()["detail"] == "A company needs name."

    def test_created_with_its_stamps_and_the_creator_as_owner(self, crm):
        http, *_ = crm
        r = _company(http, who=MEMBER["id"], domain="acme.com")
        assert r.status_code == 201, r.text
        body = r.json()
        assert (body["name"], body["domain"], body["owner_id"]) == (
            "Acme",
            "acme.com",
            MEMBER["id"],
        )
        assert body["owner_name"] == "Deepika"
        assert body["created_by"] == MEMBER["id"] and body["updated_at"]

    def test_the_owner_must_be_a_member(self, crm):
        http, *_ = crm
        r = _company(http, owner_id="u-outsider")
        assert r.status_code == 422
        assert r.json()["detail"] == "The owner must be a member of this workspace."

    def test_every_field_round_trips(self, crm):
        http, fake, labs, _ = crm
        r = _company(
            http,
            domain="https://www.Acme.com/about",
            additional_domains=["acme.io"],
            entities=["deepweaver"],
            account_type_id=fake.option(labs, "Client")["id"],
            channel_ids=[fake.option(labs, "Referral")["id"]],
            next_touch_on="2026-11-02",
            country="India",
            city="Coimbatore",
            address={"street": "1 Main Road", "postal_code": "641001"},
            website={"primary": {"url": "https://acme.com", "label": "Site"}},
            description="**Big** account",
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["domain"] == "acme.com" and body["additional_domains"] == ["acme.io"]
        assert body["entity_labels"] == ["DeepWeaver"]
        assert body["account_type_label"] == "Client"
        assert body["channel_labels"] == ["Referral"]
        assert body["address"]["street"] == "1 Main Road"
        assert body["next_touch_on"] == "2026-11-02"

    def test_an_option_of_another_list_is_refused(self, crm):
        http, fake, labs, _ = crm
        r = _company(http, account_type_id=fake.option(labs, "Referral")["id"])
        assert r.status_code == 422
        assert r.json()["detail"] == "That account type is not an option in this workspace."

    def test_an_option_of_another_workspace_is_refused(self, crm):
        http, fake, _, academy = crm
        r = _company(http, channel_ids=[fake.option(academy, "Referral")["id"]])
        assert r.status_code == 422

    def test_entity_outside_labs_is_refused(self, crm):
        http, *_ = crm
        r = _company(http, query=ACADEMY, entities=["nunnari_labs"])
        assert r.status_code == 422
        assert r.json()["detail"] == "Entity is not used in Nunnari Academy B2B."

    def test_institution_type_outside_academy_is_refused(self, crm):
        http, fake, _, academy = crm
        option = fake.add(
            "crm_select_options",
            workspace_id=academy["id"],
            list_key="company.institution_type",
            system_key=None,
            label="College",
            color=None,
            position=1,
            is_default=False,
            is_active=True,
            is_fixed=False,
        )
        r = _company(http, institution_type_id=option["id"])
        assert r.status_code == 422
        assert r.json()["detail"] == "Institution type is not used in Nunnari Labs."
        assert _company(http, query=ACADEMY, institution_type_id=option["id"]).status_code == 201

    def test_a_website_must_be_a_web_address(self, crm):
        http, *_ = crm
        r = _company(http, website={"primary": {"url": "javascript:alert(1)"}})
        assert r.status_code == 422
        assert "is not a web address" in r.json()["detail"]

    def test_an_unknown_field_is_named(self, crm):
        http, *_ = crm
        r = _company(http, colour="red")
        assert r.status_code == 422
        assert r.json()["detail"] == "colour is not a field of a company."

    def test_viewers_cannot_create(self, crm):
        http, *_ = crm
        assert _company(http, who=VIEWER["id"]).status_code == 403

    def test_a_crm_only_seller_creates(self, crm):
        http, *_ = crm
        assert _company(http, who=SELLER["id"]).status_code == 201


class TestSystemWritten:
    """FR-CRM-CO-03 — refused 422 on the web and the MCP paths alike."""

    @pytest.mark.parametrize(
        ("field", "label"),
        [
            ("last_inbound_at", "Last inbound"),
            ("last_outbound_at", "Last outbound"),
            ("last_contact_at", "Last contact"),
            ("twenty_id", "Twenty id"),
        ],
    )
    def test_on_create(self, crm, field, label):
        http, *_ = crm
        r = _company(http, **{field: "2026-10-01T09:00:00+05:30"})
        assert r.status_code == 422
        assert r.json()["detail"] == f"{label} is written by the system and cannot be set by hand."

    def test_on_update_naming_every_one(self, crm):
        http, *_ = crm
        made = _person(http).json()
        r = http.patch(
            f"/api/v1/crm/people/{made['id']}{LABS}",
            json={"last_contact_at": "2026-10-01T09:00:00Z", "last_inbound_at": None},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Last inbound and Last contact are written by the system and cannot be set by hand."
        )

    def test_through_mcp(self, crm):
        http, *_ = crm
        with pytest.raises(Exception) as refused:
            asyncio.run(
                tools.crm_create(
                    Ctx(OWNER["id"]),
                    "company",
                    {"name": "Acme", "last_contact_at": "2026-10-01T09:00:00Z"},
                    workspace="labs",
                )
            )
        assert str(refused.value) == (
            "422: Last contact is written by the system and cannot be set by hand."
        )
        made = _company(http).json()
        with pytest.raises(Exception) as refused:
            asyncio.run(
                tools.crm_update(
                    Ctx(OWNER["id"]),
                    "company",
                    made["id"],
                    {"last_outbound_at": "2026-10-01T09:00:00Z"},
                    workspace="labs",
                )
            )
        assert str(refused.value).startswith("422: Last outbound is written by the system")


class TestLeadStage:
    """FR-CRM-PIPE-01, -02; contracts §2.5.1."""

    def test_a_new_company_is_new(self, crm):
        http, *_ = crm
        body = _company(http).json()
        assert (body["lead_stage"], body["lead_stage_label"]) == ("new", "New")

    def test_a_new_company_cannot_start_elsewhere(self, crm):
        http, *_ = crm
        r = _company(http, lead_stage="qualified")
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "A new company starts at New; change its lead stage once it exists."
        )

    @pytest.mark.parametrize("stage", ["contacted", "nurturing", "qualified", "disqualified"])
    def test_moved_by_hand(self, crm, stage):
        http, *_ = crm
        made = _company(http).json()
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"lead_stage": stage},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200 and r.json()["lead_stage"] == stage

    @pytest.mark.parametrize("stage", ["deal_open", "customer"])
    def test_deal_stages_follow_from_deals(self, crm, stage):
        http, *_ = crm
        made = _company(http).json()
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"lead_stage": stage},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"].startswith("Deal open and Customer follow from")


def _partner_type(fake, ws):
    """The seeded Channel partner account type (026), which the fake leaves out."""
    return fake.add(
        "crm_select_options",
        workspace_id=ws["id"],
        list_key="company.account_type",
        system_key="channel_partner",
        label="Channel partner",
        color=None,
        position=3,
        is_default=False,
        is_active=True,
        is_fixed=False,
    )


class TestPartnerByHand:
    """FR-CRM-PIPE-02, amended 2026-10-22; contracts §2.5.1."""

    def _patch(self, http, company_id, body):
        return http.patch(
            f"/api/v1/crm/companies/{company_id}{LABS}", json=body, headers=as_(OWNER["id"])
        )

    def test_partner_needs_a_partner_account_type(self, crm):
        http, fake, labs, _ = crm
        made = _company(http).json()
        r = self._patch(http, made["id"], {"lead_stage": "partner"})
        assert r.status_code == 422
        assert r.json()["detail"].startswith("Partner is for a company whose account type is")
        prospect = fake.option(labs, "Prospect")["id"]
        r = self._patch(http, made["id"], {"account_type_id": prospect, "lead_stage": "partner"})
        assert r.status_code == 422

    def test_partner_is_chosen_by_hand_on_a_partner_account_type(self, crm):
        http, fake, labs, _ = crm
        partner = _partner_type(fake, labs)["id"]
        made = _company(http, account_type_id=partner).json()
        self._patch(http, made["id"], {"lead_stage": "nurturing"})
        r = self._patch(http, made["id"], {"lead_stage": "partner"})
        assert r.status_code == 200, r.text
        assert (r.json()["lead_stage"], r.json()["lead_stage_label"]) == ("partner", "Partner")

    def test_account_type_and_partner_in_one_write(self, crm):
        http, fake, labs, _ = crm
        partner = _partner_type(fake, labs)["id"]
        made = _company(http).json()
        r = self._patch(http, made["id"], {"account_type_id": partner, "lead_stage": "partner"})
        assert r.status_code == 200, r.text
        assert r.json()["lead_stage"] == "partner"

    @pytest.mark.parametrize("to", ["Prospect", None])
    def test_a_partner_keeps_a_partner_account_type(self, crm, to):
        http, fake, labs, _ = crm
        partner = _partner_type(fake, labs)["id"]
        made = _company(http, account_type_id=partner).json()
        self._patch(http, made["id"], {"lead_stage": "partner"})
        other = fake.option(labs, to)["id"] if to else None
        r = self._patch(http, made["id"], {"account_type_id": other})
        assert r.status_code == 422
        assert r.json()["detail"].startswith("A company at Partner keeps a Technology")

    def test_moved_off_partner_then_the_account_type_changes(self, crm):
        http, fake, labs, _ = crm
        partner = _partner_type(fake, labs)["id"]
        made = _company(http, account_type_id=partner).json()
        self._patch(http, made["id"], {"lead_stage": "partner"})
        r = self._patch(http, made["id"], {"lead_stage": "nurturing"})
        assert r.status_code == 200 and r.json()["lead_stage"] == "nurturing"
        prospect = fake.option(labs, "Prospect")["id"]
        r = self._patch(http, made["id"], {"account_type_id": prospect})
        assert r.status_code == 200, r.text

    def test_one_write_moves_off_partner_and_changes_the_type(self, crm):
        http, fake, labs, _ = crm
        partner = _partner_type(fake, labs)["id"]
        made = _company(http, account_type_id=partner).json()
        self._patch(http, made["id"], {"lead_stage": "partner"})
        prospect = fake.option(labs, "Prospect")["id"]
        r = self._patch(http, made["id"], {"lead_stage": "nurturing", "account_type_id": prospect})
        assert r.status_code == 200, r.text

    def test_a_non_partner_type_change_elsewhere_is_unaffected(self, crm):
        http, fake, labs, _ = crm
        made = _company(http).json()
        r = self._patch(http, made["id"], {"account_type_id": fake.option(labs, "Client")["id"]})
        assert r.status_code == 200, r.text

    def test_through_mcp(self, crm):
        http, fake, labs, _ = crm
        partner = _partner_type(fake, labs)["id"]
        made = _company(http, account_type_id=partner).json()
        body = asyncio.run(
            tools.crm_update(
                Ctx(OWNER["id"]), "company", made["id"], {"lead_stage": "partner"}, workspace="labs"
            )
        )
        assert body["lead_stage"] == "partner"


class TestPeople:
    """FR-CRM-PER-01."""

    def test_a_person_needs_a_first_name(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/people{LABS}", json={"last_name": "Rao"}, headers=as_(OWNER["id"])
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A person needs first name."

    def test_fields_and_the_company_name(self, crm):
        http, *_ = crm
        company = _company(http).json()
        r = _person(
            http,
            last_name="Rao",
            company_id=company["id"],
            primary_email="Deepa@Acme.COM",
            additional_emails=["D.Rao@acme.io"],
            phones=[{"number": "98400 12345", "whatsapp": True}],
            job_title="CTO",
            do_not_contact=True,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Deepa Rao" and body["company_name"] == "Acme"
        assert body["primary_email"] == "deepa@acme.com"
        assert body["additional_emails"] == ["d.rao@acme.io"]
        assert body["phones"] == [
            {"country_code": "+91", "number": "98400 12345", "whatsapp": True}
        ]
        assert body["do_not_contact"] is True

    def test_a_bad_email_is_refused(self, crm):
        http, *_ = crm
        r = _person(http, primary_email="not-an-email")
        assert r.status_code == 422
        assert r.json()["detail"].startswith("primary_email:")

    def test_a_company_of_another_workspace_is_not_found(self, crm):
        http, *_ = crm
        other = _company(http, query=ACADEMY).json()
        r = _person(http, company_id=other["id"])
        assert r.status_code == 404
        assert r.json()["detail"] == "No such company."

    def test_the_company_page_lists_its_people(self, crm):
        http, *_ = crm
        company = _company(http).json()
        _person(http, company_id=company["id"])
        page = http.get(f"/api/v1/crm/companies/{company['id']}{LABS}", headers=as_(VIEWER["id"]))
        assert [p["name"] for p in page.json()["related"]["people"]] == ["Deepa"]


class TestExactDuplicates:
    """FR-CRM-DUP-01 — refused 409, naming the record, Trash included."""

    def test_a_domain_clash_names_the_company(self, crm):
        http, *_ = crm
        _company(http, domain="acme.com")
        r = _company(http, name="Acme Two", domain="ACME.com")
        assert r.status_code == 409
        assert r.json()["detail"] == "acme.com already belongs to Acme."

    def test_additional_domains_clash_too(self, crm):
        http, *_ = crm
        _company(http, domain="acme.com", additional_domains=["acme.io"])
        r = _company(http, name="Other", domain="acme.io")
        assert r.status_code == 409
        r = _company(http, name="Other", additional_domains=["acme.com"])
        assert r.status_code == 409

    def test_a_trashed_company_still_holds_its_domain(self, crm):
        http, *_ = crm
        made = _company(http, domain="acme.com").json()
        http.delete(f"/api/v1/crm/companies/{made['id']}{LABS}", headers=as_(OWNER["id"]))
        r = _company(http, name="Acme again", domain="acme.com")
        assert r.status_code == 409
        assert r.json()["detail"] == "acme.com already belongs to Acme (in Trash)."

    def test_another_workspace_does_not_clash(self, crm):
        http, *_ = crm
        _company(http, domain="acme.com")
        assert _company(http, query=ACADEMY, domain="acme.com").status_code == 201

    def test_a_domain_given_twice_is_refused(self, crm):
        http, *_ = crm
        r = _company(http, domain="acme.com", additional_domains=["www.acme.com"])
        assert r.status_code == 422
        assert r.json()["detail"] == "acme.com is listed twice; give each domain once."

    def test_an_email_clash_names_the_person(self, crm):
        http, *_ = crm
        _person(http, last_name="Rao", primary_email="deepa@acme.com")
        r = _person(http, first_name="Dee", primary_email="Deepa@acme.com")
        assert r.status_code == 409
        assert r.json()["detail"] == "deepa@acme.com is already the email of Deepa Rao."

    def test_a_race_the_route_missed_is_still_409(self, crm, monkeypatch):
        """The unique index answers when two requests pass the check at once."""
        http, *_ = crm
        _company(http, domain="acme.com")
        monkeypatch.setattr(store, "list_domains", lambda *a, **k: [])
        r = _company(http, name="Acme Two", domain="acme.com")
        assert r.status_code == 409
        assert r.json()["detail"] == (
            "That domain was just given to another company in this workspace."
        )


class TestSimilarNames:
    """FR-CRM-DUP-02 — shown before saving; the person may continue."""

    def test_check_duplicates_creates_nothing_when_names_are_alike(self, crm):
        http, fake, *_ = crm
        _company(http, name="Acme")
        before = len(fake.rows["crm_companies"])
        r = http.post(
            f"/api/v1/crm/companies{LABS}&check_duplicates=true",
            json={"name": "Acme Corp"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["created"] is False and body["duplicates"]["exact"] is None
        assert [s["name"] for s in body["duplicates"]["similar"]] == ["Acme"]
        assert len(fake.rows["crm_companies"]) == before

    def test_continuing_creates(self, crm):
        http, *_ = crm
        _company(http, name="Acme")
        r = http.post(
            f"/api/v1/crm/companies{LABS}&check_duplicates=false",
            json={"name": "Acme Corp"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201

    def test_unlike_names_create_at_once(self, crm):
        http, *_ = crm
        _company(http, name="Acme")
        r = http.post(
            f"/api/v1/crm/companies{LABS}&check_duplicates=true",
            json={"name": "Zenith Robotics"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201

    def test_the_duplicates_route(self, crm):
        http, *_ = crm
        _company(http, name="Acme", domain="acme.com")
        r = http.post(
            f"/api/v1/crm/companies/duplicates{LABS}",
            json={"name": "Acme Corp", "domain": "https://acme.com/x"},
            headers=as_(MEMBER["id"]),
        )
        body = r.json()
        assert body["exact"]["name"] == "Acme" and body["exact"]["in_trash"] is False
        assert body["similar"][0]["name"] == "Acme"
        assert body["similar"][0]["similarity"] >= dedupe.THRESHOLD

    def test_people_by_full_name(self, crm):
        http, *_ = crm
        _person(http, last_name="Rao")
        r = http.post(
            f"/api/v1/crm/people/duplicates{LABS}",
            json={"name": "Deepa Rao S"},
            headers=as_(OWNER["id"]),
        )
        assert [s["name"] for s in r.json()["similar"]] == ["Deepa Rao"]


class TestUpdate:
    """Contracts §2.5 `PATCH` — a stale edit is refused, never overwritten."""

    def test_a_stale_edit_is_409(self, crm):
        http, *_ = crm
        made = _company(http).json()
        url = f"/api/v1/crm/companies/{made['id']}{LABS}"
        first = http.patch(
            url,
            json={"city": "Chennai", "expect_updated_at": made["updated_at"]},
            headers=as_(OWNER["id"]),
        )
        assert first.status_code == 200
        second = http.patch(
            url,
            json={"city": "Madurai", "expect_updated_at": made["updated_at"]},
            headers=as_(OWNER["id"]),
        )
        assert second.status_code == 409
        assert second.json()["detail"] == (
            "This record changed while you were editing it; reload and try again."
        )

    def test_domains_are_replaced(self, crm):
        http, fake, *_ = crm
        made = _company(http, domain="acme.com", additional_domains=["acme.io"]).json()
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"domain": "acme.in", "expect_updated_at": made["updated_at"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert (r.json()["domain"], r.json()["additional_domains"]) == ("acme.in", ["acme.io"])
        assert r.json()["updated_at"] != made["updated_at"]
        assert sorted(d["domain"] for d in fake.rows["crm_company_domains"].values()) == [
            "acme.in",
            "acme.io",
        ]

    def test_clearing_the_name_is_refused(self, crm):
        http, *_ = crm
        made = _company(http).json()
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"name": None},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A company needs name."

    def test_viewers_cannot_edit(self, crm):
        http, *_ = crm
        made = _company(http).json()
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"city": "X"},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 403


class TestRegistry:
    """The field registry the services and presenter read."""

    def test_system_fields_are_marked(self):
        assert {"last_inbound_at", "last_outbound_at", "last_contact_at", "twenty_id"} <= (
            objects.system_fields("company")
        )
        assert "owner_id" not in objects.system_fields("company")

    @pytest.mark.parametrize(
        ("text", "domain"),
        [
            ("acme.com", "acme.com"),
            ("https://www.Acme.COM/about?x=1", "acme.com"),
            ("deepa@acme.co.in", "acme.co.in"),
            ("acme", None),
            ("ac me.com", None),
        ],
    )
    def test_domains_are_normalised(self, text, domain):
        assert dedupe.normalise_domain(text) == domain
