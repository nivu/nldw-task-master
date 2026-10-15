"""Daily and weekly CRM digests — spec 007 FR-CRM-NOTIF-02 to -05,
FR-CRM-AUTO-01 (automation 6), FR-CRM-ACC-11, FR-CRM-WS-02; Q-18, Q-19,
Q-73; data-model §3.20; plan §5.11.

Run by the digest tick every 15 minutes (`crm.digest_tick`). Each tick:

- finds the people whose local time is 09:00 or later, from the memberships
  that exist at that moment, so a workspace someone was removed from is
  never in their digest;
- claims the digest's sent marker first (`crm_digest_claim`) and sends only
  when the claim is new: every later tick that day sends nothing, and a
  missed tick only delays the digest;
- one person's failure is logged and counted `failed` and the tick carries
  on; a digest that could not be built has its marker released, so the next
  tick tries again;
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
from collections.abc import Callable
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


def _send_once(
    person: dict[str, Any],
    kind: str,
    day: date,
    workspace_id: str | None,
    build: Callable[[], tuple[str, str]],
) -> str | None:
    """Claims the digest, builds `(subject, body)` and sends it. Returns the
    count it goes in: `daily`/`weekly`, `undeliverable`, `failed`, or None
    when it was sent already. Never raises."""
    try:
        send_id = store.claim_digest(person["id"], kind, day.isoformat(), workspace_id)
    except Exception:
        logger.exception('{"event": "crm_digest_failed", "kind": "%s", "step": "claim"}', kind)
        return "failed"
    if not send_id:
        return None
    try:
        subject, body = build()
    except Exception:
        logger.exception('{"event": "crm_digest_failed", "kind": "%s", "step": "build"}', kind)
        try:
            store.release_digest(send_id)
        except Exception:
            logger.exception('{"event": "crm_digest_release_failed", "kind": "%s"}', kind)
        return "failed"
    # Sent from here on: a failure to record the channels must not release
    # the marker, or the next tick would send it twice.
    channels = notifications.message(person, subject, body)
    if not channels:
        return "undeliverable"
    try:
        store.set_digest_channels(send_id, channels)
    except Exception:
        logger.exception('{"event": "crm_digest_channels_failed", "kind": "%s"}', kind)
    return kind


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

    counts = {"daily": 0, "weekly": 0, "undeliverable": 0, "failed": 0}
    for user_id, memberships in sorted(by_person.items()):
        person = people[user_id]
        day = rules.local_day_due(now, person.get("timezone"))
        if day is None:
            continue
        if not _can_receive(person):
            counts["undeliverable"] += 1
            continue

        def daily(person=person, memberships=memberships, day=day) -> tuple[str, str]:
            parts = daily_parts(person, memberships, workspaces, now)
            body = rules.daily_body(person.get("display_name") or "", day, parts, _link())
            return rules.daily_subject(day), body

        outcome = _send_once(person, "daily", day, None, daily)
        if outcome:
            counts[outcome] += 1
        if not rules.is_weekly_day(day):
            continue
        for m in memberships:
            if m["role"] != "admin":
                continue
            ws = workspaces[m["workspace_id"]]

            def week(ws=ws, day=day) -> tuple[str, str]:
                content = weekly(ws, day, now)
                return rules.weekly_subject(content), rules.weekly_body(content, _link(ws["key"]))

            outcome = _send_once(person, "weekly", day, ws["id"], week)
            if outcome:
                counts[outcome] += 1
    logger.info(
        '{"event": "crm_digests", "daily": %d, "weekly": %d, "undeliverable": %d, "failed": %d}',
        counts["daily"],
        counts["weekly"],
        counts["undeliverable"],
        counts["failed"],
    )
    return counts
