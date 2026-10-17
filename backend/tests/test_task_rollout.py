"""Phase switches — spec 008 FR-TASK-ROLL-01 to -05; C6; TP-11;
contracts §3 (`GET /me`, `PATCH /admin/users/{id}`, `GET /admin/users`,
`PUT /admin/settings/tasks_default_level`).

Acceptance criteria (spec §12, phase 1):
- "Switching a person's phase is audited; a person's level n switches on
  phases 1 to n, and the global default starts at 0 and is set through
  update_setting" — TestLevels, TestUpdateUser, TestTheDefault.
- The off-by-default gate: a person with no level of their own, under the
  default of 0, gets `tasks.level = 0` from `/me` — TestMe.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from crm_fake import FakeCrmStore
from fastapi.testclient import TestClient

from app.api import admin as admin_api
from app.api import deps
from app.api.deps import CurrentUser, current_user
from app.domain.tasks import rollout
from app.main import app
from app.mcp import server
from app.services import settings_store
from app.services import supabase as db

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
STAFF = {
    "id": "u-staff",
    "email": "staff@example.com",
    "display_name": "Staff",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
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
}


class TestLevels:
    """FR-TASK-ROLL-01 — cumulative 0-4, own level else the default."""

    @pytest.mark.parametrize(
        ("own", "default", "level"), [(None, 0, 0), (None, 2, 2), (3, 0, 3), (0, 4, 0)]
    )
    def test_own_level_else_the_default(self, own, default, level):
        assert rollout.level_for({"tasks_level": own}, default) == level

    @pytest.mark.parametrize(("level", "needed"), [(1, 1), (2, 1), (4, 3), (4, 4)])
    def test_level_n_switches_on_phases_1_to_n(self, level, needed):
        assert rollout.refusal(level, needed) is None

    @pytest.mark.parametrize(("level", "needed"), [(0, 1), (1, 2), (3, 4)])
    def test_a_phase_above_the_level_is_refused(self, level, needed):
        assert rollout.refusal(level, needed) == "Tasks are not switched on for you yet."

    def test_assignee_refusal_names_them(self):
        assert rollout.assignee_refusal("Vinita", 0) == "Vinita does not have My Tasks yet."
        assert rollout.assignee_refusal("Vinita", 1) is None


class PeopleFake:
    def __init__(self) -> None:
        self.profiles = {p["id"]: dict(p) for p in (ADMIN, STAFF, SELLER)}
        self.audit: list[dict] = []
        self.settings = {"tasks_default_level": 0}

    def get_profile(self, uid):
        return dict(self.profiles[uid]) if uid in self.profiles else None

    def update_profile(self, uid, data):
        self.profiles[uid].update(data)
        return dict(self.profiles[uid])

    def insert_audit(self, entry):
        self.audit.append(entry)

    def list_profiles(self, *, active_only=False, include_crm_only=False):
        return [dict(p) for p in self.profiles.values()]

    def list_settings(self):
        return [{"key": k, "value": v, "description": ""} for k, v in self.settings.items()]

    def update_setting(self, key, value, actor_id):
        self.settings[key] = value
        return {"key": key, "value": value}


@pytest.fixture
def people(monkeypatch) -> PeopleFake:
    fake = PeopleFake()
    for name in (
        "get_profile",
        "update_profile",
        "insert_audit",
        "list_profiles",
        "list_settings",
        "update_setting",
    ):
        monkeypatch.setattr(db, name, getattr(fake, name))
    settings_store.invalidate()
    app.dependency_overrides[current_user] = lambda: CurrentUser(fake.profiles["u-admin"])
    yield fake
    app.dependency_overrides.pop(current_user, None)
    settings_store.invalidate()


def _patch(uid: str, body: dict):
    return TestClient(app).patch(f"/api/v1/admin/users/{uid}", json=body)


class TestUpdateUser:
    """FR-TASK-ROLL-02 — a TVA admin sets it from Admin → People, audited."""

    def test_setting_a_level_is_audited_with_before_and_after(self, people):
        r = _patch("u-staff", {"tasks_level": 2})
        assert r.status_code == 200, r.text
        assert r.json()["tasks_level"] == 2
        assert people.profiles["u-staff"]["tasks_level"] == 2
        [entry] = [a for a in people.audit if a["action"] == "user.tasks_level"]
        assert (entry["target_table"], entry["target_id"], entry["actor_id"]) == (
            "profiles",
            "u-staff",
            "u-admin",
        )
        assert (entry["before"], entry["after"]) == ({"tasks_level": None}, {"tasks_level": 2})
        assert not [a for a in people.audit if a["action"] == "user.updated"]

    def test_default_follows_the_global_default(self, people):
        people.profiles["u-staff"]["tasks_level"] = 3
        r = _patch("u-staff", {"tasks_level": "default"})
        assert r.status_code == 200, r.text
        assert people.profiles["u-staff"]["tasks_level"] is None
        assert people.audit[-1]["after"] == {"tasks_level": None}

    @pytest.mark.parametrize("value", [5, -1, "three", 1.5])
    def test_only_0_to_4_or_default(self, people, value):
        assert _patch("u-staff", {"tasks_level": value}).status_code == 422

    def test_allowed_on_a_crm_only_account(self, people):
        r = _patch("u-crm", {"tasks_level": 1})
        assert r.status_code == 200, r.text
        assert people.profiles["u-crm"]["tasks_level"] == 1

    def test_only_a_tva_admin(self, people):
        app.dependency_overrides[current_user] = lambda: CurrentUser(people.profiles["u-staff"])
        assert _patch("u-staff", {"tasks_level": 4}).status_code == 403

    def test_the_admin_list_shows_their_own_level_or_null(self, people, monkeypatch):
        people.profiles["u-staff"]["tasks_level"] = 2
        book = SimpleNamespace(monthly_now=lambda user_id, day: None)
        monkeypatch.setattr(admin_api.pnl_service, "rate_book", lambda start, end: book)
        rows = {r["id"]: r for r in TestClient(app).get("/api/v1/admin/users").json()}
        assert (rows["u-staff"]["tasks_level"], rows["u-admin"]["tasks_level"]) == (2, None)
        assert "tasks_level_effective" not in rows["u-staff"]


class TestTheMcpTool:
    """FR-TASK-ROLL-02 — the existing `update_user` tool carries it in
    `changes`, with no new argument."""

    def test_update_user_passes_tasks_level_through(self, monkeypatch):
        calls = []

        async def recorder(ctx, method, path, *, params=None, body=None):
            calls.append((method, path, body))
            return {"ok": True}

        monkeypatch.setattr(server, "_api", recorder)
        asyncio.run(server.update_user(object(), "u-staff", {"tasks_level": 2}))
        assert calls == [("PATCH", "/admin/users/u-staff", {"tasks_level": 2})]


class TestTheDefault:
    """FR-TASK-ROLL-01, TP-11 — set through `update_setting`, 0 to 4."""

    @pytest.mark.parametrize("value", [0, 4, "2"])
    def test_a_whole_number_from_0_to_4(self, people, value):
        r = TestClient(app).put("/api/v1/admin/settings/tasks_default_level", json={"value": value})
        assert r.status_code == 200, r.text
        assert people.settings["tasks_default_level"] == int(value)

    @pytest.mark.parametrize("value", [5, -1, "x", True, 1.5, None])
    def test_anything_else_is_refused(self, people, value):
        r = TestClient(app).put("/api/v1/admin/settings/tasks_default_level", json={"value": value})
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "tasks_default_level must be a whole number from 0 to 4.",
        )
        assert people.settings["tasks_default_level"] == 0


@pytest.fixture
def me(monkeypatch):
    rows = {p["id"]: dict(p) for p in (STAFF, SELLER)}
    fake = FakeCrmStore().install(monkeypatch)
    monkeypatch.setattr(deps, "_verify", lambda token: token)
    monkeypatch.setattr(db, "get_profile", lambda uid: dict(rows[uid]) if uid in rows else None)
    return rows, fake


def _me(uid: str) -> dict:
    return TestClient(app).get("/api/v1/me", headers={"Authorization": f"Bearer {uid}"}).json()


class TestMe:
    """FR-TASK-ROLL-01, -03 — `tasks: {level}` in both shapes."""

    @pytest.mark.parametrize("uid", ["u-staff", "u-crm"])
    def test_off_by_default(self, me, uid):
        assert _me(uid)["tasks"] == {"level": 0}

    @pytest.mark.parametrize("uid", ["u-staff", "u-crm"])
    def test_the_global_default(self, me, uid):
        _, fake = me
        fake.tasks.default = 2
        assert _me(uid)["tasks"] == {"level": 2}

    @pytest.mark.parametrize("uid", ["u-staff", "u-crm"])
    def test_their_own_level_wins(self, me, uid):
        rows, fake = me
        fake.tasks.default = 4
        rows[uid]["tasks_level"] = 1
        assert _me(uid)["tasks"] == {"level": 1}

    def test_both_shapes_carry_it(self, me):
        staff, seller = _me("u-staff"), _me("u-crm")
        assert "capabilities" in staff and "tasks" in staff
        assert "capabilities" not in seller and "tasks" in seller
