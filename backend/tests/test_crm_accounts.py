"""CRM-only accounts in TVA — spec 007 FR-CRM-ACC-01, FR-CRM-CHG-01.

A CRM-only seller is a `profiles` row with `account_type = 'crm_only'`. They
authenticate exactly as staff do (`deps._authenticate`), and then
`current_user` refuses them on every TVA route while `any_account` lets them
reach the shared allow-list. These tests run the real dependency chain:
GoTrue and the profile read are replaced at the function boundary, nothing
else.

Also here: the reduced `/me`, the Admin → People refusals on a CRM-only
profile, the deactivation reason, the Slack button refusal, and the audit
channel label (`mcp` only with the per-process key).
"""

from __future__ import annotations

import pytest
from crm_fake import FakeCrmStore
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import deps
from app.api import slack as slack_api
from app.api.deps import CurrentUser, current_user
from app.api.errors import ProblemDetail
from app.main import app
from app.mcp.server import CHANNEL_KEY
from app.services import supabase as db
from app.services.notify import slack as slack_adapter

SELLER = {
    "id": "u-crm",
    "email": "seller@example.com",
    "display_name": "Seller",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "logs_time": False,
    "account_type": "crm_only",
    "timezone": "Australia/Sydney",
    "number_format": "full",
    "is_owner": False,
    "dashboard_access": False,
}
STAFF = {
    "id": "u-staff",
    "email": "staff@example.com",
    "display_name": "Staff",
    "role": "user",
    "lead_id": "u-admin",
    "is_active": True,
    "logs_time": True,
    "account_type": "staff",
}
ADMIN = {
    "id": "u-admin",
    "email": "admin@example.com",
    "display_name": "Admin",
    "role": "admin",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
    "is_owner": True,
}

CRM_ONLY = "This account is for the CRM only."


@pytest.fixture
def profiles(monkeypatch) -> dict[str, dict]:
    """Sessions are tokens named after the profile id: 'Bearer u-crm'."""
    rows = {p["id"]: dict(p) for p in (SELLER, STAFF, ADMIN)}
    # GET /me lists CRM workspaces (S1b); nobody here has one.
    FakeCrmStore().install(monkeypatch)
    monkeypatch.setattr(deps, "_verify", lambda token: token)
    monkeypatch.setattr(db, "get_profile", lambda uid: dict(rows[uid]) if uid in rows else None)
    return rows


@pytest.fixture
def client(profiles) -> TestClient:
    return TestClient(app)


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


class TestGuards:
    def test_current_user_refuses_crm_only(self, client):
        r = client.get("/api/v1/me/home", headers=_as("u-crm"))
        assert r.status_code == 403
        assert r.json()["detail"] == CRM_ONLY

    def test_role_guards_refuse_crm_only_the_same_way(self, client):
        r = client.get("/api/v1/admin/users", headers=_as("u-crm"))
        assert r.status_code == 403
        assert r.json()["detail"] == CRM_ONLY

    def test_any_account_allows_crm_only(self, client):
        r = client.get("/api/v1/me", headers=_as("u-crm"))
        assert r.status_code == 200

    def test_current_user_still_allows_staff(self, profiles):
        user = deps.current_user(CurrentUser(profiles["u-staff"]))
        assert user.id == "u-staff" and user.account_type == "staff"

    def test_a_profile_without_the_column_is_staff(self):
        row = {k: v for k, v in STAFF.items() if k != "account_type"}
        assert CurrentUser(row).is_crm_only is False

    def test_deactivated_crm_only_is_refused_as_deactivated(self, client, profiles):
        profiles["u-crm"]["is_active"] = False
        r = client.get("/api/v1/me", headers=_as("u-crm"))
        assert r.status_code == 403
        assert r.json()["detail"] == "This account has been deactivated."


class TestMe:
    def test_crm_only_gets_the_reduced_shape(self, client):
        body = client.get("/api/v1/me", headers=_as("u-crm")).json()
        assert body == {
            "id": "u-crm",
            "email": "seller@example.com",
            "display_name": "Seller",
            "account_type": "crm_only",
            "crm": {"workspaces": []},
            "preferences": {"timezone": "Australia/Sydney", "number_format": "full"},
        }

    def test_crm_only_has_no_role_lead_or_capabilities(self, client):
        body = client.get("/api/v1/me", headers=_as("u-crm")).json()
        assert not {"role", "lead_id", "capabilities"} & set(body)

    def test_staff_get_account_type_and_crm_too(self, client):
        body = client.get("/api/v1/me", headers=_as("u-staff")).json()
        assert body["account_type"] == "staff"
        assert body["crm"] == {"workspaces": []}
        assert body["role"] == "user" and "capabilities" in body


# ---------------------------------------------------------------------------
# Admin → People
# ---------------------------------------------------------------------------


class PeopleFake:
    def __init__(self) -> None:
        self.profiles = {p["id"]: dict(p) for p in (SELLER, STAFF, ADMIN)}
        self.audit: list[dict] = []

    def get_profile(self, uid):
        return dict(self.profiles[uid]) if uid in self.profiles else None

    def update_profile(self, uid, data):
        self.profiles[uid].update(data)
        return dict(self.profiles[uid])

    def insert_audit(self, entry):
        self.audit.append(entry)


@pytest.fixture
def people(monkeypatch) -> PeopleFake:
    fake = PeopleFake()
    for name in ("get_profile", "update_profile", "insert_audit"):
        monkeypatch.setattr(db, name, getattr(fake, name))
    app.dependency_overrides[current_user] = lambda: CurrentUser(fake.profiles["u-admin"])
    yield fake
    app.dependency_overrides.pop(current_user, None)


def _patch(uid: str, body: dict):
    return TestClient(app).patch(f"/api/v1/admin/users/{uid}", json=body)


class TestUpdateUser:
    @pytest.mark.parametrize(
        "body",
        [{"role": "lead"}, {"lead_id": "u-admin"}, {"logs_time": True}, {"dashboard_access": True}],
    )
    def test_tva_fields_are_refused_on_crm_only(self, people, body):
        r = _patch("u-crm", body)
        assert r.status_code == 422
        assert r.json()["detail"] == "Change the account type first."
        assert people.profiles["u-crm"] == SELLER

    def test_display_name_is_still_editable_on_crm_only(self, people):
        r = _patch("u-crm", {"display_name": "Seller Two"})
        assert r.status_code == 200
        assert people.profiles["u-crm"]["display_name"] == "Seller Two"

    def test_the_same_fields_are_allowed_on_staff(self, people):
        r = _patch("u-staff", {"role": "lead", "logs_time": False})
        assert r.status_code == 200

    @pytest.mark.parametrize("uid", ["u-crm", "u-staff"])
    def test_deactivation_records_admin_as_the_reason(self, people, uid):
        r = _patch(uid, {"is_active": False})
        assert r.status_code == 200
        assert people.profiles[uid]["deactivated_reason"] == "admin"
        assert people.audit[-1]["after"]["deactivated_reason"] == "admin"

    def test_reactivation_clears_the_reason(self, people):
        people.profiles["u-crm"].update(is_active=False, deactivated_reason="crm_no_membership")
        r = _patch("u-crm", {"is_active": True})
        assert r.status_code == 200
        assert people.profiles["u-crm"]["deactivated_reason"] is None


# ---------------------------------------------------------------------------
# Slack buttons act as the person without a session
# ---------------------------------------------------------------------------


class TestSlack:
    def test_the_button_refuses_a_crm_only_actor(self, monkeypatch):
        monkeypatch.setattr(slack_adapter, "lookup_email", lambda uid: "seller@example.com")
        monkeypatch.setattr(db, "get_profile_by_email_ignoring_case", lambda email: dict(SELLER))
        with pytest.raises(ProblemDetail) as refused:
            slack_api._resolve_actor({"user": {"id": "U-SELLER"}})
        assert refused.value.status == 403
        assert refused.value.detail == CRM_ONLY

    def test_the_button_still_resolves_staff(self, monkeypatch):
        monkeypatch.setattr(slack_adapter, "lookup_email", lambda uid: "staff@example.com")
        monkeypatch.setattr(db, "get_profile_by_email_ignoring_case", lambda email: dict(STAFF))
        assert slack_api._resolve_actor({"user": {"id": "U-STAFF"}}).id == "u-staff"


# ---------------------------------------------------------------------------
# The audit channel — a label, never a permission
# ---------------------------------------------------------------------------


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


class TestChannel:
    def test_mcp_with_the_process_key(self, profiles):
        request = _request({**_as("u-staff"), "X-Portal-Channel-Key": CHANNEL_KEY})
        deps._authenticate(request)
        assert request.state.channel == "mcp"

    def test_a_bare_client_header_stays_web(self, profiles):
        request = _request({**_as("u-staff"), "X-Portal-Client": "mcp"})
        deps._authenticate(request)
        assert request.state.channel == "web"

    def test_a_wrong_key_stays_web(self, profiles):
        request = _request({**_as("u-staff"), "X-Portal-Channel-Key": "guess"})
        deps._authenticate(request)
        assert request.state.channel == "web"

    def test_no_header_is_web(self, profiles):
        request = _request(_as("u-staff"))
        deps._authenticate(request)
        assert request.state.channel == "web"

    def test_the_mcp_bridge_sends_the_key(self):
        import inspect

        from app.mcp import server

        assert '"X-Portal-Channel-Key": CHANNEL_KEY' in inspect.getsource(server._api)
        assert len(CHANNEL_KEY) >= 40


class TestPeopleList:
    def test_lists_crm_only_with_their_account_type(self, people, monkeypatch):
        from app.api import admin as admin_api

        monkeypatch.setattr(
            db,
            "list_profiles",
            lambda *, active_only=False, include_crm_only=False: [
                p
                for p in people.profiles.values()
                if include_crm_only or p["account_type"] == "staff"
            ],
        )
        book = type("Book", (), {"monthly_now": lambda self, uid, day: None})()
        monkeypatch.setattr(admin_api.pnl_service, "rate_book", lambda start, end: book)
        rows = {r["id"]: r for r in TestClient(app).get("/api/v1/admin/users").json()}
        assert rows["u-crm"]["account_type"] == "crm_only"
        assert rows["u-staff"]["account_type"] == "staff"


class TestTvaWorkRefused:
    """Python refuses first, with the sentences 025's trigger also raises."""

    @pytest.fixture
    def tva(self, people, monkeypatch):
        project = {"id": "p1", "name": "Atlas", "is_archived": False}
        monkeypatch.setattr(db, "get_project", lambda pid: dict(project) if pid == "p1" else None)
        monkeypatch.setattr(db, "list_projects", lambda include_archived=False: [project])
        return people

    def test_allocating_a_crm_only_account(self, tva):
        r = TestClient(app).post(
            "/api/v1/admin/allocations",
            json={
                "project_id": "p1",
                "user_id": "u-crm",
                "starts_on": "2026-10-01",
                "ends_on": "2026-10-31",
                "percent": "50",
            },
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account cannot be allocated."

    def test_naming_a_crm_only_project_lead_on_create(self, tva):
        r = TestClient(app).post("/api/v1/admin/projects", json={"name": "New", "lead_id": "u-crm"})
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account cannot lead a project."

    def test_naming_a_crm_only_project_lead_on_update(self, tva):
        r = TestClient(app).patch("/api/v1/admin/projects/p1", json={"lead_id": "u-crm"})
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account cannot lead a project."

    def test_naming_a_crm_only_lead_on_update_user(self, tva):
        r = _patch("u-staff", {"lead_id": "u-crm"})
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account cannot be anyone's lead."
        assert tva.profiles["u-staff"] == STAFF

    def test_naming_a_crm_only_lead_on_create_user(self, tva, monkeypatch):
        class NoGoTrue:
            def __getattr__(self, name):
                raise AssertionError("GoTrue was reached before the lead was refused")

        monkeypatch.setattr(db, "get_profile_by_email", lambda email: None)
        monkeypatch.setattr(db, "supabase", NoGoTrue())
        r = TestClient(app).post(
            "/api/v1/admin/users",
            json={"email": "new@example.com", "display_name": "New", "lead_id": "u-crm"},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account cannot be anyone's lead."

    def test_backfilling_leave_for_a_crm_only_account(self, tva):
        r = TestClient(app).post(
            "/api/v1/admin/backfill",
            json={
                "user_id": "u-crm",
                "date": "2026-10-01",
                "category": "casual",
                "duration": "1.0",
                "note": "Took the day",
            },
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no leave."

    def test_setting_an_allowance_for_a_crm_only_account(self, tva):
        r = TestClient(app).put(
            "/api/v1/admin/allowances",
            json={"period": "2026-10", "category": "casual", "days": "2", "user_id": "u-crm"},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no leave."

    def test_adding_ctc_for_a_crm_only_account(self, tva):
        r = TestClient(app).post(
            "/api/v1/admin/users/u-crm/ctc",
            json={"annual_ctc": "1200000", "starts_on": "2026-10-01"},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no CTC."

    def test_starting_a_checklist_for_a_crm_only_account(self, tva):
        r = TestClient(app).post(
            "/api/v1/admin/checklists", json={"user_id": "u-crm", "kind": "onboarding"}
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no checklists."


class TestTvaRecordsAboutCrmOnlyRefused:
    """FR-CRM-CHG-02 — an admin or manager writes no absence, sign-off,
    review or leave decision about a CRM-only account, and cannot open one's
    timesheet. Each refusal comes before anything is written."""

    @pytest.fixture
    def tva(self, people, monkeypatch):
        from app.services import reviews as review_service

        def never(*args, **kwargs):
            raise AssertionError("a write was reached for a CRM-only account")

        for name in (
            "insert_booking",
            "update_booking",
            "upsert_confirmation",
            "delete_confirmation",
            "find_booking_on",
        ):
            monkeypatch.setattr(db, name, never)
        monkeypatch.setattr(review_service, "close", never)
        bookings = {
            "b-crm": {"id": "b-crm", "user_id": "u-crm", "status": "pending"},
        }
        monkeypatch.setattr(db, "get_booking", lambda bid: dict(bookings[bid]))
        return people

    def test_flagging_an_absence(self, tva):
        r = TestClient(app).post(
            "/api/v1/team/unrecognised", json={"user_id": "u-crm", "date": "2026-10-01"}
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no absences to record."

    def test_confirming_a_week(self, tva):
        r = TestClient(app).post("/api/v1/team/timesheets/u-crm/2026-10-05/confirm", json={})
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no timesheet."

    def test_reopening_a_week(self, tva):
        r = TestClient(app).delete("/api/v1/team/timesheets/u-crm/2026-10-05/confirm")
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no timesheet."

    def test_closing_a_review(self, tva):
        r = TestClient(app).post("/api/v1/team/reviews/u-crm/2026-Q3/close")
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no review."

    @pytest.mark.parametrize("approve", [True, False])
    def test_deciding_their_booking(self, tva, approve):
        r = TestClient(app).post(
            "/api/v1/bookings/b-crm/decision", json={"approve": approve, "note": "No"}
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "A CRM-only account has no leave."

    def test_their_timesheet_is_not_found(self, tva, monkeypatch):
        from app.services import timesheets as timesheet_service

        monkeypatch.setattr(timesheet_service, "week_for", lambda *a, **k: {"days": []})
        r = TestClient(app).get("/api/v1/timesheet/of/u-crm/week?week_start=2026-10-05")
        assert r.status_code == 404
        assert r.json()["detail"] == "No such person."
        staff = TestClient(app).get("/api/v1/timesheet/of/u-staff/week?week_start=2026-10-05")
        assert staff.status_code == 200
