"""The pipeline dashboard and bookings targets — spec 007 FR-CRM-DASH-01 to
-04, FR-CRM-LIST-06 (targets), FR-CRM-CAP-02, FR-CRM-ACC-11, FR-CRM-CHG-13;
contracts §2.10.

`dashboard` gathers what `domain/crm/dashboard.py` computes from: the
workspace's live deals and companies, its stage history, its flagged
records, the activity of the range and the targets. Admins see the whole
workspace; a Member sees their own numbers only (the owner filter is theirs,
whatever was asked); a Viewer has no dashboard (403), so no amount reaches
them (FR-CRM-ACC-11). In Labs the capacity check sits on the dashboard for
admins and staff members, never a CRM-only account (FR-CRM-CAP-02).

Targets are the Admin's (FR-CRM-LIST-06): `PUT` replaces the workspace's
whole set in one `crm_apply`, so every target added, changed or removed is
audited in the same transaction.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain.calendar import today_in_company_tz
from app.domain.crm import dashboard as rules
from app.domain.crm import objects
from app.schemas.crm import TargetIn
from app.services import capacity
from app.services.crm import CrmRefused, flags, store
from app.services.crm import present as show
from app.services.crm import records as base
from app.services.crm.context import actor

NO_DASHBOARD = "Viewers have no dashboard."
#: The capacity check's horizon on the dashboard (FR-CRM-CAP-01: 90 days).
CAPACITY_WEEKS = 13


def _filters(
    ctx: Any,
    ws: base.Workspace,
    *,
    entity: str | None,
    owner_id: str | None,
    channel_id: str | None,
    since: date | None,
    until: date | None,
) -> rules.Filters:
    if entity is not None and not ctx.workspace.get("has_entity"):
        raise CrmRefused(422, f"Entity is not used in {ctx.workspace['name']}.")
    if since and until and since > until:
        raise CrmRefused(422, "The range ends before it starts.")
    if channel_id is not None:
        option = ws.facts.options.get(channel_id)
        if option is None or option["list_key"] != "channel":
            raise CrmRefused(422, "That channel is not an option in this workspace.")
    if ctx.role == "member":
        # FR-CRM-DASH-01: a Member's dashboard is their own numbers.
        owner_id = ctx.user.id
    elif owner_id is not None and owner_id not in ws.members:
        raise CrmRefused(422, "The owner must be a member of this workspace.")
    return rules.Filters(
        entity=entity, owner_id=owner_id, channel_id=channel_id, since=since, until=until
    )


def _activity_window(f: rules.Filters, today: date) -> tuple[str, str]:
    """The timestamps the activity panel can need: its weeks, a day either
    side for the time zone."""
    since, until = f.since or rules.fy_start(today), f.until or today
    start = max(since, until - timedelta(weeks=rules.ACTIVITY_WEEKS)) - timedelta(days=8)
    return start.isoformat(), (until + timedelta(days=2)).isoformat()


def dashboard(
    ctx: Any,
    *,
    entity: str | None = None,
    owner_id: str | None = None,
    channel_id: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    """Contracts §2.10 `GET /crm/dashboard`."""
    if ctx.role not in ("admin", "member"):
        raise CrmRefused(403, NO_DASHBOARD)
    ws = base.load(ctx)
    f = _filters(
        ctx, ws, entity=entity, owner_id=owner_id, channel_id=channel_id, since=since, until=until
    )
    today = today_in_company_tz()
    ws_id = ctx.workspace_id
    deals = store.dashboard_deals(ws_id)
    window = _activity_window(f, today)
    owners = {m["id"]: m["owner_id"] for m in store.list_mailboxes(ws_id)}
    emails = [
        {**e, "owner_id": owners.get(e["mailbox_id"])} for e in store.emails_between(ws_id, *window)
    ]
    campaign_ids = {d["source_campaign_id"] for d in deals if d.get("source_campaign_id")}
    campaigns = base.by_id(ws_id, "crm_campaigns", campaign_ids) if campaign_ids else {}
    facts = rules.Facts(
        today=today,
        deals=deals,
        companies=store.dashboard_companies(ws_id),
        history=store.stage_history(ws_id),
        flagged={
            table: store.flag_rows(ws_id, table, flagged_only=True)
            for table in ("crm_companies", "crm_people", "crm_opportunities")
        },
        notes=store.notes_between(ws_id, *window),
        tasks=store.tasks_completed_between(ws_id, *window),
        emails=emails,
        targets=store.list_targets(ws_id),
        options={k: o["label"] for k, o in ws.facts.options.items()},
        names=ws.facts.names,
        campaigns={k: c["name"] for k, c in campaigns.items()},
        lead_stage_labels={
            o["system_key"]: o["label"]
            for o in ws.facts.options.values()
            if o["list_key"] == "company.lead_stage" and o.get("system_key")
        },
        stage_labels={
            o["system_key"]: o["label"]
            for o in ws.facts.options.values()
            if o["list_key"] == "opportunity.stage" and o.get("system_key")
        },
        members=sorted(ws.members),
        # Spec 009 FR-CRMAI-RULE-03: Stuck in stage counted while R-2 is on.
        stuck_in_stage=flags.stuck_on(ws_id),
    )
    # Deal flag rows carry no entity or channel; the panel filters by them.
    by_id = {d["id"]: d for d in deals}
    facts.flagged["crm_opportunities"] = [
        {**by_id.get(r["id"], {}), **r} for r in facts.flagged["crm_opportunities"]
    ]
    company_facts = {c["id"]: c for c in facts.companies}
    facts.flagged["crm_companies"] = [
        {**company_facts.get(r["id"], {}), **r} for r in facts.flagged["crm_companies"]
    ]
    linked = bool(ctx.workspace.get("tva_linked"))
    panels = rules.build(
        facts, f, has_entity=bool(ctx.workspace.get("has_entity")), academy=not linked
    )
    answer: dict[str, Any] = {
        "workspace": {"key": ctx.workspace["key"], "name": ctx.workspace["name"]},
        "today": today.isoformat(),
        "filters": {
            "entity": f.entity,
            "owner_id": f.owner_id,
            "channel_id": f.channel_id,
            "from": f.since.isoformat() if f.since else None,
            "to": f.until.isoformat() if f.until else None,
        },
        "own_numbers_only": ctx.role == "member",
        **panels,
    }
    if linked and not ctx.user.is_crm_only:
        # FR-CRM-CAP-02: admins and staff members (a Viewer never gets here).
        monday = today - timedelta(days=today.weekday())
        weeks = capacity.weekly(today, monday + timedelta(days=7 * CAPACITY_WEEKS - 1))
        answer["capacity"] = {"weeks": rules.capacity_weeks(weeks)}
    return answer


# ---------------------------------------------------------------------------
# Bookings targets — FR-CRM-LIST-06, contracts §2.10 `/crm/targets`
# ---------------------------------------------------------------------------


def _present_target(row: dict[str, Any]) -> dict[str, Any]:
    entity = row.get("entity")
    return {
        "id": row["id"],
        "entity": entity,
        "entity_label": objects.ENTITIES.get(entity or "") if entity else None,
        "period_kind": row["period_kind"],
        "period_start": str(row["period_start"])[:10],
        "period_end": rules.period_end(
            row["period_kind"], date.fromisoformat(str(row["period_start"])[:10])
        ).isoformat(),
        "amount_inr": show.money(row["amount_inr"]),
        "updated_at": row.get("updated_at"),
    }


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kinds = {k: i for i, k in enumerate(rules.PERIOD_KINDS)}
    return sorted(
        rows,
        key=lambda t: (
            -date.fromisoformat(t["period_start"]).toordinal(),
            kinds[t["period_kind"]],
            t["entity"] or "",
        ),
    )


def list_targets(ctx: Any) -> list[dict[str, Any]]:
    return _ordered([_present_target(t) for t in store.list_targets(ctx.workspace_id)])


def put_targets(ctx: Any, wanted: list[TargetIn]) -> list[dict[str, Any]]:
    """The workspace's whole set of targets: those sent are kept, added or
    changed; any other is removed. One transaction, every change audited."""
    seen: set[tuple[str, str, str]] = set()
    for t in wanted:
        why = rules.target_refusal(
            t.period_kind,
            t.period_start,
            t.entity,
            has_entity=bool(ctx.workspace.get("has_entity")),
            workspace=ctx.workspace["name"],
        )
        if why:
            raise CrmRefused(422, why)
        key = (t.entity or "", t.period_kind, t.period_start.isoformat())
        if key in seen:
            raise CrmRefused(422, "Each period has one target per entity; this list repeats one.")
        seen.add(key)
    current = {
        (r.get("entity") or "", r["period_kind"], str(r["period_start"])[:10]): r
        for r in store.list_targets(ctx.workspace_id)
    }
    ops: list[dict[str, Any]] = [
        {"op": "delete", "table": "crm_bookings_targets", "id": row["id"]}
        for key, row in current.items()
        if key not in seen
    ]
    for t in wanted:
        key = (t.entity or "", t.period_kind, t.period_start.isoformat())
        row = current.get(key)
        if row is None:
            ops.append(
                {
                    "op": "insert",
                    "table": "crm_bookings_targets",
                    "values": {
                        "entity": t.entity,
                        "period_kind": t.period_kind,
                        "period_start": t.period_start.isoformat(),
                        "amount_inr": str(t.amount_inr),
                    },
                }
            )
        elif Decimal(str(row["amount_inr"])) != t.amount_inr:
            ops.append(
                {
                    "op": "update",
                    "table": "crm_bookings_targets",
                    "id": row["id"],
                    "values": {"amount_inr": str(t.amount_inr)},
                }
            )
    if ops:
        store.apply(actor(ctx), ops)
    return list_targets(ctx)
