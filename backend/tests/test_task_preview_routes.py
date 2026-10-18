"""Quick-add preview — spec 008 contracts §1 `POST /tasks/quick-add/preview`;
FR-TASK-ADD-02 to -04, FR-TASK-VIS-08, FR-TASK-ROLL-04, FR-TASK-CHG-06.

The preview parses one line for the caller (`domain/tasks/quick_add`, tested
on its own in test_task_quick_add.py), says who will see the task, and
lists the questions that keep Save disabled. It writes nothing.

Acceptance criteria (spec §12, phase 1):
- "The task panel and the quick-add preview show who will see the task" —
  TestVisibleTo.
- Journey 4.2, before saving — TestTheDraft.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from crm_fake import as_
from fastapi.routing import APIRoute
from tasks_fake import ID, TaskWorld

from app.api.tasks import tasks as task_routes
from app.domain.tasks import service

PATH = "/quick-add/preview"


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    return TaskWorld(monkeypatch)


def _preview(world: TaskWorld, who: str, text: str, **answers):
    body: dict = {"text": text}
    if answers:
        body["answers"] = answers
    return world.call(who, "POST", PATH, json=body)


def _ok(world: TaskWorld, who: str, text: str, **answers) -> dict:
    r = _preview(world, who, text, **answers)
    assert r.status_code == 200, r.text
    return r.json()


class TestTheDraft:
    """Contracts §1 — `{draft, visible_to, questions}`."""

    def test_journey_4_2(self, world):
        out = _ok(world, "owner", "Call Deepak re RDX pilot fri #sales @Arun")
        assert out == {
            "draft": {
                "title": "Call Deepak re RDX pilot",
                "due_on": "2026-10-23",
                "due_weekday": "Friday",
                "bucket": "this_week",
                "tags": ["sales"],
                "assignee": {"id": ID["assignee"], "name": "Arun Assignee"},
                "links": [],
            },
            "visible_to": "Visible to: you and Arun Assignee",
            "questions": [],
        }

    def test_no_name_is_the_caller(self, world):
        out = _ok(world, "member", "Book dentist")
        assert out["draft"]["assignee"] == {"id": ID["member"], "name": "Deepika"}
        assert (out["draft"]["due_on"], out["draft"]["due_weekday"]) == (None, None)
        assert out["draft"]["bucket"] == "later"

    def test_it_writes_nothing(self, world):
        tasks, links, audit = (
            dict(world.tasks),
            dict(world.fake.rows["task_links"]),
            list(world.fake.audit),
        )
        _ok(world, "owner", "Call Deepak fri #sales @Arun")
        _ok(world, "owner", "Call Deepak tue", date="2026-10-27")
        assert (world.tasks, world.fake.rows["task_links"], world.fake.audit) == (
            tasks,
            links,
            audit,
        )

    def test_extra_fields_are_refused(self, world):
        r = world.call("owner", "POST", PATH, json={"text": "x", "title": "y"})
        assert r.status_code == 422


class TestVisibleTo:
    """FR-TASK-VIS-08 — the sentence, before anything is saved. A quick-add
    draft has no links, so it is private or shared personal."""

    def test_private(self, world):
        assert _ok(world, "owner", "Book dentist")["visible_to"] == "Visible to: only you"

    def test_shared_personal(self, world):
        out = _ok(world, "owner", "Send the deck @Lakshmi")
        assert out["visible_to"] == "Visible to: you and Lakshmi Lead"

    def test_assigning_yourself_by_name_is_private(self, world):
        assert _ok(world, "owner", "x @Navaneeth")["visible_to"] == "Visible to: only you"


class TestQuestions:
    """FR-TASK-ADD-02, -04 — a non-empty `questions` means Save stays
    disabled; an answer closes its question."""

    def test_an_ambiguous_date_asks_and_leaves_no_date(self, world):
        out = _ok(world, "owner", "Call Deepak 5/11")
        assert out["draft"]["due_on"] is None
        assert out["questions"] == [
            {
                "kind": "date",
                "prompt": "5 Nov or 11 May?",
                "options": [
                    {"value": "2026-11-05", "label": "Thu 5 Nov 2026"},
                    {"value": "2027-05-11", "label": "Tue 11 May 2027"},
                ],
            }
        ]

    def test_the_answer_closes_it(self, world):
        out = _ok(world, "owner", "Call Deepak 5/11", date="2026-11-05")
        assert (out["draft"]["due_on"], out["draft"]["due_weekday"], out["questions"]) == (
            "2026-11-05",
            "Thursday",
            [],
        )

    def test_someone_without_my_tasks_is_no_one(self, world):
        # FR-TASK-ROLL-04 — Omar has phase 1 off, so he is not offered.
        out = _ok(world, "owner", "Call Deepak @Omar")
        assert [q["prompt"] for q in out["questions"]] == [
            "No one you can assign is called Omar: who is it for?"
        ]
        assert out["draft"]["assignee"]["id"] == ID["owner"]

    def test_several_ask_and_the_chosen_one_is_used(self, world):
        world.fake.profiles[ID["former"]]["display_name"] = "Arun Former"
        out = _ok(world, "owner", "x @arun")
        assert [o["value"] for o in out["questions"][0]["options"]] == [
            ID["assignee"],
            ID["former"],
        ]
        out = _ok(world, "owner", "x @arun", assignee_id=ID["former"])
        assert out["questions"] == []
        assert out["draft"]["assignee"] == {"id": ID["former"], "name": "Arun Former"}
        assert out["visible_to"] == "Visible to: you and Arun Former"

    def test_an_answer_naming_someone_not_offered_is_404(self, world):
        r = _preview(world, "owner", "x @Omar", assignee_id=ID["off"])
        assert (r.status_code, r.json()["detail"]) == (404, "No such person.")


class TestTheirOwnDay:
    """FR-TASK-CHG-06 — "today" is the caller's, in `profiles.timezone`."""

    def test_sydney_is_already_wednesday(self, world, monkeypatch):
        # 13:30 UTC on Tue 20 Oct is 00:30 on Wed 21 Oct in Sydney (AEDT) and
        # 19:00 on Tue 20 Oct in Asia/Kolkata.
        monkeypatch.setattr(service, "_now", lambda: datetime(2026, 10, 20, 13, 30, tzinfo=UTC))
        seller = _ok(world, "seller", "Call Deepak today")
        owner = _ok(world, "owner", "Call Deepak today")
        assert (seller["draft"]["due_on"], owner["draft"]["due_on"]) == (
            "2026-10-21",
            "2026-10-20",
        )
        assert [q["prompt"] for q in _ok(world, "seller", "x wed")["questions"]] == [
            "Today or next Wednesday?"
        ]


class TestRouteOrder:
    """Contracts §1 "Route order" — the fixed path reaches its own handler,
    never `/tasks/{id}`."""

    def test_it_reaches_its_own_handler(self, world):
        r = world.http.post(
            "/api/v1/tasks/quick-add/preview", json={"text": "x"}, headers=as_(ID["owner"])
        )
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"draft", "visible_to", "questions"}

    def test_it_is_declared_before_the_id_routes(self):
        paths = [r.path for r in task_routes.router.routes if isinstance(r, APIRoute)]
        assert paths.index("/tasks/quick-add/preview") < paths.index("/tasks/{id}")
