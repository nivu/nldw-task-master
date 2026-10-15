"""Search — spec 007 contracts §2.8 `GET /crm/search`; FR-CRM-SRCH-01 to -03.

HTTP only. Any member searches their workspace; a Viewer's hits carry no
amount. The same route serves the command menu and the `crm_search` MCP
tool.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmReadDep
from app.services.crm import search as service

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/search")
def search(
    ctx: CrmReadDep,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=service.MAX_PER_OBJECT, ge=1, le=service.MAX_PER_OBJECT),
) -> dict:
    """`{companies, people, opportunities, tasks, notes, campaigns, views}`,
    best first, at most `limit` of each."""
    return service.search(ctx, q, limit)
