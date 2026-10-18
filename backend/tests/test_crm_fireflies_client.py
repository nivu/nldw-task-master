"""The Fireflies client — spec 009 FR-CRMAI-MEET-01, -02, NFR-CRMAI-01, -04,
-06; spec §15 "No Claude, Fireflies or Gmail key reaches the browser or a
log line"; C5, Q-42.

Raw HTTP on `httpx`, driven here through `httpx.MockTransport`: one GraphQL
`POST` with the key as a bearer token; the list asks for metadata only and
keeps what was updated after the cursor; the transcript is asked for by id;
the timeouts are 5 s to connect and 30 s to read; no log line or exception
carries the key, a title, an address or a body.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings as config
from app.services.crm import fireflies

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
KEY = "ff-ZQXFIREFLIESMARKER"
SECRET_BODY = "ZQXBODYMARKER Acme <priya@acme.in>"


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def _row(transcript_id: str, *, date: str, updated: str | None = None, **values) -> dict:
    return {
        "id": transcript_id,
        "title": "Pilot review",
        "date": date,
        "dateUpdated": updated or date,
        "transcript_url": f"https://app.fireflies.ai/view/{transcript_id}",
        "organizer_email": "Deepika@nunnarilabs.com",
        "participants": ["priya@acme.in", "not an address"],
        "meeting_attendees": [{"email": "ravi@acme.in"}, {"email": None}],
        **values,
    }


@pytest.fixture
def transport(monkeypatch):
    """`fireflies._http` on a mock transport; `seen` holds every request."""
    seen: list[httpx.Request] = []
    answers: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(
        fireflies, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(config, "FIREFLIES_API_KEY", SecretStr(KEY))
    return seen, answers


class TestTheRequest:
    """FR-CRMAI-MEET-01, -02; Q-42."""

    def test_fireflies_list_request_shape(self, transport):
        seen, answers = transport
        answers.append(_ok({"transcripts": []}))
        fireflies.list_since("2026-10-18T06:00:00+00:00")
        [request] = seen
        assert (request.method, str(request.url)) == ("POST", "https://api.fireflies.ai/graphql")
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["content-type"] == "application/json"
        body = json.loads(request.content)
        assert body["variables"] == {"fromDate": "2026-10-11T06:00:00Z", "limit": 50, "skip": 0}
        # Metadata only: the list never asks for what was said.
        assert "sentences" not in body["query"] and "summary" not in body["query"]
        for field in ("id", "title", "date", "dateUpdated", "transcript_url", "participants"):
            assert field in body["query"]

    def test_fireflies_first_poll_reaches_back_from_now(self, transport):
        seen, answers = transport
        answers.append(_ok({"transcripts": []}))
        fireflies.list_since(None, now=NOW)
        assert json.loads(seen[0].content)["variables"]["fromDate"] == "2026-10-11T12:00:00Z"

    def test_fireflies_transcript_request_shape(self, transport):
        seen, answers = transport
        answers.append(
            _ok(
                {
                    "transcript": {
                        "id": "t1",
                        "sentences": [{"speaker_name": "Priya", "text": "Send the plan."}],
                        "summary": {"overview": "A pilot.", "action_items": ["Send", "Call"]},
                    }
                }
            )
        )
        content = fireflies.transcript("t1")
        body = json.loads(seen[0].content)
        assert body["variables"] == {"id": "t1"}
        assert "sentences" in body["query"]
        assert content == {
            "sentences": [{"speaker": "Priya", "text": "Send the plan."}],
            "overview": "A pilot.",
            "action_items": "Send\nCall",
        }

    def test_fireflies_timeouts_are_five_and_thirty_seconds(self, transport):
        seen, answers = transport
        answers.append(_ok({"transcripts": []}))
        fireflies.list_since(None, now=NOW)
        timeout = seen[0].extensions["timeout"]
        assert (timeout["connect"], timeout["read"]) == (5.0, 30.0)

    def test_fireflies_refuses_without_a_key(self, transport, monkeypatch):
        seen, _ = transport
        monkeypatch.setattr(config, "FIREFLIES_API_KEY", None)
        assert fireflies.configured() is False
        with pytest.raises(fireflies.NotConfigured):
            fireflies.list_since(None, now=NOW)
        assert seen == []


class TestTheList:
    """FR-CRMAI-MEET-02, -07: metadata, updated after the cursor."""

    def test_fireflies_list_keeps_what_was_updated_after_the_cursor(self, transport):
        _, answers = transport
        answers.append(
            _ok(
                {
                    "transcripts": [
                        _row("old", date="2026-10-17T09:00:00Z"),
                        _row("new", date="2026-10-18T09:00:00Z"),
                        # An older meeting whose transcript was updated since.
                        _row("redone", date="2026-10-16T09:00:00Z", updated=1792317600000),
                        {"title": "No id"},
                    ]
                }
            )
        )
        listed = fireflies.list_since("2026-10-18T00:00:00+00:00")
        assert [m["id"] for m in listed] == ["new", "redone"]
        assert listed[0] == {
            "id": "new",
            "title": "Pilot review",
            "date": "2026-10-18T09:00:00+00:00",
            "updated_at": "2026-10-18T09:00:00+00:00",
            "attendees": ["deepika@nunnarilabs.com", "priya@acme.in", "ravi@acme.in"],
            "transcript_url": "https://app.fireflies.ai/view/new",
        }
        assert listed[1]["updated_at"] == "2026-10-18T10:00:00+00:00"

    def test_fireflies_list_reads_page_after_page(self, transport):
        seen, answers = transport
        page = [_row(f"t{i:03}", date="2026-10-18T09:00:00Z") for i in range(50)]
        answers.extend([_ok({"transcripts": page}), _ok({"transcripts": []})])
        assert len(fireflies.list_since(None, now=NOW)) == 50
        assert [json.loads(r.content)["variables"]["skip"] for r in seen] == [0, 50]


class TestTheKeyNeverShows:
    """NFR-CRMAI-04, -06 — logs carry the operation, status and latency only."""

    @pytest.mark.parametrize(
        "answer",
        [
            httpx.Response(500, text=f"{SECRET_BODY} {KEY}"),
            httpx.Response(401, json={"errors": [{"message": f"bad key {KEY}"}]}),
            httpx.Response(200, json={"errors": [{"message": SECRET_BODY}], "data": None}),
            httpx.ReadTimeout(f"timed out {KEY}"),
            httpx.ConnectError(f"refused {SECRET_BODY}"),
        ],
        ids=["500", "401", "graphql-error", "timeout", "connect"],
    )
    def test_fireflies_failures_name_no_key_and_no_body(self, transport, caplog, answer):
        _, answers = transport
        answers.append(answer)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(fireflies.FirefliesError) as raised:
            fireflies.transcript("t1")
        text = (
            f"{raised.value} {raised.value!r} {raised.value.__cause__} {raised.value.__context__}"
        )
        assert KEY not in text and "ZQXBODYMARKER" not in text
        assert raised.value.__context__ is None and raised.value.__cause__ is None
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert KEY not in logged and "ZQXBODYMARKER" not in logged

    def test_fireflies_the_log_line_has_operation_and_status(self, transport, caplog):
        _, answers = transport
        answers.append(
            _ok(
                {
                    "transcripts": [
                        _row("t1", date="2026-10-18T09:00:00Z", title=SECRET_BODY),
                    ]
                }
            )
        )
        caplog.set_level(logging.INFO)
        fireflies.list_since(None, now=NOW)
        [line] = [r.getMessage() for r in caplog.records if "crm_fireflies_call" in r.getMessage()]
        event = json.loads(line)
        assert event["op"] == "list" and event["status"] == "200"
        # NFR-CRMAI-06 (amended in S5): the job, and no workspace for the
        # poll, which serves every workspace.
        assert (event["job"], event["workspace"]) == ("meetings", "-")
        assert "ZQXBODYMARKER" not in line and KEY not in line and "acme" not in line

    def test_fireflies_a_transcript_fetch_logs_its_job_and_workspace(self, transport, caplog):
        _, answers = transport
        answers.append(_ok({"transcript": {"id": "t1", "sentences": [], "summary": None}}))
        caplog.set_level(logging.INFO)
        with fireflies.for_workspace("labs"):
            fireflies.transcript("t1")
        [line] = [r.getMessage() for r in caplog.records if "crm_fireflies_call" in r.getMessage()]
        event = json.loads(line)
        assert (event["job"], event["workspace"], event["op"]) == ("meetings", "labs", "transcript")
        assert isinstance(event["ms"], int)
