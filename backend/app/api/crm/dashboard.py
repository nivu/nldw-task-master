"""The pipeline dashboard — spec 007 contracts §2.10 `GET /crm/dashboard`;
FR-CRM-DASH-01 to -04, FR-CRM-CAP-02, FR-CRM-ACC-11.

HTTP only. Admins see the workspace, Members their own numbers, Viewers
nothing (403): `services/crm/dashboard.py` decides. A read, so it defaults
to the last-used workspace (D13).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmReadDep
from app.services.crm import dashboard as service

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/dashboard")
def read_dashboard(
    ctx: CrmReadDep,
    entity: Literal["nunnari_labs", "deepweaver"] | None = None,
    owner_id: Annotated[str | None, Query(max_length=64)] = None,
    channel_id: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[date | None, Query(alias="from")] = None,
    until: Annotated[date | None, Query(alias="to")] = None,
) -> dict:
    """Every panel of FR-CRM-DASH-02 (-03 in Academy), amounts in INR,
    per-owner figures in name order; Labs adds `capacity` for admins and
    staff members."""
    return service.dashboard(
        ctx, entity=entity, owner_id=owner_id, channel_id=channel_id, since=since, until=until
    )
