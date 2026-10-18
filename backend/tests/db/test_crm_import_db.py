"""The importer's writes in the real database — spec 007 FR-CRM-MIG-04, -07,
FR-CRM-FX-03, FR-CRM-DUP-02; data-model §3.9, §3.17, §5.2; migration 036;
plan, risk "PostgREST rpc statement timeout on big imports".

What only Postgres can prove: an upsert on `twenty_id` through the real
`crm_apply` is idempotent, and a re-run of unchanged rows writes no audit
row; the real importer's writes of a whole export (deals inserted won,
lost and at Proposal, `$ref` links, the fix-up next step) pass the
database's CHECKs and triggers, and its second run writes nothing; tasks
and notes now match on `twenty_id` too (036); the FX CHECK
refuses an upsert that changes an amount without its INR value; a chunk of
500 rows fits well inside the statement timeout; `crm_similar_many` answers
each name as `crm_similar` would; `crm_record_import` writes the run and its
audit row together, in the actor's workspace only.

Each test works in a workspace of its own, so leftover rows from earlier
runs never meet its assertions.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from supabase import create_client

from app.domain.tasks.rules import default_bucket
from app.services import supabase as db
from app.services.crm import importer as service
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str, kind: str = "import") -> dict:
    return {"user_id": user, "kind": kind, "channel": "web", "workspace_id": workspace}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _audit_count(stack, workspace: str) -> int:
    return stack.sql(
        f"SELECT count(*) AS n FROM audit_log WHERE workspace_id = {stack.q(workspace)}"
    )[0]["n"]


def _new_workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Import ' + key)}) RETURNING id",
    )[-1]["id"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    workspace = _new_workspace(stack)
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(owner)}, 'admin')",
    )
    return workspace


def _company(owner: str, tid: str, name: str = "Acme", **values) -> dict:
    return {
        "op": "upsert",
        "table": "crm_companies",
        "ref": f"c-{tid}",
        "values": {"twenty_id": tid, "name": name, "owner_id": owner, **values},
    }


class TestUpsert:
    def test_a_second_upsert_of_the_same_row_changes_nothing(self, stack, ws, owner):
        first = _apply(stack, _actor(ws, owner), [_company(owner, "c1", lead_stage="deal_open")])
        before = _audit_count(stack, ws)
        again = _apply(stack, _actor(ws, owner), [_company(owner, "c1", lead_stage="deal_open")])
        assert again[0]["id"] == first[0]["id"]
        assert again[0]["row"]["updated_at"] == first[0]["row"]["updated_at"]
        assert _audit_count(stack, ws) == before

    def test_a_changed_upsert_updates_in_place_and_is_audited_as_the_import(self, stack, ws, owner):
        [made] = _apply(stack, _actor(ws, owner), [_company(owner, "c1")])
        [changed] = _apply(stack, _actor(ws, owner), [_company(owner, "c1", name="Acme Corp")])
        assert changed["id"] == made["id"] and changed["row"]["name"] == "Acme Corp"
        rows = stack.sql(
            "SELECT action, actor_label FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND target_id = {stack.q(made['id'])} "
            "ORDER BY id"
        )
        assert [(r["action"], r["actor_label"]) for r in rows] == [
            ("company.created", "import"),
            ("company.updated", "import"),
        ]

    def test_tasks_and_notes_match_on_twenty_id(self, stack, ws, owner):
        """036 — without it a re-run would add every task and note again."""
        ops = [
            _company(owner, "c1"),
            {
                "op": "upsert",
                "table": "tasks",
                "ref": "t",
                "values": {
                    "twenty_id": "t1",
                    "title": "Call",
                    "assignee_id": owner,
                    "bucket": "later",
                },
            },
            {
                "op": "insert",
                "table": "task_links",
                "values": {"task_id": "$ref:t", "record_type": "company", "record_id": "$ref:c-c1"},
            },
            {
                "op": "upsert",
                "table": "crm_notes",
                "values": {"twenty_id": "n1", "body": "Met them."},
            },
        ]
        first = _apply(stack, _actor(ws, owner), ops)
        before = _audit_count(stack, ws)
        again = _apply(stack, _actor(ws, owner), [ops[0], ops[1], ops[3]])
        assert [r["id"] for r in again] == [first[0]["id"], first[1]["id"], first[3]["id"]]
        assert _audit_count(stack, ws) == before

    def test_twenty_ids_are_unique_per_workspace_only(self, stack, ws, owner):
        other = _new_workspace(stack)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(other)}, {stack.q(owner)}, 'admin')",
        )
        a = _apply(stack, _actor(ws, owner), [_company(owner, "c1")])[0]
        b = _apply(stack, _actor(other, owner), [_company(owner, "c1", name="Acme College")])[0]
        assert a["id"] != b["id"] and b["row"]["workspace_id"] == other

    def test_an_amount_changed_without_its_inr_value_is_refused(self, stack, ws, owner):
        """Data-model §3.9 — the FX CHECK, whatever path writes the amount."""
        [company] = _apply(stack, _actor(ws, owner), [_company(owner, "c1")])
        deal = {
            "twenty_id": "o1",
            "name": "Acme pilot",
            "company_id": company["id"],
            "owner_id": owner,
            "probability": 100,
            "amount": "1000.00",
            "currency": "USD",
            "amount_in_inr": "83500.00",
            "fx_rate_used": "83.5",
            "fx_month": "2026-09-01",
        }
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_opportunities",
                    "values": {**deal, "stage": "closed_won"},
                }
            ],
        )
        with pytest.raises(stack.SqlError) as refused:
            _apply(
                stack,
                _actor(ws, owner),
                [
                    {
                        "op": "upsert",
                        "table": "crm_opportunities",
                        "values": {**deal, "amount": "2000"},
                    }
                ],
            )
        assert refused.value.code == "23514"
        assert "crm_opportunities_fx_inr" in refused.value.message

    def test_an_upsert_cannot_move_a_deals_stage(self, stack, ws, owner):
        with pytest.raises(stack.SqlError) as refused:
            _apply(
                stack,
                _actor(ws, owner),
                [
                    {
                        "op": "upsert",
                        "table": "crm_opportunities",
                        "values": {"twenty_id": "o1", "stage": "proposal"},
                    }
                ],
            )
        assert (refused.value.code, refused.value.message) == ("CRM22", "Use a stage move.")

    def test_a_chunk_of_500_fits_the_statement_timeout(self, stack, ws, owner):
        """Plan risk — the importer's chunk size, under PostgREST's 8 s."""
        ops = [_company(owner, f"bulk-{n}", name=f"Bulk company {n}") for n in range(500)]
        started = time.monotonic()
        stack.sql(
            "SET statement_timeout = '8s'; "
            f"SELECT crm_apply({_j(stack, _actor(ws, owner))}, {_j(stack, ops)}) AS r"
        )
        assert time.monotonic() - started < 8
        again = time.monotonic()
        before = _audit_count(stack, ws)
        stack.sql(
            "SET statement_timeout = '8s'; "
            f"SELECT crm_apply({_j(stack, _actor(ws, owner))}, {_j(stack, ops)}) AS r"
        )
        assert time.monotonic() - again < 8
        assert _audit_count(stack, ws) == before


class TestRealImporter:
    """FR-CRM-MIG-04, -07 — `services/crm/importer` end to end through the
    real `crm_apply`, not only hand-built ops."""

    LABS = (Path(__file__).parents[1] / "fixtures" / "twenty" / "labs.json").read_bytes()

    def _options(self, stack, ws: str) -> None:
        options = [
            ("opportunity.lost_reason", "price", "Price"),
            ("opportunity.lost_reason", "other", "Other"),
            ("source", "import", "Import"),
        ]
        values = ", ".join(
            f"({stack.q(ws)}, {stack.q(k)}, {stack.q(sk)}, {stack.q(label)}, {n})"
            for n, (k, sk, label) in enumerate(options, start=1)
        )
        _as_system(
            stack,
            "INSERT INTO crm_select_options (workspace_id, list_key, system_key, label, position) "
            f"VALUES {values}",
        )
        _as_system(
            stack,
            "INSERT INTO crm_fx_rates (workspace_id, currency, month, rate) "
            f"VALUES ({stack.q(ws)}, 'USD', '2026-09-01', 83.5)",
        )

    def _rows(self, stack, ws: str, table: str) -> dict[str, dict]:
        rows = stack.sql(f"SELECT * FROM {table} WHERE workspace_id = {stack.q(ws)}")
        return {r["twenty_id"]: r for r in rows}

    def test_a_whole_export_lands_and_a_second_run_writes_nothing(
        self, stack, ws, owner, monkeypatch
    ):
        local = create_client(stack.url, stack.service_key)
        monkeypatch.setattr(db, "supabase", local)
        monkeypatch.setattr(store, "supabase", local)
        monkeypatch.setattr(service, "today_in_company_tz", lambda: date(2026, 10, 15))
        self._options(stack, ws)
        ctx = SimpleNamespace(
            user=SimpleNamespace(id=owner),
            workspace={"id": ws, "name": "Import test"},
            workspace_id=ws,
            channel="web",
        )
        source = service.Source(self.LABS, "twenty_json")

        dry = service.dry_run(ctx, source)
        done = service.apply(ctx, dry["run_id"], source, fixup=True)
        assert done["fixups_used"] == 1
        assert {obj: c["create"] for obj, c in done["counts"].items()} == {
            "companies": 6,
            "people": 4,
            "opportunities": 4,
            "tasks": 2,
            "notes": 2,
        }

        companies = self._rows(stack, ws, "crm_companies")
        deals = self._rows(stack, ws, "crm_opportunities")
        tasks = self._rows(stack, ws, "tasks")
        assert set(companies) == {
            "c-acme",
            "c-globex",
            "c-initech",
            "c-umbrella",
            "c-hooli",
            "c-acme-pvt",
        }
        assert set(self._rows(stack, ws, "crm_people")) == {"p-asha", "p-ben", "p-chen", "p-dana"}
        assert set(self._rows(stack, ws, "crm_notes")) == {"n-kickoff", "n-initech"}
        assert {t: d["stage"] for t, d in deals.items()} == {
            "o-acme-pilot": "proposal",
            "o-initech-rollout": "closed_won",
            "o-umbrella-audit": "closed_lost",
            "o-acme-phase2": "discovery",
        }
        assert deals["o-acme-pilot"]["amount_in_inr"] is not None
        assert deals["o-acme-phase2"]["next_step"] and deals["o-acme-phase2"]["next_step_on"]
        assert deals["o-acme-pilot"]["company_id"] == companies["c-acme"]["id"]
        history = stack.sql(
            "SELECT opportunity_id FROM crm_stage_history WHERE opportunity_id IN ("
            + ", ".join(stack.q(d["id"]) for d in deals.values())
            + ")"
        )
        assert {h["opportunity_id"] for h in history} == {d["id"] for d in deals.values()}
        links = stack.sql(
            "SELECT record_type, record_id FROM task_links "
            f"WHERE task_id = {stack.q(tasks['t-call-asha']['id'])}"
        )
        assert {(lk["record_type"], lk["record_id"]) for lk in links} == {
            ("person", self._rows(stack, ws, "crm_people")["p-asha"]["id"]),
            ("opportunity", deals["o-acme-pilot"]["id"]),
        }
        assert tasks["t-globex-deck"]["status"] == "done"
        # 008 FR-TASK-CHG-09, FR-TASK-DATA-08: Twenty's To do and In progress
        # are Open, and an imported open task gets the default bucket.
        opened = [t for t in tasks.values() if t["status"] == "open"]
        assert opened
        for t in opened:
            assert t["bucket"] == default_bucket(t["due_on"], date(2026, 10, 15)), t["twenty_id"]
        # 008 FR-TASK-DATA-12: import-sourced, as 047 marks the Twenty tasks.
        assert {t["source"] for t in tasks.values()} == {"import"}

        before = stack.sql(
            f"SELECT max(id) AS n FROM audit_log WHERE workspace_id = {stack.q(ws)}"
        )[0]["n"]
        again = service.dry_run(ctx, source)
        assert all(c["create"] == c["update"] == 0 for c in again["counts"].values())
        rerun = service.apply(ctx, again["run_id"], source, fixup=True)
        assert all(c["create"] == c["update"] == 0 for c in rerun["counts"].values())
        actions = stack.sql(
            "SELECT action FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND id > {before} ORDER BY id"
        )
        assert [a["action"] for a in actions] == ["import.dry_run", "import.applied"]


class TestSimilarMany:
    def test_each_name_is_answered_with_its_position(self, stack, ws, owner):
        tag = uuid.uuid4().hex[:6]
        _apply(stack, _actor(ws, owner), [_company(owner, "c1", name=f"Hooli {tag} Inc")])
        rows = stack.sql(
            f"SELECT * FROM crm_similar_many({stack.q(ws)}, 'company', "
            f"ARRAY[{stack.q('Nothing alike at all')}, {stack.q(f'Hooli {tag}')}], 3)"
        )
        assert [(r["idx"], r["name"]) for r in rows] == [(2, f"Hooli {tag} Inc")]


class TestRecordImport:
    def _run(self, stack, ws, owner, run: dict) -> dict:
        return stack.sql(
            f"SELECT crm_record_import({_j(stack, _actor(ws, owner))}, {_j(stack, run)}) AS r"
        )[0]["r"]

    def test_the_run_and_its_audit_row_land_together(self, stack, ws, owner):
        counts = {"companies": {"in_file": 3, "create": 3}}
        run = self._run(
            stack,
            ws,
            owner,
            {
                "kind": "twenty",
                "file_sha256": "a" * 64,
                "report": {"counts": counts, "conflicts": [{"detail": "Acme is open"}]},
            },
        )
        assert run["status"] == "dry_run"
        applied = self._run(
            stack, ws, owner, {"id": run["id"], "status": "applied", "report": {"counts": counts}}
        )
        assert applied["status"] == "applied" and applied["applied_at"]
        rows = stack.sql(
            "SELECT action, actor_label, after FROM audit_log "
            f"WHERE target_table = 'crm_import_runs' AND target_id = {stack.q(run['id'])} "
            "ORDER BY id"
        )
        assert [(r["action"], r["actor_label"]) for r in rows] == [
            ("import.dry_run", "import"),
            ("import.applied", "import"),
        ]
        # Counts only: the rows of the report never reach the audit log.
        assert rows[0]["after"]["counts"] == counts
        assert "Acme" not in json.dumps(rows)

    def test_another_workspaces_run_is_not_found(self, stack, ws, owner):
        run = self._run(stack, ws, owner, {"kind": "twenty", "file_sha256": "b" * 64})
        other = _new_workspace(stack)
        with pytest.raises(stack.SqlError) as refused:
            self._run(stack, other, owner, {"id": run["id"], "status": "applied"})
        assert refused.value.code == "CRM04"

    def test_a_bad_hash_is_refused(self, stack, ws, owner):
        with pytest.raises(stack.SqlError) as refused:
            self._run(stack, ws, owner, {"kind": "twenty", "file_sha256": "not-a-hash"})
        assert refused.value.code == "23514"

    def test_the_browser_cannot_read_the_runs(self, stack, seller):
        r = stack.rest("crm_import_runs", bearer=stack.jwt_for(seller))
        assert r.status_code in (401, 403, 404)
