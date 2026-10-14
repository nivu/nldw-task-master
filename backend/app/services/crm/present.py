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
from typing import Any

from app.domain.audit import actor_name
from app.domain.crm import objects
from app.domain.crm.dedupe import full_name

#: The audit's bookkeeping columns: never shown as a change.
_BOOKKEEPING = frozenset(
    {"id", "workspace_id", "created_at", "created_by", "updated_at", "updated_by", "position"}
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

NOUNS = {"company": "company", "person": "person", "drive_link": "Drive link"}


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
        **_stamps(row, facts),
    }
    return redact(out, "person", facts.role)


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
    name = (
        row["name"]
        if kind == "company"
        else full_name(row.get("first_name"), row.get("last_name"))
        if kind == "person"
        else row.get("label")
    )
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
    if name == "lead_stage":
        return facts.fixed_label("company.lead_stage", value)
    if name in ("owner_id", "deleted_by"):
        return facts.name(value) or value
    if name == "entities" and isinstance(value, list):
        return [objects.ENTITIES.get(v, v) for v in value]
    spec = objects.field(kind, name)
    if spec and spec.list_key and name.endswith("_ids") and isinstance(value, list):
        return [facts.option_label(v) or v for v in value]
    if spec and spec.list_key and name.endswith("_id"):
        return facts.option_label(value) or value
    return value


def timeline(
    rows: list[dict[str, Any]],
    kind: str,
    facts: Facts,
    *,
    links: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Audit rows as timeline entries, newest first, each naming its actor and
    channel (FR-CRM-REC-04). For a Viewer, a change to an amount field loses
    its values, and an entry left with nothing to show is dropped
    (FR-CRM-AUD-05)."""
    hidden = objects.amount_fields(kind) if facts.role == "viewer" else frozenset()
    entries = []
    for row in rows:
        entry = _entry(row, kind, facts, hidden, links or {})
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


def _entry(row, kind, facts, hidden, links) -> dict[str, Any] | None:
    table, action = row["target_table"], row["action"]
    verb = action.rsplit(".", 1)[-1]
    before, after = row.get("before") or {}, row.get("after") or {}

    if table == "crm_drive_links":
        link = links.get(row.get("target_id") or "") or after or before
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

    noun = NOUNS.get(kind, "record")
    if verb == "created":
        return {"type": "create", "summary": f"Created this {noun}", "changes": []}
    if verb == "deleted":
        return {"type": "delete", "summary": "Moved to Trash", "changes": []}
    if verb == "restored":
        return {"type": "delete", "summary": "Restored from Trash", "changes": []}
    if verb == "merged":
        return {"type": "merge", "summary": "Merged another company into this one", "changes": []}

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
    kind_of = "stage" if any(c["field"] in ("lead_stage", "stage") for c in changes) else "field"
    return {"type": kind_of, "summary": said, "changes": changes}
