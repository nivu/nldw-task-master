"""The Twenty importer's rules, as pure functions — spec 007 FR-CRM-MIG-02,
-04, -05, FR-CRM-DUP-01, -02, FR-CRM-PIPE-10, FR-CRM-FX-03; Q-46, Q-55;
contracts §2.9.

`domain/crm/importer.py` decides everything a dry run reports and a real run
writes; these tests hand it an export and a workspace's facts directly, with
a fixed `today`, and read the plan.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.crm import importer as rules

TODAY = date(2026, 10, 15)
ADMIN = "u-admin"


def _facts(**overrides) -> rules.Facts:
    values = {
        "workspace": "Nunnari Labs",
        "importer": ADMIN,
        "members": {"owner@nunnarilabs.com": "u-owner", "deepika@nunnarilabs.com": "u-member"},
        "options": [
            {
                "id": "opt-partner",
                "list_key": "company.account_type",
                "system_key": "technology_partner",
                "label": "Technology partner",
                "is_active": True,
            },
            {
                "id": "opt-client",
                "list_key": "company.account_type",
                "system_key": "client",
                "label": "Client",
                "is_active": True,
            },
            {
                "id": "opt-price",
                "list_key": "opportunity.lost_reason",
                "system_key": "price",
                "label": "Price",
                "is_active": True,
            },
            {
                "id": "opt-other",
                "list_key": "opportunity.lost_reason",
                "system_key": "other",
                "label": "Other",
                "is_active": True,
            },
        ],
        "rates": [{"currency": "USD", "month": "2026-09-01", "rate": "83.5"}],
        "probabilities": {},
        "existing": {obj: {} for obj in rules.TABLES},
    }
    values.update(overrides)
    return rules.Facts(**values)


def _plan(bundle, facts=None, *, fixup=False) -> rules.ImportPlan:
    return rules.plan(bundle, facts or _facts(), today=TODAY, fixup=fixup)


def _kinds(plan: rules.ImportPlan) -> list[tuple[str, int, str, bool]]:
    return [(c["object"], c["row"], c["kind"], c["blocking"]) for c in plan.conflicts]


COMPANY = {"id": "c1", "name": "Acme", "domainName": "acme.io"}
OPEN_DEAL = {
    "id": "o1",
    "name": "Acme pilot",
    "companyId": "c1",
    "stage": "NEW",
    "nextStep": "Call",
    "nextStepOn": "2026-10-20",
}


class TestStageMap:
    """FR-CRM-MIG-02 — NEW→Discovery … LOST→Closed Lost."""

    @pytest.mark.parametrize(
        ("twenty", "stage"),
        [
            ("NEW", "discovery"),
            ("SCREENING", "qualification"),
            ("PROPOSAL", "proposal"),
            ("NEGOTIATION", "negotiation"),
            ("CUSTOMER", "closed_won"),
            ("LOST", "closed_lost"),
            ("closed_won", "closed_won"),
        ],
    )
    def test_stages_map(self, twenty, stage):
        assert rules.stage_of(twenty) == stage

    def test_an_unknown_stage_is_imported_at_discovery_and_reported(self):
        plan = _plan({"companies": [COMPANY], "opportunities": [{**OPEN_DEAL, "stage": "WON?"}]})
        [deal] = plan.writes["opportunities"]
        assert deal.values["stage"] == "discovery"
        assert ("opportunities", 1, "unknown_stage", False) in _kinds(plan)

    def test_each_record_keeps_its_twenty_id(self):
        plan = _plan({"companies": [COMPANY], "opportunities": [OPEN_DEAL]})
        assert plan.writes["companies"][0].values["twenty_id"] == "c1"
        assert plan.writes["opportunities"][0].values["twenty_id"] == "o1"


class TestLeadStageDerivation:
    """FR-CRM-MIG-05, Q-55 — first match wins."""

    @pytest.mark.parametrize(
        ("account", "deals", "own", "expected"),
        [
            ("technology_partner", ["discovery"], None, ("partner", "partner account type")),
            ("channel_partner", [], None, ("partner", "partner account type")),
            ("training_partner", ["closed_won"], "new", ("partner", "partner account type")),
            ("client", ["closed_won", "proposal"], None, ("deal_open", "open deal")),
            (None, ["closed_lost", "closed_won"], None, ("customer", "won deal")),
            (None, ["closed_lost", "closed_lost"], "qualified", ("nurturing", "only lost deals")),
            (None, [], "contacted", ("contacted", "stage in the file")),
            (None, [], None, ("new", "no deals")),
        ],
    )
    def test_each_branch_in_order(self, account, deals, own, expected):
        assert rules.derive_lead_stage(account, deals, own) == expected

    @pytest.mark.parametrize(
        ("text", "stage"),
        [("QUALIFIED", "qualified"), ("Disqualified", "disqualified"), ("deal_open", None)],
    )
    def test_the_files_own_stage_maps_when_it_holds_without_deals(self, text, stage):
        assert rules.file_lead_stage(text) == stage

    def test_the_plan_reports_each_companys_derived_stage(self):
        bundle = {
            "companies": [
                {**COMPANY, "accountType": "Technology partner"},
                {"id": "c2", "name": "Initech", "stage": "QUALIFIED"},
            ],
            "opportunities": [OPEN_DEAL],
        }
        plan = _plan(bundle)
        assert [(s["company"], s["derived"]) for s in plan.lead_stages] == [
            ("Acme", "partner"),
            ("Initech", "qualified"),
        ]

    def test_a_company_in_the_crm_follows_the_deals_the_file_brings(self):
        existing = {obj: {} for obj in rules.TABLES}
        existing["companies"]["c1"] = {
            "id": "id-c1",
            "twenty_id": "c1",
            "name": "Acme",
            "lead_stage": "contacted",
            "account_type_id": None,
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
        plan = _plan({"opportunities": [OPEN_DEAL]}, _facts(existing=existing))
        [company] = plan.writes["companies"]
        assert (company.op, company.values) == ("update", {"lead_stage": "deal_open"})
        assert ("opportunities", 1, "deal_below_qualified", False) in _kinds(plan)


class TestOwnerMapping:
    """FR-CRM-MIG-05 — owners map to members by email, else the importing
    Admin."""

    def test_a_member_by_email_in_any_case(self):
        assert rules.owner_for("Deepika@NunnariLabs.com", _facts().members, ADMIN) == (
            "u-member",
            False,
        )

    def test_an_unknown_owner_falls_to_the_importer_and_is_flagged(self):
        assert rules.owner_for("gavin@hooli.xyz", _facts().members, ADMIN) == (ADMIN, True)

    def test_no_owner_at_all_is_the_importer_unflagged(self):
        assert rules.owner_for(None, _facts().members, ADMIN) == (ADMIN, False)

    def test_an_unknown_owner_is_listed(self):
        plan = _plan({"companies": [{**COMPANY, "accountOwnerEmail": "gavin@hooli.xyz"}]})
        assert plan.writes["companies"][0].values["owner_id"] == ADMIN
        assert ("companies", 1, "unknown_owner", False) in _kinds(plan)


class TestViolations:
    """FR-CRM-MIG-04 — every conflict and rule violation per row."""

    def test_a_domain_another_company_has_is_left_off(self):
        facts = _facts(
            domains={"acme.io": {"company_id": "id-x", "name": "Acme Old", "in_trash": True}}
        )
        plan = _plan({"companies": [COMPANY]}, facts)
        assert plan.writes["companies"][0].domains == []
        [conflict] = plan.conflicts
        assert conflict["kind"] == "duplicate_domain"
        assert conflict["detail"] == (
            "acme.io already belongs to Acme Old (in Trash); Acme is imported without it."
        )

    def test_a_domain_twice_in_the_file_goes_to_the_first(self):
        plan = _plan(
            {"companies": [COMPANY, {"id": "c2", "name": "Acme Pvt", "domain": "acme.io"}]}
        )
        first, second = plan.writes["companies"]
        assert first.domains == [("acme.io", True)] and second.domains == []
        assert _kinds(plan) == [("companies", 2, "duplicate_domain", False)]

    def test_an_email_another_person_has_moves_to_additional_emails(self):
        facts = _facts(
            emails={"asha@acme.io": {"person_id": "id-p", "name": "Asha", "in_trash": False}}
        )
        plan = _plan(
            {"people": [{"id": "p1", "firstName": "Dana", "email": "Asha@Acme.io"}]}, facts
        )
        [person] = plan.writes["people"]
        assert person.values["primary_email"] is None
        assert person.values["additional_emails"] == ["asha@acme.io"]
        assert _kinds(plan) == [("people", 1, "duplicate_email", False)]

    def test_an_open_deal_without_a_next_step_blocks_the_run(self):
        deal = {k: v for k, v in OPEN_DEAL.items() if k not in ("nextStep", "nextStepOn")}
        plan = _plan({"companies": [COMPANY], "opportunities": [deal]})
        assert ("opportunities", 1, "no_next_step", True) in _kinds(plan)
        assert plan.fixup_candidates == 1 and plan.fixups == 0
        assert plan.blocking == [
            "Acme pilot is open with no next step; add one in the file or choose the fix-up."
        ]

    def test_a_closed_deal_needs_no_next_step(self):
        deal = {"id": "o1", "name": "Won", "companyId": "c1", "stage": "CUSTOMER"}
        plan = _plan({"companies": [COMPANY], "opportunities": [deal]})
        assert plan.blocking == [] and plan.fixup_candidates == 0

    def test_a_deal_on_a_company_below_qualified_in_the_file(self):
        plan = _plan(
            {"companies": [{**COMPANY, "stage": "CONTACTED"}], "opportunities": [OPEN_DEAL]}
        )
        assert ("companies", 1, "deal_below_qualified", False) in _kinds(plan)
        assert plan.writes["companies"][0].values["lead_stage"] == "deal_open"

    def test_a_missing_fx_rate_blocks_with_the_contract_sentence(self):
        deal = {
            **OPEN_DEAL,
            "amount": "5000",
            "currency": "AUD",
            "createdAt": "2026-09-10T06:00:00Z",
        }
        plan = _plan({"companies": [COMPANY], "opportunities": [deal]})
        assert plan.blocking == ["No AUD rate for 2026-09 in Nunnari Labs."]

    def test_an_amount_converts_at_the_month_it_was_created(self):
        deal = {**OPEN_DEAL, "amount": "1000", "currency": "USD", "createdAt": "2026-10-02"}
        [write] = _plan({"companies": [COMPANY], "opportunities": [deal]}).writes["opportunities"]
        assert {k: write.values[k] for k in ("amount", "amount_in_inr", "fx_month")} == {
            "amount": "1000.00",
            "amount_in_inr": "83500.00",
            "fx_month": "2026-09-01",
        }

    def test_a_deal_whose_company_is_nowhere_is_skipped(self):
        plan = _plan({"opportunities": [OPEN_DEAL]})
        assert plan.writes["opportunities"] == []
        assert plan.counts["opportunities"]["skipped"] == 1
        assert _kinds(plan) == [("opportunities", 1, "missing_company", False)]

    def test_a_task_linked_to_nothing_known_is_skipped(self):
        task = {"id": "t1", "title": "Call", "targets": [{"type": "company", "id": "nowhere"}]}
        plan = _plan({"tasks": [task]})
        assert plan.counts["tasks"]["skipped"] == 1
        assert [k for _, _, k, _ in _kinds(plan)] == ["missing_target", "no_link"]

    def test_the_similar_name_sentence(self):
        assert rules.similar_sentence("Hooli", [{"name": "Hooli Inc", "similarity": 0.62}]) == (
            "Hooli looks like Hooli Inc (62%) in the CRM; merge them later if they are the same."
        )


class TestFixup:
    """Q-46 — the opt-in fix-up, dated the import day and counted."""

    def test_the_fixup_gives_review_after_import_dated_today(self):
        deal = {k: v for k, v in OPEN_DEAL.items() if k not in ("nextStep", "nextStepOn")}
        plan = _plan({"companies": [COMPANY], "opportunities": [deal]}, fixup=True)
        [write] = plan.writes["opportunities"]
        assert (write.values["next_step"], write.values["next_step_on"]) == (
            "Review after import",
            "2026-10-15",
        )
        assert (plan.fixup_candidates, plan.fixups, plan.blocking) == (1, 1, [])


class TestRerun:
    """FR-CRM-MIG-07 — what is already there, unchanged, is not written."""

    def test_an_unchanged_company_is_counted_and_not_written(self):
        existing = {obj: {} for obj in rules.TABLES}
        existing["companies"]["c1"] = {
            "id": "id-c1",
            "twenty_id": "c1",
            "name": "Acme",
            "lead_stage": "new",
            "owner_id": ADMIN,
            "account_type_id": None,
            "city": None,
            "country": None,
            "description": None,
            "merged_into_id": None,
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
        facts = _facts(existing=existing, company_domains={"id-c1": [("acme.io", True)]})
        plan = _plan({"companies": [COMPANY]}, facts)
        assert plan.counts["companies"] == {
            "in_file": 1,
            "create": 0,
            "update": 0,
            "unchanged": 1,
            "skipped": 0,
        }
        assert plan.writes["companies"] == []

    def test_a_changed_company_writes_only_what_changed(self):
        existing = {obj: {} for obj in rules.TABLES}
        existing["companies"]["c1"] = {
            "id": "id-c1",
            "twenty_id": "c1",
            "name": "Acme Old",
            "lead_stage": "new",
            "owner_id": ADMIN,
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
        facts = _facts(existing=existing, company_domains={"id-c1": [("acme.io", True)]})
        [write] = _plan({"companies": [COMPANY]}, facts).writes["companies"]
        assert (write.op, write.values) == ("update", {"name": "Acme"})

    def test_a_deal_in_the_crm_keeps_its_stage(self):
        existing = {obj: {} for obj in rules.TABLES}
        existing["opportunities"]["o1"] = {
            "id": "id-o1",
            "twenty_id": "o1",
            "stage": "proposal",
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
        plan = _plan(
            {"companies": [COMPANY], "opportunities": [OPEN_DEAL]}, _facts(existing=existing)
        )
        [deal] = [w for w in plan.writes["opportunities"]]
        assert deal.op == "update" and "stage" not in deal.values
        assert ("opportunities", 1, "stage_differs", False) in _kinds(plan)

    @pytest.mark.parametrize(
        ("db", "file", "equal"),
        [
            ("1000.00", "1000", True),
            ("83.500000", "83.5", True),
            ("2026-10-01T10:00:00+00:00", "2026-10-01T10:00:00Z", True),
            ("2026-10-01", "2026-10-02", False),
            (None, "", False),
            (["a"], ["a"], True),
        ],
    )
    def test_values_compare_as_the_database_returns_them(self, db, file, equal):
        assert rules.same(db, file) is equal


class TestShape:
    """Contracts §2.9 — the declared shape and its aliases."""

    def test_nested_and_flat_names_read_the_same(self):
        nested = rules.read(
            "people",
            {"id": "p", "name": {"firstName": "Asha"}, "emails": {"primaryEmail": "a@b.co"}},
        )
        flat = rules.read("people", {"id": "p", "first_name": "Asha", "Email": "a@b.co"})
        assert (nested["first_name"], nested["email"]) == (flat["first_name"], flat["email"])

    def test_a_json_export_may_sit_under_data(self):
        assert rules.bundle_from_json({"data": {"companies": [COMPANY]}}) == {
            "companies": [COMPANY]
        }

    def test_a_file_without_records_is_refused(self):
        with pytest.raises(ValueError, match="holds no companies"):
            rules.bundle_from_json({"workspaceMembers": []})

    @pytest.mark.parametrize(
        ("name", "obj"),
        [("Companies.csv", "companies"), ("deals.csv", "opportunities"), ("x.csv", None)],
    )
    def test_a_csv_names_its_object(self, name, obj):
        assert rules.object_named(name) == obj
