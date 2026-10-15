"""CRM data access — spec 007 plan §5.2, §5.3, §5.4; data-model §5.

Thin named functions over the same service-role client as
`services/supabase.py`, so every query here bypasses RLS and must be scoped
by the caller's workspace. Every read of workspace data takes `workspace_id`
as its first argument and filters on it (FR-CRM-WS-02); the few functions
that read across workspaces are account-level and are listed in
`ACCOUNT_LEVEL` (a test checks both rules by introspection).

Writes never go through `.table(...).insert/update`: they go through
`apply()` (or another CRM database function), which sets the actor in the
same transaction so the audit trigger records the change (FR-CRM-AUD-06).
The one exception is `touch_last_used`, a column the trigger ignores, and
the job log, which is a log with no audit trigger (data-model §3.18) and
no record content: notifications and digest markers, also logs, go through
their own database functions so membership and "exactly once" are held by
the database.

Postgres messages never reach a client unmapped: `rpc()` turns the CRM's
SQLSTATEs into `CrmRefused` with their one-sentence message, a unique
violation into a sentence written here, and lets anything else surface as the
generic 500 after logging it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from postgrest.exceptions import APIError

from app.services.crm import CrmRefused
from app.services.supabase import supabase

logger = logging.getLogger("nldw-task-master")

#: Functions that do not take a workspace first: they read the caller's own
#: memberships (to find their workspaces), or write through a database
#: function that scopes by the actor's workspace itself.
ACCOUNT_LEVEL = frozenset(
    {
        "memberships_for_user",
        "touch_last_used",
        "apply",
        "rpc",
        # The membership functions scope by the actor's workspace themselves.
        "invite",
        "remove_membership",
        "convert_account",
        # The TVA admin panel (CT-3): account administration across
        # workspaces, never a CRM record table.
        "list_workspaces",
        "find_membership",
        "list_account_audit",
        # Purges only from the Trash of the actor's workspace (029).
        "purge",
        # Locks the deal and its company in the actor's workspace (031, 033).
        "deal_transition",
        "link_project",
        # The view registry is not workspace data (data-model §3.15).
        "list_object_fields",
        # Locks both companies in the actor's workspace (035).
        "merge_companies",
        # Writes the import-run log of the actor's workspace (036).
        "record_import",
        # Pure: splits values for URL filters.
        "text_batches",
        # The job log (037): runs of every workspace, and the digest tick's,
        # which has none. Readers scope by workspace where the caller is one.
        "start_job_run",
        "finish_job_run",
        "job_streak",
        "mark_job_alerted",
        "list_all_job_runs",
        "latest_job_runs",
        # The bell (037): a person's own notifications, in the workspaces
        # they are a member of now; crm_notify adds only to members.
        "notify",
        "list_notifications",
        "unread_count",
        "mark_read",
        # Digests span a person's workspaces (FR-CRM-NOTIF-03).
        "claim_digest",
        "set_digest_channels",
        "list_all_memberships",
    }
)

#: SQLSTATE raised by the CRM's database functions → HTTP status.
STATUS_BY_CODE = {"CRM04": 404, "CRM09": 409, "CRM22": 422}

#: Unique constraints a person can trip, by index or constraint name.
UNIQUE_MESSAGES = {
    "crm_memberships_workspace_user": "This person is already a member of this workspace.",
    "crm_select_options_label": "This list already has an option with that name.",
    "crm_select_options_one_default": "This list's default changed meanwhile; reload and try again.",
    "crm_fx_rates_workspace_currency_month": (
        "A rate for that month was set meanwhile; reload and try again."
    ),
    # FR-CRM-DUP-01 — the routes name the record first; these catch a race.
    "crm_company_domains_workspace_domain": (
        "That domain was just given to another company in this workspace."
    ),
    "crm_company_domains_one_primary": "This company's domains changed meanwhile; reload.",
    "crm_people_workspace_email": "That email was just given to another person in this workspace.",
    "crm_opportunity_contacts_pair": "That person is already a contact on this deal.",
    "crm_task_links_target": "The task is already linked to that record.",
    "crm_note_links_target": "The note is already linked to that record.",
    "crm_note_mentions_pair": "That member is already mentioned.",
    # FR-CRM-TVA-01 — one project per deal (032); 033 checks it first.
    "projects_source_opportunity_unique": "This deal already has a TVA project.",
    # FR-CRM-VIEW-05 (034).
    "crm_saved_views_name": "There is already a view with that name here.",
    "crm_saved_views_one_default": "This list's default view changed meanwhile; reload.",
    "crm_favourites_target": "That is already a favourite.",
}

#: A reference to a row that is not in the workspace: the composite foreign
#: keys of data-model §2 answer it as a foreign-key violation.
NOT_IN_WORKSPACE = "No such record in this workspace."

_CONSTRAINT = re.compile(r'unique constraint "([^"]+)"')


def refusal_for(code: str | None, message: str | None) -> CrmRefused | None:
    """The refusal a database error stands for, or None if it is a fault."""
    if code in STATUS_BY_CODE:
        return CrmRefused(STATUS_BY_CODE[code], message or "That change was refused.")
    if code == "23505":
        found = _CONSTRAINT.search(message or "")
        name = found.group(1) if found else ""
        return CrmRefused(409, UNIQUE_MESSAGES.get(name, "That already exists."))
    if code == "23503":
        return CrmRefused(404, NOT_IN_WORKSPACE)
    return None


def rpc(fn: str, params: dict[str, Any]) -> Any:
    """Call a CRM database function, mapping its refusals (plan §5.4)."""
    try:
        return supabase.rpc(fn, params).execute().data
    except APIError as exc:
        refused = refusal_for(exc.code, exc.message)
        if refused is not None:
            raise refused from None
        logger.error('{"event": "crm_rpc_failed", "fn": "%s", "code": "%s"}', fn, exc.code)
        raise


def apply(actor: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Data-model §5.2 — every ordinary CRM write, in one transaction.

    Returns one `{ref, id, row}` per op, in order.
    """
    return rpc("crm_apply", {"p_actor": actor, "p_ops": ops}) or []


# ---------------------------------------------------------------------------
# Workspaces and memberships — FR-CRM-WS
# ---------------------------------------------------------------------------

_WORKSPACE_COLUMNS = "id, key, name, tva_linked, has_entity, allowed_domains"


def memberships_for_user(user_id: str) -> list[dict[str, Any]]:
    """The person's memberships, each with its workspace under `workspace`."""
    rows = (
        supabase.table("crm_memberships")
        .select(f"id, role, last_used_at, workspace:crm_workspaces({_WORKSPACE_COLUMNS})")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows or []


def touch_last_used(membership_id: str, at: datetime) -> None:
    """FR-CRM-WS-04. The audit trigger skips a change to this column alone,
    so it is written directly, with no actor."""
    supabase.table("crm_memberships").update({"last_used_at": at.isoformat()}).eq(
        "id", membership_id
    ).execute()


# ---------------------------------------------------------------------------
# Members, invitations, account type — FR-CRM-ACC
# ---------------------------------------------------------------------------

_MEMBERSHIP_COLUMNS = "id, workspace_id, user_id, role, created_at"


def list_members(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_memberships")
        .select(_MEMBERSHIP_COLUMNS)
        .eq("workspace_id", workspace_id)
        .execute()
        .data
        or []
    )


def get_membership(workspace_id: str, membership_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("crm_memberships")
        .select(_MEMBERSHIP_COLUMNS)
        .eq("workspace_id", workspace_id)
        .eq("id", membership_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def find_membership(membership_id: str) -> dict[str, Any] | None:
    """For the TVA admin panel, which names a membership without a workspace."""
    rows = (
        supabase.table("crm_memberships")
        .select(_MEMBERSHIP_COLUMNS)
        .eq("id", membership_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def invite(actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Data-model §5.13 — profile (created or reactivated), membership and
    their audit rows in one transaction, after the GoTrue step (CT-5)."""
    return rpc("crm_invite", {"p_actor": actor, "p_plan": plan})


def remove_membership(actor: dict[str, Any], membership_id: str) -> dict[str, Any]:
    """Data-model §5.3 — `{deactivated}`; tokens are revoked with it (Q-44)."""
    return rpc("crm_remove_membership", {"p_actor": actor, "p_membership_id": membership_id})


def convert_account(actor: dict[str, Any], user_id: str, to: str) -> dict[str, Any]:
    """Data-model §5.15 — under the profile lock tva_staff_only() waits for."""
    return rpc("crm_convert_account", {"p_actor": actor, "p_user_id": user_id, "p_to": to})


def list_workspaces() -> list[dict[str, Any]]:
    return (
        supabase.table("crm_workspaces").select(_WORKSPACE_COLUMNS).order("name").execute().data
        or []
    )


#: Contracts §2.4 — account administration, never record content.
ACCOUNT_ACTIONS = (
    "user.created",
    "user.account_type",
    "user.deactivated",
    "user.reactivated",
    "workspace.allowed_domains",
)


def list_account_audit(
    *, workspace_id: str | None = None, before: int | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Membership events of every workspace and the account events around
    them, newest first (plan CT-3).

    The `user.*` rows are TVA rows, written with no workspace. Filtered to a
    workspace, they are included for the people who are or were its members,
    so removing someone's last membership shows the deactivation it caused.
    A person in two workspaces has their account events in both.
    """
    events = f"action.like.membership.*,action.in.({','.join(ACCOUNT_ACTIONS)})"
    if not workspace_id:
        return _audit_page(lambda q: q.or_(events), before, limit)
    rows = _audit_page(lambda q: q.eq("workspace_id", workspace_id).or_(events), before, limit)
    # The people's account rows go in batches: one id list in the URL grows
    # with every member the workspace has ever had, and PostgREST refuses a
    # request line past a few kilobytes ("URI too long").
    user_actions = [a for a in ACCOUNT_ACTIONS if a.startswith("user.")]
    people = sorted(_workspace_people(workspace_id))
    for i in range(0, len(people), _ID_BATCH):
        batch = people[i : i + _ID_BATCH]
        rows += _audit_page(
            lambda q, batch=batch: (
                q.is_("workspace_id", "null").in_("action", user_actions).in_("target_id", batch)
            ),
            before,
            limit,
        )
    rows.sort(key=lambda r: r["id"], reverse=True)
    return rows[:limit]


#: Ids per PostgREST `in` filter; 36-character uuids keep a batch near 4 KB.
_ID_BATCH = 100


def _audit_page(narrow, before: int | None, limit: int) -> list[dict[str, Any]]:
    query = narrow(
        supabase.table("audit_log").select(
            "id, at, actor_id, actor_label, workspace_id, action, target_table, target_id, "
            "before, after"
        )
    )
    if before is not None:
        query = query.lt("id", before)
    return query.order("id", desc=True).limit(limit).execute().data or []


def _workspace_people(workspace_id: str) -> set[str]:
    """Everyone who is, or was, a member: the members now, and the people
    the workspace's membership events name (a removal keeps the user in
    `before`)."""
    rows = (
        supabase.table("audit_log")
        .select("after_user:after->>user_id, before_user:before->>user_id")
        .eq("workspace_id", workspace_id)
        .eq("target_table", "crm_memberships")
        .execute()
        .data
        or []
    )
    people = {r[k] for r in rows for k in ("after_user", "before_user") if r.get(k)}
    return people | {m["user_id"] for m in list_members(workspace_id)}


# ---------------------------------------------------------------------------
# Settings, select lists, FX rates — FR-CRM-LIST, FR-CRM-FX
# ---------------------------------------------------------------------------


def list_settings(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_workspace_settings")
        .select("id, key, value, updated_at")
        .eq("workspace_id", workspace_id)
        .execute()
        .data
        or []
    )


def list_options(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_select_options")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("list_key")
        .order("position")
        .execute()
        .data
        or []
    )


def get_option(workspace_id: str, option_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("crm_select_options")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("id", option_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_fx_rates(workspace_id: str) -> list[dict[str, Any]]:
    return (
        supabase.table("crm_fx_rates")
        .select("id, currency, month, rate, updated_at")
        .eq("workspace_id", workspace_id)
        .order("month", desc=True)
        .order("currency")
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# Audit — FR-CRM-AUD-03
# ---------------------------------------------------------------------------


def list_audit(
    workspace_id: str,
    *,
    target_table: str | None = None,
    target_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The workspace's own audit rows, newest first. Never another
    workspace's, and never a TVA row (workspace_id NULL)."""
    query = supabase.table("audit_log").select("*").eq("workspace_id", workspace_id)
    if target_table:
        query = query.eq("target_table", target_table)
    if target_id:
        query = query.eq("target_id", target_id)
    if actor_id:
        query = query.eq("actor_id", actor_id)
    if action:
        query = query.eq("action", action)
    if since:
        query = query.gte("at", since.isoformat())
    if until:
        query = query.lt("at", until.isoformat())
    if before is not None:
        query = query.lt("id", before)
    return query.order("id", desc=True).limit(limit).execute().data or []


# ---------------------------------------------------------------------------
# Records — companies, people, Drive links (S3; data-model §3.6–§3.8, §3.14),
# deals, tasks and notes (S4; §3.9, §3.12, §3.13)
# ---------------------------------------------------------------------------

#: The record tables that exist so far; later slices add theirs.
RECORD_TABLES = frozenset(
    {
        "crm_companies",
        "crm_people",
        "crm_drive_links",
        "crm_opportunities",
        "crm_tasks",
        "crm_notes",
    }
)
#: The plain list's `q` (contracts §2.5): a literal substring of these.
_SEARCH = {
    "crm_companies": ("name",),
    "crm_people": ("first_name", "last_name", "primary_email"),
    "crm_drive_links": ("label",),
    "crm_opportunities": ("name",),
    "crm_tasks": ("title",),
    "crm_notes": ("body",),
}
#: The polymorphic link tables of tasks and notes, by their owner column.
LINK_TABLES = {"crm_task_links": "task_id", "crm_note_links": "note_id"}


def _table(table: str) -> str:
    if table not in RECORD_TABLES:
        raise ValueError(f"not a CRM record table: {table}")
    return table


def _literal(q: str) -> str:
    """`q` as a literal for PostgREST: LIKE wildcards escaped, and the
    characters that would end a filter dropped."""
    text = "".join(c for c in q if c not in ',()"*\\')
    return text.replace("%", "\\%").replace("_", "\\_").strip()


def get_record(workspace_id: str, table: str, record_id: str) -> dict[str, Any] | None:
    """One record of the workspace, in the Trash or not; the caller decides."""
    rows = (
        supabase.table(_table(table))
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("id", record_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_records(
    workspace_id: str,
    table: str,
    *,
    q: str | None = None,
    filters: dict[str, Any] | None = None,
    any_of: dict[str, list[str]] | None = None,
    not_equal: dict[str, Any] | None = None,
    deleted: bool | None = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Records of one table, newest first; the Trash newest-deleted first.

    `deleted`: False the live records, True the Trash, None both.
    """
    long_lists = {c: v for c, v in (any_of or {}).items() if len(v) > _ID_BATCH}
    if long_lists:
        # A long id list goes in batches (PostgREST caps the URL): each batch
        # read as far as the page asked for, then merged in the same order.
        column, values = next(iter(long_lists.items()))
        wanted = sorted(set(values))
        merged: dict[str, dict[str, Any]] = {}
        for i in range(0, len(wanted), _ID_BATCH):
            for row in list_records(
                workspace_id,
                table,
                q=q,
                filters=filters,
                any_of={**(any_of or {}), column: wanted[i : i + _ID_BATCH]},
                not_equal=not_equal,
                deleted=deleted,
                limit=offset + limit,
                offset=0,
            ):
                merged[row["id"]] = row
        rows = sorted(merged.values(), key=lambda r: r["id"])
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        if deleted is True:
            rows.sort(key=lambda r: r["deleted_at"], reverse=True)
        return rows[offset : offset + limit]
    query = supabase.table(_table(table)).select("*").eq("workspace_id", workspace_id)
    for column, value in (filters or {}).items():
        query = query.eq(column, value)
    for column, value in (not_equal or {}).items():
        query = query.neq(column, value)
    for column, values in (any_of or {}).items():
        if not values:
            return []
        query = query.in_(column, values)
    if deleted is True:
        query = query.not_.is_("deleted_at", "null").order("deleted_at", desc=True)
    elif deleted is False:
        query = query.is_("deleted_at", "null")
    term = _literal(q or "")
    if term:
        query = query.or_(",".join(f"{c}.ilike.%{term}%" for c in _SEARCH[table]))
    return (
        query.order("updated_at", desc=True)
        .order("id")
        .range(offset, offset + limit - 1)
        .execute()
        .data
        or []
    )


def list_domains(
    workspace_id: str,
    *,
    company_ids: list[str] | None = None,
    domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Company domains, primary first. Trashed companies keep theirs
    (FR-CRM-DUP-01)."""
    if company_ids == [] or domains == []:
        return []

    def query_for() -> Any:
        return (
            supabase.table("crm_company_domains")
            .select("id, company_id, domain, is_primary, created_at")
            .eq("workspace_id", workspace_id)
        )

    if company_ids is not None:
        rows = _in_batches(query_for, "company_id", company_ids)
        if domains is not None:
            wanted = set(domains)
            rows = [r for r in rows if r["domain"] in wanted]
    elif domains is not None:
        rows = []
        for batch in text_batches(domains):
            rows += query_for().in_("domain", batch).execute().data or []
    else:
        rows = query_for().execute().data or []
    return sorted(rows, key=lambda r: (not r["is_primary"], r["domain"]))


def similar(workspace_id: str, kind: str, name: str, limit: int = 5) -> list[dict[str, Any]]:
    """FR-CRM-DUP-02 — live records whose name is alike (029 `crm_similar`)."""
    return (
        rpc(
            "crm_similar",
            {"p_workspace": workspace_id, "p_object": kind, "p_name": name, "p_limit": limit},
        )
        or []
    )


def list_expired(workspace_id: str, table: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Trash older than `cutoff`, for the purge job (FR-CRM-DEL-01)."""
    return (
        supabase.table(_table(table))
        .select("id, deleted_at")
        .eq("workspace_id", workspace_id)
        .lt("deleted_at", cutoff.isoformat())
        .execute()
        .data
        or []
    )


def purge(actor: dict[str, Any], items: list[dict[str, str]]) -> dict[str, Any]:
    """Data-model §5.4 — `items` of `{table, id}`, each already in the Trash."""
    return rpc("crm_purge", {"p_actor": actor, "p_plan": {"items": items}}) or {}


def _in_batches(query_for, column: str, values: list[str] | None) -> list[dict[str, Any]]:
    """Runs `query_for()` once per batch of `values` in `column`: an id
    list in a URL grows with the data, and PostgREST refuses a request line
    past a few kilobytes. None means no such filter (one query)."""
    if values is None:
        return query_for().execute().data or []
    wanted = sorted(set(values))
    rows: list[dict[str, Any]] = []
    for i in range(0, len(wanted), _ID_BATCH):
        rows += query_for().in_(column, wanted[i : i + _ID_BATCH]).execute().data or []
    return rows


def list_links(
    workspace_id: str,
    table: str,
    *,
    owner_ids: list[str] | None = None,
    record_type: str | None = None,
    record_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Task or note links (data-model §3.12, §3.13): those of some tasks or
    notes (`owner_ids`), or those naming some records."""
    if table not in LINK_TABLES:
        raise ValueError(f"not a CRM link table: {table}")
    if owner_ids == [] or record_ids == []:
        return []

    def query_for() -> Any:
        query = supabase.table(table).select("*").eq("workspace_id", workspace_id)
        if record_type is not None:
            query = query.eq("record_type", record_type)
        return query

    if owner_ids is not None and record_ids is not None:
        wanted = set(record_ids)
        rows = [
            r
            for r in _in_batches(query_for, LINK_TABLES[table], owner_ids)
            if r["record_id"] in wanted
        ]
    elif owner_ids is not None:
        rows = _in_batches(query_for, LINK_TABLES[table], owner_ids)
    else:
        rows = _in_batches(query_for, "record_id", record_ids)
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def list_mentions(workspace_id: str, note_ids: list[str]) -> list[dict[str, Any]]:
    """The members each note mentions (FR-CRM-NOTE-02)."""
    if not note_ids:
        return []
    return _in_batches(
        lambda: supabase.table("crm_note_mentions").select("*").eq("workspace_id", workspace_id),
        "note_id",
        note_ids,
    )


def list_contacts(
    workspace_id: str,
    *,
    opportunity_ids: list[str] | None = None,
    person_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Deal contacts with their Role in deal (data-model §3.10)."""
    if opportunity_ids == [] or person_ids == []:
        return []

    def query_for() -> Any:
        return (
            supabase.table("crm_opportunity_contacts").select("*").eq("workspace_id", workspace_id)
        )

    if opportunity_ids is not None:
        rows = _in_batches(query_for, "opportunity_id", opportunity_ids)
        if person_ids is not None:
            wanted = set(person_ids)
            rows = [r for r in rows if r["person_id"] in wanted]
    else:
        rows = _in_batches(query_for, "person_id", person_ids)
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]))


def list_stage_history(workspace_id: str, opportunity_id: str) -> list[dict[str, Any]]:
    """FR-CRM-OPP-02 — the deal's stages, oldest first."""
    return (
        supabase.table("crm_stage_history")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("opportunity_id", opportunity_id)
        .order("at")
        .execute()
        .data
        or []
    )


def deal_transition(actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Data-model §5.5 — a stage move and its side effects, CRM and TVA, in one
    transaction."""
    return rpc("crm_deal_transition", {"p_actor": actor, "p_plan": plan}) or {}


def link_project(actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Data-model §5.6 — Link TVA project (FR-CRM-TVA-18), audited on both sides."""
    return rpc("crm_link_project", {"p_actor": actor, "p_plan": plan}) or {}


def list_timeline(
    workspace_id: str,
    table: str,
    record_id: str,
    *,
    record_type: str,
    related: dict[str, list[str]] | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """FR-CRM-REC-03, FR-CRM-AUD-05 — the record's audit rows, newest first:
    its own; its Drive, task and note links' (by the record they name, so a
    purged link still shows); the rows of the Drive links, tasks and notes in
    `related` (table → ids); for a company, its domains'; and for a deal, its
    contacts'."""
    parts = [f"and(target_table.eq.{_table(table)},target_id.eq.{record_id})"]
    for link_table in ("crm_drive_links", "crm_task_links", "crm_note_links"):
        parts.append(
            f"and(target_table.eq.{link_table},"
            f"or(after->>record_id.eq.{record_id},before->>record_id.eq.{record_id}),"
            f"or(after->>record_type.eq.{record_type},before->>record_type.eq.{record_type}))"
        )
    for other, ids in (related or {}).items():
        if ids:
            parts.append(f"and(target_table.eq.{_table(other)},target_id.in.({','.join(ids)}))")
    if table == "crm_companies":
        parts.append(
            "and(target_table.eq.crm_company_domains,"
            f"or(after->>company_id.eq.{record_id},before->>company_id.eq.{record_id}))"
        )
    if table == "crm_opportunities":
        parts.append(
            "and(target_table.eq.crm_opportunity_contacts,"
            f"or(after->>opportunity_id.eq.{record_id},before->>opportunity_id.eq.{record_id}))"
        )
    query = (
        supabase.table("audit_log")
        .select("*")
        .eq("workspace_id", workspace_id)
        .or_(",".join(parts))
    )
    if before is not None:
        query = query.lt("id", before)
    return query.order("id", desc=True).limit(limit).execute().data or []


# ---------------------------------------------------------------------------
# Views, saved views, favourites — S6a (data-model §3.15, §3.16, §5.8)
# ---------------------------------------------------------------------------


def query(workspace_id: str, kind: str, spec: dict[str, Any], *, redact: bool) -> dict[str, Any]:
    """Data-model §5.8 — one page of a list with its total, groups and
    totals. `redact` (a Viewer) refuses every amount field again and leaves
    the amount columns out of the rows."""
    return (
        rpc(
            "crm_query",
            {"p_workspace": workspace_id, "p_object": kind, "p_spec": spec, "p_redact": redact},
        )
        or {}
    )


def list_object_fields() -> list[dict[str, Any]]:
    """The view registry `crm_query()` reads (034); `domain/crm/objects`
    mirrors it, and a test holds the two equal."""
    return (
        supabase.table("crm_object_fields")
        .select("*")
        .order("object")
        .order("field")
        .execute()
        .data
        or []
    )


def list_views(workspace_id: str, *, object: str | None = None) -> list[dict[str, Any]]:
    """Every saved view of the workspace, private ones included; the service
    keeps those the caller may see."""
    query_ = supabase.table("crm_saved_views").select("*").eq("workspace_id", workspace_id)
    if object is not None:
        query_ = query_.eq("object", object)
    return query_.order("position").order("name").execute().data or []


def get_view(workspace_id: str, view_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("crm_saved_views")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("id", view_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_favourites(
    workspace_id: str,
    *,
    user_id: str | None = None,
    target_type: str | None = None,
    target_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """A person's favourites, or those pinning some targets."""
    query_ = supabase.table("crm_favourites").select("*").eq("workspace_id", workspace_id)
    if user_id is not None:
        query_ = query_.eq("user_id", user_id)
    if target_type is not None:
        query_ = query_.eq("target_type", target_type)
    if target_ids is not None:
        if not target_ids:
            return []
        query_ = query_.in_("target_id", target_ids)
    return query_.order("position").order("created_at").execute().data or []


# ---------------------------------------------------------------------------
# Today, search, merge — S7 (data-model §5.7, §5.9; plan §5.8)
# ---------------------------------------------------------------------------

#: FR-CRM-PIPE: the stages of an open deal (domain/crm/pipeline.OPEN).
_OPEN_STAGES = ("discovery", "qualification", "proposal", "negotiation")


def due_next_steps(workspace_id: str, owner_id: str, by: str) -> list[dict[str, Any]]:
    """Today §2 — a person's open deals whose next step is due on or before
    `by` (their today, YYYY-MM-DD), oldest first."""
    return (
        supabase.table("crm_opportunities")
        .select("id, name, company_id, next_step, next_step_on, stage, owner_id")
        .eq("workspace_id", workspace_id)
        .eq("owner_id", owner_id)
        .in_("stage", list(_OPEN_STAGES))
        .is_("deleted_at", "null")
        .lte("next_step_on", by)
        .order("next_step_on")
        .order("name")
        .limit(500)
        .execute()
        .data
        or []
    )


def tasks_for_today(workspace_id: str, assignee_id: str, by: str) -> list[dict[str, Any]]:
    """Today §3 — a person's open tasks in the Today bucket or due on or
    before `by`."""
    return (
        supabase.table("crm_tasks")
        .select("id, title, status, due_on, bucket, assignee_id")
        .eq("workspace_id", workspace_id)
        .eq("assignee_id", assignee_id)
        .neq("status", "done")
        .is_("deleted_at", "null")
        .or_(f"bucket.eq.today,due_on.lte.{by}")
        .order("due_on", nullsfirst=False)
        .order("title")
        .limit(500)
        .execute()
        .data
        or []
    )


def nurturing_without_next_touch(workspace_id: str, owner_id: str) -> list[dict[str, Any]]:
    """Today §5 candidates — a person's live companies in Nurturing with no
    next touch; the service drops those with an open task."""
    return (
        supabase.table("crm_companies")
        .select("id, name, lead_stage, next_touch_on, owner_id, deleted_at")
        .eq("workspace_id", workspace_id)
        .eq("owner_id", owner_id)
        .eq("lead_stage", "nurturing")
        .is_("next_touch_on", "null")
        .is_("deleted_at", "null")
        .order("name")
        .limit(500)
        .execute()
        .data
        or []
    )


def search(workspace_id: str, q: str, limit: int, *, redact: bool) -> dict[str, Any]:
    """Data-model §5.9 — up to `limit` live hits per object, best first. With
    `redact` (a Viewer) no amount column is read."""
    return (
        rpc(
            "crm_search",
            {"p_workspace": workspace_id, "p_q": q, "p_limit": limit, "p_redact": redact},
        )
        or {}
    )


def merge_companies(actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Data-model §5.7 — `{survivor, moved}`; both companies re-checked and
    locked in the actor's workspace, everything moved in one transaction."""
    return rpc("crm_merge_companies", {"p_actor": actor, "p_plan": plan}) or {}


# ---------------------------------------------------------------------------
# The Twenty importer — S8 (data-model §3.17; FR-CRM-MIG)
# ---------------------------------------------------------------------------

#: The record tables an import matches on `twenty_id` (029, 030, 036).
TWENTY_TABLES = frozenset(
    {"crm_companies", "crm_people", "crm_opportunities", "crm_tasks", "crm_notes"}
)


def list_by_twenty_ids(
    workspace_id: str, table: str, twenty_ids: list[str]
) -> list[dict[str, Any]]:
    """Records of one table by Twenty id, Trash included. The ids go into
    the URL, so they are read in batches (PostgREST refuses a long one)."""
    if table not in TWENTY_TABLES:
        raise ValueError(f"not a table an import matches: {table}")
    rows: list[dict[str, Any]] = []
    for batch in text_batches(twenty_ids):
        rows += (
            supabase.table(table)
            .select("*")
            .eq("workspace_id", workspace_id)
            .in_("twenty_id", batch)
            .execute()
            .data
            or []
        )
    return rows


#: Characters of values per PostgREST `in` filter: well under the few
#: kilobytes a request line may have, however long each value is.
_URL_BUDGET = 2000


def text_batches(values: Any) -> list[list[str]]:
    """Distinct values, sorted, in batches that each fit `_URL_BUDGET`."""
    batches: list[list[str]] = []
    size = 0
    for value in sorted({v for v in values if v}):
        if not batches or size + len(value) + 1 > _URL_BUDGET:
            batches.append([])
            size = 0
        batches[-1].append(value)
        size += len(value) + 1
    return batches


def deals_of_companies(workspace_id: str, company_ids: list[str]) -> list[dict[str, Any]]:
    """The live deals of some companies, for the lead stage an import
    derives (FR-CRM-MIG-05)."""
    rows: list[dict[str, Any]] = []
    wanted = sorted(set(company_ids))
    for i in range(0, len(wanted), _ID_BATCH):
        rows += (
            supabase.table("crm_opportunities")
            .select("id, company_id, stage, twenty_id")
            .eq("workspace_id", workspace_id)
            .in_("company_id", wanted[i : i + _ID_BATCH])
            .is_("deleted_at", "null")
            .execute()
            .data
            or []
        )
    return rows


#: Names per `crm_similar_many` call: they travel in the body, not the URL.
_NAME_BATCH = 200


def similar_many(
    workspace_id: str, kind: str, names: list[str], limit: int = 3
) -> list[dict[str, Any]]:
    """FR-CRM-DUP-02 for many names (036 `crm_similar_many`): each match with
    `idx`, the 1-based position of the name it is like."""
    found: list[dict[str, Any]] = []
    for i in range(0, len(names), _NAME_BATCH):
        batch = names[i : i + _NAME_BATCH]
        for row in (
            rpc(
                "crm_similar_many",
                {"p_workspace": workspace_id, "p_object": kind, "p_names": batch, "p_limit": limit},
            )
            or []
        ):
            found.append({**row, "idx": row["idx"] + i})
    return found


_RUN_COLUMNS = "id, workspace_id, kind, object, file_sha256, status, report, options, " + (
    "created_by, created_at, applied_at"
)


def record_import(actor: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """036 `crm_record_import` — a dry run added, or a run marked applied or
    failed, with its `import.*` audit row (counts only) in one transaction.
    Scoped by the actor's workspace."""
    return rpc("crm_record_import", {"p_actor": actor, "p_run": run}) or {}


def get_import_run(workspace_id: str, run_id: str) -> dict[str, Any] | None:
    rows = (
        supabase.table("crm_import_runs")
        .select(_RUN_COLUMNS)
        .eq("workspace_id", workspace_id)
        .eq("id", run_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_import_runs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recent runs, newest first, without their reports' rows."""
    return (
        supabase.table("crm_import_runs")
        .select(
            "id, kind, object, file_sha256, status, counts:report->counts, options, "
            "created_by, created_at, applied_at"
        )
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# The job log — S9 (data-model §3.18; FR-CRM-JOB)
# ---------------------------------------------------------------------------

_JOB_COLUMNS = (
    "id, workspace_id, job, started_at, finished_at, outcome, counts, error, error_public, alerted"
)


def start_job_run(job: str, workspace_id: str | None, started_at: datetime) -> dict[str, Any]:
    """A run begins: its row, outcome `running`."""
    rows = (
        supabase.table("crm_job_runs")
        .insert({"job": job, "workspace_id": workspace_id, "started_at": started_at.isoformat()})
        .execute()
        .data
    )
    return rows[0]


def finish_job_run(run_id: str, values: dict[str, Any]) -> None:
    """A run ends: `outcome`, `finished_at`, `counts` and, when it failed,
    `error` and `error_public`."""
    supabase.table("crm_job_runs").update(values).eq("id", run_id).execute()


def job_streak(job: str, workspace_id: str | None, limit: int = 200) -> list[dict[str, Any]]:
    """The job's latest finished runs in one workspace (or the digest tick's),
    newest first, for the three-failure alert (FR-CRM-JOB-02)."""
    query = (
        supabase.table("crm_job_runs")
        .select("id, outcome, alerted, started_at")
        .eq("job", job)
        .neq("outcome", "running")
    )
    query = (
        query.is_("workspace_id", "null")
        if workspace_id is None
        else query.eq("workspace_id", workspace_id)
    )
    return query.order("started_at", desc=True).limit(limit).execute().data or []


def mark_job_alerted(run_id: str) -> None:
    supabase.table("crm_job_runs").update({"alerted": True}).eq("id", run_id).execute()


def list_job_runs(
    workspace_id: str, *, job: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """This workspace's runs, newest first, with `error` (members only).
    Never a row of another workspace or one with none (FR-CRM-JOB-01)."""
    query = supabase.table("crm_job_runs").select(_JOB_COLUMNS).eq("workspace_id", workspace_id)
    if job:
        query = query.eq("job", job)
    return query.order("started_at", desc=True).limit(limit).execute().data or []


def list_all_job_runs(*, job: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Every workspace's runs and the digest tick's, newest first, for the
    TVA admin panel: metadata and `error_public` only, never `error`."""
    query = supabase.table("crm_job_runs").select(
        "id, workspace_id, job, started_at, finished_at, outcome, counts, error_public, alerted"
    )
    if job:
        query = query.eq("job", job)
    return query.order("started_at", desc=True).limit(limit).execute().data or []


def latest_job_runs() -> list[dict[str, Any]]:
    """037 `crm_job_latest` — the latest run of each job in each workspace,
    and the digest tick's: no counts, no error."""
    return rpc("crm_job_latest", {}) or []


# ---------------------------------------------------------------------------
# Notifications and digests — S9 (data-model §3.19, §3.20; FR-CRM-NOTIF)
# ---------------------------------------------------------------------------

_NOTIFICATION_COLUMNS = (
    "id, workspace_id, user_id, kind, record_type, record_id, title, dedupe_key, created_at, "
    "read_at"
)


def notify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """037 `crm_notify` — adds the notifications whose person is a member of
    their workspace and whose dedupe key is new; returns those added."""
    if not rows:
        return []
    return rpc("crm_notify", {"p_rows": rows}) or []


def list_notifications(
    user_id: str,
    workspace_ids: list[str],
    *,
    unread_only: bool = False,
    before: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """A person's notifications in the workspaces they belong to now,
    newest first (FR-CRM-WS-02)."""
    if not workspace_ids:
        return []
    query = (
        supabase.table("crm_notifications")
        .select(_NOTIFICATION_COLUMNS)
        .eq("user_id", user_id)
        .in_("workspace_id", workspace_ids)
    )
    if unread_only:
        query = query.is_("read_at", "null")
    if before:
        query = query.lt("created_at", before)
    return query.order("created_at", desc=True).order("id").limit(limit).execute().data or []


def unread_count(user_id: str, workspace_ids: list[str]) -> int:
    if not workspace_ids:
        return 0
    response = (
        supabase.table("crm_notifications")
        .select("id", count="exact", head=True)
        .eq("user_id", user_id)
        .in_("workspace_id", workspace_ids)
        .is_("read_at", "null")
        .execute()
    )
    return int(response.count or 0)


def mark_read(user_id: str, workspace_ids: list[str], ids: list[str] | None, at: datetime) -> int:
    """Marks the person's own unread notifications read: `ids`, or every
    one when None. Ids go in batches, as every list in a URL does."""
    if not workspace_ids or ids == []:
        return 0
    batches: list[list[str] | None] = (
        [None] if ids is None else [ids[i : i + _ID_BATCH] for i in range(0, len(ids), _ID_BATCH)]
    )
    updated = 0
    for batch in batches:
        query = (
            supabase.table("crm_notifications")
            .update({"read_at": at.isoformat()})
            .eq("user_id", user_id)
            .in_("workspace_id", workspace_ids)
            .is_("read_at", "null")
        )
        if batch is not None:
            query = query.in_("id", batch)
        updated += len(query.execute().data or [])
    return updated


def claim_digest(user_id: str, kind: str, local_date: str, workspace_id: str | None) -> str | None:
    """037 `crm_digest_claim` — the sent marker's id when this digest was not
    sent yet, else None (exactly once, FR-CRM-NOTIF-03, -04)."""
    found = rpc(
        "crm_digest_claim",
        {
            "p_user": user_id,
            "p_kind": kind,
            "p_local_date": local_date,
            "p_workspace": workspace_id,
            "p_channels": [],
        },
    )
    return found or None


def set_digest_channels(send_id: str, channels: list[str]) -> None:
    supabase.table("crm_digest_sends").update({"channels": channels}).eq("id", send_id).execute()


def list_all_memberships() -> list[dict[str, Any]]:
    """Every membership of every workspace: the digest tick's recipients."""
    return (
        supabase.table("crm_memberships")
        .select("id, workspace_id, user_id, role")
        .order("user_id")
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# Flags — S9 (data-model §3.21; FR-CRM-FLAG)
# ---------------------------------------------------------------------------

#: The tables with flags, and the columns a flag reads from each.
FLAG_COLUMNS = {
    "crm_companies": "id, name, lead_stage, next_touch_on, owner_id, deleted_at, flag_level, flags",
    "crm_people": "id, first_name, last_name, company_id, owner_id, deleted_at, flag_level, flags",
    "crm_opportunities": (
        "id, name, stage, company_id, owner_id, next_step_on, expected_close_on, "
        "stage_entered_at, created_at, deleted_at, flag_level, flags"
    ),
    "crm_tasks": "id, title, status, due_on, assignee_id, deleted_at, flag_level, flags",
}
#: Rows per page when a whole workspace is read.
_PAGE = 1000


def flag_rows(
    workspace_id: str,
    table: str,
    *,
    ids: list[str] | None = None,
    company_ids: list[str] | None = None,
    open_or_flagged: bool = False,
    flagged_only: bool = False,
) -> list[dict[str, Any]]:
    """Live rows of a flagged table with what their flags read: some `ids`,
    a deal table's rows of some companies, or the whole workspace page by
    page. `open_or_flagged` keeps deals in an open stage and open tasks, and
    any row that carries a flag still (so a closed one is cleared);
    `flagged_only` keeps flagged rows."""
    if table not in FLAG_COLUMNS:
        raise ValueError(f"not a flagged table: {table}")

    def base() -> Any:
        query = (
            supabase.table(table)
            .select(FLAG_COLUMNS[table])
            .eq("workspace_id", workspace_id)
            .is_("deleted_at", "null")
        )
        if flagged_only:
            query = query.neq("flag_level", "none")
        if open_or_flagged and table == "crm_opportunities":
            query = query.or_(f"stage.in.({','.join(_OPEN_STAGES)}),flag_level.neq.none")
        if open_or_flagged and table == "crm_tasks":
            query = query.or_("status.neq.done,flag_level.neq.none")
        return query

    rows: list[dict[str, Any]] = []
    if ids is not None or company_ids is not None:
        column, wanted = ("id", ids) if ids is not None else ("company_id", company_ids)
        values = sorted(set(wanted or []))
        for i in range(0, len(values), _ID_BATCH):
            rows += base().in_(column, values[i : i + _ID_BATCH]).execute().data or []
        return rows
    offset = 0
    while True:
        page = base().order("id").range(offset, offset + _PAGE - 1).execute().data or []
        rows += page
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


def flag_facts(
    workspace_id: str, deal_ids: list[str] | None, company_ids: list[str] | None
) -> dict[str, Any]:
    """038 `crm_flag_facts` — `{activity: {deal id: time}, open_task_companies:
    [id]}`, for some deals and companies or (None) all of the workspace."""
    return (
        rpc(
            "crm_flag_facts",
            {"p_workspace": workspace_id, "p_deal_ids": deal_ids, "p_company_ids": company_ids},
        )
        or {}
    )


def set_flags(workspace_id: str, table: str, rows: list[dict[str, Any]], at: datetime) -> int:
    """038 `crm_set_flags` — stores computed flags, in chunks; returns how
    many rows' flags changed. Writes no audit row and leaves `updated_at`
    alone: a recompute is not an edit (data-model §3.21)."""
    changed = 0
    for i in range(0, len(rows), _PAGE):
        changed += int(
            rpc(
                "crm_set_flags",
                {
                    "p_workspace": workspace_id,
                    "p_table": table,
                    "p_rows": rows[i : i + _PAGE],
                    "p_at": at.isoformat(),
                },
            )
            or 0
        )
    return changed


def flagged_owned(workspace_id: str, table: str, owner_id: str) -> list[dict[str, Any]]:
    """Today §1 and §4 — a person's live flagged companies, people or deals."""
    if table not in ("crm_companies", "crm_people", "crm_opportunities"):
        raise ValueError(f"not an owned flagged table: {table}")
    return (
        supabase.table(table)
        .select(FLAG_COLUMNS[table])
        .eq("workspace_id", workspace_id)
        .eq("owner_id", owner_id)
        .neq("flag_level", "none")
        .is_("deleted_at", "null")
        .limit(500)
        .execute()
        .data
        or []
    )


def flag_counts(workspace_id: str) -> dict[str, dict[str, int]]:
    """The weekly digest's flag counts: live rows at red and amber, per
    table."""
    counts: dict[str, dict[str, int]] = {}
    for table in FLAG_COLUMNS:
        counts[table] = {}
        for level in ("red", "amber"):
            response = (
                supabase.table(table)
                .select("id", count="exact", head=True)
                .eq("workspace_id", workspace_id)
                .eq("flag_level", level)
                .is_("deleted_at", "null")
                .execute()
            )
            counts[table][level] = int(response.count or 0)
    return counts


def stage_moves_since(workspace_id: str, since: datetime) -> list[dict[str, Any]]:
    """The weekly digest's pipeline movement (data-model §3.11)."""
    return (
        supabase.table("crm_stage_history")
        .select("opportunity_id, from_stage, to_stage, at")
        .eq("workspace_id", workspace_id)
        .gte("at", since.isoformat())
        .order("at")
        .limit(5000)
        .execute()
        .data
        or []
    )


def closed_since(workspace_id: str, since: str) -> list[dict[str, Any]]:
    """Live deals won or lost on or after `since` (YYYY-MM-DD)."""
    return (
        supabase.table("crm_opportunities")
        .select("id, name, stage, amount_in_inr, won_on, lost_on, owner_id")
        .eq("workspace_id", workspace_id)
        .is_("deleted_at", "null")
        .or_(f"won_on.gte.{since},lost_on.gte.{since}")
        .order("name")
        .limit(1000)
        .execute()
        .data
        or []
    )


def deal_amounts(workspace_id: str, ids: list[str]) -> dict[str, Any]:
    """Deal id → amount in INR, for a digest's next-step lines."""
    found: dict[str, Any] = {}
    wanted = sorted(set(ids))
    for i in range(0, len(wanted), _ID_BATCH):
        for row in (
            supabase.table("crm_opportunities")
            .select("id, amount_in_inr")
            .eq("workspace_id", workspace_id)
            .in_("id", wanted[i : i + _ID_BATCH])
            .execute()
            .data
            or []
        ):
            found[row["id"]] = row.get("amount_in_inr")
    return found


def tasks_due_by(workspace_id: str, by: str) -> list[dict[str, Any]]:
    """Automation 7 — live open tasks due on or before `by`, page by page."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            supabase.table("crm_tasks")
            .select("id, title, status, due_on, assignee_id")
            .eq("workspace_id", workspace_id)
            .neq("status", "done")
            .is_("deleted_at", "null")
            .lte("due_on", by)
            .order("id")
            .range(offset, offset + _PAGE - 1)
            .execute()
            .data
            or []
        )
        rows += page
        if len(page) < _PAGE:
            return rows
        offset += _PAGE
