"""The CEO dashboard — spec 003 FR-DASH.

Seen only by the owner and the people the owner authorises (`DashboardDep`),
whatever their role. The figures come from the same services as the detail
pages, and those pages keep their own role guards: authorisation here opens
this summary and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DashboardDep
from app.services import dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(user: DashboardDep) -> dict:
    """Today, data health, money, cash, delivery, people, twelve months of
    trend and what needs attention — FR-DASH-04..09."""
    return dashboard.summary(breakdown=user.is_manager)
