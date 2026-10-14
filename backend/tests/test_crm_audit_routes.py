"""The TVA audit names CRM-only actors — spec 007 FR-CRM-AUD-04 (name half).

CRM-only accounts are left out of every TVA people list (`list_profiles`'s
default), but an audit row a seller caused must still show their name, not a
bare id. `read_audit` therefore asks for them explicitly. The workspace half
of FR-CRM-AUD-04 (TVA rows only) arrives with `audit_log.workspace_id` in S1b.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, current_user
from app.main import app
from app.services import supabase as db

ADMIN = {
    "id": "u-admin",
    "email": "admin@example.com",
    "display_name": "Admin",
    "role": "admin",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
SELLER = {
    "id": "u-crm",
    "email": "seller@example.com",
    "display_name": "Sanjay Seller",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "logs_time": False,
    "account_type": "crm_only",
}


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def list_profiles(*, active_only=False, include_crm_only=False):
        seen.append({"include_crm_only": include_crm_only})
        return [p for p in (ADMIN, SELLER) if include_crm_only or p["account_type"] == "staff"]

    monkeypatch.setattr(db, "list_profiles", list_profiles)
    monkeypatch.setattr(
        db,
        "list_audit",
        lambda limit: [
            {"id": 1, "actor_id": "u-crm", "action": "token.created", "target_table": "api_tokens"},
            {"id": 2, "actor_id": "u-admin", "action": "user.updated", "target_table": "profiles"},
        ],
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(ADMIN)
    yield seen
    app.dependency_overrides.pop(current_user, None)


class TestReadAudit:
    def test_names_a_crm_only_actor(self, calls):
        rows = TestClient(app).get("/api/v1/admin/audit").json()
        assert {r["id"]: r["actor"] for r in rows} == {1: "Sanjay Seller", 2: "Admin"}

    def test_asks_for_crm_only_names_explicitly(self, calls):
        TestClient(app).get("/api/v1/admin/audit")
        assert calls == [{"include_crm_only": True}]
