"""Real-database tests — spec 007 tasks "Gates".

Everything else in tests/ runs against in-memory fakes. These run against the
LOCAL Supabase stack (`npx supabase start`, then `npx supabase db reset`), to
prove what only Postgres can: grants, triggers and constraints. They are
marked `db` and skipped unless `CRM_DB_TESTS=1`:

    cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q

Connection details come from the environment (`CRM_DB_API_URL`,
`CRM_DB_ANON_KEY`, `CRM_DB_SERVICE_ROLE_KEY`, `CRM_DB_JWT_SECRET`), or else
from `npx supabase status -o env` run at the repository root. Anything but a
loopback URL is refused: these tests create and delete rows, and must never
reach the hosted project.

SQL is sent through the local stack's pg-meta endpoint (`/pg/query`, service
role only), so no Postgres driver is needed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs the local Supabase stack (CRM_DB_TESTS=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CRM_DB_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="real-database test; set CRM_DB_TESTS=1 with local Supabase up")
    for item in items:
        if item.get_closest_marker("db"):
            item.add_marker(skip)


def _status() -> dict[str, str]:
    out = subprocess.run(
        ["npx", "--yes", "supabase", "status", "-o", "env"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    values = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"')
    return values


class SqlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LocalStack:
    #: For tests, which cannot import from a conftest.
    SqlError = SqlError
    q = staticmethod(_q)

    def __init__(self) -> None:
        names = ("API_URL", "ANON_KEY", "SERVICE_ROLE_KEY", "JWT_SECRET")
        env = {n: os.environ.get(f"CRM_DB_{n}", "") for n in names}
        if not all(env.values()):
            found = _status()
            env = {n: env[n] or found.get(n, "") for n in names}
        host = urlparse(env["API_URL"]).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(f"Refusing to run database tests against {host!r}.")
        self.url = env["API_URL"].rstrip("/")
        self.anon_key = env["ANON_KEY"]
        self.service_key = env["SERVICE_ROLE_KEY"]
        self.jwt_secret = env["JWT_SECRET"]
        self.http = httpx.Client(timeout=30)

    def sql(self, query: str) -> list[dict]:
        """Run SQL as the database owner. A Postgres error raises SqlError."""
        r = self.http.post(
            f"{self.url}/pg/query",
            headers={"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"},
            json={"query": query},
        )
        body = r.json()
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            raise SqlError(body.get("code", ""), body.get("message", str(body)))
        return body

    def jwt_for(self, user_id: str) -> str:
        """A session JWT as GoTrue would issue it, signed with the local secret."""

        def part(obj: dict) -> str:
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        now = int(time.time())
        head = part({"alg": "HS256", "typ": "JWT"})
        claims = part(
            {
                "sub": user_id,
                "role": "authenticated",
                "aud": "authenticated",
                "iat": now,
                "exp": now + 3600,
            }
        )
        signed = f"{head}.{claims}".encode()
        sig = hmac.new(self.jwt_secret.encode(), signed, hashlib.sha256).digest()
        return f"{head}.{claims}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"

    def rest(self, table: str, *, bearer: str) -> httpx.Response:
        """Read a table the way the browser could: anon key plus a bearer."""
        return self.http.get(
            f"{self.url}/rest/v1/{table}",
            params={"select": "*", "limit": "5"},
            headers={"apikey": self.anon_key, "Authorization": f"Bearer {bearer}"},
        )


@pytest.fixture(scope="session")
def stack() -> LocalStack:
    return LocalStack()


@pytest.fixture
def make_profile(stack):
    """Create an auth user and its profile; both are removed afterwards."""
    made: list[str] = []

    def make(**profile) -> str:
        uid = str(uuid.uuid4())
        email = f"crm-db-test-{uid[:8]}@example.com"
        stack.sql(
            "INSERT INTO auth.users (id, instance_id, aud, role, email) VALUES "
            f"({_q(uid)}, '00000000-0000-0000-0000-000000000000', 'authenticated', "
            f"'authenticated', {_q(email)})"
        )
        made.append(uid)
        columns = {"id": uid, "email": email, "display_name": f"Test {uid[:8]}", **profile}
        names = ", ".join(columns)
        values = ", ".join(
            "NULL" if v is None else str(v).lower() if isinstance(v, bool) else _q(str(v))
            for v in columns.values()
        )
        stack.sql(f"INSERT INTO profiles ({names}) VALUES ({values})")
        return uid

    yield make
    if made:
        ids = ", ".join(_q(uid) for uid in made)
        stack.sql(f"DELETE FROM auth.users WHERE id IN ({ids})")


@pytest.fixture
def seller(make_profile) -> str:
    """A CRM-only account, shaped as 025 requires."""
    return make_profile(account_type="crm_only", role="user", logs_time=False)
