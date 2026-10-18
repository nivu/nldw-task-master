"""Reading thread summaries — spec 009 FR-CRMAI-SUM-08, FR-CRMAI-MCP-01
(`crm_thread_summary`), -02, FR-CRMAI-FLAG-03, FR-CRMAI-CHG-11 (trash);
contracts §1, §2.3, §3 tool 21; spec §15 "Email summaries":

- `GET /crm/email-threads/{thread_key}/summary`: any member reads it, a
  Viewer included, with its money already masked; another workspace's
  mailbox, a thread with no summary, a summary linked only to records in
  the Trash, and every thread while phase 1 is off are not found;
- the record `GET` carries the latest summaries and the open item count;
- `GET /crm/{object}/{id}/emails` carries each thread's `thread_key` and,
  while phase 1 is on, its `summary`;
- a `crm_get` result's `thread_key` resolves through `crm_thread_summary`.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, as_
from test_crm_summaries_job import ANSWER, Mail

from app.mcp import crm as tools

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
PRICED = {
    **ANSWER,
    "summary": "Priya asked about a budget of ₹4.5L for the pilot. Deepika will send the plan.",
}


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def mail(monkeypatch):
    mail = Mail(monkeypatch)
    mail.message("g1")
    mail.answer(PRICED)
    mail.tick()
    mail.key = f"{mail.mailbox['id']}:t1"
    return mail


def _summary(mail, key=None, who=OWNER, query=LABS):
    return mail.http.get(
        f"{API}/email-threads/{key or mail.key}/summary", params=query, headers=as_(who["id"])
    )


class TestTheRoute:
    """Contracts §2.3."""

    def test_routes_a_viewer_reads_the_summary(self, mail):
        r = _summary(mail, who=VIEWER)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["thread_key"] == mail.key
        assert body["subject"] == "Pilot scope"
        assert body["asks"] == ["A pilot plan for two plants"]
        assert body["commitments"][0] == {
            "text": "Send the pilot plan",
            "owner_name": "Deepika",
            "due_on": "2026-10-24",
        }
        assert body["next_step"] == "Send the pilot plan"
        assert body["sentiment"] == "positive"
        assert body["ai"] == {
            "source_kind": "email_thread",
            "model": "claude-sonnet-5-5",
            "generated_at": body["ai"]["generated_at"],
            "source": {"thread_key": mail.key},
        }
        assert {(lk["record_type"], lk["name"]) for lk in body["links"]} == {
            ("company", "Acme"),
            ("person", "Priya"),
            ("opportunity", "Acme pilot"),
        }

    def test_routes_money_in_the_summary_is_masked(self, mail):
        body = _summary(mail, who=VIEWER).json()
        assert "₹4.5L" not in body["summary"]
        assert "[amount]" in body["summary"]

    def test_routes_another_workspaces_mailbox_is_not_found(self, mail):
        r = _summary(mail, query={"workspace": "academy"})
        assert r.status_code == 404
        assert r.json()["detail"] == "No such summary."

    @pytest.mark.parametrize("key", ["nope", "not-a-uuid:t1"])
    def test_routes_a_key_that_is_not_one_is_not_found(self, mail, key):
        assert _summary(mail, key=key).status_code == 404

    def test_routes_a_thread_with_no_summary_is_not_found(self, mail):
        assert _summary(mail, key=f"{mail.mailbox['id']}:t9").status_code == 404

    def test_routes_phase_off_is_not_found(self, mail):
        mail.fake.set_ai(mail.labs, budget_usd=10, phases={"1": False})
        assert _summary(mail).status_code == 404

    def test_routes_every_linked_record_trashed_is_not_found_and_restore_shows_it(self, mail):
        for table, row in (
            ("crm_companies", mail.acme),
            ("crm_people", mail.priya),
            ("crm_opportunities", mail.pilot),
        ):
            mail.fake.rows[table][row["id"]]["deleted_at"] = "2026-10-17T00:00:00+00:00"
        assert _summary(mail).status_code == 404
        mail.fake.rows["crm_companies"][mail.acme["id"]]["deleted_at"] = None
        r = _summary(mail)
        assert r.status_code == 200
        assert [lk["record_type"] for lk in r.json()["links"]] == ["company"]


class TestTheRecord:
    """Contracts §1, FR-CRMAI-MCP-02."""

    @pytest.mark.parametrize("plural", ["companies", "people", "opportunities"])
    def test_routes_the_record_carries_its_summaries(self, mail, plural):
        record = {"companies": mail.acme, "people": mail.priya, "opportunities": mail.pilot}[plural]
        answer = mail.get(f"{plural}/{record['id']}", who=VIEWER)
        [summary] = answer["summaries"]
        assert summary["thread_key"] == mail.key
        assert answer["open_review_items"] == 0  # a Viewer has no queue

    def test_routes_open_items_counted_for_their_owner(self, mail):
        assert mail.get(f"opportunities/{mail.pilot['id']}", who=MEMBER)["open_review_items"] == 2
        assert mail.get(f"companies/{mail.acme['id']}", who=MEMBER)["open_review_items"] == 0

    def test_routes_phase_off_leaves_the_key_out(self, mail):
        mail.fake.set_ai(mail.labs, budget_usd=10, phases={"1": False})
        assert "summaries" not in mail.get(f"companies/{mail.acme['id']}")


class TestTheEmailsTab:
    """Contracts §1 `GET /crm/{object}/{id}/emails`, FR-CRMAI-SUM-08."""

    def test_routes_threads_carry_key_and_summary(self, mail):
        [thread] = mail.get(f"companies/{mail.acme['id']}/emails", who=VIEWER)["threads"]
        assert thread["thread_key"] == mail.key
        assert set(thread["summary"]) == {"summary", "asks", "commitments", "sentiment", "ai"}
        assert thread["summary"]["commitments"][0]["owner_name"] == "Deepika"

    def test_routes_phase_off_leaves_the_summary_out(self, mail):
        mail.fake.set_ai(mail.labs, budget_usd=10, phases={"1": False})
        [thread] = mail.get(f"companies/{mail.acme['id']}/emails")["threads"]
        assert thread["thread_key"] == mail.key
        assert "summary" not in thread


class TestThroughClaude:
    """FR-CRMAI-MCP-01 — `crm_get`'s thread key, through `crm_thread_summary`."""

    def test_routes_crm_get_thread_key_resolves(self, mail):
        got = asyncio.run(
            tools.crm_get(Ctx(VIEWER["id"]), "opportunity", mail.pilot["id"], workspace="labs")
        )
        [thread] = got["emails"]
        assert got["summaries"][0]["thread_key"] == thread["thread_key"]
        answer = asyncio.run(
            tools.crm_thread_summary(Ctx(VIEWER["id"]), thread["thread_key"], workspace="labs")
        )
        assert answer["summary"] == got["summaries"][0]["summary"]
