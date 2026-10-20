"""Gmail label sync, the rules — spec 007 FR-CRM-SYNC-01, -05, -06, -08,
FR-CRM-FLAG-04, FR-CRM-PIPE-02, FR-CRM-AUTO-01 (automations 2 and 3);
Q-29; plan §2.2, §5.13.

What a labelled message means for the CRM, decided from its headers alone
(no body is ever read or stored, Q-29):

- **Our domains** are the workspace's allow-list (FR-CRM-ACC-06). A message
  whose every participant is in them is internal and skipped altogether.
- **Direction**: outbound when the sender is in our domains, else inbound.
- **Auto-replies and bounces** are stored but never count for a flag or a
  "last contact" (FR-CRM-FLAG-04), where the headers make them detectable.
- **Matching**: each outside participant is matched to a person by any of
  their emails, then to a company by the email's domain. One with neither
  creates what is needed (automation 2, behind its switch): a company at
  New for an unknown domain and a person in it, owned by the mailbox owner,
  Source Email sync. A public webmail address (gmail.com and the like)
  makes a person with no company: it names nobody's company.
- **New → Contacted** on the first outbound email (FR-CRM-PIPE-02,
  automation 3, not switchable).

And the mailbox setting's rules (FR-CRM-SYNC-01, amended): the owner is a
member, the address is the owner's own unless a TVA admin registers it, its
domain is one the service account covers (`GMAIL_DOMAINS`), and no label is
a Gmail system label.

Pure: the service passes headers, rows and the clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from email.utils import getaddresses, parseaddr
from typing import Any, Final

from app.domain.crm.invites import PUBLIC_WEBMAIL_DOMAINS

#: The Google Workspace domains the service account is delegated for
#: (FR-CRM-SYNC-03, plan §2.2); 039 holds the same list as a CHECK.
GMAIL_DOMAINS: Final = ("nunnarilabs.com", "deepweaver.ai", "nunnari.academy")

#: Gmail's system labels, by id; their names are the same words in any case.
#: Any `CATEGORY_*` is one too (plan §5.13).
SYSTEM_LABELS: Final = frozenset(
    {"INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "UNREAD", "CHAT"}
)

#: FR-CRM-SYNC-08.
BACKFILL_DAYS: Final = 90
#: Labels per mailbox (039 CHECK).
MAX_LABELS: Final = 20

#: The headers the sync asks Gmail for (`format=metadata`).
HEADERS: Final = (
    "From",
    "To",
    "Cc",
    "Subject",
    "Date",
    "Auto-Submitted",
    "Precedence",
    "X-Autoreply",
    "X-Autorespond",
    "X-Auto-Response-Suppress",
    "Content-Type",
    # Spec 010 FR-CAP-SRC-03 (PD-2): captured bulk mail is skipped by it.
    "List-Unsubscribe",
)

#: Senders that only ever send bounces and notices.
_DAEMONS: Final = ("mailer-daemon@", "postmaster@")
#: Subjects of auto-replies and delivery failures, lower case, as they start.
_AUTO_SUBJECTS: Final = (
    "automatic reply",
    "auto reply",
    "auto-reply",
    "autoreply",
    "out of office",
    "out of the office",
    "undeliverable",
    "undelivered mail",
    "delivery status notification",
    "mail delivery failed",
    "mail delivery failure",
    "delivery failure",
    "returned mail",
)

# ---------------------------------------------------------------------------
# Mailboxes — FR-CRM-SYNC-01, amended (plan §5.13)
# ---------------------------------------------------------------------------

OWNER_NOT_MEMBER: Final = "The mailbox owner must be a member of this workspace."
NOT_OWN_ADDRESS: Final = (
    "A mailbox must be its owner's own email address; only a TVA admin can register "
    "another address."
)
NO_LABELS: Final = "Name at least one Gmail label to sync."
TOO_MANY_LABELS: Final = f"A mailbox syncs at most {MAX_LABELS} labels."
TAKEN: Final = "That address is already registered as a mailbox in a workspace."


def is_system_label(label: str) -> bool:
    """Inbox, Sent, the categories and the like, by id or name, in any case."""
    word = label.strip().upper().replace(" ", "_")
    return word in SYSTEM_LABELS or word.startswith("CATEGORY_")


def clean_labels(labels: Iterable[str]) -> list[str]:
    """Trimmed, each once (case-insensitive), in the order given."""
    seen: set[str] = set()
    out = []
    for label in labels:
        text = " ".join(str(label).split())
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def mailbox_refusal(
    *,
    email: str,
    owner_email: str | None,
    owner_is_member: bool,
    by_tva_admin: bool,
    labels: list[str],
    capture_start_date: date | None = None,
    today: date | None = None,
) -> str | None:
    """Every rule of a mailbox setting, in the contract's order (contracts
    §2.10); each is a 422. Spec 010 (contracts §1.1): the capture start date,
    when given, is not after `today`."""
    if not owner_is_member:
        return OWNER_NOT_MEMBER
    if not by_tva_admin and email != (owner_email or "").strip().lower():
        return NOT_OWN_ADDRESS
    if domain_of(email) not in GMAIL_DOMAINS:
        return (
            f"Gmail sync can read mailboxes at {', '.join(GMAIL_DOMAINS[:-1])} or "
            f"{GMAIL_DOMAINS[-1]} only."
        )
    if not labels:
        return NO_LABELS
    if len(labels) > MAX_LABELS:
        return TOO_MANY_LABELS
    system = [label for label in labels if is_system_label(label)]
    if system:
        return f"{system[0]} is a Gmail system label; sync a label of your own."
    if capture_start_date is not None and today is not None and capture_start_date > today:
        # Imported here: the capture rules read this module's headers.
        from app.domain.crm.capture import START_IN_FUTURE

        return START_IN_FUTURE
    return None


# ---------------------------------------------------------------------------
# A message's headers — FR-CRM-SYNC-05, -06, FR-CRM-FLAG-04
# ---------------------------------------------------------------------------


def header(headers: Iterable[dict[str, Any]], name: str) -> str | None:
    """The first header called `name` (case-insensitive)."""
    wanted = name.lower()
    return next(
        (str(h.get("value") or "") for h in headers if str(h.get("name", "")).lower() == wanted),
        None,
    )


def _addresses(value: str | None) -> list[tuple[str, str]]:
    return [
        (" ".join(name.split()), addr.strip().lower())
        for name, addr in getaddresses([value or ""])
        if "@" in addr
    ]


def participants(headers: list[dict[str, Any]]) -> list[dict[str, str]]:
    """`[{email, name, role}]`, the sender first, each address once."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for role, name in (("from", "From"), ("to", "To"), ("cc", "Cc")):
        for display, addr in _addresses(header(headers, name)):
            if addr not in seen:
                seen.add(addr)
                out.append({"email": addr[:254], "name": display[:200], "role": role})
    return out


def is_auto(headers: list[dict[str, Any]], sender: str) -> bool:
    """FR-CRM-FLAG-04 — an auto-reply or a bounce, where the headers say so
    (RFC 3834 Auto-Submitted, the common vendor headers, a delivery report,
    a mailer daemon, or the subjects those messages carry)."""
    auto = (header(headers, "Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if header(headers, "X-Autoreply") or header(headers, "X-Autorespond"):
        return True
    if (header(headers, "Precedence") or "").strip().lower() in ("auto_reply", "bulk", "junk"):
        return True
    if "report-type=delivery-status" in (header(headers, "Content-Type") or "").lower():
        return True
    if sender.startswith(_DAEMONS):
        return True
    subject = (header(headers, "Subject") or "").strip().lower()
    return subject.startswith(_AUTO_SUBJECTS)


def is_ours(address: str, our_domains: Iterable[str]) -> bool:
    return domain_of(address) in {d.lower() for d in our_domains}


def sent_at(internal_date_ms: Any) -> datetime:
    """Gmail's `internalDate` (milliseconds since the epoch) is when the
    message reached the mailbox; it is what Gmail sorts by, and unlike the
    Date header it cannot be forged by the sender."""
    return datetime.fromtimestamp(int(internal_date_ms) / 1000, UTC)


def classify(
    headers: list[dict[str, Any]], our_domains: Iterable[str], internal_date_ms: Any
) -> dict[str, Any]:
    """`{from_email, participants, direction, is_internal, is_auto, outside,
    subject, sent_at}` — `outside` are the participants not in our domains,
    `[{email, name}]`."""
    ours = list(our_domains)
    people = participants(headers)
    sender = parseaddr(header(headers, "From") or "")[1].strip().lower()
    outside = [
        {"email": p["email"], "name": p["name"]} for p in people if not is_ours(p["email"], ours)
    ]
    return {
        "from_email": sender[:254] or None,
        "participants": people,
        "direction": "outbound" if sender and is_ours(sender, ours) else "inbound",
        "is_internal": bool(people) and not outside,
        "is_auto": is_auto(headers, sender),
        "outside": outside,
        "subject": (header(headers, "Subject") or "").strip()[:500] or None,
        "sent_at": sent_at(internal_date_ms),
    }


def backfill_after(now: datetime) -> datetime:
    """FR-CRM-SYNC-08 — the first connection reads the last 90 days."""
    return now - timedelta(days=BACKFILL_DAYS)


# ---------------------------------------------------------------------------
# Matching and creating — FR-CRM-SYNC-05, automation 2
# ---------------------------------------------------------------------------


def company_name(domain: str) -> str:
    """'acme.co.uk' → 'Acme', 'mail.globex.com' → 'Globex': the name a
    company created from an email domain starts with; the domain itself is
    kept as its domain, and anyone can rename it."""
    parts = domain.split(".")
    second_level = ("co", "com", "org", "net", "ac", "edu", "gov")
    if len(parts) >= 3 and parts[-2] in second_level:
        base = parts[-3]
    else:
        base = parts[-2] if len(parts) >= 2 else parts[0]
    return (base.replace("-", " ").title() or domain)[:200]


def person_name(display: str | None, address: str) -> tuple[str, str | None]:
    """`(first, last)` from the display name, or else the address's local
    part ('priya.raman@…' → Priya Raman)."""
    words = (display or "").replace('"', "").split()
    if not words or "@" in (display or ""):
        local = address.split("@", 1)[0]
        words = [w for w in local.replace("_", ".").replace("-", ".").split(".") if w]
        words = [w.capitalize() for w in words] or [local]
    first = words[0][:100]
    last = " ".join(words[1:])[:100] or None
    return first, last


def to_create(
    outside: Iterable[dict[str, str]],
    people: dict[str, dict[str, Any]],
    companies: dict[str, str],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """What automation 2 creates for a page's outside participants:
    `({domain: company name}, {email: {first_name, last_name, domain}})`.
    `people` maps an email to its person, `companies` a domain to its
    company id. A public webmail address gets a person with no company
    (`domain` None)."""
    new_companies: dict[str, str] = {}
    new_people: dict[str, dict[str, Any]] = {}
    for p in outside:
        address = p["email"]
        if address in people or address in new_people:
            continue
        domain = domain_of(address)
        if not domain:
            continue
        public = domain in PUBLIC_WEBMAIL_DOMAINS
        if not public and domain not in companies:
            new_companies.setdefault(domain, company_name(domain))
        first, last = person_name(p.get("name"), address)
        new_people[address] = {
            "first_name": first,
            "last_name": last,
            "domain": None if public else domain,
        }
    return new_companies, new_people


def links_for(
    outside: Iterable[dict[str, str]],
    people: dict[str, dict[str, Any]],
    companies: dict[str, str],
    open_deals: dict[str, list[str]],
) -> list[dict[str, str]]:
    """The records a message is about: each outside participant's person,
    their company (the person's own, else the one with their email's
    domain), and that company's open deals. Each once, in that order."""
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(record_type: str, record_id: str | None) -> None:
        if record_id and (record_type, record_id) not in seen:
            seen.add((record_type, record_id))
            links.append({"record_type": record_type, "record_id": record_id})

    for p in outside:
        person = people.get(p["email"])
        company = (person or {}).get("company_id") or companies.get(domain_of(p["email"]))
        add("person", (person or {}).get("id"))
        add("company", company)
        for deal in open_deals.get(company or "", []):
            add("opportunity", deal)
    return links


# ---------------------------------------------------------------------------
# What a record learns — last contact, New → Contacted
# ---------------------------------------------------------------------------


def latest(messages: Iterable[dict[str, Any]]) -> dict[str, datetime]:
    """`{last_inbound_at, last_outbound_at, last_contact_at}` from the
    messages that count (no auto-reply or bounce); a key is absent when no
    message gives it."""
    out: dict[str, datetime] = {}
    for m in messages:
        if m.get("is_auto") or m.get("is_internal"):
            continue
        at = m["sent_at"]
        key = "last_inbound_at" if m["direction"] == "inbound" else "last_outbound_at"
        for k in (key, "last_contact_at"):
            if k not in out or at > out[k]:
                out[k] = at
    return out


def _at(value: Any) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def forward(row: dict[str, Any], seen: dict[str, datetime]) -> dict[str, str]:
    """The `last_*` values that move forward: a stored time is never moved
    back, by a late page or by a removed label (plan §9)."""
    changes = {}
    for key, at in seen.items():
        current = _at(row.get(key))
        if current is None or at > current:
            changes[key] = at.isoformat()
    return changes


def contacted(company: dict[str, Any], messages: Iterable[dict[str, Any]]) -> bool:
    """FR-CRM-PIPE-02 — a company at New moves to Contacted when the first
    outbound email (not an auto-reply) is recorded."""
    return company.get("lead_stage") == "new" and any(
        m["direction"] == "outbound" and not m.get("is_auto") for m in messages
    )
