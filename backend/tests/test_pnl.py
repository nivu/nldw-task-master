"""CTC, monthly cost, revenue and profit — spec 005 §3."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain import pnl

D = Decimal
NO_HOLIDAYS: set[date] = set()


def period(annual, starts, ends=None, user="a"):
    return pnl.Period(user_id=user, annual_ctc=D(annual), starts_on=starts, ends_on=ends)


class TestWorkingDays:
    def test_september_2026_has_22_working_days(self):
        assert pnl.working_days(date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS) == 22

    def test_a_holiday_is_removed_but_leave_is_not(self):
        assert pnl.working_days(date(2026, 9, 1), date(2026, 9, 30), {date(2026, 9, 14)}) == 21

    def test_months_between(self):
        assert pnl.months_between(date(2026, 11, 15), date(2027, 2, 1)) == [
            (2026, 11),
            (2026, 12),
            (2027, 1),
            (2027, 2),
        ]


class TestTheFigureInForceOnTheDay:
    """Spec 005 §3.1 — past entries at past rates, upcoming at upcoming."""

    def test_the_covering_period_is_used(self):
        old = period("1200000", date(2026, 1, 1), date(2026, 9, 30))
        new = period("1500000", date(2026, 10, 1))
        assert pnl.period_on([old, new], date(2026, 9, 30)) is old
        assert pnl.period_on([old, new], date(2026, 10, 1)) is new

    def test_no_period_is_unknown_not_zero(self):
        assert pnl.period_on([period("1", date(2027, 1, 1))], date(2026, 6, 1)) is None

    def test_hourly_cost_is_monthly_over_working_hours(self):
        p = period("1200000", date(2026, 1, 1))  # 100,000 a month
        assert pnl.hourly_cost(p, 20) == D("100000") / D("160")
        assert pnl.daily_cost(p, 20) == D("5000")


class TestMonthCost:
    def test_a_fully_covered_month_costs_the_monthly_ctc(self):
        c = pnl.month_cost(
            [period("1200000", date(2026, 1, 1))], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS
        )
        assert c.cost == D("100000")
        assert c.complete and not c.unknown

    def test_a_raise_mid_month_is_pro_rated_by_working_days(self):
        old = period("1200000", date(2026, 1, 1), date(2026, 9, 15))  # 11 working days
        new = period("2400000", date(2026, 9, 16))  # 11 working days
        c = pnl.month_cost([old, new], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
        assert c.cost == D("100000") * 11 / 22 + D("200000") * 11 / 22
        assert c.complete

    def test_a_gap_is_incomplete_and_named_by_the_caller(self):
        c = pnl.month_cost(
            [period("1200000", date(2026, 9, 16))], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS
        )
        assert not c.complete and not c.unknown
        assert c.covered_days == 11

    def test_no_period_at_all_is_unknown(self):
        c = pnl.month_cost([], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
        assert c.unknown and c.cost == 0


class TestRevenueByMonth:
    """Spec 005 §3.2 — evenly over the timeline's working days."""

    def test_spread_over_the_timeline(self):
        window = (date(2026, 9, 1), date(2026, 10, 31))  # 22 + 22 working days
        sept = pnl.month_revenue(
            D("440000"), window, date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS
        )
        assert sept == D("220000")

    def test_a_month_outside_the_timeline_earns_nothing(self):
        window = (date(2026, 9, 1), date(2026, 9, 30))
        assert (
            pnl.month_revenue(D("1"), window, date(2026, 11, 1), date(2026, 11, 30), NO_HOLIDAYS)
            == 0
        )

    def test_no_timeline_or_no_revenue_is_none_not_zero(self):
        assert (
            pnl.month_revenue(D("1"), None, date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
            is None
        )
        assert (
            pnl.month_revenue(
                None,
                (date(2026, 9, 1), date(2026, 9, 2)),
                date(2026, 9, 1),
                date(2026, 9, 30),
                NO_HOLIDAYS,
            )
            is None
        )

    def test_window_is_earliest_start_to_latest_end(self):
        assert pnl.project_window(
            [(date(2026, 3, 1), date(2026, 4, 1)), (date(2026, 2, 1), date(2026, 3, 15))]
        ) == (
            date(2026, 2, 1),
            date(2026, 4, 1),
        )


class TestShares:
    def test_fractions_of_the_total(self):
        assert pnl.shares({"a": D("30"), "b": D("10")}) == {"a": D("0.75"), "b": D("0.25")}

    def test_nothing_to_share_is_empty_not_invented(self):
        assert pnl.shares({"a": D("0")}) == {}


class TestCell:
    def test_profit_and_percentage(self):
        cell = pnl.Cell(revenue=D("200000"), cost=D("150000"), basis="actual", complete=True)
        assert cell.profit == D("50000")
        assert cell.profit_pct == D("25")

    def test_unknown_cost_gives_no_profit(self):
        cell = pnl.Cell(revenue=D("1"), cost=None, basis="planned", complete=False)
        assert cell.profit is None and cell.profit_pct is None

    def test_zero_revenue_gives_no_percentage(self):
        cell = pnl.Cell(revenue=D("0"), cost=D("10"), basis="actual", complete=True)
        assert cell.profit == D("-10") and cell.profit_pct is None

    def test_basis(self):
        today = date(2026, 9, 12)
        assert pnl.basis_for(date(2026, 8, 1), date(2026, 8, 31), today) == "actual"
        assert pnl.basis_for(date(2026, 9, 1), date(2026, 9, 30), today) == "planned"
