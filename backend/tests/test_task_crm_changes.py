"""What 008 changes in the CRM's task behaviour — spec 008 §8, FR-TASK-CHG-01,
-04, -05, -09, -10, FR-TASK-DATA-04, -05, -08, FR-TASK-VIS-04, FR-TASK-MCP-04;
TP-05, TP-07.

Every CRM task behaviour of `007` keeps working on the shared table, changed
only as spec §8 says. Routes and tools run against `tests/crm_fake.py`.

Acceptance criteria (spec §12, phase 1 — migration and CRM):
- "A cancelled task counts as closed everywhere open is tested (Today,
  flags, reminders, views)" — TestCancelledIsClosed, and the real store's
  Today and reminder queries in tests/db/test_crm_flags_db.py
  TestTaskQueries (the fake here re-implements them).
- "Closing a deal Lost sets its open and waiting tasks to Cancelled
  (FR-TASK-CHG-04); they do not count in Dashboard Activity; tasks closed
  this way before the migration stay Done" — TestClosedLost (the Activity
  query itself: tests/db/test_crm_flags_db.py TestTaskQueries; the
  migration half: tests/db/test_task_migration_db.py TestMapping).
- "crm_add_task, crm_complete_task, crm_get, crm_list, crm_create,
  crm_update and crm_today return the same tasks before and after;
  GET /crm/tasks?status=open returns Open and Waiting tasks; todo and
  in_progress given as a status input to crm_create, crm_update, crm_list or
  GET /crm/tasks are read as Open" — TestStatusInputs, TestTheTools.
- "The CRM writer refuses a task created through the CRM with no link;
  removing a task's last CRM link is refused through CRM routes, task routes
  and task_update" — TestLinks (CRM routes; task routes and task_update
  arrive in S1b).
- "Purging a record purges tasks left with no CRM link; none becomes
  personal" — TestLinks (and tests/db/test_crm_records_db.py
  TestPurgeLeavesNoLinklessTask).
- "Every CRM task test passes, updated only where §8 changes behaviour" —
  the existing tests/test_crm_*.py and tests/db/test_crm_*_db.py suites,
  run as they stand after S1a.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from crm_fake import DEFAULTS, MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import flags as flag_rules
from app.domain.crm import settings as setting_rules
from app.mcp import crm as tools
from app.mcp import server
from app.services.crm import deals
from app.services.crm import today as today_service

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    monkeypatch.setattr(today_service, "_now", lambda: NOW)
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    company = http.post(f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=me).json()
    return http, fake, labs, company


def _links(*records):
    return [{"record_type": r["record_type"], "record_id": r["id"]} for r in records]


def _task(http, company, who=OWNER["id"], query=LABS, **fields):
    r = http.post(
        f"/api/v1/crm/tasks{query}",
        json={"title": "Send the deck", "links": _links(company), **fields},
        headers=as_(who),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _patch(http, task, body, who=OWNER["id"]):
    return http.patch(f"/api/v1/crm/tasks/{task['id']}{LABS}", json=body, headers=as_(who))


class TestStatusInputs:
    """FR-TASK-CHG-10, TP-05 — new keys out; old keys accepted as Open."""

    def test_a_new_task_is_open_with_the_new_labels(self, crm):
        http, _, _, company = crm
        task = _task(http, company)
        assert (task["status"], task["status_label"]) == ("open", "Open")
        assert (task["priority"], task["tags"], task["waiting_on"]) == ("normal", [], None)

    @pytest.mark.parametrize("old", ["todo", "in_progress"])
    def test_old_keys_are_read_as_open_on_create_and_update(self, crm, old):
        http, fake, _, company = crm
        task = _task(http, company, status=old)
        assert task["status"] == "open"
        _patch(http, task, {"status": "done"})
        r = _patch(http, task, {"status": old})
        assert r.status_code == 200, r.text
        assert (r.json()["status"], r.json()["completed_at"]) == ("open", None)
        assert fake.rows["tasks"][task["id"]]["status"] == "open"

    def test_waiting_needs_waiting_on(self, crm):
        http, _, _, company = crm
        task = _task(http, company)
        r = _patch(http, task, {"status": "waiting"})
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Waiting needs who or what you are waiting on.",
        )
        r = _patch(http, task, {"status": "waiting", "waiting_on_id": MEMBER["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["waiting_on"] == {"id": MEMBER["id"], "label": "Deepika"}

    def test_status_open_means_open_or_waiting(self, crm):
        http, _, _, company = crm
        opened = _task(http, company, title="Open one")
        waiting = _task(http, company, title="Waiting one")
        _patch(http, waiting, {"status": "waiting", "waiting_on_text": "Client"})
        done = _task(http, company, title="Done one")
        _patch(http, done, {"status": "done"})
        cancelled = _task(http, company, title="Cancelled one")
        _patch(http, cancelled, {"status": "cancelled"})

        def titles(status):
            r = http.get(f"/api/v1/crm/tasks{LABS}&status={status}", headers=as_(OWNER["id"]))
            assert r.status_code == 200, r.text
            return sorted(t["title"] for t in r.json()["rows"])

        assert titles("open") == sorted([opened["title"], waiting["title"]])
        assert titles("todo") == titles("open") == titles("in_progress")
        assert titles("waiting") == ["Waiting one"]
        assert titles("cancelled") == ["Cancelled one"]
        assert titles("done") == ["Done one"]

    def test_a_cancelled_task_has_no_completed_at(self, crm):
        http, _, _, company = crm
        task = _task(http, company)
        r = _patch(http, task, {"status": "cancelled"})
        assert (r.json()["status"], r.json()["completed_at"]) == ("cancelled", None)


class TestTheTools:
    """FR-TASK-MCP-04 — the `crm_*` tools keep working on the same rows; the
    old keys reach them as Open (TP-05); 008 adds and renames none."""

    def test_crm_create_crm_update_and_crm_list_accept_the_old_keys(self, crm):
        http, fake, _, company = crm
        made = asyncio.run(
            tools.crm_create(
                Ctx(OWNER["id"]),
                "task",
                {"title": "Old prompt", "status": "todo", "links": _links(company)},
                workspace="labs",
            )
        )
        assert made["status"] == "open"
        asyncio.run(
            tools.crm_update(
                Ctx(OWNER["id"]), "task", made["id"], {"status": "done"}, workspace="labs"
            )
        )
        again = asyncio.run(
            tools.crm_update(
                Ctx(OWNER["id"]), "task", made["id"], {"status": "in_progress"}, workspace="labs"
            )
        )
        assert again["status"] == "open"
        listed = asyncio.run(
            tools.crm_list(
                Ctx(OWNER["id"]),
                "task",
                filters=[{"field": "status", "operator": "is", "value": "todo"}],
                workspace="labs",
            )
        )
        assert [r["title"] for r in listed["rows"]] == ["Old prompt"]

    def test_crm_add_task_and_crm_complete_task(self, crm):
        http, fake, _, company = crm
        made = asyncio.run(
            tools.crm_add_task(Ctx(OWNER["id"]), "Call", _links(company), workspace="labs")
        )
        assert (made["status"], made["bucket"]) == ("open", "later")
        done = asyncio.run(tools.crm_complete_task(Ctx(OWNER["id"]), made["id"], workspace="labs"))
        assert done["status"] == "done" and done["completed_at"]

    def test_no_crm_tool_is_added_or_renamed(self):
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert {
            "crm_add_task",
            "crm_complete_task",
            "crm_get",
            "crm_list",
            "crm_create",
            "crm_update",
            "crm_today",
        } <= names
        assert not {n for n in names if n.startswith("crm_") and "task" in n} - {
            "crm_add_task",
            "crm_complete_task",
        }


class TestBuckets:
    """FR-TASK-DATA-08 — an open or waiting CRM task always has a bucket."""

    def test_created_with_no_bucket_gets_the_default(self, crm):
        http, _, _, company = crm
        assert _task(http, company)["bucket"] == "later"
        assert _task(http, company, due_on="2026-10-14")["bucket"] == "today"
        assert _task(http, company, due_on="2026-10-01")["bucket"] == "today"
        assert _task(http, company, bucket="this_week")["bucket"] == "this_week"

    def test_a_reopened_task_with_no_bucket_gets_the_default(self, crm):
        http, fake, _, company = crm
        task = _task(http, company)
        _patch(http, task, {"status": "done"})
        # A Done task may have lost its bucket (a Twenty import, or a Done
        # row the migration left alone).
        fake.rows["tasks"][task["id"]]["bucket"] = None
        r = _patch(http, task, {"status": "open"})
        assert (r.json()["status"], r.json()["bucket"]) == ("open", "later")

    def test_clearing_the_bucket_of_an_open_task_refills_it(self, crm):
        http, _, _, company = crm
        task = _task(http, company, bucket="this_week")
        r = _patch(http, task, {"bucket": None})
        assert r.json()["bucket"] == "later"


class TestLinks:
    """FR-TASK-DATA-05 — no task loses its last CRM link; FR-TASK-CHG-01."""

    def test_removing_the_last_link_is_refused(self, crm):
        http, _, _, company = crm
        task = _task(http, company)
        r = _patch(http, task, {"links": []})
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "A CRM task needs at least one linked record; delete it instead.",
        )

    def test_a_task_created_through_the_crm_needs_a_link(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/tasks{LABS}", json={"title": "Orphan"}, headers=as_(OWNER["id"])
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "A task must be linked to at least one record.",
        )

    def test_replacing_links_keeps_a_project_link(self, crm):
        http, fake, labs, company = crm
        other = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Orbit"}, headers=as_(OWNER["id"])
        ).json()
        task = _task(http, company)
        project = fake.add_project("Lumen", status="confirmed")
        fake.tasks.add_link(fake.rows["tasks"][task["id"]], "tva_project", project["id"])
        r = _patch(http, task, {"links": _links(other)})
        assert r.status_code == 200, r.text
        kinds = sorted(
            lk["record_type"]
            for lk in fake.rows["task_links"].values()
            if lk["task_id"] == task["id"]
        )
        assert kinds == ["company", "tva_project"]

    def test_restoring_a_task_with_no_crm_link_left_is_refused(self, crm):
        http, fake, labs, company = crm
        task = _task(http, company)
        http.delete(f"/api/v1/crm/tasks/{task['id']}{LABS}", headers=as_(OWNER["id"]))
        for lk in list(fake.rows["task_links"].values()):
            if lk["task_id"] == task["id"]:
                del fake.rows["task_links"][lk["id"]]
        r = http.post(f"/api/v1/crm/tasks/{task['id']}/restore{LABS}", headers=as_(OWNER["id"]))
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "This task has no linked record left, so it cannot be restored.",
        )

    def test_purging_a_record_purges_the_tasks_it_leaves_unlinked(self, crm):
        """FR-TASK-DATA-14."""
        http, fake, labs, company = crm
        other = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Orbit"}, headers=as_(OWNER["id"])
        ).json()
        alone = _task(http, company, title="Alone")
        shared = _task(http, company, title="Shared")
        _patch(http, shared, {"links": _links(company, other)})
        http.delete(f"/api/v1/crm/companies/{company['id']}{LABS}", headers=as_(OWNER["id"]))
        r = http.delete(
            f"/api/v1/crm/companies/{company['id']}/permanent{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert alone["id"] not in fake.rows["tasks"]
        assert shared["id"] in fake.rows["tasks"]


class TestClosedLost:
    """FR-TASK-CHG-04 (TP-07) — Cancelled, not Done; not Activity."""

    def _lost(self, http, fake, academy):
        company = http.post(
            f"/api/v1/crm/companies{ACADEMY}", json={"name": "College"}, headers=as_(OWNER["id"])
        ).json()
        http.patch(
            f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        deal = http.post(
            f"/api/v1/crm/opportunities{ACADEMY}",
            json={
                "name": "College - AI",
                "company_id": company["id"],
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
            headers=as_(OWNER["id"]),
        ).json()
        tasks = {}
        for title in ("Open", "Waiting", "Done"):
            tasks[title] = _task(http, deal | {"record_type": "opportunity"}, query=ACADEMY)
        http.patch(
            f"/api/v1/crm/tasks/{tasks['Waiting']['id']}{ACADEMY}",
            json={"status": "waiting", "waiting_on_text": "Dean"},
            headers=as_(OWNER["id"]),
        )
        http.post(
            f"/api/v1/crm/tasks/{tasks['Done']['id']}/complete{ACADEMY}",
            headers=as_(OWNER["id"]),
        )
        reason = next(
            o["id"]
            for o in fake.rows["crm_select_options"].values()
            if o["workspace_id"] == academy["id"] and o.get("system_key") == "price"
        )
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/stage{ACADEMY}",
            json={"stage": "closed_lost", "changes": {"lost_reason_id": reason}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        return r.json(), tasks

    def test_open_and_waiting_tasks_are_cancelled(self, crm):
        http, fake, *_ = crm
        academy = next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == "academy")
        answer, tasks = self._lost(http, fake, academy)
        assert answer["tasks_closed"] == 2
        rows = {k: fake.rows["tasks"][t["id"]] for k, t in tasks.items()}
        assert (rows["Open"]["status"], rows["Open"]["completed_at"]) == ("cancelled", None)
        assert (rows["Waiting"]["status"], rows["Waiting"]["completed_at"]) == ("cancelled", None)
        assert rows["Done"]["status"] == "done" and rows["Done"]["completed_at"]

    def test_they_do_not_count_in_dashboard_activity(self, crm):
        http, fake, *_ = crm
        academy = next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == "academy")
        _, tasks = self._lost(http, fake, academy)
        counted = fake.tasks_completed_between(academy["id"], "2000-01-01", "2100-01-01")
        assert [t["id"] for t in counted] == [tasks["Done"]["id"]]


class TestCancelledIsClosed:
    """FR-TASK-DATA-04 — open means Open or Waiting everywhere."""

    def test_today_section_three(self, crm):
        http, fake, labs, company = crm
        mine = {"assignee_id": SELLER["id"]}
        for title, status, extra in (
            ("Open", "open", {}),
            ("Waiting", "waiting", {"waiting_on_text": "Client"}),
            ("Cancelled", "cancelled", {}),
            ("Done", "done", {"completed_at": "2026-10-13T00:00:00+00:00"}),
        ):
            fake.add(
                "tasks",
                **{
                    **DEFAULTS["tasks"],
                    "workspace_id": labs["id"],
                    "title": title,
                    "status": status,
                    "bucket": "today",
                    "created_at": "2026-10-01T00:00:00+00:00",
                    **mine,
                    **extra,
                },
            )
        r = http.get(f"/api/v1/crm/today{LABS}", headers=as_(SELLER["id"]))
        [section] = [s for s in r.json()["sections"] if s["key"] == "tasks"]
        assert sorted(i["title"] for i in section["items"]) == ["Open", "Waiting"]

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    def test_flags(self, status):
        assert (
            flag_rules.task_overdue({"status": status, "due_on": "2026-10-01"}, NOW.date()) is None
        )

    def test_waiting_is_flagged(self):
        flag = flag_rules.task_overdue({"status": "waiting", "due_on": "2026-10-01"}, NOW.date())
        assert flag["level"] == "red"

    def test_reminders(self, crm):
        http, fake, labs, company = crm
        for status in ("open", "waiting", "cancelled"):
            fake.add(
                "tasks",
                **{
                    **DEFAULTS["tasks"],
                    "workspace_id": labs["id"],
                    "title": status,
                    "status": status,
                    "bucket": "today",
                    "due_on": "2026-10-13",
                    "assignee_id": OWNER["id"],
                },
            )
        due = fake.tasks_due_by(labs["id"], "2026-10-14")
        assert sorted(t["title"] for t in due) == ["open", "waiting"]

    def test_the_seeded_open_tasks_view(self, crm):
        http, fake, labs, company = crm
        for title, status in (("Open", "open"), ("Cancelled", "cancelled"), ("Done", "done")):
            task = _task(http, company, title=title)
            if status != "open":
                _patch(http, task, {"status": status})
        r = http.post(
            f"/api/v1/crm/query{LABS}",
            json={"object": "tasks", "view": "open_tasks_by_owner", "spec": {}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert [row["title"] for row in r.json()["rows"]] == ["Open"]


class TestOnlyTheWorkspacesTasks:
    """FR-TASK-VIS-04, FR-TASK-CHG-05 — a task with no workspace never reaches
    CRM Today, the daily digest (built from Today) or the reminders."""

    def test_a_private_task_due_today_is_absent(self, crm):
        http, fake, labs, company = crm
        fake.tasks.add_task(
            title="Book dentist",
            created_by=SELLER["id"],
            assignee_id=SELLER["id"],
            due_on="2026-10-14",
            bucket="today",
        )
        built = today_service.build(labs["id"], SELLER["id"], {"timezone": "Asia/Kolkata"}, NOW)
        [section] = [s for s in built["sections"] if s["key"] == "tasks"]
        assert section["items"] == []
        assert fake.tasks_due_by(labs["id"], "2026-10-14") == []
        assert fake.tasks_for_today(labs["id"], SELLER["id"], "2026-10-14") == []


class TestTheBell:
    """FR-TASK-DEL-01 — a CRM task assigned to someone else keeps the CRM's
    in-app notice."""

    def test_assigned_to_someone_else(self, crm):
        http, fake, _, company = crm
        task = _task(http, company, assignee_id=MEMBER["id"])
        notices = [n for n in fake.rows["crm_notifications"].values() if n["kind"] == "assigned"]
        assert [(n["user_id"], n["record_id"]) for n in notices] == [(MEMBER["id"], task["id"])]

    def test_a_viewer_assignee_still_changes_only_the_status(self, crm):
        http, fake, _, company = crm
        task = _task(http, company, assignee_id=VIEWER["id"])
        r = _patch(http, task, {"status": "done"}, who=VIEWER["id"])
        assert r.status_code == 200, r.text
        r = _patch(http, task, {"priority": "high"}, who=VIEWER["id"])
        assert r.status_code == 403


class TestNewFields:
    """FR-TASK-CHG-01 — the new task fields as the CRM presents and clears
    them."""

    def test_source_is_presented_with_its_label(self, crm):
        http, fake, _, company = crm
        task = _task(http, company)
        assert (task["source"], task["source_label"]) == ("manual", "Manual")
        fake.rows["tasks"][task["id"]]["source"] = "import"
        r = http.post(
            f"/api/v1/crm/query{LABS}",
            json={"object": "tasks", "spec": {"columns": ["title", "source"]}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        [row] = r.json()["rows"]
        assert (row["source"], row["source_label"]) == ("import", "Import")

    def test_clearing_tags_and_priority_empties_them(self, crm):
        http, fake, _, company = crm
        task = _task(http, company, tags=["sales"], priority="high")
        r = _patch(http, task, {"tags": None, "priority": None})
        assert r.status_code == 200, r.text
        row = fake.rows["tasks"][task["id"]]
        assert (row["tags"], row["priority"]) == ([], "normal")
        again = asyncio.run(
            tools.crm_update(
                Ctx(OWNER["id"]), "task", task["id"], {"priority": None}, workspace="labs"
            )
        )
        assert again["priority"] == "normal"
        assert fake.rows["tasks"][task["id"]]["priority"] == "normal"

    def test_the_bucket_list_is_named_bucket(self):
        assert setting_rules.LISTS["task.bucket"].label == "Bucket"


class TestSavedViewStatus:
    """FR-TASK-CHG-10, TP-05 — a saved view's status condition reads as the
    same condition sent now: `open` is Open or Waiting, old keys are Open,
    and "not Done" leaves Cancelled out (as 047 rewrote saved views)."""

    def _four(self, http, company):
        for title, body in (
            ("Open one", {}),
            ("Waiting one", {"status": "waiting", "waiting_on_text": "Client"}),
            ("Done one", {"status": "done"}),
            ("Cancelled one", {"status": "cancelled"}),
        ):
            task = _task(http, company, title=title)
            if body:
                assert _patch(http, task, body).status_code == 200

    def _view(self, http, fake, condition):
        made = http.post(
            f"/api/v1/crm/views{LABS}",
            json={"object": "tasks", "name": "Mine", "config": {}},
            headers=as_(OWNER["id"]),
        ).json()
        # As 047 left it, or as an older client saved it.
        fake.rows["crm_saved_views"][made["id"]]["config"] = {
            "filters": {"op": "and", "items": [condition]}
        }
        return made

    def _titles(self, http, view_id=None, spec=None):
        body = {"object": "tasks", "spec": spec or {}}
        if view_id:
            body["view_id"] = view_id
        r = http.post(f"/api/v1/crm/query{LABS}", json=body, headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        return sorted(row["title"] for row in r.json()["rows"])

    @pytest.mark.parametrize("value", ["open", "todo"])
    def test_a_saved_open_view_matches_open_and_waiting(self, crm, value):
        http, fake, _, company = crm
        self._four(http, company)
        view = self._view(http, fake, {"field": "status", "operator": "is", "value": value})
        assert self._titles(http, view["id"]) == ["Open one", "Waiting one"]

    def test_a_view_saved_with_an_old_key_is_stored_as_open(self, crm):
        http, fake, _, company = crm
        self._four(http, company)
        config = {
            "filters": {
                "op": "and",
                "items": [{"field": "status", "operator": "any_of", "value": ["todo"]}],
            }
        }
        r = http.post(
            f"/api/v1/crm/views{LABS}",
            json={"object": "tasks", "name": "To do", "config": config},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201, r.text
        assert r.json()["config"]["filters"]["items"] == [
            {"field": "status", "operator": "any_of", "value": ["open", "waiting"]}
        ]
        assert self._titles(http, r.json()["id"]) == ["Open one", "Waiting one"]
        r = http.patch(
            f"/api/v1/crm/views/{r.json()['id']}{LABS}",
            json={"config": config},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["config"]["filters"]["items"][0]["value"] == ["open", "waiting"]

    @pytest.mark.parametrize(
        "condition",
        [
            {"field": "status", "operator": "is_not", "value": "done"},
            {"field": "status", "operator": "none_of", "value": ["done"]},
        ],
    )
    def test_not_done_leaves_cancelled_out_saved_or_sent(self, crm, condition):
        http, fake, _, company = crm
        self._four(http, company)
        view = self._view(http, fake, condition)
        assert self._titles(http, view["id"]) == ["Open one", "Waiting one"]
        sent = {"filters": {"op": "and", "items": [condition]}}
        assert self._titles(http, spec=sent) == ["Open one", "Waiting one"]


class TestTheProjectField:
    """FR-TASK-VIS-07 — a Labs deal task that also links a TVA project: a
    member who can see the project gets it, a `crm_only` member gets no
    project field at all, through the CRM routes and tools."""

    @pytest.fixture
    def linked(self, crm):
        http, fake, labs, company = crm
        http.patch(
            f"/api/v1/crm/companies/{company['id']}{LABS}",
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        deal = http.post(
            f"/api/v1/crm/opportunities{LABS}",
            json={
                "name": "Acme - Pilot",
                "company_id": company["id"],
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
            headers=as_(OWNER["id"]),
        )
        assert deal.status_code == 201, deal.text
        deal = deal.json()
        task = _task(http, deal | {"record_type": "opportunity"})
        project = fake.add_project("Lumen", status="confirmed")
        fake.tasks.add_link(fake.rows["tasks"][task["id"]], "tva_project", project["id"])
        return http, deal, task, project

    def _reads(self, http, deal, task, who):
        one = http.get(f"/api/v1/crm/tasks/{task['id']}{LABS}", headers=as_(who))
        assert one.status_code == 200, one.text
        listed = http.get(f"/api/v1/crm/tasks{LABS}", headers=as_(who))
        tab = http.get(f"/api/v1/crm/opportunities/{deal['id']}{LABS}", headers=as_(who))
        got = asyncio.run(tools.crm_get(Ctx(who), "opportunity", deal["id"], workspace="labs"))
        found = asyncio.run(tools.crm_list(Ctx(who), "task", workspace="labs"))
        return [
            one.json()["record"],
            next(t for t in listed.json()["rows"] if t["id"] == task["id"]),
            next(t for t in tab.json()["tasks"] if t["id"] == task["id"]),
            next(t for t in got["tasks"] if t["id"] == task["id"]),
            next(t for t in found["rows"] if t["id"] == task["id"]),
        ]

    def test_a_lead_who_is_a_member_sees_the_project(self, linked):
        http, deal, task, project = linked
        for shown in self._reads(http, deal, task, VIEWER["id"]):
            assert shown["project"] == {"id": project["id"], "name": "Lumen"}

    def test_a_crm_only_member_gets_no_project(self, linked):
        http, deal, task, project = linked
        for shown in self._reads(http, deal, task, SELLER["id"]):
            assert shown["project"] is None
            assert project["id"] not in str(shown)


class TestPrivateParent:
    """FR-TASK-DATA-09, FR-TASK-VIS-06 — a CRM task whose parent another
    member keeps private shows "a private task": no id, no title, in its
    record, lists, timeline and the workspace audit."""

    @pytest.fixture
    def child(self, crm):
        http, fake, labs, company = crm
        secret = fake.tasks.add_task(
            title="Private plan", created_by=MEMBER["id"], assignee_id=MEMBER["id"]
        )
        task = _task(http, company, who=MEMBER["id"])
        row = fake.rows["tasks"][task["id"]]
        before = dict(row)
        row["parent_id"] = secret["id"]
        fake.audit.append(
            {
                "id": len(fake.audit) + 1,
                "at": "2026-10-14T06:30:00+00:00",
                "actor_id": MEMBER["id"],
                "actor_label": "user",
                "channel": "web",
                "workspace_id": labs["id"],
                "action": "task.updated",
                "target_table": "tasks",
                "target_id": task["id"],
                "before": {"parent_id": before["parent_id"]},
                "after": {"parent_id": secret["id"]},
            }
        )
        return http, task, secret

    def test_its_creator_sees_the_parent(self, child):
        http, task, secret = child
        r = http.get(f"/api/v1/crm/tasks/{task['id']}{LABS}", headers=as_(MEMBER["id"]))
        assert r.json()["record"]["parent"] == {"id": secret["id"], "title": "Private plan"}

    @pytest.mark.parametrize("who", [SELLER["id"], VIEWER["id"], OWNER["id"]])
    def test_another_member_sees_a_private_task(self, child, who):
        http, task, secret = child
        one = http.get(f"/api/v1/crm/tasks/{task['id']}{LABS}", headers=as_(who))
        assert one.status_code == 200, one.text
        assert one.json()["record"]["parent"] == {"private": True}
        listed = http.get(f"/api/v1/crm/tasks{LABS}", headers=as_(who))
        timeline = http.get(f"/api/v1/crm/tasks/{task['id']}/timeline{LABS}", headers=as_(who))
        [change] = [c for e in timeline.json() for c in e["changes"] if c["field"] == "parent_id"]
        assert change["after"] == "a private task"
        for r in (one, listed, timeline):
            assert secret["id"] not in r.text and "Private plan" not in r.text

    def test_the_workspace_audit_leaves_the_parent_out(self, child):
        http, task, secret = child
        r = http.get(f"/api/v1/crm/audit{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        assert secret["id"] not in r.text


class TestAuditProjectLink:
    """FR-TASK-VIS-07 — a workspace Admin who cannot see a task's TVA project
    reads no row of that project link in the workspace audit."""

    def _audited_link(self, http, fake, labs, company):
        task = _task(http, company)
        project = fake.add_project("Lumen", status="confirmed")
        link = fake.tasks.add_link(fake.rows["tasks"][task["id"]], "tva_project", project["id"])
        fake.audit.append(
            {
                "id": len(fake.audit) + 1,
                "at": "2026-10-14T06:30:00+00:00",
                "actor_id": OWNER["id"],
                "actor_label": "user",
                "channel": "web",
                "workspace_id": labs["id"],
                "action": "task_link.created",
                "target_table": "task_links",
                "target_id": link["id"],
                "before": None,
                "after": {k: link[k] for k in ("task_id", "record_type", "record_id")},
            }
        )
        return project

    def test_a_crm_only_admin_gets_no_project_link(self, crm):
        http, fake, labs, company = crm
        project = self._audited_link(http, fake, labs, company)
        membership = next(
            m
            for m in fake.rows["crm_memberships"].values()
            if m["workspace_id"] == labs["id"] and m["user_id"] == SELLER["id"]
        )
        membership["role"] = "admin"
        r = http.get(f"/api/v1/crm/audit{LABS}", headers=as_(SELLER["id"]))
        assert r.status_code == 200, r.text
        assert project["id"] not in r.text

    def test_an_admin_who_sees_the_project_gets_it(self, crm):
        http, fake, labs, company = crm
        project = self._audited_link(http, fake, labs, company)
        r = http.get(f"/api/v1/crm/audit{LABS}", headers=as_(OWNER["id"]))
        assert project["id"] in r.text
