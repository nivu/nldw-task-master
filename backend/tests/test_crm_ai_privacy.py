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
(`ignore_result`).
"""

from __future__ import annotations

import json
import logging

import pytest
from crm_ai_fakes import INVALID, CapturedCelery
from test_crm_summaries_job import ANSWER, Mail

from app.tasks import crm_ai

QUOTED = "ZQXQUOTEDHISTORY"
SIGNED = "ZQXSIGNATURE"
INTERNAL = "ZQXINTERNALONLY"
MARKERS = (QUOTED, SIGNED, INTERNAL)

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


def _everything(mail: Mail, caplog, result) -> str:
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
