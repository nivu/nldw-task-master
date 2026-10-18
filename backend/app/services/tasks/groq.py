"""Speech to text — spec 008 FR-TASK-VOICE-02; T10; NFR-TASK-07; plan §2.1.

One `httpx` multipart POST to Groq's OpenAI-compatible transcription
endpoint with `whisper-large-v3`; no vendor SDK (NFR-TASK-07). The key
(`GROQ_API_KEY`) is held only by the worker and never logged: a failure
raises `TranscriptionFailed` naming the HTTP status alone, never the key,
the request or Groq's response body.

The audio is stored without an extension (`<user_id>/<id>`), so its format
is read from its first bytes and the upload named to match: Groq tells
formats apart by the file name.
"""

from __future__ import annotations

from typing import Final

import httpx

from app.config import settings

URL: Final = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL: Final = "whisper-large-v3"
TIMEOUT: Final = httpx.Timeout(120.0, connect=10.0)


class TranscriptionFailed(Exception):
    """Groq did not return a transcript. The message is safe to log."""


def file_name(audio: bytes) -> str:
    """`voice.<ext>` for the audio's container, from its magic bytes; WebM
    (what browsers record) when unknown."""
    if audio[:4] == b"OggS":
        return "voice.ogg"
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return "voice.wav"
    if audio[4:8] == b"ftyp":
        return "voice.m4a"
    if audio[:3] == b"ID3" or (len(audio) > 1 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0):
        return "voice.mp3"
    return "voice.webm"


def transcribe(audio: bytes) -> str:
    """The text of `audio`. Raises `TranscriptionFailed` when no key is set,
    Groq cannot be reached or does not answer 200 with a `text`."""
    key = settings.GROQ_API_KEY.get_secret_value() if settings.GROQ_API_KEY else ""
    if not key:
        raise TranscriptionFailed("no Groq key is set")
    try:
        response = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {key}"},
            data={"model": MODEL, "response_format": "json"},
            files={"file": (file_name(audio), audio)},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise TranscriptionFailed(f"Groq could not be reached ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise TranscriptionFailed(f"Groq answered {response.status_code}")
    try:
        text = response.json()["text"]
    except (ValueError, KeyError, TypeError):
        raise TranscriptionFailed("Groq's answer had no text") from None
    if not isinstance(text, str):
        raise TranscriptionFailed("Groq's answer had no text")
    return text
