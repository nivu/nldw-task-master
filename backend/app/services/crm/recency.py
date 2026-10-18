"""Contact recency — spec 009 FR-CRMAI-REL-01 to -03; Q-17, Q-21;
data-model §3.10; plan §5.10.

Recomputed with the flags job (`flags.recompute_workspace`) while phase 4
is on: `crm_contact_facts` (067) gives every live company and person its
latest counted emails and meeting note, `domain/crm/recency.level` decides,
and `crm_set_contact_recency` stores the level. That writer touches only
`contact_recency`, which the touch and audit triggers skip, so a recompute
moves neither "last updated" nor the audit (FR-CRMAI-REL-02). With phase 4
off nothing is recomputed, and what is stored stays, hidden
(FR-CRMAI-FLAG-03).

With Gmail sync off for the workspace (switched off, or the credential not
there), only meeting notes count and the record answer says so
(`meetings_only`, FR-CRMAI-REL-03).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.crm import ai_rules
from app.domain.crm import recency as rules
from app.services.crm import email_sync, store
from app.services.crm import records as base


def shown(workspace_id: str) -> bool:
    """FR-CRMAI-FLAG-03 — contact recency is computed and read only while
    phase 4 is on."""
    return ai_rules.phase_on(store.ai_setting(workspace_id), rules.PHASE)


def meetings_only(workspace_id: str) -> bool:
    """FR-CRMAI-REL-03 — whether Gmail sync is off, so only meetings count."""
    return email_sync.off_reason(workspace_id) is not None


def recompute_workspace(workspace_id: str, now: datetime) -> dict[str, int]:
    """Every live company's and person's level, stored. Nothing while phase
    4 is off."""
    if not shown(workspace_id):
        return {}
    only = meetings_only(workspace_id)
    facts = store.contact_facts(workspace_id, now)
    changed = 0
    for kind, table in rules.TABLES.items():
        rows = [
            {"id": record_id, "level": rules.level(found, now=now, meetings_only=only)}
            for record_id, found in sorted((facts.get(kind) or {}).items())
        ]
        if rows:
            changed += store.set_contact_recency(workspace_id, table, rows)
    return {"recency_changed": changed}


def for_record(workspace_id: str, kind: str, record_id: str) -> dict[str, Any]:
    """Contracts §1: a company's or person's `contact_recency`, `{level,
    meetings_only}`; `level` is None before the first recompute."""
    row = base.by_id(workspace_id, rules.TABLES[kind], [record_id]).get(record_id) or {}
    return {"level": row.get("contact_recency"), "meetings_only": meetings_only(workspace_id)}
