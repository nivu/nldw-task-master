"""Gmail access — spec 007 FR-CRM-SYNC-03, NFR-CRM-02, -08; plan §2.1, §2.2.

The service account signs an RS256 assertion naming itself (`iss`), the
mailbox (`sub`), the read-only Gmail scope and the token endpoint (`aud`);
the access token is cached in process memory only; every call is logged with
its latency; and neither the key nor a token ever reaches a log line.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.config import settings as config
from app.services.crm import gmail

ACCESS_TOKEN = "ya29.secret-access-token-value"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private.public_key(), pem


@pytest.fixture
def key(keypair, monkeypatch):
    _, pem = keypair
    body = {
        "type": "service_account",
        "client_email": "crm-sync@nunnari.iam.gserviceaccount.com",
        "private_key_id": "kid-1",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", SecretStr(json.dumps(body)))
    gmail.forget()
    yield body
    gmail.forget()


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> dict:
        return self._body


class TestTheAssertion:
    def test_claims(self, key, keypair):
        public, _ = keypair
        signed = gmail.assertion(key, "owner@nunnarilabs.com", 1_790_000_000)
        # Verified with the public key; the fixed clock is long past, so
        # expiry is checked by value below rather than against now.
        claims = jwt.decode(
            signed,
            public,
            algorithms=["RS256"],
            audience="https://oauth2.googleapis.com/token",
            options={"verify_exp": False, "verify_iat": False},
        )
        assert claims == {
            "iss": "crm-sync@nunnari.iam.gserviceaccount.com",
            "sub": "owner@nunnarilabs.com",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": 1_790_000_000,
            "exp": 1_790_003_600,
        }
        assert jwt.get_unverified_header(signed)["kid"] == "kid-1"

    def test_read_only_scope_and_nothing_else(self, key):
        assert gmail.claims(key, "x@nunnarilabs.com", 0)["scope"] == (
            "https://www.googleapis.com/auth/gmail.readonly"
        )

    def test_the_key_may_be_base64(self, key, monkeypatch):
        raw = base64.b64encode(json.dumps(key).encode()).decode()
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", SecretStr(raw))
        assert gmail._key()["client_email"] == key["client_email"]

    def test_no_key_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", None)
        with pytest.raises(gmail.NotConfigured):
            gmail._key()


class TestTheToken:
    def test_exchanged_once_and_cached_in_memory(self, key, monkeypatch):
        posts = []

        def post(url, data, timeout):
            posts.append((url, data))
            return _Response(200, {"access_token": ACCESS_TOKEN, "expires_in": 3600})

        monkeypatch.setattr(httpx, "post", post)
        assert gmail.token("owner@nunnarilabs.com") == ACCESS_TOKEN
        assert gmail.token("owner@nunnarilabs.com") == ACCESS_TOKEN
        assert len(posts) == 1
        url, data = posts[0]
        assert url == "https://oauth2.googleapis.com/token"
        assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"

    def test_a_refusal_names_the_status_not_the_secret(self, key, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, data, timeout: _Response(401, {"error": "unauthorized_client"}),
        )
        with pytest.raises(gmail.GmailError) as raised:
            gmail.token("owner@nunnarilabs.com")
        assert "401 unauthorized_client" in str(raised.value)
        assert "PRIVATE KEY" not in str(raised.value)


class TestNothingSecretIsLogged:
    """NFR-CRM-02, -08: each call's latency is logged; the key and the token
    never are."""

    def test_logs(self, key, monkeypatch, caplog):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, data, timeout: _Response(
                200, {"access_token": ACCESS_TOKEN, "expires_in": 3600}
            ),
        )
        seen = []

        def get(url, params, headers, timeout):
            seen.append(headers["Authorization"])
            return _Response(200, {"labels": [{"id": "Label_1", "name": "Sales"}]})

        monkeypatch.setattr(httpx, "get", get)
        with caplog.at_level(logging.DEBUG):
            assert gmail.labels("owner@nunnarilabs.com") == [{"id": "Label_1", "name": "Sales"}]
        assert seen == [f"Bearer {ACCESS_TOKEN}"]
        text = caplog.text
        assert '"event": "crm_gmail_call", "op": "token"' in text
        assert '"event": "crm_gmail_call", "op": "labels"' in text
        assert '"ms": ' in text
        assert ACCESS_TOKEN not in text
        assert "PRIVATE KEY" not in text
        assert key["private_key"].splitlines()[1] not in text
        assert gmail.assertion(key, "owner@nunnarilabs.com", 0)[:40] not in text

    def test_a_401_renews_the_token_once(self, key, monkeypatch):
        tokens = iter(["first-token", "second-token"])
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, data, timeout: _Response(
                200, {"access_token": next(tokens), "expires_in": 3600}
            ),
        )
        calls = []

        def get(url, params, headers, timeout):
            calls.append(headers["Authorization"])
            if len(calls) == 1:
                return _Response(401, {})
            return _Response(200, {"historyId": "42"})

        monkeypatch.setattr(httpx, "get", get)
        assert gmail.profile("owner@nunnarilabs.com") == {"historyId": "42"}
        assert calls == ["Bearer first-token", "Bearer second-token"]

    def test_an_expired_history_is_history_gone(self, key, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, data, timeout: _Response(200, {"access_token": "t", "expires_in": 3600}),
        )
        monkeypatch.setattr(httpx, "get", lambda url, params, headers, timeout: _Response(404, {}))
        with pytest.raises(gmail.HistoryGone):
            gmail.history("owner@nunnarilabs.com", "1", page_token=None, page_size=50)

    def test_only_metadata_is_asked_for(self, key, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, data, timeout: _Response(200, {"access_token": "t", "expires_in": 3600}),
        )
        asked = []

        def get(url, params, headers, timeout):
            asked.append(params)
            return _Response(
                200,
                {
                    "id": "m1",
                    "threadId": "t1",
                    "labelIds": ["Label_1"],
                    "internalDate": "1",
                    "payload": {"headers": [{"name": "Subject", "value": "Hi"}]},
                },
            )

        monkeypatch.setattr(httpx, "get", get)
        found = gmail.message("owner@nunnarilabs.com", "m1")
        assert ("format", "metadata") in asked[0]
        assert found["headers"] == [{"name": "Subject", "value": "Hi"}]
        assert "payload" not in found
