"""AI fields in the real database — spec 009 FR-CRMAI-FIELD-01, -05,
FR-CRMAI-CHG-11 (AI field values); Q-12, Q-13, Q-38; data-model §3.6, §4;
migration 065.

What only Postgres can prove: 065 seeds the four default fields in every
workspace, ICP fit in Labs only, in the same words as
`domain/crm/ai_fields.DEFAULT_FIELDS`, written as the system and audited; a
field an Admin creates through `crm_apply` is audited; a single select
holds 2 to 12 options and no other field holds any; a value is held to its
workspace by `crm_check_refs()` and to its column caps; the store wrappers
upsert a value, read it and mark it for recompute without moving an earlier
mark; `crm_purge` deletes a purged record's values; and
`crm_merge_companies` keeps the survivor's value of a field, or moves the
merged company's, marked for recompute. RLS, grants, EXECUTE and the
values table's place in `NOT_AUDITED` are checked by
`test_crm_security_db.py`.

The store wrappers of `services/crm/store.py` run against the local stack.
Each test works in a workspace of its own, except the seed tests, which
read Labs and Academy.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from supabase import create_client

from app.domain.crm import ai_fields as rules
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


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
    key = f"f{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('AI fields ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _field(stack, ws, owner, **values) -> str:
    body = {
        "key": f"k{uuid.uuid4().hex[:8]}",
        "name": "Renewal risk",
        "record_type": "company",
        "prompt": "How likely is a renewal?",
        "output_type": "text",
        **values,
    }
    ops = [{"op": "insert", "table": "crm_ai_fields", "values": body, "action": "ai_field.created"}]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _company(stack, ws, owner, name="Acme") -> str:
    ops = [{"op": "insert", "table": "crm_companies", "values": {"name": name, "owner_id": owner}}]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _value(field: str, company: str, **values) -> dict:
    return {
        "field_id": field,
        "record_type": "company",
        "record_id": company,
        "value_text": "A tools maker.",
        "value_number": None,
        "reason": None,
        "inputs_hash": "sha256:one",
        "model": "claude-sonnet-5-5",
        "generated_at": NOW.isoformat(),
        "dirty_at": None,
        "next_allowed_at": (NOW + timedelta(hours=1)).isoformat(),
        **values,
    }


def _values(stack, company: str) -> list[dict]:
    return stack.sql(
        "SELECT field_id, record_id, value_text, dirty_at FROM crm_ai_field_values "
        f"WHERE record_id = {stack.q(company)} ORDER BY field_id"
    )


class TestTheSeeds:
    """The FR-CRMAI-FIELD table; Q-12, Q-13; data-model §3.6, §7."""

    def _seeded(self, stack, key: str) -> list[dict]:
        return stack.sql(
            "SELECT f.key, f.name, f.record_type, f.prompt, f.output_type, f.options, "
            "f.max_lines, f.depends_on, f.with_reason, f.enabled, f.position::int AS position "
            "FROM crm_ai_fields f JOIN crm_workspaces w ON w.id = f.workspace_id "
            f"WHERE w.key = {stack.q(key)} ORDER BY f.position"
        )

    def test_ai_fields_db_icp_fit_is_seeded_in_labs_only(self, stack):
        assert [f["key"] for f in self._seeded(stack, "labs")] == [
            "icp_fit",
            "account_brief",
            "deal_outlook",
            "what_is_blocking",
        ]
        assert [f["key"] for f in self._seeded(stack, "academy")] == [
            "account_brief",
            "deal_outlook",
            "what_is_blocking",
        ]

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_ai_fields_db_the_seeds_are_the_domains_defaults(self, stack, key):
        seeded = self._seeded(stack, key)
        assert [{**f, "enabled": True} for f in rules.defaults_for(key)] == [
            {**f, "options": list(f["options"])} for f in seeded
        ]

    def test_ai_fields_db_the_seeds_were_written_as_the_system_and_audited(self, stack):
        rows = stack.sql(
            "SELECT a.actor_label, a.actor_id, a.action FROM audit_log a "
            "JOIN crm_ai_fields f ON f.id::text = a.target_id "
            "JOIN crm_workspaces w ON w.id = f.workspace_id "
            "WHERE w.key = 'labs' AND f.key = 'deal_outlook' AND a.target_table = 'crm_ai_fields'"
        )
        assert [(r["actor_label"], r["actor_id"], r["action"]) for r in rows] == [
            ("system", None, "ai_field.created")
        ]


class TestTheDefinitions:
    """FR-CRMAI-FIELD-01 — audited configuration, its options CHECK."""

    def test_ai_fields_db_an_admins_field_is_audited(self, stack, ws, owner):
        field = _field(stack, ws, owner, name="Fit score", output_type="number")
        rows = stack.sql(
            "SELECT actor_id, action, workspace_id FROM audit_log "
            f"WHERE target_table = 'crm_ai_fields' AND target_id = {stack.q(field)}"
        )
        assert [(r["actor_id"], r["action"], r["workspace_id"]) for r in rows] == [
            (owner, "ai_field.created", ws)
        ]
        [row] = stack.sql(
            "SELECT max_lines, depends_on, with_reason, enabled, options FROM crm_ai_fields "
            f"WHERE id = {stack.q(field)}"
        )
        assert row == {
            "max_lines": None,
            "depends_on": None,
            "with_reason": False,
            "enabled": True,
            "options": [],
        }

    @pytest.mark.parametrize(
        ("output_type", "options"),
        [
            ("single_select", []),
            ("single_select", ["Only"]),
            ("single_select", [f"o{i}" for i in range(13)]),
            ("text", ["Low", "High"]),
            ("number", ["Low", "High"]),
        ],
    )
    def test_ai_fields_db_the_options_check(self, stack, ws, owner, output_type, options):
        with pytest.raises(stack.SqlError) as refused:
            _field(stack, ws, owner, output_type=output_type, options=options)
        assert refused.value.code == "23514"

    def test_ai_fields_db_a_choice_with_its_options_is_kept(self, stack, ws, owner):
        field = _field(stack, ws, owner, output_type="single_select", options=["Low", "High"])
        assert stack.sql(f"SELECT options FROM crm_ai_fields WHERE id = {stack.q(field)}") == [
            {"options": ["Low", "High"]}
        ]

    @pytest.mark.parametrize("key", ["Bad key", "1st", "x", "a" * 42])
    def test_ai_fields_db_the_key_check(self, stack, ws, owner, key):
        with pytest.raises(stack.SqlError) as refused:
            _field(stack, ws, owner, key=key)
        assert refused.value.code == "23514"

    def test_ai_fields_db_a_key_is_used_once_per_workspace(self, stack, ws, owner):
        _field(stack, ws, owner, key="renewal_risk")
        with pytest.raises(stack.SqlError) as refused:
            _field(stack, ws, owner, key="renewal_risk")
        assert refused.value.code == "23505"


class TestTheValues:
    """Data-model §3.6 — a log held to its workspace and its caps."""

    def test_ai_fields_db_another_workspaces_record_is_refused(
        self, stack, ws, owner, make_profile
    ):
        field = _field(stack, ws, owner)
        other_owner = make_profile(keep=True)
        key = f"f{uuid.uuid4().hex[:12]}"
        other = _as_system(
            stack,
            f"INSERT INTO crm_workspaces (key, name) VALUES ({stack.q(key)}, {stack.q(key)}) "
            "RETURNING id",
        )[-1]["id"]
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(other)}, {stack.q(other_owner)}, 'admin')",
        )
        theirs = _company(stack, other, other_owner)
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(
                "INSERT INTO crm_ai_field_values (workspace_id, field_id, record_type, record_id, "
                "inputs_hash, next_allowed_at) VALUES "
                f"({stack.q(ws)}, {stack.q(field)}, 'company', {stack.q(theirs)}, 'h', now())"
            )
        assert refused.value.code == "CRM04"

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("value_number", "101"),
            ("value_text", "repeat('x', 601)"),
            ("reason", "repeat('x', 201)"),
        ],
    )
    def test_ai_fields_db_the_column_caps(self, stack, ws, owner, column, value):
        field = _field(stack, ws, owner)
        company = _company(stack, ws, owner)
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(
                "INSERT INTO crm_ai_field_values (workspace_id, field_id, record_type, record_id, "
                f"inputs_hash, next_allowed_at, {column}) VALUES ({stack.q(ws)}, "
                f"{stack.q(field)}, 'company', {stack.q(company)}, 'h', now(), {value})"
            )
        assert refused.value.code == "23514"

    def test_ai_fields_db_writing_no_audit_row_and_no_record_change(self, stack, ws, owner, real):
        field = _field(stack, ws, owner)
        company = _company(stack, ws, owner)
        before = stack.sql(f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(company)}")
        audit = stack.sql(f"SELECT count(*) AS n FROM audit_log WHERE workspace_id = {stack.q(ws)}")
        real.write_ai_field_value(ws, _value(field, company))
        assert (
            stack.sql(f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(company)}")
            == before
        )
        assert (
            stack.sql(f"SELECT count(*) AS n FROM audit_log WHERE workspace_id = {stack.q(ws)}")
            == audit
        )


class TestTheStore:
    """`services/crm/store.py`'s AI field wrappers."""

    def test_ai_fields_db_upsert_read_and_mark(self, stack, ws, owner, real):
        field = _field(stack, ws, owner)
        company = _company(stack, ws, owner)
        real.write_ai_field_value(ws, _value(field, company))
        real.write_ai_field_value(ws, _value(field, company, value_text="A lathe maker."))
        [row] = real.ai_field_values(ws, "company", [company])
        assert (row["value_text"], row["dirty_at"]) == ("A lathe maker.", None)
        assert [r["id"] for r in real.ai_field_values(ws)] == [row["id"]]
        assert real.ai_field_values(ws, "opportunity", [company]) == []
        real.mark_ai_fields_dirty(ws, "company", [company], NOW)
        real.mark_ai_fields_dirty(ws, "company", [company], NOW + timedelta(minutes=5))
        [row] = real.ai_field_values(ws, "company", [company])
        assert datetime.fromisoformat(row["dirty_at"]) == NOW
        assert [f["id"] for f in real.list_ai_fields(ws)] == [field]
        assert real.get_ai_field(ws, field)["key"].startswith("k")
        assert real.get_ai_field(ws, str(uuid.uuid4())) is None

    def test_ai_fields_db_marking_one_field_leaves_the_others(self, stack, ws, owner, real):
        one = _field(stack, ws, owner)
        two = _field(stack, ws, owner, name="Another")
        company = _company(stack, ws, owner)
        real.write_ai_field_value(ws, _value(one, company))
        real.write_ai_field_value(ws, _value(two, company))
        real.mark_ai_fields_dirty(ws, "company", [company], NOW, field_ids=[two])
        marked = {
            r["field_id"]: r["dirty_at"] for r in real.ai_field_values(ws, "company", [company])
        }
        assert marked[one] is None and marked[two] is not None


class TestLifecycle:
    """Data-model §4 — purge and company merge."""

    def _merge(self, stack, ws, owner, keep, gone) -> None:
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

    def test_ai_fields_db_purging_the_record_deletes_its_values(self, stack, ws, owner, real):
        field = _field(stack, ws, owner)
        company = _company(stack, ws, owner)
        kept = _company(stack, ws, owner, "Beta")
        real.write_ai_field_value(ws, _value(field, company))
        real.write_ai_field_value(ws, _value(field, kept))
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "soft_delete", "table": "crm_companies", "id": company}],
        )
        # In the Trash the value stays (a restore finds it).
        assert len(_values(stack, company)) == 1
        real.purge(_actor(ws, owner), [{"table": "crm_companies", "id": company}])
        assert _values(stack, company) == []
        assert len(_values(stack, kept)) == 1

    def test_ai_fields_db_merge_keeps_the_survivors_and_moves_the_rest(
        self, stack, ws, owner, real
    ):
        both = _field(stack, ws, owner, name="Both")
        only_gone = _field(stack, ws, owner, name="Only the merged one")
        keep = _company(stack, ws, owner, "Keep")
        gone = _company(stack, ws, owner, "Gone")
        real.write_ai_field_value(ws, _value(both, keep, value_text="Keep's."))
        real.write_ai_field_value(ws, _value(both, gone, value_text="Gone's."))
        real.write_ai_field_value(ws, _value(only_gone, gone, value_text="Moved."))
        self._merge(stack, ws, owner, keep, gone)
        assert _values(stack, gone) == []
        found = {r["field_id"]: r for r in _values(stack, keep)}
        assert found[both]["value_text"] == "Keep's." and found[both]["dirty_at"] is None
        assert found[only_gone]["value_text"] == "Moved."
        assert found[only_gone]["dirty_at"] is not None

    def test_ai_fields_db_a_field_deleted_takes_its_values(self, stack, ws, owner, real):
        field = _field(stack, ws, owner)
        company = _company(stack, ws, owner)
        real.write_ai_field_value(ws, _value(field, company))
        _as_system(stack, f"DELETE FROM crm_ai_fields WHERE id = {stack.q(field)}")
        assert _values(stack, company) == []
