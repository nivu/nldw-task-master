# ruff: noqa: F811 — `crm` is the fixture shared with test_crm_email_sync.py
"""Known-contact capture in the sync — spec 010 FR-CAP-SRC-01 to -08,
FR-CAP-CHG-01 to -03, NFR-CAP-02, -03, -05; plan §5.3 to §5.5, PD-3, PD-12,
PD-16, PD-19; spec §13 S1.

On the same `FakeGmail`, queue and fake store as `test_crm_email_sync.py`
(which passes unchanged: *Labels only* is today's sync). A mailbox switched
to *Primary and Sent* files Primary and Sent mail from people and companies
the CRM knows with no label, skips bulk mail, leaves one discovery
candidate per unknown conversation and stores nothing about it, and turns
deal documents sent to or received from known clients with open deals into
`deal_signal` items. Gmail is always faked; nothing reads a body.

R1 (tasks T464): FR-CAP-INV-01 to -03, -05 — an outside person's calendar
invitation is mail, notices and internal invitations stay skipped, and the
one rescan of past invitations; plan R1.5, PD-33, PD-34, PD-43; spec §14.5
I4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import DEFAULTS, OWNER, SELLER, as_
from postgrest.exceptions import APIError
from test_crm_email_sync import (
    LABS,
    NOW,
    _company,
    _links,
    _messages,
    _person,
    _runs,
    _sync,
    crm,  # noqa: F401 — the shared fixture
)

from app.domain.crm import capture
from app.services.crm import email_sync, gmail

PRIMARY = ("INBOX", "CATEGORY_PERSONAL")
SENT = ("SENT",)
OWNER_ADDRESS = "owner@nunnarilabs.com"


def _capturing(fake, mailbox, **values) -> dict:
    """The mailbox on Primary and Sent, as the PATCH would leave it."""
    row = fake.rows["crm_mailboxes"][mailbox["id"]]
    row.update(capture_source="primary_sent", **values)
    return row


def _candidates(fake) -> list[dict]:
    return list(fake.rows["crm_capture_candidates"].values())


def _items(fake) -> list[dict]:
    return list(fake.rows["crm_review_items"].values())


def _latest_run(fake) -> dict:
    return sorted(_runs(fake), key=lambda r: r["started_at"])[-1]


def _deal(fake, ws, company, stage="qualification", created_at="2026-09-01T00:00:00+00:00"):
    return fake.add(
        "crm_opportunities",
        **{
            **DEFAULTS["crm_opportunities"],
            "workspace_id": ws["id"],
            "name": f"{company['name']} pilot",
            "company_id": company["id"],
            "owner_id": SELLER["id"],
            "stage": stage,
            "created_at": created_at,
        },
    )


def _record_counts(fake) -> tuple[int, int, int, int]:
    return (
        len(fake.rows["crm_companies"]),
        len(fake.rows["crm_people"]),
        len(fake.rows["crm_email_messages"]),
        len(fake.rows["crm_capture_candidates"]),
    )


class TestLabelsOnly:
    """NFR-CAP-05, spec §13 S1 line 1 — Labels only behaves as today."""

    def test_capture_labels_only_reads_no_unlabelled_primary_mail(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS, "m1", sender="priya@acme.com", to=OWNER_ADDRESS, at=NOW, labels=PRIMARY
        )
        mail.send(
            OWNER_ADDRESS,
            "m2",
            sender="ravi@new.example",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t2",
        )
        _sync(queue)
        assert _messages(fake) == [] and _candidates(fake) == []
        assert not [c for c in mail.calls if c[0] == "search"]
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["docs_checked_at"] is None


class TestKnownContacts:
    """FR-CAP-SRC-04; spec §13 S1 lines 2 and 3."""

    def test_capture_files_a_primary_message_from_a_known_person(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com")
        priya = _person(fake, labs, "Priya", "priya@acme.com", acme)
        at = NOW - timedelta(hours=30)
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="Priya <priya@acme.com>",
            to=OWNER_ADDRESS,
            at=at,
            labels=PRIMARY,
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert msg["direction"] == "inbound" and msg["is_auto"] is False
        assert _links(fake, msg) == {("person", priya["id"]), ("company", acme["id"])}
        company = fake.rows["crm_companies"][acme["id"]]
        assert company["last_inbound_at"] == at.isoformat()
        assert fake.rows["crm_people"][priya["id"]]["last_inbound_at"] == at.isoformat()
        # Reply owed turned red after 24 hours with no reply (flags move).
        assert [f["kind"] for f in company["flags"]] == ["reply_owed"]
        assert _candidates(fake) == []

    def test_capture_files_a_known_domain_with_no_person(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com")
        mail.send(
            OWNER_ADDRESS, "m1", sender="ravi@acme.com", to=OWNER_ADDRESS, at=NOW, labels=PRIMARY
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert _links(fake, msg) == {("company", acme["id"])}
        # Automation 2 runs for labelled mail only: no person is made.
        assert len(fake.rows["crm_people"]) == 0

    def test_capture_files_sent_mail_and_moves_new_to_contacted(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com", lead_stage="new")
        mail.send(
            OWNER_ADDRESS, "m1", sender=OWNER_ADDRESS, to="priya@acme.com", at=NOW, labels=SENT
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert msg["direction"] == "outbound"
        company = fake.rows["crm_companies"][acme["id"]]
        assert company["lead_stage"] == "contacted"
        assert company["last_outbound_at"] == NOW.isoformat()


class TestTheSkip:
    """FR-CAP-SRC-03; NFR-CAP-02; spec §13 S1 line 4."""

    def test_capture_stores_nothing_for_bulk_internal_or_invites(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _company(fake, labs, "Acme", "acme.com")
        before = _record_counts(fake)
        mail.send(
            OWNER_ADDRESS,
            "news",
            sender="Acme News <news@acme.com>",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t-news",
            List_Unsubscribe="<mailto:unsubscribe@acme.com>",
        )
        mail.send(
            OWNER_ADDRESS,
            "internal",
            sender="deepika@nunnarilabs.com",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t-internal",
        )
        # Spec 010 R1 FR-CAP-INV-03: an invitation between colleagues only
        # (an outside person's invitation is mail: TestInvitations).
        mail.send(
            OWNER_ADDRESS,
            "invite",
            sender="Google Calendar <calendar-notification@google.com>",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t-invite",
            subject="Invitation: Intro call @ Tue 21 Oct 2026",
            Reply_To="Deepika <deepika@nunnarilabs.com>",
        )
        mail.send(
            OWNER_ADDRESS,
            "noreply",
            sender="noreply@stranger.example",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t-noreply",
        )
        _sync(queue)
        assert _record_counts(fake) == before
        counts = _latest_run(fake)["counts"]
        assert counts["skipped_bulk"] == 3 and counts["skipped_internal"] == 1
        assert counts["stored"] == 0 and counts["candidates"] == 0

    def test_capture_still_stores_labelled_bulk_mail_as_auto(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _company(fake, labs, "Acme", "acme.com")
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="news@acme.com",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=("INBOX", "CATEGORY_PERSONAL", "Label_1"),
            Precedence="bulk",
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert msg["is_auto"] is True


class TestCandidates:
    """FR-CAP-SRC-05, CHG-02; spec §13 S1 line 5."""

    def test_capture_leaves_one_candidate_for_an_unknown_sender(self, crm):
        _, fake, _, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        at = NOW - timedelta(hours=3)
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="Priya <priya@stranger.example>",
            to=OWNER_ADDRESS,
            at=at,
            labels=PRIMARY,
            thread="t9",
        )
        mail.send(
            OWNER_ADDRESS,
            "m2",
            sender=OWNER_ADDRESS,
            to="priya@stranger.example",
            at=NOW,
            labels=SENT,
            thread="t9",
        )
        _sync(queue)
        assert _messages(fake) == []
        assert fake.rows["crm_companies"] == {} and fake.rows["crm_people"] == {}
        [candidate] = _candidates(fake)
        assert candidate["mailbox_id"] == mailbox["id"] and candidate["thread_id"] == "t9"
        assert candidate["latest_message_id"] == "m2"
        assert candidate["latest_message_at"] == NOW.isoformat()
        assert candidate["status"] == "pending" and candidate["finding"] is None
        # Ids and times only: no address, name or subject anywhere on it.
        assert "stranger" not in str(candidate) and "Proposal" not in str(candidate)
        # Marked from the Primary page, then advanced from the Sent page.
        assert _latest_run(fake)["counts"]["candidates"] == 2

    def test_capture_a_later_message_advances_the_candidate(self, crm):
        _, fake, _, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="priya@stranger.example",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(days=2),
            labels=PRIMARY,
            thread="t9",
        )
        _sync(queue)
        [candidate] = _candidates(fake)
        # A decision recorded meanwhile (S2's job) is cleared by a newer message.
        candidate.update(
            status="judged",
            outcome="not_relevant",
            confidence="high",
            judged_at=NOW.isoformat(),
            judged_message_id="m1",
            classifier_version="010.1",
        )
        mail.send(
            OWNER_ADDRESS,
            "m2",
            sender="priya@stranger.example",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t9",
        )
        _sync(queue)
        [candidate] = _candidates(fake)
        assert candidate["latest_message_id"] == "m2"
        assert candidate["status"] == "pending" and candidate["outcome"] is None
        assert candidate["judged_message_id"] is None and candidate["attempts"] == 0

    def test_capture_an_older_message_never_moves_the_candidate_back(self, crm):
        _, fake, _, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        mail.send(
            OWNER_ADDRESS,
            "m2",
            sender="priya@stranger.example",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
            thread="t9",
        )
        _sync(queue)
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="priya@stranger.example",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(days=3),
            labels=PRIMARY,
            thread="t9",
        )
        _sync(queue)
        [candidate] = _candidates(fake)
        assert candidate["latest_message_id"] == "m2"

    def test_capture_runs_no_automation_two_for_captured_mail(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        fake.add(
            "crm_workspace_settings",
            workspace_id=labs["id"],
            key="automations",
            value={"email_records": True},
        )
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="ravi@stranger.example",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=PRIMARY,
        )
        _sync(queue)
        assert fake.rows["crm_companies"] == {} and fake.rows["crm_people"] == {}
        assert len(_candidates(fake)) == 1


class TestHandLabelsWin:
    """FR-CAP-SRC-05 (labelled mail), spec §13 S1 line 6."""

    def test_capture_keeps_automation_two_for_labelled_mail(self, crm):
        _, fake, _, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="Priya Shah <priya@acme-robotics.com>",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=("INBOX", "CATEGORY_PERSONAL", "Label_1"),
        )
        _sync(queue)
        [company] = fake.rows["crm_companies"].values()
        assert company["name"] == "Acme Robotics" and company["lead_stage"] == "new"
        [msg] = _messages(fake)
        assert ("company", company["id"]) in _links(fake, msg)
        assert _candidates(fake) == []


class TestEnteringScope:
    """FR-CAP-SRC-02 — history events add mail moved into Primary."""

    def test_capture_takes_a_message_moved_into_primary(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=NOW,
            labels=("INBOX", "CATEGORY_UPDATES"),
        )
        _sync(queue)
        assert _messages(fake) == []
        mail.relabel(OWNER_ADDRESS, "m1", add=("CATEGORY_PERSONAL",), remove=("CATEGORY_UPDATES",))
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m1"]


class TestLeavingScope:
    """FR-CAP-SRC-02, -08; spec §13 S1 line 7."""

    def _filed(self, crm, labels=PRIMARY, sender="priya@acme.com", to=OWNER_ADDRESS):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(OWNER_ADDRESS, "m1", sender=sender, to=to, at=NOW, labels=labels)
        _sync(queue)
        assert len(_messages(fake)) == 1
        return fake, mail, queue

    def test_capture_removes_mail_moved_out_of_primary(self, crm):
        fake, mail, queue = self._filed(crm)
        mail.relabel(
            OWNER_ADDRESS, "m1", add=("CATEGORY_PROMOTIONS",), remove=("CATEGORY_PERSONAL",)
        )
        _sync(queue)
        assert _messages(fake) == []
        assert _latest_run(fake)["counts"]["removed"] == 1

    def test_capture_keeps_a_moved_message_that_carries_a_label(self, crm):
        fake, mail, queue = self._filed(crm, labels=("INBOX", "CATEGORY_PERSONAL", "Label_1"))
        mail.relabel(OWNER_ADDRESS, "m1", remove=("CATEGORY_PERSONAL",))
        _sync(queue)
        assert len(_messages(fake)) == 1

    def test_capture_keeps_sent_mail_that_loses_primary(self, crm):
        fake, mail, queue = self._filed(
            crm, labels=("SENT", "CATEGORY_PERSONAL"), sender=OWNER_ADDRESS, to="priya@acme.com"
        )
        mail.relabel(OWNER_ADDRESS, "m1", remove=("CATEGORY_PERSONAL",))
        _sync(queue)
        assert len(_messages(fake)) == 1

    @pytest.mark.parametrize("bin_it", ["trash", "spam"])
    def test_capture_removes_mail_put_in_trash_or_spam(self, crm, bin_it):
        fake, mail, queue = self._filed(crm)
        getattr(mail, bin_it)(OWNER_ADDRESS, "m1")
        _sync(queue)
        assert _messages(fake) == []

    @pytest.mark.parametrize("bin_it", ["trash", "spam"])
    def test_capture_removes_labelled_mail_put_in_trash_or_spam(self, crm, bin_it):
        """FR-CAP-SRC-02, -08: Trash and Spam take a message out of scope
        whatever labels it carries, unlike a move out of Primary."""
        fake, mail, queue = self._filed(crm, labels=("INBOX", "CATEGORY_PERSONAL", "Label_1"))
        getattr(mail, bin_it)(OWNER_ADDRESS, "m1")
        _sync(queue)
        assert _messages(fake) == []

    def test_capture_takes_untrashed_mail_again(self, crm):
        fake, mail, queue = self._filed(crm)
        mail.trash(OWNER_ADDRESS, "m1")
        _sync(queue)
        assert _messages(fake) == []
        mail.untrash(OWNER_ADDRESS, "m1")
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m1"]

    def test_capture_switching_back_to_labels_only_removes_old_captured_mail(self, crm):
        """PD-3, NFR-CAP-05: captured mail older than 90 days goes too; a
        labelled message of the same age stays."""
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        old = NOW - timedelta(days=120)
        mail.send(
            OWNER_ADDRESS,
            "m-captured",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=old,
            labels=PRIMARY,
        )
        mail.send(
            OWNER_ADDRESS,
            "m-labelled",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=old,
            labels=("INBOX", "CATEGORY_PERSONAL", "Label_1"),
            thread="t2",
        )
        _sync(queue)
        assert len(_messages(fake)) == 2
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_source": "labels"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        row = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert row["reconcile_after"] == datetime(2026, 4, 1, tzinfo=capture.ZONE).isoformat()
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m-labelled"]
        # The reconcile point is cleared with the cursor once used.
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["reconcile_after"] is None

    def _switch(self, http, mailbox, **values) -> None:
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}", json=values, headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text

    def test_capture_switching_back_removes_mail_from_the_start_date_itself(self, crm):
        """PD-3: the day's margin is not taken off the old start date."""
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS,
            "m-first-day",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=datetime(2026, 4, 1, 10, 0, tzinfo=capture.ZONE),
            labels=PRIMARY,
        )
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m-first-day"]
        self._switch(http, mailbox, capture_source="labels")
        _sync(queue)
        assert _messages(fake) == []

    def test_capture_a_start_date_moved_later_is_still_reconciled_on_the_switch_back(self, crm):
        """PD-3, NFR-CAP-05: mail captured under an earlier start date goes
        too when the mailbox goes back to Labels only."""
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS,
            "m-april",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=datetime(2026, 4, 15, tzinfo=UTC),
            labels=PRIMARY,
        )
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m-april"]
        self._switch(http, mailbox, capture_start_date="2026-08-01")
        april = datetime(2026, 4, 1, tzinfo=capture.ZONE).isoformat()
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["reconcile_after"] == april
        _sync(queue)
        # Still in Primary, so kept while capturing; the point is kept too.
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m-april"]
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["reconcile_after"] == april
        self._switch(http, mailbox, capture_source="labels")
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["reconcile_after"] == april
        _sync(queue)
        assert _messages(fake) == []
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["reconcile_after"] is None

    def test_capture_a_start_date_moved_earlier_keeps_no_extra_point(self, crm):
        http, fake, _, _, _, queue, mailbox, _ = crm
        _capturing(fake, mailbox, capture_start_date="2026-06-01")
        _sync(queue)
        self._switch(http, mailbox, capture_start_date="2026-05-01")
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["reconcile_after"] is None


class TestTheBackfill:
    """FR-CAP-SRC-07, CHG-03; PD-12."""

    def test_capture_backfills_from_the_start_date_not_ninety_days(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS,
            "m-may",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=datetime(2026, 5, 17, tzinfo=UTC),
            labels=PRIMARY,
        )
        mail.send(
            OWNER_ADDRESS,
            "m-march",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=datetime(2026, 3, 20, tzinfo=UTC),
            labels=PRIMARY,
            thread="t2",
        )
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m-may"]

    def test_capture_a_later_start_date_reads_less(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox, capture_start_date="2026-06-01")
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS,
            "m-may",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=datetime(2026, 5, 17, tzinfo=UTC),
            labels=PRIMARY,
        )
        _sync(queue)
        assert _messages(fake) == []

    def test_capture_still_syncs_when_every_configured_label_is_gone(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox, labels=["Renamed away"])
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS, "m1", sender="priya@acme.com", to=OWNER_ADDRESS, at=NOW, labels=PRIMARY
        )
        _sync(queue)
        assert len(_messages(fake)) == 1
        assert _latest_run(fake)["outcome"] == "ok"

    def test_capture_switching_source_resets_the_cursor_and_reads_again(self, crm):
        http, fake, labs, _, mail, queue, mailbox, _ = crm
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        mail.send(
            OWNER_ADDRESS,
            "m1",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(days=150),
            labels=PRIMARY,
        )
        _sync(queue)
        assert _messages(fake) == []
        r = http.patch(
            f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
            json={"capture_source": "primary_sent"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["backfilled_at"] is None
        _sync(queue)
        assert [m["gmail_message_id"] for m in _messages(fake)] == ["m1"]


class TestDealDocuments:
    """FR-CAP-SRC-06; E8, E10; PD-19; spec §13 S1 line 8."""

    def _ready(self, crm, stage="qualification", deal_created="2026-09-01T00:00:00+00:00"):
        """A known client with an open deal, the mailbox capturing and its
        first backfill done (the search runs after the history is read)."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com", lead_stage="deal_open")
        _person(fake, labs, "Priya", "priya@acme.com", acme)
        deal = _deal(fake, labs, acme, stage=stage, created_at=deal_created)
        _sync(queue)
        return fake, mail, queue, mailbox, acme, deal

    def test_capture_suggests_proposal_for_a_sent_sow(self, crm):
        fake, mail, queue, mailbox, acme, deal = self._ready(crm)
        at = NOW - timedelta(hours=1)
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=at,
            labels=SENT,
            thread="t-sow",
            attachments=["Acme SOW v2.pdf"],
        )
        _sync(queue)
        [item] = _items(fake)
        assert item["type"] == "deal_signal" and item["status"] == "open"
        assert (item["record_type"], item["record_id"]) == ("company", acme["id"])
        assert item["owner_id"] == OWNER["id"]
        assert item["source_version"] == "m-sow"
        # Spec 010 R1 (FR-CAP-GRP-06): keyed per company, the document its entry.
        assert item["dedupe_key"] == f"email_thread|capture:deal|deal_signal|company:{acme['id']}"
        [entry] = item["source_link"]["threads"]
        assert (entry["ref"], entry["message_id"], entry["origin"]) == (
            f"gmail:{mailbox['id']}:t-sow",
            "m-sow",
            "deal_document",
        )
        suggestion = item["suggestion"]
        assert suggestion["stage"] == "proposal"
        assert suggestion["proposal_sent_on"] == at.astimezone(capture.ZONE).date().isoformat()
        assert suggestion["evidence"]["document_kinds"] == ["sow"]
        choices = [
            d["opportunity_id"]
            for d in fake.rows["crm_review_item_deals"].values()
            if d["item_id"] == item["id"]
        ]
        assert choices == [deal["id"]]
        # The SOW itself is captured mail from a known client.
        assert "m-sow" in {m["gmail_message_id"] for m in _messages(fake)}
        counts = _latest_run(fake)["counts"]
        assert counts["deal_signals"] == 1 and counts["deal_doc_failed"] == 0
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["docs_checked_at"] == NOW.isoformat()

    def test_capture_a_document_to_two_companies_names_each_one_s_contact(self, crm):
        """PD-39: one signal per company, each entry's sender that
        company's own outside person, not the document's first one."""
        fake, mail, queue, mailbox, acme, deal = self._ready(crm)
        labs = next(
            w for w in fake.rows["crm_workspaces"].values() if w["id"] == acme["workspace_id"]
        )
        beta = _company(fake, labs, "Beta", "beta.com", lead_stage="deal_open")
        _deal(fake, labs, beta, stage="qualification", created_at="2026-09-01T00:00:00+00:00")
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com, ravi@beta.com",
            at=NOW - timedelta(hours=1),
            labels=SENT,
            thread="t-sow",
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        senders = {
            i["record_id"]: [e["person_email"] for e in i["source_link"]["threads"]]
            for i in _items(fake)
        }
        assert senders == {acme["id"]: ["priya@acme.com"], beta["id"]: ["ravi@beta.com"]}

    def test_capture_a_second_sync_makes_no_second_item(self, crm):
        fake, mail, queue, *_ = self._ready(crm)
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=NOW - timedelta(hours=1),
            labels=SENT,
            thread="t-sow",
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        fake.rows["crm_mailboxes"][next(iter(fake.rows["crm_mailboxes"]))]["docs_checked_at"] = None
        _sync(queue)
        assert len(_items(fake)) == 1

    def test_capture_suggests_closed_won_for_a_received_sow(self, crm):
        fake, mail, queue, *_ = self._ready(crm, stage="proposal")
        mail.send(
            OWNER_ADDRESS,
            "m-signed",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=1),
            labels=PRIMARY,
            thread="t-signed",
            attachments=["Statement of Work - signed.pdf"],
        )
        _sync(queue)
        [item] = _items(fake)
        assert item["suggestion"]["stage"] == "closed_won"
        assert item["suggestion"]["evidence"]["reason"].endswith("Check the signed document.")

    def test_capture_a_sent_sow_for_a_deal_past_proposal_makes_nothing(self, crm):
        fake, mail, queue, *_ = self._ready(crm, stage="negotiation")
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=NOW - timedelta(hours=1),
            labels=SENT,
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        assert _items(fake) == []

    def test_capture_makes_nothing_without_an_open_deal(self, crm):
        fake, mail, queue, _, _, deal = self._ready(crm)
        fake.rows["crm_opportunities"][deal["id"]]["stage"] = "closed_lost"
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=NOW - timedelta(hours=1),
            labels=SENT,
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        assert _items(fake) == []

    def test_capture_makes_nothing_for_an_sow_in_promotions(self, crm):
        fake, mail, queue, *_ = self._ready(crm)
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=1),
            labels=("INBOX", "CATEGORY_PROMOTIONS"),
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        assert _items(fake) == []

    def test_capture_makes_nothing_for_a_bulk_sow_mail(self, crm):
        fake, mail, queue, *_ = self._ready(crm)
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=1),
            labels=("INBOX", "CATEGORY_PERSONAL", "Label_1"),
            attachments=["proposal.pdf"],
            List_Unsubscribe="<mailto:u@acme.com>",
        )
        _sync(queue)
        assert _items(fake) == []

    def test_capture_makes_nothing_for_a_document_older_than_the_deal(self, crm):
        fake, mail, queue, *_ = self._ready(crm, deal_created=NOW.isoformat())
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=NOW - timedelta(days=2),
            labels=SENT,
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        assert _items(fake) == []

    def test_capture_the_first_search_looks_back_fourteen_days(self, crm):
        fake, mail, queue, *_ = self._ready(crm)
        mail.send(
            OWNER_ADDRESS,
            "m-old",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=NOW - timedelta(days=20),
            labels=SENT,
            attachments=["SOW.pdf"],
        )
        _sync(queue)
        # Spec 010 R1: the invitation rescan searches too, without has:attachment.
        searches = [q for kind, q in mail.calls if kind == "search" and "has:attachment" in q]
        assert len(searches) == len(capture.DEAL_DOC_QUERIES)
        after = int((NOW - timedelta(days=14)).timestamp())
        assert all(f"after:{after} " in q for q in searches)
        assert all("-in:trash -in:spam" in q and 'label:"Sales"' in q for q in searches)
        assert _items(fake) == []

    def test_capture_turning_capture_back_on_searches_fourteen_days_again(self, crm):
        """PD-19, contracts §1.1: a source change clears the search's cursor,
        so an old `docs_checked_at` does not bring back months of documents."""
        fake, mail, queue, mailbox, *_ = self._ready(crm)
        http = crm[0]
        fake.rows["crm_mailboxes"][mailbox["id"]]["docs_checked_at"] = datetime(
            2026, 6, 1, tzinfo=UTC
        ).isoformat()
        for source in ("labels", "primary_sent"):
            r = http.patch(
                f"/api/v1/crm/mailboxes/{mailbox['id']}{LABS}",
                json={"capture_source": source},
                headers=as_(OWNER["id"]),
            )
            assert r.status_code == 200, r.text
            assert fake.rows["crm_mailboxes"][mailbox["id"]]["docs_checked_at"] is None
        mail.send(
            OWNER_ADDRESS,
            "m-july",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=datetime(2026, 7, 1, tzinfo=UTC),
            labels=SENT,
            thread="t-july",
            attachments=["SOW.pdf"],
        )
        _sync(queue)  # the backfill again
        before = len(mail.calls)
        _sync(queue)  # the history, then the search
        searches = [
            q for kind, q in mail.calls[before:] if kind == "search" and "has:attachment" in q
        ]
        assert len(searches) == len(capture.DEAL_DOC_QUERIES)
        after = int((NOW - timedelta(days=14)).timestamp())
        assert all(f"after:{after} " in q for q in searches)
        assert _items(fake) == []

    def test_capture_a_failed_search_is_counted_and_keeps_the_cursor(self, crm, monkeypatch):
        fake, mail, queue, mailbox, *_ = self._ready(crm)

        def refused(*args, **kwargs):
            raise gmail.GmailError("Gmail search failed (503).")

        monkeypatch.setattr(gmail, "search", refused)
        _sync(queue)
        run = _latest_run(fake)
        assert run["outcome"] == "ok" and run["counts"]["deal_doc_failed"] == 1
        assert fake.rows["crm_mailboxes"][mailbox["id"]]["docs_checked_at"] is None

    @pytest.mark.parametrize("failure", ["time_limit", "item_refused"])
    def test_capture_any_deal_document_failure_keeps_the_history_cursor(
        self, crm, monkeypatch, failure
    ):
        # OPS-1: the search is a secondary step; whatever it raises, the
        # page saves its history cursor and the sync run is not failed.
        fake, mail, queue, mailbox, acme, deal = self._ready(crm)
        mail.send(
            OWNER_ADDRESS,
            "m-sow",
            sender=OWNER_ADDRESS,
            to="priya@acme.com",
            at=NOW - timedelta(hours=1),
            labels=SENT,
            thread="t-sow",
            attachments=["Acme SOW v2.pdf"],
        )
        if failure == "time_limit":
            from celery.exceptions import SoftTimeLimitExceeded

            def refused(*args, **kwargs):
                raise SoftTimeLimitExceeded()

            monkeypatch.setattr(gmail, "search", refused)
        else:
            from app.services.crm import review

            def refused(*args, **kwargs):
                raise APIError({"code": "23514", "message": "check violation"})

            # Spec 010 R1: a deal document's signal is stored by the group upsert.
            monkeypatch.setattr(review, "capture_group_upsert", refused)
        before = fake.rows["crm_mailboxes"][mailbox["id"]]["history_id"]
        _sync(queue)
        run = _latest_run(fake)
        assert run["outcome"] == "ok" and run["counts"]["deal_doc_failed"] == 1
        row = fake.rows["crm_mailboxes"][mailbox["id"]]
        assert row["docs_checked_at"] is None
        assert row["history_id"] != before
        # The history itself was read: the SOW is stored.
        assert "m-sow" in {m["gmail_message_id"] for m in _messages(fake)}

    def test_capture_labels_only_never_searches(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _sync(queue)
        _sync(queue)
        assert not [c for c in mail.calls if c[0] == "search"]
        assert _items(fake) == []


class TestFilingAThread:
    """Plan §5.1, §5.9; tasks T207a — `crm.capture_thread` files one
    conversation that became known, on `worker-crm-sync`, as known-contact
    capture would: Primary, Sent and labelled messages only (E10)."""

    def _thread(self, mail, at):
        mail.send(
            OWNER_ADDRESS,
            "f1",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=at,
            labels=PRIMARY,
            thread="t9",
        )
        mail.send(
            OWNER_ADDRESS,
            "f2",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=at + timedelta(minutes=5),
            labels=("INBOX", "CATEGORY_PROMOTIONS"),
            thread="t9",
        )
        mail.send(
            OWNER_ADDRESS,
            "f3",
            sender="priya@acme.com",
            to=OWNER_ADDRESS,
            at=at + timedelta(minutes=10),
            labels=("CATEGORY_PERSONAL", "TRASH"),
            thread="t9",
        )

    def test_capture_files_a_known_thread_and_skips_promotions_and_trash(self, crm):
        from app.services.crm import email_sync

        _, fake, labs, _, mail, _, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com")
        priya = _person(fake, labs, "Priya", "priya@acme.com", acme)
        at = NOW - timedelta(hours=2)
        self._thread(mail, at)
        got = email_sync.file_thread(labs["id"], mailbox["id"], "t9")
        [msg] = _messages(fake)
        assert msg["gmail_message_id"] == "f1" and got["stored"] == 1
        assert _links(fake, msg) == {("person", priya["id"]), ("company", acme["id"])}
        assert fake.rows["crm_people"][priya["id"]]["last_inbound_at"] == at.isoformat()
        # Filing twice stores nothing twice.
        assert email_sync.file_thread(labs["id"], mailbox["id"], "t9")["stored"] == 0
        assert len(_messages(fake)) == 1

    def test_capture_files_nothing_for_a_paused_mailbox_or_sync_off(self, crm):
        from app.services.crm import email_sync

        _, fake, labs, _, mail, _, mailbox, _ = crm
        _capturing(fake, mailbox, is_active=False)
        _person(fake, labs, "Priya", "priya@acme.com", _company(fake, labs, "Acme", "acme.com"))
        self._thread(mail, NOW - timedelta(hours=2))
        assert email_sync.file_thread(labs["id"], mailbox["id"], "t9")["stored"] == 0
        fake.rows["crm_mailboxes"][mailbox["id"]]["is_active"] = True
        setting = next(
            r
            for r in fake.rows["crm_workspace_settings"].values()
            if r["workspace_id"] == labs["id"] and r["key"] == "email_sync"
        )
        setting["value"] = {"enabled": False}
        assert email_sync.file_thread(labs["id"], mailbox["id"], "t9")["stored"] == 0
        assert _messages(fake) == []

    def test_capture_thread_task_runs_on_the_sync_queue_and_logs_only_ids(
        self, crm, monkeypatch, caplog
    ):
        import logging

        from app.services.crm import email_sync
        from app.tasks import crm as tasks
        from app.worker import celery_app

        assert celery_app.conf.task_routes["crm.capture_thread"] == {"queue": "crm_sync"}
        assert tasks.capture_thread.ignore_result is True

        def broken(*args):
            raise RuntimeError("ZQXSECRET Priya wrote this")

        monkeypatch.setattr(email_sync, "file_thread", broken)
        caplog.set_level(logging.INFO)
        assert tasks.capture_thread("w1", "m1", "t1") is None
        assert "crm_capture_thread_failed" in caplog.text
        assert "RuntimeError" in caplog.text and "ZQXSECRET" not in caplog.text


CALENDAR = "Google Calendar <calendar-notification@google.com>"


class TestInvitations:
    """Spec 010 R1 FR-CAP-INV-01 to -03, PD-33; spec §14.5 I4 lines 1 to 3:
    an invitation with someone outside is mail (known → filed, unknown →
    candidate); notices, responses, reminders, internal invitations and
    invitations carrying any other bulk sign stay skipped."""

    def test_capture_an_outsider_s_invitation_is_a_candidate(self, crm):
        _, fake, _, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        before = _record_counts(fake)
        mail.send(
            OWNER_ADDRESS,
            "m-inv",
            sender="Ravi <ravi@stranger.example>",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=2),
            labels=PRIMARY,
            thread="t-inv",
            subject="Invitation: Intro call @ Tue 21 Oct 2026",
        )
        _sync(queue)
        assert _messages(fake) == []
        [candidate] = _candidates(fake)
        assert (candidate["thread_id"], candidate["latest_message_id"]) == ("t-inv", "m-inv")
        assert _record_counts(fake) == (before[0], before[1], before[2], 1)
        counts = _latest_run(fake)["counts"]
        assert counts["skipped_bulk"] == 0 and counts["candidates"] == 1

    def test_capture_a_calendar_sent_invitation_counts_its_reply_to_organiser(self, crm):
        # PD-33: Google Calendar sends it, the organiser only in Reply-To,
        # with its Auto-Submitted header; the calendar sender is never the
        # outside person.
        _, fake, _, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        mail.send(
            OWNER_ADDRESS,
            "m-inv",
            sender=CALENDAR,
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=2),
            labels=PRIMARY,
            thread="t-inv",
            subject="Updated invitation: Intro call @ Tue 21 Oct 2026",
            Reply_To="Ravi <ravi@stranger.example>",
            Auto_Submitted="auto-generated",
        )
        _sync(queue)
        assert _messages(fake) == []
        [candidate] = _candidates(fake)
        assert candidate["thread_id"] == "t-inv"

    def test_capture_files_a_known_contact_s_invitation(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com")
        priya = _person(fake, labs, "Priya", "priya@acme.com", acme)
        at = NOW - timedelta(hours=2)
        mail.send(
            OWNER_ADDRESS,
            "m-inv",
            sender=CALENDAR,
            to=OWNER_ADDRESS,
            at=at,
            labels=PRIMARY,
            thread="t-inv",
            subject="Invitation: Pilot review @ Wed 22 Oct 2026",
            Reply_To="Priya <priya@acme.com>",
            Auto_Submitted="auto-generated",
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert msg["gmail_message_id"] == "m-inv"
        assert _links(fake, msg) == {("person", priya["id"]), ("company", acme["id"])}
        assert _candidates(fake) == []
        # FR-CAP-INV-02: like any message, it moves Last inbound.
        assert fake.rows["crm_companies"][acme["id"]]["last_inbound_at"] == at.isoformat()
        assert fake.rows["crm_people"][priya["id"]]["last_inbound_at"] == at.isoformat()

    def test_capture_an_invitation_the_owner_sends_moves_new_to_contacted(self, crm):
        # FR-CAP-INV-02: Last outbound and New → Contacted like any message.
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        acme = _company(fake, labs, "Acme", "acme.com", lead_stage="new")
        at = NOW - timedelta(hours=2)
        mail.send(
            OWNER_ADDRESS,
            "m-inv",
            sender=OWNER_ADDRESS,
            to="Priya <priya@acme.com>",
            at=at,
            labels=SENT,
            thread="t-inv",
            subject="Invitation: Pilot review @ Wed 22 Oct 2026",
        )
        _sync(queue)
        [msg] = _messages(fake)
        assert msg["direction"] == "outbound"
        company = fake.rows["crm_companies"][acme["id"]]
        assert company["lead_stage"] == "contacted"
        assert company["last_outbound_at"] == at.isoformat()

    def test_capture_never_files_a_calendar_invitation_to_google(self, crm):
        # FR-CAP-INV-01: Google Calendar's sending address is never the
        # outside person, so a google.com company in the CRM does not make
        # an outsider's invitation known mail; the organiser's is a candidate.
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        google = _company(fake, labs, "Google", "google.com")
        mail.send(
            OWNER_ADDRESS,
            "m-inv",
            sender=CALENDAR,
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=2),
            labels=PRIMARY,
            thread="t-inv",
            subject="Invitation: Intro call @ Tue 21 Oct 2026",
            Reply_To="Ravi <ravi@stranger.example>",
            Auto_Submitted="auto-generated",
        )
        _sync(queue)
        assert _messages(fake) == []
        [candidate] = _candidates(fake)
        assert candidate["thread_id"] == "t-inv"
        assert fake.rows["crm_companies"][google["id"]]["last_inbound_at"] is None

    def test_capture_still_skips_calendar_notices(self, crm):
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        _capturing(fake, mailbox)
        before = _record_counts(fake)
        at = NOW - timedelta(hours=2)
        sent = (
            # A guest's response.
            ("m-acc", "Ravi <ravi@stranger.example>", "Accepted: Intro call @ Tue", {}),
            # A reminder.
            ("m-rem", CALENDAR, "Notification: Intro call @ Tue", {}),
            # A webinar titled as an invitation: List-Unsubscribe still wins.
            (
                "m-web",
                "Events <events@stranger.example>",
                "Invitation: AI Summit 2026",
                {"List_Unsubscribe": "<mailto:u@stranger.example>"},
            ),
            # A no-reply sender other than Google Calendar's.
            ("m-nr", "no-reply@stranger.example", "Invitation: Product webinar", {}),
        )
        for gmail_id, sender, subject, headers in sent:
            mail.send(
                OWNER_ADDRESS,
                gmail_id,
                sender=sender,
                to=OWNER_ADDRESS,
                at=at,
                labels=PRIMARY,
                thread=f"t-{gmail_id}",
                subject=subject,
                **headers,
            )
        # An invitation between colleagues only, sent by one of them.
        mail.send(
            OWNER_ADDRESS,
            "m-int",
            sender="deepika@nunnarilabs.com",
            to=OWNER_ADDRESS,
            at=at,
            labels=PRIMARY,
            thread="t-int",
            subject="Invitation: Standup @ Mon",
        )
        _sync(queue)
        assert _record_counts(fake) == before
        counts = _latest_run(fake)["counts"]
        assert counts["skipped_bulk"] == 4 and counts["skipped_internal"] == 1
        assert counts["stored"] == 0 and counts["candidates"] == 0


class TestInviteRescan:
    """Spec 010 R1 FR-CAP-INV-05, PD-34, PD-43; spec §14.5 I4 line 4: once
    per capturing mailbox, past invitations go through the filing, a page
    at a time across syncs; the finish is recorded; a failure keeps the
    token; a token Gmail refuses as invalid restarts the rescan."""

    @staticmethod
    def _ready(crm, monkeypatch, *, token=None):
        """A capturing mailbox already backfilled (before 074, so its
        invitations were skipped), its history read up to now; two pages of
        two ids per sync are one page of two (patched)."""
        _, fake, labs, _, mail, queue, mailbox, _ = crm
        acme = _company(fake, labs, "Acme", "acme.com")
        _person(fake, labs, "Priya", "priya@acme.com", acme)
        at = NOW - timedelta(days=30)
        for gmail_id, sender, subject, headers in (
            ("a-inv1", "Ravi <ravi@one.example>", "Invitation: Intro @ Mon", {}),
            # The search matches the word; the prefix check drops it.
            ("b-word", "Sam <sam@two.example>", "Thanks for the invitation", {}),
            (
                "c-inv2",
                CALENDAR,
                "Invitation: Pilot review @ Wed",
                {"Reply_To": "Priya <priya@acme.com>", "Auto_Submitted": "auto-generated"},
            ),
            ("d-inv3", "Lee <lee@three.example>", "Updated invitation: Demo @ Fri", {}),
        ):
            mail.send(
                OWNER_ADDRESS,
                gmail_id,
                sender=sender,
                to=OWNER_ADDRESS,
                at=at,
                labels=PRIMARY,
                thread=f"t-{gmail_id}",
                subject=subject,
                **headers,
            )
        row = _capturing(
            fake,
            mailbox,
            backfilled_at="2026-10-01T00:00:00+00:00",
            last_synced_at="2026-10-13T00:00:00+00:00",
            history_id=str(mail.box(OWNER_ADDRESS)["history_id"]),
            invite_rescan_token=token,
        )
        monkeypatch.setattr(capture, "INVITE_RESCAN_PAGES", 1)
        monkeypatch.setattr(capture, "INVITE_RESCAN_PAGE_SIZE", 2)
        return fake, mail, queue, row

    @staticmethod
    def _rescans(mail, since: int = 0) -> list[str]:
        return [q for kind, q in mail.calls[since:] if kind == "search" and "subject:" in q]

    def test_capture_rescans_invitations_once(self, crm, monkeypatch):
        fake, mail, queue, row = self._ready(crm, monkeypatch)
        _sync(queue)
        [query] = self._rescans(mail)
        start = int(capture._midnight("2026-04-01").timestamp())
        assert query.startswith(f"after:{start} ") and query.endswith(" subject:invitation")
        assert "-in:trash -in:spam" in query and 'label:"Sales"' in query
        counts = _latest_run(fake)["counts"]
        assert counts["invites_rescanned"] == 1 and counts["candidates"] == 1
        assert row["invite_rescan_token"] == "2" and row["invites_rescanned_at"] is None
        assert [c["thread_id"] for c in _candidates(fake)] == ["t-a-inv1"]

        _sync(queue)
        counts = _latest_run(fake)["counts"]
        assert counts["invites_rescanned"] == 2
        assert counts["stored"] == 1 and counts["candidates"] == 1
        assert row["invites_rescanned_at"] == NOW.isoformat()
        assert row["invite_rescan_token"] is None
        [msg] = _messages(fake)
        assert msg["gmail_message_id"] == "c-inv2"
        # The word match never went through the filing.
        assert sorted(c["thread_id"] for c in _candidates(fake)) == ["t-a-inv1", "t-d-inv3"]

        before = len(mail.calls)
        _sync(queue)
        assert self._rescans(mail, before) == []
        assert row["invites_rescanned_at"] == NOW.isoformat()

    def test_capture_a_rescanned_page_stores_nothing_twice(self, crm, monkeypatch):
        fake, mail, queue, row = self._ready(crm, monkeypatch)
        monkeypatch.setattr(capture, "INVITE_RESCAN_PAGES", 5)
        _sync(queue)
        assert row["invites_rescanned_at"] == NOW.isoformat()
        records = _record_counts(fake)
        candidates = {c["id"]: dict(c) for c in _candidates(fake)}
        # The same pages again (as after a restart).
        row.update(invites_rescanned_at=None, invite_rescan_token=None)
        _sync(queue)
        assert _record_counts(fake) == records
        assert {c["id"]: c for c in _candidates(fake)} == candidates
        assert _latest_run(fake)["counts"]["stored"] == 0

    def test_capture_a_failed_rescan_page_keeps_its_token(self, crm, monkeypatch):
        fake, mail, queue, row = self._ready(crm, monkeypatch, token="2")
        search = gmail.search

        def refused(mailbox, q, **kwargs):
            if "subject:" in q:
                raise gmail.GmailError("Gmail search failed (503).", 503)
            return search(mailbox, q, **kwargs)

        monkeypatch.setattr(gmail, "search", refused)
        history_before = row["history_id"]
        mail.send(
            OWNER_ADDRESS,
            "m-new",
            sender="Priya <priya@acme.com>",
            to=OWNER_ADDRESS,
            at=NOW - timedelta(hours=1),
            labels=PRIMARY,
            thread="t-new",
        )
        _sync(queue)
        run = _latest_run(fake)
        assert run["outcome"] == "ok"
        assert run["counts"]["invite_rescan_failed"] == 1
        assert run["counts"]["invite_rescan_restarted"] == 0
        assert row["invite_rescan_token"] == "2" and row["invites_rescanned_at"] is None
        # The history cursor moved on and its mail was filed.
        assert row["history_id"] != history_before
        assert "m-new" in {m["gmail_message_id"] for m in _messages(fake)}

    def test_capture_labels_only_never_rescans(self, crm, monkeypatch):
        # FR-CAP-INV-05, FR-CAP-SRC-02: only a Primary and Sent mailbox.
        fake, mail, queue, row = self._ready(crm, monkeypatch)
        row.update(capture_source="labels_only")
        _sync(queue)
        assert self._rescans(mail) == []
        assert row["invites_rescanned_at"] is None and row["invite_rescan_token"] is None
        assert _candidates(fake) == [] and _messages(fake) == []

    def test_capture_the_rescan_stops_inside_the_page_s_time_budget(self, crm, monkeypatch):
        # A page task shares its time limit: no rescan page starts after
        # INVITE_RESCAN_SECONDS, and the saved token resumes next sync.
        fake, mail, queue, row = self._ready(crm, monkeypatch)
        monkeypatch.setattr(capture, "INVITE_RESCAN_PAGES", 5)
        ticks = iter(range(0, 10_000, 70))
        monkeypatch.setattr(email_sync.time, "monotonic", lambda: next(ticks))
        _sync(queue)
        # Started at 0; the first page at 70 s, none at 140 s.
        assert len(self._rescans(mail)) == 1
        counts = _latest_run(fake)["counts"]
        assert counts["invites_rescanned"] == 1 and counts["invite_rescan_failed"] == 0
        assert row["invite_rescan_token"] == "2" and row["invites_rescanned_at"] is None

    def test_capture_the_rescan_reads_each_message_once(self, crm, monkeypatch):
        fake, mail, queue, row = self._ready(crm, monkeypatch)
        monkeypatch.setattr(capture, "INVITE_RESCAN_PAGES", 5)
        before = len(mail.calls)
        _sync(queue)
        assert row["invites_rescanned_at"] == NOW.isoformat()
        read = [i for kind, i in mail.calls[before:] if kind == "message"]
        assert sorted(read) == ["a-inv1", "b-word", "c-inv2", "d-inv3"]
        # Again from the start: what is stored is not read at all.
        row.update(invites_rescanned_at=None, invite_rescan_token=None)
        before = len(mail.calls)
        _sync(queue)
        read = [i for kind, i in mail.calls[before:] if kind == "message"]
        assert "c-inv2" not in read and sorted(read) == ["a-inv1", "b-word", "d-inv3"]

    @pytest.mark.parametrize("status", [400, 404])
    def test_capture_an_invalid_saved_token_restarts_the_rescan(self, crm, monkeypatch, status):
        fake, mail, queue, row = self._ready(crm, monkeypatch, token="stale")
        search = gmail.search
        tokens: list = []

        def answer(mailbox, q, *, page_token, page_size):
            if "subject:" in q:
                tokens.append(page_token)
                if page_token == "stale":
                    raise gmail.GmailError(f"Gmail search failed ({status}).", status)
            return search(mailbox, q, page_token=page_token, page_size=page_size)

        monkeypatch.setattr(gmail, "search", answer)
        _sync(queue)
        assert tokens == ["stale", None]
        counts = _latest_run(fake)["counts"]
        assert counts["invite_rescan_restarted"] == 1 and counts["invite_rescan_failed"] == 0
        # Read again from the start: the first page, its token saved.
        assert counts["invites_rescanned"] == 1
        assert row["invite_rescan_token"] == "2"
