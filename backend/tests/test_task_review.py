"""The weekly review — spec 008 FR-TASK-REVIEW-01; TP-08; contracts §2
`GET /tasks/review`, `POST /tasks/review/dismiss`.

The rules as pure functions (`domain/tasks/review`), then through the routes
on the shared task world with phase 4 on, at a fixed clock: Monday 19
October 2026 and Tuesday 20 October 2026, noon IST.

Acceptance criteria (spec §12, phase 4):
- "The Monday review panel shows the listed items and can be dismissed" —
  TestThePanel, TestDismiss; never sent (TP-08) — TestNeverSent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tasks_fake import ID, TaskWorld

from app.domain.tasks import review, service

MONDAY = datetime(2026, 10, 19, 6, 30, tzinfo=UTC)
TUESDAY = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)
NOT_ON = "Tasks are not switched on for you yet."


class TestTheRules:
    def test_the_week_starts_on_monday(self):
        assert review.week_start(date(2026, 10, 19)) == date(2026, 10, 19)
        assert review.week_start(date(2026, 10, 25)) == date(2026, 10, 19)

    def test_shown_only_on_monday(self):
        assert review.show(date(2026, 10, 19), None) is True
        assert review.show(date(2026, 10, 20), None) is False
        assert review.show(date(2026, 10, 25), None) is False

    def test_dismissed_for_this_week_only(self):
        assert review.show(date(2026, 10, 19), "2026-10-19") is False
        assert review.show(date(2026, 10, 26), "2026-10-19") is True

    def test_waiting_over_seven_days(self):
        today = date(2026, 10, 19)
        rows = [
            {"id": "a", "status": "waiting", "created_at": "2026-10-11T06:30:00+00:00"},
            {"id": "b", "status": "waiting", "created_at": "2026-10-12T06:30:00+00:00"},
            {"id": "c", "status": "open", "created_at": "2026-09-01T06:30:00+00:00"},
            {"id": "d", "status": "waiting", "created_at": "2026-09-01T06:30:00+00:00"},
        ]
        found = review.waiting_over(rows, today, "Asia/Kolkata")
        assert [(r["id"], days) for r, days in found] == [("d", 48), ("a", 8)]

    def test_sections_use_the_effective_bucket(self):
        today = date(2026, 10, 19)
        rows = [
            {"id": "a", "status": "open", "bucket": "this_week", "due_on": None},
            {"id": "b", "status": "open", "bucket": "this_week", "due_on": "2026-10-19"},
            {"id": "c", "status": "done", "bucket": "this_week", "due_on": None},
            {"id": "d", "status": "waiting", "bucket": "this_month", "due_on": None},
        ]
        assert [r["id"] for r in review.in_section(rows, "this_week", today)] == ["a"]
        assert [r["id"] for r in review.in_section(rows, "this_month", today)] == ["d"]


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 4 on, on Monday 19 October 2026."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 4
    monkeypatch.setattr(service, "_now", lambda: MONDAY)
    return w


def _review(world, who: str = "member") -> dict:
    r = world.call(who, "GET", "/review")
    assert r.status_code == 200, r.text
    return r.json()


class TestThePanel:
    def test_the_panel_lists_the_items(self, world):
        old = world.add("member", title="Chase legal", status="waiting", waiting_on_text="Legal")
        recent = world.add("member", title="Chase bank", status="waiting", waiting_on_text="Bank")
        world.tasks[old["id"]]["created_at"] = "2026-10-05T06:30:00+00:00"
        world.tasks[recent["id"]]["created_at"] = "2026-10-12T06:30:00+00:00"
        week = world.add("member", title="Week", bucket="this_week")
        month = world.add("member", title="Month", bucket="this_month")
        world.add("member", title="Later", bucket="later")
        world.add("member", title="Due today", bucket="this_week", due_on="2026-10-19")
        world.add("creator", title="Not mine", bucket="this_week")
        item = world.call("member", "POST", "/inbox", json={"kind": "idea", "title": "Idea"})
        out = _review(world)
        assert (out["show"], out["week_start"]) == (True, "2026-10-19")
        assert [t["id"] for t in out["this_week"]] == [week["id"]]
        assert [t["id"] for t in out["this_month"]] == [month["id"]]
        assert [(t["id"], t["age_days"]) for t in out["waiting_over_7_days"]] == [(old["id"], 14)]
        assert recent["id"] not in str(out["waiting_over_7_days"])
        assert [i["id"] for i in out["inbox"]] == [item.json()["id"]]

    def test_not_shown_on_tuesday(self, world, monkeypatch):
        monkeypatch.setattr(service, "_now", lambda: TUESDAY)
        out = _review(world)
        assert (out["show"], out["week_start"]) == (False, "2026-10-19")

    def test_monday_is_the_callers_own(self, world, monkeypatch):
        # 23:30 IST on Sunday is already Monday in Sydney (Tamil's zone).
        monkeypatch.setattr(service, "_now", lambda: datetime(2026, 10, 18, 18, 0, tzinfo=UTC))
        assert _review(world, "member")["show"] is False
        assert _review(world, "seller")["show"] is True

    def test_one_tap_bucket_move(self, world):
        week = world.add("member", title="Week", bucket="this_week")
        r = world.call("member", "PATCH", f"/{week['id']}", json={"bucket": "this_month"})
        assert r.status_code == 200, r.text
        out = _review(world)
        assert (out["this_week"], [t["id"] for t in out["this_month"]]) == ([], [week["id"]])

    def test_an_inbox_item_moves_to_a_bucket_as_a_task(self, world):
        item = world.call("member", "POST", "/inbox", json={"kind": "idea", "title": "Idea"}).json()
        r = world.call(
            "member",
            "POST",
            f"/inbox/{item['id']}/make-task",
            json={"title": "Idea", "bucket": "this_week"},
        )
        assert r.status_code == 200, r.text
        out = _review(world)
        assert out["inbox"] == []
        assert [t["title"] for t in out["this_week"]] == ["Idea"]

    def test_a_link_item_moved_keeps_its_link_and_notes(self, world):
        item = world.call(
            "member",
            "POST",
            "/inbox",
            json={
                "kind": "link",
                "title": "Read pricing article",
                "url": "https://example.com/pricing",
                "notes": "compare with ours",
                "tags": ["pricing"],
            },
        ).json()
        # What the review panel's one-tap move sends.
        r = world.call(
            "member",
            "POST",
            f"/inbox/{item['id']}/make-task",
            json={"title": item["title"], "bucket": "this_week", "tags": item["tags"]},
        )
        assert r.status_code == 200, r.text
        [task] = _review(world)["this_week"]
        assert task["notes"] == "https://example.com/pricing\n\ncompare with ours"


class TestDismiss:
    def test_dismissed_for_the_week(self, world, monkeypatch):
        r = world.call("member", "POST", "/review/dismiss")
        assert (r.status_code, r.json()) == (200, {"dismissed_week": "2026-10-19"})
        assert world.fake.profiles[ID["member"]]["tasks_review_dismissed_week"] == "2026-10-19"
        assert _review(world)["show"] is False
        assert _review(world, "creator")["show"] is True
        monkeypatch.setattr(service, "_now", lambda: datetime(2026, 10, 26, 6, 30, tzinfo=UTC))
        assert _review(world)["show"] is True

    def test_dismissing_later_in_the_week_stores_its_monday(self, world, monkeypatch):
        monkeypatch.setattr(service, "_now", lambda: TUESDAY)
        r = world.call("member", "POST", "/review/dismiss")
        assert r.json() == {"dismissed_week": "2026-10-19"}


class TestNeverSent:
    """TP-08 — shown in the app, not sent."""

    def test_reading_and_dismissing_send_nothing(self, world):
        world.add("member", title="Week", bucket="this_week")
        world.messages.clear()
        _review(world)
        world.call("member", "POST", "/review/dismiss")
        assert world.messages == []

    def test_no_job_sends_the_review(self):
        from app.worker import celery_app

        # 008's jobs are the `tasks.*` namespace, and its only beat entries
        # are the purge and the voice sweep, so a new 008 job (a review
        # sender included) fails here. 009's `crm_ai.review_expiry` is the
        # CRM Review Queue's expiry, not the weekly review.
        beat = celery_app.conf.beat_schedule
        assert {k: v["task"] for k, v in beat.items() if v["task"].startswith("tasks.")} == {
            "tasks-purge-deleted": "tasks.purge_deleted",
            "tasks-voice-sweep": "tasks.voice_sweep",
        }
        assert not [
            k for k, v in beat.items() if "review" in k and not v["task"].startswith("crm_ai.")
        ]

    def test_level_3_is_refused(self, world):
        world.fake.profiles[ID["member"]]["tasks_level"] = 3
        r = world.call("member", "GET", "/review")
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ON)
