"""The TVA side of a Labs deal — spec 007 FR-CRM-TVA-01 to -18,
FR-CRM-PIPE-09, -13, FR-CRM-WS-05; owner decisions D5, D8, D14, D15;
contracts §2.6; plan §5.6.

What a Labs deal's move does to its TVA project, worked out from the deal,
its project and the person moving it. The result is the `project` action of
the plan `crm_deal_transition` (033) writes in the same transaction as the
move (FR-CRM-PIPE-15):

| Move | Action |
|---|---|
| into Proposal or Negotiation, no project | `create` (or `link`, by a TVA manager) |
| out of Closed Lost | `unarchive_unrelease` |
| into Closed Lost | `archive_release` |
| out of Closed Won (admin reopen) | `retentative` |
| any other change of probability | `sync_probability` |

The project is always the deal's own, found by `source_opportunity_id`.
Linking a deal to an existing project is for a staff TVA manager or admin
only: a link lets a later Closed Lost archive that project and release its
people, so anyone else is told to ask one (FR-CRM-TVA-05). Academy deals
never reach here (FR-CRM-WS-05).

The other way round, `ProjectLinks` is what TVA's project routes show of the
CRM: that a deal manages a project, and its company and deal to Labs members
only (FR-CRM-TVA-02, FR-CRM-CHG-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain import timesheets as tva_rules
from app.domain.crm import handoff
from app.services import supabase as db
from app.services.crm import CrmRefused, store

#: FR-CRM-TVA-04 — the stages a deal has its tentative project at.
PROJECT_STAGES = ("proposal", "negotiation")
#: FR-CRM-TVA-05, contracts §2.6.
ASK_A_MANAGER = "Ask a TVA manager to link it."
HAS_PROJECT = "This deal has a TVA project; mark it Closed Lost instead."
#: Ids per read by id: they travel in the URL (as `records.ID_BATCH`).
ID_BATCH = 100


def may_link(user: Any) -> bool:
    """FR-CRM-TVA-05, -18 — a staff account with TVA role manager or admin."""
    return handoff.can_set_revenue(getattr(user, "profile", None))


def project_of(deal_id: str) -> dict[str, Any] | None:
    return db.get_project_by_source_opportunity(deal_id)


def is_linkable(project: dict[str, Any], status: str = "tentative") -> bool:
    """FR-CRM-TVA-05, -18 — unlinked, not archived, and of the status the
    deal's state calls for."""
    return (
        not project.get("source_opportunity_id")
        and not project.get("is_archived")
        and project.get("status", "confirmed") == status
    )


def clash(name: str, *, exclude: str | None = None) -> dict[str, Any] | None:
    """FR-CRM-TVA-05 — the project, archived included, already called `name`
    (`projects_name_unique`, 008: ignoring case and outer spaces)."""
    found = db.find_project_by_name(name)
    return found if found and found["id"] != exclude else None


def clash_sentence(project: dict[str, Any], *, can_link: bool) -> str:
    """Contracts §2.6 — the 409 names the clash and the ways on."""
    said = f"A project called “{project['name']}” already exists."
    if can_link:
        return f"{said} Rename the deal or link it to that project."
    return f"{said} Rename the deal, or ask a TVA manager to link it."


# ---------------------------------------------------------------------------
# What the CRM shows of a project — FR-CRM-TVA-02, FR-CRM-ACC-03
# ---------------------------------------------------------------------------


def block(project: dict[str, Any], phases: list[dict[str, Any]]) -> dict[str, Any]:
    """A linked project as every CRM role sees it, Viewers and CRM-only
    accounts included: its name, status, archived state and phase dates.
    Never an amount (FR-CRM-TVA-17)."""
    return {
        "id": project["id"],
        "name": project["name"],
        "status": project.get("status", "confirmed"),
        "is_archived": bool(project.get("is_archived")),
        "phases": [
            {
                "phase": p["phase"],
                "label": tva_rules.PHASE_LABELS.get(p["phase"], p["phase"]),
                "starts_on": str(p["starts_on"]),
                "ends_on": str(p["ends_on"]),
            }
            for p in sorted(phases, key=lambda p: str(p["starts_on"]))
            if p["project_id"] == project["id"]
        ],
    }


def blocks(
    *, opportunity_ids: list[str] | None = None, company_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """The linked projects of some deals or companies, with their phases, in
    two queries per `ID_BATCH` ids. Each block keeps the link it was found by."""
    found: dict[str, dict[str, Any]] = {}
    for column, ids in (("opportunity_ids", opportunity_ids), ("company_ids", company_ids)):
        wanted = list(dict.fromkeys(ids or []))
        for i in range(0, len(wanted), ID_BATCH):
            for p in db.list_crm_projects(**{column: wanted[i : i + ID_BATCH]}):
                found.setdefault(p["id"], p)
    projects = sorted(found.values(), key=lambda p: p["name"])
    project_ids = [p["id"] for p in projects]
    phases: list[dict[str, Any]] = []
    for i in range(0, len(project_ids), ID_BATCH):
        phases += db.list_project_phases(project_ids[i : i + ID_BATCH])
    return [
        {
            **block(p, phases),
            "source_opportunity_id": p.get("source_opportunity_id"),
            "crm_company_id": p.get("crm_company_id"),
        }
        for p in projects
    ]


def present(project: dict[str, Any] | None) -> dict[str, Any] | None:
    """One project's block, read with its phases."""
    if not project:
        return None
    return block(project, db.list_project_phases([project["id"]]))


# ---------------------------------------------------------------------------
# A move's project action — FR-CRM-TVA-04 to -06, -12 to -14, PIPE-13
# ---------------------------------------------------------------------------


@dataclass
class Effect:
    """What a Labs move does to its project: the plan's `project`, the
    preview's `would.project`, or why the move cannot be made."""

    action: dict[str, Any] | None = None
    would: dict[str, Any] | None = None
    refusal: CrmRefused | None = None


def effect(
    *,
    user: Any,
    deal: dict[str, Any],
    target: str,
    probability: int,
    project_name: str | None = None,
    link_project_id: str | None = None,
    lost_reason_label: str | None = None,
) -> Effect:
    """The project action of a Labs deal's move from its stage to `target`
    (never Closed Won, which goes through the handoff)."""
    current = deal["stage"]
    project = project_of(deal["id"])

    if current == "closed_won":
        # FR-CRM-PIPE-13: an admin reopen; refused while the project has
        # logged time or an invoiced milestone.
        if project is None:
            return Effect()
        why = tva_rules.make_tentative_refusal(
            project_name=project["name"],
            has_time_entries=db.project_has_time_entries(project["id"]),
            has_invoices=any(
                m.get("invoiced_on") for m in db.list_project_milestones(project["id"])
            ),
        )
        would = _would("retentative", project, probability)
        if why:
            return Effect(None, would, CrmRefused(422, why))
        return Effect({"action": "retentative", "probability": probability}, would)

    if target == "closed_lost":
        # FR-CRM-TVA-12, -13: archive the project and release its people.
        if project is None:
            return Effect()
        reason = f"Deal lost: {lost_reason_label}" if lost_reason_label else "Deal lost"
        return Effect(
            {"action": "archive_release", "reason": reason, "probability": probability},
            _would("archive_release", project, probability),
        )

    if current == "closed_lost" and project is not None:
        # FR-CRM-TVA-14: undo exactly what the loss did.
        return Effect(
            {"action": "unarchive_unrelease", "probability": probability},
            _would("unarchive_unrelease", project, probability),
        )

    if project is None:
        if target not in PROJECT_STAGES:
            return Effect()
        return _create_or_link(
            user=user,
            deal=deal,
            probability=probability,
            project_name=project_name,
            link_project_id=link_project_id,
        )

    # FR-CRM-TVA-06: while open, the tentative project follows probability.
    if project.get("status") == "tentative" and project.get("probability") != probability:
        return Effect(
            {"action": "sync_probability", "probability": probability},
            _would("sync_probability", project, probability),
        )
    return Effect()


def _create_or_link(
    *,
    user: Any,
    deal: dict[str, Any],
    probability: int,
    project_name: str | None,
    link_project_id: str | None,
) -> Effect:
    """FR-CRM-TVA-04, -05: a deal entering Proposal (or an imported one moved
    into Negotiation) without a project gets a tentative one, or is linked to
    an existing one by a TVA manager."""
    can_link = may_link(user)
    if link_project_id:
        if not can_link:
            return Effect(refusal=CrmRefused(409, ASK_A_MANAGER))
        target = next(
            (p for p in db.list_linkable_projects("tentative") if p["id"] == link_project_id),
            None,
        )
        if target is None:
            return Effect(
                refusal=CrmRefused(
                    409,
                    "That project cannot be linked: only an unlinked, unarchived tentative "
                    "project can.",
                )
            )
        return Effect(
            {"action": "link", "project_id": target["id"], "probability": probability},
            {
                "action": "link",
                "name": target["name"],
                "category": None,
                "probability": probability,
                "clash": None,
            },
        )

    name = (project_name or deal["name"]).strip()
    category = deal.get("project_category") or "client"
    found = clash(name)
    would = {
        "action": "create",
        "name": name,
        "category": category,
        "probability": probability,
        "clash": (
            {
                "project_id": found["id"],
                "name": found["name"],
                "linkable": can_link and is_linkable(found),
            }
            if found
            else None
        ),
    }
    if found:
        return Effect(None, would, CrmRefused(409, clash_sentence(found, can_link=can_link)))
    return Effect(
        {"action": "create", "name": name, "category": category, "probability": probability},
        would,
    )


def _would(action: str, project: dict[str, Any], probability: int) -> dict[str, Any]:
    return {
        "action": action,
        "name": project["name"],
        "category": project.get("category"),
        "probability": probability,
        "clash": None,
    }


# ---------------------------------------------------------------------------
# What TVA shows of the CRM — FR-CRM-TVA-02, FR-CRM-CHG-05
# ---------------------------------------------------------------------------


class ProjectLinks:
    """Spec 007 FR-CRM-TVA-02, FR-CRM-CHG-05 — what TVA shows of the CRM on a
    project: whether a deal manages it, and, to members of the Labs workspace
    only, its company and source deal (name, owner, won date; never an
    amount). Read once per request: the caller's Labs membership, then the
    linked deals and companies in one query each, whatever the number of
    projects."""

    def __init__(self, user: Any, projects: list[dict[str, Any]]) -> None:
        self.labs: dict[str, Any] | None = None
        self.deals: dict[str, dict[str, Any]] = {}
        self.companies: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        linked = [p for p in projects if p.get("source_opportunity_id") or p.get("crm_company_id")]
        if not linked:
            return
        self.labs = next(
            (
                m["workspace"]
                for m in store.memberships_for_user(user.id)
                if m["workspace"].get("tva_linked")
            ),
            None,
        )
        if self.labs is None:
            return
        deal_ids = sorted(
            {p["source_opportunity_id"] for p in linked if p.get("source_opportunity_id")}
        )
        company_ids = sorted({p["crm_company_id"] for p in linked if p.get("crm_company_id")})
        for ids, table, into in (
            (deal_ids, "crm_opportunities", self.deals),
            (company_ids, "crm_companies", self.companies),
        ):
            if ids:
                rows = store.list_records(
                    self.labs["id"], table, any_of={"id": ids}, deleted=None, limit=len(ids)
                )
                into.update({r["id"]: r for r in rows})
        if self.deals:
            self.names = {
                p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)
            }

    def managed_by(self, row: dict[str, Any]) -> str:
        """The refusal for a CRM-controlled field (FR-CRM-CHG-05): the deal's
        name only for a Labs member."""
        deal = self.deals.get(row.get("source_opportunity_id") or "")
        return f"Managed by CRM deal {deal['name']}." if deal else "Managed by a CRM deal."

    def block(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if self.labs is None:
            return None
        deal = self.deals.get(row.get("source_opportunity_id") or "")
        company = self.companies.get(row.get("crm_company_id") or "")
        if deal is None and company is None:
            return None
        return {
            "workspace": self.labs["key"],
            "company": {"id": company["id"], "name": company["name"]} if company else None,
            "opportunity": (
                {
                    "id": deal["id"],
                    "name": deal["name"],
                    "owner_name": self.names.get(deal.get("owner_id") or ""),
                    "won_on": deal.get("won_on"),
                }
                if deal
                else None
            ),
        }
