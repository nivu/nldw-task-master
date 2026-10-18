"""Tasks, their links and the workspace invariant in the real database —
spec 008 FR-TASK-DATA-05, -06, -15, FR-TASK-VIS-03; T3, T4; TP-13;
data-model §2.1, §2.2, §5.1; migrations 047, 048, 049, 056, 057.

What only Postgres can prove: the deferred `tasks_workspace_check` judges a
task at commit, after its links (both ways: a first CRM link written with the
workspace passes, a workspace with no CRM link fails); a workspace changes
only from empty to a value; one task cannot hold two workspaces' records; an
Academy task cannot link a project; deleting a task takes every link with
it, project links and tasks with no workspace included; a project link names
a real project; RLS is on with no browser grant and no browser EXECUTE; the
audit trigger, `crm_check_refs` and the composite workspace key are kept.

Acceptance criteria (spec §12, phase 1 — migration and CRM):
- "The database refuses, at commit, a live task whose workspace does not
  match its CRM links, and a live task with a workspace and no CRM link" —
  TestTheDeferredCheck.
- "Deleting a task cascades its link rows, including tva_project rows and
  rows of tasks with no workspace" — TestCascades.
- "An Academy task cannot link a TVA project (422) ... a second project link
  on a task is refused" — TestProjectLinks (the database half; the 404 and
  archived refusals are Python's: tests/test_task_create_interface.py).
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
FUNCTIONS = (
    "task_apply(jsonb,jsonb)",
    "task_purge_expired(jsonb,timestamp with time zone)",
    "task_tags_ok(text[])",
    "tasks_workspace_check()",
    "tasks_workspace_fixed()",
    "tasks_parent_check()",
)


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web"}


def _crm_actor(workspace: str, user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web", "workspace_id": workspace}


def _sql(stack, actor: dict, ops: list[dict]) -> str:
    return f"SELECT task_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r"


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(_sql(stack, actor, ops))[0]["r"]


def _crm_apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _refused(stack, query: str, *codes: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code in codes, raised.value.message
    return raised.value.message


def _workspace(stack, user: str, *, tva_linked: bool) -> str:
    """A new workspace, or for a TVA-linked one the seeded Labs (only one
    workspace is TVA-linked, 026)."""
    if tva_linked:
        ws = stack.sql("SELECT id FROM crm_workspaces WHERE tva_linked")[0]["id"]
    else:
        key = f"t{uuid.uuid4().hex[:12]}"
        ws = _as_system(
            stack,
            "INSERT INTO crm_workspaces (key, name) "
            f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
        )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(user)}, 'admin')",
    )
    return ws


def _company(stack, ws: str, user: str) -> str:
    return _crm_apply(
        stack,
        _crm_actor(ws, user),
        [{"op": "insert", "table": "crm_companies", "values": {"name": "Acme", "owner_id": user}}],
    )[0]["id"]


def _personal(stack, user: str) -> str:
    return _apply(
        stack,
        _actor(user),
        [
            {
                "op": "insert",
                "table": "tasks",
                "values": {"title": "Mine", "assignee_id": user, "bucket": "later"},
            }
        ],
    )[0]["id"]


def _project(stack) -> str:
    name = f"Task links db {uuid.uuid4().hex[:8]}"
    return stack.sql(f"INSERT INTO projects (name) VALUES ({stack.q(name)}) RETURNING id")[0]["id"]


def _project_link(task: str, project: str) -> dict:
    return {
        "op": "insert",
        "table": "task_links",
        "values": {
            "task_id": task,
            "record_type": "tva_project",
            "record_id": project,
            "project_id": project,
        },
    }


@pytest.fixture
def user(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def labs(stack, user) -> str:
    return _workspace(stack, user, tva_linked=True)


@pytest.fixture
def academy(stack, user) -> str:
    return _workspace(stack, user, tva_linked=False)


class TestTheDeferredCheck:
    """FR-TASK-DATA-05, -15 — judged at commit, after the links."""

    def test_a_first_crm_link_with_its_workspace_in_one_call(self, stack, user, labs):
        task = _personal(stack, user)
        company = _company(stack, labs, user)
        _apply(
            stack,
            _actor(user),
            [
                {"op": "update", "table": "tasks", "id": task, "values": {"workspace_id": labs}},
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {
                        "task_id": task,
                        "workspace_id": labs,
                        "record_type": "company",
                        "record_id": company,
                    },
                },
            ],
        )
        row = stack.sql(f"SELECT workspace_id FROM tasks WHERE id = {stack.q(task)}")[0]
        assert row["workspace_id"] == labs

    def test_a_workspace_with_no_crm_link_is_refused_at_commit(self, stack, user, labs):
        task = _personal(stack, user)
        message = _refused(
            stack,
            _sql(
                stack,
                _actor(user),
                [{"op": "update", "table": "tasks", "id": task, "values": {"workspace_id": labs}}],
            ),
            "CRM22",
        )
        assert message == "A CRM task needs at least one linked record; delete it instead."

    def test_removing_the_last_crm_link_is_refused_at_commit(self, stack, user, labs):
        company = _company(stack, labs, user)
        task = _crm_apply(
            stack,
            _crm_actor(labs, user),
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
                    "ref": "l",
                    "values": {"task_id": "$ref:t", "record_type": "company", "record_id": company},
                },
            ],
        )
        link = task[1]["id"]
        _refused(
            stack,
            _sql(stack, _actor(user), [{"op": "delete", "table": "task_links", "id": link}]),
            "CRM22",
        )

    def test_two_removals_at_once_cannot_take_the_last_link(self, stack, user, labs):
        """056: a removal locks its task, so the second waits for the first
        and then counts what it left."""
        companies = [_company(stack, labs, user) for _ in range(2)]
        made = _crm_apply(
            stack,
            _crm_actor(labs, user),
            [
                {
                    "op": "insert",
                    "table": "tasks",
                    "ref": "t",
                    "values": {"title": "Call", "assignee_id": user, "bucket": "later"},
                },
                *(
                    {
                        "op": "insert",
                        "table": "task_links",
                        "values": {
                            "task_id": "$ref:t",
                            "record_type": "company",
                            "record_id": company,
                        },
                    }
                    for company in companies
                ),
            ],
        )
        task, first, second = made[0]["id"], made[1]["id"], made[2]["id"]
        remove = [{"op": "delete", "table": "task_links", "id": first}]
        failed: list[Exception] = []

        def first_removal() -> None:
            # Checked at once, then its transaction is held open.
            try:
                stack.sql(
                    f"BEGIN; {_sql(stack, _actor(user), remove)}; SET CONSTRAINTS ALL IMMEDIATE; "
                    "SELECT pg_sleep(2); COMMIT;"
                )
            except Exception as exc:
                failed.append(exc)

        slow = threading.Thread(target=first_removal)
        slow.start()
        time.sleep(0.5)
        try:
            _refused(
                stack,
                _sql(stack, _actor(user), [{"op": "delete", "table": "task_links", "id": second}]),
                "CRM22",
            )
        finally:
            slow.join()
        assert failed == []
        left = stack.sql(f"SELECT id FROM task_links WHERE task_id = {stack.q(task)}")
        assert [r["id"] for r in left] == [second]

    def test_two_link_moves_at_once_do_not_deadlock(self, stack, user, labs):
        """057: each transaction adds one link and removes another of the
        same task (a company merge moving a link while a person edits the
        task's links). The insert's foreign key check holds KEY SHARE on the
        task; the removal's lock at commit must not conflict with it, or
        each waits for the other and one is aborted 40P01."""
        companies = [_company(stack, labs, user) for _ in range(4)]
        made = _crm_apply(
            stack,
            _crm_actor(labs, user),
            [
                {
                    "op": "insert",
                    "table": "tasks",
                    "ref": "t",
                    "values": {"title": "Call", "assignee_id": user, "bucket": "later"},
                },
                *(
                    {
                        "op": "insert",
                        "table": "task_links",
                        "values": {
                            "task_id": "$ref:t",
                            "record_type": "company",
                            "record_id": company,
                        },
                    }
                    for company in companies[:2]
                ),
            ],
        )
        task, links = made[0]["id"], [made[1]["id"], made[2]["id"]]

        def move(removed: str, added: str) -> list[dict]:
            return [
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {"task_id": task, "record_type": "company", "record_id": added},
                },
                {"op": "delete", "table": "task_links", "id": removed},
            ]

        failed: list[Exception] = []

        def run(removed: str, added: str) -> None:
            # Both reach commit while the other still holds its KEY SHARE.
            try:
                ops = _j(stack, move(removed, added))
                stack.sql(
                    f"BEGIN; SELECT crm_apply({_j(stack, _crm_actor(labs, user))}, {ops}); "
                    "SELECT pg_sleep(1.5); COMMIT;"
                )
            except Exception as exc:
                failed.append(exc)

        first = threading.Thread(target=run, args=(links[0], companies[2]))
        second = threading.Thread(target=run, args=(links[1], companies[3]))
        first.start()
        time.sleep(0.4)
        second.start()
        first.join()
        second.join()
        assert failed == []
        left = stack.sql(
            f"SELECT record_id FROM task_links WHERE task_id = {stack.q(task)} ORDER BY record_id"
        )
        assert [r["record_id"] for r in left] == sorted(companies[2:])

    def test_the_workspace_changes_only_from_empty_to_a_value(self, stack, user, labs, academy):
        company = _company(stack, labs, user)
        [task, _] = _crm_apply(
            stack,
            _crm_actor(labs, user),
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
        for value in (academy, None):
            message = _refused(
                stack,
                _sql(
                    stack,
                    _actor(user),
                    [
                        {
                            "op": "update",
                            "table": "tasks",
                            "id": task["id"],
                            "values": {"workspace_id": value},
                        }
                    ],
                ),
                "CRM22",
                "23503",
            )
            assert message

    def test_records_of_two_workspaces_are_refused(self, stack, user, labs, academy):
        mine = _company(stack, labs, user)
        theirs = _company(stack, academy, user)
        task = _personal(stack, user)
        _refused(
            stack,
            _sql(
                stack,
                _actor(user),
                [
                    {
                        "op": "update",
                        "table": "tasks",
                        "id": task,
                        "values": {"workspace_id": labs},
                    },
                    {
                        "op": "insert",
                        "table": "task_links",
                        "values": {
                            "task_id": task,
                            "workspace_id": labs,
                            "record_type": "company",
                            "record_id": mine,
                        },
                    },
                    {
                        "op": "insert",
                        "table": "task_links",
                        "values": {
                            "task_id": task,
                            "workspace_id": academy,
                            "record_type": "company",
                            "record_id": theirs,
                        },
                    },
                ],
            ),
            "CRM22",
            "23503",
            "CRM04",
        )


class TestProjectLinks:
    """FR-TASK-DATA-06, -15; TP-13."""

    def test_a_personal_task_links_a_project(self, stack, user):
        task = _personal(stack, user)
        project = _project(stack)
        [link] = _apply(stack, _actor(user), [_project_link(task, project)])
        assert (link["row"]["workspace_id"], link["row"]["project_id"]) == (None, project)

    def test_an_academy_task_cannot(self, stack, user, academy):
        company = _company(stack, academy, user)
        [task, _] = _crm_apply(
            stack,
            _crm_actor(academy, user),
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
        message = _refused(
            stack, _sql(stack, _actor(user), [_project_link(task["id"], _project(stack))]), "CRM22"
        )
        assert message == "Only Nunnari Labs tasks can link a TVA project."

    def test_a_second_project_is_refused(self, stack, user):
        task = _personal(stack, user)
        _apply(stack, _actor(user), [_project_link(task, _project(stack))])
        _refused(stack, _sql(stack, _actor(user), [_project_link(task, _project(stack))]), "23505")

    def test_a_project_link_names_a_real_project(self, stack, user):
        task = _personal(stack, user)
        _refused(
            stack, _sql(stack, _actor(user), [_project_link(task, str(uuid.uuid4()))]), "23503"
        )

    def test_a_project_link_has_no_workspace(self, stack, user, labs):
        task = _personal(stack, user)
        project = _project(stack)
        link = _project_link(task, project)
        link["values"]["workspace_id"] = labs
        _refused(stack, _sql(stack, _actor(user), [link]), "23514", "23503")


class TestCascades:
    """FR-TASK-DATA-15 — every link row carries a plain `task_id` key."""

    def test_deleting_a_personal_task_takes_its_project_link(self, stack, user):
        task = _personal(stack, user)
        _apply(stack, _actor(user), [_project_link(task, _project(stack))])
        _apply(stack, _actor(user), [{"op": "delete", "table": "tasks", "id": task}])
        assert stack.sql(f"SELECT 1 FROM task_links WHERE task_id = {stack.q(task)}") == []

    def test_deleting_a_crm_task_takes_every_link(self, stack, user, labs):
        company = _company(stack, labs, user)
        [task, _] = _crm_apply(
            stack,
            _crm_actor(labs, user),
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
        _apply(stack, _actor(user), [_project_link(task["id"], _project(stack))])
        _crm_apply(
            stack, _crm_actor(labs, user), [{"op": "delete", "table": "tasks", "id": task["id"]}]
        )
        assert stack.sql(f"SELECT 1 FROM task_links WHERE task_id = {stack.q(task['id'])}") == []


class TestTheBrowser:
    """FR-TASK-VIS-03 (T4) — RLS on, no policy, no grant, no EXECUTE."""

    @pytest.mark.parametrize("table", ["tasks", "task_links"])
    def test_rls_on_and_no_grant(self, stack, table):
        row = stack.sql(
            "SELECT c.relrowsecurity AS rls, "
            f"has_table_privilege('anon', 'public.{table}', 'SELECT') AS anon, "
            f"has_table_privilege('authenticated', 'public.{table}', "
            "'SELECT,INSERT,UPDATE,DELETE') AS authed, "
            f"(SELECT count(*) FROM pg_policy WHERE polrelid = 'public.{table}'::regclass) "
            "AS policies "
            f"FROM pg_class c WHERE c.oid = 'public.{table}'::regclass"
        )[0]
        assert row == {"rls": True, "anon": False, "authed": False, "policies": 0}

    @pytest.mark.parametrize("role", ["anon", "authenticated"])
    def test_execute_is_denied(self, stack, role):
        for fn in FUNCTIONS:
            row = stack.sql(
                f"SELECT has_function_privilege({stack.q(role)}, "
                f"{stack.q('public.' + fn)}, 'EXECUTE') AS ok"
            )[0]
            assert row["ok"] is False, fn


class TestKept:
    """The 007 machinery the rename keeps."""

    @pytest.mark.parametrize("table", ["tasks", "task_links"])
    def test_the_audit_trigger_on_insert_update_and_delete(self, stack, table):
        [row] = stack.sql(
            "SELECT tgtype FROM pg_trigger "
            f"WHERE tgrelid = 'public.{table}'::regclass AND tgname = {stack.q(table + '_crm_audit')} "
            "AND tgfoid = 'public.crm_audit_row'::regproc"
        )
        # tgtype bits: 1 row, 4 insert, 8 delete, 16 update.
        assert row["tgtype"] & (1 | 4 | 8 | 16) == (1 | 4 | 8 | 16)

    def test_crm_check_refs_on_task_links(self, stack):
        assert stack.sql(
            "SELECT 1 FROM pg_trigger WHERE tgrelid = 'public.task_links'::regclass "
            "AND tgname = 'task_links_check_refs' AND tgfoid = 'public.crm_check_refs'::regproc"
        )

    def test_the_composite_workspace_key_is_kept(self, stack):
        [row] = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'public.task_links'::regclass AND conname = 'task_links_task'"
        )
        assert row["def"] == (
            "FOREIGN KEY (workspace_id, task_id) REFERENCES tasks(workspace_id, id) "
            "ON DELETE CASCADE"
        )

    def test_the_plain_task_key(self, stack):
        [row] = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'public.task_links'::regclass AND conname = 'task_links_task_id'"
        )
        assert row["def"] == "FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE"
