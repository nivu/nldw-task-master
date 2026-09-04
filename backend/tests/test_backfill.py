"""The admin backfill — spec A-21.

This is the one sanctioned way past §6.3, the rule the product's integrity
rests on. These tests exist to pin the *edges* of the exception, because an
override that quietly widens stops being an override and becomes a repeal.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.rules import check_backfill

TODAY = date(2026, 9, 4)  # a Friday
YESTERDAY = date(2026, 9, 3)
LAST_MONDAY = date(2026, 8, 31)
NEXT_MONDAY = date(2026, 9, 7)  # a future WEEKDAY — a future Saturday would
# trip the weekend rule first and prove nothing
SATURDAY = date(2026, 8, 29)


def ok(result):
    return result is None


class TestPastOnly:
    """The hole is confined to dates that have already passed."""

    def test_a_past_weekday_is_allowed(self):
        assert ok(
            check_backfill(YESTERDAY, "casual", "Family event", holiday_name=None, today=TODAY)
        )

    def test_an_earlier_month_is_allowed(self):
        """The go-live case: recording what was taken before the portal existed."""
        assert ok(check_backfill(LAST_MONDAY, "sick", "Flu", holiday_name=None, today=TODAY))

    def test_today_is_refused(self):
        """Today is not locked, so the person books it themselves.

        Allowing it here would turn a narrow historical-correction power into a
        general "book leave for anybody" power, which nobody asked for.
        """
        failure = check_backfill(TODAY, "wfh", None, holiday_name=None, today=TODAY)
        assert failure and "has not passed yet" in failure

    def test_a_future_weekday_is_refused(self):
        failure = check_backfill(NEXT_MONDAY, "casual", "Wedding", holiday_name=None, today=TODAY)
        assert failure and "has not passed yet" in failure

    def test_the_refusal_says_who_should_book_it_instead(self):
        """A refusal that does not say what to do instead is a dead end."""
        failure = check_backfill(NEXT_MONDAY, "casual", "x", holiday_name=None, today=TODAY)
        assert "booked by the person themselves" in failure


class TestTheOrdinaryRulesStillApply:
    """Backfilling is an exception to the LOCK, and to nothing else."""

    def test_a_weekend_is_still_refused(self):
        """Weekends cost nothing (§6.2), so a backfill on one would record a
        charge that never existed."""
        failure = check_backfill(SATURDAY, "casual", "Family event", holiday_name=None, today=TODAY)
        assert failure and "weekend" in failure

    def test_a_holiday_is_still_refused(self):
        failure = check_backfill(
            date(2026, 8, 15), "casual", "x", holiday_name="Independence Day", today=TODAY
        )
        assert failure and "Independence Day" in failure

    def test_casual_leave_still_needs_a_reason(self):
        assert not ok(check_backfill(YESTERDAY, "casual", None, holiday_name=None, today=TODAY))

    def test_sick_leave_still_needs_a_reason(self):
        assert not ok(check_backfill(YESTERDAY, "sick", "  ", holiday_name=None, today=TODAY))

    def test_work_from_home_still_does_not(self):
        assert ok(check_backfill(YESTERDAY, "wfh", None, holiday_name=None, today=TODAY))

    def test_an_unknown_category_is_still_refused(self):
        assert not ok(check_backfill(YESTERDAY, "sabbatical", "x", holiday_name=None, today=TODAY))


class TestNoAllowanceCheck:
    """The one ordinary rule that deliberately does NOT apply.

    A backfill records what already happened. Refusing it because the allowance
    is short would leave the ledger describing a month that did not occur — and
    at go-live, when allowances are being set for the first time, that is the
    normal case rather than an edge one.
    """

    def test_check_backfill_takes_no_allowance_argument(self):
        """Structural, on purpose.

        If somebody later adds an allowance parameter here, this test fails and
        makes them state why — rather than the ledger silently starting to
        refuse historical records.
        """
        import inspect

        params = set(inspect.signature(check_backfill).parameters)
        assert "remaining" not in params
        assert "allow_excess" not in params

    def test_it_is_a_separate_function_from_ordinary_validation(self):
        """Not a flag on validate_booking.

        A shared code path with `skip_lock=True` is precisely how an exception
        stops being an exception: the next person to touch that function has to
        notice the flag to keep §6.3 true.
        """
        from app.domain.rules import validate_booking

        assert check_backfill is not validate_booking
        assert "day" in inspect_params(check_backfill)
        assert "remaining" in inspect_params(validate_booking)


def inspect_params(fn):
    import inspect

    return set(inspect.signature(fn).parameters)


@pytest.mark.parametrize("category", ["wfh", "casual", "sick"])
def test_every_category_can_be_backfilled(category):
    """Including sick leave, which cannot normally be booked for a past date.

    That is the whole point: at go-live the sick days somebody took last week
    are exactly the records that need entering.
    """
    assert ok(
        check_backfill(YESTERDAY, category, "Recorded at go-live", holiday_name=None, today=TODAY)
    )
