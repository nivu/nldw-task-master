"""The follow-up rules — spec 009 FR-CRMAI-RULE-01 to -08, FR-CRMAI-JOB-01,
-02, -05, FR-CRMAI-FLAG-04; Q-14 to Q-16, Q-39, Q-40; D-4; plan §5.9.

Each rule runs behind its own switch in the `automations` setting and
phase 3 (FR-CRMAI-RULE-01). The detectors are `domain/crm/followups.py`'s;
this module gathers their facts and writes their Review Queue items, which
send no bell and no message (FR-CRMAI-REV-10). No rule changes a record or
sends an email: approving an item does, through the manual path
(`services/crm/review.py`, FR-CRMAI-RULE-07).

**In the `automations` job** (`crm.recompute_flags`, the shared worker,
every 30 minutes), `run()`:

- R-1 No reply after outreach, also needing phase 1 and Gmail sync live:
  only detects. The outbound messages owed a follow-up and with no item yet
  are put, as ids, on one `crm_ai.followup_draft` task per workspace per
  tick, on the `crm-ai` queue (FR-CRMAI-JOB-02, -04);
- R-2 Stuck in stage: the owner's notice (`notifications`); the flag itself
  is computed by the flags job (`services/crm/flags.stuck_on`);
- R-3 Lead gone cold and R-4 Campaign follow-through: their items.

**The draft task** (`drafts()`, on `worker-crm-ai`) is the `followup_drafts`
job of its workspace (D-4): for each message still owed a follow-up, it
writes a draft through `ai.call` from the thread's stored summary and the
record's name, never a message body (FR-CRMAI-RULE-02), and stores the R-1
item. A draft that fails is counted in `failed_items`; the budget or a
switch skips the run, so every draft done, failed or skipped is in the job
log (FR-CRMAI-JOB-01).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.domain.crm import ai_rules, dedupe
from app.domain.crm import flags as flag_rules
from app.domain.crm import followups as rules
from app.schemas.crm import FollowupDraftOutput
from app.services import supabase as db
from app.services.crm import ai, email_sync, jobs, notifications, review, store
from app.services.crm import records as base

logger = logging.getLogger("nldw-task-master")

#: R-1 drafts per workspace per tick; the rest wait for the next.
BATCH: Final = 20
NO_SUCH_WORKSPACE: Final = "No such workspace."
R1_OFF: Final = "No reply after outreach is switched off."


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _thresholds(ws_id: str) -> dict[str, int]:
    found = next(
        (s["value"] for s in store.list_settings(ws_id) if s["key"] == "flag_thresholds"), None
    )
    return flag_rules.thresholds(found)


def _team(ws_id: str) -> tuple[dict[str, str], set[str]]:
    """Member id → CRM role, and the active accounts, for routing."""
    roles = {m["user_id"]: m["role"] for m in store.list_members(ws_id)}
    active = {p["id"] for p in db.list_profiles(include_crm_only=True) if p.get("is_active", False)}
    return roles, active


def _live(ws_id: str, table: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    return {i: r for i, r in base.by_id(ws_id, table, ids).items() if not r.get("deleted_at")}


def _created(made: list[dict[str, Any]]) -> int:
    return sum(1 for m in made if m.get("created"))


# ---------------------------------------------------------------------------
# The automations job — FR-CRMAI-JOB-05
# ---------------------------------------------------------------------------


def run(workspace: dict[str, Any], now: datetime, on: dict[str, bool]) -> dict[str, int]:
    """The rules of one `automations` run whose switches (`on`, rule → on)
    are on, while phase 3 is on. Counts only."""
    ws_id = workspace["id"]
    if not ai_rules.phase_on(store.ai_setting(ws_id), rules.PHASE):
        idle(workspace, now, ai_rules.SWITCHED_OFF)
        return {}
    counts: dict[str, int] = {}
    existing = store.list_review_items(ws_id, status="all") if on["R-1"] or on["R-3"] else []
    if on["R-1"]:
        counts["drafts_queued"] = queue_drafts(workspace, now, existing)
    else:
        idle(workspace, now, R1_OFF)
    if on["R-2"]:
        counts.update(notifications.stuck_in_stage_notices(workspace, now))
    if on["R-3"]:
        counts["lead_cold_items"] = lead_cold(workspace, now, existing)
    if on["R-4"]:
        counts["followthrough_items"] = campaign_followthrough(workspace, now)
    return counts


# ---------------------------------------------------------------------------
# R-1 No reply after outreach — FR-CRMAI-RULE-02
# ---------------------------------------------------------------------------


def _drafting_off(ws_id: str) -> str | None:
    """Why R-1 cannot draft now: phase 1 or 3 off, or Gmail sync not live."""
    value = store.ai_setting(ws_id)
    if not (ai_rules.phase_on(value, rules.PHASE) and ai_rules.phase_on(value, rules.DRAFT_PHASE)):
        return ai_rules.SWITCHED_OFF
    return email_sync.off_reason(ws_id)


def _owed(ws_id: str, now: datetime, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The R-1 triggers with no item yet, open or decided: one item per
    outbound message, whichever person or company matched it."""
    before = now - timedelta(days=_thresholds(ws_id)["no_reply_days"])
    candidates = store.no_reply_candidates(ws_id, before)
    if not candidates:
        return []
    people = _live(ws_id, "crm_people", {p for c in candidates for p in c.get("people") or []})
    companies = _live(
        ws_id, "crm_companies", {i for c in candidates for i in c.get("companies") or []}
    )
    made = {i["source_key"] for i in existing}
    return [t for t in rules.no_reply(candidates, people, companies) if t["source_key"] not in made]


def queue_drafts(workspace: dict[str, Any], now: datetime, existing: list[dict[str, Any]]) -> int:
    """Detection only: the messages owed a follow-up, as ids, onto one
    `crm_ai.followup_draft` task. Nothing while drafting is off."""
    ws_id = workspace["id"]
    off = _drafting_off(ws_id)
    if off:
        idle(workspace, now, off)
        return 0
    owed = _owed(ws_id, now, existing)[:BATCH]
    if not owed:
        idle(workspace, now, None)
        return 0
    from app.tasks.crm_ai import followup_draft

    followup_draft.apply_async(
        args=[
            {
                "workspace_id": ws_id,
                "messages": [
                    {
                        "mailbox_id": t["mailbox_id"],
                        "thread_id": t["thread_id"],
                        "gmail_message_id": t["gmail_message_id"],
                    }
                    for t in owed
                ],
            }
        ]
    )
    return len(owed)


def idle(workspace: dict[str, Any], now: datetime, reason: str | None) -> None:
    """A tick that enqueues no draft task still records a `followup_drafts`
    run, `ok` with nothing owed or `skipped` for `reason`, so the job is seen
    to be running and is late only when the drafts stop (FR-CRM-JOB-03)."""

    def nothing(ws: dict[str, Any] | None, at: datetime) -> dict[str, int]:
        if reason:
            raise jobs.Skipped(reason)
        return {"drafted": 0, "failed_items": 0, "no_summary": 0}

    jobs.run(rules.JOB, workspace, nothing, now)


def drafts(args: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """The `crm_ai.followup_draft` task: one `followup_drafts` run of its
    workspace (`{outcome, counts}`)."""
    now = now or _now()
    workspace = next((w for w in store.list_workspaces() if w["id"] == args["workspace_id"]), None)
    if workspace is None:
        raise ValueError(NO_SUCH_WORKSPACE)
    wanted = {
        (m["mailbox_id"], m["thread_id"], m["gmail_message_id"]) for m in args.get("messages") or []
    }
    return jobs.run(rules.JOB, workspace, lambda ws, at: _draft_all(ws, wanted, at), now)


def _draft_all(
    workspace: dict[str, Any], wanted: set[tuple[str, str, str]], now: datetime
) -> dict[str, int]:
    ws_id = workspace["id"]
    off = _drafting_off(ws_id)
    if off:
        raise jobs.Skipped(off)
    if not notifications.automation_on(ws_id, rules.SWITCHES["R-1"]):
        raise jobs.Skipped(R1_OFF)
    # Still owed now, and still without an item: a reply or another run
    # meanwhile makes nothing.
    owed = [
        t
        for t in _owed(ws_id, now, store.list_review_items(ws_id, status="all"))
        if (t["mailbox_id"], t["thread_id"], t["gmail_message_id"]) in wanted
    ]
    counts = {"drafted": 0, "failed_items": 0, "no_summary": 0}
    if not owed:
        return counts
    roles, active = _team(ws_id)
    for trigger in owed:
        summary = store.summary_by_thread(ws_id, trigger["mailbox_id"], [trigger["thread_id"]]).get(
            trigger["thread_id"]
        )
        if summary is None:
            # Drafted from the stored summary only; the next tick tries again.
            counts["no_summary"] += 1
            continue
        try:
            item = _draft(workspace, trigger, summary, roles, active, now)
        except jobs.Skipped:
            # The budget or a switch stops the whole run.
            raise
        except Exception as exc:  # noqa: BLE001 - one draft never fails the run
            # Only the fixed sentence, or the kind of failure: a database
            # error's text can hold the row (FR-CRMAI-JOB-03).
            counts["failed_items"] += 1
            logger.warning(
                '{"event": "crm_followup_draft_failed", "workspace": "%s", "error": "%s"}',
                workspace.get("key", "-"),
                exc if isinstance(exc, ai.SafeError) else type(exc).__name__,
            )
            continue
        review.upsert(ws_id, [item])
        counts["drafted"] += 1
    return counts


def _draft(
    workspace: dict[str, Any],
    trigger: dict[str, Any],
    summary: dict[str, Any],
    roles: dict[str, str],
    active: set[str],
    now: datetime,
) -> dict[str, Any]:
    ws_id = workspace["id"]
    if trigger["record_type"] == "person":
        person = base.by_id(ws_id, "crm_people", [trigger["record_id"]])[trigger["record_id"]]
        to = dedupe.full_name(person["first_name"], person.get("last_name"))
        company_id = person.get("company_id")
    else:
        to, company_id = None, trigger["record_id"]
    company = (
        base.by_id(ws_id, "crm_companies", [company_id]).get(company_id) if company_id else None
    )
    company_name = company["name"] if company else None
    output = ai.call(
        workspace,
        rules.JOB,
        rules.PHASE,
        FollowupDraftOutput,
        rules.DRAFT_INSTRUCTION,
        rules.draft_parts(summary, to or company_name or "", company_name),
        now,
    )
    # The draft came masked from ai.call; review.upsert masks it again.
    return rules.no_reply_item(
        trigger,
        output.model_dump()["draft"],
        model=ai_rules.CRM_AI_MODEL,
        generated_at=now.isoformat(),
        roles=roles,
        active=active,
    )


# ---------------------------------------------------------------------------
# R-3 Lead gone cold — FR-CRMAI-RULE-05
# ---------------------------------------------------------------------------


def lead_cold(workspace: dict[str, Any], now: datetime, existing: list[dict[str, Any]]) -> int:
    """The workspace's R-3 items; returns how many are new."""
    ws_id = workspace["id"]
    companies = [
        c
        for c in store.every_record(ws_id, "crm_companies")
        if c.get("lead_stage") in rules.LEAD_COLD_OPTIONS
    ]
    if not companies:
        return 0
    activity = store.lead_activity(ws_id, [c["id"] for c in companies])
    prefix = rules.source_key("R-3", "")
    decided = [i for i in existing if i["source_key"].startswith(prefix) and i["status"] != "open"]
    roles, active = _team(ws_id)
    items = rules.lead_cold(
        companies,
        activity,
        decided,
        now,
        _thresholds(ws_id)["lead_cold_days"],
        roles=roles,
        active=active,
    )
    return _created(review.upsert(ws_id, items))


# ---------------------------------------------------------------------------
# R-4 Campaign follow-through — FR-CRMAI-RULE-06
# ---------------------------------------------------------------------------


def _with_open_task(ws_id: str, record_ids: set[str]) -> set[str]:
    """The companies and people with an open or waiting task (008) linked."""
    links = store.list_links(ws_id, "task_links", record_ids=sorted(record_ids))
    task_ids = sorted({lk["task_id"] for lk in links})
    if not task_ids:
        return set()
    open_ids = {
        t["id"]
        for t in store.list_records(ws_id, "tasks", any_of={"id": task_ids}, limit=len(task_ids))
        if t.get("status") in ("open", "waiting")
    }
    return {lk["record_id"] for lk in links if lk["task_id"] in open_ids}


def campaign_followthrough(workspace: dict[str, Any], now: datetime) -> int:
    """The workspace's R-4 items; returns how many are new. A member of a
    campaign in the Trash is left out."""
    ws_id = workspace["id"]
    members = [
        m
        for m in store.every_record(ws_id, "crm_campaign_members")
        if m.get("status") in rules.FOLLOWTHROUGH_STATUSES
    ]
    campaigns = _live(ws_id, "crm_campaigns", {m["campaign_id"] for m in members})
    members = [m for m in members if m["campaign_id"] in campaigns]
    if not members:
        return 0
    people = _live(ws_id, "crm_people", {m["person_id"] for m in members if m.get("person_id")})
    companies = _live(
        ws_id, "crm_companies", {m["company_id"] for m in members if m.get("company_id")}
    )
    alone = {m["company_id"] for m in members if m.get("company_id") and not m.get("person_id")}
    company_people: dict[str, list[dict[str, Any]]] = {}
    if alone:
        for p in store.every_record(ws_id, "crm_people"):
            if p.get("company_id") in alone:
                company_people.setdefault(p["company_id"], []).append(p)
    roles, active = _team(ws_id)
    items = rules.campaign_followthrough(
        members,
        people=people,
        company_people=company_people,
        companies=companies,
        with_open_task=_with_open_task(ws_id, set(people) | set(companies)),
        now=now,
        roles=roles,
        active=active,
    )
    return _created(review.upsert(ws_id, items))
