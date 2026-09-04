"""The edit window — §6.3, FR-BOOK-08, NFR-04, Q-03.

This is the rule the product's integrity rests on. §6.3: without it, someone
can mark a work-from-home day, take it, and quietly remove the record afterwards
to reclaim the allowance.

These tests exist because the failure mode is silent. A lock that is off by one
day, or that resolves in the wrong timezone, produces no error — it produces a
leave balance that is slowly, invisibly wrong.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.calendar import COMPANY_TZ, today_in_company_tz
from app.domain.rules import approval_locks_booking, is_locked


class TestTheBoundary:
    """The booked day itself is editable; every day after is not."""

    def test_today_is_editable(self):
        today = date(2026, 9, 4)
        assert is_locked(today, today) is False

    def test_tomorrow_is_editable(self):
        today = date(2026, 9, 4)
        assert is_locked(date(2026, 9, 5), today) is False

    def test_yesterday_is_locked(self):
        today = date(2026, 9, 4)
        assert is_locked(date(2026, 9, 3), today) is True

    def test_the_boundary_is_exactly_one_day(self):
        """Scenario §4.3, both halves, in one assertion pair.

        Devansh withdraws his WFH booking for the 1st at 21:00 on the 1st and
        it works. On the 2nd the same attempt is refused.
        """
        booked = date(2026, 9, 1)
        assert is_locked(booked, date(2026, 9, 1)) is False  # same day: allowed
        assert is_locked(booked, date(2026, 9, 2)) is True  # next day: refused

    def test_month_and_year_boundaries_do_not_confuse_it(self):
        assert is_locked(date(2026, 8, 31), date(2026, 9, 1)) is True
        assert is_locked(date(2025, 12, 31), date(2026, 1, 1)) is True
        assert is_locked(date(2026, 1, 1), date(2025, 12, 31)) is False


class TestTimezone:
    """NFR-03 — all dates are handled in Asia/Kolkata.

    The bug being guarded against: a server in UTC asked for `date.today()` at
    02:00 IST answers with *yesterday*, because 02:00 IST is 20:30 UTC the
    previous day. Every booking made in the Indian evening would then be
    treated as already locked.
    """

    def test_company_timezone_is_india(self):
        assert COMPANY_TZ == ZoneInfo("Asia/Kolkata")

    def test_evening_in_india_is_still_the_same_day(self):
        """21:00 IST — the exact moment §6.3 uses as its example.

        In UTC this instant is 15:30 the same day, so this particular case
        agrees. The next test covers the one that does not.
        """
        instant = datetime(2026, 9, 1, 21, 0, tzinfo=COMPANY_TZ)
        assert instant.astimezone(ZoneInfo("UTC")).date() == date(2026, 9, 1)
        assert instant.date() == date(2026, 9, 1)

    def test_after_midnight_india_is_the_previous_day_in_utc(self):
        """The case that breaks a naive implementation.

        00:30 IST on the 2nd is 19:00 UTC on the 1st. A server reading UTC
        would believe the 1st is still open when in Kolkata it has closed.
        """
        instant = datetime(2026, 9, 2, 0, 30, tzinfo=COMPANY_TZ)
        assert instant.date() == date(2026, 9, 2)
        assert instant.astimezone(ZoneInfo("UTC")).date() == date(2026, 9, 1)

        # The lock must follow the Kolkata date, so the 1st is now closed.
        assert is_locked(date(2026, 9, 1), instant.date()) is True

    def test_todays_date_comes_from_the_company_timezone(self):
        assert today_in_company_tz() == datetime.now(tz=COMPANY_TZ).date()


class TestQ03SecondHalf:
    """Q-03 also asks: does approval lock a booking earlier than midnight?"""

    def test_approval_does_not_shorten_the_correction_window(self):
        assert approval_locks_booking() is False


class TestServerClockOnly:
    """NFR-04 — the lock check MUST be enforced server-side."""

    def test_default_today_is_the_servers_own_clock(self):
        """Called with no `today`, the function must not consult anything else.

        The injectable argument exists for these tests. Application code passes
        nothing, so a client cannot influence the answer by any route.
        """
        yesterday = today_in_company_tz() - timedelta(days=1)
        tomorrow = today_in_company_tz() + timedelta(days=1)
        assert is_locked(yesterday) is True
        assert is_locked(tomorrow) is False


@pytest.mark.parametrize(
    "booked,today,expected",
    [
        (date(2026, 2, 28), date(2026, 3, 1), True),
        (date(2028, 2, 29), date(2028, 3, 1), True),  # leap day
        (date(2028, 2, 29), date(2028, 2, 29), False),
    ],
)
def test_awkward_dates(booked, today, expected):
    assert is_locked(booked, today) is expected
