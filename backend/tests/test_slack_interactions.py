"""Slack interactivity — FR-NOTIF-04.

`POST /api/v1/slack/interactions` is the one unauthenticated route, so these
check that the signature is verified before anything else, and that the person
who pressed the button is identified by the email `users.info` returns for
their Slack user id (the interaction payload itself carries no email).

Requests are signed with a real HMAC-SHA256 over Slack's v0 basestring. Slack's
`users.info`, the Supabase module and the booking service are replaced at the
function boundary; nothing here talks to Slack or the database. The profile
lookup itself is exercised against a fake Supabase client.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import slack as slack_api
from app.config import settings
from app.main import app
from app.services import bookings as booking_service
from app.services import supabase as db
from app.services.notify import slack as slack_adapter

SECRET = "test-signing-secret"
URL = "/api/v1/slack/interactions"

PROFILES = {
    "lead@example.com": {
        "id": "u-lead",
        "role": "lead",
        "lead_id": None,
        "is_active": True,
    },
    "gone@example.com": {
        "id": "u-gone",
        "role": "lead",
        "lead_id": None,
        "is_active": False,
    },
}

SLACK_EMAILS = {
    "U-LEAD": "lead@example.com",
    "U-GONE": "gone@example.com",
    "U-STRANGER": "stranger@example.com",
    "U-SHOUTY": "Lead@Example.COM",
}


def _body(action_id: str = "booking_approve", user_id: str = "U-LEAD") -> bytes:
    payload = {
        "type": "block_actions",
        "user": {"id": user_id, "username": "someone", "team_id": "T1"},
        "actions": [{"action_id": action_id, "value": "b-1"}],
    }
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _headers(body: bytes, *, timestamp: int | None = None, secret: str = SECRET) -> dict:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    basestring = b"v0:" + ts.encode() + b":" + body
    signature = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": signature,
        "content-type": "application/x-www-form-urlencoded",
    }


@pytest.fixture
def decisions(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", SecretStr(SECRET))
    monkeypatch.setattr(slack_adapter, "lookup_email", SLACK_EMAILS.get)
    monkeypatch.setattr(
        db, "get_profile_by_email_ignoring_case", lambda email: PROFILES.get(email.lower())
    )
    monkeypatch.setattr(booking_service, "decide", lambda **kwargs: calls.append(kwargs))
    return calls


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _post(client: TestClient, body: bytes, headers: dict):
    return client.post(URL, content=body, headers=headers)


class TestDecision:
    def test_approve_uses_the_users_info_email(self, client, decisions):
        body = _body("booking_approve")
        response = _post(client, body, _headers(body))

        assert response.status_code == 200
        assert response.json()["text"] == "Approved."
        assert len(decisions) == 1
        call = decisions[0]
        assert call["booking_id"] == "b-1"
        assert call["approve"] is True
        assert call["note"] is None
        assert call["actor"].id == "u-lead"
        assert call["actor"].role == "lead"

    def test_reject_passes_the_note(self, client, decisions):
        body = _body("booking_reject")
        response = _post(client, body, _headers(body))

        assert response.status_code == 200
        assert response.json()["text"] == "Rejected."
        assert len(decisions) == 1
        assert decisions[0]["approve"] is False
        assert decisions[0]["note"] == "Rejected from Slack without a note."
        assert decisions[0]["actor"].id == "u-lead"

    def test_slack_email_in_a_different_case_still_matches(self, client, decisions):
        body = _body(user_id="U-SHOUTY")
        response = _post(client, body, _headers(body))

        assert response.status_code == 200
        assert len(decisions) == 1
        assert decisions[0]["actor"].id == "u-lead"

    def test_identity_lookup_runs_off_the_event_loop(self, client, decisions, monkeypatch):
        on_loop: list[bool] = []

        def lookup(user_id: str) -> str | None:
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
            return SLACK_EMAILS.get(user_id)

        monkeypatch.setattr(slack_adapter, "lookup_email", lookup)
        body = _body()
        response = _post(client, body, _headers(body))

        assert response.status_code == 200
        assert on_loop == [False]


class TestSignature:
    def test_bad_signature_is_401(self, client, decisions):
        body = _body()
        response = _post(client, body, _headers(body, secret="not-the-secret"))
        assert response.status_code == 401
        assert decisions == []

    def test_missing_signature_is_401(self, client, decisions):
        body = _body()
        headers = _headers(body)
        del headers["x-slack-signature"]
        response = _post(client, body, headers)
        assert response.status_code == 401
        assert decisions == []

    def test_stale_timestamp_is_401(self, client, decisions):
        body = _body()
        stale = int(time.time()) - slack_api._MAX_SKEW_SECONDS - 60
        response = _post(client, body, _headers(body, timestamp=stale))
        assert response.status_code == 401
        assert decisions == []

    def test_unset_signing_secret_is_503(self, client, decisions, monkeypatch):
        monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", None)
        body = _body()
        response = _post(client, body, _headers(body))
        assert response.status_code == 503
        assert decisions == []


class TestIdentity:
    def test_users_info_failure_is_403(self, client, decisions, monkeypatch):
        monkeypatch.setattr(slack_adapter, "lookup_email", lambda user_id: None)
        body = _body()
        response = _post(client, body, _headers(body))
        assert response.status_code == 403
        assert "users:read.email" in response.json()["detail"]
        assert decisions == []

    def test_missing_user_id_is_403_without_calling_slack(self, client, decisions, monkeypatch):
        looked_up: list[str] = []
        monkeypatch.setattr(slack_adapter, "lookup_email", looked_up.append)
        payload = {
            "user": {"username": "someone"},
            "actions": [{"action_id": "booking_approve", "value": "b-1"}],
        }
        body = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()
        response = _post(client, body, _headers(body))
        assert response.status_code == 403
        assert looked_up == []
        assert decisions == []

    def test_unknown_profile_is_403(self, client, decisions):
        body = _body(user_id="U-STRANGER")
        response = _post(client, body, _headers(body))
        assert response.status_code == 403
        assert decisions == []

    def test_inactive_profile_is_403(self, client, decisions):
        body = _body(user_id="U-GONE")
        response = _post(client, body, _headers(body))
        assert response.status_code == 403
        assert decisions == []


class _FakeProfiles:
    """Enough of the Supabase client for `get_profile_by_email_ignoring_case`.

    `ilike` is applied the way Postgres would (backslash escapes, `%` and `_`
    wildcards, case ignored) so the escaping is actually tested.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows, self.patterns = rows, []

    def table(self, _name):
        return self

    def select(self, *_):
        return self

    def ilike(self, column: str, pattern: str):
        self.patterns.append(pattern)
        regex, i = "", 0
        while i < len(pattern):
            ch = pattern[i]
            if ch == "\\" and i + 1 < len(pattern):
                regex += re.escape(pattern[i + 1])
                i += 2
                continue
            regex += ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
            i += 1
        self.matched = [r for r in self.rows if re.fullmatch(regex, r[column], re.IGNORECASE)]
        return self

    def execute(self):
        return type("Response", (), {"data": self.matched})()


class TestProfileByEmailIgnoringCase:
    def test_matches_regardless_of_case(self, monkeypatch):
        fake = _FakeProfiles([{"id": "p1", "email": "Priya@nunnarilabs.com"}])
        monkeypatch.setattr(db, "supabase", fake)
        assert db.get_profile_by_email_ignoring_case(" priya@NunnariLabs.com ")["id"] == "p1"

    def test_like_wildcards_in_the_address_are_escaped(self, monkeypatch):
        fake = _FakeProfiles(
            [
                {"id": "p1", "email": "aXb@example.com"},
                {"id": "p2", "email": "a_b@example.com"},
            ]
        )
        monkeypatch.setattr(db, "supabase", fake)
        assert db.get_profile_by_email_ignoring_case("a_b@example.com")["id"] == "p2"
        assert fake.patterns == ["a\\_b@example.com"]
        assert db.get_profile_by_email_ignoring_case("a%@example.com") is None

    def test_two_profiles_differing_only_in_case_is_none(self, monkeypatch):
        fake = _FakeProfiles(
            [
                {"id": "p1", "email": "dup@example.com"},
                {"id": "p2", "email": "DUP@example.com"},
            ]
        )
        monkeypatch.setattr(db, "supabase", fake)
        assert db.get_profile_by_email_ignoring_case("dup@example.com") is None

    def test_no_match_is_none(self, monkeypatch):
        monkeypatch.setattr(db, "supabase", _FakeProfiles([]))
        assert db.get_profile_by_email_ignoring_case("nobody@example.com") is None

    def test_blank_email_does_not_query(self, monkeypatch):
        monkeypatch.setattr(db, "supabase", None)
        assert db.get_profile_by_email_ignoring_case("  ") is None


class TestLookupEmail:
    """`lookup_email` itself, with the HTTP call replaced at `_get`."""

    @pytest.fixture(autouse=True)
    def token(self, monkeypatch):
        monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", SecretStr("xoxb-test"))

    def test_returns_the_profile_email(self, monkeypatch):
        seen: list[tuple] = []

        def fake_get(path: str, token: str, *, timeout: float) -> dict:
            seen.append((path, timeout))
            return {"ok": True, "user": {"id": "U1", "profile": {"email": "a@example.com"}}}

        monkeypatch.setattr(slack_adapter, "_get", fake_get)
        assert slack_adapter.lookup_email("U1") == "a@example.com"
        # Short timeout: Slack drops an interaction not answered within 3 s.
        assert seen == [("users.info?user=U1", 2)]

    @pytest.mark.parametrize(
        "response",
        [
            {"ok": False, "error": "missing_scope"},
            {"ok": True, "user": {"id": "U1", "profile": {}}},
            {"ok": True, "user": {"id": "U1", "profile": {"email": ""}}},
        ],
    )
    def test_none_when_slack_gives_no_email(self, monkeypatch, response):
        monkeypatch.setattr(slack_adapter, "_get", lambda path, token, **_: response)
        assert slack_adapter.lookup_email("U1") is None

    def test_none_without_a_token(self, monkeypatch):
        monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
        monkeypatch.setattr(
            slack_adapter, "_get", lambda path, token, **_: pytest.fail("called Slack")
        )
        assert slack_adapter.lookup_email("U1") is None
