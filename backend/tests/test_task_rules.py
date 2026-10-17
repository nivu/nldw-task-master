"""Task rules — spec 008 FR-TASK-DATA-03, -08, FR-TASK-LIST-01 to -03,
FR-TASK-CHG-06; TP-06, TP-14; §3.1 (kinds).

Pure functions, with a fixed clock: Tuesday 20 October 2026 (spec §12).

Acceptance criteria (spec §12, phase 1):
- "A task due today or overdue shows in Today whatever its stored bucket,
  with days overdue; no job rewrites buckets" — TestEffectiveBucket,
  TestDaysOverdue.
- "Completing a task sets completed at; reopening clears it" —
  TestStatusValues.
- "A tag over 40 characters, or an eleventh tag on a task, is refused" —
  TestTags.
- FR-TASK-CHG-06, the per-person time zone near midnight — TestTheirOwnDay.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.tasks import rules

TODAY = date(2026, 10, 20)  # a Tuesday
NOW = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)


def _task(**values):
    return {"status": "open", "bucket": "later", "due_on": None, **values}


class TestDefaultBucket:
    """FR-TASK-DATA-08 — Today if due today or earlier, otherwise Later."""

    @pytest.mark.parametrize(
        ("due_on", "bucket"),
        [
            ("2026-10-19", "today"),
            ("2026-10-20", "today"),
            ("2026-10-21", "later"),
            (None, "later"),
            (date(2026, 9, 1), "today"),
        ],
    )
    def test_the_default(self, due_on, bucket):
        assert rules.default_bucket(due_on, TODAY) == bucket


class TestEffectiveBucket:
    """FR-TASK-LIST-02 (TP-06) — computed on read, nothing rewritten."""

    def test_due_today_shows_in_today_whatever_its_bucket(self):
        task = _task(bucket="this_month", due_on="2026-10-20")
        assert rules.effective_bucket(task, TODAY) == "today"
        assert task["bucket"] == "this_month"

    def test_overdue_shows_in_today(self):
        assert rules.effective_bucket(_task(bucket="later", due_on="2026-10-01"), TODAY) == "today"

    def test_a_future_or_undated_task_keeps_its_bucket(self):
        assert rules.effective_bucket(_task(bucket="this_week", due_on="2026-10-22"), TODAY) == (
            "this_week"
        )
        assert rules.effective_bucket(_task(bucket="this_month"), TODAY) == "this_month"

    def test_waiting_counts_as_open(self):
        task = _task(status="waiting", bucket="later", due_on="2026-10-19")
        assert rules.effective_bucket(task, TODAY) == "today"

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    def test_a_closed_task_keeps_its_bucket(self, status):
        task = _task(status=status, bucket="later", due_on="2026-10-19")
        assert rules.effective_bucket(task, TODAY) == "later"


class TestDaysOverdue:
    """FR-TASK-LIST-03 — the CRM's Task overdue rule, computed when read."""

    @pytest.mark.parametrize(
        ("due_on", "days"),
        [("2026-10-13", 7), ("2026-10-19", 1), ("2026-10-20", 0), ("2026-10-25", 0), (None, 0)],
    )
    def test_days(self, due_on, days):
        assert rules.days_overdue(_task(due_on=due_on), TODAY) == days

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    def test_a_closed_task_is_never_overdue(self, status):
        assert rules.days_overdue(_task(status=status, due_on="2026-10-01"), TODAY) == 0

    def test_a_deleted_task_is_never_overdue(self):
        task = _task(due_on="2026-10-01", deleted_at="2026-10-02T00:00:00+00:00")
        assert rules.days_overdue(task, TODAY) == 0


class TestStatusValues:
    """FR-TASK-DATA-03 — completed at is set exactly when Done."""

    def test_completing_sets_completed_at(self):
        assert rules.status_values("done", "open", NOW) == {
            "status": "done",
            "completed_at": NOW.isoformat(),
        }

    def test_staying_done_keeps_it(self):
        assert rules.status_values("done", "done", NOW) == {"status": "done"}

    @pytest.mark.parametrize("status", ["open", "waiting", "cancelled"])
    def test_every_other_status_clears_it(self, status):
        assert rules.status_values(status, "done", NOW) == {"status": status, "completed_at": None}


class TestTags:
    """TP-14 — count and length only; trimmed; exact repeats dropped; case kept."""

    def test_trimmed_repeats_dropped_case_kept(self):
        assert rules.normalise_tags([" sales ", "Sales", "sales", "GST"]) == [
            "sales",
            "Sales",
            "GST",
        ]

    def test_forty_characters_is_allowed(self):
        assert rules.normalise_tags(["x" * 40]) == ["x" * 40]

    def test_a_tag_over_forty_characters_is_refused(self):
        with pytest.raises(ValueError, match="at most 40 characters"):
            rules.normalise_tags(["x" * 41])

    def test_ten_tags_are_allowed_and_an_eleventh_refused(self):
        assert len(rules.normalise_tags([f"t{i}" for i in range(10)])) == 10
        with pytest.raises(ValueError, match="at most 10 tags"):
            rules.normalise_tags([f"t{i}" for i in range(11)])

    def test_an_empty_tag_is_refused(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            rules.normalise_tags(["   "])

    def test_repeats_do_not_count_towards_ten(self):
        assert len(rules.normalise_tags(["a"] * 11)) == 1


class TestKinds:
    """Spec §3.1 — a task's kind follows from its links."""

    def test_private(self):
        assert rules.kind({"created_by": "a", "assignee_id": "a"}, []) == "private"

    def test_shared_personal(self):
        assert rules.kind({"created_by": "a", "assignee_id": "b"}, []) == "shared_personal"

    def test_crm_with_or_without_a_project(self):
        crm = [{"record_type": "opportunity"}]
        both = [*crm, {"record_type": "tva_project"}]
        task = {"workspace_id": "w", "created_by": "a", "assignee_id": "a"}
        assert rules.kind(task, crm) == "crm"
        assert rules.kind(task, both) == "crm"

    def test_project(self):
        task = {"workspace_id": None, "created_by": "a", "assignee_id": "b"}
        assert rules.kind(task, [{"record_type": "tva_project"}]) == "project"


class TestSortKey:
    """FR-TASK-LIST-01 — due date (none last), then priority, then title."""

    def test_order(self):
        tasks = [
            {"title": "b", "due_on": None, "priority": "urgent"},
            {"title": "z", "due_on": "2026-10-21", "priority": "low"},
            {"title": "a", "due_on": "2026-10-21", "priority": "high"},
            {"title": "c", "due_on": "2026-10-20", "priority": "normal"},
        ]
        assert [t["title"] for t in sorted(tasks, key=rules.sort_key)] == ["c", "a", "z", "b"]


class TestMissingSentence:
    """FR-TASK-DATA-02 — one sentence naming every field."""

    def test_one(self):
        assert rules.missing_sentence(["title"]) == "Title is missing or not valid."

    def test_several(self):
        assert rules.missing_sentence(["title", "due_on", "bucket"]) == (
            "Title, due on and bucket are missing or not valid."
        )


class TestTheirOwnDay:
    """FR-TASK-CHG-06 — "today", the current week and days overdue are the
    person's own, in `profiles.timezone`, near local midnight."""

    def test_new_york_just_before_midnight_is_still_monday(self):
        # 03:59 UTC on Tuesday 20 Oct is 23:59 on Monday 19 Oct in New York.
        now = datetime(2026, 10, 20, 3, 59, tzinfo=UTC)
        today = rules.day_of(now, "America/New_York")
        assert today == date(2026, 10, 19)
        assert rules.days_overdue(_task(due_on="2026-10-19"), today) == 0
        assert rules.default_bucket("2026-10-20", today) == "later"

    def test_auckland_just_after_midnight_is_already_wednesday(self):
        # 11:01 UTC on Tuesday 20 Oct is 00:01 on Wednesday 21 Oct in Auckland.
        now = datetime(2026, 10, 20, 11, 1, tzinfo=UTC)
        today = rules.day_of(now, "Pacific/Auckland")
        assert today == date(2026, 10, 21)
        assert rules.days_overdue(_task(due_on="2026-10-20"), today) == 1
        assert rules.effective_bucket(_task(bucket="later", due_on="2026-10-21"), today) == "today"

    def test_the_week_turns_over_on_their_monday(self):
        # 23:30 on Sunday 25 Oct in New York is 03:30 on Monday 26 Oct UTC.
        now = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)
        assert rules.week_of(rules.day_of(now, "America/New_York")) == (
            date(2026, 10, 19),
            date(2026, 10, 25),
        )
        assert rules.week_of(rules.day_of(now, "Pacific/Auckland")) == (
            date(2026, 10, 26),
            date(2026, 11, 1),
        )

    def test_no_zone_is_asia_kolkata(self):
        # 18:31 UTC is 00:01 the next day in Asia/Kolkata.
        assert rules.day_of(datetime(2026, 10, 20, 18, 31, tzinfo=UTC), None) == date(2026, 10, 21)
