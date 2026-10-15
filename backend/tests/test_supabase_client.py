"""The backend's Supabase client: HTTP/1.1, and reads retried once.

The library default — one HTTP/2 connection shared by every worker thread —
failed in production with `ReadError: [Errno 11]` whenever two reports ran at
once, and the failure took down every request in flight on the connection.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import supabase as db


class TestWiring:
    def test_postgrest_uses_the_shared_client(self):
        assert db.supabase.postgrest.session is db._http

    def test_auth_has_its_own_short_timeout_client(self):
        assert db.supabase.auth._http_client is db._auth_http
        assert db.supabase.auth.admin._http_client is db._auth_http
        assert db._auth_http is not db._http
        assert db._auth_http.timeout.read == 10.0
        assert db._auth_http.timeout.connect == 5.0
        assert db._auth_http._transport._pool._http2 is False

    def test_http2_is_off(self):
        transport = db._http._transport
        assert isinstance(transport, db._RetryIdempotent)
        assert transport._pool._http2 is False

    def test_timeout_and_redirects_match_postgrests_own_defaults(self):
        assert db._http.timeout.read >= 60
        assert db._http.follow_redirects is True


class _Flaky(db._RetryIdempotent):
    """Fails the first `failures` sends with ReadError, then answers 200."""

    def __init__(self, failures: int):
        super().__init__()
        self.failures = failures
        self.calls = 0

    def _send(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadError("[Errno 11] Resource temporarily unavailable", request=request)
        return httpx.Response(200, request=request)


@pytest.fixture
def flaky(monkeypatch):
    def make(failures: int) -> _Flaky:
        transport = _Flaky(failures)
        monkeypatch.setattr(
            httpx.HTTPTransport, "handle_request", lambda self, request: self._send(request)
        )
        return transport

    return make


def _send(transport, method):
    return transport.handle_request(httpx.Request(method, "http://db.internal/rest/v1/x"))


class TestRetry:
    def test_a_read_is_retried_once(self, flaky):
        transport = flaky(1)
        assert _send(transport, "GET").status_code == 200
        assert transport.calls == 2

    def test_a_write_is_never_retried(self, flaky):
        transport = flaky(1)
        with pytest.raises(httpx.ReadError):
            _send(transport, "POST")
        assert transport.calls == 1

    def test_a_read_that_fails_twice_propagates(self, flaky):
        transport = flaky(2)
        with pytest.raises(httpx.ReadError):
            _send(transport, "GET")
        assert transport.calls == 2


class _DropsMidBody(httpx.SyncByteStream):
    """Sends some of the body, then the connection drops."""

    def __iter__(self):
        yield b'[{"id":'
        raise httpx.ReadError("[Errno 11] Resource temporarily unavailable")


class TestRetryAfterHeaders:
    def test_a_read_that_drops_mid_body_is_retried(self, monkeypatch):
        calls = []

        def send(self, request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, request=request, stream=_DropsMidBody())
            return httpx.Response(200, request=request, content=b'[{"id": 1}]')

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
        response = _send(db._RetryIdempotent(), "GET")
        assert len(calls) == 2
        assert response.read() == b'[{"id": 1}]'

    def test_a_write_that_drops_mid_body_is_not_retried(self, monkeypatch):
        calls = []

        def send(self, request):
            calls.append(request)
            return httpx.Response(200, request=request, stream=_DropsMidBody())

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
        response = _send(db._RetryIdempotent(), "POST")
        with pytest.raises(httpx.ReadError):
            response.read()
        assert len(calls) == 1


class TestLongIdLists:
    """Spec 007 S12 (found by the dashboard's e2e run on a database with
    two thousand profiles): every staff id in one `in.(…)` filter passes
    PostgREST's request-line limit and is answered 414, so the capacity
    service's list of everybody goes in batches."""

    def test_list_bookings_reads_many_people_in_batches(self, monkeypatch):
        import uuid
        from urllib.parse import unquote

        ids = [str(uuid.uuid4()) for _ in range(250)]
        seen: list[str] = []

        def send(self, request):
            url = unquote(str(request.url))
            seen.append(url)
            batch = url.split("user_id=in.(", 1)[1].split(")", 1)[0].split(",")
            body = [
                {"user_id": u, "date": f"2026-10-{(n % 28) + 1:02d}"} for n, u in enumerate(batch)
            ]
            return httpx.Response(200, request=request, json=body)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
        rows = db.list_bookings(user_ids=ids + ids[:5], columns="user_id,date")
        assert len(seen) == 3
        assert max(len(url) for url in seen) < 6000
        assert sorted(r["user_id"] for r in rows) == sorted(ids)
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)

    def test_a_short_list_is_one_request(self, monkeypatch):
        seen = []

        def send(self, request):
            seen.append(request)
            return httpx.Response(200, request=request, json=[])

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
        assert db.list_bookings(user_ids=["a", "b"]) == []
        assert db.list_bookings(user_ids=[]) == []
        assert len(seen) == 1
