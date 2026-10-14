"""Lists in the real database — spec 007 FR-CRM-VIEW-02, -03, -05, -08, -10,
-11, FR-CRM-ACC-11, FR-CRM-UI-02, FR-CRM-DEL-01, FR-CRM-WS-02; data-model
§3.15, §3.16, §5.4, §5.8, §7.5; plan §4 CT-1; migration 034.

What only Postgres can prove: `crm_query()` answers filters, sorts, groups,
totals and keyset paging as computed by hand here; a field name, operator,
sort direction, group or cursor carrying SQL is refused, and a value or `q`
carrying SQL is only ever a literal; `p_redact` refuses every amount field
and leaves the amount columns out of the rows; deleted and merged rows and
another workspace's rows never appear; a favourite of another workspace's
record is refused; `crm_purge` takes a record's favourites with it; the
registry table is Python's registry; and the seeded views return the right
rows in both workspaces through `POST /crm/query`.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from supabase import create_client

from app.api import deps
from app.domain.crm import objects
from app.domain.crm import views as rules
from app.main import app
from app.services import supabase as db
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
AMOUNT = "Viewers cannot filter, sort or total by amount."


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web", "workspace_id": workspace}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _query(stack, workspace: str, kind: str, spec: dict, redact: bool = False) -> dict:
    return stack.sql(
        f"SELECT crm_query({stack.q(workspace)}::uuid, {stack.q(kind)}, {_j(stack, spec)}, "
        f"{'true' if redact else 'false'}) AS r"
    )[0]["r"]


def _refused(stack, workspace: str, kind: str, spec: dict, redact: bool = False) -> str:
    with pytest.raises(stack.SqlError) as raised:
        _query(stack, workspace, kind, spec, redact)
    assert raised.value.code == "CRM22", raised.value.message
    return raised.value.message


def _new_workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]


def _insert(stack, workspace, owner, table, values) -> str:
    return _apply(
        stack, _actor(workspace, owner), [{"op": "insert", "table": table, "values": values}]
    )[0]["id"]


def _company(stack, workspace, owner, name, **values) -> str:
    return _insert(
        stack, workspace, owner, "crm_companies", {"name": name, "owner_id": owner, **values}
    )


def _deal(stack, workspace, owner, company, name, amount, probability, **values) -> str:
    return _insert(
        stack,
        workspace,
        owner,
        "crm_opportunities",
        {
            "name": name,
            "company_id": company,
            "owner_id": owner,
            "amount": amount,
            "currency": "INR",
            "amount_in_inr": amount,
            "fx_rate_used": 1,
            "fx_month": "2026-10-01",
            "probability": probability,
            "next_step": "Call",
            "next_step_on": "2026-10-20",
            **values,
        },
    )


def _names(answer) -> list[str]:
    return [r["name"] for r in answer["rows"]]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def world(stack, owner):
    """A workspace with five companies (one deleted, one merged) and two
    deals, and a second workspace with one company of its own."""
    ws = _new_workspace(stack)
    other = _new_workspace(stack)
    a = _company(stack, ws, owner, "alpha", city="Pune", next_touch_on="2026-01-05")
    _company(stack, ws, owner, "Beta", lead_stage="qualified")
    _company(stack, ws, owner, "gamma", city="pune", lead_stage="qualified")
    gone = _company(stack, ws, owner, "Delta")
    _apply(stack, _actor(ws, owner), [{"op": "soft_delete", "table": "crm_companies", "id": gone}])
    merged = _company(stack, ws, owner, "Merged away")
    _apply(
        stack,
        _actor(ws, owner),
        [{"op": "update", "table": "crm_companies", "id": merged, "values": {"merged_into_id": a}}],
    )
    _company(stack, other, owner, "alpha elsewhere")
    _deal(stack, ws, owner, a, "Small", 1000, 10, expected_close_on="2026-10-25")
    _deal(stack, ws, owner, a, "Large", 3000, 50, expected_close_on="2026-12-05")
    return ws, other


class TestAnswers:
    def test_filters(self, stack, world):
        ws, _ = world
        cases = [
            ({"field": "city", "operator": "is", "value": "PUNE"}, ["alpha", "gamma"]),
            ({"field": "city", "operator": "is_empty"}, ["Beta"]),
            ({"field": "city", "operator": "is_not", "value": "pune"}, ["Beta"]),
            ({"field": "name", "operator": "contains", "value": "AM"}, ["gamma"]),
            ({"field": "name", "operator": "not_contains", "value": "a"}, []),
            (
                {"field": "lead_stage", "operator": "any_of", "value": ["qualified"]},
                ["Beta", "gamma"],
            ),
            ({"field": "lead_stage", "operator": "none_of", "value": ["qualified"]}, ["alpha"]),
            ({"field": "next_touch_on", "operator": "before", "value": "2026-02-01"}, ["alpha"]),
            ({"field": "next_touch_on", "operator": "on", "value": "2026-01-05"}, ["alpha"]),
            ({"field": "next_touch_on", "operator": "after", "value": "2026-01-05"}, []),
        ]
        for condition, expected in cases:
            spec = {
                "filters": {"op": "and", "items": [condition]},
                "sorts": [{"field": "name", "dir": "asc"}],
            }
            assert _names(_query(stack, ws, "company", spec)) == expected, condition

    def test_and_or(self, stack, world):
        ws, _ = world
        spec = {
            "filters": {
                "op": "or",
                "items": [
                    {"field": "name", "operator": "is", "value": "beta"},
                    {
                        "op": "and",
                        "items": [
                            {"field": "city", "operator": "is_not_empty"},
                            {"field": "lead_stage", "operator": "any_of", "value": ["qualified"]},
                        ],
                    },
                ],
            },
            "sorts": [{"field": "name", "dir": "desc"}],
        }
        assert _names(_query(stack, ws, "company", spec)) == ["gamma", "Beta"]

    def test_within_uses_the_given_today(self, stack, world):
        ws, _ = world
        spec = {
            "filters": {
                "op": "and",
                "items": [
                    {"field": "expected_close_on", "operator": "within_next_days", "value": 14}
                ],
            },
            "today": "2026-10-14",
        }
        assert _names(_query(stack, ws, "opportunity", spec)) == ["Small"]

    def test_sorts_nulls_last_and_keyset_pages(self, stack, world):
        ws, _ = world
        spec = {
            "sorts": [{"field": "city", "dir": "asc"}, {"field": "name", "dir": "desc"}],
            "limit": 1,
        }
        seen = []
        for _ in range(4):
            page = _query(stack, ws, "company", spec)
            seen += _names(page)
            if page["next"] is None:
                break
            spec = {**spec, "after": page["next"]}
        # city: "Pune" and "pune" sort together (lower), then the empty one.
        assert seen == ["gamma", "alpha", "Beta"]
        assert page["total"] == 3

    def test_groups_and_totals(self, stack, world):
        ws, _ = world
        answer = _query(
            stack,
            ws,
            "opportunity",
            {
                "group_by": "stage",
                "aggregates": [
                    {"fn": "count"},
                    {"field": "amount_in_inr", "fn": "sum"},
                    {"field": "weighted_amount", "fn": "sum"},
                    {"field": "amount", "fn": "avg"},
                    {"field": "amount", "fn": "min"},
                    {"field": "probability", "fn": "max"},
                ],
            },
        )
        totals = answer["aggregates"]
        assert totals["count"] == 2
        assert float(totals["sum_amount_in_inr"]) == 4000
        assert float(totals["sum_weighted_amount"]) == 1600
        assert float(totals["avg_amount"]) == 2000
        assert float(totals["min_amount"]) == 1000
        assert totals["max_probability"] == 50
        assert [(g["key"], g["count"]) for g in answer["groups"]] == [("discovery", 2)]
        companies = _query(stack, ws, "company", {"group_by": "lead_stage"})
        assert {g["key"]: g["count"] for g in companies["groups"]} == {"new": 1, "qualified": 2}

    def test_q_is_literal(self, stack, owner):
        ws = _new_workspace(stack)
        _company(stack, ws, owner, "50%_off deals")
        _company(stack, ws, owner, "500 offices")
        spec = rules.resolve({"q": "50%_off"}, user_id=owner, today=date(2026, 10, 14))
        assert _names(_query(stack, ws, "company", spec)) == ["50%_off deals"]


class TestNothingGetsIn:
    """CT-1 — SQL in a name is refused; SQL in a value is a literal."""

    @pytest.mark.parametrize(
        "spec",
        [
            {
                "filters": {
                    "op": "and",
                    "items": [
                        {
                            "field": "name; DROP TABLE crm_companies; --",
                            "operator": "is",
                            "value": "x",
                        }
                    ],
                }
            },
            {
                "filters": {
                    "op": "and",
                    "items": [{"field": "name", "operator": "= 'x' OR 1=1 --", "value": "x"}],
                }
            },
            {"filters": {"op": "or); DROP TABLE crm_companies; --", "items": []}},
            {"sorts": [{"field": "name", "dir": "asc; DROP TABLE crm_companies"}]},
            {"sorts": [{"field": "(SELECT 1)", "dir": "asc"}]},
            {"group_by": "name) FROM crm_companies; --"},
            {"aggregates": [{"field": "name", "fn": "pg_sleep"}]},
            {
                "after": ["x'); DROP TABLE crm_companies; --", "not-a-uuid"],
                "sorts": [{"field": "name", "dir": "asc"}],
            },
            {
                "filters": {
                    "op": "and",
                    "items": [
                        {"field": "next_touch_on", "operator": "on", "value": "2026-01-01'; --"}
                    ],
                }
            },
        ],
    )
    def test_refused(self, stack, world, spec):
        ws, _ = world
        _refused(stack, ws, "company", spec)
        assert stack.sql("SELECT to_regclass('public.crm_companies') IS NOT NULL AS ok")[0]["ok"]

    def test_values_are_literals(self, stack, world):
        ws, _ = world
        for value in ("x' OR '1'='1", "alpha'; DELETE FROM crm_companies; --"):
            spec = {
                "filters": {
                    "op": "and",
                    "items": [{"field": "name", "operator": "is", "value": value}],
                }
            }
            assert _query(stack, ws, "company", spec)["rows"] == []
        assert _query(stack, ws, "company", {"q": "' OR 1=1 --"})["rows"] == []
        assert _query(stack, ws, "company", {})["total"] == 3

    def test_not_a_list(self, stack, world):
        ws, _ = world
        _refused(stack, ws, "crm_companies; --", {})
        _refused(stack, ws, "campaign", {})


class TestRedaction:
    @pytest.mark.parametrize(
        "spec",
        [
            {
                "filters": {
                    "op": "and",
                    "items": [{"field": "amount", "operator": "gt", "value": 1}],
                }
            },
            {
                "filters": {
                    "op": "or",
                    "items": [
                        {
                            "op": "and",
                            "items": [{"field": "weighted_amount", "operator": "is_empty"}],
                        }
                    ],
                }
            },
            {"sorts": [{"field": "amount_in_inr", "dir": "desc"}]},
            {"group_by": "amount"},
            {"aggregates": [{"field": "amount_in_inr", "fn": "sum"}]},
            {"aggregates": [{"field": "amount", "fn": "count"}]},
            {"columns": [{"field": "amount", "width": 100}]},
        ],
    )
    def test_p_redact_refuses_amount_fields(self, stack, world, spec):
        ws, _ = world
        assert _refused(stack, ws, "opportunity", spec, redact=True) == AMOUNT
        if "group_by" not in spec:
            # The same spec is answered for an Admin or Member.
            _query(stack, ws, "opportunity", spec)

    def test_the_rows_carry_no_amount_column(self, stack, world):
        ws, _ = world
        rows = _query(stack, ws, "opportunity", {}, redact=True)["rows"]
        assert len(rows) == 2
        for row in rows:
            assert not {"amount", "amount_in_inr", "fx_rate_used", "won_fx_rate"} & set(row)
            assert row["currency"] == "INR"
        full = _query(stack, ws, "opportunity", {})["rows"]
        assert {"amount", "amount_in_inr"} <= set(full[0])


class TestIsolation:
    def test_deleted_merged_and_foreign_rows_never_appear(self, stack, world):
        ws, other = world
        names = _names(_query(stack, ws, "company", {"limit": 200}))
        assert sorted(names) == ["Beta", "alpha", "gamma"]
        assert _names(_query(stack, other, "company", {})) == ["alpha elsewhere"]

    def test_a_favourite_of_another_workspaces_record_is_refused(self, stack, world, owner):
        ws, other = world
        foreign = _query(stack, other, "company", {})["rows"][0]["id"]
        with pytest.raises(stack.SqlError) as raised:
            _insert(
                stack,
                ws,
                owner,
                "crm_favourites",
                {
                    "user_id": owner,
                    "target_type": "record",
                    "record_type": "company",
                    "target_id": foreign,
                },
            )
        assert raised.value.code == "CRM04"

    def test_purge_removes_the_favourites(self, stack, world, owner):
        ws, _ = world
        target = _company(stack, ws, owner, "Pinned")
        _insert(
            stack,
            ws,
            owner,
            "crm_favourites",
            {
                "user_id": owner,
                "target_type": "record",
                "record_type": "company",
                "target_id": target,
            },
        )
        actor = _actor(ws, owner)
        _apply(stack, actor, [{"op": "soft_delete", "table": "crm_companies", "id": target}])
        plan = {"items": [{"table": "crm_companies", "id": target}]}
        stack.sql(f"SELECT crm_purge({_j(stack, actor)}, {_j(stack, plan)})")
        left = stack.sql(
            f"SELECT count(*) AS n FROM crm_favourites WHERE target_id = {stack.q(target)}"
        )
        assert left[0]["n"] == 0


class TestRegistryAndSeeds:
    def test_the_registry_is_pythons(self, stack, monkeypatch):
        monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
        found = {
            (r["object"], r["field"]): (
                r["column_sql"],
                r["type"],
                r["is_amount"],
                r["filterable"],
                r["sortable"],
                r["groupable"],
                r["aggregatable"],
                r["workspace_scope"],
            )
            for r in store.list_object_fields()
        }
        expected = {
            (kind, f.field): (
                f.column_sql,
                f.type,
                f.is_amount,
                f.filterable,
                f.sortable,
                f.groupable,
                f.aggregatable,
                f.workspace_scope,
            )
            for kind, fields in objects.VIEW_FIELDS.items()
            for f in fields
        }
        assert found == expected

    def test_both_workspaces_have_the_same_seeded_views(self, stack):
        rows = stack.sql(
            "SELECT w.key, v.system_key, v.object, v.kind, v.visibility, v.is_default, v.config "
            "FROM crm_saved_views v JOIN crm_workspaces w ON w.id = v.workspace_id "
            "WHERE w.key IN ('labs', 'academy') AND v.system_key IS NOT NULL"
        )
        by = {}
        for r in rows:
            by.setdefault(r["key"], {})[r["system_key"]] = (
                r["object"],
                r["kind"],
                r["visibility"],
                r["is_default"],
                r["config"],
            )
        assert set(by["labs"]) == {
            "companies_all",
            "lead_funnel",
            "partners_channels",
            "my_accounts",
            "pipeline",
            "closing_this_month",
            "won_lost_quarter",
            "open_tasks_by_owner",
            "my_week",
        }
        assert by["labs"] == by["academy"]
        for key, (kind, *_, config) in by["labs"].items():
            for linked in (True, False):
                found = rules.validate_spec(kind, config, role="admin", tva_linked=linked)
                assert found is None, key


@pytest.fixture
def through_routes(stack, monkeypatch, owner):
    """The real routes over the local database, signed in as `owner`, an
    admin of both seeded workspaces."""
    local = create_client(stack.url, stack.service_key)
    monkeypatch.setattr(db, "supabase", local)
    monkeypatch.setattr(store, "supabase", local)
    monkeypatch.setattr(deps, "_verify", lambda token: token)
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"SELECT id, {stack.q(owner)}, 'admin' FROM crm_workspaces WHERE key IN ('labs', 'academy')",
    )
    keys = {
        r["key"]: r["id"]
        for r in stack.sql("SELECT key, id FROM crm_workspaces WHERE key IN ('labs', 'academy')")
    }
    return TestClient(app), keys


def _route_query(http, owner, key, obj, view) -> dict:
    r = http.post(
        f"/api/v1/crm/query?workspace={key}",
        json={"object": obj, "view": view, "spec": {"limit": 200}},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ids(answer) -> set[str]:
    return {r["id"] for r in answer["rows"]}


class TestSeededViewsThroughTheRoute:
    """Tasks S6a "done when": the seeded views return the right rows in both
    workspaces through POST /crm/query."""

    @pytest.mark.parametrize("key", ["labs", "academy"])
    def test_each_view(self, stack, through_routes, owner, make_profile, key):
        http, workspaces = through_routes
        ws = workspaces[key]
        someone = make_profile(keep=True)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(ws)}, {stack.q(someone)}, 'member')",
        )
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        first, last = rules.month_bounds(today)
        q_first, _ = rules.quarter_bounds(today)
        tag = uuid.uuid4().hex[:8]
        channel = stack.sql(
            f"SELECT id FROM crm_select_options WHERE workspace_id = {stack.q(ws)} "
            "AND list_key = 'channel' ORDER BY position LIMIT 1"
        )[0]["id"]
        reason = stack.sql(
            f"SELECT id FROM crm_select_options WHERE workspace_id = {stack.q(ws)} "
            "AND list_key = 'opportunity.lost_reason' ORDER BY position LIMIT 1"
        )[0]["id"]

        mine = _company(stack, ws, owner, f"Mine {tag}", lead_stage="qualified")
        theirs = _company(stack, ws, someone, f"Theirs {tag}")
        partner = _company(
            stack, ws, someone, f"Partner {tag}", lead_stage="partner", channel_ids=[channel]
        )
        soon = _deal(
            stack, ws, owner, mine, f"Soon {tag}", 1000, 10, expected_close_on=last.isoformat()
        )
        later = _deal(
            stack,
            ws,
            owner,
            mine,
            f"Later {tag}",
            1000,
            10,
            expected_close_on=(last + timedelta(days=40)).isoformat(),
        )
        won = _deal(
            stack,
            ws,
            owner,
            mine,
            f"Won {tag}",
            1000,
            100,
            stage="closed_won",
            expected_close_on=first.isoformat(),
            won_on=q_first.isoformat(),
            next_step=None,
            next_step_on=None,
        )
        lost = _deal(
            stack,
            ws,
            owner,
            mine,
            f"Lost {tag}",
            1000,
            0,
            stage="closed_lost",
            lost_reason_id=reason,
            lost_on=(q_first - timedelta(days=1)).isoformat(),
            next_step=None,
            next_step_on=None,
        )
        tasks = {}
        for name, who, status, bucket in (
            ("open-mine", owner, "todo", "today"),
            ("open-theirs", someone, "todo", None),
            ("done-mine", owner, "done", None),
        ):
            done = _apply(
                stack,
                _actor(ws, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_tasks",
                        "ref": "t",
                        "values": {
                            "title": f"{name} {tag}",
                            "assignee_id": who,
                            "status": status,
                            "bucket": bucket,
                            "completed_at": "2026-10-01T00:00:00Z" if status == "done" else None,
                        },
                    },
                    {
                        "op": "insert",
                        "table": "crm_task_links",
                        "values": {
                            "task_id": "$ref:t",
                            "record_type": "company",
                            "record_id": mine,
                        },
                    },
                ],
            )
            tasks[name] = done[0]["id"]

        get = lambda obj, view: _route_query(http, owner, key, obj, view)  # noqa: E731
        everything = _ids(get("companies", "companies_all"))
        assert {mine, theirs, partner} <= everything
        assert _ids(get("companies", "my_accounts")) & {mine, theirs, partner} == {mine}
        funnel = _ids(get("companies", "lead_funnel"))
        assert {mine, theirs} <= funnel and partner not in funnel
        partners = get("companies", "partners_channels")
        assert partner in _ids(partners) and not {mine, theirs} & _ids(partners)
        pipeline = get("opportunities", "pipeline")
        assert {soon, later} <= _ids(pipeline) and not {won, lost} & _ids(pipeline)
        assert "sum_amount_in_inr" in pipeline["groups"][0]["aggregates"]
        closing = _ids(get("opportunities", "closing_this_month"))
        assert soon in closing and not {later, won, lost} & closing
        quarter = get("opportunities", "won_lost_quarter")
        assert won in _ids(quarter) and not {lost, soon, later} & _ids(quarter)
        by_owner = _ids(get("tasks", "open_tasks_by_owner"))
        assert {tasks["open-mine"], tasks["open-theirs"]} <= by_owner
        assert tasks["done-mine"] not in by_owner
        week = _ids(get("tasks", "my_week"))
        assert {tasks["open-mine"], tasks["done-mine"]} <= week
        assert tasks["open-theirs"] not in week

    def test_a_viewer_opens_the_pipeline_without_its_sums(
        self, stack, through_routes, make_profile
    ):
        http, workspaces = through_routes
        viewer = make_profile(keep=True)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(workspaces['academy'])}, {stack.q(viewer)}, 'viewer')",
        )
        answer = _route_query(http, viewer, "academy", "opportunities", "pipeline")
        assert answer["notice"] == "A condition on an amount field was removed"
        for group in answer.get("groups") or []:
            assert set(group.get("aggregates", {})) <= {"count"}
        for row in answer["rows"]:
            assert "amount" not in row and "amount_in_inr" not in row

    def test_a_cursor_round_trips(self, stack, through_routes, owner):
        http, workspaces = through_routes
        for n in range(3):
            _company(stack, workspaces["academy"], owner, f"Page {n} {uuid.uuid4().hex[:6]}")
        seen: list[str] = []
        body = {
            "object": "companies",
            "spec": {"limit": 2, "sorts": [{"field": "name", "dir": "asc"}]},
        }
        for _ in range(500):
            r = http.post(
                "/api/v1/crm/query?workspace=academy",
                json=body,
                headers={"Authorization": f"Bearer {owner}"},
            )
            assert r.status_code == 200, r.text
            seen += [row["id"] for row in r.json()["rows"]]
            if not r.json()["next"]:
                break
            body["spec"]["after"] = r.json()["next"]
            json.loads(base64.urlsafe_b64decode(r.json()["next"] + "=="))
        assert len(seen) == len(set(seen))
