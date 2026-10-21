"""Automatic email capture, the pure rules — spec 010 FR-CAP-SRC-01 to -08,
FR-CAP-CHG-01, -03, FR-CAP-REV-01 (`deal_signal`), -04; data-model §3.1;
plan §5.3 to §5.5, §5.10, PD-2, PD-3, PD-5, PD-19. S2 (tasks T212):
FR-CAP-DIS-01, -04, -05, -07, -09, FR-CAP-RPT-03; data-model §3.2, §3.4;
plan §5.6, §5.7, PD-9, PD-11, PD-17, PD-20. S3 (tasks T313): FR-CAP-DIS-03,
-07 to -09, FR-CAP-REV-01, -03, -04, FR-CAP-RPT-04; contracts §6; plan §5.8,
§5.10, PD-4. R1 (tasks T404, T415): FR-CAP-INV-01, -03 (invitations and
calendar notices, `Reply-To`), FR-CAP-GRP-01, -03, -06, -08 (company keys,
conversation entries, the consolidation plan); plan R1.2, R1.5, PD-22,
PD-33, PD-40. R1 (tasks T421, T424): FR-CAP-RPT-05 to -07, -10 (the recall
fates, summary and line); plan R1.3, PD-27, PD-28. R1 (tasks T442, T445):
FR-CAP-ACAD-01, -03, -04, -05 (the business answer, the Academy's switch,
the Academy item, its note); contracts §7.3, §7.4; plan R1.4, PD-29, PD-30.
R1 (tasks T461): FR-CAP-INV-04, -05 (meeting hosts, the invitation prompt
part, the rescan query); plan R1.5, PD-34.

No I/O: labels, headers, rows and the clock in, decisions out.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.crm import capture
from app.domain.crm import email_rules as rules
from app.domain.crm import review as review_rules
from app.domain.crm import settings as settings_rules
from app.schemas.crm import DiscoveryOutput

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
        # Spec 010 R1 (FR-CAP-INV-01): with no outside party given, an
        # invitation is still bulk, as before; the invitation rules follow.
        assert capture.is_bulk(_h(From="priya@acme.com", Subject=subject), "priya@acme.com")


OUTSIDER = [{"email": "priya@acme.com", "name": "Priya"}]
CALENDAR = "calendar-notification@google.com"


class TestInvitations:
    """Spec 010 R1 FR-CAP-INV-01, -03; plan R1.5, PD-33 — an invitation with
    someone outside is mail; notices and colleagues' invitations are not."""

    @pytest.mark.parametrize(
        "subject",
        [
            "Invitation: Demo @ Tue 21 Oct",
            "Updated invitation: Demo",
            "Updated invitation with note: Demo",
            "Invitation from Google Calendar: Demo",
        ],
    )
    def test_capture_an_invitation_with_an_outsider_is_not_bulk(self, subject):
        headers = _h(From="Priya <priya@acme.com>", Subject=subject)
        assert capture.is_invitation(headers)
        assert not capture.is_bulk(headers, "priya@acme.com", OUTSIDER)

    def test_capture_an_invitation_from_google_calendar_with_an_outsider_is_not_bulk(self):
        headers = _h(
            From=f"Google Calendar <{CALENDAR}>",
            Subject="Invitation: Demo",
            Auto_Submitted="auto-generated",
        )
        assert not capture.is_bulk(headers, CALENDAR, OUTSIDER)

    def test_capture_an_internal_invitation_is_bulk(self):
        headers = _h(From="colleague@nunnarilabs.com", Subject="Invitation: Standup")
        assert capture.is_bulk(headers, "colleague@nunnarilabs.com", [])
        assert capture.is_calendar_notice(headers, "colleague@nunnarilabs.com", [])

    @pytest.mark.parametrize(
        "subject",
        [
            "Accepted: Demo",
            "Declined: Demo",
            "Tentatively accepted: Demo",
            "Canceled event: Demo",
            "Cancelled event: Demo",
        ],
    )
    def test_capture_each_calendar_notice_is_bulk(self, subject):
        headers = _h(From="priya@acme.com", Subject=subject)
        assert capture.is_bulk(headers, "priya@acme.com", OUTSIDER)
        assert capture.is_calendar_notice(headers, "priya@acme.com", OUTSIDER)

    R1_NOTICES = [
        "Canceled event with note: Demo",
        "Cancelled event with note: Demo",
        "Proposed new time: Demo",
        "Notification: Demo @ 10am",
    ]

    @pytest.mark.parametrize("subject", R1_NOTICES)
    def test_capture_each_r1_calendar_notice_from_google_calendar_is_bulk(self, subject):
        headers = _h(From=f"Google Calendar <{CALENDAR}>", Subject=subject)
        assert capture.is_bulk(headers, CALENDAR, OUTSIDER)
        assert capture.is_calendar_notice(headers, CALENDAR, OUTSIDER)

    @pytest.mark.parametrize(
        "subject", [*R1_NOTICES, "Notification: PO 4411 issued", "Notification: billing contact"]
    )
    def test_capture_an_r1_notice_subject_from_a_person_is_mail(self, subject):
        """Review R1-X2: the subjects R1 added are a notice only from Google
        Calendar's sender; from a person they keep their pre-074 handling."""
        headers = _h(From="Priya <priya@acme.com>", Subject=subject)
        assert not capture.is_bulk(headers, "priya@acme.com", OUTSIDER)
        assert not capture.is_calendar_notice(headers, "priya@acme.com", OUTSIDER)

    def test_capture_anything_else_from_google_calendar_is_bulk(self):
        headers = _h(From=CALENDAR, Subject="Your agenda for today")
        assert capture.is_bulk(headers, CALENDAR, OUTSIDER)
        assert capture.is_calendar_notice(headers, CALENDAR, OUTSIDER)

    def test_capture_google_calendar_is_never_the_outside_party(self):
        headers = _h(From=CALENDAR, Subject="Invitation: Demo")
        calendar_only = [{"email": CALENDAR, "name": "Google Calendar"}]
        assert capture.invite_parties(headers, CALENDAR, calendar_only) == []
        assert capture.is_bulk(headers, CALENDAR, calendar_only)

    def test_capture_an_invitation_s_outside_people_leave_out_google_calendar(self):
        # FR-CAP-INV-01: every later step (matching, the ignore list, the
        # finding's person, grouping) sees the organiser only.
        headers = _h(From=CALENDAR, Subject="Invitation: Demo", Reply_To="lee@webinarco.io")
        outside = [{"email": CALENDAR, "name": ""}, {"email": "lee@webinarco.io", "name": ""}]
        assert capture.outside_people(headers, outside) == [outside[1]]
        assert capture.ignored(
            capture.outside_people(headers, outside), [{"kind": "domain", "value": "webinarco.io"}]
        )
        # Any other message's participants are left as they are.
        other = _h(From="priya@acme.com", Subject="Pricing")
        assert capture.outside_people(other, outside) == outside

    def test_capture_a_meeting_host_address_gives_no_domain(self):
        # FR-CAP-INV-04: a meeting host is never a finding's domain.
        assert capture.domain_of_finding(None, [], "someone@google.com", []) == (None, False)
        assert capture.domain_of_finding(None, [], "lee@prospect.io", []) == (
            "prospect.io",
            False,
        )

    def test_capture_a_meeting_host_domain_is_never_a_company_key(self):
        # FR-CAP-GRP-01, FR-CAP-INV-04: never grouped under a meeting host.
        key = capture.company_key({"domain": "google.com", "person_email": "lee@prospect.io"})
        assert key == "capture:email:lee@prospect.io"

    def test_capture_the_reply_to_organiser_of_a_calendar_invitation_counts(self):
        headers = _h(
            From=f"Google Calendar <{CALENDAR}>",
            To="owner@nunnarilabs.com",
            Reply_To="Priya <priya@acme.com>",
            Subject="Invitation: Demo",
        )
        info = rules.classify(headers, ["nunnarilabs.com"], "1790000000000")
        assert {"email": "priya@acme.com", "name": "Priya"} in info["outside"]
        assert [p["role"] for p in info["participants"]] == ["from", "to", "reply_to"]
        assert not capture.is_bulk(headers, info["from_email"], info["outside"])

    def test_capture_reply_to_is_read_only_for_a_calendar_invitation(self):
        person = _h(From="sam@nunnarilabs.com", Reply_To="priya@acme.com", Subject="Hello")
        assert rules.classify(person, ["nunnarilabs.com"], "1790000000000")["outside"] == []
        notice = _h(From=CALENDAR, Reply_To="priya@acme.com", Subject="Accepted: Demo")
        info = rules.classify(notice, ["nunnarilabs.com"], "1790000000000")
        assert "priya@acme.com" not in {p["email"] for p in info["participants"]}

    @pytest.mark.parametrize(
        "headers",
        [
            _h(
                From="events@vendor.example",
                Subject="Invitation: Webinar",
                List_Unsubscribe="<mailto:u@vendor.example>",
            ),
            _h(From="no-reply@vendor.example", Subject="Invitation: Webinar"),
            _h(From="events@vendor.example", Subject="Invitation: Webinar", Precedence="bulk"),
            _h(
                From="events@vendor.example",
                Subject="Invitation: Webinar",
                Auto_Submitted="auto-generated",
            ),
        ],
    )
    def test_capture_an_invitation_with_another_bulk_sign_is_still_bulk(self, headers):
        sender = rules.classify(headers, ["nunnarilabs.com"], "1790000000000")["from_email"]
        outside = [{"email": sender, "name": ""}]
        assert capture.is_bulk(headers, sender, outside)

    def test_capture_a_reply_to_an_invitation_is_a_person_writing(self):
        headers = _h(From="priya@acme.com", Subject="Re: Invitation: Demo")
        assert not capture.is_invitation(headers)
        assert not capture.is_bulk(headers, "priya@acme.com", OUTSIDER)

    def test_capture_asks_gmail_for_reply_to(self):
        assert "Reply-To" in rules.HEADERS

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

    def test_capture_invite_rescan_query(self):
        # Spec 010 R1 FR-CAP-INV-05, PD-34: ids since the start date, in the
        # deal documents' scope, five pages of 100 a sync.
        scope = capture.deal_doc_scope(["Sales"])
        assert capture.invite_rescan_query(1_775_000_000, scope) == (
            f"after:1775000000 {scope} subject:invitation"
        )
        assert (capture.INVITE_RESCAN_PAGES, capture.INVITE_RESCAN_PAGE_SIZE) == (5, 100)

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
        # Spec 010 R1 (FR-CAP-GRP-06, contracts §7.4): one signal per company.
        assert item["source_key"] == "capture:deal"
        assert item["source_version"] == "m9"
        [entry] = item["source_link"].pop("threads")
        assert item["source_link"] == {"mailbox_id": "mb", "thread_id": "t9"}
        assert (entry["ref"], entry["message_id"], entry["origin"]) == (
            "gmail:mb:t9",
            "m9",
            "deal_document",
        )
        assert item["dedupe_key"] == "email_thread|capture:deal|deal_signal|company:co"
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

    def test_capture_says_when_the_conversation_is_an_invitation(self):
        # Spec 010 R1 FR-CAP-INV-04: `_survivors` marks a kept invitation.
        msgs = [
            _msg("m1", "priya@acme.com", OWNER_BOX, T0),
            {**_msg("m2", "priya@acme.com", OWNER_BOX, T0 + timedelta(hours=1)), "invite": True},
        ]
        parts = capture.prompt_parts(msgs, {}, websites=[], replied=False)
        assert [label for label, _ in parts[2:]] == [
            "Conversation type",
            "Websites in this conversation",
            "Mailbox owner replied",
        ]
        assert parts[2] == ("Conversation type", "meeting invitation")
        assert "Conversation type" not in dict(
            capture.prompt_parts(msgs[:1], {}, websites=[], replied=False)
        )

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

    def test_capture_never_offers_a_meeting_host(self):
        # Spec 010 R1 FR-CAP-INV-04: the calendar sender's domain, Meet,
        # Zoom (and its regional hosts), Teams and Webex are never a website.
        outside = [
            {"email": "calendar-notification@google.com"},
            {"email": "priya@acme-robotics.com"},
        ]
        texts = [
            "https://meet.google.com/abc-defg-hij https://us02web.zoom.us/j/1 zoom.us "
            "https://teams.microsoft.com/l/x teams.live.com acme.webex.com calendar.google.com"
        ]
        assert capture.website_choices(outside, texts, []) == ["acme-robotics.com"]

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
            # Spec 010 R1 (data-model §9.3): no answer reads as Labs.
            "business": "labs",
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

    def test_capture_the_reasons_in_decision_order(self):
        # Plan R1.3, FR-CAP-RPT-06: could not be judged first, then outside
        # Primary and Sent, the rules' reason, ignored, known before, now.
        fate = capture.recall_fate
        assert fate(in_capture=False, survived=False, known_before=True, failed=True) == (
            "not_judged"
        )
        assert fate(in_capture=False, survived=False, known_before=True) == "outside_scope"
        for reason in ("internal_only", "calendar", "automated"):
            assert (
                fate(in_capture=True, survived=False, known_before=True, skip_reason=reason)
                == reason
            )
        assert (
            fate(in_capture=True, survived=True, known_before=True, ignored=True, known_now=True)
            == "ignored"
        )
        assert fate(in_capture=True, survived=True, known_before=False, known_now=True) == (
            "known_now"
        )
        # A Claude failure, after every other check passed.
        assert fate(in_capture=True, survived=True, known_before=False, failed=True) == (
            "not_judged"
        )
        assert set(capture.FATES) >= {
            "internal_only",
            "calendar",
            "automated",
            "not_judged",
            "academy_unrouted",
            "skipped_rules",
        }
        assert len(capture.FATES) == 12

    def test_capture_an_academy_finding_it_could_not_take_is_still_found(self):
        fate = capture.recall_fate
        found = {"in_capture": True, "survived": True, "known_before": False}
        lead = {**found, "outcome": "new_lead", "confidence": "high", "business": "academy"}
        assert fate(**lead, routed=False) == "academy_unrouted"
        assert fate(**lead, routed=True) == "found"
        assert fate(**{**lead, "business": "labs"}, routed=False) == "found"
        assert fate(**{**lead, "confidence": "low"}, routed=False) == "judged_otherwise"

    @staticmethod
    def _rows(**fates: int) -> list[dict]:
        return [
            {"mailbox_id": "mb", "thread_id": f"{fate}-{n}", "fate": fate}
            for fate, count in fates.items()
            for n in range(count)
        ]

    def test_capture_recall_line_when_none_could_be_tested(self):
        rows = self._rows(filed_known=20, known_now=16, internal_only=15, calendar=9)
        assert capture.recall_line(rows) == (
            "None of your 60 labelled conversations could be tested: 36 involve contacts "
            "now in the CRM (filed automatically), 15 were internal-only, 9 were calendar "
            "notifications."
        )

    def test_capture_recall_line_counts_only_tested(self):
        rows = self._rows(
            found=15, judged_otherwise=3, known_now=30, internal_only=8, outside_scope=4
        )
        assert capture.recall_line(rows) == (
            "Of the 18 that could be tested, the dry run found 15. 42 could not be tested: "
            "30 involve contacts now in the CRM (filed automatically), 8 were internal-only, "
            "4 are outside Primary and Sent."
        )
        assert capture.recall_summary(rows) == {
            "total": 60,
            "tested": 18,
            "found": 15,
            "capped": False,
            "untested": {
                "known": 30,
                "ignored": 0,
                "internal_only": 8,
                "calendar": 0,
                "automated": 0,
                "outside_scope": 4,
                "not_judged": 0,
                "skipped_rules": 0,
            },
            "academy_unrouted": 0,
        }

    def test_capture_recall_line_names_course_enquiries_inside_found(self):
        rows = self._rows(found=13, academy_unrouted=2, judged_otherwise=3)
        assert capture.recall_line(rows) == (
            "Of the 18 that could be tested, the dry run found 15, of which 2 were course "
            "enquiries the Academy could not take."
        )
        summary = capture.recall_summary(rows)
        assert (summary["found"], summary["academy_unrouted"]) == (15, 2)

    def test_capture_recall_line_reads_in_the_singular(self):
        rows = self._rows(
            found=1,
            filed_known=1,
            ignored=1,
            internal_only=1,
            calendar=1,
            automated=1,
            outside_scope=1,
            not_judged=1,
            academy_unrouted=1,
        )
        assert capture.recall_line(rows) == (
            "Of the 2 that could be tested, the dry run found 2, of which 1 was a course "
            "enquiry the Academy could not take. 7 could not be tested: 1 involves contacts "
            "now in the CRM (filed automatically), 1 is on the ignore list, 1 was "
            "internal-only, 1 was a calendar notification, 1 was automated mail, 1 is "
            "outside Primary and Sent, 1 could not be judged."
        )
        assert capture.recall_line(self._rows(automated=1)) == (
            "None of your 1 labelled conversation could be tested: 1 was automated mail."
        )

    def test_capture_recall_line_says_capped_only_with_the_marker(self):
        rows = self._rows(found=500)
        # Exactly 500 conversations, every one checked: not capped.
        assert capture.recall_line(rows) == (
            "Of the 500 that could be tested, the dry run found 500."
        )
        assert capture.recall_summary(rows)["capped"] is False
        capped = [*rows, {"capped": True}]
        assert capture.recall_line(capped) == (
            "Of the 500 that could be tested, the dry run found 500. "
            "Only the first 500 were checked."
        )
        summary = capture.recall_summary(capped)
        assert (summary["total"], summary["capped"]) == (500, True)

    def test_capture_a_run_before_074_renders_its_stored_fates(self):
        # FR-CAP-RPT-10: `skipped_rules` stays unsplit; nothing re-computed.
        rows = self._rows(
            filed_known=1, found=1, outside_scope=2, judged_otherwise=1, skipped_rules=3
        )
        assert capture.recall_line(rows) == (
            "Of the 2 that could be tested, the dry run found 1. 6 could not be tested: "
            "1 involves contacts now in the CRM (filed automatically), 2 are outside "
            "Primary and Sent, 3 were skipped by the rules."
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
        # Spec 010 R1 (FR-CAP-GRP-01, contracts §7.4): keyed by its company.
        assert item["dedupe_key"] == (
            "email_thread|capture:domain:acme-robotics.com|new_lead|thread"
        )
        assert item["source_version"] == "m2"
        assert item["source_key"] == "capture:domain:acme-robotics.com"
        [entry] = item["source_link"].pop("threads")
        assert item["source_link"] == {"mailbox_id": "mb", "thread_id": "t1"}
        assert entry == {
            "ref": "gmail:mb:t1",
            "mailbox_id": "mb",
            "thread_id": "t1",
            "message_id": "m2",
            "first_at": "2026-10-01T09:12:00+05:30",
            "latest_at": "2026-10-02T11:40:00+05:30",
            "message_count": 2,
            "owner_replied": True,
            "origin": "discovery",
            "reason": "Asked for pricing; you replied.",
            "confidence": "high",
            "person_email": "priya@acme-robotics.com",
            "first_name": "Priya",
            "last_name": "Shah",
        }
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
            "conversations": 1,
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
        assert item["source_key"] == "capture:linked"
        assert item["dedupe_key"] == "email_thread|capture:linked|new_lead|company:c-1"
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
        assert item["source_key"] == "capture:deal"
        assert item["dedupe_key"] == "email_thread|capture:deal|deal_signal|company:c-1"

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


def _old_item(n: int, **suggestion) -> dict:
    """An open `new_lead` as 073 made it: keyed by its conversation."""
    item = capture.to_item(
        FINDING, outcome="new_lead", confidence="high", **{**ITEM_ARGS, "thread_id": f"t{n}"}
    )
    item["source_link"].pop("threads")
    key = capture.source_key("mb", f"t{n}")
    item.update(
        id=f"i{n}",
        created_at=f"2026-10-0{n}T00:00:00+00:00",
        source_key=key,
        dedupe_key=review_rules.dedupe_key("email_thread", key, "new_lead", None, None),
        suggestion={**item["suggestion"], **suggestion},
    )
    return item


class TestCompanyKey:
    """Spec 010 R1 FR-CAP-GRP-01, -06, -08; plan R1.2; PD-22, PD-40 — one
    suggestion per company."""

    def test_capture_a_domain_keys_the_company(self):
        assert capture.company_key(FINDING) == "capture:domain:acme-robotics.com"
        assert (
            capture.company_key({**FINDING, "domain": "https://www.Acme-Robotics.com/about"})
            == "capture:domain:acme-robotics.com"
        )

    def test_capture_webmail_is_never_a_company_key(self):
        finding = {**FINDING, "domain": "gmail.com", "company_name": None}
        assert capture.company_key({**finding, "person_email": "a@gmail.com"}) == (
            "capture:email:a@gmail.com"
        )

    def test_capture_claudes_name_keys_a_company_with_no_domain(self):
        finding = {**FINDING, "domain": None, "company_name": "  ACME   Robotics "}
        assert capture.company_key(finding) == "capture:name:acme robotics"

    def test_capture_no_name_is_made_up_from_the_person_or_address(self):
        finding = {**FINDING, "domain": None, "company_name": None}
        assert capture.company_key(finding) == "capture:email:priya@acme-robotics.com"
        item = capture.to_item(finding, outcome="new_lead", confidence="high", **ITEM_ARGS)
        # The suggestion still shows a name (FR-CAP-REV-02), the key ignores it.
        assert item["suggestion"]["company_name"] == "Priya Shah"
        assert item["source_key"] == "capture:email:priya@acme-robotics.com"

    def test_capture_a_finding_with_nothing_keys_its_conversation(self):
        assert capture.company_key({}, "gmail:mb:t1") == "capture:thread:gmail:mb:t1"

    def test_capture_a_long_key_is_hashed_within_the_check(self):
        key = capture.company_key({"company_name": "x" * 400})
        assert key.startswith("capture:sha256:") and len(key) <= 300
        assert key == capture.company_key({"company_name": "X" * 400})

    def test_capture_three_conversations_at_one_domain_share_a_key(self):
        items = [
            capture.to_item(
                {**FINDING, "person_email": f"p{n}@acme-robotics.com"},
                outcome="new_lead",
                confidence="high",
                **{**ITEM_ARGS, "thread_id": f"t{n}"},
            )
            for n in range(3)
        ]
        assert len({i["dedupe_key"] for i in items}) == 1
        assert [i["source_link"]["threads"][0]["ref"] for i in items] == [
            "gmail:mb:t0",
            "gmail:mb:t1",
            "gmail:mb:t2",
        ]

    def test_capture_two_webmail_senders_with_no_name_stay_apart(self):
        a, b = (
            capture.to_item(
                {**FINDING, "domain": None, "company_name": None, "person_email": email},
                outcome="new_lead",
                confidence="high",
                **ITEM_ARGS,
            )
            for email in ("priya@gmail.com", "ravi@gmail.com")
        )
        assert a["dedupe_key"] != b["dedupe_key"]

    def test_capture_linked_and_deal_keys_keep_the_company_head(self):
        lead = capture.to_item(
            FINDING, outcome="new_lead", confidence="high", duplicate_company_id="c-1", **ITEM_ARGS
        )
        assert lead["dedupe_key"] == "email_thread|capture:linked|new_lead|company:c-1"
        signal = capture.to_item(
            {**FINDING, "deal_stage": "discovery"},
            outcome="deal_signal",
            confidence="high",
            duplicate_company_id="c-1",
            **ITEM_ARGS,
        )
        assert signal["dedupe_key"] == "email_thread|capture:deal|deal_signal|company:c-1"

    def test_capture_the_detail_counts_conversations(self):
        evidence = {"message_count": 9, "conversations": 3, "reason": "Asked for pricing."}
        assert capture.new_lead_detail({"evidence": evidence}) == (
            "3 conversations · 9 emails · Asked for pricing."
        )
        one = {**evidence, "conversations": 1, "message_count": 2}
        assert capture.new_lead_detail({"evidence": one}) == "2 emails · Asked for pricing."

    def test_capture_an_entry_holds_ids_dates_counts_and_its_sender_only(self):
        entry = capture.thread_entry(
            FINDING, mailbox_id="mb", thread_id="t1", message_id="m2", confidence="high"
        )
        assert set(entry) == {
            "ref",
            "mailbox_id",
            "thread_id",
            "message_id",
            "first_at",
            "latest_at",
            "message_count",
            "owner_replied",
            "origin",
            "reason",
            "confidence",
            "person_email",
            "first_name",
            "last_name",
        }
        assert (entry["person_email"], entry["first_name"], entry["last_name"]) == (
            "priya@acme-robotics.com",
            "Priya",
            "Shah",
        )

    def test_capture_the_newest_entry_is_the_latest_then_the_greatest_id(self):
        old = {"latest_at": "2026-10-01T10:00:00+05:30", "message_id": "b"}
        new = {"latest_at": "2026-10-02T10:00:00+05:30", "message_id": "a"}
        same = {"latest_at": "2026-10-02T10:00:00+05:30", "message_id": "c"}
        assert max([old, new, same], key=capture.entry_order) is same
        assert max([old, {"latest_at": None, "message_id": "z"}], key=capture.entry_order) is old


class TestConsolidationPlan:
    """Spec 010 R1 FR-CAP-GRP-08; plan R1.2; PD-26, PD-40."""

    def test_capture_nine_old_items_of_one_company_make_one_group(self):
        items = [_old_item(n) for n in range(1, 10)]
        [group] = capture.consolidation_plan(items, {})
        assert group["source_key"] == "capture:domain:acme-robotics.com"
        assert group["dedupe_key"] == (
            "email_thread|capture:domain:acme-robotics.com|new_lead|thread"
        )
        assert group["members"] == [f"i{n}" for n in range(9, 0, -1)]
        assert group["company_label"] == "Acme Robotics"

    def test_capture_already_keyed_single_items_need_no_plan(self):
        item = capture.to_item(FINDING, outcome="new_lead", confidence="high", **ITEM_ARGS)
        item.update(id="i1", created_at="2026-10-01T00:00:00+00:00")
        assert capture.consolidation_plan([item], {}) == []

    def test_capture_one_old_item_alone_is_still_rekeyed(self):
        [group] = capture.consolidation_plan([_old_item(1)], {})
        assert group["members"] == ["i1"]

    def test_capture_a_finding_keys_an_old_item_before_its_suggestion(self):
        item = _old_item(1, company_name="Priya Shah", domain=None)
        finding = {**FINDING, "domain": None, "company_name": "HearSight"}
        [group] = capture.consolidation_plan([item], {("mb", "t1"): finding})
        assert group["source_key"] == "capture:name:hearsight"

    def test_capture_a_persons_name_in_an_old_suggestion_keys_by_address(self):
        # PD-40: `_company_name` fell back to the person; that is no company.
        item = _old_item(1, company_name="Priya Shah", domain=None)
        [group] = capture.consolidation_plan([item], {})
        assert group["source_key"] == "capture:email:priya@acme-robotics.com"
        fallback = _old_item(2, company_name="priya@acme-robotics.com", domain=None)
        [group] = capture.consolidation_plan([fallback], {})
        assert group["source_key"] == "capture:email:priya@acme-robotics.com"

    def test_capture_two_namesakes_at_webmail_stay_apart(self):
        a = _old_item(1, company_name="Priya Shah", domain=None, email="priya1@gmail.com")
        b = _old_item(2, company_name="Priya Shah", domain=None, email="priya2@gmail.com")
        plan = capture.consolidation_plan([a, b], {})
        assert sorted(g["source_key"] for g in plan) == [
            "capture:email:priya1@gmail.com",
            "capture:email:priya2@gmail.com",
        ]

    def test_capture_an_old_linked_item_joins_the_linked_key(self):
        item = _old_item(1)
        item.update(record_type="company", record_id="c-1")
        [group] = capture.consolidation_plan([item], {})
        assert (group["source_key"], group["record_id"]) == ("capture:linked", "c-1")
        assert group["dedupe_key"] == "email_thread|capture:linked|new_lead|company:c-1"

    def test_capture_old_deal_signals_of_one_company_make_one_group(self):
        def signal(n: int) -> dict:
            item = capture.deal_signal_item(
                mailbox_id="mb",
                thread_id=f"t{n}",
                message_id=f"m{n}",
                company_id="c-1",
                deals=[],
                stage="proposal",
                kinds=["sow"],
                direction="outbound",
                message_at=NOW,
                owner_id=None,
                generated_at=NOW.isoformat(),
            )
            key = capture.source_key("mb", f"t{n}")
            item["source_link"].pop("threads")
            item.update(id=f"s{n}", created_at=f"2026-10-0{n}T00:00:00+00:00", source_key=key)
            return item

        [group] = capture.consolidation_plan([signal(1), signal(2)], {})
        assert (group["source_key"], group["members"]) == ("capture:deal", ["s2", "s1"])

    def test_capture_an_old_item_and_a_new_one_of_its_company_merge(self):
        new = capture.to_item(FINDING, outcome="new_lead", confidence="high", **ITEM_ARGS)
        new.update(id="i9", created_at="2026-10-09T00:00:00+00:00")
        [group] = capture.consolidation_plan([_old_item(1), new], {})
        assert group["members"] == ["i9", "i1"]


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


class TestAcademyRules:
    """Spec 010 R1 FR-CAP-ACAD-01, -03 to -05; contracts §7.3, §7.4;
    plan R1.4, PD-29 to PD-31, PD-41."""

    ANSWER = {
        "outcome": "new_lead",
        "confidence": "high",
        "company_name": "City College",
        "person_name": "Asha Kumar",
        "person_email": "asha@college.example.edu",
        "person_title": None,
        "lead_stage": "contacted",
        "deal_stage": None,
        "website_choice": 1,
        "reason": "Asked about the AI course for students; you replied.",
    }

    def _msgs(self):
        return [
            _msg("m1", "asha@college.example.edu", OWNER_BOX, T0),
            _msg("m2", OWNER_BOX, "asha@college.example.edu", T0 + timedelta(days=1)),
        ]

    def test_capture_business_defaults_to_labs(self):
        answer = DiscoveryOutput.model_validate(self.ANSWER)
        assert answer.business == "labs"
        assert DiscoveryOutput.model_validate({**self.ANSWER, "business": "academy"}).business == (
            "academy"
        )

    def test_capture_business_takes_only_labs_or_academy(self):
        with pytest.raises(ValidationError):
            DiscoveryOutput.model_validate({**self.ANSWER, "business": "school"})
        schema = DiscoveryOutput.model_json_schema()
        assert schema["properties"]["business"]["enum"] == ["labs", "academy"]
        assert "business" not in schema.get("required", [])

    def test_capture_instruction_names_the_business_and_invitations(self):
        assert (
            "business: academy when the enquiry is about courses or training for individuals "
            "or students" in capture.INSTRUCTION
        )
        assert "labs for AI services, products, software or staffing." in capture.INSTRUCTION
        assert (
            "A meeting invitation is a lead only when the conversation shows interest in buying"
            in capture.INSTRUCTION
        )

    def test_capture_classifier_version_moves_for_the_business_answer(self):
        assert capture.CLASSIFIER_VERSION == "010.2"

    def test_capture_finding_keeps_the_academy_answer_in_labs(self):
        found = capture.finding_of(
            {**self.ANSWER, "business": "academy"},
            self._msgs(),
            mailbox_email=OWNER_BOX,
            choices=["college.example.edu"],
            tva_linked=True,
        )
        assert found["business"] == "academy" and found["duplicate_company_id"] is None
        assert capture.routes_to_academy(found, "high") and capture.routes_to_academy(
            found, "medium"
        )
        assert not capture.routes_to_academy(found, "low")

    def test_capture_finding_is_labs_outside_the_tva_linked_workspace(self):
        found = capture.finding_of(
            {**self.ANSWER, "business": "academy"},
            self._msgs(),
            mailbox_email=OWNER_BOX,
            choices=[],
            tva_linked=False,
        )
        assert found["business"] == "labs"
        assert not capture.routes_to_academy(found, "high")

    def test_capture_missing_business_reads_as_labs(self):
        assert capture.business_of({}, tva_linked=True) == "labs"
        assert capture.business_of({"business": None}, tva_linked=True) == "labs"

    def test_capture_academy_item_holds_no_uuid(self):
        found = capture.finding_of(
            {**self.ANSWER, "business": "academy"},
            self._msgs(),
            mailbox_email=OWNER_BOX,
            choices=["college.example.edu"],
            tva_linked=True,
        )
        item = capture.academy_lead_item(
            found,
            mailbox_email=OWNER_BOX,
            thread_id="18c2f0a9b1",
            message_id="18c2f0a9b2",
            outcome="new_lead",
            confidence="high",
            owner_id=None,
            model="claude-model",
            generated_at="2026-10-20T06:00:00+00:00",
        )
        assert not _UUID.search(json.dumps(item))
        assert "mailbox_id" not in item["source_link"]
        [entry] = item["source_link"]["threads"]
        assert "mailbox_id" not in entry
        assert entry["ref"] == f"gmail:{OWNER_BOX}:18c2f0a9b1"
        assert entry["origin"] == "academy_routing"
        assert entry["person_email"] == "asha@college.example.edu"
        assert item["source_key"] == "capture:domain:college.example.edu"
        assert item["dedupe_key"] == (
            "email_thread|capture:domain:college.example.edu|new_lead|thread"
        )
        assert item["type"] == "new_lead" and item["record_id"] is None
        evidence = item["suggestion"]["evidence"]
        assert evidence["origin"] == "academy_routing" and evidence["mailbox_email"] == OWNER_BOX

    def test_capture_academy_deal_signal_is_a_lead_with_a_hint(self):
        finding = {"company_name": "City College", "deal_stage": "proposal", "reason": "SOW"}
        item = capture.academy_lead_item(
            finding,
            mailbox_email=OWNER_BOX,
            thread_id="t1",
            message_id="m1",
            outcome="deal_signal",
            confidence="medium",
            owner_id=None,
            model=None,
            generated_at=None,
            company_id="academy-company",
        )
        assert item["type"] == "new_lead"
        assert item["suggestion"]["deal_stage_hint"] == "proposal"
        assert item["source_key"] == capture.LINKED_KEY
        assert item["dedupe_key"].endswith("|new_lead|company:academy-company")

    def test_capture_academy_item_none_for_low_confidence(self):
        assert (
            capture.academy_lead_item(
                {"company_name": "X"},
                mailbox_email=OWNER_BOX,
                thread_id="t1",
                message_id="m1",
                outcome="new_lead",
                confidence="low",
                owner_id=None,
                model=None,
                generated_at=None,
            )
            is None
        )

    def test_capture_academy_note_reads_like_the_spec(self):
        entries = [
            {
                "message_count": 2,
                "first_at": "2026-10-01T10:00:00+05:30",
                "latest_at": "2026-10-02T10:00:00+05:30",
                "message_id": "m2",
                "reason": "Asked about the AI course for final-year students.",
            },
            {
                "message_count": 1,
                "first_at": "2026-09-30T10:00:00+05:30",
                "latest_at": "2026-09-30T10:00:00+05:30",
                "message_id": "m0",
                "reason": None,
            },
        ]
        assert capture.academy_note(entries, "navaneeth@nunnarilabs.com") == (
            "Email conversation in navaneeth@nunnarilabs.com: 1 email, 30 Sep 2026.\n"
            "Email conversation in navaneeth@nunnarilabs.com: 2 emails, 1 Oct to 2 Oct 2026. "
            "Asked about the AI course for final-year students."
        )

    def test_capture_academy_note_names_each_conversation_s_own_mailbox(self):
        # One Academy item groups one college's enquiries from every Labs
        # mailbox; each line names the mailbox its `ref` carries, and the
        # evidence's address only when the ref names none.
        entries = [
            {
                "ref": capture.academy_ref("sales@nunnarilabs.com", "t-sales"),
                "message_count": 1,
                "first_at": "2026-10-02T10:00:00+05:30",
                "latest_at": "2026-10-02T10:00:00+05:30",
                "message_id": "m2",
            },
            {
                "ref": capture.academy_ref("navaneeth@nunnarilabs.com", "t-nav"),
                "message_count": 1,
                "first_at": "2026-10-01T10:00:00+05:30",
                "latest_at": "2026-10-01T10:00:00+05:30",
                "message_id": "m1",
            },
            {
                "ref": "gmail:not-an-address:t-x",
                "message_count": 1,
                "first_at": "2026-10-03T10:00:00+05:30",
                "latest_at": "2026-10-03T10:00:00+05:30",
                "message_id": "m3",
            },
        ]
        assert capture.academy_note(entries, "sales@nunnarilabs.com") == (
            "Email conversation in navaneeth@nunnarilabs.com: 1 email, 1 Oct 2026.\n"
            "Email conversation in sales@nunnarilabs.com: 1 email, 2 Oct 2026.\n"
            "Email conversation in sales@nunnarilabs.com: 1 email, 3 Oct 2026."
        )

    @pytest.mark.parametrize(
        ("ref", "mailbox"),
        [
            ("gmail:sales@nunnarilabs.com:18c2f0a", "sales@nunnarilabs.com"),
            ("gmail:5f0c6a2e-0000-4000-8000-000000000001:18c2f0a", None),
            ("gmail:sales@nunnarilabs.com:", None),
            ("gmail:sales@nunnarilabs.com", None),
            ("capture:domain:college.example.edu", None),
            (None, None),
        ],
    )
    def test_capture_academy_ref_mailbox(self, ref, mailbox):
        assert capture.academy_ref_mailbox(ref) == mailbox

    @pytest.mark.parametrize(
        ("value", "refused"),
        [
            ({"accept_routed": True}, False),
            ({"accept_routed": False}, False),
            ({"accept_routed": "yes"}, True),
            ({}, True),
            ({"accept_routed": True, "extra": 1}, True),
            (True, True),
        ],
    )
    def test_capture_routing_setting_is_one_boolean(self, value, refused):
        assert (settings_rules.setting_refusal("capture_routing", value) is not None) is refused

    def test_capture_routing_missing_row_reads_as_on(self):
        assert capture.accepts_routed(None) is True
        assert capture.accepts_routed({"accept_routed": False}) is False
        assert capture.accepts_routed({"accept_routed": "no"}) is False
