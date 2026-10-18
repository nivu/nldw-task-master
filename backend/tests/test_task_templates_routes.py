"""Starter templates through the routes — spec 008 FR-TASK-RRULE-05;
FR-TASK-ROLL-03; contracts §1 "Route order", §2 `POST /tasks/templates/{key}`,
`GET /tasks/settings`.

On the shared task world with phase 3 on for the cast (Tuesday 20 October
2026, noon IST). Each template adds one recurring task to the caller's own
My Tasks, only when chosen; the statutory dates wait for the owner's list
(spec §15.1).
"""

from __future__ import annotations

import pytest
from tasks_fake import ID, TaskWorld

from app.domain.tasks.recurrence import INVALID
from app.services.tasks.templates import NO_SUCH_TEMPLATE, STATUTORY_NOT_SET

NOT_ON = "Tasks are not switched on for you yet."


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 3 on for everyone who has phase 1."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 3
    return w


def _ok(r) -> dict:
    assert r.status_code in (200, 201), r.text
    return r.json()


class TestAddingATemplate:
    @pytest.mark.parametrize(
        ("key", "title", "rule", "due_on"),
        [
            (
                "invoice_follow_ups",
                "Follow up unpaid invoices",
                "FREQ=MONTHLY;BYMONTHDAY=5",
                "2026-11-05",
            ),
            ("pipeline_review", "Review the sales pipeline", "FREQ=WEEKLY;BYDAY=MO", "2026-10-26"),
            (
                "payroll_check",
                "Check this month's payroll",
                "FREQ=MONTHLY;BYMONTHDAY=25",
                "2026-10-25",
            ),
        ],
    )
    def test_each_template_creates_its_recurring_task(self, world, key, title, rule, due_on):
        r = world.call("user", "POST", f"/templates/{key}")
        assert r.status_code == 201, r.text
        out = r.json()
        assert (out["title"], out["recurrence"], out["due_on"]) == (title, rule, due_on)
        assert (out["kind"], out["status"], out["source"]) == ("private", "open", "manual")
        assert out["assignee"]["id"] == ID["user"]
        assert world.tasks[out["id"]]["series_id"] == out["id"]

    def test_it_starts_on_the_day_given(self, world):
        r = world.call(
            "user", "POST", "/templates/pipeline_review", json={"starts_on": "2026-11-02"}
        )
        assert _ok(r)["due_on"] == "2026-11-02"

    def test_the_statutory_dates_are_not_set_up_yet(self, world):
        before = len(world.tasks)
        r = world.call("user", "POST", "/templates/statutory_dates")
        assert (r.status_code, r.json()["detail"]) == (422, STATUTORY_NOT_SET)
        assert len(world.tasks) == before

    def test_a_start_with_no_first_date_is_refused(self, world):
        before = len(world.tasks)
        r = world.call(
            "user", "POST", "/templates/pipeline_review", json={"starts_on": "9999-12-31"}
        )
        assert (r.status_code, r.json()["detail"]) == (422, INVALID)
        assert len(world.tasks) == before

    def test_the_first_day_is_not_a_crash(self, world):
        r = world.call(
            "user", "POST", "/templates/invoice_follow_ups", json={"starts_on": "0001-01-01"}
        )
        assert _ok(r)["due_on"] == "0001-01-05"

    def test_an_unknown_template(self, world):
        r = world.call("user", "POST", "/templates/everything")
        assert (r.status_code, r.json()["detail"]) == (404, NO_SUCH_TEMPLATE)

    def test_nothing_else_in_the_body(self, world):
        r = world.call("user", "POST", "/templates/pipeline_review", json={"title": "Mine"})
        assert r.status_code == 422

    def test_completing_it_creates_the_next(self, world):
        out = _ok(world.call("user", "POST", "/templates/pipeline_review"))
        _ok(world.call("user", "POST", f"/{out['id']}/complete"))
        dues = sorted(
            str(t["due_on"]) for t in world.tasks.values() if t.get("series_id") == out["id"]
        )
        assert dues == ["2026-10-26", "2026-11-02"]

    def test_below_phase_3_it_is_refused(self, world):
        world.fake.profiles[ID["user"]]["tasks_level"] = 2
        r = world.call("user", "POST", "/templates/pipeline_review")
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ON)


class TestRouteOrder:
    """Contracts §1 — `/tasks/templates/{key}` is never read as `/tasks/{id}`."""

    def test_templates_reaches_its_own_handler(self, world):
        r = world.call("user", "POST", "/templates/complete")
        assert (r.status_code, r.json()["detail"]) == (404, NO_SUCH_TEMPLATE)


class TestSettingsListTheTemplates:
    def test_phase_3_lists_them_in_the_specs_order(self, world):
        out = _ok(world.call("user", "GET", "/settings"))
        assert [(t["key"], t["available"]) for t in out["templates"]] == [
            ("invoice_follow_ups", True),
            ("pipeline_review", True),
            ("statutory_dates", False),
            ("payroll_check", True),
        ]
        assert out["templates"][1]["repeats"] == "Every week on Monday"
        assert out["templates"][2]["repeats"] is None

    def test_a_change_keeps_them(self, world):
        out = _ok(world.call("user", "PATCH", "/settings", json={"nudge_days": 7}))
        assert out["nudge_days"] == 7 and len(out["templates"]) == 4

    def test_phase_2_does_not_list_them(self, world):
        world.fake.profiles[ID["user"]]["tasks_level"] = 2
        assert "templates" not in _ok(world.call("user", "GET", "/settings"))
