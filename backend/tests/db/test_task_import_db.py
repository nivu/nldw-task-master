"""The Life OS import in the real database — spec 008 FR-TASK-IMP-05, -08,
FR-TASK-DATA-12, FR-TASK-VIS-05, FR-TASK-CHG-07; data-model §2.3; migrations
047, 049, 051.

What only Postgres can prove: `tasks_lifeos_key` keeps a Life OS key unique
(in the Trash or not) and leaves other sources alone; a sheet's worth of keys
is read through the gateway without a 414; the run record is
written only through `task_apply` by its uploader, audited with no
workspace, and left out of the TVA audit (`services/supabase.list_audit`);
the browser cannot read it. The uploader is any profile here: who may
upload (`profiles.is_owner`) is the service's check.
"""

from __future__ import annotations

import json

import pytest
from supabase import create_client

from app.services import supabase as db
from app.services.tasks import store

pytestmark = pytest.mark.db


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _actor(user: str, kind: str = "import") -> dict:
    return {"user_id": user, "kind": kind, "channel": "web"}


def _task_apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT task_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _imported(stack, owner: str, ref: str | None, source: str = "import") -> list[dict]:
    row = {
        "title": "Book the venue",
        "assignee_id": owner,
        "bucket": "later",
        "source": source,
        "source_ref": ref,
    }
    return _task_apply(stack, _actor(owner), [{"op": "insert", "table": "tasks", "values": row}])


def _run_values(owner: str) -> dict:
    return {
        "uploader_id": owner,
        "file_sha256": "b" * 64,
        "owner_mapping": {"Navaneeth": "navaneeth@nunnarilabs.com"},
        "value_mapping": {"status": {"Open": "open"}, "priority": {"(blank)": "normal"}},
        "counts": {"rows": 1, "open": 1},
        "report": {"ambiguous_dates": [{"row": 2, "read_as": "2026-11-03"}]},
    }


class TestLifeOsKey:
    def test_a_life_os_key_is_unique(self, stack, make_profile):
        owner = make_profile(keep=True, tasks_level=1)
        key = "lifeos:" + owner.replace("-", "") + "0" * 32
        _imported(stack, owner, key)
        message = _refused(
            stack,
            f"SELECT task_apply({_j(stack, _actor(owner))}, "
            f"{_j(stack, [{'op': 'insert', 'table': 'tasks', 'values': {'title': 'Again', 'assignee_id': owner, 'bucket': 'later', 'source': 'import', 'source_ref': key}}])})",
            "23505",
        )
        assert "tasks_lifeos_key" in message

    def test_the_key_stays_taken_while_the_task_is_in_the_trash(self, stack, make_profile):
        owner = make_profile(keep=True, tasks_level=1)
        key = "lifeos:" + owner.replace("-", "") + "1" * 32
        [made] = _imported(stack, owner, key)
        _task_apply(
            stack,
            _actor(owner, "user"),
            [{"op": "soft_delete", "table": "tasks", "id": made["id"]}],
        )
        with pytest.raises(stack.SqlError) as raised:
            _imported(stack, owner, key)
        assert raised.value.code == "23505"

    def test_other_sources_and_references_are_outside_it(self, stack, make_profile):
        owner = make_profile(keep=True, tasks_level=1)
        ref = "twenty:" + owner
        _imported(stack, owner, ref, source="manual")
        _imported(stack, owner, ref, source="manual")
        _imported(stack, owner, None)
        _imported(stack, owner, None)
        count = stack.sql(f"SELECT count(*) AS n FROM tasks WHERE assignee_id = {stack.q(owner)}")[
            0
        ]["n"]
        assert count == 4

    def test_150_keys_are_read_without_a_too_long_url(self, stack, make_profile, monkeypatch):
        """Through the gateway, whose request line stops at 8 KB (414)."""
        owner = make_profile(keep=True, tasks_level=1)
        key = "lifeos:" + owner.replace("-", "") + "2" * 32
        _imported(stack, owner, key)
        others = [f"lifeos:{n:064x}" for n in range(150)]
        monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))
        assert store.existing_import_keys([*others, key]) == {key}


class TestRunRecord:
    def test_the_uploader_writes_it_through_task_apply_audited_with_no_workspace(
        self, stack, make_profile
    ):
        owner = make_profile(keep=True)
        [run] = _task_apply(
            stack,
            _actor(owner),
            [{"op": "insert", "table": "task_import_runs", "values": _run_values(owner)}],
        )
        assert run["row"]["status"] == "dry_run"
        assert run["row"]["dry_run_at"] is not None
        _task_apply(
            stack,
            _actor(owner),
            [
                {
                    "op": "update",
                    "table": "task_import_runs",
                    "id": run["id"],
                    "values": {"status": "applied", "counts": {"created": 1}},
                }
            ],
        )
        rows = stack.sql(
            "SELECT actor_id, actor_label, workspace_id, action FROM audit_log "
            f"WHERE target_table = 'task_import_runs' AND target_id = {stack.q(run['id'])} "
            "ORDER BY id"
        )
        assert [r["action"] for r in rows] == [
            "task_import_run.created",
            "task_import_run.updated",
        ]
        assert {(r["actor_id"], r["actor_label"], r["workspace_id"]) for r in rows} == {
            (owner, "import", None)
        }

    def test_someone_else_cannot_write_or_change_it(self, stack, make_profile):
        owner = make_profile(keep=True)
        other = make_profile(keep=True)
        values = _run_values(owner)
        _refused(
            stack,
            f"SELECT task_apply({_j(stack, _actor(other))}, "
            f"{_j(stack, [{'op': 'insert', 'table': 'task_import_runs', 'values': values}])})",
            "CRM04",
        )
        [run] = _task_apply(
            stack,
            _actor(owner),
            [{"op": "insert", "table": "task_import_runs", "values": values}],
        )
        _refused(
            stack,
            f"SELECT task_apply({_j(stack, _actor(other))}, "
            f"{_j(stack, [{'op': 'update', 'table': 'task_import_runs', 'id': run['id'], 'values': {'status': 'failed'}}])})",
            "CRM04",
        )

    def test_it_is_out_of_the_tva_audit(self, stack, make_profile, monkeypatch):
        owner = make_profile(keep=True)
        [run] = _task_apply(
            stack,
            _actor(owner),
            [{"op": "insert", "table": "task_import_runs", "values": _run_values(owner)}],
        )
        monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))
        assert run["id"] not in {a["target_id"] for a in db.list_audit(limit=1000)}
        everything = db.list_audit(limit=1000, tva_only=False)
        assert run["id"] in {a["target_id"] for a in everything}

    def test_the_file_hash_must_be_a_sha256(self, stack, make_profile):
        owner = make_profile(keep=True)
        values = {**_run_values(owner), "file_sha256": "not-a-hash"}
        _refused(
            stack,
            f"SELECT task_apply({_j(stack, _actor(owner))}, "
            f"{_j(stack, [{'op': 'insert', 'table': 'task_import_runs', 'values': values}])})",
            "23514",
        )

    def test_the_browser_cannot_read_it(self, stack, make_profile):
        owner = make_profile(keep=True)
        r = stack.rest("task_import_runs", bearer=stack.jwt_for(owner))
        assert r.status_code in (401, 403), r.text
