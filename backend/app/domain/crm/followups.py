"""The follow-up rules' detectors — spec 009 FR-CRMAI-RULE-01, -02, -05 to
-07, FR-CRMAI-FLAG-04; Q-15, Q-16, Q-39, Q-40; data-model §3.2.1, §3.2.2,
§3.11; plan §5.9.

Pure. Each rule watches for its trigger and makes, at most, a Review Queue
item (`rule_suggestion`): it never moves a stage, creates a deal, changes a
record or sends an email (FR-CRMAI-RULE-07). R-2 Stuck in stage is a flag
(`domain/crm/flags.stuck_in_stage`), not an item.

- **R-1 No reply after outreach** (`no_reply`): an outbound email, the
  latest counted one of its thread, older than `no_reply_days` with no later
  inbound from anyone it is linked to (the facts come from
  `crm_no_reply_candidates`, 066). One item per outbound message, whichever
  person or company matched it: the target is its first person by id, else
  its first company (FR-CRMAI-RULE-02). The item carries a draft the owner
  sends from their own mail, written later by the `followup_drafts` job
  from the thread's stored summary (`draft_parts`), never from a body.
- **R-3 Lead gone cold** (`lead_cold`): a company at Contacted or Nurturing
  with no activity for `lead_cold_days` (activity as `crm_lead_activity`
  defines it, Q-15). From Contacted: move to Nurturing or Disqualified; from
  Nurturing: to Disqualified, or set a next touch (Q-39). A new activity is
  a new source version, so a decided item is made again only once the
  company has had activity and gone cold again (FR-CRMAI-REV-08); after a
  dismissal (or an expiry, which counts as one), not within
  `lead_cold_days` of it in any case (Q-40).
- **R-4 Campaign follow-through** (`campaign_followthrough`): a member
  Engaged or Meeting booked for more than 7 days since its status last
  changed (`status_changed_at`, Q-16), with no converted deal and no open or
  waiting task (008) on its company or person: qualify (the conversion) or
  close out (Not interested).

Nobody marked Do not contact is suggested for (007 FR-CRM-CAMP-03): not a
person so marked, and not a company whose only matching people are so
marked (R-1, R-4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from app.domain.crm import review as review_rules

#: What the R-1 draft job records in the usage log and the job log (D-4).
JOB: Final = "followup_drafts"
#: Every rule runs behind phase 3; R-1 also needs phase 1 and Gmail sync
#: live, because it drafts from stored summaries (FR-CRMAI-FLAG-04).
PHASE: Final = "3"
DRAFT_PHASE: Final = "1"
SOURCE_KIND: Final = "rule"
ITEM_TYPE: Final = "rule_suggestion"

#: FR-CRMAI-RULE-01 — each rule's switch in the `automations` setting.
SWITCHES: Final = {
    "R-1": "no_reply_draft",
    "R-2": "stuck_notice",
    "R-3": "lead_cold",
    "R-4": "campaign_followthrough",
}

#: R-1: approving marks the draft used (contracts §2.2).
NO_REPLY_OPTIONS: Final = ("mark_used",)
#: R-3 (Q-39): the options by lead stage.
LEAD_COLD_OPTIONS: Final = {
    "contacted": ("nurturing", "disqualified"),
    "nurturing": ("disqualified", "next_touch"),
}
#: R-4 (Q-16).
FOLLOWTHROUGH_STATUSES: Final = ("engaged", "meeting_booked")
FOLLOWTHROUGH_DAYS: Final = 7
FOLLOWTHROUGH_OPTIONS: Final = ("qualify", "close_out")

#: data-model §3.2.1: an R-1 draft's length.
DRAFT_MAX: Final = 2000

#: The R-1 draft job's instruction, after the fixed rules every prompt
#: carries (FR-CRMAI-LLM-04).
DRAFT_INSTRUCTION: Final = (
    "Write a short, polite follow-up email from a Nunnari team member to a client "
    "who has not replied to Nunnari's last email. You are given a summary of the email "
    "thread and who the email is to. Write only the body of the email, in at most 120 "
    "words of plain text, as a gentle nudge that refers to what the thread was about. "
    "Do not invent facts, dates, prices or promises that the summary does not give, "
    "and do not include links or a subject line."
)


def _at(value: Any) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def source_key(rule: str, *parts: str) -> str:
    """Data-model §3.2: `rule:<R-n>:<record or member id>`; R-1's is
    `rule:R-1:<mailbox id>:<thread id>:<outbound message id>` (§3.2.2)."""
    return ":".join(("rule", rule, *parts))


def _common(key: str) -> dict[str, Any]:
    return {
        "type": ITEM_TYPE,
        "source_kind": SOURCE_KIND,
        "source_key": key,
        "member_id": None,
        "model": None,
        "generated_at": None,
        "deal_choices": [],
    }


def _owner(owner: str | None, roles: Mapping[str, str], active: set[str]) -> str | None:
    """FR-CRMAI-REV-02 — a company-level rule's item goes to the record's
    owner, else to the workspace's Admins."""
    return review_rules.owner_for(ITEM_TYPE, roles=roles, active=active, company_owner=owner)


# ---------------------------------------------------------------------------
# R-1 No reply after outreach — FR-CRMAI-RULE-02
# ---------------------------------------------------------------------------


def no_reply(
    candidates: Iterable[Mapping[str, Any]],
    people: Mapping[str, Mapping[str, Any]],
    companies: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The R-1 triggers among `crm_no_reply_candidates`' rows, given the live
    people and companies they name (by id): `[{mailbox_id, thread_id,
    gmail_message_id, sent_at, record_type, record_id, owner_id,
    source_key}]`. A message whose matching people are all marked Do not
    contact gives none, even if a company matched too."""
    out = []
    for c in candidates:
        matched = [people[p] for p in sorted(c.get("people") or []) if p in people]
        if matched and all(p.get("do_not_contact") for p in matched):
            continue
        reachable = [p for p in matched if not p.get("do_not_contact")]
        firms = [companies[i] for i in sorted(c.get("companies") or []) if i in companies]
        if reachable:
            record_type, record = "person", reachable[0]
        elif firms:
            record_type, record = "company", firms[0]
        else:
            continue
        out.append(
            {
                "mailbox_id": c["mailbox_id"],
                "thread_id": c["thread_id"],
                "gmail_message_id": c["gmail_message_id"],
                "sent_at": c.get("sent_at"),
                "record_type": record_type,
                "record_id": record["id"],
                "owner_id": record.get("owner_id"),
                "source_key": source_key(
                    "R-1", c["mailbox_id"], c["thread_id"], c["gmail_message_id"]
                ),
            }
        )
    return out


def draft_parts(
    summary: Mapping[str, Any], to_name: str, company_name: str | None
) -> list[tuple[str, str]]:
    """What the draft is written from: the thread's stored summary and the
    record's name and company, each quoted as data (FR-CRMAI-LLM-04). Never
    a message body, an amount or an address."""
    lines = [str(summary.get("summary") or "")]
    asks = [str(a) for a in summary.get("asks") or [] if a]
    if asks:
        lines.append("They asked: " + "; ".join(asks))
    if summary.get("next_step"):
        lines.append(f"Next step: {summary['next_step']}")
    to = to_name if not company_name or company_name == to_name else f"{to_name} at {company_name}"
    return [("thread summary", "\n".join(lines)), ("recipient", to)]


def no_reply_item(
    trigger: Mapping[str, Any],
    draft: str,
    *,
    model: str,
    generated_at: str,
    roles: Mapping[str, str],
    active: set[str],
) -> dict[str, Any]:
    """The R-1 item, with its draft (≤ 2,000, masked again by
    `review.upsert`); nothing it would replace, so no staleness."""
    key = trigger["source_key"]
    return {
        **_common(key),
        "owner_id": _owner(trigger.get("owner_id"), roles, active),
        "record_type": trigger["record_type"],
        "record_id": trigger["record_id"],
        "suggestion": {
            "rule": "R-1",
            "options": list(NO_REPLY_OPTIONS),
            "draft": draft[:DRAFT_MAX],
        },
        "current_values": {},
        "source_version": trigger["gmail_message_id"],
        "source_link": {"mailbox_id": trigger["mailbox_id"], "thread_id": trigger["thread_id"]},
        "dedupe_key": review_rules.dedupe_key(
            SOURCE_KIND, key, ITEM_TYPE, trigger["record_type"], trigger["record_id"]
        ),
        "model": model,
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# R-3 Lead gone cold — FR-CRMAI-RULE-05
# ---------------------------------------------------------------------------


def lead_cold(
    companies: Iterable[Mapping[str, Any]],
    activity: Mapping[str, Any],
    decided: Iterable[Mapping[str, Any]],
    now: datetime,
    days: int,
    *,
    roles: Mapping[str, str],
    active: set[str],
) -> list[dict[str, Any]]:
    """The R-3 items. `activity` is `crm_lead_activity`'s answer (company id
    → latest activity, or none: the company's creation counts then);
    `decided` the workspace's decided R-3 items (`record_id, status,
    decided_at`), for Q-40."""
    quiet = timedelta(days=days)
    held_off = {
        d["record_id"]
        for d in decided
        if d.get("status") in ("dismissed", "expired")
        and (at := _at(d.get("decided_at"))) is not None
        and now - at < quiet
    }
    out = []
    for c in sorted(companies, key=lambda c: c["id"]):
        stage = c.get("lead_stage")
        if stage not in LEAD_COLD_OPTIONS or c.get("deleted_at") or c["id"] in held_off:
            continue
        last = _at(activity.get(c["id"])) or _at(c.get("created_at"))
        if last is None or now - last < quiet:
            continue
        key = source_key("R-3", c["id"])
        out.append(
            {
                **_common(key),
                "owner_id": _owner(c.get("owner_id"), roles, active),
                "record_type": "company",
                "record_id": c["id"],
                "suggestion": {"rule": "R-3", "options": list(LEAD_COLD_OPTIONS[stage])},
                "current_values": {"lead_stage": stage},
                # FR-CRMAI-REV-08: new activity is a new trigger.
                "source_version": last.isoformat(),
                "source_link": {"record_type": "company", "record_id": c["id"]},
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND, key, ITEM_TYPE, "company", c["id"]
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# R-4 Campaign follow-through — FR-CRMAI-RULE-06
# ---------------------------------------------------------------------------


def _blocked(people: Sequence[Mapping[str, Any]]) -> bool:
    """A company whose only people are marked Do not contact."""
    return bool(people) and all(p.get("do_not_contact") for p in people)


def campaign_followthrough(
    members: Iterable[Mapping[str, Any]],
    *,
    people: Mapping[str, Mapping[str, Any]],
    company_people: Mapping[str, Sequence[Mapping[str, Any]]],
    companies: Mapping[str, Mapping[str, Any]],
    with_open_task: set[str],
    now: datetime,
    roles: Mapping[str, str],
    active: set[str],
) -> list[dict[str, Any]]:
    """The R-4 items. `people` and `companies` are the members' live records
    by id, `company_people` each company's live people, `with_open_task` the
    companies and people with an open or waiting task linked."""
    out = []
    for m in sorted(members, key=lambda m: m["id"]):
        if m.get("status") not in FOLLOWTHROUGH_STATUSES or m.get("deleted_at"):
            continue
        if m.get("converted_opportunity_id"):
            continue
        since = _at(m.get("status_changed_at"))
        if since is None or now - since <= timedelta(days=FOLLOWTHROUGH_DAYS):
            continue
        person = people.get(m.get("person_id") or "")
        company = companies.get(m.get("company_id") or "")
        if m.get("person_id") and (person is None or person.get("do_not_contact")):
            continue
        if m.get("company_id") and company is None:
            continue
        if not m.get("person_id") and _blocked(company_people.get(m.get("company_id") or "", [])):
            continue
        if {m.get("company_id"), m.get("person_id")} & with_open_task:
            continue
        target = ("company", company) if company is not None else ("person", person)
        record_type, record = target
        assert record is not None
        key = source_key("R-4", m["id"])
        out.append(
            {
                **_common(key),
                "owner_id": _owner(record.get("owner_id"), roles, active),
                "record_type": record_type,
                "record_id": record["id"],
                "member_id": m["id"],
                "suggestion": {"rule": "R-4", "options": list(FOLLOWTHROUGH_OPTIONS)},
                "current_values": {"member_status": m["status"]},
                # A new status change is a new trigger (FR-CRMAI-REV-08).
                "source_version": since.isoformat(),
                "source_link": {"record_type": record_type, "record_id": record["id"]},
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND, key, ITEM_TYPE, record_type, record["id"], member_id=m["id"]
                ),
            }
        )
    return out
