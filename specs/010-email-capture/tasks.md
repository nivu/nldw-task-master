# Automatic Email Capture — Tasks

| | |
|---|---|
| **Feature** | `010-email-capture` |
| **Input** | [`spec.md`](spec.md), [`plan.md`](plan.md), [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md) |
| **Shape** | Three sequential slices, S1 → S2 → S3, then D (docs, help, release). Each slice leaves the branch mergeable: every gate green, every new switch off by default |
| **Where** | `/Users/navaneethmalingan/nldw-task-master`, branch `010-email-capture` (paths below are relative to it) |

## How to use this file

- Format: `- [ ] T### [P] Description — path(s)`. **[P]**: touches only
  files no other open task in the same group touches and depends on nothing
  still open, so it can run in parallel. Tasks without it run in order.
- Slices in order; none depends on a later one.
- Migrations are `071`, `072`, `073`. A `CREATE OR REPLACE` copies the
  **latest** body in the tree (`grep -l "CREATE OR REPLACE FUNCTION
  public.<fn>(" supabase/migrations/*.sql | sort | tail -1`), never memory.
- Tests: `test_<area>_<rule-in-plain-words>`, classes per spec section,
  module docstrings cite FR ids. Gmail and Claude are always faked.
- Never commit `supabase/config.toml`.

### Gates — every slice's "done when" includes all of these

- [ ] `cd backend && uv run pytest -q` passes (whole suite)
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` pass
- [ ] Database, holding the shared lock, from a fresh reset, in **one** shell:

  ```bash
  LOCK="$(git rev-parse --git-common-dir)/db-test.lock"
  until mkdir "$LOCK" 2>/dev/null; do sleep 5; done
  trap 'rmdir "$LOCK"' EXIT
  npx supabase db reset \
    && (cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q)
  ```

- [ ] `cd frontend && pnpm build && pnpm lint` pass
- [ ] `git diff --cached --name-only` does not contain `supabase/config.toml`
- [ ] After a fresh reset: every mailbox `capture_source = 'labels'`, every workspace `capture.mode = 'off'`
- [ ] The commit message says what changed, why, and which journey it serves (the Seller's Journey)

---

## Setup

- [x] T001 Run every gate on the untouched branch; record the baseline test count in the S1 commit message — no files
- [x] T002 Read `crm_audit_row` (067), `crm_workspaces_seed_ai` and `crm_job_latest` (066) bodies and confirm with `pg_constraint` the names `crm_review_items_type_check`, `crm_workspace_settings_key_check`, `crm_job_runs_job_check`, `crm_ai_usage_job_check` — `supabase/migrations/` (read only)

---

## S1 — Known-contact capture and deal documents

**Goal.** A mailbox on *Primary and Sent* files Primary and Sent mail from
known contacts with no label; bulk mail is skipped; unknown conversations
leave candidates; SOWs to known clients with open deals become `deal_signal`
items. In `labels` mode nothing changes.

**Spec.** FR-CAP-SRC-01 to -08; FR-CAP-CHG-01 to -03; FR-CAP-REV-01 (`deal_signal`), -04; NFR-CAP-03, -05.

**Migration.** `071_crm_capture_sync.sql` (data-model §3.1–3.3).

- [x] T101 Write `071_crm_capture_sync.sql`: mailbox columns (`capture_source`, `capture_start_date`, `docs_checked_at`, `reconcile_after`); `crm_capture_candidates` (RLS, revoke, indexes, CHECKs); `crm_capture_mark()`; `crm_audit_row()` copied from 067 with `docs_checked_at` and `reconcile_after` in the mailbox skip (`-- 071`); `crm_review_items_type_check` + `deal_signal`; grants; header with Reversible — `supabase/migrations/071_crm_capture_sync.sql`
- [x] T102 [P] Pure rules: `MODES`, `scan_labels()`, `in_scope(label_ids, scan)` (a scan label and neither `TRASH` nor `SPAM`), `capture_after()`, `reconcile_window(mailbox, now)` (`min(capture_after, reconcile_after)`), `is_bulk()` (List-Unsubscribe, Precedence bulk/list/junk/auto_reply, Auto-Submitted≠no, X-Autoreply/X-Autorespond, no-reply local parts, delivery-status, calendar-invite subjects and sender), `DEAL_DOC_QUERIES`, `deal_doc_scope(label_names)` (`(category:primary OR in:sent OR label:"…") -in:trash -in:spam`), `deal_doc_query(kind, after, scope)`, `deal_doc_since(docs_checked_at, start_date, now)` (first run at most 14 days back), `deal_doc_stage(direction, deal_stage)`, `deal_doc_deals(deals, document_at)` (only deals created on or before the document), `deal_signal_item()` (dedupe key, title, evidence), `START_IN_FUTURE` — `backend/app/domain/crm/capture.py`
- [x] T103 [P] `HEADERS` + `"List-Unsubscribe"`; `mailbox_refusal` gains `capture_start_date` (not in the future) — `backend/app/domain/crm/email_rules.py`
- [x] T104 [P] `review.TYPES["deal_signal"] = "Deal signal"`; `owner_for` returns `mailbox_owner` for `new_lead`/`deal_signal` — `backend/app/domain/crm/review.py`
- [x] T105 [P] `MailboxIn`/`MailboxUpdate` + `capture_source`, `capture_start_date`; `ReviewItemType` + `"deal_signal"` — `backend/app/schemas/crm.py`
- [x] T106 `gmail.search(mailbox, q, *, page_token, page_size)` (`messages.list` with `q`, no `labelIds`, `includeSpamTrash=false`, ids only); `_get` retries 429 and 5xx: at most 3 tries in all, honouring `Retry-After` (capped 30 s) else 1 s then 4 s, then `GmailError` as today (plan §5.13); tests: 429 with `Retry-After` then 200, 503 twice then 200, three 500s raise, 404 and 403 not retried, `time.sleep` patched — `backend/app/services/crm/gmail.py`, `backend/tests/test_crm_gmail_retry.py`
- [x] T107 Store: `_MAILBOX_COLUMNS` + 4 columns; `MAILBOX_PROGRESS` + `docs_checked_at`, `reconcile_after`; `mark_candidates()` (RPC) — `backend/app/services/crm/store.py`
- [x] T108 Sync: `_label_ids(required=)`; `scan`/`ours`/`configured` in `sync_page`; `capture_after` at the backfill site and `reconcile_window` in `_reconcile` (clearing `reconcile_after` when that reconcile finishes); `changed_messages(..., trash_leaves=)` (`primary_sent`: `labelsAdded` TRASH/SPAM removes, `labelsRemoved` TRASH/SPAM re-adds when otherwise in `ours`; `labels`: as today); `in_scope` in `_take` step 1; `_take(..., configured)` steps 2, 4, 6, 8 of plan §5.4; `COUNTS` + `skipped_bulk`, `candidates`, `deal_signals`, `deal_doc_failed`; `_deal_documents()` at the end of the history phase (plan §5.5: scoped query, `in_scope`, internal-only and `is_bulk` skipped, only deals created on or before the document) — `backend/app/services/crm/email_sync.py`
- [x] T109 Mailbox routes: `_present` + 3 fields; add and update write them; null refused; cursor reset on `capture_source`/`capture_start_date`; `primary_sent` → `labels` also sets `reconcile_after` to the old start date (00:00 IST) — `backend/app/api/crm/email.py`
- [x] T110 Review service: `deal_signal` presentation (title, detail from company and suggestion), `EDITABLE["deal_signal"]`, `_deal_signal()` approve (create via `deals.create`, move via `deals.move`, at-or-past refusal, Labs Closed Won refusal passes through), dispatch in `approve` — `backend/app/services/crm/review.py`
- [x] T111 Fakes: `crm_fake.py` `DEFAULTS["crm_mailboxes"]` + 3 fields, table `crm_capture_candidates`, `mark_candidates`, `REVIEW_TYPES` + `deal_signal`; `FakeGmail.box()` + `CATEGORY_PERSONAL`, `SENT`, `TRASH`, `SPAM` (trash/spam/untrash history events); `send(..., labels=)` honours given labels (default `("Label_1",)` keeps adding `INBOX`); `search(q)` matching `filename:`/`has:attachment`/`after:`/`category:primary`/`in:sent`/`label:`/`-in:trash`/`-in:spam` against per-message `attachments=` and labels; `threads_get(thread_id)` — `backend/tests/crm_fake.py`, `backend/tests/test_crm_email_sync.py`
- [x] T112 [P] Rule tests — `backend/tests/test_crm_capture_rules.py`
- [x] T113 Sync tests, one class per FR: Labels-only unchanged (the existing file passes untouched except T111's fake); unlabelled Primary from a known person stored + linked + Last inbound + flags; Sent to a known New company → outbound + Contacted; newsletter, internal-only, calendar invite skipped with no row in any table; unknown sender → not stored, no company, one candidate; second message advances the candidate; Sales-labelled unknown sender → automation 2 as today; labelled bulk still stored `is_auto`; `TestEnteringScope`: a `labelsAdded` `CATEGORY_PERSONAL` event takes a known person's message; `TestLeavingScope`: moving out of Primary removes unless labelled or Sent, a `SENT` message that loses `CATEGORY_PERSONAL` stays, trashing or spamming a captured message removes it, untrashing takes it again; switching back to Labels only removes a captured unlabelled message 120 days old; labels all missing in `primary_sent` still syncs; backfill from start date not 90 days; cursor reset on source change; SOW sent → one `deal_signal` Proposal; received → Closed Won; no open deal → none; an SOW in Promotions makes nothing; a bulk-headed SOW mail makes nothing; a document dated before the deal was created makes nothing; the first search looks back 14 days, not to the start date; search failure counted, cursor kept — `backend/tests/test_crm_capture_sync.py`
- [x] T114 [P] Review tests for `deal_signal`: owner = mailbox owner; approve move to Proposal asks amount/close (pipeline refusal), with edits moves and creates the Labs tentative project; at-or-past refused; Closed Won in Labs refused with the handoff sentence; create on a New company refused by the pipeline sentence — `backend/tests/test_crm_capture_review.py`
- [x] T115 [P] Mailbox route tests (fields, defaults, refusal, reset, Admin only) — `backend/tests/test_crm_email_sync.py::TestMailboxes` (append)
- [x] T116 DB tests: mailbox defaults; `docs_checked_at` and `reconcile_after` writes make no audit row and a `capture_source` change does; candidate CHECKs; mark inserts, advances only forward, refuses other workspace, skips > 365 days; `deal_signal` item accepted; `NOT_AUDITED_010` partial (`crm_capture_candidates`); `_009_sql` narrowed to 060–070, `_010_sql` added — `backend/tests/db/test_crm_capture_db.py`, `backend/tests/db/test_crm_email_db.py::TestMailboxAudit`, `backend/tests/db/test_crm_security_db.py`
- [x] T117 Frontend: `CrmMailbox`/`CrmMailboxIn` fields; Mailboxes tab source select + start date + backfill wording; `ReviewItemType` + `deal_signal`; `review-item.tsx` `describe()`, evidence block, deal-signal form (Discovery next step/date; Proposal, and Closed Won outside Labs, amount, currency, close date, point of contact, project name in Labs), handoff link for Closed Won in Labs — `frontend/lib/api/crm-types.ts`, `frontend/app/(crm)/crm/[ws]/settings/page.tsx`, `frontend/components/crm/review-item.tsx`
- [x] T118 Gates; commit "Capture Primary and Sent mail from known contacts (010 S1)"

**Done when** every §13 S1 line has a passing test (Acceptance table) and the gates pass.

---

## S2 — Discovery dry run

**Goal.** An Admin starts a dry run; `crm_ai.capture` judges every candidate
since the start date through `009`'s wrapper with no phase on; the report and
recall check appear in Settings → Email capture; nothing is written to
records or the queue.

**Depends on.** S1.

**Spec.** FR-CAP-DIS-01, -02, -04, -05, -06, -07 (recorded), -10 (dry run); FR-CAP-RPT-01 to -03; FR-CAP-CHG-05, -06; NFR-CAP-01 to -04.

**Migration.** `072_crm_capture_discovery.sql` (data-model §3.4, §3.5).

- [x] T201 Write `072_crm_capture_discovery.sql`: settings key CHECK + `capture` seed (system actor); `crm_workspaces_seed_ai()` from 066 + `capture`; `crm_capture_dry_runs`; `crm_capture_record()`, `crm_capture_start_dry_run()`, `crm_capture_set_mode()` (cancels an open run; `live` needs an applied run), `crm_capture_finish_dry_run()`, `crm_capture_report()`; `cancelled` column and CHECK; job CHECK + `crm_job_latest()` from 066 + `capture`; `crm_ai_usage_job_check` + `capture`; grants — `supabase/migrations/072_crm_capture_discovery.sql`
- [x] T202 [P] Pure rules (append): `capture_refusal()`, `capture_mode()` (missing → off), `CLASSIFIER_VERSION`, `outside_of()`, `owner_replied()`, `trim()` (own text + first 20 lines of the quoted/forwarded block and signature, using `summaries`' quote patterns), `prompt_parts()` (12 messages, 24,000 chars, `trim`, websites part), `website_choices()`, `domain_of_finding()` (choice, else non-webmail `person_email` domain, `domain_from_text`), `failure_counts(exc)` (per-conversation vs run-ending), `INSTRUCTION`, `domain_for()` (webmail → None), `choose_email()`, `lead_stage_for()`, `finding_of()`, `recall_fate()`, `recall_line()`, sentences — `backend/app/domain/crm/capture.py`
- [x] T203 [P] `VALIDATORS["capture"]`; `ai_rules.USAGE_JOBS` + `capture`; `jobs.JOBS["capture"] = Job("Email capture", timedelta(hours=1))`; `JobName` + `capture` — `backend/app/domain/crm/settings.py`, `backend/app/domain/crm/ai_rules.py`, `backend/app/services/crm/jobs.py`, `backend/app/api/crm/jobs.py`
- [x] T204 [P] `CaptureSettingsValue`, `DiscoveryOutput`, `CaptureOverviewOut` — `backend/app/schemas/crm.py`
- [x] T205 `ai.gate`/`ai.call`/`_call` accept `phase: str | None` (None skips step 1 only) — `backend/app/services/crm/ai.py`
- [x] T206 `gmail.thread_meta()` (`threads.get format=metadata`, `HEADERS`, `labelIds` per message) and `gmail.thread_text(mailbox, message_ids)` (only the given ids, each `messages.get format=full` as `message_text` does: `text/plain` parts and attachment filenames; a 404 id is left out); docstrings name the two text readers (CHG-05); `summaries.py` docstring amended — `backend/app/services/crm/gmail.py`, `backend/app/services/crm/summaries.py`
- [x] T207 Store: `capture_setting()`, `pending_candidates(ws_id, mailbox_ids, newest_first)` (joins `crm_mailboxes` on `is_active` and `capture_source = 'primary_sent'`), `record_candidate()`, `start_dry_run()`, `set_capture_mode()` (RPC), `finish_dry_run()`, `capture_report()`, `latest_dry_run()`, `dry_run_findings()` — `backend/app/services/crm/store.py`
- [x] T207a Sync side of filing (moved from S3 so S2's step d queues a task that exists): `crm.capture_thread` task + `task_routes` entry to `crm_sync`; `email_sync.file_thread()` reusing `_take` with the mailbox's scan labels; sync test that it stores and links a known thread and skips Promotions/Trash messages — `backend/app/tasks/crm.py`, `backend/app/worker.py`, `backend/app/services/crm/email_sync.py`, `backend/tests/test_crm_capture_sync.py`
- [x] T208 Capture service: `run(ws, now)` with the checks of plan §5.6 in order (Gmail key check included), mailboxes filtered by `_owner_here`, oldest first in dry run, steps a–g (dry run: finding only; text read only for ids surviving a and b), `attempts`/`failed` for per-conversation failures only and run-ending failures leaving rows pending, chain guard (`crm:capture:next`), finish + recall (labelled threads judged as if unlabelled, plan §5.7); `start_dry_run(ctx)`; `overview(ctx)` — `backend/app/services/crm/capture.py`
- [x] T209 Task `crm_ai.capture` (`@jobs.crm_job("capture", seconds=jobs.AI_TASK_SECONDS)`, `ignore_result=True`, no arguments); beat `crm-ai-capture` minute 7 in a `# Spec 010` block; module docstring job table — `backend/app/tasks/crm_ai.py`, `backend/app/worker.py`
- [x] T210 Routes: `GET /crm/capture`, `POST /crm/capture/dry-run`; `ROUTERS` + `capture.router`; `PATCH /crm/settings/capture` special cases through `crm_capture_set_mode` (refuse `dry_run`; refuse `live` "Run and apply a dry run first." — always in S2; `live` needs a `primary_sent` mailbox; Gmail not configured; a change cancels an open run) — `backend/app/api/crm/capture.py`, `backend/app/api/crm/__init__.py`, `backend/app/api/crm/settings.py`
- [x] T211 Fakes: `crm_fake.py` tables `crm_capture_dry_runs`, settings key `capture` seeded off, the six RPCs, `insert_ai_usage` job list + `capture`; `FakeGmailThread(threads, texts, failing=)` recording `.meta_reads`, `.text_reads` — `backend/tests/crm_fake.py`, `backend/tests/crm_ai_fakes.py`
- [x] T212 [P] Rule tests (append) — `backend/tests/test_crm_capture_rules.py`
- [x] T213 Job tests: mode off / sync off / no `primary_sent` / finished dry run / rates unset / zero budget → skipped with reason and `text_reads == []`, `FakeClaude()` never called; internal, bulk, ignored-later (S3), known candidates never reach Claude; Gmail key missing → skipped before any candidate; known → `known` + `crm.capture_thread` queued on `crm_sync`; a Trash or Spam message and a Promotions or internal message in a candidate thread are never text-read nor sent to Claude; a spam-only candidate → `skipped`; candidates of an inactive, `labels` or owner-gone mailbox are not read; judged decision fields; low confidence recorded; dry run creates no item, company or person; not judged twice for the same latest message across a retry; newer message during judging → not recorded; 3 `OUTPUT_INVALID` failures → `failed`; `gmail.NotConfigured`, a Gmail 429/5xx after retries, a token refusal, a Claude timeout or HTTP error on the first candidate → run outcome failed, every row `pending` with `attempts` 0; time-out chains once; finish writes recall with each fate; a labelled thread whose company automation 2 created gets `found` or `judged_otherwise`, never `filed_known`, and stores no candidate row; a labelled thread of a company created before it → `filed_known`; usage rows job `capture` — `backend/tests/test_crm_capture_job.py`
- [x] T214 [P] Route tests: overview shape, Admin only (Member 403, Viewer 403, other workspace 404), dry-run start refusals, settings refusals (`live` refused "Run and apply a dry run first."); Off during a dry run cancels it and a new start then succeeds — `backend/tests/test_crm_capture_routes.py`
- [x] T215 [P] Privacy and pins: `TestCapture` in the privacy sweep (markers in body, quoted history, subject; skipped and not-relevant candidates store nothing); `thread_text` used only by `services/crm/capture.py`; `ai.call(..., None)` only from capture; job name tests — `backend/tests/test_crm_ai_privacy.py`, `backend/tests/test_crm_gmail_full_read.py`, `backend/tests/test_crm_ai_jobs.py`
- [x] T216 DB tests: key CHECK, seed in every workspace and by trigger for a new one; record writes only on matching id and pending; start resets dry-run rows, refuses a second open run, sets the setting with an audit row; `crm_capture_set_mode` off during a run cancels it (audit row) and a new start succeeds, `live` refused with no applied run; finish refuses a cancelled run; report counts; job and usage CHECKs; `crm_job_latest` lists `capture`; `NOT_AUDITED_010` + `crm_capture_dry_runs` — `backend/tests/db/test_crm_capture_db.py`, `backend/tests/db/test_crm_security_db.py`
- [x] T217 Frontend: `CrmJobName`, `CrmAiSpendJob` + `capture`, AI settings label; `getCrmCapture`, `startCrmCaptureDryRun`; Settings tab `email-capture` with `CaptureSettings` (mode Off, Run dry run button (Live arrives with T319), report: counts, cost, findings with low grouped, recall table and line) — `frontend/lib/api/crm-types.ts`, `frontend/lib/api/crm.ts`, `frontend/components/crm/ai-settings.tsx`, `frontend/components/crm/capture-settings.tsx`, `frontend/app/(crm)/crm/[ws]/settings/page.tsx`
- [x] T218 Gates; commit "Judge unknown conversations in a dry run (010 S2)"

**Done when** every §13 S2 line has a passing test and the gates pass.

---

## S3 — Live discovery, new leads, ignore list, apply

**Goal.** Live mode turns judged leads and deal signals into Review Queue
items within the hour; approving a `new_lead` creates the company and person
and files the conversation; dismissals feed the ignore list; a dry run can be
applied without new Claude calls; old decisions are purged.

**Depends on.** S2.

**Spec.** FR-CAP-DIS-03, -07, -08, -09, -10 (live), -11; FR-CAP-REV-01 to -04; FR-CAP-RPT-04; FR-CAP-CHG-04.

**Migration.** `073_crm_capture_live.sql` (data-model §3.3, §3.6, §3.7).

- [x] T301 Write `073_crm_capture_live.sql`: `crm_capture_ignores` (RLS, revoke, touch + audit triggers); `crm_review_items` nullable target + `crm_review_items_target` + type CHECK + `new_lead`; `crm_capture_apply()`, `crm_capture_purge()`; grants — `supabase/migrations/073_crm_capture_live.sql`
- [x] T302 [P] Pure rules (append): `ignored(outside, ignores)`, `ignore_refusal(kind, value, own_domains)`, `sibling_domains()`, `new_lead_item()` (dedupe, link variant, deal-stage hint), `to_item(finding, ...)` (None for low), apply sentences — `backend/app/domain/crm/capture.py`
- [x] T303 [P] `review.TYPES["new_lead"]`; `dedupe_key` accepts a `thread` target — `backend/app/domain/crm/review.py`
- [x] T304 [P] `CaptureIgnoreIn`, `ReviewDismissIn`; `ReviewItemType` + `new_lead`; `ReviewItemOut.target: dict | None` — `backend/app/schemas/crm.py`
- [x] T305 Store: `list_ignores()`, `apply_dry_run()` (RPC), `purge_capture()` (RPC) — `backend/app/services/crm/store.py`
- [x] T306 Capture service: live step g (duplicate check via `list_domains`, siblings and `records.duplicates`, on the finding's domain including one from `website_choice`; items through `review.upsert`); live order newest first; ignore step c; `apply(ctx, run_id)`; `add_ignore`, `remove_ignore`; Live accepted by the settings route once a run is applied — `backend/app/services/crm/capture.py`, `backend/app/api/crm/settings.py`
- [x] T307 (moved to S2 as T207a) — no files
- [x] T308 Review service: null targets in `_untrashed`, `present`, `today_section`, `_open_and_live`, `export_rows`; `EDITABLE["new_lead"]`; `_new_lead()` (plan §5.9 steps 1–5; reuse `item.result.company_id`; a refusal after the company exists releases with `result={company_id}`); `dismiss(ctx, id, body)` with ignore — `backend/app/services/crm/review.py`
- [x] T309 Routes: dismiss body; `POST /crm/capture/dry-runs/{id}/apply`; `POST`/`DELETE /crm/capture/ignores` — `backend/app/api/crm/review.py`, `backend/app/api/crm/capture.py`
- [x] T310 [P] MCP: `crm_review_decide` + `ignore`; docstring lines (count stays 21) — `backend/app/mcp/crm.py`
- [x] T311 Purge: `_purge` adds `capture_decisions` via `crm_capture_purge(now - 365 d)`; docstring job table — `backend/app/tasks/crm.py`
- [x] T312 Fakes: `crm_capture_ignores` (audited noun), `REVIEW_TYPES` + `new_lead`, nullable target in `review_upsert`/`add_review_item`, apply and purge RPCs — `backend/tests/crm_fake.py`
- [x] T313 [P] Rule tests (append) — `backend/tests/test_crm_capture_rules.py`
- [x] T314 Job tests (append): live high/medium → one item per thread, low → none; live judges a new candidate before a backlog of older pending ones; a second run makes no second item; newer message refreshes the open item (revision + 1); expired item not re-made for the same message; ignore by domain stops Claude; webmail → no domain; duplicate domain/sibling/name → linked item; Claude deal signal on unknown → `new_lead` with hint; apply makes items with no Claude call and sets Live — `backend/tests/test_crm_capture_job.py`
- [x] T315 Review tests (append): approve `new_lead` creates company (New → suggested stage), person, domain, queues `crm.capture_thread`, audit rows carry `suggestion_id`; linked variant creates only the person; retry after a person failure reuses the company; a webmail lead (no domain) whose person creation failed reuses the company from `item.result` on retry, never a second company of that name; never-contacted → New; dismiss with address / domain adds one ignore row audited as the dismisser; webmail domain refused; Member item owner may dismiss with ignore; later mail from the approved domain is filed with no Claude call (sync + job) — `backend/tests/test_crm_capture_review.py`
- [x] T316 [P] Route tests (append): apply refusals (not finished, cancelled, finished > 7 days ago, not latest, applied); a run started 10 days ago and finished 1 day ago is applicable; `PATCH live` accepted after an apply; ignores add/remove refusals, roles — `backend/tests/test_crm_capture_routes.py`
- [x] T317 [P] MCP test: dismiss with `ignore` through `crm_review_decide`; tool count still 21 — `backend/tests/test_crm_review_routes.py::TestThroughClaude`, `backend/tests/test_crm_mcp.py`
- [x] T318 DB tests: ignore table CHECKs, uniqueness, audit trigger, `from_item_id` SET NULL on purge; review target CHECK (null only for `new_lead`); upsert of a target-less item; merge re-keys a linked `new_lead`; apply refusals (window from `finished_at`, cancelled) and effects; purge deletes every decided row older than the cutoff, findings included, and keeps `pending` rows and newer rows; `NOT_AUDITED_010` complete, `TestThe010Additions` — `backend/tests/db/test_crm_capture_db.py`, `backend/tests/db/test_crm_security_db.py`, `backend/tests/db/test_crm_review_db.py`
- [x] T319 Frontend: `new_lead` card (company, domain, stage, person; link-existing), null `recordHref`, Dismiss with ignore choice (domain hidden for webmail), `dismissCrmReview(ws, id, ignore?)`; capture settings: ignore list editor, Apply with `window.confirm`, Live option (enabled once a dry run was applied) — `frontend/components/crm/review-item.tsx`, `frontend/lib/api/crm.ts`, `frontend/lib/api/crm-types.ts`, `frontend/components/crm/capture-settings.tsx`
- [x] T320 Gates; commit "Turn captured leads and deal signals into review items (010 S3)"

**Done when** every §13 S3 line has a passing test and the gates pass.

---

## D — Docs, help, amendments, release

- [ ] D01 [P] Architecture: email sync (capture source, scan labels, candidates, deal documents), intelligence layer (capture job, new item types), "Jobs, one line each" + `capture` and `crm.capture_thread` — `docs/architecture/crm.md`
- [ ] D02 [P] Help: "Mailboxes and email sync" (capture source, start date) and a new "Email capture" section — `docs/guides/10-crm-admin.md`
- [ ] D03 [P] Help: New lead and Deal signal items; dismiss with ignore — `docs/guides/12-crm-intelligence.md`
- [ ] D04 Regenerate the help JSON (`pnpm build:guides`) — `frontend/lib/help-content.generated.json`
- [ ] D05 [P] Deployment §9 "Email capture (spec 010)": migrations 071–073, services, release-day state, owner steps (plan §10.6, including "set `GOOGLE_SERVICE_ACCOUNT_JSON` on `worker-crm-ai`, the same key as `worker-crm-sync`" before "start the dry run"), switching on and off; the settings table row for `GOOGLE_SERVICE_ACCOUNT_JSON` on `worker-crm-ai` reads "Needed for email summaries, R-1 and email capture (010)"; fix §8's "060–068" to "060–070" — `docs/operations/deployment.md`
- [ ] D06 [P] Spec amendments per FR-CAP-CHG-01 to -03 (`007` SYNC-02, -05, -08) — `specs/007-crm/spec.md`
- [ ] D07 [P] Spec amendments per FR-CAP-CHG-04 to -06 (`009` REV, CHG-01, SUM-01 note) — `specs/009-crm-intelligence/spec.md`
- [ ] D08 Mark spec status "Planned and built" with the commit; update `.specify` feature pointer if used — `specs/010-email-capture/spec.md`
- [ ] D09 Gates; commit "Document email capture (010)"; merge `010-email-capture` to `main`
- [ ] D10 Release (plan §10): `npx supabase db push`; Railway `api`, `worker`, `beat`, `worker-crm-ai` (and `worker-crm-sync` if present) from the merge commit; Netlify with `--skip-functions-cache`; checks: `/health/jobs` ok after `capture` ticks, `/docs` and `/openapi.json` 404, every mailbox `labels`, every `capture.mode` off — no files
- [ ] D11 Report the owner steps still open (plan §10.6); update the project memory note on production state — no repo files

---

## Parallel groups

| Group | Tasks that may run together (disjoint files) |
|---|---|
| S1 after T101 | T102, T103, T104, T105 |
| S1 tests | T112, T114, T115 (after T108–T111) |
| S2 after T201 | T202, T203, T204 |
| S2 tests | T212, T214, T215 (after T207a–T211) |
| S3 after T301 | T302, T303, T304, T310 |
| S3 tests | T313, T316, T317 (after T306, T308–T312) |
| D | D01, D02, D03, D05, D06, D07 |

`backend/app/services/crm/store.py`, `schemas/crm.py`, `tests/crm_fake.py`
and `services/crm/review.py` are shared within a slice: their tasks never run
in parallel with each other.

---

## Coverage — spec to tasks

| Spec | Tasks |
|---|---|
| SRC-01 | T101, T105, T109, T117 |
| SRC-02, -08, CHG-01 | T102, T106, T108, T109, T111, T113 |
| SRC-03 | T102, T103, T108, T113 |
| SRC-04 | T108, T113 |
| SRC-05, CHG-02 | T101, T107, T108, T113 |
| SRC-06 | T102, T106, T108, T110, T113, T114 |
| SRC-07, CHG-03 | T102, T108, T109, T113 |
| DIS-01 | T202, T205, T208, T210, T213 |
| DIS-02 | T208, T209 |
| DIS-03 | T301, T302, T306, T314 |
| DIS-04 | T202, T206, T208, T212, T215 |
| DIS-05 | T204, T208, T213 |
| DIS-06 | T201, T208, T213, T216 |
| DIS-07 | T208, T302, T314 |
| DIS-08, -09 | T302, T306, T314 |
| DIS-10 | T208, T209, T213, T314 |
| DIS-11 | T301, T311, T318 |
| REV-01, CHG-04 | T101, T104, T110, T301, T303, T308, T117, T319 |
| REV-02 | T110, T207a, T306, T308, T315 |
| REV-03 | T306, T308, T309, T310, T315 |
| REV-04 | T104, T303, T314 |
| RPT-01 to -03 | T201, T208, T210, T213, T217 |
| RPT-04 | T301, T306, T309, T314, T316 |
| CHG-05 | T206, T215 |
| CHG-06 | D01 (no code) |
| NFR-CAP-01 | T205, T213 |
| NFR-CAP-02 | T113, T215 |
| NFR-CAP-03 | T106, T108, T208 |
| NFR-CAP-04 | T203, T209, T213 |
| NFR-CAP-05 | T113 (labels mode unchanged), D05 |

## Acceptance — spec §13 line to test

| §13 line | Test |
|---|---|
| S1: Labels only behaves as today | `test_crm_email_sync.py` (unchanged) + `test_crm_capture_sync.py::TestLabelsOnly` |
| S1: unlabelled Primary from a known person stored and linked; Last inbound and flags move | `TestKnownContacts::test_capture_files_a_primary_message_from_a_known_person` |
| S1: unlabelled Sent to a known company outbound; New → Contacted | `TestKnownContacts::test_capture_files_sent_mail_and_moves_new_to_contacted` |
| S1: newsletter, internal-only, calendar invite skipped; nothing stored | `TestTheSkip::test_capture_stores_nothing_for_bulk_internal_or_invites` |
| S1: unknown sender not stored, no company, one candidate | `TestCandidates::test_capture_leaves_one_candidate_for_an_unknown_sender` |
| S1: Sales-labelled unknown sender still creates the company | `TestHandLabelsWin::test_capture_keeps_automation_two_for_labelled_mail` |
| S1: moved out of Primary removed unless labelled | `TestLeavingScope::test_capture_removes_mail_moved_out_of_primary` |
| S1: SOW to known client with open deal → one `deal_signal` Proposal | `TestDealDocuments::test_capture_suggests_proposal_for_a_sent_sow` |
| S2: dry run writes decisions and report, no item/company/person | `test_crm_capture_job.py::TestDryRun::test_capture_dry_run_writes_only_decisions` |
| S2: internal, automated, known, ignored never reach Claude | `TestNeverClaude::test_capture_keeps_filtered_candidates_from_claude` |
| S2: recall lists every Sales/Leads conversation with its fate | `TestRecall::test_capture_recall_gives_every_labelled_thread_a_fate` |
| S2: zero budget or rates unset → skipped, no Gmail text read | `TestGates::test_capture_reads_no_text_when_a_gate_is_closed` |
| S3: new unknown conversation → at most one `new_lead` within the hour | `TestLive::test_capture_makes_one_new_lead_per_conversation` + `TestLive::test_capture_a_later_match_keeps_one_open_new_lead` |
| S3: approving creates company, person, domain, links; audited with approver and item id; later mail filed without Claude | `test_crm_capture_review.py::TestEndToEnd::test_capture_approval_creates_and_files` |
| S3: dismiss with ignore domain stops further candidates reaching Claude | `TestIgnore::test_capture_ignored_domain_never_reaches_claude` |
| S3: `deal_signal` into Proposal asks amount and close; Labs tentative project only then | `TestDealSignal::test_capture_proposal_needs_amount_and_close` |
| S3: not judged twice for the same latest message | `test_crm_capture_job.py::TestOnce::test_capture_judges_a_message_once_across_retries` + db `test_record_refuses_a_stale_message` |
| S3: applying a dry run creates items without Claude calls | `test_crm_capture_job.py::TestApply::test_capture_applying_a_dry_run_makes_items_without_claude` |
