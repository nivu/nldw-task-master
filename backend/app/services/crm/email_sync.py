"""Gmail label sync — spec 007 FR-CRM-SYNC-01 to -10, FR-CRM-PIPE-02,
FR-CRM-AUTO-01 (automations 2 and 3), FR-CRM-FLAG-01, FR-CRM-NOTIF-01,
FR-CRM-TODAY-02, FR-CRM-REC-03, FR-CRM-JOB-01; Q-23, Q-29, Q-62, Q-74;
contracts §2.10; plan §2.2, §5.11.

Labelled Gmail threads land on the right people and companies, as metadata
only, within 15 minutes:

- **The tick** (`crm.email_sync_tick`, every 15 minutes, on the shared
  worker) records a run in every workspace. It is `skipped` while the
  workspace's switch is off or `CRM_GMAIL_CONFIGURED` is false, so with no
  credential nothing else happens (Q-23); it never reads the key, which that
  worker does not hold. Otherwise it takes a Redis `SET NX EX 900`
  single-flight lock (`skipped` "A sync is already running" when held) and
  enqueues the first page task onto the `crm_sync` queue.
- **A page task** (`crm.email_sync_page`, on the dedicated single-slot
  `worker-crm-sync`, the only process with the key) reads one page of one
  mailbox and enqueues the next, so no task comes near the worker's time
  limit and each resumes from the cursor stored on the mailbox
  (FR-CRM-SYNC-04). It re-checks the workspace's switch, never the
  `CRM_GMAIL_CONFIGURED` flag, which that worker is not given (plan §2.2):
  a missing key fails the run where it is seen. The first connection reads
  the last 90 days label by label (FR-CRM-SYNC-08); after that Gmail's
  history says what was added and what lost its label (FR-CRM-SYNC-09). A
  later re-read (history too old, or the labels changed) then removes what
  is no longer labelled. The last page of a workspace finishes its run, and
  the last page of all releases the lock.
- **A mailbox whose owner left** the workspace, or whose account is
  inactive, is skipped (FR-CRM-SYNC-01); removing a membership also pauses
  the person's mailboxes there (040).
- **A message** is classified by `domain/crm/email_rules.py`: internal-only
  ones are skipped; outside participants are matched to people, then to
  companies by domain; what is unknown is created (automation 2, behind its
  switch); the message and its links are stored once (FR-CRM-SYNC-07);
  `last_*` move forward only; a company at New moves to Contacted on the
  first outbound email (automation 3); flags are recomputed; and a record
  whose Reply owed has turned red tells its owner (FR-CRM-NOTIF-01).

Every write to a record goes through `crm_apply` as the actor kind `sync`,
so it is audited; the message log goes through `crm_store_emails` and
`crm_remove_emails` (039). `POST /crm/email-sync/run` starts the same run
for one workspace and waits briefly for it (Q-62: Admins only).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.config import settings
from app.domain.crm import email_rules as rules
from app.domain.crm import flags as flag_rules
from app.domain.crm import today as today_rules
from app.services import supabase as db
from app.services.crm import flags, gmail, jobs, notifications, records, store

logger = logging.getLogger("nldw-task-master")

JOB: Final = "email_sync"
LOCK_KEY: Final = "crm:email_sync:lock"
#: The lock outlives a tick's interval only if a page keeps extending it.
LOCK_SECONDS: Final = 900
#: Messages per page: each one's metadata is a Gmail call, and a page must
#: end well inside the task's 240-second soft limit.
PAGE_SIZE: Final = 50
#: How long `POST /crm/email-sync/run` waits for its run before answering
#: that it is still going.
WAIT_SECONDS: Final = 20

OFF: Final = "Email sync is off"
RUNNING: Final = "A sync is already running"
NOT_CONFIGURED: Final = "The Gmail credential is not configured"
#: The `email_sync` switch cannot go on before the credential exists (Q-23).
CANNOT_SWITCH_ON: Final = (
    "Email sync cannot be switched on until the Gmail service-account credential is configured."
)
#: Addresses the sync matches and creates people for; anything stranger is
#: kept on the message as written and matched to nobody. The domain is the
#: one `crm_company_domains` accepts (029), so automation 2 never writes a
#: domain the database refuses.
_ADDRESS = re.compile(
    r"^[a-z0-9._%+'-]+@[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
#: Gmail ids per call when a re-read lists what is still labelled.
LIST_SIZE: Final = 500
#: Counts a page adds up.
COUNTS: Final = (
    "fetched",
    "stored",
    "removed",
    "skipped_internal",
    "matched_people",
    "created_companies",
    "created_people",
)


class LabelsMissing(gmail.GmailError):
    """None of a mailbox's labels exists in its Gmail."""


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# On or off — Q-23, FR-CRM-TODAY-02
# ---------------------------------------------------------------------------


def _settings(workspace_id: str) -> dict[str, Any]:
    return {row["key"]: row["value"] for row in store.list_settings(workspace_id)}


def live(settings_by_key: dict[str, Any]) -> bool:
    """Sync is live in a workspace when its switch is on and the credential
    exists (the non-secret flag; the key itself is never read here)."""
    return today_rules.email_sync_on(settings_by_key) and settings.CRM_GMAIL_CONFIGURED


def off_reason(workspace_id: str) -> str | None:
    if not today_rules.email_sync_on(_settings(workspace_id)):
        return OFF
    if not settings.CRM_GMAIL_CONFIGURED:
        return NOT_CONFIGURED
    return None


# ---------------------------------------------------------------------------
# The single-flight lock and the queue — FR-CRM-SYNC-04
# ---------------------------------------------------------------------------


def _redis():
    import redis

    return redis.Redis.from_url(str(settings.REDIS_URL), socket_timeout=5)


def acquire_lock(token: str) -> bool:
    """`SET NX EX 900`: true when this run now holds the lock."""
    return bool(_redis().set(LOCK_KEY, token, nx=True, ex=LOCK_SECONDS))


def extend_lock(token: str) -> None:
    """Each page keeps a long backfill's lock alive; a crashed run's lock
    still lapses 15 minutes after its last page."""
    client = _redis()
    if (client.get(LOCK_KEY) or b"").decode() == token:
        client.expire(LOCK_KEY, LOCK_SECONDS)


def release_lock(token: str) -> None:
    client = _redis()
    if (client.get(LOCK_KEY) or b"").decode() == token:
        client.delete(LOCK_KEY)


def enqueue(args: dict[str, Any]) -> None:
    """The next page task, onto the queue only `worker-crm-sync` serves."""
    from app.worker import CRM_SYNC_QUEUE, celery_app

    celery_app.send_task("crm.email_sync_page", args=[args], queue=CRM_SYNC_QUEUE)


# ---------------------------------------------------------------------------
# The tick and run-now
# ---------------------------------------------------------------------------


def _skip(reason: str):
    def work(workspace: Any, now: datetime) -> dict[str, Any]:
        raise jobs.Skipped(reason)

    return work


def _fail(exc: BaseException):
    def work(workspace: Any, now: datetime) -> dict[str, Any]:
        raise exc

    return work


def _members(workspace_id: str) -> set[str]:
    return {m["user_id"] for m in store.list_members(workspace_id)}


def _owner_here(ws: dict[str, Any], mailbox: dict[str, Any], members: set[str]) -> bool:
    """FR-CRM-SYNC-01 — a mailbox is read only while its owner is an active
    member of its workspace; otherwise it is skipped, and the skip logged."""
    owner = db.get_profile(mailbox["owner_id"]) if mailbox["owner_id"] in members else None
    if owner and owner.get("is_active", True):
        return True
    logger.warning(
        '{"event": "crm_email_sync_owner_gone", "workspace": "%s", "mailbox": "%s"}',
        ws["key"],
        mailbox["id"],
    )
    return False


def _start(
    workspaces: list[dict[str, Any]], now: datetime, token: str
) -> dict[str, dict[str, Any]]:
    """Opens a `running` run per workspace and enqueues the first page. A
    workspace with no active mailbox finishes at once. Releases the lock when
    there is nothing to enqueue."""
    plan, out = [], {}
    for ws in workspaces:
        run = store.start_job_run(JOB, ws["id"], now)
        members = _members(ws["id"])
        mailboxes = [
            m["id"]
            for m in store.list_mailboxes(ws["id"], active_only=True)
            if _owner_here(ws, m, members)
        ]
        out[ws["key"]] = {"outcome": "running", "run_id": run["id"], "mailboxes": len(mailboxes)}
        if not mailboxes:
            jobs.finish(JOB, ws, run["id"], {"outcome": "ok", "counts": {"mailboxes": 0}}, now)
            out[ws["key"]]["outcome"] = "ok"
            continue
        plan.append({"workspace_id": ws["id"], "run_id": run["id"], "mailboxes": mailboxes})
    if not plan:
        release_lock(token)
        return out
    try:
        enqueue({"plan": plan, "w": 0, "m": 0, "lock": token, "counts": {}})
    except Exception as exc:
        logger.exception('{"event": "crm_email_sync_enqueue_failed"}')
        by_id = {ws["id"]: ws for ws in workspaces}
        for entry in plan:
            jobs.finish(JOB, by_id[entry["workspace_id"]], entry["run_id"], jobs.failure(JOB, exc))
            out[by_id[entry["workspace_id"]]["key"]]["outcome"] = "failed"
        release_lock(token)
    return out


def tick(now: datetime | None = None) -> dict[str, Any]:
    """Every 15 minutes: a run, or a recorded skip, in every workspace."""
    now = now or _now()
    runs: dict[str, Any] = {}
    ready = []
    for ws in store.list_workspaces():
        why = off_reason(ws["id"])
        if why:
            runs[ws["key"]] = jobs.run(JOB, ws, _skip(f"{why}."), now)
        else:
            ready.append(ws)
    if not ready:
        return {"runs": runs}
    token = str(uuid.uuid4())
    try:
        held = acquire_lock(token)
    except Exception as exc:
        logger.exception('{"event": "crm_email_sync_lock_failed"}')
        for ws in ready:
            runs[ws["key"]] = jobs.run(JOB, ws, _fail(exc), now)
        return {"runs": runs}
    if not held:
        for ws in ready:
            runs[ws["key"]] = jobs.run(JOB, ws, _skip(f"{RUNNING}."), now)
        return {"runs": runs}
    try:
        runs.update(_start(ready, now, token))
    except Exception:
        release_lock(token)
        raise
    return {"runs": runs}


def _report(run: dict[str, Any] | None, started: dict[str, Any]) -> dict[str, Any]:
    counts = (run or {}).get("counts") or {}
    return {
        "run_id": started["run_id"],
        "outcome": (run or {}).get("outcome") or started["outcome"],
        "mailboxes": started["mailboxes"],
        **{k: int(counts.get(k) or 0) for k in COUNTS if k != "skipped_internal"},
        "skipped_internal": int(counts.get("skipped_internal") or 0),
    }


def run_now(ctx: Any) -> dict[str, Any]:
    """Contracts §2.10 `POST /crm/email-sync/run` — the workspace's run now
    (Q-62), answered with its counts when it ends within `WAIT_SECONDS`, or
    with `outcome: running` (the job log has the rest)."""
    ws = ctx.workspace
    why = off_reason(ws["id"])
    if why:
        return {"skipped": why}
    token = str(uuid.uuid4())
    if not acquire_lock(token):
        return {"skipped": RUNNING}
    now = _now()
    try:
        started = _start([ws], now, token)[ws["key"]]
    except Exception:
        # Never leave the lock to lapse: every run would be skipped for
        # 15 minutes.
        release_lock(token)
        raise
    deadline = time.monotonic() + WAIT_SECONDS
    run = store.get_job_run(ws["id"], started["run_id"])
    while (run or {}).get("outcome") == "running" and time.monotonic() < deadline:
        time.sleep(1)
        run = store.get_job_run(ws["id"], started["run_id"])
    logger.info(
        '{"event": "crm_email_sync_run_now", "workspace": "%s", "outcome": "%s"}',
        ws["key"],
        (run or {}).get("outcome"),
    )
    return _report(run, started)


# ---------------------------------------------------------------------------
# A page — FR-CRM-SYNC-04, -08, -09
# ---------------------------------------------------------------------------


def page(args: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """One page of one mailbox, then the next task: the rest of this
    mailbox, the next mailbox, or the next workspace. Never raises: a page
    that fails ends its workspace's run `failed` (the next tick resumes from
    the stored cursor) and the plan moves on."""
    now = now or _now()
    plan, w, m, token = args["plan"], args["w"], args["m"], args["lock"]
    entry = plan[w]
    workspaces = {ws["id"]: ws for ws in store.list_workspaces()}
    ws = workspaces.get(entry["workspace_id"])
    counts: dict[str, Any] = dict(args.get("counts") or {})
    try:
        extend_lock(token)
    except Exception:
        logger.exception('{"event": "crm_email_sync_lock_extend_failed"}')

    next_step: tuple[int, int, dict[str, Any]] | None
    try:
        if ws is None:
            raise RuntimeError("The workspace no longer exists.")
        mailbox = store.get_mailbox(ws["id"], entry["mailboxes"][m])
        finished = True
        if (
            mailbox
            and mailbox["is_active"]
            and today_rules.email_sync_on(_settings(ws["id"]))
            and _owner_here(ws, mailbox, _members(ws["id"]))
        ):
            finished, got = sync_page(ws, mailbox, now)
            for key, value in got.items():
                counts[key] = int(counts.get(key) or 0) + int(value)
        if not finished:
            next_step = (w, m, counts)
        elif m + 1 < len(entry["mailboxes"]):
            next_step = (w, m + 1, counts)
        else:
            counts["mailboxes"] = len(entry["mailboxes"])
            try:
                counts["reply_owed_notices"] = notifications.reply_owed_notices(ws, now)
            except Exception:
                logger.exception('{"event": "crm_reply_owed_notices_failed"}')
            jobs.finish(JOB, ws, entry["run_id"], {"outcome": "ok", "counts": counts}, now)
            next_step = (w + 1, 0, {}) if w + 1 < len(plan) else None
    except Exception as exc:
        logger.exception(
            '{"event": "crm_email_sync_page_failed", "workspace": "%s"}',
            (ws or {}).get("key", "-"),
        )
        jobs.finish(JOB, ws, entry["run_id"], {**jobs.failure(JOB, exc), "counts": counts}, now)
        next_step = (w + 1, 0, {}) if w + 1 < len(plan) else None

    if next_step is None:
        release_lock(token)
        return {"done": True}
    nw, nm, ncounts = next_step
    try:
        enqueue({"plan": plan, "w": nw, "m": nm, "lock": token, "counts": ncounts})
    except Exception as exc:
        # The broker is gone: what is left of the plan waits for the next
        # tick, which resumes every mailbox from its stored cursor.
        logger.exception('{"event": "crm_email_sync_enqueue_failed"}')
        for later in plan[nw:]:
            jobs.finish(
                JOB,
                workspaces.get(later["workspace_id"]),
                later["run_id"],
                {**jobs.failure(JOB, exc), "counts": ncounts if later is plan[nw] else {}},
                now,
            )
        release_lock(token)
    return {"done": False}


def _cursor(mailbox: dict[str, Any]) -> dict[str, Any]:
    try:
        found = json.loads(mailbox.get("page_token") or "{}")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _label_ids(mailbox: dict[str, Any]) -> list[str]:
    """The mailbox's labels as Gmail ids, in the order set, each named by id
    or by name (any case). A label missing from Gmail is skipped and logged;
    none at all is a failure the admins hear about."""
    found = gmail.labels(mailbox["email"])
    by_name = {str(lb.get("name", "")).lower(): lb["id"] for lb in found}
    ids = {lb["id"] for lb in found}
    out: list[str] = []
    for label in mailbox["labels"]:
        lid = label if label in ids else by_name.get(label.lower())
        if lid and lid not in out:
            out.append(lid)
    if len(out) < len(mailbox["labels"]):
        logger.warning(
            '{"event": "crm_email_sync_label_missing", "missing": %d}',
            len(mailbox["labels"]) - len(out),
        )
    if not out:
        raise LabelsMissing("None of this mailbox's labels exists in its Gmail.")
    return out


def sync_page(
    ws: dict[str, Any], mailbox: dict[str, Any], now: datetime
) -> tuple[bool, dict[str, int]]:
    """One page of one mailbox: `(mailbox finished, counts)`. The cursor is
    stored after the page's messages, so a page cut short is read again."""
    sub = mailbox["email"]
    labels = _label_ids(mailbox)
    ours = set(labels)
    cursor = _cursor(mailbox)

    if mailbox.get("backfilled_at") is None or not mailbox.get("history_id"):
        # FR-CRM-SYNC-08: the last 90 days, label by label, page by page.
        if cursor.get("phase") != "backfill":
            cursor = {
                "phase": "backfill",
                "label": 0,
                "token": None,
                "start": str(gmail.profile(sub).get("historyId") or ""),
                # A re-read: what was stored before may have lost its label.
                "reconcile": bool(mailbox.get("last_synced_at")),
            }
        got: dict[str, int] = {}
        if cursor["label"] < len(labels):
            listed = gmail.list_messages(
                sub,
                labels[cursor["label"]],
                after=int(rules.backfill_after(now).timestamp()),
                page_token=cursor.get("token"),
                page_size=PAGE_SIZE,
            )
            got = _take(ws, mailbox, [m["id"] for m in listed.get("messages") or []], ours)
            if listed.get("nextPageToken"):
                cursor["token"] = listed["nextPageToken"]
            else:
                cursor = {**cursor, "label": cursor["label"] + 1, "token": None}
        if cursor["label"] >= len(labels):
            if cursor.get("reconcile"):
                got["removed"] = got.get("removed", 0) + _reconcile(ws, mailbox, labels, now)
            store.mailbox_progress(
                ws["id"],
                mailbox["id"],
                {
                    "history_id": cursor["start"] or None,
                    "page_token": None,
                    "backfilled_at": now.isoformat(),
                    "last_synced_at": now.isoformat(),
                },
            )
            return True, got
        store.mailbox_progress(ws["id"], mailbox["id"], {"page_token": json.dumps(cursor)})
        return False, got

    # Incremental: what was labelled, unlabelled or deleted since.
    if cursor.get("phase") != "history":
        cursor = {"phase": "history", "token": None}
    try:
        changes = gmail.history(
            sub, mailbox["history_id"], page_token=cursor.get("token"), page_size=PAGE_SIZE
        )
    except gmail.HistoryGone:
        # Too old for Gmail: read the labels afresh. Stored messages are kept
        # and a second store of one is a no-op (FR-CRM-SYNC-07); the re-read
        # ends by removing those no longer labelled.
        store.mailbox_progress(
            ws["id"],
            mailbox["id"],
            {"history_id": None, "page_token": None, "backfilled_at": None},
        )
        return False, {}
    added, removed = changed_messages(changes.get("history") or [], ours)
    # A page's messages are stored a chunk at a time, so a page cut short by
    # the time limit keeps what it stored and is read again from its token.
    got = dict.fromkeys(COUNTS, 0)
    ids = sorted(added)
    for i in range(0, len(ids), PAGE_SIZE):
        for key, value in _take(ws, mailbox, ids[i : i + PAGE_SIZE], ours).items():
            got[key] += value
    got["removed"] = _drop(ws, mailbox, sorted(removed))
    if changes.get("nextPageToken"):
        cursor["token"] = changes["nextPageToken"]
        store.mailbox_progress(ws["id"], mailbox["id"], {"page_token": json.dumps(cursor)})
        return False, got
    store.mailbox_progress(
        ws["id"],
        mailbox["id"],
        {
            "history_id": str(changes.get("historyId") or mailbox["history_id"]),
            "page_token": None,
            "last_synced_at": now.isoformat(),
        },
    )
    return True, got


def _reconcile(
    ws: dict[str, Any], mailbox: dict[str, Any], labels: list[str], now: datetime
) -> int:
    """FR-CRM-SYNC-09 after a re-read: a stored message of the 90-day window
    that no label of the mailbox carries now lost its label while history
    could not say so, and is removed with its effect on flags. A day's
    margin keeps the window's edge out of it."""
    after = int(rules.backfill_after(now).timestamp())
    labelled: set[str] = set()
    for label in labels:
        token = None
        while True:
            listed = gmail.list_messages(
                mailbox["email"], label, after=after, page_token=token, page_size=LIST_SIZE
            )
            labelled |= {m["id"] for m in listed.get("messages") or []}
            token = listed.get("nextPageToken")
            if not token:
                break
    since = datetime.fromtimestamp(after, UTC) + timedelta(days=1)
    stored = store.mailbox_message_ids(ws["id"], mailbox["id"], since.isoformat())
    return _drop(ws, mailbox, sorted(stored - labelled))


def changed_messages(history: list[dict[str, Any]], ours: set[str]) -> tuple[set[str], set[str]]:
    """From Gmail history records, oldest first: the messages that now carry
    one of our labels, and those that lost the last of them or were deleted
    (FR-CRM-SYNC-09). A later record overrules an earlier one."""
    added: set[str] = set()
    removed: set[str] = set()
    for record in history:
        for event in record.get("messagesAdded") or []:
            msg = event.get("message") or {}
            if ours & set(msg.get("labelIds") or []):
                added.add(msg["id"])
                removed.discard(msg["id"])
        for event in record.get("labelsAdded") or []:
            msg = event.get("message") or {}
            if ours & set(event.get("labelIds") or []):
                added.add(msg["id"])
                removed.discard(msg["id"])
        for event in record.get("labelsRemoved") or []:
            msg = event.get("message") or {}
            if ours & set(event.get("labelIds") or []) and not ours & set(
                msg.get("labelIds") or []
            ):
                removed.add(msg["id"])
                added.discard(msg["id"])
        for event in record.get("messagesDeleted") or []:
            msg = event.get("message") or {}
            removed.add(msg["id"])
            added.discard(msg["id"])
    return added, removed


# ---------------------------------------------------------------------------
# Storing a page's messages — FR-CRM-SYNC-05 to -07
# ---------------------------------------------------------------------------


def _sync_actor(workspace_id: str) -> dict[str, Any]:
    """Email sync writes as itself (FR-CRM-AUD-02 `sync`)."""
    return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": workspace_id}


def _email_source(workspace_id: str) -> str | None:
    """The Source option "Email sync" (026 seeds it with this key)."""
    return next(
        (
            o["id"]
            for o in store.list_options(workspace_id)
            if o["list_key"] == "source" and o.get("system_key") == "email_sync"
        ),
        None,
    )


def _take(
    ws: dict[str, Any], mailbox: dict[str, Any], gmail_ids: list[str], ours: set[str]
) -> dict[str, int]:
    """Fetches, classifies, matches, creates and stores the messages of a
    page that are not stored yet; then moves `last_*` forward, New to
    Contacted, and recomputes flags."""
    ws_id = ws["id"]
    counts = dict.fromkeys(COUNTS, 0)
    if not gmail_ids:
        return counts
    known = store.stored_message_ids(ws_id, mailbox["id"], gmail_ids)
    found = []
    for gmail_id in gmail_ids:
        if gmail_id in known:
            continue
        meta = gmail.message(mailbox["email"], gmail_id)
        # Gone meanwhile, or no longer labelled.
        if not meta or not ours & set(meta.get("labelIds") or []):
            continue
        counts["fetched"] += 1
        info = rules.classify(
            meta["headers"], ws.get("allowed_domains") or [], meta["internalDate"]
        )
        if info["is_internal"]:
            counts["skipped_internal"] += 1
            continue
        info["outside"] = [p for p in info["outside"] if _ADDRESS.match(p["email"])]
        found.append({**info, "gmail_message_id": gmail_id, "thread_id": meta["threadId"]})
    if not found:
        return counts

    outside = [p for m in found for p in m["outside"]]
    addresses = sorted({p["email"] for p in outside})
    people: dict[str, dict[str, Any]] = {}
    for person in store.people_by_emails(ws_id, addresses):
        for address in [person.get("primary_email"), *(person.get("additional_emails") or [])]:
            if address in addresses:
                people.setdefault(address, person)
    counts["matched_people"] = len({p["id"] for p in people.values()})
    domains = sorted({rules.domain_of(a) for a in addresses})
    companies = {d["domain"]: d["company_id"] for d in store.list_domains(ws_id, domains=domains)}

    # Automation 2: what is unknown is created, owned by the mailbox owner.
    if notifications.automation_on(ws_id, "email_records"):
        made_companies, made_people = _create(ws_id, mailbox, outside, people, companies)
        counts["created_companies"] = made_companies
        counts["created_people"] = made_people

    company_ids = sorted(
        set(companies.values()) | {p["company_id"] for p in people.values() if p.get("company_id")}
    )
    open_deals: dict[str, list[str]] = {}
    for deal in store.deals_of_companies(ws_id, company_ids) if company_ids else []:
        if deal["stage"] in flag_rules.OPEN_STAGES:
            open_deals.setdefault(deal["company_id"], []).append(deal["id"])
    for deal_ids in open_deals.values():
        deal_ids.sort()

    messages = []
    for m in found:
        messages.append(
            {
                "gmail_message_id": m["gmail_message_id"],
                "thread_id": m["thread_id"],
                "subject": m["subject"],
                "sent_at": m["sent_at"].isoformat(),
                "direction": m["direction"],
                "from_email": m["from_email"],
                "participants": m["participants"],
                "is_internal": False,
                "is_auto": m["is_auto"],
                "links": rules.links_for(m["outside"], people, companies, open_deals),
            }
        )
    stored = set(store.store_emails(ws_id, mailbox["id"], messages))
    counts["stored"] = len(stored)
    kept = [
        {**msg, "sent_at": m["sent_at"]}
        for msg, m in zip(messages, found, strict=True)
        if msg["gmail_message_id"] in stored
    ]
    if kept:
        _touch_records(ws_id, kept)
    return counts


def _create(
    ws_id: str,
    mailbox: dict[str, Any],
    outside: list[dict[str, str]],
    people: dict[str, dict[str, Any]],
    companies: dict[str, str],
) -> tuple[int, int]:
    """Automation 2 — companies at New (with their domain) and people, owned
    by the mailbox owner, Source Email sync, in one audited write. Updates
    `people` and `companies` with what it made."""
    new_companies, new_people = rules.to_create(outside, people, companies)
    if not new_companies and not new_people:
        return 0, 0
    source = _email_source(ws_id)
    ops: list[dict[str, Any]] = []
    refs: dict[str, str] = {}
    for n, (domain, name) in enumerate(sorted(new_companies.items())):
        ref = f"c{n}"
        refs[domain] = ref
        values: dict[str, Any] = {
            "name": name,
            "lead_stage": "new",
            "owner_id": mailbox["owner_id"],
        }
        if source:
            values["source_id"] = source
        ops.append({"op": "insert", "table": "crm_companies", "values": values, "ref": ref})
        ops.append(
            {
                "op": "insert",
                "table": "crm_company_domains",
                "values": {"company_id": f"$ref:{ref}", "domain": domain, "is_primary": True},
            }
        )
    person_refs: dict[str, str] = {}
    for n, (address, spec) in enumerate(sorted(new_people.items())):
        ref = f"p{n}"
        person_refs[address] = ref
        domain = spec["domain"]
        company = companies.get(domain) if domain else None
        if domain and not company and domain in refs:
            company = f"$ref:{refs[domain]}"
        ops.append(
            {
                "op": "insert",
                "table": "crm_people",
                "values": {
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "primary_email": address,
                    "company_id": company,
                    "owner_id": mailbox["owner_id"],
                },
                "ref": ref,
            }
        )
    done = {d["ref"]: d for d in store.apply(_sync_actor(ws_id), ops) if d.get("ref")}
    for domain, ref in refs.items():
        companies[domain] = done[ref]["id"]
    for address, ref in person_refs.items():
        people[address] = done[ref]["row"]
    logger.info(
        '{"event": "crm_email_sync_created", "companies": %d, "people": %d}',
        len(new_companies),
        len(new_people),
    )
    return len(new_companies), len(new_people)


def _touch_records(ws_id: str, kept: list[dict[str, Any]]) -> None:
    """`last_*` forward only, New → Contacted on an outbound email, then the
    flags of everything the messages name (FR-CRM-SYNC-05, FR-CRM-PIPE-02)."""
    about: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for msg in kept:
        for link in msg["links"]:
            about.setdefault((link["record_type"], link["record_id"]), []).append(msg)
    company_ids = {rid for (kind, rid) in about if kind == "company"}
    person_ids = {rid for (kind, rid) in about if kind == "person"}
    deal_ids = {rid for (kind, rid) in about if kind == "opportunity"}
    ops: list[dict[str, Any]] = []
    for table, kind, ids in (
        ("crm_companies", "company", company_ids),
        ("crm_people", "person", person_ids),
    ):
        for rid, row in records.by_id(ws_id, table, ids).items():
            if row.get("deleted_at"):
                continue
            mail = about[(kind, rid)]
            values = rules.forward(row, rules.latest(mail))
            if kind == "company" and rules.contacted(row, mail):
                values["lead_stage"] = "contacted"
            if values:
                ops.append({"op": "update", "table": table, "id": rid, "values": values})
    if ops:
        store.apply(_sync_actor(ws_id), ops)
    flags.after_write(ws_id, companies=company_ids, people=person_ids, deals=deal_ids)


def _drop(ws: dict[str, Any], mailbox: dict[str, Any], gmail_ids: list[str]) -> int:
    """FR-CRM-SYNC-09 — a label removed removes the stored copy, and its
    effect on flags (`last_*` stay: they say when contact happened)."""
    if not gmail_ids:
        return 0
    gone = store.remove_emails(ws["id"], mailbox["id"], gmail_ids)
    linked = flags.linked_records(gone.get("records") or [])
    flags.after_write(
        ws["id"], companies=linked["companies"], people=linked["people"], deals=linked["deals"]
    )
    return int(gone.get("removed") or 0)


# ---------------------------------------------------------------------------
# Reading — FR-CRM-REC-03 Emails tab, `crm_get` (FR-CRM-SYNC-10)
# ---------------------------------------------------------------------------


def _people_of_company(ws_id: str, company_id: str) -> list[str]:
    """All of them, page by page: a long-running client has many."""
    ids: list[str] = []
    while True:
        rows = store.list_records(
            ws_id,
            "crm_people",
            filters={"company_id": company_id},
            deleted=None,
            limit=_PEOPLE_PAGE,
            offset=len(ids),
        )
        ids += [r["id"] for r in rows]
        if len(rows) < _PEOPLE_PAGE:
            return ids


_PEOPLE_PAGE: Final = 1000


def threads(
    ctx: Any, kind: str, record_id: str, *, before: str | None = None, limit: int = 25
) -> dict[str, Any]:
    """Contracts §2.10 `GET /crm/{object}/{id}/emails` — the record's
    threads, newest first: a person's own; a company's and all its
    people's; a deal's, its company's and its contacts'. Subject,
    participants, direction and time only (Q-29). Paged by `before`, the
    `last_at` of the last thread shown."""
    ws_id = ctx.workspace_id
    row = records.live(ws_id, kind, record_id)
    wanted: dict[str, set[str]] = {"company": set(), "person": set(), "opportunity": set()}
    wanted[kind].add(record_id)
    if kind == "company":
        wanted["person"] |= set(_people_of_company(ws_id, record_id))
    if kind == "opportunity":
        if row.get("company_id"):
            wanted["company"].add(row["company_id"])
            wanted["person"] |= set(_people_of_company(ws_id, row["company_id"]))
        if row.get("point_of_contact_id"):
            wanted["person"].add(row["point_of_contact_id"])
        wanted["person"] |= {
            c["person_id"] for c in store.list_contacts(ws_id, opportunity_ids=[record_id])
        }
    message_ids = {
        link["message_id"]
        for record_type, ids in wanted.items()
        for link in store.email_links(ws_id, record_type, sorted(ids))
    }
    mailboxes = {m["id"]: m["email"] for m in store.list_mailboxes(ws_id)}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for msg in store.email_messages(ws_id, sorted(message_ids)):
        grouped.setdefault((msg["mailbox_id"], msg["thread_id"]), []).append(msg)
    out = []
    for (mailbox_id, thread_id), msgs in grouped.items():
        msgs.sort(key=lambda m: (str(m["sent_at"]), m["id"]))
        last = msgs[-1]
        seen: dict[str, dict[str, str]] = {}
        for msg in msgs:
            for p in msg.get("participants") or []:
                seen.setdefault(p["email"], {"email": p["email"], "name": p.get("name") or ""})
        out.append(
            {
                "thread_id": thread_id,
                "subject": last.get("subject")
                or next((m["subject"] for m in reversed(msgs) if m.get("subject")), None),
                "last_at": last["sent_at"],
                "direction": last["direction"],
                "participants": list(seen.values()),
                "mailbox": mailboxes.get(mailbox_id),
                "messages": len(msgs),
            }
        )
    out.sort(key=lambda t: (str(t["last_at"]), t["thread_id"]), reverse=True)
    if before:
        out = [t for t in out if str(t["last_at"]) < before]
    return {
        "threads": out[:limit],
        "next": str(out[limit - 1]["last_at"]) if len(out) > limit else None,
    }


def recent(ctx: Any, kind: str, record_id: str) -> list[dict[str, Any]]:
    """`crm_get`'s recent emails: the five latest threads."""
    return threads(ctx, kind, record_id, limit=5)["threads"]
