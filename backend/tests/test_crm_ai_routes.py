"""Settings, AI and who sees cost — spec 009 contracts §2.1 `GET /crm/ai`,
§1 `GET /crm/bootstrap`; FR-CRMAI-LLM-08 to -10, FR-CRMAI-FLAG-03; spec §15
"Settings, AI shows month-to-date estimated spend … to that workspace's
Admins only"; D-1.

An Admin reads the month, the budget, spend in total and per job, whether
the budget is reached, the phases, the estimate note and whether the rate
table is set. A Member or Viewer is refused 403; a TVA admin who is not a
member, or anyone naming a workspace they are not in, 404. The job lists
and `/health/jobs` carry no token or cost key. Bootstrap carries the phases
for everyone and, for an Admin only, whether the budget is reached; never
the figure.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from crm_ai_fakes import FakeClaude, fix_clock, ist
from crm_fake import MEMBER, OUTSIDER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from pydantic import BaseModel

from app.api.crm import ai as ai_route
from app.api.crm import workspaces as workspace_route
from app.config import settings as config
from app.services.crm import ai, jobs, notifications

NOW = ist(2026, 10, 14, 12, 0)
LABS = "?workspace=labs"
COST_WORDS = ("cost", "token", "usd", "spend", "budget")


class Summary(BaseModel):
    summary: str


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.set_ai(labs, budget_usd=50, phases={"1": True, "3": True})
    http = crm_client(monkeypatch, fake=fake)
    monkeypatch.setattr(notifications, "deliver", lambda message: ["email"])
    monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
    monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
    fix_clock(monkeypatch, NOW, jobs, ai_route, workspace_route)
    return http, fake, labs, academy


def _spend(fake, ws, job, usd, at):
    fake.insert_ai_usage(
        ws["id"],
        {
            "job": job,
            "model": "claude-sonnet-5-5",
            "input_tokens": 100,
            "output_tokens": 10,
            "cost_usd": usd,
            "outcome": "ok",
            "at": at.isoformat(),
        },
    )


def _keys(value):
    """Every key in a JSON answer, at any depth."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


class TestAnAdminReadsIt:
    def test_ai_route_month_budget_spend_and_phases(self, crm):
        http, fake, labs, _ = crm
        _spend(fake, labs, "summaries", "1.250000", NOW - timedelta(days=2))
        _spend(fake, labs, "summaries", "0.500000", NOW - timedelta(days=1))
        _spend(fake, labs, "enrichment", "0.004000", NOW - timedelta(hours=1))
        # Last month's spend is not this month's.
        _spend(fake, labs, "summaries", "9.000000", ist(2026, 9, 30, 23, 59))
        r = http.get(f"/api/v1/crm/ai{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        assert r.json() == {
            "month": "2026-10",
            "budget_usd": "50.00",
            "spend": {
                "total_usd": "1.75",
                "by_job": {
                    "summaries": "1.75",
                    "meetings": "0.00",
                    "enrichment": "0.00",
                    "ai_fields": "0.00",
                    "followup_drafts": "0.00",
                    # Spec 010 contracts §1.5: email capture's discovery.
                    "capture": "0.00",
                },
            },
            "budget_reached": False,
            "phases": {"1": True, "2": False, "3": True, "4": False},
            "estimate_note": "Spend is an estimate from token counts.",
            "rates_configured": True,
        }

    def test_ai_route_budget_reached_and_rates_unset(self, crm, monkeypatch):
        http, fake, labs, _ = crm
        _spend(fake, labs, "summaries", "50.000000", NOW - timedelta(days=1))
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", None)
        got = http.get(f"/api/v1/crm/ai{LABS}", headers=as_(OWNER["id"])).json()
        assert got["budget_reached"] is True
        assert got["rates_configured"] is False

    @pytest.mark.parametrize("rate", ["0", "-3"])
    def test_ai_route_a_rate_of_zero_or_less_is_not_set(self, crm, monkeypatch, rate):
        http, *_ = crm
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal(rate))
        got = http.get(f"/api/v1/crm/ai{LABS}", headers=as_(OWNER["id"])).json()
        assert got["rates_configured"] is False

    def test_ai_route_a_workspace_without_the_row(self, crm):
        http, *_ = crm
        got = http.get("/api/v1/crm/ai?workspace=academy", headers=as_(OWNER["id"])).json()
        assert got["budget_usd"] == "0.00"
        assert got["phases"] == {"1": False, "2": False, "3": False, "4": False}
        # A zero budget is reached from the start: nothing runs (Q-02).
        assert got["budget_reached"] is True


class TestOnlyItsAdmins:
    """FR-CRMAI-LLM-10."""

    @pytest.mark.parametrize("who", [MEMBER, VIEWER, SELLER])
    def test_ai_route_members_and_viewers_are_refused(self, crm, who):
        http, *_ = crm
        r = http.get(f"/api/v1/crm/ai{LABS}", headers=as_(who["id"]))
        assert r.status_code == 403
        assert not any(w in r.text.lower() for w in ("usd", "spend", "1.75"))

    def test_ai_route_a_tva_admin_who_is_not_a_member(self, crm):
        http, *_ = crm
        assert http.get(f"/api/v1/crm/ai{LABS}", headers=as_(OUTSIDER["id"])).status_code == 404

    def test_ai_route_another_workspace(self, crm):
        http, fake, *_ = crm
        other = fake.add_workspace("third", "Third")
        fake.add_member(other, MEMBER["id"], "admin")
        r = http.get("/api/v1/crm/ai?workspace=third", headers=as_(SELLER["id"]))
        assert r.status_code == 404
        assert (
            http.get("/api/v1/crm/ai?workspace=academy", headers=as_(SELLER["id"])).status_code
            == 404
        )


class TestNoCostAnywhereElse:
    """FR-CRMAI-LLM-10 — job runs carry item counts only."""

    @pytest.fixture
    def ran(self, crm, monkeypatch):
        http, fake, labs, _ = crm
        FakeClaude({"summary": "Fine."}).install(monkeypatch)

        def work(ws, now):
            ai.call(ws, "summaries", "1", Summary, "Summarise.", [("email", "hi")])
            return {"summarised": 1}

        assert jobs.run("summaries", labs, work, NOW)["outcome"] == "ok"
        assert fake.rows["crm_ai_usage"]
        return http

    @pytest.mark.parametrize(
        ("path", "who"),
        [
            ("/api/v1/crm/jobs?workspace=labs", OWNER),
            ("/api/v1/crm/tva-admin/jobs", OUTSIDER),
            ("/health/jobs", None),
        ],
    )
    def test_ai_route_job_lists_carry_no_cost(self, ran, path, who):
        r = ran.get(path, headers=as_(who["id"]) if who else {})
        assert r.status_code == 200, r.text
        assert "summaries" in r.text
        keys = {k.lower() for k in _keys(r.json())}
        assert not [k for k in keys if any(w in k for w in COST_WORDS)]
        assert "0.0" not in r.text


class TestBootstrap:
    """Contracts §1 — phases for every role; budget reached for Admins only."""

    @pytest.mark.parametrize("who", [MEMBER, VIEWER, SELLER])
    def test_ai_route_bootstrap_phases_and_no_budget_for_non_admins(self, crm, who):
        http, *_ = crm
        got = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(who["id"])).json()
        # S1b: the Review Queue shows while a phase is on, never to a Viewer.
        assert got["ai"] == {
            "phases": {"1": True, "2": False, "3": True, "4": False},
            "review_visible": who is not VIEWER,
            "open_review_count": 0,
        }
        assert "ai_budget_reached" not in got

    def test_ai_route_bootstrap_an_admin_sees_whether_it_is_reached(self, crm):
        http, fake, labs, _ = crm
        got = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert got["ai_budget_reached"] is False
        _spend(fake, labs, "summaries", "50.000000", NOW - timedelta(hours=1))
        got = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert got["ai_budget_reached"] is True
        assert "budget_usd" not in str(got) and "50.00" not in str(got)
