"""The morning brief — spec 008 FR-TASK-BRIEF-01 to -03, FR-TASK-DEL-06,
FR-TASK-MCP-01 (`task_brief`), FR-TASK-VIS-04; Journey 4.8; TP-08; contracts
§2 `GET /tasks/brief`, §5 row 5.

At the shared fixed clock (Tuesday 20 October 2026, noon IST), with phase 2
on for the cast: the brief's Today and Overdue are the caller's own tasks;
"nudge?" holds the waiting tasks they created, untouched for their nudge
days; each workspace they belong to brings its CRM Today sections exactly as
`GET /crm/today` gives them; `task_brief` returns the same; nothing is sent.

Acceptance criteria (spec §12, phase 2):
- "a waiting task idle for the set days shows 'nudge?' in the brief" —
  TestNudge.
- "The brief shows the CRM Today sections exactly as CRM Today does, and is
  never pushed as a message" — TestCrmToday, TestNeverPushed.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, date, datetime, timedelta

import crm_fake
import pytest
from crm_fake import as_
from tasks_fake import ID, NOW, TaskWorld

from app.domain.tasks import brief as rules
from app.domain.tasks import service
from app.mcp import tasks as tools
from app.services.crm import today as crm_today
from app.worker import celery_app

NOT_ON = "Tasks are not switched on for you yet."
TODAY = date(2026, 10, 20)


class Ctx:
    def __init__(self, who: str) -> None:
        self.headers = {"authorization": f"Bearer {ID[who]}"}


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 2 on, and CRM Today on the same clock."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 2
    monkeypatch.setattr(crm_today, "_now", lambda: NOW)
    return w


def _brief(world: TaskWorld, who: str) -> dict:
    r = world.call(who, "GET", "/brief")
    assert r.status_code == 200, r.text
    return r.json()


def _titles(tasks: list[dict]) -> list[str]:
    return [t["title"] for t in tasks]


def _idle(world: TaskWorld, task: dict, days: int) -> None:
    """The task last changed `days` days before the fixed clock."""
    world.tasks[task["id"]]["updated_at"] = (NOW - timedelta(days=days)).isoformat()


def _waiting(world: TaskWorld, who: str, title: str, **body) -> dict:
    return world.add(who, title=title, status="waiting", waiting_on_text="Sanjeev", **body)


# ---------------------------------------------------------------------------
# FR-TASK-BRIEF-01 — Today and Overdue
# ---------------------------------------------------------------------------


class TestTodayAndOverdue:
    def test_today_and_overdue_are_the_callers_own(self, world):
        world.add("member", title="Due today", due_on="2026-10-20")
        world.add("member", title="Bucket today", bucket="today")
        world.add("member", title="Late", due_on="2026-10-17")
        world.add("member", title="Later", bucket="later")
        world.add("creator", title="Someone else's", due_on="2026-10-20")
        out = _brief(world, "member")
        assert out["as_of"] == "2026-10-20"
        assert _titles(out["today"]) == ["Due today", "Bucket today"]
        assert _titles(out["overdue"]) == ["Late"]
        assert out["overdue"][0]["days_overdue"] == 3

    def test_a_task_is_in_one_of_the_two_only(self):
        rows = [
            {"id": "a", "title": "a", "status": "open", "bucket": "today", "due_on": "2026-10-18"},
            {"id": "b", "title": "b", "status": "waiting", "bucket": "later", "due_on": None},
            {"id": "c", "title": "c", "status": "done", "bucket": "today", "due_on": None},
        ]
        on_today, overdue = rules.today_and_overdue(rows, TODAY)
        assert ([r["id"] for r in on_today], [r["id"] for r in overdue]) == ([], ["a"])

    def test_closed_tasks_are_left_out(self, world):
        task = world.add("member", title="Done", due_on="2026-10-20")
        world.call("member", "POST", f"/{task['id']}/complete")
        out = _brief(world, "member")
        assert out["today"] == [] and out["overdue"] == []

    def test_today_is_the_callers_own_day(self, world, monkeypatch):
        """Tamil is in Sydney: at 8 pm IST on 20 October it is already 1:30 am
        on the 21st there, so her Today is the 21st while Chennai's is still
        the 20th."""
        evening = datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
        monkeypatch.setattr(service, "_now", lambda: evening)
        world.add("seller", title="Sydney today", due_on="2026-10-21")
        world.add("seller", title="Yesterday there", due_on="2026-10-20")
        world.add("member", title="Chennai today", due_on="2026-10-20")
        out = _brief(world, "seller")
        assert out["as_of"] == "2026-10-21"
        assert _titles(out["today"]) == ["Sydney today"]
        assert _titles(out["overdue"]) == ["Yesterday there"]
        mine = _brief(world, "member")
        assert (mine["as_of"], _titles(mine["today"])) == ("2026-10-20", ["Chennai today"])


# ---------------------------------------------------------------------------
# FR-TASK-DEL-06 — "nudge?"
# ---------------------------------------------------------------------------


class TestNudge:
    def test_a_waiting_task_idle_for_the_default_five_days_is_a_nudge(self, world):
        task = _waiting(world, "creator", "Contract from Sanjeev")
        _idle(world, task, 5)
        [nudge] = _brief(world, "creator")["nudge"]
        assert (nudge["id"], nudge["idle_days"]) == (task["id"], 5)
        assert nudge["waiting_on"]["label"] == "Sanjeev"

    def test_four_idle_days_is_not_yet(self, world):
        task = _waiting(world, "creator", "Not yet")
        _idle(world, task, 4)
        assert _brief(world, "creator")["nudge"] == []

    def test_the_persons_own_nudge_days(self, world):
        task = _waiting(world, "creator", "Mine")
        _idle(world, task, 2)
        world.call("creator", "PATCH", "/settings", json={"nudge_days": 2})
        assert [t["id"] for t in _brief(world, "creator")["nudge"]] == [task["id"]]
        world.call("creator", "PATCH", "/settings", json={"nudge_days": 3})
        assert _brief(world, "creator")["nudge"] == []

    def test_a_change_resets_the_idle_days(self, world, monkeypatch):
        """The fake store stamps writes on the shared fixed clock, so only the
        PATCH itself can move `updated_at` to today."""
        ticks = itertools.count()
        monkeypatch.setattr(
            crm_fake, "_now", lambda: (NOW + timedelta(seconds=next(ticks))).isoformat()
        )
        task = _waiting(world, "creator", "Touched")
        _idle(world, task, 9)
        assert [t["id"] for t in _brief(world, "creator")["nudge"]] == [task["id"]]
        r = world.call("creator", "PATCH", f"/{task['id']}", json={"priority": "high"})
        assert r.status_code == 200, r.text
        assert _brief(world, "creator")["nudge"] == []

    def test_it_is_the_creators_nudge_whoever_is_assigned(self, world):
        given = _waiting(world, "creator", "Given", assignee_id=ID["assignee"])
        _idle(world, given, 6)
        assert [t["id"] for t in _brief(world, "creator")["nudge"]] == [given["id"]]
        assert _brief(world, "assignee")["nudge"] == []

    def test_the_person_waited_on_gets_nothing(self, world):
        task = _waiting(world, "creator", "Zqxwait")
        r = world.call("creator", "PATCH", f"/{task['id']}", json={"waiting_on_id": ID["waiting"]})
        assert r.status_code == 200, r.text
        _idle(world, task, 10)
        assert "zqxwait" not in world.call("waiting", "GET", "/brief").text.lower()

    def test_an_open_task_is_never_a_nudge(self, world):
        task = world.add("creator", title="Open")
        _idle(world, task, 20)
        assert _brief(world, "creator")["nudge"] == []

    def test_longest_idle_first(self):
        rows = [
            {
                "id": "a",
                "title": "a",
                "status": "waiting",
                "updated_at": "2026-10-14T06:30:00+00:00",
            },
            {
                "id": "b",
                "title": "b",
                "status": "waiting",
                "updated_at": "2026-10-10T06:30:00+00:00",
            },
            {
                "id": "c",
                "title": "c",
                "status": "waiting",
                "updated_at": "2026-10-19T06:30:00+00:00",
            },
        ]
        found = rules.nudges(rows, TODAY, "Asia/Kolkata", 5)
        assert [(r["id"], days) for r, days in found] == [("b", 10), ("a", 6)]

    def test_idle_days_are_counted_in_the_persons_day(self):
        """19 October 23:00 IST is already 20 October in Sydney."""
        task = {"updated_at": "2026-10-19T17:30:00+00:00"}
        assert rules.idle_days(task, TODAY, "Asia/Kolkata") == 1
        assert rules.idle_days(task, TODAY, "Australia/Sydney") == 0


# ---------------------------------------------------------------------------
# FR-TASK-BRIEF-01 — each workspace's CRM Today, unchanged
# ---------------------------------------------------------------------------


class TestCrmToday:
    def test_sections_equal_crm_todays_for_each_workspace(self, world):
        world.http.post(
            "/api/v1/crm/tasks",
            params={"workspace": "academy"},
            json={
                "title": "Call the college",
                "bucket": "today",
                "links": [world.link(world.college, "company")],
            },
            headers=as_(ID["owner"]),
        )
        world.add("owner", title="Deal task", links=[world.link(world.deal, "opportunity")])
        out = _brief(world, "owner")
        assert [w["workspace"] for w in out["workspaces"]] == [
            {"key": "academy", "name": "Nunnari Academy B2B"},
            {"key": "labs", "name": "Nunnari Labs"},
        ]
        for entry in out["workspaces"]:
            key = entry["workspace"]["key"]
            r = world.http.get(
                "/api/v1/crm/today", params={"workspace": key}, headers=as_(ID["owner"])
            )
            assert r.status_code == 200, r.text
            assert entry["sections"] == r.json()["sections"], key
        labs = next(w for w in out["workspaces"] if w["workspace"]["key"] == "labs")
        assert labs["sections"], "the Labs Today has its sections"

    def test_only_the_workspaces_the_caller_belongs_to(self, world):
        assert [w["workspace"]["key"] for w in _brief(world, "member")["workspaces"]] == ["labs"]
        assert _brief(world, "outsider")["workspaces"] == []

    def test_a_removed_member_gets_no_workspace(self, world):
        del world.fake.rows["crm_memberships"][world.removed["id"]]
        assert _brief(world, "removed")["workspaces"] == []

    def test_a_crm_task_is_in_the_workspace_today_and_the_task_lists(self, world):
        task = world.add(
            "member",
            title="Deal call",
            due_on="2026-10-20",
            links=[world.link(world.deal, "opportunity")],
        )
        out = _brief(world, "member")
        assert [t["id"] for t in out["today"]] == [task["id"]]
        [labs] = out["workspaces"]
        assert "Deal call" in str(labs["sections"])


# ---------------------------------------------------------------------------
# FR-TASK-BRIEF-02, -03 — the tool, never pushed, the switch, the route
# ---------------------------------------------------------------------------


class TestTheTool:
    def test_task_brief_returns_the_same(self, world):
        late = world.add("owner", title="Late", due_on="2026-10-16")
        wait = _waiting(world, "owner", "Waiting")
        _idle(world, wait, 7)
        tool = asyncio.run(tools.task_brief(Ctx("owner")))
        assert tool == _brief(world, "owner")
        assert [t["id"] for t in tool["overdue"]] == [late["id"]]
        assert [t["id"] for t in tool["nudge"]] == [wait["id"]]


class TestNeverPushed:
    def test_reading_it_sends_nothing(self, world):
        wait = _waiting(world, "creator", "Waiting")
        _idle(world, wait, 8)
        _brief(world, "creator")
        assert world.messages == []

    def test_no_job_sends_a_brief(self):
        jobs = celery_app.conf.beat_schedule
        assert not [k for k, v in jobs.items() if "brief" in k or "brief" in v["task"]]


class TestTheRoute:
    def test_level_1_is_refused(self, world):
        world.fake.profiles[ID["member"]]["tasks_level"] = 1
        r = world.call("member", "GET", "/brief")
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ON)

    def test_level_0_is_refused(self, world):
        r = world.call("off", "GET", "/brief")
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ON)

    def test_brief_reaches_its_own_handler_not_the_task_route(self, world):
        r = world.call("member", "GET", "/brief")
        assert r.status_code == 200
        assert set(r.json()) == {"as_of", "today", "overdue", "nudge", "workspaces"}
