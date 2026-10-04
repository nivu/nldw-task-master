"""Tentative projects — spec 002 FR-PROJ-08, spec 005 FR-PNL-05.

The pure rules first, then the services that apply them over an in-memory
fake of the few Supabase reads they make: the monthly profit keeps the
pipeline out of every confirmed figure, the Time page neither offers nor
accepts it, and over-allocation counts confirmed work only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain import pnl
from app.domain import timesheets as rules
from app.services import analytics, timesheets, utilisation
from app.services import pnl as pnl_service
from app.services import supabase as db

D = Decimal
TODAY = date(2026, 10, 5)  # a Monday


class TestTheRules:
    def test_a_project_without_a_status_is_confirmed(self):
        assert not rules.is_tentative({"name": "Old row"})
        assert rules.is_tentative({"status": "tentative"})
        assert not rules.is_tentative({"status": "confirmed"})

    def test_time_on_a_tentative_project_is_refused(self):
        assert (
            rules.tentative_entry_refusal(
                project_name="FluxBooks", is_tentative=True, already_logged=False
            )
            == "FluxBooks is tentative."
        )

    def test_a_line_logged_before_it_became_tentative_can_be_resaved(self):
        assert (
            rules.tentative_entry_refusal(
                project_name="FluxBooks", is_tentative=True, already_logged=True
            )
            is None
        )

    def test_a_confirmed_project_is_not_refused(self):
        assert (
            rules.tentative_entry_refusal(
                project_name="Acme", is_tentative=False, already_logged=False
            )
            is None
        )


def alloc(percent, *, tentative=False, project="p"):
    return rules.Allocation(
        user_id="u1",
        project_id=project,
        starts_on=date(2026, 11, 2),
        ends_on=date(2026, 11, 6),
        percent=D(percent),
        tentative=tentative,
    )


class TestOverAllocationCountsConfirmedOnly:
    def test_tentative_work_does_not_over_commit_anybody(self):
        both = [alloc("80"), alloc("40", tentative=True, project="t")]
        assert rules.over_allocations(both, date(2026, 11, 2), date(2026, 11, 6)) == []

    def test_including_tentative_shows_who_it_would(self):
        both = [alloc("80"), alloc("40", tentative=True, project="t")]
        flagged = rules.over_allocations(
            both, date(2026, 11, 2), date(2026, 11, 6), include_tentative=True
        )
        assert len(flagged) == 5
        assert all(total == D("120") for _, _, total in flagged)

    def test_confirmed_over_allocation_is_still_flagged(self):
        both = [alloc("80"), alloc("40", project="q")]
        assert len(rules.over_allocations(both, date(2026, 11, 2), date(2026, 11, 6))) == 5


def cell(revenue, cost="0"):
    return pnl.Cell(revenue=D(revenue), cost=D(cost), basis="planned", complete=True)


class TestWeightedPipeline:
    def test_revenue_times_probability(self):
        assert pnl.weighted_revenue(D("100000"), 40) == D("40000")

    def test_no_probability_is_unknown_not_zero(self):
        assert pnl.weighted_revenue(D("100000"), None) is None

    def test_no_revenue_weighs_nothing_whatever_the_probability(self):
        assert pnl.weighted_revenue(D("0"), None) == D("0")

    def test_a_month_sums_cells_and_weights(self):
        total, weighted = pnl.sum_pipeline(
            [(cell("100000", "50000"), 40), (cell("20000"), 50)], "planned"
        )
        assert total.revenue == D("120000")
        assert total.cost == D("50000")
        assert weighted == D("50000")

    def test_one_unweighted_line_makes_the_weight_unknown(self):
        _, weighted = pnl.sum_pipeline([(cell("100000"), 40), (cell("20000"), None)], "planned")
        assert weighted is None


# ---------------------------------------------------------------------------
# The services, over a fake database
# ---------------------------------------------------------------------------

NOV = {"starts_on": "2026-11-01", "ends_on": "2026-11-30"}


class FakeDb:
    def __init__(self) -> None:
        self.profiles = [{"id": "u1", "display_name": "Asha", "is_active": True, "role": "user"}]
        self.projects = [
            {
                "id": "p-acme",
                "name": "Acme",
                "client": "Acme",
                "revenue": "210000",
                "is_archived": False,
                "category": "client",
                "status": "confirmed",
                "probability": None,
            },
            {
                "id": "p-flux",
                "name": "FluxBooks",
                "client": "FluxBooks",
                "revenue": "100000",
                "is_archived": False,
                "category": "client",
                "status": "tentative",
                "probability": 40,
            },
        ]
        self.phases = [
            {"id": f"ph-{p['id']}", "project_id": p["id"], "phase": "delivery", **NOV}
            for p in self.projects
        ]
        self.allocations = [
            {"id": "a1", "user_id": "u1", "project_id": "p-acme", "percent": "80", **NOV},
            {"id": "a2", "user_id": "u1", "project_id": "p-flux", "percent": "40", **NOV},
        ]
        self.entries: list[dict] = []
        self.audit: list[dict] = []

    def list_profiles(self, *, active_only=False):
        return self.profiles

    def list_cost_periods(self, user_id=None):
        return [{"user_id": "u1", "annual_ctc": "1200000", "starts_on": "2026-01-01"}]

    def list_projects(self, *, include_archived=False):
        return self.projects

    def list_phases(self, project_id=None):
        return self.phases

    def list_allocations(self, *, user_ids=None, project_id=None):
        return [a for a in self.allocations if user_ids is None or a["user_id"] in user_ids]

    def list_time_entries(self, *, user_ids=None, start=None, end=None, **_):
        return self.entries

    def list_holidays(self, start=None, end=None):
        return []

    def list_milestones(self, project_id=None):
        return []

    def list_bookings(self, **_):
        return []

    def find_booking_on(self, user_id, day, statuses):
        return None

    def insert_time_entry(self, data):
        self.entries.append(data)
        return data

    def insert_audit(self, entry):
        self.audit.append(entry)


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    fake = FakeDb()
    for name in dir(FakeDb):
        if not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(fake, name))
    for module in (pnl_service, timesheets, utilisation):
        monkeypatch.setattr(module, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(pnl_service, "_currency", lambda: "INR")
    monkeypatch.setattr(timesheets, "_grace_days", lambda: 7)
    monkeypatch.setattr(timesheets, "_max_hours", lambda: D("16"))
    monkeypatch.setattr(utilisation, "bench_threshold", lambda: D("60"))
    return fake


class TestMonthlyProfitLeavesThePipelineOut:
    """FR-PNL-05 — November 2026 is planned; Asha costs 100,000 a month and is
    80% on Acme (210,000, confirmed) and 40% on FluxBooks (100,000, 40%)."""

    def test_confirmed_figures_exclude_the_tentative_project(self, fake):
        out = pnl_service.monthly("2026-11", "2026-11")
        assert [p["project_id"] for p in out["projects"]] == ["p-acme"]
        assert out["totals"][0]["revenue"] == "210000.00"
        assert out["totals"][0]["cost"] == "100000.00"  # her CTC, unchanged
        assert out["people"][0]["cells"][0]["revenue"] == "210000.00"
        client = next(c for c in out["categories"] if c["category"] == "client")
        assert client["cells"][0]["revenue"] == "210000.00"
        assert client["cells"][0]["cost"] == "80000.00"

    def test_the_pipeline_block_is_weighted_and_separate(self, fake):
        pipeline = pnl_service.monthly("2026-11", "2026-11")["pipeline"]
        assert [p["project_id"] for p in pipeline["projects"]] == ["p-flux"]
        assert pipeline["projects"][0]["probability"] == 40
        month = pipeline["totals"][0]
        assert month["revenue"] == "100000.00"
        assert month["cost"] == "40000.00"
        assert month["weighted_revenue"] == "40000.00"

    def test_a_tentative_project_without_a_probability_has_no_weight(self, fake):
        fake.projects[1]["probability"] = None
        month = pnl_service.monthly("2026-11", "2026-11")["pipeline"]["totals"][0]
        assert month["revenue"] == "100000.00"
        assert month["weighted_revenue"] is None


class TestTheTimePage:
    def test_a_tentative_project_is_not_offered(self, fake):
        day = timesheets.day_for("u1", date(2026, 11, 2))
        assert [p["id"] for p in day["projects"]] == ["p-acme"]

    def test_time_on_a_tentative_project_is_refused(self, fake, monkeypatch):
        monkeypatch.setattr(timesheets, "today_in_company_tz", lambda: date(2026, 11, 2))
        line = {"project_id": "p-flux", "hours_office": "4", "note": "Scoping"}
        with pytest.raises(timesheets.TimesheetRefused) as refused:
            timesheets.save_day(user_id="u1", day=date(2026, 11, 2), lines=[line], actor_id="u1")
        assert refused.value.message == "FluxBooks is tentative."
        assert refused.value.status == 422
        assert fake.entries == []
        assert fake.audit == []


class TestAllocationViewsMarkTentative:
    def test_timeline_bars_are_marked_and_over_is_confirmed_only(self, fake):
        person = pnl_service.timeline(date(2026, 11, 1), date(2026, 11, 30))["people"][0]
        assert {b["project_id"]: b["tentative"] for b in person["allocations"]} == {
            "p-acme": False,
            "p-flux": True,
        }
        assert person["peak_percent"] == "80"
        assert person["over"] is False
        assert person["peak_with_tentative"] == "120"
        assert person["over_with_tentative"] is True

    def test_forecast_over_allocation_is_confirmed_only(self, fake):
        out = analytics.forecast(date(2026, 11, 2), date(2026, 11, 6))
        assert {p["project_id"]: p["tentative"] for p in out["projects"]} == {
            "p-acme": False,
            "p-flux": True,
        }
        assert out["over_allocated"] == []
        assert [o["user_id"] for o in out["over_with_tentative"]] == ["u1"]

    def test_resources_week_counts_confirmed_and_shows_both(self, fake):
        week = analytics.resources_timeline(date(2026, 11, 2), date(2026, 11, 8))["people"][0][
            "weeks"
        ][0]
        assert week["allocated_pct"] == "80.00"
        assert week["over"] is False
        assert week["with_tentative_pct"] == "120.00"
        assert week["over_with_tentative"] is True
        assert {p["project_id"]: p["tentative"] for p in week["projects"]} == {
            "p-acme": False,
            "p-flux": True,
        }

    def test_bench_is_judged_on_confirmed_work(self, fake):
        fake.allocations[0]["percent"] = "20"
        fake.allocations[0].update(starts_on="2026-10-05", ends_on="2026-10-11")
        fake.allocations[1].update(starts_on="2026-10-05", ends_on="2026-10-11")
        week = utilisation.bench(1)["people"][0]["weeks"][0]
        assert week["allocated_pct"] == "20"
        assert week["with_tentative_pct"] == "60"
        assert week["bench"] is True
