"""The holiday calendar — spec 001 FR-HOL, spec 006 FR-LOC-02.

Pure checks on which dates are already taken. The routes supply the holidays
already declared; nothing here reads or writes.
"""

from __future__ import annotations

from datetime import date


def clash(existing: list[dict], day: date, location_id: str | None) -> dict | None:
    """The declared holiday that already covers `day` for `location_id`, if any.

    A holiday everywhere (None) clashes with any holiday that day; one for a
    location clashes with one everywhere or one for the same location. Two
    different locations may share a date.
    """
    for h in existing:
        if str(h["date"]) != day.isoformat():
            continue
        if location_id is None or h.get("location_id") in (None, location_id):
            return h
    return None


def plan_bulk(entries: list[dict], existing: list[dict]) -> tuple[list[dict], list[dict]]:
    """FR-HOL-08 — split a pasted list into what to declare and what to skip.

    `entries` are {date, name, location_id} in the order given. A date already
    declared for that location is skipped, not renamed: the admin pasted a
    list, not an edit. A line that repeats an earlier one in the same list is
    skipped for the same reason, so the first wins.
    """
    taken = list(existing)
    declare: list[dict] = []
    skipped: list[dict] = []
    for entry in entries:
        day, location_id = entry["date"], entry.get("location_id")
        hit = clash(taken, day, location_id)
        if hit is None:
            declare.append(entry)
            taken.append({"date": day.isoformat(), "location_id": location_id, "listed": True})
            continue
        reason = (
            "Listed earlier in this list."
            if hit.get("listed")
            else f"Already a holiday ({hit['name']})."
        )
        skipped.append(
            {
                "date": day.isoformat(),
                "name": entry["name"],
                "location_id": location_id,
                "reason": reason,
            }
        )
    return declare, skipped
