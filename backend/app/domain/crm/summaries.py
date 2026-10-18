"""Email thread summaries' rules — spec 009 FR-CRMAI-SUM-01, -03 to -07, -12;
Q-07, Q-37; data-model §3.2.2, §3.3; plan §5.5.

Pure. Which stored messages a summary reads (`qualifying`), what is cut
from each before the prompt (`strip`: quoted reply history and signatures,
FR-CRMAI-SUM-03), the prompt's parts with the team list the model names
owners from (FR-CRMAI-SUM-12), and the Review Queue items a summary makes
(`items`): one `next_step_change` when a linked open deal's next step or
date differs, with every linked open deal as a choice (FR-CRMAI-SUM-06,
Q-07), and one `task_from_commitment` per dated commitment
(FR-CRMAI-SUM-07).

A commitment's item takes the commitment's fingerprint as its source
version, not the summary's watermark: a re-summary after a new message
finds the same item (open, approved or dismissed) and does not suggest that
commitment again, which FR-CRMAI-SUM-07 asks of every re-summary. The
item's target follows the thread's records, so when they change between two
summaries `crm_store_summary` (062) leaves out a commitment whose
fingerprint the thread already has an item for under another target.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

from app.domain.crm import ai_rules
from app.domain.crm import review as review_rules

#: What the job records in the usage log and the job log.
JOB: Final = "summaries"
#: The phase switch it runs behind (FR-CRMAI-FLAG-01).
PHASE: Final = "1"
SOURCE_KIND: Final = "email_thread"
#: Deal stages that count as open (030).
OPEN_STAGES: Final = ("discovery", "qualification", "proposal", "negotiation")

#: The job's instruction, after the fixed rules every prompt carries.
INSTRUCTION: Final = (
    "Summarise one email thread between Nunnari and a client. The messages are "
    "given oldest first; each says whether the client sent it (inbound) or Nunnari "
    "did (outbound). Answer with: summary, two or three plain sentences on what was "
    "said; asks, what the client asked Nunnari for, each paraphrased in at most 200 "
    "characters (may be empty); commitments, what Nunnari promised, each paraphrased "
    "in at most 200 characters, with owner as the number of the Nunnari team member "
    "who made it from the team list, or unknown, and due_on as a date only when one "
    "was stated, else null; next_step and next_step_on, the next step and its date "
    "only when the thread implies one, else null; sentiment, positive, neutral or "
    "concern. Paraphrase; never quote the emails."
)


# ---------------------------------------------------------------------------
# Which messages — FR-CRMAI-SUM-01
# ---------------------------------------------------------------------------


def qualifying(messages: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The messages a summary reads: not internal-only, not an auto-reply or
    bounce, and linked to a company or person (`links`), oldest first."""
    kept = [
        m
        for m in messages
        if not m.get("is_internal")
        and not m.get("is_auto")
        and any(lk.get("record_type") in ("company", "person") for lk in m.get("links") or [])
    ]
    return sorted(kept, key=lambda m: (str(m.get("sent_at")), str(m.get("gmail_message_id"))))


# ---------------------------------------------------------------------------
# Stripping — FR-CRMAI-SUM-03
# ---------------------------------------------------------------------------

#: Where quoted reply history starts: everything from it on goes.
_QUOTE_STARTS: Final = (
    # "On Mon, 12 Oct 2026 at 10:00, Priya <priya@acme.in> wrote:"
    re.compile(r"^\s*On\b.{0,300}\bwrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.IGNORECASE),
    # An Outlook header block: "From: …" followed by Sent/Date/To/Subject.
    re.compile(r"^\s*From:\s.+$", re.IGNORECASE),
)
_HEADER_AFTER_FROM: Final = re.compile(r"^\s*(Sent|Date|To|Cc|Subject):\s", re.IGNORECASE)
#: The signature delimiter ("-- " on its own line) and mobile footers.
_SIGNATURE: Final = re.compile(r"^(--\s?|__+|Sent from my .+|Get Outlook for .+)$")
#: A sign-off line ("Thanks,", "Best regards") near the end starts the
#: signature block.
_SIGN_OFF: Final = re.compile(
    r"^\s*(thanks|thank you|many thanks|regards|best|best regards|kind regards|"
    r"warm regards|warmly|cheers|sincerely|yours sincerely|yours truly|thanks and regards|"
    r"thanks & regards)\s*[,.!]?\s*$",
    re.IGNORECASE,
)
#: How many lines after a sign-off still count as a signature block.
SIGN_OFF_TAIL: Final = 8


def _quote_start(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.lstrip().startswith(">"):
            return i
        for pattern in _QUOTE_STARTS:
            if pattern.match(line):
                if pattern is _QUOTE_STARTS[-1]:
                    following = [ln for ln in lines[i + 1 : i + 5] if ln.strip()]
                    if not any(_HEADER_AFTER_FROM.match(ln) for ln in following):
                        continue
                return i
    return len(lines)


def strip(text: str) -> str:
    """A message without its quoted reply history (`On … wrote:`, `>` lines,
    `-----Original Message-----`, a `From:` header block, a forwarded
    message) and without its signature (the `-- ` delimiter, a mobile
    footer, or a sign-off line in the last lines and what follows it)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = lines[: _quote_start(lines)]
    for i, line in enumerate(lines):
        if _SIGNATURE.match(line.rstrip()) or _SIGNATURE.match(line.strip()):
            lines = lines[:i]
            break
    for i, line in enumerate(lines):
        if _SIGN_OFF.match(line) and len(lines) - i - 1 <= SIGN_OFF_TAIL:
            lines = lines[:i]
            break
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# The prompt — FR-CRMAI-LLM-04, FR-CRMAI-SUM-12
# ---------------------------------------------------------------------------


def roster(
    members: Sequence[Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The workspace's Admins and Members with an active account, by name,
    as the prompt numbers them from 1: `[{user_id, name, email}]`."""
    out = []
    for m in members:
        person = profiles.get(m["user_id"]) or {}
        if m.get("role") in ("admin", "member") and person.get("is_active", False):
            out.append(
                {
                    "user_id": m["user_id"],
                    "name": person.get("display_name") or "",
                    "email": person.get("email") or "",
                }
            )
    return sorted(out, key=lambda p: (p["name"].casefold(), p["user_id"]))


def prompt_parts(
    messages: Sequence[Mapping[str, Any]], texts: Sequence[str], team: Sequence[Mapping[str, Any]]
) -> list[tuple[str, str]]:
    """The quoted parts of the prompt: the team list the model names owners
    from, then each message's stripped text with whether the client or
    Nunnari sent it. Nothing else of the email reaches the model."""
    listed = "\n".join(f"{i}. {p['name']} <{p['email']}>" for i, p in enumerate(team, 1))
    parts = [("Nunnari team", listed or "(nobody)")]
    for i, (message, text) in enumerate(zip(messages, texts, strict=True), 1):
        who = "Nunnari (outbound)" if message.get("direction") == "outbound" else "client (inbound)"
        parts.append((f"Message {i} from {who}", text))
    return parts


def commitments(
    answered: Sequence[Mapping[str, Any]], team: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The commitments as stored: the owner a member id of this workspace,
    or unknown (FR-CRMAI-SUM-12)."""
    out = []
    for c in answered:
        owner = ai_rules.resolve_owner(c.get("owner"), team)
        due = c.get("due_on")
        out.append(
            {
                "text": c["text"],
                "owner_id": owner,
                "owner_unknown": owner is None,
                "due_on": due.isoformat() if isinstance(due, date) else due,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Review Queue items — FR-CRMAI-SUM-06, -07; data-model §3.2.1
# ---------------------------------------------------------------------------


def source_key(mailbox_id: str, thread_id: str) -> str:
    return f"gmail:{mailbox_id}:{thread_id}"


def _day(value: Any) -> str | None:
    if value in ("", None):
        return None
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def items(
    *,
    mailbox_id: str,
    thread_id: str,
    watermark_id: str,
    model: str,
    generated_at: str,
    next_step: str | None,
    next_step_on: Any,
    stored_commitments: Sequence[Mapping[str, Any]],
    deals: Sequence[Mapping[str, Any]],
    companies: Sequence[Mapping[str, Any]],
    people: Sequence[Mapping[str, Any]],
    mailbox_owner: str | None,
    roles: Mapping[str, str],
    active: set[str],
) -> list[dict[str, Any]]:
    """The items one summary makes, as `crm_review_upsert` takes them.

    `deals` are the thread's linked open deals (`id, owner_id, company_id,
    next_step, next_step_on`), `companies` and `people` its linked live
    companies and people (`id, owner_id`)."""
    key = source_key(mailbox_id, thread_id)
    common = {
        "source_kind": SOURCE_KIND,
        "source_key": key,
        "source_link": {"mailbox_id": mailbox_id, "thread_id": thread_id},
        "model": model,
        "generated_at": generated_at,
    }
    deals = sorted(deals, key=lambda d: d["id"])
    company_owners = {c["id"]: c.get("owner_id") for c in companies}
    out: list[dict[str, Any]] = []

    # FR-CRMAI-SUM-06: one item, every linked open deal a choice, when any
    # of them has another next step or date.
    suggested = {"next_step": next_step, "next_step_on": _day(next_step_on)}
    if next_step and any(
        (d.get("next_step") or None, _day(d.get("next_step_on"))) != tuple(suggested.values())
        for d in deals
    ):
        first = deals[0]
        out.append(
            {
                **common,
                "type": "next_step_change",
                "owner_id": review_rules.owner_for(
                    "next_step_change", roles=roles, active=active, deal_owner=first.get("owner_id")
                ),
                "record_type": "opportunity",
                "record_id": first["id"],
                "suggestion": suggested,
                "current_values": {
                    d["id"]: {
                        "next_step": d.get("next_step"),
                        "next_step_on": _day(d.get("next_step_on")),
                    }
                    for d in deals
                },
                "source_version": watermark_id,
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND, key, "next_step_change", "opportunity", first["id"]
                ),
                "deal_choices": [d["id"] for d in deals],
            }
        )

    # FR-CRMAI-SUM-07: each dated commitment, on the one open deal, else the
    # company, else the person the thread is about.
    if len(deals) == 1:
        target = ("opportunity", deals[0]["id"])
        deal_owner = deals[0].get("owner_id")
        company_owner = company_owners.get(deals[0].get("company_id") or "")
    elif companies:
        first_company = sorted(companies, key=lambda c: c["id"])[0]
        target, deal_owner = ("company", first_company["id"]), None
        company_owner = first_company.get("owner_id")
    elif people:
        target = ("person", sorted(people, key=lambda p: p["id"])[0]["id"])
        deal_owner, company_owner = None, None
    else:
        return out
    for c in stored_commitments:
        if not c.get("due_on"):
            continue
        fingerprint = review_rules.commitment_fingerprint(c["text"], c["due_on"])
        out.append(
            {
                **common,
                "type": "task_from_commitment",
                "owner_id": review_rules.owner_for(
                    "task_from_commitment",
                    roles=roles,
                    active=active,
                    commitment_owner=c.get("owner_id"),
                    mailbox_owner=mailbox_owner,
                    deal_owner=deal_owner,
                    company_owner=company_owner,
                ),
                "record_type": target[0],
                "record_id": target[1],
                "suggestion": {
                    "title": c["text"],
                    "due_on": c["due_on"],
                    "owner_id": c.get("owner_id"),
                    "owner_unknown": c.get("owner_id") is None,
                },
                "current_values": {},
                "source_version": fingerprint,
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND,
                    key,
                    "task_from_commitment",
                    target[0],
                    target[1],
                    fingerprint=fingerprint,
                ),
                "deal_choices": [],
            }
        )
    return out
