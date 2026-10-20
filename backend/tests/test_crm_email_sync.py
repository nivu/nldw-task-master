"""Gmail label sync — spec 007 FR-CRM-SYNC-01 to -10, FR-CRM-PIPE-02,
FR-CRM-AUTO-01 (automations 2 and 3), FR-CRM-FLAG-04, FR-CRM-NOTIF-01,
FR-CRM-REC-03, FR-CRM-JOB-01; Q-23, Q-62; contracts §2.10; spec §11 AC
"Email and flags" 1 to 3.

Gmail is faked (`FakeGmail`): a mailbox with labels, messages and a
history. The queue is a list the tests drain, so a backfill can be stopped
part-way and resumed; the Redis lock is a dict. Nothing here holds a real
credential, and the tick is checked never to read the key at all.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from crm_fake import (
    DEFAULTS,
    MEMBER,
    OUTSIDER,
    OWNER,
    PEOPLE,
    SELLER,
    VIEWER,
    FakeCrmStore,
    as_,
    two_workspaces,
)
from crm_fake import client as crm_client

from app.config import settings as config
from app.services.crm import email_sync, flags, gmail, jobs, notifications
from app.tasks import crm as tasks

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
#: A workspace admin of Academy who is not a TVA admin.
SANJEEV = {
    "id": "u-sanjeev",
    "email": "sanjeev@nunnari.academy",
    "display_name": "Sanjeev",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}


def _ms(at: datetime) -> str:
    return str(int(at.timestamp() * 1000))


class FakeGmail:
    """One Gmail per mailbox address: labels, messages, and a history whose
    records carry increasing ids. Spec 010: Primary, Sent, Trash and Spam
    are labels as Gmail has them, a message may carry attachment names, and
    `search` answers the deal-document queries."""

    def __init__(self) -> None:
        self.boxes: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    def box(self, address: str) -> dict[str, Any]:
        return self.boxes.setdefault(
            address,
            {
                "labels": [
                    {"id": "INBOX", "name": "INBOX"},
                    {"id": "Label_1", "name": "Sales"},
                    {"id": "Label_2", "name": "Leads"},
                    {"id": "CATEGORY_PERSONAL", "name": "CATEGORY_PERSONAL"},
                    {"id": "SENT", "name": "SENT"},
                    {"id": "TRASH", "name": "TRASH"},
                    {"id": "SPAM", "name": "SPAM"},
                ],
                "messages": {},
                "history": [],
                "history_id": 100,
            },
        )

    def send(
        self,
        address: str,
        gmail_id: str,
        *,
        sender: str,
        to: str,
        at: datetime,
        labels=None,
        subject="Proposal",
        thread="t1",
        attachments=(),
        **headers: str,
    ) -> None:
        """`labels` as given; by default Sales in the Inbox, as before."""
        box = self.box(address)
        box["messages"][gmail_id] = {
            "id": gmail_id,
            "threadId": thread,
            "labelIds": ["INBOX", "Label_1"] if labels is None else list(labels),
            "internalDate": _ms(at),
            "attachments": list(attachments),
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                *({"name": k.replace("_", "-"), "value": v} for k, v in headers.items()),
            ],
        }
        self._record(address, {"messagesAdded": [{"message": self._ref(address, gmail_id)}]})

    def _ref(self, address: str, gmail_id: str) -> dict[str, Any]:
        msg = self.box(address)["messages"][gmail_id]
        return {"id": gmail_id, "threadId": msg["threadId"], "labelIds": list(msg["labelIds"])}

    def _record(self, address: str, record: dict[str, Any]) -> None:
        box = self.box(address)
        box["history_id"] += 1
        box["history"].append({"id": str(box["history_id"]), **record})

    def unlabel(self, address: str, gmail_id: str, label: str = "Label_1") -> None:
        msg = self.box(address)["messages"][gmail_id]
        msg["labelIds"].remove(label)
        self._record(
            address,
            {"labelsRemoved": [{"message": self._ref(address, gmail_id), "labelIds": [label]}]},
        )

    def relabel(self, address: str, gmail_id: str, *, add=(), remove=()) -> None:
        """Labels added then removed, each a history record as Gmail writes
        them (the message's labels after the change)."""
        msg = self.box(address)["messages"][gmail_id]
        if add:
            msg["labelIds"] += [lb for lb in add if lb not in msg["labelIds"]]
            self._record(
                address,
                {"labelsAdded": [{"message": self._ref(address, gmail_id), "labelIds": list(add)}]},
            )
        if remove:
            msg["labelIds"] = [lb for lb in msg["labelIds"] if lb not in remove]
            self._record(
                address,
                {
                    "labelsRemoved": [
                        {"message": self._ref(address, gmail_id), "labelIds": list(remove)}
                    ]
                },
            )

    def trash(self, address: str, gmail_id: str) -> None:
        self.relabel(address, gmail_id, add=("TRASH",))

    def spam(self, address: str, gmail_id: str) -> None:
        self.relabel(address, gmail_id, add=("SPAM",))

    def untrash(self, address: str, gmail_id: str) -> None:
        self.relabel(address, gmail_id, remove=("TRASH",))

    # -- the module's reads -------------------------------------------------

    def profile(self, mailbox: str) -> dict[str, Any]:
        self.calls.append(("profile", mailbox))
        return {"emailAddress": mailbox, "historyId": str(self.box(mailbox)["history_id"])}

    def labels(self, mailbox: str) -> list[dict[str, Any]]:
        self.calls.append(("labels", mailbox))
        return self.box(mailbox)["labels"]

    def list_messages(self, mailbox, label_id, *, after, page_token, page_size):
        self.calls.append(("list", mailbox))
        found = sorted(
            (
                m
                for m in self.box(mailbox)["messages"].values()
                if label_id in m["labelIds"] and int(m["internalDate"]) // 1000 > after
            ),
            key=lambda m: m["id"],
        )
        start = int(page_token or 0)
        chunk = found[start : start + page_size]
        out: dict[str, Any] = {
            "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in chunk]
        }
        if start + page_size < len(found):
            out["nextPageToken"] = str(start + page_size)
        return out

    def history(self, mailbox, start, *, page_token, page_size):
        self.calls.append(("history", mailbox))
        box = self.box(mailbox)
        found = [h for h in box["history"] if int(h["id"]) > int(start)]
        first = int(page_token or 0)
        out: dict[str, Any] = {
            "history": found[first : first + page_size],
            "historyId": str(box["history_id"]),
        }
        if first + page_size < len(found):
            out["nextPageToken"] = str(first + page_size)
        return out

    def message(self, mailbox, gmail_id):
        self.calls.append(("message", gmail_id))
        msg = self.box(mailbox)["messages"].get(gmail_id)
        if not msg:
            return {}
        # Metadata only: attachment names are not in it (plan CT-3).
        return {k: v for k, v in msg.items() if k != "attachments"}

    @staticmethod
    def _words(text: str) -> list[str]:
        return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]

    def _names_file(self, names: list[str], term: str) -> bool:
        want = self._words(term)
        for name in names:
            words = self._words(name)
            if any(words[i : i + len(want)] == want for i in range(len(words))):
                return True
        return False

    def search(self, mailbox, q, *, page_token, page_size):
        """The deal-document queries (`gmail.search`): has:attachment,
        after:, the scope group (category:primary, in:sent, label:) and the
        filename group; never Trash or Spam."""
        self.calls.append(("search", q))
        box = self.box(mailbox)
        by_name = {lb["name"].lower(): lb["id"] for lb in box["labels"]}
        term = r'("[^"]+"|[^\s()]+)'
        files = [t.strip('"') for t in re.findall(rf"filename:{term}", q)]
        named = [by_name.get(t.strip('"').lower()) for t in re.findall(rf"label:{term}", q)]
        scope = set(filter(None, named))
        if "category:primary" in q:
            scope.add("CATEGORY_PERSONAL")
        if "in:sent" in q:
            scope.add("SENT")
        after = re.search(r"after:(\d+)", q)
        found = []
        for m in sorted(box["messages"].values(), key=lambda m: m["id"]):
            labels = set(m["labelIds"])
            if labels & {"TRASH", "SPAM"}:
                continue
            if "has:attachment" in q and not m.get("attachments"):
                continue
            if after and int(m["internalDate"]) // 1000 <= int(after.group(1)):
                continue
            if scope and not labels & scope:
                continue
            if files and not any(self._names_file(m.get("attachments") or [], f) for f in files):
                continue
            found.append(m)
        start = int(page_token or 0)
        out: dict[str, Any] = {
            "messages": [
                {"id": m["id"], "threadId": m["threadId"]} for m in found[start : start + page_size]
            ]
        }
        if start + page_size < len(found):
            out["nextPageToken"] = str(start + page_size)
        return out

    def threads_get(self, mailbox, thread_id):
        """A thread's messages' metadata, oldest first (for S2's readers)."""
        self.calls.append(("thread", thread_id))
        msgs = [
            {k: v for k, v in m.items() if k != "attachments"}
            for m in self.box(mailbox)["messages"].values()
            if m["threadId"] == thread_id
        ]
        return {"id": thread_id, "messages": sorted(msgs, key=lambda m: int(m["internalDate"]))}

    def thread_meta(self, mailbox, thread_id):
        """`gmail.thread_meta`: `{}` for a thread it does not hold, as a 404."""
        found = self.threads_get(mailbox, thread_id)
        return found if found["messages"] else {}

    def install(self, monkeypatch) -> FakeGmail:
        for name in (
            "profile",
            "labels",
            "list_messages",
            "history",
            "message",
            "search",
            "thread_meta",
        ):
            monkeypatch.setattr(gmail, name, getattr(self, name))
        return self


class Queue:
    """The crm_sync queue and the Redis lock."""

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.lock: str | None = None
        self.inline = False

    def enqueue(self, args: dict[str, Any]) -> None:
        if self.inline:
            email_sync.page(args, NOW)
        else:
            self.tasks.append(args)

    def acquire(self, token: str) -> bool:
        if self.lock is not None:
            return False
        self.lock = token
        return True

    def release(self, token: str) -> None:
        if self.lock == token:
            self.lock = None

    def drain(self, limit: int = 100) -> int:
        n = 0
        while self.tasks and n < limit:
            email_sync.page(self.tasks.pop(0), NOW)
            n += 1
        return n


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_member(academy, SANJEEV["id"], "admin")
    http = crm_client(monkeypatch, {**PEOPLE, SANJEEV["id"]: SANJEEV}, fake=fake)
    for ws in (labs, academy):
        fake.add(
            "crm_workspace_settings",
            workspace_id=ws["id"],
            key="email_sync",
            value={"enabled": ws is labs},
        )
        fake.add(
            "crm_select_options",
            workspace_id=ws["id"],
            list_key="source",
            system_key="email_sync",
            label="Email sync",
            color=None,
            position=1,
            is_default=False,
            is_active=True,
            is_fixed=False,
        )
    monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
    for module in (email_sync, flags, jobs, notifications):
        monkeypatch.setattr(module, "_now", lambda: NOW)
    sent: list = []
    monkeypatch.setattr(notifications, "deliver", lambda message: sent.append(message) or ["email"])
    queue = Queue()
    monkeypatch.setattr(email_sync, "enqueue", queue.enqueue)
    monkeypatch.setattr(email_sync, "acquire_lock", queue.acquire)
    monkeypatch.setattr(email_sync, "release_lock", queue.release)
    monkeypatch.setattr(email_sync, "extend_lock", lambda token: None)
    mail = FakeGmail().install(monkeypatch)
    mailbox = fake.add(
        "crm_mailboxes",
        workspace_id=labs["id"],
        email="owner@nunnarilabs.com",
        owner_id=OWNER["id"],
        labels=["Sales"],
        **DEFAULTS["crm_mailboxes"],
    )
    return http, fake, labs, academy, mail, queue, mailbox, sent


def _company(fake, ws, name, domain, **values):
    company = fake.add(
        "crm_companies",
        **{
            **DEFAULTS["crm_companies"],
            "workspace_id": ws["id"],
            "name": name,
            "owner_id": SELLER["id"],
            "created_at": "2026-10-01T00:00:00+00:00",
            **values,
        },
    )
    fake.add(
        "crm_company_domains",
        workspace_id=ws["id"],
        company_id=company["id"],
        domain=domain,
        is_primary=True,
    )
    return company


def _person(fake, ws, first, email, company=None, **values):
    return fake.add(
        "crm_people",
        **{
            **DEFAULTS["crm_people"],
            "workspace_id": ws["id"],
            "first_name": first,
            "primary_email": email,
            "company_id": company and company["id"],
            "owner_id": SELLER["id"],
            "created_at": "2026-10-01T00:00:00+00:00",
            **values,
        },
    )


def _sync(queue) -> dict:
    result = email_sync.tick(NOW)
    queue.drain()
    return result


def _sync_at(queue, monkeypatch, at: datetime) -> dict:
    """A run at another time than NOW; the clocks go back to NOW after."""
    for module in (email_sync, flags, jobs, notifications):
        monkeypatch.setattr(module, "_now", lambda: at)
    result = email_sync.tick(at)
    while queue.tasks:
        email_sync.page(queue.tasks.pop(0), at)
    for module in (email_sync, flags, jobs, notifications):
        monkeypatch.setattr(module, "_now", lambda: NOW)
    return result


def fake_ws(fake, key: str) -> dict:
    return next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == key)


def _messages(fake) -> list[dict]:
    return list(fake.rows["crm_email_messages"].values())


def _links(fake, message: dict) -> set[tuple[str, str]]:
    return {
        (link["record_type"], link["record_id"])
        for link in fake.rows["crm_email_links"].values()
        if link["message_id"] == message["id"]
    }


def _runs(fake) -> list[dict]:
    """The runs that ran (Academy's switch is off: its rows are skips)."""
    return [
        r
        for r in fake.rows["crm_job_runs"].values()
        if r["job"] == "email_sync" and r["outcome"] != "skipped"
    ]


class TestALabelledMessage:
    """FR-CRM-SYNC-05, -06; AC email 1."""

    def test_stored_on_the_right_person_and_company(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        priya = _person(fake, labs, "Priya", "priya@acme.com", acme)
        mail.send(
            mailbox["email"],
            "m1",
            sender="Priya <priya@acme.com>",
            to="owner@nunnarilabs.com",
            at=NOW - timedelta(hours=2),
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert msg["direction"] == "inbound"
        assert msg["subject"] == "Proposal"
        assert "body" not in msg  # Q-29: metadata only
        assert _links(fake, msg) == {("person", priya["id"]), ("company", acme["id"])}
        at = (NOW - timedelta(hours=2)).isoformat()
        for row in (fake.rows["crm_people"][priya["id"]], fake.rows["crm_companies"][acme["id"]]):
            assert row["last_inbound_at"] == at
            assert row["last_contact_at"] == at
            assert row["last_outbound_at"] is None
        # Written as the sync, so audited (FR-CRM-AUD-02).
        assert any(a["actor_label"] == "sync" and a["target_id"] == acme["id"] for a in fake.audit)
        [run] = _runs(fake)
        assert run["outcome"] == "ok"
        assert run["counts"]["stored"] == 1 and run["counts"]["matched_people"] == 1

    def test_a_person_matched_by_an_additional_email(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        priya = _person(
            fake, labs, "Priya", "priya@acme.com", additional_emails=["p.raman@acme.io"]
        )
        mail.send(mailbox["email"], "m1", sender="p.raman@acme.io", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert ("person", priya["id"]) in _links(fake, _messages(fake)[0])

    def test_a_deal_on_the_company_is_linked_while_open(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com", lead_stage="deal_open")
        open_deal = fake.add(
            "crm_opportunities",
            **{
                **DEFAULTS["crm_opportunities"],
                "workspace_id": labs["id"],
                "name": "Pilot",
                "company_id": acme["id"],
                "owner_id": SELLER["id"],
                "stage": "discovery",
            },
        )
        fake.add(
            "crm_opportunities",
            **{
                **DEFAULTS["crm_opportunities"],
                "workspace_id": labs["id"],
                "name": "Old",
                "company_id": acme["id"],
                "owner_id": SELLER["id"],
                "stage": "closed_won",
            },
        )
        mail.send(mailbox["email"], "m1", sender="ravi@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        links = _links(fake, _messages(fake)[0])
        assert ("opportunity", open_deal["id"]) in links
        assert len([lk for lk in links if lk[0] == "opportunity"]) == 1

    def test_internal_only_is_skipped(self, crm):
        """FR-CRM-SYNC-05: every participant in our domains — not stored."""
        _, fake, _, _, mail, queue, mailbox, _ = crm
        mail.send(
            mailbox["email"], "m1", sender="deepika@nunnarilabs.com", to=mailbox["email"], at=NOW
        )
        _sync(queue)
        assert _messages(fake) == []
        assert _runs(fake)[0]["counts"]["skipped_internal"] == 1

    def test_a_second_run_stores_nothing(self, crm):
        """FR-CRM-SYNC-07; AC email 2."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _company(fake, labs, "Acme", "acme.com")
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        audit_before = len(fake.audit)
        _sync(queue)
        assert len(_messages(fake)) == 1
        second = sorted(_runs(fake), key=lambda r: r["started_at"])[-1]
        assert second["counts"].get("stored", 0) == 0
        assert len(fake.audit) == audit_before

    def test_last_values_never_move_back(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        later = (NOW + timedelta(hours=1)).isoformat()
        acme = _company(fake, labs, "Acme", "acme.com", last_inbound_at=later)
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert fake.rows["crm_companies"][acme["id"]]["last_inbound_at"] == later


class TestCreating:
    """Automation 2; AC email 3."""

    def test_an_unknown_domain_creates_a_company_at_new_owned_by_the_mailbox_owner(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        mail.send(
            mailbox["email"],
            "m1",
            sender="Li Wei <li.wei@globex.co.uk>",
            to=mailbox["email"],
            at=NOW,
        )
        _sync(queue)
        [company] = fake.rows["crm_companies"].values()
        assert company["name"] == "Globex"
        assert company["lead_stage"] == "new"
        assert company["owner_id"] == mailbox["owner_id"]
        source = fake.rows["crm_select_options"][company["source_id"]]
        assert source["system_key"] == "email_sync"
        [domain] = fake.rows["crm_company_domains"].values()
        assert (domain["domain"], domain["company_id"]) == ("globex.co.uk", company["id"])
        [person] = fake.rows["crm_people"].values()
        assert (person["first_name"], person["last_name"]) == ("Li", "Wei")
        assert person["company_id"] == company["id"]
        assert person["owner_id"] == mailbox["owner_id"]
        assert _links(fake, _messages(fake)[0]) == {
            ("person", person["id"]),
            ("company", company["id"]),
        }
        run = _runs(fake)[0]["counts"]
        assert (run["created_companies"], run["created_people"]) == (1, 1)

    def test_a_webmail_address_makes_a_person_with_no_company(self, crm):
        _, fake, _, _, mail, queue, mailbox, _ = crm
        mail.send(mailbox["email"], "m1", sender="ravi.k@gmail.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert fake.rows["crm_companies"] == {}
        [person] = fake.rows["crm_people"].values()
        assert person["company_id"] is None

    def test_switched_off_creates_nothing_but_still_stores(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        fake.add(
            "crm_workspace_settings",
            workspace_id=labs["id"],
            key="automations",
            value={"email_records": False},
        )
        mail.send(mailbox["email"], "m1", sender="li@globex.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert fake.rows["crm_companies"] == {} and fake.rows["crm_people"] == {}
        assert len(_messages(fake)) == 1

    def test_a_hyphen_edged_domain_is_kept_but_matched_to_nobody(self, crm):
        """029's domain CHECK would refuse it, and with it the whole page."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        before = len(fake.rows["crm_companies"]), len(fake.rows["crm_people"])
        mail.send(mailbox["email"], "m1", sender="x@acme-.com", to=mailbox["email"], at=NOW)
        mail.send(mailbox["email"], "m2", sender="y@-acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        [run] = _runs(fake)
        assert run["outcome"] == "ok"
        stored = sorted(_messages(fake), key=lambda m: m["gmail_message_id"])
        assert [m["gmail_message_id"] for m in stored] == ["m1", "m2"]
        assert {p["email"] for p in stored[0]["participants"]} >= {"x@acme-.com"}
        assert all(_links(fake, m) == set() for m in stored)
        assert (len(fake.rows["crm_companies"]), len(fake.rows["crm_people"])) == before


class TestNewToContacted:
    """FR-CRM-PIPE-02, automation 3 — not switchable."""

    def test_the_first_outbound_email(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        mail.send(mailbox["email"], "m1", sender=mailbox["email"], to="priya@acme.com", at=NOW)
        _sync(queue)
        row = fake.rows["crm_companies"][acme["id"]]
        assert row["lead_stage"] == "contacted"
        assert row["last_outbound_at"] == NOW.isoformat()

    def test_an_inbound_one_does_not(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert fake.rows["crm_companies"][acme["id"]]["lead_stage"] == "new"


class TestLabelRemoval:
    """FR-CRM-SYNC-09."""

    def test_removes_the_copy_and_its_flag(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        mail.send(
            mailbox["email"],
            "m1",
            sender="priya@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(hours=30),
        )
        _sync(queue)
        company = fake.rows["crm_companies"][acme["id"]]
        assert company["flag_level"] == "red"
        assert [f["kind"] for f in company["flags"]] == ["reply_owed"]

        mail.unlabel(mailbox["email"], "m1")
        _sync(queue)
        assert _messages(fake) == []
        assert fake.rows["crm_email_links"] == {}
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "none"
        latest = sorted(_runs(fake), key=lambda r: r["started_at"])[-1]
        assert latest["counts"]["removed"] == 1

    def test_a_message_labelled_later_arrives_through_history(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _company(fake, labs, "Acme", "acme.com")
        _sync(queue)  # the backfill: nothing yet
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["backfilled_at"] == NOW.isoformat()
        mail.send(mailbox["email"], "m2", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m2"]

    def test_a_re_read_after_history_is_gone_removes_what_lost_its_label(self, crm, monkeypatch):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        mail.send(
            mailbox["email"],
            "m1",
            sender="priya@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(hours=30),
        )
        mail.send(
            mailbox["email"],
            "m2",
            sender=mailbox["email"],
            to="priya@acme.com",
            at=NOW - timedelta(hours=40),
            thread="t2",
        )
        _sync(queue)
        assert len(_messages(fake)) == 2
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "red"
        # m1 loses its label while Gmail's history is too old to say so.
        mail.unlabel(mailbox["email"], "m1")
        real_history = mail.history

        def gone(*args, **kwargs):
            monkeypatch.setattr(gmail, "history", real_history)
            raise gmail.HistoryGone("Gmail history is too old (404).")

        monkeypatch.setattr(gmail, "history", gone)
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m2"]
        # Its Reply owed went with it.
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "none"
        latest = sorted(_runs(fake), key=lambda r: r["started_at"])[-1]
        assert latest["outcome"] == "ok" and latest["counts"]["removed"] == 1
        box = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert box["backfilled_at"] and box["history_id"]

    def test_changing_the_labels_removes_what_only_the_old_one_carried(self, crm):
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        mail.send(
            mailbox["email"],
            "m2",
            sender="li@globex.com",
            to=mailbox["email"],
            at=NOW,
            labels=("Label_1", "Label_2"),
            thread="t2",
        )
        _sync(queue)
        assert len(_messages(fake)) == 2
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"labels": ["Leads"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m2"]

    def test_a_first_backfill_lists_nothing_twice(self, crm):
        """Nothing was stored before it, so there is nothing to remove."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert [c for c in mail.calls if c[0] == "list"] == [("list", mailbox["email"])]


class TestBackfill:
    """FR-CRM-SYNC-04, -08 — page-sized tasks, resumable."""

    def test_resumes_after_an_interruption(self, crm, monkeypatch):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        monkeypatch.setattr(email_sync, "PAGE_SIZE", 2)
        _company(fake, labs, "Acme", "acme.com")
        for n in range(5):
            mail.send(
                mailbox["email"],
                f"m{n}",
                sender="priya@acme.com",
                to=mailbox["email"],
                at=NOW - timedelta(days=n + 1),
            )
        # Older than 90 days: not read.
        mail.send(
            mailbox["email"],
            "old",
            sender="priya@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(days=91),
        )
        email_sync.tick(NOW)
        assert queue.drain(limit=1) == 1  # one page, then the worker stops
        assert len(_messages(fake)) == 2
        cursor = fake.rows["crm_mailboxes"][mailbox["id"]]["page_token"]
        assert cursor and '"phase": "backfill"' in cursor
        queue.tasks.clear()  # the rest of the run is lost
        queue.lock = None  # and its lock lapsed
        _sync(queue)  # the next tick resumes from the stored cursor
        assert sorted(m["gmail_message_id"] for m in _messages(fake)) == [
            "m0",
            "m1",
            "m2",
            "m3",
            "m4",
        ]
        box = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert box["backfilled_at"] == NOW.isoformat() and box["page_token"] is None
        assert box["history_id"]

    def test_history_is_read_in_page_sized_pieces(self, crm, monkeypatch):
        """FR-CRM-SYNC-04 — a bulk relabel never makes one task of it."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        monkeypatch.setattr(email_sync, "PAGE_SIZE", 2)
        _sync(queue)  # the backfill: nothing yet
        for n in range(5):
            mail.send(
                mailbox["email"], f"m{n}", sender="priya@acme.com", to=mailbox["email"], at=NOW
            )
        email_sync.tick(NOW)
        assert queue.drain(limit=1) == 1
        assert len(_messages(fake)) == 2
        queue.drain()
        assert len(_messages(fake)) == 5
        assert len([c for c in mail.calls if c[0] == "history"]) == 3

    def test_the_last_page_releases_the_lock(self, crm):
        *_, queue, _, _ = crm
        _sync(queue)
        assert queue.lock is None


class TestTheTick:
    """FR-CRM-SYNC-03, -04, Q-23 — a run or a recorded skip per workspace."""

    def test_skipped_where_the_switch_is_off(self, crm):
        _, fake, labs, academy, *_ = crm
        result = email_sync.tick(NOW)
        assert result["runs"]["academy"]["outcome"] == "skipped"
        assert result["runs"]["academy"]["counts"] == {"skipped": "Email sync is off."}

    def test_skipped_when_not_configured_and_nothing_else_happens(self, crm, monkeypatch):
        _, fake, _, _, mail, queue, _, _ = crm
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        result = tasks.email_sync_tick()
        assert {r["outcome"] for r in result["runs"].values()} == {"skipped"}
        assert result["runs"]["labs"]["counts"] == {
            "skipped": "The Gmail credential is not configured."
        }
        assert queue.tasks == [] and queue.lock is None and mail.calls == []

    def test_skipped_while_another_run_holds_the_lock(self, crm):
        _, fake, _, _, mail, queue, _, _ = crm
        queue.lock = "another-run"
        result = email_sync.tick(NOW)
        assert result["runs"]["labs"]["counts"] == {"skipped": "A sync is already running."}
        assert queue.tasks == [] and mail.calls == []

    def test_the_tick_never_reads_the_key(self, crm, monkeypatch):
        """Plan §2.2: the shared worker does not hold the key; the tick reads
        the non-secret flag only."""
        *_, queue, _, _ = crm

        def no(*args, **kwargs):
            raise AssertionError("the tick read the Gmail key")

        monkeypatch.setattr(gmail, "_key", no)
        monkeypatch.setattr(gmail, "token", no)
        email_sync.tick(NOW)
        assert len(queue.tasks) == 1

    def test_a_failing_page_fails_its_run_and_releases_the_lock(self, crm, monkeypatch):
        _, fake, *_, queue, _, _ = crm

        def broken(mailbox):
            raise gmail.GmailError("Gmail labels failed (500).")

        monkeypatch.setattr(gmail, "labels", broken)
        _sync(queue)
        [run] = _runs(fake)
        assert run["outcome"] == "failed"
        assert run["error_public"] == "Email sync failed (GmailError)."
        assert queue.lock is None

    def test_the_page_worker_needs_no_configured_flag(self, crm, monkeypatch):
        """Plan §2.2: worker-crm-sync holds the key and not the flag."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        email_sync.tick(NOW)
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        queue.drain()
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m1"]
        [run] = _runs(fake)
        assert run["outcome"] == "ok" and run["counts"]["stored"] == 1

    def test_a_page_still_stops_when_the_switch_goes_off(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        email_sync.tick(NOW)
        setting = next(
            r
            for r in fake.rows["crm_workspace_settings"].values()
            if r["workspace_id"] == labs["id"] and r["key"] == "email_sync"
        )
        setting["value"] = {"enabled": False}
        queue.drain()
        assert _messages(fake) == [] and mail.calls == []

    def test_a_mailbox_whose_owner_left_is_skipped(self, crm):
        """FR-CRM-SYNC-01 — the owner must be a member while it is read."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        fake.rows["crm_mailboxes"][mailbox["id"]]["owner_id"] = SELLER["id"]
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        membership = next(
            m
            for m in fake.rows["crm_memberships"].values()
            if m["workspace_id"] == labs["id"] and m["user_id"] == SELLER["id"]
        )
        del fake.rows["crm_memberships"][membership["id"]]
        _sync(queue)
        assert _messages(fake) == [] and mail.calls == []
        [run] = _runs(fake)
        assert run["outcome"] == "ok" and run["counts"]["mailboxes"] == 0

    def test_removing_the_owner_pauses_their_mailbox(self, crm):
        """040 — crm_remove_membership pauses the person's mailboxes there."""
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        fake.rows["crm_mailboxes"][mailbox["id"]]["owner_id"] = SELLER["id"]
        membership = next(
            m
            for m in fake.rows["crm_memberships"].values()
            if m["workspace_id"] == labs["id"] and m["user_id"] == SELLER["id"]
        )
        r = http.delete(f"/api/v1/crm/members/{membership['id']}{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code in (200, 204), r.text
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["is_active"] is False
        assert any(
            a["action"] == "mailbox.updated" and a["target_id"] == mailbox["id"] for a in fake.audit
        )

    def test_the_tick_runs_on_the_default_queue_and_pages_on_crm_sync(self):
        from app.worker import CRM_SYNC_QUEUE, celery_app

        assert CRM_SYNC_QUEUE == "crm_sync"
        assert celery_app.conf.task_routes["crm.email_sync_page"] == {"queue": "crm_sync"}
        assert "crm.email_sync_tick" not in celery_app.conf.task_routes
        page = celery_app.tasks["crm.email_sync_page"]
        assert (page.soft_time_limit, page.time_limit) == (240, 300)
        beat = celery_app.conf.beat_schedule["crm-email-sync-tick"]
        assert beat["task"] == "crm.email_sync_tick"
        assert "email_sync" in jobs.JOBS


class TestReplyOwedNotice:
    """FR-CRM-NOTIF-01 — reply owed turned red tells the owner, once."""

    @staticmethod
    def _notices(fake) -> list[dict]:
        return [n for n in fake.rows["crm_notifications"].values() if n["kind"] == "reply_owed_red"]

    def test_once_when_it_turns_red_after_the_backfill(self, crm, monkeypatch):
        _, fake, labs, _, mail, queue, mailbox, sent = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        # Backfilled ten hours ago, when it had waited 20 hours: amber.
        mail.send(
            mailbox["email"],
            "m1",
            sender="priya@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(hours=30),
        )
        _sync_at(queue, monkeypatch, NOW - timedelta(hours=10))
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "amber"
        assert self._notices(fake) == []
        # The 30-minute flags turn it red; the next runs tell the owner once.
        flags.recompute_workspace(labs["id"], NOW)
        _sync(queue)
        _sync(queue)
        notices = self._notices(fake)
        assert [
            (n["user_id"], n["record_id"]) for n in notices if n["record_id"] == acme["id"]
        ] == [(SELLER["id"], acme["id"])]
        assert all("₹" not in n["title"] for n in notices)

    def test_a_backfill_of_an_old_unanswered_email_tells_nobody(self, crm):
        """FR-CRM-NOTIF-01's event is a reply owed turning red, not one that
        was red before the mailbox was connected."""
        _, fake, labs, _, mail, queue, mailbox, sent = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        mail.send(
            mailbox["email"],
            "m1",
            sender="priya@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(days=30),
        )
        _sync(queue)
        _sync(queue)
        assert fake.rows["crm_companies"][acme["id"]]["flag_level"] == "red"
        assert self._notices(fake) == [] and sent == []


class TestMailboxes:
    """FR-CRM-SYNC-01, amended; contracts §2.10."""

    def _add(self, http, who, body, ws=LABS):
        return http.post(f"/api/v1/crm/mailboxes{ws}", json=body, headers=as_(who))

    def test_the_owners_own_address(self, crm):
        http, fake, *_ = crm
        r = self._add(
            http,
            SANJEEV["id"],
            {
                "email": "sanjeev@nunnari.academy",
                "owner_id": SANJEEV["id"],
                "labels": ["academy/course"],
            },
            ACADEMY,
        )
        assert r.status_code == 201, r.text
        assert r.json()["labels"] == ["academy/course"]
        assert any(a["action"] == "mailbox.created" for a in fake.audit)

    def test_the_owner_must_be_a_member(self, crm):
        http, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {"email": "vinita@nunnarilabs.com", "owner_id": OUTSIDER["id"], "labels": ["Sales"]},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "The mailbox owner must be a member of this workspace."

    def test_another_address_is_refused_for_a_workspace_admin(self, crm):
        http, *_ = crm
        r = self._add(
            http,
            SANJEEV["id"],
            {"email": "info@nunnari.academy", "owner_id": SANJEEV["id"], "labels": ["Leads"]},
            ACADEMY,
        )
        assert r.status_code == 422
        assert "owner's own email address" in r.json()["detail"]

    def test_and_allowed_for_a_tva_admin(self, crm):
        """The owner is a TVA admin: a delegate address such as
        navaneeth@deepweaver.ai."""
        http, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {"email": "navaneeth@deepweaver.ai", "owner_id": OWNER["id"], "labels": ["Sales"]},
        )
        assert r.status_code == 201, r.text

    def test_outside_the_service_accounts_domains(self, crm):
        http, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {"email": "owner@gmail.com", "owner_id": OWNER["id"], "labels": ["Sales"]},
        )
        assert r.status_code == 422
        assert "nunnari.academy" in r.json()["detail"]

    @pytest.mark.parametrize("label", ["INBOX", "SENT", "CATEGORY_PROMOTIONS"])
    def test_a_system_label(self, crm, label):
        http, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {"email": "navaneeth@deepweaver.ai", "owner_id": OWNER["id"], "labels": [label]},
        )
        assert r.status_code == 422
        assert "system label" in r.json()["detail"]

    def test_a_workspace_admin_may_pause_and_relabel_a_delegate_mailbox(self, crm):
        """FR-CRM-SYNC-01 limits who registers a delegate address, not who
        pauses it or changes its labels."""
        http, fake, labs, academy, *_ = crm
        delegate = fake.add(
            "crm_mailboxes",
            workspace_id=academy["id"],
            email="info@nunnari.academy",
            owner_id=SANJEEV["id"],
            labels=["Leads"],
            **DEFAULTS["crm_mailboxes"],
        )
        url = f"/api/v1/crm/mailboxes/{delegate['id']}{ACADEMY}"
        for body in ({"is_active": False}, {"labels": ["Leads", "Sales"]}, {"is_active": True}):
            r = http.patch(url, json=body, headers=as_(SANJEEV["id"]))
            assert r.status_code == 200, r.text
        row = fake.rows["crm_mailboxes"][delegate["id"]]
        assert row["labels"] == ["Leads", "Sales"] and row["is_active"] is True
        # Another address still needs a TVA admin.
        r = http.patch(url, json={"email": "hello@nunnari.academy"}, headers=as_(SANJEEV["id"]))
        assert r.status_code == 422
        assert "owner's own email address" in r.json()["detail"]

    def test_an_address_registered_in_the_other_workspace(self, crm):
        """One address, one workspace: Labs has owner@nunnarilabs.com."""
        http, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {"email": "owner@nunnarilabs.com", "owner_id": OWNER["id"], "labels": ["Leads"]},
            ACADEMY,
        )
        assert r.status_code == 409
        assert "already registered" in r.json()["detail"]

    @pytest.mark.parametrize("who", [MEMBER["id"], VIEWER["id"]])
    def test_admins_only(self, crm, who):
        http, *_ = crm
        assert http.get(f"/api/v1/crm/mailboxes{LABS}", headers=as_(who)).status_code == 403

    def test_changing_the_labels_reads_them_afresh(self, crm):
        http, fake, *_, mailbox, _ = crm
        fake.rows["crm_mailboxes"][mailbox["id"]].update(
            history_id="123", backfilled_at=NOW.isoformat()
        )
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"labels": ["Sales", "Leads"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        row = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert row["labels"] == ["Sales", "Leads"]
        assert row["history_id"] is None and row["backfilled_at"] is None

    def test_removing_a_mailbox_removes_its_messages(self, crm):
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        mail.send(mailbox["email"], "m1", sender="priya@acme.com", to=mailbox["email"], at=NOW)
        _sync(queue)
        assert _messages(fake)
        r = http.delete(f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200
        assert _messages(fake) == [] and fake.rows["crm_email_links"] == {}

    def test_the_list_says_whether_sync_can_run(self, crm):
        http, *_ = crm
        body = http.get(f"/api/v1/crm/mailboxes{LABS}", headers=as_(OWNER["id"])).json()
        assert body["sync"] == {"enabled": True, "configured": True, "live": True}
        assert [m["email"] for m in body["mailboxes"]] == ["owner@nunnarilabs.com"]

    # -- Spec 010 FR-CAP-SRC-01, -07; contracts §1.1; plan §5.3, PD-3 --------

    def test_capture_fields_are_listed_with_their_defaults(self, crm):
        http, *_ = crm
        [row] = http.get(f"/api/v1/crm/mailboxes{LABS}", headers=as_(OWNER["id"])).json()[
            "mailboxes"
        ]
        assert row["capture_source"] == "labels"
        assert row["capture_start_date"] == "2026-04-01"
        assert row["docs_checked_at"] is None

    def test_capture_a_new_mailbox_defaults_to_labels_only(self, crm):
        http, fake, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {"email": "navaneeth@deepweaver.ai", "owner_id": OWNER["id"], "labels": ["Sales"]},
        )
        assert r.status_code == 201, r.text
        assert r.json()["capture_source"] == "labels"
        assert r.json()["capture_start_date"] == "2026-04-01"

    def test_capture_a_new_mailbox_may_capture_primary_and_sent(self, crm):
        http, fake, *_ = crm
        r = self._add(
            http,
            OWNER["id"],
            {
                "email": "navaneeth@deepweaver.ai",
                "owner_id": OWNER["id"],
                "labels": ["Sales"],
                "capture_source": "primary_sent",
                "capture_start_date": "2026-05-01",
            },
        )
        assert r.status_code == 201, r.text
        row = fake.rows["crm_mailboxes"][r.json()["id"]]
        assert row["capture_source"] == "primary_sent"
        assert row["capture_start_date"] == "2026-05-01"

    def test_capture_a_start_date_in_the_future_is_refused(self, crm):
        http, *_, mailbox, _ = crm
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_start_date": "2099-01-01"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "The capture start date cannot be in the future."
        r = self._add(
            http,
            OWNER["id"],
            {
                "email": "navaneeth@deepweaver.ai",
                "owner_id": OWNER["id"],
                "labels": ["Sales"],
                "capture_start_date": "2099-01-01",
            },
        )
        assert r.status_code == 422

    def test_capture_an_unknown_source_is_refused(self, crm):
        http, *_, mailbox, _ = crm
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_source": "everything"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("key", ["capture_source", "capture_start_date"])
    def test_capture_fields_cannot_be_emptied(self, crm, key):
        http, *_, mailbox, _ = crm
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={key: None},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"].endswith("cannot be empty.")

    @pytest.mark.parametrize(
        "body", [{"capture_source": "primary_sent"}, {"capture_start_date": "2026-06-01"}]
    )
    def test_capture_a_change_reads_the_mailbox_afresh(self, crm, body):
        http, fake, *_, mailbox, _ = crm
        fake.rows["crm_mailboxes"][mailbox["id"]].update(
            history_id="123", backfilled_at=NOW.isoformat(), page_token='{"phase": "history"}'
        )
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}", json=body, headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert r.json()["backfilled_at"] is None
        row = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert row["history_id"] is None and row["page_token"] is None
        assert row["reconcile_after"] is None
        # Audited as an Admin's settings change.
        [change] = [
            a
            for a in fake.audit
            if a["target_id"] == mailbox["id"] and a["action"] == "mailbox.updated"
        ]
        assert set(body) <= set(change["after"])

    def test_capture_switching_back_reconciles_from_the_old_start_date(self, crm):
        http, fake, *_, mailbox, _ = crm
        fake.rows["crm_mailboxes"][mailbox["id"]].update(
            capture_source="primary_sent", capture_start_date="2026-05-01"
        )
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_source": "labels"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        row = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert row["capture_source"] == "labels"
        assert row["reconcile_after"] == "2026-05-01T00:00:00+05:30"
        assert row["backfilled_at"] is None

    def test_capture_the_same_values_change_nothing(self, crm):
        http, fake, *_, mailbox, _ = crm
        fake.rows["crm_mailboxes"][mailbox["id"]].update(history_id="123")
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_source": "labels", "capture_start_date": "2026-04-01"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["history_id"] == "123"

    @pytest.mark.parametrize("who", [MEMBER["id"], VIEWER["id"]])
    def test_capture_only_admins_change_the_source(self, crm, who):
        http, *_, mailbox, _ = crm
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_source": "primary_sent"},
            headers=as_(who),
        )
        assert r.status_code == 403


class TestTheSwitch:
    """Q-23 — it cannot go on before the credential exists."""

    def test_refused_while_not_configured(self, crm, monkeypatch):
        http, *_ = crm
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        r = http.patch(
            f"/api/v1/crm/settings/email_sync{ACADEMY}",
            json={"value": {"enabled": True}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert "credential" in r.json()["detail"]

    def test_on_once_configured_and_off_any_time(self, crm, monkeypatch):
        http, *_ = crm
        for value in (True, False):
            r = http.patch(
                f"/api/v1/crm/settings/email_sync{ACADEMY}",
                json={"value": {"enabled": value}},
                headers=as_(OWNER["id"]),
            )
            assert r.status_code == 200, r.text

    def test_bootstrap_says_whether_it_is_live(self, crm, monkeypatch):
        http, *_ = crm
        on = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert on["email_sync_on"] is True
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        off = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert off["email_sync_on"] is False


class TestRunNow:
    """Q-62 — Admins only; contracts §2.10."""

    def test_an_admin_runs_it_and_gets_the_counts(self, crm):
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        queue.inline = True
        mail.send(mailbox["email"], "m1", sender="li@globex.com", to=mailbox["email"], at=NOW)
        r = http.post(f"/api/v1/crm/email-sync/run{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outcome"] == "ok"
        assert (body["mailboxes"], body["stored"], body["created_companies"]) == (1, 1, 1)
        again = http.post(f"/api/v1/crm/email-sync/run{LABS}", headers=as_(OWNER["id"])).json()
        assert again["stored"] == 0

    @pytest.mark.parametrize("who", [MEMBER["id"], VIEWER["id"], SELLER["id"]])
    def test_nobody_else(self, crm, who):
        http, *_ = crm
        r = http.post(f"/api/v1/crm/email-sync/run{LABS}", headers=as_(who))
        assert r.status_code == 403

    def test_off(self, crm):
        http, *_ = crm
        r = http.post(f"/api/v1/crm/email-sync/run{ACADEMY}", headers=as_(OWNER["id"]))
        assert r.json() == {"skipped": "Email sync is off"}

    def test_not_configured_says_so(self, crm, monkeypatch):
        http, *_ = crm
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        r = http.post(f"/api/v1/crm/email-sync/run{LABS}", headers=as_(OWNER["id"]))
        assert r.json() == {"skipped": "The Gmail credential is not configured"}

    def test_a_failed_start_releases_the_lock(self, crm, monkeypatch):
        http, fake, *_, queue, _, _ = crm

        def broken(*args, **kwargs):
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(email_sync.store, "list_mailboxes", broken)
        with pytest.raises(RuntimeError):
            email_sync.tick(NOW)
        assert queue.lock is None
        with pytest.raises(RuntimeError):
            email_sync.run_now(SimpleNamespace(workspace=fake_ws(fake, "labs")))
        assert queue.lock is None

    def test_already_running(self, crm):
        http, *_, queue, _, _ = crm
        queue.lock = "another-run"
        r = http.post(f"/api/v1/crm/email-sync/run{LABS}", headers=as_(OWNER["id"]))
        assert r.json() == {"skipped": "A sync is already running"}


class TestEmailsTab:
    """FR-CRM-REC-03 — a company shows all its people's threads; a deal its
    company's and its contacts'. No bodies."""

    def test_company_person_and_deal(self, crm):
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com", lead_stage="deal_open")
        priya = _person(fake, labs, "Priya", "priya@acme.com", acme)
        mail.send(
            mailbox["email"],
            "m1",
            sender="priya@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(hours=3),
            thread="t1",
        )
        mail.send(
            mailbox["email"],
            "m2",
            sender=mailbox["email"],
            to="priya@acme.com",
            at=NOW - timedelta(hours=1),
            thread="t1",
            subject="Re: Proposal",
        )
        mail.send(
            mailbox["email"],
            "m3",
            sender="ravi@acme.com",
            to=mailbox["email"],
            at=NOW - timedelta(hours=2),
            thread="t2",
            subject="Intro",
        )
        _sync(queue)
        deal = fake.add(
            "crm_opportunities",
            **{
                **DEFAULTS["crm_opportunities"],
                "workspace_id": labs["id"],
                "name": "Pilot",
                "company_id": acme["id"],
                "owner_id": SELLER["id"],
                "point_of_contact_id": priya["id"],
                "stage": "discovery",
                "probability": 10,
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
        )
        company = http.get(
            f"/api/v1/crm/companies/{acme['id']}/emails{LABS}", headers=as_(VIEWER["id"])
        )
        assert company.status_code == 200, company.text
        threads = company.json()["threads"]
        assert [(t["thread_id"], t["subject"], t["direction"], t["messages"]) for t in threads] == [
            ("t1", "Re: Proposal", "outbound", 2),
            ("t2", "Intro", "inbound", 1),
        ]
        assert threads[0]["mailbox"] == mailbox["email"]
        assert {p["email"] for p in threads[0]["participants"]} == {
            "priya@acme.com",
            mailbox["email"],
        }
        assert all("body" not in t for t in threads)
        person = http.get(
            f"/api/v1/crm/people/{priya['id']}/emails{LABS}", headers=as_(SELLER["id"])
        ).json()
        assert [t["thread_id"] for t in person["threads"]] == ["t1"]
        on_deal = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}/emails{LABS}", headers=as_(SELLER["id"])
        ).json()
        assert [t["thread_id"] for t in on_deal["threads"]] == ["t1", "t2"]
        # crm_get carries the latest threads (FR-CRM-MCP-02).
        page = http.get(f"/api/v1/crm/companies/{acme['id']}{LABS}", headers=as_(VIEWER["id"]))
        assert [t["thread_id"] for t in page.json()["emails"]] == ["t1", "t2"]

    def test_paged(self, crm):
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        for n in range(3):
            mail.send(
                mailbox["email"],
                f"m{n}",
                sender="priya@acme.com",
                to=mailbox["email"],
                at=NOW - timedelta(hours=n),
                thread=f"t{n}",
            )
        _sync(queue)
        first = http.get(
            f"/api/v1/crm/companies/{acme['id']}/emails{LABS}&limit=2", headers=as_(OWNER["id"])
        ).json()
        assert [t["thread_id"] for t in first["threads"]] == ["t0", "t1"]
        rest = http.get(
            f"/api/v1/crm/companies/{acme['id']}/emails{LABS}&limit=2&before={first['next']}",
            headers=as_(OWNER["id"]),
        ).json()
        assert [t["thread_id"] for t in rest["threads"]] == ["t2"]
        assert rest["next"] is None


class TestABrokerThatGoesAway:
    def test_the_run_fails_and_the_lock_is_released(self, crm, monkeypatch):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        monkeypatch.setattr(email_sync, "PAGE_SIZE", 1)
        for n in range(2):
            mail.send(
                mailbox["email"], f"m{n}", sender="priya@acme.com", to=mailbox["email"], at=NOW
            )
        email_sync.tick(NOW)
        first = queue.tasks.pop(0)

        def gone(args):
            raise ConnectionError("broker unreachable")

        monkeypatch.setattr(email_sync, "enqueue", gone)
        email_sync.page(first, NOW)
        [run] = _runs(fake)
        assert run["outcome"] == "failed"
        assert run["counts"]["stored"] == 1
        assert queue.lock is None
