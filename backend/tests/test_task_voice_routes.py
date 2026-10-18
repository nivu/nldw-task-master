"""Voice capture through the routes and the worker — spec 008
FR-TASK-VOICE-01 to -05; FR-TASK-ROLL-03; Journey 4.10; contracts §1
"Route order", §2 voice routes; plan §5.8, §5.10.

On the shared task world with phase 4 on for the cast (Tuesday 20 October
2026, noon IST). Groq is faked (`groq.transcribe`), and so are the upload
row and the private bucket (`FakeVoice`, the functions of
`services/tasks/voice.py` that reach Supabase); the real table and bucket
are covered by `tests/db/test_task_voice_db.py`. The transcription job is
run by calling the Celery task directly, as the worker would.

Acceptance criteria (spec §12, phase 4):
- "A voice note becomes candidates shown for confirmation, split by the
  fixed rules of FR-TASK-VOICE-03; nothing is saved before; the audio is
  gone after transcription" — TestJourney, TestNothingBeforeConfirm,
  TestTheAudio.
- "The sweep job deletes the audio and transcript of a failed transcription
  within 24 hours" — TestTheSweep.
- "The transcript is deleted when the last candidate is confirmed or
  dropped, and is never in the Celery result backend or logs" —
  TestTheTranscript.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from tasks_fake import ID, TaskWorld

from app.config import settings
from app.services.tasks import groq, voice
from app.tasks import task_jobs
from app.worker import celery_app

NOT_ON = "Tasks are not switched on for you yet."
JOURNEY = "follow up with Kiran on the contract tomorrow, and an idea: a webinar on agent evals"
AUDIO = b"\x1aE\xdf\xa3 a webm voice note"
CREATED = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


class FakeVoice:
    """`voice_uploads` and the `task-voice` bucket, in memory."""

    def __init__(self, monkeypatch) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.enqueued: list[str] = []
        self.transcript = JOURNEY
        self.fail: Exception | None = None
        self.heard: list[bytes] = []
        for name in (
            "put_audio",
            "get_audio",
            "delete_audio",
            "insert_upload",
            "get_upload",
            "update_upload",
            "mark_candidate",
            "finish_upload",
            "expired_uploads",
            "enqueue",
        ):
            monkeypatch.setattr(voice, name, getattr(self, name))
        monkeypatch.setattr(groq, "transcribe", self.transcribe)
        monkeypatch.setattr(settings, "TASKS_VOICE_CONFIGURED", True)

    def put_audio(self, path: str, content: bytes, content_type: str) -> None:
        self.objects[path] = (content, content_type)

    def get_audio(self, path: str) -> bytes:
        return self.objects[path][0]

    def delete_audio(self, path: str) -> None:
        self.objects.pop(path, None)

    def insert_upload(self, row: dict[str, Any]) -> dict[str, Any]:
        full = {
            "transcript": None,
            "candidates": None,
            "error_public": None,
            "created_at": CREATED.isoformat(),
            "transcribed_at": None,
            **row,
        }
        self.rows[row["id"]] = full
        return full

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        row = self.rows.get(upload_id)
        return dict(row) if row else None

    def update_upload(self, upload_id: str, values: dict[str, Any]) -> None:
        self.rows[upload_id].update(values)

    def mark_candidate(self, upload_id: str, index: int, handled: bool) -> bool:
        """`voice_candidate_mark` (054): only while the transcript is held and
        the flag is still the other way."""
        row = self.rows.get(upload_id)
        if row is None or row["status"] != "transcribed" or row["transcript"] is None:
            return False
        found = next((c for c in row["candidates"] if c["index"] == index), None)
        if found is None or found["handled"] is handled or found["text"] is None:
            return False
        row["candidates"] = [
            {**c, "handled": handled} if c["index"] == index else c for c in row["candidates"]
        ]
        return True

    def finish_upload(self, upload_id: str) -> bool:
        """`voice_upload_finish` (054): only once every candidate is handled."""
        row = self.rows[upload_id]
        if row["status"] != "transcribed" or not all(c["handled"] for c in row["candidates"]):
            return False
        row.update(
            status="done",
            transcript=None,
            candidates=[{**c, "text": None} for c in row["candidates"]],
        )
        return True

    def expired_uploads(self, before: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.rows.values()
            if r["created_at"] < before and (r["transcript"] is not None or r["object_path"])
        ]

    def enqueue(self, upload_id: str) -> None:
        self.enqueued.append(upload_id)

    def transcribe(self, audio: bytes) -> str:
        self.heard.append(audio)
        if self.fail:
            raise self.fail
        return self.transcript


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 4 on for everyone who has phase 1."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 4
    return w


@pytest.fixture
def fake(monkeypatch) -> FakeVoice:
    return FakeVoice(monkeypatch)


def _upload(world, who: str = "user", content: bytes = AUDIO, mime: str = "audio/webm"):
    return world.call(who, "POST", "/voice", files={"file": ("note.webm", content, mime)})


def _uploaded(world, fake, who: str = "user") -> str:
    r = _upload(world, who)
    assert r.status_code == 202, r.text
    return r.json()["upload_id"]


def _transcribed(world, fake, who: str = "user") -> str:
    upload_id = _uploaded(world, fake, who)
    assert task_jobs.transcribe_voice(upload_id) is None
    return upload_id


def _read(world, upload_id: str, who: str = "user") -> dict:
    r = world.call(who, "GET", f"/voice/{upload_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _detail(r) -> tuple[int, str]:
    return r.status_code, r.json().get("detail")


class TestJourney:
    """Journey 4.10 — one task due tomorrow and one idea, one tap each."""

    def test_the_note_becomes_a_task_and_an_idea(self, world, fake):
        upload_id = _transcribed(world, fake)
        out = _read(world, upload_id)
        assert out["status"] == "transcribed" and out["error"] is None
        task, idea = out["candidates"]
        assert (task["index"], task["kind"], task["handled"]) == (0, "task", False)
        assert task["draft"]["title"] == "follow up with Kiran on the contract"
        assert (task["draft"]["due_on"], task["draft"]["due_weekday"]) == (
            "2026-10-21",
            "Wednesday",
        )
        assert task["draft"]["assignee"]["id"] == ID["user"] and task["questions"] == []
        assert (idea["kind"], idea["draft"]["kind"], idea["draft"]["title"]) == (
            "inbox",
            "idea",
            "a webinar on agent evals",
        )

    def test_confirming_the_task_saves_it_with_source_voice(self, world, fake):
        upload_id = _transcribed(world, fake)
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["title"], out["due_on"], out["source"]) == (
            "follow up with Kiran on the contract",
            "2026-10-21",
            "voice",
        )
        assert out["assignee"]["id"] == ID["user"] and out["kind"] == "private"
        assert _read(world, upload_id)["candidates"][0]["handled"] is True

    def test_the_idea_waits_for_the_inbox(self, world, fake):
        """S4b builds the inbox; until then an inbox candidate can only be
        dropped."""
        upload_id = _transcribed(world, fake)
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 1})
        assert _detail(r) == (422, voice.INBOX_NOT_YET)
        assert _read(world, upload_id)["candidates"][1]["handled"] is False


class TestNothingBeforeConfirm:
    def test_upload_transcription_and_reading_save_no_task(self, world, fake):
        before = len(world.tasks)
        upload_id = _transcribed(world, fake)
        _read(world, upload_id)
        assert len(world.tasks) == before

    def test_a_dropped_candidate_saves_nothing(self, world, fake):
        before = len(world.tasks)
        upload_id = _transcribed(world, fake)
        r = world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 0})
        assert (r.status_code, r.json()) == (200, {"handled": True})
        assert len(world.tasks) == before

    def test_an_edited_candidate_is_saved_as_edited(self, world, fake):
        upload_id = _transcribed(world, fake)
        draft = {"title": "Call Kiran about the contract", "due_on": "2026-10-23"}
        r = world.call(
            "user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0, "draft": draft}
        )
        assert r.status_code == 200, r.text
        assert (r.json()["title"], r.json()["due_on"], r.json()["source"]) == (
            "Call Kiran about the contract",
            "2026-10-23",
            "voice",
        )

    def test_an_open_question_must_be_answered_first(self, world, fake):
        fake.transcript = "call Kiran tuesday"
        upload_id = _transcribed(world, fake)
        [candidate] = _read(world, upload_id)["candidates"]
        assert [q["kind"] for q in candidate["questions"]] == ["date"]
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (422, voice.QUESTION_OPEN)
        draft = {"title": "call Kiran", "due_on": "2026-10-27"}
        r = world.call(
            "user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0, "draft": draft}
        )
        assert r.status_code == 200, r.text

    def test_a_candidate_is_handled_once(self, world, fake):
        upload_id = _transcribed(world, fake)
        world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 0})
        for action in ("confirm", "drop"):
            r = world.call("user", "POST", f"/voice/{upload_id}/{action}", json={"index": 0})
            assert _detail(r) == (409, voice.ALREADY_HANDLED)

    def test_no_such_candidate(self, world, fake):
        upload_id = _transcribed(world, fake)
        r = world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 7})
        assert _detail(r) == (404, voice.NO_SUCH_CANDIDATE)

    def test_nothing_to_confirm_before_the_transcript(self, world, fake):
        upload_id = _uploaded(world, fake)
        assert _read(world, upload_id) == {"status": "uploaded", "candidates": [], "error": None}
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (409, voice.NOT_READY)

    def test_only_the_caller_s_own_note(self, world, fake):
        upload_id = _transcribed(world, fake)
        for method, path, body in (
            ("GET", f"/voice/{upload_id}", None),
            ("POST", f"/voice/{upload_id}/confirm", {"index": 0}),
            ("POST", f"/voice/{upload_id}/drop", {"index": 0}),
        ):
            r = world.call("owner", method, path, json=body)
            assert _detail(r) == (404, voice.NO_SUCH_UPLOAD)
        assert fake.rows[upload_id]["candidates"][0]["handled"] is False

    def test_a_line_too_long_for_a_title_asks_for_an_edit(self, world, fake):
        fake.transcript = "remind me to call the bank about the loan " + "really " * 30
        upload_id = _transcribed(world, fake)
        before = len(world.tasks)
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (422, voice.TOO_LONG)
        assert len(world.tasks) == before
        assert fake.rows[upload_id]["candidates"][0]["handled"] is False
        draft = {"title": "call the bank about the loan"}
        r = world.call(
            "user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0, "draft": draft}
        )
        assert r.status_code == 200, r.text

    def test_more_than_10_tags_asks_for_an_edit(self, world, fake):
        fake.transcript = "call Kiran " + " ".join(f"#t{i}" for i in range(11))
        upload_id = _transcribed(world, fake)
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (422, voice.TOO_LONG)
        assert fake.rows[upload_id]["candidates"][0]["handled"] is False

    def test_a_refused_task_leaves_the_candidate_open(self, world, fake, monkeypatch):
        upload_id = _transcribed(world, fake)

        def refuse(actor, payload):
            raise voice.TaskRefused(422, "refused")

        monkeypatch.setattr(voice.service, "create_task", refuse)
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (422, "refused")
        assert fake.rows[upload_id]["candidates"][0]["handled"] is False


class TestAtOnce:
    """Two requests on one note, or a request and the sweep, each read the
    row before the other wrote it (`stale`)."""

    def test_two_confirms_of_one_candidate_save_one_task(self, world, fake, monkeypatch):
        upload_id = _transcribed(world, fake)
        before = len(world.tasks)
        stale = fake.get_upload(upload_id)
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert r.status_code == 200, r.text
        monkeypatch.setattr(voice, "get_upload", lambda _id: dict(stale))
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (409, voice.ALREADY_HANDLED)
        assert len(world.tasks) == before + 1

    def test_a_confirm_and_a_drop_keep_both_flags(self, world, fake, monkeypatch):
        upload_id = _transcribed(world, fake)
        stale = fake.get_upload(upload_id)
        world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        monkeypatch.setattr(voice, "get_upload", lambda _id: dict(stale))
        r = world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 1})
        assert r.status_code == 200, r.text
        row = fake.rows[upload_id]
        assert [c["handled"] for c in row["candidates"]] == [True, True]
        assert (row["status"], row["transcript"]) == ("done", None)

    def test_a_note_swept_meanwhile_stays_cleared(self, world, fake, monkeypatch):
        upload_id = _transcribed(world, fake)
        stale = fake.get_upload(upload_id)
        monkeypatch.setattr(voice, "_now", lambda: CREATED + timedelta(hours=23, minutes=31))
        assert task_jobs.voice_sweep() == 1
        monkeypatch.setattr(voice, "get_upload", lambda _id: dict(stale))
        r = world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 0})
        assert _detail(r) == (409, voice.ALREADY_HANDLED)
        row = fake.rows[upload_id]
        assert row["transcript"] is None
        assert [c["text"] for c in row["candidates"]] == [None, None]


class TestTheAudio:
    def test_it_is_held_privately_until_transcribed(self, world, fake):
        upload_id = _uploaded(world, fake)
        path = f"{ID['user']}/{upload_id}"
        assert fake.rows[upload_id]["object_path"] == path
        assert fake.objects[path] == (AUDIO, "audio/webm")
        assert fake.enqueued == [upload_id]

    def test_it_is_deleted_after_transcription(self, world, fake):
        upload_id = _transcribed(world, fake)
        assert fake.heard == [AUDIO]
        assert fake.objects == {} and fake.rows[upload_id]["object_path"] is None

    def test_it_is_deleted_after_a_failure(self, world, fake):
        fake.fail = groq.TranscriptionFailed("Groq answered 500")
        upload_id = _transcribed(world, fake)
        assert fake.objects == {} and fake.rows[upload_id]["object_path"] is None
        out = _read(world, upload_id)
        assert out == {"status": "failed", "candidates": [], "error": voice.FAILED}

    def test_an_unexpected_failure_deletes_it_too(self, world, fake):
        fake.fail = RuntimeError("boom")
        upload_id = _transcribed(world, fake)
        assert fake.objects == {} and fake.rows[upload_id]["status"] == "failed"

    def test_nothing_heard_is_a_failure(self, world, fake):
        fake.transcript = "  "
        upload_id = _transcribed(world, fake)
        assert _read(world, upload_id)["error"] == voice.NOTHING_HEARD
        assert fake.objects == {}

    def test_a_job_run_twice_transcribes_once(self, world, fake):
        upload_id = _transcribed(world, fake)
        task_jobs.transcribe_voice(upload_id)
        assert len(fake.heard) == 1

    def test_a_browser_recording_s_codec_is_accepted(self, world, fake):
        r = _upload(world, mime="audio/webm;codecs=opus")
        assert r.status_code == 202, r.text

    def test_a_file_that_is_not_audio_is_refused(self, world, fake):
        r = _upload(world, content=b"%PDF", mime="application/pdf")
        assert _detail(r) == (415, voice.NOT_AUDIO)
        assert fake.rows == {} and fake.objects == {}

    def test_a_file_over_4_mb_answers_413(self, world, fake):
        r = _upload(world, content=b"\0" * (voice.MAX_BYTES + 1))
        assert _detail(r) == (413, voice.TOO_BIG)
        assert fake.rows == {} and fake.objects == {}

    def test_a_file_of_exactly_4_mb_is_taken(self, world, fake):
        assert _upload(world, content=b"\0" * voice.MAX_BYTES).status_code == 202

    def test_no_file_is_refused(self, world, fake):
        r = world.call("user", "POST", "/voice", files={"other": ("x", b"x", "audio/webm")})
        assert r.status_code == 422 and fake.rows == {}


class TestTheTranscript:
    def test_it_is_held_only_in_the_caller_s_row(self, world, fake):
        upload_id = _transcribed(world, fake)
        row = fake.rows[upload_id]
        assert row["transcript"] == JOURNEY and row["user_id"] == ID["user"]
        assert row["transcribed_at"] is not None

    def test_it_is_cleared_when_the_last_candidate_is_handled(self, world, fake):
        upload_id = _transcribed(world, fake)
        world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert fake.rows[upload_id]["transcript"] == JOURNEY
        world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 1})
        row = fake.rows[upload_id]
        assert (row["status"], row["transcript"]) == ("done", None)
        assert [c["text"] for c in row["candidates"]] == [None, None]
        assert [c["handled"] for c in _read(world, upload_id)["candidates"]] == [True, True]

    def test_it_is_never_in_the_celery_result(self, world, fake):
        upload_id = _uploaded(world, fake)
        assert task_jobs.transcribe_voice(upload_id) is None
        assert task_jobs.transcribe_voice.ignore_result is True

    def test_it_is_never_in_the_logs(self, world, fake, caplog):
        caplog.set_level(logging.DEBUG)
        fake.transcript = "Zqxvoice call Kiran. Idea: zqxidea"
        upload_id = _transcribed(world, fake)
        _read(world, upload_id)
        world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        world.call("user", "POST", f"/voice/{upload_id}/drop", json={"index": 1})
        fake.fail = groq.TranscriptionFailed("Groq answered 500")
        task_jobs.transcribe_voice(_uploaded(world, fake))
        assert caplog.records, "nothing was logged, so nothing was checked"
        text = "\n".join(
            f"{r.getMessage()} {r.args!r} {getattr(r, '__dict__', {})!r}" for r in caplog.records
        ).lower()
        assert "zqxvoice" not in text and "zqxidea" not in text
        assert upload_id in text


class TestTheSweep:
    def _at(self, monkeypatch, after: timedelta) -> None:
        monkeypatch.setattr(voice, "_now", lambda: CREATED + after)

    def test_a_failed_note_is_swept_before_24_hours(self, world, fake, monkeypatch):
        upload_id = _uploaded(world, fake)
        fake.rows[upload_id]["status"] = "transcribing"  # the worker died mid-way
        self._at(monkeypatch, timedelta(hours=23, minutes=31))
        assert task_jobs.voice_sweep() == 1
        row = fake.rows[upload_id]
        assert fake.objects == {} and row["object_path"] is None
        assert (row["status"], row["error_public"]) == ("failed", voice.EXPIRED)

    def test_a_transcript_is_swept_before_24_hours(self, world, fake, monkeypatch):
        upload_id = _transcribed(world, fake)
        self._at(monkeypatch, timedelta(hours=23, minutes=31))
        assert task_jobs.voice_sweep() == 1
        row = fake.rows[upload_id]
        assert (row["status"], row["transcript"]) == ("done", None)
        assert [c["text"] for c in row["candidates"]] == [None, None]
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert _detail(r) == (404, voice.NO_SUCH_CANDIDATE)

    def test_a_young_note_is_left(self, world, fake, monkeypatch):
        upload_id = _transcribed(world, fake)
        self._at(monkeypatch, timedelta(hours=23))
        assert task_jobs.voice_sweep() == 0
        assert fake.rows[upload_id]["transcript"] == JOURNEY

    def test_with_runs_every_30_minutes_nothing_outlives_24_hours(self):
        entry = celery_app.conf.beat_schedule["tasks-voice-sweep"]
        assert entry["task"] == "tasks.voice_sweep"
        assert entry["schedule"].minute == {0, 30}
        assert voice.KEEP + timedelta(minutes=30) <= timedelta(hours=24)

    def test_the_tasks_are_registered(self):
        assert {"tasks.transcribe_voice", "tasks.voice_sweep"} <= set(celery_app.tasks)


class TestTheSwitch:
    def test_503_while_unconfigured(self, world, fake, monkeypatch):
        monkeypatch.setattr(settings, "TASKS_VOICE_CONFIGURED", False)
        assert _detail(_upload(world)) == (503, voice.NOT_SET_UP)
        assert fake.rows == {}

    def test_below_phase_4_it_is_refused(self, world, fake):
        world.fake.profiles[ID["user"]]["tasks_level"] = 3
        assert _detail(_upload(world)) == (403, NOT_ON)
        r = world.call("user", "GET", f"/voice/{uuid.uuid4()}")
        assert _detail(r) == (403, NOT_ON)


class TestRouteOrder:
    """Contracts §1 — `/tasks/voice...` is never read as `/tasks/{id}`."""

    def test_voice_reaches_its_own_handler(self, world, fake, monkeypatch):
        monkeypatch.setattr(settings, "TASKS_VOICE_CONFIGURED", False)
        assert _detail(_upload(world)) == (503, voice.NOT_SET_UP)

    def test_a_voice_note_reaches_its_own_handler(self, world, fake):
        r = world.call("user", "GET", f"/voice/{uuid.uuid4()}")
        assert _detail(r) == (404, voice.NO_SUCH_UPLOAD)


class TestGroq:
    """FR-TASK-VOICE-02, NFR-TASK-07 — one `httpx` multipart POST, no SDK;
    the key never reaches a log or an exception."""

    KEY = "gsk_zqxsecret"

    def _post(self, monkeypatch, response: httpx.Response | Exception) -> list[dict]:
        calls: list[dict] = []

        def post(url, **kwargs):
            calls.append({"url": url, **kwargs})
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(settings, "GROQ_API_KEY", SecretStr(self.KEY))
        monkeypatch.setattr(httpx, "post", post)
        return calls

    def test_one_multipart_post_to_whisper_large_v3(self, monkeypatch):
        calls = self._post(monkeypatch, httpx.Response(200, json={"text": "call Kiran"}))
        assert groq.transcribe(AUDIO) == "call Kiran"
        [call] = calls
        assert call["url"] == groq.URL
        assert call["headers"] == {"Authorization": f"Bearer {self.KEY}"}
        assert call["data"]["model"] == "whisper-large-v3"
        assert call["files"] == {"file": ("voice.webm", AUDIO)}

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(401, json={"error": {"message": f"Invalid API Key {KEY}"}}),
            httpx.Response(200, json={"nothing": True}),
            httpx.ConnectError(f"refused for {KEY}"),
        ],
    )
    def test_a_failure_never_carries_the_key_or_the_body(self, monkeypatch, response):
        self._post(monkeypatch, response)
        with pytest.raises(groq.TranscriptionFailed) as raised:
            groq.transcribe(AUDIO)
        assert self.KEY not in str(raised.value) and "Invalid" not in str(raised.value)
        assert raised.value.__cause__ is None

    def test_no_key_no_call(self, monkeypatch):
        calls = self._post(monkeypatch, httpx.Response(200, json={"text": "x"}))
        monkeypatch.setattr(settings, "GROQ_API_KEY", None)
        with pytest.raises(groq.TranscriptionFailed):
            groq.transcribe(AUDIO)
        assert calls == []

    @pytest.mark.parametrize(
        ("audio", "name"),
        [
            (b"\x1aE\xdf\xa3....", "voice.webm"),
            (b"OggS....", "voice.ogg"),
            (b"RIFF\0\0\0\0WAVEfmt ", "voice.wav"),
            (b"\0\0\0\x20ftypM4A ", "voice.m4a"),
            (b"ID3\x04....", "voice.mp3"),
            (b"\xff\xfb\x90\x00", "voice.mp3"),
        ],
    )
    def test_the_file_is_named_for_its_format(self, audio, name):
        assert groq.file_name(audio) == name
