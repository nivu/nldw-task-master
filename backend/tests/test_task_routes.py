"""Task routes, phase 1 — spec 008 contracts §1; FR-TASK-ROLL-03, -04,
FR-TASK-VIS-02, FR-TASK-DATA-02, -07, -09, -11, FR-TASK-LIST-05; TP-02,
TP-03, TP-12, TP-14; `007` Q-57.

Every route answers 403 at level 0 (the off-by-default gate) while CRM
tasks keep working for that person; create, read, change, complete, reopen,
delete and restore through `/api/v1/tasks`; each fixed path reaches its own
handler, never `/tasks/{id}` (contracts §1 "Route order").

Acceptance criteria (spec §12, phase 1):
- "A person with phase 1 off gets 403 from every task route and tool; CRM
  tasks work for them as before" — TestOffByDefault (tools:
  test_task_mcp.py).
- "Completing a task sets completed at; reopening clears it" —
  TestCompleteAndReopen.
- "A crm_only account cannot be assigned a project task with no CRM link,
  and can be assigned a CRM task of its workspace" — TestCreate.
- "Assigning a task with no workspace to someone without phase 1 is refused
  422 with their name" — TestCreate.
- "A deleted task with no workspace can be restored by its creator, and not
  by its assignee" — TestDeleteAndRestore (the purge: test_task_jobs.py).
- "A tag over 40 characters, or an eleventh tag on a task, is refused" —
  TestCreate.
- "Someone who sees a project task only through the project rule can read
  it and cannot change it" — TestChange.
- "A Viewer assigned a CRM task can change its status only, through both
  the task routes and task_update" — TestChange (the tool:
  test_task_mcp.py).
- "A child task whose parent is private shows no parent id or title;
  setting a parent the caller cannot see answers 404" — TestParent.
- "A crm_only account can use My Tasks and task_* tools and is refused every
  TVA route as before" — TestCreate, test_crm_only_refused.py.
"""

from __future__ import annotations

import re
import uuid

import pytest
from crm_fake import as_
from fastapi.routing import APIRoute, iter_route_contexts
from tasks_fake import ID, TaskWorld

from app.main import app

PREFIX = "/api/v1/tasks"
NOT_ON = "Tasks are not switched on for you yet."


def _task_routes() -> list[tuple[str, str]]:
    return sorted(
        (method, rc.path)
        for rc in iter_route_contexts(app.routes)
        if isinstance(rc.original_route, APIRoute)
        and (rc.path == PREFIX or (rc.path or "").startswith(PREFIX + "/"))
        for method in rc.original_route.methods
    )


TASK_ROUTES = _task_routes()


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    return TaskWorld(monkeypatch)


def _detail(r) -> tuple[int, str]:
    return r.status_code, r.json().get("detail")


# ---------------------------------------------------------------------------
# FR-TASK-ROLL-03 — off by default
# ---------------------------------------------------------------------------


class TestOffByDefault:
    def test_the_slice_adds_routes(self):
        assert len(TASK_ROUTES) == 15

    @pytest.mark.parametrize(
        ("method", "path"), TASK_ROUTES, ids=[f"{m} {p}" for m, p in TASK_ROUTES]
    )
    def test_level_0_is_refused_on_every_route(self, world, method, path):
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
        r = world.http.request(method, url, headers=as_(ID["off"]), json={})
        assert _detail(r) == (403, NOT_ON)

    def test_level_0_from_me(self, world):
        r = world.http.get("/api/v1/me", headers=as_(ID["off"]))
        assert r.status_code == 200, r.text
        assert r.json()["tasks"] == {"level": 0}

    def test_crm_tasks_still_work_for_them(self, world):
        world.fake.add_member(world.labs, ID["off"], "member")
        r = world.http.post(
            "/api/v1/crm/tasks?workspace=labs",
            json={"title": "Send the deck", "links": [world.link(world.acme, "company")]},
            headers=as_(ID["off"]),
        )
        assert r.status_code == 201, r.text
        listed = world.http.get("/api/v1/crm/tasks?workspace=labs", headers=as_(ID["off"]))
        assert [t["id"] for t in listed.json()["rows"]] == [r.json()["id"]]

    def test_the_global_default_switches_them_on(self, world):
        world.fake.tasks.default = 1
        assert world.call("off", "GET", "").status_code == 200


# ---------------------------------------------------------------------------
# POST /tasks
# ---------------------------------------------------------------------------


class TestCreate:
    def test_a_private_task(self, world):
        out = world.add("member", title="Book dentist")
        assert (out["kind"], out["visible_to"], out["source"]) == (
            "private",
            "Visible to: only you",
            "manual",
        )
        assert world.tasks[out["id"]]["created_by"] == ID["member"]

    def test_with_tags_parent_and_waiting(self, world):
        parent = world.add("member", title="Pilot")
        out = world.add(
            "member",
            title="Pricing",
            tags=["sales", " sales ", "RDX"],
            parent_id=parent["id"],
            status="waiting",
            waiting_on_text="the CA",
        )
        row = world.tasks[out["id"]]
        assert (row["tags"], row["parent_id"], row["status"], row["waiting_on_text"]) == (
            ["sales", "RDX"],
            parent["id"],
            "waiting",
            "the CA",
        )
        assert out["parent"] == {"id": parent["id"], "title": "Pilot"}

    def test_waiting_needs_waiting_on(self, world):
        r = world.call("member", "POST", "", json={"title": "Pricing", "status": "waiting"})
        assert _detail(r) == (422, "Waiting needs who or what you are waiting on.")

    def test_done_is_not_a_new_status(self, world):
        r = world.call("member", "POST", "", json={"title": "Pricing", "status": "done"})
        assert r.status_code == 422

    def test_assignee_by_name_or_email(self, world):
        out = world.add("member", assignee="Arun Assignee")
        assert out["assignee"] == {"id": ID["assignee"], "name": "Arun Assignee"}
        assert out["kind"] == "shared_personal"
        assert out["visible_to"] == "Visible to: you and Arun Assignee"
        out = world.add("member", assignee="UMA@nunnarilabs.com")
        assert out["assignee"]["id"] == ID["user"]

    def test_an_assignee_name_matching_several_names_them(self, world):
        uma2 = str(uuid.uuid4())
        world.fake.profiles[uma2] = {
            **world.fake.profiles[ID["user"]],
            "id": uma2,
            "email": "uma.k@nunnarilabs.com",
            "display_name": "Uma K",
        }
        r = world.call("member", "POST", "", json={"title": "Pricing", "assignee": "uma"})
        assert _detail(r) == (
            422,
            "uma could be Uma K (uma.k@nunnarilabs.com) and Uma User (uma@nunnarilabs.com); "
            "give their email.",
        )

    def test_assignee_without_phase_1_is_refused_with_their_name(self, world):
        r = world.call("member", "POST", "", json={"title": "x", "assignee_id": ID["off"]})
        assert _detail(r) == (422, "Omar Off does not have My Tasks yet.")

    def test_a_crm_only_account_is_not_given_a_project_task(self, world):
        r = world.call(
            "owner",
            "POST",
            "",
            json={
                "title": "x",
                "assignee_id": ID["seller"],
                "links": [world.link(world.lumen, "tva_project")],
            },
        )
        assert _detail(r) == (422, "A CRM-only account cannot be given a project task.")

    def test_a_crm_only_member_is_given_a_crm_task_of_its_workspace(self, world):
        out = world.add("owner", assignee_id="seller", links=[world.link(world.acme, "company")])
        assert (out["kind"], out["assignee"]["id"]) == ("crm", ID["seller"])

    def test_a_crm_only_account_uses_my_tasks(self, world):
        out = world.add("seller", title="Call back")
        listed = world.call("seller", "GET", "").json()
        assert [t["id"] for t in listed["sections"]["later"]] == [out["id"]]

    def test_a_tag_over_40_characters_is_refused(self, world):
        r = world.call("member", "POST", "", json={"title": "x", "tags": ["a" * 41]})
        assert _detail(r) == (422, "A tag can be at most 40 characters.")

    def test_an_eleventh_tag_is_refused(self, world):
        r = world.call(
            "member", "POST", "", json={"title": "x", "tags": [f"t{i}" for i in range(11)]}
        )
        assert r.status_code == 422
        assert world.tasks == {}

    def test_a_blank_title_is_refused(self, world):
        r = world.call("member", "POST", "", json={"title": "   "})
        assert r.status_code == 422

    def test_from_mcp_the_source_is_nldw(self, world, monkeypatch):
        from app.mcp import server

        r = world.http.post(
            PREFIX,
            json={"title": "x"},
            headers={**as_(ID["member"]), "X-Portal-Channel-Key": server.CHANNEL_KEY},
        )
        assert r.json()["source"] == "nldw"
        assert world.fake.tasks.applied[-1][0]["channel"] == "mcp"


# ---------------------------------------------------------------------------
# GET /tasks/{id}
# ---------------------------------------------------------------------------


class TestRead:
    def test_the_creator_reads_it_with_subtasks_and_history(self, world):
        parent = world.add("member", title="Pilot")
        child = world.add("member", title="Plan", parent_id=parent["id"])
        out = world.call("member", "GET", f"/{parent['id']}").json()
        assert [s["id"] for s in out["subtasks"]] == [child["id"]]
        assert out["subtask_count"] == 1
        assert [h["action"] for h in out["history"]] == ["task.created"]
        assert out["history"][0]["actor_name"] == "Deepika"

    def test_a_task_they_cannot_see_is_404(self, world):
        mine = world.add("member", title="Book dentist")
        for who in ("owner", "outsider", "assignee"):
            assert _detail(world.call(who, "GET", f"/{mine['id']}")) == (404, "No such task.")

    def test_a_task_that_does_not_exist_is_404(self, world):
        assert world.call("member", "GET", f"/{uuid.uuid4()}").status_code == 404

    def test_a_deleted_task_is_404(self, world):
        mine = world.add("member")
        world.call("member", "DELETE", f"/{mine['id']}")
        assert world.call("member", "GET", f"/{mine['id']}").status_code == 404


# ---------------------------------------------------------------------------
# PATCH /tasks/{id}
# ---------------------------------------------------------------------------


class TestChange:
    def test_the_fields_sent_change(self, world):
        mine = world.add("member", title="Pilot")
        r = world.call(
            "member",
            "PATCH",
            f"/{mine['id']}",
            json={"title": "Pilot plan", "priority": "high", "notes": "Draft"},
        )
        assert r.status_code == 200, r.text
        assert (r.json()["title"], r.json()["priority"], r.json()["notes"]) == (
            "Pilot plan",
            "high",
            "Draft",
        )

    def test_the_assignee_of_a_shared_task_changes_it_too(self, world):
        shared = world.add("member", assignee_id="assignee")
        r = world.call("assignee", "PATCH", f"/{shared['id']}", json={"priority": "low"})
        assert r.status_code == 200, r.text

    def test_a_stale_change_is_409(self, world):
        mine = world.add("member")
        world.call("member", "PATCH", f"/{mine['id']}", json={"priority": "low"})
        r = world.call(
            "member",
            "PATCH",
            f"/{mine['id']}",
            json={"priority": "high", "expect_updated_at": mine["updated_at"]},
        )
        assert r.status_code == 409

    def test_waiting_without_waiting_on_is_refused(self, world):
        mine = world.add("member")
        r = world.call("member", "PATCH", f"/{mine['id']}", json={"status": "waiting"})
        assert _detail(r) == (422, "Waiting needs who or what you are waiting on.")

    def test_waiting_on_a_person_keeps_their_name(self, world):
        mine = world.add("member")
        r = world.call(
            "member",
            "PATCH",
            f"/{mine['id']}",
            json={"status": "waiting", "waiting_on_id": ID["waiting"]},
        )
        assert r.json()["waiting_on"] == {"id": ID["waiting"], "label": "Wasim Waiting"}

    def test_free_text_replaces_the_person_waited_on(self, world):
        mine = world.add("member", status="waiting", waiting_on_id="waiting")
        r = world.call("member", "PATCH", f"/{mine['id']}", json={"waiting_on_text": "the CA"})
        assert r.json()["waiting_on"] == {"id": None, "label": "the CA"}

    def test_removing_the_person_waited_on_removes_their_name(self, world):
        mine = world.add("member", status="waiting", waiting_on_id="waiting")
        r = world.call("member", "PATCH", f"/{mine['id']}", json={"waiting_on_id": None})
        assert _detail(r) == (422, "Waiting needs who or what you are waiting on.")
        r = world.call(
            "member", "PATCH", f"/{mine['id']}", json={"status": "open", "waiting_on_id": None}
        )
        assert r.json()["waiting_on"] is None

    def test_an_unchanged_field_is_not_checked_again(self, world):
        shared = world.add(
            "member", assignee_id="assignee", status="waiting", waiting_on_id="waiting"
        )
        world.fake.profiles[ID["assignee"]]["tasks_level"] = 0
        world.fake.profiles[ID["waiting"]]["is_active"] = False
        r = world.call(
            "member",
            "PATCH",
            f"/{shared['id']}",
            json={
                "title": "Saved",
                "assignee_id": ID["assignee"],
                "waiting_on_id": ID["waiting"],
                "waiting_on_text": "ignored",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Saved"
        assert r.json()["waiting_on"] == {"id": ID["waiting"], "label": "Wasim Waiting"}

    def test_a_required_field_cannot_be_cleared(self, world):
        mine = world.add("member")
        r = world.call("member", "PATCH", f"/{mine['id']}", json={"title": None})
        assert _detail(r) == (422, "Title is missing or not valid.")

    def test_read_only_sight_cannot_change_it(self, world):
        project_task = world.add("member", links=[world.link(world.lumen, "tva_project")])
        assert world.call("lead", "GET", f"/{project_task['id']}").status_code == 200
        r = world.call("lead", "PATCH", f"/{project_task['id']}", json={"priority": "low"})
        assert _detail(r) == (403, "You can read this task but not change it.")
        r = world.call("lead", "POST", f"/{project_task['id']}/complete")
        assert _detail(r) == (403, "You can read this task but not change it.")

    def test_a_viewer_assignee_changes_the_status_only(self, world):
        task = world.add("owner", assignee_id="viewer", links=[world.link(world.acme, "company")])
        r = world.call("viewer", "PATCH", f"/{task['id']}", json={"title": "Mine now"})
        assert _detail(r) == (403, "Viewers may change only the status of a task assigned to them.")
        r = world.call("viewer", "PATCH", f"/{task['id']}", json={"status": "done"})
        assert (r.status_code, r.json()["status"]) == (200, "done")
        assert r.json()["may"] == {"edit": False, "status_only": True, "delete": False}

    def test_a_viewer_not_assigned_reads_only(self, world):
        task = world.add("owner", links=[world.link(world.acme, "company")])
        r = world.call("viewer", "PATCH", f"/{task['id']}", json={"status": "done"})
        assert _detail(r) == (403, "You can read this task but not change it.")

    def test_a_crm_task_reassigned_keeps_its_workspace_rule(self, world):
        task = world.add("owner", links=[world.link(world.acme, "company")])
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"assignee_id": ID["user"]})
        assert _detail(r) == (422, "The assignee must be a member of this workspace.")
        r = world.call("owner", "PATCH", f"/{task['id']}", json={"assignee_id": ID["member"]})
        assert r.json()["assignee"]["id"] == ID["member"]
        notices = list(world.fake.rows["crm_notifications"].values())
        assert [n["user_id"] for n in notices] == [ID["member"]]


# ---------------------------------------------------------------------------
# Complete, reopen and subtasks
# ---------------------------------------------------------------------------


class TestCompleteAndReopen:
    def test_complete_sets_completed_at_and_reopen_clears_it(self, world):
        mine = world.add("member")
        done = world.call("member", "POST", f"/{mine['id']}/complete").json()
        assert (done["status"], done["status_label"]) == ("done", "Done")
        assert done["completed_at"]
        again = world.call("member", "POST", f"/{mine['id']}/reopen").json()
        assert (again["status"], again["completed_at"]) == ("open", None)
        assert again["bucket"] == "later"

    def test_cancelled_reopens_to_open(self, world):
        mine = world.add("member")
        world.call("member", "PATCH", f"/{mine['id']}", json={"status": "cancelled"})
        assert world.tasks[mine["id"]]["completed_at"] is None
        again = world.call("member", "POST", f"/{mine['id']}/reopen").json()
        assert again["status"] == "open"


class TestParent:
    """FR-TASK-DATA-09."""

    def test_a_parent_that_has_a_parent_is_refused(self, world):
        top = world.add("member", title="Top")
        mid = world.add("member", title="Mid", parent_id=top["id"])
        r = world.call("member", "POST", "", json={"title": "Low", "parent_id": mid["id"]})
        assert _detail(r) == (422, "A subtask cannot have subtasks.")

    def test_a_task_with_subtasks_takes_no_parent(self, world):
        top = world.add("member", title="Top")
        world.add("member", title="Mid", parent_id=top["id"])
        other = world.add("member", title="Other")
        r = world.call("member", "PATCH", f"/{top['id']}", json={"parent_id": other["id"]})
        assert _detail(r) == (422, "A subtask cannot have subtasks.")

    def test_a_parent_they_cannot_see_is_404(self, world):
        theirs = world.add("owner", title="Private")
        r = world.call("member", "POST", "", json={"title": "x", "parent_id": theirs["id"]})
        assert _detail(r) == (404, "No such task.")
        mine = world.add("member")
        r = world.call("member", "PATCH", f"/{mine['id']}", json={"parent_id": theirs["id"]})
        assert _detail(r) == (404, "No such task.")

    def test_a_private_parent_shows_no_id_or_title(self, world):
        parent = world.add("owner", title="Secret plan", assignee_id="member")
        child = world.add("member", title="Step", parent_id=parent["id"])
        world.call("owner", "PATCH", f"/{parent['id']}", json={"assignee_id": ID["owner"]})
        out = world.call("member", "GET", f"/{child['id']}").json()
        assert out["parent"] == {"private": True}
        assert "Secret plan" not in str(out) and parent["id"] not in str(out)

    def test_a_subtask_they_cannot_see_is_not_counted(self, world):
        """FR-TASK-VIS-04: a count holds only what the reader may see."""
        parent = world.add("owner", title="Deal prep", links=[world.link(world.acme, "company")])
        world.add("member", title="Private step", parent_id=parent["id"])
        mine = world.call("member", "GET", f"/{parent['id']}").json()
        assert (mine["subtask_count"], len(mine["subtasks"])) == (1, 1)
        for who in ("labsadmin", "owner"):
            theirs = world.call(who, "GET", f"/{parent['id']}").json()
            assert (theirs["subtask_count"], theirs["subtasks"]) == (0, []), who
        [listed] = world.call("owner", "GET", "").json()["sections"]["later"]
        assert (listed["id"], listed["subtask_count"]) == (parent["id"], 0)

    def test_completing_a_parent_leaves_its_children_open(self, world):
        parent = world.add("member", title="Pilot")
        child = world.add("member", title="Plan", parent_id=parent["id"])
        world.call("member", "POST", f"/{parent['id']}/complete")
        assert world.tasks[child["id"]]["status"] == "open"


# ---------------------------------------------------------------------------
# DELETE /tasks/{id}, restore and the Trash — FR-TASK-DATA-05, -11
# ---------------------------------------------------------------------------


class TestDeleteAndRestore:
    def test_the_creator_deletes_and_restores(self, world):
        mine = world.add("member", assignee_id="assignee")
        assert world.call("member", "DELETE", f"/{mine['id']}").json() == {"deleted": True}
        world.tasks[mine["id"]]["deleted_at"] = "2026-10-01T10:00:00+00:00"
        trash = world.call("member", "GET", "/trash").json()
        assert [(t["id"], t["days_left"]) for t in trash] == [(mine["id"], 71)]
        back = world.call("member", "POST", f"/{mine['id']}/restore")
        assert (back.status_code, back.json()["id"]) == (200, mine["id"])
        assert world.tasks[mine["id"]]["deleted_at"] is None

    def test_the_assignee_neither_deletes_nor_restores(self, world):
        mine = world.add("member", assignee_id="assignee")
        r = world.call("assignee", "DELETE", f"/{mine['id']}")
        assert _detail(r) == (403, "Only the task's creator can delete it.")
        world.call("member", "DELETE", f"/{mine['id']}")
        r = world.call("assignee", "POST", f"/{mine['id']}/restore")
        assert _detail(r) == (403, "Only the task's creator can restore it.")
        assert world.call("assignee", "GET", "/trash").json() == []

    def test_restoring_a_live_task_is_404(self, world):
        mine = world.add("member")
        r = world.call("member", "POST", f"/{mine['id']}/restore")
        assert _detail(r) == (404, "No such task in the Trash.")

    def test_a_crm_task_follows_the_crm_rules(self, world):
        task = world.add("creator", links=[world.link(world.acme, "company")])
        r = world.call("member", "DELETE", f"/{task['id']}")
        assert _detail(r) == (
            403,
            "Only a workspace admin or the task's creator can delete this task.",
        )
        assert world.call("creator", "DELETE", f"/{task['id']}").json() == {"deleted": True}
        r = world.call("creator", "POST", f"/{task['id']}/restore")
        assert _detail(r) == (403, "Only a workspace admin can restore this task.")
        r = world.call("labsadmin", "POST", f"/{task['id']}/restore")
        assert r.status_code == 200, r.text

    def test_a_crm_task_with_no_crm_link_left_is_not_restored(self, world):
        task = world.add("owner", links=[world.link(world.acme, "company")])
        world.call("owner", "DELETE", f"/{task['id']}")
        for link in [
            lk for lk in world.fake.rows["task_links"].values() if lk["task_id"] == task["id"]
        ]:
            del world.fake.rows["task_links"][link["id"]]
        r = world.call("owner", "POST", f"/{task['id']}/restore")
        assert _detail(r) == (
            422,
            "This task has no linked record left, so it cannot be restored.",
        )


# ---------------------------------------------------------------------------
# Contracts §1 "Route order" — each fixed path reaches its own handler
# ---------------------------------------------------------------------------


class TestFixedPaths:
    def test_every_fixed_path_is_declared_before_the_id_routes(self):
        paths = [
            rc.path
            for rc in iter_route_contexts(app.routes)
            if isinstance(rc.original_route, APIRoute) and (rc.path or "").startswith(PREFIX)
        ]
        first_id = min(i for i, p in enumerate(paths) if "{id}" in p)
        fixed = [i for i, p in enumerate(paths) if "{" not in p]
        assert fixed and max(fixed) < first_id

    def test_trash(self, world):
        r = world.call("member", "GET", "/trash")
        assert (r.status_code, r.json()) == (200, [])

    def test_people(self, world):
        r = world.call("member", "GET", "/people", params={"q": "arun"})
        assert r.status_code == 200
        assert r.json() == [
            {"id": ID["assignee"], "display_name": "Arun Assignee", "email": "arun@nunnarilabs.com"}
        ]

    def test_linkable(self, world):
        r = world.call("member", "GET", "/linkable", params={"q": "acme"})
        assert r.status_code == 200
        assert {(x["record_type"], x["name"]) for x in r.json()["records"]} == {
            ("company", "Acme"),
            ("opportunity", "Acme RDX"),
        }

    def test_audience(self, world):
        r = world.call(
            "member", "POST", "/audience", json={"assignee_id": ID["assignee"], "links": []}
        )
        assert r.json() == {"visible_to": "Visible to: you and Arun Assignee"}

    def test_audience_names_only_someone_the_draft_may_go_to(self, world):
        for who in ("gone", "off"):
            r = world.call(
                "seller", "POST", "/audience", json={"assignee_id": ID[who], "links": []}
            )
            assert _detail(r) == (404, "No such person."), who
            assert world.fake.profiles[ID[who]]["display_name"] not in r.text, who
        r = world.call(
            "member",
            "POST",
            "/audience",
            json={"assignee_id": ID["user"], "links": [world.link(world.acme, "company")]},
        )
        assert _detail(r) == (404, "No such person.")

    def test_people_for_a_project_draft_have_phase_1(self, world):
        world.fake.profiles[ID["manager"]]["tasks_level"] = None
        r = world.call("owner", "GET", "/people", params={"project_id": world.lumen["id"]})
        names = [p["display_name"] for p in r.json()]
        assert "Mona Manager" not in names and "Omar Off" not in names
        assert {"Navaneeth", "Deepika", "Lakshmi Lead"} <= set(names)

    def test_a_non_uuid_id_never_reaches_the_id_handler(self, world):
        assert world.call("member", "GET", "/not-a-task").status_code == 422
