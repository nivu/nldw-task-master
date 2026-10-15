"""The seeded workspaces and lists — spec 007 FR-CRM-WS-01, FR-CRM-LIST-02 to
-05, -09, FR-CRM-PIPE-11, FR-CRM-ACC-06, (041) FR-CRM-CAMP-01; data-model
§7.1–§7.3; Q-25, Q-40.

The values migration 026 seeds into each workspace are database state, so
they are read back from the real database after `supabase db reset`, label by
label and in order, exactly as the spec lists them. (tasks.md places these
assertions in test_crm_settings_lists.py; they live here because that module
runs against the in-memory fake, which has no seeds to check.)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db

STAGES = ["Discovery", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
LEAD_STAGES = [
    "New",
    "Contacted",
    "Nurturing",
    "Qualified",
    "Deal open",
    "Customer",
    "Partner",
    "Disqualified",
]

#: FR-CRM-LIST-03.
LABS = {
    "channel": [
        "Oracle",
        "Qualcomm",
        "Anthropic",
        "DeepWeaver",
        "Skillda",
        "Forge",
        "CODISSIA",
        "SICCI",
        "GIBA",
        "Guidance TN",
        "Infosys",
        "Referral",
        "Inbound",
        "Event",
        "Direct outbound",
    ],
    "company.account_type": [
        "Prospect",
        "Client",
        "Technology partner",
        "Channel partner",
        "Training partner",
        "Vendor",
    ],
    "task.category": ["Lead", "Sales", "Partner", "Campaign", "Delivery handoff", "Operations"],
}
#: FR-CRM-LIST-04. Programme starts empty (Q-40).
ACADEMY = {
    "channel": [
        "Inbound",
        "Referral",
        "Event",
        "Direct",
        "Webinar",
        "Forge",
        "Anthropic",
        "Skillda",
        "Guidance TN",
        "Infosys",
        "Nunnari Labs",
        "LinkedIn",
    ],
    "company.account_type": [
        "Prospect",
        "Client",
        "Training partner",
        "Technology partner",
        "Channel partner",
        "Vendor",
    ],
    "task.category": [
        "College",
        "Corporate training",
        "Lead",
        "Sales",
        "Campaign",
        "Marketing",
        "Curriculum",
        "Customer",
        "Operations",
    ],
    "company.institution_type": ["College", "University", "Corporate", "Training partner", "Other"],
}
#: FR-CRM-LIST-02 and -05, from spec §5.6 to §5.9: the same in both.
COMMON = {
    "opportunity.stage": STAGES,
    "company.lead_stage": LEAD_STAGES,
    "task.status": ["To do", "In progress", "Done"],
    "task.bucket": ["Today", "This week", "This month", "Later"],
    "company.relationship_status": ["Active", "Warm", "Cold", "Dormant", "Churned"],
    "company.size": ["1-10", "11-50", "51-200", "201-1000", "1000+"],
    "source": ["Manual", "Import", "Email sync", "Campaign", "Web form", "API"],
    "opportunity.deal_type": [
        "New business",
        "Expansion",
        "Renewal",
        "Phase follow-on",
        "Support extension",
    ],
    "opportunity.engagement_model": [
        "Discovery phase",
        "Fixed scope",
        "Milestone based",
        "Forward deployed engineering",
        "Dedicated team",
        "Staff augmentation",
        "AIOps annuity",
        "Training",
    ],
    "opportunity.lost_reason": [
        "Price",
        "Timing",
        "Competitor",
        "No budget",
        "No response",
        "Scope mismatch",
        "Chose to build in-house",
        "Other",
    ],
    "opportunity.contact_role": [
        "Decision maker",
        "Champion",
        "Influencer",
        "Technical evaluator",
        "Procurement",
        "User",
        "Other",
    ],
    # 041 (1b): spec §6.6.
    "campaign.type": [
        "Webinar",
        "Email sequence",
        "Event or roundtable",
        "LinkedIn or content",
        "Partner co-sell",
        "College outreach",
        "Newsletter",
    ],
    "campaign.status": ["Planned", "Live", "Completed", "Cancelled"],
    "campaign_member.status": [
        "Targeted",
        "Invited",
        "Registered",
        "Attended",
        "Engaged",
        "Meeting booked",
        "Converted",
        "Not interested",
        "Unsubscribed",
    ],
}


def _lists(stack, key: str) -> dict[str, list[dict]]:
    rows = stack.sql(
        "SELECT o.list_key, o.label, o.system_key, o.is_fixed, o.is_default, o.position "
        "FROM crm_select_options o JOIN crm_workspaces w ON w.id = o.workspace_id "
        f"WHERE w.key = {stack.q(key)} AND o.created_by IS NULL "
        "ORDER BY o.list_key, o.position"
    )
    lists: dict[str, list[dict]] = {}
    for row in rows:
        lists.setdefault(row["list_key"], []).append(row)
    return lists


class TestWorkspaces:
    def test_two_workspaces(self, stack):
        rows = stack.sql(
            "SELECT key, name, tva_linked, has_entity, allowed_domains FROM crm_workspaces "
            "WHERE key IN ('labs', 'academy') ORDER BY key DESC"
        )
        assert rows == [
            {
                "key": "labs",
                "name": "Nunnari Labs",
                "tva_linked": True,
                "has_entity": True,
                "allowed_domains": ["nunnarilabs.com", "deepweaver.ai"],
            },
            {
                "key": "academy",
                "name": "Nunnari Academy B2B",
                "tva_linked": False,
                "has_entity": False,
                "allowed_domains": ["nunnari.academy", "nunnarilabs.com"],
            },
        ]

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_default_probabilities(self, stack, key):
        value = stack.sql(
            "SELECT s.value FROM crm_workspace_settings s JOIN crm_workspaces w "
            f"ON w.id = s.workspace_id WHERE w.key = {stack.q(key)} "
            "AND s.key = 'stage_probabilities'"
        )[0]["value"]
        assert value == {
            "discovery": 10,
            "qualification": 25,
            "proposal": 50,
            "negotiation": 75,
            "closed_won": 100,
            "closed_lost": 0,
        }


class TestSeededLists:
    @pytest.mark.parametrize(("key", "own"), [("labs", LABS), ("academy", ACADEMY)])
    def test_exactly_the_specs_values_in_order(self, stack, key, own):
        lists = _lists(stack, key)
        expected = {**COMMON, **own}
        assert {k: [o["label"] for o in v] for k, v in lists.items()} == expected

    def test_institution_type_and_programme_are_academy_only(self, stack):
        assert "company.institution_type" not in _lists(stack, "labs")
        # Programme exists in Academy as a list with no options yet (Q-40),
        # so neither workspace has a seeded option for it.
        for key in ("labs", "academy"):
            assert "opportunity.programme" not in _lists(stack, key)

    def test_delivery_handoff_is_labs_only(self, stack):
        labs = _lists(stack, "labs")["task.category"]
        academy = _lists(stack, "academy")["task.category"]
        assert [o["system_key"] for o in labs if o["system_key"]] == ["delivery_handoff"]
        assert not [o for o in academy if o["system_key"]]

    def test_industry_starts_empty(self, stack):
        for key in ("labs", "academy"):
            assert "company.industry" not in _lists(stack, key)

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_fixed_lists_carry_their_keys(self, stack, key):
        lists = _lists(stack, key)
        assert [o["system_key"] for o in lists["opportunity.stage"]] == [
            "discovery",
            "qualification",
            "proposal",
            "negotiation",
            "closed_won",
            "closed_lost",
        ]
        for list_key in ("opportunity.stage", "company.lead_stage", "task.status", "task.bucket"):
            assert all(o["is_fixed"] for o in lists[list_key]), list_key

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_rule_bearing_options_are_keyed(self, stack, key):
        """FR-CRM-LIST-09, Q-71."""
        lists = _lists(stack, key)
        keyed = {o["system_key"] for o in lists["company.account_type"] if o["system_key"]}
        assert keyed == {"client", "technology_partner", "channel_partner", "training_partner"}
        assert all(o["system_key"] for o in lists["source"])
        assert all(o["system_key"] for o in lists["opportunity.lost_reason"])
        assert [o["system_key"] for o in lists["opportunity.engagement_model"]][0] == (
            "discovery_phase"
        )

    def test_a_keyed_option_cannot_be_deactivated_even_by_sql(self, stack):
        """The CHECK behind FR-CRM-LIST-09; the actor is set so only it can refuse."""
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                """SELECT set_config('crm.actor', '{"kind": "system"}', true); """
                "UPDATE crm_select_options SET is_active = false WHERE system_key = 'client'"
            )
        assert raised.value.code == "23514"
