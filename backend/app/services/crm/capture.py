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
c. every outside participant on the ignore list → `ignored` (after the
   metadata read alone, PD-21);
d. message by message, as the sync's `_take` decides: a message naming an
   outside participant now a person or a company domain is known-contact
   mail; every message known → `known`, and `crm.capture_thread` files it on
   `worker-crm-sync`; otherwise only the messages naming nobody known go on
   (G-5), a known one not stored yet still filed;
e. the budget again, then the text of **only the messages left after a, b
   and d**, at most the last 12 the prompt holds (`gmail.thread_text`), in
   memory;
f. one call through `009`'s wrapper (`ai.call(..., "capture", None, ...)`):
   budgeted, logged in `crm_ai_usage`, schema-checked and masked;
g. live, a high- or medium-confidence lead or deal signal first becomes a
   Review Queue item (`review.capture_group_upsert`: one per company key
   since spec 010 R1, plan R1.2) after the duplicate check
   (FR-CAP-DIS-08: the domain, a sibling domain, the name), while the
   candidate is still pending, so a fault there leaves it to be judged
   again; low confidence is recorded only (FR-CAP-DIS-07). Then the
   decision, recorded only while the candidate is still pending on the
   message judged (`crm_capture_record`); a lead or a deal signal keeps its
   finding. Live, a judgement that makes no item takes the conversation out
   of any open item of an earlier judgement, which expires when left empty
   (FR-CAP-DIS-06, FR-CAP-GRP-07). A dry run writes
   nothing else: no item, no record (FR-CAP-RPT-01).

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
if it had no label**, after the same ignore list and known check as live
(so no known client's mail reaches Claude), storing only its fate, on the open run as each is
decided, so a run out of time resumes where it stopped; with every thread
given a fate, the run finishes. After that the job skips until a new dry
run, or until an Admin **applies** it (FR-CAP-RPT-04): its high- and
medium-confidence findings become items exactly as live ones do, with no
second Claude call (only the mailboxes live reads, never a sender on the
ignore list), and the mode becomes Live.

**Consolidation** (spec 010 R1 FR-CAP-GRP-08): every run in Dry run or
Live first regroups the open items made before 074 (one per conversation)
one per company key, before any gate but Off.

**The ignore list** (FR-CAP-DIS-03, FR-CAP-REV-03) is edited by Admins here
and grows from dismissed new leads (`services/crm/review.py`).

The text lives only in this module's local variables: never stored, logged,
queued or put in an error (FR-CAP-CHG-05, NFR-CAP-02).
"""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Final

from celery.exceptions import SoftTimeLimitExceeded

from app.config import settings
from app.domain.crm import ai_rules, email_rules
from app.domain.crm import capture as rules
from app.domain.crm import review as review_rules
from app.schemas.crm import CaptureIgnoreIn, CaptureOverviewOut, DiscoveryOutput
from app.services import supabase as db
from app.services.crm import CrmRefused, ai, email_sync, gmail, jobs, review, store
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
    # Spec 010 R1 (contracts §7.2): grouping by company.
    "consolidated",
    "rekeyed",
    "consolidate_failed",
    "items_skipped",
    "released",
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
APPLY_DAYS: Final = rules.APPLY_DAYS
#: The open stages a discovered deal signal may move (contracts §6.2).
_OPEN: Final = ("discovery", "qualification", "proposal", "negotiation")

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
    # Spec 010 R1 FR-CAP-GRP-08: after the Off check, before every other
    # gate, since it calls neither Gmail nor Claude.
    merged = _consolidate(workspace)
    off = email_sync.off_reason(ws_id)
    if off:
        raise jobs.Skipped(off, merged)
    mailboxes = _capturing(workspace)
    if not mailboxes:
        raise jobs.Skipped(rules.NO_CAPTURING_MAILBOX, merged)
    dry = mode == rules.DRY_RUN
    open_run = None
    if dry:
        open_run = store.latest_dry_run(ws_id)
        if open_run is None or open_run.get("finished_at"):
            raise jobs.Skipped(rules.DRY_RUN_FINISHED, merged)
    try:
        gmail._key()
    except gmail.NotConfigured:
        raise jobs.Skipped(rules.GMAIL_KEY_MISSING, merged) from None
    try:
        ai.gate(workspace, None)
    except jobs.Skipped as skipped:
        raise jobs.Skipped(skipped.reason, {**merged, **skipped.counts}) from None

    counts = dict.fromkeys(COUNTS, 0)
    counts.update(merged)
    ids = sorted(mailboxes)
    pending = store.pending_candidates(ws_id, ids, newest_first=not dry, limit=rules.RUN_CAP)
    scans: dict[str, set[str]] = {}
    ignores = store.list_ignores(ws_id) if pending else []
    for candidate in pending:
        if jobs.out_of_time():
            # The rest wait for the next tick, or the dry run's follow-up.
            break
        mailbox = mailboxes[candidate["mailbox_id"]]
        counts["considered"] += 1
        try:
            _judge(workspace, mailbox, candidate, scans, open_run, counts, ignores)
        except jobs.Skipped as skipped:
            # A gate closed (the budget): the run stops and keeps its counts.
            raise jobs.Skipped(skipped.reason, counts) from None
        except SoftTimeLimitExceeded:
            # OPS-2: the task's soft limit, as out of time: the run ends with
            # its counts, the candidate stays pending for the next tick.
            break
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


#: Plan R1.3 — a tie between rule reasons goes to the first.
_DROP_ORDER: Final = ("calendar", "automated", "internal_only")


def _drop_reason(
    workspace: dict[str, Any], messages: list[dict[str, Any]], scan: set[str] | tuple[str, ...]
) -> str:
    """FR-CAP-RPT-06, plan R1.3 step 3 — `_survivors`' sibling over the same
    rules, for a conversation none of whose messages survived them: why the
    in-scope messages were dropped, from headers only. Each is internal-only
    (every participant ours, or an invitation between colleagues only), a
    calendar notice, or automated mail; the conversation takes the reason
    of most of them, calendar before automated before internal on a tie."""
    tally = dict.fromkeys(_DROP_ORDER, 0)
    for m in messages:
        if not rules.in_scope(m.get("labelIds") or [], scan):
            continue
        try:
            info = email_rules.classify(
                m["headers"], workspace.get("allowed_domains") or [], m["internalDate"]
            )
        except (KeyError, TypeError, ValueError):
            raise MalformedThread() from None
        outside = [p for p in info["outside"] if email_sync._ADDRESS.match(p["email"])]
        sender = info["from_email"]
        if info["is_internal"] or (
            rules.is_invitation(m["headers"])
            and not rules.invite_parties(m["headers"], sender, outside)
        ):
            tally["internal_only"] += 1
        elif rules.is_calendar_notice(m["headers"], sender, outside):
            tally["calendar"] += 1
        else:
            tally["automated"] += 1
    return max(_DROP_ORDER, key=lambda reason: (tally[reason], -_DROP_ORDER.index(reason)))


def _known_ids(ws_id: str, kept: list[dict[str, Any]]) -> set[str]:
    """Step d, message by message as `_take` decides (`links_for`): the ids
    of the messages naming an outside participant who is a person (any of
    their emails) or has a company's domain, Trash included (PD-7). Such a
    message is the sync's to file; the rest are what made the conversation
    a candidate, and only they are judged (G-5)."""
    addresses = sorted({p["email"] for m in kept for p in m["outside"]})
    if not addresses:
        return set()
    known: set[str] = set()
    for person in store.people_by_emails(ws_id, addresses):
        for address in [person.get("primary_email"), *(person.get("additional_emails") or [])]:
            if address in addresses:
                known.add(address)
    domains = sorted({email_rules.domain_of(a) for a in addresses})
    held = {d["domain"] for d in store.list_domains(ws_id, domains=domains)}
    return {
        m["id"]
        for m in kept
        if any(
            p["email"] in known or email_rules.domain_of(p["email"]) in held for p in m["outside"]
        )
    }


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
    # Only the messages the prompt can hold are read (its last 12), so one
    # long conversation costs at most that many Gmail reads (OPS-2).
    ordered = sorted(kept, key=lambda m: (m["sent_at"], m["id"]))
    wanted = [m["id"] for m in ordered[-rules.MAX_MESSAGES :]]
    read = gmail.thread_text(mailbox["email"], wanted)
    by_id = {r["id"]: r for r in read}
    if not by_id:
        return None
    gone = set(wanted) - set(by_id)
    judged = [
        {**m, "attachments": (by_id.get(m["id"]) or {}).get("attachments") or []}
        for m in kept
        if m["id"] not in gone
    ]
    texts = {m["id"]: by_id[m["id"]]["text"] for m in judged if m["id"] in by_id}
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
    ignores: list[dict[str, Any]],
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
    if rules.ignored(rules.outside_of(kept), ignores):
        # FR-CAP-DIS-03: before any text is read.
        if _record(ws_id, candidate, {**base_values, "status": "ignored"}):
            counts["ignored"] += 1
        return
    known_ids = _known_ids(ws_id, kept)
    unknown = [m for m in kept if m["id"] not in known_ids]
    if not unknown:
        if _record(ws_id, candidate, {**base_values, "status": "known"}):
            counts["known"] += 1
            _file(ws_id, mailbox["id"], candidate["thread_id"])
        return
    if known_ids and len(store.stored_message_ids(ws_id, mailbox["id"], sorted(known_ids))) < len(
        known_ids
    ):
        # A known message the sync has not stored (its contact became known
        # after the sync passed it) is filed; the rest are judged below.
        _file(ws_id, mailbox["id"], candidate["thread_id"])
    asked = _ask(workspace, mailbox, unknown, open_run)
    if asked is None:
        if _record(ws_id, candidate, {**base_values, "status": "skipped"}):
            counts["skipped_rules"] += 1
        return
    answer, judged, choices = asked
    finding = rules.finding_of(answer, judged, mailbox_email=mailbox["email"], choices=choices)
    if finding is not None:
        # FR-CAP-DIS-08: kept with the finding, so the report shows it too.
        finding["duplicate_company_id"] = duplicate_of(ws_id, finding)
    if not dry:
        # Live: the item is built and stored while the candidate is still
        # pending, so a fault in the duplicate check, the owner or the upsert
        # leaves it pending to be judged again; a decision recorded first
        # would never be picked up again. The upsert is idempotent per dedupe
        # key and source version, so the retry makes no second item.
        item = (
            item_for(
                workspace,
                mailbox,
                thread_id=candidate["thread_id"],
                message_id=candidate["latest_message_id"],
                outcome=answer["outcome"],
                confidence=answer["confidence"],
                finding=finding,
                generated_at=_now().isoformat(),
            )
            if finding is not None
            else None
        )
        if item is not None:
            made = store_items(ws_id, [item])
            counts["items"] += sum(1 for m in made if m.get("created"))
            counts["items_skipped"] += sum(1 for m in made if m.get("skipped"))
            counts["released"] += sum(int(m.get("released") or 0) for m in made)
        else:
            # FR-CAP-DIS-06, FR-CAP-GRP-07: judged again from the start, the
            # conversation makes no item now, so it leaves any open item of
            # an earlier judgement (its evidence stale); an item left with
            # no conversation expires.
            released = store.capture_release_thread(
                ws_id, rules.source_key(mailbox["id"], candidate["thread_id"]), ""
            )
            counts["released"] += int(released.get("removed") or 0)
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
        # A message arrived meanwhile: it is judged again, from the start
        # (live, refreshing the item just stored).
        return
    counts["judged"] += 1
    if finding is not None and answer["confidence"] == "low":
        # FR-CAP-DIS-07: recorded, never an item; the report lists it apart.
        counts["low_confidence"] += 1


# ---------------------------------------------------------------------------
# Items — FR-CAP-DIS-07, -08, -09, FR-CAP-REV-01, -04; plan §5.8, §5.10
# ---------------------------------------------------------------------------


def _live_company(ws_id: str, company_id: str) -> bool:
    row = base.by_id(ws_id, "crm_companies", [company_id]).get(company_id)
    return row is not None and not row.get("deleted_at")


def duplicate_of(ws_id: str, finding: Mapping[str, Any]) -> str | None:
    """FR-CAP-DIS-08 — an existing company the lead is: one holding the
    finding's domain (one Claude chose from the text included), one holding
    a sibling domain (`acme.io` for `acme.com`), or one whose name is alike
    (`007`'s duplicate check). Trashed companies are not offered."""
    domain = finding.get("domain")
    if domain:
        for row in store.list_domains(ws_id, domains=[domain]):
            if _live_company(ws_id, row["company_id"]):
                return row["company_id"]
        name = rules.registrable_name(domain)
        # Only the domains of that name are read, never the whole list.
        known = store.domains_named(ws_id, name) if name else []
        for sibling in rules.sibling_domains(domain, [d["domain"] for d in known]):
            company_id = next(d["company_id"] for d in known if d["domain"] == sibling)
            if _live_company(ws_id, company_id):
                return company_id
    name = finding.get("company_name")
    if name:
        alike = base.duplicates(
            SimpleNamespace(workspace_id=ws_id), "company", name=name, domain=None, email=None
        )["similar"]
        if alike:
            return alike[0]["id"]
    return None


def store_items(ws_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spec 010 R1 (FR-CAP-GRP-02, -07; plan R1.2): each item through
    `review.capture_group_upsert` with its one conversation entry, so a
    company keeps one open item, the conversation leaves any other open
    capture item (one made before a duplicate check linked it to a company,
    before the company went, or before a re-judgement turned the lead into
    a deal signal), and a conversation a decided item listed is skipped. A
    deal-document entry is the sync's and is never moved. The merge is in
    SQL, so the order items arrive in does not matter. `[{id, created,
    skipped, released}]`."""
    made = []
    for item in items:
        [entry] = (item.get("source_link") or {}).get("threads") or [None]
        made.append(review.capture_group_upsert(ws_id, item, entry))
    return made


def _consolidate(workspace: Mapping[str, Any]) -> dict[str, int]:
    """FR-CAP-GRP-08 (PD-26, PD-40) — the open capture items made before
    074, one per conversation, regrouped one per company key: each keyed
    from its conversation's stored finding when the candidate still has
    one, else from its suggestion; `crm_capture_consolidate` re-reads every
    member under the capture lock, so a decision made meanwhile is never
    touched. Its counts, non-zero only; a failure is logged and counted, and
    the run goes on (the next tick tries again)."""
    ws_id = workspace["id"]
    try:
        items = store.open_capture_items(ws_id)
        threads: dict[str, set[str]] = {}
        for item in items:
            link = item.get("source_link") or {}
            if str(item.get("source_key") or "").startswith("gmail:") and link.get("thread_id"):
                threads.setdefault(str(link.get("mailbox_id")), set()).add(link["thread_id"])
        findings: dict[tuple[str, str], dict[str, Any]] = {}
        for mailbox_id, ids in sorted(threads.items()):
            for row in store.candidates_for_threads(ws_id, mailbox_id, sorted(ids)):
                if row.get("finding"):
                    findings[(mailbox_id, row["thread_id"])] = row["finding"]
        plan = rules.consolidation_plan(items, findings)
        if not plan:
            return {}
        companies = base.by_id(
            ws_id, "crm_companies", {g["record_id"] for g in plan if g.get("record_id")}
        )
        for group in plan:
            company = companies.get(group.get("record_id") or "")
            if company and company.get("name"):
                group["company_label"] = str(company["name"])[:200]
        done = store.capture_consolidate(ws_id, plan)
    except Exception as exc:  # noqa: BLE001 - the run goes on; the next tick retries
        logger.warning(
            '{"event": "crm_capture_consolidate_failed", "workspace": "%s", "error": "%s"}',
            workspace.get("key", "-"),
            type(exc).__name__,
        )
        return {"consolidate_failed": 1}
    out = {"consolidated": int(done.get("expired") or 0), "rekeyed": int(done.get("rekeyed") or 0)}
    logger.info(
        '{"event": "crm_capture_consolidated", "workspace": "%s", "groups": %d, "expired": %d}',
        workspace.get("key", "-"),
        int(done.get("groups") or 0),
        out["consolidated"],
    )
    return {k: v for k, v in out.items() if v}


def _owner(workspace_id: str, mailbox: Mapping[str, Any]) -> str | None:
    """FR-CAP-REV-04 — the mailbox owner when an active Admin or Member,
    else the Admins (PD-5)."""
    roles = {m["user_id"]: m["role"] for m in store.list_members(workspace_id)}
    active = {p["id"] for p in db.list_profiles(include_crm_only=True) if p.get("is_active")}
    return review_rules.owner_for(
        "new_lead", roles=roles, active=active, mailbox_owner=mailbox.get("owner_id")
    )


def item_for(
    workspace: Mapping[str, Any],
    mailbox: Mapping[str, Any],
    *,
    thread_id: str,
    message_id: str,
    outcome: str,
    confidence: str,
    finding: Mapping[str, Any],
    generated_at: str | None,
) -> dict[str, Any] | None:
    """The Review Queue item a judged conversation makes (plan §5.10), live
    or from an applied dry run alike; None for low confidence. The
    duplicate check is made again, since records may have changed."""
    if confidence == "low" or outcome not in rules.FINDING_OUTCOMES:
        return None
    ws_id = workspace["id"]
    duplicate = duplicate_of(ws_id, finding)
    open_deals: list[dict[str, Any]] = []
    if duplicate and outcome == "deal_signal":
        open_deals = [
            d for d in store.deals_of_companies(ws_id, [duplicate]) if d["stage"] in _OPEN
        ]
    return rules.to_item(
        {**finding, "duplicate_company_id": duplicate},
        outcome=outcome,
        confidence=confidence,
        mailbox_id=mailbox["id"],
        thread_id=thread_id,
        message_id=message_id,
        owner_id=_owner(ws_id, mailbox),
        model=ai_rules.CRM_AI_MODEL,
        generated_at=generated_at,
        duplicate_company_id=duplicate,
        open_deals=open_deals,
    )


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
    ignores: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """A labelled conversation judged as if it had no label: `(fate, latest
    message time)`. Nothing is stored for it but the fate. The reasons come
    in the decision order of plan R1.3 (FR-CAP-RPT-06), from headers and
    records only until Claude: Gmail returned it empty or malformed
    (`not_judged`); no message in Primary or Sent; every message dropped by
    the rules (`_drop_reason`); the ignore list (`ignored`); the known-
    contact check, so a conversation whose every message names someone in
    the CRM now is never read (`known_now`, unless its record predates it:
    `filed_known`), and of the rest only the messages naming nobody known
    are judged (G-5, NFR-CAP-02), a failure on it `not_judged`."""
    ws_id = workspace["id"]
    fate = functools.partial(rules.recall_fate, in_capture=True, survived=True, known_before=False)
    messages = gmail.thread_meta(mailbox["email"], thread_id).get("messages") or []
    if not messages or any(not m.get("id") for m in messages):
        return fate(failed=True), None
    try:
        times = [email_rules.sent_at(m["internalDate"]) for m in messages if m.get("internalDate")]
    except (TypeError, ValueError):
        # A date Gmail sent that is not a number: malformed, as in `_survivors`.
        return fate(failed=True), None
    latest = max(times).isoformat() if times else None
    if not any(rules.in_scope(m.get("labelIds") or [], rules.CAPTURED) for m in messages):
        return fate(in_capture=False), latest
    try:
        kept = _survivors(workspace, messages, rules.CAPTURED)
        if not kept:
            reason = _drop_reason(workspace, messages, rules.CAPTURED)
            return fate(survived=False, skip_reason=reason), latest
    except MalformedThread:
        return fate(failed=True), latest
    if rules.ignored(rules.outside_of(kept), ignores):
        # FR-CAP-DIS-03: before any text is read.
        return fate(ignored=True), latest
    first = min(times) if times else min(m["sent_at"] for m in kept)
    if _known_before(ws_id, rules.outside_of(kept), first):
        return fate(known_before=True), latest
    known_ids = _known_ids(ws_id, kept)
    unknown = [m for m in kept if m["id"] not in known_ids]
    if not unknown:
        # G-5: a known client's mail never reaches Claude, even for recall.
        return fate(known_now=True), latest
    try:
        asked = _ask(workspace, mailbox, unknown, open_run)
    except ai.AiFailed as failed:
        if not rules.failure_counts(failed):
            raise
        asked = None
    if asked is None:
        # Claude failed on it, or every message was deleted meanwhile.
        return fate(failed=True), latest
    answer = asked[0]
    # `routed` is I3's (T443: `_academy_target() is not None` for an Academy
    # finding, plan R1.3 step 7 and R1.4); until then an Academy finding
    # counts as found.
    return (
        fate(
            outcome=answer["outcome"],
            confidence=answer["confidence"],
            business=answer.get("business"),
            routed=None,
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
    their start date with its fate (at most 500 rows, then the marker row
    `{"capped": true}` when a labelled conversation is left unchecked,
    plan R1.3); None when the run's
    time ran out first. Each fate is kept on the open run as it is decided
    (`crm_capture_recall_add`), and a thread that has one already is not
    read or judged again, so the next tick resumes where this one stopped."""
    ws_id = workspace["id"]
    rows: list[dict[str, Any]] = [r for r in open_run.get("recall") or [] if r.get("thread_id")]
    done = {(r["mailbox_id"], r["thread_id"]) for r in rows}
    ignores = store.list_ignores(ws_id)
    for mailbox in sorted(mailboxes.values(), key=lambda m: m["email"]):
        for thread_id in _labelled_threads(mailbox, now):
            if (mailbox["id"], thread_id) in done:
                continue
            if len(rows) >= rules.RECALL_CAP:
                # FR-CAP-RPT-07: stopped at the cap with this one unchecked.
                return [*rows, {"capped": True}]
            if jobs.out_of_time():
                return None
            try:
                fate, latest = _fate(workspace, mailbox, thread_id, open_run, ignores)
            except SoftTimeLimitExceeded:
                # OPS-2: as out of time; the next tick resumes here.
                return None
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
    return rules.apply_refusal(run, latest_id, now) is None


def apply(ctx: Any, run_id: str) -> dict[str, Any]:
    """`POST /crm/capture/dry-runs/{id}/apply` (contracts §2.3) — the run's
    high- and medium-confidence findings become Review Queue items exactly
    as live ones do, with no Claude call; then `crm_capture_apply` marks it
    applied and sets the mode to Live. Repeating it after a failure in
    between is safe: the upsert is idempotent per dedupe key."""
    ws_id = ctx.workspace_id
    run = store.get_dry_run(ws_id, run_id) if base._uuid(run_id) else None
    latest = store.latest_dry_run(ws_id)
    refused = rules.apply_refusal(run, (latest or {}).get("id"), _now())
    if refused is not None:
        raise CrmRefused(*refused)
    # As live: only the mailboxes the job reads (NFR-CAP-05), and never a
    # sender put on the ignore list since the run (FR-CAP-DIS-03).
    mailboxes = _capturing(ctx.workspace)
    ignores = store.list_ignores(ws_id)
    items = []
    for row in store.dry_run_findings(ws_id):
        mailbox = mailboxes.get(row["mailbox_id"])
        if mailbox is None or not row.get("finding"):
            continue
        email = row["finding"].get("person_email")
        if email and rules.ignored([{"email": email}], ignores):
            continue
        item = item_for(
            ctx.workspace,
            mailbox,
            thread_id=row["thread_id"],
            message_id=row["judged_message_id"] or row["latest_message_id"],
            outcome=row["outcome"],
            confidence=row["confidence"],
            finding=row["finding"],
            generated_at=str(row["judged_at"]) if row.get("judged_at") else None,
        )
        if item is not None:
            items.append(item)
    made = store_items(ws_id, items)
    done = store.apply_dry_run(actor(ctx), run_id)
    logger.info(
        '{"event": "crm_capture_dry_run_applied", "workspace": "%s", "items": %d}',
        ctx.workspace["key"],
        len(items),
    )
    return {
        "applied_at": done.get("applied_at"),
        "items_created": sum(1 for m in made if m.get("created")),
        "mode": rules.LIVE,
    }


def _ignore_out(row: Mapping[str, Any], names: Mapping[str, str] | None = None) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "kind": row["kind"],
        "value": row["value"],
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }
    if names is not None:
        out["from_item_id"] = row.get("from_item_id")
        out["created_by_name"] = names.get(row.get("created_by") or "")
    return out


def find_ignore(ws_id: str, kind: str, value: str) -> dict[str, Any] | None:
    """The entry for this address or domain, if the list has it."""
    return next(
        (i for i in store.list_ignores(ws_id) if i["kind"] == kind and i["value"] == value), None
    )


def write_ignore(
    ctx: Any, kind: str, value: str, *, from_item_id: str | None = None
) -> dict[str, Any]:
    """One entry, written through `crm_apply` as the person (audited
    `capture_ignore.created`)."""
    values: dict[str, Any] = {"kind": kind, "value": value}
    if from_item_id:
        values["from_item_id"] = from_item_id
    done = store.apply(
        actor(ctx), [{"op": "insert", "table": "crm_capture_ignores", "values": values}]
    )
    return done[0]["row"]


def add_ignore(ctx: Any, body: CaptureIgnoreIn) -> dict[str, Any]:
    """`POST /crm/capture/ignores` (contracts §2.4) — `{ignore}`; 422 for a
    malformed value, a public webmail domain or one of the workspace's own
    domains, 409 when it is listed already."""
    value = rules.ignore_value(body.value)
    why = rules.ignore_refusal(body.kind, value, ctx.workspace.get("allowed_domains") or [])
    if why:
        raise CrmRefused(422, why)
    if find_ignore(ctx.workspace_id, body.kind, value) is not None:
        raise CrmRefused(409, rules.ALREADY_IGNORED)
    return {"ignore": _ignore_out(write_ignore(ctx, body.kind, value))}


def remove_ignore(ctx: Any, ignore_id: str) -> None:
    """`DELETE /crm/capture/ignores/{id}` (contracts §2.5). Candidates already
    `ignored` stay so until a new message arrives."""
    found = next((i for i in store.list_ignores(ctx.workspace_id) if i["id"] == ignore_id), None)
    if found is None:
        raise CrmRefused(404, rules.NO_SUCH_IGNORE)
    store.apply(actor(ctx), [{"op": "delete", "table": "crm_capture_ignores", "id": ignore_id}])


def _finding(row: Mapping[str, Any], companies: Mapping[str, Any]) -> dict[str, Any]:
    found = row.get("finding") or {}
    duplicate = companies.get(found.get("duplicate_company_id") or "")
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
        "duplicate_company": {"id": duplicate["id"], "name": duplicate.get("name")}
        if duplicate
        else None,
    }


def _recall_rows(ws_id: str, recall: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recall rows with the subject of a thread already stored; marker
    rows (no `thread_id`, plan R1.3) are not conversations."""
    recall = [r for r in recall if r.get("thread_id")]
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
    """`GET /crm/capture` — the mode, the mailboxes, the ignore list and
    the latest dry run's report with its live counts, estimated cost,
    findings and recall check (FR-CAP-RPT-02, -03)."""
    ws_id = ctx.workspace_id
    now = _now()
    boxes = store.list_mailboxes(ws_id)
    mailboxes = [
        {
            "id": m["id"],
            "email": m["email"],
            "capture_source": m.get("capture_source") or rules.LABELS_ONLY,
            "capture_start_date": str(m.get("capture_start_date") or rules.DEFAULT_START)[:10],
            "backfilled_at": m.get("backfilled_at"),
        }
        for m in boxes
    ]
    latest = store.latest_dry_run(ws_id)
    names = {p["id"]: p["display_name"] for p in db.list_profiles(include_crm_only=True)}
    ignores = [_ignore_out(i, names) for i in store.list_ignores(ws_id)]
    dry_run = None
    if latest is not None:
        findings = store.dry_run_findings(ws_id)
        companies = base.by_id(
            ws_id,
            "crm_companies",
            {(r.get("finding") or {}).get("duplicate_company_id") for r in findings} - {None},
        )
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
            "findings": [_finding(r, companies) for r in findings],
            "recall": {
                "line": rules.recall_line(recall),
                "rows": _recall_rows(ws_id, recall),
                "summary": rules.recall_summary(recall),
            },
        }
    live_allowed = store.has_applied_dry_run(ws_id) and any(
        m["is_active"] and m.get("capture_source") == rules.PRIMARY_SENT for m in boxes
    )
    return CaptureOverviewOut(
        mode=rules.capture_mode(store.capture_setting(ws_id)),
        live_allowed=live_allowed,
        mailboxes=mailboxes,
        ignores=ignores,
        dry_run=dry_run,
    ).model_dump(mode="json")
