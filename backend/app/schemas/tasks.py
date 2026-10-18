"""Task request models — spec 008 contracts/api.md §1.1, §4.

`TaskCreate` is the 008/009 interface's payload (plan §9 rule 2): its fields,
types and defaults are fixed in slice S1a and pinned by
`tests/test_task_create_interface.py`; nobody changes them after S1a. Every
request model forbids extra fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaskLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_type: Literal["company", "person", "opportunity", "campaign", "tva_project"]
    record_id: UUID


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=20_000)
    due_on: date | None = None
    bucket: Literal["today", "this_week", "this_month", "later"] | None = None  # None = derived
    priority: Literal["urgent", "high", "normal", "low"] = "normal"
    assignee_id: UUID | None = None  # None = actor
    workspace_id: UUID | None = None
    links: list[TaskLinkIn] = Field(default_factory=list, max_length=20)
    source: Literal[
        "manual", "nldw", "voice", "email_commitment", "meeting_action", "rule", "import"
    ]
    source_ref: str | None = Field(default=None, max_length=500)
    # --- Optional fields with defaults, fixed in S1a with the rest. They carry
    # --- what POST /tasks (TaskIn), quick add and the Life OS import need;
    # --- a caller that passes only the fields above (009) is unaffected.
    tags: list[str] = Field(default_factory=list, max_length=10)  # each 1–40 after trim (TP-14)
    parent_id: UUID | None = None
    status: Literal["open", "waiting", "done", "cancelled"] = "open"
    waiting_on_id: UUID | None = None
    waiting_on_text: str | None = Field(default=None, max_length=200)
    category_id: UUID | None = None  # CRM tasks only
    completed_at: datetime | None = None  # import only


class TaskIn(BaseModel):
    """Contracts §1.1 — `POST /tasks` and `task_add`. The route resolves
    `assignee` (a name or email) to `assignee_id` and turns the rest into a
    `TaskCreate` field for field, with `source` from the channel."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=20_000)
    due_on: date | None = None
    bucket: Literal["today", "this_week", "this_month", "later"] | None = None
    priority: Literal["urgent", "high", "normal", "low"] = "normal"
    tags: list[str] = Field(default_factory=list, max_length=10)
    assignee_id: UUID | None = None
    assignee: str | None = Field(default=None, min_length=1, max_length=200)
    links: list[TaskLinkIn] = Field(default_factory=list, max_length=20)
    parent_id: UUID | None = None
    status: Literal["open", "waiting"] = "open"
    waiting_on_id: UUID | None = None
    waiting_on_text: str | None = Field(default=None, max_length=200)
    category_id: UUID | None = None


class TaskPatch(BaseModel):
    """Contracts §1.1 — `PATCH /tasks/{id}` and `task_update`. Only the fields
    sent change; links change only through the link routes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=20_000)
    status: Literal["open", "waiting", "done", "cancelled"] | None = None
    due_on: date | None = None
    bucket: Literal["today", "this_week", "this_month", "later"] | None = None
    priority: Literal["urgent", "high", "normal", "low"] | None = None
    tags: list[str] | None = Field(default=None, max_length=10)
    assignee_id: UUID | None = None
    parent_id: UUID | None = None
    waiting_on_id: UUID | None = None
    waiting_on_text: str | None = Field(default=None, max_length=200)
    category_id: UUID | None = None
    expect_updated_at: datetime | None = None


class TaskAudienceIn(BaseModel):
    """Contracts §1 `POST /tasks/audience` — a draft's assignee and links."""

    model_config = ConfigDict(extra="forbid")
    assignee_id: UUID | None = None
    links: list[TaskLinkIn] = Field(default_factory=list, max_length=20)


class QuickAddAnswers(BaseModel):
    """Contracts §1 `POST /tasks/quick-add/preview` — the answers to the
    preview's questions: a chosen date and a chosen person."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    due_on: date | None = Field(default=None, alias="date")
    assignee_id: UUID | None = None


class QuickAddIn(BaseModel):
    """Contracts §1 `POST /tasks/quick-add/preview` — one line, and any
    answers so far (FR-TASK-ADD-02 to -04)."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    answers: QuickAddAnswers | None = None
