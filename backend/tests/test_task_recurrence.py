"""Recurrence rules — spec 008 FR-TASK-RRULE-01, -02, -06; T9; NFR-TASK-08.

Pure, at fixed dates around the S1b clock (Tuesday 20 October 2026): the four
allowed forms and their one written form, the next due date after the
current one (weekly days, month ends, leap years), a template's first date,
and the rule in words.

Acceptance criteria (spec §12, phase 3):
- "Completing or cancelling a recurring task creates exactly one next
  occurrence with the right due date" — the right due date is
  TestNextDue; the one occurrence is `test_task_recurrence_routes.py`.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.tasks import recurrence
from app.domain.tasks.recurrence import INVALID

TUE = date(2026, 10, 20)


# ---------------------------------------------------------------------------
# FR-TASK-RRULE-01 — the four forms
# ---------------------------------------------------------------------------


class TestAllowedForms:
    @pytest.mark.parametrize(
        "rule",
        [
            "FREQ=DAILY",
            "FREQ=WEEKLY;BYDAY=MO",
            "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU",
            "FREQ=MONTHLY;BYMONTHDAY=1",
            "FREQ=MONTHLY;BYMONTHDAY=31",
            "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29",
            "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31",
        ],
    )
    def test_each_form_is_kept_as_written(self, rule):
        assert recurrence.normalise(rule) == rule

    def test_parts_in_any_order_and_case_are_written_one_way(self):
        assert recurrence.normalise("byday=th,mo;freq=weekly") == "FREQ=WEEKLY;BYDAY=MO,TH"
        assert (
            recurrence.normalise(" BYMONTHDAY=29 ; FREQ=YEARLY ; BYMONTH=2 ")
            == "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29"
        )

    @pytest.mark.parametrize(
        "rule",
        [
            "",
            "FREQ=HOURLY",
            "FREQ=DAILY;INTERVAL=2",
            "FREQ=WEEKLY",
            "FREQ=WEEKLY;BYDAY=",
            "FREQ=WEEKLY;BYDAY=MO,MO",
            "FREQ=WEEKLY;BYDAY=XX",
            "FREQ=WEEKLY;BYDAY=1MO",
            "FREQ=MONTHLY",
            "FREQ=MONTHLY;BYMONTHDAY=0",
            "FREQ=MONTHLY;BYMONTHDAY=32",
            "FREQ=MONTHLY;BYMONTHDAY=-1",
            "FREQ=MONTHLY;BYDAY=MO",
            "FREQ=YEARLY",
            "FREQ=YEARLY;BYMONTH=13;BYMONTHDAY=1",
            "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30",
            "FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=31",
            "FREQ=DAILY;FREQ=DAILY",
            "FREQ=DAILY;COUNT=3",
            "RRULE:FREQ=DAILY",
        ],
    )
    def test_anything_else_is_refused_with_one_sentence(self, rule):
        with pytest.raises(ValueError) as raised:
            recurrence.parse(rule)
        assert str(raised.value) == INVALID


# ---------------------------------------------------------------------------
# FR-TASK-RRULE-02, -06 — the next due date after the current one
# ---------------------------------------------------------------------------


class TestNextDue:
    def test_daily_is_the_next_day(self):
        assert recurrence.next_due("FREQ=DAILY", TUE) == date(2026, 10, 21)
        assert recurrence.next_due("FREQ=DAILY", date(2026, 12, 31)) == date(2027, 1, 1)

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            (date(2026, 10, 19), date(2026, 10, 22)),  # Monday → Thursday
            (TUE, date(2026, 10, 22)),  # Tuesday → Thursday
            (date(2026, 10, 22), date(2026, 10, 26)),  # Thursday → next Monday
            (date(2026, 10, 25), date(2026, 10, 26)),  # Sunday → Monday
        ],
    )
    def test_weekly_on_days_is_the_next_of_those_days(self, current, expected):
        assert recurrence.next_due("FREQ=WEEKLY;BYDAY=MO,TH", current) == expected

    def test_weekly_on_one_day_is_a_week_later(self):
        assert recurrence.next_due("FREQ=WEEKLY;BYDAY=TU", TUE) == date(2026, 10, 27)

    def test_monthly_is_that_day_next_month(self):
        assert recurrence.next_due("FREQ=MONTHLY;BYMONTHDAY=20", TUE) == date(2026, 11, 20)

    def test_monthly_from_another_day_is_that_day_this_month(self):
        assert recurrence.next_due("FREQ=MONTHLY;BYMONTHDAY=25", TUE) == date(2026, 10, 25)

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            (date(2026, 1, 31), date(2026, 2, 28)),
            (date(2026, 2, 28), date(2026, 3, 31)),
            (date(2026, 3, 31), date(2026, 4, 30)),
            (date(2026, 4, 30), date(2026, 5, 31)),
            (date(2028, 1, 31), date(2028, 2, 29)),
        ],
    )
    def test_monthly_on_the_31st_is_the_month_end_when_shorter(self, current, expected):
        assert recurrence.next_due("FREQ=MONTHLY;BYMONTHDAY=31", current) == expected

    def test_monthly_on_the_30th_in_february(self):
        assert recurrence.next_due("FREQ=MONTHLY;BYMONTHDAY=30", date(2027, 1, 30)) == date(
            2027, 2, 28
        )
        assert recurrence.next_due("FREQ=MONTHLY;BYMONTHDAY=30", date(2027, 2, 28)) == date(
            2027, 3, 30
        )

    def test_yearly_is_that_day_next_year(self):
        assert recurrence.next_due("FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=20", TUE) == date(
            2027, 10, 20
        )

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            (date(2028, 2, 29), date(2029, 2, 28)),
            (date(2029, 2, 28), date(2030, 2, 28)),
            (date(2031, 2, 28), date(2032, 2, 29)),
        ],
    )
    def test_yearly_on_29_february_outside_a_leap_year_is_the_28th(self, current, expected):
        assert recurrence.next_due("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", current) == expected

    def test_never_the_current_date(self):
        for rule in ("FREQ=DAILY", "FREQ=WEEKLY;BYDAY=TU", "FREQ=MONTHLY;BYMONTHDAY=20"):
            assert recurrence.next_due(rule, TUE) > TUE


class TestFirstDue:
    def test_a_matching_day_is_its_own_first_date(self):
        assert recurrence.first_due("FREQ=WEEKLY;BYDAY=TU", TUE) == TUE

    def test_otherwise_the_next_matching_day(self):
        assert recurrence.first_due("FREQ=WEEKLY;BYDAY=MO", TUE) == date(2026, 10, 26)
        assert recurrence.first_due("FREQ=MONTHLY;BYMONTHDAY=5", TUE) == date(2026, 11, 5)


class TestTheEndsOfTheCalendar:
    """A date with no next date is refused with the sentence, never a crash."""

    def test_no_next_date_after_the_last_year(self):
        with pytest.raises(ValueError, match=INVALID):
            recurrence.next_due("FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1", date(9999, 6, 1))

    def test_no_first_date_after_the_last_day(self):
        with pytest.raises(ValueError, match=INVALID):
            recurrence.first_due("FREQ=MONTHLY;BYMONTHDAY=5", date(9999, 12, 31))

    def test_the_first_day_has_a_first_date(self):
        assert recurrence.first_due("FREQ=MONTHLY;BYMONTHDAY=5", date(1, 1, 1)) == date(1, 1, 5)


class TestLabel:
    @pytest.mark.parametrize(
        ("rule", "words"),
        [
            ("FREQ=DAILY", "Every day"),
            ("FREQ=WEEKLY;BYDAY=MO", "Every week on Monday"),
            ("FREQ=WEEKLY;BYDAY=MO,TH,FR", "Every week on Monday, Thursday and Friday"),
            ("FREQ=MONTHLY;BYMONTHDAY=1", "Every month on the 1st"),
            ("FREQ=MONTHLY;BYMONTHDAY=22", "Every month on the 22nd"),
            ("FREQ=MONTHLY;BYMONTHDAY=13", "Every month on the 13th"),
            ("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", "Every year on 29 February"),
        ],
    )
    def test_the_rule_in_words(self, rule, words):
        assert recurrence.label(rule) == words
