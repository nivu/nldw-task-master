"""The Review Queue — spec 009 FR-CRMAI-REV-01 to -10, FR-CRMAI-FLAG-04,
FR-CRMAI-CHG-03, -09, -11 (items); contracts §2.2; plan §5.4, §5.11.

Every AI suggestion waits here as an item until a person decides it. The
rules (types, routing, dedupe, staleness, expiry, who sees what) are
`domain/crm/review.py`; this module gathers the rows, and writes through
migration 061's functions and, for an approval, through the very services a
manual edit uses:

- `next_step_change`: `deals.update`, as `PATCH /crm/opportunities/{id}`;
- `enrichment_conflict`: `records.update`, as `PATCH /crm/companies/{id}`;
- `deal_link`: the note's links, as `PATCH /crm/notes/{id}`;
- `task_from_*`: `008`'s `create_task`, with only the fields of plan §5.4.

So every validation, permission check, pipeline rule, audit entry and
optimistic lock of the manual path applies (FR-CRMAI-REV-04). The change is
made with a copy of the caller's context carrying the item's id, so its audit
row says it was an approved suggestion (FR-CRMAI-REV-05); a task's own audit
rows carry it through `TaskActor.suggestion_id`.

An approval first claims the item (`crm_review_claim`: only one claim wins),
then makes the change. If the manual path refuses, the item opens again and
shows the refusal (`crm_review_release`), and the refusal is the answer.

Creating or refreshing an item sends no bell and no message
(FR-CRMAI-REV-10): Today's To review section and the daily digest carry
them. An item whose target is in the Trash is left out of every list and of
Today, and cannot be decided (FR-CRMAI-CHG-11); restoring the record shows it
again.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.domain.crm import ai_rules
from app.domain.crm import review as rules
from app.domain.crm import today as today_rules
from app.domain.tasks.service import TaskActor, create_task
from app.schemas.crm import ReviewApproveIn, ReviewItemOut
from app.schemas.tasks import TaskCreate
from app.services import supabase as db
from app.services.crm import CrmRefused, deals, store
from app.services.crm import records as base
from app.services.crm.context import actor

#: The target tables of an item (data-model §3.2 `record_type`).
TARGET_TABLES = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "note": "crm_notes",
}
#: What "Edit and approve" may change, per type (contracts §2.2.1).
EDITABLE = {
    "next_step_change": frozenset(rules.NEXT_STEP_FIELDS),
    "task_from_commitment": frozenset({"title", "due_on", "assignee_id", "notes"}),
    "task_from_meeting": frozenset({"title", "due_on", "assignee_id", "notes"}),
    "deal_link": frozenset(),
    "enrichment_conflict": frozenset({"value"}),
    "rule_suggestion": frozenset(),
}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _sync(workspace_id: str) -> dict[str, Any]:
    """The jobs' own actor: items are written as `sync`, as email sync is."""
    return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": workspace_id}


def _system(workspace_id: str) -> dict[str, Any]:
    return {"user_id": None, "kind": "system", "channel": None, "workspace_id": workspace_id}


# ---------------------------------------------------------------------------
# Creating items — FR-CRMAI-REV-08, -10
# ---------------------------------------------------------------------------


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return ai_rules.mask_money(value)
    if isinstance(value, dict):
        return {k: _masked(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_masked(v) for v in value]
    return value


def upsert(workspace_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stores items built by a job (`[{type, owner_id, record_type, record_id,
    member_id?, suggestion, current_values, source_kind, source_key,
    source_version, source_link, dedupe_key, model, generated_at,
    deal_choices}]`), masking money in every suggestion string again
    (FR-CRMAI-LLM-05), and returns `[{id, created}]`. Sends nothing."""
    if not items:
        return []
    masked = [{**item, "suggestion": _masked(item.get("suggestion") or {})} for item in items]
    return store.review_upsert(_sync(workspace_id), masked)


# ---------------------------------------------------------------------------
# Reading — FR-CRMAI-REV-01, -03, FR-CRMAI-FLAG-04
# ---------------------------------------------------------------------------


def _targets(ws_id: str, items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The records the items target, Trash included."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for record_type, table in TARGET_TABLES.items():
        ids = {i["record_id"] for i in items if i["record_type"] == record_type}
        for rid, row in base.by_id(ws_id, table, ids).items():
            found[(record_type, rid)] = row
    return found


def _untrashed(ws_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FR-CRMAI-CHG-11 — an item whose target is in the Trash is left out."""
    targets = _targets(ws_id, items)
    return [
        i
        for i in items
        if (row := targets.get((i["record_type"], i["record_id"]))) and not row.get("deleted_at")
    ]


def _name(record_type: str, row: dict[str, Any] | None) -> str | None:
    if row is None:
        return None
    return base.record_name(record_type, row)


def _role(ws_id: str, person_id: str) -> str | None:
    return next((m["role"] for m in store.list_members(ws_id) if m["user_id"] == person_id), None)


def present(ws_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items as the queue shows them (contracts §2.2.1): what each would
    change, the value it would replace, its deal choices with their next
    steps now, where it came from and its AI provenance."""
    if not items:
        return []
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    targets = _targets(ws_id, items)
    choices: dict[str, list[str]] = {}
    for row in store.list_review_item_deals(ws_id, [i["id"] for i in items]):
        choices.setdefault(row["item_id"], []).append(row["opportunity_id"])
    deal_rows = base.by_id(ws_id, "crm_opportunities", {d for ids in choices.values() for d in ids})
    out = []
    for item in items:
        target = targets.get((item["record_type"], item["record_id"]))
        mine = [
            deal_rows[d]
            for d in sorted(choices.get(item["id"], []))
            if d in deal_rows and not deal_rows[d].get("deleted_at")
        ]
        recorded = item.get("current_values") or {}
        if item["type"] == "next_step_change":
            # One deal: its next step when the item was made; several: each
            # deal choice carries its own.
            current = recorded.get(mine[0]["id"], {}) if len(mine) == 1 else {}
        else:
            current = recorded
        link = dict(item.get("source_link") or {})
        if link.get("mailbox_id") and link.get("thread_id"):
            link["thread_key"] = f"{link['mailbox_id']}:{link['thread_id']}"
        owner = item.get("owner_id")
        decider = item.get("decided_by")
        out.append(
            ReviewItemOut(
                id=item["id"],
                type=item["type"],
                type_label=rules.TYPES[item["type"]],
                status=item["status"],
                owner={"id": owner, "name": names.get(owner)} if owner else None,
                target={
                    "record_type": item["record_type"],
                    "record_id": item["record_id"],
                    "name": _name(item["record_type"], target),
                },
                deal_choices=[
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "next_step": d.get("next_step"),
                        "next_step_on": d.get("next_step_on"),
                    }
                    for d in mine
                ],
                suggestion=item.get("suggestion") or {},
                current=current,
                source={
                    "kind": item["source_kind"],
                    "label": rules.SOURCE_KINDS[item["source_kind"]],
                    "link": link,
                },
                ai={
                    "source_kind": item["source_kind"],
                    "model": item.get("model"),
                    "generated_at": item.get("generated_at"),
                },
                refusal=item.get("refusal"),
                created_at=item["created_at"],
                expires_on=rules.expires_on(item["created_at"]),
                decided_at=item.get("decided_at"),
                decided_by={"id": decider, "name": names.get(decider)} if decider else None,
            ).model_dump(mode="json")
        )
    return out


def list_items(ctx: Any, *, scope: str, status: str, limit: int) -> dict[str, Any]:
    """Contracts §2.2 `GET /crm/review` — `{items, total}`. A Member's own
    open items; an Admin's `mine` adds the items that belong to the Admins,
    `all` every item, `decided` the last 90 days' decisions (tombstones left
    out). A Viewer has no queue: nothing, never a refusal."""
    if ctx.role not in ("admin", "member"):
        return {"items": [], "total": 0}
    if (scope == "all" or status == "decided") and ctx.role != "admin":
        raise CrmRefused(403, rules.ADMINS_ONLY)
    since = _now() - timedelta(days=rules.TOMBSTONE_DAYS) if status == "decided" else None
    rows = store.list_review_items(ctx.workspace_id, status=status, since=since)
    if scope == "mine":
        rows = [r for r in rows if rules.is_mine(ctx.role, ctx.user.id, r)]
    rows = _untrashed(ctx.workspace_id, rows)
    return {"items": present(ctx.workspace_id, rows[:limit]), "total": len(rows)}


def _open_mine(ws_id: str, person_id: str, role: str | None) -> list[dict[str, Any]]:
    rows = store.list_review_items(ws_id, status="open")
    return _untrashed(ws_id, [r for r in rows if rules.is_mine(role or "", person_id, r)])


def visible(ws_id: str, person_id: str, role: str | None = None) -> bool:
    """FR-CRMAI-FLAG-04 — the queue and To review show while any phase is
    on, or while the person has open items (an Admin: while the workspace
    has any). Never for a Viewer."""
    role = role or _role(ws_id, person_id)
    if role not in ("admin", "member"):
        return False
    if any(ai_rules.phases_of(store.ai_setting(ws_id)).values()):
        return True
    rows = store.list_review_items(ws_id, status="open")
    if role == "member":
        rows = [r for r in rows if r.get("owner_id") == person_id]
    return bool(_untrashed(ws_id, rows))


def open_count(ws_id: str, person_id: str, role: str | None = None) -> int:
    """The person's own open items (To review's count)."""
    role = role or _role(ws_id, person_id)
    if role not in ("admin", "member"):
        return 0
    return len(_open_mine(ws_id, person_id, role))


def today_section(ws_id: str, person_id: str) -> list[dict[str, Any]]:
    """FR-CRMAI-REV-09 — Today's sixth section, To review, as a list of none
    or one: absent for a Viewer and while the queue is not visible."""
    role = _role(ws_id, person_id)
    if not visible(ws_id, person_id, role):
        return []
    items = _open_mine(ws_id, person_id, role)
    targets = _targets(ws_id, items)
    rows = [
        {
            "id": i["id"],
            "created_at": i["created_at"],
            "title": rules.TYPES[i["type"]],
            "detail": _name(i["record_type"], targets.get((i["record_type"], i["record_id"])))
            or "",
        }
        for i in items
    ]
    return [today_rules.to_review(rows)]


# ---------------------------------------------------------------------------
# Deciding — FR-CRMAI-REV-04 to -06, contracts §2.2
# ---------------------------------------------------------------------------


def _load(ctx: Any, item_id: str) -> dict[str, Any]:
    """The item, if the caller may see it: another workspace's, or another
    Member's, is not found (FR-CRMAI-REV-03)."""
    row = store.get_review_item(ctx.workspace_id, item_id) if base._uuid(item_id) else None
    if row is None or not rules.may_decide(ctx.role, ctx.user.id, row):
        raise CrmRefused(404, rules.NO_SUCH)
    return row


def _open_and_live(ctx: Any, item: dict[str, Any]) -> dict[str, Any]:
    """An item still open whose target is not in the Trash; its target."""
    if item["status"] != "open":
        raise CrmRefused(409, rules.ALREADY_DECIDED)
    target = _targets(ctx.workspace_id, [item]).get((item["record_type"], item["record_id"]))
    if target is None or target.get("deleted_at"):
        raise CrmRefused(422, rules.TRASHED)
    return target


def _edits(item: dict[str, Any], body: ReviewApproveIn) -> dict[str, Any]:
    edits = body.edits or {}
    for key in edits:
        if key not in EDITABLE[item["type"]]:
            raise CrmRefused(422, f"{key} cannot be edited on this suggestion.")
    return edits


def _deal(ctx: Any, item: dict[str, Any], body: ReviewApproveIn) -> str:
    """The deal a next step or a link applies to: the one chosen, or the
    only choice (Q-07)."""
    choices = sorted(
        d["opportunity_id"] for d in store.list_review_item_deals(ctx.workspace_id, [item["id"]])
    )
    if not choices and item["record_type"] == "opportunity":
        choices = [item["record_id"]]
    chosen = body.deal_id or (choices[0] if len(choices) == 1 else None)
    if chosen is None or chosen not in choices:
        raise CrmRefused(422, rules.CHOOSE_DEAL)
    deal = base.by_id(ctx.workspace_id, "crm_opportunities", [chosen]).get(chosen)
    if deal is None or deal.get("deleted_at"):
        raise CrmRefused(422, rules.TRASHED)
    return chosen


#: What an approval does once claimed: `(approver) → (result, shown)`.
Change = Callable[[Any], tuple[dict[str, Any], dict[str, Any]]]

#: 033's optimistic lock (CRM09) when `expect_updated_at` no longer matches.
LOCKED = "This record changed while you were editing it; reload and try again."


def _locked(refused: CrmRefused) -> CrmRefused:
    """A change between the check and the write is refused with contracts
    §2.2's sentence, not the manual path's."""
    if refused.status == 409 and refused.detail == LOCKED:
        return CrmRefused(409, rules.CHANGED)
    return refused


def _next_step(ctx: Any, item: dict[str, Any], body: ReviewApproveIn) -> Change:
    edits = _edits(item, body)
    deal_id = _deal(ctx, item, body)
    suggestion = item.get("suggestion") or {}
    values = {f: edits.get(f, suggestion.get(f)) for f in rules.NEXT_STEP_FIELDS}
    recorded = (item.get("current_values") or {}).get(deal_id) or {}

    def change(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        row = base.live(ctx.workspace_id, "opportunity", deal_id)
        # Edit and approve is made against the value the person sees now.
        if not edits and rules.stale_fields(recorded, row):
            raise CrmRefused(409, rules.CHANGED)
        try:
            shown = deals.update(
                approver, deal_id, {**values, "expect_updated_at": row["updated_at"]}
            )
        except CrmRefused as refused:
            raise _locked(refused) from None
        return {"opportunity_id": deal_id}, {"opportunity": shown}

    return change


def _enrichment(ctx: Any, item: dict[str, Any], body: ReviewApproveIn) -> Change:
    edits = _edits(item, body)
    suggestion = item.get("suggestion") or {}
    field = suggestion.get("field")
    if field not in rules.ENRICHED_FIELDS:
        raise CrmRefused(422, rules.NOT_YET)
    value = edits.get("value", suggestion.get("value"))
    if field == "linkedin" and isinstance(value, str):
        # Edit and approve sends the address as typed; the field is a Links.
        value = {"primary": {"url": value}, "others": []}
    recorded = {k: v for k, v in (item.get("current_values") or {}).items() if k == field}

    def change(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        row = base.live(ctx.workspace_id, "company", item["record_id"])
        if not edits and rules.stale_fields(recorded, row):
            raise CrmRefused(409, rules.CHANGED)
        try:
            shown = base.update(
                approver,
                "company",
                item["record_id"],
                {field: value, "expect_updated_at": row["updated_at"]},
            )
        except CrmRefused as refused:
            raise _locked(refused) from None
        return {"field": field}, {"company": shown}

    return change


def _deal_link(ctx: Any, item: dict[str, Any], body: ReviewApproveIn) -> Change:
    _edits(item, body)
    deal_id = _deal(ctx, item, body)
    note_id = item["record_id"]

    def change(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        current = store.list_links(ctx.workspace_id, "crm_note_links", owner_ids=[note_id])
        links = [{"record_type": lk["record_type"], "record_id": lk["record_id"]} for lk in current]
        if {"record_type": "opportunity", "record_id": deal_id} not in links:
            links.append({"record_type": "opportunity", "record_id": deal_id})
        shown = base.update(approver, "note", note_id, {"links": links})
        return {"note_id": note_id, "opportunity_id": deal_id}, {"note": shown}

    return change


def _assignee(ws_id: str, owner_id: str | None, approver_id: str) -> str:
    """FR-CRMAI-REV-06 — the commitment's owner when an active Admin or
    Member of the workspace, else the approver."""
    if owner_id:
        role = _role(ws_id, owner_id)
        profile = db.get_profile(owner_id) or {}
        if role in ("admin", "member") and profile.get("is_active"):
            return owner_id
    return approver_id


def _task(ctx: Any, item: dict[str, Any], body: ReviewApproveIn, target: dict) -> Change:
    edits = _edits(item, body)
    suggestion = item.get("suggestion") or {}
    links = [(item["record_type"], item["record_id"])]
    company_id = target["id"] if item["record_type"] == "company" else target.get("company_id")
    if company_id:
        company = base.by_id(ctx.workspace_id, "crm_companies", [company_id]).get(company_id)
        if company and not company.get("deleted_at"):
            links.append(("company", company_id))
    # 008's create_task refuses the same record twice: a company target is
    # sent once (plan §5.4).
    links = list(dict.fromkeys(links))
    try:
        payload = TaskCreate(
            title=edits.get("title", suggestion.get("title")),
            notes=edits.get("notes"),
            due_on=edits.get("due_on", suggestion.get("due_on")),
            bucket=None,
            priority="normal",
            assignee_id=edits.get("assignee_id")
            or _assignee(ctx.workspace_id, suggestion.get("owner_id"), ctx.user.id),
            workspace_id=ctx.workspace_id,
            links=[{"record_type": t, "record_id": i} for t, i in links],
            source=rules.TASK_SOURCES[item["type"]],
            source_ref=item["source_key"],
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise CrmRefused(422, f"{where}: {first.get('msg', 'not valid')}.") from None

    def change(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        task = create_task(
            TaskActor(
                user=ctx.user,
                kind="user",
                channel=ctx.channel,
                suggestion_id=UUID(item["id"]),
            ),
            payload,
        )
        return {"task_id": task["id"]}, {"task": task}

    return change


def approve(ctx: Any, item_id: str, body: ReviewApproveIn) -> dict[str, Any]:
    """Contracts §2.2 `POST /crm/review/{id}/approve` — `{item, result}`.

    Approve, or Edit and approve with `edits`. A recorded value that changed
    since is refused 409 (per field, FR-CRMAI-REV-04); the manual path's own
    refusals come back as they are, and the item stays open showing it."""
    item = _load(ctx, item_id)
    target = _open_and_live(ctx, item)
    kind = item["type"]
    if kind == "next_step_change":
        change = _next_step(ctx, item, body)
    elif kind == "enrichment_conflict":
        change = _enrichment(ctx, item, body)
    elif kind == "deal_link":
        change = _deal_link(ctx, item, body)
    elif kind in rules.TASK_SOURCES:
        change = _task(ctx, item, body, target)
    else:
        raise CrmRefused(422, rules.NOT_YET)

    who = actor(ctx)
    store.review_claim(who, item_id, "approved")
    approver = dataclasses.replace(ctx, suggestion_id=item_id)
    try:
        result, shown = change(approver)
    except CrmRefused as refused:
        store.review_release(who, item_id, refused.detail, None)
        raise
    store.review_release(who, item_id, None, result)
    after = store.get_review_item(ctx.workspace_id, item_id) or item
    return {"item": present(ctx.workspace_id, [after])[0], "result": shown}


def dismiss(ctx: Any, item_id: str) -> dict[str, Any]:
    """Contracts §2.2 `POST /crm/review/{id}/dismiss` — `{item}`. A dismissed
    item is not made again from the same source (FR-CRMAI-REV-08)."""
    item = _load(ctx, item_id)
    _open_and_live(ctx, item)
    store.review_claim(actor(ctx), item_id, "dismissed")
    after = store.get_review_item(ctx.workspace_id, item_id) or item
    return {"item": present(ctx.workspace_id, [after])[0]}


# ---------------------------------------------------------------------------
# Expiry and tombstones — FR-CRMAI-REV-07, Q-34
# ---------------------------------------------------------------------------


def expire(workspace_id: str, now: datetime) -> dict[str, int]:
    """The daily `review_expiry` run of one workspace, as the system: items
    untouched for 14 days expire (each audited), and items decided 90 days
    ago become dedupe tombstones."""
    system = _system(workspace_id)
    return {
        "expired": store.review_expire(system, now - timedelta(days=rules.EXPIRY_DAYS)),
        "tombstoned": store.review_tombstone(system, now - timedelta(days=rules.TOMBSTONE_DAYS)),
    }


# ---------------------------------------------------------------------------
# The workspace export — FR-CRMAI-CHG-11, data-model §4
# ---------------------------------------------------------------------------

#: The columns an exported item carries: ids, never a name.
EXPORT_COLUMNS = (
    "id",
    "type",
    "status",
    "owner_id",
    "record_type",
    "record_id",
    "member_id",
    "suggestion",
    "current_values",
    "source_kind",
    "source_key",
    "source_version",
    "source_link",
    "dedupe_key",
    "model",
    "generated_at",
    "refusal",
    "created_at",
    "decided_at",
    "decided_by",
    "result",
    "tombstoned_at",
)


def export_rows(workspace_id: str) -> list[dict[str, Any]]:
    """Every item of the workspace, tombstones included, with its deal
    choices (ids only); an item whose target is in the Trash is left out."""
    rows = _untrashed(workspace_id, store.list_review_items(workspace_id, status="all"))
    choices: dict[str, list[str]] = {}
    for row in store.list_review_item_deals(workspace_id, [r["id"] for r in rows]):
        choices.setdefault(row["item_id"], []).append(row["opportunity_id"])
    return [
        {
            **{c: r.get(c) for c in EXPORT_COLUMNS},
            "deal_choices": sorted(choices.get(r["id"], [])),
        }
        for r in rows
    ]
