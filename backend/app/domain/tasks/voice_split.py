"""Splitting a voice note — spec 008 FR-TASK-VOICE-03; TP-22; Journey 4.10;
plan §5.10.

A transcript becomes candidates by fixed rules, not AI (TP-22):

1. It is cut into sentences: at `.`, `!` or `?` followed by a space or the
   end (so a link's dots stay whole), and at line breaks.
2. Each sentence is cut again at every "and" / "also" join outside a link
   (so a link's "-and-" or "/also" stays whole), and the commas around a
   cut are dropped.
3. A candidate starting "idea" (after "a", "an" or "another") or "read", or
   containing a link, is an **inbox** item; every other candidate is a
   **task** candidate.
4. Each candidate is parsed by the quick-add rules (`quick_add.parse`), so a
   spoken "tomorrow" becomes the due date exactly as typed one would.

An inbox item is an idea when it starts "idea", a link when it has one, and
otherwise a note (a "read" with nothing to open). Its title drops the
"idea" lead-in and the link; a link with nothing else said, or only "read",
is its own title. A `www.` link is given `https://`.

Pure: the service passes the caller's `today`, `zone` and the people they
may assign, as quick add's route does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from app.domain.tasks import quick_add

_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+|\n+")
_JOIN: Final = re.compile(r"\s*,?\s*\b(?:and|also)\b\s*,?\s*", re.IGNORECASE)
_LINK: Final = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)
_IDEA: Final = re.compile(r"^(?:(?:a|an|another)\s+)?ideas?\b[\s:,.\-–—]*", re.IGNORECASE)
_READ: Final = re.compile(r"^read\b", re.IGNORECASE)
#: Punctuation trimmed from a candidate's ends (a sentence's last stop, the
#: comma of a join); a link's own characters are kept.
_TRIM: Final = " \t,;:.!?-–—"


@dataclass(frozen=True)
class Candidate:
    """One line of the note: `index` in the order spoken, `kind` by rule 3."""

    index: int
    kind: Literal["task", "inbox"]
    text: str


def _link(text: str) -> str | None:
    found = _LINK.search(text)
    return found.group(0).rstrip(".,;:!?)") if found else None


def kind_of(text: str) -> Literal["task", "inbox"]:
    """Rule 3 — inbox for an idea, a read or a link; else a task."""
    if _IDEA.match(text) or _READ.match(text) or _link(text):
        return "inbox"
    return "task"


def _joins(sentence: str) -> list[str]:
    """Rule 2 — the sentence cut at its joins, each link kept whole."""
    parts = [""]
    at = 0
    for link in _LINK.finditer(sentence):
        first, *rest = _JOIN.split(sentence[at : link.start()])
        parts[-1] += first
        parts += rest
        parts[-1] += link.group(0)
        at = link.end()
    first, *rest = _JOIN.split(sentence[at:])
    parts[-1] += first
    return parts + rest


def split(transcript: str) -> list[Candidate]:
    """Rules 1 to 3 — the transcript's candidates, in order; blank pieces
    are left out."""
    pieces: list[str] = []
    for sentence in _SENTENCE.split(transcript):
        for part in _joins(sentence):
            text = part.strip(_TRIM)
            if text:
                pieces.append(text)
    return [Candidate(i, kind_of(text), text) for i, text in enumerate(pieces)]


def task_draft(
    text: str, today: date, people: Iterable[dict[str, Any]], zone: str
) -> quick_add.Parsed:
    """Rule 4 for a task candidate: its quick-add draft and questions."""
    return quick_add.parse(text, today, people, zone)


def inbox_draft(
    text: str, today: date, people: Iterable[dict[str, Any]], zone: str
) -> dict[str, Any]:
    """Rule 4 for an inbox candidate: `{kind, title, url, tags}`. The title
    is the quick-add title of the text without its "idea" lead-in and link."""
    found = _link(text)
    # The inbox keeps `http(s)://` links only (data-model §2.10).
    url = found if not found or found.lower().startswith("http") else f"https://{found}"
    idea = _IDEA.match(text)
    rest = text[idea.end() :] if idea else text
    if found:
        rest = rest.replace(found, " ")
    parsed = quick_add.parse(rest, today, people, zone)
    title = parsed.title.strip(_TRIM)
    if not title or (url and title.lower() == "read"):
        title = url or text
    kind = "idea" if idea else "link" if url else "note"
    return {"kind": kind, "title": title, "url": url, "tags": list(parsed.tags)}
