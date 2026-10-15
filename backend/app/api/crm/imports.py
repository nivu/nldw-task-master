"""Imports — spec 007 contracts §2.9; FR-CRM-MIG-01 to -08, FR-CRM-CHG-08,
FR-CRM-MCP-08.

Admins only. A file arrives as a multipart upload (`file`, with optional
`format`, `object` and, for the real run, `fixup_next_step`; a CSV of one
object also `mapping`, `mapping_id`, `mode` and `actions`, S11b) through the
portal's proxy, as JSON `{format, content, object?, fixup_next_step?}`
from `crm_import`, or as a Twenty JSON export posted as it is (the real run
then takes `?fixup_next_step=true`). Either way it is held to 4 MB here as well as in the
proxy: the body is read as a stream with a running count, so a missing or
wrong `Content-Length` changes nothing. `services/crm/importer.py` does the
rest, off the event loop.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.api.crm.deps import CrmAdminDep
from app.api.errors import ProblemDetail
from app.domain.crm.importer import MAX_BYTES
from app.schemas.crm import CsvMappingIn, ImportIn
from app.services.crm import importer as service

router = APIRouter(prefix="/crm/imports", tags=["crm"])

TOO_LARGE = "Content Too Large"
#: Room for the multipart envelope and the other fields around a 4 MB file;
#: JSON may escape the text, so it gets twice the room. The file itself is
#: then held to exactly 4 MB.
_MULTIPART_ROOM = 64 * 1024
_TRUE = ("1", "true", "on", "yes")


async def _body(request: Request, limit: int) -> bytes:
    """The body, refused 413 the moment it passes `limit` bytes."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ProblemDetail(413, service.TOO_BIG, title=TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ProblemDetail(413, service.TOO_BIG, title=TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _is_export(body: bytes) -> bool:
    """A JSON object that is an export itself rather than `{format,
    content}`: it has no `content` key."""
    try:
        parsed = json.loads(body or b"{}")
    except ValueError:
        return False
    return isinstance(parsed, dict) and "content" not in parsed and "format" not in parsed


async def _source(request: Request) -> tuple[service.Source, bool]:
    """The file and the fix-up choice, from an upload or from JSON."""
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media == "multipart/form-data":
        body = await _body(request, MAX_BYTES + _MULTIPART_ROOM)

        async def replay() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        form = await Request(request.scope, replay).form(max_files=1, max_fields=10)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ProblemDetail(422, "Attach the export as the form field “file”.")
        content = await upload.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ProblemDetail(413, service.TOO_BIG, title=TOO_LARGE)
        name = upload.filename or ""
        fmt = str(form.get("format") or "")
        if not fmt:
            fmt = "twenty_json" if name.lower().endswith(".json") else "twenty_csv"
        if fmt not in ("twenty_json", "twenty_csv", "csv"):
            raise ProblemDetail(422, "The format is twenty_json, twenty_csv or csv.")
        source = service.Source(
            content,
            fmt,
            str(form.get("object") or "") or None,
            name,
            **_csv_fields(form),
        )
        return source, str(form.get("fixup_next_step") or "").lower() in _TRUE
    if media not in ("application/json", ""):
        raise ProblemDetail(415, "Send the file as multipart/form-data or as JSON.")
    body = await _body(request, 2 * MAX_BYTES + _MULTIPART_ROOM)
    if _is_export(body):
        # A Twenty JSON export posted as it is (quickstart §3 S8); the real
        # run takes the fix-up as `?fixup_next_step=true`.
        if len(body) > MAX_BYTES:
            raise ProblemDetail(413, service.TOO_BIG, title=TOO_LARGE)
        fixup = request.query_params.get("fixup_next_step", "").lower() in _TRUE
        return service.Source(body, "twenty_json"), fixup
    try:
        payload = ImportIn.model_validate_json(body or b"{}")
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise ProblemDetail(422, f"{where or 'The request'}: {first['msg']}.") from None
    content = payload.content.encode("utf-8")
    if len(content) > MAX_BYTES:
        raise ProblemDetail(413, service.TOO_BIG, title=TOO_LARGE)
    source = service.Source(
        content,
        payload.format,
        payload.object,
        mapping=payload.mapping,
        mapping_id=payload.mapping_id,
        mode=payload.mode,
        actions=payload.actions,
    )
    return source, payload.fixup_next_step


def _csv_fields(form: Any) -> dict[str, Any]:
    """A CSV upload's other form fields (S11b): `mapping` and `actions` as
    JSON text, `mapping_id`, `mode`."""
    out: dict[str, Any] = {}
    for name in ("mapping", "actions"):
        text = form.get(name)
        if not text:
            continue
        try:
            value = json.loads(str(text))
        except ValueError:
            value = None
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in value.items()
        ):
            raise ProblemDetail(422, f"The form field “{name}” is a JSON object of text.")
        out[name] = value
    if form.get("mapping_id"):
        out["mapping_id"] = str(form.get("mapping_id"))
    mode = str(form.get("mode") or "")
    if mode:
        if mode not in ("skip", "update", "create"):
            raise ProblemDetail(422, "The mode is skip, update or create.")
        out["mode"] = mode
    return out


@router.post("/dry-run")
async def dry_run(request: Request, ctx: CrmAdminDep) -> dict:
    """`{run_id, counts, lead_stages, conflicts, attachments,
    fixup_candidates, blocking}`. Writes nothing but the run."""
    source, _ = await _source(request)
    return await run_in_threadpool(service.dry_run, ctx, source)


@router.post("/{run_id}/apply")
async def apply(run_id: str, request: Request, ctx: CrmAdminDep) -> dict:
    """`{run_id, counts, fixups_used}`. 409 if the file is not the dry run's
    or the dry run is more than 24 hours old; 422 while it lists something
    to fix first."""
    source, fixup = await _source(request)
    return await run_in_threadpool(service.apply, ctx, run_id, source, fixup=fixup)


@router.get("")
def recent(ctx: CrmAdminDep) -> list[dict]:
    """Recent runs with their counts; never the file or its rows."""
    return service.runs(ctx)


# ---------------------------------------------------------------------------
# Saved CSV mappings — contracts §2.10 (S11b, FR-CRM-IO-01)
# ---------------------------------------------------------------------------

mappings_router = APIRouter(prefix="/crm/csv-mappings", tags=["crm"])


@mappings_router.get("")
def list_mappings(
    ctx: CrmAdminDep, object: Annotated[str | None, Query(max_length=40)] = None
) -> list[dict]:
    return service.list_mappings(ctx, object)


@mappings_router.post("", status_code=201)
def save_mapping(payload: CsvMappingIn, ctx: CrmAdminDep) -> dict:
    """Saving under a name the object's mappings already have replaces it."""
    return service.save_mapping(ctx, payload.object, payload.name, payload.mapping)


@mappings_router.delete("/{mapping_id}")
def delete_mapping(mapping_id: str, ctx: CrmAdminDep) -> dict:
    return service.delete_mapping(ctx, mapping_id)
