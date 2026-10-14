"""The one capacity service — spec 007 FR-CRM-CHG-17 (with -03, -04).

The weekly arithmetic first (`domain/capacity.py`), then the service over an
in-memory fake of the reads it makes, then the forecast's `weeks`, which must
come back even when nothing counts any more (plan §5.7: the early return).
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.domain import capacity as calc
from app.domain import timesheets as rules
from app.services import analytics, settings_store
from app.services import capacity as service
from app.services import supabase as db

D = Decimal
MON = date(2026, 11, 2)
SUN = date(2026, 11, 8)
FULL_TIME = lambda uid: lambda day: D("8")  # noqa: E731


def week(people, allocations=(), *, holidays=None, leave=None, hours_on=FULL_TIME, start=MON):
    (only,) = calc.weekly(
        start,
        SUN,
        people=list(people),
        allocations=list(allocations),
        holidays=holidays or {},
        leave=leave or {},
        hours_on=hours_on,
    )
    return only


def alloc(percent, *, user="a", tentative=False, starts=MON, ends=SUN):
    return rules.Allocation(
        user_id=user,
        project_id="p",
        starts_on=starts,
        ends_on=ends,
        percent=D(percent),
        tentative=tentative,
    )


class TestSupply:
    def test_a_full_time_week_is_forty_hours(self):
        w = week([calc.Person("a")])
        assert (w.week_start, w.supply_hours, w.contributors) == (MON, D("40.00"), 1)

    def test_a_holiday_that_applies_to_the_person_is_taken_off(self):
        w = week(
            [calc.Person("a"), calc.Person("b")], holidays={"a": {date(2026, 11, 4)}, "b": set()}
        )
        assert w.supply_hours == D("72.00")

    def test_leave_is_taken_off_a_half_day_by_half(self):
        leave = {"a": {date(2026, 11, 3): D("1"), date(2026, 11, 4): D("0.5")}}
        assert week([calc.Person("a")], leave=leave).supply_hours == D("28.00")

    def test_nothing_before_joining_or_after_leaving(self):
        joined = calc.Person("a", joined_on=date(2026, 11, 4))
        left = calc.Person("b", left_on=date(2026, 11, 3))
        assert week([joined]).supply_hours == D("24.00")
        assert week([left]).supply_hours == D("16.00")

    def test_a_week_wholly_before_joining_has_no_supply_and_no_contributor(self):
        w = week([calc.Person("a", joined_on=date(2026, 12, 1))])
        assert (w.supply_hours, w.contributors) == (D("0.00"), 0)

    def test_a_part_timer_supplies_their_contracted_hours(self):
        hours = {"a": D("8"), "b": D("4")}
        w = week([calc.Person("a"), calc.Person("b")], hours_on=lambda uid: lambda day: hours[uid])
        assert w.supply_hours == D("60.00")

    def test_a_range_starting_midweek_counts_only_its_days(self):
        w = week([calc.Person("a")], start=date(2026, 11, 4))
        assert (w.week_start, w.supply_hours) == (MON, D("24.00"))

    def test_contributors_are_the_people_with_supply(self):
        people = [calc.Person("a"), calc.Person("b"), calc.Person("c")]
        everyone_off = {d: D("1") for d in (date(2026, 11, n) for n in range(2, 7))}
        assert week(people).contributors == 3
        assert week(people, leave={"c": everyone_off}).contributors == 2


class TestDemand:
    def test_confirmed_and_tentative_are_kept_apart(self):
        w = week([calc.Person("a")], [alloc("50"), alloc("25", tentative=True)])
        assert (w.confirmed_hours, w.tentative_hours) == (D("20.00"), D("10.00"))

    def test_demand_shrinks_with_leave_as_the_forecast_does(self):
        leave = {"a": {date(2026, 11, 3): D("1")}}
        assert week([calc.Person("a")], [alloc("50")], leave=leave).confirmed_hours == D("16.00")

    def test_an_allocation_counts_only_its_days_in_the_week(self):
        w = week([calc.Person("a")], [alloc("100", starts=date(2026, 11, 5))])
        assert w.confirmed_hours == D("16.00")

    def test_as_dict_is_decimal_strings(self):
        assert week([calc.Person("a")], [alloc("50")]).as_dict() == {
            "week_start": "2026-11-02",
            "supply_hours": "40.00",
            "confirmed_hours": "20.00",
            "tentative_hours": "0.00",
            "contributors": 1,
        }

    def test_week_starts_are_the_mondays_touching_the_range(self):
        assert calc.week_starts(date(2026, 11, 4), date(2026, 11, 16)) == [
            date(2026, 11, 2),
            date(2026, 11, 9),
            date(2026, 11, 16),
        ]


# ---------------------------------------------------------------------------
# The service, over a fake database
# ---------------------------------------------------------------------------

NOV_WEEK = {"starts_on": "2026-11-02", "ends_on": "2026-11-08"}


def _person(uid: str, **extra) -> dict:
    return {
        "id": uid,
        "display_name": uid,
        "role": "user",
        "is_active": True,
        "account_type": "staff",
        "location_id": None,
        **extra,
    }


class FakeDb:
    def __init__(self) -> None:
        self.people = [_person("u1"), _person("u2"), _person("u3", location_id="loc-blr")]
        self.projects = [
            {"id": "p-acme", "name": "Acme", "status": "confirmed", "is_archived": False},
            {"id": "p-flux", "name": "Flux", "status": "tentative", "is_archived": False},
            {"id": "p-old", "name": "Old", "status": "confirmed", "is_archived": True},
        ]
        self.allocations = [
            {"id": "a1", "user_id": "u1", "project_id": "p-acme", "percent": "50", **NOV_WEEK},
            {"id": "a2", "user_id": "u2", "project_id": "p-flux", "percent": "50", **NOV_WEEK},
            {"id": "a3", "user_id": "u2", "project_id": "p-acme", "percent": "25", **NOV_WEEK}
            | {"released_at": "2026-10-01T00:00:00+05:30", "release_reason": "Lost"},
            {"id": "a4", "user_id": "u3", "project_id": "p-old", "percent": "50", **NOV_WEEK},
        ]
        # Chennai's holiday on Wednesday; Bengaluru works.
        self.holidays = [{"date": "2026-11-04", "name": "Local", "location_id": "loc-chennai"}]
        self.bookings = [
            {
                "user_id": "u1",
                "date": "2026-11-03",
                "duration": "1",
                "category": "casual",
                "status": "approved",
            }
        ]
        self.cost_periods = [
            {"user_id": "u3", "annual_ctc": "600000", "starts_on": "2026-01-01"}
            | {"hours_per_week": "20"}
        ]

    def list_profiles(self, *, active_only=False, include_crm_only=False):
        return [
            dict(p)
            for p in self.people
            if (not active_only or p["is_active"])
            and (include_crm_only or p["account_type"] == "staff")
        ]

    def list_projects(self, *, include_archived=False):
        return [dict(p) for p in self.projects if include_archived or not p["is_archived"]]

    def list_allocations(self, *, user_ids=None, project_id=None):
        return [dict(a) for a in self.allocations if user_ids is None or a["user_id"] in user_ids]

    def list_counting_allocations(self, *, user_ids=None, project_id=None):
        archived = {p["id"] for p in self.projects if p["is_archived"]}
        return [
            a
            for a in self.list_allocations(user_ids=user_ids, project_id=project_id)
            if not a.get("released_at") and a["project_id"] not in archived
        ]

    def list_holidays(self, start=None, end=None):
        return list(self.holidays)

    def default_location_id(self):
        return "loc-chennai"

    def list_bookings(self, *, user_ids=None, **_):
        return [b for b in self.bookings if user_ids is None or b["user_id"] in user_ids]

    def list_cost_periods(self, user_id=None):
        return list(self.cost_periods)


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    fake = FakeDb()
    for name, fn in inspect.getmembers(db, inspect.isfunction):
        if fn.__module__ == db.__name__ and not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(fake, name, None) or (lambda *a, **k: []))
    settings_store.invalidate()
    yield fake
    settings_store.invalidate()


class TestTheService:
    """u1 and u2 in Chennai (Wednesday off), full time; u3 in Bengaluru on 20
    hours a week; u1 on casual leave Tuesday."""

    def test_supply_per_person_less_their_holidays_and_leave(self, fake):
        (w,) = service.weekly(MON, SUN)
        # u1: 5 days - Wed holiday - Tue leave = 24h; u2: 32h; u3: 5 x 4h = 20h.
        assert w["supply_hours"] == "76.00"
        assert w["contributors"] == 3

    def test_demand_leaves_out_released_and_archived(self, fake):
        (w,) = service.weekly(MON, SUN)
        # u1 50% of 24h is confirmed; u2 50% of 32h tentative. The released
        # 25% (u2) and the archived 50% (u3) count nowhere.
        assert (w["confirmed_hours"], w["tentative_hours"]) == ("12.00", "16.00")

    def test_un_releasing_counts_it_again(self, fake):
        fake.allocations[2].pop("released_at")
        fake.allocations[2].pop("release_reason")
        (w,) = service.weekly(MON, SUN)
        assert w["confirmed_hours"] == "20.00"

    def test_a_crm_only_or_inactive_account_supplies_nothing(self, fake):
        fake.people.append(_person("u-crm", account_type="crm_only"))
        fake.people[0]["is_active"] = False
        (w,) = service.weekly(MON, SUN)
        assert (w["supply_hours"], w["contributors"]) == ("52.00", 2)


class TestTheForecastWeeks:
    def test_weeks_come_from_the_capacity_service(self, fake):
        out = analytics.forecast(MON, SUN)
        assert out["weeks"] == [
            {
                "week_start": "2026-11-02",
                "supply_hours": "76.00",
                "confirmed_hours": "12.00",
                "tentative_hours": "16.00",
            }
        ]

    def test_weeks_are_there_when_every_allocation_is_released(self, fake):
        for a in fake.allocations:
            a.update(released_at="2026-10-01T00:00:00+05:30", release_reason="Lost")
        out = analytics.forecast(MON, SUN)
        assert out["projects"] == []
        assert out["weeks"] == [
            {
                "week_start": "2026-11-02",
                "supply_hours": "76.00",
                "confirmed_hours": "0.00",
                "tentative_hours": "0.00",
            }
        ]
