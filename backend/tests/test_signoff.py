"""Weekly sign-off — spec 006 FR-SIGN-02 and FR-SIGN-03.

The same question decides both: has the whole week's edit window closed? A
reopen is allowed only before, an auto-confirm happens only after. Getting the
boundary wrong either strands a week nobody can fix or confirms one a lead is
still looking at.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.approval import Person
from app.domain.timesheets import week_closed
from app.services import confirmations

MONDAY = date(2026, 9, 21)
SUNDAY = MONDAY + timedelta(days=6)
GRACE = 7
#: Sunday + grace is the last editable day; the day after, the week is closed.
LAST_OPEN = SUNDAY + timedelta(days=GRACE)
FIRST_CLOSED = LAST_OPEN + timedelta(days=1)


class TestWeekClosed:
    def test_open_during_the_week(self):
        assert week_closed(MONDAY, MONDAY + timedelta(days=3), grace_days=GRACE) is False

    def test_open_on_the_last_grace_day(self):
        assert week_closed(MONDAY, LAST_OPEN, grace_days=GRACE) is False

    def test_closed_the_day_after(self):
        assert week_closed(MONDAY, FIRST_CLOSED, grace_days=GRACE) is True

    def test_follows_the_sunday_not_the_monday(self):
        """Monday's own entries lock six days before the week as a whole does."""
        assert week_closed(MONDAY, FIRST_CLOSED - timedelta(days=1), grace_days=GRACE) is False

    def test_grace_setting_moves_the_boundary(self):
        assert week_closed(MONDAY, SUNDAY + timedelta(days=3), grace_days=2) is True
        assert week_closed(MONDAY, SUNDAY + timedelta(days=2), grace_days=2) is False


LEAD = Person(id="lead", role="lead", lead_id=None, is_active=True)
REPORT = {"id": "p1", "role": "employee", "lead_id": "lead", "is_active": True}


@pytest.fixture
def service(monkeypatch):
    """The service with its database, audit, clock and dispatch replaced."""
    state = {"confirmed": {}, "deleted": [], "notified": [], "today": LAST_OPEN}

    monkeypatch.setattr(confirmations, "_grace_days", lambda: GRACE)
    monkeypatch.setattr(confirmations.settings_store, "portal_start_date", lambda: None)
    monkeypatch.setattr(confirmations, "today_in_company_tz", lambda: state["today"])
    monkeypatch.setattr(confirmations.audit, "record", lambda **_: None)
    monkeypatch.setattr(confirmations.db, "get_profile", lambda uid: REPORT)
    monkeypatch.setattr(confirmations.db, "list_profiles", lambda active_only: [REPORT])
    monkeypatch.setattr(
        confirmations.db,
        "list_confirmations",
        lambda user_ids, week_start: [
            row
            for (uid, week), row in state["confirmed"].items()
            if uid in user_ids and week == week_start.isoformat()
        ],
    )

    def upsert(row):
        state["confirmed"][(row["user_id"], row["week_start"])] = {**row, "id": "c1"}
        return state["confirmed"][(row["user_id"], row["week_start"])]

    def delete(user_id, week_start):
        state["deleted"].append((user_id, week_start))
        state["confirmed"].pop((user_id, week_start.isoformat()), None)

    monkeypatch.setattr(confirmations.db, "upsert_confirmation", upsert)
    monkeypatch.setattr(confirmations.db, "delete_confirmation", delete)
    monkeypatch.setattr(
        confirmations,
        "_notify_reopened",
        lambda user_id, week_start, actor_id: state["notified"].append((user_id, week_start)),
    )
    return state


class TestReopen:
    def test_reopens_and_tells_the_person(self, service):
        service["confirmed"][("p1", MONDAY.isoformat())] = {"user_id": "p1"}
        confirmations.reopen(user_id="p1", week_start=MONDAY, actor=LEAD)
        assert service["deleted"] == [("p1", MONDAY)]
        assert service["notified"] == [("p1", MONDAY)]

    def test_refused_once_the_week_has_closed(self, service):
        service["today"] = FIRST_CLOSED
        service["confirmed"][("p1", MONDAY.isoformat())] = {"user_id": "p1"}
        with pytest.raises(confirmations.SignoffRefused) as exc:
            confirmations.reopen(user_id="p1", week_start=MONDAY, actor=LEAD)
        assert exc.value.status == 409
        assert service["deleted"] == []
        assert service["notified"] == []

    def test_nothing_to_tell_when_the_week_was_not_confirmed(self, service):
        confirmations.reopen(user_id="p1", week_start=MONDAY, actor=LEAD)
        assert service["notified"] == []


class TestAutoConfirm:
    def test_confirms_a_closed_week_nobody_signed(self, service):
        assert confirmations.auto_confirm_closed(FIRST_CLOSED) >= 1
        row = service["confirmed"][("p1", MONDAY.isoformat())]
        assert row["status"] == "auto"

    def test_leaves_a_week_that_is_still_editable(self, service):
        confirmations.auto_confirm_closed(LAST_OPEN)
        assert ("p1", MONDAY.isoformat()) not in service["confirmed"]
        earlier = (MONDAY - timedelta(weeks=1)).isoformat()
        assert service["confirmed"][("p1", earlier)]["status"] == "auto"

    def test_skips_a_week_already_confirmed(self, service):
        service["confirmed"][("p1", MONDAY.isoformat())] = {"user_id": "p1", "status": "confirmed"}
        confirmations.auto_confirm_closed(FIRST_CLOSED)
        assert service["confirmed"][("p1", MONDAY.isoformat())]["status"] == "confirmed"

    def test_idempotent(self, service):
        first = confirmations.auto_confirm_closed(FIRST_CLOSED)
        assert first > 0
        assert confirmations.auto_confirm_closed(FIRST_CLOSED) == 0


class TestReopenNotification:
    def test_tells_the_person_who_reopened_it_and_until_when(self, monkeypatch):
        from app.tasks import notifications

        profiles = {
            "p1": {"id": "p1", "email": "p1@example.com", "display_name": "Asha"},
            "lead": {"id": "lead", "email": "l@example.com", "display_name": "Ravi"},
        }
        sent = []
        monkeypatch.setattr(notifications.db, "get_profile", profiles.get)
        monkeypatch.setattr(notifications, "_grace_days", lambda: GRACE)
        monkeypatch.setattr(
            notifications.notify, "deliver", lambda message: sent.append(message) or ["email"]
        )

        result = notifications.week_reopened.run("p1", MONDAY.isoformat(), "lead")

        assert result == {"delivered": ["email"]}
        (message,) = sent
        assert message.recipient_email == "p1@example.com"
        assert "Ravi reopened" in message.body
        assert LAST_OPEN.strftime("%a %d %b %Y") in message.body
