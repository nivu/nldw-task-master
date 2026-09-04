"""Backend entry point — FastAPI application."""

import asyncio
import logging
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import ProblemDetail, problem_handler, problem_response, unhandled_handler
from app.config import settings

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("nldw-task-master")

# The Supabase client logs every outbound request through httpx at INFO, which
# drowns this application's own structured lines and leaks query strings —
# including row ids — into the log. Constitution, Observability: log lines must
# be parseable by standard aggregation tools, and these are not ours to shape.
for _noisy in ("httpx", "httpcore", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Lifespan: optionally run a Celery worker inside the API process.
# Off by default — production runs a dedicated worker service. See
# Settings.RUN_EMBEDDED_WORKER.
# ---------------------------------------------------------------------------
_celery_proc: subprocess.Popen | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _celery_proc

    if not settings.RUN_EMBEDDED_WORKER:
        logger.info(
            '{"event": "embedded_worker_disabled", '
            '"detail": "tasks are handled by the dedicated worker service"}'
        )
        yield
        return

    _celery_proc = subprocess.Popen(  # noqa: ASYNC220 - must outlive this coroutine
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker",
            "worker",
            "--loglevel=info",
            "--concurrency=2",
        ],
        stdout=None,  # inherit so logs appear in the same terminal
        stderr=None,
    )
    logger.info("Celery worker started (pid=%d)", _celery_proc.pid)
    try:
        yield
    finally:
        if _celery_proc and _celery_proc.poll() is None:
            _celery_proc.terminate()
            # .wait() blocks; keep it off the event loop so shutdown stays responsive.
            await asyncio.to_thread(_shutdown_celery, _celery_proc)
            logger.info("Celery worker stopped")


def _shutdown_celery(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Nunnari Employee Portal API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------
_allowed_origins = list(
    {
        settings.FRONTEND_URL.rstrip("/"),
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Structured request logging middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Any) -> Response:
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    # Constitution, Observability: log the authenticated user's ID and never
    # their personal data. request.state.user_id is set by the auth dependency.
    logger.info(
        '{"method": "%s", "path": "%s", "status_code": %d, "duration_ms": %.2f, "user_id": "%s"}',
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        getattr(request.state, "user_id", "-"),
    )
    return response


# ---------------------------------------------------------------------------
# Error handling — RFC 7807 everywhere (constitution, Observability)
#
# Registered for three cases so that no route can accidentally return a
# differently-shaped error: our own refusals, request validation failures, and
# anything unforeseen.
# ---------------------------------------------------------------------------
app.add_exception_handler(ProblemDetail, problem_handler)
app.add_exception_handler(Exception, unhandled_handler)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """Turn Pydantic's error list into one sentence a person can act on."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "That request was not valid.")
    detail = f"{field}: {message}" if field else message
    return problem_response(request, 422, detail, "Unprocessable Entity")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Mount API routers under /api/v1
#
# Add each new router module to this list. Routers are imported by path so a
# broken module fails loudly at startup rather than silently going unmounted.
# ---------------------------------------------------------------------------
def _mount_routers() -> None:
    import importlib

    router_modules = [
        ("app.api.me", "router"),
        ("app.api.bookings", "router"),
        ("app.api.team", "router"),
        ("app.api.admin", "router"),
        ("app.api.slack", "router"),
    ]

    for module_path, attr_name in router_modules:
        module = importlib.import_module(module_path)
        router = getattr(module, attr_name)
        app.include_router(router, prefix="/api/v1")
        logger.info("Mounted router: %s", module_path)


_mount_routers()
