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
# Spec 007 Gmail sync — FR-CRM-SYNC-04, Q-74, plan §2.2
#
# Its page tasks go to their own queue, served only by the `worker-crm-sync`
# service (`celery -A app.worker worker -Q crm_sync --concurrency=1`), the one
# process that holds the Gmail key. The shared worker consumes the default
# queue only, so a long backfill never takes the slots leave notifications
# need. Everything else stays on the default queue.
# ---------------------------------------------------------------------------
CRM_SYNC_QUEUE = "crm_sync"
celery_app.conf.task_routes = {"crm.email_sync_page": {"queue": CRM_SYNC_QUEUE}}

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
    # Spec 006. Hourly on weekdays; the task fires only at `nudge_hour`.
    "nudge-nothing-logged": {
        "task": "ops.nudge_today",
        "schedule": crontab(minute=0, day_of_week="mon-fri"),
    },
    "nudge-weekly-gaps": {
        "task": "ops.nudge_weekly_gaps",
        "schedule": crontab(hour=17, minute=0, day_of_week="fri"),
    },
    "nudge-over-allocation": {
        "task": "ops.nudge_over_allocation",
        "schedule": crontab(hour=9, minute=0, day_of_week="mon"),
    },
    "morning-out-post": {
        "task": "ops.morning_post",
        "schedule": crontab(hour=9, minute=0, day_of_week="mon-fri"),
    },
    "weekly-digest": {
        "task": "ops.weekly_digest",
        "schedule": crontab(hour=9, minute=5, day_of_week="mon"),
    },
    "auto-confirm-weeks": {
        "task": "ops.auto_confirm_weeks",
        "schedule": crontab(hour=0, minute=15),
    },
    "lapse-compoff": {"task": "ops.lapse_compoff", "schedule": crontab(hour=0, minute=20)},
    # Spec 007 FR-CRM-DEL-01, plan §5.11: Trash older than 90 days, per workspace.
    "crm-purge-trash": {"task": "crm.purge_trash", "schedule": crontab(hour=1, minute=10)},
    # Spec 007 FR-CRM-FLAG-02, plan §5.11: every workspace's follow-up flags,
    # then the stale-deal notices they raise (automations 4 and 5).
    "crm-recompute-flags": {"task": "crm.recompute_flags", "schedule": crontab(minute="*/30")},
    # FR-CRM-NOTIF-03, -04, automation 7: digests go to whoever is at 09:00
    # their own time, so the tick runs every quarter hour; task reminders too.
    "crm-digest-tick": {"task": "crm.digest_tick", "schedule": crontab(minute="*/15")},
    # FR-CRM-SYNC-04: labelled Gmail threads within 15 minutes. The tick runs
    # on the default queue and only enqueues pages onto CRM_SYNC_QUEUE; while
    # sync is off or unconfigured it records a skip and does nothing else.
    "crm-email-sync-tick": {"task": "crm.email_sync_tick", "schedule": crontab(minute="*/15")},
}

# ---------------------------------------------------------------------------
# Spec 008 — tasks
#
# 008's own block, after 007's and before 009's (plan §5.8, §9 rule 3).
# ---------------------------------------------------------------------------
celery_app.conf.beat_schedule.update(
    {
        # FR-TASK-DATA-11, TP-12: tasks with no workspace deleted > 90 days ago.
        "tasks-purge-deleted": {
            "task": "tasks.purge_deleted",
            "schedule": crontab(hour=1, minute=20),
        },
        # FR-TASK-VOICE-02, -05: voice audio and transcripts before 24 hours.
        "tasks-voice-sweep": {
            "task": "tasks.voice_sweep",
            "schedule": crontab(minute="*/30"),
        },
    }
)


# ---------------------------------------------------------------------------
# Spec 009 — the CRM intelligence layer, FR-CRMAI-JOB-04, Q-05, plan §5.1
#
# Every AI task (`crm_ai.*`) goes to its own queue, served only by the
# `worker-crm-ai` service (`celery -A app.worker worker -Q crm-ai
# --concurrency=1`), the one process that holds the Claude and Fireflies
# keys. One task at a time, so budget overshoot is one call (FR-CRMAI-LLM-07)
# and the Gmail sync lock is never held while a model call runs. The shared
# worker runs no AI task. Beat entries are added with the jobs themselves.
# ---------------------------------------------------------------------------
CRM_AI_QUEUE = "crm-ai"
celery_app.conf.task_routes = {
    **celery_app.conf.task_routes,
    "crm_ai.*": {"queue": CRM_AI_QUEUE},
}
celery_app.conf.beat_schedule.update(
    {
        # FR-CRMAI-REV-07: Review Queue items expire at 14 days and are
        # tombstoned at 90, per workspace, daily.
        "crm-ai-review-expiry": {
            "task": "crm_ai.review_expiry",
            "schedule": crontab(hour=1, minute=30),
        },
        # FR-CRMAI-SUM-02, NFR-CRMAI-05: labelled threads summarised within
        # one sync cycle of a message being stored.
        "crm-ai-summaries": {
            "task": "crm_ai.summaries",
            "schedule": crontab(minute="*/15"),
        },
    }
)
