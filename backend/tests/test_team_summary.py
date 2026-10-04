"""The Team page's day summary — FR-LEAD-01/02/03."""

from __future__ import annotations

from app.domain import dashboard as dash
from app.domain import team
from app.domain.rules import CATEGORIES


def _entry(state: str, category: str | None = None) -> dict:
    return {"state": state, "category": category}


class TestSummarise:
    def test_empty_has_every_category(self):
        assert team.summarise([]) == {
            "present": 0,
            "wfh": 0,
            "casual": 0,
            "sick": 0,
            "compoff": 0,
            "unrecognised": 0,
        }

    def test_compoff_is_counted(self):
        # The day someone took comp-off used to fail the whole page.
        out = team.summarise([_entry("approved", "compoff"), _entry("present")])
        assert out["compoff"] == 1
        assert out["present"] == 1

    def test_every_category_is_counted(self):
        entries = [_entry("approved", c) for c in CATEGORIES]
        entries += [_entry("pending", "casual"), _entry("unrecognised"), _entry("present")]
        assert team.summarise(entries) == {
            "present": 1,
            "wfh": 1,
            "casual": 2,
            "sick": 1,
            "compoff": 1,
            "unrecognised": 1,
        }

    def test_unknown_category_cannot_crash(self):
        out = team.summarise([_entry("approved", "sabbatical")])
        assert out["sabbatical"] == 1

    def test_agrees_with_the_dashboard(self):
        # FR-DASH — the dashboard's day status follows the Team page's rule.
        bookings = [
            {"user_id": "a", "status": "approved", "category": "wfh"},
            {"user_id": "b", "status": "pending", "category": "casual"},
            {"user_id": "c", "status": "approved", "category": "sick"},
            {"user_id": "d", "status": "approved", "category": "compoff"},
            {"user_id": "e", "status": "unrecognised", "category": None},
        ]
        ids = ["a", "b", "c", "d", "e", "f"]
        by_user = {b["user_id"]: b for b in bookings}
        entries = [
            _entry("present")
            if uid not in by_user
            else _entry(by_user[uid]["status"], by_user[uid]["category"])
            for uid in ids
        ]
        summary = team.summarise(entries)
        status = dash.day_status(ids, bookings)
        assert summary["present"] == status["present"]
        assert summary["wfh"] == status["wfh"]
        assert summary["unrecognised"] == status["unrecognised"]
        assert {c: summary[c] for c in status["leave"]} == status["leave"]
