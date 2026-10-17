"""Tasks' background work — spec 008 FR-TASK-DATA-11; TP-12; plan §5.8.

Acceptance criteria (spec §12, phase 1):
- "A deleted task with no workspace can be restored by its creator, and not
  by its assignee; the daily purge job removes it 90 days after deletion" —
  the purge half here (the restore half arrives with the task routes, S1b).
"""

from __future__ import annotations

from datetime import UTC, datetime

from celery.schedules import crontab
from crm_fake import OWNER, FakeCrmStore

from app.tasks import task_jobs
from app.worker import celery_app

NOW = datetime(2026, 10, 20, 19, 50, tzinfo=UTC)  # 01:20 IST on 21 October


class TestPurgeDeleted:
    def test_calls_task_purge_expired_with_now_minus_90_days_as_the_system(self, monkeypatch):
        calls = []

        def purge(actor, before):
            calls.append((actor, before))
            return 3

        monkeypatch.setattr(task_jobs, "_now", lambda: NOW)
        monkeypatch.setattr(task_jobs.store, "purge_expired", purge)
        assert task_jobs.purge_deleted() == 3
        assert calls == [
            (
                {"user_id": None, "kind": "system", "channel": None},
                "2026-07-22T19:50:00+00:00",
            )
        ]

    def test_only_tasks_with_no_workspace_deleted_before_then_go(self, monkeypatch):
        fake = FakeCrmStore().install(monkeypatch)
        labs = fake.add_workspace("labs", "Nunnari Labs", tva_linked=True)
        mine = {"created_by": OWNER["id"], "assignee_id": OWNER["id"]}
        old = fake.tasks.add_task(title="Old", deleted_at="2026-07-01T00:00:00+00:00", **mine)
        recent = fake.tasks.add_task(title="New", deleted_at="2026-10-01T00:00:00+00:00", **mine)
        live = fake.tasks.add_task(title="Live", **mine)
        crm = fake.tasks.add_task(
            title="CRM", workspace_id=labs["id"], deleted_at="2026-07-01T00:00:00+00:00", **mine
        )
        monkeypatch.setattr(task_jobs, "_now", lambda: NOW)
        assert task_jobs.purge_deleted() == 1
        assert set(fake.rows["tasks"]) == {recent["id"], live["id"], crm["id"]}
        [row] = [a for a in fake.audit if a["target_id"] == old["id"]]
        assert (row["action"], row["actor_label"], row["actor_id"]) == (
            "task.purged",
            "system",
            None,
        )


class TestTheSchedule:
    def test_daily_at_01_20(self):
        entry = celery_app.conf.beat_schedule["tasks-purge-deleted"]
        assert entry["task"] == "tasks.purge_deleted"
        assert entry["schedule"] == crontab(hour=1, minute=20)

    def test_the_task_is_registered(self):
        assert "tasks.purge_deleted" in celery_app.tasks
