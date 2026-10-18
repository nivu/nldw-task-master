"""Handing work over, the pure rules — spec 008 FR-TASK-DEL-01, -02, -03, -05,
-06; data-model §2.1 (phase 2); `app/domain/tasks/delegation.py`.

New is computed from `assigned_by` and `accepted_at`; a hand-back gives the
task to its creator; one write's messages go to someone else made the
assignee and to the creator when someone else completes or hands back
their task, never to the person who acted; Waiting on names someone the
caller may assign.

Acceptance criteria (spec §12, phase 2):
- "Assigning to someone else sends exactly one message; completing or
  handing back tells the creator once" — TestNoticeEvents (end to end:
  test_task_delegation_routes.py TestMessages).
- "Waiting needs Waiting on, limited to people the caller may assign" —
  TestWaitingOn (end to end: test_task_delegation_routes.py TestWaitingOn).
"""

from __future__ import annotations

import pytest

from app.domain.tasks import delegation

NAV, TIM, SAN = "u-nav", "u-tim", "u-san"
NOW = "2026-10-20T06:30:00+00:00"


def task(**values) -> dict:
    return {
        "id": "t-1",
        "created_by": NAV,
        "assignee_id": TIM,
        "assigned_by": NAV,
        "accepted_at": None,
        "status": "open",
        **values,
    }


class TestNew:
    """FR-TASK-DEL-02."""

    def test_assigned_by_someone_else_and_not_accepted_is_new(self):
        assert delegation.is_new(task())

    def test_accepted_is_not_new(self):
        assert not delegation.is_new(task(accepted_at=NOW))

    def test_a_self_assignment_is_not_new(self):
        assert not delegation.is_new(task(assignee_id=NAV, assigned_by=None))

    def test_a_row_from_before_phase_2_is_not_new(self):
        assert not delegation.is_new(task(assigned_by=None))

    def test_assigned_by_the_assignee_is_not_new(self):
        assert not delegation.is_new(task(assigned_by=TIM))

    def test_the_assignee_acting_accepts(self):
        assert delegation.accepting(task(), TIM, NOW) == {"accepted_at": NOW}

    def test_someone_else_acting_does_not_accept(self):
        assert delegation.accepting(task(), NAV, NOW) == {}

    def test_acting_on_a_task_that_is_not_new_writes_nothing(self):
        assert delegation.accepting(task(accepted_at=NOW), TIM, NOW) == {}

    def test_a_new_assignment_is_set_by_the_actor_and_not_accepted(self):
        assert delegation.assignment(TIM, NAV) == {"assigned_by": NAV, "accepted_at": None}

    def test_assigning_yourself_is_set_by_nobody(self):
        assert delegation.assignment(NAV, NAV) == {"assigned_by": None, "accepted_at": None}


class TestHandBack:
    """FR-TASK-DEL-03."""

    def test_the_creator_is_the_assignee_again_set_by_the_assignee(self):
        assert delegation.hand_back_plan(task(), TIM) == {
            "assignee_id": NAV,
            "assigned_by": TIM,
            "accepted_at": None,
        }

    def test_only_the_current_assignee_hands_back(self):
        with pytest.raises(ValueError, match="assigned to"):
            delegation.hand_back_plan(task(), SAN)

    def test_the_creator_cannot_hand_back_their_own_task(self):
        with pytest.raises(ValueError, match="your own task"):
            delegation.hand_back_plan(task(assignee_id=NAV), NAV)


class TestNoticeEvents:
    """FR-TASK-DEL-01, -05."""

    def test_a_new_task_for_someone_else_tells_them(self):
        assert delegation.notice_events(None, task(), NAV) == [("assigned", TIM)]

    def test_a_new_task_for_yourself_tells_nobody(self):
        assert delegation.notice_events(None, task(assignee_id=NAV), NAV) == []

    def test_a_reassignment_tells_the_new_assignee(self):
        assert delegation.notice_events(task(), task(assignee_id=SAN), NAV) == [("assigned", SAN)]

    def test_taking_a_task_yourself_tells_nobody(self):
        assert delegation.notice_events(task(), task(assignee_id=SAN), SAN) == []

    def test_a_change_that_keeps_the_assignee_tells_nobody(self):
        assert delegation.notice_events(task(), task(status="waiting"), NAV) == []

    def test_someone_else_completing_tells_the_creator(self):
        assert delegation.notice_events(task(), task(status="done"), TIM) == [("completed", NAV)]

    def test_the_creator_completing_tells_nobody(self):
        assert delegation.notice_events(task(), task(status="done"), NAV) == []

    def test_a_task_already_done_tells_nobody_again(self):
        assert delegation.notice_events(task(status="done"), task(status="done"), TIM) == []

    def test_a_hand_back_tells_the_creator_and_nothing_else(self):
        after = task(assignee_id=NAV, assigned_by=TIM)
        assert delegation.notice_events(task(), after, TIM, handed_back=True) == [
            ("handed_back", NAV)
        ]


class TestWaitingOn:
    """FR-TASK-DEL-06."""

    def test_someone_the_caller_may_assign(self):
        assert delegation.waiting_on_refusal(SAN, {NAV, SAN}) is None

    def test_anyone_else_is_refused(self):
        assert delegation.waiting_on_refusal(SAN, {NAV}) == delegation.WAITING_ON

    def test_the_nudge_default_and_range(self):
        assert (
            delegation.DEFAULT_NUDGE_DAYS,
            delegation.MIN_NUDGE_DAYS,
            delegation.MAX_NUDGE_DAYS,
        ) == (5, 1, 30)
