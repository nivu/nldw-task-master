"""Project health — spec 006 FR-HEALTH-02."""

from decimal import Decimal as D

from app.domain import health


class TestBurn:
    def test_in_step_is_green(self):
        assert health.burn(D("40"), D("100"), D("45")).colour == "green"

    def test_ahead_of_timeline_is_amber_then_red(self):
        assert health.burn(D("60"), D("100"), D("40")).colour == "amber"
        assert health.burn(D("80"), D("100"), D("40")).colour == "red"

    def test_over_budget_is_red_whatever_the_calendar_says(self):
        assert health.burn(D("101"), D("100"), D("5")).colour == "red"

    def test_no_budget_cannot_be_judged(self):
        d = health.burn(D("10"), None, D("50"))
        assert d.colour == "amber" and "budget" in d.detail


class TestMargin:
    def test_on_plan(self):
        assert health.margin(D("38"), D("40"), True).colour == "green"

    def test_below_plan(self):
        assert health.margin(D("28"), D("40"), True).colour == "amber"
        assert health.margin(D("10"), D("40"), True).colour == "red"

    def test_incomplete_cost_is_never_green(self):
        assert health.margin(D("90"), D("40"), False).colour == "amber"


class TestSchedule:
    def test_past_end_with_budget_unspent_is_red(self):
        assert health.schedule(D("110"), D("50"), False).colour == "red"

    def test_past_end_but_spent_is_amber(self):
        assert health.schedule(D("110"), D("95"), False).colour == "amber"

    def test_archived_is_settled(self):
        assert health.schedule(D("300"), D("10"), True).colour == "green"


def test_overall_is_the_worst():
    dims = [health.burn(D("1"), D("100"), D("50")), health.margin(D("1"), D("40"), True)]
    assert health.overall(dims) == "red"
