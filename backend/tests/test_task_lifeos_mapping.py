"""The Life OS import's rules — spec 008 FR-TASK-IMP-02 to -05, -07, -08;
TP-17; Journey 4.1; data-model §2.3.

Pure, on `tests/fixtures/lifeos_tasks.csv` (11 rows: a "Vinitha" owner, two
status values the fixed mapping does not know, an ambiguous `D/M/YYYY`
date, a repeated row and a waiting row) at the fixed clock of spec §12:
Tuesday 20 October 2026, noon IST.

Acceptance criteria (spec §12, phase 1 — the rules behind them; the routes
are proved in test_task_lifeos_routes.py):
- "Life OS dry run … report[s] … matching counts; an unmapped owner, status
  or priority value blocks the real run" — TestCounts, TestOwners,
  TestStatusAndPriority.
- "a real run of an edited file creates only the new or edited rows" —
  TestContentKey.
- "Waiting rows carry Waiting on "Life OS"" — TestRows.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.domain.tasks import lifeos

FIXTURE = Path(__file__).parent / "fixtures" / "lifeos_tasks.csv"
NOW = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)
IST = "Asia/Kolkata"


def _account(n: int, name: str, email: str, **values) -> dict:
    return {
        "id": f"00000000-0000-4000-8000-{n:012d}",
        "display_name": name,
        "email": email,
        "tasks_level": 1,
        **values,
    }


NAVANEETH = _account(1, "Navaneeth Malingan", "navaneeth@nunnarilabs.com")
TIMOTHY = _account(2, "Timothy Paul", "timothy@nunnarilabs.com")
VINITA = _account(3, "Vinita", "vinita@nunnarilabs.com")
DEEPIKA = _account(4, "Deepika", "deepika@nunnarilabs.com")
ACCOUNTS = [NAVANEETH, TIMOTHY, VINITA, DEEPIKA]


def _text(**replace: str) -> str:
    text = FIXTURE.read_text(encoding="utf-8")
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def _plan(text: str | None = None, mapping=None, accounts=ACCOUNTS, default_level=0):
    return lifeos.plan(
        lifeos.read(text if text is not None else _text()),
        mapping=mapping,
        accounts=accounts,
        default_level=default_level,
        now=NOW,
        zone=IST,
    )


VINITHA_MAPPED = {"Vinitha": VINITA["email"]}


class TestReading:
    def test_the_columns_of_the_sheet(self):
        rows = lifeos.read(_text())
        assert len(rows) == 11
        first = rows[0]
        assert first.n == 2
        assert first.cells == {
            "title": "Call Deepak re RDX pilot",
            "status": "Open",
            "priority": "High",
            "owner": "Navaneeth",
            "due_on": "2026-10-23",
            "tags": "sales, rdx",
            "notes": "",
            "completed_on": "",
        }
        assert rows[2].cells["notes"] == "Draft v2 is in Drive"

    def test_a_missing_column_reads_as_blank_and_a_blank_row_is_left_out(self):
        rows = lifeos.read("task,owner\nOne,Navaneeth\n,\n")
        assert [r.cells["title"] for r in rows] == ["One"]
        assert rows[0].cells["status"] == ""

    def test_a_file_with_no_task_column_is_refused(self):
        with pytest.raises(ValueError, match="no Task column"):
            lifeos.read("Title,Owner\nOne,Navaneeth\n")


class TestDates:
    @pytest.mark.parametrize(
        ("text", "read_as", "ambiguous"),
        [
            ("2026-10-23", date(2026, 10, 23), False),
            ("20/10/2026", date(2026, 10, 20), False),
            ("3/11/2026", date(2026, 11, 3), True),
            ("11/11/2026", date(2026, 11, 11), False),
            ("13/11/2026", date(2026, 11, 13), False),
            ("5 Nov 2026", date(2026, 11, 5), False),
            ("30 October 2026", date(2026, 10, 30), False),
            ("", None, False),
        ],
    )
    def test_the_four_forms_day_first(self, text, read_as, ambiguous):
        assert lifeos.read_date(text) == (read_as, ambiguous)

    @pytest.mark.parametrize("text", ["10/23/2026", "tomorrow", "2026/10/23", "5 Novembre 2026"])
    def test_anything_else_is_not_a_date(self, text):
        with pytest.raises(ValueError):
            lifeos.read_date(text)

    def test_the_ambiguous_date_is_listed_read_day_first(self):
        assert _plan().ambiguous_dates == [
            {"row": 5, "value": "3/11/2026", "read_as": "2026-11-03"}
        ]

    def test_an_unreadable_date_blocks_the_real_run_naming_its_row(self):
        plan = _plan(_text(**{"2026-10-10": "sometime"}), VINITHA_MAPPED)
        assert "Row 6: the due date is not a date the import can read." in plan.blocking


class TestOwners:
    def test_a_full_name_or_a_unique_first_name_is_proposed_ignoring_case(self):
        proposed = lifeos.propose(["timothy paul", "Navaneeth", "Vinitha"], ACCOUNTS)
        assert proposed == {
            "timothy paul": TIMOTHY["email"],
            "Navaneeth": NAVANEETH["email"],
            "Vinitha": None,
        }

    def test_a_first_name_shared_by_two_accounts_is_left_unmapped(self):
        other = _account(9, "Deepika Raj", "deepika.r@nunnarilabs.com")
        assert lifeos.propose(["Deepika"], [*ACCOUNTS, other]) == {"Deepika": DEEPIKA["email"]}
        assert lifeos.propose(["deepika"], [_account(8, "Deepika S", "s@x"), other]) == {
            "deepika": None
        }

    def test_vinitha_is_unmapped_until_the_owner_maps_her_to_vinita(self):
        plan = _plan()
        assert plan.unmapped["owners"] == ["Vinitha"]
        assert "The owner “Vinitha” is not mapped to an active account." in plan.blocking
        mapped = _plan(mapping=VINITHA_MAPPED)
        assert mapped.unmapped["owners"] == []
        assert {o["value"]: o["proposed_name"] for o in mapped.owners} == {
            "Navaneeth": "Navaneeth Malingan",
            "Deepika": "Deepika",
            "Vinitha": "Vinita",
        }
        assert mapped.owner_mapping["Vinitha"] == VINITA["email"]

    def test_the_owners_edit_replaces_the_proposal_and_null_unmaps(self):
        plan = _plan(mapping={"Navaneeth": None, "Vinitha": VINITA["email"]})
        assert plan.unmapped["owners"] == ["Navaneeth"]

    def test_an_email_of_no_active_account_stays_unmapped(self):
        plan = _plan(mapping={"Vinitha": "nobody@nunnarilabs.com"})
        assert plan.unmapped["owners"] == ["Vinitha"]

    def test_a_blank_owner_is_one_value_and_is_never_proposed(self):
        plan = _plan("Task,Owner\nOne,\nTwo,  \n")
        assert [o["value"] for o in plan.owners] == [lifeos.BLANK]
        assert plan.unmapped["owners"] == [lifeos.BLANK]
        mapped = _plan("Task,Owner\nOne,\n", {lifeos.BLANK: NAVANEETH["email"]})
        assert mapped.rows[0].assignee_id == NAVANEETH["id"]

    def test_a_mapped_owner_without_my_tasks_blocks_naming_them(self):
        off = {**TIMOTHY, "tasks_level": None}
        plan = _plan("Task,Owner\nOne,Timothy Paul\n", accounts=[NAVANEETH, off])
        assert plan.owners_without_tasks == ["Timothy Paul"]
        assert plan.blocking == ["Timothy Paul does not have My Tasks yet."]
        on = _plan("Task,Owner\nOne,Timothy Paul\n", accounts=[NAVANEETH, off], default_level=1)
        assert on.blocking == []


class TestStatusAndPriority:
    def test_the_fixed_mapping(self):
        assert [lifeos.map_status(v) for v in ("Open", "to do", "In Progress", "")] == ["open"] * 4
        assert lifeos.map_status("WAITING") == "waiting"
        assert lifeos.map_status("Completed") == "done"
        assert lifeos.map_status("Canceled") == "cancelled"
        assert [lifeos.map_priority(v) for v in ("Urgent", "high", "Medium", "", "Low")] == [
            "urgent",
            "high",
            "normal",
            "normal",
            "low",
        ]

    def test_two_unknown_statuses_block_and_say_fix_it_in_the_sheet(self):
        plan = _plan(mapping=VINITHA_MAPPED)
        assert plan.unmapped["status"] == ["Someday", "Blocked"]
        assert plan.blocking == [
            "The status “Someday” cannot be mapped; fix it in the sheet and export again.",
            "The status “Blocked” cannot be mapped; fix it in the sheet and export again.",
        ]

    def test_an_unknown_priority_blocks(self):
        plan = _plan("Task,Priority,Owner\nOne,P0,Navaneeth\n")
        assert plan.unmapped["priority"] == ["P0"]
        assert plan.blocking == [
            "The priority “P0” cannot be mapped; fix it in the sheet and export again."
        ]

    def test_the_value_mapping_records_the_file(self):
        assert _plan().value_mapping["status"] == {
            "Open": "open",
            "To Do": "open",
            "In Progress": "open",
            "Waiting": "waiting",
            "Done": "done",
            "Cancelled": "cancelled",
            "Someday": None,
            "Blocked": None,
        }
        assert _plan().value_mapping["priority"][lifeos.BLANK] == "normal"

    def test_a_fixed_file_has_nothing_blocking(self):
        plan = _plan(_text(Someday="Open", Blocked="Waiting"), VINITHA_MAPPED)
        assert plan.blocking == []


class TestCounts:
    def test_the_fixture_counts(self):
        assert _plan(mapping=VINITHA_MAPPED).counts == {
            "rows": 11,
            "open": 4,
            "waiting": 1,
            "done": 2,
            "cancelled": 1,
            "skipped": 1,
            "existing": 0,
            "repeated": 1,
        }

    def test_the_fixed_file_counts(self):
        plan = _plan(_text(Someday="Open", Blocked="Waiting"), VINITHA_MAPPED)
        assert plan.counts["open"] == 5
        assert plan.counts["waiting"] == 2
        assert plan.waiting_as_life_os == 2
        assert len(plan.to_create) == 10

    def test_the_repeated_row_is_the_second(self):
        plan = _plan(mapping=VINITHA_MAPPED)
        assert [(r.n, r.skip) for r in plan.rows if r.skip] == [(9, "repeated")]

    def test_rows_an_earlier_run_created_are_skipped_as_existing(self):
        plan = _plan(_text(Someday="Open", Blocked="Waiting"), VINITHA_MAPPED)
        plan.skip_existing({r.key for r in plan.rows if r.key})
        assert plan.to_create == []
        assert plan.counts["existing"] == 10
        assert plan.counts["skipped"] == 11


class TestContentKey:
    def test_the_key_is_lifeos_and_a_hash_of_owner_title_and_due(self):
        key = lifeos.content_key("u1", "  Call   Deepak ", date(2026, 10, 23))
        assert key.startswith("lifeos:")
        assert len(key) == len("lifeos:") + 64
        assert key == lifeos.content_key("u1", "call deepak", date(2026, 10, 23))

    @pytest.mark.parametrize(
        "changed",
        [
            ("u2", "Call Deepak", date(2026, 10, 23)),
            ("u1", "Call Deepak today", date(2026, 10, 23)),
            ("u1", "Call Deepak", date(2026, 10, 24)),
            ("u1", "Call Deepak", None),
        ],
    )
    def test_an_edited_title_owner_or_due_date_is_a_new_key(self, changed):
        assert lifeos.content_key(*changed) != lifeos.content_key(
            "u1", "Call Deepak", date(2026, 10, 23)
        )

    def test_an_edited_file_creates_only_its_new_rows(self):
        first = _plan(_text(Someday="Open", Blocked="Waiting"), VINITHA_MAPPED)
        edited = _plan(
            _text(Someday="Open", Blocked="Waiting", **{"Review the deck": "Review the new deck"})
            + "A brand new row,Open,,Navaneeth,,,,\n",
            VINITHA_MAPPED,
        )
        edited.skip_existing({r.key for r in first.rows if r.key})
        assert [r.title for r in edited.to_create] == ["Review the new deck", "A brand new row"]


class TestRows:
    def _by_title(self) -> dict[str, lifeos.Planned]:
        plan = _plan(_text(Someday="Open", Blocked="Waiting"), VINITHA_MAPPED)
        return {r.title: r for r in plan.to_create}

    def test_waiting_rows_wait_on_life_os(self):
        rows = self._by_title()
        assert rows["Book the venue"].status == "waiting"
        assert rows["Book the venue"].waiting_on_text == "Life OS"
        assert rows["Call Deepak re RDX pilot"].waiting_on_text is None

    def test_done_rows_take_their_completed_date_or_the_import_time(self):
        rows = self._by_title()
        gst = rows["File GST return"].completed_at
        assert gst is not None and gst.isoformat() == "2026-10-12T00:00:00+05:30"
        assert rows["Old idea"].completed_at == NOW
        assert rows["Dropped plan"].status == "cancelled"
        assert rows["Dropped plan"].completed_at is None
        assert rows["Call Deepak re RDX pilot"].completed_at is None

    def test_owner_tags_notes_priority_and_due(self):
        rows = self._by_title()
        call = rows["Call Deepak re RDX pilot"]
        assert call.assignee_id == NAVANEETH["id"]
        assert call.tags == ["sales", "rdx"]
        assert call.priority == "high"
        assert call.due_on == date(2026, 10, 23)
        deck = rows["Review the deck"]
        assert deck.assignee_id == DEEPIKA["id"]
        assert deck.notes == "Draft v2 is in Drive"
        assert rows["Write the blog post"].priority == "normal"
        assert rows["Old idea"].assignee_id == VINITA["id"]

    def test_a_row_with_no_title_blocks_naming_its_row(self):
        plan = _plan("Task,Owner,Status\nOne,Navaneeth,\n,Navaneeth,Open\n")
        assert plan.blocking == ["Row 3: the task has no title."]


class TestTheReport:
    def test_the_report_keeps_no_cell_text_but_the_values_being_mapped(self):
        report = json.dumps(_plan().report())
        for text in ("Deepak", "invoice", "Draft v2", "venue", "3/11/2026", "sales"):
            assert text not in report
        assert "Vinitha" in report and "Someday" in report
        assert _plan().report()["ambiguous_dates"] == [{"row": 5, "read_as": "2026-11-03"}]
