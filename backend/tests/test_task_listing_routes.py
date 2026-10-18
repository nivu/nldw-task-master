"""My Tasks — spec 008 FR-TASK-LIST-01 to -04, -07, FR-TASK-CHG-06; TP-06,
TP-15; contracts §1 `GET /tasks`.

Open and waiting tasks assigned to the caller, in four sections by effective
bucket, each by due date (none last), priority and title; done and
cancelled ones in a page of their own, newest first; filters by tag,
priority, link and text; the caller's own zone decides "today".

Acceptance criteria (spec §12, phase 1):
- "A task due today or overdue shows in Today whatever its stored bucket,
  with days overdue; no job rewrites buckets" — TestSections.
- "My Tasks filters by tag, priority, link and text" — TestFilters.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tasks_fake import ID, TaskWorld

from app.domain.tasks import service


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    return TaskWorld(monkeypatch)


def _mine(world: TaskWorld, who: str = "member", **params) -> dict:
    r = world.call(who, "GET", "", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _detail(r) -> tuple[int, str]:
    return r.status_code, r.json().get("detail")


def _titles(tasks: list[dict]) -> list[str]:
    return [t["title"] for t in tasks]


class TestSections:
    """FR-TASK-LIST-01, -02, -03."""

    def test_four_sections_by_effective_bucket(self, world):
        world.add("member", title="Overdue later", bucket="later", due_on="2026-10-18")
        world.add("member", title="Week", bucket="this_week")
        world.add("member", title="Month", bucket="this_month")
        world.add("member", title="No date")
        found = _mine(world)
        assert (found["as_of"], found["timezone"]) == ("2026-10-20", "Asia/Kolkata")
        assert {k: _titles(v) for k, v in found["sections"].items()} == {
            "today": ["Overdue later"],
            "this_week": ["Week"],
            "this_month": ["Month"],
            "later": ["No date"],
        }
        [overdue] = found["sections"]["today"]
        assert (overdue["bucket"], overdue["days_overdue"]) == ("later", 2)

    def test_nothing_is_rewritten(self, world):
        task = world.add("member", bucket="later", due_on="2026-10-18")
        _mine(world)
        assert world.tasks[task["id"]]["bucket"] == "later"

    def test_due_date_none_last_then_priority_then_title(self, world):
        for title, due, priority in (
            ("b no date", None, "normal"),
            ("a no date", None, "normal"),
            ("urgent no date", None, "urgent"),
            ("later due", "2026-12-01", "low"),
            ("sooner due", "2026-11-01", "low"),
            ("sooner urgent", "2026-11-01", "urgent"),
        ):
            world.add("member", title=title, due_on=due, priority=priority, bucket="later")
        assert _titles(_mine(world)["sections"]["later"]) == [
            "sooner urgent",
            "sooner due",
            "later due",
            "urgent no date",
            "a no date",
            "b no date",
        ]

    def test_only_tasks_assigned_to_the_caller(self, world):
        world.add("member", title="Mine")
        world.add("member", title="Theirs", assignee_id="assignee")
        world.add("owner", title="Not mine")
        assert _titles(_mine(world)["sections"]["later"]) == ["Mine"]
        assert _titles(_mine(world, "assignee")["sections"]["later"]) == ["Theirs"]

    def test_waiting_is_open(self, world):
        world.add("member", title="Waiting", status="waiting", waiting_on_text="CA")
        assert _titles(_mine(world)["sections"]["later"]) == ["Waiting"]

    def test_a_crm_task_after_removal_is_gone(self, world):
        world.add("removed", title="Deal task", links=[world.link(world.acme, "company")])
        assert _titles(_mine(world, "removed")["sections"]["later"]) == ["Deal task"]
        del world.fake.rows["crm_memberships"][world.removed["id"]]
        assert _mine(world, "removed")["sections"]["later"] == []


class TestClosed:
    """FR-TASK-LIST-01 — done and cancelled, collapsed, newest first."""

    def test_newest_first_in_their_own_page(self, world):
        first = world.add("member", title="First")
        second = world.add("member", title="Second")
        world.add("member", title="Still open")
        world.call("member", "POST", f"/{first['id']}/complete")
        world.call("member", "PATCH", f"/{second['id']}", json={"status": "cancelled"})
        found = _mine(world)
        assert _titles(found["closed"]) == ["Second", "First"]
        assert _titles(found["sections"]["later"]) == ["Still open"]
        assert found["next_closed"] is None

    def test_paged_by_closed_limit_and_closed_before(self, world):
        for n in range(5):
            task = world.add("member", title=f"T{n}")
            world.call("member", "POST", f"/{task['id']}/complete")
        page = _mine(world, closed_limit=2)
        assert _titles(page["closed"]) == ["T4", "T3"]
        assert page["next_closed"]
        rest = _mine(world, closed_limit=2, closed_before=page["next_closed"])
        assert _titles(rest["closed"]) == ["T2", "T1"]
        last = _mine(world, closed_limit=2, closed_before=rest["next_closed"])
        assert (_titles(last["closed"]), last["next_closed"]) == (["T0"], None)

    def test_tasks_closed_together_are_not_skipped_at_a_page_edge(self, world):
        """One transaction (a deal lost, an import) gives them one updated_at."""
        for n in range(3):
            task = world.add("member", title=f"T{n}")
            world.call("member", "POST", f"/{task['id']}/complete")
        for task in world.tasks.values():
            task["updated_at"] = "2026-10-20T06:00:00.123456+00:00"
        page = _mine(world, closed_limit=2)
        rest = _mine(world, closed_limit=2, closed_before=page["next_closed"])
        assert rest["next_closed"] is None
        assert sorted(_titles(page["closed"]) + _titles(rest["closed"])) == ["T0", "T1", "T2"]

    def test_a_cursor_not_given_by_a_page_is_refused(self, world):
        r = world.call("member", "GET", "", params={"closed_before": "yesterday"})
        assert _detail(r) == (422, "closed_before must be the next_closed of the page before.")

    def test_closed_limit_is_at_most_100(self, world):
        assert world.call("member", "GET", "", params={"closed_limit": 101}).status_code == 422


class TestFilters:
    """FR-TASK-LIST-07 (TP-15)."""

    def test_by_tag(self, world):
        world.add("member", title="Sales", tags=["sales"])
        world.add("member", title="Ops", tags=["ops"])
        assert _titles(_mine(world, tag="sales")["sections"]["later"]) == ["Sales"]

    def test_by_priority(self, world):
        world.add("member", title="Hot", priority="urgent")
        world.add("member", title="Cold", priority="low")
        assert _titles(_mine(world, priority="urgent")["sections"]["later"]) == ["Hot"]

    def test_by_link(self, world):
        world.add("member", title="On the deal", links=[world.link(world.deal, "opportunity")])
        world.add("member", title="On Lumen", links=[world.link(world.lumen, "tva_project")])
        world.add("member", title="Loose")
        by_deal = _mine(world, link_type="opportunity", link_id=world.deal["id"])
        assert _titles(by_deal["sections"]["later"]) == ["On the deal"]
        by_project = _mine(world, link_type="tva_project", link_id=world.lumen["id"])
        assert _titles(by_project["sections"]["later"]) == ["On Lumen"]

    def test_by_text_in_title_or_notes_literally(self, world):
        world.add("member", title="Call Deepak")
        world.add("member", title="Write", notes="ask deepak about 50%")
        world.add("member", title="Other")
        assert _titles(_mine(world, q="deepak")["sections"]["later"]) == ["Call Deepak", "Write"]
        assert _titles(_mine(world, q="50%")["sections"]["later"]) == ["Write"]

    def test_text_needs_two_characters(self, world):
        r = world.call("member", "GET", "", params={"q": "d"})
        assert (r.status_code, r.json()["detail"]) == (422, "Type at least two characters.")

    def test_text_of_characters_never_matched_is_too_short(self, world):
        world.add("member", title="Call Deepak")
        for q in ("()", "**", "a,", '"\\'):
            r = world.call("member", "GET", "", params={"q": q})
            assert _detail(r) == (422, "Type at least two characters."), q

    def test_a_tag_is_checked_like_a_saved_one(self, world):
        r = world.call("member", "GET", "", params={"tag": "x" * 41})
        assert _detail(r) == (422, "A tag can be at most 40 characters.")
        world.add("member", title="Comma", tags=["a,b"])
        world.add("member", title="Both", tags=["a", "b"])
        assert _titles(_mine(world, tag=" a,b ")["sections"]["later"]) == ["Comma"]

    def test_filters_narrow_the_closed_page_too(self, world):
        task = world.add("member", title="Sales done", tags=["sales"])
        other = world.add("member", title="Ops done", tags=["ops"])
        for t in (task, other):
            world.call("member", "POST", f"/{t['id']}/complete")
        assert _titles(_mine(world, tag="sales")["closed"]) == ["Sales done"]


class TestBucketMoves:
    """FR-TASK-LIST-04 — a move changes the bucket only."""

    def test_moving_the_bucket_never_changes_the_due_date(self, world):
        task = world.add("member", due_on="2026-11-05", bucket="later")
        r = world.call("member", "PATCH", f"/{task['id']}", json={"bucket": "this_week"})
        assert (r.json()["bucket"], r.json()["due_on"]) == ("this_week", "2026-11-05")
        assert _titles(_mine(world)["sections"]["this_week"]) == ["Call Deepak"]


class TestTheCallersZone:
    """FR-TASK-CHG-06 — "today" is the caller's own date."""

    def test_sections_follow_the_callers_zone(self, world, monkeypatch):
        # 12:30 UTC on 20 October: 18:00 in Kolkata, 01:30 on the 21st in Auckland.
        monkeypatch.setattr(service, "_now", lambda: datetime(2026, 10, 20, 12, 30, tzinfo=UTC))
        world.fake.profiles[ID["user"]]["timezone"] = "Pacific/Auckland"
        for who in ("member", "user"):
            world.add(who, title="Due 21st", due_on="2026-10-21", bucket="later")
        kolkata, auckland = _mine(world), _mine(world, "user")
        assert (kolkata["as_of"], auckland["as_of"]) == ("2026-10-20", "2026-10-21")
        assert auckland["timezone"] == "Pacific/Auckland"
        assert _titles(kolkata["sections"]["later"]) == ["Due 21st"]
        assert _titles(auckland["sections"]["today"]) == ["Due 21st"]

    def test_days_overdue_in_new_york(self, world, monkeypatch):
        # 02:00 UTC on 21 October is still the 20th in New York.
        monkeypatch.setattr(service, "_now", lambda: datetime(2026, 10, 21, 2, 0, tzinfo=UTC))
        world.fake.profiles[ID["user"]]["timezone"] = "America/New_York"
        world.add("user", title="Due 19th", due_on="2026-10-19")
        [task] = _mine(world, "user")["sections"]["today"]
        assert task["days_overdue"] == 1


class TestTrash:
    """FR-TASK-DATA-11 — days left, counted in the caller's own zone."""

    def test_days_left_from_the_callers_own_day(self, world):
        mine = world.add("member")
        world.call("member", "DELETE", f"/{mine['id']}")
        # 02:00 IST on 20 October is still the 19th in UTC.
        world.tasks[mine["id"]]["deleted_at"] = "2026-10-19T20:30:00+00:00"
        [task] = world.call("member", "GET", "/trash").json()
        assert task["days_left"] == 90
