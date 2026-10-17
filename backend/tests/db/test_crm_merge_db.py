"""Company merge in the real database — spec 007 FR-CRM-DUP-03, -04,
FR-CRM-AUD-04; data-model §5.7; migration 035.

What only Postgres can prove: `crm_merge_companies` moves everything in one
transaction (a refusal part-way writes nothing); every domain of both lands
on the survivor with one primary; the merged id resolves to the survivor
through `merged_into_id`; a TVA project linked to the merged company is
linked to the survivor with a TVA audit row; and the function trusts no id
in the plan: a company of another workspace, a deleted or already-merged
company, or the same id twice is refused and writes nothing.
"""

from __future__ import annotations

import json
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


def _merge_sql(stack, actor: dict, plan: dict) -> str:
    return f"SELECT crm_merge_companies({_j(stack, actor)}, {_j(stack, plan)}) AS r"


def _merge(stack, actor: dict, plan: dict) -> dict:
    return stack.sql(_merge_sql(stack, actor, plan))[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _member(stack, workspace: str, user: str, role: str = "admin") -> None:
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(user)}, {stack.q(role)}) "
        "ON CONFLICT DO NOTHING",
    )


def _new_workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]


@pytest.fixture
def owner(make_profile) -> str:
    """A staff profile that acts (and so is kept, see conftest)."""
    return make_profile(keep=True)


@pytest.fixture
def labs(stack, owner) -> str:
    """The seeded TVA-linked workspace: the only one projects link to."""
    ws = stack.sql("SELECT id FROM crm_workspaces WHERE tva_linked")[0]["id"]
    _member(stack, ws, owner)
    return ws


@pytest.fixture
def other(stack, owner) -> str:
    ws = _new_workspace(stack)
    _member(stack, ws, owner)
    return ws


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(stack, ws, owner, name, domains=(), **values) -> str:
    ops = [
        {
            "op": "insert",
            "table": "crm_companies",
            "ref": "c",
            "values": {"name": name, "owner_id": owner, **values},
        }
    ]
    ops += [
        {
            "op": "insert",
            "table": "crm_company_domains",
            "values": {"company_id": "$ref:c", "domain": d, "is_primary": n == 0},
        }
        for n, d in enumerate(domains)
    ]
    return _apply(stack, _actor(ws, owner), ops)[0]["id"]


def _row(stack, table: str, row_id: str) -> dict:
    return stack.sql(f"SELECT * FROM {table} WHERE id = {stack.q(row_id)}")[0]


def _plan(stack, keep: str, gone: str, **extra) -> dict:
    return {
        "survivor_id": keep,
        "merged_id": gone,
        "expect": {
            "survivor_updated_at": _row(stack, "crm_companies", keep)["updated_at"],
            "merged_updated_at": _row(stack, "crm_companies", gone)["updated_at"],
        },
        "survivor_changes": {},
        **extra,
    }


@pytest.fixture
def pair(stack, labs, owner):
    """Two Labs companies; everything that can move hangs off the second."""
    t = _tag()
    keep = _company(stack, labs, owner, f"Acme {t}", (f"acme-{t}.com",), lead_stage="deal_open")
    gone = _company(
        stack,
        labs,
        owner,
        f"Acme Corp {t}",
        (f"acmecorp-{t}.com", f"acmecorp-{t}.in"),
        lead_stage="deal_open",
        city="Bengaluru",
    )
    actor = _actor(labs, owner)
    made = _apply(
        stack,
        actor,
        [
            {
                "op": "insert",
                "table": "crm_people",
                "values": {"first_name": f"Priya {t}", "company_id": gone, "owner_id": owner},
            },
            {
                "op": "insert",
                "table": "crm_opportunities",
                "values": {
                    "name": f"Deal {t}",
                    "company_id": gone,
                    "owner_id": owner,
                    "probability": 10,
                    "next_step": "Call",
                    "next_step_on": "2026-10-20",
                },
            },
            {
                "op": "insert",
                "table": "tasks",
                "ref": "t",
                "values": {"title": f"Call {t}", "assignee_id": owner, "bucket": "later"},
            },
            {
                "op": "insert",
                "table": "task_links",
                "values": {"task_id": "$ref:t", "record_type": "company", "record_id": gone},
            },
            # Linked to both: keeps one link, the survivor's.
            {
                "op": "insert",
                "table": "task_links",
                "values": {"task_id": "$ref:t", "record_type": "company", "record_id": keep},
            },
            {
                "op": "insert",
                "table": "crm_notes",
                "ref": "n",
                "values": {"body": f"Met at the expo {t}."},
            },
            {
                "op": "insert",
                "table": "crm_note_links",
                "values": {"note_id": "$ref:n", "record_type": "company", "record_id": gone},
            },
            {
                "op": "insert",
                "table": "crm_drive_links",
                "values": {
                    "record_type": "company",
                    "record_id": gone,
                    "url": "https://drive.google.com/x",
                    "label": "NDA",
                },
            },
        ],
    )
    ids = {
        "person": made[0]["id"],
        "deal": made[1]["id"],
        "task": made[2]["id"],
        "note": made[5]["id"],
        "link": made[7]["id"],
    }
    project = stack.sql(
        "INSERT INTO projects (name, status, crm_company_id) "
        f"VALUES ({stack.q('Pilot ' + t)}, 'tentative', {stack.q(gone)}) RETURNING id"
    )[0]["id"]
    # Won from the merged company's deal: it moves through 033's trigger.
    sourced = stack.sql(
        "INSERT INTO projects (name, status, client, crm_company_id, source_opportunity_id) "
        f"VALUES ({stack.q('Rollout ' + t)}, 'tentative', {stack.q('Acme Corp ' + t)}, "
        f"{stack.q(gone)}, {stack.q(ids['deal'])}) RETURNING id"
    )[0]["id"]
    own = stack.sql(
        "INSERT INTO projects (name, status, crm_company_id) "
        f"VALUES ({stack.q('Support ' + t)}, 'tentative', {stack.q(keep)}) RETURNING id"
    )[0]["id"]
    return {
        "keep": keep,
        "gone": gone,
        "actor": actor,
        "project": project,
        "sourced": sourced,
        "own": own,
        "t": t,
        **ids,
    }


def _snapshot(stack, keep: str, gone: str) -> dict:
    ids = f"({stack.q(keep)}, {stack.q(gone)})"
    return {
        "companies": stack.sql(
            f"SELECT id, name, updated_at, deleted_at, merged_into_id FROM crm_companies "
            f"WHERE id IN {ids} ORDER BY id"
        ),
        "domains": stack.sql(
            f"SELECT company_id, domain, is_primary FROM crm_company_domains "
            f"WHERE company_id IN {ids} ORDER BY domain"
        ),
        "people": stack.sql(f"SELECT id, company_id FROM crm_people WHERE company_id IN {ids}"),
        "deals": stack.sql(
            f"SELECT id, company_id FROM crm_opportunities WHERE company_id IN {ids}"
        ),
        "task_links": stack.sql(
            f"SELECT task_id, record_id FROM task_links WHERE record_id IN {ids} ORDER BY record_id"
        ),
        "projects": stack.sql(
            f"SELECT id, crm_company_id FROM projects WHERE crm_company_id IN {ids}"
        ),
        "audit": stack.sql(
            f"SELECT count(*) AS n FROM audit_log WHERE target_id IN "
            f"({stack.q(keep)}, {stack.q(gone)})"
        ),
    }


class TestTheMerge:
    def test_everything_moves_in_one_call(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        out = _merge(stack, pair["actor"], _plan(stack, keep, gone, primary_domain=None))
        assert out["moved"] == {
            "people": 1,
            "deals": 1,
            "tasks": 1,
            "notes": 1,
            "links": 1,
            "domains": 2,
            "projects": 2,
            "emails": 0,
            "campaign_members": 0,
        }
        assert _row(stack, "crm_people", pair["person"])["company_id"] == keep
        assert _row(stack, "crm_opportunities", pair["deal"])["company_id"] == keep
        assert _row(stack, "crm_drive_links", pair["link"])["record_id"] == keep
        links = stack.sql(
            f"SELECT record_id FROM task_links WHERE task_id = {stack.q(pair['task'])}"
        )
        assert links == [{"record_id": keep}]
        notes = stack.sql(
            f"SELECT record_id FROM crm_note_links WHERE note_id = {stack.q(pair['note'])}"
        )
        assert notes == [{"record_id": keep}]

    def test_the_merged_id_resolves_to_the_survivor(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        _merge(stack, pair["actor"], _plan(stack, keep, gone))
        row = _row(stack, "crm_companies", gone)
        assert row["merged_into_id"] == keep and row["deleted_at"] is not None
        resolved = stack.sql(
            "SELECT s.id FROM crm_companies m JOIN crm_companies s ON s.id = m.merged_into_id "
            f"WHERE m.id = {stack.q(gone)}"
        )
        assert resolved == [{"id": keep}]

    def test_chosen_values_and_every_domain_with_one_primary(self, stack, pair):
        keep, gone, t = pair["keep"], pair["gone"], pair["t"]
        plan = _plan(
            stack,
            keep,
            gone,
            survivor_changes={"name": f"Acme Corp {t}x", "city": "Bengaluru"},
            primary_domain=f"acmecorp-{t}.com",
        )
        _merge(stack, pair["actor"], plan)
        row = _row(stack, "crm_companies", keep)
        assert (row["name"], row["city"]) == (f"Acme Corp {t}x", "Bengaluru")
        domains = stack.sql(
            "SELECT domain, is_primary FROM crm_company_domains "
            f"WHERE company_id = {stack.q(keep)} ORDER BY domain"
        )
        assert domains == [
            {"domain": f"acme-{t}.com", "is_primary": False},
            {"domain": f"acmecorp-{t}.com", "is_primary": True},
            {"domain": f"acmecorp-{t}.in", "is_primary": False},
        ]
        # Each domain is still unique in the workspace: one row each.
        assert (
            stack.sql(
                "SELECT count(*) AS n FROM crm_company_domains "
                f"WHERE domain LIKE {stack.q('%' + t + '%')}"
            )[0]["n"]
            == 3
        )

    def test_a_tva_project_moves_with_a_tva_audit_row(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        _merge(stack, pair["actor"], _plan(stack, keep, gone))
        project = _row(stack, "projects", pair["project"])
        assert (project["crm_company_id"], project["name"]) == (keep, f"Pilot {pair['t']}")
        rows = stack.sql(
            "SELECT workspace_id, action, before, after FROM audit_log "
            f"WHERE target_table = 'projects' AND target_id = {stack.q(pair['project'])} "
            "AND action = 'project.updated'"
        )
        assert {"workspace_id": None, "action": "project.updated"}.items() <= rows[-1].items()
        assert rows[-1]["before"] == {"crm_company_id": gone}
        assert rows[-1]["after"] == {"crm_company_id": keep}

    def test_a_deal_sourced_project_takes_the_survivors_chosen_name(self, stack, pair):
        """FR-CRM-DUP-04, FR-CRM-TVA-01 — the deal's move carries its project
        to the survivor (033's trigger), after the chosen values, so the
        client is the survivor's chosen name; the survivor's own project
        stays. Each move is a TVA audit row."""
        keep, gone, t = pair["keep"], pair["gone"], pair["t"]
        plan = _plan(stack, keep, gone, survivor_changes={"name": f"Acme Group {t}"})
        _merge(stack, pair["actor"], plan)
        sourced = _row(stack, "projects", pair["sourced"])
        assert (sourced["crm_company_id"], sourced["client"]) == (keep, f"Acme Group {t}")
        assert _row(stack, "projects", pair["own"])["crm_company_id"] == keep
        for project in (pair["sourced"], pair["project"]):
            rows = stack.sql(
                "SELECT workspace_id, after FROM audit_log "
                f"WHERE target_table = 'projects' AND target_id = {stack.q(project)} "
                "AND action = 'project.updated'"
            )
            assert rows and rows[-1]["workspace_id"] is None
            assert rows[-1]["after"]["crm_company_id"] == keep
        [moved] = stack.sql(
            "SELECT after FROM audit_log WHERE target_table = 'projects' "
            f"AND target_id = {stack.q(pair['sourced'])} AND action = 'project.updated'"
        )
        assert moved["after"]["client"] == f"Acme Group {t}"

    def test_a_chained_merge_keeps_the_way_to_the_live_company(self, stack, pair, labs, owner):
        """A into B, then B into C: A points at B and B at C, which is what
        `records.get` follows to the live company (FR-CRM-DUP-03)."""
        keep, gone = pair["keep"], pair["gone"]
        _merge(stack, pair["actor"], _plan(stack, keep, gone))
        third = _company(stack, labs, owner, f"Acme Ltd {pair['t']}")
        _merge(stack, pair["actor"], _plan(stack, third, keep))
        hops = stack.sql(
            "WITH RECURSIVE chain AS ("
            f" SELECT id, merged_into_id, deleted_at, 0 AS n FROM crm_companies"
            f" WHERE id = {stack.q(gone)}"
            " UNION ALL SELECT c.id, c.merged_into_id, c.deleted_at, chain.n + 1"
            " FROM crm_companies c JOIN chain ON c.id = chain.merged_into_id)"
            " SELECT id, deleted_at IS NULL AS live FROM chain ORDER BY n"
        )
        assert hops == [
            {"id": gone, "live": False},
            {"id": keep, "live": False},
            {"id": third, "live": True},
        ]
        # Everything that was the first merged company's now sits on the last.
        assert _row(stack, "crm_people", pair["person"])["company_id"] == third
        assert _row(stack, "projects", pair["sourced"])["crm_company_id"] == third

    def test_both_ids_are_audited_as_merged(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        _merge(stack, pair["actor"], _plan(stack, keep, gone))
        rows = stack.sql(
            "SELECT target_id, after FROM audit_log WHERE action = 'company.merged' "
            f"AND target_id IN ({stack.q(keep)}, {stack.q(gone)})"
        )
        assert {r["target_id"] for r in rows} == {keep, gone}
        [mine] = [r for r in rows if r["target_id"] == keep]
        assert mine["after"]["merged_name"] == f"Acme Corp {pair['t']}"


class TestRefusedAndWritesNothing:
    """Data-model §5.7 — the function's own re-checks."""

    def test_the_same_id_twice(self, stack, pair):
        keep = pair["keep"]
        before = _snapshot(stack, keep, pair["gone"])
        plan = _plan(stack, keep, pair["gone"]) | {"merged_id": keep}
        message = _refused(stack, _merge_sql(stack, pair["actor"], plan), "CRM22")
        assert message == "Pick two different companies to merge."
        assert _snapshot(stack, keep, pair["gone"]) == before

    def test_a_company_of_another_workspace(self, stack, pair, other, owner):
        stranger = _company(stack, other, owner, f"Elsewhere {pair['t']}")
        before = _snapshot(stack, pair["keep"], stranger)
        plan = _plan(stack, pair["keep"], stranger)
        _refused(stack, _merge_sql(stack, pair["actor"], plan), "CRM04")
        assert _snapshot(stack, pair["keep"], stranger) == before

    def test_a_deleted_company(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        _apply(
            stack,
            pair["actor"],
            [
                {
                    "op": "soft_delete",
                    "table": "crm_companies",
                    "id": gone,
                    "deleted_group": str(uuid.uuid4()),
                }
            ],
        )
        before = _snapshot(stack, keep, gone)
        message = _refused(
            stack, _merge_sql(stack, pair["actor"], _plan(stack, keep, gone)), "CRM22"
        )
        assert "is in the Trash" in message
        assert _snapshot(stack, keep, gone) == before

    def test_an_already_merged_company(self, stack, pair, labs, owner):
        keep, gone = pair["keep"], pair["gone"]
        _merge(stack, pair["actor"], _plan(stack, keep, gone))
        third = _company(stack, labs, owner, f"Acme Ltd {pair['t']}")
        before = _snapshot(stack, third, gone)
        message = _refused(
            stack, _merge_sql(stack, pair["actor"], _plan(stack, third, gone)), "CRM22"
        )
        assert "was already merged" in message
        assert _snapshot(stack, third, gone) == before

    def test_a_change_meanwhile(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        plan = _plan(stack, keep, gone)
        plan["expect"]["merged_updated_at"] = "2020-01-01T00:00:00+00:00"
        before = _snapshot(stack, keep, gone)
        _refused(stack, _merge_sql(stack, pair["actor"], plan), "CRM09")
        assert _snapshot(stack, keep, gone) == before

    def test_a_late_refusal_rolls_everything_back(self, stack, pair):
        """A primary domain of neither company is refused after people, deals
        and links have moved: the transaction undoes all of it."""
        keep, gone = pair["keep"], pair["gone"]
        before = _snapshot(stack, keep, gone)
        plan = _plan(stack, keep, gone, primary_domain="nowhere.example")
        _refused(stack, _merge_sql(stack, pair["actor"], plan), "CRM22")
        assert _snapshot(stack, keep, gone) == before

    def test_a_system_field_cannot_be_chosen(self, stack, pair):
        keep, gone = pair["keep"], pair["gone"]
        plan = _plan(stack, keep, gone, survivor_changes={"workspace_id": str(uuid.uuid4())})
        _refused(stack, _merge_sql(stack, pair["actor"], plan), "CRM22")

    def test_no_browser_role_may_call_it(self, stack):
        rows = stack.sql(
            "SELECT has_function_privilege('authenticated', "
            "'crm_merge_companies(jsonb, jsonb)', 'EXECUTE') AS auth, "
            "has_function_privilege('anon', 'crm_merge_companies(jsonb, jsonb)', 'EXECUTE') "
            "AS anon"
        )
        assert rows == [{"auth": False, "anon": False}]
