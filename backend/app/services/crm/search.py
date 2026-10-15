"""Search — spec 007 FR-CRM-SRCH-01 to -03, FR-CRM-WS-02, FR-CRM-ACC-11;
contracts §2.8 `GET /crm/search`; plan §5.8.

The Cmd/Ctrl+K search: one `crm_search()` call (035, 041) across
companies, people, deals, tasks, notes and (1b) campaigns of the caller's
workspace, and the saved views they can see by name, so the command menu
can jump to either.

Every hit is presented here, so a Viewer's deal hits carry no amount: the
database read none (`p_redact`), and the presenter drops the keys anyway.
A task or note has no page of its own, so its hit says which record it
`opens`: the first one it is linked to, as the Tasks list does.
"""

from __future__ import annotations

from typing import Any

from app.services.crm import CrmRefused, store
from app.services.crm import present as show
from app.services.crm import views as saved

#: Contracts §2.8.
MIN_CHARS = 2
MAX_PER_OBJECT = 8
TOO_SHORT = "Type at least two characters."

#: The answer's keys, and the kind each one holds.
_GROUPS = (
    ("companies", "company"),
    ("people", "person"),
    ("opportunities", "opportunity"),
    ("tasks", "task"),
    ("notes", "note"),
    ("campaigns", "campaign"),
)


def search(ctx: Any, q: str, limit: int = MAX_PER_OBJECT) -> dict[str, list[dict[str, Any]]]:
    """`{companies, people, opportunities, tasks, notes, campaigns, views}`,
    each best first, at most `limit` (≤ 8) of each."""
    text = " ".join(q.split())
    if len(text) < MIN_CHARS:
        raise CrmRefused(422, TOO_SHORT)
    limit = max(1, min(limit, MAX_PER_OBJECT))
    found = store.search(ctx.workspace_id, text, limit, redact=not ctx.may("see_amounts"))
    options = {o["id"]: o for o in store.list_options(ctx.workspace_id)}
    facts = show.Facts(options=options, role=ctx.role)
    out: dict[str, list[dict[str, Any]]] = {
        key: [show.search_hit(kind, row, facts) for row in found.get(key) or []][:limit]
        for key, kind in _GROUPS
    }
    for key, table, owner in (
        ("tasks", "crm_task_links", "task_id"),
        ("notes", "crm_note_links", "note_id"),
    ):
        links = store.list_links(ctx.workspace_id, table, owner_ids=[h["id"] for h in out[key]])
        first: dict[str, dict[str, str]] = {}
        for link in links:
            first.setdefault(
                link[owner], {"record_type": link["record_type"], "record_id": link["record_id"]}
            )
        for hit in out[key]:
            hit["opens"] = first.get(hit["id"])
    wanted = text.lower()
    out["views"] = [
        {
            "id": v["id"],
            "object": v["object"],
            "name": v["name"],
            "icon": v.get("icon"),
            "kind": v["kind"],
            "system_key": v.get("system_key"),
        }
        for v in saved.list_views(ctx, None)
        if wanted in v["name"].lower()
    ][:limit]
    return out
