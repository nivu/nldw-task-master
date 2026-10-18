"""Fakes for the AI jobs — spec 009 tasks "How to use this file"; NFR-CRMAI-09.

No test calls Claude, Fireflies, Gmail or a website. `FakeClaude` stands in
for `services/crm/claude.messages`: it answers from a script, can answer
with output that fails the schema, time out or fail, and records every
prompt it was given, so a test can check what reached the model.
`FakeGmailText` stands in for `services/crm/gmail.message_text` and records
every message id it was asked for, so a test can check that only stored
messages are read. `FakeFireflies` stands in for `services/crm/fireflies`'s
two reads and records every transcript fetch, so a test can check that an
internal or unmatched meeting is never downloaded. `CapturedCelery` records every task a test enqueues
instead of sending it.
The clock helpers build fixed times on the Asia/Kolkata wall clock, where
the budget month turns (Q-23).

Route and store tests use `tests/crm_fake.py`; these sit beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from celery.app.task import Task

from app.services.crm import claude, fireflies, gmail

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


def ist(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A wall-clock time in Asia/Kolkata, as an aware UTC time."""
    return datetime(year, month, day, hour, minute, tzinfo=IST).astimezone(UTC)


def fix_clock(monkeypatch, now: datetime, *modules: Any) -> None:
    """Sets each module's `_now()` to `now`."""
    for module in modules:
        monkeypatch.setattr(module, "_now", lambda: now)
