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
- `task_from_*`: `008`'s `create_task`, with only the fields of plan §5.4;
- `rule_suggestion`: R-3 `records.update` of the lead stage or next touch,
  R-4 `campaigns.convert` or `campaigns.update_member`, R-1 nothing (the
  draft is marked used);
- `deal_signal` (spec 010 FR-CAP-REV-02): `deals.create` at Discovery, or
  `deals.move` of the chosen deal, as the deal routes do, so Proposal's
  fields, Labs' tentative project and Closed Won's handoff all apply;
- `new_lead` (spec 010 FR-CAP-REV-02): `records.create` of the company (at
  New, then `records.update` to the suggested lead stage) and of the person,
  as the record routes do; then `crm.capture_thread` files the conversation
  on `worker-crm-sync`. A `new_lead` may have no target (it matched no
  company); it is live while it is open. Dismissing one may add its sender
  or its domain to the ignore list (FR-CAP-REV-03).

So every validation, permission check, pipeline rule, audit entry and
optimistic lock of the manual path applies (FR-CRMAI-REV-04). The change is
made with a copy of the caller's context carrying the item's id, so its audit
row says it was an approved suggestion (FR-CRMAI-REV-05); a task's own audit
rows carry it through `TaskActor.suggestion_id`.

An approval first claims the item (`crm_review_claim`: only one claim wins),
then makes the change. If the manual path refuses, the item opens again and
shows the refusal (`crm_review_release`), and the refusal is the answer. If
the change fails any other way, the item opens again too, showing a fixed
sentence, and the failure is raised.

Creating or refreshing an item sends no bell and no message
(FR-CRMAI-REV-10): Today's To review section and the daily digest carry
them. An item whose target is in the Trash is left out of every list and of
Today, and cannot be decided (FR-CRMAI-CHG-11); restoring the record shows it
again.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.domain.crm import ai_rules, dedupe, email_rules
from app.domain.crm import capture as capture_rules
from app.domain.crm import review as rules
from app.domain.crm import today as today_rules
from app.domain.crm.invites import PUBLIC_WEBMAIL_DOMAINS
from app.domain.tasks.service import TaskActor, create_task
from app.schemas.crm import (
    CampaignMemberUpdate,
    ConvertIn,
    MoveChanges,
    ReviewApproveIn,
    ReviewDismissIn,
    ReviewItemOut,
    StageMoveIn,
)
from app.schemas.tasks import TaskCreate
from app.services import supabase as db
from app.services.crm import CrmRefused, campaigns, deals, store
from app.services.crm import records as base
from app.services.crm.context import actor

logger = logging.getLogger("nldw-task-master")

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
    # Spec 009 FR-CRMAI-RULE-07 (contracts §2.2.1): per option; an R-1
    # draft is not sent back.
    "rule_suggestion": frozenset({"to_stage", "next_touch_on", "new_opportunity"}),
    # Spec 010 plan §5.9 (contracts §1.3): what the stage asks for.
    "deal_signal": frozenset(
        {
            "name",
            "next_step",
            "next_step_on",
            "amount",
            "currency",
            "expected_close_on",
            "proposal_sent_on",
            "point_of_contact_id",
            "project_name",
        }
    ),
    "new_lead": frozenset(
        {
            "company_name",
            "domain",
            "lead_stage",
            "first_name",
            "last_name",
            "email",
            "title",
            "link_company_id",
        }
    ),
}
#: A rule suggestion's refusals before the manual path's own.
CHOOSE_OPTION = "Choose what to do with this suggestion."
NEXT_TOUCH_DATE = "Choose the date of the next touch."
#: Spec 010 contracts §1.3 — a deal signal for a deal already there.
AT_OR_PAST = "This deal is already at or past that stage."
#: How far along each stage is; a closed deal is past every open one.
STAGE_RANK = {
    "discovery": 0,
    "qualification": 1,
    "proposal": 2,
    "negotiation": 3,
    "closed_won": 4,
    "closed_lost": 4,
}
#: The fields a deal signal's move takes from the suggestion and the edits.
MOVE_FIELDS = (
    "amount",
    "currency",
    "expected_close_on",
    "proposal_sent_on",
    "point_of_contact_id",
    "next_step",
    "next_step_on",
)


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


def capture_group_upsert(
    workspace_id: str, item: dict[str, Any], entry: dict[str, Any]
) -> dict[str, Any]:
    """Spec 010 R1 (FR-CAP-GRP-02, plan R1.2) — one capture item (`new_lead`
    or `deal_signal`) stored per company key through 074's
    `crm_capture_group_upsert`, money masked first in every suggestion
    string and in every conversation's reason (FR-CRMAI-LLM-05). The job,
    an applied dry run, the sync's deal documents and the Academy step all
    store through here, never the store directly. Returns `{id, created,
    skipped, released}`. Sends nothing."""
    masked = {**item, "suggestion": _masked(item.get("suggestion") or {})}
    link = item.get("source_link") or {}
    if isinstance(link.get("threads"), list):
        masked["source_link"] = {
            **link,
            "threads": [{**e, "reason": _masked(e.get("reason"))} for e in link["threads"]],
        }
    given = {**entry, "reason": _masked(entry.get("reason"))}
    return store.capture_group_upsert(workspace_id, masked, given)


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
    """FR-CRMAI-CHG-11 — an item whose target is in the Trash is left out. A
    spec 010 `new_lead` with no target is kept."""
    targets = _targets(ws_id, items)
    return [
        i
        for i in items
        if (i["record_id"] is None and i["type"] == "new_lead")
        or ((row := targets.get((i["record_type"], i["record_id"]))) and not row.get("deleted_at"))
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
        merged = (item.get("result") or {}).get("superseded_by")
        out.append(
            ReviewItemOut(
                id=item["id"],
                type=item["type"],
                type_label=rules.TYPES[item["type"]],
                status=item["status"],
                version=item.get("revision") or 1,
                owner={"id": owner, "name": names.get(owner)} if owner else None,
                target={
                    "record_type": item["record_type"],
                    "record_id": item["record_id"],
                    "name": _name(item["record_type"], target),
                }
                if item["record_id"]
                else None,
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
                result={"superseded_by": merged} if merged else None,
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
            "title": _title(i),
            "detail": _detail(i, targets.get((i["record_type"], i["record_id"]))),
        }
        for i in items
    ]
    return [today_rules.to_review(rows)]


def _title(item: dict[str, Any]) -> str:
    """What To review calls an item: its type, or for a deal signal what it
    would do (spec 010 contracts §6.2)."""
    if item["type"] == "deal_signal":
        return capture_rules.deal_signal_title(item.get("suggestion") or {})
    if item["type"] == "new_lead":
        return capture_rules.new_lead_title(item.get("suggestion") or {})
    return rules.TYPES[item["type"]]


def _detail(item: dict[str, Any], target: dict[str, Any] | None) -> str:
    """The target's name; a deal signal adds its reason (contracts §6.2); a
    new lead says how many emails and why (contracts §6.1)."""
    if item["type"] == "new_lead":
        return capture_rules.new_lead_detail(item.get("suggestion") or {})
    name = _name(item["record_type"], target) or ""
    if item["type"] == "deal_signal":
        reason = ((item.get("suggestion") or {}).get("evidence") or {}).get("reason")
        return " · ".join(p for p in (name, reason) if p)
    return name


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
    if item["record_id"] is None and item["type"] == "new_lead":
        return {}
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


def _option(item: dict[str, Any], body: ReviewApproveIn) -> str:
    """The option chosen, or the only one; a choice it does not offer is
    refused (contracts §2.2)."""
    options = (item.get("suggestion") or {}).get("options") or []
    chosen = body.option or (options[0] if len(options) == 1 else None)
    if chosen not in options:
        raise CrmRefused(422, CHOOSE_OPTION)
    return chosen


def _rule(ctx: Any, item: dict[str, Any], body: ReviewApproveIn) -> Change:
    """Spec 009 FR-CRMAI-RULE-07 — a rule suggestion goes through the manual
    path, so pipeline rules apply: R-3 the company's lead stage or next
    touch (`records.update`), R-4 the member's conversion or close-out
    (`campaigns`), R-1 only marks the draft used. Staleness is the recorded
    lead stage or member status (FR-CRMAI-REV-04)."""
    edits = _edits(item, body)
    suggestion = item.get("suggestion") or {}
    option = _option(item, body)
    recorded = item.get("current_values") or {}
    rule = suggestion.get("rule")
    if rule == "R-1":
        if edits:
            raise CrmRefused(422, f"{sorted(edits)[0]} cannot be edited on this suggestion.")
        return lambda approver: ({"used": True}, {})
    if rule == "R-3":
        if option == "next_touch":
            if not edits.get("next_touch_on"):
                raise CrmRefused(422, NEXT_TOUCH_DATE)
            field, value = "next_touch_on", edits["next_touch_on"]
        else:
            field, value = "lead_stage", edits.get("to_stage", option)
            if value == "next_touch" or value not in suggestion.get("options", []):
                raise CrmRefused(422, CHOOSE_OPTION)
        was = {"lead_stage": recorded.get("lead_stage")}

        def move(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            row = base.live(ctx.workspace_id, "company", item["record_id"])
            if rules.stale_fields(was, row):
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

        return move
    if rule == "R-4" and item.get("member_id"):
        member_id = item["member_id"]
        was = {"status": recorded.get("member_status")}
        new = edits.get("new_opportunity")
        if option == "close_out" and new is not None:
            raise CrmRefused(422, "new_opportunity cannot be edited on this suggestion.")
        # Checked before the item is claimed, so a malformed edit leaves it open.
        try:
            conversion = ConvertIn(new=new or {})
        except ValidationError as exc:
            first = exc.errors()[0]
            raise CrmRefused(422, f"new_opportunity: {first.get('msg', 'not valid')}.") from None

        def follow_through(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            row = campaigns._member(approver, member_id)
            if rules.stale_fields(was, row):
                raise CrmRefused(409, rules.CHANGED)
            try:
                if option == "qualify":
                    done = campaigns.convert(approver, member_id, conversion)
                    return (
                        {"member_id": member_id, "opportunity_id": done["opportunity"]["id"]},
                        {"member": done["member"], "opportunity": done["opportunity"]},
                    )
                shown = campaigns.update_member(
                    approver,
                    member_id,
                    CampaignMemberUpdate(
                        status="not_interested", expect_updated_at=row["updated_at"]
                    ),
                )
            except CrmRefused as refused:
                raise _locked(refused) from None
            return {"member_id": member_id}, {"member": shown}

        return follow_through
    raise CrmRefused(422, rules.NOT_YET)


def _when(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _deal_signal(
    ctx: Any, item: dict[str, Any], body: ReviewApproveIn, target: dict[str, Any]
) -> Change:
    """Spec 010 FR-CAP-REV-02 (plan §5.9) — a deal at Discovery on the
    company (`deals.create`: the company must be Qualified or later, with a
    next step and date), or the chosen open deal moved to the suggested
    stage (`deals.move`: Proposal asks for its fields, which come from the
    edits; in Labs it makes the tentative project, and Closed Won is refused
    with the handoff's sentence). A deal already at or past the stage is
    refused before anything is claimed."""
    edits = _edits(item, body)
    suggestion = item.get("suggestion") or {}
    stage = suggestion.get("stage")
    if suggestion.get("action") == "create":
        values = {
            "company_id": item["record_id"],
            "name": edits.get("name") or suggestion.get("name") or _name("company", target),
            "next_step": edits.get("next_step", suggestion.get("next_step")),
            "next_step_on": edits.get("next_step_on", suggestion.get("next_step_on")),
        }
        if item.get("owner_id"):
            values["owner_id"] = item["owner_id"]

        def create(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            shown = deals.create(approver, {k: v for k, v in values.items() if v is not None})
            return {"opportunity_id": shown["id"]}, {"opportunity": shown}

        return create

    if stage not in STAGE_RANK:
        raise CrmRefused(422, rules.NOT_YET)
    deal_id = _deal(ctx, item, body)
    row = base.live(ctx.workspace_id, "opportunity", deal_id)
    if STAGE_RANK.get(row["stage"], 0) >= STAGE_RANK[stage]:
        raise CrmRefused(422, AT_OR_PAST)
    given = {f: edits.get(f, suggestion.get(f)) for f in MOVE_FIELDS}
    try:
        planned = StageMoveIn(
            stage=stage,
            changes=MoveChanges(**{k: v for k, v in given.items() if v is not None}),
            project_name=edits.get("project_name"),
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise CrmRefused(422, f"{where}: {first.get('msg', 'not valid')}.") from None

    def move(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        now = base.live(ctx.workspace_id, "opportunity", deal_id)
        try:
            shown = deals.move(
                approver,
                deal_id,
                # model_copy does not validate: the time goes in parsed.
                planned.model_copy(update={"expect_updated_at": _when(now["updated_at"])}),
            )
        except CrmRefused as refused:
            raise _locked(refused) from None
        result: dict[str, Any] = {"opportunity_id": deal_id, "stage": stage}
        if (shown.get("project") or {}).get("id"):
            result["project_id"] = shown["project"]["id"]
        return result, shown

    return move


def _queue_filing(workspace_id: str, link: dict[str, Any]) -> None:
    """`crm.capture_thread` on `worker-crm-sync` (plan §5.9 step 4): ids only.
    A failure to queue is logged; the conversation is filed when its next
    message arrives (PD-10)."""
    if not (link.get("mailbox_id") and link.get("thread_id")):
        return
    try:
        from app.tasks.crm import capture_thread
        from app.worker import CRM_SYNC_QUEUE

        capture_thread.apply_async(
            args=[workspace_id, link["mailbox_id"], link["thread_id"]], queue=CRM_SYNC_QUEUE
        )
    except Exception as exc:  # noqa: BLE001 - the records exist; filing waits
        logger.warning('{"event": "crm_capture_file_failed", "error": "%s"}', type(exc).__name__)


def _new_lead(ctx: Any, item: dict[str, Any], body: ReviewApproveIn) -> Change:
    """Spec 010 FR-CAP-REV-02 (plan §5.9) — as the approver, through the
    record routes' services: (1) the company: the one chosen
    (`link_company_id`) or matched (the target), the one an earlier
    approval of this item made, the one holding the domain, or a new one at
    New owned by the mailbox owner; (2) the suggested lead stage, from New;
    (3) the person, unless one has the address; (4) the conversation queued
    for filing. A refusal or a fault after the company exists keeps its id
    on the item, so a retry does not make a second company of that name.

    Spec 010 R1 (FR-CAP-GRP-04, PD-35): an item grouping several
    conversations makes (or reuses) one person per distinct sender address
    among them, the latest sender as the suggestion (and the edits) say,
    the others with their own names (an edited address replaces the newest
    conversation's, which makes no second person); an address already a
    person of no company is linked to this company, one under another
    company is left there; then every conversation is queued for
    filing, one job each. `threads_filed` counts the conversations whose
    sender is a person of the workspace after this step (an edited-away
    address is not one); an item made
    before 074 files its one link as before.

    Spec 010 R1 FR-CAP-ACAD-05 (PD-31): an Academy item
    (`evidence.origin == "academy_routing"`) makes the company and the
    people the same way, then records its conversations as one note
    (money masked) linked to the company and every person made or linked,
    instead of filing: the Academy has no mailbox. A fault on the note
    refuses the approval with the company kept for the retry."""
    edits = _edits(item, body)
    suggestion = item.get("suggestion") or {}
    stage = edits.get("lead_stage", suggestion.get("lead_stage") or "new")
    if stage not in capture_rules.LEAD_STAGES:
        raise CrmRefused(422, capture_rules.CHOOSE_LEAD_STAGE)
    ws_id = ctx.workspace_id
    chosen = edits.get("link_company_id") or (
        item["record_id"] if item["record_type"] == "company" else None
    )
    if chosen:
        base.live(ws_id, "company", str(chosen))
    name = edits.get("company_name", suggestion.get("company_name"))
    domain = edits.get("domain", suggestion.get("domain"))
    email = edits.get("email", suggestion.get("email"))
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    first = edits.get("first_name", suggestion.get("first_name"))
    last = edits.get("last_name", suggestion.get("last_name"))
    title = edits.get("title", suggestion.get("title"))
    entries = capture_rules.entries_of(item)
    evidence = suggestion.get("evidence") or {}
    academy = evidence.get("origin") == capture_rules.ACADEMY_ROUTING
    # Plan R1.2: the newest conversation's sender is the suggestion's
    # person, so an edited address replaces that entry's address.
    newest = max(entries, key=capture_rules.entry_order) if entries else None

    def earlier() -> str | None:
        made = (item.get("result") or {}).get("company_id")
        row = base.by_id(ws_id, "crm_companies", [made]).get(made) if made else None
        return made if row and not row.get("deleted_at") else None

    def holding_domain() -> str | None:
        if not domain:
            return None
        normal = dedupe.normalise_domain(str(domain))
        for d in store.list_domains(ws_id, domains=[normal] if normal else []):
            row = base.by_id(ws_id, "crm_companies", [d["company_id"]]).get(d["company_id"])
            if row and not row.get("deleted_at"):
                return d["company_id"]
        return None

    def sender(entry: dict[str, Any]) -> str | None:
        """An entry's sender; a pre-074 entry without one is the suggestion's."""
        given = entry.get("person_email")
        given = given.strip().lower() if isinstance(given, str) and given.strip() else None
        return given or email

    def person_for(
        approver: Any,
        company_id: str,
        address: str | None,
        names: tuple[Any, Any],
        job_title: Any,
    ) -> str:
        held = store.people_by_emails(ws_id, [address]) if address else []
        if held:
            # FR-CAP-GRP-04: a person of no company is linked to this one,
            # as the approver; one under another company stays there.
            if not held[0].get("company_id") and not held[0].get("deleted_at"):
                base.update(approver, "person", held[0]["id"], {"company_id": company_id})
            return held[0]["id"]
        person: dict[str, Any] = {"first_name": names[0], "company_id": company_id}
        for key, value in (("last_name", names[1]), ("primary_email", address)):
            if value:
                person[key] = value
        if job_title:
            person["job_title"] = job_title
        return base.create(approver, "person", person, check_duplicates=False)["id"]

    def change(approver: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        made_before = earlier()
        existing = chosen or (None if made_before else holding_domain())
        company_id = existing or made_before
        # Linked: an existing company was used, not one this item made.
        linked = existing is not None
        if company_id is None:
            values: dict[str, Any] = {"name": name}
            if item.get("owner_id"):
                values["owner_id"] = item["owner_id"]
            if domain:
                values["domain"] = domain
            company_id = base.create(approver, "company", values, check_duplicates=False)["id"]
        try:
            row = base.live(ws_id, "company", company_id)
            if stage != "new" and row.get("lead_stage") == "new":
                base.update(
                    approver,
                    "company",
                    company_id,
                    {"lead_stage": stage, "expect_updated_at": row["updated_at"]},
                )
            person_id = person_for(approver, company_id, email, (first, last), title)
            person_ids = [person_id]
            # Two sets (FR-CAP-GRP-04): `known` is the addresses that make
            # no new person; `held` the addresses a person holds after
            # this step, which alone count a conversation as filed.
            held = {email} if email else set()
            known = set(held)
            if email and newest is not None and sender(newest):
                # Its original address, when the approver edited it, makes
                # no second person, but nobody holds it: not counted filed.
                known.add(str(sender(newest)))
            people = 1
            # FR-CAP-GRP-04: every other sender among the conversations.
            for entry in entries:
                address = sender(entry)
                if entry is newest or not address or address in known:
                    continue
                default_first, default_last = capture_rules.split_name(None, address)
                other = person_for(
                    approver,
                    company_id,
                    address,
                    (entry.get("first_name") or default_first, entry.get("last_name")),
                    None,
                )
                if other not in person_ids:
                    person_ids.append(other)
                known.add(address)
                held.add(address)
                people += 1
            note_id = (
                _academy_note(approver, company_id, person_ids, entries, evidence)
                if academy
                else None
            )
        except Exception:  # noqa: BLE001 - a refusal or a fault alike: re-raised
            # Kept on the item while it is claimed, before it opens again
            # (approve's handler then opens it with the right sentence).
            store.review_release(actor(ctx), item["id"], None, {"company_id": company_id})
            raise
        if academy:
            result = {
                "company_id": company_id,
                "person_id": person_id,
                "people": people,
                "note_id": note_id,
                "linked": linked,
                "threads_filed": 0,
            }
            return result, dict(result)
        filed = 0
        links = [e for e in entries if e.get("mailbox_id") and e.get("thread_id")]
        if links:
            for entry in links:
                _queue_filing(ws_id, entry)
                if _known_after(ws_id, company_id, held, sender(entry)):
                    filed += 1
        else:
            link = item.get("source_link") or {}
            _queue_filing(ws_id, link)
            if link.get("mailbox_id") and link.get("thread_id") and email:
                filed = 1
        result = {
            "company_id": company_id,
            "person_id": person_id,
            "people": people,
            "linked": linked,
            "filed": "queued",
            "threads_filed": filed,
        }
        return result, dict(result)

    return change


def _known_after(ws_id: str, company_id: str, held: set[str], address: str | None) -> bool:
    """FR-CAP-GRP-04: whether a conversation's sender is known after the
    approval, so its filing stores something: a person holds the address,
    or (not webmail) the company holds its domain, which is how the sync
    matches a sender with no person (`email_rules.links_for`)."""
    if not address:
        return False
    if address in held:
        return True
    domain = dedupe.normalise_domain(email_rules.domain_of(address))
    if not domain or domain in PUBLIC_WEBMAIL_DOMAINS:
        return False
    return bool(store.list_domains(ws_id, company_ids=[company_id], domains=[domain]))


def _academy_note(
    approver: Any,
    company_id: str,
    person_ids: list[str],
    entries: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> str:
    """FR-CAP-ACAD-05 — the one note an Academy approval records, linked to
    the company and every person (`records._targets`' `{record_type,
    record_id}` links, at most 20), dated on the latest conversation, its
    body masked (Academy Viewers read notes; FR-CRMAI-LLM-05)."""
    body = _masked(capture_rules.academy_note(entries, str(evidence.get("mailbox_email") or "")))
    links = [{"record_type": "company", "record_id": company_id}]
    links += [{"record_type": "person", "record_id": p} for p in person_ids]
    values: dict[str, Any] = {"kind": "note", "body": body, "links": links[:20]}
    latest = max((str(e.get("latest_at") or "") for e in entries), default="")
    if latest[:10]:
        values["occurred_on"] = latest[:10]
    return base.create(approver, "note", values, check_duplicates=False)["id"]


def approve(ctx: Any, item_id: str, body: ReviewApproveIn) -> dict[str, Any]:
    """Contracts §2.2 `POST /crm/review/{id}/approve` — `{item, result}`.

    Approve, or Edit and approve with `edits`. A recorded value that changed
    since is refused 409 (per field, FR-CRMAI-REV-04); the manual path's own
    refusals come back as they are, and the item stays open showing it."""
    item = _load(ctx, item_id)
    target = _open_and_live(ctx, item)
    if (item.get("revision") or 1) != body.version:
        # Refreshed since it was shown: the person approves what they saw only.
        raise CrmRefused(409, rules.SUGGESTION_CHANGED)
    kind = item["type"]
    if kind == "next_step_change":
        change = _next_step(ctx, item, body)
    elif kind == "enrichment_conflict":
        change = _enrichment(ctx, item, body)
    elif kind == "deal_link":
        change = _deal_link(ctx, item, body)
    elif kind in rules.TASK_SOURCES:
        change = _task(ctx, item, body, target)
    elif kind == "rule_suggestion":
        change = _rule(ctx, item, body)
    elif kind == "deal_signal":
        change = _deal_signal(ctx, item, body, target)
    elif kind == "new_lead":
        change = _new_lead(ctx, item, body)
    else:
        raise CrmRefused(422, rules.NOT_YET)

    who = actor(ctx)
    # The claim checks the version again, so a refresh in between is refused too.
    store.review_claim(who, item_id, "approved", revision=body.version)
    approver = dataclasses.replace(ctx, suggestion_id=item_id)
    try:
        result, shown = change(approver)
    except CrmRefused as refused:
        store.review_release(who, item_id, refused.detail, None)
        raise
    except Exception:
        # Not a refusal (a timeout, a database fault): the item must not stay
        # claimed with nothing changed, so it opens again with a fixed
        # sentence (never the exception's text), and the fault goes on.
        store.review_release(who, item_id, rules.NOT_MADE, None)
        raise
    store.review_release(who, item_id, None, result)
    after = store.get_review_item(ctx.workspace_id, item_id) or item
    return {"item": present(ctx.workspace_id, [after])[0], "result": shown}


def _ignore_for(ctx: Any, item: dict[str, Any], ignore: str) -> tuple[str, str]:
    """Spec 010 FR-CAP-REV-03 — `(kind, value)` a dismissal adds: the new
    lead's sender, or that sender's whole domain (never a public webmail
    domain, PD-6)."""
    if item["type"] != "new_lead":
        raise CrmRefused(422, capture_rules.ONLY_NEW_LEAD)
    address = capture_rules.ignore_value((item.get("suggestion") or {}).get("email"))
    value = address if ignore == "address" else email_rules.domain_of(address)
    why = capture_rules.ignore_refusal(ignore, value, ctx.workspace.get("allowed_domains") or [])
    if why:
        raise CrmRefused(422, why)
    return ignore, value


def dismiss(ctx: Any, item_id: str, body: ReviewDismissIn | None = None) -> dict[str, Any]:
    """Contracts §2.2 `POST /crm/review/{id}/dismiss` — `{item}`. A dismissed
    item is not made again from the same source (FR-CRMAI-REV-08). Spec 010
    (contracts §1.3): a `new_lead` may add its sender or its domain to the
    ignore list, as the dismisser (audited), reusing an entry already
    there; the response always has `ignore` (null when none was added)."""
    from app.services.crm import capture

    item = _load(ctx, item_id)
    _open_and_live(ctx, item)
    wanted = _ignore_for(ctx, item, body.ignore) if body and body.ignore else None
    store.review_claim(actor(ctx), item_id, "dismissed")
    after = store.get_review_item(ctx.workspace_id, item_id) or item
    out: dict[str, Any] = {"item": present(ctx.workspace_id, [after])[0]}
    out["ignore"] = None
    if wanted is not None:
        kind, value = wanted
        row = capture.find_ignore(ctx.workspace_id, kind, value)
        if row is None:
            try:
                row = capture.write_ignore(ctx, kind, value, from_item_id=item_id)
            except CrmRefused:
                # Added by another dismissal meanwhile (crm_capture_ignores_once):
                # that entry is reused, not an error.
                row = capture.find_ignore(ctx.workspace_id, kind, value)
                if row is None:
                    raise
        out["ignore"] = {"id": row["id"], "kind": row["kind"], "value": row["value"]}
    return out


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
