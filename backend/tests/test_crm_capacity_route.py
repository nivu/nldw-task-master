"""The CRM capacity check — spec 007 FR-CRM-CAP-01 to -03; contracts §2.8;
Q-39.

Runs against `tests/crm_fake.py` for the workspaces and a small fake of the
TVA reads the capacity service makes. The staff profiles there (Owner,
Deepika, Devansh, Vinita) and an Academy-only admin are the supply; Tamil is
CRM-only and supplies nothing.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta

import pytest
from crm_fake import (
    MEMBER,
    OUTSIDER,
    OWNER,
    PEOPLE,
    SELLER,
    VIEWER,
    FakeCrmStore,
    as_,
    two_workspaces,
)
from crm_fake import client as crm_client

from app.api.crm import capacity as capacity_route
from app.services import analytics, settings_store
from app.services import supabase as db

URL = "/api/v1/crm/capacity"
TODAY = date(2026, 11, 4)  # a Wednesday
MONDAY = date(2026, 11, 2)


class TvaReads:
    def __init__(self) -> None:
        self.projects = [
            {"id": "p-acme", "name": "Acme", "status": "confirmed", "is_archived": False},
            {"id": "p-flux", "name": "Flux", "status": "tentative", "is_archived": False},
        ]
        span = {"starts_on": "2026-11-02", "ends_on": "2026-12-31"}
        self.allocations = [
            {"id": "a1", "user_id": OWNER["id"], "project_id": "p-acme", "percent": "50", **span},
            {"id": "a2", "user_id": MEMBER["id"], "project_id": "p-flux", "percent": "50", **span},
        ]

    def list_projects(self, *, include_archived=False):
        return [dict(p) for p in self.projects if include_archived or not p["is_archived"]]

    def list_counting_allocations(self, *, user_ids=None, project_id=None):
        return [dict(a) for a in self.allocations]


@pytest.fixture
def crm(monkeypatch):
    tva = TvaReads()
    for name, fn in inspect.getmembers(db, inspect.isfunction):
        if fn.__module__ == db.__name__ and not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(tva, name, None) or _empty(name))
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    monkeypatch.setattr(capacity_route, "today_in_company_tz", lambda: TODAY)
    settings_store.invalidate()
    people = {k: dict(v) for k, v in PEOPLE.items()}
    people["u-academy"] = {**MEMBER, "id": "u-academy", "email": "a@nunnarilabs.com"}
    fake.add_member(academy, "u-academy", "admin")
    # `client` copies the profiles into `fake.profiles`, which every request reads.
    yield crm_client(monkeypatch, people, fake=fake), fake.profiles, tva
    settings_store.invalidate()


def _empty(name: str):
    if name.startswith("get_") or name == "default_location_id":
        return lambda *a, **k: None
    return lambda *a, **k: []


class TestWhoSeesIt:
    @pytest.mark.parametrize("who", [OWNER["id"], MEMBER["id"]])
    def test_a_labs_admin_and_a_staff_member_see_it(self, crm, who):
        http, *_ = crm
        r = http.get(f"{URL}?workspace=labs", headers=as_(who))
        assert r.status_code == 200
        assert len(r.json()["weeks"]) == 13

    def test_a_viewer_is_refused(self, crm):
        http, *_ = crm
        assert http.get(f"{URL}?workspace=labs", headers=as_(VIEWER["id"])).status_code == 403

    def test_a_crm_only_member_is_refused(self, crm):
        http, *_ = crm
        assert http.get(f"{URL}?workspace=labs", headers=as_(SELLER["id"])).status_code == 403

    def test_academy_has_no_capacity_check(self, crm):
        http, *_ = crm
        assert http.get(f"{URL}?workspace=academy", headers=as_(OWNER["id"])).status_code == 404

    def test_someone_outside_labs_is_not_told_it_exists(self, crm):
        http, *_ = crm
        for who in ("u-academy", OUTSIDER["id"]):
            r = http.get(f"{URL}?workspace=labs", headers=as_(who))
            assert (r.status_code, r.json()["detail"]) == (404, "No such workspace.")


class TestTheFigures:
    def test_totals_only_never_a_person(self, crm):
        http, *_ = crm
        r = http.get(f"{URL}?workspace=labs&weeks=2", headers=as_(OWNER["id"]))
        for week in r.json()["weeks"]:
            assert set(week) == {"week_start", "tentative_hours", "free_hours"}
        for person in PEOPLE.values():
            assert person["id"] not in r.text
            assert person["display_name"] not in r.text

    def test_free_is_supply_less_confirmed(self, crm):
        http, *_ = crm
        first = http.get(f"{URL}?workspace=labs&weeks=1", headers=as_(OWNER["id"])).json()
        # From Wednesday: five staff x 3 days x 8h = 120h supply; the Owner's
        # 50% of 24h is confirmed, Deepika's 50% tentative.
        assert first["weeks"] == [
            {"week_start": "2026-11-02", "tentative_hours": "12.00", "free_hours": "108.00"}
        ]

    def test_a_week_with_fewer_than_three_contributors_is_hidden(self, crm):
        http, people, _ = crm
        for uid in (VIEWER["id"], OUTSIDER["id"], "u-academy"):
            people[uid]["left_on"] = "2026-11-08"
        weeks = http.get(f"{URL}?workspace=labs&weeks=2", headers=as_(OWNER["id"])).json()["weeks"]
        assert "free_hours" in weeks[0]
        assert weeks[1] == {"week_start": "2026-11-09", "hidden": "fewer than 3 people"}

    def test_the_figures_are_the_forecasts(self, crm):
        """FR-CRM-CAP-03 — the same service, so the same numbers."""
        http, *_ = crm
        weeks = http.get(f"{URL}?workspace=labs", headers=as_(OWNER["id"])).json()["weeks"]
        forecast = analytics.forecast(TODAY, MONDAY + timedelta(days=7 * 13 - 1))["weeks"]
        assert [w["week_start"] for w in weeks] == [w["week_start"] for w in forecast]
        for mine, theirs in zip(weeks, forecast, strict=True):
            assert mine["tentative_hours"] == theirs["tentative_hours"]
            free = float(theirs["supply_hours"]) - float(theirs["confirmed_hours"])
            assert float(mine["free_hours"]) == pytest.approx(free)

    @pytest.mark.parametrize("weeks", [0, 14])
    def test_at_most_thirteen_weeks(self, crm, weeks):
        http, *_ = crm
        r = http.get(f"{URL}?workspace=labs&weeks={weeks}", headers=as_(OWNER["id"]))
        assert r.status_code == 422
