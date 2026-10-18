"""Company enrichment — spec 009 FR-CRMAI-ENR-01 to -08, FR-CRMAI-JOB-01,
FR-CRMAI-MCP-02, -04, FR-CRMAI-CHG-11 (enrichment); NFR-CRMAI-01, -06; Q-30
to Q-33; D-2, D-3; contracts §1, §2.4; plan §5.1, §5.7.

**Queuing** is the database's: migration 064's trigger queues a company
when it is created with a website or a domain, by any path, and again when
its website or primary domain changes (FR-CRMAI-ENR-01, Q-32). An import or
a web request does nothing more (FR-CRMAI-ENR-07). An Admin's **Enrich
selected** (`request`, `POST /crm/enrichment/run`) queues the selected
companies again, also those already enriched from the same site
(FR-CRMAI-ENR-08).

**The job** follows email sync's pattern (007 S10), on `worker-crm-ai`:

- the tick (`crm_ai.enrichment`, every 15 minutes) records, per workspace,
  a skip while phase 2 is off, an empty run when nothing is queued, or
  starts a run: it takes the workspace's lock (Redis `SET NX EX 900`, so two
  runs never read at once) and enqueues the first batch with the run id;
- each batch (`crm_ai.enrichment_batch`) enriches at most 10 companies
  queued before the run began, then enqueues the next with `countdown=60`,
  so a workspace is read at most 10 companies a minute (FR-CRMAI-ENR-07),
  and summaries and meetings run between batches on the one AI slot. The
  last batch (queue empty, budget reached or phase switched off) finishes
  the run and releases the lock. A run stopped by the budget or the switch
  leaves the rest queued, and the next tick continues from there without
  reading a done company again.

For one company: its own site is read (`web_fetch.read_site`, at most 5
pages, never a private address, Q-30, Q-31), Claude answers through
`ai.call` (phase, rates, budget, quoting, schema, money masking, links kept
only on that site or LinkedIn), and the values are written to the
enrichment row with their provenance, never to a person-entered field
(FR-CRMAI-ENR-03). A field already filled with something else gets an
`enrichment_conflict` item through `review.upsert` (FR-CRMAI-ENR-05). A site
that cannot be read (its address refused included) is recorded failed
"Website did not respond"; one that says nothing usable, "No company
information found"; a failed model call, its reason (FR-CRMAI-ENR-06, D-2).
Any other failure is counted in `failed_items` and the company waits for
the next run. Page text lives only in local variables (P-3).

**Reading**: a company's record read carries its `enrichment` while phase 2
is on (`for_company`): the values, status, what may be promoted and the
open conflict items. Promoting is `PATCH /crm/companies/{id}`, an ordinary
audited edit (FR-CRMAI-ENR-04, FR-CRMAI-MCP-04).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.config import settings
from app.domain.crm import ai_rules
from app.domain.crm import enrichment as rules
from app.domain.crm import review as review_rules
from app.schemas.crm import EnrichmentOutput
from app.services import supabase as db
from app.services.crm import CrmRefused, ai, ai_fields, jobs, review, store, web_fetch
from app.services.crm import records as base

logger = logging.getLogger("nldw-task-master")

JOB: Final = rules.JOB
#: FR-CRMAI-ENR-07 — at most 10 companies a batch, a minute apart.
BATCH: Final = 10
INTERVAL_SECONDS: Final = 60
#: A batch stops taking companies after this long, well inside Celery's
#: 600-second task limit; the rest wait for the next batch.
BATCH_SECONDS: Final = 400
#: The run's single-flight lock, per workspace, kept alive by each batch.
LOCK_KEY: Final = "crm:enrichment:lock:{workspace_id}"
LOCK_SECONDS: Final = 900
RUNNING: Final = "Enrichment is already running"
NO_SUCH: Final = "No such company."
#: A run's counts (FR-CRMAI-JOB-01): item counts only.
EMPTY: Final = {"companies": 0, "enriched": 0, "failed": 0, "items": 0, "failed_items": 0}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# The lock and the queue — plan §5.1
# ---------------------------------------------------------------------------


def _redis():
    import redis

    return redis.Redis.from_url(str(settings.REDIS_URL), socket_timeout=5)


def acquire_lock(workspace_id: str, token: str) -> bool:
    """`SET NX EX 900`: true when this run now holds the workspace's lock."""
    key = LOCK_KEY.format(workspace_id=workspace_id)
    return bool(_redis().set(key, token, nx=True, ex=LOCK_SECONDS))


def extend_lock(workspace_id: str, token: str) -> None:
    """Each batch keeps the run's lock alive; a crashed run's lock lapses
    15 minutes after its last batch."""
    key = LOCK_KEY.format(workspace_id=workspace_id)
    client = _redis()
    if (client.get(key) or b"").decode() == token:
        client.expire(key, LOCK_SECONDS)


def release_lock(workspace_id: str, token: str) -> None:
    key = LOCK_KEY.format(workspace_id=workspace_id)
    client = _redis()
    if (client.get(key) or b"").decode() == token:
        client.delete(key)


def enqueue(args: dict[str, Any], countdown: int) -> None:
    """The next batch, onto the `crm-ai` queue (the task's route). Its
    arguments are ids, a time and counts, never text (FR-CRMAI-JOB-02)."""
    from app.tasks.crm_ai import enrichment_batch

    enrichment_batch.apply_async(args=[args], countdown=countdown)


# ---------------------------------------------------------------------------
# The tick — FR-CRMAI-ENR-07, FR-CRMAI-JOB-01
# ---------------------------------------------------------------------------


def _skip(reason: str):
    def work(workspace: Any, now: datetime) -> dict[str, Any]:
        raise jobs.Skipped(reason)

    return work


def _fail(exc: BaseException):
    def work(workspace: Any, now: datetime) -> dict[str, Any]:
        raise exc

    return work


def _start(ws: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not ai_rules.phase_on(store.ai_setting(ws["id"]), rules.PHASE):
        return jobs.run(JOB, ws, _skip(ai_rules.SWITCHED_OFF), now)
    if not store.queued_enrichment(ws["id"], 1, before=now):
        return jobs.run(JOB, ws, lambda workspace, at: dict(EMPTY), now)
    token = str(uuid.uuid4())
    try:
        held = acquire_lock(ws["id"], token)
    except Exception as exc:
        logger.warning(
            '{"event": "crm_enrichment_lock_failed", "workspace": "%s", "error": "%s"}',
            ws["key"],
            type(exc).__name__,
        )
        return jobs.run(JOB, ws, _fail(exc), now)
    if not held:
        return jobs.run(JOB, ws, _skip(RUNNING), now)
    run = store.start_job_run(JOB, ws["id"], now)
    try:
        enqueue(
            {
                "workspace_id": ws["id"],
                "run_id": run["id"],
                "cutoff": now.isoformat(),
                "lock": token,
                "counts": dict(EMPTY),
            },
            0,
        )
    except Exception as exc:
        logger.warning(
            '{"event": "crm_enrichment_enqueue_failed", "workspace": "%s", "error": "%s"}',
            ws["key"],
            type(exc).__name__,
        )
        failed = jobs.failure(JOB, exc)
        jobs.finish(JOB, ws, run["id"], failed, now)
        release_lock(ws["id"], token)
        return {"outcome": "failed", "counts": {}}
    return {"outcome": "running", "run_id": run["id"]}


def tick(now: datetime | None = None) -> dict[str, Any]:
    """Every 15 minutes: a run, an empty run or a recorded skip in every
    workspace. `{runs: {workspace key: {outcome, ...}}, failed: [key]}`."""
    now = now or _now()
    runs = {ws["key"]: _start(ws, now) for ws in store.list_workspaces()}
    return {"runs": runs, "failed": [k for k, r in runs.items() if r["outcome"] == "failed"]}


# ---------------------------------------------------------------------------
# A batch — FR-CRMAI-ENR-02 to -07
# ---------------------------------------------------------------------------


def _options(workspace_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The workspace's active industry and size options, in their order."""
    rows = [o for o in store.list_options(workspace_id) if o.get("is_active", True)]
    rows.sort(key=lambda o: (o.get("position") or 0, o["label"]))
    return (
        [o for o in rows if o["list_key"] == rules.INDUSTRY_LIST],
        [o for o in rows if o["list_key"] == rules.SIZE_LIST],
    )


def batch(args: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """At most `BATCH` queued companies of one workspace's run, then the next
    batch a minute later, or the run's end. Never raises."""
    now = now or _now()
    ws_id, run_id, token = args["workspace_id"], args["run_id"], args["lock"]
    cutoff = datetime.fromisoformat(args["cutoff"])
    counts = {**EMPTY, **(args.get("counts") or {})}
    ws = next((w for w in store.list_workspaces() if w["id"] == ws_id), None)
    began = time.monotonic()
    try:
        extend_lock(ws_id, token)
    except Exception as exc:  # noqa: BLE001 - the run goes on; the lock lapses later
        logger.warning(
            '{"event": "crm_enrichment_lock_extend_failed", "error": "%s"}', type(exc).__name__
        )
    try:
        if ws is None:
            raise RuntimeError("The workspace no longer exists.")
        if not ai_rules.phase_on(store.ai_setting(ws_id), rules.PHASE):
            raise jobs.Skipped(ai_rules.SWITCHED_OFF)
        rows = store.queued_enrichment(ws_id, BATCH, before=cutoff)
        if rows:
            _work(ws, rows, counts, cutoff, now, began)
        more = bool(store.queued_enrichment(ws_id, 1, before=cutoff))
    except jobs.Skipped as skipped:
        # FR-CRMAI-ENR-07: the rest stays queued for the next run.
        _end(
            ws,
            ws_id,
            run_id,
            token,
            {"outcome": "skipped", "counts": {**counts, "skipped": skipped.reason}},
            now,
        )
        return {"done": True}
    except Exception as exc:
        logger.warning(
            '{"event": "crm_enrichment_batch_failed", "workspace": "%s", "error": "%s"}',
            (ws or {}).get("key", "-"),
            type(exc).__name__,
        )
        _end(ws, ws_id, run_id, token, {**jobs.failure(JOB, exc), "counts": counts}, now)
        return {"done": True}
    if not more:
        _end(ws, ws_id, run_id, token, {"outcome": "ok", "counts": counts}, now)
        return {"done": True}
    try:
        enqueue({**args, "counts": counts}, INTERVAL_SECONDS)
    except Exception as exc:
        # The broker is gone: the rest waits, queued, for the next tick.
        logger.warning(
            '{"event": "crm_enrichment_enqueue_failed", "workspace": "%s", "error": "%s"}',
            ws["key"],
            type(exc).__name__,
        )
        _end(ws, ws_id, run_id, token, {**jobs.failure(JOB, exc), "counts": counts}, now)
        return {"done": True}
    return {"done": False}


def _end(
    ws: dict[str, Any] | None,
    ws_id: str,
    run_id: str,
    token: str,
    values: dict[str, Any],
    now: datetime,
) -> None:
    jobs.finish(JOB, ws, run_id, values, now)
    try:
        release_lock(ws_id, token)
    except Exception as exc:  # noqa: BLE001 - it lapses on its own
        logger.warning(
            '{"event": "crm_enrichment_lock_release_failed", "error": "%s"}', type(exc).__name__
        )


def _work(
    ws: dict[str, Any],
    rows: list[dict[str, Any]],
    counts: dict[str, Any],
    cutoff: datetime,
    now: datetime,
    began: float,
) -> None:
    ws_id = ws["id"]
    industries, sizes = _options(ws_id)
    members = store.list_members(ws_id)
    roles = {m["user_id"]: m["role"] for m in members}
    profiles = db.list_profiles(include_crm_only=True)
    active = {p["id"] for p in profiles if p.get("is_active", False)}
    companies = base.by_id(ws_id, "crm_companies", {r["company_id"] for r in rows})
    for row in rows:
        if time.monotonic() - began > BATCH_SECONDS:
            break
        company = companies.get(row["company_id"])
        if company is None or company.get("deleted_at"):
            continue
        counts["companies"] += 1
        try:
            made = _enrich(ws, row, company, industries, sizes, roles, active, now)
        except jobs.Skipped:
            # The budget or a switch stops the whole run; the company waits.
            raise
        except Exception as exc:  # noqa: BLE001 - one company never fails the run
            # Only the kind of failure is logged (a database error's text can
            # hold the row, FR-CRMAI-JOB-03). The company waits for the next
            # run: queued after this one began, so this run does not retry it.
            counts["failed_items"] += 1
            logger.warning(
                '{"event": "crm_enrichment_failed", "workspace": "%s", "error": "%s"}',
                ws.get("key", "-"),
                type(exc).__name__,
            )
            later = max(now, cutoff + timedelta(microseconds=1))
            try:
                store.write_enrichment(
                    ws_id, row["company_id"], row["website"], {"queued_at": later.isoformat()}
                )
            except Exception:  # noqa: BLE001 - the next run takes it anyway
                logger.warning('{"event": "crm_enrichment_requeue_failed"}')
            continue
        if made["status"]:
            counts[made["status"]] += 1
        counts["items"] += made["items"]


def _failed(ws_id: str, row: dict[str, Any], reason: str) -> dict[str, Any]:
    """FR-CRMAI-ENR-06: failed with a reason from the fixed list, nothing
    guessed."""
    written = store.write_enrichment(
        ws_id,
        row["company_id"],
        row["website"],
        {
            "status": "failed",
            "failure_reason": reason,
            "values": {},
            "model": None,
            "generated_at": None,
        },
    )
    return {"status": "failed" if written else None, "items": 0}


def _enrich(
    ws: dict[str, Any],
    row: dict[str, Any],
    company: dict[str, Any],
    industries: list[dict[str, Any]],
    sizes: list[dict[str, Any]],
    roles: dict[str, str],
    active: set[str],
    now: datetime,
) -> dict[str, Any]:
    """One company: `{status: "enriched" | "failed" | None, items}`; None when
    its website changed meanwhile and the new site is queued instead."""
    ws_id, site = ws["id"], row["website"]
    try:
        # FR-CRMAI-ENR-02: the company's own site, in memory only.
        read = web_fetch.read_site(site, job=JOB, workspace=ws.get("key") or "-")
    except web_fetch.Unreadable:
        return _failed(ws_id, row, rules.NO_RESPONSE)
    if not any(text.strip() for _, text in read.pages):
        return _failed(ws_id, row, rules.NO_INFO)
    parts = rules.prompt_parts(read.pages, read.links, industries, sizes)
    del read
    try:
        output = ai.call(
            ws,
            JOB,
            rules.PHASE,
            EnrichmentOutput,
            rules.INSTRUCTION,
            parts,
            now,
            site=site,
        )
    except ai.AiFailed as failed:
        return _failed(ws_id, row, failed.reason)
    finally:
        del parts
    # Every string came masked from ai.call (FR-CRMAI-LLM-05).
    values = rules.values_from(output.model_dump(mode="json"), industries, sizes)
    if not rules.usable(values):
        return _failed(ws_id, row, rules.NO_INFO)
    generated_at = now.isoformat()
    written = store.write_enrichment(
        ws_id,
        row["company_id"],
        site,
        {
            "status": "done",
            "failure_reason": None,
            "values": values,
            "model": ai_rules.CRM_AI_MODEL,
            "generated_at": generated_at,
        },
    )
    if not written:
        return {"status": None, "items": 0}
    # Spec 009 FR-CRMAI-FIELD-04: the company's AI fields are due.
    ai_fields.mark_dirty(ws_id, [{"record_type": "company", "record_id": row["company_id"]}], now)
    found = rules.conflicts(company, values, industries, sizes)
    made = review.upsert(
        ws_id,
        rules.items(
            company,
            found,
            website=site,
            model=ai_rules.CRM_AI_MODEL,
            generated_at=generated_at,
            roles=roles,
            active=active,
        ),
    )
    return {"status": "enriched", "items": sum(1 for m in made if m.get("created"))}


# ---------------------------------------------------------------------------
# Enrich selected — contracts §2.4; FR-CRMAI-ENR-01, -08
# ---------------------------------------------------------------------------


def request(ctx: Any, company_ids: list[str]) -> dict[str, int]:
    """The selected companies queued again, for the next run: `{queued,
    skipped_no_website, already_enriched}`, where `already_enriched` counts
    the queued ones already enriched from the same site (an Admin asking
    again re-reads them, FR-CRMAI-ENR-08). Another workspace's company, or
    one in the Trash, is not found. Nothing is read here (FR-CRMAI-ENR-07)."""
    ws_id = ctx.workspace_id
    wanted = list(dict.fromkeys(company_ids))
    if not all(base._uuid(i) for i in wanted):
        raise CrmRefused(404, NO_SUCH)
    found = base.by_id(ws_id, "crm_companies", wanted)
    if any(i not in found or found[i].get("deleted_at") for i in wanted):
        raise CrmRefused(404, NO_SUCH)
    primary = {
        d["company_id"]: d["domain"]
        for d in store.list_domains(ws_id, company_ids=wanted)
        if d["is_primary"]
    }
    existing = {r["company_id"]: r for r in store.enrichment_for(ws_id, wanted)}
    plan: list[dict[str, Any]] = []
    counts = {"queued": 0, "skipped_no_website": 0, "already_enriched": 0}
    for company_id in wanted:
        site = rules.site(found[company_id].get("website"), primary.get(company_id))
        if site is None:
            counts["skipped_no_website"] += 1
            continue
        before = existing.get(company_id) or {}
        same_site = before.get("website") == site
        if same_site and before.get("status") == "done":
            counts["already_enriched"] += 1
        plan.append(
            {
                "company_id": company_id,
                "website": site,
                "requested_by": ctx.user.id,
                # A new site's old values describe another site (Q-32).
                "values": (before.get("values") or {}) if same_site else {},
                "model": before.get("model") if same_site else None,
                "generated_at": before.get("generated_at") if same_site else None,
            }
        )
    store.requeue_enrichment(ws_id, plan, _now())
    counts["queued"] = len(plan)
    logger.info(
        '{"event": "crm_enrichment_requested", "workspace": "%s", "queued": %d}',
        ctx.workspace["key"],
        counts["queued"],
    )
    return counts


# ---------------------------------------------------------------------------
# Reading — contracts §1; FR-CRMAI-ENR-04, -05, FR-CRMAI-FLAG-03
# ---------------------------------------------------------------------------


def shown(workspace_id: str) -> bool:
    """FR-CRMAI-FLAG-03 — enrichment is read only while phase 2 is on."""
    return ai_rules.phase_on(store.ai_setting(workspace_id), rules.PHASE)


def for_company(ctx: Any, company: dict[str, Any]) -> dict[str, Any] | None:
    """A company's `enrichment` (contracts §1): `{status, failure_reason?,
    values, promotable: {field: bool}, conflicts: [item ids], ai}`, or None
    when it has never been queued. `conflicts` are the open
    `enrichment_conflict` items the caller may see (none for a Viewer)."""
    rows = store.enrichment_for(ctx.workspace_id, [company["id"]])
    if not rows:
        return None
    row = rows[0]
    industries, sizes = _options(ctx.workspace_id)
    stored = row.get("values") or {}
    values = {k: stored.get(k) for k in rules.VALUE_KEYS}
    values["products"] = stored.get("products") or []
    out: dict[str, Any] = {
        "status": row["status"],
        "values": values,
        "promotable": rules.promotable(company, stored, industries, sizes),
        "conflicts": [
            item["id"]
            for item in store.list_review_items(ctx.workspace_id, status="open")
            if item["type"] == "enrichment_conflict"
            and item["record_type"] == "company"
            and item["record_id"] == company["id"]
            and review_rules.may_see(ctx.role, ctx.user.id, item)
        ],
        "ai": (
            {
                "source_kind": rules.SOURCE_KIND,
                "model": row["model"],
                "generated_at": row["generated_at"],
                "source": None,
            }
            if row.get("generated_at")
            else None
        ),
    }
    if row["status"] == "failed":
        out["failure_reason"] = row["failure_reason"]
    return out


# ---------------------------------------------------------------------------
# The workspace export — FR-CRMAI-CHG-11, data-model §4
# ---------------------------------------------------------------------------

#: The columns an exported enrichment row carries: ids, never a name.
EXPORT_COLUMNS = (
    "id",
    "company_id",
    "website",
    "status",
    "failure_reason",
    "values",
    "model",
    "generated_at",
    "queued_at",
    "requested_by",
    "created_at",
)


def export_rows(workspace_id: str) -> list[dict[str, Any]]:
    """Every enrichment row of the workspace; one of a company in the Trash
    is left out."""
    rows = store.enrichment_for(workspace_id, None)
    companies = base.by_id(workspace_id, "crm_companies", {r["company_id"] for r in rows})
    return [
        {c: r.get(c) for c in EXPORT_COLUMNS}
        for r in rows
        if (company := companies.get(r["company_id"])) and not company.get("deleted_at")
    ]
