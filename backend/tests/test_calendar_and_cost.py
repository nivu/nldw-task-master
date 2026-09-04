"""Calendar arithmetic and cost — §6.2, FR-CAL, A-13, and the Q-09 default."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.calendar import (
    is_weekend,
    month_matrix,
    period_bounds,
    period_of,
    periods_between,
    previous_period,
)
from app.domain.cost import SandwichRuleNotImplemented, bridging_days, cost_of


class TestPeriods:
    def test_period_of_a_date(self):
        assert period_of(date(2026, 9, 4)) == "2026-09"
        assert period_of(date(2026, 12, 31)) == "2026-12"

    def test_previous_period_crosses_the_year(self):
        assert previous_period("2026-01") == "2025-12"
        assert previous_period("2026-09") == "2026-08"

    def test_period_bounds(self):
        assert period_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
        assert period_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))
        assert period_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))

    def test_periods_between_is_inclusive(self):
        assert periods_between("2026-01", "2026-03") == ["2026-01", "2026-02", "2026-03"]

    def test_periods_between_crosses_a_year(self):
        assert periods_between("2025-11", "2026-02") == [
            "2025-11",
            "2025-12",
            "2026-01",
            "2026-02",
        ]

    def test_a_backwards_range_is_empty(self):
        """What the carry-forward calculation asks for in someone's first month."""
        assert periods_between("2026-09", "2026-08") == []

    def test_a_single_month_range(self):
        assert periods_between("2026-09", "2026-09") == ["2026-09"]

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "not-a-period", "202609"])
    def test_malformed_periods_are_rejected(self, bad):
        with pytest.raises(ValueError):
            period_bounds(bad)


class TestWeekends:
    """A-13 — the source spec never defines 'weekend'. Saturday and Sunday."""

    def test_saturday_and_sunday(self):
        assert is_weekend(date(2026, 9, 5)) is True
        assert is_weekend(date(2026, 9, 6)) is True

    @pytest.mark.parametrize("day", [7, 8, 9, 10, 11])
    def test_monday_to_friday_are_working_days(self, day):
        assert is_weekend(date(2026, 9, day)) is False


class TestMonthGrid:
    """FR-CAL-01 — one month at a time, laid out for rendering."""

    def test_every_row_has_seven_cells(self):
        for week in month_matrix("2026-09"):
            assert len(week) == 7

    def test_all_days_of_the_month_appear_exactly_once(self):
        cells = [cell for week in month_matrix("2026-09") for cell in week if cell]
        assert len(cells) == 30
        assert cells[0] == date(2026, 9, 1)
        assert cells[-1] == date(2026, 9, 30)

    def test_padding_is_none_not_a_date_from_an_adjacent_month(self):
        """A stray adjacent-month date in the grid is bookable-looking and is
        the classic way a calendar lets someone book the wrong day."""
        first_week = month_matrix("2026-09")[0]
        # 1 Sept 2026 is a Tuesday, so Monday's cell is padding.
        assert first_week[0] is None
        assert first_week[1] == date(2026, 9, 1)

    def test_a_month_starting_on_monday_has_no_leading_padding(self):
        assert month_matrix("2026-06")[0][0] == date(2026, 6, 1)


class TestCost:
    """§6.2 — full day 1.0, half day 0.5, holidays and weekends 0."""

    def test_a_full_day_costs_one(self):
        assert cost_of(date(2026, 9, 7), Decimal("1.0")) == Decimal("1.0")

    def test_a_half_day_costs_a_half(self):
        assert cost_of(date(2026, 9, 7), Decimal("0.5")) == Decimal("0.5")

    def test_a_holiday_costs_nothing(self):
        """FR-HOL-03, and the case that matters: a holiday declared over a
        date somebody had already booked (FR-HOL-05) must stop costing."""
        assert cost_of(
            date(2026, 8, 15), Decimal("1.0"), holiday_name="Independence Day"
        ) == Decimal("0.0")

    def test_a_weekend_costs_nothing(self):
        assert cost_of(date(2026, 9, 5), Decimal("1.0")) == Decimal("0.0")


class TestQ09SandwichRule:
    """Q-09 is unanswered. The default is off; the on-branch does not exist."""

    def test_weekends_do_not_consume_by_default(self):
        assert bridging_days(date(2026, 9, 4), date(2026, 9, 7), sandwich_rule=False) == []

    def test_switching_it_on_fails_loudly_rather_than_silently_doing_nothing(self):
        """The failure mode being prevented: an admin turns the setting on,
        sees no error, and believes weekends are being counted when the
        ledger is quietly ignoring them."""
        with pytest.raises(SandwichRuleNotImplemented):
            bridging_days(date(2026, 9, 4), date(2026, 9, 7), sandwich_rule=True)
