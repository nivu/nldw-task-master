"""Phases 1 to 4's acceptance criteria, one test each — spec 008 §12
(phases 1 to 4); NFR-TASK-08; tasks T073, T098, T104, T111, T126.

The lines are read from `specs/008-tasks/spec.md` §12 itself, from "Phase 1 —
migration and CRM" to "Phase 2", from "Phase 2" to "Phase 3", from "Phase 3"
to "Phase 4" and from "Phase 4" to the end of §12, so each one is its own
test here and a line added to
or reworded in the spec fails `test_every_line_has_exactly_one_entry` until
this file says how it is proved. Phase 2's lines are proved by named
covering tests only. Phase 3's lines (S3a) are proved by named covering
tests and, for what is screen state only (Accept, the merge, the warning;
plan §5.9), by named tests of the local Playwright spec
`frontend/e2e/day-log.spec.ts`. No gate runs that spec, so those are
recorded proofs: each is asserted to exist, the spec to skip nothing, and
the test to be among those a recorded run saw pass (`E2E_RECORDED`,
`E2E_PASSED`); the recurrence line (S3b) by named covering tests. Phase 4's lines
(S4a, S4b) are proved by named covering tests only.

Each line is proved one of two ways:

- **A scenario**, run here through the routes and tools on the shared task
  world (`tests/tasks_fake.py`): the lines no single covering test proves
  end to end.
- **Named covering tests**, each asserted to exist (a module, a class or a
  test function, found by parsing the file) and not to be skipped: no
  `skip`, `skipif` or `xfail` mark on it, its class or its module, and no
  `pytest.skip(` call in its source. A test marked `db`
  is not skipped in that sense: it runs, unskipped, at every slice's
  database gate (`CRM_DB_TESTS=1`, plan §8.1), and only there.

**Two stated exceptions** are proved by a recorded opt-in run instead of an
unskipped test (T073):

- the migration mapping (ids, links and history kept; the "in progress"
  tag; linkless tasks to Trash; and two clauses of other lines: a renamed
  Done label survives, and tasks a Lost move closed before the migration
  stay Done) by `tests/db/test_task_migration_db.py::TestMapping`, run with
  `TASKS_MIGRATION_CHECK=1` (S1a); its output is in the S1a commit message;
- the 5,000-task speed line (NFR-TASK-01) by
  `scripts/simulations/tasks_load_5k.py`, run under the database lock with
  the API up (S1e); its output is in the S1e commit message.

For those two this asserts the covering class or script exists and names
where its recorded output is (`RECORDED`). A line that cites a recorded run
beside unskipped covers names, in `RECORDED_CLAUSES`, the clauses the run
alone proves, so no clause rests on it unstated.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from crm_fake import as_
from tasks_fake import ID, TaskWorld

from app.mcp import tasks as task_tools

BACKEND = Path(__file__).resolve().parents[1]
TESTS = BACKEND / "tests"
SPEC = BACKEND.parent / "specs" / "008-tasks" / "spec.md"
LABS = {"workspace": "labs"}

#: The two opt-in proofs of T073, and where their recorded output is.
RECORDED = {
    "db/test_task_migration_db.py::TestMapping": (
        'the commit message of "Add the shared tasks table, create_task and task '
        'visibility (008 slice S1a)": "TASKS_MIGRATION_CHECK: seed 1 passed; after '
        '046-050, 31 passed, 1 skipped"'
    ),
    "../scripts/simulations/tasks_load_5k.py": (
        "the commit message of 008 slice S1e: p95 of GET /tasks and POST /tasks "
        "for one person with 5,000 tasks"
    ),
}


#: For each line citing a recorded run beside unskipped covers (a prefix of
#: the line): its clauses that only the recorded run proves.
RECORDED_CLAUSES = {
    "Closing a deal Lost sets its open and waiting tasks to Cancelled": (
        "tasks closed this way before the migration stay Done",
    ),
    "The task status list in each workspace holds": ("a renamed Done label survives",),
}


#: Covers skipped only while `TASKS_MIGRATION_CHECK=seed` holds the database
#: at 045 for the opt-in run; at every database gate they run.
SEEDING_ONLY = {"db/test_task_migration_db.py::TestAfterMigration"}


def _phase_1_lines() -> list[str]:
    """Spec §12's phase-1 lines, in order, without their check boxes."""
    text = SPEC.read_text()
    start = text.index("Phase 1 — migration and CRM")
    end = text.index("\nPhase 2\n", start)
    return [
        line.removeprefix("- [ ] ").removeprefix("- [x] ")
        for line in text[start:end].splitlines()
        if line.startswith(("- [ ] ", "- [x] "))
    ]


LINES = _phase_1_lines()


def _phase_2_lines() -> list[str]:
    """Spec §12's phase-2 lines, in order, without their check boxes."""
    text = SPEC.read_text()
    start = text.index("\nPhase 2\n")
    end = text.index("\nPhase 3\n", start)
    return [
        line.removeprefix("- [ ] ").removeprefix("- [x] ")
        for line in text[start:end].splitlines()
        if line.startswith(("- [ ] ", "- [x] "))
    ]


LINES_2 = _phase_2_lines()


def _phase_3_lines() -> list[str]:
    """Spec §12's phase-3 lines, in order, without their check boxes."""
    text = SPEC.read_text()
    start = text.index("\nPhase 3\n")
    end = text.index("\nPhase 4\n", start)
    return [
        line.removeprefix("- [ ] ").removeprefix("- [x] ")
        for line in text[start:end].splitlines()
        if line.startswith(("- [ ] ", "- [x] "))
    ]


LINES_3 = _phase_3_lines()


def _phase_4_lines() -> list[str]:
    """Spec §12's phase-4 lines, in order, without their check boxes."""
    text = SPEC.read_text()
    start = text.index("\nPhase 4\n")
    end = text.index("\n---", start)
    return [
        line.removeprefix("- [ ] ").removeprefix("- [x] ")
        for line in text[start:end].splitlines()
        if line.startswith(("- [ ] ", "- [x] "))
    ]


LINES_4 = _phase_4_lines()
DAY_LOG_E2E = BACKEND.parent / "frontend" / "e2e" / "day-log.spec.ts"

#: Where the recorded run of `day-log.spec.ts` is: no gate runs Playwright,
#: so a title in the file proves nothing until a run is seen to pass it.
E2E_RECORDED = (
    'the commit message of "Record a passing run of the Log a day browser tests '
    '(008 slice S3a)": "16 passed" (each test on the mobile and the desktop project, '
    "after `npx supabase db reset`, with that commit's API and frontend)"
)
#: The tests that run passed (the titles as `test(` names them).
E2E_PASSED = frozenset(
    {
        "accepting two tasks on one project gives one line at zero hours",
        "nothing is written without Save",
        "removing the line, or the title from its note, offers the task again",
        "ignoring a task while choosing where it goes closes the choice",
        "a task on a project not allocated that day adds the project to the choices",
        "saving with hours writes one normal entry through Save day",
        "a task with no project asks where it goes, and a zero-hour line is not saved",
        "Log a day warns that a note is visible to the lead, managers and admins",
    }
)


# ---------------------------------------------------------------------------
# Covering tests: they exist and are not skipped
# ---------------------------------------------------------------------------


def _skips(decorators: list[ast.expr], *, seeding_only: bool = False) -> list[str]:
    found = []
    for d in decorators:
        names = {n.attr for n in ast.walk(d) if isinstance(n, ast.Attribute)}
        if (
            seeding_only
            and names & {"skipif"}
            and 'MODE == "seed"' in ast.unparse(d).replace("'", '"')
        ):
            continue
        found += sorted(names & {"skip", "skipif", "xfail"})
    return found


def _module_skips(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            elements = node.value.elts if isinstance(node.value, ast.List) else [node.value]
            return _skips(elements)
    return []


def _tests_in(node: ast.AST) -> list[str]:
    return [
        n.name
        for n in getattr(node, "body", [])
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test")
    ]


def check_cover(ref: str) -> None:
    """`ref` is `path` (a module), `path::Name` (a class or a test) or
    `path::Class::test`, the path relative to `tests/`."""
    path, *names = ref.split("::")
    file = TESTS / path
    assert file.is_file(), f"{ref}: no such file"
    if ref in RECORDED:
        assert RECORDED[ref]
        if not names:
            return
    tree = ast.parse(file.read_text())
    node: ast.AST = tree
    skips = _module_skips(tree)
    for name in names:
        found = [
            n
            for n in getattr(node, "body", [])
            if isinstance(n, ast.ClassDef | ast.FunctionDef) and n.name == name
        ]
        assert found, f"{ref}: no {name}"
        node = found[0]
        skips += _skips(node.decorator_list, seeding_only=ref in SEEDING_ONLY)
    if isinstance(node, ast.Module):
        assert any(
            isinstance(n, ast.FunctionDef) and n.name.startswith("test") for n in ast.walk(node)
        ), f"{ref}: holds no test"
    elif isinstance(node, ast.ClassDef):
        assert _tests_in(node), f"{ref}: holds no test"
    else:
        assert node.name.startswith("test"), f"{ref}: not a test"
    source = file.read_text() if isinstance(node, ast.Module) else ast.unparse(node)
    if "pytest.skip(" in source:
        skips.append("pytest.skip()")
    if ref not in RECORDED:
        assert not skips, f"{ref}: skipped ({skips})"


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def _sections(w: TaskWorld, who: str) -> dict[str, list[str]]:
    out = w.call(who, "GET", "").json()["sections"]
    return {name: [t["id"] for t in tasks] for name, tasks in out.items()}


def appears_in_my_tasks(monkeypatch) -> None:
    """Quick add (preview, then save), `task_add` and a CRM record's "Add
    task" each land in the creator's open My Tasks at once, in the right
    section; an assignee sees theirs on their next load (FR-TASK-ADD-06)."""
    w = TaskWorld(monkeypatch)

    def save(draft: dict, **extra) -> dict:
        body = {
            "title": draft["title"],
            "due_on": draft["due_on"],
            "bucket": draft["bucket"],
            "tags": draft["tags"],
            "assignee_id": draft["assignee"]["id"],
            **extra,
        }
        r = w.call("member", "POST", "", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    started = time.monotonic()
    preview = w.call(
        "member", "POST", "/quick-add/preview", json={"text": "Call Deepak re RDX pilot fri #sales"}
    ).json()
    assert preview["questions"] == []
    quick = save(preview["draft"])
    assert quick["id"] in _sections(w, "member")["this_week"]
    assert time.monotonic() - started < 2

    tool = asyncio.run(task_tools.task_add(Ctx("member"), "Ring the bank", due_on="2026-10-20"))
    assert tool["id"] in _sections(w, "member")["today"]

    r = w.http.post(
        "/api/v1/crm/tasks",
        params=LABS,
        json={
            "title": "Send the deck",
            "bucket": "this_month",
            "links": [w.link(w.acme, "company")],
        },
        headers=as_(ID["member"]),
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] in _sections(w, "member")["this_month"]

    given = w.call(
        "member", "POST", "/quick-add/preview", json={"text": "Draft the plan fri @Arun"}
    ).json()
    assert given["questions"] == [] and given["draft"]["assignee"]["id"] == ID["assignee"]
    shared = save(given["draft"])
    assert shared["id"] not in sum(_sections(w, "member").values(), [])
    assert shared["id"] in _sections(w, "assignee")["this_week"]


def timelines_read_the_old_names(monkeypatch) -> None:
    """History written before the rename keeps the old table names
    (`crm_tasks`, `crm_task_links`); the record timeline still shows it
    (FR-TASK-CHG-07)."""
    w = TaskWorld(monkeypatch)
    r = w.http.post(
        "/api/v1/crm/tasks",
        params=LABS,
        json={"title": "Zqxold follow-up", "links": [w.link(w.acme, "company")]},
        headers=as_(ID["owner"]),
    )
    assert r.status_code == 201, r.text
    task = r.json()
    done = w.http.post(
        f"/api/v1/crm/tasks/{task['id']}/complete", params=LABS, headers=as_(ID["owner"])
    )
    assert done.status_code == 200, done.text
    old = {"tasks": "crm_tasks", "task_links": "crm_task_links"}
    rows = [a for a in w.fake.audit if a["target_table"] in old]
    assert {a["target_table"] for a in rows} == set(old)
    for row in rows:
        row["target_table"] = old[row["target_table"]]
    timeline = w.http.get(
        f"/api/v1/crm/companies/{w.acme['id']}/timeline", params=LABS, headers=as_(ID["member"])
    )
    assert timeline.status_code == 200, timeline.text
    said = [e["summary"] for e in timeline.json()]
    assert "Added task “Zqxold follow-up”" in said
    assert "Completed task “Zqxold follow-up”" in said


class Ctx:
    def __init__(self, who: str) -> None:
        self.headers = {"authorization": f"Bearer {ID[who]}"}


# ---------------------------------------------------------------------------
# Each line: a scenario, or its covering tests
# ---------------------------------------------------------------------------

Proof = tuple[str, ...] | Callable[..., None]

#: (a prefix that starts exactly one spec line, how it is proved).
ENTRIES: list[tuple[str, Proof]] = [
    # -- Phase 1 — migration and CRM ---------------------------------------
    (
        "Every CRM task exists in `tasks` with the same id",
        ("db/test_task_migration_db.py::TestMapping",),
    ),
    (
        "Live workspace tasks with no link before the migration",
        ("db/test_task_migration_db.py::TestMapping",),
    ),
    (
        "Every CRM task test passes",
        (
            "test_crm_tasks_notes.py",
            "test_crm_today.py",
            "test_crm_flags.py",
            "test_crm_automations.py",
            "test_crm_digests.py",
            "test_crm_views_routes.py",
            "test_crm_search.py",
            "test_crm_merge.py",
            "test_crm_trash.py",
            "test_crm_import_routes.py",
            "test_crm_importer_mapping.py",
            "test_crm_csv_io.py",
            "test_crm_deals_routes.py",
            "test_crm_tva_lifecycle.py",
            "test_crm_handoff_rules.py",
            "test_crm_dashboard.py",
            "test_crm_timeline.py",
            "db/test_crm_records_db.py",
            "db/test_crm_deal_transition_db.py",
            "db/test_crm_flags_db.py",
            "db/test_crm_query_db.py",
            "db/test_crm_merge_db.py",
            "db/test_crm_import_db.py",
            "db/test_crm_search_db.py",
        ),
    ),
    (
        "A cancelled task counts as closed everywhere",
        (
            "test_task_crm_changes.py::TestCancelledIsClosed",
            "db/test_crm_flags_db.py::TestTaskQueries",
        ),
    ),
    (
        "Closing a deal Lost sets its open and waiting tasks to Cancelled",
        (
            "test_task_crm_changes.py::TestClosedLost",
            "db/test_crm_flags_db.py::TestTaskQueries",
            "db/test_task_migration_db.py::TestMapping",
        ),
    ),
    (
        "`crm_add_task`, `crm_complete_task`, `crm_get`",
        (
            "test_task_crm_changes.py::TestStatusInputs",
            "test_task_crm_changes.py::TestTheTools",
        ),
    ),
    (
        "The task status list in each workspace holds",
        (
            "db/test_task_migration_db.py::TestAfterMigration",
            "db/test_crm_seed_db.py::TestSeededLists",
            "db/test_task_migration_db.py::TestMapping",
        ),
    ),
    (
        "No saved view, favourite or seeded view references",
        (
            "db/test_task_migration_db.py::TestAfterMigration",
            "test_task_crm_changes.py::TestSavedViewStatus",
        ),
    ),
    (
        "The database refuses, at commit,",
        ("db/test_task_links_db.py::TestTheDeferredCheck",),
    ),
    (
        "The CRM writer refuses a task created through the CRM with no link",
        (
            "db/test_task_write_path_db.py::TestCrmApply",
            "test_task_crm_changes.py::TestLinks",
            "test_task_links_routes.py::TestLastCrmLink",
        ),
    ),
    (
        "Purging a record purges tasks left with no CRM link",
        (
            "test_task_crm_changes.py::TestLinks",
            "db/test_crm_records_db.py::TestPurgeLeavesNoLinklessTask",
        ),
    ),
    (
        "Deleting a task cascades its link rows",
        ("db/test_task_links_db.py::TestCascades",),
    ),
    (
        "An Academy task cannot link a TVA project",
        (
            "test_task_links_routes.py::TestProjectLinks",
            "test_task_create_interface.py::TestProjectLink",
            "db/test_task_links_db.py::TestProjectLinks",
        ),
    ),
    ("Record timelines show task history written before the rename", timelines_read_the_old_names),
    # -- Phase 1 — privacy -----------------------------------------------------
    (
        "FR-TASK-VIS-06 passes for every route and tool",
        (
            "test_task_privacy_sweep.py::TestScope",
            "test_task_privacy_sweep.py::TestTheTenAccounts",
            "test_task_privacy_sweep.py::TestTheMatrix",
        ),
    ),
    (
        "A CRM task is visible to every active workspace member",
        (
            "test_task_privacy_sweep.py::TestCrmLinksWin",
            "test_task_visibility.py::TestCrmTasks",
        ),
    ),
    (
        "A `crm_only` Labs member sees a deal task",
        (
            "test_task_privacy_sweep.py::TestCrmLinksWin::test_a_crm_only_member_gets_no_project_field",
            "test_task_mcp.py::TestCrmOnly",
            "test_task_visibility.py::TestProjectField",
        ),
    ),
    (
        "A project task with no CRM link is visible to every lead",
        (
            "test_task_privacy_sweep.py::TestProjectTasks",
            "test_task_visibility.py::TestProjectTasks",
        ),
    ),
    (
        "Someone who sees a project task only through the project rule",
        (
            "test_task_routes.py::TestChange::test_read_only_sight_cannot_change_it",
            "test_task_visibility.py::TestProjectTasks",
        ),
    ),
    (
        "A Viewer assigned a CRM task can change its status only",
        (
            "test_task_routes.py::TestChange::test_a_viewer_assignee_changes_the_status_only",
            "test_task_mcp.py::TestViewer",
        ),
    ),
    (
        "A Viewer, or a TVA user in no workspace, cannot add a CRM link",
        ("test_task_links_routes.py::TestCrmLinks",),
    ),
    (
        "A child task whose parent is private shows no parent id or title",
        (
            "test_task_privacy_sweep.py::TestParentAndHistory::test_a_private_parent_shows_no_id_or_title",
            "test_task_routes.py::TestParent",
            "test_task_create_interface.py::TestParent",
        ),
    ),
    (
        "A private task later linked to a CRM record shows its earlier history",
        (
            "test_task_privacy_sweep.py::TestParentAndHistory::test_private_era_history_stays_with_the_creator_and_assignee",
            "test_task_visibility.py::TestHistory",
        ),
    ),
    (
        "The task panel and the quick-add preview show who will see the task",
        (
            "test_task_preview_routes.py::TestVisibleTo",
            "test_task_visibility.py::TestAudience",
            "test_task_routes.py::TestFixedPaths",
        ),
    ),
    (
        "No task audit entry appears on the TVA audit page",
        ("test_task_audit_exclusion.py::TestTheTvaAudit",),
    ),
    (
        "No task title, note or transcript appears in the logs",
        ("test_task_logging.py::test_no_title_or_note_in_the_logs",),
    ),
    # -- Phase 1 — My Tasks, capture, tools, import, switches ----------------
    ("A task added by quick add, `task_add` or from a record", appears_in_my_tasks),
    (
        "Quick add at the fixed clock:",
        ("test_task_quick_add.py::TestTheFixedClock",),
    ),
    (
        "A task due today or overdue shows in Today whatever its stored bucket",
        (
            "test_task_listing_routes.py::TestSections",
            "test_task_rules.py::TestEffectiveBucket",
            "test_task_rules.py::TestDaysOverdue",
        ),
    ),
    (
        "Completing a task sets completed at; reopening clears it.",
        (
            "test_task_routes.py::TestCompleteAndReopen",
            "test_task_rules.py::TestStatusValues",
        ),
    ),
    (
        "A `crm_only` account can use My Tasks and `task_*` tools",
        (
            "test_task_routes.py::TestCreate::test_a_crm_only_account_uses_my_tasks",
            "test_task_mcp.py::TestCrmOnly",
            "test_crm_only_refused.py::TestClassification",
        ),
    ),
    (
        "A `crm_only` account cannot be assigned a project task",
        (
            "test_task_routes.py::TestCreate::test_a_crm_only_account_is_not_given_a_project_task",
            "test_task_routes.py::TestCreate::test_a_crm_only_member_is_given_a_crm_task_of_its_workspace",
            "test_task_create_interface.py::TestAssignee",
        ),
    ),
    (
        "Assigning a task with no workspace to someone without phase 1",
        (
            "test_task_routes.py::TestCreate::test_assignee_without_phase_1_is_refused_with_their_name",
            "test_task_create_interface.py::TestAssignee",
        ),
    ),
    (
        "A deleted task with no workspace can be restored by its creator",
        (
            "test_task_routes.py::TestDeleteAndRestore",
            "test_task_jobs.py::TestPurgeDeleted",
            "db/test_task_write_path_db.py::TestPurgeExpired",
        ),
    ),
    (
        "A tag over 40 characters, or an eleventh tag",
        (
            "test_task_routes.py::TestCreate::test_a_tag_over_40_characters_is_refused",
            "test_task_routes.py::TestCreate::test_an_eleventh_tag_is_refused",
            "test_task_rules.py::TestTags",
        ),
    ),
    (
        "My Tasks filters by tag, priority, link and text.",
        ("test_task_listing_routes.py::TestFilters",),
    ),
    (
        "The task panel shows the task's history",
        (
            "test_task_routes.py::TestRead::test_the_creator_reads_it_with_subtasks_and_history",
            "test_task_privacy_sweep.py::TestParentAndHistory",
            "test_task_visibility.py::TestHistory",
        ),
    ),
    (
        "My Tasks loads in under 1 second for a person with 5,000 visible tasks",
        ("../scripts/simulations/tasks_load_5k.py",),
    ),
    (
        "Life OS dry run and real run report and create matching counts",
        (
            "test_task_lifeos_routes.py::TestDryRun",
            "test_task_lifeos_routes.py::TestRealRun",
            "test_task_lifeos_mapping.py::TestContentKey",
        ),
    ),
    (
        "Imported rows of other owners are created by the uploader",
        ("test_task_lifeos_routes.py::TestWhatIsCreated",),
    ),
    (
        "Only the owner can run the Life OS import",
        ("test_task_lifeos_routes.py::TestOwnerOnly",),
    ),
    (
        "The import run record holds counts, owner mapping and file hash",
        (
            "test_task_lifeos_routes.py::TestDryRun::test_the_run_record_has_no_file_content_and_is_audited",
            "db/test_task_import_db.py::TestRunRecord",
        ),
    ),
    (
        "A person with phase 1 off gets 403 from every task route and tool",
        (
            "test_task_routes.py::TestOffByDefault",
            "test_task_mcp.py::TestThroughTheRoutes::test_phase_1_off_is_refused_by_every_tool",
        ),
    ),
    (
        "Switching a person's phase is audited",
        (
            "test_task_rollout.py::TestLevels",
            "test_task_rollout.py::TestUpdateUser",
            "test_task_rollout.py::TestTheDefault",
        ),
    ),
]


#: Phase 2 (S2a, S2b): (a prefix that starts exactly one spec line, its
#: covering tests).
ENTRIES_2: list[tuple[str, tuple[str, ...]]] = [
    (
        "Assigning to someone else sends exactly one message",
        (
            "test_task_delegation_routes.py::TestMessages",
            "test_task_delegation_routes.py::TestCrmRoutes",
        ),
    ),
    (
        "Waiting needs Waiting on, limited to people the caller may assign",
        (
            "test_task_delegation_routes.py::TestWaitingOn",
            "test_task_brief.py::TestNudge",
        ),
    ),
    (
        "The brief shows the CRM Today sections exactly as CRM Today does",
        (
            "test_task_brief.py::TestCrmToday",
            "test_task_brief.py::TestNeverPushed",
            "test_task_brief.py::TestTheTool",
        ),
    ),
    (
        "A TVA project page shows the tasks of that project that the viewer may see",
        ("test_task_views_routes.py::TestProjectPanel",),
    ),
]


#: Phase 3 (S3a): (a prefix that starts exactly one spec line, its covering
#: tests). `e2e:<title>` names a test of `frontend/e2e/day-log.spec.ts`.
ENTRIES_3: list[tuple[str, tuple[str, ...]]] = [
    (
        "Tasks completed today appear as suggestions on Log a day",
        (
            "test_task_suggestions.py::TestWhichTasks",
            "test_task_suggestions.py::TestWhenOffered",
        ),
    ),
    (
        "Accepting two tasks on one project gives one line",
        (
            "e2e:accepting two tasks on one project gives one line at zero hours",
            "e2e:saving with hours writes one normal entry through Save day",
            "e2e:nothing is written without Save",
            "e2e:a task with no project asks where it goes, and a zero-hour line is not saved",
            "test_task_suggestions.py::TestNothingStored",
        ),
    ),
    (
        "Suggestions never appear in someone else's week view",
        (
            "test_task_suggestions.py::TestOnlyTheOwnDay",
            "test_task_suggestions.py::TestWhichTasks::test_an_imported_task_is_never_suggested",
            "test_task_suggestions.py::TestAlreadyAdded",
            "test_task_suggestions.py::TestNothingStored",
        ),
    ),
    (
        "Log a day warns that a note is visible",
        ("e2e:Log a day warns that a note is visible to the lead, managers and admins",),
    ),
    (
        "Completing or cancelling a recurring task",
        (
            "test_task_recurrence.py::TestNextDue",
            "test_task_recurrence_routes.py::TestNextOccurrence",
            "test_task_recurrence_routes.py::TestSameFieldsAndLinks",
            "db/test_task_recurrence_db.py::TestSeriesDueDates",
        ),
    ),
]


#: Phase 4 (S4a, S4b): (a prefix that starts exactly one spec line, its
#: covering tests).
ENTRIES_4: list[tuple[str, tuple[str, ...]]] = [
    (
        "A voice note becomes candidates shown for confirmation",
        (
            "test_task_voice_split.py",
            "test_task_voice_routes.py::TestJourney",
            "test_task_voice_routes.py::TestNothingBeforeConfirm",
            "test_task_voice_routes.py::TestTheAudio",
        ),
    ),
    (
        "The sweep job deletes the audio and transcript of a failed transcription",
        (
            "test_task_voice_routes.py::TestTheSweep",
            "db/test_task_voice_db.py::TestTheRow",
        ),
    ),
    (
        "The transcript is deleted when the last candidate is confirmed or dropped",
        (
            "test_task_voice_routes.py::TestTheTranscript",
            "test_task_inbox_routes.py::TestFromAVoiceNote",
        ),
    ),
    (
        "Inbox items are visible only to their owner",
        (
            "test_task_inbox_routes.py::TestOwnerOnly",
            "test_task_inbox_routes.py::TestMakeItATask",
            "db/test_task_inbox_db.py::TestWrites",
            "test_task_privacy_sweep.py::TestTheTenAccounts",
        ),
    ),
    (
        "The Monday review panel shows the listed items and can be dismissed",
        (
            "test_task_review.py::TestThePanel",
            "test_task_review.py::TestDismiss",
            "test_task_review.py::TestNeverSent",
        ),
    ),
]


def check_e2e(title: str) -> None:
    """A test of the local Playwright spec exists by `title`, the spec skips,
    fixes or focuses nothing, and the recorded run saw the test pass."""
    source = DAY_LOG_E2E.read_text()
    assert f'test("{title}",' in source, f"day-log.spec.ts: no test {title!r}"
    for mark in ("test.skip(", "test.fixme(", ".only("):
        assert mark not in source, f"day-log.spec.ts: {mark}"
    assert E2E_RECORDED
    assert title in E2E_PASSED, f"day-log.spec.ts: no recorded passing run of {title!r}"


def _entry(line: str) -> Proof:
    [proof] = [proof for prefix, proof in ENTRIES if line.startswith(prefix)]
    return proof


def _id(line: str) -> str:
    return line[:60]


class TestTheLines:
    def test_phase_1_has_its_lines(self):
        assert len(LINES) > 40

    def test_every_line_has_exactly_one_entry(self):
        for line in LINES:
            assert sum(line.startswith(p) for p, _ in ENTRIES) == 1, line
        for prefix, _ in ENTRIES:
            assert sum(line.startswith(prefix) for line in LINES) == 1, prefix

    def test_phase_2_has_its_lines(self):
        assert len(LINES_2) == 4

    def test_every_phase_2_line_has_exactly_one_entry(self):
        for line in LINES_2:
            assert sum(line.startswith(p) for p, _ in ENTRIES_2) == 1, line
        for prefix, _ in ENTRIES_2:
            assert sum(line.startswith(prefix) for line in LINES_2) == 1, prefix

    def test_phase_3_has_its_lines(self):
        assert len(LINES_3) == 5

    def test_every_phase_3_line_has_exactly_one_entry(self):
        for line in LINES_3:
            assert sum(line.startswith(p) for p, _ in ENTRIES_3) == 1, line
        for prefix, _ in ENTRIES_3:
            assert sum(line.startswith(prefix) for line in LINES_3) == 1, prefix

    def test_phase_4_has_its_lines(self):
        assert len(LINES_4) == 5

    def test_every_phase_4_line_has_exactly_one_entry(self):
        for line in LINES_4:
            assert sum(line.startswith(p) for p, _ in ENTRIES_4) == 1, line
        for prefix, _ in ENTRIES_4:
            assert sum(line.startswith(prefix) for line in LINES_4) == 1, prefix

    def test_a_missing_or_skipped_e2e_test_is_caught(self, tmp_path, monkeypatch):
        spec = tmp_path / "day-log.spec.ts"
        spec.write_text('test("a", async () => {});\n')
        monkeypatch.setattr(sys.modules[__name__], "DAY_LOG_E2E", spec)
        monkeypatch.setattr(sys.modules[__name__], "E2E_PASSED", frozenset({"a", "b"}))
        check_e2e("a")
        with pytest.raises(AssertionError, match="no test"):
            check_e2e("b")
        spec.write_text('test("a", async () => {});\ntest.skip("b", async () => {});\n')
        with pytest.raises(AssertionError, match=r"test\.skip"):
            check_e2e("a")

    def test_an_e2e_test_never_seen_passing_is_caught(self, tmp_path, monkeypatch):
        """A title in the spec is not a proof: no gate runs Playwright."""
        spec = tmp_path / "day-log.spec.ts"
        spec.write_text('test("a", async () => {});\ntest("new", async () => {});\n')
        monkeypatch.setattr(sys.modules[__name__], "DAY_LOG_E2E", spec)
        monkeypatch.setattr(sys.modules[__name__], "E2E_PASSED", frozenset({"a"}))
        check_e2e("a")
        with pytest.raises(AssertionError, match="no recorded passing run"):
            check_e2e("new")

    def test_every_recorded_e2e_title_is_in_the_spec(self):
        source = DAY_LOG_E2E.read_text()
        for title in E2E_PASSED:
            assert f'test("{title}",' in source, title

    def test_the_recorded_proofs_are_the_two_exceptions(self):
        """Only the migration mapping and the 5,000-task line rest on a
        recorded run alone: whole lines, and the stated clauses of lines
        that cite one beside unskipped covers."""
        cited = [
            line
            for line in LINES
            if not callable(_entry(line)) and set(_entry(line)) & set(RECORDED)
        ]
        alone = [line for line in cited if set(_entry(line)) <= set(RECORDED)]
        mixed = [line for line in cited if line not in alone]
        assert [line[:40] for line in alone] == [
            "Every CRM task exists in `tasks` with th",
            "Live workspace tasks with no link before",
            "My Tasks loads in under 1 second for a p",
        ]
        for line in mixed:
            clauses = next((c for p, c in RECORDED_CLAUSES.items() if line.startswith(p)), None)
            assert clauses is not None, f"say which clauses only the recorded run proves: {line}"
            for clause in clauses:
                assert clause in line, clause
        assert [line[:40] for line in mixed] == [
            "Closing a deal Lost sets its open and wa",
            "The task status list in each workspace h",
        ]
        assert len(RECORDED_CLAUSES) == len(mixed)

    def test_the_load_script_measures_both_calls(self):
        script = (BACKEND / "scripts" / "simulations" / "tasks_load_5k.py").read_text()
        assert '"/tasks"' in script and "http.post" in script and "BOUND_MS = 1000" in script
        assert "p95" in script

    def test_the_load_script_measures_the_count_asked_for(self):
        """Seeded tasks left from a run with another `--tasks` stop the run,
        so a pass always means the count asked for was measured."""
        path = BACKEND / "scripts" / "simulations" / "tasks_load_5k.py"
        spec = importlib.util.spec_from_file_location("tasks_load_5k", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)

        class Stack:
            def __init__(self, already: int) -> None:
                self.already, self.inserts = already, 0

            def sql(self, query: str) -> list[dict]:
                return [{"n": self.already}] if "count(*)" in query else []

            def as_system(self, statement: str) -> list[dict]:
                self.inserts += 1
                return []

        with pytest.raises(SystemExit, match="200 seeded tasks.*5000"):
            script.seed(Stack(200), ID["owner"], 5000)
        same = Stack(5000)
        script.seed(same, ID["owner"], 5000)
        fresh = Stack(0)
        script.seed(fresh, ID["owner"], 5000)
        assert (same.inserts, fresh.inserts) == (0, 1)

    def test_a_skipped_cover_is_caught(self, tmp_path, monkeypatch):
        """The check itself: a skipped test or a missing class fails it."""
        (tmp_path / "test_x.py").write_text(
            "import pytest\n\n\nclass TestA:\n    @pytest.mark.skip\n    def test_a(self):\n"
            "        pass\n\n\nclass TestB:\n    def test_b(self):\n        pass\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "TESTS", tmp_path)
        check_cover("test_x.py::TestB")
        with pytest.raises(AssertionError, match="skipped"):
            check_cover("test_x.py::TestA::test_a")
        with pytest.raises(AssertionError, match="no TestC"):
            check_cover("test_x.py::TestC")

    def test_an_xfail_or_a_skip_call_is_caught(self, tmp_path, monkeypatch):
        (tmp_path / "test_y.py").write_text(
            "import pytest\n\n\n@pytest.mark.xfail\nclass TestA:\n    def test_a(self):\n"
            "        pass\n\n\nclass TestB:\n    def test_b(self):\n"
            '        pytest.skip("later")\n\n\ndef test_c():\n    pass\n'
        )
        (tmp_path / "test_z.py").write_text(
            "import pytest\n\n\ndef test_d():\n    pytest.skip('later')\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "TESTS", tmp_path)
        check_cover("test_y.py::test_c")
        with pytest.raises(AssertionError, match="xfail"):
            check_cover("test_y.py::TestA")
        with pytest.raises(AssertionError, match=r"pytest\.skip"):
            check_cover("test_y.py::TestB::test_b")
        with pytest.raises(AssertionError, match=r"pytest\.skip"):
            check_cover("test_y.py::TestB")
        with pytest.raises(AssertionError, match=r"pytest\.skip"):
            check_cover("test_z.py")


@pytest.mark.parametrize("line", LINES, ids=_id)
def test_phase_1_line(line, monkeypatch):
    proof = _entry(line)
    if callable(proof):
        proof(monkeypatch)
        return
    for ref in proof:
        check_cover(ref)


@pytest.mark.parametrize("line", LINES_2, ids=_id)
def test_phase_2_line(line):
    [proof] = [proof for prefix, proof in ENTRIES_2 if line.startswith(prefix)]
    for ref in proof:
        assert ref not in RECORDED
        check_cover(ref)


@pytest.mark.parametrize("line", LINES_3, ids=_id)
def test_phase_3_line(line):
    [proof] = [proof for prefix, proof in ENTRIES_3 if line.startswith(prefix)]
    for ref in proof:
        if ref.startswith("e2e:"):
            check_e2e(ref.removeprefix("e2e:"))
            continue
        assert ref not in RECORDED
        check_cover(ref)


@pytest.mark.parametrize("line", LINES_4, ids=_id)
def test_phase_4_line(line):
    [proof] = [proof for prefix, proof in ENTRIES_4 if line.startswith(prefix)]
    for ref in proof:
        assert ref not in RECORDED
        check_cover(ref)
