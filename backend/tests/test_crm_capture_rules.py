"""Automatic email capture, the pure rules — spec 010 FR-CAP-SRC-01 to -08,
FR-CAP-CHG-01, -03, FR-CAP-REV-01 (`deal_signal`), -04; data-model §3.1;
plan §5.3 to §5.5, §5.10, PD-2, PD-3, PD-5, PD-19. S2 (tasks T212):
FR-CAP-DIS-01, -04, -05, -07, -09, FR-CAP-RPT-03; data-model §3.2, §3.4;
plan §5.6, §5.7, PD-9, PD-11, PD-17, PD-20. S3 (tasks T313): FR-CAP-DIS-03,
-07 to -09, FR-CAP-REV-01, -03, -04, FR-CAP-RPT-04; contracts §6; plan §5.8,
§5.10, PD-4.

No I/O: labels, headers, rows and the clock in, decisions out.
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# S2 — discovery (tasks T212)
# ---------------------------------------------------------------------------


class TestTheCaptureSetting:
    """FR-CAP-DIS-01; data-model §3.4 — `{"mode": off | dry_run | live}`."""

    @pytest.mark.parametrize("mode", ["off", "dry_run", "live"])
    def test_capture_each_mode_is_accepted(self, mode):
        assert capture.capture_refusal({"mode": mode}) is None
        assert capture.capture_mode({"mode": mode}) == mode

    @pytest.mark.parametrize(
        "value",
        [None, [], {}, {"mode": "on"}, {"mode": "off", "extra": 1}, {"enabled": True}, "off"],
    )
    def test_capture_anything_else_is_refused(self, value):
        assert capture.capture_refusal(value) == "Email capture is off, dry run or live."

    @pytest.mark.parametrize("value", [None, {}, {"mode": "everything"}])
    def test_capture_a_missing_or_broken_setting_reads_off(self, value):
        assert capture.capture_mode(value) == "off"

    def test_capture_the_setting_has_a_validator(self):
        from app.domain.crm import settings

        assert settings.setting_refusal("capture", {"mode": "dry_run"}) is None
        assert settings.setting_refusal("capture", {"mode": "x"}) == capture.MODE_REFUSAL


def _msg(mid, sender, to, at, direction="inbound", attachments=(), name=""):
    info = rules.classify(
        _h(From=f"{name} <{sender}>" if name else sender, To=to),
        ["nunnarilabs.com"],
        int(at.timestamp() * 1000),
    )
    return {**info, "id": mid, "attachments": list(attachments)}


T0 = datetime(2026, 10, 1, 3, 42, tzinfo=UTC)  # 09:12 IST
OWNER_BOX = "navaneeth@nunnarilabs.com"


class TestTheConversation:
    """FR-CAP-DIS-04 — who is outside, and whether the owner replied."""

    def test_capture_outside_participants_once_in_order(self):
        msgs = [
            _msg("m1", "priya@acme.com", f"{OWNER_BOX}, ravi@acme.com", T0),
            _msg("m2", OWNER_BOX, "priya@acme.com", T0 + timedelta(hours=1)),
        ]
        assert [p["email"] for p in capture.outside_of(msgs)] == ["priya@acme.com", "ravi@acme.com"]

    def test_capture_owner_replied_only_when_the_owner_wrote(self):
        inbound = _msg("m1", "priya@acme.com", OWNER_BOX, T0)
        colleague = _msg("m2", "deepika@nunnarilabs.com", "priya@acme.com", T0)
        owner = _msg("m3", OWNER_BOX, "priya@acme.com", T0)
        assert capture.owner_replied([inbound, colleague], OWNER_BOX) is False
        assert capture.owner_replied([inbound, owner], OWNER_BOX.upper()) is True

    def test_capture_webmail_names_no_company_domain(self):
        assert capture.domain_for("priya@gmail.com") is None
        assert capture.domain_for("priya@Acme.com") == "acme.com"
        assert capture.domain_for("acme.io") == "acme.io"


class TestTrim:
    """FR-CAP-DIS-04, PD-9 — a message's own text and 20 lines of the rest."""

    def test_capture_keeps_a_forwarded_inquiry(self):
        text = (
            "See below, can you help them?\n\n"
            "---------- Forwarded message ----------\n"
            "From: Priya <priya@acme.com>\nSubject: Pricing\n\n"
            "We need a quote for 40 seats."
        )
        trimmed = capture.trim(text)
        assert trimmed.startswith("See below") and "We need a quote for 40 seats." in trimmed

    def test_capture_keeps_at_most_twenty_quoted_lines(self):
        quoted = "\n".join(f"> line {i}" for i in range(1, 40))
        trimmed = capture.trim(f"Thanks, sending it today.\n\nOn Mon, X wrote:\n{quoted}")
        lines = trimmed.split("\n")
        assert lines[0] == "Thanks, sending it today."
        assert "> line 19" in lines and "> line 20" not in lines
        assert len(lines) == 2 + 20

    def test_capture_the_signature_counts_toward_the_twenty(self):
        sig = "\n".join(f"Sig {i}" for i in range(1, 26))
        trimmed = capture.trim(f"Body here.\n-- \n{sig}\n> quoted")
        lines = trimmed.split("\n")
        assert lines[0] == "Body here." and len(lines) == 1 + 20
        assert "> quoted" not in trimmed

    def test_capture_a_plain_message_is_kept_whole(self):
        assert capture.trim("  Hello there.\r\nTwo lines.  ") == "Hello there.\nTwo lines."


class TestThePrompt:
    """FR-CAP-DIS-04 — 12 messages, 24,000 characters, websites, the reply."""

    def test_capture_parts_oldest_first_then_websites_then_reply(self):
        msgs = [
            _msg(
                "m2",
                OWNER_BOX,
                "priya@acme-robotics.com",
                T0 + timedelta(days=1),
                attachments=["Nunnari pricing.pdf"],
            ),
            _msg("m1", "priya@acme-robotics.com", OWNER_BOX, T0, name="Priya Shah"),
        ]
        parts = capture.prompt_parts(
            msgs,
            {"m1": "Pricing please?", "m2": "Attached."},
            websites=["acme-robotics.com"],
            replied=True,
        )
        assert [label for label, _ in parts] == [
            "Message 1 of 2 inbound",
            "Message 2 of 2 outbound",
            "Websites in this conversation",
            "Mailbox owner replied",
        ]
        assert parts[0][1] == (
            "inbound · from Priya Shah <priya@acme-robotics.com> · to navaneeth@nunnarilabs.com · "
            "2026-10-01 09:12 IST · attachments: none\n\nPricing please?"
        )
        assert "attachments: Nunnari pricing.pdf" in parts[1][1]
        assert parts[2] == ("Websites in this conversation", "1. acme-robotics.com")
        assert parts[3] == ("Mailbox owner replied", "yes")

    def test_capture_labels_survive_the_quoting(self):
        from app.domain.crm import ai_rules

        quoted = ai_rules.quote_untrusted("Message 12 of 12 outbound", "x")
        assert quoted.startswith('<data source="Message 12 of 12 outbound">')

    def test_capture_only_the_last_twelve_messages(self):
        msgs = [
            _msg(f"m{i:02}", "priya@acme.com", OWNER_BOX, T0 + timedelta(hours=i))
            for i in range(15)
        ]
        parts = capture.prompt_parts(
            msgs, {m["id"]: m["id"] for m in msgs}, websites=[], replied=False
        )
        bodies = [text.rsplit("\n\n", 1)[-1] for _, text in parts[:-2]]
        assert bodies == [f"m{i:02}" for i in range(3, 15)]
        assert parts[-2] == ("Websites in this conversation", "none")
        assert parts[-1] == ("Mailbox owner replied", "no")

    def test_capture_drops_the_oldest_until_24000_characters(self):
        msgs = [
            _msg(f"m{i}", "priya@acme.com", OWNER_BOX, T0 + timedelta(hours=i)) for i in range(3)
        ]
        texts = {"m0": "a" * 10_000, "m1": "b" * 10_000, "m2": "c" * 10_000}
        parts = capture.prompt_parts(msgs, texts, websites=[], replied=False)
        assert [label for label, _ in parts[:-2]] == [
            "Message 1 of 2 inbound",
            "Message 2 of 2 inbound",
        ]
        assert parts[0][1].endswith("b" * 10_000)

    def test_capture_cuts_one_long_message_at_the_end(self):
        msgs = [_msg("m0", "priya@acme.com", OWNER_BOX, T0)]
        parts = capture.prompt_parts(msgs, {"m0": "x" * 30_000}, websites=[], replied=False)
        assert parts[0][1].endswith("x" * 24_000) and "x" * 24_001 not in parts[0][1]


class TestTheWebsite:
    """FR-CAP-DIS-05, -09, PD-20 — chosen from the hosts found, never typed."""

    def test_capture_choices_are_participant_domains_then_text_hosts(self):
        outside = [{"email": "priya@acme-robotics.com"}, {"email": "ravi@gmail.com"}]
        texts = [
            "See https://www.acme-robotics.com/about and acme.io, notes.pdf, R.Priya",
            "nunnarilabs.com",
        ]
        assert capture.website_choices(outside, texts, ["nunnarilabs.com"]) == [
            "acme-robotics.com",
            "acme.io",
        ]

    def test_capture_at_most_ten_choices(self):
        text = " ".join(f"site{i}.com" for i in range(20))
        assert len(capture.website_choices([], [text], [])) == 10

    def test_capture_a_choice_by_number(self):
        outside = [{"email": "priya@acme.com"}]
        assert capture.domain_of_finding(1, ["acme.com", "acme.io"], "priya@acme.com", outside) == (
            "acme.com",
            False,
        )
        assert capture.domain_of_finding(2, ["acme.com", "acme.io"], "priya@acme.com", outside) == (
            "acme.io",
            True,
        )

    def test_capture_no_choice_falls_back_to_the_address_unless_webmail(self):
        outside = [{"email": "priya@acme.com"}]
        assert capture.domain_of_finding(None, [], "priya@acme.com", outside) == ("acme.com", False)
        assert capture.domain_of_finding(7, ["acme.com"], "priya@gmail.com", outside) == (
            None,
            False,
        )


class TestTheFinding:
    """FR-CAP-DIS-05, -06, -09; data-model §3.2 `finding`."""

    ANSWER = {
        "outcome": "new_lead",
        "confidence": "high",
        "company_name": "Acme Robotics",
        "person_name": "Priya Shah",
        "person_email": "not-in-thread@x.com",
        "person_title": "Head of Ops",
        "lead_stage": "qualified",
        "deal_stage": None,
        "website_choice": 1,
        "reason": "Asked for pricing; you replied.",
    }

    def _msgs(self):
        return [
            _msg("m1", "priya@acme-robotics.com", OWNER_BOX, T0, attachments=["brief.pdf"]),
            _msg(
                "m2",
                OWNER_BOX,
                "priya@acme-robotics.com",
                T0 + timedelta(days=1),
                attachments=["brief.pdf", "quote.pdf"],
            ),
        ]

    def test_capture_a_lead_keeps_its_finding(self):
        found = capture.finding_of(
            self.ANSWER, self._msgs(), mailbox_email=OWNER_BOX, choices=["acme-robotics.com"]
        )
        assert found == {
            "company_name": "Acme Robotics",
            "domain": "acme-robotics.com",
            "domain_from_text": False,
            "person_name": "Priya Shah",
            # Not one of the thread's outside participants: the first sender.
            "person_email": "priya@acme-robotics.com",
            "person_title": "Head of Ops",
            "lead_stage": "qualified",
            "deal_stage": None,
            "reason": "Asked for pricing; you replied.",
            "first_at": "2026-10-01T09:12:00+05:30",
            "latest_at": "2026-10-02T09:12:00+05:30",
            "message_count": 2,
            "owner_replied": True,
            "attachments": ["brief.pdf", "quote.pdf"],
            "duplicate_company_id": None,
        }

    def test_capture_no_reply_suggests_new(self):
        msgs = self._msgs()[:1]
        found = capture.finding_of(self.ANSWER, msgs, mailbox_email=OWNER_BOX, choices=[])
        assert found["lead_stage"] == "new" and found["owner_replied"] is False

    def test_capture_other_outcomes_keep_nothing(self):
        for outcome in ("other_business", "not_relevant"):
            answer = {**self.ANSWER, "outcome": outcome}
            assert (
                capture.finding_of(answer, self._msgs(), mailbox_email=OWNER_BOX, choices=[])
                is None
            )

    def test_capture_a_finding_in_tamil_fits_its_check_in_bytes(self):
        # 071 holds a finding to 4096 bytes as stored; a Tamil letter is
        # three bytes in UTF-8, so character caps alone overflow it.
        tamil = "அ" * 300
        answer = {**self.ANSWER, "company_name": tamil, "person_name": tamil}
        answer["person_title"] = tamil
        answer["reason"] = tamil
        msgs = [
            _msg(
                "m1",
                "priya@acme-robotics.com",
                OWNER_BOX,
                T0,
                attachments=[f"{n}{tamil}.pdf" for n in range(10)],
            )
        ]
        found = capture.finding_of(answer, msgs, mailbox_email=OWNER_BOX, choices=[])
        assert capture.finding_bytes(found) <= capture.FINDING_BYTES < 4096
        assert len(json.dumps(found, ensure_ascii=False).encode()) <= 4096
        assert 0 < len(found["attachments"]) < 10
        assert found["attachments"][0].startswith("0")
        assert len(found["company_name"].encode()) <= 400

    def test_capture_lead_stage_defaults_to_contacted_once_replied(self):
        assert capture.lead_stage_for(None, True) == "contacted"
        assert capture.lead_stage_for("qualified", False) == "new"


class TestFailures:
    """PD-17 — which failures count against a conversation."""

    def test_capture_only_invalid_output_and_a_malformed_thread_count(self):
        from app.domain.crm import ai_rules
        from app.services.crm import ai, gmail
        from app.services.crm.capture import MalformedThread

        assert capture.failure_counts(ai.AiFailed(ai_rules.OUTPUT_INVALID, "s", "t")) is True
        assert capture.failure_counts(MalformedThread()) is True
        for reason in (ai_rules.CALL_FAILED, ai_rules.CALL_TIMED_OUT):
            assert capture.failure_counts(ai.AiFailed(reason, "s", "t")) is False
        assert capture.failure_counts(gmail.GmailError("Gmail thread failed (503).")) is False
        assert capture.failure_counts(gmail.NotConfigured("x")) is False


class TestRecall:
    """FR-CAP-RPT-03, PD-11 — a labelled conversation's fate, and the line."""

    def test_capture_the_fates_in_order(self):
        fate = capture.recall_fate
        assert fate(in_capture=False, survived=True, known_before=True) == "outside_scope"
        assert fate(in_capture=True, survived=False, known_before=True) == "skipped_rules"
        assert fate(in_capture=True, survived=True, known_before=True) == "filed_known"
        assert (
            fate(
                in_capture=True,
                survived=True,
                known_before=False,
                outcome="new_lead",
                confidence="medium",
            )
            == "found"
        )
        assert (
            fate(
                in_capture=True,
                survived=True,
                known_before=False,
                outcome="deal_signal",
                confidence="high",
            )
            == "found"
        )
        assert (
            fate(
                in_capture=True,
                survived=True,
                known_before=False,
                outcome="new_lead",
                confidence="low",
            )
            == "judged_otherwise"
        )
        assert (
            fate(
                in_capture=True,
                survived=True,
                known_before=False,
                outcome="other_business",
                confidence="high",
            )
            == "judged_otherwise"
        )
        assert fate(in_capture=True, survived=True, known_before=False) == "judged_otherwise"

    def test_capture_the_recall_line(self):
        rows = [
            {"fate": f}
            for f in ("filed_known", "found", "outside_scope", "judged_otherwise", "outside_scope")
        ]
        assert capture.recall_line(rows) == (
            "Found 2 of your 5 labelled conversations; 2 of the rest are outside Primary and Sent."
        )


FINDING = {
    "company_name": "Acme Robotics",
    "domain": "acme-robotics.com",
    "domain_from_text": False,
    "person_name": "Priya Shah",
    "person_email": "priya@acme-robotics.com",
    "person_title": "Head of Ops",
    "lead_stage": "contacted",
    "deal_stage": None,
    "reason": "Asked for pricing; you replied.",
    "first_at": "2026-10-01T09:12:00+05:30",
    "latest_at": "2026-10-02T11:40:00+05:30",
    "message_count": 2,
    "owner_replied": True,
    "attachments": ["Nunnari pricing.pdf"],
    "duplicate_company_id": None,
}
ITEM_ARGS = {
    "mailbox_id": "mb",
    "thread_id": "t1",
    "message_id": "m2",
    "owner_id": "u-owner",
    "model": "claude-sonnet-5-5",
    "generated_at": "2026-10-20T06:00:00+00:00",
}


class TestTheIgnoreList:
    """FR-CAP-DIS-03, REV-03; contracts §2.4."""

    IGNORES = [
        {"kind": "address", "value": "priya@acme.com"},
        {"kind": "domain", "value": "vendor.io"},
    ]

    def test_capture_every_outside_participant_must_be_ignored(self):
        assert capture.ignored([{"email": "sales@vendor.io"}], self.IGNORES)
        assert capture.ignored(
            [{"email": "priya@acme.com"}, {"email": "a@vendor.io"}], self.IGNORES
        )
        assert not capture.ignored(
            [{"email": "priya@acme.com"}, {"email": "ravi@acme.com"}], self.IGNORES
        )

    def test_capture_nobody_outside_is_not_ignored(self):
        assert not capture.ignored([], self.IGNORES)
        assert not capture.ignored([{"email": "a@vendor.io"}], [])

    def test_capture_the_value_is_trimmed_and_lower_cased(self):
        assert capture.ignore_value("  Sales@Vendor.IO ") == "sales@vendor.io"

    @pytest.mark.parametrize(
        ("kind", "value", "why"),
        [
            ("address", "priya@acme.com", None),
            ("address", "not-an-address", capture.ENTER_ADDRESS),
            ("address", "deepika@nunnarilabs.com", capture.OWN_DOMAIN),
            ("domain", "vendor.io", None),
            ("domain", "a@vendor.io", capture.ENTER_DOMAIN),
            ("domain", "vendor", capture.ENTER_DOMAIN),
            ("domain", "gmail.com", capture.WEBMAIL_IGNORE),
            ("domain", "nunnarilabs.com", capture.OWN_DOMAIN),
        ],
    )
    def test_capture_an_entry_is_refused_in_one_sentence(self, kind, value, why):
        assert capture.ignore_refusal(kind, value, ["nunnarilabs.com"]) == why


class TestDuplicates:
    """FR-CAP-DIS-08 — a sibling domain is the same name on another suffix."""

    def test_capture_sibling_domains_share_the_first_label(self):
        known = ["acme.com", "acme.io", "acmecorp.com", "other.io", "acme.com"]
        assert capture.sibling_domains("acme.io", known) == ["acme.com"]
        assert capture.sibling_domains("acme.co.uk", known) == ["acme.com", "acme.io"]
        assert capture.sibling_domains(None, known) == []
        assert capture.sibling_domains("acme", known) == []

    def test_capture_a_subdomain_has_no_sibling(self):
        # The first label of a subdomain names no company (S3-C5, S3-01).
        assert capture.sibling_domains("uk.sony.com", ["uk.fujitsu.com"]) == []
        assert capture.sibling_domains("mail.foo.com", ["mail.bar.io", "mail.foo.io"]) == []
        assert capture.sibling_domains("acme.com", ["acme.robotics.com", "mail.acme.io"]) == []
        assert capture.sibling_domains("acme.com.au", ["acme.co.uk", "co.uk"]) == ["acme.co.uk"]
        assert capture.registrable_name("acme.co.uk") == "acme"
        assert capture.registrable_name("uk.sony.com") is None


class TestItems:
    """Contracts §6.1, §6.2; plan §5.10, PD-4; FR-CAP-DIS-07, -09."""

    def test_capture_a_lead_makes_an_item_with_no_target(self):
        item = capture.to_item(FINDING, outcome="new_lead", confidence="high", **ITEM_ARGS)
        assert (item["type"], item["record_type"], item["record_id"]) == ("new_lead", None, None)
        assert item["dedupe_key"] == "email_thread|gmail:mb:t1|new_lead|thread"
        assert item["source_version"] == "m2" and item["source_key"] == "gmail:mb:t1"
        assert item["source_link"] == {"mailbox_id": "mb", "thread_id": "t1"}
        s = item["suggestion"]
        assert (s["company_name"], s["domain"], s["lead_stage"]) == (
            "Acme Robotics",
            "acme-robotics.com",
            "contacted",
        )
        assert (s["first_name"], s["last_name"], s["email"], s["title"]) == (
            "Priya",
            "Shah",
            "priya@acme-robotics.com",
            "Head of Ops",
        )
        assert s["link"] is False and s["deal_stage_hint"] is None
        assert s["evidence"] == {
            "first_at": "2026-10-01T09:12:00+05:30",
            "latest_at": "2026-10-02T11:40:00+05:30",
            "message_count": 2,
            "owner_replied": True,
            "attachments": ["Nunnari pricing.pdf"],
            "reason": "Asked for pricing; you replied.",
            "confidence": "high",
            "origin": "discovery",
        }
        assert item["owner_id"] == "u-owner" and item["deal_choices"] == []

    def test_capture_low_confidence_or_no_finding_makes_nothing(self):
        assert capture.to_item(FINDING, outcome="new_lead", confidence="low", **ITEM_ARGS) is None
        assert capture.to_item(None, outcome="new_lead", confidence="high", **ITEM_ARGS) is None
        assert (
            capture.to_item(FINDING, outcome="other_business", confidence="high", **ITEM_ARGS)
            is None
        )

    def test_capture_a_duplicate_links_the_lead(self):
        item = capture.to_item(
            FINDING,
            outcome="new_lead",
            confidence="medium",
            duplicate_company_id="c-1",
            **ITEM_ARGS,
        )
        assert (item["type"], item["record_type"], item["record_id"]) == (
            "new_lead",
            "company",
            "c-1",
        )
        assert item["dedupe_key"] == "email_thread|gmail:mb:t1|new_lead|company:c-1"
        assert item["suggestion"]["link"] is True

    def test_capture_a_webmail_lead_has_no_domain(self):
        webmail = {**FINDING, "domain": "gmail.com", "person_email": "priya@gmail.com"}
        item = capture.to_item(webmail, outcome="new_lead", confidence="high", **ITEM_ARGS)
        assert item["suggestion"]["domain"] is None
        assert item["suggestion"]["domain_from_text"] is False

    def test_capture_a_deal_signal_on_an_unknown_company_is_a_lead_with_a_hint(self):
        signal = {**FINDING, "deal_stage": "proposal"}
        item = capture.to_item(signal, outcome="deal_signal", confidence="high", **ITEM_ARGS)
        assert item["type"] == "new_lead"
        assert item["suggestion"]["deal_stage_hint"] == "proposal"

    def test_capture_a_deal_signal_on_a_matched_company_moves_its_open_deal(self):
        signal = {**FINDING, "deal_stage": "proposal"}
        deals = [
            {"id": "d-2", "stage": "qualification"},
            {"id": "d-1", "stage": "discovery"},
            {"id": "d-3", "stage": "negotiation"},
        ]
        item = capture.to_item(
            signal,
            outcome="deal_signal",
            confidence="high",
            duplicate_company_id="c-1",
            open_deals=deals,
            **ITEM_ARGS,
        )
        assert (item["type"], item["record_type"], item["record_id"]) == (
            "deal_signal",
            "company",
            "c-1",
        )
        assert item["deal_choices"] == ["d-1", "d-2"]
        assert item["suggestion"]["action"] == "move"
        assert item["suggestion"]["stage"] == "proposal"
        assert item["suggestion"]["proposal_sent_on"] == "2026-10-02"
        assert item["suggestion"]["evidence"]["origin"] == "discovery"
        assert item["dedupe_key"] == "email_thread|gmail:mb:t1|deal_signal|company:c-1"

    def test_capture_a_deal_signal_with_no_open_deal_creates_one_at_discovery(self):
        signal = {**FINDING, "deal_stage": "proposal"}
        item = capture.to_item(
            signal,
            outcome="deal_signal",
            confidence="high",
            duplicate_company_id="c-1",
            open_deals=[],
            **ITEM_ARGS,
        )
        assert item["suggestion"]["action"] == "create"
        assert item["suggestion"]["stage"] == "discovery"
        assert item["deal_choices"] == []

    def test_capture_a_deal_signal_past_every_open_deal_makes_nothing(self):
        signal = {**FINDING, "deal_stage": "proposal"}
        assert (
            capture.to_item(
                signal,
                outcome="deal_signal",
                confidence="high",
                duplicate_company_id="c-1",
                open_deals=[{"id": "d-3", "stage": "negotiation"}],
                **ITEM_ARGS,
            )
            is None
        )

    @pytest.mark.parametrize("stage", ["discovery", None])
    def test_capture_a_discovery_signal_on_a_company_with_an_open_deal_makes_nothing(self, stage):
        # S3-C2: `create` only for a company with no open deal; a second open
        # deal would be a duplicate.
        assert (
            capture.to_item(
                {**FINDING, "deal_stage": stage},
                outcome="deal_signal",
                confidence="high",
                duplicate_company_id="c-1",
                open_deals=[{"id": "d-1", "stage": "qualification"}],
                **ITEM_ARGS,
            )
            is None
        )

    def test_capture_a_discovered_signal_says_what_matched_the_company(self):
        # S3-PRIV-3: a match made through a website in the text says so.
        signal = {
            **FINDING,
            "deal_stage": "closed_won",
            "domain": "bigclient.com",
            "domain_from_text": True,
            "person_email": "x@unknown-evil.com",
        }
        item = capture.to_item(
            signal,
            outcome="deal_signal",
            confidence="high",
            duplicate_company_id="c-1",
            open_deals=[{"id": "d-1", "stage": "qualification"}],
            **ITEM_ARGS,
        )
        evidence = item["suggestion"]["evidence"]
        assert (evidence["domain"], evidence["domain_from_text"], evidence["email"]) == (
            "bigclient.com",
            True,
            "x@unknown-evil.com",
        )

    def test_capture_a_lead_with_no_company_name_falls_back_to_its_domain(self):
        item = capture.to_item(
            {**FINDING, "company_name": None}, outcome="new_lead", confidence="high", **ITEM_ARGS
        )
        assert item["suggestion"]["company_name"] == "acme-robotics.com"

    def test_capture_a_name_alone_or_an_address_alone_gives_a_first_name(self):
        assert capture.split_name("Priya Devi Shah", None) == ("Priya", "Devi Shah")
        assert capture.split_name(None, "priya@acme.com") == ("priya", None)

    def test_capture_what_today_calls_a_new_lead(self):
        item = capture.to_item(FINDING, outcome="new_lead", confidence="high", **ITEM_ARGS)
        assert capture.new_lead_title(item["suggestion"]) == "New lead: Acme Robotics · Priya Shah"
        assert capture.new_lead_detail(item["suggestion"]) == (
            "2 emails · Asked for pricing; you replied."
        )

    def test_capture_the_thread_dedupe_key(self):
        assert (
            review_rules.dedupe_key("email_thread", "gmail:mb:t1", "new_lead", None, None)
            == "email_thread|gmail:mb:t1|new_lead|thread"
        )
        assert review_rules.TYPES["new_lead"] == "New lead"

    def test_capture_a_new_lead_is_the_mailbox_owners(self):
        roles = {"u-owner": "admin", "u-viewer": "viewer"}
        active = {"u-owner", "u-viewer"}
        assert (
            review_rules.owner_for("new_lead", roles=roles, active=active, mailbox_owner="u-owner")
            == "u-owner"
        )
        assert (
            review_rules.owner_for("new_lead", roles=roles, active=active, mailbox_owner="u-viewer")
            is None
        )

    def test_capture_an_item_fits_the_suggestion_cap(self):
        long = {**FINDING, "attachments": ["x" * 200] * 10, "reason": "r" * 160}
        item = capture.to_item(long, outcome="new_lead", confidence="high", **ITEM_ARGS)
        assert len(json.dumps(item["suggestion"])) <= 8192


class TestApplying:
    """FR-CAP-RPT-04; contracts §2.3 — when a dry run can be applied."""

    RUN = {
        "id": "r1",
        "finished_at": (NOW - timedelta(days=1)).isoformat(),
        "cancelled": False,
        "applied_at": None,
    }

    @pytest.mark.parametrize(
        ("change", "latest", "refused"),
        [
            ({}, "r1", None),
            ({"finished_at": None}, "r1", (422, capture.NOT_FINISHED)),
            ({"cancelled": True}, "r1", (422, capture.STOPPED)),
            ({"applied_at": NOW.isoformat()}, "r1", (409, capture.ALREADY_APPLIED)),
            ({}, "r2", (422, capture.NOT_LATEST)),
            (
                {"finished_at": (NOW - timedelta(days=7, minutes=1)).isoformat()},
                "r1",
                (422, capture.TOO_OLD),
            ),
        ],
    )
    def test_capture_apply_refusals(self, change, latest, refused):
        assert capture.apply_refusal({**self.RUN, **change}, latest, NOW) == refused

    def test_capture_an_unknown_run_is_not_found(self):
        assert capture.apply_refusal(None, "r1", NOW) == (404, capture.NO_SUCH_RUN)
