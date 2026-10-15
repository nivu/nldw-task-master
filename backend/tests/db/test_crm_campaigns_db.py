"""Campaigns in the real database — spec 007 FR-CRM-CAMP-01, -04, -05,
FR-CRM-WS-02, FR-CRM-DUP-03, FR-CRM-DEL-01, FR-CRM-SRCH-01, FR-CRM-FX-03;
data-model §3.23, §5.4, §5.7, §5.8, §5.9; migration 041.

What only Postgres can prove: a member of workspace A pointing at a
campaign, company, person or deal of B is refused by the composite foreign
keys whatever the caller sends; a member names a company or a person, once
per campaign, and only a converted one has a deal; the budget's FX CHECKs;
`crm_merge_companies` moves campaign members to the survivor (one the
survivor already has keeps the survivor's); `crm_purge` keeps a membership
that still names the other record; `crm_search` returns a campaign, and
`crm_query` lists members in the board's status order; and quickstart
§3 S11 through the real routes (journey 4.9).

Each test works in workspaces it creates, and asserts only on rows it made:
the local database keeps leftovers between runs.
"""

from __future__ import annotations

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


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _refused(stack, query: str, *codes: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code in codes, raised.value.message
    return raised.value.message


def _apply_sql(stack, actor: dict, ops: list[dict]) -> str:
    return f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r"


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


def _records(stack, ws: str, owner: str) -> dict[str, str]:
    """A company, a person, a deal and a campaign, all of `ws`."""
    t = uuid.uuid4().hex[:8]
    done = _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_companies",
                "ref": "c",
                "values": {"name": f"College {t}", "owner_id": owner, "lead_stage": "qualified"},
            },
            {
                "op": "insert",
                "table": "crm_people",
                "values": {"first_name": f"Priya {t}", "company_id": "$ref:c", "owner_id": owner},
            },
            {
                "op": "insert",
                "table": "crm_opportunities",
                "values": {
                    "name": f"Course {t}",
                    "company_id": "$ref:c",
                    "owner_id": owner,
                    "probability": 10,
                    "next_step": "Call",
                    "next_step_on": "2026-10-20",
                },
            },
            {
                "op": "insert",
                "table": "crm_campaigns",
                "values": {"name": f"Webinars {t}", "owner_id": owner},
            },
        ],
    )
    return {
        "company": done[0]["id"],
        "person": done[1]["id"],
        "deal": done[2]["id"],
        "campaign": done[3]["id"],
        "tag": t,
    }


@pytest.fixture
def two(stack, owner):
    a, b = _new_workspace(stack, owner), _new_workspace(stack, owner)
    return {"a": a, "b": b, "ra": _records(stack, a, owner), "rb": _records(stack, b, owner)}


def _member(stack, ws: str, owner: str, **values) -> list[dict]:
    return _apply(
        stack,
        _actor(ws, owner),
        [{"op": "insert", "table": "crm_campaign_members", "values": values}],
    )


class TestIsolation:
    """FR-CRM-WS-02 — the composite foreign keys of data-model §2."""

    @pytest.mark.parametrize("column", ["company_id", "person_id", "converted_opportunity_id"])
    def test_a_member_in_a_naming_a_record_of_b_is_refused(self, stack, two, owner, column):
        source = {
            "company_id": "company",
            "person_id": "person",
            "converted_opportunity_id": "deal",
        }
        values = {
            "campaign_id": two["ra"]["campaign"],
            "company_id": two["ra"]["company"],
            column: two["rb"][source[column]],
        }
        if column == "converted_opportunity_id":
            values["status"] = "converted"
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(two["a"], owner),
                [{"op": "insert", "table": "crm_campaign_members", "values": values}],
            ),
            "23503",
        )

    def test_a_member_of_a_campaign_of_b_is_refused(self, stack, two, owner):
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(two["a"], owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_campaign_members",
                        "values": {
                            "campaign_id": two["rb"]["campaign"],
                            "company_id": two["ra"]["company"],
                        },
                    }
                ],
            ),
            "23503",
        )

    def test_a_deals_source_campaign_of_b_is_refused(self, stack, two, owner):
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(two["a"], owner),
                [
                    {
                        "op": "update",
                        "table": "crm_opportunities",
                        "id": two["ra"]["deal"],
                        "values": {"source_campaign_id": two["rb"]["campaign"]},
                    }
                ],
            ),
            "23503",
        )


class TestMemberRules:
    """FR-CRM-CAMP-01, -04; data-model §3.23."""

    def test_a_member_names_a_company_or_a_person(self, stack, two, owner):
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(two["a"], owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_campaign_members",
                        "values": {"campaign_id": two["ra"]["campaign"]},
                    }
                ],
            ),
            "23514",
        )

    def test_once_per_campaign_nulls_not_distinct(self, stack, two, owner):
        r = two["ra"]
        _member(stack, two["a"], owner, campaign_id=r["campaign"], company_id=r["company"])
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(two["a"], owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_campaign_members",
                        "values": {"campaign_id": r["campaign"], "company_id": r["company"]},
                    }
                ],
            ),
            "23505",
        )

    def test_only_a_converted_member_has_a_deal(self, stack, two, owner):
        r = two["ra"]
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(two["a"], owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_campaign_members",
                        "values": {
                            "campaign_id": r["campaign"],
                            "person_id": r["person"],
                            "status": "engaged",
                            "converted_opportunity_id": r["deal"],
                        },
                    }
                ],
            ),
            "23514",
        )

    def test_the_budget_fx_checks(self, stack, two, owner):
        actor = _actor(two["a"], owner)
        for values in (
            {"budget": "100", "budget_currency": "USD", "budget_in_inr": "8400"},
            {
                "budget": "100",
                "budget_currency": "USD",
                "budget_in_inr": "8300",
                "budget_fx_rate": "84",
            },
            {
                "budget": "100",
                "budget_currency": "INR",
                "budget_in_inr": "8400",
                "budget_fx_rate": "84",
            },
        ):
            _refused(
                stack,
                _apply_sql(
                    stack,
                    actor,
                    [
                        {
                            "op": "insert",
                            "table": "crm_campaigns",
                            "values": {"name": "Budgeted", "owner_id": owner, **values},
                        }
                    ],
                ),
                "23514",
            )
        made = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_campaigns",
                    "values": {
                        "name": "Budgeted",
                        "owner_id": owner,
                        "budget": "100",
                        "budget_currency": "USD",
                        "budget_in_inr": "8400.00",
                        "budget_fx_rate": "84",
                    },
                }
            ],
        )
        assert made[0]["row"]["budget_in_inr"] == 8400.00


class TestMerge:
    """FR-CRM-DUP-03 — 041's crm_merge_companies moves campaign members."""

    def test_members_move_to_the_survivor(self, stack, two, owner):
        ws, r = two["a"], two["ra"]
        actor = _actor(ws, owner)
        gone = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": f"College dup {r['tag']}", "owner_id": owner},
                }
            ],
        )[0]["id"]
        second = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_campaigns",
                    "values": {"name": f"Outreach {r['tag']}", "owner_id": owner},
                }
            ],
        )[0]["id"]
        # In the first campaign both companies are members: the survivor's
        # stays and the merged one's goes. In the second only the merged one.
        _member(stack, ws, owner, campaign_id=r["campaign"], company_id=r["company"])
        dropped = _member(stack, ws, owner, campaign_id=r["campaign"], company_id=gone)[0]["id"]
        moved = _member(stack, ws, owner, campaign_id=second, company_id=gone)[0]["id"]
        keep_row = stack.sql(
            f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(r['company'])}"
        )
        gone_row = stack.sql(f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(gone)}")
        plan = {
            "survivor_id": r["company"],
            "merged_id": gone,
            "expect": {
                "survivor_updated_at": keep_row[0]["updated_at"],
                "merged_updated_at": gone_row[0]["updated_at"],
            },
            "survivor_changes": {},
        }
        result = stack.sql(
            f"SELECT crm_merge_companies({_j(stack, actor)}, {_j(stack, plan)}) AS r"
        )[0]["r"]
        assert result["moved"]["campaign_members"] == 2
        rows = stack.sql(
            "SELECT id, campaign_id, company_id FROM crm_campaign_members "
            f"WHERE id IN ({stack.q(dropped)}, {stack.q(moved)})"
        )
        assert rows == [{"id": moved, "campaign_id": second, "company_id": r["company"]}]

    def test_a_converted_membership_of_the_merged_company_is_kept(self, stack, two, owner):
        """042: the survivor's Targeted membership goes and the merged
        company's Converted one moves, deal and all (FR-CRM-CAMP-05)."""
        ws, r = two["a"], two["ra"]
        actor = _actor(ws, owner)
        gone = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": f"College dup {r['tag']}", "owner_id": owner},
                }
            ],
        )[0]["id"]
        targeted = _member(stack, ws, owner, campaign_id=r["campaign"], company_id=r["company"])[0][
            "id"
        ]
        converted = _member(
            stack,
            ws,
            owner,
            campaign_id=r["campaign"],
            company_id=gone,
            status="converted",
            converted_opportunity_id=r["deal"],
        )[0]["id"]
        plan = {"survivor_id": r["company"], "merged_id": gone, "survivor_changes": {}}
        stack.sql(f"SELECT crm_merge_companies({_j(stack, actor)}, {_j(stack, plan)}) AS r")
        rows = stack.sql(
            "SELECT id, company_id, status, converted_opportunity_id FROM crm_campaign_members "
            f"WHERE id IN ({stack.q(targeted)}, {stack.q(converted)})"
        )
        assert rows == [
            {
                "id": converted,
                "company_id": r["company"],
                "status": "converted",
                "converted_opportunity_id": r["deal"],
            }
        ]


class TestPurge:
    """FR-CRM-DEL-01 — 041's crm_purge and the members' foreign keys."""

    def test_a_purged_person_leaves_the_membership_with_its_company(self, stack, two, owner):
        ws, r = two["a"], two["ra"]
        actor = _actor(ws, owner)
        both = _member(
            stack,
            ws,
            owner,
            campaign_id=r["campaign"],
            company_id=r["company"],
            person_id=r["person"],
        )[0]["id"]
        alone = _member(stack, ws, owner, campaign_id=r["campaign"], person_id=r["person"])[0]["id"]
        _apply(
            stack,
            actor,
            [
                {
                    "op": "soft_delete",
                    "table": "crm_people",
                    "id": r["person"],
                    "deleted_group": str(uuid.uuid4()),
                }
            ],
        )
        stack.sql(
            f"SELECT crm_purge({_j(stack, actor)}, "
            f"{_j(stack, {'items': [{'table': 'crm_people', 'id': r['person']}]})})"
        )
        rows = stack.sql(
            "SELECT id, company_id, person_id FROM crm_campaign_members "
            f"WHERE id IN ({stack.q(both)}, {stack.q(alone)})"
        )
        assert rows == [{"id": both, "company_id": r["company"], "person_id": None}]

    def test_a_purged_company_keeps_a_converted_shared_membership(self, stack, two, owner):
        """042: the person's own Targeted membership goes and the Converted
        one naming both stays with the person (FR-CRM-CAMP-05)."""
        ws, r = two["a"], two["ra"]
        actor = _actor(ws, owner)
        # A company with no deal of its own, so nothing holds its purge back.
        gone = _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": f"Partner {r['tag']}", "owner_id": owner},
                }
            ],
        )[0]["id"]
        both = _member(
            stack,
            ws,
            owner,
            campaign_id=r["campaign"],
            company_id=gone,
            person_id=r["person"],
            status="converted",
            converted_opportunity_id=r["deal"],
        )[0]["id"]
        alone = _member(stack, ws, owner, campaign_id=r["campaign"], person_id=r["person"])[0]["id"]
        _apply(
            stack,
            actor,
            [
                {
                    "op": "soft_delete",
                    "table": "crm_companies",
                    "id": gone,
                    "deleted_group": str(uuid.uuid4()),
                }
            ],
        )
        stack.sql(
            f"SELECT crm_purge({_j(stack, actor)}, "
            f"{_j(stack, {'items': [{'table': 'crm_companies', 'id': gone}]})})"
        )
        rows = stack.sql(
            "SELECT id, company_id, person_id, status FROM crm_campaign_members "
            f"WHERE id IN ({stack.q(both)}, {stack.q(alone)})"
        )
        assert rows == [
            {"id": both, "company_id": None, "person_id": r["person"], "status": "converted"}
        ]

    def test_a_purged_person_keeps_a_converted_shared_membership(self, stack, two, owner):
        ws, r = two["a"], two["ra"]
        actor = _actor(ws, owner)
        both = _member(
            stack,
            ws,
            owner,
            campaign_id=r["campaign"],
            company_id=r["company"],
            person_id=r["person"],
            status="converted",
            converted_opportunity_id=r["deal"],
        )[0]["id"]
        alone = _member(stack, ws, owner, campaign_id=r["campaign"], company_id=r["company"])[0][
            "id"
        ]
        _apply(
            stack,
            actor,
            [
                {
                    "op": "soft_delete",
                    "table": "crm_people",
                    "id": r["person"],
                    "deleted_group": str(uuid.uuid4()),
                }
            ],
        )
        stack.sql(
            f"SELECT crm_purge({_j(stack, actor)}, "
            f"{_j(stack, {'items': [{'table': 'crm_people', 'id': r['person']}]})})"
        )
        rows = stack.sql(
            "SELECT id, company_id, person_id, status, converted_opportunity_id "
            f"FROM crm_campaign_members WHERE id IN ({stack.q(both)}, {stack.q(alone)})"
        )
        assert rows == [
            {
                "id": both,
                "company_id": r["company"],
                "person_id": None,
                "status": "converted",
                "converted_opportunity_id": r["deal"],
            }
        ]

    def test_a_purged_campaign_takes_its_members_and_clears_the_source(self, stack, two, owner):
        ws, r = two["a"], two["ra"]
        actor = _actor(ws, owner)
        member = _member(
            stack,
            ws,
            owner,
            campaign_id=r["campaign"],
            company_id=r["company"],
            status="converted",
            converted_opportunity_id=r["deal"],
        )[0]["id"]
        _apply(
            stack,
            actor,
            [
                {
                    "op": "update",
                    "table": "crm_opportunities",
                    "id": r["deal"],
                    "values": {"source_campaign_id": r["campaign"]},
                },
                {
                    "op": "soft_delete",
                    "table": "crm_campaigns",
                    "id": r["campaign"],
                    "deleted_group": str(uuid.uuid4()),
                },
            ],
        )
        stack.sql(
            f"SELECT crm_purge({_j(stack, actor)}, "
            f"{_j(stack, {'items': [{'table': 'crm_campaigns', 'id': r['campaign']}]})})"
        )
        assert stack.sql(f"SELECT id FROM crm_campaign_members WHERE id = {stack.q(member)}") == []
        deal = stack.sql(
            f"SELECT source_campaign_id FROM crm_opportunities WHERE id = {stack.q(r['deal'])}"
        )
        assert deal == [{"source_campaign_id": None}]


class TestReads:
    """FR-CRM-SRCH-01 (1b), FR-CRM-VIEW-11 (1b)."""

    def test_crm_search_returns_a_campaign(self, stack, two):
        r = two["ra"]
        found = stack.sql(
            f"SELECT crm_search({stack.q(two['a'])}, {stack.q('webinars ' + r['tag'])}, 8, true) AS r"
        )[0]["r"]
        assert [c["id"] for c in found["campaigns"]] == [r["campaign"]]
        assert not {"budget", "budget_in_inr", "budget_fx_rate"} & set(found["campaigns"][0])
        elsewhere = stack.sql(
            f"SELECT crm_search({stack.q(two['b'])}, {stack.q('webinars ' + r['tag'])}, 8, false) AS r"
        )[0]["r"]
        assert r["campaign"] not in [c["id"] for c in elsewhere["campaigns"]]

    def test_crm_query_groups_members_in_board_order(self, stack, two, owner):
        ws, r = two["a"], two["ra"]
        _member(
            stack, ws, owner, campaign_id=r["campaign"], company_id=r["company"], status="attended"
        )
        _member(
            stack, ws, owner, campaign_id=r["campaign"], person_id=r["person"], status="invited"
        )
        spec = {
            "filters": {
                "op": "and",
                "items": [{"field": "campaign_id", "operator": "is", "value": r["campaign"]}],
            },
            "group_by": "status",
            "sorts": [{"field": "position", "dir": "asc"}],
        }
        found = stack.sql(
            f"SELECT crm_query({stack.q(ws)}, 'campaign_member', {_j(stack, spec)}, false) AS r"
        )[0]["r"]
        assert [g["key"] for g in found["groups"]] == ["invited", "attended"]
        assert [row["status"] for row in found["rows"]] == ["invited", "attended"]

    def test_a_viewer_cannot_query_the_budget(self, stack, two):
        spec = {"sorts": [{"field": "budget", "dir": "asc"}]}
        _refused(
            stack,
            f"SELECT crm_query({stack.q(two['a'])}, 'campaign', {_j(stack, spec)}, true)",
            "CRM22",
        )


class TestJourneyThroughTheRoutes:
    """Quickstart §3 S11, journey 4.9, over the local database: a campaign,
    members added from a saved Companies view, one converted to a new deal
    that shows the campaign as its source."""

    def test_add_from_a_view_and_convert(self, stack, monkeypatch, owner):
        local = create_client(stack.url, stack.service_key)
        monkeypatch.setattr(db, "supabase", local)
        monkeypatch.setattr(store, "supabase", local)
        monkeypatch.setattr(deps, "_verify", lambda token: token)
        ws = _new_workspace(stack, owner)
        key = stack.sql(f"SELECT key FROM crm_workspaces WHERE id = {stack.q(ws)}")[0]["key"]
        http = TestClient(app)
        me = {"Authorization": f"Bearer {owner}"}
        q = f"?workspace={key}"
        tag = uuid.uuid4().hex[:8]

        def call(method, path, body=None):
            r = http.request(method, f"/api/v1/crm{path}{q}", json=body, headers=me)
            assert r.status_code in (200, 201), r.text
            return r.json()

        campaign = call("POST", "/campaigns", {"name": f"Webinars {tag}"})
        college = call("POST", "/companies", {"name": f"College {tag}"})
        call("PATCH", f"/companies/{college['id']}", {"lead_stage": "qualified"})
        call("POST", "/companies", {"name": f"Second {tag}"})
        view = call(
            "POST",
            "/views",
            {
                "object": "company",
                "name": f"Tagged {tag}",
                "visibility": "private",
                "config": {
                    "filters": {
                        "op": "and",
                        "items": [{"field": "name", "operator": "contains", "value": tag}],
                    }
                },
            },
        )
        added = call("POST", f"/campaigns/{campaign['id']}/members", {"view_id": view["id"]})
        assert (added["added"], added["skipped_existing"]) == (2, 0)

        board = call("GET", f"/campaigns/{campaign['id']}/members")
        member = next(m for m in board["rows"] if m["company_id"] == college["id"])
        moved = call("PATCH", f"/campaign-members/{member['id']}", {"status": "registered"})
        assert moved["status"] == "registered"
        done = call(
            "POST",
            f"/campaign-members/{member['id']}/convert",
            {"new": {"name": f"Course {tag}", "next_step": "Call", "next_step_on": "2026-12-01"}},
        )
        assert done["member"]["status"] == "converted"
        deal = call("GET", f"/opportunities/{done['opportunity']['id']}")["record"]
        assert deal["source_campaign_id"] == campaign["id"]
        assert deal["source_campaign_name"] == f"Webinars {tag}"
        page = call("GET", f"/campaigns/{campaign['id']}")["record"]
        assert (page["member_count"], page["converted_deals"], page["conversion_rate"]) == (
            2,
            1,
            "50.0",
        )
