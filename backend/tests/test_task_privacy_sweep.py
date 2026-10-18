"""A private task stays private, swept — spec 008 FR-TASK-VIS-06 (T4; replaces
the source's "RLS test"), FR-TASK-VIS-02, -05, -07, FR-TASK-DATA-09; plan §8
"Privacy sweep".

The route list comes from `app.routes` and the tool list from the MCP
registry. **Scope**, so 009's new routes and tools never need an edit here:
a fixture is required for every route under `/api/v1/tasks`, every
`task_*` and `inbox_add` tool, and every entry of `EXISTING`, the CRM, TVA
and admin routes and `crm_*` tools that return, count, search, export or
audit-read a task. Any other `/api/v1/crm` route or `crm_*` tool is left to
the CRM isolation sweep (`test_crm_isolation_sweep.py`; CRM routes filter on
the workspace, so a task with no workspace cannot reach them) and is listed
in the test output, not failed. A route or tool in scope with no fixture
fails `test_every_route_and_tool_in_scope_has_a_fixture`; the committed
self-test proves that check fails when one is added.

Chitra (a Labs Member whose TVA lead is Lakshmi) holds a private task, a
shared personal task first given to Farah and then reassigned to Arun and
waiting on Wasim, one handed back to her by Farah, and a deleted one; the
private and the shared one carry a comment of hers (S2a). Phase 2 is on for
the cast, so the comment routes answer them too, and so do the views, the
project panel and the brief (S2b), where the shared task is Chitra's
"nudge?". Each of the ten account kinds of FR-TASK-VIS-06 — a TVA admin,
a manager, the owner, the creator's TVA lead, a CRM workspace Admin, a
Viewer, a `crm_only` account, the former assignee, the Waiting-on person
and a removed workspace member — calls every fixture; no answer carries a
marker string or one of the tasks' ids, every task route given one of their
ids answers 404, and the tasks are unchanged afterwards. `TestTheMatrix`
then tries each of the ten against the private and the shared personal task
one at a time (twenty cells), and checks that the cast really holds the ten
kinds.

FR-TASK-VIS-06's further proofs: a removed workspace member reads none of
the workspace's CRM tasks they created or were assigned, through the task
routes, the CRM routes or the tools; a task whose parent is private shows
no parent id or title; and the private-era history of a task later linked
to a CRM record reaches no workspace member through the task panel, the
record and task timelines, the workspace audit or `crm_get`.

Acceptance criteria (spec §12, phase 1 — privacy):
- "FR-TASK-VIS-06 passes for every route and tool, and fails when a route or
  tool is added without a fixture" — TestScope, TestTheTenAccounts,
  TestTheMatrix.
- "A CRM task is visible to every active workspace member and to no
  non-member, including its creator or assignee after removal from the
  workspace, and including leads, managers and admins outside the workspace
  when it links a project" — TestCrmLinksWin.
- "A project task with no CRM link is visible to every lead, manager and
  admin, every TVA user who is or was allocated to the project, its creator
  and its assignee, and to no other TVA user or crm_only account; a creator
  or assignee who cannot see the project sees only the project's name" —
  TestProjectTasks.
- "A child task whose parent is private shows no parent id or title" —
  TestParentAndHistory.
- "A private task later linked to a CRM record shows its earlier history
  only to its creator and assignee" — TestParentAndHistory.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from crm_fake import as_
from fastapi import APIRouter
from fastapi.routing import APIRoute, iter_route_contexts
from tasks_fake import ID, TASK_PEOPLE, TaskWorld

from app.main import app
from app.mcp import crm as crm_tools
from app.mcp import server
from app.mcp import tasks as task_tools
from app.mcp.server import ToolError

TASKS = "/api/v1/tasks"
MARKS = ("zqxpriv", "zqxshared", "zqxhanded", "zqxtrash")
#: FR-TASK-VIS-06's ten account kinds, by `tasks_fake.ID` key.
TEN = (
    "outsider",  # a TVA admin
    "manager",
    "owner",
    "lead",  # the creator's TVA lead
    "labsadmin",  # a CRM workspace Admin
    "viewer",
    "seller",  # crm_only
    "former",  # the former assignee, after reassignment and after hand-back
    "waiting",  # the person named in Waiting on
    "removed",  # a removed workspace member
)

#: The existing CRM, TVA and admin routes and `crm_*` tools that return,
#: count, search, export or audit-read a task.
EXISTING = {
    ("GET", "/api/v1/crm/tasks"),
    ("GET", "/api/v1/crm/tasks/{record_id}"),
    ("GET", "/api/v1/crm/tasks/{record_id}/timeline"),
    ("GET", "/api/v1/crm/companies/{record_id}"),
    ("GET", "/api/v1/crm/companies/{record_id}/timeline"),
    ("GET", "/api/v1/crm/opportunities/{record_id}"),
    ("GET", "/api/v1/crm/opportunities/{record_id}/timeline"),
    ("GET", "/api/v1/crm/today"),
    ("GET", "/api/v1/crm/search"),
    ("POST", "/api/v1/crm/query"),
    ("POST", "/api/v1/crm/export/view"),
    ("GET", "/api/v1/crm/export/workspace"),
    ("GET", "/api/v1/crm/dashboard"),
    ("GET", "/api/v1/crm/audit"),
    ("GET", "/api/v1/crm/tva-admin/audit"),
    ("GET", "/api/v1/admin/audit"),
    ("tool", "crm_list"),
    ("tool", "crm_get"),
    ("tool", "crm_today"),
    ("tool", "crm_search"),
    ("tool", "audit_log"),
}


# ---------------------------------------------------------------------------
# The registries and the scope
# ---------------------------------------------------------------------------


def _route_keys(routes: list) -> set[tuple[str, str]]:
    return {
        (method, rc.path or "")
        for rc in iter_route_contexts(routes)
        if isinstance(rc.original_route, APIRoute)
        for method in rc.original_route.methods
    }


def _tool_names(tools: list) -> set[str]:
    return {t.name for t in tools}


def _in_scope(path: str) -> bool:
    return path == TASKS or path.startswith(TASKS + "/")


def required(routes: set[tuple[str, str]], tools: set[str]) -> set[tuple[str, str]]:
    """Every route and tool that must have a fixture."""
    return (
        {(m, p) for m, p in routes if _in_scope(p)}
        | {("tool", n) for n in tools if n.startswith("task_") or n == "inbox_add"}
        | EXISTING
    )


def missing(routes: set[tuple[str, str]], tools: set[str]) -> set[tuple[str, str]]:
    return required(routes, tools) - set(FIXTURES)


def _registries() -> tuple[set[tuple[str, str]], set[str]]:
    return _route_keys(app.routes), _tool_names(asyncio.run(server.mcp.list_tools()))


# ---------------------------------------------------------------------------
# The world: Chitra's tasks
# ---------------------------------------------------------------------------


class Sweep:
    def __init__(self, monkeypatch) -> None:
        w = self.w = TaskWorld(monkeypatch)
        # S2a: phase 2 on for everyone who has phase 1, so the comment and
        # delegation routes answer the ten accounts with 404, not 403.
        for person in w.fake.profiles.values():
            if person.get("tasks_level") == 1:
                person["tasks_level"] = 2
        self.private = w.add(
            "creator",
            title="Zqxpriv dentist",
            notes="Zqxpriv root canal",
            tags=["zqxpriv"],
            due_on="2026-10-20",
        )
        self.shared = w.add(
            "creator", title="Zqxshared plan", notes="Zqxshared notes", assignee_id="former"
        )
        self._as("creator", "PATCH", self.shared, json={"assignee_id": ID["assignee"]})
        self._as(
            "creator",
            "PATCH",
            self.shared,
            json={"status": "waiting", "waiting_on_id": ID["waiting"]},
        )
        # S2b: idle long enough to be Chitra's "nudge?" in her brief.
        w.tasks[self.shared["id"]]["updated_at"] = "2026-10-01T06:30:00+00:00"
        self.handed = w.add("creator", title="Zqxhanded back", assignee_id="former")
        self._as("former", "PATCH", self.handed, json={"assignee_id": ID["creator"]})
        for task, mark in ((self.private, "Zqxpriv"), (self.shared, "Zqxshared")):
            r = w.call("creator", "POST", f"/{task['id']}/comments", json={"body": f"{mark} said"})
            assert r.status_code == 200, r.text
        self.trashed = w.add("creator", title="Zqxtrash old", tags=["zqxtrash"])
        self._as("creator", "DELETE", self.trashed)
        del w.fake.rows["crm_memberships"][w.removed["id"]]
        self.targets = [self.private, self.shared, self.handed, self.trashed]
        self.ids = [t["id"] for t in self.targets]

    def _as(self, who: str, method: str, task: dict, **kwargs: Any) -> None:
        r = self.w.call(who, method, f"/{task['id']}", **kwargs)
        assert r.status_code == 200, r.text

    def rows(self) -> dict[str, dict[str, Any]]:
        return {i: dict(self.w.tasks[i]) for i in self.ids}


#: One answer: (status or None for a tool, text, whether it must be a 404).
Answer = tuple[int | None, str, bool]


def _http(s: Sweep, who: str, method: str, path: str, *, must_404: bool = False, **kw):
    r = s.w.http.request(method, path, headers=as_(ID[who]), **kw)
    try:
        body = r.json()
        if isinstance(body, dict):
            body.pop("instance", None)  # the request's own path
        text = json.dumps(body)
    except ValueError:
        text = r.text
    return (r.status_code, text, must_404)


def _tool(call) -> Answer:
    try:
        return (None, json.dumps(asyncio.run(call)), False)
    except ToolError as exc:
        return (None, str(exc), False)


class Ctx:
    def __init__(self, who: str) -> None:
        self.headers = {"authorization": f"Bearer {ID[who]}"}


def _each(s: Sweep, who: str, method: str, suffix: str, **kw) -> list[Answer]:
    return [_http(s, who, method, f"{TASKS}/{i}{suffix}", must_404=True, **kw) for i in s.ids]


LABS = {"workspace": "labs"}
#: A Life OS file for the import routes: a row of the caller's own.
ZQX_CSV = b"Task,Status,Owner\nZqx import row,Open,Chitra Creator\n"

FIXTURES: dict[tuple[str, str], Callable[[Sweep, str], list[Answer]]] = {
    # -- every task route -------------------------------------------------
    ("GET", TASKS): lambda s, who: [
        _http(s, who, "GET", TASKS, params=p)
        for p in (
            {},
            {"q": "zqx"},
            {"tag": "zqxpriv"},
            {"closed_limit": 100},
            {"view": "waiting"},
            {"view": "assigned_by_me"},
        )
    ],
    ("POST", TASKS): lambda s, who: [
        _http(s, who, "POST", TASKS, json={"title": "x", "parent_id": i}, must_404=True)
        for i in s.ids
    ],
    ("POST", f"{TASKS}/audience"): lambda s, who: [
        _http(s, who, "POST", f"{TASKS}/audience", json={"assignee_id": ID["creator"]})
    ],
    ("POST", f"{TASKS}/quick-add/preview"): lambda s, who: [
        _http(s, who, "POST", f"{TASKS}/quick-add/preview", json={"text": "zqx fri @Chitra"})
    ],
    ("GET", f"{TASKS}/people"): lambda s, who: [
        _http(s, who, "GET", f"{TASKS}/people", params={"q": "zqx"})
    ],
    ("GET", f"{TASKS}/linkable"): lambda s, who: [
        _http(s, who, "GET", f"{TASKS}/linkable", params={"q": "zqx"})
    ],
    ("GET", f"{TASKS}/trash"): lambda s, who: [_http(s, who, "GET", f"{TASKS}/trash")],
    ("POST", f"{TASKS}/import/dry-run"): lambda s, who: [
        _http(s, who, "POST", f"{TASKS}/import/dry-run", files={"file": ("t.csv", ZQX_CSV)})
    ],
    ("POST", f"{TASKS}/import/{{run_id}}/apply"): lambda s, who: [
        _http(
            s,
            who,
            "POST",
            f"{TASKS}/import/{uuid.uuid4()}/apply",
            files={"file": ("t.csv", ZQX_CSV)},
        )
    ],
    ("GET", f"{TASKS}/settings"): lambda s, who: [_http(s, who, "GET", f"{TASKS}/settings")],
    ("PATCH", f"{TASKS}/settings"): lambda s, who: [
        _http(s, who, "PATCH", f"{TASKS}/settings", json={"nudge_days": 5})
    ],
    ("GET", f"{TASKS}/brief"): lambda s, who: [_http(s, who, "GET", f"{TASKS}/brief")],
    ("GET", f"{TASKS}/project/{{project_id}}"): lambda s, who: [
        _http(s, who, "GET", f"{TASKS}/project/{s.w.lumen['id']}", params=p)
        for p in ({}, {"include_closed": True})
    ],
    ("GET", f"{TASKS}/{{id}}"): lambda s, who: _each(s, who, "GET", ""),
    ("PATCH", f"{TASKS}/{{id}}"): lambda s, who: _each(
        s, who, "PATCH", "", json={"priority": "low"}
    ),
    ("DELETE", f"{TASKS}/{{id}}"): lambda s, who: _each(s, who, "DELETE", ""),
    ("POST", f"{TASKS}/{{id}}/complete"): lambda s, who: _each(s, who, "POST", "/complete"),
    ("POST", f"{TASKS}/{{id}}/reopen"): lambda s, who: _each(s, who, "POST", "/reopen"),
    ("POST", f"{TASKS}/{{id}}/restore"): lambda s, who: _each(s, who, "POST", "/restore"),
    ("POST", f"{TASKS}/{{id}}/links"): lambda s, who: _each(
        s, who, "POST", "/links", json=s.w.link(s.w.acme, "company")
    ),
    ("DELETE", f"{TASKS}/{{id}}/links/{{link_id}}"): lambda s, who: _each(
        s, who, "DELETE", f"/links/{uuid.uuid4()}"
    ),
    ("POST", f"{TASKS}/{{id}}/accept"): lambda s, who: _each(s, who, "POST", "/accept"),
    ("POST", f"{TASKS}/{{id}}/hand-back"): lambda s, who: _each(
        s, who, "POST", "/hand-back", json={"note": "x"}
    ),
    ("GET", f"{TASKS}/{{id}}/comments"): lambda s, who: _each(s, who, "GET", "/comments"),
    ("POST", f"{TASKS}/{{id}}/comments"): lambda s, who: _each(
        s, who, "POST", "/comments", json={"body": "x"}
    ),
    ("DELETE", f"{TASKS}/{{id}}/comments/{{comment_id}}"): lambda s, who: _each(
        s, who, "DELETE", f"/comments/{uuid.uuid4()}"
    ),
    # -- every task tool ----------------------------------------------------
    ("tool", "task_add"): lambda s, who: [
        _tool(task_tools.task_add(Ctx(who), "x", parent_id=i)) for i in s.ids
    ],
    ("tool", "task_list"): lambda s, who: (
        [
            _tool(task_tools.task_list(Ctx(who), status=st))
            for st in (None, "open", "waiting", "done", "cancelled")
        ]
        + [_tool(task_tools.task_list(Ctx(who), tag="zqxpriv"))]
        + [
            _tool(task_tools.task_list(Ctx(who), assigned="by_me", status=st))
            for st in (None, "waiting")
        ]
    ),
    ("tool", "task_brief"): lambda s, who: [_tool(task_tools.task_brief(Ctx(who)))],
    ("tool", "task_update"): lambda s, who: [
        _tool(task_tools.task_update(Ctx(who), i, {"priority": "low"})) for i in s.ids
    ],
    ("tool", "task_complete"): lambda s, who: [
        _tool(task_tools.task_complete(Ctx(who), i)) for i in s.ids
    ],
    # -- existing routes and tools that read tasks ------------------------
    ("GET", "/api/v1/crm/tasks"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/tasks", params={**LABS, **p})
        for p in ({}, {"status": "open"}, {"status": "done"}, {"status": "cancelled"})
    ],
    ("GET", "/api/v1/crm/tasks/{record_id}"): lambda s, who: [
        _http(s, who, "GET", f"/api/v1/crm/tasks/{i}", params=LABS) for i in s.ids
    ],
    ("GET", "/api/v1/crm/tasks/{record_id}/timeline"): lambda s, who: [
        _http(s, who, "GET", f"/api/v1/crm/tasks/{i}/timeline", params=LABS) for i in s.ids
    ],
    ("GET", "/api/v1/crm/companies/{record_id}"): lambda s, who: [
        _http(s, who, "GET", f"/api/v1/crm/companies/{s.w.acme['id']}", params=LABS)
    ],
    ("GET", "/api/v1/crm/companies/{record_id}/timeline"): lambda s, who: [
        _http(s, who, "GET", f"/api/v1/crm/companies/{s.w.acme['id']}/timeline", params=LABS)
    ],
    ("GET", "/api/v1/crm/opportunities/{record_id}"): lambda s, who: [
        _http(s, who, "GET", f"/api/v1/crm/opportunities/{s.w.deal['id']}", params=LABS)
    ],
    ("GET", "/api/v1/crm/opportunities/{record_id}/timeline"): lambda s, who: [
        _http(s, who, "GET", f"/api/v1/crm/opportunities/{s.w.deal['id']}/timeline", params=LABS)
    ],
    ("GET", "/api/v1/crm/today"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/today", params=LABS)
    ],
    ("GET", "/api/v1/crm/search"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/search", params={**LABS, "q": q})
        for q in ("zqxpriv", "zqxshared", "dentist")
    ],
    ("POST", "/api/v1/crm/query"): lambda s, who: [
        _http(
            s, who, "POST", "/api/v1/crm/query", params=LABS, json={"object": "tasks", "spec": {}}
        )
    ],
    ("POST", "/api/v1/crm/export/view"): lambda s, who: [
        _http(s, who, "POST", "/api/v1/crm/export/view", params=LABS, json={"object": "tasks"})
    ],
    ("GET", "/api/v1/crm/export/workspace"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/export/workspace", params={**LABS, "object": o})
        for o in (None, "tasks")
    ],
    ("GET", "/api/v1/crm/dashboard"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/dashboard", params=LABS)
    ],
    ("GET", "/api/v1/crm/audit"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/audit", params=LABS)
    ],
    ("GET", "/api/v1/crm/tva-admin/audit"): lambda s, who: [
        _http(s, who, "GET", "/api/v1/crm/tva-admin/audit")
    ],
    ("GET", "/api/v1/admin/audit"): lambda s, who: [_http(s, who, "GET", "/api/v1/admin/audit")],
    ("tool", "crm_list"): lambda s, who: [
        _tool(crm_tools.crm_list(Ctx(who), "task", workspace="labs"))
    ],
    ("tool", "crm_get"): lambda s, who: [
        _tool(crm_tools.crm_get(Ctx(who), "task", i, workspace="labs")) for i in s.ids
    ],
    ("tool", "crm_today"): lambda s, who: [_tool(crm_tools.crm_today(Ctx(who), workspace="labs"))],
    ("tool", "crm_search"): lambda s, who: [
        _tool(crm_tools.crm_search(Ctx(who), "zqxpriv", workspace="labs"))
    ],
    ("tool", "audit_log"): lambda s, who: [_tool(server.audit_log(Ctx(who)))],
}


# ---------------------------------------------------------------------------
# The scope
# ---------------------------------------------------------------------------


class TestScope:
    def test_every_route_and_tool_in_scope_has_a_fixture(self):
        assert missing(*_registries()) == set()

    def test_every_fixture_names_a_real_route_or_tool(self):
        routes, tools = _registries()
        stale = [k for k in FIXTURES if k not in routes and k[1] not in tools]
        assert stale == []

    def test_the_check_fails_when_a_route_or_tool_is_added_without_a_fixture(self):
        """The committed self-test: a dummy `/api/v1/tasks` route and a dummy
        `task_` tool, registered on copies of the registries, are missing."""
        dummy = APIRouter()

        @dummy.get("/api/v1/tasks/dummy")
        def _dummy() -> dict:
            return {}

        routes = copy.copy(app.routes) + dummy.routes
        tools = [*asyncio.run(server.mcp.list_tools()), SimpleNamespace(name="task_dummy")]
        assert missing(_route_keys(routes), _tool_names(tools)) == {
            ("GET", "/api/v1/tasks/dummy"),
            ("tool", "task_dummy"),
        }
        assert ("GET", "/api/v1/tasks/dummy") not in _route_keys(app.routes)

    def test_the_rest_of_the_crm_is_left_to_the_crm_sweep(self, capsys):
        """Listed, not failed: the CRM isolation sweep covers them."""
        routes, tools = _registries()
        rest = sorted(
            [
                f"{m} {p}"
                for m, p in routes
                if p.startswith("/api/v1/crm") and (m, p) not in EXISTING
            ]
            + [f"tool {n}" for n in tools if n.startswith("crm_") and ("tool", n) not in EXISTING]
        )
        with capsys.disabled():
            print(f"\nLeft to the CRM isolation sweep ({len(rest)}):")
            for line in rest:
                print(f"  {line}")
        assert rest


# ---------------------------------------------------------------------------
# FR-TASK-VIS-06 — the ten accounts
# ---------------------------------------------------------------------------


@pytest.fixture
def sweep(monkeypatch) -> Sweep:
    return Sweep(monkeypatch)


class TestTheTenAccounts:
    @pytest.mark.parametrize("who", TEN)
    def test_nothing_of_a_private_or_shared_task_reaches_them(self, sweep, who):
        before = sweep.rows()
        leaks: list[str] = []
        for key, fixture in FIXTURES.items():
            for status, text, must_404 in fixture(sweep, who):
                lowered = text.lower()
                found = [m for m in MARKS if m in lowered] + [i for i in sweep.ids if i in text]
                if found:
                    leaks.append(f"{key}: {found}")
                if must_404 and status != 404:
                    leaks.append(f"{key}: {status}")
        assert leaks == []
        assert sweep.rows() == before

    def test_the_sweep_would_see_a_leak(self, sweep):
        """The creator's own answers carry the markers, so a leak would be
        found by the same check."""
        [(_, text, _)] = FIXTURES[("GET", f"{TASKS}/trash")](sweep, "creator")
        assert "zqxtrash" in text.lower()
        answers = FIXTURES[("GET", TASKS)](sweep, "creator")
        assert "zqxpriv" in answers[0][1].lower()
        assert "zqxshared" in FIXTURES[("GET", TASKS)](sweep, "assignee")[0][1].lower()
        [(_, brief, _)] = FIXTURES[("GET", f"{TASKS}/brief")](sweep, "creator")
        assert "zqxpriv" in brief.lower() and "zqxshared" in brief.lower()


#: FR-TASK-VIS-06's two tasks: the attribute of `Sweep` and its marker.
TARGETS = {"private": "zqxpriv", "shared": "zqxshared"}


class TestTheMatrix:
    @pytest.mark.parametrize("target", sorted(TARGETS))
    @pytest.mark.parametrize("who", TEN)
    def test_one_account_against_one_task(self, sweep, who, target):
        """One cell of the matrix: every fixture, with only this task's id,
        and only this task's marker looked for."""
        task, mark = getattr(sweep, target), TARGETS[target]
        sweep.ids = [task["id"]]
        before = dict(sweep.w.tasks[task["id"]])
        leaks: list[str] = []
        for key, fixture in FIXTURES.items():
            for status, text, must_404 in fixture(sweep, who):
                if mark in text.lower() or task["id"] in text:
                    leaks.append(f"{key}: found")
                if must_404 and status != 404:
                    leaks.append(f"{key}: {status}")
        assert leaks == []
        assert dict(sweep.w.tasks[task["id"]]) == before

    def test_the_cast_is_the_ten_kinds(self, sweep):
        """Each key of `TEN` is the account kind FR-TASK-VIS-06 names."""
        w, people = sweep.w, {k: TASK_PEOPLE[ID[k]] for k in ID}
        labs = {
            m["user_id"]: m["role"]
            for m in w.fake.rows["crm_memberships"].values()
            if m["workspace_id"] == w.labs["id"]
        }
        assert people["outsider"]["role"] == "admin" and ID["outsider"] not in labs
        assert people["manager"]["role"] == "manager"
        assert people["owner"]["is_owner"] is True
        assert people["creator"]["lead_id"] == ID["lead"]
        assert labs[ID["labsadmin"]] == "admin"
        assert labs[ID["viewer"]] == "viewer"
        assert people["seller"]["account_type"] == "crm_only"
        assert ID["removed"] not in labs and w.removed["user_id"] == ID["removed"]
        shared, handed = w.tasks[sweep.shared["id"]], w.tasks[sweep.handed["id"]]
        assert shared["waiting_on_id"] == ID["waiting"] and shared["status"] == "waiting"
        for task in (shared, handed):
            assert task["assignee_id"] != ID["former"]
            assert any(
                (r.get("before") or {}).get("assignee_id") == ID["former"]
                for r in w.fake.audit
                if r["target_table"] == "tasks" and r["target_id"] == task["id"]
            )
        assert handed["assignee_id"] == handed["created_by"] == ID["creator"]
        private = w.tasks[sweep.private["id"]]
        assert private["assignee_id"] == private["created_by"] and not private["workspace_id"]
        assert shared["assignee_id"] != shared["created_by"] and not shared["workspace_id"]


# ---------------------------------------------------------------------------
# FR-TASK-VIS-06's further proofs and FR-TASK-VIS-07
# ---------------------------------------------------------------------------


class TestCrmLinksWin:
    def test_a_removed_member_cannot_read_crm_tasks_they_created_or_were_given(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        made = w.add("removed", title="Theirs", links=[w.link(w.acme, "company")])
        given = w.add(
            "owner", title="Given", assignee_id="removed", links=[w.link(w.acme, "company")]
        )
        del w.fake.rows["crm_memberships"][w.removed["id"]]
        for task in (made, given):
            assert w.call("removed", "GET", f"/{task['id']}").status_code == 404
            r = w.http.get(
                f"/api/v1/crm/tasks/{task['id']}", params=LABS, headers=as_(ID["removed"])
            )
            assert r.status_code == 404
        listed = w.call("removed", "GET", "").json()
        assert all(not v for v in listed["sections"].values())
        answers = [
            w.http.get("/api/v1/crm/tasks", params=LABS, headers=as_(ID["removed"])).text,
            w.http.get(
                "/api/v1/crm/search", params={**LABS, "q": "Given"}, headers=as_(ID["removed"])
            ).text,
            _tool(task_tools.task_list(Ctx("removed")))[1],
            _tool(crm_tools.crm_list(Ctx("removed"), "task", workspace="labs"))[1],
            *[
                _tool(crm_tools.crm_get(Ctx("removed"), "task", t["id"], workspace="labs"))[1]
                for t in (made, given)
            ],
        ]
        for text in answers:
            assert made["id"] not in text and given["id"] not in text
        assert w.call("member", "GET", f"/{made['id']}").status_code == 200

    def test_no_one_outside_the_workspace_sees_a_crm_task_with_a_project(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        task = w.add(
            "member",
            links=[w.link(w.deal, "opportunity"), w.link(w.lumen, "tva_project")],
        )
        for who in ("outsider", "manager", "lead", "user"):
            assert w.call(who, "GET", f"/{task['id']}").status_code == 404, who
        for who in ("owner", "labsadmin", "member", "creator", "viewer", "seller"):
            assert w.call(who, "GET", f"/{task['id']}").status_code == 200, who

    def test_a_crm_only_member_gets_no_project_field(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        task = w.add(
            "member",
            links=[w.link(w.deal, "opportunity"), w.link(w.lumen, "tva_project")],
        )
        out = w.call("seller", "GET", f"/{task['id']}").json()
        assert out["project"] is None
        assert "Lumen" not in json.dumps(out) and w.lumen["id"] not in json.dumps(out)
        crm = w.http.get(f"/api/v1/crm/tasks/{task['id']}", params=LABS, headers=as_(ID["seller"]))
        assert "Lumen" not in crm.text and w.lumen["id"] not in crm.text


class TestProjectTasks:
    def test_who_sees_a_project_task(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        w.fake.add_allocation(w.lumen, ID["user"], released_at="2026-09-01T00:00:00+00:00")
        task = w.add("member", assignee_id="assignee", links=[w.link(w.lumen, "tva_project")])
        for who in ("outsider", "owner", "manager", "lead", "viewer", "member", "assignee", "user"):
            assert w.call(who, "GET", f"/{task['id']}").status_code == 200, who
        for who in ("seller", "creator", "former", "waiting", "labsadmin"):
            assert w.call(who, "GET", f"/{task['id']}").status_code == 404, who

    def test_a_creator_who_cannot_see_the_project_sees_its_name_only(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        task = w.add("member", assignee_id="creator", links=[w.link(w.lumen, "tva_project")])
        out = w.call("creator", "GET", f"/{task['id']}").json()
        assert out["project"] == {"name": "Lumen"}
        assert w.lumen["id"] not in json.dumps(out)

    def test_a_crm_only_assignee_sees_a_project_task_only_as_creator_or_assignee(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        mine = w.add("seller", title="Mine")
        assert w.call("seller", "GET", f"/{mine['id']}").json()["project"] is None
        other = w.add("owner", links=[w.link(w.lumen, "tva_project")])
        assert w.call("seller", "GET", f"/{other['id']}").status_code == 404


class TestParentAndHistory:
    def test_a_private_parent_shows_no_id_or_title(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        parent = w.add("creator", title="Zqxpriv parent", assignee_id="assignee")
        child = w.add("assignee", title="Step", parent_id=parent["id"])
        w.call("creator", "PATCH", f"/{parent['id']}", json={"assignee_id": ID["creator"]})
        out = w.call("assignee", "GET", f"/{child['id']}").json()
        assert out["parent"] == {"private": True}
        assert "zqxpriv" not in json.dumps(out).lower() and parent["id"] not in json.dumps(out)

    def test_a_new_assignee_gets_no_earlier_values_of_the_row_that_assigned_them(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        task = w.add("creator", title="Plan", notes="Zqxold private notes")
        r = w.call(
            "creator",
            "PATCH",
            f"/{task['id']}",
            json={"notes": "Cleaned up", "assignee_id": ID["assignee"]},
        )
        assert r.status_code == 200, r.text
        theirs = w.call("assignee", "GET", f"/{task['id']}").json()["history"]
        assert "zqxold" not in json.dumps(theirs).lower()
        [row] = [h for h in theirs if h["action"] == "task.updated"]
        assert {c["field"]: c["before"] for c in row["changes"]}["assignee_id"] == ID["creator"]
        mine = w.call("creator", "GET", f"/{task['id']}").json()["history"]
        assert "zqxold" in json.dumps(mine).lower()

    def test_private_era_history_stays_with_the_creator_and_assignee(self, monkeypatch):
        w = TaskWorld(monkeypatch)
        task = w.add("creator", title="Zqxera old")
        w.call("creator", "PATCH", f"/{task['id']}", json={"title": "Visible now"})
        r = w.call("creator", "POST", f"/{task['id']}/links", json=w.link(w.acme, "company"))
        assert r.status_code == 200, r.text
        mine = w.call("creator", "GET", f"/{task['id']}").json()["history"]
        assert "zqxera" in json.dumps(mine).lower()
        assert [h["action"] for h in mine][:2] == ["task.created", "task.updated"]
        for who in ("labsadmin", "member", "viewer", "owner"):
            seen = w.call(who, "GET", f"/{task['id']}").json()
            assert "zqxera" not in json.dumps(seen).lower(), who
            assert "task.created" not in [h["action"] for h in seen["history"]], who
            timeline = w.http.get(
                f"/api/v1/crm/companies/{w.acme['id']}/timeline", params=LABS, headers=as_(ID[who])
            )
            assert "zqxera" not in timeline.text.lower(), who
            own = w.http.get(
                f"/api/v1/crm/tasks/{task['id']}/timeline", params=LABS, headers=as_(ID[who])
            )
            assert own.status_code == 200 and "zqxera" not in own.text.lower(), who
            got = _tool(crm_tools.crm_get(Ctx(who), "task", task["id"], workspace="labs"))[1]
            assert "zqxera" not in got.lower(), who
        audit = w.http.get("/api/v1/crm/audit", params=LABS, headers=as_(ID["labsadmin"]))
        assert audit.status_code == 200 and "zqxera" not in audit.text.lower()
