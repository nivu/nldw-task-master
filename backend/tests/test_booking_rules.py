"""Booking validation — FR-BOOK, §6.1, §6.4, and the Q-07/Q-08 defaults."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.rules import (
    ALLOWED_TRANSITIONS,
    CONSUMING_STATES,
    OCCUPYING_STATES,
    BookingRequest,
    check_allowance,
    check_bookable_day,
    check_duration,
    check_reason,
    check_timing,
    check_transition,
    validate_booking,
)

TODAY = date(2026, 9, 4)  # a Friday
MONDAY = date(2026, 9, 7)
SATURDAY = date(2026, 9, 5)


def ok(result):
    """A rule passed. Reads better than `is None` at every call site."""
    return result is None


class TestReasons:
    """FR-BOOK-03 and Q-07 — required for casual and sick, optional for WFH."""

    def test_casual_leave_needs_a_reason(self):
        assert not ok(check_reason("casual", None))
        assert not ok(check_reason("casual", "   "))

    def test_sick_leave_needs_a_reason(self):
        assert not ok(check_reason("sick", ""))

    def test_work_from_home_does_not(self):
        assert ok(check_reason("wfh", None))

    def test_a_supplied_reason_always_passes(self):
        assert ok(check_reason("casual", "Dentist appointment"))


class TestDuration:
    """FR-BOOK-02, §6.2 — full day or half day, nothing in between."""

    @pytest.mark.parametrize("value", ["0.5", "1.0"])
    def test_valid(self, value):
        assert ok(check_duration(Decimal(value)))

    @pytest.mark.parametrize("value", ["0.25", "0.75", "2.0", "0", "-0.5"])
    def test_rejected(self, value):
        assert not ok(check_duration(Decimal(value)))


class TestNonWorkingDays:
    """FR-BOOK-04, FR-CAL-05 — holidays and weekends cannot be booked."""

    def test_a_holiday_is_refused_by_name(self):
        failure = check_bookable_day(date(2026, 8, 15), holiday_name="Independence Day")
        assert failure and "Independence Day" in failure

    def test_a_saturday_is_refused(self):
        assert not ok(check_bookable_day(SATURDAY, holiday_name=None))

    def test_a_sunday_is_refused(self):
        assert not ok(check_bookable_day(date(2026, 9, 6), holiday_name=None))

    def test_a_weekday_is_allowed(self):
        assert ok(check_bookable_day(MONDAY, holiday_name=None))


class TestTiming:
    """§6.1 — the notice rules differ per category, deliberately."""

    def test_sick_leave_can_be_marked_today(self):
        """FR-BOOK-10, scenario §4.2 — Tarun wakes with a fever."""
        assert ok(check_timing(TODAY, "sick", today=TODAY, max_future_days=365))

    def test_sick_leave_cannot_be_booked_in_advance(self):
        """§6.1: sick leave is 'planned ahead: No'. Nobody knows in advance."""
        assert not ok(check_timing(MONDAY, "sick", today=TODAY, max_future_days=365))

    def test_casual_leave_cannot_be_taken_today(self):
        """§6.1: casual is 'same-day: No'. It is planned by definition."""
        failure = check_timing(TODAY, "casual", today=TODAY, max_future_days=365)
        assert failure and "sick leave" in failure.lower()

    def test_casual_leave_can_be_booked_ahead(self):
        """Scenario §4.1 — Deepika books the 28th in advance."""
        assert ok(check_timing(MONDAY, "casual", today=TODAY, max_future_days=365))

    def test_work_from_home_works_both_today_and_ahead(self):
        assert ok(check_timing(TODAY, "wfh", today=TODAY, max_future_days=365))
        assert ok(check_timing(MONDAY, "wfh", today=TODAY, max_future_days=365))

    def test_a_past_day_is_locked_for_every_category(self):
        """FR-BOOK-08 — the rule that outranks all the others above."""
        yesterday = date(2026, 9, 3)
        for category in ("wfh", "casual", "sick"):
            failure = check_timing(yesterday, category, today=TODAY, max_future_days=365)
            assert failure and "locked" in failure

    def test_booking_too_far_ahead_is_refused(self):
        """A-14 — an invented bound, so it is asserted rather than assumed."""
        assert not ok(check_timing(date(2028, 1, 1), "casual", today=TODAY, max_future_days=365))


class TestAllowance:
    """FR-BOOK-05 and Q-08 — blocked by default, flaggable later."""

    def test_a_booking_within_the_balance_is_allowed(self):
        assert ok(check_allowance(Decimal("1.5"), Decimal("0.5"), "casual", allow_excess=False))

    def test_exactly_exhausting_the_balance_is_allowed(self):
        assert ok(check_allowance(Decimal("0.5"), Decimal("0.5"), "casual", allow_excess=False))

    def test_going_below_zero_is_refused(self):
        failure = check_allowance(Decimal("0.5"), Decimal("1.0"), "casual", allow_excess=False)
        assert failure and "0.5 days remaining" in failure

    def test_the_message_names_the_numbers(self):
        """FR-BOOK-12's spirit: never make the person do the arithmetic."""
        failure = check_allowance(Decimal("0.0"), Decimal("1.0"), "sick", allow_excess=False)
        assert "0 days remaining" in failure and "1 day requested" in failure

    def test_q08_can_be_relaxed_without_touching_this_rule(self):
        assert ok(check_allowance(Decimal("0.0"), Decimal("1.0"), "casual", allow_excess=True))


class TestStateMachine:
    """§6.4 — which transitions exist, and which must not."""

    def test_pending_can_be_approved_or_rejected(self):
        assert ok(check_transition("pending", "approved"))
        assert ok(check_transition("pending", "rejected"))

    def test_an_approved_booking_can_still_be_withdrawn(self):
        """Scenario §4.3 — Devansh undoes an approved WFH day the same evening."""
        assert ok(check_transition("approved", "withdrawn"))

    def test_an_approved_booking_cannot_be_re_decided(self):
        assert not ok(check_transition("approved", "rejected"))

    def test_the_refusal_message_is_grammatical(self):
        """It is shown to a person, so "A approved booking" will not do."""
        assert check_transition("approved", "approved").startswith("An approved")
        assert check_transition("rejected", "approved").startswith("A rejected")

    @pytest.mark.parametrize("terminal", ["rejected", "withdrawn", "released", "unrecognised"])
    def test_terminal_states_go_nowhere(self, terminal):
        """Re-deciding a closed booking would silently resurrect its charge."""
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
        for target in ("approved", "rejected", "withdrawn"):
            assert not ok(check_transition(terminal, target))

    def test_holidays_can_release_a_booking_in_either_open_state(self):
        """FR-HOL-05."""
        assert ok(check_transition("pending", "released"))
        assert ok(check_transition("approved", "released"))


class TestStateSets:
    """§6.4's two different questions: does it cost, and does it occupy?"""

    def test_only_pending_and_approved_consume(self):
        assert CONSUMING_STATES == frozenset({"pending", "approved"})

    def test_unrecognised_occupies_a_day_without_consuming_it(self):
        assert "unrecognised" in OCCUPYING_STATES
        assert "unrecognised" not in CONSUMING_STATES

    def test_closed_states_neither_occupy_nor_consume(self):
        """So a withdrawn day can be re-booked."""
        for state in ("withdrawn", "rejected", "released"):
            assert state not in OCCUPYING_STATES
            assert state not in CONSUMING_STATES


class TestValidationOrder:
    """The order rules fire in is part of the product, not an accident."""

    def test_a_weekend_is_reported_as_a_weekend_not_as_a_shortfall(self):
        """Telling someone their balance is short when the real problem is
        that they tapped a Saturday sends them to the wrong place."""
        failure = validate_booking(
            BookingRequest(SATURDAY, "casual", Decimal("1.0"), "Wedding"),
            holiday_name=None,
            remaining=Decimal("0.0"),  # also insufficient
            today=TODAY,
            max_future_days=365,
            allow_excess=False,
        )
        assert "weekend" in failure

    def test_a_missing_reason_is_reported_before_the_calendar(self):
        failure = validate_booking(
            BookingRequest(SATURDAY, "casual", Decimal("1.0"), None),
            holiday_name=None,
            remaining=Decimal("5.0"),
            today=TODAY,
            max_future_days=365,
            allow_excess=False,
        )
        assert "reason" in failure

    def test_a_fully_valid_booking_passes(self):
        assert ok(
            validate_booking(
                BookingRequest(MONDAY, "casual", Decimal("0.5"), "Dentist appointment"),
                holiday_name=None,
                remaining=Decimal("1.5"),
                today=TODAY,
                max_future_days=365,
                allow_excess=False,
            )
        )
