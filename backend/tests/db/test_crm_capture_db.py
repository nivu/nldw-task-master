"""Email capture's tables in the real database — spec 010 FR-CAP-SRC-01,
-05, -06, -07, FR-CAP-DIS-06, FR-CAP-REV-01, NFR-CAP-02, -05; data-model
§3.1 to §3.3, §5, §7; migration 071; plan PD-15.

What only Postgres can prove: every mailbox starts on *Labels only* from
1 April 2026; the capture source is held to its two values;
`crm_capture_candidates` keeps ids and times only, a decision whole or not
at all (none of it on an undecided row), and a finding only for a lead
or a deal signal, never with no outcome; `crm_capture_mark`
inserts one row per thread, advances it only forward (re-opening the
decision), ignores messages older than a year and refuses another
workspace's mailbox; a mailbox's candidates go with it; and
`crm_review_upsert` accepts a `deal_signal`. The audit skip of the new
cursor columns is in `test_crm_email_db.py::TestMailboxAudit`; the tables'
place in the security sweep in `test_crm_security_db.py`.

Each test works in a workspace of its own.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from supabase import create_client

from app.domain.crm import capture
from app.services.crm import CrmRefused, store

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


def _actor(ws: str, owner: str) -> dict:
    return {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}


def _workspace(stack, owner) -> str:
    key = f"cp{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Capture ' + key)}) RETURNING id",
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
    return _workspace(stack, owner)


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _mailbox(stack, ws, owner, **values) -> dict:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_mailboxes",
                "values": {
                    "email": f"t{uuid.uuid4().hex[:12]}@nunnarilabs.com",
                    "owner_id": owner,
                    "labels": ["Sales"],
                    **values,
                },
            }
        ],
    )[0]["row"]


def _candidates(stack, mailbox_id: str) -> list[dict]:
    return stack.sql(
        "SELECT * FROM crm_capture_candidates "
        f"WHERE mailbox_id = {stack.q(mailbox_id)} ORDER BY thread_id"
    )


def _at(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def _row(thread: str, message: str, delta: timedelta = timedelta(0)) -> dict:
    return {"thread_id": thread, "message_id": message, "sent_at": _at(delta)}


def _insert(stack, ws: str, mailbox: str, **values) -> None:
    columns = {
        "workspace_id": ws,
        "mailbox_id": mailbox,
        "thread_id": f"t{uuid.uuid4().hex[:8]}",
        "latest_message_id": "m1",
        "latest_message_at": _at(),
        **values,
    }
    names = ", ".join(columns)
    given = ", ".join(
        _j(stack, v) if isinstance(v, dict) else ("NULL" if v is None else stack.q(str(v)))
        for v in columns.values()
    )
    stack.sql(f"INSERT INTO crm_capture_candidates ({names}) VALUES ({given})")


def _refused(stack, statement: str, code: str = "23514") -> None:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(statement)
    assert raised.value.code == code, raised.value.message


class TestMailboxColumns:
    """FR-CAP-SRC-01, -07; data-model §3.1, §7; NFR-CAP-05."""

    def test_capture_a_mailbox_starts_on_labels_only_from_april(self, stack, ws, owner):
        box = _mailbox(stack, ws, owner)
        assert box["capture_source"] == "labels"
        assert box["capture_start_date"] == "2026-04-01"
        assert box["docs_checked_at"] is None and box["reconcile_after"] is None

    def test_capture_every_existing_mailbox_is_on_labels_only(self, stack):
        """After a fresh reset nothing captures (tasks "Gates")."""
        rows = stack.sql(
            "SELECT count(*) AS n FROM crm_mailboxes WHERE capture_source <> 'labels' "
            "AND email NOT LIKE 't%@nunnarilabs.com'"
        )
        assert rows[0]["n"] == 0

    def test_capture_source_is_one_of_two(self, stack, ws, owner):
        with pytest.raises(stack.SqlError) as raised:
            _mailbox(stack, ws, owner, capture_source="everything")
        assert raised.value.code == "23514"

    def test_capture_primary_sent_is_kept(self, stack, ws, owner, real):
        box = _mailbox(
            stack, ws, owner, capture_source="primary_sent", capture_start_date="2026-05-01"
        )
        row = real.get_mailbox(ws, box["id"])
        assert row["capture_source"] == "primary_sent"
        assert row["capture_start_date"] == "2026-05-01"


class TestTheCandidates:
    """FR-CAP-SRC-05, DIS-06; NFR-CAP-02; data-model §3.2."""

    def test_capture_no_text_or_address_column(self, stack):
        columns = {
            r["column_name"]
            for r in stack.sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'crm_capture_candidates'"
            )
        }
        assert not columns & {"body", "snippet", "subject", "text", "email", "from_email"}
        assert {"thread_id", "latest_message_id", "latest_message_at", "finding"} <= columns

    def test_capture_a_decision_is_whole_or_absent(self, stack, ws, owner):
        box = _mailbox(stack, ws, owner)
        _refused(
            stack,
            "INSERT INTO crm_capture_candidates (workspace_id, mailbox_id, thread_id, "
            "latest_message_id, latest_message_at, status) VALUES "
            f"({stack.q(ws)}, {stack.q(box['id'])}, 't1', 'm1', now(), 'judged')",
        )
        _refused(
            stack,
            "INSERT INTO crm_capture_candidates (workspace_id, mailbox_id, thread_id, "
            "latest_message_id, latest_message_at, outcome, confidence, judged_at, "
            "judged_message_id) VALUES "
            f"({stack.q(ws)}, {stack.q(box['id'])}, 't1', 'm1', now(), 'new_lead', 'high', "
            "now(), 'm1')",
        )
        _insert(
            stack,
            ws,
            box["id"],
            status="judged",
            outcome="not_relevant",
            confidence="high",
            judged_at=_at(),
            judged_message_id="m1",
        )

    def test_capture_a_finding_only_for_a_lead_or_a_deal_signal(self, stack, ws, owner):
        box = _mailbox(stack, ws, owner)
        judged = {
            "status": "judged",
            "confidence": "high",
            "judged_at": _at(),
            "judged_message_id": "m1",
        }
        with pytest.raises(stack.SqlError) as raised:
            _insert(
                stack,
                ws,
                box["id"],
                outcome="other_business",
                finding={"company_name": "Vendor"},
                **judged,
            )
        assert raised.value.code == "23514"
        _insert(
            stack, ws, box["id"], outcome="new_lead", finding={"company_name": "Acme"}, **judged
        )
        with pytest.raises(stack.SqlError) as raised:
            _insert(
                stack,
                ws,
                box["id"],
                outcome="new_lead",
                finding={"reason": "x" * 5000},
                **judged,
            )
        assert raised.value.code == "23514"

    @pytest.mark.parametrize("status", ["pending", "failed", "skipped"])
    def test_capture_no_finding_without_an_outcome(self, stack, ws, owner, status):
        """NFR-CAP-02: a NULL outcome must not let a finding through."""
        box = _mailbox(stack, ws, owner)
        with pytest.raises(stack.SqlError) as raised:
            _insert(stack, ws, box["id"], status=status, finding={"person_email": "priya@acme.com"})
        assert raised.value.code == "23514"

    @pytest.mark.parametrize(
        "partial",
        [
            {"outcome": "not_relevant"},
            {"confidence": "low"},
            {"judged_message_id": "m1"},
        ],
    )
    def test_capture_no_partial_decision_on_an_undecided_row(self, stack, ws, owner, partial):
        box = _mailbox(stack, ws, owner)
        with pytest.raises(stack.SqlError) as raised:
            _insert(stack, ws, box["id"], status="failed", **partial)
        assert raised.value.code == "23514"

    @pytest.mark.parametrize(
        "values",
        [
            {"status": "maybe"},
            {"attempts": 4},
            {"thread_id": ""},
            {"latest_message_id": "m" * 201},
            {"outcome": "spam"},
            {"confidence": "certain"},
        ],
    )
    def test_capture_the_value_checks(self, stack, ws, owner, values):
        box = _mailbox(stack, ws, owner)
        with pytest.raises(stack.SqlError) as raised:
            _insert(stack, ws, box["id"], **values)
        assert raised.value.code == "23514"

    def test_capture_one_row_per_mailbox_and_thread(self, stack, ws, owner):
        box = _mailbox(stack, ws, owner)
        _insert(stack, ws, box["id"], thread_id="t-same")
        with pytest.raises(stack.SqlError) as raised:
            _insert(stack, ws, box["id"], thread_id="t-same")
        assert raised.value.code == "23505"

    def test_capture_a_mailbox_of_another_workspace_is_refused(self, stack, ws, owner):
        other = _workspace(stack, owner)
        box = _mailbox(stack, other, owner)
        with pytest.raises(stack.SqlError) as raised:
            _insert(stack, ws, box["id"])
        assert raised.value.code == "23503"

    def test_capture_removing_the_mailbox_removes_its_candidates(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        _apply(
            stack, _actor(ws, owner), [{"op": "delete", "table": "crm_mailboxes", "id": box["id"]}]
        )
        assert _candidates(stack, box["id"]) == []


class TestMark:
    """071 crm_capture_mark — FR-CAP-SRC-05, DIS-06; PD-15."""

    def test_capture_mark_inserts_one_row_per_thread(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        n = real.mark_candidates(
            ws,
            box["id"],
            [
                _row("t1", "m1", -timedelta(hours=2)),
                _row("t1", "m2", -timedelta(hours=1)),
                _row("t2", "m3"),
            ],
        )
        assert n == 2
        rows = _candidates(stack, box["id"])
        assert [(r["thread_id"], r["latest_message_id"]) for r in rows] == [
            ("t1", "m2"),
            ("t2", "m3"),
        ]
        assert all(r["status"] == "pending" and r["attempts"] == 0 for r in rows)
        assert all(r["finding"] is None and r["dry_run"] is False for r in rows)

    def test_capture_mark_advances_only_forward_and_reopens(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        real.mark_candidates(ws, box["id"], [_row("t1", "m1", -timedelta(hours=2))])
        stack.sql(
            "UPDATE crm_capture_candidates SET status = 'judged', outcome = 'not_relevant', "
            "confidence = 'high', judged_at = now(), judged_message_id = 'm1', "
            f"classifier_version = '010.1', attempts = 2 WHERE mailbox_id = {stack.q(box['id'])}"
        )
        # An older or the same message changes nothing.
        assert real.mark_candidates(ws, box["id"], [_row("t1", "m0", -timedelta(days=2))]) == 0
        [row] = _candidates(stack, box["id"])
        assert row["status"] == "judged" and row["latest_message_id"] == "m1"
        # A later one re-opens it, from the start.
        assert real.mark_candidates(ws, box["id"], [_row("t1", "m2")]) == 1
        [row] = _candidates(stack, box["id"])
        assert row["latest_message_id"] == "m2" and row["status"] == "pending"
        assert row["outcome"] is None and row["confidence"] is None
        assert row["judged_message_id"] is None and row["classifier_version"] is None
        assert row["attempts"] == 0

    def test_capture_mark_skips_messages_older_than_a_year(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        assert real.mark_candidates(ws, box["id"], [_row("t1", "m1", -timedelta(days=366))]) == 0
        assert _candidates(stack, box["id"]) == []

    def test_capture_mark_refuses_another_workspaces_mailbox(self, stack, ws, owner, real):
        other = _workspace(stack, owner)
        theirs = _mailbox(stack, other, owner)
        with pytest.raises(CrmRefused) as refused:
            real.mark_candidates(ws, theirs["id"], [_row("t1", "m1")])
        assert refused.value.status == 404
        assert _candidates(stack, theirs["id"]) == []

    def test_capture_mark_writes_no_audit_row(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        [row] = _candidates(stack, box["id"])
        assert (
            stack.sql(
                f"SELECT count(*) AS n FROM audit_log WHERE target_id = {stack.q(row['id'])}"
            )[0]["n"]
            == 0
        )


class TestDealSignalItems:
    """FR-CAP-REV-01, CHG-04 — 071's type CHECK."""

    def test_capture_a_deal_signal_is_stored(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        company = _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Acme", "owner_id": owner, "lead_stage": "deal_open"},
                }
            ],
        )[0]["id"]
        item = capture.deal_signal_item(
            mailbox_id=box["id"],
            thread_id="t-sow",
            message_id="m-sow",
            company_id=company,
            deals=[],
            stage="proposal",
            kinds=["sow"],
            direction="outbound",
            message_at=datetime.now(UTC),
            owner_id=owner,
            generated_at=_at(),
        )
        sync = {"user_id": None, "kind": "sync", "channel": None, "workspace_id": ws}
        [made] = real.review_upsert(sync, [item])
        assert made["created"] is True
        [row] = stack.sql(
            f"SELECT type, record_type, model FROM crm_review_items WHERE id = {stack.q(made['id'])}"
        )
        assert row == {"type": "deal_signal", "record_type": "company", "model": None}

    def test_capture_another_type_is_still_refused(self, stack):
        [row] = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'crm_review_items_type_check'"
        )
        assert "'deal_signal'" in row["d"] and "'new_lead'" not in row["d"]
