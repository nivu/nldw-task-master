"""The bell — spec 007 FR-CRM-NOTIF-01, -05, FR-CRM-NOTE-02, FR-CRM-WS-02,
FR-CRM-ACC-11; contracts §2.10; data-model §3.19.

Each event reaches exactly the people it is about, never a non-member and
never the person who caused it; no title carries an amount; the unread count
and marking read; and a member removed from a workspace keeps none of its
notifications, in the list, the count or the table.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import (
    DEFAULTS,
    MEMBER,
    OUTSIDER,
    OWNER,
    SELLER,
    VIEWER,
    FakeCrmStore,
    as_,
    two_workspaces,
)
from crm_fake import client as crm_client
from test_crm_tva_lifecycle import (
    PEOPLE as ALL,
)
from test_crm_tva_lifecycle import (
    PRIYA,
    SANJEEV,
    SRIRAM,
    _at_proposal,
    _deal,
    _handoff,
    _move,
)

from app.config import settings
from app.services.crm import deals, notifications
from app.tasks import crm as tasks

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"


@pytest.fixture
def crm(monkeypatch):
    from datetime import date

    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add_member(labs, SRIRAM["id"], "member")
    fake.add_member(labs, SANJEEV["id"], "admin")
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="USD", month="2026-10-01", rate="84")
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    people = {**ALL, SRIRAM["id"]: SRIRAM, SANJEEV["id"]: SANJEEV, PRIYA["id"]: PRIYA}
    return crm_client(monkeypatch, people, fake=fake), fake, labs, academy


def _bell(fake, kind=None):
    return [n for n in fake.rows["crm_notifications"].values() if kind is None or n["kind"] == kind]


def _company(http, name="Acme", who=OWNER["id"], **fields):
    r = http.post(f"/api/v1/crm/companies{LABS}", json={"name": name, **fields}, headers=as_(who))
    assert r.status_code == 201, r.text
    return r.json()


class TestEvents:
    def test_a_task_assigned_to_someone_else(self, crm):
        http, fake, labs, _ = crm
        acme = _company(http)
        link = [{"record_type": "company", "record_id": acme["id"]}]
        http.post(
            f"/api/v1/crm/tasks{LABS}",
            json={"title": "Mine", "links": link},
            headers=as_(OWNER["id"]),
        )
        assert _bell(fake) == []
        r = http.post(
            f"/api/v1/crm/tasks{LABS}",
            json={"title": "Call Priya", "links": link, "assignee_id": MEMBER["id"]},
            headers=as_(OWNER["id"]),
        )
        [n] = _bell(fake, "assigned")
        assert (n["user_id"], n["workspace_id"], n["record_id"]) == (
            MEMBER["id"],
            labs["id"],
            r.json()["id"],
        )
        assert n["title"] == "Owner assigned you the task “Call Priya”"

    def test_reassigning_a_task(self, crm):
        http, fake, _, _ = crm
        acme = _company(http)
        task = http.post(
            f"/api/v1/crm/tasks{LABS}",
            json={"title": "T", "links": [{"record_type": "company", "record_id": acme["id"]}]},
            headers=as_(OWNER["id"]),
        ).json()
        http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"assignee_id": SELLER["id"]},
            headers=as_(OWNER["id"]),
        )
        assert [n["user_id"] for n in _bell(fake, "assigned")] == [SELLER["id"]]

    def test_a_record_owner(self, crm):
        http, fake, _, _ = crm
        _company(http, owner_id=OWNER["id"])
        assert _bell(fake) == []
        acme = _company(http, name="Beta", owner_id=SELLER["id"])
        [n] = _bell(fake, "assigned")
        assert (n["user_id"], n["record_type"], n["record_id"]) == (
            SELLER["id"],
            "company",
            acme["id"],
        )
        http.patch(
            f"/api/v1/crm/companies/{acme['id']}{LABS}",
            json={"owner_id": MEMBER["id"]},
            headers=as_(OWNER["id"]),
        )
        assert sorted(n["user_id"] for n in _bell(fake, "assigned")) == sorted(
            [SELLER["id"], MEMBER["id"]]
        )

    def test_a_mention(self, crm):
        """FR-CRM-NOTE-02 — the members mentioned, not the author, once."""
        http, fake, _, _ = crm
        acme = _company(http)
        note = http.post(
            f"/api/v1/crm/notes{LABS}",
            json={
                "body": "@Deepika over to you",
                "links": [{"record_type": "company", "record_id": acme["id"]}],
                "mention_ids": [MEMBER["id"], OWNER["id"]],
            },
            headers=as_(OWNER["id"]),
        ).json()
        [n] = _bell(fake, "mentioned")
        assert n["user_id"] == MEMBER["id"]
        assert (n["record_type"], n["record_id"]) == ("company", acme["id"])
        assert n["title"] == "Owner mentioned you in a note on “Acme”"
        http.patch(
            f"/api/v1/crm/notes/{note['id']}{LABS}",
            json={"mention_ids": [MEMBER["id"], SELLER["id"]]},
            headers=as_(OWNER["id"]),
        )
        assert sorted(n["user_id"] for n in _bell(fake, "mentioned")) == sorted(
            [MEMBER["id"], SELLER["id"]]
        )

    def test_a_read_after_the_write_never_fails_it(self, crm, monkeypatch):
        """The links and names read after a note's write are only for flags
        and the bell: their failure leaves the write saved and answered."""
        from app.services.crm import records, store

        http, fake, _, _ = crm
        acme = _company(http)
        real_names, names_failed = records.link_names, []

        def link_names(*a, **k):
            # The first read is the bell's; the response's own comes after.
            if not names_failed:
                names_failed.append(True)
                raise RuntimeError("down")
            return real_names(*a, **k)

        monkeypatch.setattr(records, "link_names", link_names)
        r = http.post(
            f"/api/v1/crm/notes{LABS}",
            json={
                "body": "@Deepika over to you",
                "links": [{"record_type": "company", "record_id": acme["id"]}],
                "mention_ids": [MEMBER["id"]],
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201, r.text
        [n] = _bell(fake, "mentioned")
        assert n["title"] == "Owner mentioned you in a note"

        applied = {"done": False, "failed": False}
        real_apply, real_links = store.apply, store.list_links

        def apply(*a, **k):
            applied["done"] = True
            return real_apply(*a, **k)

        def list_links(*a, **k):
            if applied["done"] and not applied["failed"]:
                applied["failed"] = True
                raise RuntimeError("down")
            return real_links(*a, **k)

        monkeypatch.setattr(store, "apply", apply)
        monkeypatch.setattr(store, "list_links", list_links)
        r = http.patch(
            f"/api/v1/crm/notes/{r.json()['id']}{LABS}",
            json={"mention_ids": [MEMBER["id"], SELLER["id"]]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert applied["failed"]
        assert SELLER["id"] in {n["user_id"] for n in _bell(fake, "mentioned")}

    def test_a_deal_moved_by_someone_else(self, crm):
        http, fake, _, _ = crm
        deal = _deal(http, who=SELLER["id"])
        fake.rows["crm_notifications"].clear()
        r = _move(http, deal["id"], "qualification", who=SELLER["id"])
        assert r.status_code == 200, r.text
        assert _bell(fake, "stage_changed") == []
        _move(http, deal["id"], "discovery", who=OWNER["id"])
        [n] = _bell(fake, "stage_changed")
        assert n["user_id"] == SELLER["id"]
        assert n["title"] == "Owner moved “Acme - Phase 1” to Discovery"

    def test_a_handoff_leaves_its_tasks_waiting(self, crm):
        """D8: Closed Won by a TVA user leaves the revenue confirmation with
        the owner (the Labs admin who is a TVA admin); the delivery kickoff
        goes to the deal owner, here the person who won it."""
        http, fake, _, _ = crm
        deal = _at_proposal(http, who=MEMBER["id"])["opportunity"]
        fake.rows["crm_notifications"].clear()
        r = _handoff(http, deal["id"], who=MEMBER["id"])
        assert r.status_code == 200, r.text
        got = sorted((n["kind"], n["user_id"]) for n in _bell(fake) if n["kind"] != "assigned")
        assert got == [("revenue_confirm", OWNER["id"])]

    def test_a_handoff_by_the_owner_tells_the_deal_owner(self, crm):
        http, fake, _, _ = crm
        deal = _at_proposal(http, who=MEMBER["id"])["opportunity"]
        fake.rows["crm_notifications"].clear()
        r = _handoff(http, deal["id"], who=OWNER["id"])
        assert r.status_code == 200, r.text
        got = sorted((n["kind"], n["user_id"]) for n in _bell(fake))
        assert got == [("handoff_waiting", MEMBER["id"]), ("stage_changed", MEMBER["id"])]


class TestNeverANonMember:
    def test_crm_notify_skips_them(self, crm):
        _, fake, labs, academy = crm
        got = notifications.send(
            [
                {
                    "workspace_id": labs["id"],
                    "user_id": OUTSIDER["id"],
                    "kind": "assigned",
                    "title": "x",
                    "dedupe_key": "k1",
                },
                {
                    "workspace_id": academy["id"],
                    "user_id": SELLER["id"],
                    "kind": "assigned",
                    "title": "x",
                    "dedupe_key": "k2",
                },
            ]
        )
        assert got["added"] == 0 and _bell(fake) == []

    def test_once_per_dedupe_key(self, crm):
        _, fake, labs, _ = crm
        row = {
            "workspace_id": labs["id"],
            "user_id": MEMBER["id"],
            "kind": "task_due",
            "title": "Task due today: Call",
            "dedupe_key": "task_due:t:2026-10-14",
        }
        assert notifications.send([dict(row)])["added"] == 1
        assert notifications.send([dict(row)])["added"] == 0


class TestNoAmountInATitle:
    def test_titles_name_records_never_amounts(self, crm):
        http, fake, _, _ = crm
        deal = _at_proposal(http, who=SELLER["id"])["opportunity"]
        _move(
            http,
            deal["id"],
            "negotiation",
            who=OWNER["id"],
            changes={"proposal_sent_on": "2026-10-12"},
        )
        _handoff(http, deal["id"], who=MEMBER["id"])
        titles = [n["title"] for n in _bell(fake)]
        assert titles
        for title in titles:
            assert not re.search(r"\d{3,}|₹|USD|INR", title), title


class TestTheBell:
    def _seed(self, fake, labs, academy):
        for ws, key in ((labs, "a"), (labs, "b"), (academy, "c")):
            notifications.send(
                [
                    {
                        "workspace_id": ws["id"],
                        "user_id": MEMBER["id"],
                        "kind": "assigned",
                        "title": f"Title {key}",
                        "dedupe_key": key,
                    }
                ]
            )

    def test_list_and_count(self, crm):
        http, fake, labs, academy = crm
        self._seed(fake, labs, academy)
        got = http.get("/api/v1/crm/notifications", headers=as_(MEMBER["id"])).json()
        assert sorted(n["workspace_key"] for n in got) == ["academy", "labs", "labs"]
        count = http.get("/api/v1/crm/notifications/unread-count", headers=as_(MEMBER["id"]))
        assert count.json() == {"count": 3}
        assert http.get(
            "/api/v1/crm/notifications/unread-count", headers=as_(OWNER["id"])
        ).json() == {"count": 0}

    def test_mark_read(self, crm):
        http, fake, labs, academy = crm
        self._seed(fake, labs, academy)
        first = http.get("/api/v1/crm/notifications", headers=as_(MEMBER["id"])).json()[0]
        r = http.post(
            "/api/v1/crm/notifications/read", json={"ids": [first["id"]]}, headers=as_(MEMBER["id"])
        )
        assert r.json() == {"updated": 1}
        unread = http.get(
            "/api/v1/crm/notifications?unread_only=true", headers=as_(MEMBER["id"])
        ).json()
        assert first["id"] not in {n["id"] for n in unread} and len(unread) == 2
        r = http.post(
            "/api/v1/crm/notifications/read", json={"all": True}, headers=as_(MEMBER["id"])
        )
        assert r.json() == {"updated": 2}
        assert http.get(
            "/api/v1/crm/notifications/unread-count", headers=as_(MEMBER["id"])
        ).json() == {"count": 0}

    def test_someone_elses_ids_are_not_marked(self, crm):
        http, fake, labs, academy = crm
        self._seed(fake, labs, academy)
        theirs = [n["id"] for n in _bell(fake)]
        r = http.post(
            "/api/v1/crm/notifications/read", json={"ids": theirs}, headers=as_(OWNER["id"])
        )
        assert r.json() == {"updated": 0}

    def test_ids_or_all(self, crm):
        http, *_ = crm
        r = http.post("/api/v1/crm/notifications/read", json={}, headers=as_(MEMBER["id"]))
        assert r.status_code == 422

    def test_a_bad_id_or_time_is_422(self, crm):
        http, *_ = crm
        r = http.post(
            "/api/v1/crm/notifications/read", json={"ids": ["nope"]}, headers=as_(MEMBER["id"])
        )
        assert r.status_code == 422
        r = http.get("/api/v1/crm/notifications?before=yesterday", headers=as_(MEMBER["id"]))
        assert r.status_code == 422

    def test_paging_by_created_at(self, crm):
        http, fake, labs, academy = crm
        self._seed(fake, labs, academy)
        first = http.get("/api/v1/crm/notifications", headers=as_(MEMBER["id"])).json()
        r = http.get(
            "/api/v1/crm/notifications",
            params={"before": first[0]["created_at"]},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200
        assert first[0]["id"] not in {n["id"] for n in r.json()}

    def test_someone_with_no_workspace(self, crm):
        http, *_ = crm
        r = http.get("/api/v1/crm/notifications", headers=as_(OUTSIDER["id"]))
        assert r.status_code == 404

    def test_the_bootstrap_carries_the_count(self, crm):
        http, fake, labs, academy = crm
        self._seed(fake, labs, academy)
        boot = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(MEMBER["id"])).json()
        assert boot["unread"] == 3


class TestRemovedMember:
    """FR-CRM-WS-02, data-model §5.3 — removed from Academy, Deepika keeps
    none of Academy's notifications: not listed, not counted, deleted."""

    def test_none_left(self, crm):
        http, fake, labs, academy = crm
        TestTheBell()._seed(fake, labs, academy)
        membership = next(
            m
            for m in fake.rows["crm_memberships"].values()
            if m["workspace_id"] == academy["id"] and m["user_id"] == MEMBER["id"]
        )
        r = http.delete(
            f"/api/v1/crm/members/{membership['id']}{ACADEMY}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert {n["workspace_id"] for n in _bell(fake)} == {labs["id"]}
        got = http.get("/api/v1/crm/notifications", headers=as_(MEMBER["id"])).json()
        assert {n["workspace_key"] for n in got} == {"labs"}
        assert http.get(
            "/api/v1/crm/notifications/unread-count", headers=as_(MEMBER["id"])
        ).json() == {"count": 2}

    def test_listing_joins_the_memberships_of_now(self, crm):
        """Even a row the removal missed is never shown."""
        http, fake, labs, academy = crm
        fake.add(
            "crm_notifications",
            workspace_id=academy["id"],
            user_id=VIEWER["id"],
            kind="assigned",
            title="Old",
            dedupe_key="old",
            record_type=None,
            record_id=None,
            created_at="2026-10-01T00:00:00+00:00",
            read_at=None,
        )
        got = http.get("/api/v1/crm/notifications", headers=as_(VIEWER["id"])).json()
        assert got == []
        assert http.get(
            "/api/v1/crm/notifications/unread-count", headers=as_(VIEWER["id"])
        ).json() == {"count": 0}


class TestStuckInStageNotice:
    """Spec 009 FR-CRMAI-RULE-04, -08, FR-CRMAI-CHG-08; Q-14 — the owner gets
    one bell notice and one message per deal per stage entry; with a Stale
    deal notice at the same level the same day, one message; a CRM-only
    owner, the bell only. The clock is 2026-10-14 06:30 UTC, noon in
    Chennai."""

    NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)

    @pytest.fixture
    def stuck(self, monkeypatch):
        fake = FakeCrmStore().install(monkeypatch)
        labs, _ = two_workspaces(fake)
        fake.add(
            "crm_workspace_settings",
            workspace_id=labs["id"],
            key="automations",
            value={"stale_notice": True, "stuck_notice": True},
        )
        fake.set_ai(labs, budget_usd=0, phases={"3": True})
        crm_client(monkeypatch, fake=fake)
        sent: list = []
        monkeypatch.setattr(notifications, "deliver", lambda m: sent.append(m) or ["slack"])
        monkeypatch.setattr(settings, "SMTP_HOST", "")
        return fake, labs, sent

    def _deal(self, fake, labs, *, days, owner=MEMBER, activity_days=None):
        company = fake.add(
            "crm_companies",
            **{**DEFAULTS["crm_companies"], "workspace_id": labs["id"], "name": "Acme"},
        )
        entered = (self.NOW - timedelta(days=days)).isoformat()
        deal = fake.add(
            "crm_opportunities",
            **{
                **DEFAULTS["crm_opportunities"],
                "workspace_id": labs["id"],
                "name": "Acme pilot",
                "company_id": company["id"],
                "owner_id": owner["id"],
                "stage": "proposal",
                "probability": 50,
                "next_step": "Call",
                "next_step_on": "2026-11-30",
                "created_at": entered,
                "stage_entered_at": entered,
            },
        )
        if activity_days is not None:
            note = fake.add(
                "crm_notes",
                **{
                    **DEFAULTS["crm_notes"],
                    "workspace_id": labs["id"],
                    "body": "Spoke",
                    "created_at": (self.NOW - timedelta(days=activity_days)).isoformat(),
                },
            )
            fake.add(
                "crm_note_links",
                workspace_id=labs["id"],
                note_id=note["id"],
                record_type="opportunity",
                record_id=deal["id"],
            )
        return deal

    def _run(self, now=None):
        now = now or self.NOW
        tasks._flags(now)
        return tasks._automations(now)["runs"]["labs"]

    def _messages(self, sent, word):
        return [m for m in sent if word in m.subject]

    def test_notice_once_per_stage_entry(self, stuck):
        fake, labs, sent = stuck
        deal = self._deal(fake, labs, days=15, activity_days=1)
        self._run()
        self._run(self.NOW + timedelta(hours=1))
        [notice] = _bell(fake, "stuck_in_stage")
        assert (notice["user_id"], notice["record_id"]) == (MEMBER["id"], deal["id"])
        assert len(self._messages(sent, "stuck")) == 1
        # Re-entering the stage is a new stage entry: a new notice.
        fake.rows["crm_opportunities"][deal["id"]]["stage_entered_at"] = (
            self.NOW - timedelta(days=14, hours=1)
        ).isoformat()
        self._run(self.NOW + timedelta(hours=2))
        assert len(_bell(fake, "stuck_in_stage")) == 2

    def test_notice_one_message_with_a_stale_notice_the_same_day(self, stuck):
        fake, labs, sent = stuck
        # Both turned amber an hour ago (Stale deal reads the stage entry).
        self._deal(fake, labs, days=14 + 1 / 24)
        self._run()
        assert len(_bell(fake, "stale_deal")) == 1 and len(_bell(fake, "stuck_in_stage")) == 1
        assert len(self._messages(sent, "stale")) == 1
        assert self._messages(sent, "stuck") == []

    def test_notice_a_message_when_the_stale_notice_was_another_day(self, stuck):
        fake, labs, sent = stuck
        row = next(
            s for s in fake.rows["crm_workspace_settings"].values() if s["key"] == "automations"
        )
        row["value"] = {"stale_notice": True, "stuck_notice": False}
        self._deal(fake, labs, days=16)
        # Stale deal turned amber, and told its owner, two days ago.
        self._run(self.NOW - timedelta(days=2))
        assert len(self._messages(sent, "stale")) == 1
        row["value"] = {"stale_notice": True, "stuck_notice": True}
        self._run()
        assert len(self._messages(sent, "stuck")) == 1
        assert len(self._messages(sent, "stale")) == 1

    def test_notice_a_crm_only_owner_gets_the_bell_only(self, stuck):
        fake, labs, sent = stuck
        self._deal(fake, labs, days=15, owner=SELLER, activity_days=1)
        run = self._run()
        [notice] = _bell(fake, "stuck_in_stage")
        assert notice["user_id"] == SELLER["id"]
        assert sent == [] and run["counts"]["stuck_undeliverable"] == 1

    def test_notice_none_with_r2_off(self, stuck):
        fake, labs, sent = stuck
        row = next(
            s for s in fake.rows["crm_workspace_settings"].values() if s["key"] == "automations"
        )
        row["value"] = {"stale_notice": True, "stuck_notice": False}
        self._deal(fake, labs, days=15, activity_days=1)
        self._run()
        assert _bell(fake, "stuck_in_stage") == [] and sent == []


class TestStaleNoticeAfterStuckNotice:
    """Spec 009 FR-CRMAI-RULE-04; Q-14, the reverse order — the Stuck in
    stage notice, with its message, went earlier today; when the deal's Stale
    deal flag reaches the same level later the same day, the owner gets the
    stale notice in the bell only: one message a day. On a different day,
    both messages go."""

    NOW = TestStuckInStageNotice.NOW

    stuck = TestStuckInStageNotice.stuck
    _deal = TestStuckInStageNotice._deal
    _run = TestStuckInStageNotice._run
    _messages = TestStuckInStageNotice._messages

    def test_stale_notice_bell_only_after_a_stuck_message_the_same_day(self, stuck):
        fake, labs, sent = stuck
        # In the stage 14 days and 3 hours; last activity 14 days and 1 hour ago.
        self._deal(fake, labs, days=14 + 3 / 24, activity_days=14 + 1 / 24)
        # Two hours ago (10:00 in Chennai): stuck amber, not yet stale.
        self._run(self.NOW - timedelta(hours=2))
        assert len(_bell(fake, "stuck_in_stage")) == 1 and _bell(fake, "stale_deal") == []
        assert len(self._messages(sent, "stuck")) == 1
        # Now (noon the same day): stale amber, the bell only.
        self._run()
        [notice] = _bell(fake, "stale_deal")
        assert notice["user_id"] == MEMBER["id"]
        assert self._messages(sent, "stale") == []
        assert len(self._messages(sent, "stuck")) == 1

    def test_stale_notice_a_message_when_the_stuck_message_was_another_day(self, stuck):
        fake, labs, sent = stuck
        # In the stage 16 days; last activity 14 days and 1 hour ago.
        self._deal(fake, labs, days=16, activity_days=14 + 1 / 24)
        # Two days ago: stuck amber, its message; not yet stale.
        self._run(self.NOW - timedelta(days=2))
        assert len(self._messages(sent, "stuck")) == 1 and _bell(fake, "stale_deal") == []
        # Today: stale amber, with its message.
        self._run()
        assert len(_bell(fake, "stale_deal")) == 1
        assert len(self._messages(sent, "stale")) == 1
        assert len(self._messages(sent, "stuck")) == 1

    def test_stale_notice_one_message_when_both_turn_amber_between_runs(self, stuck):
        fake, labs, sent = stuck
        # Stuck amber three hours ago, stale amber an hour ago, one run now.
        self._deal(fake, labs, days=14 + 3 / 24, activity_days=14 + 1 / 24)
        self._run()
        assert len(_bell(fake, "stale_deal")) == 1 and len(_bell(fake, "stuck_in_stage")) == 1
        assert len(self._messages(sent, "stuck")) == 1
        assert self._messages(sent, "stale") == []
