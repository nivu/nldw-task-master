"""Small fixes found through Claude — spec 001 FR-BOOK-02, FR-APPR-08,
spec 004 FR-MCP-03 (`pending_approvals`), spec 006 FR-COMP-01.

- A duration may be written "full" / "half", as Claude naturally writes it.
- The booking and backfill tools say a reason is required for casual and sick.
- An admin's approval queue spans the organisation and names each approver;
  `pending_approvals` joins leave and comp-off into one answer.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_api_permissions import FakeDb

from app.api.deps import CurrentUser, current_user
from app.main import app
from app.mcp import server
from app.schemas import BackfillIn, BookingCreate
from app.services import bookings as booking_service
from app.services import holidays as holiday_service
from app.services import supabase as db

BOOKING = {"date": "2026-12-15", "category": "casual", "reason": "Family"}
BACKFILL = {
    "user_id": "u-mine",
    "date": "2026-10-08",
    "category": "casual",
    "reason": "Family",
    "note": "Told me on the day",
}


class TestDayDuration:
    """FR-BOOK-02 — "full" / "half" are synonyms for 1.0 / 0.5."""

    @pytest.mark.parametrize("model,base", [(BookingCreate, BOOKING), (BackfillIn, BACKFILL)])
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("full", "1.0"),
            ("Full Day", "1.0"),
            ("HALF", "0.5"),
            (" half day ", "0.5"),
            ("1", "1.0"),
            ("1.0", "1.0"),
            ("0.5", "0.5"),
            (1, "1.0"),
            (1.0, "1.0"),
            (0.5, "0.5"),
        ],
    )
    def test_accepted(self, model, base, given, expected):
        assert model(**base, duration=given).duration == Decimal(expected)

    @pytest.mark.parametrize("model,base", [(BookingCreate, BOOKING), (BackfillIn, BACKFILL)])
    @pytest.mark.parametrize("given", ["2", "quarter", "", 0, 0.25, "fullday"])
    def test_refused(self, model, base, given):
        with pytest.raises(ValidationError):
            model(**base, duration=given)

    def test_the_refusal_names_both_forms(self):
        with pytest.raises(ValidationError) as exc:
            BackfillIn(**BACKFILL, duration="2")
        assert "duration must be full (1.0) or half (0.5)" in str(exc.value)


@pytest.fixture
def fake(monkeypatch) -> FakeDb:
    fake = FakeDb()
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


class TestBackfillTakesAWord:
    def test_half_is_stored_as_half_a_day(self, as_, fake, monkeypatch):
        inserted: list[dict] = []

        def insert_booking(data):
            row = {"id": "b-new", **data}
            inserted.append(row)
            return row

        monkeypatch.setattr(booking_service, "today_in_company_tz", lambda: date(2026, 10, 10))
        monkeypatch.setattr(holiday_service, "holiday_on_for_user", lambda *_: None)
        monkeypatch.setattr(db, "find_booking_on", lambda *_, **__: None, raising=False)
        monkeypatch.setattr(db, "insert_booking", insert_booking)
        monkeypatch.setattr(db, "list_allowances", lambda **_: [])  # balance_after

        r = as_("u-admin").post("/api/v1/admin/backfill", json={**BACKFILL, "duration": "half"})
        assert r.status_code == 201, r.text
        assert r.json()["duration"] == "0.5"
        assert inserted[0]["duration"] == "0.5"
        assert any(e["action"] == "booking.backfilled" for e in fake.audit)


class TestToolDescriptions:
    @pytest.mark.parametrize("name", ["book_leave", "backfill_leave"])
    def test_the_reason_rule_is_stated(self, name):
        (tool,) = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == name]
        assert "required for casual and sick" in tool.description
        assert '"full" | "half"' in tool.description

    @pytest.mark.parametrize("name", ["team_compoff", "decide_compoff"])
    def test_comp_off_tools_are_for_admins_too(self, name):
        (tool,) = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == name]
        assert tool.description.startswith("LEADS AND ADMINS")


class TestAdminApprovalQueue:
    """FR-APPR-08 — an admin sees every pending request but their own, each
    naming who it waits on."""

    ROWS = [
        {"id": f"b-{uid}", "user_id": uid, "date": "2026-12-15", "category": "casual",
         "duration": "1.0", "status": "pending", "reason": "x", "created_at": None}
        for uid in ("u-mine", "u-theirs", "u-lead", "u-admin")
    ]  # fmt: skip

    @pytest.fixture(autouse=True)
    def _pending(self, fake, monkeypatch):
        monkeypatch.setattr(
            db,
            "list_bookings",
            lambda user_ids=None, **_: [
                dict(r) for r in self.ROWS if user_ids is None or r["user_id"] in user_ids
            ],
        )

    def queue(self, as_, who):
        r = as_(who).get("/api/v1/team/approvals")
        assert r.status_code == 200
        return {item["user_id"]: item["approver"] for item in r.json()}

    def test_an_admin_sees_every_lead_queue_but_not_their_own(self, as_):
        assert self.queue(as_, "u-admin") == {
            "u-mine": "u-lead",
            "u-theirs": "u-other-lead",
            "u-lead": None,
        }

    def test_a_lead_sees_their_own_reports_only(self, as_):
        assert self.queue(as_, "u-lead") == {"u-mine": "u-lead"}

    def test_a_manager_who_leads_nobody_sees_nothing(self, as_):
        assert self.queue(as_, "u-manager") == {}


class TestPendingApprovalsTool:
    """Spec 004 — one call returns leave and pending comp-off together."""

    class Ctx:
        headers = {"authorization": "Bearer nunp_test"}

    def test_leave_and_pending_claims_are_merged(self, monkeypatch):
        leave = [{"id": "b-1", "user_id": "u-mine", "approver": "u-lead"}]
        claims = [
            {"id": "c-1", "status": "pending"},
            {"id": "c-2", "status": "approved"},
            {"id": "c-3", "status": "rejected"},
        ]
        answers = {"/api/v1/team/approvals": leave, "/api/v1/compoff/team": claims}
        seen: list[str] = []

        class FakeClient:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def request(self, method, url, **__):
                seen.append(url)
                return httpx.Response(200, json=answers[url])

        monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)
        out = asyncio.run(server.pending_approvals(self.Ctx()))
        assert out == {"leave": leave, "compoff": [{"id": "c-1", "status": "pending"}]}
        assert sorted(seen) == sorted(answers)
