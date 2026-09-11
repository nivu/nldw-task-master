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


# ---------------------------------------------------------------------------
# Projects, phases, allocations and time entries — spec 002
# ---------------------------------------------------------------------------


def list_projects(*, include_archived: bool = False) -> list[dict[str, Any]]:
    query = supabase.table("projects").select("*").order("name")
    if not include_archived:
        query = query.eq("is_archived", False)
    return query.execute().data or []


def get_project(project_id: str) -> dict[str, Any] | None:
    response = supabase.table("projects").select("*").eq("id", project_id).limit(1).execute()
    return response.data[0] if response.data else None


def insert_project(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("projects").insert(data).execute().data[0]


def update_project(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("projects").update(data).eq("id", project_id).execute().data[0]


def list_phases(project_id: str | None = None) -> list[dict[str, Any]]:
    query = supabase.table("project_phases").select("*")
    if project_id is not None:
        query = query.eq("project_id", project_id)
    return query.order("starts_on").execute().data or []


def upsert_phase(data: dict[str, Any]) -> dict[str, Any]:
    return (
        supabase.table("project_phases")
        .upsert(data, on_conflict="project_id,phase")
        .execute()
        .data[0]
    )


def delete_phase(phase_id: str) -> None:
    supabase.table("project_phases").delete().eq("id", phase_id).execute()


def list_allocations(
    *, user_ids: list[str] | None = None, project_id: str | None = None
) -> list[dict[str, Any]]:
    query = supabase.table("allocations").select("*")
    if user_ids is not None:
        if not user_ids:
            return []
        query = query.in_("user_id", user_ids)
    if project_id is not None:
        query = query.eq("project_id", project_id)
    return query.order("starts_on").execute().data or []


def insert_allocation(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("allocations").insert(data).execute().data[0]


def delete_allocation(allocation_id: str) -> None:
    supabase.table("allocations").delete().eq("id", allocation_id).execute()


def list_time_entries(
    *,
    user_ids: list[str] | None = None,
    project_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    query = supabase.table("time_entries").select("*")
    if user_ids is not None:
        if not user_ids:
            return []
        query = query.in_("user_id", user_ids)
    if project_id is not None:
        query = query.eq("project_id", project_id)
    if start is not None:
        query = query.gte("date", start.isoformat())
    if end is not None:
        query = query.lte("date", end.isoformat())
    return query.order("date").execute().data or []


def insert_time_entry(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("time_entries").insert(data).execute().data[0]


def update_time_entry(entry_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Correct an existing line in place.

    Update-by-id rather than upsert: since 009 there are two partial unique
    indexes (one for project lines, one for activity lines), and PostgREST's
    `on_conflict` cannot name a partial index's predicate. The service layer
    already knows which row it is correcting, so it says so.
    """
    return supabase.table("time_entries").update(data).eq("id", entry_id).execute().data[0]


def delete_time_entry(entry_id: str) -> None:
    supabase.table("time_entries").delete().eq("id", entry_id).execute()


def get_time_entry(entry_id: str) -> dict[str, Any] | None:
    response = supabase.table("time_entries").select("*").eq("id", entry_id).limit(1).execute()
    return response.data[0] if response.data else None


# ---------------------------------------------------------------------------
# Personal access tokens — spec 004. Backend-only table; the browser role has
# no grant on it at all (010_api_tokens.sql).
# ---------------------------------------------------------------------------

TOKEN_COLUMNS = (
    "id, user_id, name, prefix, created_at, expires_at, last_used_at, revoked_at, client_id"
)


def get_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    response = (
        supabase.table("api_tokens")
        .select("id, user_id, expires_at, revoked_at")
        .eq("token_hash", token_hash)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def list_tokens(user_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("api_tokens")
        .select(TOKEN_COLUMNS)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )


def insert_token(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("api_tokens").insert(data).execute().data[0]


def revoke_token(token_id: str, user_id: str, at: str) -> dict[str, Any] | None:
    """Revoke, scoped to the owner: a token id is not proof of ownership."""
    response = (
        supabase.table("api_tokens")
        .update({"revoked_at": at})
        .eq("id", token_id)
        .eq("user_id", user_id)
        .is_("revoked_at", "null")
        .execute()
    )
    return response.data[0] if response.data else None


def touch_token(token_id: str, at: str) -> None:
    supabase.table("api_tokens").update({"last_used_at": at}).eq("id", token_id).execute()


# ---------------------------------------------------------------------------
# CTC periods — spec 005. Backend-only table (011_cost_periods.sql).
# ---------------------------------------------------------------------------


def list_cost_periods(user_id: str | None = None) -> list[dict[str, Any]]:
    query = supabase.table("cost_periods").select("*")
    if user_id is not None:
        query = query.eq("user_id", user_id)
    return query.order("starts_on").execute().data or []


def insert_cost_period(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("cost_periods").insert(data).execute().data[0]


def close_cost_period(period_id: str, ends_on: str) -> None:
    supabase.table("cost_periods").update({"ends_on": ends_on}).eq("id", period_id).execute()


def get_cost_period(period_id: str) -> dict[str, Any] | None:
    response = supabase.table("cost_periods").select("*").eq("id", period_id).limit(1).execute()
    return response.data[0] if response.data else None


def delete_cost_period(period_id: str) -> None:
    supabase.table("cost_periods").delete().eq("id", period_id).execute()


# ---------------------------------------------------------------------------
# Spec 006 — locations, comp-off, sign-off, milestones, checklists, reviews,
# OAuth. All backend-only tables (012_ops.sql).
# ---------------------------------------------------------------------------


def list_locations() -> list[dict[str, Any]]:
    return supabase.table("locations").select("*").order("name").execute().data or []


def insert_location(name: str) -> dict[str, Any]:
    return supabase.table("locations").insert({"name": name}).execute().data[0]


def default_location_id() -> str | None:
    rows = supabase.table("locations").select("id").eq("is_default", True).limit(1).execute().data
    return rows[0]["id"] if rows else None


def list_holidays_for_location(
    start: date | None, end: date | None, location_id: str | None
) -> list[dict[str, Any]]:
    """Holidays that apply everywhere plus those for one location (FR-LOC-02)."""
    rows = list_holidays(start, end)
    return [r for r in rows if r.get("location_id") in (None, location_id)]


def list_compoff_credits(
    *, user_ids: list[str] | None = None, statuses: list[str] | None = None
) -> list[dict[str, Any]]:
    query = supabase.table("compoff_credits").select("*")
    if user_ids is not None:
        if not user_ids:
            return []
        query = query.in_("user_id", user_ids)
    if statuses:
        query = query.in_("status", statuses)
    return query.order("worked_on").execute().data or []


def get_compoff_credit(credit_id: str) -> dict[str, Any] | None:
    rows = supabase.table("compoff_credits").select("*").eq("id", credit_id).limit(1).execute().data
    return rows[0] if rows else None


def insert_compoff_credit(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("compoff_credits").insert(data).execute().data[0]


def update_compoff_credit(credit_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("compoff_credits").update(data).eq("id", credit_id).execute().data[0]


def list_confirmations(
    *, user_ids: list[str] | None = None, week_start: date | None = None
) -> list[dict[str, Any]]:
    query = supabase.table("timesheet_confirmations").select("*")
    if user_ids is not None:
        if not user_ids:
            return []
        query = query.in_("user_id", user_ids)
    if week_start is not None:
        query = query.eq("week_start", week_start.isoformat())
    return query.execute().data or []


def upsert_confirmation(data: dict[str, Any]) -> dict[str, Any]:
    return (
        supabase.table("timesheet_confirmations")
        .upsert(data, on_conflict="user_id,week_start")
        .execute()
        .data[0]
    )


def delete_confirmation(user_id: str, week_start: date) -> None:
    supabase.table("timesheet_confirmations").delete().eq("user_id", user_id).eq(
        "week_start", week_start.isoformat()
    ).execute()


def list_milestones(project_id: str | None = None) -> list[dict[str, Any]]:
    query = supabase.table("project_milestones").select("*")
    if project_id is not None:
        query = query.eq("project_id", project_id)
    return query.order("due_on").execute().data or []


def insert_milestone(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("project_milestones").insert(data).execute().data[0]


def update_milestone(milestone_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    rows = supabase.table("project_milestones").update(data).eq("id", milestone_id).execute().data
    return rows[0] if rows else None


def delete_milestone(milestone_id: str) -> None:
    supabase.table("project_milestones").delete().eq("id", milestone_id).execute()


def list_checklist_templates(kind: str | None = None) -> list[dict[str, Any]]:
    query = supabase.table("checklist_templates").select("*")
    if kind:
        query = query.eq("kind", kind)
    return query.order("kind").order("position").execute().data or []


def replace_checklist_templates(kind: str, labels: list[str]) -> None:
    supabase.table("checklist_templates").delete().eq("kind", kind).execute()
    if labels:
        supabase.table("checklist_templates").insert(
            [{"kind": kind, "position": i + 1, "label": label} for i, label in enumerate(labels)]
        ).execute()


def list_checklists() -> list[dict[str, Any]]:
    return (
        supabase.table("checklists").select("*").order("created_at", desc=True).execute().data or []
    )


def insert_checklist(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("checklists").insert(data).execute().data[0]


def update_checklist(checklist_id: str, data: dict[str, Any]) -> None:
    supabase.table("checklists").update(data).eq("id", checklist_id).execute()


def list_checklist_items(checklist_ids: list[str]) -> list[dict[str, Any]]:
    if not checklist_ids:
        return []
    return (
        supabase.table("checklist_items")
        .select("*")
        .in_("checklist_id", checklist_ids)
        .order("position")
        .execute()
        .data
        or []
    )


def insert_checklist_items(rows: list[dict[str, Any]]) -> None:
    if rows:
        supabase.table("checklist_items").insert(rows).execute()


def update_checklist_item(item_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    rows = supabase.table("checklist_items").update(data).eq("id", item_id).execute().data
    return rows[0] if rows else None


def get_review(user_id: str, quarter: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("reviews")
        .select("*")
        .eq("user_id", user_id)
        .eq("quarter", quarter)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_reviews(user_ids: list[str], quarter: str) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    return (
        supabase.table("reviews")
        .select("*")
        .in_("user_id", user_ids)
        .eq("quarter", quarter)
        .execute()
        .data
        or []
    )


def upsert_review(data: dict[str, Any]) -> dict[str, Any]:
    return supabase.table("reviews").upsert(data, on_conflict="user_id,quarter").execute().data[0]


def get_oauth_client(client_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("oauth_clients")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def insert_oauth_client(data: dict[str, Any]) -> None:
    supabase.table("oauth_clients").insert(data).execute()


def insert_oauth_transaction(data: dict[str, Any]) -> None:
    supabase.table("oauth_transactions").insert(data).execute()


def pop_oauth_transaction(txn: str) -> dict[str, Any] | None:
    rows = supabase.table("oauth_transactions").select("*").eq("txn", txn).limit(1).execute().data
    if rows:
        supabase.table("oauth_transactions").delete().eq("txn", txn).execute()
    return rows[0] if rows else None


def insert_oauth_code(data: dict[str, Any]) -> None:
    supabase.table("oauth_codes").insert(data).execute()


def pop_oauth_code(code: str) -> dict[str, Any] | None:
    rows = supabase.table("oauth_codes").select("*").eq("code", code).limit(1).execute().data
    if rows:
        supabase.table("oauth_codes").delete().eq("code", code).execute()
    return rows[0] if rows else None


def insert_oauth_refresh(data: dict[str, Any]) -> None:
    supabase.table("oauth_refresh_tokens").insert(data).execute()


def get_oauth_refresh(token_hash: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("oauth_refresh_tokens")
        .select("*")
        .eq("token_hash", token_hash)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def revoke_oauth_refresh(token_hash: str, at: str) -> None:
    supabase.table("oauth_refresh_tokens").update({"revoked_at": at}).eq(
        "token_hash", token_hash
    ).execute()


def get_token(token_id: str) -> dict[str, Any] | None:
    rows = supabase.table("api_tokens").select("*").eq("id", token_id).limit(1).execute().data
    return rows[0] if rows else None
