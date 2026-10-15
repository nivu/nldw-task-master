"""Today — spec 007 FR-CRM-TODAY-01 to -04, FR-CRM-CHG-13, FR-CRM-ACC-15;
spec §4.1 (Journey 4.1's independent test); Q-26, Q-50; contracts §2.8.

The clock is fixed at 2026-10-14 14:30 UTC: 20:00 on the 14th in Chennai,
already 01:30 on the 15th in Sydney (daylight time, UTC+11). Tamil, the
CRM-only seller, works in Sydney; the owner in Chennai.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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

from app.domain.crm import today as rules
from app.services.crm import today as service

NOW = datetime(2026, 10, 14, 14, 30, tzinfo=UTC)
LABS = "?workspace=labs"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    monkeypatch.setattr(service, "_now", lambda: NOW)
    http = crm_client(monkeypatch, fake=fake)
    acme = _company(fake, labs, "Acme", lead_stage="deal_open", owner_id=OWNER["id"])
    return http, fake, labs, acme


def _row(fake, table, labs, **values):
    return fake.add(
        table,
        **{
            **DEFAULTS.get(table, {}),
            "workspace_id": labs["id"],
            "created_at": "2026-10-01T00:00:00+00:00",
            **values,
        },
    )


def _company(fake, labs, name, **values):
    return _row(fake, "crm_companies", labs, name=name, **values)


def _deal(fake, labs, company, name, next_step_on, owner=SELLER["id"], **values):
    return _row(
        fake,
        "crm_opportunities",
        labs,
        name=name,
        company_id=company["id"],
        owner_id=owner,
        probability=10,
        next_step="Send the deck",
        next_step_on=next_step_on,
        **values,
    )


def _task(fake, labs, title, *, assignee=SELLER["id"], links=(), **values):
    task = _row(fake, "crm_tasks", labs, title=title, assignee_id=assignee, **values)
    for record_type, record in links:
        _row(
            fake,
            "crm_task_links",
            labs,
            task_id=task["id"],
            record_type=record_type,
            record_id=record["id"],
        )
    return task


def _today(http, who=SELLER["id"], query=""):
    return http.get(f"/api/v1/crm/today{LABS}{query}", headers=as_(who))


def _section(answer, key):
    return next(s for s in answer["sections"] if s["key"] == key)


def _titles(answer, key):
    return [i["title"] for i in _section(answer, key)["items"]]


class TestSections:
    """FR-CRM-TODAY-01, -02."""

    def test_the_five_sections_in_order(self, crm):
        http, *_ = crm
        answer = _today(http).json()
        assert [s["key"] for s in answer["sections"]] == [
            "replies_owed",
            "next_steps",
            "tasks",
            "silent_clients",
            "loose_ends",
        ]

    def test_email_sections_say_sync_is_off(self, crm):
        http, *_ = crm
        answer = _today(http).json()
        for key in ("replies_owed", "silent_clients"):
            assert _section(answer, key) == {
                "key": key,
                "off_reason": "Email sync is off",
                "items": [],
            }
        for key in ("next_steps", "tasks", "loose_ends"):
            assert "off_reason" not in _section(answer, key)

    def test_once_sync_is_on_they_no_longer_say_so(self, crm):
        http, fake, labs, _ = crm
        fake.add(
            "crm_workspace_settings",
            workspace_id=labs["id"],
            key="email_sync",
            value={"enabled": True},
        )
        answer = _today(http).json()
        assert "off_reason" not in _section(answer, "replies_owed")
        assert "off_reason" not in _section(answer, "silent_clients")


class TestNextSteps:
    """FR-CRM-TODAY-01 §2 — my open deals, due today or overdue."""

    def test_overdue_first_then_due_today(self, crm):
        http, fake, labs, acme = crm
        _deal(fake, labs, acme, "Due today", "2026-10-15")
        _deal(fake, labs, acme, "Overdue", "2026-10-10")
        _deal(fake, labs, acme, "Tomorrow", "2026-10-16")
        _deal(fake, labs, acme, "Not mine", "2026-10-10", owner=OWNER["id"])
        _deal(fake, labs, acme, "Trashed", "2026-10-10", deleted_at="2026-10-11T00:00:00+00:00")
        _deal(
            fake,
            labs,
            acme,
            "Won",
            "2026-10-10",
            stage="closed_won",
        )
        items = _section(_today(http).json(), "next_steps")["items"]
        assert [(i["title"], i["level"], i["due_on"]) for i in items] == [
            ("Overdue", "overdue", "2026-10-10"),
            ("Due today", "today", "2026-10-15"),
        ]
        assert items[0]["record_type"] == "opportunity"
        assert items[0]["detail"] == "Send the deck · Acme"


class TestTasks:
    """FR-CRM-TODAY-01 §3 — my open tasks in the Today bucket or due."""

    def test_overdue_then_due_today_then_the_today_bucket(self, crm):
        http, fake, labs, acme = crm
        _task(fake, labs, "Bucket", bucket="today", links=[("company", acme)])
        _task(fake, labs, "Due today", due_on="2026-10-15")
        _task(fake, labs, "Overdue", due_on="2026-10-13")
        _task(fake, labs, "Tomorrow", due_on="2026-10-16")
        _task(fake, labs, "This week", bucket="this_week")
        _task(
            fake,
            labs,
            "Done",
            due_on="2026-10-15",
            status="done",
            completed_at="2026-10-14T10:00:00+00:00",
        )
        _task(fake, labs, "Someone else's", due_on="2026-10-15", assignee=OWNER["id"])
        items = _section(_today(http).json(), "tasks")["items"]
        assert [(i["title"], i["level"]) for i in items] == [
            ("Overdue", "overdue"),
            ("Due today", "today"),
            ("Bucket", None),
        ]
        assert items[2]["detail"] == "Acme"
        # A task has no page: its line opens the record it is linked to.
        assert items[2]["opens"] == {"record_type": "company", "record_id": acme["id"]}
        assert items[0]["opens"] is None


class TestLooseEnds:
    """FR-CRM-TODAY-01 §5, spec §5.21 — Nurturing, no open task, no next
    touch."""

    def test_only_nurturing_companies_with_nothing_planned(self, crm):
        http, fake, labs, _ = crm
        loose = _company(fake, labs, "Loose", lead_stage="nurturing", owner_id=SELLER["id"])
        planned = _company(fake, labs, "Planned", lead_stage="nurturing", owner_id=SELLER["id"])
        _task(fake, labs, "Follow up", links=[("company", planned)])
        finished = _company(fake, labs, "Finished", lead_stage="nurturing", owner_id=SELLER["id"])
        _task(
            fake,
            labs,
            "Old",
            status="done",
            completed_at="2026-10-01T10:00:00+00:00",
            links=[("company", finished)],
        )
        _company(
            fake,
            labs,
            "Touching",
            lead_stage="nurturing",
            owner_id=SELLER["id"],
            next_touch_on="2026-11-01",
        )
        _company(fake, labs, "Contacted", lead_stage="contacted", owner_id=SELLER["id"])
        _company(fake, labs, "Not mine", lead_stage="nurturing", owner_id=OWNER["id"])
        items = _section(_today(http).json(), "loose_ends")["items"]
        assert [i["title"] for i in items] == ["Finished", "Loose"]
        assert items[1] == {
            "record_type": "company",
            "record_id": loose["id"],
            "title": "Loose",
            "detail": "Nurturing, with no open task and no next touch",
            "due_on": None,
            "level": None,
        }


class TestTheDayIsThePersons:
    """FR-CRM-ACC-15 — Today turns over at the person's midnight."""

    def test_a_sydney_sellers_day_has_already_turned(self, crm):
        http, fake, labs, acme = crm
        _deal(fake, labs, acme, "Sydney deal", "2026-10-15")
        _deal(fake, labs, acme, "Chennai deal", "2026-10-15", owner=OWNER["id"])
        sydney = _today(http).json()
        assert (sydney["as_of"], sydney["timezone"]) == ("2026-10-15", "Australia/Sydney")
        assert _titles(sydney, "next_steps") == ["Sydney deal"]
        chennai = _today(http, OWNER["id"]).json()
        assert (chennai["as_of"], chennai["timezone"]) == ("2026-10-14", "Asia/Kolkata")
        assert _titles(chennai, "next_steps") == []

    def test_the_pure_day(self):
        assert rules.day_of(NOW, "Australia/Sydney") == date(2026, 10, 15)
        assert rules.day_of(NOW, "Asia/Kolkata") == date(2026, 10, 14)
        assert rules.day_of(NOW, None) == date(2026, 10, 14)


class TestAnotherMembersToday:
    """FR-CRM-TODAY-03, FR-CRM-CHG-13 (Q-26)."""

    def test_an_admin_may_pick_a_member(self, crm):
        http, fake, labs, acme = crm
        _deal(fake, labs, acme, "Tamil's deal", "2026-10-15")
        answer = _today(http, OWNER["id"], f"&person={SELLER['id']}").json()
        assert answer["person"] == {"id": SELLER["id"], "name": "Tamil"}
        assert answer["timezone"] == "Australia/Sydney"
        assert _titles(answer, "next_steps") == ["Tamil's deal"]

    @pytest.mark.parametrize("who", [MEMBER["id"], VIEWER["id"]])
    def test_a_member_or_viewer_may_not(self, crm, who):
        http, *_ = crm
        r = _today(http, who, f"&person={SELLER['id']}")
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a workspace admin can see another member's Today."

    def test_naming_yourself_is_your_own(self, crm):
        http, *_ = crm
        assert _today(http, MEMBER["id"], f"&person={MEMBER['id']}").status_code == 200

    def test_someone_outside_the_workspace_is_not_found(self, crm):
        http, *_ = crm
        r = _today(http, OWNER["id"], f"&person={OUTSIDER['id']}")
        assert (r.status_code, r.json()["detail"]) == (404, "No such member in this workspace.")


class TestJourney41:
    """Spec §4.1 independent test: a CRM-only Member with two overdue next
    steps and one task due today sees exactly those three lines; any TVA
    route is refused."""

    def test_exactly_three_lines_and_no_tva(self, crm):
        http, fake, labs, acme = crm
        _deal(fake, labs, acme, "Acme - AIOps", "2026-10-12")
        _deal(fake, labs, acme, "Acme - Data", "2026-10-13")
        _task(fake, labs, "Send the proposal", due_on="2026-10-15", links=[("company", acme)])
        answer = _today(http).json()
        lines = [i for s in answer["sections"] for i in s["items"]]
        assert [(i["record_type"], i["title"]) for i in lines] == [
            ("opportunity", "Acme - AIOps"),
            ("opportunity", "Acme - Data"),
            ("task", "Send the proposal"),
        ]
        refused = http.get("/api/v1/team/approvals", headers=as_(SELLER["id"]))
        assert (refused.status_code, refused.json()["detail"]) == (
            403,
            "This account is for the CRM only.",
        )


class TestPureRules:
    def test_may_see(self):
        assert rules.may_see(is_admin=False, caller_id="a", person_id="a") is None
        assert rules.may_see(is_admin=True, caller_id="a", person_id="b") is None
        assert rules.may_see(is_admin=False, caller_id="a", person_id="b") == rules.NOT_YOURS

    @pytest.mark.parametrize(
        ("settings", "on"),
        [
            ({}, False),
            ({"email_sync": {"enabled": False}}, False),
            ({"email_sync": {"enabled": "yes"}}, False),
            ({"email_sync": {"enabled": True}}, True),
        ],
    )
    def test_email_sync_switch(self, settings, on):
        assert rules.email_sync_on(settings) is on
