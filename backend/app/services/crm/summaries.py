"""Email thread summaries — spec 009 FR-CRMAI-SUM-01 to -12, FR-CRMAI-CHG-01,
FR-CRMAI-MCP-01 (`crm_thread_summary`), -02 (summaries); NFR-CRMAI-01, -05;
contracts §1, §2.3; plan §5.5.

**The job** (`crm_ai.summaries`, every 15 minutes, on `worker-crm-ai`), per
workspace: skipped unless phase 1 is on and Gmail sync is live. It takes the
threads due a summary (`crm_pending_threads`, migration 062), and for each:

1. reads, with the read-only Gmail scope, the text of **only the messages
   the sync stored** for the thread (`gmail.message_text`, never the Gmail
   thread itself, FR-CRMAI-SUM-03);
2. strips quoted reply history and signatures (`domain/crm/summaries.strip`);
3. asks Claude through `ai.call` (phase, rates, budget, quoting, schema,
   money masking), with the team list it names owners from;
4. builds the Review Queue items (`domain/crm/summaries.items`) and stores
   summary, links and items in one transaction (`crm_store_summary`), which
   re-checks that no message arrived meanwhile.

The text lives only in this module's local variables: it is never stored,
logged, queued or put in an error. A thread that fails (the call, the
schema, Gmail, a message stored meanwhile, the database) is counted in `failed_items` and
stays pending for the next tick; the run itself is not failed
(FR-CRMAI-JOB-01). A thread with no new message is not pending, so a second
run makes no call and changes nothing (FR-CRMAI-SUM-09). The run takes no
more threads once its share of the task's time is spent (`jobs.out_of_time`,
FR-CRMAI-JOB-01, amended in S5).

**Reading**: a record's latest summaries (`for_record`), a thread's summary
on the Emails tab (`for_threads`) and by its thread key (`by_key`). While
phase 1 is off the summaries stay stored and are hidden from every read
(FR-CRMAI-FLAG-03); a summary linked only to records in the Trash is left
out (FR-CRMAI-CHG-11). Every member of the workspace reads them, Viewers
included (FR-CRMAI-SUM-08): their text was masked for money before it was
stored (FR-CRMAI-LLM-05).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

from app.domain.crm import ai_rules
from app.domain.crm import summaries as rules
from app.schemas.crm import SummaryOutput, ThreadSummaryOut
from app.services import supabase as db
from app.services.crm import CrmRefused, ai, ai_fields, email_sync, gmail, jobs, store
from app.services.crm import records as base

logger = logging.getLogger("nldw-task-master")

#: Threads summarised per workspace per tick; the rest wait for the next.
BATCH: Final = 20
#: A record's summaries (contracts §1: the latest five threads).
RECORD_LIMIT: Final = 5
NO_SUCH: Final = "No such summary."
#: The record kinds a summary links to, and their tables.
TABLES: Final = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _sync(workspace_id: str) -> dict[str, Any]:
    """The job's actor: summaries and their items are written as `sync`."""
    return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": workspace_id}


# ---------------------------------------------------------------------------
# The job — FR-CRMAI-SUM-01 to -07, -09 to -12
# ---------------------------------------------------------------------------


def run(workspace: dict[str, Any], now: datetime) -> dict[str, int]:
    """One `summaries` run of one workspace: `{threads, summarised, items,
    failed_items}`."""
    ws_id = workspace["id"]
    if not ai_rules.phase_on(store.ai_setting(ws_id), rules.PHASE):
        raise jobs.Skipped(ai_rules.SWITCHED_OFF)
    off = email_sync.off_reason(ws_id)
    if off:
        raise jobs.Skipped(off)
    counts = {"threads": 0, "summarised": 0, "items": 0, "failed_items": 0}
    pending = store.pending_threads(ws_id, BATCH)
    if not pending:
        return counts
    mailboxes = {m["id"]: m for m in store.list_mailboxes(ws_id, active_only=True)}
    members = store.list_members(ws_id)
    profiles = {p["id"]: p for p in db.list_profiles(include_crm_only=True)}
    team = rules.roster(members, profiles)
    roles = {m["user_id"]: m["role"] for m in members}
    active = {pid for pid, p in profiles.items() if p.get("is_active", False)}
    for thread in pending:
        if jobs.out_of_time():
            # The task's time is spent; the rest stay pending for the next tick.
            break
        mailbox = mailboxes.get(thread["mailbox_id"])
        if mailbox is None:
            continue
        counts["threads"] += 1
        try:
            made = _summarise(workspace, thread, mailbox, team, roles, active, now)
        except jobs.Skipped as skipped:
            # The budget or a switch stops the whole run, which keeps its counts.
            raise jobs.Skipped(skipped.reason, counts) from None
        except Exception as exc:  # noqa: BLE001 - one thread never fails the run
            # The thread stays pending; only the kind of failure is logged
            # (a database error's text can hold the row, FR-CRMAI-JOB-03).
            counts["failed_items"] += 1
            logger.warning(
                '{"event": "crm_summary_failed", "workspace": "%s", "error": "%s"}',
                workspace.get("key", "-"),
                type(exc).__name__,
            )
            continue
        counts["summarised"] += 1
        counts["items"] += sum(1 for i in made.get("items") or [] if i.get("created"))
    return counts


def _summarise(
    workspace: dict[str, Any],
    thread: dict[str, Any],
    mailbox: dict[str, Any],
    team: list[dict[str, Any]],
    roles: dict[str, str],
    active: set[str],
    now: datetime,
) -> dict[str, Any]:
    ws_id = workspace["id"]
    messages = thread.get("messages") or [{"gmail_id": g} for g in thread["message_ids"]]
    # FR-CRMAI-SUM-03: the stored messages only, each stripped, in memory.
    texts = [rules.strip(gmail.message_text(mailbox["email"], m["gmail_id"])) for m in messages]
    output = ai.call(
        workspace,
        rules.JOB,
        rules.PHASE,
        SummaryOutput,
        rules.INSTRUCTION,
        rules.prompt_parts(messages, texts, team),
    )
    answer = output.model_dump(mode="json")
    stored = rules.commitments(answer["commitments"], team)

    linked = _linked(ws_id, thread.get("links") or [])
    # No Review Queue item targets a record in the Trash.
    live = {kind: [r for r in rows if not r.get("deleted_at")] for kind, rows in linked.items()}
    generated_at = now.isoformat()
    plan = {
        "mailbox_id": thread["mailbox_id"],
        "thread_id": thread["thread_id"],
        "summary": answer["summary"],
        "asks": answer["asks"],
        "commitments": stored,
        "next_step": answer["next_step"],
        "next_step_on": answer["next_step_on"] if answer["next_step"] else None,
        "sentiment": answer["sentiment"],
        "watermark": thread["latest"],
        "model": ai_rules.CRM_AI_MODEL,
        "generated_at": generated_at,
        "links": [
            {"record_type": kind, "record_id": row["id"]}
            for kind, rows in linked.items()
            for row in rows
        ],
        "items": rules.items(
            mailbox_id=thread["mailbox_id"],
            thread_id=thread["thread_id"],
            watermark_id=thread["latest"]["gmail_id"],
            model=ai_rules.CRM_AI_MODEL,
            generated_at=generated_at,
            next_step=answer["next_step"],
            next_step_on=answer["next_step_on"],
            stored_commitments=stored,
            deals=live["opportunity"],
            companies=live["company"],
            people=live["person"],
            mailbox_owner=mailbox.get("owner_id"),
            roles=roles,
            active=active,
        ),
    }
    # Every string came masked from ai.call (FR-CRMAI-LLM-05).
    made = store.store_summary(_sync(ws_id), plan)
    # Spec 009 FR-CRMAI-FIELD-04: the linked records' AI fields are due.
    ai_fields.mark_dirty(ws_id, plan["links"], now)
    return made


def _linked(ws_id: str, links: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """FR-CRMAI-SUM-05 — the thread's companies and people and its open deals,
    in the Trash or not: the Trash only hides a summary when it is read, so
    a restored record shows it again (FR-CRMAI-CHG-11, data-model §4)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for kind, table in TABLES.items():
        ids = {lk["record_id"] for lk in links if lk["record_type"] == kind}
        rows = list(base.by_id(ws_id, table, ids).values())
        if kind == "opportunity":
            rows = [r for r in rows if r.get("stage") in rules.OPEN_STAGES]
        out[kind] = sorted(rows, key=lambda r: r["id"])
    return out


# ---------------------------------------------------------------------------
# Reading — FR-CRMAI-SUM-08, FR-CRMAI-MCP-01, -02; contracts §1, §2.3
# ---------------------------------------------------------------------------


def shown(workspace_id: str) -> bool:
    """FR-CRMAI-FLAG-03 — summaries are read only while phase 1 is on."""
    return ai_rules.phase_on(store.ai_setting(workspace_id), rules.PHASE)


def thread_key(mailbox_id: str, thread_id: str) -> str:
    """Contracts §0: `<mailbox_id>:<gmail thread id>`."""
    return f"{mailbox_id}:{thread_id}"


def _live_links(ws_id: str, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Each summary's links to records not in the Trash, with their names."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, table in TABLES.items():
        ids = {lk["record_id"] for r in rows for lk in r["links"] if lk["record_type"] == kind}
        for rid, row in base.by_id(ws_id, table, ids).items():
            if not row.get("deleted_at"):
                found[(kind, rid)] = row
    return {
        r["id"]: [
            {
                "record_type": lk["record_type"],
                "record_id": lk["record_id"],
                "name": base.record_name(lk["record_type"], found[key]),
            }
            for lk in r["links"]
            if (key := (lk["record_type"], lk["record_id"])) in found
        ]
        for r in rows
    }


def _present(row: dict[str, Any], links: list[dict[str, Any]], names: dict[str, str]) -> dict:
    key = thread_key(row["mailbox_id"], row["thread_id"])
    return ThreadSummaryOut(
        thread_key=key,
        subject=row.get("subject"),
        last_at=str(row["last_at"]) if row.get("last_at") else None,
        summary=row["summary"],
        asks=row.get("asks") or [],
        commitments=[
            {
                "text": c["text"],
                "owner_name": names.get(c.get("owner_id") or ""),
                "due_on": c.get("due_on"),
            }
            for c in row.get("commitments") or []
        ],
        next_step=row.get("next_step"),
        next_step_on=row.get("next_step_on"),
        sentiment=row["sentiment"],
        links=links,
        ai={
            "source_kind": rules.SOURCE_KIND,
            "model": row["model"],
            "generated_at": row["generated_at"],
            "source": {"thread_key": key},
        },
    ).model_dump(mode="json")


def _visible(ws_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Presented, leaving out a summary linked only to records in the Trash."""
    if not rows:
        return []
    live = _live_links(ws_id, rows)
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    return [_present(r, live[r["id"]], names) for r in rows if live[r["id"]]]


def for_record(ws_id: str, kind: str, record_id: str) -> list[dict[str, Any]]:
    """The record's latest five threads' summaries (contracts §1)."""
    return _visible(ws_id, store.summaries_for(ws_id, kind, record_id, limit=RECORD_LIMIT))


def for_threads(ws_id: str, threads: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """The Emails tab's threads' summaries, by `(mailbox_id, thread_id)`."""
    out: dict[tuple[str, str], dict] = {}
    for mailbox_id in sorted({m for m, _ in threads}):
        found = store.summary_by_thread(
            ws_id, mailbox_id, [t for m, t in threads if m == mailbox_id]
        )
        for shown_row in _visible(ws_id, list(found.values())):
            out[(mailbox_id, shown_row["thread_key"].split(":", 1)[1])] = shown_row
    return out


def by_key(ctx: Any, key: str) -> dict[str, Any]:
    """Contracts §2.3 `GET /crm/email-threads/{thread_key}/summary`. Not
    found while phase 1 is off, for a mailbox of another workspace, a thread
    with no summary, or one whose every linked record is in the Trash."""
    mailbox_id, _, thread_id = key.partition(":")
    if not (base._uuid(mailbox_id) and thread_id) or not shown(ctx.workspace_id):
        raise CrmRefused(404, NO_SUCH)
    if store.get_mailbox(ctx.workspace_id, mailbox_id) is None:
        raise CrmRefused(404, NO_SUCH)
    row = store.summary_by_thread(ctx.workspace_id, mailbox_id, [thread_id]).get(thread_id)
    found = _visible(ctx.workspace_id, [row] if row else [])
    if not found:
        raise CrmRefused(404, NO_SUCH)
    return found[0]


# ---------------------------------------------------------------------------
# The workspace export — FR-CRMAI-CHG-11, data-model §4
# ---------------------------------------------------------------------------

#: The columns an exported summary carries: ids, never a name.
EXPORT_COLUMNS = (
    "id",
    "mailbox_id",
    "thread_id",
    "summary",
    "asks",
    "commitments",
    "next_step",
    "next_step_on",
    "sentiment",
    "watermark_message_id",
    "watermark_at",
    "model",
    "generated_at",
    "created_at",
    "updated_at",
)


def export_rows(workspace_id: str) -> list[dict[str, Any]]:
    """Every summary of the workspace with its links (ids only); one linked
    only to records in the Trash is left out."""
    rows = store.summaries_for(workspace_id)
    live = _live_links(workspace_id, rows) if rows else {}
    return [
        {
            **{c: r.get(c) for c in EXPORT_COLUMNS},
            "links": [
                {"record_type": lk["record_type"], "record_id": lk["record_id"]}
                for lk in live[r["id"]]
            ],
        }
        for r in rows
        if live[r["id"]]
    ]
