"""Deals, tasks and notes in the real database — spec 007 FR-CRM-OPP-02,
FR-CRM-PIPE-07, -10, -14, -15, FR-CRM-FX-03, FR-CRM-TASK-01, FR-CRM-NOTE-03,
FR-CRM-DEL-01, FR-CRM-WS-02; data-model §3.9–§3.13, §5.2, §5.4, §5.5;
migrations 030, 031.

What only Postgres can prove: `crm_deal_transition` refuses a stale plan and
writes nothing; every move writes its history row; a move that fails takes
its company change, closed tasks and note with it; it trusts no id in the
plan (another workspace's deal, company, task, assignee or link target is
"not found"); `crm_apply` refuses a stage change and writes a new deal's
first history row; the backstop and FX CHECKs refuse what Python should have;
a deal cannot point into another workspace; and `crm_purge` removes the task
and note links to a purged record. The store's new PostgREST queries run
against the real API too.
"""

from __future__ import annotations

import json
import uuid

import pytest
from supabase import create_client

from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
NEXT = {"next_step": "Call", "next_step_on": "2026-10-20"}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str | None) -> dict:
    return {"user_id": user, "kind": "user", "channel": "web", "workspace_id": workspace}


def _apply_sql(stack, actor: dict, ops: list[dict]) -> str:
    return f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r"


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(_apply_sql(stack, actor, ops))[0]["r"]


def _move_sql(stack, actor: dict, plan: dict) -> str:
    return f"SELECT crm_deal_transition({_j(stack, actor)}, {_j(stack, plan)}) AS r"


def _move(stack, actor: dict, plan: dict) -> dict:
    return stack.sql(_move_sql(stack, actor, plan))[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _one(stack, query: str) -> dict:
    return stack.sql(query)[0]


def _new_workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}) RETURNING id",
    )[-1]["id"]


def _option(stack, workspace: str, list_key: str, label: str, key: str | None = None) -> str:
    system_key = stack.q(key) if key else "NULL"
    return _as_system(
        stack,
        "INSERT INTO crm_select_options (workspace_id, list_key, system_key, label, position) "
        f"VALUES ({stack.q(workspace)}, {stack.q(list_key)}, {system_key}, {stack.q(label)}, 1) "
        "RETURNING id",
    )[-1]["id"]


def _member(stack, workspace: str, user: str) -> None:
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(user)}, 'admin')",
    )


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def workspace(stack, owner) -> str:
    ws = _new_workspace(stack)
    _member(stack, ws, owner)
    return ws


@pytest.fixture
def other(stack, owner) -> str:
    ws = _new_workspace(stack)
    _member(stack, ws, owner)
    return ws


def _company(stack, workspace, owner, stage="deal_open") -> str:
    return _apply(
        stack,
        _actor(workspace, owner),
        [
            {
                "op": "insert",
                "table": "crm_companies",
                "values": {"name": "Acme", "owner_id": owner, "lead_stage": stage},
            }
        ],
    )[0]["id"]


def _deal_values(company: str, owner: str, **extra) -> dict:
    return {
        "name": "Acme - deal",
        "company_id": company,
        "owner_id": owner,
        "probability": 10,
        **NEXT,
        **extra,
    }


def _deal(stack, workspace, owner, company=None, **extra) -> str:
    company = company or _company(stack, workspace, owner)
    return _apply(
        stack,
        _actor(workspace, owner),
        [
            {
                "op": "insert",
                "table": "crm_opportunities",
                "values": _deal_values(company, owner, **extra),
            }
        ],
    )[0]["id"]


def _task(stack, workspace, owner, deal: str, title="Call back") -> str:
    return _apply(
        stack,
        _actor(workspace, owner),
        [
            {
                "op": "insert",
                "table": "tasks",
                "ref": "t",
                "values": {"title": title, "assignee_id": owner, "bucket": "later"},
            },
            {
                "op": "insert",
                "table": "task_links",
                "values": {"task_id": "$ref:t", "record_type": "opportunity", "record_id": deal},
            },
        ],
    )[0]["id"]


def _deal_row(stack, deal: str) -> dict:
    return _one(stack, f"SELECT * FROM crm_opportunities WHERE id = {stack.q(deal)}")


def _history(stack, deal: str) -> list[dict]:
    return stack.sql(
        'SELECT from_stage, to_stage, at, "by", actor_kind FROM crm_stage_history '
        f"WHERE opportunity_id = {stack.q(deal)} ORDER BY at, to_stage"
    )


def _plan(stack, deal: str, to: str, **extra) -> dict:
    row = _deal_row(stack, deal)
    return {
        "opportunity_id": deal,
        "expect_stage": row["stage"],
        "expect_updated_at": row["updated_at"],
        "opportunity": {"stage": to, "stage_entered_at": "now", **extra.pop("values", {})},
        "history": {"from": row["stage"], "to": to},
        **extra,
    }


class TestApplyAndDeals:
    def test_a_new_deal_writes_its_first_history_row(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        assert [(h["from_stage"], h["to_stage"], h["by"]) for h in _history(stack, deal)] == [
            (None, "discovery", owner)
        ]

    def test_crm_apply_refuses_a_stage_change(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        message = _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "update",
                        "table": "crm_opportunities",
                        "id": deal,
                        "values": {"stage": "qualification"},
                    }
                ],
            ),
            "CRM22",
        )
        assert message == "Use a stage move."

    def test_an_open_deal_needs_a_next_step(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        values = _deal_values(company, owner)
        values.pop("next_step")
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [{"op": "insert", "table": "crm_opportunities", "values": values}],
            ),
            "23514",
        )

    def test_the_fx_checks(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        bad = [
            {"amount": "100", "currency": "USD", "amount_in_inr": "8000.00", "fx_rate_used": "84"},
            {"amount": "100", "currency": "INR", "amount_in_inr": "200.00", "fx_rate_used": "2"},
            {"amount": "100", "currency": "USD", "amount_in_inr": "100.00"},
        ]
        for money in bad:
            _refused(
                stack,
                _apply_sql(
                    stack,
                    _actor(workspace, owner),
                    [
                        {
                            "op": "insert",
                            "table": "crm_opportunities",
                            "values": _deal_values(company, owner, **money),
                        }
                    ],
                ),
                "23514",
            )
        good = {
            "amount": "100.05",
            "currency": "USD",
            "amount_in_inr": "8416.71",
            "fx_rate_used": "84.125",
            "fx_month": "2026-10-01",
        }
        assert _deal(stack, workspace, owner, company, **good)

    def test_a_deal_cannot_point_into_another_workspace(self, stack, workspace, other, owner):
        foreign_company = _company(stack, other, owner)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_opportunities",
                        "values": _deal_values(foreign_company, owner),
                    }
                ],
            ),
            "23503",
        )
        foreign_person = _apply(
            stack,
            _actor(other, owner),
            [{"op": "insert", "table": "crm_people", "values": {"first_name": "P"}}],
        )[0]["id"]
        company = _company(stack, workspace, owner)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_opportunities",
                        "values": _deal_values(company, owner, point_of_contact_id=foreign_person),
                    }
                ],
            ),
            "23503",
        )
        deal = _deal(stack, workspace, owner, company)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "crm_opportunity_contacts",
                        "values": {"opportunity_id": deal, "person_id": foreign_person},
                    }
                ],
            ),
            "23503",
        )

    def test_a_task_needs_a_link_in_its_workspace(self, stack, workspace, other, owner):
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "tasks",
                        "values": {"title": "Orphan", "assignee_id": owner, "bucket": "later"},
                    }
                ],
            ),
            "CRM22",
        )
        foreign = _deal(stack, other, owner)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [
                    {
                        "op": "insert",
                        "table": "tasks",
                        "ref": "t",
                        "values": {"title": "X", "assignee_id": owner, "bucket": "later"},
                    },
                    {
                        "op": "insert",
                        "table": "task_links",
                        "values": {
                            "task_id": "$ref:t",
                            "record_type": "opportunity",
                            "record_id": foreign,
                        },
                    },
                ],
            ),
            "CRM04",
        )

    def test_a_logged_activity_needs_its_date(self, stack, workspace, owner):
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [{"op": "insert", "table": "crm_notes", "values": {"body": "x", "kind": "call"}}],
            ),
            "23514",
        )

    def test_done_and_completed_at_go_together(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        task = _task(stack, workspace, owner, deal)
        _refused(
            stack,
            _apply_sql(
                stack,
                _actor(workspace, owner),
                [{"op": "update", "table": "tasks", "id": task, "values": {"status": "done"}}],
            ),
            "23514",
        )


class TestTransition:
    def test_a_move_writes_the_deal_and_its_history(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        person = _apply(
            stack,
            _actor(workspace, owner),
            [{"op": "insert", "table": "crm_people", "values": {"first_name": "P"}}],
        )[0]["id"]
        done = _move(
            stack,
            _actor(workspace, owner),
            _plan(
                stack,
                deal,
                "qualification",
                values={"probability": 25, "point_of_contact_id": person},
            ),
        )
        assert (done["opportunity"]["stage"], done["opportunity"]["probability"]) == (
            "qualification",
            25,
        )
        history = _history(stack, deal)
        assert [(h["from_stage"], h["to_stage"]) for h in history] == [
            (None, "discovery"),
            ("discovery", "qualification"),
        ]
        row = _deal_row(stack, deal)
        assert history[-1]["at"] == row["stage_entered_at"]
        action = _one(
            stack,
            "SELECT action, actor_id FROM audit_log WHERE target_table = 'crm_opportunities' "
            f"AND target_id = {stack.q(deal)} ORDER BY id DESC LIMIT 1",
        )
        assert action == {"action": "opportunity.stage_changed", "actor_id": owner}

    def test_a_stale_stage_or_stamp_writes_nothing(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        plan = _plan(stack, deal, "closed_lost")
        for stale in ({"expect_stage": "proposal"}, {"expect_updated_at": "2020-01-01T00:00:00Z"}):
            _refused(stack, _move_sql(stack, _actor(workspace, owner), {**plan, **stale}), "CRM09")
        assert _deal_row(stack, deal)["stage"] == "discovery"
        assert len(_history(stack, deal)) == 1

    def test_a_failed_move_rolls_back_the_company_tasks_and_note(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        deal = _deal(stack, workspace, owner, company)
        task = _task(stack, workspace, owner, deal)
        before = _one(stack, f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(company)}")
        reason = _option(stack, workspace, "opportunity.lost_reason", "Price", "price")
        plan = _plan(
            stack,
            deal,
            "closed_lost",
            values={"probability": 0, "lost_reason_id": reason, "lost_on": "2026-10-14"},
            company={
                "id": company,
                "expect_updated_at": before["updated_at"],
                "changes": {"lead_stage": "nurturing"},
            },
            close_tasks=[task],
            # A blank note fails its CHECK last, after the deal, its history,
            # the company and the tasks were written.
            close_note={"body": " "},
        )
        _refused(stack, _move_sql(stack, _actor(workspace, owner), plan), "23514")
        assert (
            _one(stack, f"SELECT lead_stage FROM crm_companies WHERE id = {stack.q(company)}")[
                "lead_stage"
            ]
            == "deal_open"
        )
        assert (
            _one(stack, f"SELECT status FROM tasks WHERE id = {stack.q(task)}")["status"] == "open"
        )
        assert stack.sql(f"SELECT 1 FROM crm_notes WHERE workspace_id = {stack.q(workspace)}") == []
        assert len(_history(stack, deal)) == 1

    def test_lost_closes_the_tasks_with_a_note(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        deal = _deal(stack, workspace, owner, company)
        task = _task(stack, workspace, owner, deal)
        reason = _option(stack, workspace, "opportunity.lost_reason", "Price", "price")
        done = _move(
            stack,
            _actor(workspace, owner),
            _plan(
                stack,
                deal,
                "closed_lost",
                values={"probability": 0, "lost_reason_id": reason, "lost_on": "2026-10-14"},
                company={"id": company, "changes": {"lead_stage": "nurturing"}},
                close_tasks=[task],
                close_note={"body": "Closed: deal lost (Price)."},
            ),
        )
        assert done["tasks_closed"] == 1 and done["company"]["lead_stage"] == "nurturing"
        row = _one(stack, f"SELECT status, completed_at FROM tasks WHERE id = {stack.q(task)}")
        # 008 FR-TASK-CHG-04 (TP-07): Cancelled, not Done.
        assert row["status"] == "cancelled" and row["completed_at"] is None
        note = _one(
            stack,
            "SELECT n.body, l.record_id FROM crm_notes n JOIN crm_note_links l ON l.note_id = n.id "
            f"WHERE n.workspace_id = {stack.q(workspace)}",
        )
        assert note == {"body": "Closed: deal lost (Price).", "record_id": deal}

    def test_a_sibling_closed_meanwhile_refuses_the_move(self, stack, workspace, owner):
        """FR-CRM-PIPE-05, -15 — two deals of one company closed at once: each
        plan saw the other open; the second write counts again and refuses."""
        company = _company(stack, workspace, owner)
        first = _deal(stack, workspace, owner, company)
        second = _deal(stack, workspace, owner, company)
        reason = _option(stack, workspace, "opportunity.lost_reason", "Price", "price")
        lost = {"probability": 0, "lost_reason_id": reason, "lost_on": "2026-10-14"}
        siblings = {"open": 1, "won": False}
        plan_first = _plan(stack, first, "closed_lost", values=lost, expect_siblings=siblings)
        plan_second = _plan(stack, second, "closed_lost", values=lost, expect_siblings=siblings)
        _move(stack, _actor(workspace, owner), plan_first)
        message = _refused(stack, _move_sql(stack, _actor(workspace, owner), plan_second), "CRM09")
        assert message == "The company's deals changed meanwhile; reload and try again."
        assert _deal_row(stack, second)["stage"] == "discovery"
        won = {**plan_second, "expect_siblings": {"open": 0, "won": True}}
        _refused(stack, _move_sql(stack, _actor(workspace, owner), won), "CRM09")
        right = {**plan_second, "expect_siblings": {"open": 0, "won": False}}
        assert _move(stack, _actor(workspace, owner), right)["opportunity"]["stage"] == (
            "closed_lost"
        )

    def test_lost_refuses_when_a_task_was_added_meanwhile(self, stack, workspace, owner):
        """Automation 9 — every open task on the deal closes with it."""
        deal = _deal(stack, workspace, owner)
        first = _task(stack, workspace, owner, deal)
        reason = _option(stack, workspace, "opportunity.lost_reason", "Price", "price")
        plan = _plan(
            stack,
            deal,
            "closed_lost",
            values={"probability": 0, "lost_reason_id": reason, "lost_on": "2026-10-14"},
            close_tasks=[first],
            close_note={"body": "Closed: deal lost (Price)."},
        )
        late = _task(stack, workspace, owner, deal, title="Added meanwhile")
        message = _refused(stack, _move_sql(stack, _actor(workspace, owner), plan), "CRM09")
        assert message == "The deal's tasks changed meanwhile; reload and try again."
        assert _deal_row(stack, deal)["stage"] == "discovery"
        done = _move(stack, _actor(workspace, owner), {**plan, "close_tasks": [first, late]})
        assert done["tasks_closed"] == 2

    def test_the_backstops_hold_through_a_move(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        _refused(
            stack,
            _move_sql(stack, _actor(workspace, owner), _plan(stack, deal, "closed_lost")),
            "23514",
        )
        _refused(
            stack,
            _move_sql(
                stack,
                _actor(workspace, owner),
                _plan(stack, deal, "qualification", values={"next_step": None}),
            ),
            "23514",
        )

    def test_a_project_action_outside_a_tva_linked_workspace_is_refused(
        self, stack, workspace, owner
    ):
        """033 re-check 3; the TVA side itself is in test_crm_lifecycle_db.py."""
        deal = _deal(stack, workspace, owner)
        plan = _plan(stack, deal, "discovery", project={"action": "create", "name": "Acme"})
        message = _refused(stack, _move_sql(stack, _actor(workspace, owner), plan), "CRM22")
        assert message == "This workspace is not connected to TVA projects."


class TestTheTransitionTrustsNoId:
    """Data-model §5.5 re-checks 1 and 2, Q-68."""

    def test_another_workspaces_deal(self, stack, workspace, other, owner):
        deal = _deal(stack, other, owner)
        _refused(
            stack,
            _move_sql(stack, _actor(workspace, owner), _plan(stack, deal, "closed_lost")),
            "CRM04",
        )

    def test_a_company_that_is_not_the_deals_own(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        stranger = _company(stack, workspace, owner)
        plan = _plan(
            stack,
            deal,
            "qualification",
            company={"id": stranger, "changes": {"lead_stage": "customer"}},
        )
        _refused(stack, _move_sql(stack, _actor(workspace, owner), plan), "CRM04")
        assert (
            _one(stack, f"SELECT lead_stage FROM crm_companies WHERE id = {stack.q(stranger)}")[
                "lead_stage"
            ]
            == "deal_open"
        )

    def test_a_task_of_another_deal_or_workspace(self, stack, workspace, other, owner):
        deal = _deal(stack, workspace, owner)
        elsewhere = _task(stack, workspace, owner, _deal(stack, workspace, owner))
        foreign = _task(stack, other, owner, _deal(stack, other, owner))
        for task in (elsewhere, foreign):
            plan = _plan(stack, deal, "discovery", close_tasks=[task])
            _refused(stack, _move_sql(stack, _actor(workspace, owner), plan), "CRM04")
        assert (
            _one(stack, f"SELECT status FROM tasks WHERE id = {stack.q(elsewhere)}")["status"]
            == "open"
        )

    def test_a_non_member_assignee_or_foreign_link(
        self, stack, workspace, other, owner, make_profile
    ):
        deal = _deal(stack, workspace, owner)
        stranger = make_profile(keep=True)
        foreign = _deal(stack, other, owner)
        for task in (
            {
                "title": "X",
                "assignee_id": stranger,
                "links": [{"record_type": "opportunity", "record_id": deal}],
            },
            {
                "title": "X",
                "assignee_id": owner,
                "links": [{"record_type": "opportunity", "record_id": foreign}],
            },
        ):
            plan = _plan(stack, deal, "discovery", create_tasks=[task])
            _refused(stack, _move_sql(stack, _actor(workspace, owner), plan), "CRM04")
        assert stack.sql(f"SELECT 1 FROM tasks WHERE workspace_id = {stack.q(workspace)}") == []

    def test_create_tasks_with_their_links(self, stack, workspace, owner):
        deal = _deal(stack, workspace, owner)
        _option(stack, workspace, "task.category", "Delivery handoff", "delivery_handoff")
        plan = _plan(
            stack,
            deal,
            "discovery",
            create_tasks=[
                {
                    "title": "Hand over",
                    "assignee_id": owner,
                    "category_key": "delivery_handoff",
                    "bucket": "this_week",
                    "links": [{"record_type": "opportunity", "record_id": deal}],
                }
            ],
        )
        plan["history"] = None
        plan["opportunity"] = {"probability": 15}
        done = _move(stack, _actor(workspace, owner), plan)
        assert len(done["tasks_created"]) == 1
        row = _one(
            stack,
            "SELECT t.bucket, o.system_key FROM tasks t "
            "JOIN crm_select_options o ON o.id = t.category_id "
            f"WHERE t.id = {stack.q(done['tasks_created'][0])}",
        )
        assert row == {"bucket": "this_week", "system_key": "delivery_handoff"}


class TestPurge:
    """Data-model §5.4, 030 form."""

    def test_task_and_note_links_go_with_the_record(self, stack, workspace, owner):
        company = _company(stack, workspace, owner)
        deal = _deal(stack, workspace, owner, company)
        task = _task(stack, workspace, owner, deal)
        actor = _actor(workspace, owner)
        _apply(
            stack,
            actor,
            [
                {
                    "op": "insert",
                    "table": "task_links",
                    "values": {"task_id": task, "record_type": "company", "record_id": company},
                },
                {"op": "insert", "table": "crm_notes", "ref": "n", "values": {"body": "x"}},
                {
                    "op": "insert",
                    "table": "crm_note_links",
                    "values": {
                        "note_id": "$ref:n",
                        "record_type": "opportunity",
                        "record_id": deal,
                    },
                },
                {"op": "soft_delete", "table": "crm_opportunities", "id": deal},
            ],
        )
        purged = stack.sql(
            f"SELECT crm_purge({_j(stack, actor)}, "
            f"{_j(stack, {'items': [{'table': 'crm_opportunities', 'id': deal}]})}) AS r"
        )[0]["r"]
        assert purged == {"purged": 1}
        assert stack.sql(f"SELECT 1 FROM crm_note_links WHERE record_id = {stack.q(deal)}") == []
        links = stack.sql(f"SELECT record_type FROM task_links WHERE task_id = {stack.q(task)}")
        assert links == [{"record_type": "company"}]
        assert _history(stack, deal) == []


class TestStoreQueries:
    """The store's S4 PostgREST queries against the real API."""

    def test_links_contacts_history_and_the_timeline(self, stack, workspace, owner, monkeypatch):
        company = _company(stack, workspace, owner)
        deal = _deal(stack, workspace, owner, company)
        task = _task(stack, workspace, owner, deal)
        person = _apply(
            stack,
            _actor(workspace, owner),
            [
                {"op": "insert", "table": "crm_people", "ref": "p", "values": {"first_name": "P"}},
                {
                    "op": "insert",
                    "table": "crm_opportunity_contacts",
                    "values": {"opportunity_id": deal, "person_id": "$ref:p"},
                },
            ],
        )[0]["id"]
        monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
        links = store.list_links(
            workspace, "task_links", record_type="opportunity", record_ids=[deal]
        )
        assert [lk["task_id"] for lk in links] == [task]
        assert [c["person_id"] for c in store.list_contacts(workspace, opportunity_ids=[deal])] == [
            person
        ]
        open_tasks = store.list_records(
            workspace, "tasks", any_of={"id": [task], "status": ["open", "waiting"]}
        )
        assert [t["id"] for t in open_tasks] == [task]
        reason = _option(stack, workspace, "opportunity.lost_reason", "Price", "price")
        done = store.deal_transition(
            _actor(workspace, owner),
            _plan(
                stack,
                deal,
                "closed_lost",
                values={"probability": 0, "lost_reason_id": reason},
                close_tasks=[task],
            ),
        )
        assert done["opportunity"]["stage"] == "closed_lost"
        assert [h["to_stage"] for h in store.list_stage_history(workspace, deal)] == [
            "discovery",
            "closed_lost",
        ]
        rows = store.list_timeline(
            workspace,
            "crm_opportunities",
            deal,
            record_type="opportunity",
            related={"tasks": [task]},
        )
        assert [r["action"] for r in rows] == [
            "task.updated",
            "opportunity.stage_changed",
            "opportunity_contact.created",
            "task_link.created",
            "task.created",
            "opportunity.created",
        ]
