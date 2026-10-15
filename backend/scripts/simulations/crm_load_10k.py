"""The CRM at 10,000 rows — spec 007 NFR-CRM-01; quickstart §5; tasks S12.

Seeds one workspace of the LOCAL stack with 10,000 companies, 10,000 people
and 10,000 deals, then times the five calls a seller makes most, through the
running API, and prints p50 and p95 for each:

    list     POST /crm/query, the saved view companies_all
    kanban   POST /crm/query, the saved view pipeline (its stage sums too)
    search   GET  /crm/search?q=acm
    record   GET  /crm/companies/{id}, a different seeded company each time
    today    GET  /crm/today

Every p95 must be under BOUND_MS (NFR-CRM-01); the script exits 1 if one is
not. Write the figures in the commit message, not in a report file.

Usage (local Supabase up with migrations applied, and the API running on the
same stack: `uv run uvicorn app.main:app --port 8000`):

    cd backend
    uv run python scripts/simulations/crm_load_10k.py --workspace labs --rows 10000
    uv run python scripts/simulations/crm_load_10k.py --cleanup

What it writes, it tags: every seeded row's `twenty_id` starts `load10k-`,
and `--cleanup` deletes exactly those (deals, then people, then companies).
The audit rows their inserts and deletes leave stay, as the audit log is
append-only (005); `npx supabase db reset` clears them. It signs in with a
personal token (spec 004) it issues itself, as a workspace Admin, and
removes the token when it is done.

It refuses anything but a loopback Supabase URL: it writes ten thousand rows
of each kind and must never reach the hosted project.
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
TAG = "load10k-"
REPO = Path(__file__).resolve().parents[3]
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SYSTEM_ACTOR = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


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
        """With the system actor set, which the audit trigger needs (027)."""
        actor = json.dumps(SYSTEM_ACTOR).replace("'", "''")
        return self.sql(f"SELECT set_config('crm.actor', '{actor}', true); {statement}")


def q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def workspace_and_admin(stack: Stack, key: str) -> tuple[str, str]:
    rows = stack.sql(
        "SELECT w.id AS workspace_id, m.user_id FROM crm_workspaces w "
        "JOIN crm_memberships m ON m.workspace_id = w.id AND m.role = 'admin' "
        "JOIN profiles p ON p.id = m.user_id AND p.is_active "
        f"WHERE w.key = {q(key)} ORDER BY p.is_owner DESC NULLS LAST, p.email LIMIT 1"
    )
    if not rows:
        sys.exit(f"No workspace {key!r} with an active Admin on this stack.")
    return rows[0]["workspace_id"], rows[0]["user_id"]


def seed(stack: Stack, workspace: str, owner: str, rows: int) -> None:
    already = stack.sql(
        f"SELECT count(*) AS n FROM crm_companies WHERE workspace_id = {q(workspace)} "
        f"AND twenty_id LIKE '{TAG}%'"
    )[0]["n"]
    if already:
        print(f"{already} seeded companies already there; run --cleanup to start again.")
        return
    started = time.monotonic()
    ws, me = q(workspace), q(owner)
    # Every tenth company is an "Acme …", so a search for "acm" has hits to
    # rank among ten thousand names, as a real book would.
    stack.as_system(
        "INSERT INTO crm_companies (workspace_id, name, owner_id, lead_stage, city, twenty_id) "
        f"SELECT {ws}, CASE WHEN n % 10 = 0 THEN 'Acme ' ELSE '' END "
        "|| initcap(substr(md5(n::text), 1, 8)) || ' ' || n, "
        f"{me}, (ARRAY['new','contacted','nurturing','qualified','deal_open'])[1 + n % 5], "
        f"(ARRAY['Chennai','Pune','Sydney','Bengaluru'])[1 + n % 4], '{TAG}c-' || n "
        f"FROM generate_series(1, {rows}) AS n"
    )
    stack.as_system(
        "INSERT INTO crm_people (workspace_id, first_name, last_name, company_id, "
        "primary_email, owner_id, twenty_id) "
        "SELECT c.workspace_id, initcap(substr(md5('p' || c.twenty_id), 1, 6)), "
        "initcap(substr(md5('l' || c.twenty_id), 1, 7)), c.id, "
        f"replace(c.twenty_id, '{TAG}c-', 'person') || '@load10k.example', c.owner_id, "
        f"replace(c.twenty_id, '{TAG}c-', '{TAG}p-') "
        f"FROM crm_companies c WHERE c.workspace_id = {ws} AND c.twenty_id LIKE '{TAG}c-%'"
    )
    # Open deals across the four open stages, next steps spread over four
    # months either side of today; INR, so no rate is needed.
    stack.as_system(
        "INSERT INTO crm_opportunities (workspace_id, name, company_id, point_of_contact_id, "
        "stage, amount, currency, amount_in_inr, fx_rate_used, fx_month, probability, "
        "expected_close_on, proposal_sent_on, next_step, next_step_on, owner_id, twenty_id) "
        "SELECT c.workspace_id, c.name || ' - deal', c.id, p.id, s.stage, "
        "100000 + (c.n * 37) % 900000, 'INR', 100000 + (c.n * 37) % 900000, 1, "
        "date_trunc('month', now())::date, s.probability, "
        "current_date + c.n % 120, "
        "CASE WHEN s.stage = 'negotiation' THEN current_date - 7 END, "
        "'Follow up', current_date + c.n % 240 - 120, c.owner_id, "
        f"replace(c.twenty_id, '{TAG}c-', '{TAG}d-') "
        "FROM (SELECT c.*, (row_number() OVER (ORDER BY c.twenty_id))::int AS n FROM crm_companies c "
        f"WHERE c.workspace_id = {ws} AND c.twenty_id LIKE '{TAG}c-%') c "
        "JOIN crm_people p ON p.workspace_id = c.workspace_id "
        f"AND p.twenty_id = replace(c.twenty_id, '{TAG}c-', '{TAG}p-') "
        "CROSS JOIN LATERAL (SELECT (ARRAY['discovery','qualification','proposal','negotiation'])"
        "[1 + c.n % 4] AS stage, (ARRAY[10, 25, 50, 75])[1 + c.n % 4] AS probability) s"
    )
    stack.sql("ANALYZE crm_companies; ANALYZE crm_people; ANALYZE crm_opportunities")
    print(f"Seeded {rows} companies, people and deals in {time.monotonic() - started:.1f} s.")


def cleanup(stack: Stack) -> None:
    for table in ("crm_opportunities", "crm_people", "crm_companies"):
        gone = stack.as_system(
            f"WITH d AS (DELETE FROM {table} WHERE twenty_id LIKE '{TAG}%' RETURNING 1) "
            "SELECT count(*) AS n FROM d"
        )
        print(f"{table}: {gone[-1]['n']} removed")
    stack.sql("DELETE FROM api_tokens WHERE name = 'crm_load_10k'")


def issue_token(stack: Stack, user: str) -> str:
    plaintext = "nunp_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    stack.sql(
        "INSERT INTO api_tokens (user_id, name, token_hash, prefix, expires_at) VALUES "
        f"({q(user)}, 'crm_load_10k', {q(digest)}, {q(plaintext[:12])}, now() + interval '1 hour')"
    )
    return plaintext


def p(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    return ordered[max(0, round(pct / 100 * len(ordered)) - 1)]


def measure(api: str, token: str, workspace_key: str, ids: list[str], runs: int) -> bool:
    http = httpx.Client(
        base_url=f"{api.rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    ws = {"workspace": workspace_key}
    calls = {
        "list": lambda i: http.post(
            "/crm/query", params=ws, json={"object": "company", "view": "companies_all"}
        ),
        "kanban": lambda i: http.post(
            "/crm/query", params=ws, json={"object": "opportunity", "view": "pipeline"}
        ),
        "search": lambda i: http.get("/crm/search", params={**ws, "q": "acm"}),
        "record": lambda i: http.get(f"/crm/companies/{ids[i % len(ids)]}", params=ws),
        "today": lambda i: http.get("/crm/today", params=ws),
    }
    ok = True
    print(f"{'call':8} {'p50 ms':>8} {'p95 ms':>8}  (n={runs}, bound {BOUND_MS} ms)")
    for name, call in calls.items():
        first = call(0)
        if first.status_code != 200:
            sys.exit(f"{name}: {first.status_code} {first.text[:300]}")
        times = []
        for i in range(runs):
            started = time.perf_counter()
            r = call(i)
            times.append((time.perf_counter() - started) * 1000)
            r.raise_for_status()
        p50, p95 = statistics.median(times), p(times, 95)
        ok = ok and p95 < BOUND_MS
        print(f"{name:8} {p50:8.0f} {p95:8.0f}  {'ok' if p95 < BOUND_MS else 'OVER'}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", default="labs", help="workspace key (default labs)")
    parser.add_argument("--rows", type=int, default=10_000, help="companies to seed")
    parser.add_argument("--runs", type=int, default=40, help="timed calls of each kind")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="the running API")
    parser.add_argument("--cleanup", action="store_true", help="remove what this seeded")
    args = parser.parse_args()
    if urlparse(args.api).hostname not in LOOPBACK:
        sys.exit("The API must be the local one.")

    stack = Stack()
    if args.cleanup:
        cleanup(stack)
        return
    workspace, admin = workspace_and_admin(stack, args.workspace)
    seed(stack, workspace, admin, args.rows)
    ids = [
        r["id"]
        for r in stack.sql(
            f"SELECT id FROM crm_companies WHERE workspace_id = {q(workspace)} "
            f"AND twenty_id LIKE '{TAG}%' ORDER BY random() LIMIT 200"
        )
    ]
    token = issue_token(stack, admin)
    try:
        ok = measure(args.api, token, args.workspace, ids, args.runs)
    finally:
        stack.sql("DELETE FROM api_tokens WHERE name = 'crm_load_10k'")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
