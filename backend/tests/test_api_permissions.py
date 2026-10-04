"""Route-level permissions — spec 003 FR-ROLE-02/07/08, spec 002 FR-PROJ-04a, FR-ALLOC-06.

The domain tests check each rule in isolation. These check that the routes
actually apply them: the backend holds the service-role key, so a guard
missing from a route is a guard missing altogether (see app.api.deps).

Auth is replaced by a dependency override and the Supabase module by an
in-memory fake holding only what the project and allocation routes touch.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, current_user
from app.main import app
from app.services import supabase as db

PEOPLE = {
    "u-admin": {"role": "admin", "lead_id": None},
    "u-manager": {"role": "manager", "lead_id": None},
    "u-lead": {"role": "lead", "lead_id": None},
    "u-other-lead": {"role": "lead", "lead_id": None},
    "u-mine": {"role": "user", "lead_id": "u-lead"},
    "u-theirs": {"role": "user", "lead_id": "u-other-lead"},
}


class FakeDb:
    """Just enough of app.services.supabase for /admin/projects and /admin/allocations."""

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
        self.entries: list[dict] = []
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

    # Time, leave and holidays — read by the category totals
    def list_time_entries(self, *, user_ids=None, project_id=None, start=None, end=None):
        return [
            e
            for e in self.entries
            if (start is None or e["date"] >= start.isoformat())
            and (end is None or e["date"] <= end.isoformat())
        ]

    def list_bookings(self, **_):
        return []

    def list_holidays(self, start=None, end=None):
        return []

    def default_location_id(self):
        return None

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


class TestCategoryEffort:
    """Spec 002 FR-ANALYTICS-08 — hours by category: leads and up, no money."""

    PATH = "/api/v1/analytics/categories?start=2026-10&end=2026-10"

    def test_a_plain_user_is_refused(self, as_, fake):
        assert as_("u-mine").get(self.PATH).status_code == 403

    @pytest.mark.parametrize("who", ["u-lead", "u-manager", "u-admin"])
    def test_leads_and_up_see_hours_and_never_money(self, as_, fake, who):
        r = as_(who).get(self.PATH)
        assert r.status_code == 200
        assert [c["category"] for c in r.json()["categories"]] == [
            "client",
            "poc",
            "product",
            "internal",
            "activity",
        ]
        for word in ("revenue", "cost", "profit", "ctc"):
            assert word not in r.text

    def test_a_lead_sees_the_whole_company_not_just_their_reports(self, as_, fake):
        fake.entries = [
            {
                "user_id": "u-theirs",
                "project_id": "p-live",
                "date": "2026-10-05",
                "hours_office": "6",
                "hours_home": "0",
            },
            {
                "user_id": "u-mine",
                "project_id": None,
                "activity": "learning",
                "date": "2026-10-05",
                "hours_office": "2",
                "hours_home": "0",
            },
        ]
        cells = {
            c["category"]: c["cells"][0] for c in as_("u-lead").get(self.PATH).json()["categories"]
        }
        # Two 50% allocations on Live (client) and one on Old (internal) over
        # October's 22 weekdays: 88h each.
        assert cells["client"] == {"logged_hours": "6.00", "planned_hours": "176.00"}
        assert cells["internal"] == {"logged_hours": "0.00", "planned_hours": "88.00"}
        assert cells["activity"] == {"logged_hours": "2.00", "planned_hours": None}

    @pytest.mark.parametrize(
        "query", ["start=2026-11&end=2026-10", "start=2026-13&end=2026-12", "start=Oct"]
    )
    def test_bad_ranges_are_refused(self, as_, fake, query):
        r = as_("u-lead").get(f"/api/v1/analytics/categories?{query}")
        assert r.status_code == 422

    @pytest.mark.parametrize(
        ("query", "start", "end"),
        [("start=2026-10", "2026-10", "2026-12"), ("end=2025-03", "2025-01", "2025-03")],
    )
    def test_one_month_given_frames_the_rest_of_that_year(self, as_, fake, query, start, end):
        body = as_("u-lead").get(f"/api/v1/analytics/categories?{query}").json()
        assert (body["start"], body["end"]) == (start, end)
