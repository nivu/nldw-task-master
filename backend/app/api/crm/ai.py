"""Settings, AI — spec 009 contracts §2.1; FR-CRMAI-LLM-07 to -10,
FR-CRMAI-FLAG-01; D-1.

HTTP only, workspace Admins only: a Member or Viewer is refused 403 and a
non-member 404, as every Admin route (FR-CRMAI-LLM-10). Cost is workspace
administration and this is the only route that returns it. The budget and
the phases are changed through `PATCH /crm/settings/ai`.

This module never imports the Claude client: every model call is made on
`worker-crm-ai` (FR-CRMAI-LLM-01).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter

from app.api.crm.deps import CrmAdminDep
from app.config import settings as config
from app.domain.crm import ai_rules
from app.schemas.crm import AiOverviewOut, AiSpendOut
from app.services.crm import store

router = APIRouter(prefix="/crm", tags=["crm"])

ESTIMATE_NOTE = "Spend is an estimate from token counts."


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


@router.get("/ai")
def read_ai(ctx: CrmAdminDep) -> dict:
    """This Asia/Kolkata month's budget, estimated spend in total and per
    job, whether the budget is reached, the phases, and whether the rate
    table is set (D-1)."""
    now = _now()
    value = store.ai_setting(ctx.workspace_id)
    month_start = ai_rules.ist_month_start(now)
    spend = store.ai_spend(ctx.workspace_id, month_start)
    total = Decimal(str(spend.get("total_usd") or "0"))
    budget = ai_rules.budget_of(value)
    return AiOverviewOut(
        month=month_start.strftime("%Y-%m"),
        budget_usd=budget,
        spend=AiSpendOut(
            total_usd=total.quantize(Decimal("0.01")),
            by_job={
                job: Decimal(str((spend.get("by_job") or {}).get(job) or "0")).quantize(
                    Decimal("0.01")
                )
                for job in ai_rules.USAGE_JOBS
            },
        ),
        budget_reached=total >= budget,
        phases=ai_rules.phases_of(value),
        estimate_note=ESTIMATE_NOTE,
        rates_configured=ai_rules.rates(
            config.CRM_AI_INPUT_USD_PER_MTOK, config.CRM_AI_OUTPUT_USD_PER_MTOK
        )
        is not None,
    ).model_dump(mode="json")
