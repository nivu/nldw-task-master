"""AI fields — spec 009 contracts §2.5; FR-CRMAI-FIELD-01, -04; Q-38; D-3.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/ai_fields.py` does the rest. The definitions are for the
workspace's Admins, in Settings, on the web only (no MCP tool,
FR-CRMAI-MCP-04): name, record type, prompt, output type, options and
enabled; the record and output types are fixed once a field exists, and
there is no DELETE (a field is disabled, its values stay, hidden). An Admin
or a Member asks for a record's AI fields to be recomputed from its page;
a Viewer is refused. Another workspace's field or record is not found. A
record's values are read with the record (`GET
/crm/{companies|opportunities}/{id}`, `ai_fields`).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.crm.deps import CrmAdminDep, CrmWriteDep
from app.schemas.crm import AiFieldIn, AiFieldPatch
from app.services.crm import ai_fields as service

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/ai-fields")
def list_fields(ctx: CrmAdminDep) -> list[dict]:
    """`[{id, key, name, record_type, prompt, output_type, options, enabled}]`."""
    return service.list_fields(ctx)


@router.post("/ai-fields", status_code=201)
def create_field(ctx: CrmAdminDep, body: AiFieldIn) -> dict:
    """The field; 422 "A choice field needs 2 to 12 options."; 409 a name
    already used for that record type."""
    return service.create_field(ctx, body)


@router.patch("/ai-fields/{field_id}")
def update_field(field_id: str, ctx: CrmAdminDep, body: AiFieldPatch) -> dict:
    """The field; 404 "No such AI field."."""
    return service.update_field(ctx, field_id, body)


@router.post("/companies/{record_id}/ai-fields/recompute")
def recompute_company(record_id: str, ctx: CrmWriteDep) -> dict:
    """`{queued, next_allowed_at}` (Q-38); 404 "No such company."."""
    return service.request(ctx, "company", record_id)


@router.post("/opportunities/{record_id}/ai-fields/recompute")
def recompute_deal(record_id: str, ctx: CrmWriteDep) -> dict:
    """`{queued, next_allowed_at}` (Q-38); 404 "No such opportunity."."""
    return service.request(ctx, "opportunity", record_id)
