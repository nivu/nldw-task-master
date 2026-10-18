"""AI fields — spec 009 FR-CRMAI-FIELD-01 to -06, FR-CRMAI-JOB-01,
FR-CRMAI-FLAG-03, FR-CRMAI-CHG-11 (AI field values); Q-12, Q-13, Q-27,
Q-28, Q-38; D-3; contracts §1, §2.5; plan §5.8.

**Definitions** (Settings → AI fields, web only, Admins): name, record type,
prompt, output type, options and enabled; the four defaults are seeded by
migration 065 and may be edited or disabled, never deleted (its values stay,
hidden). Each change goes through `crm_apply`, so it is audited like any
setting (FR-CRMAI-FIELD-01).

**The job** (`crm_ai.ai_fields`, every 15 minutes, on `worker-crm-ai`), per
workspace: skipped unless phase 3 is on. For every enabled field and every
live company or deal it is defined on, the record's inputs
(`domain/crm/ai_fields.facts`: its own fields without any amount, its latest
summaries and meeting notes, its enriched values, next step dates and time
in stage, FR-CRMAI-FIELD-03) are hashed, and the value is computed through
`ai.call` when it is missing, when the hash changed or when it was marked
for recompute, at most once an hour per record; what changes inside the
hour is marked (`dirty_at`) and recomputed once at its end (Q-38). What is
blocking is computed only while Deal outlook is At risk or Stalled, else its
value is cleared without a call. At most `BATCH` model calls a run, and none once
its share of the task's time is spent (`jobs.out_of_time`, FR-CRMAI-JOB-01,
amended in S5); the rest wait for the next tick. A record that fails is counted in `failed_items` and
keeps its old value (FR-CRMAI-JOB-01). The values are written to their own
table, never to the record, and feed no stage, probability, flag, colour or
automation (FR-CRMAI-FIELD-05).

**Marking**: a stored summary, meeting note or enrichment marks the values
of the records it is linked to (`mark_dirty`), so the next tick recomputes
them; an Admin or Member asks the same for one record from its page
(`request`, `POST /crm/{companies|opportunities}/{id}/ai-fields/recompute`).

**Reading**: a company's or deal's record read carries its `ai_fields` while
phase 3 is on (`for_record`), for every role: they hold no amounts
(FR-CRMAI-FIELD-06). They are on record pages only, never a view column or
filter (Q-28), and a number is never sorted, filtered or ranked (Q-27).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

from app.domain.crm import ai_fields as rules
from app.domain.crm import ai_rules
from app.schemas.crm import AiFieldIn, AiFieldOutput, AiFieldPatch
from app.services.crm import CrmRefused, ai, jobs, store
from app.services.crm import records as base
from app.services.crm.context import actor

logger = logging.getLogger("nldw-task-master")

#: Model calls per workspace per run; the rest wait for the next tick.
BATCH: Final = 50
NO_SUCH: Final = "No such AI field."
#: The columns a definition shows (contracts §2.5).
DEFINITION_COLUMNS: Final = (
    "id",
    "key",
    "name",
    "record_type",
    "prompt",
    "output_type",
    "options",
    "enabled",
)
NOUNS: Final = {"company": "companies", "opportunity": "deals"}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Definitions — FR-CRMAI-FIELD-01; contracts §2.5; D-3
# ---------------------------------------------------------------------------


def _definition(row: dict[str, Any]) -> dict[str, Any]:
    return {c: row.get(c) for c in DEFINITION_COLUMNS} | {"options": list(row.get("options") or [])}


def list_fields(ctx: Any) -> list[dict[str, Any]]:
    """Every field of the workspace, enabled or not, companies' first."""
    rows = store.list_ai_fields(ctx.workspace_id)
    rows.sort(key=lambda f: (f["record_type"] != "company", f.get("position") or 0, f["name"]))
    return [_definition(r) for r in rows]


def _field(ctx: Any, field_id: str) -> dict[str, Any]:
    row = store.get_ai_field(ctx.workspace_id, field_id) if base._uuid(field_id) else None
    if row is None:
        raise CrmRefused(404, NO_SUCH)
    return row


def _name_taken(rows: list[dict[str, Any]], record_type: str, name: str, but: str | None) -> None:
    if any(
        r["record_type"] == record_type and r["name"].casefold() == name.casefold()
        for r in rows
        if r["id"] != but
    ):
        raise CrmRefused(
            409, f"There is already an AI field with that name on {NOUNS[record_type]}."
        )


def create_field(ctx: Any, body: AiFieldIn) -> dict[str, Any]:
    """A new field of the Admin's: `max_lines`, `depends_on`, `with_reason`
    and `position` keep their defaults (D-3), the position after the last."""
    options = rules.clean_options(body.options)
    why = rules.options_refusal(body.output_type, options)
    if why:
        raise CrmRefused(422, why)
    rows = store.list_ai_fields(ctx.workspace_id)
    _name_taken(rows, body.record_type, body.name, None)
    values = {
        "key": rules.key_for(body.name, {r["key"] for r in rows}),
        "name": body.name,
        "record_type": body.record_type,
        "prompt": body.prompt,
        "output_type": body.output_type,
        "options": options,
        "enabled": body.enabled,
        "position": max((float(r.get("position") or 0) for r in rows), default=0) + 1,
    }
    [made] = store.apply(
        actor(ctx),
        [
            {
                "op": "insert",
                "table": "crm_ai_fields",
                "values": values,
                "action": "ai_field.created",
            }
        ],
    )
    return _definition(made["row"])


def update_field(ctx: Any, field_id: str, body: AiFieldPatch) -> dict[str, Any]:
    """Name, prompt, options or enabled; the record and output types stay."""
    row = _field(ctx, field_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("name") is None:
        values.pop("name", None)
    if values.get("prompt") is None:
        values.pop("prompt", None)
    if values.get("enabled") is None:
        values.pop("enabled", None)
    if "options" in values:
        values["options"] = rules.clean_options(values["options"])
        why = rules.options_refusal(row["output_type"], values["options"])
        if why:
            raise CrmRefused(422, why)
    if "name" in values:
        _name_taken(
            store.list_ai_fields(ctx.workspace_id), row["record_type"], values["name"], row["id"]
        )
    if not values:
        raise CrmRefused(422, "Nothing to change.")
    [made] = store.apply(
        actor(ctx),
        [
            {
                "op": "update",
                "table": "crm_ai_fields",
                "id": row["id"],
                "values": values,
                "action": "ai_field.updated",
            }
        ],
    )
    return _definition(made["row"])


# ---------------------------------------------------------------------------
# The job — FR-CRMAI-FIELD-03 to -05, Q-38
# ---------------------------------------------------------------------------


def _summaries(ws_id: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Each company's and deal's summaries, latest first."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in store.summaries_for(ws_id):
        for link in row["links"]:
            if link["record_type"] in rules.TABLES:
                out.setdefault((link["record_type"], link["record_id"]), []).append(row)
    return out


def _meeting_notes(ws_id: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Each company's and deal's live meeting notes, latest first."""
    notes = {
        n["id"]: n for n in store.every_record(ws_id, "crm_notes") if n.get("kind") == "meeting"
    }
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if not notes:
        return out
    for link in store.list_links(ws_id, "crm_note_links", owner_ids=sorted(notes)):
        if link["record_type"] in rules.TABLES:
            out.setdefault((link["record_type"], link["record_id"]), []).append(
                notes[link["note_id"]]
            )
    for rows in out.values():
        rows.sort(
            key=lambda n: (str(n.get("occurred_on") or ""), str(n["created_at"])), reverse=True
        )
    return out


def run(workspace: dict[str, Any], now: datetime) -> dict[str, int]:
    """One `ai_fields` run of one workspace: `{records, computed, cleared,
    waiting, failed_items}`."""
    ws_id = workspace["id"]
    if not ai_rules.phase_on(store.ai_setting(ws_id), rules.PHASE):
        raise jobs.Skipped(ai_rules.SWITCHED_OFF)
    counts = {"records": 0, "computed": 0, "cleared": 0, "waiting": 0, "failed_items": 0}
    fields = [f for f in store.list_ai_fields(ws_id) if f["enabled"]]
    if not fields:
        return counts
    labels = {o["id"]: o["label"] for o in store.list_options(ws_id)}
    values = {(v["field_id"], v["record_id"]): v for v in store.ai_field_values(ws_id)}
    summaries = _summaries(ws_id)
    notes = _meeting_notes(ws_id)
    enriched = {
        e["company_id"]: e for e in store.enrichment_for(ws_id, None) if e["status"] == "done"
    }
    companies = {c["id"]: c for c in store.every_record(ws_id, "crm_companies")}
    budget = {"calls": 0}
    for kind, table in rules.TABLES.items():
        mine = rules.ordered([f for f in fields if f["record_type"] == kind])
        if not mine:
            continue
        records = (
            list(companies.values()) if kind == "company" else store.every_record(ws_id, table)
        )
        for record in records:
            counts["records"] += 1
            company = (
                companies.get(record.get("company_id") or "") if kind == "opportunity" else None
            )
            facts = rules.facts(
                kind,
                record,
                labels=labels,
                company_name=company["name"] if company else None,
                summaries=summaries.get((kind, record["id"]), []),
                notes=notes.get((kind, record["id"]), []),
                enrichment=enriched.get(record["id"]) if kind == "company" else None,
            )
            _record(workspace, kind, record, mine, facts, values, counts, budget, now)
    return counts


def _record(
    workspace: dict[str, Any],
    kind: str,
    record: dict[str, Any],
    fields: list[dict[str, Any]],
    facts: dict[str, Any],
    values: dict[tuple[str, str], dict[str, Any]],
    counts: dict[str, int],
    budget: dict[str, int],
    now: datetime,
) -> None:
    """One record's fields, in order, so a field that depends on another
    reads the value this run left it."""
    ws_id = workspace["id"]
    by_key = {f["key"]: f for f in fields}
    current = {f["key"]: values.get((f["id"], record["id"])) for f in fields}
    for field in fields:
        row = current[field["key"]]
        applies, depends = rules.dependency(field, by_key, current)
        new_hash = rules.inputs_hash(field, facts, depends)
        base_row = {"field_id": field["id"], "record_type": kind, "record_id": record["id"]}
        if not applies:
            # The FR-CRMAI-FIELD table: What is blocking only while Deal
            # outlook is At risk or Stalled; else its value is cleared.
            if row is not None and not (rules.cleared(row) and row["inputs_hash"] == new_hash):
                cleared = {
                    **base_row,
                    "value_text": None,
                    "value_number": None,
                    "reason": None,
                    "inputs_hash": new_hash,
                    "model": None,
                    "generated_at": now.isoformat(),
                    "dirty_at": None,
                    "next_allowed_at": row["next_allowed_at"],
                }
                store.write_ai_field_value(ws_id, cleared)
                current[field["key"]] = cleared
                counts["cleared"] += 1
            continue
        decision = rules.decide(row, new_hash, now)
        if decision == rules.SKIP:
            continue
        if decision == rules.WAIT:
            # Q-38: the trailing recompute at the end of the hour.
            if not row.get("dirty_at"):
                store.mark_ai_fields_dirty(
                    ws_id, kind, [record["id"]], now, field_ids=[field["id"]]
                )
                current[field["key"]] = {**row, "dirty_at": now.isoformat()}
            counts["waiting"] += 1
            continue
        if budget["calls"] >= BATCH or jobs.out_of_time():
            # The rest wait for the next tick.
            continue
        budget["calls"] += 1
        try:
            written = _compute(workspace, field, facts, depends, new_hash, base_row, now)
        except jobs.Skipped as skipped:
            # The budget or a switch stops the whole run, which keeps its counts.
            raise jobs.Skipped(skipped.reason, counts) from None
        except Exception as exc:  # noqa: BLE001 - one record never fails the run
            # The old value stays; only the kind of failure is logged (a
            # database error's text can hold the row, FR-CRMAI-JOB-03).
            counts["failed_items"] += 1
            logger.warning(
                '{"event": "crm_ai_field_failed", "workspace": "%s", "error": "%s"}',
                workspace.get("key", "-"),
                type(exc).__name__,
            )
            continue
        current[field["key"]] = written
        counts["computed"] += 1


def _compute(
    workspace: dict[str, Any],
    field: dict[str, Any],
    facts: dict[str, Any],
    depends: dict[str, Any] | None,
    new_hash: str,
    base_row: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    output = ai.call(
        workspace,
        rules.JOB,
        rules.PHASE,
        AiFieldOutput.for_field(
            field["output_type"], field.get("options") or [], with_reason=field["with_reason"]
        ),
        rules.instruction(field),
        rules.prompt_parts(facts, now, depends),
        now,
    )
    # Every string came masked from ai.call (FR-CRMAI-LLM-05).
    answer = rules.stored(field, output.model_dump(mode="json"))
    if answer is None:
        raise ai.AiFailed(ai_rules.OUTPUT_INVALID, "Schema validation", "ValidationError")
    row = {
        **base_row,
        **answer,
        "inputs_hash": new_hash,
        "model": ai_rules.CRM_AI_MODEL,
        "generated_at": now.isoformat(),
        "dirty_at": None,
        "next_allowed_at": rules.next_allowed(now).isoformat(),
    }
    store.write_ai_field_value(workspace["id"], row)
    return row


# ---------------------------------------------------------------------------
# Marking — FR-CRMAI-FIELD-04, Q-38
# ---------------------------------------------------------------------------


def mark_dirty(workspace_id: str, links: list[dict[str, Any]], now: datetime) -> None:
    """A summary, meeting note or enrichment was stored: the AI field values
    of the companies and deals it is linked to are recomputed by the next
    tick, or at the end of their hour. Never raises: the hash of the inputs
    finds the change anyway."""
    try:
        for kind in rules.TABLES:
            ids = sorted({lk["record_id"] for lk in links if lk["record_type"] == kind})
            if ids:
                store.mark_ai_fields_dirty(workspace_id, kind, ids, now)
    except Exception as exc:  # noqa: BLE001 - the next tick's hash catches it
        logger.warning('{"event": "crm_ai_fields_mark_failed", "error": "%s"}', type(exc).__name__)


def request(ctx: Any, kind: str, record_id: str) -> dict[str, Any]:
    """Contracts §2.5 recompute: `{queued, next_allowed_at}`. The record's
    values are marked; the next tick recomputes them when their hour is over
    (`queued`), else at its end (`next_allowed_at`, Q-38). Another
    workspace's record, or one in the Trash, is not found."""
    record = base.live(ctx.workspace_id, kind, record_id)
    now = _now()
    rows = store.ai_field_values(ctx.workspace_id, kind, [record["id"]])
    store.mark_ai_fields_dirty(ctx.workspace_id, kind, [record["id"]], now)
    latest = max((rules.at(r["next_allowed_at"]) for r in rows), default=None)
    queued = latest is None or latest <= now
    return {"queued": queued, "next_allowed_at": None if queued else latest.isoformat()}


# ---------------------------------------------------------------------------
# Reading — contracts §1; FR-CRMAI-FIELD-05, -06, FR-CRMAI-FLAG-03
# ---------------------------------------------------------------------------


def shown(workspace_id: str) -> bool:
    """FR-CRMAI-FLAG-03 — AI fields are read only while phase 3 is on."""
    return ai_rules.phase_on(store.ai_setting(workspace_id), rules.PHASE)


def for_record(ctx: Any, kind: str, record_id: str) -> list[dict[str, Any]]:
    """A company's or deal's `ai_fields` (contracts §1): `[{key, name,
    output_type, value, reason?, ai}]`, one per enabled field in order;
    `value` and `ai` are None before the first computation or once the
    value is cleared. `reason` is there for a field that gives one."""
    fields = [
        f
        for f in store.list_ai_fields(ctx.workspace_id)
        if f["enabled"] and f["record_type"] == kind
    ]
    found = {v["field_id"]: v for v in store.ai_field_values(ctx.workspace_id, kind, [record_id])}
    out = []
    for field in fields:
        row = found.get(field["id"])
        value = rules.shown(field, row)
        item: dict[str, Any] = {
            "key": field["key"],
            "name": field["name"],
            "output_type": field["output_type"],
            "value": value,
            "ai": (
                {
                    "source_kind": rules.SOURCE_KIND,
                    "model": row["model"],
                    "generated_at": row["generated_at"],
                    "source": None,
                }
                if row and value is not None
                else None
            ),
        }
        if field["with_reason"]:
            item["reason"] = row.get("reason") if row and value is not None else None
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# The workspace export — FR-CRMAI-CHG-11, data-model §4
# ---------------------------------------------------------------------------

#: The columns an exported field carries.
EXPORT_FIELD_COLUMNS: Final = (
    "id",
    "key",
    "name",
    "record_type",
    "prompt",
    "output_type",
    "options",
    "max_lines",
    "depends_on",
    "with_reason",
    "enabled",
    "position",
    "created_at",
    "updated_at",
)
#: The columns an exported value carries: ids, never a name.
EXPORT_VALUE_COLUMNS: Final = (
    "id",
    "field_id",
    "record_type",
    "record_id",
    "value_text",
    "value_number",
    "reason",
    "model",
    "generated_at",
)


def export_fields(workspace_id: str) -> list[dict[str, Any]]:
    """Every field of the workspace, enabled or not."""
    return [{c: r.get(c) for c in EXPORT_FIELD_COLUMNS} for r in store.list_ai_fields(workspace_id)]


def export_values(workspace_id: str) -> list[dict[str, Any]]:
    """Every value of the workspace; one of a record in the Trash is left
    out (data-model §4)."""
    rows = store.ai_field_values(workspace_id)
    live: set[tuple[str, str]] = set()
    for kind, table in rules.TABLES.items():
        ids = {r["record_id"] for r in rows if r["record_type"] == kind}
        live |= {
            (kind, rid)
            for rid, rec in base.by_id(workspace_id, table, ids).items()
            if not rec.get("deleted_at")
        }
    return [
        {c: r.get(c) for c in EXPORT_VALUE_COLUMNS}
        for r in rows
        if (r["record_type"], r["record_id"]) in live
    ]
