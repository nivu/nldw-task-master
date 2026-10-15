"""Search in the real database — spec 007 FR-CRM-SRCH-01 to -03,
FR-CRM-ACC-11, NFR-CRM-01; data-model §5.9; migration 035.

What only Postgres can prove: `crm_search` ranks by trigram similarity, a
`%` or `_` in the query is matched literally rather than as a wildcard, a
Viewer's deal hits read no amount, the Trash and merged companies are never
found, and a search over ten thousand companies answers well under a second
on a laptop (a smoke check; the full performance check is S12).
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

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


def _search(stack, ws: str, q: str, *, redact: bool = False, limit: int = 8) -> dict:
    return stack.sql(
        f"SELECT crm_search({stack.q(ws)}, {stack.q(q)}, {limit}, {str(redact).lower()}) AS r"
    )[0]["r"]


def _new_workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def workspace(stack, owner) -> str:
    ws = _new_workspace(stack)
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


def _companies(stack, ws, owner, *names, **values) -> list[str]:
    ops = [
        {
            "op": "insert",
            "table": "crm_companies",
            "values": {"name": n, "owner_id": owner, **values},
        }
        for n in names
    ]
    return [r["id"] for r in _apply(stack, _actor(ws, owner), ops)]


def _names(hits: list[dict]) -> list[str]:
    return [h["name"] for h in hits]


class TestRanking:
    def test_the_closest_name_first(self, stack, workspace, owner):
        _companies(stack, workspace, owner, "Northwind Traders", "Northwind", "Northwinds Ltd")
        found = _search(stack, workspace, "northwind")
        assert _names(found["companies"])[0] == "Northwind"
        assert set(_names(found["companies"])) == {
            "Northwind",
            "Northwinds Ltd",
            "Northwind Traders",
        }

    def test_a_typo_still_finds_it(self, stack, workspace, owner):
        _companies(stack, workspace, owner, "Contoso Pharmaceuticals")
        assert _names(_search(stack, workspace, "contoso pharma")["companies"]) == [
            "Contoso Pharmaceuticals"
        ]

    def test_another_workspace_is_never_searched(self, stack, workspace, owner):
        other = _new_workspace(stack)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(other)}, {stack.q(owner)}, 'admin')",
        )
        _companies(stack, other, owner, "Fabrikam Elsewhere")
        assert _search(stack, workspace, "fabrikam")["companies"] == []


class TestLiteralWildcards:
    def test_percent_is_matched_literally(self, stack, workspace, owner):
        """Unescaped, `%ab` would be "anything, then ab" and find the crab."""
        _companies(stack, workspace, owner, "%ab Studio", "Crab Shack")
        assert _names(_search(stack, workspace, "%ab")["companies"]) == ["%ab Studio"]

    def test_underscore_is_matched_literally(self, stack, workspace, owner):
        _companies(stack, workspace, owner, "ab_cd Labs", "abxcd Labs")
        hits = _names(_search(stack, workspace, "ab_c")["companies"])
        assert "ab_cd Labs" in hits and "abxcd Labs" not in hits


class TestWhatIsNeverFound:
    def test_the_trash_and_merged_companies(self, stack, workspace, owner):
        live, trashed = _companies(stack, workspace, owner, "Tailspin Live", "Tailspin Gone")
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "soft_delete",
                    "table": "crm_companies",
                    "id": trashed,
                    "deleted_group": str(uuid.uuid4()),
                }
            ],
        )
        assert _names(_search(stack, workspace, "tailspin")["companies"]) == ["Tailspin Live"]

    def test_a_viewers_deal_hits_read_no_amount(self, stack, workspace, owner):
        [company] = _companies(stack, workspace, owner, "Woodgrove", lead_stage="deal_open")
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_opportunities",
                    "values": {
                        "name": "Woodgrove renewal",
                        "company_id": company,
                        "owner_id": owner,
                        "probability": 10,
                        "next_step": "Call",
                        "next_step_on": "2026-10-20",
                    },
                }
            ],
        )
        [hit] = _search(stack, workspace, "woodgrove", redact=True)["opportunities"]
        assert "amount" not in hit and "amount_in_inr" not in hit
        [full] = _search(stack, workspace, "woodgrove")["opportunities"]
        assert "amount" in full

    def test_one_character_is_refused(self, stack, workspace):
        with pytest.raises(stack.SqlError) as raised:
            _search(stack, workspace, "a")
        assert raised.value.code == "CRM22"


class TestSpeed:
    def test_ten_thousand_companies_under_a_second(self, stack, workspace, owner):
        _as_system(
            stack,
            "INSERT INTO crm_companies (workspace_id, name, owner_id) "
            f"SELECT {stack.q(workspace)}, 'Seeded company ' || md5(g::text), {stack.q(owner)} "
            "FROM generate_series(1, 10000) AS g",
        )
        stack.sql("ANALYZE crm_companies")
        _search(stack, workspace, "seeded")
        started = time.perf_counter()
        found = _search(stack, workspace, "seeded company 4f")
        elapsed = time.perf_counter() - started
        assert len(found["companies"]) == 8
        assert elapsed < 1.0, f"{elapsed:.2f}s"
