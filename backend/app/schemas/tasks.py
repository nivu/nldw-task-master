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
