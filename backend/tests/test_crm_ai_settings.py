"""The `ai` setting — spec 009 FR-CRMAI-FLAG-01 to -03, FR-CRMAI-LLM-07;
contracts §1 `PATCH /crm/settings/ai`; Q-02, Q-04.

A budget from 0 to 10000 USD with at most two decimals and an on/off for
each of the four phases, unknown or missing keys refused; a workspace
without the row reads every phase off; a phase switched off stops its jobs
at the next run, recorded `skipped` "Switched off".
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crm_ai_fakes import FakeClaude
from crm_fake import MEMBER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from pydantic import BaseModel

from app.config import settings as config
from app.domain.crm import ai_rules
from app.domain.crm import settings as rules
from app.services.crm import ai, jobs

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
LABS = "?workspace=labs"
ON = {"1": True, "2": False, "3": False, "4": False}
OFF = {"1": False, "2": False, "3": False, "4": False}


class Answer(BaseModel):
    summary: str


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.set_ai(labs)
    http = crm_client(monkeypatch, fake=fake)
    monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
    monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
    monkeypatch.setattr(jobs, "_now", lambda: NOW)
    return http, fake, labs, academy


def _patch(http, value, who=OWNER):
    return http.patch(
        f"/api/v1/crm/settings/ai{LABS}", json={"value": value}, headers=as_(who["id"])
    )


class TestTheValidator:
    """FR-CRMAI-FLAG-01 — `VALIDATORS["ai"]`."""

    @pytest.mark.parametrize("budget", [0, 10000, 12.5, 49.99, Decimal("7.10")])
    def test_ai_settings_accept_a_budget_in_range(self, budget):
        assert rules.setting_refusal("ai", {"budget_usd": budget, "phases": OFF}) is None

    @pytest.mark.parametrize("budget", [-1, 10000.01, 12.345, True, "50", None, float("nan")])
    def test_ai_settings_refuse_a_budget_out_of_range(self, budget):
        assert (
            rules.setting_refusal("ai", {"budget_usd": budget, "phases": OFF})
            == "The AI budget must be a number of US dollars from 0 to 10000, with at most two decimals."
        )

    @pytest.mark.parametrize(
        "phases",
        [
            {"1": True, "2": False, "3": False},
            {**OFF, "5": True},
            {**OFF, "1": "yes"},
            {**OFF, "2": 1},
            [True, False, False, False],
        ],
    )
    def test_ai_settings_refuse_phases_that_are_not_the_four_switches(self, phases):
        assert (
            rules.setting_refusal("ai", {"budget_usd": 10, "phases": phases})
            == "Phases are 1, 2, 3 and 4, each on (true) or off (false)."
        )

    def test_ai_settings_refuse_unknown_and_missing_keys(self):
        assert (
            rules.setting_refusal("ai", {"budget_usd": 1, "phases": OFF, "model": "x"})
            == "Unknown AI setting: model."
        )
        assert rules.setting_refusal("ai", {"phases": OFF}) == "The AI setting needs budget_usd."
        assert (
            rules.setting_refusal("ai", [])
            == "The AI setting must give a budget and the four phases."
        )


class TestSavingIt:
    def test_ai_settings_an_admin_saves_a_budget_and_a_phase(self, crm):
        http, fake, labs, _ = crm
        r = _patch(http, {"budget_usd": 50, "phases": ON})
        assert r.status_code == 200, r.text
        assert fake.ai_setting(labs["id"]) == {"budget_usd": 50, "phases": ON}
        actor, ops = fake.applied[-1]
        assert (actor["user_id"], ops[0]["action"]) == (OWNER["id"], "setting.updated")

    def test_ai_settings_a_refusal_is_one_sentence(self, crm):
        http, *_ = crm
        r = _patch(http, {"budget_usd": 20000, "phases": ON})
        assert r.status_code == 422
        assert "10000" in r.json()["detail"]

    def test_ai_settings_a_member_cannot(self, crm):
        http, *_ = crm
        assert _patch(http, {"budget_usd": 50, "phases": ON}, who=MEMBER).status_code == 403


class TestAWorkspaceWithoutTheRow:
    """FR-CRMAI-FLAG-02 — a missing row, or a missing key, reads off."""

    def test_ai_settings_no_row_reads_every_phase_off(self, crm):
        http, fake, _, academy = crm
        assert fake.ai_setting(academy["id"]) is None
        assert ai_rules.phases_of(None) == OFF
        assert ai_rules.budget_of(None) == Decimal("0.00")
        assert not ai_rules.phase_on({"budget_usd": 5, "phases": {}}, "1")
        r = http.get("/api/v1/crm/bootstrap?workspace=academy", headers=as_(OWNER["id"]))
        assert r.json()["ai"]["phases"] == OFF

    def test_ai_settings_no_row_runs_nothing(self, crm, monkeypatch):
        _, fake, _, academy = crm
        claude = FakeClaude().install(monkeypatch)

        def work(ws, now):
            ai.call(ws, "summaries", "1", Answer, "Summarise.", [("email", "hi")], now)

        result = jobs.run("summaries", academy, work, NOW)
        assert result == {"outcome": "skipped", "counts": {"skipped": "Switched off"}}
        assert claude.calls == []


class TestSwitchingAPhaseOff:
    """FR-CRMAI-FLAG-03 — the next run is skipped "Switched off"."""

    def test_ai_settings_switching_off_records_switched_off(self, crm, monkeypatch):
        http, fake, labs, _ = crm
        claude = FakeClaude(default={"summary": "Fine."}).install(monkeypatch)

        def work(ws, now):
            ai.call(ws, "summaries", "1", Answer, "Summarise.", [("email", "hi")], now)
            return {"done": 1}

        _patch(http, {"budget_usd": 50, "phases": ON})
        assert jobs.run("summaries", labs, work, NOW)["outcome"] == "ok"
        assert len(claude.calls) == 1

        _patch(http, {"budget_usd": 50, "phases": OFF})
        assert jobs.run("summaries", labs, work, NOW) == {
            "outcome": "skipped",
            "counts": {"skipped": "Switched off"},
        }
        assert len(claude.calls) == 1
        latest = list(fake.rows["crm_job_runs"].values())[-1]
        assert latest["outcome"] == "skipped"

    def test_ai_settings_another_phase_does_not_count(self, crm, monkeypatch):
        http, _, labs, _ = crm
        claude = FakeClaude().install(monkeypatch)
        _patch(http, {"budget_usd": 50, "phases": {**OFF, "2": True}})

        def work(ws, now):
            ai.call(ws, "summaries", "1", Answer, "Summarise.", [("email", "hi")], now)

        assert jobs.run("summaries", labs, work, NOW)["counts"] == {"skipped": "Switched off"}
        assert claude.calls == []
