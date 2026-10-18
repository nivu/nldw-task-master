"""The 008/009 interface `create_task` — spec 008 §14, contracts §4; plan §9
rule 2; FR-TASK-DATA-05 to -09, -13, -15, FR-TASK-ROLL-04; CT-2.

The shape is pinned here, character for character with contracts §4:
`create_task(actor, payload) -> dict`, `TaskActor(user, kind, channel,
suggestion_id=None)` with `as_rpc()`, and `TaskCreate` with the rule-2
fields (title to source_ref) and the optional fields of S1a. After S1a
neither changes; 009 relies on it.

Then the behaviour of contracts §4 steps 1-8, against the in-memory store
(`tests/tasks_fake.py` fakes `task_apply` and the deferred check).

Acceptance criteria (spec §12, phase 1):
- "An Academy task cannot link a TVA project (422); linking a project the
  caller cannot see answers 404; linking an archived project is refused; a
  second project link on a task is refused; a TVA user can link a project
  they are or were allocated to" — TestProjectLink.
- "A crm_only account cannot be assigned a project task with no CRM link,
  and can be assigned a CRM task of its workspace" — TestAssignee.
- "Assigning a task with no workspace to someone without phase 1 is refused
  422 with their name" — TestAssignee.
- "A tag over 40 characters, or an eleventh tag on a task, is refused" —
  TestFields.
- "Setting a parent the caller cannot see answers 404" — TestParent.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing
import uuid
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

import pytest
from crm_fake import FakeCrmStore
from pydantic import ValidationError

from app.api.deps import CurrentUser
from app.domain.tasks import service
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import TaskActor, create_task
from app.schemas.tasks import TaskCreate, TaskLinkIn
from app.services.crm import CrmRefused

NOW = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)  # Tuesday 20 October 2026, noon IST


def _person(name: str, **values) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "email": f"{name.lower()}@nunnarilabs.com",
        "display_name": name,
        "role": "user",
        "lead_id": None,
        "is_active": True,
        "account_type": "staff",
        "timezone": "Asia/Kolkata",
        "tasks_level": None,
        **values,
    }


NAV = _person("Navaneeth", role="admin", tasks_level=1)
TIM = _person("Timothy", tasks_level=1)
VIN = _person("Vinita", role="admin")
DEV = _person("Devansh", tasks_level=1)
SEL = _person("Tamil", account_type="crm_only", tasks_level=1)


class World:
    def __init__(self, monkeypatch) -> None:
        self.fake = FakeCrmStore().install(monkeypatch)
        self.fake.profiles = {p["id"]: dict(p) for p in (NAV, TIM, VIN, DEV, SEL)}
        self.labs = self.fake.add_workspace("labs", "Nunnari Labs", tva_linked=True)
        self.academy = self.fake.add_workspace("academy", "Nunnari Academy B2B")
        for ws in (self.labs, self.academy):
            self.fake.add_deal_lists(ws)
        self.fake.add_member(self.labs, NAV["id"], "admin")
        self.fake.add_member(self.academy, NAV["id"], "admin")
        self.fake.add_member(self.labs, TIM["id"], "member")
        self.fake.add_member(self.labs, SEL["id"], "member")
        self.fake.add_member(self.labs, DEV["id"], "viewer")
        self.acme = self._company(self.labs, "Acme")
        self.college = self._company(self.academy, "College")
        self.project = self.fake.add_project("Lumen", status="confirmed")
        monkeypatch.setattr(service, "_now", lambda: NOW)

    def _company(self, ws, name):
        return self.fake.add(
            "crm_companies",
            workspace_id=ws["id"],
            name=name,
            deleted_at=None,
            created_at=NOW.isoformat(),
        )

    @property
    def tasks(self):
        return self.fake.rows["tasks"]


@pytest.fixture
def world(monkeypatch) -> World:
    return World(monkeypatch)


def actor(person, **values) -> TaskActor:
    return TaskActor(user=CurrentUser(dict(person)), **values)


def link(record_type: str, record_id: str) -> TaskLinkIn:
    return TaskLinkIn(record_type=record_type, record_id=record_id)


def make(**values) -> TaskCreate:
    return TaskCreate(**{"title": "Call Deepak", "source": "manual", **values})


def refused(call) -> tuple[int, str]:
    with pytest.raises(TaskRefused) as caught:
        call()
    return caught.value.status, caught.value.detail


# ---------------------------------------------------------------------------
# The shape — contracts §4, fixed in S1a
# ---------------------------------------------------------------------------


class TestTheShape:
    def test_create_task_signature(self):
        sig = inspect.signature(create_task)
        assert list(sig.parameters) == ["actor", "payload"]
        hints = typing.get_type_hints(create_task)
        assert hints == {"actor": TaskActor, "payload": TaskCreate, "return": dict}

    def test_task_actor_fields(self):
        assert dataclasses.is_dataclass(TaskActor)
        assert TaskActor.__dataclass_params__.frozen
        fields = {f.name: f for f in dataclasses.fields(TaskActor)}
        assert list(fields) == ["user", "kind", "channel", "suggestion_id"]
        assert fields["user"].default is dataclasses.MISSING
        assert (fields["kind"].default, fields["channel"].default) == ("user", None)
        assert fields["suggestion_id"].default is None
        hints = typing.get_type_hints(TaskActor)
        assert hints["user"] is CurrentUser
        assert hints["kind"] == Literal["user", "system", "import"]
        assert hints["channel"] == Literal["web", "mcp"] | None
        assert hints["suggestion_id"] == UUID | None

    def test_task_link_in(self):
        assert TaskLinkIn.model_config["extra"] == "forbid"
        fields = TaskLinkIn.model_fields
        assert list(fields) == ["record_type", "record_id"]
        assert (
            fields["record_type"].annotation
            == Literal["company", "person", "opportunity", "campaign", "tva_project"]
        )
        assert fields["record_id"].annotation is UUID

    def test_task_create_fields_and_defaults(self):
        assert TaskCreate.model_config["extra"] == "forbid"
        assert TaskCreate.model_config["str_strip_whitespace"] is True
        expected = {
            # The rule-2 fields (plan §9 rule 2).
            "title": (str, ...),
            "notes": (str | None, None),
            "due_on": (date | None, None),
            "bucket": (Literal["today", "this_week", "this_month", "later"] | None, None),
            "priority": (Literal["urgent", "high", "normal", "low"], "normal"),
            "assignee_id": (UUID | None, None),
            "workspace_id": (UUID | None, None),
            "links": (list[TaskLinkIn], list),
            "source": (
                Literal[
                    "manual",
                    "nldw",
                    "voice",
                    "email_commitment",
                    "meeting_action",
                    "rule",
                    "import",
                ],
                ...,
            ),
            "source_ref": (str | None, None),
            # The optional fields of S1a.
            "tags": (list[str], list),
            "parent_id": (UUID | None, None),
            "status": (Literal["open", "waiting", "done", "cancelled"], "open"),
            "waiting_on_id": (UUID | None, None),
            "waiting_on_text": (str | None, None),
            "category_id": (UUID | None, None),
            "completed_at": (datetime | None, None),
        }
        fields = TaskCreate.model_fields
        assert list(fields) == list(expected)
        for name, (annotation, default) in expected.items():
            field = fields[name]
            assert field.annotation == annotation, name
            if default is ...:
                assert field.is_required(), name
            elif default is list:
                assert field.default_factory is list, name
            else:
                assert field.default == default, name

    @pytest.mark.parametrize(
        ("name", "limits"),
        [
            ("title", {"min_length": 1, "max_length": 200}),
            ("notes", {"max_length": 20_000}),
            ("links", {"max_length": 20}),
            ("source_ref", {"max_length": 500}),
            ("tags", {"max_length": 10}),
            ("waiting_on_text", {"max_length": 200}),
        ],
    )
    def test_task_create_limits(self, name, limits):
        found = {
            k: getattr(m, k)
            for m in TaskCreate.model_fields[name].metadata
            for k in ("min_length", "max_length")
            if hasattr(m, k)
        }
        assert found == limits

    def test_as_rpc_without_and_with_a_suggestion(self):
        user = CurrentUser(dict(NAV))
        assert TaskActor(user=user, channel="web").as_rpc() == {
            "user_id": NAV["id"],
            "kind": "user",
            "channel": "web",
        }
        suggestion = uuid.uuid4()
        assert TaskActor(user=user, channel="mcp", suggestion_id=suggestion).as_rpc() == {
            "user_id": NAV["id"],
            "kind": "user",
            "channel": "mcp",
            "suggestion_id": str(suggestion),
        }

    def test_task_refused_is_a_crm_refusal(self):
        assert issubclass(TaskRefused, CrmRefused)


# ---------------------------------------------------------------------------
# Behaviour — contracts §4 steps 1-8
# ---------------------------------------------------------------------------


class TestOnlyTheRule2Fields:
    def test_a_call_with_title_and_source_makes_a_private_task(self, world):
        out = create_task(actor(NAV), TaskCreate(title="Book dentist", source="manual"))
        row = world.tasks[out["id"]]
        assert (row["workspace_id"], row["status"], row["bucket"], row["priority"]) == (
            None,
            "open",
            "later",
            "normal",
        )
        assert (row["created_by"], row["assignee_id"], row["source"]) == (
            NAV["id"],
            NAV["id"],
            "manual",
        )
        assert out["kind"] == "private"
        assert out["visible_to"] == "Visible to: only you"

    def test_the_bucket_is_derived_from_the_due_date_in_their_day(self, world):
        out = create_task(actor(NAV), make(due_on=date(2026, 10, 20)))
        assert world.tasks[out["id"]]["bucket"] == "today"

    def test_returns_task_out(self, world):
        out = create_task(actor(NAV), make(tags=["sales"]))
        assert set(out) == {
            "id",
            "kind",
            "title",
            "notes",
            "status",
            "status_label",
            "bucket",
            "bucket_label",
            "effective_bucket",
            "due_on",
            "days_overdue",
            "priority",
            "priority_label",
            "tags",
            "assignee",
            "created_by",
            "waiting_on",
            "parent",
            "subtask_count",
            "workspace",
            "category",
            "links",
            "project",
            "source",
            "source_ref",
            "visible_to",
            "may",
            "completed_at",
            "created_at",
            "updated_at",
            # Phase 2 (contracts §0.1).
            "new",
            "accepted_at",
            # Phase 3 (contracts §0.1).
            "recurrence",
        }
        assert (out["status_label"], out["bucket_label"], out["priority_label"]) == (
            "Open",
            "Later",
            "Normal",
        )
        assert out["assignee"] == {"id": NAV["id"], "name": "Navaneeth"}
        assert out["may"] == {"edit": True, "status_only": False, "delete": True}


class TestWorkspace:
    """Step 1 — derived from the CRM links."""

    def test_a_crm_link_makes_a_crm_task_of_its_workspace(self, world):
        out = create_task(
            actor(TIM, channel="web"), make(links=[link("company", world.acme["id"])])
        )
        row = world.tasks[out["id"]]
        assert row["workspace_id"] == world.labs["id"]
        [lk] = [lk for lk in world.fake.rows["task_links"].values() if lk["task_id"] == row["id"]]
        assert (lk["workspace_id"], lk["record_type"]) == (world.labs["id"], "company")
        assert out["kind"] == "crm"
        assert out["workspace"]["key"] == "labs"
        assert out["links"][0]["name"] == "Acme"
        assert out["visible_to"] == "Visible to: Nunnari Labs members"

    def test_a_matching_workspace_id_is_accepted(self, world):
        out = create_task(
            actor(TIM),
            make(workspace_id=world.labs["id"], links=[link("company", world.acme["id"])]),
        )
        assert world.tasks[out["id"]]["workspace_id"] == world.labs["id"]

    def test_a_workspace_id_with_no_crm_link_is_refused(self, world):
        assert refused(lambda: create_task(actor(NAV), make(workspace_id=world.labs["id"]))) == (
            422,
            "A CRM task needs at least one linked record.",
        )

    def test_a_workspace_id_that_differs_is_refused(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(NAV),
            make(workspace_id=world.academy["id"], links=[link("company", world.acme["id"])]),
        )
        assert refused(call)[0] == 422

    def test_records_of_two_workspaces_are_refused(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(NAV),
            make(links=[link("company", world.acme["id"]), link("company", world.college["id"])]),
        )
        assert refused(call) == (422, "A task cannot link records of two workspaces.")

    def test_a_record_outside_their_workspaces_is_404(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(TIM), make(links=[link("company", world.college["id"])])
        )
        assert refused(call) == (404, "No such company.")

    def test_a_viewer_cannot_link_a_record(self, world):
        """FR-TASK-DATA-13 — Admin or Member only; a Viewer is answered 404."""
        call = lambda: create_task(  # noqa: E731
            actor(DEV), make(links=[link("company", world.acme["id"])])
        )
        assert refused(call) == (404, "No such company.")

    def test_repeated_links_are_refused_before_anything_is_written(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(NAV),
            make(links=[link("company", world.acme["id"]), link("company", world.acme["id"])]),
        )
        assert refused(call) == (422, "The same record is linked twice.")
        assert world.fake.tasks.applied == [] and world.tasks == {}


class TestProjectLink:
    """Step 2 — one project, visible to the actor, not archived, Labs only."""

    def test_a_project_task(self, world):
        out = create_task(actor(NAV), make(links=[link("tva_project", world.project["id"])]))
        [lk] = list(world.fake.rows["task_links"].values())
        assert (lk["workspace_id"], lk["project_id"]) == (None, world.project["id"])
        assert out["kind"] == "project"
        assert out["project"] == {"id": world.project["id"], "name": "Lumen"}
        assert out["links"] == []

    def test_a_labs_task_may_link_a_project(self, world):
        out = create_task(
            actor(NAV),
            make(
                links=[link("company", world.acme["id"]), link("tva_project", world.project["id"])]
            ),
        )
        assert out["kind"] == "crm" and out["project"]["name"] == "Lumen"

    def test_an_academy_task_cannot(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(NAV),
            make(
                links=[
                    link("company", world.college["id"]),
                    link("tva_project", world.project["id"]),
                ]
            ),
        )
        assert refused(call) == (422, "Only Nunnari Labs tasks can link a TVA project.")

    def test_a_project_they_cannot_see_is_404(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(TIM), make(links=[link("tva_project", world.project["id"])])
        )
        assert refused(call) == (404, "No such project.")

    def test_a_user_links_a_project_they_were_allocated_to(self, world):
        world.fake.add_allocation(world.project, TIM["id"], released_at="2026-10-01T00:00:00Z")
        out = create_task(actor(TIM), make(links=[link("tva_project", world.project["id"])]))
        assert out["project"]["name"] == "Lumen"

    def test_an_archived_project_is_refused(self, world):
        world.project["is_archived"] = True
        call = lambda: create_task(  # noqa: E731
            actor(NAV), make(links=[link("tva_project", world.project["id"])])
        )
        assert refused(call) == (422, "That project is archived.")

    def test_a_second_project_is_refused(self, world):
        other = world.fake.add_project("Orbit", status="confirmed")
        call = lambda: create_task(  # noqa: E731
            actor(NAV),
            make(
                links=[link("tva_project", world.project["id"]), link("tva_project", other["id"])]
            ),
        )
        assert refused(call) == (422, "A task can link only one TVA project.")


class TestAssignee:
    """Step 3 — FR-TASK-DATA-07, FR-TASK-ROLL-04."""

    def test_someone_with_phase_one_gets_a_shared_personal_task(self, world):
        out = create_task(actor(NAV), make(assignee_id=TIM["id"]))
        assert out["kind"] == "shared_personal"
        assert out["visible_to"] == "Visible to: you and Timothy"

    def test_someone_without_phase_one_is_refused_by_name(self, world):
        call = lambda: create_task(actor(NAV), make(assignee_id=VIN["id"]))  # noqa: E731
        assert refused(call) == (422, "Vinita does not have My Tasks yet.")

    def test_the_global_default_switches_them_on(self, world):
        world.fake.tasks.default = 1
        out = create_task(actor(NAV), make(assignee_id=VIN["id"]))
        assert out["assignee"]["id"] == VIN["id"]

    def test_the_actors_own_level_is_not_checked(self, world):
        out = create_task(actor(VIN), make())  # Vinita has level 0
        assert (out["kind"], out["assignee"]["id"]) == ("private", VIN["id"])

    def test_a_crm_task_goes_to_a_member_only(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(NAV), make(assignee_id=VIN["id"], links=[link("company", world.acme["id"])])
        )
        assert refused(call) == (422, "The assignee must be a member of this workspace.")

    def test_a_crm_only_member_may_be_given_a_crm_task(self, world):
        out = create_task(
            actor(NAV), make(assignee_id=SEL["id"], links=[link("company", world.acme["id"])])
        )
        assert out["assignee"]["id"] == SEL["id"]

    def test_a_crm_only_account_cannot_be_given_a_project_task(self, world):
        call = lambda: create_task(  # noqa: E731
            actor(NAV),
            make(assignee_id=SEL["id"], links=[link("tva_project", world.project["id"])]),
        )
        assert refused(call) == (422, "A CRM-only account cannot be given a project task.")

    def test_an_inactive_account_is_refused(self, world):
        world.fake.profiles[TIM["id"]]["is_active"] = False
        call = lambda: create_task(actor(NAV), make(assignee_id=TIM["id"]))  # noqa: E731
        assert refused(call) == (422, "The assignee must be an active account.")


class TestStatus:
    """Step 4 — status, completed at and Waiting on."""

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    def test_done_and_cancelled_are_refused_for_a_person(self, world, status):
        call = lambda: create_task(actor(NAV), make(status=status))  # noqa: E731
        assert refused(call) == (422, "A new task is open or waiting.")

    def test_an_import_done_row_takes_the_import_time(self, world):
        out = create_task(actor(NAV, kind="import"), make(status="done", source="import"))
        assert out["completed_at"] == NOW.isoformat()

    def test_an_import_done_row_keeps_its_completed_at(self, world):
        when = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        out = create_task(
            actor(NAV, kind="import"), make(status="done", completed_at=when, source="import")
        )
        assert out["completed_at"] == when.isoformat()

    def test_an_import_cancelled_row_has_no_completed_at(self, world):
        out = create_task(actor(NAV, kind="import"), make(status="cancelled", source="import"))
        assert out["completed_at"] is None

    @pytest.mark.parametrize(
        ("kind", "status"), [("user", "open"), ("import", "open"), ("import", "cancelled")]
    )
    def test_completed_at_outside_an_import_done_row_is_refused(self, world, kind, status):
        call = lambda: create_task(  # noqa: E731
            actor(NAV, kind=kind), make(status=status, completed_at=NOW)
        )
        assert refused(call) == (422, "Only an imported Done task can carry a completed time.")

    def test_waiting_needs_waiting_on(self, world):
        call = lambda: create_task(actor(NAV), make(status="waiting"))  # noqa: E731
        assert refused(call) == (422, "Waiting needs who or what you are waiting on.")

    def test_waiting_on_text(self, world):
        out = create_task(actor(NAV), make(status="waiting", waiting_on_text="Life OS"))
        assert out["waiting_on"] == {"id": None, "label": "Life OS"}

    def test_waiting_on_a_person_keeps_their_name(self, world):
        out = create_task(actor(NAV), make(status="waiting", waiting_on_id=TIM["id"]))
        assert out["waiting_on"] == {"id": TIM["id"], "label": "Timothy"}


class TestFields:
    def test_tags_are_trimmed_and_repeats_dropped(self, world):
        out = create_task(actor(NAV), make(tags=[" sales ", "sales", "Sales"]))
        assert out["tags"] == ["sales", "Sales"]

    def test_a_tag_over_forty_characters_is_refused(self, world):
        assert refused(lambda: create_task(actor(NAV), make(tags=["x" * 41]))) == (
            422,
            "A tag can be at most 40 characters.",
        )

    def test_an_eleventh_tag_is_refused(self):
        with pytest.raises(ValidationError):
            make(tags=[f"t{i}" for i in range(11)])

    def test_a_category_only_on_a_crm_task(self, world):
        option = world.fake.option(world.labs, "Sales")
        call = lambda: create_task(actor(NAV), make(category_id=option["id"]))  # noqa: E731
        assert refused(call) == (422, "A category is only for CRM tasks.")
        out = create_task(
            actor(NAV),
            make(category_id=option["id"], links=[link("company", world.acme["id"])]),
        )
        assert out["category"] == {"id": option["id"], "label": "Sales"}


class TestParent:
    def test_a_parent_they_cannot_see_is_404(self, world):
        hidden = world.fake.tasks.add_task(
            title="Secret", created_by=TIM["id"], assignee_id=TIM["id"]
        )
        call = lambda: create_task(actor(NAV), make(parent_id=hidden["id"]))  # noqa: E731
        assert refused(call) == (404, "No such task.")

    def test_a_parent_with_a_parent_is_refused(self, world):
        top = world.fake.tasks.add_task(title="Top", created_by=NAV["id"], assignee_id=NAV["id"])
        mid = world.fake.tasks.add_task(
            title="Mid", created_by=NAV["id"], assignee_id=NAV["id"], parent_id=top["id"]
        )
        call = lambda: create_task(actor(NAV), make(parent_id=mid["id"]))  # noqa: E731
        assert refused(call) == (422, "A subtask cannot have subtasks.")

    def test_a_visible_parent(self, world):
        top = world.fake.tasks.add_task(title="Top", created_by=NAV["id"], assignee_id=NAV["id"])
        out = create_task(actor(NAV), make(parent_id=top["id"]))
        assert out["parent"] == {"id": top["id"], "title": "Top"}


class TestTheWrite:
    """Step 5 — one `task_apply` call, audited with the actor's kind and
    channel; `suggestion_id` reaches `crm.actor` only when set."""

    def test_one_call_with_the_task_and_its_links(self, world):
        create_task(actor(NAV), make(links=[link("company", world.acme["id"])]))
        [(sent, ops)] = world.fake.tasks.applied
        assert [(op["op"], op["table"]) for op in ops] == [
            ("insert", "tasks"),
            ("insert", "task_links"),
        ]
        assert "workspace_id" not in sent

    @pytest.mark.parametrize(
        ("kind", "channel"), [("user", "web"), ("user", "mcp"), ("import", None)]
    )
    def test_the_audit_row_has_actor_kind_and_channel(self, world, kind, channel):
        out = create_task(actor(NAV, kind=kind, channel=channel), make())
        [row] = [a for a in world.fake.audit if a["target_id"] == out["id"]]
        assert (row["actor_id"], row["actor_label"], row["channel"], row["action"]) == (
            NAV["id"],
            kind,
            channel,
            "task.created",
        )
        assert row["workspace_id"] is None

    def test_suggestion_id_reaches_the_actor_when_set(self, world):
        suggestion = uuid.uuid4()
        create_task(actor(NAV, channel="web", suggestion_id=suggestion), make())
        [(sent, _)] = world.fake.tasks.applied
        assert sent == {
            "user_id": NAV["id"],
            "kind": "user",
            "channel": "web",
            "suggestion_id": str(suggestion),
        }

    def test_suggestion_id_is_absent_when_not_set(self, world):
        create_task(actor(NAV, channel="web"), make())
        [(sent, _)] = world.fake.tasks.applied
        assert "suggestion_id" not in sent

    def test_a_crm_task_assigned_to_someone_else_rings_the_bell(self, world):
        out = create_task(
            actor(NAV), make(assignee_id=TIM["id"], links=[link("company", world.acme["id"])])
        )
        [notice] = [n for n in world.fake.rows["crm_notifications"].values()]
        assert (notice["user_id"], notice["kind"], notice["record_id"]) == (
            TIM["id"],
            "assigned",
            out["id"],
        )
