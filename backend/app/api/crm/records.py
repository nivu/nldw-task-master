"""Records — spec 007 contracts §2.5 (S3: companies, people, Drive links,
Trash, duplicates, timeline; S4: opportunities, tasks, notes); FR-CRM-CO,
-PER, -OPP, -TASK, -NOTE, -LINK, -DUP-01, -02, -DEL-01, -02, -04, -REC-01 to
-04, -AUD-05.

HTTP only: each route resolves the caller's workspace and role through the
CRM guard and hands the rest to `services/crm/records.py`, or for a deal's
create and edit to `services/crm/deals.py`. The same routes serve the web app
and the `crm_get`, `crm_create`, `crm_update`, `crm_set_next_step`,
`crm_add_task`, `crm_complete_task` and `crm_add_note` MCP tools
(FR-CRM-MCP-07).

Each object gets its own literal paths (`/crm/companies`, `/crm/people`)
rather than one `/crm/{object}` pattern, so a record route can never swallow
another CRM path such as `/crm/settings`. Later slices add their objects to
`OBJECTS`.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from app.api.crm.deps import CrmAdminDep, CrmReadDep, CrmWriteDep
from app.schemas.crm import CompanyDeleteIn, DriveLinkIn, DuplicatesIn, MoveDealsTo
from app.services.crm import deals, present
from app.services.crm import records as service

router = APIRouter(prefix="/crm", tags=["crm"])

#: Contracts §2.5 "Objects in paths", the ones that exist so far.
OBJECTS = ("companies", "people", "opportunities", "tasks", "notes")
#: FR-CRM-DUP-02 — the objects checked for names alike.
DEDUPED = ("companies", "people")
TrashObject = Literal["companies", "people", "opportunities", "tasks", "notes"]
TaskStatus = Literal["todo", "in_progress", "done", "open"]


# ---------------------------------------------------------------------------
# Trash and Drive links — fixed paths first
# ---------------------------------------------------------------------------


@router.get("/trash")
def read_trash(
    ctx: CrmAdminDep,
    object: TrashObject = "companies",
    limit: int = Query(default=50, ge=1, le=200),
    after: str | None = Query(default=None, max_length=20),
) -> dict:
    """FR-CRM-DEL-01 — Admins see the Trash, with the days left before purge."""
    return service.trash(ctx, service.PLURALS[object], limit=limit, after=after)


@router.post("/links", status_code=201)
def add_drive_link(payload: DriveLinkIn, ctx: CrmWriteDep) -> dict:
    """FR-CRM-LINK-01, -02 — an `https://` address and a label; no content."""
    return service.add_link(ctx, payload)


@router.delete("/links/{link_id}")
def delete_drive_link(link_id: str, ctx: CrmWriteDep) -> dict:
    return service.delete_link(ctx, link_id)


# ---------------------------------------------------------------------------
# One set of routes per object
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, ctx: CrmReadDep) -> dict:
    """Q-57 — Admins and Members, or a Viewer on a task assigned to them; the
    service checks which."""
    return service.complete_task(ctx, task_id)


@router.get("/tasks")
def list_tasks(
    ctx: CrmReadDep,
    q: str | None = Query(default=None, max_length=200),
    assignee_id: str | None = Query(default=None, max_length=64),
    status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    after: str | None = Query(default=None, max_length=20),
) -> dict:
    """The Tasks page: newest first, narrowed to an assignee and a status
    (`open` is anything not Done)."""
    filters = {"assignee_id": assignee_id} if assignee_id else {}
    if status and status != "open":
        filters["status"] = status
    return service.list_records(
        ctx, "task", q=q, limit=limit, after=after, filters=filters, open_only=status == "open"
    )


def _register(plural: str) -> None:
    kind = service.PLURALS[plural]

    def list_records(
        ctx: CrmReadDep,
        q: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=50, ge=1, le=200),
        after: str | None = Query(default=None, max_length=20),
    ) -> dict:
        """Newest first, paged. The plain list of related panels and pickers;
        tables and kanbans read through `POST /crm/query` (contracts §2.5)."""
        return service.list_records(ctx, kind, q=q, limit=limit, after=after)

    def create(
        ctx: CrmWriteDep,
        payload: dict[str, Any] = Body(...),
        check_duplicates: bool = False,
    ) -> Any:
        """FR-CRM-DUP-02 — with `check_duplicates=true` and names alike,
        nothing is created and the possible duplicates come back (200)."""
        if kind == "opportunity":
            return JSONResponse(status_code=201, content=deals.create(ctx, payload))
        made = service.create(ctx, kind, payload, check_duplicates=check_duplicates)
        if made.get("created") is False:
            return JSONResponse(status_code=200, content=made)
        return JSONResponse(status_code=201, content=made)

    def find_duplicates(payload: DuplicatesIn, ctx: CrmWriteDep) -> dict:
        return service.duplicates(
            ctx, kind, name=payload.name, domain=payload.domain, email=payload.email
        )

    def read(record_id: str, ctx: CrmReadDep) -> dict:
        return service.get(ctx, kind, record_id)

    def update(record_id: str, ctx: CrmWriteDep, payload: dict[str, Any] = Body(...)) -> dict:
        if kind == "opportunity":
            return deals.update(ctx, record_id, payload)
        return service.update(ctx, kind, record_id, payload)

    def update_task(record_id: str, ctx: CrmReadDep, payload: dict[str, Any] = Body(...)) -> dict:
        """Q-57 — a Viewer may change the status of a task assigned to them,
        and nothing else; the service checks it."""
        return service.update(ctx, kind, record_id, payload)

    def delete(
        record_id: str,
        ctx: CrmWriteDep,
        payload: CompanyDeleteIn | None = Body(default=None),
    ) -> dict:
        """FR-CRM-DEL-02, -04. A company says what happens to its people and
        deals."""
        people = payload.people if payload else None
        chosen = payload.deals if payload else None
        return service.soft_delete(
            ctx,
            kind,
            record_id,
            people=people,
            deals="delete" if chosen == "delete" else None,
            move_deals_to=chosen.move_to if isinstance(chosen, MoveDealsTo) else None,
        )

    def restore(record_id: str, ctx: CrmAdminDep) -> dict:
        return service.restore(ctx, kind, record_id)

    def purge(record_id: str, ctx: CrmAdminDep) -> dict:
        return service.purge(ctx, kind, record_id)

    def timeline(
        record_id: str,
        ctx: CrmReadDep,
        types: str | None = Query(default=None, max_length=200),
        before: int | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict]:
        wanted = (
            {t.strip() for t in types.split(",")} & set(present.TIMELINE_TYPES) if types else None
        )
        return service.timeline(ctx, kind, record_id, types=wanted, before=before, limit=limit)

    base = f"/{plural}"
    add = router.add_api_route
    if plural != "tasks":
        add(base, list_records, methods=["GET"], name=f"{plural}_list")
    add(base, create, methods=["POST"], status_code=201, name=f"{plural}_create")
    if plural in DEDUPED:
        add(f"{base}/duplicates", find_duplicates, methods=["POST"], name=f"{plural}_duplicates")
    add(f"{base}/{{record_id}}", read, methods=["GET"], name=f"{plural}_get")
    add(
        f"{base}/{{record_id}}",
        update_task if plural == "tasks" else update,
        methods=["PATCH"],
        name=f"{plural}_update",
    )
    add(f"{base}/{{record_id}}", delete, methods=["DELETE"], name=f"{plural}_delete")
    add(f"{base}/{{record_id}}/restore", restore, methods=["POST"], name=f"{plural}_restore")
    add(f"{base}/{{record_id}}/permanent", purge, methods=["DELETE"], name=f"{plural}_purge")
    add(f"{base}/{{record_id}}/timeline", timeline, methods=["GET"], name=f"{plural}_timeline")


for _plural in OBJECTS:
    _register(_plural)
