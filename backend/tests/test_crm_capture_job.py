"""Email capture's discovery job — spec 010 FR-CAP-DIS-01, -02, -04 to -07,
-10, FR-CAP-RPT-01, -03, FR-CAP-CHG-05, NFR-CAP-01 to -04; plan §5.6, §5.7,
PD-11, PD-14, PD-17; spec §13 S2. S3 (tasks T314): FR-CAP-DIS-03, -07 to
-10 (live), FR-CAP-REV-04, FR-CAP-RPT-04; plan §5.8, §5.10, PD-4; spec §13
S3 lines 1, 3, 5 and 6. R1 (tasks T416): FR-CAP-GRP-01 to -08 — one item
per company, order-independent, a moved conversation leaves its item, one
deal signal per company, consolidation of the items made before 074; plan
R1.2, PD-24, PD-26, PD-38 to PD-40; spec §14.5 I1. R1 (tasks T425):
FR-CAP-RPT-05 to -07 — the recall's reasons, a failure not judged, the cap
marker; plan R1.3; spec §14.5 I2.

Every run is on `tests/crm_fake.py` with `FakeClaude` and `FakeGmailThread`
(`tests/crm_ai_fakes.py`): no network. A `FakeClaude()` with no script fails
the test on any call, so a candidate that must never reach Claude proves it
by the run passing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from crm_ai_fakes import FAIL, INVALID, TIMEOUT, CapturedCelery, FakeClaude, FakeGmailThread
from crm_fake import DEFAULTS, MEMBER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from postgrest.exceptions import APIError

from app.config import settings as config
from app.domain.crm import capture as rules
from app.services.crm import capture, gmail, jobs, notifications
from app.tasks import crm_ai

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
BOX = "owner@nunnarilabs.com"
LEAD = {
    "outcome": "new_lead",
    "confidence": "high",
    "company_name": "Acme Robotics",
    "person_name": "Priya Shah",
    "person_email": "priya@acme-robotics.com",
    "person_title": "Head of Ops",
    "lead_stage": "contacted",
    "deal_stage": None,
    "website_choice": 1,
    "reason": "Asked for pricing; you replied.",
}
VENDOR = {**LEAD, "outcome": "other_business", "company_name": None, "website_choice": None}


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    def get(self, key):
        value = self.keys.get(key)
        return value.encode() if isinstance(value, str) else value

    def delete(self, key):
        self.keys.pop(key, None)


class Capture:
    """Labs: a dry run open, email sync live, a budget and the rates set,
    every `009` phase off; the owner's mailbox on Primary and Sent,
    backfilled."""

    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs, self.academy = two_workspaces(fake)
        fake.set_ai(self.labs, budget_usd=10)
        fake.add(
            "crm_workspace_settings",
            workspace_id=self.labs["id"],
            key="email_sync",
            value={"enabled": True},
        )
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        for module in (jobs, capture):
            monkeypatch.setattr(module, "_now", lambda: NOW)
        monkeypatch.setattr(notifications, "deliver", lambda message: ["email"])
        self.http = crm_client(monkeypatch, fake=fake)
        self.mailbox = fake.add(
            "crm_mailboxes",
            workspace_id=self.labs["id"],
            email=BOX,
            owner_id=OWNER["id"],
            labels=["Sales"],
            **{
                **DEFAULTS["crm_mailboxes"],
                "capture_source": "primary_sent",
                "backfilled_at": "2026-10-01T00:00:00+00:00",
                "history_id": "100",
            },
        )
        fake.set_capture(self.labs, "dry_run")
        self.run = fake.add_dry_run(self.labs, started_at="2026-10-20T05:00:00+00:00")
        self.gmail = FakeGmailThread().install(monkeypatch)
        self.claude = FakeClaude().install(monkeypatch)
        self.celery = CapturedCelery().install(monkeypatch)
        self.redis = FakeRedis()
        monkeypatch.setattr(capture, "_redis", lambda: self.redis)
        capture._more.clear()

    def answer(self, *script, **kwargs) -> FakeClaude:
        self.claude = FakeClaude(*script, **kwargs).install(self.monkeypatch)
        return self.claude

    def conversation(
        self, thread: str, *, sender: str = "Priya Shah <priya@acme-robotics.com>", **values
    ) -> dict:
        """One inbound Primary message, marked a candidate as the sync marks it."""
        at = values.pop("at", NOW - timedelta(days=2))
        msg = self.gmail.message(
            thread,
            f"{thread}-m1",
            sender=sender,
            to=BOX,
            at=at,
            text=values.pop("text", "Could you send your pricing for 40 seats?"),
            **values,
        )
        self.mark(thread, msg["id"], at)
        return self.candidate(thread)

    def mark(self, thread: str, message_id: str, at: datetime) -> None:
        self.fake.mark_candidates(
            self.labs["id"],
            self.mailbox["id"],
            [{"thread_id": thread, "message_id": message_id, "sent_at": at.isoformat()}],
        )

    def candidate(self, thread: str) -> dict:
        return next(
            c for c in self.fake.rows["crm_capture_candidates"].values() if c["thread_id"] == thread
        )

    def tick(self) -> dict:
        return crm_ai._capture(NOW)["runs"]["labs"]

    def records(self) -> tuple[int, int, int]:
        return (
            len(self.fake.rows["crm_companies"]),
            len(self.fake.rows["crm_people"]),
            len(self.fake.rows["crm_review_items"]),
        )


class _Ctx:
    """The overview's request context: Labs, as its owner."""

    def __init__(self, cap: Capture) -> None:
        self.workspace_id = cap.labs["id"]
        self.workspace = cap.labs


@pytest.fixture
def cap(monkeypatch) -> Capture:
    return Capture(monkeypatch)


class TestGates:
    """FR-CAP-DIS-01, plan §5.6 checks 1 to 6; spec §13 S2 line 4."""

    @pytest.mark.parametrize(
        ("close", "reason"),
        [
            ("mode_off", "Email capture is off."),
            ("sync_off", "Email sync is off"),
            ("labels_only", "No mailbox captures Primary and Sent."),
            ("run_finished", "The dry run is finished; apply it or run it again."),
            ("rates_unset", "AI rates are not set"),
            ("zero_budget", "AI budget reached"),
        ],
    )
    def test_capture_reads_no_text_when_a_gate_is_closed(self, cap, monkeypatch, close, reason):
        cap.conversation("t1")
        if close == "mode_off":
            cap.fake.set_capture(cap.labs, "off")
        elif close == "sync_off":
            monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
            next(
                r
                for r in cap.fake.rows["crm_workspace_settings"].values()
                if r["key"] == "email_sync" and r["workspace_id"] == cap.labs["id"]
            )["value"] = {"enabled": False}
        elif close == "labels_only":
            cap.fake.rows["crm_mailboxes"][cap.mailbox["id"]]["capture_source"] = "labels"
        elif close == "run_finished":
            cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] = NOW.isoformat()
        elif close == "rates_unset":
            monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", None)
        else:
            cap.fake.set_ai(cap.labs, budget_usd=0)
        got = cap.tick()
        assert (got["outcome"], got["counts"]["skipped"]) == ("skipped", reason)
        assert cap.gmail.meta_reads == [] and cap.gmail.text_reads == []
        assert cap.claude.calls == []
        assert cap.candidate("t1")["status"] == "pending"

    def test_capture_without_the_gmail_key_skips_before_any_candidate(self, cap, monkeypatch):
        cap.conversation("t1")
        FakeGmailThread().install(monkeypatch, key=False)
        got = cap.tick()
        assert (got["outcome"], got["counts"]["skipped"]) == (
            "skipped",
            "The Gmail key is not set on this service.",
        )
        assert cap.candidate("t1")["status"] == "pending"

    def test_capture_runs_with_every_ai_phase_off(self, cap):
        cap.conversation("t1")
        cap.answer(LEAD)
        assert cap.tick()["outcome"] == "ok"
        assert cap.candidate("t1")["status"] == "judged"

    @pytest.mark.parametrize("change", ["off", "restarted"])
    def test_capture_switching_off_mid_run_reads_and_sends_nothing_more(self, cap, change):
        # FR-CAP-DIS-01, plan §5.7: the mode ends the run, not just its start.
        cap.conversation("t1", at=NOW - timedelta(days=3))
        cap.conversation("t2", sender="ravi@robots.io")
        admin = {
            "kind": "user",
            "user_id": OWNER["id"],
            "channel": "web",
            "workspace_id": cap.labs["id"],
        }
        reply = cap.answer(LEAD, LEAD).messages

        def answer_then_switch(**kwargs):
            out = reply(**kwargs)
            cap.fake.set_capture_mode(admin, cap.labs["id"], "off")
            if change == "restarted":
                cap.fake.start_dry_run(admin)
            return out

        cap.monkeypatch.setattr("app.services.crm.claude.messages", answer_then_switch)
        got = cap.tick()
        reason = rules.CAPTURE_OFF if change == "off" else rules.DRY_RUN_FINISHED
        assert (got["outcome"], got["counts"]["skipped"]) == ("skipped", reason)
        assert cap.gmail.text_reads == ["t1-m1"] and len(cap.claude.calls) == 1
        assert cap.candidate("t2")["status"] == "pending"

    def test_capture_a_budget_stop_keeps_the_skipped_by_rules_count(self, cap, monkeypatch):
        # NFR-CAP-04: the reason goes in `skipped`, the rules' count beside it.
        cap.conversation(
            "news",
            sender="news@vendor.io",
            List_Unsubscribe="<mailto:x@vendor.io>",
            at=NOW - timedelta(days=3),
        )
        cap.conversation("t1")
        gate, seen = capture.ai.gate, {"n": 0}

        def budget_spent_after_the_start(workspace, phase):
            seen["n"] += 1
            if seen["n"] > 1:
                cap.fake.set_ai(cap.labs, budget_usd=0)
            return gate(workspace, phase)

        monkeypatch.setattr(capture.ai, "gate", budget_spent_after_the_start)
        got = cap.tick()
        assert got["outcome"] == "skipped" and got["counts"]["skipped"] == "AI budget reached"
        assert got["counts"]["skipped_rules"] == 1 and got["counts"]["considered"] == 2
        [run] = [
            r
            for r in cap.fake.rows["crm_job_runs"].values()
            if r["job"] == "capture" and r["workspace_id"] == cap.labs["id"]
        ]
        assert run["counts"]["skipped_rules"] == 1
        assert cap.claude.calls == []

    def test_capture_mailboxes_paused_on_labels_or_with_their_owner_gone_are_not_read(self, cap):
        for n, values in enumerate(
            [
                {"is_active": False},
                {"capture_source": "labels"},
                {"owner_id": "u-nobody"},
            ]
        ):
            other = cap.fake.add(
                "crm_mailboxes",
                workspace_id=cap.labs["id"],
                email=f"box{n}@nunnarilabs.com",
                labels=["Sales"],
                **{
                    **DEFAULTS["crm_mailboxes"],
                    "owner_id": MEMBER["id"],
                    "capture_source": "primary_sent",
                    "backfilled_at": "2026-10-01T00:00:00+00:00",
                    **values,
                },
            )
            cap.fake.mark_candidates(
                cap.labs["id"],
                other["id"],
                [{"thread_id": f"x{n}", "message_id": "x", "sent_at": NOW.isoformat()}],
            )
        got = cap.tick()
        assert got["outcome"] == "ok" and got["counts"]["considered"] == 0
        assert cap.gmail.meta_reads == []


class TestNeverClaude:
    """NFR-CAP-02; spec §13 S2 line 2 — internal, automated, known and
    ignored conversations never reach Claude (the ignore list from S3)."""

    def test_capture_keeps_filtered_candidates_from_claude(self, cap):
        _ignore(cap, "domain", "vendor.example")
        acme = cap.fake.add(
            "crm_companies",
            **{**DEFAULTS["crm_companies"], "workspace_id": cap.labs["id"], "name": "Acme"},
        )
        cap.fake.add(
            "crm_company_domains",
            workspace_id=cap.labs["id"],
            company_id=acme["id"],
            domain="acme.com",
            is_primary=True,
        )
        cap.conversation("internal", sender="deepika@nunnarilabs.com")
        cap.conversation("news", sender="news@vendor.io", List_Unsubscribe="<mailto:x@vendor.io>")
        cap.conversation("invite", sender="ravi@other.io", subject="Invitation: Sync @ Mon")
        cap.conversation("noreply", sender="no-reply@saas.io")
        cap.conversation("known", sender="ravi@acme.com")
        cap.conversation("ignored", sender="sam@vendor.example")
        got = cap.tick()  # FakeClaude() with no script: any call fails the test.
        assert got["outcome"] == "ok", got
        status = {
            t: cap.candidate(t)["status"]
            for t in ("internal", "news", "invite", "noreply", "known", "ignored")
        }
        assert status == {
            "internal": "skipped",
            "news": "skipped",
            "invite": "skipped",
            "noreply": "skipped",
            "known": "known",
            "ignored": "ignored",
        }
        # S3-04: the dry run's report counts it (FR-CAP-RPT-02).
        assert got["counts"]["ignored"] == 1
        assert cap.fake.capture_report(cap.labs["id"], cap.run["id"])["ignored"] == 1
        assert cap.gmail.text_reads == [] and cap.claude.calls == []
        [filed] = cap.celery.named("crm.capture_thread")
        assert filed["args"] == [cap.labs["id"], cap.mailbox["id"], "known"]
        assert filed["options"]["queue"] == "crm_sync"
        assert got["counts"]["known"] == 1 and got["counts"]["skipped_rules"] == 4

    def test_capture_reads_and_sends_only_the_messages_that_passed(self, cap):
        cap.conversation("t1", text="Please quote for the pilot.")
        at = NOW - timedelta(days=1)
        for mid, labels, sender, text in (
            ("t1-trash", ("CATEGORY_PERSONAL", "TRASH"), "priya@acme-robotics.com", "ZQXTRASH"),
            ("t1-spam", ("CATEGORY_PERSONAL", "SPAM"), "priya@acme-robotics.com", "ZQXSPAM"),
            ("t1-promo", ("INBOX", "CATEGORY_PROMOTIONS"), "priya@acme-robotics.com", "ZQXPROMO"),
            ("t1-internal", ("CATEGORY_PERSONAL",), "deepika@nunnarilabs.com", "ZQXINTERNAL"),
        ):
            cap.gmail.message("t1", mid, sender=sender, to=BOX, at=at, labels=labels, text=text)
        cap.answer(LEAD)
        cap.tick()
        assert cap.gmail.text_reads == ["t1-m1"]
        [prompt] = cap.claude.prompts
        assert "Please quote for the pilot." in prompt
        for marker in ("ZQXTRASH", "ZQXSPAM", "ZQXPROMO", "ZQXINTERNAL"):
            assert marker not in prompt

    def test_capture_a_spam_only_candidate_is_skipped(self, cap):
        cap.conversation("t1", labels=("CATEGORY_PERSONAL", "SPAM"))
        assert cap.tick()["counts"]["skipped_rules"] == 1
        assert cap.candidate("t1")["status"] == "skipped" and cap.gmail.text_reads == []

    def test_capture_a_thread_gone_from_gmail_is_skipped(self, cap):
        cap.conversation("t1")
        cap.gmail.threads.clear()
        cap.tick()
        assert cap.candidate("t1")["status"] == "skipped"


class TestTheDecision:
    """FR-CAP-DIS-05, -06, -07; data-model §3.2."""

    def test_capture_records_the_judged_decision(self, cap):
        cap.conversation(
            "t1", text="Pricing please. Our site: acme-robotics.com", attachments=("brief.pdf",)
        )
        cap.answer(LEAD)
        got = cap.tick()
        row = cap.candidate("t1")
        assert (row["status"], row["outcome"], row["confidence"]) == ("judged", "new_lead", "high")
        assert row["judged_message_id"] == "t1-m1" and row["judged_at"]
        assert row["classifier_version"] == rules.CLASSIFIER_VERSION and row["dry_run"] is True
        assert row["finding"]["company_name"] == "Acme Robotics"
        assert row["finding"]["domain"] == "acme-robotics.com"
        assert row["finding"]["person_email"] == "priya@acme-robotics.com"
        assert row["finding"]["attachments"] == ["brief.pdf"]
        assert row["finding"]["lead_stage"] == "new"  # the owner never replied
        assert got["counts"]["judged"] == 1 and got["counts"]["low_confidence"] == 0
        [call] = cap.claude.calls
        assert call["job"] == "capture"
        assert call["schema"]["additionalProperties"] is False

    def test_capture_other_business_keeps_no_finding(self, cap):
        cap.conversation("t1")
        cap.answer(VENDOR)
        cap.tick()
        row = cap.candidate("t1")
        assert (row["outcome"], row["finding"]) == ("other_business", None)

    def test_capture_low_confidence_is_recorded_and_counted(self, cap):
        cap.conversation("t1")
        cap.answer({**LEAD, "confidence": "low"})
        got = cap.tick()
        assert cap.candidate("t1")["confidence"] == "low"
        assert got["counts"]["low_confidence"] == 1

    def test_capture_usage_rows_are_job_capture(self, cap):
        cap.conversation("t1")
        cap.answer(LEAD)
        cap.tick()
        [usage] = cap.fake.rows["crm_ai_usage"].values()
        assert (usage["job"], usage["outcome"]) == ("capture", "ok")


class TestDryRun:
    """FR-CAP-RPT-01; spec §13 S2 line 1."""

    def test_capture_dry_run_writes_only_decisions(self, cap):
        cap.conversation("t1")
        cap.conversation("t2", sender="ravi@robots.io")
        before = cap.records()
        audit = len(cap.fake.audit)
        cap.answer(LEAD, {**LEAD, "outcome": "deal_signal", "deal_stage": "proposal"})
        cap.tick()
        assert cap.records() == before == (0, 0, 0)
        assert len(cap.fake.audit) == audit
        assert {cap.candidate(t)["status"] for t in ("t1", "t2")} == {"judged"}
        assert cap.fake.rows["crm_email_messages"] == {}


class TestOnce:
    """FR-CAP-DIS-06; spec §13 S3 line 5 (from S2's record)."""

    def test_capture_judges_a_message_once_across_retries(self, cap):
        cap.conversation("t1")
        cap.answer(LEAD)
        cap.tick()
        cap.tick()  # FakeClaude has no second answer: a second call fails.
        assert len(cap.claude.calls) == 1

    def test_capture_a_newer_message_while_judging_wins(self, cap):
        cap.conversation("t1")
        later = NOW - timedelta(hours=1)

        def answer_and_arrive(**kwargs):
            cap.gmail.message("t1", "t1-m2", sender="priya@acme-robotics.com", to=BOX, at=later)
            cap.mark("t1", "t1-m2", later)
            return reply(**kwargs)

        reply = cap.answer(LEAD).messages
        cap.monkeypatch.setattr("app.services.crm.claude.messages", answer_and_arrive)
        got = cap.tick()
        row = cap.candidate("t1")
        assert (row["status"], row["latest_message_id"], row["outcome"]) == (
            "pending",
            "t1-m2",
            None,
        )
        assert got["counts"]["judged"] == 0


class TestFailures:
    """PD-17 — what counts against a conversation, and what ends the run."""

    def test_capture_three_invalid_answers_fail_the_candidate(self, cap):
        cap.conversation("t1")
        cap.answer(default=INVALID)
        for attempt in (1, 2, 3):
            got = cap.tick()
            assert got["outcome"] == "ok" and got["counts"]["failed_items"] == 1
            row = cap.candidate("t1")
            assert row["attempts"] == attempt
        assert row["status"] == "failed"
        cap.tick()
        assert len(cap.claude.calls) == 6

    @pytest.mark.parametrize(
        "failure",
        ["not_configured", "gmail_429", "token_refused", "claude_timeout", "claude_failed"],
    )
    def test_capture_a_service_failure_ends_the_run_with_rows_pending(self, cap, failure):
        # t1 is the older, so the first a dry run takes.
        cap.conversation("t1", at=NOW - timedelta(days=3))
        cap.conversation("t2", sender="ravi@robots.io")
        first = "t1"
        if failure == "not_configured":
            cap.gmail.failing[first] = gmail.NotConfigured("The key is not valid JSON.")
        elif failure == "gmail_429":
            cap.gmail.failing[f"{first}-m1"] = gmail.GmailError("Gmail thread_text failed (429).")
        elif failure == "token_refused":
            cap.gmail.failing[first] = gmail.GmailError(
                "The Gmail token request was refused (401 unauthorized_client)."
            )
        cap.answer(
            TIMEOUT
            if failure == "claude_timeout"
            else FAIL
            if failure == "claude_failed"
            else LEAD,
            default=LEAD,
        )
        got = cap.tick()
        assert got["outcome"] == "failed", got
        for thread in ("t1", "t2"):
            row = cap.candidate(thread)
            assert (row["status"], row["attempts"]) == ("pending", 0)
        [run] = [
            r
            for r in cap.fake.rows["crm_job_runs"].values()
            if r["job"] == "capture" and r["workspace_id"] == cap.labs["id"]
        ]
        assert run["error"] == run["error_public"]
        assert "acme" not in run["error"].lower() and "priya" not in run["error"].lower()
        assert run["counts"]["considered"] == 1

    def test_capture_three_failed_runs_alert_the_admins(self, cap):
        cap.conversation("t1")
        cap.gmail.failing["t1"] = gmail.GmailError("Gmail thread failed (503).")
        for _ in range(3):
            assert cap.tick()["outcome"] == "failed"
        assert any(n["kind"] == "job_failed" for n in cap.fake.rows["crm_notifications"].values())


class TestTheChain:
    """FR-CAP-DIS-10, plan §5.7 — a dry run with work left follows itself."""

    def test_capture_time_out_chains_once(self, cap, monkeypatch):
        cap.conversation("t1")
        cap.conversation("t2", sender="ravi@robots.io")
        cap.answer(LEAD)
        seen = {"n": 0}

        def out_of_time():
            seen["n"] += 1
            return seen["n"] > 1

        monkeypatch.setattr(jobs, "out_of_time", out_of_time)
        crm_ai.capture_conversations()
        [follow] = cap.celery.named("crm_ai.capture")
        # The guard holds the follow-up's own task id, so only it clears it.
        assert follow["options"] == {
            "countdown": 60,
            "queue": "crm-ai",
            "task_id": cap.redis.keys[capture.NEXT_KEY],
        }
        assert follow["args"] == [] and follow["kwargs"] == {}
        # A second stop while the follow-up is queued queues nothing more.
        capture._more.add(cap.labs["id"])
        assert capture.wants_more() is True and capture.chain() is False
        assert len(cap.celery.named("crm_ai.capture")) == 1

    def test_capture_an_hourly_tick_while_a_follow_up_waits_queues_no_second(
        self, cap, monkeypatch
    ):
        # Plan §5.7: only the follow-up clears the guard, so one is ever queued.
        cap.conversation("t1")
        monkeypatch.setattr(jobs, "out_of_time", lambda: True)
        crm_ai.capture_conversations()  # an hourly tick: queues F1
        [first] = cap.celery.named("crm_ai.capture")
        crm_ai.capture_conversations()  # the next tick lands while F1 waits
        assert cap.celery.named("crm_ai.capture") == [first]
        assert cap.redis.keys[capture.NEXT_KEY] == first["options"]["task_id"]
        # F1 itself runs: it clears the guard and queues the one next.
        crm_ai.capture_conversations.apply(task_id=first["options"]["task_id"])
        sent = cap.celery.named("crm_ai.capture")
        assert len(sent) == 2
        assert sent[1]["options"]["task_id"] != first["options"]["task_id"]
        assert cap.redis.keys[capture.NEXT_KEY] == sent[1]["options"]["task_id"]

    def test_capture_a_closed_gate_ends_the_chain(self, cap):
        cap.conversation("t1")
        cap.fake.set_ai(cap.labs, budget_usd=0)
        crm_ai.capture_conversations()
        assert cap.celery.named("crm_ai.capture") == []

    def test_capture_the_task_stores_no_result_and_takes_no_argument(self):
        assert crm_ai.capture_conversations.ignore_result is True
        assert crm_ai.capture_conversations.name == "crm_ai.capture"

    def test_capture_the_task_has_a_soft_limit_below_the_hard_one(self):
        # OPS-2: a slow conversation ends the run, never kills the child.
        from app.worker import celery_app

        soft = crm_ai.capture_conversations.soft_time_limit
        assert soft is not None and jobs.AI_TASK_SECONDS < soft < celery_app.conf.task_time_limit

    @pytest.mark.parametrize("where", ["gmail", "claude"])
    def test_capture_the_soft_limit_ends_the_run_with_its_counts(self, cap, monkeypatch, where):
        # OPS-2: as out of time: the run is recorded with its counts, the
        # candidate stays pending, and the dry run follows itself.
        from celery.exceptions import SoftTimeLimitExceeded

        cap.conversation("t1", at=NOW - timedelta(days=3))
        cap.conversation("t2", sender="ravi@robots.io")
        cap.answer(LEAD, LEAD)
        if where == "gmail":
            real = cap.gmail.thread_text

            def slow(mailbox, ids):
                if "t2-m1" in ids:
                    raise SoftTimeLimitExceeded()
                return real(mailbox, ids)

            monkeypatch.setattr(gmail, "thread_text", slow)
        else:
            reply = cap.claude.messages
            calls = {"n": 0}

            def slow(**kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise SoftTimeLimitExceeded()
                return reply(**kwargs)

            monkeypatch.setattr("app.services.crm.claude.messages", slow)
        result = crm_ai.capture_conversations()
        got = result["runs"]["labs"]
        assert got["outcome"] == "ok", got
        assert got["counts"]["judged"] == 1 and got["counts"]["pending_left"] == 1
        assert cap.candidate("t1")["status"] == "judged"
        assert cap.candidate("t2")["status"] == "pending"
        assert len(cap.celery.named("crm_ai.capture")) == 1

    def test_capture_reads_at_most_the_messages_the_prompt_holds(self, cap):
        # OPS-2: a long conversation costs at most 12 text reads, its latest.
        for n in range(15):
            cap.gmail.message(
                "t1",
                f"t1-m{n:02d}",
                sender="priya@acme-robotics.com",
                to=BOX,
                at=NOW - timedelta(days=20 - n),
            )
        cap.mark("t1", "t1-m14", NOW - timedelta(days=6))
        cap.answer(LEAD)
        cap.tick()
        assert cap.gmail.text_reads == [f"t1-m{n:02d}" for n in range(3, 15)]
        row = cap.candidate("t1")
        assert row["status"] == "judged" and row["finding"]["message_count"] == 15

    def test_capture_the_beat_runs_it_hourly_at_minute_seven(self):
        from app.worker import celery_app

        entry = celery_app.conf.beat_schedule["crm-ai-capture"]
        assert entry["task"] == "crm_ai.capture"
        assert entry["schedule"].minute == {7} and entry["schedule"].hour == set(range(24))
        assert celery_app.amqp.router.route({}, "crm_ai.capture")["queue"].name == "crm-ai"


class TestRecall:
    """FR-CAP-RPT-03, -05 to -07, plan §5.7, R1.3, PD-11, PD-27, PD-28; spec
    §13 S2 line 3, §14.5 I2."""

    SALES = ("INBOX", "Label_1")

    def _labelled(self, cap, thread, labels, sender, *, at=None, text="Pricing?", **headers):
        at = at or NOW - timedelta(days=30)
        cap.gmail.message(
            thread,
            f"{thread}-m1",
            sender=sender,
            to=BOX,
            at=at,
            labels=labels,
            text=text,
            **headers,
        )

    def _company(self, cap, name, domain, created_at):
        company = cap.fake.add(
            "crm_companies",
            **{
                **DEFAULTS["crm_companies"],
                "workspace_id": cap.labs["id"],
                "name": name,
                "created_at": created_at,
            },
        )
        cap.fake.add(
            "crm_company_domains",
            workspace_id=cap.labs["id"],
            company_id=company["id"],
            domain=domain,
            is_primary=True,
        )
        return company

    def test_capture_recall_gives_every_labelled_thread_a_fate(self, cap):
        primary_sales = (*self.SALES, "CATEGORY_PERSONAL")
        self._labelled(cap, "r-out", self.SALES, "a@outside.io")
        self._labelled(cap, "r-bulk", primary_sales, "news@bulk.io", List_Unsubscribe="<x>")
        self._labelled(cap, "r-known", primary_sales, "ravi@old.io")
        self._company(cap, "Old", "old.io", "2026-01-01T00:00:00+00:00")
        # Automation 2 made this company from the labelled mail itself: it
        # is not `filed_known`, and, a client now, never sent to Claude (G-5).
        self._labelled(cap, "r-auto", primary_sales, "priya@acme-robotics.com")
        self._company(cap, "Acme Robotics", "acme-robotics.com", NOW.isoformat())
        self._labelled(cap, "r-new", primary_sales, "sam@newco.io")
        self._labelled(cap, "r-vendor", primary_sales, "sales@vendor.io")
        cap.answer(LEAD, VENDOR)
        got = cap.tick()
        assert got["outcome"] == "ok", got
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert run["finished_at"] is not None
        fates = {r["thread_id"]: r["fate"] for r in run["recall"]}
        assert fates == {
            "r-out": "outside_scope",
            "r-bulk": "automated",
            "r-known": "filed_known",
            "r-auto": "known_now",
            "r-new": "found",
            "r-vendor": "judged_otherwise",
        }
        assert {r["mailbox_id"] for r in run["recall"]} == {cap.mailbox["id"]}
        assert all(
            set(r) == {"mailbox_id", "thread_id", "latest_at", "fate"} for r in run["recall"]
        )
        # Nothing is stored for a labelled thread but its fate.
        assert cap.fake.rows["crm_capture_candidates"] == {}
        assert len(cap.claude.calls) == 2
        assert sorted(cap.gmail.text_reads) == ["r-new-m1", "r-vendor-m1"]
        recall = capture.overview(_Ctx(cap))["dry_run"]["recall"]
        # FR-CAP-RPT-07: filed as known is not tested (PD-28).
        assert recall["line"] == (
            "Of the 2 that could be tested, the dry run found 1. 4 could not be tested: "
            "2 involve contacts now in the CRM (filed automatically), 1 was automated mail, "
            "1 is outside Primary and Sent."
        )
        assert recall["summary"]["untested"]["known"] == 2

    def test_capture_recall_names_each_reason(self, cap, monkeypatch):
        # Plan R1.3: the rules' reasons from headers only; Gmail's empty or
        # malformed answer is not judged; a lead is still judged.
        primary_sales = (*self.SALES, "CATEGORY_PERSONAL")
        self._labelled(cap, "r-internal", primary_sales, "deepika@nunnarilabs.com")
        self._labelled(
            cap,
            "r-cal",
            primary_sales,
            "Google Calendar <calendar-notification@google.com>",
            subject="Accepted: Demo @ Tue",
        )
        self._labelled(cap, "r-bulk", primary_sales, "news@bulk.io", List_Unsubscribe="<x>")
        self._labelled(cap, "r-empty", primary_sales, "sam@empty.io")
        self._labelled(cap, "r-malformed", primary_sales, "sam@broken.io")
        cap.gmail.threads["r-malformed"][0]["headers"] = None
        # A date that is not a number is malformed too (plan R1.3 step 1).
        self._labelled(cap, "r-baddate", primary_sales, "sam@baddate.io")
        # FR-CAP-RPT-06: an invitation between colleagues only is internal-only.
        self._labelled(
            cap,
            "r-colleague-invite",
            primary_sales,
            "Google Calendar <calendar-notification@google.com>",
            subject="Invitation: Standup @ Mon",
            Reply_To="Deepika <deepika@nunnarilabs.com>",
        )
        self._labelled(cap, "r-new", primary_sales, "Priya Shah <priya@acme-robotics.com>")
        # A tie between a calendar notice and automated mail goes to calendar.
        self._labelled(
            cap,
            "r-tie",
            primary_sales,
            "Google Calendar <calendar-notification@google.com>",
            subject="Declined: Demo",
        )
        cap.gmail.message(
            "r-tie",
            "r-tie-m2",
            sender="news@bulk.io",
            to=BOX,
            at=NOW - timedelta(days=29),
            labels=primary_sales,
            List_Unsubscribe="<x>",
        )
        meta = gmail.thread_meta

        def thread_meta(mailbox, thread_id):
            got = meta(mailbox, thread_id)
            if thread_id == "r-baddate":
                got["messages"][0]["internalDate"] = "abc"
            return {"id": thread_id, "messages": []} if thread_id == "r-empty" else got

        monkeypatch.setattr(gmail, "thread_meta", thread_meta)
        cap.answer(LEAD)
        got = cap.tick()
        assert got["outcome"] == "ok", got
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert {r["thread_id"]: r["fate"] for r in run["recall"]} == {
            "r-internal": "internal_only",
            "r-cal": "calendar",
            "r-bulk": "automated",
            "r-empty": "not_judged",
            "r-malformed": "not_judged",
            "r-baddate": "not_judged",
            "r-colleague-invite": "internal_only",
            "r-new": "found",
            "r-tie": "calendar",
        }
        assert cap.gmail.text_reads == ["r-new-m1"]
        summary = capture.overview(_Ctx(cap))["dry_run"]["recall"]["summary"]
        assert summary["untested"] == {
            "known": 0,
            "ignored": 0,
            "internal_only": 2,
            "calendar": 2,
            "automated": 1,
            "outside_scope": 0,
            "not_judged": 3,
            "skipped_rules": 0,
        }

    def test_capture_recall_counts_a_failure_as_not_judged(self, cap):
        # FR-CAP-RPT-06: Claude failing on a conversation is not a judgement.
        primary_sales = (*self.SALES, "CATEGORY_PERSONAL")
        self._labelled(cap, "r-new", primary_sales, "sam@newco.io")
        cap.answer(default=INVALID)
        got = cap.tick()
        assert got["outcome"] == "ok", got
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert run["finished_at"] is not None
        assert {r["thread_id"]: r["fate"] for r in run["recall"]} == {"r-new": "not_judged"}
        assert capture.overview(_Ctx(cap))["dry_run"]["recall"]["line"] == (
            "None of your 1 labelled conversation could be tested: 1 could not be judged."
        )

    def test_capture_recall_marks_a_run_capped_only_with_threads_left(self, cap, monkeypatch):
        # Plan R1.3: the marker only when the check stops at the cap with a
        # labelled conversation unchecked; a mailbox with exactly the cap is not.
        monkeypatch.setattr(rules, "RECALL_CAP", 2)
        primary_sales = (*self.SALES, "CATEGORY_PERSONAL")
        for n in range(2):
            self._labelled(cap, f"r{n}", primary_sales, f"news{n}@bulk.io", List_Unsubscribe="<x>")
        cap.tick()
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert [r.get("thread_id") for r in run["recall"]] == ["r0", "r1"]
        assert not capture.overview(_Ctx(cap))["dry_run"]["recall"]["summary"]["capped"]
        self._labelled(cap, "r2", primary_sales, "news2@bulk.io", List_Unsubscribe="<x>")
        run.update(finished_at=None, recall=[])
        cap.tick()
        assert run["recall"][-1] == {"capped": True}
        assert len(run["recall"]) == 3
        recall = capture.overview(_Ctx(cap))["dry_run"]["recall"]
        assert [r["thread_id"] for r in recall["rows"]] == ["r0", "r1"]
        assert recall["summary"]["capped"] is True
        assert recall["line"].endswith(" Only the first 2 were checked.")

    def test_capture_recall_never_sends_an_ignored_or_known_client_message(self, cap):
        # PRIV-1, PRIV-2: the recall applies live's ignore list and known
        # check before any text; a mixed thread sends only its unknown part.
        primary_sales = (*self.SALES, "CATEGORY_PERSONAL")
        _ignore(cap, "domain", "vendor.io")
        self._labelled(cap, "r-ignored", primary_sales, "sales@vendor.io", text="ZQXIGNORED")
        self._company(cap, "Hearsight", "hearsight.com", NOW.isoformat())
        self._labelled(cap, "r-mixed", primary_sales, "client@hearsight.com", text="ZQXCLIENT")
        cap.gmail.message(
            "r-mixed",
            "r-mixed-m2",
            sender="newperson@acme.io",
            to=BOX,
            at=NOW - timedelta(days=20),
            labels=primary_sales,
            text="Could we get a quote?",
        )
        cap.answer(LEAD)
        got = cap.tick()
        assert got["outcome"] == "ok", got
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert {r["thread_id"]: r["fate"] for r in run["recall"]} == {
            "r-ignored": "ignored",
            "r-mixed": "found",
        }
        assert cap.gmail.text_reads == ["r-mixed-m2"]
        [prompt] = cap.claude.prompts
        assert "ZQXIGNORED" not in prompt and "ZQXCLIENT" not in prompt

    def test_capture_recall_out_of_time_resumes_without_judging_twice(self, cap, monkeypatch):
        # Plan §5.7: each fate is kept on the open run as it is decided, so a
        # recall longer than one run's share sends each thread to Claude once.
        primary_sales = (*self.SALES, "CATEGORY_PERSONAL")
        for n in range(3):
            self._labelled(cap, f"r{n}", primary_sales, f"priya@lead{n}.io")
        cap.answer(LEAD, VENDOR, LEAD)
        seen = {"n": 0}

        def out_of_time():
            seen["n"] += 1
            return seen["n"] == 3

        monkeypatch.setattr(jobs, "out_of_time", out_of_time)
        crm_ai.capture_conversations()
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert run["finished_at"] is None
        assert [r["thread_id"] for r in run["recall"]] == ["r0", "r1"]
        assert len(cap.claude.calls) == 2
        # The open run's report shows no partial recall.
        assert capture.overview(_Ctx(cap))["dry_run"]["recall"]["rows"] == []
        [follow] = cap.celery.named("crm_ai.capture")
        crm_ai.capture_conversations.apply(task_id=follow["options"]["task_id"])
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert run["finished_at"] is not None
        assert {r["thread_id"]: r["fate"] for r in run["recall"]} == {
            "r0": "found",
            "r1": "judged_otherwise",
            "r2": "found",
        }
        assert len(cap.claude.calls) == 3
        assert sorted(cap.gmail.text_reads) == ["r0-m1", "r1-m1", "r2-m1"]
        assert sorted(cap.gmail.meta_reads) == ["r0", "r1", "r2"]

    def test_capture_the_dry_run_waits_for_every_backfill(self, cap):
        cap.fake.rows["crm_mailboxes"][cap.mailbox["id"]]["backfilled_at"] = None
        assert cap.tick()["outcome"] == "ok"
        assert cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] is None

    def test_capture_the_dry_run_does_not_finish_with_candidates_pending(self, cap, monkeypatch):
        cap.conversation("t1")
        monkeypatch.setattr(jobs, "out_of_time", lambda: True)
        got = cap.tick()
        assert got["counts"]["pending_left"] == 1
        assert cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] is None

    def test_capture_after_the_dry_run_finishes_the_job_skips(self, cap):
        cap.tick()
        assert cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] is not None
        cap.conversation("later")
        got = cap.tick()
        assert got["counts"]["skipped"] == rules.DRY_RUN_FINISHED
        assert cap.candidate("later")["status"] == "pending"

    def test_capture_a_finished_runs_counts_stay_as_they_stood(self, cap):
        # Contracts §2.1, plan §5.7: candidates marked after the run are not its.
        cap.conversation("t1")
        cap.answer(VENDOR)
        cap.tick()  # judges t1
        cap.tick()  # nothing pending: the recall, then the run finishes
        assert cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] is not None
        before = capture.overview(_Ctx(cap))["dry_run"]["counts"]
        assert (before["considered"], before["pending"]) == (1, 0)
        cap.conversation("later", sender="ravi@robots.io")
        assert capture.overview(_Ctx(cap))["dry_run"]["counts"] == before


def _ignore(cap, kind: str, value: str) -> None:
    cap.fake.add(
        "crm_capture_ignores",
        workspace_id=cap.labs["id"],
        kind=kind,
        value=value,
        from_item_id=None,
        created_by=OWNER["id"],
        created_at=NOW.isoformat(),
    )


def _items(cap) -> list[dict]:
    return sorted(cap.fake.rows["crm_review_items"].values(), key=lambda i: i["created_at"])


def _company(cap, name, domain=None):
    company = cap.fake.add(
        "crm_companies",
        **{**DEFAULTS["crm_companies"], "workspace_id": cap.labs["id"], "name": name},
    )
    if domain:
        cap.fake.add(
            "crm_company_domains",
            workspace_id=cap.labs["id"],
            company_id=company["id"],
            domain=domain,
            is_primary=True,
        )
    return company


@pytest.fixture
def live(cap) -> Capture:
    """Labs in Live: a dry run was applied before."""
    cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]].update(
        finished_at="2026-10-19T00:00:00+00:00", applied_at="2026-10-19T01:00:00+00:00"
    )
    cap.fake.set_capture(cap.labs, "live")
    return cap


class TestLive:
    """FR-CAP-DIS-07, -10 (live), REV-04; spec §13 S3 line 1."""

    def test_capture_makes_one_new_lead_per_conversation(self, live):
        # Live takes the newest first: t1, t2, then t3.
        live.conversation("t1", at=NOW - timedelta(days=1))
        live.conversation("t2", sender="ravi@robots.io", at=NOW - timedelta(days=2))
        live.conversation("t3", sender="sam@widgets.io", at=NOW - timedelta(days=3))
        live.answer(LEAD, {**LEAD, "confidence": "medium"}, {**LEAD, "confidence": "low"})
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert got["counts"]["items"] == 2 and got["counts"]["low_confidence"] == 1
        items = _items(live)
        assert len(items) == 2
        assert {i["type"] for i in items} == {"new_lead"}
        assert {i["record_id"] for i in items} == {None}
        assert {i["owner_id"] for i in items} == {OWNER["id"]}
        assert {i["source_link"]["thread_id"] for i in items} == {"t1", "t2"}
        low = live.candidate("t3")
        assert (low["confidence"], low["status"]) == ("low", "judged")
        assert low["finding"] is not None
        assert {c["dry_run"] for c in live.fake.rows["crm_capture_candidates"].values()} == {False}
        # Nothing but the queue: no company, no person, no stored mail.
        assert live.fake.rows["crm_companies"] == {} and live.fake.rows["crm_people"] == {}
        assert live.fake.rows["crm_email_messages"] == {}

    def test_capture_live_judges_a_new_candidate_before_older_ones(self, live, monkeypatch):
        for n in range(3):
            live.conversation(f"old{n}", sender=f"a{n}@old{n}.io", at=NOW - timedelta(days=30 + n))
        live.conversation("new", at=NOW - timedelta(minutes=20))
        live.answer(LEAD)
        seen = {"n": 0}

        def out_of_time():
            seen["n"] += 1
            return seen["n"] > 1

        monkeypatch.setattr(jobs, "out_of_time", out_of_time)
        live.tick()
        assert live.candidate("new")["status"] == "judged"
        assert {live.candidate(f"old{n}")["status"] for n in range(3)} == {"pending"}
        [item] = _items(live)
        assert item["source_link"]["thread_id"] == "new"

    def test_capture_a_second_run_makes_no_second_item(self, live):
        live.conversation("t1")
        live.answer(LEAD)
        live.tick()
        got = live.tick()  # FakeClaude has no second answer: a call would fail.
        assert got["outcome"] == "ok" and got["counts"]["items"] == 0
        assert len(_items(live)) == 1 and len(live.claude.calls) == 1

    def test_capture_a_newer_message_refreshes_the_open_item(self, live):
        live.conversation("t1")
        live.answer(LEAD, {**LEAD, "reason": "Asked again for pricing."})
        live.tick()
        [item] = _items(live)
        assert (item["revision"], item["source_version"]) == (1, "t1-m1")
        later = NOW - timedelta(hours=1)
        live.gmail.message("t1", "t1-m2", sender="priya@acme-robotics.com", to=BOX, at=later)
        live.mark("t1", "t1-m2", later)
        got = live.tick()
        assert got["counts"]["items"] == 0
        [item] = _items(live)
        assert (item["revision"], item["source_version"]) == (2, "t1-m2")
        assert item["suggestion"]["evidence"]["reason"] == "Asked again for pricing."
        assert item["suggestion"]["evidence"]["message_count"] == 2

    def test_capture_an_expired_item_is_not_made_again_for_the_same_message(self, live):
        live.conversation("t1")
        live.answer(LEAD, LEAD)
        live.tick()
        [item] = _items(live)
        item.update(status="expired", decided_at=NOW.isoformat())
        # A restart that re-judges the same latest message (FR-CAP-REV-04).
        live.candidate("t1").update(DEFAULTS["crm_capture_candidates"])
        got = live.tick()
        assert got["counts"]["judged"] == 1 and got["counts"]["items"] == 0
        assert len(_items(live)) == 1

    def test_capture_a_claude_deal_signal_on_an_unknown_company_is_a_new_lead(self, live):
        live.conversation("t1")
        live.answer({**LEAD, "outcome": "deal_signal", "deal_stage": "proposal"})
        live.tick()
        [item] = _items(live)
        assert item["type"] == "new_lead"
        assert item["suggestion"]["deal_stage_hint"] == "proposal"

    def test_capture_a_webmail_lead_proposes_no_domain(self, live):
        live.conversation("t1", sender="Priya Shah <priya.shah@gmail.com>")
        live.answer({**LEAD, "person_email": "priya.shah@gmail.com", "website_choice": None})
        live.tick()
        [item] = _items(live)
        assert item["suggestion"]["domain"] is None
        assert item["suggestion"]["email"] == "priya.shah@gmail.com"

    def test_capture_a_fault_storing_the_item_leaves_the_candidate_pending(self, live, monkeypatch):
        # S3-C1: the item is stored before the decision, so a fault in the
        # upsert ends the run with the conversation still to be judged.
        live.conversation("t1")
        live.answer(LEAD, LEAD)
        # Spec 010 R1: items are stored through the group upsert.
        real = capture.review.capture_group_upsert

        def fails(*args, **kwargs):
            raise APIError({"code": "57014", "message": "canceling statement due to timeout"})

        monkeypatch.setattr(capture.review, "capture_group_upsert", fails)
        assert live.tick()["outcome"] == "failed"
        assert live.candidate("t1")["status"] == "pending" and _items(live) == []
        monkeypatch.setattr(capture.review, "capture_group_upsert", real)
        got = live.tick()
        assert got["outcome"] == "ok" and got["counts"]["items"] == 1
        assert live.candidate("t1")["status"] == "judged"
        assert len(_items(live)) == 1

    def test_capture_a_later_match_keeps_one_open_new_lead(self, live):
        # S3-02: an unlinked item is open; a company is then made by hand;
        # the next message links the lead, and the unlinked item expires.
        live.conversation("t1")
        live.answer(LEAD, LEAD)
        live.tick()
        [first] = _items(live)
        assert first["dedupe_key"].endswith("|new_lead|thread")
        company = _company(live, "Acme Robotics")
        later = NOW - timedelta(hours=1)
        live.gmail.message("t1", "t1-m2", sender="priya@acme-robotics.com", to=BOX, at=later)
        live.mark("t1", "t1-m2", later)
        live.tick()
        open_leads = [i for i in _items(live) if i["status"] == "open" and i["type"] == "new_lead"]
        assert len(open_leads) == 1
        assert open_leads[0]["record_id"] == company["id"]
        assert live.fake.rows["crm_review_items"][first["id"]]["status"] == "expired"
        assert any(
            a["action"] == "review.expired" and a["target_id"] == first["id"]
            for a in live.fake.audit
        )


class TestRejudging:
    """FR-CAP-DIS-06, -08 — a conversation judged again from the start keeps
    at most one open capture item, and none when it no longer makes one."""

    @pytest.mark.parametrize("stored", [True, False])
    def test_capture_a_new_sender_in_a_known_thread_is_judged(self, live, stored):
        # CAP-INT-1: the known check is the sync's, message by message. The
        # client's message is filed; the stranger's is judged, alone (G-5).
        _company(live, "Hearsight", "hearsight.com")
        at = NOW - timedelta(days=3)
        live.gmail.message(
            "t1", "t1-m1", sender="client@hearsight.com", to=BOX, at=at, text="ZQXCLIENT"
        )
        if stored:
            live.fake.store_emails(
                live.labs["id"],
                live.mailbox["id"],
                [
                    {
                        "gmail_message_id": "t1-m1",
                        "thread_id": "t1",
                        "subject": "Hello",
                        "sent_at": at.isoformat(),
                        "direction": "inbound",
                        "from_email": "client@hearsight.com",
                        "participants": [],
                        "is_internal": False,
                        "is_auto": False,
                        "links": [],
                    }
                ],
            )
        later = NOW - timedelta(days=1)
        live.gmail.message("t1", "t1-m2", sender="New Person <newperson@acme.io>", to=BOX, at=later)
        live.mark("t1", "t1-m2", later)
        live.answer({**LEAD, "company_name": "Acme IO", "person_email": "newperson@acme.io"})
        got = live.tick()
        assert got["outcome"] == "ok", got
        row = live.candidate("t1")
        assert (row["status"], row["outcome"]) == ("judged", "new_lead")
        assert live.gmail.text_reads == ["t1-m2"]
        [prompt] = live.claude.prompts
        assert "ZQXCLIENT" not in prompt
        [item] = _items(live)
        assert item["suggestion"]["email"] == "newperson@acme.io"
        filed = live.celery.named("crm.capture_thread")
        assert len(filed) == (0 if stored else 1)

    def test_capture_a_lead_turned_deal_signal_keeps_one_open_item(self, live):
        # CAP-INT-3: a webmail lead, a company then made by hand by name, a
        # deal signal at the next message: the earlier new lead expires.
        live.conversation("t1", sender="Priya Shah <priya.shah@gmail.com>")
        signal = {
            **LEAD,
            "outcome": "deal_signal",
            "confidence": "medium",
            "deal_stage": "discovery",
            "person_email": "priya.shah@gmail.com",
            "website_choice": None,
        }
        live.answer(signal, signal)
        live.tick()
        [first] = _items(live)
        assert first["type"] == "new_lead"
        company = _company(live, "Acme Robotics")
        later = NOW - timedelta(hours=1)
        live.gmail.message("t1", "t1-m2", sender="priya.shah@gmail.com", to=BOX, at=later)
        live.mark("t1", "t1-m2", later)
        live.tick()
        opened = [i for i in _items(live) if i["status"] == "open"]
        assert [(i["type"], i["record_id"]) for i in opened] == [("deal_signal", company["id"])]
        assert live.fake.rows["crm_review_items"][first["id"]]["status"] == "expired"

    @pytest.mark.parametrize(
        "answer",
        [
            {**LEAD, "outcome": "not_relevant", "company_name": None, "website_choice": None},
            {**LEAD, "confidence": "low"},
        ],
        ids=["not_relevant", "low_confidence"],
    )
    def test_capture_a_rejudgement_with_no_item_expires_the_open_one(self, live, answer):
        # CAP-INT-4: "sorry, wrong company" at the next message.
        live.conversation("t1")
        live.answer(LEAD, answer)
        live.tick()
        [first] = _items(live)
        # A deal document's signal on the same conversation is the sync's.
        document = live.fake.add_review_item(
            live.labs,
            type="deal_signal",
            record_type="company",
            record_id=_company(live, "Other", "other.example")["id"],
            source_key=first["source_key"],
            suggestion={
                "action": "move",
                "stage": "proposal",
                "evidence": {"origin": "deal_document"},
            },
        )
        later = NOW - timedelta(hours=1)
        live.gmail.message("t1", "t1-m2", sender="priya@acme-robotics.com", to=BOX, at=later)
        live.mark("t1", "t1-m2", later)
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert live.candidate("t1")["status"] == "judged"
        assert live.fake.rows["crm_review_items"][first["id"]]["status"] == "expired"
        assert live.fake.rows["crm_review_items"][document["id"]]["status"] == "open"


class TestIgnore:
    """FR-CAP-DIS-03; spec §13 S3 line 3."""

    def test_capture_ignored_domain_never_reaches_claude(self, live):
        live.fake.add(
            "crm_capture_ignores",
            workspace_id=live.labs["id"],
            kind="domain",
            value="acme-robotics.com",
            from_item_id=None,
            created_by=OWNER["id"],
            created_at=NOW.isoformat(),
        )
        live.conversation("t1")
        live.conversation("t2", sender="ravi@acme-robotics.com")
        got = live.tick()  # FakeClaude() with no script: any call fails the test.
        assert got["outcome"] == "ok", got
        assert {live.candidate(t)["status"] for t in ("t1", "t2")} == {"ignored"}
        assert got["counts"]["ignored"] == 2
        assert live.gmail.text_reads == [] and live.claude.calls == []
        assert _items(live) == []

    def test_capture_an_ignored_address_spares_the_rest_of_its_domain(self, live):
        live.fake.add(
            "crm_capture_ignores",
            workspace_id=live.labs["id"],
            kind="address",
            value="priya@acme-robotics.com",
            from_item_id=None,
            created_by=OWNER["id"],
            created_at=NOW.isoformat(),
        )
        live.conversation("t1")
        live.conversation("t2", sender="ravi@acme-robotics.com")
        live.answer(LEAD)
        live.tick()
        assert live.candidate("t1")["status"] == "ignored"
        assert live.candidate("t2")["status"] == "judged"


class TestDuplicateLeads:
    """FR-CAP-DIS-08 — a lead that is an existing company links to it."""

    @pytest.mark.parametrize("match", ["domain_from_text", "sibling", "name"])
    def test_capture_a_duplicate_makes_a_linked_item(self, live, match):
        if match == "domain_from_text":
            company = _company(live, "Robotics Co", "acme-robotics.com")
            live.conversation(
                "t1",
                sender="Priya <priya.shah@gmail.com>",
                text="Pricing please. Our site is acme-robotics.com",
            )
            answer = {**LEAD, "company_name": "Zz", "person_email": "priya.shah@gmail.com"}
        elif match == "sibling":
            company = _company(live, "Robotics Co", "acme-robotics.io")
            live.conversation("t1")
            answer = {**LEAD, "company_name": "Zz"}
        else:
            company = _company(live, "Acme Robotics")
            live.conversation("t1")
            answer = LEAD
        live.answer(answer)
        live.tick()
        [item] = _items(live)
        assert (item["type"], item["record_type"], item["record_id"]) == (
            "new_lead",
            "company",
            company["id"],
        )
        assert item["suggestion"]["link"] is True
        assert item["dedupe_key"].endswith(f"|new_lead|company:{company['id']}")
        assert live.candidate("t1")["finding"]["duplicate_company_id"] == company["id"]

    def test_capture_the_sibling_check_reads_only_domains_of_that_name(self, live, monkeypatch):
        # S3-C4: an unfiltered read is cut at PostgREST's row limit; the
        # check reads the domains of the lead's name only.
        company = _company(live, "Robotics Co", "acme-robotics.io")
        for n in range(5):
            _company(live, f"Other {n}", f"other{n}.com")
        real = live.fake.list_domains

        def filtered_only(workspace_id, *, company_ids=None, domains=None):
            assert company_ids is not None or domains is not None, "an unfiltered read"
            return real(workspace_id, company_ids=company_ids, domains=domains)

        monkeypatch.setattr(capture.store, "list_domains", filtered_only)
        live.conversation("t1")
        live.answer({**LEAD, "company_name": "Zz"})
        live.tick()
        [item] = _items(live)
        assert item["record_id"] == company["id"]

    def test_capture_a_shared_subdomain_label_is_no_match(self, live):
        # S3-C5, S3-01: mail.foo.com and mail.bar.io are different companies.
        _company(live, "Bar", "mail.bar.io")
        live.conversation("t1", sender="Sam <sam@mail.foo.com>")
        live.answer({**LEAD, "company_name": "Zz", "person_email": "sam@mail.foo.com"})
        live.tick()
        [item] = _items(live)
        assert (item["record_id"], item["suggestion"]["link"]) == (None, False)

    def test_capture_a_deal_signal_on_a_duplicate_is_a_deal_signal(self, live):
        company = _company(live, "Acme Robotics")
        live.conversation("t1")
        live.answer({**LEAD, "outcome": "deal_signal", "deal_stage": "proposal"})
        live.tick()
        [item] = _items(live)
        assert (item["type"], item["record_id"]) == ("deal_signal", company["id"])
        assert item["suggestion"]["action"] == "create"
        assert item["suggestion"]["stage"] == "discovery"


class TestApply:
    """FR-CAP-RPT-04; spec §13 S3 line 6 — applying a dry run makes items
    with no new Claude call and sets Live."""

    def test_capture_applying_a_dry_run_makes_items_without_claude(self, cap):
        # A dry run takes the oldest first: t1, t2, then t3.
        cap.conversation("t1", at=NOW - timedelta(days=3))
        cap.conversation("t2", sender="ravi@robots.io", at=NOW - timedelta(days=2))
        cap.conversation("t3", sender="sam@widgets.io", at=NOW - timedelta(days=1))
        cap.answer(LEAD, {**LEAD, "confidence": "low"}, VENDOR)
        cap.tick()
        assert cap.records() == (0, 0, 0)
        cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        cap.answer()  # no script: a Claude call fails the test
        r = cap.http.post(
            f"/api/v1/crm/capture/dry-runs/{cap.run['id']}/apply?workspace=labs",
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["items_created"], body["mode"]) == (1, "live")
        assert body["applied_at"]
        assert cap.claude.calls == []
        [item] = _items(cap)
        assert item["type"] == "new_lead" and item["source_link"]["thread_id"] == "t1"
        assert item["source_version"] == "t1-m1"
        assert cap.fake.capture_setting(cap.labs["id"]) == {"mode": "live"}
        assert {c["dry_run"] for c in cap.fake.rows["crm_capture_candidates"].values()} == {False}
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert run["applied_by"] == OWNER["id"]
        # Live from here: an applied run is not applied twice.
        again = cap.http.post(
            f"/api/v1/crm/capture/dry-runs/{cap.run['id']}/apply?workspace=labs",
            headers=as_(OWNER["id"]),
        )
        assert again.status_code == 409, again.text
        assert len(_items(cap)) == 1

    def _finished_run(self, cap) -> None:
        cap.conversation("t1")
        cap.answer(LEAD)
        cap.tick()
        cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()

    def _apply(self, cap):
        r = cap.http.post(
            f"/api/v1/crm/capture/dry-runs/{cap.run['id']}/apply?workspace=labs",
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        return r.json()

    @pytest.mark.parametrize(
        ("kind", "value"), [("domain", "acme-robotics.com"), ("address", "priya@acme-robotics.com")]
    )
    def test_capture_an_ignore_added_before_applying_makes_no_item(self, cap, kind, value):
        # S3-PRIV-1: apply makes items as live would, ignore list included.
        self._finished_run(cap)
        _ignore(cap, kind, value)
        assert self._apply(cap)["items_created"] == 0
        assert _items(cap) == []

    @pytest.mark.parametrize("change", ["labels", "inactive", "owner_gone"])
    def test_capture_a_mailbox_no_longer_capturing_makes_no_item(self, cap, change):
        # S3-PRIV-2: only the mailboxes the live job reads (NFR-CAP-05).
        self._finished_run(cap)
        row = cap.fake.rows["crm_mailboxes"][cap.mailbox["id"]]
        if change == "labels":
            row["capture_source"] = "labels"
        elif change == "inactive":
            row["is_active"] = False
        else:
            row["owner_id"] = "u-nobody"
        assert self._apply(cap)["items_created"] == 0
        assert _items(cap) == []


# ---------------------------------------------------------------------------
# Spec 010 R1 — one suggestion per company (FR-CAP-GRP; tasks T416)
# ---------------------------------------------------------------------------


def _open(cap) -> list[dict]:
    return [i for i in _items(cap) if i["status"] == "open"]


def _deal_row(cap, company, stage: str) -> dict:
    return cap.fake.add(
        "crm_opportunities",
        workspace_id=cap.labs["id"],
        company_id=company["id"],
        name=f"{company['name']} {stage}",
        stage=stage,
        deleted_at=None,
        created_at="2026-09-01T00:00:00+00:00",
    )


def _signal(cap, company, deals, *, stage, at, thread, message, confidence="high"):
    """A discovered `deal_signal` on a matched company, as `item_for` makes it."""
    finding = {
        **FOUND,
        "deal_stage": stage,
        "latest_at": at.astimezone(rules.ZONE).isoformat(),
        "first_at": at.astimezone(rules.ZONE).isoformat(),
    }
    return rules.to_item(
        finding,
        outcome="deal_signal",
        confidence=confidence,
        mailbox_id=cap.mailbox["id"],
        thread_id=thread,
        message_id=message,
        owner_id=OWNER["id"],
        model="claude-sonnet-5-5",
        generated_at=NOW.isoformat(),
        duplicate_company_id=company["id"],
        open_deals=deals,
    )


FOUND = {
    "company_name": "Acme Robotics",
    "domain": "acme-robotics.com",
    "domain_from_text": False,
    "person_name": "Priya Shah",
    "person_email": "priya@acme-robotics.com",
    "person_title": None,
    "lead_stage": "contacted",
    "deal_stage": None,
    "reason": "Asked for pricing.",
    "first_at": "2026-10-01T09:00:00+05:30",
    "latest_at": "2026-10-01T09:00:00+05:30",
    "message_count": 1,
    "owner_replied": False,
    "attachments": [],
    "duplicate_company_id": None,
}


class TestGrouping:
    """Spec 010 R1 FR-CAP-GRP-01 to -07; plan R1.2; spec §14.5 I1."""

    def test_capture_groups_a_company_s_conversations(self, live):
        for n, who in enumerate(("priya", "ravi", "sam"), 1):
            live.conversation(
                f"t{n}", sender=f"{who}@acme-robotics.com", at=NOW - timedelta(days=n)
            )
        live.answer(*({**LEAD, "person_email": f"{w}@acme-robotics.com"} for w in "abc"))
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert got["counts"]["items"] == 1 and got["counts"]["judged"] == 3
        [item] = _items(live)
        assert item["source_key"] == "capture:domain:acme-robotics.com"
        threads = item["source_link"]["threads"]
        assert sorted(t["thread_id"] for t in threads) == ["t1", "t2", "t3"]
        assert {t["person_email"] for t in threads} == {
            "priya@acme-robotics.com",
            "ravi@acme-robotics.com",
            "sam@acme-robotics.com",
        }
        evidence = item["suggestion"]["evidence"]
        assert (evidence["conversations"], evidence["message_count"]) == (3, 3)
        # The latest conversation (t1, newest) gives the person and the top level.
        assert item["source_link"]["thread_id"] == "t1"
        assert item["suggestion"]["email"] == "priya@acme-robotics.com"
        assert item["revision"] == 3

    def test_capture_groups_by_claude_s_company_name(self, live):
        live.conversation("t1", sender="a.k@gmail.com", at=NOW - timedelta(days=1))
        live.conversation("t2", sender="b.r@gmail.com", at=NOW - timedelta(days=2))
        live.conversation("t3", sender="c.x@gmail.com", at=NOW - timedelta(days=3))
        live.conversation("t4", sender="d.y@gmail.com", at=NOW - timedelta(days=4))
        named = {**LEAD, "company_name": "Zeta Labs", "website_choice": None}
        nameless = {**LEAD, "company_name": None, "website_choice": None}
        live.answer(
            {**named, "person_email": "a.k@gmail.com"},
            {**named, "person_email": "b.r@gmail.com"},
            {**nameless, "person_email": "c.x@gmail.com"},
            {**nameless, "person_email": "d.y@gmail.com"},
        )
        live.tick()
        keys = sorted(i["source_key"] for i in _open(live))
        assert keys == [
            "capture:email:c.x@gmail.com",
            "capture:email:d.y@gmail.com",
            "capture:name:zeta labs",
        ]
        [zeta] = [i for i in _open(live) if i["source_key"] == "capture:name:zeta labs"]
        assert len(zeta["source_link"]["threads"]) == 2

    def test_capture_apply_in_reverse_order_gives_the_same_item(self, live):
        def lead(n: int) -> dict:
            at = NOW - timedelta(days=n)
            return rules.to_item(
                {
                    **FOUND,
                    "person_email": f"p{n}@acme-robotics.com",
                    "reason": f"Conversation {n}.",
                    "latest_at": at.astimezone(rules.ZONE).isoformat(),
                },
                outcome="new_lead",
                confidence="high",
                mailbox_id=live.mailbox["id"],
                thread_id=f"t{n}",
                message_id=f"m{n}",
                owner_id=OWNER["id"],
                model="claude-sonnet-5-5",
                generated_at=NOW.isoformat(),
            )

        shown = []
        for order in ((1, 2, 3), (3, 2, 1)):
            live.fake.rows["crm_review_items"].clear()
            capture.store_items(live.labs["id"], [lead(n) for n in order])
            [item] = _items(live)
            shown.append(
                (
                    item["suggestion"],
                    item["source_version"],
                    item["source_link"]["thread_id"],
                    sorted(t["ref"] for t in item["source_link"]["threads"]),
                )
            )
        assert shown[0] == shown[1]
        assert shown[0][0]["evidence"]["reason"] == "Conversation 1."
        assert shown[0][1] == "m1"

    def test_capture_an_older_signal_never_replaces_a_newer_ones_stage(self, live):
        company = _company(live, "Acme Robotics")
        early = _deal_row(live, company, "discovery")
        late = _deal_row(live, company, "proposal")
        newer = _signal(
            live,
            company,
            [early, late],
            stage="closed_won",
            at=NOW - timedelta(days=1),
            thread="t-won",
            message="m-won",
        )
        older = _signal(
            live,
            company,
            [early, late],
            stage="proposal",
            at=NOW - timedelta(days=5),
            thread="t-prop",
            message="m-prop",
        )
        assert newer["deal_choices"] == sorted([early["id"], late["id"]])
        assert older["deal_choices"] == [early["id"]]
        capture.store_items(live.labs["id"], [newer, older])
        [item] = _items(live)
        assert item["suggestion"]["stage"] == "closed_won"
        assert live.fake._review_deals(item["id"]) == sorted([early["id"], late["id"]])
        assert len(item["source_link"]["threads"]) == 2
        assert item["source_version"] == "m-won"

    def test_capture_one_document_to_two_companies_keeps_a_signal_for_each(self, live):
        from app.services.crm import review

        first, second = _company(live, "Acme"), _company(live, "Beta")
        for company in (first, second):
            deal = _deal_row(live, company, "discovery")
            item = rules.deal_signal_item(
                mailbox_id=live.mailbox["id"],
                thread_id="t-sow",
                message_id="m-sow",
                company_id=company["id"],
                deals=[deal],
                stage="proposal",
                kinds=["sow"],
                direction="outbound",
                message_at=NOW - timedelta(hours=1),
                owner_id=OWNER["id"],
                generated_at=NOW.isoformat(),
            )
            made = review.capture_group_upsert(
                live.labs["id"], item, item["source_link"]["threads"][0]
            )
            assert made["created"], made
        assert sorted(i["record_id"] for i in _open(live)) == sorted([first["id"], second["id"]])
        # Dismissing one company's signal leaves the other's document state.
        acme = next(i for i in _open(live) if i["record_id"] == first["id"])
        live.fake.rows["crm_review_items"][acme["id"]].update(
            status="dismissed", decided_at=NOW.isoformat()
        )
        again = rules.deal_signal_item(
            mailbox_id=live.mailbox["id"],
            thread_id="t-sow",
            message_id="m-sow",
            company_id=second["id"],
            deals=[],
            stage="proposal",
            kinds=["sow"],
            direction="outbound",
            message_at=NOW - timedelta(hours=1),
            owner_id=OWNER["id"],
            generated_at=NOW.isoformat(),
        )
        made = review.capture_group_upsert(
            live.labs["id"], again, again["source_link"]["threads"][0]
        )
        assert not made["skipped"]

    def test_capture_a_dismissed_company_returns_only_for_new_mail(self, live):
        live.conversation("t1")
        live.answer(LEAD, LEAD, {**LEAD, "person_email": "ravi@acme-robotics.com"})
        live.tick()
        [item] = _items(live)
        r = live.http.post(
            f"/api/v1/crm/review/{item['id']}/dismiss?workspace=labs", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        # Re-judged at the same latest message (a restart): not suggested again.
        live.candidate("t1").update(DEFAULTS["crm_capture_candidates"])
        got = live.tick()
        assert got["counts"]["items"] == 0 and got["counts"]["items_skipped"] == 1
        assert _open(live) == []
        # Re-applied: the same finding again is skipped too.
        again = capture.item_for(
            live.labs,
            live.mailbox,
            thread_id="t1",
            message_id="t1-m1",
            outcome="new_lead",
            confidence="high",
            finding=live.candidate("t1")["finding"],
            generated_at=NOW.isoformat(),
        )
        [made] = capture.store_items(live.labs["id"], [again])
        assert made["skipped"] and _open(live) == []
        # A new conversation from the company is suggested.
        live.conversation("t2", sender="ravi@acme-robotics.com")
        got = live.tick()
        assert got["counts"]["items"] == 1
        [fresh] = _open(live)
        assert [t["thread_id"] for t in fresh["source_link"]["threads"]] == ["t2"]

    def test_capture_a_moved_conversation_leaves_its_item(self, live):
        live.conversation("t1", at=NOW - timedelta(days=2))
        live.conversation("t2", sender="ravi@acme-robotics.com", at=NOW - timedelta(days=3))
        live.answer(LEAD, {**LEAD, "person_email": "ravi@acme-robotics.com"})
        live.tick()
        [first] = _items(live)
        assert len(first["source_link"]["threads"]) == 2
        # t1 again: it names another company's site, which Claude picks.
        later = NOW - timedelta(hours=1)
        live.gmail.message(
            "t1",
            "t1-m2",
            sender="priya@acme-robotics.com",
            to=BOX,
            at=later,
            text="I moved to zeta.io, please write to me there.",
        )
        live.mark("t1", "t1-m2", later)
        live.answer({**LEAD, "company_name": "Zeta", "website_choice": 2})
        got = live.tick()
        assert got["counts"]["released"] == 1
        acme = live.fake.rows["crm_review_items"][first["id"]]
        assert acme["status"] == "open"
        assert [t["thread_id"] for t in acme["source_link"]["threads"]] == ["t2"]
        assert acme["suggestion"]["evidence"]["conversations"] == 1
        # FR-CAP-GRP-03: the person suggested is now t2's sender, not t1's.
        assert acme["suggestion"]["email"] == "ravi@acme-robotics.com"
        [zeta] = [i for i in _open(live) if i["source_key"] == "capture:domain:zeta.io"]
        assert [t["thread_id"] for t in zeta["source_link"]["threads"]] == ["t1"]
        # t2 judged no lead now: the Acme item is left empty and expires.
        live.gmail.message("t2", "t2-m2", sender="ravi@acme-robotics.com", to=BOX, at=later)
        live.mark("t2", "t2-m2", later)
        live.answer({**VENDOR})
        live.tick()
        assert live.fake.rows["crm_review_items"][first["id"]]["status"] == "expired"
        assert any(
            a["action"] == "review.expired" and a["target_id"] == first["id"]
            for a in live.fake.audit
        )

    def test_capture_one_deal_signal_per_company(self, live):
        from app.services.crm import review

        company = _company(live, "Acme Robotics")
        deal = _deal_row(live, company, "discovery")
        document = rules.deal_signal_item(
            mailbox_id=live.mailbox["id"],
            thread_id="t-sow",
            message_id="m-sow",
            company_id=company["id"],
            deals=[deal],
            stage="proposal",
            kinds=["sow"],
            direction="outbound",
            message_at=NOW - timedelta(days=3),
            owner_id=OWNER["id"],
            generated_at=NOW.isoformat(),
        )
        review.capture_group_upsert(
            live.labs["id"], document, document["source_link"]["threads"][0]
        )
        live.conversation("t1", at=NOW - timedelta(days=1))
        live.answer({**LEAD, "outcome": "deal_signal", "deal_stage": "proposal"})
        live.tick()
        [item] = _open(live)
        assert (item["type"], item["record_id"]) == ("deal_signal", company["id"])
        assert sorted(t["origin"] for t in item["source_link"]["threads"]) == [
            "deal_document",
            "discovery",
        ]
        assert item["suggestion"]["stage"] == "proposal"

    def test_capture_a_deal_document_s_conversation_outlives_discovery(self, live):
        """PD-39: discovery's signal on a deal document's conversation keeps
        the entry a document's, so a later no-item judgement leaves it."""
        from app.services.crm import review

        company = _company(live, "Acme Robotics")
        deal = _deal_row(live, company, "discovery")
        document = rules.deal_signal_item(
            mailbox_id=live.mailbox["id"],
            thread_id="t1",
            message_id="m-sow",
            company_id=company["id"],
            deals=[deal],
            stage="proposal",
            kinds=["proposal"],
            direction="outbound",
            message_at=NOW - timedelta(days=3),
            owner_id=OWNER["id"],
            generated_at=NOW.isoformat(),
        )
        made = review.capture_group_upsert(
            live.labs["id"], document, document["source_link"]["threads"][0]
        )
        live.conversation("t1", at=NOW - timedelta(days=1))
        live.answer({**LEAD, "outcome": "deal_signal", "deal_stage": "proposal"})
        live.tick()
        [item] = _open(live)
        assert item["id"] == made["id"]
        [entry] = item["source_link"]["threads"]
        assert (entry["message_id"], entry["origin"]) == ("t1-m1", "deal_document")
        later = NOW - timedelta(hours=1)
        live.gmail.message("t1", "t1-m2", sender="priya@acme-robotics.com", to=BOX, at=later)
        live.mark("t1", "t1-m2", later)
        live.answer({**VENDOR})
        live.tick()
        row = live.fake.rows["crm_review_items"][made["id"]]
        assert row["status"] == "open"
        assert [t["thread_id"] for t in row["source_link"]["threads"]] == ["t1"]

    def test_capture_a_refusal_merge_leaves_out_what_another_item_holds(self, live):
        """FR-CAP-GRP-07 (the fake's crm_review_release): a conversation
        moved to another company while the item was claimed is not merged
        back into the fresh item when the approval is refused."""

        def lead(thread, *, domain="acme-robotics.com", message=None, at="2026-10-01"):
            item = rules.to_item(
                {
                    **FOUND,
                    "domain": domain,
                    "person_email": f"{thread}@{domain}",
                    "first_at": f"{at}T09:00:00+05:30",
                    "latest_at": f"{at}T09:00:00+05:30",
                },
                outcome="new_lead",
                confidence="high",
                mailbox_id=live.mailbox["id"],
                thread_id=thread,
                message_id=message or f"{thread}-m1",
                owner_id=OWNER["id"],
                model=None,
                generated_at=NOW.isoformat(),
            )
            [made] = capture.store_items(live.labs["id"], [item])
            return made["id"]

        claimed = lead("t1")
        assert lead("t2", at="2026-10-02") == claimed
        user = {"user_id": OWNER["id"], "kind": "user", "channel": None}
        actor = {**user, "workspace_id": live.labs["id"]}
        live.fake.review_claim(actor, claimed, "approved")
        moved = lead("t2", domain="zeta.io", message="t2-m2", at="2026-10-08")
        fresh = lead("t3", at="2026-10-03")
        assert fresh not in (claimed, moved)
        live.fake.review_release(actor, claimed, "Refused.", None)
        rows = live.fake.rows["crm_review_items"]
        assert rows[claimed]["status"] == "expired"
        assert sorted(t["thread_id"] for t in rows[fresh]["source_link"]["threads"]) == [
            "t1",
            "t3",
        ]
        holders = [
            i["id"]
            for i in _open(live)
            if "t2" in [t["thread_id"] for t in i["source_link"]["threads"]]
        ]
        assert holders == [moved]

    def test_capture_money_is_masked_in_the_suggestion_and_every_reason(self, live):
        live.conversation("t1")
        live.answer({**LEAD, "reason": "Asked for a quote; budget USD 40,000."})
        live.tick()
        [item] = _items(live)
        [entry] = item["source_link"]["threads"]
        assert "40,000" not in entry["reason"]
        assert "40,000" not in item["suggestion"]["evidence"]["reason"]
        # The save-time check itself (FR-CRMAI-LLM-05): a finding's figure is
        # masked by the group upsert's wrapper, whatever reached it.
        lead = rules.to_item(
            {**FOUND, "domain": "zeta.io", "reason": "Budget of Rs 5,00,000 agreed."},
            outcome="new_lead",
            confidence="high",
            mailbox_id=live.mailbox["id"],
            thread_id="t-money",
            message_id="m-money",
            owner_id=OWNER["id"],
            model=None,
            generated_at=NOW.isoformat(),
        )
        [made] = capture.store_items(live.labs["id"], [lead])
        stored = live.fake.rows["crm_review_items"][made["id"]]
        assert "5,00,000" not in json.dumps(stored["suggestion"])
        assert "5,00,000" not in json.dumps(stored["source_link"])


def _old(cap, n: int, *, status="open", **suggestion) -> dict:
    """An open `new_lead` as 073 made it: one per conversation."""
    thread = f"h{n}"
    key = rules.source_key(cap.mailbox["id"], thread)
    return cap.fake.add_review_item(
        cap.labs,
        type="new_lead",
        record_type=None,
        record_id=None,
        owner_id=OWNER["id"],
        source_key=key,
        source_version=f"{thread}-m1",
        source_link={"mailbox_id": cap.mailbox["id"], "thread_id": thread},
        created_at=f"2026-10-{10 + n:02d}T00:00:00+00:00",
        status=status,
        decided_at=None if status == "open" else "2026-10-19T00:00:00+00:00",
        suggestion={
            "company_name": "HearSight",
            "domain": "hearsight.ai",
            "first_name": "Asha",
            "last_name": None,
            "email": f"asha{n}@hearsight.ai",
            "lead_stage": "new",
            "evidence": {
                "first_at": f"2026-10-{n:02d}T09:00:00+05:30",
                "latest_at": f"2026-10-{n:02d}T10:00:00+05:30",
                "message_count": 1,
                "owner_replied": False,
                "reason": f"Conversation {n}.",
                "confidence": "high",
                "origin": "discovery",
            },
            **suggestion,
        },
    )


class TestConsolidation:
    """Spec 010 R1 FR-CAP-GRP-08; plan R1.2, PD-26, PD-40; spec §14.5 I1."""

    def test_capture_consolidates_open_items_once(self, live):
        old = [_old(live, n) for n in range(1, 10)]
        approved = _old(live, 10, status="approved")
        dismissed = _old(live, 11, status="dismissed")
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert (got["counts"]["consolidated"], got["counts"]["rekeyed"]) == (8, 1)
        rows = live.fake.rows["crm_review_items"]
        survivor = rows[old[-1]["id"]]
        assert survivor["status"] == "open"
        assert survivor["source_key"] == "capture:domain:hearsight.ai"
        assert len(survivor["source_link"]["threads"]) == 9
        assert survivor["suggestion"]["evidence"]["conversations"] == 9
        for item in old[:-1]:
            row = rows[item["id"]]
            assert row["status"] == "expired"
            assert row["result"] == {
                "superseded_by": survivor["id"],
                "reason": "Merged into one suggestion for HearSight.",
            }
            [audit] = [
                a
                for a in live.fake.audit
                if a["target_id"] == item["id"] and a["action"] == "review.expired"
            ]
            assert audit["after"]["superseded_by"] == survivor["id"]
            # The loser keeps its own reference.
            assert "threads" not in row["source_link"] and row["source_link"]["thread_id"]
        assert rows[approved["id"]]["status"] == "approved"
        assert rows[dismissed["id"]]["status"] == "dismissed"
        assert "superseded_by" not in rows[dismissed["id"]]["result"]
        before = json.dumps(rows, sort_keys=True, default=str)
        again = live.tick()
        assert "consolidated" not in again["counts"] or again["counts"]["consolidated"] == 0
        assert json.dumps(rows, sort_keys=True, default=str) == before

    def test_capture_an_open_item_on_the_new_key_takes_the_old_ones(self, live):
        # Keyed by Claude's name: no domain.
        old = [_old(live, n, domain=None) for n in (1, 2)]
        new = rules.to_item(
            {**FOUND, "domain": None, "company_name": "HearSight"},
            outcome="new_lead",
            confidence="high",
            mailbox_id=live.mailbox["id"],
            thread_id="t9",
            message_id="t9-m1",
            owner_id=OWNER["id"],
            model=None,
            generated_at=NOW.isoformat(),
        )
        [made] = capture.store_items(live.labs["id"], [new])
        live.fake.rows["crm_review_items"][made["id"]]["created_at"] = "2026-10-01T00:00:00+00:00"
        assert capture._consolidate(live.labs) == {"consolidated": 2, "rekeyed": 1}
        [item] = _open(live)
        assert item["source_key"] == "capture:name:hearsight"
        assert len(item["source_link"]["threads"]) == 3
        # The newest by creation survives, holding every conversation.
        assert item["id"] == old[-1]["id"]
        assert (
            live.fake.rows["crm_review_items"][made["id"]]["result"]["superseded_by"]
            == (item["id"])
        )

    def test_capture_a_consolidation_failure_does_not_stop_the_run(self, live, monkeypatch):
        _old(live, 1)
        _old(live, 2)

        def fails(*args, **kwargs):
            raise APIError({"code": "57014", "message": "canceling statement due to timeout"})

        monkeypatch.setattr(capture.store, "capture_consolidate", fails)
        live.conversation("t1")
        live.answer(LEAD)
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert got["counts"]["consolidate_failed"] == 1 and got["counts"]["judged"] == 1

    def test_capture_off_leaves_every_item_untouched(self, live):
        _old(live, 1)
        _old(live, 2)
        live.fake.set_capture(live.labs, "off")
        before = json.dumps(live.fake.rows["crm_review_items"], sort_keys=True, default=str)
        got = live.tick()
        assert (got["outcome"], got["counts"]) == ("skipped", {"skipped": rules.CAPTURE_OFF})
        assert json.dumps(live.fake.rows["crm_review_items"], sort_keys=True, default=str) == (
            before
        )

    def test_capture_consolidates_in_a_dry_run_before_the_other_gates(self, cap):
        _old(cap, 1)
        _old(cap, 2)
        cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] = NOW.isoformat()
        got = cap.tick()
        assert got["outcome"] == "skipped"
        assert got["counts"]["consolidated"] == 1
        assert len(_open(cap)) == 1

    def test_capture_an_old_person_name_item_and_new_mail_end_in_one_item(self, live):
        # PD-40: the old item's company name is the person's (no Claude
        # name), so it keys by address, as the new conversation does.
        old = _old(
            live,
            1,
            company_name="Priya Shah",
            domain=None,
            first_name="Priya",
            last_name="Shah",
            email="priya.shah@gmail.com",
        )
        live.conversation("t1", sender="Priya Shah <priya.shah@gmail.com>")
        live.answer(
            {
                **LEAD,
                "company_name": None,
                "person_email": "priya.shah@gmail.com",
                "website_choice": None,
            }
        )
        got = live.tick()
        assert got["outcome"] == "ok", got
        [item] = _open(live)
        assert item["id"] == old["id"]
        assert item["source_key"] == "capture:email:priya.shah@gmail.com"
        assert sorted(t["thread_id"] for t in item["source_link"]["threads"]) == ["h1", "t1"]

    def test_capture_an_old_item_keys_from_its_candidates_finding(self, live):
        old = _old(live, 1, company_name="Asha", domain=None, email="asha@gmail.com")
        live.fake.mark_candidates(
            live.labs["id"],
            live.mailbox["id"],
            [{"thread_id": "h1", "message_id": "h1-m1", "sent_at": NOW.isoformat()}],
        )
        row = next(
            c for c in live.fake.rows["crm_capture_candidates"].values() if c["thread_id"] == "h1"
        )
        row.update(
            status="judged",
            outcome="new_lead",
            confidence="high",
            judged_message_id="h1-m1",
            judged_at=NOW.isoformat(),
            finding={**FOUND, "domain": None, "company_name": "HearSight"},
        )
        capture._consolidate(live.labs)
        assert live.fake.rows["crm_review_items"][old["id"]]["source_key"] == (
            "capture:name:hearsight"
        )


# ---------------------------------------------------------------------------
# Spec 010 R1 I3 — course enquiries to the Academy (tasks T446)
# ---------------------------------------------------------------------------

COURSE = {
    **LEAD,
    "company_name": "City College",
    "person_name": "Asha Kumar",
    "person_email": "asha@college.example.edu",
    "person_title": None,
    "reason": "Asked about the AI course for final-year students; you replied.",
    "business": "academy",
}
STUDENT = "Asha Kumar <asha@college.example.edu>"


def _in(cap, workspace) -> list[dict]:
    return [i for i in _items(cap) if i["workspace_id"] == workspace["id"]]


def _open_in(cap, workspace) -> list[dict]:
    return [i for i in _in(cap, workspace) if i["status"] == "open"]


def _academy_membership(cap, user_id):
    return next(
        m
        for m in cap.fake.rows["crm_memberships"].values()
        if m["workspace_id"] == cap.academy["id"] and m["user_id"] == user_id
    )


def _academy_setting(cap):
    return next(
        r
        for r in cap.fake.rows["crm_workspace_settings"].values()
        if r["workspace_id"] == cap.academy["id"] and r["key"] == "capture_routing"
    )


class TestAcademy:
    """FR-CAP-ACAD-02 to -04, -07 to -10, FR-CAP-RPT-09; plan R1.4, PD-30,
    PD-42; spec §14.5 I3."""

    def test_capture_routes_a_course_enquiry_to_the_academy(self, live):
        candidate = live.conversation("c1", sender=STUDENT)
        live.answer(COURSE)
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert got["counts"]["academy_routed"] == 1 and got["counts"]["items"] == 0
        assert got["counts"]["academy_unrouted"] == 0
        # FR-CAP-ACAD-02: nothing in Labs.
        assert _in(live, live.labs) == []
        [item] = _in(live, live.academy)
        assert item["type"] == "new_lead" and item["status"] == "open"
        # The mailbox owner is an Academy Admin: his item (FR-CAP-ACAD-03).
        assert item["owner_id"] == OWNER["id"]
        assert item["source_key"] == "capture:domain:college.example.edu"
        assert item["source_link"].keys() == {"threads"}
        [entry] = item["source_link"]["threads"]
        assert entry["ref"] == f"gmail:{BOX}:c1" and "mailbox_id" not in entry
        assert entry["origin"] == "academy_routing"
        assert item["suggestion"]["evidence"]["mailbox_email"] == BOX
        # No Labs id in any column (FR-CAP-ACAD-04).
        held = json.dumps(item, default=str)
        for labs_id in (live.labs["id"], live.mailbox["id"], candidate["id"], live.run["id"]):
            assert labs_id not in held
        row = live.candidate("c1")
        assert row["finding"]["business"] == "academy" and row["finding"]["routed"] is True
        assert row["finding"]["duplicate_company_id"] is None
        assert row["classifier_version"] == "010.2"

    def test_capture_the_academy_s_admins_own_it_when_the_owner_is_not_a_member(self, live):
        live.fake.rows["crm_memberships"].pop(_academy_membership(live, OWNER["id"])["id"])
        _academy_membership(live, MEMBER["id"])["role"] = "admin"
        live.conversation("c1", sender=STUDENT)
        live.answer(COURSE)
        live.tick()
        [item] = _in(live, live.academy)
        assert item["owner_id"] is None

    def test_capture_an_earlier_labs_item_lets_the_conversation_go(self, live):
        live.conversation("c1", sender=STUDENT)
        live.answer({**COURSE, "business": "labs"}, COURSE)
        live.tick()
        [labs_item] = _open_in(live, live.labs)
        later = NOW - timedelta(hours=1)
        live.gmail.message("c1", "c1-m2", sender=STUDENT, to=BOX, at=later, text="And dates?")
        live.mark("c1", "c1-m2", later)
        got = live.tick()
        assert got["counts"]["released"] == 1 and got["counts"]["academy_routed"] == 1
        assert live.fake.rows["crm_review_items"][labs_item["id"]]["status"] == "expired"
        assert _open_in(live, live.labs) == []
        assert len(_open_in(live, live.academy)) == 1

    @pytest.mark.parametrize("gap", ["no_workspace", "no_admin", "inactive_admin", "switch_off"])
    def test_capture_without_an_academy_admin_suggests_nothing(self, live, gap):
        if gap == "no_workspace":
            live.fake.rows["crm_workspaces"].pop(live.academy["id"])
        elif gap == "no_admin":
            _academy_membership(live, OWNER["id"])["role"] = "member"
        elif gap == "inactive_admin":
            # The Academy's only Admin has an inactive account.
            _academy_membership(live, OWNER["id"])["role"] = "member"
            _academy_membership(live, MEMBER["id"])["role"] = "admin"
            live.fake.profiles[MEMBER["id"]]["is_active"] = False
        else:
            _academy_setting(live)["value"] = {"accept_routed": False}
        live.conversation("c1", sender=STUDENT)
        live.answer(COURSE)
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert got["counts"]["academy_unrouted"] == 1 and got["counts"]["academy_routed"] == 0
        assert _items(live) == []
        assert live.candidate("c1")["finding"]["routed"] is False

    def test_capture_academy_ignore_list_stops_routing(self, live, caplog):
        live.fake.add(
            "crm_capture_ignores",
            workspace_id=live.academy["id"],
            kind="domain",
            value="college.example.edu",
            from_item_id=None,
            created_by=OWNER["id"],
            created_at=NOW.isoformat(),
        )
        live.conversation("c1", sender=STUDENT)
        live.answer(COURSE)
        with caplog.at_level("INFO", logger="nldw-task-master"):
            got = live.tick()
        # Handed over, whatever the Academy then did (PD-42).
        assert got["counts"]["academy_routed"] == 1
        assert _items(live) == []
        assert live.candidate("c1")["finding"]["routed"] is True
        skipped = [r.getMessage() for r in caplog.records if "capture.academy_skipped" in r.message]
        assert skipped and '"reason": "ignored"' in skipped[0]
        assert "college.example.edu" not in skipped[0]

    def test_capture_rejudged_to_labs_leaves_the_academy_item(self, live):
        live.conversation("c1", sender=STUDENT)
        live.answer(COURSE, {**COURSE, "business": "labs"})
        live.tick()
        [academy_item] = _open_in(live, live.academy)
        # A later message reopens the candidate through the real mark, which
        # clears the earlier finding (071 `crm_capture_mark`).
        later = NOW - timedelta(hours=1)
        live.gmail.message("c1", "c1-m2", sender=STUDENT, to=BOX, at=later, text="Pilot?")
        live.mark("c1", "c1-m2", later)
        assert live.candidate("c1")["status"] == "pending"
        assert live.candidate("c1")["finding"] is None
        got = live.tick()
        assert got["counts"]["items"] == 1
        assert live.fake.rows["crm_review_items"][academy_item["id"]]["status"] == "expired"
        assert len(_open_in(live, live.labs)) == 1
        assert _open_in(live, live.academy) == []

    def test_capture_the_usage_row_is_labs(self, live):
        live.conversation("c1", sender=STUDENT)
        live.answer(COURSE)
        live.tick()
        [usage] = live.fake.rows["crm_ai_usage"].values()
        assert usage["workspace_id"] == live.labs["id"]

    def test_capture_no_routing_outside_the_tva_linked_workspace(self, live):
        # PD-29: only the TVA-linked workspace routes; elsewhere it is Labs.
        live.conversation("c1", sender=STUDENT)
        live.labs["tva_linked"] = False
        live.fake.rows["crm_workspaces"][live.labs["id"]]["tva_linked"] = False
        live.answer(COURSE)
        got = live.tick()
        assert got["counts"]["academy_routed"] == 0 and got["counts"]["items"] == 1
        assert live.candidate("c1")["finding"]["business"] == "labs"
        assert _in(live, live.academy) == []

    @pytest.mark.parametrize("target", [True, False])
    def test_capture_a_dry_run_recall_row_for_a_course_enquiry(self, cap, target):
        # Plan R1.3 step 7: `found` with an Academy target, else
        # `academy_unrouted` (counted inside found, FR-CAP-RPT-07).
        if not target:
            _academy_setting(cap)["value"] = {"accept_routed": False}
        cap.gmail.message(
            "r-course",
            "r-course-m1",
            sender=STUDENT,
            to=BOX,
            at=NOW - timedelta(days=30),
            labels=("INBOX", "Label_1", "CATEGORY_PERSONAL"),
            text="Course dates?",
        )
        cap.answer(COURSE)
        got = cap.tick()
        assert got["outcome"] == "ok", got
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        [row] = run["recall"]
        assert row["fate"] == ("found" if target else "academy_unrouted")
        # A dry run routes nothing.
        assert _items(cap) == []

    def test_capture_the_report_counts_course_enquiries(self, cap):
        cap.conversation("c1", sender=STUDENT)
        cap.conversation("t2", sender="ravi@robots.io")
        cap.answer(COURSE, LEAD)
        cap.tick()
        out = capture.overview(_Ctx(cap))["dry_run"]
        assert out["counts"]["academy"] == 1 and out["counts"]["academy_unrouted"] == 0
        academy = next(f for f in out["findings"] if f["business"] == "academy")
        assert academy["routed"] is None and academy["duplicate_company"] is None
        _academy_setting(cap)["value"] = {"accept_routed": False}
        out = capture.overview(_Ctx(cap))["dry_run"]
        assert out["counts"]["academy_unrouted"] == 1

    def _finish(self, cap):
        cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]["finished_at"] = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()

    def _apply(self, cap):
        r = cap.http.post(
            f"/api/v1/crm/capture/dry-runs/{cap.run['id']}/apply?workspace=labs",
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_capture_apply_routes(self, cap):
        cap.conversation("c1", sender=STUDENT)
        cap.answer(COURSE)
        cap.tick()
        self._finish(cap)
        cap.answer()  # no script: a Claude call fails the test
        assert self._apply(cap)["items_created"] == 0
        assert _in(cap, cap.labs) == []
        [item] = _in(cap, cap.academy)
        assert item["source_link"]["threads"][0]["ref"] == f"gmail:{BOX}:c1"
        # FR-CAP-ACAD-02: the applied decision records the hand-over, as a
        # live judgement does.
        row = cap.candidate("c1")
        assert row["dry_run"] is False
        assert row["finding"]["routed"] is True and row["finding"]["duplicate_company_id"] is None
        # FR-CAP-RPT-09: the applied run still counts it.
        out = capture.overview(_Ctx(cap))["dry_run"]
        assert out["applied_at"]
        assert out["counts"]["academy"] == 1 and out["counts"]["academy_unrouted"] == 0

    def test_capture_apply_checks_the_target_again(self, cap):
        cap.conversation("c1", sender=STUDENT)
        cap.answer(COURSE)
        cap.tick()
        self._finish(cap)
        _academy_setting(cap)["value"] = {"accept_routed": False}
        self._apply(cap)
        assert _items(cap) == []
        assert cap.candidate("c1")["finding"]["routed"] is False
        out = capture.overview(_Ctx(cap))["dry_run"]
        assert out["counts"]["academy"] == 1 and out["counts"]["academy_unrouted"] == 1
        # Turning the switch on again does not rewrite what Apply recorded.
        _academy_setting(cap)["value"] = {"accept_routed": True}
        out = capture.overview(_Ctx(cap))["dry_run"]
        assert out["counts"]["academy"] == 1 and out["counts"]["academy_unrouted"] == 1

    def test_capture_apply_keeps_the_run_s_other_counts(self, cap):
        cap.conversation("c1", sender=STUDENT)
        cap.conversation("t2", sender="ravi@robots.io")
        cap.answer(COURSE, LEAD)
        cap.tick()
        before = capture.overview(_Ctx(cap))["dry_run"]["counts"]
        self._finish(cap)
        self._apply(cap)
        after = capture.overview(_Ctx(cap))["dry_run"]["counts"]
        assert after == before
        assert after["academy"] == 1 and after["judged"] == 2

    def test_capture_an_applied_run_from_before_074_counts_no_course_enquiry(self, cap):
        # Contracts §7.1: present for every run, 0 when none.
        cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]].update(
            finished_at="2026-10-19T00:00:00+00:00",
            applied_at="2026-10-19T01:00:00+00:00",
            counts={"considered": 0},
        )
        counts = capture.overview(_Ctx(cap))["dry_run"]["counts"]
        assert counts == {"considered": 0, "academy": 0, "academy_unrouted": 0}

    def test_capture_apply_routed_is_refused_for_a_run_that_cannot_be_applied(self, cap):
        from app.services.crm import CrmRefused, store

        actor = {
            "user_id": OWNER["id"],
            "kind": "user",
            "channel": "web",
            "workspace_id": cap.labs["id"],
        }
        with pytest.raises(CrmRefused) as refused:
            store.capture_apply_routed(
                actor, cap.run["id"], {}, {"academy": 0, "academy_unrouted": 0}
            )
        assert refused.value.status == 422

    @pytest.mark.parametrize("gap", ["switch_off", "no_admin"])
    def test_capture_rejudged_to_labs_leaves_the_academy_item_without_a_target(self, live, gap):
        # FR-CAP-ACAD-09: letting go depends only on the Academy existing,
        # not on it still taking enquiries (FR-CAP-ACAD-03 is for routing).
        live.conversation("c1", sender=STUDENT)
        live.answer(COURSE, {**COURSE, "business": "labs"})
        live.tick()
        [academy_item] = _open_in(live, live.academy)
        if gap == "switch_off":
            _academy_setting(live)["value"] = {"accept_routed": False}
        else:
            _academy_membership(live, OWNER["id"])["role"] = "member"
        later = NOW - timedelta(hours=1)
        live.gmail.message("c1", "c1-m2", sender=STUDENT, to=BOX, at=later, text="Pilot?")
        live.mark("c1", "c1-m2", later)
        got = live.tick()
        assert got["counts"]["items"] == 1
        assert live.fake.rows["crm_review_items"][academy_item["id"]]["status"] == "expired"
        assert len(_open_in(live, live.labs)) == 1

    def test_capture_live_the_labs_ignore_list_applies_before_judging(self, live):
        # FR-CAP-DIS-03 over §14.5 I3 (spec amended): live, the Labs ignore
        # list is read before any text, so a course enquiry from a Labs-
        # ignored domain is never judged nor routed; only an applied dry run
        # reads the Academy's list alone.
        _ignore(live, "domain", "college.example.edu")
        live.conversation("c1", sender=STUDENT)
        live.answer()  # no script: a Claude call fails the test
        got = live.tick()
        assert got["outcome"] == "ok", got
        assert got["counts"]["ignored"] == 1 and got["counts"].get("academy_routed", 0) == 0
        assert live.candidate("c1")["status"] == "ignored"
        assert _items(live) == []

    def test_capture_apply_reads_the_academy_ignore_list_not_labs(self, cap):
        cap.conversation("c1", sender=STUDENT)
        cap.answer(COURSE)
        cap.tick()
        self._finish(cap)
        _ignore(cap, "address", "asha@college.example.edu")
        self._apply(cap)
        assert len(_in(cap, cap.academy)) == 1
