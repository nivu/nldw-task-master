"""Task links — spec 008 FR-TASK-DATA-05, -06, -13, -15; TP-01, TP-13;
contracts §1 `POST /tasks/{id}/links`, `DELETE /tasks/{id}/links/{link_id}`.

A first CRM link turns a personal or project task into a CRM task of that
workspace in the same write; a CRM task never loses its last CRM link; a
project link is one at most, of a project the caller can see, not archived,
and only on a Labs task.

Acceptance criteria (spec §12, phase 1):
- "The CRM writer refuses a task created through the CRM with no link;
  removing a task's last CRM link is refused through CRM routes, task
  routes and task_update" — TestLastCrmLink (the CRM routes:
  test_task_crm_changes.py).
- "An Academy task cannot link a TVA project (422); linking a project the
  caller cannot see answers 404; linking an archived project is refused; a
  second project link on a task is refused; a TVA user can link a project
  they are or were allocated to" — TestProjectLinks.
- "A Viewer, or a TVA user in no workspace, cannot add a CRM link to their
  personal task (404 or 403 by 007 rules)" — TestCrmLinks.
"""

from __future__ import annotations

import asyncio

import pytest
from tasks_fake import ID, TaskWorld

from app.mcp import tasks as tools
from app.mcp.server import ToolError


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    return TaskWorld(monkeypatch)


class Ctx:
    def __init__(self, who: str) -> None:
        self.headers = {"authorization": f"Bearer {ID[who]}"}


def _link(world: TaskWorld, who: str, task: dict, record: dict, record_type: str):
    return world.call(who, "POST", f"/{task['id']}/links", json=world.link(record, record_type))


def _detail(r) -> tuple[int, str]:
    return r.status_code, r.json().get("detail")


class TestCrmLinks:
    """FR-TASK-DATA-05, -13, -15."""

    def test_the_first_crm_link_sets_the_workspace(self, world):
        task = world.add("member", title="Book dentist")
        r = _link(world, "member", task, world.acme, "company")
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["kind"], out["workspace"]["key"]) == ("crm", "labs")
        assert world.tasks[task["id"]]["workspace_id"] == world.labs["id"]
        [actor, ops] = world.fake.tasks.applied[-1]
        assert [op["op"] for op in ops] == ["update", "insert"]

    def test_a_second_record_of_the_same_workspace(self, world):
        task = world.add("member", links=[world.link(world.acme, "company")])
        r = _link(world, "member", task, world.deal, "opportunity")
        assert [lk["record_type"] for lk in r.json()["links"]] == ["company", "opportunity"]

    def test_records_of_two_workspaces_are_refused(self, world):
        task = world.add("owner", links=[world.link(world.acme, "company")])
        r = _link(world, "owner", task, world.college, "company")
        assert _detail(r) == (422, "A task cannot link records of two workspaces.")

    def test_a_viewer_cannot_add_a_crm_link_to_their_personal_task(self, world):
        task = world.add("viewer", title="Mine")
        r = _link(world, "viewer", task, world.acme, "company")
        assert _detail(r) == (404, "No such company.")
        assert world.tasks[task["id"]]["workspace_id"] is None

    def test_a_tva_user_in_no_workspace_cannot_either(self, world):
        task = world.add("user", title="Mine")
        assert _detail(_link(world, "user", task, world.acme, "company")) == (
            404,
            "No such company.",
        )

    def test_the_assignee_must_be_a_member_of_the_workspace(self, world):
        task = world.add("member", assignee_id="assignee")
        r = _link(world, "member", task, world.acme, "company")
        assert _detail(r) == (422, "The assignee must be a member of this workspace.")

    def test_a_repeated_link_is_refused(self, world):
        task = world.add("member", links=[world.link(world.acme, "company")])
        r = _link(world, "member", task, world.acme, "company")
        assert _detail(r) == (422, "The same record is linked twice.")


class TestLastCrmLink:
    """FR-TASK-DATA-05."""

    def test_the_last_crm_link_is_refused(self, world):
        task = world.add("member", links=[world.link(world.acme, "company")])
        [link] = task["links"]
        r = world.call("member", "DELETE", f"/{task['id']}/links/{link['id']}")
        assert _detail(r) == (
            422,
            "A CRM task needs at least one linked record; delete it instead.",
        )

    def test_one_of_two_crm_links_goes(self, world):
        task = world.add(
            "member",
            links=[world.link(world.acme, "company"), world.link(world.deal, "opportunity")],
        )
        first = task["links"][0]
        r = world.call("member", "DELETE", f"/{task['id']}/links/{first['id']}")
        assert [lk["record_type"] for lk in r.json()["links"]] == ["opportunity"]

    def test_task_update_cannot_remove_links(self, world):
        task = world.add("member", links=[world.link(world.acme, "company")])
        with pytest.raises(ToolError) as caught:
            asyncio.run(tools.task_update(Ctx("member"), task["id"], {"links": []}))
        assert str(caught.value).startswith("422")
        assert [lk["task_id"] for lk in world.fake.rows["task_links"].values()] == [task["id"]]

    def test_a_link_not_on_the_task_is_404(self, world):
        task = world.add("member")
        other = world.add("member", links=[world.link(world.acme, "company")])
        r = world.call("member", "DELETE", f"/{task['id']}/links/{other['links'][0]['id']}")
        assert _detail(r) == (404, "No such link.")


class TestProjectLinks:
    """FR-TASK-DATA-06 (TP-01, TP-13)."""

    def test_an_academy_task_cannot_link_a_project(self, world):
        task = world.add("owner", links=[world.link(world.college, "company")])
        r = _link(world, "owner", task, world.lumen, "tva_project")
        assert _detail(r) == (422, "Only Nunnari Labs tasks can link a TVA project.")
        project_task = world.add("owner", links=[world.link(world.lumen, "tva_project")])
        r = _link(world, "owner", project_task, world.college, "company")
        assert _detail(r) == (422, "Only Nunnari Labs tasks can link a TVA project.")

    def test_a_project_they_cannot_see_is_404(self, world):
        task = world.add("user")
        r = _link(world, "user", task, world.orbit, "tva_project")
        assert _detail(r) == (404, "No such project.")

    def test_an_archived_project_is_refused(self, world):
        task = world.add("owner")
        r = _link(world, "owner", task, world.old, "tva_project")
        assert _detail(r) == (422, "That project is archived.")

    def test_a_link_to_a_project_archived_later_stays(self, world):
        task = world.add("owner", links=[world.link(world.orbit, "tva_project")])
        world.fake.rows["projects"][world.orbit["id"]]["is_archived"] = True
        out = world.call("owner", "GET", f"/{task['id']}").json()
        assert out["project"] == {"id": world.orbit["id"], "name": "Orbit"}
        assert out["kind"] == "project"

    def test_a_second_project_is_refused(self, world):
        task = world.add("owner", links=[world.link(world.lumen, "tva_project")])
        r = _link(world, "owner", task, world.orbit, "tva_project")
        assert _detail(r) == (422, "A task can link only one TVA project.")

    def test_a_repeated_project_link_is_refused(self, world):
        task = world.add("owner", links=[world.link(world.lumen, "tva_project")])
        r = _link(world, "owner", task, world.lumen, "tva_project")
        assert _detail(r) == (422, "The same record is linked twice.")

    def test_a_tva_user_links_a_project_they_were_allocated_to(self, world):
        world.fake.add_allocation(world.orbit, ID["user"], released_at="2026-09-01T00:00:00+00:00")
        task = world.add("user")
        r = _link(world, "user", task, world.orbit, "tva_project")
        assert r.status_code == 200, r.text
        assert r.json()["project"] == {"id": world.orbit["id"], "name": "Orbit"}
        assert world.add("member", links=[world.link(world.lumen, "tva_project")])["kind"] == (
            "project"
        )

    def test_a_labs_task_links_a_project(self, world):
        task = world.add("owner", links=[world.link(world.acme, "company")])
        r = _link(world, "owner", task, world.lumen, "tva_project")
        assert (r.json()["kind"], r.json()["project"]["name"]) == ("crm", "Lumen")
        assert [lk["record_type"] for lk in r.json()["links"]] == ["company"]

    def test_a_crm_only_assignee_keeps_a_personal_task_off_projects(self, world):
        task = world.add("owner", assignee_id="seller")
        r = _link(world, "owner", task, world.lumen, "tva_project")
        assert _detail(r) == (422, "A CRM-only account cannot be given a project task.")

    def test_a_project_link_is_removed_by_the_project_id(self, world):
        task = world.add("owner", links=[world.link(world.lumen, "tva_project")])
        r = world.call("owner", "DELETE", f"/{task['id']}/links/{task['project']['id']}")
        assert (r.status_code, r.json()["project"], r.json()["kind"]) == (200, None, "private")

    def test_a_project_they_cannot_see_is_never_disclosed(self, world):
        """FR-TASK-VIS-07: no project link at all, whichever project is tried."""
        world.fake.add_allocation(world.orbit, ID["creator"])
        task = world.add("owner", links=[world.link(world.acme, "company")])
        _link(world, "owner", task, world.lumen, "tva_project")
        for who, project in (
            ("creator", world.orbit),
            ("seller", world.lumen),
            ("seller", world.orbit),
        ):
            r = _link(world, who, task, project, "tva_project")
            assert _detail(r) == (404, "No such project."), (who, project["name"])
        r = world.call("creator", "DELETE", f"/{task['id']}/links/{world.lumen['id']}")
        assert _detail(r) == (404, "No such link.")

    def test_a_project_link_is_removed(self, world):
        task = world.add("owner", links=[world.link(world.lumen, "tva_project")])
        [link] = [
            lk for lk in world.fake.rows["task_links"].values() if lk["task_id"] == task["id"]
        ]
        r = world.call("owner", "DELETE", f"/{task['id']}/links/{link['id']}")
        assert (r.status_code, r.json()["project"], r.json()["kind"]) == (200, None, "private")
