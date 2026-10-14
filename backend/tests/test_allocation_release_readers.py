"""Released allocations and archived projects stop counting — spec 007
FR-CRM-CHG-03, -04; AC "forecast, resources, bench, hiring signal, planned
cost and the P&L pipeline no longer count them" (data half); D5.

Every allocation reader of FR-CRM-CHG-03 runs here against one world:

- Asha (u1) 80% on Acme, and Bala (u2) 60% on Acme — the allocations that count;
- Bala 50% on Beta, **released** (a lost deal), and
- Bala 50% on Old, a confirmed project since **archived**.

Each test computes the reader's figure and checks that

1. it equals the figure computed with the released and archived rows deleted
   outright, so they add nothing;
2. un-releasing the row changes the figure (the reader can see the row, so
   step 1 is not passing by accident) and releasing it again restores the
   first figure exactly; un-releasing once more gives the un-released figure
   exactly (D5: a reopen restores what was released);
3. un-archiving Old changes the figure too.

A reader that went back to `list_allocations` would count both rows and fail
step 1: `World.list_allocations` returns every row, as the real one does.

Every other function in `app.services.supabase` answers empty, so nothing here
touches a database.
"""

from __future__ import annotations

import copy
import inspect
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import (
    analytics,
    dashboard,
    digest,
    health,
    home,
    settings_store,
    timesheets,
    utilisation,
)
from app.services import pnl as pnl_service
from app.services import supabase as db

TODAY = date(2026, 10, 5)  # a Monday
SPAN = {"starts_on": "2026-10-05", "ends_on": "2026-11-27"}
PHASE = {
    "phase": "delivery",
    "starts_on": "2026-10-01",
    "ends_on": "2026-11-30",
    "budget_hours": None,
}


def _person(uid: str, name: str, role: str = "user") -> dict:
    return {
        "id": uid,
        "email": f"{uid}@example.com",
        "display_name": name,
        "role": role,
        "lead_id": None,
        "is_active": True,
        "logs_time": True,
        "account_type": "staff",
        "location_id": None,
    }


def _project(pid: str, name: str, revenue: str, **extra) -> dict:
    return {
        "id": pid,
        "name": name,
        "client": name,
        "revenue": revenue,
        "is_archived": False,
        "category": "client",
        "status": "confirmed",
        "probability": None,
        "lead_id": None,
        **extra,
    }


def _alloc(aid: str, uid: str, pid: str, percent: str, **extra) -> dict:
    return {"id": aid, "user_id": uid, "project_id": pid, "percent": percent, **SPAN, **extra}


RELEASED = {"released_at": "2026-10-01T09:00:00+05:30", "release_reason": "Deal lost"}


class World:
    def __init__(self) -> None:
        self.people = [
            _person("u1", "Asha"),
            _person("u2", "Bala"),
            _person("u-boss", "Chitra", role="admin"),
        ]
        self.projects = [
            _project("p-acme", "Acme", "210000"),
            _project("p-beta", "Beta", "90000"),
            _project("p-old", "Old", "50000", is_archived=True),
        ]
        self.phases = [
            {"id": f"ph-{p['id']}", "project_id": p["id"], **PHASE} for p in self.projects
        ]
        self.allocations = [
            _alloc("a-asha", "u1", "p-acme", "80"),
            _alloc("a-bala", "u2", "p-acme", "60"),
            _alloc("a-rel", "u2", "p-beta", "50", **RELEASED),
            _alloc("a-old", "u2", "p-old", "50"),
        ]

    # -- the reads the readers make ---------------------------------------

    def list_profiles(self, *, active_only=False, include_crm_only=False):
        return [dict(p) for p in self.people if not active_only or p["is_active"]]

    def get_profile(self, user_id):
        return next((dict(p) for p in self.people if p["id"] == user_id), None)

    def list_projects(self, *, include_archived=False):
        return [dict(p) for p in self.projects if include_archived or not p["is_archived"]]

    def get_project(self, project_id):
        return next((dict(p) for p in self.projects if p["id"] == project_id), None)

    def list_phases(self, project_id=None):
        return [dict(p) for p in self.phases if project_id in (None, p["project_id"])]

    def list_allocations(self, *, user_ids=None, project_id=None):
        return [
            dict(a)
            for a in self.allocations
            if (user_ids is None or a["user_id"] in user_ids)
            and project_id in (None, a["project_id"])
        ]

    def list_counting_allocations(self, *, user_ids=None, project_id=None):
        archived = {p["id"] for p in self.projects if p["is_archived"]}
        return [
            a
            for a in self.list_allocations(user_ids=user_ids, project_id=project_id)
            if not a.get("released_at") and a["project_id"] not in archived
        ]

    def list_cost_periods(self, user_id=None):
        return [
            {"id": f"c-{p['id']}", "user_id": p["id"], "annual_ctc": "1200000"}
            | {"starts_on": "2026-01-01", "ends_on": None}
            for p in self.people
            if user_id in (None, p["id"])
        ]

    # -- the world's levers -------------------------------------------------

    def release(self, aid: str) -> None:
        next(a for a in self.allocations if a["id"] == aid).update(RELEASED)

    def unrelease(self, aid: str) -> None:
        row = next(a for a in self.allocations if a["id"] == aid)
        row.pop("released_at", None)
        row.pop("release_reason", None)


def _empty(name: str):
    if name.startswith(("list_", "count_")):
        return lambda *a, **k: []
    if name.startswith("has_"):
        return lambda *a, **k: False
    if name.startswith("get_") or name in ("default_location_id", "find_booking_on"):
        return lambda *a, **k: None
    return lambda *a, **k: {}


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()
    for name, fn in inspect.getmembers(db, inspect.isfunction):
        if fn.__module__ == db.__name__ and not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(w, name, None) or _empty(name))
    for module in (analytics, dashboard, digest, health, home, pnl_service, timesheets):
        monkeypatch.setattr(module, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(utilisation, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(pnl_service, "_currency", lambda: "INR")
    settings_store.invalidate()
    yield w
    settings_store.invalidate()


def _check(world: World, figure) -> None:
    counted = copy.deepcopy(figure())

    everything = world.allocations
    world.allocations = [a for a in everything if a["id"] not in ("a-rel", "a-old")]
    assert figure() == counted, "a released or archived allocation was counted"
    world.allocations = everything

    world.unrelease("a-rel")
    unreleased = copy.deepcopy(figure())
    assert unreleased != counted, "the reader does not see the allocation at all"
    world.release("a-rel")
    assert figure() == counted
    world.unrelease("a-rel")
    assert figure() == unreleased
    world.release("a-rel")

    old = next(p for p in world.projects if p["id"] == "p-old")
    old["is_archived"] = False
    assert figure() != counted, "the reader does not see the archived project's allocation"
    old["is_archived"] = True
    assert figure() == counted


class TestEveryReaderIgnoresReleasedAndArchived:
    def test_forecast(self, world):
        _check(world, lambda: analytics.forecast(TODAY, TODAY + timedelta(days=13)))

    def test_category_planned_hours(self, world):
        _check(world, lambda: analytics.category_effort("2026-10", "2026-11"))

    def test_resources_timeline(self, world):
        _check(world, lambda: analytics.resources_timeline(TODAY, TODAY + timedelta(days=27)))

    def test_bench(self, world):
        _check(world, lambda: utilisation.bench(4))

    def test_hiring_signal(self, world):
        _check(world, lambda: utilisation.hiring(2, Decimal("1200000"), "2026-10"))

    def test_monthly_profit_planned_cost(self, world):
        # November is planned (after TODAY): its cost comes from allocations.
        _check(world, lambda: pnl_service.monthly("2026-11", "2026-11"))

    def test_allocation_timeline(self, world):
        _check(world, lambda: pnl_service.timeline(TODAY, TODAY + timedelta(days=27)))

    def test_project_health_planned_margin(self, world):
        def figure():
            return [
                health.project_health(pid)["dimensions"] for pid in ("p-beta", "p-old", "p-acme")
            ]

        _check(world, figure)

    def test_ceo_dashboard(self, world):
        # Bala's counting work runs out inside the dashboard's 30 days, so he
        # is "ending" unless a released or archived row is taken as follow-on.
        next(a for a in world.allocations if a["id"] == "a-bala")["ends_on"] = "2026-10-20"
        _check(world, lambda: dashboard.summary(True))

    def test_over_allocation_nudge(self, world, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(digest, "_send", lambda person, subject, body: sent.append(body) or 1)

        def figure():
            sent.clear()
            return digest.over_allocation_next_week(TODAY), list(sent)

        _check(world, figure)

    def test_leadership_digest(self, world, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(digest, "_send", lambda person, subject, body: sent.append(body) or 1)

        def figure():
            sent.clear()
            digest.weekly_digest(TODAY)
            return list(sent)

        _check(world, figure)

    def test_home_for_managers(self, world):
        _check(world, lambda: home._manager(TODAY))


class TestTheTimePage:
    """The Time page offers today's allocated projects. A released one is not
    offered; un-released, it is again. An archived project was never offered
    (FR-PROJ-04)."""

    def _offered(self) -> list[str]:
        return [p["id"] for p in timesheets.day_for("u2", TODAY)["projects"]]

    def test_a_released_allocation_is_not_offered(self, world, monkeypatch):
        monkeypatch.setattr(timesheets, "_grace_days", lambda: 7)
        monkeypatch.setattr(timesheets, "_max_hours", lambda: Decimal("16"))
        assert self._offered() == ["p-acme"]
        world.unrelease("a-rel")
        assert self._offered() == ["p-acme", "p-beta"]
        world.release("a-rel")
        assert self._offered() == ["p-acme"]
