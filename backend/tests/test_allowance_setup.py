"""Allowances that were never set up, and removing a personal override.

Spec 001 FR-BAL-09 (say "not set up yet", distinctly from "used up"),
FR-ADMIN-01a (remove one person's override), Q-01 (the company sick default).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api_permissions import FakeDb

from app.api.deps import CurrentUser, current_user
from app.domain import ledger
from app.domain.rules import BookingRequest, check_allowance, validate_booking
from app.main import app
from app.services import balances, settings_store
from app.services import supabase as db

D = Decimal
MIGRATION = (
    Path(__file__).resolve().parents[2] / "supabase/migrations/023_sick_allowance_default.sql"
)


def grant(period, category, days, user_id=None):
    return ledger.Grant(period=period, category=category, days=D(days), user_id=user_id)


class TestIsConfigured:
    def test_no_grants_is_not_configured(self):
        assert not ledger.is_configured([], "2026-10", "sick")

    def test_an_earlier_company_default_counts(self):
        assert ledger.is_configured([grant("2026-09", "sick", "1.0")], "2026-10", "sick")

    def test_a_personal_row_counts(self):
        assert ledger.is_configured([grant("2026-10", "sick", "2.0", "u-1")], "2026-10", "sick")

    def test_a_later_grant_does_not_count(self):
        assert not ledger.is_configured([grant("2026-11", "sick", "1.0")], "2026-10", "sick")

    def test_another_category_does_not_count(self):
        assert not ledger.is_configured([grant("2026-09", "casual", "3.0")], "2026-10", "sick")


class TestBalanceConfigured:
    def test_nothing_set_up_is_zero_and_not_configured(self):
        balance = ledger.balance_for([grant("2026-09", "casual", "3.0")], [], "2026-10", "sick")
        assert balance.remaining == D("0")
        assert balance.configured is False

    def test_used_up_is_zero_but_configured(self):
        used = [ledger.Consumption(period="2026-10", category="sick", duration=D("1.0"))]
        balance = ledger.balance_for([grant("2026-10", "sick", "1.0")], used, "2026-10", "sick")
        assert balance.remaining == D("0")
        assert balance.configured is True

    def test_balances_serialise_configured(self, monkeypatch):
        monkeypatch.setattr(
            db,
            "list_allowances",
            lambda **_: [
                {"period": "2026-09", "category": "casual", "days": "3.0", "user_id": None}
            ],
        )
        monkeypatch.setattr(db, "list_bookings", lambda **_: [])
        monkeypatch.setattr(settings_store, "carry_forward_policy", lambda: "rolling")

        summary = {row["category"]: row for row in balances.summary_for("u-1", period="2026-10")}
        assert summary["casual"]["configured"] is True
        assert summary["sick"]["configured"] is False


class TestCheckAllowance:
    def test_not_configured_says_so(self):
        failure = check_allowance(D("0"), D("1.0"), "sick", allow_excess=False, configured=False)
        assert failure == (
            "Sick leave has not been set up yet, so there is no allowance to book against. "
            "Ask an admin to set a monthly sick leave allowance (Admin > Allowances)."
        )

    def test_allow_excess_still_wins(self):
        assert (
            check_allowance(D("0"), D("1.0"), "sick", allow_excess=True, configured=False) is None
        )

    def test_configured_zero_keeps_the_used_up_message(self):
        failure = check_allowance(D("0"), D("1.0"), "sick", allow_excess=False, configured=True)
        assert "0 days remaining" in failure
        assert "1 day requested" in failure

    def test_same_day_sick_with_nothing_set_up(self):
        today = date(2026, 10, 12)  # a Monday
        failure = validate_booking(
            BookingRequest(today, "sick", D("1.0"), "Fever"),
            holiday_name=None,
            remaining=D("0"),
            today=today,
            max_future_days=365,
            allow_excess=False,
            configured=False,
        )
        assert "has not been set up yet" in failure

    def test_a_weekend_is_still_reported_first(self):
        saturday = date(2026, 10, 10)
        failure = validate_booking(
            BookingRequest(saturday, "sick", D("1.0"), "Fever"),
            holiday_name=None,
            remaining=D("0"),
            today=saturday,
            max_future_days=365,
            allow_excess=False,
            configured=False,
        )
        assert "weekend" in failure


class TestMigration:
    def test_sick_default_is_one_day_from_october(self):
        sql = MIGRATION.read_text()
        assert "('2026-10', 'sick', 1.0, NULL)" in sql
        assert "ON CONFLICT DO NOTHING" in sql


# ---------------------------------------------------------------------------
# DELETE /admin/allowances/{id} — FR-ADMIN-01a
# ---------------------------------------------------------------------------

API = "/api/v1/admin/allowances"


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    fake = FakeDb()
    fake.allowance_rows = {
        "al-override": {
            "id": "al-override",
            "period": "2026-10",
            "category": "casual",
            "days": "2.0",
            "user_id": "u-mine",
        },
        "al-default": {
            "id": "al-default",
            "period": "2026-10",
            "category": "sick",
            "days": "1.0",
            "user_id": None,
        },
    }
    for name in dir(FakeDb):
        if not name.startswith("_"):
            monkeypatch.setattr(db, name, getattr(fake, name))
    return fake


@pytest.fixture
def as_(fake):
    def sign_in(user_id: str) -> TestClient:
        app.dependency_overrides[current_user] = lambda: CurrentUser(fake.profiles[user_id])
        return TestClient(app)

    yield sign_in
    app.dependency_overrides.pop(current_user, None)


class TestRemoveAllowance:
    @pytest.mark.parametrize("who", ["u-manager", "u-lead", "u-mine"])
    def test_only_admins(self, as_, fake, who):
        r = as_(who).delete(f"{API}/al-override")
        assert r.status_code == 403
        assert "al-override" in fake.allowance_rows
        assert fake.audit == []

    def test_missing_is_404(self, as_, fake):
        r = as_("u-admin").delete(f"{API}/nope")
        assert r.status_code == 404
        assert fake.audit == []

    def test_a_company_default_is_refused(self, as_, fake):
        r = as_("u-admin").delete(f"{API}/al-default")
        assert r.status_code == 422
        assert "al-default" in fake.allowance_rows
        assert fake.audit == []

    def test_an_override_is_removed_and_audited(self, as_, fake):
        r = as_("u-admin").delete(f"{API}/al-override")
        assert r.status_code == 200
        assert r.json() == {"status": "removed"}
        assert "al-override" not in fake.allowance_rows
        entry = fake.audit[-1]
        assert entry["action"] == "allowance.removed"
        assert entry["before"] == {
            "period": "2026-10",
            "category": "casual",
            "days": "2.0",
            "user_id": "u-mine",
        }
