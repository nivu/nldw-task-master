"""Dashboards, CSV import and export in the real database — spec 007
FR-CRM-DASH-01, -02, FR-CRM-IO-01, -03, -04, FR-CRM-LIST-06, FR-CRM-AUD-01;
data-model §3.24; migration 043; quickstart §3 S11 (dashboard panels load
for an admin; export a view to CSV; import that CSV back in update mode with
no changes).

What only Postgres can prove: a bookings target's period starts on its
first day and is set once per workspace, entity and period (with no entity
counted as one); a saved mapping's name is unique per object, ignoring
case; both tables are audited; `crm_record_export` writes the export's
audit row for a member of the workspace and refuses anyone else, and the
browser cannot call it. Then the S11 checks through the real routes.

Each test works in a workspace it creates and asserts only on its rows: the
local database keeps leftovers between runs.
"""

from __future__ import annotations

import csv
import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from supabase import create_client

from app.api import deps
from app.main import app
from app.services import supabase as db
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web", "workspace_id": workspace}


def _apply_sql(stack, actor: dict, ops: list[dict]) -> str:
    return f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r"


def _refused(stack, query: str, *codes: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code in codes, raised.value.message
    return raised.value.message


def _new_workspace(stack, owner: str) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


def _target(month: str, kind: str = "month", entity: str | None = None) -> dict:
    return {
        "op": "insert",
        "table": "crm_bookings_targets",
        "values": {
            "entity": entity,
            "period_kind": kind,
            "period_start": month,
            "amount_inr": "1000",
        },
    }


class TestTargets:
    def test_a_period_starts_on_its_first_day(self, stack, owner):
        ws = _new_workspace(stack, owner)
        me = _actor(ws, owner)
        for op in (
            _target("2026-10-02"),
            _target("2026-11-01", "quarter"),
            _target("2026-01-01", "fy"),
        ):
            message = _refused(stack, _apply_sql(stack, me, [op]), "23514")
            assert "crm_bookings_targets_period_start" in message
        stack.sql(_apply_sql(stack, me, [_target("2026-10-01", "quarter")]))

    def test_one_target_per_entity_and_period(self, stack, owner):
        ws = _new_workspace(stack, owner)
        me = _actor(ws, owner)
        stack.sql(_apply_sql(stack, me, [_target("2026-10-01")]))
        message = _refused(stack, _apply_sql(stack, me, [_target("2026-10-01")]), "23505")
        assert "crm_bookings_targets_period" in message
        stack.sql(_apply_sql(stack, me, [_target("2026-10-01", entity="deepweaver")]))
        actions = stack.sql(
            "SELECT action FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND target_table = 'crm_bookings_targets'"
        )
        assert [a["action"] for a in actions] == ["bookings_target.created"] * 2


class TestMappings:
    def test_a_name_once_per_object_ignoring_case(self, stack, owner):
        ws = _new_workspace(stack, owner)
        me = _actor(ws, owner)

        def save(obj: str, name: str) -> str:
            return _apply_sql(
                stack,
                me,
                [
                    {
                        "op": "insert",
                        "table": "crm_csv_mappings",
                        "values": {"object": obj, "name": name, "mapping": {"Org": "name"}},
                    }
                ],
            )

        stack.sql(save("company", "Fair list"))
        assert "crm_csv_mappings_name" in _refused(stack, save("company", "FAIR LIST"), "23505")
        stack.sql(save("person", "Fair list"))
        _refused(stack, save("task", "Tasks"), "23514")
        actions = stack.sql(
            "SELECT action FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND target_table = 'crm_csv_mappings'"
        )
        assert [a["action"] for a in actions] == ["csv_mapping.created"] * 2


class TestRecordExport:
    def test_a_members_export_is_audited_with_no_values(self, stack, owner):
        ws = _new_workspace(stack, owner)
        detail = {"kind": "view", "table": "crm_companies", "object": "company", "rows": 3}
        stack.sql(f"SELECT crm_record_export({_j(stack, _actor(ws, owner))}, {_j(stack, detail)})")
        [row] = stack.sql(
            "SELECT action, target_table, target_id, after, channel, actor_label FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND action LIKE 'export.%'"
        )
        assert (row["action"], row["target_table"], row["target_id"]) == (
            "export.view",
            "crm_companies",
            None,
        )
        assert row["after"] == {"kind": "view", "object": "company", "rows": 3}
        assert (row["channel"], row["actor_label"]) == ("web", "user")

    def test_someone_not_a_member_is_refused(self, stack, owner, make_profile):
        ws = _new_workspace(stack, owner)
        stranger = make_profile()
        detail = {"kind": "workspace", "rows": {}}
        _refused(
            stack,
            f"SELECT crm_record_export({_j(stack, _actor(ws, stranger))}, {_j(stack, detail)})",
            "CRM04",
        )
        _refused(
            stack,
            f"SELECT crm_record_export({_j(stack, _actor(ws, owner))}, "
            f"{_j(stack, {'kind': 'other'})})",
            "CRM22",
        )

    def test_the_browser_cannot_call_it(self, stack, owner):
        r = stack.http.post(
            f"{stack.url}/rest/v1/rpc/crm_record_export",
            json={"p_actor": {}, "p_detail": {}},
            headers={"apikey": stack.anon_key, "Authorization": f"Bearer {stack.jwt_for(owner)}"},
        )
        assert r.status_code in (401, 403, 404), r.text


class TestQuickstartS11:
    """Dashboard panels load for an admin; a view exports to CSV and imports
    back in update mode with no changes — through the real routes."""

    @pytest.fixture
    def http(self, stack, owner, monkeypatch):
        local = create_client(stack.url, stack.service_key)
        monkeypatch.setattr(db, "supabase", local)
        monkeypatch.setattr(store, "supabase", local)
        monkeypatch.setattr(deps, "_verify", lambda token: token)
        ws = _new_workspace(stack, owner)
        key = stack.sql(f"SELECT key FROM crm_workspaces WHERE id = {stack.q(ws)}")[0]["key"]
        return TestClient(app), {"Authorization": f"Bearer {owner}"}, f"?workspace={key}", ws

    def test_export_and_import_back_unchanged(self, stack, http):
        client, me, q, ws = http
        tag = uuid.uuid4().hex[:8]
        for n in range(3):
            r = client.post(
                f"/api/v1/crm/companies{q}",
                json={"name": f"College {n} {tag}", "domain": f"c{n}-{tag}.edu", "city": "Kochi"},
                headers=me,
            )
            assert r.status_code == 201, r.text
        spec = {"q": tag, "sorts": [{"field": "name", "dir": "asc"}]}
        r = client.post(
            f"/api/v1/crm/export/view{q}",
            json={"object": "companies", "spec": spec, "columns": ["name", "domain", "city"]},
            headers=me,
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-disposition"].startswith('attachment; filename="companies-')
        content = r.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(content)))
        assert [row[1] for row in rows[1:]] == [f"College {n} {tag}" for n in range(3)]

        body = {"format": "csv", "content": content, "object": "companies", "mode": "update"}
        r = client.post(f"/api/v1/crm/imports/dry-run{q}", json=body, headers=me)
        assert r.status_code == 200, r.text
        dry = r.json()
        assert dry["counts"]["companies"] == {
            "in_file": 3,
            "create": 0,
            "update": 0,
            "unchanged": 3,
            "skipped": 0,
        }
        r = client.post(f"/api/v1/crm/imports/{dry['run_id']}/apply{q}", json=body, headers=me)
        assert r.status_code == 200, r.text
        assert (r.json()["created"], r.json()["updated"], r.json()["failed"]) == (0, 0, [])
        audit = stack.sql(
            "SELECT action FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND action LIKE 'export.%'"
        )
        assert [a["action"] for a in audit] == ["export.view"]

    def test_a_csv_of_people_relates_by_name_and_domain(self, stack, http):
        client, me, q, ws = http
        tag = uuid.uuid4().hex[:8]
        r = client.post(
            f"/api/v1/crm/companies{q}",
            json={"name": f"Amrita {tag}", "domain": f"amrita-{tag}.edu"},
            headers=me,
        )
        company = r.json()
        content = (
            "First name,Company,Email\n"
            f"Ravi,Amrita {tag},ravi-{tag}@amrita.edu\n"
            f"Asha,amrita-{tag}.edu,asha-{tag}@amrita.edu\n"
        )
        body = {"format": "csv", "content": content, "object": "people"}
        dry = client.post(f"/api/v1/crm/imports/dry-run{q}", json=body, headers=me).json()
        assert [row["errors"] for row in dry["rows"]] == [[], []]
        r = client.post(f"/api/v1/crm/imports/{dry['run_id']}/apply{q}", json=body, headers=me)
        assert r.status_code == 200, r.text
        people = stack.sql(
            f"SELECT first_name, company_id FROM crm_people WHERE workspace_id = {stack.q(ws)} "
            "ORDER BY first_name"
        )
        assert people == [
            {"first_name": "Asha", "company_id": company["id"]},
            {"first_name": "Ravi", "company_id": company["id"]},
        ]

    def test_the_dashboard_loads_for_an_admin(self, stack, http):
        client, me, q, ws = http
        tag = uuid.uuid4().hex[:8]
        company = client.post(
            f"/api/v1/crm/companies{q}", json={"name": f"Kochi {tag}"}, headers=me
        ).json()
        client.patch(
            f"/api/v1/crm/companies/{company['id']}{q}",
            json={"lead_stage": "qualified"},
            headers=me,
        )
        r = client.post(
            f"/api/v1/crm/opportunities{q}",
            json={
                "name": f"Kochi deal {tag}",
                "company_id": company["id"],
                "amount": "250000",
                "currency": "INR",
                "next_step": "Call",
                "next_step_on": "2026-12-01",
            },
            headers=me,
        )
        assert r.status_code == 201, r.text
        r = client.put(
            f"/api/v1/crm/targets{q}",
            json=[{"period_kind": "fy", "period_start": "2026-04-01", "amount_inr": "1000000"}],
            headers=me,
        )
        assert r.status_code == 200, r.text
        r = client.get(f"/api/v1/crm/dashboard{q}", headers=me)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pipeline_by_stage"]["total"] == {
            "count": 1,
            "amount_in_inr": "250000.00",
            "weighted": "25000.00",
        }
        fy = body["bookings"][2]
        assert (fy["target"], fy["value"]) == ("1000000.00", "0.00")
        assert body["time_in_stage"]["days_to_close"] == {"average_days": None, "deals": 0}
        funnel = {s["lead_stage"]: s["count"] for s in body["lead_funnel"]["stages"]}
        assert funnel["deal_open"] == 1
        assert "capacity" not in body
