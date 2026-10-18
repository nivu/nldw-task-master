"""The Claude client and the keys — spec 009 FR-CRMAI-LLM-01 to -03, -06,
NFR-CRMAI-04, -06, -08; spec §15 "No Claude, Fireflies or Gmail key reaches
the browser or a log line"; C4, Q-42.

Raw HTTP on `httpx`, driven here through `httpx.MockTransport`: the request
carries the key, the API version, the model and the job's schema as the
structured-output format; the timeouts are 5 s to connect and 60 s to read;
usage is read from the answer; no log line or exception carries the key or
a body. The model id goes with every output into the usage log (LLM-02).
No route response or page payload carries a key.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.config import settings as config
from app.domain.crm import ai_rules
from app.services.crm import ai, claude, jobs

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
KEY = "sk-ant-api03-ZQXKEYMARKER"
FIREFLIES = "ff-ZQXFIREFLIESMARKER"
GMAIL = '{"client_email": "sync@x.iam", "private_key": "ZQXGMAILMARKER"}'
SECRET_BODY = "ZQXBODYMARKER Acme <priya@acme.com>"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=600)
    asks: list[str] = Field(max_length=10)


def _ok(data, *, input_tokens=1234, output_tokens=56, model="claude-sonnet-5-5"):
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": json.dumps(data)},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


@pytest.fixture
def transport(monkeypatch):
    """`claude._http` on a mock transport; `seen` holds every request."""
    seen: list[httpx.Request] = []
    answers: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(
        claude, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", SecretStr(KEY))
    return seen, answers


def _call(**overrides):
    kwargs = {
        "model": ai_rules.CRM_AI_MODEL,
        "system": "Rules.",
        "user": "<data>hi</data>",
        "schema": Answer.model_json_schema(),
        "job": "summaries",
        "workspace": "labs",
    }
    return claude.messages(**{**kwargs, **overrides})


class TestTheRequest:
    def test_client_request_shape(self, transport):
        seen, answers = transport
        answers.append(_ok({"summary": "Fine.", "asks": []}))
        _call()
        [request] = seen
        assert (request.method, str(request.url)) == (
            "POST",
            "https://api.anthropic.com/v1/messages",
        )
        assert request.headers["x-api-key"] == KEY
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["content-type"] == "application/json"
        body = json.loads(request.content)
        assert body["model"] == "claude-sonnet-5-5"
        assert body["system"] == "Rules."
        assert body["messages"] == [{"role": "user", "content": "<data>hi</data>"}]
        # claude-sonnet-5-5 refuses a forced tool call: structured output instead.
        assert "tool_choice" not in body and "tools" not in body
        fmt = body["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["schema"]["additionalProperties"] is False
        assert fmt["schema"]["required"] == ["summary", "asks"]
        assert "maxLength" not in json.dumps(fmt) and "maxItems" not in json.dumps(fmt)

    def test_client_timeouts_are_five_and_sixty_seconds(self, transport):
        seen, answers = transport
        answers.append(_ok({"summary": "Fine.", "asks": []}))
        _call()
        timeout = seen[0].extensions["timeout"]
        assert (timeout["connect"], timeout["read"]) == (5.0, 60.0)

    def test_client_usage_and_answer_are_parsed(self, transport):
        _, answers = transport
        answers.append(
            _ok({"summary": "Fine.", "asks": ["Deck"]}, input_tokens=10, output_tokens=3)
        )
        reply = _call()
        assert reply == claude.Reply(
            data={"summary": "Fine.", "asks": ["Deck"]},
            model="claude-sonnet-5-5",
            input_tokens=10,
            output_tokens=3,
        )

    def test_client_an_answer_that_is_not_json_is_none(self, transport):
        _, answers = transport
        answers.append(
            httpx.Response(
                200,
                json={
                    "model": "claude-sonnet-5-5",
                    "content": [{"type": "text", "text": "Sure! Here it is"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        )
        assert _call().data is None

    def test_client_refuses_without_a_key(self, transport, monkeypatch):
        seen, _ = transport
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
        with pytest.raises(claude.NotConfigured):
            _call()
        assert seen == []


class TestTheKeyNeverShows:
    """NFR-CRMAI-04, -06 — logs carry the status, latency and tokens only."""

    @pytest.mark.parametrize(
        ("answer", "error"),
        [
            (httpx.Response(500, text=f"{SECRET_BODY} {KEY}"), claude.ClaudeError),
            (
                httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}),
                claude.ClaudeError,
            ),
            (httpx.ReadTimeout(f"timed out {KEY}"), claude.ClaudeTimeout),
            (httpx.ConnectError(f"refused {SECRET_BODY}"), claude.ClaudeError),
        ],
    )
    def test_client_failures_name_no_key_and_no_body(self, transport, caplog, answer, error):
        _, answers = transport
        answers.append(answer)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(error) as raised:
            _call()
        text = (
            f"{raised.value} {raised.value!r} {raised.value.__cause__} {raised.value.__context__}"
        )
        assert KEY not in text and "ZQXBODYMARKER" not in text
        assert raised.value.__context__ is None and raised.value.__cause__ is None
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert KEY not in logged and "ZQXBODYMARKER" not in logged

    def test_client_the_log_line_has_status_and_tokens(self, transport, caplog):
        _, answers = transport
        answers.append(_ok({"summary": SECRET_BODY, "asks": []}, input_tokens=7, output_tokens=2))
        caplog.set_level(logging.INFO)
        _call(user=f"<data>{SECRET_BODY}</data>")
        [line] = [r.getMessage() for r in caplog.records if "crm_claude_call" in r.getMessage()]
        event = json.loads(line)
        assert event["status"] == "200" and event["input_tokens"] == 7
        assert event["job"] == "summaries" and event["workspace"] == "labs"
        assert "ZQXBODYMARKER" not in line and KEY not in line

    def test_client_a_declined_call_is_a_failure(self, transport):
        _, answers = transport
        answers.append(
            httpx.Response(
                200,
                json={
                    "model": "claude-sonnet-5-5",
                    "content": [],
                    "stop_reason": "refusal",
                    "usage": {"input_tokens": 9, "output_tokens": 0},
                },
            )
        )
        with pytest.raises(claude.ClaudeError) as raised:
            _call()
        assert raised.value.input_tokens == 9


class TestAnUnansweredCallIsCounted:
    """FR-CRMAI-LLM-06, -07 — a call sent but not answered (a read timeout,
    a broken connection) may be billed in full, so it carries an upper
    bound of its tokens; one never sent carries none."""

    @pytest.mark.parametrize(
        ("failure", "error"),
        [
            (httpx.ReadTimeout("timed out"), claude.ClaudeTimeout),
            (httpx.RemoteProtocolError("closed"), claude.ClaudeError),
        ],
    )
    def test_client_a_sent_call_carries_an_upper_bound(self, transport, failure, error):
        _, answers = transport
        answers.append(failure)
        user = "<data>" + "word " * 2000 + "</data>"
        with pytest.raises(error) as raised:
            _call(user=user)
        assert raised.value.input_tokens >= len(user)
        assert raised.value.output_tokens == claude.MAX_TOKENS

    @pytest.mark.parametrize(
        "failure", [httpx.ConnectError("refused"), httpx.ConnectTimeout("no route")]
    )
    def test_client_a_call_never_sent_carries_none(self, transport, failure):
        _, answers = transport
        answers.append(failure)
        with pytest.raises(claude.ClaudeError) as raised:
            _call()
        assert (raised.value.input_tokens, raised.value.output_tokens) == (0, 0)

    def test_client_a_timed_out_call_counts_towards_the_budget(self, transport, monkeypatch):
        _, answers = transport
        fake = FakeCrmStore().install(monkeypatch)
        labs, _ = two_workspaces(fake)
        fake.set_ai(labs, budget_usd=10, phases={"1": True})
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        answers.append(httpx.ReadTimeout("timed out"))
        with pytest.raises(ai.AiFailed):
            ai.call(labs, "summaries", "1", Answer, "Summarise.", [("email", "hi")])
        [row] = fake.rows["crm_ai_usage"].values()
        assert row["outcome"] == "timed_out"
        assert row["output_tokens"] == claude.MAX_TOKENS
        # 16,000 output tokens at 15 USD a million: at least 0.24 USD.
        assert Decimal(row["cost_usd"]) >= Decimal("0.24")


class TestTheModelIdGoesWithEveryOutput:
    """FR-CRMAI-LLM-02 — through the wrapper, on the real client."""

    def test_client_the_usage_row_names_the_model(self, transport, monkeypatch):
        _, answers = transport
        fake = FakeCrmStore().install(monkeypatch)
        labs, _ = two_workspaces(fake)
        fake.set_ai(labs, budget_usd=10, phases={"1": True})
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        answers.append(_ok({"summary": "Fine.", "asks": []}))
        out = ai.call(labs, "summaries", "1", Answer, "Summarise.", [("email", "hi")])
        assert out == Answer(summary="Fine.", asks=[])
        [row] = fake.rows["crm_ai_usage"].values()
        assert row["model"] == ai_rules.CRM_AI_MODEL == "claude-sonnet-5-5"


class TestNoKeyReachesTheBrowser:
    """NFR-CRMAI-04, spec §15 — bootstrap, settings and Settings, AI."""

    @pytest.fixture
    def crm(self, monkeypatch):
        fake = FakeCrmStore().install(monkeypatch)
        labs, _ = two_workspaces(fake)
        fake.set_ai(labs, budget_usd=10, phases={"1": True})
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", SecretStr(KEY))
        monkeypatch.setattr(config, "FIREFLIES_API_KEY", SecretStr(FIREFLIES))
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", SecretStr(GMAIL))
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        monkeypatch.setattr(jobs, "_now", lambda: NOW)
        return crm_client(monkeypatch, fake=fake)

    @pytest.mark.parametrize(
        ("path", "who"),
        [
            ("/api/v1/crm/bootstrap", OWNER),
            ("/api/v1/crm/bootstrap", MEMBER),
            ("/api/v1/crm/bootstrap", VIEWER),
            ("/api/v1/crm/settings", OWNER),
            ("/api/v1/crm/ai", OWNER),
            ("/api/v1/crm/jobs", OWNER),
        ],
    )
    def test_client_no_key_in_a_route_response(self, crm, path, who):
        r = crm.get(f"{path}?workspace=labs", headers=as_(who["id"]))
        assert r.status_code == 200, r.text
        for marker in ("ZQXKEYMARKER", "ZQXFIREFLIESMARKER", "ZQXGMAILMARKER"):
            assert marker not in r.text
