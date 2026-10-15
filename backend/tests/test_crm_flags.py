"""Follow-up and deal-health flags — spec 007 §6.2, FR-CRM-FLAG-01 to -05;
spec §11 AC "Reply-owed, client-silent and next-step flags match §6.2 in
fixed-clock tests", "Internal-only emails never change flags"; Q-20.

The clock is fixed at 2026-10-14 06:30 UTC, noon in Chennai. Each row of the
spec's table is checked at red, amber and none, on each side of each
threshold; then the service: thresholds read from the workspace's settings,
the worst flag of a deal and of a company, flags stored after a write and by
the workspace job, and a recompute that is not an edit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from crm_fake import DEFAULTS, OWNER, SELLER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import flags as rules
from app.services.crm import flags as service

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
TODAY = date(2026, 10, 14)
T = dict(rules.DEFAULT_THRESHOLDS)
LABS = "?workspace=labs"


def _mail(direction: str, ago: timedelta, **extra) -> dict:
    return {"direction": direction, "sent_at": (NOW - ago).isoformat(), **extra}


def _level(flag) -> str:
    return flag["level"] if flag else "none"


OPEN = {"id": "d1", "name": "Acme renewal", "stage": "proposal"}


class TestReplyOwed:
    """Their latest email newer than ours: red past 24 h, amber 12–24 h."""

    @pytest.mark.parametrize(
        ("hours", "level"),
        [(11.9, "none"), (12, "amber"), (24, "amber"), (24.1, "red"), (72, "red")],
    )
    def test_the_edges(self, hours, level):
        mails = [_mail("outbound", timedelta(days=5)), _mail("inbound", timedelta(hours=hours))]
        assert _level(rules.reply_owed(mails, NOW, T)) == level

    def test_answered_is_none(self):
        mails = [_mail("inbound", timedelta(days=3)), _mail("outbound", timedelta(days=2))]
        assert rules.reply_owed(mails, NOW, T) is None

    def test_no_email_is_none(self):
        assert rules.reply_owed([], NOW, T) is None

    def test_thresholds_move_it(self):
        mails = [_mail("inbound", timedelta(hours=7))]
        t = {**T, "reply_owed_amber_hours": 6, "reply_owed_red_hours": 8}
        assert _level(rules.reply_owed(mails, NOW, t)) == "amber"
        t = {**T, "reply_owed_amber_hours": 2, "reply_owed_red_hours": 6}
        assert _level(rules.reply_owed(mails, NOW, t)) == "red"


class TestClientSilent:
    """No inbound after our last outbound: red past 3 days, amber 2–3."""

    @pytest.mark.parametrize(
        ("days", "level"),
        [(1.9, "none"), (2, "amber"), (3, "amber"), (3.1, "red")],
    )
    def test_the_edges(self, days, level):
        mails = [_mail("inbound", timedelta(days=10)), _mail("outbound", timedelta(days=days))]
        assert _level(rules.client_silent(OPEN, mails, NOW, T)) == level

    def test_a_reply_since_clears_it(self):
        mails = [_mail("outbound", timedelta(days=5)), _mail("inbound", timedelta(days=4))]
        assert rules.client_silent(OPEN, mails, NOW, T) is None

    def test_only_for_an_open_deal(self):
        mails = [_mail("outbound", timedelta(days=9))]
        assert rules.client_silent({**OPEN, "stage": "closed_won"}, mails, NOW, T) is None


class TestNextStep:
    """Next step date past: red; today or tomorrow: amber."""

    @pytest.mark.parametrize(
        ("on", "level"),
        [
            ("2026-10-13", "red"),
            ("2026-10-14", "amber"),
            ("2026-10-15", "amber"),
            ("2026-10-16", "none"),
        ],
    )
    def test_the_edges(self, on, level):
        assert _level(rules.next_step({**OPEN, "next_step_on": on}, TODAY)) == level

    def test_a_closed_deal_has_none(self):
        deal = {**OPEN, "stage": "closed_lost", "next_step_on": "2026-01-01"}
        assert rules.next_step(deal, TODAY) is None


class TestCloseDate:
    """Expected close past: red; within 7 days: amber."""

    @pytest.mark.parametrize(
        ("on", "level"),
        [
            ("2026-10-13", "red"),
            ("2026-10-14", "amber"),
            ("2026-10-21", "amber"),
            ("2026-10-22", "none"),
        ],
    )
    def test_the_edges(self, on, level):
        assert _level(rules.close_date({**OPEN, "expected_close_on": on}, TODAY, T)) == level

    def test_the_threshold_from_settings(self):
        deal = {**OPEN, "expected_close_on": "2026-10-24"}
        assert _level(rules.close_date(deal, TODAY, {**T, "close_amber_days": 10})) == "amber"


class TestStaleDeal:
    """Nothing for 21 days: red; 14–21: amber."""

    @pytest.mark.parametrize(
        ("days", "level"),
        [(13.9, "none"), (14, "amber"), (20.9, "amber"), (21, "red")],
    )
    def test_the_edges(self, days, level):
        assert _level(rules.stale_deal(OPEN, NOW - timedelta(days=days), NOW, T)) == level

    def test_it_falls_back_to_the_stage_change(self):
        deal = {**OPEN, "stage_entered_at": (NOW - timedelta(days=30)).isoformat()}
        assert _level(rules.stale_deal(deal, None, NOW, T)) == "red"

    def test_an_email_is_activity(self):
        deal = {**OPEN, "stage_entered_at": (NOW - timedelta(days=30)).isoformat()}
        got = rules.for_deal(
            deal, now=NOW, today=TODAY, t=T, messages=[_mail("outbound", timedelta(days=1))]
        )
        assert "stale_deal" not in {f["kind"] for f in got["flags"]}


class TestLooseEnd:
    """A company in Nurturing with no open task and no next touch: red."""

    def test_red(self):
        assert _level(rules.loose_end({"lead_stage": "nurturing"}, False)) == "red"

    @pytest.mark.parametrize(
        ("company", "open_task"),
        [
            ({"lead_stage": "nurturing"}, True),
            ({"lead_stage": "nurturing", "next_touch_on": "2026-11-01"}, False),
            ({"lead_stage": "contacted"}, False),
        ],
    )
    def test_none(self, company, open_task):
        assert rules.loose_end(company, open_task) is None


class TestTaskOverdue:
    """Due date past: red; due today: amber."""

    @pytest.mark.parametrize(
        ("on", "level"), [("2026-10-13", "red"), ("2026-10-14", "amber"), ("2026-10-15", "none")]
    )
    def test_the_edges(self, on, level):
        assert _level(rules.task_overdue({"status": "todo", "due_on": on}, TODAY)) == level

    def test_done_is_none(self):
        assert rules.task_overdue({"status": "done", "due_on": "2026-01-01"}, TODAY) is None


class TestWorst:
    """FR-CRM-FLAG-03 — a deal's health is its worst flag; a company's the
    worst across its open deals and its own."""

    def test_a_deal(self):
        deal = {**OPEN, "next_step_on": "2026-10-15", "expected_close_on": "2026-10-01"}
        got = rules.for_deal(deal, now=NOW, today=TODAY, t=T, last_activity=NOW)
        assert got["flag_level"] == "red"
        assert {f["kind"]: f["level"] for f in got["flags"]} == {
            "next_step": "amber",
            "close_date": "red",
        }

    def test_a_company_takes_its_deals(self):
        deals = [
            {"id": "a", "name": "A", "flag_level": "amber"},
            {"id": "b", "name": "B", "flag_level": "red"},
            {"id": "c", "name": "C", "flag_level": "none"},
        ]
        got = rules.for_company(
            {"lead_stage": "deal_open"}, now=NOW, t=T, has_open_task=False, open_deals=deals
        )
        assert got["flag_level"] == "red"
        assert [f["record_id"] for f in got["flags"]] == ["a", "b"]

    def test_a_company_takes_its_own(self):
        got = rules.for_company({"lead_stage": "nurturing"}, now=NOW, t=T, has_open_task=False)
        assert got["flag_level"] == "red"

    def test_levels(self):
        assert rules.worst([]) == "none"
        assert rules.worst(["amber", None, "none"]) == "amber"


class TestInternalAndAutoEmails:
    """FR-CRM-FLAG-04 — internal-only emails and auto-replies never change a
    flag."""

    def test_an_internal_inbound_owes_no_reply(self):
        mails = [_mail("inbound", timedelta(hours=30), is_internal=True)]
        assert rules.reply_owed(mails, NOW, T) is None

    def test_an_auto_reply_owes_no_reply(self):
        mails = [_mail("inbound", timedelta(hours=30), is_auto=True)]
        assert rules.reply_owed(mails, NOW, T) is None

    def test_an_internal_inbound_does_not_end_a_silence(self):
        mails = [
            _mail("outbound", timedelta(days=5)),
            _mail("inbound", timedelta(days=1), is_internal=True),
        ]
        assert _level(rules.client_silent(OPEN, mails, NOW, T)) == "red"

    def test_an_internal_outbound_is_not_our_reply(self):
        mails = [
            _mail("inbound", timedelta(hours=30)),
            _mail("outbound", timedelta(hours=1), is_internal=True),
        ]
        assert _level(rules.reply_owed(mails, NOW, T)) == "red"

    def test_internal_mail_alone_changes_nothing_on_a_deal(self):
        deal = {**OPEN, "stage_entered_at": NOW.isoformat()}
        internal = [_mail("inbound", timedelta(hours=40), is_internal=True)]
        assert rules.for_deal(deal, now=NOW, today=TODAY, t=T, messages=internal) == rules.for_deal(
            deal, now=NOW, today=TODAY, t=T
        )


class TestThresholdSettings:
    def test_defaults_fill_what_is_missing(self):
        assert rules.thresholds({"silent_red_days": 5})["silent_red_days"] == 5
        assert rules.thresholds(None) == rules.DEFAULT_THRESHOLDS
        assert rules.thresholds({"silent_red_days": "x"})["silent_red_days"] == 3

    def test_a_change_is_validated(self):
        assert rules.thresholds_refusal(dict(T)) is None
        assert "Unknown threshold" in rules.thresholds_refusal({**T, "x": 1})
        assert "cannot be more than" in rules.thresholds_refusal({**T, "stale_amber_days": 30})
        assert "whole number" in rules.thresholds_refusal({**T, "silent_red_days": -1})


# ---------------------------------------------------------------------------
# Stored flags — FR-CRM-FLAG-01 to -03 through the service
# ---------------------------------------------------------------------------


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, _ = two_workspaces(fake)
    fake.add_deal_lists(labs)
    monkeypatch.setattr(service, "_now", lambda: NOW)
    http = crm_client(monkeypatch, fake=fake)
    return http, fake, labs


def _row(fake, table, labs, **values):
    return fake.add(
        table,
        **{
            **DEFAULTS.get(table, {}),
            "workspace_id": labs["id"],
            "created_at": (NOW - timedelta(days=1)).isoformat(),
            **values,
        },
    )


def _deal(fake, labs, company, **values):
    return _row(
        fake,
        "crm_opportunities",
        labs,
        **{
            "name": "Acme renewal",
            "company_id": company["id"],
            "owner_id": SELLER["id"],
            "probability": 50,
            "stage": "qualification",
            "stage_entered_at": (NOW - timedelta(days=1)).isoformat(),
            "next_step": "Call",
            "next_step_on": "2026-10-30",
            **values,
        },
    )


class TestStored:
    def test_the_workspace_job_stores_every_flag(self, crm):
        _, fake, labs = crm
        acme = _row(fake, "crm_companies", labs, name="Acme", lead_stage="deal_open")
        deal = _deal(fake, labs, acme, next_step_on="2026-10-10")
        quiet = _row(fake, "crm_companies", labs, name="Quiet", lead_stage="nurturing")
        task = _row(
            fake, "crm_tasks", labs, title="Call", assignee_id=SELLER["id"], due_on="2026-10-14"
        )
        service.recompute_workspace(labs["id"], NOW)
        rows = fake.rows
        assert rows["crm_opportunities"][deal["id"]]["flag_level"] == "red"
        assert rows["crm_companies"][acme["id"]]["flag_level"] == "red"
        assert rows["crm_companies"][quiet["id"]]["flags"][0]["kind"] == "loose_end"
        assert rows["crm_tasks"][task["id"]]["flag_level"] == "amber"
        assert rows["crm_opportunities"][deal["id"]]["flags_computed_at"] == NOW.isoformat()

    def test_thresholds_come_from_settings(self, crm):
        _, fake, labs = crm
        acme = _row(fake, "crm_companies", labs, name="Acme", lead_stage="deal_open")
        deal = _deal(fake, labs, acme, expected_close_on="2026-10-24")
        service.recompute_workspace(labs["id"], NOW)
        assert fake.rows["crm_opportunities"][deal["id"]]["flag_level"] == "none"
        _row(
            fake,
            "crm_workspace_settings",
            labs,
            key="flag_thresholds",
            value={**T, "close_amber_days": 10},
        )
        service.recompute_workspace(labs["id"], NOW)
        assert fake.rows["crm_opportunities"][deal["id"]]["flag_level"] == "amber"

    def test_a_closed_deal_is_cleared(self, crm):
        _, fake, labs = crm
        acme = _row(fake, "crm_companies", labs, name="Acme", lead_stage="deal_open")
        deal = _deal(fake, labs, acme, next_step_on="2026-10-01")
        service.recompute_workspace(labs["id"], NOW)
        fake.rows["crm_opportunities"][deal["id"]].update(stage="closed_won", next_step=None)
        service.recompute_workspace(labs["id"], NOW)
        assert fake.rows["crm_opportunities"][deal["id"]]["flag_level"] == "none"
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "none"

    def test_stale_reads_notes_and_tasks(self, crm):
        _, fake, labs = crm
        acme = _row(fake, "crm_companies", labs, name="Acme", lead_stage="deal_open")
        old = (NOW - timedelta(days=30)).isoformat()
        deal = _deal(fake, labs, acme, created_at=old, stage_entered_at=old)
        service.recompute_workspace(labs["id"], NOW)
        assert fake.rows["crm_opportunities"][deal["id"]]["flag_level"] == "red"
        note = _row(fake, "crm_notes", labs, body="Spoke", created_at=NOW.isoformat())
        _row(
            fake,
            "crm_note_links",
            labs,
            note_id=note["id"],
            record_type="opportunity",
            record_id=deal["id"],
        )
        service.recompute_workspace(labs["id"], NOW)
        assert fake.rows["crm_opportunities"][deal["id"]]["flag_level"] == "none"

    def test_a_recompute_is_not_an_edit(self, crm):
        """038: no audit row, `updated_at` untouched."""
        _, fake, labs = crm
        acme = _row(fake, "crm_companies", labs, name="Q", lead_stage="nurturing")
        before = fake.rows["crm_companies"][acme["id"]]["updated_at"]
        audit = len(fake.audit)
        service.recompute_workspace(labs["id"], NOW)
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "red"
        assert fake.rows["crm_companies"][acme["id"]]["updated_at"] == before
        assert len(fake.audit) == audit


class TestAfterWrites:
    """FR-CRM-FLAG-02 — recomputed on every relevant change."""

    def test_a_task_due_today_is_flagged_as_it_is_made(self, crm):
        http, fake, labs = crm
        acme = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
        ).json()
        r = http.post(
            f"/api/v1/crm/tasks{LABS}",
            json={
                "title": "Call Acme",
                "due_on": "2026-10-13",
                "links": [{"record_type": "company", "record_id": acme["id"]}],
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201, r.text
        assert fake.rows["crm_tasks"][r.json()["id"]]["flag_level"] == "red"
        page = http.get(f"/api/v1/crm/tasks/{r.json()['id']}{LABS}", headers=as_(OWNER["id"]))
        assert page.json()["record"]["flag_level"] == "red"

    def test_completing_a_task_clears_it(self, crm):
        http, fake, labs = crm
        acme = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
        ).json()
        task = http.post(
            f"/api/v1/crm/tasks{LABS}",
            json={
                "title": "Call",
                "due_on": "2026-10-01",
                "links": [{"record_type": "company", "record_id": acme["id"]}],
            },
            headers=as_(OWNER["id"]),
        ).json()
        http.post(f"/api/v1/crm/tasks/{task['id']}/complete{LABS}", headers=as_(OWNER["id"]))
        assert fake.rows["crm_tasks"][task["id"]]["flag_level"] == "none"

    def test_a_company_moved_to_nurturing_becomes_a_loose_end(self, crm):
        http, fake, labs = crm
        acme = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
        ).json()
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "none"
        http.patch(
            f"/api/v1/crm/companies/{acme['id']}{LABS}",
            json={"lead_stage": "nurturing"},
            headers=as_(OWNER["id"]),
        )
        got = http.get(f"/api/v1/crm/companies/{acme['id']}{LABS}", headers=as_(OWNER["id"]))
        assert got.json()["record"]["flag_level"] == "red"
        assert got.json()["record"]["flags"][0]["label"] == "Loose end"

    def test_flags_cannot_be_set_by_hand(self, crm):
        http, _, _ = crm
        acme = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
        ).json()
        r = http.patch(
            f"/api/v1/crm/companies/{acme['id']}{LABS}",
            json={"flag_level": "none"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422


class TestFollowUpsOwed:
    """FR-CRM-VIEW-11 (1b) — amber or red, red first."""

    def test_the_seeded_view(self, crm):
        http, fake, labs = crm
        _row(fake, "crm_companies", labs, name="Calm", lead_stage="new")
        _row(fake, "crm_companies", labs, name="Amber", lead_stage="new", flag_level="amber")
        _row(fake, "crm_companies", labs, name="Red", lead_stage="new", flag_level="red")
        r = http.post(
            f"/api/v1/crm/query{LABS}",
            json={"object": "companies", "view": "follow_ups_owed"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert [row["name"] for row in r.json()["rows"]] == ["Red", "Amber"]
