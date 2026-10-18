"""The Life OS import's routes — spec 008 contracts §1; FR-TASK-IMP-01 to
-08; TP-10, TP-17, TP-19; Journey 4.1.

`tests/fixtures/lifeos_tasks.csv` through `POST /tasks/import/dry-run` and
`POST /tasks/import/{run_id}/apply`, as Navaneeth (the owner), on the shared
task world at the fixed clock (Tuesday 20 October 2026, noon IST). The
fixture's two unknown statuses are fixed "in the sheet" (the text) before a
real run, as Journey 4.1 does; "Vinitha" is mapped to Vinita's account.

Acceptance criteria (spec §12, phase 1):
- "Life OS dry run and real run report and create matching counts; an
  unmapped owner, status or priority value blocks the real run; a real run
  more than 24 hours after its dry run is refused; a second real run of the
  same file changes nothing; a real run of an edited file creates only the
  new or edited rows; a real run of a file other than the last dry run is
  refused" — TestDryRun, TestRealRun (`test_another_file_is_refused`,
  `test_a_run_other_than_the_last_dry_run_is_refused`).
- "Imported rows of other owners are created by the uploader and assigned
  to that owner; Waiting rows carry Waiting on "Life OS"" — TestWhatIsCreated.
- "Only the owner can run the Life OS import; no MCP tool runs it" —
  TestOwnerOnly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest
from starlette.datastructures import FormData
from tasks_fake import ID, NOW, TaskWorld

from app.mcp import server

FIXTURE = Path(__file__).parent / "fixtures" / "lifeos_tasks.csv"
VINITHA = {"owners": {"Vinitha": "vinita@nunnarilabs.com"}}


def _csv(**replace: str) -> bytes:
    text = FIXTURE.read_text(encoding="utf-8")
    for old, new in replace.items():
        text = text.replace(old, new)
    return text.encode("utf-8")


#: The fixture with its two unknown statuses fixed in the sheet.
FIXED = _csv(Someday="Open", Blocked="Waiting")


@pytest.fixture
def w(monkeypatch) -> TaskWorld:
    return TaskWorld(monkeypatch)


def _dry_run(w: TaskWorld, content: bytes, mapping=VINITHA, who: str = "owner"):
    data = {"mapping": json.dumps(mapping)} if mapping is not None else {}
    return w.call(who, "POST", "/import/dry-run", files={"file": ("tasks.csv", content)}, data=data)


def _apply(w: TaskWorld, run_id: str, content: bytes, who: str = "owner"):
    return w.call(who, "POST", f"/import/{run_id}/apply", files={"file": ("tasks.csv", content)})


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _run(w: TaskWorld, content: bytes = FIXED) -> dict:
    """A dry run and its real run; the real run's answer."""
    run = _ok(_dry_run(w, content))
    return _ok(_apply(w, run["run_id"], content))


def _imported(w: TaskWorld) -> dict[str, dict]:
    return {t["title"]: t for t in w.tasks.values() if t["source"] == "import"}


class TestDryRun:
    def test_it_reports_the_counts_owners_unmapped_values_and_dates(self, w):
        body = _ok(_dry_run(w, _csv(), mapping=None))
        assert body["counts"] == {
            "rows": 11,
            "open": 4,
            "waiting": 1,
            "done": 2,
            "cancelled": 1,
            "skipped": 1,
            "existing": 0,
            "repeated": 1,
        }
        assert body["owners"] == [
            {
                "value": "Navaneeth",
                "proposed_email": "navaneeth@nunnarilabs.com",
                "proposed_name": "Navaneeth",
                "mapped": True,
            },
            {
                "value": "Deepika",
                "proposed_email": "deepika@nunnarilabs.com",
                "proposed_name": "Deepika",
                "mapped": True,
            },
            {"value": "Vinitha", "proposed_email": None, "proposed_name": None, "mapped": False},
        ]
        assert body["unmapped"] == {
            "owners": ["Vinitha"],
            "status": ["Someday", "Blocked"],
            "priority": [],
        }
        assert body["ambiguous_dates"] == [
            {"row": 5, "value": "3/11/2026", "read_as": "2026-11-03"}
        ]
        assert body["waiting_as_life_os"] == 1
        assert body["owners_without_tasks"] == []
        assert len(body["blocking"]) == 3
        uuid.UUID(body["run_id"])

    def test_the_owners_mapping_maps_vinitha_to_vinita(self, w):
        body = _ok(_dry_run(w, _csv()))
        assert body["unmapped"]["owners"] == []
        assert body["owners"][2]["proposed_name"] == "Vinita"

    def test_it_writes_nothing_but_its_run_record(self, w):
        before = len(w.tasks)
        _ok(_dry_run(w, FIXED))
        assert len(w.tasks) == before
        assert len(w.fake.rows["task_import_runs"]) == 1

    def test_the_run_record_has_no_file_content_and_is_audited(self, w):
        _ok(_dry_run(w, FIXED))
        [run] = w.fake.rows["task_import_runs"].values()
        text = json.dumps(run)
        for cell in ("Deepak", "invoice", "Draft v2", "venue", "sales", "3/11/2026"):
            assert cell not in text
        assert run["uploader_id"] == ID["owner"]
        assert run["owner_mapping"] == {
            "Navaneeth": "navaneeth@nunnarilabs.com",
            "Deepika": "deepika@nunnarilabs.com",
            "Vinitha": "vinita@nunnarilabs.com",
        }
        assert run["value_mapping"]["status"]["Waiting"] == "waiting"
        [audit] = [a for a in w.fake.audit if a["target_table"] == "task_import_runs"]
        assert audit["action"] == "task_import_run.created"
        assert audit["actor_id"] == ID["owner"]
        assert audit["workspace_id"] is None

    def test_a_mapping_with_status_or_priority_keys_is_refused(self, w):
        for extra in ({"status": {"Someday": "open"}}, {"priority": {"P0": "high"}}):
            r = _dry_run(w, _csv(), mapping={**VINITHA, **extra})
            assert r.status_code == 422, r.text
        assert w.fake.rows["task_import_runs"] == {}

    def test_a_file_over_4_mb_answers_413(self, w):
        big = FIXED + b"x" * (4 * 1024 * 1024)
        r = _dry_run(w, big)
        assert r.status_code == 413
        assert w.fake.rows["task_import_runs"] == {}

    def test_the_file_stays_in_memory_and_the_form_is_closed(self, w, monkeypatch):
        """FR-TASK-IMP-08 — Starlette spools a file part past 1 MB to disk."""
        rolled, closed = [], []
        monkeypatch.setattr(SpooledTemporaryFile, "rollover", lambda f: rolled.append(f))
        close = FormData.close

        async def counted(form):
            closed.append(form)
            await close(form)

        monkeypatch.setattr(FormData, "close", counted)
        content = FIXED + b"\n" * (3 * 1024 * 1024)
        run = _ok(_dry_run(w, content))
        _ok(_apply(w, run["run_id"], content))
        assert rolled == []
        assert len(closed) == 2

    def test_dry_run_reaches_its_own_handler_not_tasks_id(self, w):
        assert "run_id" in _ok(_dry_run(w, FIXED))
        r = w.call("owner", "POST", "/import/not-a-uuid/apply", files={"file": ("t.csv", FIXED)})
        assert r.status_code == 422

    def test_a_mapped_owner_without_my_tasks_blocks(self, w):
        content = FIXED + b"Ask Omar,Open,,Omar Off,,,,\n"
        body = _ok(_dry_run(w, content))
        assert body["owners_without_tasks"] == ["Omar Off"]
        assert body["blocking"] == ["Omar Off does not have My Tasks yet."]
        r = _apply(w, body["run_id"], content)
        assert r.status_code == 422
        assert "Omar Off does not have My Tasks yet." in r.json()["detail"]


class TestRealRun:
    def test_it_creates_the_counts_the_dry_run_reported(self, w):
        run = _ok(_dry_run(w, FIXED))
        assert run["blocking"] == []
        done = _ok(_apply(w, run["run_id"], FIXED))
        assert done == {
            "run_id": run["run_id"],
            "counts": {"created": 10, "existing": 0, "repeated": 1, "skipped": 1},
        }
        assert len(_imported(w)) == 10
        statuses = [t["status"] for t in _imported(w).values()]
        assert {s: statuses.count(s) for s in set(statuses)} == {
            "open": run["counts"]["open"],
            "waiting": run["counts"]["waiting"],
            "done": run["counts"]["done"],
            "cancelled": run["counts"]["cancelled"],
        }
        stored = w.fake.rows["task_import_runs"][run["run_id"]]
        assert stored["status"] == "applied"
        assert stored["counts"]["created"] == 10

    def test_an_unmapped_status_blocks_the_real_run(self, w):
        content = _csv()
        run = _ok(_dry_run(w, content))
        r = _apply(w, run["run_id"], content)
        assert r.status_code == 422
        assert "Someday" in r.json()["detail"]
        assert "fix it in the sheet and export again" in r.json()["detail"]
        assert _imported(w) == {}

    def test_an_unmapped_owner_blocks_the_real_run(self, w):
        run = _ok(_dry_run(w, FIXED, mapping=None))
        r = _apply(w, run["run_id"], FIXED)
        assert r.status_code == 422
        assert "Vinitha" in r.json()["detail"]
        assert _imported(w) == {}

    def test_an_unmapped_priority_blocks_the_real_run(self, w):
        content = _csv(Someday="Open", Blocked="Waiting", Urgent="P0")
        run = _ok(_dry_run(w, content))
        assert run["unmapped"]["priority"] == ["P0"]
        assert _apply(w, run["run_id"], content).status_code == 422

    def test_more_than_24_hours_after_the_dry_run_is_refused(self, w):
        run = _ok(_dry_run(w, FIXED))
        stored = w.fake.rows["task_import_runs"][run["run_id"]]
        stored["dry_run_at"] = (NOW - timedelta(hours=24, seconds=1)).isoformat()
        r = _apply(w, run["run_id"], FIXED)
        assert r.status_code == 409
        assert r.json()["detail"] == "The dry run is more than 24 hours old; dry-run it again."
        stored["dry_run_at"] = (NOW - timedelta(hours=23)).isoformat()
        assert _apply(w, run["run_id"], FIXED).status_code == 200

    def test_another_file_is_refused(self, w):
        run = _ok(_dry_run(w, FIXED))
        r = _apply(w, run["run_id"], FIXED + b"One more,Open,,Navaneeth,,,,\n")
        assert r.status_code == 409
        assert r.json()["detail"] == "This is not the file you dry-ran; dry-run it again."
        assert _imported(w) == {}

    def test_a_second_real_run_of_the_same_file_changes_nothing(self, w):
        first = _run(w)
        tasks = {k: dict(v) for k, v in w.tasks.items()}
        again = _run(w)
        assert first["counts"]["created"] == 10
        assert again["counts"] == {"created": 0, "existing": 10, "repeated": 1, "skipped": 11}
        assert w.tasks == tasks

    def test_applying_a_run_again_changes_nothing(self, w):
        first = _run(w)
        tasks = {k: dict(v) for k, v in w.tasks.items()}
        stored = dict(w.fake.rows["task_import_runs"][first["run_id"]])
        assert _ok(_apply(w, first["run_id"], FIXED)) == first
        assert w.tasks == tasks
        assert w.fake.rows["task_import_runs"][first["run_id"]] == stored

    def test_a_run_other_than_the_last_dry_run_is_refused(self, w):
        older = _ok(_dry_run(w, FIXED))
        newer = _csv(Someday="Open", Blocked="Waiting", **{"Review the deck": "Review it"})
        _ok(_dry_run(w, newer, mapping={"owners": {"Vinitha": "deepika@nunnarilabs.com"}}))
        r = _apply(w, older["run_id"], FIXED)
        assert r.status_code == 409
        assert r.json()["detail"] == "This is not the file you dry-ran; dry-run it again."
        assert _imported(w) == {}

    def test_an_edited_file_creates_only_the_new_rows(self, w):
        _run(w)
        before = set(_imported(w))
        edited = (
            _csv(Someday="Open", Blocked="Waiting", **{"Review the deck": "Review the new deck"})
            + b"A brand new row,Open,,Navaneeth,,,,\n"
        )
        done = _run(w, edited)
        assert done["counts"]["created"] == 2
        assert set(_imported(w)) - before == {"Review the new deck", "A brand new row"}
        assert "Review the deck" in _imported(w)  # the earlier task stays

    def test_another_persons_run_is_404(self, w):
        run = _ok(_dry_run(w, FIXED))
        w.fake.rows["task_import_runs"][run["run_id"]]["uploader_id"] = ID["outsider"]
        assert _apply(w, run["run_id"], FIXED).status_code == 404
        assert _apply(w, str(uuid.uuid4()), FIXED).status_code == 404


class TestWhatIsCreated:
    def test_the_uploader_creates_and_the_rows_owner_is_assigned(self, w):
        _run(w)
        tasks = _imported(w)
        assert {t["created_by"] for t in tasks.values()} == {ID["owner"]}
        assert tasks["Review the deck"]["assignee_id"] == ID["member"]
        assert tasks["Old idea"]["assignee_id"] == ID["outsider"]
        assert tasks["Call Deepak re RDX pilot"]["assignee_id"] == ID["owner"]
        assert all(t["workspace_id"] is None for t in tasks.values())
        assert all(t["source_ref"].startswith("lifeos:") for t in tasks.values())

    def test_the_audit_records_the_uploader_with_actor_kind_import(self, w):
        _run(w)
        ids = {t["id"] for t in _imported(w).values()}
        rows = [a for a in w.fake.audit if a["target_id"] in ids]
        assert len(rows) == 10
        assert {(a["actor_id"], a["actor_label"]) for a in rows} == {(ID["owner"], "import")}

    def test_the_other_owners_see_their_rows_in_my_tasks(self, w):
        _run(w)
        body = _ok(w.call("member", "GET", ""))
        titles = [t["title"] for section in body["sections"].values() for t in section]
        assert sorted(titles) == ["Plan the offsite", "Review the deck", "Write the blog post"]

    def test_waiting_rows_wait_on_life_os(self, w):
        _run(w)
        tasks = _imported(w)
        for title in ("Book the venue", "Hire a designer"):
            assert tasks[title]["status"] == "waiting"
            assert tasks[title]["waiting_on_text"] == "Life OS"
            assert tasks[title]["waiting_on_id"] is None

    def test_done_rows_carry_their_completed_at_and_cancelled_rows_none(self, w):
        _run(w)
        tasks = _imported(w)
        assert tasks["File GST return"]["completed_at"] == "2026-10-12T00:00:00+05:30"
        assert tasks["Old idea"]["completed_at"] == NOW.isoformat()
        assert tasks["Dropped plan"]["status"] == "cancelled"
        assert tasks["Dropped plan"]["completed_at"] is None
        assert tasks["Call Deepak re RDX pilot"]["completed_at"] is None

    def test_open_rows_take_their_bucket_from_the_due_date(self, w):
        _run(w)
        tasks = _imported(w)
        assert tasks["Send invoice to Acme"]["bucket"] == "today"  # due 20 October
        assert tasks["Call Deepak re RDX pilot"]["bucket"] == "later"
        assert tasks["Call Deepak re RDX pilot"]["tags"] == ["sales", "rdx"]
        assert tasks["Call Deepak re RDX pilot"]["priority"] == "high"

    def test_an_imported_task_sends_no_notice(self, w):
        before = dict(w.fake.rows["crm_notifications"])
        _run(w)
        assert w.fake.rows["crm_notifications"] == before


class TestOwnerOnly:
    @pytest.mark.parametrize("who", ["member", "outsider", "manager", "seller"])
    def test_anyone_but_the_owner_is_refused(self, w, who):
        r = _dry_run(w, FIXED, who=who)
        assert r.status_code == 403
        assert r.json()["detail"] == "Only the owner can import Life OS."
        r = _apply(w, str(uuid.uuid4()), FIXED, who=who)
        assert r.status_code == 403
        assert r.json()["detail"] == "Only the owner can import Life OS."
        assert w.fake.rows["task_import_runs"] == {}

    def test_no_tool_runs_it(self):
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert not [n for n in names if "lifeos" in n or "life_os" in n]
        assert not [n for n in names if n.startswith("task_") and "import" in n]
