"""Quick add — spec 008 FR-TASK-ADD-02 to -04, FR-TASK-CHG-06; T7;
contracts §1.2; plan §5.6.

One line in, a draft and any questions out, by fixed rules, not AI (T7).
Matched words are removed from the title. The preview asks instead of
guessing (FR-TASK-ADD-04); while a question is open nothing is saved.

| Token | Result | Asks when |
|---|---|---|
| `#word` | tag `word` | — |
| `@name` | assignee: display name (whole or first name) or email | no match, or several |
| `today`, `tomorrow` | that date | — |
| weekday or three-letter form | the next such day after today | it is today's weekday |
| `next <weekday>` | — | always |
| `next week` | next Monday | — |
| `YYYY-MM-DD`, `D Mon`, `D Month` | that date | it has passed (`D Mon`: this year's) |
| `D/M` | day first | both parts ≤ 12 and differ (a reading passed this year is next year's) |

Bucket: This week when the due date is after today and within the
caller's current Monday–Sunday week; else FR-TASK-DATA-08.

The first date and the first `@name` count; a later one stays in the title.

Pure: the route passes `today`, the caller's date in their own zone `zone`
(FR-TASK-CHG-06), and the people they may assign (`GET /tasks/people`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Final, Literal

from app.domain.tasks import rules

WEEKDAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_ISO: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DAY_MONTH: Final = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_DAY: Final = re.compile(r"^\d{1,2}$")


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class Question:
    """Contracts §1 — `{kind, prompt, options}`. A `date` answer is a
    `YYYY-MM-DD`; an `assignee` answer is a person's id."""

    kind: Literal["date", "assignee"]
    prompt: str
    options: tuple[Option, ...] = ()


@dataclass(frozen=True)
class Parsed:
    """The draft. `assignee` is the person `@name` matched, `{id, name}`;
    None means the caller. `due_on` is None while a date question is open."""

    title: str
    due_on: date | None
    bucket: str
    tags: tuple[str, ...]
    assignee: dict[str, str] | None
    questions: tuple[Question, ...]


def _weekday(word: str) -> int | None:
    for i, name in enumerate(WEEKDAYS):
        if word in (name, name[:3]):
            return i
    return None


def _month(word: str) -> int | None:
    for i, name in enumerate(MONTHS):
        if word in (name, name[:3]):
            return i + 1
    return None


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _short(day: date) -> str:
    """`23 Oct`."""
    return f"{day.day} {MONTHS[day.month - 1][:3].capitalize()}"


def _long(day: date) -> str:
    """`Fri 23 Oct 2026`."""
    return f"{WEEKDAYS[day.weekday()][:3].capitalize()} {_short(day)} {day.year}"


def _on(day: date, label: str | None = None) -> Option:
    return Option(day.isoformat(), label or _long(day))


def weekday_name(day: date) -> str:
    """`Friday` — the draft's `due_weekday`."""
    return WEEKDAYS[day.weekday()].capitalize()


def bucket_for(due_on: date | None, today: date) -> str:
    """FR-TASK-ADD-03 — This week when due after today and by this Sunday;
    else FR-TASK-DATA-08 (Today when due today or earlier, else Later)."""
    if due_on is not None and today < due_on <= rules.week_of(today)[1]:
        return "this_week"
    return rules.default_bucket(due_on, today)


def _passed(shown: str, day: date, *others: date) -> Question:
    """FR-TASK-ADD-04 — a date that has passed: keep it, or another."""
    return Question(
        "date",
        f"{shown} has passed: keep it or choose another date?",
        (_on(day, f"Keep {_long(day)}"), *(_on(d) for d in others)),
    )


def _this_year(day: int, month: int, today: date) -> tuple[date | None, Question | None]:
    """A date with no year: this year's, asked about when it has passed."""
    found = _date(today.year, month, day)
    if found is None or found >= today:
        return found, None
    return None, _passed(_short(found), found, *filter(None, [_date(today.year + 1, month, day)]))


Read = tuple[int, date | None, Question | None]


def _read_date(words: list[str], i: int, today: date) -> Read:
    """The date at `words[i]`: how many words it takes (0: not a date), and
    the date, or the question to ask instead."""
    word = words[i].lower()
    after = words[i + 1].lower() if i + 1 < len(words) else ""
    if word == "today":
        return 1, today, None
    if word == "tomorrow":
        return 1, today + timedelta(days=1), None
    if word == "next" and after == "week":
        return 2, today + timedelta(days=7 - today.weekday()), None
    if word == "next" and (wd := _weekday(after)) is not None:
        first = today + timedelta(days=(wd - today.weekday() - 1) % 7 + 1)
        second = first + timedelta(days=7)
        prompt = f"Next {WEEKDAYS[wd].capitalize()}: {_short(first)} or {_short(second)}?"
        return 2, None, Question("date", prompt, (_on(first), _on(second)))
    if (wd := _weekday(word)) is not None:
        if wd != today.weekday():
            return 1, today + timedelta(days=(wd - today.weekday()) % 7), None
        name = WEEKDAYS[wd].capitalize()
        later = today + timedelta(days=7)
        options = (
            _on(today, f"Today, {_long(today)}"),
            _on(later, f"Next {name}, {_long(later)}"),
        )
        return 1, None, Question("date", f"Today or next {name}?", options)
    if (m := _ISO.match(word)) and (found := _date(*map(int, m.groups()))):
        if found >= today:
            return 1, found, None
        return 1, None, _passed(word, found)
    if m := _DAY_MONTH.match(word):
        a, b = map(int, m.groups())
        if a <= 12 and b <= 12 and a != b:
            readings = [d for d in (_date(today.year, b, a), _date(today.year, a, b)) if d]
            if not readings:
                return 0, None, None
            # FR-TASK-ADD-04 — a reading that has passed this year is next year's.
            readings = [d if d >= today else d.replace(year=d.year + 1) for d in readings]
            prompt = " or ".join(_short(d) for d in readings) + "?"
            return 1, None, Question("date", prompt, tuple(_on(d) for d in readings))
        day, month = (a, b) if b <= 12 else (b, a)
        if month > 12 or _date(today.year, month, day) is None:
            return 0, None, None
        return (1, *_this_year(day, month, today))
    if _DAY.match(word) and (month := _month(after)) and _date(today.year, month, int(word)):
        return (2, *_this_year(int(word), month, today))
    return 0, None, None


def _match(name: str, people: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """`@name` — an email, a whole display name or a first name,
    case-insensitive."""
    wanted = name.lower()
    return [
        p
        for p in people
        if wanted
        in (
            (p.get("email") or "").lower(),
            (p.get("display_name") or "").lower(),
            ((p.get("display_name") or "").split() or [""])[0].lower(),
        )
    ]


def _assignee(name: str, people: Iterable[dict[str, Any]]) -> tuple[dict | None, Question | None]:
    found = _match(name, people)
    if len(found) == 1:
        return {"id": found[0]["id"], "name": found[0]["display_name"]}, None
    if not found:
        return None, Question("assignee", f"No one you can assign is called {name}: who is it for?")
    options = tuple(
        Option(p["id"], f"{p['display_name']} ({p['email']})")
        for p in sorted(found, key=lambda p: ((p.get("display_name") or "").lower(), p["id"]))
    )
    return None, Question("assignee", f"Which {name}?", options)


def parse(text: str, today: date, people: Iterable[dict[str, Any]], zone: str) -> Parsed:
    """Contracts §1.2 — the draft of `text` for a caller whose date is `today`
    in `zone`; `people` are those they may assign (`id`, `display_name`,
    `email`)."""
    people = list(people)
    words = text.split()
    title: list[str] = []
    tags: list[str] = []
    due: date | None = None
    assignee: dict[str, str] | None = None
    questions: list[Question] = []
    dated = named = False
    i = 0
    while i < len(words):
        word = words[i]
        if word.startswith("#") and len(word) > 1:
            if word[1:] not in tags:
                tags.append(word[1:])
            i += 1
            continue
        if word.startswith("@") and len(word) > 1 and not named:
            named = True
            assignee, asked = _assignee(word[1:], people)
            questions += [asked] if asked else []
            i += 1
            continue
        taken, found, asked = (0, None, None) if dated else _read_date(words, i, today)
        if taken:
            dated = True
            due = found
            questions += [asked] if asked else []
            i += taken
            continue
        title.append(word)
        i += 1
    return Parsed(
        title=" ".join(title),
        due_on=due,
        bucket=bucket_for(due, today),
        tags=tuple(tags),
        assignee=assignee,
        questions=tuple(questions),
    )


def answer(
    parsed: Parsed,
    today: date,
    *,
    due_on: date | None = None,
    assignee: dict[str, str] | None = None,
) -> Parsed:
    """Contracts §1 `answers` — a chosen date closes the date question, a
    chosen person the assignee question; the bucket follows the date."""
    if due_on is not None:
        parsed = replace(
            parsed,
            due_on=due_on,
            bucket=bucket_for(due_on, today),
            questions=tuple(q for q in parsed.questions if q.kind != "date"),
        )
    if assignee is not None:
        parsed = replace(
            parsed,
            assignee=assignee,
            questions=tuple(q for q in parsed.questions if q.kind != "assignee"),
        )
    return parsed
