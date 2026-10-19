"""Gmail reads under load — spec 010 NFR-CAP-03, FR-CAP-SRC-06; plan §5.5,
§5.13.

`gmail._get` tries a 429 or a 5xx again, at most three tries in all,
waiting `Retry-After` (capped at 30 seconds) or else 1 then 4 seconds, and
then fails as before. A 404 or a 403 is an answer and is not tried again.
`gmail.search` asks for ids only, never in Spam or Trash. Nothing here
holds a real credential; `time.sleep` is patched, so nothing waits.
"""

from __future__ import annotations

import pytest

from app.services.crm import gmail

MAILBOX = "owner@nunnarilabs.com"


class _Response:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body


@pytest.fixture
def calls(monkeypatch):
    """Scripted Gmail answers, the token stubbed, and every wait recorded."""
    monkeypatch.setattr(gmail, "token", lambda mailbox: "token")
    script: list[_Response] = []
    seen: list[dict] = []
    waits: list[float] = []

    def get(url, params, headers, timeout):
        seen.append({"url": url, "params": params})
        return script.pop(0)

    monkeypatch.setattr(gmail.httpx, "get", get)
    monkeypatch.setattr(gmail.time, "sleep", waits.append)
    return script, seen, waits


class TestTryingAgain:
    """Plan §5.13 — 429 and 5xx, at most three tries."""

    def test_gmail_retry_honours_retry_after_then_succeeds(self, calls):
        script, seen, waits = calls
        script += [_Response(429, headers={"Retry-After": "7"}), _Response(200, {"id": "x"})]
        assert gmail.profile(MAILBOX) == {"id": "x"}
        assert len(seen) == 2 and waits == [7.0]

    def test_gmail_retry_caps_a_long_retry_after(self, calls):
        script, _, waits = calls
        script += [_Response(429, headers={"Retry-After": "600"}), _Response(200, {})]
        gmail.profile(MAILBOX)
        assert waits == [30.0]

    def test_gmail_retry_waits_one_then_four_seconds_for_a_5xx(self, calls):
        script, seen, waits = calls
        script += [_Response(503), _Response(503), _Response(200, {"ok": True})]
        assert gmail.profile(MAILBOX) == {"ok": True}
        assert len(seen) == 3 and waits == [1.0, 4.0]

    def test_gmail_retry_gives_up_after_three_tries(self, calls):
        script, seen, waits = calls
        script += [_Response(500), _Response(500), _Response(500)]
        with pytest.raises(gmail.GmailError, match=r"\(500\)"):
            gmail.profile(MAILBOX)
        assert len(seen) == 3 and waits == [1.0, 4.0]

    @pytest.mark.parametrize("status", [404, 403])
    def test_gmail_retry_never_repeats_an_answer(self, calls, status):
        script, seen, waits = calls
        script += [_Response(status)]
        with pytest.raises(gmail.GmailError, match=rf"\({status}\)"):
            gmail.labels(MAILBOX)
        assert len(seen) == 1 and waits == []

    def test_gmail_retry_a_missing_message_still_answers_empty(self, calls):
        script, seen, _ = calls
        script += [_Response(404)]
        assert gmail.message(MAILBOX, "m1") == {}
        assert len(seen) == 1


class TestSearch:
    """FR-CAP-SRC-06 — ids only, never Spam or Trash, page by page."""

    def test_gmail_search_asks_for_ids_with_the_query(self, calls):
        script, seen, _ = calls
        script += [_Response(200, {"messages": [{"id": "m1", "threadId": "t1"}]})]
        found = gmail.search(MAILBOX, "has:attachment filename:sow", page_token="p2", page_size=100)
        assert found == {"messages": [{"id": "m1", "threadId": "t1"}]}
        params = seen[0]["params"]
        assert seen[0]["url"].endswith("/me/messages")
        assert params == {
            "q": "has:attachment filename:sow",
            "maxResults": 100,
            "includeSpamTrash": "false",
            "pageToken": "p2",
        }
        assert "labelIds" not in params and "format" not in params
