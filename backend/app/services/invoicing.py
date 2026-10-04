"""Client invoicing tracker — spec 006 FR-MILE-05..07. Every milestone on
every project, archived ones included: a finished project can still be owed."""

from __future__ import annotations

from typing import Any

from app.domain import invoicing as rules
from app.domain.calendar import today_in_company_tz
from app.services import settings_store
from app.services import supabase as db


def listing(status: str | None = None) -> dict[str, Any]:
    today = today_in_company_tz()
    terms = settings_store.invoice_payment_terms_days()
    projects = {p["id"]: p for p in db.list_projects(include_archived=True)}
    milestones = [m for m in db.list_milestones() if m["project_id"] in projects]

    invoices = []
    for m in milestones:
        project = projects[m["project_id"]]
        pay_by = rules.payment_due_on(m, terms)
        invoices.append(
            {
                "id": m["id"],
                "project_id": m["project_id"],
                "project_name": project["name"],
                "client": project.get("client"),
                "category": project.get("category"),
                "is_archived": project.get("is_archived", False),
                "name": m["name"],
                "amount": str(m["amount"]),
                "due_on": m["due_on"],
                "invoice_number": m.get("invoice_number"),
                "invoiced_on": m.get("invoiced_on"),
                "payment_due_on": None if pay_by is None else pay_by.isoformat(),
                "paid_on": m.get("paid_on"),
                "status": rules.invoice_status(m, today, terms),
                "days_overdue": rules.days_overdue(m, today, terms),
            }
        )

    return {
        "currency": str(settings_store.get("currency_code", "INR")),
        "today": today.isoformat(),
        "payment_terms_days": terms,
        # Totals are over every milestone, whatever the status filter shows.
        "totals": {k: str(v) for k, v in rules.totals(milestones, today, terms).items()},
        "invoices": [i for i in invoices if status is None or i["status"] == status],
    }
