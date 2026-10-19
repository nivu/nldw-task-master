"""Automatic email capture, the pure rules — spec 010 FR-CAP-SRC-01 to -08,
FR-CAP-CHG-01, -03, FR-CAP-REV-01 (`deal_signal`), -04; data-model §3.1;
plan §5.3 to §5.5, §5.10, PD-2, PD-3, PD-5, PD-19.

No I/O: labels, headers, rows and the clock in, decisions out.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.crm import capture
from app.domain.crm import email_rules as rules
from app.domain.crm import review as review_rules

NOW = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)
IST = capture.ZONE


def _h(**headers: str) -> list[dict[str, str]]:
    return [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]


class TestScope:
    """FR-CAP-SRC-02, -08, CHG-01 — what a mailbox scans."""

    def test_capture_labels_only_scans_the_configured_labels(self):
        assert capture.scan_labels(["Label_1", "Label_2"], "labels") == ["Label_1", "Label_2"]

    def test_capture_primary_sent_adds_primary_and_sent_after_the_labels(self):
        assert capture.scan_labels(["Label_1"], "primary_sent") == [
            "Label_1",
            "CATEGORY_PERSONAL",
            "SENT",
        ]

    def test_capture_scans_each_label_once(self):
        assert capture.scan_labels(["Label_1", "Label_1"], "primary_sent") == [
            "Label_1",
            "CATEGORY_PERSONAL",
            "SENT",
        ]

    def test_capture_scans_primary_and_sent_with_no_configured_label_left(self):
        assert capture.scan_labels([], "primary_sent") == ["CATEGORY_PERSONAL", "SENT"]

    @pytest.mark.parametrize(
        ("labels", "expected"),
        [
            (["INBOX", "CATEGORY_PERSONAL"], True),
            (["SENT"], True),
            (["Label_1", "CATEGORY_PROMOTIONS"], True),
            (["INBOX", "CATEGORY_PROMOTIONS"], False),
            (["CATEGORY_PERSONAL", "TRASH"], False),
            (["SENT", "SPAM"], False),
            (["Label_1", "TRASH"], False),
            ([], False),
        ],
    )
    def test_capture_in_scope_needs_a_scanned_label_and_no_bin(self, labels, expected):
        scan = ["Label_1", "CATEGORY_PERSONAL", "SENT"]
        assert capture.in_scope(labels, scan) is expected


class TestTheWindow:
    """FR-CAP-SRC-07, CHG-03; PD-3."""

    def test_capture_after_is_the_start_date_at_midnight_ist(self):
        mailbox = {"capture_source": "primary_sent", "capture_start_date": "2026-04-01"}
        assert capture.capture_after(mailbox, NOW) == datetime(2026, 4, 1, tzinfo=IST)

    def test_capture_after_defaults_to_the_first_of_april(self):
        mailbox = {"capture_source": "primary_sent", "capture_start_date": None}
        assert capture.capture_after(mailbox, NOW) == datetime(2026, 4, 1, tzinfo=IST)

    def test_capture_labels_only_keeps_ninety_days(self):
        mailbox = {"capture_source": "labels", "capture_start_date": "2026-04-01"}
        assert capture.capture_after(mailbox, NOW) == rules.backfill_after(NOW)

    def test_capture_reconcile_window_is_the_capture_window_by_default(self):
        mailbox = {"capture_source": "labels", "reconcile_after": None}
        assert capture.reconcile_window(mailbox, NOW) == NOW - timedelta(days=90)

    def test_capture_reconcile_window_reaches_back_after_a_switch_back(self):
        back = datetime(2026, 4, 1, tzinfo=IST)
        mailbox = {"capture_source": "labels", "reconcile_after": back.isoformat()}
        assert capture.reconcile_window(mailbox, NOW) == back

    def test_capture_reconcile_window_never_narrows(self):
        mailbox = {
            "capture_source": "labels",
            "reconcile_after": (NOW - timedelta(days=10)).isoformat(),
        }
        assert capture.reconcile_window(mailbox, NOW) == NOW - timedelta(days=90)

    def test_capture_reconcile_bounds_keep_the_margin_off_the_moving_edge(self):
        mailbox = {"capture_source": "labels", "reconcile_after": None}
        edge = NOW - timedelta(days=90)
        assert capture.reconcile_bounds(mailbox, NOW) == (edge, edge + timedelta(days=1))

    def test_capture_reconcile_bounds_compare_from_the_old_start_date_itself(self):
        """PD-3: mail stored on the old start date is compared too."""
        back = datetime(2026, 4, 1, tzinfo=IST)
        mailbox = {"capture_source": "labels", "reconcile_after": back.isoformat()}
        assert capture.reconcile_bounds(mailbox, NOW) == (back - timedelta(days=1), back)

    def test_capture_reconcile_bounds_compare_from_the_start_date_while_capturing(self):
        mailbox = {"capture_source": "primary_sent", "capture_start_date": "2026-04-01"}
        start = datetime(2026, 4, 1, tzinfo=IST)
        assert capture.reconcile_bounds(mailbox, NOW) == (start - timedelta(days=1), start)


class TestTheSkip:
    """FR-CAP-SRC-03; PD-2 — bulk and automated mail."""

    @pytest.mark.parametrize(
        "headers",
        [
            _h(List_Unsubscribe="<mailto:unsub@news.example>"),
            _h(Precedence="bulk"),
            _h(Precedence="list"),
            _h(Precedence="junk"),
            _h(Precedence="auto_reply"),
            _h(Auto_Submitted="auto-generated"),
            _h(X_Autoreply="yes"),
            _h(X_Autorespond="yes"),
            _h(Content_Type="multipart/report; report-type=delivery-status"),
        ],
    )
    def test_capture_bulk_headers_are_skipped(self, headers):
        assert capture.is_bulk(headers, "news@vendor.example")

    @pytest.mark.parametrize(
        "sender",
        [
            "noreply@vendor.example",
            "no-reply@vendor.example",
            "donotreply@vendor.example",
            "do-not-reply@vendor.example",
            "notifications@github.com",
            "notification@vendor.example",
            "mailer-daemon@googlemail.com",
            "postmaster@vendor.example",
            "noreply+billing@vendor.example",
            "calendar-notification@google.com",
        ],
    )
    def test_capture_no_reply_senders_are_skipped(self, sender):
        assert capture.is_bulk(_h(From=sender), sender)

    @pytest.mark.parametrize(
        "subject",
        [
            "Invitation: Demo @ Tue 21 Oct",
            "Updated invitation: Demo",
            "Accepted: Demo",
            "Declined: Demo",
            "Tentatively accepted: Demo",
            "Canceled event: Demo",
            "Cancelled event: Demo",
        ],
    )
    def test_capture_calendar_invitations_are_skipped(self, subject):
        assert capture.is_bulk(_h(From="priya@acme.com", Subject=subject), "priya@acme.com")

    def test_capture_a_person_writing_is_not_bulk(self):
        headers = _h(
            From="Priya <priya@acme.com>",
            Subject="Re: pricing",
            Auto_Submitted="no",
            Precedence="first-class",
        )
        assert not capture.is_bulk(headers, "priya@acme.com")

    def test_capture_reads_the_sender_from_the_headers_when_not_given(self):
        assert capture.is_bulk(_h(From="No Reply <noreply@vendor.example>"))

    def test_capture_a_local_part_that_only_contains_notify_is_a_person(self):
        assert not capture.is_bulk(_h(From="notifyme@acme.com"), "notifyme@acme.com")

    def test_capture_asks_gmail_for_list_unsubscribe(self):
        assert "List-Unsubscribe" in rules.HEADERS
        for name in ("Precedence", "Auto-Submitted", "Content-Type"):
            assert name in rules.HEADERS


class TestDealDocuments:
    """FR-CAP-SRC-06; E8; PD-19."""

    def test_capture_one_query_per_kind(self):
        assert list(capture.DEAL_DOC_QUERIES) == [
            "sow",
            "proposal",
            "quotation",
            "msa",
            "purchase_order",
        ]
        assert 'filename:"statement of work"' in capture.DEAL_DOC_QUERIES["sow"]

    def test_capture_deal_doc_scope_is_primary_sent_and_labels_never_bins(self):
        assert capture.deal_doc_scope(["Sales", 'Le"ads']) == (
            '(category:primary OR in:sent OR label:"Sales" OR label:"Leads") -in:trash -in:spam'
        )

    def test_capture_deal_doc_query_puts_it_together(self):
        scope = capture.deal_doc_scope(["Sales"])
        assert capture.deal_doc_query("msa", 1_790_000_000, scope) == (
            f"has:attachment after:1790000000 {scope} (filename:msa)"
        )

    def test_capture_the_first_search_looks_back_fourteen_days(self):
        since = capture.deal_doc_since(None, date(2026, 4, 1), NOW)
        assert since == NOW - timedelta(days=14)

    def test_capture_the_first_search_never_starts_before_the_start_date(self):
        since = capture.deal_doc_since(None, "2026-10-15", NOW)
        assert since == datetime(2026, 10, 15, tzinfo=IST)

    def test_capture_a_later_search_starts_at_the_last_check(self):
        checked = (NOW - timedelta(hours=1)).isoformat()
        assert capture.deal_doc_since(checked, "2026-04-01", NOW) == NOW - timedelta(hours=1)

    @pytest.mark.parametrize(
        ("direction", "stage", "expected"),
        [
            ("outbound", "discovery", "proposal"),
            ("outbound", "qualification", "proposal"),
            ("outbound", "proposal", None),
            ("outbound", "negotiation", None),
            ("inbound", "discovery", "closed_won"),
            ("inbound", "negotiation", "closed_won"),
            ("inbound", "closed_won", None),
            ("inbound", "closed_lost", None),
        ],
    )
    def test_capture_direction_decides_the_stage(self, direction, stage, expected):
        assert capture.deal_doc_stage(direction, stage) == expected

    def test_capture_only_deals_created_on_or_before_the_document(self):
        at = datetime(2026, 10, 10, 12, tzinfo=UTC)
        deals = [
            {"id": "old", "created_at": "2026-09-01T00:00:00+00:00"},
            {"id": "same", "created_at": at.isoformat()},
            {"id": "new", "created_at": "2026-10-11T00:00:00+00:00"},
        ]
        assert [d["id"] for d in capture.deal_doc_deals(deals, at)] == ["old", "same"]


class TestDealSignalItem:
    """FR-CAP-REV-01; contracts §6.2."""

    def _item(self, **overrides):
        values = {
            "mailbox_id": "mb",
            "thread_id": "t9",
            "message_id": "m9",
            "company_id": "co",
            "deals": [{"id": "d2"}, {"id": "d1"}],
            "stage": "proposal",
            "kinds": ["sow"],
            "direction": "outbound",
            "message_at": datetime(2026, 10, 20, 9, 32, tzinfo=UTC),
            "owner_id": "u-owner",
            "generated_at": NOW.isoformat(),
        }
        return capture.deal_signal_item(**{**values, **overrides})

    def test_capture_a_sent_sow_suggests_proposal_sent_that_day(self):
        item = self._item()
        assert item["type"] == "deal_signal"
        assert (item["record_type"], item["record_id"]) == ("company", "co")
        assert item["deal_choices"] == ["d1", "d2"]
        assert item["source_kind"] == "email_thread"
        assert item["source_key"] == "gmail:mb:t9"
        assert item["source_version"] == "m9"
        assert item["source_link"] == {"mailbox_id": "mb", "thread_id": "t9"}
        assert item["dedupe_key"] == "email_thread|gmail:mb:t9|deal_signal|company:co"
        assert item["model"] is None
        suggestion = item["suggestion"]
        assert suggestion["action"] == "move" and suggestion["stage"] == "proposal"
        assert suggestion["proposal_sent_on"] == "2026-10-20"
        evidence = suggestion["evidence"]
        assert evidence["origin"] == "deal_document"
        assert evidence["document_kinds"] == ["sow"]
        assert evidence["direction"] == "outbound" and evidence["owner_replied"] is True
        assert evidence["message_count"] == 1 and evidence["attachments"] == []
        assert evidence["reason"] == "SOW sent to the client."
        assert evidence["confidence"] is None

    def test_capture_proposal_sent_on_is_the_ist_day(self):
        late = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)  # 01:30 on the 20th in IST
        assert self._item(message_at=late)["suggestion"]["proposal_sent_on"] == "2026-10-20"

    def test_capture_a_received_document_asks_to_check_for_closed_won(self):
        item = self._item(stage="closed_won", direction="inbound", kinds=["sow", "msa"])
        suggestion = item["suggestion"]
        assert suggestion["stage"] == "closed_won" and "proposal_sent_on" not in suggestion
        assert suggestion["evidence"]["reason"] == (
            "SOW and MSA received. Check the signed document."
        )
        # The thread is not read, so whether the owner replied is not said.
        assert suggestion["evidence"]["owner_replied"] is None

    def test_capture_titles_say_what_the_signal_would_do(self):
        assert (
            capture.deal_signal_title(self._item()["suggestion"])
            == "Move to Proposal, proposal sent on 20 Oct"
        )
        won = self._item(stage="closed_won", direction="inbound")["suggestion"]
        assert capture.deal_signal_title(won) == "Possibly Closed Won: check the signed document"
        assert capture.deal_signal_title({"action": "create", "stage": "discovery"}) == (
            "New deal at Discovery"
        )


class TestTheMailboxSetting:
    """FR-CAP-SRC-01, -07 — contracts §1.1."""

    def _refusal(self, start, today=date(2026, 10, 20)):
        return rules.mailbox_refusal(
            email="owner@nunnarilabs.com",
            owner_email="owner@nunnarilabs.com",
            owner_is_member=True,
            by_tva_admin=False,
            labels=["Sales"],
            capture_start_date=start,
            today=today,
        )

    def test_capture_start_date_today_or_before_is_accepted(self):
        assert self._refusal(date(2026, 10, 20)) is None
        assert self._refusal(date(2026, 4, 1)) is None

    def test_capture_start_date_in_the_future_is_refused(self):
        assert self._refusal(date(2026, 10, 21)) == capture.START_IN_FUTURE
        assert capture.START_IN_FUTURE == "The capture start date cannot be in the future."

    def test_capture_labels_are_still_required(self):
        assert (
            rules.mailbox_refusal(
                email="owner@nunnarilabs.com",
                owner_email="owner@nunnarilabs.com",
                owner_is_member=True,
                by_tva_admin=False,
                labels=[],
                capture_start_date=date(2026, 4, 1),
                today=date(2026, 10, 20),
            )
            == rules.NO_LABELS
        )


class TestTheQueue:
    """FR-CAP-REV-01, -04; PD-5."""

    def test_capture_deal_signal_is_a_review_type(self):
        assert review_rules.TYPES["deal_signal"] == "Deal signal"

    @pytest.mark.parametrize("kind", ["new_lead", "deal_signal"])
    def test_capture_items_belong_to_the_mailbox_owner(self, kind):
        roles = {"u-owner": "member", "u-deal": "admin"}
        active = {"u-owner", "u-deal"}
        assert (
            review_rules.owner_for(
                kind,
                roles=roles,
                active=active,
                mailbox_owner="u-owner",
                deal_owner="u-deal",
                company_owner="u-deal",
            )
            == "u-owner"
        )

    def test_capture_an_ineligible_mailbox_owner_sends_it_to_the_admins(self):
        assert (
            review_rules.owner_for(
                "deal_signal",
                roles={"u-owner": "viewer"},
                active={"u-owner"},
                mailbox_owner="u-owner",
            )
            is None
        )
