"""Client-ready effort statements — spec 006 FR-STMT. Hours only, never money."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.domain import pnl
from app.services import supabase as db
from app.services.confirmations import monday_of


def statement(project_id: str, period: str) -> dict[str, Any] | None:
    project = db.get_project(project_id)
    if project is None:
        return None
    year, month = (int(x) for x in period.split("-"))
    first, last = pnl.month_bounds(year, month)
    entries = [e for e in db.list_time_entries(project_id=project_id, start=first, end=last)]
    names = {p["id"]: p["display_name"] for p in db.list_profiles()}
    phases = {ph["id"]: ph for ph in db.list_phases(project_id)}
    labels = {"pre": "Pre-project", "delivery": "Delivery", "support": "Post-delivery support"}

    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    by_person: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    by_phase: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    notes: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        hours = Decimal(str(e["hours_office"])) + Decimal(str(e["hours_home"]))
        by_person[e["user_id"]][e["date"]] += hours
        phase = phases.get(e.get("phase_id") or "")
        by_phase[labels[phase["phase"]] if phase else "Outside any phase"] += hours
        if e.get("note"):
            notes[e["user_id"]].append({"date": e["date"], "note": e["note"]})

    # FR-STMT-03 — which weeks in the month are not yet confirmed, per person.
    weeks = sorted({monday_of(d) for d in days})
    confirmations = {
        (c["user_id"], c["week_start"]): c["status"]
        for c in db.list_confirmations(user_ids=list(by_person))
    }

    people = []
    for uid in sorted(by_person, key=lambda u: names.get(u, "")):
        per_day = by_person[uid]
        unconfirmed = [w.isoformat() for w in weeks if (uid, w.isoformat()) not in confirmations]
        people.append(
            {
                "user_id": uid,
                "display_name": names.get(uid, "—"),
                "days": {d: str(h) for d, h in sorted(per_day.items())},
                "total": str(sum(per_day.values(), Decimal("0"))),
                "unconfirmed_weeks": unconfirmed,
                "notes": sorted(notes[uid], key=lambda n: n["date"]),
            }
        )
    total = sum((Decimal(p["total"]) for p in people), Decimal("0"))
    return {
        "project": {"id": project["id"], "name": project["name"], "client": project.get("client")},
        "period": period,
        "first": first.isoformat(),
        "last": last.isoformat(),
        "days": [d.isoformat() for d in days],
        "people": people,
        "by_phase": [{"phase": k, "hours": str(v)} for k, v in sorted(by_phase.items())],
        "total_hours": str(total),
        "unconfirmed": any(p["unconfirmed_weeks"] for p in people),
    }


def as_csv(data: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"{data['project']['name']} — effort statement {data['period']}"])
    writer.writerow(["Person", *data["days"], "Total", "Unconfirmed weeks"])
    for person in data["people"]:
        writer.writerow(
            [
                person["display_name"],
                *[person["days"].get(d, "") for d in data["days"]],
                person["total"],
                "; ".join(person["unconfirmed_weeks"]),
            ]
        )
    writer.writerow([])
    writer.writerow(["Phase", "Hours"])
    for row in data["by_phase"]:
        writer.writerow([row["phase"], row["hours"]])
    writer.writerow(["Total", data["total_hours"]])
    return buffer.getvalue()
