"""Today — spec 007 FR-CRM-TODAY-01 to -04, FR-CRM-CHG-13; Q-26, Q-50;
contracts §2.8 `GET /crm/today`; plan §5.8.

The CRM home answers "what do I do today", one line per item, each naming
the record it opens, in five sections in a fixed order:

1. replies I owe (1b, from email sync);
2. my deals' next steps due today or overdue;
3. my tasks in the Today bucket, or due today or before;
4. clients gone silent on my open deals (1b, from email sync);
5. my loose ends: companies I own in Nurturing with no open task and no
   next touch (spec §5.21 "Loose end").

Until email sync is live in the workspace, sections 1 and 4 say "Email sync
is off" instead of appearing empty (FR-CRM-TODAY-02, Q-50).

"Today" is the person's own day, in their time zone (FR-CRM-ACC-15), never
the server's: a Sydney seller's day turns over hours before Chennai's. A
workspace Admin may open a member's Today (FR-CRM-TODAY-03), a sales-only
exception to TVA's no-ranking rules (FR-CRM-CHG-13); nobody else may.

Pure: the service passes the rows, `today` and `now`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

#: FR-CRM-TODAY-01, in this order.
SECTIONS: Final = ("replies_owed", "next_steps", "tasks", "silent_clients", "loose_ends")
#: The sections built from email sync (1b).
EMAIL_SECTIONS: Final = frozenset({"replies_owed", "silent_clients"})
#: FR-CRM-TODAY-02.
EMAIL_SYNC_OFF: Final = "Email sync is off"
#: The CRM's default time zone, as TVA's (FR-CRM-ACC-15).
DEFAULT_ZONE: Final = "Asia/Kolkata"

NOT_YOURS: Final = "Only a workspace admin can see another member's Today."
NOT_A_MEMBER: Final = "No such member in this workspace."


def day_of(now: datetime, zone: str | None) -> date:
    """The person's calendar day at `now` (an aware datetime)."""
    return now.astimezone(ZoneInfo(zone or DEFAULT_ZONE)).date()


def email_sync_on(settings: dict[str, Any]) -> bool:
    """Whether the workspace's `email_sync` switch (039) is on. Before 039
    there is no such setting, so it is off."""
    value = settings.get("email_sync")
    return isinstance(value, dict) and value.get("enabled") is True


def may_see(*, is_admin: bool, caller_id: str, person_id: str) -> str | None:
    """FR-CRM-TODAY-03 — anyone sees their own; only an Admin another's."""
    if person_id == caller_id or is_admin:
        return None
    return NOT_YOURS


def _due(due_on: str, today: date) -> tuple[int, str]:
    """Overdue lines first, then today's; `level` says which."""
    return (0, "overdue") if date.fromisoformat(str(due_on)[:10]) < today else (1, "today")


def next_steps(deals: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Section 2 — open deals whose next step is due today or overdue,
    oldest first. `deals` are the person's open deals with a next step on or
    before `today`; anything else is ignored."""
    items = []
    for deal in deals:
        due_on = deal.get("next_step_on")
        if not due_on or date.fromisoformat(str(due_on)[:10]) > today:
            continue
        _, level = _due(str(due_on), today)
        company = deal.get("company_name")
        step = deal.get("next_step") or ""
        items.append(
            {
                "record_type": "opportunity",
                "record_id": deal["id"],
                "title": deal["name"],
                "detail": f"{step} · {company}" if company else step,
                "due_on": str(due_on)[:10],
                "level": level,
            }
        )
    return sorted(items, key=lambda i: (i["due_on"], i["title"].lower(), i["record_id"]))


def tasks(
    rows: list[dict[str, Any]],
    today: date,
    names: dict[str, list[str]] | None = None,
    opens: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Section 3 — open or waiting tasks in the Today bucket or due today or
    before (008 FR-TASK-CHG-05).
    Overdue first, then due today, then the Today bucket without a date
    (`names`: task id → the records it links to; `opens`: task id → the
    first of them, which a task line opens, having no page of its own)."""
    items = []
    for task in rows:
        if task.get("status") not in ("open", "waiting"):
            continue
        due_on = task.get("due_on")
        overdue_or_today = due_on and date.fromisoformat(str(due_on)[:10]) <= today
        if not overdue_or_today and task.get("bucket") != "today":
            continue
        rank, level = _due(due_on, today) if overdue_or_today else (2, None)
        linked = (names or {}).get(task["id"]) or []
        item: dict[str, Any] = {
            "record_type": "task",
            "record_id": task["id"],
            "title": task["title"],
            "detail": ", ".join(linked) if linked else "Today",
            "due_on": str(due_on)[:10] if due_on else None,
            "level": level,
            "opens": (opens or {}).get(task["id"]),
        }
        items.append((rank, item["due_on"] or "", item["title"].lower(), task["id"], item))
    return [i[-1] for i in sorted(items, key=lambda i: i[:4])]


def is_loose_end(company: dict[str, Any], has_open_task: bool) -> bool:
    """Spec §5.21 "Loose end": a company in Nurturing with no open task and
    no next touch."""
    return (
        company.get("lead_stage") == "nurturing"
        and not company.get("next_touch_on")
        and not company.get("deleted_at")
        and not has_open_task
    )


def loose_ends(companies: list[dict[str, Any]], with_open_tasks: set[str]) -> list[dict[str, Any]]:
    """Section 5 — by name."""
    items = [
        {
            "record_type": "company",
            "record_id": c["id"],
            "title": c["name"],
            "detail": "Nurturing, with no open task and no next touch",
            "due_on": None,
            "level": None,
        }
        for c in companies
        if is_loose_end(c, c["id"] in with_open_tasks)
    ]
    return sorted(items, key=lambda i: (i["title"].lower(), i["record_id"]))


def _flag_items(rows: list[dict[str, Any]], kind: str, record_type: str) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        flag = next((f for f in row.get("flags") or [] if f.get("kind") == kind), None)
        if flag is None or flag.get("level") not in ("amber", "red"):
            continue
        title = row.get("name") or " ".join(
            p for p in (row.get("first_name"), row.get("last_name")) if p
        )
        items.append(
            {
                "record_type": record_type,
                "record_id": row["id"],
                "title": title,
                "detail": flag.get("detail") or "",
                "due_on": None,
                "level": flag["level"],
            }
        )
    return items


def _red_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda i: (i["level"] != "red", i["title"].lower(), i["record_id"]))


def replies_owed(
    people: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    deals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Section 1 (1b) — the person's records with a Reply owed flag, red
    first (FR-CRM-TODAY-01; flags from domain/crm/flags.py)."""
    return _red_first(
        _flag_items(people, "reply_owed", "person")
        + _flag_items(companies, "reply_owed", "company")
        + _flag_items(deals, "reply_owed", "opportunity")
    )


def silent_clients(deals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Section 4 (1b) — the person's open deals whose client went silent,
    red first."""
    return _red_first(_flag_items(deals, "client_silent", "opportunity"))


def assemble(*, sync_on: bool, built: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The five sections in order. An email section while sync is off carries
    `off_reason` and no items (FR-CRM-TODAY-02)."""
    sections = []
    for key in SECTIONS:
        if key in EMAIL_SECTIONS and not sync_on:
            sections.append({"key": key, "off_reason": EMAIL_SYNC_OFF, "items": []})
        else:
            sections.append({"key": key, "items": built.get(key, [])})
    return sections


# ---------------------------------------------------------------------------
# Spec 009 — To review, the sixth section (FR-CRMAI-REV-09, FR-CRMAI-CHG-03)
# ---------------------------------------------------------------------------

#: After the five of FR-CRM-TODAY-01; shown while the Review Queue is
#: visible (FR-CRMAI-FLAG-04), never to a Viewer.
TO_REVIEW: Final = "to_review"
#: How many of the oldest items the section lists.
TO_REVIEW_SHOWN: Final = 3


def to_review(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The count of the person's open Review Queue items and the three
    oldest, each opening the queue. `items`: `{id, created_at, title,
    detail}`."""
    oldest = sorted(items, key=lambda i: (str(i["created_at"]), i["id"]))
    return {
        "key": TO_REVIEW,
        "count": len(items),
        "items": [
            {
                "record_type": "review_item",
                "record_id": i["id"],
                "title": i["title"],
                "detail": i["detail"],
                "created_at": i["created_at"],
            }
            for i in oldest[:TO_REVIEW_SHOWN]
        ],
    }
