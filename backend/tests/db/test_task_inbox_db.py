"""The inbox and the weekly review in the real database — spec 008
FR-TASK-INBOX-01, -02, FR-TASK-REVIEW-01; data-model §2.10, §5.2, §5.4;
migration 055.

What only Postgres can prove: `inbox_items` takes only its owner's writes
through `task_apply`, each audited with no workspace; its CHECKs hold the
kind, title, link and tags; the browser cannot read it; the task an item
became may be deleted (the item keeps no dangling id); deleting a profile
that owns inbox items succeeds, the cascade audited as the system; and the
store's reads and the dismissed week (`profiles.tasks_review_dismissed_week`)
work against the real tables.
"""

from __future__ import annotations

import json

import pytest
from supabase import create_client

from app.services import supabase as db
from app.services.tasks import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


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


def _insert(owner: str, **values) -> dict:
    return {
        "op": "insert",
        "table": "inbox_items",
        "values": {"owner_id": owner, "kind": "idea", "title": "A webinar", **values},
    }


def _item(stack, owner: str, **values) -> dict:
    return _apply(stack, owner, [_insert(owner, **values)])[0]["row"]


@pytest.fixture
def local(stack, monkeypatch) -> None:
    monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))


class TestTheTable:
    def test_rls_on_and_no_grants(self, stack, make_profile):
        [row] = stack.sql("SELECT relrowsecurity FROM pg_class WHERE relname = 'inbox_items'")
        assert row["relrowsecurity"] is True
        grants = stack.sql(
            "SELECT grantee FROM information_schema.role_table_grants "
            "WHERE table_name = 'inbox_items' AND grantee IN ('anon', 'authenticated')"
        )
        assert grants == []
        user = make_profile()
        r = stack.rest("inbox_items", bearer=stack.jwt_for(user))
        assert r.status_code in (401, 403), r.text

    def test_the_review_column_exists(self, stack):
        [col] = stack.sql(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'profiles' AND column_name = 'tasks_review_dismissed_week'"
        )
        assert (col["data_type"], col["is_nullable"]) == ("date", "YES")


class TestWrites:
    def test_the_owner_adds_an_item_audited_without_a_workspace(self, stack, make_profile):
        owner = make_profile(keep=True)
        item = _item(stack, owner, url="https://example.com/a", tags=["ai"])
        assert (item["processed_at"], item["archived"], item["task_id"]) == (None, False, None)
        [audit] = stack.sql(
            "SELECT action, actor_id, workspace_id FROM audit_log "
            f"WHERE target_table = 'inbox_items' AND target_id = {stack.q(item['id'])}"
        )
        assert audit == {"action": "inbox_item.created", "actor_id": owner, "workspace_id": None}

    def test_nobody_else_writes_it(self, stack, make_profile):
        owner, other = make_profile(keep=True), make_profile(keep=True)
        message = _refused(stack, _apply_sql(stack, other, [_insert(owner)]), "CRM04")
        assert message == "No such task."
        item = _item(stack, owner)
        update = {
            "op": "update",
            "table": "inbox_items",
            "id": item["id"],
            "values": {"archived": True},
        }
        _refused(stack, _apply_sql(stack, other, [update]), "CRM04")

    @pytest.mark.parametrize(
        "values",
        [
            {"kind": "thought"},
            {"title": "  "},
            {"title": "x" * 201},
            {"url": "ftp://example.com"},
            {"url": "https://exa mple.com"},
            {"tags": [f"t{n}" for n in range(11)]},
            {"notes": "x" * 20_001},
        ],
    )
    def test_the_checks_hold(self, stack, make_profile, values):
        owner = make_profile(keep=True)
        _refused(stack, _apply_sql(stack, owner, [_insert(owner, **values)]), "23514")

    @staticmethod
    def _make_task_ops(owner: str, item: dict, expect: str) -> list[dict]:
        """Make it a task's one batch: the task, then the item linked to it."""
        return [
            {
                "op": "insert",
                "table": "tasks",
                "ref": "task",
                "values": {"title": "A webinar", "assignee_id": owner, "bucket": "later"},
            },
            {
                "op": "update",
                "table": "inbox_items",
                "id": item["id"],
                "values": {"task_id": "$ref:task", "processed_at": "2026-10-20T06:30:00+00:00"},
                "expect_updated_at": expect,
            },
        ]

    def test_make_it_a_task_records_the_task_and_survives_its_purge(self, stack, make_profile):
        owner = make_profile(keep=True)
        item = _item(stack, owner)
        done = _apply(stack, owner, self._make_task_ops(owner, item, item["updated_at"]))
        task = done[0]["id"]
        assert done[1]["row"]["task_id"] == task
        _apply(stack, owner, [{"op": "delete", "table": "tasks", "id": task}])
        [row] = stack.sql(
            f"SELECT task_id, processed_at FROM inbox_items WHERE id = {stack.q(item['id'])}"
        )
        assert row["task_id"] is None and row["processed_at"] is not None

    def test_a_stale_item_leaves_no_task(self, stack, make_profile):
        owner = make_profile(keep=True)
        item = _item(stack, owner)
        ops = self._make_task_ops(owner, item, "2000-01-01T00:00:00+00:00")
        _refused(stack, _apply_sql(stack, owner, ops), "CRM09")
        assert stack.sql(f"SELECT id FROM tasks WHERE assignee_id = {stack.q(owner)}") == []
        [row] = stack.sql(
            f"SELECT task_id, processed_at FROM inbox_items WHERE id = {stack.q(item['id'])}"
        )
        assert row["task_id"] is None and row["processed_at"] is None


class TestProfileDeletion:
    """Data-model §5.4 — the cascade is audited as the system."""

    def test_deleting_a_profile_that_owns_inbox_items(self, stack, make_profile):
        owner = make_profile()
        # Written as the system, so no audit row names the owner as actor
        # (the append-only log would refuse that row's SET NULL).
        [*_, made] = stack.sql(
            f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); "
            "INSERT INTO inbox_items (owner_id, kind, title) "
            f"VALUES ({stack.q(owner)}, 'note', 'Read the report') RETURNING id"
        )
        stack.sql(f"DELETE FROM auth.users WHERE id = {stack.q(owner)}")
        assert stack.sql(f"SELECT 1 FROM inbox_items WHERE id = {stack.q(made['id'])}") == []
        [audit] = stack.sql(
            "SELECT actor_id, actor_label, action FROM audit_log "
            f"WHERE target_table = 'inbox_items' AND target_id = {stack.q(made['id'])} "
            "AND action LIKE '%.purged' ORDER BY id DESC LIMIT 1"
        )
        assert (audit["actor_id"], audit["actor_label"]) == (None, "system")


class TestTheStore:
    def test_unprocessed_newest_first_and_the_dismissed_week(self, stack, make_profile, local):
        owner = make_profile(keep=True)
        first = _item(stack, owner, title="First")
        second = _item(stack, owner, title="Second")
        done = _item(stack, owner, title="Done")
        _apply(
            stack,
            owner,
            [
                {
                    "op": "update",
                    "table": "inbox_items",
                    "id": done["id"],
                    "values": {"processed_at": "2026-10-20T06:30:00+00:00", "archived": True},
                }
            ],
        )
        listed = [r["id"] for r in store.unprocessed_inbox(owner)]
        assert listed == [second["id"], first["id"]]
        assert store.get_inbox_item(done["id"])["archived"] is True
        row = store.set_review_dismissed(owner, "2026-10-19")
        assert row["tasks_review_dismissed_week"] == "2026-10-19"
