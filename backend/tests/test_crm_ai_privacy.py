"""No source text at rest — spec 009 NFR-CRMAI-01, P-3; FR-CRMAI-CHG-01,
FR-CRMAI-SUM-03; spec §15 "The privacy test finds no marker in any column,
log, queue payload or result key, including after invalid model output".

Grown slice by slice (plan §8): S1c seeds a unique marker in an email's
quoted reply history, in its signature, and in an internal-only message on
the same thread, never in the part the model is asked to paraphrase. It runs
the summaries job, on success and when the model's output is invalid twice,
and then finds no marker in the prompts, in any row the fake database holds
(every table, the audit log and the job log included), in any log line, in
any Celery payload, or in the task's result, which is never stored
(`ignore_result`). S2a adds a marker in a meeting's transcript and in
Fireflies' own summary of it, and runs the meetings job the same two ways.
S2b adds a marker in a company's web pages (its home page and its about
page), and runs the enrichment job the same two ways, its batches sent
through the real enqueue path so their Celery payloads are inspected too.
S3b adds a marker in the body of the outbound email R-1 follows up: the
draft task's Celery payload holds ids only, no body is read, and a draft
that fails with a marker in its exception leaves only the fixed sentence.
"""

from __future__ import annotations

import json
import logging

import pytest
from crm_ai_fakes import INVALID, CapturedCelery, FakeClaude
from test_crm_enrichment_job import ANSWER as ENRICHED
from test_crm_enrichment_job import Sites
from test_crm_followup_rules import DRAFT, Rules
from test_crm_meetings_job import ANSWER as MEETING_ANSWER
from test_crm_meetings_job import PRIYA, Calls
from test_crm_summaries_job import ANSWER, Mail

from app.services.crm import ai, enrichment
from app.tasks import crm_ai

QUOTED = "ZQXQUOTEDHISTORY"
SIGNED = "ZQXSIGNATURE"
INTERNAL = "ZQXINTERNALONLY"
MARKERS = (QUOTED, SIGNED, INTERNAL)
TRANSCRIPT = "ZQXTRANSCRIPT"
FIREFLIES_SUMMARY = "ZQXFIREFLIESSUMMARY"
WEB_PAGE = "ZQXWEBPAGE"
WEB_ABOUT = "ZQXWEBABOUT"

EMAIL = (
    "Could you send the pilot plan by Friday?\n\n"
    f"Best regards,\nPriya Rao {SIGNED}\nAcme\n\n"
    "On Mon, 12 Oct 2026 at 10:00, Deepika <deepika@nunnarilabs.com> wrote:\n"
    f"> Our earlier proposal {QUOTED} stands."
)


@pytest.fixture
def mail(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    mail = Mail(monkeypatch)
    mail.celery = CapturedCelery().install(monkeypatch)
    mail.message("g1", EMAIL)
    mail.message(
        "g2",
        f"Internal: {INTERNAL} do not share.",
        sent_at="2026-10-16T10:00:00+00:00",
        direction="outbound",
        is_internal=True,
    )
    return mail


def _everything(mail: Mail | Calls | Sites, caplog, result) -> str:
    return "\n".join(
        [
            json.dumps(mail.fake.rows, default=str),
            json.dumps(mail.fake.audit, default=str),
            caplog.text,
            "\n".join(r.getMessage() for r in caplog.records),
            json.dumps(mail.celery.sent, default=str),
            json.dumps(result, default=str),
        ]
    )


class TestEmail:
    def test_privacy_no_marker_reaches_the_model(self, mail):
        claude = mail.answer(ANSWER)
        crm_ai.summarise_threads()
        [prompt] = claude.prompts
        assert "Could you send the pilot plan by Friday?" in prompt
        for marker in MARKERS:
            assert marker not in prompt, marker

    @pytest.mark.parametrize("answers", [(ANSWER,), (INVALID, INVALID)], ids=["ok", "invalid"])
    def test_privacy_no_marker_in_any_column_log_payload_or_result(self, mail, caplog, answers):
        mail.answer(*answers)
        result = crm_ai._summaries()
        counts = result["runs"]["labs"]["counts"]
        assert counts["summarised" if len(answers) == 1 else "failed_items"] == 1, counts
        found = _everything(mail, caplog, result)
        for marker in MARKERS:
            assert marker not in found, marker
        assert "Could you send the pilot plan" not in found

    def test_privacy_the_task_stores_no_result_and_takes_no_text(self, mail):
        assert crm_ai.summarise_threads.ignore_result is True
        mail.answer(ANSWER)
        crm_ai.summarise_threads()
        assert mail.celery.sent == []


@pytest.fixture
def calls(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    calls = Calls(monkeypatch)
    calls.celery = CapturedCelery().install(monkeypatch)
    calls.fireflies.meeting(
        "t1",
        [PRIYA],
        sentences=[("Priya", f"Our budget code is {TRANSCRIPT}, please keep it.")],
        overview=f"The team discussed {FIREFLIES_SUMMARY}.",
    )
    return calls


class TestMeeting:
    def test_privacy_the_transcript_reaches_the_model_only(self, calls):
        claude = calls.answer(MEETING_ANSWER)
        crm_ai.capture_meetings()
        [prompt] = claude.prompts
        assert TRANSCRIPT in prompt and FIREFLIES_SUMMARY in prompt

    @pytest.mark.parametrize(
        "answers", [(MEETING_ANSWER,), (INVALID, INVALID)], ids=["ok", "invalid"]
    )
    def test_privacy_no_transcript_marker_in_any_column_log_payload_or_result(
        self, calls, caplog, answers
    ):
        calls.answer(*answers)
        result = crm_ai._meetings()
        counts = result["runs"]["labs"]["counts"]
        assert counts["notes" if len(answers) == 1 else "failed_items"] == 1, counts
        found = _everything(calls, caplog, result)
        for marker in (TRANSCRIPT, FIREFLIES_SUMMARY):
            assert marker not in found, marker

    def test_privacy_the_meetings_task_stores_no_result_and_takes_no_text(self, calls):
        assert crm_ai.capture_meetings.ignore_result is True
        calls.answer(MEETING_ANSWER)
        crm_ai.capture_meetings()
        assert calls.celery.sent == []


@pytest.fixture
def sites(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    real_enqueue = enrichment.enqueue
    sites = Sites(monkeypatch)
    # The batches go through the real enqueue, so their payloads are seen.
    monkeypatch.setattr(enrichment, "enqueue", real_enqueue)
    sites.celery = CapturedCelery().install(monkeypatch)
    sites.company("Acme", "https://acme.in")
    sites.site(
        home=f"<html><body><p>Acme makes machine tools. Code {WEB_PAGE}.</p>"
        '<a href="/about">About</a></body></html>',
        about=f"<html><body><p>Founded by {WEB_ABOUT} in 1998.</p></body></html>",
    )
    return sites


def _enrich(sites: Sites) -> dict:
    """The tick, then every batch it and its batches sent."""
    result = crm_ai._enrichment()
    done = 0
    while done < len(sites.celery.named("crm_ai.enrichment_batch")):
        [args] = sites.celery.named("crm_ai.enrichment_batch")[done]["args"]
        crm_ai.enrichment_batch(args)
        done += 1
    return result


class TestWebPage:
    def test_privacy_the_web_pages_reach_the_model_only(self, sites):
        claude = sites.answer(ENRICHED)
        _enrich(sites)
        [prompt] = claude.prompts
        assert WEB_PAGE in prompt and WEB_ABOUT in prompt

    @pytest.mark.parametrize("answers", [(ENRICHED,), (INVALID, INVALID)], ids=["ok", "invalid"])
    def test_privacy_no_web_page_marker_in_any_column_log_payload_or_result(
        self, sites, caplog, answers
    ):
        sites.answer(*answers)
        result = _enrich(sites)
        run = sites.last_run()
        assert run["counts"]["enriched" if len(answers) == 1 else "failed"] == 1, run
        assert sites.celery.named("crm_ai.enrichment_batch")
        found = _everything(sites, caplog, result)
        for marker in (WEB_PAGE, WEB_ABOUT):
            assert marker not in found, marker
        assert "machine tools. Code" not in found

    def test_privacy_the_enrichment_tasks_store_no_result_and_take_no_text(self, sites):
        assert crm_ai.enrich_companies.ignore_result is True
        assert crm_ai.enrichment_batch.ignore_result is True
        sites.answer(ENRICHED)
        _enrich(sites)
        for sent in sites.celery.sent:
            [args] = sent["args"]
            assert set(args) == {"workspace_id", "run_id", "cutoff", "lock", "counts"}


# ---------------------------------------------------------------------------
# S3b — the R-1 draft (FR-CRMAI-RULE-02, FR-CRMAI-JOB-02, -03)
# ---------------------------------------------------------------------------

OUTREACH = "ZQXOUTREACHBODY"
FAILURE = "ZQXFAILURETEXT"


@pytest.fixture
def rules(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    made = Rules(monkeypatch)
    made.message("g1")
    made.gmail.texts["g1"] = f"Here is the pilot plan. {OUTREACH}"
    made.summary()
    return made


class TestFollowUpDraft:
    def test_privacy_the_draft_task_takes_ids_and_reads_no_body(self, rules, caplog):
        assert crm_ai.followup_draft.ignore_result is True
        rules.automations()
        [queued] = rules.celery.named("crm_ai.followup_draft")
        [args] = queued["args"]
        assert set(args) == {"workspace_id", "messages"}
        assert [set(m) for m in args["messages"]] == [
            {"mailbox_id", "thread_id", "gmail_message_id"}
        ]
        rules.claude = FakeClaude(DRAFT).install(rules.monkeypatch)
        result = rules.drafts()
        assert rules.gmail.reads == []
        assert OUTREACH not in "\n".join(rules.claude.prompts)
        assert OUTREACH not in _everything(rules, caplog, result)

    def test_privacy_a_failed_draft_leaves_only_the_fixed_sentence(self, rules, caplog):
        rules.automations()
        rules.claude = FakeClaude(RuntimeError(f"boom {FAILURE}")).install(rules.monkeypatch)
        result = rules.drafts()
        assert result["counts"]["failed_items"] == 1
        [run] = rules.runs("followup_drafts")
        assert run["outcome"] == "ok" and run.get("error") is None
        sentence = str(ai.SafeError("AI call", "RuntimeError"))
        assert sentence in caplog.text
        found = _everything(rules, caplog, result)
        assert FAILURE not in found and OUTREACH not in found
