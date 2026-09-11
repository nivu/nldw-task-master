"""Scheduled org operations — spec 006.

Nudges, the morning post, the Monday digest, weekly auto-confirmation and
comp-off lapsing. Each is idempotent and safe to run by hand from the admin
panel; Celery beat (app.worker) runs them on the clock in Asia/Kolkata.
"""

from __future__ import annotations

import logging

from app.domain.calendar import today_in_company_tz
from app.services import compoff, confirmations, digest, settings_store
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")


@celery_app.task(name="ops.nudge_today")
def nudge_today() -> dict:
    """Runs every hour on weekdays; fires only at the configured hour so an
    admin can move it without a deploy (FR-NUDGE-01)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.hour != int(settings_store.get("nudge_hour", 18)):
        return {"sent": 0, "skipped": f"not the nudge hour ({now.hour})"}
    result = digest.nothing_logged_today(today_in_company_tz())
    logger.info('{"event": "nudge_today", "sent": %s}', result.get("sent", 0))
    return result


@celery_app.task(name="ops.nudge_weekly_gaps")
def nudge_weekly_gaps() -> dict:
    return digest.weekly_gaps()


@celery_app.task(name="ops.nudge_over_allocation")
def nudge_over_allocation() -> dict:
    return digest.over_allocation_next_week()


@celery_app.task(name="ops.morning_post")
def morning_post() -> dict:
    return digest.morning_out_post()


@celery_app.task(name="ops.weekly_digest")
def weekly_digest() -> dict:
    result = digest.weekly_digest()
    return {k: v for k, v in result.items() if k != "body"}


@celery_app.task(name="ops.auto_confirm_weeks")
def auto_confirm_weeks() -> dict:
    return {"confirmed": confirmations.auto_confirm_closed()}


@celery_app.task(name="ops.lapse_compoff")
def lapse_compoff() -> dict:
    return {"lapsed": compoff.lapse_expired()}
