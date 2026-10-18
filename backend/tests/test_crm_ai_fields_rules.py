"""AI fields' rules — spec 009 FR-CRMAI-FIELD-01 to -05, FR-CRMAI-LLM-05;
Q-12, Q-13, Q-38; data-model §3.6; `domain/crm/ai_fields.py`:

- the defaults: ICP fit in Labs only, Deal outlook not "Deal health";
- the inputs carry no amount: no amount field is read, and an amount typed
  into a text field is masked before the prompt;
- the hash: the same inputs give the same hash, a day passing does not
  change it, a new summary or an edited prompt does;
- one recompute an hour plus one trailing (Q-38);
- What is blocking only when Deal outlook is At risk or Stalled;
- an answer is checked against its field's type.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.crm import ai_fields as rules

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
DEAL = {
    "id": "d1",
    "name": "Acme platform",
    "stage": "proposal",
    "stage_entered_at": "2026-10-08T12:00:00+00:00",
    "next_step": "Send the quote of ₹5,00,000 by Friday",
    "next_step_on": "2026-10-20",
    "expected_close_on": "2026-12-01",
    "amount": "1234567",
    "amount_in_inr": "103703628",
    "currency": "USD",
    "fx_rate_used": "84",
    "won_fx_rate": None,
    "probability": 50,
    "deal_type_id": "opt-1",
}
LABELS = {"opt-1": "New business"}
SUMMARY = {
    "summary": "Priya asked for the pilot plan. We will send it on Monday.",
    "asks": ["The pilot plan"],
    "commitments": [{"text": "Send the pilot plan"}],
    "sentiment": "positive",
    "next_step": "Send the pilot plan",
    "next_step_on": "2026-10-20",
}


def _field(key: str) -> dict:
    found = next(f for f in rules.DEFAULT_FIELDS if f["key"] == key)
    return {**found, "id": f"f-{key}", "enabled": True}


def _facts(**more) -> dict:
    return rules.facts("opportunity", DEAL, labels=LABELS, company_name="Acme", **more)


def _prompt(facts: dict, now: datetime = NOW) -> str:
    return "\n".join(f"{label}\n{text}" for label, text in rules.prompt_parts(facts, now))


class TestDefaults:
    """The FR-CRMAI-FIELD table; Q-12, Q-13."""

    def test_ai_fields_icp_fit_is_seeded_in_labs_only(self):
        assert [f["key"] for f in rules.defaults_for("labs")] == [
            "icp_fit",
            "account_brief",
            "deal_outlook",
            "what_is_blocking",
        ]
        assert [f["key"] for f in rules.defaults_for("academy")] == [
            "account_brief",
            "deal_outlook",
            "what_is_blocking",
        ]

    def test_ai_fields_deal_outlook_is_not_called_deal_health(self):
        names = {f["name"] for f in rules.DEFAULT_FIELDS}
        assert "Deal outlook" in names and not any("health" in n.lower() for n in names)
        outlook = _field("deal_outlook")
        assert outlook["options"] == ["On track", "At risk", "Stalled"]
        assert outlook["with_reason"] is True

    def test_ai_fields_the_defaults_fit_their_own_rules(self):
        for f in rules.DEFAULT_FIELDS:
            assert rules.options_refusal(f["output_type"], f["options"]) is None, f["key"]
            assert rules.key_for(f["name"], set()) == f["key"]


class TestDefinitions:
    """FR-CRMAI-FIELD-01."""

    @pytest.mark.parametrize("options", [[], ["Only"], [f"o{i}" for i in range(13)]])
    def test_ai_fields_a_choice_field_needs_two_to_twelve_options(self, options):
        assert rules.options_refusal("single_select", options) == (
            "A choice field needs 2 to 12 options."
        )

    @pytest.mark.parametrize("output_type", ["text", "number"])
    def test_ai_fields_only_a_choice_field_has_options(self, output_type):
        assert rules.options_refusal(output_type, []) is None
        assert rules.options_refusal(output_type, ["A", "B"]) == "Only a choice field has options."

    def test_ai_fields_options_are_trimmed_and_empty_ones_dropped(self):
        assert rules.clean_options([" Hot ", "", "  ", "Cold"]) == ["Hot", "Cold"]

    @pytest.mark.parametrize(
        ("name", "taken", "key"),
        [
            ("Renewal risk", set(), "renewal_risk"),
            ("Renewal risk!", {"renewal_risk"}, "renewal_risk_2"),
            ("2027 plan", set(), "field_2027_plan"),
            ("Ü", set(), "field"),
        ],
    )
    def test_ai_fields_a_key_from_the_name(self, name, taken, key):
        made = rules.key_for(name, taken)
        assert made == key
        assert len(made) <= 41 and made[0].isalpha()


class TestInputsCarryNoAmount:
    """FR-CRMAI-FIELD-03, FR-CRMAI-LLM-05."""

    def test_ai_fields_no_amount_field_is_read(self):
        facts = _facts()
        prompt = _prompt(facts)
        for value in ("1234567", "103703628", "USD", "84", "50"):
            assert value not in prompt, value
        assert set(facts["fields"]) == {
            "Company",
            "Name",
            "Stage",
            "Deal type",
            "Next step",
            "Next step on",
            "Expected close on",
        }

    def test_ai_fields_an_amount_typed_into_text_is_masked(self):
        prompt = _prompt(_facts())
        assert "5,00,000" not in prompt and "₹" not in prompt
        assert "Next step: Send the quote of [amount] by Friday" in prompt

    def test_ai_fields_summaries_notes_and_enrichment_are_masked_too(self):
        company = {"id": "c1", "name": "Acme", "description": "Turnover of $2,000,000"}
        facts = rules.facts(
            "company",
            company,
            labels={},
            summaries=[{**SUMMARY, "summary": "They budgeted INR 4.5 lakh. We agreed."}],
            notes=[{"occurred_on": "2026-10-17", "body": "Price agreed at USD 1200."}],
            enrichment={"values": {"description": "A $5M tools maker.", "products": ["Lathes"]}},
        )
        prompt = _prompt(facts)
        for figure in ("2,000,000", "4.5 lakh", "1200", "$5M"):
            assert figure not in prompt, figure
        assert "Products: Lathes" in prompt

    def test_ai_fields_the_record_its_history_and_its_time_in_stage(self):
        prompt = _prompt(
            _facts(summaries=[SUMMARY], notes=[{"occurred_on": "2026-10-17", "body": "Met."}])
        )
        assert "Company: Acme" in prompt
        assert "Days in this stage: 10" in prompt
        assert "Priya asked for the pilot plan." in prompt
        assert "Promised: Send the pilot plan" in prompt
        assert "Met." in prompt

    def test_ai_fields_at_most_five_summaries_and_notes(self):
        facts = _facts(
            summaries=[{**SUMMARY, "summary": f"S{i}."} for i in range(8)],
            notes=[{"occurred_on": None, "body": f"N{i}"} for i in range(8)],
        )
        assert len(facts["summaries"]) == 5 and len(facts["meeting_notes"]) == 5


class TestTheHash:
    """FR-CRMAI-FIELD-04 — recompute only when the inputs changed."""

    def test_ai_fields_the_same_inputs_give_the_same_hash(self):
        field = _field("deal_outlook")
        assert rules.inputs_hash(field, _facts()) == rules.inputs_hash(field, _facts())
        assert rules.inputs_hash(field, _facts()).startswith("sha256:")

    def test_ai_fields_a_day_passing_changes_nothing(self):
        field = _field("deal_outlook")
        facts = _facts()
        assert _prompt(facts) != _prompt(facts, NOW + timedelta(days=1))
        assert rules.inputs_hash(field, facts) == rules.inputs_hash(field, _facts())

    def test_ai_fields_a_new_summary_changes_it(self):
        field = _field("deal_outlook")
        assert rules.inputs_hash(field, _facts()) != rules.inputs_hash(
            field, _facts(summaries=[SUMMARY])
        )

    def test_ai_fields_an_edited_prompt_changes_it(self):
        field = _field("deal_outlook")
        edited = {**field, "prompt": "Is this deal going well?"}
        assert rules.inputs_hash(field, _facts()) != rules.inputs_hash(edited, _facts())

    def test_ai_fields_the_value_it_depends_on_changes_it(self):
        field = _field("what_is_blocking")
        risk = {"name": "Deal outlook", "value": "At risk", "reason": "No reply"}
        stalled = {**risk, "value": "Stalled"}
        assert rules.inputs_hash(field, _facts(), risk) != rules.inputs_hash(
            field, _facts(), stalled
        )


class TestOnceAnHourPlusOneTrailing:
    """FR-CRMAI-FIELD-04, Q-38."""

    def _row(self, hash_: str, at: datetime, dirty: datetime | None = None) -> dict:
        return {
            "inputs_hash": hash_,
            "generated_at": at.isoformat(),
            "next_allowed_at": (at + rules.CAP).isoformat(),
            "dirty_at": dirty.isoformat() if dirty else None,
        }

    def test_ai_fields_a_missing_value_is_computed(self):
        assert rules.decide(None, "h1", NOW) == rules.COMPUTE

    def test_ai_fields_unchanged_inputs_are_skipped(self):
        assert rules.decide(self._row("h1", NOW - timedelta(days=2)), "h1", NOW) == rules.SKIP

    def test_ai_fields_one_recompute_an_hour_plus_one_trailing(self):
        # Computed at noon; the inputs change at 12:10 and again at 12:40.
        row = self._row("h1", NOW)
        computed = []
        for minutes, new_hash in ((10, "h2"), (25, "h2"), (40, "h3"), (55, "h3"), (60, "h3")):
            at = NOW + timedelta(minutes=minutes)
            decision = rules.decide(row, new_hash, at)
            if decision == rules.WAIT and not row["dirty_at"]:
                row = {**row, "dirty_at": at.isoformat()}
            if decision == rules.COMPUTE:
                computed.append(minutes)
                row = self._row(new_hash, at)
        assert computed == [60]
        # The next hour: nothing changed since, nothing is computed.
        assert rules.decide(row, "h3", NOW + timedelta(minutes=135)) == rules.SKIP

    def test_ai_fields_a_marked_value_is_recomputed_once_its_hour_is_over(self):
        row = self._row("h1", NOW - timedelta(minutes=30), dirty=NOW)
        assert rules.decide(row, "h1", NOW) == rules.WAIT
        assert rules.decide(row, "h1", NOW + timedelta(minutes=30)) == rules.COMPUTE


class TestWhatIsBlocking:
    """The FR-CRMAI-FIELD table: only when Deal outlook is At risk or Stalled."""

    def _fields(self, *keys: str) -> dict:
        return {k: _field(k) for k in keys}

    @pytest.mark.parametrize(
        ("outlook", "applies"),
        [
            ("At risk", True),
            ("Stalled", True),
            ("On track", False),
            (None, False),
        ],
    )
    def test_ai_fields_what_is_blocking_only_when_at_risk_or_stalled(self, outlook, applies):
        by_key = self._fields("deal_outlook", "what_is_blocking")
        values = {"deal_outlook": {"value_text": outlook, "reason": "No reply in 3 weeks"}}
        found, depends = rules.dependency(by_key["what_is_blocking"], by_key, values)
        assert found is applies
        assert (depends or {}).get("value") == (outlook if applies else None)

    def test_ai_fields_not_while_deal_outlook_is_disabled(self):
        by_key = self._fields("what_is_blocking")
        values = {"deal_outlook": {"value_text": "At risk"}}
        assert rules.dependency(by_key["what_is_blocking"], by_key, values) == (False, None)

    def test_ai_fields_a_field_without_a_dependency_always_applies(self):
        by_key = self._fields("deal_outlook")
        assert rules.dependency(by_key["deal_outlook"], by_key, {}) == (True, None)

    def test_ai_fields_a_dependent_field_comes_after_the_one_it_reads(self):
        fields = [_field("what_is_blocking"), _field("deal_outlook")]
        assert [f["key"] for f in rules.ordered(fields)] == ["deal_outlook", "what_is_blocking"]

    def test_ai_fields_the_prompt_names_the_outlook(self):
        depends = {"name": "Deal outlook", "value": "Stalled", "reason": "No reply"}
        parts = dict(rules.prompt_parts(_facts(), NOW, depends))
        assert parts["depends on"] == "Deal outlook: Stalled (No reply)"


class TestTheAnswerFitsItsField:
    """FR-CRMAI-FIELD-01 — output validation per type."""

    def test_ai_fields_a_choice_must_be_one_of_the_options(self):
        icp = _field("icp_fit")
        assert rules.stored(icp, {"value": "Academy"}) == {
            "value_text": "Academy",
            "value_number": None,
            "reason": None,
        }
        assert rules.stored(icp, {"value": "Something else"}) is None
        assert rules.stored(icp, {"value": None}) is None

    def test_ai_fields_deal_outlook_needs_its_one_line_reason(self):
        outlook = _field("deal_outlook")
        assert rules.stored(outlook, {"value": "At risk", "reason": " No reply. "})["reason"] == (
            "No reply."
        )
        assert rules.stored(outlook, {"value": "At risk"}) is None
        assert rules.stored(outlook, {"value": "At risk", "reason": "One.\nTwo."}) is None
        assert rules.stored(_field("icp_fit"), {"value": "None", "reason": "x"}) is None

    def test_ai_fields_text_keeps_to_its_lines(self):
        brief = _field("account_brief")
        assert rules.stored(brief, {"value": "One.\n\nTwo.\nThree."})["value_text"] == (
            "One.\nTwo.\nThree."
        )
        assert rules.stored(brief, {"value": "1\n2\n3\n4"}) is None
        assert rules.stored(_field("what_is_blocking"), {"value": "One.\nTwo."}) is None
        assert rules.stored(brief, {"value": "x" * 601}) is None
        assert rules.stored(brief, {"value": None})["value_text"] is None

    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            (0, True),
            (100, True),
            (101, False),
            (-1, False),
            (True, False),
            ("50", False),
            (None, True),
        ],
    )
    def test_ai_fields_a_number_is_a_whole_number_from_0_to_100(self, value, ok):
        field = {"name": "Fit score", "output_type": "number", "with_reason": False}
        found = rules.stored(field, {"value": value})
        assert (found is not None) is ok
        if ok:
            assert found["value_number"] == value

    def test_ai_fields_the_instruction_states_the_form(self):
        assert "exactly one of: On track; At risk; Stalled." in rules.instruction(
            _field("deal_outlook")
        )
        assert "reason" in rules.instruction(_field("deal_outlook"))
        assert "at most 3 short lines" in rules.instruction(_field("account_brief"))
        assert "at most 1 short line " in rules.instruction(_field("what_is_blocking"))
