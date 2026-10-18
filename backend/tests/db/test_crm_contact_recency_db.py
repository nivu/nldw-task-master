"""Contact recency in the real database — spec 009 FR-CRMAI-REL-01, -02;
data-model §3.10; migration 067; spec §15 "Contact recency filters company
and person views and recomputing it does not move the record's updated
time".

What only Postgres can prove: `crm_set_contact_recency` stores a level and
moves neither `updated_at` nor `updated_by` nor the audit, needs no actor,
and writes only its workspace's rows of the two tables; the column's CHECK
takes the three levels only; `crm_query` filters companies and people by
it and refuses to sort or group by it; `crm_contact_facts` reads counted
email and live meeting notes, a company's including its people's, through
the real store. RLS, grants and EXECUTE are checked by
`test_crm_security_db.py`.

Each test works in a workspace of its own.
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


def _actor(ws: str, owner: str) -> dict:
    return {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    key = f"c{uuid.uuid4().hex[:12]}"
    workspace = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Recency ' + key)}) RETURNING id",
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


def _company(stack, ws, owner, name="Acme") -> str:
    ops = [{"op": "insert", "table": "crm_companies", "values": {"name": name, "owner_id": owner}}]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _person(stack, ws, owner, company: str | None, first="Priya") -> str:
    values = {"first_name": first, "owner_id": owner, "company_id": company}
    ops = [{"op": "insert", "table": "crm_people", "values": values}]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _row(stack, table: str, record: str) -> dict:
    return stack.sql(
        f"SELECT contact_recency, updated_at, updated_by FROM {table} WHERE id = {stack.q(record)}"
    )[0]


def _audits(stack, record: str) -> int:
    return stack.sql(f"SELECT count(*) AS n FROM audit_log WHERE target_id = {stack.q(record)}")[0][
        "n"
    ]


def _set(stack, ws: str, table: str, rows: list[dict]) -> int:
    """`crm_set_contact_recency` with no actor set, as the job calls it."""
    return stack.sql(
        f"SELECT crm_set_contact_recency({stack.q(ws)}::uuid, {stack.q(table)}, "
        f"{_j(stack, rows)}) AS n"
    )[0]["n"]


class TestTheWriter:
    """FR-CRMAI-REL-02 — a recompute is not an edit."""

    @pytest.mark.parametrize("table", ["crm_companies", "crm_people"])
    def test_recency_db_moves_neither_updated_time_nor_by_nor_the_audit(
        self, stack, ws, owner, table
    ):
        record = (
            _company(stack, ws, owner)
            if table == "crm_companies"
            else _person(stack, ws, owner, None)
        )
        before = _row(stack, table, record)
        audits = _audits(stack, record)
        assert before["contact_recency"] is None
        assert _set(stack, ws, table, [{"id": record, "level": "fading"}]) == 1
        after = _row(stack, table, record)
        assert after["contact_recency"] == "fading"
        assert (after["updated_at"], after["updated_by"]) == (
            before["updated_at"],
            before["updated_by"],
        )
        assert _audits(stack, record) == audits

    def test_recency_db_an_unchanged_level_is_no_change(self, stack, ws, owner):
        acme = _company(stack, ws, owner)
        assert _set(stack, ws, "crm_companies", [{"id": acme, "level": "recent"}]) == 1
        assert _set(stack, ws, "crm_companies", [{"id": acme, "level": "recent"}]) == 0

    def test_recency_db_an_edit_still_moves_updated_time_and_is_audited(self, stack, ws, owner):
        acme = _company(stack, ws, owner)
        _set(stack, ws, "crm_companies", [{"id": acme, "level": "recent"}])
        before = _row(stack, "crm_companies", acme)
        audits = _audits(stack, acme)
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "update", "table": "crm_companies", "id": acme, "values": {"city": "Pune"}}],
        )
        assert _row(stack, "crm_companies", acme)["updated_at"] > before["updated_at"]
        assert _audits(stack, acme) == audits + 1

    def test_recency_db_only_its_workspaces_rows(self, stack, ws, owner):
        acme = _company(stack, ws, owner)
        other = _as_system(
            stack,
            "INSERT INTO crm_workspaces (key, name) "
            f"VALUES ({stack.q('o' + uuid.uuid4().hex[:12])}, 'Other') RETURNING id",
        )[-1]["id"]
        assert _set(stack, other, "crm_companies", [{"id": acme, "level": "recent"}]) == 0
        assert _row(stack, "crm_companies", acme)["contact_recency"] is None

    @pytest.mark.parametrize("table", ["crm_opportunities", "tasks", "audit_log"])
    def test_recency_db_no_other_table(self, stack, ws, table):
        with pytest.raises(Exception, match="no contact recency"):
            _set(stack, ws, table, [])

    def test_recency_db_the_check_takes_the_three_levels_only(self, stack, ws, owner):
        acme = _company(stack, ws, owner)
        for level in ("recent", "fading", "lapsed"):
            _set(stack, ws, "crm_companies", [{"id": acme, "level": level}])
        with pytest.raises(Exception, match="contact_recency_check"):
            _set(stack, ws, "crm_companies", [{"id": acme, "level": "warm"}])

    def test_recency_db_through_the_store(self, stack, ws, owner, real):
        acme = _company(stack, ws, owner)
        assert real.set_contact_recency(ws, "crm_companies", [{"id": acme, "level": "lapsed"}]) == 1
        assert _row(stack, "crm_companies", acme)["contact_recency"] == "lapsed"


class TestTheQuery:
    """FR-CRMAI-REL-02 — a filter on company and person views, not a sort
    or a grouping."""

    def _query(self, stack, ws: str, kind: str, spec: dict) -> dict:
        return stack.sql(
            f"SELECT crm_query({stack.q(ws)}::uuid, {stack.q(kind)}, {_j(stack, spec)}, false) AS r"
        )[0]["r"]

    @staticmethod
    def _filter(operator: str, value) -> dict:
        item = {"field": "contact_recency", "operator": operator}
        if value is not None:
            item["value"] = value
        return {"filters": {"op": "and", "items": [item]}}

    def test_recency_db_filters_companies_and_people(self, stack, ws, owner):
        acme = _company(stack, ws, owner)
        bolt = _company(stack, ws, owner, name="Bolt")
        never = _company(stack, ws, owner, name="Never")
        priya = _person(stack, ws, owner, acme)
        ravi = _person(stack, ws, owner, bolt, first="Ravi")
        _set(
            stack,
            ws,
            "crm_companies",
            [{"id": acme, "level": "recent"}, {"id": bolt, "level": "lapsed"}],
        )
        _set(stack, ws, "crm_people", [{"id": priya, "level": "fading"}])

        def ids(kind, operator, value=None):
            return {
                r["id"] for r in self._query(stack, ws, kind, self._filter(operator, value))["rows"]
            }

        assert ids("company", "any_of", ["recent"]) == {acme}
        assert ids("company", "is", "lapsed") == {bolt}
        assert ids("company", "none_of", ["recent", "lapsed"]) == {never}
        assert ids("person", "is", "fading") == {priya}
        assert ids("person", "is_empty") == {ravi}

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ({"sorts": [{"field": "contact_recency", "dir": "asc"}]}, "cannot be sorted"),
            ({"group_by": "contact_recency"}, "cannot group"),
        ],
    )
    def test_recency_db_not_a_sort_or_a_grouping(self, stack, ws, spec, message):
        with pytest.raises(Exception, match=message):
            self._query(stack, ws, "company", spec)

    def test_recency_db_a_deal_has_no_such_field(self, stack, ws):
        with pytest.raises(Exception, match="cannot be filtered"):
            self._query(stack, ws, "opportunity", self._filter("is", "recent"))


class TestTheFacts:
    """067 `crm_contact_facts` (FR-CRMAI-REL-01)."""

    def _mailbox(self, stack, ws, owner) -> str:
        values = {"email": f"s{uuid.uuid4().hex[:12]}@nunnarilabs.com", "owner_id": owner}
        ops = [{"op": "insert", "table": "crm_mailboxes", "values": {**values, "labels": ["S"]}}]
        return _apply(stack, _actor(ws, owner), ops)[0]["id"]

    def _message(self, gmail_id, sent_at, direction, links, **values) -> dict:
        return {
            "gmail_message_id": gmail_id,
            "thread_id": "t1",
            "subject": "Pilot",
            "sent_at": sent_at.isoformat(),
            "direction": direction,
            "from_email": "x@example.com",
            "participants": [],
            "is_internal": False,
            "is_auto": False,
            "links": links,
            **values,
        }

    def _meeting(self, stack, ws, owner, record_type, record, on: str, **values) -> None:
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_notes",
                    "ref": "n",
                    "values": {"body": "Met", "kind": "meeting", "occurred_on": on, **values},
                },
                {
                    "op": "insert",
                    "table": "crm_note_links",
                    "values": {
                        "note_id": "$ref:n",
                        "record_type": record_type,
                        "record_id": record,
                    },
                },
            ],
        )

    def test_recency_db_counted_email_and_meetings_a_company_with_its_people(
        self, stack, ws, owner, real
    ):
        now = datetime.now(UTC)
        acme = _company(stack, ws, owner)
        priya = _person(stack, ws, owner, acme)
        ravi = _person(stack, ws, owner, acme, first="Ravi")
        quiet = _company(stack, ws, owner, name="Quiet")
        box = self._mailbox(stack, ws, owner)
        to_acme = [{"record_type": "company", "record_id": acme}]
        to_priya = [{"record_type": "person", "record_id": priya}]
        to_quiet = [{"record_type": "company", "record_id": quiet}]
        real.store_emails(
            ws,
            box,
            [
                self._message("a1", now - timedelta(days=5), "inbound", to_acme),
                self._message("a2", now - timedelta(days=4), "outbound", to_priya),
                # Neither internal nor automatic mail counts.
                self._message("a3", now - timedelta(days=1), "inbound", to_priya, is_internal=True),
                self._message("a4", now - timedelta(days=1), "inbound", to_quiet, is_auto=True),
                # Older than the window.
                self._message("a5", now - timedelta(days=95), "inbound", to_quiet),
            ],
        )
        today = now.date()
        self._meeting(stack, ws, owner, "person", ravi, (today - timedelta(days=10)).isoformat())
        self._meeting(
            stack, ws, owner, "company", quiet, (today - timedelta(days=3)).isoformat(), kind="call"
        )
        facts = real.contact_facts(ws, now)
        assert set(facts["company"]) == {acme, quiet}
        assert set(facts["person"]) == {priya, ravi}
        a = facts["company"][acme]
        assert datetime.fromisoformat(a["in"]) == now - timedelta(days=5)
        assert a["out"] is not None and a["meeting"] == (today - timedelta(days=10)).isoformat()
        assert facts["person"][priya]["in"] is None and facts["person"][priya]["out"] is not None
        assert facts["person"][ravi] == {
            "in": None,
            "out": None,
            "meeting": (today - timedelta(days=10)).isoformat(),
        }
        assert facts["company"][quiet] == {"in": None, "out": None, "meeting": None}

    def test_recency_db_the_trash_does_not_count(self, stack, ws, owner, real):
        acme = _company(stack, ws, owner)
        priya = _person(stack, ws, owner, acme)
        on = datetime.now(UTC).date().isoformat()
        self._meeting(stack, ws, owner, "person", priya, on)
        _apply(
            stack, _actor(ws, owner), [{"op": "soft_delete", "table": "crm_people", "id": priya}]
        )
        facts = real.contact_facts(ws, datetime.now(UTC))
        assert set(facts["person"]) == set()
        assert facts["company"][acme]["meeting"] is None
