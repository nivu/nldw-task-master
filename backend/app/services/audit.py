"""Writing the audit log — FR-APPR-07, FR-ADMIN-06.

A database trigger already records every booking state transition
(005_audit_triggers.sql). This module records the things the database cannot
infer: administrative actions, and the *intent* behind a change. The trigger is
the safety net; this is the narrative.

What must never appear here: a booking `reason` or a `decision_note`. NFR-05
limits a reason to the person, their lead and an admin, while the audit log is
admin-readable and long-lived — copying a sick-leave reason into it would both
widen and outlive that access. The log records that something happened, not
somebody's medical situation.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services import supabase as db

logger = logging.getLogger("nldw-task-master")

#: Never written to the audit log, at any nesting level. See NFR-05 above.
REDACTED_FIELDS = frozenset({"reason", "decision_note", "password", "encrypted_password"})


def record(
    *,
    action: str,
    target_table: str,
    target_id: str | None,
    actor_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append one entry.

    A failure to write is logged and swallowed. That is a deliberate trade: the
    alternative is an admin's action being rolled back because the record of it
    could not be saved, which turns an observability problem into an outage.
    The database-level trigger still captures every booking transition
    independently, so the integrity-critical half of the record survives even
    if this call fails.
    """
    try:
        db.insert_audit(
            {
                "actor_id": actor_id,
                "actor_label": "system" if actor_id is None else "user",
                "action": action,
                "target_table": target_table,
                "target_id": target_id,
                "before": _redact(before),
                "after": _redact(after),
            }
        )
    except Exception:
        logger.exception(
            '{"event": "audit_write_failed", "action": "%s", "target": "%s"}',
            action,
            target_id,
        )


def _redact(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        key: ("[redacted]" if key in REDACTED_FIELDS and value is not None else value)
        for key, value in payload.items()
    }
