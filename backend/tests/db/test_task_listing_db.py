"""My Tasks' reads against the real database — spec 008 FR-TASK-LIST-01,
-07; contracts §1 `GET /tasks`.

What only PostgREST can prove about `store.assigned_tasks`: the closed page's
`(updated_at, id)` cursor skips none of the tasks one transaction closed
together (they share `now()`), also beside a text filter (two `or` filters);
and a tag with a comma or a quote is one array element, not a broken one.
"""

from __future__ import annotations

import json

import pytest
from supabase import create_client

from app.services import supabase as db
from app.services.tasks import store

pytestmark = pytest.mark.db

#: 030 `crm_tasks_completed`: a done task has a completed time.
DONE = {"status": "done", "completed_at": "2026-10-01T00:00:00+00:00"}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(
        f"SELECT task_apply({stack.q(json.dumps(actor))}::jsonb, "
        f"{stack.q(json.dumps(ops))}::jsonb) AS r"
    )[0]["r"]


@pytest.fixture
def user(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def local(stack, monkeypatch) -> None:
    monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))


def _tasks(stack, user: str, *values: dict) -> list[str]:
    """These tasks, in one transaction: one `updated_at` for all."""
    ops = [{"op": "insert", "table": "tasks", "values": {"assignee_id": user, **v}} for v in values]
    return [r["id"] for r in _apply(stack, {"user_id": user, "kind": "import"}, ops)]


def _pages(user: str, **narrow) -> list[str]:
    seen: list[str] = []
    before = None
    while True:
        page = store.assigned_tasks(user, ("done",), before=before, limit=2, **narrow)
        seen += [r["id"] for r in page]
        if len(page) < 2:
            return seen
        before = (page[-1]["updated_at"], page[-1]["id"])


class TestTheClosedCursor:
    def test_tasks_closed_together_are_all_paged(self, stack, user, local):
        made = _tasks(stack, user, *({"title": f"Done {n}", **DONE} for n in range(5)))
        assert sorted(_pages(user)) == sorted(made)

    def test_beside_a_text_filter(self, stack, user, local):
        made = _tasks(
            stack,
            user,
            *({"title": f"Zqxpage {n}", **DONE} for n in range(3)),
            {"title": "Other", **DONE},
        )
        assert sorted(_pages(user, q="zqxpage")) == sorted(made[:3])


class TestTheTagFilter:
    def test_a_tag_with_a_comma_or_quote_is_one_tag(self, stack, user, local):
        comma, quote, _ = _tasks(
            stack,
            user,
            {"title": "Comma", "bucket": "later", "tags": ["a,b"]},
            {"title": "Quote", "bucket": "later", "tags": ['say "hi"\\']},
            {"title": "Both", "bucket": "later", "tags": ["a", "b"]},
        )
        found = store.assigned_tasks(user, ("open",), tag="a,b")
        assert [r["id"] for r in found] == [comma]
        found = store.assigned_tasks(user, ("open",), tag='say "hi"\\')
        assert [r["id"] for r in found] == [quote]
        assert [r["id"] for r in store.assigned_tasks(user, ("open",), tag="{")] == []
