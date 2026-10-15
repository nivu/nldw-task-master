"""The capacity check — spec 007 contracts §2.8; FR-CRM-CAP-01 to -03.

For the coming weeks, the hours tentative projects would need against the
hours nobody is yet promised to (supply less confirmed demand), so a staffing
problem shows before a deal is won. The figures are the forecast's own
(`services/capacity.py`, FR-CRM-CAP-03).

Totals only, never a person, and a week with fewer than 3 people supplying it
is hidden: with two people, one person's leave could be read off the total
(Q-39).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Query

from app.api.crm.deps import CrmCapacityDep
from app.domain.calendar import today_in_company_tz
from app.domain.crm.dashboard import capacity_weeks
from app.services import capacity

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/capacity")
def capacity_check(
    ctx: CrmCapacityDep,
    weeks: int = Query(default=13, ge=1, le=13, description="Weeks from this one; 13 is 90 days."),
) -> dict:
    """The rule a week is shown by is the dashboard's too (S11b), so both
    show the same weeks."""
    today = today_in_company_tz()
    monday = today - timedelta(days=today.weekday())
    rows = capacity.weekly(today, monday + timedelta(days=7 * weeks - 1))
    return {"weeks": capacity_weeks(rows)}
