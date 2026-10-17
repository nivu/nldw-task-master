"""A task refusal — spec 008 contracts §4 step 8; plan §4 CT-2.

A subclass of the CRM's refusal, so `main.py`'s existing handler renders it
as RFC 7807 with no new handler. This and `service.py` are the only
`domain/tasks` modules that import from outside the domain (CT-2).
"""

from __future__ import annotations

from app.services.crm import CrmRefused


class TaskRefused(CrmRefused):
    """A task refusal with a sentence safe to show the person: 422 for a rule,
    404 for a task, record, project or parent the caller may not see."""
