"""AI fields' rules — spec 009 FR-CRMAI-FIELD-01 to -05, FR-CRMAI-LLM-05;
Q-12, Q-13, Q-27, Q-38; D-3; data-model §3.6; plan §5.8.

Pure. The four default fields migration 065 seeds (`DEFAULT_FIELDS`, ICP fit
in Labs only, Q-12; Deal outlook, not "Deal health", Q-13), what an Admin's
definition may say (`options_refusal`, `key_for`), what the model is given
about one record (`facts`, then `prompt_parts`: its own fields, its latest
summaries and meeting notes, its enriched values, its next step dates and
its time in stage, every text masked for money, and no amount field at
all, FR-CRMAI-FIELD-03), the hash of those inputs (`inputs_hash`), when a
value is computed (`decide`: at most once an hour, with one trailing
recompute for what changed inside it, Q-38), which fields apply
(`applies`: What is blocking only while Deal outlook is At risk or
Stalled), and whether an answer fits its field's type (`stored`).

Nothing here reads or writes a stage, probability, flag or colour: a value
is shown on its record page and nowhere else (FR-CRMAI-FIELD-05).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from app.domain.crm import ai_rules

#: What the job records in the usage log and the job log.
JOB: Final = "ai_fields"
#: The phase switch it runs behind (FR-CRMAI-FLAG-01).
PHASE: Final = "3"
SOURCE_KIND: Final = "ai_field"

#: The records an AI field is defined on, and their tables (FR-CRMAI-FIELD-01).
TABLES: Final = {"company": "crm_companies", "opportunity": "crm_opportunities"}
OUTPUT_TYPES: Final = ("text", "single_select", "number")
#: FR-CRMAI-FIELD-01 — a single select's options (065's CHECK).
MIN_OPTIONS: Final = 2
MAX_OPTIONS: Final = 12
OPTIONS_REFUSAL: Final = "A choice field needs 2 to 12 options."
NOT_A_CHOICE: Final = "Only a choice field has options."
#: Q-38 — a record's AI fields are recomputed at most once an hour.
CAP: Final = timedelta(hours=1)
#: 065's column caps.
TEXT_MAX: Final = 600
REASON_MAX: Final = 200
#: A text field an Admin defines (D-3: its lines are not editable).
DEFAULT_LINES: Final = 3
#: How much of a record's history the prompt carries.
SUMMARY_LIMIT: Final = 5
NOTE_LIMIT: Final = 5
NOTE_CHARS: Final = 2000

#: `decide`'s answers.
COMPUTE: Final = "compute"
WAIT: Final = "wait"
SKIP: Final = "skip"

#: Migration 065's seeds, in the same words (a test holds the two together).
DEFAULT_FIELDS: Final = (
    {
        "key": "icp_fit",
        "name": "ICP fit",
        "record_type": "company",
        "prompt": (
            "Which of Nunnari Labs' lines of business this company fits best, judged from what "
            "it does and what it has discussed with us. Answer None when nothing on the record "
            "shows a fit."
        ),
        "output_type": "single_select",
        "options": ["Factory Brain", "Academy", "Services", "DeepWeaver", "None"],
        "max_lines": None,
        "depends_on": None,
        "with_reason": False,
        "position": 1,
        "labs_only": True,
    },
    {
        "key": "account_brief",
        "name": "Account brief",
        "record_type": "company",
        "prompt": (
            "A brief on this account for a seller about to open it: who the company is, where "
            "the relationship stands and what happens next. At most 3 short lines."
        ),
        "output_type": "text",
        "options": [],
        "max_lines": 3,
        "depends_on": None,
        "with_reason": False,
        "position": 2,
        "labs_only": False,
    },
    {
        "key": "deal_outlook",
        "name": "Deal outlook",
        "record_type": "opportunity",
        "prompt": (
            "How this deal is going, judged from its emails, meetings, next step dates and time "
            "in its stage: On track, At risk or Stalled, with a one-line reason."
        ),
        "output_type": "single_select",
        "options": ["On track", "At risk", "Stalled"],
        "max_lines": None,
        "depends_on": None,
        "with_reason": True,
        "position": 3,
        "labs_only": False,
    },
    {
        "key": "what_is_blocking",
        "name": "What is blocking",
        "record_type": "opportunity",
        "prompt": "What is holding this deal back, in one short line.",
        "output_type": "text",
        "options": [],
        "max_lines": 1,
        "depends_on": {"field": "deal_outlook", "values": ["At risk", "Stalled"]},
        "with_reason": False,
        "position": 4,
        "labs_only": False,
    },
)

#: The job's instruction, after the fixed rules every prompt carries; the
#: field's own name, prompt and answer form follow it (`instruction`).
INSTRUCTION: Final = (
    "Answer one question about one record of a sales CRM: a company or a deal. You are "
    "given the record's own fields, its latest email summaries and meeting notes, what was "
    "read from the company's website, its next step dates and how long it has been in its "
    "stage. Judge only from what you are given; never guess. Paraphrase; never quote."
)


# ---------------------------------------------------------------------------
# Definitions — FR-CRMAI-FIELD-01; D-3
# ---------------------------------------------------------------------------


def defaults_for(workspace_key: str) -> list[dict[str, Any]]:
    """The default fields 065 seeds in a workspace: ICP fit in Labs only."""
    return [
        {k: v for k, v in f.items() if k != "labs_only"}
        for f in DEFAULT_FIELDS
        if not f["labs_only"] or workspace_key == "labs"
    ]


def clean_options(options: Sequence[str] | None) -> list[str]:
    """The options as given, trimmed, empty ones left out, in order."""
    return [o.strip() for o in options or [] if o and o.strip()]


def options_refusal(output_type: str, options: Sequence[str]) -> str | None:
    """A single select has 2 to 12 options; no other field has any."""
    if output_type != "single_select":
        return NOT_A_CHOICE if options else None
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        return OPTIONS_REFUSAL
    return None


_KEY_PART: Final = re.compile(r"[^a-z0-9]+")


def key_for(name: str, taken: set[str]) -> str:
    """065's `^[a-z][a-z0-9_]{1,40}$`, from the name, not one already taken
    in the workspace."""
    base = _KEY_PART.sub("_", name.casefold()).strip("_")
    if not base or not base[0].isalpha():
        base = f"field_{base}".rstrip("_")
    base = base[:36].rstrip("_") or "field"
    if len(base) < 2:
        base = f"{base}_field"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def ordered(fields: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """A record's fields in their order, a field that depends on another
    after every field without one, so What is blocking sees this tick's
    Deal outlook."""
    return sorted(
        fields,
        key=lambda f: (bool(f.get("depends_on")), f.get("position") or 0, f["name"], f["id"]),
    )


# ---------------------------------------------------------------------------
# Inputs — FR-CRMAI-FIELD-03, FR-CRMAI-LLM-05
# ---------------------------------------------------------------------------

#: The record's own fields the model reads, by column, with their label;
#: `option` columns hold a select option's id, shown by its label. No amount,
#: currency, rate or probability is among them (FR-CRMAI-FIELD-03).
COMPANY_FIELDS: Final = (
    ("name", "Name", "text"),
    ("lead_stage", "Lead stage", "text"),
    ("account_type_id", "Account type", "option"),
    ("relationship_status_id", "Relationship status", "option"),
    ("industry_id", "Industry", "option"),
    ("institution_type_id", "Institution type", "option"),
    ("size_id", "Size", "option"),
    ("city", "City", "text"),
    ("country", "Country", "text"),
    ("description", "Description", "text"),
    ("next_touch_on", "Next touch on", "text"),
)
OPPORTUNITY_FIELDS: Final = (
    ("name", "Name", "text"),
    ("stage", "Stage", "text"),
    ("deal_type_id", "Deal type", "option"),
    ("engagement_model_id", "Engagement model", "option"),
    ("programme_id", "Programme", "option"),
    ("next_step", "Next step", "text"),
    ("next_step_on", "Next step on", "text"),
    ("expected_close_on", "Expected close on", "text"),
    ("proposal_sent_on", "Proposal sent on", "text"),
    ("lost_reason_id", "Lost reason", "option"),
)
OWN_FIELDS: Final = {"company": COMPANY_FIELDS, "opportunity": OPPORTUNITY_FIELDS}
#: What an enrichment row says, by key (064), shown with these labels.
ENRICHED: Final = (
    ("industry", "Industry"),
    ("sub_industry", "Sub-industry"),
    ("employee_band", "Employees"),
    ("city", "City"),
    ("country", "Country"),
    ("description", "Description"),
    ("products", "Products"),
)


def _text(value: Any) -> str:
    """A value as one masked line of text (FR-CRMAI-LLM-05)."""
    if isinstance(value, list | tuple):
        value = "; ".join(str(v) for v in value if v not in (None, ""))
    return ai_rules.mask_money(" ".join(str(value).split()))


def facts(
    kind: str,
    record: Mapping[str, Any],
    *,
    labels: Mapping[str, str],
    company_name: str | None = None,
    summaries: Sequence[Mapping[str, Any]] = (),
    notes: Sequence[Mapping[str, Any]] = (),
    enrichment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the model may know about one record, as plain data: the
    record's own fields (listed in `OWN_FIELDS`, never an amount), its
    latest summaries and meeting notes, its enriched values and, for a deal,
    its company and when it entered its stage. Every text is masked for
    money. The same facts give the same hash."""
    own: dict[str, str] = {}
    if kind == "opportunity" and company_name:
        own["Company"] = _text(company_name)
    for column, label, how in OWN_FIELDS[kind]:
        value = record.get(column)
        if how == "option":
            value = labels.get(str(value)) if value else None
        if value in (None, "", []):
            continue
        own[label] = _text(value)
    out: dict[str, Any] = {"record_type": kind, "fields": own}
    if kind == "opportunity" and record.get("stage_entered_at"):
        out["stage_entered_at"] = str(record["stage_entered_at"])
    out["summaries"] = [
        {
            "summary": _text(s.get("summary") or ""),
            "asks": [_text(a) for a in s.get("asks") or []],
            "commitments": [_text(c.get("text") or "") for c in s.get("commitments") or []],
            "sentiment": s.get("sentiment"),
            "next_step": _text(s["next_step"]) if s.get("next_step") else None,
            "next_step_on": str(s["next_step_on"]) if s.get("next_step_on") else None,
        }
        for s in list(summaries)[:SUMMARY_LIMIT]
    ]
    out["meeting_notes"] = [
        {
            "occurred_on": str(n["occurred_on"]) if n.get("occurred_on") else None,
            "body": ai_rules.mask_money(str(n.get("body") or "")[:NOTE_CHARS]),
        }
        for n in list(notes)[:NOTE_LIMIT]
    ]
    values = (enrichment or {}).get("values") or {}
    out["enriched"] = {
        label: _text(values[key])
        for key, label in ENRICHED
        if values.get(key) not in (None, "", [])
    }
    return out


def days_in_stage(entered: Any, now: datetime) -> int | None:
    if not entered:
        return None
    at = entered if isinstance(entered, datetime) else datetime.fromisoformat(str(entered))
    return max(0, (now - at).days)


def _depends_line(depends: Mapping[str, Any] | None) -> str | None:
    if not depends:
        return None
    line = f"{depends['name']}: {depends['value']}"
    return f"{line} ({depends['reason']})" if depends.get("reason") else line


def prompt_parts(
    record_facts: Mapping[str, Any],
    now: datetime,
    depends: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """The facts as quoted parts: the record, each summary, each meeting
    note, the enriched values, and the value this field depends on."""
    lines = [f"{label}: {value}" for label, value in record_facts["fields"].items()]
    days = days_in_stage(record_facts.get("stage_entered_at"), now)
    if days is not None:
        lines.append(f"Days in this stage: {days}")
    parts = [("record", "\n".join(lines) or "Nothing recorded.")]
    for n, s in enumerate(record_facts["summaries"], start=1):
        text = [s["summary"]]
        text += [f"Asked: {a}" for a in s["asks"]]
        text += [f"Promised: {c}" for c in s["commitments"]]
        if s.get("sentiment"):
            text.append(f"Sentiment: {s['sentiment']}")
        if s.get("next_step"):
            text.append(f"Next step: {s['next_step']} ({s.get('next_step_on') or 'no date'})")
        parts.append((f"email summary {n}", "\n".join(text)))
    for n, note in enumerate(record_facts["meeting_notes"], start=1):
        parts.append((f"meeting note {n} {note['occurred_on'] or ''}".strip(), note["body"]))
    if record_facts["enriched"]:
        parts.append(
            (
                "website",
                "\n".join(f"{label}: {value}" for label, value in record_facts["enriched"].items()),
            )
        )
    line = _depends_line(depends)
    if line:
        parts.append(("depends on", line))
    return parts


def inputs_hash(
    field: Mapping[str, Any],
    record_facts: Mapping[str, Any],
    depends: Mapping[str, Any] | None = None,
) -> str:
    """FR-CRMAI-FIELD-04 — the hash of what a value is computed from: the
    field's own definition and the record's facts (when it entered its
    stage, not how many days ago, so a day passing changes nothing)."""
    payload = {
        "field": {
            "name": field["name"],
            "prompt": field["prompt"],
            "output_type": field["output_type"],
            "options": list(field.get("options") or []),
            "max_lines": field.get("max_lines"),
            "with_reason": bool(field.get("with_reason")),
        },
        "facts": record_facts,
        "depends": dict(depends) if depends else None,
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# When — FR-CRMAI-FIELD-04, Q-38
# ---------------------------------------------------------------------------


def at(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def decide(row: Mapping[str, Any] | None, new_hash: str, now: datetime) -> str:
    """`COMPUTE` a value that is missing, or whose inputs changed (or that
    was marked for recompute) once its hour is over; `WAIT` for the end of
    the hour when that happened inside it (the trailing recompute); `SKIP`
    one whose inputs are the same and that nobody marked."""
    if row is None:
        return COMPUTE
    if row.get("inputs_hash") == new_hash and not row.get("dirty_at"):
        return SKIP
    if now >= at(row["next_allowed_at"]):
        return COMPUTE
    return WAIT


def next_allowed(now: datetime) -> datetime:
    return now + CAP


# ---------------------------------------------------------------------------
# Dependencies — the FR-CRMAI-FIELD table (What is blocking)
# ---------------------------------------------------------------------------


def dependency(
    field: Mapping[str, Any],
    by_key: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Mapping[str, Any] | None],
) -> tuple[bool, dict[str, Any] | None]:
    """`(applies, depends)` for a field: one with no `depends_on` always
    applies; one with it applies only while the field it names is enabled
    and holds one of the named values, given back as `depends` for the
    prompt and the hash."""
    rule = field.get("depends_on")
    if not rule:
        return True, None
    other = by_key.get(rule.get("field") or "")
    if other is None:
        return False, None
    value = values.get(other["key"]) or {}
    shown = value.get("value_text")
    if shown not in (rule.get("values") or []):
        return False, None
    return True, {"name": other["name"], "value": shown, "reason": value.get("reason")}


def cleared(row: Mapping[str, Any] | None) -> bool:
    """A value that holds nothing (or no value at all)."""
    return row is None or (
        row.get("value_text") is None
        and row.get("value_number") is None
        and row.get("reason") is None
    )


# ---------------------------------------------------------------------------
# Answers — FR-CRMAI-FIELD-01; the FR-CRMAI-FIELD table
# ---------------------------------------------------------------------------


def lines_of(field: Mapping[str, Any]) -> int:
    return int(field.get("max_lines") or DEFAULT_LINES)


def instruction(field: Mapping[str, Any]) -> str:
    """The job's instruction with the field's name, the Admin's prompt and
    the form of the answer."""
    kind = field["output_type"]
    if kind == "single_select":
        form = "value, exactly one of: " + "; ".join(field.get("options") or []) + "."
    elif kind == "number":
        form = "value, a whole number from 0 to 100, or null when the record does not say enough."
    else:
        n = lines_of(field)
        form = (
            f"value, plain text of at most {n} short line{'s' if n > 1 else ''} and "
            f"{TEXT_MAX} characters, or null when the record does not say enough."
        )
    if field.get("with_reason"):
        form += f" Also answer reason, one line of at most {REASON_MAX} characters on why."
    return f"{INSTRUCTION}\n\nThe field: {field['name']}.\n{field['prompt']}\n\nAnswer with {form}"


def stored(field: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any] | None:
    """The answer as 065's columns `{value_text, value_number, reason}`, or
    None when it does not fit the field: an option it does not list, a
    number outside 0 to 100, text past its lines or length, or a reason
    missing where the field gives one (or present where it does not)."""
    value = answer.get("value")
    reason = answer.get("reason")
    out: dict[str, Any] = {"value_text": None, "value_number": None, "reason": None}
    kind = field["output_type"]
    if kind == "single_select":
        if not isinstance(value, str) or value not in (field.get("options") or []):
            return None
        out["value_text"] = value
    elif kind == "number":
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                return None
            out["value_number"] = value
    else:
        if value is not None:
            if not isinstance(value, str):
                return None
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            text = "\n".join(lines)
            if len(lines) > lines_of(field) or len(text) > TEXT_MAX:
                return None
            out["value_text"] = text or None
    if field.get("with_reason"):
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= REASON_MAX:
            return None
        if "\n" in reason.strip():
            return None
        out["reason"] = reason.strip()
    elif reason is not None:
        return None
    return out


def shown(field: Mapping[str, Any], row: Mapping[str, Any] | None) -> Any:
    """The value a record page shows: the number for a number field, else
    the text; None before the first computation or once cleared."""
    if row is None:
        return None
    return row.get("value_number") if field["output_type"] == "number" else row.get("value_text")
