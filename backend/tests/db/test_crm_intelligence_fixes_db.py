"""The whole-branch review's database fix in the real database — spec 009
FR-CRMAI-REV-06, -08; data-model §3.2.3 (amended in S5); migration 068.

An approval claims its item, makes the change and releases it in three
transactions. When a job stores a newer suggestion of the same dedupe key
in between and the change is then refused, the release does not break the
open-once index: the claimed item expires, keeping its refusal, with a
`review.expired` audit row, and the newer item stays open. Without such an
item the refused one reopens as before (`test_crm_review_db.py`).

Each test works in a workspace of its own.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str | None, kind: str = "user") -> dict:
    return {
        "user_id": user,
        "kind": kind,
        "channel": "web" if kind == "user" else None,
        "workspace_id": workspace,
    }


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    key = f"fx{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Fixes ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


def _company(stack, ws: str, owner: str) -> str:
    ops = [
        {
            "op": "insert",
            "table": "crm_companies",
            "values": {"name": "Acme", "owner_id": owner, "lead_stage": "deal_open"},
        }
    ]
    made = stack.sql(f"SELECT crm_apply({_j(stack, _actor(ws, owner))}, {_j(stack, ops)}) AS r")
    return made[0]["r"][0]["id"]


def _item(company: str, version: str, city: str) -> dict:
    return {
        "type": "enrichment_conflict",
        "owner_id": None,
        "record_type": "company",
        "record_id": company,
        "suggestion": {"field": "city", "value": city},
        "current_values": {"city": None},
        "source_kind": "enrichment",
        "source_key": f"enrichment:{company}",
        "source_version": version,
        "source_link": {},
        "model": "claude-sonnet-5-5",
        "generated_at": "2026-10-18T00:00:00+00:00",
        "deal_choices": [],
        "dedupe_key": f"enrichment|enrichment:{company}|enrichment_conflict|company:{company}",
    }


def _upsert(stack, ws: str, item: dict) -> dict:
    actor = _j(stack, _actor(ws, None, "sync"))
    return stack.sql(f"SELECT crm_review_upsert({actor}, {_j(stack, [item])}) AS r")[0]["r"][0]


def _row(stack, item_id: str) -> dict:
    return stack.sql(f"SELECT * FROM crm_review_items WHERE id = {stack.q(item_id)}")[0]


def _audit(stack, item_id: str) -> list[dict]:
    return stack.sql(
        "SELECT action, actor_id, before, after FROM audit_log "
        f"WHERE target_table = 'crm_review_items' AND target_id = {stack.q(item_id)} ORDER BY id"
    )


class TestARefusalAfterANewerItem:
    """FR-CRMAI-REV-06, -08 (amended in S5)."""

    def test_review_db_a_refusal_with_a_newer_open_item_expires_the_claimed_one(
        self, stack, ws, owner
    ):
        company = _company(stack, ws, owner)
        claimed = _upsert(stack, ws, _item(company, "v1", "Pune"))
        who = _j(stack, _actor(ws, owner))
        stack.sql(
            f"SELECT crm_review_claim({who}, {stack.q(claimed['id'])}::uuid, 'approved') AS r"
        )
        # A new message meanwhile: a second open item of the same key.
        newer = _upsert(stack, ws, _item(company, "v2", "Chennai"))
        assert newer["created"] is True and newer["id"] != claimed["id"]

        stack.sql(
            f"SELECT crm_review_release({who}, {stack.q(claimed['id'])}::uuid, "
            "'The company changed meanwhile.', NULL)"
        )
        row = _row(stack, claimed["id"])
        assert (row["status"], row["refusal"]) == ("expired", "The company changed meanwhile.")
        assert row["decided_at"] is not None
        assert _row(stack, newer["id"])["status"] == "open"
        assert [(a["action"], a["before"], a["after"]) for a in _audit(stack, claimed["id"])] == [
            ("review.approved", {"status": "open"}, {"status": "approved"}),
            ("review.expired", {"status": "approved"}, {"status": "expired"}),
        ]
        assert _audit(stack, claimed["id"])[-1]["actor_id"] == owner
        assert stack.sql(
            "SELECT count(*) AS n FROM crm_review_items "
            f"WHERE workspace_id = {stack.q(ws)} AND status = 'open'"
        ) == [{"n": 1}]

    def test_review_db_without_a_newer_item_the_refused_one_reopens(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        claimed = _upsert(stack, ws, _item(company, "v1", "Pune"))
        who = _j(stack, _actor(ws, owner))
        stack.sql(
            f"SELECT crm_review_claim({who}, {stack.q(claimed['id'])}::uuid, 'approved') AS r"
        )
        stack.sql(
            f"SELECT crm_review_release({who}, {stack.q(claimed['id'])}::uuid, 'Refused.', NULL)"
        )
        row = _row(stack, claimed["id"])
        assert (row["status"], row["refusal"]) == ("open", "Refused.")
        assert [a["action"] for a in _audit(stack, claimed["id"])] == [
            "review.approved",
            "review.reopened",
        ]
