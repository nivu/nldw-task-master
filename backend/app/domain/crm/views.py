"""Views — spec 007 FR-CRM-VIEW-01 to -05, -08, -11, FR-CRM-ACC-11; Q-37,
Q-67; data-model §3.15, §5.8; plan §5.5, §5.8.

What a list request or a saved view may say, checked before `crm_query()`
sees it: every field is in the view registry (`objects.VIEW_FIELDS`) with the
flag its use needs, every operator fits its field's type, filters nest at
most three deep with at most thirty conditions, and a Viewer names no amount
field anywhere (FR-CRM-ACC-11). The database checks the same again (CT-1).

A saved view a Viewer opens is cleaned instead of refused: its amount
conditions, columns, sorts, group and totals are dropped, and the answer says
so (Q-67). `is_me` and the calendar operators (`this_month`,
`this_quarter`) are resolved here, per request, so a seeded view such as
"My accounts" or "Closing this month" means the person and the day asking.
LIKE's wildcards in `q` and `contains` values are escaped here, so a search
for "50%_off" matches those characters. Pure: `today` is an argument.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from app.domain.crm import objects
from app.domain.crm.objects import ViewField

MAX_DEPTH: Final = 3
MAX_CONDITIONS: Final = 30
MAX_SORTS: Final = 10
MAX_AGGREGATES: Final = 20
MAX_COLUMNS: Final = 60
AGGREGATE_FNS: Final = ("count", "sum", "avg", "min", "max")

#: Contracts §0, FR-CRM-ACC-11.
AMOUNT_REFUSAL: Final = "Viewers cannot filter, sort or total by amount."
#: Q-67, verbatim.
AMOUNT_NOTICE: Final = "A condition on an amount field was removed"

_TEXT = frozenset({"is", "is_not", "contains", "not_contains", "is_empty", "is_not_empty"})
_OPTION = frozenset({"is", "is_not", "any_of", "none_of", "is_empty", "is_not_empty"})
_DAYS = frozenset(
    {
        "on",
        "before",
        "after",
        "gte",
        "lte",
        "within_last_days",
        "within_next_days",
        "this_month",
        "this_quarter",
        "is_empty",
        "is_not_empty",
    }
)
_NUMBER = frozenset({"is", "is_not", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"})

#: FR-CRM-VIEW-02 — the operators suited to each type.
OPERATORS: Final[dict[str, frozenset[str]]] = {
    "text": _TEXT,
    "long_text": frozenset({"contains", "not_contains", "is_empty", "is_not_empty"}),
    "number": _NUMBER,
    "currency": _NUMBER,
    "date": _DAYS,
    "datetime": _DAYS,
    "boolean": frozenset({"is", "is_not"}),
    "select": _OPTION,
    "fixed_select": _OPTION,
    "flag": _OPTION,
    "person": _OPTION | {"is_me"},
    "relation": _OPTION,
    "multi_select": frozenset({"any_of", "none_of", "is_empty", "is_not_empty"}),
}
#: Operators that take no value.
_NO_VALUE: Final = frozenset({"is_empty", "is_not_empty", "is_me", "this_month", "this_quarter"})
#: Kanban groups by a select field (FR-CRM-VIEW-01).
KANBAN_TYPES: Final = ("select", "fixed_select", "flag")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fields_for(kind: str, *, tva_linked: bool) -> dict[str, ViewField]:
    """The registry of an object as this workspace sees it: Entity and
    Project category only where TVA is linked, Institution type and
    Programme only where it is not (FR-CRM-WS-06)."""
    return {
        f.field: f
        for f in objects.VIEW_FIELDS.get(kind, ())
        if f.workspace_scope == "all" or (f.workspace_scope == "tva_linked") == tva_linked
    }


def _shown(kind: str, name: Any, fields: dict[str, ViewField]) -> bool:
    """A column or card field: anything the registry or the record has."""
    return isinstance(name, str) and (
        name in fields or objects.field(kind, name) is not None or name == "name"
    )


def _is_amount(kind: str, name: Any, fields: dict[str, ViewField]) -> bool:
    found = fields.get(name) if isinstance(name, str) else None
    return bool(found and found.is_amount) or name in objects.AMOUNT_FIELDS.get(kind, ())


def _column_name(column: Any) -> Any:
    return column.get("field") if isinstance(column, dict) else column


def validate_spec(
    kind: str,
    spec: dict[str, Any],
    *,
    role: str,
    tva_linked: bool,
) -> str | None:
    """A list request's spec or a saved view's config, refused in one
    sentence when wrong (422). A Viewer naming an amount field anywhere is
    refused with AMOUNT_REFUSAL (FR-CRM-ACC-11)."""
    if objects.VIEW_FIELDS.get(kind) is None:
        return "There is no such list."
    fields = fields_for(kind, tva_linked=tva_linked)
    viewer = role == "viewer"

    filters = spec.get("filters")
    if filters is not None:
        counted = [0]
        why = _check_node(filters, fields, viewer, 1, counted)
        if why:
            return why
        if counted[0] > MAX_CONDITIONS:
            return f"Use at most {MAX_CONDITIONS} conditions."

    sorts = spec.get("sorts") or []
    if not isinstance(sorts, list) or len(sorts) > MAX_SORTS:
        return f"Sort by at most {MAX_SORTS} fields."
    for sort in sorts:
        if not isinstance(sort, dict) or set(sort) - {"field", "dir"}:
            return "A sort is a field and a direction."
        found = fields.get(sort.get("field"))
        if found is None or not found.sortable:
            return f"{_label(kind, sort.get('field'))} cannot be sorted on."
        if viewer and found.is_amount:
            return AMOUNT_REFUSAL
        if sort.get("dir", "asc") not in ("asc", "desc"):
            return "A sort is asc or desc."

    for key in ("group_by", "kanban_field"):
        name = spec.get(key)
        if name is None:
            continue
        found = fields.get(name)
        if viewer and found is not None and found.is_amount:
            return AMOUNT_REFUSAL
        if found is None or not found.groupable:
            return f"{_label(kind, name)} cannot group a list."
        if key == "kanban_field" and found.type not in KANBAN_TYPES:
            return "A kanban groups by a select field."

    aggregates = spec.get("aggregates") or []
    if not isinstance(aggregates, list) or len(aggregates) > MAX_AGGREGATES:
        return f"Ask for at most {MAX_AGGREGATES} totals."
    for total in aggregates:
        if not isinstance(total, dict) or set(total) - {"field", "fn"}:
            return "A total is a function and a field."
        if total.get("fn") not in AGGREGATE_FNS:
            return "A total is a count, sum, average, minimum or maximum."
        name = total.get("field")
        if name is None:
            if total["fn"] != "count":
                return "Which field is totalled?"
            continue
        found = fields.get(name)
        if viewer and found is not None and found.is_amount:
            return AMOUNT_REFUSAL
        if found is None or not (total["fn"] == "count" or found.aggregatable):
            return f"{_label(kind, name)} cannot be totalled."

    for key in ("columns", "card_fields"):
        shown = spec.get(key) or []
        if not isinstance(shown, list) or len(shown) > MAX_COLUMNS:
            return f"Show at most {MAX_COLUMNS} fields."
        for column in shown:
            if isinstance(column, dict):
                if set(column) - {"field", "width"}:
                    return "A column is a field and a width."
                width = column.get("width")
                if width is not None and (
                    isinstance(width, bool) or not isinstance(width, int) or not 40 <= width <= 800
                ):
                    return "A column is 40 to 800 pixels wide."
            name = _column_name(column)
            if not _shown(kind, name, fields):
                return f"{_label(kind, name)} is not a field of this list."
            if viewer and _is_amount(kind, name, fields):
                return AMOUNT_REFUSAL
    return None


def _label(kind: str, name: Any) -> str:
    if not isinstance(name, str) or not name:
        return "That field"
    return objects.label(kind, name)[:60]


def _check_node(
    node: Any, fields: dict[str, ViewField], viewer: bool, depth: int, counted: list[int]
) -> str | None:
    if not isinstance(node, dict):
        return "A filter is a condition or a group of them."
    if "items" in node:
        if depth > MAX_DEPTH:
            return f"Filters nest at most {MAX_DEPTH} levels deep."
        if set(node) - {"op", "items"} or node.get("op", "and") not in ("and", "or"):
            return "A filter group joins its conditions with and or or."
        if not isinstance(node["items"], list):
            return "A filter group lists its conditions."
        for item in node["items"]:
            why = _check_node(item, fields, viewer, depth + 1, counted)
            if why:
                return why
        return None

    counted[0] += 1
    if set(node) - {"field", "operator", "value"}:
        return "A condition is a field, an operator and a value."
    found = fields.get(node.get("field"))
    if found is None or not found.filterable:
        return "That field cannot be filtered on."
    if viewer and found.is_amount:
        return AMOUNT_REFUSAL
    op = node.get("operator")
    if op not in OPERATORS[found.type]:
        return f"“{str(op)[:30]}” does not fit {found.field.replace('_', ' ')}."
    return _check_value(found, op, node.get("value"))


def _check_value(found: ViewField, op: str, value: Any) -> str | None:
    if op in _NO_VALUE:
        return None if value is None else f"{op.replace('_', ' ').capitalize()} takes no value."
    if op in ("any_of", "none_of"):
        if (
            not isinstance(value, list)
            or not 1 <= len(value) <= 100
            or not all(isinstance(v, str) and 0 < len(v) <= 64 for v in value)
        ):
            return "Any of and none of take a list of options."
        return None
    if op in ("within_last_days", "within_next_days"):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3650:
            return "Within takes a number of days from 0 to 3650."
        return None
    if found.type in ("date", "datetime"):
        if not isinstance(value, str) or not _DATE.match(value) or _date(value) is None:
            return "A date is written YYYY-MM-DD."
        return None
    if found.type in ("number", "currency"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return "That is not a number."
        try:
            if not Decimal(str(value)).is_finite():
                return "That is not a number."
        except InvalidOperation:
            return "That is not a number."
        return None
    if found.type == "boolean":
        return None if isinstance(value, bool) else "Choose true or false."
    if not isinstance(value, str) or not value or len(value) > 200:
        return "A condition needs a value of at most 200 characters."
    return None


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# A Viewer opening a saved view — Q-67
# ---------------------------------------------------------------------------


def clean_shared_view(
    kind: str, config: dict[str, Any], *, tva_linked: bool
) -> tuple[dict[str, Any], bool]:
    """The view without anything about amounts, and whether anything was
    dropped: amount conditions, columns, card fields, sorts, an amount group
    or kanban field, and every amount total (the seeded `pipeline` kanban's
    sum headers). The view then opens for a Viewer instead of failing."""
    fields = fields_for(kind, tva_linked=tva_linked)
    dropped = [False]

    def amount(name: Any) -> bool:
        if _is_amount(kind, name, fields):
            dropped[0] = True
            return True
        return False

    def clean(node: Any) -> Any:
        if isinstance(node, dict) and "items" in node and isinstance(node["items"], list):
            kept = [clean(i) for i in node["items"]]
            return {**node, "items": [i for i in kept if i is not None]}
        if isinstance(node, dict) and amount(node.get("field")):
            return None
        return node

    out = dict(config)
    if out.get("filters") is not None:
        out["filters"] = clean(out["filters"]) or {"op": "and", "items": []}
    for key in ("sorts", "aggregates"):
        if isinstance(out.get(key), list):
            out[key] = [s for s in out[key] if not (isinstance(s, dict) and amount(s.get("field")))]
    for key in ("columns", "card_fields"):
        if isinstance(out.get(key), list):
            out[key] = [c for c in out[key] if not amount(_column_name(c))]
    for key in ("group_by", "kanban_field"):
        if out.get(key) is not None and amount(out[key]):
            out.pop(key)
    return out, dropped[0]


# ---------------------------------------------------------------------------
# From a request to crm_query's spec
# ---------------------------------------------------------------------------


def merge(config: dict[str, Any], spec: dict[str, Any], sent: set[str]) -> dict[str, Any]:
    """A saved view narrowed by the request (contracts §2.7): both sets of
    filters must hold; anything else the request sent replaces the view's.
    A kanban view groups by its kanban field unless the request groups."""
    out = {k: v for k, v in config.items() if k != "card_fields"}
    if out.get("kanban_field") and "group_by" not in out:
        out["group_by"] = out["kanban_field"]
    out.pop("kanban_field", None)
    for key in sent:
        if key == "filters":
            continue
        out[key] = spec.get(key)
    mine = spec.get("filters")
    if mine is not None:
        theirs = config.get("filters")
        out["filters"] = {"op": "and", "items": [theirs, mine]} if theirs else mine
    return {k: v for k, v in out.items() if v is not None}


def escape_like(text: str) -> str:
    """`\\`, `%` and `_` taken literally in a LIKE pattern (ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def month_bounds(today: date) -> tuple[date, date]:
    first = today.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first, following - timedelta(days=1)


def quarter_bounds(today: date) -> tuple[date, date]:
    first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    following = (first + timedelta(days=95)).replace(day=1)
    return first, following - timedelta(days=1)


def resolve(spec: dict[str, Any], *, user_id: str, today: date) -> dict[str, Any]:
    """The spec `crm_query()` runs: `is_me` as the person's id, a calendar
    operator as the days it spans, LIKE's wildcards escaped, and the
    person's today for the `within` operators."""

    def node(n: Any) -> Any:
        if not isinstance(n, dict):
            return n
        if "items" in n:
            return {**n, "items": [node(i) for i in n["items"]]}
        op = n.get("operator")
        if op == "is_me":
            return {"field": n["field"], "operator": "is", "value": user_id}
        if op in ("this_month", "this_quarter"):
            first, last = (month_bounds if op == "this_month" else quarter_bounds)(today)
            return {
                "op": "and",
                "items": [
                    {"field": n["field"], "operator": "after", "value": _iso(first, -1)},
                    {"field": n["field"], "operator": "before", "value": _iso(last, 1)},
                ],
            }
        if op in ("contains", "not_contains") and isinstance(n.get("value"), str):
            return {**n, "value": escape_like(n["value"])}
        return n

    out = dict(spec)
    if out.get("filters") is not None:
        out["filters"] = node(out["filters"])
    if out.get("q"):
        out["q"] = escape_like(out["q"].strip())
    out["today"] = today.isoformat()
    return out


def _iso(day: date, shift: int) -> str:
    return (day + timedelta(days=shift)).isoformat()
