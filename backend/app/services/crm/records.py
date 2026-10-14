"""Companies, people and Drive links — spec 007 FR-CRM-CO-01 to -03,
FR-CRM-PER-01, FR-CRM-LINK-01 to -03, FR-CRM-DUP-01, -02, FR-CRM-DEL-01,
-02, -04, FR-CRM-PIPE-01, FR-CRM-REC-01 to -04; contracts §2.5; plan §5.3.

Generic create, update, soft delete (with a `deleted_group`), restore,
permanent delete and list, each validated from the field registry
(`domain/crm/objects.py`) and written in one `crm_apply` or `crm_purge` call,
so the change and its audit rows land together (FR-CRM-AUD-06).

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

from app.domain.crm import dedupe, objects
from app.schemas.crm import (
    CompanyFields,
    CompanyUpdate,
    DriveLinkIn,
    PersonFields,
    PersonUpdate,
)
from app.services import supabase as db
from app.services.crm import CrmRefused, store
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
}
#: URL segment → kind (contracts §2.5 "Objects in paths").
PLURALS = {"companies": "company", "people": "person"}
#: The record tables a soft delete, restore or purge may touch, in purge order.
TRASH_TABLES = ("crm_drive_links", "crm_people", "crm_companies")


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


def _check(ws: Workspace, kind: str, values: dict[str, Any], existing: dict | None) -> None:
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


def present_rows(ws: Workspace, kind: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if kind == "company":
        domains = store.list_domains(ws.id, company_ids=[r["id"] for r in rows])
        return [show.company(r, ws.facts, domains) for r in rows]
    company_ids = sorted({r["company_id"] for r in rows if r.get("company_id")})
    companies = (
        {
            c["id"]: c
            for c in store.list_records(
                ws.id,
                "crm_companies",
                any_of={"id": company_ids},
                deleted=None,
                limit=len(company_ids) or 1,
            )
        }
        if company_ids
        else {}
    )
    return [show.person(r, ws.facts, companies) for r in rows]


def list_records(ctx: Any, kind: str, *, q: str | None, limit: int, after: str | None) -> dict:
    """Contracts §2.5 `GET /crm/{object}` — newest first, paged by `next`."""
    ws = load(ctx)
    offset = int(after) if after and after.isdigit() else 0
    rows = store.list_records(
        ctx.workspace_id, KINDS[kind].table, q=q, limit=limit + 1, offset=offset
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
    if kind == "company":
        people = store.list_records(
            ctx.workspace_id, "crm_people", filters={"company_id": row["id"]}, limit=200
        )
        related: dict[str, Any] = {"people": [show.person(p, ws.facts) for p in people]}
    else:
        related = {
            "company": (
                {"id": record["company_id"], "name": record["company_name"]}
                if record.get("company_id")
                else None
            )
        }
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
    if merged_from:
        answer["merged_from"] = merged_from
    return answer


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
            related_ids=sorted(links),
            before=before,
            limit=batch,
        )
        found = show.timeline(rows, kind, ws.facts, links=links)
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
    ws = load(ctx)
    values = parse(kind, payload, creating=True)
    # An owner defaults to the person creating the record, always a member:
    # Viewers cannot create.
    if kind == "company" and not values.get("owner_id"):
        values["owner_id"] = ctx.user.id
    _check(ws, kind, values, None)
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
    ws = load(ctx)
    row = live(ctx.workspace_id, kind, record_id)
    values = parse(kind, payload, creating=False)
    expect = values.pop("expect_updated_at", None)
    _check(ws, kind, values, row)

    ops: list[dict[str, Any]] = []
    if kind == "company":
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


def soft_delete(ctx: Any, kind: str, record_id: str, *, people: str | None = None) -> dict:
    """FR-CRM-DEL-01, -02, -04. A Member deletes only what they own (or, with
    no owner set, created); everything deleted in this one action shares a
    `deleted_group`, so a restore brings it back together."""
    row = live(ctx.workspace_id, kind, record_id)
    if not ctx.may("soft_delete", owns=_owns(row, ctx.user.id)):
        raise CrmRefused(
            403, f"Only a workspace admin or its owner can delete this {show.NOUNS[kind]}."
        )
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
    store.apply(actor(ctx), ops)
    return {"deleted_group": group}


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
    return store.purge(actor(ctx), [{"table": KINDS[kind].table, "id": record_id}])


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
    system = {"user_id": None, "kind": "system", "channel": None, "workspace_id": workspace_id}
    return int(store.purge(system, items).get("purged", 0))
