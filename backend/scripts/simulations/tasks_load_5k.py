"""My Tasks at 5,000 tasks — spec 008 NFR-TASK-01 (TP-21); plan §8; tasks T075.

Seeds 5,000 tasks for one person of the LOCAL stack, every one assigned to
them (four in five open or waiting, across the four buckets and due dates
either side of today; one in five done), then times the two calls NFR-TASK-01
bounds, through the running API, and prints p50 and p95 for each:

    list     GET  /tasks             My Tasks: the four sections and a closed page
    add      POST /tasks             quick add's save, a private task each time

Every p95 must be under BOUND_MS; the script exits 1 if one is not. Write the
figures in the commit message, not in a report file.

Run it under the shared database lock (plan §8.1), in one shell, with the API
running on the same stack (`uv run uvicorn app.main:app --port 8000`):

    cd backend
    uv run python scripts/simulations/tasks_load_5k.py --tasks 5000

The person is the stack's owner (`profiles.is_owner`), or else its first
active admin; their own phase level is set to 1 for the run (My Tasks is off
by default, FR-TASK-ROLL-01) and put back afterwards. Every seeded task has
source `import` and a `source_ref` starting `load5k-`; the tasks the timed
adds make and the seeded ones stay until `npx supabase db reset`, as do their
audit rows (the audit log is append-only, 005). It signs in with a personal
token (spec 004) it issues itself and removes the token when it is done.

It refuses anything but a loopback Supabase URL: it writes thousands of rows
and must never reach the hosted project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

BOUND_MS = 1000
TAG = "load5k-"
REPO = Path(__file__).resolve().parents[3]
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SYSTEM_ACTOR = {"kind": "system", "user_id": None, "channel": None}


class Stack:
    """The local stack's SQL endpoint (pg-meta, service role), as tests/db do."""

    def __init__(self) -> None:
        env = {
            "API_URL": os.environ.get("CRM_DB_API_URL", ""),
            "SERVICE_ROLE_KEY": os.environ.get("CRM_DB_SERVICE_ROLE_KEY", ""),
        }
        if not all(env.values()):
            out = subprocess.run(
                ["npx", "--yes", "supabase", "status", "-o", "env"],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for line in out.splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() in env and not env[key.strip()]:
                    env[key.strip()] = value.strip().strip('"')
        host = urlparse(env["API_URL"]).hostname
        if host not in LOOPBACK:
            sys.exit(f"Refusing to run against {host!r}: this script is for the local stack only.")
        self.url = env["API_URL"].rstrip("/")
        self.key = env["SERVICE_ROLE_KEY"]
        self.http = httpx.Client(timeout=600)

    def sql(self, query: str) -> list[dict]:
        r = self.http.post(
            f"{self.url}/pg/query",
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"},
            json={"query": query},
        )
        body = r.json()
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            sys.exit(f"SQL failed: {body}")
        return body

    def as_system(self, statement: str) -> list[dict]:
        """With the system actor set, which the audit trigger needs (027, 048)."""
        actor = json.dumps(SYSTEM_ACTOR).replace("'", "''")
        return self.sql(f"SELECT set_config('crm.actor', '{actor}', true); {statement}")


def q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def the_person(stack: Stack) -> tuple[str, int | None]:
    rows = stack.sql(
        "SELECT id, tasks_level FROM profiles WHERE is_active "
        "AND (is_owner IS TRUE OR role = 'admin') "
        "ORDER BY is_owner DESC NULLS LAST, email LIMIT 1"
    )
    if not rows:
        sys.exit("No owner or active admin on this stack.")
    return rows[0]["id"], rows[0]["tasks_level"]


def seed(stack: Stack, person: str, tasks: int) -> None:
    already = stack.sql(
        f"SELECT count(*) AS n FROM tasks WHERE assignee_id = {q(person)} "
        f"AND source_ref LIKE '{TAG}%'"
    )[0]["n"]
    if already and already != tasks:
        sys.exit(
            f"{already} seeded tasks already there, not {tasks}; "
            "`npx supabase db reset` to start again."
        )
    if already:
        print(f"{already} seeded tasks already there; `npx supabase db reset` to start again.")
        return
    started = time.monotonic()
    me = q(person)
    # Private tasks (no workspace, no link): open, waiting and done; every
    # third due within two months either side of today; every seventh tagged.
    stack.as_system(
        "INSERT INTO tasks (workspace_id, title, body, status, bucket, priority, tags, "
        "due_on, completed_at, waiting_on_text, assignee_id, created_by, updated_by, "
        "source, source_ref) "
        "SELECT NULL, 'Load task ' || n, CASE WHEN n % 4 = 0 THEN 'Notes for ' || n END, "
        "s.status, CASE WHEN s.status <> 'done' "
        "THEN (ARRAY['today','this_week','this_month','later'])[1 + n % 4] END, "
        "(ARRAY['urgent','high','normal','normal','low'])[1 + n % 5], "
        "CASE WHEN n % 7 = 0 THEN ARRAY['load', 'tag' || (n % 3)] ELSE '{}' END, "
        "CASE WHEN n % 3 = 0 THEN current_date + (n % 120) - 60 END, "
        "CASE WHEN s.status = 'done' THEN now() - make_interval(hours => n) END, "
        "CASE WHEN s.status = 'waiting' THEN 'Someone' END, "
        f"{me}, {me}, {me}, 'import', '{TAG}' || n "
        f"FROM generate_series(1, {tasks}) AS n "
        "CROSS JOIN LATERAL (SELECT CASE WHEN n % 5 = 0 THEN 'done' "
        "WHEN n % 11 = 0 THEN 'waiting' ELSE 'open' END AS status) s"
    )
    stack.sql("ANALYZE tasks")
    print(f"Seeded {tasks} tasks in {time.monotonic() - started:.1f} s.")


def issue_token(stack: Stack, user: str) -> str:
    plaintext = "nunp_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    stack.sql(
        "INSERT INTO api_tokens (user_id, name, token_hash, prefix, expires_at) VALUES "
        f"({q(user)}, 'tasks_load_5k', {q(digest)}, {q(plaintext[:12])}, "
        "now() + interval '1 hour')"
    )
    return plaintext


def p(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    return ordered[max(0, round(pct / 100 * len(ordered)) - 1)]


def measure(api: str, token: str, runs: int) -> bool:
    http = httpx.Client(
        base_url=f"{api.rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    calls = {
        "list": lambda i: http.get("/tasks"),
        "add": lambda i: http.post("/tasks", json={"title": f"Timed add {i}"}),
    }
    first = http.get("/tasks")
    if first.status_code != 200:
        sys.exit(f"list: {first.status_code} {first.text[:300]}")
    shown = sum(len(v) for v in first.json()["sections"].values())
    print(f"GET /tasks shows {shown} open and waiting tasks in its sections.")
    ok = True
    print(f"{'call':8} {'p50 ms':>8} {'p95 ms':>8}  (n={runs}, bound {BOUND_MS} ms)")
    for name, call in calls.items():
        times = []
        for i in range(runs):
            started = time.perf_counter()
            r = call(i)
            times.append((time.perf_counter() - started) * 1000)
            if r.status_code >= 400:
                sys.exit(f"{name}: {r.status_code} {r.text[:300]}")
        p50, p95 = statistics.median(times), p(times, 95)
        ok = ok and p95 < BOUND_MS
        print(f"{name:8} {p50:8.0f} {p95:8.0f}  {'ok' if p95 < BOUND_MS else 'OVER'}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tasks", type=int, default=5_000, help="tasks to seed")
    parser.add_argument("--runs", type=int, default=40, help="timed calls of each kind")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="the running API")
    args = parser.parse_args()
    if urlparse(args.api).hostname not in LOOPBACK:
        sys.exit("The API must be the local one.")

    stack = Stack()
    person, level = the_person(stack)
    seed(stack, person, args.tasks)
    stack.sql(f"UPDATE profiles SET tasks_level = 1 WHERE id = {q(person)}")
    token = issue_token(stack, person)
    try:
        ok = measure(args.api, token, args.runs)
    finally:
        stack.sql("DELETE FROM api_tokens WHERE name = 'tasks_load_5k'")
        put_back = "NULL" if level is None else str(int(level))
        stack.sql(f"UPDATE profiles SET tasks_level = {put_back} WHERE id = {q(person)}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
