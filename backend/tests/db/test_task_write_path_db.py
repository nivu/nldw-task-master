"""The task write path in the real database — spec 008 FR-TASK-DATA-05, -09,
-10, -11, FR-TASK-CHG-01, FR-TASK-VIS-05; TQ-02; data-model §5.2, §5.3, §5.4;
migrations 047, 048, 049.

What only Postgres can prove: `task_apply` re-checks the caller's right to
each row (a task with no workspace answers only to its creator and assignee,
or the system; one with a workspace to its members), refuses a stale edit
(CRM09), audits every write with the actor and the row's own workspace (none
for a personal task), and nothing reaches a task table without an actor;
`task_purge_expired` removes only personal tasks deleted before the cut-off;
`crm_apply` with `p_task_needs_link` refuses a task with no CRM link;
`tasks_parent_check` keeps subtasks one level deep; a Labs task's project
link is audited with the task's workspace; and deleting a profile named in
Waiting on succeeds, recorded as the system.

Acceptance criteria (spec §12, phase 1):
- "The CRM writer refuses a task created through the CRM with no link" —
  TestCrmApply.
- "A deleted task with no workspace ... the daily purge job removes it 90
  days after deletion" — TestPurgeExpired (the job's cut-off:
  tests/test_task_jobs.py).
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _crm_actor(workspace: str, user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web", "workspace_id": workspace}


def _task_actor(user: str | None, kind: str = "user") -> dict:
    return {"user_id": user, "kind": kind, "channel": "web" if kind == "user" else None}


def _crm_apply_sql(stack, actor: dict, ops: list[dict], needs_link: bool | None = None) -> str:
    extra = "" if needs_link is None else f", {'true' if needs_link else 'false'}"
    return f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}{extra}) AS r"


def _task_apply_sql(stack, actor: dict, ops: list[dict]) -> str:
    return f"SELECT task_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r"


def _task_apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(_task_apply_sql(stack, actor, ops))[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _one(stack, query: str) -> dict:
    return stack.sql(query)[0]


def _new_workspace(stack, *, tva_linked: bool = False) -> str:
    """A new workspace, or for a TVA-linked one the seeded Labs (only one
    workspace is TVA-linked, 026)."""
    if tva_linked:
        return stack.sql("SELECT id FROM crm_workspaces WHERE tva_linked")[0]["id"]
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name, tva_linked) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}, {str(tva_linked).lower()}) "
        "RETURNING id",
    )[-1]["id"]


def _member(stack, workspace: str, user: str, role: str = "member") -> None:
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(user)}, {stack.q(role)})",
    )


def _personal(stack, creator: str, **values) -> str:
    row = {"title": "Book dentist", "assignee_id": creator, "bucket": "later", **values}
    return _task_apply(
        stack, _task_actor(creator), [{"op": "insert", "table": "tasks", "values": row}]
    )[0]["id"]


def _crm_task(stack, workspace: str, user: str) -> str:
    company = stack.sql(
        _crm_apply_sql(
            stack,
            _crm_actor(workspace, user),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Acme", "owner_id": user},
                }
            ],
        )
    )[0]["r"][0]["id"]
    return stack.sql(
        _crm_apply_sql(
            stack,
            _crm_actor(workspace, user),
            [
                {
                    "op": "insert",
                    "table": "tasks",
                    "ref": "t",
                    "values": {"title": "Call", "assignee_id": user, "bucket": "later"},
                },
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {"task_id": "$ref:t", "record_type": "company", "record_id": company},
                },
            ],
        )
    )[0]["r"][0]["id"]


@pytest.fixture
def people(make_profile) -> dict[str, str]:
    return {name: make_profile(keep=True) for name in ("creator", "assignee", "other")}


class TestRightRechecks:
    """data-model §5.2 — the backstop to the Python rules."""

    def test_a_personal_task_answers_to_its_creator_and_assignee(self, stack, people):
        task = _personal(stack, people["creator"], assignee_id=people["assignee"])
        for who in ("creator", "assignee"):
            _task_apply(
                stack,
                _task_actor(people[who]),
                [{"op": "update", "table": "tasks", "id": task, "values": {"priority": "high"}}],
            )
        _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["other"]),
                [{"op": "update", "table": "tasks", "id": task, "values": {"priority": "low"}}],
            ),
            "CRM04",
        )

    def test_the_system_may_write_a_personal_task(self, stack, people):
        task = _personal(stack, people["creator"])
        _task_apply(
            stack,
            _task_actor(None, "system"),
            [{"op": "update", "table": "tasks", "id": task, "values": {"priority": "low"}}],
        )

    def test_nobody_inserts_a_task_for_someone_else_as_creator(self, stack, people):
        """`created_by` is the actor, never a value."""
        _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["other"]),
                [
                    {
                        "op": "insert",
                        "table": "tasks",
                        "values": {
                            "title": "X",
                            "assignee_id": people["other"],
                            "bucket": "later",
                            "created_by": people["creator"],
                        },
                    }
                ],
            ),
            "CRM22",
        )

    def test_a_task_with_a_workspace_answers_to_its_members(self, stack, people):
        ws = _new_workspace(stack)
        _member(stack, ws, people["creator"], "admin")
        task = _crm_task(stack, ws, people["creator"])
        _task_apply(
            stack,
            _task_actor(people["creator"]),
            [{"op": "update", "table": "tasks", "id": task, "values": {"priority": "high"}}],
        )
        # The assignee or creator outside the workspace gets nothing.
        _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["other"]),
                [{"op": "update", "table": "tasks", "id": task, "values": {"priority": "low"}}],
            ),
            "CRM04",
        )

    def test_a_stale_edit_is_refused(self, stack, people):
        task = _personal(stack, people["creator"])
        _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["creator"]),
                [
                    {
                        "op": "update",
                        "table": "tasks",
                        "id": task,
                        "values": {"title": "Later"},
                        "expect_updated_at": "2000-01-01T00:00:00+00:00",
                    }
                ],
            ),
            "CRM09",
        )

    def test_only_task_tables(self, stack, people):
        _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["creator"]),
                [{"op": "insert", "table": "crm_companies", "values": {"name": "X"}}],
            ),
            "CRM22",
        )


class TestTheAudit:
    """FR-TASK-DATA-10 — every write audited with actor, kind and channel."""

    def test_a_personal_task_is_audited_with_no_workspace(self, stack, people):
        task = _personal(stack, people["creator"])
        row = _one(
            stack,
            "SELECT actor_id, actor_label, channel, workspace_id, action FROM audit_log "
            f"WHERE target_table = 'tasks' AND target_id = {stack.q(task)}",
        )
        assert row == {
            "actor_id": people["creator"],
            "actor_label": "user",
            "channel": "web",
            "workspace_id": None,
            "action": "task.created",
        }

    def test_an_unaudited_write_is_refused(self, stack, people):
        message = _refused(
            stack,
            "INSERT INTO tasks (title, assignee_id, bucket) "
            f"VALUES ('Sneaky', {stack.q(people['creator'])}, 'later')",
            "CRM22",
        )
        assert message == "A CRM change must be made through the CRM."

    def test_a_project_link_on_a_labs_task_takes_the_tasks_workspace(self, stack, people):
        ws = _new_workspace(stack, tva_linked=True)
        _member(stack, ws, people["creator"], "admin")
        task = _crm_task(stack, ws, people["creator"])
        project = stack.sql(
            f"INSERT INTO projects (name) VALUES ({stack.q('Task db ' + task[:8])}) RETURNING id"
        )[0]["id"]
        link = _task_apply(
            stack,
            _task_actor(people["creator"]),
            [
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {
                        "task_id": task,
                        "record_type": "tva_project",
                        "record_id": project,
                        "project_id": project,
                    },
                }
            ],
        )[0]["row"]
        assert link["workspace_id"] is None
        audited = _one(
            stack,
            "SELECT workspace_id FROM audit_log "
            f"WHERE target_table = 'task_links' AND target_id = {stack.q(link['id'])}",
        )
        assert audited["workspace_id"] == ws

    def test_deleting_a_profile_named_in_waiting_on(self, stack, people, make_profile):
        named = make_profile()
        task = _personal(
            stack,
            people["creator"],
            status="waiting",
            waiting_on_id=named,
            waiting_on_text="Named",
        )
        stack.sql(f"DELETE FROM auth.users WHERE id = {stack.q(named)}")
        row = _one(
            stack, f"SELECT waiting_on_id, waiting_on_text FROM tasks WHERE id = {stack.q(task)}"
        )
        assert row == {"waiting_on_id": None, "waiting_on_text": "Named"}
        audited = _one(
            stack,
            "SELECT actor_id, actor_label, before, after FROM audit_log "
            f"WHERE target_table = 'tasks' AND target_id = {stack.q(task)} "
            "AND action = 'task.updated' ORDER BY id DESC LIMIT 1",
        )
        assert (audited["actor_id"], audited["actor_label"]) == (None, "system")
        assert audited["before"] == {"waiting_on_id": named}
        assert audited["after"] == {"waiting_on_id": None}


class TestPurgeExpired:
    """data-model §5.3 (TP-12)."""

    def test_only_personal_tasks_deleted_before_the_cut_off(self, stack, people):
        old = _personal(stack, people["creator"], title="Old")
        recent = _personal(stack, people["creator"], title="Recent")
        for task in (old, recent):
            _task_apply(
                stack,
                _task_actor(people["creator"]),
                [{"op": "soft_delete", "table": "tasks", "id": task}],
            )
        _as_system(
            stack,
            f"UPDATE tasks SET deleted_at = now() - interval '91 days' WHERE id = {stack.q(old)}",
        )
        ws = _new_workspace(stack)
        _member(stack, ws, people["creator"], "admin")
        crm = _crm_task(stack, ws, people["creator"])
        _as_system(
            stack,
            f"UPDATE tasks SET deleted_at = now() - interval '200 days' WHERE id = {stack.q(crm)}",
        )
        purged = _one(
            stack,
            f"SELECT task_purge_expired({_j(stack, _task_actor(None, 'system'))}, "
            "now() - interval '90 days') AS n",
        )["n"]
        assert purged >= 1
        left = {
            r["id"]
            for r in stack.sql(
                f"SELECT id FROM tasks WHERE id IN ({stack.q(old)}, {stack.q(recent)}, "
                f"{stack.q(crm)})"
            )
        }
        assert left == {recent, crm}
        row = _one(
            stack,
            "SELECT actor_label, action FROM audit_log "
            f"WHERE target_table = 'tasks' AND target_id = {stack.q(old)} ORDER BY id DESC LIMIT 1",
        )
        assert row == {"actor_label": "system", "action": "task.purged"}

    def test_only_as_the_system(self, stack, people):
        _refused(
            stack,
            f"SELECT task_purge_expired({_j(stack, _task_actor(people['creator']))}, now())",
            "CRM22",
        )


class TestCrmApply:
    """FR-TASK-CHG-01 — the CRM writer's link rule is its `p_task_needs_link`."""

    def _orphan(self, stack, ws, user, needs_link):
        return _crm_apply_sql(
            stack,
            _crm_actor(ws, user),
            [
                {
                    "op": "insert",
                    "table": "tasks",
                    "values": {"title": "Orphan", "assignee_id": user, "bucket": "later"},
                }
            ],
            needs_link,
        )

    @pytest.mark.parametrize("needs_link", [None, True])
    def test_a_task_with_no_crm_link_is_refused(self, stack, people, needs_link):
        ws = _new_workspace(stack)
        message = _refused(stack, self._orphan(stack, ws, people["creator"], needs_link), "CRM22")
        assert message == "A task must be linked to at least one record."

    def test_without_the_rule_the_deferred_check_still_refuses_it(self, stack, people):
        ws = _new_workspace(stack)
        message = _refused(stack, self._orphan(stack, ws, people["creator"], False), "CRM22")
        assert message == "A CRM task needs at least one linked record; delete it instead."


class TestSubtasks:
    """FR-TASK-DATA-09 — one level deep (047 `tasks_parent_check`)."""

    def test_a_parent_that_has_a_parent_is_refused(self, stack, people):
        top = _personal(stack, people["creator"], title="Top")
        mid = _personal(stack, people["creator"], title="Mid", parent_id=top)
        message = _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["creator"]),
                [
                    {
                        "op": "insert",
                        "table": "tasks",
                        "values": {
                            "title": "Low",
                            "assignee_id": people["creator"],
                            "bucket": "later",
                            "parent_id": mid,
                        },
                    }
                ],
            ),
            "CRM22",
        )
        assert message == "A subtask cannot have subtasks."

    def test_a_task_with_children_cannot_take_a_parent(self, stack, people):
        top = _personal(stack, people["creator"], title="Top")
        _personal(stack, people["creator"], title="Child", parent_id=top)
        other = _personal(stack, people["creator"], title="Other")
        _refused(
            stack,
            _task_apply_sql(
                stack,
                _task_actor(people["creator"]),
                [{"op": "update", "table": "tasks", "id": top, "values": {"parent_id": other}}],
            ),
            "CRM22",
        )
