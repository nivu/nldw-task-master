"""CRM request models — spec 007 contracts/api.md.

A module beside `schemas/__init__.py` so that file does not triple in size
(plan §5.2). Mirrored by hand in `frontend/lib/api/crm-types.ts`; change one,
change the other. Every request model forbids extra fields. Money and rates
cross the wire as decimal strings, as everywhere in the portal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Timezone = Literal["Asia/Kolkata", "Australia/Sydney"]
NumberFormat = Literal["indian_short", "full"]
Color = Literal[
    "gray", "red", "orange", "amber", "green", "teal", "blue", "indigo", "purple", "pink"
]


class PreferencesUpdate(BaseModel):
    """FR-CRM-ACC-15 — CRM display only; TVA stays on Asia/Kolkata."""

    model_config = ConfigDict(extra="forbid")

    timezone: Timezone | None = None
    number_format: NumberFormat | None = None


class SettingUpdate(BaseModel):
    """FR-CRM-LIST-06 — the value is checked per key by domain/crm/settings.py."""

    model_config = ConfigDict(extra="forbid")

    value: Any


class OptionCreate(BaseModel):
    """FR-CRM-LIST-01."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    color: Color | None = None


class OptionUpdate(BaseModel):
    """FR-CRM-LIST-01, -02, -09 — a fixed list takes label and colour only."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=60)
    color: Color | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)
    is_default: bool | None = None
    is_active: bool | None = None


class FxRateIn(BaseModel):
    """FR-CRM-FX-02 — INR per one unit of the currency, for one month."""

    model_config = ConfigDict(extra="forbid")

    rate: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
