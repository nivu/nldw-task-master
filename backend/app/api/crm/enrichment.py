"""Company enrichment — spec 009 contracts §2.4; FR-CRMAI-ENR-01, -07, -08,
FR-CRMAI-MCP-04; D-3.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/enrichment.py` does the rest. **Enrich selected** is for the
workspace's Admins, from a company view, on the web only (no MCP tool,
`007` FR-CRM-MCP-10). It queues the selected companies, also those already
enriched from the same site; the reading happens in the background, never
in this request (FR-CRMAI-ENR-07). A company of another workspace, or one in
the Trash, is not found. Promoting an enriched value is `PATCH
/crm/companies/{id}`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.crm.deps import CrmAdminDep
from app.schemas.crm import EnrichRunIn
from app.services.crm import enrichment as service

router = APIRouter(prefix="/crm/enrichment", tags=["crm"])


@router.post("/run")
def enrich_selected(ctx: CrmAdminDep, body: EnrichRunIn) -> dict:
    """`{queued, skipped_no_website, already_enriched}`; 404 "No such
    company."."""
    return service.request(ctx, body.company_ids)
