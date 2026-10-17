"""The Review Queue's rules — spec 009 FR-CRMAI-REV-02, -03, -04, -07, -08,
FR-CRMAI-SUM-07; spec §15 "A new synced email that updates the company's
Last inbound does not invalidate an open enrichment_conflict or company
rule_suggestion on it"; data-model §3.2.2.

Pure: `domain/crm/review.py`, with fixed values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.crm import review as rules

ADMIN, MEMBER, VIEWER, GONE, OUTSIDER = "u-admin", "u-member", "u-viewer", "u-gone", "u-out"
ROLES = {ADMIN: "admin", MEMBER: "member", VIEWER: "viewer", GONE: "member"}
ACTIVE = {ADMIN, MEMBER, VIEWER, OUTSIDER}


def owner(item_type: str, **facts) -> str | None:
    return rules.owner_for(item_type, roles=ROLES, active=ACTIVE, **facts)


class TestRouting:
    """FR-CRMAI-REV-02."""

    def test_review_a_next_step_goes_to_the_deal_owner(self):
        assert owner("next_step_change", deal_owner=MEMBER, company_owner=ADMIN) == MEMBER

    def test_review_a_commitment_goes_to_its_owner_when_a_member(self):
        assert owner("task_from_commitment", commitment_owner=ADMIN, mailbox_owner=MEMBER) == ADMIN

    def test_review_an_unknown_commitment_owner_falls_back_to_the_mailbox_owner(self):
        assert owner("task_from_commitment", commitment_owner=None, mailbox_owner=MEMBER) == MEMBER

    @pytest.mark.parametrize("who", [VIEWER, GONE, OUTSIDER])
    def test_review_a_commitment_owner_who_cannot_take_it_falls_back(self, who):
        assert owner("task_from_commitment", commitment_owner=who, mailbox_owner=MEMBER) == MEMBER

    def test_review_a_meeting_action_falls_back_to_the_deal_or_company_owner(self):
        assert owner("task_from_meeting", deal_owner=MEMBER, company_owner=ADMIN) == MEMBER
        assert owner("task_from_meeting", company_owner=ADMIN) == ADMIN

    @pytest.mark.parametrize("item_type", ["deal_link", "enrichment_conflict"])
    def test_review_company_items_go_to_the_company_owner(self, item_type):
        assert owner(item_type, company_owner=MEMBER, deal_owner=ADMIN) == MEMBER

    def test_review_a_rule_suggestion_by_its_level(self):
        facts = {"deal_owner": MEMBER, "company_owner": ADMIN}
        assert owner("rule_suggestion", rule_level="deal", **facts) == MEMBER
        assert owner("rule_suggestion", rule_level="company", **facts) == ADMIN

    @pytest.mark.parametrize(
        ("who", "why"),
        [
            (VIEWER, "a Viewer"),
            (GONE, "an inactive account"),
            (OUTSIDER, "not a member"),
            (None, "unknown"),
        ],
    )
    def test_review_goes_to_the_admins_when_the_owner_cannot_take_it(self, who, why):
        assert owner("next_step_change", deal_owner=who) is None, why
        assert owner("enrichment_conflict", company_owner=who) is None, why
        assert owner("task_from_commitment", commitment_owner=None, mailbox_owner=who) is None


class TestDedupe:
    """FR-CRMAI-REV-08, FR-CRMAI-SUM-07; data-model §3.2.2."""

    def test_review_the_key_names_source_type_and_target(self):
        assert (
            rules.dedupe_key("email_thread", "gmail:m:t", "next_step_change", "opportunity", "d1")
            == "email_thread|gmail:m:t|next_step_change|opportunity:d1"
        )

    def test_review_a_member_and_a_fingerprint_extend_the_target(self):
        assert rules.dedupe_key(
            "rule", "rule:R-4:m1", "rule_suggestion", "company", "c1", member_id="m1"
        ).endswith("|company:c1:m1")
        assert rules.dedupe_key(
            "email_thread", "gmail:m:t", "task_from_commitment", "company", "c1", fingerprint="ab"
        ).endswith("|company:c1#ab")

    def test_review_the_fingerprint_ignores_case_and_spacing(self):
        a = rules.commitment_fingerprint("Send the  SOW", "2026-10-24")
        assert a == rules.commitment_fingerprint(" send the sow ", date(2026, 10, 24))
        assert len(a) == 16 and int(a, 16) >= 0

    def test_review_a_new_date_is_a_new_commitment(self):
        assert rules.commitment_fingerprint("Send the SOW", "2026-10-24") != (
            rules.commitment_fingerprint("Send the SOW", "2026-10-25")
        )
        assert rules.commitment_fingerprint("Send the SOW", None) != (
            rules.commitment_fingerprint("Send the SOW", "2026-10-24")
        )


class TestStaleness:
    """FR-CRMAI-REV-04 — per field, never per record."""

    def test_review_a_last_inbound_change_does_not_refuse(self):
        recorded = {"city": "Pune"}
        company = {"city": "Pune", "last_inbound_at": "2026-10-18T09:00:00+00:00"}
        assert rules.stale_fields(recorded, company) == []

    def test_review_a_recorded_field_that_changed_is_named(self):
        recorded = {"next_step": "Call", "next_step_on": "2026-10-21"}
        deal = {"next_step": "Send deck", "next_step_on": "2026-10-21"}
        assert rules.stale_fields(recorded, deal) == ["next_step"]

    def test_review_a_date_and_an_empty_value_compare_as_they_mean(self):
        assert (
            rules.stale_fields({"next_step_on": date(2026, 10, 21)}, {"next_step_on": "2026-10-21"})
            == []
        )
        assert rules.stale_fields({"city": ""}, {"city": None}) == []
        assert rules.stale_fields({"city": None}, {"city": "Pune"}) == ["city"]

    def test_review_nothing_recorded_is_never_stale(self):
        assert rules.stale_fields({}, {"next_step": "Anything"}) == []


class TestExpiry:
    """FR-CRMAI-REV-07."""

    def test_review_an_item_expires_14_days_on(self):
        assert rules.expires_on("2026-10-18T03:00:00+00:00") == date(2026, 11, 1)

    def test_review_the_day_is_read_in_asia_kolkata(self):
        # 20:00 UTC is 01:30 the next day in Asia/Kolkata.
        assert rules.expires_on(datetime(2026, 10, 18, 20, 0, tzinfo=UTC)) == date(2026, 11, 2)


class TestWhoSeesWhat:
    """FR-CRMAI-REV-03."""

    mine = {"owner_id": MEMBER}
    theirs = {"owner_id": "u-other"}
    admins = {"owner_id": None}

    def test_review_an_admin_sees_and_decides_every_item(self):
        for item in (self.mine, self.theirs, self.admins):
            assert rules.may_see("admin", ADMIN, item) and rules.may_decide("admin", ADMIN, item)

    def test_review_a_member_sees_only_their_own(self):
        assert rules.may_see("member", MEMBER, self.mine)
        assert not rules.may_see("member", MEMBER, self.theirs)
        assert not rules.may_decide("member", MEMBER, self.admins)

    def test_review_a_viewer_has_no_queue(self):
        assert not rules.may_see("viewer", MEMBER, self.mine)

    def test_review_an_admins_own_queue_holds_the_admins_items(self):
        assert rules.is_mine("admin", ADMIN, self.admins)
        assert rules.is_mine("admin", ADMIN, {"owner_id": ADMIN})
        assert not rules.is_mine("admin", ADMIN, self.mine)
        assert not rules.is_mine("member", MEMBER, self.admins)
