"""Email capture's discovery — spec 010 FR-CAP-DIS-01, -02, -04 to -07, -10,
FR-CAP-RPT-01 to -03, FR-CAP-CHG-05, NFR-CAP-01 to -04; contracts §1.2,
§2.1, §2.2, §4; plan §5.6, §5.7, PD-11, PD-13, PD-14, PD-17.

**The job** (`crm_ai.capture`, hourly at minute 7, on `worker-crm-ai`), per
workspace. Before any Gmail read it checks, in order: the `capture` mode is
not Off; Gmail sync is live; an active mailbox whose owner is still here is
on *Primary and Sent*; in Dry run, a dry run is open; the Gmail key is set on
this service; and `009`'s rates and budget (`ai.gate` with no phase). Then
for each pending candidate the sync marked, at most 200 a run, oldest first
in a dry run and newest first live:

a. the conversation's metadata (`gmail.thread_meta`); messages outside
   Primary, Sent and the mailbox's labels, or in Trash or Spam, drop out;
   none left, or the thread is gone → `skipped`;
b. internal-only and bulk messages drop out (the sync's rules); none left →
   `skipped`;
c. (S3: the ignore list);
d. an outside participant now a person or a company domain → `known`, and
   `crm.capture_thread` files it on `worker-crm-sync`;
e. the budget again, then the text of **only the messages left after a and
   b** (`gmail.thread_text`), in memory;
f. one call through `009`'s wrapper (`ai.call(..., "capture", None, ...)`):
   budgeted, logged in `crm_ai_usage`, schema-checked and masked;
g. the decision, recorded only while the candidate is still pending on the
   message judged (`crm_capture_record`); a lead or a deal signal keeps its
   finding. A dry run writes nothing else: no item, no record (FR-CAP-RPT-01;
   S3 adds live items).

Failures (PD-17): Claude answering off-schema twice, or a malformed thread,
counts against the conversation (`attempts`; `failed` at the third). Any
other failure is the service's: the run ends `failed` with its counts and
the candidate stays pending as it was, so an outage or a missing key never
uses up the backlog; three such runs alert the Admins (`jobs.STREAK`).

**A dry run** (FR-CAP-DIS-10) chains itself a minute later while candidates
remain and its time is spent, at most one follow-up queued
(`crm:capture:next` holds the follow-up's task id, and only that task
clears it); a gate that closes ends the chain and the hourly tick resumes.
The mode is read again before each conversation's text is read: a switch
to Off, or a cancelled run, stops the run there. With nothing pending and
every capturing mailbox backfilled, the recall check (FR-CAP-RPT-03, PD-11)
judges each conversation labelled Sales or Leads since the start date **as
if it had no label**, storing only its fate, on the open run as each is
decided, so a run out of time resumes where it stopped; with every thread
given a fate, the run finishes. After that the job skips until a new dry
run.

The text lives only in this module's local variables: never stored, logged,
queued or put in an error (FR-CAP-CHG-05, NFR-CAP-02).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from app.config import settings
from app.domain.crm import capture as rules
from app.domain.crm import email_rules
from app.schemas.crm import CaptureOverviewOut, DiscoveryOutput
from app.services.crm import CrmRefused, ai, email_sync, gmail, jobs, store
from app.services.crm import records as base
from app.services.crm.context import actor

logger = logging.getLogger("nldw-task-master")

#: Contracts §1.4 — what a `capture` run counts.
COUNTS: Final = (
    "considered",
    # Not `skipped`: `jobs.run` writes a stopped run's reason there.
    "skipped_rules",
    "ignored",
    "known",
    "judged",
    "items",
    "low_confidence",
    "failed_items",
    "pending_left",
)
#: Plan §5.7 — the dry run's follow-up: at most one queued, a minute later.
#: The key holds its task id until it starts; it outlives a follow-up that
#: waits behind other AI tasks on the single slot.
NEXT_KEY: Final = "crm:capture:next"
NEXT_SECONDS: Final = 3600
NEXT_COUNTDOWN: Final = 60
#: Gmail ids per call when the recall check lists a label.
LIST_SIZE: Final = 500
#: A dry run's report can be applied for this long after it finished
#: (FR-CAP-RPT-04).
APPLY_DAYS: Final = 7

#: The workspaces whose dry run stopped with work left, this task.
_more: set[str] = set()


class MalformedThread(Exception):
    """A conversation Gmail answered in a shape that cannot be judged (no
    message ids, unreadable headers): it counts against the conversation."""


class RunFailed(ai.SafeError):
    """The run's own failure (Gmail, a token, Claude's service), as a fixed
    sentence, with the counts it had (NFR-CAP-04)."""

    def __init__(self, step: str, exc_type: str, counts: Mapping[str, Any]) -> None:
        super().__init__(step, exc_type)
        self.counts = dict(counts)


def _now() -> datetime:
    """The clock; tests fix it."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# The follow-up of a dry run — plan §5.7
# ---------------------------------------------------------------------------


def _redis():
    import redis

    return redis.Redis.from_url(str(settings.REDIS_URL), socket_timeout=5)


def unchain(task_id: str | None) -> None:
    """The follow-up that runs now (its task id is the key's) lets the next
    one be queued. Any other run, the hourly tick included, leaves the key,
    so a follow-up already queued stays the only one."""
    if not task_id:
        return
    try:
        client = _redis()
        queued = client.get(NEXT_KEY)
        if queued is not None and (
            queued.decode() if isinstance(queued, bytes) else str(queued)
        ) == str(task_id):
            client.delete(NEXT_KEY)
    except Exception as exc:  # noqa: BLE001 - the hourly tick still runs
        logger.warning('{"event": "crm_capture_chain_failed", "error": "%s"}', type(exc).__name__)


def wants_more() -> bool:
    """Whether a dry run of this task stopped with work left; resets."""
    more = bool(_more)
    _more.clear()
    return more


def chain() -> bool:
    """Queues the task again a minute later, unless a follow-up is queued
    already (`SET NX EX`, the value the follow-up's task id). Ids only: the
    task takes no argument."""
    try:
        task_id = uuid.uuid4().hex
        if not _redis().set(NEXT_KEY, task_id, nx=True, ex=NEXT_SECONDS):
            return False
        from app.tasks.crm_ai import capture_conversations
        from app.worker import CRM_AI_QUEUE

        capture_conversations.apply_async(
            countdown=NEXT_COUNTDOWN, queue=CRM_AI_QUEUE, task_id=task_id
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the hourly tick resumes it
        logger.warning('{"event": "crm_capture_chain_failed", "error": "%s"}', type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# The job — plan §5.6
# ---------------------------------------------------------------------------


def _capturing(workspace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The workspace's active *Primary and Sent* mailboxes whose owner is
    still an active member (FR-CRM-SYNC-01), by id: no other mailbox's
    candidates are read (NFR-CAP-05)."""
    members = {m["user_id"] for m in store.list_members(workspace["id"])}
    return {
        m["id"]: m
        for m in store.list_mailboxes(workspace["id"], active_only=True)
        if m.get("capture_source") == rules.PRIMARY_SENT
        and email_sync._owner_here(workspace, m, members)
    }


def run(workspace: dict[str, Any], now: datetime) -> dict[str, int]:
    """One `capture` run of one workspace (contracts §1.4 counts)."""
    ws_id = workspace["id"]
    mode = rules.capture_mode(store.capture_setting(ws_id))
    if mode == rules.OFF:
        raise jobs.Skipped(rules.CAPTURE_OFF)
    off = email_sync.off_reason(ws_id)
    if off:
        raise jobs.Skipped(off)
    mailboxes = _capturing(workspace)
    if not mailboxes:
        raise jobs.Skipped(rules.NO_CAPTURING_MAILBOX)
    dry = mode == rules.DRY_RUN
    open_run = None
    if dry:
        open_run = store.latest_dry_run(ws_id)
        if open_run is None or open_run.get("finished_at"):
            raise jobs.Skipped(rules.DRY_RUN_FINISHED)
    try:
        gmail._key()
    except gmail.NotConfigured:
        raise jobs.Skipped(rules.GMAIL_KEY_MISSING) from None
    ai.gate(workspace, None)

    counts = dict.fromkeys(COUNTS, 0)
    ids = sorted(mailboxes)
    pending = store.pending_candidates(ws_id, ids, newest_first=not dry, limit=rules.RUN_CAP)
    scans: dict[str, set[str]] = {}
    for candidate in pending:
        if jobs.out_of_time():
            # The rest wait for the next tick, or the dry run's follow-up.
            break
        mailbox = mailboxes[candidate["mailbox_id"]]
        counts["considered"] += 1
        try:
            _judge(workspace, mailbox, candidate, scans, open_run, counts)
        except jobs.Skipped as skipped:
            # A gate closed (the budget): the run stops and keeps its counts.
            raise jobs.Skipped(skipped.reason, counts) from None
        except Exception as exc:  # noqa: BLE001 - sorted by whose failure it is
            if rules.failure_counts(exc):
                _attempt_failed(ws_id, candidate, dry, counts, exc)
                continue
            raise _failed(exc, counts) from None

    left = store.pending_candidates(ws_id, ids, newest_first=not dry, limit=1000)
    counts["pending_left"] = len(left)
    if not dry:
        return counts
    if left:
        _more.add(ws_id)
        return counts
    if any(not m.get("backfilled_at") for m in mailboxes.values()):
        # FR-CAP-DIS-10: the run covers everything since the start date, so
        # it waits for every capturing mailbox's backfill.
        return counts
    assert open_run is not None
    try:
        recall = _recall(workspace, mailboxes, now, open_run)
    except jobs.Skipped as skipped:
        raise jobs.Skipped(skipped.reason, counts) from None
    except Exception as exc:  # noqa: BLE001 - the run's own failure
        raise _failed(exc, counts) from None
    if recall is None:
        _more.add(ws_id)
        return counts
    store.finish_dry_run(ws_id, open_run["id"], recall)
    logger.info(
        '{"event": "crm_capture_dry_run_finished", "workspace": "%s", "recall": %d}',
        workspace.get("key", "-"),
        len(recall),
    )
    return counts


def _failed(exc: BaseException, counts: Mapping[str, Any]) -> RunFailed:
    """The run's own failure as a fixed sentence naming the step and the
    exception type only (FR-CRMAI-JOB-03)."""
    if isinstance(exc, ai.SafeError):
        step, name = exc.step, exc.exc_type
    elif isinstance(exc, gmail.GmailError):
        step, name = "Gmail read", type(exc).__name__
    else:
        step, name = "Email capture", type(exc).__name__
    logger.warning('{"event": "crm_capture_run_failed", "step": "%s", "error": "%s"}', step, name)
    return RunFailed(step, name, counts)


def _attempt_failed(
    ws_id: str, candidate: dict[str, Any], dry: bool, counts: dict[str, int], exc: BaseException
) -> None:
    """PD-17 — one more try used; the third makes the candidate `failed`
    until a new message arrives."""
    attempts = min(int(candidate.get("attempts") or 0) + 1, rules.MAX_ATTEMPTS)
    store.record_candidate(
        ws_id,
        candidate["id"],
        candidate["latest_message_id"],
        {
            "status": "failed" if attempts >= rules.MAX_ATTEMPTS else "pending",
            "attempts": attempts,
            "dry_run": dry,
        },
    )
    counts["failed_items"] += 1
    logger.warning(
        '{"event": "crm_capture_candidate_failed", "candidate": "%s", "attempts": %d, '
        '"error": "%s"}',
        candidate["id"],
        attempts,
        type(exc).__name__,
    )


def _scan(mailbox: dict[str, Any], scans: dict[str, set[str]]) -> set[str]:
    """The labels a capturing mailbox reads: its own, Primary and Sent."""
    if mailbox["id"] not in scans:
        configured = email_sync._label_ids(mailbox, required=False)
        scans[mailbox["id"]] = set(rules.scan_labels(configured, rules.PRIMARY_SENT))
    return scans[mailbox["id"]]


def _survivors(
    workspace: dict[str, Any], messages: list[dict[str, Any]], scan: set[str] | tuple[str, ...]
) -> list[dict[str, Any]]:
    """Steps a and b: the messages in scope and neither internal-only nor
    bulk, each as the sync classifies it, with its Gmail id."""
    out = []
    for m in messages:
        if not m.get("id"):
            raise MalformedThread()
        if not rules.in_scope(m.get("labelIds") or [], scan):
            continue
        try:
            info = email_rules.classify(
                m["headers"], workspace.get("allowed_domains") or [], m["internalDate"]
            )
        except (KeyError, TypeError, ValueError):
            raise MalformedThread() from None
        if info["is_internal"] or rules.is_bulk(m["headers"], info["from_email"]):
            continue
        info["outside"] = [p for p in info["outside"] if email_sync._ADDRESS.match(p["email"])]
        out.append({**info, "id": m["id"]})
    return out


def _known(ws_id: str, outside: list[dict[str, str]]) -> bool:
    """Step d — an outside participant is a person (any of their emails) or
    has a company's domain, Trash included, as the sync matches (PD-7)."""
    addresses = sorted({p["email"] for p in outside})
    if not addresses:
        return False
    if store.people_by_emails(ws_id, addresses):
        return True
    domains = sorted({email_rules.domain_of(a) for a in addresses})
    return bool(store.list_domains(ws_id, domains=domains))


def _record(ws_id: str, candidate: dict[str, Any], values: dict[str, Any]) -> bool:
    return store.record_candidate(ws_id, candidate["id"], candidate["latest_message_id"], values)


def _file(ws_id: str, mailbox_id: str, thread_id: str) -> None:
    """`crm.capture_thread` on `worker-crm-sync`: ids only."""
    from app.tasks.crm import capture_thread
    from app.worker import CRM_SYNC_QUEUE

    capture_thread.apply_async(args=[ws_id, mailbox_id, thread_id], queue=CRM_SYNC_QUEUE)


def _still_on(ws_id: str, open_run: Mapping[str, Any] | None) -> None:
    """FR-CAP-DIS-01, plan §5.7 — the mode as the run began, read again
    before any text: Off, another mode, or a dry run no longer open (an
    Admin cancelled it) ends the run there."""
    mode = rules.capture_mode(store.capture_setting(ws_id))
    if mode == rules.OFF:
        raise jobs.Skipped(rules.CAPTURE_OFF)
    if mode != (rules.DRY_RUN if open_run is not None else rules.LIVE):
        raise jobs.Skipped(rules.MODE_CHANGED)
    if open_run is not None:
        latest = store.latest_dry_run(ws_id)
        if latest is None or latest["id"] != open_run["id"] or latest.get("finished_at"):
            raise jobs.Skipped(rules.DRY_RUN_FINISHED)


def _ask(
    workspace: dict[str, Any],
    mailbox: dict[str, Any],
    kept: list[dict[str, Any]],
    open_run: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]] | None:
    """Steps e and f for the surviving messages: the masked answer, the
    messages as judged (with their attachment names) and the website
    choices; None when every one of them was deleted meanwhile. The mode
    and the budget are checked again first."""
    _still_on(workspace["id"], open_run)
    ai.gate(workspace, None)
    read = gmail.thread_text(mailbox["email"], [m["id"] for m in kept])
    by_id = {r["id"]: r for r in read}
    judged = [{**m, "attachments": by_id[m["id"]]["attachments"]} for m in kept if m["id"] in by_id]
    if not judged:
        return None
    texts = {m["id"]: by_id[m["id"]]["text"] for m in judged}
    outside = rules.outside_of(judged)
    choices = rules.website_choices(outside, texts.values(), workspace.get("allowed_domains") or [])
    parts = rules.prompt_parts(
        judged,
        texts,
        websites=choices,
        replied=rules.owner_replied(judged, mailbox["email"]),
    )
    output = ai.call(workspace, rules.JOB, None, DiscoveryOutput, rules.INSTRUCTION, parts)
    return output.model_dump(mode="json"), judged, choices


def _judge(
    workspace: dict[str, Any],
    mailbox: dict[str, Any],
    candidate: dict[str, Any],
    scans: dict[str, set[str]],
    open_run: Mapping[str, Any] | None,
    counts: dict[str, int],
) -> None:
    """Steps a to g for one candidate (plan §5.6); `open_run` is the dry
    run being judged, None live."""
    ws_id = workspace["id"]
    dry = open_run is not None
    base_values = {"dry_run": dry, "classifier_version": rules.CLASSIFIER_VERSION}
    thread = gmail.thread_meta(mailbox["email"], candidate["thread_id"])
    if thread and not thread.get("messages"):
        raise MalformedThread()
    kept = _survivors(workspace, thread.get("messages") or [], _scan(mailbox, scans))
    if not kept:
        if _record(ws_id, candidate, {**base_values, "status": "skipped"}):
            counts["skipped_rules"] += 1
        return
    if _known(ws_id, rules.outside_of(kept)):
        if _record(ws_id, candidate, {**base_values, "status": "known"}):
            counts["known"] += 1
            _file(ws_id, mailbox["id"], candidate["thread_id"])
        return
    asked = _ask(workspace, mailbox, kept, open_run)
    if asked is None:
        if _record(ws_id, candidate, {**base_values, "status": "skipped"}):
            counts["skipped_rules"] += 1
        return
    answer, judged, choices = asked
    finding = rules.finding_of(answer, judged, mailbox_email=mailbox["email"], choices=choices)
    recorded = _record(
        ws_id,
        candidate,
        {
            **base_values,
            "status": "judged",
            "outcome": answer["outcome"],
            "confidence": answer["confidence"],
            "finding": finding,
        },
    )
    if not recorded:
        # A message arrived meanwhile: it is judged again, from the start.
        return
    counts["judged"] += 1
    if finding is not None and answer["confidence"] == "low":
        # FR-CAP-DIS-07: recorded, never an item; the report lists it apart.
        counts["low_confidence"] += 1


# ---------------------------------------------------------------------------
# The recall check — FR-CAP-RPT-03; plan §5.7, PD-11
# ---------------------------------------------------------------------------


def _created_before(row: Mapping[str, Any] | None, when: datetime) -> bool:
    created = (row or {}).get("created_at")
    if not created:
        return False
    at = created if isinstance(created, datetime) else datetime.fromisoformat(str(created))
    return at < when


def _known_before(ws_id: str, outside: list[dict[str, str]], when: datetime) -> bool:
    """A person, or a company by its domain, that existed before the
    conversation began: capture would have filed it as known. A company
    automation 2 made from the labelled mail itself does not count."""
    addresses = sorted({p["email"] for p in outside})
    if not addresses:
        return False
    people = store.people_by_emails(ws_id, addresses)
    rows = base.by_id(ws_id, "crm_people", {p["id"] for p in people})
    if any(_created_before(r, when) for r in rows.values()):
        return True
    domains = sorted({email_rules.domain_of(a) for a in addresses})
    company_ids = {d["company_id"] for d in store.list_domains(ws_id, domains=domains)}
    companies = base.by_id(ws_id, "crm_companies", company_ids)
    return any(_created_before(r, when) for r in companies.values())


def _labelled_threads(mailbox: dict[str, Any], now: datetime) -> list[str]:
    """The conversations of the mailbox's own labels since its start date."""
    configured = email_sync._label_ids(mailbox, required=False)
    after = int(rules.capture_after(mailbox, now).timestamp())
    threads: set[str] = set()
    for label in configured:
        token = None
        while True:
            listed = gmail.list_messages(
                mailbox["email"], label, after=after, page_token=token, page_size=LIST_SIZE
            )
            threads |= {m["threadId"] for m in listed.get("messages") or [] if m.get("threadId")}
            token = listed.get("nextPageToken")
            if not token:
                break
    return sorted(threads)


def _fate(
    workspace: dict[str, Any],
    mailbox: dict[str, Any],
    thread_id: str,
    open_run: Mapping[str, Any],
) -> tuple[str, str | None]:
    """A labelled conversation judged as if it had no label: `(fate, latest
    message time)`. Nothing is stored for it but the fate."""
    ws_id = workspace["id"]
    messages = gmail.thread_meta(mailbox["email"], thread_id).get("messages") or []
    if not messages:
        return "outside_scope", None
    times = [email_rules.sent_at(m["internalDate"]) for m in messages if m.get("internalDate")]
    latest = max(times).isoformat() if times else None
    if not any(rules.in_scope(m.get("labelIds") or [], rules.CAPTURED) for m in messages):
        return rules.recall_fate(in_capture=False, survived=False, known_before=False), latest
    try:
        kept = _survivors(workspace, messages, rules.CAPTURED)
    except MalformedThread:
        kept = []
    if not kept:
        return rules.recall_fate(in_capture=True, survived=False, known_before=False), latest
    first = min(times) if times else min(m["sent_at"] for m in kept)
    if _known_before(ws_id, rules.outside_of(kept), first):
        return rules.recall_fate(in_capture=True, survived=True, known_before=True), latest
    try:
        asked = _ask(workspace, mailbox, kept, open_run)
    except ai.AiFailed as failed:
        if not rules.failure_counts(failed):
            raise
        asked = None
    if asked is None:
        return rules.recall_fate(in_capture=True, survived=True, known_before=False), latest
    answer = asked[0]
    return (
        rules.recall_fate(
            in_capture=True,
            survived=True,
            known_before=False,
            outcome=answer["outcome"],
            confidence=answer["confidence"],
        ),
        latest,
    )


def _recall(
    workspace: dict[str, Any],
    mailboxes: dict[str, dict[str, Any]],
    now: datetime,
    open_run: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    """Every Sales or Leads conversation of the capturing mailboxes since
    their start date with its fate (at most 500 rows); None when the run's
    time ran out first. Each fate is kept on the open run as it is decided
    (`crm_capture_recall_add`), and a thread that has one already is not
    read or judged again, so the next tick resumes where this one stopped."""
    ws_id = workspace["id"]
    rows: list[dict[str, Any]] = list(open_run.get("recall") or [])
    done = {(r["mailbox_id"], r["thread_id"]) for r in rows}
    for mailbox in sorted(mailboxes.values(), key=lambda m: m["email"]):
        for thread_id in _labelled_threads(mailbox, now):
            if len(rows) >= rules.RECALL_CAP:
                return rows
            if (mailbox["id"], thread_id) in done:
                continue
            if jobs.out_of_time():
                return None
            fate, latest = _fate(workspace, mailbox, thread_id, open_run)
            row = {
                "mailbox_id": mailbox["id"],
                "thread_id": thread_id,
                "latest_at": latest,
                "fate": fate,
            }
            if not store.add_recall(ws_id, open_run["id"], [row]):
                # Cancelled, or finished, since the run began.
                raise jobs.Skipped(rules.DRY_RUN_FINISHED)
            rows.append(row)
            done.add((mailbox["id"], thread_id))
    return rows


# ---------------------------------------------------------------------------
# Settings, Email capture — contracts §2.1, §2.2
# ---------------------------------------------------------------------------


def start_dry_run(ctx: Any) -> dict[str, Any]:
    """`POST /crm/capture/dry-run` — opens a run and sets the mode to Dry
    run; the hourly job, or its follow-up, does the work (the API holds no
    Gmail or Claude key)."""
    ws_id = ctx.workspace_id
    if not any(
        m["is_active"] and m.get("capture_source") == rules.PRIMARY_SENT
        for m in store.list_mailboxes(ws_id)
    ):
        raise CrmRefused(422, rules.SET_MAILBOX_FIRST)
    if email_sync.off_reason(ws_id):
        raise CrmRefused(422, rules.SYNC_FIRST)
    run_id = store.start_dry_run(actor(ctx))
    started = store.latest_dry_run(ws_id)
    logger.info('{"event": "crm_capture_dry_run_started", "workspace": "%s"}', ctx.workspace["key"])
    return {
        "dry_run": {
            "id": run_id,
            "started_at": (started or {}).get("started_at")
            if (started or {}).get("id") == run_id
            else None,
        }
    }


def _at(value: Any) -> datetime | None:
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def applicable(run: Mapping[str, Any], latest_id: str, now: datetime) -> bool:
    """FR-CAP-RPT-04 — finished, not cancelled, not applied, the latest, and
    finished within 7 days."""
    finished = _at(run.get("finished_at"))
    return (
        finished is not None
        and not run.get("cancelled")
        and not run.get("applied_at")
        and run["id"] == latest_id
        and now - finished <= timedelta(days=APPLY_DAYS)
    )


def _finding(row: Mapping[str, Any]) -> dict[str, Any]:
    found = row.get("finding") or {}
    return {
        "candidate_id": row["id"],
        "outcome": row["outcome"],
        "confidence": row["confidence"],
        "company_name": found.get("company_name"),
        "domain": found.get("domain"),
        "domain_from_text": bool(found.get("domain_from_text")),
        "person_name": found.get("person_name"),
        "person_email": found.get("person_email"),
        "first_at": found.get("first_at"),
        "latest_at": found.get("latest_at"),
        "message_count": int(found.get("message_count") or 0),
        "owner_replied": bool(found.get("owner_replied")),
        "reason": found.get("reason"),
        "duplicate_company": None,
    }


def _recall_rows(ws_id: str, recall: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recall rows with the subject of a thread already stored."""
    subjects: dict[tuple[str, str], str] = {}
    for mailbox_id in sorted({r["mailbox_id"] for r in recall}):
        threads = [r["thread_id"] for r in recall if r["mailbox_id"] == mailbox_id]
        for thread_id, subject in store.thread_subjects(ws_id, mailbox_id, threads).items():
            subjects[(mailbox_id, thread_id)] = subject
    return [
        {
            "thread_id": r["thread_id"],
            "latest_at": r.get("latest_at"),
            "subject": subjects.get((r["mailbox_id"], r["thread_id"])),
            "fate": r["fate"],
        }
        for r in recall
    ]


def overview(ctx: Any) -> dict[str, Any]:
    """`GET /crm/capture` — the mode, the mailboxes, the ignore list (S3)
    and the latest dry run's report with its live counts, estimated cost,
    findings and recall check (FR-CAP-RPT-02, -03)."""
    ws_id = ctx.workspace_id
    now = _now()
    mailboxes = [
        {
            "id": m["id"],
            "email": m["email"],
            "capture_source": m.get("capture_source") or rules.LABELS_ONLY,
            "capture_start_date": str(m.get("capture_start_date") or rules.DEFAULT_START)[:10],
            "backfilled_at": m.get("backfilled_at"),
        }
        for m in store.list_mailboxes(ws_id)
    ]
    latest = store.latest_dry_run(ws_id)
    dry_run = None
    if latest is not None:
        started = _at(latest["started_at"])
        assert started is not None
        spend = store.ai_spend(ws_id, started)
        cost = Decimal(str((spend.get("by_job") or {}).get(rules.JOB) or "0"))
        # The recall is kept as it goes; it is the report's once finished.
        recall = (latest.get("recall") or []) if latest.get("finished_at") else []
        dry_run = {
            "id": latest["id"],
            "started_at": str(latest["started_at"]),
            "finished_at": latest.get("finished_at"),
            "cancelled": bool(latest.get("cancelled")),
            "applied_at": latest.get("applied_at"),
            "applicable": applicable(latest, latest["id"], now),
            "counts": store.capture_report(ws_id, latest["id"]),
            "estimated_cost_usd": str(cost.quantize(Decimal("0.01"))),
            "findings": [_finding(r) for r in store.dry_run_findings(ws_id)],
            "recall": {
                "line": rules.recall_line(recall),
                "rows": _recall_rows(ws_id, recall),
            },
        }
    return CaptureOverviewOut(
        mode=rules.capture_mode(store.capture_setting(ws_id)),
        mailboxes=mailboxes,
        ignores=[],
        dry_run=dry_run,
    ).model_dump(mode="json")
