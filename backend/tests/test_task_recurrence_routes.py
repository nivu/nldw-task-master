"""Recurring tasks through the routes — spec 008 FR-TASK-RRULE-01 to -04;
TP-18; FR-TASK-ROLL-03; data-model §2.8; contracts §0.1, §1.1.

On the shared task world with phase 3 on for the cast (Tuesday 20 October
2026, noon IST). Completing or cancelling a recurring task creates exactly
one next occurrence, with the same fields and links, the next due date, the
bucket of FR-TASK-DATA-08 and status Open; reopening and completing again
creates none; a recurring task needs a due date; removing the recurrence
ends the series.

Acceptance criteria (spec §12, phase 3):
- "Completing or cancelling a recurring task creates exactly one next
  occurrence with the right due date and bucket" — TestNextOccurrence.
"""

from __future__ import annotations

import pytest
from crm_fake import as_
from tasks_fake import ID, TaskWorld

from app.domain.tasks.recurrence import INVALID, NEEDS_DUE
from app.services.tasks.store import SERIES_DATE_TAKEN, SERIES_TAKEN

NOT_ON = "Tasks are not switched on for you yet."
WEEKLY = "FREQ=WEEKLY;BYDAY=MO,TH"


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


def _recurring(world: TaskWorld, who: str = "owner", rule: str = WEEKLY, **body) -> dict:
    """A task due Tuesday 20 October 2026, then made recurring."""
    task = world.add(who, **{"due_on": "2026-10-20", **body})
    return _ok(world.call(who, "PATCH", f"/{task['id']}", json={"recurrence": rule}))


def _series(world: TaskWorld, task: dict) -> list[dict]:
    """Every live task of the task's series, by due date."""
    series = world.tasks[task["id"]]["series_id"]
    return sorted(
        (t for t in world.tasks.values() if t.get("series_id") == series and not t["deleted_at"]),
        key=lambda t: str(t["due_on"]),
    )


# ---------------------------------------------------------------------------
# FR-TASK-RRULE-01 — a recurrence, picked, with a due date
# ---------------------------------------------------------------------------


class TestSettingARecurrence:
    def test_the_task_carries_it_and_names_its_series(self, world):
        task = _recurring(world)
        assert task["recurrence"] == WEEKLY
        row = world.tasks[task["id"]]
        assert (row["series_id"], row.get("recurrence_spawned")) == (task["id"], None)

    def test_it_is_written_one_way(self, world):
        task = _recurring(world, rule="byday=th,mo;freq=weekly")
        assert task["recurrence"] == WEEKLY

    def test_only_the_four_forms(self, world):
        task = world.add("owner", due_on="2026-10-20")
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"recurrence": "FREQ=HOURLY"})
        assert (r.status_code, r.json()["detail"]) == (422, INVALID)

    def test_a_due_date_is_required(self, world):
        task = world.add("owner")
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"recurrence": WEEKLY})
        assert (r.status_code, r.json()["detail"]) == (422, NEEDS_DUE)
        assert world.tasks[task["id"]].get("recurrence") is None

    def test_the_due_date_cannot_be_removed_while_it_recurs(self, world):
        task = _recurring(world)
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"due_on": None})
        assert (r.status_code, r.json()["detail"]) == (422, NEEDS_DUE)

    def test_a_recurrence_and_a_due_date_together(self, world):
        task = world.add("owner")
        body = {"recurrence": WEEKLY, "due_on": "2026-10-22"}
        out = _ok(world.call("owner", "PATCH", f"/{task['id']}", json=body))
        assert (out["recurrence"], out["due_on"]) == (WEEKLY, "2026-10-22")

    def test_below_phase_3_it_is_refused(self, world):
        world.fake.profiles[ID["owner"]]["tasks_level"] = 2
        task = world.add("owner", due_on="2026-10-20")
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"recurrence": WEEKLY})
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ON)

    def test_a_due_date_with_no_next_date_is_refused(self, world):
        task = world.add("owner", due_on="9999-06-01")
        body = {"recurrence": "FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1"}
        r = world.call("owner", "PATCH", f"/{task['id']}", json=body)
        assert (r.status_code, r.json()["detail"]) == (422, INVALID)
        task = _recurring(world)
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"due_on": "9999-12-31"})
        assert (r.status_code, r.json()["detail"]) == (422, INVALID)

    def test_task_out_carries_it(self, world):
        task = _recurring(world)
        assert _ok(world.call("owner", "GET", f"/{task['id']}"))["recurrence"] == WEEKLY
        assert world.add("owner")["recurrence"] is None


# ---------------------------------------------------------------------------
# FR-TASK-RRULE-02, -03; TP-18 — exactly one next occurrence
# ---------------------------------------------------------------------------


class TestNextOccurrence:
    def test_completing_creates_one_with_the_next_due_date_and_bucket(self, world):
        task = _recurring(world)
        done = _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        assert done["status"] == "done"
        first, nxt = _series(world, task)
        assert first["id"] == task["id"] and first["recurrence_spawned"] is True
        assert (nxt["status"], str(nxt["due_on"]), nxt["bucket"]) == ("open", "2026-10-22", "later")
        assert (nxt["recurrence"], nxt["completed_at"]) == (WEEKLY, None)

    def test_an_overdue_occurrence_comes_back_in_today(self, world):
        task = _recurring(world, rule="FREQ=DAILY", due_on="2026-10-18")
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        assert (str(nxt["due_on"]), nxt["bucket"]) == ("2026-10-19", "today")

    def test_cancelling_creates_one_too(self, world):
        task = _recurring(world)
        out = _ok(world.call("owner", "PATCH", f"/{task['id']}", json={"status": "cancelled"}))
        assert out["status"] == "cancelled"
        assert [str(t["due_on"]) for t in _series(world, task)] == ["2026-10-20", "2026-10-22"]

    def test_done_through_a_change_creates_one(self, world):
        task = _recurring(world)
        _ok(world.call("owner", "PATCH", f"/{task['id']}", json={"status": "done"}))
        assert len(_series(world, task)) == 2

    def test_reopening_and_completing_again_creates_none(self, world):
        task = _recurring(world)
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        _ok(world.call("owner", "POST", f"/{task['id']}/reopen"))
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        _ok(world.call("owner", "POST", f"/{task['id']}/reopen"))
        _ok(world.call("owner", "PATCH", f"/{task['id']}", json={"status": "cancelled"}))
        assert len(_series(world, task)) == 2

    def test_completing_a_done_task_again_creates_none(self, world):
        task = _recurring(world)
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        assert len(_series(world, task)) == 2

    def test_the_next_occurrence_recurs_in_turn(self, world):
        task = _recurring(world)
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        _ok(world.call("owner", "POST", f"/{nxt['id']}/complete"))
        assert [str(t["due_on"]) for t in _series(world, task)] == [
            "2026-10-20",
            "2026-10-22",
            "2026-10-26",
        ]

    def test_one_write_closes_it_and_creates_the_next(self, world):
        task = _recurring(world)
        before = len(world.fake.tasks.applied)
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        assert len(world.fake.tasks.applied) == before + 1
        ops = world.fake.tasks.applied[-1][1]
        assert [(o["op"], o["table"]) for o in ops] == [("update", "tasks"), ("insert", "tasks")]

    def test_a_second_next_occurrence_is_refused(self, world):
        task = _recurring(world)
        world.fake.tasks.add_task(
            title="Call Deepak",
            assignee_id=ID["owner"],
            created_by=ID["owner"],
            due_on="2026-10-22",
            recurrence=WEEKLY,
            series_id=task["id"],
        )
        r = world.call("owner", "POST", f"/{task['id']}/complete")
        assert (r.status_code, r.json()["detail"]) == (409, SERIES_TAKEN)
        assert world.tasks[task["id"]]["status"] == "open"

    def test_moving_an_occurrence_onto_anothers_date_says_so(self, world):
        task = _recurring(world, rule="FREQ=DAILY", due_on="2026-10-18")
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        _ok(world.call("owner", "POST", f"/{task['id']}/reopen"))
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"due_on": "2026-10-19"})
        assert (r.status_code, r.json()["detail"]) == (409, SERIES_DATE_TAKEN)
        nxt = _series(world, task)[-1]
        r = world.call("owner", "PATCH", f"/{nxt['id']}", json={"due_on": "2026-10-18"})
        assert (r.status_code, r.json()["detail"]) == (409, SERIES_DATE_TAKEN)

    def test_restoring_onto_a_taken_date_says_so(self, world):
        task = _recurring(world, rule="FREQ=DAILY", due_on="2026-10-18")
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        _ok(world.call("owner", "DELETE", f"/{nxt['id']}"))
        world.fake.tasks.add_task(
            title="Call Deepak",
            assignee_id=ID["owner"],
            created_by=ID["owner"],
            due_on="2026-10-19",
            recurrence="FREQ=DAILY",
            series_id=task["id"],
        )
        r = world.call("owner", "POST", f"/{nxt['id']}/restore")
        assert (r.status_code, r.json()["detail"]) == (409, SERIES_DATE_TAKEN)

    def test_a_task_that_does_not_recur_creates_none(self, world):
        task = world.add("owner", due_on="2026-10-20")
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        assert [t["id"] for t in world.tasks.values() if t["title"] == "Call Deepak"] == [
            task["id"]
        ]


class TestSameFieldsAndLinks:
    """FR-TASK-RRULE-02 — the next occurrence copies the fields and links."""

    def test_a_crm_task_keeps_its_fields_links_and_workspace(self, world):
        task = _recurring(
            world,
            title="Chase the PO",
            notes="Ask for the number",
            priority="high",
            tags=["sales", "po"],
            assignee_id="member",
            links=[world.link(world.deal, "opportunity"), world.link(world.acme, "company")],
        )
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        row = world.tasks[task["id"]]
        for name in ("workspace_id", "title", "body", "priority", "tags", "assignee_id"):
            assert nxt[name] == row[name], name
        out = _ok(world.call("owner", "GET", f"/{nxt['id']}"))
        assert out["kind"] == "crm"
        assert sorted((lk["record_type"], lk["record_id"]) for lk in out["links"]) == sorted(
            (lk["record_type"], lk["record_id"]) for lk in task["links"]
        )

    def test_a_project_task_keeps_its_project(self, world):
        task = _recurring(
            world, "member", links=[world.link(world.lumen, "tva_project")], assignee_id="member"
        )
        _ok(world.call("member", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        out = _ok(world.call("member", "GET", f"/{nxt['id']}"))
        assert (out["kind"], out["project"]) == (
            "project",
            {"id": world.lumen["id"], "name": "Lumen"},
        )

    def test_a_shared_task_keeps_its_creator_and_who_sees_it(self, world):
        task = _recurring(world, "creator", assignee_id="assignee")
        _ok(world.call("assignee", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        assert (nxt["created_by"], nxt["assignee_id"]) == (ID["creator"], ID["assignee"])
        out = _ok(world.call("creator", "GET", f"/{nxt['id']}"))
        assert out["kind"] == "shared_personal"
        assert out["new"] is False

    def test_a_subtask_keeps_its_parent(self, world):
        parent = world.add("owner", title="Quarter close")
        task = _recurring(world, parent_id=parent["id"])
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        assert _series(world, task)[-1]["parent_id"] == parent["id"]

    def test_the_next_occurrence_is_open_with_nothing_awaited(self, world):
        task = _recurring(world, status="waiting", waiting_on_text="Finance")
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        assert (nxt["status"], nxt["waiting_on_text"]) == ("open", None)


# ---------------------------------------------------------------------------
# FR-TASK-RRULE-04 — removing the recurrence ends the series
# ---------------------------------------------------------------------------


class TestEndingTheSeries:
    def test_removing_it_ends_the_series(self, world):
        task = _recurring(world)
        out = _ok(world.call("owner", "PATCH", f"/{task['id']}", json={"recurrence": None}))
        assert out["recurrence"] is None
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        assert len(_series(world, task)) == 1

    def test_removing_it_from_a_later_occurrence_ends_it_there(self, world):
        task = _recurring(world)
        _ok(world.call("owner", "POST", f"/{task['id']}/complete"))
        nxt = _series(world, task)[-1]
        _ok(world.call("owner", "PATCH", f"/{nxt['id']}", json={"recurrence": None}))
        _ok(world.call("owner", "POST", f"/{nxt['id']}/complete"))
        assert len(_series(world, task)) == 2


# ---------------------------------------------------------------------------
# FR-TASK-RRULE-01, -02; TP-18 — through the CRM's own task writes
# ---------------------------------------------------------------------------


class TestThroughTheCrm:
    """A CRM task closed on CRM Today, a record page, `crm_complete_task`,
    `crm_update` or a bulk change creates its next occurrence as My Tasks
    does, and keeps its due date."""

    def _crm(self, world: TaskWorld, method: str, path: str, **kwargs):
        return world.http.request(
            method,
            f"/api/v1/crm/tasks/{path}?workspace=labs",
            headers=as_(ID["member"]),
            **kwargs,
        )

    def _crm_task(self, world: TaskWorld) -> dict:
        return _recurring(
            world,
            due_on="2026-10-22",
            assignee_id="member",
            links=[world.link(world.deal, "opportunity"), world.link(world.lumen, "tva_project")],
        )

    def _check_next(self, world: TaskWorld, task: dict) -> None:
        first, nxt = _series(world, task)
        assert first["recurrence_spawned"] is True
        assert (nxt["status"], str(nxt["due_on"]), nxt["created_by"]) == (
            "open",
            "2026-10-26",
            ID["owner"],
        )
        assert nxt["workspace_id"] == first["workspace_id"]
        out = _ok(world.call("owner", "GET", f"/{nxt['id']}"))
        assert sorted((lk["record_type"], lk["record_id"]) for lk in out["links"]) == sorted(
            (lk["record_type"], lk["record_id"]) for lk in task["links"]
        )

    def test_completing_on_the_crm_creates_one(self, world):
        task = self._crm_task(world)
        r = self._crm(world, "POST", f"{task['id']}/complete")
        assert _ok(r)["status"] == "done"
        self._check_next(world, task)
        _ok(world.call("owner", "POST", f"/{task['id']}/reopen"))
        _ok(self._crm(world, "POST", f"{task['id']}/complete"))
        assert len(_series(world, task)) == 2

    def test_cancelling_on_the_crm_creates_one(self, world):
        task = self._crm_task(world)
        r = self._crm(world, "PATCH", task["id"], json={"status": "cancelled"})
        assert _ok(r)["status"] == "cancelled"
        self._check_next(world, task)

    def test_the_crm_cannot_remove_its_due_date(self, world):
        task = self._crm_task(world)
        r = self._crm(world, "PATCH", task["id"], json={"due_on": None})
        assert (r.status_code, r.json()["detail"]) == (422, NEEDS_DUE)
        assert str(world.tasks[task["id"]]["due_on"]) == "2026-10-22"
