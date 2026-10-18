"""Splitting a voice note — spec 008 FR-TASK-VOICE-03; TP-22; Journey 4.10.

Pure, at the fixed clock of spec §12: Tuesday 20 October 2026, Asia/Kolkata.
Fixed rules, not AI: sentences, then "and" / "also" joins; a candidate
starting "idea" or "read", or with a link, is an inbox item; each candidate
is parsed by the quick-add rules.

Acceptance criteria (spec §12, phase 4):
- "A voice note becomes candidates shown for confirmation, split by the
  fixed rules of FR-TASK-VOICE-03" — TestJourney, TestSentences, TestJoins,
  TestInboxOrTask, TestQuickAddRules (the route half is in
  `test_task_voice_routes.py`).
"""

from __future__ import annotations

from datetime import date

from app.domain.tasks import voice_split

TODAY = date(2026, 10, 20)  # a Tuesday
IST = "Asia/Kolkata"
PEOPLE = [{"id": "p-kiran", "display_name": "Kiran", "email": "kiran@nunnarilabs.com"}]


def _split(text: str) -> list[tuple[str, str]]:
    return [(c.kind, c.text) for c in voice_split.split(text)]


def _inbox(text: str) -> dict:
    return voice_split.inbox_draft(text, TODAY, PEOPLE, IST)


class TestJourney:
    """Journey 4.10 — one task due tomorrow and one idea."""

    def test_the_note_of_journey_4_10(self):
        text = (
            "follow up with Kiran on the contract tomorrow, and an idea: a webinar on agent evals"
        )
        assert _split(text) == [
            ("task", "follow up with Kiran on the contract tomorrow"),
            ("inbox", "an idea: a webinar on agent evals"),
        ]
        task = voice_split.task_draft(
            "follow up with Kiran on the contract tomorrow", TODAY, PEOPLE, IST
        )
        assert (task.title, task.due_on, task.questions) == (
            "follow up with Kiran on the contract",
            date(2026, 10, 21),
            (),
        )
        assert _inbox("an idea: a webinar on agent evals") == {
            "kind": "idea",
            "title": "a webinar on agent evals",
            "url": None,
            "tags": [],
        }


class TestSentences:
    def test_each_sentence_is_a_candidate(self):
        assert _split("Call Deepa. Send the deck! Is the venue booked?") == [
            ("task", "Call Deepa"),
            ("task", "Send the deck"),
            ("task", "Is the venue booked"),
        ]

    def test_a_line_break_ends_a_sentence(self):
        assert _split("Call Deepa\nSend the deck") == [
            ("task", "Call Deepa"),
            ("task", "Send the deck"),
        ]

    def test_a_link_s_dots_do_not_end_a_sentence(self):
        assert _split("read https://example.com/a.html. Call Deepa") == [
            ("inbox", "read https://example.com/a.html"),
            ("task", "Call Deepa"),
        ]

    def test_an_empty_note_has_no_candidates(self):
        assert _split("") == []
        assert _split("  .  ") == []


class TestJoins:
    def test_and_splits_a_sentence(self):
        assert _split("book flights and renew the passport") == [
            ("task", "book flights"),
            ("task", "renew the passport"),
        ]

    def test_also_splits_a_sentence(self):
        assert _split("Call Deepa. Also, email Kiran") == [
            ("task", "Call Deepa"),
            ("task", "email Kiran"),
        ]

    def test_and_also_is_one_join(self):
        assert _split("book flights and also renew the passport") == [
            ("task", "book flights"),
            ("task", "renew the passport"),
        ]

    def test_and_inside_a_word_is_not_a_join(self):
        assert _split("ask Sandy about Android") == [("task", "ask Sandy about Android")]

    def test_and_or_also_inside_a_link_is_not_a_join(self):
        assert _split("Read https://blog.example.com/tips-and-tricks later.") == [
            ("inbox", "Read https://blog.example.com/tips-and-tricks later"),
        ]
        assert _split("read www.example.com/also and call Deepa") == [
            ("inbox", "read www.example.com/also"),
            ("task", "call Deepa"),
        ]

    def test_the_candidates_are_numbered_in_order(self):
        assert [c.index for c in voice_split.split("a and b. c")] == [0, 1, 2]


class TestInboxOrTask:
    def test_a_candidate_starting_idea_is_an_inbox_idea(self):
        assert _split("Idea: a podcast on evals") == [("inbox", "Idea: a podcast on evals")]
        assert _inbox("Idea: a podcast on evals")["kind"] == "idea"
        assert _inbox("Idea: a podcast on evals")["title"] == "a podcast on evals"

    def test_a_candidate_starting_read_is_an_inbox_item(self):
        assert _split("Read the Q3 report") == [("inbox", "Read the Q3 report")]
        assert _inbox("Read the Q3 report") == {
            "kind": "note",
            "title": "Read the Q3 report",
            "url": None,
            "tags": [],
        }

    def test_a_candidate_with_a_link_is_an_inbox_link(self):
        assert _split("look at https://example.com/x") == [
            ("inbox", "look at https://example.com/x")
        ]
        assert _inbox("look at https://example.com/x") == {
            "kind": "link",
            "title": "look at",
            "url": "https://example.com/x",
            "tags": [],
        }

    def test_a_link_alone_is_its_own_title(self):
        drafted = _inbox("read https://example.com/post")
        assert (drafted["title"], drafted["url"]) == (
            "https://example.com/post",
            "https://example.com/post",
        )

    def test_a_www_link_is_given_https(self):
        assert _inbox("check www.nunnari.ai")["url"] == "https://www.nunnari.ai"

    def test_idea_or_read_only_count_at_the_start(self):
        assert _split("share the idea with Kiran") == [("task", "share the idea with Kiran")]
        assert _split("Kiran will read it") == [("task", "Kiran will read it")]

    def test_every_other_candidate_is_a_task(self):
        assert _split("send the invoice") == [("task", "send the invoice")]


class TestQuickAddRules:
    def test_a_spoken_weekday_is_the_due_date(self):
        parsed = voice_split.task_draft("send the invoice friday", TODAY, PEOPLE, IST)
        assert (parsed.title, parsed.due_on, parsed.bucket) == (
            "send the invoice",
            date(2026, 10, 23),
            "this_week",
        )

    def test_a_question_is_asked_not_guessed(self):
        parsed = voice_split.task_draft("call Kiran tuesday", TODAY, PEOPLE, IST)
        assert parsed.due_on is None and [q.kind for q in parsed.questions] == ["date"]

    def test_an_inbox_title_loses_its_date_words(self):
        assert _inbox("idea: a webinar tomorrow")["title"] == "a webinar"
