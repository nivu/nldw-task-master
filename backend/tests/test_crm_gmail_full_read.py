"""The summariser's full Gmail read — spec 009 FR-CRMAI-CHG-01,
FR-CRMAI-SUM-03; NFR-06 (007 NFR-CRM-08); plan §5.5, CT-2.

The sync's read stays metadata only (`test_crm_gmail_token.py`
`test_only_metadata_is_asked_for`, unchanged). `gmail.message_text` reads
one message with `format=full`, returns its decoded text parts only, in
memory, logs one structured line with no content, and is called from
`services/crm/summaries.py` and nowhere else in `app/`.
"""

from __future__ import annotations

import ast
import base64
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from test_crm_gmail_token import _Response, key, keypair  # noqa: F401

from app.config import settings as config
from app.services.crm import gmail

APP = Path(__file__).resolve().parents[1] / "app"
BODY = "ZQXBODY Could you send the pilot plan?"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.fixture
def gmail_api(key, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, data, timeout: _Response(200, {"access_token": "t", "expires_in": 3600}),
    )
    asked: list = []

    def get(url, params, headers, timeout):
        asked.append((url, params))
        if url.endswith("/gone"):
            return _Response(404, {})
        return _Response(
            200,
            {
                "id": "m1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "multipart/alternative",
                            "parts": [
                                {"mimeType": "text/plain", "body": {"data": _b64(BODY)}},
                                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML</p>")}},
                            ],
                        },
                        {
                            "mimeType": "text/plain",
                            "filename": "notes.txt",
                            "body": {"attachmentId": "a1"},
                        },
                    ],
                },
            },
        )

    monkeypatch.setattr(httpx, "get", get)
    return asked


class TestTheRead:
    def test_full_read_asks_for_the_full_format(self, gmail_api):
        gmail.message_text("owner@nunnarilabs.com", "m1")
        [(url, params)] = gmail_api
        assert url.endswith("/me/messages/m1")
        assert ("format", "full") in params

    def test_full_read_returns_the_text_parts_only(self, gmail_api):
        assert gmail.message_text("owner@nunnarilabs.com", "m1") == BODY

    def test_full_read_a_deleted_message_is_empty(self, gmail_api):
        assert gmail.message_text("owner@nunnarilabs.com", "gone") == ""

    def test_full_read_logs_one_line_with_no_content(self, gmail_api, caplog):
        caplog.set_level(logging.INFO, logger="nldw-task-master")
        gmail.message_text("owner@nunnarilabs.com", "m1")
        lines = [r.getMessage() for r in caplog.records if "crm_gmail_call" in r.getMessage()]
        [line] = [ln for ln in lines if '"op": "message_text"' in ln]
        assert json.loads(line)["status"] == "200"
        everything = "\n".join(r.getMessage() for r in caplog.records)
        assert "ZQXBODY" not in everything
        assert "owner@nunnarilabs.com" not in everything

    def test_full_read_without_the_key_is_refused(self, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", SecretStr(""))
        gmail.forget()
        with pytest.raises(gmail.NotConfigured):
            gmail.message_text("owner@nunnarilabs.com", "m1")


class TestOnlyTheSummariser:
    """FR-CRMAI-CHG-01 — an import-graph test pins the only caller."""

    def test_full_read_is_referenced_only_by_the_summariser(self):
        users = set()
        for path in APP.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                named = (isinstance(node, ast.Attribute) and node.attr == "message_text") or (
                    isinstance(node, ast.Name) and node.id == "message_text"
                )
                imported = isinstance(node, ast.ImportFrom) and any(
                    a.name == "message_text" for a in node.names
                )
                if named or imported:
                    users.add(path.relative_to(APP).as_posix())
        assert users == {"services/crm/summaries.py"}
