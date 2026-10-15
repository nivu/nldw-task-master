"""Daily and weekly CRM digests — spec 007 FR-CRM-NOTIF-02 to -05,
FR-CRM-AUTO-01 (automation 6), FR-CRM-ACC-11, FR-CRM-WS-02; Q-18, Q-19,
Q-73; data-model §3.20; plan §5.11.

Run by the digest tick every 15 minutes (`crm.digest_tick`). Each tick:

- finds the people whose local time is in their 09:00 window, from the
  memberships that exist at that moment, so a workspace someone was removed
  from is never in their digest;
- claims the digest's sent marker first (`crm_digest_claim`) and sends only
  when the claim is new: two ticks in one window send once;
- builds the daily digest's section for each workspace with the person's
  role there (a Viewer's carries no amount), and on Mondays a weekly digest
  per workspace for its Admins;
- sends through `notify.deliver`. A CRM-only person gets a digest only once
  a production SMTP sender exists; until then they are counted
  `undeliverable` and nothing is claimed (FR-CRM-NOTIF-02, Q-73).

A workspace whose `digests` automation is switched off is left out of every
digest (FR-CRM-AUTO-02); when every workspace has it off, the tick is
recorded `skipped`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from app.config import settings
from app.domain.crm import digests as rules
from app.services import supabase as db
from app.services.crm import jobs, notifications, store
from app.services.crm import today as today_service

logger = logging.getLogger("nldw-task-master")


def _link(key: str | None = None) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}/crm/{key}" if key else f"{base}/crm"


def _can_receive(person: dict[str, Any]) -> bool:
    """A CRM-only person's only channel is email (FR-CRM-NOTIF-02)."""
    return person.get("account_type") != "crm_only" or settings.email_enabled


def _send(person: dict[str, Any], subject: str, body: str, send_id: str) -> list[str]:
    channels = notifications.message(person, subject, body)
    if channels:
        store.set_digest_channels(send_id, channels)
    return channels


def daily_parts(
    person: dict[str, Any],
    memberships: list[dict[str, Any]],
    workspaces: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """The person's daily digest: one section per workspace, each built and
    redacted with their role there (FR-CRM-NOTIF-05)."""
    parts = []
    for m in sorted(memberships, key=lambda m: workspaces[m["workspace_id"]]["name"]):
        ws = workspaces[m["workspace_id"]]
        built = today_service.build(ws["id"], person["id"], person, now)
        deal_ids = [
            item["record_id"]
            for section in built["sections"]
            for item in section["items"]
            if item.get("record_type") == "opportunity"
        ]
        sees_amounts = m["role"] in ("admin", "member")
        amounts = store.deal_amounts(ws["id"], deal_ids) if sees_amounts and deal_ids else {}
        parts.append(rules.daily_section(ws, m["role"], built["sections"], amounts))
    return parts


def weekly(workspace: dict[str, Any], day: date, now: datetime) -> dict[str, Any]:
    """FR-CRM-NOTIF-04 — the week to `day` in one workspace."""
    since = day - timedelta(days=7)
    return rules.weekly_content(
        workspace,
        since=since,
        until=day,
        moves=store.stage_moves_since(workspace["id"], now - timedelta(days=7)),
        closed=store.closed_since(workspace["id"], since.isoformat()),
        flag_counts=store.flag_counts(workspace["id"]),
    )


def tick(_: Any, now: datetime) -> dict[str, int]:
    """One digest tick across every workspace (the job with no workspace)."""
    workspaces = {w["id"]: w for w in store.list_workspaces()}
    on = {ws_id for ws_id in workspaces if notifications.automation_on(ws_id, "digests")}
    if workspaces and not on:
        raise jobs.Skipped("Digests are switched off in every workspace.")
    people = {p["id"]: p for p in db.list_profiles(include_crm_only=True, active_only=True)}
    by_person: dict[str, list[dict[str, Any]]] = {}
    for m in store.list_all_memberships():
        if m["workspace_id"] in on and m["user_id"] in people:
            by_person.setdefault(m["user_id"], []).append(m)

    counts = {"daily": 0, "weekly": 0, "undeliverable": 0}
    for user_id, memberships in sorted(by_person.items()):
        person = people[user_id]
        day = rules.local_day_in_window(now, person.get("timezone"))
        if day is None:
            continue
        if not _can_receive(person):
            counts["undeliverable"] += 1
            continue
        send_id = store.claim_digest(user_id, "daily", day.isoformat(), None)
        if send_id:
            parts = daily_parts(person, memberships, workspaces, now)
            body = rules.daily_body(person.get("display_name") or "", day, parts, _link())
            if _send(person, rules.daily_subject(day), body, send_id):
                counts["daily"] += 1
            else:
                counts["undeliverable"] += 1
        if not rules.is_weekly_day(day):
            continue
        for m in memberships:
            if m["role"] != "admin":
                continue
            ws = workspaces[m["workspace_id"]]
            send_id = store.claim_digest(user_id, "weekly", day.isoformat(), ws["id"])
            if not send_id:
                continue
            content = weekly(ws, day, now)
            body = rules.weekly_body(content, _link(ws["key"]))
            if _send(person, rules.weekly_subject(content), body, send_id):
                counts["weekly"] += 1
            else:
                counts["undeliverable"] += 1
    logger.info(
        '{"event": "crm_digests", "daily": %d, "weekly": %d, "undeliverable": %d}',
        counts["daily"],
        counts["weekly"],
        counts["undeliverable"],
    )
    return counts
