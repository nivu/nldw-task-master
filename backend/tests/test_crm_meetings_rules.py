"""Meeting capture's rules — spec 009 FR-CRMAI-MEET-02 to -06, -SUM-12;
Q-10, Q-11, Q-26; data-model §3.2.1, §3.4. Pure functions of
`app/domain/crm/meetings.py`, and the output model of `app/schemas/crm.py`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.crm import meetings as rules
from app.domain.crm import review as review_rules
from app.schemas.crm import MeetingOutput

OURS = ["nunnarilabs.com"]
ACME, BETA, PRIYA, RAVI = "c-acme", "c-beta", "p-priya", "p-ravi"
PILOT, ROLLOUT = "d-pilot", "d-rollout"
ROLES = {"u-asha": "member", "u-bala": "member", "u-admin": "admin", "u-dev": "viewer"}
ACTIVE = {"u-asha", "u-bala", "u-admin", "u-dev"}
TEAM = [
    {"user_id": "u-asha", "name": "Asha", "email": "asha@nunnarilabs.com"},
    {"user_id": "u-bala", "name": "Bala", "email": "bala@nunnarilabs.com"},
]
PEOPLE = [
    {"id": PRIYA, "primary_email": "Priya@Acme.in", "additional_emails": [], "company_id": ACME},
    {
        "id": RAVI,
        "primary_email": "ravi@beta.in",
        "additional_emails": ["ravi.k@acme.in"],
        "company_id": BETA,
    },
    {
        "id": "p-gone",
        "primary_email": "gone@acme.in",
        "additional_emails": [],
        "deleted_at": "2026-10-01T00:00:00+00:00",
    },
]


def _deal(deal_id, company_id=ACME, stage="discovery", **values):
    return {"id": deal_id, "company_id": company_id, "stage": stage, "owner_id": "u-asha", **values}


def _items(**overrides):
    given = {
        "transcript_id": "t1",
        "version": "2026-10-18T09:30:00+00:00",
        "model": "claude-sonnet-5-5",
        "generated_at": "2026-10-18T12:00:00+00:00",
        "next_step": None,
        "next_step_on": None,
        "actions": [],
        "deal": None,
        "choices": [],
        "companies": [{"id": ACME, "owner_id": "u-bala"}],
        "people": [{"id": PRIYA, "owner_id": None}],
        "roles": ROLES,
        "active": ACTIVE,
    }
    return rules.items(**{**given, **overrides})


class TestMatching:
    """FR-CRMAI-MEET-02, -03; Q-10, Q-26."""

    def test_rules_our_own_domains_are_internal(self):
        found = rules.external(
            ["asha@nunnarilabs.com", "Priya@Acme.in", "", "bala@NunnariLabs.com"], OURS
        )
        assert found == ["priya@acme.in"]

    def test_rules_internal_only_meeting_matches_nobody(self):
        internal = ["asha@nunnarilabs.com", "bala@nunnarilabs.com"]
        assert rules.match(internal, OURS, PEOPLE) == []

    def test_rules_a_person_matches_by_primary_or_additional_email(self):
        found = rules.match(["priya@acme.in", "ravi.k@acme.in"], OURS, PEOPLE)
        assert [p["id"] for p in found] == [PRIYA, RAVI]

    def test_rules_unknown_externals_and_people_in_the_trash_match_nobody(self):
        assert rules.match(["stranger@acme.in", "gone@acme.in"], OURS, PEOPLE) == []

    def test_rules_an_internal_address_never_matches_a_person(self):
        # Even a CRM person holding an address in our own domains.
        ours = [{"id": "p-us", "primary_email": "asha@nunnarilabs.com"}]
        assert rules.match(["asha@nunnarilabs.com"], OURS, ours) == []

    def test_rules_placement_nowhere_one_or_several(self):
        assert rules.placement({"labs": [], "academy": []}) == ("none", None)
        assert rules.placement({"labs": [{"id": PRIYA}], "academy": []}) == ("one", "labs")
        assert rules.placement({"labs": [{"id": PRIYA}], "academy": [{"id": RAVI}]}) == (
            "several",
            None,
        )


class TestTheDeal:
    """FR-CRMAI-MEET-04."""

    def test_rules_exactly_one_open_deal_is_the_note_s_deal(self):
        deal, choices = rules.resolve_deal(
            [_deal(PILOT), _deal("d-won", stage="closed_won"), _deal("d-x", deleted_at="x")]
        )
        assert deal["id"] == PILOT and choices == []

    def test_rules_two_open_deals_are_choices(self):
        deal, choices = rules.resolve_deal([_deal(ROLLOUT), _deal(PILOT, company_id=BETA)])
        assert deal is None
        assert [d["id"] for d in choices] == [PILOT, ROLLOUT]

    def test_rules_no_open_deal_no_deal_and_no_choice(self):
        assert rules.resolve_deal([_deal(PILOT, stage="closed_lost")]) == (None, [])


class TestTheNote:
    """FR-CRMAI-MEET-05; Q-11: plain text under fixed headings."""

    def test_rules_the_note_body_layout(self):
        body = rules.note_body(
            title="Pilot review",
            attendees=["asha@nunnarilabs.com", "priya@acme.in"],
            summary="Priya asked for a pilot plan.\nAsha will send it.",
            decisions=["Start with two plants"],
            actions=[
                {"text": "Send the pilot plan", "owner_id": "u-asha", "due_on": "2026-10-24"},
                {"text": "Share references", "owner_id": None, "due_on": None},
            ],
            owner_names={"u-asha": "Asha"},
            transcript_url="https://app.fireflies.ai/view/t1",
        )
        assert body == (
            "Meeting: Pilot review\n"
            "\n"
            "Attendees\n"
            "- asha@nunnarilabs.com\n"
            "- priya@acme.in\n"
            "\n"
            "Summary\n"
            "Priya asked for a pilot plan.\n"
            "Asha will send it.\n"
            "\n"
            "Decisions\n"
            "- Start with two plants\n"
            "\n"
            "Action items\n"
            "- Send the pilot plan (owner: Asha; due 2026-10-24)\n"
            "- Share references (owner: unknown)\n"
            "\n"
            "Transcript\n"
            "https://app.fireflies.ai/view/t1"
        )

    def test_rules_an_empty_meeting_still_has_every_heading(self):
        body = rules.note_body(
            title=" ",
            attendees=[],
            summary="A short call.",
            decisions=[],
            actions=[],
            owner_names={},
            transcript_url=None,
        )
        lines = body.splitlines()
        assert lines[0] == "Meeting: (untitled)"
        for heading in ("Attendees", "Summary", "Decisions", "Action items", "Transcript"):
            assert heading in lines
        assert lines[-1] == "(no link)"
        assert "<" not in body and "*" not in body and "#" not in body

    def test_rules_money_in_the_title_is_masked(self):
        body = rules.note_body(
            title="Pricing call ₹ 4,50,000",
            attendees=[],
            summary="Pricing.",
            decisions=[],
            actions=[],
            owner_names={},
            transcript_url=None,
        )
        assert "4,50,000" not in body.splitlines()[0]

    def test_rules_links_in_the_title_and_attendees_are_dropped(self):
        """Q-37: the transcript link is the only URL a meeting note stores;
        the invite's title and attendees are written by outsiders."""
        body = rules.note_body(
            title="Kickoff - verify access at https://acme-sso.evil.example/login",
            attendees=["priya@acme.in", "Priya (see acme-sso.co/login)", "www.phish.example/x"],
            summary="Kickoff.",
            decisions=[],
            actions=[],
            owner_names={},
            transcript_url="https://app.fireflies.ai/view/t1",
        )
        lines = body.splitlines()
        assert lines[0] == "Meeting: Kickoff - verify access at"
        assert "- priya@acme.in" in lines and "- Priya (see )" in lines
        assert "evil" not in body and "acme-sso" not in body and "phish" not in body
        assert [w for w in body.split() if "/" in w] == ["https://app.fireflies.ai/view/t1"]

    def test_rules_long_attendee_lists_are_cut(self):
        many = [f"p{i:02}@acme.in" for i in range(rules.ATTENDEES_SHOWN + 3)]
        body = rules.note_body(
            title="All hands",
            attendees=many,
            summary="Big call.",
            decisions=[],
            actions=[],
            owner_names={},
            transcript_url=None,
        )
        assert "- and 3 more" in body
        assert many[-1] not in body

    def test_rules_only_a_fireflies_link_is_kept(self):
        assert rules.transcript_link("https://app.fireflies.ai/view/t1") == (
            "https://app.fireflies.ai/view/t1"
        )
        for url in ("https://evil.example/view/t1", "javascript:alert(1)", None, 7):
            assert rules.transcript_link(url) is None

    def test_rules_the_note_is_dated_the_meeting_day_in_india(self):
        assert rules.meeting_day("2026-10-17T20:00:00+00:00") == "2026-10-18"
        assert rules.source_ref("t1") == "fireflies:t1"

    def test_rules_action_owners_resolve_to_members_or_unknown(self):
        out = rules.action_items(
            [
                {"text": "Send", "owner": "1", "due_on": None},
                {"text": "Call", "owner": "Somebody", "due_on": "2026-10-24"},
            ],
            TEAM,
        )
        assert [(a["owner_id"], a["owner_unknown"]) for a in out] == [
            ("u-asha", False),
            (None, True),
        ]


class TestItems:
    """FR-CRMAI-MEET-04, -06; data-model §3.2.1."""

    def test_rules_each_action_item_is_a_task_on_the_deal(self):
        deal = _deal(PILOT, owner_id="u-bala")
        out = _items(
            deal=deal,
            actions=[
                {"text": "Send the pilot plan", "owner_id": "u-asha", "due_on": "2026-10-24"},
                {"text": "Share references", "owner_id": None, "due_on": None},
            ],
        )
        tasks = [i for i in out if i["type"] == "task_from_meeting"]
        assert [(t["record_type"], t["record_id"]) for t in tasks] == [("opportunity", PILOT)] * 2
        assert [t["owner_id"] for t in tasks] == ["u-asha", "u-bala"]
        assert tasks[0]["source_kind"] == "meeting"
        assert tasks[0]["source_key"] == "fireflies:t1"
        fingerprint = review_rules.commitment_fingerprint("Send the pilot plan", "2026-10-24")
        assert tasks[0]["dedupe_key"] == (
            f"meeting|fireflies:t1|task_from_meeting|opportunity:{PILOT}#{fingerprint}"
        )

    def test_rules_without_a_deal_actions_land_on_the_company_then_the_person(self):
        action = [{"text": "Send", "owner_id": None, "due_on": None}]
        [on_company] = _items(actions=action)
        assert (on_company["record_type"], on_company["record_id"]) == ("company", ACME)
        assert on_company["owner_id"] == "u-bala"
        [on_person] = _items(actions=action, companies=[])
        assert (on_person["record_type"], on_person["record_id"]) == ("person", PRIYA)
        assert on_person["owner_id"] is None

    def test_rules_a_changed_next_step_is_suggested(self):
        deal = _deal(PILOT, next_step="Follow up", next_step_on="2026-10-21")
        [step] = _items(deal=deal, next_step="Send the plan", next_step_on="2026-10-24")
        assert step["type"] == "next_step_change"
        assert step["suggestion"] == {"next_step": "Send the plan", "next_step_on": "2026-10-24"}
        assert step["current_values"] == {
            PILOT: {"next_step": "Follow up", "next_step_on": "2026-10-21"}
        }
        assert step["owner_id"] == "u-asha"

    def test_rules_the_same_next_step_is_not_suggested(self):
        deal = _deal(PILOT, next_step="Follow up", next_step_on="2026-10-21")
        assert _items(deal=deal, next_step="Follow up", next_step_on="2026-10-21") == []
        assert _items(deal=deal, next_step=None) == []

    def test_rules_no_single_deal_asks_which_deal(self):
        choices = [_deal(ROLLOUT), _deal(PILOT)]
        [ask] = _items(choices=choices, next_step="Send the plan")
        assert ask["type"] == "deal_link"
        assert ask["record_type"] == "note"
        assert ask["deal_choices"] == [PILOT, ROLLOUT]
        assert ask["dedupe_key"] == "meeting|fireflies:t1|deal_link|note:$note"
        # The company owner decides.
        assert ask["owner_id"] == "u-bala"

    def test_rules_no_open_deal_at_all_asks_nothing(self):
        assert _items() == []


class TestOutput:
    """FR-CRMAI-MEET-05: the model's answer, validated before anything is saved."""

    ANSWER = {
        "summary": "One.\nTwo.",
        "decisions": ["Pilot first"],
        "action_items": [{"text": "Send", "owner": "1", "due_on": "2026-10-24"}],
        "next_step": None,
        "next_step_on": None,
    }

    def test_rules_a_valid_answer(self):
        out = MeetingOutput.model_validate(self.ANSWER)
        assert out.summary == "One.\nTwo."

    @pytest.mark.parametrize(
        "change",
        [
            {"summary": "1\n2\n3\n4\n5\n6"},
            {"summary": " "},
            {"decisions": ["x" * 201]},
            {"action_items": [{"text": "x" * 201, "owner": "1", "due_on": None}]},
            {"extra": "field"},
        ],
        ids=["six-lines", "empty", "long-decision", "long-action", "extra"],
    )
    def test_rules_an_invalid_answer_is_refused(self, change):
        with pytest.raises(ValidationError):
            MeetingOutput.model_validate({**self.ANSWER, **change})
