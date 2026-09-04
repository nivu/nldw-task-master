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

from app.api.deps import CurrentUserDep, LeadDep
from app.api.errors import ProblemDetail
from app.domain.approval import Person, can_view_reason
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
    """A report's week — Q-08.

    Reuses `can_view_reason` from `001` rather than reimplementing the rule.
    Who may read a colleague's timesheet is the same question as who may read
    their leave reason, and answering it twice is how the two answers drift.
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
    if not can_view_reason(user, subject):
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
    """Whose data this person may aggregate — the same rule as the team view."""
    if user.is_admin:
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


def _monday(week_start: date | None) -> date:
    day = week_start or today_in_company_tz()
    return day - timedelta(days=day.weekday())
