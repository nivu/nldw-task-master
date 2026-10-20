"""The follow-up rules in the real database — spec 009 FR-CRMAI-RULE-01, -02,
-05, -06, FR-CRMAI-JOB-01, FR-CRMAI-CHG-08; Q-15, Q-16; D-4; data-model
§3.7, §3.8, §3.9, §3.11; migration 066.

What only Postgres can prove: every workspace's `automations` and
`flag_thresholds` rows carry the new keys, off and at their defaults, and a
re-run of 066 merges them into a row that lacks them while keeping what is
stored; a workspace inserted after 066 is seeded with every new key off;
`status_changed_at` is backfilled from the audit log (else `updated_at`)
without moving any member's `updated_at` or writing a member audit row, and
afterwards moves only when the status changes; the kind CHECK takes
`stuck_in_stage` and the job CHECK `followup_drafts`, which
`crm_job_latest()` reports; and the two fact functions read what they
should, through the real store. RLS, grants and EXECUTE are checked by
`test_crm_security_db.py`.

Each test works in a workspace of its own, except the seed tests, which
read Labs and Academy.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from supabase import create_client

from app.domain.crm import flags as flag_rules
from app.domain.crm import settings as setting_rules
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
MIGRATION = Path(__file__).resolve().parents[3] / "supabase/migrations/066_crm_follow_up_rules.sql"
#: Spec 010: the migration that replaces 066's `crm_workspaces_seed_ai`,
#: `crm_job_latest` and job CHECK. Re-run after 066, so the database the
#: later tests share is still the one the tree builds.
LATER = Path(__file__).resolve().parents[3] / "supabase/migrations/072_crm_capture_discovery.sql"
RULE_SWITCHES = {
    "no_reply_draft": False,
    "stuck_notice": False,
    "lead_cold": False,
    "campaign_followthrough": False,
}
RULE_DAYS = {
    "stuck_discovery_days": 14,
    "stuck_qualification_days": 21,
    "stuck_proposal_days": 14,
    "stuck_negotiation_days": 21,
    "no_reply_days": 5,
    "lead_cold_days": 30,
}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(ws: str, owner: str) -> dict:
    return {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    key = f"r{uuid.uuid4().hex[:12]}"
    workspace = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Rules ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(owner)}, 'admin')",
    )
    return workspace


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _settings(stack, ws: str) -> dict:
    return {
        r["key"]: r["value"]
        for r in stack.sql(
            "SELECT key, value FROM crm_workspace_settings "
            f"WHERE workspace_id = {stack.q(ws)} AND key IN ('automations', 'flag_thresholds')"
        )
    }


def _company(stack, ws, owner, name="Acme", **values) -> str:
    ops = [
        {
            "op": "insert",
            "table": "crm_companies",
            "values": {"name": name, "owner_id": owner, **values},
        }
    ]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _person(stack, ws, owner, company: str | None, first="Priya") -> str:
    values = {"first_name": first, "owner_id": owner, "company_id": company}
    ops = [{"op": "insert", "table": "crm_people", "values": values}]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _member(stack, ws, owner, company: str, status="engaged") -> dict:
    [campaign, member] = _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_campaigns",
                "ref": "c",
                "values": {"name": f"Roundtable {uuid.uuid4().hex[:6]}", "owner_id": owner},
            },
            {
                "op": "insert",
                "table": "crm_campaign_members",
                "values": {"campaign_id": "$ref:c", "company_id": company, "status": status},
            },
        ],
    )
    return member["row"]


def _member_row(stack, member_id: str) -> dict:
    return stack.sql(
        "SELECT status, status_changed_at, updated_at FROM crm_campaign_members "
        f"WHERE id = {stack.q(member_id)}"
    )[0]


def _rerun(stack, before: str = "") -> None:
    """066 again, in one transaction, after `before`: it is idempotent. Then
    072, which replaces some of 066's objects, so nothing later sees 066's."""
    stack.sql(before + MIGRATION.read_text() + "\n" + LATER.read_text())


class TestTheSettings:
    """FR-CRMAI-RULE-01, data-model §3.7."""

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_followup_db_every_workspace_has_the_new_keys_off(self, stack, key):
        [row] = stack.sql(f"SELECT id FROM crm_workspaces WHERE key = {stack.q(key)}")
        found = _settings(stack, row["id"])
        assert {k: found["automations"][k] for k in RULE_SWITCHES} == RULE_SWITCHES
        assert {k: found["flag_thresholds"][k] for k in RULE_DAYS} == RULE_DAYS
        assert setting_rules.setting_refusal("automations", found["automations"]) is None
        assert setting_rules.setting_refusal("flag_thresholds", found["flag_thresholds"]) is None

    def test_followup_db_a_workspace_made_later_is_seeded_with_every_key_off(self, stack, ws):
        found = _settings(stack, ws)
        assert found["automations"] == {
            "email_records": True,
            "stale_notice": True,
            "digests": True,
            "task_reminders": True,
            "campaign_conversion": True,
            **RULE_SWITCHES,
        }
        assert found["flag_thresholds"] == dict(flag_rules.DEFAULT_THRESHOLDS)

    def test_followup_db_the_merge_adds_missing_keys_and_keeps_stored_values(self, stack, ws):
        old_switches = {
            "email_records": True,
            "stale_notice": False,
            "digests": True,
            "task_reminders": True,
            "campaign_conversion": True,
        }
        old_days = {
            k: v for k, v in flag_rules.DEFAULT_THRESHOLDS.items() if k not in RULE_DAYS
        } | {"stale_red_days": 30}
        _as_system(
            stack,
            f"UPDATE crm_workspace_settings SET value = {_j(stack, old_switches)} "
            f"WHERE workspace_id = {stack.q(ws)} AND key = 'automations'; "
            f"UPDATE crm_workspace_settings SET value = {_j(stack, old_days)} "
            f"WHERE workspace_id = {stack.q(ws)} AND key = 'flag_thresholds'",
        )
        _rerun(stack)
        found = _settings(stack, ws)
        assert found["automations"] == {**old_switches, **RULE_SWITCHES}
        assert found["flag_thresholds"] == {**old_days, **RULE_DAYS}
        assert found["flag_thresholds"]["stale_red_days"] == 30


class TestStatusChangedAt:
    """FR-CRMAI-RULE-06, Q-16, data-model §3.9."""

    def test_followup_db_backfilled_from_the_audit_else_updated_at(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        moved = _member(stack, ws, owner, company, status="invited")
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_campaign_members",
                    "id": moved["id"],
                    "values": {"status": "engaged"},
                }
            ],
        )
        # A member no audit row names: written with the audit trigger off.
        other = _company(stack, ws, owner, name="Bolt")
        unaudited = str(uuid.uuid4())
        stack.sql(
            "ALTER TABLE crm_campaign_members DISABLE TRIGGER crm_campaign_members_crm_audit; "
            "INSERT INTO crm_campaign_members (id, workspace_id, campaign_id, company_id, status, "
            "updated_at) SELECT "
            f"{stack.q(unaudited)}, workspace_id, campaign_id, {stack.q(other)}, 'engaged', "
            "'2026-01-02T00:00:00+00:00' FROM crm_campaign_members "
            f"WHERE id = {stack.q(moved['id'])}; "
            "ALTER TABLE crm_campaign_members ENABLE TRIGGER crm_campaign_members_crm_audit"
        )
        [last_status] = stack.sql(
            "SELECT max(at) AS at FROM audit_log WHERE target_table = 'crm_campaign_members' "
            f"AND target_id = {stack.q(moved['id'])} AND after ? 'status'"
        )
        members = f"workspace_id = {stack.q(ws)}"
        before = stack.sql(
            f"SELECT id, updated_at FROM crm_campaign_members WHERE {members} ORDER BY id"
        )
        [audited] = stack.sql(
            "SELECT count(*) AS n FROM audit_log WHERE target_table = 'crm_campaign_members'"
        )
        _rerun(
            stack,
            "ALTER TABLE crm_campaign_members DISABLE TRIGGER crm_campaign_members_touch, "
            "DISABLE TRIGGER crm_campaign_members_crm_audit; "
            "ALTER TABLE crm_campaign_members ALTER COLUMN status_changed_at DROP NOT NULL; "
            f"UPDATE crm_campaign_members SET status_changed_at = NULL WHERE {members}; "
            "ALTER TABLE crm_campaign_members ENABLE TRIGGER crm_campaign_members_touch, "
            "ENABLE TRIGGER crm_campaign_members_crm_audit; ",
        )
        # No member's updated_at moved and no member audit row was written.
        after = stack.sql(
            f"SELECT id, updated_at FROM crm_campaign_members WHERE {members} ORDER BY id"
        )
        assert after == before
        [audited_after] = stack.sql(
            "SELECT count(*) AS n FROM audit_log WHERE target_table = 'crm_campaign_members'"
        )
        assert audited_after == audited
        rows = {
            r["id"]: r
            for r in stack.sql(
                "SELECT id, status_changed_at = updated_at AS from_updated, "
                f"status_changed_at = {stack.q(last_status['at'])}::timestamptz AS from_audit "
                f"FROM crm_campaign_members WHERE {members}"
            )
        }
        assert rows[moved["id"]]["from_audit"] is True
        assert rows[unaudited]["from_updated"] is True
        [nullable] = stack.sql(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'crm_campaign_members' AND column_name = 'status_changed_at'"
        )
        assert nullable == {"is_nullable": "NO", "column_default": "now()"}

    def test_followup_db_moved_only_by_a_status_change(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        member = _member(stack, ws, owner, company)
        first = _member_row(stack, member["id"])
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_campaign_members",
                    "id": member["id"],
                    "values": {"notes": "Met at the roundtable"},
                }
            ],
        )
        edited = _member_row(stack, member["id"])
        assert edited["status_changed_at"] == first["status_changed_at"]
        assert edited["updated_at"] != first["updated_at"]
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_campaign_members",
                    "id": member["id"],
                    "values": {"status": "meeting_booked"},
                }
            ],
        )
        moved = _member_row(stack, member["id"])
        assert moved["status_changed_at"] != first["status_changed_at"]
        assert moved["status_changed_at"] == moved["updated_at"]


class TestKindAndJob:
    """FR-CRMAI-CHG-08, FR-CRMAI-JOB-01, D-4."""

    def test_followup_db_the_kind_check_takes_stuck_in_stage(self, stack, ws, owner):
        rows = [
            {
                "workspace_id": ws,
                "user_id": owner,
                "kind": "stuck_in_stage",
                "title": "Deal stuck in Proposal",
                "dedupe_key": f"stuck:{uuid.uuid4()}:proposal:2026-10-01",
            }
        ]
        [added] = stack.sql(f"SELECT crm_notify({_j(stack, rows)}) AS r")[0]["r"]
        assert added["kind"] == "stuck_in_stage"
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(f"SELECT crm_notify({_j(stack, [{**rows[0], 'kind': 'stuck'}])})")
        assert refused.value.code == "23514"

    def test_followup_db_the_job_check_takes_followup_drafts(self, stack, ws, real):
        started = datetime.now(UTC)
        run = real.start_job_run("followup_drafts", ws, started)
        real.finish_job_run(
            run["id"],
            {"outcome": "ok", "counts": {"drafted": 1}, "finished_at": started.isoformat()},
        )
        latest = [
            r
            for r in real.latest_job_runs()
            if r["job"] == "followup_drafts" and r["workspace_id"] == ws
        ]
        assert [r["outcome"] for r in latest] == ["ok"]
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(
                "INSERT INTO crm_job_runs (job, workspace_id, started_at) "
                f"VALUES ('drafts', {stack.q(ws)}, now())"
            )
        assert refused.value.code == "23514"


class TestLeadActivity:
    """066 `crm_lead_activity` (Q-15)."""

    def test_followup_db_each_kind_of_activity_counts(self, stack, ws, owner, real):
        quiet = _company(stack, ws, owner, name="Quiet", lead_stage="contacted")
        contacted = _company(stack, ws, owner, name="Called")
        # System-written (FR-CRM-CO-03), as email sync writes it.
        _as_system(
            stack,
            "UPDATE crm_companies SET last_contact_at = '2026-09-01T10:00:00+00:00' "
            f"WHERE id = {stack.q(contacted)}",
        )
        noted = _company(stack, ws, owner, name="Noted")
        tasked = _company(stack, ws, owner, name="Tasked")
        staged = _company(stack, ws, owner, name="Staged")
        dealt = _company(stack, ws, owner, name="Dealt", lead_stage="qualified")
        actor = _actor(ws, owner)
        _apply(
            stack,
            actor,
            [
                {"op": "insert", "table": "crm_notes", "ref": "n", "values": {"body": "Called"}},
                {
                    "op": "insert",
                    "table": "crm_note_links",
                    "values": {"note_id": "$ref:n", "record_type": "company", "record_id": noted},
                },
                {
                    "op": "insert",
                    "table": "tasks",
                    "ref": "t",
                    "values": {"title": "Call", "assignee_id": owner, "bucket": "later"},
                },
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {"task_id": "$ref:t", "record_type": "company", "record_id": tasked},
                },
                {
                    "op": "update",
                    "table": "crm_companies",
                    "id": staged,
                    "values": {"lead_stage": "nurturing"},
                },
                {
                    "op": "insert",
                    "table": "crm_opportunities",
                    "values": {
                        "name": "Pilot",
                        "company_id": dealt,
                        "owner_id": owner,
                        "probability": 10,
                        "next_step": "Call",
                        "next_step_on": "2026-12-01",
                    },
                },
            ],
        )
        found = real.lead_activity(ws, None)
        assert found[quiet] is None
        assert datetime.fromisoformat(found[contacted]) == datetime(2026, 9, 1, 10, tzinfo=UTC)
        recent = datetime.now(UTC) - timedelta(minutes=5)
        for company in (noted, tasked, staged, dealt):
            assert datetime.fromisoformat(found[company]) > recent, company
        assert set(real.lead_activity(ws, [quiet, noted])) == {quiet, noted}


class TestNoReplyCandidates:
    """066 `crm_no_reply_candidates` (R-1)."""

    def _mailbox(self, stack, ws, owner) -> str:
        values = {"email": f"s{uuid.uuid4().hex[:12]}@nunnarilabs.com", "owner_id": owner}
        ops = [{"op": "insert", "table": "crm_mailboxes", "values": {**values, "labels": ["S"]}}]
        return _apply(stack, _actor(ws, owner), ops)[0]["id"]

    def _message(self, gmail_id, thread, sent_at, direction, links, **values) -> dict:
        return {
            "gmail_message_id": gmail_id,
            "thread_id": thread,
            "subject": "Pilot",
            "sent_at": sent_at,
            "direction": direction,
            "from_email": "x@example.com",
            "participants": [],
            "is_internal": False,
            "is_auto": False,
            "links": links,
            **values,
        }

    def test_followup_db_the_latest_outbound_with_no_later_inbound(self, stack, ws, owner, real):
        acme = _company(stack, ws, owner)
        priya = _person(stack, ws, owner, acme)
        bolt = _company(stack, ws, owner, name="Bolt")
        ravi = _person(stack, ws, owner, bolt, first="Ravi")
        box = self._mailbox(stack, ws, owner)
        both = [
            {"record_type": "company", "record_id": acme},
            {"record_type": "person", "record_id": priya},
        ]
        bolts = [{"record_type": "person", "record_id": ravi}]
        real.store_emails(
            ws,
            box,
            [
                # Owed: our latest on t1, nothing from Acme or Priya since.
                self._message("a1", "t1", "2026-10-01T09:00:00+00:00", "inbound", both),
                self._message("a2", "t1", "2026-10-02T09:00:00+00:00", "outbound", both),
                # An internal or automatic message later does not count.
                self._message(
                    "a3", "t1", "2026-10-03T09:00:00+00:00", "inbound", both, is_internal=True
                ),
                self._message(
                    "a4", "t1", "2026-10-04T09:00:00+00:00", "inbound", both, is_auto=True
                ),
                # Ravi replied on another thread: t2 is not owed.
                self._message("b1", "t2", "2026-10-02T09:00:00+00:00", "outbound", bolts),
                self._message("b2", "t3", "2026-10-05T09:00:00+00:00", "inbound", bolts),
                # Too recent for the cut-off.
                self._message("c1", "t4", "2026-10-09T09:00:00+00:00", "outbound", both),
            ],
        )
        found = real.no_reply_candidates(ws, datetime(2026, 10, 8, tzinfo=UTC))
        assert found == [
            {
                "mailbox_id": box,
                "thread_id": "t1",
                "gmail_message_id": "a2",
                "sent_at": found[0]["sent_at"],
                "people": [priya],
                "companies": [acme],
            }
        ]
        assert datetime.fromisoformat(found[0]["sent_at"]) == datetime(2026, 10, 2, 9, tzinfo=UTC)

    def test_followup_db_a_trashed_record_is_not_a_match(self, stack, ws, owner, real):
        acme = _company(stack, ws, owner)
        box = self._mailbox(stack, ws, owner)
        real.store_emails(
            ws,
            box,
            [
                self._message(
                    "d1",
                    "t1",
                    "2026-10-02T09:00:00+00:00",
                    "outbound",
                    [{"record_type": "company", "record_id": acme}],
                )
            ],
        )
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "soft_delete", "table": "crm_companies", "id": acme}],
        )
        assert real.no_reply_candidates(ws, datetime(2026, 10, 8, tzinfo=UTC)) == []
