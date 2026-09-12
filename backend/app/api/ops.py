"""Org operations — spec 006. Several small routers, mounted by main.py."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse, Response

from app.api.deps import AdminDep, CurrentUserDep, LeadDep, ManagerDep, SessionDep
from app.api.errors import ProblemDetail
from app.config import settings
from app.domain.approval import Person
from app.domain.calendar import today_in_company_tz
from app.schemas import (
    ChecklistItemUpdate,
    ChecklistStart,
    ChecklistTemplateIn,
    CompoffClaim,
    CompoffDecision,
    ConfirmWeek,
    LocationIn,
    MilestoneIn,
    MilestoneUpdate,
    NudgeRun,
    OAuthApprove,
    ReviewIn,
)
from app.services import audit, confirmations, digest, statements, utilisation
from app.services import checklists as checklist_service
from app.services import compoff as compoff_service
from app.services import feeds as feed_service
from app.services import health as health_service
from app.services import reviews as review_service
from app.services import supabase as db
from app.services import timesheets as timesheet_service

# ---------------------------------------------------------------------------
# Comp-off — FR-COMP
# ---------------------------------------------------------------------------

compoff = APIRouter(prefix="/compoff", tags=["compoff"])


def _names() -> dict[str, str]:
    return {p["id"]: p["display_name"] for p in db.list_profiles()}


@compoff.get("")
def my_compoff(user: CurrentUserDep) -> dict:
    """My claims and how many comp-off days I can book right now."""
    names = _names()
    return {
        "available": str(compoff_service.available(user.id)),
        "valid_days": compoff_service.valid_days(),
        "claims": [
            compoff_service.present(c, names) for c in db.list_compoff_credits(user_ids=[user.id])
        ],
    }


@compoff.post("", status_code=201)
def claim_compoff(payload: CompoffClaim, user: CurrentUserDep) -> dict:
    try:
        row = compoff_service.claim(
            user_id=user.id, worked_on=payload.worked_on, days=payload.days, note=payload.note
        )
    except compoff_service.CompoffRefused as exc:
        raise ProblemDetail(exc.status, exc.detail) from exc
    return compoff_service.present(row, _names())


@compoff.get("/team")
def team_compoff(user: LeadDep) -> list[dict]:
    """Claims from the people this person may decide for, pending first."""
    from app.api.team import _population

    people = _population(user)
    names = _names()
    rows = db.list_compoff_credits(user_ids=[p["id"] for p in people])
    return sorted(
        (compoff_service.present(c, names) for c in rows),
        key=lambda c: (c["status"] != "pending", c["display_name"], c["worked_on"]),
    )


@compoff.post("/{credit_id}/decision")
def decide_compoff(credit_id: str, payload: CompoffDecision, user: LeadDep) -> dict:
    try:
        row = compoff_service.decide(
            credit_id=credit_id, actor=user, approve=payload.approve, note=payload.note
        )
    except compoff_service.CompoffRefused as exc:
        raise ProblemDetail(exc.status, exc.detail) from exc
    return compoff_service.present(row, _names())


# ---------------------------------------------------------------------------
# Team: weekly sign-off and quarterly reviews — FR-SIGN, FR-REV
# ---------------------------------------------------------------------------

team = APIRouter(prefix="/team", tags=["team"])


@team.get("/timesheets")
def team_weeks(user: LeadDep, week_start: date | None = None) -> dict:
    """Each report's week: total hours, missing days, and confirmation status."""
    from app.api.team import _population

    monday = confirmations.monday_of(week_start or today_in_company_tz())
    people = sorted(_population(user), key=lambda p: p["display_name"])
    status = confirmations.status_for([p["id"] for p in people], monday)
    rows = []
    for person in people:
        week = timesheet_service.week_for(person["id"], monday)
        conf = status.get(person["id"])
        rows.append(
            {
                "user_id": person["id"],
                "display_name": person["display_name"],
                "total": week["total"],
                "days": [
                    {
                        "date": d["date"],
                        "total": d["total"],
                        "holiday": d["holiday"],
                        "on_leave": d["on_leave"],
                    }
                    for d in week["days"]
                ],
                "missing_days": [
                    d["date"]
                    for d in week["days"]
                    if not d["holiday"]
                    and not d["on_leave"]
                    and Decimal(d["total"]) == 0
                    and date.fromisoformat(d["date"]).weekday() < 5
                    and d["date"] <= today_in_company_tz().isoformat()
                ],
                "confirmation": None
                if conf is None
                else {
                    "status": conf["status"],
                    "confirmed_at": conf["confirmed_at"],
                    "note": conf.get("note"),
                },
            }
        )
    return {"week_start": monday.isoformat(), "people": rows}


@team.post("/timesheets/{user_id}/{week_start}/confirm")
def confirm_week(user_id: str, week_start: date, payload: ConfirmWeek, user: LeadDep) -> dict:
    try:
        row = confirmations.confirm(
            user_id=user_id, week_start=week_start, actor=user, note=payload.note
        )
    except confirmations.SignoffRefused as exc:
        raise ProblemDetail(exc.status, exc.detail) from exc
    return {"user_id": user_id, "week_start": week_start.isoformat(), "status": row["status"]}


@team.delete("/timesheets/{user_id}/{week_start}/confirm")
def reopen_week(user_id: str, week_start: date, user: LeadDep) -> dict:
    try:
        confirmations.reopen(user_id=user_id, week_start=week_start, actor=user)
    except confirmations.SignoffRefused as exc:
        raise ProblemDetail(exc.status, exc.detail) from exc
    return {"user_id": user_id, "week_start": week_start.isoformat(), "status": "open"}


@team.get("/reviews")
def team_reviews(user: LeadDep, quarter: str | None = None) -> dict:
    """Reports' quarterly self-summaries with the same figures they saw."""
    from app.api.team import _population

    q = quarter or review_service.quarter_of(today_in_company_tz())
    people = sorted(_population(user), key=lambda p: p["display_name"])
    return {
        "quarter": q,
        "people": [
            {
                "user_id": p["id"],
                "display_name": p["display_name"],
                **review_service.get(p["id"], q),
            }
            for p in people
        ],
    }


@team.post("/reviews/{user_id}/{quarter}/close")
def close_review(user_id: str, quarter: str, user: LeadDep) -> dict:
    subject = db.get_profile(user_id)
    if subject is None:
        raise ProblemDetail(404, "No such person.")
    from app.domain.approval import can_decide

    if not can_decide(
        user, Person(**{k: subject[k] for k in ("id", "role", "lead_id", "is_active")})
    ):
        raise ProblemDetail(403, "Only this person's lead or an admin can close their review.")
    review_service.close(user_id=user_id, quarter=quarter, actor_id=user.id)
    return {"user_id": user_id, "quarter": quarter, "status": "closed"}


# ---------------------------------------------------------------------------
# Me: review, calendar feed — FR-REV, FR-FEED
# ---------------------------------------------------------------------------

me = APIRouter(prefix="/me", tags=["me"])


@me.get("/review")
def my_review(user: CurrentUserDep, quarter: str | None = None) -> dict:
    """My quarter: hours by project, every note I wrote, and my summary so far."""
    return review_service.get(user.id, quarter)


@me.put("/review")
def save_my_review(payload: ReviewIn, user: CurrentUserDep) -> dict:
    try:
        review_service.save(user.id, payload.quarter, payload.summary, payload.submit)
    except ValueError as exc:
        raise ProblemDetail(409, str(exc)) from exc
    return review_service.get(user.id, payload.quarter)


def _feed_url(key: str) -> str:
    base = settings.MCP_PUBLIC_URL.rsplit("/mcp", 1)[0] if settings.MCP_PUBLIC_URL else ""
    return f"{base}/api/v1/feed/{key}.ics" if base else f"/api/v1/feed/{key}.ics"


@me.get("/feed")
def my_feed(user: SessionDep) -> dict:
    """The private calendar feed address. Session-only: a token must not be
    able to mint a second long-lived credential."""
    return {"url": _feed_url(feed_service.key_for(user.profile))}


@me.post("/feed/rotate")
def rotate_my_feed(user: SessionDep) -> dict:
    key = feed_service.rotate(user.id)
    audit.record(
        action="feed.rotated", target_table="profiles", target_id=user.id, actor_id=user.id
    )
    return {"url": _feed_url(key)}


feed = APIRouter(prefix="/feed", tags=["feed"])


@feed.get("/{key}.ics", include_in_schema=False)
def calendar_feed(key: str) -> Response:
    """Unauthenticated by design: calendar apps cannot sign in. The key is
    the credential — per person, unguessable, rotatable (FR-FEED-02)."""
    profile = feed_service.resolve(key)
    if profile is None:
        raise ProblemDetail(404, "No such feed.")
    return PlainTextResponse(
        feed_service.ics_for(profile), media_type="text/calendar; charset=utf-8"
    )


# ---------------------------------------------------------------------------
# OAuth consent — FR-OAUTH-02. Session-only, like token management.
# ---------------------------------------------------------------------------

oauth = APIRouter(prefix="/oauth", tags=["oauth"])


@oauth.get("/transaction/{txn}", include_in_schema=False)
def oauth_transaction(txn: str, user: SessionDep) -> dict:
    from app.services.oauth import provider

    row = provider.transaction(txn)
    if row is None:
        raise ProblemDetail(404, "That connection request has expired. Start again from Claude.")
    return {"client_name": row["params"].get("client_name"), "scopes": row["params"].get("scopes")}


@oauth.post("/approve", include_in_schema=False)
def oauth_approve(payload: OAuthApprove, user: SessionDep) -> dict:
    from mcp.server.auth.provider import AuthorizeError

    from app.services.oauth import provider

    try:
        redirect = provider.approve(txn=payload.txn, user_id=user.id, approved=payload.approved)
    except AuthorizeError as exc:
        raise ProblemDetail(400, exc.error_description or exc.error) from exc
    return {"redirect": redirect}


# ---------------------------------------------------------------------------
# Analytics — statements, utilisation, bench, hiring, health
# ---------------------------------------------------------------------------

analytics = APIRouter(prefix="/analytics", tags=["analytics"])


@analytics.get("/projects/{project_id}/statement")
def effort_statement(
    project_id: str,
    user: ManagerDep,
    period: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    format: str = "json",
):  # noqa: A002
    """Hours by person by day for a client — never money (FR-STMT)."""
    data = statements.statement(project_id, period)
    if data is None:
        raise ProblemDetail(404, "No such project.")
    if format == "csv":
        return PlainTextResponse(
            statements.as_csv(data),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{data["project"]["name"]}-{period}.csv"'
            },
        )
    return data


@analytics.get("/utilisation")
def utilisation_by_month(user: LeadDep, start: str | None = None, end: str | None = None) -> dict:
    """Billable, internal and activity hours against capacity, per person per
    month (FR-UTIL). Leads see their reports; managers everyone."""
    from app.api.timesheet import _population

    today = today_in_company_tz()
    this = today.replace(day=1)
    s = start or f"{(this - timedelta(days=150)).year}-{(this - timedelta(days=150)).month:02d}"
    e = end or f"{this.year}-{this.month:02d}"
    return utilisation.monthly(_population(user), s, e)


@analytics.get("/bench")
def bench(user: ManagerDep, weeks: int = Query(default=8, ge=1, le=26)) -> dict:
    return utilisation.bench(weeks)


@analytics.get("/hiring")
def hiring_signal(
    user: ManagerDep,
    months: int = Query(default=6, ge=1, le=24),
    annual_ctc: Decimal = Query(default=Decimal("1200000"), ge=0),
    start: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> dict:
    """Demand against supply, FTE needed and their cost at a given CTC (FR-HIRE).
    `start` (YYYY-MM) frames the months from there instead of from today."""
    return utilisation.hiring(months, annual_ctc, start)


@analytics.get("/projects/{project_id}/health")
def project_health(project_id: str, user: ManagerDep) -> dict:
    result = health_service.project_health(project_id)
    if result is None:
        raise ProblemDetail(404, "No such project.")
    return result


@analytics.get("/health")
def all_health(user: ManagerDep) -> list[dict]:
    return health_service.all_projects_health()


# ---------------------------------------------------------------------------
# Admin — locations, milestones, checklists, notifications
# ---------------------------------------------------------------------------

admin = APIRouter(prefix="/admin", tags=["admin"])


@admin.get("/locations")
def list_locations(admin_user: AdminDep) -> list[dict]:
    return [
        {"id": r["id"], "name": r["name"], "is_default": r["is_default"]}
        for r in db.list_locations()
    ]


@admin.post("/locations", status_code=201)
def add_location(payload: LocationIn, admin_user: AdminDep) -> dict:
    try:
        row = db.insert_location(payload.name.strip())
    except Exception as exc:  # noqa: BLE001
        if "locations_name_unique" in str(exc):
            raise ProblemDetail(409, "A location with that name exists.") from exc
        raise
    audit.record(
        action="location.added",
        target_table="locations",
        target_id=row["id"],
        actor_id=admin_user.id,
        after={"name": row["name"]},
    )
    return {"id": row["id"], "name": row["name"], "is_default": row["is_default"]}


def _present_milestone(row: dict) -> dict:
    return {
        k: (str(row[k]) if k == "amount" else row.get(k))
        for k in ("id", "project_id", "name", "due_on", "amount", "invoiced_on")
    }


@admin.get("/projects/{project_id}/milestones")
def list_milestones(project_id: str, manager: ManagerDep) -> dict:
    """Milestones with the reconciliation the monthly table relies on (FR-MILE-03)."""
    project = db.get_project(project_id)
    if project is None:
        raise ProblemDetail(404, "No such project.")
    rows = db.list_milestones(project_id)
    total = sum((Decimal(str(r["amount"])) for r in rows), Decimal("0"))
    revenue = Decimal(str(project["revenue"])) if project.get("revenue") is not None else None
    invoiced = sum((Decimal(str(r["amount"])) for r in rows if r.get("invoiced_on")), Decimal("0"))
    return {
        "milestones": [_present_milestone(r) for r in rows],
        "total": str(total),
        "invoiced": str(invoiced),
        "revenue": None if revenue is None else str(revenue),
        "gap": None if revenue is None else str(revenue - total),
    }


@admin.post("/projects/{project_id}/milestones", status_code=201)
def add_milestone(project_id: str, payload: MilestoneIn, manager: ManagerDep) -> dict:
    if db.get_project(project_id) is None:
        raise ProblemDetail(404, "No such project.")
    row = db.insert_milestone(
        {
            "project_id": project_id,
            "name": payload.name.strip(),
            "due_on": payload.due_on.isoformat(),
            "amount": str(payload.amount),
        }
    )
    audit.record(
        action="milestone.added",
        target_table="project_milestones",
        target_id=row["id"],
        actor_id=manager.id,
        after=_present_milestone(row),
    )
    return _present_milestone(row)


@admin.patch("/milestones/{milestone_id}")
def update_milestone(milestone_id: str, payload: MilestoneUpdate, manager: ManagerDep) -> dict:
    changes = {
        k: v for k, v in payload.model_dump(exclude_none=True).items() if k != "clear_invoiced"
    }
    if "due_on" in changes:
        changes["due_on"] = changes["due_on"].isoformat()
    if "invoiced_on" in changes:
        changes["invoiced_on"] = changes["invoiced_on"].isoformat()
    if "amount" in changes:
        changes["amount"] = str(changes["amount"])
    if payload.clear_invoiced:
        changes["invoiced_on"] = None
    row = db.update_milestone(milestone_id, changes)
    if row is None:
        raise ProblemDetail(404, "No such milestone.")
    audit.record(
        action="milestone.updated",
        target_table="project_milestones",
        target_id=milestone_id,
        actor_id=manager.id,
        after=changes,
    )
    return _present_milestone(row)


@admin.delete("/milestones/{milestone_id}")
def remove_milestone(milestone_id: str, manager: ManagerDep) -> dict:
    db.delete_milestone(milestone_id)
    audit.record(
        action="milestone.removed",
        target_table="project_milestones",
        target_id=milestone_id,
        actor_id=manager.id,
    )
    return {"status": "removed"}


@admin.get("/checklists")
def list_checklists(admin_user: AdminDep) -> dict:
    return {"templates": checklist_service.templates(), "checklists": checklist_service.listing()}


@admin.put("/checklists/templates/{kind}")
def set_checklist_template(kind: str, payload: ChecklistTemplateIn, admin_user: AdminDep) -> dict:
    if kind not in ("onboarding", "offboarding"):
        raise ProblemDetail(422, "kind must be onboarding or offboarding.")
    checklist_service.set_template(kind, payload.labels, admin_user.id)
    return {"kind": kind, "labels": checklist_service.templates()[kind]}


@admin.post("/checklists", status_code=201)
def start_checklist(payload: ChecklistStart, admin_user: AdminDep) -> dict:
    if db.get_profile(payload.user_id) is None:
        raise ProblemDetail(404, "No such person.")
    row = checklist_service.start(
        user_id=payload.user_id, kind=payload.kind, actor_id=admin_user.id
    )
    return {"id": row["id"], "user_id": row["user_id"], "kind": row["kind"]}


@admin.patch("/checklist-items/{item_id}")
def update_checklist_item(item_id: str, payload: ChecklistItemUpdate, admin_user: AdminDep) -> dict:
    row = checklist_service.tick(
        item_id=item_id,
        done=payload.done,
        actor_id=admin_user.id,
        owner_id=payload.owner_id,
        due_on=payload.due_on.isoformat() if payload.due_on else None,
    )
    if row is None:
        raise ProblemDetail(404, "No such item.")
    return {k: row.get(k) for k in ("id", "label", "owner_id", "due_on", "done_at", "done_by")}


@admin.post("/checklists/{checklist_id}/close")
def close_checklist(checklist_id: str, admin_user: AdminDep) -> dict:
    checklist_service.close(checklist_id, admin_user.id)
    return {"id": checklist_id, "status": "closed"}


@admin.post("/notifications/test")
def notification_test(admin_user: AdminDep) -> dict:
    """FR-NUDGE-04 — a message to yourself, to prove the Slack token works."""
    delivered = digest.test_message(admin_user.profile)
    return {"delivered": delivered, "slack_configured": settings.slack_enabled}


@admin.post("/nudges/run")
def run_nudge(payload: NudgeRun, admin_user: AdminDep) -> dict:
    """Run a scheduled message now (FR-DIGEST-02 and the nudges)."""
    runner = {
        "today": digest.nothing_logged_today,
        "weekly_gaps": digest.weekly_gaps,
        "over_allocation": digest.over_allocation_next_week,
        "morning_post": digest.morning_out_post,
        "digest": digest.weekly_digest,
    }[payload.kind]
    result = runner()
    audit.record(
        action=f"nudge.{payload.kind}.run",
        target_table="app_settings",
        target_id=None,
        actor_id=admin_user.id,
        after={k: v for k, v in result.items() if k != "body"},
    )
    return result


ROUTERS = [compoff, team, me, feed, oauth, analytics, admin]
