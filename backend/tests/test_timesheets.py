"""Timesheet rules — spec 002 §5.3 and the capacity arithmetic behind §5.4.

Two things here are worth more than the rest: the edit window, because it is
deliberately NOT the rule `001` uses and the difference is easy to "fix" by
mistake; and capacity, because a wrong number there is invisible — it produces
a plausible forecast that is simply untrue.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.timesheets import (
    DEFAULT_MAX_HOURS_PER_DAY,
    Allocation,
    DayEntry,
    allocated_hours,
    check_can_log,
    check_day_total,
    check_hours,
    day_total,
    entry_locks_on,
    is_locked,
    leave_warning,
    over_allocations,
    overlap,
    phase_for,
    week_end,
    working_days,
)

# September 2026: the 7th is a Monday, so the 12th/13th are that weekend.
MON = date(2026, 9, 7)
FRI = date(2026, 9, 11)
SAT = date(2026, 9, 12)
SUN = date(2026, 9, 13)
NEXT_MON = date(2026, 9, 14)


def ok(result):
    return result is None


class TestTheEditWindow:
    """Q-01, FR-TIME-08 — deliberately not `001` §6.3."""

    def test_a_week_ends_on_sunday(self):
        assert week_end(MON) == SUN
        assert week_end(FRI) == SUN
        assert week_end(SUN) == SUN

    def test_mondays_entry_is_still_editable_on_friday(self):
        """The case a same-day lock would break.

        `001` locks a leave booking the moment its date passes, and that is
        right there: the record exists before the day, so deleting it
        afterwards reclaims an allowance. A timesheet is written after the day,
        and people forget Friday until Monday.
        """
        assert is_locked(MON, FRI) is False

    def test_still_editable_a_week_later(self):
        assert is_locked(MON, date(2026, 9, 18)) is False

    def test_locked_once_the_grace_period_has_passed(self):
        # Week ends Sun 13th, plus 7 days grace = locks on the 21st.
        assert entry_locks_on(MON) == date(2026, 9, 21)
        assert is_locked(MON, date(2026, 9, 20)) is False
        assert is_locked(MON, date(2026, 9, 21)) is True

    def test_the_whole_week_locks_together(self):
        """Monday and Friday of one week close on the same day.

        Per-entry expiry would mean a week that closes gradually, so somebody
        correcting a week on the last day finds half of it frozen.
        """
        assert entry_locks_on(MON) == entry_locks_on(FRI) == date(2026, 9, 21)

    def test_the_grace_period_is_configurable(self):
        assert entry_locks_on(MON, grace_days=0) == date(2026, 9, 14)
        assert is_locked(MON, date(2026, 9, 14), grace_days=0) is True

    def test_future_days_cannot_be_logged(self):
        """Logging tomorrow is predicting work, not recording it."""
        failure = check_can_log(NEXT_MON, today=MON)
        assert failure and "has not happened yet" in failure

    def test_today_can_be_logged(self):
        assert ok(check_can_log(MON, today=MON))

    def test_a_locked_day_says_when_it_closed(self):
        """A refusal that does not say when is a refusal nobody can act on."""
        failure = check_can_log(MON, today=date(2026, 9, 25))
        assert failure and "2026-09-21" in failure


class TestHours:
    """FR-TIME-01/04/05."""

    def test_a_normal_split_day(self):
        assert ok(check_hours(Decimal("5"), Decimal("2")))

    def test_all_office_or_all_home(self):
        assert ok(check_hours(Decimal("8"), Decimal("0")))
        assert ok(check_hours(Decimal("0"), Decimal("8")))

    def test_zero_is_not_a_record_of_anything(self):
        assert not ok(check_hours(Decimal("0"), Decimal("0")))

    def test_negative_is_refused(self):
        assert not ok(check_hours(Decimal("-1"), Decimal("2")))

    @pytest.mark.parametrize("value", ["0.25", "0.5", "0.75", "1", "7.5"])
    def test_quarter_hour_steps_are_allowed(self, value):
        assert ok(check_hours(Decimal(value), Decimal("0")))

    @pytest.mark.parametrize("value", ["1.3", "0.1", "2.7"])
    def test_odd_fractions_are_refused(self, value):
        assert not ok(check_hours(Decimal(value), Decimal("0")))

    def test_the_day_limit_catches_a_mistyped_number(self):
        """Q-06 — this exists to catch 80 typed for 8, not to police overwork."""
        failure = check_day_total(Decimal("80"))
        assert failure and "mistyped" in failure

    def test_a_long_but_plausible_day_is_allowed(self):
        assert ok(check_day_total(DEFAULT_MAX_HOURS_PER_DAY))

    def test_a_day_totals_across_projects(self):
        entries = [
            DayEntry("p1", Decimal("5"), Decimal("0")),
            DayEntry("p2", Decimal("0"), Decimal("2.5")),
        ]
        assert day_total(entries) == Decimal("7.5")


class TestLeaveClash:
    """Q-03, FR-TIME-10 — warn, never refuse."""

    def test_no_leave_means_no_warning(self):
        assert leave_warning(None, None) is None

    def test_a_sick_day_warns_but_the_caller_may_proceed(self):
        warning = leave_warning("sick", "1.0")
        assert warning and "sick leave" in warning
        assert "still log hours" in warning

    def test_a_half_day_says_so(self):
        assert "half day" in leave_warning("casual", "0.5")

    def test_the_warning_is_a_string_not_a_refusal(self):
        """The distinction the whole decision turns on.

        Refusing would make the humans lie and the effort vanish from the
        project. This returns text for the caller to show, and the caller is
        free to save anyway.
        """
        assert isinstance(leave_warning("sick", "1.0"), str)


class TestCapacity:
    """Q-02, FR-ANALYTICS-06 — where a wrong number is invisible."""

    def test_weekends_are_not_capacity(self):
        # Mon 7th to Sun 13th is five working days.
        assert working_days(MON, SUN, holidays=set()) == Decimal("5")

    def test_holidays_are_not_capacity(self):
        assert working_days(MON, SUN, holidays={date(2026, 9, 9)}) == Decimal("4")

    def test_approved_leave_is_not_capacity(self):
        """The reason a forecast is worth reading at all.

        Over raw calendar days, a team of three 'has' sixty days next month
        while two of them are away for a fortnight — and the plan built on that
        number is wrong before anybody starts.
        """
        result = working_days(MON, SUN, holidays=set(), leave_days={FRI: Decimal("1")})
        assert result == Decimal("4")

    def test_a_half_day_of_leave_removes_half_a_day(self):
        result = working_days(MON, SUN, holidays=set(), leave_days={FRI: Decimal("0.5")})
        assert result == Decimal("4.5")

    def test_leave_on_a_weekend_changes_nothing(self):
        """It was never capacity, so it cannot be subtracted twice."""
        result = working_days(MON, SUN, holidays=set(), leave_days={SAT: Decimal("1")})
        assert result == Decimal("5")

    def test_leave_on_a_holiday_changes_nothing(self):
        result = working_days(
            MON, SUN, holidays={date(2026, 9, 9)}, leave_days={date(2026, 9, 9): Decimal("1")}
        )
        assert result == Decimal("4")

    def test_a_full_allocation_is_a_full_week(self):
        hours = allocated_hours(Decimal("100"), MON, SUN, holidays=set())
        assert hours == Decimal("40.00")  # 5 days x 8 hours

    def test_half_allocation_is_half_the_hours(self):
        assert allocated_hours(Decimal("50"), MON, SUN, holidays=set()) == Decimal("20.00")

    def test_allocation_shrinks_with_leave_without_being_edited(self):
        """Q-02's whole point: a percentage OF CAPACITY, not of the calendar."""
        hours = allocated_hours(
            Decimal("100"), MON, SUN, holidays=set(), leave_days={FRI: Decimal("1")}
        )
        assert hours == Decimal("32.00")


class TestOverAllocation:
    """FR-ALLOC-04 — surface it, do not forbid it."""

    def test_two_half_allocations_are_fine(self):
        allocations = [
            Allocation("u1", "p1", MON, FRI, Decimal("50")),
            Allocation("u1", "p2", MON, FRI, Decimal("50")),
        ]
        assert over_allocations(allocations, MON, FRI) == []

    def test_beyond_full_capacity_is_flagged_per_day(self):
        allocations = [
            Allocation("u1", "p1", MON, FRI, Decimal("80")),
            Allocation("u1", "p2", MON, FRI, Decimal("40")),
        ]
        flagged = over_allocations(allocations, MON, FRI)
        assert len(flagged) == 5  # one per working day
        assert all(total == Decimal("120") for _, _, total in flagged)

    def test_only_the_overlapping_days_are_flagged(self):
        allocations = [
            Allocation("u1", "p1", MON, FRI, Decimal("80")),
            Allocation("u1", "p2", date(2026, 9, 10), FRI, Decimal("40")),
        ]
        days = [d for _, d, _ in over_allocations(allocations, MON, FRI)]
        assert days == [date(2026, 9, 10), date(2026, 9, 11)]

    def test_weekends_are_never_flagged(self):
        allocations = [
            Allocation("u1", "p1", MON, SUN, Decimal("100")),
            Allocation("u1", "p2", MON, SUN, Decimal("100")),
        ]
        assert all(d.weekday() < 5 for _, d, _ in over_allocations(allocations, MON, SUN))

    def test_two_people_are_not_added_together(self):
        allocations = [
            Allocation("u1", "p1", MON, FRI, Decimal("80")),
            Allocation("u2", "p1", MON, FRI, Decimal("80")),
        ]
        assert over_allocations(allocations, MON, FRI) == []


class TestPhaseResolution:
    """FR-TIME-07 — which phase a date belongs to."""

    PHASES = [
        ("pre-id", date(2026, 6, 1), date(2026, 6, 30)),
        ("delivery-id", date(2026, 7, 1), date(2026, 9, 30)),
        ("support-id", date(2026, 10, 1), date(2026, 12, 31)),
    ]

    def test_a_date_inside_a_phase(self):
        assert phase_for(date(2026, 8, 15), self.PHASES) == "delivery-id"

    def test_the_boundaries_are_inclusive(self):
        assert phase_for(date(2026, 7, 1), self.PHASES) == "delivery-id"
        assert phase_for(date(2026, 9, 30), self.PHASES) == "delivery-id"

    def test_a_date_outside_every_phase(self):
        """Not an error — effort against a project outside its planned window
        is real, and refusing to record it would hide exactly the overrun the
        analytics exist to show."""
        assert phase_for(date(2027, 1, 1), self.PHASES) is None

    def test_overlapping_phases_resolve_to_the_earliest_start(self):
        """Stable rather than dependent on row order."""
        overlapping = [
            ("late", date(2026, 7, 1), date(2026, 8, 1)),
            ("early", date(2026, 6, 1), date(2026, 8, 1)),
        ]
        assert phase_for(date(2026, 7, 15), overlapping) == "early"


def test_overlap_of_two_ranges():
    assert overlap(MON, FRI, date(2026, 9, 10), NEXT_MON) == (date(2026, 9, 10), FRI)
    assert overlap(MON, FRI, NEXT_MON, date(2026, 9, 20)) is None
