"""Claude, raw HTTP — spec 009 FR-CRMAI-LLM-01 to -03, -06, NFR-CRMAI-04,
-06, -08; C4, Q-42; plan §2.1, §5.3.

One request shape: `POST /v1/messages` with the key, the API version, the
model, a system prompt, one user message and a JSON-schema output format, so
the answer is structured output (FR-CRMAI-LLM-03). `claude-sonnet-5-5`
refuses a forced tool call (`tool_choice` "tool" answers 400), so the job's
schema is sent as `output_config.format` instead and the answer is the JSON
of the first text block. The schema is checked again by the caller with
the job's pydantic model; the copy sent here leaves out the constraints the
API does not take (lengths, bounds, patterns) and closes every object.

No vendor SDK, as the Gmail client (Q-42). Timeouts: 5 s to connect, 60 s
to read. The key (`ANTHROPIC_API_KEY`) is set on `worker-crm-ai` only and
this module refuses to run without it. Every call logs one JSON line with
the job, the workspace, the status, the latency and the tokens, never the
prompt or the answer (NFR-CRMAI-06). No exception raised here carries a
response body, request text or the key: messages name the status or the
exception type only, and none keeps the exception it replaced.

Only `services/crm/ai.py` calls this module; nothing under `app/api`
imports it (FR-CRMAI-LLM-01, pinned by a test).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.config import settings

logger = logging.getLogger("nldw-task-master")

API: Final = "https://api.anthropic.com/v1/messages"
VERSION: Final = "2023-06-01"
TIMEOUT: Final = httpx.Timeout(60.0, connect=5.0)
MAX_TOKENS: Final = 16000
#: JSON-schema keywords the structured-output format does not take; the
#: caller's pydantic model still enforces them.
_UNSUPPORTED: Final = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "title",
        "default",
    }
)


class ClaudeError(Exception):
    """A Claude call failed. The message names the status or the exception
    type only. A declined call is still billed, so it carries its tokens."""

    input_tokens = 0
    output_tokens = 0


class ClaudeTimeout(ClaudeError):
    """The call did not answer within the timeouts."""


class NotConfigured(ClaudeError):
    """No Claude key in this process."""


@dataclass(frozen=True)
class Reply:
    #: The parsed JSON answer, or None when the answer was not JSON.
    data: Any
    model: str
    input_tokens: int
    output_tokens: int


def _http() -> httpx.Client:
    """The HTTP client; tests give it a mock transport."""
    return httpx.Client(timeout=TIMEOUT)


def api_schema(schema: Any) -> Any:
    """The output schema as the API takes it: unsupported constraints left
    out, every object closed (`additionalProperties: false`)."""
    if isinstance(schema, list):
        return [api_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED:
            continue
        if key in ("properties", "$defs"):
            out[key] = {name: api_schema(sub) for name, sub in value.items()}
        else:
            out[key] = api_schema(value)
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def _log(job: str, workspace: str, status: Any, started: float, usage: dict | None) -> None:
    logger.info(
        '{"event": "crm_claude_call", "job": "%s", "workspace": "%s", "status": "%s", '
        '"ms": %d, "input_tokens": %d, "output_tokens": %d}',
        job,
        workspace,
        status,
        int((time.monotonic() - started) * 1000),
        int((usage or {}).get("input_tokens") or 0),
        int((usage or {}).get("output_tokens") or 0),
    )


def messages(
    *, model: str, system: str, user: str, schema: dict[str, Any], job: str, workspace: str
) -> Reply:
    """One structured-output call. Raises `NotConfigured` without a key,
    `ClaudeTimeout` on a timeout and `ClaudeError` on any other failure."""
    secret = settings.ANTHROPIC_API_KEY
    key = secret.get_secret_value().strip() if secret else ""
    if not key:
        raise NotConfigured("The Claude key is not set in this process.")
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": api_schema(schema)}},
    }
    headers = {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}
    started = time.monotonic()
    failed: tuple[type[ClaudeError], str] | None = None
    try:
        with _http() as http:
            response = http.post(API, json=body, headers=headers, timeout=TIMEOUT)
    except httpx.TimeoutException as exc:
        failed = (ClaudeTimeout, type(exc).__name__)
    except httpx.HTTPError as exc:
        failed = (ClaudeError, type(exc).__name__)
    if failed is not None:
        # Raised outside the handler, so the original exception (whose text
        # may name the request) is not kept as the context either.
        error, name = failed
        _log(job, workspace, name, started, None)
        verb = "timed out" if error is ClaudeTimeout else "failed"
        raise error(f"The Claude call {verb} ({name}).")
    try:
        answer = response.json() if response.status_code == 200 else {}
    except ValueError:
        answer = {}
    usage = answer.get("usage") if isinstance(answer, dict) else None
    _log(job, workspace, response.status_code, started, usage)
    if response.status_code != 200 or not isinstance(answer, dict):
        raise ClaudeError(f"The Claude call failed ({response.status_code}).")
    if answer.get("stop_reason") == "refusal":
        declined = ClaudeError("The Claude call was declined (refusal).")
        declined.input_tokens = int((usage or {}).get("input_tokens") or 0)
        declined.output_tokens = int((usage or {}).get("output_tokens") or 0)
        raise declined
    text = next(
        (b.get("text") for b in answer.get("content") or [] if b.get("type") == "text"), None
    )
    try:
        data = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        data = None
    return Reply(
        data=data,
        model=str(answer.get("model") or model)[:100],
        input_tokens=int((usage or {}).get("input_tokens") or 0),
        output_tokens=int((usage or {}).get("output_tokens") or 0),
    )
