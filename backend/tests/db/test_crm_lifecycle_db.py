"""A Labs deal's TVA side in the real database — spec 007 FR-CRM-TVA-01 to
-18, FR-CRM-PIPE-13, -15, FR-CRM-DEL-02, -03, FR-CRM-WS-05, FR-CRM-AUD-04;
owner decisions D5, D8; data-model §5.4, §5.5 re-checks 3 to 6, §5.6;
migrations 032, 033.

What only Postgres can prove: each project action of `crm_deal_transition`
writes what it should and nothing else, in the move's transaction; a clash
raised inside the function rolls the move back; the plan's project id is
never trusted (`archive_release` touches only the deal's own project and its
unreleased rows; `unarchive_unrelease` only the rows this deal released);
`retentative`, `link` and revenue are re-checked against the database;
`crm_link_project` re-verifies its target; a project action outside the
TVA-linked workspace is refused; `crm_purge` clears a purged company's
project link and refuses a deal with a project. The S5 project reads of
`services/supabase.py` run against the real API too.
"""

from __future__ import annotations

import json
import uuid

import pytest
from supabase import create_client

from app.services import supabase as db

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}
NEXT = {"next_step": "Call", "next_step_on": "2026-10-20"}
MONEY = {
    "amount": "10000.00",
    "currency": "INR",
    "amount_in_inr": "10000.00",
    "fx_rate_used": "1",
    "fx_month": "2026-10-01",
    "expected_close_on": "2026-12-15",
}


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


def _move_sql(stack, actor: dict, plan: dict) -> str:
    return f"SELECT crm_deal_transition({_j(stack, actor)}, {_j(stack, plan)}) AS r"


def _move(stack, actor: dict, plan: dict) -> dict:
    return stack.sql(_move_sql(stack, actor, plan))[0]["r"]


def _link_sql(stack, actor: dict, plan: dict) -> str:
    return f"SELECT crm_link_project({_j(stack, actor)}, {_j(stack, plan)}) AS r"


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _one(stack, query: str) -> dict:
    return stack.sql(query)[0]


def _workspace(stack, *, tva_linked: bool) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name, tva_linked) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}, {str(tva_linked).lower()}) "
        "RETURNING id",
    )[-1]["id"]


def _member(stack, workspace: str, user: str, role: str = "admin") -> None:
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(user)}, {stack.q(role)})",
    )


@pytest.fixture
def manager(make_profile) -> str:
    """A staff TVA manager."""
    return make_profile(keep=True, role="manager")


@pytest.fixture
def seller(make_profile) -> str:
    """A staff TVA user: no project rights of their own."""
    return make_profile(keep=True, role="user")


@pytest.fixture
def labs(stack, manager, seller) -> str:
    """The seeded Nunnari Labs workspace: only one may be TVA-linked (026)."""
    ws = stack.sql("SELECT id FROM crm_workspaces WHERE tva_linked")[0]["id"]
    _member(stack, ws, manager, "admin")
    _member(stack, ws, seller, "member")
    return ws


def _name(prefix: str = "Acme") -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def _deal(stack, ws: str, owner: str, *, stage: str = "discovery", **extra) -> tuple[str, str]:
    company = _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_companies",
                "values": {"name": _name("Co"), "owner_id": owner, "lead_stage": "deal_open"},
            }
        ],
    )[0]["id"]
    values = {
        "name": _name("Deal"),
        "company_id": company,
        "owner_id": owner,
        "probability": 10,
        **NEXT,
        **(MONEY if stage != "discovery" else {}),
        **extra,
    }
    deal = _apply(
        stack,
        _actor(ws, owner),
        [{"op": "insert", "table": "crm_opportunities", "values": values}],
    )[0]["id"]
    if stage != "discovery":
        _as_system(
            stack,
            f"UPDATE crm_opportunities SET stage = {stack.q(stage)} WHERE id = {stack.q(deal)}",
        )
    return deal, company


def _row(stack, table: str, row_id: str) -> dict:
    return _one(stack, f"SELECT * FROM {table} WHERE id = {stack.q(row_id)}")


def _plan(stack, deal: str, to: str | None = None, **extra) -> dict:
    row = _row(stack, "crm_opportunities", deal)
    plan: dict = {
        "opportunity_id": deal,
        "expect_stage": row["stage"],
        "expect_updated_at": row["updated_at"],
        "opportunity": extra.pop("values", {}),
        **extra,
    }
    if to:
        plan["opportunity"] = {"stage": to, "stage_entered_at": "now", **plan["opportunity"]}
        plan["history"] = {"from": row["stage"], "to": to}
    return plan


def _project(stack, name: str | None = None, **values) -> str:
    columns = {"name": name or _name("Project"), "status": "tentative", **values}
    names = ", ".join(columns)
    vals = ", ".join(
        "NULL" if v is None else str(v).lower() if isinstance(v, bool) else stack.q(str(v))
        for v in columns.values()
    )
    return stack.sql(f"INSERT INTO projects ({names}) VALUES ({vals}) RETURNING id")[0]["id"]


def _project_of(stack, deal: str) -> dict | None:
    rows = stack.sql(f"SELECT * FROM projects WHERE source_opportunity_id = {stack.q(deal)}")
    return rows[0] if rows else None


def _allocation(stack, project: str, user: str, **values) -> str:
    columns = {
        "project_id": project,
        "user_id": user,
        "starts_on": "2026-11-02",
        "ends_on": "2026-12-31",
        "percent": "50",
        **values,
    }
    names = ", ".join(columns)
    vals = ", ".join(stack.q(str(v)) for v in columns.values())
    return stack.sql(f"INSERT INTO allocations ({names}) VALUES ({vals}) RETURNING id")[0]["id"]


def _tva_audit(stack, target: str) -> list[dict]:
    return stack.sql(
        "SELECT actor_id, actor_label, workspace_id, action FROM audit_log "
        f"WHERE target_id = {stack.q(target)} AND workspace_id IS NULL ORDER BY id"
    )


class TestCreate:
    """FR-CRM-TVA-04, -05, re-check 4."""

    def test_proposal_creates_the_tentative_project_as_the_system(
        self, stack, labs, manager, seller
    ):
        deal, company = _deal(stack, labs, seller, stage="qualification")
        name = _name("Acme - Phase 1")
        done = _move(
            stack,
            _actor(labs, seller),
            _plan(
                stack,
                deal,
                "proposal",
                values={"probability": 50},
                project={"action": "create", "name": name, "category": "poc", "probability": 50},
            ),
        )
        project = _project_of(stack, deal)
        assert done["project"]["id"] == project["id"]
        assert (project["name"], project["status"], project["probability"]) == (
            name,
            "tentative",
            50,
        )
        assert project["crm_company_id"] == company
        assert project["category"] == "poc"
        assert project["client"] == _row(stack, "crm_companies", company)["name"]
        assert _tva_audit(stack, project["id"]) == [
            {
                "actor_id": seller,
                "actor_label": "system",
                "workspace_id": None,
                "action": "project.created",
            }
        ]

    def test_a_deal_with_a_project_keeps_it(self, stack, labs, seller):
        deal, _ = _deal(stack, labs, seller, stage="qualification")
        first = _name()
        _move(
            stack,
            _actor(labs, seller),
            _plan(stack, deal, "proposal", project={"action": "create", "name": first}),
        )
        _move(
            stack,
            _actor(labs, seller),
            _plan(stack, deal, "qualification", project={"action": "create", "name": _name()}),
        )
        assert _project_of(stack, deal)["name"] == first

    def test_a_clash_inside_the_function_rolls_back_the_move(self, stack, labs, seller):
        existing = _name("Clash")
        _project(stack, existing.upper() + "  ")
        deal, _ = _deal(stack, labs, seller, stage="qualification")
        message = _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, seller),
                _plan(stack, deal, "proposal", project={"action": "create", "name": existing}),
            ),
            "CRM09",
        )
        assert message == f"A project called “{existing.upper()}  ” already exists."
        assert _row(stack, "crm_opportunities", deal)["stage"] == "qualification"
        assert _project_of(stack, deal) is None

    def test_outside_a_tva_linked_workspace_any_project_action_is_refused(self, stack, manager):
        academy = _workspace(stack, tva_linked=False)
        _member(stack, academy, manager)
        deal, _ = _deal(stack, academy, manager, stage="qualification")
        message = _refused(
            stack,
            _move_sql(
                stack,
                _actor(academy, manager),
                _plan(stack, deal, "proposal", project={"action": "create", "name": _name()}),
            ),
            "CRM22",
        )
        assert message == "This workspace is not connected to TVA projects."
        assert _row(stack, "crm_opportunities", deal)["stage"] == "qualification"


class TestLinkAndRevenueAreForManagers:
    """Re-checks 5 and 6 (FR-CRM-TVA-05, -08, D8)."""

    def test_link_by_a_manager(self, stack, labs, manager):
        target = _project(stack, probability=20)
        deal, company = _deal(stack, labs, manager, stage="qualification")
        _move(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "proposal",
                values={"probability": 50},
                project={"action": "link", "project_id": target, "probability": 50},
            ),
        )
        linked = _row(stack, "projects", target)
        assert (linked["source_opportunity_id"], linked["crm_company_id"]) == (deal, company)
        assert linked["probability"] == 50

    def test_link_by_anyone_else_writes_nothing(self, stack, labs, seller):
        target = _project(stack)
        deal, _ = _deal(stack, labs, seller, stage="qualification")
        message = _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, seller),
                _plan(stack, deal, "proposal", project={"action": "link", "project_id": target}),
            ),
            "CRM22",
        )
        assert message == "Ask a TVA manager to link it."
        assert _row(stack, "projects", target)["source_opportunity_id"] is None
        assert _row(stack, "crm_opportunities", deal)["stage"] == "qualification"

    @pytest.mark.parametrize("shape", [{"status": "confirmed"}, {"is_archived": True}])
    def test_link_only_to_an_unlinked_tentative_unarchived_project(
        self, stack, labs, manager, shape
    ):
        target = _project(stack, **shape)
        deal, _ = _deal(stack, labs, manager, stage="qualification")
        _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, manager),
                _plan(stack, deal, "proposal", project={"action": "link", "project_id": target}),
            ),
            "CRM09",
        )

    def _confirm(self, **extra) -> dict:
        return {
            "action": "confirm",
            "name": _name("Won"),
            "category": "client",
            "probability": 100,
            "phases": {"delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}},
            **extra,
        }

    def test_confirm_with_revenue_from_a_tva_user_writes_nothing(self, stack, labs, seller):
        deal, _ = _deal(stack, labs, seller, stage="negotiation", proposal_sent_on="2026-10-01")
        for extra in (
            {"revenue": "840000.00"},
            {"milestones": [{"name": "M1", "due_on": "2026-11-02", "amount": "1"}]},
        ):
            message = _refused(
                stack,
                _move_sql(
                    stack,
                    _actor(labs, seller),
                    _plan(
                        stack,
                        deal,
                        "closed_won",
                        values={"probability": 100, "won_on": "2026-10-14"},
                        project=self._confirm(**extra),
                    ),
                ),
                "CRM22",
            )
            assert message == "Only a TVA manager or admin can set revenue and milestones."
        assert _project_of(stack, deal) is None
        assert _row(stack, "crm_opportunities", deal)["stage"] == "negotiation"

    def test_confirm_by_a_manager_writes_revenue_milestones_and_phases(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager, stage="negotiation", proposal_sent_on="2026-10-01")
        lead = manager
        done = _move(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "closed_won",
                values={"probability": 100, "won_on": "2026-10-14"},
                project=self._confirm(
                    lead_id=lead,
                    revenue="840000.00",
                    milestones=[
                        {"name": "Kickoff", "due_on": "2026-11-02", "amount": "420000.00"},
                        {"name": "Go-live", "due_on": "2027-01-29", "amount": "420000.00"},
                    ],
                ),
            ),
        )
        project = _project_of(stack, deal)
        assert done["project"]["status"] == "confirmed"
        assert (project["status"], float(project["revenue"]), project["lead_id"]) == (
            "confirmed",
            840000.0,
            lead,
        )
        milestones = stack.sql(
            f"SELECT name FROM project_milestones WHERE project_id = {stack.q(project['id'])} "
            "ORDER BY due_on"
        )
        assert [m["name"] for m in milestones] == ["Kickoff", "Go-live"]
        phases = stack.sql(
            f"SELECT phase FROM project_phases WHERE project_id = {stack.q(project['id'])}"
        )
        assert phases == [{"phase": "delivery"}]
        handoff = stack.sql(
            "SELECT workspace_id, action FROM audit_log "
            f"WHERE target_id = {stack.q(deal)} AND action = 'opportunity.handoff'"
        )
        assert handoff == [{"workspace_id": labs, "action": "opportunity.handoff"}]

    def test_confirm_updates_the_deals_tentative_project(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager, stage="qualification")
        _move(
            stack,
            _actor(labs, manager),
            _plan(stack, deal, "proposal", project={"action": "create", "name": _name()}),
        )
        tentative = _project_of(stack, deal)
        stack.sql(
            "INSERT INTO project_phases (project_id, phase, starts_on, ends_on) "
            f"VALUES ({stack.q(tentative['id'])}, 'delivery', '2026-10-01', '2026-10-31')"
        )
        renamed = _name("Renamed")
        _move(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "closed_won",
                values={"probability": 100, "won_on": "2026-10-14"},
                project=self._confirm(name=renamed, category="product", lead_id=manager),
            ),
        )
        project = _project_of(stack, deal)
        assert project["id"] == tentative["id"]
        assert (project["name"], project["category"], project["status"], project["lead_id"]) == (
            renamed,
            "product",
            "confirmed",
            manager,
        )
        phases = stack.sql(
            "SELECT phase, starts_on, ends_on FROM project_phases "
            f"WHERE project_id = {stack.q(project['id'])}"
        )
        # One per kind: the existing delivery phase is updated, not doubled.
        assert phases == [{"phase": "delivery", "starts_on": "2026-11-02", "ends_on": "2027-01-29"}]

    def test_confirm_refuses_spillover(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager, stage="negotiation", proposal_sent_on="2026-10-01")
        plan = self._confirm()
        plan["phases"]["spillover"] = {"starts_on": "2027-02-01", "ends_on": "2027-02-28"}
        _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, manager),
                _plan(stack, deal, "closed_won", values={"probability": 100}, project=plan),
            ),
            "CRM22",
        )
        assert _project_of(stack, deal) is None


class TestConfirmMilestonesAndAdoption:
    """The confirm's milestones are the project's full set, matched by id, so
    the pre-fill sent back adds nothing (FR-CRM-TVA-07, -08); a confirm may
    adopt an existing tentative project (FR-CRM-TVA-10 with -05)."""

    def _tentative_with_milestones(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager, stage="qualification")
        _move(
            stack,
            _actor(labs, manager),
            _plan(stack, deal, "proposal", project={"action": "create", "name": _name()}),
        )
        project = _project_of(stack, deal)["id"]
        stack.sql(
            "INSERT INTO project_milestones (project_id, name, due_on, amount) VALUES "
            f"({stack.q(project)}, 'Kickoff', '2026-11-02', 420000), "
            f"({stack.q(project)}, 'Go-live', '2027-01-29', 420000)"
        )
        return deal, project

    def _milestones(self, stack, project: str) -> list[dict]:
        return stack.sql(
            "SELECT id, name, due_on, amount::text AS amount FROM project_milestones "
            f"WHERE project_id = {stack.q(project)} ORDER BY due_on"
        )

    def _won(self, stack, labs, manager, deal, **project):
        return _move(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "closed_won",
                values={"probability": 100, "won_on": "2026-10-14"},
                project={
                    "action": "confirm",
                    "category": "client",
                    "lead_id": manager,
                    "probability": 100,
                    "phases": {"delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}},
                    **project,
                },
            ),
        )

    def test_the_prefilled_milestones_sent_back_keep_one_copy(self, stack, labs, manager):
        deal, project = self._tentative_with_milestones(stack, labs, manager)
        before = self._milestones(stack, project)
        self._won(stack, labs, manager, deal, milestones=before)
        assert self._milestones(stack, project) == before
        actions = {a["action"] for a in _tva_audit(stack, before[0]["id"])}
        assert actions == set()

    def test_left_out_is_removed_changed_is_updated_new_is_added(self, stack, labs, manager):
        deal, project = self._tentative_with_milestones(stack, labs, manager)
        kickoff, golive = self._milestones(stack, project)
        self._won(
            stack,
            labs,
            manager,
            deal,
            milestones=[
                {**kickoff, "amount": "500000.00"},
                {"name": "Support", "due_on": "2027-03-01", "amount": "340000.00"},
            ],
        )
        after = self._milestones(stack, project)
        assert [(m["name"], m["amount"]) for m in after] == [
            ("Kickoff", "500000.00"),
            ("Support", "340000.00"),
        ]
        assert after[0]["id"] == kickoff["id"]
        assert [a["action"] for a in _tva_audit(stack, golive["id"])] == ["milestone.removed"]
        assert [a["action"] for a in _tva_audit(stack, kickoff["id"])] == ["milestone.updated"]

    def test_an_invoiced_milestone_is_never_removed_or_changed(self, stack, labs, manager):
        deal, project = self._tentative_with_milestones(stack, labs, manager)
        kickoff, golive = self._milestones(stack, project)
        stack.sql(
            "UPDATE project_milestones SET invoiced_on = '2026-10-01' "
            f"WHERE id = {stack.q(golive['id'])}"
        )
        for milestones, said in (
            ([kickoff], "“Go-live” is invoiced, so the handoff cannot remove it."),
            (
                [kickoff, {**golive, "amount": "1.00"}],
                "“Go-live” is invoiced, so the handoff cannot change it.",
            ),
        ):
            message = _refused(
                stack,
                _move_sql(
                    stack,
                    _actor(labs, manager),
                    _plan(
                        stack,
                        deal,
                        "closed_won",
                        values={"probability": 100},
                        project={
                            "action": "confirm",
                            "lead_id": manager,
                            "probability": 100,
                            "milestones": milestones,
                        },
                    ),
                ),
                "CRM22",
            )
            assert message == said
        assert len(self._milestones(stack, project)) == 2
        assert _row(stack, "crm_opportunities", deal)["stage"] == "proposal"

    def test_a_milestone_of_another_project_is_refused(self, stack, labs, manager):
        deal, _ = self._tentative_with_milestones(stack, labs, manager)
        other = _project(stack)
        stray = stack.sql(
            "INSERT INTO project_milestones (project_id, name, due_on, amount) "
            f"VALUES ({stack.q(other)}, 'Stray', '2026-12-01', 1) RETURNING id"
        )[0]["id"]
        _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, manager),
                _plan(
                    stack,
                    deal,
                    "closed_won",
                    values={"probability": 100},
                    project={
                        "action": "confirm",
                        "lead_id": manager,
                        "probability": 100,
                        "milestones": [
                            {"id": stray, "name": "X", "due_on": "2026-12-01", "amount": "2"}
                        ],
                    },
                ),
            ),
            "CRM04",
        )
        assert _row(stack, "project_milestones", stray)["name"] == "Stray"

    def test_an_empty_list_from_a_tva_user_is_refused(self, stack, labs, manager, seller):
        deal, project = self._tentative_with_milestones(stack, labs, manager)
        _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, seller),
                _plan(
                    stack,
                    deal,
                    "closed_won",
                    values={"probability": 100},
                    project={"action": "confirm", "lead_id": manager, "milestones": []},
                ),
            ),
            "CRM22",
        )
        assert len(self._milestones(stack, project)) == 2

    def test_a_manager_adopts_a_project_at_the_handoff(self, stack, labs, manager):
        target = _project(stack, probability=30)
        deal, company = _deal(
            stack, labs, manager, stage="qualification", proposal_sent_on="2026-10-01"
        )
        self._won(stack, labs, manager, deal, project_id=target)
        adopted = _row(stack, "projects", target)
        assert (adopted["source_opportunity_id"], adopted["crm_company_id"]) == (deal, company)
        assert adopted["status"] == "confirmed"

    def test_adopting_by_anyone_else_writes_nothing(self, stack, labs, seller):
        target = _project(stack)
        deal, _ = _deal(stack, labs, seller, stage="qualification")
        message = _refused(
            stack,
            _move_sql(
                stack,
                _actor(labs, seller),
                _plan(
                    stack,
                    deal,
                    "closed_won",
                    values={"probability": 100},
                    project={"action": "confirm", "project_id": target, "lead_id": seller},
                ),
            ),
            "CRM22",
        )
        assert message == "Ask a TVA manager to link it."
        assert _row(stack, "projects", target)["source_opportunity_id"] is None


class TestProjectFollowsCompany:
    """FR-CRM-TVA-01, -02: whatever write moves a deal to another company,
    its project's company and client follow, with a TVA audit row."""

    def test_an_edit_moves_the_project(self, stack, labs, manager):
        deal, old = _deal(stack, labs, manager, stage="qualification")
        _move(
            stack,
            _actor(labs, manager),
            _plan(stack, deal, "proposal", project={"action": "create", "name": _name()}),
        )
        new_name = _name("Globex")
        new = _apply(
            stack,
            _actor(labs, manager),
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "values": {"name": new_name, "owner_id": manager, "lead_stage": "deal_open"},
                }
            ],
        )[0]["id"]
        _apply(
            stack,
            _actor(labs, manager),
            [
                {
                    "op": "update",
                    "table": "crm_opportunities",
                    "id": deal,
                    "values": {"company_id": new},
                }
            ],
        )
        project = _project_of(stack, deal)
        assert (project["crm_company_id"], project["client"]) == (new, new_name)
        moved = stack.sql(
            "SELECT actor_id, actor_label, after FROM audit_log "
            f"WHERE target_id = {stack.q(project['id'])} AND workspace_id IS NULL "
            "AND after ? 'crm_company_id' AND after ->> 'crm_company_id' = "
            f"{stack.q(new)}"
        )
        assert [(m["actor_id"], m["actor_label"]) for m in moved] == [(manager, "system")]
        assert old != new


class TestLostAndReopen:
    """FR-CRM-TVA-12 to -14, PIPE-13, D5; re-check 4."""

    def _with_project(self, stack, labs, owner):
        deal, _ = _deal(stack, labs, owner, stage="qualification")
        _move(
            stack,
            _actor(labs, owner),
            _plan(stack, deal, "proposal", project={"action": "create", "name": _name()}),
        )
        return deal, _project_of(stack, deal)["id"]

    def _lose(self, stack, labs, owner, deal, **project):
        # The seeded reason: a test adds no options to the real workspace.
        reason = _one(
            stack,
            "SELECT id FROM crm_select_options WHERE list_key = 'opportunity.lost_reason' "
            f"AND system_key = 'price' AND workspace_id = {stack.q(labs)}",
        )["id"]
        return _move(
            stack,
            _actor(labs, owner),
            _plan(
                stack,
                deal,
                "closed_lost",
                values={
                    "probability": 0,
                    "lost_reason_id": reason,
                    "lost_on": "2026-10-14",
                    "lost_from_stage": "proposal",
                },
                expect_siblings={"open": 0, "won": False},
                project={"action": "archive_release", "reason": "Deal lost: Price", **project},
            ),
        )

    def test_archive_release_touches_only_its_own_projects_unreleased_rows(
        self, stack, labs, manager, seller
    ):
        deal, project = self._with_project(stack, labs, manager)
        mine = _allocation(stack, project, seller)
        earlier = _allocation(
            stack,
            project,
            manager,
            released_at="2026-09-01T00:00:00+00:00",
            release_reason="Earlier",
        )
        bystander = _project(stack)
        theirs = _allocation(stack, bystander, seller)
        # The plan names another project: ignored (re-check 4).
        done = self._lose(stack, labs, manager, deal, project_id=bystander)
        assert done["allocations_released"] == 1
        row = _row(stack, "allocations", mine)
        assert (row["released_by"], row["release_reason"], row["released_by_opportunity_id"]) == (
            manager,
            "Deal lost: Price",
            deal,
        )
        assert _row(stack, "allocations", earlier)["release_reason"] == "Earlier"
        assert _row(stack, "allocations", theirs)["released_at"] is None
        assert _row(stack, "projects", bystander)["is_archived"] is False
        archived = _row(stack, "projects", project)
        assert archived["is_archived"] is True and archived["crm_archived_at"] is not None
        actions = [r["action"] for r in _tva_audit(stack, mine)]
        assert actions == ["allocation.released"]

    def test_unarchive_unrelease_restores_only_that_deals_rows(self, stack, labs, manager, seller):
        deal, project = self._with_project(stack, labs, manager)
        mine = _allocation(stack, project, seller)
        self._lose(stack, labs, manager, deal)
        # Released by another deal's loss: not this reopen's to restore.
        other_deal, _ = _deal(stack, labs, manager)
        other = _allocation(
            stack,
            project,
            manager,
            released_at="2026-10-01T00:00:00+00:00",
            release_reason="Other",
            released_by_opportunity_id=other_deal,
        )
        done = _move(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "proposal",
                values={
                    "probability": 50,
                    "lost_on": None,
                    "lost_reason_id": None,
                    "lost_from_stage": None,
                    **NEXT,
                },
                project={"action": "unarchive_unrelease", "probability": 50},
            ),
        )
        assert done["allocations_restored"] == 1
        assert _row(stack, "allocations", mine)["released_at"] is None
        assert _row(stack, "allocations", other)["released_at"] is not None
        restored = _row(stack, "projects", project)
        assert (restored["is_archived"], restored["crm_archived_at"]) == (False, None)
        assert restored["probability"] == 50

    def _won(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager, stage="negotiation", proposal_sent_on="2026-10-01")
        _move(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "closed_won",
                values={"probability": 100, "won_on": "2026-10-14"},
                project={
                    "action": "confirm",
                    "name": _name("Won"),
                    "probability": 100,
                    "revenue": "500000.00",
                    "phases": {"delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}},
                },
            ),
        )
        return deal, _project_of(stack, deal)["id"]

    def _reopen_sql(self, stack, labs, manager, deal):
        return _move_sql(
            stack,
            _actor(labs, manager),
            _plan(
                stack,
                deal,
                "negotiation",
                values={"probability": 75, "won_on": None},
                project={"action": "retentative", "probability": 75},
            ),
        )

    def test_retentative_refused_with_time_logged(self, stack, labs, manager):
        deal, project = self._won(stack, labs, manager)
        stack.sql(
            "INSERT INTO time_entries (user_id, date, project_id, hours_office) "
            f"VALUES ({stack.q(manager)}, '2026-11-03', {stack.q(project)}, 4)"
        )
        message = _refused(stack, self._reopen_sql(stack, labs, manager, deal), "CRM22")
        assert message.endswith("has time logged against it, so it cannot be made tentative.")
        assert _row(stack, "crm_opportunities", deal)["stage"] == "closed_won"
        assert _row(stack, "projects", project)["status"] == "confirmed"

    def test_retentative_refused_with_an_invoiced_milestone(self, stack, labs, manager):
        deal, project = self._won(stack, labs, manager)
        stack.sql(
            "INSERT INTO project_milestones (project_id, name, due_on, amount, invoiced_on) "
            f"VALUES ({stack.q(project)}, 'M1', '2026-11-02', 1, '2026-11-03')"
        )
        message = _refused(stack, self._reopen_sql(stack, labs, manager, deal), "CRM22")
        assert message.endswith("has invoiced milestones, so it cannot be made tentative.")

    def test_retentative_keeps_revenue(self, stack, labs, manager):
        deal, project = self._won(stack, labs, manager)
        stack.sql(self._reopen_sql(stack, labs, manager, deal))
        row = _row(stack, "projects", project)
        assert (row["status"], float(row["revenue"]), row["probability"]) == (
            "tentative",
            500000.0,
            75,
        )


class TestLinkProject:
    """Data-model §5.6, FR-CRM-TVA-18."""

    def _plan(self, stack, deal: str, **extra) -> dict:
        row = _row(stack, "crm_opportunities", deal)
        return {"opportunity_id": deal, "expect_updated_at": row["updated_at"], **extra}

    def test_a_won_deal_links_its_confirmed_project_unchanged(self, stack, labs, manager):
        deal, company = _deal(stack, labs, manager, stage="closed_won")
        target = _project(stack, status="confirmed", revenue="900000", probability=None)
        done = stack.sql(
            _link_sql(
                stack,
                _actor(labs, manager),
                self._plan(stack, deal, mode="link", project_id=target),
            )
        )[0]["r"]
        row = _row(stack, "projects", target)
        assert done["project"]["id"] == target
        assert (row["source_opportunity_id"], row["crm_company_id"]) == (deal, company)
        assert (row["status"], float(row["revenue"]), row["probability"]) == (
            "confirmed",
            900000.0,
            None,
        )
        linked = stack.sql(
            "SELECT workspace_id FROM audit_log "
            f"WHERE target_id = {stack.q(deal)} AND action = 'opportunity.project_linked'"
        )
        assert linked == [{"workspace_id": labs}]

    def test_the_target_is_reverified(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager, stage="proposal")
        taken_by, _ = _deal(stack, labs, manager)
        for target in (
            _project(stack, status="confirmed"),
            _project(stack, is_archived=True),
            _project(stack, source_opportunity_id=taken_by),
        ):
            _refused(
                stack,
                _link_sql(
                    stack,
                    _actor(labs, manager),
                    self._plan(stack, deal, mode="link", project_id=target),
                ),
                "CRM09",
            )
        assert _project_of(stack, deal) is None

    def test_link_mode_needs_a_tva_manager_and_a_workspace_admin(
        self, stack, labs, manager, seller
    ):
        deal, _ = _deal(stack, labs, manager, stage="proposal")
        target = _project(stack)
        # Seller is a Labs member, not an admin.
        message = _refused(
            stack,
            _link_sql(
                stack,
                _actor(labs, seller),
                self._plan(stack, deal, mode="create", name=_name()),
            ),
            "CRM22",
        )
        assert message == "Only a workspace admin can link a TVA project."
        _as_system(
            stack,
            f"UPDATE crm_memberships SET role = 'admin' WHERE workspace_id = {stack.q(labs)} "
            f"AND user_id = {stack.q(seller)}",
        )
        message = _refused(
            stack,
            _link_sql(
                stack,
                _actor(labs, seller),
                self._plan(stack, deal, mode="link", project_id=target),
            ),
            "CRM22",
        )
        assert message == "Ask a TVA manager to link it."
        # A Labs admin who is a TVA user may still create one.
        stack.sql(
            _link_sql(
                stack, _actor(labs, seller), self._plan(stack, deal, mode="create", name=_name())
            )
        )
        assert _project_of(stack, deal)["status"] == "tentative"

    def test_only_from_proposal_on(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager)
        _refused(
            stack,
            _link_sql(stack, _actor(labs, manager), self._plan(stack, deal, mode="create")),
            "CRM22",
        )


class TestPurge:
    """Data-model §5.4, 033 form; FR-CRM-DEL-02, -03."""

    def _purge_sql(self, stack, labs, owner, table, row_id):
        plan = {"items": [{"table": table, "id": row_id}]}
        return f"SELECT crm_purge({_j(stack, _actor(labs, owner))}, {_j(stack, plan)}) AS r"

    def test_purging_a_company_clears_its_projects_link(self, stack, labs, manager):
        _, company = _deal(stack, labs, manager)
        project = _project(stack, client="Acme", crm_company_id=company)
        _as_system(
            stack,
            f"DELETE FROM crm_opportunities WHERE company_id = {stack.q(company)}",
        )
        _apply(
            stack,
            _actor(labs, manager),
            [{"op": "soft_delete", "table": "crm_companies", "id": company}],
        )
        stack.sql(self._purge_sql(stack, labs, manager, "crm_companies", company))
        row = _row(stack, "projects", project)
        assert (row["crm_company_id"], row["client"]) == (None, "Acme")
        assert [r["action"] for r in _tva_audit(stack, project)] == ["project.updated"]

    def test_a_deal_with_a_project_is_never_purged(self, stack, labs, manager):
        deal, _ = _deal(stack, labs, manager)
        _project(stack, source_opportunity_id=deal)
        _apply(
            stack,
            _actor(labs, manager),
            [{"op": "soft_delete", "table": "crm_opportunities", "id": deal}],
        )
        message = _refused(
            stack, self._purge_sql(stack, labs, manager, "crm_opportunities", deal), "CRM22"
        )
        assert message == "This deal has a TVA project; mark it Closed Lost instead."


class TestProjectReads:
    """The S5 reads of services/supabase.py against the real API."""

    def test_the_crm_project_reads(self, stack, labs, manager, monkeypatch):
        monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))
        deal, company = _deal(stack, labs, manager)
        name = _name("Read_100%")
        linked = _project(stack, name, source_opportunity_id=deal, crm_company_id=company)
        free = _project(stack, client="Free Ltd")
        _project(stack, is_archived=True)
        stack.sql(
            "INSERT INTO project_phases (project_id, phase, starts_on, ends_on) "
            f"VALUES ({stack.q(linked)}, 'delivery', '2026-11-02', '2027-01-29')"
        )
        assert db.get_project_by_source_opportunity(deal)["id"] == linked
        assert [p["id"] for p in db.list_crm_projects(opportunity_ids=[deal])] == [linked]
        assert [p["id"] for p in db.list_crm_projects(company_ids=[company])] == [linked]
        assert db.find_project_by_name(f"  {name.upper()} ")["id"] == linked
        assert db.find_project_by_name(name[:-1]) is None
        linkable = {p["id"] for p in db.list_linkable_projects("tentative")}
        assert free in linkable and linked not in linkable
        assert [p["phase"] for p in db.list_project_phases([linked])] == ["delivery"]
        assert db.project_has_time_entries(linked) is False
        assert db.list_project_milestones(linked) == []
