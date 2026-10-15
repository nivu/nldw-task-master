"""The bell — spec 007 FR-CRM-NOTIF-01, -05, FR-CRM-NOTE-02, FR-CRM-WS-02,
FR-CRM-ACC-11; contracts §2.10; data-model §3.19.

Each event reaches exactly the people it is about, never a non-member and
never the person who caused it; no title carries an amount; the unread count
and marking read; and a member removed from a workspace keeps none of its
notifications, in the list, the count or the table.
"""

from __future__ import annotations

import re

import pytest
from crm_fake import MEMBER, OUTSIDER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
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

from app.services.crm import deals, notifications

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
