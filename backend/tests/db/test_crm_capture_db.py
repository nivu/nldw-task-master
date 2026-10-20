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

S2 (migration 072, tasks T216; FR-CAP-DIS-01, -06, -10, FR-CAP-RPT-01 to
-03; data-model §3.4, §3.5; plan PD-13): the `capture` setting is a key the
CHECK takes, seeded off in every workspace and by the trigger in a new
one; `crm_capture_record` writes only on the latest message of a pending
row; a dry run's start resets what a dry run judged, refuses a second open
run and sets the mode with an audit row; `crm_capture_set_mode` cancels an
open run (audited) so a new one can start, and refuses Live with no
applied run; a cancelled run cannot finish; the recall check's progress is
kept on the open run, each thread once, and a full recall (RECALL_CAP rows)
fits its CHECK; a finding in Tamil at its largest fits 071's; the report
counts, live while the run is open and as they stood once it finished or
was cancelled; and the job and usage CHECKs and `crm_job_latest` know
`capture`.

S3 (migration 073, tasks T318; FR-CAP-DIS-03, -11, FR-CAP-REV-03,
FR-CAP-RPT-04; data-model §3.6, §3.7): the ignore list holds lower-case
addresses and domains of the right shape, once each, audited as the
person, and forgets the item it came from when that item is purged;
`crm_capture_apply` applies only the latest run finished within 7 days
(counted from its end), not cancelled or applied, turns its decisions
live and sets the mode Live with an audit row; `crm_capture_purge` deletes
every decided row older than the cutoff, findings included, and keeps
pending and newer rows.

R1 (migration 074, tasks T418; FR-CAP-GRP-02, -05 to -08, FR-CAP-ACAD-03;
data-model §9; plan PD-24, PD-38, PD-39): the group upsert inserts, merges
and skips (both skip forms; an expiry by refusal or by merge is no
decision), takes the suggestion, current values and deals only from the
newest entry; an upsert after a claim makes a fresh item, a refusal merges
into it, a reopened item drops what another open item holds; a release
removes, recomputes and expires, never a deal document's entry; the
consolidation keeps the newest, audits each loser, leaves decided items and
changes nothing a second time; the company merge still re-keys the linked
and deal keys; and `capture_routing` is a key seeded on everywhere.

Each test works in a workspace of its own.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from supabase import create_client

from app.domain.crm import capture
from app.domain.crm import review as review_rules
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
        # 073 (S3) widened the list with `new_lead`; nothing else is taken.
        [row] = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'crm_review_items_type_check'"
        )
        assert "'deal_signal'" in row["d"] and "'new_lead'" in row["d"]
        assert "'lead_suggestion'" not in row["d"]


# ---------------------------------------------------------------------------
# S2 — migration 072 (tasks T216)
# ---------------------------------------------------------------------------


def _setting(stack, ws: str) -> dict | None:
    rows = stack.sql(
        "SELECT value FROM crm_workspace_settings "
        f"WHERE workspace_id = {stack.q(ws)} AND key = 'capture'"
    )
    return rows[0]["value"] if rows else None


def _audit(stack, ws: str) -> list[dict]:
    return stack.sql(
        "SELECT action, actor_label, after FROM audit_log "
        f"WHERE workspace_id = {stack.q(ws)} AND target_table = 'crm_workspace_settings' "
        "ORDER BY id"
    )


def _runs(stack, ws: str) -> list[dict]:
    return stack.sql(
        f"SELECT * FROM crm_capture_dry_runs WHERE workspace_id = {stack.q(ws)} ORDER BY started_at"
    )


class TestTheCaptureSetting:
    """Data-model §3.4; tasks "Gates": every workspace starts Off."""

    def test_capture_every_workspace_is_seeded_off(self, stack):
        rows = stack.sql(
            "SELECT w.id, s.value FROM crm_workspaces w LEFT JOIN crm_workspace_settings s "
            "ON s.workspace_id = w.id AND s.key = 'capture'"
        )
        assert rows and all(r["value"] is not None for r in rows)
        assert (
            stack.sql(
                "SELECT count(*) AS n FROM crm_workspace_settings s JOIN crm_workspaces w "
                "ON w.id = s.workspace_id WHERE s.key = 'capture' AND s.value <> '{\"mode\": \"off\"}' "
                "AND w.key NOT LIKE 'cp%'"
            )[0]["n"]
            == 0
        )

    def test_capture_a_new_workspace_is_seeded_by_the_trigger(self, stack, ws):
        assert _setting(stack, ws) == {"mode": "off"}
        [row] = [r for r in _audit(stack, ws) if (r["after"] or {}).get("key") == "capture"]
        assert (row["action"], row["actor_label"]) == ("setting.created", "system")

    def test_capture_the_key_check_takes_capture_only(self, stack):
        [row] = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'crm_workspace_settings_key_check'"
        )
        assert "'capture'" in row["d"] and "'ai'" in row["d"]


class TestRecord:
    """072 crm_capture_record — FR-CAP-DIS-06."""

    def _one(self, stack, ws, owner, real) -> tuple[dict, dict]:
        box = _mailbox(stack, ws, owner)
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        [row] = _candidates(stack, box["id"])
        return box, row

    def test_record_writes_a_judged_decision(self, stack, ws, owner, real):
        box, row = self._one(stack, ws, owner, real)
        finding = {"company_name": "Acme", "person_email": "priya@acme.com"}
        assert real.record_candidate(
            ws,
            row["id"],
            "m1",
            {
                "status": "judged",
                "outcome": "new_lead",
                "confidence": "high",
                "classifier_version": "010.1",
                "finding": finding,
                "dry_run": True,
            },
        )
        [row] = _candidates(stack, box["id"])
        assert (row["status"], row["outcome"], row["confidence"]) == ("judged", "new_lead", "high")
        assert row["judged_message_id"] == "m1" and row["judged_at"] is not None
        assert row["finding"] == finding and row["dry_run"] is True

    def test_record_refuses_a_stale_message(self, stack, ws, owner, real):
        box, row = self._one(stack, ws, owner, real)
        real.mark_candidates(ws, box["id"], [_row("t1", "m2", timedelta(minutes=1))])
        assert not real.record_candidate(
            ws,
            row["id"],
            "m1",
            {"status": "judged", "outcome": "not_relevant", "confidence": "high", "dry_run": True},
        )
        [row] = _candidates(stack, box["id"])
        assert (row["status"], row["latest_message_id"]) == ("pending", "m2")

    def test_record_refuses_a_decided_row_and_another_workspace(self, stack, ws, owner, real):
        _, row = self._one(stack, ws, owner, real)
        assert real.record_candidate(ws, row["id"], "m1", {"status": "skipped", "dry_run": True})
        assert not real.record_candidate(ws, row["id"], "m1", {"status": "known", "dry_run": True})
        other = _workspace(stack, owner)
        assert not real.record_candidate(other, row["id"], "m1", {"status": "known"})

    def test_record_takes_a_tamil_finding_at_its_largest(self, stack, ws, owner, real):
        # 071's 4096-byte CHECK counts UTF-8 bytes: `finding_of` keeps under it.
        box, row = self._one(stack, ws, owner, real)
        tamil = "அ" * 300
        sent = datetime(2026, 10, 1, 3, 42, tzinfo=UTC)
        messages = [
            {
                "id": "m1",
                "sent_at": sent,
                "direction": "inbound",
                "from_email": "priya@acme.com",
                "outside": [{"email": "priya@acme.com", "name": tamil}],
                "attachments": [f"{n}{tamil}.pdf" for n in range(10)],
            }
        ]
        answer = {
            "outcome": "new_lead",
            "confidence": "high",
            "company_name": tamil,
            "person_name": tamil,
            "person_email": "priya@acme.com",
            "person_title": tamil,
            "lead_stage": "contacted",
            "deal_stage": None,
            "website_choice": None,
            "reason": tamil,
        }
        finding = capture.finding_of(answer, messages, mailbox_email=box["email"], choices=[])
        assert real.record_candidate(
            ws,
            row["id"],
            "m1",
            {
                "status": "judged",
                "outcome": "new_lead",
                "confidence": "high",
                "finding": json.loads(json.dumps(finding, default=str)),
                "dry_run": True,
            },
        )
        [row] = _candidates(stack, box["id"])
        assert row["finding"]["company_name"] == finding["company_name"]

    def test_record_counts_attempts_and_keeps_no_finding_unjudged(self, stack, ws, owner, real):
        box, row = self._one(stack, ws, owner, real)
        assert real.record_candidate(
            ws, row["id"], "m1", {"status": "pending", "attempts": 1, "finding": {"x": 1}}
        )
        [row] = _candidates(stack, box["id"])
        assert (row["status"], row["attempts"], row["finding"]) == ("pending", 1, None)
        assert real.record_candidate(ws, row["id"], "m1", {"status": "failed", "attempts": 3})
        assert _candidates(stack, box["id"])[0]["status"] == "failed"


class TestDryRuns:
    """072 start, set mode, finish — FR-CAP-DIS-10, RPT-01; PD-13."""

    def test_start_resets_dry_run_rows_and_sets_the_mode(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        real.mark_candidates(ws, box["id"], [_row("t1", "m1"), _row("t2", "m2")])
        rows = _candidates(stack, box["id"])
        real.record_candidate(
            ws,
            rows[0]["id"],
            "m1",
            {"status": "judged", "outcome": "not_relevant", "confidence": "low", "dry_run": True},
        )
        real.record_candidate(ws, rows[1]["id"], "m2", {"status": "skipped", "dry_run": False})
        run_id = real.start_dry_run(_actor(ws, owner))
        [run] = _runs(stack, ws)
        assert run["id"] == run_id and run["started_by"] == owner and run["finished_at"] is None
        t1, t2 = _candidates(stack, box["id"])
        assert (t1["status"], t1["outcome"], t1["dry_run"]) == ("pending", None, False)
        assert t2["status"] == "skipped"
        assert _setting(stack, ws) == {"mode": "dry_run"}
        last = _audit(stack, ws)[-1]
        assert (last["action"], last["actor_label"]) == ("setting.updated", "user")
        assert last["after"] == {"value": {"mode": "dry_run"}}

    def test_start_refuses_a_second_open_run(self, stack, ws, owner, real):
        real.start_dry_run(_actor(ws, owner))
        with pytest.raises(CrmRefused) as refused:
            real.start_dry_run(_actor(ws, owner))
        assert (refused.value.status, refused.value.detail) == (409, capture.ALREADY_RUNNING)
        assert len(_runs(stack, ws)) == 1

    def test_set_mode_off_cancels_an_open_run_and_a_new_start_succeeds(
        self, stack, ws, owner, real
    ):
        run_id = real.start_dry_run(_actor(ws, owner))
        done = real.set_capture_mode(_actor(ws, owner), ws, "off")
        assert done == {"mode": "off", "cancelled_run_id": run_id}
        [run] = _runs(stack, ws)
        assert run["cancelled"] is True and run["finished_at"] is not None
        assert _setting(stack, ws) == {"mode": "off"}
        assert _audit(stack, ws)[-1]["after"] == {"value": {"mode": "off"}}
        assert not real.finish_dry_run(ws, run_id, [])
        real.start_dry_run(_actor(ws, owner))
        assert len(_runs(stack, ws)) == 2

    def test_set_mode_live_rejudges_a_dry_runs_decisions(self, stack, ws, owner, real):
        # CAP-INT-2: Live by PATCH after an applied run; the second run's
        # decisions go back to pending (its counts kept as they stood).
        stack.sql(
            "INSERT INTO crm_capture_dry_runs (workspace_id, started_at, finished_at, applied_at) "
            f"VALUES ({stack.q(ws)}, now() - interval '2 days', now() - interval '2 days', "
            "now() - interval '2 days')"
        )
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        run_id = real.start_dry_run(_actor(ws, owner))
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        [row] = _candidates(stack, box["id"])
        real.record_candidate(
            ws,
            row["id"],
            "m1",
            {"status": "judged", "outcome": "new_lead", "confidence": "high", "dry_run": True},
        )
        done = real.set_capture_mode(_actor(ws, owner), ws, "live")
        assert done == {"mode": "live", "cancelled_run_id": run_id}
        [t1] = _candidates(stack, box["id"])
        assert (t1["status"], t1["outcome"], t1["dry_run"]) == ("pending", None, False)
        cancelled = next(r for r in _runs(stack, ws) if r["id"] == run_id)
        assert cancelled["cancelled"] is True and cancelled["counts"]["judged"] == 1

    def test_start_set_mode_and_apply_share_the_capture_lock(self, stack):
        # MIG-1: a start, a mode change and an apply run one after another.
        for name in ("crm_capture_start_dry_run", "crm_capture_set_mode", "crm_capture_apply"):
            [row] = stack.sql(
                "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p "
                f"WHERE p.proname = {stack.q(name)}"
            )
            assert "pg_advisory_xact_lock(hashtext('crm_capture:'" in row["d"], name

    @pytest.mark.parametrize(
        ("mode", "detail"),
        [
            ("live", capture.APPLY_FIRST),
            ("dry_run", capture.START_FROM_CAPTURE),
            ("on", capture.MODE_REFUSAL),
        ],
    )
    def test_set_mode_refusals(self, stack, ws, owner, real, mode, detail):
        with pytest.raises(CrmRefused) as refused:
            real.set_capture_mode(_actor(ws, owner), ws, mode)
        assert (refused.value.status, refused.value.detail) == (422, detail)
        assert _setting(stack, ws) == {"mode": "off"}

    def test_set_mode_refuses_another_workspace(self, stack, ws, owner, real):
        other = _workspace(stack, owner)
        with pytest.raises(CrmRefused) as refused:
            real.set_capture_mode(_actor(ws, owner), other, "off")
        assert refused.value.status == 404

    def test_finish_records_the_recall_once(self, stack, ws, owner, real):
        run_id = real.start_dry_run(_actor(ws, owner))
        recall = [{"mailbox_id": "x", "thread_id": "t1", "latest_at": None, "fate": "found"}]
        assert real.finish_dry_run(ws, run_id, recall)
        assert not real.finish_dry_run(ws, run_id, [])
        [run] = _runs(stack, ws)
        assert run["recall"] == recall and run["finished_at"] is not None

    def test_finish_takes_a_full_recall(self, stack, ws, owner, real):
        # The job keeps up to RECALL_CAP rows; the CHECK holds every one.
        run_id = real.start_dry_run(_actor(ws, owner))
        mailbox = str(uuid.uuid4())
        recall = [
            {
                "mailbox_id": mailbox,
                "thread_id": f"18f{n:013x}",
                "latest_at": "2026-10-19T14:05:31.123000+05:30",
                "fate": "judged_otherwise",
            }
            for n in range(capture.RECALL_CAP)
        ]
        half = capture.RECALL_CAP // 2
        assert real.add_recall(ws, run_id, recall[:half])
        assert real.add_recall(ws, run_id, recall[half:])
        assert real.finish_dry_run(ws, run_id, recall)
        [run] = _runs(stack, ws)
        assert len(run["recall"]) == capture.RECALL_CAP

    def test_recall_add_keeps_progress_and_each_thread_once(self, stack, ws, owner, real):
        run_id = real.start_dry_run(_actor(ws, owner))
        first = {"mailbox_id": "x", "thread_id": "t1", "latest_at": None, "fate": "found"}
        second = {"mailbox_id": "x", "thread_id": "t2", "latest_at": None, "fate": "filed_known"}
        assert real.add_recall(ws, run_id, [first])
        assert real.add_recall(ws, run_id, [{**first, "fate": "outside_scope"}, second])
        [run] = _runs(stack, ws)
        assert run["recall"] == [first, second] and run["finished_at"] is None
        other = _workspace(stack, owner)
        assert not real.add_recall(other, run_id, [first])
        assert real.finish_dry_run(ws, run_id, [first, second])
        assert not real.add_recall(ws, run_id, [first])

    def test_cancel_keeps_the_counts_and_clears_the_recall(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        run_id = real.start_dry_run(_actor(ws, owner))
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        real.add_recall(
            ws, run_id, [{"mailbox_id": "x", "thread_id": "t1", "latest_at": None, "fate": "found"}]
        )
        before = real.capture_report(ws, run_id)
        real.set_capture_mode(_actor(ws, owner), ws, "off")
        [run] = _runs(stack, ws)
        assert run["cancelled"] is True and run["recall"] == []
        real.mark_candidates(ws, box["id"], [_row("t2", "m2")])
        assert real.capture_report(ws, run_id) == before
        assert before["considered"] == 1

    def test_a_cancelled_run_is_never_applicable(self, stack, ws):
        _refused(
            stack,
            "INSERT INTO crm_capture_dry_runs (workspace_id, cancelled) "
            f"VALUES ({stack.q(ws)}, true)",
        )
        _refused(
            stack,
            "INSERT INTO crm_capture_dry_runs (workspace_id, applied_at) "
            f"VALUES ({stack.q(ws)}, now())",
        )


class TestTheReport:
    """072 crm_capture_report — FR-CAP-RPT-02, counts only."""

    def test_report_counts(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        labels = _mailbox(stack, ws, owner)
        real.mark_candidates(ws, box["id"], [_row(f"t{i}", f"m{i}") for i in range(4)])
        real.mark_candidates(ws, labels["id"], [_row("x1", "y1")])
        rows = _candidates(stack, box["id"])
        real.record_candidate(
            ws,
            rows[0]["id"],
            "m0",
            {"status": "judged", "outcome": "new_lead", "confidence": "high", "dry_run": True},
        )
        real.record_candidate(
            ws,
            rows[1]["id"],
            "m1",
            {"status": "judged", "outcome": "not_relevant", "confidence": "low", "dry_run": True},
        )
        # Skipped outside a dry run: a new dry run does not re-open it.
        real.record_candidate(ws, rows[2]["id"], "m2", {"status": "skipped", "dry_run": False})
        real.store_emails(
            ws,
            box["id"],
            [
                {
                    "gmail_message_id": f"g{i}",
                    "thread_id": "stored",
                    "subject": None,
                    "sent_at": _at(),
                    "direction": "inbound",
                    "from_email": "priya@acme.com",
                    "participants": [],
                    "is_internal": False,
                    "is_auto": False,
                    "links": [],
                }
                for i in range(2)
            ],
        )
        run_id = real.start_dry_run(_actor(ws, owner))
        report = real.capture_report(ws, run_id)
        # The dry run's start re-opened the two it judged.
        assert {k: v for k, v in report.items() if k != "by_outcome"} == {
            "considered": 4,
            "pending": 3,
            "skipped_rules": 1,
            "ignored": 0,
            "known": 0,
            "judged": 0,
            "failed": 0,
            "filed_known_threads": 1,
        }
        rows = _candidates(stack, box["id"])
        real.record_candidate(
            ws,
            rows[0]["id"],
            "m0",
            {"status": "judged", "outcome": "new_lead", "confidence": "medium", "dry_run": True},
        )
        report = real.capture_report(ws, run_id)
        assert report["judged"] == 1
        assert report["by_outcome"]["new_lead"] == {"high": 0, "medium": 1, "low": 0}
        assert set(report["by_outcome"]) == {
            "new_lead",
            "deal_signal",
            "other_business",
            "not_relevant",
        }

    def test_report_of_a_finished_run_stays_as_it_stood(self, stack, ws, owner, real):
        # Contracts §2.1, plan §5.7: what the sync marks later is not the run's.
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        run_id = real.start_dry_run(_actor(ws, owner))
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        [row] = _candidates(stack, box["id"])
        real.record_candidate(
            ws,
            row["id"],
            "m1",
            {"status": "judged", "outcome": "not_relevant", "confidence": "high", "dry_run": True},
        )
        assert real.finish_dry_run(ws, run_id, [])
        before = real.capture_report(ws, run_id)
        assert (before["considered"], before["pending"], before["judged"]) == (1, 0, 1)
        real.mark_candidates(ws, box["id"], [_row(f"n{i}", f"y{i}") for i in range(3)])
        assert real.capture_report(ws, run_id) == before

    def test_report_refuses_another_workspaces_run(self, stack, ws, owner, real):
        run_id = real.start_dry_run(_actor(ws, owner))
        other = _workspace(stack, owner)
        with pytest.raises(CrmRefused) as refused:
            real.capture_report(other, run_id)
        assert refused.value.status == 404


class TestTheJobName:
    """072 — the job log, the usage log and `crm_job_latest` know `capture`."""

    def test_capture_job_and_usage_rows_are_accepted(self, stack, ws, real):
        stack.sql(
            "INSERT INTO crm_job_runs (job, workspace_id, started_at, outcome) "
            f"VALUES ('capture', {stack.q(ws)}, now(), 'running')"
        )
        try:
            self._check(stack, ws, real)
        finally:
            # test_crm_followup_db re-runs 066, whose job CHECK predates
            # `capture`: the shared database keeps no such run.
            stack.sql(
                f"DELETE FROM crm_job_runs WHERE workspace_id = {stack.q(ws)} AND job = 'capture'"
            )

    def _check(self, stack, ws, real) -> None:
        real.insert_ai_usage(
            ws,
            {
                "job": "capture",
                "model": "claude-sonnet-5-5",
                "input_tokens": 10,
                "output_tokens": 1,
                "cost_usd": "0.0001",
                "outcome": "ok",
                "at": _at(),
            },
        )
        latest = stack.sql("SELECT crm_job_latest() AS r")[0]["r"]
        assert any(r["job"] == "capture" and r["workspace_id"] == ws for r in latest)
        _refused(
            stack,
            "INSERT INTO crm_job_runs (job, workspace_id, started_at, outcome) "
            f"VALUES ('captures', {stack.q(ws)}, now(), 'running')",
        )


class TestTheStoreReads:
    """The store's 072 reads against the real database (plan §5.6, §5.7)."""

    def test_capture_store_reads(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        assert real.capture_setting(ws) == {"mode": "off"}
        assert real.latest_dry_run(ws) is None and real.has_applied_dry_run(ws) is False
        real.mark_candidates(
            ws,
            box["id"],
            [_row("old", "m1", -timedelta(hours=2)), _row("new", "m2", -timedelta(hours=1))],
        )
        oldest = real.pending_candidates(ws, [box["id"]], newest_first=False)
        newest = real.pending_candidates(ws, [box["id"]], newest_first=True, limit=1)
        assert [c["thread_id"] for c in oldest] == ["old", "new"]
        assert [c["thread_id"] for c in newest] == ["new"]
        assert real.pending_candidates(ws, [], newest_first=True) == []
        run_id = real.start_dry_run(_actor(ws, owner))
        assert real.latest_dry_run(ws)["id"] == run_id
        assert real.capture_setting(ws) == {"mode": "dry_run"}
        real.record_candidate(
            ws,
            oldest[0]["id"],
            "m1",
            {
                "status": "judged",
                "outcome": "new_lead",
                "confidence": "low",
                "dry_run": True,
                "finding": {"company_name": "Acme"},
            },
        )
        [found] = real.dry_run_findings(ws)
        assert (found["thread_id"], found["finding"]) == ("old", {"company_name": "Acme"})
        stack.sql(
            "UPDATE crm_capture_dry_runs SET finished_at = now(), applied_at = now() "
            f"WHERE id = {stack.q(run_id)}"
        )
        assert real.has_applied_dry_run(ws) is True
        real.store_emails(
            ws,
            box["id"],
            [
                {
                    "gmail_message_id": "g1",
                    "thread_id": "s1",
                    "subject": "Re: Proposal",
                    "sent_at": _at(),
                    "direction": "inbound",
                    "from_email": "priya@acme.com",
                    "participants": [],
                    "is_internal": False,
                    "is_auto": False,
                    "links": [],
                }
            ],
        )
        assert real.thread_subjects(ws, box["id"], ["s1", "s2"]) == {"s1": "Re: Proposal"}


# ---------------------------------------------------------------------------
# S3 — migration 073 (tasks T318)
# ---------------------------------------------------------------------------


def _ignores(stack, ws: str) -> list[dict]:
    return stack.sql(
        f"SELECT * FROM crm_capture_ignores WHERE workspace_id = {stack.q(ws)} ORDER BY value"
    )


def _ignore_op(kind: str, value: str, **values) -> dict:
    return {
        "op": "insert",
        "table": "crm_capture_ignores",
        "values": {"kind": kind, "value": value, **values},
    }


def _new_lead(stack, ws: str, mailbox_id: str = "mb") -> str:
    item = capture.to_item(
        {"company_name": "Acme", "person_email": "priya@acme.com", "message_count": 1},
        outcome="new_lead",
        confidence="high",
        mailbox_id=mailbox_id,
        thread_id=f"t{uuid.uuid4().hex[:8]}",
        message_id="m1",
        owner_id=None,
        model="claude-sonnet-5-5",
        generated_at=_at(),
    )
    sync = {"user_id": None, "kind": "sync", "channel": None, "workspace_id": ws}
    [made] = stack.sql(f"SELECT crm_review_upsert({_j(stack, sync)}, {_j(stack, [item])}) AS r")[0][
        "r"
    ]
    return made["id"]


class TestTheIgnoreList:
    """073 crm_capture_ignores — FR-CAP-DIS-03, REV-03; data-model §3.6."""

    def test_ignore_entries_are_audited_as_the_person(self, stack, ws, owner):
        made = _apply(stack, _actor(ws, owner), [_ignore_op("domain", "vendor.io")])[0]["row"]
        assert made["created_by"] == owner and made["from_item_id"] is None
        [audit] = stack.sql(
            "SELECT action, actor_id, actor_label FROM audit_log "
            f"WHERE target_table = 'crm_capture_ignores' AND target_id = {stack.q(made['id'])}"
        )
        assert audit == {
            "action": "capture_ignore.created",
            "actor_id": owner,
            "actor_label": "user",
        }
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "delete", "table": "crm_capture_ignores", "id": made["id"]}],
        )
        assert _ignores(stack, ws) == []
        actions = [
            r["action"]
            for r in stack.sql(
                "SELECT action FROM audit_log WHERE target_table = 'crm_capture_ignores' "
                f"AND target_id = {stack.q(made['id'])} ORDER BY id"
            )
        ]
        assert actions == ["capture_ignore.created", "capture_ignore.purged"]

    @pytest.mark.parametrize(
        ("kind", "value"),
        [
            ("domain", "Vendor.io"),
            ("domain", "a@vendor.io"),
            ("address", "@vendor.io"),
            ("address", "vendor.io"),
            ("domain", "ab"),
            ("sender", "a@vendor.io"),
        ],
    )
    def test_ignore_checks(self, stack, ws, owner, kind, value):
        with pytest.raises(stack.SqlError) as raised:
            _apply(stack, _actor(ws, owner), [_ignore_op(kind, value)])
        assert raised.value.code == "23514", raised.value.message
        assert _ignores(stack, ws) == []

    def test_ignore_an_entry_is_listed_once(self, stack, ws, owner):
        _apply(stack, _actor(ws, owner), [_ignore_op("address", "priya@acme.com")])
        with pytest.raises(stack.SqlError) as raised:
            _apply(stack, _actor(ws, owner), [_ignore_op("address", "priya@acme.com")])
        assert raised.value.code == "23505"
        # The same value in another workspace is its own entry.
        other = _workspace(stack, owner)
        _apply(stack, _actor(other, owner), [_ignore_op("address", "priya@acme.com")])
        assert len(_ignores(stack, ws)) == len(_ignores(stack, other)) == 1

    def test_ignore_from_item_is_of_the_workspace_and_cleared_on_purge(self, stack, ws, owner):
        item = _new_lead(stack, ws)
        made = _apply(
            stack, _actor(ws, owner), [_ignore_op("domain", "acme.com", from_item_id=item)]
        )[0]["row"]
        assert made["from_item_id"] == item
        other = _workspace(stack, owner)
        with pytest.raises(stack.SqlError) as raised:
            _apply(
                stack, _actor(other, owner), [_ignore_op("domain", "acme.com", from_item_id=item)]
            )
        assert raised.value.code == "23503"
        # An item is deleted only by a purge (crm_purge, as its actor).
        _as_system(stack, f"DELETE FROM crm_review_items WHERE id = {stack.q(item)}")
        [row] = _ignores(stack, ws)
        assert row["from_item_id"] is None
        [audit] = stack.sql(
            "SELECT action, before, after FROM audit_log WHERE target_table = "
            f"'crm_capture_ignores' AND target_id = {stack.q(row['id'])} AND action = "
            "'capture_ignore.updated'"
        )
        assert audit["before"] == {"from_item_id": item} and audit["after"] == {
            "from_item_id": None
        }


class TestTheTargetlessItem:
    """073 crm_review_items_target — data-model §3.3."""

    def test_review_a_new_lead_may_have_no_target(self, stack, ws):
        item = _new_lead(stack, ws)
        [row] = stack.sql(
            "SELECT type, record_type, record_id, dedupe_key FROM crm_review_items "
            f"WHERE id = {stack.q(item)}"
        )
        assert (row["type"], row["record_type"], row["record_id"]) == ("new_lead", None, None)
        assert row["dedupe_key"].endswith("|new_lead|thread")

    def test_review_only_a_new_lead_has_no_target_and_never_half_of_one(self, stack, ws, owner):
        company = _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Acme", "owner_id": owner},
                }
            ],
        )[0]["id"]
        base_row = (
            "INSERT INTO crm_review_items (workspace_id, type, record_type, record_id, "
            "source_kind, source_key, source_version, dedupe_key) VALUES "
            f"({stack.q(ws)}, "
        )
        for values in (
            "'deal_signal', NULL, NULL",
            "'new_lead', 'company', NULL",
            f"'new_lead', NULL, {stack.q(company)}",
        ):
            _refused(
                stack,
                base_row + values + ", 'email_thread', 'gmail:mb:t', 'm1', "
                f"{stack.q('k' + uuid.uuid4().hex)})",
            )
        stack.sql(
            base_row + f"'new_lead', 'company', {stack.q(company)}, 'email_thread', "
            f"'gmail:mb:t', 'm1', 'k-linked')"
        )


def _pre074(item: dict) -> dict:
    """An item as 073's code built it: keyed by its conversation
    (`gmail:<mailbox>:<thread>`), no `threads` list. 074's builders key by
    company; 073's `crm_capture_supersede` stays in the tree for such items."""
    link = {k: v for k, v in item["source_link"].items() if k != "threads"}
    key = capture.source_key(link["mailbox_id"], link["thread_id"])
    return {
        **item,
        "source_key": key,
        "source_link": link,
        "dedupe_key": review_rules.dedupe_key(
            "email_thread", key, item["type"], item.get("record_type"), item.get("record_id")
        ),
    }


class TestSupersede:
    """073 crm_capture_supersede — one open new lead a conversation (spec §13
    S3 line 1); data-model §3.7. On items keyed as 073 made them."""

    def test_supersede_expires_the_conversations_other_open_new_leads(self, stack, ws, owner):
        company = _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Acme", "owner_id": owner},
                }
            ],
        )[0]["id"]
        sync = {"user_id": None, "kind": "sync", "channel": None, "workspace_id": ws}

        def lead(thread: str, company_id: str | None) -> dict:
            return _pre074(
                capture.to_item(
                    {"company_name": "Acme", "person_email": "priya@acme.com", "message_count": 1},
                    outcome="new_lead",
                    confidence="high",
                    mailbox_id="mb",
                    thread_id=thread,
                    message_id="m1",
                    owner_id=None,
                    model="claude-sonnet-5-5",
                    generated_at=_at(),
                    duplicate_company_id=company_id,
                )
            )

        unlinked, other = lead("t-1", None), lead("t-2", None)
        made = stack.sql(
            f"SELECT crm_review_upsert({_j(stack, sync)}, {_j(stack, [unlinked, other])}) AS r"
        )[0]["r"]
        linked = lead("t-1", company)
        stack.sql(f"SELECT crm_review_upsert({_j(stack, sync)}, {_j(stack, [linked])}) AS r")
        [n] = stack.sql(
            f"SELECT crm_capture_supersede({_j(stack, sync)}, {stack.q(linked['source_key'])}, "
            f"{stack.q(linked['dedupe_key'])}) AS n"
        )
        assert n["n"] == 1
        rows = {
            r["dedupe_key"]: r["status"]
            for r in stack.sql(
                "SELECT dedupe_key, status FROM crm_review_items "
                f"WHERE workspace_id = {stack.q(ws)}"
            )
        }
        assert rows == {
            unlinked["dedupe_key"]: "expired",
            other["dedupe_key"]: "open",
            linked["dedupe_key"]: "open",
        }
        [audit] = stack.sql(
            "SELECT action, actor_label FROM audit_log WHERE target_table = 'crm_review_items' "
            f"AND target_id = {stack.q(made[0]['id'])} AND action = 'review.expired'"
        )
        assert audit == {"action": "review.expired", "actor_label": "sync"}
        person = {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                f"SELECT crm_capture_supersede({_j(stack, person)}, 'gmail:mb:t-2', 'k') AS n"
            )
        assert raised.value.code == "CRM22"

    def test_supersede_covers_discovered_deal_signals_but_not_documents(self, stack, ws, owner):
        # CAP-INT-3, -4: a discovered deal signal expires like a new lead; a
        # deal document's signal on the same conversation is the sync's.
        company = _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "Acme", "owner_id": owner},
                }
            ],
        )[0]["id"]
        sync = {"user_id": None, "kind": "sync", "channel": None, "workspace_id": ws}
        finding = {
            "company_name": "Acme",
            "person_email": "priya@acme.com",
            "message_count": 1,
            "deal_stage": "discovery",
        }
        common = {
            "confidence": "high",
            "mailbox_id": "mb",
            "thread_id": "t-9",
            "message_id": "m1",
            "owner_id": None,
            "model": "claude-sonnet-5-5",
            "generated_at": _at(),
        }
        lead = _pre074(capture.to_item(finding, outcome="new_lead", **common))
        signal = _pre074(
            capture.to_item(finding, outcome="deal_signal", duplicate_company_id=company, **common)
        )
        document = {
            **signal,
            "dedupe_key": f"{signal['dedupe_key']}|document",
            "model": None,
            "suggestion": {**signal["suggestion"], "evidence": {"origin": "deal_document"}},
        }
        stack.sql(
            f"SELECT crm_review_upsert({_j(stack, sync)}, "
            f"{_j(stack, [lead, signal, document])}) AS r"
        )
        [n] = stack.sql(
            f"SELECT crm_capture_supersede({_j(stack, sync)}, {stack.q(lead['source_key'])}, '') "
            "AS n"
        )
        assert n["n"] == 2
        rows = {
            r["dedupe_key"]: r["status"]
            for r in stack.sql(
                "SELECT dedupe_key, status FROM crm_review_items "
                f"WHERE workspace_id = {stack.q(ws)}"
            )
        }
        assert rows == {
            lead["dedupe_key"]: "expired",
            signal["dedupe_key"]: "expired",
            document["dedupe_key"]: "open",
        }


class TestTheSiblingRead:
    """store.domains_named against PostgREST (S3-C4): a LIKE on the name,
    paged on the id, so no row limit cuts the sibling check short."""

    def test_domains_named_reads_every_page_and_only_that_name(
        self, stack, ws, owner, real, monkeypatch
    ):
        ops = [
            {
                "op": "insert",
                "table": "crm_companies",
                "ref": "c",
                "values": {"name": "Acme", "owner_id": owner},
            }
        ]
        ops += [
            {
                "op": "insert",
                "table": "crm_company_domains",
                "values": {"company_id": "$ref:c", "domain": d, "is_primary": n == 0},
            }
            for n, d in enumerate(("acme.com", "acme.io", "acme.co.uk", "acmecorp.com", "xacme.io"))
        ]
        _apply(stack, _actor(ws, owner), ops)
        monkeypatch.setattr(store, "_PAGE", 2)
        assert [d["domain"] for d in real.domains_named(ws, "acme")] == [
            "acme.com",
            "acme.co.uk",
            "acme.io",
        ]
        assert real.domains_named(ws, "acme_") == []


class TestApplying:
    """073 crm_capture_apply — FR-CAP-RPT-04; data-model §3.7."""

    def _finished(self, stack, ws, owner, real, **values) -> str:
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        run_id = real.start_dry_run(_actor(ws, owner))
        real.mark_candidates(ws, box["id"], [_row("t1", "m1")])
        [row] = _candidates(stack, box["id"])
        real.record_candidate(
            ws,
            row["id"],
            "m1",
            {"status": "judged", "outcome": "new_lead", "confidence": "high", "dry_run": True},
        )
        assert real.finish_dry_run(ws, run_id, [])
        if values:
            sets = ", ".join(f"{k} = {v}" for k, v in values.items())
            stack.sql(f"UPDATE crm_capture_dry_runs SET {sets} WHERE id = {stack.q(run_id)}")
        return run_id

    def test_apply_marks_the_run_and_sets_live(self, stack, ws, owner, real):
        run_id = self._finished(stack, ws, owner, real)
        done = real.apply_dry_run(_actor(ws, owner), run_id)
        assert done["applied_at"]
        [run] = _runs(stack, ws)
        assert run["applied_at"] is not None and run["applied_by"] == owner
        assert {
            r["dry_run"]
            for r in stack.sql(
                f"SELECT dry_run FROM crm_capture_candidates WHERE workspace_id = {stack.q(ws)}"
            )
        } == {False}
        assert _setting(stack, ws) == {"mode": "live"}
        last = _audit(stack, ws)[-1]
        assert (last["action"], last["actor_label"]) == ("setting.updated", "user")
        assert last["after"] == {"value": {"mode": "live"}}
        # Live is accepted from here; a second apply is not.
        assert real.set_capture_mode(_actor(ws, owner), ws, "off")["mode"] == "off"
        assert real.set_capture_mode(_actor(ws, owner), ws, "live")["mode"] == "live"
        with pytest.raises(CrmRefused) as refused:
            real.apply_dry_run(_actor(ws, owner), run_id)
        assert refused.value.status == 422

    @pytest.mark.parametrize(
        "values",
        [
            {"finished_at": "now() - interval '8 days'"},
            {"cancelled": "true", "finished_at": "now()"},
        ],
    )
    def test_apply_refuses_an_old_or_cancelled_run(self, stack, ws, owner, real, values):
        run_id = self._finished(stack, ws, owner, real, **values)
        with pytest.raises(CrmRefused) as refused:
            real.apply_dry_run(_actor(ws, owner), run_id)
        assert (refused.value.status, refused.value.detail) == (
            422,
            "This dry run cannot be applied.",
        )
        assert _setting(stack, ws) == {"mode": "dry_run"}
        assert _runs(stack, ws)[0]["applied_at"] is None

    def test_apply_counts_the_window_from_the_end(self, stack, ws, owner, real):
        run_id = self._finished(
            stack,
            ws,
            owner,
            real,
            started_at="now() - interval '10 days'",
            finished_at="now() - interval '1 day'",
        )
        assert real.apply_dry_run(_actor(ws, owner), run_id)["applied_at"]

    def test_apply_refuses_an_open_run_an_older_run_and_another_workspace(
        self, stack, ws, owner, real
    ):
        older = self._finished(stack, ws, owner, real)
        newer = real.start_dry_run(_actor(ws, owner))
        for run_id in (older, newer):
            with pytest.raises(CrmRefused) as refused:
                real.apply_dry_run(_actor(ws, owner), run_id)
            assert refused.value.status == 422
        other = _workspace(stack, owner)
        with pytest.raises(CrmRefused) as refused:
            real.apply_dry_run(_actor(other, owner), newer)
        assert (refused.value.status, refused.value.detail) == (404, "No such dry run.")

    def test_apply_is_a_persons(self, stack, ws, owner, real):
        run_id = self._finished(stack, ws, owner, real)
        with pytest.raises(CrmRefused) as refused:
            real.apply_dry_run({**_actor(ws, owner), "kind": "system"}, run_id)
        assert refused.value.status == 422

    def test_apply_routed_records_the_hand_over_and_the_counts(self, stack, ws, owner, real):
        # 074 crm_capture_apply_routed — FR-CAP-ACAD-02, FR-CAP-RPT-09.
        box = _mailbox(stack, ws, owner, capture_source="primary_sent")
        run_id = real.start_dry_run(_actor(ws, owner))
        real.mark_candidates(ws, box["id"], [_row("t1", "m1"), _row("t2", "m2")])
        course, lead = _candidates(stack, box["id"])
        for row, business in ((course, "academy"), (lead, "labs")):
            real.record_candidate(
                ws,
                row["id"],
                row["latest_message_id"],
                {
                    "status": "judged",
                    "outcome": "new_lead",
                    "confidence": "high",
                    "dry_run": True,
                    "finding": {"company_name": "City College", "business": business},
                },
            )
        assert real.finish_dry_run(ws, run_id, [])
        stored = _runs(stack, ws)[0]["counts"]
        counts = {"academy": 1, "academy_unrouted": 0}
        routed = {course["id"]: True, lead["id"]: True}
        assert real.capture_apply_routed(_actor(ws, owner), run_id, routed, counts)
        course, lead = _candidates(stack, box["id"])
        assert course["finding"]["routed"] is True
        assert course["finding"]["duplicate_company_id"] is None
        assert "routed" not in lead["finding"]  # a Labs finding is not touched
        assert _runs(stack, ws)[0]["counts"] == {**stored, **counts}
        assert real.capture_report(ws, run_id) == {**stored, **counts}
        assert real.apply_dry_run(_actor(ws, owner), run_id)["applied_at"]
        # Applied: refused like a second apply.
        with pytest.raises(CrmRefused) as refused:
            real.capture_apply_routed(_actor(ws, owner), run_id, routed, counts)
        assert refused.value.status == 422

    @pytest.mark.parametrize(
        ("routed", "counts"),
        [
            ([], {"academy": 0, "academy_unrouted": 0}),
            ({"x": "yes"}, {"academy": 0, "academy_unrouted": 0}),
            ({}, {"academy": 0}),
            ({}, {"academy": -1, "academy_unrouted": 0}),
            ({}, {"academy": 0, "academy_unrouted": 0, "items": 1}),
        ],
    )
    def test_apply_routed_refuses_a_malformed_payload_and_a_system_actor(
        self, stack, ws, owner, real, routed, counts
    ):
        run_id = self._finished(stack, ws, owner, real)
        with pytest.raises(CrmRefused) as refused:
            real.capture_apply_routed(_actor(ws, owner), run_id, routed, counts)
        assert refused.value.status == 422
        with pytest.raises(CrmRefused) as refused:
            real.capture_apply_routed(
                {**_actor(ws, owner), "kind": "system"},
                run_id,
                {},
                {"academy": 0, "academy_unrouted": 0},
            )
        assert refused.value.status == 422


class TestThePurge:
    """073 crm_capture_purge — FR-CAP-DIS-11; E12, PD-15."""

    def test_purge_deletes_decided_rows_older_than_the_cutoff(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        old = "now() - interval '400 days'"
        finding = {"company_name": "Acme", "person_email": "priya@acme.com"}
        _insert(stack, ws, box["id"], thread_id="old-skipped", status="skipped")
        _insert(
            stack,
            ws,
            box["id"],
            thread_id="old-lead",
            status="judged",
            outcome="new_lead",
            confidence="high",
            judged_message_id="m1",
            judged_at=_at(),
            finding=finding,
        )
        _insert(stack, ws, box["id"], thread_id="old-pending")
        _insert(stack, ws, box["id"], thread_id="new-skipped", status="skipped")
        stack.sql(
            f"UPDATE crm_capture_candidates SET updated_at = {old} "
            f"WHERE mailbox_id = {stack.q(box['id'])} AND thread_id LIKE 'old-%'"
        )
        other = _workspace(stack, owner)
        other_box = _mailbox(stack, other, owner)
        _insert(stack, other, other_box["id"], thread_id="old-elsewhere", status="known")
        stack.sql(
            f"UPDATE crm_capture_candidates SET updated_at = {old} "
            f"WHERE mailbox_id = {stack.q(other_box['id'])}"
        )
        cutoff = datetime.now(UTC) - timedelta(days=365)
        assert real.purge_capture(ws, cutoff) == 2
        assert [r["thread_id"] for r in _candidates(stack, box["id"])] == [
            "new-skipped",
            "old-pending",
        ]
        assert len(_candidates(stack, other_box["id"])) == 1
        assert real.purge_capture(ws, cutoff) == 0


# ---------------------------------------------------------------------------
# Spec 010 R1 — migration 074 (tasks T418): one suggestion per company
# ---------------------------------------------------------------------------

SYNC_KIND = "sync"


def _sync(ws: str) -> dict:
    return {"user_id": None, "kind": SYNC_KIND, "channel": None, "workspace_id": ws}


def _finding(n: int, **values) -> dict:
    return {
        "company_name": "Acme Robotics",
        "domain": "acme-robotics.com",
        "person_name": f"Person {n}",
        "person_email": f"p{n}@acme-robotics.com",
        "first_at": f"2026-10-0{n}T09:00:00+05:30",
        "latest_at": f"2026-10-0{n}T10:00:00+05:30",
        "message_count": n,
        "owner_replied": n == 2,
        "reason": f"Conversation {n}.",
        "lead_stage": "new",
        **values,
    }


def _lead_item(n: int, *, company: str | None = None, message: str = "", **values) -> dict:
    return capture.to_item(
        _finding(n, **values),
        outcome="new_lead",
        confidence="high",
        mailbox_id="mb",
        thread_id=f"t{n}",
        message_id=message or f"m{n}",
        owner_id=None,
        model="claude-sonnet-5-5",
        generated_at=_at(),
        duplicate_company_id=company,
    )


def _group(stack, ws: str, item: dict, entry: dict | None = None) -> dict:
    entry = entry or item["source_link"]["threads"][0]
    return stack.sql(
        f"SELECT crm_capture_group_upsert({_j(stack, _sync(ws))}, {_j(stack, item)}, "
        f"{_j(stack, entry)}) AS r"
    )[0]["r"]


def _item_row(stack, item_id: str) -> dict:
    return stack.sql(f"SELECT * FROM crm_review_items WHERE id = {stack.q(item_id)}")[0]


def _open_rows(stack, ws: str) -> list[dict]:
    return stack.sql(
        "SELECT * FROM crm_review_items "
        f"WHERE workspace_id = {stack.q(ws)} AND status = 'open' ORDER BY created_at, id"
    )


def _refs(row: dict) -> list[str]:
    return sorted(e["ref"] for e in row["source_link"].get("threads") or [])


def _decide(stack, item_id: str, status: str, **values) -> None:
    sets = ", ".join(
        [f"status = {stack.q(status)}", "decided_at = now()"]
        + [f"{k} = {stack.q(v)}" for k, v in values.items()]
    )
    stack.sql(f"UPDATE crm_review_items SET {sets} WHERE id = {stack.q(item_id)}")


def _claim(stack, ws: str, owner: str, item_id: str) -> None:
    stack.sql(
        f"SELECT crm_review_claim({_j(stack, _actor(ws, owner))}, {stack.q(item_id)}::uuid, "
        "'approved') AS r"
    )


def _refuse(stack, ws: str, owner: str, item_id: str) -> None:
    stack.sql(
        f"SELECT crm_review_release({_j(stack, _actor(ws, owner))}, {stack.q(item_id)}::uuid, "
        "'Refused.', NULL)"
    )


def _company_id(stack, ws: str, owner: str, name: str = "Acme") -> str:
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


def _deal_id(stack, ws: str, owner: str, company: str, name: str) -> str:
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


class TestGroupUpsert:
    """074 crm_capture_group_upsert — data-model §9.4; FR-CAP-GRP-02, -05,
    -06; plan R1.2, PD-24, PD-38."""

    def test_group_upsert_inserts_then_merges_by_company(self, stack, ws):
        first = _group(stack, ws, _lead_item(1))
        assert (first["created"], first["skipped"], first["revision"]) == (True, False, 1)
        second = _group(stack, ws, _lead_item(2))
        assert (second["id"], second["created"], second["revision"]) == (first["id"], False, 2)
        row = _item_row(stack, first["id"])
        assert row["source_key"] == "capture:domain:acme-robotics.com"
        assert _refs(row) == ["gmail:mb:t1", "gmail:mb:t2"]
        evidence = row["suggestion"]["evidence"]
        assert (evidence["conversations"], evidence["message_count"]) == (2, 3)
        assert evidence["owner_replied"] is True
        assert evidence["first_at"] == "2026-10-01T09:00:00+05:30"
        assert evidence["latest_at"] == "2026-10-02T10:00:00+05:30"
        assert evidence["reason"] == "Conversation 2."
        assert (row["source_version"], row["source_link"]["thread_id"]) == ("m2", "t2")
        assert row["suggestion"]["email"] == "p2@acme-robotics.com"
        # The same conversation state again changes nothing.
        again = _group(stack, ws, _lead_item(2))
        assert again["revision"] == 2

    def test_group_upsert_an_older_entry_joins_but_never_replaces(self, stack, ws):
        made = _group(stack, ws, _lead_item(3))
        _group(stack, ws, _lead_item(1))
        row = _item_row(stack, made["id"])
        assert _refs(row) == ["gmail:mb:t1", "gmail:mb:t3"]
        assert row["suggestion"]["email"] == "p3@acme-robotics.com"
        assert row["source_version"] == "m3"

    def test_group_upsert_skips_a_decided_conversation_state(self, stack, ws):
        made = _group(stack, ws, _lead_item(1))
        _decide(stack, made["id"], "dismissed")
        skipped = _group(stack, ws, _lead_item(1))
        assert (skipped["id"], skipped["skipped"]) == (None, True)
        # A new conversation of the company is a new item.
        fresh = _group(stack, ws, _lead_item(2))
        assert fresh["created"] is True and fresh["id"] != made["id"]

    def test_group_upsert_skips_the_pre_074_form(self, stack, ws):
        old = _pre074(_lead_item(1))
        [made] = stack.sql(
            f"SELECT crm_review_upsert({_j(stack, _sync(ws))}, {_j(stack, [old])}) AS r"
        )[0]["r"]
        _decide(stack, made["id"], "dismissed")
        assert _group(stack, ws, _lead_item(1))["skipped"] is True

    def test_group_upsert_an_expiry_by_refusal_or_merge_is_no_decision(self, stack, ws):
        refused = _group(stack, ws, _lead_item(1))
        _decide(stack, refused["id"], "expired", refusal="Refused.")
        assert _group(stack, ws, _lead_item(1))["skipped"] is False
        merged = _group(stack, ws, _lead_item(2, domain="beta.example"))
        stack.sql(
            "UPDATE crm_review_items SET status = 'expired', decided_at = now(), "
            f'result = \'{{"superseded_by": "{refused["id"]}"}}\'::jsonb '
            f"WHERE id = {stack.q(merged['id'])}"
        )
        assert _group(stack, ws, _lead_item(2, domain="beta.example"))["skipped"] is False

    def test_group_upsert_takes_deals_only_from_the_newest_entry(self, stack, ws, owner):
        company = _company_id(stack, ws, owner)
        early = _deal_id(stack, ws, owner, company, "Early")
        late = _deal_id(stack, ws, owner, company, "Late")

        def signal(n: int, stage: str, deals: list[str]) -> dict:
            item = capture.to_item(
                _finding(n, deal_stage=stage),
                outcome="deal_signal",
                confidence="high",
                mailbox_id="mb",
                thread_id=f"t{n}",
                message_id=f"m{n}",
                owner_id=None,
                model=None,
                generated_at=_at(),
                duplicate_company_id=company,
                open_deals=[{"id": d, "stage": "discovery"} for d in deals],
            )
            return {**item, "current_values": {"stage": stage}}

        made = _group(stack, ws, signal(5, "closed_won", [early, late]))
        _group(stack, ws, signal(2, "proposal", [early]))
        row = _item_row(stack, made["id"])
        assert row["suggestion"]["stage"] == "closed_won"
        assert row["current_values"] == {"stage": "closed_won"}
        deals = stack.sql(
            "SELECT opportunity_id FROM crm_review_item_deals "
            f"WHERE item_id = {stack.q(made['id'])} ORDER BY opportunity_id"
        )
        assert [d["opportunity_id"] for d in deals] == sorted([early, late])
        assert _refs(row) == ["gmail:mb:t2", "gmail:mb:t5"]

    def test_group_upsert_after_a_claim_makes_a_fresh_item(self, stack, ws, owner):
        claimed = _group(stack, ws, _lead_item(1))
        _claim(stack, ws, owner, claimed["id"])
        fresh = _group(stack, ws, _lead_item(2))
        assert fresh["created"] is True and fresh["id"] != claimed["id"]
        assert _refs(_item_row(stack, claimed["id"])) == ["gmail:mb:t1"]
        assert _item_row(stack, claimed["id"])["status"] == "approved"

    def test_a_refusal_merges_into_the_fresh_item_then_expires(self, stack, ws, owner):
        claimed = _group(stack, ws, _lead_item(1))
        _claim(stack, ws, owner, claimed["id"])
        fresh = _group(stack, ws, _lead_item(2))
        _refuse(stack, ws, owner, claimed["id"])
        gone = _item_row(stack, claimed["id"])
        assert (gone["status"], gone["refusal"]) == ("expired", "Refused.")
        kept = _item_row(stack, fresh["id"])
        assert kept["status"] == "open"
        assert _refs(kept) == ["gmail:mb:t1", "gmail:mb:t2"]
        assert kept["suggestion"]["evidence"]["conversations"] == 2
        # Nothing in the refused item blocks its conversation.
        assert _group(stack, ws, _lead_item(1))["skipped"] is False

    def test_a_reopened_item_drops_what_another_open_item_holds(self, stack, ws, owner):
        claimed = _group(stack, ws, _lead_item(1))
        _group(stack, ws, _lead_item(2))
        # While it is claimed, t1 is judged under another company: the
        # claimed item is not changed.
        _claim(stack, ws, owner, claimed["id"])
        # A later message of t1 (the same state would be skipped: the claimed
        # item lists it).
        _group(
            stack,
            ws,
            _lead_item(
                1, domain="zeta.example", message="m1-later", latest_at="2026-10-09T10:00:00+05:30"
            ),
        )
        [fresh] = _open_rows(stack, ws)
        assert fresh["source_key"] == "capture:domain:zeta.example"
        assert _refs(_item_row(stack, claimed["id"])) == ["gmail:mb:t1", "gmail:mb:t2"]
        _refuse(stack, ws, owner, claimed["id"])
        reopened = _item_row(stack, claimed["id"])
        assert reopened["status"] == "open" and reopened["refusal"] == "Refused."
        assert _refs(reopened) == ["gmail:mb:t2"]
        assert reopened["suggestion"]["evidence"]["conversations"] == 1

    def test_a_refusal_merge_leaves_out_what_a_third_item_holds(self, stack, ws, owner):
        """FR-CAP-GRP-07: while A is claimed, t2 moves to another company's
        open item; a fresh item takes A's key; A is refused. Only t1 joins
        the fresh item, so t2 stays in one open item."""
        claimed = _group(stack, ws, _lead_item(1))
        _group(stack, ws, _lead_item(2))
        _claim(stack, ws, owner, claimed["id"])
        moved = _group(
            stack,
            ws,
            _lead_item(
                2, domain="zeta.example", message="m2-later", latest_at="2026-10-08T10:00:00+05:30"
            ),
        )
        fresh = _group(stack, ws, _lead_item(3))
        assert fresh["created"] is True
        _refuse(stack, ws, owner, claimed["id"])
        assert _item_row(stack, claimed["id"])["status"] == "expired"
        assert _refs(_item_row(stack, fresh["id"])) == ["gmail:mb:t1", "gmail:mb:t3"]
        assert _refs(_item_row(stack, moved["id"])) == ["gmail:mb:t2"]
        holders = [r["id"] for r in _open_rows(stack, ws) if "gmail:mb:t2" in _refs(r)]
        assert holders == [moved["id"]]

    def test_a_reopened_item_names_its_newest_sender(self, stack, ws, owner):
        """FR-CAP-GRP-03: the reopened item's person follows its newest
        remaining conversation."""
        claimed = _group(stack, ws, _lead_item(1))
        _group(stack, ws, _lead_item(2))
        _claim(stack, ws, owner, claimed["id"])
        _group(
            stack,
            ws,
            _lead_item(
                2, domain="zeta.example", message="m2-later", latest_at="2026-10-09T10:00:00+05:30"
            ),
        )
        _refuse(stack, ws, owner, claimed["id"])
        row = _item_row(stack, claimed["id"])
        assert row["status"] == "open" and _refs(row) == ["gmail:mb:t1"]
        assert (row["suggestion"]["email"], row["suggestion"]["first_name"]) == (
            "p1@acme-robotics.com",
            "Person",
        )

    def test_a_reopened_item_left_with_nothing_expires(self, stack, ws, owner):
        claimed = _group(stack, ws, _lead_item(1))
        _claim(stack, ws, owner, claimed["id"])
        assert _group(stack, ws, _lead_item(1, domain="zeta.example"))["skipped"] is True
        _group(
            stack,
            ws,
            _lead_item(
                1, domain="zeta.example", message="m1-later", latest_at="2026-10-09T10:00:00+05:30"
            ),
        )
        _refuse(stack, ws, owner, claimed["id"])
        row = _item_row(stack, claimed["id"])
        assert (row["status"], row["refusal"]) == ("expired", "Refused.")
        [audit] = stack.sql(
            "SELECT before, after FROM audit_log WHERE target_table = 'crm_review_items' "
            f"AND target_id = {stack.q(claimed['id'])} AND action = 'review.expired'"
        )
        assert audit == {"before": {"status": "approved"}, "after": {"status": "expired"}}

    def test_group_upsert_is_the_sync_or_the_system_only(self, stack, ws, owner):
        item = _lead_item(1)
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                f"SELECT crm_capture_group_upsert({_j(stack, _actor(ws, owner))}, "
                f"{_j(stack, item)}, {_j(stack, item['source_link']['threads'][0])})"
            )
        assert raised.value.code == "CRM22"


class TestReleaseThread:
    """074 crm_capture_release_thread — FR-CAP-GRP-07; PD-39."""

    def test_release_removes_recomputes_and_expires_an_empty_item(self, stack, ws):
        both = _group(stack, ws, _lead_item(1))
        _group(stack, ws, _lead_item(2))
        alone = _group(stack, ws, _lead_item(3, domain="beta.example"))
        out = stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t2', '') AS r"
        )[0]["r"]
        assert out == {"removed": 1, "expired": 0}
        row = _item_row(stack, both["id"])
        assert _refs(row) == ["gmail:mb:t1"]
        assert row["suggestion"]["evidence"]["reason"] == "Conversation 1."
        assert row["source_version"] == "m1" and row["revision"] == 3
        out = stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t3', '') AS r"
        )[0]["r"]
        assert out == {"removed": 1, "expired": 1}
        assert _item_row(stack, alone["id"])["status"] == "expired"
        [audit] = stack.sql(
            "SELECT actor_label FROM audit_log WHERE action = 'review.expired' "
            f"AND target_id = {stack.q(alone['id'])}"
        )
        assert audit["actor_label"] == "sync"

    def test_release_never_moves_a_deal_documents_entry(self, stack, ws, owner):
        company = _company_id(stack, ws, owner)
        document = capture.deal_signal_item(
            mailbox_id="mb",
            thread_id="t1",
            message_id="m-doc",
            company_id=company,
            deals=[],
            stage="proposal",
            kinds=["sow"],
            direction="outbound",
            message_at=datetime.now(UTC),
            owner_id=None,
            generated_at=_at(),
        )
        made = _group(stack, ws, document)
        assert made["created"] is True
        stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t1', '') AS r"
        )
        assert _item_row(stack, made["id"])["status"] == "open"
        # A discovery lead on the same conversation leaves the document too.
        _group(stack, ws, _lead_item(1))
        assert _refs(_item_row(stack, made["id"])) == ["gmail:mb:t1"]

    def test_release_of_the_newest_moves_the_suggested_person(self, stack, ws):
        """FR-CAP-GRP-03: the person suggested is the newest remaining
        conversation's sender, never the one released (title dropped)."""
        made = _group(stack, ws, _lead_item(1, person_name="Bob Rao"))
        _group(stack, ws, _lead_item(2, person_name="Alice Iyer", person_title="CTO"))
        row = _item_row(stack, made["id"])
        assert (row["suggestion"]["email"], row["suggestion"]["title"]) == (
            "p2@acme-robotics.com",
            "CTO",
        )
        stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t2', '') AS r"
        )
        s = _item_row(stack, made["id"])["suggestion"]
        assert (s["email"], s["first_name"], s["last_name"]) == (
            "p1@acme-robotics.com",
            "Bob",
            "Rao",
        )
        assert "title" not in s
        # Releasing an older conversation keeps the person as it is.
        _group(stack, ws, _lead_item(3, person_title="CEO"))
        stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t1', '') AS r"
        )
        s = _item_row(stack, made["id"])["suggestion"]
        assert (s["email"], s["title"]) == ("p3@acme-robotics.com", "CEO")

    def test_a_discovery_signal_keeps_a_documents_entry(self, stack, ws, owner):
        """PD-39: a discovery signal on a deal document's conversation
        replaces its entry's state but not its origin, so a later
        re-judgement to no item never releases it."""
        company = _company_id(stack, ws, owner)
        document = capture.deal_signal_item(
            mailbox_id="mb",
            thread_id="t1",
            message_id="m-doc",
            company_id=company,
            deals=[],
            stage="proposal",
            kinds=["proposal"],
            direction="outbound",
            message_at=datetime(2026, 10, 1, tzinfo=UTC),
            owner_id=None,
            generated_at=_at(),
        )
        made = _group(stack, ws, document)
        discovered = capture.to_item(
            _finding(1, deal_stage="proposal", latest_at="2026-10-05T10:00:00+05:30"),
            outcome="deal_signal",
            confidence="high",
            mailbox_id="mb",
            thread_id="t1",
            message_id="m-reply",
            owner_id=None,
            model=None,
            generated_at=_at(),
            duplicate_company_id=company,
            open_deals=[],
        )
        assert _group(stack, ws, discovered)["id"] == made["id"]
        [entry] = _item_row(stack, made["id"])["source_link"]["threads"]
        assert (entry["message_id"], entry["origin"]) == ("m-reply", "deal_document")
        stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t1', '') AS r"
        )
        row = _item_row(stack, made["id"])
        assert row["status"] == "open" and _refs(row) == ["gmail:mb:t1"]

    def test_merge_keeps_a_documents_origin_either_way(self, stack):
        doc = {
            "ref": "r",
            "message_id": "a",
            "latest_at": "2026-10-01T00:00:00Z",
            "origin": "deal_document",
        }
        newer = {
            "ref": "r",
            "message_id": "b",
            "latest_at": "2026-10-02T00:00:00Z",
            "origin": "discovery",
        }
        older = {**newer, "message_id": "z", "latest_at": "2026-09-01T00:00:00Z"}
        [r] = stack.sql(
            f"SELECT crm_capture_merge({_j(stack, [doc])}, {_j(stack, newer)}) AS a, "
            f"crm_capture_merge({_j(stack, [older])}, {_j(stack, doc)}) AS b, "
            f"crm_capture_merge({_j(stack, [older])}, {_j(stack, newer)}) AS c"
        )
        assert r["a"] == [{**newer, "origin": "deal_document"}]
        assert r["b"] == [doc]
        assert r["c"] == [newer]

    def test_release_skips_an_item_no_longer_open(self, stack, ws, owner):
        claimed = _group(stack, ws, _lead_item(1))
        _claim(stack, ws, owner, claimed["id"])
        stack.sql(
            f"SELECT crm_capture_release_thread({_j(stack, _sync(ws))}, 'gmail:mb:t1', '') AS r"
        )
        row = _item_row(stack, claimed["id"])
        assert row["status"] == "approved" and _refs(row) == ["gmail:mb:t1"]


class TestConsolidate:
    """074 crm_capture_consolidate — FR-CAP-GRP-08; PD-26; spec §14.5 I1."""

    def _old(self, stack, ws: str, n: int) -> str:
        old = _pre074(_lead_item(n))
        [made] = stack.sql(
            f"SELECT crm_review_upsert({_j(stack, _sync(ws))}, {_j(stack, [old])}) AS r"
        )[0]["r"]
        stack.sql(
            "UPDATE crm_review_items SET created_at = "
            f"'2026-10-0{n}T00:00:00+00:00' WHERE id = {stack.q(made['id'])}"
        )
        return made["id"]

    def _plan(self, members: list[str]) -> list[dict]:
        return [
            {
                "source_key": "capture:domain:acme-robotics.com",
                "dedupe_key": "email_thread|capture:domain:acme-robotics.com|new_lead|thread",
                "record_type": None,
                "record_id": None,
                "company_label": "HearSight",
                "members": members,
            }
        ]

    def _run(self, stack, ws: str, plan: list[dict]) -> dict:
        return stack.sql(
            f"SELECT crm_capture_consolidate({_j(stack, _sync(ws))}, {_j(stack, plan)}) AS r"
        )[0]["r"]

    def test_consolidate_merges_into_the_newest_and_audits_each(self, stack, ws):
        ids = [self._old(stack, ws, n) for n in range(1, 10)]
        out = self._run(stack, ws, self._plan(list(reversed(ids))))
        assert out == {"groups": 1, "expired": 8, "rekeyed": 1}
        survivor = _item_row(stack, ids[-1])
        assert survivor["status"] == "open"
        assert survivor["source_key"] == "capture:domain:acme-robotics.com"
        assert len(survivor["source_link"]["threads"]) == 9
        assert survivor["suggestion"]["evidence"]["message_count"] == sum(range(1, 10))
        for item_id in ids[:-1]:
            row = _item_row(stack, item_id)
            assert row["status"] == "expired"
            assert row["result"] == {
                "superseded_by": survivor["id"],
                "reason": "Merged into one suggestion for HearSight.",
            }
            assert "threads" not in row["source_link"]
            [audit] = stack.sql(
                "SELECT after FROM audit_log WHERE action = 'review.expired' "
                f"AND target_id = {stack.q(item_id)}"
            )
            assert audit["after"] == {"status": "expired", "superseded_by": survivor["id"]}
        # A second run changes nothing.
        before = _item_row(stack, survivor["id"])
        assert self._run(stack, ws, self._plan([survivor["id"]])) == {
            "groups": 0,
            "expired": 0,
            "rekeyed": 0,
        }
        assert _item_row(stack, survivor["id"]) == before

    def test_consolidate_leaves_decided_items(self, stack, ws, owner):
        ids = [self._old(stack, ws, n) for n in (1, 2, 3)]
        _decide(stack, ids[0], "dismissed")
        _claim(stack, ws, owner, ids[2])
        out = self._run(stack, ws, self._plan(ids))
        assert out["expired"] == 0 and out["groups"] == 1
        assert _item_row(stack, ids[0])["status"] == "dismissed"
        assert _item_row(stack, ids[2])["status"] == "approved"
        assert _item_row(stack, ids[2])["source_key"].startswith("gmail:")
        assert _item_row(stack, ids[1])["source_key"] == "capture:domain:acme-robotics.com"

    def test_consolidate_takes_an_open_item_already_on_the_key(self, stack, ws):
        old = self._old(stack, ws, 1)
        new = _group(stack, ws, _lead_item(2))
        out = self._run(stack, ws, self._plan([old]))
        assert out["groups"] == 1
        [row] = _open_rows(stack, ws)
        assert row["id"] == new["id"]
        assert _refs(row) == ["gmail:mb:t1", "gmail:mb:t2"]
        assert _item_row(stack, old)["result"]["superseded_by"] == new["id"]

    def test_consolidate_takes_the_capture_lock_and_refuses_a_non_array(self, stack, ws):
        for name in (
            "crm_capture_consolidate",
            "crm_capture_group_upsert",
            "crm_capture_release_thread",
            "crm_review_release",
        ):
            [row] = stack.sql(
                "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p "
                f"WHERE p.proname = {stack.q(name)}"
            )
            assert "pg_advisory_xact_lock(hashtext('crm_capture:'" in row["d"], name
        with pytest.raises(stack.SqlError) as raised:
            self._run(stack, ws, {"not": "a list"})  # type: ignore[arg-type]
        assert raised.value.code == "CRM22"


class TestMergeKeepsCaptureKeys:
    """065 crm_merge_companies still moves `capture:linked` and
    `capture:deal` items (their `company:` head), plan R1.2."""

    def test_merge_rekeys_linked_and_deal_items(self, stack, ws, owner):
        keep = _company_id(stack, ws, owner, "Keep")
        gone = _company_id(stack, ws, owner, "Gone")
        linked = _group(stack, ws, _lead_item(1, company=gone))
        signal = capture.to_item(
            _finding(2, deal_stage="discovery"),
            outcome="deal_signal",
            confidence="high",
            mailbox_id="mb",
            thread_id="t2",
            message_id="m2",
            owner_id=None,
            model=None,
            generated_at=_at(),
            duplicate_company_id=gone,
        )
        deal = _group(stack, ws, signal)
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
        assert _item_row(stack, linked["id"])["dedupe_key"] == (
            f"email_thread|capture:linked|new_lead|company:{keep}"
        )
        assert _item_row(stack, deal["id"])["dedupe_key"] == (
            f"email_thread|capture:deal|deal_signal|company:{keep}"
        )


class TestTheRoutingSetting:
    """074 `capture_routing` — data-model §9.2a; FR-CAP-ACAD-03; PD-41."""

    def test_routing_every_workspace_is_seeded_on(self, stack):
        rows = stack.sql(
            "SELECT w.id, s.value FROM crm_workspaces w LEFT JOIN crm_workspace_settings s "
            "ON s.workspace_id = w.id AND s.key = 'capture_routing'"
        )
        assert rows and all(r["value"] == {"accept_routed": True} for r in rows)

    def test_routing_a_new_workspace_is_seeded_by_the_trigger(self, stack, ws):
        [row] = stack.sql(
            "SELECT value FROM crm_workspace_settings "
            f"WHERE workspace_id = {stack.q(ws)} AND key = 'capture_routing'"
        )
        assert row["value"] == {"accept_routed": True}
        [audit] = [
            r for r in _audit(stack, ws) if (r["after"] or {}).get("key") == "capture_routing"
        ]
        assert (audit["action"], audit["actor_label"]) == ("setting.created", "system")
        assert _setting(stack, ws) == {"mode": "off"}

    def test_routing_the_key_check_takes_capture_routing(self, stack):
        [row] = stack.sql(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'crm_workspace_settings_key_check'"
        )
        assert "'capture_routing'" in row["d"] and "'capture'" in row["d"]
