"""Fakes for the AI jobs — spec 009 tasks "How to use this file"; NFR-CRMAI-09.

No test calls Claude, Fireflies, Gmail or a website. `FakeClaude` stands in
for `services/crm/claude.messages`: it answers from a script, can answer
with output that fails the schema, time out or fail, and records every
prompt it was given, so a test can check what reached the model.
`FakeGmailText` stands in for `services/crm/gmail.message_text` and records
every message id it was asked for, so a test can check that only stored
messages are read. `FakeFireflies` stands in for `services/crm/fireflies`'s
two reads and records every transcript fetch, so a test can check that an
internal or unmatched meeting is never downloaded. `FakeWeb` stands in for
the website reader's DNS lookups and HTTP client (`services/crm/web_fetch`)
with scripted answers and pages, and records every lookup and request, so a
test can check which address was connected to. `CapturedCelery` records every task a test enqueues
instead of sending it. `FakeGmailThread` (spec 010) stands in for email
capture's reads, `gmail.thread_meta` and `gmail.thread_text`, with the
label list and listing they need, and records every metadata read
(`meta_reads`, thread ids) and every text read (`text_reads`, message ids),
so a test can check that only the messages that passed the rules are read.
The clock helpers build fixed times on the Asia/Kolkata wall clock, where
the budget month turns (Q-23).

Route and store tests use `tests/crm_fake.py`; these sit beside it.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from celery.app.task import Task

from app.services.crm import claude, fireflies, gmail, web_fetch

IST = ZoneInfo("Asia/Kolkata")

#: Script entries other than an answer.
INVALID = "<invalid output>"
TIMEOUT = "<timeout>"
FAIL = "<failed>"


class FakeClaude:
    """`claude.messages`, scripted. Each entry is the JSON the model answers
    with (a dict), or `INVALID` (an answer that matches no schema), `TIMEOUT`,
    `FAIL` (an HTTP failure), or an exception to raise. With `default`, an
    exhausted script keeps answering it; without, one more call fails the
    test, so an unexpected call is never silent."""

    def __init__(
        self,
        *script: Any,
        default: Any = None,
        input_tokens: int = 1000,
        output_tokens: int = 200,
    ) -> None:
        self.script = list(script)
        self.default = default
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls: list[dict[str, Any]] = []

    def install(self, monkeypatch) -> FakeClaude:
        monkeypatch.setattr(claude, "messages", self.messages)
        return self

    @property
    def prompts(self) -> list[str]:
        """Everything each call sent the model: system prompt and message."""
        return [f"{c['system']}\n{c['user']}" for c in self.calls]

    def messages(
        self, *, model: str, system: str, user: str, schema: dict, job: str, workspace: str
    ) -> claude.Reply:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user": user,
                "schema": schema,
                "job": job,
                "workspace": workspace,
            }
        )
        if self.script:
            entry = self.script.pop(0)
        elif self.default is not None:
            entry = self.default
        else:
            raise AssertionError("FakeClaude: a model call nobody scripted")
        if entry == TIMEOUT:
            raise claude.ClaudeTimeout("The Claude call timed out (ReadTimeout).")
        if entry == FAIL:
            raise claude.ClaudeError("The Claude call failed (500).")
        if isinstance(entry, BaseException):
            raise entry
        return claude.Reply(
            data={"not": "the schema"} if entry == INVALID else entry,
            model=model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class FakeGmailText:
    """`gmail.message_text`, from a dict of Gmail id → text. Every read is
    recorded as `(mailbox address, Gmail id)`. An id it does not know is
    the empty text, as a message deleted meanwhile is; `failing` ids raise
    a `GmailError`."""

    def __init__(self, texts: dict[str, str] | None = None, *, failing: set[str] = frozenset()):
        self.texts = dict(texts or {})
        self.failing = set(failing)
        self.reads: list[tuple[str, str]] = []

    def install(self, monkeypatch) -> FakeGmailText:
        monkeypatch.setattr(gmail, "message_text", self.message_text)
        return self

    @property
    def ids(self) -> list[str]:
        return [gmail_id for _, gmail_id in self.reads]

    def message_text(self, mailbox: str, message_id: str) -> str:
        self.reads.append((mailbox, message_id))
        if message_id in self.failing:
            raise gmail.GmailError("Gmail message_text failed (500).")
        return self.texts.get(message_id, "")


class FakeFireflies:
    """`fireflies.list_since` and `fireflies.transcript`, from meetings added
    with `meeting()`. The list answers metadata only, updated after the
    cursor, as the client does; every content fetch is recorded in
    `fetched`. `failing` ids raise a `FirefliesError` when fetched, and
    `down` makes the list fail. Installing it also sets a Fireflies key
    and keeps the poll cursor in memory (`cursor`), not in Redis."""

    def __init__(self) -> None:
        self.meetings: dict[str, dict[str, Any]] = {}
        self.contents: dict[str, dict[str, Any]] = {}
        self.fetched: list[str] = []
        self.listed: list[str | None] = []
        self.failing: set[str] = set()
        self.down = False
        self.cursor: str | None = None

    def install(self, monkeypatch, *, key: bool = True) -> FakeFireflies:
        from pydantic import SecretStr

        from app.config import settings
        from app.services.crm import meetings

        monkeypatch.setattr(settings, "FIREFLIES_API_KEY", SecretStr("ff-test") if key else None)
        monkeypatch.setattr(fireflies, "list_since", self.list_since)
        monkeypatch.setattr(fireflies, "transcript", self.transcript)
        monkeypatch.setattr(meetings, "read_cursor", lambda: self.cursor)
        monkeypatch.setattr(meetings, "save_cursor", self._save)
        return self

    def _save(self, value: str) -> None:
        self.cursor = value

    def meeting(
        self,
        transcript_id: str,
        attendees: list[str],
        *,
        title: str = "Pilot review",
        date: str = "2026-10-18T09:30:00+00:00",
        updated_at: str | None = None,
        sentences: list[tuple[str, str]] | None = None,
        overview: str = "",
        action_items: str = "",
    ) -> dict[str, Any]:
        """A recorded meeting: its metadata as listed and its content."""
        self.meetings[transcript_id] = {
            "id": transcript_id,
            "title": title,
            "date": date,
            "updated_at": updated_at or date,
            "attendees": sorted(a.lower() for a in attendees),
            "transcript_url": f"https://app.fireflies.ai/view/{transcript_id}",
        }
        self.contents[transcript_id] = {
            "sentences": [
                {"speaker": who, "text": text}
                for who, text in (sentences or [("Priya", "Could you send the pilot plan?")])
            ],
            "overview": overview,
            "action_items": action_items,
        }
        return self.meetings[transcript_id]

    def list_since(self, cursor: str | None, *, now: datetime | None = None) -> list[dict]:
        self.listed.append(cursor)
        if self.down:
            raise fireflies.FirefliesError("Fireflies list failed (503).")
        found = [
            dict(m)
            for m in self.meetings.values()
            if cursor is None or _iso(m["updated_at"]) > _iso(cursor)
        ]
        return sorted(found, key=lambda m: (m["updated_at"], m["id"]))

    def transcript(self, transcript_id: str) -> dict[str, Any]:
        self.fetched.append(transcript_id)
        if transcript_id in self.failing:
            raise fireflies.FirefliesError("Fireflies transcript failed (500).")
        return self.contents[transcript_id]


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


#: A public address for a scripted host (TEST-NET-like answers are not
#: global, so a real public address is used; nothing connects to it).
PUBLIC = "93.184.216.34"


class FakeWeb:
    """`web_fetch.resolve` and `web_fetch._client`, scripted. `answer(host,
    *answers)` sets what a host resolves to: each answer a list of
    addresses, used in turn (the last one repeats), so a second lookup can
    differ from the first (DNS rebinding). `page(host, path, html)` serves a
    page; `status`, `headers`, `chunks` (an iterator of bytes, pulled
    lazily) and `wait` (seconds the clock moves before the answer arrives)
    shape the answer. Every lookup is in `lookups` and every request
    in `requests` as `{address, host, sni, path, accept_encoding}`: the
    address is the one
    connected to. `clock` is the reader's monotonic clock; `tick` moves it on
    with every chunk a body yields, for the time cap."""

    def __init__(self) -> None:
        self.dns: dict[str, list[list[str]]] = {}
        self.pages: dict[tuple[str, str], dict[str, Any]] = {}
        self.lookups: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.pulled = 0
        self.clock = 0.0
        self.tick = 0.0

    def install(self, monkeypatch) -> FakeWeb:
        monkeypatch.setattr(web_fetch, "resolve", self.resolve)
        monkeypatch.setattr(web_fetch, "_client", self.client)
        monkeypatch.setattr(web_fetch, "_clock", lambda: self.clock)
        return self

    def answer(self, host: str, *answers: list[str]) -> None:
        self.dns[host] = [list(a) for a in answers]

    def site(self, host: str, pages: dict[str, str], *, address: str = PUBLIC) -> None:
        """A host resolving to a public address, serving `pages` by path."""
        self.answer(host, [address])
        for path, html in pages.items():
            self.page(host, path, html)

    def page(
        self,
        host: str,
        path: str,
        html: str = "",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: Iterator[bytes] | None = None,
        wait: float = 0.0,
    ) -> None:
        self.pages[(host, path)] = {
            "html": html,
            "status": status,
            "headers": {"content-type": "text/html; charset=utf-8", **(headers or {})},
            "chunks": chunks,
            "wait": wait,
        }

    def resolve(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        answers = self.dns.get(host)
        if not answers:
            raise socket.gaierror("Name or service not known")
        return list(answers.pop(0) if len(answers) > 1 else answers[0])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle), follow_redirects=False)

    def _stream(self, chunks: Iterator[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            self.pulled += len(chunk)
            self.clock += self.tick
            yield chunk

    def _handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        self.requests.append(
            {
                "address": request.url.host,
                "host": host,
                "sni": request.extensions.get("sni_hostname"),
                "path": request.url.path,
                "accept_encoding": request.headers.get("accept-encoding"),
            }
        )
        page = self.pages.get((host, request.url.path))
        if page is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, content=b"")
        self.clock += page["wait"]
        if page["chunks"] is not None:
            return httpx.Response(
                page["status"], headers=page["headers"], content=self._stream(page["chunks"])
            )
        body = page["html"].encode()
        self.pulled += len(body)
        return httpx.Response(page["status"], headers=page["headers"], content=body)


class CapturedCelery:
    """Every `apply_async` (and so `delay`) a test makes, recorded and not
    sent: `[{task, args, kwargs, options}]`."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def install(self, monkeypatch) -> CapturedCelery:
        def apply_async(task, args=None, kwargs=None, **options):
            self.sent.append(
                {
                    "task": task.name,
                    "args": list(args or ()),
                    "kwargs": dict(kwargs or {}),
                    "options": options,
                }
            )

        monkeypatch.setattr(Task, "apply_async", apply_async)
        return self

    def named(self, name: str) -> list[dict[str, Any]]:
        return [s for s in self.sent if s["task"] == name]


#: The labels a Labs mailbox has in `FakeGmailThread`.
THREAD_LABELS = [
    {"id": "INBOX", "name": "INBOX"},
    {"id": "Label_1", "name": "Sales"},
    {"id": "Label_2", "name": "Leads"},
    {"id": "CATEGORY_PERSONAL", "name": "CATEGORY_PERSONAL"},
    {"id": "CATEGORY_PROMOTIONS", "name": "CATEGORY_PROMOTIONS"},
    {"id": "SENT", "name": "SENT"},
    {"id": "TRASH", "name": "TRASH"},
    {"id": "SPAM", "name": "SPAM"},
]


class FakeGmailThread:
    """Spec 010 — `gmail.thread_meta` and `gmail.thread_text` over
    conversations added with `message()`, plus `gmail.labels` and
    `gmail.list_messages` (the recall check lists the Sales label) and
    `gmail._key` (the job's key check). `threads` maps a thread id to its
    messages as metadata (`{id, threadId, labelIds, internalDate,
    headers}`); `texts` a message id to its plain text, or to `{text,
    attachments}`. `failing` maps an id (a thread's for the metadata read, a
    message's for the text read) to the exception that read raises. Every
    metadata read is in `meta_reads` (thread ids), every text read in
    `text_reads` (message ids)."""

    def __init__(
        self,
        threads: dict[str, list[dict[str, Any]]] | None = None,
        texts: dict[str, Any] | None = None,
        *,
        failing: dict[str, BaseException] | None = None,
    ) -> None:
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        self.texts = dict(texts or {})
        self.failing = dict(failing or {})
        self.meta_reads: list[str] = []
        self.text_reads: list[str] = []
        self.listed: list[str] = []

    def install(self, monkeypatch, *, key: bool = True) -> FakeGmailThread:
        monkeypatch.setattr(gmail, "thread_meta", self.thread_meta)
        monkeypatch.setattr(gmail, "thread_text", self.thread_text)
        monkeypatch.setattr(gmail, "labels", lambda mailbox: [dict(lb) for lb in THREAD_LABELS])
        monkeypatch.setattr(gmail, "list_messages", self.list_messages)
        monkeypatch.setattr(gmail, "_key", self._key if key else self._no_key)
        return self

    @staticmethod
    def _key() -> dict[str, Any]:
        return {"client_email": "sync@example.iam", "private_key": "-"}

    @staticmethod
    def _no_key() -> dict[str, Any]:
        raise gmail.NotConfigured("The Gmail service-account key is not set in this process.")

    def message(
        self,
        thread_id: str,
        message_id: str,
        *,
        sender: str,
        to: str,
        at: datetime,
        labels: tuple[str, ...] = ("INBOX", "CATEGORY_PERSONAL"),
        text: str = "",
        attachments: tuple[str, ...] = (),
        subject: str = "Hello",
        **headers: str,
    ) -> dict[str, Any]:
        """A message of a conversation, its text and attachment names."""
        msg = {
            "id": message_id,
            "threadId": thread_id,
            "labelIds": list(labels),
            "internalDate": str(int(at.timestamp() * 1000)),
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                *({"name": k.replace("_", "-"), "value": v} for k, v in headers.items()),
            ],
        }
        self.threads.setdefault(thread_id, []).append(msg)
        self.texts[message_id] = {"text": text, "attachments": list(attachments)}
        return msg

    def thread_meta(self, mailbox: str, thread_id: str) -> dict[str, Any]:
        self.meta_reads.append(thread_id)
        if thread_id in self.failing:
            raise self.failing[thread_id]
        msgs = self.threads.get(thread_id)
        if not msgs:
            return {}
        ordered = sorted(msgs, key=lambda m: int(m["internalDate"]))
        return {"id": thread_id, "messages": [dict(m) for m in ordered]}

    def thread_text(self, mailbox: str, message_ids: list[str]) -> list[dict[str, Any]]:
        out = []
        for message_id in message_ids:
            self.text_reads.append(message_id)
            if message_id in self.failing:
                raise self.failing[message_id]
            if message_id not in self.texts:
                continue
            found = self.texts[message_id]
            if isinstance(found, str):
                found = {"text": found, "attachments": []}
            out.append({"id": message_id, **found})
        return out

    def list_messages(self, mailbox, label_id, *, after, page_token, page_size):
        self.listed.append(label_id)
        found = sorted(
            (
                m
                for msgs in self.threads.values()
                for m in msgs
                if label_id in m["labelIds"] and int(m["internalDate"]) // 1000 > after
            ),
            key=lambda m: m["id"],
        )
        return {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in found]}


def ist(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A wall-clock time in Asia/Kolkata, as an aware UTC time."""
    return datetime(year, month, day, hour, minute, tzinfo=IST).astimezone(UTC)


def fix_clock(monkeypatch, now: datetime, *modules: Any) -> None:
    """Sets each module's `_now()` to `now`."""
    for module in modules:
        monkeypatch.setattr(module, "_now", lambda: now)


class SlowCalls:
    """Every model call takes `seconds` on the elapsed-time clock
    (`jobs.monotonic`), so a test can run an AI task out of its time
    (FR-CRMAI-JOB-01, amended in S5). Install it after the `FakeClaude`
    whose answers it passes on."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.now = 0.0

    def install(self, monkeypatch) -> SlowCalls:
        from app.services.crm import jobs

        answer = claude.messages

        def messages(**kwargs: Any) -> claude.Reply:
            self.now += self.seconds
            return answer(**kwargs)

        monkeypatch.setattr(jobs, "monotonic", lambda: self.now)
        monkeypatch.setattr(claude, "messages", messages)
        return self
