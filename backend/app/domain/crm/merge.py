"""Company merge — spec 007 FR-CRM-DUP-03, -04, FR-CRM-PIPE-04; contracts
§2.8 `merge-preview` and `merge`; data-model §5.7.

Basic merge, companies only (Phase 1). The person picks the surviving
company and, for each field the two hold differently, whose value it keeps;
everything attached to the other company then moves to the survivor in one
database call (`crm_merge_companies`, 035), and the merged company's id
resolves to the survivor.

What a person chooses between is a company's own fields: never one the
system writes (last contact, flags, the Twenty id), never one of the other
workspace's (Entity is Labs's, Institution type Academy's). Additional
domains are not chosen at all: the survivor keeps every domain of both, and
the person picks which one is primary.

Pure: the service gathers the two rows and their domains.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from app.domain.crm import objects, pipeline

Side = Literal["a", "b"]

#: The fields a merge chooses between, in the order the dialog shows them.
#: `domain` is the primary domain (in `crm_company_domains`, not a column).
CHOOSABLE: Final = tuple(
    f.name for f in objects.COMPANY if not f.system and f.name not in ("additional_domains",)
)

SAME_COMPANY: Final = "Pick two different companies to merge."


def choosable(*, tva_linked: bool) -> tuple[str, ...]:
    """The fields of this workspace (FR-CRM-WS-06): Entity in Labs only,
    Institution type in Academy only."""
    out = []
    for name in CHOOSABLE:
        scope = objects.field("company", name).scope  # type: ignore[union-attr]
        if (scope == "labs" and not tva_linked) or (scope == "academy" and tva_linked):
            continue
        out.append(name)
    return tuple(out)


def _comparable(value: Any) -> Any:
    """Equal values compare equal: a multi-select in any order, an empty
    list, string or object as nothing."""
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        return sorted(value, key=str)
    return value


def primary_domain(domains: list[dict[str, Any]], company_id: str) -> str | None:
    return next(
        (d["domain"] for d in domains if d["company_id"] == company_id and d["is_primary"]),
        None,
    )


def value_of(row: dict[str, Any], name: str, domains: list[dict[str, Any]]) -> Any:
    if name == "domain":
        return primary_domain(domains, row["id"])
    return row.get(name)


def differing(
    a: dict[str, Any],
    b: dict[str, Any],
    domains: list[dict[str, Any]],
    *,
    tva_linked: bool,
) -> list[str]:
    """The fields the two companies hold differently, in dialog order. A
    field only one of them has filled is still a choice: keeping the empty
    one would lose the other's value, so the default is the filled one."""
    return [
        name
        for name in choosable(tva_linked=tva_linked)
        if _comparable(value_of(a, name, domains)) != _comparable(value_of(b, name, domains))
    ]


def default_side(name: str, a: dict, b: dict, domains: list[dict[str, Any]]) -> Side:
    """The survivor's value, unless only the merged company has one."""
    mine = _comparable(value_of(a, name, domains))
    theirs = _comparable(value_of(b, name, domains))
    return "b" if mine is None and theirs is not None else "a"


def choice_refusal(choices: dict[str, str], allowed: tuple[str, ...]) -> str | None:
    """A choice must name a field of this workspace's companies, and a side."""
    for name, side in choices.items():
        if name not in allowed:
            return f"{name} is not a field you can choose in a merge."
        if side not in ("a", "b"):
            return f"Choose a or b for {objects.label('company', name).lower()}."
    return None


def survivor_changes(
    survivor: dict[str, Any],
    merged: dict[str, Any],
    domains: list[dict[str, Any]],
    choices: dict[str, str],
    *,
    tva_linked: bool,
) -> tuple[dict[str, Any], str | None]:
    """What the survivor takes from the merged company, and its primary
    domain afterwards. `choices` maps a field to "a" (the survivor's value)
    or "b" (the merged company's); a differing field it leaves out keeps
    `default_side`."""
    changes: dict[str, Any] = {}
    primary = primary_domain(domains, survivor["id"])
    for name in differing(survivor, merged, domains, tva_linked=tva_linked):
        side = choices.get(name) or default_side(name, survivor, merged, domains)
        if side != "b":
            continue
        if name == "domain":
            primary = primary_domain(domains, merged["id"])
        else:
            changes[name] = merged.get(name)
    if primary is None:
        primary = primary_domain(domains, merged["id"])
    return changes, primary


def lead_stage_after(
    chosen: str, *, has_open_deals: bool, name: str, chosen_label: str
) -> tuple[str, str | None]:
    """FR-CRM-PIPE-04 — the merged company keeps every open deal of both, so
    it sits at Qualified or later: Qualified becomes Deal open, and an
    earlier or Disqualified stage is refused. Returns (stage, refusal)."""
    if not has_open_deals or chosen in ("deal_open", "customer", "partner"):
        return chosen, None
    if chosen == "qualified":
        return "deal_open", None
    return chosen, (
        f"{name} will hold open deals, so its lead stage cannot be {chosen_label}; "
        "keep the other company's."
    )


def has_open(deals: list[dict[str, Any]]) -> bool:
    return any(d.get("stage") in pipeline.OPEN for d in deals if not d.get("deleted_at"))
