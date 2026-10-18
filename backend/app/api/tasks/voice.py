"""Voice capture, phase 4 — spec 008 contracts §2 voice routes;
FR-TASK-VOICE-01 to -05; Journey 4.10.

HTTP only: the phase-4 guard (`TasksDep4`) and `services/tasks/voice`. Web
only (contracts §5: no tool). The note arrives as a multipart upload
(`file`) through the portal's proxy and is held to 4 MB here as well: the
body is read as a stream with a running count, so a missing or wrong
`Content-Length` changes nothing, and it stays in memory, never spooled to
disk. `upload_id` is typed `UUID`, and this router is listed in `ROUTERS`
before the `tasks.py` router, so `/tasks/voice...` is never read as
`/tasks/{id}` (contracts §1 "Route order").
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.errors import ProblemDetail
from app.api.tasks.deps import TasksDep4
from app.schemas.tasks import VoiceConfirmIn, VoiceDropIn
from app.services.tasks import voice

router = APIRouter(prefix="/tasks/voice", tags=["tasks"])

#: Room for the multipart envelope around a 4 MB note; the file itself is
#: then held to exactly 4 MB.
_MULTIPART_ROOM = 64 * 1024


class _InMemoryParser(MultiPartParser):
    """Starlette's parser writes a file part past 1 MB to a temporary file;
    this one keeps the whole body in memory."""

    spool_max_size = voice.MAX_BYTES + _MULTIPART_ROOM


async def _form(request: Request) -> FormData:
    """The multipart form, refused 413 the moment the body passes 4 MB and
    its envelope."""
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "multipart/form-data":
        raise ProblemDetail(415, "Send the voice note as multipart/form-data.")
    limit = voice.MAX_BYTES + _MULTIPART_ROOM
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise voice.too_big()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise voice.too_big()
        chunks.append(chunk)
    body = b"".join(chunks)

    async def stream():
        yield body

    try:
        return await _InMemoryParser(request.headers, stream(), max_files=1, max_fields=1).parse()
    except MultiPartException as exc:
        raise ProblemDetail(400, exc.message) from None


@router.post("", status_code=202)
async def upload(request: Request, actor: TasksDep4) -> dict[str, Any]:
    """`{upload_id, status: "uploaded"}`; the note is transcribed in the
    background. 503 while voice capture is not set up; 413 over 4 MB."""
    voice.configured()
    form = await _form(request)
    try:
        part = form.get("file")
        if not isinstance(part, UploadFile):
            raise ProblemDetail(422, "Attach the voice note as the form field “file”.")
        content = await part.read(voice.MAX_BYTES + 1)
        content_type = part.content_type
    finally:
        await form.close()
    return await run_in_threadpool(voice.upload, actor, content, content_type)


@router.get("/{upload_id}")
def read(actor: TasksDep4, upload_id: UUID) -> dict[str, Any]:
    """`{status, candidates, error}`: the caller's own note only."""
    return voice.read(actor, str(upload_id))


@router.post("/{upload_id}/confirm")
def confirm(actor: TasksDep4, upload_id: UUID, body: VoiceConfirmIn) -> dict[str, Any]:
    """Saves one candidate (source `voice`); the last one clears the
    transcript."""
    return voice.confirm(actor, str(upload_id), body.index, body.draft)


@router.post("/{upload_id}/drop")
def drop(actor: TasksDep4, upload_id: UUID, body: VoiceDropIn) -> dict[str, bool]:
    """Drops one candidate; nothing is saved."""
    return voice.drop(actor, str(upload_id), body.index)
