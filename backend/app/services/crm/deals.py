"""Deals — spec 007 FR-CRM-OPP-01 to -03, FR-CRM-FX-01, -03 to -05,
FR-CRM-PIPE-02 to -15, FR-CRM-TVA-03 to -18, FR-CRM-AUTO-01 (automations 9
and 10); contracts §2.5, §2.6; plan §5.3, §5.6.

Create, edit, preview and move a deal, hand a won Labs deal off to TVA, link
a deal to a TVA project, and a deal's contacts. The rules are decided in
`domain/crm/pipeline.py`, `domain/crm/fx.py` and `domain/crm/handoff.py`;
this module gathers the rows they need and writes the result in one database
call: `crm_apply` for a create or an edit (the deal, its contacts and its
company's move to Deal open together), `crm_deal_transition` for a stage
move and every side effect it has, CRM and TVA (FR-CRM-PIPE-15), and
`crm_link_project` for Link TVA project.

From 1b every write here recomputes the deal's flags and its company's
(FR-CRM-FLAG-02), and tells the people it concerns: a new owner, the owner
of a deal someone else moved, whoever a handoff left a task (FR-CRM-NOTIF-01).
Neither ever fails the write.

A deal's stage never changes through an edit: only a move changes it, and
`crm_apply` refuses it too. In Labs (FR-CRM-WS-05), a move also carries its
project action (`services/crm/tva.py`), a deal edit that changes probability
goes through the transition so the tentative project follows it
(FR-CRM-TVA-06), and Closed Won goes through the handoff (FR-CRM-TVA-07).
"""

from __future__ import annotations

from typing import Any

from app.domain.calendar import today_in_company_tz
from app.domain.crm import fx, handoff, pipeline
from app.schemas.crm import (
    ContactIn,
    HandoffIn,
    LinkProjectIn,
    StageMoveIn,
    StagePreviewIn,
)
from app.services import supabase as db
from app.services.crm import CrmRefused, flags, notifications, store, tva
from app.services.crm import present as show
from app.services.crm import records as base
from app.services.crm.context import actor

#: Contracts §2.6 — these change only through a move.
MOVE_ONLY = "A deal's stage and lost reason change through a stage move."


def _probabilities(ws_id: str) -> dict[str, Any]:
    found = next((s for s in store.list_settings(ws_id) if s["key"] == "stage_probabilities"), None)
    return found["value"] if found else {}


def _money(ws: base.Workspace, values: dict[str, Any], existing: dict[str, Any] | None) -> None:
    """FR-CRM-FX-03, in place: an amount or currency sent is stored with its
    INR value at the rate for this month, or the latest earlier one. Clearing
    the amount clears its currency and conversion with it."""
    if "amount" not in values and "currency" not in values:
        return
    if "amount" in values and values["amount"] is None:
        if values.get("currency"):
            raise CrmRefused(422, "A currency needs an amount.")
        values.update(fx.CLEARED)
        return
    amount = values["amount"] if "amount" in values else (existing or {}).get("amount")
    currency = values["currency"] if "currency" in values else (existing or {}).get("currency")
    if amount is None:
        raise CrmRefused(422, "A currency needs an amount.")
    if currency is None:
        raise CrmRefused(422, "An amount needs a currency.")
    result = fx.converted(
        store.list_fx_rates(ws.id),
        amount,
        currency,
        today_in_company_tz(),
        ws.ctx.workspace["name"],
    )
    if isinstance(result, str):
        raise CrmRefused(422, result)
    values.update(result)


def _person(ws: base.Workspace, person_id: str | None) -> None:
    if person_id:
        base.live(ws.id, "person", person_id)


def _option(ws: base.Workspace, option_id: str | None, list_key: str, what: str) -> None:
    if option_id is None:
        return
    option = ws.facts.options.get(option_id)
    if option is None or option["list_key"] != list_key:
        raise CrmRefused(422, f"That {what} is not an option in this workspace.")
    if not option["is_active"]:
        raise CrmRefused(422, f"“{option['label']}” is no longer offered for {what}.")


def _labelled(ws: base.Workspace, company: dict[str, Any]) -> dict[str, Any]:
    return {
        **company,
        "lead_stage_label": ws.facts.fixed_label("company.lead_stage", company["lead_stage"]),
    }


# ---------------------------------------------------------------------------
# Create and edit — contracts §2.5
# ---------------------------------------------------------------------------


def create(
    ctx: Any,
    payload: Any,
    *,
    source_campaign_id: str | None = None,
    then: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """FR-CRM-PIPE-04 — a deal opens on a company at Qualified or later, and a
    Qualified company moves to Deal open in the same call. The deal starts at
    Discovery with its next step and date (FR-CRM-PIPE-07, -10) and the
    stage's default probability unless one is given (FR-CRM-PIPE-11).

    1b (FR-CRM-CAMP-04): a campaign conversion passes the deal's source
    campaign, and `then`, the member's ops, which may name the new deal as
    `$ref:deal`: all of it is one transaction."""
    if isinstance(payload, dict) and {"stage", "lost_reason_id"} & set(payload):
        raise CrmRefused(422, MOVE_ONLY)
    ws = base.load(ctx)
    values = base.parse("opportunity", payload, creating=True)
    contacts = [ContactIn.model_validate(c) for c in values.pop("contacts", None) or []]
    if not values.get("owner_id"):
        values["owner_id"] = ctx.user.id
    base.check_fields(ws, "opportunity", values, None)

    company = base.live(ws.id, "company", values["company_id"])
    company_changes, why = pipeline.company_effect_on_open(_labelled(ws, company))
    if why:
        raise CrmRefused(422, why)
    _person(ws, values.get("point_of_contact_id"))
    seen: set[str] = set()
    for c in contacts:
        _person(ws, c.person_id)
        _option(ws, c.role_id, "opportunity.contact_role", "role in deal")
        if c.person_id in seen:
            raise CrmRefused(422, "A person is a contact on a deal once.")
        seen.add(c.person_id)

    missing = pipeline.stage_requirements("discovery", values, [])
    if missing:
        raise CrmRefused(422, pipeline.missing_sentence("discovery", missing))
    if values.get("probability") is None:
        values["probability"] = pipeline.default_probability("discovery", _probabilities(ws.id))
    _money(ws, values, None)

    if source_campaign_id:
        values["source_campaign_id"] = source_campaign_id
    ops: list[dict[str, Any]] = [
        {
            "op": "insert",
            "table": "crm_opportunities",
            "ref": "deal",
            "values": {**values, "stage": "discovery"},
        }
    ]
    ops += [
        {
            "op": "insert",
            "table": "crm_opportunity_contacts",
            "values": {
                "opportunity_id": "$ref:deal",
                "person_id": c.person_id,
                "role_id": c.role_id,
            },
        }
        for c in contacts
    ]
    if company_changes:
        ops.append(
            {
                "op": "update",
                "table": "crm_companies",
                "id": company["id"],
                "expect_updated_at": company["updated_at"],
                "values": company_changes,
            }
        )
    done = store.apply(actor(ctx), [*ops, *(then or [])])
    deal = done[0]["row"]
    base.reflag(ws.id, "opportunity", [deal])
    notifications.quietly(
        notifications.assigned(ctx, "opportunity", deal["id"], deal["name"], deal["owner_id"])
    )
    return base.present_rows(ws, "opportunity", [deal])[0]


def _after_edit(ctx: Any, ws: base.Workspace, before: dict, after: dict, changes: dict) -> None:
    """Flags of the deal and of its company, old and new; a new owner told."""
    base.reflag(ws.id, "opportunity", [after, {**after, "company_id": before["company_id"]}])
    if "owner_id" in changes:
        notifications.quietly(
            notifications.assigned(
                ctx, "opportunity", after["id"], after["name"], changes["owner_id"]
            )
        )


def update(ctx: Any, deal_id: str, payload: Any) -> dict[str, Any]:
    """Contracts §2.5 `PATCH /crm/opportunities/{id}` — never the stage. An
    open deal keeps its next step and date (FR-CRM-PIPE-10) and the fields
    its stage required; an amount or currency saved is converted again
    (FR-CRM-FX-03)."""
    if isinstance(payload, dict) and {"stage", "lost_reason_id"} & set(payload):
        raise CrmRefused(422, MOVE_ONLY)
    ws = base.load(ctx)
    row = base.live(ws.id, "opportunity", deal_id)
    values = base.parse("opportunity", payload, creating=False)
    expect = values.pop("expect_updated_at", None)
    base.check_fields(ws, "opportunity", values, row)
    if "probability" in values and values["probability"] is None:
        raise CrmRefused(422, "Probability is required.")
    company_ops: list[dict[str, Any]] = []
    if values.get("company_id") and values["company_id"] != row["company_id"]:
        target = base.live(ws.id, "company", values["company_id"])
        if row["stage"] in pipeline.OPEN:
            # FR-CRM-PIPE-04 holds through an edit too: an open deal moves
            # only onto a company it could have been opened on.
            company_ops = base.open_deal_company_ops(ws, target)
    _person(ws, values.get("point_of_contact_id"))
    _money(ws, values, row)

    if row["stage"] in pipeline.OPEN:
        after = {**row, **values}
        # What the deal needed to reach its stage stays: an edit cannot clear
        # it. Fields it never had (an imported deal's) are not asked for here.
        earlier = list(pipeline.STAGES[: pipeline.STAGES.index(row["stage"])])
        missing = [
            f
            for f in pipeline.stage_requirements(row["stage"], after, earlier)
            if f in values or f in pipeline.NEXT_STEP
        ]
        if missing:
            raise CrmRefused(422, pipeline.missing_sentence(row["stage"], missing))

    changes = {k: v for k, v in values.items() if row.get(k) != v}
    if not changes:
        return base.present_rows(ws, "opportunity", [row])[0]
    project = (
        tva.project_of(deal_id)
        if ctx.workspace.get("tva_linked") and "probability" in changes
        else None
    )
    if project and project.get("status") == "tentative":
        # FR-CRM-TVA-06: the tentative project follows the deal's
        # probability, in the same transaction as the save.
        if company_ops:
            raise CrmRefused(
                422, "Change the deal's company and its probability in separate saves."
            )
        done = store.deal_transition(
            actor(ctx),
            {
                "opportunity_id": deal_id,
                "expect_updated_at": expect,
                "opportunity": changes,
                "project": {"action": "sync_probability", "probability": changes["probability"]},
            },
        )
        _after_edit(ctx, ws, row, done["opportunity"], changes)
        return base.present_rows(ws, "opportunity", [done["opportunity"]])[0]
    op: dict[str, Any] = {
        "op": "update",
        "table": "crm_opportunities",
        "id": deal_id,
        "values": changes,
    }
    if expect:
        op["expect_updated_at"] = expect
    done = store.apply(actor(ctx), [op, *company_ops])
    _after_edit(ctx, ws, row, done[0]["row"], changes)
    return base.present_rows(ws, "opportunity", [done[0]["row"]])[0]


# ---------------------------------------------------------------------------
# Stage moves — contracts §2.6
# ---------------------------------------------------------------------------


def _plan(
    ctx: Any,
    deal_id: str,
    body: StagePreviewIn,
    expect: str | None,
    *,
    handoff_move: bool = False,
) -> tuple[base.Workspace, dict[str, Any], pipeline.Move, CrmRefused | None]:
    """The move's plan, CRM side and, in Labs, its project action; or why
    not. The last item is a refusal the move would meet that is not a
    missing field: a name clash (409), a link only a TVA manager may make,
    a reopen-won the project's history refuses (422)."""
    ws = base.load(ctx)
    row = base.live(ws.id, "opportunity", deal_id)
    target = body.stage
    labs = bool(ctx.workspace.get("tva_linked"))
    if labs and target == "closed_won" and not handoff_move:
        # FR-CRM-PIPE-07, -TVA-07: the deal is won when the handoff is.
        return (
            ws,
            row,
            pipeline.Move(False, f"Closed Won in {ctx.workspace['name']} needs the handoff."),
            None,
        )

    changes = body.changes.model_dump(mode="json", exclude_unset=True)
    _person(ws, changes.get("point_of_contact_id"))
    _option(ws, changes.get("lost_reason_id"), "opportunity.lost_reason", "lost reason")
    try:
        _money(ws, changes, row)
    except CrmRefused as refused:
        return ws, row, pipeline.Move(False, refused.detail), None

    company = base.live(ws.id, "company", row["company_id"])
    siblings = [
        d
        for d in store.list_records(
            ws.id, "crm_opportunities", filters={"company_id": company["id"]}, limit=2000
        )
        if d["id"] != row["id"]
    ]
    task_ids = sorted(
        {
            link["task_id"]
            for link in store.list_links(
                ws.id, "task_links", record_type="opportunity", record_ids=[row["id"]]
            )
        }
    )
    open_tasks = (
        store.list_records(
            ws.id,
            "tasks",
            any_of={"id": task_ids, "status": ["open", "waiting"]},
            limit=len(task_ids),
        )
        if task_ids
        else []
    )
    client = next(
        (
            o
            for o in ws.facts.options.values()
            if o["list_key"] == "company.account_type" and o.get("system_key") == "client"
        ),
        None,
    )
    reason = changes.get("lost_reason_id") or row.get("lost_reason_id")
    move = pipeline.plan_move(
        deal=row,
        company=_labelled(ws, company),
        target=target,
        changes=changes,
        role=ctx.role,
        user_id=ctx.user.id,
        probabilities=_probabilities(ws.id),
        other_open_deals=sum(1 for d in siblings if d["stage"] in pipeline.OPEN),
        has_won=any(d["stage"] == "closed_won" for d in siblings),
        company_on_loss=body.company_on_loss,
        open_task_ids=[t["id"] for t in open_tasks],
        lost_reason_label=ws.facts.option_label(reason),
        today=today_in_company_tz(),
        client_option_id=client["id"] if client else None,
        expect_updated_at=expect,
    )
    refusal = None
    if (
        labs
        and target != "closed_won"
        and pipeline.may_move(
            row["stage"],
            target,
            ctx.role,
            row.get("owner_id") == ctx.user.id,
            lost_from=row.get("lost_from_stage"),
        )
        is None
    ):
        # FR-CRM-TVA-03 to -06, -12 to -14, PIPE-13: the project action.
        found = tva.effect(
            user=ctx.user,
            deal=row,
            target=target,
            probability=move.would["probability"],
            project_name=body.project_name,
            link_project_id=body.link_project_id,
            lost_reason_label=ws.facts.option_label(reason),
        )
        move.would["project"] = found.would
        refusal = found.refusal
        if found.action and move.plan is not None:
            move.plan["project"] = found.action
    return ws, row, move, refusal


def preview(ctx: Any, deal_id: str, body: StagePreviewIn) -> dict[str, Any]:
    """Contracts §2.6 `…/stage/preview` — what a move would do, writing
    nothing: every field it lacks, and its effect on probability, the
    company and the deal's open tasks."""
    ws, _, move, refusal = _plan(ctx, deal_id, body, None)
    would = dict(move.would)
    if "company_lead_stage" in would:
        would["company_lead_stage_label"] = ws.facts.fixed_label(
            "company.lead_stage", would["company_lead_stage"]
        )
    allowed, message = move.allowed, move.message
    if refusal and allowed:
        allowed, message = False, refusal.detail
    return {
        "allowed": allowed,
        "missing": move.missing,
        "message": message,
        "would": would,
    }


def move(ctx: Any, deal_id: str, body: StageMoveIn) -> dict[str, Any]:
    """Contracts §2.6 `…/stage` — the move and every side effect, or one
    sentence naming every missing field (422)."""
    expect = body.expect_updated_at.isoformat() if body.expect_updated_at else None
    ws, _, planned, refusal = _plan(ctx, deal_id, body, expect)
    if not planned.allowed or planned.plan is None:
        raise CrmRefused(422, planned.message or "That move is not allowed.")
    if refusal:
        raise refusal
    done = store.deal_transition(actor(ctx), planned.plan)
    _after_move(ctx, ws, done)
    return _answer(ws, done)


def _after_move(ctx: Any, ws: base.Workspace, done: dict[str, Any]) -> None:
    """A move changes the deal's flags, its company's, and those of the
    tasks it closed; the owner hears of a move someone else made."""
    deal = done["opportunity"]
    base.reflag(ws.id, "opportunity", [deal])
    try:
        tasks = {
            lk["task_id"]
            for lk in store.list_links(
                ws.id, "task_links", record_type="opportunity", record_ids=[deal["id"]]
            )
        }
    except Exception:
        flags.logger.exception('{"event": "crm_flags_links_failed"}')
        tasks = set()
    flags.after_write(ws.id, tasks=tasks)
    notifications.quietly(
        notifications.stage_changed(
            ctx, deal, ws.facts.fixed_label("opportunity.stage", deal["stage"])
        )
    )


def _answer(ws: base.Workspace, done: dict[str, Any]) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "opportunity": base.present_rows(ws, "opportunity", [done["opportunity"]])[0],
        "project": tva.present(done.get("project")),
        "tasks_closed": done.get("tasks_closed", 0),
    }
    if done.get("company"):
        answer["company"] = base.present_rows(ws, "company", [done["company"]])[0]
    if done.get("allocations_released"):
        answer["allocations_released"] = done["allocations_released"]
    if done.get("allocations_restored"):
        answer["allocations_restored"] = done["allocations_restored"]
    return answer


# ---------------------------------------------------------------------------
# The Closed Won handoff — FR-CRM-TVA-07 to -10, FR-CRM-FX-05; contracts §2.6
# ---------------------------------------------------------------------------


def _labs(ctx: Any) -> None:
    """FR-CRM-WS-05 — only the TVA-linked workspace has a TVA side."""
    if not ctx.workspace.get("tva_linked"):
        raise CrmRefused(422, f"{ctx.workspace['name']} is not connected to TVA projects.")


def _option_key(ws: base.Workspace, option_id: str | None) -> str | None:
    option = ws.facts.options.get(option_id or "")
    return option.get("system_key") if option else None


def handoff_preview(ctx: Any, deal_id: str) -> dict[str, Any]:
    """Contracts §2.6 `GET …/handoff` — the handoff, pre-filled from the deal
    and the project's existing phases, writing nothing. The project's
    existing revenue and milestone amounts are only for a staff TVA manager
    or admin."""
    _labs(ctx)
    ws = base.load(ctx)
    row = base.live(ws.id, "opportunity", deal_id)
    project = tva.project_of(deal_id)
    phases = db.list_project_phases([project["id"]]) if project else []
    may_set = handoff.can_set_revenue(ctx.user.profile)
    milestones = db.list_project_milestones(project["id"]) if project and may_set else []
    won = handoff.won_fx(
        row, store.list_fx_rates(ws.id), today_in_company_tz(), ctx.workspace["name"]
    )
    leads = [
        {"id": p["id"], "display_name": p["display_name"]}
        for p in sorted(db.list_profiles(active_only=True), key=lambda p: p["display_name"])
        if handoff.lead_refusal(p) is None
    ]
    return handoff.prefill(
        deal=row,
        project=project,
        phases=phases,
        milestones=milestones,
        won=won,
        may_set_revenue=may_set,
        engagement_model_key=_option_key(ws, row.get("engagement_model_id")),
        leads=leads,
    )


def handoff_confirm(ctx: Any, deal_id: str, body: HandoffIn) -> dict[str, Any]:
    """Contracts §2.6 `POST …/handoff` — the deal is Closed Won and its
    project confirmed with its lead and phases, in one transaction
    (FR-CRM-TVA-07, -09, -10). Revenue and milestones (INR) are written only
    for a staff TVA manager or admin; anyone else's are ignored and the Labs
    admins get a task to set them (FR-CRM-TVA-08, D8). The delivery handoff
    task goes to the lead, or the deal owner (automation 10)."""
    _labs(ctx)
    expect = body.expect_updated_at.isoformat() if body.expect_updated_at else None
    ws, row, move, _ = _plan(
        ctx, deal_id, StagePreviewIn(stage="closed_won"), expect, handoff_move=True
    )
    if not move.allowed or move.plan is None:
        raise CrmRefused(422, move.message or "That move is not allowed.")

    profiles = {p["id"]: p for p in db.list_profiles(include_crm_only=True)}
    lead = profiles.get(body.lead_id)
    if why := handoff.lead_refusal(lead):
        raise CrmRefused(422, why)
    phases = {
        kind: span.model_dump()
        for kind, span in (
            ("pre", body.phases.pre),
            ("delivery", body.phases.delivery),
            ("support", body.phases.support),
        )
        if span is not None
    }
    if why := handoff.phases_refusal(phases):
        raise CrmRefused(422, why)
    won = handoff.won_fx(
        row, store.list_fx_rates(ws.id), today_in_company_tz(), ctx.workspace["name"]
    )
    if isinstance(won, str):
        raise CrmRefused(422, won)

    project = tva.project_of(deal_id)
    adopted = None
    if body.link_project_id:
        # FR-CRM-TVA-10 with -05: a deal won without a project may adopt an
        # existing tentative one, by a staff TVA manager or admin.
        if not tva.may_link(ctx.user):
            raise CrmRefused(409, tva.ASK_A_MANAGER)
        if project:
            raise CrmRefused(409, "This deal already has a TVA project.")
        adopted = next(
            (p for p in db.list_linkable_projects("tentative") if p["id"] == body.link_project_id),
            None,
        )
        if adopted is None:
            raise CrmRefused(
                409,
                "That project cannot be linked: only an unlinked, unarchived tentative "
                "project can.",
            )
        project = adopted
    name = (body.project_name or (project["name"] if project else row["name"])).strip()
    found = tva.clash(name, exclude=project["id"] if project else None)
    if found:
        way = " or link the deal to it" if tva.may_link(ctx.user) and not project else ""
        raise CrmRefused(
            409,
            f"A project called “{found['name']}” already exists. Give the project another "
            f"name{way}.",
        )
    category = body.category or (
        project["category"]
        if project and project.get("category") in ("client", "poc", "product")
        else row.get("project_category") or "client"
    )
    action: dict[str, Any] = {
        "action": "confirm",
        "name": name,
        "category": category,
        "lead_id": body.lead_id,
        "probability": move.plan["opportunity"]["probability"],
        "phases": {
            kind: {
                "starts_on": span["starts_on"].isoformat(),
                "ends_on": span["ends_on"].isoformat(),
            }
            for kind, span in phases.items()
        },
    }
    if adopted:
        action["project_id"] = adopted["id"]
    may_set = handoff.can_set_revenue(ctx.user.profile)
    if may_set and body.revenue is not None:
        action["revenue"] = format(body.revenue, "f")
    if may_set and body.milestones is not None:
        # The project's full set (FR-CRM-TVA-07): an existing milestone keeps
        # its id, so the pre-fill sent back unchanged adds nothing.
        known = {m["id"] for m in db.list_project_milestones(project["id"])} if project else set()
        if any(m.id and m.id not in known for m in body.milestones):
            raise CrmRefused(404, "No such milestone on this project.")
        action["milestones"] = [
            {
                **({"id": m.id} if m.id else {}),
                "name": m.name,
                "due_on": m.due_on.isoformat(),
                "amount": format(m.amount, "f"),
            }
            for m in body.milestones
        ]

    tasks: list[dict[str, Any]] = []
    if not may_set:
        admins = [
            profiles[m["user_id"]]
            for m in store.list_members(ws.id)
            if m["role"] == "admin" and m["user_id"] in profiles
        ]
        tasks += [
            handoff.revenue_task(name, deal_id, who)
            for who in handoff.revenue_task_recipients(admins)
        ]
    assignee, name_lead = handoff.delivery_assignee(body.lead_id, ws.members, row["owner_id"])
    if assignee not in ws.members:
        assignee = ctx.user.id  # the owner has left the workspace
    tasks.append(handoff.delivery_task(name, lead["display_name"], deal_id, assignee, name_lead))

    plan = move.plan
    if isinstance(won, dict):
        plan["opportunity"].update(won)
    plan["project"] = action
    plan["create_tasks"] = tasks
    done = store.deal_transition(actor(ctx), plan)
    _after_move(ctx, ws, done)
    notifications.quietly(
        notifications.handoff_waiting(
            ctx,
            done["opportunity"],
            [
                {**task, "id": task_id}
                for task_id, task in zip(done.get("tasks_created") or [], tasks, strict=False)
            ],
        )
    )
    answer = _answer(ws, done)
    answer["revenue_written"] = "revenue" in action
    answer["tasks_created"] = [
        {
            "id": task_id,
            "title": task["title"],
            "assignee": {"id": task["assignee_id"], "name": ws.facts.name(task["assignee_id"])},
        }
        for task_id, task in zip(done.get("tasks_created") or [], tasks, strict=False)
    ]
    return answer


# ---------------------------------------------------------------------------
# Link TVA project — FR-CRM-TVA-18; contracts §2.6
# ---------------------------------------------------------------------------


def link_project(ctx: Any, deal_id: str, body: LinkProjectIn) -> dict[str, Any]:
    """An open deal at Proposal or later is linked to an unlinked, unarchived
    tentative project, or gets one created; a won deal is linked to an
    unlinked confirmed project, which keeps its status, revenue, phases and
    milestones. Linking an existing project needs a staff TVA manager or
    admin (FR-CRM-TVA-05). How imported deals reach their projects
    (FR-CRM-MIG-06)."""
    _labs(ctx)
    ws = base.load(ctx)
    row = base.live(ws.id, "opportunity", deal_id)
    if body.mode == "link" and not tva.may_link(ctx.user):
        raise CrmRefused(409, tva.ASK_A_MANAGER)
    if tva.project_of(deal_id):
        raise CrmRefused(409, "This deal already has a TVA project.")
    won = row["stage"] == "closed_won"
    if row["stage"] not in ("proposal", "negotiation", "closed_won"):
        raise CrmRefused(
            422, "Only a deal at Proposal or later, or a won deal, can have a TVA project."
        )
    plan: dict[str, Any] = {
        "opportunity_id": deal_id,
        "expect_updated_at": (
            body.expect_updated_at.isoformat() if body.expect_updated_at else None
        ),
        "mode": body.mode,
    }
    if body.mode == "link":
        status = "confirmed" if won else "tentative"
        target = next(
            (p for p in db.list_linkable_projects(status) if p["id"] == body.project_id), None
        )
        if target is None:
            raise CrmRefused(
                409,
                f"That project cannot be linked: only an unlinked, unarchived {status} "
                "project can.",
            )
        plan["project_id"] = target["id"]
    else:
        if won:
            raise CrmRefused(422, "A won deal is linked to its existing confirmed project.")
        name = (body.name or row["name"]).strip()
        if found := tva.clash(name):
            way = (
                "link the deal to it" if tva.may_link(ctx.user) else "ask a TVA manager to link it"
            )
            raise CrmRefused(
                409,
                f"A project called “{found['name']}” already exists. Give the project "
                f"another name, or {way}.",
            )
        plan["name"] = name
        plan["category"] = body.category or row.get("project_category") or "client"
    done = store.link_project(actor(ctx), plan)
    return {
        "opportunity": base.present_rows(
            ws, "opportunity", [base.live(ws.id, "opportunity", deal_id)]
        )[0],
        "project": tva.present(done.get("project")),
    }


def linkable(ctx: Any, status: str | None) -> list[dict[str, Any]]:
    """Contracts §2.6 `GET /crm/projects/linkable` — unlinked, unarchived
    projects, for a Labs admin who is a staff TVA manager or admin only:
    nobody else sees TVA project names and clients this way."""
    _labs(ctx)
    if not tva.may_link(ctx.user):
        raise CrmRefused(403, "Only a TVA manager or admin can link TVA projects.")
    return [
        {"id": p["id"], "name": p["name"], "status": p["status"], "client": p.get("client")}
        for p in db.list_linkable_projects(status)
    ]


# ---------------------------------------------------------------------------
# Contacts — spec §5.8 "Other contacts … Role in deal"
# ---------------------------------------------------------------------------


def add_contact(ctx: Any, deal_id: str, payload: ContactIn) -> dict[str, Any]:
    ws = base.load(ctx)
    base.live(ws.id, "opportunity", deal_id)
    _person(ws, payload.person_id)
    _option(ws, payload.role_id, "opportunity.contact_role", "role in deal")
    done = store.apply(
        actor(ctx),
        [
            {
                "op": "insert",
                "table": "crm_opportunity_contacts",
                "values": {
                    "opportunity_id": deal_id,
                    "person_id": payload.person_id,
                    "role_id": payload.role_id,
                },
            }
        ],
    )
    people = base.by_id(ws.id, "crm_people", {payload.person_id})
    return show.contact(done[0]["row"], ws.facts, people)


def remove_contact(ctx: Any, deal_id: str, person_id: str) -> dict[str, Any]:
    ws = base.load(ctx)
    base.live(ws.id, "opportunity", deal_id)
    found = next(
        (
            c
            for c in store.list_contacts(ws.id, opportunity_ids=[deal_id])
            if c["person_id"] == person_id
        ),
        None,
    )
    if found is None:
        raise CrmRefused(404, "No such contact on this deal.")
    store.apply(
        actor(ctx), [{"op": "delete", "table": "crm_opportunity_contacts", "id": found["id"]}]
    )
    return {"removed": True}
