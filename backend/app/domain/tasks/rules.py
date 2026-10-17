"""Task rules — spec 008 FR-TASK-DATA-02, -03, -08, FR-TASK-LIST-01 to -03,
FR-TASK-CHG-06; TP-06, TP-14; plan §5.3, §5.6.

Computed, not stored (plan §1 choice 5): the effective bucket and days
overdue are worked out when a task is read; nothing rewrites a task at
night. "Today" and the current week are the person's, in their own time zone
(FR-TASK-CHG-06), which the caller passes as `today`.

Pure: the service passes the task, `today` and `now`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

#: The statuses that count as open (FR-TASK-DATA-04); the rest are closed.
OPEN: Final = ("open", "waiting")
#: Most urgent first (FR-TASK-LIST-01 sorts by it).
PRIORITIES: Final = ("urgent", "high", "normal", "low")
#: TP-14 — count and length only, nothing stricter.
MAX_TAGS: Final = 10
MAX_TAG_LENGTH: Final = 40
#: The portal's default zone, as TVA's and the CRM's.
DEFAULT_ZONE: Final = "Asia/Kolkata"


def day_of(now: datetime, zone: str | None) -> date:
    """The person's calendar day at `now` (an aware datetime), in their zone
    (FR-TASK-CHG-06)."""
    return now.astimezone(ZoneInfo(zone or DEFAULT_ZONE)).date()


def week_of(today: date) -> tuple[date, date]:
    """The current week, Monday to Sunday, around the person's `today`."""
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def _day(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def is_open(task: dict[str, Any]) -> bool:
    return task.get("status") in OPEN


def default_bucket(due_on: Any, today: date) -> str:
    """FR-TASK-DATA-08 — Today when due today or earlier, otherwise Later."""
    due = _day(due_on)
    return "today" if due is not None and due <= today else "later"


def effective_bucket(task: dict[str, Any], today: date) -> str | None:
    """FR-TASK-LIST-02 (TP-06) — an open or waiting task due today or earlier
    shows in Today whatever its stored bucket. Nothing is rewritten."""
    due = _day(task.get("due_on"))
    if is_open(task) and due is not None and due <= today:
        return "today"
    return task.get("bucket")


def days_overdue(task: dict[str, Any], today: date) -> int:
    """FR-TASK-LIST-03 — by the CRM's Task overdue rule (`007` FR-CRM-FLAG):
    an open or waiting, live task whose due date is past. 0 otherwise."""
    due = _day(task.get("due_on"))
    if not is_open(task) or task.get("deleted_at") or due is None or due >= today:
        return 0
    return (today - due).days


def status_values(new: str, was: str | None, now: datetime) -> dict[str, Any]:
    """FR-TASK-DATA-03 — the status to write and its `completed_at`: set when
    the status becomes Done, kept while it stays Done, cleared otherwise."""
    if new == "done":
        return (
            {"status": new} if was == "done" else {"status": new, "completed_at": now.isoformat()}
        )
    return {"status": new, "completed_at": None}


def normalise_tags(tags: Iterable[str]) -> list[str]:
    """TP-14 — each tag trimmed, an exact repeat dropped, case kept as typed.
    Raises ValueError with a sentence safe to show when a tag is empty or over
    40 characters, or there are more than 10."""
    kept: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if not text:
            raise ValueError("A tag cannot be empty.")
        if len(text) > MAX_TAG_LENGTH:
            raise ValueError(f"A tag can be at most {MAX_TAG_LENGTH} characters.")
        if text not in kept:
            kept.append(text)
    if len(kept) > MAX_TAGS:
        raise ValueError(f"A task can have at most {MAX_TAGS} tags.")
    return kept


def kind(task: dict[str, Any], links: Iterable[dict[str, Any]]) -> str:
    """Spec §3.1 — a task's kind follows from its links; it is not stored.
    A CRM link makes a CRM task whatever else it links; a project link with
    no CRM link a project task; otherwise private, or shared personal when
    assigned to someone other than its creator."""
    types = {link["record_type"] for link in links}
    if task.get("workspace_id") or types - {"tva_project"}:
        return "crm"
    if "tva_project" in types:
        return "project"
    if task.get("assignee_id") and task.get("assignee_id") != task.get("created_by"):
        return "shared_personal"
    return "private"


def sort_key(task: dict[str, Any]) -> tuple[Any, ...]:
    """FR-TASK-LIST-01 — within a section: due date (none last), then
    priority, then title."""
    due = _day(task.get("due_on"))
    priority = task.get("priority") or "normal"
    return (
        due is None,
        due or date.max,
        PRIORITIES.index(priority) if priority in PRIORITIES else len(PRIORITIES),
        (task.get("title") or "").lower(),
    )


def missing_sentence(fields: Iterable[str]) -> str:
    """FR-TASK-DATA-02 — one sentence naming every missing or invalid field."""
    names = [f.replace("_", " ") for f in dict.fromkeys(fields)]
    if not names:
        return "That task is not valid."
    if len(names) == 1:
        return f"{names[0].capitalize()} is missing or not valid."
    return f"{', '.join(names[:-1]).capitalize()} and {names[-1]} are missing or not valid."
