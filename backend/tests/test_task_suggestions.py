"""Day-log suggestions — spec 008 FR-TASK-LOG-01 to -03, -05, -07; G-6;
Journey 4.9; TP-09, TP-19; TQ-16; plan §5.9; contracts §3 `GET
/timesheet/day`.

At the shared fixed clock (Tuesday 20 October 2026, noon IST), with phase 3
on for the cast: Log a day's read carries the tasks the person finished that
day in Asia/Kolkata, never imported ones, each with its project only when it
is confirmed and unarchived; none for `crm_only`, below level 3, or on a
future or locked day; only the person's own day read carries them; nothing
is stored; a title already in a note that day shows as added. Accept, Ignore
and the merge are client state, proven by `frontend/e2e/day-log.spec.ts`.

Acceptance criteria (spec §12, phase 3):
- "Tasks completed today appear as suggestions on Log a day for staff with
  phase 3 switched on; none for `crm_only`, none on a locked or future day"
  — TestWhichTasks, TestWhenOffered.
- "Suggestions never appear in someone else's week view; imported tasks are
  never suggested; a suggestion whose title is already in that day's note
  shows as added; nothing about accepting or ignoring is stored" —
  TestOnlyTheOwnDay, TestWhichTasks, TestAlreadyAdded, TestNothingStored.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from crm_fake import as_
from tasks_fake import ID, TaskWorld

from app.api import timesheet as timesheet_api
from app.domain.tasks import suggestions as rules
from app.services import holidays, timesheets
from app.services import supabase as db
from app.services.tasks import suggestions as service

TODAY = date(2026, 10, 20)
APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 3 on, and the day log's reads on the same
    clock: no entries yet, Lumen allocated to Deepika."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 3
    w.entries = []
    monkeypatch.setattr(db, "list_time_entries", lambda **_: [dict(e) for e in w.entries])
    monkeypatch.setattr(
        db,
        "list_counting_allocations",
        lambda **_: [
            {"project_id": w.lumen["id"], "starts_on": "2026-10-01", "ends_on": "2026-12-31"}
        ],
    )
    monkeypatch.setattr(
        db, "list_projects", lambda **_: [dict(p) for p in w.fake.rows["projects"].values()]
    )
    monkeypatch.setattr(db, "find_booking_on", lambda *_: None)
    monkeypatch.setattr(timesheets, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(timesheet_api, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(timesheets, "_grace_days", lambda: 7)
    monkeypatch.setattr(timesheets, "_max_hours", lambda: "16")
    return w


def _done(w: TaskWorld, who: str, title: str, at: str, **values) -> dict:
    return w.fake.tasks.add_task(
        **{
            "title": title,
            "created_by": ID[who],
            "assignee_id": ID[who],
            "status": "done",
            "bucket": None,
            "completed_at": at,
            **values,
        }
    )


def _day(w: TaskWorld, who: str, day: str | None = None):
    params = {"day": day} if day else {}
    return w.http.get("/api/v1/timesheet/day", params=params, headers=as_(ID[who]))


def _suggested(w: TaskWorld, who: str = "member", day: str | None = None) -> list[dict]:
    r = _day(w, who, day)
    assert r.status_code == 200, r.text
    return r.json()["suggestions"]


class TestWhichTasks:
    """FR-TASK-LOG-01; TP-19; TQ-16."""

    def test_suggestions_are_tasks_completed_today_in_ist(self, world):
        early = _done(world, "member", "Fix login", "2026-10-19T19:00:00+00:00")
        _done(world, "member", "Yesterday", "2026-10-19T18:00:00+00:00")
        _done(world, "member", "Tomorrow", "2026-10-20T18:30:00+00:00")
        late = _done(world, "member", "Ship report", "2026-10-20T18:00:00+00:00")
        assert [s["task_id"] for s in _suggested(world)] == [early["id"], late["id"]]

    def test_each_has_its_title_and_no_project_when_unlinked(self, world):
        task = _done(world, "member", "Fix login", "2026-10-20T05:00:00+00:00")
        assert _suggested(world) == [
            {"task_id": task["id"], "title": "Fix login", "project": None, "added": False}
        ]

    def test_a_confirmed_project_comes_with_the_suggestion(self, world):
        task = _done(world, "member", "Fix login", "2026-10-20T05:00:00+00:00")
        world.fake.tasks.add_link(task, "tva_project", world.lumen["id"])
        [s] = _suggested(world)
        assert s["project"] == {"id": world.lumen["id"], "name": "Lumen"}

    def test_a_tentative_or_archived_project_gives_no_project(self, world):
        for project in (world.orbit, world.old):
            task = _done(world, "member", project["name"], "2026-10-20T05:00:00+00:00")
            world.fake.tasks.add_link(task, "tva_project", project["id"])
        assert [s["project"] for s in _suggested(world)] == [None, None]

    def test_a_crm_task_s_project_is_left_out_for_a_member_who_cannot_see_it(self, world):
        """FR-TASK-VIS-07 — Ravi is a Labs member never allocated to Lumen."""
        crm = _done(
            world,
            "removed",
            "Call Acme",
            "2026-10-20T05:00:00+00:00",
            workspace_id=world.labs["id"],
        )
        world.fake.tasks.add_link(crm, "opportunity", world.deal["id"])
        world.fake.tasks.add_link(crm, "tva_project", world.lumen["id"])
        assert [(s["task_id"], s["project"]) for s in _suggested(world, "removed")] == [
            (crm["id"], None)
        ]
        world.fake.add_allocation(world.lumen, ID["removed"])
        [s] = _suggested(world, "removed")
        assert s["project"] == {"id": world.lumen["id"], "name": "Lumen"}

    def test_a_personal_task_s_project_the_person_cannot_see_gives_no_project(self, world):
        """FR-TASK-VIS-07 — Uma would see the name only, and cannot log
        against a project she cannot see."""
        task = _done(world, "user", "Fix login", "2026-10-20T05:00:00+00:00")
        world.fake.tasks.add_link(task, "tva_project", world.lumen["id"])
        [s] = _suggested(world, "user")
        assert s["project"] is None
        assert world.lumen["name"] not in str(s) and world.lumen["id"] not in str(s)

    def test_an_imported_task_is_never_suggested(self, world):
        _done(world, "member", "From Life OS", "2026-10-20T05:00:00+00:00", source="import")
        assert _suggested(world) == []

    def test_only_done_tasks_assigned_to_the_person(self, world):
        _done(world, "member", "Cancelled", "2026-10-20T05:00:00+00:00", status="cancelled")
        _done(world, "member", "Deleted", "2026-10-20T05:00:00+00:00", deleted_at="2026-10-20")
        world.fake.tasks.add_task(
            title="Someone else's",
            created_by=ID["member"],
            assignee_id=ID["assignee"],
            status="done",
            completed_at="2026-10-20T05:00:00+00:00",
        )
        assert _suggested(world) == []

    def test_a_crm_task_is_suggested_only_while_a_member(self, world):
        crm = _done(
            world,
            "removed",
            "Call Acme",
            "2026-10-20T05:00:00+00:00",
            workspace_id=world.labs["id"],
        )
        assert [s["task_id"] for s in _suggested(world, "removed")] == [crm["id"]]
        del world.fake.rows["crm_memberships"][world.removed["id"]]
        assert _suggested(world, "removed") == []

    def test_the_rule_reads_the_day_in_ist(self):
        start, end = rules.day_bounds(TODAY)
        assert (start.isoformat(), end.isoformat()) == (
            "2026-10-20T00:00:00+05:30",
            "2026-10-21T00:00:00+05:30",
        )
        task = {"status": "done", "source": "manual", "completed_at": "2026-10-19T18:30:00Z"}
        assert rules.completed_on(task, TODAY)
        assert not rules.completed_on({**task, "completed_at": "2026-10-19T18:29:59Z"}, TODAY)


class TestWhenOffered:
    """FR-TASK-LOG-02, -03."""

    def test_none_below_level_3(self, world):
        _done(world, "member", "Fix login", "2026-10-20T05:00:00+00:00")
        world.fake.profiles[ID["member"]]["tasks_level"] = 2
        assert _suggested(world) == []

    def test_the_global_default_switches_them_on(self, world):
        _done(world, "off", "Fix login", "2026-10-20T05:00:00+00:00")
        assert _suggested(world, "off") == []
        world.fake.tasks.default = 3
        assert len(_suggested(world, "off")) == 1

    def test_none_for_a_crm_only_account(self, world):
        _done(world, "seller", "Fix login", "2026-10-20T05:00:00+00:00")
        world.fake.profiles[ID["seller"]]["tasks_level"] = 3
        assert _day(world, "seller").status_code == 403
        assert not rules.offered(3, "crm_only", True)
        assert (
            service.for_day(
                ID["seller"], TODAY, level=3, account_type="crm_only", can_log=True, notes=[]
            )
            == []
        )

    def test_none_on_a_future_day(self, world):
        _done(world, "member", "Fix login", "2026-10-21T05:00:00+00:00")
        r = _day(world, "member", "2026-10-21")
        assert r.json()["suggestions"] == []
        assert r.json()["refusal"] == "You cannot log hours for a day that has not happened yet."

    def test_none_on_a_locked_day_and_the_reason_shows(self, world):
        _done(world, "member", "Fix login", "2026-08-03T05:00:00+00:00")
        r = _day(world, "member", "2026-08-03")
        assert r.json()["can_log"] is False and r.json()["suggestions"] == []
        assert "closed for editing" in r.json()["refusal"]

    def test_the_rule(self):
        assert rules.offered(3, "staff", True)
        assert rules.offered(4, "staff", True)
        assert not rules.offered(2, "staff", True)
        assert not rules.offered(3, "staff", False)


class TestAlreadyAdded:
    """FR-TASK-LOG-05; TP-09."""

    def test_a_title_already_in_a_note_that_day_shows_as_added(self, world):
        _done(world, "member", "Fix login", "2026-10-20T05:00:00+00:00")
        _done(world, "member", "Ship report", "2026-10-20T06:00:00+00:00")
        world.entries.append(
            {
                "id": "e1",
                "project_id": world.lumen["id"],
                "activity": None,
                "hours_office": "2.00",
                "hours_home": "0.00",
                "note": "Standup; Fix login",
            }
        )
        assert [(s["title"], s["added"]) for s in _suggested(world)] == [
            ("Fix login", True),
            ("Ship report", False),
        ]

    def test_a_short_title_inside_a_longer_word_is_not_added(self, world):
        _done(world, "member", "Fix", "2026-10-20T05:00:00+00:00")
        _done(world, "member", "QA", "2026-10-20T06:00:00+00:00")
        world.entries.append(
            {
                "id": "e1",
                "project_id": world.lumen["id"],
                "activity": None,
                "hours_office": "2.00",
                "hours_home": "0.00",
                "note": "Fixed the login bug; QA review of Fixtures",
            }
        )
        assert [(s["title"], s["added"]) for s in _suggested(world)] == [
            ("Fix", False),
            ("QA", False),
        ]

    def test_the_rule(self):
        assert rules.already_added("Fix login", [None, "Fix login; Ship report"])
        assert rules.already_added("Ship report", ["Fix login; Ship report"])
        assert rules.already_added("Fix login", ["Fix login"])
        assert rules.already_added("A; B", ["Standup; A; B"])
        assert not rules.already_added("Fix login", [None, "", "Fixed it"])
        assert not rules.already_added("Fix", ["Fixed the login bug"])
        assert not rules.already_added("QA", ["QA review of Fixtures"])
        assert not rules.already_added("login", ["Fix login; Ship report"])


class TestNothingStored:
    """FR-TASK-LOG-04, -05; TP-09 — the read writes nothing."""

    def test_reading_the_day_writes_nothing(self, world):
        task = _done(world, "member", "Fix login", "2026-10-20T05:00:00+00:00")
        before = dict(world.tasks[task["id"]])
        audit = len(world.fake.audit)
        assert len(_suggested(world)) == 1
        assert world.fake.tasks.applied == []
        assert len(world.fake.audit) == audit
        assert world.tasks[task["id"]] == before


class TestOnlyTheOwnDay:
    """FR-TASK-LOG-07 — only `/timesheet/day` carries suggestions: never
    someone else's week, team weeks or sign-off."""

    @staticmethod
    def _callers(name: str) -> set[str]:
        """`file:function` for each function in `app/` that calls `name`."""
        found = set()
        for path in APP.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for node in ast.walk(fn):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute | ast.Name)
                        and getattr(node.func, "attr", getattr(node.func, "id", None)) == name
                    ):
                        found.add(f"{path.relative_to(APP)}:{fn.name}")
        return found

    def test_only_the_day_read_computes_suggestions(self):
        assert self._callers("for_day") == {"services/timesheets.py:day_for"}

    def test_only_the_person_s_own_day_route_reads_the_day(self):
        assert self._callers("day_for") == {"api/timesheet.py:my_day"}

    def test_a_week_carries_no_suggestions(self, world, monkeypatch):
        def refuse(*_, **__):
            raise AssertionError("a week read computed suggestions")

        monkeypatch.setattr(service, "for_day", refuse)
        monkeypatch.setattr(holidays, "holidays_by_person", lambda ids, *_: {i: set() for i in ids})
        monkeypatch.setattr(db, "list_bookings", lambda **_: [])
        week = timesheets.week_for(ID["member"], date(2026, 10, 19))
        assert "suggestions" not in str(week)
