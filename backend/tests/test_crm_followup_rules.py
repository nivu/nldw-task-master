"""The follow-up rules — spec 009 FR-CRMAI-RULE-01 to -08, FR-CRMAI-JOB-01,
-05, FR-CRMAI-FLAG-04; Q-14 to Q-16, Q-29, Q-39, Q-40; D-4; spec §15 "AI
fields and rules" (rules):

- a Proposal deal turns Stuck in stage amber at 14 days and red at 28, and
  its owner gets one notice; Stale deal behaves as before;
- R-1 makes one item per outbound message, whichever record matched it,
  none for a person marked Do not contact; its draft task gets ids only and
  reads no message body;
- R-3's activity, its options by lead stage, its suppression after a
  dismissal;
- R-4 is measured from `status_changed_at`, and makes nothing when the
  member has a converted deal or its company or person an Open or Waiting
  task;
- a company whose only people are marked Do not contact gets nothing from
  R-1 or R-4;
- with phase 3 off and every rule switch on: no item, no Stuck in stage
  flag, no notice; with phase 1 off, or Gmail not live, R-1 makes nothing
  while R-3 and R-4 still run;
- a draft that fails or is budget-skipped shows in the `followup_drafts`
  run;
- no rule sends an email or changes a record: only items are written.

The clock is fixed at 2026-10-18 06:30 UTC, noon in Chennai. Every job runs
on `tests/crm_fake.py` with `FakeClaude`, `FakeGmailText` and
`CapturedCelery` (`tests/crm_ai_fakes.py`): no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from crm_ai_fakes import FAIL, CapturedCelery, FakeClaude, FakeGmailText
from crm_fake import DEFAULTS, MEMBER, OWNER, SELLER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings as config
from app.domain.crm import ai_rules
from app.domain.crm import flags as flag_rules
from app.domain.crm import followups as rules
from app.services.crm import followups, jobs, notifications
from app.tasks import crm as tasks

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 18, 6, 30, tzinfo=UTC)
ON = {
    "email_records": True,
    "stale_notice": True,
    "digests": True,
    "task_reminders": True,
    "campaign_conversion": True,
    "no_reply_draft": True,
    "stuck_notice": True,
    "lead_cold": True,
    "campaign_followthrough": True,
}
DRAFT = {"draft": "Hi Priya, just checking whether the pilot plan reached you."}


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


class Rules:
    """Labs, phases 1 and 3 on with a budget, every rule switch on, Gmail
    sync live; Acme (Deepika's) with Priya, Deepika's mailbox. Claude,
    Gmail and Celery are faked."""

    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs, self.academy = two_workspaces(fake)
        fake.add_deal_lists(self.labs)
        fake.add_campaign_lists(self.labs)
        fake.set_ai(self.labs, budget_usd=10, phases={"1": True, "3": True})
        self.setting("automations", dict(ON))
        self.setting("flag_thresholds", dict(flag_rules.DEFAULT_THRESHOLDS))
        self.setting("email_sync", {"enabled": True})
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        monkeypatch.setattr(config, "SMTP_HOST", "")
        monkeypatch.setattr(jobs, "_now", lambda: NOW)
        self.sent: list = []
        monkeypatch.setattr(
            notifications, "deliver", lambda message: self.sent.append(message) or ["slack"]
        )
        self.http = crm_client(monkeypatch, fake=fake)
        self.acme = self.company("Acme", lead_stage="qualified")
        self.priya = self.person("Priya", self.acme)
        self.mailbox = fake.add(
            "crm_mailboxes",
            workspace_id=self.labs["id"],
            email="deepika@nunnarilabs.com",
            owner_id=MEMBER["id"],
            labels=["Clients"],
            is_active=True,
            backfilled_at="2026-01-01T00:00:00+00:00",
        )
        self.gmail = FakeGmailText().install(monkeypatch)
        self.claude = FakeClaude().install(monkeypatch)
        self.celery = CapturedCelery().install(monkeypatch)

    # -- set-up -------------------------------------------------------------

    def setting(self, key: str, value: dict) -> None:
        found = next(
            (
                s
                for s in self.fake.rows["crm_workspace_settings"].values()
                if s["workspace_id"] == self.labs["id"] and s["key"] == key
            ),
            None,
        )
        if found is not None:
            found["value"] = value
        else:
            self.fake.add(
                "crm_workspace_settings", workspace_id=self.labs["id"], key=key, value=value
            )

    def switch(self, **values: bool) -> None:
        self.setting("automations", {**ON, **values})

    def row(self, table: str, **values) -> dict:
        return self.fake.add(
            table,
            **{
                **DEFAULTS.get(table, {}),
                "workspace_id": self.labs["id"],
                "created_at": _ago(60),
                "created_by": OWNER["id"],
                **values,
            },
        )

    def company(self, name: str, **values) -> dict:
        return self.row(
            "crm_companies",
            **{"name": name, "owner_id": MEMBER["id"], "lead_stage": "new", **values},
        )

    def person(self, first: str, company: dict | None, **values) -> dict:
        return self.row(
            "crm_people",
            **{
                "first_name": first,
                "last_name": None,
                "company_id": company["id"] if company else None,
                "owner_id": MEMBER["id"],
                **values,
            },
        )

    def deal(self, company: dict, **values) -> dict:
        return self.row(
            "crm_opportunities",
            **{
                "name": f"{company['name']} pilot",
                "company_id": company["id"],
                "owner_id": MEMBER["id"],
                "stage": "proposal",
                "probability": 50,
                "next_step": "Call",
                "next_step_on": "2026-11-30",
                "stage_entered_at": _ago(1),
                **values,
            },
        )

    def message(
        self,
        gmail_id: str,
        *,
        thread: str = "t1",
        days: float = 6,
        direction: str = "outbound",
        links: list[tuple[str, dict]] | None = None,
        **values,
    ) -> None:
        """A message as the sync stores it; its text is in Gmail, which no
        rule may read."""
        self.gmail.texts[gmail_id] = f"BODY-MARKER-{gmail_id}"
        if links is None:
            links = [("company", self.acme), ("person", self.priya)]
        self.fake.store_emails(
            self.labs["id"],
            self.mailbox["id"],
            [
                {
                    "gmail_message_id": gmail_id,
                    "thread_id": thread,
                    "subject": "Pilot scope",
                    "sent_at": _ago(days),
                    "direction": direction,
                    "from_email": "deepika@nunnarilabs.com",
                    "participants": [],
                    "is_internal": False,
                    "is_auto": False,
                    "links": [{"record_type": k, "record_id": r["id"]} for k, r in links],
                    **values,
                }
            ],
        )

    def summary(self, thread: str = "t1") -> dict:
        return self.fake.add(
            "crm_email_summaries",
            workspace_id=self.labs["id"],
            mailbox_id=self.mailbox["id"],
            thread_id=thread,
            summary="Deepika sent the pilot plan for two plants.",
            asks=["A pilot plan"],
            commitments=[],
            next_step=None,
            next_step_on=None,
            sentiment="neutral",
            model="claude-sonnet-5-5",
            generated_at=_ago(5),
            created_at=_ago(5),
        )

    def campaign(self) -> dict:
        return self.row("crm_campaigns", name="Plant heads roundtable")

    def member(self, campaign: dict, *, status="engaged", days=8, **values) -> dict:
        return self.row(
            "crm_campaign_members",
            **{
                "campaign_id": campaign["id"],
                "status": status,
                "status_changed_at": _ago(days),
                "updated_at": _ago(days),
                **values,
            },
        )

    def task(self, record: tuple[str, dict], status: str = "open", done_days: float = 40) -> dict:
        task = self.row(
            "tasks",
            title="Call them",
            status=status,
            assignee_id=MEMBER["id"],
            bucket="later" if status in ("open", "waiting") else None,
            waiting_on_text="Their reply" if status == "waiting" else None,
            completed_at=_ago(done_days) if status == "done" else None,
            created_at=_ago(40),
        )
        kind, row = record
        self.row("task_links", task_id=task["id"], record_type=kind, record_id=row["id"])
        return task

    # -- running ------------------------------------------------------------

    def automations(self, now: datetime = NOW) -> dict:
        tasks._flags(now)
        return tasks._automations(now)["runs"]["labs"]

    def drafts(self, now: datetime = NOW) -> dict:
        [queued] = self.celery.named("crm_ai.followup_draft")
        self.celery.sent.clear()
        return followups.drafts(queued["args"][0], now)

    def items(self, rule: str | None = None) -> list[dict]:
        return [
            r
            for r in self.fake.rows["crm_review_items"].values()
            if r["workspace_id"] == self.labs["id"]
            and (rule is None or r["suggestion"].get("rule") == rule)
        ]

    def runs(self, job: str) -> list[dict]:
        """Labs' runs of `job`: every tick records one in each workspace."""
        return [
            r
            for r in self.fake.rows["crm_job_runs"].values()
            if r["job"] == job and r["workspace_id"] == self.labs["id"]
        ]

    def bell(self, kind: str) -> list[dict]:
        return [n for n in self.fake.rows["crm_notifications"].values() if n["kind"] == kind]

    def dismiss(self, item: dict) -> None:
        r = self.http.post(
            f"{API}/review/{item['id']}/dismiss", params=LABS, headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text


@pytest.fixture
def made(monkeypatch) -> Rules:
    return Rules(monkeypatch)


# ---------------------------------------------------------------------------
# R-2 Stuck in stage — FR-CRMAI-RULE-03, -04
# ---------------------------------------------------------------------------


class TestStuckInStage:
    def test_rules_a_proposal_deal_is_amber_at_14_and_red_at_28(self, made):
        deal = made.deal(made.acme, stage_entered_at=_ago(14), created_at=_ago(14))
        made.automations()
        stored = made.fake.rows["crm_opportunities"][deal["id"]]
        stuck = next(f for f in stored["flags"] if f["kind"] == "stuck_in_stage")
        assert stuck["level"] == "amber"
        # Stale deal, which reads activity, behaves as before.
        stale = next(f for f in stored["flags"] if f["kind"] == "stale_deal")
        assert stale["level"] == "amber"
        made.automations(NOW + timedelta(days=14))
        stored = made.fake.rows["crm_opportunities"][deal["id"]]
        assert {f["kind"]: f["level"] for f in stored["flags"]}["stuck_in_stage"] == "red"
        [notice] = made.bell("stuck_in_stage")
        assert (notice["user_id"], notice["record_id"]) == (MEMBER["id"], deal["id"])
        assert notice["title"] == "Deal stuck in Proposal: “Acme pilot”"


# ---------------------------------------------------------------------------
# R-1 No reply after outreach — FR-CRMAI-RULE-02
# ---------------------------------------------------------------------------


class TestNoReply:
    def test_rules_one_item_per_outbound_message_whichever_record_matched(self, made):
        made.message("g1")
        made.summary()
        assert made.automations()["counts"]["drafts_queued"] == 1
        made.claude = FakeClaude(DRAFT).install(made.monkeypatch)
        run = made.drafts()
        assert run == {
            "outcome": "ok",
            "counts": {"drafted": 1, "failed_items": 0, "no_summary": 0},
        }
        [item] = made.items("R-1")
        assert (item["type"], item["record_type"], item["record_id"]) == (
            "rule_suggestion",
            "person",
            made.priya["id"],
        )
        assert item["suggestion"] == {"rule": "R-1", "options": ["mark_used"], **DRAFT}
        assert item["source_key"] == f"rule:R-1:{made.mailbox['id']}:t1:g1"
        assert item["owner_id"] == MEMBER["id"]
        assert item["model"] == "claude-sonnet-5-5"
        # The next tick finds the item: nothing queued, no second call.
        assert made.automations()["counts"]["drafts_queued"] == 0
        assert made.celery.named("crm_ai.followup_draft") == []
        assert len(made.claude.calls) == 1

    def test_rules_a_dismissed_draft_is_not_made_again(self, made):
        made.message("g1")
        made.summary()
        made.automations()
        made.claude = FakeClaude(DRAFT).install(made.monkeypatch)
        made.drafts()
        made.dismiss(made.items("R-1")[0])
        assert made.automations()["counts"]["drafts_queued"] == 0
        # A new outbound message on the thread is a new trigger.
        made.message("g2", days=5.5)
        assert made.automations()["counts"]["drafts_queued"] == 1

    def test_rules_a_reply_ends_the_trigger(self, made):
        made.message("g1")
        made.message("g2", days=2, direction="inbound", thread="t9", links=[("person", made.priya)])
        made.summary()
        assert made.automations()["counts"]["drafts_queued"] == 0

    def test_rules_too_recent_is_not_a_trigger(self, made):
        made.message("g1", days=4.9)
        made.summary()
        assert made.automations()["counts"]["drafts_queued"] == 0

    def test_rules_none_for_a_person_marked_do_not_contact(self, made):
        made.fake.rows["crm_people"][made.priya["id"]]["do_not_contact"] = True
        made.message("g1")
        made.summary()
        assert made.automations()["counts"]["drafts_queued"] == 0
        assert made.items() == []

    def test_rules_the_draft_task_gets_ids_and_reads_no_body(self, made):
        made.message("g1")
        made.summary()
        made.automations()
        [queued] = made.celery.named("crm_ai.followup_draft")
        assert queued["args"] == [
            {
                "workspace_id": made.labs["id"],
                "messages": [
                    {"mailbox_id": made.mailbox["id"], "thread_id": "t1", "gmail_message_id": "g1"}
                ],
            }
        ]
        made.claude = FakeClaude(DRAFT).install(made.monkeypatch)
        made.drafts()
        assert made.gmail.reads == []
        assert all("BODY-MARKER" not in p for p in made.claude.prompts)
        assert "Deepika sent the pilot plan" in made.claude.prompts[0]

    def test_rules_no_summary_yet_waits(self, made):
        made.message("g1")
        made.automations()
        assert made.drafts()["counts"] == {"drafted": 0, "failed_items": 0, "no_summary": 1}
        assert made.items() == []
        assert len(made.celery.sent) == 0
        made.automations()
        assert len(made.celery.named("crm_ai.followup_draft")) == 1


# ---------------------------------------------------------------------------
# R-3 Lead gone cold — FR-CRMAI-RULE-05, Q-15, Q-39, Q-40
# ---------------------------------------------------------------------------


class TestLeadCold:
    @pytest.mark.parametrize(
        ("stage", "options"),
        [
            ("contacted", ["nurturing", "disqualified"]),
            ("nurturing", ["disqualified", "next_touch"]),
        ],
    )
    def test_rules_options_by_lead_stage(self, made, stage, options):
        quiet = made.company("Quiet", lead_stage=stage, next_touch_on="2026-12-01")
        made.automations()
        [item] = made.items("R-3")
        assert item["record_id"] == quiet["id"]
        assert item["suggestion"] == {"rule": "R-3", "options": options}
        assert item["current_values"] == {"lead_stage": stage}
        assert item["owner_id"] == MEMBER["id"]

    def test_rules_only_contacted_and_nurturing(self, made):
        for stage in ("new", "qualified", "customer", "disqualified"):
            made.company(f"Quiet {stage}", lead_stage=stage)
        made.automations()
        assert made.items("R-3") == []

    def test_rules_activity_is_contact_notes_tasks_stage_changes_and_deals(self, made):
        by_contact = made.company("By contact", lead_stage="contacted", last_contact_at=_ago(29))
        by_note = made.company("By note", lead_stage="contacted")
        note = made.row("crm_notes", body="Called", created_at=_ago(10))
        made.row(
            "crm_note_links", note_id=note["id"], record_type="company", record_id=by_note["id"]
        )
        by_task = made.company("By task", lead_stage="contacted")
        made.task(("company", by_task), status="done", done_days=3)
        by_stage = made.company("By stage", lead_stage="new")
        r = made.http.patch(
            f"{API}/companies/{by_stage['id']}",
            params=LABS,
            json={"lead_stage": "contacted"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        by_deal = made.company("By deal", lead_stage="contacted")
        made.deal(by_deal, stage="closed_lost", stage_entered_at=_ago(60), updated_at=_ago(2))
        cold = made.company("Cold", lead_stage="contacted", last_contact_at=_ago(31))
        made.automations()
        assert [i["record_id"] for i in made.items("R-3")] == [cold["id"]]
        assert by_contact["id"] != cold["id"]

    def test_rules_one_open_suggestion_per_company(self, made):
        made.company("Quiet", lead_stage="contacted")
        made.automations()
        made.automations()
        assert len(made.items("R-3")) == 1

    @pytest.mark.parametrize("status", ["dismissed", "expired"])
    def test_rules_not_again_within_lead_cold_days_of_a_dismissal(self, made, status):
        quiet = made.company("Quiet", lead_stage="contacted")
        made.automations()
        [item] = made.items("R-3")
        if status == "dismissed":
            made.dismiss(item)
        else:
            # An expiry counts as a dismissal (Q-40, FR-CRMAI-REV-08).
            made.fake.rows["crm_review_items"][item["id"]]["status"] = "expired"
        made.fake.rows["crm_review_items"][item["id"]]["decided_at"] = NOW.isoformat()
        # New activity, then cold again 31 days later, but within 30 days
        # of the dismissal it stays quiet (Q-40); a day later it is back.
        made.fake.rows["crm_companies"][quiet["id"]]["last_contact_at"] = _ago(1)
        made.automations(NOW + timedelta(days=29))
        assert [i["status"] for i in made.items("R-3")] == [status]
        made.automations(NOW + timedelta(days=31))
        assert sorted(i["status"] for i in made.items("R-3")) == sorted([status, "open"])

    def test_rules_a_dismissal_without_new_activity_is_final(self, made):
        made.company("Quiet", lead_stage="contacted")
        made.automations()
        [item] = made.items("R-3")
        made.dismiss(item)
        made.fake.rows["crm_review_items"][item["id"]]["decided_at"] = NOW.isoformat()
        made.automations(NOW + timedelta(days=60))
        assert [i["status"] for i in made.items("R-3")] == ["dismissed"]


# ---------------------------------------------------------------------------
# R-4 Campaign follow-through — FR-CRMAI-RULE-06, Q-16
# ---------------------------------------------------------------------------


class TestFollowThrough:
    def test_rules_engaged_for_more_than_7_days(self, made):
        camp = made.campaign()
        member = made.member(camp, company_id=made.acme["id"], person_id=made.priya["id"])
        made.member(camp, status="meeting_booked", days=6.9, company_id=made.company("B")["id"])
        made.member(camp, status="invited", days=30, company_id=made.company("C")["id"])
        made.automations()
        [item] = made.items("R-4")
        assert (item["record_type"], item["record_id"], item["member_id"]) == (
            "company",
            made.acme["id"],
            member["id"],
        )
        assert item["suggestion"] == {"rule": "R-4", "options": ["qualify", "close_out"]}
        assert item["current_values"] == {"member_status": "engaged"}

    def test_rules_measured_from_status_changed_at_not_an_unrelated_edit(self, made):
        camp = made.campaign()
        member = made.member(camp, company_id=made.acme["id"])
        r = made.http.patch(
            f"{API}/campaign-members/{member['id']}",
            params=LABS,
            json={"notes": "Met at the roundtable"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        stored = made.fake.rows["crm_campaign_members"][member["id"]]
        assert stored["status_changed_at"] == _ago(8)
        assert stored["updated_at"] != _ago(8)
        made.automations()
        assert len(made.items("R-4")) == 1

    def test_rules_a_status_change_moves_status_changed_at(self, made):
        camp = made.campaign()
        member = made.member(camp, status="attended", days=30, company_id=made.acme["id"])
        r = made.http.patch(
            f"{API}/campaign-members/{member['id']}",
            params=LABS,
            json={"status": "engaged"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        stored = made.fake.rows["crm_campaign_members"][member["id"]]
        assert datetime.fromisoformat(stored["status_changed_at"]) > NOW - timedelta(days=30)

    @pytest.mark.parametrize("status", ["open", "waiting"])
    @pytest.mark.parametrize("on", ["company", "person"])
    def test_rules_none_with_an_open_or_waiting_task(self, made, status, on):
        camp = made.campaign()
        made.member(camp, company_id=made.acme["id"], person_id=made.priya["id"])
        made.task((on, made.acme if on == "company" else made.priya), status=status)
        made.automations()
        assert made.items("R-4") == []

    def test_rules_a_done_task_does_not_stop_it(self, made):
        camp = made.campaign()
        made.member(camp, company_id=made.acme["id"])
        made.task(("company", made.acme), status="done")
        made.automations()
        assert len(made.items("R-4")) == 1

    def test_rules_none_with_a_converted_deal(self):
        member = {
            "id": "m1",
            "status": "engaged",
            "company_id": "c1",
            "person_id": None,
            "converted_opportunity_id": "d1",
            "status_changed_at": _ago(10),
        }
        assert (
            rules.campaign_followthrough(
                [member],
                people={},
                company_people={},
                companies={"c1": {"id": "c1", "owner_id": None}},
                with_open_task=set(),
                now=NOW,
                roles={},
                active=set(),
            )
            == []
        )

    def test_rules_none_for_a_person_marked_do_not_contact(self, made):
        camp = made.campaign()
        ravi = made.person("Ravi", made.acme, do_not_contact=True)
        made.member(camp, person_id=ravi["id"])
        made.automations()
        assert made.items("R-4") == []


class TestDoNotContactCompanies:
    """A company whose only people are marked Do not contact gets nothing
    from R-1 or R-4 (007 FR-CRM-CAMP-03)."""

    def test_rules_r1_and_r4_skip_it(self, made):
        guarded = made.company("Guarded")
        ravi = made.person("Ravi", guarded, do_not_contact=True)
        made.message("g1", links=[("company", guarded), ("person", ravi)])
        made.summary()
        camp = made.campaign()
        made.member(camp, company_id=guarded["id"])
        run = made.automations()
        assert run["counts"]["drafts_queued"] == 0
        assert run["counts"]["followthrough_items"] == 0
        assert made.items() == []

    def test_rules_a_company_with_no_people_is_followed_up(self, made):
        open_firm = made.company("Open")
        camp = made.campaign()
        made.member(camp, company_id=open_firm["id"])
        made.automations()
        assert len(made.items("R-4")) == 1


# ---------------------------------------------------------------------------
# Phases and switches — FR-CRMAI-RULE-01, FR-CRMAI-FLAG-04, FR-CRMAI-JOB-05
# ---------------------------------------------------------------------------


class TestPhasesAndSwitches:
    def _everything(self, made) -> dict:
        deal = made.deal(made.acme, stage_entered_at=_ago(20), created_at=_ago(20))
        made.company("Quiet", lead_stage="contacted")
        made.member(made.campaign(), company_id=made.acme["id"])
        made.message("g1")
        made.summary()
        return deal

    def test_rules_phase_3_off_nothing_at_all(self, made):
        made.fake.set_ai(made.labs, budget_usd=10, phases={"1": True, "3": False})
        deal = self._everything(made)
        run = made.automations()
        assert made.items() == []
        flags = made.fake.rows["crm_opportunities"][deal["id"]]["flags"]
        assert "stuck_in_stage" not in {f["kind"] for f in flags}
        assert made.bell("stuck_in_stage") == []
        assert made.celery.sent == []
        assert set(run["counts"]) == {"stale_notices", "undeliverable"}

    @pytest.mark.parametrize("off", ["phase_1", "gmail"])
    def test_rules_without_phase_1_or_gmail_r1_waits_and_r3_r4_run(self, made, off):
        if off == "phase_1":
            made.fake.set_ai(made.labs, budget_usd=10, phases={"3": True})
        else:
            made.setting("email_sync", {"enabled": False})
        self._everything(made)
        run = made.automations()
        assert run["counts"]["drafts_queued"] == 0
        assert made.celery.sent == []
        assert {i["suggestion"]["rule"] for i in made.items()} == {"R-3", "R-4"}

    def test_rules_each_switch_on_its_own(self, made):
        self._everything(made)
        made.switch(no_reply_draft=False, stuck_notice=False, campaign_followthrough=False)
        run = made.automations()
        assert run["counts"]["lead_cold_items"] == 1
        assert "drafts_queued" not in run["counts"]
        assert {i["suggestion"]["rule"] for i in made.items()} == {"R-3"}
        assert made.bell("stuck_in_stage") == []


# ---------------------------------------------------------------------------
# The followup_drafts run — FR-CRMAI-JOB-01, D-4
# ---------------------------------------------------------------------------


class TestTheDraftRun:
    def test_rules_a_failed_draft_is_counted_in_the_run(self, made):
        made.message("g1")
        made.summary()
        made.automations()
        made.claude = FakeClaude(FAIL).install(made.monkeypatch)
        assert made.drafts() == {
            "outcome": "ok",
            "counts": {"drafted": 0, "failed_items": 1, "no_summary": 0},
        }
        [run] = made.runs("followup_drafts")
        assert run["workspace_id"] == made.labs["id"]
        assert run["counts"]["failed_items"] == 1
        assert made.items() == []

    def test_rules_a_budget_skip_is_the_runs_outcome(self, made):
        made.message("g1")
        made.summary()
        made.automations()
        made.fake.set_ai(made.labs, budget_usd=0, phases={"1": True, "3": True})
        assert made.drafts() == {"outcome": "skipped", "counts": {"skipped": "AI budget reached"}}
        assert made.claude.calls == []
        [run] = made.runs("followup_drafts")
        assert run["outcome"] == "skipped"

    def test_rules_switched_off_meanwhile_is_skipped(self, made):
        made.message("g1")
        made.summary()
        made.automations()
        made.switch(no_reply_draft=False)
        assert made.drafts()["counts"] == {"skipped": followups.R1_OFF}

    def test_rules_a_tick_with_nothing_owed_still_records_a_run(self, made):
        made.message("g1")
        made.summary()
        made.automations()
        made.claude = FakeClaude(DRAFT).install(made.monkeypatch)
        assert made.drafts()["counts"]["drafted"] == 1
        # An hour on, nothing new is owed: the tick records an ok run itself,
        # so Follow-up drafts is not reported late (FR-CRM-JOB-03).
        later = NOW + timedelta(hours=1, minutes=5)
        made.automations(later)
        assert made.celery.named("crm_ai.followup_draft") == []
        assert [r["outcome"] for r in made.runs("followup_drafts")] == ["ok", "ok"]
        late = jobs.stale(later + timedelta(minutes=1), workspace_id=made.labs["id"])
        assert "followup_drafts" not in {s["job"] for s in late}
        # With no tick, the last run is late after twice the interval.
        late = jobs.stale(later + timedelta(hours=1, minutes=1), workspace_id=made.labs["id"])
        assert "followup_drafts" in {s["job"] for s in late}

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"no_reply_draft": False}, followups.R1_OFF),
            (
                {
                    "no_reply_draft": False,
                    "stale_notice": False,
                    "stuck_notice": False,
                    "lead_cold": False,
                    "campaign_followthrough": False,
                },
                followups.R1_OFF,
            ),
        ],
    )
    def test_rules_a_tick_with_r1_off_records_a_skip(self, made, change, reason):
        made.switch(**change)
        made.automations()
        [run] = made.runs("followup_drafts")
        assert (run["outcome"], run["counts"]) == ("skipped", {"skipped": reason})

    def test_rules_a_tick_with_phase_3_off_records_a_skip(self, made):
        made.fake.set_ai(made.labs, phases={"1": True, "3": False})
        made.automations()
        [run] = made.runs("followup_drafts")
        assert (run["outcome"], run["counts"]) == ("skipped", {"skipped": ai_rules.SWITCHED_OFF})


# ---------------------------------------------------------------------------
# Nothing changes without approval — FR-CRMAI-RULE-07, spec §15
# ---------------------------------------------------------------------------


class TestOnlyItems:
    def test_rules_no_rule_sends_an_email_or_changes_a_record(self, made):
        made.switch(stale_notice=False, stuck_notice=False)
        made.company("Quiet", lead_stage="contacted")
        member = made.member(made.campaign(), company_id=made.acme["id"])
        made.message("g1")
        made.summary()
        before = {
            t: {k: dict(v) for k, v in made.fake.rows[t].items()}
            for t in ("crm_companies", "crm_people", "crm_opportunities", "crm_campaign_members")
        }
        audit = len(made.fake.audit)
        made.automations()
        made.claude = FakeClaude(DRAFT).install(made.monkeypatch)
        made.drafts()
        assert {i["suggestion"]["rule"] for i in made.items()} == {"R-1", "R-3", "R-4"}
        assert made.sent == []
        assert made.fake.applied == []
        assert len(made.fake.audit) == audit
        for table, rows in before.items():
            for rid, row in rows.items():
                now = made.fake.rows[table][rid]
                kept = {
                    k: v
                    for k, v in now.items()
                    if k not in ("flags", "flag_level", "flags_computed_at")
                }
                assert kept == {k: v for k, v in row.items() if k in kept}, (table, rid)
        assert made.fake.rows["crm_campaign_members"][member["id"]]["status"] == "engaged"

    def test_rules_a_crm_only_owners_notice_is_the_bell_only(self, made):
        tamil = made.company("Tamil's", owner_id=SELLER["id"])
        made.deal(tamil, owner_id=SELLER["id"], stage_entered_at=_ago(15), created_at=_ago(15))
        run = made.automations()
        [notice] = made.bell("stuck_in_stage")
        assert notice["user_id"] == SELLER["id"]
        assert run["counts"]["stuck_undeliverable"] == 1
        assert [m for m in made.sent if "stuck" in m.subject.lower()] == []
