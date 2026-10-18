"""CRM digests — spec 007 FR-CRM-NOTIF-02 to -05, FR-CRM-AUTO-01 (automation
6), FR-CRM-ACC-11, FR-CRM-WS-02; Q-18, Q-19; plan §5.11.

Two digests, each sent once (the service holds the sent markers):

- **Daily**, at 09:00 in the person's own time zone: their Today, one
  section per workspace they are a member of at that moment, each built with
  their role there. A Viewer's section carries no amount; a Member's or
  Admin's shows each deal's amount in INR beside its next step. A person who
  is a Viewer in one workspace and a Member in the other gets one section of
  each kind (FR-CRM-ACC-11).
- **Weekly**, Monday 09:00 in each recipient's time zone, to each
  workspace's Admins, one per workspace: what moved through the pipeline,
  what was won and lost, and how many records carry each flag. It is not
  TVA's leadership digest and is sent apart from it (Q-19).

Pure: the service gathers the rows and decides who receives what.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

#: FR-CRM-NOTIF-03, -04: the hour digests go out, local time. The digest
#: tick runs every 15 minutes (plan §5.11); a person is due in every tick
#: from 09:00 their time to the end of their day, and the sent marker makes
#: it once, so a missed or late tick delays a digest rather than losing it.
SEND_AT: Final = time(9, 0)
DEFAULT_ZONE: Final = "Asia/Kolkata"

#: Today's section keys and the headings a digest gives them.
HEADINGS: Final = {
    "replies_owed": "Replies you owe",
    "next_steps": "Deal next steps due",
    "tasks": "Tasks for today",
    "silent_clients": "Clients gone silent",
    "loose_ends": "Loose ends",
    # Spec 009 FR-CRMAI-REV-09: Today's sixth section.
    "to_review": "To review",
}
STAGE_LABELS: Final = {
    "discovery": "Discovery",
    "qualification": "Qualification",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closed_won": "Closed Won",
    "closed_lost": "Closed Lost",
}
FLAG_OBJECTS: Final = {
    "crm_companies": "Companies",
    "crm_people": "People",
    "crm_opportunities": "Deals",
    "tasks": "Tasks",
}
#: Spec 009 FR-CRMAI-RULE-03, FR-CRMAI-CHG-05: flag kinds the weekly digest
#: also counts, when the service gives them (Stuck in stage, while R-2 is on).
FLAG_KINDS: Final = {"stuck_in_stage": "Deals stuck in stage"}


def local_day_due(now: datetime, zone: str | None) -> date | None:
    """The person's date once it is 09:00 or later their time, else None."""
    local = now.astimezone(ZoneInfo(zone or DEFAULT_ZONE))
    if local.time() >= SEND_AT:
        return local.date()
    return None


def is_weekly_day(day: date) -> bool:
    """FR-CRM-NOTIF-04 — Mondays."""
    return day.weekday() == 0


def inr(value: Any) -> str:
    """An INR amount the Indian way: ₹12,34,567 (paise dropped)."""
    whole = int(Decimal(str(value)).to_integral_value())
    digits = str(abs(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{'-' if whole < 0 else ''}₹{digits}"


def long_date(day: date) -> str:
    """'15 Oct 2026', as the CRM shows dates (owner decisions)."""
    return f"{day.day} {day.strftime('%b %Y')}"


# ---------------------------------------------------------------------------
# Daily — FR-CRM-NOTIF-03
# ---------------------------------------------------------------------------


def daily_section(
    workspace: dict[str, Any],
    role: str,
    sections: list[dict[str, Any]],
    amounts: dict[str, Any],
) -> dict[str, Any]:
    """One workspace's part of a daily digest: its Today sections, each item
    with its amount in INR when the role may see amounts. `amounts`: deal id
    → amount in INR. A Viewer's section has no `amount` anywhere
    (FR-CRM-ACC-11)."""
    sees_amounts = role in ("admin", "member")
    out = []
    for section in sections:
        items = []
        for item in section.get("items") or []:
            line = {k: item.get(k) for k in ("record_type", "record_id", "title", "detail")}
            line["level"] = item.get("level")
            amount = amounts.get(item.get("record_id") or "")
            if sees_amounts and item.get("record_type") == "opportunity" and amount is not None:
                line["amount"] = inr(amount)
            items.append(line)
        part = {
            "key": section["key"],
            "heading": HEADINGS.get(section["key"], section["key"]),
            "off_reason": section.get("off_reason"),
            "items": items,
        }
        if "count" in section:
            # Spec 009 FR-CRMAI-REV-09: To review's count of open items,
            # beside its three oldest.
            part["count"] = section["count"]
        out.append(part)
    return {"workspace": workspace["name"], "workspace_key": workspace["key"], "sections": out}


def daily_is_empty(parts: list[dict[str, Any]]) -> bool:
    return not any(s["items"] for part in parts for s in part["sections"])


def daily_subject(day: date) -> str:
    return f"Your CRM day — {long_date(day)}"


def daily_body(name: str, day: date, parts: list[dict[str, Any]], link: str) -> str:
    """The message: one block per workspace, then where to open it."""
    lines = [f"Good morning {name}. Your CRM for {long_date(day)}:"]
    for part in parts:
        lines += ["", f"{part['workspace']}"]
        shown = False
        for section in part["sections"]:
            if section.get("off_reason") or not section["items"]:
                continue
            shown = True
            count = f" ({section['count']})" if section.get("count") is not None else ""
            lines.append(f"  {section['heading']}{count}:")
            for item in section["items"]:
                text = f"    - {item['title']}"
                if item.get("amount"):
                    text += f" ({item['amount']})"
                if item.get("detail"):
                    text += f": {item['detail']}"
                if item.get("level") in ("overdue", "red"):
                    text += " [overdue]" if item["level"] == "overdue" else " [red]"
                lines.append(text)
        if not shown:
            lines.append("  Nothing due today.")
    lines += ["", f"Open the CRM: {link}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Weekly — FR-CRM-NOTIF-04
# ---------------------------------------------------------------------------


def weekly_content(
    workspace: dict[str, Any],
    *,
    since: date,
    until: date,
    moves: list[dict[str, Any]],
    closed: list[dict[str, Any]],
    flag_counts: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Pipeline movement (stage changes in the week, by stage entered), won
    and lost (each deal with its amount in INR: the recipients are Admins),
    and flag counts per object."""
    entered: dict[str, int] = {}
    for move in moves:
        if move.get("from_stage") is None:
            continue
        entered[move["to_stage"]] = entered.get(move["to_stage"], 0) + 1
    won = [d for d in closed if d.get("stage") == "closed_won"]
    lost = [d for d in closed if d.get("stage") == "closed_lost"]

    def deal(d: dict[str, Any]) -> dict[str, Any]:
        out = {"id": d["id"], "name": d["name"]}
        if d.get("amount_in_inr") is not None:
            out["amount"] = inr(d["amount_in_inr"])
        return out

    return {
        "workspace": workspace["name"],
        "workspace_key": workspace["key"],
        "since": since.isoformat(),
        "until": until.isoformat(),
        "moved": [
            {"stage": s, "label": STAGE_LABELS[s], "count": entered[s]}
            for s in STAGE_LABELS
            if entered.get(s)
        ],
        "won": [deal(d) for d in sorted(won, key=lambda d: d["name"].lower())],
        "won_total": inr(
            sum((Decimal(str(d["amount_in_inr"])) for d in won if d.get("amount_in_inr")), 0)
        ),
        "lost": [deal(d) for d in sorted(lost, key=lambda d: d["name"].lower())],
        "flags": [
            {
                "object": FLAG_OBJECTS[t],
                "red": flag_counts.get(t, {}).get("red", 0),
                "amber": flag_counts.get(t, {}).get("amber", 0),
            }
            for t in FLAG_OBJECTS
        ],
        "flag_kinds": [
            {
                "kind": kind,
                "label": label,
                "red": flag_counts[kind].get("red", 0),
                "amber": flag_counts[kind].get("amber", 0),
            }
            for kind, label in FLAG_KINDS.items()
            if kind in flag_counts
        ],
    }


def weekly_subject(content: dict[str, Any]) -> str:
    return f"{content['workspace']} CRM week — {content['until']}"


def weekly_body(content: dict[str, Any], link: str) -> str:
    lines = [f"{content['workspace']}: the week to {content['until']}.", "", "Pipeline movement:"]
    lines += [f"  - {m['label']}: {m['count']}" for m in content["moved"]] or ["  - No moves."]
    lines += ["", f"Won ({len(content['won'])}, {content['won_total']}):"]
    lines += [f"  - {d['name']} ({d.get('amount', '—')})" for d in content["won"]] or ["  - None."]
    lines += ["", f"Lost ({len(content['lost'])}):"]
    lines += [f"  - {d['name']}" for d in content["lost"]] or ["  - None."]
    lines += ["", "Follow-up and deal-health flags:"]
    lines += [f"  - {f['object']}: {f['red']} red, {f['amber']} amber" for f in content["flags"]]
    lines += [
        f"  - {k['label']}: {k['red']} red, {k['amber']} amber"
        for k in content.get("flag_kinds") or []
    ]
    lines += ["", f"Open the CRM: {link}"]
    return "\n".join(lines)
