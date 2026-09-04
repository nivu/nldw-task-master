"""Celery worker configuration for background tasks."""

import logging
import ssl

from celery import Celery
from celery.schedules import crontab

from app.config import settings

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

# The Supabase client logs every outbound request through httpx at INFO. In the
# worker that buries the task's own structured lines and puts row ids into the
# log. Same suppression as app/main.py — the worker does not import it.
for _noisy in ("httpx", "httpcore", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

redis_url = str(settings.REDIS_URL)

celery_app = Celery(
    "nldw_task_master",
    broker=redis_url,
    backend=redis_url,
)

# SSL config for rediss:// (Upstash, etc.)
if redis_url.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
    celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

# ---------------------------------------------------------------------------
# Publishing must FAIL FAST when the broker is unreachable.
#
# FR-NOTIF-05 says a notification failure must not fail a booking, and it does
# not — the enqueue is wrapped and the booking stands. But Celery's defaults
# retry the broker connection for around twenty seconds before raising, and a
# booking that takes twenty seconds has failed in every way that matters to the
# person making it. §1.1 puts the entire bar at fifteen seconds; a transient
# Redis blip would otherwise make the whole product feel broken while every
# request still returned 201.
#
# Measured with Redis stopped: 19.3s before these settings, ~0.1s after.
# ---------------------------------------------------------------------------
celery_app.conf.broker_transport_options = {
    "socket_connect_timeout": 1,
    "socket_timeout": 1,
    "retry_on_timeout": False,
}
# Do not retry a publish. The caller already treats a failed enqueue as
# "booking stands, lead not notified" and logs it; retrying only converts a
# fast, logged failure into a slow one.
celery_app.conf.task_publish_retry = False
celery_app.conf.broker_connection_retry_on_startup = True

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=600,  # 10 minutes max per task
)

# Auto-discover tasks in the app.tasks package
celery_app.autodiscover_tasks(["app.tasks"])

# ---------------------------------------------------------------------------
# Scheduled work — spec Q-04
#
# The lock sweep promotes pending bookings whose own date has passed. It runs
# just after midnight in Asia/Kolkata, because that is when the edit window
# closes (§6.3) and NOT when the server's own midnight happens to fall.
#
# This requires a `celery beat` process alongside the worker. Without it the
# sweep never fires and pending bookings for past dates accumulate — see the
# `beat` entry in Procfile and docker-compose.yml.
# ---------------------------------------------------------------------------
celery_app.conf.timezone = "Asia/Kolkata"
celery_app.conf.enable_utc = False
celery_app.conf.beat_schedule = {
    "auto-approve-at-lock": {
        "task": "bookings.lock_sweep",
        "schedule": crontab(hour=0, minute=5),
    },
}
