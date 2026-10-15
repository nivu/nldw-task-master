"""Notifications — spec 007 FR-CRM-NOTIF-01, -02, -05, FR-CRM-NOTE-02,
FR-CRM-AUTO-01 (automations 5 and 7), FR-CRM-ACC-11, FR-CRM-WS-02; Q-18,
Q-73; contracts §2.10; data-model §3.19; plan §5.11.

The in-app bell, and the out-of-app messages that go with some of its
events through the portal's existing channel (`notify.deliver`: Slack DM
first, email as the fallback).

- Every notification is added by `crm_notify` (037), which adds it only for
  a member of its workspace and only once per person and `dedupe_key`
  (FR-CRM-WS-02). An out-of-app message goes only with a notification that
  was new, so a reminder that runs every 15 minutes still sends once.
- Titles name a record and a person, never an amount (FR-CRM-ACC-11).
- A CRM-only person is not in Nunnari's Slack, so email is their only
  out-of-app path: without a production SMTP sender they get the bell only,
  and the run counts them `undeliverable` (FR-CRM-NOTIF-02, Q-73).
- The bell lists and counts only notifications of workspaces the person is
  a member of now (contracts §2.10).

Events that come from a person's write (assigned, mentioned, a deal's stage
moved by someone else, a handoff or revenue confirmation waiting) never
fail that write: the change stands, and a failure here is logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.domain.crm import dedupe
from app.domain.crm import flags as flag_rules
from app.domain.crm import today as today_rules
from app.domain.crm.digests import long_date
from app.services import supabase as db
from app.services.crm import CrmRefused, store
from app.services.crm.context import workspaces_of
from app.services.notify import Message, deliver

logger = logging.getLogger("nldw-task-master")

NO_SUCH_WORKSPACE = "No such workspace."

#: A record's noun in a title.
NOUNS = {
    "company": "company",
    "person": "person",
    "opportunity": "deal",
    "task": "task",
    "note": "note",
    "campaign": "campaign",
}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _link(workspace_key: str | None = None) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}/crm/{workspace_key}" if workspace_key else f"{base}/crm"


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def message(profile: dict[str, Any], subject: str, body: str) -> list[str]:
    """One out-of-app message, or none for a CRM-only person while there is
    no SMTP sender. Returns the channels that sent; empty is undeliverable."""
    if not profile or not profile.get("email") or not profile.get("is_active", True):
        return []
    if profile.get("account_type") == "crm_only" and not settings.email_enabled:
        return []
    return deliver(
        Message(
            recipient_email=profile["email"],
            recipient_name=profile.get("display_name") or profile["email"],
            subject=subject,
            body=body,
        )
    )


def send(
    rows: list[dict[str, Any]],
    *,
    out_of_app: bool = False,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Adds notifications (`{workspace_id, user_id, kind, title, dedupe_key,
    record_type?, record_id?, body?}`), and with `out_of_app` sends each new
    one as a message too. Returns `{added, delivered, undeliverable}`."""
    bodies = {(r["user_id"], r["dedupe_key"]): r.pop("body", None) for r in rows}
    added = store.notify(rows)
    counts = {"added": len(added), "delivered": 0, "undeliverable": 0}
    if not out_of_app:
        return counts
    for row in added:
        person = (profiles or {}).get(row["user_id"]) or db.get_profile(row["user_id"]) or {}
        body = bodies.get((row["user_id"], row["dedupe_key"])) or row["title"]
        if message(person, row["title"], f"{body}\n\n{_link()}"):
            counts["delivered"] += 1
        else:
            counts["undeliverable"] += 1
    return counts


def quietly(rows: list[dict[str, Any]]) -> None:
    """In-app notifications from a person's write: never fails the write."""
    if not rows:
        return
    try:
        store.notify(rows)
    except Exception:
        logger.exception('{"event": "crm_notify_failed", "rows": %d}', len(rows))


def _row(
    workspace_id: str,
    user_id: str,
    kind: str,
    title: str,
    dedupe_key: str,
    record_type: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "kind": kind,
        "title": title[:200],
        "dedupe_key": dedupe_key[:200],
        "record_type": record_type,
        "record_id": record_id,
    }


# ---------------------------------------------------------------------------
# Events from writes — FR-CRM-NOTIF-01, FR-CRM-NOTE-02
# ---------------------------------------------------------------------------


def _actor_name(ctx: Any) -> str:
    return ctx.user.display_name or "Someone"


def assigned(
    ctx: Any, record_type: str, record_id: str, name: str, user_id: str | None
) -> list[dict[str, Any]]:
    """Someone else was made a record's owner or a task's assignee."""
    if not user_id or user_id == ctx.user.id:
        return []
    noun = NOUNS[record_type]
    what = "the task" if record_type == "task" else f"owner of the {noun}"
    title = (
        f"{_actor_name(ctx)} assigned you {what} “{name}”"
        if record_type == "task"
        else f"{_actor_name(ctx)} made you {what} “{name}”"
    )
    return [
        _row(
            ctx.workspace_id,
            user_id,
            "assigned",
            title,
            f"assigned:{record_type}:{record_id}:{uuid.uuid4()}",
            record_type,
            record_id,
        )
    ]


def mentioned(
    ctx: Any, note_id: str, member_ids: list[str], about: dict[str, str] | None
) -> list[dict[str, Any]]:
    """Members newly mentioned in a note, once per note each. The note
    opens on the first record it is linked to."""
    where = f" on “{about['name']}”" if about and about.get("name") else ""
    return [
        _row(
            ctx.workspace_id,
            member,
            "mentioned",
            f"{_actor_name(ctx)} mentioned you in a note{where}",
            f"mentioned:{note_id}:{member}",
            (about or {}).get("record_type") or "note",
            (about or {}).get("record_id") or note_id,
        )
        for member in member_ids
        if member != ctx.user.id
    ]


def stage_changed(ctx: Any, deal: dict[str, Any], stage_label: str | None) -> list[dict]:
    """A deal I own was moved by someone else."""
    owner = deal.get("owner_id")
    if not owner or owner == ctx.user.id:
        return []
    return [
        _row(
            ctx.workspace_id,
            owner,
            "stage_changed",
            f"{_actor_name(ctx)} moved “{deal['name']}” to {stage_label or deal['stage']}",
            f"stage_changed:{deal['id']}:{deal['stage']}:{uuid.uuid4()}",
            "opportunity",
            deal["id"],
        )
    ]


def handoff_waiting(ctx: Any, deal: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict]:
    """The tasks a Closed Won handoff left: the delivery kickoff, and a
    revenue confirmation for each Labs admin when it went without revenue."""
    rows = []
    for task in tasks:
        delivery = task.get("category_key") == "delivery_handoff"
        kind = "handoff_waiting" if delivery else "revenue_confirm"
        title = (
            f"Delivery handoff waiting: “{deal['name']}”"
            if delivery
            else f"Confirm revenue and milestones for “{deal['name']}”"
        )
        if task.get("assignee_id") and task.get("id") and task["assignee_id"] != ctx.user.id:
            rows.append(
                _row(
                    ctx.workspace_id,
                    task["assignee_id"],
                    kind,
                    title,
                    f"{kind}:{task['id']}",
                    "opportunity",
                    deal["id"],
                )
            )
    return rows


# ---------------------------------------------------------------------------
# The bell — contracts §2.10
# ---------------------------------------------------------------------------


def _mine(user_id: str) -> dict[str, dict[str, Any]]:
    """The person's workspaces now, by id. None at all is "No such
    workspace." as every CRM route answers a non-member (FR-CRM-WS-02)."""
    mine = {w["id"]: w for w in workspaces_of(user_id)}
    if not mine:
        raise CrmRefused(404, NO_SUCH_WORKSPACE)
    return mine


def present(row: dict[str, Any], workspaces: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_key": workspaces[row["workspace_id"]]["key"],
        "kind": row["kind"],
        "title": row["title"],
        "record_type": row.get("record_type"),
        "record_id": row.get("record_id"),
        "created_at": row["created_at"],
        "read_at": row.get("read_at"),
    }


def list_for(user_id: str, *, unread_only: bool, before: str | None, limit: int) -> list[dict]:
    mine = _mine(user_id)
    rows = store.list_notifications(
        user_id, sorted(mine), unread_only=unread_only, before=before, limit=limit
    )
    return [present(r, mine) for r in rows if r["workspace_id"] in mine]


def unread_for(user_id: str) -> int:
    return store.unread_count(user_id, sorted(_mine(user_id)))


def mark_read(user_id: str, ids: list[str] | None) -> int:
    return store.mark_read(user_id, sorted(_mine(user_id)), ids, _now())


# ---------------------------------------------------------------------------
# Automations 5 and 7 — FR-CRM-AUTO-01, -02
# ---------------------------------------------------------------------------


def automation_on(workspace_id: str, key: str) -> bool:
    """FR-CRM-AUTO-02 — a switch of the `automations` setting; on unless an
    admin switched it off."""
    found = next(
        (s["value"] for s in store.list_settings(workspace_id) if s["key"] == "automations"), None
    )
    return not (isinstance(found, dict) and found.get(key) is False)


def _profiles() -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in db.list_profiles(include_crm_only=True)}


def stale_deal_notices(workspace: dict[str, Any], now: datetime) -> dict[str, int]:
    """Automation 5 — a deal gone stale tells its owner, and at red the
    workspace admins too, once per deal per level (the dedupe key names the
    level and when the quiet began)."""
    ws_id = workspace["id"]
    admins = [m["user_id"] for m in store.list_members(ws_id) if m["role"] == "admin"]
    rows = []
    for deal in store.flag_rows(ws_id, "crm_opportunities", flagged_only=True):
        stale = next((f for f in deal.get("flags") or [] if f.get("kind") == "stale_deal"), None)
        if stale is None or deal.get("stage") not in flag_rules.OPEN_STAGES:
            continue
        level, since = stale["level"], str(stale.get("since") or "")[:10]
        to = {deal["owner_id"]} | (set(admins) if level == "red" else set())
        title = f"Deal going stale ({level}): “{deal['name']}”"
        for user_id in sorted(u for u in to if u):
            row = _row(
                ws_id,
                user_id,
                "stale_deal",
                title,
                f"stale_deal:{deal['id']}:{level}:{since}",
                "opportunity",
                deal["id"],
            )
            row["body"] = (
                f"{stale.get('detail') or 'Nothing has happened on it lately.'} "
                f"Open it in {workspace['name']}."
            )
            rows.append(row)
    counts = send(rows, out_of_app=True, profiles=_profiles())
    return {"stale_notices": counts["added"], "undeliverable": counts["undeliverable"]}


def task_reminders(workspace: dict[str, Any], now: datetime) -> dict[str, int]:
    """Automation 7 — a task due today is a bell notification; an overdue
    one also a message, once per task per day, from 09:00 the assignee's
    time (nobody is told about a task at midnight)."""
    ws_id = workspace["id"]
    profiles = _profiles()
    # No time zone is a day ahead of UTC by more than one date.
    latest = (now.astimezone(UTC) + timedelta(days=1)).date()
    due, overdue = [], []
    for task in store.tasks_due_by(ws_id, latest.isoformat()):
        person = profiles.get(task["assignee_id"]) or {}
        local = now.astimezone(ZoneInfo(person.get("timezone") or today_rules.DEFAULT_ZONE))
        if local.hour < 9:
            continue
        day = local.date()
        due_on = str(task["due_on"])[:10]
        if due_on == day.isoformat():
            due.append(
                _row(
                    ws_id,
                    task["assignee_id"],
                    "task_due",
                    f"Task due today: {task['title']}",
                    f"task_due:{task['id']}:{day.isoformat()}",
                    "task",
                    task["id"],
                )
            )
        elif due_on < day.isoformat():
            row = _row(
                ws_id,
                task["assignee_id"],
                "task_overdue",
                f"Task overdue: {task['title']}",
                f"task_overdue:{task['id']}:{day.isoformat()}",
                "task",
                task["id"],
            )
            row["body"] = (
                f"“{task['title']}” was due {long_date(date.fromisoformat(due_on))} "
                f"in {workspace['name']}."
            )
            overdue.append(row)
    sent_due = send(due)
    sent_overdue = send(overdue, out_of_app=True, profiles=profiles)
    return {
        "due_today": sent_due["added"],
        "overdue": sent_overdue["added"],
        "undeliverable": sent_overdue["undeliverable"],
    }


def _at(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def reply_owed_notices(workspace: dict[str, Any], now: datetime) -> int:
    """FR-CRM-NOTIF-01 — a person, company or deal whose Reply owed flag
    turned red tells its owner, in the bell and by message, once per email
    waiting (the dedupe key names when it arrived). Sent after each email
    sync run (S10), which the 30-minute flags have turned red meanwhile.

    A backfill reads up to 90 days of mail at once (FR-CRM-SYNC-08), and
    every unanswered email in it is red already: a flag that turned red
    before the latest backfill finished was never watched turning red, so
    it shows on the record and tells nobody."""
    ws_id = workspace["id"]
    found = next(
        (s["value"] for s in store.list_settings(ws_id) if s["key"] == "flag_thresholds"), None
    )
    red_after = timedelta(hours=flag_rules.thresholds(found)["reply_owed_red_hours"])
    watched_from = max(
        (_at(m["backfilled_at"]) for m in store.list_mailboxes(ws_id) if m.get("backfilled_at")),
        default=None,
    )
    rows = []
    for table, record_type in (
        ("crm_people", "person"),
        ("crm_companies", "company"),
        ("crm_opportunities", "opportunity"),
    ):
        for row in store.flag_rows(ws_id, table, flagged_only=True):
            owed = next((f for f in row.get("flags") or [] if f.get("kind") == "reply_owed"), None)
            if owed is None or owed.get("level") != "red" or not row.get("owner_id"):
                continue
            since = _at(owed.get("since"))
            if watched_from and since and since + red_after <= watched_from:
                continue
            name = row.get("name") or dedupe.full_name(row.get("first_name"), row.get("last_name"))
            notice = _row(
                ws_id,
                row["owner_id"],
                "reply_owed_red",
                f"Reply owed: “{name}”",
                f"reply_owed_red:{record_type}:{row['id']}:{owed.get('since') or ''}",
                record_type,
                row["id"],
            )
            notice["body"] = (
                f"{owed.get('detail') or 'Their email is waiting.'} In {workspace['name']}."
            )
            rows.append(notice)
    return send(rows, out_of_app=True, profiles=_profiles())["added"] if rows else 0
