"""Built-in automations 5 to 7 and their switches — spec 007 FR-CRM-AUTO-01,
-02, FR-CRM-LIST-06; data-model §7.3; plan §5.11.

Automation 5 (the stale-deal notice), 6 (digests) and 7 (task reminders)
each run behind a workspace switch; switched off, nothing is sent and the
run is still logged, `skipped`. Automations 9 and 10 (and 1, 3, 4) are the
pipeline rules themselves and have no switch.

The clock is 2026-10-14 06:30 UTC, noon in Chennai.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import DEFAULTS, MEMBER, OWNER, SELLER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings
from app.domain.crm import settings as setting_rules
from app.services.crm import flags, notifications
from app.tasks import crm as tasks

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
LABS = "?workspace=labs"
ON = {k: True for k in setting_rules.AUTOMATIONS}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    for ws in (labs, academy):
        fake.add("crm_workspace_settings", workspace_id=ws["id"], key="automations", value=dict(ON))
    http = crm_client(monkeypatch, fake=fake)
    sent: list = []
    monkeypatch.setattr(notifications, "deliver", lambda message: sent.append(message) or ["slack"])
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    return http, fake, labs, academy, sent


def _switch(fake, ws, **values):
    row = next(
        s
        for s in fake.rows["crm_workspace_settings"].values()
        if s["workspace_id"] == ws["id"] and s["key"] == "automations"
    )
    row["value"] = {**row["value"], **values}


def _runs(fake, job):
    return {r["workspace_id"]: r for r in fake.rows["crm_job_runs"].values() if r["job"] == job}


def _stale_deal(fake, ws, owner=MEMBER, days=15):
    company = fake.add(
        "crm_companies",
        **{**DEFAULTS["crm_companies"], "workspace_id": ws["id"], "name": "Acme"},
    )
    old = (NOW - timedelta(days=days)).isoformat()
    return fake.add(
        "crm_opportunities",
        **{
            **DEFAULTS["crm_opportunities"],
            "workspace_id": ws["id"],
            "name": "Acme renewal",
            "company_id": company["id"],
            "owner_id": owner["id"],
            "stage": "qualification",
            "probability": 25,
            "next_step": "Call",
            "next_step_on": "2026-11-30",
            "created_at": old,
            "stage_entered_at": old,
        },
    )


def _task(fake, ws, title, due_on, assignee=MEMBER):
    return fake.add(
        "crm_tasks",
        **{
            **DEFAULTS["crm_tasks"],
            "workspace_id": ws["id"],
            "title": title,
            "assignee_id": assignee["id"],
            "due_on": due_on,
        },
    )


def _bell(fake, kind):
    return [n for n in fake.rows["crm_notifications"].values() if n["kind"] == kind]


def _recompute(monkeypatch, now=NOW):
    monkeypatch.setattr(flags, "_now", lambda: now)
    tasks._flags(now)
    tasks._stale_notices(now)


class TestStaleDealNotice:
    """Automation 5 — the owner, and at red the admins too; once per level."""

    def test_amber_tells_the_owner_once(self, crm, monkeypatch):
        _, fake, labs, _, sent = crm
        deal = _stale_deal(fake, labs)
        _recompute(monkeypatch)
        [n] = _bell(fake, "stale_deal")
        assert (n["user_id"], n["record_id"]) == (MEMBER["id"], deal["id"])
        assert n["title"] == "Deal going stale (amber): “Acme renewal”"
        assert [m.recipient_email for m in sent] == [MEMBER["email"]]
        _recompute(monkeypatch)
        assert len(_bell(fake, "stale_deal")) == 1 and len(sent) == 1

    def test_red_tells_the_admins_too(self, crm, monkeypatch):
        _, fake, labs, _, _ = crm
        _stale_deal(fake, labs, days=22)
        _recompute(monkeypatch)
        assert sorted(n["user_id"] for n in _bell(fake, "stale_deal")) == sorted(
            [MEMBER["id"], OWNER["id"]]
        )

    def test_amber_then_red_is_a_second_notice(self, crm, monkeypatch):
        _, fake, labs, _, _ = crm
        _stale_deal(fake, labs, days=20)
        _recompute(monkeypatch)
        _recompute(monkeypatch, NOW + timedelta(days=2))
        assert sorted((n["user_id"], n["title"]) for n in _bell(fake, "stale_deal")) == sorted(
            [
                (MEMBER["id"], "Deal going stale (amber): “Acme renewal”"),
                (MEMBER["id"], "Deal going stale (red): “Acme renewal”"),
                (OWNER["id"], "Deal going stale (red): “Acme renewal”"),
            ]
        )

    def test_switched_off_nothing_and_skipped(self, crm, monkeypatch):
        _, fake, labs, academy, sent = crm
        _stale_deal(fake, labs)
        _switch(fake, labs, stale_notice=False)
        _recompute(monkeypatch)
        assert _bell(fake, "stale_deal") == [] and sent == []
        runs = _runs(fake, "automations")
        assert runs[labs["id"]]["outcome"] == "skipped"
        assert runs[academy["id"]]["outcome"] == "ok"
        # Flags themselves (automation 4) are not switchable: still computed.
        assert _runs(fake, "flags")[labs["id"]]["outcome"] == "ok"


class TestTaskReminders:
    """Automation 7 — due today in the bell; overdue also a message, once a
    day; never before 09:00 the assignee's time."""

    def test_due_and_overdue(self, crm):
        _, fake, labs, _, sent = crm
        today = _task(fake, labs, "Send deck", "2026-10-14")
        late = _task(fake, labs, "Chase PO", "2026-10-10")
        _task(fake, labs, "Later", "2026-10-20")
        result = tasks._reminders(NOW)
        assert result["runs"]["labs"]["counts"] == {
            "due_today": 1,
            "overdue": 1,
            "undeliverable": 0,
        }
        assert [n["record_id"] for n in _bell(fake, "task_due")] == [today["id"]]
        assert [n["record_id"] for n in _bell(fake, "task_overdue")] == [late["id"]]
        assert [m.subject for m in sent] == ["Task overdue: Chase PO"]
        tasks._reminders(NOW + timedelta(minutes=15))
        assert len(sent) == 1
        # The next day both are overdue: one message each.
        tasks._reminders(NOW + timedelta(days=1))
        assert sorted(m.subject for m in sent[1:]) == [
            "Task overdue: Chase PO",
            "Task overdue: Send deck",
        ]

    def test_not_before_nine(self, crm):
        _, fake, labs, _, sent = crm
        _task(fake, labs, "Chase PO", "2026-10-10")
        tasks._reminders(datetime(2026, 10, 14, 2, 0, tzinfo=UTC))  # 07:30 in Chennai
        assert _bell(fake, "task_overdue") == [] and sent == []

    def test_a_crm_only_assignee_waits_for_smtp(self, crm):
        _, fake, labs, _, sent = crm
        _task(fake, labs, "Chase PO", "2026-10-10", assignee=SELLER)
        result = tasks._reminders(NOW)
        assert result["runs"]["labs"]["counts"]["undeliverable"] == 1
        assert sent == [] and len(_bell(fake, "task_overdue")) == 1

    def test_switched_off_nothing_and_skipped(self, crm):
        _, fake, labs, academy, sent = crm
        _task(fake, labs, "Chase PO", "2026-10-10")
        _switch(fake, labs, task_reminders=False)
        tasks._reminders(NOW)
        assert _bell(fake, "task_overdue") == [] and sent == []
        runs = _runs(fake, "reminders")
        assert runs[labs["id"]]["outcome"] == "skipped"
        assert runs[labs["id"]]["counts"] == {"skipped": "Task reminders are switched off."}
        assert runs[academy["id"]]["outcome"] == "ok"


class TestDigestSwitch:
    """Automation 6."""

    def test_off_everywhere_nothing_and_skipped(self, crm):
        _, fake, labs, academy, sent = crm
        _switch(fake, labs, digests=False)
        _switch(fake, academy, digests=False)
        tasks._digests(datetime(2026, 10, 14, 3, 30, tzinfo=UTC))
        assert sent == []
        [run] = _runs(fake, "digests").values()
        assert run["outcome"] == "skipped"

    def test_off_in_one_leaves_it_out(self, crm):
        _, fake, labs, academy, sent = crm
        _switch(fake, academy, digests=False)
        tasks._digests(datetime(2026, 10, 14, 3, 30, tzinfo=UTC))
        [mail] = [m for m in sent if m.recipient_email == OWNER["email"]]
        assert "Nunnari Labs" in mail.body and "Nunnari Academy B2B" not in mail.body


class TestSwitchesInSettings:
    """FR-CRM-AUTO-02, FR-CRM-LIST-06."""

    def test_the_switchable_ones(self):
        assert set(setting_rules.AUTOMATIONS) == {
            "email_records",
            "stale_notice",
            "digests",
            "task_reminders",
            "campaign_conversion",
        }

    @pytest.mark.parametrize("unswitchable", ["closed_lost_tasks", "delivery_handoff", "flags"])
    def test_automations_9_and_10_have_no_switch(self, crm, unswitchable):
        http, *_ = crm
        r = http.patch(
            f"/api/v1/crm/settings/automations{LABS}",
            json={"value": {**ON, unswitchable: False}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert "Unknown automation" in r.json()["detail"]

    def test_an_admin_switches_one_off(self, crm):
        http, fake, labs, _, _ = crm
        r = http.patch(
            f"/api/v1/crm/settings/automations{LABS}",
            json={"value": {**ON, "digests": False}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert not notifications.automation_on(labs["id"], "digests")
        assert any(a["action"] == "setting.updated" for a in fake.audit)

    def test_a_member_cannot(self, crm):
        http, *_ = crm
        r = http.patch(
            f"/api/v1/crm/settings/automations{LABS}",
            json={"value": ON},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 403

    def test_thresholds_are_validated(self, crm):
        http, fake, labs, _, _ = crm
        fake.add(
            "crm_workspace_settings",
            workspace_id=labs["id"],
            key="flag_thresholds",
            value=dict(flags.rules.DEFAULT_THRESHOLDS),
        )
        good = {**flags.rules.DEFAULT_THRESHOLDS, "stale_red_days": 30}
        r = http.patch(
            f"/api/v1/crm/settings/flag_thresholds{LABS}",
            json={"value": good},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        bad = {**good, "silent_amber_days": 9}
        r = http.patch(
            f"/api/v1/crm/settings/flag_thresholds{LABS}",
            json={"value": bad},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
