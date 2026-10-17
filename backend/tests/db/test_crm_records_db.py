"""Companies, people and Drive links in the real database — spec 007
FR-CRM-CO-01, FR-CRM-PER-01, FR-CRM-DUP-01, -02, FR-CRM-DEL-01, -02,
FR-CRM-LINK-02, FR-CRM-WS-02, FR-CRM-REC-03; data-model §2, §3.6–§3.8,
§3.14, §5.2, §5.4, §5.14; migration 029.

What only Postgres can prove: `crm_apply` writes a company and its domains in
one call through `$ref`; one unique index refuses a domain whether it is
primary or additional; a stale edit is refused; a soft delete and its restore
work by group; `crm_purge` takes the Drive links with the record, and only
from the Trash; a reference into another workspace is refused by the
composite foreign keys or by `crm_check_refs()`; and the duplicate warning is
served by the trigram index. The store's own PostgREST queries (the timeline
filter, the plain list's search) are run against the real API too.
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


def _actor(workspace: str, user: str | None, *, kind: str = "user") -> dict:
    return {"user_id": user, "kind": kind, "channel": "web", "workspace_id": workspace}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _purge(stack, actor: dict, items: list[dict]) -> dict:
    plan = {"items": items}
    return stack.sql(f"SELECT crm_purge({_j(stack, actor)}, {_j(stack, plan)}) AS r")[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _apply_sql(stack, actor: dict, ops: list[dict]) -> str:
    return f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)})"


def _new_workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]


def _option(stack, workspace: str, list_key: str = "channel", label: str = "Referral") -> str:
    return _as_system(
        stack,
        "INSERT INTO crm_select_options (workspace_id, list_key, label, position) "
        f"VALUES ({stack.q(workspace)}, {stack.q(list_key)}, {stack.q(label)}, 1) RETURNING id",
    )[-1]["id"]


@pytest.fixture
def workspace(stack) -> str:
    return _new_workspace(stack)


@pytest.fixture
def other(stack) -> str:
    return _new_workspace(stack)


@pytest.fixture
def owner(make_profile) -> str:
    """A staff profile that acts (and so is kept, see conftest)."""
    return make_profile(keep=True)


def _company(stack, workspace, owner, name="Acme", domains=()) -> str:
    ops = [
        {
            "op": "insert",
            "table": "crm_companies",
            "ref": "c",
            "values": {"name": name, "owner_id": owner},
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
    return _apply(stack, _actor(workspace, owner), ops)[0]["id"]


class TestApply:
    def test_a_company_and_its_domains_in_one_call(self, stack, workspace, owner):
        company = _company(stack, workspace, owner, domains=("acme.com", "acme.io"))
        rows = stack.sql(
            "SELECT domain, is_primary FROM crm_company_domains "
            f"WHERE company_id = {stack.q(company)} ORDER BY domain"
        )
        assert rows == [
            {"domain": "acme.com", "is_primary": True},
            {"domain": "acme.io", "is_primary": False},
        ]
        row = stack.sql(f"SELECT * FROM crm_companies WHERE id = {stack.q(company)}")[0]
        assert (row["lead_stage"], row["created_by"]) == ("new", owner)
        actions = stack.sql(
            "SELECT action FROM audit_log WHERE workspace_id = "
            f"{stack.q(workspace)} AND target_table <> 'crm_workspaces' "
            # 009's 060 seeds a new workspace's `ai` setting, audited as the system.
            "AND target_table <> 'crm_workspace_settings' ORDER BY id"
        )
        assert [a["action"] for a in actions] == [
            "company.created",
            "company_domain.created",
            "company_domain.created",
        ]

    def test_a_domain_is_unique_across_primary_and_additional(self, stack, workspace, owner):
        _company(stack, workspace, owner, domains=("acme.com", "acme.io"))
        other = _company(stack, workspace, owner, name="Other")
        for domain, primary in (("acme.io", True), ("acme.com", False)):
            message = _refused(
                stack,
                _apply_sql(
                    stack,
                    _actor(workspace, owner),
                    [
                        {
                            "op": "insert",
                            "table": "crm_company_domains",
                            "values": {
                                "company_id": other,
                                "domain": domain,
                                "is_primary": primary,
                            },
                        }
                    ],
                ),
                "23505",
            )
            assert "crm_company_domains_workspace_domain" in message

    def test_a_trashed_company_keeps_its_domain(self, stack, workspace, owner):
        company = _company(stack, workspace, owner, domains=("acme.com",))
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "soft_delete",
                    "table": "crm_companies",
                    "id": company,
                    "deleted_group": str(uuid.uuid4()),
                }
            ],
        )
        with pytest.raises(stack.SqlError):
            _company(stack, workspace, owner, name="Again", domains=("acme.com",))

    def test_one_primary_email_per_workspace(self, stack, workspace, other, owner):
        person = {"first_name": "Deepa", "primary_email": "deepa@acme.com"}
        _apply(
            stack,
            _actor(workspace, owner),
            [{"op": "insert", "table": "crm_people", "values": person}],
        )
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [{"op": "insert", "table": "crm_people", "values": person}],
            ),
            "23505",
        )
        _apply(
            stack, _actor(other, owner), [{"op": "insert", "table": "crm_people", "values": person}]
        )

    def test_a_stale_edit_is_refused(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        seen = stack.sql(f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(company)}")[0]
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "update",
                    "table": "crm_companies",
                    "id": company,
                    "values": {"city": "Chennai"},
                }
            ],
        )
        message = _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "update",
                        "table": "crm_companies",
                        "id": company,
                        "values": {"city": "Madurai"},
                        "expect_updated_at": seen["updated_at"],
                    }
                ],
            ),
            "CRM09",
        )
        assert message == "This record changed while you were editing it; reload and try again."

    def test_soft_delete_and_restore_by_group(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        person = _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_people",
                    "values": {"first_name": "D", "company_id": company},
                }
            ],
        )[0]["id"]
        group = str(uuid.uuid4())
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "soft_delete",
                    "table": "crm_companies",
                    "id": company,
                    "deleted_group": group,
                },
                {"op": "soft_delete", "table": "crm_people", "id": person, "deleted_group": group},
            ],
        )
        trashed = stack.sql(
            "SELECT id FROM crm_companies WHERE deleted_group = "
            f"{stack.q(group)} UNION ALL SELECT id FROM crm_people WHERE deleted_group = {stack.q(group)}"
        )
        assert {r["id"] for r in trashed} == {company, person}
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {"op": "restore", "table": "crm_companies", "id": company},
                {"op": "restore", "table": "crm_people", "id": person},
            ],
        )
        row = stack.sql(f"SELECT * FROM crm_people WHERE id = {stack.q(person)}")[0]
        assert (row["deleted_at"], row["deleted_group"], row["company_id"]) == (None, None, company)
        actions = stack.sql(
            f"SELECT action FROM audit_log WHERE target_id = {stack.q(company)} ORDER BY id"
        )
        assert [a["action"] for a in actions] == [
            "company.created",
            "company.deleted",
            "company.restored",
        ]

    def test_a_person_restored_and_unlinked_in_one_call(self, stack, workspace, owner):
        """`records.restore` unlinks a person whose company is still in the
        Trash: the restore and the update are two ops of one call."""
        company = _company(stack, workspace, owner)
        person = _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_people",
                    "values": {"first_name": "D", "company_id": company},
                }
            ],
        )[0]["id"]
        for table, target in (("crm_people", person), ("crm_companies", company)):
            _apply(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "soft_delete",
                        "table": table,
                        "id": target,
                        "deleted_group": str(uuid.uuid4()),
                    }
                ],
            )
        done = _apply(
            stack,
            _actor(workspace, owner),
            [
                {"op": "restore", "table": "crm_people", "id": person},
                {
                    "op": "update",
                    "table": "crm_people",
                    "id": person,
                    "values": {"company_id": None},
                },
            ],
        )
        assert done[-1]["row"]["company_id"] is None
        row = stack.sql(f"SELECT * FROM crm_people WHERE id = {stack.q(person)}")[0]
        assert (row["deleted_at"], row["company_id"]) == (None, None)


class TestPurge:
    """Data-model §5.4, 029's first form."""

    def _trashed_with_link(self, stack, workspace, owner) -> tuple[str, str]:
        company = _company(stack, workspace, owner, domains=("acme.com",))
        link = _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_drive_links",
                    "values": {
                        "record_type": "company",
                        "record_id": company,
                        "url": "https://drive.google.com/x",
                        "label": "SOW",
                    },
                },
                {
                    "op": "soft_delete",
                    "table": "crm_companies",
                    "id": company,
                    "deleted_group": str(uuid.uuid4()),
                },
            ],
        )[0]["id"]
        return company, link

    def test_it_removes_the_drive_links_and_domains(self, stack, workspace, owner):
        company, link = self._trashed_with_link(stack, workspace, owner)
        person = _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_people",
                    "values": {"first_name": "D", "company_id": company},
                }
            ],
        )[0]["id"]
        assert _purge(
            stack,
            _actor(workspace, None, kind="system"),
            [{"table": "crm_companies", "id": company}],
        ) == {"purged": 1}
        assert stack.sql(f"SELECT id FROM crm_drive_links WHERE id = {stack.q(link)}") == []
        assert (
            stack.sql(f"SELECT id FROM crm_company_domains WHERE company_id = {stack.q(company)}")
            == []
        )
        assert stack.sql(f"SELECT company_id FROM crm_people WHERE id = {stack.q(person)}") == [
            {"company_id": None}
        ]
        actions = {
            a["action"]
            for a in stack.sql(
                f"SELECT action FROM audit_log WHERE workspace_id = {stack.q(workspace)}"
            )
        }
        assert {"company.purged", "drive_link.purged", "company_domain.purged"} <= actions

    def test_only_from_the_trash(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        plan = {"items": [{"table": "crm_companies", "id": company}]}
        _refused(
            stack,
            f"SELECT crm_purge({_j(stack, _actor(workspace, owner))}, {_j(stack, plan)})",
            "CRM04",
        )

    def test_only_from_the_actors_workspace(self, stack, workspace, other, owner):
        company, _ = self._trashed_with_link(stack, workspace, owner)
        plan = {"items": [{"table": "crm_companies", "id": company}]}
        _refused(
            stack,
            f"SELECT crm_purge({_j(stack, _actor(other, owner))}, {_j(stack, plan)})",
            "CRM04",
        )

    def test_not_any_table(self, stack, workspace, owner):
        plan = {"items": [{"table": "crm_memberships", "id": str(uuid.uuid4())}]}
        _refused(
            stack,
            f"SELECT crm_purge({_j(stack, _actor(workspace, owner))}, {_j(stack, plan)})",
            "CRM22",
        )


class TestPurgeLeavesNoLinklessTask:
    """008 FR-TASK-DATA-14 (048): purging a record purges, in the same call,
    every task of the workspace it leaves with no CRM link, live or in the
    Trash; a task still linked to something else stays."""

    def _task(self, stack, workspace, owner, *companies) -> str:
        ops = [
            {
                "op": "insert",
                "table": "tasks",
                "ref": "t",
                "values": {"title": "Call", "assignee_id": owner, "bucket": "later"},
            }
        ]
        ops += [
            {
                "op": "insert",
                "table": "task_links",
                "values": {"task_id": "$ref:t", "record_type": "company", "record_id": c},
            }
            for c in companies
        ]
        return _apply(stack, _actor(workspace, owner), ops)[0]["id"]

    def test_tasks_left_with_no_crm_link_go_with_the_record(self, stack, workspace, owner):
        gone = _company(stack, workspace, owner, "Gone")
        kept = _company(stack, workspace, owner, "Kept")
        alone = self._task(stack, workspace, owner, gone)
        trashed = self._task(stack, workspace, owner, gone)
        shared = self._task(stack, workspace, owner, gone, kept)
        group = str(uuid.uuid4())
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {"op": "soft_delete", "table": "tasks", "id": trashed, "deleted_group": group},
                {"op": "soft_delete", "table": "crm_companies", "id": gone, "deleted_group": group},
            ],
        )
        _purge(stack, _actor(workspace, owner), [{"table": "crm_companies", "id": gone}])
        left = {
            r["id"]
            for r in stack.sql(
                f"SELECT id FROM tasks WHERE id IN ({stack.q(alone)}, {stack.q(trashed)}, "
                f"{stack.q(shared)})"
            )
        }
        assert left == {shared}
        actions = {
            r["target_id"]: r["action"]
            for r in stack.sql(
                "SELECT target_id, action FROM audit_log WHERE target_table = 'tasks' "
                f"AND target_id IN ({stack.q(alone)}, {stack.q(trashed)}) "
                "AND action = 'task.purged'"
            )
        }
        assert actions == {alone: "task.purged", trashed: "task.purged"}


class TestTheCrmFillsTheBucket:
    """008 FR-TASK-DATA-08: a task created or reopened through the CRM routes
    with no bucket gets the default one, in the person's own day."""

    @pytest.fixture
    def http(self, stack, monkeypatch, owner):
        local = create_client(stack.url, stack.service_key)
        monkeypatch.setattr(db, "supabase", local)
        monkeypatch.setattr(store, "supabase", local)
        monkeypatch.setattr(deps, "_verify", lambda token: token)
        _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"SELECT id, {stack.q(owner)}, 'admin' FROM crm_workspaces WHERE key = 'labs' "
            "ON CONFLICT DO NOTHING",
        )
        labs = stack.sql("SELECT id FROM crm_workspaces WHERE key = 'labs'")[0]["id"]
        return TestClient(app), labs

    def test_created_and_reopened(self, stack, http, owner):
        client, labs = http
        me = {"Authorization": f"Bearer {owner}"}
        company = _company(stack, labs, owner, f"Bucket {uuid.uuid4().hex[:6]}")
        links = [{"record_type": "company", "record_id": company}]
        r = client.post(
            "/api/v1/crm/tasks?workspace=labs", json={"title": "Call", "links": links}, headers=me
        )
        assert r.status_code == 201, r.text
        task = r.json()
        assert (task["status"], task["bucket"]) == ("open", "later")
        r = client.post(
            "/api/v1/crm/tasks?workspace=labs",
            json={"title": "Overdue", "links": links, "due_on": "2026-01-01"},
            headers=me,
        )
        assert r.json()["bucket"] == "today"

        client.patch(
            f"/api/v1/crm/tasks/{task['id']}?workspace=labs", json={"status": "done"}, headers=me
        )
        _as_system(stack, f"UPDATE tasks SET bucket = NULL WHERE id = {stack.q(task['id'])}")
        r = client.patch(
            f"/api/v1/crm/tasks/{task['id']}?workspace=labs", json={"status": "todo"}, headers=me
        )
        assert r.status_code == 200, r.text
        assert (r.json()["status"], r.json()["bucket"], r.json()["completed_at"]) == (
            "open",
            "later",
            None,
        )


class TestReferencesStayInTheWorkspace:
    """Data-model §2, §5.14, FR-CRM-WS-02."""

    def test_a_person_pointing_at_another_workspaces_company(self, stack, workspace, other, owner):
        foreign = _company(stack, other, owner)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_people",
                        "values": {"first_name": "D", "company_id": foreign},
                    }
                ],
            ),
            "23503",
        )

    def test_a_company_with_another_workspaces_option(self, stack, workspace, other, owner):
        option = _option(stack, other, "company.account_type", "Client")
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_companies",
                        "values": {"name": "X", "owner_id": owner, "account_type_id": option},
                    }
                ],
            ),
            "23503",
        )

    def test_channels_of_another_workspace(self, stack, workspace, other, owner):
        option = _option(stack, other)
        message = _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_companies",
                        "values": {"name": "X", "owner_id": owner, "channel_ids": [option]},
                    }
                ],
            ),
            "CRM04",
        )
        assert message == "No such option in this workspace."
        mine = _option(stack, workspace)
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": "X", "owner_id": owner, "channel_ids": [mine]},
                }
            ],
        )

    def test_a_drive_link_to_another_workspaces_record(self, stack, workspace, other, owner):
        foreign = _company(stack, other, owner)
        message = _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_drive_links",
                        "values": {
                            "record_type": "company",
                            "record_id": foreign,
                            "url": "https://drive.google.com/x",
                            "label": "SOW",
                        },
                    }
                ],
            ),
            "CRM04",
        )
        assert message == "No such company in this workspace."

    @pytest.mark.parametrize("url", ["http://drive.google.com/x", "https://a b", "javascript:x"])
    def test_a_drive_link_is_https_only(self, stack, workspace, owner, url):
        company = _company(stack, workspace, owner)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_drive_links",
                        "values": {
                            "record_type": "company",
                            "record_id": company,
                            "url": url,
                            "label": "L",
                        },
                    }
                ],
            ),
            "23514",
        )


class TestSimilarNames:
    """FR-CRM-DUP-02 — `crm_similar` (029), served by the trigram index."""

    def test_names_alike_and_not(self, stack, workspace, owner, monkeypatch):
        _company(stack, workspace, owner, name="Acme")
        _company(stack, workspace, owner, name="Zenith Robotics")
        monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
        found = store.similar(workspace, "company", "Acme Corp")
        assert [f["name"] for f in found] == ["Acme"]
        assert found[0]["similarity"] >= 0.4

    def test_the_trigram_index_is_used(self, stack, workspace, owner):
        """At the 10,000 companies NFR-CRM-01 plans for, `crm_similar`'s query
        is served by the GIN index. The EXPLAIN runs with the planner setting
        the function itself carries (029), checked first."""
        config = stack.sql("SELECT proconfig FROM pg_proc WHERE proname = 'crm_similar'")[0][
            "proconfig"
        ]
        assert "cpu_operator_cost=0.25" in config and "pg_trgm.similarity_threshold=0.4" in config
        _as_system(
            stack,
            "INSERT INTO crm_companies (workspace_id, name, owner_id) "
            f"SELECT {stack.q(workspace)}, md5(n::text) || ' ' || substr(md5((n * 7)::text), 1, 8), "
            f"{stack.q(owner)} FROM generate_series(1, 10000) AS n; ANALYZE crm_companies",
        )
        plan = stack.sql(
            "SET cpu_operator_cost = 0.25; EXPLAIN SELECT id FROM crm_companies "
            f"WHERE workspace_id = {stack.q(workspace)} AND deleted_at IS NULL "
            "AND lower(name) OPERATOR(extensions.%) 'acme corp'"
        )
        text = "\n".join(r["QUERY PLAN"] for r in plan)
        assert "crm_companies_name_trgm" in text, text


class TestStoreQueries:
    """The store's PostgREST filters, against the real API."""

    def test_the_timeline_finds_the_record_its_domains_and_links(
        self, stack, workspace, owner, monkeypatch
    ):
        company = _company(stack, workspace, owner, domains=("acme.com",))
        _apply(
            stack,
            _actor(workspace, owner),
            [
                {
                    "op": "insert",
                    "table": "crm_drive_links",
                    "values": {
                        "record_type": "company",
                        "record_id": company,
                        "url": "https://drive.google.com/x",
                        "label": "SOW",
                    },
                },
                {
                    "op": "update",
                    "table": "crm_companies",
                    "id": company,
                    "values": {"city": "Pune"},
                },
            ],
        )
        _company(stack, workspace, owner, name="Unrelated", domains=("other.com",))
        monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
        rows = store.list_timeline(workspace, "crm_companies", company, record_type="company")
        assert [r["action"] for r in rows] == [
            "company.updated",
            "drive_link.created",
            "company_domain.created",
            "company.created",
        ]

    def test_the_plain_list_search_is_literal(self, stack, workspace, owner, monkeypatch):
        _company(stack, workspace, owner, name="100% Robotics")
        _company(stack, workspace, owner, name="1000 Robots")
        monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
        found = store.list_records(workspace, "crm_companies", q="100%")
        assert [r["name"] for r in found] == ["100% Robotics"]
        domains = store.list_domains(workspace, domains=["acme.com"])
        assert domains == []
