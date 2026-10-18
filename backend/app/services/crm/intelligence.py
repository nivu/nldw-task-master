"""What the AI layer adds to a record's read — spec 009 FR-CRMAI-MCP-02,
FR-CRMAI-SUM-08, FR-CRMAI-FLAG-03, -04; contracts §1 (`GET
/crm/{companies|people|opportunities}/{id}`).

`decorate()` takes the answer `records.get` built and adds, for a company,
a person or a deal:

- `summaries`: the latest five threads' summaries, while phase 1 is on (the
  key is absent, not empty, while it is off);
- `enrichment`, for a company not in the Trash: its enriched values, status,
  what may be promoted and its open conflict items, while phase 2 is on
  (None when it was never queued; absent while the phase is off);
- `ai_fields`, for a company or a deal: its AI field values, while phase 3
  is on, for every role (absent while the phase is off, FR-CRMAI-FIELD-05,
  -06);
- `open_review_items`: how many open Review Queue items about the record
  the caller may see (none for a Viewer, FR-CRMAI-REV-03).

The record route and `crm_get` read through it, so the web page and Claude
see the same. A later slice adds contact recency here.
"""

from __future__ import annotations

from typing import Any

from app.domain.crm import ai_fields as field_rules
from app.domain.crm import review as review_rules
from app.services.crm import ai_fields, enrichment, store, summaries
from app.services.crm import records as base

#: The record kinds the AI layer decorates.
KINDS = ("company", "person", "opportunity")


def decorate(ctx: Any, kind: str, answer: dict[str, Any]) -> dict[str, Any]:
    """The record answer with what the AI layer adds to it."""
    if kind not in KINDS:
        return answer
    record_id = answer["record"]["id"]
    if summaries.shown(ctx.workspace_id):
        answer["summaries"] = summaries.for_record(ctx.workspace_id, kind, record_id)
    if kind == "company" and enrichment.shown(ctx.workspace_id):
        company = base.by_id(ctx.workspace_id, "crm_companies", [record_id]).get(record_id)
        if company and not company.get("deleted_at"):
            answer["enrichment"] = enrichment.for_company(ctx, company)
    if kind in field_rules.TABLES and ai_fields.shown(ctx.workspace_id):
        answer["ai_fields"] = ai_fields.for_record(ctx, kind, record_id)
    answer["open_review_items"] = sum(
        1
        for item in store.list_review_items(ctx.workspace_id, status="open")
        if item["record_type"] == kind
        and item["record_id"] == record_id
        and review_rules.may_see(ctx.role, ctx.user.id, item)
    )
    return answer
