"""Handing work to Celery without ever blocking the caller.

FR-NOTIF-05 says a notification failure must not fail a booking. Wrapping
`.delay()` in try/except satisfies the letter of that and misses the point: when
the broker is unreachable, Celery spends about twenty seconds retrying the
connection *before* raising, so the booking succeeds and the person waited
twenty seconds for it.

Measured with Redis stopped: 19.3s inside `.delay()`. §1.1 puts the whole
product's bar at fifteen seconds for marking leave. A transient Redis blip would
make everything feel broken while every request still returned 201 — the worst
kind of outage, because the metrics look fine.

None of Celery's fail-fast settings move that number:

    defaults                            19.25s
    task_publish_retry=False            19.17s
    broker_connection_max_retries=0     19.14s
    broker_connection_timeout=1         19.21s
    transport socket timeouts           19.17s

So the enqueue is moved off the request thread entirely. The HTTP response no
longer waits on the broker being reachable, which is the property actually
wanted — a notification is not part of the transaction, and the booking is
already committed by the time this is called.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger("nldw-task-master")

#: Small and bounded. These threads exist only to absorb a broker that is slow
#: or gone; if more than a few are ever in flight, the broker is down and
#: queueing more work to discover that again helps nobody.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dispatch")


def fire_and_forget(
    task: Callable[..., Any], *args: Any, label: str = "task", **kwargs: Any
) -> None:
    """Enqueue a Celery task without waiting for the broker.

    Returns immediately. Any failure is logged with its impact spelled out,
    because "notification_enqueue_failed" on its own does not tell whoever
    reads the log whether anything a person cares about was lost.
    """

    def _run() -> None:
        try:
            task.delay(*args, **kwargs)
        except Exception:
            logger.exception(
                '{"event": "dispatch_failed", "task": "%s", '
                '"impact": "the record stands; no notification was sent"}',
                label,
            )

    try:
        _POOL.submit(_run)
    except RuntimeError:
        # The pool is shutting down — the process is going away, so there is
        # nothing useful left to do and certainly nothing worth failing for.
        logger.warning(
            '{"event": "dispatch_skipped", "task": "%s", "reason": "shutting down"}', label
        )
