# Automatic Email Capture — Plan

| | |
|---|---|
| **Feature** | `010-email-capture` |
| **Branch** | `010-email-capture`, from `main` at `8ad5904` (009 merged as `3a3d8e3`) |
| **Date** | 20 October 2026 |
| **Spec** | [`spec.md`](spec.md) (operative); [`source.md`](source.md) (owner's brief, behaviour only) |
| **Companions** | [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md), [`tasks.md`](tasks.md) |
| **Builds on** | `007-crm` email sync as built (`services/crm/email_sync.py`, `gmail.py`, `domain/crm/email_rules.py`, migration 039); `009-crm-intelligence` as built (`services/crm/ai.py`, `review.py`, `summaries.py`, `jobs.py`, `tasks/crm_ai.py`, migrations 060–070) |
| **Constitution** | v1.3.0 |

This is the HOW for `spec.md`. Where this file and the spec disagree, the spec
wins and this file is wrong. FR, NFR and E ids are the spec's. The layout
follows `specs/009-crm-intelligence/plan.md`.

---

## 1. Summary

Capture changes three things: which Gmail messages the existing sync reads,
what it does with a message from someone it does not know, and a new hourly
job that asks Claude about those unknown conversations. The design adds no
service, no queue and no dependency.

Six choices hold the rest together:

1. **Capture is a wider label set, not a new sync.** For a mailbox on
   *Primary and Sent*, `email_sync.sync_page` scans the configured labels
   **plus** Gmail's `CATEGORY_PERSONAL` and `SENT`. Backfill, history,
   removal and reconcile already work by label, so FR-CAP-SRC-02 and -08
   come from the code that exists. The configured labels stay in
   `crm_mailboxes.labels`; the two system labels are added in code from
   `capture_source`, because `mailbox_refusal` refuses system labels on
   purpose (§5.3).
2. **Hand labels win.** A message that carries a configured label goes
   through today's path unchanged: stored, linked, automation 2, bulk mail
   stored with `is_auto`. Only a message reached *through* Primary or Sent
   alone gets the new rules: bulk skip, no automation 2, and "unknown
   becomes a candidate" (FR-CAP-SRC-03, -05; spec §3).
3. **The sync stores no text and no addresses for unknown mail.** An unknown
   conversation leaves one `crm_capture_candidates` row: mailbox, thread id,
   latest message id and time (FR-CAP-SRC-05). Everything else the job
   needs it reads again from Gmail when it runs.
4. **Discovery is one job on `worker-crm-ai`, through `009`'s one call
   wrapper.** `crm_ai.capture` runs hourly, reads pending candidates, applies
   rules, the ignore list and the known check from a **metadata** thread read,
   and reads text only for what passes (FR-CAP-DIS-02, -03, -04; NFR-CAP-02).
   It calls `ai.call(..., phase=None)`: no `009` phase has to be on, but the
   rates and budget gates still apply (FR-CAP-DIS-01, §5.6).
5. **Every finding is a Review Queue item; approval is a person's edit.** Two
   new item types, `new_lead` and `deal_signal`, are approved through
   `records.create`, `records.update`, `deals.create` and `deals.move`, as
   `009`'s items are (FR-CAP-REV-02; `009` CT-1).
6. **Off by default, three switches.** `capture_source = labels` on every
   mailbox, `capture.mode = off` in every workspace, both seeded by migration.
   With both as seeded, every existing sync test passes unchanged
   (spec §13 S1 line 1; NFR-CAP-05).

### 1.1 Slices

| Slice | Delivers | Migration | Uses Claude |
|---|---|---|---|
| **S1** | Capture source and start date on mailboxes; Primary and Sent scanning; bulk skip; automation 2 limited to labelled mail; discovery candidates; deal-document search and `deal_signal` items from the sync; Settings → Mailboxes fields | `071_crm_capture_sync.sql` | No |
| **S2** | `capture` setting (Off, and Dry run through its button; Live refused); `crm_ai.capture` job (dry-run mode); thread reads on `worker-crm-ai`; Claude structured output; decision records; dry runs (cancelled when the mode leaves Dry run); recall check; report; `crm.capture_thread` to file a conversation that became known; Settings → Email capture (mode, dry run, report) | `072_crm_capture_discovery.sql` | Yes |
| **S3** | Live mode (only after an applied dry run); `new_lead` items; duplicate check; ignore list (Settings and dismissal); approval for `new_lead` and Claude's `deal_signal` (filing through S2's `crm.capture_thread`); apply a dry run; retention purge | `073_crm_capture_live.sql` | Yes |
| **D** | Docs, help guides, `007` and `009` spec amendments, release | none | — |

Each slice ends with every gate green (tasks.md "Gates") and every new switch
off in a freshly reset database. S1 is useful alone: known-client mail is
filed with no label. S2 is useful alone: the report.

---

## 2. Technical context

| | |
|---|---|
| **Language/Version** | Python 3.11+ (CI 3.13), TypeScript 5 strict |
| **Backend** | FastAPI (sync handlers), Pydantic v2, supabase-py (service role), Celery + Redis, raw `httpx` for Gmail and Claude |
| **Frontend** | Next.js 16 App Router, shadcn base-nova (read `frontend/node_modules/next/dist/docs/` before writing pages) |
| **Storage** | Supabase Postgres: three new tables, four columns on `crm_mailboxes`, CHECK changes on `crm_review_items`, `crm_workspace_settings`, `crm_job_runs`, `crm_ai_usage` |
| **External** | Gmail API, read-only (`gmail.readonly`): `messages.list` with `q` (search), `threads.get` (`format=metadata` and `format=full`); Anthropic Messages API through `009`'s client |
| **Testing** | pytest with `tests/crm_fake.py`, `tests/crm_ai_fakes.py`, `FakeGmail` in `tests/test_crm_email_sync.py` extended; `@pytest.mark.db` tests with `CRM_DB_TESTS=1` |
| **Target** | Railway `api`, `worker`, `beat`, `worker-crm-sync` (exists only after the owner's 007 step zero), `worker-crm-ai`; Netlify; Supabase |
| **Volume** | One mailbox (E2). 1 April–20 October 2026: ~1,387 Primary conversations, 249 with Sent mail; the dry run judges an estimated 400–600 candidates (spec §1 sample: 36% internal, 7% automated and 20% known are filtered out first) |
| **Cost** | NFR-CAP-01: < US$5 dry run, < US$1/month live; enforced only by `009`'s budget gate |

### 2.1 Dependencies

None added. `pyproject.toml` and `package.json` are unchanged.

### 2.2 New configuration

| Setting | Where | Default | Meaning |
|---|---|---|---|
| `crm_mailboxes.capture_source` | per mailbox, Settings → Mailboxes | `labels` | `labels` or `primary_sent` (FR-CAP-SRC-01) |
| `crm_mailboxes.capture_start_date` | per mailbox | `2026-04-01` | Backfill start for `primary_sent` (FR-CAP-SRC-07, E11) |
| `capture` workspace setting | `crm_workspace_settings`, Settings → Email capture | `{"mode": "off"}` | `off`, `dry_run`, `live` (FR-CAP-DIS-01) |
| `capture_rules.CLASSIFIER_VERSION` | constant in `domain/crm/capture.py` | `"010.1"` | Stored with every decision (FR-CAP-DIS-06) |
| `capture_rules.RETENTION_DAYS` | constant | `365` | FR-CAP-DIS-11, E12 |

No new environment variable. `GOOGLE_SERVICE_ACCOUNT_JSON`, `ANTHROPIC_API_KEY`
and the two rate settings are `009`'s, on the services `009` names.

---

## 3. Constitution check

| # | Question | Answer |
|---|---|---|
| 1 | Does it serve a journey? | The Seller's Journey: replies owed, clients gone quiet and New → Contacted become right without labelling (E1), and new leads reach the seller's queue without typing. |
| 2 | Security-First and Clear Boundaries? | Yes. Gmail stays `gmail.readonly` (E4). New tables: RLS on, no policies, no browser grants; new functions `crm_`-prefixed with EXECUTE revoked from `PUBLIC, anon, authenticated`. Every route goes through `api/crm/deps.py`; capture configuration is Admin only. The API holds neither the Gmail nor the Claude key: it only queues work. Message text exists only in local variables on `worker-crm-ai`; nothing about a skipped, *other business* or *not relevant* conversation is stored beyond its decision row (NFR-CAP-02). Outsider text is quoted as data (`009` FR-CRMAI-LLM-04). |
| 3 | Data integrity? | Three numbered migrations (071–073). Candidate marking, decision recording, the dry-run start and apply are single plpgsql functions. Records are created only through the manual services after a person approves (E3), except known-contact filing (E5), which is today's `crm_store_emails`. |
| 4 | Simplicity? | No new service, queue, table for "runs" beyond one small dry-run log, MCP tool or dependency. Scanning reuses label sync; the job reuses `009`'s wrapper, job log, review queue and fakes. |
| 5 | Observability? | Every page and job run writes `crm_job_runs` counts (NFR-CAP-04); structured log lines carry ids and counts only; three failures in a row alert through `jobs.STREAK`. |

---

## 4. Complexity tracking

| # | Exception | Why needed | Simpler alternative rejected because |
|---|---|---|---|
| CT-1 | **A second module reads message text** (amends `009` CT-2 and FR-CRMAI-CHG-01) | FR-CAP-DIS-04 needs the words of unknown conversations; FR-CAP-CHG-05 allows it. The read is `gmail.thread_text()`, called only from `services/crm/capture.py` on `worker-crm-ai`, pinned by an import-graph test like `TestOnlyTheSummariser`. It reads unstored messages by design; it runs only after the rules, ignore list and known check passed on a metadata read. | Judging on subjects and senders only: cannot tell a lead from a vendor pitch (E7). |
| CT-2 | **Approving a `new_lead` is not one transaction** (as `009` CT-1) | Approval goes through `records.create` (company), `records.create` (person) and `records.update` (lead stage), each its own transaction, then queues `crm.capture_thread` (the API has no Gmail key). A partial failure reopens the item with the refusal; a retry reuses a company that already holds the domain instead of failing on the clash (§5.9). | One plpgsql function re-implementing record creation: a second copy of the field, domain and email rules. |
| CT-3 | **The sync worker runs one Gmail search per document kind per sync** | FR-CAP-SRC-06 asks for attachment names; `format=metadata` carries no parts, and Q-29 keeps the sync at metadata. Gmail's own `filename:` search finds them and returns ids only. One query per kind (five) tells the item which kind matched without reading the message. | `format=full` on the sync worker: reads bodies on a service that must not. |

---

## 5. Architecture

### 5.1 Workers, queues and beat

```text
beat (IST)
 ├─ */15  crm.email_sync_tick ─▶ default ─▶ worker ──enqueue──▶ crm_sync ─▶ worker-crm-sync (concurrency 1)
 │                                                               crm.email_sync_page   (S1: Primary+Sent scan,
 │                                                                                      bulk skip, candidates,
 │                                                                                      deal-document search)
 │                                                               crm.capture_thread    (S2: file one conversation
 │                                                                                      that became known; S3: also
 │                                                                                      after approval)
 ├─ :07   crm_ai.capture ──────▶ crm-ai ─▶ worker-crm-ai (concurrency 1)
 │                                          crm_ai.capture   (S2/S3: candidates → rules → ignore → known →
 │                                                            Claude → decision → item); in dry run, chains
 │                                                            itself while candidates remain (§5.7)
 └─ 01:10 crm.purge_trash ─────▶ default ─▶ worker   (S3: + capture decision retention)
```

- **Classification is on `worker-crm-ai` only** (FR-CAP-DIS-02). It holds
  the Claude key and the Gmail key with text access (`009` deployment §8).
- **Known-contact capture and the deal-document search stay on
  `worker-crm-sync`**, inside the existing lock and page chain (E9).
- `crm.capture_thread` is routed to `crm_sync` by an explicit
  `task_routes["crm.capture_thread"]` entry, so the shared worker never runs
  it; it is enqueued by `crm_ai.capture` (a candidate that became known,
  from S2) and by the API (approval, S3). It ships in S2 with its route, so
  S2 never queues a task nobody runs. It is not a `JOBS` entry; a failure logs
  `crm_capture_thread_failed` with ids and the exception type.
- Beat entry `"crm-ai-capture": {"task": "crm_ai.capture", "schedule":
  crontab(minute=7)}` in a `# Spec 010` block placed last in `worker.py`.
  Minute 7 keeps it off `009`'s `*/15` ticks.
- `crm_ai.capture` has `soft_time_limit=540`, below Celery's 600-second hard
  limit. `SoftTimeLimitExceeded` is treated like `jobs.out_of_time()`: the
  run ends `ok` with its counts, the conversation being judged stays
  pending, and a dry run chains its follow-up. (`ai.call` passes it through
  instead of turning it into a `SafeError`.) Without it, one slow
  conversation past 600 s had the worker child killed and its `crm_job_runs`
  row left `running`.

### 5.2 Layering

| Layer | New | Changed |
|---|---|---|
| `app/domain/crm/` | `capture.py` (pure: modes, `capture_refusal`, `capture_mode`, `scan_labels`, `capture_after`, `is_bulk`, `DEAL_DOC_QUERIES`, `deal_doc_stage`, candidate filters, ignore matching, owner-replied, prompt parts and trimming, `INSTRUCTION`, output → finding, duplicate candidates, lead stage, item builders, recall fates, `CLASSIFIER_VERSION`, refusal sentences) | `email_rules.py` (`HEADERS` + `List-Unsubscribe`; `mailbox_refusal` gains the start-date rule), `settings.py` (`VALIDATORS["capture"]`), `review.py` (`TYPES`, `owner_for`, `dedupe_key` for a thread target), `ai_rules.py` (`USAGE_JOBS` + `capture`) |
| `app/services/crm/` | `capture.py` (the job, dry runs, apply, report, ignores, `capture_thread`) | `email_sync.py` (§5.3–5.5), `gmail.py` (`search`, `thread_meta`, `thread_text`; `_get` retries 429/5xx, §5.13), `store.py` (mailbox columns, candidate, ignore, dry-run RPC wrappers), `ai.py` (`phase: str \| None`), `review.py` (`new_lead`, `deal_signal`, dismiss with ignore, null targets), `jobs.py` (`JOBS["capture"]`) |
| `app/api/crm/` | `capture.py` (`/crm/capture...`) | `email.py` (mailbox fields, cursor reset), `settings.py` (`capture` special case), `review.py` (dismiss body), `jobs.py` (`JobName`), `__init__.py` (`ROUTERS`) |
| `app/schemas/crm.py` | `CaptureSettingsValue`, `DiscoveryOutput`, `CaptureIgnoreIn`, `CaptureOverviewOut`, `ReviewDismissIn` | `MailboxIn`, `MailboxUpdate`, `ReviewItemType` |
| `app/tasks/` | — | `crm_ai.py` (`crm_ai.capture`), `crm.py` (`crm.capture_thread`; `_purge` adds capture retention; docstring job table) |
| `app/mcp/crm.py` | — | `crm_review_decide` gains optional `ignore`; docstring names the two types. Tool count stays 21 |
| `app/worker.py` | — | route for `crm.capture_thread`; beat `crm-ai-capture` |

### 5.3 S1 — which messages are read (FR-CAP-SRC-01, -02, -07, -08; CHG-01, -03)

In `email_sync.sync_page`:

```python
configured = _label_ids(mailbox, required=mailbox["capture_source"] == "labels")
scan = rules_capture.scan_labels(configured, mailbox["capture_source"])
#   labels       -> configured
#   primary_sent -> configured + ["CATEGORY_PERSONAL", "SENT"] (deduplicated, order kept)
ours = set(scan)
```

- The backfill walks `scan` label by label and `_reconcile` lists every label
  in `scan`. The window is `capture.capture_after(mailbox, now)`:
  `capture_start_date` at 00:00 IST for `primary_sent`, else
  `email_rules.backfill_after(now)` (90 days). Both call sites (today's lines
  455 and 530) use it.
- `_label_ids` raises `LabelsMissing` only in `labels` mode. In
  `primary_sent` a mailbox whose configured labels are all gone still scans
  Primary and Sent, with the existing warning log line.
- History: `changed_messages(history, ours, trash_leaves=...)`. A message
  moved into Primary gains `CATEGORY_PERSONAL` (labelsAdded) and is taken;
  one moved out loses it and is dropped unless it still carries `SENT` or a
  configured label (FR-CAP-SRC-08). **Trash and Spam** (`primary_sent` only,
  `trash_leaves=True`): deleting in Gmail moves a message to Trash and keeps
  its `CATEGORY_PERSONAL`/`SENT` labels, so a `labelsAdded` of `TRASH` or
  `SPAM` counts as removed, and a `labelsRemoved` of `TRASH` or `SPAM` counts
  as added when the message is otherwise in `ours`. In `labels` mode the
  argument is `False` and the function behaves exactly as today (NFR-CAP-05).
  The reconcile already agrees: `list_messages` sends `includeSpamTrash=false`.
- `capture.in_scope(label_ids, scan)`: carries a label of `scan` **and**
  neither `TRASH` nor `SPAM`. `_take`, `crm_ai.capture`, the recall check and
  the deal-document search all use it (FR-CAP-SRC-02 as amended).
- `_take(ws, mailbox, ids, ours, configured)` gains the `configured` set
  (§5.4).
- Cursor reset (FR-CAP-SRC-07): `PATCH /crm/mailboxes/{id}` clears
  `history_id`, `page_token`, `backfilled_at` when `email`, `labels`,
  `capture_source` or `capture_start_date` changes. The next page re-reads
  with reconcile on (`last_synced_at` is set), as a label change does today.
  A change of `capture_source` or `capture_start_date` also clears
  `docs_checked_at`, so the deal-document search starts again at most 14
  days back (PD-19).
- **Switching back to *Labels only*** (PD-3). The same PATCH, when
  `capture_source` goes from `primary_sent` to `labels`, also sets
  `reconcile_after` to `reconcile_window(mailbox)`: the old
  `capture_start_date` at 00:00 IST, or an earlier `reconcile_after`. A
  `primary_sent` mailbox whose start date is moved later gets the same
  value, so mail captured under the earlier start date is still the switch
  back's to remove. `_reconcile` compares from `min(capture_after(mailbox,
  now), reconcile_after)`; from a fixed day (a start date or
  `reconcile_after`) the day's margin is on the listing, not the
  comparison, so mail stored on that day is compared too
  (`capture.reconcile_bounds`). Captured mail older than 90 days is removed
  too, and the page that finishes that reconcile on a *Labels only*
  mailbox writes `reconcile_after = NULL` with the cursor
  (`MAILBOX_PROGRESS`; skipped by the audit like the cursor); while
  capturing it is kept.

### 5.4 S1 — what `_take` does with each message (FR-CAP-SRC-03, -04, -05; CHG-02)

Order, with the new steps in **bold**:

1. Skip stored ids; fetch metadata; drop unless `capture.in_scope(labelIds, ours)` (in `labels` mode: a label in `ours`, as today).
2. **`labelled = bool(configured & labelIds)`.**
3. `classify(...)`; internal-only → `skipped_internal` (unchanged, stores
   nothing).
4. **Not labelled and `capture.is_bulk(headers, sender, subject)` →
   `skipped_bulk`, `continue`.** `is_bulk` is true for any `List-Unsubscribe`
   header; `Precedence` in `bulk, list, junk, auto_reply`; `Auto-Submitted`
   other than `no`; `X-Autoreply`/`X-Autorespond`; a no-reply sender (local
   part matching `no-?reply|do-?not-?reply|notifications?|mailer-daemon|postmaster`);
   a delivery-status `Content-Type`; a calendar invitation, by subject prefix
   (`Invitation:`, `Updated invitation:`, `Accepted:`, `Declined:`,
   `Tentatively accepted:`, `Canceled event:`/`Cancelled event:`) or sender
   `calendar-notification@google.com`. A labelled bulk message keeps today's
   behaviour (stored with `is_auto`).
5. Match people and companies (unchanged).
6. **Automation 2 runs over the outside participants of labelled messages
   only** (FR-CAP-SRC-05, CHG-02). Its switch is still checked first.
7. Open deals and `links_for` (unchanged).
8. **A message that is not labelled and has no links is not stored.** It is
   collected as `(thread_id, gmail_message_id, sent_at)`; after the page,
   `store.mark_candidates(ws_id, mailbox_id, rows)` (RPC
   `crm_capture_mark`) upserts one row per thread, advancing
   `latest_message_id`/`latest_message_at` only when the new message is
   later, and then setting `status = 'pending'` and clearing the previous
   decision (FR-CAP-DIS-06 "judged again ... from the start"). Counted as
   `candidates`.
9. Everything else is stored, linked and touched as today (FR-CAP-SRC-04).
   `_touch_records` moves New → Contacted on outbound mail, so an unlabelled
   Sent message to a known New company moves it (spec §13 S1 line 3).

`COUNTS` gains `skipped_bulk`, `candidates`, `deal_signals`. In `labels` mode
every message is labelled, so steps 4, 6 and 8 never change anything: the
existing suite is the regression test.

### 5.5 S1 — deal documents (FR-CAP-SRC-06; E8)

At the end of the history phase (the branch that returns `True` with no
`nextPageToken`), and only for `primary_sent`, `sync_page` calls
`_deal_documents(ws, mailbox, now)` before its final `mailbox_progress`:

- `since = docs_checked_at`, or on the first search `max(capture_start_date,
  now - 14 days)`: a document older than that is history, not a signal
  (PD-19). For each kind in
  `capture.DEAL_DOC_QUERIES` (`sow`: `filename:sow OR filename:"statement of work"`;
  `proposal`: `filename:proposal`; `quotation`: `filename:quotation OR filename:quote`;
  `msa`: `filename:msa`; `purchase_order`: `filename:"purchase order" OR filename:po`),
  `gmail.search(sub, f"has:attachment after:{epoch} {scope} ({query})")`
  returns ids (at most 2 pages of 100 per kind per sync). `scope` is
  `capture.deal_doc_scope(labels)`: `(category:primary OR in:sent OR
  label:"<configured name>" ...) -in:trash -in:spam`, and `gmail.search`
  sends `includeSpamTrash=false`, so Updates, Social, Promotions and Forums
  are never searched (E10).
- For each id: `gmail.message` (metadata); skip unless `in_scope`; `classify`;
  skip internal-only; skip `capture.is_bulk` (a labelled message too: a
  bulk mail with "proposal.pdf" is not a signal);
  match companies through people and domains as `_take` does (PD-7); for each matched company with open deals, build one
  `deal_signal` (§5.10) with `origin: "deal_document"`, the document kinds
  found, the direction and the date. Outbound: suggest `proposal`, with
  `proposal_sent_on` = the IST date, only for open deals before Proposal.
  Inbound: suggest `closed_won`, "check the signed document", for any open
  deal. Only deals created on or before the document's date are offered (a
  document cannot be about a deal opened after it). No open deal at a
  suggestable stage: nothing.
- `review.upsert` (actor kind `sync`). Then `docs_checked_at = now` is
  written with the cursor (`MAILBOX_PROGRESS` gains it; `crm_audit_row` skips
  it, data-model §3.1).
- A Gmail error here is logged and counted (`deal_doc_failed`); the cursor
  is not advanced, so the next sync retries; the page itself succeeds.

### 5.6 S2 — the gate and the call (FR-CAP-DIS-01, -05; LLM-07)

`ai.gate(ws, phase)` and `ai.call(ws, job, phase, ...)` accept `phase: str |
None`. `None` skips step 1 only; rates and budget are checked as before,
including the second budget check before a retry. Only `services/crm/capture.py`
passes `None` (a test pins this with an AST walk).

The job's checks, in order, before any Gmail read:

1. `capture.mode` is `off` → `Skipped("Email capture is off.")`.
2. `email_sync.off_reason(ws_id)` → `Skipped(reason)`.
3. No active mailbox with `capture_source = primary_sent` →
   `Skipped("No mailbox captures Primary and Sent.")`.
4. Dry run: no open dry run → `Skipped("The dry run is finished; apply it or run it again.")`.
5. The Gmail key is not readable on this service (`gmail._key()` raises
   `NotConfigured`) → `Skipped("The Gmail key is not set on this service.")`.
6. `ai.gate(ws, None)` → rates, budget.

Then per candidate, each under `jobs.out_of_time()`, at most 200 per run.
`store.pending_candidates(ws_id, mailbox_ids, order)` takes only candidates
of mailboxes that are active, on `primary_sent`, and whose owner passes
`email_sync._owner_here` (FR-CRM-SYNC-01), so a mailbox switched back to
*Labels only*, made inactive or left by its owner is read no further
(NFR-CAP-05). Order: **dry run** oldest `latest_message_at` first; **live**
newest first, so a new conversation is judged within the hour behind no
backlog (G-3), and older pending rows are worked when nothing newer waits.

| Step | Read | Outcome |
|---|---|---|
| a | `gmail.thread_meta` (metadata, `HEADERS`); messages not `in_scope` (no Primary/Sent/configured label, or in Trash or Spam) are dropped | thread gone or nothing left → `skipped` |
| b | rules on each message: drop internal-only and `is_bulk` ones | none left → `skipped` |
| c | ignore list: every outside participant matched by address or domain (after a metadata-only read; PD-21) | → `ignored` |
| d | known check, **message by message as `_take` decides** (`links_for`): a message naming an outside participant who is a person or has a company domain (PD-7) is known-contact mail. Every surviving message known → `known`, enqueue `crm.capture_thread`. Otherwise only the messages naming nobody known go on to e–g (they are what made the thread a candidate; G-5); `crm.capture_thread` is still queued when a known message is not stored yet | → `known` |
| e | `ai.gate` again (the budget may have moved), then `gmail.thread_text(mailbox, ids)` for the **message ids that survived a, b and d only**, at most the last 12 (all the prompt holds), so one long conversation costs at most 12 text reads | — |
| f | `ai.call(ws, "capture", None, DiscoveryOutput, INSTRUCTION, parts)` | → `judged` with outcome and confidence |
| g | live: high/medium lead or deal → item (§5.10); dry run: finding kept only | — |

`crm_capture_record(...)` writes the decision **only if the row's
`latest_message_id` is still the one judged**, so a message that arrived
meanwhile wins and the row is judged again later; a retry after a crash
re-judges only rows still `pending` (spec §13 S3 line 5).

**Failures (PD-17).** Only failures that belong to the conversation count
toward `attempts`: `AiFailed` with `OUTPUT_INVALID` (Claude answered twice
off-schema), and a malformed thread (no message ids, unparseable headers).
At 3 the row becomes `failed` (never retried until a new message arrives);
counted in `failed_items`, logged with the exception type only. Every other
failure is the service's, not the thread's, and **ends the run as failed**
with the candidate left `pending` and its `attempts` unchanged:
`gmail.NotConfigured`, a token refusal, `GmailError` after `_get`'s retries
(401/403/429/5xx, network), `AiFailed` with `CALL_FAILED` or
`CALL_TIMED_OUT` (which include `claude.NotConfigured`), and `SafeError`.
The run's counts so far are kept; three such runs in a row alert the Admins
through `jobs.STREAK` (NFR-CAP-04). A 404 thread is `skipped` (step a).

**Prompt (FR-CAP-DIS-04).** `parts`, built by `capture.prompt_parts`:
one part per message, oldest first, label `Message {i} of {n} · {direction} ·
from {name <address>} · to {addresses} · {YYYY-MM-DD HH:MM IST} ·
attachments: {names or none}`, text = `capture.trim(text)`: the message's
own text, then at most the **first 20 lines** of its quoted or forwarded
block (the block starts where `summaries._quote_start` finds `On … wrote:`,
`>`, `-----Original Message-----`, an Outlook `From:` header block or
`---------- Forwarded message ----------`), and the signature kept within
the same 20-line allowance (FR-CAP-DIS-04; PD-9). A referral forwarded with
"see below" therefore still shows the forwarded inquiry. Only the last 12
messages; then oldest parts are dropped until the total is ≤ 24,000
characters; a single message over the limit is cut at the end. Then one
part `("Websites in this conversation", "1. acme.com\n2. …" | "none")`
(below), and last `("Mailbox owner replied", "yes" | "no")`. `ai.call`
applies `009`'s phone stripping and quoting.

**Output (FR-CAP-DIS-05).** `DiscoveryOutput` (contracts §4). `person_email`
must be one of the thread's outside participants, else the first outside
sender is used. **Website.** `ai._clean` drops bare domains from any string
Claude returns, so Claude does not type the website; it picks one.
`capture.website_choices(participants, texts)` lists, in code, the hosts
found verbatim in the thread: outside participants' domains and hosts in the
message text (signatures, links), lower-cased, valid host syntax, not a
public webmail domain, not a workspace `allowed_domains` value, at most 10.
Claude returns `website_choice` (the number, or `null`). The finding's
`domain` is the chosen host; failing that, the domain of `person_email`
unless it is webmail (FR-CAP-DIS-09); else `None`. A domain that came from
the text rather than the sender's own address is flagged
`domain_from_text: true`: the item shows it for the reviewer to confirm and
it is used by the duplicate check (FR-CAP-DIS-08; PD-20).

### 5.7 S2 — dry runs and the report (FR-CAP-DIS-10; RPT-01 to -03)

- **Start.** `POST /crm/capture/dry-run` (Admin) calls
  `crm_capture_start_dry_run(p_actor)`: one transaction that inserts a
  `crm_capture_dry_runs` row, returns every candidate of the workspace that a
  **dry run** judged (`dry_run = true`) to `pending`, and sets
  `capture.mode = dry_run` (audited `setting.updated`). `PATCH
  /crm/settings/capture` refuses `dry_run` ("Start a dry run from Email
  capture.").
- **Changing the mode.** `PATCH /crm/settings/capture` does not write the
  setting through the generic path; it calls `crm_capture_set_mode(p_actor,
  p_workspace, p_mode)`, one transaction that writes the setting (audited)
  and, when a dry run is open, **cancels** it: `finished_at = now()`,
  `cancelled = true`, recall left `[]`. A cancelled run is never applicable,
  and the next `POST /crm/capture/dry-run` starts normally. (The partial
  unique index would otherwise refuse every later start, and only hand SQL
  could clear it.) A switch to `live` also returns every candidate a dry run
  judged (`dry_run = true`, never applied) to `pending`, after the cancelled
  run's counts are kept, so the live job judges them again: the live job
  reads only pending candidates, and a lead judged during a second dry run
  would otherwise never become an item.
- **One at a time.** `crm_capture_start_dry_run`, `crm_capture_set_mode` and
  `crm_capture_apply` (073) each first take the workspace's capture lock,
  `pg_advisory_xact_lock(hashtext('crm_capture:' || workspace))`, so an
  Apply and a Run dry run (or a mode change) racing each other run one after
  the other, and apply's latest-run check sees a start that committed
  meanwhile. Without it, a race could leave the mode Live with a new run
  open, refusing every later start.
- **Running.** The hourly job in `dry_run` judges pending candidates with
  `dry_run = true` and creates no item and no record (FR-CAP-RPT-01). When it
  stops for time with candidates left, it re-enqueues itself with
  `countdown=60`, guarded by a Redis key `crm:capture:next` (`SET NX EX
  3600`, holding the follow-up's task id; only the task with that id
  deletes it when it starts, so an hourly tick that lands while a follow-up
  waits neither clears it nor queues another), so at most one follow-up is
  ever queued and summaries interleave on the single slot. A gate that
  closes (budget, rates, mode) ends the chain; the hourly tick resumes. The
  mode is read again before each conversation's text is read: Off, another
  mode, or a dry run no longer open (cancelled, or a new one started) ends
  the run there, with no further text read or Claude call.
- **Finishing.** When no pending candidate remains and every `primary_sent`
  mailbox has `backfilled_at`, the job computes the recall check and sets
  `finished_at` and `recall` on the run. Later candidates stay `pending`
  until the mode is Live or a new dry run starts ("then stops").
- **Recall (FR-CAP-RPT-03; G-4).** Hand labels win (§5.4 step 2), so a
  labelled thread is stored by the label path and, with automation 2,
  creates its own company: it never becomes a candidate. Counting it
  `filed_known` would measure the labels, not capture. So each labelled
  thread is judged **as if it had no label**. For each `primary_sent`
  mailbox, the configured labels are listed (`gmail.list_messages`) since
  the start date; for each thread, from `thread_meta` and the database:
  1. no message `in_scope` through Primary or Sent alone → `outside_scope`;
  2. steps a–b leave nothing → `skipped_rules`;
  3. every outside participant on the ignore list (step c) → `ignored`,
     before any text is read (FR-CAP-DIS-03);
  4. an outside participant matches a person or a company domain whose
     record was **created before the thread's first message** →
     `filed_known` (capture would have filed it as known);
  5. step d as live: every surviving message names someone the CRM holds
     **now** (typically a company automation 2 made from the label) →
     `known_now`, **not sent to Claude** (G-5, NFR-CAP-02: Claude never sees
     known-client mail, not even for the recall check). The summary line
     counts these apart;
  6. otherwise steps e–f run on the messages naming nobody known, as a
     dry-run-only evaluation (same prompt, same call, job `capture`) and
     its fate is `found` for a high- or medium-confidence `new_lead` or
     `deal_signal`, else `judged_otherwise` (low confidence included, as it
     would make no item).
  Nothing is stored for it beyond the fate: no candidate row, no finding.
  Each fate is appended to the open run as it is decided
  (`crm_capture_recall_add`); if a gate closes or the run runs out of time
  during the recall, the run is not finished and the next tick resumes
  with the threads that have no fate yet, so no thread is read or sent to
  Claude twice. Stored as `[{mailbox_id, thread_id, latest_at, fate}]`
  (≤ 500 rows, about 165 bytes each; no subject, no address). The report
  shows the recall only once the run has finished; a cancelled run's
  partial recall is cleared.
- **Report (FR-CAP-RPT-02).** `GET /crm/capture` returns the latest run with
  counts computed live by `crm_capture_report(p_workspace, p_run)` while it
  is open, and as they stood when it finished or was cancelled (stored on
  the run then, since the sync keeps marking candidates afterwards):
  considered, skipped by rules, ignored, filed as known (candidates `known`
  plus distinct stored threads of `primary_sent` mailboxes since the start
  date), judged, by outcome × confidence, failed, pending; the estimated cost
  (`crm_ai_spend(ws, started_at).by_job.capture`); every finding (lead and
  deal signal, all confidences, low ones flagged); the recall rows with
  their subject when the thread is stored (stored subjects are already
  captured mail) and a summary line.

### 5.8 S3 — live mode, ignore list, apply (FR-CAP-DIS-03, -07, -08, -10; REV-03; RPT-04)

- `PATCH /crm/settings/capture {"mode": "live"}` (Admin) is refused 422
  "Run and apply a dry run first." unless the workspace has an applied dry
  run (E6: dry run first), and "Set a mailbox to Primary and Sent first."
  while no active mailbox is on `primary_sent`. Live is therefore first
  entered through Apply (FR-CAP-RPT-04); afterwards an Admin may switch Off
  and back to Live, also while or after a later dry run that was not applied
  (its decisions are then judged again live, §5.7). Off is always accepted. In S2 no run can be applied, so
  `live` is always refused there (live findings would make no item until
  S3's step g).
- Live: step g of §5.6 turns each high/medium `new_lead`/`deal_signal` into
  an item through `review.upsert` (actor kind `sync`). Low confidence: the
  decision and finding only (FR-CAP-DIS-07). The item is built and upserted
  **before** `crm_capture_record`, while the candidate is still pending: a
  fault in the duplicate check, the owner lookup or the upsert ends the run
  with the conversation still pending, to be judged again (a decision
  recorded first would never be read again, and the lead would be lost).
  The retry makes no second item: the upsert is idempotent per dedupe key
  and source version. A newer message arriving meanwhile refreshes that
  item at the next judgement. After any capture item's upsert (`new_lead` or
  discovered `deal_signal`), `crm_capture_supersede` expires every other
  open capture item of the same conversation under another dedupe key (made
  before a later duplicate check linked it, or unlinked it, or before the
  lead became a deal signal on a matched company), so a conversation keeps
  one open capture item. A live judgement that makes **no** item (not
  relevant, other business, low confidence, or a deal signal whose deals
  are all past its stage) calls it with a key that matches nothing, so the
  earlier judgement's item, its evidence now stale, expires (FR-CAP-DIS-06).
  A discovered `deal_signal` is told from a deal document's by its evidence
  (`origin: discovery`); a deal document's signal is never expired here.
- **Duplicates (FR-CAP-DIS-08)**, before a `new_lead` item: exact domain
  (`store.list_domains`), a sibling domain (the same name on another public
  suffix: `acme.io` ↔ `acme.com` ↔ `acme.co.uk`; both must be bare
  `name.suffix` domains, so a subdomain such as `mail.foo.com` has no
  sibling; read with `store.domains_named`, a `LIKE 'name.%'` paged on the
  id, never the whole domain list), and `records.duplicates`'s name match. One match →
  the item targets that company (`record_type = company`) with `link: true`;
  a Claude `deal_signal` on an unknown company becomes a `new_lead` carrying
  `deal_stage` as a hint, unless a duplicate matched, in which case it is a
  `deal_signal` on the matched company (PD-4). That `deal_signal` moves the
  company's open deals still before Claude's stage, or creates one at
  Discovery only when the company has no open deal (none when it has one
  and Claude suggested Discovery or no stage); its evidence carries the
  sender's address, the domain and `domain_from_text`, and the card warns
  when the match came from a website named in the text.
- **Ignore list.** `crm_capture_ignores` (address or domain, lower-case).
  Admins add and remove entries in Settings → Email capture; a dismissal of a
  `new_lead` adds one (§5.9). Public webmail domains are refused as domain
  entries (`invites.PUBLIC_WEBMAIL_DOMAINS`).
- **Apply (FR-CAP-RPT-04).** `POST /crm/capture/dry-runs/{id}/apply`
  (Admin): 422 when the run is not finished, was cancelled, **finished**
  more than 7 days ago (the report's age; a run paused by the monthly
  budget can take longer than 7 days to finish), is not the latest, or was
  already applied. `capture.apply` builds items from the run's
  high/medium findings exactly as live does (no Claude call): only for the
  mailboxes the live job reads (active, `primary_sent`, owner still a
  member), and never for a person whose address or domain is on the ignore
  list now; `review.upsert`s them, then `crm_capture_apply(p_actor, p_run)` marks the run applied, sets
  those candidates `dry_run = false`, and sets `capture.mode = live`. A crash
  in between is safe to repeat: the upsert is idempotent per dedupe key.

### 5.9 S3 — approving and dismissing (FR-CAP-REV-01 to -04)

`services/crm/review.py`:

- `_untrashed`, `present`, `today_section`, `_open_and_live` accept a `new_lead`
  with no target: it is live, its `target` is `null` and its title comes from
  `suggestion.company_name`.
- `EDITABLE["new_lead"]`: `company_name`, `domain`, `lead_stage`,
  `first_name`, `last_name`, `email`, `title`, `link_company_id`.
  `EDITABLE["deal_signal"]`: `name`, `next_step`, `next_step_on`, `amount`,
  `currency`, `expected_close_on`, `proposal_sent_on`, `point_of_contact_id`,
  `project_name`.
- **`_new_lead(ctx, item, body)`**, run as the approver with
  `suggestion_id = item.id`:
  1. Company: `link_company_id` (edit) or the item's target → use it.
     Otherwise `item.result.company_id` from an earlier partial approval, if
     that company still exists and is not trashed → use it (CT-2; this also
     covers a webmail lead, which has no domain to find it by). Otherwise, if
     a company already holds the domain, use it. Otherwise `records.create(approver, "company",
     {name, owner_id: mailbox owner, domain?}, check_duplicates=False)` at New
     (`lead_stage_refusal` allows only New at creation).
  2. Lead stage: when the suggestion (or edit) is `contacted` or `qualified`
     and the company is at New, `records.update(..., {"lead_stage": ...,
     "expect_updated_at": ...})`. "New if it was never contacted" is set by
     the job: no owner reply → `new`.
  3. Person: unless a person already holds the email, `records.create(...,
     "person", {first_name, last_name, primary_email, title, company_id})`.
  4. Queue `crm.capture_thread(workspace_id, mailbox_id, thread_id)`.
  5. Result `{company_id, person_id, filed: "queued"}`.
  A refusal or any other failure (a timeout, a database fault) at step 2
  or 3, after the company exists, releases the item with
  `review_release(..., result={company_id})` (it already takes a result),
  so the retry reuses it instead of creating a second company of the same
  name.
- **`_deal_signal(ctx, item, body)`**: the deal is chosen from the item's
  deal choices (`_deal`, `CHOOSE_DEAL`). `action = create`:
  `deals.create(approver, {company_id, name, next_step, next_step_on,
  owner_id})`; the pipeline's own refusals (company not Qualified, missing
  next step) come back and the item stays open. `action = move`:
  `deals.move(approver, deal_id, StageMoveIn(stage, changes=..., project_name,
  expect_updated_at))`; Proposal's required fields (amount, currency,
  expected close, point of contact when Qualification is skipped, next step
  and date) come from `edits`, and in Labs the move creates the tentative TVA
  project. A suggested stage at or behind the deal's is refused "This deal is
  already at or past that stage." Closed Won in Labs: `deals.move` refuses
  with its handoff sentence; the card links to the deal's handoff instead of
  offering Approve.
- **`dismiss(ctx, item_id, body: ReviewDismissIn | None)`**: for a `new_lead`,
  `ignore = "address" | "domain" | None`; the address is
  `suggestion.email`, the domain its domain (refused 422 for a public
  webmail domain). The ignore row is written through `store.apply` as the
  dismisser (audited `capture_ignore.created`), `from_item_id` = the item.
  An entry already there is reused, including one another dismissal added
  between the read and the insert (`crm_capture_ignores_once`: read again).
  The response always has `ignore` (`null` when none was asked for).
  `ignore` on any other type is refused 422. Members who may decide the item
  may add to the list this way (PD-6).
- **Owner (FR-CAP-REV-04).** `owner_for("new_lead"|"deal_signal",
  mailbox_owner=...)` returns the mailbox owner when eligible, else the
  Admins (PD-5).
- **Filing a conversation.** `crm.capture_thread` on `worker-crm-sync`:
  `gmail.thread_meta` → message ids → `_take(ws, mailbox, ids, ours=scan,
  configured)`. The participants are known now, so the messages are stored,
  linked and `last_*` move; messages outside Primary/Sent stay out (E10).
  `crm_email_messages` is a log and is not audited, so the item id reaches
  the audit through the company and person rows, and the item's `result`
  (PD-8).

### 5.10 Review items made by capture

| | `new_lead` | `deal_signal` |
|---|---|---|
| Made by | `crm_ai.capture` (live or apply) | the sync (deal documents, model NULL) or `crm_ai.capture` |
| Target | none, or the duplicate company (`link: true`) | the company; deal choices = its open deals (`crm_review_item_deals`) |
| `source_kind` / `source_key` | `email_thread` / `gmail:<mailbox_id>:<thread_id>` | same |
| `source_version` | latest Gmail message id judged | the message id (document) or latest judged |
| `dedupe_key` | `email_thread\|gmail:<mb>:<thread>\|new_lead\|thread` (or `\|company:<id>` when linked) | `email_thread\|gmail:<mb>:<thread>\|deal_signal\|company:<id>` |
| Owner | mailbox owner | mailbox owner |

An expired, dismissed or approved item is not made again for the same
`source_version` (`crm_review_upsert`, 069), which meets "an expired
`new_lead` does not return for the same messages". `crm_merge_companies`
re-keys the `company:` heads unchanged.

### 5.11 Frontend

- `app/(crm)/crm/[ws]/settings/page.tsx`: `MailboxesTab` gains a capture
  source select (*Labels only* / *Primary and Sent*) and a start date (shown
  for Primary and Sent) on the add form and each row; the backfill wording
  says "reading from <date>" for Primary and Sent. A new tab
  `email-capture` ("Email capture", Admin only) renders
  `components/crm/capture-settings.tsx`: mode (Off / Dry run / Live, with Dry
  run started by its button; Live enabled from the overview's
  `live_allowed`, the server's own rule, not from the latest run), ignore
  list editor, the report (counts, cost, findings table, recall table and
  line, the recall shown only once the run finished and was not cancelled),
  Apply with `window.confirm`, disabled unless `applicable`. Run dry run
  asks `window.confirm` while capture is Live (live capture pauses until it
  is applied or Live is chosen again).
- `components/crm/review-item.tsx`: cases for both types in `describe()`,
  `initialEdits()`, `editsPayload()`, `EDITABLE`; `recordHref()` handles a
  null target; an evidence block (date range, message count, owner replied,
  attachment names, reason, confidence; never text); the new-lead form
  (company, domain, stage, person; link to an existing company); the
  deal-signal form (Discovery: next step and date; Proposal: amount,
  currency, expected close, point of contact, project name in Labs); Dismiss
  with *ignore this sender* / *ignore this whole domain* (the domain offered
  only when the sender's domain is the lead's domain: hidden for webmail,
  and for a lead whose website came from the text).
- `lib/api/crm-types.ts`, `lib/api/crm.ts`: the new types and calls
  (contracts §5).

### 5.12 Privacy controls (NFR-CAP-02; `009` FR-CRMAI-LLM-04)

| Control | Where | Test |
|---|---|---|
| No body column anywhere; candidate rows hold ids and times only | data-model §3.2 | db: column list; `char_length` caps |
| Skipped, ignored, known, other and not-relevant rows hold no address, subject or name | CHECK `finding IS NULL OR outcome IN (...)` | db + fake |
| Text read only by `thread_text`, only from `services/crm/capture.py` | import-graph test | `test_crm_gmail_full_read.py` |
| Text never reaches a log, a row, a task argument, an error or a result | `ai.SafeError`; ids-only task args; `ignore_result=True` | `test_crm_ai_privacy.py::TestCapture` (markers in body, quoted history, subject of a skipped and a not-relevant candidate) |
| Internal, bulk, ignored and known candidates never reach Claude, nor known or ignored labelled threads in the recall check, nor the known messages of a mixed thread | step order §5.6, §5.7; step d per message | `FakeClaude()` with no script fails on any call; `TestRecall`, `TestRejudging` |
| Only surviving messages reach Claude: an internal, bulk, Promotions, Trash or Spam message in a candidate thread is never fetched as text nor quoted | §5.6 step e reads only the ids left after a and b | `FakeGmailThread.text_reads` lists only those ids; marker absent from the Claude request |
| A closed gate reads no Gmail text | gate before step a and e | `FakeGmailThread.reads == []` |
| The sync stores nothing for a skipped message | `_take` step 3–4, 8 | sync tests assert no row of any table |

### 5.13 Failure handling

| Failure | Behaviour |
|---|---|
| Gmail 429 or 5xx on any call | `gmail._get` retries: up to 3 tries in all, waiting `Retry-After` (capped at 30 s) or 1 s then 4 s; then `GmailError` as today (NFR-CAP-03) |
| Gmail error on a sync page | unchanged: that workspace's run fails, the plan moves on, the cursor stays |
| Deal-document search error | any exception (Gmail, a soft time limit, a refused item): logged with ids, counted, its cursor not advanced; the history cursor is saved and the page succeeds |
| `crm_ai.capture` near its time limit | soft limit at 540 s: the run ends `ok` with its counts, the conversation stays pending, a dry run chains |
| `LabelsMissing` in `primary_sent` | not raised; Primary and Sent still scanned |
| Candidate thread gone (404) | `skipped` |
| Claude invalid twice (`OUTPUT_INVALID`), malformed thread | `attempts += 1`; `failed` at 3 |
| Gmail key missing on `worker-crm-ai` | `Skipped` before any candidate (§5.6 check 5) |
| Gmail error after retries, token refused, Claude timeout / HTTP error / not configured | the run ends `failed`; the candidate stays `pending`, `attempts` unchanged; STREAK alerts after three |
| Budget reached mid-run | `Skipped` with the counts so far; resumes next tick; dry-run chain ends |
| Mode switched away from Dry run during a run | `crm_capture_set_mode` cancels the open run; the job's next check skips |
| A newer message during judging | `crm_capture_record` writes nothing; re-judged next run |
| Approval refused by a pipeline rule | item reopens showing the refusal (`009` REV-06) |
| `crm.capture_thread` fails | logged; the records exist; the conversation is filed when its next message arrives, or by re-queueing from the item (no UI; PD-10) |
| Three failed `capture` runs in a row | `jobs.STREAK` alert to Admins (NFR-CAP-04) |

---

## 6. Requirement → code

| FR / NFR | Code |
|---|---|
| SRC-01 | 071 columns; `MailboxIn/Update`; `api/crm/email.py`; `MailboxesTab` |
| SRC-02, -08, CHG-01 | `capture.scan_labels`, `in_scope`; `sync_page`, `_reconcile` (`reconcile_after`), `changed_messages` (Trash/Spam in `primary_sent`) |
| SRC-03 | `capture.is_bulk`; `_take` step 4; `HEADERS` + `List-Unsubscribe` |
| SRC-04 | `_take` (unchanged storing and linking) |
| SRC-05, CHG-02 | `_take` steps 2, 6, 8; `crm_capture_mark` |
| SRC-06 | `gmail.search`; `capture.DEAL_DOC_QUERIES`, `deal_doc_scope`, `deal_doc_stage`; `email_sync._deal_documents`; `docs_checked_at` |
| SRC-07, CHG-03 | `capture.capture_after`; cursor reset in `update_mailbox` |
| DIS-01 | `capture_refusal`; `crm_capture_set_mode`; `ai.gate(ws, None)`; job checks §5.6 |
| DIS-02 | `crm_ai.capture` on `crm-ai`; candidates only |
| DIS-03 | `capture.ignored`; `crm_capture_ignores` |
| DIS-04 | `gmail.thread_meta`/`thread_text`; `capture.prompt_parts`; `capture.trim` |
| DIS-05 | `DiscoveryOutput`; `ai.call(... "capture", None ...)` |
| DIS-06 | `crm_capture_record`; `crm_capture_mark` reset |
| DIS-07 | `capture.to_item` returns nothing for low |
| DIS-08 | `capture.duplicate_of` + `records.duplicates` |
| DIS-09 | `capture.domain_for` |
| DIS-10 | beat `crm-ai-capture`; dry-run chain |
| DIS-11 | `crm_capture_purge`; `tasks/crm.py::_purge` |
| REV-01, CHG-04 | 071/073 CHECKs; `review.TYPES`; `review-item.tsx` |
| REV-02 | `review._new_lead`, `_deal_signal`; `crm.capture_thread` |
| REV-03 | `ReviewDismissIn`; `review.dismiss`; MCP `ignore` |
| REV-04 | `owner_for`; `dedupe_key`; 069 upsert |
| RPT-01–03 | `capture.py` dry-run functions; `crm_capture_report`; recall |
| RPT-04 | `capture.apply`; `crm_capture_apply` |
| CHG-05 | `gmail.py`, `summaries.py` docstrings; import-graph test |
| CHG-06 | nothing to build: `crm_pending_threads` already covers stored, linked mail (cost noted, R-4) |
| NFR-CAP-01 | `009` budget gate; report's estimated cost |
| NFR-CAP-02 | §5.12 |
| NFR-CAP-03 | pages of 50; candidate cap 200/run; search 2 pages/kind; `gmail._get` retry on 429/5xx (new, §5.13) |
| NFR-CAP-04 | `crm_job_runs` counts per step; `STREAK` |
| NFR-CAP-05 | defaults; §11 PD-3 |

---

## 7. Project structure

```text
supabase/migrations/
  071_crm_capture_sync.sql          S1
  072_crm_capture_discovery.sql     S2
  073_crm_capture_live.sql          S3
backend/app/
  domain/crm/capture.py             new (pure)
  domain/crm/{email_rules,settings,review,ai_rules}.py
  services/crm/capture.py           new
  services/crm/{email_sync,gmail,store,ai,review,jobs}.py
  api/crm/capture.py                new
  api/crm/{email,settings,review,jobs,__init__}.py
  schemas/crm.py
  tasks/{crm,crm_ai}.py
  mcp/crm.py
  worker.py
backend/tests/
  test_crm_capture_rules.py         new
  test_crm_capture_sync.py          new (S1 behaviour on FakeGmail)
  test_crm_capture_job.py           new (S2/S3 job)
  test_crm_capture_routes.py        new
  test_crm_capture_review.py        new (approve/dismiss new types)
  db/test_crm_capture_db.py         new
  test_crm_email_sync.py            FakeGmail extended (labels, search, threads)
  crm_fake.py, crm_ai_fakes.py      tables, RPCs, FakeGmailThread
  test_crm_ai_privacy.py, test_crm_gmail_full_read.py, test_crm_ai_jobs.py,
  test_crm_review_*.py, db/test_crm_security_db.py, test_crm_mcp.py
frontend/
  components/crm/capture-settings.tsx   new
  components/crm/review-item.tsx
  app/(crm)/crm/[ws]/settings/page.tsx
  lib/api/{crm,crm-types}.ts
docs/architecture/crm.md; docs/guides/10-crm-admin.md, 12-crm-intelligence.md;
docs/operations/deployment.md (§9); specs/007-crm/spec.md, specs/009-crm-intelligence/spec.md
```

---

## 8. Testing

| Layer | What | How |
|---|---|---|
| Pure rules | `scan_labels`, `in_scope` (Trash, Spam), `capture_after`, `is_bulk` (each header, no-reply, invites), `capture_refusal`, ignore matching, owner replied, `trim` (forwarded inquiry kept, 20 quoted lines), prompt trimming (12 messages, 24,000 chars), `website_choices`, domain choice, duplicate siblings, lead stage, item builders, dedupe keys, recall fates, deal-document stage by direction | `test_crm_capture_rules.py`, no I/O |
| Sync | every §13 S1 line on `FakeGmail` with `CATEGORY_PERSONAL`/`SENT`/`TRASH`/`SPAM`, `search`, `threads`; entering and leaving scope by history; trash removes; switch back reconciles to the old start date; deal documents (scope, bulk, old document on a newer deal); `labels` mode: the whole existing `test_crm_email_sync.py` unchanged | `test_crm_capture_sync.py` with the existing `crm`/`Queue` fixtures |
| Job | gates in order (Gmail key check); never reaching Claude for internal, bulk, ignored, known, Trash/Spam; only surviving ids read as text; decisions; low confidence; dry run → no item; chain guard; finish + recall (labelled thread judged as if unlabelled); live → items, newest first; inactive / `labels` / owner-gone mailboxes skipped; `attempts` only for per-conversation failures, service failures end the run with rows pending; newer message wins | `test_crm_capture_job.py` with `FakeClaude`, `FakeGmailThread`, `fix_clock`, `SlowCalls` |
| Review | approve `new_lead` (create, link, retry reuse, stage, queue), `deal_signal` (create, move to Proposal asks fields, Labs tentative project, Closed Won refused), dismiss with ignore, webmail refusal, owner routing | `test_crm_capture_review.py` |
| Routes | every route of contracts §2 for Admin, Member, Viewer, other workspace (404) | `test_crm_capture_routes.py` |
| Privacy | markers never in rows, audit, logs, Celery args, results | `test_crm_ai_privacy.py::TestCapture` |
| Database | CHECKs, RPCs (mark advances only forward, record refuses stale, start resets dry-run rows, apply, purge), audit skip of `docs_checked_at`, seeds and trigger, grants, RLS, `NOT_AUDITED_010` sweep | `tests/db/test_crm_capture_db.py`, `test_crm_security_db.py` (`CRM_DB_TESTS=1`) |
| Frontend | `pnpm build` (strict exhaustiveness of `describe()`), `pnpm lint` | CI |

---

## 9. Risks

| # | Risk | Mitigation |
|---|---|---|
| R-1 | Gmail sync is not live in production (`worker-crm-sync` not created, `CRM_GMAIL_CONFIGURED` unset) | S1–S3 ship dark; release §10 lists the owner steps |
| R-2 | Backfill since April re-fetches unstored messages (candidates, skips) on every reconcile | pages of 50; reconcile only after a reset; acceptable at one mailbox (NFR-CAP-03) |
| R-3 | Gmail `filename:` search misses names like "SOW_v2_final" or catches "po" in odd names | per-kind queries are constants, tuned without schema change; items are reviewed |
| R-4 | Captured mail raises phase-1 summary cost (FR-CAP-CHG-06) | counted in the same budget; noted in the deployment section |
| R-5 | Recall would be circular for labelled threads (hand labels win, and automation 2 makes their company) | each labelled thread is judged as if unlabelled; known only through records created before it (§5.7, PD-11). One whose contacts are in the CRM now is not sent to Claude (G-5) and is counted apart (`known_now`), so the recall measures fewer threads than it was labelled with |
| R-6 | Dry run longer than expected | self-chaining; the report shows pending counts |

---

## 10. Release

1. Merge `010-email-capture` to `main` after every gate.
2. `npx supabase db push` (071–073, after 070). Never commit
   `supabase/config.toml` (it has a local change now).
3. Railway, one commit, each service: `cd backend && railway up .
   --path-as-root --service <name>` for `api`, `worker`, `beat`,
   `worker-crm-ai`, and `worker-crm-sync` if it exists.
4. Netlify from `frontend/`: `NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=false netlify
   deploy --build --prod --skip-functions-cache`.
5. Checks: `/health/jobs` ok once `capture` has ticked (a `skipped` run
   counts); `/docs` and `/openapi.json` 404; every mailbox `labels`, every
   workspace `capture.mode = off`.
6. Owner, before anything captures (deployment.md §9): 007 step zero (create
   `worker-crm-sync` with `GOOGLE_SERVICE_ACCOUNT_JSON`; `CRM_GMAIL_CONFIGURED=true`
   on `api`, `beat`, `worker`, `worker-crm-ai`); **set
   `GOOGLE_SERVICE_ACCOUNT_JSON` on `worker-crm-ai` (the same key as
   `worker-crm-sync`)**: capture reads threads there, and without it the job
   skips with "The Gmail key is not set on this service."; `009`'s Claude
   key and rates on `worker-crm-ai` and `api`; a non-zero Labs AI budget;
   switch Labs email sync on; set `navaneeth@nunnarilabs.com` to *Primary
   and Sent*; wait for the backfill; start the dry run; review; apply.

---

## 11. Planning decisions

Recorded where the spec was silent or conflicted with the code; each is the
simplest option consistent with the spec.

| # | Decision | Why |
|---|---|---|
| PD-1 | Migrations are **071–073**. `test_crm_security_db._009_sql()` is narrowed to 060–070 and a `_010_sql()`/`NOT_AUDITED_010` sweep (071–079) is added; `TestThe048BodiesAreKept`'s 060–079 range still accepts 071's `crm_audit_row` | 009's sweep reads 060–079 and asserts exactly its tables |
| PD-2 | `List-Unsubscribe` is the only new header; `Precedence`, `Auto-Submitted`, `Content-Type` are already fetched. Calendar invitations are detected by subject prefix and Google's sender, because metadata has no nested `text/calendar` part | spec §11 vs `email_rules.HEADERS` |
| PD-3 | Switching a mailbox back to *Labels only* resets the cursor and sets `reconcile_after` to the earliest capture start date the mailbox captured from (kept when the start date moves later while capturing); the reconcile then removes captured mail without a label back to that date, the date itself included, not only the last 90 days (§5.3). "Leaves scope" includes that switch | NFR-CAP-05 is otherwise unimplementable without remembering which label brought each message |
| PD-4 | A Claude `deal_signal` for a candidate (unknown by definition) becomes a `new_lead` with a deal-stage hint, unless the duplicate check matches a company, when it is a `deal_signal` on that company | a candidate has no known company to put a deal on |
| PD-5 | Both types are owned by the mailbox owner, including a deal-document move (journey 4.4 says "the deal's owner") | FR-CAP-REV-04 is the requirement; in Labs they are the same person today |
| PD-6 | A Member who may decide a `new_lead` may add to the ignore list by dismissing it; editing the list directly is Admin only | FR-CAP-REV-03 vs the Admin-only list |
| PD-7 | "Known" includes Trash, as `007` matching does today: mail from a trashed company's domain is filed onto it (seen again on restore) and never reaches Claude | one matching rule for labelled and captured mail; no change to `people_by_emails`/`list_domains` |
| PD-8 | The conversation is stored by `crm.capture_thread` on `worker-crm-sync` after approval; the item id is in the audit of the company and person, not of the (unaudited) email log | the API has no Gmail key; `crm_store_emails` takes no actor |
| PD-9 | `capture.trim` keeps the message's own text and the first 20 lines of its quoted or forwarded block and signature, reusing `summaries`' quote patterns; it is not `summaries.strip`, which drops the whole block | FR-CAP-DIS-04; a forwarded referral must keep the forwarded inquiry |
| PD-10 | A failed `crm.capture_thread` is logged, not retried; no UI | YAGNI; the next message files the thread |
| PD-11 | Recall fates: `filed_known`, `found`, `judged_otherwise`, `skipped_rules`, `outside_scope`, `ignored`, `known_now`. Each labelled thread is judged as if unlabelled: `filed_known` only through a person or company created before its first message; on the ignore list → `ignored`, and every message naming someone in the CRM now → `known_now`, neither sent to Claude (G-5, NFR-CAP-02; amended after review, the spec wins over recall coverage); otherwise its unknown messages are evaluated by steps e–f in the dry run, storing only the fate | G-4 asks what capture would have found, not what the labels did |
| PD-12 | A *Primary and Sent* mailbox still needs at least one configured label | DB CHECK and `NO_LABELS`; recall needs the labels anyway |
| PD-13 | Dry run is entered only by `POST /crm/capture/dry-run`; `PATCH /crm/settings/capture` accepts `off` always and `live` only when the workspace has an applied dry run and a `primary_sent` mailbox; Live is first entered through Apply. Any mode change through PATCH cancels an open dry run (`crm_capture_set_mode`) | E6 "dry run first, applied after review"; the run row must exist; a run left open would block every later start |
| PD-14 | After a dry run finishes, new candidates wait as `pending`; the job skips with "The dry run is finished..." until Live or a new dry run | "runs once ... then stops" |
| PD-15 | Retention deletes every decided row (any status but `pending`, findings included) 12 months after it was last updated; an item from it has expired (14 days) and been tombstoned (90 days) long before. `crm_capture_mark` ignores messages older than 12 months so a re-read does not recreate them | E12 applies to every marker; a lead's name and address are not kept without limit after a person decided (NFR-CAP-02) |
| PD-16 | A candidate's message that leaves Primary, or goes to Trash or Spam, is handled at judging time (thread read shows nothing in scope → `skipped`), not by `_drop` | no stored message to drop |
| PD-17 | Only per-conversation failures (`OUTPUT_INVALID`, a malformed thread) count toward `attempts`, 3 then `failed` until a new message; Gmail, token and Claude service failures end the run as failed and leave candidates `pending` (§5.6) | NFR-CAP-01 cost, without losing the backlog to an outage or a missing key |
| PD-18 | No workspace export of capture tables; no new MCP tool (count stays 21); `crm_review_decide` gains `ignore` | YAGNI; `test_crm_mcp.py` pins 21 |
| PD-19 | The deal-document search runs only in `primary_sent` mode, only over Primary, Sent and configured labels (not Trash or Spam), skips bulk mail, starts at most 14 days back on its first run, and offers only deals created on or before the document's date | FR-CAP-SRC-06 is part of S1 capture; E10; a months-old document is not a signal about a newer deal |
| PD-20 | Claude picks the website from a list of hosts found verbatim in the thread (`website_choice`), never types it; failing that the domain comes from `person_email` unless webmail. A host from the text is flagged for the reviewer to confirm and is used for the duplicate check | FR-CAP-DIS-05, -08, -09; `ai._clean` drops bare domains Claude writes |
| PD-21 | The ignore list is applied after a metadata-only thread read (candidates store no addresses), and before any text read; spec FR-CAP-DIS-03 amended to "before any text read" | NFR-CAP-02 keeps candidate rows address-free |

## 12. Owner decisions cited

| E | Where it lands |
|---|---|
| E1 Primary and Sent | `scan_labels` adds `CATEGORY_PERSONAL` and `SENT` |
| E2 one mailbox | per-mailbox `capture_source`; only `navaneeth@nunnarilabs.com` switched at release |
| E3 Review Queue | `new_lead`/`deal_signal`; no record without approval |
| E4 read-only | no Gmail write call anywhere; `gmail.readonly` unchanged |
| E5 known filed automatically | `_take` stores known mail; no review, no Claude |
| E6 dry run first | §5.7, apply §5.8 |
| E7 four outcomes | `DiscoveryOutput.outcome` |
| E8 direction decides stage | `deal_doc_stage` |
| E9 hourly discovery, 15-min sync | beat `:07` hourly; sync unchanged |
| E10 Primary and Sent only | no other category scanned, also for known contacts and `capture_thread` |
| E11 1 April 2026 | `capture_start_date` default |
| E12 12 months | `crm_capture_purge`, PD-15 |
