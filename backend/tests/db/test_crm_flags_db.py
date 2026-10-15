"""Flags, the bell, digests and the job log in the real database — spec 007
FR-CRM-FLAG-02, FR-CRM-NOTIF-01, -03, FR-CRM-JOB-01, -03, FR-CRM-WS-02;
data-model §3.18 to §3.21, §5.3; migrations 037 and 038.

What only Postgres can prove: a flags-only update writes no audit row, needs
no actor and leaves `updated_at` alone, in the workspace only; the digest's
sent marker is claimed once (`ON CONFLICT DO NOTHING RETURNING`), so a
second tick sends nothing; `crm_notify` adds only for members and once per
key, and removing a membership removes its notifications; the flag facts
and the latest-run lookup read what they should, through the real store.

Each test works in a workspace of its own, so leftover rows from earlier
runs never meet its assertions.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from supabase import create_client

from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    workspace = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Flags ' + key)}) RETURNING id",
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


def _actor(ws: str, owner: str) -> dict:
    return {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}


def _company(stack, ws, owner, name="Acme", **values) -> dict:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_companies",
                "values": {"name": name, "owner_id": owner, **values},
            }
        ],
    )[0]["row"]


def _audit(stack, target: str) -> int:
    return stack.sql(f"SELECT count(*) AS n FROM audit_log WHERE target_id = {stack.q(target)}")[0][
        "n"
    ]


class TestFlagsOnlyUpdate:
    def test_no_audit_row_and_no_new_updated_at(self, stack, ws, owner, real):
        company = _company(stack, ws, owner, lead_stage="nurturing")
        before = _audit(stack, company["id"])
        changed = real.set_flags(
            ws,
            "crm_companies",
            [{"id": company["id"], "flag_level": "red", "flags": [{"kind": "loose_end"}]}],
            datetime.now(UTC),
        )
        assert changed == 1
        [row] = stack.sql(
            "SELECT flag_level, flags, flags_computed_at, "
            f"updated_at = {stack.q(company['updated_at'])}::timestamptz AS same "
            f"FROM crm_companies WHERE id = {stack.q(company['id'])}"
        )
        assert row["flag_level"] == "red" and row["flags"] == [{"kind": "loose_end"}]
        assert row["flags_computed_at"] is not None
        assert row["same"] is True
        assert _audit(stack, company["id"]) == before

    def test_the_same_flags_again_change_nothing(self, stack, ws, owner, real):
        company = _company(stack, ws, owner)
        rows = [{"id": company["id"], "flag_level": "amber", "flags": []}]
        assert real.set_flags(ws, "crm_companies", rows, datetime.now(UTC)) == 1
        assert real.set_flags(ws, "crm_companies", rows, datetime.now(UTC)) == 0

    def test_an_edit_with_flags_still_moves_updated_at(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [done] = _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_companies",
                    "id": company["id"],
                    "values": {"city": "X"},
                }
            ],
        )
        assert done["row"]["updated_at"] != company["updated_at"]

    def test_another_workspace_is_not_touched(self, stack, ws, owner, real):
        company = _company(stack, ws, owner)
        other = str(uuid.uuid4())
        changed = real.set_flags(
            other,
            "crm_companies",
            [{"id": company["id"], "flag_level": "red", "flags": []}],
            datetime.now(UTC),
        )
        assert changed == 0
        [row] = stack.sql(
            f"SELECT flag_level FROM crm_companies WHERE id = {stack.q(company['id'])}"
        )
        assert row["flag_level"] == "none"

    def test_only_the_four_tables(self, stack, ws):
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(f"SELECT crm_set_flags({stack.q(ws)}, 'crm_notes', '[]'::jsonb, now())")
        assert refused.value.code == "CRM22"


class TestFlagFacts:
    def test_activity_and_open_tasks(self, stack, ws, owner, real):
        company = _company(stack, ws, owner, lead_stage="qualified")
        old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        done = _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_opportunities",
                    "ref": "d",
                    "values": {
                        "name": "Deal",
                        "company_id": company["id"],
                        "owner_id": owner,
                        "probability": 10,
                        "next_step": "Call",
                        "next_step_on": "2026-12-01",
                    },
                },
                {
                    "op": "insert",
                    "table": "crm_tasks",
                    "ref": "t",
                    "values": {"title": "Call", "assignee_id": owner},
                },
                {
                    "op": "insert",
                    "table": "crm_task_links",
                    "values": {
                        "task_id": "$ref:t",
                        "record_type": "company",
                        "record_id": company["id"],
                    },
                },
            ],
        )
        deal = done[0]["row"]
        facts = real.flag_facts(ws, [deal["id"]], [company["id"]])
        assert set(facts["activity"]) == {deal["id"]}
        assert facts["open_task_companies"] == [company["id"]]
        assert old < facts["activity"][deal["id"]]


class TestDigestClaim:
    def test_once(self, stack, owner, real):
        first = real.claim_digest(owner, "daily", "2026-10-14", None)
        second = real.claim_digest(owner, "daily", "2026-10-14", None)
        assert first and second is None

    def test_the_weekly_one_is_per_workspace(self, stack, ws, owner, real):
        assert real.claim_digest(owner, "weekly", "2026-10-12", ws)
        assert real.claim_digest(owner, "weekly", "2026-10-12", ws) is None

    def test_a_released_claim_can_be_claimed_again(self, stack, owner, real):
        first = real.claim_digest(owner, "daily", "2026-10-15", None)
        real.release_digest(first)
        again = real.claim_digest(owner, "daily", "2026-10-15", None)
        assert again and again != first


class TestNotify:
    def test_members_only_once_and_removed_with_the_membership(
        self, stack, ws, owner, make_profile, real
    ):
        stranger = make_profile(keep=True)
        member = make_profile(keep=True)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(ws)}, {stack.q(member)}, 'member')",
        )
        row = {"workspace_id": ws, "kind": "assigned", "title": "Hi", "dedupe_key": "k"}
        added = real.notify([{**row, "user_id": member}, {**row, "user_id": stranger}])
        assert [a["user_id"] for a in added] == [member]
        assert real.notify([{**row, "user_id": member}]) == []
        assert real.unread_count(member, [ws]) == 1
        assert [n["title"] for n in real.list_notifications(member, [ws])] == ["Hi"]
        assert real.mark_read(member, [ws], None, datetime.now(UTC)) == 1
        assert real.unread_count(member, [ws]) == 0

        [m] = stack.sql(
            f"SELECT id FROM crm_memberships WHERE workspace_id = {stack.q(ws)} "
            f"AND user_id = {stack.q(member)}"
        )
        stack.sql(
            f"SELECT crm_remove_membership({_j(stack, _actor(ws, owner))}, {stack.q(m['id'])})"
        )
        left = stack.sql(
            f"SELECT count(*) AS n FROM crm_notifications WHERE user_id = {stack.q(member)}"
        )
        assert left[0]["n"] == 0

    def test_a_long_id_list_is_batched(self, stack, ws, owner, real):
        ids = [str(uuid.uuid4()) for _ in range(450)]
        assert real.mark_read(owner, [ws], ids, datetime.now(UTC)) == 0


class TestJobLog:
    def test_latest_per_workspace(self, stack, ws, real):
        t0 = datetime.now(UTC) - timedelta(hours=2)
        first = real.start_job_run("flags", ws, t0)
        real.finish_job_run(first["id"], {"outcome": "ok", "finished_at": t0.isoformat()})
        second = real.start_job_run("flags", ws, t0 + timedelta(hours=1))
        real.finish_job_run(
            second["id"],
            {
                "outcome": "failed",
                "finished_at": (t0 + timedelta(hours=1)).isoformat(),
                "error": "x",
                "error_public": "Follow-up flags failed (RuntimeError).",
            },
        )
        latest = [r for r in real.latest_job_runs() if r["workspace_id"] == ws]
        assert [(r["job"], r["outcome"]) for r in latest] == [("flags", "failed")]
        assert "error" not in latest[0]
        assert datetime.fromisoformat(real.last_ok_job_run("flags", ws)) == t0
        assert real.last_ok_job_run("trash_purge", ws) is None
        streak = real.job_streak("flags", ws)
        assert [r["outcome"] for r in streak] == ["failed", "ok"]

    def test_only_the_digest_tick_has_no_workspace(self, stack):
        with pytest.raises(stack.SqlError):
            stack.sql("INSERT INTO crm_job_runs (job, workspace_id) VALUES ('flags', NULL)")


class TestFollowUpsOwed:
    """FR-CRM-VIEW-11 (1b) through the real `crm_query`: the seeded view's
    own config, amber or red, red first (038's flag rank)."""

    def test_red_first(self, stack, ws, owner, real):
        made = {
            name: _company(stack, ws, owner, name=name)
            for name in ("Calm", "Amber one", "Red one", "Amber two")
        }
        real.set_flags(
            ws,
            "crm_companies",
            [
                {"id": made["Amber one"]["id"], "flag_level": "amber", "flags": []},
                {"id": made["Red one"]["id"], "flag_level": "red", "flags": []},
                {"id": made["Amber two"]["id"], "flag_level": "amber", "flags": []},
            ],
            datetime.now(UTC),
        )
        [view] = stack.sql(
            "SELECT config FROM crm_saved_views v JOIN crm_workspaces w ON w.id = v.workspace_id "
            "WHERE w.key = 'labs' AND v.system_key = 'follow_ups_owed'"
        )
        spec = {**view["config"], "limit": 10}
        got = stack.sql(
            f"SELECT crm_query({stack.q(ws)}::uuid, 'company', {_j(stack, spec)}, false) AS r"
        )[0]["r"]
        assert [r["name"] for r in got["rows"]] == ["Red one", "Amber one", "Amber two"]
        # And the keyset cursor pages through the flag rank.
        page = stack.sql(
            f"SELECT crm_query({stack.q(ws)}::uuid, 'company', "
            f"{_j(stack, {**spec, 'limit': 1})}, false) AS r"
        )[0]["r"]
        after = stack.sql(
            f"SELECT crm_query({stack.q(ws)}::uuid, 'company', "
            f"{_j(stack, {**spec, 'limit': 1, 'after': page['next']})}, false) AS r"
        )[0]["r"]
        assert [r["name"] for r in after["rows"]] == ["Amber one"]
