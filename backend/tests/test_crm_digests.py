"""Digests — spec 007 FR-CRM-NOTIF-02 to -05, FR-CRM-ACC-11, FR-CRM-WS-02;
spec §11 AC "Each daily digest is delivered exactly once per person per day
at 09:00 their time"; Q-18, Q-19, Q-73; data-model §3.20.

Clocks: 2026-10-14 03:30 UTC is 09:00 in Chennai; 2026-10-13 22:00 UTC is
09:00 on the 14th in Sydney (daylight time, UTC+11). 2026-10-12 is a Monday.
The owner, Deepika and Devansh are in Chennai (the default); Tamil, the
CRM-only seller, is in Sydney.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from crm_fake import DEFAULTS, MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings
from app.domain.crm import digests as rules
from app.services.crm import digests, notifications
from app.tasks import crm as tasks

KOLKATA_9 = datetime(2026, 10, 14, 3, 30, tzinfo=UTC)
SYDNEY_9 = datetime(2026, 10, 13, 22, 0, tzinfo=UTC)
MONDAY_9 = datetime(2026, 10, 12, 3, 30, tzinfo=UTC)


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    http = crm_client(monkeypatch, fake=fake)
    sent: list = []
    monkeypatch.setattr(notifications, "deliver", lambda message: sent.append(message) or ["slack"])
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    return http, fake, labs, academy, sent


def _to(sent, who):
    return [m for m in sent if m.recipient_email == who["email"]]


def _deal(fake, ws, name, owner, amount="500000.00", next_step_on="2026-10-14"):
    company = fake.add(
        "crm_companies",
        **{**DEFAULTS["crm_companies"], "workspace_id": ws["id"], "name": f"{name} Co"},
    )
    return fake.add(
        "crm_opportunities",
        **{
            **DEFAULTS["crm_opportunities"],
            "workspace_id": ws["id"],
            "name": name,
            "company_id": company["id"],
            "owner_id": owner["id"],
            "stage": "qualification",
            "probability": 25,
            "amount": amount,
            "currency": "INR",
            "amount_in_inr": amount,
            "fx_rate_used": 1,
            "next_step": "Call back",
            "next_step_on": next_step_on,
        },
    )


class TestWindow:
    @pytest.mark.parametrize(
        ("now", "zone", "day"),
        [
            (KOLKATA_9, "Asia/Kolkata", date(2026, 10, 14)),
            (datetime(2026, 10, 14, 3, 44, tzinfo=UTC), "Asia/Kolkata", date(2026, 10, 14)),
            (datetime(2026, 10, 14, 3, 45, tzinfo=UTC), "Asia/Kolkata", None),
            (datetime(2026, 10, 14, 3, 29, tzinfo=UTC), "Asia/Kolkata", None),
            (SYDNEY_9, "Australia/Sydney", date(2026, 10, 14)),
            (KOLKATA_9, "Australia/Sydney", None),
        ],
    )
    def test_the_0900_window(self, now, zone, day):
        assert rules.local_day_in_window(now, zone) == day

    def test_indian_grouping(self):
        assert rules.inr("12345678.40") == "₹1,23,45,678"
        assert rules.inr("500") == "₹500"


class TestExactlyOnce:
    def test_two_ticks_in_one_window_send_once(self, crm):
        _, fake, _, _, sent = crm
        tasks._digests(KOLKATA_9)
        tasks._digests(datetime(2026, 10, 14, 3, 40, tzinfo=UTC))
        assert len(_to(sent, OWNER)) == 1
        assert len(_to(sent, MEMBER)) == 1
        assert len(_to(sent, VIEWER)) == 1
        assert len([d for d in fake.rows["crm_digest_sends"].values() if d["kind"] == "daily"]) == 3
        assert _to(sent, OWNER)[0].subject == "Your CRM day — 14 Oct 2026"

    def test_the_marker_holds_the_channels(self, crm):
        _, fake, _, _, _ = crm
        tasks._digests(KOLKATA_9)
        assert {tuple(d["channels"]) for d in fake.rows["crm_digest_sends"].values()} == {
            ("slack",)
        }

    def test_outside_0900_nobody(self, crm):
        _, _, _, _, sent = crm
        tasks._digests(datetime(2026, 10, 14, 6, 0, tzinfo=UTC))
        assert sent == []

    def test_each_in_their_own_window(self, crm, monkeypatch):
        _, _, _, _, sent = crm
        monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
        tasks._digests(SYDNEY_9)
        assert [m.recipient_email for m in sent] == [SELLER["email"]]
        sent.clear()
        tasks._digests(KOLKATA_9)
        assert SELLER["email"] not in {m.recipient_email for m in sent}
        assert OWNER["email"] in {m.recipient_email for m in sent}

    def test_the_run_is_logged_with_no_workspace(self, crm):
        _, fake, _, _, _ = crm
        result = tasks._digests(KOLKATA_9)
        assert result["runs"]["all"]["outcome"] == "ok"
        assert result["runs"]["all"]["counts"]["daily"] == 3
        [run] = fake.rows["crm_job_runs"].values()
        assert (run["job"], run["workspace_id"]) == ("digests", None)


class TestCrmOnlyWaitsForSmtp:
    """FR-CRM-NOTIF-02, Q-73."""

    def test_no_sender_no_digest_and_counted(self, crm):
        _, fake, _, _, sent = crm
        result = tasks._digests(SYDNEY_9)
        assert sent == []
        assert result["runs"]["all"]["counts"]["undeliverable"] == 1
        assert fake.rows["crm_digest_sends"] == {}

    def test_with_a_sender_it_goes(self, crm, monkeypatch):
        _, _, _, _, sent = crm
        monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
        tasks._digests(SYDNEY_9)
        assert len(_to(sent, SELLER)) == 1


class TestWeekly:
    """FR-CRM-NOTIF-04 — Mondays, to each workspace's Admins, one per
    workspace, apart from TVA's leadership digest."""

    def test_admins_on_monday(self, crm):
        _, fake, labs, _, sent = crm
        won = _deal(fake, labs, "Big win", OWNER, next_step_on="2026-11-01")
        fake.rows["crm_opportunities"][won["id"]].update(stage="closed_won", won_on="2026-10-09")
        tasks._digests(MONDAY_9)
        weekly = [m for m in sent if "CRM week" in m.subject]
        assert sorted(m.subject for m in weekly) == [
            "Nunnari Academy B2B CRM week — 2026-10-12",
            "Nunnari Labs CRM week — 2026-10-12",
        ]
        assert {m.recipient_email for m in weekly} == {OWNER["email"]}
        labs_week = next(m for m in weekly if m.subject.startswith("Nunnari Labs"))
        assert "Big win (₹5,00,000)" in labs_week.body
        assert "Follow-up and deal-health flags" in labs_week.body
        # The daily one still goes, separately.
        assert len([m for m in _to(sent, OWNER) if m.subject.startswith("Your CRM day")]) == 1

    def test_not_on_tuesday(self, crm):
        _, _, _, _, sent = crm
        tasks._digests(KOLKATA_9)
        assert not [m for m in sent if "CRM week" in m.subject]

    def test_once(self, crm):
        _, _, _, _, sent = crm
        tasks._digests(MONDAY_9)
        tasks._digests(MONDAY_9)
        assert len([m for m in sent if "CRM week" in m.subject]) == 2


class TestRoles:
    """FR-CRM-NOTIF-05, FR-CRM-ACC-11 — each section with the role there."""

    def test_a_viewers_digest_carries_no_amount(self, crm):
        _, fake, labs, _, sent = crm
        _deal(fake, labs, "Acme renewal", VIEWER)
        tasks._digests(KOLKATA_9)
        [mail] = _to(sent, VIEWER)
        assert "Acme renewal" in mail.body
        assert "5,00,000" not in mail.body and "500000" not in mail.body

    def test_viewer_in_one_member_in_the_other(self, crm):
        _, fake, labs, academy, sent = crm
        fake.add_member(academy, VIEWER["id"], "member")
        _deal(fake, labs, "Labs deal", VIEWER, amount="700000.00")
        _deal(fake, academy, "Academy deal", VIEWER, amount="300000.00")
        parts = digests.daily_parts(
            {**VIEWER, "timezone": "Asia/Kolkata"},
            [m for m in fake.list_all_memberships() if m["user_id"] == VIEWER["id"]],
            {w["id"]: w for w in fake.list_workspaces()},
            KOLKATA_9,
        )
        by = {p["workspace_key"]: p for p in parts}
        labs_items = [i for s in by["labs"]["sections"] for i in s["items"]]
        academy_items = [i for s in by["academy"]["sections"] for i in s["items"]]
        assert [i["title"] for i in labs_items] == ["Labs deal"]
        assert "amount" not in labs_items[0]
        assert academy_items[0]["amount"] == "₹3,00,000"
        tasks._digests(KOLKATA_9)
        [mail] = _to(sent, VIEWER)
        assert "₹3,00,000" in mail.body and "7,00,000" not in mail.body

    def test_a_removed_membership_is_not_in_it(self, crm):
        http, fake, _, academy, sent = crm
        membership = next(
            m
            for m in fake.rows["crm_memberships"].values()
            if m["workspace_id"] == academy["id"] and m["user_id"] == MEMBER["id"]
        )
        r = http.delete(
            f"/api/v1/crm/members/{membership['id']}?workspace=academy", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        tasks._digests(KOLKATA_9)
        [mail] = _to(sent, MEMBER)
        assert "Nunnari Labs" in mail.body and "Nunnari Academy B2B" not in mail.body
