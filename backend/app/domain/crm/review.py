"""The Review Queue's rules — spec 009 FR-CRMAI-REV-01 to -08, -10,
FR-CRMAI-FLAG-04; Q-07, Q-34; data-model §3.2; plan §5.4.

Pure. What an item is (its types and their labels), who it belongs to
(`owner_for`, decided once when it is created), what makes two items the
same (`dedupe_key`, `commitment_fingerprint`), whether a recorded value has
changed since (`stale_fields`, per field and never per record), when it
expires, and who may see and decide it.

An item belongs to one person, or to nobody, which means the workspace's
Admins (`owner_id` NULL, FR-CRMAI-REV-02). Members see and decide their own
items; a workspace Admin (the CRM role, not the TVA one) sees and decides
every item of the workspace; Viewers have no queue (FR-CRMAI-REV-03).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

#: Data-model §3.2 `type`, with what the queue calls each.
TYPES: Final = {
    "next_step_change": "Change next step",
    "task_from_commitment": "Task from an email commitment",
    "task_from_meeting": "Task from a meeting action",
    "deal_link": "Link meeting to a deal",
    "enrichment_conflict": "Use the enriched value",
    "rule_suggestion": "Follow-up suggestion",
}
#: The items that create a task, and the task source each gives it
#: (FR-CRMAI-REV-06, FR-CRMAI-CHG-10).
TASK_SOURCES: Final = {
    "task_from_commitment": "email_commitment",
    "task_from_meeting": "meeting_action",
}
#: Data-model §3.2 `source_kind`, with what an item's source line says.
SOURCE_KINDS: Final = {
    "email_thread": "Email thread",
    "meeting": "Meeting",
    "enrichment": "Company enrichment",
    "rule": "Follow-up rule",
}
#: The fields an `enrichment_conflict` may replace (data-model §3.2.1).
ENRICHED_FIELDS: Final = ("industry_id", "size_id", "city", "country", "description", "linkedin")
#: The fields a `next_step_change` replaces, and checks for staleness.
NEXT_STEP_FIELDS: Final = ("next_step", "next_step_on")

#: FR-CRMAI-REV-07.
EXPIRY_DAYS: Final = 14
TOMBSTONE_DAYS: Final = 90
#: An item's expiry date is read on the CRM's default day (Asia/Kolkata).
ZONE: Final = ZoneInfo("Asia/Kolkata")

#: The refusals an approval meets before the manual path's own (contracts
#: §2.2).
NO_SUCH: Final = "No such suggestion."
ALREADY_DECIDED: Final = "This suggestion was already decided."
CHANGED: Final = "This record changed after the suggestion was made."
TRASHED: Final = "This record is in the Trash."
CHOOSE_DEAL: Final = "Choose the deal this applies to."
ADMINS_ONLY: Final = "Only a workspace admin can see the whole queue."
NOT_YET: Final = "This kind of suggestion cannot be approved yet."
#: An approval's change failed for a reason that is not a refusal (a
#: timeout, a database fault): the item opens again showing this.
NOT_MADE: Final = "The change could not be made. Nothing was changed; try again."


# ---------------------------------------------------------------------------
# Routing — FR-CRMAI-REV-02
# ---------------------------------------------------------------------------


def _eligible(person: str | None, roles: Mapping[str, str], active: set[str]) -> bool:
    """An Admin or Member of the workspace, with an active account."""
    return bool(person) and roles.get(person or "") in ("admin", "member") and person in active


def owner_for(
    item_type: str,
    *,
    roles: Mapping[str, str],
    active: set[str],
    deal_owner: str | None = None,
    company_owner: str | None = None,
    commitment_owner: str | None = None,
    mailbox_owner: str | None = None,
    rule_level: str | None = None,
) -> str | None:
    """The one person an item belongs to, or None for the workspace's Admins.

    - `next_step_change`: the deal owner.
    - `task_from_*`: the commitment or action-item owner when they are an
      Admin or Member here; else the mailbox owner (an email commitment) or
      the deal or company owner (a meeting action).
    - `deal_link`, `enrichment_conflict`: the company owner.
    - `rule_suggestion`: the deal owner for a deal-level rule
      (`rule_level="deal"`), else the company owner.

    `roles`: member id → CRM role in this workspace; `active`: the ids of
    active accounts. Whoever it lands on must be an Admin or Member with an
    active account: a Viewer, an inactive account, a non-member or nobody
    ("unknown") sends it to the Admins.
    """
    if item_type in TASK_SOURCES:
        if _eligible(commitment_owner, roles, active):
            return commitment_owner
        fallback = next(
            (p for p in (mailbox_owner, deal_owner, company_owner) if p is not None), None
        )
    elif item_type == "next_step_change":
        fallback = deal_owner
    elif item_type == "rule_suggestion":
        fallback = deal_owner if rule_level == "deal" else company_owner
    else:
        fallback = company_owner
    return fallback if _eligible(fallback, roles, active) else None


# ---------------------------------------------------------------------------
# Dedupe — FR-CRMAI-REV-08, FR-CRMAI-SUM-07; data-model §3.2.2
# ---------------------------------------------------------------------------


def commitment_fingerprint(title: str, due_on: str | date | None) -> str:
    """16 hex characters of sha256 over the lower-cased, whitespace-normalised
    title and the due date, so a re-summary does not suggest the same
    commitment twice (FR-CRMAI-SUM-07)."""
    normalised = " ".join((title or "").split()).lower()
    day = due_on.isoformat() if isinstance(due_on, date) else (due_on or "")
    return hashlib.sha256(f"{normalised}{day}".encode()).hexdigest()[:16]


def dedupe_key(
    source_kind: str,
    source_key: str,
    item_type: str,
    record_type: str,
    record_id: str,
    *,
    member_id: str | None = None,
    fingerprint: str | None = None,
) -> str:
    """`<source_kind>|<source_key>|<type>|<record_type>:<record_id>`, with
    `:<member id>` for R-4 and `#<fingerprint>` for a task item."""
    target = f"{record_type}:{record_id}"
    if member_id:
        target += f":{member_id}"
    if fingerprint:
        target += f"#{fingerprint}"
    return f"{source_kind}|{source_key}|{item_type}|{target}"


# ---------------------------------------------------------------------------
# Staleness — FR-CRMAI-REV-04
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """A value as compared: nothing for an empty string, a date by its day."""
    if value in ("", None):
        return None
    if isinstance(value, date):
        return value.isoformat()
    return value


def stale_fields(recorded: Mapping[str, Any], row: Mapping[str, Any]) -> list[str]:
    """The fields whose value at creation (`recorded`) differs from the
    record's now, in the order recorded. Only those fields count: a change to
    any other (Last inbound written by email sync, say) does not."""
    return [f for f, value in recorded.items() if _plain(value) != _plain(row.get(f))]


# ---------------------------------------------------------------------------
# Expiry — FR-CRMAI-REV-07
# ---------------------------------------------------------------------------


def expires_on(created_at: str | datetime) -> date:
    """The day an untouched item expires: 14 days after it was created, on
    the Asia/Kolkata calendar."""
    at = created_at if isinstance(created_at, datetime) else datetime.fromisoformat(created_at)
    return (at + timedelta(days=EXPIRY_DAYS)).astimezone(ZONE).date()


# ---------------------------------------------------------------------------
# Who sees and decides — FR-CRMAI-REV-03
# ---------------------------------------------------------------------------


def may_see(role: str, person_id: str, item: Mapping[str, Any]) -> bool:
    """A workspace Admin every item; a Member their own; a Viewer none."""
    if role == "admin":
        return True
    return role == "member" and item.get("owner_id") == person_id


def may_decide(role: str, person_id: str, item: Mapping[str, Any]) -> bool:
    """Deciding is open to whoever may see the item (FR-CRMAI-REV-03); the
    change itself then needs the same permission as making it by hand."""
    return may_see(role, person_id, item)


def is_mine(role: str, person_id: str, item: Mapping[str, Any]) -> bool:
    """The person's own queue ("Mine", To review): a Member's items; for an
    Admin also the items that belong to the Admins (no owner)."""
    owner = item.get("owner_id")
    if role == "admin":
        return owner in (person_id, None)
    return role == "member" and owner == person_id
