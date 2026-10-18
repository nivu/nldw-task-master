"""Email thread summaries' rules — spec 009 FR-CRMAI-SUM-01, -03, -04, -06,
-07, -12; data-model §3.2.2, §3.3. Pure functions of
`app/domain/crm/summaries.py`, and the output model of
`app/schemas/crm.py`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.crm import review as review_rules
from app.domain.crm import summaries as rules
from app.schemas.crm import SummaryOutput

ACME, PRIYA, PILOT, ROLLOUT = "c-acme", "p-priya", "d-pilot", "d-rollout"
ROLES = {"u-asha": "member", "u-bala": "member", "u-admin": "admin", "u-dev": "viewer"}
ACTIVE = {"u-asha", "u-bala", "u-admin", "u-dev"}
TEAM = [
    {"user_id": "u-asha", "name": "Asha", "email": "asha@nunnarilabs.com"},
    {"user_id": "u-bala", "name": "Bala", "email": "bala@nunnarilabs.com"},
    {"user_id": "u-bala2", "name": "Bala", "email": "bala.k@nunnarilabs.com"},
]


def _msg(gmail_id, **values):
    return {
        "gmail_message_id": gmail_id,
        "sent_at": "2026-10-16T09:00:00+00:00",
        "is_internal": False,
        "is_auto": False,
        "links": [{"record_type": "company", "record_id": ACME}],
        **values,
    }


class TestQualifying:
    """FR-CRMAI-SUM-01."""

    def test_rules_internal_auto_and_unlinked_messages_do_not_qualify(self):
        kept = rules.qualifying(
            [
                _msg("g3", sent_at="2026-10-16T11:00:00+00:00"),
                _msg("g1"),
                _msg("internal", is_internal=True),
                _msg("auto", is_auto=True),
                _msg("deal-only", links=[{"record_type": "opportunity", "record_id": PILOT}]),
                _msg("person", links=[{"record_type": "person", "record_id": PRIYA}]),
            ]
        )
        assert [m["gmail_message_id"] for m in kept] == ["g1", "person", "g3"]


class TestStrip:
    """FR-CRMAI-SUM-03 — quoted history and signatures go before the prompt."""

    @pytest.mark.parametrize(
        "quoted",
        [
            "On Mon, 12 Oct 2026 at 10:00, Asha <asha@nunnarilabs.com> wrote:\n> QUOTED",
            "> QUOTED\n> more",
            "-----Original Message-----\nFrom: Asha\nQUOTED",
            "From: Asha <asha@nunnarilabs.com>\nSent: Monday\nTo: Priya\nSubject: Re\n\nQUOTED",
            "---------- Forwarded message ---------\nFrom: X\nQUOTED",
        ],
    )
    def test_rules_quoted_history_is_removed(self, quoted):
        assert rules.strip(f"Please send the plan.\n\n{quoted}") == "Please send the plan."

    @pytest.mark.parametrize(
        "signature",
        [
            "-- \nPriya Rao\nHead of Ops\n+91 98765 43210",
            "Best regards,\nPriya Rao\nAcme",
            "Thanks,\nPriya",
            "Sent from my iPhone",
        ],
    )
    def test_rules_signature_markers_are_removed(self, signature):
        assert rules.strip(f"Please send the plan.\n\n{signature}") == "Please send the plan."

    def test_rules_a_from_line_in_the_body_is_kept(self):
        text = "From: next week we start.\nPlease confirm."
        assert rules.strip(text) == text

    def test_rules_thanks_early_in_a_long_message_is_kept(self):
        body = "Thanks,\n" + "\n".join(f"Line {i}" for i in range(12))
        assert rules.strip(body) == body


class TestPrompt:
    """FR-CRMAI-SUM-12 — the team list, numbered; who sent each message."""

    def test_rules_roster_is_active_admins_and_members_by_name(self):
        members = [
            {"user_id": "u-bala", "role": "member"},
            {"user_id": "u-asha", "role": "admin"},
            {"user_id": "u-dev", "role": "viewer"},
            {"user_id": "u-gone", "role": "member"},
        ]
        profiles = {
            "u-bala": {"display_name": "Bala", "email": "b@x", "is_active": True},
            "u-asha": {"display_name": "Asha", "email": "a@x", "is_active": True},
            "u-dev": {"display_name": "Dev", "email": "d@x", "is_active": True},
            "u-gone": {"display_name": "Gowri", "email": "g@x", "is_active": False},
        }
        assert [p["user_id"] for p in rules.roster(members, profiles)] == ["u-asha", "u-bala"]

    def test_rules_prompt_parts_list_the_team_then_each_message(self):
        parts = rules.prompt_parts(
            [{"direction": "inbound"}, {"direction": "outbound"}], ["Hi", "Hello"], TEAM[:2]
        )
        assert parts == [
            ("Nunnari team", "1. Asha <asha@nunnarilabs.com>\n2. Bala <bala@nunnarilabs.com>"),
            ("Message 1 from client (inbound)", "Hi"),
            ("Message 2 from Nunnari (outbound)", "Hello"),
        ]


class TestOwners:
    """FR-CRMAI-SUM-12 — an owner is a member of the workspace, or unknown."""

    @pytest.mark.parametrize(
        ("answer", "owner"),
        [
            ("1", "u-asha"),
            ("asha@nunnarilabs.com", "u-asha"),
            ("Asha", "u-asha"),
            ("Bala", None),  # two people are called Bala: ambiguous
            ("Vinita", None),  # another workspace's person is never listed
            ("9", None),
            ("unknown", None),
        ],
    )
    def test_rules_owner_resolution(self, answer, owner):
        [c] = rules.commitments([{"text": "Send", "owner": answer, "due_on": None}], TEAM)
        assert c["owner_id"] == owner
        assert c["owner_unknown"] is (owner is None)


def _items(**overrides):
    values = {
        "mailbox_id": "mb1",
        "thread_id": "t1",
        "watermark_id": "g1",
        "model": "claude-sonnet-5-5",
        "generated_at": "2026-10-18T06:00:00+00:00",
        "next_step": "Send the plan",
        "next_step_on": "2026-10-24",
        "stored_commitments": [
            {"text": "Send the plan", "owner_id": "u-bala", "due_on": "2026-10-24"},
            {"text": "Share references", "owner_id": None, "due_on": None},
        ],
        "deals": [
            {
                "id": PILOT,
                "owner_id": "u-asha",
                "company_id": ACME,
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            }
        ],
        "companies": [{"id": ACME, "owner_id": "u-asha"}],
        "people": [{"id": PRIYA}],
        "mailbox_owner": "u-asha",
        "roles": ROLES,
        "active": ACTIVE,
    }
    return rules.items(**{**values, **overrides})


class TestItems:
    """FR-CRMAI-SUM-06, -07; Q-07."""

    def test_rules_items_only_for_dated_commitments(self):
        tasks = [i for i in _items() if i["type"] == "task_from_commitment"]
        assert [t["suggestion"]["title"] for t in tasks] == ["Send the plan"]
        assert tasks[0]["owner_id"] == "u-bala"
        assert (tasks[0]["record_type"], tasks[0]["record_id"]) == ("opportunity", PILOT)

    def test_rules_a_re_summary_does_not_repeat_a_commitment(self):
        first = [i for i in _items() if i["type"] == "task_from_commitment"]
        later = [
            i
            for i in _items(
                watermark_id="g7",
                stored_commitments=[
                    {"text": "  send the   PLAN ", "owner_id": "u-bala", "due_on": "2026-10-24"}
                ],
            )
            if i["type"] == "task_from_commitment"
        ]
        # Same key and same source version: crm_review_upsert finds it.
        assert (later[0]["dedupe_key"], later[0]["source_version"]) == (
            first[0]["dedupe_key"],
            first[0]["source_version"],
        )
        assert first[0]["dedupe_key"].endswith(
            "#" + review_rules.commitment_fingerprint("Send the plan", "2026-10-24")
        )

    def test_rules_next_step_item_when_the_deal_differs(self):
        [step] = [i for i in _items() if i["type"] == "next_step_change"]
        assert step["owner_id"] == "u-asha"
        assert step["deal_choices"] == [PILOT]
        assert step["source_version"] == "g1"
        assert step["current_values"] == {
            PILOT: {"next_step": "Call", "next_step_on": "2026-10-20"}
        }

    def test_rules_no_next_step_item_when_it_is_the_same(self):
        deal = {
            "id": PILOT,
            "owner_id": "u-asha",
            "company_id": ACME,
            "next_step": "Send the plan",
            "next_step_on": "2026-10-24",
        }
        assert not [i for i in _items(deals=[deal]) if i["type"] == "next_step_change"]

    def test_rules_two_deals_one_item_with_both_choices(self):
        deals = [
            {
                "id": ROLLOUT,
                "owner_id": "u-bala",
                "company_id": ACME,
                "next_step": None,
                "next_step_on": None,
            },
            {
                "id": PILOT,
                "owner_id": "u-asha",
                "company_id": ACME,
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
        ]
        steps = [i for i in _items(deals=deals) if i["type"] == "next_step_change"]
        assert len(steps) == 1
        assert steps[0]["deal_choices"] == sorted([PILOT, ROLLOUT])
        tasks = [i for i in _items(deals=deals) if i["type"] == "task_from_commitment"]
        assert (tasks[0]["record_type"], tasks[0]["record_id"]) == ("company", ACME)

    def test_rules_an_unknown_owner_falls_back_to_the_mailbox_owner(self):
        items = _items(
            stored_commitments=[{"text": "Send", "owner_id": None, "due_on": "2026-10-24"}]
        )
        [task] = [i for i in items if i["type"] == "task_from_commitment"]
        assert task["owner_id"] == "u-asha"
        assert task["suggestion"]["owner_unknown"] is True

    def test_rules_no_record_no_item(self):
        assert _items(deals=[], companies=[], people=[]) == []


class TestOutput:
    """FR-CRMAI-SUM-04 — the strict output model."""

    def _answer(self, **values):
        return {
            "summary": "They asked for a plan. We will send it.",
            "asks": ["A plan"],
            "commitments": [],
            "next_step": None,
            "next_step_on": None,
            "sentiment": "neutral",
            **values,
        }

    def test_rules_a_valid_answer(self):
        assert SummaryOutput.model_validate(self._answer()).sentiment == "neutral"

    @pytest.mark.parametrize(
        "values",
        [
            {"summary": "One sentence only."},
            {"summary": "One. Two. Three. Four."},
            {"summary": "A. " + "x" * 600 + "."},
            {"asks": ["x" * 201]},
            {"commitments": [{"text": "x" * 201, "owner": "1", "due_on": None}]},
            {"sentiment": "angry"},
            {"extra": "field"},
        ],
    )
    def test_rules_an_invalid_answer_is_refused(self, values):
        with pytest.raises(ValidationError):
            SummaryOutput.model_validate(self._answer(**values))
