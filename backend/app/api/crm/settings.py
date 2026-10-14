"""Workspace settings, select lists and FX rates — spec 007 contracts §2.3;
FR-CRM-LIST-01 to -06, -09, FR-CRM-FX-02, FR-CRM-PIPE-11.

Everything here is Admin only except reading the lists, which every member
needs for the pickers. Each change goes through `crm_apply`, so it is
audited with the workspace, the person and the channel (FR-CRM-AUD-01).

The domain allow-list is shown here read-only: it limits whom a workspace
admin may invite, so only a TVA admin edits it (FR-CRM-ACC-06, plan §5.13).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Path

from app.api.crm.deps import CrmAdminDep, CrmContext, CrmReadDep
from app.api.errors import ProblemDetail
from app.domain.crm import settings as rules
from app.schemas.crm import FxRateIn, OptionCreate, OptionUpdate, SettingUpdate
from app.services.crm import store
from app.services.crm.context import actor

router = APIRouter(prefix="/crm", tags=["crm"])

ALLOW_LIST_KEY = "allowed_domains"
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
_OPTION_FIELDS = (
    "id",
    "system_key",
    "label",
    "color",
    "position",
    "is_default",
    "is_active",
    "is_fixed",
)


# ---------------------------------------------------------------------------
# Settings — FR-CRM-LIST-06
# ---------------------------------------------------------------------------


@router.get("/settings")
def read_settings(ctx: CrmAdminDep) -> dict:
    return {
        "settings": {row["key"]: row["value"] for row in store.list_settings(ctx.workspace_id)},
        "allowed_domains": ctx.workspace.get("allowed_domains") or [],
    }


@router.patch("/settings/{key}")
def update_setting(key: str, payload: SettingUpdate, ctx: CrmAdminDep) -> dict:
    if key == ALLOW_LIST_KEY:
        raise ProblemDetail(403, "Only a TVA admin can change the domain allow-list.")
    current = next((r for r in store.list_settings(ctx.workspace_id) if r["key"] == key), None)
    if current is None or key not in rules.VALIDATORS:
        raise ProblemDetail(404, "No such setting.")
    why = rules.setting_refusal(key, payload.value)
    if why:
        raise ProblemDetail(422, why)
    store.apply(
        actor(ctx),
        [
            {
                "op": "update",
                "table": "crm_workspace_settings",
                "id": current["id"],
                "values": {"value": payload.value},
                "action": "setting.updated",
            }
        ],
    )
    return {"key": key, "value": payload.value}


# ---------------------------------------------------------------------------
# Select lists — FR-CRM-LIST-01, -02, -09
# ---------------------------------------------------------------------------


def present_lists(ctx: CrmContext) -> dict[str, list[dict]]:
    """Every list this workspace has, empty ones included, options in order.
    Inactive options are kept: old records still show them (FR-CRM-LIST-01)."""
    lists: dict[str, list[dict]] = {
        key: [] for key in rules.lists_for(tva_linked=bool(ctx.workspace.get("tva_linked")))
    }
    for row in store.list_options(ctx.workspace_id):
        if row["list_key"] in lists:
            lists[row["list_key"]].append(_option(row))
    for options in lists.values():
        options.sort(key=lambda o: o["position"])
    return lists


@router.get("/lists")
def read_lists(ctx: CrmReadDep) -> dict[str, list[dict]]:
    return present_lists(ctx)


@router.post("/lists/{list_key}/options", status_code=201)
def add_option(list_key: str, payload: OptionCreate, ctx: CrmAdminDep) -> dict:
    _check_list(ctx, list_key)
    why = rules.add_option_refusal(list_key)
    if why:
        raise ProblemDetail(422, why)
    siblings = [o for o in store.list_options(ctx.workspace_id) if o["list_key"] == list_key]
    values = {
        "list_key": list_key,
        "label": _label(payload.label),
        "color": payload.color,
        "position": max((o["position"] for o in siblings), default=0) + 1,
    }
    done = store.apply(
        actor(ctx),
        [{"op": "insert", "table": "crm_select_options", "values": values}],
    )
    return _option(done[0]["row"])


@router.patch("/lists/{list_key}/options/{option_id}")
def update_option(list_key: str, option_id: str, payload: OptionUpdate, ctx: CrmAdminDep) -> dict:
    _check_list(ctx, list_key)
    option = store.get_option(ctx.workspace_id, option_id)
    if option is None or option["list_key"] != list_key:
        raise ProblemDetail(404, "No such option.")
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if "label" in changes:
        if changes["label"] is None:
            raise ProblemDetail(422, "An option needs a name.")
        changes["label"] = _label(changes["label"])
    for flag in ("position", "is_default", "is_active"):
        if flag in changes and changes[flag] is None:
            del changes[flag]
    why = rules.option_change_refusal(option, changes)
    if why:
        raise ProblemDetail(422, why)
    if not changes:
        return _option(option)

    siblings = [o for o in store.list_options(ctx.workspace_id) if o["list_key"] == list_key]
    ops: list[dict[str, Any]] = []
    if changes.get("is_default") is True and not option["is_default"]:
        # One default per list: the old one gives way first, in the same
        # transaction, so the unique index never sees two.
        for other in siblings:
            if other["is_default"] and other["id"] != option_id:
                ops.append(
                    {
                        "op": "update",
                        "table": "crm_select_options",
                        "id": other["id"],
                        "values": {"is_default": False},
                    }
                )
    if "position" in changes:
        # A reorder renumbers the whole list in the same call, so no two
        # options share a place and the order asked for is the order shown.
        moves = rules.reorder(siblings, option_id, changes.pop("position"))
        ops.extend(
            {"op": "update", "table": "crm_select_options", "id": oid, "values": {"position": n}}
            for oid, n in moves.items()
            if oid != option_id
        )
        if option_id in moves:
            changes["position"] = moves[option_id]
    if not changes and not ops:
        return _option(option)
    if changes:
        ops.append(
            {"op": "update", "table": "crm_select_options", "id": option_id, "values": changes}
        )
    done = store.apply(actor(ctx), ops)
    row = next((d["row"] for d in reversed(done) if d["id"] == option_id), None)
    return _option(row or store.get_option(ctx.workspace_id, option_id))


def _check_list(ctx: CrmContext, list_key: str) -> None:
    why = rules.list_refusal(
        list_key,
        tva_linked=bool(ctx.workspace.get("tva_linked")),
        workspace_name=ctx.workspace["name"],
    )
    if why:
        raise ProblemDetail(404, why)


def _label(text: str) -> str:
    label = " ".join(text.split())
    if not label:
        raise ProblemDetail(422, "An option needs a name.")
    return label


def _option(row: dict) -> dict:
    return {k: row.get(k) for k in _OPTION_FIELDS}


# ---------------------------------------------------------------------------
# FX rates — FR-CRM-FX-02
# ---------------------------------------------------------------------------


@router.get("/fx-rates")
def read_fx_rates(ctx: CrmAdminDep) -> list[dict]:
    return [_rate(row) for row in store.list_fx_rates(ctx.workspace_id)]


@router.put("/fx-rates/{currency}/{month}")
def set_fx_rate(
    currency: str,
    month: Annotated[str, Path(pattern=MONTH, description="YYYY-MM")],
    payload: FxRateIn,
    ctx: CrmAdminDep,
) -> dict:
    """Sets the month's rate. Amounts already saved keep the INR value they
    were stored with: a rate edit never recomputes them (Q-59)."""
    code = currency.upper()
    if code == "INR":
        raise ProblemDetail(422, "INR converts at 1; it needs no rate.")
    if code not in rules.FX_CURRENCIES:
        raise ProblemDetail(422, f"Rates are kept for {' and '.join(rules.FX_CURRENCIES)} only.")
    first = rules.month_start(month).isoformat()
    existing = next(
        (
            r
            for r in store.list_fx_rates(ctx.workspace_id)
            if r["currency"] == code and str(r["month"]) == first
        ),
        None,
    )
    rate = str(payload.rate)
    if existing:
        op = {
            "op": "update",
            "table": "crm_fx_rates",
            "id": existing["id"],
            "values": {"rate": rate},
        }
    else:
        op = {
            "op": "insert",
            "table": "crm_fx_rates",
            "values": {"currency": code, "month": first, "rate": rate},
        }
    done = store.apply(actor(ctx), [{**op, "action": "fx_rate.set"}])
    return _rate(done[0]["row"])


def _rate(row: dict) -> dict:
    return {
        "currency": row["currency"],
        "month": str(row["month"])[:7],
        # PostgREST sends numeric as a JSON number; normalise to the decimal
        # string every money and rate value is on the wire.
        "rate": format(Decimal(str(row["rate"])).normalize(), "f"),
    }
