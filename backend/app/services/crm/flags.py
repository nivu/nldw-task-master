"""Follow-up and deal-health flags, stored — spec 007 FR-CRM-FLAG-01 to -04,
FR-CRM-AUTO-01 (automation 4); Q-20; data-model §3.21; plan §5.11.

Flags are computed by `domain/crm/flags.py` and stored on the record, so a
view filters and sorts by them on the server. They are recomputed:

- after every write that can change one (`after_write`, called by the record
  and deal services): the record itself, and what its flags feed — a deal's
  company, a task's or note's linked deals and companies;
- for a whole workspace by the 30-minute job (`recompute_workspace`), which
  catches what time alone changes (a next step falling due, a deal going
  stale).

They are written by `crm_set_flags` (038): no audit row and no change to
`updated_at`, because a recompute is not an edit. Email flags (Reply owed,
Client silent) and a deal's email activity read the messages email sync
stores (S10): `crm_flag_facts` (039) gives each record its latest inbound and
outbound message that counts, never an internal or automatic one
(FR-CRM-FLAG-04). A record with no stored email has none of those flags.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.domain.crm import ai_rules
from app.domain.crm import flags as rules
from app.domain.crm import followups as rule_defs
from app.domain.crm import today as today_rules
from app.services import supabase as db
from app.services.crm import notifications, store

logger = logging.getLogger("nldw-task-master")

#: Record type → table, for the record services' callers.
TABLES = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "task": "tasks",
}


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


def _thresholds(workspace_id: str) -> dict[str, int]:
    found = next(
        (s["value"] for s in store.list_settings(workspace_id) if s["key"] == "flag_thresholds"),
        None,
    )
    return rules.thresholds(found)


def _zones() -> dict[str, str]:
    """Each person's time zone: a deal's days are counted in its owner's."""
    return {
        p["id"]: p.get("timezone") or today_rules.DEFAULT_ZONE
        for p in db.list_profiles(include_crm_only=True)
    }


def _mail(facts: dict[str, Any], record_type: str, record_id: str) -> list[dict[str, Any]]:
    """A record's email facts as the messages the rules read: its latest
    inbound and outbound, which is all Reply owed and Client silent need."""
    found = ((facts.get("emails") or {}).get(record_type) or {}).get(record_id) or {}
    return [
        {"direction": direction, "sent_at": found[key]}
        for key, direction in (("in", "inbound"), ("out", "outbound"))
        if found.get(key)
    ]


def _compute(
    workspace_id: str,
    *,
    deals: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    people: list[dict[str, Any]],
    facts: dict[str, Any],
    now: datetime,
) -> dict[str, int]:
    """Computes and stores the flags of the rows given. A company's open
    deals must be among `deals`."""
    t = _thresholds(workspace_id)
    zones = _zones()
    stuck = stuck_on(workspace_id)

    def today(owner: str | None):
        return today_rules.day_of(now, zones.get(owner or "") or today_rules.DEFAULT_ZONE)

    activity = facts.get("activity") or {}
    with_open_task = set(facts.get("open_task_companies") or [])

    computed: dict[str, list[dict[str, Any]]] = {table: [] for table in store.FLAG_COLUMNS}
    deal_flags: dict[str, dict[str, Any]] = {}
    for deal in deals:
        out = rules.for_deal(
            deal,
            now=now,
            today=today(deal.get("owner_id")),
            t=t,
            last_activity=activity.get(deal["id"]),
            messages=_mail(facts, "opportunity", deal["id"]),
            stuck=stuck,
        )
        deal_flags[deal["id"]] = {**deal, **out}
        computed["crm_opportunities"].append({"id": deal["id"], **out})
    for company in companies:
        open_deals = [
            d
            for d in deal_flags.values()
            if d.get("company_id") == company["id"] and d.get("stage") in rules.OPEN_STAGES
        ]
        out = rules.for_company(
            company,
            now=now,
            t=t,
            has_open_task=company["id"] in with_open_task,
            open_deals=open_deals,
            messages=_mail(facts, "company", company["id"]),
        )
        computed["crm_companies"].append({"id": company["id"], **out})
    for task in tasks:
        out = rules.for_task(task, today=today(task.get("assignee_id")))
        computed["tasks"].append({"id": task["id"], **out})
    for person in people:
        computed["crm_people"].append(
            {
                "id": person["id"],
                **rules.for_person(
                    person, now=now, t=t, messages=_mail(facts, "person", person["id"])
                ),
            }
        )

    changed = {}
    for table, rows in computed.items():
        if rows:
            changed[table] = store.set_flags(workspace_id, table, rows, now)
    return {
        "deals": len(deals),
        "companies": len(companies),
        "tasks": len(tasks),
        "people": len(people),
        "changed": sum(changed.values()),
    }


def recompute_workspace(workspace_id: str, now: datetime | None = None) -> dict[str, int]:
    """Automation 4 — every flagged table of the workspace. Deals and tasks
    are those open, or still flagged (so a closed one is cleared); people
    only while they can have a flag: those with stored email (S10), and
    those still flagged."""
    now = now or _now()
    deals = store.flag_rows(workspace_id, "crm_opportunities", open_or_flagged=True)
    companies = store.flag_rows(workspace_id, "crm_companies")
    tasks = store.flag_rows(workspace_id, "tasks", open_or_flagged=True)
    facts = store.flag_facts(workspace_id, None, None, None)
    people = {p["id"]: p for p in store.flag_rows(workspace_id, "crm_people", flagged_only=True)}
    with_mail = sorted(set((facts.get("emails") or {}).get("person") or {}) - set(people))
    if with_mail:
        people.update(
            {p["id"]: p for p in store.flag_rows(workspace_id, "crm_people", ids=with_mail)}
        )
    return _compute(
        workspace_id,
        deals=deals,
        companies=companies,
        tasks=tasks,
        people=list(people.values()),
        facts=facts,
        now=now,
    )


def recompute(
    workspace_id: str,
    *,
    companies: set[str] | None = None,
    people: set[str] | None = None,
    deals: set[str] | None = None,
    tasks: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Some records and what their flags feed: a deal's company, and that
    company's other open deals (its level is the worst of them)."""
    now = now or _now()
    deal_rows = store.flag_rows(workspace_id, "crm_opportunities", ids=sorted(deals or ()))
    company_ids = set(companies or ()) | {d["company_id"] for d in deal_rows if d.get("company_id")}
    company_rows = store.flag_rows(workspace_id, "crm_companies", ids=sorted(company_ids))
    if company_rows:
        known = {d["id"] for d in deal_rows}
        deal_rows += [
            d
            for d in store.flag_rows(
                workspace_id, "crm_opportunities", company_ids=[c["id"] for c in company_rows]
            )
            if d["id"] not in known
        ]
    task_rows = store.flag_rows(workspace_id, "tasks", ids=sorted(tasks or ()))
    people_rows = store.flag_rows(workspace_id, "crm_people", ids=sorted(people or ()))
    if not (deal_rows or company_rows or task_rows or people_rows):
        return {"changed": 0}
    facts = (
        store.flag_facts(
            workspace_id,
            [d["id"] for d in deal_rows],
            [c["id"] for c in company_rows],
            [p["id"] for p in people_rows],
        )
        if deal_rows or company_rows or people_rows
        else {}
    )
    return _compute(
        workspace_id,
        deals=deal_rows,
        companies=company_rows,
        tasks=task_rows,
        people=people_rows,
        facts=facts,
        now=now,
    )


def linked_records(links: list[dict[str, Any]]) -> dict[str, set[str]]:
    """The deals and companies a task's or note's links name."""
    out: dict[str, set[str]] = {"deals": set(), "companies": set(), "people": set()}
    key = {"opportunity": "deals", "company": "companies", "person": "people"}
    for link in links:
        if link.get("record_type") in key:
            out[key[link["record_type"]]].add(link["record_id"])
    return out


def after_write(
    workspace_id: str,
    *,
    companies: set[str] | list[str] = (),
    people: set[str] | list[str] = (),
    deals: set[str] | list[str] = (),
    tasks: set[str] | list[str] = (),
) -> None:
    """FR-CRM-FLAG-02 — recompute after a write. The write has committed:
    a failure here is logged, never raised, and the next job catches up."""
    if not (companies or people or deals or tasks):
        return
    try:
        recompute(
            workspace_id,
            companies={c for c in companies if c},
            people={p for p in people if p},
            deals={d for d in deals if d},
            tasks={t for t in tasks if t},
        )
    except Exception:
        logger.exception('{"event": "crm_flags_after_write_failed"}')


# ---------------------------------------------------------------------------
# Spec 009 — R-2 Stuck in stage (FR-CRMAI-RULE-03, FR-CRMAI-CHG-06, Q-29)
# ---------------------------------------------------------------------------


def stuck_on(workspace_id: str) -> bool:
    """The Stuck in stage flag is computed only while R-2's switch and phase
    3 are on; with either off it is absent and every level is as before."""
    return notifications.automation_on(
        workspace_id, rule_defs.SWITCHES["R-2"]
    ) and ai_rules.phase_on(store.ai_setting(workspace_id), rule_defs.PHASE)
