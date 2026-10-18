"""Meeting capture from Fireflies — spec 009 FR-CRMAI-MEET-01 to -08,
FR-CRMAI-JOB-01; NFR-CRMAI-01, -05; Q-10, Q-26, Q-41; plan §5.6.

**The job** (`crm_ai.meetings`, every 15 minutes, on `worker-crm-ai`) is one
task for every workspace, because one Fireflies poll serves them all; it
still records one `meetings` run per workspace (`jobs.run`):

1. one poll lists the transcripts updated since the cursor, **metadata
   only** (`fireflies.list_since`), and only while some workspace has
   phase 2 on and the Fireflies key is set;
2. each meeting's external attendees (outside the workspace's own domains)
   are matched to every workspace's people by primary or additional email,
   before any text is read (FR-CRMAI-MEET-02). A meeting matched nowhere is
   ignored; one matched in several workspaces is processed in none, and
   each of them counts it in `skipped_multi_workspace` (Q-26);
3. in the one workspace it matched, while phase 2 is on there: the
   transcript's content is fetched (`fireflies.transcript`), Claude writes
   the note through `ai.call`, and `crm_store_meeting_note` (063) stores
   the note, its links and its Review Queue items in one transaction, as
   the `sync` actor (FR-CRMAI-MEET-04 to -07).

A meeting already noted from the same transcript version is not fetched
again. The cursor, the newest update seen, is kept in Redis
(`crm:fireflies:cursor`); it never passes a meeting that failed or waits on
a skipped run, so that meeting is polled again next tick. Re-polling is
safe anyway: a note is unique per transcript and workspace.

The transcript lives only in this module's local variables: it is never
stored, logged, queued or put in an error. A meeting that fails (the fetch,
the call, the schema, the database) is counted in `failed_items` and the
run itself is not failed (FR-CRMAI-JOB-01).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

from app.config import settings
from app.domain.crm import ai_rules
from app.domain.crm import meetings as rules
from app.domain.crm.summaries import roster
from app.schemas.crm import MeetingOutput
from app.services import supabase as db
from app.services.crm import ai, ai_fields, fireflies, jobs, store
from app.services.crm import records as base

logger = logging.getLogger("nldw-task-master")

#: The poll cursor: a time, never content (plan §5.6).
CURSOR_KEY: Final = "crm:fireflies:cursor"


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _sync(workspace_id: str) -> dict[str, Any]:
    """The job's actor: meeting notes and their items are written as `sync`
    (FR-CRMAI-MEET-05)."""
    return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": workspace_id}


# ---------------------------------------------------------------------------
# The cursor — plan §5.6
# ---------------------------------------------------------------------------


def _redis():
    import redis

    return redis.Redis.from_url(str(settings.REDIS_URL), socket_timeout=5)


def read_cursor() -> str | None:
    value = _redis().get(CURSOR_KEY)
    return value.decode() if isinstance(value, bytes) else value


def save_cursor(value: str) -> None:
    _redis().set(CURSOR_KEY, value)


def next_cursor(listed: list[dict[str, Any]], finished: set[str], cursor: str | None) -> str | None:
    """The newest update among the finished meetings that is older than
    every unfinished one, so an unfinished meeting is listed again."""
    waiting = [datetime.fromisoformat(m["updated_at"]) for m in listed if m["id"] not in finished]
    best = datetime.fromisoformat(cursor) if cursor else None
    for m in listed:
        at = datetime.fromisoformat(m["updated_at"])
        if m["id"] in finished and (not waiting or at < min(waiting)):
            best = at if best is None else max(best, at)
    return best.isoformat() if best else None


# ---------------------------------------------------------------------------
# The tick — FR-CRMAI-MEET-01 to -03
# ---------------------------------------------------------------------------


def _place(
    workspaces: list[dict[str, Any]], listed: list[dict[str, Any]]
) -> tuple[dict[str, list[tuple[dict, list]]], dict[str, int], set[str]]:
    """`(placed, several, finished)`: each workspace's meetings with their
    matched people, how many meetings each workspace shares with another,
    and the meetings with nothing more to do (matched nowhere or in
    several workspaces). Attendee metadata only."""
    people: dict[str, list[dict[str, Any]]] = {}
    for ws in workspaces:
        emails = sorted(
            {e for m in listed for e in rules.external(m["attendees"], ws["allowed_domains"] or [])}
        )
        people[ws["id"]] = store.people_by_emails(ws["id"], emails) if emails else []
    placed: dict[str, list[tuple[dict, list]]] = {ws["id"]: [] for ws in workspaces}
    several = {ws["id"]: 0 for ws in workspaces}
    finished: set[str] = set()
    for meeting in listed:
        matches = {
            ws["id"]: rules.match(
                meeting["attendees"], ws["allowed_domains"] or [], people[ws["id"]]
            )
            for ws in workspaces
        }
        where, ws_id = rules.placement(matches)
        if where == "one":
            placed[ws_id].append((meeting, matches[ws_id]))
            continue
        finished.add(meeting["id"])
        if where == "several":
            for matched_ws, found in matches.items():
                if found:
                    several[matched_ws] += 1
    return placed, several, finished


def tick(now: datetime | None = None) -> dict[str, Any]:
    """One poll for every workspace, one `meetings` run per workspace:
    `{runs: {workspace key: {outcome, counts}}, failed: [workspace key]}`."""
    now = now or _now()
    workspaces = store.list_workspaces()
    on = {ws["id"]: ai_rules.phase_on(store.ai_setting(ws["id"]), rules.PHASE) for ws in workspaces}
    connected = fireflies.configured()
    listed: list[dict[str, Any]] = []
    cursor: str | None = None
    poll_failed: str | None = None
    if any(on.values()) and connected:
        try:
            cursor = read_cursor()
            listed = fireflies.list_since(cursor, now=now)
        except Exception as exc:  # noqa: BLE001 - recorded in each run, without its text
            poll_failed = type(exc).__name__
    failed_step = "Fireflies poll"
    placed: dict[str, list[tuple[dict, list]]] = {ws["id"]: [] for ws in workspaces}
    several = {ws["id"]: 0 for ws in workspaces}
    finished: set[str] = set()
    if poll_failed is None:
        try:
            placed, several, finished = _place(workspaces, listed)
        except Exception as exc:  # noqa: BLE001 - its text can hold attendee emails (FR-CRMAI-JOB-03)
            failed_step, poll_failed = "Fireflies match", type(exc).__name__

    runs: dict[str, Any] = {}
    for ws in workspaces:

        def work(workspace: dict[str, Any], at: datetime) -> dict[str, int]:
            ws_id = workspace["id"]
            if not on[ws_id]:
                # Nothing fetched; the meeting is not waited on (plan §5.6).
                finished.update(m["id"] for m, _ in placed[ws_id])
                raise jobs.Skipped(ai_rules.SWITCHED_OFF)
            if not connected:
                raise jobs.Skipped(rules.NOT_CONNECTED)
            if poll_failed is not None:
                raise ai.SafeError(failed_step, poll_failed)
            return run(workspace, placed[ws_id], several[ws_id], finished, at)

        runs[ws["key"]] = jobs.run(rules.JOB, ws, work, now)

    if listed and poll_failed is None:
        moved = next_cursor(listed, finished, cursor)
        if moved and moved != cursor:
            try:
                save_cursor(moved)
            except Exception as exc:  # noqa: BLE001 - the next poll lists them again
                logger.warning(
                    '{"event": "crm_fireflies_cursor_failed", "error": "%s"}', type(exc).__name__
                )
    return {"runs": runs, "failed": [k for k, r in runs.items() if r["outcome"] == "failed"]}


# ---------------------------------------------------------------------------
# One workspace's meetings — FR-CRMAI-MEET-04 to -07
# ---------------------------------------------------------------------------


def run(
    workspace: dict[str, Any],
    meetings: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    skipped_several: int,
    finished: set[str],
    now: datetime,
) -> dict[str, int]:
    """The workspace's matched meetings captured: `{meetings, notes,
    updated, items, skipped_multi_workspace, failed_items}`."""
    ws_id = workspace["id"]
    counts = {
        "meetings": len(meetings),
        "notes": 0,
        "updated": 0,
        "items": 0,
        "skipped_multi_workspace": skipped_several,
        "failed_items": 0,
    }
    if not meetings:
        return counts
    refs = [rules.source_ref(m["id"]) for m, _ in meetings]
    noted = {
        n["source_ref"]: (n.get("ai_provenance") or {}).get("transcript_updated_at")
        for n in store.list_records(
            ws_id, "crm_notes", any_of={"source_ref": refs}, deleted=None, limit=len(refs)
        )
    }
    members = store.list_members(ws_id)
    profiles = {p["id"]: p for p in db.list_profiles(include_crm_only=True)}
    team = roster(members, profiles)
    roles = {m["user_id"]: m["role"] for m in members}
    active = {pid for pid, p in profiles.items() if p.get("is_active", False)}
    for meeting, people in meetings:
        seen = noted.get(rules.source_ref(meeting["id"]))
        if seen and datetime.fromisoformat(seen) >= datetime.fromisoformat(meeting["updated_at"]):
            # FR-CRMAI-MEET-07: this version is noted already; nothing fetched.
            finished.add(meeting["id"])
            continue
        try:
            made = _capture(workspace, meeting, people, team, roles, active, now)
        except jobs.Skipped:
            # The budget or a switch stops the whole run; the meeting waits.
            raise
        except Exception as exc:  # noqa: BLE001 - one meeting never fails the run
            # The meeting stays pending; only the kind of failure is logged
            # (a database error's text can hold the row, FR-CRMAI-JOB-03).
            counts["failed_items"] += 1
            logger.warning(
                '{"event": "crm_meeting_failed", "workspace": "%s", "error": "%s"}',
                workspace.get("key", "-"),
                type(exc).__name__,
            )
            continue
        finished.add(meeting["id"])
        counts["notes"] += 1 if made.get("created") else 0
        counts["updated"] += 1 if made.get("updated") else 0
        counts["items"] += sum(1 for i in made.get("items") or [] if i.get("created"))
    return counts


def _capture(
    workspace: dict[str, Any],
    meeting: dict[str, Any],
    people: list[dict[str, Any]],
    team: list[dict[str, Any]],
    roles: dict[str, str],
    active: set[str],
    now: datetime,
) -> dict[str, Any]:
    ws_id = workspace["id"]
    # FR-CRMAI-MEET-02: the content only now, after the match, in memory.
    content = fireflies.transcript(meeting["id"])
    output = ai.call(
        workspace,
        rules.JOB,
        rules.PHASE,
        MeetingOutput,
        rules.INSTRUCTION,
        rules.prompt_parts(meeting["title"], content, team),
        now,
    )
    del content
    answer = output.model_dump(mode="json")
    actions = rules.action_items(answer["action_items"], team)

    # FR-CRMAI-MEET-04: the matched people's companies and their open deals.
    company_ids = {p["company_id"] for p in people if p.get("company_id")}
    companies = [
        c
        for c in base.by_id(ws_id, "crm_companies", company_ids).values()
        if not c.get("deleted_at")
    ]
    deal_ids = {d["id"] for d in store.deals_of_companies(ws_id, [c["id"] for c in companies])}
    deals = list(base.by_id(ws_id, "crm_opportunities", deal_ids).values())
    deal, choices = rules.resolve_deal(deals)

    url = rules.transcript_link(meeting.get("transcript_url"))
    generated_at = now.isoformat()
    links = [
        {"record_type": "company", "record_id": c["id"]}
        for c in sorted(companies, key=lambda c: c["id"])
    ]
    links += [{"record_type": "person", "record_id": p["id"]} for p in people]
    if deal is not None:
        links.append({"record_type": "opportunity", "record_id": deal["id"]})
    plan = {
        "source_ref": rules.source_ref(meeting["id"]),
        "occurred_on": rules.meeting_day(meeting["date"]),
        "body": rules.note_body(
            title=meeting.get("title") or "",
            attendees=meeting["attendees"],
            summary=answer["summary"],
            decisions=answer["decisions"],
            actions=actions,
            owner_names={p["user_id"]: p["name"] for p in team},
            transcript_url=url,
        ),
        "ai_provenance": {
            "source_kind": rules.SOURCE_KIND,
            "model": ai_rules.CRM_AI_MODEL,
            "generated_at": generated_at,
            "transcript_updated_at": meeting["updated_at"],
            "transcript_url": url,
        },
        "links": links,
        "items": rules.items(
            transcript_id=meeting["id"],
            version=meeting["updated_at"],
            model=ai_rules.CRM_AI_MODEL,
            generated_at=generated_at,
            next_step=answer["next_step"],
            next_step_on=answer["next_step_on"],
            actions=actions,
            deal=deal,
            choices=choices,
            companies=companies,
            people=people,
            roles=roles,
            active=active,
        ),
    }
    # Every generated string came masked from ai.call (FR-CRMAI-LLM-05).
    made = store.store_meeting_note(_sync(ws_id), plan)
    # Spec 009 FR-CRMAI-FIELD-04: the linked records' AI fields are due.
    ai_fields.mark_dirty(ws_id, links, now)
    return made
