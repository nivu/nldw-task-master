"""Today — spec 007 contracts §2.8 `GET /crm/today`; FR-CRM-TODAY-01 to -04,
FR-CRM-CHG-13.

HTTP only. Any member reads their own Today; `person` names another member,
which only a workspace Admin may (the service checks it). The same route
serves the web app's home and the `crm_today` MCP tool.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmReadDep
from app.services.crm import today as service

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/today")
def read_today(ctx: CrmReadDep, person: str | None = Query(default=None, max_length=64)) -> dict:
    """`{as_of, timezone, person, sections: [{key, off_reason?, items}]}`."""
    return service.today(ctx, person)
