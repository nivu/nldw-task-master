"""Hours by project category — spec 002 FR-ANALYTICS-08."""

from datetime import date
from decimal import Decimal as D

from app.domain import categories as c
from app.domain.timesheets import Allocation

OCT, NOV = (2026, 10), (2026, 11)
CATS = {"p-acme": "client", "p-poc": "poc", "p-site": "internal"}


def _run(months=(OCT,), logged=(), allocations=(), holidays=None, leave=None, cats=CATS):
    return c.by_category(
        list(months),
        logged=list(logged),
        allocations=list(allocations),
        categories=cats,
        holidays=holidays or {},
        leave=leave or {},
    )


def test_rows_are_every_category_in_fixed_order_then_activities():
    rows = _run()
    assert list(rows) == ["client", "poc", "product", "internal", "activity"]
    assert all(cells[0].logged == D("0") for cells in rows.values())


def test_logged_hours_land_in_their_projects_category_and_month():
    rows = _run(
        months=(OCT, NOV),
        logged=[
            c.Logged(date(2026, 10, 5), "p-acme", D("6")),
            c.Logged(date(2026, 10, 6), "p-acme", D("2.5")),
            c.Logged(date(2026, 10, 6), "p-site", D("1")),
            c.Logged(date(2026, 11, 2), "p-poc", D("4")),
        ],
    )
    assert [x.logged for x in rows["client"]] == [D("8.5"), D("0")]
    assert [x.logged for x in rows["internal"]] == [D("1"), D("0")]
    assert [x.logged for x in rows["poc"]] == [D("0"), D("4")]


def test_activities_are_their_own_row_with_no_plan():
    """Not folded into "internal": that would inflate the internal projects."""
    rows = _run(logged=[c.Logged(date(2026, 10, 5), None, D("3"))])
    assert rows["activity"][0].logged == D("3")
    assert rows["activity"][0].planned is None
    assert rows["internal"][0].logged == D("0")
    assert rows["activity"][0].as_dict() == {"logged_hours": "3.00", "planned_hours": None}


def test_a_project_with_no_category_counts_as_client():
    rows = _run(
        logged=[c.Logged(date(2026, 10, 5), "p-unknown", D("2"))],
        cats={**CATS, "p-unknown": None},
    )
    assert rows["client"][0].logged == D("2")


def test_lines_outside_the_range_are_ignored():
    rows = _run(logged=[c.Logged(date(2026, 9, 30), "p-acme", D("8"))])
    assert rows["client"][0].logged == D("0")


def test_planned_hours_are_allocation_hours_net_of_holidays_and_leave():
    # October 2026 has 22 weekdays. A 50% allocation for the whole month is
    # 22 × 8 × 0.5 = 88h; one holiday and one full day of leave take 8h off.
    a = Allocation("u-1", "p-acme", date(2026, 10, 1), date(2026, 10, 31), D("50"))
    assert _run(allocations=[a])["client"][0].planned == D("88.00")
    rows = _run(
        allocations=[a],
        holidays={"u-1": {date(2026, 10, 2)}},
        leave={"u-1": {date(2026, 10, 5): D("1")}},
    )
    assert rows["client"][0].planned == D("80.00")


def test_holidays_are_per_person():
    a = Allocation("u-1", "p-acme", date(2026, 10, 1), date(2026, 10, 31), D("100"))
    rows = _run(allocations=[a], holidays={"u-2": {date(2026, 10, 2)}})
    assert rows["client"][0].planned == D("176.00")


def test_an_allocation_spanning_months_is_split_by_month():
    a = Allocation("u-1", "p-poc", date(2026, 10, 26), date(2026, 11, 6), D("100"))
    rows = _run(months=(OCT, NOV), allocations=[a])
    # 26–30 Oct is five weekdays, 2–6 Nov five more.
    assert [x.planned for x in rows["poc"]] == [D("40.00"), D("40.00")]


def test_totals_sum_every_row_and_plan_only_the_categories():
    a = Allocation("u-1", "p-acme", date(2026, 10, 1), date(2026, 10, 31), D("50"))
    rows = _run(
        logged=[
            c.Logged(date(2026, 10, 5), "p-acme", D("6")),
            c.Logged(date(2026, 10, 5), None, D("2")),
        ],
        allocations=[a],
    )
    [total] = c.totals(rows)
    assert total.logged == D("8")
    assert total.planned == D("88.00")


def test_totals_of_nothing_is_nothing():
    assert c.totals({}) == []
