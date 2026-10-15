"""Company merge — spec 007 FR-CRM-DUP-03, -04; contracts §2.8; data-model
§5.7.

The preview says what the person is choosing between and what will move;
the merge re-reads both companies, applies `domain/crm/merge.py` to their
choices and makes one `crm_merge_companies()` call (035), which re-checks
both ids in the caller's workspace and moves everything in one transaction.
The merged company goes to the Trash with `merged_into_id` set, so a link
to it opens the survivor (`records.get`).

Admins and Members merge (the route's guard); nobody needs to own either
company, since nothing is lost: every record moves.
"""

from __future__ import annotations

from typing import Any

from app.domain.crm import merge as rules
from app.domain.crm import objects
from app.schemas.crm import MergeIn
from app.services import supabase as db
from app.services.crm import CrmRefused, store
from app.services.crm import present as show
from app.services.crm import records as base
from app.services.crm.context import actor


def _pair(ctx: Any, a: str, b: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if a == b:
        raise CrmRefused(422, rules.SAME_COMPANY)
    return base.live(ctx.workspace_id, "company", a), base.live(ctx.workspace_id, "company", b)


def _tva_linked(ctx: Any) -> bool:
    return bool(ctx.workspace.get("tva_linked"))


def _shown(name: str, value: Any, facts: show.Facts) -> Any:
    """A choice as the dialog shows it."""
    if value in (None, "", [], {}):
        return None
    if name in ("website", "linkedin"):
        return ((value or {}).get("primary") or {}).get("url")
    if name == "address":
        return ", ".join(str(v) for v in value.values() if v) or None
    shown = show.display("company", name, value, facts)
    if isinstance(shown, list):
        return ", ".join(str(v) for v in shown if v is not None)
    return shown


def moves(ctx: Any, company_id: str) -> dict[str, int]:
    """What would move from this company to the survivor (FR-CRM-DUP-03):
    every person and deal (Trash included), task and note link, Drive link,
    and in Labs every TVA project linked to it (FR-CRM-DUP-04)."""
    ws_id = ctx.workspace_id
    every = {"deleted": None, "limit": 10_000}
    return {
        "people": len(
            store.list_records(ws_id, "crm_people", filters={"company_id": company_id}, **every)
        ),
        "deals": len(
            store.list_records(
                ws_id, "crm_opportunities", filters={"company_id": company_id}, **every
            )
        ),
        "tasks": len(
            store.list_links(
                ws_id, "crm_task_links", record_type="company", record_ids=[company_id]
            )
        ),
        "notes": len(
            store.list_links(
                ws_id, "crm_note_links", record_type="company", record_ids=[company_id]
            )
        ),
        "links": len(
            store.list_records(
                ws_id,
                "crm_drive_links",
                filters={"record_type": "company", "record_id": company_id},
                **every,
            )
        ),
        "projects": (
            len(db.list_crm_projects(company_ids=[company_id])) if _tva_linked(ctx) else 0
        ),
        # 1b (041): its campaign memberships.
        "campaign_members": len(
            store.campaign_members(ws_id, column="company_id", ids=[company_id], deleted=None)
        ),
    }


def preview(ctx: Any, a: str, b: str) -> dict[str, Any]:
    """Contracts §2.8 `GET /crm/companies/merge-preview` — `a` survives, `b`
    is merged into it. Each differing field says both values and which one a
    merge keeps unless told otherwise."""
    survivor, merged = _pair(ctx, a, b)
    ws = base.load(ctx)
    domains = store.list_domains(ws.id, company_ids=[a, b])
    fields = []
    for name in rules.differing(survivor, merged, domains, tva_linked=_tva_linked(ctx)):
        values = [rules.value_of(row, name, domains) for row in (survivor, merged)]
        fields.append(
            {
                "field": name,
                "label": objects.label("company", name),
                "a": values[0],
                "b": values[1],
                "a_display": _shown(name, values[0], ws.facts),
                "b_display": _shown(name, values[1], ws.facts),
                "default": rules.default_side(name, survivor, merged, domains),
            }
        )
    presented = base.present_rows(ws, "company", [survivor, merged])
    return {
        "a": presented[0],
        "b": presented[1],
        "differing_fields": fields,
        "moves": moves(ctx, b),
    }


def merge(ctx: Any, body: MergeIn) -> dict[str, Any]:
    """Contracts §2.8 `POST /crm/companies/merge` — the survivor, as its
    record page shows it."""
    survivor, merged = _pair(ctx, body.survivor_id, body.merged_id)
    ws = base.load(ctx)
    tva = _tva_linked(ctx)
    why = rules.choice_refusal(body.choices, rules.choosable(tva_linked=tva))
    if why:
        raise CrmRefused(422, why)
    domains = store.list_domains(ws.id, company_ids=[survivor["id"], merged["id"]])
    changes, primary = rules.survivor_changes(
        survivor, merged, domains, body.choices, tva_linked=tva
    )

    # FR-CRM-PIPE-04: the survivor keeps every open deal of both.
    deals = store.list_records(
        ws.id,
        "crm_opportunities",
        any_of={"company_id": [survivor["id"], merged["id"]]},
        limit=10_000,
    )
    chosen = changes.get("lead_stage", survivor["lead_stage"])
    stage, why = rules.lead_stage_after(
        chosen,
        has_open_deals=rules.has_open(deals),
        name=changes.get("name", survivor["name"]),
        chosen_label=ws.facts.fixed_label("company.lead_stage", chosen) or chosen,
    )
    if why:
        raise CrmRefused(422, why)
    if stage != survivor["lead_stage"]:
        changes["lead_stage"] = stage
    else:
        changes.pop("lead_stage", None)
    if changes.get("owner_id") and changes["owner_id"] not in ws.members:
        raise CrmRefused(422, "The owner must be a member of this workspace.")

    expect = body.expect
    plan = {
        "survivor_id": survivor["id"],
        "merged_id": merged["id"],
        "expect": {
            "survivor_updated_at": (expect and expect.survivor_updated_at)
            or survivor["updated_at"],
            "merged_updated_at": (expect and expect.merged_updated_at) or merged["updated_at"],
        },
        "survivor_changes": changes,
        "primary_domain": primary,
    }
    store.merge_companies(actor(ctx), plan)
    row = store.get_record(ws.id, "crm_companies", survivor["id"])
    return base.present_rows(ws, "company", [row])[0]
