"""Records — spec 007 FR-CRM-CO-01 to -03, FR-CRM-PER-01, FR-CRM-TASK-01,
-02, FR-CRM-NOTE-01 to -03, FR-CRM-LINK-01 to -03, FR-CRM-DUP-01, -02,
FR-CRM-DEL-01, -02, -04, FR-CRM-PIPE-01, FR-CRM-REC-01 to -04; (1b)
FR-CRM-CAMP-01, -05, FR-CRM-CO-02; Q-57, Q-63; contracts §2.5; plan §5.3.

Generic create, update, soft delete (with a `deleted_group`), restore,
permanent delete and list, each validated from the field registry
(`domain/crm/objects.py`) and written in one `crm_apply` or `crm_purge` call,
so the change and its audit rows land together (FR-CRM-AUD-06). Companies,
people, tasks, notes and (1b) campaigns are created and edited here; deals
in `deals.py`, whose stage moves have rules of their own, and read, deleted
and restored here like every other record. A campaign's members are
`campaigns.py`'s; a campaign page here carries its figures.

Every read is of the caller's workspace, and a record of another workspace,
or one in the Trash, is "No such company." (404), never "forbidden"
(FR-CRM-WS-02, FR-CRM-DEL-01).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from app.domain.calendar import today_in_company_tz
from app.domain.crm import campaigns as campaign_rules
from app.domain.crm import dedupe, objects, pipeline
from app.domain.tasks import delegation as task_delegation
from app.domain.tasks import recurrence as task_recurrence
from app.domain.tasks import rollout as task_rollout
from app.domain.tasks import rules as task_rules
from app.domain.tasks import visibility as task_visibility
from app.domain.tasks.visibility import TaskFacts
from app.schemas.crm import (
    CampaignFields,
    CampaignUpdate,
    CompanyFields,
    CompanyUpdate,
    DriveLinkIn,
    NoteFields,
    NoteUpdate,
    OpportunityCreate,
    OpportunityUpdate,
    PersonFields,
    PersonUpdate,
    TaskFields,
    TaskUpdate,
)
from app.services import supabase as db
from app.services.crm import CrmRefused, flags, notifications, store, tva
from app.services.crm import present as show
from app.services.crm.context import actor
from app.services.tasks import notices as task_notices
from app.services.tasks import store as task_store
from app.services.tasks import viewer as task_viewer

#: FR-CRM-DEL-01.
TRASH_DAYS = 90
#: FR-CRM-DUP-03 — how far `get` follows merged ids (A into B, B into C...).
MERGE_HOPS = 10
MERGED_NO_RESTORE = "This company was merged into another; open the survivor instead."
#: Ids per read by id: they travel in the URL.
ID_BATCH = 100


@dataclass(frozen=True)
class Kind:
    kind: str
    table: str
    noun: str
    create_model: type[BaseModel]
    update_model: type[BaseModel]


KINDS = {
    "company": Kind("company", "crm_companies", "company", CompanyFields, CompanyUpdate),
    "person": Kind("person", "crm_people", "person", PersonFields, PersonUpdate),
    "opportunity": Kind(
        "opportunity", "crm_opportunities", "deal", OpportunityCreate, OpportunityUpdate
    ),
    "task": Kind("task", "tasks", "task", TaskFields, TaskUpdate),
    "note": Kind("note", "crm_notes", "note", NoteFields, NoteUpdate),
    "campaign": Kind("campaign", "crm_campaigns", "campaign", CampaignFields, CampaignUpdate),
}
#: URL segment → kind (contracts §2.5 "Objects in paths").
PLURALS = {
    "companies": "company",
    "people": "person",
    "opportunities": "opportunity",
    "tasks": "task",
    "notes": "note",
    "campaigns": "campaign",
}
#: The record tables a soft delete, restore or purge may touch, in purge
#: order: what points at a record goes before it (a deal before its company).
TRASH_TABLES = (
    "crm_drive_links",
    "crm_notes",
    "tasks",
    "crm_campaigns",
    "crm_opportunities",
    "crm_people",
    "crm_companies",
)
#: A task's or note's link table, by kind.
LINKS_OF = {"task": "task_links", "note": "crm_note_links"}
#: The records whose page has Tasks and Notes tabs (FR-CRM-REC-03).
SHOW_TABS = objects.TASK_NOTE_TARGETS
#: 1b: the records whose page has an Emails tab (FR-CRM-REC-03).
EMAIL_TABS = ("company", "person", "opportunity")


def no_such(kind: str) -> CrmRefused:
    return CrmRefused(404, f"No such {show.NOUNS.get(kind, 'record')}.")


def _uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# What a request may say
# ---------------------------------------------------------------------------


def parse(kind: str, payload: Any, *, creating: bool) -> dict[str, Any]:
    """The request's fields, refused in one sentence when wrong.

    System-written fields are refused first and by name (FR-CRM-CO-03), so a
    seller or an MCP caller learns why rather than "extra fields not
    permitted". A field left out is not in the result; `null` clears.
    """
    if not isinstance(payload, dict):
        raise CrmRefused(422, "Send the record's fields as a JSON object.")
    why = objects.system_refusal(kind, set(payload))
    if why:
        raise CrmRefused(422, why)
    spec = KINDS[kind]
    model = spec.create_model if creating else spec.update_model
    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        if first.get("type") == "extra_forbidden":
            raise CrmRefused(422, f"{where} is not a field of a {spec.noun}.") from None
        raise CrmRefused(422, f"{where}: {first.get('msg', 'not valid')}.") from None
    # Only the fields sent, but nested defaults kept (a phone's +91).
    values = {
        k: v for k, v in parsed.model_dump(mode="json").items() if k in parsed.model_fields_set
    }
    # Columns that are never NULL: clearing one empties it.
    for name, empty in _EMPTY.items():
        if name in values and values[name] is None:
            values[name] = empty
    return values


def _a(noun: str) -> str:
    return f"{'an' if noun[0] in 'aeiou' else 'a'} {noun}"


#: NOT NULL columns, and what clearing them means.
_EMPTY: dict[str, Any] = {
    "entities": [],
    "channel_ids": [],
    "additional_emails": [],
    "phones": [],
    "do_not_contact": False,
    "tags": [],
    "priority": "normal",
}


@dataclass
class Workspace:
    """The workspace facts every write and answer needs, read once."""

    ctx: Any
    facts: show.Facts
    members: set[str]

    @property
    def id(self) -> str:
        return self.ctx.workspace_id


def load(ctx: Any) -> Workspace:
    options = {o["id"]: o for o in store.list_options(ctx.workspace_id)}
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    members = {m["user_id"] for m in store.list_members(ctx.workspace_id)}
    return Workspace(ctx, show.Facts(options=options, names=names, role=ctx.role), members)


def check_fields(ws: Workspace, kind: str, values: dict[str, Any], existing: dict | None) -> None:
    """FR-CRM-CO-01, FR-CRM-PER-01 — every rule a field's note states."""
    ctx = ws.ctx
    present = {k for k, v in values.items() if v not in (None, [], {}, "")}
    why = objects.scope_refusal(
        kind,
        present,
        has_entity=bool(ctx.workspace.get("has_entity")),
        tva_linked=bool(ctx.workspace.get("tva_linked")),
        workspace=ctx.workspace["name"],
    )
    if why:
        raise CrmRefused(422, why)

    if existing is None:
        why = objects.required_refusal(kind, values)
    elif any(f.required and f.name in values for f in objects.FIELDS[kind]):
        why = objects.required_refusal(kind, {**existing, **values})
    else:
        why = None
    if why:
        raise CrmRefused(422, why)

    if "lead_stage" in values and (
        existing is None or values["lead_stage"] != existing.get("lead_stage")
    ):
        why = objects.lead_stage_refusal(
            values["lead_stage"] or "",
            creating=existing is None,
            account_type_key=account_type_key_of(ws, values, existing),
        )
        if why:
            raise CrmRefused(422, why)

    for f in objects.FIELDS[kind]:
        if f.name not in values or not f.list_key or not f.name.endswith(("_id", "_ids")):
            continue
        chosen = values[f.name]
        ids = chosen if isinstance(chosen, list) else [chosen] if chosen else []
        before = (existing or {}).get(f.name)
        kept = set(before if isinstance(before, list) else [before] if before else [])
        for option_id in ids:
            option = ws.facts.options.get(option_id)
            if option is None or option["list_key"] != f.list_key:
                raise CrmRefused(422, f"That {f.label.lower()} is not an option in this workspace.")
            if not option["is_active"] and option_id not in kept:
                raise CrmRefused(
                    422, f"“{option['label']}” is no longer offered for {f.label.lower()}."
                )
        if isinstance(chosen, list) and len(set(chosen)) != len(chosen):
            raise CrmRefused(422, f"{f.label} lists the same option twice.")

    if kind == "company" and existing is not None and "account_type_id" in values:
        why = objects.account_type_refusal(
            values.get("lead_stage", existing.get("lead_stage")),
            account_type_key_of(ws, values, existing),
        )
        if why:
            raise CrmRefused(422, why)

    if values.get("owner_id") and values["owner_id"] not in ws.members:
        raise CrmRefused(422, "The owner must be a member of this workspace.")

    for name in ("website", "linkedin"):
        links = values.get(name) or {}
        for link in [links.get("primary"), *(links.get("others") or [])]:
            if link:
                why = objects.url_refusal(link["url"])
                if why:
                    raise CrmRefused(422, why)


def account_type_key_of(ws: Workspace, values: dict[str, Any], existing: dict | None) -> str | None:
    """The system key of a company's account type once this write lands."""
    option_id = (
        values["account_type_id"]
        if "account_type_id" in values
        else (existing or {}).get("account_type_id")
    )
    option = ws.facts.options.get(option_id or "")
    return option.get("system_key") if option else None


def open_deal_company_ops(ws: Workspace, company: dict[str, Any]) -> list[dict[str, Any]]:
    """FR-CRM-PIPE-04 — an open deal sits on a company at Qualified or later,
    never a Disqualified one, and a Qualified company it moves onto goes to
    Deal open in the same write. The ops for that, or the refusal."""
    labelled = {
        **company,
        "lead_stage_label": ws.facts.fixed_label("company.lead_stage", company["lead_stage"]),
    }
    changes, why = pipeline.company_effect_on_open(labelled)
    if why:
        raise CrmRefused(422, why)
    if not changes:
        return []
    return [
        {
            "op": "update",
            "table": "crm_companies",
            "id": company["id"],
            "expect_updated_at": company["updated_at"],
            "values": changes,
        }
    ]


def _lead_stage_follows_deals(ws: Workspace, row: dict[str, Any], values: dict[str, Any]) -> None:
    """Contracts §2.5.1, FR-CRM-PIPE-04, -05 — Deal open, Customer and
    Partner follow from the deals: while the company has an open deal, its
    lead stage is not moved off them by hand."""
    if "lead_stage" not in values or values["lead_stage"] == row.get("lead_stage"):
        return
    if row.get("lead_stage") not in ("deal_open", "customer", "partner"):
        return
    deals = store.list_records(
        ws.id,
        "crm_opportunities",
        filters={"company_id": row["id"]},
        any_of={"stage": list(pipeline.OPEN)},
        limit=1,
    )
    if deals:
        raise CrmRefused(422, f"{row['name']} has open deals; its lead stage follows them.")


def _domains(
    ws: Workspace, values: dict[str, Any], company_id: str | None
) -> list[tuple[str, bool]] | None:
    """The company's domains as asked, normalised, each checked against every
    other company of the workspace, Trash included (FR-CRM-DUP-01). None
    when the request does not touch them; a PATCH that sends only one of
    `domain` and `additional_domains` keeps the other as it is."""
    asked_primary, asked_extra = "domain" in values, "additional_domains" in values
    if not (asked_primary or asked_extra):
        return None
    primary = values.pop("domain", None)
    extra = values.pop("additional_domains", None) or []
    if company_id is not None:
        current = store.list_domains(ws.id, company_ids=[company_id])
        if not asked_primary:
            primary = next((d["domain"] for d in current if d["is_primary"]), None)
        if not asked_extra:
            extra = [d["domain"] for d in current if not d["is_primary"]]
    wanted: list[tuple[str, bool]] = []
    for text, is_primary in [(primary, True)] + [(d, False) for d in extra]:
        if text is None or not str(text).strip():
            continue
        domain = dedupe.normalise_domain(str(text))
        if domain is None:
            raise CrmRefused(422, f"“{str(text)[:80]}” is not a domain, such as acme.com.")
        if any(domain == w for w, _ in wanted):
            raise CrmRefused(422, dedupe.repeated_domain(domain))
        wanted.append((domain, is_primary))
    taken = [
        d
        for d in store.list_domains(ws.id, domains=[w for w, _ in wanted])
        if d["company_id"] != company_id
    ]
    if taken:
        owner = store.get_record(ws.id, "crm_companies", taken[0]["company_id"]) or {}
        raise CrmRefused(
            409,
            dedupe.domain_clash(
                taken[0]["domain"],
                owner.get("name", "another company"),
                in_trash=bool(owner.get("deleted_at")),
            ),
        )
    return wanted


def _emails(ws: Workspace, values: dict[str, Any], person_id: str | None) -> None:
    """FR-CRM-DUP-01 — a primary email belongs to one person, Trash included.
    Emails are kept in lower case."""
    if values.get("additional_emails"):
        values["additional_emails"] = list(
            dict.fromkeys(dedupe.normalise_email(e) for e in values["additional_emails"])
        )
    email = values.get("primary_email")
    if not email:
        return
    values["primary_email"] = email = dedupe.normalise_email(email)
    clash = [
        p
        for p in store.list_records(
            ws.id, "crm_people", filters={"primary_email": email}, deleted=None, limit=2
        )
        if p["id"] != person_id
    ]
    if clash:
        raise CrmRefused(
            409,
            dedupe.email_clash(
                email,
                dedupe.full_name(clash[0]["first_name"], clash[0].get("last_name")),
                in_trash=bool(clash[0].get("deleted_at")),
            ),
        )


def _campaign_values(ws: Workspace, values: dict[str, Any], existing: dict | None) -> None:
    """Spec §6.6 — the dates in order, and a budget sent stored with its INR
    value at the rate for this month or the latest earlier one
    (FR-CRM-FX-03); a rate edited later never recomputes it."""
    after = {**(existing or {}), **values}
    why = campaign_rules.dates_refusal(after.get("starts_on"), after.get("ends_on"))
    if why:
        raise CrmRefused(422, why)
    if "budget" not in values and "budget_currency" not in values:
        return
    currency = after.get("budget_currency")
    if "budget" in values and values["budget"] is None:
        # Clearing the budget clears its stored currency with it, as a
        # deal's amount does; only a currency sent with it is refused.
        currency = values.get("budget_currency")
    found = campaign_rules.budget_values(
        store.list_fx_rates(ws.id),
        after.get("budget"),
        currency,
        today_in_company_tz(),
        ws.ctx.workspace["name"],
    )
    if isinstance(found, str):
        raise CrmRefused(422, found)
    values.update(found)


def _company_ref(ws: Workspace, values: dict[str, Any]) -> None:
    """A person's company is a live company of this workspace."""
    company_id = values.get("company_id")
    if not company_id:
        return
    row = store.get_record(ws.id, "crm_companies", company_id) if _uuid(company_id) else None
    if row is None or row.get("deleted_at"):
        raise CrmRefused(404, "No such company.")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def live(ws_id: str, kind: str, record_id: str) -> dict[str, Any]:
    """The record, or "No such company." for one that is absent, in another
    workspace or in the Trash."""
    row = store.get_record(ws_id, KINDS[kind].table, record_id) if _uuid(record_id) else None
    if row is None or row.get("deleted_at"):
        raise no_such(kind)
    return row


def by_id(ws_id: str, table: str, ids: set[str] | list[str]) -> dict[str, dict[str, Any]]:
    """Records of one table by id, Trash included (a name stays readable)."""
    wanted = sorted({i for i in ids if i})
    found: dict[str, dict[str, Any]] = {}
    # In batches: the ids go into the URL, which PostgREST caps.
    for i in range(0, len(wanted), ID_BATCH):
        batch = wanted[i : i + ID_BATCH]
        for row in store.list_records(
            ws_id, table, any_of={"id": batch}, deleted=None, limit=len(batch)
        ):
            found[row["id"]] = row
    return found


def link_names(ws_id: str, links: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """The name of every record some task or note links to."""
    names: dict[tuple[str, str], str] = {}
    for record_type, table in (
        ("company", "crm_companies"),
        ("person", "crm_people"),
        ("opportunity", "crm_opportunities"),
        ("campaign", "crm_campaigns"),
    ):
        ids = {link["record_id"] for link in links if link["record_type"] == record_type}
        for rid, row in by_id(ws_id, table, ids).items():
            names[(record_type, rid)] = (
                dedupe.full_name(row["first_name"], row.get("last_name"))
                if record_type == "person"
                else row["name"]
            )
    return names


def _task_projects(ws: Workspace, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """008 FR-TASK-VIS-07 — a CRM task's TVA project, by task, for a member
    who can see that project; a member who cannot gets none at all."""
    found = [
        lk
        for lk in task_store.list_links([r["id"] for r in rows])
        if lk["record_type"] == "tva_project"
    ]
    if not found:
        return {}
    me = task_viewer.load(ws.ctx.user)
    projects = {p["id"]: p for p in task_store.get_projects([lk["record_id"] for lk in found])}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        link = next((lk for lk in found if lk["task_id"] == row["id"]), None)
        project = projects.get(link["record_id"]) if link else None
        if project is None:
            continue
        task = TaskFacts(
            id=row["id"],
            workspace_id=row.get("workspace_id"),
            created_by=row.get("created_by"),
            assignee_id=row.get("assignee_id"),
            project_id=project["id"],
        )
        if task_visibility.project_field(me, task) == "full":
            out[row["id"]] = {"id": project["id"], "name": project["name"]}
    return out


def task_parents(user: Any, ids: set[str]) -> dict[str, dict[str, Any] | None]:
    """008 FR-TASK-DATA-09, FR-TASK-VIS-06 — each parent task as `user` may
    see it: its id and title, or "a private task" with neither. A parent no
    longer in the table cannot be checked, so it is private too."""
    wanted = sorted(i for i in ids if i)
    if not wanted:
        return {}
    me = task_viewer.load(user)
    found = {p["id"]: p for p in task_store.get_tasks(wanted)}
    projects: dict[str, str] = {
        lk["task_id"]: lk["record_id"]
        for lk in task_store.list_links(list(found))
        if lk["record_type"] == "tva_project"
    }
    out: dict[str, dict[str, Any] | None] = {}
    for pid in wanted:
        parent = found.get(pid)
        if parent is None:
            out[pid] = {"private": True}
            continue
        facts = TaskFacts(
            id=parent["id"],
            workspace_id=parent.get("workspace_id"),
            created_by=parent.get("created_by"),
            assignee_id=parent.get("assignee_id"),
            project_id=projects.get(pid),
            deleted_at=parent.get("deleted_at"),
            parent_id=parent.get("parent_id"),
        )
        out[pid] = task_visibility.parent_field(me, facts, parent.get("title"))
    return out


def present_rows(ws: Workspace, kind: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if kind == "company":
        domains = store.list_domains(ws.id, company_ids=[r["id"] for r in rows])
        return [show.company(r, ws.facts, domains) for r in rows]
    if kind == "opportunity":
        companies = by_id(ws.id, "crm_companies", {r["company_id"] for r in rows})
        people = by_id(ws.id, "crm_people", {r.get("point_of_contact_id") or "" for r in rows})
        projects = {
            b["source_opportunity_id"]: _project(b)
            for b in (
                tva.blocks(opportunity_ids=[r["id"] for r in rows])
                if rows and ws.ctx.workspace.get("tva_linked")
                else []
            )
        }
        sources = by_id(ws.id, "crm_campaigns", {r.get("source_campaign_id") or "" for r in rows})
        return [show.opportunity(r, ws.facts, companies, people, projects, sources) for r in rows]
    if kind == "campaign":
        found = campaign_figures(ws.id, rows)
        return [show.campaign(r, ws.facts, found.get(r["id"])) for r in rows]
    if kind == "campaign_member":
        return present_members(ws, rows)
    if kind in LINKS_OF:
        links = store.list_links(ws.id, LINKS_OF[kind], owner_ids=[r["id"] for r in rows])
        names = link_names(ws.id, links)
        owner = "task_id" if kind == "task" else "note_id"
        if kind == "task":
            projects = _task_projects(ws, rows)
            parents = task_parents(ws.ctx.user, {r.get("parent_id") or "" for r in rows})
            return [
                show.task(
                    r,
                    ws.facts,
                    [lk for lk in links if lk[owner] == r["id"]],
                    names,
                    projects.get(r["id"]),
                    parents.get(r.get("parent_id") or ""),
                )
                for r in rows
            ]
        mentions = store.list_mentions(ws.id, [r["id"] for r in rows])
        return [
            show.note(
                r,
                ws.facts,
                [lk for lk in links if lk[owner] == r["id"]],
                names,
                [m for m in mentions if m["note_id"] == r["id"]],
            )
            for r in rows
        ]
    companies = by_id(ws.id, "crm_companies", {r.get("company_id") or "" for r in rows})
    return [show.person(r, ws.facts, companies) for r in rows]


def shown_members(ws_id: str, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The members whose company and person are out of the Trash: a record
    in the Trash leaves its campaigns' boards and figures, as it leaves
    every list, and is back with its restore (adding it is refused too)."""
    companies = by_id(ws_id, "crm_companies", {m.get("company_id") or "" for m in members})
    people = by_id(ws_id, "crm_people", {m.get("person_id") or "" for m in members})

    def trashed(found: dict[str, dict[str, Any]], record_id: str | None) -> bool:
        return bool(record_id) and bool((found.get(record_id) or {}).get("deleted_at"))

    return [
        m
        for m in members
        if not trashed(companies, m.get("company_id")) and not trashed(people, m.get("person_id"))
    ]


def campaign_figures(ws_id: str, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """FR-CRM-CAMP-05 — each campaign's figures from its live members and
    the deals they converted to."""
    if not rows:
        return {}
    members = shown_members(
        ws_id, store.campaign_members(ws_id, column="campaign_id", ids=[r["id"] for r in rows])
    )
    deals = by_id(
        ws_id,
        "crm_opportunities",
        {m["converted_opportunity_id"] for m in members if m.get("converted_opportunity_id")},
    )
    return {
        r["id"]: campaign_rules.figures(
            [m for m in members if m["campaign_id"] == r["id"]], deals, r.get("budget_in_inr")
        )
        for r in rows
    }


def present_members(ws: Workspace, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Campaign members with their campaign, company, person and deal named
    (Trash included, so a name stays readable)."""
    people = by_id(ws.id, "crm_people", {r.get("person_id") or "" for r in rows})
    companies = by_id(
        ws.id,
        "crm_companies",
        {r.get("company_id") or "" for r in rows}
        | {p.get("company_id") or "" for p in people.values()},
    )
    campaigns = by_id(ws.id, "crm_campaigns", {r["campaign_id"] for r in rows})
    deals = by_id(
        ws.id, "crm_opportunities", {r.get("converted_opportunity_id") or "" for r in rows}
    )
    return [
        show.campaign_member(
            r, ws.facts, campaigns=campaigns, companies=companies, people=people, deals=deals
        )
        for r in rows
    ]


def _memberships(ws: Workspace, column: str, record_id: str) -> list[dict[str, Any]]:
    """FR-CRM-CO-02, REC-03 Related — a company's or person's campaign
    memberships, live campaigns only."""
    members = store.campaign_members(ws.id, column=column, ids=[record_id])
    campaigns = by_id(ws.id, "crm_campaigns", {m["campaign_id"] for m in members})
    live = [m for m in members if not (campaigns.get(m["campaign_id"]) or {}).get("deleted_at")]
    return present_members(ws, live)


def _project(found: dict[str, Any]) -> dict[str, Any]:
    """A project block without the link it was found by."""
    return {k: v for k, v in found.items() if k not in ("source_opportunity_id", "crm_company_id")}


def list_records(
    ctx: Any,
    kind: str,
    *,
    q: str | None,
    limit: int,
    after: str | None,
    filters: dict[str, Any] | None = None,
    open_only: bool = False,
) -> dict:
    """Contracts §2.5 `GET /crm/{object}` — newest first, paged by `next`.
    The Tasks page narrows tasks to an assignee and to the open ones."""
    ws = load(ctx)
    offset = int(after) if after and after.isdigit() else 0
    rows = store.list_records(
        ctx.workspace_id,
        KINDS[kind].table,
        q=q,
        filters=filters,
        any_of={"status": list(task_rules.OPEN)} if open_only else None,
        limit=limit + 1,
        offset=offset,
    )
    more = len(rows) > limit
    return {
        "rows": present_rows(ws, kind, rows[:limit]),
        "next": str(offset + limit) if more else None,
    }


def get(ctx: Any, kind: str, record_id: str) -> dict[str, Any]:
    """Contracts §2.5 `GET /crm/{object}/{id}` — the record page in one call:
    the record, what is related, the last ten timeline entries and the Drive
    links. A merged company answers with its survivor (FR-CRM-DUP-03)."""
    ws = load(ctx)
    row = (
        store.get_record(ctx.workspace_id, KINDS[kind].table, record_id)
        if _uuid(record_id)
        else None
    )
    merged_from = None
    if row and kind == "company" and row.get("merged_into_id") and row.get("deleted_at"):
        merged_from = {"id": row["id"], "name": row["name"]}
        # A survivor merged later itself: follow the chain to the live one.
        for _ in range(MERGE_HOPS):
            if not (row and row.get("merged_into_id") and row.get("deleted_at")):
                break
            row = store.get_record(ctx.workspace_id, "crm_companies", row["merged_into_id"])
    if row is None or row.get("deleted_at"):
        raise no_such(kind)

    record = present_rows(ws, kind, [row])[0]
    related = _related(ws, kind, row, record)
    links = store.list_records(
        ctx.workspace_id,
        "crm_drive_links",
        filters={"record_type": kind, "record_id": row["id"]},
        limit=200,
    )
    answer = {
        "record": record,
        "related": related,
        "timeline_head": timeline(ctx, kind, row["id"], ws=ws, limit=10),
        "links": [show.drive_link(link, ws.facts) for link in links],
    }
    if kind in SHOW_TABS:
        answer["tasks"] = present_rows(ws, "task", linked(ws.id, "task", kind, row["id"]))
        answer["notes"] = present_rows(ws, "note", linked(ws.id, "note", kind, row["id"]))
    if kind in EMAIL_TABS:
        # 1b (S10): the latest threads, for `crm_get` (FR-CRM-MCP-02); the
        # Emails tab pages through the rest. Imported here: email sync
        # reads records through this module.
        from app.services.crm import email_sync

        answer["emails"] = email_sync.recent(ctx, kind, row["id"])
    if merged_from:
        answer["merged_from"] = merged_from
    return answer


def linked(ws_id: str, of: str, record_type: str, record_id: str) -> list[dict[str, Any]]:
    """The live tasks or notes linked to a record: open and waiting tasks
    first, then by due date; notes newest first."""
    links = store.list_links(ws_id, LINKS_OF[of], record_type=record_type, record_ids=[record_id])
    owner = "task_id" if of == "task" else "note_id"
    ids = sorted({lk[owner] for lk in links})
    if not ids:
        return []
    rows = store.list_records(ws_id, KINDS[of].table, any_of={"id": ids}, limit=len(ids))
    if of == "task":
        return sorted(
            rows,
            key=lambda r: (
                r["status"] not in task_rules.OPEN,
                r.get("due_on") or "9999-12-31",
                r["title"],
            ),
        )
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def _deals_of(ws: Workspace, **filters: Any) -> list[dict[str, Any]]:
    rows = store.list_records(ws.id, "crm_opportunities", filters=filters, limit=200)
    return present_rows(ws, "opportunity", rows)


def _related(ws: Workspace, kind: str, row: dict[str, Any], record: dict[str, Any]) -> dict:
    """FR-CRM-REC-03 Related: a company's people and deals; a person's company
    and the deals they are on; a deal's company, point of contact and other
    contacts. A Viewer's deals carry no amounts (the presenter)."""
    if kind == "company":
        people = store.list_records(
            ws.id, "crm_people", filters={"company_id": row["id"]}, limit=200
        )
        out: dict[str, Any] = {
            "people": [show.person(p, ws.facts) for p in people],
            "opportunities": _deals_of(ws, company_id=row["id"]),
            # 1b (FR-CRM-CO-02): its campaign memberships.
            "campaign_members": _memberships(ws, "company_id", row["id"]),
        }
        if ws.ctx.workspace.get("tva_linked"):
            # FR-CRM-CO-02: a Labs company's TVA projects, with status and
            # phase dates, for every role.
            out["projects"] = [_project(b) for b in tva.blocks(company_ids=[row["id"]])]
        return out
    if kind == "person":
        on = {c["opportunity_id"] for c in store.list_contacts(ws.id, person_ids=[row["id"]])}
        deals = {d["id"]: d for d in _deals_of(ws, point_of_contact_id=row["id"])}
        if on - set(deals):
            more = store.list_records(
                ws.id, "crm_opportunities", any_of={"id": sorted(on - set(deals))}, limit=200
            )
            deals.update({d["id"]: d for d in present_rows(ws, "opportunity", more)})
        return {
            "company": (
                {"id": record["company_id"], "name": record["company_name"]}
                if record.get("company_id")
                else None
            ),
            "opportunities": sorted(deals.values(), key=lambda d: d["updated_at"], reverse=True),
            "campaign_members": _memberships(ws, "person_id", row["id"]),
        }
    if kind == "opportunity":
        contacts = store.list_contacts(ws.id, opportunity_ids=[row["id"]])
        people = by_id(ws.id, "crm_people", {c["person_id"] for c in contacts})
        return {
            "company": {"id": record["company_id"], "name": record["company_name"]},
            "point_of_contact": (
                {"id": record["point_of_contact_id"], "name": record["point_of_contact_name"]}
                if record.get("point_of_contact_id")
                else None
            ),
            "contacts": [show.contact(c, ws.facts, people) for c in contacts],
            # FR-CRM-REC-03: a Labs deal's TVA project, as on the record.
            "project": record.get("project"),
        }
    if kind == "campaign":
        # FR-CRM-CAMP-05: the deals its members converted to. The board
        # reads the members themselves (`GET /crm/campaigns/{id}/members`).
        members = shown_members(
            ws.id, store.campaign_members(ws.id, column="campaign_id", ids=[row["id"]])
        )
        ids = sorted(
            {m["converted_opportunity_id"] for m in members if m.get("converted_opportunity_id")}
        )
        deals = [
            d for d in by_id(ws.id, "crm_opportunities", ids).values() if not d.get("deleted_at")
        ]
        return {"opportunities": present_rows(ws, "opportunity", deals)}
    return {}


def timeline(
    ctx: Any,
    kind: str,
    record_id: str,
    *,
    ws: Workspace | None = None,
    types: set[str] | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """FR-CRM-REC-03, -04, FR-CRM-AUD-05 — what happened to the record,
    newest first, each entry naming its actor and channel."""
    ws = ws or load(ctx)
    live(ctx.workspace_id, kind, record_id)
    links = {
        link["id"]: link
        for link in store.list_records(
            ctx.workspace_id,
            "crm_drive_links",
            filters={"record_type": kind, "record_id": record_id},
            deleted=None,
            limit=500,
        )
    }
    around = show.Around(links=links)
    related: dict[str, list[str]] = {"crm_drive_links": sorted(links)}
    if kind in SHOW_TABS:
        for of, attr in (("task", "tasks"), ("note", "notes")):
            owner = "task_id" if of == "task" else "note_id"
            ids = {
                lk[owner]
                for lk in store.list_links(
                    ctx.workspace_id, LINKS_OF[of], record_type=kind, record_ids=[record_id]
                )
            }
            setattr(around, attr, by_id(ctx.workspace_id, KINDS[of].table, ids))
            related[KINDS[of].table] = sorted(ids)
    if kind == "opportunity":
        contacts = store.list_contacts(ctx.workspace_id, opportunity_ids=[record_id])
        around.people = by_id(ctx.workspace_id, "crm_people", {c["person_id"] for c in contacts})
    # A type filter, and a Viewer's dropped entries, leave a batch of audit
    # rows short, so batches are read until `limit` entries are found or the
    # history ends. Every row skipped past the last entry shown matched
    # nothing, so that entry's id stays a sound `before` for the next page.
    entries: list[dict[str, Any]] = []
    batch = max(limit, 200)
    while len(entries) < limit:
        rows = store.list_timeline(
            ctx.workspace_id,
            KINDS[kind].table,
            record_id,
            record_type=kind,
            related=related,
            before=before,
            limit=batch,
        )
        found = show.timeline(rows, kind, ws.facts, around=around)
        # Spec 009 FR-CRMAI-REV-05: an entry written under an approved
        # suggestion says so ("approved AI suggestion").
        suggested = {r["id"]: r.get("suggestion_id") for r in rows}
        for entry in found:
            if suggested.get(entry["id"]):
                entry["suggestion_id"] = suggested[entry["id"]]
                entry["approved_suggestion"] = True
        if kind == "task":
            _parents_shown(ws, found)
        entries += [e for e in found if not types or e["type"] in types]
        if len(rows) < batch:
            break
        before = rows[-1]["id"]
    return entries[:limit]


def _parents_shown(ws: Workspace, entries: list[dict[str, Any]]) -> None:
    """008 FR-TASK-DATA-09 — a change of a task's parent names the parent by
    its title, or "a private task" when the reader may not see it; never its
    id."""
    changes = [c for e in entries for c in e["changes"] if c["field"] == "parent_id"]
    parents = task_parents(
        ws.ctx.user, {c[side] or "" for c in changes for side in ("before", "after")}
    )
    for change in changes:
        for side in ("before", "after"):
            shown = parents.get(change[side] or "")
            if shown is not None:
                change[side] = "a private task" if shown.get("private") else shown.get("title")


def duplicates(
    ctx: Any, kind: str, *, name: str | None, domain: str | None, email: str | None
) -> dict[str, Any]:
    """FR-CRM-DUP-02 — the exact clash, if any, and names alike."""
    ws_id = ctx.workspace_id
    exact = None
    if kind == "company" and domain:
        normal = dedupe.normalise_domain(domain)
        found = store.list_domains(ws_id, domains=[normal]) if normal else []
        if found:
            owner = store.get_record(ws_id, "crm_companies", found[0]["company_id"]) or {}
            exact = {
                "id": found[0]["company_id"],
                "name": owner.get("name"),
                "field": "domain",
                "value": normal,
                "in_trash": bool(owner.get("deleted_at")),
            }
    if kind == "person" and email:
        normal = dedupe.normalise_email(email)
        found = store.list_records(
            ws_id, "crm_people", filters={"primary_email": normal}, deleted=None, limit=1
        )
        if found:
            exact = {
                "id": found[0]["id"],
                "name": dedupe.full_name(found[0]["first_name"], found[0].get("last_name")),
                "field": "primary_email",
                "value": normal,
                "in_trash": bool(found[0].get("deleted_at")),
            }
    similar = []
    if name and name.strip():
        similar = [
            {"id": s["id"], "name": s["name"], "similarity": round(float(s["similarity"]), 2)}
            for s in store.similar(ws_id, kind, name.strip())
            if float(s["similarity"]) >= dedupe.THRESHOLD
        ]
    return {"exact": exact, "similar": similar}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def create(ctx: Any, kind: str, payload: Any, *, check_duplicates: bool) -> dict[str, Any]:
    """Contracts §2.5 `POST /crm/{object}`. With `check_duplicates` and names
    alike, nothing is written and the possible duplicates come back instead
    (FR-CRM-DUP-02): the web app shows them, and an MCP caller asks the
    person before calling again without it."""
    if kind in LINKS_OF:
        return _create_task_or_note(ctx, kind, payload)
    ws = load(ctx)
    values = parse(kind, payload, creating=True)
    # An owner defaults to the person creating the record, always a member:
    # Viewers cannot create.
    if kind in ("company", "campaign") and not values.get("owner_id"):
        values["owner_id"] = ctx.user.id
    check_fields(ws, kind, values, None)
    domains: list[tuple[str, bool]] = []
    if kind == "company":
        values.setdefault("lead_stage", "new")
        domains = _domains(ws, values, None) or []
    elif kind == "campaign":
        _campaign_values(ws, values, None)
    else:
        _company_ref(ws, values)
        _emails(ws, values, None)

    if check_duplicates and kind in ("company", "person"):
        name = values.get("name") or dedupe.full_name(
            values.get("first_name"), values.get("last_name")
        )
        found = duplicates(ctx, kind, name=name, domain=None, email=None)["similar"]
        if found:
            return {"created": False, "duplicates": {"exact": None, "similar": found}}

    ops: list[dict[str, Any]] = [
        {"op": "insert", "table": KINDS[kind].table, "ref": "record", "values": values}
    ]
    ops += [
        {
            "op": "insert",
            "table": "crm_company_domains",
            "values": {"company_id": "$ref:record", "domain": domain, "is_primary": primary},
        }
        for domain, primary in domains
    ]
    done = store.apply(actor(ctx), ops)
    row = done[0]["row"]
    reflag(ws.id, kind, [row])
    notifications.quietly(
        notifications.assigned(ctx, kind, row["id"], record_name(kind, row), row.get("owner_id"))
    )
    return present_rows(ws, kind, [row])[0]


def update(ctx: Any, kind: str, record_id: str, payload: Any) -> dict[str, Any]:
    """Contracts §2.5 `PATCH /crm/{object}/{id}`. `expect_updated_at`, when
    sent, refuses a change made meanwhile (CRM09 → 409)."""
    if kind in LINKS_OF:
        return _update_task_or_note(ctx, kind, record_id, payload)
    ws = load(ctx)
    row = live(ctx.workspace_id, kind, record_id)
    values = parse(kind, payload, creating=False)
    expect = values.pop("expect_updated_at", None)
    check_fields(ws, kind, values, row)

    ops: list[dict[str, Any]] = []
    if kind == "company":
        _lead_stage_follows_deals(ws, row, values)
        wanted = _domains(ws, values, record_id)
        if wanted is not None:
            current = store.list_domains(ctx.workspace_id, company_ids=[record_id])
            keep = {(d["domain"], d["is_primary"]) for d in current} & set(wanted)
            ops += [
                {"op": "delete", "table": "crm_company_domains", "id": d["id"]}
                for d in current
                if (d["domain"], d["is_primary"]) not in keep
            ]
            ops += [
                {
                    "op": "insert",
                    "table": "crm_company_domains",
                    "values": {"company_id": record_id, "domain": d, "is_primary": p},
                }
                for d, p in wanted
                if (d, p) not in keep
            ]
    elif kind == "campaign":
        _campaign_values(ws, values, row)
    else:
        _company_ref(ws, values)
        _emails(ws, values, record_id)

    changes = {k: v for k, v in values.items() if row.get(k) != v}
    if not changes and not ops:
        return present_rows(ws, kind, [row])[0]
    # The record's own write goes first and carries the stale check. A change
    # to its domains alone still touches it, so `updated_at` moves and the
    # next stale check sees it; an `updated_by`-only change writes no audit
    # row of its own (026 trigger).
    main = {
        "op": "update",
        "table": KINDS[kind].table,
        "id": record_id,
        "values": changes or {"updated_by": ctx.user.id},
    }
    if expect:
        main["expect_updated_at"] = expect
    done = store.apply(actor(ctx), [main, *ops])
    after = done[0]["row"]
    reflag(ws.id, kind, [after])
    if "owner_id" in changes:
        notifications.quietly(
            notifications.assigned(
                ctx, kind, record_id, record_name(kind, after), changes["owner_id"]
            )
        )
    return present_rows(ws, kind, [after])[0]


def record_name(kind: str, row: dict[str, Any]) -> str:
    """What a notification calls a record."""
    if kind == "person":
        return dedupe.full_name(row["first_name"], row.get("last_name"))
    if kind in ("task",):
        return row["title"]
    if kind == "note":
        return "a note"
    return row["name"]


def reflag(ws_id: str, kind: str, rows: list[dict[str, Any]], *, links: list | None = None) -> None:
    """FR-CRM-FLAG-02 — after a write, recompute the flags it can change: the
    record's own, a deal's company's, and a task's or note's linked deals and
    companies (their activity and open tasks). Never fails the write."""
    touched: dict[str, set[str]] = {"companies": set(), "people": set(), "deals": set()}
    tasks: set[str] = set()
    for row in rows:
        if kind == "company":
            touched["companies"].add(row["id"])
        elif kind == "person":
            touched["people"].add(row["id"])
        elif kind == "opportunity":
            touched["deals"].add(row["id"])
            touched["companies"].add(row.get("company_id") or "")
        elif kind == "task":
            tasks.add(row["id"])
    if kind in LINKS_OF and rows:
        try:
            found = links
            if found is None:
                found = store.list_links(ws_id, LINKS_OF[kind], owner_ids=[r["id"] for r in rows])
            for key, ids in flags.linked_records(found).items():
                touched[key] |= ids
        except Exception:
            flags.logger.exception('{"event": "crm_flags_links_failed"}')
    flags.after_write(ws_id, tasks=tasks, **touched)


def _owns(row: dict[str, Any], user_id: str) -> bool:
    """FR-CRM-DEL-04 — the owner, or with no owner set, the creator."""
    owner = row.get("owner_id")
    return owner == user_id if owner else row.get("created_by") == user_id


def soft_delete(
    ctx: Any,
    kind: str,
    record_id: str,
    *,
    people: str | None = None,
    deals: str | None = None,
    move_deals_to: str | None = None,
) -> dict:
    """FR-CRM-DEL-01, -02, -04. A Member deletes only what they own (or, with
    no owner set, created); everything deleted in this one action shares a
    `deleted_group`, so a restore brings it back together. A company says
    what happens to its people (unlink or delete) and its deals (move to
    another company, or delete: a deal cannot be unlinked, Q-54)."""
    row = live(ctx.workspace_id, kind, record_id)
    if not ctx.may("soft_delete", owns=_owns(row, ctx.user.id)):
        raise CrmRefused(
            403, f"Only a workspace admin or its owner can delete this {show.NOUNS[kind]}."
        )
    if ctx.workspace.get("tva_linked"):
        _tva_delete_guard(ctx, kind, row)
    group = str(uuid.uuid4())
    ops: list[dict[str, Any]] = [
        {"op": "soft_delete", "table": KINDS[kind].table, "id": record_id, "deleted_group": group}
    ]
    if kind == "company":
        staff = store.list_records(
            ctx.workspace_id, "crm_people", filters={"company_id": record_id}, limit=2000
        )
        if staff and people is None:
            n = len(staff)
            raise CrmRefused(
                422,
                f"{row['name']} has {n} {'person' if n == 1 else 'people'}; say whether to "
                "unlink them or delete them too.",
            )
        # The people go to the Trash too, so each is held to the same rule:
        # a Member's company does not carry another seller's people with it.
        barred = [
            dedupe.full_name(p["first_name"], p.get("last_name"))
            for p in staff
            if not ctx.may("soft_delete", owns=_owns(p, ctx.user.id))
        ]
        if people == "delete" and barred:
            raise CrmRefused(
                403,
                f"Only a workspace admin or their owner can delete {', '.join(barred)}; "
                "unlink them instead, or ask a workspace admin.",
            )
        for p in staff:
            if people == "delete":
                ops.append(
                    {
                        "op": "soft_delete",
                        "table": "crm_people",
                        "id": p["id"],
                        "deleted_group": group,
                    }
                )
            else:
                ops.append(
                    {
                        "op": "update",
                        "table": "crm_people",
                        "id": p["id"],
                        "values": {"company_id": None},
                    }
                )
        ops += _company_deals(ctx, row, group, deals=deals, move_to=move_deals_to)
    store.apply(actor(ctx), ops)
    reflag(ctx.workspace_id, kind, [row])
    return {"deleted_group": group}


def _tva_delete_guard(ctx: Any, kind: str, row: dict[str, Any]) -> None:
    """FR-CRM-DEL-02, -03 — a deal linked to a TVA project, tentative or
    confirmed, is never deleted, and neither is a company with one, whatever
    was chosen for its deals: nothing is partly deleted."""
    if kind == "opportunity" and tva.project_of(row["id"]):
        raise CrmRefused(422, tva.HAS_PROJECT)
    if kind != "company":
        return
    deals = store.list_records(
        ctx.workspace_id, "crm_opportunities", filters={"company_id": row["id"]}, limit=2000
    )
    linked = {
        b["source_opportunity_id"]
        for b in (tva.blocks(opportunity_ids=[d["id"] for d in deals]) if deals else [])
    }
    names = sorted(d["name"] for d in deals if d["id"] in linked)
    if names:
        verb = "has" if len(names) == 1 else "have"
        raise CrmRefused(
            422,
            f"{row['name']} cannot be deleted: {_listed(names)} {verb} a TVA project. "
            "Mark the deal Closed Lost instead.",
        )


def _listed(names: list[str]) -> str:
    quoted = [f"“{n}”" for n in names]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _company_deals(
    ctx: Any, company: dict[str, Any], group: str, *, deals: str | None, move_to: str | None
) -> list[dict[str, Any]]:
    """FR-CRM-DEL-02 — a company's deals move to another company of the
    workspace or go to the Trash with it. A company with a deal linked to a
    TVA project is refused before this (`_tva_delete_guard`)."""
    rows = store.list_records(
        ctx.workspace_id, "crm_opportunities", filters={"company_id": company["id"]}, limit=2000
    )
    if not rows:
        return []
    if deals is None and move_to is None:
        n = len(rows)
        raise CrmRefused(
            422,
            f"{company['name']} has {n} {'deal' if n == 1 else 'deals'}; say whether to move "
            "them to another company or delete them too.",
        )
    if move_to is not None:
        target = (
            store.get_record(ctx.workspace_id, "crm_companies", move_to) if _uuid(move_to) else None
        )
        if target is None or target.get("deleted_at"):
            raise CrmRefused(404, "No such company.")
        if target["id"] == company["id"]:
            raise CrmRefused(422, "Move the deals to another company.")
        # FR-CRM-PIPE-04: open deals move only onto a company they could
        # have been opened on, and a Qualified one goes to Deal open.
        company_ops = (
            open_deal_company_ops(load(ctx), target)
            if any(d["stage"] in pipeline.OPEN for d in rows)
            else []
        )
        return [
            {
                "op": "update",
                "table": "crm_opportunities",
                "id": d["id"],
                "values": {"company_id": target["id"]},
            }
            for d in rows
        ] + company_ops
    barred = [d["name"] for d in rows if not ctx.may("soft_delete", owns=_owns(d, ctx.user.id))]
    if barred:
        raise CrmRefused(
            403,
            f"Only a workspace admin or their owner can delete {', '.join(barred)}; "
            "move them instead, or ask a workspace admin.",
        )
    return [
        {"op": "soft_delete", "table": "crm_opportunities", "id": d["id"], "deleted_group": group}
        for d in rows
    ]


def restore(ctx: Any, kind: str, record_id: str) -> dict[str, Any]:
    """FR-CRM-DEL-01, -02 — Admin only; brings back everything deleted with it."""
    ws = load(ctx)
    table = KINDS[kind].table
    row = store.get_record(ctx.workspace_id, table, record_id) if _uuid(record_id) else None
    if row is None or not row.get("deleted_at"):
        raise CrmRefused(404, f"No such {show.NOUNS[kind]} in the Trash.")
    if kind == "company" and row.get("merged_into_id"):
        raise CrmRefused(422, MERGED_NO_RESTORE)
    group = row.get("deleted_group")
    back: list[tuple[str, dict[str, Any]]] = []
    if group:
        for t in reversed(TRASH_TABLES):
            back += [
                (t, r)
                for r in store.list_records(
                    ctx.workspace_id, t, filters={"deleted_group": group}, deleted=True, limit=2000
                )
            ]
    if not any(r["id"] == record_id for _, r in back):
        back.insert(0, (table, row))
    ops: list[dict[str, Any]] = [{"op": "restore", "table": t, "id": r["id"]} for t, r in back]

    # A person trashed on their own keeps their company_id; if that company
    # went to the Trash later and is not coming back with them, the person
    # returns unlinked rather than pointing at a trashed company.
    coming = {r["id"] for t, r in back if t == "crm_companies"}
    people = [r for t, r in back if t == "crm_people"]
    wanted = sorted({p["company_id"] for p in people if p.get("company_id")} - coming)
    gone = {
        c["id"]
        for c in (
            store.list_records(
                ctx.workspace_id,
                "crm_companies",
                any_of={"id": wanted},
                deleted=None,
                limit=len(wanted),
            )
            if wanted
            else []
        )
        if c.get("deleted_at")
    }
    ops += [
        {"op": "update", "table": "crm_people", "id": p["id"], "values": {"company_id": None}}
        for p in people
        if p.get("company_id") in gone
    ]
    # 008 FR-TASK-DATA-05: a task comes back only with a CRM link left.
    tasks_back = [r["id"] for t, r in back if t == "tasks"]
    if tasks_back:
        linked_ids = {
            lk["task_id"]
            for lk in store.list_links(ctx.workspace_id, "task_links", owner_ids=tasks_back)
        }
        if any(t not in linked_ids for t in tasks_back):
            raise CrmRefused(422, NO_LINK_LEFT)
    # A deal cannot come back without its company (Company is required).
    deals = [r for t, r in back if t == "crm_opportunities"]
    held = sorted({d["company_id"] for d in deals} - coming)
    for company in by_id(ctx.workspace_id, "crm_companies", held).values():
        if company.get("deleted_at"):
            raise CrmRefused(
                422, f"This deal's company, {company['name']}, is in the Trash; restore it first."
            )
    done = store.apply(actor(ctx), ops)
    restored = [d["row"] for d in done if d["id"] == record_id][-1]
    reflag(ws.id, kind, [restored])
    return present_rows(ws, kind, [restored])[0]


def purge(ctx: Any, kind: str, record_id: str) -> dict[str, Any]:
    """FR-CRM-DEL-01 — Admin only, and only from the Trash."""
    row = (
        store.get_record(ctx.workspace_id, KINDS[kind].table, record_id)
        if _uuid(record_id)
        else None
    )
    if row is None or not row.get("deleted_at"):
        raise CrmRefused(404, f"No such {show.NOUNS[kind]} in the Trash.")
    items = [{"table": KINDS[kind].table, "id": record_id}]
    if kind == "company":
        items = _deals_first(ctx.workspace_id, [record_id], items)
    return store.purge(actor(ctx), items)


def _deals_first(
    ws_id: str, company_ids: list[str], items: list[dict[str, str]]
) -> list[dict[str, str]]:
    """A company goes with its deals, which are in the Trash with it: a deal
    cannot outlive its company. A live deal still on the company is refused
    by the foreign key instead."""
    if not company_ids:
        return items
    queued = {i["id"] for i in items}
    deals = [
        {"table": "crm_opportunities", "id": d["id"]}
        for d in store.list_records(
            ws_id,
            "crm_opportunities",
            any_of={"company_id": sorted(company_ids)},
            deleted=True,
            limit=2000,
        )
        if d["id"] not in queued
    ]
    first = [i for i in items if i["table"] != "crm_companies"]
    return first + deals + [i for i in items if i["table"] == "crm_companies"]


def trash(ctx: Any, kind: str, *, limit: int, after: str | None) -> dict[str, Any]:
    """Contracts §2.5 `GET /crm/trash` — newest deleted first."""
    ws = load(ctx)
    offset = int(after) if after and after.isdigit() else 0
    rows = store.list_records(
        ctx.workspace_id, KINDS[kind].table, deleted=True, limit=limit + 1, offset=offset
    )
    now = datetime.now(UTC)
    return {
        "rows": [show.trash_row(kind, r, ws.facts, now) for r in rows[:limit]],
        "next": str(offset + limit) if len(rows) > limit else None,
    }


# ---------------------------------------------------------------------------
# Drive links — FR-CRM-LINK-01 to -03
# ---------------------------------------------------------------------------


def add_link(ctx: Any, payload: DriveLinkIn) -> dict[str, Any]:
    """A URL and a label on a record; no file content, preview or Drive
    permission (FR-CRM-LINK-02)."""
    ws = load(ctx)
    if payload.record_type not in objects.LINKABLE:
        raise CrmRefused(404, f"No such {payload.record_type}.")
    live(ctx.workspace_id, payload.record_type, payload.record_id)
    why = objects.url_refusal(payload.url, https_only=True)
    if why:
        raise CrmRefused(422, why)
    done = store.apply(
        actor(ctx),
        [
            {
                "op": "insert",
                "table": "crm_drive_links",
                "values": {
                    "record_type": payload.record_type,
                    "record_id": payload.record_id,
                    "url": payload.url,
                    "label": " ".join(payload.label.split()),
                },
            }
        ],
    )
    return show.drive_link(done[0]["row"], ws.facts)


def delete_link(ctx: Any, link_id: str) -> dict[str, Any]:
    """FR-CRM-DEL-04 — a link has no owner, so a Member removes only the
    links they added."""
    row = store.get_record(ctx.workspace_id, "crm_drive_links", link_id) if _uuid(link_id) else None
    if row is None or row.get("deleted_at"):
        raise CrmRefused(404, "No such Drive link.")
    if not ctx.may("soft_delete", owns=row.get("created_by") == ctx.user.id):
        raise CrmRefused(403, "Only a workspace admin or whoever added it can remove this link.")
    group = str(uuid.uuid4())
    store.apply(
        actor(ctx),
        [{"op": "soft_delete", "table": "crm_drive_links", "id": link_id, "deleted_group": group}],
    )
    return {"deleted_group": group}


# ---------------------------------------------------------------------------
# The purge job — FR-CRM-DEL-01
# ---------------------------------------------------------------------------


def purge_expired(workspace_id: str, now: datetime) -> int:
    """Purges this workspace's Trash older than 90 days, as the system, in one
    `crm_purge` call. Links go first, then people, then companies."""
    cutoff = now - timedelta(days=TRASH_DAYS)
    items = [
        {"table": t, "id": r["id"]}
        for t in TRASH_TABLES
        for r in store.list_expired(workspace_id, t, cutoff)
    ]
    if not items:
        return 0
    companies = [i["id"] for i in items if i["table"] == "crm_companies"]
    items = _deals_first(workspace_id, companies, items)
    system = {"user_id": None, "kind": "system", "channel": None, "workspace_id": workspace_id}
    return int(store.purge(system, items).get("purged", 0))


# ---------------------------------------------------------------------------
# Tasks and notes — FR-CRM-TASK-01, -02, FR-CRM-NOTE-01 to -03, Q-57, Q-63
# ---------------------------------------------------------------------------

MUST_LINK = "A task must be linked to at least one record."
#: 008 FR-TASK-DATA-05: a CRM task never loses its last CRM link.
LAST_LINK = "A CRM task needs at least one linked record; delete it instead."
#: 008 FR-TASK-DATA-05: nor comes back from the Trash without one.
NO_LINK_LEFT = "This task has no linked record left, so it cannot be restored."
#: 008 TP-05: the old status keys, still accepted as input, read as Open.
OLD_STATUSES = ("todo", "in_progress")


def _targets(ws: Workspace, links: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The records a task or note links to: live records of this workspace,
    each once. A campaign arrives in 1b, so it is not found yet."""
    wanted: list[tuple[str, str]] = []
    for link in links:
        pair = (link["record_type"], link["record_id"])
        if pair[0] not in objects.TASK_NOTE_TARGETS:
            raise CrmRefused(404, f"No such {pair[0]}.")
        live(ws.id, pair[0], pair[1])
        if pair not in wanted:
            wanted.append(pair)
    return wanted


def _category(ws: Workspace, values: dict[str, Any]) -> None:
    """`category` by label or key, for an MCP caller (contracts §3 tool 6)."""
    asked = values.pop("category", None)
    if asked is None:
        return
    text = str(asked).strip().lower()
    found = next(
        (
            o
            for o in ws.facts.options.values()
            if o["list_key"] == "task.category"
            and o["is_active"]
            and text in (o["label"].lower(), (o.get("system_key") or "").lower())
        ),
        None,
    )
    if found is None:
        raise CrmRefused(422, f"“{asked}” is not a task category in this workspace.")
    if values.get("category_id") not in (None, found["id"]):
        raise CrmRefused(422, "Give the category once, by id or by name.")
    values["category_id"] = found["id"]


def _status(values: dict[str, Any], existing: dict[str, Any] | None) -> None:
    """`completed_at` is written when the status becomes Done and cleared when
    it leaves Done (spec §5.9; 008 FR-TASK-DATA-03), for every status."""
    if "status" not in values:
        return
    values.update(
        task_rules.status_values(
            values["status"], (existing or {}).get("status"), datetime.now(UTC)
        )
    )


def _bucket(ctx: Any, values: dict[str, Any], existing: dict[str, Any] | None) -> None:
    """008 FR-TASK-DATA-08 — an open or waiting task has a bucket: a create,
    or an update or reopen that leaves the task open or waiting with none,
    gets the default for its due date, in the person's own day."""
    after = {**(existing or {}), **values}
    if after.get("status") in task_rules.OPEN and not after.get("bucket"):
        today = task_rules.day_of(datetime.now(UTC), ctx.user.profile.get("timezone"))
        values["bucket"] = task_rules.default_bucket(after.get("due_on"), today)


def _tasks_level(ctx: Any) -> int:
    """008 FR-TASK-ROLL-03 — the actor's phase level: a task write sends its
    delegation messages (FR-TASK-DEL-01, -05) only from phase 2."""
    return task_rollout.level_for(ctx.user.profile, task_store.default_level())


def _task_extras(ws: Workspace, values: dict[str, Any], existing: dict[str, Any] | None) -> None:
    """008 FR-TASK-CHG-01 — the new task fields through the CRM: tags trimmed
    with exact repeats dropped (TP-14); a parent that is a live task of this
    workspace with no parent of its own (FR-TASK-DATA-09); Waiting on a
    member of the workspace, kept as their name, and required while Waiting
    (FR-TASK-DATA-03)."""
    if values.get("tags") is not None:
        try:
            values["tags"] = task_rules.normalise_tags(values["tags"])
        except ValueError as exc:
            raise CrmRefused(422, str(exc)) from None
    if values.get("parent_id"):
        parent = live(ws.id, "task", values["parent_id"])
        if parent.get("parent_id") or (existing and parent["id"] == existing["id"]):
            raise CrmRefused(422, "A subtask cannot have subtasks.")
    if values.get("waiting_on_id"):
        if values["waiting_on_id"] not in ws.members:
            raise CrmRefused(422, "You can wait only on a member of this workspace.")
        values["waiting_on_text"] = ws.facts.name(values["waiting_on_id"]) or values.get(
            "waiting_on_text"
        )
    after = {**(existing or {}), **values}
    if after.get("status") == "waiting" and not (after.get("waiting_on_text") or "").strip():
        raise CrmRefused(422, "Waiting needs who or what you are waiting on.")


def _mentions(ws: Workspace, ids: list[str]) -> list[str]:
    """FR-CRM-NOTE-02, Q-63 — a mention notifies its target, so only members
    of the workspace can be mentioned."""
    for member_id in ids:
        if member_id not in ws.members:
            who = ws.facts.name(member_id)
            raise CrmRefused(
                422,
                f"{who} is not a member of this workspace; only members can be mentioned."
                if who
                else "Only members of this workspace can be mentioned.",
            )
    return list(dict.fromkeys(ids))


def _task_or_note_values(
    ws: Workspace, kind: str, payload: Any, existing: dict[str, Any] | None
) -> tuple[dict[str, Any], list[tuple[str, str]] | None, list[str] | None]:
    creating = existing is None
    values = parse(kind, payload, creating=creating)
    values.pop("expect_updated_at", None)
    raw_links = values.pop("links", None)
    mention_ids = values.pop("mention_ids", None)
    if kind == "task":
        if values.get("status") in OLD_STATUSES:
            values["status"] = "open"
        _category(ws, values)
        if creating and not values.get("assignee_id"):
            values["assignee_id"] = ws.ctx.user.id
        if creating:
            values.setdefault("status", "open")
    else:
        if creating and not values.get("kind"):
            values["kind"] = "note"
    check_fields(ws, kind, values, existing)
    if kind == "task":
        if "assignee_id" in values and values["assignee_id"] not in ws.members:
            raise CrmRefused(422, "The assignee must be a member of this workspace.")
        _status(values, existing)
        _bucket(ws.ctx, values, existing)
        _task_extras(ws, values, existing)
        if existing is not None and "due_on" in values:
            # 008 FR-TASK-RRULE-01: a recurring task keeps a due date.
            try:
                task_recurrence.check(existing.get("recurrence"), values["due_on"])
            except ValueError as exc:
                raise CrmRefused(422, str(exc)) from None
    else:
        after = {**(existing or {}), **values}
        if after.get("kind", "note") != "note" and not after.get("occurred_on"):
            raise CrmRefused(422, "Log activity needs the date it happened.")
    links = _targets(ws, raw_links) if raw_links is not None else None
    if kind == "task" and creating and not links:
        raise CrmRefused(422, MUST_LINK)
    if kind == "task" and links == []:
        raise CrmRefused(422, LAST_LINK)
    mentions = _mentions(ws, mention_ids) if mention_ids is not None else None
    return values, links, mentions


def _create_task_or_note(ctx: Any, kind: str, payload: Any) -> dict[str, Any]:
    """Contracts §2.5 `POST /crm/tasks`, `POST /crm/notes`: the record, its
    links and (a note) its mentions in one `crm_apply` call."""
    ws = load(ctx)
    values, links, mentions = _task_or_note_values(ws, kind, payload, None)
    if kind == "task":
        # Who set the assignee (008 FR-TASK-DEL-02).
        values["assigned_by"] = task_delegation.assignment(values["assignee_id"], ctx.user.id)[
            "assigned_by"
        ]
    owner = "task_id" if kind == "task" else "note_id"
    ops: list[dict[str, Any]] = [
        {"op": "insert", "table": KINDS[kind].table, "ref": "record", "values": values}
    ]
    ops += [
        {
            "op": "insert",
            "table": LINKS_OF[kind],
            "values": {owner: "$ref:record", "record_type": t, "record_id": i},
        }
        for t, i in links or []
    ]
    ops += [
        {
            "op": "insert",
            "table": "crm_note_mentions",
            "values": {"note_id": "$ref:record", "member_id": m},
        }
        for m in mentions or []
    ]
    done = store.apply(actor(ctx), ops)
    row = done[0]["row"]
    targets = [{"record_type": t, "record_id": i} for t, i in links or []]
    reflag(ws.id, kind, [row], links=targets)
    if kind == "task":
        # 008 FR-TASK-DEL-01: the message, and the bell as well.
        task_notices.after_write(_tasks_level(ctx), ctx.user.id, None, row)
        notifications.quietly(
            notifications.assigned(ctx, "task", row["id"], row["title"], row.get("assignee_id"))
        )
    elif mentions:
        notifications.quietly(
            notifications.mentioned(ctx, row["id"], mentions, _about(ws.id, targets))
        )
    return present_rows(ws, kind, [row])[0]


def _about(ws_id: str, links: list[dict[str, Any]]) -> dict[str, str] | None:
    """The first record a note is linked to, which its notification opens.
    Read after the write: a failure leaves the name out, never fails it."""
    if not links:
        return None
    first = links[0]
    try:
        name = link_names(ws_id, [first]).get((first["record_type"], first["record_id"]))
    except Exception:
        flags.logger.exception('{"event": "crm_notify_about_failed"}')
        name = None
    return {**first, "name": name or ""}


def _next_task(
    ctx: Any, row: dict[str, Any], after: dict[str, Any], links: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """008 FR-TASK-RRULE-02, -03; TP-18 — closing a recurring task here
    inserts its next occurrence, exactly as My Tasks does; 422 when its rule
    has no next date."""
    today = task_rules.day_of(datetime.now(UTC), ctx.user.profile.get("timezone"))
    try:
        return task_recurrence.next_occurrence_ops(row, after, links, today)
    except ValueError as exc:
        raise CrmRefused(422, str(exc)) from None


def _write_task(
    ctx: Any,
    ops: list[dict[str, Any]],
    following: list[dict[str, Any]],
    *,
    moves_due: bool = False,
) -> list[dict[str, Any]]:
    """A task write. One that inserts a next occurrence (`_next_task`) goes
    through `task_apply`, as My Tasks' does, so the next occurrence keeps the
    series' creator and its project link; its new links name the workspace,
    which `crm_apply` would have set."""
    if not following:
        return store.apply(actor(ctx), ops)
    ops = [
        {**op, "values": {**op["values"], "workspace_id": ctx.workspace_id}}
        if op["table"] == LINKS_OF["task"] and op["op"] == "insert"
        else op
        for op in ops
    ]
    who = {"user_id": ctx.user.id, "kind": "user", "channel": ctx.channel}
    return task_store._apply_closing(who, ops, following, moves_due=moves_due)


def _may_change_task(ctx: Any, row: dict[str, Any], sent: set[str]) -> None:
    """Q-57 — a Viewer changes only the status of a task assigned to them."""
    if ctx.may("edit_records"):
        return
    if not ctx.may("complete_task", is_assignee=row["assignee_id"] == ctx.user.id) or (
        sent - {"status", "expect_updated_at"}
    ):
        raise CrmRefused(403, "Viewers may change only the status of a task assigned to them.")


def _update_task_or_note(ctx: Any, kind: str, record_id: str, payload: Any) -> dict[str, Any]:
    """Contracts §2.5 `PATCH`. Links and mentions sent replace the ones there."""
    ws = load(ctx)
    row = live(ctx.workspace_id, kind, record_id)
    if kind == "task":
        _may_change_task(ctx, row, set(payload) if isinstance(payload, dict) else set())
    elif not ctx.may("edit_records"):
        raise CrmRefused(403, "Viewers cannot change records.")
    expect = payload.get("expect_updated_at") if isinstance(payload, dict) else None
    values, links, mentions = _task_or_note_values(ws, kind, payload, row)
    owner = "task_id" if kind == "task" else "note_id"

    ops: list[dict[str, Any]] = []
    current_links: list[dict[str, Any]] | None = None
    had_mentions: set[str] = set()
    if links is not None:
        current = store.list_links(ctx.workspace_id, LINKS_OF[kind], owner_ids=[record_id])
        current_links = current
        have = {(lk["record_type"], lk["record_id"]): lk for lk in current}
        ops += [
            {"op": "delete", "table": LINKS_OF[kind], "id": lk["id"]}
            for pair, lk in have.items()
            if pair not in links
        ]
        ops += [
            {
                "op": "insert",
                "table": LINKS_OF[kind],
                "values": {owner: record_id, "record_type": t, "record_id": i},
            }
            for t, i in links
            if (t, i) not in have
        ]
    if mentions is not None:
        current = store.list_mentions(ctx.workspace_id, [record_id])
        have_m = {m["member_id"]: m for m in current}
        had_mentions = set(have_m)
        ops += [
            {"op": "delete", "table": "crm_note_mentions", "id": m["id"]}
            for member, m in have_m.items()
            if member not in mentions
        ]
        ops += [
            {
                "op": "insert",
                "table": "crm_note_mentions",
                "values": {"note_id": record_id, "member_id": m},
            }
            for m in mentions
            if m not in have_m
        ]

    changes = {k: v for k, v in values.items() if row.get(k) != v}
    if not changes and not ops:
        return present_rows(ws, kind, [row])[0]
    # 008 FR-TASK-DEL-02: a new assignee has not accepted yet; any other
    # change by the assignee accepts.
    if kind == "task" and "assignee_id" in changes:
        changes.update(task_delegation.assignment(changes["assignee_id"], ctx.user.id))
    elif kind == "task":
        changes.update(task_delegation.accepting(row, ctx.user.id, datetime.now(UTC).isoformat()))
    following: list[dict[str, Any]] = []
    if kind == "task" and row.get("recurrence"):
        # 008 FR-TASK-RRULE-02: the next occurrence takes the links the task
        # has after this change.
        task_links = task_store.list_links([record_id])
        if links is not None:
            pairs = {(lk["record_type"], lk["record_id"]) for lk in task_links}
            task_links = [
                lk
                for lk in task_links
                if lk["record_type"] == "tva_project"
                or (lk["record_type"], lk["record_id"]) in links
            ] + [
                {"record_type": t, "record_id": i, "workspace_id": ctx.workspace_id}
                for t, i in links
                if (t, i) not in pairs
            ]
        following = _next_task(ctx, row, {**row, **changes}, task_links)
        if following:
            changes["recurrence_spawned"] = True
    main = {
        "op": "update",
        "table": KINDS[kind].table,
        "id": record_id,
        "values": changes or {"updated_by": ctx.user.id},
    }
    if expect:
        main["expect_updated_at"] = expect
    done = _write_task(ctx, [main, *ops], following, moves_due="due_on" in changes)
    after = done[0]["row"]
    spawned = [done[1 + len(ops)]["row"]] if following else []
    # Read after the write: a failure falls back to the links read before it
    # and never fails the write.
    try:
        now_links = store.list_links(ctx.workspace_id, LINKS_OF[kind], owner_ids=[record_id])
    except Exception:
        flags.logger.exception('{"event": "crm_links_reread_failed", "kind": "%s"}', kind)
        now_links = current_links or []
    reflag(ws.id, kind, [after, *spawned], links=[*now_links, *(current_links or [])])
    if kind == "task":
        task_notices.after_write(_tasks_level(ctx), ctx.user.id, row, after)
    if kind == "task" and "assignee_id" in changes:
        notifications.quietly(
            notifications.assigned(ctx, "task", record_id, after["title"], changes["assignee_id"])
        )
    if kind == "note" and mentions:
        new = [m for m in mentions if m not in had_mentions]
        if new:
            notifications.quietly(
                notifications.mentioned(ctx, record_id, new, _about(ws.id, now_links))
            )
    return present_rows(ws, kind, [after])[0]


def complete_task(ctx: Any, task_id: str) -> dict[str, Any]:
    """Contracts §2.5 `POST /crm/tasks/{id}/complete` — Admins and Members, or
    a Viewer on a task assigned to them (Q-57)."""
    ws = load(ctx)
    row = live(ctx.workspace_id, "task", task_id)
    if not ctx.may("complete_task", is_assignee=row["assignee_id"] == ctx.user.id):
        raise CrmRefused(403, "Only the task's assignee or a member can mark it done.")
    if row["status"] == "done":
        return present_rows(ws, "task", [row])[0]
    now = datetime.now(UTC)
    values = {
        **task_rules.status_values("done", row["status"], now),
        **task_delegation.accepting(row, ctx.user.id, now.isoformat()),
    }
    # 008 FR-TASK-RRULE-02: a recurring task's next occurrence, in the same write.
    task_links = task_store.list_links([task_id]) if row.get("recurrence") else []
    following = _next_task(ctx, row, {**row, **values}, task_links)
    if following:
        values["recurrence_spawned"] = True
    done = _write_task(
        ctx, [{"op": "update", "table": "tasks", "id": task_id, "values": values}], following
    )
    task_notices.after_write(_tasks_level(ctx), ctx.user.id, row, done[0]["row"])
    reflag(ws.id, "task", [done[0]["row"], *([done[1]["row"]] if following else [])])
    return present_rows(ws, "task", [done[0]["row"]])[0]
