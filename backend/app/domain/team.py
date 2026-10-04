"""The lead's day summary — FR-LEAD-01/02/03.

Pure: hand it the roster entries, get the counts the Team page shows.
"""

from __future__ import annotations

from app.domain.rules import CATEGORIES


def summarise(entries: list[dict]) -> dict[str, int]:
    """Present, each booking category, and unrecognised — everyone once.

    Keyed from CATEGORIES so a new category appears without touching this,
    and a category this code has never heard of is counted rather than
    failing the page."""
    counts = {"present": 0, **{c: 0 for c in CATEGORIES}, "unrecognised": 0}
    for entry in entries:
        if entry["state"] == "present":
            counts["present"] += 1
        elif entry["state"] == "unrecognised":
            counts["unrecognised"] += 1
        elif entry["category"]:
            counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    return counts
