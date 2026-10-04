"""The CEO dashboard — spec 003 FR-DASH-02, FR-DASH-05, FR-DASH-08/09."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.domain import dashboard as dash
from app.domain.timesheets import coverage_ratio

MON = date(2026, 10, 5)


class TestMayView:
    @pytest.mark.parametrize(
        ("profile", "allowed"),
        [
            ({"is_owner": True, "dashboard_access": False}, True),
            ({"is_owner": False, "dashboard_access": True}, True),
            ({"is_owner": True, "dashboard_access": True}, True),
            ({"is_owner": False, "dashboard_access": False}, False),
            ({}, False),
        ],
    )
    def test_owner_or_authorised(self, profile, allowed):
        assert dash.may_view_dashboard(profile) is allowed

    @pytest.mark.parametrize("role", ["admin", "manager", "lead", "user"])
    def test_no_role_opens_it(self, role):
        assert dash.may_view_dashboard({"role": role}) is False

    @pytest.mark.parametrize("value", ["true", 1, "yes"])
    def test_only_a_real_true_counts(self, value):
        assert dash.may_view_dashboard({"dashboard_access": value, "is_owner": value}) is False


class TestLastWorkingDay:
    def test_monday_looks_at_friday(self):
        assert dash.last_working_day(MON, set()) == date(2026, 10, 2)

    def test_midweek_is_yesterday(self):
        assert dash.last_working_day(date(2026, 10, 7), set()) == date(2026, 10, 6)

    def test_holidays_are_skipped(self):
        holidays = {date(2026, 10, 2), date(2026, 10, 1)}
        assert dash.last_working_day(MON, holidays) == date(2026, 9, 30)


class TestCoverageRatio:
    def test_two_places(self):
        assert coverage_ratio(5, 6) == D("0.83")
        assert coverage_ratio(6, 6) == D("1.00")
        assert coverage_ratio(0, 6) == D("0.00")

    def test_nothing_expected_is_not_a_ratio(self):
        assert coverage_ratio(0, 0) is None


class TestDayStatus:
    def test_everyone_once(self):
        bookings = [
            {"user_id": "b", "status": "approved", "category": "wfh"},
            {"user_id": "c", "status": "pending", "category": "casual"},
            {"user_id": "d", "status": "approved", "category": "compoff"},
            {"user_id": "e", "status": "unrecognised", "category": None},
            {"user_id": "f", "status": "approved", "category": "sick"},
        ]
        out = dash.day_status(["a", "b", "c", "d", "e", "f"], bookings)
        assert out == {
            "people": 6,
            "present": 1,
            "wfh": 1,
            "leave": {"casual": 1, "sick": 1, "compoff": 1},
            "unrecognised": 1,
        }


def _person(uid: str) -> dict:
    return {"id": uid, "display_name": uid.title()}


def _alloc(uid: str, starts: str, ends: str, project: str = "p") -> dict:
    return {"user_id": uid, "project_id": project, "starts_on": starts, "ends_on": ends}


class TestUnallocated:
    def test_confirmed_allocation_today_counts(self):
        people = [_person("a"), _person("b"), _person("c")]
        allocations = [
            _alloc("a", "2026-10-01", "2026-10-31"),
            _alloc("b", "2026-10-06", "2026-10-31"),  # starts tomorrow
            _alloc("c", "2026-10-01", "2026-10-31", project="tentative"),
        ]
        out = dash.unallocated(people, allocations, MON, {"tentative"})
        assert [p["user_id"] for p in out] == ["b", "c"]


class TestAllocationsEnding:
    def test_ending_without_follow_on(self):
        people = [_person("a"), _person("b"), _person("c"), _person("d")]
        allocations = [
            _alloc("a", "2026-10-01", "2026-10-20"),
            _alloc("b", "2026-10-01", "2026-10-20"),
            _alloc("b", "2026-10-21", "2026-12-31"),  # follow-on
            _alloc("c", "2026-10-01", "2026-12-31"),  # beyond 30 days
            _alloc("d", "2026-10-01", "2026-10-25"),
            _alloc("d", "2026-10-26", "2026-12-31", project="tentative"),
        ]
        out = dash.allocations_ending(people, allocations, MON, {"tentative"})
        assert [(p["user_id"], p["ends_on"]) for p in out] == [
            ("a", "2026-10-20"),
            ("d", "2026-10-25"),
        ]

    def test_the_window_edge_is_inside(self):
        out = dash.allocations_ending(
            [_person("a")], [_alloc("a", "2026-10-01", "2026-11-04")], MON, set()
        )
        assert [p["ends_on"] for p in out] == ["2026-11-04"]
        out = dash.allocations_ending(
            [_person("a")], [_alloc("a", "2026-10-01", "2026-11-05")], MON, set()
        )
        assert out == []

    def test_long_finished_work_is_not_ending(self):
        out = dash.allocations_ending(
            [_person("a")], [_alloc("a", "2026-01-01", "2026-03-31")], MON, set()
        )
        assert out == []


def _summary(**overrides) -> dict:
    """A summary with nothing to report; each test turns one thing on."""
    base = {
        "money": {
            "currency": "INR",
            "breakdown": True,
            "incomplete": {"unrated": [], "no_timeline": []},
            "unattributed": [],
        },
        "cash": {
            "totals": {"overdue_receivable": "0"},
            "overdue_count": 0,
            "uninvoiced_overdue_count": 0,
        },
        "delivery": {"red": [], "spillover": []},
        "data_health": {
            "last_working_day": {"start": "2026-10-02", "coverage": "1.00"},
            "missing_last_working_day": [],
        },
        "people": {"unallocated": [], "ending": []},
    }
    for path, value in overrides.items():
        section, key = path.split("__")
        base[section][key] = value
    return base


class TestAttention:
    def test_a_quiet_company_has_nothing(self):
        assert dash.attention(_summary()) == []

    def test_red_project(self):
        red = [{"project_id": "p", "project_name": "Acme", "reasons": ["Over budget."]}]
        (alert,) = dash.attention(_summary(delivery__red=red))
        assert alert["severity"] == "high"
        assert alert["title"] == "Acme is red"
        assert alert["detail"] == "Over budget."
        assert alert["link"] == "/analytics"

    def test_revenue_with_nobody_allocated(self):
        rows = [{"project_id": "p", "project_name": "Acme"}]
        (alert,) = dash.attention(_summary(money__unattributed=rows))
        assert alert["severity"] == "medium"
        assert "Acme earns revenue this month" in alert["detail"]
        assert alert["link"] == "/projects"

    def test_overdue_invoices_quote_the_amount(self):
        cash = {
            "totals": {"overdue_receivable": "40000"},
            "overdue_count": 2,
            "uninvoiced_overdue_count": 1,
        }
        s = _summary()
        s["cash"] = cash
        (alert,) = dash.attention(s)
        assert alert["severity"] == "high"
        assert "2 invoices are past payment terms (INR 40000 owed)" in alert["detail"]
        assert "1 milestone is past due and not yet invoiced" in alert["detail"]

    def test_spillover_cost(self):
        spill = [
            {"project_name": "Acme", "cost": "50000.00"},
            {"project_name": "Beta", "cost": None},
        ]
        (alert,) = dash.attention(_summary(delivery__spillover=spill))
        assert alert["severity"] == "medium"
        assert "Acme (INR 50000.00 planned cost this month)" in alert["detail"]
        assert "Beta (cost unknown — incomplete)" in alert["detail"]

    def test_spillover_is_not_costed_without_the_breakdown(self):
        s = _summary(delivery__spillover=[{"project_name": "Acme", "cost": None}])
        s["money"]["breakdown"] = False
        (alert,) = dash.attention(s)
        assert alert["detail"] == "In an unpaid spill-over phase: Acme."

    @pytest.mark.parametrize(("ratio", "alerts"), [("0.79", 1), ("0.80", 0), (None, 0)])
    def test_coverage_below_80_percent(self, ratio, alerts):
        s = _summary(
            data_health__last_working_day={"start": "2026-10-02", "coverage": ratio},
            data_health__missing_last_working_day=[{"display_name": "Asha"}],
        )
        out = dash.attention(s)
        assert len(out) == alerts
        if alerts:
            assert out[0]["title"] == "Timesheets for 2026-10-02 are 79% complete"
            assert "1 person has not logged: Asha" in out[0]["detail"]

    def test_unallocated_and_ending(self):
        out = dash.attention(
            _summary(
                people__unallocated=[{"display_name": "Asha"}, {"display_name": "Bala"}],
                people__ending=[{"display_name": "Chitra", "ends_on": "2026-10-20"}],
            )
        )
        assert [a["severity"] for a in out] == ["medium", "low"]
        assert out[0]["title"] == "2 people are unallocated today"
        assert "Chitra (2026-10-20)" in out[1]["detail"]

    def test_incomplete_money_names_who_and_why(self):
        incomplete = {
            "unrated": [{"display_name": "Asha"}],
            "no_timeline": [{"project_id": "p", "project_name": "Acme"}],
        }
        (alert,) = dash.attention(_summary(money__incomplete=incomplete))
        assert alert["severity"] == "high"
        assert "no CTC recorded for Asha" in alert["detail"]
        assert "Acme" in alert["detail"]

    def test_most_severe_first(self):
        out = dash.attention(
            _summary(
                people__ending=[{"display_name": "Chitra", "ends_on": "2026-10-20"}],
                people__unallocated=[{"display_name": "Asha"}],
                delivery__red=[{"project_id": "p", "project_name": "Acme", "reasons": []}],
            )
        )
        assert [a["severity"] for a in out] == ["high", "medium", "low"]
        assert out[0]["detail"] == "Project health is red."
