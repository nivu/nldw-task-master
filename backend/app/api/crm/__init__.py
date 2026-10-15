"""The sales CRM's routes — spec 007 contracts §2. Mounted by main.py under
/api/v1, so every path here starts /api/v1/crm/.

Each route depends on a guard from `deps.py` (or, for the routes about the
person rather than one workspace, on `AnyAccountDep`), never on TVA's
`current_user`: a CRM-only seller is the CRM's main user (FR-CRM-CHG-01).
The one exception is `tva_admin.py`, the TVA admin panel, which is TVA's
`AdminDep` by design (contracts §2.4).
"""

from __future__ import annotations

from app.api.crm import (
    audit,
    capacity,
    deals,
    imports,
    members,
    merge,
    records,
    search,
    settings,
    today,
    tva_admin,
    views,
    workspaces,
)

ROUTERS = [
    workspaces.router,
    members.router,
    settings.router,
    audit.router,
    tva_admin.router,
    capacity.router,
    today.router,
    search.router,
    # Before the record routes: /companies/merge-preview is not a company id.
    merge.router,
    records.router,
    deals.router,
    deals.projects_router,
    views.router,
    imports.router,
]
