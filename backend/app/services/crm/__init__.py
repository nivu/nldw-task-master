"""The sales CRM's data access and orchestration — spec 007 plan §5.2.

`store` is the only module that reads `crm_*` tables or calls the CRM's
database functions. Services gather rows, ask `app.domain.crm` to decide,
and write through one database function call, so a change and its audit row
land together (FR-CRM-AUD-06).
"""

from __future__ import annotations


class CrmRefused(Exception):
    """A CRM refusal with a sentence safe to show the person.

    Rendered as RFC 7807 by the handler `main.py` registers, exactly like the
    API's own `ProblemDetail`, so a service can refuse without importing the
    API layer.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.title: str | None = None
