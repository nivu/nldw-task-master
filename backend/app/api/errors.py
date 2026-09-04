"""RFC 7807 problem details.

Constitution, Observability: all errors returned to clients follow
`type`/`title`/`status`/`detail`, and stack traces never appear in a response.

`detail` is the one field written for a human. Everything raised through
`problem()` has passed a rule the person can act on — "Casual leave must be
requested before the day itself" — so the frontend renders it verbatim (A-17).
Anything that has *not* been written for a human is turned into a generic
message here, and the real cause goes to the log instead.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("nldw-task-master")

TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
}


class ProblemDetail(Exception):
    """An error with a message that is safe to show the requester."""

    def __init__(self, status: int, detail: str, *, title: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.title = title or TITLES.get(status, "Error")


def problem_response(request: Request, status: int, detail: str, title: str | None = None):
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": title or TITLES.get(status, "Error"),
            "status": status,
            "detail": detail,
            "instance": str(request.url.path),
        },
        media_type="application/problem+json",
    )


async def problem_handler(request: Request, exc: ProblemDetail):
    return problem_response(request, exc.status, exc.detail, exc.title)


async def unhandled_handler(request: Request, exc: Exception):
    """The last line of defence.

    An exception that reached here was not anticipated, so its message may
    contain anything — a Postgres error naming a column, a stack of internal
    calls. None of that goes to the client. It goes to the log with the path
    that produced it, and the caller gets a sentence.
    """
    logger.exception(
        '{"event": "unhandled_exception", "path": "%s", "method": "%s"}',
        request.url.path,
        request.method,
    )
    return problem_response(
        request,
        500,
        "Something went wrong on our side. The failure has been logged.",
    )
