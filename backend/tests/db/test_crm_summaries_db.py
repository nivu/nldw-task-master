"""Email thread summaries in the real database — spec 009 FR-CRMAI-SUM-01,
-02, -05, -10, -11, FR-CRMAI-CHG-11 (summaries); data-model §3.3, §4, §5;
migration 062.

What only Postgres can prove: one summary per thread (`UNIQUE (mailbox_id,
thread_id)`); `crm_pending_threads` lists a thread of an active mailbox with
a qualifying message stored after the backfill, and stops listing it once
its summary's watermark is the latest message; `crm_store_summary`
re-checks that watermark, holds every link to the workspace and stores the
items through `crm_review_upsert`, all in one transaction, and locks its
watermark so a concurrent `crm_remove_emails` never leaves a summary on an
emptied thread; `crm_remove_emails`
drops the summary of a thread left with no message; `crm_purge` and
`crm_merge_companies` take and move a record's summary links; a summary
longer than 600 characters is refused by its CHECK. The two tables' place in
`NOT_AUDITED` (and RLS, grants and EXECUTE) is checked by
`test_crm_security_db.py`.

The store wrappers of `services/crm/store.py` run against the local stack.
Each test works in workspaces of its own.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest
from postgrest.exceptions import APIError
from supabase import create_client

from app.services.crm import CrmRefused, store

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


def _sync(ws: str) -> dict:
    return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": ws}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


def _workspace(stack, owner) -> str:
    key = f"s{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Summaries ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


@pytest.fixture
def ws(stack, owner) -> str:
    return _workspace(stack, owner)


@pytest.fixture
def other(stack, owner) -> str:
    return _workspace(stack, owner)


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _mailbox(stack, ws, owner, *, backfilled: str | None = "2020-01-01T00:00:00+00:00") -> str:
    box = _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_mailboxes",
                "values": {
                    "email": f"s{uuid.uuid4().hex[:12]}@nunnarilabs.com",
                    "owner_id": owner,
                    "labels": ["Sales"],
                },
            }
        ],
    )[0]["id"]
    if backfilled:
        # The sync's cursor: written directly, never audited (039).
        stack.sql(
            f"UPDATE crm_mailboxes SET backfilled_at = {stack.q(backfilled)} "
            f"WHERE id = {stack.q(box)}"
        )
    return box


def _company(stack, ws, owner, name="Acme") -> str:
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


def _deal(stack, ws, owner, company) -> str:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_opportunities",
                "values": {
                    "name": "Pilot",
                    "company_id": company,
                    "owner_id": owner,
                    "probability": 10,
                    "next_step": "Call",
                    "next_step_on": "2026-10-21",
                },
            }
        ],
    )[0]["id"]


def _message(gmail_id: str, links: list[dict], **values) -> dict:
    return {
        "gmail_message_id": gmail_id,
        "thread_id": "t1",
        "subject": "Proposal",
        "sent_at": "2026-10-13T09:00:00+00:00",
        "direction": "inbound",
        "from_email": "priya@acme.com",
        "participants": [{"email": "priya@acme.com", "name": "Priya", "role": "from"}],
        "is_internal": False,
        "is_auto": False,
        "links": links,
        **values,
    }


def _plan(box: str, latest: dict, links: list[dict], **values) -> dict:
    return {
        "mailbox_id": box,
        "thread_id": "t1",
        "summary": "Priya asked for a proposal. We will send it.",
        "asks": ["A proposal"],
        "commitments": [
            {"text": "Send it", "owner_id": None, "owner_unknown": True, "due_on": None}
        ],
        "next_step": None,
        "next_step_on": None,
        "sentiment": "neutral",
        "watermark": latest,
        "model": "claude-sonnet-5-5",
        "generated_at": "2026-10-18T06:00:00+00:00",
        "links": links,
        "items": [],
        **values,
    }


def _at_once(first: str, second, stack, *, head_start: float = 0.4):
    """Run `first` (which sleeps holding its locks) and, once it has started,
    `second()`; return what `first` returned or raised."""
    outcome: dict = {}

    def run_first():
        try:
            outcome["value"] = stack.sql(first)
        except stack.SqlError as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=run_first)
    worker.start()
    time.sleep(head_start)
    try:
        second()
    finally:
        worker.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _summaries(stack, box: str) -> list[dict]:
    return stack.sql(
        "SELECT id, thread_id, watermark_message_id FROM crm_email_summaries "
        f"WHERE mailbox_id = {stack.q(box)} ORDER BY thread_id"
    )


@pytest.fixture
def thread(stack, ws, owner, real):
    """A mailbox with one labelled thread about Acme and its deal."""
    box = _mailbox(stack, ws, owner)
    acme = _company(stack, ws, owner)
    deal = _deal(stack, ws, owner, acme)
    links = [
        {"record_type": "company", "record_id": acme},
        {"record_type": "opportunity", "record_id": deal},
    ]
    real.store_emails(ws, box, [_message("m1", links)])
    return {"box": box, "acme": acme, "deal": deal, "links": links}


def _store(real, ws, thread, **values) -> dict:
    [pending] = [p for p in real.pending_threads(ws, 20) if p["mailbox_id"] == thread["box"]]
    return real.store_summary(
        _sync(ws), _plan(thread["box"], pending["latest"], thread["links"], **values)
    )


class TestOnePerThread:
    """Q-06, FR-CRMAI-SUM-02."""

    def test_summaries_db_unique_per_thread(self, stack, ws, owner, real, thread):
        _store(real, ws, thread)
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                "INSERT INTO crm_email_summaries (workspace_id, mailbox_id, thread_id, summary, "
                "sentiment, watermark_message_id, watermark_at, model, generated_at) VALUES "
                f"({stack.q(ws)}, {stack.q(thread['box'])}, 't1', 'A. B.', 'neutral', 'm1', "
                "now(), 'm', now())"
            )
        assert raised.value.code == "23505"

    def test_summaries_db_a_long_summary_is_refused(self, stack, ws, real, thread):
        with pytest.raises(APIError) as raised:
            _store(real, ws, thread, summary="x" * 601)
        assert raised.value.code == "23514"
        assert _summaries(stack, thread["box"]) == []

    def test_summaries_db_ten_longest_asks_in_tamil_are_stored(self, stack, ws, real, thread):
        # 200 characters of 3 UTF-8 bytes each, ten times: what the schema allows.
        long = "த" * 200
        _store(
            real,
            ws,
            thread,
            asks=[long] * 10,
            commitments=[
                {"text": long, "owner_id": None, "owner_unknown": True, "due_on": "2026-10-24"}
            ]
            * 10,
        )
        assert len(_summaries(stack, thread["box"])) == 1


class TestPending:
    """FR-CRMAI-SUM-01, -02, -10, -11."""

    def test_summaries_db_pending_until_summarised_then_again_on_a_new_message(
        self, stack, ws, real, thread
    ):
        [pending] = [p for p in real.pending_threads(ws, 20) if p["mailbox_id"] == thread["box"]]
        assert pending["thread_id"] == "t1"
        assert pending["message_ids"] == ["m1"]
        assert pending["messages"][0]["direction"] == "inbound"
        assert pending["latest"]["gmail_id"] == "m1"
        assert sorted((lk["record_type"], lk["record_id"]) for lk in pending["links"]) == sorted(
            (lk["record_type"], lk["record_id"]) for lk in thread["links"]
        )
        _store(real, ws, thread)
        assert real.pending_threads(ws, 20) == []
        real.store_emails(
            ws, thread["box"], [_message("m2", thread["links"], sent_at="2026-10-14T09:00:00Z")]
        )
        [again] = real.pending_threads(ws, 20)
        assert (again["latest"]["gmail_id"], again["message_ids"]) == ("m2", ["m1", "m2"])

    def test_summaries_db_backfill_paused_internal_and_auto_are_not_pending(
        self, stack, ws, owner, real
    ):
        acme = _company(stack, ws, owner)
        links = [{"record_type": "company", "record_id": acme}]
        fresh = _mailbox(stack, ws, owner, backfilled=None)
        real.store_emails(ws, fresh, [_message("b1", links)])
        later = _mailbox(stack, ws, owner, backfilled="2999-01-01T00:00:00+00:00")
        real.store_emails(ws, later, [_message("b2", links)])
        paused = _mailbox(stack, ws, owner)
        real.store_emails(ws, paused, [_message("p1", links)])
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_mailboxes",
                    "id": paused,
                    "values": {"is_active": False},
                }
            ],
        )
        box = _mailbox(stack, ws, owner)
        real.store_emails(
            ws,
            box,
            [
                _message("i1", links, is_internal=True),
                _message("a1", links, is_auto=True, thread_id="t2"),
                _message("d1", [], thread_id="t3"),
            ],
        )
        assert real.pending_threads(ws, 20) == []

    def test_summaries_db_another_workspace_is_not_listed(self, stack, ws, other, real, thread):
        assert real.pending_threads(other, 20) == []


class TestStoring:
    """FR-CRMAI-SUM-02, -05, -06, -07."""

    def test_summaries_db_watermark_re_check(self, stack, ws, real, thread):
        [pending] = real.pending_threads(ws, 20)
        real.store_emails(
            ws, thread["box"], [_message("m2", thread["links"], sent_at="2026-10-14T09:00:00Z")]
        )
        with pytest.raises(CrmRefused) as refused:
            real.store_summary(_sync(ws), _plan(thread["box"], pending["latest"], thread["links"]))
        assert refused.value.status == 409
        assert _summaries(stack, thread["box"]) == []

    def test_summaries_db_links_refuse_another_workspaces_record(
        self, stack, ws, other, owner, real, thread
    ):
        foreign = _company(stack, other, owner, "Elsewhere")
        with pytest.raises(CrmRefused) as refused:
            _store(
                real,
                ws,
                {**thread, "links": [{"record_type": "company", "record_id": foreign}]},
            )
        assert refused.value.status == 404
        assert _summaries(stack, thread["box"]) == []

    def test_summaries_db_replaces_links_and_stores_items(self, stack, ws, owner, real, thread):
        item = {
            "type": "next_step_change",
            "owner_id": owner,
            "record_type": "opportunity",
            "record_id": thread["deal"],
            "suggestion": {"next_step": "Send it", "next_step_on": "2026-10-24"},
            "current_values": {thread["deal"]: {"next_step": "Call", "next_step_on": "2026-10-21"}},
            "source_kind": "email_thread",
            "source_key": f"gmail:{thread['box']}:t1",
            "source_version": "m1",
            "source_link": {"mailbox_id": thread["box"], "thread_id": "t1"},
            "dedupe_key": f"email_thread|gmail:{thread['box']}:t1|next_step_change|"
            f"opportunity:{thread['deal']}",
            "model": "claude-sonnet-5-5",
            "generated_at": "2026-10-18T06:00:00+00:00",
            "deal_choices": [thread["deal"]],
        }
        made = _store(real, ws, thread, items=[item])
        assert made["items"][0]["created"] is True
        again = real.store_summary(
            _sync(ws),
            _plan(
                thread["box"],
                {"gmail_id": "m1", "sent_at": "2026-10-13T09:00:00+00:00"},
                thread["links"][:1],
                summary="Priya asked again. We will send it today.",
            ),
        )
        assert again["id"] == made["id"]
        [row] = real.summaries_for(ws, "company", thread["acme"])
        assert row["summary"] == "Priya asked again. We will send it today."
        assert row["links"] == [{"record_type": "company", "record_id": thread["acme"]}]
        assert row["subject"] == "Proposal"
        assert real.summaries_for(ws, "opportunity", thread["deal"]) == []
        assert set(real.summary_by_thread(ws, thread["box"], ["t1", "t9"])) == {"t1"}
        [stored] = stack.sql(
            f"SELECT status, source_version FROM crm_review_items WHERE id = "
            f"{stack.q(made['items'][0]['id'])}"
        )
        assert stored == {"status": "open", "source_version": "m1"}

    def test_summaries_db_another_workspaces_mailbox_is_refused(
        self, stack, ws, other, real, thread
    ):
        with pytest.raises(CrmRefused) as refused:
            real.store_summary(
                _sync(other),
                _plan(
                    thread["box"],
                    {"gmail_id": "m1", "sent_at": "2026-10-13T09:00:00+00:00"},
                    [],
                ),
            )
        assert refused.value.status == 404
        assert _summaries(stack, thread["box"]) == []

    def test_summaries_db_a_commitment_under_another_target_is_not_suggested_again(
        self, stack, ws, owner, real, thread
    ):
        def commitment(record_type: str, record_id: str) -> dict:
            return {
                "type": "task_from_commitment",
                "owner_id": owner,
                "record_type": record_type,
                "record_id": record_id,
                "suggestion": {
                    "title": "Send it",
                    "due_on": "2026-10-24",
                    "owner_id": None,
                    "owner_unknown": True,
                },
                "current_values": {},
                "source_kind": "email_thread",
                "source_key": f"gmail:{thread['box']}:t1",
                "source_version": "f00dfeedf00dfeed",
                "source_link": {"mailbox_id": thread["box"], "thread_id": "t1"},
                "dedupe_key": f"email_thread|gmail:{thread['box']}:t1|task_from_commitment|"
                f"{record_type}:{record_id}#f00dfeedf00dfeed",
                "model": "claude-sonnet-5-5",
                "generated_at": "2026-10-18T06:00:00+00:00",
                "deal_choices": [],
            }

        made = _store(real, ws, thread, items=[commitment("opportunity", thread["deal"])])
        assert made["items"][0]["created"] is True
        latest = {"gmail_id": "m1", "sent_at": "2026-10-13T09:00:00+00:00"}
        again = real.store_summary(
            _sync(ws),
            _plan(
                thread["box"],
                latest,
                thread["links"],
                items=[commitment("company", thread["acme"])],
            ),
        )
        assert again["items"] == []
        assert stack.sql(
            "SELECT count(*) AS n FROM crm_review_items "
            f"WHERE workspace_id = {stack.q(ws)} AND type = 'task_from_commitment'"
        ) == [{"n": 1}]

    def test_summaries_db_only_the_jobs_actor_stores(self, stack, ws, owner, real, thread):
        with pytest.raises(CrmRefused) as refused:
            real.store_summary(
                _actor(ws, owner),
                _plan(thread["box"], {"gmail_id": "m1", "sent_at": "x"}, thread["links"]),
            )
        assert refused.value.status == 422


class TestTheThreadAndItsRecords:
    """FR-CRMAI-SUM-02, FR-CRMAI-CHG-11, data-model §4."""

    def test_summaries_db_remove_emails_drops_the_summary_of_an_emptied_thread(
        self, stack, ws, real, thread
    ):
        real.store_emails(
            ws, thread["box"], [_message("m2", thread["links"], sent_at="2026-10-14T09:00:00Z")]
        )
        [pending] = real.pending_threads(ws, 20)
        real.store_summary(_sync(ws), _plan(thread["box"], pending["latest"], thread["links"]))
        real.remove_emails(ws, thread["box"], ["m1"])
        assert len(_summaries(stack, thread["box"])) == 1
        real.remove_emails(ws, thread["box"], ["m2"])
        assert _summaries(stack, thread["box"]) == []
        assert stack.sql(
            "SELECT count(*) AS n FROM crm_email_summary_links "
            f"WHERE record_id = {stack.q(thread['acme'])}"
        ) == [{"n": 0}]

    def test_summaries_db_removing_the_watermark_message_drops_the_summary(
        self, stack, ws, real, thread
    ):
        """070: the summary was built from the message removed; the thread is
        due again and is summarised from what is still stored."""
        real.store_emails(
            ws, thread["box"], [_message("m2", thread["links"], sent_at="2026-10-14T09:00:00Z")]
        )
        [pending] = real.pending_threads(ws, 20)
        real.store_summary(_sync(ws), _plan(thread["box"], pending["latest"], thread["links"]))
        assert [s["watermark_message_id"] for s in _summaries(stack, thread["box"])] == ["m2"]
        real.remove_emails(ws, thread["box"], ["m2"])
        assert _summaries(stack, thread["box"]) == []
        assert stack.sql(
            "SELECT count(*) AS n FROM crm_email_summary_links "
            f"WHERE record_id = {stack.q(thread['acme'])}"
        ) == [{"n": 0}]
        [again] = real.pending_threads(ws, 20)
        assert (again["latest"]["gmail_id"], again["message_ids"]) == ("m1", ["m1"])

    def test_summaries_db_remove_emails_during_a_store_drops_the_new_summary(
        self, stack, ws, real, thread
    ):
        """The store holds its lock first: the removal waits for it, then
        deletes the summary of the emptied thread."""
        [pending] = real.pending_threads(ws, 20)
        plan = _plan(thread["box"], pending["latest"], thread["links"])
        first = (
            f"BEGIN; SELECT crm_store_summary({_j(stack, _sync(ws))}, {_j(stack, plan)}); "
            "SELECT pg_sleep(1.5); COMMIT;"
        )
        _at_once(first, lambda: real.remove_emails(ws, thread["box"], ["m1"]), stack)
        assert _summaries(stack, thread["box"]) == []
        assert stack.sql(
            "SELECT count(*) AS n FROM crm_email_summary_links "
            f"WHERE record_id = {stack.q(thread['acme'])}"
        ) == [{"n": 0}]

    def test_summaries_db_a_store_during_remove_emails_is_refused(self, stack, ws, real, thread):
        """The removal holds its locks first: the store waits for it, finds
        the thread empty and refuses (CRM09)."""
        [pending] = real.pending_threads(ws, 20)
        first = (
            f"BEGIN; SELECT crm_remove_emails({stack.q(ws)}::uuid, {stack.q(thread['box'])}::uuid, "
            "ARRAY['m1']); SELECT pg_sleep(1.5); COMMIT;"
        )
        refused: list[int] = []

        def second():
            with pytest.raises(CrmRefused) as raised:
                real.store_summary(
                    _sync(ws), _plan(thread["box"], pending["latest"], thread["links"])
                )
            refused.append(raised.value.status)

        _at_once(first, second, stack)
        assert refused == [409]
        assert _summaries(stack, thread["box"]) == []

    def test_summaries_db_purge_takes_the_records_links(self, stack, ws, owner, real, thread):
        _store(real, ws, thread)
        actor = _actor(ws, owner)
        _apply(
            stack,
            actor,
            [{"op": "soft_delete", "table": "crm_opportunities", "id": thread["deal"]}],
        )
        plan = {"items": [{"table": "crm_opportunities", "id": thread["deal"]}]}
        stack.sql(f"SELECT crm_purge({_j(stack, actor)}, {_j(stack, plan)})")
        [row] = real.summaries_for(ws, "company", thread["acme"])
        assert row["links"] == [{"record_type": "company", "record_id": thread["acme"]}]

    def test_summaries_db_merge_moves_the_links(self, stack, ws, owner, real, thread):
        _store(real, ws, thread)
        keep = _company(stack, ws, owner, "Acme Holdings")
        rows = {
            r["id"]: r
            for r in stack.sql(
                f"SELECT id, updated_at FROM crm_companies WHERE id IN ({stack.q(keep)}, "
                f"{stack.q(thread['acme'])})"
            )
        }
        plan = {
            "survivor_id": keep,
            "merged_id": thread["acme"],
            "expect": {
                "survivor_updated_at": rows[keep]["updated_at"],
                "merged_updated_at": rows[thread["acme"]]["updated_at"],
            },
            "survivor_changes": {},
        }
        stack.sql(f"SELECT crm_merge_companies({_j(stack, _actor(ws, owner))}, {_j(stack, plan)})")
        assert real.summaries_for(ws, "company", thread["acme"]) == []
        [row] = real.summaries_for(ws, "company", keep)
        assert {"record_type": "company", "record_id": keep} in row["links"]

    def test_summaries_db_deleting_the_mailbox_deletes_its_summaries(
        self, stack, ws, owner, real, thread
    ):
        _store(real, ws, thread)
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "delete", "table": "crm_mailboxes", "id": thread["box"]}],
        )
        assert _summaries(stack, thread["box"]) == []
