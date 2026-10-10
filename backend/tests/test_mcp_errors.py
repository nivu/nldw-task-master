"""A crash reaches the model as a sentence — spec 004 FR-MCP-07 — and a token
never reaches the log — FR-TOK-08.

The production failure behind both: `httpx.ReadError` inside a route came out
of the tool as a bare "Error executing tool team_weeks", because the ASGI
transport re-raised it past the route's own RFC 7807 500.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

import app.main
from app.api.errors import ProblemDetail, problem_handler, unhandled_handler
from app.main import _RedactAccessLog, redact
from app.mcp import server


class Ctx:
    headers = {"authorization": "Bearer nunp_test"}


def _crashing_app() -> FastAPI:
    tiny = FastAPI()
    tiny.add_exception_handler(Exception, unhandled_handler)

    @tiny.get("/api/v1/boom")
    def boom():
        raise httpx.ReadError("[Errno 11] Resource temporarily unavailable")

    return tiny


class TestCrashesAreReadable:
    def test_a_crash_becomes_the_routes_500_with_a_retry_hint(self, monkeypatch, caplog):
        monkeypatch.setattr(app.main, "app", _crashing_app())
        with caplog.at_level(logging.ERROR, logger="nldw-task-master"):
            with pytest.raises(ToolError) as raised:
                asyncio.run(server._api(Ctx(), "GET", "/boom"))
        assert not isinstance(raised.value, UnexpectedToolError)
        message = str(raised.value)
        assert message.startswith("500: Something went wrong on our side")
        assert message.endswith("This is usually temporary; try once more.")
        # The real failure is logged server-side, with its route and traceback.
        logged = [r for r in caplog.records if "unhandled_exception" in r.getMessage()]
        assert logged and "/api/v1/boom" in logged[0].getMessage()
        assert logged[0].exc_info and logged[0].exc_info[0] is httpx.ReadError

    def test_a_failed_write_is_not_retried_blindly(self, monkeypatch):
        tiny = FastAPI()
        tiny.add_exception_handler(Exception, unhandled_handler)

        @tiny.post("/api/v1/boom")
        def boom():
            raise httpx.ReadError("[Errno 11] Resource temporarily unavailable")

        monkeypatch.setattr(app.main, "app", tiny)
        with pytest.raises(ToolError) as raised:
            asyncio.run(server._api(Ctx(), "POST", "/boom", body={}))
        message = str(raised.value)
        assert message.startswith("500: Something went wrong on our side")
        assert message.endswith("It may or may not have been saved; check before trying again.")
        assert "try once more" not in message

    def test_a_refusal_has_no_retry_hint(self, monkeypatch):
        tiny = FastAPI()
        tiny.add_exception_handler(ProblemDetail, problem_handler)

        @tiny.get("/api/v1/nope")
        def nope():
            raise ProblemDetail(403, "Not yours.")

        monkeypatch.setattr(app.main, "app", tiny)
        with pytest.raises(ToolError, match=r"^403: Not yours\.$"):
            asyncio.run(server._api(Ctx(), "GET", "/nope"))


class TestTokensStayOutOfTheLog:
    TOKEN = "nunp_nwEzj64AbC-d_e9"

    def test_redact_removes_a_token_and_what_follows_bearer(self):
        decoded = f'/mcp --header "Authorization: Bearer {self.TOKEN}"'
        encoded = f"/mcp%20--header%20%22Authorization%3A%20Bearer%20{self.TOKEN}%22"
        for line in (decoded, encoded, f"/x?t={self.TOKEN}", "Bearer abc.def"):
            out = redact(line)
            assert self.TOKEN not in out and "abc.def" not in out
            assert "[redacted]" in out
        assert redact("/api/v1/team/timesheets") == "/api/v1/team/timesheets"

    def test_the_request_log_line_is_redacted(self, caplog):
        with caplog.at_level(logging.INFO, logger="nldw-task-master"):
            TestClient(app.main.app).get(f'/mcp --header "Authorization: Bearer {self.TOKEN}"')
        lines = [r.getMessage() for r in caplog.records if '"path"' in r.getMessage()]
        assert lines, "the request was not logged"
        assert all(self.TOKEN not in line for line in lines)

    def test_uvicorns_access_line_is_redacted(self):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            0,
            '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4:5", "GET", f"/mcp%20Bearer%20{self.TOKEN}", "1.1", 404),
            None,
        )
        assert _RedactAccessLog().filter(record)
        assert self.TOKEN not in record.getMessage()
