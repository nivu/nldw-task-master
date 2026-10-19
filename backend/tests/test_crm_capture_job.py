"""Email capture's discovery job — spec 010 FR-CAP-DIS-01, -02, -04 to -07,
-10, FR-CAP-RPT-01, -03, FR-CAP-CHG-05, NFR-CAP-01 to -04; plan §5.6, §5.7,
PD-11, PD-14, PD-17; spec §13 S2.

Every run is on `tests/crm_fake.py` with `FakeClaude` and `FakeGmailThread`
(`tests/crm_ai_fakes.py`): no network. A `FakeClaude()` with no script fails
the test on any call, so a candidate that must never reach Claude proves it
by the run passing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from crm_ai_fakes import FAIL, INVALID, TIMEOUT, CapturedCelery, FakeClaude, FakeGmailThread
from crm_fake import DEFAULTS, MEMBER, OWNER, FakeCrmStore, two_workspaces
from crm_fake import client as crm_client

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
    """NFR-CAP-02; spec §13 S2 line 2 — internal, automated and known
    conversations never reach Claude (the ignore list arrives in S3)."""

    def test_capture_keeps_filtered_candidates_from_claude(self, cap):
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
        got = cap.tick()  # FakeClaude() with no script: any call fails the test.
        assert got["outcome"] == "ok", got
        status = {
            t: cap.candidate(t)["status"]
            for t in ("internal", "news", "invite", "noreply", "known")
        }
        assert status == {
            "internal": "skipped",
            "news": "skipped",
            "invite": "skipped",
            "noreply": "skipped",
            "known": "known",
        }
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

    def test_capture_the_beat_runs_it_hourly_at_minute_seven(self):
        from app.worker import celery_app

        entry = celery_app.conf.beat_schedule["crm-ai-capture"]
        assert entry["task"] == "crm_ai.capture"
        assert entry["schedule"].minute == {7} and entry["schedule"].hour == set(range(24))
        assert celery_app.amqp.router.route({}, "crm_ai.capture")["queue"].name == "crm-ai"


class TestRecall:
    """FR-CAP-RPT-03, plan §5.7, PD-11; spec §13 S2 line 3."""

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
        # Automation 2 made this company from the labelled mail itself.
        self._labelled(cap, "r-auto", primary_sales, "priya@acme-robotics.com")
        self._company(cap, "Acme Robotics", "acme-robotics.com", NOW.isoformat())
        self._labelled(cap, "r-vendor", primary_sales, "sales@vendor.io")
        cap.answer(LEAD, VENDOR)
        got = cap.tick()
        assert got["outcome"] == "ok", got
        run = cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]]
        assert run["finished_at"] is not None
        fates = {r["thread_id"]: r["fate"] for r in run["recall"]}
        assert fates == {
            "r-out": "outside_scope",
            "r-bulk": "skipped_rules",
            "r-known": "filed_known",
            "r-auto": "found",
            "r-vendor": "judged_otherwise",
        }
        assert {r["mailbox_id"] for r in run["recall"]} == {cap.mailbox["id"]}
        assert all(
            set(r) == {"mailbox_id", "thread_id", "latest_at", "fate"} for r in run["recall"]
        )
        # Nothing is stored for a labelled thread but its fate.
        assert cap.fake.rows["crm_capture_candidates"] == {}
        assert len(cap.claude.calls) == 2

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
