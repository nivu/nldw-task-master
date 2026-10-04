"""Weekly timesheet sign-off — spec 006 FR-SIGN."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.domain import timesheets as rules
from app.domain.approval import Person, can_decide
from app.domain.calendar import today_in_company_tz
from app.services import audit, settings_store
from app.services import supabase as db
from app.services.timesheets import _grace_days


class SignoffRefused(Exception):
    def __init__(self, detail: str, status: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _subject(user_id: str) -> Person:
    profile = db.get_profile(user_id)
    if profile is None:
        raise SignoffRefused("No such person.", status=404)
    return Person(**{k: profile[k] for k in ("id", "role", "lead_id", "is_active")})


def confirm(
    *, user_id: str, week_start: date, actor: Person, note: str | None = None
) -> dict[str, Any]:
    if week_start.weekday() != 0:
        raise SignoffRefused("A week starts on a Monday.")
    if not can_decide(actor, _subject(user_id)):
        raise SignoffRefused(
            "Only this person's lead or an admin can confirm their week.", status=403
        )
    if week_start > monday_of(today_in_company_tz()):
        raise SignoffRefused("That week has not happened yet.")
    row = db.upsert_confirmation(
        {
            "user_id": user_id,
            "week_start": week_start.isoformat(),
            "status": "confirmed",
            "confirmed_by": actor.id,
            "confirmed_at": datetime.now(UTC).isoformat(),
            "note": (note or "").strip() or None,
        }
    )
    audit.record(
        action="timesheet.confirmed",
        target_table="timesheet_confirmations",
        target_id=row["id"],
        actor_id=actor.id,
        after={"user_id": user_id, "week_start": week_start.isoformat()},
    )
    return row


def reopen(*, user_id: str, week_start: date, actor: Person) -> None:
    """FR-SIGN-03 — only while the week is still editable."""
    if not can_decide(actor, _subject(user_id)):
        raise SignoffRefused(
            "Only this person's lead or an admin can reopen their week.", status=403
        )
    today = today_in_company_tz()
    if rules.week_closed(week_start, today, grace_days=_grace_days()):
        raise SignoffRefused(
            "That week's edit window has closed; it cannot be reopened.", status=409
        )
    was_confirmed = bool(status_for([user_id], week_start))
    db.delete_confirmation(user_id, week_start)
    audit.record(
        action="timesheet.reopened",
        target_table="timesheet_confirmations",
        target_id=None,
        actor_id=actor.id,
        after={"user_id": user_id, "week_start": week_start.isoformat()},
    )
    if was_confirmed:
        _notify_reopened(user_id, week_start, actor.id)


def _notify_reopened(user_id: str, week_start: date, actor_id: str) -> None:
    """FR-SIGN-03 — the person is told, off the request thread (FR-NOTIF-05)."""
    from app.services.dispatch import fire_and_forget
    from app.tasks import notifications

    fire_and_forget(
        notifications.week_reopened,
        user_id,
        week_start.isoformat(),
        actor_id,
        label="notify:week_reopened",
    )


def status_for(user_ids: list[str], week_start: date) -> dict[str, dict[str, Any]]:
    return {
        row["user_id"]: row
        for row in db.list_confirmations(user_ids=user_ids, week_start=week_start)
    }


def auto_confirm_closed(today: date | None = None) -> int:
    """FR-SIGN-02 — every week whose edit window has closed and nobody
    confirmed. Idempotent: confirmed rows are skipped."""
    today = today or today_in_company_tz()
    grace = _grace_days()
    portal_start = settings_store.portal_start_date()
    # FR-SIGN-05 — no sign-off is expected of anyone who does not log time.
    people = [p for p in db.list_profiles(active_only=True) if rules.logs_time(p)]
    # Weeks that closed in the last ~5 weeks; earlier ones were caught before.
    weeks = [monday_of(today) - timedelta(weeks=n) for n in range(1, 6)]
    done = 0
    for week in weeks:
        if not rules.week_closed(week, today, grace_days=grace):
            continue
        if not rules.week_expected(week, portal_start):
            continue
        existing = status_for([p["id"] for p in people], week)
        for person in people:
            if person["id"] in existing:
                continue
            db.upsert_confirmation(
                {
                    "user_id": person["id"],
                    "week_start": week.isoformat(),
                    "status": "auto",
                    "confirmed_by": None,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                    "note": "Confirmed automatically when the edit window closed (spec 006 FR-SIGN-02).",
                }
            )
            done += 1
    return done
