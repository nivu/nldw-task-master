"""The 008 migration in the real database — spec 008 §8.2, FR-TASK-DATA-01,
-03, -08, -12, FR-TASK-CHG-04, -07, -11, NFR-TASK-03; TP-04; T1, T2;
data-model §1, §2.1, §3.1, §3.2, §5.4; migrations 046, 047, 048.

Two classes:

- `TestAfterMigration` runs at every db gate (008's and 009's): the status
  list and its relaxed CHECK, saved views free of the old keys, no function
  body naming `crm_task` (this also guards 009's later function copies, plan
  §9 rule 1), and each function 048 replaced, called once.
- `TestMapping` runs only with `TASKS_MIGRATION_CHECK=1`, after
  `TASKS_MIGRATION_CHECK=seed` has seeded a database reset to 045 and
  `npx supabase migration up` has applied 046 onwards (plan §8.1, one
  invocation under the db lock, then a plain `db reset`):

      bash -c '
      LOCK="$(git rev-parse --git-common-dir)/db-test.lock"
      until mkdir "$LOCK" 2>/dev/null; do sleep 5; done
      trap "rmdir \\"$LOCK\\"" EXIT
      npx supabase db reset --version 045 \\
        && (cd backend && TASKS_MIGRATION_CHECK=seed CRM_DB_TESTS=1 \\
            uv run pytest -m db -q tests/db/test_task_migration_db.py) \\
        && npx supabase migration up \\
        && (cd backend && TASKS_MIGRATION_CHECK=1 CRM_DB_TESTS=1 \\
            uv run pytest -m db -q tests/db/test_task_migration_db.py)
      npx supabase db reset
      '

Acceptance criteria (spec §12, phase 1 — migration and CRM):
- "Every CRM task exists in tasks with the same id, links, title, notes,
  dates, assignee and history; To do and In progress tasks are Open, and
  former In progress tasks carry the tag 'in progress'" — TestMapping
  (dates: test_dates_are_kept).
- "Live workspace tasks with no link before the migration are in their
  workspace Trash, and their count was reported" — TestMapping (the count
  046's NOTICE prints during `migration up`, from the rows it audited:
  test_the_linkless_count_046_reports).
- "Closing a deal Lost ... tasks closed this way before the migration stay
  Done" — TestMapping (a Done task stays Done).
- "The task status list in each workspace holds open, waiting, done,
  cancelled as active options, with todo and in_progress deactivated; a
  renamed Done label survives" — TestAfterMigration, TestMapping.
- "No saved view, favourite or seeded view references todo or in_progress,
  and no task-status filter 'is not Done' remains" — TestAfterMigration.
- "Record timelines show task history written before the rename" —
  TestMapping.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, timedelta

import pytest
from supabase import create_client

from app.services.crm import store

pytestmark = pytest.mark.db

MODE = os.environ.get("TASKS_MIGRATION_CHECK", "")
SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
#: The functions 048 replaced (data-model §5.4).
REPLACED = (
    "crm_apply",
    "crm_check_refs",
    "crm_deal_transition",
    "crm_purge",
    "crm_merge_companies",
    "crm_search",
    "crm_query",
    "crm_query_rank",
    "crm_query_rank_type",
    "crm_flag_facts",
    "crm_set_flags",
    "crm_audit_row",
)
#: The seed's tasks, by title: (Twenty id, status, bucket, due in days, deleted, linked).
SEED = {
    "map-todo": (None, "todo", None, -5, False, True),
    "map-progress": (None, "in_progress", "this_week", None, False, True),
    "map-done": (None, "done", None, None, False, True),
    "map-twenty": ("tw-map-1", "todo", "later", None, False, True),
    "map-linkless": (None, "todo", "later", None, False, False),
    "map-deleted-bucketless": (None, "todo", None, 30, True, True),
}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web", "workspace_id": workspace}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _one(stack, query: str) -> dict:
    return stack.sql(query)[0]


def _ws(stack, key: str) -> str:
    return _one(stack, f"SELECT id FROM crm_workspaces WHERE key = {stack.q(key)}")["id"]


# ---------------------------------------------------------------------------
# Always: the state after 046-050
# ---------------------------------------------------------------------------


@pytest.mark.skipif(MODE == "seed", reason="the database is at 045 while it is seeded")
class TestAfterMigration:
    def test_the_status_check(self, stack):
        row = _one(
            stack,
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'public.tasks'::regclass AND conname = 'tasks_status'",
        )
        for key in ("open", "waiting", "done", "cancelled"):
            assert f"'{key}'" in row["def"]
        assert "todo" not in row["def"] and "in_progress" not in row["def"]

    def test_the_relaxed_keyed_active_check(self, stack):
        row = _one(
            stack,
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conname = 'crm_select_options_keyed_active'",
        )
        assert "task.status" in row["def"] and "todo" in row["def"] and "in_progress" in row["def"]

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_the_status_list(self, stack, key):
        rows = stack.sql(
            "SELECT system_key, is_active, is_default FROM crm_select_options "
            f"WHERE workspace_id = {stack.q(_ws(stack, key))} AND list_key = 'task.status'"
        )
        active = {r["system_key"] for r in rows if r["is_active"]}
        assert active == {"open", "waiting", "done", "cancelled"}
        assert {r["system_key"] for r in rows if not r["is_active"]} == {"todo", "in_progress"}
        assert [r["system_key"] for r in rows if r["is_default"]] == ["open"]

    def test_only_todo_and_in_progress_may_be_deactivated(self, stack):
        with pytest.raises(stack.SqlError) as raised:
            _as_system(
                stack,
                "UPDATE crm_select_options SET is_active = false "
                "WHERE list_key = 'task.status' AND system_key = 'waiting'",
            )
        assert raised.value.code == "23514"

    def test_saved_views_are_free_of_the_old_keys(self, stack):
        rows = stack.sql(
            "SELECT id, config FROM crm_saved_views WHERE object = 'task' AND ("
            "config::text LIKE '%\"todo\"%' OR config::text LIKE '%\"in_progress\"%' "
            "OR jsonb_path_exists(config, "
            '\'$.** ? (@.field == "status" && @.operator == "is_not" && @.value == "done")\'))'
        )
        assert rows == []

    def test_the_seeded_open_tasks_view_excludes_cancelled(self, stack):
        rows = stack.sql(
            "SELECT config -> 'filters' AS filters FROM crm_saved_views "
            "WHERE system_key = 'open_tasks_by_owner'"
        )
        assert rows
        for row in rows:
            assert {"field": "status", "operator": "none_of", "value": ["done", "cancelled"]} in (
                row["filters"]["items"]
            )

    def test_no_function_body_names_crm_task(self, stack):
        rows = stack.sql(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prosrc LIKE '%crm\\_task%'"
        )
        assert rows == []

    def test_the_field_registry_has_priority_and_source(self, stack):
        rows = stack.sql(
            "SELECT field, type FROM crm_object_fields WHERE object = 'task' "
            "AND field IN ('priority', 'source') ORDER BY field"
        )
        assert rows == [
            {"field": "priority", "type": "fixed_select"},
            {"field": "source", "type": "fixed_select"},
        ]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.mark.skipif(MODE == "seed", reason="the database is at 045 while it is seeded")
class TestEachReplacedFunctionRuns:
    """data-model §5.4 — each function 048 replaced, called once against
    the renamed tables (plpgsql resolves a table only when it runs)."""

    @pytest.fixture
    def world(self, stack, owner):
        key = f"t{uuid.uuid4().hex[:12]}"
        ws = _as_system(
            stack,
            "INSERT INTO crm_workspaces (key, name) "
            f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
        )[-1]["id"]
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
        )
        actor = _actor(ws, owner)
        made = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "ref": "a",
                    "values": {"name": "Alpha", "lead_stage": "deal_open", "owner_id": owner},
                },
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "ref": "b",
                    "values": {"name": "Beta", "owner_id": owner},
                },
                {
                    "op": "insert",
                    "table": "crm_opportunities",
                    "ref": "d",
                    "values": {
                        "name": "Alpha - deal",
                        "company_id": "$ref:a",
                        "owner_id": owner,
                        "probability": 10,
                        "next_step": "Call",
                        "next_step_on": "2026-10-20",
                    },
                },
                {
                    "op": "insert",
                    "table": "tasks",
                    "ref": "t",
                    "values": {"title": "Call Alpha", "assignee_id": owner, "bucket": "later"},
                },
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {
                        "task_id": "$ref:t",
                        "record_type": "company",
                        "record_id": "$ref:a",
                    },
                },
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {
                        "task_id": "$ref:t",
                        "record_type": "opportunity",
                        "record_id": "$ref:d",
                    },
                },
            ],
        )
        return {
            "ws": ws,
            "actor": actor,
            "a": made[0]["id"],
            "b": made[1]["id"],
            "deal": made[2]["id"],
            "task": made[3]["id"],
        }

    def test_crm_apply_crm_check_refs_and_crm_audit_row(self, stack, world):
        rows = stack.sql(
            "SELECT target_table, action FROM audit_log "
            f"WHERE target_id = {stack.q(world['task'])} ORDER BY id"
        )
        assert rows == [{"target_table": "tasks", "action": "task.created"}]

    def test_crm_query_with_both_rank_functions(self, stack, world):
        for field in ("status", "priority", "bucket"):
            spec = {"sorts": [{"field": field, "dir": "asc"}], "group_by": field, "limit": 10}
            answer = _one(
                stack,
                f"SELECT crm_query({stack.q(world['ws'])}::uuid, 'task', {_j(stack, spec)}, "
                "false) AS r",
            )["r"]
            assert [r["id"] for r in answer["rows"]] == [world["task"]]

    def test_crm_search(self, stack, world):
        found = _one(
            stack,
            f"SELECT crm_search({stack.q(world['ws'])}::uuid, 'Call Alpha', 5, false) AS r",
        )["r"]
        assert [t["id"] for t in found["tasks"]] == [world["task"]]

    def test_crm_flag_facts_and_crm_set_flags(self, stack, world):
        facts = _one(
            stack,
            f"SELECT crm_flag_facts({stack.q(world['ws'])}::uuid, NULL, NULL, NULL) AS r",
        )["r"]
        assert facts["open_task_companies"] == [world["a"]]
        rows = [{"id": world["task"], "flag_level": "amber", "flags": []}]
        changed = _one(
            stack,
            f"SELECT crm_set_flags({stack.q(world['ws'])}::uuid, 'tasks', {_j(stack, rows)}, "
            "now()) AS n",
        )["n"]
        assert changed == 1

    def test_crm_deal_transition(self, stack, world):
        reason = _as_system(
            stack,
            "INSERT INTO crm_select_options (workspace_id, list_key, system_key, label, position) "
            f"VALUES ({stack.q(world['ws'])}, 'opportunity.lost_reason', 'price', 'Price', 1) "
            "RETURNING id",
        )[-1]["id"]
        deal = _one(stack, f"SELECT * FROM crm_opportunities WHERE id = {stack.q(world['deal'])}")
        plan = {
            "opportunity_id": world["deal"],
            "expect_stage": deal["stage"],
            "expect_updated_at": deal["updated_at"],
            "opportunity": {
                "stage": "closed_lost",
                "stage_entered_at": "now",
                "probability": 0,
                "lost_reason_id": reason,
            },
            "history": {"from": deal["stage"], "to": "closed_lost"},
            "close_tasks": [world["task"]],
        }
        done = _one(
            stack,
            f"SELECT crm_deal_transition({_j(stack, world['actor'])}, {_j(stack, plan)}) AS r",
        )["r"]
        assert done["tasks_closed"] == 1
        row = _one(
            stack, f"SELECT status, completed_at FROM tasks WHERE id = {stack.q(world['task'])}"
        )
        assert row == {"status": "cancelled", "completed_at": None}

    def test_crm_merge_companies(self, stack, world):
        a = _one(stack, f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(world['a'])}")
        b = _one(stack, f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(world['b'])}")
        plan = {
            "survivor_id": world["b"],
            "merged_id": world["a"],
            "expect": {
                "survivor_updated_at": b["updated_at"],
                "merged_updated_at": a["updated_at"],
            },
            "survivor_changes": {},
        }
        _one(
            stack,
            f"SELECT crm_merge_companies({_j(stack, world['actor'])}, {_j(stack, plan)}) AS r",
        )
        links = stack.sql(
            "SELECT record_id FROM task_links "
            f"WHERE task_id = {stack.q(world['task'])} AND record_type = 'company'"
        )
        assert [lk["record_id"] for lk in links] == [world["b"]]

    def test_crm_purge(self, stack, world):
        _apply(
            stack,
            world["actor"],
            [{"op": "soft_delete", "table": "tasks", "id": world["task"]}],
        )
        plan = {"items": [{"table": "tasks", "id": world["task"]}]}
        done = _one(
            stack, f"SELECT crm_purge({_j(stack, world['actor'])}, {_j(stack, plan)}) AS r"
        )["r"]
        assert done == {"purged": 1}
        assert stack.sql(f"SELECT 1 FROM tasks WHERE id = {stack.q(world['task'])}") == []


# ---------------------------------------------------------------------------
# Only around a migration: seed at 045, check after 046-050
# ---------------------------------------------------------------------------


def _seed_owner(stack) -> str:
    return _one(stack, "SELECT id FROM profiles WHERE email = 'task-migration@example.com'")["id"]


@pytest.mark.skipif(MODE != "seed", reason="seeds a database reset to 045 only")
class TestSeed:
    def test_seed(self, stack):
        uid = str(uuid.uuid4())
        stack.sql(
            "INSERT INTO auth.users (id, instance_id, aud, role, email) VALUES "
            f"({stack.q(uid)}, '00000000-0000-0000-0000-000000000000', 'authenticated', "
            "'authenticated', 'task-migration@example.com')"
        )
        stack.sql(
            "INSERT INTO profiles (id, email, display_name) "
            f"VALUES ({stack.q(uid)}, 'task-migration@example.com', 'Mapping owner')"
        )
        labs, academy = _ws(stack, "labs"), _ws(stack, "academy")
        for ws in (labs, academy):
            _as_system(
                stack,
                "INSERT INTO crm_memberships (workspace_id, user_id, role) "
                f"VALUES ({stack.q(ws)}, {stack.q(uid)}, 'admin')",
            )
        actor = _actor(labs, uid)
        company = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Mapping Co", "owner_id": uid},
                }
            ],
        )[0]["id"]
        for title, (twenty, status, bucket, due, deleted, linked) in SEED.items():
            values = {"title": title, "assignee_id": uid, "status": status, "body": "notes"}
            if bucket:
                values["bucket"] = bucket
            if due is not None:
                values["due_on"] = (date.today() + timedelta(days=due)).isoformat()
            if status == "done":
                values["completed_at"] = "2026-10-01T00:00:00Z"
            op = {"op": "insert", "table": "crm_tasks", "ref": "t", "values": values}
            if twenty:
                op = {**op, "op": "upsert", "values": {**values, "twenty_id": twenty}}
            ops = [
                op,
                {
                    "op": "insert",
                    "table": "crm_task_links",
                    "ref": "l",
                    "values": {"task_id": "$ref:t", "record_type": "company", "record_id": company},
                },
            ]
            done = _apply(stack, actor, ops)
            task, link = done[0]["id"], done[1]["id"]
            if not linked:
                _apply(stack, actor, [{"op": "delete", "table": "crm_task_links", "id": link}])
            if deleted:
                _apply(
                    stack,
                    actor,
                    [
                        {
                            "op": "soft_delete",
                            "table": "crm_tasks",
                            "id": task,
                            "deleted_group": str(uuid.uuid4()),
                        }
                    ],
                )
        # An Admin renames Done in Labs, and To do to "Open" in Academy.
        _as_system(
            stack,
            "UPDATE crm_select_options SET label = 'Finished' "
            f"WHERE workspace_id = {stack.q(labs)} AND list_key = 'task.status' "
            "AND system_key = 'done'",
        )
        _as_system(
            stack,
            "UPDATE crm_select_options SET label = 'Open' "
            f"WHERE workspace_id = {stack.q(academy)} AND list_key = 'task.status' "
            "AND system_key = 'todo'",
        )
        # A view a person made, filtering on the old keys.
        config = {
            "filters": {
                "op": "and",
                "items": [
                    {"field": "status", "operator": "any_of", "value": ["todo", "in_progress"]},
                    {"field": "status", "operator": "is_not", "value": "done"},
                ],
            }
        }
        _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_saved_views",
                    "values": {
                        "object": "task",
                        "name": "Mapping view",
                        "visibility": "private",
                        "owner_id": uid,
                        "config": config,
                    },
                }
            ],
        )


@pytest.mark.skipif(MODE != "1", reason="set TASKS_MIGRATION_CHECK=1 after a seeded migration")
class TestMapping:
    @pytest.fixture
    def seeded(self, stack) -> dict[str, dict]:
        """Each seeded task by title, found by its id in the audit rows
        written before the rename (`target_table = 'crm_tasks'`)."""
        rows = stack.sql(
            "SELECT target_id, after ->> 'title' AS title FROM audit_log "
            "WHERE target_table = 'crm_tasks' AND action = 'task.created' "
            "AND after ->> 'title' LIKE 'map-%'"
        )
        found = {r["title"]: r["target_id"] for r in rows}
        assert set(found) == set(SEED)
        tasks = {
            r["id"]: r
            for r in stack.sql(
                "SELECT * FROM tasks WHERE id IN ("
                + ", ".join(stack.q(i) for i in found.values())
                + ")"
            )
        }
        return {title: tasks[i] for title, i in found.items()}

    def test_ids_titles_notes_and_assignees_are_kept(self, stack, seeded):
        owner = _seed_owner(stack)
        for title, row in seeded.items():
            assert (row["title"], row["body"], row["assignee_id"]) == (title, "notes", owner)

    def test_dates_are_kept(self, stack, seeded):
        """Due date, created and completed times as the seed wrote them, read
        from each task's creation row written before the rename."""
        rows = stack.sql(
            "SELECT t.title, "
            "t.due_on IS NOT DISTINCT FROM (a.after ->> 'due_on')::date AS due, "
            "t.created_at = (a.after ->> 'created_at')::timestamptz AS created, "
            "t.completed_at IS NOT DISTINCT FROM (a.after ->> 'completed_at')::timestamptz "
            "AS completed "
            "FROM tasks t JOIN audit_log a ON a.target_id = t.id::text "
            "AND a.target_table = 'crm_tasks' AND a.action = 'task.created' "
            "WHERE t.id IN (" + ", ".join(stack.q(r["id"]) for r in seeded.values()) + ")"
        )
        assert sorted(r["title"] for r in rows) == sorted(SEED)
        for row in rows:
            assert row["due"] and row["created"] and row["completed"], row["title"]
        assert seeded["map-todo"]["due_on"] is not None
        done = _one(
            stack,
            "SELECT completed_at = '2026-10-01T00:00:00Z'::timestamptz AS same FROM tasks "
            f"WHERE id = {stack.q(seeded['map-done']['id'])}",
        )
        assert done["same"] is True

    def test_the_linkless_count_046_reports(self, stack, seeded):
        """046's NOTICE counts the rows it moved to the Trash, each audited as
        the system; on the seeded database that is the one linkless task."""
        rows = stack.sql(
            "SELECT target_id FROM audit_log WHERE target_table = 'crm_tasks' "
            "AND action = 'task.deleted' AND actor_label = 'system'"
        )
        assert [r["target_id"] for r in rows] == [seeded["map-linkless"]["id"]]

    def test_the_statuses_are_mapped(self, seeded):
        assert seeded["map-todo"]["status"] == "open"
        assert seeded["map-progress"]["status"] == "open"
        assert seeded["map-progress"]["tags"] == ["in progress"]
        assert seeded["map-todo"]["tags"] == []
        assert seeded["map-done"]["status"] == "done" and seeded["map-done"]["completed_at"]

    def test_links_are_kept(self, stack, seeded):
        for title in ("map-todo", "map-progress", "map-done", "map-twenty"):
            links = stack.sql(
                f"SELECT record_type FROM task_links WHERE task_id = {stack.q(seeded[title]['id'])}"
            )
            assert [lk["record_type"] for lk in links] == ["company"], title

    def test_a_linkless_task_went_to_the_trash_as_the_system(self, stack, seeded):
        row = seeded["map-linkless"]
        assert row["deleted_at"] and row["deleted_group"] and row["deleted_by"] is None
        audit = _one(
            stack,
            "SELECT actor_label, action FROM audit_log "
            f"WHERE target_id = {stack.q(row['id'])} AND action = 'task.deleted'",
        )
        assert audit == {"actor_label": "system", "action": "task.deleted"}

    def test_every_open_or_waiting_row_has_a_bucket(self, stack, seeded):
        assert (
            stack.sql("SELECT id FROM tasks WHERE status IN ('open', 'waiting') AND bucket IS NULL")
            == []
        )
        assert seeded["map-todo"]["bucket"] == "today"  # due five days ago
        assert seeded["map-deleted-bucketless"]["bucket"] == "later"
        assert seeded["map-deleted-bucketless"]["deleted_at"]
        assert seeded["map-progress"]["bucket"] == "this_week"

    def test_sources(self, seeded):
        assert seeded["map-twenty"]["source"] == "import"
        assert {seeded[t]["source"] for t in SEED if t != "map-twenty"} == {"manual"}
        assert all(row["source_ref"] is None for row in seeded.values())

    def test_history_is_kept_under_both_names(self, stack, seeded):
        tables = [
            r["target_table"]
            for r in stack.sql(
                "SELECT target_table FROM audit_log "
                f"WHERE target_id = {stack.q(seeded['map-progress']['id'])} ORDER BY id"
            )
        ]
        assert tables[0] == "crm_tasks" and "tasks" in tables

    def test_a_record_timeline_shows_history_from_before_the_rename(
        self, stack, seeded, monkeypatch
    ):
        monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
        task = seeded["map-todo"]
        company = _one(
            stack, f"SELECT record_id FROM task_links WHERE task_id = {stack.q(task['id'])}"
        )["record_id"]
        rows = store.list_timeline(
            task["workspace_id"],
            "crm_companies",
            company,
            record_type="company",
            related={"tasks": [task["id"]]},
            limit=200,
        )
        tables = {r["target_table"] for r in rows}
        assert {"crm_tasks", "crm_task_links", "tasks"} <= tables

    def test_the_renamed_done_label_survives(self, stack):
        row = _one(
            stack,
            "SELECT label FROM crm_select_options "
            f"WHERE workspace_id = {stack.q(_ws(stack, 'labs'))} AND list_key = 'task.status' "
            "AND system_key = 'done'",
        )
        assert row["label"] == "Finished"

    def test_old_labels_are_not_carried_to_open(self, stack):
        rows = {
            r["system_key"]: r["label"]
            for r in stack.sql(
                "SELECT system_key, label FROM crm_select_options "
                f"WHERE workspace_id = {stack.q(_ws(stack, 'academy'))} "
                "AND list_key = 'task.status'"
            )
        }
        assert rows["open"] == "Open" and rows["todo"] == "Open (old)"
        assert rows["in_progress"] == "In progress"
        assert (rows["waiting"], rows["cancelled"]) == ("Waiting", "Cancelled")

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_exactly_one_default_per_status_list(self, stack, key):
        rows = stack.sql(
            "SELECT system_key FROM crm_select_options "
            f"WHERE workspace_id = {stack.q(_ws(stack, key))} AND list_key = 'task.status' "
            "AND is_default"
        )
        assert rows == [{"system_key": "open"}]

    def test_a_view_a_person_made_is_rewritten(self, stack):
        row = _one(
            stack,
            "SELECT config -> 'filters' -> 'items' AS items FROM crm_saved_views "
            "WHERE name = 'Mapping view'",
        )
        assert row["items"] == [
            {"field": "status", "operator": "any_of", "value": ["open"]},
            {"field": "status", "operator": "none_of", "value": ["done", "cancelled"]},
        ]
