"""Gmail sync, the pure rules — spec 007 FR-CRM-SYNC-01, -05, -06, -08,
FR-CRM-FLAG-04, FR-CRM-PIPE-02; plan §5.13.

Our domains are the workspace's allow-list; a message among ourselves only
is skipped; the sender says the direction; auto-replies and bounces are
detected from headers; outside participants match people by any email, then
companies by domain; what is unknown is created, a public webmail address
with no company; `last_*` only move forward; a company at New becomes
Contacted on the first outbound email.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.crm import email_rules as rules

OURS = ["nunnarilabs.com", "deepweaver.ai"]
#: 2026-10-14 06:30 UTC in milliseconds, as Gmail's internalDate.
AT_MS = "1791959400000"


def _h(**headers: str) -> list[dict[str, str]]:
    return [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]


class TestMailboxRules:
    """FR-CRM-SYNC-01, amended (plan §5.13)."""

    def _why(self, **over):
        args = {
            "email": "owner@nunnarilabs.com",
            "owner_email": "Owner@nunnarilabs.com",
            "owner_is_member": True,
            "by_tva_admin": False,
            "labels": ["Sales"],
        }
        return rules.mailbox_refusal(**{**args, **over})

    def test_the_owners_own_address_is_fine(self):
        assert self._why() is None

    def test_the_owner_must_be_a_member(self):
        assert self._why(owner_is_member=False) == rules.OWNER_NOT_MEMBER

    def test_another_address_needs_a_tva_admin(self):
        assert self._why(email="navaneeth@deepweaver.ai") == rules.NOT_OWN_ADDRESS
        assert self._why(email="navaneeth@deepweaver.ai", by_tva_admin=True) is None

    def test_a_domain_the_service_account_does_not_cover(self):
        why = self._why(email="owner@gmail.com", owner_email="owner@gmail.com")
        assert why and "nunnarilabs.com" in why and "nunnari.academy" in why

    @pytest.mark.parametrize(
        "label", ["INBOX", "inbox", "Sent", "CATEGORY_PROMOTIONS", "category_social", "Starred"]
    )
    def test_system_labels_by_id_or_name(self, label):
        assert rules.is_system_label(label)
        assert "system label" in self._why(labels=["Sales", label])

    @pytest.mark.parametrize("label", ["Sales", "Leads", "academy/course", "Inbox zero"])
    def test_labels_of_ones_own(self, label):
        assert not rules.is_system_label(label)

    def test_no_labels(self):
        assert self._why(labels=[]) == rules.NO_LABELS

    def test_labels_are_trimmed_and_given_once(self):
        assert rules.clean_labels([" Sales ", "sales", "Leads", "", "academy/course"]) == [
            "Sales",
            "Leads",
            "academy/course",
        ]


class TestClassify:
    """FR-CRM-SYNC-05, -06."""

    def test_inbound_from_a_client(self):
        info = rules.classify(
            _h(
                From="Priya Raman <Priya@Acme.com>",
                To="owner@nunnarilabs.com",
                Cc="ravi@acme.com, sanjeev@deepweaver.ai",
                Subject=" Proposal ",
            ),
            OURS,
            AT_MS,
        )
        assert info["direction"] == "inbound"
        assert info["from_email"] == "priya@acme.com"
        assert info["subject"] == "Proposal"
        assert not info["is_internal"] and not info["is_auto"]
        assert [p["email"] for p in info["outside"]] == ["priya@acme.com", "ravi@acme.com"]
        assert [(p["email"], p["role"]) for p in info["participants"]] == [
            ("priya@acme.com", "from"),
            ("owner@nunnarilabs.com", "to"),
            ("ravi@acme.com", "cc"),
            ("sanjeev@deepweaver.ai", "cc"),
        ]
        assert info["sent_at"] == datetime(2026, 10, 14, 6, 30, tzinfo=UTC)

    def test_outbound_when_the_sender_is_ours(self):
        info = rules.classify(_h(From="owner@nunnarilabs.com", To="priya@acme.com"), OURS, AT_MS)
        assert info["direction"] == "outbound"

    def test_our_domains_only_is_internal(self):
        info = rules.classify(
            _h(From="owner@nunnarilabs.com", To="sanjeev@deepweaver.ai"), OURS, AT_MS
        )
        assert info["is_internal"] and info["outside"] == []

    def test_our_domains_are_the_workspaces_allow_list(self):
        """Academy's allow-list does not have deepweaver.ai: there it is outside."""
        info = rules.classify(
            _h(From="owner@nunnarilabs.com", To="sanjeev@deepweaver.ai"),
            ["nunnari.academy", "nunnarilabs.com"],
            AT_MS,
        )
        assert not info["is_internal"]
        assert [p["email"] for p in info["outside"]] == ["sanjeev@deepweaver.ai"]


class TestAutoReplies:
    """FR-CRM-FLAG-04 — auto-replies and bounces, where detectable."""

    @pytest.mark.parametrize(
        "headers",
        [
            {"Auto_Submitted": "auto-replied"},
            {"X_Autoreply": "yes"},
            {"Precedence": "auto_reply"},
            {"Content_Type": "multipart/report; report-type=delivery-status; boundary=x"},
            {"Subject": "Automatic reply: Proposal"},
            {"Subject": "Out of Office until Monday"},
            {"Subject": "Undeliverable: Proposal"},
        ],
    )
    def test_detected(self, headers):
        h = _h(From="priya@acme.com", **{"Subject": "x", **headers})
        assert rules.is_auto(h, "priya@acme.com")

    def test_a_mailer_daemon(self):
        assert rules.is_auto(_h(Subject="Returned"), "mailer-daemon@googlemail.com")

    def test_auto_submitted_no_is_a_person(self):
        assert not rules.is_auto(_h(Auto_Submitted="no", Subject="Re: Proposal"), "p@acme.com")


class TestMatchingAndCreating:
    """FR-CRM-SYNC-05, automation 2."""

    def test_a_known_person_and_a_known_domain_create_nothing(self):
        people = {"priya@acme.com": {"id": "p1", "company_id": "c1"}}
        companies = {"acme.com": "c1"}
        outside = [{"email": "priya@acme.com", "name": "Priya"}, {"email": "ravi@acme.com"}]
        made_companies, made_people = rules.to_create(outside, people, companies)
        assert made_companies == {}
        assert made_people == {
            "ravi@acme.com": {"first_name": "Ravi", "last_name": None, "domain": "acme.com"}
        }

    def test_an_unknown_domain_creates_its_company(self):
        made_companies, made_people = rules.to_create(
            [{"email": "li.wei@globex.co.uk", "name": "Li Wei"}], {}, {}
        )
        assert made_companies == {"globex.co.uk": "Globex"}
        assert made_people["li.wei@globex.co.uk"] == {
            "first_name": "Li",
            "last_name": "Wei",
            "domain": "globex.co.uk",
        }

    def test_a_public_webmail_address_has_no_company(self):
        made_companies, made_people = rules.to_create(
            [{"email": "someone.nl@gmail.com", "name": ""}], {}, {}
        )
        assert made_companies == {}
        assert made_people["someone.nl@gmail.com"] == {
            "first_name": "Someone",
            "last_name": "Nl",
            "domain": None,
        }

    def test_links_person_company_and_its_open_deals_once(self):
        people = {"priya@acme.com": {"id": "p1", "company_id": "c1"}}
        links = rules.links_for(
            [{"email": "priya@acme.com"}, {"email": "ravi@acme.com"}],
            people,
            {"acme.com": "c1"},
            {"c1": ["d1", "d2"]},
        )
        assert links == [
            {"record_type": "person", "record_id": "p1"},
            {"record_type": "company", "record_id": "c1"},
            {"record_type": "opportunity", "record_id": "d1"},
            {"record_type": "opportunity", "record_id": "d2"},
        ]

    def test_a_persons_own_company_wins_over_the_domain(self):
        people = {"priya@gmail.com": {"id": "p1", "company_id": "c9"}}
        links = rules.links_for([{"email": "priya@gmail.com"}], people, {}, {})
        assert {"record_type": "company", "record_id": "c9"} in links

    @pytest.mark.parametrize(
        ("domain", "name"),
        [("acme.com", "Acme"), ("mail.globex.com", "Globex"), ("big-co.ac.in", "Big Co")],
    )
    def test_company_names_from_domains(self, domain, name):
        assert rules.company_name(domain) == name


class TestWhatARecordLearns:
    """FR-CRM-SYNC-05 `last_*`; FR-CRM-PIPE-02 New → Contacted."""

    T1 = datetime(2026, 10, 10, 9, tzinfo=UTC)
    T2 = datetime(2026, 10, 12, 9, tzinfo=UTC)

    def test_latest_ignores_auto_replies(self):
        seen = rules.latest(
            [
                {"direction": "inbound", "sent_at": self.T1},
                {"direction": "outbound", "sent_at": self.T2},
                {"direction": "inbound", "sent_at": self.T2, "is_auto": True},
            ]
        )
        assert seen == {
            "last_inbound_at": self.T1,
            "last_outbound_at": self.T2,
            "last_contact_at": self.T2,
        }

    def test_last_values_only_move_forward(self):
        row = {"last_inbound_at": self.T2.isoformat(), "last_contact_at": None}
        changes = rules.forward(row, {"last_inbound_at": self.T1, "last_contact_at": self.T1})
        assert changes == {"last_contact_at": self.T1.isoformat()}

    def test_new_becomes_contacted_on_an_outbound_email(self):
        out = [{"direction": "outbound", "sent_at": self.T1}]
        assert rules.contacted({"lead_stage": "new"}, out)
        assert not rules.contacted({"lead_stage": "nurturing"}, out)
        assert not rules.contacted(
            {"lead_stage": "new"}, [{"direction": "inbound", "sent_at": self.T1}]
        )

    def test_the_backfill_reads_ninety_days(self):
        assert rules.backfill_after(self.T2) == datetime(2026, 7, 14, 9, tzinfo=UTC)
