"""The Life OS import — spec 008 contracts §1; FR-TASK-IMP-01 to -08; TP-10,
TP-17.

Owner only, web only (no tool, TP-10). The file arrives as a multipart
upload (`file`, and on the dry run an optional `mapping`: JSON
`{owners: {value: email | null}}`) through the portal's proxy, and is held to
4 MB here as well: the body is read as a stream with a running count, so a
missing or wrong `Content-Length` changes nothing. The file stays in memory
(never spooled to disk) and the form is closed once read (FR-TASK-IMP-08).
`services/tasks/lifeos.py` does the rest, off the event loop.

`GET /tasks/import` is not built: the spec does not ask for it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.errors import ProblemDetail
from app.api.tasks.deps import TasksDep
from app.domain.tasks.lifeos import MAX_BYTES
from app.schemas.tasks import LifeOsMapping
from app.services.tasks import lifeos

router = APIRouter(prefix="/tasks/import", tags=["tasks"])

TOO_LARGE = "Content Too Large"
#: Room for the multipart envelope and the mapping around a 4 MB file; the
#: file itself is then held to exactly 4 MB.
_MULTIPART_ROOM = 64 * 1024


class _InMemoryParser(MultiPartParser):
    """Starlette's parser writes a file part past 1 MB to a temporary file;
    this one keeps the whole body in memory."""

    spool_max_size = MAX_BYTES + _MULTIPART_ROOM


async def _form(request: Request) -> FormData:
    """The multipart form, refused 413 the moment the body passes 4 MB and
    its envelope, or the file passes 4 MB."""
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "multipart/form-data":
        raise ProblemDetail(415, "Send the file as multipart/form-data.")
    limit = MAX_BYTES + _MULTIPART_ROOM
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ProblemDetail(413, lifeos.TOO_BIG, title=TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ProblemDetail(413, lifeos.TOO_BIG, title=TOO_LARGE)
        chunks.append(chunk)
    body = b"".join(chunks)

    async def stream():
        yield body

    try:
        return await _InMemoryParser(request.headers, stream(), max_files=1, max_fields=5).parse()
    except MultiPartException as exc:
        raise ProblemDetail(400, exc.message) from None


async def _file(form: FormData) -> bytes:
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise ProblemDetail(422, "Attach the CSV as the form field “file”.")
    content = await upload.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ProblemDetail(413, lifeos.TOO_BIG, title=TOO_LARGE)
    return content


def _mapping(form: FormData) -> LifeOsMapping | None:
    """The owner's edits to the owner mapping; status and priority are not
    editable, so any other key is refused (FR-TASK-IMP-04)."""
    text = form.get("mapping")
    if not text:
        return None
    try:
        return LifeOsMapping.model_validate_json(str(text))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise ProblemDetail(
            422, f"mapping{'.' + where if where else ''}: {first['msg']}."
        ) from None


@router.post("/dry-run")
async def dry_run(request: Request, actor: TasksDep) -> dict[str, Any]:
    """Counts, the proposed owner mapping, every unmapped value, ambiguous
    dates and what blocks the real run, with the `run_id` it needs. Writes
    only the run record."""
    lifeos.owner_only(actor)
    form = await _form(request)
    try:
        content = await _file(form)
        mapping = _mapping(form)
    finally:
        await form.close()
    return await run_in_threadpool(lifeos.dry_run, actor, content, mapping)


@router.post("/{run_id}/apply")
async def apply(run_id: UUID, request: Request, actor: TasksDep) -> dict[str, Any]:
    """`{run_id, counts}`. 409 another file or a dry run more than 24 hours
    old; 422 while anything is unmapped; 404 another person's run."""
    lifeos.owner_only(actor)
    form = await _form(request)
    try:
        content = await _file(form)
    finally:
        await form.close()
    return await run_in_threadpool(lifeos.apply, actor, str(run_id), content)
