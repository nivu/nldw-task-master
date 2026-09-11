"""Timesheet endpoints — spec 002 §5.3, §5.4.

Split by audience: `/timesheet/*` is a person's own record, `/analytics/*` is
what a lead or admin sees. The split is not cosmetic — Q-08 limits an
individual's timesheet to the person, their lead and admins, and keeping the
routes apart keeps that rule in one place per audience rather than as a
condition inside a shared handler.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, LeadDep, ManagerDep
from app.api.errors import ProblemDetail
from app.domain.approval import Person, can_view_timesheet
from app.domain.calendar import today_in_company_tz
from app.schemas import TimesheetDay
from app.services import analytics as analytics_service
from app.services import supabase as db
from app.services import timesheets as timesheet_service

router = APIRouter(prefix="/timesheet", tags=["timesheet"])


@router.get("/day")
def my_day(
    user: CurrentUserDep,
    day: date | None = Query(default=None, description="Defaults to today"),
) -> dict:
    """Everything the log-a-day screen needs, in one request — NFR-01/02."""
    return timesheet_service.day_for(user.id, day or today_in_company_tz())


@router.put("/day")
def save_my_day(payload: TimesheetDay, user: CurrentUserDep) -> dict:
    """Replace a day's lines with what was submitted — FR-TIME-01/03/05/08.

    A person logs only for themselves. There is no `user_id` in the payload,
    deliberately: accepting one would make "log time on somebody else's behalf"
    a request-shaped decision rather than a product decision nobody has made.
    """
    try:
        return timesheet_service.save_day(
            user_id=user.id,
            day=payload.date,
            lines=[line.model_dump() for line in payload.lines],
            actor_id=user.id,
        )
    except timesheet_service.TimesheetRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc


@router.get("/week")
def my_week(
    user: CurrentUserDep,
    week_start: date | None = Query(default=None, description="Monday; defaults to this week"),
) -> dict:
    return timesheet_service.week_for(user.id, _monday(week_start))


@router.get("/of/{user_id}/week")
def someone_elses_week(user_id: str, user: CurrentUserDep, week_start: date | None = None) -> dict:
    """A report's week — 002 Q-08, widened to managers by spec 003 §7.

    `can_view_timesheet`, not `can_view_reason`. They were one function until
    managers arrived: a manager sees every project's hours and therefore every
    person's timesheet, but "runs the projects" is no reason to read a
    colleague's sick-leave reason. Keeping the two rules apart is what stops
    widening one from widening the other.
    """
    subject_row = db.get_profile(user_id)
    if subject_row is None:
        raise ProblemDetail(404, "No such person.")

    subject = Person(
        id=subject_row["id"],
        role=subject_row["role"],
        lead_id=subject_row["lead_id"],
        is_active=subject_row["is_active"],
    )
    if not can_view_timesheet(user, subject):
        # 404 rather than 403: confirming that a colleague's timesheet exists
        # already says something about them.
        raise ProblemDetail(404, "No such person.")

    return {
        "user_id": user_id,
        "display_name": subject_row["display_name"],
        **timesheet_service.week_for(user_id, _monday(week_start)),
    }


# ---------------------------------------------------------------------------
# Analytics — lead and admin
# ---------------------------------------------------------------------------

analytics = APIRouter(prefix="/analytics", tags=["analytics"])


def _population(user) -> list[str]:  # noqa: ANN001
    """Whose data this person may aggregate.

    Managers see everyone (spec 003 FR-ROLE-05, Q-02 — chosen against the
    recommendation; the consequence is recorded in spec 003 §3). Leads see
    their reports, as in 002.
    """
    if user.is_manager:
        return [p["id"] for p in db.list_profiles(active_only=True)]
    return [p["id"] for p in db.list_reports(user.id, active_only=True)]


@analytics.get("/projects")
def project_list(user: LeadDep) -> list[dict]:
    """Every project with its logged total — the entry point to the detail."""
    projects = db.list_projects(include_archived=True)
    entries = db.list_time_entries()
    from decimal import Decimal

    totals: dict[str, Decimal] = {}
    for entry in entries:
        if not entry.get("project_id"):
            continue  # an activity has no project to total under (FR-ACT-04)
        totals[entry["project_id"]] = (
            totals.get(entry["project_id"], Decimal("0"))
            + Decimal(str(entry["hours_office"]))
            + Decimal(str(entry["hours_home"]))
        )

    return [
        {
            "id": p["id"],
            "name": p["name"],
            "client": p.get("client"),
            "is_archived": p["is_archived"],
            "logged_hours": str(totals.get(p["id"], Decimal("0"))),
        }
        for p in projects
    ]


@analytics.get("/projects/{project_id}")
def project_detail(project_id: str, user: LeadDep) -> dict:
    """FR-ANALYTICS-02/03 — the budget conversation."""
    result = analytics_service.project_effort(project_id)
    if not result:
        raise ProblemDetail(404, "No such project.")
    return result


@analytics.get("/coverage")
def timesheet_coverage(
    user: LeadDep,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """FR-ANALYTICS-05 — how much of the timesheet actually exists.

    Read this before believing anything on the project pages. Effort totals
    over a partly-filled timesheet are biased low, and get quoted as complete.
    """
    today = today_in_company_tz()
    return analytics_service.coverage(
        _population(user), start or (today - timedelta(days=30)), end or today
    )


@analytics.get("/forecast")
def capacity_forecast(user: LeadDep, start: date | None = None, end: date | None = None) -> dict:
    """FR-ANALYTICS-06 — capacity the allocations imply, net of leave."""
    today = today_in_company_tz()
    return analytics_service.forecast(start or today, end or (today + timedelta(days=90)))


@analytics.get("/current")
def what_the_team_is_doing(user: LeadDep, days: int = Query(default=7, ge=1, le=90)) -> list[dict]:
    """G-6, FR-ANALYTICS-04 — what people have actually been working on."""
    return analytics_service.current_work(_population(user), days=days)


# ---------------------------------------------------------------------------
# Money and resourcing — spec 003. MANAGER AND ADMIN ONLY.
#
# A separate guard from the effort routes above, on purpose. Effort (hours) is
# a lead's business; money is not. Putting these behind LeadDep with a role
# check inside the handler would be one forgotten `if` away from a lead seeing
# every cost rate in the company.
# ---------------------------------------------------------------------------


@analytics.get("/projects/{project_id}/financials")
def project_financials(project_id: str, user: ManagerDep) -> dict:
    """Revenue, COGS, margin, per-person attribution — FR-FIN-04/06."""
    result = analytics_service.project_financials(project_id)
    if not result:
        raise ProblemDetail(404, "No such project.")
    return result


@analytics.get("/people/financials")
def people_financials(user: ManagerDep) -> dict:
    """Hours, COGS and attributed revenue per person across projects — FR-FIN-05."""
    return analytics_service.people_financials()


@analytics.get("/resources")
def resources(user: ManagerDep, start: date | None = None, end: date | None = None) -> dict:
    """Who is on what, week by week, present and future — FR-RES-01..04."""
    today = today_in_company_tz()
    return analytics_service.resources_timeline(
        start or (today - timedelta(days=14)), end or (today + timedelta(days=90))
    )


@analytics.get("/pnl")
def monthly_profit(user: ManagerDep, start: str | None = None, end: str | None = None) -> dict:
    """Revenue, cost and profit per person and per project, month by month —
    spec 005 FR-PNL. `start`/`end` are YYYY-MM; default three months back to
    five ahead. Past months are actual (logged hours), the current and future
    months planned (allocations)."""
    from app.services import pnl as pnl_service

    return pnl_service.monthly(start, end)


@analytics.get("/timeline")
def allocation_timeline(
    user: ManagerDep, start: date | None = None, end: date | None = None
) -> dict:
    """Allocations as bars per person — spec 005 FR-TL. Default: this month
    back to six months ahead."""
    from app.services import pnl as pnl_service

    today = today_in_company_tz()
    return pnl_service.timeline(start or today.replace(day=1), end or (today + timedelta(days=183)))


@analytics.get("/people")
def people_for_allocation(user: ManagerDep) -> list[dict]:
    """Everyone who can be allocated — name and id, nothing else.

    Managers cannot reach /admin/users (FR-ROLE-03), so the allocation picker
    needs a list that carries no email, no role and certainly no cost rate.
    """
    return [
        {"id": p["id"], "display_name": p["display_name"]}
        for p in sorted(db.list_profiles(active_only=True), key=lambda p: p["display_name"])
    ]


def _monday(week_start: date | None) -> date:
    day = week_start or today_in_company_tz()
    return day - timedelta(days=day.weekday())
