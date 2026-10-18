"""The Life OS import's rules — spec 008 FR-TASK-IMP-02 to -05, -07; TP-17;
Journey 4.1; data-model §2.3.

Pure: the service reads the CSV's text, the active accounts, the global
phase default and the content keys that already exist, and passes them in
with `now` and the uploader's zone. Nothing here reads or writes.

- **Columns** (FR-TASK-IMP-02): Task → title, Status → status, Priority →
  priority, Owner → assignee, Due Date → due date, Tags → tags (comma
  separated), Notes / Update → notes, Completed Date → completed at. A file
  with no Task column is refused; a missing other column reads as blank.
- **Dates**: `YYYY-MM-DD`, `D/M/YYYY` (day first), `D Mon YYYY` or
  `D Month YYYY`. A `D/M/YYYY` date whose day and month are both 12 or less
  and differ is listed, read day first, so the owner can check it.
- **Owners** (FR-TASK-IMP-04): each distinct Owner value ("(blank)" for a
  blank cell) maps to the email of an active account. The proposal matches
  the full display name, else a first name unique among active accounts,
  ignoring case; the owner's edits replace it. A mapped owner needs My Tasks
  switched on (FR-TASK-ROLL-04).
- **Status and priority** use the fixed mapping below, which is not
  editable: a value it does not know blocks the real run, and is fixed in
  the sheet and the file exported again (Journey 4.1, TP-17). A blank status
  is Open and a blank priority Normal.
- **Content key** (FR-TASK-IMP-05): `lifeos:` and a SHA-256 of the mapped
  owner's account id, the normalised title (trimmed, lower-case, single
  spaces) and the due date. A row whose key repeats an earlier row of the
  file is skipped as repeated; one whose key already exists, as existing.
- Waiting rows wait on the free text "Life OS" (FR-TASK-IMP-02); a done row
  with no Completed Date is completed at the import time (FR-TASK-IMP-07).

The run record's report keeps row numbers and the owner, status and priority
values being mapped, never another cell's text (FR-TASK-IMP-08).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.tasks import rollout, rules

#: FR-TASK-IMP-01 — the largest file the import reads.
MAX_BYTES: Final = 4 * 1024 * 1024
#: FR-TASK-IMP-02 — the sheet's header → the field it fills.
COLUMNS: Final = {
    "task": "title",
    "status": "status",
    "priority": "priority",
    "owner": "owner",
    "due date": "due_on",
    "tags": "tags",
    "notes / update": "notes",
    "completed date": "completed_on",
}
#: FR-TASK-IMP-04 — the Owner value of a blank cell.
BLANK: Final = "(blank)"
#: FR-TASK-IMP-02 — what an imported Waiting row waits on.
WAITING_ON: Final = "Life OS"
#: FR-TASK-IMP-05, FR-TASK-DATA-12.
KEY_PREFIX: Final = "lifeos:"
#: The fixed status mapping, by the sheet value in lower case. Not editable.
STATUS: Final = {
    "": "open",
    "open": "open",
    "to do": "open",
    "todo": "open",
    "not started": "open",
    "in progress": "open",
    "waiting": "waiting",
    "done": "done",
    "completed": "done",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
#: The fixed priority mapping, by the sheet value in lower case. Not editable.
PRIORITY: Final = {
    "": "normal",
    "urgent": "urgent",
    "high": "high",
    "medium": "normal",
    "normal": "normal",
    "low": "low",
}
#: The statuses a row may take, in the order the counts give them.
STATUSES: Final = ("open", "waiting", "done", "cancelled")
#: TaskCreate's limits (contracts §4).
MAX_TITLE: Final = 200
MAX_NOTES: Final = 20_000

NO_TASK_COLUMN = "The file has no Task column; export the Life OS Tasks tab as CSV."
FIX_IN_SHEET = "fix it in the sheet and export again"

_MONTHS: Final = {
    name: n
    for n, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_D_MON_Y = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})$")


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetRow:
    """One data row: its number in the sheet (the header is row 1) and its
    cells by field, trimmed."""

    n: int
    cells: dict[str, str]


def read(text: str) -> list[SheetRow]:
    """FR-TASK-IMP-02 — the data rows of the CSV, wholly blank rows left
    out. Raises ValueError with a sentence safe to show for a file with no
    Task column."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None) or []
    fields = [COLUMNS.get(" ".join(h.split()).lower()) for h in header]
    if "title" not in fields:
        raise ValueError(NO_TASK_COLUMN)
    out: list[SheetRow] = []
    for n, cells in enumerate(reader, start=2):
        values = {name: "" for name in COLUMNS.values()}
        for name, cell in zip(fields, cells, strict=False):
            if name is not None:
                values[name] = cell.strip()
        if any(cell.strip() for cell in cells):
            out.append(SheetRow(n, values))
    return out


def read_date(text: str) -> tuple[date | None, bool]:
    """FR-TASK-IMP-02 — (the date, whether it is an ambiguous `D/M/YYYY`).
    A blank cell is no date. Raises ValueError for anything else."""
    value = text.strip()
    if not value:
        return None, False
    if m := _ISO.match(value):
        return date(int(m[1]), int(m[2]), int(m[3])), False
    if m := _DMY.match(value):
        day, month = int(m[1]), int(m[2])
        return date(int(m[3]), month, day), day <= 12 and month <= 12 and day != month
    if (m := _D_MON_Y.match(value)) and m[2].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])), False
    raise ValueError(value)


def owner_value(cell: str) -> str:
    """FR-TASK-IMP-04 — the Owner value a cell maps by."""
    return " ".join(cell.split()) or BLANK


def map_status(value: str) -> str | None:
    return STATUS.get(" ".join(value.split()).lower())


def map_priority(value: str) -> str | None:
    return PRIORITY.get(" ".join(value.split()).lower())


def normalise_title(title: str) -> str:
    """FR-TASK-IMP-05 — trimmed, lower-case, single spaces."""
    return " ".join(title.split()).lower()


def content_key(owner_id: str, title: str, due_on: date | None) -> str:
    """FR-TASK-IMP-05 — `lifeos:` and a hash of the owner's account id, the
    normalised title and the due date."""
    text = "\n".join((owner_id, normalise_title(title), due_on.isoformat() if due_on else ""))
    return KEY_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The owner mapping
# ---------------------------------------------------------------------------


def propose(values: list[str], accounts: list[dict[str, Any]]) -> dict[str, str | None]:
    """FR-TASK-IMP-04 — each Owner value's proposed email: the one active
    account whose display name is the value, else the one whose first name
    is, ignoring case. More than one match, or none, proposes nothing."""
    out: dict[str, str | None] = {}
    for value in values:
        wanted = value.lower()
        full = [
            a for a in accounts if " ".join((a.get("display_name") or "").split()).lower() == wanted
        ]
        if not full and value != BLANK:
            full = [
                a
                for a in accounts
                if ((a.get("display_name") or "").split() or [""])[0].lower() == wanted
            ]
        out[value] = full[0]["email"] if len(full) == 1 else None
    return out


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class Planned:
    """One row as it would be imported. `skip` is "existing" or "repeated"
    for a row that creates nothing."""

    n: int
    title: str
    status: str | None
    priority: str | None
    owner: str
    assignee_id: str | None
    due_on: date | None
    tags: list[str]
    notes: str | None
    completed_at: datetime | None
    key: str | None
    skip: str | None = None

    @property
    def waiting_on_text(self) -> str | None:
        return WAITING_ON if self.status == "waiting" else None


@dataclass
class Plan:
    rows: list[Planned]
    #: Data rows in the file.
    total: int
    owners: list[dict[str, Any]]
    owner_mapping: dict[str, str | None]
    value_mapping: dict[str, dict[str, str | None]]
    unmapped: dict[str, list[str]]
    owners_without_tasks: list[str]
    ambiguous_dates: list[dict[str, Any]]
    problems: list[str] = field(default_factory=list)

    def skip_existing(self, existing: set[str]) -> None:
        """FR-TASK-IMP-03, -05 — a row whose key an earlier run created is
        skipped as existing."""
        for row in self.rows:
            if row.skip is None and row.key is not None and row.key in existing:
                row.skip = "existing"

    @property
    def to_create(self) -> list[Planned]:
        return [row for row in self.rows if row.skip is None]

    @property
    def counts(self) -> dict[str, int]:
        """FR-TASK-IMP-03 — rows in the file; what would be created by
        status; skipped (existing and repeated)."""
        made = self.to_create
        existing = sum(row.skip == "existing" for row in self.rows)
        repeated = sum(row.skip == "repeated" for row in self.rows)
        return {
            "rows": self.total,
            **{status: sum(row.status == status for row in made) for status in STATUSES},
            "skipped": existing + repeated,
            "existing": existing,
            "repeated": repeated,
        }

    @property
    def waiting_as_life_os(self) -> int:
        """FR-TASK-IMP-02 — how many rows will wait on "Life OS"."""
        return sum(row.status == "waiting" for row in self.to_create)

    @property
    def blocking(self) -> list[str]:
        """FR-TASK-IMP-04, TP-17 — every reason the real run is refused, one
        sentence each."""
        out = [
            f"The owner “{v}” is not mapped to an active account." for v in self.unmapped["owners"]
        ]
        out += [
            f"The status “{v}” cannot be mapped; {FIX_IN_SHEET}." for v in self.unmapped["status"]
        ]
        out += [
            f"The priority “{v}” cannot be mapped; {FIX_IN_SHEET}."
            for v in self.unmapped["priority"]
        ]
        out += [rollout.assignee_refusal(name, 0) or "" for name in self.owners_without_tasks]
        return out + self.problems

    def report(self) -> dict[str, Any]:
        """The run record's report (data-model §2.3): unmapped values and
        ambiguous dates by row number, and row problems as sentences that
        name no cell's text."""
        return {
            "unmapped": self.unmapped,
            "owners_without_tasks": self.owners_without_tasks,
            "ambiguous_dates": [
                {"row": d["row"], "read_as": d["read_as"]} for d in self.ambiguous_dates
            ],
            "waiting_as_life_os": self.waiting_as_life_os,
            "problems": self.problems,
        }

    def dry_run(self) -> dict[str, Any]:
        """Contracts §1 `POST /tasks/import/dry-run`, less the `run_id`."""
        return {
            "counts": self.counts,
            "owners": self.owners,
            "unmapped": self.unmapped,
            "owners_without_tasks": self.owners_without_tasks,
            "ambiguous_dates": self.ambiguous_dates,
            "waiting_as_life_os": self.waiting_as_life_os,
            "blocking": self.blocking,
        }


def _distinct(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def plan(
    sheet: list[SheetRow],
    *,
    mapping: dict[str, str | None] | None,
    accounts: list[dict[str, Any]],
    default_level: int,
    now: datetime,
    zone: str | None,
) -> Plan:
    """FR-TASK-IMP-02 to -05, -07 — what the file would import, before the
    keys that already exist are known (`Plan.skip_existing`). `mapping` is
    the owner's edits ({Owner value: email or null}); a value it leaves out
    takes the proposal. `accounts` are the active accounts."""
    by_email = {(a.get("email") or "").lower(): a for a in accounts}
    owner_values = _distinct([owner_value(r.cells["owner"]) for r in sheet])
    proposed = propose(owner_values, accounts)
    edits = mapping or {}
    effective = {v: edits[v] if v in edits else proposed[v] for v in owner_values}

    owners: list[dict[str, Any]] = []
    owner_ids: dict[str, str | None] = {}
    without: list[str] = []
    for value in owner_values:
        email = effective[value]
        account = by_email.get((email or "").lower()) if email else None
        owner_ids[value] = account["id"] if account else None
        owners.append(
            {
                "value": value,
                "proposed_email": email,
                "proposed_name": account.get("display_name") if account else None,
                "mapped": account is not None,
            }
        )
        if account is not None:
            name = account.get("display_name") or account["email"]
            if rollout.level_for(account, default_level) < 1 and name not in without:
                without.append(name)

    statuses = _distinct([" ".join(r.cells["status"].split()) for r in sheet])
    priorities = _distinct([" ".join(r.cells["priority"].split()) for r in sheet])
    value_mapping = {
        "status": {v or BLANK: map_status(v) for v in statuses},
        "priority": {v or BLANK: map_priority(v) for v in priorities},
    }

    tz = ZoneInfo(zone or rules.DEFAULT_ZONE)
    problems: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    planned: list[Planned] = []
    seen: set[Any] = set()
    for row in sheet:
        c = row.cells
        dates: dict[str, date | None] = {}
        for name, label in (("due_on", "due date"), ("completed_on", "completed date")):
            try:
                dates[name], unsure = read_date(c[name])
            except ValueError:
                problems.append(f"Row {row.n}: the {label} is not a date the import can read.")
                dates[name], unsure = None, False
            if unsure and dates[name] is not None:
                ambiguous.append(
                    {"row": row.n, "value": c[name], "read_as": dates[name].isoformat()}
                )
        title = " ".join(c["title"].split())
        if not title:
            problems.append(f"Row {row.n}: the task has no title.")
        elif len(title) > MAX_TITLE:
            problems.append(f"Row {row.n}: the task is longer than {MAX_TITLE} characters.")
        notes = c["notes"] or None
        if notes and len(notes) > MAX_NOTES:
            problems.append(f"Row {row.n}: the notes are longer than {MAX_NOTES:,} characters.")
        try:
            tags = rules.normalise_tags(t for t in c["tags"].split(",") if t.strip())
        except ValueError as exc:
            problems.append(f"Row {row.n}: {str(exc)[0].lower()}{str(exc)[1:]}")
            tags = []
        status = map_status(c["status"])
        completed_at = None
        if status == "done":
            on = dates["completed_on"]
            completed_at = datetime.combine(on, time(), tz) if on else now
        value = owner_value(c["owner"])
        assignee_id = owner_ids[value]
        key = content_key(assignee_id, title, dates["due_on"]) if assignee_id else None
        item = Planned(
            n=row.n,
            title=title,
            status=status,
            priority=map_priority(c["priority"]),
            owner=value,
            assignee_id=assignee_id,
            due_on=dates["due_on"],
            tags=tags,
            notes=notes,
            completed_at=completed_at,
            key=key,
        )
        same = key or (value.lower(), normalise_title(title), dates["due_on"])
        if same in seen:
            item.skip = "repeated"
        seen.add(same)
        planned.append(item)

    return Plan(
        rows=planned,
        total=len(sheet),
        owners=owners,
        owner_mapping=effective,
        value_mapping=value_mapping,
        unmapped={
            "owners": [o["value"] for o in owners if not o["mapped"]],
            "status": [v for v, k in value_mapping["status"].items() if k is None],
            "priority": [v for v, k in value_mapping["priority"].items() if k is None],
        },
        owners_without_tasks=without,
        ambiguous_dates=ambiguous,
        problems=problems,
    )
