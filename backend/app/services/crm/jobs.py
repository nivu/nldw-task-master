"""The job log — spec 007 FR-CRM-JOB-01 to -03, FR-CRM-AUTO-02; Q-69, Q-72;
data-model §3.18; plan §5.11.

Every CRM background run is recorded in `crm_job_runs`: which job, which
workspace, when it started and ended, its outcome, its counts and, when it
failed, its error. `@crm_job(name)` wraps the work of one workspace:

- The wrapped function runs once per workspace (one row each), except the
  digest tick, which spans workspaces and writes one row with none.
- A workspace that fails is recorded `failed` and the others still run. One
  that raises `Skipped` (its automation is switched off, FR-CRM-AUTO-02) is
  recorded `skipped`, so a switched-off job is still seen to be running.
- `error` may name records and is shown to the workspace's members only;
  `error_public` names no record, address or subject (just the job and the
  kind of failure) and is all anyone else sees (FR-CRM-JOB-01, Q-69).
- Three consecutive failures of one job in one workspace alert, once per
  streak, the workspace's Admins in the bell and by message, and TVA admins
  by message (FR-CRM-JOB-02).

A stopped scheduler cannot alert about itself, so staleness is checked on
read (FR-CRM-JOB-03): `stale()` names every job whose last run started more
than twice its interval ago. It feeds the CRM's start-up call and Today for
Admins, and `/health/jobs` for an external uptime monitor, which carries no
record content. A job that has never run is not reported: before its first
scheduled run there is nothing it is late for.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.services import supabase as db
from app.services.crm import notifications, store

logger = logging.getLogger("nldw-task-master")


@dataclass(frozen=True)
class Job:
    label: str
    #: How often beat runs it (plan §5.11); stale after twice this.
    every: timedelta


#: Data-model §3.18 `job`. Email sync arrives with its own schedule in S10.
JOBS: Final = {
    "flags": Job("Follow-up flags", timedelta(minutes=30)),
    "automations": Job("Stale-deal notices", timedelta(minutes=30)),
    "digests": Job("Digests", timedelta(minutes=15)),
    "reminders": Job("Task reminders", timedelta(minutes=15)),
    "trash_purge": Job("Trash purge", timedelta(days=1)),
}
#: FR-CRM-JOB-02.
STREAK: Final = 3


class Skipped(Exception):
    """The run had nothing to do on purpose: recorded `skipped`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def public_error(job: str, exc: BaseException) -> str:
    """What a failure says to someone who may not see the workspace's
    records: the job and the kind of error, never its message."""
    return f"{JOBS[job].label} failed ({type(exc).__name__})."[:500]


def run(
    job: str,
    workspace: dict[str, Any] | None,
    work: Callable[[dict[str, Any] | None, datetime], dict[str, Any] | None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Runs `work` for one workspace (or none, for the digest tick) and
    records it. Never raises for the work's own failure."""
    now = now or _now()
    ws_id = workspace["id"] if workspace else None
    started = store.start_job_run(job, ws_id, now)
    values: dict[str, Any]
    try:
        counts = work(workspace, now) or {}
        values = {"outcome": "ok", "counts": counts}
    except Skipped as skipped:
        values = {"outcome": "skipped", "counts": {"skipped": skipped.reason}}
    except Exception as exc:
        logger.exception(
            '{"event": "crm_job_failed", "job": "%s", "workspace": "%s"}',
            job,
            (workspace or {}).get("key", "-"),
        )
        values = {
            "outcome": "failed",
            "counts": {},
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "error_public": public_error(job, exc),
        }
    values["finished_at"] = max(_now(), now).isoformat()
    store.finish_job_run(started["id"], values)
    logger.info(
        '{"event": "crm_job_done", "job": "%s", "workspace": "%s", "outcome": "%s"}',
        job,
        (workspace or {}).get("key", "-"),
        values["outcome"],
    )
    if values["outcome"] == "failed":
        try:
            _alert_if_streak(job, workspace, started["id"], values["error_public"])
        except Exception:
            logger.exception('{"event": "crm_job_alert_failed", "job": "%s"}', job)
    return {"outcome": values["outcome"], "counts": values["counts"]}


def crm_job(job: str, *, per_workspace: bool = True):
    """Wraps `fn(workspace, now) -> counts` as a recorded job. The wrapped
    callable takes `now` and returns `{runs: {workspace key: {outcome,
    counts}}, failed: [workspace key]}`."""
    if job not in JOBS:
        raise ValueError(f"unknown CRM job: {job}")

    def wrap(fn: Callable[[Any, datetime], dict[str, Any] | None]):
        @functools.wraps(fn)
        def wrapped(now: datetime | None = None) -> dict[str, Any]:
            now = now or _now()
            targets = store.list_workspaces() if per_workspace else [None]
            runs: dict[str, Any] = {}
            for workspace in targets:
                runs[workspace["key"] if workspace else "all"] = run(job, workspace, fn, now)
            return {
                "runs": runs,
                "failed": [k for k, r in runs.items() if r["outcome"] == "failed"],
            }

        wrapped.job = job  # type: ignore[attr-defined]
        return wrapped

    return wrap


# ---------------------------------------------------------------------------
# Three failures — FR-CRM-JOB-02
# ---------------------------------------------------------------------------


def _alert_if_streak(
    job: str, workspace: dict[str, Any] | None, run_id: str, error_public: str
) -> None:
    runs = store.job_streak(job, workspace["id"] if workspace else None)
    streak = []
    for r in runs:
        if r["outcome"] != "failed":
            break
        streak.append(r)
    if len(streak) < STREAK or any(r.get("alerted") for r in streak):
        return
    store.mark_job_alerted(run_id)
    where = f" in {workspace['name']}" if workspace else ""
    title = f"{JOBS[job].label}{where} has failed {len(streak)} times in a row"
    body = f"{title}. The last error: {error_public} See CRM Settings, Jobs."
    profiles = {p["id"]: p for p in db.list_profiles(include_crm_only=True)}
    told: set[str] = set()
    if workspace:
        admins = [m["user_id"] for m in store.list_members(workspace["id"]) if m["role"] == "admin"]
        rows = [
            {
                "workspace_id": workspace["id"],
                "user_id": user_id,
                "kind": "job_failed",
                "title": title,
                "dedupe_key": f"job_failed:{job}:{run_id}",
                "body": body,
            }
            for user_id in admins
        ]
        notifications.send(rows, out_of_app=True, profiles=profiles)
        told |= set(admins)
    # TVA admins administer every workspace's accounts and see its run
    # metadata (Q-69); those who are not its admins get the message only.
    for person in profiles.values():
        if (
            person["id"] not in told
            and person.get("role") == "admin"
            and person.get("account_type", "staff") == "staff"
            and person.get("is_active", True)
        ):
            notifications.message(person, title, body)
    logger.warning('{"event": "crm_job_alert", "job": "%s", "streak": %d}', job, len(streak))


# ---------------------------------------------------------------------------
# Staleness — FR-CRM-JOB-03
# ---------------------------------------------------------------------------


def _at(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def stale(now: datetime | None = None, *, workspace_id: str | None = None) -> list[dict]:
    """Jobs whose last run started more than twice their interval ago. With
    `workspace_id`, that workspace's own jobs only, never the digest tick's
    row, which has none (FR-CRM-JOB-01)."""
    now = now or _now()
    out = []
    for row in store.latest_job_runs():
        spec = JOBS.get(row["job"])
        if spec is None:
            continue
        if workspace_id is not None and row.get("workspace_id") != workspace_id:
            continue
        late = now - _at(row["started_at"])
        if late > 2 * spec.every:
            out.append(
                {
                    "job": row["job"],
                    "label": spec.label,
                    "workspace_id": row.get("workspace_id"),
                    "last_started_at": row["started_at"],
                    "overdue_minutes": int((late - spec.every).total_seconds() // 60),
                }
            )
    return out


def stale_for(ctx: Any) -> list[dict[str, Any]]:
    """The banner an Admin sees: this workspace's late jobs. Others get none."""
    if not ctx.may("administer"):
        return []
    return [
        {k: v for k, v in s.items() if k != "workspace_id"}
        for s in stale(workspace_id=ctx.workspace_id)
    ]


def health(now: datetime | None = None) -> dict[str, Any]:
    """`/health/jobs` — every job's last run and whether it is late, by
    workspace key, with no counts and no error (FR-CRM-JOB-03)."""
    now = now or _now()
    keys = {w["id"]: w["key"] for w in store.list_workspaces()}
    late = {(s["job"], s["workspace_id"]) for s in stale(now)}
    jobs = [
        {
            "job": row["job"],
            "workspace": keys.get(row.get("workspace_id") or "", None),
            "last_started_at": row["started_at"],
            "last_outcome": row["outcome"],
            "stale": (row["job"], row.get("workspace_id")) in late,
        }
        for row in store.latest_job_runs()
        if row["job"] in JOBS
    ]
    return {"status": "stale" if late else "ok", "jobs": jobs}


# ---------------------------------------------------------------------------
# Listing — contracts §2.4, §2.10
# ---------------------------------------------------------------------------


def present_run(
    row: dict[str, Any], *, with_error: bool, keys: dict[str, str] | None = None
) -> dict[str, Any]:
    """A run as a list shows it. `error` only `with_error` (a member of its
    workspace); `error_public` always."""
    out = {
        "id": row["id"],
        "job": row["job"],
        "label": JOBS[row["job"]].label if row["job"] in JOBS else row["job"],
        "started_at": row["started_at"],
        "finished_at": row.get("finished_at"),
        "outcome": row["outcome"],
        "counts": row.get("counts") or {},
        "error_public": row.get("error_public"),
        "alerted": bool(row.get("alerted")),
    }
    if keys is not None:
        out["workspace"] = keys.get(row.get("workspace_id") or "")
    if with_error:
        out["error"] = row.get("error")
    return out
