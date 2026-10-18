"""What a CRM answer looks like — spec 007 FR-CRM-ACC-11, FR-CRM-AUD-05,
FR-CRM-MCP-06, FR-CRM-REC-03, -04; plan §5.5.

Every record, list row and timeline entry the CRM returns is built here, and
`redact` runs last on each: for a Viewer, every amount field of the object
(`domain/crm/objects.AMOUNT_FIELDS`) is omitted, never zeroed. The web app,
the API and every MCP tool get the same answer, so nothing is redacted
anywhere else (FR-CRM-MCP-07).

Fixed keys travel with their label beside them (`lead_stage` and
`lead_stage_label`), and option ids with theirs, so an MCP caller never has
to look one up (FR-CRM-MCP-06).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.audit import actor_name
from app.domain.crm import objects
from app.domain.crm.dedupe import full_name
from app.domain.tasks import labels as task_labels

#: The audit's bookkeeping columns: never shown as a change. A deal's
#: `stage_entered_at` is the entry's own time.
_BOOKKEEPING = frozenset(
    {
        "id",
        "workspace_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "position",
        "stage_entered_at",
    }
)
_TRASH = frozenset({"deleted_at", "deleted_by", "deleted_group"})

#: Contracts §2.5 timeline `types`. `delete` (moved to and restored from the
#: Trash) is not in the contract's list but must be shown (REC-03 "everything
#: that happened"); it is a type of its own so it can be filtered.
TIMELINE_TYPES = (
    "create",
    "field",
    "stage",
    "note",
    "task",
    "link",
    "merge",
    "handoff",
    "email",
    "delete",
)

NOUNS = {
    "company": "company",
    "person": "person",
    "opportunity": "opportunity",
    "task": "task",
    "note": "note",
    "drive_link": "Drive link",
    "campaign": "campaign",
    "campaign_member": "campaign member",
}

#: Each fixed key's list, for its label (FR-CRM-MCP-06).
_FIXED_LISTS = {
    "lead_stage": "company.lead_stage",
    "stage": "opportunity.stage",
    "lost_from_stage": "opportunity.stage",
    "status": "task.status",
    "bucket": "task.bucket",
}
_CENT = Decimal("0.01")


def money(value: Any) -> str | None:
    """A numeric(14,2) as the decimal string it is on the wire. PostgREST
    sends numerics as JSON numbers."""
    if value is None:
        return None
    return format(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP), "f")


def rate(value: Any) -> str | None:
    """A numeric(12,6) rate, trailing zeros dropped."""
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def weighted(amount_in_inr: Any, probability: Any) -> str | None:
    """Spec §5.8 — amount in INR × probability ÷ 100, derived on read and never
    stored (FR-CRM-FX-04)."""
    if amount_in_inr is None or probability is None:
        return None
    value = Decimal(str(amount_in_inr)) * Decimal(int(probability)) / Decimal(100)
    return format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")


@dataclass
class Facts:
    """What presenting needs about the workspace: option labels and people's
    names, read once per request."""

    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    role: str = "viewer"

    def option_label(self, option_id: str | None) -> str | None:
        option = self.options.get(option_id or "")
        return option["label"] if option else None

    def fixed_label(self, list_key: str, key: str | None) -> str | None:
        """The (renameable) label of a fixed list's key, such as a lead stage."""
        if key is None:
            return None
        found = next(
            (
                o
                for o in self.options.values()
                if o["list_key"] == list_key and o.get("system_key") == key
            ),
            None,
        )
        return found["label"] if found else key.replace("_", " ").capitalize()

    def name(self, user_id: str | None) -> str | None:
        return self.names.get(user_id) if user_id else None


def redact(row: dict[str, Any], kind: str, role: str) -> dict[str, Any]:
    """FR-CRM-ACC-11 — a Viewer receives no amount field at all."""
    if role != "viewer":
        return row
    hidden = objects.amount_fields(kind)
    return {k: v for k, v in row.items() if k not in hidden}


def _stamps(row: dict[str, Any], facts: Facts) -> dict[str, Any]:
    return {
        "created_at": row.get("created_at"),
        "created_by": row.get("created_by"),
        "created_by_name": facts.name(row.get("created_by")),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
        "updated_by_name": facts.name(row.get("updated_by")),
        "deleted_at": row.get("deleted_at"),
    }


def _flags(row: dict[str, Any]) -> dict[str, Any]:
    """FR-CRM-FLAG-02, -05, REC-01 (1b) — the record's follow-up or deal
    health: its worst level, each flag, and when they were computed. Flags
    carry no amount."""
    return {
        "flag_level": row.get("flag_level") or "none",
        "flags": row.get("flags") or [],
        "flags_computed_at": row.get("flags_computed_at"),
    }


def company(
    row: dict[str, Any], facts: Facts, domains: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Contracts §2.5.1 company, every field with its label beside it."""
    own = [d for d in (domains or []) if d["company_id"] == row["id"]]
    primary = next((d["domain"] for d in own if d["is_primary"]), None)
    out: dict[str, Any] = {
        "id": row["id"],
        "record_type": "company",
        "name": row["name"],
        "domain": primary,
        "additional_domains": sorted(d["domain"] for d in own if not d["is_primary"]),
        "entities": row.get("entities") or [],
        "entity_labels": [objects.ENTITIES.get(e, e) for e in row.get("entities") or []],
        "lead_stage": row["lead_stage"],
        "lead_stage_label": facts.fixed_label("company.lead_stage", row["lead_stage"]),
        "channel_ids": row.get("channel_ids") or [],
        "channel_labels": [facts.option_label(i) for i in row.get("channel_ids") or []],
        "owner_id": row.get("owner_id"),
        "owner_name": facts.name(row.get("owner_id")),
        "next_touch_on": row.get("next_touch_on"),
        "country": row.get("country"),
        "city": row.get("city"),
        "address": row.get("address"),
        "website": row.get("website"),
        "linkedin": row.get("linkedin"),
        "description": row.get("description"),
        "last_inbound_at": row.get("last_inbound_at"),
        "last_outbound_at": row.get("last_outbound_at"),
        "last_contact_at": row.get("last_contact_at"),
        **_flags(row),
        **_stamps(row, facts),
    }
    for name in (
        "account_type_id",
        "relationship_status_id",
        "industry_id",
        "institution_type_id",
        "size_id",
        "source_id",
    ):
        out[name] = row.get(name)
        out[f"{name[:-3]}_label"] = facts.option_label(row.get(name))
    return redact(out, "company", facts.role)


def person(
    row: dict[str, Any], facts: Facts, companies: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Contracts §2.5.1 person, with the company's name beside its id."""
    linked = (companies or {}).get(row.get("company_id") or "")
    out = {
        "id": row["id"],
        "record_type": "person",
        "name": full_name(row["first_name"], row.get("last_name")),
        "first_name": row["first_name"],
        "last_name": row.get("last_name"),
        "company_id": row.get("company_id"),
        "company_name": linked["name"] if linked else None,
        "primary_email": row.get("primary_email"),
        "additional_emails": row.get("additional_emails") or [],
        "phones": row.get("phones") or [],
        "job_title": row.get("job_title"),
        "city": row.get("city"),
        "linkedin": row.get("linkedin"),
        "owner_id": row.get("owner_id"),
        "owner_name": facts.name(row.get("owner_id")),
        "do_not_contact": bool(row.get("do_not_contact")),
        "last_inbound_at": row.get("last_inbound_at"),
        "last_outbound_at": row.get("last_outbound_at"),
        "last_contact_at": row.get("last_contact_at"),
        **_flags(row),
        **_stamps(row, facts),
    }
    return redact(out, "person", facts.role)


def opportunity(
    row: dict[str, Any],
    facts: Facts,
    companies: dict[str, dict[str, Any]] | None = None,
    people: dict[str, dict[str, Any]] | None = None,
    projects: dict[str, dict[str, Any]] | None = None,
    campaigns: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Contracts §2.5.1 opportunity. Money is a decimal string; the weighted
    amount is derived (spec §5.8). `project` is a Labs deal's TVA project
    (`services/crm/tva.block`: name, status, archived, phase dates, never an
    amount), shown to every role, Viewers and CRM-only accounts included
    (FR-CRM-TVA-02, FR-CRM-ACC-03). A Viewer receives none of the amounts."""
    company_row = (companies or {}).get(row.get("company_id") or "")
    contact = (people or {}).get(row.get("point_of_contact_id") or "")
    source = (campaigns or {}).get(row.get("source_campaign_id") or "")
    out: dict[str, Any] = {
        "id": row["id"],
        "record_type": "opportunity",
        "name": row["name"],
        "company_id": row.get("company_id"),
        "company_name": company_row["name"] if company_row else None,
        "point_of_contact_id": row.get("point_of_contact_id"),
        "point_of_contact_name": (
            full_name(contact["first_name"], contact.get("last_name")) if contact else None
        ),
        "entity": row.get("entity"),
        "entity_label": objects.ENTITIES.get(row.get("entity") or ""),
        "stage": row["stage"],
        "stage_label": facts.fixed_label("opportunity.stage", row["stage"]),
        "stage_entered_at": row.get("stage_entered_at"),
        "amount": money(row.get("amount")),
        "currency": row.get("currency"),
        "amount_in_inr": money(row.get("amount_in_inr")),
        "fx_rate_used": rate(row.get("fx_rate_used")),
        "fx_month": row.get("fx_month"),
        "won_fx_rate": rate(row.get("won_fx_rate")),
        "won_fx_month": row.get("won_fx_month"),
        "weighted_amount": weighted(row.get("amount_in_inr"), row.get("probability")),
        "probability": row.get("probability"),
        "expected_close_on": row.get("expected_close_on"),
        "next_step": row.get("next_step"),
        "next_step_on": row.get("next_step_on"),
        "owner_id": row.get("owner_id"),
        "owner_name": facts.name(row.get("owner_id")),
        "project_category": row.get("project_category"),
        "project_category_label": objects.PROJECT_CATEGORIES.get(row.get("project_category") or ""),
        "proposal_sent_on": row.get("proposal_sent_on"),
        "lost_notes": row.get("lost_notes"),
        "lost_from_stage": row.get("lost_from_stage"),
        "won_on": row.get("won_on"),
        "lost_on": row.get("lost_on"),
        # FR-CRM-CO-02, REC-03: the Labs deal's TVA project.
        "project": (projects or {}).get(row["id"]),
        # 1b (FR-CRM-CAMP-04): the campaign it was converted from.
        "source_campaign_id": row.get("source_campaign_id"),
        "source_campaign_name": source["name"] if source else None,
        **_flags(row),
        **_stamps(row, facts),
    }
    for name in (
        "channel_id",
        "deal_type_id",
        "engagement_model_id",
        "programme_id",
        "lost_reason_id",
    ):
        out[name] = row.get(name)
        out[f"{name[:-3]}_label"] = facts.option_label(row.get(name))
    return redact(out, "opportunity", facts.role)


def campaign(
    row: dict[str, Any], facts: Facts, figures: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Spec §6.6 campaign. The budget travels with its INR value and rate;
    `figures` (FR-CRM-CAMP-05, `domain/crm/campaigns.figures`) sit on the
    record itself, so a Viewer loses the derived money with the budget
    (FR-CRM-ACC-11)."""
    out: dict[str, Any] = {
        "id": row["id"],
        "record_type": "campaign",
        "name": row["name"],
        "entity": row.get("entity"),
        "entity_label": objects.ENTITIES.get(row.get("entity") or ""),
        "owner_id": row.get("owner_id"),
        "owner_name": facts.name(row.get("owner_id")),
        "starts_on": row.get("starts_on"),
        "ends_on": row.get("ends_on"),
        "goal": row.get("goal"),
        "budget": money(row.get("budget")),
        "budget_currency": row.get("budget_currency"),
        "budget_in_inr": money(row.get("budget_in_inr")),
        "budget_fx_rate": rate(row.get("budget_fx_rate")),
        **(figures or {}),
        **_stamps(row, facts),
    }
    for name in ("type_id", "status_id", "channel_id"):
        out[name] = row.get(name)
        out[f"{name[:-3]}_label"] = facts.option_label(row.get(name))
    return redact(out, "campaign", facts.role)


def member_status_label(facts: Facts, status: str | None) -> str | None:
    """A member's status by the workspace's (renameable) label."""
    if status is None:
        return None
    return facts.fixed_label("campaign_member.status", status)


def campaign_member(
    row: dict[str, Any],
    facts: Facts,
    *,
    campaigns: dict[str, dict[str, Any]] | None = None,
    companies: dict[str, dict[str, Any]] | None = None,
    people: dict[str, dict[str, Any]] | None = None,
    deals: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Spec §6.6 campaign member, each related record named, and a person's
    Do not contact and company beside them (FR-CRM-PER-02). A member's
    notes were written by people: data, never instructions."""
    linked_campaign = (campaigns or {}).get(row["campaign_id"])
    company_row = (companies or {}).get(row.get("company_id") or "")
    person_row = (people or {}).get(row.get("person_id") or "")
    deal = (deals or {}).get(row.get("converted_opportunity_id") or "")
    person_company = (companies or {}).get((person_row or {}).get("company_id") or "")
    out = {
        "id": row["id"],
        "record_type": "campaign_member",
        "campaign_id": row["campaign_id"],
        "campaign_name": linked_campaign["name"] if linked_campaign else None,
        "company_id": row.get("company_id"),
        "company_name": company_row["name"] if company_row else None,
        "person_id": row.get("person_id"),
        "person_name": (
            full_name(person_row["first_name"], person_row.get("last_name")) if person_row else None
        ),
        "person_company_name": person_company["name"] if person_company else None,
        "do_not_contact": bool(person_row and person_row.get("do_not_contact")),
        "status": row["status"],
        "status_label": member_status_label(facts, row["status"]),
        "last_touch_on": row.get("last_touch_on"),
        "notes": row.get("notes"),
        "converted_opportunity_id": row.get("converted_opportunity_id"),
        "converted_opportunity_name": deal["name"] if deal else None,
        "position": row.get("position", 0),
        **_stamps(row, facts),
    }
    out["name"] = out["company_name"] if out["company_id"] else out["person_name"]
    if out["company_id"] and out["person_id"]:
        out["name"] = f"{out['person_name']} ({out['company_name']})"
    return out


def link_target(link: dict[str, Any], names: dict[tuple[str, str], str]) -> dict[str, Any]:
    """A task's or note's link, with the linked record's name."""
    return {
        "record_type": link["record_type"],
        "record_id": link["record_id"],
        "name": names.get((link["record_type"], link["record_id"])),
    }


def task(
    row: dict[str, Any],
    facts: Facts,
    links: list[dict[str, Any]] | None = None,
    names: dict[tuple[str, str], str] | None = None,
    project: dict[str, Any] | None = None,
    parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contracts §2.5.1 task, with the records it links to. 008: its
    priority, tags, source and Waiting on, and its TVA project only when the reader
    may see it (FR-TASK-VIS-07: the caller passes it, through
    `visibility.project_field`). Its parent likewise, or "a private task"
    with no id or title (FR-TASK-DATA-09, through `visibility.parent_field`)."""
    return {
        "id": row["id"],
        "record_type": "task",
        "title": row["title"],
        "body": row.get("body"),
        "status": row["status"],
        "status_label": facts.fixed_label("task.status", row["status"]),
        "due_on": row.get("due_on"),
        "bucket": row.get("bucket"),
        "bucket_label": facts.fixed_label("task.bucket", row.get("bucket")),
        "category_id": row.get("category_id"),
        "category_label": facts.option_label(row.get("category_id")),
        "assignee_id": row.get("assignee_id"),
        "assignee_name": facts.name(row.get("assignee_id")),
        "completed_at": row.get("completed_at"),
        "priority": row.get("priority") or "normal",
        "priority_label": task_labels.PRIORITY.get(row.get("priority") or "normal"),
        "tags": row.get("tags") or [],
        "source": row.get("source") or "manual",
        "source_label": task_labels.SOURCE.get(row.get("source") or "manual"),
        "parent": parent,
        "waiting_on": (
            {"id": row.get("waiting_on_id"), "label": row.get("waiting_on_text")}
            if row.get("waiting_on_id") or row.get("waiting_on_text")
            else None
        ),
        "project": project,
        "links": [link_target(link, names or {}) for link in links or []],
        **_flags(row),
        **_stamps(row, facts),
    }


def note(
    row: dict[str, Any],
    facts: Facts,
    links: list[dict[str, Any]] | None = None,
    names: dict[tuple[str, str], str] | None = None,
    mentions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Contracts §2.5.1 note. Its body was written by a person: data, never
    instructions (FR-CRM-MCP-06)."""
    return {
        "id": row["id"],
        "record_type": "note",
        "body": row["body"],
        "kind": row["kind"],
        "kind_label": objects.NOTE_KINDS.get(row["kind"], row["kind"]),
        "occurred_on": row.get("occurred_on"),
        "links": [link_target(link, names or {}) for link in links or []],
        "mentions": [
            {"id": m["member_id"], "name": facts.name(m["member_id"])} for m in mentions or []
        ],
        **_stamps(row, facts),
        # Spec 009 FR-CRMAI-MEET-05: a meeting note the job wrote; its body
        # is plain text (contracts §1).
        "source_ref": row.get("source_ref"),
        "ai_provenance": row.get("ai_provenance"),
        **_note_ai(row),
    }


def _note_ai(row: dict[str, Any]) -> dict[str, Any]:
    """The `ai` provenance of a meeting note the job wrote (contracts §1, P-2)."""
    made = row.get("ai_provenance")
    if not made:
        return {}
    return {
        "ai": {
            "source_kind": made.get("source_kind"),
            "model": made.get("model"),
            "generated_at": made.get("generated_at"),
            "source": {"record_type": "note", "record_id": row["id"]},
        }
    }


def contact(row: dict[str, Any], facts: Facts, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A deal's other contact and their Role in deal (spec §5.8)."""
    person_row = people.get(row["person_id"])
    return {
        "id": row["id"],
        "person_id": row["person_id"],
        "person_name": (
            full_name(person_row["first_name"], person_row.get("last_name")) if person_row else None
        ),
        "job_title": person_row.get("job_title") if person_row else None,
        "role_id": row.get("role_id"),
        "role_label": facts.option_label(row.get("role_id")),
    }


def drive_link(row: dict[str, Any], facts: Facts) -> dict[str, Any]:
    """FR-CRM-LINK-01 — the address and its label, nothing of the file."""
    return {
        "id": row["id"],
        "record_type": row["record_type"],
        "record_id": row["record_id"],
        "url": row["url"],
        "label": row["label"],
        "created_at": row.get("created_at"),
        "created_by_name": facts.name(row.get("created_by")),
    }


def trash_row(kind: str, row: dict[str, Any], facts: Facts, now: datetime) -> dict[str, Any]:
    """Contracts §2.5 `GET /crm/trash` — what it is, who deleted it, and how
    many days are left before the purge (FR-CRM-DEL-01)."""
    deleted = datetime.fromisoformat(str(row["deleted_at"]))
    if deleted.tzinfo is None:
        deleted = deleted.replace(tzinfo=UTC)
    left = 90 - (now - deleted).days
    if kind in ("company", "opportunity", "campaign"):
        name = row["name"]
    elif kind == "person":
        name = full_name(row.get("first_name"), row.get("last_name"))
    elif kind == "task":
        name = row.get("title")
    elif kind == "note":
        body = " ".join(str(row.get("body") or "").split())
        name = body if len(body) <= 80 else f"{body[:79]}…"
    else:
        name = row.get("label")
    return {
        "id": row["id"],
        "record_type": kind,
        "name": name,
        "deleted_at": row["deleted_at"],
        "deleted_by": row.get("deleted_by"),
        "deleted_by_name": facts.name(row.get("deleted_by")),
        "days_left": max(left, 0),
    }


# ---------------------------------------------------------------------------
# Timeline — FR-CRM-REC-03, -04, FR-CRM-AUD-05
# ---------------------------------------------------------------------------


def _display(kind: str, name: str, value: Any, facts: Facts) -> Any:
    """A changed value as a person reads it: option and person ids by name."""
    if value is None:
        return None
    if kind == "campaign_member" and name == "status":
        return member_status_label(facts, value)
    if name in _FIXED_LISTS:
        return facts.fixed_label(_FIXED_LISTS[name], value)
    if name in ("owner_id", "deleted_by", "assignee_id"):
        return facts.name(value) or value
    if name == "entities" and isinstance(value, list):
        return [objects.ENTITIES.get(v, v) for v in value]
    if name == "entity":
        return objects.ENTITIES.get(value, value)
    if name == "kind" and kind == "note":
        return objects.NOTE_KINDS.get(value, value)
    if name == "project_category":
        return objects.PROJECT_CATEGORIES.get(value, value)
    spec = objects.field(kind, name)
    if spec and spec.list_key and name.endswith("_ids") and isinstance(value, list):
        return [facts.option_label(v) or v for v in value]
    if spec and spec.list_key and name.endswith("_id"):
        return facts.option_label(value) or value
    return value


def display(kind: str, name: str, value: Any, facts: Facts) -> Any:
    """A field's value as a person reads it, for the merge dialog's choices."""
    return _display(kind, name, value, facts)


@dataclass
class Around:
    """What a record's timeline names besides the record: its Drive links,
    tasks and notes by id, and people by id (a deal's contacts)."""

    links: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    people: dict[str, dict[str, Any]] = field(default_factory=dict)


def timeline(
    rows: list[dict[str, Any]],
    kind: str,
    facts: Facts,
    *,
    links: dict[str, dict[str, Any]] | None = None,
    around: Around | None = None,
) -> list[dict[str, Any]]:
    """Audit rows as timeline entries, newest first, each naming its actor and
    channel (FR-CRM-REC-04). For a Viewer, a change to an amount field loses
    its values, and an entry left with nothing to show is dropped
    (FR-CRM-AUD-05)."""
    hidden = objects.amount_fields(kind) if facts.role == "viewer" else frozenset()
    near = around or Around()
    if links:
        near.links = {**near.links, **links}
    entries = []
    for row in rows:
        entry = _entry(row, kind, facts, hidden, near)
        if entry is None:
            continue
        entries.append(
            {
                "id": row["id"],
                "at": row["at"],
                "actor_name": actor_name(row.get("actor_id"), facts.names, row.get("actor_label")),
                "actor_kind": row.get("actor_label"),
                "channel": row.get("channel"),
                **entry,
            }
        )
    return entries


_ACTIVITY = {
    "note": "Added a note",
    "call": "Logged a call",
    "meeting": "Logged a meeting",
    "whatsapp": "Logged a WhatsApp message",
    "other": "Logged an activity",
}


def _task_entry(row, verb, before, after, near: Around) -> dict[str, Any] | None:
    """FR-CRM-REC-03 "task" — a task linked to the record, by its title."""
    if row["target_table"] in ("crm_task_links", "task_links"):
        task_id = (after or before).get("task_id")
        title = (near.tasks.get(task_id) or {}).get("title") or "a task"
        said = f"Added task “{title}”" if verb == "created" else f"Unlinked task “{title}”"
        return {"type": "task", "summary": said, "changes": []}
    title = (near.tasks.get(row.get("target_id") or "") or after or before).get("title") or "task"
    if verb == "created":
        return None  # the link row says it
    if verb == "deleted":
        said = f"Moved task “{title}” to Trash"
    elif verb == "restored":
        said = f"Restored task “{title}”"
    elif verb == "purged":
        said = f"Deleted task “{title}” for good"
    elif after.get("status") == "done":
        said = f"Completed task “{title}”"
    elif before.get("status") == "done":
        said = f"Reopened task “{title}”"
    else:
        said = f"Changed task “{title}”"
    return {"type": "task", "summary": said, "changes": []}


def _note_entry(row, verb, before, after, near: Around) -> dict[str, Any] | None:
    """FR-CRM-REC-03 "note" — a note or logged activity on the record."""
    if row["target_table"] == "crm_note_links":
        note_id = (after or before).get("note_id")
        found = near.notes.get(note_id) or {}
        if verb != "created":
            return {"type": "note", "summary": "Unlinked a note", "changes": []}
        said = _ACTIVITY.get(found.get("kind") or "note", "Added a note")
        if found.get("occurred_on") and found.get("kind") != "note":
            said = f"{said} on {found['occurred_on']}"
        entry = {"type": "note", "summary": said, "changes": []}
        # Spec 009 FR-CRMAI-MEET-05: the AI marker on a meeting note's entry.
        made = found.get("ai_provenance")
        if made:
            entry["ai"] = {
                "source_kind": made.get("source_kind"),
                "model": made.get("model"),
                "generated_at": made.get("generated_at"),
                "source": {"record_type": "note", "record_id": note_id},
            }
        return entry
    if verb == "created":
        return None  # the link row says it
    said = {
        "deleted": "Moved a note to Trash",
        "restored": "Restored a note",
        "purged": "Deleted a note for good",
    }.get(verb, "Edited a note")
    return {"type": "note", "summary": said, "changes": []}


def _entry(row, kind, facts, hidden, near: Around) -> dict[str, Any] | None:
    table, action = row["target_table"], row["action"]
    verb = action.rsplit(".", 1)[-1]
    before, after = row.get("before") or {}, row.get("after") or {}

    if table in ("crm_task_links", "task_links", "crm_tasks", "tasks") and kind != "task":
        return _task_entry(row, verb, before, after, near)
    if table in ("crm_note_links", "crm_notes") and kind != "note":
        return _note_entry(row, verb, before, after, near)
    if table == "crm_opportunity_contacts":
        person_row = near.people.get((after or before).get("person_id") or "")
        who = full_name(person_row["first_name"], person_row.get("last_name")) if person_row else ""
        if verb == "created":
            said = f"Added contact {who}".strip()
        elif verb == "purged":
            said = f"Removed contact {who}".strip()
        else:
            said = f"Changed contact {who}".strip()
        return {"type": "field", "summary": said, "changes": []}

    if table == "crm_drive_links":
        link = near.links.get(row.get("target_id") or "") or after or before
        label = link.get("label") or "a Drive link"
        said = {
            "created": f"Added Drive link “{label}”",
            "deleted": f"Removed Drive link “{label}”",
            "restored": f"Restored Drive link “{label}”",
            "purged": f"Deleted Drive link “{label}” for good",
        }.get(verb, f"Changed Drive link “{label}”")
        return {"type": "link", "summary": said, "changes": []}

    if table == "crm_company_domains":
        if verb == "created":
            said = f"Added domain {after.get('domain')}"
        else:
            said = f"Removed domain {before.get('domain')}"
        return {"type": "field", "summary": said, "changes": []}

    if verb == "handoff":
        # FR-CRM-TVA-09: the deal's handoff event (033).
        said = f"Handed off to TVA: project “{after.get('project_name')}” confirmed"
        if after.get("lead_id"):
            said += f", led by {facts.name(after['lead_id']) or 'a staff member'}"
        return {"type": "handoff", "summary": f"{said}.", "changes": []}
    if verb == "project_linked":
        # FR-CRM-TVA-18.
        made = "Created" if after.get("mode") == "create" else "Linked"
        said = f"{made} TVA project “{after.get('project_name')}”"
        return {"type": "handoff", "summary": said, "changes": []}

    noun = NOUNS.get(kind, "record")
    if verb == "created":
        return {"type": "create", "summary": f"Created this {noun}", "changes": []}
    if verb == "deleted":
        return {"type": "delete", "summary": "Moved to Trash", "changes": []}
    if verb == "restored":
        return {"type": "delete", "summary": "Restored from Trash", "changes": []}
    if verb == "merged":
        # FR-CRM-DUP-03 (035): the survivor's row names the company merged
        # into it; the merged company's row is its move to the Trash.
        if after.get("merged_name"):
            said = f"Merged “{after['merged_name']}” into this company"
        elif after.get("merged_into_id"):
            said = "Merged into another company"
        else:
            said = "Merged another company into this one"
        return {"type": "merge", "summary": said, "changes": []}

    changes = [
        {
            "field": name,
            "label": objects.label(kind, name),
            "before": _display(kind, name, before.get(name), facts),
            "after": _display(kind, name, after.get(name), facts),
        }
        for name in sorted(set(after) | set(before))
        if name not in _BOOKKEEPING and name not in _TRASH and name not in hidden
    ]
    if not changes:
        return None
    labels = [c["label"] for c in changes]
    said = (
        f"Changed {labels[0]}"
        if len(labels) == 1
        else f"Changed {', '.join(labels[:-1])} and {labels[-1]}"
    )
    moved = next((c for c in changes if c["field"] == "stage"), None)
    if moved:
        said = f"Moved from {moved['before']} to {moved['after']}"
    kind_of = "stage" if any(c["field"] in ("lead_stage", "stage") for c in changes) else "field"
    return {"type": kind_of, "summary": said, "changes": changes}


# ---------------------------------------------------------------------------
# Search hits — FR-CRM-SRCH-01 to -03, FR-CRM-ACC-11
# ---------------------------------------------------------------------------


def search_hit(kind: str, row: dict[str, Any], facts: Facts) -> dict[str, Any]:
    """One line of the command menu: what it is and enough to tell two apart.
    A Viewer's deal hit carries no amount (`crm_search` read none either)."""
    if kind == "company":
        out = {
            "id": row["id"],
            "record_type": "company",
            "name": row["name"],
            "domain": row.get("domain"),
            "lead_stage": row.get("lead_stage"),
            "lead_stage_label": facts.fixed_label("company.lead_stage", row.get("lead_stage")),
        }
    elif kind == "person":
        out = {
            "id": row["id"],
            "record_type": "person",
            "name": full_name(row.get("first_name"), row.get("last_name")),
            "email": row.get("primary_email"),
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name"),
        }
    elif kind == "opportunity":
        out = {
            "id": row["id"],
            "record_type": "opportunity",
            "name": row["name"],
            "stage": row.get("stage"),
            "stage_label": facts.fixed_label("opportunity.stage", row.get("stage")),
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name"),
            "currency": row.get("currency"),
            "amount": money(row.get("amount")),
            "amount_in_inr": money(row.get("amount_in_inr")),
        }
    elif kind == "task":
        out = {
            "id": row["id"],
            "record_type": "task",
            "name": row["title"],
            "status": row.get("status"),
            "status_label": facts.fixed_label("task.status", row.get("status")),
            "due_on": row.get("due_on"),
        }
    elif kind == "campaign":
        # 041: a name, a status and dates; never the budget, for anyone.
        out = {
            "id": row["id"],
            "record_type": "campaign",
            "name": row["name"],
            "status_id": row.get("status_id"),
            "status_label": facts.option_label(row.get("status_id")),
            "starts_on": row.get("starts_on"),
            "ends_on": row.get("ends_on"),
        }
    else:
        snippet = " ".join(str(row.get("snippet") or "").split())
        out = {
            "id": row["id"],
            "record_type": "note",
            "name": snippet if len(snippet) <= 80 else f"{snippet[:79]}…",
            "kind": row.get("kind"),
            "kind_label": objects.NOTE_KINDS.get(row.get("kind") or "note"),
        }
    return redact(out, kind, facts.role)
