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

---

## Revision 1 — capture improvements (20 October 2026)

Spec §14 (FR-CAP-GRP, FR-CAP-RPT-05 to -10, FR-CAP-ACAD, FR-CAP-INV; E13–E16).
Branch `010-capture-improvements` from `main` `4c3f497`. One migration,
`074_crm_capture_improvements.sql` (data-model §9). Applied migrations
001–073 are not edited. The owner was away; every open question took the
recommended option, and the planning decisions below (PD-22 to PD-44) say
which.

### R1.1 Summary

| Improvement | Where it changes | Migration |
|---|---|---|
| **I1** one suggestion per company | `domain/crm/capture.py` (company key, item keys, thread entries), `services/crm/capture.py::store_items`, `_judge` no-item path, consolidation at the start of `run`; `email_sync._deal_documents` (keys); `services/crm/review.py::_new_lead` (file every thread), `present`; `review-item.tsx` | 074: `crm_capture_group_upsert`, `crm_capture_release_thread`, `crm_capture_consolidate`; `crm_review_release` replaced from 068 |
| **I2** honest recall line | `domain/crm/capture.py` (`FATES`, `recall_fate`, `recall_line`, `recall_summary`), `services/crm/capture.py::_fate` and a new `_drop_reason`; schema `CaptureRecallOut`; `capture-settings.tsx` | none (recall rows are free jsonb, 072) |
| **I3** course enquiries to Academy | `DiscoveryOutput.business`, `INSTRUCTION`, `finding_of`, `academy_lead_item`; `services/crm/capture.py::_academy_target`, `_route_academy`; `review._new_lead` (note, no filing); overview counts; `review-item.tsx`; Academy setting `capture_routing` | 074: `capture_routing` settings key (key CHECK and seed function replaced from 072) |
| **I4** invitations from outside people | `domain/crm/capture.py` (`is_invitation`, `is_calendar_notice`, `is_bulk(..., outside)`, `MEETING_HOSTS`, `invite_rescan_query`), `email_rules.HEADERS` + `Reply-To`, `email_sync._take`, `_deal_documents`, new `_rescan_invites`; `capture._survivors`, `prompt_parts` | 074: `crm_mailboxes.invites_rescanned_at`, `invite_rescan_token`; `crm_audit_row` re-copied from 071 |

Order of work (tasks R1): 074 first; the calendar predicates (I4's rules)
before I2's recall reasons, because I2 counts what I4 still skips; then I1,
I2, I3, I4's sync side, docs, release.

### R1.2 I1 — one suggestion per company

**Company key** (`capture.company_key(finding)`, pure):

1. `domain:<d>` where `d = dedupe.normalise_domain(domain_for(finding))`
   (never a `PUBLIC_WEBMAIL_DOMAINS` entry);
2. else `name:<n>` where `n` is Claude's own `company_name`, case-folded,
   whitespace collapsed, trimmed (never the fallback `_company_name` builds
   from the domain, the person or the address);
3. else `email:<person_email lower>`;
4. a finding with none of the three (should not happen: a lead has an
   address) keys on its conversation, `thread:<ref>`, as today.

**Keys** (FR-CAP-GRP-01, -02, -06; PD-22):

| Item | `source_key` | `dedupe_key` |
|---|---|---|
| `new_lead`, no matched company | `capture:<company key>` (when over 300 characters, `capture:sha256:<hex>`) | `email_thread\|capture:<key>\|new_lead\|thread` |
| `new_lead` linked to company X | `capture:linked` | `email_thread\|capture:linked\|new_lead\|company:<X>` |
| `deal_signal` on company X (discovery or deal document) | `capture:deal` | `email_thread\|capture:deal\|deal_signal\|company:<X>` |

The linked and deal keys keep the `<kind>|<source_key>|<type>|company:<id>`
head that `crm_merge_companies` (065) rewrites, so a company merge still
moves them. Two domains that match one company make one item.

**Conversation entries.** Each item's `source_link` becomes
`{mailbox_id, thread_id, threads: [entry, …]}` where `mailbox_id`/`thread_id`
are the latest conversation's (so `present`'s `thread_key` and "Open
conversation" work unchanged) and each entry is

```json
{"ref": "gmail:<mailbox_id>:<thread_id>", "mailbox_id": "…", "thread_id": "…",
 "message_id": "<latest judged>", "first_at": "…", "latest_at": "…",
 "message_count": 2, "owner_replied": true, "origin": "discovery",
 "reason": "Asked for pricing; you replied.", "confidence": "high",
 "person_email": "asha@acme-robotics.com", "first_name": "Asha", "last_name": "Rao"}
```

The sender fields are the finding's person (FR-CAP-GRP-04): approval makes
one person per distinct `person_email`. "Newest entry" everywhere below means
the greatest `(latest_at, message_id)`.

`source_link` has no size CHECK (061); `suggestion` (8 KB) holds only the
totals: `evidence.{first_at, latest_at, message_count, owner_replied,
conversations, attachments, reason, confidence, origin}`. The list never
goes into `suggestion`.

**Merging happens in SQL** (`crm_capture_group_upsert`, data-model §9.4),
not in Python, because the job, an apply through the API and the sync's
deal documents can touch the same company at once. The function takes the
workspace's capture lock (`hashtext('crm_capture:' || ws)`, the one 072
start/mode/apply take), so the 074 functions are serial among themselves.
Approvals (`crm_review_claim`, 069) and refusals (`crm_review_release`, 068)
do not take that lock, so every 074 function also reads each row it will
change with `SELECT … FOR UPDATE` and re-checks `status = 'open'` before
updating it (PD-38): an upsert whose open row was claimed meanwhile inserts
a fresh open item instead of merging into the claimed one; consolidation and
release skip a member that is no longer open. In one transaction it:

1. **re-suggestion check** (FR-CAP-GRP-05; PD-24): returns `{skipped:
   true}` when a decided capture item of the workspace, not superseded by
   consolidation, lists an entry with the same `ref` and `message_id`, or
   (an item from before 074) has `source_key = ref` and `source_version =
   message_id`. This replaces 069's "same dedupe key and version" for
   capture items, which a company key would defeat both ways. An item that
   `crm_review_release` expired with a refusal (`status = 'expired' AND
   refusal IS NOT NULL`) is not a decision and is ignored here;
2. **releases** the conversation from every other open capture item
   (FR-CAP-GRP-07; same body as `crm_capture_release_thread`); skipped
   when `p_entry.origin = 'deal_document'`, and never removes a
   `deal_document` entry (PD-39);
3. **upserts** by `dedupe_key` as 069's `crm_review_upsert` does (copied,
   lines marked `-- 074`), but merging the entry into
   `source_link.threads` (an entry with the same `ref` is replaced only
   when the new `latest_at` is not older), recomputing the totals into
   `suggestion.evidence`, and taking the incoming suggestion's names,
   person, stage and hint, its `current_values` and its `deal_choices`
   (replacing `crm_review_item_deals`) **only when its entry is the
   newest**; otherwise the item's suggestion fields, `current_values` and
   deal rows stay as they are, so the stage shown and the deals offered
   always come from one entry. It sets `source_key`, `source_link`,
   `source_version` (the newest entry's `message_id`), `owner_id` only on
   insert, and `revision + 1` when anything changed.

Grouping is therefore order-independent: an apply that walks findings in
any order, or a deal document and a discovery signal from two processes,
yields the same item (tested both ways: an older proposal signal applied
after a newer closed-won one keeps the closed-won stage and its choices).

**Thread moves and no-item judgements.** `store_items` calls
`review.capture_group_upsert(ws, item, entry)` for every capture item
instead of `review.upsert` + `capture_supersede`. That wrapper applies
`_masked` to `item.suggestion` and to `entry.reason`, as `review.upsert`
does (FR-CRMAI-LLM-05's save-time check), then calls
`store.capture_group_upsert`; the sync's deal documents and the Academy
step use the same wrapper, never the store directly; `_judge`'s no-item path (today `capture_supersede(ws,
source_key, "")`) calls `crm_capture_release_thread(actor, ref, '')`, which
removes the entry from any open capture item, recomputes that item's totals
(reason and confidence from its newest remaining entry), expires an item
left with no entry (`review.expired` audit row), and returns `{removed,
expired}`. The 073 `crm_capture_supersede` stays in the tree, unused by new
code (PD-25). An entry with `origin = 'deal_document'` is never released and
a `deal_document` entry releases nothing (PD-39): `_deal_documents` makes
one signal per company for a document sent to two known companies (073
behaviour, the loop over `company_of[d['id']]`), so such a conversation can
sit in one deal-document item per company and in one discovery item, the
stated exception of FR-CAP-GRP-07. A later document in the same thread
replaces the entry inside its own item, by `ref`.

**Deal-document signals** (`deal_signal_item`, made by the sync) use the
`capture:deal` key and the group upsert too, so a document and a discovery
signal for one company make one item (FR-CAP-GRP-06; PD-23). The newest
signal sets the stage and the deal choices; an older one only joins the
list.

**Consolidating the open items in production** (FR-CAP-GRP-08; PD-26).
`capture.run(ws, now)` calls `_consolidate(ws)` right after the `mode ==
OFF` check and before every other gate (email sync off, no capturing
mailbox, dry run finished, Gmail key): those gates do not concern it, since
it calls neither Gmail nor Claude. With capture Off nothing is read or
changed, so Off stays the kill switch (NFR-CAP-05):

Counts `consolidated` (items expired into a survivor) and `rekeyed`.

1. read the workspace's open capture items (`new_lead`; `deal_signal`
   with either origin) — **never** a decided one;
2. compute each one's target key in Python (only Python normalises names
   as the builders do; PD-40): `capture:linked`/`capture:deal` with its
   `record_id`; otherwise `company_key` of the finding stored on its
   candidate (`crm_capture_candidates` by `source_link.mailbox_id` and
   `thread_id`) when that finding exists; otherwise from the suggestion,
   where `company_name` counts as Claude's only when it differs from the
   suggestion's `domain`, its `email` and `"<first_name> <last_name>"`
   (`_company_name` fills it from those when Claude gave none), so such an
   item keys by domain, then email, as a new conversation from that sender
   would;
3. plan the groups that need work: two or more items with one target key,
   or one item still on a pre-074 key (`source_key LIKE 'gmail:%'`);
4. send the plan to `crm_capture_consolidate(actor, plan)`, which, under
   the capture lock and per group: locks the members `FOR UPDATE` and
   keeps only those still open; adds an
   open item already holding the target key; picks the survivor (newest
   `created_at`, then `id`); builds every member's entries (a pre-074 item
   gives one entry from its `source_link`, `source_version` and
   `suggestion.evidence`); expires each other member first (`status =
   'expired'`, `decided_at = now()`, `result = {"superseded_by":
   <survivor>, "reason": "Merged into one suggestion for <company>."}`,
   one `review.expired` audit row with `after.superseded_by`), then re-keys
   the survivor (`source_key`, `dedupe_key`, `source_link`, totals,
   `revision + 1`). Losers keep their own `source_link`, so no reference is
   lost even in history.

A second run plans nothing. With the 31 production items (HearSight nine)
it leaves one item per company; whatever the owner decided meanwhile is
skipped because the function re-reads status inside the lock. The
survivor's expiry clock is the newest member's, the longest left
(FR-CAP-GRP-09). Consolidation runs in Dry run and Live, never in Off.

**Approvals and refusals at the same time** (PD-38). An approval claims the
item (`status = 'approved'`), runs `_new_lead`, and only then files; a
refusal or a failure releases it through `crm_review_release` (068). Three
interleavings are handled:

- *A conversation arrives while the item is claimed.* The upsert's `FOR
  UPDATE` re-check sees the row is not open and inserts a fresh open item
  on the same key; the claimed item is not changed, so the approval files
  exactly what it loaded and the new conversation is in an open item.
- *The claimed item is refused while a fresh item holds its key.* 068
  would expire the refused item with its refusal, stranding its
  conversations. 074 replaces `crm_review_release` (copied from 068, lines
  marked `-- 074`): for a `new_lead` or `deal_signal` with
  `source_link ? 'threads'` (or a pre-074 capture key), it takes the
  capture lock, and before expiring the refused item merges its
  `source_link.threads` into the open item (same merge as the upsert,
  totals recomputed, `revision + 1`). The skip rule ignores expired items
  that carry a refusal, as a second guard.
- *The refused item reopens while another open item holds one of its
  conversations* (released there while it was claimed). The replaced
  `crm_review_release`, on the reopen path, removes from the reopened item
  every entry whose `ref` another open capture item lists; an item left
  with none expires instead of reopening (refusal kept, `review.expired`).

**Approving** (`review._new_lead`, FR-CAP-GRP-04; PD-35): after the
company, one person per distinct entry `person_email` (the suggestion's
person for the latest, names from each entry otherwise), each through the
same `base.create` path as today's one person, or linked to the company
when the address is already a person of the workspace; then
`_queue_filing` once per entry with a `mailbox_id` (falling back to the
single top-level link for an item without `threads`). The result adds
`people: n` and `threads_filed: n`, where only entries whose sender is a
known person after this step count: with every sender a person,
`_take` files webmail senders and name-keyed groups too (a pre-074 entry
without `person_email` uses the suggestion's address). Where email sync is switched off (production
today), `file_thread` stores nothing, as before this revision; the items
and records are still made (risk R1-6). **Dismissing** is unchanged; the
ignore choice uses the latest `suggestion.email`.

**Display.** `new_lead_detail`: "3 conversations · 9 emails · <reason>"
("1 conversation" omitted when there is one, as today's "2 emails · …").
`review-item.tsx`: evidence row *Conversations*; approve label "File these N
conversations" / "File this conversation"; an expired item with
`result.superseded_by` reads "Merged into another suggestion".

### R1.3 I2 — an honest recall line

**Fates** (`capture.FATES`; PD-27): `filed_known`, `known_now`, `found`,
`judged_otherwise`, `ignored`, `internal_only`, `calendar`, `automated`,
`outside_scope`, `not_judged`, `academy_unrouted`, and the legacy
`skipped_rules` (accepted when read, never written). The schema `Literal`,
the TypeScript union and `FATE_TEXT` list all twelve.

**Which reason** (`_fate`, in this order, the *decision* order;
FR-CAP-RPT-06's list is the *display* order; headers and records only until
step 7):

1. Gmail returns no message, or `MalformedThread` (a message with no id,
   unreadable headers or a non-numeric `internalDate`) → `not_judged`
   (today `outside_scope` / `skipped_rules`);
2. no message in Primary or Sent → `outside_scope`;
3. nothing survives the rules → the new `_drop_reason(thread)`: for each
   in-scope message, `internal` (`email_rules.classify(...).is_internal`,
   or an invitation with no outside party — FR-CAP-RPT-06: an invitation
   between colleagues only is internal-only), `calendar`
   (`is_calendar_notice` otherwise), or `automated` (`is_bulk` otherwise); the thread takes the reason
   with most messages, ties `calendar` > `automated` > `internal`; fates
   `internal_only`, `calendar`, `automated`. `_survivors` is unchanged for
   the live path; `_drop_reason` is its sibling over the same rules;
4. ignored → `ignored`; 5. known before → `filed_known`; 6. known now →
   `known_now`;
7. Claude: a per-conversation failure (`asked is None`) → `not_judged`
   (today `judged_otherwise`); an Academy finding (high or medium) →
   `found` when the Academy target exists, else `academy_unrouted`; a lead
   or deal signal (high or medium) → `found`; else `judged_otherwise`.

**The line** (`recall_line(rows)`, from `recall_summary(rows) ->
{total, tested, found, untested: {reason: n}, capped}`; PD-28):

- found = `found + academy_unrouted` (Claude found both; PD-28);
  tested = found + `judged_otherwise`;
- untested reasons in the fixed order of FR-CAP-RPT-06, where *involve
  contacts now in the CRM (filed automatically)* = `filed_known +
  known_now`, then `ignored`, `internal_only`, `calendar`, `automated`,
  `outside_scope`, `not_judged`, legacy `skipped_rules` ("were skipped by
  the rules");
- tested 0: "None of your {total} labelled conversations could be tested:
  {list}."; else "Of the {tested} that could be tested, the dry run found
  {found}." (with ", of which {n} were course enquiries the Academy could
  not take" before the full stop when `academy_unrouted > 0`) plus, when
  any are untested, " {u} could not be tested: {list}."; plus " Only the
  first 500 were checked." only when the run is **capped**: `_recall`
  appends one marker row `{"capped": true}` to the recall it passes to
  `crm_capture_finish_dry_run` only when it stops at `RECALL_CAP` with a
  labelled conversation still unchecked (a mailbox with exactly 500 is not
  capped); `_recall_rows`, `recall_summary` and `_recall`'s resume set skip
  rows without `thread_id`;
- the list joins with commas, as the owner wrote it: "36 involve …, 15
  were internal-only, 9 were calendar notifications"; singulars "1 conversation", "1 was", "1 is", "1
  involves".

`overview()` returns `recall: {line, rows, summary}`; the page renders the
summary as a list under the line and each row's `FATE_TEXT`. A run stored
before the revision has old fates only; the same code renders it
(FR-CAP-RPT-10). The MCP tools do not show the recall (checked:
`backend/app/mcp/crm.py` has no recall), so no MCP text changes.

### R1.4 I3 — course enquiries to the Academy

**Classification** (PD-29). `DiscoveryOutput.business: Literal["labs",
"academy"] = "labs"`. `INSTRUCTION` gains: "business: academy when the
enquiry is about courses or training for individuals or students — a course
enrolment, student training, a college or university programme; labs for
AI services, products, software or staffing." `CLASSIFIER_VERSION =
"010.2"`. Old decisions are not re-judged for the bump. `finding_of` keeps
`business`; in a workspace that is not `tva_linked` it is forced to
`labs`; a missing key reads as `labs`.

**Target** (`capture._academy_target()` → `{workspace, admins, roles,
active}` or None): the `list_workspaces()` row with key `academy`, at least
one `list_members` row with role `admin` whose profile is active, and the
Academy's `capture_routing` setting `{"accept_routed": true}` (PD-41; a new
settings key, seeded on in every workspace by 074, read only for the
Academy, changed by an Academy Admin through `PATCH
/crm/settings/capture_routing`). Cached per run.

**Routing** (`_route_academy`, live `_judge` step g and `apply`; PD-30):
for a high- or medium-confidence `new_lead` or `deal_signal` with
`business = 'academy'`:

1. Labs: no item; `crm_capture_release_thread(labs, ref, '')` lets any
   earlier Labs item go;
2. no target (no workspace, no active Admin, or `accept_routed` false) →
   `finding.routed = false`, count `academy_unrouted`, stop;
3. otherwise `finding.routed = true` and count `academy_routed` —
   **whatever the Academy step then does** (PD-42) — and run the Academy
   step under a `sync` actor whose `workspace_id` is the Academy's. Its
   outcome never returns to a Labs row, report or count:
   a. every outside participant on the **Academy's** ignore list → no item;
      logged only, as the structured event `capture.academy_skipped` with
      the Academy workspace id and reason `ignored` (no address);
   b. otherwise `academy_lead_item(finding, mailbox_email, thread_id, …)`: a
   `new_lead` (a deal signal becomes one with `deal_stage_hint`; Labs never
   sees Academy deals), keys as R1.2 within the Academy (`capture:<key>`,
   or `capture:linked` with the Academy company that `duplicate_of(academy,
   finding)` matched), `source_link = {"threads": [entry]}` where the entry
   is `{ref: "gmail:<mailbox email>:<thread_id>", thread_id, message_id,
   first_at, latest_at, message_count, owner_replied, origin:
   "academy_routing", reason, confidence}` — **no `mailbox_id`**, so no
   top-level link, no `thread_key`, nothing to file;
   `suggestion.evidence.origin = "academy_routing"`,
   `evidence.mailbox_email`; owner `owner_for("new_lead",
   roles=academy_roles, active, mailbox_owner)`; stored with
   `review.capture_group_upsert` (masked) under that actor, so the audit
   row is in the Academy; a skip there for an earlier Academy decision is
   logged the same way (reason `decided`).

The Labs candidate's `finding` keeps `business` and `routed` and sets
`duplicate_company_id = null`: no Academy id, and nothing the Academy
decided, is ever written in Labs; Labs rows look the same for an enquiry
the Academy filed, joined, skipped or ignored. Live, `_record` writes it
with the judgement; on apply, `crm_capture_apply_routed` (074) writes it
for every routed candidate just before `crm_capture_apply`, with the run's
`academy` and `academy_unrouted` counts (an enquiry of a mailbox no longer
captured counts as unrouted).

**Ignore lists.** Live, the Labs ignore list is read before any text
(FR-CAP-DIS-03), so a course enquiry whose every outside person is on it
is recorded `ignored` and never judged or routed; only then can the
Academy's list apply. On apply, a routed finding reads the Academy's list
alone (the Labs list is not consulted for it).

**Back to Labs** (FR-CAP-ACAD-09). The job does not rely on the candidate's
previous finding: `crm_capture_mark` (071) clears `finding`, `outcome` and
`judged_message_id` when a later message reopens the candidate, so by the
time `_judge` runs again the earlier answer is gone. Instead, **whenever a
Labs judgement of a conversation (live `_judge` or `apply`) ends as a Labs
item or as no item, and the Academy workspace exists** (key `academy`; its
Admins and its `accept_routed` switch do not matter here, they decide only
routing), the job calls
`crm_capture_release_thread(<academy sync actor>, "gmail:<mailbox
email>:<thread_id>", '')`. It does nothing when the Academy holds no such
entry; its result is not counted in Labs.

**Approving in the Academy** (`review._new_lead`, when `evidence.origin ==
"academy_routing"`; PD-31): company, lead stage and person as today in the
Academy context; then one note through `base.create(approver, "note",
{"kind": "note", "body": …, "occurred_on": latest_at[:10], "links": [the
company, the person]})` (the `links` shape `records._targets` takes;
verified in task T444), linked to the company and every person made or
linked (one per distinct entry `person_email`, as in Labs), body "Email
conversation in <mailbox email>: <n> emails, <first> to <latest>. <reason>"
(each entry's own mailbox, from its `ref`; `evidence.mailbox_email` only
when the ref names none)
per entry, cut to the note's limit and passed through `_masked` (Academy
Viewers read notes; FR-CRMAI-LLM-05); no `_queue_filing`; result
`{company_id, person_id, people, note_id, linked, threads_filed: 0}`.

**Report** (FR-CAP-RPT-09; PD-32). `overview()` adds `academy` (high and
medium Academy findings of the run) and `academy_unrouted` (those with
`routed = false`, or, for a run not yet applied, the whole count when there
is no target now) to the counts, computed in Python from
`dry_run_findings`; once the run is applied (its candidates are live
decisions then), from the counts `crm_capture_apply_routed` stored with the
run, 0 for a run applied before 074; `crm_capture_report` (072) is not
replaced. The page labels the first row "to be sent to the Academy" until
the run is applied, and shows both rows only when `academy > 0`. Live job
runs count `academy_routed` (handed over) and `academy_unrouted`; there is
no Labs count of what the Academy did (PD-42).

**Budget** (FR-CAP-ACAD-08). `ai.call(labs, "capture", …)` is unchanged:
the usage row and the spend are Labs'. The Academy's gate is not consulted.

**Isolation argument.** The constitution forbids a CRM *record* crossing
workspaces. What crosses here is a suggestion composed only of values read
from Gmail (names, an address, dates, counts, a reason Claude wrote from the
email), the same values the Academy would hold had the mail reached an
Academy mailbox. No Labs company, person, deal, mailbox, candidate or run id
enters any Academy column (source key, dedupe key, link, suggestion,
result, audit), and no Academy id enters any Labs row (candidate finding,
report, job counts), nor any fact the Academy decided (its ignore list, its
earlier decisions, its duplicates). The one Academy-derived fact visible in
Labs is whether the Academy takes enquiries now (`routed = false` /
`academy_unrouted`): an accepted disclosure, spec FR-CAP-ACAD-07. Approval
happens in the Academy by an Academy member. Tests assert both directions
with the isolation sweep's markers, and that Labs rows are identical for an
Academy-ignored and a routed finding.

### R1.5 I4 — invitations from outside people

**Rules** (`domain/crm/capture.py`; PD-33):

- `_INVITATION_SUBJECTS = ("invitation:", "updated invitation:", "updated
  invitation with note:", "invitation from google calendar:")`;
  `_CALENDAR_NOTICE_SUBJECTS = ("accepted:", "declined:", "tentatively
  accepted:", "canceled event:", "cancelled event:", "canceled event with
  note:", "cancelled event with note:", "proposed new time:",
  "notification:")`;
- `is_invitation(headers)` — subject prefix in `_INVITATION_SUBJECTS`,
  case-insensitive, no `Re:`/`Fwd:` stripping (a reply to an invitation is
  a person writing, already ordinary mail);
- `invite_parties(headers, sender, outside)` — the outside addresses minus
  `_CALENDAR_SENDER`, plus `Reply-To`'s address when the sender is
  `_CALENDAR_SENDER` and it is outside;
- `is_bulk(headers, sender, outside=())` keeps today's checks first and
  in order — `List-Unsubscribe`, `Precedence` bulk/list/junk,
  `X-Autoreply`/`X-Autorespond`, a delivery report, and a no-reply local
  part other than `_CALENDAR_SENDER` — each still returning True. Only then,
  when `is_invitation(headers)` and `invite_parties(...)` is not empty, it
  returns False, overriding just the three signs an invitation always
  carries: the invitation subject prefix, the `_CALENDAR_SENDER` rule, and
  `Auto-Submitted` when the sender is `_CALENDAR_SENDER` (`Auto-Submitted`
  from any other sender is still bulk). `_CALENDAR_NOTICE_SUBJECTS` and
  invitations without an outside party stay bulk. So "Invitation: …"
  webinar mail with `List-Unsubscribe` or from `no-reply@` is bulk;
- `is_calendar_notice(headers, sender)` — the calendar sender or a notice
  subject or a party-less invitation; used by I2;
- `MEETING_HOSTS = frozenset({"google.com", "meet.google.com",
  "calendar.google.com", "zoom.us", "teams.microsoft.com",
  "teams.live.com", "webex.com"})` excluded from `website_choices`;
- `invite_rescan_query(after_epoch, scope) = f"after:{after_epoch} {scope}
  subject:invitation"` (`scope` from `deal_doc_scope`; the code re-checks
  the prefix, since `subject:` matches words);
- `email_rules.HEADERS` + `"Reply-To"`; `classify` adds the `Reply-To`
  address to the participants only when the sender is the calendar sender
  and the subject is an invitation, so matching (known → filed) sees the
  organiser.

**Call sites.** `_take` passes `info["outside"]` (filtered by `_ADDRESS`,
moved before the bulk check), `_deal_documents` and `_survivors` likewise.
`_survivors` sets `info["invite"] = True` on a kept invitation;
`prompt_parts` adds `("Conversation type", "meeting invitation")` when any
judged message has it, and `INSTRUCTION` adds: "A meeting invitation is a
lead only when the conversation shows interest in buying; an invitation to
a webinar or event is not relevant."

**Rescan** (FR-CAP-INV-05; PD-34). `email_sync._rescan_invites(ws, mailbox,
ours, configured, now)`, in `sync_page`'s incremental branch after
`_deal_documents`, when the mailbox is capturing and
`invites_rescanned_at` is null, in its own `try/except` like deal
documents:

1. `gmail.search(mailbox, invite_rescan_query(midnight(capture_start_date),
   deal_doc_scope(labels)), page_token=mailbox.invite_rescan_token,
   page_size=100)`, at most `INVITE_RESCAN_PAGES = 5` pages per sync;
2. each page's ids → `gmail.message` metadata → keep `is_invitation` →
   `_take(ws, mailbox, ids, ours, set(configured))`, which already skips
   stored ids, out-of-scope, internal and bulk mail, files known mail and
   marks candidates (idempotent: a candidate only moves forward);
3. save `invite_rescan_token` after each page (`mailbox_progress`); on the
   last page set `invites_rescanned_at = now`, token null;
4. a failure counts `invite_rescan_failed` and leaves the token: the next
   sync resumes from it. When a request made **with a saved token** is
   refused as invalid (Gmail 400 `invalidArgument`, or 404), the token is
   cleared and the rescan restarts from its query start on the next page
   request, counted `invite_rescan_restarted` (PD-43): safe, because
   `_take` skips stored ids and `crm_capture_mark` only moves a candidate
   forward. Count `invites_rescanned`.

The API never resets the two columns (a later start-date change or
re-backfill already reads with the new rule). Both columns are skipped by
the audit (`crm_audit_row` re-copied from 071, `-- 074`) and listed in
`_MAILBOX_COLUMNS` and `MAILBOX_PROGRESS`.

### R1.6 Data changes

Migration `074_crm_capture_improvements.sql` (data-model §9): two mailbox
columns; `crm_audit_row()` from 071 with both in the mailbox skip;
`crm_capture_group_upsert`, `crm_capture_release_thread`,
`crm_capture_consolidate`; `crm_review_release()` replaced from 068
(PD-38); `crm_workspace_settings_key_check` replaced from 072 adding
`capture_routing`, seeded `{"accept_routed": true}` in every workspace, and
`crm_workspaces_seed_ai()` replaced from 072 to seed it (PD-41); grants. No
new table, no `uuid[]` column. Recall fates, `finding.business`/`routed`, and the
`source_link.threads` list need no schema change (free jsonb; the
candidate finding stays under 4096 bytes).

### R1.7 Privacy and isolation

- G-5 and NFR-CAP-02 hold: every new recall reason is decided from headers
  and records before any text is read; recall rows store a fate only.
- Invitations reach Claude only when they pass the same rules, the ignore
  list and the known check as any mail. An invitation's body (its agenda)
  is text as any other message's, held in memory only.
- `source_link.threads` holds Gmail ids, dates, counts, a reason and a
  confidence; no body, no subject. It is blanked by `009`'s 90-day
  tombstone with the rest of `source_link`.
- Academy items: R1.4's isolation argument; the sweep tests both
  directions.

### R1.8 Failure handling

| Failure | Effect |
|---|---|
| Consolidation RPC fails | logged, counted `consolidate_failed`; the run continues (new items still group by the new keys); next tick retries |
| A group upsert fails | the candidate stays `pending` (sequencing rule: item stored before the decision), so the next run retries it |
| Group upsert skipped (already decided) | the decision is recorded; no item; counted `items_skipped` |
| Academy target lookup fails | treated as no target for this run (`academy_unrouted`); the finding is recorded so a re-apply can route it |
| Academy note fails on approval | the approval is refused after the company and person exist; `result = {company_id, person_id}` is kept and reused on retry, as today's retry rule |
| Rescan page fails | `invite_rescan_failed`; token kept; history cursor and deal documents unaffected |
| Gmail refuses the saved rescan token as invalid | token cleared, rescan restarts from its start; `invite_rescan_restarted` |
| Group upsert meets a claimed row | fresh open item on the same key; a later refusal merges the claimed item's threads into it |
| A thread entry list grows large | no cap; at about 300 bytes an entry, 1,000 conversations is 300 KB, far above any company's two weeks |

### R1.9 Test strategy

| Layer | New tests |
|---|---|
| Pure rules | `company_key` (domain, sibling forms, webmail never, Claude name only, address, hash at 300); item keys; invitation, notice and party rules (`Reply-To` from the calendar sender, the sender never an outside party); `MEETING_HOSTS`; `invite_rescan_query`; fates and `recall_line` (none tested, some tested, singulars, cap, legacy `skipped_rules`, Academy clause); `DiscoveryOutput.business` default; `INSTRUCTION` sentences; `academy_lead_item` holds no uuid |
| Fake | `crm_fake.py` mirrors the three 074 functions (lock order irrelevant in-process), the mailbox columns, and Academy's ignore table rows; `FakeGmail.search` matches `subject:` |
| Job | grouping across three conversations, by name, webmail apart; order independence under apply (older proposal after newer closed-won keeps the newer stage and choices); one document to two companies keeps two signals; consolidation keyed from the candidate's finding and, without one, never by a person-name fallback; Off leaves items untouched; Academy release after a real `mark_candidates` reopen; money masked in suggestion and `threads[].reason`; re-suggestion after dismissal (same message skipped, new conversation made); thread move and empty-item expiry; deal-signal one per company; consolidation of nine items (survivor newest, eight expired with `superseded_by`, audit rows, decided untouched, second run no-op); recall reasons; Academy routing, no workspace, no active Admin, owner choice, Academy ignore, Labs item released, back-to-Labs release, usage row Labs |
| Review | approve files every entry (`threads_filed`), legacy single link; Academy approve creates company, person, note, files nothing; Academy dismiss writes the Academy ignore |
| Sync | invitation from an unknown outsider → candidate; from a known contact → stored; notices and internal invites skipped; an invitation with `List-Unsubscribe` or from `no-reply@` still bulk; invalid saved token restarts the rescan; calendar sender with outside `Reply-To` → candidate; rescan pages, resumes, finishes once, idempotent second pass; failure keeps the token |
| Isolation | `test_crm_isolation_sweep.py`: an Academy item holds no Labs uuid (mailbox, candidate, company, run); Labs rows hold no Academy id; Academy-only member sees it, Labs-only member does not |
| Database | 074 functions: lock, skip rule (expired-with-refusal ignored), release, consolidate (idempotent, decided untouched, collision with an open key), audit rows; the three claim/refusal interleavings of R1.2; deal choices replaced only by the newest entry; `capture_routing` key accepted and seeded; mailbox columns not audited; grants; `_010_sql` sweep names |
| Frontend | `pnpm build` (exhaustive `FATE_TEXT`), `pnpm lint` |

### R1.10 Release

1. Merge `010-capture-improvements` to `main` after every gate.
2. `npx supabase db push` (074 only). Never commit `supabase/config.toml`.
3. Railway, all five services from the one merge commit: `api`, `worker`,
   `beat`, `worker-crm-ai`, `worker-crm-sync` (the sync's new bulk rule and
   rescan, and the job's grouping, must ship together: a sync on the old
   code would keep writing per-thread deal-document keys).
4. Netlify with `--skip-functions-cache`.
5. Checks: within an hour of the deploy, the first `capture` tick's run
   shows `consolidated`; in SQL, `SELECT dedupe_key, count(*) FROM
   crm_review_items WHERE status='open' AND type='new_lead' GROUP BY 1
   HAVING count(*) > 1` returns nothing; HearSight has one open item whose
   `source_link->'threads'` has nine entries (or fewer if decided
   meanwhile); the expired members carry `result->>'superseded_by'` and a
   `review.expired` audit row each; after the next sync of
   `navaneeth@nunnarilabs.com`, `invites_rescanned_at` is set (or the token
   advancing) if the mailbox is on Primary and Sent; `/health/jobs` ok.
6. Owner: the honest recall line appears on the **next** dry run; the
   existing report keeps its old fates. Course enquiries reach the Academy
   only once its workspace has an active Admin (its *Accept course
   enquiries from Labs* switch is on by default). Course enquiries already
   open in Labs before 074 stay there (FR-CAP-ACAD-11): the owner dismisses
   them or they expire within 14 days.

### R1.11 Risks

| # | Risk | Mitigation |
|---|---|---|
| R1-1 | A name-only key merges two different companies with a generic name | only Claude's own name; the reviewer sees every conversation's count and can edit; dismissing does not ignore a domain |
| R1-2 | A company merge (065) expiring a moved item on collision drops its threads from the open item | consolidation each tick re-groups by `record_id`, but an item already expired by the merge stays expired; its references remain on it (history); accepted |
| R1-3 | Invitation header shapes are inferred, not checked on production mail | the invitation exception overrides only the subject, calendar-sender and its `Auto-Submitted` signs, every other bulk header still applies; `Reply-To` for the calendar sender; sync tests on both shapes; the owner reviews what comes |
| R1-4 | Discovery cost rises with invitation candidates | each is one call under the Labs budget; webinar organisers go to the ignore list after one dismissal |
| R1-5 | Every new conversation bumps the revision; a form left open gets 409 | expected; the message says to review it again |
| R1-6 | Email sync off in production: approved groups queue filings that store nothing | existing behaviour; the deployment note says so |
| R1-7 | Academy routing depends on Claude's judgement of "course" vs "service" | high/medium only; the Academy reviewer dismisses mistakes; the Labs report counts routed findings |

### R1.12 Planning decisions

| # | Decision | Why |
|---|---|---|
| PD-22 | Company key: normalised domain, else Claude's company name, else address; linked leads by record (`capture:linked`); keys keep the `company:` head | spec FR-CAP-GRP-01; the merge rewrite (065) keeps working |
| PD-23 | Deal-document and discovery signals share one key per company (`capture:deal`); newest stage wins | "one open item per deal"; two open items for one deal confuse the owner |
| PD-24 | Re-suggestion is per conversation state (`ref` + `message_id`) over every decided, non-superseded capture item, plus the pre-074 `source_key`/`source_version` form | a company key would otherwise let a re-judged, already dismissed conversation back, and pre-074 dismissals would be forgotten |
| PD-25 | `crm_capture_supersede` (073) stays defined and untouched; new code calls `crm_capture_release_thread` | applied migrations are not edited; dropping a function is not needed |
| PD-26 | Consolidation is Python-planned, SQL-applied, every tick in Dry run and Live, after the Off check and before the other gates; losers `expired` with `result.superseded_by` and `result.reason`, audit `review.expired`; no new status | idempotent, safe against decisions made meanwhile; a `superseded` status would mean a CHECK and frontend change for no user benefit; `refusal` is kept for approval refusals |
| PD-27 | New fates `internal_only`, `calendar`, `automated`, `not_judged`, `academy_unrouted`; `skipped_rules` read-only legacy | stored runs must still validate |
| PD-28 | `filed_known` and `known_now` both count as "contacts now in the CRM (filed automatically)", not tested; `academy_unrouted` counts as found, named "of which N were course enquiries the Academy could not take" | the owner's example puts them there; recall measures discovery, and Claude did find the enquiry |
| PD-29 | `business` defaults to `labs`, forced to `labs` outside the TVA-linked workspace; classifier `010.2` without re-judging | old answers stay valid; routing only ever goes Labs → Academy |
| PD-30 | Academy items carry only Gmail-derived values, no `mailbox_id`; keyed and grouped inside the Academy; Academy's ignore list and duplicates only | constitution isolation; nothing to file without a mailbox |
| PD-31 | Academy approval records a note on the company and person instead of storing mail | the Academy has no mailbox |
| PD-32 | Academy counts computed in Python from findings; `crm_capture_report` not replaced | no migration for a count |
| PD-33 | `Reply-To` fetched and used only for invitations from Google Calendar's sender | some organisers' invitations come from that sender with the organiser only in `Reply-To` |
| PD-34 | One rescan per mailbox, recorded by `invites_rescanned_at`, never reset by the API; five pages of 100 per sync, resumable by `invite_rescan_token` | past invitations had no marker; a later re-backfill reads with the new rule |
| PD-35 | Approval creates (or links) one person per distinct sender address in the item's entries; `threads_filed` counts only entries whose sender is then known | filing (`_take`) stores only mail of known people or known domains; webmail and name-keyed groups would otherwise file nothing yet stay blocked |
| PD-36 | Hidden guests (body only) are not read; an internal organiser's invite to outside guests stays skipped | headers only before the known check (G-5) |
| PD-37 | Thread list has no cap | `source_link` has no size CHECK; realistic volumes are small |
| PD-38 | 074 functions lock rows `FOR UPDATE` and re-check `open`; `crm_review_release` replaced so a refused capture item's threads merge into the open item on its key, and a reopened one drops threads held elsewhere; expired-with-refusal never blocks | claim (069) and release (068) do not take the capture lock; without this a conversation is lost or held twice |
| PD-39 | Deal-document entries are never released and release nothing | one document to two companies keeps one signal per company (073) |
| PD-40 | Consolidation keys an old item from its candidate's finding, else from the suggestion with the `_company_name` fallback recognised | a person-name key would split one sender in two and merge namesakes |
| PD-41 | Academy-side switch `capture_routing.accept_routed`, a new settings key, default on | each workspace governs what it receives; on matches the owner's request |
| PD-42 | Labs records only "handed over" (`routed = true`); the Academy step's outcome is logged in the Academy only; no `academy_ignored` count | constitution: no Labs-visible data from a workspace the caller is not in |
| PD-43 | An invalid saved rescan token restarts the rescan | otherwise a stale token fails every sync forever |
| PD-44 | Course enquiries open in Labs before 074 stay there | no re-judging for the bump (PD-29); they expire within 14 days |
