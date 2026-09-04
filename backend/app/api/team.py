"""The lead's view — FR-LEAD.

G-1: a lead can see, at a glance, who is available today and who is not. That
sentence is the whole point of the product for a lead, so this endpoint is
built to answer it in one request with no filtering (FR-LEAD-05: the default
view is today, reachable without navigation).

Q-06 is enforced here rather than by RLS. A lead may read their reports'
booking rows — that is a row-level permission — but the *reason* is personal
data (NFR-05) and is withheld from the roster. It appears only in the approval
queue below, which is the one place a lead needs it to decide.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, LeadDep
from app.api.errors import ProblemDetail
from app.domain.approval import Person, can_decide
from app.domain.calendar import is_weekend, period_of, today_in_company_tz
from app.domain.rules import CATEGORY_LABELS, OCCUPYING_STATES
from app.schemas import UnrecognisedFlag
from app.services import balances, settings_store
from app.services import bookings as booking_service
from app.services import supabase as db

router = APIRouter(prefix="/team", tags=["team"])


def _population(user) -> list[dict]:  # noqa: ANN001
    """Whose days this person may see.

    A lead sees their own reports (FR-LEAD-01); an admin sees the whole
    organisation (FR-ADMIN-05). Deactivated people are excluded — they are not
    absent, they have left.
    """
    if user.is_admin:
        return [p for p in db.list_profiles(active_only=True) if p["id"] != user.id]
    return db.list_reports(user.id, active_only=True)


@router.get("")
def team_day(
    user: LeadDep,
    day: date | None = Query(default=None, description="Defaults to today (FR-LEAD-05)"),
) -> dict:
    """Who is where, on one day — FR-LEAD-01/02/03.

    Everyone in the population appears exactly once, including those who are
    simply present. A roster that listed only absences would make "nobody is
    off today" indistinguishable from "the page failed to load".
    """
    today = today_in_company_tz()
    target = day or today
    people = _population(user)
    if not people:
        return _empty(target, today)

    ids = [person["id"] for person in people]
    bookings = {
        row["user_id"]: row
        for row in db.list_bookings(
            user_ids=ids,
            start=target,
            end=target,
            statuses=sorted(OCCUPYING_STATES),
        )
    }

    holiday = db.get_holiday_on(target)
    show_reason = settings_store.lead_view_shows_reason()

    entries = []
    for person in people:
        booking = bookings.get(person["id"])
        entry = {
            "user_id": person["id"],
            "display_name": person["display_name"],
            "state": "present" if booking is None else booking["status"],
            "category": None if booking is None else booking["category"],
            "category_label": None
            if booking is None or booking["category"] is None
            else CATEGORY_LABELS[booking["category"]],
            "duration": None if booking is None else str(booking["duration"]),
            "booking_id": None if booking is None else booking["id"],
        }
        # Q-06 — off by default. The setting exists so the decision is visible
        # in the product rather than buried in a document.
        if show_reason and booking is not None:
            entry["reason"] = booking.get("reason")
        entries.append(entry)

    entries.sort(key=lambda e: (e["state"] == "present", e["display_name"]))

    return {
        "date": target.isoformat(),
        "today": today.isoformat(),
        "is_weekend": is_weekend(target),
        "holiday": holiday["name"] if holiday else None,
        "people": entries,
        "summary": _summarise(entries),
    }


def _summarise(entries: list[dict]) -> dict:
    counts = {"present": 0, "wfh": 0, "casual": 0, "sick": 0, "unrecognised": 0}
    for entry in entries:
        if entry["state"] == "present":
            counts["present"] += 1
        elif entry["state"] == "unrecognised":
            counts["unrecognised"] += 1
        elif entry["category"]:
            counts[entry["category"]] += 1
    return counts


def _empty(target: date, today: date) -> dict:
    return {
        "date": target.isoformat(),
        "today": today.isoformat(),
        "is_weekend": is_weekend(target),
        "holiday": None,
        "people": [],
        "summary": {"present": 0, "wfh": 0, "casual": 0, "sick": 0, "unrecognised": 0},
    }


@router.get("/approvals")
def pending_approvals(user: LeadDep) -> list[dict]:
    """The queue a lead acts on — FR-APPR-02.

    Reasons ARE included here. Q-06 keeps them out of the roster, not out of
    the decision: a lead cannot reasonably approve a request whose reason they
    are not allowed to read.
    """
    people = {person["id"]: person for person in _population(user)}
    if not people:
        return []

    rows = db.list_bookings(user_ids=list(people), statuses=["pending"])

    out = []
    for row in rows:
        person = people[row["user_id"]]
        subject = Person(
            id=person["id"],
            role=person["role"],
            lead_id=person["lead_id"],
            is_active=person["is_active"],
        )
        if not can_decide(user, subject):
            continue
        out.append(
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "display_name": person["display_name"],
                "date": row["date"],
                "category": row["category"],
                "category_label": CATEGORY_LABELS.get(row["category"], row["category"]),
                "duration": str(row["duration"]),
                "reason": row.get("reason"),
                "created_at": row.get("created_at"),
            }
        )
    return sorted(out, key=lambda entry: entry["date"])


@router.get("/consumption")
def team_consumption(user: LeadDep, period: str = Query(default="")) -> dict:
    """Per-person consumption for the period — FR-LEAD-04."""
    resolved = period or period_of(today_in_company_tz())
    people = _population(user)
    return {
        "period": resolved,
        "people": [
            {
                "user_id": person["id"],
                "display_name": person["display_name"],
                "balances": balances.summary_for(person["id"], period=resolved),
            }
            for person in people
        ],
    }


@router.post("/unrecognised", status_code=201)
def flag_unrecognised(payload: UnrecognisedFlag, user: LeadDep) -> dict:
    """Record an absence nobody booked — FR-LEAD-03.

    Manual by necessity: V1 has no attendance data, so nothing can detect this
    automatically. Automatic detection waits for day-login (§10).
    """
    try:
        created = booking_service.flag_unrecognised(
            user_id=payload.user_id, day=payload.date, note=payload.note, actor=user
        )
    except booking_service.BookingRefused as exc:
        raise ProblemDetail(exc.status, exc.message) from exc

    return {"id": created["id"], "date": created["date"], "status": created["status"]}


@router.get("/reports")
def my_reports(user: CurrentUserDep) -> list[dict]:
    """The people this lead is responsible for — used to populate pickers."""
    if not user.is_lead:
        raise ProblemDetail(403, "Only a lead can see the team view.")
    return [
        {"id": person["id"], "display_name": person["display_name"]} for person in _population(user)
    ]
