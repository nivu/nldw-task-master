"""Who is expected to log, and on which days — spec 002 FR-ANALYTICS-07.

Every "missing" number — coverage, the lead's gaps, the nudges, sign-off —
starts from this. Counting a day before the company used the portal, or a
person who never keeps a timesheet, turns a complete record into an
apparently incomplete one.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.timesheets import (
    check_employment_dates,
    employed_in_week,
    employed_on,
    employment,
    expected_log_days,
    logs_time,
    week_expected,
)
from app.services import analytics, confirmations, settings_store

# Monday 28 Sep 2026 .. Sunday 4 Oct 2026.
MONDAY = date(2026, 9, 28)
SUNDAY = date(2026, 10, 4)


class TestExpectedLogDays:
    def test_working_days_only(self):
        days = expected_log_days(MONDAY, SUNDAY, today=SUNDAY, holidays=set())
        assert days == [MONDAY + timedelta(days=n) for n in range(5)]

    def test_nothing_after_today(self):
        days = expected_log_days(MONDAY, SUNDAY, today=MONDAY + timedelta(days=1), holidays=set())
        assert days == [MONDAY, MONDAY + timedelta(days=1)]

    def test_holidays_and_full_leave_are_not_expected(self):
        tue, wed, thu = (MONDAY + timedelta(days=n) for n in (1, 2, 3))
        days = expected_log_days(
            MONDAY,
            SUNDAY,
            today=SUNDAY,
            holidays={tue},
            leave_days={wed: Decimal("1.0"), thu: Decimal("0.5")},
        )
        assert tue not in days
        assert wed not in days
        assert thu in days  # a half day still owes a timesheet

    def test_days_before_the_portal_start_are_not_expected(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), portal_start=date(2026, 10, 1)
        )
        assert days == [date(2026, 10, 1), date(2026, 10, 2)]

    def test_portal_start_before_the_range_changes_nothing(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), portal_start=date(2026, 1, 1)
        )
        assert len(days) == 5

    def test_portal_start_in_the_future_expects_nothing(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), portal_start=date(2026, 11, 2)
        )
        assert days == []


class TestJoiningAndLeaving:
    """FR-ANALYTICS-08 — nobody owes a timesheet before they joined or after
    they left."""

    def test_days_before_joining_are_not_expected(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), joined_on=date(2026, 9, 30)
        )
        assert days == [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]

    def test_days_after_leaving_are_not_expected(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), left_on=date(2026, 9, 29)
        )
        assert days == [MONDAY, date(2026, 9, 29)]

    def test_the_later_of_joining_and_portal_start_wins(self):
        days = expected_log_days(
            MONDAY,
            SUNDAY,
            today=SUNDAY,
            holidays=set(),
            portal_start=date(2026, 9, 29),
            joined_on=date(2026, 10, 1),
        )
        assert days == [date(2026, 10, 1), date(2026, 10, 2)]

    def test_left_before_the_range_expects_nothing(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), left_on=date(2026, 9, 1)
        )
        assert days == []

    def test_joining_in_the_future_expects_nothing(self):
        days = expected_log_days(
            MONDAY, SUNDAY, today=SUNDAY, holidays=set(), joined_on=date(2026, 11, 2)
        )
        assert days == []

    def test_leaving_after_today_still_stops_at_today(self):
        days = expected_log_days(
            MONDAY,
            SUNDAY,
            today=MONDAY + timedelta(days=1),
            holidays=set(),
            left_on=date(2026, 12, 31),
        )
        assert days == [MONDAY, MONDAY + timedelta(days=1)]

    def test_employment_reads_stored_strings_and_blanks(self):
        assert employment({"joined_on": "2026-09-30", "left_on": None}) == (date(2026, 9, 30), None)
        assert employment({"id": "p1"}) == (None, None)

    def test_employed_on_is_inclusive(self):
        person = {"joined_on": "2026-09-30", "left_on": "2026-10-02"}
        assert employed_on(person, date(2026, 9, 30))
        assert employed_on(person, date(2026, 10, 2))
        assert not employed_on(person, date(2026, 9, 29))
        assert not employed_on(person, date(2026, 10, 3))
        assert employed_on({"id": "p1"}, date(2000, 1, 1))

    def test_week_expected_needs_one_day_of_employment(self):
        assert week_expected(MONDAY, None, joined_on=SUNDAY) is True
        assert week_expected(MONDAY, None, joined_on=SUNDAY + timedelta(days=1)) is False
        assert week_expected(MONDAY, None, left_on=MONDAY) is True
        assert week_expected(MONDAY, None, left_on=MONDAY - timedelta(days=1)) is False

    def test_employed_in_week(self):
        assert employed_in_week({"left_on": "2026-09-27"}, MONDAY) is False
        assert employed_in_week({"left_on": "2026-09-28"}, MONDAY) is True
        assert employed_in_week({"id": "p1"}, MONDAY) is True

    def test_leaving_before_joining_is_refused(self):
        assert check_employment_dates(date(2026, 10, 2), date(2026, 10, 1))
        assert check_employment_dates(date(2026, 10, 1), date(2026, 10, 1)) is None
        assert check_employment_dates(None, date(2026, 10, 1)) is None
        assert check_employment_dates(date(2026, 10, 1), None) is None


class TestLogsTime:
    def test_default_is_yes(self):
        assert logs_time({"id": "p1"}) is True
        assert logs_time({"id": "p1", "logs_time": None}) is True

    def test_false_opts_out(self):
        assert logs_time({"id": "p1", "logs_time": False}) is False


class TestWeekExpected:
    def test_no_start_date_expects_every_week(self):
        assert week_expected(MONDAY, None) is True

    def test_week_entirely_before_start_is_not_expected(self):
        assert week_expected(MONDAY - timedelta(weeks=1), date(2026, 10, 1)) is False

    def test_week_containing_the_start_is_expected(self):
        assert week_expected(MONDAY, date(2026, 10, 1)) is True
        assert week_expected(MONDAY, SUNDAY) is True


class TestPortalStartSetting:
    @pytest.mark.parametrize("raw", ["", None, "not-a-date"])
    def test_empty_or_bad_means_none(self, monkeypatch, raw):
        monkeypatch.setattr(settings_store, "get", lambda key, default=None: raw)
        assert settings_store.portal_start_date() is None

    def test_reads_a_date(self, monkeypatch):
        monkeypatch.setattr(settings_store, "get", lambda key, default=None: "2026-10-01")
        assert settings_store.portal_start_date() == date(2026, 10, 1)


class TestAutoConfirmSkips:
    """`006` FR-SIGN-05 — no sign-off is expected where no time is."""

    @pytest.fixture
    def confirmed(self, monkeypatch):
        rows: list[dict] = []
        people = [
            {"id": "logs", "logs_time": True},
            {"id": "never", "logs_time": False},
        ]
        monkeypatch.setattr(confirmations, "_grace_days", lambda: 7)
        monkeypatch.setattr(confirmations.db, "list_profiles", lambda active_only: people)
        monkeypatch.setattr(confirmations.db, "list_confirmations", lambda **_: [])
        monkeypatch.setattr(confirmations.db, "upsert_confirmation", rows.append)
        return rows

    def test_people_who_do_not_log_are_not_auto_confirmed(self, monkeypatch, confirmed):
        monkeypatch.setattr(settings_store, "portal_start_date", lambda: None)
        confirmations.auto_confirm_closed(date(2026, 10, 20))
        assert confirmed
        assert {r["user_id"] for r in confirmed} == {"logs"}

    def test_weeks_outside_joining_and_leaving_are_not_auto_confirmed(self, monkeypatch, confirmed):
        monkeypatch.setattr(settings_store, "portal_start_date", lambda: None)
        monkeypatch.setattr(
            confirmations.db,
            "list_profiles",
            lambda active_only: [
                {"id": "joined", "joined_on": "2026-10-01"},
                {"id": "left", "left_on": "2026-09-20"},
            ],
        )
        confirmations.auto_confirm_closed(date(2026, 10, 20))
        # Closed weeks: 5 Oct, 28 Sep, 21 Sep, 14 Sep. "joined" owes the two
        # reaching 1 Oct; "left" owes only the week holding 20 Sep (14 Sep).
        assert sorted((r["user_id"], r["week_start"]) for r in confirmed) == [
            ("joined", MONDAY.isoformat()),
            ("joined", "2026-10-05"),
            ("left", "2026-09-14"),
        ]

    def test_weeks_before_the_portal_start_are_not_auto_confirmed(self, monkeypatch, confirmed):
        monkeypatch.setattr(settings_store, "portal_start_date", lambda: date(2026, 10, 1))
        confirmations.auto_confirm_closed(date(2026, 10, 20))
        # Closed weeks: 5 Oct, 28 Sep, 21 Sep, 14 Sep. Only those reaching
        # 1 Oct are due — the week holding it (28 Sep) counts.
        assert [r["week_start"] for r in confirmed] == ["2026-10-05", MONDAY.isoformat()]


class TestCoverageRespectsJoiningAndLeaving:
    """FR-ANALYTICS-08 through the coverage service — the number the nudges,
    the digest and the lead's gaps all read."""

    def test_only_days_in_post_are_expected(self, monkeypatch):
        people = [
            {"id": "new", "display_name": "New", "joined_on": "2026-10-01"},
            {"id": "gone", "display_name": "Gone", "left_on": "2026-09-29"},
        ]
        monkeypatch.setattr(analytics.db, "list_profiles", lambda: people)
        monkeypatch.setattr(analytics.db, "list_time_entries", lambda **_: [])
        monkeypatch.setattr(analytics, "holidays_between", lambda *_: set())
        monkeypatch.setattr(analytics, "leave_days_for", lambda *_: {})
        monkeypatch.setattr(analytics, "today_in_company_tz", lambda: SUNDAY)
        monkeypatch.setattr(settings_store, "portal_start_date", lambda: None)

        rows = {
            r["user_id"]: r for r in analytics.coverage(["new", "gone"], MONDAY, SUNDAY)["people"]
        }
        assert rows["new"]["missing_days"] == ["2026-10-01", "2026-10-02"]
        assert rows["gone"]["missing_days"] == ["2026-09-28", "2026-09-29"]
