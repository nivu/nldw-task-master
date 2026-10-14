"""CRM request models — spec 007 contracts/api.md.

A module beside `schemas/__init__.py` so that file does not triple in size
(plan §5.2). Mirrored by hand in `frontend/lib/api/crm-types.ts`; change one,
change the other. Every request model forbids extra fields. Money and rates
cross the wire as decimal strings, as everywhere in the portal.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Timezone = Literal["Asia/Kolkata", "Australia/Sydney"]
CrmRole = Literal["admin", "member", "viewer"]
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


class InvitationIn(BaseModel):
    """FR-CRM-ACC-04, -05, -15 — a CRM role only: no TVA field can be set
    through an invitation (FR-CRM-ACC-09). Strings are stripped before they
    are checked, so a blank name is refused rather than stored empty."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: CrmRole = "member"
    timezone: Timezone | None = None


class TvaInvitationIn(InvitationIn):
    """Contracts §2.4 — a TVA admin may bypass the allow-list for an existing
    staff account (FR-CRM-ACC-05)."""

    bypass_allow_list: bool = False


class MembershipUpdate(BaseModel):
    """Contracts §2.2 `PATCH /crm/members/{id}`."""

    model_config = ConfigDict(extra="forbid")

    role: CrmRole


class AllowedDomainsIn(BaseModel):
    """FR-CRM-ACC-06 — TVA admins only."""

    model_config = ConfigDict(extra="forbid")

    domains: list[str] = Field(max_length=20)


class AccountTypeUpdate(BaseModel):
    """Contracts §2.4, through `crm_convert_account` (data-model §5.15)."""

    model_config = ConfigDict(extra="forbid")

    account_type: Literal["staff", "crm_only"]


# ---------------------------------------------------------------------------
# Records — contracts §2.5, §2.5.1 (S3: companies, people, Drive links)
#
# Write models read a plain JSON body through `services/crm/records.parse`,
# which first refuses system-written fields by name (FR-CRM-CO-03) and then
# validates here, so a refusal is one sentence rather than a list of errors.
# A field left out is not changed; `null` clears it.
# ---------------------------------------------------------------------------

LeadStage = Literal[
    "new",
    "contacted",
    "nurturing",
    "qualified",
    "deal_open",
    "customer",
    "partner",
    "disqualified",
]
Entity = Literal["nunnari_labs", "deepweaver"]
RecordType = Literal["company", "person", "opportunity", "task", "note", "campaign"]


class LinkValue(BaseModel):
    """FR-CRM-LIST-07 "links" — a URL and an optional label."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(default=None, max_length=200)


class Links(BaseModel):
    """A primary link and any others (website, LinkedIn)."""

    model_config = ConfigDict(extra="forbid")

    primary: LinkValue | None = None
    others: list[LinkValue] = Field(default_factory=list, max_length=20)


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    street: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)


class Phone(BaseModel):
    """FR-CRM-LIST-07 — country code (default +91), number, WhatsApp marker."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    country_code: str = Field(default="+91", pattern=r"^\+\d{1,4}$")
    number: str = Field(pattern=r"^[0-9][0-9 ()-]{3,19}$")
    whatsapp: bool = False


class CompanyFields(BaseModel):
    """Contracts §2.5.1 company, every field optional (the PATCH shape)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=255)
    additional_domains: list[str] | None = Field(default=None, max_length=20)
    entities: list[Entity] | None = None
    lead_stage: LeadStage | None = None
    account_type_id: str | None = None
    relationship_status_id: str | None = None
    channel_ids: list[str] | None = Field(default=None, max_length=30)
    owner_id: str | None = None
    next_touch_on: date | None = None
    industry_id: str | None = None
    institution_type_id: str | None = None
    size_id: str | None = None
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: Address | None = None
    website: Links | None = None
    linkedin: Links | None = None
    description: str | None = Field(default=None, max_length=20_000)
    source_id: str | None = None


class CompanyUpdate(CompanyFields):
    """`expect_updated_at` is the `updated_at` the change was based on; the web
    app always sends it, an MCP caller that has not read the record may not."""

    expect_updated_at: datetime | None = None


class PersonFields(BaseModel):
    """Contracts §2.5.1 person, every field optional (the PATCH shape)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    company_id: str | None = None
    primary_email: EmailStr | None = None
    additional_emails: list[EmailStr] | None = Field(default=None, max_length=20)
    phones: list[Phone] | None = Field(default=None, max_length=10)
    job_title: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    linkedin: Links | None = None
    owner_id: str | None = None
    do_not_contact: bool | None = None


class PersonUpdate(PersonFields):
    expect_updated_at: datetime | None = None


class DriveLinkIn(BaseModel):
    """FR-CRM-LINK-01, -02 — a link, never content."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    record_type: RecordType
    record_id: str
    url: str = Field(min_length=1, max_length=2000)
    label: str = Field(min_length=1, max_length=200)


class DuplicatesIn(BaseModel):
    """FR-CRM-DUP-02 — what the person has typed so far."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)


class CompanyDeleteIn(BaseModel):
    """FR-CRM-DEL-02, Q-54 — what happens to the company's people. Deals join
    in S4."""

    model_config = ConfigDict(extra="forbid")

    people: Literal["unlink", "delete"] | None = None
