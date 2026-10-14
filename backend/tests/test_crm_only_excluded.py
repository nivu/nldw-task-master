"""CRM-only accounts are left out of every TVA people population — spec 007
FR-CRM-CHG-02; AC "appears in no TVA people list, report, reminder or job".

`db.list_profiles` leaves CRM-only accounts out unless a caller passes
`include_crm_only=True`, so every existing TVA population is covered by its
default (plan §5.7). Each test below runs one population against a world
holding a CRM-only seller who would otherwise qualify (active, no CTC, no
timesheet), and checks two things: every profile read took the default, and
the seller is nowhere in the answer, nor among the people messaged.

The name-map callers (plan §5.7) keep the default by decision: every id they
resolve is a lead, an allocated person, a time-logger or a TVA admin, none of
whom can be CRM-only. A test pins each one to that decision.

Every other function in `app.services.supabase` answers empty, so nothing
here touches a database.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.api import admin as admin_api
from app.api import ops as ops_api
from app.api import team as team_api
from app.api import timesheet as timesheet_api
from app.api.deps import CurrentUser
from app.domain import timesheets as rules
from app.services import (
    analytics,
    bookings,
    checklists,
    confirmations,
    dashboard,
    digest,
    feeds,
    holidays,
    pnl,
    settings_store,
    statements,
    utilisation,
)
from app.services import supabase as db
from app.tasks import notifications

SELLER = "u-crm"
TODAY = date(2026, 10, 14)  # a Wednesday
WEEK = {"starts_on": "2026-10-12", "ends_on": "2026-10-18"}


def _person(uid: str, role: str = "user", **extra) -> dict:
    return {
        "id": uid,
        "email": f"{uid}@example.com",
        "display_name": uid,
        "role": role,
        "lead_id": None,
        "is_active": True,
        "logs_time": True,
        "account_type": "staff",
        "location_id": None,
        **extra,
    }


PEOPLE = [
    _person("u-admin", "admin"),
    _person("u-manager", "manager"),
    _person("u-lead", "lead"),
    _person("u-staff", lead_id="u-lead"),
    _person(SELLER, logs_time=False, account_type="crm_only"),
]

PROJECT = {
    "id": "p1",
    "name": "Atlas",
    "client": "Acme",
    "revenue": "100000",
    "is_archived": False,
    "category": "client",
    "status": "confirmed",
    "probability": None,
    "lead_id": "u-lead",
}


class World:
    """Profile reads, recorded. Everything else in the module answers empty."""

    def __init__(self) -> None:
        self.people = [dict(p) for p in PEOPLE]
        self.calls: list[dict] = []

    def list_profiles(self, *, active_only=False, include_crm_only=False):
        self.calls.append({"include_crm_only": include_crm_only})
        return [
            dict(p)
            for p in self.people
            if (not active_only or p["is_active"])
            and (include_crm_only or p["account_type"] == "staff")
        ]

    def list_reports(self, lead_id, *, active_only=True):
        self.calls.append({"include_crm_only": False, "reports_of": lead_id})
        return [
            dict(p) for p in self.people if p["lead_id"] == lead_id and p["account_type"] == "staff"
        ]

    def get_profile(self, user_id):
        return next((dict(p) for p in self.people if p["id"] == user_id), None)

    def get_project(self, project_id):
        return dict(PROJECT) if project_id == PROJECT["id"] else None

    def list_projects(self, *, include_archived=False):
        return [dict(PROJECT)]

    def list_allocations(self, *, user_ids=None, project_id=None):
        # Staff only: 025 refuses an allocation for a CRM-only account.
        return [
            {"id": f"a{n}", "project_id": "p1", "user_id": "u-staff", "percent": "75", **WEEK}
            for n in (1, 2)
            if user_ids is None or "u-staff" in user_ids
        ]

    def list_counting_allocations(self, *, user_ids=None, project_id=None):
        # Nothing here is released or archived, so every allocation counts.
        return self.list_allocations(user_ids=user_ids, project_id=project_id)

    def list_bookings(self, *, user_ids=None, **_):
        return [
            {
                "id": f"b-{uid}",
                "user_id": uid,
                "date": TODAY.isoformat(),
                "category": "casual",
                "duration": "1",
                "status": "approved",
                "reason": None,
            }
            for uid in ("u-staff", SELLER)
            if user_ids is None or uid in user_ids
        ]


def _empty(name: str):
    if name.startswith(("list_", "count_")):
        return lambda *a, **k: []
    if name.startswith("has_"):
        return lambda *a, **k: False
    if name.startswith("get_") or name == "default_location_id":
        return lambda *a, **k: None
    return lambda *a, **k: {}


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()
    for name, fn in inspect.getmembers(db, inspect.isfunction):
        if fn.__module__ == db.__name__ and not name.startswith("_"):
            monkeypatch.setattr(db, name, _empty(name))
    for name in (
        "list_profiles",
        "list_reports",
        "get_profile",
        "get_project",
        "list_projects",
        "list_allocations",
        "list_counting_allocations",
        "list_bookings",
    ):
        monkeypatch.setattr(db, name, getattr(w, name))
    settings_store.invalidate()
    yield w
    settings_store.invalidate()


@pytest.fixture
def sent(monkeypatch) -> list[str]:
    """Who a digest or nudge would have messaged."""
    out: list[str] = []

    def record(person, subject, body):
        out.append(person["id"])
        return ["slack"]

    monkeypatch.setattr(digest, "_send", record)
    return out


def _excluded(world: World, result) -> None:
    assert world.calls, "the population was not read from list_profiles / list_reports"
    assert all(not c["include_crm_only"] for c in world.calls)
    assert SELLER not in repr(result)


def _user(uid: str) -> CurrentUser:
    return CurrentUser(next(p for p in PEOPLE if p["id"] == uid))


# ---------------------------------------------------------------------------
# The listing itself
# ---------------------------------------------------------------------------


class _Query:
    def __init__(self, log: list) -> None:
        self.log = log

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append((name, args))
            return self

        return call

    def execute(self):
        return type("R", (), {"data": []})()


class TestTheListing:
    @pytest.fixture
    def log(self, monkeypatch) -> list:
        log: list = []
        monkeypatch.setattr(db, "supabase", type("C", (), {"table": lambda s, t: _Query(log)})())
        return log

    def test_profiles_leave_crm_only_out_by_default(self, log):
        db.list_profiles()
        assert ("eq", ("account_type", "staff")) in log

    def test_profiles_include_crm_only_when_asked(self, log):
        db.list_profiles(include_crm_only=True)
        assert all(args[:1] != ("account_type",) for _, args in log)

    def test_reports_are_staff_only(self, log):
        db.list_reports("u-lead")
        assert ("eq", ("account_type", "staff")) in log


# ---------------------------------------------------------------------------
# Populations — tasks S1a "TVA people populations to verify"
# ---------------------------------------------------------------------------


class TestPopulations:
    def test_team_day(self, world):
        _excluded(world, team_api._population(_user("u-admin")))

    def test_timesheet_population(self, world):
        _excluded(world, timesheet_api._population(_user("u-manager")))

    def test_allocatable_picker(self, world):
        _excluded(world, timesheet_api.people_for_allocation(_user("u-manager")))

    def test_utilisation(self, world):
        ids = [p["id"] for p in PEOPLE]
        _excluded(world, utilisation.monthly(ids, "2026-09", "2026-10"))

    def test_bench(self, world):
        _excluded(world, utilisation.bench(weeks=4))

    def test_hiring_supply(self, world):
        _excluded(world, utilisation.hiring(3, Decimal("1200000"), "2026-10"))

    def test_coverage(self, world):
        # Coverage reads the ids its caller passes; every caller passes a
        # population (here the analytics one), which is where the seller goes.
        ids = timesheet_api._population(_user("u-manager"))
        _excluded(world, analytics.coverage_windows(ids, [(date(2026, 10, 5), TODAY)]))

    def test_people_financials_and_ctc_missing(self, world):
        result = analytics.people_financials()
        _excluded(world, result)
        assert result["unrated"], "staff with no CTC are listed, so the seller would be too"

    def test_resources(self, world):
        _excluded(world, analytics.resources_timeline(date(2026, 10, 1), date(2026, 12, 31)))

    def test_pnl_monthly(self, world):
        _excluded(world, pnl.monthly("2026-09", "2026-10"))

    def test_pnl_timeline(self, world):
        _excluded(world, pnl.timeline(date(2026, 10, 1), date(2026, 12, 31)))

    def test_ceo_headcount(self, world):
        result = dashboard.summary(True)
        _excluded(world, result)

    def test_nothing_logged_nudge(self, world, sent):
        _excluded(world, digest.nothing_logged_today(TODAY))
        assert SELLER not in sent

    def test_weekly_gaps(self, world, sent):
        _excluded(world, digest.weekly_gaps(TODAY))
        assert SELLER not in sent

    def test_over_allocation_next_week(self, world, sent):
        _excluded(world, digest.over_allocation_next_week(TODAY))
        assert sent and SELLER not in sent

    def test_morning_who_is_out(self, world, monkeypatch):
        posted: list[str] = []
        real_get = settings_store.get
        monkeypatch.setattr(
            digest.settings_store,
            "get",
            lambda key, default=None: (
                "#out" if key == "slack_out_channel" else real_get(key, default)
            ),
        )
        monkeypatch.setattr(
            digest.slack_adapter, "post_to_channel", lambda ch, text: posted.append(text) or True
        )
        _excluded(world, digest.morning_out_post(TODAY))
        assert posted and "u-staff" in posted[0] and SELLER not in posted[0]

    def test_leadership_digest_and_its_coverage(self, world, sent):
        _excluded(world, digest.weekly_digest(TODAY))
        assert SELLER not in sent

    def test_sign_off(self, world):
        _excluded(world, confirmations.auto_confirm_closed(TODAY))

    def test_approvers(self, world):
        _excluded(world, notifications._approvers_for(_person("u-new")))

    def test_feed_population(self, world):
        _excluded(world, feeds.ics_for(_person("u-manager", "manager")))

    def test_holiday_location_lookup(self, world):
        # As coverage: the ids come from the caller's population.
        ids = timesheet_api._population(_user("u-manager"))
        _excluded(world, holidays.holidays_by_person(ids, date(2026, 10, 1), TODAY))

    def test_holiday_release_location_lookup(self, world):
        result = bookings.release_for_holiday(
            day=TODAY, holiday_name="Diwali", actor_id="u-admin", location_id="loc-1"
        )
        _excluded(world, result)

    def test_org_consumption(self, world, monkeypatch):
        monkeypatch.setattr(admin_api.balances, "summary_for", lambda uid, period: [])
        _excluded(world, admin_api.org_consumption(_user("u-admin"), "2026-10"))


class TestFeed:
    def test_never_resolves_a_crm_only_key(self, world):
        seller = next(p for p in PEOPLE if p["id"] == SELLER)
        assert feeds.resolve(feeds.key_for(seller)) is None

    def test_refuses_crm_only_even_if_the_listing_let_one_through(self, world, monkeypatch):
        seller = next(p for p in PEOPLE if p["id"] == SELLER)
        monkeypatch.setattr(db, "list_profiles", lambda **_: [dict(seller)])
        assert feeds.resolve(feeds.key_for(seller)) is None

    def test_still_resolves_staff(self, world):
        staff = next(p for p in PEOPLE if p["id"] == "u-staff")
        assert feeds.resolve(feeds.key_for(staff))["id"] == "u-staff"


# ---------------------------------------------------------------------------
# Name-map callers — each kept on the default by decision (plan §5.7)
# ---------------------------------------------------------------------------


class TestNameMapsKeepTheDefault:
    def test_project_list_lead_names(self, world):
        _excluded(world, admin_api.list_projects(_user("u-manager")))

    def test_backfills(self, world):
        _excluded(world, admin_api.list_backfills(_user("u-admin")))

    def test_allocations(self, world):
        _excluded(world, admin_api.list_allocations(_user("u-manager")))

    def test_compoff_names(self, world):
        _excluded(world, ops_api._names())

    def test_analytics_project_lead_names(self, world):
        _excluded(world, timesheet_api.project_list(_user("u-manager")))

    def test_project_effort(self, world):
        _excluded(world, analytics.project_effort("p1"))

    def test_forecast(self, world):
        _excluded(world, analytics.forecast(date(2026, 10, 1), date(2026, 10, 31)))

    def test_current_work(self, world):
        _excluded(world, analytics.current_work(["u-staff"]))

    def test_project_financials(self, world):
        _excluded(world, analytics.project_financials("p1"))

    def test_effort_statement(self, world):
        _excluded(world, statements.statement("p1", "2026-10"))

    def test_checklists(self, world):
        _excluded(world, checklists.listing())


# ---------------------------------------------------------------------------
# Refusals with the spec's sentences
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_lead_refusal_names_crm_only(self):
        seller = next(p for p in PEOPLE if p["id"] == SELLER)
        assert rules.project_lead_refusal(seller) == "A CRM-only account cannot lead a project."

    def test_lead_refusal_still_allows_staff(self):
        assert rules.project_lead_refusal(_person("u-lead", "lead")) is None

    def test_allocation_refusal_names_crm_only(self):
        seller = next(p for p in PEOPLE if p["id"] == SELLER)
        assert rules.allocation_person_refusal(seller) == "A CRM-only account cannot be allocated."

    def test_allocation_refusal_allows_staff(self):
        assert rules.allocation_person_refusal(_person("u-staff")) is None
