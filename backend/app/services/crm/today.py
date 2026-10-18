"""Today — spec 007 FR-CRM-TODAY-01 to -04, FR-CRM-CHG-13; contracts §2.8;
plan §5.8.

One indexed query per section (and one more for loose ends' open tasks),
assembled by `domain/crm/today.py` in the person's own day. Sections 1 and 4
come from email sync (1b); until it is on in the workspace they say so.

The person is the caller, or for a workspace Admin any member
(FR-CRM-TODAY-03): their deals, their tasks and their companies, in their
time zone. A Today line carries no amount, so it reads the same for every
role.

From 1b, sections 1 and 4 list the person's records whose Reply owed or
Client silent flag is up, red first, once email sync is live in the
workspace: its switch on and the Gmail credential configured (S10); and an
Admin's Today names any background job that is late (FR-CRM-JOB-03).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.crm import today as rules
from app.services import supabase as db
from app.services.crm import CrmRefused, email_sync, jobs, review, store
from app.services.crm import records as base


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _profile(ctx: Any, person_id: str) -> dict[str, Any]:
    """The person whose Today it is: their name and time zone."""
    if person_id == ctx.user.id:
        return ctx.user.profile
    return db.get_profile(person_id) or {}


def today(ctx: Any, person: str | None) -> dict[str, Any]:
    """Contracts §2.8 `GET /crm/today` — `{as_of, timezone, person, sections}`,
    and for an Admin `stale_jobs`, the background jobs that are late
    (FR-CRM-JOB-03): a stopped scheduler cannot say so itself."""
    person_id = person or ctx.user.id
    why = rules.may_see(is_admin=ctx.may("administer"), caller_id=ctx.user.id, person_id=person_id)
    if why:
        raise CrmRefused(403, why)
    if person_id not in {m["user_id"] for m in store.list_members(ctx.workspace_id)}:
        raise CrmRefused(404, rules.NOT_A_MEMBER)

    profile = _profile(ctx, person_id)
    answer = {
        **build(ctx.workspace_id, person_id, profile, _now()),
        "person": {"id": person_id, "name": profile.get("display_name")},
    }
    if ctx.may("administer"):
        answer["stale_jobs"] = jobs.stale_for(ctx)
    return answer


def build(
    workspace_id: str, person_id: str, profile: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """A person's Today in one workspace, in their own day: `{as_of,
    timezone, sections}`. Also the daily digest's section for the workspace
    (services/crm/digests.py)."""
    zone = profile.get("timezone") or rules.DEFAULT_ZONE
    day = rules.day_of(now, zone)
    by = day.isoformat()
    ws_id = workspace_id

    # §2 — deal next steps due today or overdue.
    deals = store.due_next_steps(ws_id, person_id, by)
    companies = base.by_id(ws_id, "crm_companies", {d["company_id"] for d in deals})
    for deal in deals:
        deal["company_name"] = (companies.get(deal["company_id"]) or {}).get("name")

    # §3 — tasks for today, each with the records it is linked to.
    tasks = store.tasks_for_today(ws_id, person_id, by)
    links = store.list_links(ws_id, "task_links", owner_ids=[t["id"] for t in tasks])
    named = base.link_names(ws_id, links)
    linked: dict[str, list[str]] = {}
    opens: dict[str, dict[str, str]] = {}
    for link in links:
        name = named.get((link["record_type"], link["record_id"]))
        if name:
            linked.setdefault(link["task_id"], []).append(name)
            opens.setdefault(
                link["task_id"],
                {"record_type": link["record_type"], "record_id": link["record_id"]},
            )

    # §5 — loose ends: Nurturing, no next touch, and no open or waiting task.
    nurturing = store.nurturing_without_next_touch(ws_id, person_id)
    company_links = store.list_links(
        ws_id, "task_links", record_type="company", record_ids=[c["id"] for c in nurturing]
    )
    task_ids = {lk["task_id"] for lk in company_links}
    open_ids = {
        t["id"]
        for t in base.by_id(ws_id, "tasks", task_ids).values()
        if t["status"] in ("open", "waiting") and not t.get("deleted_at")
    }
    with_open = {lk["record_id"] for lk in company_links if lk["task_id"] in open_ids}

    settings = {row["key"]: row["value"] for row in store.list_settings(ws_id)}
    sync_on = email_sync.live(settings)
    built = {
        "next_steps": rules.next_steps(deals, day),
        "tasks": rules.tasks(tasks, day, linked, opens),
        "loose_ends": rules.loose_ends(nurturing, with_open),
    }
    if sync_on:
        # §1 and §4 (1b): from the person's flagged records, red first.
        flagged_deals = store.flagged_owned(ws_id, "crm_opportunities", person_id)
        built["replies_owed"] = rules.replies_owed(
            store.flagged_owned(ws_id, "crm_people", person_id),
            store.flagged_owned(ws_id, "crm_companies", person_id),
            flagged_deals,
        )
        built["silent_clients"] = rules.silent_clients(flagged_deals)
    return {
        "as_of": by,
        "timezone": zone,
        "sections": rules.assemble(sync_on=sync_on, built=built)
        + review.today_section(ws_id, person_id),
    }
