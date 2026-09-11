"""Request and response models.

Constitution, Clear Boundaries: the contract between layers is defined in code
before implementation — Pydantic here, TypeScript in `frontend/lib/api/types.ts`.
The two are kept in step by hand; if you change one, change the other.

Day counts cross the wire as **strings**, not numbers. §6.2 requires half-day
granularity, and JSON's only numeric type is a float. `"0.5"` survives a round
trip exactly; `0.5` accumulates error once enough of them are added up in a
browser, and a leave balance that is wrong by a fraction of a day is worse than
one that is obviously broken.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Category = Literal["wfh", "casual", "sick", "compoff"]
Role = Literal["user", "lead", "manager", "admin"]
Status = Literal["pending", "approved", "rejected", "withdrawn", "released", "unrecognised"]


class BookingCreate(BaseModel):
    """FR-BOOK-01/02/03 — what the booking form submits."""

    model_config = ConfigDict(extra="forbid")

    date: date
    category: Category
    duration: Decimal
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("duration")
    @classmethod
    def _half_or_full(cls, value: Decimal) -> Decimal:
        if value not in (Decimal("0.5"), Decimal("1.0")):
            raise ValueError("duration must be 0.5 (half day) or 1.0 (full day)")
        return value

    @field_validator("reason")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class BookingDecision(BaseModel):
    """FR-APPR-02/03. A rejection's note is required by the service layer."""

    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: str | None = Field(default=None, max_length=500)


class UnrecognisedFlag(BaseModel):
    """FR-LEAD-03 — a lead recording an absence nobody booked."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    date: date
    note: str | None = Field(default=None, max_length=500)


class BookingOut(BaseModel):
    id: str
    user_id: str
    date: date
    category: Category | None
    duration: Decimal
    status: Status
    reason: str | None = None
    decision_note: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    locked: bool = False
    can_edit: bool = False


class BalanceOut(BaseModel):
    """FR-BAL-06 — allowance, used and remaining, per category."""

    category: Category
    period: str
    opening: str
    allowance: str
    used: str
    remaining: str


class HolidayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    name: str = Field(min_length=1, max_length=120)
    # Spec 006 FR-LOC-02 — None applies everywhere.
    location_id: str | None = None


class HolidayOut(BaseModel):
    id: str
    date: date
    name: str


class UserCreate(BaseModel):
    """FR-AUTH-03 — everything an admin supplies to make an account.

    No password: sign-in is Google only (FR-AUTH-08). An admin creates the
    account, and the person's first Google sign-in attaches an identity to it.
    Nothing is issued that could be written down or passed to a colleague.

    There is no self-registration equivalent of this model, deliberately
    (FR-AUTH-02).
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: Role = "user"
    lead_id: str | None = None


class UserUpdate(BaseModel):
    """FR-ADMIN-02/03, FR-AUTH-06."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    lead_id: str | None = None
    is_active: bool | None = None
    # Spec 006 FR-LOC-01.
    location_id: str | None = None


class AllowanceIn(BaseModel):
    """FR-ADMIN-01. `user_id` omitted means the organisation default."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    category: Category
    days: Decimal = Field(ge=0, le=365)
    user_id: str | None = None


class BackfillIn(BaseModel):
    """Spec A-21 — an admin recording leave somebody already took.

    `note` is required and has no default. It is the justification for
    overriding the lock in §6.3, and it goes into the append-only audit log.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    date: date
    category: Category
    duration: Decimal
    reason: str | None = Field(default=None, max_length=500)
    note: str = Field(min_length=1, max_length=500)

    @field_validator("duration")
    @classmethod
    def _half_or_full(cls, value: Decimal) -> Decimal:
        if value not in (Decimal("0.5"), Decimal("1.0")):
            raise ValueError("duration must be 0.5 (half day) or 1.0 (full day)")
        return value


class ProfileOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: Role
    lead_id: str | None
    is_active: bool


class SettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: object


# ---------------------------------------------------------------------------
# Timesheets — spec 002
# ---------------------------------------------------------------------------

Phase = Literal["pre", "delivery", "support"]


Activity = Literal["learning", "internal", "admin", "other"]


class TimesheetLine(BaseModel):
    """One project's — or one activity's — worth of a day — FR-TIME-01/02.

    Exactly one of `project_id` / `activity` (spec 003 FR-ACT-01). The service
    layer refuses the other combinations with a sentence; this only shapes.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    activity: Activity | None = None
    hours_office: Decimal = Field(default=Decimal("0"), ge=0, le=24)
    hours_home: Decimal = Field(default=Decimal("0"), ge=0, le=24)
    note: str | None = Field(default=None, max_length=500)


class TimesheetDay(BaseModel):
    """A whole day, submitted at once.

    Whole-day rather than per-line because FR-TIME-05 caps the *day*, and a
    per-line endpoint cannot enforce that without the caller sending the day
    anyway. It also makes removing a line the same operation as changing one.
    """

    model_config = ConfigDict(extra="forbid")

    date: date
    lines: list[TimesheetLine] = Field(default_factory=list, max_length=20)


class ProjectIn(BaseModel):
    """FR-PROJ-01; revenue per spec 003 FR-FIN-02 (money — manager/admin only)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    client: str | None = Field(default=None, max_length=160)
    revenue: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    client: str | None = Field(default=None, max_length=160)
    is_archived: bool | None = None
    revenue: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)


class PhaseIn(BaseModel):
    """FR-PROJ-02. `budget_hours` is HOURS — Q-05 keeps money out of this."""

    model_config = ConfigDict(extra="forbid")

    phase: Phase
    starts_on: date
    ends_on: date
    budget_hours: Decimal | None = Field(default=None, ge=0, le=1_000_000)


class AllocationIn(BaseModel):
    """FR-ALLOC-01/03. `percent` is a share of capacity, not of the calendar."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    user_id: str
    starts_on: date
    ends_on: date
    percent: Decimal = Field(gt=0, le=100)


class TokenCreate(BaseModel):
    """Spec 004 FR-TOK-01 — a name is required so a list of tokens is legible."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)


class CtcPeriodIn(BaseModel):
    """Spec 005 FR-CTC — one dated CTC figure. Annual (Q-03); the API shows
    it monthly. `ends_on` None means until further notice."""

    model_config = ConfigDict(extra="forbid")

    annual_ctc: Decimal = Field(ge=0, le=1_000_000_000)
    starts_on: date
    ends_on: date | None = None


# ---------------------------------------------------------------------------
# Spec 006 — org operations
# ---------------------------------------------------------------------------


class CompoffClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worked_on: date
    days: Decimal = Field(default=Decimal("1"))
    note: str | None = Field(default=None, max_length=300)


class CompoffDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: str | None = Field(default=None, max_length=300)


class ConfirmWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=300)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quarter: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    summary: str = Field(max_length=8000)
    submit: bool = False


class OAuthApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    txn: str = Field(min_length=8, max_length=200)
    approved: bool


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)


class MilestoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    due_on: date
    amount: Decimal = Field(ge=0, le=1_000_000_000)


class MilestoneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    due_on: date | None = None
    amount: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)
    invoiced_on: date | None = None
    clear_invoiced: bool = False


class ChecklistStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    kind: Literal["onboarding", "offboarding"]


class ChecklistItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    done: bool | None = None
    owner_id: str | None = None
    due_on: date | None = None


class ChecklistTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(max_length=40)


class NudgeRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["today", "weekly_gaps", "over_allocation", "morning_post", "digest"]
