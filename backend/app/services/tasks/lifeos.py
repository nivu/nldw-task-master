"""The Life OS import — spec 008 FR-TASK-IMP-01 to -08; TP-10, TP-17, TP-19;
Journey 4.1; contracts §1; data-model §2.3.

Gathers what `domain/tasks/lifeos.py` needs (the active accounts, the global
phase default, the keys an earlier run created), and writes:

- the **dry run** writes only its run record (`task_import_runs`, through
  `task_apply`): counts, the owner mapping, the fixed status and priority
  mapping as applied to the file, the file hash and a report of row numbers;
  never the file (FR-TASK-IMP-03, -08);
- the **real run** re-reads the same file (by hash), within 24 hours of its
  dry run, with every owner, status and priority value mapped and every
  mapped owner at level ≥ 1 (FR-TASK-IMP-04, TP-17), and creates each row
  whose content key is new through `create_task`, as the uploader with actor
  kind `import` and source `import` (FR-TASK-IMP-05, -06). Only the
  uploader's last dry run may be used; a run already applied answers what it
  did and creates nothing, as does a second real run of the same file. No notice is sent: an imported task has
  no workspace, and phase 2's notice skips `source = 'import'`.

Only the owner (`profiles.is_owner`) may run it (TP-10).
"""

from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Final

from app.domain.tasks import lifeos, service
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import TaskActor
from app.schemas.tasks import LifeOsMapping, TaskCreate
from app.services.tasks import store

logger = logging.getLogger("nldw-task-master")

ONLY_OWNER: Final = "Only the owner can import Life OS."
TOO_BIG: Final = "The file is larger than 4 MB."
NOT_TEXT: Final = "The file is not UTF-8 text."
NO_SUCH_RUN: Final = "No such import run."
NOT_THE_FILE: Final = "This is not the file you dry-ran; dry-run it again."
TOO_OLD: Final = "The dry run is more than 24 hours old; dry-run it again."
#: TP-17 — how long a dry run stays good for the real run.
DRY_RUN_FOR: Final = timedelta(hours=24)


def owner_only(actor: TaskActor) -> None:
    """TP-10 — 403 for anyone without `profiles.is_owner`."""
    if actor.user.profile.get("is_owner") is not True:
        raise TaskRefused(403, ONLY_OWNER)


def _importer(actor: TaskActor) -> TaskActor:
    """The uploader, audited with actor kind `import` (FR-TASK-IMP-06)."""
    return replace(actor, kind="import")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sheet(content: bytes) -> list[lifeos.SheetRow]:
    """The file's rows, or a 413 / 422."""
    if len(content) > lifeos.MAX_BYTES:
        refused = TaskRefused(413, TOO_BIG)
        refused.title = "Content Too Large"
        raise refused
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TaskRefused(422, NOT_TEXT) from None
    try:
        return lifeos.read(text)
    except ValueError as exc:
        raise TaskRefused(422, str(exc)) from None
    except csv.Error as exc:
        raise TaskRefused(422, f"The CSV could not be read: {exc}.") from None


def _plan(
    actor: TaskActor, sheet: list[lifeos.SheetRow], mapping: dict[str, str | None] | None
) -> lifeos.Plan:
    plan = lifeos.plan(
        sheet,
        mapping=mapping,
        accounts=store.active_profiles(),
        default_level=store.default_level(),
        now=service._now(),
        zone=actor.user.profile.get("timezone"),
    )
    plan.skip_existing(store.existing_import_keys([r.key for r in plan.rows if r.key]))
    return plan


def dry_run(actor: TaskActor, content: bytes, mapping: LifeOsMapping | None) -> dict[str, Any]:
    """Contracts §1 `POST /tasks/import/dry-run` — the report and the
    `run_id` the real run needs. Writes only the run record."""
    owner_only(actor)
    sheet = _sheet(content)
    plan = _plan(actor, sheet, mapping.owners if mapping else None)
    row = store.apply(
        _importer(actor).as_rpc(),
        [
            {
                "op": "insert",
                "table": "task_import_runs",
                "values": {
                    "uploader_id": actor.user.id,
                    "file_sha256": _sha256(content),
                    "owner_mapping": plan.owner_mapping,
                    "value_mapping": plan.value_mapping,
                    "counts": plan.counts,
                    "report": plan.report(),
                },
            }
        ],
    )[0]["row"]
    return {"run_id": row["id"], **plan.dry_run()}


def _at(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def apply(actor: TaskActor, run_id: str, content: bytes) -> dict[str, Any]:
    """Contracts §1 `POST /tasks/import/{run_id}/apply` — creates each row
    whose key is new and returns `{run_id, counts}`."""
    owner_only(actor)
    run = store.get_import_run(run_id)
    if run is None or run["uploader_id"] != actor.user.id:
        raise TaskRefused(404, NO_SUCH_RUN)
    if store.latest_import_run_id(actor.user.id) != run_id:
        raise TaskRefused(409, NOT_THE_FILE)  # not the last dry run (FR-TASK-IMP-04)
    if run["file_sha256"] != _sha256(content):
        raise TaskRefused(409, NOT_THE_FILE)
    if service._now() - _at(run["dry_run_at"]) > DRY_RUN_FOR:
        raise TaskRefused(409, TOO_OLD)
    if run["status"] == "applied":  # done already: changes nothing (FR-TASK-IMP-05)
        return {"run_id": run_id, "counts": _answer(run["counts"])}
    plan = _plan(actor, _sheet(content), run["owner_mapping"])
    if plan.blocking:
        raise TaskRefused(422, " ".join(plan.blocking))

    importer = _importer(actor)
    counts = plan.counts
    created = 0
    try:
        for item in plan.to_create:
            service.create_task(
                importer,
                TaskCreate(
                    title=item.title,
                    notes=item.notes,
                    due_on=item.due_on,
                    priority=item.priority,
                    assignee_id=item.assignee_id,
                    source="import",
                    source_ref=item.key,
                    tags=item.tags,
                    status=item.status,
                    waiting_on_text=item.waiting_on_text,
                    completed_at=item.completed_at,
                ),
            )
            created += 1
    except Exception:
        try:
            _record(importer, run_id, "failed", {**counts, "created": created})
        except Exception:  # the write's own error is the one to surface
            logger.exception('{"event": "task_import_failed_unrecorded", "run": "%s"}', run_id)
        raise
    _record(importer, run_id, "applied", {**counts, "created": created})
    return {"run_id": run_id, "counts": _answer({**counts, "created": created})}


def _answer(counts: dict[str, int]) -> dict[str, int]:
    return {k: counts[k] for k in ("created", "existing", "repeated", "skipped")}


def _record(importer: TaskActor, run_id: str, status: str, counts: dict[str, int]) -> None:
    store.apply(
        importer.as_rpc(),
        [
            {
                "op": "update",
                "table": "task_import_runs",
                "id": run_id,
                "values": {
                    "status": status,
                    "counts": counts,
                    **({"applied_at": service._now().isoformat()} if status == "applied" else {}),
                },
            }
        ],
    )
