"""The CEO dashboard — spec 003 FR-DASH.

Who may see it, and the few judgements the summary makes on top of the
figures it borrows from the detail pages: whose day it is, who is free, whose
work runs out, and what deserves the owner's attention first. Pure: hand it
the rows, get the verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain.calendar import is_weekend
from app.domain.rules import CATEGORIES

#: FR-DASH-09 — the last working day's coverage below this is an alert.
COVERAGE_ALERT_BELOW = Decimal("0.80")
#: FR-DASH-08 — an allocation running out within this many days is flagged.
ENDING_WITHIN_DAYS = 30
#: FR-DASH-08 — a project whose hours budget is burnt past this is listed.
BURN_WATCH_PCT = Decimal("80")

HIGH, MEDIUM, LOW = "high", "medium", "low"
_SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}


def may_view_dashboard(profile: Mapping[str, Any]) -> bool:
    """FR-DASH-02 — the owner, or someone the owner authorised. Never a role:
    an admin without the flag is refused like anybody else.

    `is True`, not truthiness: a column read as anything but a real boolean
    must not open the dashboard."""
    return profile.get("is_owner") is True or profile.get("dashboard_access") is True


def last_working_day(today: date, holidays: set[date]) -> date:
    """The most recent weekday before today that was not a declared holiday —
    "yesterday" for the data-health row, so a Monday looks at Friday."""
    day = today - timedelta(days=1)
    while is_weekend(day) or day in holidays:
        day -= timedelta(days=1)
    return day


def day_status(user_ids: list[str], bookings: list[dict]) -> dict[str, Any]:
    """Present, working from home, on leave by category, or flagged
    unrecognised — everyone exactly once (the Team page's rule, FR-LEAD-01).
    A pending request counts as away, as it does there."""
    by_user = {b["user_id"]: b for b in bookings}
    leave = {c: 0 for c in CATEGORIES if c != "wfh"}
    out: dict[str, Any] = {
        "people": len(user_ids),
        "present": 0,
        "wfh": 0,
        "leave": leave,
        "unrecognised": 0,
    }
    for uid in user_ids:
        booking = by_user.get(uid)
        if booking is None:
            out["present"] += 1
        elif booking["status"] == "unrecognised":
            out["unrecognised"] += 1
        elif booking.get("category") == "wfh":
            out["wfh"] += 1
        elif booking.get("category"):
            leave[booking["category"]] = leave.get(booking["category"], 0) + 1
    return out


def _confirmed_on(allocations: list[dict], user_id: str, tentative_ids: set[str]) -> list[dict]:
    return [
        a for a in allocations if a["user_id"] == user_id and a["project_id"] not in tentative_ids
    ]


def unallocated(
    people: list[dict], allocations: list[dict], day: date, tentative_ids: set[str]
) -> list[dict]:
    """People with no confirmed allocation covering `day`. Pencilled in on a
    tentative project is still free (`002` FR-PROJ-08), as on the bench."""
    out = []
    for person in people:
        mine = _confirmed_on(allocations, person["id"], tentative_ids)
        if not any(a["starts_on"] <= day.isoformat() <= a["ends_on"] for a in mine):
            out.append({"user_id": person["id"], "display_name": person["display_name"]})
    return sorted(out, key=lambda r: r["display_name"])


def allocations_ending(
    people: list[dict],
    allocations: list[dict],
    today: date,
    tentative_ids: set[str],
    within_days: int = ENDING_WITHIN_DAYS,
) -> list[dict]:
    """People whose confirmed work runs out within `within_days` with nothing
    confirmed after it: the latest end among their allocations still running
    or to come falls inside the window. A follow-on is any later confirmed
    allocation, so it moves that latest end past the window."""
    horizon = today + timedelta(days=within_days)
    out = []
    for person in people:
        ends = [
            a["ends_on"]
            for a in _confirmed_on(allocations, person["id"], tentative_ids)
            if a["ends_on"] >= today.isoformat()
        ]
        if ends and max(ends) <= horizon.isoformat():
            out.append(
                {
                    "user_id": person["id"],
                    "display_name": person["display_name"],
                    "ends_on": max(ends),
                }
            )
    return sorted(out, key=lambda r: (r["ends_on"], r["display_name"]))


def _names(rows: list[dict], key: str = "display_name") -> str:
    return ", ".join(r[key] for r in rows)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def attention(summary: dict[str, Any]) -> list[dict[str, str]]:
    """FR-DASH-09 — what deserves the owner's attention, most severe first.

    Built only from the summary's own sections, so every alert can be checked
    against a figure on the same page and the detail page it links to.
    Amounts are quoted as the API returned them; nothing is added up here.
    """
    alerts: list[dict[str, str]] = []
    currency = summary["money"]["currency"]

    def add(severity: str, title: str, detail: str, link: str) -> None:
        alerts.append({"severity": severity, "title": title, "detail": detail, "link": link})

    for project in summary["delivery"]["red"]:
        add(
            HIGH,
            f"{project['project_name']} is red",
            " ".join(project["reasons"]) or "Project health is red.",
            "/analytics",
        )

    cash = summary["cash"]
    if cash["overdue_count"] or cash["uninvoiced_overdue_count"]:
        parts = []
        if cash["overdue_count"]:
            parts.append(
                f"{_plural(cash['overdue_count'], 'invoice is', 'invoices are')} past "
                f"payment terms ({currency} {cash['totals']['overdue_receivable']} owed)"
            )
        if cash["uninvoiced_overdue_count"]:
            parts.append(
                f"{_plural(cash['uninvoiced_overdue_count'], 'milestone is', 'milestones are')}"
                " past due and not yet invoiced"
            )
        add(HIGH, "Overdue invoices", "; ".join(parts) + ".", "/analytics")

    incomplete = summary["money"]["incomplete"]
    if incomplete["unrated"] or incomplete["no_timeline"]:
        why = []
        if incomplete["unrated"]:
            why.append(f"no CTC recorded for {_names(incomplete['unrated'])}")
        if incomplete["no_timeline"]:
            why.append(
                f"revenue with no phases (so no monthly revenue) on "
                f"{_names(incomplete['no_timeline'], 'project_name')}"
            )
        add(
            HIGH,
            "Money figures are incomplete",
            "This or last month's profit is not the whole picture: " + "; ".join(why) + ".",
            "/admin",
        )

    unattributed = summary["money"]["unattributed"]
    if unattributed:
        add(
            MEDIUM,
            "Revenue with nobody allocated",
            f"{_names(unattributed, 'project_name')} "
            f"{'earns' if len(unattributed) == 1 else 'earn'} revenue this month "
            "but nobody is allocated to deliver it.",
            "/projects",
        )

    spill = summary["delivery"]["spillover"]
    if spill:
        # FR-DASH-12 — below manager, the projects are named but not costed.
        lines = [
            p["project_name"]
            if not summary["money"]["breakdown"]
            else f"{p['project_name']} ({currency} {p['cost']} planned cost this month)"
            if p["cost"] is not None
            else f"{p['project_name']} (cost unknown — incomplete)"
            for p in spill
        ]
        add(
            MEDIUM,
            "Spill-over is costing money",
            "In an unpaid spill-over phase: " + ", ".join(lines) + ".",
            "/analytics",
        )

    last_day = summary["data_health"]["last_working_day"]
    ratio = last_day["coverage"]
    if ratio is not None and Decimal(ratio) < COVERAGE_ALERT_BELOW:
        missing = summary["data_health"]["missing_last_working_day"]
        add(
            MEDIUM,
            f"Timesheets for {last_day['start']} are {int(Decimal(ratio) * 100)}% complete",
            f"{_plural(len(missing), 'person has', 'people have')} not logged: "
            f"{_names(missing)}. Effort and cost for that day are understated until they do.",
            "/analytics",
        )

    people = summary["people"]
    if people["unallocated"]:
        add(
            MEDIUM,
            _plural(len(people["unallocated"]), "person is unallocated", "people are unallocated")
            + " today",
            f"No confirmed allocation today: {_names(people['unallocated'])}.",
            "/projects",
        )

    if people["ending"]:
        add(
            LOW,
            "Allocations ending without a follow-on",
            "Confirmed work runs out within 30 days with nothing after it: "
            + ", ".join(f"{p['display_name']} ({p['ends_on']})" for p in people["ending"])
            + ".",
            "/projects",
        )

    return sorted(alerts, key=lambda a: _SEVERITY_ORDER[a["severity"]])
