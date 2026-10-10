"""Work from home is a working day, not leave — spec 002 FR-ANALYTICS-07,
FR-TIME-10; spec 006 FR-NUDGE-01, FR-UTIL-01, FR-SIGN.

A WFH booking used to come back from `leave_days_for` like casual or sick
leave, so the week views showed it as leave, coverage stopped expecting the
day, the nudge skipped the person and utilisation capacity shrank.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api import ops
from app.api.deps import CurrentUser, current_user
from app.domain.rules import LEAVE_CATEGORIES, is_leave
from app.domain.timesheets import leave_warning
from app.main import app
from app.mcp import server
from app.services import analytics, digest, settings_store, utilisation
from app.services import supabase as db
from app.services import timesheets as timesheet_service

# Monday 28 Sep 2026 .. Sunday 4 Oct 2026; "today" is the Friday.
MONDAY = date(2026, 9, 28)
SUNDAY = date(2026, 10, 4)
FRIDAY = date(2026, 10, 2)

PEOPLE = {
    "u-lead": {"role": "lead", "lead_id": None},
    "u-mine": {"role": "user", "lead_id": "u-lead"},
}


def _booking(user_id: str, day: str, category: str, duration: str = "1.0") -> dict:
    return {
        "user_id": user_id,
        "date": day,
        "category": category,
        "duration": duration,
        "status": "approved",
    }


class FakeDb:
    """Just enough of app.services.supabase for the week, coverage, nudge and
    utilisation reads."""

    def __init__(self) -> None:
        self.profiles = {
            uid: {
                "id": uid,
                "email": f"{uid}@example.com",
                "display_name": uid,
                "is_active": True,
                **attrs,
            }
            for uid, attrs in PEOPLE.items()
        }
        self.bookings: list[dict] = []
        self.entries: list[dict] = []

    def get_profile(self, user_id):
        return dict(self.profiles[user_id]) if user_id in self.profiles else None

    def list_profiles(self, *, active_only=False):
        return list(self.profiles.values())

    def list_reports(self, lead_id, *, active_only=True):
        return [p for p in self.profiles.values() if p["lead_id"] == lead_id]

    def list_bookings(self, *, user_ids=None, start=None, end=None, statuses=None, **_):
        return [
            b
            for b in self.bookings
            if (user_ids is None or b["user_id"] in user_ids)
            and (start is None or b["date"] >= start.isoformat())
            and (end is None or b["date"] <= end.isoformat())
            and (statuses is None or b["status"] in statuses)
        ]

    def list_time_entries(self, *, user_ids=None, project_id=None, start=None, end=None):
        return [
            e
            for e in self.entries
            if (user_ids is None or e["user_id"] in user_ids)
            and (start is None or e["date"] >= start.isoformat())
            and (end is None or e["date"] <= end.isoformat())
        ]

    def list_projects(self, *, include_archived=False):
        return []

    def list_holidays(self, start=None, end=None):
        return []

    def default_location_id(self):
        return None

    def list_confirmations(self, *, user_ids=None, week_start=None):
        return []

    def list_cost_periods(self, *_, **__):
        return []


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    fake = FakeDb()
    for name in dir(FakeDb):
        if not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(fake, name))
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: default)
    monkeypatch.setattr(settings_store, "portal_start_date", lambda: None)
    monkeypatch.setattr(timesheet_service, "today_in_company_tz", lambda: FRIDAY)
    monkeypatch.setattr(ops, "today_in_company_tz", lambda: FRIDAY)
    return fake


@pytest.fixture
def week_fake(fake) -> FakeDb:
    """u-mine's week: Mon WFH, Tue casual, Wed half-day casual, Thu nothing,
    Fri 8 hours logged — and nothing logged on Mon to Thu."""
    fake.bookings = [
        _booking("u-mine", "2026-09-28", "wfh"),
        _booking("u-mine", "2026-09-29", "casual"),
        _booking("u-mine", "2026-09-30", "casual", "0.5"),
    ]
    fake.entries = [
        {
            "id": "e-1",
            "user_id": "u-mine",
            "date": "2026-10-02",
            "project_id": None,
            "activity": "learning",
            "hours_office": "8",
            "hours_home": "0",
        }
    ]
    return fake


class TestLeaveCategories:
    def test_wfh_is_not_leave(self):
        assert "wfh" not in LEAVE_CATEGORIES
        assert not is_leave("wfh")
        assert all(is_leave(c) for c in ("casual", "sick", "compoff"))
        assert not is_leave(None)

    def test_leave_days_for_drops_wfh_and_keeps_casual_and_compoff(self, fake):
        fake.bookings = [
            _booking("u-mine", "2026-09-28", "wfh"),
            _booking("u-mine", "2026-09-29", "casual"),
            _booking("u-mine", "2026-09-30", "compoff", "0.5"),
        ]
        leave = timesheet_service.leave_days_for(["u-mine"], MONDAY, SUNDAY)
        assert leave == {
            "u-mine": {date(2026, 9, 29): Decimal("1.0"), date(2026, 9, 30): Decimal("0.5")}
        }

    def test_bookings_by_day_for_keeps_every_category(self, fake):
        fake.bookings = [_booking("u-mine", "2026-09-28", "wfh")]
        out = timesheet_service.bookings_by_day_for(["u-mine"], MONDAY, SUNDAY)
        assert out["u-mine"][MONDAY] == {
            "category": "wfh",
            "label": "Work from home",
            "duration": "1.0",
        }


class TestLeaveWarning:
    def test_wfh_gives_no_warning(self):
        assert leave_warning("wfh", "1.0") is None

    @pytest.mark.parametrize("category", ["casual", "sick", "compoff"])
    def test_leave_still_warns(self, category):
        assert leave_warning(category, "1.0") is not None


class TestWeekFor:
    def test_shapes(self, week_fake):
        week = timesheet_service.week_for("u-mine", MONDAY)
        days = {d["date"]: d for d in week["days"]}

        wfh = days["2026-09-28"]
        assert wfh["on_leave"] is None
        assert wfh["wfh"] == "1.0"
        assert wfh["booked"]["category"] == "wfh"

        casual = days["2026-09-29"]
        assert casual["on_leave"] == "1.0"
        assert casual["wfh"] is None
        assert casual["booked"] == {
            "category": "casual",
            "label": "Casual leave",
            "duration": "1.0",
        }

        assert days["2026-10-01"]["booked"] is None

    def test_missing_days(self, week_fake):
        # WFH day expected; full casual day not; half-day casual still
        # expected; Thursday expected; Friday logged; weekend never.
        week = timesheet_service.week_for("u-mine", MONDAY)
        assert week["missing_days"] == ["2026-09-28", "2026-09-30", "2026-10-01"]

    def test_nothing_missing_before_joining(self, week_fake):
        week_fake.profiles["u-mine"]["joined_on"] = "2026-10-01"
        week = timesheet_service.week_for("u-mine", MONDAY)
        assert week["missing_days"] == ["2026-10-01"]


class TestCoverage:
    def test_wfh_day_is_expected(self, fake, monkeypatch):
        monkeypatch.setattr(analytics, "today_in_company_tz", lambda: FRIDAY)
        fake.bookings = [
            _booking("u-mine", "2026-09-28", "wfh"),
            _booking("u-mine", "2026-09-29", "casual"),
        ]
        row = analytics.coverage(["u-mine"], MONDAY, SUNDAY)["people"][0]
        assert "2026-09-28" in row["missing_days"]
        assert "2026-09-29" not in row["missing_days"]
        assert row["expected_days"] == 4


class TestNudge:
    def test_wfh_person_with_nothing_logged_is_nudged(self, fake, monkeypatch):
        fake.profiles = {"u-mine": fake.profiles["u-mine"]}
        fake.bookings = [_booking("u-mine", "2026-09-28", "wfh")]
        sent = []
        monkeypatch.setattr(digest, "_send", lambda person, *_: sent.append(person["id"]) or ["x"])
        out = digest.nothing_logged_today(MONDAY)
        assert out["sent"] == 1
        assert sent == ["u-mine"]

    def test_person_on_full_leave_is_not_nudged(self, fake, monkeypatch):
        fake.profiles = {"u-mine": fake.profiles["u-mine"]}
        fake.bookings = [_booking("u-mine", "2026-09-28", "sick")]
        monkeypatch.setattr(digest, "_send", lambda *_: ["x"])
        assert digest.nothing_logged_today(MONDAY)["sent"] == 0


class TestUtilisation:
    def _capacity(self) -> str:
        out = utilisation.monthly(["u-mine"], "2026-09", "2026-09")
        return out["people"][0]["months"][0]["capacity"]

    def test_wfh_does_not_reduce_capacity(self, fake, monkeypatch):
        monkeypatch.setattr(utilisation, "contracted_hours", lambda: lambda uid: None)
        before = self._capacity()
        fake.bookings = [_booking("u-mine", "2026-09-28", "wfh")]
        assert self._capacity() == before
        fake.bookings = [_booking("u-mine", "2026-09-28", "casual")]
        assert Decimal(self._capacity()) < Decimal(before)


class TestTeamWeeksRoute:
    @pytest.fixture
    def lead(self, week_fake):
        app.dependency_overrides[current_user] = lambda: CurrentUser(week_fake.profiles["u-lead"])
        yield TestClient(app)
        app.dependency_overrides.pop(current_user, None)

    def test_wfh_and_missing_days(self, lead):
        r = lead.get("/api/v1/team/timesheets", params={"week_start": MONDAY.isoformat()})
        assert r.status_code == 200
        (person,) = r.json()["people"]
        days = {d["date"]: d for d in person["days"]}
        assert days["2026-09-28"]["wfh"] == "1.0"
        assert days["2026-09-28"]["on_leave"] is None
        assert days["2026-09-28"]["booked"]["category"] == "wfh"
        assert days["2026-09-29"]["on_leave"] == "1.0"
        assert person["missing_days"] == ["2026-09-28", "2026-09-30", "2026-10-01"]


class TestMcpDocstrings:
    @pytest.mark.parametrize("tool", ["my_week", "someone_elses_week", "team_weeks"])
    def test_week_tools_describe_wfh(self, tool):
        doc = getattr(server, tool).__doc__
        assert "wfh" in doc
        assert "on_leave" in doc
