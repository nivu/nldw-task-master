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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.calendar import today_in_company_tz
from app.domain.crm import dedupe
from app.domain.crm import importer as rules
from app.services import supabase as db
from app.services.crm import CrmRefused, store
from app.services.crm.context import actor

logger = logging.getLogger("nldw-task-master")

TOO_BIG = "The file is larger than 4 MB; export fewer records at a time."
NO_SUCH_RUN = "No such import run."
NOT_THE_FILE = "This is not the file the dry run read; dry-run it again."
TOO_OLD = "The dry run is more than 24 hours old; dry-run the file again."
#: FR-CRM-MIG-04 — how long a dry run stays good for the real run.
DRY_RUN_FOR = timedelta(hours=24)


@dataclass(frozen=True)
class Source:
    """What was sent: the bytes, the format, and for a CSV its object
    (named, or read from the file name)."""

    content: bytes
    format: str
    object: str | None = None
    filename: str | None = None


def parse(source: Source) -> tuple[str, str | None, dict[str, list[dict[str, Any]]]]:
    """(kind, object, bundle) — contracts §2.9's shape, or a 413 / 422."""
    if len(source.content) > rules.MAX_BYTES:
        refused = CrmRefused(413, TOO_BIG)
        refused.title = "Content Too Large"
        raise refused
    if source.format == "csv":
        raise CrmRefused(
            422,
            "A CSV of one object arrives with CSV import; for a Twenty export use twenty_csv.",
        )
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
    for obj, table in (("tasks", "crm_task_links"), ("notes", "crm_note_links")):
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
    attachments, and the `run_id` the real run needs. Writes only the run."""
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
_LINKS = {"tasks": ("crm_task_links", "task_id"), "notes": ("crm_note_links", "note_id")}


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
