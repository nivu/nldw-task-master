"""Automatic email capture, the rules — spec 010 FR-CAP-SRC-01 to -08,
FR-CAP-CHG-01 to -03, FR-CAP-REV-01 (`deal_signal`); data-model §3.1; plan
§5.3 to §5.5, §5.10, PD-2, PD-3, PD-19.

What a mailbox on *Primary and Sent* reads, and what the sync makes of it,
decided from labels, headers, rows and the clock alone (no body is read):

- **Scope.** A mailbox scans its configured labels and, on `primary_sent`,
  Gmail's `CATEGORY_PERSONAL` (the Primary tab) and `SENT`. A message is in
  scope when it carries one of those and is in neither Trash nor Spam (a
  deleted message keeps its other labels in Trash).
- **Bulk.** A message that reached the sync through Primary or Sent alone,
  with no configured label, is skipped when its headers say it is bulk or
  automated, or it is a calendar invitation (PD-2: by subject prefix and
  Google's sender, since metadata has no `text/calendar` part). Hand labels
  win: a labelled bulk message is stored as today, marked `is_auto`.
- **The window.** A `primary_sent` mailbox backfills from its capture start
  date at 00:00 IST; a `labels` mailbox the last 90 days. After switching
  back to `labels`, the next reconcile looks back to the old start date
  (`reconcile_after`, PD-3).
- **Deal documents.** Every sync of a `primary_sent` mailbox searches its
  Primary, Sent and labelled mail for attachments named like an SOW,
  proposal, quotation, MSA or purchase order, since the last search (the
  first at most 14 days back, PD-19). A document sent to a known company
  with an open deal before Proposal suggests Proposal; one received suggests
  checking for Closed Won. Only deals created on or before the document are
  offered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from email.utils import parseaddr
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.crm import email_rules
from app.domain.crm import review as review_rules

#: The workspace's discovery modes (FR-CAP-DIS-01); S1 only names them.
MODES: Final = ("off", "dry_run", "live")
#: A mailbox's capture source (FR-CAP-SRC-01).
SOURCES: Final = ("labels", "primary_sent")
LABELS_ONLY: Final = "labels"
PRIMARY_SENT: Final = "primary_sent"

#: Gmail's system labels capture reads or refuses.
PRIMARY: Final = "CATEGORY_PERSONAL"
SENT: Final = "SENT"
TRASH: Final = "TRASH"
SPAM: Final = "SPAM"
#: A message carrying either is out of scope whatever else it carries.
BINNED: Final = frozenset({TRASH, SPAM})

#: The CRM's day (data-model §3.1: the start date is read at 00:00 IST).
ZONE: Final = ZoneInfo("Asia/Kolkata")

#: The default capture start date (E11).
DEFAULT_START: Final = date(2026, 4, 1)

#: Contracts §1.1 — the mailbox PATCH's new refusal.
START_IN_FUTURE: Final = "The capture start date cannot be in the future."

# ---------------------------------------------------------------------------
# Scope — FR-CAP-SRC-02, -08; CHG-01
# ---------------------------------------------------------------------------


def scan_labels(configured: Sequence[str], source: str) -> list[str]:
    """The Gmail label ids a mailbox reads: its configured labels, plus
    Primary and Sent on `primary_sent`, each once, in that order."""
    out = list(dict.fromkeys(configured))
    if source == PRIMARY_SENT:
        for label in (PRIMARY, SENT):
            if label not in out:
                out.append(label)
    return out


def in_scope(label_ids: Iterable[str], scan: Iterable[str]) -> bool:
    """A message carries a label the mailbox scans and is in neither Trash
    nor Spam."""
    labels = set(label_ids or [])
    return bool(labels & set(scan)) and not labels & BINNED


def _midnight(day: date | str) -> datetime:
    when = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
    return datetime.combine(when, time(0, 0), tzinfo=ZONE)


def capture_after(mailbox: Mapping[str, Any], now: datetime) -> datetime:
    """FR-CAP-SRC-07, CHG-03 — where a backfill and a reconcile start: the
    capture start date at 00:00 IST on `primary_sent`, else 90 days back."""
    if mailbox.get("capture_source") == PRIMARY_SENT:
        return _midnight(mailbox.get("capture_start_date") or DEFAULT_START)
    return email_rules.backfill_after(now)


def _at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def reconcile_window(mailbox: Mapping[str, Any], now: datetime) -> datetime:
    """PD-3 — a reconcile looks back to the capture window, or further to
    `reconcile_after`: set when the mailbox goes back to *Labels only*, or
    when its start date is moved later while capturing, so captured mail
    with no label is removed however old it is."""
    after = capture_after(mailbox, now)
    back = _at(mailbox.get("reconcile_after"))
    return min(after, back) if back is not None else after


def reconcile_bounds(mailbox: Mapping[str, Any], now: datetime) -> tuple[datetime, datetime]:
    """`(list Gmail after, compare stored mail from)` for a reconcile. The
    day's margin keeps the moving 90-day edge out of the comparison; when
    the window starts on a fixed day (a start date or `reconcile_after`)
    the margin goes on the listing instead, so mail stored on that day
    itself is compared too (PD-3)."""
    window = reconcile_window(mailbox, now)
    if window < email_rules.backfill_after(now) or mailbox.get("capture_source") == PRIMARY_SENT:
        return window - timedelta(days=1), window
    return window, window + timedelta(days=1)


# ---------------------------------------------------------------------------
# Bulk and automated mail — FR-CAP-SRC-03, PD-2
# ---------------------------------------------------------------------------

#: `Precedence` values of bulk or automated mail.
_BULK_PRECEDENCE: Final = ("bulk", "list", "junk", "auto_reply")
#: A sender that never writes as a person (the local part, as it starts).
_NO_REPLY: Final = re.compile(
    r"^(no-?reply|do-?not-?reply|notifications?|mailer-daemon|postmaster)([+._-].*)?$"
)
#: Google Calendar's invitations, by how their subject starts (lower case).
_INVITE_SUBJECTS: Final = (
    "invitation:",
    "updated invitation:",
    "accepted:",
    "declined:",
    "tentatively accepted:",
    "canceled event:",
    "cancelled event:",
)
_CALENDAR_SENDER: Final = "calendar-notification@google.com"


def is_bulk(headers: list[dict[str, Any]], sender: str | None = None) -> bool:
    """A newsletter, a notification, an auto-reply, a bounce or a calendar
    invitation, from its headers (`List-Unsubscribe`, `Precedence`,
    `Auto-Submitted`, `X-Autoreply`/`X-Autorespond`, a delivery report), its
    sender (a no-reply address, Google Calendar) or its subject (an
    invitation)."""
    header = email_rules.header
    if (header(headers, "List-Unsubscribe") or "").strip():
        return True
    if (header(headers, "Precedence") or "").strip().lower() in _BULK_PRECEDENCE:
        return True
    auto = (header(headers, "Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if header(headers, "X-Autoreply") or header(headers, "X-Autorespond"):
        return True
    if "report-type=delivery-status" in (header(headers, "Content-Type") or "").lower():
        return True
    address = (sender or parseaddr(header(headers, "From") or "")[1]).strip().lower()
    if address == _CALENDAR_SENDER or _NO_REPLY.match(address.split("@", 1)[0]):
        return True
    subject = (header(headers, "Subject") or "").strip().lower()
    return subject.startswith(_INVITE_SUBJECTS)


# ---------------------------------------------------------------------------
# Deal documents — FR-CAP-SRC-06; E8; PD-19
# ---------------------------------------------------------------------------

#: One Gmail search per kind, so an item says which kind matched without
#: reading the message (plan CT-3). Tuned here without a schema change (R-3).
DEAL_DOC_QUERIES: Final = {
    "sow": 'filename:sow OR filename:"statement of work"',
    "proposal": "filename:proposal",
    "quotation": "filename:quotation OR filename:quote",
    "msa": "filename:msa",
    "purchase_order": 'filename:"purchase order" OR filename:po',
}
#: What an item calls each kind.
DEAL_DOC_NAMES: Final = {
    "sow": "SOW",
    "proposal": "Proposal",
    "quotation": "Quotation",
    "msa": "MSA",
    "purchase_order": "Purchase order",
}
#: The first search of a mailbox looks back at most this far (PD-19).
DEAL_DOC_FIRST_DAYS: Final = 14
#: Pages of ids per kind per sync, and ids per page (NFR-CAP-03).
DEAL_DOC_PAGES: Final = 2
DEAL_DOC_PAGE_SIZE: Final = 100

#: The open stages an outbound document can move to Proposal.
_BEFORE_PROPOSAL: Final = ("discovery", "qualification")
_OPEN: Final = ("discovery", "qualification", "proposal", "negotiation")

#: Contracts §6.2 — the reasons an item gives.
SIGNED_DOCUMENT: Final = "Check the signed document."


def _quoted(name: str) -> str:
    return '"' + str(name).replace('"', "").strip() + '"'


def deal_doc_scope(label_names: Iterable[str]) -> str:
    """Primary, Sent and the configured labels, never Trash or Spam: Updates,
    Social, Promotions and Forums are not searched (E10)."""
    terms = ["category:primary", "in:sent", *(f"label:{_quoted(n)}" for n in label_names)]
    return f"({' OR '.join(terms)}) -in:trash -in:spam"


def deal_doc_query(kind: str, after: int, scope: str) -> str:
    """The search for one kind of document since `after` (epoch seconds)."""
    return f"has:attachment after:{after} {scope} ({DEAL_DOC_QUERIES[kind]})"


def deal_doc_since(docs_checked_at: Any, start_date: date | str | None, now: datetime) -> datetime:
    """Where a search starts: the last check, or on the first one the later
    of the capture start date and 14 days back (PD-19)."""
    checked = _at(docs_checked_at)
    if checked is not None:
        return checked
    return max(_midnight(start_date or DEFAULT_START), now - timedelta(days=DEAL_DOC_FIRST_DAYS))


def deal_doc_stage(direction: str, deal_stage: str) -> str | None:
    """E8 — sent by us: Proposal, for a deal still before it; received:
    possibly Closed Won, for any open deal. Otherwise nothing."""
    if direction == "outbound":
        return "proposal" if deal_stage in _BEFORE_PROPOSAL else None
    return "closed_won" if deal_stage in _OPEN else None


def deal_doc_deals(
    deals: Iterable[Mapping[str, Any]], document_at: datetime
) -> list[Mapping[str, Any]]:
    """Only deals created on or before the document: it cannot be about a
    deal opened after it (PD-19)."""
    return [d for d in deals if (_at(d.get("created_at")) or document_at) <= document_at]


def source_key(mailbox_id: str, thread_id: str) -> str:
    """Contracts §6: the same key `009`'s summaries use for a thread."""
    return f"gmail:{mailbox_id}:{thread_id}"


def deal_signal_item(
    *,
    mailbox_id: str,
    thread_id: str,
    message_id: str,
    company_id: str,
    deals: Sequence[Mapping[str, Any]],
    stage: str,
    kinds: Sequence[str],
    direction: str,
    message_at: datetime,
    owner_id: str | None,
    generated_at: str,
) -> dict[str, Any]:
    """Contracts §6.2 — one `deal_signal` from a deal document, as
    `crm_review_upsert` takes it: the company the target, its eligible open
    deals the choices, the message id the source version (model NULL: no
    Claude)."""
    key = source_key(mailbox_id, thread_id)
    names = [DEAL_DOC_NAMES[k] for k in kinds if k in DEAL_DOC_NAMES]
    what = " and ".join(names) or "Document"
    suggestion: dict[str, Any] = {"action": "move", "stage": stage}
    if stage == "proposal":
        suggestion["proposal_sent_on"] = message_at.astimezone(ZONE).date().isoformat()
        reason = f"{what} sent to the client."
    else:
        reason = f"{what} received. {SIGNED_DOCUMENT}"
    suggestion["evidence"] = {
        "origin": "deal_document",
        "document_kinds": list(kinds),
        "direction": direction,
        "message_at": message_at.astimezone(ZONE).isoformat(),
        "message_count": 1,
        # Sent by the owner, so they wrote in the thread; for a received
        # document the thread was not read, so it is not said (the card
        # hides the row).
        "owner_replied": True if direction == "outbound" else None,
        "attachments": [],
        "reason": reason[:160],
        "confidence": None,
    }
    return {
        "type": "deal_signal",
        "owner_id": owner_id,
        "record_type": "company",
        "record_id": company_id,
        "suggestion": suggestion,
        "current_values": {},
        "source_kind": "email_thread",
        "source_key": key,
        "source_version": message_id,
        "source_link": {"mailbox_id": mailbox_id, "thread_id": thread_id},
        "dedupe_key": review_rules.dedupe_key(
            "email_thread", key, "deal_signal", "company", company_id
        ),
        "model": None,
        "generated_at": generated_at,
        "deal_choices": sorted(d["id"] for d in deals),
    }


#: Contracts §6.2 — what Today and the digest call a deal signal.
_STAGE_TITLES: Final = {
    "discovery": "New deal at Discovery",
    "closed_won": "Possibly Closed Won: check the signed document",
}


def deal_signal_title(suggestion: Mapping[str, Any]) -> str:
    """ "Move to Proposal, proposal sent on 20 Oct", "Possibly Closed Won:
    check the signed document" or "New deal at Discovery"."""
    stage = suggestion.get("stage")
    if suggestion.get("action") == "create":
        return _STAGE_TITLES["discovery"]
    if stage == "proposal":
        sent = suggestion.get("proposal_sent_on")
        if sent:
            day = date.fromisoformat(str(sent)[:10])
            return f"Move to Proposal, proposal sent on {day.day} {day:%b}"
        return "Move to Proposal"
    return _STAGE_TITLES.get(str(stage), "Deal signal")
