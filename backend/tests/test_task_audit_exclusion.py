"""Task audit rows never reach the TVA audit — spec 008 FR-TASK-VIS-05,
FR-TASK-CHG-07; contracts §3 `GET /admin/audit` (and `audit_log`).

A task with no workspace writes audit rows with no workspace, which the TVA
audit's old filter (`workspace_id IS NULL`, `007` FR-CRM-AUD-04) would have
let through. `services/supabase.list_audit` also leaves out every row whose
`target_table` is a task table. The audit table is faked as an in-memory
query that applies exactly the filters `list_audit` sends.

Acceptance criteria (spec §12, phase 1 — privacy):
- "No task audit entry appears on the TVA audit page or through audit_log" —
  TestTheTvaAudit.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from crm_fake import OWNER, FakeCrmStore
from crm_fake import client as crm_client

from app.api.deps import CurrentUser
from app.domain.tasks.service import TaskActor, create_task
from app.mcp import server
from app.schemas.tasks import TaskCreate
from app.services import supabase as db


class AuditQuery:
    """`supabase.table("audit_log")` over a list of rows: the calls
    `list_audit` makes, applied as PostgREST would."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = list(rows)

    def select(self, *_):
        return self

    def is_(self, column, value):
        assert value == "null"
        self.rows = [r for r in self.rows if r.get(column) is None]
        return self

    def filter(self, column, operator, criteria):
        assert operator == "not.in"
        excluded = set(criteria.strip("()").split(","))
        self.rows = [r for r in self.rows if r.get(column) not in excluded]
        return self

    def order(self, column, desc=False):
        self.rows.sort(key=lambda r: r.get(column) or "", reverse=desc)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def written(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    person = {**OWNER, "id": str(uuid.uuid4()), "tasks_level": 1}
    people = {OWNER["id"]: dict(OWNER), person["id"]: person}
    http = crm_client(monkeypatch, people, fake=fake)
    task = create_task(
        TaskActor(user=CurrentUser(dict(person)), channel="web"),
        TaskCreate(title="Book dentist", source="manual"),
    )
    tva_row = {
        "id": 10_000,
        "at": "2026-10-20T00:00:00+00:00",
        "actor_id": OWNER["id"],
        "workspace_id": None,
        "action": "user.updated",
        "target_table": "profiles",
        "target_id": person["id"],
    }
    rows = [*fake.audit, tva_row]
    monkeypatch.setattr(db.supabase, "table", lambda name: AuditQuery(rows))
    return http, task, rows


class TestTheTvaAudit:
    def test_the_task_wrote_an_audit_row_with_no_workspace(self, written):
        _, task, rows = written
        [row] = [r for r in rows if r.get("target_id") == task["id"]]
        assert (row["target_table"], row["workspace_id"]) == ("tasks", None)

    def test_list_audit_leaves_every_task_table_out(self, written):
        _, task, _ = written
        found = db.list_audit(100)
        assert [r["action"] for r in found] == ["user.updated"]
        assert db.TASK_AUDIT_TABLES == (
            "tasks",
            "task_links",
            "task_comments",
            "task_import_runs",
            "inbox_items",
        )

    def test_get_admin_audit_shows_none_of_it(self, written):
        http, task, _ = written
        r = http.get("/api/v1/admin/audit", headers={"Authorization": f"Bearer {OWNER['id']}"})
        assert r.status_code == 200, r.text
        assert [row["action"] for row in r.json()] == ["user.updated"]
        assert task["id"] not in {row.get("target_id") for row in r.json()}

    def test_the_audit_log_tool_shows_none_of_it(self, written):
        _, task, _ = written
        found = asyncio.run(server.audit_log(Ctx(OWNER["id"])))
        assert [row["action"] for row in found] == ["user.updated"]
