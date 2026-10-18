"""Quick add — spec 008 FR-TASK-ADD-02 to -04, FR-TASK-CHG-06; T7;
contracts §1.2.

Pure, at the fixed clock of spec §12: Tuesday 20 October 2026, Asia/Kolkata.

Acceptance criteria (spec §12, phase 1):
- "Quick add at the fixed clock: "fri" → Friday 23 October, This week;
  "sun" → Sunday 25 October, This week; "mon" → Monday 26 October, Later;
  "tue" → asks "today or next Tuesday?"; "5/11" → asks "5 Nov or 11 May?";
  "13/11" → 13 November; "11/11" → 11 November; "1/3" → asks; a date
  already passed this year → asks" — TestTheFixedClock.
- Journey 4.2's line — TestJourney.
- FR-TASK-CHG-06, a caller in another zone near midnight — TestTheirOwnDay.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.tasks import quick_add, rules

TODAY = date(2026, 10, 20)  # a Tuesday
IST = "Asia/Kolkata"
PEOPLE = [
    {"id": "p-timothy", "display_name": "Timothy Paul", "email": "timothy@nunnarilabs.com"},
    {"id": "p-navaneeth", "display_name": "Navaneeth", "email": "navaneeth@nunnarilabs.com"},
    {"id": "p-vinita", "display_name": "Vinita", "email": "vinita@nunnarilabs.com"},
    {"id": "p-deepika-r", "display_name": "Deepika R", "email": "deepika.r@nunnarilabs.com"},
    {"id": "p-deepika-s", "display_name": "Deepika S", "email": "deepika.s@nunnarilabs.com"},
]


def _parse(text: str, today: date = TODAY) -> quick_add.Parsed:
    return quick_add.parse(text, today, PEOPLE, IST)


def _prompts(parsed: quick_add.Parsed) -> list[str]:
    return [q.prompt for q in parsed.questions]


def _values(question: quick_add.Question) -> list[str]:
    return [o.value for o in question.options]


class TestJourney:
    """Journey 4.2 — one line, previewed before it is saved."""

    def test_the_line_of_journey_4_2(self):
        parsed = _parse("Call Deepak re RDX pilot fri #sales @Timothy")
        assert parsed.title == "Call Deepak re RDX pilot"
        assert parsed.due_on == date(2026, 10, 23)
        assert quick_add.weekday_name(parsed.due_on) == "Friday"
        assert parsed.tags == ("sales",)
        assert parsed.assignee == {"id": "p-timothy", "name": "Timothy Paul"}
        assert parsed.bucket == "this_week"
        assert parsed.questions == ()


class TestTheFixedClock:
    """Spec §12 "Quick add at the fixed clock", line by line."""

    @pytest.mark.parametrize(
        ("word", "due_on", "bucket"),
        [
            ("fri", date(2026, 10, 23), "this_week"),
            ("sun", date(2026, 10, 25), "this_week"),
            ("mon", date(2026, 10, 26), "later"),
            ("13/11", date(2026, 11, 13), "later"),
            ("11/11", date(2026, 11, 11), "later"),
        ],
    )
    def test_read_without_asking(self, word, due_on, bucket):
        parsed = _parse(f"Send the deck {word}")
        assert (parsed.title, parsed.due_on, parsed.bucket, parsed.questions) == (
            "Send the deck",
            due_on,
            bucket,
            (),
        )

    def test_tue_asks_today_or_next_tuesday(self):
        parsed = _parse("Send the deck tue")
        assert parsed.due_on is None
        [question] = parsed.questions
        assert (question.kind, question.prompt) == ("date", "Today or next Tuesday?")
        assert _values(question) == ["2026-10-20", "2026-10-27"]

    def test_5_11_asks_5_nov_or_11_may(self):
        [question] = _parse("Send the deck 5/11").questions
        assert question.prompt == "5 Nov or 11 May?"
        # 11 May 2026 has passed (FR-TASK-ADD-04): it is offered as next year's.
        assert _values(question) == ["2026-11-05", "2027-05-11"]
        assert [o.label for o in question.options] == ["Thu 5 Nov 2026", "Tue 11 May 2027"]

    def test_an_ambiguous_d_m_never_offers_a_passed_date(self):
        [question] = _parse("Call vendor 3/4", date(2026, 10, 18)).questions
        assert question.prompt == "3 Apr or 4 Mar?"
        assert _values(question) == ["2027-04-03", "2027-03-04"]

    def test_1_3_asks(self):
        [question] = _parse("Send the deck 1/3").questions
        assert question.prompt == "1 Mar or 3 Jan?"

    @pytest.mark.parametrize("word", ["2026-10-02", "2 Oct", "2 October", "13/2"])
    def test_a_date_already_passed_this_year_asks(self, word):
        parsed = _parse(f"Renew the lease {word}")
        assert parsed.title == "Renew the lease"
        assert parsed.due_on is None
        [question] = parsed.questions
        assert question.kind == "date"
        assert question.prompt.endswith("has passed: keep it or choose another date?")

    def test_the_past_iso_date_names_itself(self):
        assert _prompts(_parse("x 2026-10-02")) == [
            "2026-10-02 has passed: keep it or choose another date?"
        ]

    def test_a_past_d_mon_offers_this_year_or_next(self):
        [question] = _parse("x 2 Oct").questions
        assert question.prompt == "2 Oct has passed: keep it or choose another date?"
        assert _values(question) == ["2026-10-02", "2027-10-02"]


class TestDates:
    """FR-TASK-ADD-03 — the date words, each removed from the title."""

    @pytest.mark.parametrize(
        ("text", "due_on", "bucket"),
        [
            ("Pay rent today", date(2026, 10, 20), "today"),
            ("Pay rent tomorrow", date(2026, 10, 21), "this_week"),
            ("Pay rent Friday", date(2026, 10, 23), "this_week"),
            ("Pay rent next week", date(2026, 10, 26), "later"),
            ("Pay rent 2026-11-30", date(2026, 11, 30), "later"),
            ("Pay rent 2026-10-20", date(2026, 10, 20), "today"),
            ("Pay rent 25 Dec", date(2026, 12, 25), "later"),
            ("Pay rent 25 december", date(2026, 12, 25), "later"),
            ("Pay rent 22 Oct", date(2026, 10, 22), "this_week"),
        ],
    )
    def test_each_form(self, text, due_on, bucket):
        parsed = _parse(text)
        assert (parsed.title, parsed.due_on, parsed.bucket, parsed.questions) == (
            "Pay rent",
            due_on,
            bucket,
            (),
        )

    def test_next_week_is_next_monday(self):
        assert _parse("x next week").due_on == date(2026, 10, 26)

    def test_next_weekday_always_asks(self):
        for word in ("fri", "friday", "tue", "mon"):
            parsed = _parse(f"Call Deepak next {word}")
            assert parsed.title == "Call Deepak"
            assert [q.kind for q in parsed.questions] == ["date"]
        [question] = _parse("Call Deepak next fri").questions
        assert question.prompt == "Next Friday: 23 Oct or 30 Oct?"
        assert _values(question) == ["2026-10-23", "2026-10-30"]

    def test_d_m_with_one_part_over_12_is_day_first_whichever_side(self):
        assert _parse("x 11/13").due_on == date(2026, 11, 13)

    def test_no_date_is_later(self):
        parsed = _parse("Read the contract")
        assert (parsed.due_on, parsed.bucket) == (None, "later")

    @pytest.mark.parametrize(
        "text",
        [
            "Plan 31/2 offsite",
            "Plan 13/13 offsite",
            "Plan 30 Feb x",
            "Fix 0/5 split",
            "Fix 5/0 split",
        ],
    )
    def test_a_date_that_does_not_exist_stays_in_the_title(self, text):
        parsed = _parse(text)
        assert (parsed.due_on, parsed.title, parsed.questions) == (None, text, ())

    def test_only_the_first_date_counts(self):
        parsed = _parse("Move the call from tomorrow to fri")
        assert (parsed.due_on, parsed.title) == (date(2026, 10, 21), "Move the call from to fri")


class TestTagsAndTitle:
    """FR-TASK-ADD-03 — `#word` is a tag, removed from the title."""

    def test_tags_are_stripped_and_kept_as_typed(self):
        parsed = _parse("#Sales Call Deepak #rdx about the pilot #Sales")
        assert parsed.title == "Call Deepak about the pilot"
        assert parsed.tags == ("Sales", "rdx")

    def test_a_lone_hash_or_at_stays(self):
        assert _parse("Fix # and @ signs").title == "Fix # and @ signs"

    def test_spaces_are_single(self):
        assert _parse("  Call   Deepak  fri ").title == "Call Deepak"


class TestAssignee:
    """FR-TASK-ADD-03, -04 — `@name` against the people the caller may
    assign: a whole or first display name, or an email."""

    @pytest.mark.parametrize(
        "word", ["@Timothy", "@timothy", "@timothy@nunnarilabs.com", "@VINITA"]
    )
    def test_one_match(self, word):
        parsed = _parse(f"Call Deepak {word}")
        assert parsed.title == "Call Deepak"
        assert parsed.assignee is not None and parsed.questions == ()

    def test_no_one_asks(self):
        parsed = _parse("Call Deepak @Zed")
        assert parsed.assignee is None
        [question] = parsed.questions
        assert (question.kind, question.prompt, question.options) == (
            "assignee",
            "No one you can assign is called Zed: who is it for?",
            (),
        )

    def test_several_ask_with_each_of_them(self):
        [question] = _parse("Call Deepak @deepika").questions
        assert question.kind == "assignee"
        assert question.prompt == "Which deepika?"
        assert [(o.value, o.label) for o in question.options] == [
            ("p-deepika-r", "Deepika R (deepika.r@nunnarilabs.com)"),
            ("p-deepika-s", "Deepika S (deepika.s@nunnarilabs.com)"),
        ]

    def test_no_name_is_the_caller(self):
        assert _parse("Call Deepak").assignee is None


class TestAnswers:
    """Contracts §1 `answers` — a chosen date or person closes its question."""

    def test_a_chosen_date_closes_the_date_question(self):
        parsed = quick_add.answer(_parse("x tue @Zed"), TODAY, due_on=date(2026, 10, 20))
        assert (parsed.due_on, parsed.bucket) == (date(2026, 10, 20), "today")
        assert [q.kind for q in parsed.questions] == ["assignee"]

    def test_a_chosen_person_closes_the_assignee_question(self):
        who = {"id": "p-deepika-s", "name": "Deepika S"}
        parsed = quick_add.answer(_parse("x @deepika 5/11"), TODAY, assignee=who)
        assert parsed.assignee == who
        assert [q.kind for q in parsed.questions] == ["date"]

    def test_the_bucket_follows_the_chosen_date(self):
        parsed = quick_add.answer(_parse("x 5/11"), TODAY, due_on=date(2026, 10, 25))
        assert (parsed.bucket, parsed.questions) == ("this_week", ())


class TestTheirOwnDay:
    """FR-TASK-CHG-06 — "today" and the weekday are the caller's own, in
    their zone, near midnight."""

    def test_new_york_late_monday_reads_tue_as_tomorrow(self):
        # 03:30 UTC on Tuesday 20 Oct is 23:30 on Monday 19 Oct in New York;
        # in Asia/Kolkata it is already Tuesday, so the same word asks there.
        now = datetime(2026, 10, 20, 3, 30, tzinfo=UTC)
        zone = "America/New_York"
        today = rules.day_of(now, zone)
        parsed = quick_add.parse("Call Deepak tue", today, PEOPLE, zone)
        assert (parsed.due_on, parsed.bucket, parsed.questions) == (
            date(2026, 10, 20),
            "this_week",
            (),
        )
        here = quick_add.parse("Call Deepak tue", rules.day_of(now, IST), PEOPLE, IST)
        assert [q.prompt for q in here.questions] == ["Today or next Tuesday?"]

    def test_auckland_just_after_midnight_is_already_wednesday(self):
        # 11:01 UTC on Tuesday 20 Oct is 00:01 on Wednesday 21 Oct in Auckland.
        now = datetime(2026, 10, 20, 11, 1, tzinfo=UTC)
        zone = "Pacific/Auckland"
        today = rules.day_of(now, zone)
        assert quick_add.parse("x today", today, PEOPLE, zone).due_on == date(2026, 10, 21)
        assert [q.prompt for q in quick_add.parse("x wed", today, PEOPLE, zone).questions] == [
            "Today or next Wednesday?"
        ]
        assert quick_add.parse("x tue", today, PEOPLE, zone).due_on == date(2026, 10, 27)

    def test_the_week_ends_on_their_sunday(self):
        # 23:30 on Sunday 25 Oct in New York is 03:30 on Monday 26 Oct UTC.
        now = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)
        zone = "America/New_York"
        today = rules.day_of(now, zone)
        assert today == date(2026, 10, 25)
        assert quick_add.parse("x tomorrow", today, PEOPLE, zone).bucket == "later"
        assert quick_add.parse("x today", today, PEOPLE, zone).bucket == "today"
