"""CSV and JSON export — spec 007 FR-CRM-IO-04, -05, FR-CRM-VIEW-07,
FR-CRM-ACC-11, NFR-CRM-05; contracts §2.10; plan §5.12.

A view exports to CSV with its current filters, sorts and columns: the rows
are read through the same `POST /crm/query` path the table shows them by
(`services/crm/views.query`), page after page, and each cell is the value
the table shows (a label, a person's or record's name). The bulk bar exports
the rows it has selected instead. Admins also export the whole workspace,
or one object of it, as JSON.

Admins and Members export; Viewers do not (§3.3), so no amount leaves the
CRM for them (FR-CRM-ACC-11). Both are capped at 5 MB, past which the
answer is 413 with a sentence (Netlify's response limit is about 6 MB, plan
§5.12), and both write an `export.view` / `export.workspace` audit row with
what was exported, never a value of it.

CSV cells starting `=`, `+`, `-`, `@`, a tab or a carriage return are
prefixed with `'` (`domain/crm/csv_io.neutralise`): names reach the CRM
from outside senders through Gmail sync.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.domain.crm import csv_io, objects
from app.domain.crm import views as view_rules
from app.schemas.crm import ExportViewIn, QueryIn
from app.services.crm import (
    CrmRefused,
    ai_fields,
    enrichment,
    review,
    store,
    summaries,
    views,
)
from app.services.crm import present as show
from app.services.crm import records as base
from app.services.crm.context import actor

NO_EXPORT = "Viewers cannot export."
#: The CSV starts with a byte-order mark, so a spreadsheet reads the text
#: as UTF-8; CSV import reads it back the same way.
BOM = "﻿"
#: Rows per page read for an export.
PAGE = 200
#: Rows presented at a time for a whole-workspace export.
PRESENT_BATCH = 500
#: The objects of a whole-workspace export, by their name in the file.
WORKSPACE_OBJECTS = {
    "companies": "company",
    "people": "person",
    "opportunities": "opportunity",
    "tasks": "task",
    "notes": "note",
    "campaigns": "campaign",
    "campaign_members": "campaign_member",
    "drive_links": "drive_link",
    # Spec 009 FR-CRMAI-CHG-11: the Review Queue's items, read by their own
    # reader (`services/crm/review.export_rows`), not as records.
    "review_items": "review_item",
    # Spec 009 FR-CRMAI-CHG-11: email thread summaries with their links
    # (`services/crm/summaries.export_rows`).
    "email_summaries": "email_summary",
    # Spec 009 FR-CRMAI-CHG-11: company enrichment
    # (`services/crm/enrichment.export_rows`).
    "enrichment": "enrichment",
    # Spec 009 FR-CRMAI-CHG-11: AI field definitions and values
    # (`services/crm/ai_fields.export_fields`, `export_values`).
    "ai_fields": "ai_field",
    "ai_field_values": "ai_field_value",
}
#: Spec 009: the AI objects of the export, which are not record kinds, and
#: their tables (for the export's audit row).
AI_TABLES = {
    "review_item": "crm_review_items",
    "email_summary": "crm_email_summaries",
    "enrichment": "crm_company_enrichment",
    "ai_field": "crm_ai_fields",
    "ai_field_value": "crm_ai_field_values",
}


def _too_big() -> CrmRefused:
    refused = CrmRefused(413, csv_io.TOO_BIG_EXPORT)
    refused.title = "Content Too Large"
    return refused


def _may_export(ctx: Any) -> None:
    if not ctx.may("export"):
        raise CrmRefused(403, NO_EXPORT)


def _plural(kind: str) -> str:
    return next((name for name, k in WORKSPACE_OBJECTS.items() if k == kind), kind).replace(
        "_", "-"
    )


def _columns(ctx: Any, kind: str, body: ExportViewIn) -> list[str]:
    """The columns asked for, else the view's, else every field the list
    has here; each checked as a list's columns are."""
    asked = body.columns
    if not asked and (body.view_id or body.view):
        found = views.view_for(ctx, kind, view_id=body.view_id, name=body.view)
        asked = (found.get("config") or {}).get("columns")
    linked = bool(ctx.workspace.get("tva_linked"))
    if not asked:
        asked = [
            f.field
            for f in view_rules.fields_for(kind, tva_linked=linked).values()
            if f.field != "position"
        ]
    why = view_rules.validate_spec(kind, {"columns": asked}, role=ctx.role, tva_linked=linked)
    if why:
        raise CrmRefused(422, why)
    names = [c.get("field") if isinstance(c, dict) else c for c in asked]
    return [str(n) for n in dict.fromkeys(names) if n != "id"]


def _selected(ctx: Any, kind: str, ids: list[str]) -> list[dict[str, Any]]:
    """The bulk bar's rows, live ones of this workspace, in the order
    selected; an id of another workspace or in the Trash is left out."""
    if kind not in base.KINDS and kind != "campaign_member":
        raise CrmRefused(422, "That list cannot be exported by selection.")
    table = objects.TABLES[kind]
    wanted = [i for i in dict.fromkeys(ids) if base._uuid(i)]
    found = base.by_id(ctx.workspace_id, table, wanted)
    rows = [found[i] for i in wanted if i in found and not found[i].get("deleted_at")]
    return base.present_rows(base.load(ctx), kind, rows)


def view_csv(ctx: Any, body: ExportViewIn) -> tuple[str, bytes]:
    """FR-CRM-IO-04 — `(filename, bytes)` of the view as CSV: an ID column,
    then its columns in order, every row its filters keep."""
    _may_export(ctx)
    kind = views.kind_of(body.object)
    columns = _columns(ctx, kind, body)
    parts = [BOM + csv_io.csv_line(csv_io.export_headers(kind, columns))]
    size = len(parts[0].encode("utf-8"))
    count = 0

    def add(rows: list[dict[str, Any]]) -> None:
        nonlocal size, count
        for row in rows:
            line = csv_io.csv_line([row["id"], *(csv_io.cell(row, c) for c in columns)])
            size += len(line.encode("utf-8"))
            if size > csv_io.EXPORT_MAX_BYTES:
                raise _too_big()
            parts.append(line)
            count += 1

    if body.ids:
        add(_selected(ctx, kind, body.ids))
    else:
        spec = body.spec.model_copy(update={"limit": PAGE, "after": None})
        while True:
            page = views.query(
                ctx,
                QueryIn(object=kind, spec=spec, view_id=body.view_id, view=body.view),
            )
            add(page["rows"])
            if not page.get("next"):
                break
            spec = spec.model_copy(update={"after": page["next"]})
    store.record_export(
        actor(ctx),
        {
            "kind": "view",
            "object": kind,
            "table": objects.TABLES[kind],
            "view_id": body.view_id,
            "selected": bool(body.ids),
            "columns": columns,
            "rows": count,
            "bytes": size,
        },
    )
    name = csv_io.filename(_plural(kind), views.today_for(ctx.user), "csv")
    return name, "".join(parts).encode("utf-8")


def _all_rows(ctx: Any, ws: base.Workspace, kind: str) -> list[dict[str, Any]]:
    """Every live record of one object, in id order, presented as the
    record routes present it (a Viewer never gets here), `PRESENT_BATCH`
    rows at a time."""
    if kind == "review_item":
        return review.export_rows(ctx.workspace_id)
    if kind == "email_summary":
        return summaries.export_rows(ctx.workspace_id)
    if kind == "enrichment":
        return enrichment.export_rows(ctx.workspace_id)
    if kind == "ai_field":
        return ai_fields.export_fields(ctx.workspace_id)
    if kind == "ai_field_value":
        return ai_fields.export_values(ctx.workspace_id)
    rows = store.every_record(ctx.workspace_id, objects.TABLES[kind])
    if kind == "drive_link":
        return [show.drive_link(r, ws.facts) for r in rows]
    out: list[dict[str, Any]] = []
    for i in range(0, len(rows), PRESENT_BATCH):
        batch = rows[i : i + PRESENT_BATCH]
        if kind == "campaign_member":
            batch = base.shown_members(ctx.workspace_id, batch)
        out += base.present_rows(ws, kind, batch) if batch else []
    return out


def workspace_json(ctx: Any, object_name: str | None) -> tuple[str, bytes]:
    """FR-CRM-IO-04 — `(filename, bytes)` of every object of the workspace
    (or one) as JSON, for Admins."""
    if object_name is not None:
        key = object_name.replace("-", "_")
        key = key if key in WORKSPACE_OBJECTS else _plural(views.kind_of(key)).replace("-", "_")
        if key not in WORKSPACE_OBJECTS:
            raise CrmRefused(422, "Export one of: " + ", ".join(WORKSPACE_OBJECTS) + ".")
        wanted = [key]
    else:
        wanted = list(WORKSPACE_OBJECTS)
    ws = base.load(ctx)
    now = datetime.now(UTC)
    out: dict[str, Any] = {
        "workspace": {"key": ctx.workspace["key"], "name": ctx.workspace["name"]},
        "exported_at": now.isoformat(),
        "objects": {},
    }
    size = 0
    counts: dict[str, int] = {}
    for name in wanted:
        rows = _all_rows(ctx, ws, WORKSPACE_OBJECTS[name])
        out["objects"][name] = rows
        counts[name] = len(rows)
        size += len(json.dumps(rows, default=str).encode("utf-8"))
        if size > csv_io.EXPORT_MAX_BYTES:
            raise _too_big()
    data = json.dumps(out, default=str, ensure_ascii=False).encode("utf-8")
    if len(data) > csv_io.EXPORT_MAX_BYTES:
        raise _too_big()
    one = wanted[0] if object_name is not None else None
    store.record_export(
        actor(ctx),
        {
            "kind": "workspace",
            "object": one,
            "table": {**objects.TABLES, **AI_TABLES}[WORKSPACE_OBJECTS[one]] if one else None,
            "rows": counts,
            "bytes": len(data),
        },
    )
    stem = f"{ctx.workspace['key']}-{one.replace('_', '-') if one else 'workspace'}"
    return csv_io.filename(stem, views.today_for(ctx.user), "json"), data
