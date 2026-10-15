"""CTC, monthly cost, revenue and profit — spec 005 §3; archived projects in
the monthly profit — spec 007 FR-CRM-CHG-04."""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

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


class TestContractedHours:
    """FR-CTC-06 — a CTC pays for a number of hours a week, 40 by default."""

    def test_a_period_is_full_time_unless_told_otherwise(self):
        assert period("1200000", date(2026, 1, 1)).hours_per_week == D("40")

    def test_ten_hours_a_week_costs_four_times_as_much_an_hour(self):
        # 50,000 a month for 10 h a week: 20 working days of 2 h is 40 hours.
        p = pnl.Period("a", D("600000"), date(2026, 1, 1), None, hours_per_week=D("10"))
        assert pnl.hourly_cost(p, 20) == D("50000") / D("40")
        assert pnl.hourly_cost(p, 20) == pnl.hourly_cost(period("600000", date(2026, 1, 1)), 20) * 4

    def test_daily_cost_ignores_the_hours(self):
        # An allocation percent is a share of their own hours, so a day of
        # them still costs the month over its working days.
        p = pnl.Period("a", D("600000"), date(2026, 1, 1), None, hours_per_week=D("10"))
        assert pnl.daily_cost(p, 20) == D("2500")

    def test_contracted_hours_follow_the_period_in_force(self):
        full = period("1200000", date(2026, 1, 1), date(2026, 9, 30))
        part = pnl.Period("a", D("600000"), date(2026, 10, 1), None, hours_per_week=D("10"))
        assert pnl.contracted_hours_on([full, part], date(2026, 9, 30)) == D("8")
        assert pnl.contracted_hours_on([full, part], date(2026, 10, 1)) == D("2")

    def test_a_day_with_no_period_counts_as_full_time(self):
        assert pnl.contracted_hours_on([], date(2026, 10, 1)) == D("8")


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

    def test_a_gap_between_periods_is_incomplete_and_named_by_the_caller(self):
        before = period("1200000", date(2026, 1, 1), date(2026, 9, 10))  # 8 working days
        after = period("1200000", date(2026, 9, 21))  # 8 working days
        c = pnl.month_cost([before, after], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
        assert not c.complete and not c.unknown
        assert c.missing_days == 6

    def test_days_before_joining_are_not_missing(self):
        # Joined mid-month: the first half is before they worked here.
        c = pnl.month_cost(
            [period("1200000", date(2026, 9, 16))], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS
        )
        assert c.complete and not c.unknown
        assert c.covered_days == 11 and c.missing_days == 0

    def test_a_month_wholly_before_joining_costs_nothing_and_is_complete(self):
        c = pnl.month_cost(
            [period("780000", date(2026, 10, 1))], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS
        )
        assert c.complete and not c.unknown
        assert c.cost == 0

    def test_days_after_the_last_period_ends_are_not_missing(self):
        # Left mid-month: the second half is after the contract ended.
        c = pnl.month_cost(
            [period("1200000", date(2026, 1, 1), date(2026, 9, 15))],
            date(2026, 9, 1),
            date(2026, 9, 30),
            NO_HOLIDAYS,
        )
        assert c.complete and not c.unknown
        assert c.covered_days == 11 and c.missing_days == 0
        assert c.cost == D("100000") * 11 / 22

    def test_a_month_wholly_after_leaving_costs_nothing_and_is_complete(self):
        c = pnl.month_cost(
            [period("1200000", date(2026, 1, 1), date(2026, 8, 31))],
            date(2026, 9, 1),
            date(2026, 9, 30),
            NO_HOLIDAYS,
        )
        assert c.complete and not c.unknown
        assert c.cost == 0 and c.expected_days == 0

    def test_a_gap_before_a_last_period_that_ended_is_still_missing(self):
        before = period("1200000", date(2026, 1, 1), date(2026, 9, 10))  # 8 working days
        after = period("1200000", date(2026, 9, 21), date(2026, 9, 25))  # 5 working days
        c = pnl.month_cost([before, after], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
        assert not c.complete and not c.unknown
        assert c.missing_days == 6

    def test_an_open_ended_period_keeps_every_later_day_expected(self):
        # An earlier open-ended period outlives a later one that ended.
        open_ended = period("1200000", date(2026, 1, 1))
        ended = period("1200000", date(2026, 3, 1), date(2026, 3, 31))
        c = pnl.month_cost([open_ended, ended], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
        assert c.expected_days == 22

    def test_no_period_at_all_is_unknown(self):
        c = pnl.month_cost([], date(2026, 9, 1), date(2026, 9, 30), NO_HOLIDAYS)
        assert c.unknown and c.cost == 0


class TestPaidBetween:
    """FR-PNL-02 — who belongs in a range's monthly report."""

    def test_a_period_that_ended_inside_the_range_counts(self):
        periods = [period("1200000", date(2026, 1, 1), date(2026, 8, 15))]
        assert pnl.paid_between(periods, date(2026, 8, 1), date(2026, 12, 31))

    def test_a_period_that_ended_before_the_range_does_not(self):
        periods = [period("1200000", date(2026, 1, 1), date(2026, 7, 31))]
        assert not pnl.paid_between(periods, date(2026, 8, 1), date(2026, 12, 31))


class TestEndOnLeaving:
    """FR-PNL-02 — a deactivated person whose CTC was never end-dated."""

    TODAY = date(2026, 10, 4)

    def test_an_active_person_is_untouched(self):
        periods = [period("1200000", date(2026, 1, 1))]
        assert pnl.end_on_leaving(periods, True, self.TODAY) == (periods, False)

    def test_a_deactivated_person_with_an_end_date_is_untouched(self):
        periods = [period("1200000", date(2026, 1, 1), date(2026, 8, 31))]
        assert pnl.end_on_leaving(periods, False, self.TODAY) == (periods, False)

    def test_an_open_period_ends_yesterday_and_is_flagged(self):
        periods, left_open = pnl.end_on_leaving(
            [period("1200000", date(2026, 1, 1))], False, self.TODAY
        )
        assert left_open
        assert periods[0].ends_on == date(2026, 10, 3)
        # A future month costs nothing for someone who has gone.
        c = pnl.month_cost(periods, date(2026, 11, 1), date(2026, 11, 30), NO_HOLIDAYS)
        assert c.cost == 0 and c.expected_days == 0

    def test_an_open_period_not_yet_started_is_dropped(self):
        periods, left_open = pnl.end_on_leaving(
            [period("1200000", date(2026, 11, 1))], False, self.TODAY
        )
        assert periods == [] and left_open

    def test_no_periods_is_not_paid(self):
        assert not pnl.paid_between([], date(2026, 8, 1), date(2026, 12, 31))


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

    def test_revenue_window_leaves_out_spillover(self):
        assert pnl.revenue_window(
            [
                ("delivery", date(2026, 4, 1), date(2026, 9, 30)),
                ("spillover", date(2026, 10, 1), date(2026, 12, 31)),
            ]
        ) == (date(2026, 4, 1), date(2026, 9, 30))

    def test_no_revenue_in_spillover_months(self):
        window = pnl.revenue_window(
            [
                ("delivery", date(2026, 4, 1), date(2026, 9, 30)),
                ("spillover", date(2026, 10, 1), date(2026, 12, 31)),
            ]
        )
        assert (
            pnl.month_revenue(
                D("3600000"), window, date(2026, 10, 1), date(2026, 10, 31), NO_HOLIDAYS
            )
            == 0
        )

    def test_only_spillover_has_no_revenue_timeline(self):
        assert pnl.revenue_window([("spillover", date(2026, 10, 1), date(2026, 12, 31))]) is None


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


class TestSumCells:
    def test_adds_revenue_and_cost(self):
        total = pnl.sum_cells(
            [
                pnl.Cell(revenue=D("100"), cost=D("40"), basis="actual", complete=True),
                pnl.Cell(revenue=D("0"), cost=D("25"), basis="actual", complete=True),
            ],
            "actual",
        )
        assert (total.revenue, total.cost, total.complete) == (D("100"), D("65"), True)

    def test_unknown_cost_makes_the_total_unknown_and_incomplete(self):
        total = pnl.sum_cells(
            [
                pnl.Cell(revenue=D("100"), cost=D("40"), basis="planned", complete=True),
                pnl.Cell(revenue=D("10"), cost=None, basis="planned", complete=False),
            ],
            "planned",
        )
        assert total.cost is None
        assert not total.complete

    def test_an_empty_category_is_zero_and_complete(self):
        total = pnl.sum_cells([], "actual")
        assert (total.revenue, total.cost, total.complete) == (0, 0, True)


class TestHolidaySpan:
    def test_covers_every_project_timeline_beyond_the_range(self):
        assert pnl.holiday_span(
            date(2026, 10, 1),
            date(2026, 10, 31),
            [(date(2026, 6, 8), date(2026, 11, 6)), None, (date(2026, 4, 1), date(2026, 9, 30))],
        ) == (date(2026, 4, 1), date(2026, 11, 6))

    def test_is_the_range_when_no_timeline_reaches_outside_it(self):
        assert pnl.holiday_span(date(2026, 10, 1), date(2026, 10, 31), [None]) == (
            date(2026, 10, 1),
            date(2026, 10, 31),
        )

    def test_month_revenue_is_the_same_whatever_the_range(self):
        # A holiday in September must reduce October's share even when only
        # October is on screen — the bug this span exists to prevent.
        window = (date(2026, 9, 1), date(2026, 10, 30))
        september_holiday = {date(2026, 9, 15)}
        october = pnl.month_revenue(
            D("1000000"), window, date(2026, 10, 1), date(2026, 10, 31), september_holiday
        )
        without = pnl.month_revenue(
            D("1000000"), window, date(2026, 10, 1), date(2026, 10, 31), NO_HOLIDAYS
        )
        assert october != without


# ---------------------------------------------------------------------------
# Archived projects in the monthly profit — spec 007 FR-CRM-CHG-04
# ---------------------------------------------------------------------------


class _Books:
    """Asha costs 100,000 a month. Acme is confirmed and live; Old is
    confirmed, archived, with eight hours logged in September; Flux is
    tentative; Gone is tentative and archived (a lost deal)."""

    def __init__(self) -> None:
        def project(pid, name, revenue, status="confirmed", archived=False):
            return {
                "id": pid,
                "name": name,
                "client": name,
                "revenue": revenue,
                "is_archived": archived,
                "category": "client",
                "status": status,
                "probability": 50 if status == "tentative" else None,
            }

        self.projects = [
            project("p-acme", "Acme", "300000"),
            project("p-old", "Old", None, archived=True),
            project("p-flux", "Flux", "100000", status="tentative"),
            project("p-gone", "Gone", "60000", status="tentative", archived=True),
        ]
        nov = {"starts_on": "2026-11-01", "ends_on": "2026-11-30"}
        self.phases = [
            {"id": f"ph-{p['id']}", "project_id": p["id"], "phase": "delivery", **nov}
            for p in self.projects
        ]
        self.allocations = [
            {"id": "a-acme", "user_id": "u1", "project_id": "p-acme", "percent": "50", **nov},
            {"id": "a-flux", "user_id": "u1", "project_id": "p-flux", "percent": "40", **nov},
            {"id": "a-gone", "user_id": "u1", "project_id": "p-gone", "percent": "40", **nov},
        ]
        self.entries = [
            {
                "user_id": "u1",
                "project_id": "p-old",
                "date": "2026-09-15",
                "hours_office": "8",
                "hours_home": "0",
            }
        ]

    def list_profiles(self, *, active_only=False, include_crm_only=False):
        return [{"id": "u1", "display_name": "Asha", "is_active": True, "role": "user"}]

    def list_cost_periods(self, user_id=None):
        return [{"user_id": "u1", "annual_ctc": "1200000", "starts_on": "2026-01-01"}]

    def list_projects(self, *, include_archived=False):
        return [dict(p) for p in self.projects if include_archived or not p["is_archived"]]

    def list_phases(self, project_id=None):
        return list(self.phases)

    def list_counting_allocations(self, *, user_ids=None, project_id=None):
        archived = {p["id"] for p in self.projects if p["is_archived"]}
        return [a for a in self.allocations if a["project_id"] not in archived]

    def list_time_entries(self, *, start=None, end=None, **_):
        return [e for e in self.entries if start.isoformat() <= e["date"] <= end.isoformat()]


@pytest.fixture
def books(monkeypatch) -> _Books:
    from app.services import pnl as pnl_service
    from app.services import supabase as db

    fake = _Books()
    for name, fn in inspect.getmembers(db, inspect.isfunction):
        if fn.__module__ == db.__name__ and not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(fake, name, None) or (lambda *a, **k: []))
    monkeypatch.setattr(pnl_service, "today_in_company_tz", lambda: date(2026, 10, 5))
    monkeypatch.setattr(pnl_service, "_currency", lambda: "INR")
    return fake


class TestArchivedProjectsInTheMonthlyProfit:
    def _report(self):
        from app.services import pnl as pnl_service

        return pnl_service.monthly("2026-09", "2026-11")

    def test_an_archived_tentative_project_is_out_of_the_pipeline_block(self, books):
        pipeline = self._report()["pipeline"]
        assert [p["project_id"] for p in pipeline["projects"]] == ["p-flux"]
        assert pipeline["totals"][2]["revenue"] == "100000.00"
        assert pipeline["totals"][2]["cost"] == "40000.00"

    def test_un_archiving_it_puts_it_back(self, books):
        books.projects[3]["is_archived"] = False
        pipeline = self._report()["pipeline"]
        assert [p["project_id"] for p in pipeline["projects"]] == ["p-flux", "p-gone"]
        assert pipeline["totals"][2]["revenue"] == "160000.00"

    def test_an_archived_confirmed_project_keeps_its_logged_cost(self, books):
        report = self._report()
        old = next(p for p in report["projects"] if p["project_id"] == "p-old")
        assert D(old["cells"][0]["cost"]) > 0
        books.projects[1]["is_archived"] = False
        again = next(p for p in self._report()["projects"] if p["project_id"] == "p-old")
        assert again["cells"][0] == old["cells"][0]


class TestAWonDealCountsOnce:
    """Spec 007 FR-CRM-TVA-08, -11; AC "Closed Won confirmed by a TVA manager or
    admin makes the project confirmed with revenue in INR … nothing is
    double-counted in the P&L". The handoff (033 `confirm`, as the fake
    applies it) turns the tentative project into a confirmed one with the
    handoff's revenue: the same row, so it leaves the pipeline block as it
    enters the confirmed P&L, and its revenue counts once."""

    def _won(self, books):
        from crm_fake import OWNER, FakeCrmStore, two_workspaces

        crm = FakeCrmStore()
        crm.profiles = {OWNER["id"]: dict(OWNER)}
        labs, _ = two_workspaces(crm)
        company = crm.add(
            "crm_companies", workspace_id=labs["id"], name="Flux", lead_stage="deal_open"
        )
        deal = crm.add(
            "crm_opportunities",
            workspace_id=labs["id"],
            name="Flux",
            company_id=company["id"],
            stage="negotiation",
            probability=75,
            amount=None,
            currency=None,
            amount_in_inr=None,
            fx_rate_used=None,
            twenty_id="tw",
            lost_reason_id=None,
            deleted_at=None,
        )
        flux = next(p for p in books.projects if p["id"] == "p-flux")
        crm.rows["projects"]["p-flux"] = {
            **flux,
            "lead_id": None,
            "source_opportunity_id": deal["id"],
            "crm_company_id": company["id"],
            "crm_archived_at": None,
        }
        crm.deal_transition(
            {"user_id": OWNER["id"], "kind": "user", "channel": "web", "workspace_id": labs["id"]},
            {
                "opportunity_id": deal["id"],
                "expect_stage": "negotiation",
                "opportunity": {
                    "stage": "closed_won",
                    "stage_entered_at": "now",
                    "probability": 100,
                    "won_on": "2026-10-05",
                },
                "history": {"from": "negotiation", "to": "closed_won"},
                "project": {
                    "action": "confirm",
                    "name": "Flux",
                    "category": "client",
                    "lead_id": None,
                    "probability": 100,
                    "phases": {"delivery": {"starts_on": "2026-11-01", "ends_on": "2026-11-30"}},
                    "revenue": "150000.00",
                },
            },
        )
        books.projects = [
            {k: crm.rows["projects"]["p-flux"][k] for k in p} if p["id"] == "p-flux" else p
            for p in books.projects
        ]

    def test_the_won_project_leaves_the_pipeline_and_counts_once(self, books):
        from app.services import pnl as pnl_service

        before = pnl_service.monthly("2026-09", "2026-11")
        assert [p["project_id"] for p in before["pipeline"]["projects"]] == ["p-flux"]
        self._won(books)
        after = pnl_service.monthly("2026-09", "2026-11")
        assert after["pipeline"]["projects"] == []
        confirmed = [p["project_id"] for p in after["projects"]]
        assert confirmed.count("p-flux") == 1
        flux = next(p for p in after["projects"] if p["project_id"] == "p-flux")
        assert flux["cells"][2]["revenue"] == "150000.00"
