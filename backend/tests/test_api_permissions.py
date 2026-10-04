"""Route-level permissions — spec 003 FR-ROLE-02/07/08, spec 002 FR-PROJ-04a/07/08,
FR-ALLOC-06, spec 001 FR-HOL-07/08.

The domain tests check each rule in isolation. These check that the routes
actually apply them: the backend holds the service-role key, so a guard
missing from a route is a guard missing altogether (see app.api.deps).

Auth is replaced by a dependency override and the Supabase module by an
in-memory fake holding only what the project, allocation and holiday routes
touch.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, current_user
from app.main import app
from app.services import bookings as booking_service
from app.services import supabase as db

PEOPLE = {
    "u-admin": {"role": "admin", "lead_id": None},
    "u-manager": {"role": "manager", "lead_id": None},
    "u-lead": {"role": "lead", "lead_id": None},
    "u-other-lead": {"role": "lead", "lead_id": None},
    "u-mine": {"role": "user", "lead_id": "u-lead"},
    "u-theirs": {"role": "user", "lead_id": "u-other-lead"},
    "u-gone": {"role": "lead", "lead_id": None, "is_active": False},
}


class FakeDb:
    """Just enough of app.services.supabase for /admin/projects, /admin/allocations,
    /admin/holidays, /admin/milestones and /analytics/invoices."""

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
        self.projects = {
            "p-live": {
                "id": "p-live",
                "name": "Live",
                "client": "Acme",
                "revenue": "120000",
                "is_archived": False,
                "category": "client",
            },
            "p-archived": {
                "id": "p-archived",
                "name": "Old",
                "client": None,
                "revenue": None,
                "is_archived": True,
                "category": "internal",
            },
        }
        self.allocations = {
            "a-mine": self._alloc("a-mine", "u-mine", "p-live"),
            "a-theirs": self._alloc("a-theirs", "u-theirs", "p-live"),
            "a-archived": self._alloc("a-archived", "u-mine", "p-archived"),
        }
        self.cost_periods: dict[str, dict] = {}
        self.holidays = [
            {"id": "h-diwali", "date": "2026-11-08", "name": "Diwali", "location_id": None}
        ]
        self.milestones = {
            "m-billed": {
                "id": "m-billed",
                "project_id": "p-live",
                "name": "Kickoff",
                "due_on": "2026-09-01",
                "amount": "40000",
                "invoiced_on": "2026-09-01",
                "invoice_number": None,
                "paid_on": None,
            },
            "m-unbilled": {
                "id": "m-unbilled",
                "project_id": "p-live",
                "name": "Delivery",
                "due_on": "2026-12-01",
                "amount": "80000",
                "invoiced_on": None,
                "invoice_number": None,
                "paid_on": None,
            },
        }
        self.audit: list[dict] = []

    @staticmethod
    def _alloc(aid: str, user_id: str, project_id: str) -> dict:
        return {
            "id": aid,
            "project_id": project_id,
            "user_id": user_id,
            "starts_on": "2026-10-01",
            "ends_on": "2026-10-31",
            "percent": "50",
        }

    # Profiles
    # Reads hand back copies, as a real query would: a route holding `existing`
    # must not see it change underneath it when the row is updated.
    def get_profile(self, user_id):
        return dict(self.profiles[user_id]) if user_id in self.profiles else None

    def list_profiles(self, *, active_only=False):
        return list(self.profiles.values())

    def list_reports(self, lead_id, *, active_only=True):
        return [p for p in self.profiles.values() if p["lead_id"] == lead_id]

    def update_profile(self, user_id, data):
        self.profiles[user_id].update(data)
        return dict(self.profiles[user_id])

    # Projects
    def list_projects(self, *, include_archived=False):
        return [p for p in self.projects.values() if include_archived or not p["is_archived"]]

    def get_project(self, project_id):
        return dict(self.projects[project_id]) if project_id in self.projects else None

    def insert_project(self, data):
        row = {"id": f"p-{len(self.projects)}", "is_archived": False, **data}
        self.projects[row["id"]] = row
        return row

    def update_project(self, project_id, data):
        self.projects[project_id].update(data)
        return dict(self.projects[project_id])

    def list_phases(self, project_id=None):
        return []

    # Allocations
    def list_allocations(self, *, user_ids=None, project_id=None):
        return [
            a
            for a in self.allocations.values()
            if (user_ids is None or a["user_id"] in user_ids)
            and (project_id is None or a["project_id"] == project_id)
        ]

    def get_allocation(self, allocation_id):
        row = self.allocations.get(allocation_id)
        return dict(row) if row else None

    def insert_allocation(self, data):
        row = {"id": f"a-{len(self.allocations)}", **data}
        self.allocations[row["id"]] = row
        return row

    def update_allocation(self, allocation_id, data):
        self.allocations[allocation_id].update(data)
        return dict(self.allocations[allocation_id])

    def delete_allocation(self, allocation_id):
        del self.allocations[allocation_id]

    # CTC periods
    def list_cost_periods(self, user_id=None):
        return [
            dict(r)
            for r in self.cost_periods.values()
            if user_id is None or r["user_id"] == user_id
        ]

    def insert_cost_period(self, data):
        row = {"id": f"c-{len(self.cost_periods)}", **data}
        self.cost_periods[row["id"]] = row
        return dict(row)

    def close_cost_period(self, period_id, ends_on):
        self.cost_periods[period_id]["ends_on"] = ends_on

    # Time — only for the /analytics/projects list
    def list_time_entries(self, **_):
        return []

    # Holidays and locations
    def list_holidays(self, start=None, end=None):
        return [
            h
            for h in self.holidays
            if (start is None or h["date"] >= start.isoformat())
            and (end is None or h["date"] <= end.isoformat())
        ]

    def insert_holiday(self, data):
        row = {"id": f"h-{len(self.holidays)}", **data}
        self.holidays.append(row)
        return row

    def list_locations(self):
        return [{"id": "loc-chennai", "name": "Chennai"}]

    # Milestones
    def list_milestones(self, project_id=None):
        return [
            dict(m)
            for m in self.milestones.values()
            if project_id is None or m["project_id"] == project_id
        ]

    def get_milestone(self, milestone_id):
        row = self.milestones.get(milestone_id)
        return dict(row) if row else None

    def update_milestone(self, milestone_id, data):
        self.milestones[milestone_id].update(data)
        return dict(self.milestones[milestone_id])

    # Settings — none stored, so every read takes its default.
    def list_settings(self):
        return []

    # Audit
    def insert_audit(self, entry):
        self.audit.append(entry)


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    fake = FakeDb()
    for name in dir(FakeDb):
        if not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(fake, name))
    return fake


@pytest.fixture
def as_(fake):
    """`as_("u-lead")` returns a client signed in as that person."""

    def sign_in(user_id: str) -> TestClient:
        app.dependency_overrides[current_user] = lambda: CurrentUser(fake.profiles[user_id])
        return TestClient(app)

    yield sign_in
    app.dependency_overrides.pop(current_user, None)


API = "/api/v1/admin"
OCT = {"starts_on": "2026-10-01", "ends_on": "2026-10-31"}


class TestProjects:
    def test_lead_can_create_a_project(self, as_, fake):
        r = as_("u-lead").post(f"{API}/projects", json={"name": "New"})
        assert r.status_code == 201
        assert "revenue" not in r.json()

    def test_lead_cannot_create_a_project_with_revenue(self, as_, fake):
        r = as_("u-lead").post(f"{API}/projects", json={"name": "New", "revenue": "1000"})
        assert r.status_code == 403
        assert len(fake.projects) == 2

    def test_lead_can_update_a_project(self, as_, fake):
        r = as_("u-lead").patch(f"{API}/projects/p-live", json={"is_archived": True})
        assert r.status_code == 200
        assert fake.projects["p-live"]["is_archived"] is True
        assert "revenue" not in r.json()

    @pytest.mark.parametrize("revenue", ["5000", None])
    def test_lead_cannot_set_or_clear_revenue(self, as_, fake, revenue):
        r = as_("u-lead").patch(f"{API}/projects/p-live", json={"revenue": revenue})
        assert r.status_code == 403
        assert fake.projects["p-live"]["revenue"] == "120000"

    def test_lead_project_list_has_no_revenue_key(self, as_, fake):
        r = as_("u-lead").get(f"{API}/projects")
        assert r.status_code == 200
        assert len(r.json()) == 2
        assert all("revenue" not in p for p in r.json())

    @pytest.mark.parametrize("who", ["u-manager", "u-admin"])
    def test_manager_and_admin_see_revenue(self, as_, fake, who):
        projects = {p["id"]: p for p in as_(who).get(f"{API}/projects").json()}
        assert projects["p-live"]["revenue"] == "120000"
        assert projects["p-archived"]["revenue"] is None

    def test_manager_can_set_revenue(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/projects/p-live", json={"revenue": "5000"})
        assert r.status_code == 200
        assert r.json()["revenue"] == "5000"


class TestProjectLead:
    """Spec 002 FR-PROJ-07 — whoever may edit a project may name its lead."""

    @pytest.mark.parametrize("who", ["u-lead", "u-manager", "u-admin"])
    def test_set_on_create(self, as_, fake, who):
        r = as_(who).post(f"{API}/projects", json={"name": "New", "lead_id": "u-other-lead"})
        assert r.status_code == 201
        assert r.json()["lead_id"] == "u-other-lead"
        assert r.json()["lead_name"] == "u-other-lead"
        assert fake.audit[-1]["after"]["lead_id"] == "u-other-lead"

    @pytest.mark.parametrize("who", ["u-lead", "u-manager", "u-admin"])
    def test_set_and_cleared_on_update(self, as_, fake, who):
        r = as_(who).patch(f"{API}/projects/p-live", json={"lead_id": "u-mine"})
        assert r.status_code == 200
        assert r.json()["lead_name"] == "u-mine"
        assert fake.projects["p-live"]["lead_id"] == "u-mine"
        r = as_(who).patch(f"{API}/projects/p-live", json={"lead_id": None})
        assert r.status_code == 200
        assert r.json()["lead_id"] is None
        assert r.json()["lead_name"] is None
        assert fake.audit[-1]["before"] == {"lead_id": "u-mine"}

    @pytest.mark.parametrize("lead_id", ["u-nobody", "u-gone"])
    def test_unknown_or_inactive_lead_is_refused(self, as_, fake, lead_id):
        r = as_("u-manager").post(f"{API}/projects", json={"name": "New", "lead_id": lead_id})
        assert r.status_code == 422
        assert len(fake.projects) == 2
        r = as_("u-manager").patch(f"{API}/projects/p-live", json={"lead_id": lead_id})
        assert r.status_code == 422
        assert "lead_id" not in fake.projects["p-live"]
        assert fake.audit == []

    def test_lists_show_the_lead_name(self, as_, fake):
        fake.projects["p-live"]["lead_id"] = "u-lead"
        projects = {p["id"]: p for p in as_("u-lead").get(f"{API}/projects").json()}
        assert projects["p-live"]["lead_name"] == "u-lead"
        assert projects["p-archived"]["lead_name"] is None
        effort = {p["id"]: p for p in as_("u-lead").get("/api/v1/analytics/projects").json()}
        assert effort["p-live"]["lead_name"] == "u-lead"
        assert effort["p-archived"]["lead_name"] is None

    def test_plain_user_cannot_set_a_lead(self, as_, fake):
        r = as_("u-mine").patch(f"{API}/projects/p-live", json={"lead_id": "u-mine"})
        assert r.status_code == 403
        assert "lead_id" not in fake.projects["p-live"]


class TestPlainUserIsRefused:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("GET", "/projects", None),
            ("POST", "/projects", {"name": "New"}),
            ("PATCH", "/projects/p-live", {"name": "Renamed"}),
            (
                "PUT",
                "/projects/p-live/phases",
                {"phase": "delivery", **OCT},
            ),
            ("GET", "/allocations", None),
            (
                "POST",
                "/allocations",
                {"project_id": "p-live", "user_id": "u-mine", "percent": "50", **OCT},
            ),
            ("PATCH", "/allocations/a-mine", {"percent": "20"}),
            ("DELETE", "/allocations/a-mine", None),
        ],
    )
    def test_403(self, as_, fake, method, path, body):
        r = as_("u-mine").request(method, f"{API}{path}", json=body)
        assert r.status_code == 403
        assert fake.allocations["a-mine"]["percent"] == "50"
        assert fake.projects["p-live"]["name"] == "Live"
        assert fake.audit == []


class TestLeadAllocations:
    def _new(self, user_id: str, project_id: str = "p-live") -> dict:
        return {"project_id": project_id, "user_id": user_id, "percent": "40", **OCT}

    def test_lead_sees_only_their_reports(self, as_, fake):
        r = as_("u-lead").get(f"{API}/allocations")
        assert {a["id"] for a in r.json()} == {"a-mine", "a-archived"}

    def test_lead_can_allocate_their_report(self, as_, fake):
        r = as_("u-lead").post(f"{API}/allocations", json=self._new("u-mine"))
        assert r.status_code == 201
        assert fake.audit[-1]["action"] == "allocation.created"

    def test_lead_cannot_allocate_someone_elses_report(self, as_, fake):
        r = as_("u-lead").post(f"{API}/allocations", json=self._new("u-theirs"))
        assert r.status_code == 403
        assert len(fake.allocations) == 3

    def test_lead_can_edit_their_reports_allocation(self, as_, fake):
        r = as_("u-lead").patch(f"{API}/allocations/a-mine", json={"percent": "80"})
        assert r.status_code == 200
        assert fake.allocations["a-mine"]["percent"] == "80"

    def test_lead_cannot_edit_someone_elses_allocation(self, as_, fake):
        r = as_("u-lead").patch(f"{API}/allocations/a-theirs", json={"percent": "80"})
        assert r.status_code == 403
        assert fake.allocations["a-theirs"]["percent"] == "50"

    def test_lead_can_remove_their_reports_allocation(self, as_, fake):
        r = as_("u-lead").delete(f"{API}/allocations/a-mine")
        assert r.status_code == 200
        assert "a-mine" not in fake.allocations

    def test_lead_cannot_remove_someone_elses_allocation(self, as_, fake):
        r = as_("u-lead").delete(f"{API}/allocations/a-theirs")
        assert r.status_code == 403
        assert "a-theirs" in fake.allocations

    def test_manager_may_allocate_anyone(self, as_, fake):
        r = as_("u-manager").post(f"{API}/allocations", json=self._new("u-theirs"))
        assert r.status_code == 201


class TestArchivedProject:
    def test_refuses_a_new_allocation(self, as_, fake):
        body = {"project_id": "p-archived", "user_id": "u-mine", "percent": "40", **OCT}
        r = as_("u-manager").post(f"{API}/allocations", json=body)
        assert r.status_code == 422
        assert r.json()["detail"] == "Old is archived."
        assert len(fake.allocations) == 3

    def test_refuses_extending_an_allocation(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/allocations/a-archived", json={"ends_on": "2026-11-30"})
        assert r.status_code == 422
        assert fake.allocations["a-archived"]["ends_on"] == "2026-10-31"

    def test_allows_shortening_an_allocation(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/allocations/a-archived", json={"ends_on": "2026-10-15"})
        assert r.status_code == 200
        assert fake.allocations["a-archived"]["ends_on"] == "2026-10-15"


class TestAllocationEditValidation:
    """FR-ALLOC-06 — the end may not precede the start; 0 < percent ≤ 100."""

    def test_end_before_existing_start_is_refused(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/allocations/a-mine", json={"ends_on": "2026-09-30"})
        assert r.status_code == 422
        assert fake.allocations["a-mine"]["ends_on"] == "2026-10-31"

    def test_start_after_existing_end_is_refused(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/allocations/a-mine", json={"starts_on": "2026-11-01"})
        assert r.status_code == 422
        assert fake.allocations["a-mine"]["starts_on"] == "2026-10-01"

    def test_both_dates_reversed_is_refused(self, as_, fake):
        body = {"starts_on": "2026-10-20", "ends_on": "2026-10-10"}
        r = as_("u-manager").patch(f"{API}/allocations/a-mine", json=body)
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "body", [{"starts_on": "not-a-date"}, {"percent": "0"}, {"percent": "101"}]
    )
    def test_malformed_values_are_refused(self, as_, fake, body):
        r = as_("u-manager").patch(f"{API}/allocations/a-mine", json=body)
        assert r.status_code == 422
        assert fake.allocations["a-mine"]["percent"] == "50"

    def test_nothing_to_change_is_refused(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/allocations/a-mine", json={})
        assert r.status_code == 422

    def test_a_valid_edit_is_audited_before_and_after(self, as_, fake):
        r = as_("u-manager").patch(
            f"{API}/allocations/a-mine", json={"starts_on": "2026-10-05", "percent": "25"}
        )
        assert r.status_code == 200
        assert r.json() == {
            "id": "a-mine",
            "starts_on": "2026-10-05",
            "ends_on": "2026-10-31",
            "percent": "25",
        }
        entry = fake.audit[-1]
        assert entry["action"] == "allocation.updated"
        assert entry["before"]["starts_on"] == "2026-10-01"
        assert entry["after"]["percent"] == "25"


class TestContractedHours:
    """Spec 005 FR-CTC-06 — the hours a CTC period pays for. Admin only, like
    every CTC write (FR-CTC-01)."""

    CTC = {"annual_ctc": "600000", "starts_on": "2026-10-01"}

    def test_admin_records_part_time_hours(self, as_, fake):
        r = as_("u-admin").post(
            f"{API}/users/u-mine/ctc", json={**self.CTC, "hours_per_week": "10"}
        )
        assert r.status_code == 201
        assert r.json()["hours_per_week"] == "10.0"
        assert fake.audit[-1]["after"]["hours_per_week"] == "10"

    def test_hours_default_to_full_time(self, as_, fake):
        r = as_("u-admin").post(f"{API}/users/u-mine/ctc", json=self.CTC)
        assert r.status_code == 201
        assert r.json()["hours_per_week"] == "40.0"
        listed = as_("u-admin").get(f"{API}/users/u-mine/ctc").json()
        assert listed["periods"][0]["hours_per_week"] == "40.0"

    @pytest.mark.parametrize("hours", ["0", "-5", "60.5", "10.25"])
    def test_out_of_range_hours_are_refused(self, as_, fake, hours):
        r = as_("u-admin").post(
            f"{API}/users/u-mine/ctc", json={**self.CTC, "hours_per_week": hours}
        )
        assert r.status_code == 422
        assert fake.cost_periods == {}

    @pytest.mark.parametrize("who", ["u-manager", "u-lead", "u-mine"])
    def test_only_an_admin_may_set_them(self, as_, fake, who):
        r = as_(who).post(f"{API}/users/u-mine/ctc", json={**self.CTC, "hours_per_week": "10"})
        assert r.status_code == 403
        assert fake.cost_periods == {}


class TestPeopleDates:
    """Spec 002 FR-ANALYTICS-08 — joining and leaving dates are set by an admin
    only, and a person cannot leave before they joined."""

    @pytest.mark.parametrize("who", ["u-mine", "u-lead", "u-manager"])
    def test_only_an_admin_sets_them(self, as_, fake, who):
        r = as_(who).patch(f"{API}/users/u-mine", json={"joined_on": "2026-10-01"})
        assert r.status_code == 403
        assert "joined_on" not in fake.profiles["u-mine"]
        assert fake.audit == []

    def test_admin_sets_and_clears_them(self, as_, fake):
        body = {"joined_on": "2026-10-01", "left_on": "2026-12-31"}
        r = as_("u-admin").patch(f"{API}/users/u-mine", json=body)
        assert r.status_code == 200
        assert r.json()["joined_on"] == "2026-10-01"
        assert r.json()["left_on"] == "2026-12-31"
        assert fake.audit[-1]["after"] == body

        r = as_("u-admin").patch(f"{API}/users/u-mine", json={"left_on": None})
        assert r.status_code == 200
        assert fake.profiles["u-mine"]["left_on"] is None

    def test_leaving_before_joining_is_refused(self, as_, fake):
        body = {"joined_on": "2026-10-10", "left_on": "2026-10-01"}
        r = as_("u-admin").patch(f"{API}/users/u-mine", json=body)
        assert r.status_code == 422
        assert "joined_on" not in fake.profiles["u-mine"]

    def test_checked_against_the_stored_date(self, as_, fake):
        fake.profiles["u-mine"]["joined_on"] = "2026-10-10"
        r = as_("u-admin").patch(f"{API}/users/u-mine", json={"left_on": "2026-10-01"})
        assert r.status_code == 422
        assert "left_on" not in fake.profiles["u-mine"]


class TestTentativeProject:
    """Spec 002 FR-PROJ-08 — status and probability are not money, so a lead
    who runs projects may set them; a tentative project still takes
    allocations."""

    def test_lead_can_create_a_tentative_project_with_a_probability(self, as_, fake):
        body = {"name": "FluxBooks", "status": "tentative", "probability": 40}
        r = as_("u-lead").post(f"{API}/projects", json=body)
        assert r.status_code == 201
        assert r.json()["status"] == "tentative"
        assert r.json()["probability"] == 40
        assert "revenue" not in r.json()
        assert fake.audit[-1]["after"]["status"] == "tentative"

    def test_a_new_project_is_confirmed_by_default(self, as_, fake):
        r = as_("u-lead").post(f"{API}/projects", json={"name": "New"})
        assert r.json()["status"] == "confirmed"
        assert r.json()["probability"] is None

    def test_lead_can_confirm_a_tentative_project(self, as_, fake):
        fake.projects["p-live"].update(status="tentative", probability=60)
        r = as_("u-lead").patch(f"{API}/projects/p-live", json={"status": "confirmed"})
        assert r.status_code == 200
        assert fake.projects["p-live"]["status"] == "confirmed"
        assert fake.audit[-1]["before"] == {"status": "tentative"}

    @pytest.mark.parametrize("body", [{"status": "maybe"}, {"probability": 101}, {"probability": -1}])
    def test_malformed_values_are_refused(self, as_, fake, body):
        r = as_("u-manager").patch(f"{API}/projects/p-live", json=body)
        assert r.status_code == 422
        assert fake.audit == []

    def test_a_status_cannot_be_cleared(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/projects/p-live", json={"status": None})
        assert r.status_code == 422

    def test_a_tentative_project_takes_allocations(self, as_, fake):
        fake.projects["p-live"]["status"] = "tentative"
        body = {"project_id": "p-live", "user_id": "u-mine", "percent": "40", **OCT}
        r = as_("u-lead").post(f"{API}/allocations", json=body)
        assert r.status_code == 201

    def test_plain_user_cannot_change_status(self, as_, fake):
        r = as_("u-mine").patch(f"{API}/projects/p-live", json={"status": "tentative"})
        assert r.status_code == 403
        assert "status" not in fake.projects["p-live"]


class TestBulkHolidays:
    """FR-HOL-08 — each holiday declared through the single-holiday path."""

    @pytest.fixture
    def released(self, monkeypatch):
        calls: list[dict] = []

        def release(**kwargs):
            calls.append(kwargs)
            return [{"id": f"b-{len(calls)}"}]

        monkeypatch.setattr(booking_service, "release_for_holiday", release)
        return calls

    BODY = {
        "holidays": [
            {"date": "2026-10-20", "name": "Ayudha Puja"},
            {"date": "2026-11-08", "name": "Deepavali"},
            {"date": "2026-12-25", "name": "Christmas", "location_id": "loc-chennai"},
            {"date": "2026-12-25", "name": "Xmas", "location_id": "loc-chennai"},
        ]
    }

    @pytest.mark.parametrize("who", ["u-mine", "u-lead", "u-manager"])
    def test_only_an_admin(self, as_, fake, released, who):
        r = as_(who).post(f"{API}/holidays/bulk", json=self.BODY)
        assert r.status_code == 403
        assert len(fake.holidays) == 1
        assert released == []
        assert fake.audit == []

    def test_declares_new_dates_and_skips_taken_ones(self, as_, fake, released):
        r = as_("u-admin").post(f"{API}/holidays/bulk", json=self.BODY)
        assert r.status_code == 200
        body = r.json()
        assert [c["name"] for c in body["created"]] == ["Ayudha Puja", "Christmas"]
        assert [(s["name"], s["reason"]) for s in body["skipped"]] == [
            ("Deepavali", "Already a holiday (Diwali)."),
            ("Xmas", "Listed earlier in this list."),
        ]
        assert body["released_bookings"] == 2
        assert [c["location_id"] for c in released] == [None, "loc-chennai"]
        assert [a["action"] for a in fake.audit] == ["holiday.declared", "holiday.declared"]

    def test_an_unknown_location_refuses_the_whole_list(self, as_, fake, released):
        body = {
            "holidays": [
                {"date": "2026-10-20", "name": "Ayudha Puja"},
                {"date": "2026-10-21", "name": "Vijayadashami", "location_id": "loc-nowhere"},
            ]
        }
        r = as_("u-admin").post(f"{API}/holidays/bulk", json=body)
        assert r.status_code == 422
        assert "Holiday 2" in r.json()["detail"]
        assert len(fake.holidays) == 1
        assert released == []

    @pytest.mark.parametrize(
        "holidays",
        [
            [],
            [{"date": "20/10/2026", "name": "Ayudha Puja"}],
            [{"date": "2026-10-20", "name": ""}],
            [{"date": "2026-10-20", "name": "Day"}] * 101,
        ],
    )
    def test_malformed_lists_are_refused(self, as_, fake, released, holidays):
        r = as_("u-admin").post(f"{API}/holidays/bulk", json={"holidays": holidays})
        assert r.status_code == 422
        assert len(fake.holidays) == 1


class TestInvoices:
    """Spec 006 FR-MILE-05..08 — the invoicing tracker is money: managers and
    admins only, and a payment cannot precede its invoice."""

    @pytest.fixture(autouse=True)
    def _today(self, monkeypatch):
        from app.services import invoicing, settings_store

        settings_store.invalidate()
        monkeypatch.setattr(invoicing, "today_in_company_tz", lambda: date(2026, 10, 15))

    @pytest.mark.parametrize("who", ["u-lead", "u-mine"])
    def test_lead_and_user_are_refused(self, as_, fake, who):
        r = as_(who).get("/api/v1/analytics/invoices")
        assert r.status_code == 403

    @pytest.mark.parametrize("who", ["u-manager", "u-admin"])
    def test_manager_and_admin_see_every_milestone(self, as_, fake, who):
        r = as_(who).get("/api/v1/analytics/invoices")
        assert r.status_code == 200
        body = r.json()
        rows = {i["id"]: i for i in body["invoices"]}
        assert rows["m-billed"]["status"] == "payment_overdue"
        assert rows["m-billed"]["days_overdue"] == 14
        assert rows["m-billed"]["client"] == "Acme"
        assert rows["m-unbilled"]["status"] == "upcoming"
        assert body["payment_terms_days"] == 30
        assert body["totals"]["receivable"] == "40000"

    def test_status_filters_the_list_not_the_totals(self, as_, fake):
        r = as_("u-manager").get("/api/v1/analytics/invoices?status=upcoming")
        assert [i["id"] for i in r.json()["invoices"]] == ["m-unbilled"]
        assert r.json()["totals"]["receivable"] == "40000"

    def test_unknown_status_is_refused(self, as_, fake):
        r = as_("u-manager").get("/api/v1/analytics/invoices?status=lost")
        assert r.status_code == 422

    def test_a_tentative_project_bills_nobody(self, as_, fake):
        fake.projects["p-live"]["status"] = "tentative"
        r = as_("u-manager").get("/api/v1/analytics/invoices")
        assert r.json()["invoices"] == []
        assert r.json()["totals"]["receivable"] == "0"

    @pytest.mark.parametrize("who", ["u-lead", "u-mine"])
    def test_lead_cannot_mark_a_milestone_paid(self, as_, fake, who):
        r = as_(who).patch(f"{API}/milestones/m-billed", json={"paid_on": "2026-10-10"})
        assert r.status_code == 403
        assert fake.milestones["m-billed"]["paid_on"] is None

    def test_manager_records_invoice_number_and_payment(self, as_, fake):
        r = as_("u-manager").patch(
            f"{API}/milestones/m-billed",
            json={"invoice_number": " INV-042 ", "paid_on": "2026-10-10"},
        )
        assert r.status_code == 200
        assert r.json()["invoice_number"] == "INV-042"
        assert r.json()["paid_on"] == "2026-10-10"
        assert fake.audit[-1]["action"] == "milestone.updated"

    def test_empty_invoice_number_clears_it(self, as_, fake):
        fake.milestones["m-billed"]["invoice_number"] = "INV-1"
        r = as_("u-manager").patch(f"{API}/milestones/m-billed", json={"invoice_number": ""})
        assert r.status_code == 200
        assert fake.milestones["m-billed"]["invoice_number"] is None

    def test_paying_an_uninvoiced_milestone_is_refused(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/milestones/m-unbilled", json={"paid_on": "2026-10-10"})
        assert r.status_code == 422
        assert fake.milestones["m-unbilled"]["paid_on"] is None
        assert fake.audit == []

    def test_paying_before_the_invoice_date_is_refused(self, as_, fake):
        r = as_("u-manager").patch(f"{API}/milestones/m-billed", json={"paid_on": "2026-08-31"})
        assert r.status_code == 422

    def test_uninvoicing_a_paid_milestone_is_refused(self, as_, fake):
        fake.milestones["m-billed"]["paid_on"] = "2026-10-01"
        r = as_("u-manager").patch(f"{API}/milestones/m-billed", json={"clear_invoiced": True})
        assert r.status_code == 422
        assert fake.milestones["m-billed"]["invoiced_on"] == "2026-09-01"

    def test_clearing_both_together_is_allowed(self, as_, fake):
        fake.milestones["m-billed"]["paid_on"] = "2026-10-01"
        r = as_("u-manager").patch(
            f"{API}/milestones/m-billed", json={"clear_invoiced": True, "clear_paid": True}
        )
        assert r.status_code == 200
        assert fake.milestones["m-billed"]["paid_on"] is None
