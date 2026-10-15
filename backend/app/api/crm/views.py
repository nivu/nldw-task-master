"""Views — spec 007 contracts §2.7 and `POST /crm/bulk` (§2.5);
FR-CRM-VIEW-02 to -05, -07, -08, -10, -11, FR-CRM-ACC-11, FR-CRM-UI-02.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/views.py` does the rest. `POST /crm/query` is what every
table, kanban and the `crm_list` MCP tool read through; it only reads, so it
defaults to the last-used workspace as a GET does (D13). Saved views and
favourites are managed by any role, the service deciding which (a Viewer's
are private, Q-37); bulk actions are for Admins and Members, each record then
held to its own rules.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.crm.deps import CrmQueryDep, CrmReadDep, CrmWriteDep
from app.schemas.crm import BulkIn, FavouriteIn, QueryIn, ViewIn, ViewObject, ViewUpdate
from app.services.crm import views as service

router = APIRouter(prefix="/crm", tags=["crm"])


@router.post("/query")
def run_query(payload: QueryIn, ctx: CrmQueryDep) -> dict:
    """`{rows, total | "10000+", groups?, aggregates?, next?, notice?}`. A
    Viewer's filter, sort, group or total on an amount is 422; a saved view
    with one opens without it, and says so."""
    return service.query(ctx, payload)


@router.get("/views")
def list_views(
    ctx: CrmReadDep, object: ViewObject | None = None, include_hidden: bool = False
) -> list[dict]:
    """The shared views and the caller's private ones; `include_hidden=true`
    adds the hidden seeded views for Admins and Members."""
    return service.list_views(ctx, object, include_hidden=include_hidden)


@router.post("/views", status_code=201)
def create_view(payload: ViewIn, ctx: CrmReadDep) -> dict:
    return service.create_view(ctx, payload)


@router.patch("/views/{view_id}")
def update_view(view_id: str, payload: ViewUpdate, ctx: CrmReadDep) -> dict:
    return service.update_view(ctx, view_id, payload)


@router.delete("/views/{view_id}")
def delete_view(view_id: str, ctx: CrmReadDep) -> dict:
    """A seeded view is hidden, not deleted."""
    return service.delete_view(ctx, view_id)


@router.get("/favourites")
def list_favourites(ctx: CrmReadDep) -> list[dict]:
    return service.list_favourites(ctx)


@router.post("/favourites", status_code=201)
def add_favourite(payload: FavouriteIn, ctx: CrmReadDep) -> dict:
    return service.add_favourite(ctx, payload)


@router.delete("/favourites/{favourite_id}")
def remove_favourite(favourite_id: str, ctx: CrmReadDep) -> dict:
    return service.remove_favourite(ctx, favourite_id)


@router.post("/bulk")
def bulk(payload: BulkIn, ctx: CrmWriteDep) -> dict:
    """`{ok: [ids], failed: [{id, reason}]}`; deleting is held per record to
    who may delete it (FR-CRM-DEL-04)."""
    return service.bulk(ctx, payload)
