"""The Twenty importer — spec 007 FR-CRM-MIG-01 to -08, FR-CRM-DUP-02,
FR-CRM-MCP-08; contracts §2.9; data-model §3.17; plan §5.3.

A file (an upload, or `crm_import`'s text) is parsed with the standard
library (`json`, `csv`), hashed, and planned by `domain/crm/importer.py`
against what the workspace holds now. The dry run reports the plan and
records the run; the real run needs the same file (by SHA-256) and a dry
run of it from the last 24 hours, plans again (so a second run finds
everything unchanged), and writes through `crm_apply` in chunks of at most
500 operations as the actor kind `import`. Attachments are listed, never
fetched (FR-CRM-MIG-03).

Every id list that goes into a URL is read in batches: a whole export's ids
in one PostgREST filter would be refused as too long.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.calendar import today_in_company_tz
from app.domain.crm import csv_io, dedupe, fx, objects, pipeline
from app.domain.crm import importer as rules
from app.services import supabase as db
from app.services.crm import CrmRefused, deals, store
from app.services.crm import records as base
from app.services.crm.context import actor

logger = logging.getLogger("nldw-task-master")

TOO_BIG = "The file is larger than 4 MB; export fewer records at a time."
NO_SUCH_RUN = "No such import run."
NOT_THE_FILE = "This is not the file the dry run read; dry-run it again."
TOO_OLD = "The dry run is more than 24 hours old; dry-run the file again."
ALREADY_RUN = "This import has already run; dry-run the file again."
#: FR-CRM-MIG-04 — how long a dry run stays good for the real run.
DRY_RUN_FOR = timedelta(hours=24)


@dataclass(frozen=True)
class Source:
    """What was sent: the bytes, the format, and for a CSV its object
    (named, or read from the file name). A CSV of one object (S11b) also
    carries its column mapping (or a saved one's id), what a row matching
    an existing record does, and per-row choices."""

    content: bytes
    format: str
    object: str | None = None
    filename: str | None = None
    mapping: dict[str, str | None] | None = None
    mapping_id: str | None = None
    mode: str | None = None
    actions: dict[str, str] | None = None


def parse(source: Source) -> tuple[str, str | None, dict[str, list[dict[str, Any]]]]:
    """(kind, object, bundle) — contracts §2.9's shape, or a 413 / 422."""
    if len(source.content) > rules.MAX_BYTES:
        refused = CrmRefused(413, TOO_BIG)
        refused.title = "Content Too Large"
        raise refused
    try:
        text = source.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CrmRefused(422, "The file is not UTF-8 text.") from None
    if source.format == "twenty_json":
        try:
            return "twenty", None, rules.bundle_from_json(json.loads(text))
        except json.JSONDecodeError:
            raise CrmRefused(422, "The file is not valid JSON.") from None
        except ValueError as exc:
            raise CrmRefused(422, str(exc)) from None
    obj = rules.object_named(source.object) or rules.object_named(source.filename)
    if obj is None:
        raise CrmRefused(
            422,
            "Name the object this CSV holds: companies, people, opportunities, tasks, "
            "notes or attachments.",
        )
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader]
    except csv.Error as exc:
        raise CrmRefused(422, f"The CSV could not be read: {exc}.") from None
    if not reader.fieldnames:
        raise CrmRefused(422, "The CSV has no header row.")
    return "twenty", obj, {obj: rows}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# What the workspace holds
# ---------------------------------------------------------------------------


def _batched(read: Callable[[list[str]], list[dict[str, Any]]], values: Iterable[str]) -> list:
    """`read` over the values in batches that fit a request line."""
    found: list[dict[str, Any]] = []
    for batch in store.text_batches(values):
        found += read(batch)
    return found


def _rows(ws_id: str, table: str, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Records by id, Trash included."""
    rows = _batched(
        lambda batch: store.list_records(
            ws_id, table, any_of={"id": batch}, deleted=None, limit=len(batch)
        ),
        ids,
    )
    return {r["id"]: r for r in rows}


def _facts(ctx: Any, bundle: dict[str, list[dict[str, Any]]]) -> rules.Facts:
    ws_id = ctx.workspace_id
    wanted = rules.twenty_ids(bundle)
    existing = {
        obj: {
            r["twenty_id"]: r
            for r in (
                store.list_by_twenty_ids(ws_id, rules.TABLES[obj], sorted(ids)) if ids else []
            )
        }
        for obj, ids in wanted.items()
    }
    companies = existing["companies"]
    company_ids = [r["id"] for r in companies.values()]

    company_domains: dict[str, list[tuple[str, bool]]] = {}
    for d in _batched(lambda b: store.list_domains(ws_id, company_ids=b), company_ids):
        company_domains.setdefault(d["company_id"], []).append((d["domain"], d["is_primary"]))
    named = {rules.read("companies", item)["domain"] for item in bundle.get("companies", [])}
    domains_in_file = {dedupe.normalise_domain(str(d)) for d in named if d}
    holders = _batched(lambda b: store.list_domains(ws_id, domains=b), domains_in_file - {None})
    owners = _rows(ws_id, "crm_companies", {d["company_id"] for d in holders})
    domains = {
        d["domain"]: {
            "company_id": d["company_id"],
            "name": owners.get(d["company_id"], {}).get("name", "another company"),
            "in_trash": bool(owners.get(d["company_id"], {}).get("deleted_at")),
        }
        for d in holders
    }

    emails_in_file = {
        rules.clean_email(rules.read("people", item)["email"]) for item in bundle.get("people", [])
    }
    people = _batched(
        lambda b: store.list_records(
            ws_id, "crm_people", any_of={"primary_email": b}, deleted=None, limit=len(b)
        ),
        emails_in_file - {None},
    )
    emails = {
        p["primary_email"]: {
            "person_id": p["id"],
            "name": dedupe.full_name(p["first_name"], p.get("last_name")),
            "in_trash": bool(p.get("deleted_at")),
        }
        for p in people
    }

    links: dict[str, set[tuple[str, str]]] = {}
    for obj, table in (("tasks", "task_links"), ("notes", "crm_note_links")):
        owner = store.LINK_TABLES[table]
        ids = [r["id"] for r in existing[obj].values()]
        for link in _batched(lambda b, t=table: store.list_links(ws_id, t, owner_ids=b), ids):
            links.setdefault(link[owner], set()).add((link["record_type"], link["record_id"]))

    company_deals: dict[str, list[dict[str, Any]]] = {}
    for deal in store.deals_of_companies(ws_id, company_ids) if company_ids else []:
        company_deals.setdefault(deal["company_id"], []).append(deal)

    member_ids = {m["user_id"] for m in store.list_members(ws_id)}
    members = {
        p["email"].strip().lower(): p["id"]
        for p in db.list_profiles(include_crm_only=True)
        if p["id"] in member_ids and p.get("is_active", True) and p.get("email")
    }
    settings = {s["key"]: s["value"] for s in store.list_settings(ws_id)}

    # 1b: who is already on the campaigns the file names, so a member under
    # another id is reported rather than doubled.
    campaign_ids = [r["id"] for r in existing["campaigns"].values()]
    member_pairs = {
        (m["campaign_id"], m.get("company_id"), m.get("person_id")): m.get("twenty_id")
        for m in (
            store.campaign_members(ws_id, column="campaign_id", ids=campaign_ids, deleted=None)
            if campaign_ids
            else []
        )
    }
    return rules.Facts(
        workspace=ctx.workspace["name"],
        importer=ctx.user.id,
        members=members,
        options=store.list_options(ws_id),
        rates=store.list_fx_rates(ws_id),
        probabilities=settings.get("stage_probabilities") or {},
        existing=existing,
        survivors=_rows(
            ws_id,
            "crm_companies",
            {r["merged_into_id"] for r in companies.values() if r.get("merged_into_id")},
        ),
        domains=domains,
        company_domains=company_domains,
        emails=emails,
        links=links,
        company_deals=company_deals,
        has_entity=bool(ctx.workspace.get("has_entity")),
        member_pairs=member_pairs,
    )


def _similar(ws_id: str, plan: rules.ImportPlan, bundle: dict) -> list[dict[str, Any]]:
    """FR-CRM-DUP-02, -MIG-04 — the new companies and people whose names are
    like a record already in the CRM: shown, never refused."""
    found: list[dict[str, Any]] = []
    for obj, kind in (("companies", "company"), ("people", "person")):
        rows = {
            str(rules.read(obj, item)["id"]).strip(): i
            for i, item in enumerate(bundle.get(obj, []), start=1)
        }
        names = rules.new_names(plan)[obj]
        if not names:
            continue
        matches: dict[int, list[dict[str, Any]]] = {}
        for match in store.similar_many(ws_id, kind, [name for _, name in names]):
            matches.setdefault(int(match["idx"]), []).append(match)
        for idx, alike in sorted(matches.items()):
            tid, name = names[idx - 1]
            found.append(
                {
                    "object": obj,
                    "row": rows.get(tid, 0),
                    "kind": "similar_name",
                    "detail": rules.similar_sentence(name, alike),
                    "blocking": False,
                }
            )
    return found


# ---------------------------------------------------------------------------
# Routes' entry points
# ---------------------------------------------------------------------------


def dry_run(ctx: Any, source: Source) -> dict[str, Any]:
    """FR-CRM-MIG-04 — counts, derived lead stages, conflicts and
    attachments, and the `run_id` the real run needs. Writes only the run.
    A CSV of one object (S11b) is CSV import's."""
    if source.format == "csv":
        return csv_dry_run(ctx, source)
    kind, obj, bundle = parse(source)
    plan = rules.plan(bundle, _facts(ctx, bundle), today=today_in_company_tz(), fixup=False)
    report = plan.report()
    report["conflicts"] = sorted(
        report["conflicts"] + _similar(ctx.workspace_id, plan, bundle),
        key=lambda c: (rules.OBJECTS.index(c["object"]), c["row"]),
    )
    run = store.record_import(
        actor(ctx, "import"),
        {
            "kind": kind,
            "object": obj,
            "file_sha256": _sha256(source.content),
            "report": report,
            "options": {"format": source.format},
        },
    )
    return {"run_id": run["id"], **report}


def apply(ctx: Any, run_id: str, source: Source, *, fixup: bool) -> dict[str, Any]:
    """FR-CRM-MIG-04, -07 — the real run of the file a dry run read."""
    run = _run(ctx, run_id)
    if source.format == "csv" or run["kind"] == "csv":
        if source.format != "csv" or run["kind"] != "csv":
            raise CrmRefused(409, NOT_THE_FILE)
        return csv_apply(ctx, run, source, fixup=fixup)
    kind, obj, bundle = parse(source)
    if (run["kind"], run.get("object"), run["file_sha256"]) != (kind, obj, _sha256(source.content)):
        raise CrmRefused(409, NOT_THE_FILE)
    created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
    if datetime.now(UTC) - created > DRY_RUN_FOR:
        raise CrmRefused(409, TOO_OLD)
    plan = rules.plan(bundle, _facts(ctx, bundle), today=today_in_company_tz(), fixup=fixup)
    if plan.blocking:
        raise CrmRefused(422, rules.blocked_sentence(plan.blocking))
    done = {"counts": plan.counts, "fixups_used": plan.fixups}
    options = {"format": source.format, "fixup_next_step": fixup}
    try:
        _write(ctx, plan)
    except Exception:
        try:
            store.record_import(
                actor(ctx, "import"),
                {"id": run_id, "status": "failed", "report": done, "options": options},
            )
        except Exception:  # the write's own error is the one to surface
            logger.exception('{"event": "crm_import_failed_unrecorded", "run": "%s"}', run_id)
        raise
    store.record_import(
        actor(ctx, "import"),
        {"id": run_id, "status": "applied", "report": done, "options": options},
    )
    return {"run_id": run_id, **done}


def runs(ctx: Any) -> list[dict[str, Any]]:
    """Recent runs with their counts, never their rows or the file."""
    return store.list_import_runs(ctx.workspace_id)


def _run(ctx: Any, run_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(run_id)
    except ValueError:
        raise CrmRefused(404, NO_SUCH_RUN) from None
    run = store.get_import_run(ctx.workspace_id, run_id)
    if run is None:
        raise CrmRefused(404, NO_SUCH_RUN)
    return run


# ---------------------------------------------------------------------------
# Writing — crm_apply in chunks (plan, "statement timeout on big imports")
# ---------------------------------------------------------------------------

#: A task's or note's link table and its owner column.
_LINKS = {"tasks": ("task_links", "task_id"), "notes": ("crm_note_links", "note_id")}


def _write(ctx: Any, plan: rules.ImportPlan) -> None:
    """Object by object, so every record a row points at has its CRM id
    before that row is written; each record with its domains or links in
    one chunk, so they land together."""
    who = actor(ctx, "import")
    ids: dict[tuple[str, str], str] = {}

    def resolve(value: Any) -> Any:
        if not isinstance(value, rules.Ref):
            return value
        found = ids.get((value.object, value.twenty_id))
        if found is None:
            raise CrmRefused(409, "The workspace changed during the import; dry-run it again.")
        return found

    for obj, table in rules.TABLES.items():
        groups = [_group(obj, table, n, write, resolve) for n, write in enumerate(plan.writes[obj])]
        chunk: list[dict[str, Any]] = []
        for group in [*groups, None]:
            if chunk and (group is None or len(chunk) + len(group) > rules.CHUNK):
                for op, result in zip(chunk, store.apply(who, chunk), strict=True):
                    if op.get("ref") and result.get("row"):
                        ids[(obj, result["row"]["twenty_id"])] = result["id"]
                chunk = []
            if group:
                chunk += group


def _group(
    obj: str, table: str, n: int, write: rules.Write, resolve: Callable[[Any], Any]
) -> list[dict[str, Any]]:
    values = {k: resolve(v) for k, v in write.values.items()}
    ops: list[dict[str, Any]] = []
    if write.op in ("upsert", "insert"):
        ref = f"{obj}{n}"
        ops.append({"op": write.op, "table": table, "values": values, "ref": ref})
        owner = f"$ref:{ref}"
    else:
        owner = write.existing["id"]
        if values:
            ops.append(
                {
                    "op": "update",
                    "table": table,
                    "id": owner,
                    "expect_updated_at": write.existing["updated_at"],
                    "values": values,
                }
            )
    for domain, primary in write.domains:
        ops.append(
            {
                "op": "insert",
                "table": "crm_company_domains",
                "values": {"company_id": owner, "domain": domain, "is_primary": primary},
            }
        )
    if write.links:
        link_table, column = _LINKS[obj]
        for record_type, target in write.links:
            ops.append(
                {
                    "op": "insert",
                    "table": link_table,
                    "values": {
                        column: owner,
                        "record_type": record_type,
                        "record_id": resolve(target),
                    },
                }
            )
    return ops


# ---------------------------------------------------------------------------
# CSV import — S11b (FR-CRM-IO-01 to -03, FR-CRM-DUP-02; contracts §2.9)
# ---------------------------------------------------------------------------

NOT_A_CSV_OBJECT = "Name the object this CSV holds: companies, people or opportunities."
NO_SUCH_MAPPING = "No such saved mapping."


@dataclass
class CsvRow:
    """One row of the file and what it will do."""

    n: int
    cells: list[str]
    action: str = "create"
    #: The record it matches: `{id, name, by}` (by id, domain or email).
    match: dict[str, Any] | None = None
    #: The fields to write: a new record's, or an existing one's changes.
    values: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unchanged: bool = False
    fixup: bool = False
    #: A new deal missing only its next step, which the fix-up can give.
    fixable: bool = False

    @property
    def blocking(self) -> bool:
        return bool(self.errors) and self.action != "skip"


@dataclass
class CsvPlan:
    kind: str
    headers: list[str]
    mapping: dict[str, str | None]
    rows: list[CsvRow]

    def counts(self) -> dict[str, dict[str, int]]:
        tally = {"in_file": len(self.rows), "create": 0, "update": 0, "unchanged": 0, "skipped": 0}
        for row in self.rows:
            if row.action == "skip":
                tally["skipped"] += 1
            elif row.unchanged:
                tally["unchanged"] += 1
            else:
                tally[row.action] += 1
        return {csv_io.PLURAL[self.kind]: tally}

    def conflicts(self) -> list[dict[str, Any]]:
        out = []
        for row in self.rows:
            for detail in row.errors:
                out.append(
                    {
                        "object": csv_io.PLURAL[self.kind],
                        "row": row.n,
                        "kind": "row_error",
                        "detail": detail,
                        "blocking": row.blocking,
                    }
                )
            for detail in row.warnings:
                out.append(
                    {
                        "object": csv_io.PLURAL[self.kind],
                        "row": row.n,
                        "kind": "similar_name",
                        "detail": detail,
                        "blocking": False,
                    }
                )
        return out

    def blocking(self) -> list[str]:
        return [f"Row {r.n}: {r.errors[0]}" for r in self.rows if r.blocking]


def _csv_text(source: Source) -> str:
    if len(source.content) > csv_io.MAX_BYTES:
        refused = CrmRefused(413, TOO_BIG)
        refused.title = "Content Too Large"
        raise refused
    try:
        return source.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CrmRefused(422, "The file is not UTF-8 text.") from None


def _csv_object(source: Source) -> str:
    kind = csv_io.object_named(source.object) or csv_io.object_named(source.filename)
    if kind is None:
        raise CrmRefused(422, NOT_A_CSV_OBJECT)
    return kind


def _refuse_mapping(ctx: Any, kind: str, mapping: dict, headers: list[str] | None) -> None:
    why = csv_io.mapping_refusal(
        kind,
        mapping,
        headers,
        has_entity=bool(ctx.workspace.get("has_entity")),
        tva_linked=bool(ctx.workspace.get("tva_linked")),
        workspace=ctx.workspace["name"],
    )
    if why:
        raise CrmRefused(422, why)


def _saved_mapping(ctx: Any, mapping_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(mapping_id)
    except ValueError:
        raise CrmRefused(404, NO_SUCH_MAPPING) from None
    found = store.get_csv_mapping(ctx.workspace_id, mapping_id)
    if found is None:
        raise CrmRefused(404, NO_SUCH_MAPPING)
    return found


def _csv_mapping(ctx: Any, kind: str, headers: list[str], source: Source) -> dict[str, str | None]:
    """The mapping sent, a saved one (its columns this file has), or the
    one the headers name."""
    if source.mapping is not None:
        mapping = dict(source.mapping)
        _refuse_mapping(ctx, kind, mapping, headers)
        return {h: mapping.get(h) for h in headers}
    if source.mapping_id:
        saved = _saved_mapping(ctx, source.mapping_id)
        if saved["object"] != kind:
            raise CrmRefused(422, f"That mapping is for {csv_io.PLURAL[saved['object']]}.")
        mapping = {h: (saved["mapping"] or {}).get(h) for h in headers}
    else:
        mapping = csv_io.default_mapping(kind, headers)
    _refuse_mapping(ctx, kind, mapping, headers)
    return mapping


def _mapped_cells(plan_rows: list[list[str]], headers: list[str], mapping, field_: str) -> set:
    """The non-empty cells of the column mapped to `field_`."""
    wanted = [i for i, h in enumerate(headers) if mapping.get(h) == field_]
    return {row[i].strip() for row in plan_rows for i in wanted if row[i].strip()}


def _lookups(
    ws: base.Workspace, kind: str, headers: list[str], rows: list[list[str]], mapping
) -> csv_io.Lookups:
    look = csv_io.Lookups()
    for option in ws.facts.options.values():
        by = look.options.setdefault(option["list_key"], {})
        by[option["id"]] = option
        by.setdefault(option["label"].casefold(), option)
        if option["list_key"] == "company.lead_stage" and option.get("system_key"):
            look.lead_stages[option["system_key"]] = option["system_key"]
            look.lead_stages[option["label"].casefold()] = option["system_key"]
    for key in objects.LEAD_STAGES:
        look.lead_stages.setdefault(key, key)
        look.lead_stages.setdefault(key.replace("_", " "), key)
    look.member_ids = set(ws.members)
    for p in db.list_profiles(include_crm_only=True):
        if p["id"] not in ws.members:
            continue
        for text in (p.get("email"), p.get("display_name")):
            if text:
                look.members.setdefault(text.casefold().strip(), []).append(p["id"])

    fields = set(mapping.values())
    if kind != "company" and "company_id" in fields:
        for c in store.record_names(ws.id, "crm_companies"):
            look.company_ids.add(c["id"])
            look.company_names.setdefault(c["name"].casefold().strip(), []).append(c["id"])
        cells = _mapped_cells(rows, headers, mapping, "company_id")
        domains = {dedupe.normalise_domain(c) for c in cells if "." in c and " " not in c}
        for d in _batched(lambda b: store.list_domains(ws.id, domains=b), domains - {None}):
            if d["company_id"] in look.company_ids:
                look.company_domains[d["domain"]] = d["company_id"]
    if kind == "opportunity" and "point_of_contact_id" in fields:
        for p in store.record_names(ws.id, "crm_people"):
            look.person_ids.add(p["id"])
            name = dedupe.full_name(p["first_name"], p.get("last_name")).casefold()
            look.person_names.setdefault(name, []).append(p["id"])
            if p.get("primary_email"):
                look.person_emails[p["primary_email"]] = p["id"]
    return look


def _holders(ws_id: str, kind: str, reads: list[csv_io.Read]) -> dict[str, dict[str, Any]]:
    """The records (Trash included) already holding a domain or an email the
    file names: `{domain or email: record}` (FR-CRM-DUP-01)."""
    if kind == "company":
        named = set()
        for read in reads:
            named |= {read.values.get("domain")} | set(read.values.get("additional_domains") or [])
        holders = _batched(lambda b: store.list_domains(ws_id, domains=b), named - {None})
        owners = _rows(ws_id, "crm_companies", {d["company_id"] for d in holders})
        return {d["domain"]: owners[d["company_id"]] for d in holders if d["company_id"] in owners}
    if kind == "person":
        named = {read.values.get("primary_email") for read in reads} - {None}
        people = _batched(
            lambda b: store.list_records(
                ws_id, "crm_people", any_of={"primary_email": b}, deleted=None, limit=len(b)
            ),
            named,
        )
        return {p["primary_email"]: p for p in people}
    return {}


def _name(kind: str, row: dict[str, Any]) -> str:
    if kind == "person":
        return dedupe.full_name(row["first_name"], row.get("last_name"))
    return row["name"]


def _existing_value(kind: str, name: str, row: dict[str, Any], domains: list[dict]) -> Any:
    if kind == "company" and name == "domain":
        return next((d["domain"] for d in domains if d["is_primary"]), None)
    if kind == "company" and name == "additional_domains":
        return [d["domain"] for d in domains if not d["is_primary"]]
    return row.get(name)


def _try(errors: list[str], check: Callable[[], Any]) -> Any:
    """Runs a record route's own check, a refusal becoming the row's error."""
    try:
        return check()
    except CrmRefused as refused:
        errors.append(refused.detail)
        return None


def _deal_values(
    ws: base.Workspace,
    row: CsvRow,
    values: dict[str, Any],
    companies: dict[str, dict[str, Any]],
    opened: set[str],
    *,
    rates: list[dict[str, Any]],
    probabilities: dict[str, Any],
    fixup: bool,
) -> dict[str, Any] | None:
    """FR-CRM-IO-03 "every pipeline rule applies to imported rows": a deal
    opens at Discovery on a company at Qualified or later (which then moves
    to Deal open), with its next step and date (or the fix-up's), the
    stage's probability unless one is given, and its amount in INR at the
    month's rate. As `deals.create` (FR-CRM-PIPE-04, -07, -10, -11,
    FR-CRM-FX-03)."""
    company = companies.get(values.get("company_id") or "")
    if company is None or company.get("deleted_at"):
        row.errors.append("A deal needs a live company.")
        return None
    if company["id"] not in opened:
        labelled = {
            **company,
            "lead_stage_label": ws.facts.fixed_label("company.lead_stage", company["lead_stage"]),
        }
        _, why = pipeline.company_effect_on_open(labelled)
        if why:
            row.errors.append(why)
            return None
    missing = pipeline.stage_requirements("discovery", values, [])
    if missing and fixup and set(missing) <= set(pipeline.NEXT_STEP):
        values.update(
            next_step=rules.FIXUP_NEXT_STEP, next_step_on=today_in_company_tz().isoformat()
        )
        row.fixup = True
        missing = []
    if missing:
        row.fixable = set(missing) <= set(pipeline.NEXT_STEP)
        row.errors.append(pipeline.missing_sentence("discovery", missing))
        return None
    if values.get("probability") is None:
        values["probability"] = pipeline.default_probability("discovery", probabilities)
    if values.get("amount") is not None or values.get("currency"):
        if values.get("amount") is None:
            row.errors.append("A currency needs an amount.")
            return None
        if not values.get("currency"):
            row.errors.append("An amount needs a currency.")
            return None
        found = fx.converted(
            rates,
            values["amount"],
            values["currency"],
            today_in_company_tz(),
            ws.ctx.workspace["name"],
        )
        if isinstance(found, str):
            row.errors.append(found)
            return None
        values.update(found)
    return values


def plan_csv(
    ctx: Any,
    kind: str,
    headers: list[str],
    cells: list[list[str]],
    mapping: dict[str, str | None],
    *,
    mode: str,
    actions: dict[str, str],
    fixup: bool,
) -> CsvPlan:
    """What each row of the file would do, and every reason one cannot."""
    ws = base.load(ctx)
    ws_id = ctx.workspace_id
    look = _lookups(ws, kind, headers, cells, mapping)
    reads = [csv_io.read_row(kind, mapping, headers, row, look) for row in cells]
    table = base.KINDS[kind].table
    by_id = _rows(ws_id, table, {r.values["id"] for r in reads if r.values.get("id")})
    holders = _holders(ws_id, kind, reads)
    company_domains: dict[str, list[dict[str, Any]]] = {}
    if kind == "company":
        ids = [r["id"] for r in by_id.values()] + [r["id"] for r in holders.values()]
        for d in store.list_domains(ws_id, company_ids=ids) if ids else []:
            company_domains.setdefault(d["company_id"], []).append(d)
    deal_companies: dict[str, dict[str, Any]] = {}
    if kind == "opportunity":
        deal_companies = _rows(
            ws_id, "crm_companies", {r.values.get("company_id") for r in reads} - {None}
        )
    rates = store.list_fx_rates(ws_id) if kind == "opportunity" else []
    settings = {s["key"]: s["value"] for s in store.list_settings(ws_id)}
    probabilities = settings.get("stage_probabilities") or {}

    seen: dict[str, int] = {}
    opened: set[str] = set()
    rows: list[CsvRow] = []
    for n, (raw, read) in enumerate(zip(cells, reads, strict=True), start=1):
        row = CsvRow(n=n, cells=raw, errors=list(read.errors))
        rows.append(row)
        values = dict(read.values)
        record_id = values.pop("id", None)

        # The record it matches (FR-CRM-IO-03): by ID, domain or email.
        existing, by = None, None
        if record_id:
            existing, by = by_id.get(record_id), "id"
            if existing is None:
                row.errors.append(f"No such {csv_io.NOUN[kind]} with ID {record_id}.")
        else:
            key = values.get("domain") if kind == "company" else values.get("primary_email")
            if kind != "opportunity" and key and key in holders:
                existing, by = holders[key], ("domain" if kind == "company" else "email")
        if existing is not None and existing.get("deleted_at"):
            row.errors.append(
                f"{_name(kind, existing)} is in the Trash; restore it or skip this row."
            )
            existing = None
        if existing is not None:
            row.match = {"id": existing["id"], "name": _name(kind, existing), "by": by}

        # The same record twice in one file.
        row_domains = [values.get("domain"), *(values.get("additional_domains") or [])]
        for key in (
            f"id:{record_id}" if record_id else None,
            *(f"domain:{d}" for d in dict.fromkeys(row_domains) if d),
            f"email:{values['primary_email']}" if values.get("primary_email") else None,
        ):
            if key is None:
                continue
            if key in seen:
                what = key.split(":", 1)[0].replace("id", "ID")
                row.errors.append(f"Row {seen[key]} has the same {what}.")
            else:
                seen[key] = n

        chosen = actions.get(str(n))
        row.action, why = csv_io.action_for(
            existing is not None,
            mode=mode,
            chosen=chosen,
            unique_match=by in ("domain", "email"),
        )
        if why:
            row.errors.append(why)
        if row.action == "skip" or row.errors:
            continue

        if row.action == "update":
            domains = company_domains.get(existing["id"], [])
            changes = {
                k: v
                for k, v in values.items()
                if not csv_io.unchanged(k, v, _existing_value(kind, k, existing, domains))
            }
            if not changes:
                row.unchanged = True
                row.values = {}
                continue
            parsed = _try(row.errors, lambda: base.parse(kind, changes, creating=False))
            if parsed is not None:
                _try(row.errors, lambda: base.check_fields(ws, kind, dict(parsed), existing))
            row.values = changes
        else:
            if kind in ("company", "opportunity") and not values.get("owner_id"):
                values["owner_id"] = ctx.user.id
            stage = values.pop("lead_stage", None) if kind == "company" else None
            if stage is not None and stage != "new":
                why = objects.lead_stage_refusal(stage, creating=False)
                if why:
                    row.errors.append(why)
            parsed = _try(row.errors, lambda: base.parse(kind, values, creating=True))
            if parsed is None:
                continue
            _try(row.errors, lambda: base.check_fields(ws, kind, dict(parsed), None))
            if kind == "company":
                parsed["lead_stage"] = stage or "new"
            if kind == "opportunity" and not row.errors:
                parsed = _deal_values(
                    ws,
                    row,
                    parsed,
                    deal_companies,
                    opened,
                    rates=rates,
                    probabilities=probabilities,
                    fixup=fixup,
                )
                if parsed is not None:
                    opened.add(parsed["company_id"])
            row.values = parsed

        # A domain or email another record holds (FR-CRM-DUP-01).
        # The matched record's own domain or email is no clash only when
        # the row updates it; a new record would collide with it.
        mine = row.match["id"] if row.match and row.action == "update" else None
        if kind == "company" and row.values:
            wanted = [row.values.get("domain")] + list(row.values.get("additional_domains") or [])
            for domain in [d for d in wanted if d]:
                holder = holders.get(domain)
                if holder is not None and holder["id"] != mine:
                    row.errors.append(
                        dedupe.domain_clash(
                            domain, holder["name"], in_trash=bool(holder.get("deleted_at"))
                        )
                    )
                    break
        if kind == "person" and row.values and row.values.get("primary_email"):
            holder = holders.get(row.values["primary_email"])
            if holder is not None and holder["id"] != mine:
                row.errors.append(
                    dedupe.email_clash(
                        row.values["primary_email"],
                        _name("person", holder),
                        in_trash=bool(holder.get("deleted_at")),
                    )
                )

    # FR-CRM-DUP-02: names like a record already in the CRM, shown only.
    if kind in ("company", "person"):
        fresh = [r for r in rows if r.action == "create" and not r.errors and r.values]
        names = [
            r.values["name"]
            if kind == "company"
            else dedupe.full_name(r.values["first_name"], r.values.get("last_name"))
            for r in fresh
        ]
        matches: dict[int, list[dict[str, Any]]] = {}
        for match in store.similar_many(ws_id, kind, names) if names else []:
            matches.setdefault(int(match["idx"]), []).append(match)
        for idx, alike in matches.items():
            fresh[idx - 1].warnings.append(rules.similar_sentence(names[idx - 1], alike))
    return CsvPlan(kind=kind, headers=headers, mapping=mapping, rows=rows)


def _read_csv(ctx: Any, source: Source) -> tuple[str, list[str], list[list[str]]]:
    kind = _csv_object(source)
    try:
        headers, cells = csv_io.parse(_csv_text(source))
    except ValueError as exc:
        raise CrmRefused(422, str(exc)) from None
    return kind, headers, cells


def _actions(source: Source) -> dict[str, str]:
    actions = dict(source.actions or {})
    for key, value in actions.items():
        if not key.isdigit() or value not in csv_io.MODES:
            raise CrmRefused(422, "A row's choice is skip, update or create, by row number.")
    return actions


def _preview(plan: CsvPlan) -> list[dict[str, Any]]:
    return [
        {
            "row": r.n,
            "action": r.action,
            "unchanged": r.unchanged,
            "match": r.match,
            "errors": r.errors,
            "warnings": r.warnings,
            "cells": r.cells,
        }
        for r in plan.rows
    ]


def csv_dry_run(ctx: Any, source: Source) -> dict[str, Any]:
    """FR-CRM-IO-01, -03 — the mapping used, what each row would do, every
    row's errors and warnings, and the `run_id` the real run needs. Writes
    only the run, whose report keeps counts and sentences, never a row."""
    kind, headers, cells = _read_csv(ctx, source)
    mapping = _csv_mapping(ctx, kind, headers, source)
    mode = source.mode or "skip"
    plan = plan_csv(
        ctx, kind, headers, cells, mapping, mode=mode, actions=_actions(source), fixup=False
    )
    report = {
        "counts": plan.counts(),
        "conflicts": plan.conflicts(),
        "blocking": plan.blocking(),
        "fixup_candidates": sum(1 for r in plan.rows if r.fixable),
    }
    run = store.record_import(
        actor(ctx, "import"),
        {
            "kind": "csv",
            "object": kind,
            "file_sha256": _sha256(source.content),
            "report": report,
            "options": {"format": "csv", "mapping": mapping, "mode": mode},
        },
    )
    return {
        "run_id": run["id"],
        "object": kind,
        "headers": headers,
        "mapping": mapping,
        "fields": [
            {"field": c.field, "label": csv_io.label(kind, c.field)} for c in csv_io.COLUMNS[kind]
        ],
        **report,
        "lead_stages": [],
        "attachments": [],
        "rows": _preview(plan),
    }


def csv_apply(ctx: Any, run: dict[str, Any], source: Source, *, fixup: bool) -> dict[str, Any]:
    """FR-CRM-IO-03 — the real run of the file its dry run read, with the
    dry run's mapping: new records written in chunks as the import, each
    with its domains; changed records through the record routes' own
    update, so every rule (and a Labs deal's TVA side) applies. A row
    refused at write time is reported, the rest written."""
    kind, headers, cells = _read_csv(ctx, source)
    if (run.get("object"), run["file_sha256"]) != (kind, _sha256(source.content)):
        raise CrmRefused(409, NOT_THE_FILE)
    created_at = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
    if datetime.now(UTC) - created_at > DRY_RUN_FOR:
        raise CrmRefused(409, TOO_OLD)
    # A CSV row has no key a second run would match it by (a deal never
    # does), so a run applied, or failed part-way, is not run again.
    if run.get("status", "dry_run") != "dry_run":
        raise CrmRefused(409, ALREADY_RUN)
    options = run.get("options") or {}
    mapping = options.get("mapping") or csv_io.default_mapping(kind, headers)
    mode = source.mode or options.get("mode") or "skip"
    plan = plan_csv(
        ctx, kind, headers, cells, mapping, mode=mode, actions=_actions(source), fixup=fixup
    )
    blocking = plan.blocking()
    if blocking:
        raise CrmRefused(422, rules.blocked_sentence(blocking))

    failed: list[dict[str, Any]] = []
    try:
        created = _write_new(ctx, kind, [r for r in plan.rows if r.action == "create"], failed)
        updated = 0
        for row in plan.rows:
            if row.action != "update" or row.unchanged:
                continue
            try:
                if kind == "opportunity":
                    deals.update(ctx, row.match["id"], row.values)
                else:
                    base.update(ctx, kind, row.match["id"], row.values)
                updated += 1
            except CrmRefused as refused:
                failed.append({"row": row.n, "detail": refused.detail})
    except Exception:
        try:
            store.record_import(
                actor(ctx, "import"),
                {"id": run["id"], "status": "failed", "report": {"counts": plan.counts()}},
            )
        except Exception:  # the write's own error is the one to surface
            logger.exception('{"event": "crm_import_failed_unrecorded", "run": "%s"}', run["id"])
        raise
    done = {
        "counts": plan.counts(),
        "created": created,
        "updated": updated,
        "failed": sorted(failed, key=lambda f: f["row"]),
        "fixups_used": sum(1 for r in plan.rows if r.fixup and r.action == "create"),
    }
    store.record_import(
        actor(ctx, "import"),
        {
            "id": run["id"],
            "status": "applied",
            "report": {k: v for k, v in done.items() if k != "failed"}
            | {"failed_rows": [f["row"] for f in failed]},
            "options": {**options, "mode": mode, "fixup_next_step": fixup},
        },
    )
    return {"run_id": run["id"], **done}


def _new_ops(kind: str, row: CsvRow, ref: str) -> list[dict[str, Any]]:
    """A new record and what lands with it: a company's domains, a deal's
    company moving to Deal open (idempotent: a second deal's is a no-op)."""
    table = base.KINDS[kind].table
    values = dict(row.values or {})
    ops: list[dict[str, Any]] = []
    if kind == "company":
        domains = [(values.pop("domain", None), True)] + [
            (d, False) for d in values.pop("additional_domains", None) or []
        ]
        ops.append({"op": "insert", "table": table, "values": values, "ref": ref})
        ops += [
            {
                "op": "insert",
                "table": "crm_company_domains",
                "values": {"company_id": f"$ref:{ref}", "domain": d, "is_primary": primary},
            }
            for d, primary in domains
            if d
        ]
        return ops
    if kind == "opportunity":
        ops.append(
            {"op": "insert", "table": table, "values": {**values, "stage": "discovery"}, "ref": ref}
        )
        return ops
    ops.append({"op": "insert", "table": table, "values": values, "ref": ref})
    return ops


def _write_new(ctx: Any, kind: str, rows: list[CsvRow], failed: list[dict[str, Any]]) -> int:
    """New records in chunks of at most `rules.CHUNK` operations, as the
    import. A chunk refused is written again row by row, so one bad row
    does not keep the rest out."""
    who = actor(ctx, "import")
    ws = base.load(ctx)
    groups: list[tuple[CsvRow, list[dict[str, Any]]]] = []
    company_moves: dict[str, dict[str, Any]] = {}
    if kind == "opportunity":
        companies = _rows(ctx.workspace_id, "crm_companies", {r.values["company_id"] for r in rows})
        for cid, company in companies.items():
            labelled = {
                **company,
                "lead_stage_label": ws.facts.fixed_label(
                    "company.lead_stage", company["lead_stage"]
                ),
            }
            changes, _ = pipeline.company_effect_on_open(labelled)
            if changes:
                company_moves[cid] = changes
    for i, row in enumerate(rows):
        ops = _new_ops(kind, row, f"row{i}")
        cid = (row.values or {}).get("company_id")
        if kind == "opportunity" and cid in company_moves:
            ops.append(
                {"op": "update", "table": "crm_companies", "id": cid, "values": company_moves[cid]}
            )
        groups.append((row, ops))

    written: list[dict[str, Any]] = []

    def write(chunk: list[tuple[CsvRow, list[dict[str, Any]]]]) -> None:
        ops = [op for _, group in chunk for op in group]
        done = store.apply(who, ops)
        written.extend(r["row"] for op, r in zip(ops, done, strict=True) if op.get("ref"))

    chunk: list[tuple[CsvRow, list[dict[str, Any]]]] = []
    for group in [*groups, None]:
        size = sum(len(g) for _, g in chunk)
        if chunk and (group is None or size + len(group[1]) > rules.CHUNK):
            try:
                write(chunk)
            except CrmRefused:
                for one in chunk:
                    try:
                        write([one])
                    except CrmRefused as refused:
                        failed.append({"row": one[0].n, "detail": refused.detail})
            chunk = []
        if group is not None:
            chunk.append(group)
    if written:
        base.reflag(ctx.workspace_id, kind, written)
    return len(written)


# ---------------------------------------------------------------------------
# Saved mappings — contracts §2.10 `/crm/csv-mappings` (FR-CRM-IO-01)
# ---------------------------------------------------------------------------


def _present_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "object": row["object"],
        "name": row["name"],
        "mapping": row["mapping"],
        "updated_at": row.get("updated_at"),
    }


def list_mappings(ctx: Any, object_name: str | None = None) -> list[dict[str, Any]]:
    kind = csv_io.object_named(object_name) if object_name else None
    if object_name and kind is None:
        raise CrmRefused(422, NOT_A_CSV_OBJECT)
    return [_present_mapping(m) for m in store.list_csv_mappings(ctx.workspace_id, object=kind)]


def save_mapping(ctx: Any, object_name: str, name: str, mapping: dict) -> dict[str, Any]:
    """A named mapping for this object; saving under a name it has already
    replaces that one's columns."""
    kind = csv_io.object_named(object_name)
    if kind is None:
        raise CrmRefused(422, NOT_A_CSV_OBJECT)
    _refuse_mapping(ctx, kind, mapping, None)
    same = next(
        (
            m
            for m in store.list_csv_mappings(ctx.workspace_id, object=kind)
            if m["name"].casefold() == name.casefold()
        ),
        None,
    )
    if same is None:
        op = {
            "op": "insert",
            "table": "crm_csv_mappings",
            "values": {"object": kind, "name": name, "mapping": mapping},
        }
    else:
        op = {
            "op": "update",
            "table": "crm_csv_mappings",
            "id": same["id"],
            "values": {"name": name, "mapping": mapping},
        }
    done = store.apply(actor(ctx), [op])
    return _present_mapping(done[0]["row"])


def delete_mapping(ctx: Any, mapping_id: str) -> dict[str, Any]:
    row = _saved_mapping(ctx, mapping_id)
    store.apply(actor(ctx), [{"op": "delete", "table": "crm_csv_mappings", "id": row["id"]}])
    return {"deleted": True}
