"""Spec 009 §15, line by line — tasks T166 and the "Acceptance — §15 line to
test" table of `specs/009-crm-intelligence/tasks.md`; NFR-CRMAI-09.

The lines are read from `specs/009-crm-intelligence/spec.md` §15 itself, so
each one is its own test here, and a line added to or reworded in the spec
fails `test_every_line_has_exactly_one_entry` until this file says how it is
proved.

Each line is proved one of two ways:

- **Named covering tests**, the ones the table names (or, where the table
  names only a file, the tests in it that run the line's scenario), each
  asserted to exist (a module, a class or a test function, found by parsing
  the file) and not to be skipped: no `skip`, `skipif` or `xfail` mark on
  it, its class or its module, and no `pytest.skip(` call in its source. A
  test marked `db` is not skipped in that sense: it runs, unskipped, at
  every slice's database gate (`CRM_DB_TESTS=1`).
- **A scenario run here**, for the one line the table assigns to S5: one
  company, through the routes and the summaries, enrichment and AI fields
  jobs with the fakes, goes to the Trash (its summaries and items are
  hidden), comes back (they show), is merged into another company (its
  summaries, enrichment, AI values and open items move to the survivor), is
  in the full workspace export (the new tables, never the AI usage log), and
  is purged (they are deleted). The named db and export tests are checked
  beside it.

"`008`'s brief shows the same section" (T052, T165) is proved through
`008`'s morning brief itself (`GET /tasks/brief` and `task_brief`) by
`test_crm_review_routes.py::TestTheMorningBrief`.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from crm_ai_fakes import FakeClaude, FakeWeb
from crm_fake import MEMBER, OWNER, as_
from test_crm_ai_fields_job import ANSWERS as FIELD_ANSWERS
from test_crm_ai_fields_job import ByField
from test_crm_enrichment_job import ABOUT, HOME, NOTHING
from test_crm_summaries_job import ANSWER, NOW, Mail

from app.services.crm import ai_fields, deals, enrichment
from app.tasks import crm_ai

BACKEND = Path(__file__).resolve().parents[1]
TESTS = BACKEND / "tests"
SPEC = BACKEND.parent / "specs" / "009-crm-intelligence" / "spec.md"
API = "/api/v1/crm"
LABS = {"workspace": "labs"}


def _section_15() -> list[str]:
    """Spec §15's lines, in order, without their check boxes."""
    text = SPEC.read_text()
    body = text.split("## 15. Acceptance criteria", 1)[1].split("\n## 16.", 1)[0]
    return [
        line.removeprefix("- [ ] ").removeprefix("- [x] ")
        for line in body.splitlines()
        if line.startswith(("- [ ] ", "- [x] "))
    ]


LINES = _section_15()


# ---------------------------------------------------------------------------
# Covering tests: they exist and are not skipped
# ---------------------------------------------------------------------------


def _skips(decorators: list[ast.expr]) -> list[str]:
    found = []
    for d in decorators:
        names = {n.attr for n in ast.walk(d) if isinstance(n, ast.Attribute)}
        found += sorted(names & {"skip", "skipif", "xfail"})
    return found


def _module_skips(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            elements = node.value.elts if isinstance(node.value, ast.List) else [node.value]
            return _skips(elements)
    return []


def _tests_in(node: ast.AST) -> list[str]:
    return [
        n.name
        for n in getattr(node, "body", [])
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test")
    ]


def check_cover(ref: str) -> None:
    """`ref` is `path` (a module), `path::Name` (a class or a test) or
    `path::Class::test`, the path relative to `tests/`."""
    path, *names = ref.split("::")
    file = TESTS / path
    assert file.is_file(), f"{ref}: no such file"
    tree = ast.parse(file.read_text())
    node: ast.AST = tree
    skips = _module_skips(tree)
    for name in names:
        found = [
            n
            for n in getattr(node, "body", [])
            if isinstance(n, ast.ClassDef | ast.FunctionDef) and n.name == name
        ]
        assert found, f"{ref}: no {name}"
        node = found[0]
        skips += _skips(node.decorator_list)
    if isinstance(node, ast.Module):
        assert any(
            isinstance(n, ast.FunctionDef) and n.name.startswith("test") for n in ast.walk(node)
        ), f"{ref}: holds no test"
    elif isinstance(node, ast.ClassDef):
        assert _tests_in(node), f"{ref}: holds no test"
    else:
        assert node.name.startswith("test"), f"{ref}: not a test"
    source = file.read_text() if isinstance(node, ast.Module) else ast.unparse(node)
    if "pytest.skip(" in source:
        skips.append("pytest.skip()")
    assert not skips, f"{ref}: skipped ({skips})"


# ---------------------------------------------------------------------------
# The S5 scenario: one company through the Trash, a merge, the export and a
# purge
# ---------------------------------------------------------------------------

#: What the enrichment job reads off Acme's site: a city that differs from
#: the one a person entered (an `enrichment_conflict` item on the company)
#: and a description (an enriched value).
ENRICHED = {
    **NOTHING,
    "city": "Coimbatore",
    "description": "Acme makes precision machine tools.",
}


class Company(Mail):
    """`Mail`'s Labs (Acme, Priya, the Acme pilot deal, Deepika's mailbox)
    with phases 1, 2 and 3 on: the summaries, enrichment and AI fields jobs
    all run, on the same fake store, the web, Claude and the batch queue
    faked."""

    def __init__(self, monkeypatch) -> None:
        super().__init__(monkeypatch)
        fake = self.fake
        fake.set_ai(self.labs, budget_usd=10, phases={"1": True, "2": True, "3": True})
        fake.seed_ai_fields(self.labs)
        monkeypatch.setattr(enrichment, "_now", lambda: NOW)
        monkeypatch.setattr(ai_fields, "_now", lambda: NOW)
        monkeypatch.setattr(deals, "today_in_company_tz", lambda: NOW.date())
        self.web = FakeWeb().install(monkeypatch)
        self.web.site("acme.in", {"/": HOME, "/about": ABOUT})
        self.queue: list[dict] = []
        monkeypatch.setattr(enrichment, "enqueue", lambda args, countdown: self.queue.append(args))
        monkeypatch.setattr(enrichment, "acquire_lock", lambda ws, token: True)
        monkeypatch.setattr(enrichment, "extend_lock", lambda ws, token: None)
        monkeypatch.setattr(enrichment, "release_lock", lambda ws, token: None)

    def call(self, method: str, path: str, *, who=OWNER, status: int = 200, query=None, **kw):
        params = {**LABS, **(query or {})}
        r = self.http.request(method, f"{API}/{path}", params=params, headers=as_(who["id"]), **kw)
        assert r.status_code == status, r.text
        return r.json() if r.content else None

    def summarise(self) -> None:
        self.message(
            "g1",
            links=[
                {"record_type": "company", "record_id": self.acme["id"]},
                {"record_type": "opportunity", "record_id": self.pilot["id"]},
            ],
        )
        self.answer(ANSWER)
        assert self.tick()["counts"]["summarised"] == 1

    def enrich(self) -> None:
        FakeClaude(default=ENRICHED).install(self.monkeypatch)
        crm_ai._enrichment(NOW)
        while self.queue:
            crm_ai.enrichment_batch(self.queue.pop(0))

    def compute_fields(self) -> None:
        ByField(FIELD_ANSWERS).install(self.monkeypatch)
        crm_ai._ai_fields(NOW)

    def queue_items(self) -> list[tuple[str, str, str]]:
        """The whole Review Queue, as the Admin reads it."""
        listed = self.call("GET", "review", query={"scope": "all"})["items"]
        return sorted(
            (i["type"], i["target"]["record_type"], i["target"]["record_id"]) for i in listed
        )

    def thread_summary(self, status: int = 200):
        return self.call("GET", f"email-threads/{self.mailbox['id']}:t1/summary", status=status)

    def export(self) -> dict:
        r = self.http.get(f"{API}/export/workspace", params=LABS, headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        return json.loads(r.content)["objects"]

    def of(self, table: str, company_id: str) -> list[dict]:
        """The rows of a new table that belong to one company."""
        rows = self.rows(table)
        if table == "crm_company_enrichment":
            return [r for r in rows if r["company_id"] == company_id]
        return [r for r in rows if r["record_type"] == "company" and r["record_id"] == company_id]


def trash_restore_merge_export_purge(monkeypatch) -> None:
    """The S5 scenario of the line "Trashing a company hides its summaries
    and items; restoring shows them; purging deletes them; merging two
    companies moves summaries, enrichment, AI values and open items to the
    survivor; the full workspace export holds the new tables except the AI
    usage log"."""
    w = Company(monkeypatch)
    acme = w.acme["id"]
    w.call(
        "PATCH",
        f"companies/{acme}",
        json={"website": {"primary": {"url": "https://acme.in"}}, "city": "Chennai"},
    )
    w.summarise()
    w.enrich()
    w.compute_fields()

    # Everything the three jobs made for Acme is there.
    page = w.call("GET", f"companies/{acme}")
    assert [s["summary"] for s in page["summaries"]] == [ANSWER["summary"]]
    assert page["enrichment"]["values"]["description"] == ENRICHED["description"]
    assert {f["key"]: f["value"] for f in page["ai_fields"]}["icp_fit"] == "Factory Brain"
    [conflict] = w.of("crm_review_items", acme)
    assert conflict["type"] == "enrichment_conflict"
    before = w.queue_items()
    assert ("enrichment_conflict", "company", acme) in before
    assert {t for t, *_ in before} >= {"next_step_change", "task_from_commitment"}
    assert w.thread_summary()["summary"] == ANSWER["summary"]

    # Trash: the company, and its deal with it, are hidden with their
    # summaries and items; nothing stored changes.
    w.call("DELETE", f"companies/{acme}", json={"people": "delete", "deals": "delete"})
    assert w.queue_items() == []
    w.thread_summary(status=404)
    objects = w.export()
    assert objects["review_items"] == []
    assert objects["email_summaries"] == []
    assert objects["enrichment"] == []
    assert [v for v in objects["ai_field_values"] if v["record_id"] == acme] == []
    assert len(w.of("crm_email_summary_links", acme)) == 1

    # Restore: they show again.
    w.call("POST", f"companies/{acme}/restore")
    assert w.queue_items() == before
    assert w.thread_summary()["summary"] == ANSWER["summary"]
    page = w.call("GET", f"companies/{acme}")
    assert [s["summary"] for s in page["summaries"]] == [ANSWER["summary"]]

    # Merge Acme into Acme Group: its summaries, enrichment, AI values and
    # open items move to the survivor.
    group = w.post("companies", {"name": "Acme Group", "owner_id": MEMBER["id"]})["id"]
    w.call(
        "POST",
        "companies/merge",
        json={"survivor_id": group, "merged_id": acme, "choices": {"lead_stage": "b"}},
    )
    for table in (
        "crm_email_summary_links",
        "crm_company_enrichment",
        "crm_ai_field_values",
        "crm_review_items",
    ):
        assert w.of(table, acme) == [], table
        assert w.of(table, group), table
    survivor = w.call("GET", f"companies/{group}")
    assert [s["summary"] for s in survivor["summaries"]] == [ANSWER["summary"]]
    assert survivor["enrichment"]["values"]["description"] == ENRICHED["description"]
    assert {f["key"]: f["value"] for f in survivor["ai_fields"]}["icp_fit"] == "Factory Brain"
    assert ("enrichment_conflict", "company", group) in w.queue_items()
    assert ("enrichment_conflict", "company", acme) not in w.queue_items()

    # The full export holds the new tables, the survivor's rows in them, and
    # never the AI usage log (which the jobs did write).
    objects = w.export()
    for key in ("review_items", "email_summaries", "enrichment", "ai_fields", "ai_field_values"):
        assert objects[key], key
    assert group in {i["record_id"] for i in objects["review_items"]}
    assert [e["company_id"] for e in objects["enrichment"]] == [group]
    assert group in {v["record_id"] for v in objects["ai_field_values"]}
    assert w.rows("crm_ai_usage")
    assert not {k for k in objects if "usage" in k}
    assert "crm_ai_usage" not in json.dumps(objects)

    # Purge the survivor (its deal goes with it): its summaries' links,
    # enrichment, AI values and items are deleted.
    w.call("DELETE", f"companies/{group}", json={"people": "delete", "deals": "delete"})
    w.call("DELETE", f"companies/{group}/permanent")
    for table in (
        "crm_email_summary_links",
        "crm_company_enrichment",
        "crm_ai_field_values",
        "crm_review_items",
    ):
        assert w.of(table, group) == [], table
    pilot = w.pilot["id"]
    assert [r for r in w.rows("crm_review_items") if r["record_id"] == pilot] == []
    assert [r for r in w.rows("crm_ai_field_values") if r["record_id"] == pilot] == []
    assert [r for r in w.rows("crm_email_summary_links") if r["record_id"] == pilot] == []
    w.thread_summary(status=404)
    assert w.queue_items() == []


# ---------------------------------------------------------------------------
# §15 line → proof
# ---------------------------------------------------------------------------

Proof = tuple[str, ...] | object

ENTRIES: list[tuple[str, Proof]] = [
    # Foundations (phase 1)
    (
        "With phase 1 off, or with a zero budget, no model call is made",
        (
            "test_crm_ai_budget.py::TestNothingRunsWithoutLeave::test_budget_phase_off_makes_no_call",
            "test_crm_ai_budget.py::TestNothingRunsWithoutLeave::test_budget_zero_makes_no_call",
        ),
    ),
    (
        "Reaching the budget stops further calls that month",
        (
            "test_crm_ai_budget.py::TestTheBudgetIsReached::test_budget_reached_skips_and_tells_the_admins_once",
            "test_crm_ai_budget.py::TestResumingAfterTheBudgetIsRaised::test_budget_raised_resumes_without_duplicates",
            "test_crm_ai_routes.py::TestAnAdminReadsIt::test_ai_route_budget_reached_and_rates_unset",
            "test_crm_ai_routes.py::TestBootstrap::test_ai_route_bootstrap_an_admin_sees_whether_it_is_reached",
        ),
    ),
    (
        "With a fixed clock, a budget reached on the last day of an Asia/Kolkata month",
        ("test_crm_ai_budget.py::TestTheMonthTurnsInKolkata",),
    ),
    (
        "Settings, AI shows month-to-date estimated spend",
        (
            "test_crm_ai_routes.py::TestAnAdminReadsIt::test_ai_route_month_budget_spend_and_phases",
            "test_crm_ai_routes.py::TestOnlyItsAdmins",
            "test_crm_ai_routes.py::TestNoCostAnywhereElse::test_ai_route_job_lists_carry_no_cost",
            "test_crm_ai_routes.py::TestBootstrap::test_ai_route_bootstrap_phases_and_no_budget_for_non_admins",
        ),
    ),
    (
        "No Claude, Fireflies or Gmail key reaches the browser or a log line",
        (
            "test_crm_ai_client.py::TestTheKeyNeverShows",
            "test_crm_ai_client.py::TestNoKeyReachesTheBrowser::test_client_no_key_in_a_route_response",
            "test_crm_fireflies_client.py::TestTheKeyNeverShows",
            "test_crm_gmail_token.py::TestNothingSecretIsLogged::test_logs",
        ),
    ),
    (
        "A workspace that existed before the migration reads every new phase and rule switch",
        (
            "db/test_crm_ai_foundations_db.py::TestTheSeed::test_foundations_every_existing_workspace_is_off_with_no_budget",
            "db/test_crm_ai_foundations_db.py::TestSavingStraightAfterTheMigration::test_foundations_labs_settings_save",
            "db/test_crm_followup_db.py::TestTheSettings::test_followup_db_every_workspace_has_the_new_keys_off",
        ),
    ),
    (
        "Switching phase 1 off keeps stored summaries",
        (
            "test_crm_summaries_job.py::TestPhaseSwitch::test_job_switched_off_hides_summaries_keeps_items_and_brings_them_back",
            "test_crm_today.py::TestToReview::test_to_review_is_present_while_any_phase_is_on",
        ),
    ),
    (
        "With phase 1 off and phase 2 on, meeting and enrichment items appear",
        (
            "test_crm_meetings_job.py::TestPhaseSwitch::test_job_phase_one_off_items_are_in_review_and_today_and_decidable",
            # The enrichment world switches phase 2 on alone.
            "test_crm_enrichment_job.py::TestInTheBackground::test_enrichment_a_filled_field_with_another_value_waits_as_a_conflict",
            "test_crm_enrichment_routes.py::TestConflictApproval::test_enrichment_approving_replaces_the_field_as_a_manual_edit",
        ),
    ),
    (
        "Turning the stale-deal notice off does not stop R-1, R-3 or R-4",
        (
            "test_crm_automations.py::TestPerSwitchRuns::test_automations_skipped_only_when_all_five_are_off",
            "test_crm_automations.py::TestPerSwitchRuns::test_automations_one_rule_alone",
            "test_crm_followup_rules.py::TestPhasesAndSwitches::test_rules_each_switch_on_its_own",
        ),
    ),
    # Email summaries (phase 1, after step zero)
    (
        "A labelled thread with a client produces a summary within one sync cycle",
        (
            "test_crm_summaries_job.py::TestOneTick::test_job_one_tick_summarises_the_thread",
            "test_crm_summaries_routes.py::TestTheRecord::test_routes_the_record_carries_its_summaries",
            "test_crm_summaries_routes.py::TestTheRoute::test_routes_a_viewer_reads_the_summary",
        ),
    ),
    (
        "The privacy test (NFR-CRMAI-01) finds no marker",
        (
            "test_crm_ai_privacy.py::TestEmail::test_privacy_no_marker_in_any_column_log_payload_or_result",
            "test_crm_ai_privacy.py::TestMeeting::test_privacy_no_transcript_marker_in_any_column_log_payload_or_result",
            "test_crm_ai_privacy.py::TestWebPage::test_privacy_no_web_page_marker_in_any_column_log_payload_or_result",
        ),
    ),
    (
        "A thread holding an internal-only message with a unique marker",
        (
            "test_crm_ai_privacy.py::TestEmail::test_privacy_no_marker_reaches_the_model",
            "test_crm_summaries_job.py::TestWhatIsRead::test_job_quoted_history_and_signature_never_reach_the_prompt",
            "test_crm_summaries_rules.py::TestStrip",
        ),
    ),
    (
        "Running the job twice on an unchanged thread makes no model call",
        (
            "test_crm_summaries_job.py::TestIdempotent::test_job_twice_no_second_call_nothing_changes",
            "test_crm_summaries_job.py::TestIdempotent::test_job_a_new_message_replaces_the_summary",
        ),
    ),
    (
        "Removing a thread's label deletes its summary",
        (
            "test_crm_summaries_job.py::TestRemovedAndPaused::test_job_label_removal_deletes_the_summary",
            "db/test_crm_summaries_db.py::TestTheThreadAndItsRecords::test_summaries_db_remove_emails_drops_the_summary_of_an_emptied_thread",
        ),
    ),
    (
        "A paused mailbox's threads are not summarised",
        (
            "test_crm_summaries_job.py::TestRemovedAndPaused::test_job_paused_mailbox_skipped_and_its_summaries_kept",
        ),
    ),
    (
        "Connecting a mailbox and running its 90-day backfill makes no model call",
        ("test_crm_summaries_job.py::TestWhatIsRead::test_job_the_backfill_makes_no_call",),
    ),
    (
        "A promised date in an email appears as a `task_from_commitment` item",
        (
            "test_crm_summaries_rules.py::TestItems::test_rules_items_only_for_dated_commitments",
            "test_crm_summaries_job.py::TestOneTick::test_job_next_step_and_dated_commitment_become_items",
        ),
    ),
    (
        "A suggested next step that differs from the deal's appears as a `next_step_change` item",
        (
            "test_crm_summaries_job.py::TestOneTick::test_job_next_step_and_dated_commitment_become_items",
            "test_crm_summaries_job.py::TestOneTick::test_job_with_two_open_deals_one_item_with_a_picker",
            "test_crm_review_routes.py::TestNextStep::test_review_approving_sets_the_deal_through_the_manual_path",
            "test_crm_review_routes.py::TestNextStep::test_review_two_open_deals_need_the_chosen_one",
        ),
    ),
    (
        "A fixture email with an injected instruction and a link",
        (
            "test_crm_summaries_job.py::TestWhatIsRead::test_job_injected_instruction_and_link_have_no_effect",
        ),
    ),
    (
        "With a fixture email and transcript holding ₹4.5L",
        (
            # The six fixtures through every Viewer route and tool (T169).
            "test_crm_redaction_sweep.py::TestTheMoneyFixtures",
            "test_crm_summaries_routes.py::TestTheRoute::test_routes_money_in_the_summary_is_masked",
            "test_crm_review_routes.py::TestNoNotice::test_review_money_in_a_suggestion_is_masked_again",
            "test_crm_ai_fields_job.py::TestInputs::test_ai_fields_the_prompt_carries_no_amount",
        ),
    ),
    (
        "Owner resolution: an exact member match is routed to that member",
        (
            "test_crm_summaries_rules.py::TestOwners::test_rules_owner_resolution",
            "test_crm_summaries_rules.py::TestItems::test_rules_an_unknown_owner_falls_back_to_the_mailbox_owner",
        ),
    ),
    # Review Queue (phase 1)
    (
        "Approving a next-step item changes the deal through the same validation",
        (
            "test_crm_review_routes.py::TestNextStep::test_review_approving_sets_the_deal_through_the_manual_path",
            "test_crm_review_routes.py::TestNextStep::test_review_an_empty_date_is_refused_as_a_manual_edit_is",
            "test_crm_review_routes.py::TestNextStep::test_review_someone_elses_edit_is_refused_as_changed",
        ),
    ),
    (
        "A new synced email that updates the company's Last inbound",
        (
            "test_crm_review_rules.py::TestStaleness::test_review_a_last_inbound_change_does_not_refuse",
            "test_crm_enrichment_routes.py::TestConflictApproval::test_enrichment_another_fields_change_does_not_refuse",
        ),
    ),
    (
        "Approving a task item creates one `008` task",
        (
            # The real 008 create_task, since S1b (T165).
            "test_crm_review_routes.py::TestTasks::test_review_approving_creates_one_task_with_its_source",
            "test_crm_review_routes.py::TestTasks::test_review_approving_twice_creates_one_task",
        ),
    ),
    (
        "Members see only their items; workspace Admins see all",
        (
            "test_crm_review_routes.py::TestWhoSeesTheQueue::test_review_members_see_only_theirs",
            "test_crm_review_routes.py::TestWhoSeesTheQueue::test_review_admins_see_all_and_their_own_holds_the_admins_items",
            "test_crm_review_routes.py::TestWhoSeesTheQueue::test_review_viewers_see_none",
            "test_crm_review_routes.py::TestWhoSeesTheQueue::test_review_another_workspaces_item_is_not_found",
        ),
    ),
    (
        "An item untouched for 14 days expires",
        ("test_crm_review_expiry.py::TestExpiry",),
    ),
    (
        "An approved change's audit entry names the approver",
        (
            "test_crm_review_routes.py::TestNextStep::test_review_the_audit_row_names_the_approver_and_carries_the_item",
            "db/test_crm_review_db.py::TestTheAuditRow::test_review_db_suggestion_id_lands_on_the_audit_row",
        ),
    ),
    (
        "Today shows To review with the count and the oldest items",
        (
            "test_crm_today.py::TestToReview::test_to_review_shows_the_count_and_the_three_oldest",
            "test_crm_review_routes.py::TestBootstrapAndToday::test_review_today_has_the_section_last",
            "test_crm_review_routes.py::TestTheMorningBrief::test_review_the_brief_shows_the_members_to_review_as_today_does",
            "test_crm_review_routes.py::TestTheMorningBrief::test_review_task_brief_shows_the_same_to_review",
            "test_crm_review_routes.py::TestTheMorningBrief::test_review_the_brief_never_shows_it_to_a_viewer",
            "test_crm_review_routes.py::TestTheMorningBrief::test_review_the_brief_leaves_it_out_while_the_queue_is_not_visible",
        ),
    ),
    (
        "`crm_review_list` and `crm_review_decide` work through NLDW",
        (
            "test_crm_mcp.py::TestReviewTools",
            "test_crm_review_routes.py::TestThroughClaude",
        ),
    ),
    (
        "Trashing a company hides its summaries and items",
        trash_restore_merge_export_purge,
    ),
    # Meetings and enrichment (phase 2)
    (
        "A client call recorded in Fireflies appears as a meeting note",
        (
            "test_crm_meetings_job.py::TestOneTick::test_job_the_note_is_on_the_right_company_and_deal",
            "test_crm_meetings_job.py::TestOneTick::test_job_action_items_and_next_step_become_items",
            "test_crm_ai_jobs.py::TestTheNewJobNames::test_jobs_the_five_names_and_their_intervals",
            "test_crm_ai_privacy.py::TestMeeting::test_privacy_no_transcript_marker_in_any_column_log_payload_or_result",
        ),
    ),
    (
        "A meeting with only internal attendees, or with no attendee matched",
        (
            "test_crm_meetings_job.py::TestNeverDownloaded::test_job_internal_only_and_unmatched_meetings_are_not_fetched",
        ),
    ),
    (
        "A meeting matched in two workspaces creates nothing in either",
        (
            "test_crm_meetings_job.py::TestNeverDownloaded::test_job_two_workspaces_nothing_in_either_skipped_in_each",
        ),
    ),
    (
        "Re-polling the same transcript creates no second note",
        (
            "test_crm_meetings_job.py::TestIdempotent::test_job_a_re_poll_makes_no_second_note",
            "db/test_crm_meetings_db.py::TestOnePerTranscript::test_meetings_db_a_re_poll_changes_nothing",
        ),
    ),
    (
        "An updated transcript updates the AI-written note until a person edits it",
        (
            "test_crm_meetings_job.py::TestIdempotent::test_job_an_updated_transcript_updates_the_note",
            "test_crm_meetings_job.py::TestIdempotent::test_job_after_a_person_edits_the_note_ai_updates_stop",
            "db/test_crm_meetings_db.py::TestUpdatedTranscript",
        ),
    ),
    (
        "Importing the CODISSIA or Australia list does no enrichment work",
        (
            "test_crm_enrichment_job.py::TestQueuedNotRead::test_enrichment_an_import_does_no_enrichment_work_in_the_request",
            "test_crm_enrichment_job.py::TestInTheBackground::test_enrichment_the_values_are_marked_ai",
            "test_crm_enrichment_job.py::TestInTheBackground::test_enrichment_no_person_entered_field_is_written",
        ),
    ),
    (
        "A company created by email sync is queued for enrichment",
        (
            "test_crm_enrichment_job.py::TestQueuedNotRead::test_enrichment_a_company_created_by_email_sync_is_queued",
            "db/test_crm_enrichment_db.py::TestTheTriggerQueues",
        ),
    ),
    (
        "An unreadable site records failed with a reason and no values",
        (
            "test_crm_enrichment_job.py::TestFailures::test_enrichment_an_unreadable_site_did_not_respond",
        ),
    ),
    (
        "A site address resolving to a private network is refused",
        (
            "test_crm_web_fetch.py::TestPrivateAddressesAreRefused",
            "test_crm_enrichment_job.py::TestFailures::test_enrichment_a_private_address_did_not_respond",
        ),
    ),
    (
        "Promoting an enriched value is an audited edit by the person",
        (
            "test_crm_enrichment_routes.py::TestPromote::test_enrichment_promote_is_an_audited_edit_by_the_person",
            "test_crm_enrichment_job.py::TestInTheBackground::test_enrichment_a_filled_field_with_another_value_waits_as_a_conflict",
        ),
    ),
    # AI fields and rules (phase 3)
    (
        "AI fields are read-only, marked AI, shown on record pages only",
        (
            "test_crm_ai_fields_job.py::TestNeverFeedsAnything::test_ai_fields_a_value_never_changes_a_stage_probability_flag_or_colour",
            "test_crm_ai_fields_routes.py::TestTheRecordRead::test_ai_fields_a_company_and_a_deal_carry_their_values",
            "test_crm_ai_fields_routes.py::TestTheRecordRead::test_ai_fields_a_number_is_shown_on_the_record_only",
        ),
    ),
    (
        "With fixed clocks: a Proposal deal turns Stuck in stage amber at 14 days",
        (
            "test_crm_followup_rules.py::TestStuckInStage::test_rules_a_proposal_deal_is_amber_at_14_and_red_at_28",
            "test_crm_flags.py::TestStuckInStage::test_stuck_a_proposal_deal_at_14_and_28_days",
            "test_crm_notifications.py::TestStuckInStageNotice::test_notice_once_per_stage_entry",
            "test_crm_automations.py::TestStaleDealNotice",
        ),
    ),
    (
        "Switching R-2 on can turn a deal's dashboard colour",
        (
            "test_crm_flags.py::TestStuckStored::test_stuck_counts_toward_the_levels_with_r2_on",
            "test_crm_flags.py::TestStuckStored::test_stuck_absent_with_r2_or_phase_3_off",
        ),
    ),
    (
        "A deal that gets a Stale deal and a Stuck in stage notice at the same level",
        (
            "test_crm_notifications.py::TestStuckInStageNotice::test_notice_one_message_with_a_stale_notice_the_same_day",
            "test_crm_notifications.py::TestStaleNoticeAfterStuckNotice",
        ),
    ),
    (
        "R-1, R-3 and R-4 create one suggestion per trigger",
        (
            "test_crm_followup_rules.py::TestNoReply::test_rules_one_item_per_outbound_message_whichever_record_matched",
            "test_crm_followup_rules.py::TestNoReply::test_rules_a_dismissed_draft_is_not_made_again",
            "test_crm_followup_rules.py::TestLeadCold::test_rules_one_open_suggestion_per_company",
            "test_crm_followup_rules.py::TestLeadCold::test_rules_not_again_within_lead_cold_days_of_a_dismissal",
            "test_crm_followup_rules.py::TestFollowThrough::test_rules_one_suggestion_per_member_across_ticks",
            "test_crm_followup_rules.py::TestFollowThrough::test_rules_not_again_after_a_dismissal_until_the_status_changes",
            "test_crm_followup_rules.py::TestPhasesAndSwitches::test_rules_each_switch_on_its_own",
            "test_crm_review_expiry.py::TestExpiry::test_review_expiry_is_not_re_created_from_the_same_source",
        ),
    ),
    (
        "R-1: an outbound email matched to a person and their company produces one item",
        (
            "test_crm_followup_rules.py::TestNoReply::test_rules_one_item_per_outbound_message_whichever_record_matched",
            "test_crm_followup_rules.py::TestNoReply::test_rules_the_draft_task_gets_ids_and_reads_no_body",
            "test_crm_followup_rules.py::TestNoReply::test_rules_none_for_a_person_marked_do_not_contact",
            "test_crm_followup_rules.py::TestFollowThrough::test_rules_none_for_a_person_marked_do_not_contact",
            "test_crm_ai_privacy.py::TestFollowUpDraft::test_privacy_the_draft_task_takes_ids_and_reads_no_body",
        ),
    ),
    (
        "R-4 measures 7 days from `status_changed_at`",
        (
            "test_crm_followup_rules.py::TestFollowThrough::test_rules_measured_from_status_changed_at_not_an_unrelated_edit",
            "db/test_crm_followup_db.py::TestStatusChangedAt",
        ),
    ),
    (
        "Notices reach CRM-only owners by bell only",
        (
            "test_crm_followup_rules.py::TestOnlyItems::test_rules_a_crm_only_owners_notice_is_the_bell_only",
            "test_crm_notifications.py::TestStuckInStageNotice::test_notice_a_crm_only_owner_gets_the_bell_only",
        ),
    ),
    (
        "No rule sends an email or changes a record without approval",
        (
            "test_crm_followup_rules.py::TestOnlyItems::test_rules_no_rule_sends_an_email_or_changes_a_record",
        ),
    ),
    # Contact recency (phase 4)
    (
        "With fixed clocks, the levels match FR-CRMAI-REL-01",
        (
            "test_crm_contact_recency.py::TestTheLevels::test_recency_both_directions_at_the_edges",
            "test_crm_contact_recency.py::TestTheLevels::test_recency_one_sided_email_is_fading",
            "test_crm_contact_recency.py::TestTheFlagsJob::test_recency_a_company_includes_its_people",
        ),
    ),
    (
        'With Gmail off, the chip says "Meetings only"',
        (
            "test_crm_contact_recency.py::TestTheRecordRead::test_recency_gmail_off_says_meetings_only",
            "test_crm_contact_recency.py::TestTheFlagsJob::test_recency_with_gmail_sync_off_only_meetings_count",
        ),
    ),
    (
        "Contact recency filters company and person views",
        (
            "test_crm_contact_recency.py::TestTheViewFilter::test_recency_filters_a_view",
            "test_crm_contact_recency.py::TestTheFlagsJob::test_recency_a_recompute_is_not_an_edit",
            "db/test_crm_contact_recency_db.py::TestTheWriter::test_recency_db_moves_neither_updated_time_nor_by_nor_the_audit",
            "db/test_crm_contact_recency_db.py::TestTheQuery::test_recency_db_filters_companies_and_people",
        ),
    ),
    # Isolation (every phase)
    (
        "The isolation test shows no workspace B data",
        (
            "test_crm_ai_isolation.py::TestSummaries",
            "test_crm_ai_isolation.py::TestMeetings",
            "test_crm_ai_isolation.py::TestEnrichment",
            "test_crm_ai_isolation.py::TestAiFields",
            "test_crm_ai_isolation.py::TestFollowUpDrafts",
        ),
    ),
]

#: The named covers of the S5 scenario's line, checked beside it: the db
#: tests of trash, purge and merge, and the export tests (T050, T054, T080,
#: T115, T131, T044, T073, T109, T127).
SCENARIO_COVERS = (
    "test_crm_review_routes.py::TestTrash::test_review_trashing_the_target_hides_it_and_restoring_shows_it",
    "db/test_crm_review_db.py::TestTheRecordLifecycle",
    "db/test_crm_summaries_db.py::TestTheThreadAndItsRecords::test_summaries_db_purge_takes_the_records_links",
    "db/test_crm_summaries_db.py::TestTheThreadAndItsRecords::test_summaries_db_merge_moves_the_links",
    "db/test_crm_enrichment_db.py::TestTheTable::test_enrichment_db_purging_the_company_deletes_its_row",
    "db/test_crm_enrichment_db.py::TestMerge",
    "db/test_crm_ai_fields_db.py::TestLifecycle",
    "test_crm_csv_io.py::TestTheReviewQueueInTheExport",
    "test_crm_csv_io.py::TestTheEmailSummariesInTheExport",
    "test_crm_csv_io.py::TestTheEnrichmentInTheExport",
    "test_crm_csv_io.py::TestTheAiFieldsInTheExport",
)


def _entry(line: str) -> Proof:
    [proof] = [proof for prefix, proof in ENTRIES if line.startswith(prefix)]
    return proof


def _id(line: str) -> str:
    return line[:60]


class TestTheLines:
    def test_section_15_has_its_lines(self):
        assert len(LINES) == 53

    def test_every_line_has_exactly_one_entry(self):
        for line in LINES:
            assert sum(line.startswith(p) for p, _ in ENTRIES) == 1, line
        for prefix, _ in ENTRIES:
            assert sum(line.startswith(prefix) for line in LINES) == 1, prefix

    def test_the_scenario_is_the_one_line_assigned_to_s5(self):
        run_here = [line[:40] for line in LINES if callable(_entry(line))]
        assert run_here == ["Trashing a company hides its summaries a"]

    def test_a_skipped_cover_is_caught(self, tmp_path, monkeypatch):
        """The check itself: a skipped test or a missing class fails it."""
        (tmp_path / "test_x.py").write_text(
            "import pytest\n\n\nclass TestA:\n    @pytest.mark.skip\n    def test_a(self):\n"
            "        pass\n\n\nclass TestB:\n    def test_b(self):\n        pass\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "TESTS", tmp_path)
        check_cover("test_x.py::TestB")
        with pytest.raises(AssertionError, match="skipped"):
            check_cover("test_x.py::TestA::test_a")
        with pytest.raises(AssertionError, match="no TestC"):
            check_cover("test_x.py::TestC")
        with pytest.raises(AssertionError, match="no such file"):
            check_cover("test_none.py")

    def test_an_xfail_or_a_skip_call_is_caught(self, tmp_path, monkeypatch):
        (tmp_path / "test_y.py").write_text(
            "import pytest\n\n\n@pytest.mark.xfail\nclass TestA:\n    def test_a(self):\n"
            "        pass\n\n\nclass TestB:\n    def test_b(self):\n"
            '        pytest.skip("later")\n\n\ndef test_c():\n    pass\n'
        )
        (tmp_path / "test_z.py").write_text(
            "import pytest\n\npytestmark = [pytest.mark.skipif(True, reason='x')]\n\n\n"
            "def test_d():\n    pass\n"
        )
        monkeypatch.setattr(sys.modules[__name__], "TESTS", tmp_path)
        check_cover("test_y.py::test_c")
        with pytest.raises(AssertionError, match="xfail"):
            check_cover("test_y.py::TestA")
        with pytest.raises(AssertionError, match=r"pytest\.skip"):
            check_cover("test_y.py::TestB::test_b")
        with pytest.raises(AssertionError, match="skipif"):
            check_cover("test_z.py::test_d")


@pytest.mark.parametrize("line", LINES, ids=_id)
def test_section_15_line(line, monkeypatch):
    proof = _entry(line)
    if callable(proof):
        for ref in SCENARIO_COVERS:
            check_cover(ref)
        proof(monkeypatch)
        return
    for ref in proof:
        check_cover(ref)
