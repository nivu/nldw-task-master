"""The one way to call Claude — spec 009 FR-CRMAI-LLM-01 to -08, FR-CRMAI-JOB-03,
NFR-CRMAI-01; Q-02, Q-03, Q-08, Q-23, Q-36, Q-37; D-1; plan §5.3.

Every model call of every job goes through `call()`, on `worker-crm-ai`:

1. the job's phase must be on in the workspace's `ai` setting, else the run
   is skipped "Switched off" (FR-CRMAI-FLAG-03). Spec 010 (plan §5.6): email
   capture's discovery has its own switch and passes no phase (`None`),
   which skips this step only;
2. both rates of the rate table must be set, and above 0, else "AI rates
   are not set" (D-1);
3. month-to-date estimated spend (the Asia/Kolkata month of the check,
   not of the run's start) must be under the budget, else the Admins are told once that month and the run is skipped
   "AI budget reached" (FR-CRMAI-LLM-07, -08). The check runs before every
   call, the retry included; `worker-crm-ai` runs one task at a time, so
   overshoot is the one call in flight;
4. the prompt is the fixed rules, the job's instruction, and each part as a
   quoted <data> block with phone numbers removed (FR-CRMAI-LLM-04);
5. one structured-output call (`services/crm/claude.py`);
6. one usage row per attempt, stamped when it ended: tokens (for a call
   sent but not answered, the client's upper bound), estimated cost,
   outcome, never text (FR-CRMAI-LLM-06);
7. the answer is validated against the job's output model; an invalid
   answer is asked again once, and a second invalid answer fails the item
   "Output did not match the schema" (FR-CRMAI-LLM-03, Q-36);
8. money is masked and disallowed links dropped in every string of the
   answer (FR-CRMAI-LLM-04, -05), so no job can forget to. Enrichment passes
   the company's `site`, whose own addresses are kept too.

A skip raises `jobs.Skipped`, which the job log records as `skipped` with
its reason. A failed item raises `AiFailed`, whose `reason` is one of
`ai_rules.FAILURE_REASONS`: the job counts it and the item stays pending
for the next tick. Any other exception becomes a `SafeError`, whose message
is a fixed sentence naming the step and the exception type, never
`str(exc)`, a body or model output (FR-CRMAI-JOB-03). Prompts and answers
live only in local variables.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.domain.crm import ai_rules as rules
from app.services.crm import claude, jobs, notifications, store


class SafeError(Exception):
    """An AI job's failure as a fixed sentence: "<step> failed: <type>"."""

    def __init__(self, step: str, exc_type: str) -> None:
        super().__init__(f"{step} failed: {exc_type}")
        self.step = step
        self.exc_type = exc_type


class AiFailed(SafeError):
    """One item's call failed, for one of `ai_rules.FAILURE_REASONS`."""

    def __init__(self, reason: str, step: str, exc_type: str) -> None:
        super().__init__(step, exc_type)
        self.reason = reason


def call(
    ws: dict[str, Any],
    job: str,
    phase: str | None,
    output_model: type[BaseModel],
    instruction: str,
    parts: Sequence[tuple[str, str]],
    *,
    site: str | None = None,
) -> BaseModel:
    """One model answer for `job` in workspace `ws`, validated against
    `output_model` and masked. `parts` are `(label, text)` pairs, each quoted
    as data. With `site`, links on that site survive the link policy."""
    try:
        return _call(ws, job, phase, output_model, instruction, parts, site=site)
    except (jobs.Skipped, SafeError, SoftTimeLimitExceeded):
        # A task's soft time limit is its own to handle (spec 010 OPS-2),
        # and carries no text.
        raise
    except Exception as exc:
        name = type(exc).__name__
    # Raised outside the handler, so the exception it replaces (whose text
    # may carry a prompt or an answer) is not kept as its context.
    raise SafeError("AI call", name)


def _rates() -> tuple[Decimal, Decimal] | None:
    return rules.rates(settings.CRM_AI_INPUT_USD_PER_MTOK, settings.CRM_AI_OUTPUT_USD_PER_MTOK)


def gate(ws: dict[str, Any], phase: str | None) -> tuple[Any, tuple[Decimal, Decimal]]:
    """Steps 1 to 3 (the phase, the rates, the budget), as `call` runs them:
    `(the ai setting, the rates)`, else `jobs.Skipped`. A job runs it before
    any outside read (a website, Gmail, Fireflies), so a switched-off or
    spent workspace reads nothing. With no phase (spec 010's discovery, which
    has its own switch) step 1 is skipped; the rates and budget are not."""
    value = store.ai_setting(ws["id"])
    if phase is not None and not rules.phase_on(value, phase):
        raise jobs.Skipped(rules.SWITCHED_OFF)
    rates = _rates()
    if rates is None:
        raise jobs.Skipped(rules.RATES_NOT_SET)
    _check_budget(ws, value)
    return value, rates


def _check_budget(ws: dict[str, Any], value: Any) -> None:
    # The month of this check, not of the run's start: a run that crosses
    # midnight at the month's end is held to the new month's budget.
    month_start = rules.ist_month_start(jobs._now())
    spend = Decimal(str(store.ai_spend(ws["id"], month_start).get("total_usd") or "0"))
    if spend >= rules.budget_of(value):
        notifications.budget_reached_notice(ws, month_start.strftime("%Y-%m"))
        raise jobs.Skipped(rules.BUDGET_REACHED)


def _call(
    ws: dict[str, Any],
    job: str,
    phase: str | None,
    output_model: type[BaseModel],
    instruction: str,
    parts: Sequence[tuple[str, str]],
    *,
    site: str | None = None,
) -> BaseModel:
    value, rates = gate(ws, phase)

    system = f"{rules.PROMPT_RULES}\n\n{instruction}"
    user = "\n\n".join(
        rules.quote_untrusted(label, rules.strip_phones(text)) for label, text in parts
    )
    schema = output_model.model_json_schema()

    for attempt in (1, 2):
        if attempt == 2:
            _check_budget(ws, value)
        reply, failed = _ask(ws, job, system, user, schema)
        if failed is not None:
            reason, exc = failed
            if not isinstance(exc, claude.NotConfigured):
                outcome = "timed_out" if reason == rules.CALL_TIMED_OUT else "failed"
                _usage(
                    ws,
                    job,
                    rules.CRM_AI_MODEL,
                    exc.input_tokens,
                    exc.output_tokens,
                    outcome,
                    rates,
                )
            raise AiFailed(reason, "Claude call", type(exc).__name__)
        assert reply is not None
        output = _valid(output_model, reply.data)
        outcome = "ok" if output is not None else "invalid_output"
        _usage(ws, job, reply.model, reply.input_tokens, reply.output_tokens, outcome, rates)
        if output is None:
            if attempt == 2:
                raise AiFailed(rules.OUTPUT_INVALID, "Schema validation", "ValidationError")
            continue
        masked = _valid(output_model, _clean(output.model_dump(mode="python"), site))
        if masked is None:
            raise AiFailed(rules.OUTPUT_INVALID, "Schema validation", "ValidationError")
        return masked
    raise AssertionError("unreachable")


def _ask(
    ws: dict[str, Any], job: str, system: str, user: str, schema: dict[str, Any]
) -> tuple[claude.Reply | None, tuple[str, claude.ClaudeError] | None]:
    """One call: the reply, or the failure reason and the client's error
    (whose message names a status or an exception type only)."""
    try:
        reply = claude.messages(
            model=rules.CRM_AI_MODEL,
            system=system,
            user=user,
            schema=schema,
            job=job,
            workspace=ws.get("key") or "-",
        )
    except claude.ClaudeTimeout as exc:
        return None, (rules.CALL_TIMED_OUT, exc)
    except claude.ClaudeError as exc:
        return None, (rules.CALL_FAILED, exc)
    return reply, None


def _valid(output_model: type[BaseModel], data: Any) -> BaseModel | None:
    """The answer as the job's model, or None: a validation error's text
    repeats the answer, so it goes no further than here."""
    try:
        return output_model.model_validate(data)
    except ValidationError:
        return None


def _clean(value: Any, site: str | None = None) -> Any:
    """Money masked and disallowed links dropped in every string."""
    if isinstance(value, str):
        return rules.mask_money(rules.drop_urls(value, site=site))
    if isinstance(value, dict):
        return {k: _clean(v, site) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v, site) for v in value]
    return value


def _usage(
    ws: dict[str, Any],
    job: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    outcome: str,
    rates: tuple[Decimal, Decimal],
) -> None:
    # Stamped when the call ended, not when the run began, so it counts in
    # the month it was made.
    store.insert_ai_usage(
        ws["id"],
        {
            "job": job,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": str(rules.estimate_cost(input_tokens, output_tokens, *rates)),
            "outcome": outcome,
            "at": jobs._now().isoformat(),
        },
    )
