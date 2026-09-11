from decimal import Decimal as D

from app.domain import utilisation as u


def test_utilisation_is_billable_over_capacity():
    m = u.MonthUtilisation(billable=D("120"), internal=D("8"), activity=D("16"), capacity=D("160"))
    assert m.logged == D("144")
    assert m.utilisation_pct == D("75.0")
    assert m.as_dict(D("80"))["below_target"] is True


def test_no_capacity_gives_no_percentage_not_infinity():
    m = u.MonthUtilisation(billable=D("8"), internal=D("0"), activity=D("0"), capacity=D("0"))
    assert m.utilisation_pct is None


def test_hiring_signal():
    h = u.HiringMonth(
        period="2026-10", demand_hours=D("1000"), supply_hours=D("640"), hours_per_fte=D("128")
    )
    assert h.shortfall_hours == D("360")
    assert h.fte_needed == D("2.8")
    assert h.cost_at(D("1200000")) == D("280000.00")


def test_no_shortfall_costs_nothing():
    h = u.HiringMonth(
        period="2026-10", demand_hours=D("100"), supply_hours=D("640"), hours_per_fte=D("128")
    )
    assert h.fte_needed == D("0.0") and h.cost_at(D("1")) == D("0.00")
