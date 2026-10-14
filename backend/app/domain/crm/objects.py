"""The CRM's field registry — spec 007 §5.6–§5.8, FR-CRM-LIST-07, -08,
FR-CRM-CO-01, -03, FR-CRM-PER-01, FR-CRM-WS-06, FR-CRM-ACC-11; plan §5.2, §5.5.

One table per object of what each field is: its type (one of FR-CRM-LIST-07),
whether it is required, written by the system only, an amount (omitted for a
Viewer), used in one workspace only, sortable, and which select list its
options come from. The services validate writes from it and the presenter
redacts from it, so a field's rules live in one place. Pure.

Opportunities, tasks and notes join in S4, campaigns in 1b; their amount
fields are listed already, because redaction must never depend on a slice
remembering to add them (plan §5.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

FieldType = Literal[
    "text",
    "long_text",
    "number",
    "currency",
    "date",
    "datetime",
    "boolean",
    "select",
    "multi_select",
    "rating",
    "email",
    "emails",
    "phones",
    "links",
    "domain",
    "domains",
    "address",
    "relation",
    "array",
    "actor",
]
#: Labs is the workspace with Entity (FR-CRM-WS-06); Academy the one with
#: institution types and programmes (Q-40).
Scope = Literal["labs", "academy"]


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: FieldType
    required: bool = False
    system: bool = False
    amount: bool = False
    scope: Scope | None = None
    sortable: bool = True
    list_key: str | None = None
    max_length: int | None = None


def _system(name: str, label: str, type_: FieldType = "datetime") -> Field:
    return Field(name, label, type_, system=True)


#: Spec §5.6. `domain` and `additional_domains` are kept in
#: `crm_company_domains`, one unique index for all of them (FR-CRM-DUP-01).
COMPANY: Final = (
    Field("name", "Name", "text", required=True, max_length=200),
    Field("domain", "Domain", "domain"),
    Field("additional_domains", "Additional domains", "domains", sortable=False),
    Field("entities", "Entity", "multi_select", scope="labs", sortable=False),
    Field("lead_stage", "Lead stage", "select", list_key="company.lead_stage"),
    Field("account_type_id", "Account type", "select", list_key="company.account_type"),
    Field(
        "relationship_status_id",
        "Relationship status",
        "select",
        list_key="company.relationship_status",
    ),
    Field("channel_ids", "Channels", "multi_select", list_key="channel", sortable=False),
    Field("owner_id", "Owner", "relation", required=True),
    Field("next_touch_on", "Next touch", "date"),
    Field("industry_id", "Industry", "select", list_key="company.industry"),
    Field(
        "institution_type_id",
        "Institution type",
        "select",
        scope="academy",
        list_key="company.institution_type",
    ),
    Field("size_id", "Size", "select", list_key="company.size"),
    Field("country", "Country", "text", max_length=100),
    Field("city", "City", "text", max_length=100),
    Field("address", "Address", "address", sortable=False),
    Field("website", "Website", "links", sortable=False),
    Field("linkedin", "LinkedIn", "links", sortable=False),
    Field("description", "Description", "long_text", sortable=False, max_length=20_000),
    Field("source_id", "Source", "select", list_key="source"),
    _system("last_inbound_at", "Last inbound"),
    _system("last_outbound_at", "Last outbound"),
    _system("last_contact_at", "Last contact"),
    _system("flag_level", "Follow-up flag", "select"),
    _system("flags", "Follow-up flags", "array"),
    _system("flags_computed_at", "Follow-up flags computed"),
    _system("twenty_id", "Twenty id", "text"),
    _system("merged_into_id", "Merged into", "relation"),
)

#: Spec §5.7.
PERSON: Final = (
    Field("first_name", "First name", "text", required=True, max_length=100),
    Field("last_name", "Last name", "text", max_length=100),
    Field("company_id", "Company", "relation"),
    Field("primary_email", "Email", "email"),
    Field("additional_emails", "Additional emails", "emails", sortable=False),
    Field("phones", "Phones", "phones", sortable=False),
    Field("job_title", "Job title", "text", max_length=200),
    Field("city", "City", "text", max_length=100),
    Field("linkedin", "LinkedIn", "links", sortable=False),
    Field("owner_id", "Owner", "relation"),
    Field("do_not_contact", "Do not contact", "boolean"),
    _system("last_inbound_at", "Last inbound"),
    _system("last_outbound_at", "Last outbound"),
    _system("last_contact_at", "Last contact"),
    _system("flag_level", "Follow-up flag", "select"),
    _system("flags", "Follow-up flags", "array"),
    _system("flags_computed_at", "Follow-up flags computed"),
    _system("twenty_id", "Twenty id", "text"),
)

#: FR-CRM-LINK-01, D4.
DRIVE_LINK: Final = (
    Field("record_type", "Record type", "select", required=True),
    Field("record_id", "Record", "relation", required=True),
    Field("url", "Link", "text", required=True, max_length=2000),
    Field("label", "Label", "text", required=True, max_length=200),
)

FIELDS: Final[dict[str, tuple[Field, ...]]] = {
    "company": COMPANY,
    "person": PERSON,
    "drive_link": DRIVE_LINK,
}

#: Plan §5.5, FR-CRM-ACC-11 — omitted for a Viewer wherever they would
#: appear: records, lists, timelines, search, exports and MCP answers. A
#: currency code is not an amount and stays.
AMOUNT_FIELDS: Final[dict[str, frozenset[str]]] = {
    "company": frozenset(),
    "person": frozenset(),
    "drive_link": frozenset(),
    "opportunity": frozenset(
        {"amount", "amount_in_inr", "fx_rate_used", "won_fx_rate", "weighted_amount"}
    ),
    "campaign": frozenset(
        {
            "budget",
            "budget_in_inr",
            "budget_fx_rate",
            "pipeline_created",
            "won_value",
            "cost_per_converted",
        }
    ),
}
#: TVA figures a CRM answer can carry (the handoff preview, S5).
TVA_AMOUNT_KEYS: Final = frozenset({"revenue", "suggested_inr", "existing_tva_revenue"})

#: Spec record type → table, for the objects that exist so far.
TABLES: Final = {
    "company": "crm_companies",
    "person": "crm_people",
    "drive_link": "crm_drive_links",
}
#: The records a Drive link may hang on (FR-CRM-LINK-01). Opportunities,
#: tasks and notes arrive in S4, campaigns in 1b; a link to one of them is
#: refused until its table exists.
LINKABLE: Final = ("company", "person")

#: FR-CRM-LIST-02 — the fixed lead stages, in order.
LEAD_STAGES: Final = (
    "new",
    "contacted",
    "nurturing",
    "qualified",
    "deal_open",
    "customer",
    "partner",
    "disqualified",
)
#: Contracts §2.5.1 — chosen by hand. Deal open, Customer and Partner follow
#: from deals (FR-CRM-PIPE-04, -05).
MANUAL_LEAD_STAGES: Final = ("new", "contacted", "nurturing", "qualified", "disqualified")

#: FR-CRM-WS-06.
ENTITIES: Final = {"nunnari_labs": "Nunnari Labs", "deepweaver": "DeepWeaver"}


def field(kind: str, name: str) -> Field | None:
    return next((f for f in FIELDS.get(kind, ()) if f.name == name), None)


def system_fields(kind: str) -> frozenset[str]:
    return frozenset(f.name for f in FIELDS.get(kind, ()) if f.system)


def amount_fields(kind: str) -> frozenset[str]:
    return AMOUNT_FIELDS.get(kind, frozenset()) | TVA_AMOUNT_KEYS


def label(kind: str, name: str) -> str:
    found = field(kind, name)
    return found.label if found else name.replace("_", " ").capitalize()


def system_refusal(kind: str, names: set[str] | list[str]) -> str | None:
    """FR-CRM-CO-03 — a system-written field is refused on any manual or MCP
    write, naming every one sent."""
    sent = [f.label for f in FIELDS.get(kind, ()) if f.system and f.name in names]
    if not sent:
        return None
    verb = "is" if len(sent) == 1 else "are"
    return f"{_and(sent)} {verb} written by the system and cannot be set by hand."


def scope_refusal(
    kind: str, names: set[str] | list[str], *, has_entity: bool, tva_linked: bool, workspace: str
) -> str | None:
    """FR-CRM-WS-06, Q-40 — Entity is Labs's, Institution type Academy's."""
    for f in FIELDS.get(kind, ()):
        if f.name not in names or f.scope is None:
            continue
        if (f.scope == "labs" and not has_entity) or (f.scope == "academy" and tva_linked):
            return f"{f.label} is not used in {workspace}."
    return None


def required_refusal(kind: str, values: dict) -> str | None:
    """The required fields missing or blank, named in one sentence."""
    missing = [
        f.label
        for f in FIELDS.get(kind, ())
        if f.required and (values.get(f.name) is None or str(values.get(f.name)).strip() == "")
    ]
    if not missing:
        return None
    article = {"company": "A company", "person": "A person", "drive_link": "A Drive link"}
    return f"{article.get(kind, 'A record')} needs {_and([m.lower() for m in missing])}."


def lead_stage_refusal(stage: str, *, creating: bool) -> str | None:
    """FR-CRM-PIPE-01, -02 and contracts §2.5.1."""
    if creating and stage != "new":
        return "A new company starts at New; change its lead stage once it exists."
    if stage not in MANUAL_LEAD_STAGES:
        return (
            "Deal open, Customer and Partner follow from the company's deals; "
            "choose New, Contacted, Nurturing, Qualified or Disqualified."
        )
    return None


def url_refusal(url: str, *, https_only: bool = False) -> str | None:
    """A link is a web address: `https://` for Drive links (FR-CRM-LINK-02),
    `http://` or `https://` for a website or LinkedIn."""
    text = url.strip()
    schemes = ("https://",) if https_only else ("https://", "http://")
    scheme = next((s for s in schemes if text.lower().startswith(s)), None)
    if scheme is None or len(text) == len(scheme) or any(c.isspace() for c in text):
        if https_only:
            return "A Drive link must be an https:// address."
        return f"“{text[:80]}” is not a web address starting https:// or http://."
    return None


def _and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"
