"""The sales CRM's routes — spec 007 contracts §2. Mounted by main.py under
/api/v1, so every path here starts /api/v1/crm/.

Each route depends on a guard from `deps.py` (or, for the routes about the
person rather than one workspace, on `AnyAccountDep`), never on TVA's
`current_user`: a CRM-only seller is the CRM's main user (FR-CRM-CHG-01).
"""

from __future__ import annotations

from app.api.crm import audit, settings, workspaces

ROUTERS = [workspaces.router, settings.router, audit.router]
