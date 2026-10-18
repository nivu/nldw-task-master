"""Recurring tasks in the real database — spec 008 FR-TASK-RRULE-01 to -03;
data-model §2.1 (phase 3), §2.8, §5.2; migration 053.

What only Postgres can prove: a series has one live occurrence per due date
(`tasks_series_due`), so a second next occurrence is refused in a race and
an edit onto a taken date is refused, each with its sentence of data-model
§2.8; a deleted
occurrence frees its date; a recurring task has a due date
(`tasks_recurrence_due`); and only the next occurrence of a series may name
its creator, and only that series' creator, through `task_apply`. A CRM
task closed through the CRM gets its next occurrence from the same ops, in
one `task_apply` call (FR-TASK-RRULE-02).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from supabase import create_client

from app.domain.tasks import recurrence
from app.services import supabase as db
from app.services.crm import CrmRefused
from app.services.tasks import store

pytestmark = pytest.mark.db

RULE = "FREQ=WEEKLY;BYDAY=MO,TH"


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


def _recurring(stack, creator: str, **values) -> dict:
    """A recurring task of its own series, due Thursday 22 October 2026."""
    row = {
        "title": "Weekly review",
        "assignee_id": creator,
        "bucket": "later",
        "due_on": "2026-10-22",
        **values,
    }
    task = _apply(stack, creator, [{"op": "insert", "table": "tasks", "values": row}])[0]["row"]
    _apply(
        stack,
        creator,
        [
            {
                "op": "update",
                "table": "tasks",
                "id": task["id"],
                "values": {"recurrence": RULE, "series_id": task["id"]},
            }
        ],
    )
    return task


def _next(task: dict, due_on: str, **values) -> dict:
    return {
        "op": "insert",
        "table": "tasks",
        "values": {
            "title": task["title"],
            "assignee_id": task["assignee_id"],
            "bucket": "later",
            "due_on": due_on,
            "recurrence": RULE,
            "series_id": task["id"],
            **values,
        },
    }


@pytest.fixture
def people(make_profile) -> dict[str, str]:
    return {name: make_profile(keep=True) for name in ("creator", "assignee", "other")}


@pytest.fixture
def local(stack, monkeypatch) -> None:
    monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))


class TestSeriesDueDates:
    """Data-model §2.8 (FR-TASK-RRULE-03)."""

    def test_one_live_occurrence_per_due_date(self, stack, people):
        task = _recurring(stack, people["creator"])
        _apply(stack, people["creator"], [_next(task, "2026-10-26")])
        _refused(stack, _apply_sql(stack, people["creator"], [_next(task, "2026-10-26")]), "23505")

    def test_the_store_answers_409_with_the_sentence(self, stack, people, local):
        task = _recurring(stack, people["creator"])
        store.apply(_actor(people["creator"]), [_next(task, "2026-10-26")])
        done = {"status": "done", "completed_at": "2026-10-22T10:00:00+00:00"}
        close = {"op": "update", "table": "tasks", "id": task["id"], "values": done}
        with pytest.raises(CrmRefused) as raised:
            store._apply_closing(_actor(people["creator"]), [close], [_next(task, "2026-10-26")])
        assert (raised.value.status, raised.value.detail) == (409, store.SERIES_TAKEN)

    def test_an_edit_onto_a_taken_date_is_told_so(self, stack, people, local):
        task = _recurring(stack, people["creator"])
        store.apply(_actor(people["creator"]), [_next(task, "2026-10-26")])
        move = {
            "op": "update",
            "table": "tasks",
            "id": task["id"],
            "values": {"due_on": "2026-10-26"},
        }
        with pytest.raises(CrmRefused) as raised:
            store.apply(_actor(people["creator"]), [move])
        assert (raised.value.status, raised.value.detail) == (409, store.SERIES_DATE_TAKEN)

    def test_a_deleted_occurrence_frees_its_date(self, stack, people):
        task = _recurring(stack, people["creator"])
        made = _apply(stack, people["creator"], [_next(task, "2026-10-26")])[0]
        _apply(
            stack,
            people["creator"],
            [
                {
                    "op": "soft_delete",
                    "table": "tasks",
                    "id": made["id"],
                    "deleted_group": made["id"],
                }
            ],
        )
        assert _apply(stack, people["creator"], [_next(task, "2026-10-26")])[0]["id"]

    def test_tasks_of_no_series_share_due_dates(self, stack, people):
        row = {"title": "Call", "assignee_id": people["creator"], "bucket": "later"}
        ops = [{"op": "insert", "table": "tasks", "values": {**row, "due_on": "2026-10-26"}}]
        _apply(stack, people["creator"], ops)
        assert _apply(stack, people["creator"], ops)[0]["id"]


class TestRecurrenceColumns:
    """Data-model §2.1 phase 3 (FR-TASK-RRULE-01)."""

    def test_a_recurring_task_has_a_due_date(self, stack, people):
        task = _recurring(stack, people["creator"])
        _refused(
            stack,
            _apply_sql(
                stack,
                people["creator"],
                [{"op": "update", "table": "tasks", "id": task["id"], "values": {"due_on": None}}],
            ),
            "23514",
        )

    def test_a_new_task_has_not_spawned(self, stack, people):
        task = _recurring(stack, people["creator"])
        assert task["recurrence_spawned"] is False


class TestTheSeriesCreator:
    """Data-model §5.2, 053's change: the next occurrence keeps the series'
    creator (FR-TASK-RRULE-02)."""

    def test_the_next_occurrence_keeps_the_creator(self, stack, people):
        task = _recurring(stack, people["creator"], assignee_id=people["assignee"])
        made = _apply(
            stack,
            people["assignee"],
            [_next(task, "2026-10-26", created_by=people["creator"])],
        )[0]["row"]
        assert (made["created_by"], made["updated_by"]) == (people["creator"], people["assignee"])

    def test_only_the_series_creator(self, stack, people):
        task = _recurring(stack, people["creator"], assignee_id=people["assignee"])
        msg = _refused(
            stack,
            _apply_sql(
                stack, people["assignee"], [_next(task, "2026-10-26", created_by=people["other"])]
            ),
            "CRM22",
        )
        assert msg == "created_by cannot be set here."

    def test_not_without_a_series(self, stack, people):
        row = {
            "title": "Call",
            "assignee_id": people["assignee"],
            "bucket": "later",
            "created_by": people["creator"],
        }
        _refused(
            stack,
            _apply_sql(
                stack, people["assignee"], [{"op": "insert", "table": "tasks", "values": row}]
            ),
            "CRM22",
        )

    def test_never_on_an_update(self, stack, people):
        task = _recurring(stack, people["creator"])
        _refused(
            stack,
            _apply_sql(
                stack,
                people["creator"],
                [
                    {
                        "op": "update",
                        "table": "tasks",
                        "id": task["id"],
                        "values": {"created_by": people["other"]},
                    }
                ],
            ),
            "CRM22",
        )


class TestACrmTask:
    """FR-TASK-RRULE-02 — the CRM closes a recurring CRM task with the ops of
    `recurrence.next_occurrence_ops`, through `task_apply`
    (`services/crm/records._write_task`): a member who did not create it
    closes it, and the next occurrence keeps its creator, workspace and link."""

    def test_a_member_closes_it_and_the_next_keeps_creator_and_link(self, stack, people, local):
        key = f"t{uuid.uuid4().hex[:12]}"
        system = json.dumps({"kind": "system", "user_id": None, "channel": None})
        ws = stack.sql(
            f"SELECT set_config('crm.actor', {stack.q(system)}, true); "
            "INSERT INTO crm_workspaces (key, name) "
            f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id"
        )[-1]["id"]
        for who in ("creator", "other"):
            stack.sql(
                f"SELECT set_config('crm.actor', {stack.q(system)}, true); "
                "INSERT INTO crm_memberships (workspace_id, user_id, role) "
                f"VALUES ({stack.q(ws)}, {stack.q(people[who])}, 'member')"
            )
        crm_actor = {**_actor(people["creator"]), "workspace_id": ws}
        ops = [
            {
                "op": "insert",
                "table": "crm_companies",
                "ref": "c",
                "values": {"name": "Acme", "owner_id": people["creator"]},
            },
            {
                "op": "insert",
                "table": "tasks",
                "ref": "t",
                "values": {
                    "title": "Chase the PO",
                    "assignee_id": people["other"],
                    "bucket": "later",
                    "due_on": "2026-10-22",
                },
            },
            {
                "op": "insert",
                "table": "task_links",
                "values": {"task_id": "$ref:t", "record_type": "company", "record_id": "$ref:c"},
            },
        ]
        made = stack.sql(f"SELECT crm_apply({_j(stack, crm_actor)}, {_j(stack, ops)}) AS r")[0]["r"]
        task_id = made[1]["id"]
        _apply(
            stack,
            people["creator"],
            [
                {
                    "op": "update",
                    "table": "tasks",
                    "id": task_id,
                    "values": {"recurrence": RULE, "series_id": task_id},
                }
            ],
        )
        row = store.get_task(task_id)
        values = {"status": "done", "completed_at": "2026-10-22T10:00:00+00:00"}
        following = recurrence.next_occurrence_ops(
            row, {**row, **values}, store.list_links([task_id]), date(2026, 10, 20)
        )
        values["recurrence_spawned"] = True
        close = {"op": "update", "table": "tasks", "id": task_id, "values": values}
        done = store._apply_closing(_actor(people["other"]), [close], following)
        nxt = done[1]["row"]
        assert (nxt["workspace_id"], nxt["created_by"], str(nxt["due_on"])) == (
            ws,
            people["creator"],
            "2026-10-26",
        )
        assert [(lk["record_type"], lk["record_id"]) for lk in store.list_links([nxt["id"]])] == [
            ("company", made[0]["id"])
        ]
        assert store.get_task(task_id)["recurrence_spawned"] is True
