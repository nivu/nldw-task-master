"""AI fields' routes — spec 009 FR-CRMAI-FIELD-01, -04 to -06,
FR-CRMAI-FLAG-03, FR-CRMAI-MCP-04; Q-27, Q-28, Q-38; D-3; contracts §1,
§2.5:

- the definitions are for Admins only, with the spec's attributes only;
  a choice field needs 2 to 12 options; a name is used once per record
  type; the record and output types are fixed once a field exists; every
  change is audited; there is no MCP tool and no DELETE;
- a company's or deal's record read carries its `ai_fields` while phase 3
  is on, for Viewers too, and a disabled field is hidden;
- a number is shown on the record only: no AI field is a view field, so
  none is sorted, filtered or ranked (Q-27, Q-28);
- the recompute route is for Admins and Members (a Viewer gets 403): it
  marks the record's values, queued now or at the end of their hour.

Routes run against `tests/crm_fake.py`, with the job of
`test_crm_ai_fields_job.py` and its fakes.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, as_
from test_crm_ai_fields_job import ANSWERS, API, LABS, NOW, Fields

from app.domain.crm import objects
from app.mcp import crm as tools

CONTRACT_KEYS = {"id", "key", "name", "record_type", "prompt", "output_type", "options", "enabled"}


@pytest.fixture
def fields(monkeypatch) -> Fields:
    return Fields(monkeypatch)


def _list(fields: Fields, who=OWNER):
    return fields.http.get(f"{API}/ai-fields", params=LABS, headers=as_(who["id"]))


def _create(fields: Fields, body: dict, who=OWNER):
    return fields.http.post(f"{API}/ai-fields", params=LABS, json=body, headers=as_(who["id"]))


def _patch(fields: Fields, field_id: str, body: dict, who=OWNER):
    return fields.http.patch(
        f"{API}/ai-fields/{field_id}", params=LABS, json=body, headers=as_(who["id"])
    )


def _get(fields: Fields, plural: str, record: dict, who=OWNER) -> dict:
    r = fields.http.get(f"{API}/{plural}/{record['id']}", params=LABS, headers=as_(who["id"]))
    assert r.status_code == 200, r.text
    return r.json()


def _recompute(fields: Fields, plural: str, record: dict, who=OWNER):
    return fields.http.post(
        f"{API}/{plural}/{record['id']}/ai-fields/recompute", params=LABS, headers=as_(who["id"])
    )


RISK = {
    "name": "Renewal risk",
    "record_type": "opportunity",
    "prompt": "How likely this deal is to slip past its close date, from 0 to 100.",
    "output_type": "number",
}


class TestDefinitions:
    """FR-CRMAI-FIELD-01; contracts §2.5; D-3."""

    @pytest.mark.parametrize("who", [MEMBER, VIEWER])
    def test_ai_fields_definitions_are_for_admins_only(self, fields, who):
        assert _list(fields, who).status_code == 403
        assert _create(fields, RISK, who).status_code == 403
        field = fields.fake.ai_field(fields.labs, "deal_outlook")
        r = _patch(fields, field["id"], {"enabled": False}, who)
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a workspace admin can do that."

    def test_ai_fields_the_seeds_with_the_spec_attributes_only(self, fields):
        r = _list(fields)
        assert r.status_code == 200, r.text
        rows = r.json()
        assert [f["key"] for f in rows] == [
            "icp_fit",
            "account_brief",
            "deal_outlook",
            "what_is_blocking",
        ]
        assert all(set(f) == CONTRACT_KEYS for f in rows)
        assert rows[0]["options"] == ["Factory Brain", "Academy", "Services", "DeepWeaver", "None"]

    def test_ai_fields_academy_has_no_icp_fit(self, fields):
        r = fields.http.get(
            f"{API}/ai-fields", params={"workspace": "academy"}, headers=as_(OWNER["id"])
        )
        assert "icp_fit" not in {f["key"] for f in r.json()}

    def test_ai_fields_an_admin_creates_a_field_audited(self, fields):
        r = _create(fields, {**RISK, "name": "  Renewal risk  "})
        assert r.status_code == 201, r.text
        made = r.json()
        assert set(made) == CONTRACT_KEYS
        assert (made["key"], made["name"], made["options"], made["enabled"]) == (
            "renewal_risk",
            "Renewal risk",
            [],
            True,
        )
        row = fields.fake.rows["crm_ai_fields"][made["id"]]
        assert (row["max_lines"], row["depends_on"], row["with_reason"]) == (None, None, False)
        assert row["position"] == 5
        [audit] = [a for a in fields.fake.audit if a["action"] == "ai_field.created"]
        assert (audit["actor_id"], audit["target_id"]) == (OWNER["id"], made["id"])

    @pytest.mark.parametrize("options", [None, [], ["Hot"], [f"o{i}" for i in range(13)]])
    def test_ai_fields_a_choice_field_needs_two_to_twelve_options(self, fields, options):
        body = {**RISK, "output_type": "single_select", "options": options}
        r = _create(fields, body)
        assert r.status_code == 422
        assert r.json()["detail"] == "A choice field needs 2 to 12 options."

    def test_ai_fields_only_a_choice_field_has_options(self, fields):
        r = _create(fields, {**RISK, "options": ["Low", "High"]})
        assert r.status_code == 422
        assert r.json()["detail"] == "Only a choice field has options."

    def test_ai_fields_a_name_is_used_once_per_record_type(self, fields):
        r = _create(fields, {**RISK, "name": "deal OUTLOOK"})
        assert r.status_code == 409
        assert r.json()["detail"] == "There is already an AI field with that name on deals."
        r = _create(fields, {**RISK, "name": "Deal outlook", "record_type": "company"})
        assert r.status_code == 201, r.text
        assert r.json()["key"] == "deal_outlook_2"

    def test_ai_fields_an_admin_edits_name_prompt_options_and_enabled(self, fields):
        outlook = fields.fake.ai_field(fields.labs, "deal_outlook")
        body = {
            "name": "Outlook",
            "prompt": "How is this deal going?",
            "options": ["On track", "At risk", "Stalled", "Won soon"],
            "enabled": False,
        }
        r = _patch(fields, outlook["id"], body)
        assert r.status_code == 200, r.text
        assert {k: r.json()[k] for k in body} == body
        [audit] = [a for a in fields.fake.audit if a["action"] == "ai_field.updated"]
        assert audit["after"]["enabled"] is False

    @pytest.mark.parametrize("body", [{"record_type": "company"}, {"output_type": "text"}])
    def test_ai_fields_record_and_output_types_are_fixed(self, fields, body):
        outlook = fields.fake.ai_field(fields.labs, "deal_outlook")
        assert _patch(fields, outlook["id"], body).status_code == 422

    @pytest.mark.parametrize("body", [{"max_lines": 5}, {"with_reason": False}, {"position": 1}])
    def test_ai_fields_the_seed_only_attributes_are_not_editable(self, fields, body):
        outlook = fields.fake.ai_field(fields.labs, "deal_outlook")
        assert _patch(fields, outlook["id"], body).status_code == 422

    def test_ai_fields_options_follow_the_fields_type_on_edit(self, fields):
        outlook = fields.fake.ai_field(fields.labs, "deal_outlook")
        r = _patch(fields, outlook["id"], {"options": ["Only"]})
        assert r.json()["detail"] == "A choice field needs 2 to 12 options."
        brief = fields.fake.ai_field(fields.labs, "account_brief")
        r = _patch(fields, brief["id"], {"options": ["A", "B"]})
        assert r.json()["detail"] == "Only a choice field has options."

    def test_ai_fields_renaming_onto_another_name_is_refused(self, fields):
        blocking = fields.fake.ai_field(fields.labs, "what_is_blocking")
        r = _patch(fields, blocking["id"], {"name": "Deal Outlook"})
        assert r.status_code == 409
        assert _patch(fields, blocking["id"], {"name": "What is blocking"}).status_code == 200

    @pytest.mark.parametrize("field_id", ["not-an-id", str(uuid.uuid4())])
    def test_ai_fields_an_unknown_field_is_not_found(self, fields, field_id):
        r = _patch(fields, field_id, {"enabled": False})
        assert r.status_code == 404 and r.json()["detail"] == "No such AI field."

    def test_ai_fields_there_is_no_delete(self, fields):
        outlook = fields.fake.ai_field(fields.labs, "deal_outlook")
        r = fields.http.delete(
            f"{API}/ai-fields/{outlook['id']}", params=LABS, headers=as_(OWNER["id"])
        )
        assert r.status_code == 405

    def test_ai_fields_definitions_are_web_only(self):
        assert not [n for n in dir(tools) if n.startswith("crm_") and "field" in n]


class TestTheRecordRead:
    """Contracts §1; FR-CRMAI-FIELD-05, -06, FR-CRMAI-FLAG-03."""

    def test_ai_fields_a_company_and_a_deal_carry_their_values(self, fields):
        fields.tick()
        company = _get(fields, "companies", fields.acme)["ai_fields"]
        assert [(f["key"], f["output_type"], f["value"]) for f in company] == [
            ("icp_fit", "single_select", "Factory Brain"),
            ("account_brief", "text", ANSWERS["Account brief"]["value"]),
        ]
        assert company[0]["ai"] == {
            "source_kind": "ai_field",
            "model": "claude-sonnet-5-5",
            "generated_at": NOW.isoformat(),
            "source": None,
        }
        assert "reason" not in company[0]
        deal = {f["key"]: f for f in _get(fields, "opportunities", fields.deal)["ai_fields"]}
        assert (deal["deal_outlook"]["value"], deal["deal_outlook"]["reason"]) == (
            "At risk",
            "No reply since the proposal.",
        )
        assert deal["deal_outlook"]["name"] == "Deal outlook"

    def test_ai_fields_before_the_first_tick_the_fields_are_empty(self, fields):
        shown = _get(fields, "companies", fields.acme)["ai_fields"]
        assert [(f["value"], f["ai"]) for f in shown] == [(None, None), (None, None)]

    def test_ai_fields_viewers_see_the_values(self, fields):
        fields.tick()
        shown = _get(fields, "opportunities", fields.deal, who=VIEWER)
        assert {f["key"]: f["value"] for f in shown["ai_fields"]}["deal_outlook"] == "At risk"
        assert "amount" not in shown["record"]

    def test_ai_fields_hidden_while_phase_3_is_off(self, fields):
        fields.tick()
        fields.fake.set_ai(fields.labs, budget_usd=10, phases={"3": False})
        assert "ai_fields" not in _get(fields, "companies", fields.acme)
        assert "ai_fields" not in _get(fields, "opportunities", fields.deal)

    def test_ai_fields_a_disabled_field_is_hidden_and_its_value_kept(self, fields):
        fields.tick()
        icp = fields.fake.ai_field(fields.labs, "icp_fit")
        assert _patch(fields, icp["id"], {"enabled": False}).status_code == 200
        shown = _get(fields, "companies", fields.acme)["ai_fields"]
        assert [f["key"] for f in shown] == ["account_brief"]
        assert fields.value(fields.acme, "icp_fit")["value_text"] == "Factory Brain"

    def test_ai_fields_a_person_has_none(self, fields):
        person = fields.post("people", {"first_name": "Priya", "company_id": fields.acme["id"]})
        assert "ai_fields" not in _get(fields, "people", person)

    def test_ai_fields_a_number_is_shown_on_the_record_only(self, fields):
        made = _create(fields, RISK).json()
        fields.answer({**ANSWERS, "Renewal risk": {"value": 70}})
        fields.tick()
        shown = {f["key"]: f for f in _get(fields, "opportunities", fields.deal)["ai_fields"]}
        assert (shown["renewal_risk"]["output_type"], shown["renewal_risk"]["value"]) == (
            "number",
            70,
        )
        # Q-27, Q-28: no AI field is a view field, so none can be a column,
        # a filter, a sort or a grouping.
        names = {f.field for kind in ("company", "opportunity") for f in objects.VIEW_FIELDS[kind]}
        assert names.isdisjoint({made["key"], "icp_fit", "account_brief", "deal_outlook"})
        assert names.isdisjoint({"what_is_blocking", "ai_fields"})
        r = fields.http.post(
            f"{API}/query",
            params=LABS,
            json={
                "object": "opportunities",
                "spec": {"sort": [{"field": "renewal_risk", "direction": "desc"}]},
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422, r.text


class TestRecompute:
    """FR-CRMAI-FIELD-04, Q-38; contracts §2.5."""

    def test_ai_fields_recompute_a_viewer_is_refused(self, fields):
        r = _recompute(fields, "companies", fields.acme, who=VIEWER)
        assert r.status_code == 403
        assert r.json()["detail"] == "Viewers cannot change records."

    def test_ai_fields_recompute_a_member_queues_it_now(self, fields):
        fields.tick()
        later = NOW + timedelta(hours=2)
        fields.monkeypatch.setattr("app.services.crm.ai_fields._now", lambda: later)
        r = _recompute(fields, "opportunities", fields.deal, who=MEMBER)
        assert r.status_code == 200, r.text
        assert r.json() == {"queued": True, "next_allowed_at": None}
        assert fields.value(fields.deal, "deal_outlook")["dirty_at"] == later.isoformat()
        calls = len(fields.claude.calls)
        fields.tick(later)
        # Nothing changed, but asked for: recomputed, the company untouched.
        assert fields.claude.fields[calls:] == ["Deal outlook", "What is blocking"]
        assert fields.value(fields.deal, "deal_outlook")["dirty_at"] is None

    def test_ai_fields_recompute_inside_the_hour_waits_for_its_end(self, fields):
        fields.tick()
        r = _recompute(fields, "companies", fields.acme, who=MEMBER)
        assert r.json() == {
            "queued": False,
            "next_allowed_at": (NOW + timedelta(hours=1)).isoformat(),
        }
        calls = len(fields.claude.calls)
        fields.tick(NOW + timedelta(minutes=30))
        assert len(fields.claude.calls) == calls
        fields.tick(NOW + timedelta(hours=1))
        assert fields.claude.fields[calls:] == ["ICP fit", "Account brief"]

    def test_ai_fields_recompute_before_any_value_is_queued(self, fields):
        r = _recompute(fields, "companies", fields.acme)
        assert r.json() == {"queued": True, "next_allowed_at": None}

    @pytest.mark.parametrize("record_id", ["not-an-id", str(uuid.uuid4())])
    def test_ai_fields_recompute_an_unknown_record_is_not_found(self, fields, record_id):
        r = _recompute(fields, "companies", {"id": record_id})
        assert r.status_code == 404 and r.json()["detail"] == "No such company."
        r = _recompute(fields, "opportunities", {"id": record_id})
        assert r.status_code == 404 and r.json()["detail"] == "No such opportunity."

    def test_ai_fields_recompute_a_record_in_the_trash_is_not_found(self, fields):
        fields.fake.rows["crm_companies"][fields.acme["id"]]["deleted_at"] = NOW.isoformat()
        assert _recompute(fields, "companies", fields.acme).status_code == 404
