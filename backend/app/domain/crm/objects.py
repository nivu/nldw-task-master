"""The CRM's field registry — spec 007 §5.6–§5.8, FR-CRM-LIST-07, -08,
FR-CRM-CO-01, -03, FR-CRM-PER-01, FR-CRM-WS-06, FR-CRM-ACC-11; plan §5.2, §5.5.

One table per object of what each field is: its type (one of FR-CRM-LIST-07),
whether it is required, written by the system only, an amount (omitted for a
Viewer), used in one workspace only, sortable, and which select list its
options come from. The services validate writes from it and the presenter
redacts from it, so a field's rules live in one place. Pure.

Opportunities, tasks and notes joined in S4, campaigns and their members
in 1b (S11a); the campaign's amount fields were listed before its table
existed, because redaction must never depend on a slice remembering to add
them (plan §5.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

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

#: Spec §5.8. `stage` and `lost_reason_id` change only through a stage move
#: (contracts §2.6); the rest of the system-written fields are set by a move,
#: the FX conversion or the handoff.
OPPORTUNITY: Final = (
    Field("name", "Name", "text", required=True, max_length=200),
    Field("company_id", "Company", "relation", required=True),
    Field("point_of_contact_id", "Point of contact", "relation"),
    Field("entity", "Entity", "select", scope="labs"),
    Field("stage", "Stage", "select", list_key="opportunity.stage"),
    Field("amount", "Amount", "currency", amount=True),
    Field("currency", "Currency", "select"),
    Field("probability", "Probability", "number"),
    Field("expected_close_on", "Expected close date", "date"),
    Field("next_step", "Next step", "text", max_length=500),
    Field("next_step_on", "Next step date", "date"),
    Field("owner_id", "Owner", "relation", required=True),
    Field("channel_id", "Channel", "select", list_key="channel"),
    Field("deal_type_id", "Deal type", "select", list_key="opportunity.deal_type"),
    Field("project_category", "Project category", "select", scope="labs"),
    Field(
        "engagement_model_id",
        "Engagement model",
        "select",
        list_key="opportunity.engagement_model",
    ),
    Field("programme_id", "Programme", "select", scope="academy", list_key="opportunity.programme"),
    Field("proposal_sent_on", "Proposal sent on", "date"),
    Field("lost_reason_id", "Lost reason", "select", list_key="opportunity.lost_reason"),
    Field("lost_notes", "Lost notes", "long_text", sortable=False, max_length=20_000),
    _system("stage_entered_at", "Stage entered at"),
    Field("amount_in_inr", "Amount in INR", "currency", system=True, amount=True),
    Field("fx_rate_used", "FX rate used", "number", system=True, amount=True),
    _system("fx_month", "FX month", "date"),
    Field("won_fx_rate", "FX rate at win", "number", system=True, amount=True),
    _system("won_fx_month", "FX month at win", "date"),
    Field("weighted_amount", "Weighted amount", "currency", system=True, amount=True),
    _system("won_on", "Won on", "date"),
    _system("lost_on", "Lost on", "date"),
    _system("lost_from_stage", "Lost from stage", "select"),
    # 1b (S11a): written by a campaign conversion (FR-CRM-CAMP-04).
    _system("source_campaign_id", "Source campaign", "relation"),
    _system("flag_level", "Deal health flag", "select"),
    _system("flags", "Deal health flags", "array"),
    _system("flags_computed_at", "Deal health computed"),
    _system("twenty_id", "Twenty id", "text"),
)

#: Spec §5.9. A task links to at least one record (FR-CRM-TASK-01).
TASK: Final = (
    Field("title", "Title", "text", required=True, max_length=200),
    Field("body", "Notes", "long_text", sortable=False, max_length=20_000),
    Field("status", "Status", "select", list_key="task.status"),
    Field("due_on", "Due date", "date"),
    Field("bucket", "Bucket", "select", list_key="task.bucket"),
    Field("category_id", "Category", "select", list_key="task.category"),
    Field("assignee_id", "Assignee", "relation", required=True),
    _system("completed_at", "Completed at"),
    _system("flag_level", "Follow-up flag", "select"),
    _system("flags", "Follow-up flags", "array"),
    _system("flags_computed_at", "Follow-up flags computed"),
)

#: Spec §5.10. A typed note is a logged activity with its date
#: (FR-CRM-NOTE-03).
NOTE: Final = (
    Field("body", "Text", "long_text", required=True, sortable=False, max_length=20_000),
    Field("kind", "Kind", "select"),
    Field("occurred_on", "Date", "date"),
)

#: Spec §6.6 (1b). The budget is an amount: it is stored with its INR value
#: at the month's rate (FR-CRM-FX-03) and omitted for a Viewer. Converted
#: deals, pipeline created and won value are derived on read.
CAMPAIGN: Final = (
    Field("name", "Name", "text", required=True, max_length=200),
    Field("type_id", "Type", "select", list_key="campaign.type"),
    Field("status_id", "Status", "select", list_key="campaign.status"),
    Field("channel_id", "Channel", "select", list_key="channel"),
    Field("entity", "Entity", "select", scope="labs"),
    Field("owner_id", "Owner", "relation", required=True),
    Field("starts_on", "Start date", "date"),
    Field("ends_on", "End date", "date"),
    Field("goal", "Goal", "long_text", sortable=False, max_length=20_000),
    Field("budget", "Budget", "currency", amount=True),
    Field("budget_currency", "Budget currency", "select"),
    Field("budget_in_inr", "Budget in INR", "currency", system=True, amount=True),
    Field("budget_fx_rate", "Budget FX rate", "number", system=True, amount=True),
    _system("twenty_id", "Twenty id", "text"),
)

#: Spec §6.6 (1b). A member is a company, a person or both (FR-CRM-CAMP-01);
#: Converted is reached only by a conversion, which sets the deal.
CAMPAIGN_MEMBER: Final = (
    Field("campaign_id", "Campaign", "relation", required=True),
    Field("company_id", "Company", "relation"),
    Field("person_id", "Person", "relation"),
    Field("status", "Status", "select", list_key="campaign_member.status"),
    Field("last_touch_on", "Last touch", "date"),
    Field("notes", "Notes", "long_text", sortable=False, max_length=20_000),
    Field("converted_opportunity_id", "Converted opportunity", "relation", system=True),
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
    "opportunity": OPPORTUNITY,
    "task": TASK,
    "note": NOTE,
    "drive_link": DRIVE_LINK,
    "campaign": CAMPAIGN,
    "campaign_member": CAMPAIGN_MEMBER,
}

#: Plan §5.5, FR-CRM-ACC-11 — omitted for a Viewer wherever they would
#: appear: records, lists, timelines, search, exports and MCP answers. A
#: currency code is not an amount and stays.
AMOUNT_FIELDS: Final[dict[str, frozenset[str]]] = {
    "company": frozenset(),
    "person": frozenset(),
    "task": frozenset(),
    "note": frozenset(),
    "drive_link": frozenset(),
    "campaign_member": frozenset(),
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
#: The campaign's amounts derived on read from its members' deals (spec
#: §6.6): redacted like the stored ones, but no column of the registry.
DERIVED_AMOUNTS: Final[dict[str, frozenset[str]]] = {
    "campaign": frozenset({"pipeline_created", "won_value", "cost_per_converted"}),
}
#: TVA figures a CRM answer can carry (the handoff preview, S5).
TVA_AMOUNT_KEYS: Final = frozenset({"revenue", "suggested_inr", "existing_tva_revenue"})

#: Spec record type → table, for the objects that exist so far.
TABLES: Final = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "task": "tasks",
    "note": "crm_notes",
    "drive_link": "crm_drive_links",
    "campaign": "crm_campaigns",
    "campaign_member": "crm_campaign_members",
}
#: The records a Drive link may hang on (FR-CRM-LINK-01); campaigns from 1b.
LINKABLE: Final = ("company", "person", "opportunity", "task", "note", "campaign")
#: The records a task or a note links to (FR-CRM-TASK-01, FR-CRM-NOTE-01);
#: campaigns from 1b.
TASK_NOTE_TARGETS: Final = ("company", "person", "opportunity", "campaign")

#: FR-CRM-OPP-01 and the fixed task and note lists.
CURRENCIES: Final = ("INR", "AUD", "USD")
PROJECT_CATEGORIES: Final = {"client": "Client", "poc": "POC", "product": "Product"}
NOTE_KINDS: Final = {
    "note": "Note",
    "call": "Call",
    "meeting": "Meeting",
    "whatsapp": "WhatsApp",
    "other": "Other",
}

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

#: Spec §6.6 — a campaign member's fixed statuses, in board order (041).
MEMBER_STATUSES: Final = (
    "targeted",
    "invited",
    "registered",
    "attended",
    "engaged",
    "meeting_booked",
    "converted",
    "not_interested",
    "unsubscribed",
)


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
    article = {
        "company": "A company",
        "person": "A person",
        "opportunity": "A deal",
        "task": "A task",
        "note": "A note",
        "drive_link": "A Drive link",
        "campaign": "A campaign",
        "campaign_member": "A campaign member",
    }
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


# ---------------------------------------------------------------------------
# The view registry — data-model §3.15, FR-CRM-VIEW-02, -03, -08, -10
# ---------------------------------------------------------------------------

#: `crm_object_fields.type`: how a field is filtered, sorted and grouped.
#: Option ids are `select`, fixed keys (stage, lead stage) `fixed_select`,
#: people `person`, records `relation`.
ViewType = Literal[
    "text",
    "long_text",
    "number",
    "currency",
    "date",
    "datetime",
    "boolean",
    "select",
    "multi_select",
    "fixed_select",
    "person",
    "relation",
    "flag",
]
#: Entity and Project category belong to the TVA-linked workspace,
#: Institution type and Programme to the other (FR-CRM-WS-06, Q-40).
ViewScope = Literal["all", "tva_linked", "not_tva_linked"]


@dataclass(frozen=True)
class ViewField:
    """One row of `crm_object_fields`, which `crm_query()` reads; a test
    holds the two equal (S6a)."""

    field: str
    column_sql: str
    type: ViewType
    is_amount: bool = False
    filterable: bool = True
    sortable: bool = True
    groupable: bool = False
    aggregatable: bool = False
    workspace_scope: ViewScope = "all"


def _v(field_: str, type_: ViewType, **flags: Any) -> ViewField:
    """A field whose column has its name. Option, person and relation fields
    group a list; money and numbers total."""
    flags.setdefault(
        "groupable", type_ in ("select", "fixed_select", "person", "relation", "boolean", "flag")
    )
    flags.setdefault("aggregatable", type_ in ("number", "currency"))
    return ViewField(field_, flags.pop("column_sql", field_), type_, **flags)


#: Every record has these.
_STAMPS: Final = (
    _v("created_at", "datetime"),
    _v("updated_at", "datetime"),
)
#: Kanban order (data-model §2): sorted on, never filtered or totalled.
_POSITION: Final = _v("position", "number", filterable=False, aggregatable=False)

VIEW_FIELDS: Final[dict[str, tuple[ViewField, ...]]] = {
    "company": (
        _v("name", "text"),
        _v("domain", "text", column_sql="primary_domain"),
        _v("entities", "multi_select", sortable=False, workspace_scope="tva_linked"),
        _v("lead_stage", "fixed_select"),
        _v("account_type_id", "select"),
        _v("relationship_status_id", "select"),
        _v("channel_ids", "multi_select", sortable=False),
        _v("owner_id", "person"),
        _v("next_touch_on", "date"),
        _v("industry_id", "select"),
        _v("institution_type_id", "select", workspace_scope="not_tva_linked"),
        _v("size_id", "select"),
        _v("country", "text"),
        _v("city", "text"),
        _v("description", "long_text", sortable=False),
        _v("source_id", "select"),
        _v("last_inbound_at", "datetime"),
        _v("last_outbound_at", "datetime"),
        _v("last_contact_at", "datetime"),
        # 038: follow-up flags (FR-CRM-FLAG-05).
        _v("flag_level", "flag"),
        *_STAMPS,
        _POSITION,
        # Spec 009 (067): contact recency, filter only (FR-CRMAI-REL-02).
        _v("contact_recency", "fixed_select", sortable=False, groupable=False),
    ),
    "person": (
        _v("name", "text", column_sql="full_name"),
        _v("first_name", "text"),
        _v("last_name", "text"),
        _v("company_id", "relation"),
        _v("primary_email", "text"),
        _v("job_title", "text"),
        _v("city", "text"),
        _v("owner_id", "person"),
        _v("do_not_contact", "boolean"),
        _v("last_inbound_at", "datetime"),
        _v("last_outbound_at", "datetime"),
        _v("last_contact_at", "datetime"),
        _v("flag_level", "flag"),
        *_STAMPS,
        _POSITION,
        # Spec 009 (067): contact recency, filter only (FR-CRMAI-REL-02).
        _v("contact_recency", "fixed_select", sortable=False, groupable=False),
    ),
    "opportunity": (
        _v("name", "text"),
        _v("company_id", "relation"),
        _v("point_of_contact_id", "relation"),
        _v("entity", "fixed_select", workspace_scope="tva_linked"),
        _v("stage", "fixed_select"),
        _v("amount", "currency", is_amount=True),
        _v("currency", "fixed_select"),
        _v("amount_in_inr", "currency", is_amount=True),
        _v("weighted_amount", "currency", is_amount=True),
        _v("fx_rate_used", "number", is_amount=True, aggregatable=False),
        _v("won_fx_rate", "number", is_amount=True, aggregatable=False),
        _v("probability", "number"),
        _v("expected_close_on", "date"),
        _v("next_step", "text"),
        _v("next_step_on", "date"),
        _v("owner_id", "person"),
        _v("channel_id", "select"),
        _v("deal_type_id", "select"),
        _v("project_category", "fixed_select", workspace_scope="tva_linked"),
        _v("engagement_model_id", "select"),
        _v("programme_id", "select", workspace_scope="not_tva_linked"),
        _v("proposal_sent_on", "date"),
        _v("lost_reason_id", "select"),
        _v("lost_notes", "long_text", sortable=False),
        _v("lost_from_stage", "fixed_select"),
        _v("stage_entered_at", "datetime"),
        _v("won_on", "date"),
        _v("lost_on", "date"),
        _v("flag_level", "flag"),
        *_STAMPS,
        _POSITION,
    ),
    "task": (
        _v("title", "text"),
        _v("body", "long_text", sortable=False),
        _v("status", "fixed_select"),
        _v("due_on", "date"),
        _v("bucket", "fixed_select"),
        _v("category_id", "select"),
        _v("assignee_id", "person"),
        _v("completed_at", "datetime"),
        _v("flag_level", "flag"),
        # 008 (047): owner decision of 18 October 2026, data-model §3.3.
        _v("priority", "fixed_select"),
        _v("source", "fixed_select"),
        *_STAMPS,
        _POSITION,
    ),
    "note": (
        _v("body", "long_text", sortable=False),
        _v("kind", "fixed_select"),
        _v("occurred_on", "date"),
        _v("created_by", "person"),
        *_STAMPS,
    ),
    # 041 (1b): campaigns and their members.
    "campaign": (
        _v("name", "text"),
        _v("type_id", "select"),
        _v("status_id", "select"),
        _v("channel_id", "select"),
        _v("entity", "fixed_select", workspace_scope="tva_linked"),
        _v("owner_id", "person"),
        _v("starts_on", "date"),
        _v("ends_on", "date"),
        _v("goal", "long_text", sortable=False),
        _v("budget", "currency", is_amount=True),
        _v("budget_currency", "fixed_select"),
        _v("budget_in_inr", "currency", is_amount=True),
        _v("budget_fx_rate", "number", is_amount=True, aggregatable=False),
        *_STAMPS,
        _POSITION,
    ),
    "campaign_member": (
        _v("campaign_id", "relation"),
        _v("company_id", "relation"),
        _v("person_id", "relation"),
        _v("status", "fixed_select"),
        _v("last_touch_on", "date"),
        _v("notes", "long_text", sortable=False),
        _v("converted_opportunity_id", "relation"),
        *_STAMPS,
        _POSITION,
    ),
}


def view_field(kind: str, name: str) -> ViewField | None:
    return next((f for f in VIEW_FIELDS.get(kind, ()) if f.field == name), None)
