"""The meetings job — spec 009 FR-CRMAI-MEET-01 to -08, FR-CRMAI-FLAG-03, -04,
FR-CRMAI-JOB-01; NFR-CRMAI-05; Q-10, Q-26, Q-41; spec §15 "Meetings":

- one tick writes the note on the right company and deal, as `sync`, with
  its items in the Review Queue;
- an internal-only or unmatched meeting is never downloaded;
- a meeting matched in two workspaces is processed in neither, and each
  counts it as skipped;
- a re-poll makes no second note and no second fetch;
- an updated transcript updates the note until a person edits it;
- phase 2 off: skipped, nothing fetched;
- phase 1 off and phase 2 on: the items are in the Review Queue and To
  review, and can be decided.

Every job runs on `tests/crm_fake.py` with `FakeClaude` and `FakeFireflies`
(`tests/crm_ai_fakes.py`): no network.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crm_ai_fakes import INVALID, FakeClaude, FakeFireflies, SlowCalls
from crm_fake import MEMBER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings as config
from app.services.crm import fireflies, jobs, meetings, notifications, store
from app.tasks import crm_ai

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
PRIYA = "priya@acme.in"
US = "deepika@nunnarilabs.com"
#: An answer as the model gives it. Deepika is number 1 on the team list
#: (Deepika, Owner, Tamil, by name).
ANSWER = {
    "summary": "Priya asked for a pilot plan.\nDeepika will send it by Friday.",
    "decisions": ["Start with two plants"],
    "action_items": [
        {"text": "Send the pilot plan", "owner": "1", "due_on": "2026-10-24"},
        {"text": "Share references", "owner": "unknown", "due_on": None},
    ],
    "next_step": "Send the pilot plan",
    "next_step_on": "2026-10-24",
}
EMPTY = {"meetings": 0, "notes": 0, "updated": 0, "items": 0}


class Calls:
    """Labs, phase 2 on with a budget; Acme (Deepika's) with Priya and a deal
    of Deepika's; Fireflies faked with no meeting yet."""

    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs, self.academy = two_workspaces(fake)
        fake.add_deal_lists(self.labs)
        fake.set_ai(self.labs, budget_usd=10, phases={"2": True})
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        monkeypatch.setattr(jobs, "_now", lambda: NOW)
        monkeypatch.setattr(notifications, "deliver", lambda message: ["email"])
        self.http = crm_client(monkeypatch, fake=fake)
        self.acme = self.post("companies", {"name": "Acme", "owner_id": MEMBER["id"]})
        r = self.http.patch(
            f"{API}/companies/{self.acme['id']}",
            params=LABS,
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        self.priya = self.post(
            "people",
            {"first_name": "Priya", "company_id": self.acme["id"], "primary_email": PRIYA},
        )
        self.pilot = self.deal("Acme pilot")
        self.fireflies = FakeFireflies().install(monkeypatch)
        self.claude = FakeClaude().install(monkeypatch)

    def post(self, plural, body, params=LABS) -> dict:
        r = self.http.post(f"{API}/{plural}", params=params, json=body, headers=as_(OWNER["id"]))
        assert r.status_code == 201, r.text
        return r.json()

    def deal(self, name) -> dict:
        return self.post(
            "opportunities",
            {
                "name": name,
                "company_id": self.acme["id"],
                "owner_id": MEMBER["id"],
                "next_step": "Follow up",
                "next_step_on": "2026-10-21",
            },
        )

    def answer(self, *answers) -> FakeClaude:
        self.claude = FakeClaude(*answers).install(self.monkeypatch)
        return self.claude

    def tick(self) -> dict:
        return crm_ai._meetings(NOW)["runs"]

    def rows(self, table: str, workspace: dict | None = None) -> list[dict]:
        ws = (workspace or self.labs)["id"]
        return [r for r in self.fake.rows[table].values() if r["workspace_id"] == ws]

    def notes(self, workspace: dict | None = None) -> list[dict]:
        return [n for n in self.rows("crm_notes", workspace) if n.get("source_ref")]

    def items(self, item_type: str | None = None, workspace: dict | None = None) -> list[dict]:
        return [
            r for r in self.rows("crm_review_items", workspace) if item_type in (None, r["type"])
        ]

    def get(self, path, who=OWNER, **params):
        r = self.http.get(f"{API}/{path}", params={**LABS, **params}, headers=as_(who["id"]))
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def calls(monkeypatch):
    return Calls(monkeypatch)


def _links(calls: Calls, note: dict) -> set[tuple[str, str]]:
    return {
        (lk["record_type"], lk["record_id"])
        for lk in calls.rows("crm_note_links")
        if lk["note_id"] == note["id"]
    }


class TestOneTick:
    """FR-CRMAI-MEET-04 to -06, NFR-CRMAI-05."""

    def test_job_the_note_is_on_the_right_company_and_deal(self, calls):
        calls.fireflies.meeting("t1", [PRIYA, US])
        calls.answer(ANSWER)
        runs = calls.tick()
        assert runs["labs"] == {
            "outcome": "ok",
            "counts": {
                "meetings": 1,
                "notes": 1,
                "updated": 0,
                "items": 3,
                "skipped_multi_workspace": 0,
                "failed_items": 0,
            },
        }
        assert runs["academy"]["outcome"] == "skipped"
        assert calls.fireflies.fetched == ["t1"]
        [note] = calls.notes()
        assert note["kind"] == "meeting"
        assert note["occurred_on"] == "2026-10-18"
        assert note["source_ref"] == "fireflies:t1"
        assert note["ai_provenance"] == {
            "source_kind": "meeting",
            "model": "claude-sonnet-5-5",
            "generated_at": NOW.isoformat(),
            "transcript_updated_at": "2026-10-18T09:30:00+00:00",
            "transcript_url": "https://app.fireflies.ai/view/t1",
        }
        assert "Send the pilot plan (owner: Deepika; due 2026-10-24)" in note["body"]
        assert note["body"].endswith("Transcript\nhttps://app.fireflies.ai/view/t1")
        assert _links(calls, note) == {
            ("company", calls.acme["id"]),
            ("person", calls.priya["id"]),
            ("opportunity", calls.pilot["id"]),
        }

    def test_job_the_note_is_written_by_sync(self, calls):
        calls.fireflies.meeting("t1", [PRIYA, US])
        calls.answer(ANSWER)
        calls.tick()
        [note] = calls.notes()
        [created] = [
            a
            for a in calls.fake.audit
            if a["target_table"] == "crm_notes" and a["target_id"] == note["id"]
        ]
        assert (created["action"], created["actor_label"], created["actor_id"]) == (
            "note.created",
            "sync",
            None,
        )

    def test_job_action_items_and_next_step_become_items(self, calls):
        calls.fireflies.meeting("t1", [PRIYA, US])
        calls.answer(ANSWER)
        calls.tick()
        [note] = calls.notes()
        [step] = calls.items("next_step_change")
        assert (step["record_type"], step["record_id"]) == ("opportunity", calls.pilot["id"])
        assert step["owner_id"] == MEMBER["id"]
        assert step["suggestion"] == {
            "next_step": "Send the pilot plan",
            "next_step_on": "2026-10-24",
        }
        tasks = calls.items("task_from_meeting")
        assert sorted(t["suggestion"]["title"] for t in tasks) == [
            "Send the pilot plan",
            "Share references",
        ]
        assert {(t["record_type"], t["record_id"]) for t in tasks} == {
            ("opportunity", calls.pilot["id"])
        }
        for item in [step, *tasks]:
            assert item["source_kind"] == "meeting"
            assert item["source_link"] == {"record_type": "note", "record_id": note["id"]}

    def test_job_two_open_deals_link_no_deal_and_ask_which(self, calls):
        rollout = calls.deal("Acme rollout")
        calls.fireflies.meeting("t1", [PRIYA])
        calls.answer(ANSWER)
        calls.tick()
        [note] = calls.notes()
        assert _links(calls, note) == {
            ("company", calls.acme["id"]),
            ("person", calls.priya["id"]),
        }
        [ask] = calls.items("deal_link")
        assert (ask["record_type"], ask["record_id"]) == ("note", note["id"])
        assert ask["owner_id"] == MEMBER["id"]
        choices = sorted(
            r["opportunity_id"]
            for r in calls.fake.rows["crm_review_item_deals"].values()
            if r["item_id"] == ask["id"]
        )
        assert choices == sorted([calls.pilot["id"], rollout["id"]])
        # No single deal: no next-step item, and the actions land on the company.
        assert calls.items("next_step_change") == []
        assert {(t["record_type"], t["record_id"]) for t in calls.items("task_from_meeting")} == {
            ("company", calls.acme["id"])
        }

    def test_job_the_meeting_note_reads_with_its_provenance(self, calls):
        calls.fireflies.meeting("t1", [PRIYA])
        calls.answer(ANSWER)
        calls.tick()
        [note] = calls.notes()
        shown = calls.get(f"notes/{note['id']}", who=MEMBER)["record"]
        assert shown["source_ref"] == "fireflies:t1"
        assert shown["ai_provenance"]["transcript_url"] == "https://app.fireflies.ai/view/t1"
        assert shown["ai"]["source_kind"] == "meeting"
        assert shown["ai"]["source"] == {"record_type": "note", "record_id": note["id"]}
        entries = calls.get(f"opportunities/{calls.pilot['id']}/timeline")
        [entry] = [e for e in entries if e["type"] == "note"]
        assert entry["ai"]["source_kind"] == "meeting"
        assert entry["ai"]["source"] == {"record_type": "note", "record_id": note["id"]}


class TestNeverDownloaded:
    """FR-CRMAI-MEET-02, -03; Q-10, Q-26."""

    def test_job_internal_only_and_unmatched_meetings_are_not_fetched(self, calls):
        calls.fireflies.meeting("internal", [US, "owner@nunnarilabs.com"])
        calls.fireflies.meeting("stranger", ["someone@unknown.in", US])
        claude = calls.answer()
        runs = calls.tick()
        assert runs["labs"]["counts"] == {
            **EMPTY,
            "skipped_multi_workspace": 0,
            "failed_items": 0,
        }
        assert calls.fireflies.fetched == []
        assert claude.calls == []
        assert calls.notes() == []
        # No person or company is made for an unknown attendee (Q-10).
        assert len(calls.rows("crm_people")) == 1
        assert len(calls.rows("crm_companies")) == 1

    def test_job_two_workspaces_nothing_in_either_skipped_in_each(self, calls):
        calls.fake.set_ai(calls.academy, budget_usd=10, phases={"2": True})
        academy = {"workspace": "academy"}
        beta = calls.post("companies", {"name": "Beta"}, params=academy)
        calls.post(
            "people",
            {"first_name": "Ravi", "company_id": beta["id"], "primary_email": "ravi@beta.in"},
            params=academy,
        )
        calls.fireflies.meeting("t1", [PRIYA, "ravi@beta.in"])
        claude = calls.answer()
        runs = calls.tick()
        for key in ("labs", "academy"):
            assert runs[key] == {
                "outcome": "ok",
                "counts": {**EMPTY, "skipped_multi_workspace": 1, "failed_items": 0},
            }
        assert calls.fireflies.fetched == []
        assert claude.calls == []
        assert calls.notes() == [] and calls.notes(calls.academy) == []
        assert calls.items() == [] and calls.items(workspace=calls.academy) == []


class TestIdempotent:
    """FR-CRMAI-MEET-07; P-5; Q-41."""

    def test_job_a_re_poll_makes_no_second_note(self, calls):
        calls.fireflies.meeting("t1", [PRIYA])
        calls.answer(ANSWER)
        calls.tick()
        assert calls.fireflies.cursor == "2026-10-18T09:30:00+00:00"
        before = copy.deepcopy(calls.fake.rows)
        # Even with the cursor lost, the noted version is not fetched again.
        calls.fireflies.cursor = None
        claude = calls.answer()
        runs = calls.tick()
        assert runs["labs"]["counts"]["notes"] == 0
        assert calls.fireflies.fetched == ["t1"]
        assert claude.calls == []
        after = copy.deepcopy(calls.fake.rows)
        assert {t: v for t, v in after.items() if t != "crm_job_runs"} == {
            t: v for t, v in before.items() if t != "crm_job_runs"
        }

    def test_job_an_updated_transcript_updates_the_note(self, calls):
        calls.fireflies.meeting("t1", [PRIYA])
        calls.answer(ANSWER)
        calls.tick()
        calls.fireflies.meeting("t1", [PRIYA], updated_at="2026-10-18T11:00:00+00:00")
        calls.answer({**ANSWER, "summary": "Priya confirmed the pilot."})
        runs = calls.tick()
        assert runs["labs"]["counts"]["updated"] == 1
        [note] = calls.notes()
        assert "Priya confirmed the pilot." in note["body"]
        assert note["ai_provenance"]["transcript_updated_at"] == "2026-10-18T11:00:00+00:00"
        # The same action items are not suggested twice.
        assert len(calls.items("task_from_meeting")) == 2

    def test_job_after_a_person_edits_the_note_ai_updates_stop(self, calls):
        calls.fireflies.meeting("t1", [PRIYA])
        calls.answer(ANSWER)
        calls.tick()
        [note] = calls.notes()
        r = calls.http.patch(
            f"{API}/notes/{note['id']}",
            params=LABS,
            json={"body": "My own words."},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200, r.text
        calls.fireflies.meeting("t1", [PRIYA], updated_at="2026-10-18T11:00:00+00:00")
        calls.answer({**ANSWER, "summary": "Priya confirmed the pilot."})
        runs = calls.tick()
        assert runs["labs"]["counts"]["updated"] == 0
        [note] = calls.notes()
        assert note["body"] == "My own words."
        assert note["ai_provenance"]["transcript_updated_at"] == "2026-10-18T09:30:00+00:00"

    def test_job_a_failed_meeting_is_counted_and_polled_again(self, calls):
        calls.fireflies.meeting("t1", [PRIYA])
        calls.fireflies.failing.add("t1")
        calls.answer()
        runs = calls.tick()
        assert runs["labs"]["outcome"] == "ok"
        assert runs["labs"]["counts"]["failed_items"] == 1
        assert calls.fireflies.cursor is None
        calls.fireflies.failing.clear()
        calls.answer(INVALID, INVALID)
        assert calls.tick()["labs"]["counts"]["failed_items"] == 1
        assert calls.notes() == []
        calls.answer(ANSWER)
        assert calls.tick()["labs"]["counts"]["notes"] == 1
        assert calls.fireflies.cursor == "2026-10-18T09:30:00+00:00"

    def test_job_a_failed_poll_fails_the_run_without_its_text(self, calls):
        calls.fireflies.down = True
        runs = calls.tick()
        assert runs["labs"]["outcome"] == "failed"
        [run] = [
            r
            for r in calls.rows("crm_job_runs")
            if r["job"] == "meetings" and r["outcome"] == "failed"
        ]
        assert "503" not in str(run.get("error") or "")

    def test_job_a_failed_match_fails_each_run_without_its_text(self, calls, monkeypatch):
        def down(ws_id, emails):
            raise RuntimeError(f"GET /crm_people?primary_email=in.({','.join(emails)})")

        monkeypatch.setattr(store, "people_by_emails", down)
        calls.fireflies.meeting("t1", [PRIYA])
        runs = calls.tick()
        assert runs["labs"]["outcome"] == "failed"
        [run] = [
            r
            for r in calls.rows("crm_job_runs")
            if r["job"] == "meetings" and r["outcome"] == "failed"
        ]
        assert PRIYA not in str(run.get("error") or "")
        assert calls.fireflies.fetched == []
        assert calls.fireflies.cursor is None


class TestPhaseSwitch:
    """FR-CRMAI-FLAG-03, -04; C5."""

    def test_job_phase_off_is_skipped_and_nothing_fetched(self, calls):
        calls.fake.set_ai(calls.labs, budget_usd=10, phases={"2": False})
        calls.fireflies.meeting("t1", [PRIYA])
        claude = calls.answer()
        runs = calls.tick()
        assert runs["labs"] == {"outcome": "skipped", "counts": {"skipped": "Switched off"}}
        assert calls.fireflies.listed == []
        assert calls.fireflies.fetched == []
        assert claude.calls == []
        assert calls.notes() == []

    def test_job_without_a_fireflies_key_is_skipped(self, calls):
        calls.fireflies.install(calls.monkeypatch, key=False)
        calls.fireflies.meeting("t1", [PRIYA])
        runs = calls.tick()
        assert runs["labs"] == {
            "outcome": "skipped",
            "counts": {"skipped": "Fireflies is not connected"},
        }
        assert calls.fireflies.listed == [] and calls.fireflies.fetched == []

    def test_job_phase_one_off_items_are_in_review_and_today_and_decidable(self, calls):
        calls.fireflies.meeting("t1", [PRIYA])
        calls.answer(ANSWER)
        calls.tick()
        queue = calls.get("review", who=MEMBER)
        assert {i["type"] for i in queue["items"]} == {"next_step_change", "task_from_meeting"}
        sections = calls.get("today", who=MEMBER)["sections"]
        [to_review] = [s for s in sections if s["key"] == "to_review"]
        assert to_review["count"] == 3
        step = next(i for i in queue["items"] if i["type"] == "next_step_change")
        r = calls.http.post(
            f"{API}/review/{step['id']}/approve",
            params=LABS,
            json={"version": step["version"]},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200, r.text
        deal = calls.fake.rows["crm_opportunities"][calls.pilot["id"]]
        assert (deal["next_step"], str(deal["next_step_on"])) == (
            "Send the pilot plan",
            "2026-10-24",
        )
        task = next(i for i in queue["items"] if i["type"] == "task_from_meeting")
        r = calls.http.post(
            f"{API}/review/{task['id']}/dismiss", params=LABS, headers=as_(MEMBER["id"])
        )
        assert r.status_code == 200, r.text
        assert [i["status"] for i in calls.items() if i["id"] in (step["id"], task["id"])] == [
            "approved",
            "dismissed",
        ]


class TestTheTaskTimeLimit:
    """FR-CRMAI-JOB-01 (amended in S5): each workspace fetches at most
    `meetings.BATCH` meetings a tick and none once its share of the task's
    time is spent; the rest are not finished, so the cursor waits for them;
    a budget stop keeps the run's counts."""

    def test_job_at_most_a_batch_of_meetings_a_tick(self, calls, monkeypatch):
        monkeypatch.setattr(meetings, "BATCH", 2)
        for n in (1, 2, 3):
            calls.fireflies.meeting(f"t{n}", [PRIYA], updated_at=f"2026-10-18T09:3{n}:00+00:00")
        calls.claude = FakeClaude(default=ANSWER).install(monkeypatch)
        runs = calls.tick()
        assert runs["labs"]["counts"]["notes"] == 2
        assert calls.fireflies.fetched == ["t1", "t2"]
        assert calls.fireflies.cursor == "2026-10-18T09:32:00+00:00"
        assert calls.tick()["labs"]["counts"]["notes"] == 1
        assert calls.fireflies.fetched == ["t1", "t2", "t3"]
        assert calls.fireflies.cursor == "2026-10-18T09:33:00+00:00"

    def test_job_out_of_time_the_rest_wait_and_the_cursor_holds(self, calls, monkeypatch):
        calls.fireflies.meeting("t1", [PRIYA], updated_at="2026-10-18T09:31:00+00:00")
        calls.fireflies.meeting("t2", [PRIYA], updated_at="2026-10-18T09:32:00+00:00")
        calls.answer(ANSWER, ANSWER)
        # Labs runs second, until the task's 400 s; each call takes 450 s.
        SlowCalls(450).install(monkeypatch)
        runs = calls.tick()
        assert runs["labs"]["outcome"] == "ok"
        assert runs["labs"]["counts"]["notes"] == 1
        assert runs["academy"]["outcome"] == "skipped"
        assert calls.fireflies.fetched == ["t1"]
        assert calls.fireflies.cursor == "2026-10-18T09:31:00+00:00"

    def test_job_a_budget_stop_part_way_keeps_the_counts(self, calls, monkeypatch):
        calls.fireflies.meeting("t1", [PRIYA], updated_at="2026-10-18T09:31:00+00:00")
        calls.fireflies.meeting("t2", [PRIYA], updated_at="2026-10-18T09:32:00+00:00")
        # The first call costs 18 USD, past the 1 USD budget.
        calls.fake.set_ai(calls.labs, budget_usd=1, phases={"2": True})
        calls.claude = FakeClaude(
            default=ANSWER, input_tokens=1_000_000, output_tokens=1_000_000
        ).install(monkeypatch)
        runs = calls.tick()
        assert runs["labs"] == {
            "outcome": "skipped",
            "counts": {
                "meetings": 2,
                "notes": 1,
                "updated": 0,
                "items": 3,
                "skipped_multi_workspace": 0,
                "failed_items": 0,
                "skipped": "AI budget reached",
            },
        }
        assert calls.fireflies.cursor == "2026-10-18T09:31:00+00:00"
        # The second transcript is not fetched once no call could be made.
        assert calls.fireflies.fetched == ["t1"]


class TestTheCallLog:
    """NFR-CRMAI-06 (amended in S5): a transcript fetch is logged with the
    workspace it is for."""

    def test_job_the_transcript_is_fetched_for_its_workspace(self, calls, monkeypatch):
        calls.fireflies.meeting("t1", [PRIYA, US])
        calls.answer(ANSWER)
        fetch = fireflies.transcript
        during: list[str] = []

        def transcript(transcript_id):
            during.append(fireflies._workspace.get())
            return fetch(transcript_id)

        monkeypatch.setattr(fireflies, "transcript", transcript)
        calls.tick()
        assert during == ["labs"]
        assert fireflies._workspace.get() == "-"
