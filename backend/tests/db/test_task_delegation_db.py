"""Delegation in the real database — spec 008 FR-TASK-DEL-02, -04, -06, -07;
data-model §2.1 (phase 2), §2.5-2.7, §5.2, §5.4; migration 052.

What only Postgres can prove: a message's sent marker is claimed once per
task, event, recipient and time of the event, so a retried job finds it
taken (also through `store.claim_notice`, the request the sender makes); a comment carries its task's workspace when written and keeps it;
`task_apply` writes a comment once and lets it only be soft-deleted; the
nudge days stay within 1-30; and deleting a profile named in `assigned_by`
succeeds, recorded as the system.
"""

from __future__ import annotations

import json
import uuid

import pytest
from supabase import create_client

from app.services import supabase as db
from app.services.tasks import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web"}


def _apply_sql(stack, user: str, ops: list[dict]) -> str:
    return f"SELECT task_apply({_j(stack, _actor(user))}, {_j(stack, ops)}) AS r"


def _apply(stack, user: str, ops: list[dict]) -> list[dict]:
    return stack.sql(_apply_sql(stack, user, ops))[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _one(stack, query: str) -> dict:
    return stack.sql(query)[0]


def _personal(stack, creator: str, **values) -> str:
    row = {"title": "Draft the plan", "assignee_id": creator, "bucket": "later", **values}
    return _apply(stack, creator, [{"op": "insert", "table": "tasks", "values": row}])[0]["id"]


def _crm_task(stack, user: str) -> tuple[str, str]:
    """A task of a new workspace with one company link; (workspace, task)."""
    key = f"t{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(user)}, 'member')",
    )
    crm = {"user_id": user, "kind": "user", "channel": "web", "workspace_id": ws}
    company = stack.sql(
        f"SELECT crm_apply({_j(stack, crm)}, "
        + _j(
            stack,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Acme", "owner_id": user},
                }
            ],
        )
        + ") AS r"
    )[0]["r"][0]["id"]
    ops = [
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
    ]
    task = stack.sql(f"SELECT crm_apply({_j(stack, crm)}, {_j(stack, ops)}) AS r")[0]["r"][0]
    return ws, task["id"]


def _comment(task: str, author: str, workspace: str | None, body: str = "On it") -> dict:
    return {
        "op": "insert",
        "table": "task_comments",
        "values": {"task_id": task, "workspace_id": workspace, "author_id": author, "body": body},
    }


@pytest.fixture
def people(make_profile) -> dict[str, str]:
    return {name: make_profile(keep=True) for name in ("creator", "assignee")}


@pytest.fixture
def local(stack, monkeypatch) -> None:
    monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))


class TestNoticeSends:
    """Data-model §2.6 (FR-TASK-DEL-07)."""

    def test_the_senders_claim_is_new_once(self, stack, people, local):
        task = _personal(stack, people["creator"], assignee_id=people["assignee"])
        at = "2026-10-20T06:30:00+00:00"
        first = store.claim_notice(task, "assigned", people["assignee"], at)
        assert first is not None
        assert store.claim_notice(task, "assigned", people["assignee"], at) is None
        store.mark_notice_sent(first, "2026-10-20T06:30:05+00:00", ["slack"])
        row = _one(stack, f"SELECT channels FROM task_notice_sends WHERE id = {stack.q(first)}")
        assert row == {"channels": ["slack"]}

    def _claim(self, stack, task: str, recipient: str, at: str) -> list[dict]:
        return stack.sql(
            "INSERT INTO task_notice_sends (task_id, event, recipient_id, occurred_at) "
            f"VALUES ({stack.q(task)}, 'assigned', {stack.q(recipient)}, {stack.q(at)}) "
            "ON CONFLICT (task_id, event, recipient_id, occurred_at) DO NOTHING RETURNING id"
        )

    def test_a_message_is_claimed_once(self, stack, people):
        task = _personal(stack, people["creator"], assignee_id=people["assignee"])
        at = "2026-10-20T06:30:00+00:00"
        assert len(self._claim(stack, task, people["assignee"], at)) == 1
        assert self._claim(stack, task, people["assignee"], at) == []
        _refused(
            stack,
            "INSERT INTO task_notice_sends (task_id, event, recipient_id, occurred_at) "
            f"VALUES ({stack.q(task)}, 'assigned', {stack.q(people['assignee'])}, "
            f"{stack.q(at)})",
            "23505",
        )

    def test_a_later_event_is_a_new_message(self, stack, people):
        task = _personal(stack, people["creator"], assignee_id=people["assignee"])
        assert self._claim(stack, task, people["assignee"], "2026-10-20T06:30:00+00:00")
        assert self._claim(stack, task, people["assignee"], "2026-10-21T06:30:00+00:00")

    def test_only_the_three_events(self, stack, people):
        task = _personal(stack, people["creator"])
        _refused(
            stack,
            "INSERT INTO task_notice_sends (task_id, event, recipient_id, occurred_at) "
            f"VALUES ({stack.q(task)}, 'nudged', {stack.q(people['creator'])}, now())",
            "23514",
        )

    def test_a_log_with_rls_on_no_grants_and_no_audit(self, stack):
        row = _one(
            stack,
            "SELECT c.relrowsecurity AS rls, "
            "has_table_privilege('authenticated', 'task_notice_sends', 'SELECT') AS auth, "
            "has_table_privilege('anon', 'task_notice_sends', 'SELECT') AS anon, "
            "EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid "
            "AND t.tgname = 'task_notice_sends_crm_audit') AS audited "
            "FROM pg_class c WHERE c.relname = 'task_notice_sends'",
        )
        assert row == {"rls": True, "auth": False, "anon": False, "audited": False}


class TestComments:
    """Data-model §2.5, §5.2 (FR-TASK-DEL-04)."""

    def test_a_comment_carries_its_tasks_workspace(self, stack, people):
        ws, task = _crm_task(stack, people["creator"])
        msg = _refused(
            stack,
            _apply_sql(stack, people["creator"], [_comment(task, people["creator"], None)]),
            "CRM22",
        )
        assert msg == "A comment belongs to its task's workspace."
        made = _apply(stack, people["creator"], [_comment(task, people["creator"], ws)])
        assert made[0]["row"]["workspace_id"] == ws
        audited = _one(
            stack,
            "SELECT workspace_id, action FROM audit_log "
            f"WHERE target_table = 'task_comments' AND target_id = {stack.q(made[0]['id'])}",
        )
        assert audited == {"workspace_id": ws, "action": "task_comment.created"}

    def test_a_personal_tasks_comment_has_no_workspace(self, stack, people):
        task = _personal(stack, people["creator"])
        ws, _ = _crm_task(stack, people["creator"])
        _refused(
            stack,
            _apply_sql(stack, people["creator"], [_comment(task, people["creator"], ws)]),
            "CRM22",
        )
        made = _apply(stack, people["creator"], [_comment(task, people["creator"], None)])
        assert made[0]["row"]["workspace_id"] is None

    def test_a_comment_keeps_its_workspace(self, stack, people):
        task = _personal(stack, people["creator"])
        made = _apply(stack, people["creator"], [_comment(task, people["creator"], None)])
        ws, _ = _crm_task(stack, people["creator"])
        _refused(
            stack,
            f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); "
            f"UPDATE task_comments SET workspace_id = {stack.q(ws)} "
            f"WHERE id = {stack.q(made[0]['id'])}",
            "CRM22",
        )

    @pytest.mark.parametrize(
        "op",
        [
            {"op": "update", "values": {"body": "Edited"}},
            {"op": "restore"},
            {"op": "delete"},
        ],
        ids=["update", "restore", "delete"],
    )
    def test_not_editable_through_task_apply(self, stack, people, op):
        task = _personal(stack, people["creator"])
        made = _apply(stack, people["creator"], [_comment(task, people["creator"], None)])
        msg = _refused(
            stack,
            _apply_sql(
                stack,
                people["creator"],
                [{**op, "table": "task_comments", "id": made[0]["id"]}],
            ),
            "CRM22",
        )
        assert msg == "A comment cannot be changed."

    def test_soft_deleted_and_audited(self, stack, people):
        task = _personal(stack, people["creator"])
        made = _apply(stack, people["creator"], [_comment(task, people["creator"], None)])
        gone = _apply(
            stack,
            people["creator"],
            [{"op": "soft_delete", "table": "task_comments", "id": made[0]["id"]}],
        )
        assert gone[0]["row"]["deleted_at"] is not None
        assert gone[0]["row"]["deleted_by"] == people["creator"]
        actions = [
            r["action"]
            for r in stack.sql(
                "SELECT action FROM audit_log WHERE target_table = 'task_comments' "
                f"AND target_id = {stack.q(made[0]['id'])} ORDER BY id"
            )
        ]
        assert actions == ["task_comment.created", "task_comment.deleted"]

    def test_only_the_tasks_people_write_a_comment(self, stack, people, make_profile):
        task = _personal(stack, people["creator"])
        other = make_profile(keep=True)
        _refused(stack, _apply_sql(stack, other, [_comment(task, other, None)]), "CRM04")

    @pytest.mark.parametrize("body", ["   ", "x" * 2001])
    def test_a_comment_is_one_to_2000_characters(self, stack, people, body):
        task = _personal(stack, people["creator"])
        _refused(
            stack,
            _apply_sql(stack, people["creator"], [_comment(task, people["creator"], None, body)]),
            "23514",
        )

    def test_rls_on_and_no_grants(self, stack):
        row = _one(
            stack,
            "SELECT c.relrowsecurity AS rls, "
            "has_table_privilege('authenticated', 'task_comments', 'SELECT') AS auth, "
            "has_table_privilege('anon', 'task_comments', 'SELECT') AS anon "
            "FROM pg_class c WHERE c.relname = 'task_comments'",
        )
        assert row == {"rls": True, "auth": False, "anon": False}


class TestProfiles:
    """Data-model §2.1 (phase 2), §2.7, §5.4."""

    def test_nudge_days_default_to_five_and_stay_within_1_to_30(self, stack, make_profile):
        person = make_profile()
        days = _one(stack, f"SELECT task_nudge_days FROM profiles WHERE id = {stack.q(person)}")
        assert days == {"task_nudge_days": 5}
        for bad in (0, 31):
            _refused(
                stack,
                f"UPDATE profiles SET task_nudge_days = {bad} WHERE id = {stack.q(person)}",
                "23514",
            )

    def test_deleting_a_profile_named_in_assigned_by(self, stack, people, make_profile):
        named = make_profile()
        task = _personal(
            stack, people["creator"], assignee_id=people["assignee"], assigned_by=named
        )
        stack.sql(f"DELETE FROM auth.users WHERE id = {stack.q(named)}")
        row = _one(stack, f"SELECT assigned_by FROM tasks WHERE id = {stack.q(task)}")
        assert row == {"assigned_by": None}
        audited = _one(
            stack,
            "SELECT actor_id, actor_label, before, after FROM audit_log "
            f"WHERE target_table = 'tasks' AND target_id = {stack.q(task)} "
            "AND action = 'task.updated' ORDER BY id DESC LIMIT 1",
        )
        assert (audited["actor_id"], audited["actor_label"]) == (None, "system")
        assert audited["before"] == {"assigned_by": named}
        assert audited["after"] == {"assigned_by": None}
