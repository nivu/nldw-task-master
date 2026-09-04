"""Supabase client and data access.

Uses the service-role key, so **every query here bypasses Row-Level Security**.
That is deliberate — the backend is trusted server-side infrastructure and owns
the business rules RLS cannot express (the lock window, allowance sufficiency,
the state machine). It also means this module cannot be careless: any function
that takes a user id from a request must have had that id checked by
`app.api.deps` first. The policies in 004_rls_policies.sql protect the browser's
connection, not this one.

Functions here are thin and synchronous. FastAPI route handlers are declared
with `def` rather than `async def` so Starlette runs them in a worker thread —
calling this blocking client from an async handler would stall the event loop.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from supabase import Client, create_client

from app.config import settings

supabase: Client = create_client(
    settings.SUPABASE_URL,
    settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value(),
)

# Columns that are safe to return for someone other than the booking's owner
# when the reason must not travel (Q-06, NFR-05).
BOOKING_PUBLIC_COLUMNS = "id,user_id,date,category,duration,status,created_at,decided_by,decided_at"
BOOKING_FULL_COLUMNS = (
    BOOKING_PUBLIC_COLUMNS
    + ",reason,decision_note,created_by,backfilled_by,backfilled_at,backfill_note"
)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def get_profile(user_id: str) -> dict[str, Any] | None:
    response = supabase.table("profiles").select("*").eq("id", user_id).limit(1).execute()
    return response.data[0] if response.data else None


def get_profile_by_email(email: str) -> dict[str, Any] | None:
    response = supabase.table("profiles").select("*").eq("email", email).limit(1).execute()
    return response.data[0] if response.data else None


def list_profiles(*, active_only: bool = False) -> list[dict[str, Any]]:
    query = supabase.table("profiles").select("*").order("display_name")
    if active_only:
        query = query.eq("is_active", True)
    return query.execute().data or []


def list_reports(lead_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    """A lead's direct reports — the population FR-LEAD-01 shows."""
    query = supabase.table("profiles").select("*").eq("lead_id", lead_id)
    if active_only:
        query = query.eq("is_active", True)
    return query.order("display_name").execute().data or []


def insert_profile(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("profiles").insert(data).execute().data[0]


def update_profile(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("profiles").update(data).eq("id", user_id).execute().data[0]


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------


def get_booking(booking_id: str) -> dict[str, Any] | None:
    response = (
        supabase.table("bookings")
        .select(BOOKING_FULL_COLUMNS)
        .eq("id", booking_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def list_bookings(
    *,
    user_ids: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    statuses: list[str] | None = None,
    columns: str = BOOKING_FULL_COLUMNS,
) -> list[dict[str, Any]]:
    query = supabase.table("bookings").select(columns)
    if user_ids is not None:
        if not user_ids:
            return []
        query = query.in_("user_id", user_ids)
    if start is not None:
        query = query.gte("date", start.isoformat())
    if end is not None:
        query = query.lte("date", end.isoformat())
    if statuses is not None:
        query = query.in_("status", statuses)
    return query.order("date").execute().data or []


def find_booking_on(user_id: str, day: date, statuses: list[str]) -> dict[str, Any] | None:
    """The row occupying a given day, if any.

    `statuses` is normally OCCUPYING_STATES rather than CONSUMING_STATES —
    an unrecognised day costs nothing but still holds the date.
    """
    response = (
        supabase.table("bookings")
        .select(BOOKING_FULL_COLUMNS)
        .eq("user_id", user_id)
        .eq("date", day.isoformat())
        .in_("status", statuses)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def insert_booking(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("bookings").insert(data).execute().data[0]


def update_booking(booking_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("bookings").update(data).eq("id", booking_id).execute().data[0]


def list_pending_before(day: date) -> list[dict[str, Any]]:
    """Pending bookings whose own date has passed — the Q-04 sweep's input."""
    return (
        supabase.table("bookings")
        .select(BOOKING_FULL_COLUMNS)
        .eq("status", "pending")
        .lt("date", day.isoformat())
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# Allowances
# ---------------------------------------------------------------------------


def list_allowances(
    *, user_id: str | None = None, include_defaults: bool = True
) -> list[dict[str, Any]]:
    """Grants relevant to one person: their overrides plus the org defaults.

    PostgREST has no OR-with-IS-NULL that reads well here, so this is two
    queries. At this scale that is cheaper than the alternative of fetching
    every row and filtering in Python.
    """
    rows: list[dict[str, Any]] = []
    if include_defaults:
        rows += supabase.table("allowances").select("*").is_("user_id", "null").execute().data or []
    if user_id is not None:
        rows += supabase.table("allowances").select("*").eq("user_id", user_id).execute().data or []
    return rows


def list_all_allowances() -> list[dict[str, Any]]:
    return supabase.table("allowances").select("*").order("period", desc=True).execute().data or []


def upsert_allowance(data: dict[str, Any]) -> dict[str, Any]:
    """Set a grant for a period, replacing any existing one for that scope.

    FR-BAL-07: this writes the row for the period it is set for and touches no
    other, so a change cannot retroactively invalidate a closed period.
    """
    return (
        supabase.table("allowances")
        .upsert(data, on_conflict="period,category,user_id")
        .execute()
        .data[0]
    )


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------


def list_holidays(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    query = supabase.table("holidays").select("*")
    if start is not None:
        query = query.gte("date", start.isoformat())
    if end is not None:
        query = query.lte("date", end.isoformat())
    return query.order("date").execute().data or []


def get_holiday_on(day: date) -> dict[str, Any] | None:
    response = supabase.table("holidays").select("*").eq("date", day.isoformat()).limit(1).execute()
    return response.data[0] if response.data else None


def insert_holiday(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("holidays").insert(data).execute().data[0]


def update_holiday(holiday_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("holidays").update(data).eq("id", holiday_id).execute().data[0]


def delete_holiday(holiday_id: str) -> None:
    supabase.table("holidays").delete().eq("id", holiday_id).execute()


# ---------------------------------------------------------------------------
# Settings and audit
# ---------------------------------------------------------------------------


def list_settings() -> list[dict[str, Any]]:
    return supabase.table("app_settings").select("*").execute().data or []


def update_setting(key: str, value: Any, actor_id: str | None) -> dict[str, Any]:
    return (
        supabase.table("app_settings")
        .update({"value": value, "updated_by": actor_id})
        .eq("key", key)
        .execute()
        .data[0]
    )


def insert_audit(entry: dict[str, Any]) -> None:
    """Append to the audit log.

    Deliberately returns nothing and swallows no errors: a failure to record an
    administrative action should surface, not be silently discarded. The table
    rejects UPDATE and DELETE outright (005_audit_triggers.sql), so this is the
    only way anything gets in.
    """
    supabase.table("audit_log").insert(entry).execute()


def list_audit(limit: int = 200) -> list[dict[str, Any]]:
    return (
        supabase.table("audit_log").select("*").order("at", desc=True).limit(limit).execute().data
        or []
    )
