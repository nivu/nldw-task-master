"""FR-BAL-10 — the `no_carry_forward_categories` setting.

The ledger arithmetic is in test_ledger.py; this covers the setting itself:
how it is read, that balances honour it per category, and what an admin may
store in it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, current_user
from app.main import app
from app.services import balances, settings_store
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


class SettingsFake:
    def __init__(self) -> None:
        self.settings: dict = {
            "carry_forward_policy": "rolling",
            "no_carry_forward_categories": ["sick", "wfh"],
        }
        self.audit: list[dict] = []

    def list_settings(self):
        return [{"key": k, "value": v, "description": ""} for k, v in self.settings.items()]

    def update_setting(self, key, value, actor_id):
        self.settings[key] = value
        return {"key": key, "value": value}

    def insert_audit(self, entry):
        self.audit.append(entry)

    def get_profile(self, uid):
        return dict(ADMIN) if uid == "u-admin" else None

    def list_profiles(self, *, active_only=False, include_crm_only=False):
        return [dict(ADMIN)]


@pytest.fixture
def store(monkeypatch) -> SettingsFake:
    fake = SettingsFake()
    for name in ("list_settings", "update_setting", "insert_audit", "get_profile", "list_profiles"):
        monkeypatch.setattr(db, name, getattr(fake, name))
    settings_store.invalidate()
    app.dependency_overrides[current_user] = lambda: CurrentUser(dict(ADMIN))
    yield fake
    app.dependency_overrides.pop(current_user, None)
    settings_store.invalidate()


def _put(value):
    return TestClient(app).put(
        "/api/v1/admin/settings/no_carry_forward_categories", json={"value": value}
    )


class TestReading:
    def test_the_stored_list(self, store):
        assert settings_store.no_carry_forward_categories() == {"wfh", "sick"}

    def test_a_malformed_value_carries_everything(self, store):
        store.settings["no_carry_forward_categories"] = "wfh"
        assert settings_store.no_carry_forward_categories() == frozenset()


class TestBalances:
    """Casual carries; WFH and sick on the list open at zero."""

    def test_only_listed_categories_lapse(self, store, monkeypatch):
        rows = [
            {"period": "2026-10", "category": c, "days": "2.0", "user_id": None}
            for c in ("wfh", "casual", "sick")
        ]
        monkeypatch.setattr(db, "list_allowances", lambda **_: rows)
        monkeypatch.setattr(db, "list_bookings", lambda **_: [])

        result = balances.balances_for("u-staff", "2026-11")
        assert result["casual"].opening == Decimal("2.0")
        assert result["wfh"].opening == Decimal("0.0")
        assert result["sick"].opening == Decimal("0.0")


class TestUpdating:
    @pytest.mark.parametrize(
        ("value", "stored"),
        [(["wfh"], ["wfh"]), (["wfh", "sick", "wfh"], ["sick", "wfh"]), ([], [])],
    )
    def test_a_list_of_allowance_categories(self, store, value, stored):
        r = _put(value)
        assert r.status_code == 200, r.text
        assert store.settings["no_carry_forward_categories"] == stored

    @pytest.mark.parametrize("value", ["wfh", ["compoff"], ["holiday"], None, True])
    def test_anything_else_is_refused(self, store, value):
        r = _put(value)
        assert r.status_code == 422
        assert r.json()["detail"].startswith("no_carry_forward_categories must be a list")
        assert store.settings["no_carry_forward_categories"] == ["sick", "wfh"]
