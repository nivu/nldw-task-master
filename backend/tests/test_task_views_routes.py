"""Waiting on, Assigned by me and the project task panel — spec 008
FR-TASK-VIEW-01 to -03, FR-TASK-ADD-05, FR-TASK-VIS-01, -02, FR-TASK-ROLL-03;
TP-01; contracts §1 "Route order", §2.

Through the routes, on the shared task world with phase 2 on for the cast
(Tuesday 20 October 2026, noon IST).

Acceptance criteria (spec §12, phase 2):
- "A TVA project page shows the tasks of that project that the viewer may
  see" — TestProjectPanel.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from crm_fake import as_
from tasks_fake import ID, NOW, TaskWorld

NOT_ON = "Tasks are not switched on for you yet."


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 2 on for everyone who has phase 1."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 2
    return w


def _ok(r) -> dict:
    assert r.status_code in (200, 201), r.text
    return r.json()


def _waiting(world: TaskWorld, who: str, title: str, *, days: int = 0, **on) -> dict:
    task = world.add(who, title=title, status="waiting", **on)
    world.tasks[task["id"]]["created_at"] = (NOW - timedelta(days=days)).isoformat()
    return task


def _project(world: TaskWorld, who: str, project: dict, **query) -> tuple[int, dict]:
    r = world.call(who, "GET", f"/project/{project['id']}", params=query)
    return r.status_code, r.json()


# ---------------------------------------------------------------------------
# FR-TASK-ROLL-03 — the views are phase 2
# ---------------------------------------------------------------------------


class TestPhaseSwitch:
    @pytest.mark.parametrize(
        ("path", "params"),
        [("", {"view": "waiting"}), ("", {"view": "assigned_by_me"}), ("/project/{p}", {})],
    )
    def test_level_1_is_refused(self, world, path, params):
        world.fake.profiles[ID["owner"]]["tasks_level"] = 1
        r = world.call("owner", "GET", path.replace("{p}", world.lumen["id"]), params=params)
        assert (r.status_code, r.json()["detail"]) == (403, NOT_ON)

    def test_level_1_keeps_my_tasks(self, world):
        world.fake.profiles[ID["owner"]]["tasks_level"] = 1
        assert set(_ok(world.call("owner", "GET", "", params={"view": "mine"}))) >= {"sections"}

    def test_an_unknown_view_is_refused(self, world):
        assert world.call("owner", "GET", "", params={"view": "everyone"}).status_code == 422


# ---------------------------------------------------------------------------
# FR-TASK-VIEW-01 — Waiting on
# ---------------------------------------------------------------------------


class TestWaiting:
    def test_grouped_by_whom_they_wait_on_with_their_age(self, world):
        a = _waiting(world, "creator", "Pricing", days=6, waiting_on_id=ID["assignee"])
        b = _waiting(world, "creator", "Contract", days=2, waiting_on_text="Life OS")
        c = _waiting(world, "creator", "Deck", days=0, waiting_on_id=ID["assignee"])
        world.add("creator", title="Open one")
        out = _ok(world.call("creator", "GET", "", params={"view": "waiting"}))
        groups = [
            (g["waiting_on"], [(t["id"], t["age_days"]) for t in g["tasks"]]) for g in out["groups"]
        ]
        assert groups == [
            (
                {"id": ID["assignee"], "label": "Arun Assignee"},
                [(c["id"], 0), (a["id"], 6)],
            ),
            ({"id": None, "label": "Life OS"}, [(b["id"], 2)]),
        ]

    def test_a_picked_person_and_free_text_can_share_a_label(self, world):
        """A Life OS import, or a waited-on profile since deleted, leaves only
        the text; the two groups sort without comparing None with an id."""
        picked = _waiting(world, "creator", "Picked", waiting_on_id=ID["assignee"])
        text = _waiting(world, "creator", "Text", waiting_on_text="arun assignee")
        out = _ok(world.call("creator", "GET", "", params={"view": "waiting"}))
        assert [(g["waiting_on"]["id"], [t["id"] for t in g["tasks"]]) for g in out["groups"]] == [
            (None, [text["id"]]),
            (ID["assignee"], [picked["id"]]),
        ]

    def test_only_the_callers_own_waiting_tasks(self, world):
        given = _waiting(world, "creator", "Given", waiting_on_text="Bank", assignee_id="assignee")
        mine = _waiting(world, "creator", "Mine", waiting_on_text="Bank")
        ids = lambda who: [  # noqa: E731
            t["id"]
            for g in _ok(world.call(who, "GET", "", params={"view": "waiting"}))["groups"]
            for t in g["tasks"]
        ]
        assert ids("creator") == [mine["id"]]
        assert ids("assignee") == [given["id"]]
        assert ids("member") == []

    def test_the_person_waited_on_sees_nothing(self, world):
        _waiting(world, "creator", "Zqxwait", waiting_on_id=ID["waiting"])
        r = world.call("waiting", "GET", "", params={"view": "waiting"})
        assert _ok(r) == {"groups": []}
        assert "zqxwait" not in r.text.lower()

    def test_a_crm_task_after_removal_is_gone(self, world):
        _waiting(
            world,
            "removed",
            "Theirs",
            waiting_on_text="Client",
            links=[world.link(world.acme, "company")],
        )
        del world.fake.rows["crm_memberships"][world.removed["id"]]
        assert _ok(world.call("removed", "GET", "", params={"view": "waiting"})) == {"groups": []}


# ---------------------------------------------------------------------------
# FR-TASK-VIEW-02 — Assigned by me
# ---------------------------------------------------------------------------


class TestAssignedByMe:
    def test_tasks_the_caller_created_for_someone_else(self, world):
        given = world.add("creator", title="Draft", assignee_id="assignee", due_on="2026-10-22")
        later = world.add("creator", title="Plan", assignee_id="member")
        world.add("creator", title="Mine")
        world.add("assignee", title="Theirs", assignee_id="creator")
        done = world.add("creator", title="Done", assignee_id="assignee")
        world.call("assignee", "POST", f"/{done['id']}/complete")
        out = _ok(world.call("creator", "GET", "", params={"view": "assigned_by_me"}))
        assert [(t["id"], t["assignee"]["name"], t["status"]) for t in out["tasks"]] == [
            (given["id"], "Arun Assignee", "open"),
            (later["id"], "Deepika", "open"),
            (done["id"], "Arun Assignee", "done"),
        ]

    def test_a_handed_back_task_is_no_longer_given(self, world):
        task = world.add("creator", title="Back", assignee_id="assignee")
        world.call("assignee", "POST", f"/{task['id']}/hand-back", json={"note": "Not mine"})
        assert _ok(world.call("creator", "GET", "", params={"view": "assigned_by_me"})) == {
            "tasks": []
        }

    def test_a_deleted_task_is_left_out(self, world):
        task = world.add("creator", title="Gone", assignee_id="assignee")
        world.call("creator", "DELETE", f"/{task['id']}")
        assert _ok(world.call("creator", "GET", "", params={"view": "assigned_by_me"})) == {
            "tasks": []
        }

    def test_a_crm_task_after_the_creator_left_the_workspace_is_gone(self, world):
        world.add(
            "removed",
            title="Given",
            assignee_id="member",
            links=[world.link(world.acme, "company")],
        )
        del world.fake.rows["crm_memberships"][world.removed["id"]]
        assert _ok(world.call("removed", "GET", "", params={"view": "assigned_by_me"})) == {
            "tasks": []
        }


# ---------------------------------------------------------------------------
# FR-TASK-VIEW-03, FR-TASK-ADD-05 — the TVA project's task panel
# ---------------------------------------------------------------------------


class TestProjectPanel:
    def test_lists_only_what_the_viewer_may_see(self, world):
        lumen = world.link(world.lumen, "tva_project")
        project = world.add("member", title="Project task", links=[lumen])
        crm = world.add(
            "member", title="Deal and project", links=[world.link(world.deal, "opportunity"), lumen]
        )
        world.add("member", title="Not on the project")
        status, out = _project(world, "lead", world.lumen)
        assert status == 200
        assert out["project"] == {"id": world.lumen["id"], "name": "Lumen"}
        assert [t["id"] for t in out["tasks"]] == [project["id"]]
        assert out["tasks"][0]["may"]["edit"] is False
        _, labs = _project(world, "owner", world.lumen)
        assert {t["id"] for t in labs["tasks"]} == {project["id"], crm["id"]}

    def test_closed_tasks_on_request(self, world):
        lumen = world.link(world.lumen, "tva_project")
        open_ = world.add("member", title="Open", links=[lumen])
        done = world.add("member", title="Done", links=[lumen])
        world.call("member", "POST", f"/{done['id']}/complete")
        gone = world.add("member", title="Gone", links=[lumen])
        world.call("member", "DELETE", f"/{gone['id']}")
        assert [t["id"] for t in _project(world, "manager", world.lumen)[1]["tasks"]] == [
            open_["id"]
        ]
        assert [
            t["id"]
            for t in _project(world, "manager", world.lumen, include_closed=True)[1]["tasks"]
        ] == [open_["id"], done["id"]]

    @pytest.mark.parametrize("who", ["seller", "creator"])
    def test_a_project_the_caller_cannot_see_is_no_such_project(self, world, who):
        world.add("member", title="Zqxproj", links=[world.link(world.lumen, "tva_project")])
        status, out = _project(world, who, world.lumen)
        assert (status, out["detail"]) == (404, "No such project.")

    def test_an_unknown_project_is_no_such_project(self, world):
        r = world.call("owner", "GET", "/project/00000000-0000-4000-8000-000000000999")
        assert (r.status_code, r.json()["detail"]) == (404, "No such project.")

    def test_a_tva_user_cannot_open_the_project_page_but_sees_the_task_in_my_tasks(self, world):
        world.fake.add_allocation(world.lumen, ID["user"])
        task = world.add(
            "lead",
            title="Wire the API",
            assignee_id="user",
            links=[world.link(world.lumen, "tva_project")],
        )
        page = world.http.get(
            f"/api/v1/analytics/projects/{world.lumen['id']}", headers=as_(ID["user"])
        )
        assert page.status_code == 403
        mine = _ok(world.call("user", "GET", ""))
        assert task["id"] in [t["id"] for s in mine["sections"].values() for t in s]
        assert _ok(world.call("user", "GET", f"/{task['id']}"))["project"]["name"] == "Lumen"

    def test_add_task_from_the_panel_is_pre_linked_to_the_project(self, world):
        """The panel's Add task sends the project link (FR-TASK-ADD-05)."""
        made = _ok(
            world.call(
                "lead",
                "POST",
                "",
                json={"title": "Plan sprint", "links": [world.link(world.lumen, "tva_project")]},
            )
        )
        assert (made["kind"], made["project"]) == (
            "project",
            {"id": world.lumen["id"], "name": "Lumen"},
        )
        assert [t["id"] for t in _project(world, "lead", world.lumen)[1]["tasks"]] == [made["id"]]
        assert [t["id"] for t in _project(world, "member", world.lumen)[1]["tasks"]] == [made["id"]]

    def test_project_reaches_its_own_handler(self, world):
        status, out = _project(world, "owner", world.lumen)
        assert status == 200 and set(out) == {"project", "tasks"}

    def test_a_project_id_that_is_not_a_uuid_is_refused(self, world):
        assert world.call("owner", "GET", "/project/lumen").status_code == 422
