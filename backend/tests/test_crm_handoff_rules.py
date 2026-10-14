"""The Closed Won handoff, pure — spec 007 FR-CRM-TVA-07, -08, -09,
FR-CRM-FX-05, FR-CRM-AUTO-01 automation 10; owner decision D8; Q-09, Q-57.

`domain/crm/handoff.py` decides what the handoff offers and accepts and who
gets which task; nothing here touches a store.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.crm import handoff

STAFF_MANAGER = {
    "id": "u-sriram",
    "display_name": "Sriram",
    "role": "manager",
    "is_active": True,
    "account_type": "staff",
}
STAFF_ADMIN = {**STAFF_MANAGER, "id": "u-owner", "display_name": "Owner", "role": "admin"}
STAFF_USER = {**STAFF_MANAGER, "id": "u-deepika", "display_name": "Deepika", "role": "user"}
CRM_ONLY = {
    **STAFF_MANAGER,
    "id": "u-tamil",
    "display_name": "Tamil",
    "role": "admin",
    "account_type": "crm_only",
}
INACTIVE = {**STAFF_MANAGER, "id": "u-gone", "display_name": "Gone", "is_active": False}

DEAL = {
    "id": "d-1",
    "name": "Acme - Phase 1",
    "amount": "10000.00",
    "currency": "USD",
    "project_category": None,
}
RATES = [
    {"currency": "USD", "month": "2026-09-01", "rate": "83.10"},
    {"currency": "USD", "month": "2026-10-01", "rate": "84.25"},
]


def _prefill(**overrides):
    args = {
        "deal": DEAL,
        "project": None,
        "phases": [],
        "milestones": [],
        "won": handoff.won_fx(DEAL, RATES, date(2026, 10, 15), "Nunnari Labs"),
        "may_set_revenue": True,
        "engagement_model_key": None,
        "leads": [],
    }
    return handoff.prefill(**{**args, **overrides})


class TestWhoMaySetRevenue:
    """FR-CRM-TVA-08, D8 — an active staff TVA manager or admin."""

    @pytest.mark.parametrize(
        ("profile", "allowed"),
        [
            (STAFF_MANAGER, True),
            (STAFF_ADMIN, True),
            (STAFF_USER, False),
            (CRM_ONLY, False),
            (INACTIVE, False),
            (None, False),
        ],
    )
    def test_handoff_revenue_only_for_staff_managers(self, profile, allowed):
        assert handoff.can_set_revenue(profile) is allowed


class TestPrefill:
    """FR-CRM-TVA-07 — pre-filled from the deal and the project's phases."""

    def test_handoff_prefill_from_the_deal_without_a_project(self):
        out = _prefill()
        assert out["project"] == {
            "name": "Acme - Phase 1",
            "category": "client",
            "lead_id": None,
            "phases": {},
        }

    def test_handoff_prefill_from_the_existing_project_and_phases(self):
        project = {
            "id": "p-1",
            "name": "Acme - Phase 1",
            "category": "poc",
            "status": "tentative",
            "lead_id": "u-sriram",
            "revenue": "800000",
        }
        phases = [
            {"phase": "delivery", "starts_on": "2026-11-02", "ends_on": "2027-01-29"},
            {"phase": "spillover", "starts_on": "2027-02-01", "ends_on": "2027-02-28"},
        ]
        out = _prefill(project=project, phases=phases)
        assert out["project"]["id"] == "p-1"
        assert out["project"]["category"] == "poc"
        assert out["project"]["lead_id"] == "u-sriram"
        # Spillover is never the CRM's to set, so it is not offered.
        assert out["project"]["phases"] == {
            "delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}
        }

    def test_handoff_prefill_category_from_the_deal(self):
        out = _prefill(deal={**DEAL, "project_category": "product"})
        assert out["project"]["category"] == "product"

    def test_handoff_offers_pre_only_for_a_discovery_phase_deal(self):
        assert _prefill(engagement_model_key="discovery_phase")["offer_pre_phase"] is True
        assert _prefill(engagement_model_key=None)["offer_pre_phase"] is False

    def test_handoff_suggests_inr_at_the_won_date_rate(self):
        out = _prefill()
        assert out["revenue"]["suggested_inr"] == "842500.00"
        assert out["revenue"]["won_fx_rate"] == "84.25"
        assert out["revenue"]["won_fx_month"] == "2026-10-01"

    def test_handoff_preview_shows_existing_revenue_to_a_staff_manager(self):
        project = {"id": "p-1", "name": "Acme", "category": "client", "revenue": "800000"}
        milestones = [{"name": "Kickoff", "due_on": "2026-11-02", "amount": "200000"}]
        out = _prefill(project=project, milestones=milestones, may_set_revenue=True)
        assert out["revenue"]["existing_tva_revenue"] == "800000.00"
        assert out["milestones"] == [
            {"name": "Kickoff", "due_on": "2026-11-02", "amount": "200000.00"}
        ]
        assert out["can_set_revenue"] is True

    @pytest.mark.parametrize("profile", [STAFF_USER, CRM_ONLY])
    def test_handoff_preview_hides_existing_revenue_from_anyone_else(self, profile):
        project = {"id": "p-1", "name": "Acme", "category": "client", "revenue": "800000"}
        milestones = [{"name": "Kickoff", "due_on": "2026-11-02", "amount": "200000"}]
        out = _prefill(
            project=project,
            milestones=milestones,
            may_set_revenue=handoff.can_set_revenue(profile),
        )
        assert "existing_tva_revenue" not in out["revenue"]
        assert out["milestones"] == []
        assert out["can_set_revenue"] is False


class TestWonFx:
    """FR-CRM-FX-05 — the won date's rate, or the latest earlier one."""

    def test_handoff_converts_at_the_won_month(self):
        won = handoff.won_fx(DEAL, RATES, date(2026, 10, 15), "Nunnari Labs")
        assert won == {
            "amount_in_inr": "842500.00",
            "fx_rate_used": "84.25",
            "fx_month": "2026-10-01",
            "won_fx_rate": "84.25",
            "won_fx_month": "2026-10-01",
        }

    def test_handoff_falls_back_to_an_earlier_month(self):
        won = handoff.won_fx(DEAL, RATES[:1], date(2026, 10, 15), "Nunnari Labs")
        assert won["won_fx_rate"] == "83.1"
        assert won["won_fx_month"] == "2026-09-01"

    def test_handoff_refuses_without_a_rate(self):
        assert handoff.won_fx(DEAL, [], date(2026, 10, 15), "Nunnari Labs") == (
            "No USD rate for 2026-10 or any earlier month in Nunnari Labs; "
            "a workspace admin sets it in Settings."
        )

    def test_handoff_inr_is_one(self):
        won = handoff.won_fx({**DEAL, "currency": "INR"}, [], date(2026, 10, 15), "Labs")
        assert won["amount_in_inr"] == "10000.00"
        assert won["won_fx_rate"] == "1"

    def test_handoff_without_an_amount_converts_nothing(self):
        assert handoff.won_fx({**DEAL, "amount": None}, RATES, date(2026, 10, 15), "L") is None


class TestPhases:
    """FR-CRM-TVA-07 — delivery required; pre and support optional."""

    D = {"starts_on": date(2026, 11, 2), "ends_on": date(2027, 1, 29)}

    def test_handoff_phases_need_delivery(self):
        assert handoff.phases_refusal({"support": self.D}) == (
            "The handoff needs the delivery phase's start and end dates."
        )

    def test_handoff_phases_end_after_start(self):
        backwards = {"starts_on": date(2027, 1, 29), "ends_on": date(2026, 11, 2)}
        assert handoff.phases_refusal({"delivery": self.D, "support": backwards}) == (
            "The post-delivery support phase cannot end before it starts."
        )

    def test_handoff_phases_never_spillover(self):
        assert handoff.phases_refusal({"delivery": self.D, "spillover": self.D}) == (
            "The handoff sets only pre, delivery and support phases."
        )

    def test_handoff_phases_accepted(self):
        assert handoff.phases_refusal({"pre": self.D, "delivery": self.D}) is None


class TestLead:
    """FR-CRM-TVA-07, FR-CRM-CHG-02 — an active staff account."""

    def test_handoff_lead_must_exist(self):
        assert handoff.lead_refusal(None) == "That lead does not exist."

    def test_handoff_lead_cannot_be_crm_only(self):
        assert handoff.lead_refusal(CRM_ONLY) == "A CRM-only account cannot lead a project."

    def test_handoff_lead_must_be_active(self):
        assert handoff.lead_refusal(INACTIVE) == "Gone is not active."

    def test_handoff_lead_staff_of_any_role(self):
        assert handoff.lead_refusal(STAFF_USER) is None


class TestMilestones:
    """FR-CRM-TVA-07 — milestones add up to revenue or show the difference."""

    def test_handoff_milestones_matching_revenue(self):
        ms = [{"amount": "500000"}, {"amount": "500000.00"}]
        assert handoff.milestone_difference(Decimal("1000000"), ms) == "0.00"

    def test_handoff_milestones_difference_in_inr(self):
        ms = [{"amount": "400000"}]
        assert handoff.milestone_difference(Decimal("1000000"), ms) == "600000.00"

    def test_handoff_milestones_without_revenue(self):
        assert handoff.milestone_difference(None, [{"amount": "1"}]) is None


class TestTasks:
    """FR-CRM-TVA-08, -09, Q-09, Q-57."""

    def test_handoff_revenue_task_goes_to_admins_who_are_managers(self):
        admins = [STAFF_MANAGER, STAFF_USER, CRM_ONLY]
        assert handoff.revenue_task_recipients(admins) == ["u-sriram"]

    def test_handoff_revenue_task_goes_to_every_admin_when_none_is_a_manager(self):
        admins = [STAFF_USER, CRM_ONLY]
        assert handoff.revenue_task_recipients(admins) == ["u-deepika", "u-tamil"]

    def test_handoff_revenue_task_says_where_to_set_it(self):
        task = handoff.revenue_task("Acme", "d-1", "u-sriram")
        assert task["title"] == "Confirm revenue and milestones"
        assert "TVA Projects" in task["body"] and "TVA manager or admin" in task["body"]
        assert task["links"] == [{"record_type": "opportunity", "record_id": "d-1"}]

    def test_handoff_delivery_task_to_the_lead_when_a_member(self):
        assert handoff.delivery_assignee("u-lead", {"u-lead", "u-owner"}, "u-owner") == (
            "u-lead",
            False,
        )

    def test_handoff_delivery_task_to_the_owner_naming_the_lead(self):
        who, name_lead = handoff.delivery_assignee("u-lead", {"u-owner"}, "u-owner")
        assert (who, name_lead) == ("u-owner", True)
        task = handoff.delivery_task("Acme", "Priya", "d-1", who, name_lead)
        assert "Priya leads it" in task["body"]
        assert task["category_key"] == "delivery_handoff"
        assert task["assignee_id"] == "u-owner"
