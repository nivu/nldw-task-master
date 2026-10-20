"""The job log — spec 007 FR-CRM-JOB-01 to -03; Q-69, Q-72; contracts §2.4
`/crm/tva-admin/jobs`, §2.10 `/crm/jobs`; plan §5.11.

Every run is recorded, per workspace (the digest tick once, with none); three
failures in a row alert once per streak; a job more than twice its interval
late shows to Admins, and on `/health/jobs`, which carries no record content;
a run's `error` is for its workspace's members, everyone else gets
`error_public`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import DEFAULTS, MEMBER, OUTSIDER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.services.crm import flags, jobs, notifications
from app.tasks import crm as tasks

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
LABS = "?workspace=labs"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    http = crm_client(monkeypatch, fake=fake)
    sent: list = []
    monkeypatch.setattr(notifications, "deliver", lambda message: sent.append(message) or ["email"])
    monkeypatch.setattr(jobs, "_now", lambda: NOW)
    return http, fake, labs, academy, sent


def _runs(fake, job=None):
    return [r for r in fake.rows["crm_job_runs"].values() if job is None or r["job"] == job]


def _break_flags(monkeypatch):
    def boom(workspace_id, now=None):
        raise RuntimeError("Acme Corp <priya@acme.com> broke it")

    monkeypatch.setattr(flags, "recompute_workspace", boom)


class TestEveryRunIsRecorded:
    def test_flags_and_automations_one_row_per_workspace(self, crm):
        _, fake, labs, academy, _ = crm
        result = tasks.recompute_flags()
        assert set(result["flags"]["runs"]) == {"labs", "academy"}
        for job in ("flags", "automations"):
            rows = _runs(fake, job)
            assert {r["workspace_id"] for r in rows} == {labs["id"], academy["id"]}
            assert {r["outcome"] for r in rows} == {"ok"}
            assert all(r["finished_at"] for r in rows)

    def test_trash_purge_one_row_per_workspace(self, crm):
        _, fake, labs, academy, _ = crm
        tasks.purge_trash()
        rows = _runs(fake, "trash_purge")
        assert sorted(r["workspace_id"] for r in rows) == sorted([labs["id"], academy["id"]])
        # Spec 010 FR-CAP-DIS-11: the purge also counts capture decisions.
        assert all(r["counts"] == {"purged": 0, "capture_decisions": 0} for r in rows)

    def test_trash_purge_deletes_capture_decisions_after_a_year(self, crm):
        # Spec 010 FR-CAP-DIS-11 (S3-07): the nightly task passes now - 365
        # days; a pending candidate is never purged, however old.
        _, fake, labs, _, _ = crm
        now = datetime.now(UTC)
        rows = {}
        for name, status, days in (
            ("old", "judged", 366),
            ("recent", "judged", 300),
            ("pending", "pending", 400),
        ):
            rows[name] = fake.add(
                "crm_capture_candidates",
                **{
                    **DEFAULTS["crm_capture_candidates"],
                    "workspace_id": labs["id"],
                    "mailbox_id": "mb-1",
                    "thread_id": f"t-{name}",
                    "latest_message_id": f"m-{name}",
                    "latest_message_at": (now - timedelta(days=days)).isoformat(),
                    "status": status,
                    "updated_at": (now - timedelta(days=days)).isoformat(),
                },
            )
        tasks.purge_trash()
        [run] = [r for r in _runs(fake, "trash_purge") if r["workspace_id"] == labs["id"]]
        assert run["counts"]["capture_decisions"] == 1
        assert set(fake.rows["crm_capture_candidates"]) == {
            rows["recent"]["id"],
            rows["pending"]["id"],
        }

    def test_the_digest_tick_has_no_workspace_and_reminders_do(self, crm):
        _, fake, labs, academy, _ = crm
        tasks.digest_tick()
        assert [r["workspace_id"] for r in _runs(fake, "digests")] == [None]
        assert {r["workspace_id"] for r in _runs(fake, "reminders")} == {labs["id"], academy["id"]}

    def test_a_failure_is_recorded_and_the_others_still_run(self, crm, monkeypatch):
        _, fake, labs, _, _ = crm
        real = flags.recompute_workspace

        def one_fails(workspace_id, now=None):
            if workspace_id == labs["id"]:
                raise RuntimeError("Acme Corp broke it")
            return real(workspace_id, now)

        monkeypatch.setattr(flags, "recompute_workspace", one_fails)
        result = tasks.recompute_flags()
        assert result["flags"]["failed"] == ["labs"]
        failed = next(r for r in _runs(fake, "flags") if r["outcome"] == "failed")
        assert "Acme Corp" in failed["error"]
        assert failed["error_public"] == "Follow-up flags failed (RuntimeError)."
        assert {r["outcome"] for r in _runs(fake, "flags")} == {"failed", "ok"}


class TestThreeFailures:
    """FR-CRM-JOB-02 — once per streak, to the workspace admins (bell and
    message) and TVA admins (message)."""

    def test_the_third_alerts_once(self, crm, monkeypatch):
        _, fake, labs, _, sent = crm
        _break_flags(monkeypatch)
        for _ in range(2):
            tasks._flags(NOW)
        assert not [n for n in fake.rows["crm_notifications"].values()]
        tasks._flags(NOW)
        bell = [n for n in fake.rows["crm_notifications"].values() if n["kind"] == "job_failed"]
        # The owner admins both workspaces; each failed three times.
        academy = next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == "academy")
        assert sorted((n["workspace_id"], n["user_id"]) for n in bell) == sorted(
            [(labs["id"], OWNER["id"]), (academy["id"], OWNER["id"])]
        )
        assert all("Acme" not in n["title"] for n in bell)
        to = sorted({m.recipient_email for m in sent})
        assert to == sorted([OWNER["email"], OUTSIDER["email"]])
        assert all("Acme" not in m.body and "priya" not in m.body for m in sent)
        alerted = len(sent)

        for _ in range(3):
            tasks._flags(NOW)
        assert len(sent) == alerted
        assert len([r for r in _runs(fake, "flags") if r["alerted"]]) == 2

    def test_a_success_starts_a_new_streak(self, crm, monkeypatch):
        _, fake, labs, _, sent = crm
        real = flags.recompute_workspace
        _break_flags(monkeypatch)
        for _ in range(3):
            tasks._flags(NOW)
        first = len(sent)
        monkeypatch.setattr(flags, "recompute_workspace", real)
        tasks._flags(NOW)
        _break_flags(monkeypatch)
        for _ in range(2):
            tasks._flags(NOW)
        assert len(sent) == first
        tasks._flags(NOW)
        assert len(sent) == 2 * first

    def test_a_member_is_not_alerted(self, crm, monkeypatch):
        _, fake, labs, _, _ = crm
        _break_flags(monkeypatch)
        for _ in range(3):
            tasks._flags(NOW)
        assert not [
            n for n in fake.rows["crm_notifications"].values() if n["user_id"] == MEMBER["id"]
        ]


def _run(fake, job, workspace_id, ago, outcome="ok", error=None):
    return fake.add(
        "crm_job_runs",
        workspace_id=workspace_id,
        job=job,
        started_at=(NOW - ago).isoformat(),
        finished_at=(NOW - ago).isoformat(),
        outcome=outcome,
        counts={"changed": 1},
        error=error,
        error_public="Digests failed (RuntimeError)." if error else None,
        alerted=False,
    )


class TestStaleness:
    """FR-CRM-JOB-03 — checked on read; late is more than twice the interval."""

    def test_an_admin_sees_a_job_late_past_twice_its_interval(self, crm):
        http, fake, labs, _, _ = crm
        _run(fake, "flags", labs["id"], timedelta(minutes=59))
        boot = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert boot["stale_jobs"] == []
        _run(fake, "flags", labs["id"], timedelta(minutes=61))
        fake.rows["crm_job_runs"] = {
            k: r
            for k, r in fake.rows["crm_job_runs"].items()
            if r["started_at"] != (NOW - timedelta(minutes=59)).isoformat()
        }
        boot = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert [s["job"] for s in boot["stale_jobs"]] == ["flags"]
        assert boot["stale_jobs"][0]["label"] == "Follow-up flags"

    def test_a_member_sees_no_banner(self, crm):
        http, fake, labs, _, _ = crm
        _run(fake, "flags", labs["id"], timedelta(hours=5))
        boot = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(MEMBER["id"])).json()
        assert boot["stale_jobs"] == []

    def test_today_names_it_for_an_admin(self, crm, monkeypatch):
        http, fake, labs, _, _ = crm
        _run(fake, "trash_purge", labs["id"], timedelta(days=3))
        got = http.get(f"/api/v1/crm/today{LABS}", headers=as_(OWNER["id"])).json()
        assert [s["job"] for s in got["stale_jobs"]] == ["trash_purge"]

    def test_never_run_is_not_late(self, crm):
        http, *_ = crm
        boot = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert boot["stale_jobs"] == []


class TestHealthJobs:
    def test_no_record_content(self, crm):
        http, fake, labs, _, _ = crm
        _run(fake, "flags", labs["id"], timedelta(minutes=5), "failed", error="Acme Corp")
        r = http.get("/health/jobs")
        assert r.status_code == 200
        assert "Acme" not in r.text and "error" not in r.text and "counts" not in r.text
        assert r.json()["jobs"] == [
            {
                "job": "flags",
                "workspace_key": "labs",
                "last_ok_at": None,
                "overdue": False,
                "last_started_at": (NOW - timedelta(minutes=5)).isoformat(),
                "last_outcome": "failed",
            }
        ]

    def test_last_ok_at_is_the_last_success(self, crm):
        """Contracts §1: a job that keeps failing still shows when it last
        worked."""
        http, fake, labs, _, _ = crm
        _run(fake, "flags", labs["id"], timedelta(minutes=50))
        _run(fake, "flags", labs["id"], timedelta(minutes=20), "failed", error="x")
        _run(fake, "trash_purge", labs["id"], timedelta(hours=1), "skipped")
        got = {j["job"]: j for j in http.get("/health/jobs").json()["jobs"]}
        assert got["flags"]["last_ok_at"] == (NOW - timedelta(minutes=50)).isoformat()
        assert got["flags"]["last_outcome"] == "failed"
        assert got["trash_purge"]["last_ok_at"] == (NOW - timedelta(hours=1)).isoformat()

    def test_late_is_503(self, crm):
        http, fake, labs, _, _ = crm
        _run(fake, "digests", None, timedelta(hours=2))
        r = http.get("/health/jobs")
        assert r.status_code == 503
        assert r.json()["status"] == "stale"
        [job] = r.json()["jobs"]
        assert (job["workspace_key"], job["overdue"]) == (None, True)


class TestWhoSeesTheError:
    def test_a_workspace_admin_sees_its_own_with_error(self, crm):
        http, fake, labs, academy, _ = crm
        _run(fake, "flags", labs["id"], timedelta(minutes=5), "failed", error="Acme Corp")
        _run(fake, "flags", academy["id"], timedelta(minutes=5), "failed", error="College X")
        got = http.get(f"/api/v1/crm/jobs{LABS}", headers=as_(OWNER["id"])).json()
        assert [r["error"] for r in got["runs"]] == ["Acme Corp"]

    def test_never_a_row_with_no_workspace(self, crm):
        http, fake, labs, _, _ = crm
        _run(fake, "digests", None, timedelta(hours=3), "failed", error="priya@acme.com")
        got = http.get(f"/api/v1/crm/jobs{LABS}", headers=as_(OWNER["id"]))
        assert got.status_code == 200
        assert got.json()["runs"] == [] and got.json()["stale_jobs"] == []
        assert "priya" not in got.text
        boot = http.get(f"/api/v1/crm/bootstrap{LABS}", headers=as_(OWNER["id"])).json()
        assert boot["stale_jobs"] == []

    def test_a_member_cannot_list_jobs(self, crm):
        http, *_ = crm
        r = http.get(f"/api/v1/crm/jobs{LABS}", headers=as_(MEMBER["id"]))
        assert r.status_code == 403

    def test_a_tva_admin_who_is_not_a_member_sees_error_public_only(self, crm):
        http, fake, labs, _, _ = crm
        _run(fake, "flags", labs["id"], timedelta(minutes=5), "failed", error="Acme Corp")
        _run(fake, "digests", None, timedelta(minutes=5), "failed", error="priya@acme.com")
        r = http.get("/api/v1/crm/tva-admin/jobs", headers=as_(OUTSIDER["id"]))
        assert r.status_code == 200, r.text
        assert "Acme" not in r.text and "priya" not in r.text
        runs = r.json()["runs"]
        assert {run["workspace"] for run in runs} == {"labs", None}
        assert all("error" not in run and run["error_public"] for run in runs)

    def test_the_panel_is_tva_admins_only(self, crm):
        http, *_ = crm
        assert http.get("/api/v1/crm/tva-admin/jobs", headers=as_(MEMBER["id"])).status_code == 403


class TestSchedule:
    """Plan §5.11 — beat runs the flags every 30 minutes and the digest tick
    every 15."""

    def test_beat_entries(self):
        from app.worker import celery_app

        flags_entry = celery_app.conf.beat_schedule["crm-recompute-flags"]
        assert flags_entry["task"] == "crm.recompute_flags"
        assert flags_entry["schedule"].minute == {0, 30}
        tick = celery_app.conf.beat_schedule["crm-digest-tick"]
        assert tick["task"] == "crm.digest_tick"
        assert tick["schedule"].minute == {0, 15, 30, 45}

    def test_the_tasks_are_registered(self):
        from app.worker import celery_app

        assert {"crm.recompute_flags", "crm.digest_tick", "crm.purge_trash"} <= set(
            celery_app.tasks
        )
