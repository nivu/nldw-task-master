"""The Review Queue in the real database — spec 009 FR-CRMAI-REV-04, -05,
-07, -08, FR-CRMAI-CHG-09, -11 (items); spec §15 "An approved change's
audit entry names the approver and carries the item id"; data-model §3.2,
§3.12, §4, §5; migration 061.

What only Postgres can prove: one open item per dedupe key, whoever writes;
`crm_review_upsert` refreshes an open item, does not make a decided one
again from the same source, and holds the target and every deal choice to
the workspace; of two claims only one wins; `crm_audit_row` writes the
actor's `suggestion_id`; expiry and tombstones; `crm_purge` takes a purged
record's items and its place among deal choices with it; and
`crm_merge_companies` moves the merged company's open items, recomputing
their keys. The two tables' place in `NOT_AUDITED` (and RLS, grants and
EXECUTE) is checked by `test_crm_security_db.py`; a copy of a replaced
function made from a pre-048 body is caught by 008's `TestAfterMigration`.

Each test works in a workspace of its own.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
OPEN_ONCE = "crm_review_items_open_once"


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


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _new_workspace(stack, owner: str) -> str:
    key = f"rv{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Review ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    return _new_workspace(stack, owner)


@pytest.fixture
def other(stack, owner) -> str:
    return _new_workspace(stack, owner)


def _company(stack, ws: str, owner: str, name: str = "Acme") -> str:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_companies",
                "values": {"name": name, "owner_id": owner, "lead_stage": "deal_open"},
            }
        ],
    )[0]["id"]


def _deal(stack, ws: str, owner: str, company: str, name: str = "Pilot") -> str:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_opportunities",
                "values": {
                    "name": name,
                    "company_id": company,
                    "owner_id": owner,
                    "probability": 10,
                    "next_step": "Call",
                    "next_step_on": "2026-10-21",
                },
            }
        ],
    )[0]["id"]


def _item(record_type: str, record_id: str, **values) -> dict:
    item = {
        "type": "enrichment_conflict",
        "owner_id": None,
        "record_type": record_type,
        "record_id": record_id,
        "suggestion": {"field": "city", "value": "Pune"},
        "current_values": {"city": None},
        "source_kind": "enrichment",
        "source_key": f"enrichment:{record_id}",
        "source_version": "v1",
        "source_link": {},
        "model": "claude-sonnet-5-5",
        "generated_at": "2026-10-18T00:00:00+00:00",
        "deal_choices": [],
        **values,
    }
    item.setdefault(
        "dedupe_key",
        f"{item['source_kind']}|{item['source_key']}|{item['type']}|{record_type}:{record_id}",
    )
    return item


def _upsert_sql(stack, ws: str, items: list[dict]) -> str:
    return (
        f"SELECT crm_review_upsert({_j(stack, _actor(ws, None, 'sync'))}, {_j(stack, items)}) AS r"
    )


def _upsert(stack, ws: str, items: list[dict]) -> list[dict]:
    return stack.sql(_upsert_sql(stack, ws, items))[0]["r"]


def _claim_sql(stack, ws: str, owner: str, item_id: str, status: str = "approved") -> str:
    return (
        f"SELECT crm_review_claim({_j(stack, _actor(ws, owner))}, {stack.q(item_id)}::uuid, "
        f"{stack.q(status)}) AS r"
    )


def _row(stack, item_id: str) -> dict:
    return stack.sql(f"SELECT * FROM crm_review_items WHERE id = {stack.q(item_id)}")[0]


def _audit(stack, item_id: str) -> list[dict]:
    return stack.sql(
        "SELECT action, actor_id, actor_label, before, after, suggestion_id FROM audit_log "
        f"WHERE target_table = 'crm_review_items' AND target_id = {stack.q(item_id)} ORDER BY id"
    )


class TestTheQueue:
    """FR-CRMAI-REV-08, data-model §3.2.2."""

    def test_review_db_one_open_item_per_key(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        insert = (
            "INSERT INTO crm_review_items (workspace_id, type, record_type, record_id, "
            "source_kind, source_key, source_version, dedupe_key) VALUES "
            f"({stack.q(ws)}, 'enrichment_conflict', 'company', {stack.q(company)}, "
            "'enrichment', 'enrichment:x', 'v1', 'k1') RETURNING id"
        )
        first = stack.sql(insert)[0]["id"]
        message = _refused(stack, insert, "23505")
        assert OPEN_ONCE in message
        stack.sql(
            "UPDATE crm_review_items SET status = 'dismissed', decided_at = now() "
            f"WHERE id = {stack.q(first)}"
        )
        assert stack.sql(insert)[0]["id"] != first

    def test_review_db_an_open_item_is_refreshed_not_doubled(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        assert made["created"] is True
        [again] = _upsert(
            stack,
            ws,
            [
                _item(
                    "company",
                    company,
                    source_version="v2",
                    suggestion={"field": "city", "value": "Chennai"},
                )
            ],
        )
        assert again == {"id": made["id"], "created": False}
        row = _row(stack, made["id"])
        assert (row["suggestion"]["value"], row["source_version"]) == ("Chennai", "v2")

    def test_review_db_a_decided_key_is_not_made_again_from_the_same_source(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        stack.sql(_claim_sql(stack, ws, owner, made["id"], "dismissed"))
        assert _upsert(stack, ws, [_item("company", company)]) == [{"id": None, "created": False}]
        [changed] = _upsert(stack, ws, [_item("company", company, source_version="v2")])
        assert changed["created"] is True

    def test_review_db_a_foreign_target_is_refused(self, stack, ws, other, owner):
        foreign = _company(stack, other, owner)
        _refused(stack, _upsert_sql(stack, ws, [_item("company", foreign)]), "CRM04")
        assert stack.sql(
            f"SELECT count(*) AS n FROM crm_review_items WHERE workspace_id = {stack.q(ws)}"
        ) == [{"n": 0}]

    def test_review_db_a_deal_choice_must_be_an_open_deal_here(self, stack, ws, other, owner):
        company = _company(stack, ws, owner)
        foreign = _deal(stack, other, owner, _company(stack, other, owner))
        item = _item(
            "company", company, type="deal_link", source_kind="meeting", deal_choices=[foreign]
        )
        message = _refused(stack, _upsert_sql(stack, ws, [item]), "CRM04")
        assert message == "No such open deal in this workspace."
        mine = _deal(stack, ws, owner, company)
        [made] = _upsert(stack, ws, [{**item, "deal_choices": [mine]}])
        assert stack.sql(
            "SELECT opportunity_id FROM crm_review_item_deals "
            f"WHERE item_id = {stack.q(made['id'])}"
        ) == [{"opportunity_id": mine}]

    def test_review_db_only_the_jobs_actor_writes_items(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        _refused(
            stack,
            f"SELECT crm_review_upsert({_j(stack, _actor(ws, owner))}, "
            f"{_j(stack, [_item('company', company)])})",
            "CRM22",
        )


class TestClaim:
    """FR-CRMAI-REV-04, -05, data-model §3.2.3."""

    def test_review_db_of_two_claims_one_wins(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        won = stack.sql(_claim_sql(stack, ws, owner, made["id"]))[0]["r"]
        assert (won["status"], won["decided_by"]) == ("approved", owner)
        message = _refused(stack, _claim_sql(stack, ws, owner, made["id"], "dismissed"), "CRM09")
        assert message == "This suggestion was already decided."
        [row] = _audit(stack, made["id"])
        assert (row["action"], row["actor_id"], row["suggestion_id"]) == (
            "review.approved",
            owner,
            made["id"],
        )
        assert (row["before"], row["after"]) == ({"status": "open"}, {"status": "approved"})
        assert "Pune" not in json.dumps(row)

    def test_review_db_another_workspaces_item_is_not_found(self, stack, ws, other, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        _refused(stack, _claim_sql(stack, other, owner, made["id"]), "CRM04")
        assert _row(stack, made["id"])["status"] == "open"

    def test_review_db_a_refused_change_opens_the_item_again(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        stack.sql(_claim_sql(stack, ws, owner, made["id"]))
        stack.sql(
            f"SELECT crm_review_release({_j(stack, _actor(ws, owner))}, "
            f"{stack.q(made['id'])}::uuid, 'A next step needs a date.', NULL)"
        )
        row = _row(stack, made["id"])
        assert (row["status"], row["decided_at"], row["refusal"]) == (
            "open",
            None,
            "A next step needs a date.",
        )
        assert [a["action"] for a in _audit(stack, made["id"])] == [
            "review.approved",
            "review.reopened",
        ]

    def test_review_db_an_approval_records_what_it_made(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        stack.sql(_claim_sql(stack, ws, owner, made["id"]))
        stack.sql(
            f"SELECT crm_review_release({_j(stack, _actor(ws, owner))}, "
            f"{stack.q(made['id'])}::uuid, NULL, {_j(stack, {'field': 'city'})})"
        )
        assert _row(stack, made["id"])["result"] == {"field": "city"}

    def test_review_db_claim_takes_only_approved_or_dismissed(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        _refused(stack, _claim_sql(stack, ws, owner, made["id"], "expired"), "CRM22")
        assert _row(stack, made["id"])["status"] == "open"
        assert _audit(stack, made["id"]) == []

    def test_review_db_release_of_an_open_item_is_refused(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        who = _j(stack, _actor(ws, owner))
        item = f"{stack.q(made['id'])}::uuid"
        before = _row(stack, made["id"])
        _refused(stack, f"SELECT crm_review_release({who}, {item}, 'Refused.', NULL)", "CRM04")
        _refused(
            stack,
            f"SELECT crm_review_release({who}, {item}, NULL, {_j(stack, {'field': 'city'})})",
            "CRM04",
        )
        assert _row(stack, made["id"]) == before
        assert _audit(stack, made["id"]) == []


class TestTheAuditRow:
    """FR-CRMAI-REV-05, data-model §3.12: `crm_audit_row` writes the actor's
    `suggestion_id`."""

    def test_review_db_suggestion_id_lands_on_the_audit_row(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        deal = _deal(stack, ws, owner, company)
        suggestion = str(uuid.uuid4())
        approver = {**_actor(ws, owner), "suggestion_id": suggestion}
        _apply(
            stack,
            approver,
            [
                {
                    "op": "update",
                    "table": "crm_opportunities",
                    "id": deal,
                    "values": {"next_step": "Send SOW"},
                }
            ],
        )
        rows = stack.sql(
            "SELECT actor_id, suggestion_id, after FROM audit_log WHERE target_table = "
            f"'crm_opportunities' AND target_id = {stack.q(deal)} ORDER BY id"
        )
        assert [r["suggestion_id"] for r in rows] == [None, suggestion]
        assert (rows[-1]["actor_id"], rows[-1]["after"]) == (owner, {"next_step": "Send SOW"})


class TestExpiryAndTombstones:
    """FR-CRMAI-REV-07, Q-34."""

    def test_review_db_expire_and_tombstone(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        old, young = _upsert(
            stack,
            ws,
            [
                _item("company", company),
                _item("company", company, source_key="enrichment:other"),
            ],
        )
        stack.sql(
            "UPDATE crm_review_items SET created_at = now() - interval '15 days' "
            f"WHERE id = {stack.q(old['id'])}"
        )
        system = _j(stack, _actor(ws, None, "system"))
        expired = stack.sql(f"SELECT crm_review_expire({system}, now() - interval '14 days') AS n")[
            0
        ]["n"]
        assert expired == 1
        assert (_row(stack, old["id"])["status"], _row(stack, young["id"])["status"]) == (
            "expired",
            "open",
        )
        [row] = _audit(stack, old["id"])
        assert (row["action"], row["actor_label"], row["suggestion_id"]) == (
            "review.expired",
            "system",
            old["id"],
        )
        stack.sql(
            "UPDATE crm_review_items SET decided_at = now() - interval '91 days' "
            f"WHERE id = {stack.q(old['id'])}"
        )
        n = stack.sql(f"SELECT crm_review_tombstone({system}, now() - interval '90 days') AS n")
        assert n[0]["n"] == 1
        row = _row(stack, old["id"])
        assert (row["suggestion"], row["current_values"], row["source_link"]) == ({}, {}, {})
        assert row["tombstoned_at"] is not None
        assert (row["dedupe_key"], row["source_version"], row["status"]) == (
            _item("company", company)["dedupe_key"],
            "v1",
            "expired",
        )
        # The tombstone still stops the same source (FR-CRMAI-REV-08).
        assert _upsert(stack, ws, [_item("company", company)]) == [{"id": None, "created": False}]

    def test_review_db_only_the_jobs_actor_expires_and_tombstones(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        [made] = _upsert(stack, ws, [_item("company", company)])
        stack.sql(
            "UPDATE crm_review_items SET created_at = now() - interval '15 days' "
            f"WHERE id = {stack.q(made['id'])}"
        )
        before = _row(stack, made["id"])
        user = _j(stack, _actor(ws, owner))
        for fn in ("crm_review_expire", "crm_review_tombstone"):
            _refused(stack, f"SELECT {fn}({user}, now() - interval '14 days')", "CRM22")
        assert _row(stack, made["id"]) == before
        assert _audit(stack, made["id"]) == []


class TestTheRecordLifecycle:
    """FR-CRMAI-CHG-11, data-model §4."""

    def test_review_db_purge_takes_the_items_and_the_deal_choice(self, stack, ws, owner):
        company = _company(stack, ws, owner)
        deal = _deal(stack, ws, owner, company)
        on_deal, on_company = _upsert(
            stack,
            ws,
            [
                _item(
                    "opportunity",
                    deal,
                    type="next_step_change",
                    source_kind="email_thread",
                    source_key="gmail:m:t",
                    suggestion={"next_step": "SOW", "next_step_on": "2026-10-24"},
                    deal_choices=[deal],
                ),
                _item(
                    "company",
                    company,
                    type="deal_link",
                    source_kind="meeting",
                    source_key="fireflies:x",
                    deal_choices=[deal],
                ),
            ],
        )
        actor = _actor(ws, owner)
        _apply(stack, actor, [{"op": "soft_delete", "table": "crm_opportunities", "id": deal}])
        plan = {"items": [{"table": "crm_opportunities", "id": deal}]}
        stack.sql(f"SELECT crm_purge({_j(stack, actor)}, {_j(stack, plan)})")
        left = {
            r["id"]
            for r in stack.sql(
                f"SELECT id FROM crm_review_items WHERE workspace_id = {stack.q(ws)}"
            )
        }
        assert left == {on_company["id"]}
        assert stack.sql(
            "SELECT count(*) AS n FROM crm_review_item_deals "
            f"WHERE item_id IN ({stack.q(on_deal['id'])}, {stack.q(on_company['id'])})"
        ) == [{"n": 0}]

    def test_review_db_merge_moves_open_items_and_recomputes_their_keys(self, stack, ws, owner):
        keep = _company(stack, ws, owner, "Acme")
        gone = _company(stack, ws, owner, "Acme Corp")
        moved, clash, decided = _upsert(
            stack,
            ws,
            [
                _item("company", gone, source_key="enrichment:a"),
                _item("company", gone, source_key="enrichment:b"),
                _item("company", gone, source_key="enrichment:c"),
            ],
        )
        [survivors] = _upsert(stack, ws, [_item("company", keep, source_key="enrichment:b")])
        stack.sql(_claim_sql(stack, ws, owner, decided["id"], "dismissed"))
        rows = {
            r["id"]: r
            for r in stack.sql(
                f"SELECT id, updated_at FROM crm_companies WHERE id IN ({stack.q(keep)}, "
                f"{stack.q(gone)})"
            )
        }
        plan = {
            "survivor_id": keep,
            "merged_id": gone,
            "expect": {
                "survivor_updated_at": rows[keep]["updated_at"],
                "merged_updated_at": rows[gone]["updated_at"],
            },
            "survivor_changes": {},
        }
        stack.sql(f"SELECT crm_merge_companies({_j(stack, _actor(ws, owner))}, {_j(stack, plan)})")
        row = _row(stack, moved["id"])
        assert (row["record_id"], row["status"]) == (keep, "open")
        assert row["dedupe_key"] == f"enrichment|enrichment:a|enrichment_conflict|company:{keep}"
        clashed = _row(stack, clash["id"])
        assert (clashed["record_id"], clashed["status"]) == (gone, "expired")
        assert [a["action"] for a in _audit(stack, clash["id"])] == ["review.expired"]
        assert _row(stack, survivors["id"])["status"] == "open"
        assert _row(stack, decided["id"])["record_id"] == gone
