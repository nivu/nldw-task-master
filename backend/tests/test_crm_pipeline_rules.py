"""The pipeline rules, pure — spec 007 FR-CRM-PIPE-03 to -14, FR-CRM-OPP-02;
Q-31, Q-33, Q-60, Q-64; plan §5.4.

One class per rule, against `domain/crm/pipeline.py` alone: no route, no
store. The routes that apply them are in `test_crm_deals_routes.py`.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.crm import pipeline

TODAY = date(2026, 10, 14)


def _deal(**fields):
    return {
        "id": "d1",
        "stage": "discovery",
        "company_id": "c1",
        "owner_id": "u-owner",
        "next_step": "Call",
        "next_step_on": "2026-10-20",
        "point_of_contact_id": None,
        "amount": None,
        "currency": None,
        "expected_close_on": None,
        "proposal_sent_on": None,
        "lost_reason_id": None,
        "lost_from_stage": None,
        "updated_at": "2026-10-14T10:00:00+00:00",
        **fields,
    }


def _company(**fields):
    return {
        "id": "c1",
        "name": "Acme",
        "lead_stage": "deal_open",
        "account_type_id": None,
        "updated_at": "2026-10-14T09:00:00+00:00",
        **fields,
    }


def _plan(deal=None, target="qualification", changes=None, **overrides):
    args = {
        "deal": deal or _deal(),
        "company": _company(),
        "target": target,
        "changes": changes or {},
        "role": "member",
        "user_id": "u-member",
        "probabilities": None,
        "other_open_deals": 0,
        "has_won": False,
        "company_on_loss": None,
        "open_task_ids": [],
        "lost_reason_label": "Price",
        "today": TODAY,
        "client_option_id": "opt-client",
    }
    args.update(overrides)
    return pipeline.plan_move(**args)


class TestEntryRequirements:
    """FR-CRM-PIPE-07 — what entering each stage needs."""

    def test_discovery_needs_company_owner_next_step_and_date(self):
        missing = pipeline.stage_requirements(
            "discovery", {"company_id": None, "owner_id": None}, []
        )
        assert missing == ["company_id", "owner_id", "next_step", "next_step_on"]

    def test_qualification_needs_a_point_of_contact(self):
        assert pipeline.stage_requirements("qualification", _deal(), []) == ["point_of_contact_id"]

    def test_proposal_needs_amount_currency_and_close_date(self):
        deal = _deal(point_of_contact_id="p1")
        assert pipeline.stage_requirements("proposal", deal, []) == [
            "amount",
            "currency",
            "expected_close_on",
        ]

    def test_negotiation_needs_the_proposal_date(self):
        assert pipeline.stage_requirements("negotiation", _deal(), []) == ["proposal_sent_on"]

    def test_closed_lost_needs_its_reason(self):
        assert pipeline.stage_requirements("closed_lost", _deal(), []) == ["lost_reason_id"]

    def test_every_open_stage_keeps_the_next_step(self):
        """FR-CRM-PIPE-10."""
        deal = _deal(next_step=" ", next_step_on=None, point_of_contact_id="p1")
        assert pipeline.stage_requirements("qualification", deal, []) == [
            "next_step",
            "next_step_on",
        ]

    def test_the_sentence_names_every_missing_field(self):
        """Plan §5.4 — not only the first."""
        assert pipeline.missing_sentence(
            "proposal", ["amount", "currency", "expected_close_on"]
        ) == ("Proposal needs amount, currency and expected close date.")
        assert pipeline.missing_sentence("closed_lost", ["lost_reason_id"]) == (
            "Closed Lost needs lost reason."
        )


class TestSkipping:
    """FR-CRM-PIPE-08, Q-31."""

    def test_skipped_stages(self):
        assert pipeline.skipped_stages("discovery", "negotiation") == [
            "qualification",
            "proposal",
        ]
        assert pipeline.skipped_stages("negotiation", "qualification") == []
        assert pipeline.skipped_stages("discovery", "closed_lost") == []

    def test_a_skip_meets_the_requirements_up_to_proposal(self):
        move = _plan(target="negotiation")
        assert move.allowed is False
        assert move.missing == [
            "point_of_contact_id",
            "amount",
            "currency",
            "expected_close_on",
            "proposal_sent_on",
        ]
        assert move.message == (
            "Negotiation needs point of contact, amount, currency, expected close date and "
            "proposal sent date."
        )

    def test_proposal_sent_on_is_needed_only_to_enter_negotiation(self):
        deal = _deal(
            stage="proposal",
            point_of_contact_id="p1",
            amount="10",
            currency="INR",
            expected_close_on="2026-12-01",
        )
        assert _plan(deal, "closed_won").allowed is True
        assert _plan(
            _deal(stage="qualification", point_of_contact_id="p1"), "closed_won"
        ).missing == [
            "amount",
            "currency",
            "expected_close_on",
        ]

    def test_lost_needs_only_its_reason_whatever_it_skips(self):
        move = _plan(target="closed_lost", changes={"lost_reason_id": "r1"})
        assert move.allowed is True


class TestBackwards:
    """FR-CRM-PIPE-12, -13."""

    def test_backwards_is_allowed(self):
        deal = _deal(stage="negotiation", point_of_contact_id="p1")
        move = _plan(deal, "qualification")
        assert move.allowed is True
        assert move.plan["history"] == {"from": "negotiation", "to": "qualification"}

    def test_out_of_won_only_by_an_admin(self):
        deal = _deal(stage="closed_won")
        assert _plan(deal, "negotiation").message == (
            "Only a workspace admin can reopen a won deal."
        )

    def test_a_won_deal_reopens_to_negotiation(self):
        deal = _deal(stage="closed_won", won_on="2026-10-01")
        assert _plan(deal, "proposal", role="admin").message == "A won deal reopens to Negotiation."
        move = _plan(deal, "negotiation", role="admin", changes={"proposal_sent_on": "2026-09-01"})
        assert move.allowed is True
        assert move.plan["opportunity"]["won_on"] is None

    def test_won_does_not_go_straight_to_lost(self):
        deal = _deal(stage="closed_won")
        move = _plan(deal, "closed_lost", role="admin", changes={"lost_reason_id": "r1"})
        assert move.message == "A won deal reopens to Negotiation."


class TestReopenFromLost:
    """FR-CRM-PIPE-12, Q-64."""

    def _lost(self, **fields):
        return _deal(
            stage="closed_lost",
            lost_reason_id="r1",
            lost_from_stage="qualification",
            owner_id="u-seller",
            point_of_contact_id="p1",
            **fields,
        )

    def test_by_the_owner_or_an_admin_only(self):
        move = _plan(self._lost(), "qualification")
        assert move.message == (
            "Only a workspace admin or the deal's owner can reopen a lost deal."
        )
        changes = {"next_step": "Re-engage", "next_step_on": "2026-11-01"}
        assert _plan(self._lost(), "qualification", changes, user_id="u-seller").allowed
        assert _plan(self._lost(), "qualification", changes, role="admin").allowed

    def test_back_to_the_stage_it_was_lost_from(self):
        move = _plan(self._lost(), "discovery", user_id="u-seller")
        assert move.message == ("A lost deal reopens to Qualification, the stage it was lost from.")

    def test_with_a_next_step_and_date_in_the_same_save(self):
        move = _plan(self._lost(), "qualification", user_id="u-seller")
        assert move.missing == ["next_step", "next_step_on"]
        assert move.message == "Qualification needs next step and next step date."

    def test_the_loss_is_cleared(self):
        move = _plan(
            self._lost(),
            "qualification",
            {"next_step": "Re-engage", "next_step_on": "2026-11-01"},
            user_id="u-seller",
        )
        values = move.plan["opportunity"]
        assert (values["lost_on"], values["lost_reason_id"], values["lost_from_stage"]) == (
            None,
            None,
            None,
        )

    def test_a_nurturing_company_goes_back_to_deal_open(self):
        """FR-CRM-PIPE-04: the loss moved it to Nurturing; it has a deal again."""
        move = _plan(
            self._lost(),
            "qualification",
            {"next_step": "Re-engage", "next_step_on": "2026-11-01"},
            user_id="u-seller",
            company=_company(lead_stage="nurturing"),
        )
        assert move.plan["company"]["changes"] == {"lead_stage": "deal_open"}
        assert move.would["company_lead_stage"] == "deal_open"

    def test_a_disqualified_company_takes_no_reopened_deal(self):
        move = _plan(
            self._lost(),
            "qualification",
            {"next_step": "Re-engage", "next_step_on": "2026-11-01"},
            user_id="u-seller",
            company=_company(lead_stage="disqualified"),
        )
        assert not move.allowed
        assert move.message == "Acme is Disqualified; a disqualified company takes no deal."


class TestProbability:
    """FR-CRM-PIPE-11."""

    def test_reset_to_the_stage_default(self):
        move = _plan(_deal(point_of_contact_id="p1"), "qualification")
        assert move.plan["opportunity"]["probability"] == 25

    def test_the_workspace_setting_wins(self):
        move = _plan(
            _deal(point_of_contact_id="p1"), "qualification", probabilities={"qualification": 30}
        )
        assert move.plan["opportunity"]["probability"] == 30

    def test_unless_set_in_the_same_save(self):
        move = _plan(_deal(point_of_contact_id="p1"), "qualification", {"probability": 40})
        assert move.plan["opportunity"]["probability"] == 40

    def test_a_null_is_no_choice(self):
        move = _plan(_deal(point_of_contact_id="p1"), "qualification", {"probability": None})
        assert move.plan["opportunity"]["probability"] == 25

    def test_a_broken_setting_falls_back(self):
        assert pipeline.default_probability("proposal", {"proposal": "fifty"}) == 50
        assert pipeline.default_probability("proposal", {"proposal": True}) == 50


class TestCompanyOnOpen:
    """FR-CRM-PIPE-04, Q-60."""

    @pytest.mark.parametrize("stage", ["new", "contacted", "nurturing"])
    def test_below_qualified_is_refused(self, stage):
        changes, why = pipeline.company_effect_on_open(
            {"name": "Acme", "lead_stage": stage, "lead_stage_label": stage.capitalize()}
        )
        assert changes == {}
        assert why == (
            f"A deal needs its company at Qualified or later; Acme is at {stage.capitalize()}."
        )

    def test_disqualified_is_refused(self):
        _, why = pipeline.company_effect_on_open({"name": "Acme", "lead_stage": "disqualified"})
        assert why == "Acme is Disqualified; a disqualified company takes no deal."

    def test_qualified_moves_to_deal_open(self):
        assert pipeline.company_effect_on_open({"lead_stage": "qualified"}) == (
            {"lead_stage": "deal_open"},
            None,
        )

    @pytest.mark.parametrize("stage", ["deal_open", "customer", "partner"])
    def test_the_others_keep_their_stage(self, stage):
        assert pipeline.company_effect_on_open({"lead_stage": stage}) == ({}, None)


class TestCompanyOnClose:
    """FR-CRM-PIPE-05, Q-60 — the last open deal decides."""

    def _close(self, stage="deal_open", outcome="closed_won", **kw):
        args = {"other_open_deals": 0, "has_won": False, "choice": None}
        args.update(kw)
        return pipeline.company_effect_on_close(
            {"lead_stage": stage, "account_type_id": None},
            outcome,
            args["other_open_deals"],
            args["has_won"],
            args["choice"],
            client_option_id="opt-client",
        )

    def test_won_makes_a_customer_and_a_client(self):
        assert self._close() == {"lead_stage": "customer", "account_type_id": "opt-client"}

    def test_a_partner_stays_a_partner(self):
        assert self._close("partner") == {}

    def test_another_open_deal_keeps_the_company_as_it_is(self):
        assert self._close(other_open_deals=1) == {}
        assert self._close(outcome="closed_lost", other_open_deals=1) == {}

    def test_lost_moves_deal_open_to_nurturing(self):
        assert self._close(outcome="closed_lost") == {"lead_stage": "nurturing"}

    def test_or_to_disqualified_when_chosen(self):
        assert self._close(outcome="closed_lost", choice="disqualified") == {
            "lead_stage": "disqualified"
        }

    def test_a_company_with_a_won_deal_becomes_a_customer(self):
        assert self._close(outcome="closed_lost", has_won=True) == {"lead_stage": "customer"}

    @pytest.mark.parametrize("stage", ["customer", "partner"])
    def test_customer_and_partner_are_unchanged_by_a_loss(self, stage):
        assert self._close(stage, outcome="closed_lost", choice="disqualified") == {}

    def test_the_plan_carries_the_counts_it_used(self):
        """FR-CRM-PIPE-15 — 031 counts them again under the company's lock."""
        move = _plan(
            target="closed_lost",
            changes={"lost_reason_id": "r1"},
            other_open_deals=1,
            has_won=True,
        )
        assert move.plan["expect_siblings"] == {"open": 1, "won": True}
        assert "expect_siblings" not in _plan(_deal(point_of_contact_id="p1")).plan


class TestLostClosesTasks:
    """FR-CRM-PIPE-09, FR-CRM-AUTO-01 automation 9."""

    def test_open_tasks_close_with_a_note(self):
        move = _plan(
            target="closed_lost",
            changes={"lost_reason_id": "r1"},
            open_task_ids=["t1", "t2"],
        )
        assert move.plan["close_tasks"] == ["t1", "t2"]
        assert move.plan["close_note"] == {"body": "Closed: deal lost (Price)."}
        assert move.would["tasks_closed"] == 2

    def test_no_open_tasks_no_note(self):
        move = _plan(target="closed_lost", changes={"lost_reason_id": "r1"})
        assert "close_tasks" not in move.plan and "close_note" not in move.plan

    def test_lost_records_the_stage_and_day(self):
        move = _plan(
            _deal(stage="qualification", point_of_contact_id="p1"),
            "closed_lost",
            {"lost_reason_id": "r1"},
        )
        values = move.plan["opportunity"]
        assert (values["lost_on"], values["lost_from_stage"], values["probability"]) == (
            "2026-10-14",
            "qualification",
            0,
        )

    def test_the_company_choice_is_one_of_two(self):
        move = _plan(target="closed_lost", changes={"lost_reason_id": "r1"}, company_on_loss="x")
        assert move.message == "After a loss the company goes to Nurturing or Disqualified."


class TestWhoMayMove:
    """Spec §3.3 and the plan's refusals."""

    def test_viewers_move_nothing(self):
        assert pipeline.may_move("discovery", "qualification", "viewer", False) == (
            "Viewers cannot move deals."
        )

    def test_not_to_the_same_stage(self):
        assert pipeline.may_move("discovery", "discovery", "admin", True) == (
            "The deal is already at Discovery."
        )

    def test_not_to_an_unknown_stage(self):
        assert pipeline.may_move("discovery", "won", "admin", True) == "“won” is not a stage."

    def test_a_lost_reason_only_with_a_loss(self):
        move = _plan(_deal(point_of_contact_id="p1"), "qualification", {"lost_reason_id": "r1"})
        assert move.message == "A lost reason is set only by a move to Closed Lost."


class TestTheMoveRecordsItsTime:
    """FR-CRM-PIPE-14, FR-CRM-OPP-02 — every move asks the database for its
    clock and writes a history row."""

    def test_stage_entered_now_and_history(self):
        move = _plan(_deal(point_of_contact_id="p1"), "qualification")
        assert move.plan["opportunity"]["stage_entered_at"] == "now"
        assert move.plan["history"] == {"from": "discovery", "to": "qualification"}
        assert move.plan["expect_stage"] == "discovery"

    def test_won_sets_the_day(self):
        deal = _deal(
            stage="negotiation",
            point_of_contact_id="p1",
            amount="10",
            currency="INR",
            expected_close_on="2026-12-01",
            proposal_sent_on="2026-10-01",
        )
        move = _plan(deal, "closed_won")
        assert move.plan["opportunity"]["won_on"] == "2026-10-14"
        assert move.plan["company"]["changes"] == {
            "lead_stage": "customer",
            "account_type_id": "opt-client",
        }
