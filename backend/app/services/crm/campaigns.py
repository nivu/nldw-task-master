"""Campaign members — spec 007 FR-CRM-CAMP-02 to -05, FR-CRM-PER-02,
FR-CRM-AUTO-01 (automation 8), FR-CRM-VIEW-07; contracts §2.10.

A campaign itself is a record like any other (`records.py`: create, edit,
Trash, Drive links, tasks and notes, and its figures on its page). This
module is its members: the board's list, adding companies and people (by
id, from a view's bulk bar, or everyone a saved view of companies or people
lists), moving a member through the statuses, and converting one to a new
or existing deal. The rules are `domain/crm/campaigns.py`'s; each change is
one `crm_apply` call, so a conversion's member, deal and attribution land
together or not at all.

Sends happen outside the CRM (FR-CRM-CAMP-07): outcomes are recorded here.
"""

from __future__ import annotations

from typing import Any

from app.domain.crm import campaigns as rules
from app.domain.crm import dedupe
from app.schemas.crm import (
    CampaignMembersIn,
    CampaignMemberUpdate,
    ConvertIn,
    QueryIn,
    QuerySpec,
)
from app.services.crm import CrmRefused, deals, notifications, store
from app.services.crm import records as base
from app.services.crm.context import actor

NO_SUCH_MEMBER = "No such campaign member."
#: Contracts §2.10 — the people a view of companies or people lists.
VIEW_OBJECTS = ("company", "person")


def live_campaign(ctx: Any, campaign_id: str) -> dict[str, Any]:
    """The campaign, or "No such campaign." (absent, another workspace's, or
    in the Trash)."""
    return base.live(ctx.workspace_id, "campaign", campaign_id)


def _member(ctx: Any, member_id: str, campaign_id: str | None = None) -> dict[str, Any]:
    """A live member of a live campaign of this workspace, and of
    `campaign_id` when the caller names one (the MCP tool confirms a
    campaign with the person; a member of another is "No such campaign
    member.")."""
    row = (
        store.get_record(ctx.workspace_id, "crm_campaign_members", member_id)
        if base._uuid(member_id)
        else None
    )
    if row is None or row.get("deleted_at"):
        raise CrmRefused(404, NO_SUCH_MEMBER)
    if campaign_id is not None and str(row["campaign_id"]).lower() != campaign_id.lower():
        raise CrmRefused(404, NO_SUCH_MEMBER)
    campaign = store.get_record(ctx.workspace_id, "crm_campaigns", row["campaign_id"])
    if campaign is None or campaign.get("deleted_at"):
        raise CrmRefused(404, NO_SUCH_MEMBER)
    return row


def _present(ctx: Any, row: dict[str, Any]) -> dict[str, Any]:
    return base.present_members(base.load(ctx), [row])[0]


# ---------------------------------------------------------------------------
# The board — FR-CRM-CAMP-02
# ---------------------------------------------------------------------------


def members(ctx: Any, campaign_id: str, *, status: str | None = None) -> dict[str, Any]:
    """Contracts §2.10 — the campaign's members in board order, each named,
    with the campaign's counts by status (FR-CRM-CAMP-05). A member whose
    company or person is in the Trash is left out until it is restored."""
    campaign = live_campaign(ctx, campaign_id)
    ws = base.load(ctx)
    rows = base.shown_members(
        ctx.workspace_id,
        store.campaign_members(ctx.workspace_id, column="campaign_id", ids=[campaign["id"]]),
    )
    shown = [r for r in rows if status is None or r["status"] == status]
    found = base.campaign_figures(ctx.workspace_id, [campaign]).get(campaign["id"], {})
    return {
        "campaign_id": campaign["id"],
        "member_counts": found.get("member_counts", {}),
        "rows": base.present_members(ws, shown),
    }


# ---------------------------------------------------------------------------
# Adding members — FR-CRM-CAMP-03, FR-CRM-PER-02
# ---------------------------------------------------------------------------


def _from_view(ctx: Any, view_id: str) -> tuple[str, list[str]]:
    """Everyone a saved view of companies or people lists, page by page
    through the same query the list runs (FR-CRM-CAMP-03 "by a saved
    filter"). Imported here: views reads campaigns for its bulk bar."""
    from app.services.crm import views

    view = views._view(ctx, view_id)
    if view["object"] not in VIEW_OBJECTS:
        raise CrmRefused(422, "Add members from a view of companies or people.")
    ids: list[str] = []
    after: str | None = None
    while True:
        page = views.query(
            ctx,
            QueryIn(object=view["object"], view_id=view_id, spec=QuerySpec(limit=200, after=after)),
        )
        ids += [r["id"] for r in page["rows"]]
        if len(ids) > rules.MAX_ADD:
            raise CrmRefused(422, rules.TOO_MANY)
        after = page.get("next")
        if not after:
            return view["object"], ids


def _plan(
    ctx: Any, campaign: dict[str, Any], company_ids: list[str], person_ids: list[str]
) -> tuple[rules.Addition, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """What adding these would do, after checking each is a live record of
    this workspace (an id of another one is "No such company.")."""
    ws_id = ctx.workspace_id
    for kind, ids in (("company", company_ids), ("person", person_ids)):
        if any(not base._uuid(i) for i in ids):
            raise base.no_such(kind)
    companies = base.by_id(ws_id, "crm_companies", company_ids)
    people = base.by_id(ws_id, "crm_people", person_ids)
    for kind, ids, found in (("company", company_ids, companies), ("person", person_ids, people)):
        if any(i not in found or found[i].get("deleted_at") for i in ids):
            raise base.no_such(kind)
    existing = store.campaign_members(
        ws_id, column="campaign_id", ids=[campaign["id"]], deleted=None
    )
    plan = rules.plan_additions(company_ids, [people[i] for i in person_ids], existing)
    return plan, companies, people


def _insert(ctx: Any, campaign: dict[str, Any], pairs: list[tuple[str | None, str | None]]) -> None:
    """New members at the end of the Targeted column, in one `crm_apply`
    call (at most `MAX_ADD`; the ops go in the request body, not the URL),
    so an add lands whole or not at all."""
    if not pairs:
        return
    current = store.campaign_members(ctx.workspace_id, column="campaign_id", ids=[campaign["id"]])
    start = max((float(m["position"]) for m in current), default=0.0) + 1
    ops = [
        {
            "op": "insert",
            "table": "crm_campaign_members",
            "values": {
                "campaign_id": campaign["id"],
                "company_id": company_id,
                "person_id": person_id,
                "status": "targeted",
                "position": start + n,
            },
        }
        for n, (company_id, person_id) in enumerate(pairs)
    ]
    store.apply(actor(ctx), ops)


def add_members(ctx: Any, campaign_id: str, body: CampaignMembersIn) -> dict[str, Any]:
    """Contracts §2.10 `POST /crm/campaigns/{id}/members` —
    `{added, skipped_do_not_contact, skipped_existing, skipped}`: a person
    marked Do not contact is skipped and named, and so is anyone already in
    the campaign (FR-CRM-PER-02)."""
    campaign = live_campaign(ctx, campaign_id)
    company_ids = list(dict.fromkeys(body.company_ids))
    person_ids = list(dict.fromkeys(body.person_ids))
    if body.view_id:
        kind, ids = _from_view(ctx, body.view_id)
        (company_ids if kind == "company" else person_ids).extend(ids)
        company_ids = list(dict.fromkeys(company_ids))
        person_ids = list(dict.fromkeys(person_ids))
    if not company_ids and not person_ids:
        raise CrmRefused(422, "Choose the companies or people to add.")
    if len(company_ids) + len(person_ids) > rules.MAX_ADD:
        raise CrmRefused(422, rules.TOO_MANY)
    plan, companies, people = _plan(ctx, campaign, company_ids, person_ids)
    _insert(ctx, campaign, plan.add)

    def name(record_id: str) -> str:
        if record_id in companies:
            return companies[record_id]["name"]
        person = people[record_id]
        return dedupe.full_name(person["first_name"], person.get("last_name"))

    return {
        "added": len(plan.add),
        "skipped_do_not_contact": len(plan.do_not_contact),
        "skipped_existing": len(plan.existing),
        "skipped": [
            {"id": i, "name": name(i), "reason": rules.do_not_contact(name(i))}
            for i in plan.do_not_contact
        ]
        + [
            {"id": i, "name": name(i), "reason": rules.already_member(name(i))}
            for i in plan.existing
        ],
    }


def add_one(ctx: Any, campaign_id: str, kind: str, record_id: str) -> None:
    """One record of the bulk bar's Add to campaign (FR-CRM-VIEW-07): added,
    or refused with the reason (Do not contact, already a member)."""
    campaign = live_campaign(ctx, campaign_id)
    company_ids = [record_id] if kind == "company" else []
    person_ids = [record_id] if kind == "person" else []
    plan, companies, people = _plan(ctx, campaign, company_ids, person_ids)
    if plan.do_not_contact:
        person = people[record_id]
        raise CrmRefused(
            422,
            rules.do_not_contact(dedupe.full_name(person["first_name"], person.get("last_name"))),
        )
    if plan.existing:
        found = companies.get(record_id) or people.get(record_id) or {}
        name = found.get("name") or dedupe.full_name(
            found.get("first_name"), found.get("last_name")
        )
        raise CrmRefused(409, rules.already_member(name))
    _insert(ctx, campaign, plan.add)


# ---------------------------------------------------------------------------
# Moving and converting — FR-CRM-CAMP-02, -04
# ---------------------------------------------------------------------------


def update_member(
    ctx: Any, member_id: str, body: CampaignMemberUpdate, *, campaign_id: str | None = None
) -> dict[str, Any]:
    """Contracts §2.10 `PATCH /crm/campaign-members/{id}` — status, last
    touch, notes and board position. Converted is refused here: a
    conversion names the deal."""
    row = _member(ctx, member_id, campaign_id)
    values = body.model_dump(mode="json", exclude_unset=True)
    expect = values.pop("expect_updated_at", None)
    if "status" in values:
        if values["status"] is None:
            raise CrmRefused(422, "A member needs a status.")
        why = rules.status_refusal(row["status"], values["status"])
        if why:
            raise CrmRefused(422, why)
    if "position" in values and values["position"] is None:
        values.pop("position")
    changes = {k: v for k, v in values.items() if row.get(k) != v}
    if not changes:
        return _present(ctx, row)
    op: dict[str, Any] = {
        "op": "update",
        "table": "crm_campaign_members",
        "id": member_id,
        "values": changes,
    }
    if expect:
        op["expect_updated_at"] = expect
    done = store.apply(actor(ctx), [op])
    return _present(ctx, done[0]["row"])


def _new_deal_fields(ctx: Any, row: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """A new deal's fields, its company and point of contact taken from the
    member where the person converting has not named them."""
    fields = dict(new)
    person = (
        store.get_record(ctx.workspace_id, "crm_people", row["person_id"])
        if row.get("person_id")
        else None
    )
    if not fields.get("company_id"):
        company = row.get("company_id") or (person or {}).get("company_id")
        if not company:
            raise CrmRefused(422, "Say which company the new deal is with.")
        fields["company_id"] = company
    if person and not person.get("deleted_at") and "point_of_contact_id" not in fields:
        fields["point_of_contact_id"] = person["id"]
    return fields


def convert(
    ctx: Any, member_id: str, body: ConvertIn, *, campaign_id: str | None = None
) -> dict[str, Any]:
    """Contracts §2.10 `POST /crm/campaign-members/{id}/convert` — the member
    is Converted with its deal, an existing one or a new one opened by the
    deal rules (FR-CRM-PIPE-04, -07, -10), and with automation 8 on the
    deal records the campaign as its source (FR-CRM-CAMP-04, AUTO-01). One
    transaction. `{member, opportunity}`."""
    row = _member(ctx, member_id, campaign_id)
    if row["status"] == "converted":
        raise CrmRefused(422, rules.ALREADY_CONVERTED)
    if body.opportunity_id and body.new is not None:
        raise CrmRefused(422, rules.NAME_ONE_DEAL)
    if not body.opportunity_id and body.new is None:
        raise CrmRefused(422, rules.NAME_A_DEAL)
    attribution = notifications.automation_on(ctx.workspace_id, "campaign_conversion")
    ws = base.load(ctx)
    if body.opportunity_id:
        deal = base.live(ctx.workspace_id, "opportunity", body.opportunity_id)
        member_changes, deal_changes = rules.conversion(row, deal, attribution=attribution)
        ops: list[dict[str, Any]] = [
            {
                "op": "update",
                "table": "crm_campaign_members",
                "id": member_id,
                "expect_updated_at": row["updated_at"],
                "values": member_changes,
            }
        ]
        if deal_changes:
            ops.append(
                {
                    "op": "update",
                    "table": "crm_opportunities",
                    "id": deal["id"],
                    "values": deal_changes,
                }
            )
        done = store.apply(actor(ctx), ops)
        opportunity = base.present_rows(
            ws, "opportunity", [done[-1]["row"] if deal_changes else deal]
        )[0]
    else:
        fields = _new_deal_fields(ctx, row, body.new or {})
        then = [
            {
                "op": "update",
                "table": "crm_campaign_members",
                "id": member_id,
                "expect_updated_at": row["updated_at"],
                "values": {"status": "converted", "converted_opportunity_id": "$ref:deal"},
            }
        ]
        opportunity = deals.create(
            ctx,
            fields,
            source_campaign_id=row["campaign_id"] if attribution else None,
            then=then,
        )
    after = store.get_record(ctx.workspace_id, "crm_campaign_members", member_id) or row
    return {"member": base.present_members(ws, [after])[0], "opportunity": opportunity}
