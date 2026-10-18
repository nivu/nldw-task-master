"""The Ideas and Reading inbox through the routes — spec 008
FR-TASK-INBOX-01 to -03, FR-TASK-VOICE-04, FR-TASK-ROLL-03; Journey 4.10;
contracts §1 "Route order", §2 inbox routes; data-model §2.10.

On the shared task world with phase 4 on for the cast (Tuesday 20 October
2026, noon IST). The real table is covered by
`tests/db/test_task_inbox_db.py`.

Acceptance criteria (spec §12, phase 4):
- "Inbox items are visible only to their owner; Make it a task links the
  two" — TestOwnerOnly, TestMakeItATask.
- Journey 4.10's inbox half: a confirmed voice idea lands in the inbox —
  TestFromAVoiceNote (replacing S4a's `test_the_idea_waits_for_the_inbox`).
"""

from __future__ import annotations

import logging

import pytest
from tasks_fake import ID, TaskWorld, _refuse
from test_task_voice_routes import FakeVoice, _read, _transcribed

from app.services.tasks import inbox, voice

NOT_ON = "Tasks are not switched on for you yet."
MISSING = "00000000-0000-4000-8000-0000000000ff"


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """The S1b world with phase 4 on for everyone who has phase 1."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 4
    return w


def _add(world, who: str = "member", **body) -> dict:
    r = world.call(who, "POST", "/inbox", json={"kind": "idea", "title": "Webinar", **body})
    assert r.status_code == 201, r.text
    return r.json()


def _detail(r) -> tuple[int, str]:
    return r.status_code, r.json().get("detail")


@pytest.fixture
def items(world) -> dict:
    return world.fake.rows["inbox_items"]


class TestAdding:
    def test_an_item_is_added_with_its_fields(self, world, items):
        item = _add(
            world,
            kind="link",
            title="Agent evals",
            url="https://example.com/evals",
            notes="Read before Friday",
            tags=["ai", "ai", " reading "],
        )
        assert {k: item[k] for k in ("kind", "kind_label", "title", "url", "notes", "tags")} == {
            "kind": "link",
            "kind_label": "Link",
            "title": "Agent evals",
            "url": "https://example.com/evals",
            "notes": "Read before Friday",
            "tags": ["ai", "reading"],
        }
        assert (item["processed_at"], item["archived"], item["task_id"]) == (None, False, None)
        assert items[item["id"]]["owner_id"] == ID["member"]

    def test_the_add_is_audited_as_the_owner(self, world):
        item = _add(world)
        [row] = [a for a in world.fake.audit if a["target_id"] == item["id"]]
        assert (row["action"], row["actor_id"], row["workspace_id"]) == (
            "inbox_item.created",
            ID["member"],
            None,
        )

    @pytest.mark.parametrize(
        "body",
        [
            {"kind": "thought", "title": "x"},
            {"kind": "idea", "title": "  "},
            {"kind": "idea", "title": "x" * 201},
            {"kind": "link", "title": "x", "url": "ftp://example.com"},
            {"kind": "link", "title": "x", "url": "https://exa mple.com"},
            {"kind": "idea", "title": "x", "tags": [f"t{n}" for n in range(11)]},
            {"kind": "idea", "title": "x", "owner_id": ID["owner"]},
        ],
    )
    def test_a_bad_item_is_refused(self, world, items, body):
        r = world.call("member", "POST", "/inbox", json=body)
        assert r.status_code == 422, r.text
        assert items == {}

    def test_a_long_tag_is_refused(self, world, items):
        r = world.call(
            "member", "POST", "/inbox", json={"kind": "note", "title": "x", "tags": ["t" * 41]}
        )
        assert _detail(r) == (422, "A tag can be at most 40 characters.")


class TestListing:
    def test_unprocessed_items_newest_first(self, world, monkeypatch):
        import crm_fake

        stamps = iter(["2026-10-19T10:00:00+00:00", "2026-10-20T10:00:00+00:00"])
        monkeypatch.setattr(crm_fake, "_now", lambda: next(stamps, "2026-10-20T11:00:00+00:00"))
        older = _add(world, title="Older")
        newer = _add(world, title="Newer")
        done = _add(world, title="Archived")
        world.call("member", "POST", f"/inbox/{done['id']}/archive")
        listed = world.call("member", "GET", "/inbox").json()
        assert [i["id"] for i in listed] == [newer["id"], older["id"]]


class TestOwnerOnly:
    """FR-TASK-INBOX-01 — private to its owner, admins included."""

    @pytest.mark.parametrize("who", ["owner", "outsider", "manager", "lead", "labsadmin"])
    def test_nobody_else_sees_or_changes_an_item(self, world, items, who):
        item = _add(world, "creator")
        assert world.call(who, "GET", "/inbox").json() == []
        for action in ("make-task", "archive"):
            r = world.call(who, "POST", f"/inbox/{item['id']}/{action}")
            assert _detail(r) == (404, inbox.NO_SUCH_ITEM)
        assert items[item["id"]]["processed_at"] is None
        assert not any(t["title"] == "Webinar" for t in world.tasks.values())

    def test_an_item_that_does_not_exist_is_404(self, world):
        r = world.call("member", "POST", f"/inbox/{MISSING}/archive")
        assert _detail(r) == (404, inbox.NO_SUCH_ITEM)

    def test_the_review_shows_only_your_own_items(self, world):
        mine = _add(world, "member", title="Mine")
        _add(world, "creator", title="Theirs")
        review = world.call("member", "GET", "/review").json()
        assert [i["id"] for i in review["inbox"]] == [mine["id"]]


class TestMakeItATask:
    """FR-TASK-INBOX-02."""

    def test_make_it_a_task_links_the_two(self, world, items):
        item = _add(
            world,
            kind="link",
            title="Agent evals",
            url="https://example.com/e",
            notes="Skim",
            tags=["ai"],
        )
        r = world.call("member", "POST", f"/inbox/{item['id']}/make-task")
        assert r.status_code == 200, r.text
        out = r.json()
        task = out["task"]
        assert (task["title"], task["notes"], task["tags"], task["kind"]) == (
            "Agent evals",
            "https://example.com/e\n\nSkim",
            ["ai"],
            "private",
        )
        assert (task["source"], task["assignee"]["id"]) == ("manual", ID["member"])
        assert out["item"]["task_id"] == task["id"]
        assert out["item"]["processed_at"] is not None and out["item"]["archived"] is False
        assert items[item["id"]]["task_id"] == task["id"]
        assert world.call("member", "GET", "/inbox").json() == []

    def test_make_it_a_task_with_the_fields_sent(self, world):
        item = _add(world)
        r = world.call(
            "member",
            "POST",
            f"/inbox/{item['id']}/make-task",
            json={"title": "Plan the webinar", "bucket": "this_week", "assignee": "Arun Assignee"},
        )
        assert r.status_code == 200, r.text
        task = r.json()["task"]
        assert (task["title"], task["bucket"], task["assignee"]["id"], task["kind"]) == (
            "Plan the webinar",
            "this_week",
            ID["assignee"],
            "shared_personal",
        )

    def test_the_fields_sent_keep_the_items_link_notes_and_tags(self, world):
        item = _add(
            world,
            kind="link",
            title="Agent evals",
            url="https://example.com/evals",
            notes="Read before Friday",
            tags=["ai"],
        )
        r = world.call(
            "member",
            "POST",
            f"/inbox/{item['id']}/make-task",
            json={"title": "Agent evals", "bucket": "this_week"},
        )
        assert r.status_code == 200, r.text
        task = r.json()["task"]
        assert (task["notes"], task["tags"], task["bucket"]) == (
            "https://example.com/evals\n\nRead before Friday",
            ["ai"],
            "this_week",
        )

    def test_the_task_and_the_items_link_are_written_together(self, world, items, monkeypatch):
        item = _add(world)
        before = len(world.tasks)
        one = world.fake.tasks._one

        def failing(actor, op, refs):
            # The write that records the task on the item.
            if op["table"] == "inbox_items" and "task_id" in op.get("values", {}):
                raise _refuse("CRM22", "lost")
            return one(actor, op, refs)

        monkeypatch.setattr(world.fake.tasks, "_one", failing)
        r = world.call("member", "POST", f"/inbox/{item['id']}/make-task")
        assert r.status_code != 200
        assert (items[item["id"]]["processed_at"], items[item["id"]]["task_id"]) == (None, None)
        assert len(world.tasks) == before

    def test_a_processed_item_is_not_made_twice(self, world):
        item = _add(world)
        world.call("member", "POST", f"/inbox/{item['id']}/make-task")
        before = len(world.tasks)
        for action in ("make-task", "archive"):
            r = world.call("member", "POST", f"/inbox/{item['id']}/{action}")
            assert _detail(r) == (409, inbox.PROCESSED)
        assert len(world.tasks) == before

    def test_a_refused_task_leaves_the_item_unprocessed(self, world, items):
        item = _add(world)
        r = world.call(
            "member",
            "POST",
            f"/inbox/{item['id']}/make-task",
            json={"title": "x", "assignee_id": ID["gone"]},
        )
        assert r.status_code == 422, r.text
        assert items[item["id"]]["processed_at"] is None
        assert [i["id"] for i in world.call("member", "GET", "/inbox").json()] == [item["id"]]


class TestArchive:
    def test_archive_processes_and_archives(self, world, items):
        item = _add(world)
        r = world.call("member", "POST", f"/inbox/{item['id']}/archive")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["archived"] is True and out["processed_at"] is not None
        assert out["task_id"] is None
        assert world.call("member", "GET", "/inbox").json() == []


class TestFromAVoiceNote:
    """Journey 4.10, FR-TASK-VOICE-04 — a confirmed voice idea lands in the
    inbox; this replaces S4a's `test_the_idea_waits_for_the_inbox`."""

    @pytest.fixture
    def fake(self, monkeypatch) -> FakeVoice:
        return FakeVoice(monkeypatch)

    def test_the_idea_lands_in_the_inbox(self, world, fake):
        upload_id = _transcribed(world, fake, "user")
        r = world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 1})
        assert r.status_code == 200, r.text
        item = r.json()
        assert (item["kind"], item["title"]) == ("idea", "a webinar on agent evals")
        assert [i["id"] for i in world.call("user", "GET", "/inbox").json()] == [item["id"]]
        assert _read(world, upload_id)["candidates"][1]["handled"] is True

    def test_both_candidates_confirmed_clear_the_transcript(self, world, fake):
        upload_id = _transcribed(world, fake, "user")
        world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 1})
        row = fake.rows[upload_id]
        assert (row["status"], row["transcript"]) == ("done", None)

    def test_an_edited_idea_is_saved_as_edited(self, world, fake):
        upload_id = _transcribed(world, fake, "user")
        draft = {"kind": "note", "title": "Webinar on evals", "tags": ["events"]}
        r = world.call(
            "user", "POST", f"/voice/{upload_id}/confirm", json={"index": 1, "draft": draft}
        )
        assert r.status_code == 200, r.text
        assert (r.json()["kind"], r.json()["title"], r.json()["tags"]) == (
            "note",
            "Webinar on evals",
            ["events"],
        )

    def test_a_draft_of_the_wrong_kind_is_refused(self, world, fake):
        upload_id = _transcribed(world, fake, "user")
        r = world.call(
            "user",
            "POST",
            f"/voice/{upload_id}/confirm",
            json={"index": 1, "draft": {"title": "x"}},
        )
        assert _detail(r) == (422, voice.NOT_A_TASK)
        r = world.call(
            "user",
            "POST",
            f"/voice/{upload_id}/confirm",
            json={"index": 0, "draft": {"kind": "idea", "title": "x"}},
        )
        assert _detail(r) == (422, voice.NOT_AN_ITEM)
        assert not any(c["handled"] for c in _read(world, upload_id)["candidates"])

    def test_nothing_of_the_idea_is_logged(self, world, fake, caplog):
        fake.transcript = "Idea: zqxinbox"
        upload_id = _transcribed(world, fake, "user")
        with caplog.at_level(logging.DEBUG):
            world.call("user", "POST", f"/voice/{upload_id}/confirm", json={"index": 0})
        assert "zqxinbox" not in caplog.text


class TestRoutes:
    """Contracts §1 "Route order" and the phase guard."""

    def test_inbox_and_review_reach_their_own_handlers(self, world):
        r = world.call("member", "GET", "/inbox")
        assert (r.status_code, r.json()) == (200, [])
        r = world.call("member", "GET", "/review")
        assert r.status_code == 200
        assert set(r.json()) == {
            "show",
            "week_start",
            "this_week",
            "this_month",
            "waiting_over_7_days",
            "inbox",
        }
        r = world.call("member", "POST", f"/inbox/{MISSING}/archive")
        assert _detail(r) == (404, inbox.NO_SUCH_ITEM)

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/inbox"),
            ("POST", "/inbox"),
            ("POST", f"/inbox/{MISSING}/make-task"),
            ("POST", f"/inbox/{MISSING}/archive"),
            ("GET", "/review"),
            ("POST", "/review/dismiss"),
        ],
    )
    def test_level_3_is_refused(self, world, method, path):
        world.fake.profiles[ID["member"]]["tasks_level"] = 3
        body = {"kind": "idea", "title": "x"} if path == "/inbox" and method == "POST" else None
        r = world.call("member", method, path, json=body)
        assert _detail(r) == (403, NOT_ON)
