"""The two audits — spec 007 FR-CRM-AUD-03, -04; FR-CRM-TVA-03.

TVA's audit (`GET /admin/audit`, and the `audit_log` tool, which calls it)
shows TVA rows only: a CRM row, one with a workspace, never appears there, for
anyone. It names CRM-only actors too: they are left out of every TVA people
list (`list_profiles`'s default), but an audit row a seller caused must still
show their name. A TVA change the CRM made for someone (D8) is a `system` row
that names them: "*Name* (via CRM deal)".

A workspace's own audit (`GET /crm/audit`) is for that workspace's admins,
shows that workspace's rows only, and filters by record, person and action.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OUTSIDER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, current_user
from app.main import app
from app.mcp import server
from app.services import supabase as db

ADMIN = {
    "id": "u-admin",
    "email": "admin@example.com",
    "display_name": "Admin",
    "role": "admin",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
SELLER = {
    "id": "u-crm",
    "email": "seller@example.com",
    "display_name": "Sanjay Seller",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "logs_time": False,
    "account_type": "crm_only",
}


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def list_profiles(*, active_only=False, include_crm_only=False):
        seen.append({"include_crm_only": include_crm_only})
        return [p for p in (ADMIN, SELLER) if include_crm_only or p["account_type"] == "staff"]

    monkeypatch.setattr(db, "list_profiles", list_profiles)

    def list_audit(limit, *, tva_only=True):
        seen.append({"tva_only": tva_only})
        return [
            {"id": 1, "actor_id": "u-crm", "action": "token.created", "target_table": "api_tokens"},
            {"id": 2, "actor_id": "u-admin", "action": "user.updated", "target_table": "profiles"},
            {
                "id": 3,
                "actor_id": "u-admin",
                "actor_label": "system",
                "action": "project.updated",
                "target_table": "projects",
            },
            # What crm_remove_membership writes (027): the removing admin's
            # own kind, so no "(via CRM deal)".
            {
                "id": 4,
                "actor_id": "u-admin",
                "actor_label": "user",
                "action": "user.deactivated",
                "target_table": "profiles",
            },
        ]

    monkeypatch.setattr(db, "list_audit", list_audit)
    app.dependency_overrides[current_user] = lambda: CurrentUser(ADMIN)
    yield seen
    app.dependency_overrides.pop(current_user, None)


class TestReadAudit:
    def test_names_a_crm_only_actor(self, calls):
        rows = TestClient(app).get("/api/v1/admin/audit").json()
        assert {r["id"]: r["actor"] for r in rows}[1] == "Sanjay Seller"
        assert {r["id"]: r["actor"] for r in rows}[2] == "Admin"

    def test_asks_for_crm_only_names_explicitly(self, calls):
        TestClient(app).get("/api/v1/admin/audit")
        assert {"include_crm_only": True} in calls

    def test_reads_tva_rows_only(self, calls):
        TestClient(app).get("/api/v1/admin/audit")
        assert {"tva_only": True} in calls

    def test_a_system_row_names_the_person_via_the_crm(self, calls):
        rows = TestClient(app).get("/api/v1/admin/audit").json()
        assert {r["id"]: r["actor"] for r in rows}[3] == "Admin (via CRM deal)"

    def test_a_membership_removal_deactivation_is_the_admins_own(self, calls):
        rows = TestClient(app).get("/api/v1/admin/audit").json()
        assert {r["id"]: r["actor"] for r in rows}[4] == "Admin"


class _Recorder:
    """Stands in for the PostgREST query builder and records each call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            return self

        return call

    def execute(self):
        return type("Result", (), {"data": []})()


class TestListAuditQuery:
    def test_the_tva_read_filters_out_workspace_rows(self, monkeypatch):
        recorder = _Recorder()
        monkeypatch.setattr(db.supabase, "table", lambda name: recorder)
        db.list_audit(10)
        assert ("is_", ("workspace_id", "null")) in recorder.calls

    def test_only_an_explicit_ask_reads_crm_rows(self, monkeypatch):
        recorder = _Recorder()
        monkeypatch.setattr(db.supabase, "table", lambda name: recorder)
        db.list_audit(10, tva_only=False)
        assert not [c for c in recorder.calls if c[0] == "is_"]


class TestAuditTool:
    def test_the_audit_log_tool_reads_the_tva_route(self, monkeypatch):
        seen = []

        async def fake_api(ctx, method, path, **kwargs):
            seen.append((method, path))
            return []

        monkeypatch.setattr(server, "_api", fake_api)
        asyncio.run(server.audit_log(object()))
        assert seen == [("GET", "/admin/audit")]


# ---------------------------------------------------------------------------
# GET /crm/audit — FR-CRM-AUD-03
# ---------------------------------------------------------------------------


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    http = crm_client(monkeypatch)
    # Two changes in Labs (one by Deepika), one in Academy.
    for ws, who, label in (
        (labs, OWNER, "Event"),
        (labs, MEMBER, "Partner"),
        (academy, OWNER, "Web"),
    ):
        fake.apply(
            {"user_id": who["id"], "kind": "user", "channel": "web", "workspace_id": ws["id"]},
            [
                {
                    "op": "insert",
                    "table": "crm_select_options",
                    "values": {
                        "list_key": "channel",
                        "label": label,
                        "position": 9,
                        "is_fixed": False,
                        "is_default": False,
                    },
                    "action": "option.created",
                }
            ],
        )
    return http, fake, labs, academy


class TestCrmAudit:
    def test_admin_only(self, crm):
        http, *_ = crm
        r = http.get("/api/v1/crm/audit?workspace=labs", headers=as_(MEMBER["id"]))
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a workspace admin can do that."

    def test_workspace_scoped(self, crm):
        http, *_ = crm
        rows = http.get("/api/v1/crm/audit?workspace=labs", headers=as_(OWNER["id"])).json()
        assert sorted(r["after"]["label"] for r in rows) == ["Event", "Partner"]

    def test_filtered_by_person(self, crm):
        http, *_ = crm
        rows = http.get(
            f"/api/v1/crm/audit?workspace=labs&actor_id={MEMBER['id']}", headers=as_(OWNER["id"])
        ).json()
        assert [r["actor_name"] for r in rows] == ["Deepika"]

    def test_filtered_by_record(self, crm):
        http, fake, labs, _ = crm
        target = fake.option(labs, "Event")["id"]
        rows = http.get(
            f"/api/v1/crm/audit?workspace=labs&record_type=option&record_id={target}",
            headers=as_(OWNER["id"]),
        ).json()
        assert [r["record_id"] for r in rows] == [target]
        assert rows[0]["record_type"] == "option"

    def test_carries_kind_and_channel(self, crm):
        http, *_ = crm
        row = http.get("/api/v1/crm/audit?workspace=labs", headers=as_(OWNER["id"])).json()[0]
        assert (row["actor_kind"], row["channel"], row["action"]) == (
            "user",
            "web",
            "option.created",
        )

    def test_a_tva_admin_who_is_not_a_member_sees_nothing(self, crm):
        """Q-22: a TVA admin reads a workspace's CRM audit only as its admin."""
        http, *_ = crm
        r = http.get("/api/v1/crm/audit?workspace=labs", headers=as_(OUTSIDER["id"]))
        assert r.status_code == 404
        assert r.json()["detail"] == "No such workspace."
