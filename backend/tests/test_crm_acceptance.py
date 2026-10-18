"""Spec 007 §11, line by line — NFR-CRM-10 ("every acceptance criterion in
§11 has a test").

One test per line of §11, read from the spec itself, so the list cannot drift
from the suite: a line added, changed or removed in the spec fails
`test_every_line_is_mapped` until this file follows. Each line's test either
runs its scenario end to end through the routes or the tools (with the fake
store), or asserts that the tests named for it exist and are not skipped,
where those tests already run the scenario.

A named test counts only if it runs in the default suite: no skip or xfail
mark on it, its class or its module, no `pytest.skip(` in its body, and not a
real-database test (`tests/db/`, skipped unless `CRM_DB_TESTS=1`). The one
line about speed at 10,000 rows is proved by the load script's p95 figures
(quickstart §5), recorded in the S12 commit message; its test checks the
script is there and holds the 1-second bound, and that the database's own
10,000-row check exists.

The email lines run against the fake Gmail of `test_crm_email_sync.py`: the
service-account credential does not exist yet (Q-74), and the
switch keeps real sync inert until it does.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from crm_fake import OWNER, FakeCrmStore, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.mcp import server
from app.services.crm import deals
from app.services.crm import today as today_service
from app.services.crm import views as view_service

BACKEND = Path(__file__).resolve().parents[1]
SPEC = BACKEND.parent / "specs" / "007-crm" / "spec.md"


def _section_11() -> list[str]:
    text = SPEC.read_text()
    body = text.split("## 11. Acceptance criteria", 1)[1].split("\n## 12.", 1)[0]
    return [
        line[len("- [ ] ") :].strip() for line in body.splitlines() if line.startswith("- [ ] ")
    ]


#: §11 line (by how it starts) → the tests that prove it. "run here" means
#: this file runs the scenario itself, below.
COVERAGE: dict[str, list[str]] = {
    # Workspaces and access (1a)
    "No query, search, view, dashboard, export or MCP call returns data": [
        "test_crm_isolation_sweep.py::test_another_workspaces_id_is_not_found",
        "test_crm_isolation_sweep.py::TestAnswersForLabsHoldNothingOfAcademy::test_every_open_read",
        "test_crm_isolation_sweep.py::TestAnswersForLabsHoldNothingOfAcademy::test_every_post_read",
        "test_crm_isolation_sweep.py::TestThroughEveryTool::test_a_tool_naming_an_academy_record_in_labs",
        "test_crm_isolation_sweep.py::TestThroughEveryTool::test_a_read_tool_answers_nothing_of_academy",
        "test_crm_isolation.py::TestDashboardAndExport::test_exports_hold_nothing_of_it",
    ],
    "A CRM-only account sees only the CRM": [
        "test_crm_only_refused.py::test_crm_only_is_refused",
        "test_crm_only_refused.py::TestClassification::test_every_route_is_classified",
        "test_crm_only_refused.py::TestClassification::test_the_allow_list_is_exactly_contracts_section_1",
        "test_crm_accounts.py",
    ],
    "A CRM-only account appears in no TVA people list": [
        "test_crm_only_excluded.py::TestPopulations::test_bench",
        "test_crm_only_excluded.py::TestPopulations::test_resources",
        "test_crm_only_excluded.py::TestPopulations::test_utilisation",
        "test_crm_only_excluded.py::TestPopulations::test_team_day",
        "test_crm_only_excluded.py::TestPopulations::test_timesheet_population",
        "test_crm_only_excluded.py::TestPopulations::test_nothing_logged_nudge",
        "test_crm_only_excluded.py::TestPopulations::test_leadership_digest_and_its_coverage",
        "test_crm_only_excluded.py::TestPopulations::test_people_financials_and_ctc_missing",
        "test_crm_only_excluded.py::TestTheListing::test_profiles_leave_crm_only_out_by_default",
    ],
    "A Viewer receives no amount, weighted amount or pipeline value anywhere": [
        "test_crm_redaction_sweep.py::test_every_get_as_a_viewer",
        "test_crm_redaction_sweep.py::test_every_post_read_as_a_viewer",
        "test_crm_redaction_sweep.py::TestAmountsCannotBeAskedFor::test_every_amount_field_every_way",
        "test_crm_redaction_sweep.py::TestAmountsCannotBeAskedFor::test_through_crm_list",
        "test_crm_redaction_sweep.py::TestEveryReadTool::test_each_as_a_viewer",
        "test_crm_redaction.py::TestListsAndTotals::test_a_viewer_gets_none",
        "test_crm_redaction.py::TestBellAndDigests::test_the_bell",
        "test_crm_redaction.py::TestBellAndDigests::test_the_daily_digest",
        "test_crm_redaction.py::TestDashboardAndExport::test_a_viewer_is_refused",
    ],
    "A workspace Admin can invite only into their workspace": [
        "test_crm_invitations.py::TestWhoMayInviteWhere::test_a_workspace_admin_cannot_invite_into_a_workspace_they_are_not_in",
        "test_crm_invitations.py::TestWhoMayInviteWhere::test_an_address_off_the_allow_list_is_403_naming_the_domains",
        "test_crm_invitations.py::TestWhoMayInviteWhere::test_an_invitation_carries_no_tva_field",
        "test_crm_invitations.py::TestRemovingAndRoles::test_a_workspace_admin_cannot_touch_the_owner_or_a_tva_admin",
        "test_crm_invitations.py::TestWhoMayInviteWhere::test_a_tva_admin_invites_into_any_workspace_without_joining",
    ],
    # Pipeline rules (1a)
    "An open deal cannot be saved without next step and date": [
        "test_crm_deals_routes.py::TestCreate::test_an_open_deal_needs_a_next_step_and_date",
        "test_crm_deals_routes.py::TestEdit::test_clearing_the_next_step_is_refused",
        "test_crm_deals_routes.py::TestThroughMcp::test_crm_create_refuses_an_open_deal_without_a_next_step",
        "test_crm_deals_routes.py::TestImportHoldsTheNextStepRule::test_refused_without_the_fixup",
        "test_crm_views_routes.py::TestBulk::test_a_deal_save_still_needs_its_next_step",
        "test_crm_csv_io.py::TestRelationsAndRules::test_a_deal_follows_the_pipeline_rules",
    ],
    "A deal cannot be created for a company below Qualified": [
        "test_crm_deals_routes.py::TestCreate::test_a_company_below_qualified_takes_no_deal",
        "test_crm_deals_routes.py::TestCreate::test_creating_one_moves_a_qualified_company_to_deal_open",
    ],
    "Moving to Proposal without amount, currency and expected close fails": [
        "test_crm_deals_routes.py::TestMoves::test_proposal_without_its_fields_names_all_three",
        "test_crm_pipeline_rules.py::TestEntryRequirements::test_proposal_needs_amount_currency_and_close_date",
    ],
    "Closed Lost without a lost reason fails": [
        "test_crm_deals_routes.py::TestLost::test_lost_without_a_reason_is_refused",
        "test_crm_pipeline_rules.py::TestEntryRequirements::test_closed_lost_needs_its_reason",
    ],
    "Every stage change records its time": [
        "test_crm_deals_routes.py::TestMoves::test_every_move_writes_history_with_its_time",
        "test_crm_dashboard.py::TestTimeInStage::test_time_in_stage_is_correct_on_the_dashboard",
    ],
    "A non-INR amount stores amount in INR at the month's rate": [
        "test_crm_fx.py::TestStoredOnSave::test_usd_at_the_months_rate",
        "test_crm_fx.py::TestStoredOnSave::test_no_rate_is_refused_naming_it",
    ],
    # TVA integration (1a)
    "Moving a Labs deal to Proposal creates exactly one tentative project": [
        "test_crm_tva_lifecycle.py::TestProposal::test_proposal_creates_exactly_one_tentative_project",
        "test_crm_tva_lifecycle.py::TestProposal::test_probability_changes_sync_while_open",
        "test_capacity.py::TestDemand::test_confirmed_and_tentative_are_kept_apart",
    ],
    "A Proposal move whose name clashes with a project is refused": [
        "test_crm_tva_lifecycle.py::TestNameClash::test_a_clash_is_409_naming_it_for_a_tva_user",
        "test_crm_tva_lifecycle.py::TestNameClash::test_a_clash_proceeds_by_renaming_the_project",
        "test_crm_tva_lifecycle.py::TestNameClash::test_a_clash_offers_the_link_to_a_tva_manager",
    ],
    "Closed Won confirmed by a TVA manager or admin": [
        "test_crm_tva_lifecycle.py::TestClosedWon::test_won_as_a_manager_writes_inr_revenue_and_milestones",
        "test_pnl.py::TestAWonDealCountsOnce::test_the_won_project_leaves_the_pipeline_and_counts_once",
    ],
    "Closed Won by anyone else confirms the project without revenue": [
        "test_crm_tva_lifecycle.py::TestClosedWon::test_won_as_a_tva_user_confirms_without_revenue_and_tasks_the_admins",
    ],
    "Closed Lost archives the tentative project and releases its allocations": [
        "test_crm_tva_lifecycle.py::TestClosedLostAndReopen::test_lost_archives_and_releases_with_reason_and_person",
        "test_allocation_release_readers.py::TestEveryReaderIgnoresReleasedAndArchived::test_forecast",
        "test_allocation_release_readers.py::TestEveryReaderIgnoresReleasedAndArchived::test_resources_timeline",
        "test_allocation_release_readers.py::TestEveryReaderIgnoresReleasedAndArchived::test_bench",
        "test_allocation_release_readers.py::TestEveryReaderIgnoresReleasedAndArchived::test_hiring_signal",
        "test_allocation_release_readers.py::TestEveryReaderIgnoresReleasedAndArchived::test_monthly_profit_planned_cost",
        "test_pnl.py::TestArchivedProjectsInTheMonthlyProfit::test_an_archived_tentative_project_is_out_of_the_pipeline_block",
    ],
    "Reopening from Lost restores the project to tentative": [
        "test_crm_tva_lifecycle.py::TestClosedLostAndReopen::test_reopen_from_lost_restores_exactly_those_rows",
    ],
    "The Projects page and `update_project` refuse status and probability": [
        "test_crm_project_guard.py::TestRefusals::test_a_linked_tentative_project_refuses_what_the_deal_controls",
        "test_crm_project_guard.py::TestRefusals::test_the_mcp_tool_refuses_too",
        "test_crm_project_guard.py::TestRefusals::test_a_won_linked_project_archives_and_unarchives_as_today",
    ],
    "An imported Closed Won deal links to its existing confirmed project": [
        "test_crm_tva_lifecycle.py::TestLinkProject::test_an_imported_won_deal_links_to_its_confirmed_project_unchanged",
    ],
    "Deleting a company with a deal linked to a TVA project is refused": [
        "test_crm_delete_guards.py::TestCompany::test_a_company_with_a_linked_deal_is_refused_naming_it",
    ],
    "Academy deals never create TVA projects": [
        "test_crm_tva_lifecycle.py::TestAcademyAndAudit::test_academy_never_touches_projects",
        "test_crm_deals_routes.py::TestMoves::test_academy_goes_through_every_stage_with_no_project",
    ],
    # Views and data (1a; CSV 1b)
    "Table and kanban views with saved filters load in under 1 second": ["run here"],
    "The Twenty importer's dry run reports counts per object": [
        "test_crm_import_routes.py::TestDryRun::test_the_counts_match_the_fixture",
        "test_crm_import_routes.py::TestApply::test_the_links_are_reproduced_with_the_twenty_ids",
        "test_crm_import_routes.py::TestApply::test_a_second_run_changes_nothing",
        "test_crm_campaigns.py::TestTwentyImport::test_campaigns_members_and_attribution",
    ],
    "CSV import catches duplicate domains and emails before writing": [
        "test_crm_csv_io.py::TestDuplicates::test_domain_and_email_duplicates_are_caught_before_any_write",
        "test_crm_csv_io.py::TestDuplicates::test_the_real_run_is_refused_while_a_row_blocks",
        "test_crm_csv_io.py::TestRowActions::test_skip_update_and_create",
    ],
    "A CSV export downloads as a CSV file": [
        "test_crm_csv_io.py::TestExport::test_the_view_keeps_its_filters_and_columns_and_sets_the_headers",
    ],
    # Email and flags (1b) — the fake Gmail
    "A labelled email appears on the right person and company within 15 minutes": [
        "test_crm_email_sync.py::TestALabelledMessage::test_stored_on_the_right_person_and_company",
        "run here",
    ],
    "Running the sync twice creates no duplicates": [
        "test_crm_email_sync.py::TestALabelledMessage::test_a_second_run_stores_nothing",
    ],
    "An email from an unknown external domain with the Sales label": [
        "test_crm_email_sync.py::TestCreating::test_an_unknown_domain_creates_a_company_at_new_owned_by_the_mailbox_owner",
    ],
    "Internal-only emails never change flags": [
        "test_crm_email_sync.py::TestALabelledMessage::test_internal_only_is_skipped",
        "test_crm_flags.py::TestInternalAndAutoEmails::test_internal_mail_alone_changes_nothing_on_a_deal",
    ],
    "Reply-owed, client-silent and next-step flags match": [
        "test_crm_flags.py::TestReplyOwed::test_the_edges",
        "test_crm_flags.py::TestClientSilent::test_the_edges",
        "test_crm_flags.py::TestNextStep::test_the_edges",
    ],
    "Each daily digest is delivered exactly once per person per day": [
        "test_crm_digests.py::TestExactlyOnce::test_two_ticks_in_one_window_send_once",
        "test_crm_digests.py::TestExactlyOnce::test_each_in_their_own_window",
        "test_crm_digests.py::TestWindow::test_due_from_0900_to_the_end_of_the_day",
    ],
    # MCP (1a, 1b tools)
    "Claude can run the whole morning follow-up review": [
        "run here",
        "test_crm_mcp.py::TestTheFinalSet::test_exactly_the_twenty_one_crm_tools",
    ],
    "A write by a two-workspace member naming no workspace is refused": [
        "test_crm_mcp.py::TestThroughTheBridge::test_a_write_naming_no_workspace_lists_them",
        "test_crm_mcp.py::TestRecordTools::test_a_write_naming_no_workspace_lists_them",
        "test_crm_deals_routes.py",
    ],
}


def _key(line: str) -> str:
    [key] = [k for k in COVERAGE if line.startswith(k)]
    return key


def _ids() -> list[str]:
    return [re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-")[:60] for k in COVERAGE]


def test_every_line_is_mapped():
    """Each §11 line starts with exactly one key, and each key is a line."""
    lines = _section_11()
    assert len(lines) == 33
    for line in lines:
        assert len([k for k in COVERAGE if line.startswith(k)]) == 1, line
    assert {_key(line) for line in lines} == set(COVERAGE)


_SKIPS = {"skip", "skipif", "xfail", "db"}


def _marks(obj) -> set[str]:
    marks = getattr(obj, "pytestmark", [])
    if not isinstance(marks, list):
        marks = [marks]
    return {m.name for m in marks}


def _named(ref: str) -> None:
    """The test exists, and runs (is not skipped) in the default suite."""
    module_name, *parts = ref.split("::")
    path = BACKEND / "tests" / module_name
    assert path.is_file(), ref
    assert "/db/" not in str(path.relative_to(BACKEND)), f"{ref} needs CRM_DB_TESTS=1"
    tree = ast.parse(path.read_text())
    module = __import__(module_name.removesuffix(".py"))
    assert not (_marks(module) & _SKIPS), ref
    if not parts:
        # The whole module: it has tests, and none of them is skipped.
        found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert any(n.name.startswith("test_") for n in found), ref
        assert "pytest.skip(" not in path.read_text(), ref
        return
    obj = module
    for part in parts:
        obj = getattr(obj, part, None)
        assert obj is not None, f"{ref}: no {part}"
        assert not (_marks(obj) & _SKIPS), ref
    assert parts[-1].startswith("test_"), ref
    assert "pytest.skip(" not in inspect.getsource(obj), ref


@pytest.mark.parametrize("key", list(COVERAGE), ids=_ids())
def test_each_line_is_proved(key):
    named = [ref for ref in COVERAGE[key] if ref != "run here"]
    for ref in named:
        _named(ref)
    assert named or "run here" in COVERAGE[key], key


# -- Scenarios run here -------------------------------------------------------


def test_the_10k_check_holds_the_one_second_bound():
    """NFR-CRM-01; AC "Table and kanban views with saved filters load in under
    1 second for 10,000 companies". The figures are measured by the load
    script against the local stack (quickstart §5) and written in the S12
    commit message; the database's 10,000-row search check sits beside it."""
    script = BACKEND / "scripts" / "simulations" / "crm_load_10k.py"
    source = script.read_text()
    assert "BOUND_MS = 1000" in source
    for call in ("list", "kanban", "search", "record", "today"):
        assert f'"{call}"' in source, call
    assert "--cleanup" in source and "127.0.0.1" in source
    db_check = (BACKEND / "tests" / "db" / "test_crm_search_db.py").read_text()
    assert "def test_ten_thousand_companies_under_a_second" in db_check


def test_the_email_tick_runs_every_15_minutes():
    """AC "within 15 minutes": the sync tick is on the beat every quarter
    hour; the message itself is stored on the tick's run
    (test_crm_email_sync.py, against the fake Gmail)."""
    from app.worker import celery_app

    schedule = celery_app.conf.beat_schedule["crm-email-sync-tick"]["schedule"]
    assert schedule.minute == {0, 15, 30, 45}
    assert schedule.hour == set(range(24))


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


#: The tools that change something, as their annotations say.
WRITE_TOOLS = {
    t.name
    for t in asyncio.run(server.mcp.list_tools())
    if t.name.startswith("crm_") and not t.annotations.read_only_hint
}


def test_claude_runs_the_morning_review_and_a_deal_through_every_stage(monkeypatch):
    """AC "Claude can run the whole morning follow-up review, move a deal
    through every stage including the handoff, and add tasks and notes,
    entirely through NLDW, each write after confirmation." Every step below
    is a `crm_*` tool through the in-process bridge, as the owner in Labs;
    every write tool used says CONFIRM FIRST."""
    fake = FakeCrmStore().install(monkeypatch)
    labs, _ = two_workspaces(fake)
    fake.add_deal_lists(labs)
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    monkeypatch.setattr(view_service, "today_for", lambda user: date(2026, 10, 14))
    monkeypatch.setattr(today_service, "_now", lambda: datetime(2026, 10, 14, 5, 0, tzinfo=UTC))
    crm_client(monkeypatch, fake=fake)
    me = Ctx(OWNER["id"])
    used: list[str] = []

    def run(name: str, *args, **kwargs):
        used.append(name)
        return asyncio.run(getattr(tools, name)(me, *args, **kwargs, workspace="labs"))

    # Set the scene through the tools too.
    company = run("crm_create", "company", {"name": "Acme Robotics"})
    run("crm_update", "company", company["id"], {"lead_stage": "qualified"})
    person = run("crm_create", "person", {"first_name": "Priya", "company_id": company["id"]})
    deal = run(
        "crm_create",
        "opportunity",
        {
            "name": "Acme - Vision",
            "company_id": company["id"],
            "next_step": "Call Priya",
            "next_step_on": "2026-10-14",
        },
    )
    link = [{"record_type": "opportunity", "record_id": deal["id"]}]
    task = run("crm_add_task", "Send the deck", link, due_on="2026-10-14")

    # The morning review: what is due, then dealing with each line.
    morning = run("crm_today")
    due = {i.get("record_id") for s in morning["sections"] for i in s["items"]}
    assert {deal["id"], task["id"]} <= due
    run("crm_complete_task", task["id"])
    run(
        "crm_add_note",
        "Called Priya; she wants a proposal.",
        link,
        kind="call",
        occurred_on="2026-10-14",
    )
    run("crm_set_next_step", deal["id"], "Send the proposal", "2026-10-16")
    assert run("crm_today")["sections"]

    # Every stage, each previewed first.
    steps = [
        ("qualification", {"point_of_contact_id": person["id"]}),
        ("proposal", {"amount": "500000", "currency": "INR", "expected_close_on": "2026-12-15"}),
        ("negotiation", {"proposal_sent_on": "2026-10-16"}),
    ]
    for stage, changes in steps:
        preview = run("crm_move_stage", deal["id"], stage, changes=changes, preview=True)
        assert preview, stage
        moved = run("crm_move_stage", deal["id"], stage, changes=changes)
        assert moved["opportunity"]["stage"] == stage
    [project] = fake.rows["projects"].values()
    assert project["status"] == "tentative"

    # Closed Won is the handoff: preview, then confirm.
    handoff = run("crm_handoff", deal["id"])
    assert handoff
    run(
        "crm_handoff",
        deal["id"],
        "confirm",
        lead_id=OWNER["id"],
        phases={"delivery": {"starts_on": "2026-11-02", "ends_on": "2027-01-29"}},
        revenue="500000",
    )
    won = run("crm_get", "opportunity", deal["id"])
    assert won["record"]["stage"] == "closed_won"
    assert project["status"] == "confirmed"
    run(
        "crm_add_task",
        "Kick-off with Priya",
        [{"record_type": "company", "record_id": company["id"]}],
    )

    # Each write went through a tool that asks for confirmation first.
    descriptions = {t.name: t.description for t in asyncio.run(server.mcp.list_tools())}
    writes = set(used) & WRITE_TOOLS
    assert writes >= {"crm_create", "crm_move_stage", "crm_handoff", "crm_add_task", "crm_add_note"}
    for name in writes:
        assert "CONFIRM FIRST" in descriptions[name], name
    # And every one of them was audited as MCP.
    assert {actor["channel"] for actor, _ in fake.applied} == {"mcp"}
