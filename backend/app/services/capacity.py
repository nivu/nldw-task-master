"""The one capacity service — spec 007 FR-CRM-CHG-17, FR-CRM-CAP-03.

TVA's forecast (`analytics.forecast`, its `weeks`) and the CRM capacity check
(`GET /crm/capacity`) both read `weekly`, so the two never disagree. The
arithmetic is `domain/capacity.py`; this module only reads its inputs.

Supply is every active staff account (CRM-only accounts are no TVA population,
FR-CRM-CHG-02), the same people as the allocation picker and the hiring
signal. Holidays are the ones that apply to each person by location (spec 006
FR-LOC-02). Supply is reduced by approved leave only, since a pending request
is not yet time off; demand keeps `leave_days_for`'s approved and pending
leave, the same booked leave the forecast's allocated hours already net out.
Demand is the allocations that count (`list_counting_allocations`), for the
same people and days as supply.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.domain import capacity as calc
from app.domain import timesheets as rules
from app.services import pnl as pnl_service
from app.services import supabase as db
from app.services.holidays import holidays_by_person
from app.services.timesheets import leave_days_for


def weekly(start: date, end: date) -> list[dict[str, Any]]:
    """Per week touching `start`..`end`: `week_start`, `supply_hours`,
    `confirmed_hours`, `tentative_hours` (decimal strings) and
    `contributors` (how many people have supply that week)."""
    profiles = db.list_profiles(active_only=True)
    people = [calc.Person(p["id"], *rules.employment(p)) for p in profiles]
    tentative_ids = {
        p["id"] for p in db.list_projects(include_archived=True) if rules.is_tentative(p)
    }
    allocations = [
        rules.Allocation(
            user_id=a["user_id"],
            project_id=a["project_id"],
            starts_on=date.fromisoformat(a["starts_on"]),
            ends_on=date.fromisoformat(a["ends_on"]),
            percent=Decimal(str(a["percent"])),
            tentative=a["project_id"] in tentative_ids,
        )
        for a in db.list_counting_allocations()
    ]
    user_ids = sorted({p.user_id for p in people} | {a.user_id for a in allocations})
    weeks = calc.weekly(
        start,
        end,
        people=people,
        allocations=allocations,
        holidays=holidays_by_person(user_ids, start, end),
        leave=leave_days_for(user_ids, start, end),
        approved_leave=leave_days_for(user_ids, start, end, statuses=frozenset({"approved"})),
        hours_on=pnl_service.contracted_hours(),
    )
    return [w.as_dict() for w in weeks]
