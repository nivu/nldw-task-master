"""Records — spec 007 FR-CRM-CO-01 to -03, FR-CRM-PER-01, FR-CRM-TASK-01,
-02, FR-CRM-NOTE-01 to -03, FR-CRM-LINK-01 to -03, FR-CRM-DUP-01, -02,
FR-CRM-DEL-01, -02, -04, FR-CRM-PIPE-01, FR-CRM-REC-01 to -04; Q-57, Q-63;
contracts §2.5; plan §5.3.

Generic create, update, soft delete (with a `deleted_group`), restore,
permanent delete and list, each validated from the field registry
(`domain/crm/objects.py`) and written in one `crm_apply` or `crm_purge` call,
so the change and its audit rows land together (FR-CRM-AUD-06). Companies,
people, tasks and notes are created and edited here; deals in `deals.py`,
whose stage moves have rules of their own, and read, deleted and restored
here like every other record.

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

from app.domain.crm import dedupe, objects, pipeline
from app.schemas.crm import (
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
from app.services.crm import CrmRefused, store, tva
from app.services.crm import present as show
from app.services.crm.context import actor

#: FR-CRM-DEL-01.
TRASH_DAYS = 90


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
    "task": Kind("task", "crm_tasks", "task", TaskFields, TaskUpdate),
    "note": Kind("note", "crm_notes", "note", NoteFields, NoteUpdate),
}
#: URL segment → kind (contracts §2.5 "Objects in paths").
PLURALS = {
    "companies": "company",
    "people": "person",
    "opportunities": "opportunity",
    "tasks": "task",
    "notes": "note",
}
#: The record tables a soft delete, restore or purge may touch, in purge
#: order: what points at a record goes before it (a deal before its company).
TRASH_TABLES = (
    "crm_drive_links",
    "crm_notes",
    "crm_tasks",
    "crm_opportunities",
    "crm_people",
    "crm_companies",
)
#: A task's or note's link table, by kind.
LINKS_OF = {"task": "crm_task_links", "note": "crm_note_links"}
#: The records whose page has Tasks and Notes tabs (FR-CRM-REC-03).
SHOW_TABS = objects.TASK_NOTE_TARGETS


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
        why = objects.lead_stage_refusal(values["lead_stage"] or "", creating=existing is None)
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

    if values.get("owner_id") and values["owner_id"] not in ws.members:
        raise CrmRefused(422, "The owner must be a member of this workspace.")

    for name in ("website", "linkedin"):
        links = values.get(name) or {}
        for link in [links.get("primary"), *(links.get("others") or [])]:
            if link:
                why = objects.url_refusal(link["url"])
                if why:
                    raise CrmRefused(422, why)


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
    wanted = sorted(i for i in ids if i)
    if not wanted:
        return {}
    rows = store.list_records(ws_id, table, any_of={"id": wanted}, deleted=None, limit=len(wanted))
    return {r["id"]: r for r in rows}


def link_names(ws_id: str, links: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """The name of every record some task or note links to."""
    names: dict[tuple[str, str], str] = {}
    for record_type, table in (
        ("company", "crm_companies"),
        ("person", "crm_people"),
        ("opportunity", "crm_opportunities"),
    ):
        ids = {link["record_id"] for link in links if link["record_type"] == record_type}
        for rid, row in by_id(ws_id, table, ids).items():
            names[(record_type, rid)] = (
                dedupe.full_name(row["first_name"], row.get("last_name"))
                if record_type == "person"
                else row["name"]
            )
    return names


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
        return [show.opportunity(r, ws.facts, companies, people, projects) for r in rows]
    if kind in LINKS_OF:
        links = store.list_links(ws.id, LINKS_OF[kind], owner_ids=[r["id"] for r in rows])
        names = link_names(ws.id, links)
        owner = "task_id" if kind == "task" else "note_id"
        if kind == "task":
            return [
                show.task(r, ws.facts, [lk for lk in links if lk[owner] == r["id"]], names)
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
        not_equal={"status": "done"} if open_only else None,
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
    if merged_from:
        answer["merged_from"] = merged_from
    return answer


def linked(ws_id: str, of: str, record_type: str, record_id: str) -> list[dict[str, Any]]:
    """The live tasks or notes linked to a record: open tasks first, then by
    due date; notes newest first."""
    links = store.list_links(ws_id, LINKS_OF[of], record_type=record_type, record_ids=[record_id])
    owner = "task_id" if of == "task" else "note_id"
    ids = sorted({lk[owner] for lk in links})
    if not ids:
        return []
    rows = store.list_records(ws_id, KINDS[of].table, any_of={"id": ids}, limit=len(ids))
    if of == "task":
        return sorted(
            rows,
            key=lambda r: (r["status"] == "done", r.get("due_on") or "9999-12-31", r["title"]),
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
        entries += [e for e in found if not types or e["type"] in types]
        if len(rows) < batch:
            break
        before = rows[-1]["id"]
    return entries[:limit]


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
    if kind == "company" and not values.get("owner_id"):
        values["owner_id"] = ctx.user.id
    check_fields(ws, kind, values, None)
    domains: list[tuple[str, bool]] = []
    if kind == "company":
        values.setdefault("lead_stage", "new")
        domains = _domains(ws, values, None) or []
    else:
        _company_ref(ws, values)
        _emails(ws, values, None)

    if check_duplicates:
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
    return present_rows(ws, kind, [done[0]["row"]])[0]


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
    return present_rows(ws, kind, [done[0]["row"]])[0]


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
    """`completed_at` is written when the status becomes Done (spec §5.9)."""
    if "status" not in values:
        return
    was = (existing or {}).get("status")
    if values["status"] == "done" and was != "done":
        values["completed_at"] = datetime.now(UTC).isoformat()
    elif values["status"] != "done":
        values["completed_at"] = None


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
        _category(ws, values)
        if creating and not values.get("assignee_id"):
            values["assignee_id"] = ws.ctx.user.id
        if creating:
            values.setdefault("status", "todo")
    else:
        if creating and not values.get("kind"):
            values["kind"] = "note"
    check_fields(ws, kind, values, existing)
    if kind == "task":
        if "assignee_id" in values and values["assignee_id"] not in ws.members:
            raise CrmRefused(422, "The assignee must be a member of this workspace.")
        _status(values, existing)
    else:
        after = {**(existing or {}), **values}
        if after.get("kind", "note") != "note" and not after.get("occurred_on"):
            raise CrmRefused(422, "Log activity needs the date it happened.")
    links = _targets(ws, raw_links) if raw_links is not None else None
    if kind == "task" and (links == [] or (creating and not links)):
        raise CrmRefused(422, MUST_LINK)
    mentions = _mentions(ws, mention_ids) if mention_ids is not None else None
    return values, links, mentions


def _create_task_or_note(ctx: Any, kind: str, payload: Any) -> dict[str, Any]:
    """Contracts §2.5 `POST /crm/tasks`, `POST /crm/notes`: the record, its
    links and (a note) its mentions in one `crm_apply` call."""
    ws = load(ctx)
    values, links, mentions = _task_or_note_values(ws, kind, payload, None)
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
    return present_rows(ws, kind, [done[0]["row"]])[0]


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
    if links is not None:
        current = store.list_links(ctx.workspace_id, LINKS_OF[kind], owner_ids=[record_id])
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
    main = {
        "op": "update",
        "table": KINDS[kind].table,
        "id": record_id,
        "values": changes or {"updated_by": ctx.user.id},
    }
    if expect:
        main["expect_updated_at"] = expect
    done = store.apply(actor(ctx), [main, *ops])
    return present_rows(ws, kind, [done[0]["row"]])[0]


def complete_task(ctx: Any, task_id: str) -> dict[str, Any]:
    """Contracts §2.5 `POST /crm/tasks/{id}/complete` — Admins and Members, or
    a Viewer on a task assigned to them (Q-57)."""
    ws = load(ctx)
    row = live(ctx.workspace_id, "task", task_id)
    if not ctx.may("complete_task", is_assignee=row["assignee_id"] == ctx.user.id):
        raise CrmRefused(403, "Only the task's assignee or a member can mark it done.")
    if row["status"] == "done":
        return present_rows(ws, "task", [row])[0]
    done = store.apply(
        actor(ctx),
        [
            {
                "op": "update",
                "table": "crm_tasks",
                "id": task_id,
                "values": {"status": "done", "completed_at": datetime.now(UTC).isoformat()},
            }
        ],
    )
    return present_rows(ws, "task", [done[0]["row"]])[0]
