"""The AI fields job — spec 009 FR-CRMAI-FIELD-03 to -05, FR-CRMAI-JOB-01,
FR-CRMAI-FLAG-03, FR-CRMAI-LLM-05; Q-38; spec §15 "AI fields":

- one tick computes every enabled field of every live company and deal, with
  its provenance; a second tick with nothing changed makes no call;
- What is blocking is computed only while Deal outlook is At risk or
  Stalled, and cleared without a call once it is not;
- a record's fields are recomputed at most once an hour, and what changes
  inside the hour gets one trailing recompute at its end (Q-38);
- the prompt carries no amount (FR-CRMAI-FIELD-03);
- a value never changes a stage, probability, flag or colour, and writes no
  audit row of the record (FR-CRMAI-FIELD-05);
- a stored summary marks the linked records' values, and the next tick
  recomputes them (FR-CRMAI-FIELD-04);
- phase 3 switched off skips the run; an answer that does not fit its field
  is counted and the old value stays.

Every job runs on `tests/crm_fake.py` with a `FakeClaude` that answers per
field (`tests/crm_ai_fakes.py`): no network.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from crm_ai_fakes import INVALID, FakeClaude, SlowCalls
from crm_fake import MEMBER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from test_crm_summaries_job import ANSWER as SUMMARY_ANSWER
from test_crm_summaries_job import Mail

from app.config import settings as config
from app.services.crm import ai_fields, deals, jobs, notifications
from app.tasks import crm_ai

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
#: An answer per field, as the model gives it.
ANSWERS = {
    "ICP fit": {"value": "Factory Brain"},
    "Account brief": {"value": "Acme makes machine tools.\nA pilot is under discussion."},
    "Deal outlook": {"value": "At risk", "reason": "No reply since the proposal."},
    "What is blocking": {"value": "Waiting on the plant head's sign-off."},
}
_FIELD = re.compile(r"The field: (.+)\.\n")


class ByField(FakeClaude):
    """`claude.messages`, answering each call with the answer of the field
    its instruction names; a field with no answer fails the test."""

    def __init__(self, answers: dict[str, object]) -> None:
        super().__init__()
        self.answers = dict(answers)

    @property
    def fields(self) -> list[str]:
        return [_FIELD.search(c["system"]).group(1) for c in self.calls]

    def messages(self, *, model, system, user, schema, job, workspace):
        name = _FIELD.search(system).group(1)
        if name not in self.answers:
            raise AssertionError(f"ByField: no answer for {name}")
        answer = self.answers[name]
        self.script = [answer.pop(0) if isinstance(answer, list) else answer]
        return super().messages(
            model=model, system=system, user=user, schema=schema, job=job, workspace=workspace
        )


class Fields:
    """Labs, phase 3 on with a budget and its four seeded fields; Acme
    (Deepika's) with a deal at Discovery; Claude answering per field."""

    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs, self.academy = two_workspaces(fake)
        fake.add_deal_lists(self.labs)
        fake.set_ai(self.labs, budget_usd=10, phases={"3": True})
        fake.seed_ai_fields(self.labs)
        fake.seed_ai_fields(self.academy)
        fake.add(
            "crm_fx_rates",
            workspace_id=self.labs["id"],
            currency="USD",
            month="2026-10-01",
            rate=84,
        )
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        monkeypatch.setattr(jobs, "_now", lambda: NOW)
        monkeypatch.setattr(ai_fields, "_now", lambda: NOW)
        monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 18))
        monkeypatch.setattr(notifications, "deliver", lambda message: ["email"])
        self.http = crm_client(monkeypatch, fake=fake)
        self.acme = self.post(
            "companies", {"name": "Acme", "owner_id": MEMBER["id"], "city": "Coimbatore"}
        )
        self.patch("companies", self.acme, {"lead_stage": "qualified"})
        self.deal = self.post(
            "opportunities",
            {
                "name": "Acme pilot",
                "company_id": self.acme["id"],
                "owner_id": MEMBER["id"],
                "next_step": "Send the quote of ₹5,00,000",
                "next_step_on": "2026-10-21",
                "amount": "1234567",
                "currency": "USD",
            },
        )
        self.answer(ANSWERS)

    def post(self, plural, body) -> dict:
        r = self.http.post(f"{API}/{plural}", params=LABS, json=body, headers=as_(OWNER["id"]))
        assert r.status_code == 201, r.text
        return r.json()

    def patch(self, plural, record, body) -> dict:
        r = self.http.patch(
            f"{API}/{plural}/{record['id']}", params=LABS, json=body, headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        return r.json()

    def answer(self, answers: dict[str, object]) -> ByField:
        self.claude = ByField(answers).install(self.monkeypatch)
        return self.claude

    def tick(self, at: datetime = NOW) -> dict:
        return crm_ai._ai_fields(at)["runs"]["labs"]

    def value(self, record: dict, key: str) -> dict | None:
        field = self.fake.ai_field(self.labs, key)
        return next(
            (
                v
                for v in self.fake.rows["crm_ai_field_values"].values()
                if v["field_id"] == field["id"] and v["record_id"] == record["id"]
            ),
            None,
        )


@pytest.fixture
def fields(monkeypatch) -> Fields:
    return Fields(monkeypatch)


class TestOneTick:
    """FR-CRMAI-FIELD-03, -04, FR-CRMAI-JOB-01."""

    def test_ai_fields_one_tick_computes_each_field_with_its_provenance(self, fields):
        run = fields.tick()
        assert run == {
            "outcome": "ok",
            "counts": {
                "records": 2,
                "computed": 4,
                "cleared": 0,
                "waiting": 0,
                "failed_items": 0,
            },
        }
        assert sorted(fields.claude.fields) == sorted(ANSWERS)
        assert {c["job"] for c in fields.claude.calls} == {"ai_fields"}
        outlook = fields.value(fields.deal, "deal_outlook")
        assert (outlook["value_text"], outlook["reason"]) == (
            "At risk",
            "No reply since the proposal.",
        )
        assert (outlook["model"], outlook["generated_at"]) == ("claude-sonnet-5-5", NOW.isoformat())
        assert outlook["next_allowed_at"] == (NOW + timedelta(hours=1)).isoformat()
        assert outlook["dirty_at"] is None
        assert fields.value(fields.deal, "what_is_blocking")["value_text"] == (
            "Waiting on the plant head's sign-off."
        )
        assert fields.value(fields.acme, "icp_fit")["value_text"] == "Factory Brain"
        assert (
            fields.value(fields.acme, "account_brief")["value_text"]
            == (ANSWERS["Account brief"]["value"])
        )

    def test_ai_fields_twice_no_second_call_nothing_changes(self, fields):
        fields.tick()
        before = {k: dict(v) for k, v in fields.fake.rows["crm_ai_field_values"].items()}
        calls = len(fields.claude.calls)
        run = fields.tick(NOW + timedelta(hours=3))
        assert run["counts"]["computed"] == 0
        assert len(fields.claude.calls) == calls
        assert {k: dict(v) for k, v in fields.fake.rows["crm_ai_field_values"].items()} == before

    def test_ai_fields_switched_off_is_skipped(self, fields):
        fields.fake.set_ai(fields.labs, budget_usd=10, phases={"3": False})
        assert fields.tick() == {"outcome": "skipped", "counts": {"skipped": "Switched off"}}
        assert fields.claude.calls == []
        assert fields.fake.rows["crm_ai_field_values"] == {}

    def test_ai_fields_a_disabled_field_is_not_computed(self, fields):
        fields.fake.ai_field(fields.labs, "icp_fit")["enabled"] = False
        fields.tick()
        assert "ICP fit" not in fields.claude.fields
        assert fields.value(fields.acme, "icp_fit") is None

    def test_ai_fields_a_record_in_the_trash_is_not_computed(self, fields):
        fields.fake.rows["crm_companies"][fields.acme["id"]]["deleted_at"] = NOW.isoformat()
        fields.tick()
        assert {"ICP fit", "Account brief"}.isdisjoint(fields.claude.fields)

    def test_ai_fields_an_answer_that_does_not_fit_is_counted_and_the_value_stays(self, fields):
        fields.tick()
        before = dict(fields.value(fields.acme, "account_brief"))
        fields.patch("companies", fields.acme, {"city": "Chennai"})
        fields.answer({**ANSWERS, "Account brief": {"value": "1\n2\n3\n4"}, "ICP fit": INVALID})
        run = fields.tick(NOW + timedelta(hours=2))
        assert run["counts"]["failed_items"] == 2
        assert fields.value(fields.acme, "account_brief") == before
        assert fields.value(fields.acme, "icp_fit")["value_text"] == "Factory Brain"


class TestWhatIsBlocking:
    """The FR-CRMAI-FIELD table: only when Deal outlook is At risk or Stalled."""

    def test_ai_fields_on_track_makes_no_blocking_call(self, fields):
        fields.answer({**ANSWERS, "Deal outlook": {"value": "On track", "reason": "Replied."}})
        fields.tick()
        assert "What is blocking" not in fields.claude.fields
        assert fields.value(fields.deal, "what_is_blocking") is None

    def test_ai_fields_blocking_is_cleared_once_the_deal_is_on_track(self, fields):
        fields.tick()
        fields.patch("opportunities", fields.deal, {"next_step": "Pilot kick-off"})
        fields.answer({"Deal outlook": {"value": "On track", "reason": "Pilot agreed."}})
        run = fields.tick(NOW + timedelta(hours=2))
        assert fields.claude.fields == ["Deal outlook"]
        assert run["counts"]["cleared"] == 1
        blocking = fields.value(fields.deal, "what_is_blocking")
        assert (blocking["value_text"], blocking["model"]) == (None, None)
        r = fields.http.get(
            f"{API}/opportunities/{fields.deal['id']}", params=LABS, headers=as_(OWNER["id"])
        )
        shown = {f["key"]: f for f in r.json()["ai_fields"]}
        assert (shown["what_is_blocking"]["value"], shown["what_is_blocking"]["ai"]) == (None, None)
        # And it stays cleared without another write.
        assert fields.tick(NOW + timedelta(hours=4))["counts"]["cleared"] == 0


class TestOnceAnHour:
    """FR-CRMAI-FIELD-04, Q-38."""

    def test_ai_fields_one_recompute_an_hour_plus_one_trailing(self, fields):
        fields.tick()
        calls = len(fields.claude.calls)
        fields.patch("opportunities", fields.deal, {"next_step": "Call the plant head"})
        run = fields.tick(NOW + timedelta(minutes=15))
        assert len(fields.claude.calls) == calls
        assert run["counts"]["waiting"] == 2
        assert (
            fields.value(fields.deal, "deal_outlook")["dirty_at"]
            == (NOW + timedelta(minutes=15)).isoformat()
        )
        fields.patch("opportunities", fields.deal, {"next_step": "Visit the plant"})
        fields.tick(NOW + timedelta(minutes=30))
        fields.tick(NOW + timedelta(minutes=45))
        assert len(fields.claude.calls) == calls
        # The end of the hour: one trailing recompute, of the deal only.
        fields.tick(NOW + timedelta(minutes=60))
        assert fields.claude.fields[calls:] == ["Deal outlook", "What is blocking"]
        outlook = fields.value(fields.deal, "deal_outlook")
        assert outlook["dirty_at"] is None
        assert outlook["next_allowed_at"] == (NOW + timedelta(hours=2)).isoformat()
        fields.tick(NOW + timedelta(minutes=75))
        assert len(fields.claude.calls) == calls + 2


class TestInputs:
    """FR-CRMAI-FIELD-03, FR-CRMAI-LLM-05 — no amounts are sent to the model."""

    def test_ai_fields_the_prompt_carries_no_amount(self, fields):
        fields.tick()
        assert fields.fake.rows["crm_opportunities"][fields.deal["id"]]["amount"] is not None
        for prompt in fields.claude.prompts:
            for figure in ("1234567", "1,234,567", "5,00,000", "₹"):
                assert figure not in prompt, figure
        [deal_prompt] = [
            p
            for p, f in zip(fields.claude.prompts, fields.claude.fields, strict=True)
            if f == "Deal outlook"
        ]
        assert "Next step: Send the quote of [amount]" in deal_prompt
        assert "Company: Acme" in deal_prompt

    def test_ai_fields_the_prompt_quotes_the_record_as_data(self, fields):
        fields.tick()
        assert all('<data source="record">' in c["user"] for c in fields.claude.calls)


class TestNeverFeedsAnything:
    """FR-CRMAI-FIELD-05 — a value never changes a stage, probability, flag
    or colour, and is stored in its own table."""

    def test_ai_fields_a_value_never_changes_a_stage_probability_flag_or_colour(self, fields):
        deal = dict(fields.fake.rows["crm_opportunities"][fields.deal["id"]])
        company = dict(fields.fake.rows["crm_companies"][fields.acme["id"]])
        audit = len(fields.fake.audit)
        fields.answer({**ANSWERS, "Deal outlook": {"value": "Stalled", "reason": "Silent."}})
        fields.tick()
        assert fields.fake.rows["crm_opportunities"][fields.deal["id"]] == deal
        assert fields.fake.rows["crm_companies"][fields.acme["id"]] == company
        for column in ("stage", "probability", "flag_level", "flags"):
            assert deal[column] == fields.fake.rows["crm_opportunities"][deal["id"]][column]
        assert fields.fake.audit[audit:] == []


class TestMarking:
    """FR-CRMAI-FIELD-04 — a stored summary marks the linked records' values."""

    def test_ai_fields_a_stored_summary_sets_dirty_at_and_the_next_tick_recomputes(
        self, monkeypatch
    ):
        mail = Mail(monkeypatch)
        mail.fake.set_ai(mail.labs, budget_usd=10, phases={"1": True, "3": True})
        mail.fake.seed_ai_fields(mail.labs)
        monkeypatch.setattr(ai_fields, "_now", lambda: NOW)
        claude = ByField(ANSWERS).install(monkeypatch)
        crm_ai._ai_fields(NOW)
        before = len(claude.calls)
        outlook = mail.fake.ai_field(mail.labs, "deal_outlook")

        def value():
            return next(
                v
                for v in mail.fake.rows["crm_ai_field_values"].values()
                if v["field_id"] == outlook["id"] and v["record_id"] == mail.pilot["id"]
            )

        assert value()["dirty_at"] is None
        mail.message("g1")
        FakeClaude(SUMMARY_ANSWER).install(monkeypatch)
        assert mail.tick()["counts"]["summarised"] == 1
        assert value()["dirty_at"] is not None
        claude = ByField(ANSWERS).install(monkeypatch)
        crm_ai._ai_fields(NOW + timedelta(hours=2))
        assert "Deal outlook" in claude.fields and "Account brief" in claude.fields
        assert value()["dirty_at"] is None
        assert before == 4

    def test_ai_fields_marking_never_fails_the_write_that_asked(self, fields, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("the database is gone")

        monkeypatch.setattr(ai_fields.store, "mark_ai_fields_dirty", broken)
        ai_fields.mark_dirty(
            fields.labs["id"], [{"record_type": "company", "record_id": fields.acme["id"]}], NOW
        )


class TestTheTaskTimeLimit:
    """FR-CRMAI-JOB-01 (amended in S5): no more calls once the run's share
    of the task's time is spent, and a budget stop keeps its counts."""

    def test_ai_fields_out_of_time_the_rest_wait_for_the_next_tick(self, fields):
        # Labs runs second, until the task's 400 s; each call takes 450 s.
        SlowCalls(450).install(fields.monkeypatch)
        run = fields.tick()
        assert run["outcome"] == "ok"
        assert run["counts"]["computed"] == 1
        assert len(fields.claude.calls) == 1
        fields.answer(ANSWERS)
        assert fields.tick()["counts"]["computed"] == 3

    def test_ai_fields_a_budget_stop_part_way_keeps_the_counts(self, fields):
        # The first call costs 18 USD, past the 1 USD budget.
        fields.fake.set_ai(fields.labs, budget_usd=1, phases={"3": True})
        claude = fields.answer(ANSWERS)
        claude.input_tokens = claude.output_tokens = 1_000_000
        run = fields.tick()
        assert run["outcome"] == "skipped"
        assert run["counts"] == {
            "records": 1,
            "computed": 1,
            "cleared": 0,
            "waiting": 0,
            "failed_items": 0,
            "skipped": "AI budget reached",
        }
