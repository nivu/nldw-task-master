"""Email thread summaries — spec 009 contracts §2.3; FR-CRMAI-SUM-08,
FR-CRMAI-MCP-01 (`crm_thread_summary`), FR-CRMAI-FLAG-03.

HTTP only: the CRM guard resolves the workspace and role, and
`services/crm/summaries.py` does the rest. Every member of the workspace
reads a thread's summary, Viewers included (FR-CRMAI-SUM-08); a thread of
another workspace's mailbox, one with no summary, and every thread while
phase 1 is off are not found. The thread key is the one the Emails tab and
`crm_get` return.
"""

from __future__ import annotations

from fastapi import APIRouter, Path

from app.api.crm.deps import CrmReadDep
from app.services.crm import summaries as service

router = APIRouter(prefix="/crm/email-threads", tags=["crm"])


@router.get("/{thread_key}/summary")
def thread_summary(ctx: CrmReadDep, thread_key: str = Path(max_length=300)) -> dict:
    """`{thread_key, subject, last_at, summary, asks, commitments, next_step,
    next_step_on, sentiment, links, ai}`; 404 "No such summary."."""
    return service.by_key(ctx, thread_key)
