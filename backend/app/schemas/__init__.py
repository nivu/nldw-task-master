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

Category = Literal["wfh", "casual", "sick"]
Role = Literal["user", "lead", "admin"]
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


class HolidayOut(BaseModel):
    id: str
    date: date
    name: str


class UserCreate(BaseModel):
    """FR-AUTH-03 — everything an admin supplies to make an account.

    There is no self-registration equivalent of this model, deliberately
    (FR-AUTH-02).
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
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


class PasswordChange(BaseModel):
    """FR-AUTH-05."""

    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=8, max_length=128)


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
