"""The Twenty importer's rules — spec 007 FR-CRM-MIG-01 to -08, FR-CRM-DUP-01,
-02, FR-CRM-PIPE-04, -10, FR-CRM-FX-03; Q-46, Q-55; contracts §2.9.

Clean room (NFR-CRM-09): the input shape is the one contracts §2.9 declares
and this module spells out field by field (`FIELDS`), with aliases for the
column-name variants an export may use. Nothing here is taken from Twenty's
code, schema or files.

`plan()` turns the parsed export and what the workspace already holds into
an `ImportPlan`: the counts per object, the lead stage derived for every
company, every conflict per row, the attachments to re-link from Drive, and
the rows to write. The dry run reports it; the real run writes it through
`crm_apply`, matched on `twenty_id`, so a second run of the same file finds
every row unchanged (FR-CRM-MIG-07). Pure: the service reads the workspace
and passes it in, with `today`.

Rules the plan follows, beyond the spec's:
- A deal already in the CRM keeps its stage and probability: they move in
  the CRM (a stage only through a stage move, data-model §5.2), so a re-run
  never moves a deal. A file that disagrees is reported.
- An open deal with no next step is refused unless the fix-up is chosen,
  which gives it "Review after import" dated the import day (Q-46). The
  database CHECK (030) would refuse it anyway (FR-CRM-PIPE-10).
- A domain or email another record already has is not taken from it: the
  row is imported without it (an email moves to the person's additional
  emails), and the report says so (FR-CRM-DUP-01).
- A company merged into another (FR-CRM-DUP-03) is not rewritten; its
  people, deals, tasks and notes land on the survivor.
- A re-run refreshes a record from the file, but a field the file leaves
  blank keeps what the CRM has.
- (1b) A campaign member names its campaign and a company, a person or
  both; a person marked Do not contact in the CRM is not added (FR-CRM-
  PER-02), and a member already on the campaign under another id is
  reported, not doubled. A member with a converted deal is Converted, and
  the deal records the campaign as its source (attribution) unless the file
  names another; Converted with no deal in the file is imported as Engaged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.crm import dedupe, fx
from app.domain.crm.objects import LEAD_STAGES, MEMBER_STATUSES
from app.domain.crm.pipeline import OPEN, default_probability
from app.domain.crm.settings import STAGE_LABELS

#: Contracts §2.9 and FR-CRM-MCP-08: an upload, or `crm_import` content.
MAX_BYTES: Final = 4 * 1024 * 1024
#: Rows per `crm_apply` call (plan, risk "statement timeout on big imports").
CHUNK: Final = 500
#: The fix-up of Q-46.
FIXUP_NEXT_STEP: Final = "Review after import"
#: Dates in the export are read in TVA's zone, as every CRM date is stored.
ZONE: Final = ZoneInfo("Asia/Kolkata")

#: The objects of a Twenty export, in the order they are written: each
#: refers only to those before it (a deal to its source campaign, a member
#: to its converted deal). Campaigns and their members from 1b (S11a).
OBJECTS: Final = (
    "companies",
    "people",
    "campaigns",
    "opportunities",
    "campaign_members",
    "tasks",
    "notes",
    "attachments",
)
TABLES: Final = {
    "companies": "crm_companies",
    "people": "crm_people",
    "campaigns": "crm_campaigns",
    "opportunities": "crm_opportunities",
    "campaign_members": "crm_campaign_members",
    "tasks": "crm_tasks",
    "notes": "crm_notes",
}
#: A link's record type, by the object it names.
RECORD_TYPES: Final = {
    "companies": "company",
    "people": "person",
    "opportunities": "opportunity",
    "campaigns": "campaign",
}
NOUNS: Final = {
    "companies": "company",
    "people": "person",
    "campaigns": "campaign",
    "opportunities": "deal",
    "campaign_members": "campaign member",
    "tasks": "task",
    "notes": "note",
    "attachments": "attachment",
}

#: The declared shape (contracts §2.9). Canonical field → accepted names,
#: compared after `_key()`: case, spaces, `_` and `-` do not matter, and a
#: nested JSON value is reached by a dotted path. CSV headers use the same
#: names (one file per object).
FIELDS: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "companies": {
        "id": ("id",),
        "name": ("name", "companyname"),
        "domain": ("domainname", "domain", "website", "domainname.url", "domainname.primaryurl"),
        "stage": ("stage", "leadstage", "status"),
        "account_type": ("accounttype", "type"),
        "owner_email": ("owneremail", "accountowneremail", "owner.email", "accountowner.email"),
        "city": ("city", "address.city", "address.addresscity"),
        "country": ("country", "address.country", "address.addresscountry"),
        "description": ("description", "about"),
    },
    "people": {
        "id": ("id",),
        "first_name": ("firstname", "name.firstname", "givenname"),
        "last_name": ("lastname", "name.lastname", "familyname", "surname"),
        "email": ("email", "primaryemail", "emails.primaryemail", "emails.primary"),
        "additional_emails": ("additionalemails", "emails.additionalemails", "emails.others"),
        "phone": ("phone", "phonenumber", "phones.primaryphonenumber", "phones.number"),
        "phone_country": ("phonecountrycode", "phones.primaryphonecountrycode", "countrycode"),
        "job_title": ("jobtitle", "title", "position"),
        "city": ("city",),
        "company_id": ("companyid", "company.id"),
        "owner_email": ("owneremail", "owner.email"),
        "last_inbound_at": ("lastinboundat", "lastemailreceivedat", "lastreceivedat"),
        "last_outbound_at": ("lastoutboundat", "lastemailsentat", "lastsentat"),
    },
    "opportunities": {
        "id": ("id",),
        "name": ("name",),
        "company_id": ("companyid", "company.id"),
        "contact_id": ("pointofcontactid", "personid", "pointofcontact.id"),
        "stage": ("stage",),
        "amount": ("amount", "amount.amount", "amount.value", "amountvalue"),
        "currency": ("currency", "currencycode", "amount.currency", "amount.currencycode"),
        "close_on": ("closedate", "expectedcloseon", "expectedclosedate", "closeon"),
        "probability": ("probability",),
        "next_step": ("nextstep",),
        "next_step_on": ("nextstepon", "nextstepdate", "nextstepdue"),
        "owner_email": ("owneremail", "owner.email"),
        "lost_reason": ("lostreason",),
        "created_at": ("createdat",),
        # 1b: the campaign it came from, when the file says so.
        "campaign_id": ("campaignid", "sourcecampaignid", "campaign.id", "sourcecampaign.id"),
    },
    "campaigns": {
        "id": ("id",),
        "name": ("name",),
        "type": ("type", "campaigntype"),
        "status": ("status",),
        "channel": ("channel",),
        "entity": ("entity",),
        "owner_email": ("owneremail", "owner.email"),
        "starts_on": ("startdate", "startson", "startat", "start"),
        "ends_on": ("enddate", "endson", "endat", "end"),
        "goal": ("goal", "objective", "description"),
        "budget": ("budget", "budget.amount", "budget.value", "budgetamount"),
        "budget_currency": (
            "budgetcurrency",
            "budget.currency",
            "budget.currencycode",
            "currency",
        ),
        "created_at": ("createdat",),
    },
    "campaign_members": {
        "id": ("id",),
        "campaign_id": ("campaignid", "campaign.id"),
        "company_id": ("companyid", "company.id"),
        "person_id": ("personid", "person.id"),
        "status": ("status",),
        "last_touch_on": ("lasttouchon", "lasttouchat", "lasttouch", "lasttouchdate"),
        "notes": ("notes", "note"),
        "converted_opportunity_id": (
            "convertedopportunityid",
            "opportunityid",
            "convertedopportunity.id",
            "opportunity.id",
        ),
    },
    "tasks": {
        "id": ("id",),
        "title": ("title", "name"),
        "body": ("body", "description", "bodyv2.markdown", "body.markdown"),
        "status": ("status",),
        "due_on": ("dueat", "duedate", "dueon"),
        "assignee_email": ("assigneeemail", "assignee.email"),
        "completed_at": ("completedat",),
        "updated_at": ("updatedat",),
        "targets": ("targets", "tasktargets"),
        "company_id": ("targetcompanyid", "companyid"),
        "person_id": ("targetpersonid", "personid"),
        "opportunity_id": ("targetopportunityid", "opportunityid"),
    },
    "notes": {
        "id": ("id",),
        "title": ("title",),
        "body": ("body", "bodyv2.markdown", "body.markdown", "text", "content"),
        "targets": ("targets", "notetargets"),
        "company_id": ("targetcompanyid", "companyid"),
        "person_id": ("targetpersonid", "personid"),
        "opportunity_id": ("targetopportunityid", "opportunityid"),
    },
    "attachments": {
        "id": ("id",),
        "name": ("name", "filename", "title"),
        "targets": ("targets",),
        "company_id": ("companyid", "targetcompanyid"),
        "person_id": ("personid", "targetpersonid"),
        "opportunity_id": ("opportunityid", "targetopportunityid"),
    },
}
#: Top-level keys of a JSON export, by object (contracts §2.9).
OBJECT_KEYS: Final = {
    "companies": "companies",
    "people": "people",
    "persons": "people",
    "opportunities": "opportunities",
    "deals": "opportunities",
    "tasks": "tasks",
    "notes": "notes",
    "attachments": "attachments",
    "campaigns": "campaigns",
    "campaignmembers": "campaign_members",
    "members": "campaign_members",
}

#: FR-CRM-MIG-02. The CRM's own keys are accepted too.
STAGE_MAP: Final = {
    "new": "discovery",
    "screening": "qualification",
    "proposal": "proposal",
    "negotiation": "negotiation",
    "customer": "closed_won",
    "lost": "closed_lost",
    **{s.replace("_", ""): s for s in STAGE_LABELS},
}
#: Q-55 — a company's own stage in the export, where it names a lead stage
#: that holds without deals (Deal open needs an open deal).
LEAD_STAGE_MAP: Final = {s.replace("_", ""): s for s in LEAD_STAGES if s != "deal_open"}
LEAD_STAGE_LABELS: Final = {
    "new": "New",
    "contacted": "Contacted",
    "nurturing": "Nurturing",
    "qualified": "Qualified",
    "deal_open": "Deal open",
    "customer": "Customer",
    "partner": "Partner",
    "disqualified": "Disqualified",
}
#: FR-CRM-MIG-05 — account types whose companies are partners.
PARTNER_TYPES: Final = ("technology_partner", "channel_partner", "training_partner")
#: Lead stages a deal may sit on (FR-CRM-PIPE-04).
OPENABLE: Final = ("qualified", "deal_open", "customer", "partner")
TASK_STATUS: Final = {"todo": "todo", "inprogress": "in_progress", "done": "done"}

_TWENTY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_EMAIL = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
_PHONE = re.compile(r"^[0-9][0-9 ()-]{3,19}$")
_COUNTRY_CODE = re.compile(r"^\+\d{1,4}$")


# ---------------------------------------------------------------------------
# Reading the declared shape
# ---------------------------------------------------------------------------


def _key(name: str) -> str:
    return re.sub(r"[\s_-]", "", name).lower()


def _flatten(item: dict[str, Any]) -> dict[str, Any]:
    """Every value by its normalised key, nested ones by a dotted path too."""
    out: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        out.setdefault(prefix, value)
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{_key(str(k))}" if prefix else _key(str(k)), v)

    for k, v in item.items():
        walk(_key(str(k)), v)
    return out


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value in ([], {})


def read(obj: str, item: dict[str, Any]) -> dict[str, Any]:
    """One row of `obj` in canonical field names; absent or blank → None."""
    flat = _flatten(item)
    row: dict[str, Any] = {}
    for name, aliases in FIELDS[obj].items():
        row[name] = next(
            (
                flat[a]
                for a in aliases
                if a in flat and not _blank(flat[a]) and not isinstance(flat[a], dict)
            ),
            None,
        )
        if isinstance(row[name], str):
            row[name] = row[name].strip()
    return row


def bundle_from_json(data: Any) -> dict[str, list[dict[str, Any]]]:
    """Contracts §2.9 JSON: `{"companies": […], "people": […], …}`, also
    accepted inside a top-level `data`. Raises ValueError with a sentence."""
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise ValueError("The file is not a Twenty export: it should hold companies, people…")
    bundle: dict[str, list[dict[str, Any]]] = {}
    for key, items in data.items():
        obj = OBJECT_KEYS.get(_key(str(key)))
        if obj is None:
            continue
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ValueError(f"“{key}” should be a list of records.")
        bundle.setdefault(obj, []).extend(items)
    if not bundle:
        raise ValueError("The file holds no companies, people, deals, tasks or notes.")
    return bundle


def object_named(name: str | None) -> str | None:
    """'Companies', 'companies.csv', 'deals' → the object, or None."""
    if not name:
        return None
    stem = re.sub(r"\.csv$", "", name.strip().rsplit("/", 1)[-1], flags=re.IGNORECASE)
    return OBJECT_KEYS.get(_key(stem))


# ---------------------------------------------------------------------------
# Rules — FR-CRM-MIG-02, -05
# ---------------------------------------------------------------------------


def stage_of(text: Any) -> str | None:
    """FR-CRM-MIG-02 — NEW→discovery … LOST→closed_lost; None if unknown."""
    return STAGE_MAP.get(_key(str(text))) if not _blank(text) else None


def file_lead_stage(text: Any) -> str | None:
    """Q-55 — the company's own stage in the export, if it is a lead stage."""
    return LEAD_STAGE_MAP.get(_key(str(text))) if not _blank(text) else None


def derive_lead_stage(
    account_type_key: str | None, deal_stages: list[str], own_stage: str | None
) -> tuple[str, str]:
    """FR-CRM-MIG-05, Q-55 — the lead stage of an imported company, first
    match wins, and which rule chose it."""
    if account_type_key in PARTNER_TYPES:
        return "partner", "partner account type"
    if any(s in OPEN for s in deal_stages):
        return "deal_open", "open deal"
    if "closed_won" in deal_stages:
        return "customer", "won deal"
    if deal_stages:
        return "nurturing", "only lost deals"
    if own_stage:
        return own_stage, "stage in the file"
    return "new", "no deals"


def owner_for(email: Any, members: dict[str, str], importer: str) -> tuple[str, bool]:
    """FR-CRM-MIG-05 — a member by email, else the importing Admin. The flag
    says an email was given that matched no member."""
    if _blank(email):
        return importer, False
    found = members.get(str(email).strip().lower())
    return (found, False) if found else (importer, True)


def missing_rate(currency: str, on: date, workspace: str) -> str:
    """Contracts §2.9 / data-model §3.9 — the apply waits for the rate."""
    return f"No {currency} rate for {on:%Y-%m} in {workspace}."


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _text(value: Any, limit: int) -> tuple[str | None, bool]:
    """Text cut to `limit` characters, and whether it was cut."""
    if _blank(value):
        return None, False
    text = str(value).strip()
    return text[:limit], len(text) > limit


def _instant(value: Any) -> datetime | None:
    if _blank(value):
        return None
    try:
        found = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def _timestamp(value: Any) -> str | None:
    found = _instant(value)
    return found.astimezone(UTC).isoformat() if found else None


def _day(value: Any) -> date | None:
    """A date, or the date of a timestamp in TVA's zone."""
    if _blank(value):
        return None
    text = str(value).strip()
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    found = _instant(text)
    return found.astimezone(ZONE).date() if found else None


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _list(value: Any) -> list[str]:
    """A list, a JSON list in a CSV cell, or text separated by ; or ,."""
    if _blank(value):
        return []
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if isinstance(value, list):
        return [str(v).strip() for v in value if not _blank(v)]
    return [part.strip() for part in re.split(r"[;,]", str(value)) if part.strip()]


def clean_email(value: Any) -> str | None:
    if _blank(value):
        return None
    email = dedupe.normalise_email(str(value))
    return email if _EMAIL.match(email) and len(email) <= 254 else None


def same(a: Any, b: Any) -> bool:
    """Whether a value read from the database equals one about to be written:
    numbers as decimals, timestamps as instants, so `1000` equals `1000.00`
    and `…Z` equals `…+00:00`."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (list, dict, bool)) or isinstance(b, (list, dict, bool)):
        return a == b
    sa, sb = str(a), str(b)
    if sa == sb:
        return True
    try:
        return Decimal(sa) == Decimal(sb)
    except InvalidOperation:
        pass
    ia, ib = _instant(sa), _instant(sb)
    return ia is not None and ib is not None and len(sa) > 10 and ia == ib


@dataclass(frozen=True)
class Ref:
    """A record this import creates, known by its Twenty id until the real
    run gives it a CRM id."""

    object: str
    twenty_id: str


@dataclass
class Write:
    """One row to write.

    `op` is `upsert` for a new row (matched on `twenty_id`, so a chunk that
    failed half-way re-runs safely), `insert` for a new deal (only an insert
    may set a deal's first stage, data-model §5.2) and `update` for a row
    already in the CRM, carrying only what changed. `values` may hold
    `Ref`s; `domains` and `links` are rows to add beside it.
    """

    object: str
    twenty_id: str
    op: str
    values: dict[str, Any]
    existing: dict[str, Any] | None = None
    domains: list[tuple[str, bool]] = field(default_factory=list)
    links: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class Facts:
    """What the workspace already holds, read by the service."""

    workspace: str
    importer: str
    #: member email (lower case) → user id.
    members: dict[str, str]
    options: list[dict[str, Any]]
    rates: list[dict[str, Any]]
    probabilities: dict[str, Any]
    #: object → twenty_id → row (Trash included).
    existing: dict[str, dict[str, dict[str, Any]]]
    #: company id → row: the survivors of merged-away companies.
    survivors: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: domain → {company_id, twenty_id, name, in_trash}.
    domains: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: existing company id → its domains [(domain, is_primary)].
    company_domains: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)
    #: primary email → {person_id, twenty_id, name, in_trash}.
    emails: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: existing task or note id → {(record_type, record_id)}.
    links: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    #: existing company id → its live deals [{twenty_id, stage}].
    company_deals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: 1b: whether the workspace has Entity (FR-CRM-WS-06).
    has_entity: bool = False
    #: 1b: the existing campaigns' members, (campaign id, company id, person
    #: id) → twenty_id (None for one made in the CRM).
    member_pairs: dict[tuple[str, str | None, str | None], str | None] = field(default_factory=dict)


@dataclass
class ImportPlan:
    counts: dict[str, dict[str, int]]
    lead_stages: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    attachments: list[dict[str, Any]]
    fixup_candidates: int
    fixups: int
    writes: dict[str, list[Write]]

    @property
    def blocking(self) -> list[str]:
        return [c["detail"] for c in self.conflicts if c["blocking"]]

    def report(self) -> dict[str, Any]:
        """The dry run's answer (contracts §2.9), without `run_id`."""
        return {
            "counts": self.counts,
            "lead_stages": self.lead_stages,
            "conflicts": self.conflicts,
            "attachments": self.attachments,
            "fixup_candidates": self.fixup_candidates,
            "blocking": self.blocking,
        }


def blocked_sentence(blocking: list[str]) -> str:
    """The real run's refusal while the dry run lists something to fix."""
    first = blocking[0].rstrip(".")
    more = f" (and {len(blocking) - 1} more; see the dry run)" if len(blocking) > 1 else ""
    return f"The import cannot run yet: {first}{more}."


def _type_object(text: Any) -> str | None:
    """A target's type ('company', 'Person', 'deal') → its object."""
    kind = _key(str(text or ""))
    return {
        "company": "companies",
        "companies": "companies",
        "person": "people",
        "people": "people",
        "persons": "people",
        "opportunity": "opportunities",
        "opportunities": "opportunities",
        "deal": "opportunities",
        "deals": "opportunities",
        "campaign": "campaigns",
        "campaigns": "campaigns",
    }.get(kind)


def _targets(row: dict[str, Any]) -> list[tuple[str, str]]:
    """Contracts §2.9 — `targets: [{type, id}]` (a JSON list in a CSV cell
    too), and one target id per object as columns."""
    raw = row.get("targets")
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    wanted: list[tuple[str, str]] = []
    for target in raw if isinstance(raw, list) else []:
        if not isinstance(target, dict):
            continue
        flat = _flatten(target)
        obj = _type_object(flat.get("type") or flat.get("recordtype") or flat.get("object"))
        tid = flat.get("id") or flat.get("recordid")
        if obj and not _blank(tid):
            wanted.append((obj, str(tid).strip()))
    for obj, column in (
        ("companies", "company_id"),
        ("people", "person_id"),
        ("opportunities", "opportunity_id"),
    ):
        if not _blank(row.get(column)):
            wanted.append((obj, str(row[column]).strip()))
    return list(dict.fromkeys(wanted))


def _tid(value: Any) -> str | None:
    if _blank(value):
        return None
    text = str(value).strip()
    return text if _TWENTY_ID.match(text) else None


class _Planner:
    def __init__(
        self, bundle: dict[str, list[dict[str, Any]]], facts: Facts, today: date, fixup: bool
    ) -> None:
        self.facts = facts
        self.today = today
        self.fixup = fixup
        self.rows = {obj: [read(obj, item) for item in bundle.get(obj, [])] for obj in OBJECTS}
        self.counts: dict[str, dict[str, int]] = {}
        for obj in OBJECTS:
            if obj in bundle and obj in TABLES:
                self.count(obj)
        self.conflicts: list[dict[str, Any]] = []
        self.writes: dict[str, list[Write]] = {obj: [] for obj in TABLES}
        self.lead_stages: list[dict[str, Any]] = []
        self.attachments: list[dict[str, Any]] = []
        self.fixup_candidates = 0
        self.fixups = 0
        #: (object, twenty_id) → name, of the rows this import writes.
        self.names: dict[tuple[str, str], str] = {}
        #: The Twenty ids this import writes, per object.
        self.written: dict[str, set[str]] = {obj: set() for obj in TABLES}
        #: Company Twenty id → the stage of each deal it will have.
        self.deal_stages: dict[str, list[str]] = {}

    # -- helpers --------------------------------------------------------------

    def count(self, obj: str) -> dict[str, int]:
        return self.counts.setdefault(
            obj,
            {
                "in_file": len(self.rows.get(obj, [])),
                "create": 0,
                "update": 0,
                "unchanged": 0,
                "skipped": 0,
            },
        )

    def conflict(self, obj: str, row: int, kind: str, detail: str, *, blocking: bool = False):
        self.conflicts.append(
            {"object": obj, "row": row, "kind": kind, "detail": detail, "blocking": blocking}
        )

    def option(self, list_key: str, text: Any) -> dict[str, Any] | None:
        """An active option of the list whose label or key the text names."""
        if _blank(text):
            return None
        wanted = _key(str(text))
        return next(
            (
                o
                for o in self.facts.options
                if o["list_key"] == list_key
                and o.get("is_active", True)
                and wanted in (_key(o["label"]), _key(o.get("system_key") or ""))
            ),
            None,
        )

    def option_key(self, option_id: str | None) -> str | None:
        found = next((o for o in self.facts.options if o["id"] == option_id), None)
        return found.get("system_key") if found else None

    def ref(self, obj: str, twenty_id: Any) -> str | Ref | None:
        """A record the file names by its Twenty id: its CRM id when it is in
        the CRM (a merged company's survivor), a `Ref` when this import
        creates it, None when it is in neither."""
        tid = _tid(twenty_id)
        if tid is None:
            return None
        found = self.facts.existing.get(obj, {}).get(tid)
        if found is not None:
            return found.get("merged_into_id") or found["id"]
        return Ref(obj, tid) if tid in self.written.get(obj, set()) else None

    def name_of(self, obj: str, target: str | Ref) -> str:
        if isinstance(target, Ref):
            return self.names.get((obj, target.twenty_id), target.twenty_id)
        rows = list(self.facts.existing.get(obj, {}).values())
        if obj == "companies":
            rows += list(self.facts.survivors.values())
        for row in rows:
            if row["id"] == target:
                return row.get("name") or dedupe.full_name(
                    row.get("first_name"), row.get("last_name")
                )
        return target

    def claim(self, obj: str, i: int, row: dict[str, Any], label: str) -> str | None:
        """The row's Twenty id, or None (and the reason) when it has none or
        repeats one."""
        tid = _tid(row["id"])
        if tid is None:
            self.conflict(obj, i, "invalid", f"{label} has no usable id, so it is not imported.")
        elif tid in self.written[obj]:
            self.conflict(
                obj, i, "invalid", f"{label} repeats id {tid}; only the first is imported."
            )
            tid = None
        if tid is None:
            self.count(obj)["skipped"] += 1
        return tid

    def skip(self, obj: str, i: int, kind: str, detail: str) -> None:
        self.conflict(obj, i, kind, detail)
        self.count(obj)["skipped"] += 1

    def owner(self, email: Any, existing: dict[str, Any] | None) -> tuple[str | None, bool]:
        """FR-CRM-MIG-05/07 — the importing Admin stands in only on a new
        record. A record already in the CRM keeps its owner unless the file
        names a member, so a re-run by another Admin, or after a reassignment
        in the CRM, changes nothing."""
        found, unknown = owner_for(email, self.facts.members, self.facts.importer)
        if existing is not None and (unknown or _blank(email)):
            return None, unknown
        return found, unknown

    def outcome(self, write: Write) -> None:
        """Counts the row as created, updated or unchanged, and keeps it to
        write unless it is unchanged. An update carries only what changed."""
        counts = self.count(write.object)
        if write.existing is None:
            counts["create"] += 1
            self.writes[write.object].append(write)
            return
        # A field the file leaves blank keeps what the CRM has: a re-import
        # refreshes records, it never empties what someone filled in since.
        changed = {
            k: v
            for k, v in write.values.items()
            if k != "twenty_id" and not _blank(v) and not same(write.existing.get(k), v)
        }
        if not (changed or write.domains or write.links):
            counts["unchanged"] += 1
            return
        counts["update"] += 1
        write.op, write.values = "update", changed
        self.writes[write.object].append(write)

    # -- companies, first pass: which ones this file writes -------------------

    def claim_companies(self) -> None:
        for i, row in enumerate(self.rows["companies"], start=1):
            name, cut = _text(row["name"], 200)
            tid = self.claim("companies", i, row, name or f"Company {i}")
            if tid is None:
                continue
            if name is None:
                self.skip(
                    "companies", i, "invalid", f"Company {i} has no name, so it is not imported."
                )
                continue
            existing = self.facts.existing["companies"].get(tid)
            if existing is not None and existing.get("merged_into_id"):
                survivor = self.facts.survivors.get(existing["merged_into_id"], {})
                self.conflict(
                    "companies",
                    i,
                    "merged",
                    f"{name} was merged into {survivor.get('name', 'another company')}; "
                    "what the file links to it goes there.",
                )
                self.count("companies")["unchanged"] += 1
                continue
            if cut:
                self.conflict(
                    "companies", i, "truncated", f"{name}'s name is cut to 200 characters."
                )
            self.names[("companies", tid)] = name
            self.written["companies"].add(tid)

    # -- people ---------------------------------------------------------------

    def people(self) -> None:
        taken = dict(self.facts.emails)
        for i, row in enumerate(self.rows["people"], start=1):
            email = clean_email(row["email"])
            first, _ = _text(row["first_name"], 100)
            last, _ = _text(row["last_name"], 100)
            if first is None and last:
                first, last = last, None
            if first is None and email:
                first = email.split("@")[0][:100]
            label = dedupe.full_name(first, last) or f"Person {i}"
            tid = self.claim("people", i, row, label)
            if tid is None:
                continue
            if first is None:
                self.skip(
                    "people", i, "invalid", f"Person {i} has no name, so they are not imported."
                )
                continue
            self.names[("people", tid)] = label
            self.written["people"].add(tid)
            existing = self.facts.existing["people"].get(tid)
            if existing is not None and existing.get("deleted_at"):
                self.conflict(
                    "people", i, "in_trash", f"{label} is in the Trash; restore them to see them."
                )
            if row["email"] and email is None:
                self.conflict(
                    "people",
                    i,
                    "invalid",
                    f"“{str(row['email'])[:80]}” is not an email; {label} is imported without it.",
                )
            extra = [e for e in (clean_email(x) for x in _list(row["additional_emails"])) if e]
            holder = taken.get(email) if email else None
            if holder and (existing is None or holder.get("person_id") != existing["id"]):
                where = " (in Trash)" if holder.get("in_trash") else ""
                self.conflict(
                    "people",
                    i,
                    "duplicate_email",
                    f"{email} is already the email of {holder['name']}{where}; "
                    f"it goes to {label}'s additional emails.",
                )
                extra.insert(0, email)
                email = None
            elif email:
                taken[email] = {
                    "person_id": existing["id"] if existing else None,
                    "name": label,
                    "in_trash": False,
                }
            phones = []
            if row["phone"]:
                number = str(row["phone"]).strip()
                code = str(row["phone_country"] or "+91").strip()
                code = code if code.startswith("+") else f"+{code}"
                if _PHONE.match(number) and _COUNTRY_CODE.match(code):
                    phones.append({"country_code": code, "number": number, "whatsapp": False})
                else:
                    self.conflict(
                        "people",
                        i,
                        "invalid",
                        f"“{number[:40]}” is not a phone number; {label} is imported without it.",
                    )
            owner = None
            if row["owner_email"]:
                owner, unknown = self.owner(row["owner_email"], existing)
                if unknown:
                    self.conflict(
                        "people",
                        i,
                        "unknown_owner",
                        f"{label}'s owner {row['owner_email']} is not a member; "
                        + ("they keep their owner." if existing else "you own them instead."),
                    )
            company = self.ref("companies", row["company_id"])
            if row["company_id"] and company is None:
                self.conflict(
                    "people",
                    i,
                    "missing_company",
                    f"{label}'s company is neither in the file nor in the CRM; "
                    "they are imported without one.",
                )
            values = {
                "twenty_id": tid,
                "first_name": first,
                "last_name": last,
                "primary_email": email,
                "additional_emails": list(dict.fromkeys(e for e in extra if e != email)),
                "phones": phones,
                "job_title": _text(row["job_title"], 200)[0],
                "city": _text(row["city"], 100)[0],
                "owner_id": owner,
                "company_id": company,
                "last_inbound_at": _timestamp(row["last_inbound_at"]),
                "last_outbound_at": _timestamp(row["last_outbound_at"]),
            }
            self.outcome(Write("people", tid, "upsert", values, existing))

    # -- deals ----------------------------------------------------------------

    def deals(self) -> None:
        lost_reasons = [o for o in self.facts.options if o["list_key"] == "opportunity.lost_reason"]
        other = next((o for o in lost_reasons if o.get("system_key") == "other"), None)
        other = other or (lost_reasons[0] if lost_reasons else None)
        for i, row in enumerate(self.rows["opportunities"], start=1):
            name, cut = _text(row["name"], 200)
            tid = self.claim("opportunities", i, row, name or f"Deal {i}")
            if tid is None:
                continue
            if name is None:
                self.skip(
                    "opportunities", i, "invalid", f"Deal {i} has no name, so it is not imported."
                )
                continue
            company = self.ref("companies", row["company_id"])
            if company is None:
                self.skip(
                    "opportunities",
                    i,
                    "missing_company",
                    f"{name}'s company is neither in the file nor in the CRM, so it is not imported.",
                )
                continue
            self.names[("opportunities", tid)] = name
            self.written["opportunities"].add(tid)
            if cut:
                self.conflict(
                    "opportunities", i, "truncated", f"{name}'s name is cut to 200 characters."
                )
            existing = self.facts.existing["opportunities"].get(tid)
            stage = stage_of(row["stage"])
            if stage is None:
                if row["stage"]:
                    self.conflict(
                        "opportunities",
                        i,
                        "unknown_stage",
                        f"{name}'s stage “{row['stage']}” is not known; "
                        "it is imported at Discovery.",
                    )
                stage = "discovery"
            if existing is not None and existing["stage"] != stage:
                self.conflict(
                    "opportunities",
                    i,
                    "stage_differs",
                    f"{name} is at {STAGE_LABELS[existing['stage']]} in the CRM and "
                    f"{STAGE_LABELS[stage]} in the file; a re-import does not move deals.",
                )
            current = existing["stage"] if existing else stage
            self.deal_stages.setdefault(str(row["company_id"]).strip(), []).append(current)

            owner, unknown = self.owner(row["owner_email"], existing)
            if unknown:
                self.conflict(
                    "opportunities",
                    i,
                    "unknown_owner",
                    f"{name}'s owner {row['owner_email']} is not a member; "
                    + ("it keeps its owner." if existing else "you own it instead."),
                )
            contact = self.ref("people", row["contact_id"])
            if row["contact_id"] and contact is None:
                self.conflict(
                    "opportunities",
                    i,
                    "missing_contact",
                    f"{name}'s point of contact is neither in the file nor in the CRM; "
                    "it is imported without one.",
                )
            values: dict[str, Any] = {
                "twenty_id": tid,
                "name": name,
                "company_id": company,
                "point_of_contact_id": contact,
                "owner_id": owner,
                "expected_close_on": _iso(_day(row["close_on"])),
                **self.amount(i, name, row),
            }
            # 1b: the deal's source campaign, as the file names it or as a
            # member converted to it says (attribution, FR-CRM-CAMP-04).
            source = self.ref("campaigns", row["campaign_id"] or self.sources.get(tid))
            if source is not None:
                values["source_campaign_id"] = source
            self.below_qualified(i, name, row["company_id"], company, current)

            step, _ = _text(row["next_step"], 500)
            step_on = _day(row["next_step_on"])
            if step and step_on:
                values.update(next_step=step, next_step_on=step_on.isoformat())
            elif existing is None and stage in OPEN:
                # Q-46 — every open deal has a next step (FR-CRM-PIPE-10).
                self.fixup_candidates += 1
                if self.fixup:
                    self.fixups += 1
                    values.update(next_step=FIXUP_NEXT_STEP, next_step_on=self.today.isoformat())
                else:
                    self.conflict(
                        "opportunities",
                        i,
                        "no_next_step",
                        f"{name} is open with no next step; add one in the file "
                        "or choose the fix-up.",
                        blocking=True,
                    )

            if existing is not None:
                self.outcome(Write("opportunities", tid, "update", values, existing))
                continue
            values["stage"] = stage
            values["probability"] = self.probability(row["probability"], stage)
            closed = _day(row["close_on"]) or _day(row["created_at"]) or self.today
            if stage == "closed_won":
                values["won_on"] = closed.isoformat()
            if stage == "closed_lost":
                values["lost_on"] = closed.isoformat()
                reason = self.option("opportunity.lost_reason", row["lost_reason"])
                if row["lost_reason"] and reason is None:
                    self.conflict(
                        "opportunities",
                        i,
                        "unknown_option",
                        f"{name}'s lost reason “{row['lost_reason']}” is not in this "
                        "workspace's list; it is imported as Other.",
                    )
                reason = reason or other
                if reason is None:
                    self.conflict(
                        "opportunities",
                        i,
                        "no_lost_reason",
                        "This workspace has no lost reasons; add one in Settings first.",
                        blocking=True,
                    )
                values["lost_reason_id"] = reason and reason["id"]
            self.outcome(Write("opportunities", tid, "insert", values))

    def probability(self, value: Any, stage: str) -> int:
        try:
            found = int(Decimal(str(value)))
        except (InvalidOperation, ValueError, TypeError):
            found = -1
        if 0 <= found <= 100:
            return found
        return default_probability(stage, self.facts.probabilities)

    def amount(
        self, i: int, name: str, row: dict[str, Any], obj: str = "opportunities"
    ) -> dict[str, Any]:
        """FR-CRM-FX-03 — the amount with its INR value at the rate for the
        month the deal (or campaign) was created; with no rate the import
        waits for one (data-model §3.9)."""
        if _blank(row["amount"]):
            return dict(fx.CLEARED)
        try:
            amount = Decimal(str(row["amount"]).replace(",", ""))
        except InvalidOperation:
            amount = Decimal(-1)
        if not amount.is_finite() or amount < 0 or amount >= Decimal("1e12"):
            self.conflict(
                obj,
                i,
                "invalid",
                f"{name}'s amount “{str(row['amount'])[:40]}” is not an amount; it is left out.",
            )
            return dict(fx.CLEARED)
        currency = str(row["currency"] or "").strip().upper()
        if not currency:
            self.conflict(obj, i, "no_currency", f"{name} has no currency; it is imported as INR.")
            currency = "INR"
        if currency not in fx.CURRENCIES:
            self.conflict(
                obj,
                i,
                "unknown_currency",
                f"{currency} is not a CRM currency (INR, AUD, USD); {name}'s amount is left out.",
            )
            return dict(fx.CLEARED)
        on = _day(row["created_at"]) or self.today
        found = fx.converted(self.facts.rates, amount, currency, on, self.facts.workspace)
        if isinstance(found, str):
            self.conflict(
                obj,
                i,
                "missing_fx_rate",
                missing_rate(currency, on, self.facts.workspace),
                blocking=True,
            )
            return {}
        return found

    def below_qualified(
        self, i: int, name: str, company_tid: Any, company: str | Ref, stage: str
    ) -> None:
        """FR-CRM-MIG-04 — an open deal on a CRM company below Qualified that
        the file does not bring; its lead stage then follows its deals."""
        tid = _tid(company_tid)
        if stage not in OPEN or isinstance(company, Ref) or tid in self.written["companies"]:
            return
        found = self.facts.existing["companies"].get(tid or "") or {}
        current = found.get("lead_stage")
        if current and current not in OPENABLE:
            self.conflict(
                "opportunities",
                i,
                "deal_below_qualified",
                f"{name} is open on {found.get('name')}, which is "
                f"{LEAD_STAGE_LABELS[current]}; its lead stage becomes Deal open.",
            )

    # -- companies, second pass: their values, once the deals are known -------

    def companies(self) -> None:
        source = next(
            (
                o["id"]
                for o in self.facts.options
                if o["list_key"] == "source" and o.get("system_key") == "import"
            ),
            None,
        )
        contact = self.people_contact()
        taken = dict(self.facts.domains)
        for i, row in enumerate(self.rows["companies"], start=1):
            tid = _tid(row["id"])
            name = self.names.get(("companies", tid or ""))
            if tid is None or name is None or tid in self._companies:
                continue
            self._companies.add(tid)
            existing = self.facts.existing["companies"].get(tid)
            if existing is not None and existing.get("deleted_at"):
                self.conflict(
                    "companies", i, "in_trash", f"{name} is in the Trash; restore it to see it."
                )
            account = self.option("company.account_type", row["account_type"])
            if row["account_type"] and account is None:
                self.conflict(
                    "companies",
                    i,
                    "unknown_option",
                    f"{name}'s account type “{row['account_type']}” is not in this workspace's "
                    "list; it is left empty.",
                )
            owner, unknown = self.owner(row["owner_email"], existing)
            if unknown:
                self.conflict(
                    "companies",
                    i,
                    "unknown_owner",
                    f"{name}'s owner {row['owner_email']} is not a member; "
                    + ("it keeps its owner." if existing else "you own it instead."),
                )
            own = file_lead_stage(row["stage"])
            stages = self.stages_of(tid, existing)
            derived, because = derive_lead_stage(account and account.get("system_key"), stages, own)
            self.lead_stages.append(
                {
                    "row": i,
                    "company": name,
                    "derived": derived,
                    "derived_label": LEAD_STAGE_LABELS[derived],
                    "because": because,
                }
            )
            if own in ("new", "contacted", "nurturing", "disqualified") and any(
                s in OPEN for s in stages
            ):
                self.conflict(
                    "companies",
                    i,
                    "deal_below_qualified",
                    f"{name} is {LEAD_STAGE_LABELS[own]} in the file but has open deals; "
                    "it is imported as Deal open.",
                )
            values: dict[str, Any] = {
                "twenty_id": tid,
                "name": name,
                "lead_stage": derived,
                "owner_id": owner,
                "account_type_id": account["id"] if account else None,
                "city": _text(row["city"], 100)[0],
                "country": _text(row["country"], 100)[0],
                "description": _text(row["description"], 20_000)[0],
            }
            if existing is None and source:
                values["source_id"] = source
            inbound, outbound = contact.get(tid, (None, None))
            if inbound:
                values["last_inbound_at"] = inbound
            if outbound:
                values["last_outbound_at"] = outbound
            domains: list[tuple[str, bool]] = []
            if row["domain"]:
                domain = dedupe.normalise_domain(str(row["domain"]))
                current = self.facts.company_domains.get(existing["id"], []) if existing else []
                holder = taken.get(domain) if domain else None
                if domain is None:
                    self.conflict(
                        "companies",
                        i,
                        "invalid",
                        f"“{str(row['domain'])[:80]}” is not a domain; "
                        f"{name} is imported without it.",
                    )
                elif holder and (existing is None or holder.get("company_id") != existing["id"]):
                    where = " (in Trash)" if holder.get("in_trash") else ""
                    self.conflict(
                        "companies",
                        i,
                        "duplicate_domain",
                        f"{domain} already belongs to {holder['name']}{where}; "
                        f"{name} is imported without it.",
                    )
                elif all(d != domain for d, _ in current):
                    domains.append((domain, not any(primary for _, primary in current)))
                    taken[domain] = {"company_id": None, "name": name, "in_trash": False}
            self.outcome(Write("companies", tid, "upsert", values, existing, domains=domains))

    def stages_of(self, tid: str, existing: dict[str, Any] | None) -> list[str]:
        """The stages of every deal the company will have: this file's, and
        its other deals in the CRM."""
        stages = list(self.deal_stages.get(tid, []))
        if existing is not None:
            for deal in self.facts.company_deals.get(existing["id"], []):
                if deal.get("twenty_id") not in self.written["opportunities"]:
                    stages.append(deal["stage"])
        return stages

    def deal_companies(self) -> None:
        """Companies already in the CRM that this file's deals land on: their
        lead stage follows their deals as well (FR-CRM-MIG-05)."""
        for tid in self.deal_stages:
            existing = self.facts.existing["companies"].get(tid)
            if (
                tid in self.written["companies"]
                or existing is None
                or existing.get("merged_into_id")
            ):
                continue
            derived, _ = derive_lead_stage(
                self.option_key(existing.get("account_type_id")),
                self.stages_of(tid, existing),
                None,
            )
            if derived != existing["lead_stage"]:
                self.outcome(Write("companies", tid, "update", {"lead_stage": derived}, existing))

    def people_contact(self) -> dict[str, tuple[str | None, str | None]]:
        """Company Twenty id → the latest inbound and outbound of its people
        in the file (FR-CRM-MIG-02)."""
        out: dict[str, tuple[str | None, str | None]] = {}
        for row in self.rows["people"]:
            company = _tid(row["company_id"])
            if company is None:
                continue
            latest = list(out.get(company, (None, None)))
            for slot, value in enumerate((row["last_inbound_at"], row["last_outbound_at"])):
                found, current = _instant(value), _instant(latest[slot])
                if found and (current is None or found > current):
                    latest[slot] = _timestamp(value)
            out[company] = (latest[0], latest[1])
        return out

    # -- tasks and notes ------------------------------------------------------

    def links(self, obj: str, i: int, label: str, row: dict[str, Any]) -> list[tuple[str, Any]]:
        found: list[tuple[str, Any]] = []
        for target_obj, tid in _targets(row):
            target = self.ref(target_obj, tid)
            if target is None:
                self.conflict(
                    obj,
                    i,
                    "missing_target",
                    f"{label} names a {NOUNS[target_obj]} that is neither in the file nor in "
                    "the CRM; that link is left out.",
                )
            elif (RECORD_TYPES[target_obj], target) not in found:
                found.append((RECORD_TYPES[target_obj], target))
        return found

    def tasks(self) -> None:
        for i, row in enumerate(self.rows["tasks"], start=1):
            title, cut = _text(row["title"], 200)
            label = f"Task “{title}”" if title else f"Task {i}"
            tid = self.claim("tasks", i, row, label)
            if tid is None:
                continue
            if title is None:
                self.skip("tasks", i, "invalid", f"Task {i} has no title, so it is not imported.")
                continue
            if cut:
                self.conflict("tasks", i, "truncated", f"{label}'s title is cut to 200 characters.")
            links = self.links("tasks", i, label, row)
            if not links:
                # FR-CRM-TASK-01: a task links to at least one record.
                self.skip(
                    "tasks",
                    i,
                    "no_link",
                    f"{label} is linked to nothing in the file or the CRM, so it is not imported.",
                )
                continue
            self.written["tasks"].add(tid)
            existing = self.facts.existing["tasks"].get(tid)
            status = TASK_STATUS.get(_key(str(row["status"] or "todo")), "todo")
            assignee, unknown = self.owner(row["assignee_email"], existing)
            if unknown:
                self.conflict(
                    "tasks",
                    i,
                    "unknown_owner",
                    f"{label}'s assignee {row['assignee_email']} is not a member; "
                    + ("it keeps its assignee." if existing else "it is yours instead."),
                )
            values: dict[str, Any] = {
                "twenty_id": tid,
                "title": title,
                "body": _text(row["body"], 20_000)[0],
                "due_on": _iso(_day(row["due_on"])),
                "assignee_id": assignee,
            }
            # Like a deal's stage, a task's status moves in the CRM: a re-run
            # sets it on a new task only, never reopens or closes one.
            if existing is None:
                values.update(status=status, completed_at=None)
                if status == "done":
                    done = _timestamp(row["completed_at"]) or _timestamp(row["updated_at"])
                    values["completed_at"] = done or f"{self.today.isoformat()}T00:00:00+00:00"
            self.with_links("tasks", tid, values, existing, links)

    def notes(self) -> None:
        for i, row in enumerate(self.rows["notes"], start=1):
            title, _ = _text(row["title"], 20_000)
            body, _ = _text(row["body"], 20_000)
            text = "\n\n".join(part for part in (title, body) if part)
            label = f"Note “{text[:40]}”" if text else f"Note {i}"
            tid = self.claim("notes", i, row, label)
            if tid is None:
                continue
            if not text:
                self.skip("notes", i, "invalid", f"Note {i} is empty, so it is not imported.")
                continue
            if len(text) > 20_000:
                self.conflict("notes", i, "truncated", f"{label} is cut to 20,000 characters.")
                text = text[:20_000]
            links = self.links("notes", i, label, row)
            if not links:
                self.skip(
                    "notes",
                    i,
                    "no_link",
                    f"{label} is linked to nothing in the file or the CRM, so it is not imported.",
                )
                continue
            self.written["notes"].add(tid)
            existing = self.facts.existing["notes"].get(tid)
            self.with_links("notes", tid, {"twenty_id": tid, "body": text}, existing, links)

    def with_links(
        self,
        obj: str,
        tid: str,
        values: dict[str, Any],
        existing: dict[str, Any] | None,
        links: list[tuple[str, Any]],
    ) -> None:
        have = self.facts.links.get(existing["id"], set()) if existing else set()
        missing = [(t, r) for t, r in links if isinstance(r, Ref) or (t, r) not in have]
        self.outcome(Write(obj, tid, "upsert", values, existing, links=missing))

    # -- attachments ----------------------------------------------------------

    def attachment_list(self) -> None:
        """FR-CRM-MIG-03 — listed for re-linking from Drive, never fetched."""
        for i, row in enumerate(self.rows["attachments"], start=1):
            name = _text(row["name"], 200)[0] or f"Attachment {i}"
            for record_type, target in self.links("attachments", i, f"“{name}”", row) or [
                (None, None)
            ]:
                obj = next((o for o, t in RECORD_TYPES.items() if t == record_type), None)
                self.attachments.append(
                    {
                        "name": name,
                        "record_type": record_type,
                        "record": self.name_of(obj, target) if obj and target else None,
                        "record_id": target if isinstance(target, str) else None,
                    }
                )

    # -- campaigns and their members (1b) --------------------------------------

    def claim_campaigns(self) -> None:
        """First pass: which campaigns this file writes, so a deal can name
        one as its source."""
        for i, row in enumerate(self.rows["campaigns"], start=1):
            name, cut = _text(row["name"], 200)
            tid = self.claim("campaigns", i, row, name or f"Campaign {i}")
            if tid is None:
                continue
            if name is None:
                self.skip(
                    "campaigns", i, "invalid", f"Campaign {i} has no name, so it is not imported."
                )
                continue
            if cut:
                self.conflict(
                    "campaigns", i, "truncated", f"{name}'s name is cut to 200 characters."
                )
            self.names[("campaigns", tid)] = name
            self.written["campaigns"].add(tid)

    def member_sources(self) -> dict[str, str]:
        """Deal Twenty id → the campaign a member converted to it from: the
        first such member wins."""
        out: dict[str, str] = {}
        for row in self.rows["campaign_members"]:
            deal, campaign = _tid(row["converted_opportunity_id"]), _tid(row["campaign_id"])
            if deal and campaign:
                out.setdefault(deal, campaign)
        return out

    def campaigns(self) -> None:
        """Spec §6.6: the campaign's fields; its budget converted to INR at
        the rate for the month it was created (FR-CRM-FX-03), the import
        waiting for a missing rate as a deal's does."""
        default_status = next(
            (
                o
                for o in self.facts.options
                if o["list_key"] == "campaign.status" and o.get("is_default")
            ),
            None,
        )
        for i, row in enumerate(self.rows["campaigns"], start=1):
            tid = _tid(row["id"])
            name = self.names.get(("campaigns", tid or ""))
            if tid is None or name is None:
                continue
            existing = self.facts.existing["campaigns"].get(tid)
            values: dict[str, Any] = {"twenty_id": tid, "name": name}
            for column, list_key, text in (
                ("type_id", "campaign.type", row["type"]),
                ("status_id", "campaign.status", row["status"]),
                ("channel_id", "channel", row["channel"]),
            ):
                found = self.option(list_key, text)
                if text and found is None:
                    self.conflict(
                        "campaigns",
                        i,
                        "unknown_option",
                        f"{name}'s {list_key.split('.')[-1]} “{str(text)[:60]}” is not in this "
                        "workspace's list; it is left empty.",
                    )
                values[column] = found["id"] if found else None
            if values["status_id"] is None and existing is None and default_status:
                values["status_id"] = default_status["id"]
            entity = {"nunnarilabs": "nunnari_labs", "deepweaver": "deepweaver"}.get(
                _key(str(row["entity"] or ""))
            )
            if row["entity"] and (entity is None or not self.facts.has_entity):
                self.conflict(
                    "campaigns",
                    i,
                    "invalid",
                    f"{name}'s entity “{str(row['entity'])[:40]}” is not used here; it is left out.",
                )
                entity = None
            values["entity"] = entity
            owner, unknown = self.owner(row["owner_email"], existing)
            if unknown:
                self.conflict(
                    "campaigns",
                    i,
                    "unknown_owner",
                    f"{name}'s owner {row['owner_email']} is not a member; "
                    + ("it keeps its owner." if existing else "you own it instead."),
                )
            values["owner_id"] = owner
            starts, ends = _day(row["starts_on"]), _day(row["ends_on"])
            if starts and ends and ends < starts:
                self.conflict(
                    "campaigns",
                    i,
                    "invalid",
                    f"{name} ends before it starts; its end date is left out.",
                )
                ends = None
            values.update(
                starts_on=_iso(starts),
                ends_on=_iso(ends),
                goal=_text(row["goal"], 20_000)[0],
                **self.budget(i, name, row),
            )
            self.outcome(Write("campaigns", tid, "upsert", values, existing))

    def budget(self, i: int, name: str, row: dict[str, Any]) -> dict[str, Any]:
        """A campaign's budget, as a deal's amount (`amount`)."""
        found = self.amount(
            i,
            name,
            {
                "amount": row["budget"],
                "currency": row["budget_currency"],
                "created_at": row["created_at"],
            },
            obj="campaigns",
        )
        if not found:
            return {}
        return {
            "budget": found["amount"],
            "budget_currency": found["currency"],
            "budget_in_inr": found["amount_in_inr"],
            "budget_fx_rate": found["fx_rate_used"],
        }

    def campaign_members(self) -> None:
        statuses = {s.replace("_", ""): s for s in MEMBER_STATUSES}
        seen = dict(self.facts.member_pairs)
        people = self.facts.existing["people"]
        for i, row in enumerate(self.rows["campaign_members"], start=1):
            label = f"Member {i}"
            tid = self.claim("campaign_members", i, row, label)
            if tid is None:
                continue
            campaign = self.ref("campaigns", row["campaign_id"])
            if campaign is None:
                self.skip(
                    "campaign_members",
                    i,
                    "missing_campaign",
                    f"{label}'s campaign is neither in the file nor in the CRM, so it is not "
                    "imported.",
                )
                continue
            company = self.ref("companies", row["company_id"])
            person = self.ref("people", row["person_id"])
            for obj, raw, found in (
                ("companies", row["company_id"], company),
                ("people", row["person_id"], person),
            ):
                if raw and found is None:
                    self.conflict(
                        "campaign_members",
                        i,
                        "missing_target",
                        f"{label} names a {NOUNS[obj]} that is neither in the file nor in "
                        "the CRM; that part is left out.",
                    )
            if company is None and person is None:
                self.skip(
                    "campaign_members",
                    i,
                    "no_link",
                    f"{label} names no company or person, so it is not imported.",
                )
                continue
            existing = self.facts.existing["campaign_members"].get(tid)
            who = " ".join(
                part
                for part in (
                    self.name_of("people", person) if person else None,
                    self.name_of("companies", company) if company else None,
                )
                if part
            )
            label = f"{who} on {self.name_of('campaigns', campaign)}"
            person_row = next((p for p in people.values() if person and p["id"] == person), None)
            if existing is None and person_row and person_row.get("do_not_contact"):
                # FR-CRM-PER-02: never added; earlier memberships keep their
                # history.
                self.skip(
                    "campaign_members",
                    i,
                    "do_not_contact",
                    f"{label}: they are marked Do not contact, so they are not added.",
                )
                continue
            pair = (self._key_of(campaign), self._key_of(company), self._key_of(person))
            if pair in seen and seen[pair] != tid:
                self.skip(
                    "campaign_members",
                    i,
                    "duplicate",
                    f"{label} is already a member of that campaign; this row is not imported.",
                )
                continue
            seen[pair] = tid
            self.written["campaign_members"].add(tid)
            status = statuses.get(_key(str(row["status"] or "")), None)
            if row["status"] and status is None:
                self.conflict(
                    "campaign_members",
                    i,
                    "unknown_status",
                    f"{label}'s status “{str(row['status'])[:40]}” is not known; it is "
                    "imported as Targeted.",
                )
            deal = self.ref("opportunities", row["converted_opportunity_id"])
            if row["converted_opportunity_id"] and deal is None:
                self.conflict(
                    "campaign_members",
                    i,
                    "missing_target",
                    f"{label}'s converted deal is neither in the file nor in the CRM.",
                )
            if deal is not None:
                status = "converted"
            elif status == "converted":
                self.conflict(
                    "campaign_members",
                    i,
                    "no_deal",
                    f"{label} is Converted with no deal in the file; it is imported as Engaged.",
                )
                status = "engaged"
            values: dict[str, Any] = {
                "twenty_id": tid,
                "campaign_id": campaign,
                "company_id": company,
                "person_id": person,
                "last_touch_on": _iso(_day(row["last_touch_on"])),
                "notes": _text(row["notes"], 20_000)[0],
                "converted_opportunity_id": deal,
                "position": i,
            }
            # Like a deal's stage, a member's status moves in the CRM: a
            # re-run sets it on a new member, or converts one the file says
            # converted.
            if existing is None or deal is not None:
                values["status"] = status or "targeted"
            self.outcome(Write("campaign_members", tid, "upsert", values, existing))

    @staticmethod
    def _key_of(value: str | Ref | None) -> str | None:
        """A record as a key: its CRM id, or `obj:twenty_id` for one this
        import creates."""
        if isinstance(value, Ref):
            return f"{value.object}:{value.twenty_id}"
        return value

    def run(self) -> ImportPlan:
        self._companies: set[str] = set()
        self.claim_companies()
        self.people()
        self.claim_campaigns()
        self.sources = self.member_sources()
        self.deals()
        self.companies()
        self.deal_companies()
        self.campaigns()
        self.campaign_members()
        self.tasks()
        self.notes()
        self.attachment_list()
        # The report reads object by object, row by row.
        self.conflicts.sort(key=lambda c: (OBJECTS.index(c["object"]), c["row"]))
        return ImportPlan(
            counts=self.counts,
            lead_stages=self.lead_stages,
            conflicts=self.conflicts,
            attachments=self.attachments,
            fixup_candidates=self.fixup_candidates,
            fixups=self.fixups,
            writes=self.writes,
        )


def plan(
    bundle: dict[str, list[dict[str, Any]]], facts: Facts, *, today: date, fixup: bool
) -> ImportPlan:
    """FR-CRM-MIG-01 to -07 — everything a dry run reports and a real run
    writes."""
    return _Planner(bundle, facts, today, fixup).run()


def twenty_ids(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    """Every Twenty id the file names, per object: its own rows' and the
    records they point at, so the service reads only those."""
    out: dict[str, set[str]] = {obj: set() for obj in TABLES}

    def add(obj: str, value: Any) -> None:
        tid = _tid(value)
        if tid is not None:
            out[obj].add(tid)

    for obj in OBJECTS:
        for item in bundle.get(obj, []):
            row = read(obj, item)
            if obj in TABLES:
                add(obj, row["id"])
            add("companies", row.get("company_id"))
            add("people", row.get("contact_id"))
            # 1b: a member's person, campaign and deal; a deal's campaign.
            if obj in ("campaign_members", "opportunities"):
                add("campaigns", row.get("campaign_id"))
            if obj == "campaign_members":
                add("people", row.get("person_id"))
                add("opportunities", row.get("converted_opportunity_id"))
            for target_obj, tid in (
                _targets(row) if obj in ("tasks", "notes", "attachments") else []
            ):
                add(target_obj, tid)
    return out


def new_names(plan_: ImportPlan) -> dict[str, list[tuple[str, str]]]:
    """The companies and people this import creates, (twenty_id, name), for
    the possible-duplicate check (FR-CRM-DUP-02)."""
    out: dict[str, list[tuple[str, str]]] = {"companies": [], "people": []}
    for obj, found in out.items():
        for write in plan_.writes.get(obj, []):
            if write.existing is None:
                found.append(
                    (
                        write.twenty_id,
                        write.values.get("name")
                        or dedupe.full_name(
                            write.values.get("first_name"), write.values.get("last_name")
                        ),
                    )
                )
    return out


def similar_sentence(name: str, matches: list[dict[str, Any]]) -> str:
    """FR-CRM-DUP-02 in the dry run: shown, never refused."""
    named = ", ".join(f"{m['name']} ({round(float(m['similarity']) * 100)}%)" for m in matches[:3])
    return f"{name} looks like {named} in the CRM; merge them later if they are the same."
