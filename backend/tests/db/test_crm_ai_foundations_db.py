"""The AI foundations in the real database — spec 009 FR-CRMAI-FLAG-01, -02,
FR-CRMAI-LLM-06, -07, FR-CRMAI-JOB-01, FR-CRMAI-CHG-07, -08; spec §15 "A
workspace that existed before the migration reads every new phase and rule
switch as off and can save its `ai`, `automations` and `flag_thresholds`
settings straight after the migration"; data-model §3.1, §3.7, §3.8, §5;
migration 060.

What only Postgres can prove: every workspace has the `ai` row, off with a
zero budget, and a workspace inserted later gets it, written as the system
with its creator's actor put back; the three widened CHECKs take the new
values and still refuse others; `crm_ai_spend` sums by month and job;
`crm_job_latest` reports the new job names once they have run; the usage
log is closed to the browser. Its place in `NOT_AUDITED` is checked by
`test_crm_security_db.py`.

Each test that writes works in a workspace of its own.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from supabase import create_client

from app.domain.crm import ai_rules
from app.domain.crm import settings as rules
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


@pytest.fixture
def ws(stack) -> str:
    key = f"ai{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('AI ' + key)}) RETURNING id",
    )[-1]["id"]


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _setting(stack, ws: str, key: str):
    rows = stack.sql(
        "SELECT value FROM crm_workspace_settings "
        f"WHERE workspace_id = {stack.q(ws)} AND key = {stack.q(key)}"
    )
    return rows[0]["value"] if rows else None


def _usage(stack, ws: str, job: str, cost: str, at: datetime) -> None:
    stack.sql(
        "INSERT INTO crm_ai_usage (workspace_id, job, model, input_tokens, output_tokens, "
        f"cost_usd, outcome, at) VALUES ({stack.q(ws)}, {stack.q(job)}, 'claude-sonnet-5-5', "
        f"10, 2, {cost}, 'ok', {stack.q(at.isoformat())})"
    )


class TestTheSeed:
    """FR-CRMAI-FLAG-02."""

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_foundations_every_existing_workspace_is_off_with_no_budget(self, stack, key):
        value = stack.sql(
            "SELECT s.value FROM crm_workspace_settings s JOIN crm_workspaces w "
            f"ON w.id = s.workspace_id WHERE w.key = {stack.q(key)} AND s.key = 'ai'"
        )[0]["value"]
        assert value == ai_rules.AI_OFF
        assert rules.setting_refusal("ai", value) is None

    def test_foundations_no_workspace_lacks_the_row(self, stack):
        rows = stack.sql(
            "SELECT w.key FROM crm_workspaces w WHERE NOT EXISTS (SELECT 1 FROM "
            "crm_workspace_settings s WHERE s.workspace_id = w.id AND s.key = 'ai')"
        )
        assert rows == []

    def test_foundations_a_workspace_inserted_later_gets_the_row(self, stack, ws):
        assert _setting(stack, ws, "ai") == ai_rules.AI_OFF
        [created] = stack.sql(
            "SELECT actor_label, action FROM audit_log WHERE target_table = "
            f"'crm_workspace_settings' AND workspace_id = {stack.q(ws)}"
        )
        assert created == {"actor_label": "system", "action": "setting.created"}

    def test_foundations_the_creators_actor_is_put_back(self, stack, make_profile):
        person = make_profile(keep=True)
        actor = {"kind": "user", "user_id": person, "channel": "web", "workspace_id": None}
        key = f"ai{uuid.uuid4().hex[:12]}"
        rows = stack.sql(
            f"SELECT set_config('crm.actor', {_j(stack, actor)}::text, true); "
            "INSERT INTO crm_workspaces (key, name) "
            f"VALUES ({stack.q(key)}, {stack.q('AI ' + key)}); "
            "SELECT current_setting('crm.actor', true) AS actor"
        )
        assert json.loads(rows[-1]["actor"]) == actor
        ws = stack.sql(f"SELECT id FROM crm_workspaces WHERE key = {stack.q(key)}")[0]["id"]
        labels = {
            r["target_table"]: r["actor_label"]
            for r in stack.sql(
                "SELECT target_table, actor_label FROM audit_log "
                f"WHERE workspace_id = {stack.q(ws)}"
            )
        }
        assert labels == {"crm_workspaces": "user", "crm_workspace_settings": "system"}


class TestSavingStraightAfterTheMigration:
    """Spec §15 — a pre-migration workspace's settings pass their validators
    and can be written."""

    @pytest.mark.parametrize("key", ["ai", "automations", "flag_thresholds"])
    def test_foundations_labs_settings_save(self, stack, key):
        [row] = stack.sql(
            "SELECT s.id, s.value FROM crm_workspace_settings s JOIN crm_workspaces w "
            f"ON w.id = s.workspace_id WHERE w.key = 'labs' AND s.key = {stack.q(key)}"
        )
        assert rules.setting_refusal(key, row["value"]) is None
        _as_system(
            stack,
            f"UPDATE crm_workspace_settings SET value = {_j(stack, row['value'])} "
            f"WHERE id = {stack.q(row['id'])}",
        )

    def test_foundations_the_key_check_takes_ai_and_refuses_others(self, stack, ws):
        with pytest.raises(stack.SqlError) as refused:
            _as_system(
                stack,
                "INSERT INTO crm_workspace_settings (workspace_id, key, value) "
                f"VALUES ({stack.q(ws)}, 'bogus', '{{}}'::jsonb)",
            )
        assert refused.value.code == "23514"
        definition = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'crm_workspace_settings_key_check'"
        )[0]["d"]
        assert "'ai'" in definition and "'email_sync'" in definition


class TestTheUsageLog:
    """FR-CRMAI-LLM-06, -07 — `crm_ai_spend` by month and job."""

    def test_foundations_spend_sums_by_month(self, stack, ws, real):
        october = datetime(2026, 10, 1, tzinfo=UTC) - timedelta(hours=5, minutes=30)
        _usage(stack, ws, "summaries", "1.250000", october + timedelta(days=3))
        _usage(stack, ws, "summaries", "0.500000", october + timedelta(days=4))
        _usage(stack, ws, "enrichment", "0.004000", october + timedelta(days=5))
        # One second before the Asia/Kolkata month began.
        _usage(stack, ws, "summaries", "9.000000", october - timedelta(seconds=1))
        spend = real.ai_spend(ws, october)
        assert spend == {
            "total_usd": "1.754000",
            "by_job": {"summaries": "1.750000", "enrichment": "0.004000"},
        }

    def test_foundations_spend_of_a_quiet_month_is_zero(self, stack, ws, real):
        assert real.ai_spend(ws, datetime(2026, 1, 1, tzinfo=UTC)) == {
            "total_usd": "0",
            "by_job": {},
        }

    def test_foundations_the_store_writes_and_reads_a_usage_row(self, stack, ws, real):
        now = datetime.now(UTC)
        real.insert_ai_usage(
            ws,
            {
                "job": "ai_fields",
                "model": "claude-sonnet-5-5",
                "input_tokens": 3,
                "output_tokens": 1,
                "cost_usd": "0.000024",
                "outcome": "invalid_output",
                "at": now.isoformat(),
            },
        )
        assert real.ai_spend(ws, now - timedelta(minutes=1))["by_job"] == {"ai_fields": "0.000024"}
        assert real.ai_setting(ws) == ai_rules.AI_OFF

    @pytest.mark.parametrize(
        "column",
        ["job = 'review_expiry'", "outcome = 'skipped'", "input_tokens = -1", "cost_usd = -1"],
    )
    def test_foundations_the_usage_checks(self, stack, ws, column):
        _usage(stack, ws, "summaries", "0.1", datetime.now(UTC))
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(f"UPDATE crm_ai_usage SET {column} WHERE workspace_id = {stack.q(ws)}")
        assert refused.value.code == "23514"

    def test_foundations_the_browser_cannot_read_it(self, stack, seller):
        row = stack.sql(
            "SELECT c.relrowsecurity AS rls, "
            "has_table_privilege('anon', 'public.crm_ai_usage', 'SELECT') AS anon, "
            "has_table_privilege('authenticated', 'public.crm_ai_usage', "
            "'SELECT,INSERT,UPDATE,DELETE') AS authed, "
            "has_function_privilege('authenticated', "
            "'public.crm_ai_spend(uuid, timestamptz)', 'EXECUTE') AS spend, "
            "has_function_privilege('anon', 'public.crm_workspaces_seed_ai()', 'EXECUTE') "
            "AS seed FROM pg_class c WHERE c.oid = 'public.crm_ai_usage'::regclass"
        )[0]
        assert row == {"rls": True, "anon": False, "authed": False, "spend": False, "seed": False}
        r = stack.rest("crm_ai_usage", bearer=stack.jwt_for(seller))
        assert r.status_code in (401, 403, 404) or r.json() == [], r.text


class TestJobNamesAndNotifications:
    """FR-CRMAI-JOB-01, FR-CRMAI-CHG-07, -08."""

    def test_foundations_new_job_names_are_accepted_and_reported(self, stack, ws, real):
        names = ("summaries", "meetings", "enrichment", "ai_fields", "review_expiry")
        assert {r["job"] for r in real.latest_job_runs() if r["workspace_id"] == ws} == set()
        for name in names:
            run = real.start_job_run(name, ws, datetime.now(UTC))
            real.finish_job_run(
                run["id"], {"outcome": "skipped", "finished_at": datetime.now(UTC).isoformat()}
            )
        latest = {r["job"]: r["outcome"] for r in real.latest_job_runs() if r["workspace_id"] == ws}
        assert latest == dict.fromkeys(names, "skipped")

    def test_foundations_a_job_not_yet_named_is_refused(self, stack, ws):
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(
                f"INSERT INTO crm_job_runs (job, workspace_id) VALUES ('followup_drafts', {stack.q(ws)})"
            )
        assert refused.value.code == "23514"

    def test_foundations_the_budget_notice_kind(self, stack, ws, make_profile):
        admin = make_profile(keep=True)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(ws)}, {stack.q(admin)}, 'admin')",
        )
        rows = [
            {
                "workspace_id": ws,
                "user_id": admin,
                "kind": "ai_budget_reached",
                "title": "AI budget reached in AI test",
                "dedupe_key": f"ai_budget:{ws}:2026-10",
            }
        ]
        added = stack.sql(f"SELECT crm_notify({_j(stack, rows)}) AS r")[0]["r"]
        assert [a["kind"] for a in added] == ["ai_budget_reached"]
        assert stack.sql(f"SELECT crm_notify({_j(stack, rows)}) AS r")[0]["r"] == []
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(
                f"SELECT crm_notify({_j(stack, [{**rows[0], 'kind': 'bogus', 'dedupe_key': 'x'}])})"
            )
        assert refused.value.code == "23514"
