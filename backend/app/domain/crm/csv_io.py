"""CSV import and export — spec 007 FR-CRM-IO-01 to -05, FR-CRM-DUP-02;
contracts §2.9 (CSV), §2.10 (export); plan §5.12.

Import (Admins, one object per file: companies, people or deals):

- A mapping says which CSV column fills which field: by header, `{column:
  field}`, a column mapped to nothing is left out. `default_mapping` maps a
  header that names a field (its key or its label, as an export writes it),
  so a file exported from a view maps itself back (quickstart §3 S11).
- Each row's cells are read here into the request fields the record routes
  take (contracts §2.5.1): an option by its label, a lead stage, entity or
  category by its label or key, an owner by member email or name, a
  company by name or domain, a person by email or name (FR-CRM-IO-01
  "relations by name, domain or email"). A cell that cannot be read is an
  error on its row, named in one sentence, fixable in the web app before
  anything is written.
- An empty cell is not a value: a new record goes without the field, an
  existing one keeps it (as the Twenty importer, contracts §2.9).
- What a row does: `create`, `update` the record it matches (by the `id`
  column, a company's domain or a person's email), or `skip`
  (FR-CRM-IO-03). `mode` says what a matching row does; `actions` choose
  per row.

Export: a cell is the value as the web app shows it (labels, names), and a
cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a
leading `'`, so a spreadsheet never runs it as a formula (OWASP CSV
injection; names arrive from outside through Gmail sync, plan §5.12).

Pure.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from app.domain.crm import dedupe, objects

#: FR-CRM-IO-02: what the proxy carries.
MAX_BYTES: Final = 4 * 1024 * 1024
#: Rows per file: an import is written in one request, behind a proxy with
#: a time limit; a bigger list goes in several files.
MAX_ROWS: Final = 5000
#: Contracts §2.10: an export larger than this is refused (Netlify's
#: response limit is about 6 MB).
EXPORT_MAX_BYTES: Final = 5 * 1024 * 1024
TOO_BIG_EXPORT: Final = "This export is larger than 5 MB; export one object, or a narrower view."
#: OWASP: a cell starting with one of these is a formula to a spreadsheet.
FORMULA_STARTS: Final = ("=", "+", "-", "@", "\t", "\r")
#: Several values in one cell (channels, domains, emails).
SEPARATOR: Final = ";"

Mode = Literal["skip", "update", "create"]
MODES: Final = ("skip", "update", "create")

#: How a cell is read.
Kind = Literal[
    "id",
    "text",
    "long_text",
    "full_name",
    "date",
    "probability",
    "money",
    "currency",
    "boolean",
    "option",
    "options",
    "lead_stage",
    "entity",
    "entities",
    "category",
    "member",
    "company",
    "person",
    "domain",
    "domains",
    "email",
    "emails",
    "url",
]


@dataclass(frozen=True)
class Column:
    """A field a column can fill."""

    field: str
    kind: Kind
    list_key: str | None = None
    aliases: tuple[str, ...] = ()


#: The fields each object imports. A deal's stage moves only through a
#: stage move (contracts §2.6), and every imported deal opens at Discovery;
#: system-written fields are never imported (FR-CRM-CO-03).
COLUMNS: Final[dict[str, tuple[Column, ...]]] = {
    "company": (
        Column("id", "id", aliases=("record id",)),
        Column("name", "text", aliases=("company", "company name")),
        Column("domain", "domain", aliases=("primary domain",)),
        Column("additional_domains", "domains"),
        Column("entities", "entities"),
        Column("lead_stage", "lead_stage"),
        Column("account_type_id", "option", "company.account_type"),
        Column("relationship_status_id", "option", "company.relationship_status"),
        Column("channel_ids", "options", "channel", aliases=("channel",)),
        Column("owner_id", "member", aliases=("owner email",)),
        Column("next_touch_on", "date"),
        Column("industry_id", "option", "company.industry"),
        Column("institution_type_id", "option", "company.institution_type"),
        Column("size_id", "option", "company.size"),
        Column("country", "text"),
        Column("city", "text"),
        Column("website", "url"),
        Column("linkedin", "url"),
        Column("description", "long_text"),
        Column("source_id", "option", "source"),
    ),
    "person": (
        Column("id", "id", aliases=("record id",)),
        Column("name", "full_name", aliases=("full name",)),
        Column("first_name", "text"),
        Column("last_name", "text"),
        Column("company_id", "company", aliases=("company name",)),
        Column("primary_email", "email", aliases=("email address", "primary email")),
        Column("additional_emails", "emails"),
        Column("job_title", "text", aliases=("title",)),
        Column("city", "text"),
        Column("linkedin", "url"),
        Column("owner_id", "member", aliases=("owner email",)),
        Column("do_not_contact", "boolean"),
    ),
    "opportunity": (
        Column("id", "id", aliases=("record id",)),
        Column("name", "text", aliases=("deal", "deal name", "opportunity")),
        Column("company_id", "company", aliases=("company name",)),
        Column("point_of_contact_id", "person", aliases=("contact",)),
        Column("entity", "entity"),
        Column("amount", "money"),
        Column("currency", "currency"),
        Column("probability", "probability"),
        Column("expected_close_on", "date", aliases=("close date", "expected close")),
        Column("next_step", "text"),
        Column("next_step_on", "date"),
        Column("owner_id", "member", aliases=("owner email",)),
        Column("channel_id", "option", "channel"),
        Column("deal_type_id", "option", "opportunity.deal_type"),
        Column("project_category", "category"),
        Column("engagement_model_id", "option", "opportunity.engagement_model"),
        Column("programme_id", "option", "opportunity.programme"),
        Column("proposal_sent_on", "date"),
        Column("lost_notes", "long_text"),
    ),
}
#: Plural object names (a file's, a route's) → the record type.
OBJECT_NAMES: Final = {
    "company": "company",
    "companies": "company",
    "person": "person",
    "people": "person",
    "contacts": "person",
    "opportunity": "opportunity",
    "opportunities": "opportunity",
    "deals": "opportunity",
}
PLURAL: Final = {"company": "companies", "person": "people", "opportunity": "opportunities"}
NOUN: Final = {"company": "company", "person": "person", "opportunity": "deal"}


def object_named(name: str | None) -> str | None:
    """'companies', 'People.csv', 'deals' → the record type, or None."""
    if not name:
        return None
    stem = re.sub(r"\.csv$", "", name.strip().lower())
    stem = re.split(r"[^a-z]+", stem)[0] if stem else stem
    return OBJECT_NAMES.get(stem)


def column(kind: str, name: str) -> Column | None:
    return next((c for c in COLUMNS.get(kind, ()) if c.field == name), None)


def label(kind: str, name: str) -> str:
    """A field's header: "ID", or the label the web app shows."""
    if name == "id":
        return "ID"
    return objects.label(kind, name)


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.strip().casefold())


def default_mapping(kind: str, headers: list[str]) -> dict[str, str | None]:
    """Each header to the field it names (by key, label or a common alias),
    each field once; any other header to nothing."""
    names: dict[str, str] = {}
    for c in COLUMNS[kind]:
        for text in (c.field, label(kind, c.field), *c.aliases):
            names.setdefault(_norm(text), c.field)
    used: set[str] = set()
    out: dict[str, str | None] = {}
    for header in headers:
        found = names.get(_norm(header))
        out[header] = found if found and found not in used else None
        if found:
            used.add(found)
    return out


def mapping_refusal(
    kind: str,
    mapping: dict[str, str | None],
    headers: list[str] | None,
    *,
    has_entity: bool,
    tva_linked: bool,
    workspace: str,
) -> str | None:
    """A mapping is of this object's fields, each at most once, and of the
    file's own columns. It names the record: a name (or, for a person, a
    first name) or the `id` of the record to update."""
    seen: dict[str, str] = {}
    for header, name in mapping.items():
        if headers is not None and header not in headers:
            return f"The file has no column “{header[:80]}”."
        if name is None:
            continue
        found = column(kind, name)
        if found is None:
            return f"{name[:60]} is not a field a {NOUN[kind]} imports."
        spec = objects.field(kind, name)
        if spec is not None and spec.scope is not None:
            if (spec.scope == "labs" and not has_entity) or (
                spec.scope == "academy" and tva_linked
            ):
                return f"{spec.label} is not used in {workspace}."
        if name in seen:
            return f"“{seen[name][:80]}” and “{header[:80]}” both fill {label(kind, name)}."
        seen[name] = header
    named = {"company": ("name", "id"), "person": ("first_name", "name", "id")}.get(
        kind, ("name", "id")
    )
    if not any(n in seen for n in named):
        need = {"company": "Name", "person": "First name or Name", "opportunity": "Name"}[kind]
        return f"Map a column to {need} (or to ID, to update records)."
    return None


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


def parse(text: str) -> tuple[list[str], list[list[str]]]:
    """The header and the rows; blank lines dropped. ValueError with a
    sentence when the file is not a CSV this can read."""
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError(f"The CSV could not be read: {exc}.") from None
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError("The CSV has no header row.")
    headers = [h.strip() for h in rows[0]]
    if any(not h for h in headers):
        raise ValueError("Every column of the CSV needs a header.")
    if len(set(headers)) != len(headers):
        dup = next(h for h in headers if headers.count(h) > 1)
        raise ValueError(f"The CSV has two columns called “{dup[:80]}”.")
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise ValueError(f"The file has {len(body)} rows; import at most {MAX_ROWS} at a time.")
    for n, row in enumerate(body, start=1):
        if len(row) > len(headers):
            raise ValueError(f"Row {n} has more cells than the header has columns.")
    return headers, [r + [""] * (len(headers) - len(r)) for r in body]


@dataclass
class Lookups:
    """What reading a row may look up in the workspace."""

    #: list_key → casefolded label or id → option row.
    options: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    #: casefolded label or key → lead stage key.
    lead_stages: dict[str, str] = field(default_factory=dict)
    #: member email or display name, casefolded → their ids.
    members: dict[str, list[str]] = field(default_factory=dict)
    member_ids: set[str] = field(default_factory=set)
    #: live companies: casefolded name → ids; domain → id; ids.
    company_names: dict[str, list[str]] = field(default_factory=dict)
    company_domains: dict[str, str] = field(default_factory=dict)
    company_ids: set[str] = field(default_factory=set)
    #: live people: email → id; casefolded full name → ids; ids.
    person_emails: dict[str, str] = field(default_factory=dict)
    person_names: dict[str, list[str]] = field(default_factory=dict)
    person_ids: set[str] = field(default_factory=set)


@dataclass
class Read:
    """A row read into request fields, with what went wrong."""

    values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


_DATE_FORMATS: Final = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%d-%m-%Y")


def read_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split(SEPARATOR) if p.strip()]


def _one(names: dict[str, list[str]], text: str) -> list[str]:
    return names.get(text.casefold().strip(), [])


def read_cell(kind: str, col: Column, text: str, look: Lookups) -> tuple[Any, str | None]:
    """One non-empty cell → (value, None) or (None, the sentence why not)."""
    what = label(kind, col.field)
    quoted = f"“{text[:80]}”"
    if col.kind in ("text", "long_text"):
        return text, None
    if col.kind == "id":
        return (text, None) if is_uuid(text) else (None, f"{quoted} is not a record ID.")
    if col.kind == "full_name":
        first, _, last = text.partition(" ")
        return {"first_name": first.strip(), **({"last_name": last.strip()} if last else {})}, None
    if col.kind == "date":
        day = read_date(text)
        if day is None:
            return None, f"{what}: {quoted} is not a date; use YYYY-MM-DD."
        return day.isoformat(), None
    if col.kind == "probability":
        try:
            number = Decimal(text.rstrip("%").strip())
        except InvalidOperation:
            number = Decimal(-1)
        if number != number.to_integral_value() or not 0 <= number <= 100:
            return None, f"{what}: {quoted} is not a whole number from 0 to 100."
        return int(number), None
    if col.kind == "money":
        cleaned = re.sub(r"[,\s₹$]", "", text)
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            return None, f"{what}: {quoted} is not an amount."
        if amount < 0 or amount != amount.quantize(Decimal("0.01")):
            return None, f"{what}: {quoted} is not an amount with at most two decimals."
        return str(amount.quantize(Decimal("0.01"))), None
    if col.kind == "currency":
        code = text.upper()
        if code not in objects.CURRENCIES:
            return None, f"{what}: {quoted} is not INR, AUD or USD."
        return code, None
    if col.kind == "boolean":
        found = {"yes": True, "y": True, "true": True, "1": True}.get(text.casefold())
        if found is None and text.casefold() in ("no", "n", "false", "0"):
            found = False
        if found is None:
            return None, f"{what}: {quoted} is not Yes or No."
        return found, None
    if col.kind in ("option", "options"):
        options = look.options.get(col.list_key or "", {})
        parts = [text] if col.kind == "option" else _split(text)
        ids = []
        for part in parts:
            option = options.get(part.casefold()) or options.get(part)
            if option is None:
                return None, f"{what}: “{part[:80]}” is not an option of this list."
            ids.append(option["id"])
        return (ids[0] if col.kind == "option" else ids), None
    if col.kind == "lead_stage":
        key = look.lead_stages.get(text.casefold())
        if key is None:
            return None, f"{what}: {quoted} is not a lead stage."
        return key, None
    if col.kind in ("entity", "entities"):
        names = {k: k for k in objects.ENTITIES} | {
            v.casefold(): k for k, v in objects.ENTITIES.items()
        }
        parts = [text] if col.kind == "entity" else _split(text)
        keys = []
        for part in parts:
            key = names.get(part.casefold())
            if key is None:
                return None, f"{what}: “{part[:80]}” is not Nunnari Labs or DeepWeaver."
            keys.append(key)
        return (keys[0] if col.kind == "entity" else keys), None
    if col.kind == "category":
        names = {k: k for k in objects.PROJECT_CATEGORIES} | {
            v.casefold(): k for k, v in objects.PROJECT_CATEGORIES.items()
        }
        key = names.get(text.casefold())
        if key is None:
            return None, f"{what}: {quoted} is not Client, POC or Product."
        return key, None
    if col.kind == "member":
        if is_uuid(text):
            found = [text] if text in look.member_ids else []
        else:
            found = _one(look.members, text)
        if len(found) != 1:
            why = "is not a member of this workspace" if not found else "names two members"
            return None, f"{what}: {quoted} {why}; use their email."
        return found[0], None
    if col.kind == "company":
        if is_uuid(text):
            found = [text] if text in look.company_ids else []
        else:
            domain = dedupe.normalise_domain(text) if "." in text and " " not in text else None
            by_domain = look.company_domains.get(domain or "")
            found = [by_domain] if by_domain else _one(look.company_names, text)
        if not found:
            return None, f"{what}: no company called {quoted}."
        if len(found) > 1:
            return None, f"{what}: two companies are called {quoted}; use its domain."
        return found[0], None
    if col.kind == "person":
        if is_uuid(text):
            found = [text] if text in look.person_ids else []
        elif "@" in text:
            hit = look.person_emails.get(dedupe.normalise_email(text))
            found = [hit] if hit else []
        else:
            found = _one(look.person_names, text)
        if not found:
            return None, f"{what}: no person called {quoted}."
        if len(found) > 1:
            return None, f"{what}: two people are called {quoted}; use their email."
        return found[0], None
    if col.kind in ("domain", "domains"):
        parts = [text] if col.kind == "domain" else _split(text)
        domains = []
        for part in parts:
            domain = dedupe.normalise_domain(part)
            if domain is None:
                return None, f"{what}: “{part[:80]}” is not a domain, such as acme.com."
            domains.append(domain)
        return (domains[0] if col.kind == "domain" else domains), None
    if col.kind in ("email", "emails"):
        parts = [text] if col.kind == "email" else _split(text)
        emails = []
        for part in parts:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", part):
                return None, f"{what}: “{part[:80]}” is not an email address."
            emails.append(dedupe.normalise_email(part))
        return (emails[0] if col.kind == "email" else emails), None
    # url: the record's primary link.
    why = objects.url_refusal(text)
    if why:
        return None, f"{what}: {why}"
    return {"primary": {"url": text}, "others": []}, None


def read_row(
    kind: str, mapping: dict[str, str | None], headers: list[str], cells: list[str], look: Lookups
) -> Read:
    """The row's mapped, non-empty cells as request fields."""
    out = Read()
    for header, text in zip(headers, cells, strict=True):
        name = mapping.get(header)
        value = unneutralise(text).strip()
        if not name or not value:
            continue
        col = column(kind, name)
        if col is None:
            continue
        parsed, why = read_cell(kind, col, value, look)
        if why:
            out.errors.append(why)
            continue
        if col.kind == "full_name":
            # A Name column fills first and last name only where their own
            # columns leave them empty: those, when mapped, win.
            for part, text_ in parsed.items():
                out.values.setdefault(part, text_)
            continue
        out.values[name] = parsed
    return out


# ---------------------------------------------------------------------------
# What a row does
# ---------------------------------------------------------------------------


def action_for(
    matched: bool, *, mode: Mode, chosen: Mode | None, unique_match: bool
) -> tuple[Mode, str | None]:
    """(action, refusal). A row matching a record does what `mode` says
    unless chosen per row; one matching nothing is created (or skipped).
    A record matched by its domain or email cannot be created twice."""
    if not matched:
        if chosen == "update":
            return "update", "This row matches no record to update; create it or skip it."
        return (chosen or "create"), None
    action = chosen or ("create" if mode == "create" else mode)
    if action == "create" and unique_match:
        return action, "A record with this domain or email exists; update it or skip the row."
    return action, None


def unchanged(field_name: str, value: Any, existing: Any) -> bool:
    """Whether a value read from the file equals what the record holds."""
    if value is None:
        return existing is None
    if field_name in ("website", "linkedin"):
        primary = ((existing or {}).get("primary") or {}).get("url")
        return primary == value["primary"]["url"]
    if isinstance(value, list):
        return sorted(value) == sorted(existing or [])
    if field_name in ("amount",):
        return existing is not None and Decimal(str(existing)) == Decimal(str(value))
    return str(value) == str(existing) if existing is not None else False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def neutralise(text: str) -> str:
    """OWASP CSV injection: a cell a spreadsheet would read as a formula is
    prefixed with `'`."""
    return f"'{text}" if text.startswith(FORMULA_STARTS) else text


def unneutralise(text: str) -> str:
    """The cell before `neutralise`, so an exported view reads back as it
    was: a `'` before a formula start is the export's, not the value's."""
    return text[1:] if text.startswith("'") and text[1:].startswith(FORMULA_STARTS) else text


#: A field whose shown value sits under another key of a presented record.
_SHOWN_AS: Final = {
    "owner_id": "owner_name",
    "assignee_id": "assignee_name",
    "created_by": "created_by_name",
    "company_id": "company_name",
    "point_of_contact_id": "point_of_contact_name",
    "person_id": "person_name",
    "campaign_id": "campaign_name",
    "converted_opportunity_id": "converted_opportunity_name",
    "source_campaign_id": "source_campaign_name",
    "entities": "entity_labels",
    "channel_ids": "channel_labels",
    "full_name": "name",
}


def cell(row: dict[str, Any], name: str) -> str:
    """A presented record's field as the web app shows it: a label beside a
    key or id, a person's or record's name, lists joined with "; ", a link's
    address, Yes or No."""
    key = _SHOWN_AS.get(name)
    if key is None:
        for candidate in (f"{name}_label", f"{name[:-3]}_label" if name.endswith("_id") else ""):
            if candidate and candidate in row:
                key = candidate
                break
    value = row.get(key) if key else row.get(name)
    if key and value is None and name.endswith("_id") and row.get(name):
        value = row.get(name)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return f"{SEPARATOR} ".join(
            cell({"v": v}, "v") if not isinstance(v, dict) else str(v.get("url") or "")
            for v in value
        )
    if isinstance(value, dict):
        primary = value.get("primary")
        if isinstance(primary, dict):
            return str(primary.get("url") or "")
        return "; ".join(f"{v}" for v in value.values() if v)
    return str(value)


def csv_line(cells: list[str]) -> str:
    """One CSV record, every cell neutralised."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow([neutralise(c) for c in cells])
    return buffer.getvalue()


def export_headers(kind: str, columns: list[str]) -> list[str]:
    return ["ID", *(label(kind, c) for c in columns)]


def filename(stem: str, on: date, extension: str) -> str:
    """`companies-2026-10-15.csv`: letters, digits and dashes only, so the
    header needs no quoting rules beyond the plain form."""
    safe = re.sub(r"[^a-z0-9]+", "-", stem.casefold()).strip("-") or "export"
    return f"{safe}-{on.isoformat()}.{extension}"
