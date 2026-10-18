"""Tasks' routes — spec 008 contracts §1, §2. Mounted by main.py under
/api/v1, so every path here starts /api/v1/tasks.

Each route depends on a guard from `deps.py` (`TasksDep` for phase 1, and so
on), which runs on `any_account`: staff and CRM-only accounts alike
(FR-TASK-CHG-02).

Route order (contracts §1): FastAPI matches in registration order and does
not fall through, so every other router module is listed before the
`tasks.py` router, which is always last; inside `tasks.py` the fixed paths
come before `/tasks/{id}`.
"""

from __future__ import annotations

from app.api.tasks import lifeos, tasks

ROUTERS = [
    lifeos.router,
    # Always last: its `/tasks/{id}` routes come after every fixed path.
    tasks.router,
]
