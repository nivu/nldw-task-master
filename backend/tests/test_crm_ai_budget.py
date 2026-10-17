"""The budget and the gates before every call — spec 009 FR-CRMAI-LLM-06 to
-08, FR-CRMAI-FLAG-03; spec §15 (Foundations); Q-02, Q-03, Q-23; D-1.

Phase off or a zero budget: no call, and the run says why. A reached
budget: no call, `skipped` "AI budget reached", one notice and one message
per Admin per month. The month turns at 00:00 Asia/Kolkata. Overshoot is
the one call in flight. Work skipped for the budget is picked up by the
next tick once the budget is raised, once, because jobs work from what is
missing. Without the rate table nothing runs: "AI rates are not set".

S1a has no AI job yet, so these drive `ai.call` through `jobs.run` with a
small job of the same shape as the summaries job of S1c: it works from what
is pending and stores one summary and its items per thread.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from crm_ai_fakes import FakeClaude, ist
from crm_fake import OWNER, FakeCrmStore, two_workspaces
from crm_fake import client as crm_client
from pydantic import BaseModel

from app.config import settings as config
from app.domain.crm import ai_rules
from app.services.crm import ai, jobs, notifications

NOW = ist(2026, 10, 14, 12, 0)
ANSWER = {"summary": "They asked for the deck.", "asks": ["Send the deck"]}


class Summary(BaseModel):
    summary: str
    asks: list[str]


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.set_ai(labs, budget_usd=10, phases={"1": True})
    crm_client(monkeypatch, fake=fake)
    sent: list = []
    monkeypatch.setattr(notifications, "deliver", lambda message: sent.append(message) or ["email"])
    monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
    monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
    monkeypatch.setattr(jobs, "_now", lambda: NOW)
    return fake, labs, sent


class ThreadJob:
    """Pending threads in, one summary and one set of items out per thread."""

    def __init__(self, threads: list[str]) -> None:
        self.pending = list(threads)
        self.summaries: dict[str, dict] = {}
        self.items: dict[str, list[str]] = {}

    def __call__(self, ws, now):
        done = 0
        for thread in list(self.pending):
            out = ai.call(ws, "summaries", "1", Summary, "Summarise.", [("email", thread)], now)
            self.summaries[thread] = out.model_dump()
            self.items[thread] = list(out.asks)
            self.pending.remove(thread)
            done += 1
        return {"summarised": done}


def _spent(fake, labs, usd: str, at) -> None:
    fake.insert_ai_usage(
        labs["id"],
        {
            "job": "summaries",
            "model": ai_rules.CRM_AI_MODEL,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": usd,
            "outcome": "ok",
            "at": at.isoformat(),
        },
    )


def _budget_notices(fake):
    return [n for n in fake.rows["crm_notifications"].values() if n["kind"] == "ai_budget_reached"]


class TestNothingRunsWithoutLeave:
    """Spec §15: "With phase 1 off, or with a zero budget, no model call is
    made and the job run says why"."""

    def test_budget_phase_off_makes_no_call(self, crm, monkeypatch):
        fake, labs, _ = crm
        fake.set_ai(labs, budget_usd=10, phases={"1": False})
        claude = FakeClaude().install(monkeypatch)
        result = jobs.run("summaries", labs, ThreadJob(["t1"]), NOW)
        assert result == {"outcome": "skipped", "counts": {"skipped": "Switched off"}}
        assert claude.calls == []

    def test_budget_zero_makes_no_call(self, crm, monkeypatch):
        fake, labs, _ = crm
        fake.set_ai(labs, budget_usd=0, phases={"1": True})
        claude = FakeClaude().install(monkeypatch)
        result = jobs.run("summaries", labs, ThreadJob(["t1"]), NOW)
        assert result == {"outcome": "skipped", "counts": {"skipped": "AI budget reached"}}
        assert claude.calls == []

    def test_budget_rates_unset_makes_no_call(self, crm, monkeypatch):
        _, labs, _ = crm
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", None)
        claude = FakeClaude().install(monkeypatch)
        result = jobs.run("summaries", labs, ThreadJob(["t1"]), NOW)
        assert result == {"outcome": "skipped", "counts": {"skipped": "AI rates are not set"}}
        assert claude.calls == []


class TestTheBudgetIsReached:
    """FR-CRMAI-LLM-07, -08."""

    def test_budget_reached_skips_and_tells_the_admins_once(self, crm, monkeypatch):
        fake, labs, sent = crm
        _spent(fake, labs, "10.000000", NOW - timedelta(days=1))
        claude = FakeClaude().install(monkeypatch)
        for _ in range(3):
            result = jobs.run("summaries", labs, ThreadJob(["t1"]), NOW)
            assert result == {"outcome": "skipped", "counts": {"skipped": "AI budget reached"}}
        assert claude.calls == []
        [notice] = _budget_notices(fake)
        assert (notice["user_id"], notice["dedupe_key"]) == (
            OWNER["id"],
            f"ai_budget:{labs['id']}:2026-10",
        )
        assert notice["title"] == "AI budget reached in Nunnari Labs"
        assert len(sent) == 1 and sent[0].recipient_email == OWNER["email"]
        assert "October 2026" in sent[0].body
        assert "10" not in notice["title"] and "$" not in sent[0].body

    def test_budget_a_new_month_tells_them_again(self, crm, monkeypatch):
        fake, labs, sent = crm
        FakeClaude().install(monkeypatch)
        fake.set_ai(labs, budget_usd=0, phases={"1": True})
        jobs.run("summaries", labs, ThreadJob(["t1"]), NOW)
        jobs.run("summaries", labs, ThreadJob(["t1"]), ist(2026, 11, 2, 9, 0))
        assert sorted(n["dedupe_key"] for n in _budget_notices(fake)) == [
            f"ai_budget:{labs['id']}:2026-10",
            f"ai_budget:{labs['id']}:2026-11",
        ]
        assert len(sent) == 2

    def test_budget_overshoot_is_the_one_call_in_flight(self, crm, monkeypatch):
        fake, labs, _ = crm
        fake.set_ai(labs, budget_usd=1, phases={"1": True})
        _spent(fake, labs, "0.990000", NOW - timedelta(hours=1))
        # Each call costs 3 + 15 = 18 USD at a million tokens each way.
        claude = FakeClaude(
            default=ANSWER, input_tokens=1_000_000, output_tokens=1_000_000
        ).install(monkeypatch)
        job = ThreadJob(["t1", "t2", "t3"])
        result = jobs.run("summaries", labs, job, NOW)
        assert result["counts"] == {"skipped": "AI budget reached"}
        assert len(claude.calls) == 1
        assert list(job.summaries) == ["t1"]
        assert job.pending == ["t2", "t3"]


class TestTheMonthTurnsInKolkata:
    """Spec §15: a budget reached on the last day of an Asia/Kolkata month
    allows calls again at 00:00 IST on the first of the next."""

    def test_budget_2359_on_the_last_day_then_0000_on_the_first(self, crm, monkeypatch):
        fake, labs, _ = crm
        last = ist(2026, 10, 31, 23, 59)
        first = ist(2026, 11, 1, 0, 0)
        assert last.date().isoformat() == "2026-10-31" and first.date().isoformat() == "2026-10-31"
        _spent(fake, labs, "10.000000", ist(2026, 10, 31, 23, 0))
        claude = FakeClaude(default=ANSWER).install(monkeypatch)

        assert jobs.run("summaries", labs, ThreadJob(["t1"]), last)["counts"] == {
            "skipped": "AI budget reached"
        }
        assert claude.calls == []
        assert jobs.run("summaries", labs, ThreadJob(["t1"]), first)["outcome"] == "ok"
        assert len(claude.calls) == 1

    def test_budget_the_month_start_is_ist_midnight(self):
        assert ai_rules.ist_month_start(ist(2026, 10, 31, 23, 59)).isoformat() == (
            "2026-10-01T00:00:00+05:30"
        )
        assert ai_rules.ist_month_start(ist(2026, 11, 1, 0, 0)).isoformat() == (
            "2026-11-01T00:00:00+05:30"
        )


class TestResumingAfterTheBudgetIsRaised:
    """FR-CRMAI-LLM-08 — work skipped for the budget is picked up by the next
    tick after it is raised, once."""

    def test_budget_raised_resumes_without_duplicates(self, crm, monkeypatch):
        fake, labs, _ = crm
        _spent(fake, labs, "10.000000", NOW - timedelta(days=1))
        claude = FakeClaude(ANSWER).install(monkeypatch)
        job = ThreadJob(["t1"])

        assert jobs.run("summaries", labs, job, NOW)["counts"] == {"skipped": "AI budget reached"}
        assert job.pending == ["t1"] and job.summaries == {}

        fake.set_ai(labs, budget_usd=50, phases={"1": True})
        assert jobs.run("summaries", labs, job, NOW) == {
            "outcome": "ok",
            "counts": {"summarised": 1},
        }
        assert job.summaries == {"t1": ANSWER} and job.items == {"t1": ["Send the deck"]}

        assert jobs.run("summaries", labs, job, NOW) == {
            "outcome": "ok",
            "counts": {"summarised": 0},
        }
        assert len(claude.calls) == 1
        assert len(job.summaries) == 1 and len(job.items["t1"]) == 1


class TestTheUsageLog:
    """FR-CRMAI-LLM-06 — one row per call, cost from the rate table."""

    def test_budget_each_call_is_logged_with_its_estimated_cost(self, crm, monkeypatch):
        fake, labs, _ = crm
        FakeClaude(ANSWER, input_tokens=2000, output_tokens=400).install(monkeypatch)
        jobs.run("summaries", labs, ThreadJob(["t1"]), NOW)
        [row] = fake.rows["crm_ai_usage"].values()
        assert (row["input_tokens"], row["output_tokens"], row["outcome"]) == (2000, 400, "ok")
        # 2000 × 3 / 1M + 400 × 15 / 1M
        assert Decimal(row["cost_usd"]) == Decimal("0.012000")
        assert fake.ai_spend(labs["id"], ai_rules.ist_month_start(NOW))["total_usd"] == "0.012000"

    def test_budget_estimate_cost_rounds_to_six_places(self):
        assert ai_rules.estimate_cost(1, 1, Decimal("3"), Decimal("15")) == Decimal("0.000018")
        assert ai_rules.estimate_cost(0, 0, Decimal("3"), Decimal("15")) == Decimal("0.000000")
