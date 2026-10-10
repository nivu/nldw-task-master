"""Fixing a day wrongly marked absent — spec 001 FR-BACK-08/10/11.

A lead's "mark absent" leaves an `unrecognised` row that every ordinary path
refuses to touch. These tests pin the three admin ways out: convert it into
the leave actually taken (allowance checked), let a backfill replace it in
place (allowance not checked), and remove it through undo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, current_user
from app.domain.approval import Person
from app.domain.ledger import Balance
from app.domain.rules import check_absence_conversion
from app.main import app
from app.services import audit, balances, settings_store
from app.services import bookings as booking_service
from app.services import compoff as compoff_service
from app.services import holidays as holiday_service
from app.services import supabase as db

TODAY = date(2026, 10, 9)  # a Friday
PAST = date(2026, 10, 1)  # a Thursday
SATURDAY = date(2026, 10, 3)

ADMIN = Person(id="u-admin", role="admin", lead_id=None)
LEAD = Person(id="u-lead", role="lead", lead_id=None)


def ok(result):
    return result is None


# ---------------------------------------------------------------------------
# The pure rule
# ---------------------------------------------------------------------------


class TestRule:
    @pytest.mark.parametrize("status", ["pending", "approved", "withdrawn", "rejected"])
    def test_only_unrecognised_converts(self, status):
        failure = check_absence_conversion(
            status, PAST, "wfh", None, holiday_name=None, today=TODAY
        )
        assert failure and "unrecognised" in failure

    @pytest.mark.parametrize(
        ("category", "reason"),
        [("wfh", None), ("casual", "Family"), ("sick", "Flu"), ("compoff", None)],
    )
    def test_every_category_converts(self, category, reason):
        assert ok(
            check_absence_conversion(
                "unrecognised", PAST, category, reason, holiday_name=None, today=TODAY
            )
        )

    @pytest.mark.parametrize("category", ["casual", "sick"])
    def test_casual_and_sick_need_a_reason(self, category):
        assert check_absence_conversion(
            "unrecognised", PAST, category, None, holiday_name=None, today=TODAY
        )

    def test_weekend_holiday_today_and_future_are_refused(self):
        for day, holiday in ((SATURDAY, None), (PAST, "Gandhi Jayanti"), (TODAY, None)):
            assert check_absence_conversion(
                "unrecognised", day, "wfh", None, holiday_name=holiday, today=TODAY
            )

    def test_unknown_category_is_refused(self):
        assert check_absence_conversion(
            "unrecognised", PAST, "holiday", None, holiday_name=None, today=TODAY
        )


# ---------------------------------------------------------------------------
# The service, against an in-memory store
# ---------------------------------------------------------------------------


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.consumed: list[tuple] = []
        self.released: list[str] = []
        self.remaining = Decimal("2")
        self.configured = True
        self.compoff = Decimal("0")
        self.lose_race = False

    def add(self, bid: str, **row) -> dict:
        self.rows[bid] = {
            "id": bid,
            "user_id": "u-mine",
            "date": PAST.isoformat(),
            "category": None,
            "duration": "1.0",
            "reason": None,
            "status": "unrecognised",
            "decision_note": "Not in",
            "created_by": "u-lead",
            "backfilled_by": None,
            "backfill_note": None,
            **row,
        }
        return self.rows[bid]

    def get_booking(self, bid):
        return dict(self.rows[bid]) if bid in self.rows else None

    def get_profile(self, uid):
        return {"id": uid, "display_name": "Mine", "role": "user", "lead_id": "u-lead"}

    def find_booking_on(self, user_id, day, statuses):
        for row in self.rows.values():
            if (
                row["user_id"] == user_id
                and row["date"] == day.isoformat()
                and row["status"] in statuses
            ):
                return dict(row)
        return None

    def insert_booking(self, data):
        row = {"id": f"b-{len(self.rows)}", **data}
        self.rows[row["id"]] = row
        return dict(row)

    def update_booking(self, bid, data):
        row = {**self.rows[bid], **data}
        # bookings_category_required (migrations 001, 024): only an
        # unrecognised or withdrawn row may have no category.
        if row["status"] not in ("unrecognised", "withdrawn") and row["category"] is None:
            raise AssertionError("violates bookings_category_required")
        self.rows[bid] = row
        return dict(row)

    def update_booking_if_status(self, bid, expected, data):
        if self.lose_race or self.rows[bid]["status"] != expected:
            return None
        return self.update_booking(bid, data)


@pytest.fixture
def store(monkeypatch) -> Store:
    s = Store()
    for name in (
        "get_booking",
        "get_profile",
        "find_booking_on",
        "insert_booking",
        "update_booking",
        "update_booking_if_status",
    ):
        monkeypatch.setattr(db, name, getattr(s, name))
    monkeypatch.setattr(booking_service, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(holiday_service, "holiday_on_for_user", lambda uid, day: None)
    monkeypatch.setattr(balances, "remaining_for", lambda *a, **k: s.remaining)

    def balances_for(user_id, period, **kw):
        return {
            category: Balance(
                category=category,
                period=period,
                opening=Decimal("0"),
                allowance=s.remaining,
                used=Decimal("0"),
                remaining=s.remaining,
                configured=s.configured,
            )
            for category in ("wfh", "casual", "sick")
        }

    monkeypatch.setattr(balances, "balances_for", balances_for)
    monkeypatch.setattr(settings_store, "allow_excess_booking", lambda: False)
    monkeypatch.setattr(audit, "record", lambda **kw: s.audit.append(kw))
    monkeypatch.setattr(compoff_service, "available", lambda uid, on=None: s.compoff)
    monkeypatch.setattr(
        compoff_service,
        "consume",
        lambda **kw: s.consumed.append((kw["booking_id"], kw["days"])),
    )
    monkeypatch.setattr(
        compoff_service, "release_for_booking", lambda bid: s.released.append(bid) or 1
    )

    def no_notify(*a):
        raise AssertionError("an admin correction sends no notification")

    monkeypatch.setattr(booking_service, "_notify", no_notify)
    return s


def convert(**overrides):
    args = {
        "booking_id": "b-absent",
        "category": "casual",
        "duration": Decimal("1.0"),
        "reason": "Family",
        "note": "Recorded after the fact",
        "actor": ADMIN,
    }
    return booking_service.convert_absence(**{**args, **overrides})


def backfill(**overrides):
    args = {
        "user_id": "u-mine",
        "day": PAST,
        "category": "wfh",
        "duration": Decimal("1.0"),
        "reason": None,
        "note": "Recorded after the fact",
        "actor": ADMIN,
    }
    return booking_service.backfill(**{**args, **overrides})


class TestConvert:
    def test_converts_the_same_row_in_place(self, store):
        store.add("b-absent")
        row = convert()
        assert row["id"] == "b-absent"
        assert row["status"] == "approved"
        assert row["category"] == "casual"
        assert row["backfilled_by"] == "u-admin"
        assert row["backfill_note"] == "Recorded after the fact"
        # The lead's absence note and authorship are left as they were.
        assert row["decision_note"] == "Not in"
        assert row["created_by"] == "u-lead"
        assert len(store.rows) == 1

    def test_audit_has_before_and_after(self, store):
        store.add("b-absent")
        convert(duration=Decimal("0.5"))
        (entry,) = store.audit
        assert entry["action"] == "booking.absence_converted"
        assert entry["before"] == {
            "status": "unrecognised",
            "category": None,
            "duration": "1.0",
            "date": PAST.isoformat(),
        }
        assert entry["after"] == {
            "status": "approved",
            "category": "casual",
            "duration": "0.5",
            "backfill_note": "Recorded after the fact",
        }

    def test_a_lead_is_refused(self, store):
        store.add("b-absent")
        with pytest.raises(booking_service.BookingRefused) as exc:
            convert(actor=LEAD)
        assert exc.value.status == 403
        assert store.rows["b-absent"]["status"] == "unrecognised"

    def test_a_note_is_required(self, store):
        store.add("b-absent")
        with pytest.raises(booking_service.BookingRefused):
            convert(note="  ")

    def test_an_approved_row_is_409(self, store):
        store.add("b-absent", status="approved", category="wfh")
        with pytest.raises(booking_service.BookingRefused) as exc:
            convert()
        assert exc.value.status == 409

    def test_a_lost_race_is_409(self, store):
        store.add("b-absent")
        store.lose_race = True
        with pytest.raises(booking_service.BookingRefused) as exc:
            convert()
        assert exc.value.status == 409
        assert "changed while you were converting" in exc.value.message
        assert store.audit == []

    def test_the_normal_balance_check_applies(self, store):
        store.add("b-absent")
        store.remaining = Decimal("0.5")
        with pytest.raises(booking_service.BookingRefused) as exc:
            convert()
        assert "Not enough casual leave" in exc.value.message
        assert store.rows["b-absent"]["status"] == "unrecognised"

    def test_an_unconfigured_category_is_refused(self, store):
        store.add("b-absent")
        store.configured = False
        with pytest.raises(booking_service.BookingRefused) as exc:
            convert()
        assert "has not been set up yet" in exc.value.message
        assert store.rows["b-absent"]["status"] == "unrecognised"

    def test_allow_excess_lets_it_through(self, store, monkeypatch):
        store.add("b-absent")
        store.remaining = Decimal("0")
        monkeypatch.setattr(settings_store, "allow_excess_booking", lambda: True)
        assert convert()["status"] == "approved"

    def test_compoff_without_credits_is_refused(self, store):
        store.add("b-absent")
        with pytest.raises(booking_service.BookingRefused) as exc:
            convert(category="compoff", reason=None)
        assert "comp-off" in exc.value.message
        assert store.consumed == []

    def test_compoff_consumes_credits(self, store):
        store.add("b-absent")
        store.compoff = Decimal("1")
        row = convert(category="compoff", reason=None)
        assert store.consumed == [("b-absent", Decimal("1.0"))]
        assert booking_service.balance_after(row) is None

    def test_balance_after_is_reported(self, store):
        store.add("b-absent")
        row = convert(category="wfh", reason=None)
        assert booking_service.balance_after(row) == "2"


class TestBackfillOverAnAbsence:
    def test_replaces_an_unrecognised_row_in_place(self, store):
        store.add("b-absent")
        row = backfill()
        assert row["replaced_absence"] is True
        assert row["id"] == "b-absent"
        assert store.rows["b-absent"]["status"] == "approved"
        assert len(store.rows) == 1
        assert store.audit[0]["action"] == "booking.absence_converted"

    def test_no_allowance_check_on_the_backfill_path(self, store):
        """FR-BACK-07 still holds when a backfill replaces an absence."""
        store.add("b-absent")
        store.remaining = Decimal("0")
        assert backfill()["status"] == "approved"

    @pytest.mark.parametrize("status", ["approved", "pending"])
    def test_a_real_booking_is_still_409(self, store, status):
        store.add("b-real", status=status, category="wfh")
        with pytest.raises(booking_service.BookingRefused) as exc:
            backfill()
        assert exc.value.status == 409

    def test_an_empty_day_inserts(self, store):
        row = backfill()
        assert row["replaced_absence"] is False
        assert row["status"] == "approved"

    def test_compoff_backfill_spends_credits(self, store):
        with pytest.raises(booking_service.BookingRefused):
            backfill(category="compoff")
        store.compoff = Decimal("1")
        row = backfill(category="compoff")
        assert store.consumed == [(row["id"], Decimal("1.0"))]


class TestUndo:
    def test_removes_an_unrecognised_row(self, store):
        store.add("b-absent")
        row = booking_service.undo_backfill(booking_id="b-absent", actor=ADMIN)
        assert row["status"] == "withdrawn"
        assert row["category"] is None
        assert store.rows["b-absent"]["category"] is None
        assert row["decided_by"] == "u-admin"
        (entry,) = store.audit
        assert entry["action"] == "booking.absence_cleared"
        assert entry["before"] == {"status": "unrecognised", "date": PAST.isoformat()}
        assert entry["after"] == {"status": "withdrawn"}

    def test_a_lead_cannot_remove_it(self, store):
        store.add("b-absent")
        with pytest.raises(booking_service.BookingRefused) as exc:
            booking_service.undo_backfill(booking_id="b-absent", actor=LEAD)
        assert exc.value.status == 403

    def test_a_booking_the_person_made_stays_locked(self, store):
        store.add("b-real", status="approved", category="wfh", created_by="u-mine")
        with pytest.raises(booking_service.BookingRefused) as exc:
            booking_service.undo_backfill(booking_id="b-real", actor=ADMIN)
        assert exc.value.status == 409
        assert store.rows["b-real"]["status"] == "approved"

    def test_undoing_a_compoff_backfill_returns_credits(self, store):
        store.add(
            "b-comp",
            status="approved",
            category="compoff",
            backfilled_by="u-admin",
            backfill_note="x",
        )
        booking_service.undo_backfill(booking_id="b-comp", actor=ADMIN)
        assert store.released == ["b-comp"]


# ---------------------------------------------------------------------------
# The route guard
# ---------------------------------------------------------------------------


@pytest.fixture
def as_role():
    def sign_in(role: str) -> TestClient:
        profile = {
            "id": f"u-{role}",
            "role": role,
            "lead_id": None,
            "is_active": True,
            "email": f"{role}@example.com",
            "display_name": role,
        }
        app.dependency_overrides[current_user] = lambda: CurrentUser(profile)
        return TestClient(app)

    yield sign_in
    app.dependency_overrides.pop(current_user, None)


class TestRoute:
    @pytest.mark.parametrize("role", ["user", "lead", "manager"])
    def test_only_an_admin_may_convert(self, as_role, role):
        r = as_role(role).post(
            "/api/v1/admin/absences/b-absent/convert",
            json={"category": "wfh", "duration": "1.0", "note": "x"},
        )
        assert r.status_code == 403

    def test_admin_converts_and_sees_the_balance(self, as_role, store):
        store.add("b-absent")
        r = as_role("admin").post(
            "/api/v1/admin/absences/b-absent/convert",
            json={"category": "wfh", "duration": "half", "note": "Recorded after the fact"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["duration"] == "0.5"
        assert body["balance_after"] == "2"

    def test_a_note_is_required_by_the_schema(self, as_role, store):
        store.add("b-absent")
        r = as_role("admin").post(
            "/api/v1/admin/absences/b-absent/convert",
            json={"category": "wfh", "duration": "1.0"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# The schema rule a removed absence relies on
# ---------------------------------------------------------------------------


def test_migration_024_lets_a_withdrawn_row_have_no_category():
    migration = (
        Path(__file__).resolve().parents[2]
        / "supabase/migrations/024_withdrawn_absence_category.sql"
    ).read_text()
    assert "DROP CONSTRAINT bookings_category_required" in migration
    assert "CHECK (status IN ('unrecognised', 'withdrawn') OR category IS NOT NULL)" in migration
