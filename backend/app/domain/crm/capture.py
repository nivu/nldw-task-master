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

Spec 010 S2 (FR-CAP-DIS-01, -04 to -07, -10, FR-CAP-RPT-01 to -03; plan
§5.6, §5.7, PD-9, PD-11, PD-14, PD-17, PD-20) adds the discovery job's
rules: the `capture` setting and its refusal, who in a conversation is from
outside and whether the mailbox owner replied, what is cut from each
message (`trim`) and what the prompt holds (`prompt_parts`: at most the last
12 messages and 24,000 characters, then the websites found in the thread and
whether the owner replied), the website Claude picks from (`website_choices`;
Claude never types one, PD-20), the finding a lead or a deal signal keeps,
which failures count against a conversation (PD-17), and the recall check's
fates and its line.

Spec 010 S3 (FR-CAP-DIS-03, -07 to -09, FR-CAP-REV-01 to -04, FR-CAP-RPT-04;
plan §5.8 to §5.10, PD-4, PD-21) adds the ignore list (`ignored`,
`ignore_refusal`), sibling domains for the duplicate check, the items a
finding makes (`to_item`: none for low confidence; a `new_lead`, linked to
a matched company; a Claude `deal_signal` on an unknown company is a
`new_lead` with a deal-stage hint), and when a dry run can be applied.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from email.utils import parseaddr
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.crm import ai_rules, email_rules
from app.domain.crm import review as review_rules
from app.domain.crm import summaries as summary_rules
from app.domain.crm.invites import PUBLIC_WEBMAIL_DOMAINS

#: The workspace's discovery modes (FR-CAP-DIS-01).
MODES: Final = ("off", "dry_run", "live")
OFF: Final = "off"
DRY_RUN: Final = "dry_run"
LIVE: Final = "live"
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


# ---------------------------------------------------------------------------
# The `capture` setting — FR-CAP-DIS-01; contracts §1.2; PD-13
# ---------------------------------------------------------------------------

#: Stored with every decision (FR-CAP-DIS-06): a change of rules or prompt
#: that should be told apart moves it.
CLASSIFIER_VERSION: Final = "010.1"
#: What the discovery job records in the usage log and the job log.
JOB: Final = "capture"
#: Candidates a run judges at most (NFR-CAP-03).
RUN_CAP: Final = 200
#: Recall rows a dry run keeps at most (data-model §3.5).
RECALL_CAP: Final = 500

#: Contracts §1.2, §2.2 — the sentences.
MODE_REFUSAL: Final = "Email capture is off, dry run or live."
START_FROM_CAPTURE: Final = "Start a dry run from Email capture."
APPLY_FIRST: Final = "Run and apply a dry run first."
SET_MAILBOX_FIRST: Final = "Set a mailbox to Primary and Sent first."
SYNC_FIRST: Final = "Switch email sync on first."
ALREADY_RUNNING: Final = "A dry run is already running."
#: Plan §5.6 — why a run did nothing.
CAPTURE_OFF: Final = "Email capture is off."
NO_CAPTURING_MAILBOX: Final = "No mailbox captures Primary and Sent."
DRY_RUN_FINISHED: Final = "The dry run is finished; apply it or run it again."
MODE_CHANGED: Final = "The capture mode changed during the run."
GMAIL_KEY_MISSING: Final = "The Gmail key is not set on this service."


def capture_refusal(value: Any) -> str | None:
    """The setting is exactly `{"mode": "off" | "dry_run" | "live"}`; any
    other value, an unknown or a missing key is refused."""
    if not isinstance(value, dict) or set(value) != {"mode"} or value["mode"] not in MODES:
        return MODE_REFUSAL
    return None


def capture_mode(value: Any) -> str:
    """The workspace's mode: a missing or broken setting reads as off."""
    return value["mode"] if capture_refusal(value) is None else OFF


# ---------------------------------------------------------------------------
# A conversation — FR-CAP-DIS-03, -04
# ---------------------------------------------------------------------------


def domain_for(address_or_domain: str) -> str | None:
    """FR-CAP-DIS-09 — the company domain an address (or a domain) gives:
    none for a public webmail domain, which names nobody's company."""
    text = (address_or_domain or "").strip().lower()
    domain = email_rules.domain_of(text) if "@" in text else text
    return domain if domain and domain not in PUBLIC_WEBMAIL_DOMAINS else None


def outside_of(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Every outside participant of the conversation's messages (each a
    `classify` answer), each address once, in the order they appear."""
    seen: dict[str, dict[str, str]] = {}
    for m in messages:
        for p in m.get("outside") or []:
            seen.setdefault(p["email"], {"email": p["email"], "name": p.get("name") or ""})
    return list(seen.values())


def owner_replied(messages: Sequence[Mapping[str, Any]], mailbox_email: str) -> bool:
    """Whether the mailbox owner wrote in the conversation (FR-CAP-DIS-04)."""
    owner = (mailbox_email or "").strip().lower()
    return any((m.get("from_email") or "").lower() == owner for m in messages)


#: FR-CAP-DIS-04, PD-9 — lines of quoted or forwarded history and signature
#: kept after a message's own text.
QUOTED_LINES: Final = 20
#: The prompt's limits (FR-CAP-DIS-04).
MAX_MESSAGES: Final = 12
MAX_CHARS: Final = 24_000


def _signature_start(lines: list[str]) -> int:
    """Where a message's signature starts (`summaries`' patterns: the `-- `
    delimiter, a mobile footer, or a sign-off in the last lines)."""
    for i, line in enumerate(lines):
        if summary_rules._SIGNATURE.match(line.rstrip()) or summary_rules._SIGNATURE.match(
            line.strip()
        ):
            return i
    for i, line in enumerate(lines):
        if (
            summary_rules._SIGN_OFF.match(line)
            and len(lines) - i - 1 <= summary_rules.SIGN_OFF_TAIL
        ):
            return i
    return len(lines)


def trim(text: str) -> str:
    """PD-9 — a message's own text, then at most the first 20 lines of its
    signature and its quoted or forwarded block together (`summaries`' quote
    patterns), so a referral forwarded with "see below" still shows the
    inquiry it forwards. Not `summaries.strip`, which drops the block."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    quote = summary_rules._quote_start(lines)
    own, quoted = lines[:quote], lines[quote:]
    sign = _signature_start(own)
    body, signature = own[:sign], own[sign:]
    kept = (signature + quoted)[:QUOTED_LINES]
    return "\n".join(body + kept).strip()


def _when(at: datetime) -> str:
    return at.astimezone(ZONE).strftime("%Y-%m-%d %H:%M IST")


def _who(name: str | None, address: str | None) -> str:
    address = address or ""
    return f"{name} <{address}>" if name else address


def prompt_parts(
    messages: Sequence[Mapping[str, Any]],
    texts: Mapping[str, str],
    *,
    websites: Sequence[str],
    replied: bool,
) -> list[tuple[str, str]]:
    """The quoted parts of the prompt (contracts §4): one per message, oldest
    first, at most the last 12, the oldest dropped until the texts total at
    most 24,000 characters (one message alone over it is cut at the end);
    then the websites Claude may pick from and whether the owner replied.

    `messages` are `classify` answers with `id` and `attachments`; `texts`
    the plain text by message id. `ai.quote_untrusted` keeps only plain
    characters of a part's label and at most 60 of them, so a message's
    sender, recipients, date and attachments open its text instead."""
    ordered = sorted(messages, key=lambda m: (m["sent_at"], m["id"]))[-MAX_MESSAGES:]
    bodies = [trim(texts.get(m["id"], "")) for m in ordered]
    while len(bodies) > 1 and sum(len(b) for b in bodies) > MAX_CHARS:
        ordered, bodies = ordered[1:], bodies[1:]
    if bodies and len(bodies[0]) > MAX_CHARS:
        bodies[0] = bodies[0][:MAX_CHARS]
    n = len(ordered)
    parts: list[tuple[str, str]] = []
    for i, (m, body) in enumerate(zip(ordered, bodies, strict=True), 1):
        to = ", ".join(p["email"] for p in m.get("participants") or [] if p.get("role") != "from")
        sender = next(
            (p for p in m.get("participants") or [] if p.get("role") == "from"),
            {"email": m.get("from_email") or "", "name": ""},
        )
        names = ", ".join(m.get("attachments") or []) or "none"
        head = (
            f"{m['direction']} · from {_who(sender.get('name'), sender.get('email'))} · "
            f"to {to or 'nobody'} · {_when(m['sent_at'])} · attachments: {names}"
        )
        parts.append((f"Message {i} of {n} {m['direction']}", f"{head}\n\n{body}"))
    listed = "\n".join(f"{i}. {host}" for i, host in enumerate(websites, 1))
    parts.append(("Websites in this conversation", listed or "none"))
    parts.append(("Mailbox owner replied", "yes" if replied else "no"))
    return parts


#: A host written in a message: labels and a letters-only top level.
_HOST: Final = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?"
    r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])",
    re.IGNORECASE,
)
#: Most hosts a prompt lists.
MAX_WEBSITES: Final = 10


def _plain_host(found: str) -> str | None:
    last = found.rsplit(".", 1)[-1]
    # A file name (`notes.pdf`) or an initial and a name (`R.Priya`).
    if last.lower() in ai_rules._FILE_SUFFIXES or (last[0].isupper() and not last.isupper()):
        return None
    host = found.lower()
    return host[4:] if host.startswith("www.") else host


def website_choices(
    outside: Sequence[Mapping[str, str]], texts: Iterable[str], our_domains: Iterable[str]
) -> list[str]:
    """PD-20 — the hosts found verbatim in the conversation, which Claude
    picks the website from by number: the outside participants' domains,
    then hosts in the text (signatures, links), each once, never a public
    webmail domain or one of the workspace's own, at most 10."""
    ours = {d.lower() for d in our_domains}
    found: list[str] = []

    def add(host: str | None) -> None:
        if (
            host
            and host not in found
            and host not in ours
            and host not in PUBLIC_WEBMAIL_DOMAINS
            and len(found) < MAX_WEBSITES
        ):
            found.append(host)

    for p in outside:
        add(domain_for(p["email"]))
    for text in texts:
        for match in _HOST.finditer(text or ""):
            add(_plain_host(match.group(1)))
    return found


def domain_of_finding(
    choice: int | None,
    choices: Sequence[str],
    person_email: str | None,
    outside: Sequence[Mapping[str, str]],
) -> tuple[str | None, bool]:
    """`(domain, domain_from_text)`: the host Claude chose (a number outside
    the list is no choice), else the person's address's domain unless it is
    webmail (FR-CAP-DIS-09), else none. A host that is no participant's own
    domain came from the text, for the reviewer to confirm."""
    own = {d for p in outside if (d := domain_for(p["email"]))}
    if isinstance(choice, int) and 1 <= choice <= len(choices):
        host = choices[choice - 1]
        return host, host not in own
    domain = domain_for(person_email or "") if person_email else None
    return domain, False


def choose_email(
    answered: str | None,
    outside: Sequence[Mapping[str, str]],
    messages: Sequence[Mapping[str, Any]],
) -> str | None:
    """Plan §5.6 — the person's address is one of the thread's outside
    participants; else the first outside sender; else the first outside
    participant."""
    addresses = [p["email"] for p in outside]
    wanted = (answered or "").strip().lower()
    if wanted in addresses:
        return wanted
    for m in sorted(messages, key=lambda m: (m["sent_at"], m["id"])):
        sender = (m.get("from_email") or "").lower()
        if sender in addresses:
            return sender
    return addresses[0] if addresses else None


def lead_stage_for(answered: str | None, replied: bool) -> str:
    """FR-CAP-REV-02 — New when the owner never replied; else Claude's
    suggestion, Contacted when it gave none."""
    if not replied:
        return "new"
    return answered if answered in ("contacted", "qualified") else "contacted"


#: The outcomes that keep a finding (data-model §3.2).
FINDING_OUTCOMES: Final = ("new_lead", "deal_signal")
_NAME_CAP: Final = 200
_ATTACHMENTS: Final = 10
#: 071's CHECK holds a finding to 4096 bytes as stored, and Postgres counts
#: UTF-8 bytes, not characters: a finding is kept under this many, which
#: leaves room for jsonb's own overhead.
FINDING_BYTES: Final = 3584


def _cap(value: Any, size: int = _NAME_CAP) -> str | None:
    """At most `size` characters and `2 * size` bytes in UTF-8, so a name in
    Tamil or Chinese (three bytes a character) is cut shorter."""
    text = " ".join(str(value).split()) if value is not None else ""
    text = text[:size]
    raw = text.encode()
    if len(raw) > 2 * size:
        text = raw[: 2 * size].decode(errors="ignore").rstrip()
    return text or None


def finding_bytes(finding: Mapping[str, Any]) -> int:
    """A finding's size in UTF-8 as JSON."""
    return len(json.dumps(finding, ensure_ascii=False, default=str).encode())


def finding_of(
    answer: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    *,
    mailbox_email: str,
    choices: Sequence[str],
) -> dict[str, Any] | None:
    """Data-model §3.2 `finding` — what a lead or a deal signal keeps (the
    only place a name or an address is kept); none for *other business* or
    *not relevant*. `answer` is the masked `DiscoveryOutput`; `messages`
    the conversation's surviving messages (`classify` answers with `id` and
    `attachments`)."""
    if answer.get("outcome") not in FINDING_OUTCOMES:
        return None
    outside = outside_of(messages)
    replied = owner_replied(messages, mailbox_email)
    email = choose_email(answer.get("person_email"), outside, messages)
    domain, from_text = domain_of_finding(answer.get("website_choice"), choices, email, outside)
    ordered = sorted(messages, key=lambda m: (m["sent_at"], m["id"]))
    names: list[str] = []
    for m in ordered:
        for name in m.get("attachments") or []:
            capped = _cap(name)
            if capped and capped not in names and len(names) < _ATTACHMENTS:
                names.append(capped)
    finding = {
        "company_name": _cap(answer.get("company_name")),
        "domain": domain,
        "domain_from_text": from_text,
        "person_name": _cap(answer.get("person_name")),
        "person_email": email,
        "person_title": _cap(answer.get("person_title")),
        "lead_stage": lead_stage_for(answer.get("lead_stage"), replied),
        "deal_stage": answer.get("deal_stage"),
        "reason": _cap(answer.get("reason"), 160),
        "first_at": ordered[0]["sent_at"].astimezone(ZONE).isoformat() if ordered else None,
        "latest_at": ordered[-1]["sent_at"].astimezone(ZONE).isoformat() if ordered else None,
        "message_count": len(ordered),
        "owner_replied": replied,
        "attachments": names,
        "duplicate_company_id": None,
    }
    # The last attachment names go first until the finding fits (071).
    while names and finding_bytes(finding) > FINDING_BYTES:
        names.pop()
    return finding


#: The job's instruction, after the fixed rules every prompt carries
#: (contracts §4).
INSTRUCTION: Final = (
    "Judge one email conversation of a company that sells software and services, as "
    "seen from its own mailbox. The messages are given oldest first; each opens with "
    "its direction (inbound from someone outside, outbound from the company), sender, "
    "recipients, date and attachment names. Answer with: outcome, new_lead when someone "
    "outside may buy from the company and is not yet a client, deal_signal when the "
    "conversation moves a sale on (a proposal, a quote, a signed order), other_business "
    "for a vendor, a hiring or job conversation, a partner or a service provider, and "
    "not_relevant for anything else (personal, social, notifications); confidence, "
    "high, medium or low; for a new lead or a deal signal, company_name, person_name, "
    "person_email (one of the outside participants), person_title, lead_stage "
    "(contacted or qualified) or deal_stage (discovery, proposal or closed_won), and "
    "website_choice, the number of the company's website in the websites list, or null "
    "when none of them is it; and reason, one line under 160 characters naming what "
    "was asked and whether the mailbox owner replied. Never quote the messages. Use "
    "null for anything not known."
)


# ---------------------------------------------------------------------------
# Failures — PD-17
# ---------------------------------------------------------------------------

#: Most tries one conversation gets before it is `failed` (data-model §3.2).
MAX_ATTEMPTS: Final = 3


def failure_counts(exc: BaseException) -> bool:
    """Whether a failure belongs to the conversation, and counts toward its
    attempts: Claude answered off-schema twice, or the thread is malformed.
    Every other failure (Gmail, a token, Claude's service) is the service's:
    it ends the run and the candidate stays pending as it was."""
    if getattr(exc, "reason", None) == ai_rules.OUTPUT_INVALID:
        return True
    return type(exc).__name__ == "MalformedThread"


# ---------------------------------------------------------------------------
# The recall check — FR-CAP-RPT-03; plan §5.7, PD-11
# ---------------------------------------------------------------------------

#: Data-model §3.5 — a labelled conversation's fate in the recall check.
#: `ignored` and `known_now` are never sent to Claude (G-5, NFR-CAP-02).
FATES: Final = (
    "filed_known",
    "found",
    "judged_otherwise",
    "skipped_rules",
    "outside_scope",
    "ignored",
    "known_now",
)
#: Labelled mail is judged as if it had no label: through Primary and Sent alone.
CAPTURED: Final = (PRIMARY, SENT)


def recall_fate(
    *,
    in_capture: bool,
    survived: bool,
    known_before: bool,
    ignored: bool = False,
    known_now: bool = False,
    outcome: str | None = None,
    confidence: str | None = None,
) -> str:
    """PD-11 — what capture would have done with a labelled conversation:
    outside Primary and Sent; skipped by the rules; every outside person on
    the ignore list; filed as known through a person or company that
    existed before it began; every message naming someone in the CRM now
    (its record made later, often from the label itself), so not judged:
    Claude never sees known-client mail (G-5); or judged, `found` only as a
    high- or medium-confidence lead or deal signal."""
    if not in_capture:
        return "outside_scope"
    if not survived:
        return "skipped_rules"
    if ignored:
        return "ignored"
    if known_before:
        return "filed_known"
    if known_now:
        return "known_now"
    if outcome in FINDING_OUTCOMES and confidence in ("high", "medium"):
        return "found"
    return "judged_otherwise"


def recall_line(rows: Sequence[Mapping[str, Any]]) -> str:
    """ "Found N of your M labelled conversations; K of the rest are outside
    Primary and Sent." (spec §4.5), then, when there are any, how many were
    not judged because they name only people already in the CRM."""
    total = len(rows)
    found = sum(1 for r in rows if r.get("fate") in ("filed_known", "found"))
    outside = sum(1 for r in rows if r.get("fate") == "outside_scope")
    known_now = sum(1 for r in rows if r.get("fate") == "known_now")
    line = (
        f"Found {found} of your {total} labelled conversations; "
        f"{outside} of the rest are outside Primary and Sent."
    )
    if known_now:
        line += (
            f" {known_now} of the rest name only people added to the CRM after they began,"
            " so they were not sent to Claude."
        )
    return line


# ---------------------------------------------------------------------------
# The ignore list — FR-CAP-DIS-03, FR-CAP-REV-03; contracts §1.3, §2.4; PD-21
# ---------------------------------------------------------------------------

#: Contracts §1.3, §2.4, §2.5 — the sentences.
ENTER_ADDRESS: Final = "Enter an email address."
ENTER_DOMAIN: Final = "Enter a domain such as example.com."
WEBMAIL_IGNORE: Final = "A public email domain cannot be ignored; ignore the sender instead."
OWN_DOMAIN: Final = "Your own domains cannot be ignored."
ALREADY_IGNORED: Final = "That is already on the ignore list."
NO_SUCH_IGNORE: Final = "No such ignore entry."
ONLY_NEW_LEAD: Final = "Only a new lead can add to the ignore list."
#: What an entry holds (073's CHECK).
IGNORE_KINDS: Final = ("address", "domain")
_ADDRESS: Final = re.compile(
    r"^[a-z0-9._%+'-]+@[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
_DOMAIN: Final = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def ignore_value(value: str | None) -> str:
    """An entry as stored: trimmed and lower case."""
    return (value or "").strip().lower()


def ignore_refusal(kind: str, value: str, own_domains: Iterable[str]) -> str | None:
    """Contracts §2.4 — an address or a domain of the right shape (3 to 320
    characters), never a public webmail domain (its sender is ignored
    instead) and never one of the workspace's own domains."""
    ours = {d.lower() for d in own_domains}
    if kind == "address":
        if not (3 <= len(value) <= 320) or not _ADDRESS.match(value):
            return ENTER_ADDRESS
        return OWN_DOMAIN if email_rules.domain_of(value) in ours else None
    if kind != "domain" or not (3 <= len(value) <= 320) or not _DOMAIN.match(value):
        return ENTER_DOMAIN
    if value in PUBLIC_WEBMAIL_DOMAINS:
        return WEBMAIL_IGNORE
    return OWN_DOMAIN if value in ours else None


def ignored(outside: Sequence[Mapping[str, str]], ignores: Iterable[Mapping[str, Any]]) -> bool:
    """FR-CAP-DIS-03 — every outside participant is on the ignore list, by
    address or by domain. A conversation with nobody outside is not."""
    ignores = list(ignores or [])
    addresses = {i["value"] for i in ignores if i["kind"] == "address"}
    domains = {i["value"] for i in ignores if i["kind"] == "domain"}
    people = [(p.get("email") or "").lower() for p in outside]
    return bool(people) and all(
        a in addresses or email_rules.domain_of(a) in domains for a in people
    )


# ---------------------------------------------------------------------------
# Duplicates — FR-CAP-DIS-08 (plan §5.8)
# ---------------------------------------------------------------------------


#: The labels that make a two-label suffix under a country code (`co.uk`,
#: `com.au`, `ac.in`); every other suffix is the last label alone.
_SECOND_LEVEL: Final = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})


def registrable_name(domain: str | None) -> str | None:
    """The name of a bare `name.suffix` domain (`acme` for `acme.com` and
    `acme.co.uk`); None for a domain with a subdomain (`mail.foo.com`,
    `uk.sony.com`), whose first label names no company."""
    labels = (domain or "").split(".")
    if len(labels) < 2 or not all(labels):
        return None
    suffix = 2 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 1
    return labels[0] if len(labels) == suffix + 1 else None


def sibling_domains(domain: str | None, known: Iterable[str]) -> list[str]:
    """The known domains with the same name on another public suffix
    (`acme.io` and `acme.com`, `acme.co.uk`), each once, in order. Both
    must be bare `name.suffix` domains: a subdomain has no sibling."""
    name = registrable_name(domain)
    if name is None:
        return []
    out: list[str] = []
    for other in known:
        if other != domain and registrable_name(other) == name and other not in out:
            out.append(other)
    return out


# ---------------------------------------------------------------------------
# Items from findings — FR-CAP-REV-01, -04, FR-CAP-DIS-07 to -09; contracts
# §6.1, §6.2; plan §5.10, PD-4
# ---------------------------------------------------------------------------

#: Contracts §1.3 — a lead stage to approve at.
LEAD_STAGES: Final = ("new", "contacted", "qualified")
CHOOSE_LEAD_STAGE: Final = "Choose a lead stage: New, Contacted or Qualified."
#: The stages a discovered deal signal moves an open deal to, by rank.
_MOVE_RANK: Final = {"discovery": 0, "qualification": 1, "proposal": 2, "negotiation": 3}
_TARGET_RANK: Final = {"proposal": 2, "closed_won": 4}


def split_name(name: str | None, email: str | None) -> tuple[str, str | None]:
    """`(first, last)` from a person's name; with none, the address's local
    part, so a person always has a first name."""
    words = (name or "").split()
    if words:
        return words[0][:100], (" ".join(words[1:])[:100] or None)
    local = (email or "").split("@", 1)[0] or "Unknown"
    return local[:100], None


def _company_name(finding: Mapping[str, Any]) -> str:
    """Claude's company name; else the domain; else the person."""
    return (
        finding.get("company_name")
        or finding.get("domain")
        or finding.get("person_name")
        or finding.get("person_email")
        or "Unknown company"
    )[:200]


def _evidence(finding: Mapping[str, Any], confidence: str) -> dict[str, Any]:
    return {
        "first_at": finding.get("first_at"),
        "latest_at": finding.get("latest_at"),
        "message_count": int(finding.get("message_count") or 0),
        "owner_replied": bool(finding.get("owner_replied")),
        "attachments": list(finding.get("attachments") or []),
        "reason": finding.get("reason"),
        "confidence": confidence,
        "origin": "discovery",
    }


def _base_item(
    *,
    mailbox_id: str,
    thread_id: str,
    message_id: str,
    owner_id: str | None,
    model: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    key = source_key(mailbox_id, thread_id)
    return {
        "owner_id": owner_id,
        "current_values": {},
        "source_kind": "email_thread",
        "source_key": key,
        "source_version": message_id,
        "source_link": {"mailbox_id": mailbox_id, "thread_id": thread_id},
        "model": model,
        "generated_at": generated_at,
    }


def new_lead_item(
    finding: Mapping[str, Any],
    *,
    confidence: str,
    mailbox_id: str,
    thread_id: str,
    message_id: str,
    owner_id: str | None,
    model: str | None,
    generated_at: str | None,
    company_id: str | None = None,
    deal_stage_hint: str | None = None,
) -> dict[str, Any]:
    """Contracts §6.1 — a `new_lead` as `crm_review_upsert` takes it: with
    no target, or linked to the existing company a duplicate check matched
    (`link: true`). A public webmail address gives no domain (FR-CAP-DIS-09)."""
    first, last = split_name(finding.get("person_name"), finding.get("person_email"))
    item = _base_item(
        mailbox_id=mailbox_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_id=owner_id,
        model=model,
        generated_at=generated_at,
    )
    domain = finding.get("domain")
    domain = domain_for(domain) if domain else None
    item.update(
        type="new_lead",
        record_type="company" if company_id else None,
        record_id=company_id,
        suggestion={
            "company_name": _company_name(finding),
            "domain": domain,
            "domain_from_text": bool(finding.get("domain_from_text")) and domain is not None,
            "lead_stage": finding.get("lead_stage") or "new",
            "first_name": first,
            "last_name": last,
            "email": finding.get("person_email"),
            "title": finding.get("person_title"),
            "link": company_id is not None,
            "deal_stage_hint": deal_stage_hint,
            "evidence": _evidence(finding, confidence),
        },
        dedupe_key=review_rules.dedupe_key(
            "email_thread",
            item["source_key"],
            "new_lead",
            "company" if company_id else None,
            company_id,
        ),
        deal_choices=[],
    )
    return item


def discovered_signal_item(
    finding: Mapping[str, Any],
    *,
    confidence: str,
    mailbox_id: str,
    thread_id: str,
    message_id: str,
    owner_id: str | None,
    model: str | None,
    generated_at: str | None,
    company_id: str,
    open_deals: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Contracts §6.2 — a discovered `deal_signal` on a matched company:
    Proposal or Closed Won for its open deals still before that stage, or a
    new deal at Discovery only when the company has no open deal. None when
    every open deal is already at or past the stage, or when Claude
    suggested Discovery (or no stage) for a company with an open deal: a
    second open deal would be a duplicate."""
    stage = finding.get("deal_stage")
    target = _TARGET_RANK.get(str(stage))
    choices = (
        [d for d in open_deals if _MOVE_RANK.get(d.get("stage"), 9) < target]
        if target is not None
        else []
    )
    if open_deals and not choices:
        return None
    item = _base_item(
        mailbox_id=mailbox_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_id=owner_id,
        model=model,
        generated_at=generated_at,
    )
    if choices:
        suggestion: dict[str, Any] = {"action": "move", "stage": stage}
        if stage == "proposal" and finding.get("latest_at"):
            suggestion["proposal_sent_on"] = str(finding["latest_at"])[:10]
    else:
        suggestion = {"action": "create", "stage": "discovery"}
    domain = finding.get("domain")
    domain = domain_for(domain) if domain else None
    suggestion["evidence"] = {
        **_evidence(finding, confidence),
        # What tied the conversation to this company, so a match made only
        # through a website named in the email's text can be checked.
        "email": finding.get("person_email"),
        "domain": domain,
        "domain_from_text": bool(finding.get("domain_from_text")) and domain is not None,
    }
    item.update(
        type="deal_signal",
        record_type="company",
        record_id=company_id,
        suggestion=suggestion,
        dedupe_key=review_rules.dedupe_key(
            "email_thread", item["source_key"], "deal_signal", "company", company_id
        ),
        deal_choices=sorted(d["id"] for d in choices),
    )
    return item


def to_item(
    finding: Mapping[str, Any] | None,
    *,
    outcome: str,
    confidence: str,
    mailbox_id: str,
    thread_id: str,
    message_id: str,
    owner_id: str | None,
    model: str | None,
    generated_at: str | None,
    duplicate_company_id: str | None = None,
    open_deals: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """FR-CAP-DIS-07, PD-4 — the item a judged conversation makes: none for
    low confidence or an outcome without a finding; a `new_lead` (linked
    when a duplicate matched); a Claude `deal_signal` on an unknown company
    becomes a `new_lead` with the deal stage as a hint, and a `deal_signal`
    on the matched company when a duplicate matched."""
    if finding is None or outcome not in FINDING_OUTCOMES or confidence == "low":
        return None
    common = {
        "confidence": confidence,
        "mailbox_id": mailbox_id,
        "thread_id": thread_id,
        "message_id": message_id,
        "owner_id": owner_id,
        "model": model,
        "generated_at": generated_at,
    }
    if outcome == "deal_signal" and duplicate_company_id:
        return discovered_signal_item(
            finding, company_id=duplicate_company_id, open_deals=open_deals, **common
        )
    hint = finding.get("deal_stage") if outcome == "deal_signal" else None
    return new_lead_item(finding, company_id=duplicate_company_id, deal_stage_hint=hint, **common)


def new_lead_title(suggestion: Mapping[str, Any]) -> str:
    """Contracts §6.1 — "New lead: Acme Robotics · Priya Shah"."""
    person = " ".join(p for p in (suggestion.get("first_name"), suggestion.get("last_name")) if p)
    name = suggestion.get("company_name") or "Unknown company"
    return f"New lead: {name} · {person}" if person else f"New lead: {name}"


def new_lead_detail(suggestion: Mapping[str, Any]) -> str:
    """Contracts §6.1 — "2 emails · Asked for pricing; you replied."."""
    evidence = suggestion.get("evidence") or {}
    n = int(evidence.get("message_count") or 0)
    count = f"{n} email" if n == 1 else f"{n} emails"
    return " · ".join(p for p in (count, evidence.get("reason")) if p)


# ---------------------------------------------------------------------------
# Applying a dry run — FR-CAP-RPT-04; contracts §2.3
# ---------------------------------------------------------------------------

NO_SUCH_RUN: Final = "No such dry run."
NOT_FINISHED: Final = "This dry run has not finished."
STOPPED: Final = "This dry run was stopped before it finished; run it again."
TOO_OLD: Final = "This dry run finished more than 7 days ago; run it again."
NOT_LATEST: Final = "A newer dry run exists; apply that one."
ALREADY_APPLIED: Final = "This dry run was already applied."
#: A dry run's report can be applied for this long after it finished.
APPLY_DAYS: Final = 7


def apply_refusal(
    run: Mapping[str, Any] | None, latest_id: str | None, now: datetime
) -> tuple[int, str] | None:
    """`(status, sentence)` when the run cannot be applied: not found, not
    finished, cancelled, applied already, not the latest, or finished more
    than 7 days ago (the report's age, not the start's)."""
    if run is None:
        return 404, NO_SUCH_RUN
    finished = _at(run.get("finished_at"))
    if finished is None:
        return 422, NOT_FINISHED
    if run.get("cancelled"):
        return 422, STOPPED
    if run.get("applied_at"):
        return 409, ALREADY_APPLIED
    if run["id"] != latest_id:
        return 422, NOT_LATEST
    if now - finished > timedelta(days=APPLY_DAYS):
        return 422, TOO_OLD
    return None
