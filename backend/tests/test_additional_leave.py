"""Additional leave — spec 001 FR-BOOK-13.

A category with no allowance of its own, for leave taken once the person's
own has run out: booked for today it stands in for sick leave, booked ahead
it stands in for casual leave, and it is refused while that allowance can
still cover the day.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain import rules
from app.domain.ledger import Balance
from app.services import audit, balances, settings_store
from app.services import bookings as booking_service
from app.services import holidays as holiday_service
from app.services import supabase as db

TODAY = date(2026, 11, 5)  # a Thursday
TOMORROW = date(2026, 11, 6)  # a Friday
SATURDAY = date(2026, 11, 7)
FULL, HALF = Decimal("1.0"), Decimal("0.5")


class TestRule:
    def test_today_stands_in_for_sick_and_ahead_for_casual(self):
        assert rules.additional_stands_in_for(TODAY, TODAY) == "sick"
        assert rules.additional_stands_in_for(TOMORROW, TODAY) == "casual"

    @pytest.mark.parametrize(("remaining", "duration"), [("0", FULL), ("0.5", FULL), ("0", HALF)])
    def test_allowed_once_the_allowance_cannot_cover_the_day(self, remaining, duration):
        assert (
            rules.check_additional(
                TOMORROW, duration, fallback_remaining=Decimal(remaining), today=TODAY
            )
            is None
        )

    def test_refused_while_casual_is_left_for_a_future_day(self):
        failure = rules.check_additional(
            TOMORROW, FULL, fallback_remaining=Decimal("2"), today=TODAY
        )
        assert failure == (
            "You still have 2 days of casual leave left. "
            "Book casual leave instead; Additional leave is for when it is used up."
        )

    def test_refused_while_sick_is_left_for_today(self):
        failure = rules.check_additional(TODAY, HALF, fallback_remaining=FULL, today=TODAY)
        assert failure and failure.startswith("You still have 1 day of sick leave left.")

    def test_it_is_leave_needs_a_reason_and_has_no_allowance(self):
        assert rules.is_leave("additional")
        assert rules.CATEGORY_LABELS["additional"] == "Additional leave"
        assert rules.check_reason("additional", None) == "Additional leave needs a reason."
        assert "additional" not in rules.ALLOWANCE_CATEGORIES

    def test_bookable_today_and_ahead(self):
        for day in (TODAY, TOMORROW):
            assert rules.check_timing(day, "additional", today=TODAY, max_future_days=365) is None


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.remaining = {"wfh": Decimal("2"), "casual": Decimal("0"), "sick": Decimal("0")}
        self.excluded: list[tuple] = []

    def find_booking_on(self, user_id, day, statuses):
        for row in self.rows.values():
            if row["date"] == day.isoformat() and row["status"] in statuses:
                return dict(row)
        return None

    def insert_booking(self, data):
        row = {"id": f"b-{len(self.rows)}", **data}
        self.rows[row["id"]] = row
        return dict(row)

    def update_booking(self, bid, data):
        self.rows[bid] = {**self.rows[bid], **data}
        return dict(self.rows[bid])


@pytest.fixture
def store(monkeypatch) -> Store:
    s = Store()
    for name in ("find_booking_on", "insert_booking", "update_booking"):
        monkeypatch.setattr(db, name, getattr(s, name))
    monkeypatch.setattr(booking_service, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(holiday_service, "holiday_on_for_user", lambda uid, day: None)

    def balances_for(user_id, period, *, exclude_booking_duration=None, exclude_category=None):
        s.excluded.append((exclude_category, exclude_booking_duration))
        back = exclude_booking_duration or Decimal("0")
        return {
            c: Balance(
                category=c,
                period=period,
                opening=Decimal("0"),
                allowance=s.remaining[c],
                used=Decimal("0"),
                remaining=s.remaining[c] + (back if c == exclude_category else Decimal("0")),
                configured=True,
            )
            for c in ("wfh", "casual", "sick")
        }

    monkeypatch.setattr(balances, "balances_for", balances_for)
    monkeypatch.setattr(settings_store, "allow_excess_booking", lambda: False)
    monkeypatch.setattr(settings_store, "max_future_booking_days", lambda: 365)
    monkeypatch.setattr(audit, "record", lambda **kw: None)
    monkeypatch.setattr(booking_service, "_notify", lambda *a: None)
    return s


def _book(day, duration=FULL, reason="Family function"):
    return booking_service.create_or_replace(
        user_id="u-mine",
        day=day,
        category="additional",
        duration=duration,
        reason=reason,
        actor_id="u-mine",
    )


class TestBooking:
    def test_booked_ahead_once_casual_is_used_up(self, store):
        row = _book(TOMORROW)
        assert (row["category"], row["status"]) == ("additional", "pending")
        assert store.excluded == [("casual", None)]

    def test_booked_today_once_sick_is_used_up(self, store):
        store.remaining["casual"] = Decimal("3")  # casual cannot be taken today anyway
        row = _book(TODAY, reason="Fever")
        assert row["category"] == "additional"
        assert store.excluded == [("sick", None)]

    def test_refused_while_casual_is_left(self, store):
        store.remaining["casual"] = FULL
        with pytest.raises(booking_service.BookingRefused) as refused:
            _book(TOMORROW)
        assert "1 day of casual leave left" in refused.value.message
        assert not store.rows

    def test_switching_a_casual_day_counts_that_day_as_available(self, store):
        store.rows["b-x"] = {
            "id": "b-x",
            "user_id": "u-mine",
            "date": TOMORROW.isoformat(),
            "category": "casual",
            "duration": "1.0",
            "status": "pending",
        }
        with pytest.raises(booking_service.BookingRefused):
            _book(TOMORROW)
        assert store.excluded == [("casual", FULL)]

    def test_calendar_rules_come_before_the_used_up_check(self, store):
        store.remaining["casual"] = Decimal("3")
        with pytest.raises(booking_service.BookingRefused) as refused:
            _book(SATURDAY)
        assert "casual leave left" not in refused.value.message

    def test_a_reason_is_required(self, store):
        with pytest.raises(booking_service.BookingRefused) as refused:
            _book(TOMORROW, reason=" ")
        assert refused.value.message == "Additional leave needs a reason."
