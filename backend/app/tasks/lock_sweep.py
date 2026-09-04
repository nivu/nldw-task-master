"""The nightly lock sweep — spec Q-04.

"What happens to a pending booking when its own date arrives un-actioned?"

The shipped answer is auto-approval, consistent with the stated default that
requests are approved unless there is a conversation. The alternative — leaving
them pending forever — creates records that are neither absence nor attendance,
which is precisely the state §1 says the current email workflow produces and
this product exists to end.

Runs at 00:05 Asia/Kolkata, just after the day closes. Scheduled in
`app.worker`; requires `celery beat` to be running alongside the worker.

Idempotent: a second run finds nothing pending before today and does nothing,
so a missed night is repaired by the next one and a double-fire is harmless.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.domain.calendar import today_in_company_tz
from app.services import audit, settings_store
from app.services import supabase as db
from app.worker import celery_app

logger = logging.getLogger("nldw-task-master")


@celery_app.task(name="bookings.lock_sweep")
def sweep() -> dict:
    """Promote every pending booking whose date has passed."""
    if not settings_store.auto_approve_at_lock():
        logger.info('{"event": "lock_sweep_skipped", "reason": "auto_approve_at_lock is false"}')
        return {"approved": 0, "skipped": True}

    today = today_in_company_tz()
    stale = db.list_pending_before(today)

    approved = 0
    for booking in stale:
        try:
            db.update_booking(
                booking["id"],
                {
                    "status": "approved",
                    # decided_by stays NULL on purpose. The audit trigger reads
                    # it to decide actor_label, so a NULL here is what marks
                    # the row as 'system' rather than attributing the decision
                    # to a lead who never made it.
                    "decided_at": datetime.now(tz=UTC).isoformat(),
                    "decision_note": "Auto-approved at lock — no decision was recorded "
                    "before the day passed (spec Q-04).",
                },
            )
            audit.record(
                action="booking.auto_approved",
                target_table="bookings",
                target_id=booking["id"],
                actor_id=None,
                before={"status": "pending"},
                after={"status": "approved", "rule": "Q-04 auto-approve at lock"},
            )
            approved += 1
        except Exception:
            # One bad row must not abandon the rest of the night's sweep.
            logger.exception(
                '{"event": "lock_sweep_row_failed", "booking_id": "%s"}', booking["id"]
            )

    logger.info(
        '{"event": "lock_sweep_complete", "date": "%s", "considered": %d, "approved": %d}',
        today.isoformat(),
        len(stale),
        approved,
    )
    return {"approved": approved, "considered": len(stale), "date": today.isoformat()}
