"""Deals — spec 007 FR-CRM-OPP-01 to -03, FR-CRM-FX-01, -03, -04,
FR-CRM-PIPE-02 to -15 (CRM side), FR-CRM-AUTO-01 (automation 9); contracts
§2.5, §2.6; plan §5.3, §5.6.

Create, edit, preview and move a deal, and its contacts. The rules are
decided in `domain/crm/pipeline.py` and `domain/crm/fx.py`; this module
gathers the rows they need and writes the result in one database call:
`crm_apply` for a create or an edit (the deal, its contacts and its
company's move to Deal open together), `crm_deal_transition` for a stage
move and every side effect it has (FR-CRM-PIPE-15).

A deal's stage never changes through an edit: only a move changes it, and
`crm_apply` refuses it too.

The TVA side of a Labs deal is S5's. Until it exists, a Labs deal is not
moved into Proposal, Negotiation or Closed Won (plan §5.6), so no Labs deal
reaches Proposal without the tentative project S5 will create for it.
"""

from __future__ import annotations

from typing import Any

from app.domain.calendar import today_in_company_tz
from app.domain.crm import fx, pipeline
from app.schemas.crm import ContactIn, StageMoveIn, StagePreviewIn
from app.services.crm import CrmRefused, store
from app.services.crm import present as show
from app.services.crm import records as base
from app.services.crm.context import actor

#: Contracts §2.6 — these change only through a move.
MOVE_ONLY = "A deal's stage and lost reason change through a stage move."
#: Plan §5.6, removed in S5.
TVA_STAGES = ("proposal", "negotiation", "closed_won")
TVA_NOT_BUILT = "This move needs the TVA link, which is not built yet."


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


def create(ctx: Any, payload: Any) -> dict[str, Any]:
    """FR-CRM-PIPE-04 — a deal opens on a company at Qualified or later, and a
    Qualified company moves to Deal open in the same call. The deal starts at
    Discovery with its next step and date (FR-CRM-PIPE-07, -10) and the
    stage's default probability unless one is given (FR-CRM-PIPE-11)."""
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
    done = store.apply(actor(ctx), ops)
    return base.present_rows(ws, "opportunity", [done[0]["row"]])[0]


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
    op: dict[str, Any] = {
        "op": "update",
        "table": "crm_opportunities",
        "id": deal_id,
        "values": changes,
    }
    if expect:
        op["expect_updated_at"] = expect
    done = store.apply(actor(ctx), [op, *company_ops])
    return base.present_rows(ws, "opportunity", [done[0]["row"]])[0]


# ---------------------------------------------------------------------------
# Stage moves — contracts §2.6
# ---------------------------------------------------------------------------


def _plan(ctx: Any, deal_id: str, body: StagePreviewIn, expect: str | None):
    ws = base.load(ctx)
    row = base.live(ws.id, "opportunity", deal_id)
    target = body.stage
    if ctx.workspace.get("tva_linked") and target in TVA_STAGES:
        return ws, row, pipeline.Move(False, TVA_NOT_BUILT)

    changes = body.changes.model_dump(mode="json", exclude_unset=True)
    _person(ws, changes.get("point_of_contact_id"))
    _option(ws, changes.get("lost_reason_id"), "opportunity.lost_reason", "lost reason")
    try:
        _money(ws, changes, row)
    except CrmRefused as refused:
        return ws, row, pipeline.Move(False, refused.detail)

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
                ws.id, "crm_task_links", record_type="opportunity", record_ids=[row["id"]]
            )
        }
    )
    open_tasks = (
        store.list_records(
            ws.id,
            "crm_tasks",
            any_of={"id": task_ids},
            not_equal={"status": "done"},
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
    return ws, row, move


def preview(ctx: Any, deal_id: str, body: StagePreviewIn) -> dict[str, Any]:
    """Contracts §2.6 `…/stage/preview` — what a move would do, writing
    nothing: every field it lacks, and its effect on probability, the
    company and the deal's open tasks."""
    ws, _, move = _plan(ctx, deal_id, body, None)
    would = dict(move.would)
    if "company_lead_stage" in would:
        would["company_lead_stage_label"] = ws.facts.fixed_label(
            "company.lead_stage", would["company_lead_stage"]
        )
    return {
        "allowed": move.allowed,
        "missing": move.missing,
        "message": move.message,
        "would": would,
    }


def move(ctx: Any, deal_id: str, body: StageMoveIn) -> dict[str, Any]:
    """Contracts §2.6 `…/stage` — the move and every side effect, or one
    sentence naming every missing field (422)."""
    expect = body.expect_updated_at.isoformat() if body.expect_updated_at else None
    ws, _, planned = _plan(ctx, deal_id, body, expect)
    if not planned.allowed or planned.plan is None:
        raise CrmRefused(422, planned.message or "That move is not allowed.")
    done = store.deal_transition(actor(ctx), planned.plan)
    answer: dict[str, Any] = {
        "opportunity": base.present_rows(ws, "opportunity", [done["opportunity"]])[0],
        "project": None,
        "tasks_closed": done.get("tasks_closed", 0),
    }
    if done.get("company"):
        answer["company"] = base.present_rows(ws, "company", [done["company"]])[0]
    return answer


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
