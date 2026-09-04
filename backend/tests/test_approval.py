"""Who may decide, and who may read a reason — FR-APPR-05, NFR-05, Q-05.

Q-05 had no provisional answer in the source specification. What is asserted
here is our default, so that if Devansh overrules it the tests say plainly what
changed.
"""

from __future__ import annotations

from app.domain.approval import Person, approver_for, can_decide, can_view_reason

ADMIN = Person(id="vinita", role="admin", lead_id=None)
LEAD = Person(id="devansh", role="lead", lead_id=None)
OTHER_LEAD = Person(id="other-lead", role="lead", lead_id=None)
REPORT = Person(id="deepika", role="user", lead_id="devansh")
STRANGER = Person(id="tarun", role="user", lead_id="other-lead")


class TestQ05:
    """ "Who approves a lead's own leave?" — upward, to an admin."""

    def test_a_report_is_approved_by_their_lead(self):
        assert approver_for(REPORT) == "devansh"

    def test_a_lead_has_no_named_approver_so_it_falls_to_an_admin(self):
        assert approver_for(LEAD) is None
        assert can_decide(ADMIN, LEAD) is True

    def test_a_peer_lead_cannot_approve_another_lead(self):
        """Upward, not sideways."""
        assert can_decide(OTHER_LEAD, LEAD) is False


class TestFrAppr05:
    """A lead MUST NOT act on bookings outside their own reports."""

    def test_a_lead_decides_for_their_own_report(self):
        assert can_decide(LEAD, REPORT) is True

    def test_a_lead_cannot_decide_for_someone_elses_report(self):
        assert can_decide(LEAD, STRANGER) is False

    def test_an_admin_may_decide_for_anyone(self):
        assert can_decide(ADMIN, REPORT) is True
        assert can_decide(ADMIN, STRANGER) is True

    def test_a_plain_user_cannot_decide_for_anyone(self):
        assert can_decide(REPORT, STRANGER) is False


class TestNobodyApprovesTheirOwnLeave:
    """The clause that keeps approval from becoming a formality."""

    def test_a_lead_cannot_approve_their_own_booking(self):
        assert can_decide(LEAD, LEAD) is False

    def test_an_admin_cannot_approve_their_own_booking_either(self):
        """Without this, "an admin MAY act on any" would include themselves,
        and the one person whose absence is hardest to cover would be the one
        person nobody reviews."""
        assert can_decide(ADMIN, ADMIN) is False


class TestDeactivated:
    """FR-AUTH-06 — a deactivated account stops being able to act at once."""

    def test_a_deactivated_lead_cannot_decide(self):
        gone = Person(id="devansh", role="lead", lead_id=None, is_active=False)
        assert can_decide(gone, REPORT) is False

    def test_a_deactivated_admin_cannot_read_reasons(self):
        gone = Person(id="vinita", role="admin", lead_id=None, is_active=False)
        assert can_view_reason(gone, REPORT) is False


class TestNfr05:
    """Reasons are readable by the person, their lead, and admins. Nobody else."""

    def test_you_can_always_read_your_own_reason(self):
        assert can_view_reason(REPORT, REPORT) is True

    def test_your_lead_can_read_it(self):
        assert can_view_reason(LEAD, REPORT) is True

    def test_an_admin_can_read_it(self):
        assert can_view_reason(ADMIN, REPORT) is True

    def test_a_colleague_cannot(self):
        assert can_view_reason(STRANGER, REPORT) is False

    def test_another_teams_lead_cannot(self):
        """A sick-leave reason is health information about a named person."""
        assert can_view_reason(OTHER_LEAD, REPORT) is False


def test_deciding_and_reading_are_separate_permissions():
    """They coincide for most people, which is exactly why they are kept apart.

    A lead may read their own reason but may not approve their own leave.
    Collapsing the two into one check would quietly grant self-approval.
    """
    assert can_view_reason(LEAD, LEAD) is True
    assert can_decide(LEAD, LEAD) is False
