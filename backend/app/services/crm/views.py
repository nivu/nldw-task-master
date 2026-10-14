"""Views — spec 007 FR-CRM-VIEW-02 to -05, -07, -08, -10, -11, FR-CRM-ACC-11,
FR-CRM-UI-02, FR-CRM-MCP-10; Q-37, Q-67; contracts §2.7, §2.5 `/crm/bulk`;
plan §5.5, §5.8.

Every list the CRM shows is one `crm_query()` call (data-model §5.8): this
module finds the saved view a request names, cleans it for a Viewer, checks
the request (`domain/crm/views.py`), resolves "me" and "this month" for the
person asking, and presents the rows exactly as the record routes do, so a
list row and a record page say the same thing and redact the same way.

Saved views are private to their owner or shared with the workspace. A
Viewer saves private views only (Q-37); Admins and Members manage shared
ones. A seeded view can be hidden but not deleted. Favourites are the
person's own pins (FR-CRM-UI-02).

Bulk actions run each record through the same service call as one edit,
move or delete would, each in its own transaction, and report which
succeeded and why the others did not (FR-CRM-VIEW-07).
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domain.crm import dedupe, objects
from app.domain.crm import views as rules
from app.schemas.crm import BulkIn, CompanyDeleteIn, FavouriteIn, QueryIn, StageMoveIn, ViewIn
from app.schemas.crm import ViewUpdate as ViewChange
from app.services.crm import CrmRefused, deals, store
from app.services.crm import present as show
from app.services.crm import records as base
from app.services.crm.context import actor

NO_SUCH_VIEW = "No such view."
#: Contracts §2.7: a count past this is "10000+".
COUNT_CAP = "10000+"

#: Each fixed key's list, for a group's label (FR-CRM-MCP-06).
_FIXED_LISTS = {
    "lead_stage": "company.lead_stage",
    "stage": "opportunity.stage",
    "lost_from_stage": "opportunity.stage",
    "status": "task.status",
    "bucket": "task.bucket",
}
_FIXED_LABELS = {
    "entity": objects.ENTITIES,
    "project_category": objects.PROJECT_CATEGORIES,
    "kind": objects.NOTE_KINDS,
}
#: The record a `relation` field names, by field.
_RELATIONS = {
    "company_id": ("crm_companies", "company"),
    "point_of_contact_id": ("crm_people", "person"),
}


def kind_of(name: str) -> str:
    """`companies` or `company` → `company`."""
    return base.PLURALS.get(name, name)


def today_for(user: Any) -> date:
    """The person's own today (FR-CRM-ACC-15), for "this month" and the
    `within` operators."""
    zone = (getattr(user, "profile", None) or {}).get("timezone") or "Asia/Kolkata"
    return datetime.now(ZoneInfo(zone)).date()


def _viewer(ctx: Any) -> bool:
    return not ctx.may("see_amounts")


def _tva_linked(ctx: Any) -> bool:
    return bool(ctx.workspace.get("tva_linked"))


def _refuse(why: str | None) -> None:
    if why:
        raise CrmRefused(422, why)


# ---------------------------------------------------------------------------
# Saved views — contracts §2.7
# ---------------------------------------------------------------------------


def _visible(ctx: Any, view: dict[str, Any]) -> bool:
    return view["visibility"] == "shared" or view.get("owner_id") == ctx.user.id


def _may_edit(ctx: Any, view: dict[str, Any]) -> bool:
    """A private view: its owner. A shared one: Admins and Members (Q-37)."""
    if view["visibility"] == "private":
        return view.get("owner_id") == ctx.user.id
    return ctx.may("manage_shared_views")


def present_view(ctx: Any, view: dict[str, Any]) -> dict[str, Any]:
    """A saved view as the caller may see it: for a Viewer, nothing about
    amounts (Q-67), with the notice when something was dropped."""
    config = view.get("config") or {}
    notice = None
    if _viewer(ctx):
        config, dropped = rules.clean_shared_view(
            view["object"], config, tva_linked=_tva_linked(ctx)
        )
        notice = rules.AMOUNT_NOTICE if dropped else None
    out = {
        "id": view["id"],
        "object": view["object"],
        "name": view["name"],
        "icon": view.get("icon"),
        "kind": view["kind"],
        "visibility": view["visibility"],
        "owner_id": view.get("owner_id"),
        "system_key": view.get("system_key"),
        "is_default": bool(view.get("is_default")),
        "hidden": bool(view.get("hidden")),
        "position": view.get("position", 0),
        "config": config,
        "can_edit": _may_edit(ctx, view),
        "created_at": view.get("created_at"),
        "updated_at": view.get("updated_at"),
    }
    if notice:
        out["notice"] = notice
    return out


def _view(ctx: Any, view_id: str) -> dict[str, Any]:
    row = store.get_view(ctx.workspace_id, view_id) if base._uuid(view_id) else None
    if row is None or not _visible(ctx, row):
        raise CrmRefused(404, NO_SUCH_VIEW)
    return row


def _named(ctx: Any, kind: str, name: str) -> dict[str, Any]:
    """A view by its system key or name, the caller's own private view
    first (FR-CRM-MCP-10: crm_list runs any view the caller can see)."""
    seen = [v for v in store.list_views(ctx.workspace_id, object=kind) if _visible(ctx, v)]
    wanted = name.strip().lower()
    for match in (
        lambda v: v.get("system_key") == name,
        lambda v: v["visibility"] == "private" and v["name"].lower() == wanted,
        lambda v: v["name"].lower() == wanted and not v.get("hidden"),
    ):
        found = next((v for v in seen if match(v)), None)
        if found:
            return found
    raise CrmRefused(404, NO_SUCH_VIEW)


def list_views(ctx: Any, object_name: str | None) -> list[dict[str, Any]]:
    """Contracts §2.7 `GET /crm/views` — the shared views (not hidden) and the
    caller's private ones, in order."""
    kind = kind_of(object_name) if object_name else None
    return [
        present_view(ctx, v)
        for v in store.list_views(ctx.workspace_id, object=kind)
        if _visible(ctx, v) and not v.get("hidden")
    ]


def _check_config(ctx: Any, kind: str, view_kind: str, config: dict[str, Any]) -> None:
    unknown = set(config) - {
        "filters",
        "sorts",
        "group_by",
        "kanban_field",
        "columns",
        "aggregates",
        "card_fields",
    }
    if unknown:
        raise CrmRefused(422, f"{sorted(unknown)[0][:40]} is not part of a view.")
    _refuse(rules.validate_spec(kind, config, role=ctx.role, tva_linked=_tva_linked(ctx)))
    if view_kind == "kanban" and not config.get("kanban_field"):
        raise CrmRefused(422, "A kanban view needs the field it groups by.")


def create_view(ctx: Any, body: ViewIn) -> dict[str, Any]:
    """Contracts §2.7 `POST /crm/views`. A Viewer saves private views only."""
    kind = kind_of(body.object)
    if body.visibility == "shared" and not ctx.may("manage_shared_views"):
        raise CrmRefused(403, "Viewers can save private views only.")
    _check_config(ctx, kind, body.kind, body.config)
    mine = store.list_views(ctx.workspace_id, object=kind)
    values = {
        "object": kind,
        "name": body.name,
        "icon": body.icon,
        "kind": body.kind,
        "visibility": body.visibility,
        "owner_id": ctx.user.id if body.visibility == "private" else None,
        "config": body.config,
        "position": max((v.get("position", 0) for v in mine), default=0) + 1,
    }
    done = store.apply(actor(ctx), [{"op": "insert", "table": "crm_saved_views", "values": values}])
    return present_view(ctx, done[0]["row"])


def update_view(ctx: Any, view_id: str, body: ViewChange) -> dict[str, Any]:
    """Contracts §2.7 `PATCH /crm/views/{id}` — the owner of a private view,
    Admins and Members for a shared one. A seeded view keeps its object,
    kind and visibility."""
    row = _view(ctx, view_id)
    if not _may_edit(ctx, row):
        raise CrmRefused(403, "Only Admins and Members can change a shared view.")
    sent = body.model_dump(exclude_unset=True)
    for key in ("name", "kind", "visibility", "is_default", "hidden", "position"):
        if key in sent and sent[key] is None:
            raise CrmRefused(422, f"A view's {key.replace('_', ' ')} cannot be empty.")
    if row.get("system_key") and {"kind", "visibility"} & set(sent):
        raise CrmRefused(422, "A seeded view keeps its kind and who sees it; save a copy instead.")
    if "hidden" in sent and not row.get("system_key"):
        raise CrmRefused(422, "Only a seeded view is hidden; delete this one instead.")
    visibility = sent.get("visibility", row["visibility"])
    if visibility == "shared" and not ctx.may("manage_shared_views"):
        raise CrmRefused(403, "Viewers can save private views only.")
    if sent.get("is_default") and visibility != "shared":
        raise CrmRefused(422, "Only a shared view can be the list's default.")
    if "config" in sent or "kind" in sent:
        _check_config(
            ctx, row["object"], sent.get("kind", row["kind"]), sent.get("config", row["config"])
        )

    values = {k: v for k, v in sent.items() if row.get(k) != v}
    if "visibility" in values:
        values["owner_id"] = ctx.user.id if visibility == "private" else None
        if visibility == "private":
            values["is_default"] = False
    if not values:
        return present_view(ctx, row)
    ops: list[dict[str, Any]] = []
    if values.get("is_default"):
        # One shared default per object: the old one gives way first.
        ops += [
            {
                "op": "update",
                "table": "crm_saved_views",
                "id": v["id"],
                "values": {"is_default": False},
            }
            for v in store.list_views(ctx.workspace_id, object=row["object"])
            if v.get("is_default") and v["id"] != row["id"]
        ]
    ops.append({"op": "update", "table": "crm_saved_views", "id": row["id"], "values": values})
    done = store.apply(actor(ctx), ops)
    return present_view(ctx, done[-1]["row"])


def delete_view(ctx: Any, view_id: str) -> dict[str, Any]:
    """Contracts §2.7 `DELETE /crm/views/{id}`: a seeded view is hidden, any
    other is removed with the favourites that pin it."""
    row = _view(ctx, view_id)
    if not _may_edit(ctx, row):
        raise CrmRefused(403, "Only Admins and Members can change a shared view.")
    if row.get("system_key"):
        if not row.get("hidden"):
            store.apply(
                actor(ctx),
                [
                    {
                        "op": "update",
                        "table": "crm_saved_views",
                        "id": row["id"],
                        "values": {"hidden": True, "is_default": False},
                    }
                ],
            )
        return {"hidden": True}
    pins = store.list_favourites(ctx.workspace_id, target_type="view", target_ids=[row["id"]])
    ops = [{"op": "delete", "table": "crm_favourites", "id": f["id"]} for f in pins]
    ops.append({"op": "delete", "table": "crm_saved_views", "id": row["id"]})
    store.apply(actor(ctx), ops)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# The query — contracts §2.7 `POST /crm/query`
# ---------------------------------------------------------------------------


def _cursor_out(key: Any) -> str | None:
    if key is None:
        return None
    raw = json.dumps(key, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_in(text: str) -> list[Any]:
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        key = json.loads(raw)
    except (binascii.Error, ValueError):
        key = None
    if not isinstance(key, list):
        raise CrmRefused(422, "That page does not belong to this list.")
    return key


def query(ctx: Any, body: QueryIn) -> dict[str, Any]:
    """One page of a list (FR-CRM-VIEW-02 to -04, -08, -10): rows presented as
    the record routes present them, the total ("10000+" past ten thousand),
    the groups with their counts and totals, the totals, and the next page's
    cursor. A Viewer opening a view with amount conditions gets it cleaned
    and the notice (Q-67); a Viewer asking for one gets 422."""
    kind = kind_of(body.object)
    viewer = _viewer(ctx)
    linked = _tva_linked(ctx)
    spec = body.spec.model_dump(exclude_unset=True)
    _refuse(rules.validate_spec(kind, spec, role=ctx.role, tva_linked=linked))

    view = None
    notice = None
    config: dict[str, Any] = {}
    if body.view_id or body.view:
        view = _view(ctx, body.view_id) if body.view_id else _named(ctx, kind, body.view or "")
        if view["object"] != kind:
            raise CrmRefused(404, NO_SUCH_VIEW)
        config = view.get("config") or {}
        if viewer:
            config, dropped = rules.clean_shared_view(kind, config, tva_linked=linked)
            notice = rules.AMOUNT_NOTICE if dropped else None
        _refuse(rules.validate_spec(kind, config, role=ctx.role, tva_linked=linked))

    merged = rules.merge(config, spec, set(spec) - {"q", "limit", "after"})
    merged["limit"] = body.spec.limit
    if body.spec.q:
        merged["q"] = body.spec.q
    if body.spec.after:
        merged["after"] = _cursor_in(body.spec.after)
    resolved = rules.resolve(merged, user_id=ctx.user.id, today=today_for(ctx.user))
    found = store.query(ctx.workspace_id, kind, resolved, redact=viewer)

    ws = base.load(ctx)
    total = found.get("total")
    answer: dict[str, Any] = {
        "rows": base.present_rows(ws, kind, found.get("rows") or []),
        "total": COUNT_CAP if total is None else total,
        "next": _cursor_out(found.get("next")),
    }
    group_by = resolved.get("group_by")
    if group_by:
        answer["group_by"] = group_by
        answer["groups"] = _groups(ws, kind, group_by, found.get("groups") or [])
    if resolved.get("aggregates"):
        answer["aggregates"] = _totals(kind, found.get("aggregates") or {}, viewer)
    if view is not None:
        answer["view"] = present_view(ctx, view)
    if notice:
        answer["notice"] = notice
    return answer


def _totals(kind: str, raw: dict[str, Any], viewer: bool) -> dict[str, Any]:
    """`{count, sum_amount_in_inr: "…", …}` as decimal strings; for a Viewer
    no total of an amount, whatever reached here (FR-CRM-ACC-11)."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        fn, _, name = key.partition("_")
        found = objects.view_field(kind, name) if name else None
        if viewer and found is not None and found.is_amount:
            continue
        if fn == "count" or value is None:
            out[key] = value
        elif found is not None and found.type == "currency":
            out[key] = show.money(value)
        else:
            number = Decimal(str(value))
            out[key] = format(
                number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if fn == "avg" else number,
                "f",
            )
    return out


def _groups(
    ws: base.Workspace, kind: str, field: str, raw: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Each group with the label of its key, in the list's own order (a
    select's option order, else by label), empty last."""
    found = objects.view_field(kind, field)
    kind_of_field = found.type if found else "text"
    viewer = ws.facts.role == "viewer"
    names: dict[str, str] = {}
    if field in _RELATIONS:
        table, record_type = _RELATIONS[field]
        rows = base.by_id(ws.id, table, {g["key"] for g in raw if g.get("key")})
        names = {
            k: (
                dedupe.full_name(r["first_name"], r.get("last_name"))
                if record_type == "person"
                else r["name"]
            )
            for k, r in rows.items()
        }

    def label(key: Any) -> str | None:
        if key is None:
            return None
        if kind_of_field == "select":
            return ws.facts.option_label(key)
        if kind_of_field == "person":
            return ws.facts.name(key)
        if kind_of_field == "relation":
            return names.get(key)
        if kind_of_field == "boolean":
            return "Yes" if key else "No"
        if field in _FIXED_LISTS:
            return ws.facts.fixed_label(_FIXED_LISTS[field], key)
        return _FIXED_LABELS.get(field, {}).get(key, str(key))

    def order(group: dict[str, Any]) -> tuple:
        key = group.get("key")
        option = ws.facts.options.get(key or "") if kind_of_field == "select" else None
        if option is None and field in _FIXED_LISTS:
            option = next(
                (
                    o
                    for o in ws.facts.options.values()
                    if o["list_key"] == _FIXED_LISTS[field] and o.get("system_key") == key
                ),
                None,
            )
        return (key is None, option["position"] if option else 0, label(key) or "")

    out = []
    for group in sorted(raw, key=order):
        entry = {
            "key": group.get("key"),
            "label": label(group.get("key")),
            "count": group.get("count", 0),
        }
        if group.get("aggregates"):
            entry["aggregates"] = _totals(kind, group["aggregates"], viewer)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Favourites — FR-CRM-UI-02
# ---------------------------------------------------------------------------


def _present_favourite(fav: dict[str, Any], name: str | None) -> dict[str, Any]:
    return {
        "id": fav["id"],
        "target_type": fav["target_type"],
        "record_type": fav.get("record_type"),
        "target_id": fav["target_id"],
        "name": name,
        "position": fav.get("position", 0),
    }


def list_favourites(ctx: Any) -> list[dict[str, Any]]:
    """The person's own favourites with each target's name; a pinned record
    now in the Trash is left out until it is restored."""
    ws = base.load(ctx)
    favs = store.list_favourites(ctx.workspace_id, user_id=ctx.user.id)
    views = {v["id"]: v for v in store.list_views(ctx.workspace_id)}
    out = []
    for fav in favs:
        if fav["target_type"] == "view":
            view = views.get(fav["target_id"])
            if view is None or not _visible(ctx, view):
                continue
            out.append(_present_favourite(fav, view["name"]))
            continue
        row = base.by_id(ws.id, base.KINDS[fav["record_type"]].table, [fav["target_id"]]).get(
            fav["target_id"]
        )
        if row is None or row.get("deleted_at"):
            continue
        out.append(_present_favourite(fav, _record_name(fav["record_type"], row)))
    return out


def _record_name(record_type: str, row: dict[str, Any]) -> str:
    if record_type == "person":
        return dedupe.full_name(row["first_name"], row.get("last_name"))
    if record_type == "task":
        return row["title"]
    if record_type == "note":
        return (row.get("body") or "")[:80]
    return row["name"]


def add_favourite(ctx: Any, body: FavouriteIn) -> dict[str, Any]:
    ws = base.load(ctx)
    if body.target_type == "view":
        if body.record_type is not None:
            raise CrmRefused(422, "A pinned view has no record type.")
        name = _view(ctx, body.target_id)["name"]
    else:
        if body.record_type is None:
            raise CrmRefused(422, "Say which kind of record to pin.")
        name = _record_name(body.record_type, base.live(ws.id, body.record_type, body.target_id))
    mine = store.list_favourites(ctx.workspace_id, user_id=ctx.user.id)
    values = {
        "user_id": ctx.user.id,
        "target_type": body.target_type,
        "record_type": body.record_type,
        "target_id": body.target_id,
        "position": max((f.get("position", 0) for f in mine), default=0) + 1,
    }
    done = store.apply(actor(ctx), [{"op": "insert", "table": "crm_favourites", "values": values}])
    return _present_favourite(done[0]["row"], name)


def remove_favourite(ctx: Any, favourite_id: str) -> dict[str, Any]:
    mine = store.list_favourites(ctx.workspace_id, user_id=ctx.user.id)
    if not any(f["id"] == favourite_id for f in mine):
        raise CrmRefused(404, "No such favourite.")
    store.apply(actor(ctx), [{"op": "delete", "table": "crm_favourites", "id": favourite_id}])
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Bulk actions — FR-CRM-VIEW-07
# ---------------------------------------------------------------------------


def bulk(ctx: Any, body: BulkIn) -> dict[str, Any]:
    """Contracts §2.5 `POST /crm/bulk` — `{ok: [ids], failed: [{id, reason}]}`.
    Each record goes through exactly the call one edit would make, in its own
    transaction, so each obeys every rule (permissions, pipeline, TVA) and
    one refusal does not undo the others."""
    kind = kind_of(body.object)
    one = _bulk_action(ctx, kind, body)
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    for record_id in dict.fromkeys(body.ids):
        try:
            one(record_id)
        except CrmRefused as refused:
            failed.append({"id": record_id, "reason": refused.detail})
        else:
            ok.append(record_id)
    return {"ok": ok, "failed": failed}


def _bulk_action(ctx: Any, kind: str, body: BulkIn) -> Any:
    """The call for one record, after refusing (422) a request that could
    not apply to any of them."""
    values = dict(body.values)
    if body.action == "add_to_campaign":
        raise CrmRefused(422, "Campaigns are not available yet.")

    if body.action == "set":
        if not values:
            raise CrmRefused(422, "Say which fields to set.")
        if kind == "opportunity" and "stage" in values:
            if set(values) - {"stage", "changes", "company_on_loss"}:
                raise CrmRefused(422, "Move a deal's stage on its own, with what the stage needs.")
            try:
                move = StageMoveIn.model_validate(values)
            except ValidationError as exc:
                raise CrmRefused(422, _first_error(exc)) from None
            return lambda record_id: deals.move(ctx, record_id, move)
        if kind == "opportunity":
            return lambda record_id: deals.update(ctx, record_id, values)
        return lambda record_id: base.update(ctx, kind, record_id, values)

    if body.action == "assign_owner":
        owner = values.get("owner_id")
        if set(values) != {"owner_id"} or not isinstance(owner, str):
            raise CrmRefused(422, "Name the new owner: owner_id.")
        if kind == "note":
            raise CrmRefused(422, "Notes have no owner.")
        field = "assignee_id" if kind == "task" else "owner_id"
        if kind == "opportunity":
            return lambda record_id: deals.update(ctx, record_id, {field: owner})
        return lambda record_id: base.update(ctx, kind, record_id, {field: owner})

    if body.action == "add_task":
        if kind not in objects.TASK_NOTE_TARGETS:
            raise CrmRefused(422, "Tasks are added to companies, people and deals.")
        if "links" in values:
            raise CrmRefused(422, "Each task is linked to its record; send no links.")

        def add(record_id: str) -> Any:
            base.live(ctx.workspace_id, kind, record_id)
            task = {**values, "links": [{"record_type": kind, "record_id": record_id}]}
            return base.create(ctx, "task", task, check_duplicates=False)

        return add

    # delete: as DELETE /crm/{object}/{id} (FR-CRM-DEL-04).
    choices = None
    if kind == "company":
        try:
            choices = CompanyDeleteIn.model_validate(values)
        except ValidationError as exc:
            raise CrmRefused(422, _first_error(exc)) from None
    elif values:
        raise CrmRefused(422, "Only deleting companies takes choices.")

    def delete(record_id: str) -> Any:
        chosen = choices.deals if choices else None
        return base.soft_delete(
            ctx,
            kind,
            record_id,
            people=choices.people if choices else None,
            deals="delete" if chosen == "delete" else None,
            move_deals_to=getattr(chosen, "move_to", None),
        )

    return delete


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ())) or "values"
    return f"{where}: {first.get('msg', 'not valid')}."
