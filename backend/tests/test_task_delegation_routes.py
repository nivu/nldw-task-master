"""Handing work over, through the routes — spec 008 FR-TASK-DEL-01 to -08;
Journey 4.7; contracts §2; FR-TASK-ROLL-03, FR-TASK-IMP-06.

The messages go through the real sender (`services/tasks/notices.send`), run
at once by `TaskWorld`, with `notify.deliver` recorded instead of sent; the
sent markers are the fake store's `task_notice_sends`.

Acceptance criteria (spec §12, phase 2):
- "Assigning to someone else sends exactly one message; completing or
  handing back tells the creator once" — TestMessages.
- "Waiting needs Waiting on, limited to people the caller may assign; the
  person named gains no sight" — TestWaitingOn ("a waiting task idle for the
  set days shows 'nudge?' in the brief" is S2b's brief; the days are set
  here, TestSettings).
"""

from __future__ import annotations

import pytest
from crm_fake import as_
from tasks_fake import ID, NOW, TASK_PEOPLE, TaskWorld

from app.api.deps import CurrentUser
from app.domain.tasks import delegation, service
from app.domain.tasks.service import TaskActor
from app.schemas.tasks import TaskCreate
from app.services.crm import notifications
from app.services.tasks import notices

NOT_ON = "Tasks are not switched on for you yet."
#: The routes S2a adds, with a body each accepts.
PHASE_2 = (
    ("POST", "/{id}/accept", None),
    ("POST", "/{id}/hand-back", {"note": "Needs pricing from you"}),
    ("GET", "/{id}/comments", None),
    ("POST", "/{id}/comments", {"body": "On it"}),
    ("DELETE", "/{id}/comments/00000000-0000-4000-8000-000000000999", None),
    ("GET", "/settings", None),
    ("PATCH", "/settings", {"nudge_days": 7}),
)


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 2 on for everyone who has phase 1."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 2
    return w


def _email(key: str) -> str:
    return TASK_PEOPLE[ID[key]]["email"]


def _to(world: TaskWorld) -> list[str]:
    return [m.recipient_email for m in world.messages]


def _detail(r) -> tuple[int, str]:
    return r.status_code, r.json().get("detail")


def _ok(r) -> dict:
    assert r.status_code in (200, 201), r.text
    return r.json()


def _crm_link(world: TaskWorld) -> list[dict]:
    return [world.link(world.acme, "company")]


# ---------------------------------------------------------------------------
# FR-TASK-ROLL-03 — phase 2 is its own switch
# ---------------------------------------------------------------------------


class TestPhaseSwitch:
    @pytest.mark.parametrize(("method", "path", "body"), PHASE_2, ids=[p[1] for p in PHASE_2])
    def test_level_1_is_refused_on_every_phase_2_route(self, world, method, path, body):
        task = world.add("creator", assignee_id="assignee")
        world.fake.profiles[ID["assignee"]]["tasks_level"] = 1
        r = world.call("assignee", method, path.replace("{id}", task["id"]), json=body)
        assert _detail(r) == (403, NOT_ON)

    @pytest.mark.parametrize(("method", "path", "body"), PHASE_2, ids=[p[1] for p in PHASE_2])
    def test_level_0_is_refused_on_every_phase_2_route(self, world, method, path, body):
        task = world.add("creator")
        r = world.call("off", method, path.replace("{id}", task["id"]), json=body)
        assert _detail(r) == (403, NOT_ON)

    def test_an_actor_without_phase_2_sends_no_message(self, world):
        world.fake.profiles[ID["creator"]]["tasks_level"] = 1
        world.add("creator", assignee_id="assignee")
        assert world.messages == []

    def test_the_message_goes_whatever_the_assignees_level(self, world):
        world.fake.profiles[ID["assignee"]]["tasks_level"] = 1
        world.add("creator", assignee_id="assignee")
        assert _to(world) == [_email("assignee")]


# ---------------------------------------------------------------------------
# FR-TASK-DEL-01, -05, -07 — the messages
# ---------------------------------------------------------------------------


class TestMessages:
    def test_assigning_to_someone_else_sends_exactly_one_message(self, world):
        world.add("creator", title="Draft the RDX pilot plan", assignee_id="assignee")
        assert _to(world) == [_email("assignee")]
        [message] = world.messages
        assert message.subject == "Chitra Creator assigned you a task"
        assert "Draft the RDX pilot plan" in message.body

    def test_assigning_yourself_sends_nothing(self, world):
        task = world.add("creator")
        world.call("creator", "PATCH", f"/{task['id']}", json={"assignee_id": ID["creator"]})
        assert world.messages == []

    def test_reassigning_tells_only_the_new_assignee(self, world):
        task = world.add("creator", assignee_id="former")
        _ok(world.call("creator", "PATCH", f"/{task['id']}", json={"assignee_id": ID["assignee"]}))
        assert _to(world) == [_email("former"), _email("assignee")]

    def test_a_change_that_keeps_the_assignee_sends_nothing(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("creator", "PATCH", f"/{task['id']}", json={"priority": "high"}))
        _ok(world.call("assignee", "PATCH", f"/{task['id']}", json={"assignee_id": ID["assignee"]}))
        assert _to(world) == [_email("assignee")]

    def test_completing_tells_the_creator_once(self, world):
        task = world.add("creator", title="Send the deck", assignee_id="assignee")
        _ok(world.call("assignee", "POST", f"/{task['id']}/complete"))
        _ok(world.call("assignee", "POST", f"/{task['id']}/complete"))
        assert _to(world) == [_email("assignee"), _email("creator")]
        assert world.messages[-1].subject == "Arun Assignee completed your task"

    def test_completing_through_a_change_tells_the_creator(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("assignee", "PATCH", f"/{task['id']}", json={"status": "done"}))
        assert _to(world) == [_email("assignee"), _email("creator")]

    def test_the_creator_completing_tells_nobody(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("creator", "POST", f"/{task['id']}/complete"))
        assert _to(world) == [_email("assignee")]

    def test_handing_back_tells_the_creator_once(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("assignee", "POST", f"/{task['id']}/hand-back", json={"note": "Pricing?"}))
        assert _to(world) == [_email("assignee"), _email("creator")]
        assert world.messages[-1].subject == "Arun Assignee handed back your task"
        assert "Pricing?" not in world.messages[-1].body

    def test_a_retried_job_sends_nothing_twice(self, world):
        task = world.add("creator", assignee_id="assignee")
        [marker] = world.fake.rows["task_notice_sends"].values()
        assert (marker["task_id"], marker["event"], marker["recipient_id"]) == (
            task["id"],
            "assigned",
            ID["assignee"],
        )
        assert marker["sent_at"] and marker["channels"] == ["slack"]
        again = notices.send(task["id"], "assigned", ID["assignee"], marker["occurred_at"])
        assert again == {"sent": False, "channels": []}
        assert len(world.messages) == 1

    def test_the_job_carries_ids_only(self, world, monkeypatch):
        jobs: list[tuple] = []
        monkeypatch.setattr(notices, "enqueue", lambda *args: jobs.append(args))
        task = world.add("creator", title="Secret plan", assignee_id="assignee")
        [(task_id, event, recipient, occurred_at)] = jobs
        assert (task_id, event, recipient) == (task["id"], "assigned", ID["assignee"])
        assert occurred_at == str(world.tasks[task["id"]]["updated_at"])
        assert "Secret" not in repr(jobs)

    def test_an_imported_task_sends_no_notice(self, world):
        uploader = CurrentUser(dict(world.fake.profiles[ID["owner"]]))
        service.create_task(
            TaskActor(user=uploader, kind="import"),
            TaskCreate(title="From Life OS", source="import", assignee_id=ID["assignee"]),
        )
        assert world.messages == []

    def test_a_crm_task_keeps_its_bell_notice_too(self, world):
        task = world.add("owner", assignee_id="member", links=_crm_link(world))
        assert _to(world) == [_email("member")]
        bells = [
            n
            for n in world.fake.rows["crm_notifications"].values()
            if n["record_id"] == task["id"] and n["kind"] == "assigned"
        ]
        assert [b["user_id"] for b in bells] == [ID["member"]]


# ---------------------------------------------------------------------------
# FR-TASK-DEL-01, -02, -05 through the CRM's task routes
# ---------------------------------------------------------------------------

LABS = "?workspace=labs"


class TestCrmRoutes:
    def _crm(self, world, who: str, method: str, path: str, **kwargs):
        return world.http.request(
            method, f"/api/v1/crm/tasks{path}{LABS}", headers=as_(ID[who]), **kwargs
        )

    def _made(self, world, who: str, assignee: str) -> dict:
        r = self._crm(
            world,
            who,
            "POST",
            "",
            json={"title": "Send the deck", "links": _crm_link(world), "assignee_id": ID[assignee]},
        )
        assert r.status_code == 201, r.text
        return r.json()

    def test_creating_one_for_someone_else_sends_one_message(self, world):
        task = self._made(world, "owner", "member")
        assert _to(world) == [_email("member")]
        assert world.tasks[task["id"]]["assigned_by"] == ID["owner"]

    def test_reassigning_sends_one_message_and_is_new_again(self, world):
        task = world.add("creator", assignee_id="member", links=_crm_link(world))
        _ok(world.call("member", "POST", f"/{task['id']}/accept"))
        r = self._crm(world, "owner", "PATCH", f"/{task['id']}", json={"assignee_id": ID["seller"]})
        assert r.status_code == 200, r.text
        assert _to(world) == [_email("member"), _email("seller")]
        row = world.tasks[task["id"]]
        assert (row["assigned_by"], row["accepted_at"]) == (ID["owner"], None)
        assert _ok(world.call("seller", "GET", f"/{task['id']}"))["new"] is True

    def test_the_assignee_changing_it_accepts(self, world):
        task = world.add("creator", assignee_id="member", links=_crm_link(world))
        r = self._crm(world, "member", "PATCH", f"/{task['id']}", json={"priority": "high"})
        assert r.status_code == 200, r.text
        assert world.tasks[task["id"]]["accepted_at"] is not None

    def test_completing_accepts_and_tells_the_creator(self, world):
        task = world.add("creator", assignee_id="member", links=_crm_link(world))
        r = self._crm(world, "member", "POST", f"/{task['id']}/complete")
        assert r.status_code == 200, r.text
        assert _to(world) == [_email("member"), _email("creator")]
        assert world.tasks[task["id"]]["accepted_at"] is not None

    def test_an_actor_without_phase_2_sends_no_message(self, world):
        world.fake.profiles[ID["owner"]]["tasks_level"] = 1
        self._made(world, "owner", "member")
        assert world.messages == []


# ---------------------------------------------------------------------------
# FR-TASK-VIS-04 — a message only to someone who may still see the task
# ---------------------------------------------------------------------------


class TestMessageSight:
    def test_a_creator_who_left_the_workspace_is_not_told(self, world):
        task = world.add("removed", assignee_id="member", links=_crm_link(world))
        del world.fake.rows["crm_memberships"][world.removed["id"]]
        _ok(world.call("member", "POST", f"/{task['id']}/complete"))
        assert _to(world) == [_email("member")]
        events = [m["event"] for m in world.fake.rows["task_notice_sends"].values()]
        assert events == ["assigned"]

    def test_an_assignee_replaced_before_the_job_ran_is_not_told(self, world, monkeypatch):
        jobs: list[tuple] = []
        monkeypatch.setattr(notices, "enqueue", lambda *args: jobs.append(args))
        # A CRM task: Deepika, a member, still sees it once it is Tamil's.
        task = world.add("owner", assignee_id="member", links=_crm_link(world))
        _ok(world.call("owner", "PATCH", f"/{task['id']}", json={"assignee_id": ID["seller"]}))
        for job in jobs:
            notices.send(*job)
        assert _to(world) == [_email("seller")]


# ---------------------------------------------------------------------------
# FR-TASK-DEL-02 — New until the assignee acts
# ---------------------------------------------------------------------------


class TestNew:
    def _new(self, world, who: str, task_id: str) -> bool:
        return _ok(world.call(who, "GET", f"/{task_id}"))["new"]

    def test_a_task_from_someone_else_is_new_until_accepted(self, world):
        task = world.add("creator", assignee_id="assignee")
        assert task["new"] is False  # the creator's view: it is theirs
        mine = _ok(world.call("assignee", "GET", ""))["sections"]["later"]
        assert [(t["id"], t["new"]) for t in mine] == [(task["id"], True)]
        accepted = _ok(world.call("assignee", "POST", f"/{task['id']}/accept"))
        assert (accepted["new"], accepted["accepted_at"]) == (False, NOW.isoformat())
        assert not self._new(world, "assignee", task["id"])

    def test_new_shows_only_once_phase_2_is_on_for_the_assignee(self, world):
        task = world.add("creator", assignee_id="assignee")
        world.fake.profiles[ID["assignee"]]["tasks_level"] = 1
        assert not self._new(world, "assignee", task["id"])

    def test_only_the_assignee_accepts(self, world):
        task = world.add("creator", assignee_id="assignee")
        r = world.call("creator", "POST", f"/{task['id']}/accept")
        assert _detail(r) == (403, delegation.NOT_ASSIGNEE)

    def test_accepting_a_task_that_is_not_new_changes_nothing(self, world):
        task = world.add("creator")
        before = dict(world.tasks[task["id"]])
        assert _ok(world.call("creator", "POST", f"/{task['id']}/accept"))["new"] is False
        assert world.tasks[task["id"]] == before

    @pytest.mark.parametrize(
        ("method", "suffix", "body"),
        [
            ("PATCH", "", {"priority": "high"}),
            ("POST", "/comments", {"body": "On it"}),
            ("POST", "/complete", None),
        ],
        ids=["a change", "a comment", "completing"],
    )
    def test_the_assignee_acting_accepts(self, world, method, suffix, body):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("assignee", method, f"/{task['id']}{suffix}", json=body))
        assert world.tasks[task["id"]]["accepted_at"] == NOW.isoformat()

    def test_the_creator_changing_it_does_not_accept(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("creator", "PATCH", f"/{task['id']}", json={"priority": "high"}))
        assert self._new(world, "assignee", task["id"])

    def test_a_reassignment_is_new_again(self, world):
        task = world.add("creator", assignee_id="former")
        _ok(world.call("former", "POST", f"/{task['id']}/accept"))
        _ok(world.call("creator", "PATCH", f"/{task['id']}", json={"assignee_id": ID["assignee"]}))
        row = world.tasks[task["id"]]
        assert (row["assigned_by"], row["accepted_at"]) == (ID["creator"], None)
        assert self._new(world, "assignee", task["id"])

    def test_a_closed_task_is_not_new(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("creator", "POST", f"/{task['id']}/complete"))
        assert not self._new(world, "assignee", task["id"])

    def test_a_hand_back_is_new_for_the_creator(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("assignee", "POST", f"/{task['id']}/hand-back", json={"note": "x"}))
        assert self._new(world, "creator", task["id"])

    def test_a_self_assignment_records_nobody(self, world):
        task = world.add("creator")
        assert world.tasks[task["id"]]["assigned_by"] is None


# ---------------------------------------------------------------------------
# FR-TASK-DEL-03 — handing back
# ---------------------------------------------------------------------------


class TestHandBack:
    def test_the_creator_is_the_assignee_again_and_the_note_is_kept(self, world):
        task = world.add("creator", assignee_id="assignee")
        out = _ok(
            world.call(
                "assignee", "POST", f"/{task['id']}/hand-back", json={"note": "needs pricing"}
            )
        )
        assert out["assignee"]["id"] == ID["creator"]
        row = world.tasks[task["id"]]
        assert (row["assignee_id"], row["assigned_by"]) == (ID["creator"], ID["assignee"])
        comments = _ok(world.call("creator", "GET", f"/{task['id']}/comments"))
        assert [(c["kind"], c["body"], c["author"]["id"]) for c in comments] == [
            ("hand_back", "needs pricing", ID["assignee"])
        ]

    def test_the_former_assignee_loses_sight(self, world):
        task = world.add("creator", assignee_id="assignee")
        _ok(world.call("assignee", "POST", f"/{task['id']}/hand-back", json={"note": "x"}))
        assert world.call("assignee", "GET", f"/{task['id']}").status_code == 404

    def test_the_creator_cannot_hand_back(self, world):
        task = world.add("creator", assignee_id="assignee")
        r = world.call("creator", "POST", f"/{task['id']}/hand-back", json={"note": "x"})
        assert _detail(r) == (403, delegation.NOT_ASSIGNEE)
        own = world.add("creator")
        r = world.call("creator", "POST", f"/{own['id']}/hand-back", json={"note": "x"})
        assert _detail(r) == (403, delegation.OWN_TASK)

    def test_another_member_of_a_crm_task_cannot_hand_it_back(self, world):
        task = world.add("owner", assignee_id="member", links=_crm_link(world))
        r = world.call("labsadmin", "POST", f"/{task['id']}/hand-back", json={"note": "x"})
        assert _detail(r) == (403, delegation.NOT_ASSIGNEE)

    def test_a_crm_task_handed_back_keeps_the_note_in_its_workspace(self, world):
        task = world.add("owner", assignee_id="member", links=_crm_link(world))
        _ok(world.call("member", "POST", f"/{task['id']}/hand-back", json={"note": "x"}))
        [comment] = world.fake.rows["task_comments"].values()
        assert comment["workspace_id"] == world.labs["id"]

    @pytest.mark.parametrize("note", ["", "x" * 2001])
    def test_the_note_is_one_to_2000_characters(self, world, note):
        task = world.add("creator", assignee_id="assignee")
        r = world.call("assignee", "POST", f"/{task['id']}/hand-back", json={"note": note})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# FR-TASK-DEL-04 — comments
# ---------------------------------------------------------------------------


class TestComments:
    def _comment(self, world, who: str, task_id: str, body: str = "On it") -> dict:
        return _ok(world.call(who, "POST", f"/{task_id}/comments", json={"body": body}))

    def test_visible_to_whoever_sees_the_task(self, world):
        task = world.add("owner", assignee_id="member", links=_crm_link(world))
        made = self._comment(world, "member", task["id"], "Called them")
        assert (made["kind"], made["author"]["name"], made["may_delete"]) == (
            "comment",
            "Deepika",
            True,
        )
        for who in ("owner", "labsadmin", "viewer", "member"):
            listed = _ok(world.call(who, "GET", f"/{task['id']}/comments"))
            assert [c["body"] for c in listed] == ["Called them"], who
        for who in ("outsider", "manager", "user"):
            assert world.call(who, "GET", f"/{task['id']}/comments").status_code == 404, who

    def test_a_personal_tasks_comments_reach_only_its_creator_and_assignee(self, world):
        task = world.add("creator", assignee_id="former")
        self._comment(world, "creator", task["id"])
        _ok(world.call("creator", "PATCH", f"/{task['id']}", json={"assignee_id": ID["assignee"]}))
        assert len(_ok(world.call("assignee", "GET", f"/{task['id']}/comments"))) == 1
        for who in ("former", "lead", "owner", "waiting"):
            assert world.call(who, "GET", f"/{task['id']}/comments").status_code == 404, who
        [comment] = world.fake.rows["task_comments"].values()
        assert comment["workspace_id"] is None

    def test_deletable_by_their_author_only(self, world):
        task = world.add("owner", assignee_id="member", links=_crm_link(world))
        made = self._comment(world, "member", task["id"])
        listed = _ok(world.call("owner", "GET", f"/{task['id']}/comments"))
        assert listed[0]["may_delete"] is False
        r = world.call("owner", "DELETE", f"/{task['id']}/comments/{made['id']}")
        assert _detail(r) == (403, service.COMMENT_AUTHOR)
        r = world.call("member", "DELETE", f"/{task['id']}/comments/{made['id']}")
        assert (r.status_code, r.json()) == (200, {"deleted": True})
        assert _ok(world.call("owner", "GET", f"/{task['id']}/comments")) == []
        again = world.call("member", "DELETE", f"/{task['id']}/comments/{made['id']}")
        assert _detail(again) == (404, service.NO_SUCH_COMMENT)

    def test_a_comment_of_another_task_is_no_such_comment(self, world):
        one = world.add("creator")
        other = world.add("creator")
        made = self._comment(world, "creator", one["id"])
        r = world.call("creator", "DELETE", f"/{other['id']}/comments/{made['id']}")
        assert _detail(r) == (404, service.NO_SUCH_COMMENT)

    def test_comments_are_audited_and_never_edited(self, world):
        task = world.add("creator")
        made = self._comment(world, "creator", task["id"])
        audited = [a for a in world.fake.audit if a["target_table"] == "task_comments"]
        assert [(a["action"], a["target_id"]) for a in audited] == [
            ("task_comment.created", made["id"])
        ]
        with pytest.raises(Exception, match="A comment cannot be changed"):
            world.fake.tasks.apply(
                {"user_id": ID["creator"], "kind": "user", "channel": "web"},
                [
                    {
                        "op": "update",
                        "table": "task_comments",
                        "id": made["id"],
                        "values": {"body": "Edited"},
                    }
                ],
            )

    def test_read_only_sight_cannot_comment(self, world):
        task = world.add("member", links=[world.link(world.lumen, "tva_project")])
        assert world.call("lead", "GET", f"/{task['id']}/comments").status_code == 200
        r = world.call("lead", "POST", f"/{task['id']}/comments", json={"body": "x"})
        assert _detail(r) == (403, service.READ_ONLY)

    def test_a_viewer_assigned_a_crm_task_may_comment(self, world):
        task = world.add("owner", assignee_id="viewer", links=_crm_link(world))
        self._comment(world, "viewer", task["id"])

    @pytest.mark.parametrize("body", ["", "   ", "x" * 2001])
    def test_a_comment_is_one_to_2000_characters(self, world, body):
        task = world.add("creator")
        r = world.call("creator", "POST", f"/{task['id']}/comments", json={"body": body})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# FR-TASK-DEL-06 — Waiting on
# ---------------------------------------------------------------------------


class TestWaitingOn:
    def test_waiting_on_someone_the_caller_may_assign(self, world):
        task = world.add("creator", status="waiting", waiting_on_id="waiting")
        assert task["waiting_on"] == {"id": ID["waiting"], "label": "Wasim Waiting"}

    def test_waiting_on_someone_the_caller_may_not_assign_is_refused(self, world):
        project = [world.link(world.lumen, "tva_project")]
        task = world.add("member", links=project)
        r = world.call(
            "member",
            "PATCH",
            f"/{task['id']}",
            json={"status": "waiting", "waiting_on_id": ID["user"]},
        )
        assert _detail(r) == (422, delegation.WAITING_ON)
        r = world.call(
            "member",
            "POST",
            "",
            json={
                "title": "x",
                "links": project,
                "status": "waiting",
                "waiting_on_id": ID["user"],
            },
        )
        assert _detail(r) == (422, delegation.WAITING_ON)

    def test_waiting_on_someone_without_my_tasks_is_refused(self, world):
        r = world.call(
            "creator",
            "POST",
            "",
            json={"title": "x", "status": "waiting", "waiting_on_id": ID["off"]},
        )
        assert r.status_code == 422

    def test_the_person_named_gains_no_sight(self, world):
        task = world.add("creator", status="waiting", waiting_on_id="waiting")
        assert world.call("waiting", "GET", f"/{task['id']}").status_code == 404
        assert world.call("waiting", "GET", f"/{task['id']}/comments").status_code == 404
        mine = _ok(world.call("waiting", "GET", ""))
        assert all(not tasks for tasks in mine["sections"].values())
        assert world.messages == []

    def test_waiting_needs_waiting_on(self, world):
        task = world.add("creator")
        r = world.call("creator", "PATCH", f"/{task['id']}", json={"status": "waiting"})
        assert _detail(r) == (422, service.WAITING)


# ---------------------------------------------------------------------------
# FR-TASK-DEL-06 — the person's nudge days; contracts §1 "Route order"
# ---------------------------------------------------------------------------


class TestSettings:
    def test_the_default_is_five_days(self, world):
        assert _ok(world.call("creator", "GET", "/settings")) == {"nudge_days": 5}

    def test_the_person_sets_their_own(self, world):
        assert _ok(world.call("creator", "PATCH", "/settings", json={"nudge_days": 12})) == {
            "nudge_days": 12
        }
        assert _ok(world.call("creator", "GET", "/settings")) == {"nudge_days": 12}
        assert _ok(world.call("assignee", "GET", "/settings")) == {"nudge_days": 5}

    @pytest.mark.parametrize("days", [0, 31, -1, 100])
    def test_outside_1_to_30_is_refused(self, world, days):
        r = world.call("creator", "PATCH", "/settings", json={"nudge_days": days})
        assert r.status_code == 422

    @pytest.mark.parametrize("days", [1, 30])
    def test_the_ends_are_allowed(self, world, days):
        r = world.call("creator", "PATCH", "/settings", json={"nudge_days": days})
        assert _ok(r) == {"nudge_days": days}

    def test_get_settings_reaches_its_own_handler(self, world):
        r = world.call("creator", "GET", "/settings")
        assert r.status_code == 200
        assert set(r.json()) == {"nudge_days"}

    def test_patch_settings_reaches_its_own_handler(self, world):
        r = world.call("creator", "PATCH", "/settings", json={})
        assert _ok(r) == {"nudge_days": 5}


# ---------------------------------------------------------------------------
# FR-TASK-DEL-08 — no new reminder
# ---------------------------------------------------------------------------


class TestNoNewReminder:
    def test_a_task_with_no_workspace_sends_no_reminder(self, world):
        personal = world.add("member", title="Book dentist", due_on="2026-10-19")
        crm = world.add("member", due_on="2026-10-19", links=_crm_link(world))
        world.messages.clear()
        counts = notifications.task_reminders(world.labs, NOW)
        assert counts["overdue"] == 1
        reminded = {n["record_id"] for n in world.fake.rows["crm_notifications"].values()}
        assert crm["id"] in reminded
        assert personal["id"] not in reminded
        assert world.messages == []


def test_the_cast_is_signed_in_as_themselves(world):
    """The fixture raises the cast's level, not their identity."""
    r = world.http.get("/api/v1/me", headers=as_(ID["creator"]))
    assert r.json()["tasks"] == {"level": 2}
