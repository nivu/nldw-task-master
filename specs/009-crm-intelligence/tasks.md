# CRM Intelligence Layer — Tasks

| | |
|---|---|
| **Feature** | `009-crm-intelligence` |
| **Input** | [`spec.md`](spec.md), [`plan.md`](plan.md), [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md) |
| **Shape** | Nine sequential slices: S1a–S1c (phase 1), S2a–S2b (phase 2), S3a–S3b (phase 3), S4 (phase 4), S5 (hardening). Phase 0 is the owner's and has no tasks. Each slice leaves `009-crm-intelligence` mergeable: every gate green, every phase switch still off by default, nothing half-wired |
| **Where** | Worktree `../nldw-task-master-009` (all paths below are relative to it), branch `009-crm-intelligence`, branched from `008-tasks` |

## How to use this file

- Format: `- [ ] T### [P] Description — path(s)`. **[P]** means the task
  touches only files no other open task in the same group touches and depends
  on nothing still open, so it can be done in parallel. Tasks without it run
  in the order listed.
- Build the slices **in order**; none depends on a later one. Every slice
  starts by rebasing onto `008-tasks` (coordination rule 4) and ends with the
  gates.
- Migrations are `060`–`067` (rule 1). A slice never edits an earlier slice's
  migration once applied anywhere. A `CREATE OR REPLACE` of a function `007`
  or `008` defined is copied from the **latest version in the rebased tree**
  (data-model §1), never from memory.
- Tests are named `test_<area>_<rule-in-plain-words>` inside classes per spec
  section; each module's docstring cites its FR ids. Route tests use
  `tests/crm_fake.py`; job tests use `tests/crm_ai_fakes.py` (created in
  S1a). Model, Fireflies, Gmail and web calls are always faked (NFR-CRMAI-09).
- `008`'s files are read, never edited, except where coordination rule 3 or
  its approved extension 3a (plan §6) names a shared file and this file names the exact change. For `007` files
  both plans edit (plan §6 rule 3a), `009` only appends and never edits a line
  `008` touched; on rebase it keeps `008`'s hunks verbatim.

### Gates — every slice's "done when" includes all of these

Run from the worktree root.

- [ ] `cd backend && uv run pytest -q` passes (the whole suite)
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` pass
- [ ] Database, holding the shared lock (rule 5), from a fresh reset, all
  lines in **one** shell invocation (one Bash tool call; shell state does not
  persist between calls):

  ```bash
  LOCK="$(git rev-parse --git-common-dir)/db-test.lock"
  until mkdir "$LOCK" 2>/dev/null; do sleep 5; done
  trap 'rmdir "$LOCK"' EXIT
  npx supabase db reset \
    && (cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q)
  ```

  (one shell, so the `trap` releases the lock on success and on failure;
  the reset makes the shared stack hold exactly this branch's migrations)
- [ ] `cd frontend && pnpm build && pnpm lint` pass
- [ ] `git diff --cached --name-only` contains neither `supabase/config.toml` (rule 6) nor any file `008` owns outside the shared-file changes this slice names
- [ ] Every new phase switch, rule switch and budget reads off / zero in a freshly reset database
- [ ] The commit message says what changed, why, and which journey it serves (the Seller's Journey)

---

## Setup (once, before S1a)

- [ ] T000 Precondition, not `009`'s to do: the owner or the `008` session has committed `specs/008-tasks/` and `specs/009-crm-intelligence/` on `008-tasks` (never `supabase/config.toml`). Check `git log --oneline 008-tasks -- specs/009-crm-intelligence specs/008-tasks` is non-empty; if not, stop and report — no files
- [ ] T001 Create the worktree from `008-tasks` once both plans are committed: `git worktree add ../nldw-task-master-009 -b 009-crm-intelligence 008-tasks` — `../nldw-task-master-009`
- [ ] T002 Install dependencies in the worktree (`uv sync`, `pnpm install`); change no lock file — `backend/uv.lock`, `frontend/pnpm-lock.yaml` (unchanged)
- [ ] T003 Run every gate on the untouched branch and record the baseline (test count, build passes) in the S1a commit message — no files

---

## S1a — Foundations: switches, Claude client, budget, queue (phase 1)

**Goal.** Every workspace has an `ai` setting with every phase off and a zero
budget. One wrapper is the only way to call Claude; it enforces the phase, the
budget, the prompt rules, schema validation, money masking, usage logging and
safe errors. A `crm-ai` queue and a `worker-crm-ai` process type exist.
Admins see Settings → AI. Nothing calls a model yet.

**Depends on.** T001–T003.

**Spec.** FR-CRMAI-LLM-01 to -10; FR-CRMAI-FLAG-01 to -03; FR-CRMAI-JOB-01 (names), -02, -03, -04; FR-CRMAI-CHG-07 (names), -08 (budget event); NFR-CRMAI-04, -06, -08.

**Migration.** `060_crm_ai_foundations.sql`: data-model §3.1, §3.7 (`ai`), §3.8.

- [ ] T004 Rebase onto `008-tasks`; after a locked reset, read `pg_get_functiondef('public.crm_job_latest'::regproc)` and find its file with `grep -l "CREATE OR REPLACE FUNCTION public.crm_job_latest(" supabase/migrations/*.sql | sort | tail -1`; confirm with `pg_constraint` the names and current definitions of `crm_workspace_settings_key_check`, `crm_job_runs_job_check` and `crm_notifications_kind_check` (unnamed inline CHECKs from `026`/`037`, possibly rebuilt by `008`); note them in the migration header — `supabase/migrations/` (read only)
- [ ] T005 Write `060_crm_ai_foundations.sql`: key CHECK (dropped and re-added by name, as `041`:76 did) + `ai` seed (system actor) + `crm_workspaces_seed_ai` trigger; `crm_ai_usage` (RLS, revoke); `crm_ai_spend()`; job CHECK and `crm_job_latest()` with the five names; kind CHECK + `ai_budget_reached`; grants — `supabase/migrations/060_crm_ai_foundations.sql`
- [ ] T006 [P] Pure rules: `CRM_AI_MODEL`, `PHASES`, `ai_refusal()` (budget 0–10000 USD, two decimals; phases "1"–"4" booleans; unknown or missing keys refused), `phase_on()`, `ist_month_start()`, `estimate_cost()`, `mask_money()` (every pattern of FR-CRMAI-LLM-05), `strip_phones()`, `quote_untrusted()`, `allowed_url()`, `resolve_owner()` (index → member id or unknown), `FAILURE_REASONS` — `backend/app/domain/crm/ai_rules.py`
- [ ] T007 [P] `ANTHROPIC_API_KEY`, `FIREFLIES_API_KEY` (`SecretStr | None`), `CRM_AI_INPUT_USD_PER_MTOK`, `CRM_AI_OUTPUT_USD_PER_MTOK` (`Decimal | None`) with comments naming the one service that holds each; while either rate is unset every AI job is skipped "AI rates are not set" (D-1, approved; plan §11.1) — `backend/app/config.py`
- [ ] T008 [P] Append `AiSettingsValue`, `AiOverviewOut` — `backend/app/schemas/crm.py`
- [ ] T009 Register `VALIDATORS["ai"] = ai_rules.ai_refusal` — `backend/app/domain/crm/settings.py`
- [ ] T010 Append store functions: `insert_ai_usage()`, `ai_spend()` (RPC), `ai_setting()` — `backend/app/services/crm/store.py`
- [ ] T011 Raw-HTTP Messages client on `httpx` (headers, structured output through `output_config.format` with the job's schema (a forced tool call is refused by `claude-sonnet-5-5`; amended in S1a), timeouts 5 s / 60 s, usage parsing, structured log line without content, refuses without a key) — `backend/app/services/crm/claude.py`
- [ ] T012 The one call wrapper `ai.call(ws, job, phase, output_model, instruction, parts, now)` per plan §5.3, with `SafeError`, `AiFailed`, retry once on invalid output (Q-36), usage row per attempt, masking over every string — `backend/app/services/crm/ai.py`
- [ ] T013 `JOBS` gains `summaries`, `meetings`, `enrichment`, `ai_fields` (15 min) and `review_expiry` (1 day); `run()` and `failure()` take the `SafeError` path (fixed sentence for `error` and `error_public`, no `logger.exception`) — `backend/app/services/crm/jobs.py`
- [ ] T014 `budget_reached_notice(workspace, month)`: one bell notice per Admin (dedupe `ai_budget:<ws>:<YYYY-MM>`) and one message through `notify.deliver` — `backend/app/services/crm/notifications.py`
- [ ] T015 `009` block placed last in the file, after `007`'s and `008`'s (if both end at EOF after a rebase, move `009`'s below `008`'s): `CRM_AI_QUEUE = "crm-ai"`, `task_routes` merged with `"crm_ai.*"` (no beat entries yet) — `backend/app/worker.py`
- [ ] T016 [P] `worker_crm_ai` process type (`celery -A app.worker worker -Q crm-ai --concurrency=1`) and the matching line — `backend/entrypoint.sh`, `backend/Procfile`
- [ ] T017 `GET /crm/ai` (Admin): month, budget, spend total and by job, `budget_reached`, phases, estimate note, `rates_configured` (D-1); add to `ROUTERS` — `backend/app/api/crm/ai.py`, `backend/app/api/crm/__init__.py`
- [ ] T018 `bootstrap` gains `ai: {phases, review_visible: false, open_review_count: 0}` and, for Admins, `ai_budget_reached` — `backend/app/api/crm/workspaces.py`
- [ ] T019 Extend the fake store: `ai` setting rows, usage rows, `ai_spend` — `backend/tests/crm_fake.py`
- [ ] T020 Create `FakeClaude` (scripted outputs, invalid output, timeouts; records prompts), `CapturedCelery`, fixed clock helpers — `backend/tests/crm_ai_fakes.py`
- [ ] T021 [P] Tests: validator refusals; a workspace without an `ai` row reads off; switching a phase off records `skipped` "Switched off" — `backend/tests/test_crm_ai_settings.py`
- [ ] T022 [P] Tests: every money pattern of FR-CRMAI-LLM-05 and the spec §15 fixture list masked; phones removed; untrusted text quoted; URL policy — `backend/tests/test_crm_ai_text_rules.py`
- [ ] T023 [P] Tests: phase off or budget 0 → no call and the run says why; budget reached → skipped "AI budget reached", one notice and one message per month; 23:59 IST on the last day vs 00:00 IST on the first (spec §15); overshoot bounded to one call; **resume**: budget reached leaves a thread pending, raising the budget and running the tick gives one summary and one set of items, and a second tick changes nothing; rates unset → skipped "AI rates are not set" (D-1) — `backend/tests/test_crm_ai_budget.py`
- [ ] T024 [P] Tests: request shape, model id, timeouts, usage parsed, the key never in a log line or exception text (`httpx.MockTransport`); the model id is stored with every output (LLM-02); no route response and no page payload (bootstrap, settings, `GET /crm/ai`) carries `ANTHROPIC_API_KEY`, `FIREFLIES_API_KEY` or the Gmail credential (NFR-CRMAI-04, §15) — `backend/tests/test_crm_ai_client.py`
- [ ] T025 [P] Tests: invalid output retried once then failed with the fixed reason; any exception becomes a fixed sentence in `crm_job_runs`; usage rows hold no text; `crm_ai.*` routes to `crm-ai`; no module under `app/api` imports `services.crm.claude` (FR-CRMAI-LLM-01); a failing thread is counted in `failed_items` and the run is not failed (JOB-01); `crm_job_latest` returns the new job names once they have run (JOB-01) — `backend/tests/test_crm_ai_jobs.py`
- [ ] T026 [P] Tests: `GET /crm/ai` for an Admin; Member and Viewer 403; a TVA admin not in the workspace 404; another workspace 404; `GET /crm/jobs`, `GET /crm/tva-admin/jobs` and `GET /health/jobs` carry no tokens or cost keys; bootstrap carries phases and no budget for non-Admins — `backend/tests/test_crm_ai_routes.py`
- [ ] T027 [P] db tests: every existing workspace has `ai` off with budget 0; a workspace inserted later gets the row; the key CHECK accepts `ai`; settings `ai`, `automations`, `flag_thresholds` save straight after migration; `crm_ai_spend` sums by month; new job names accepted; the kind CHECK accepts `ai_budget_reached` (CHG-08); grants and RLS; add `crm_ai_usage` to `NOT_AUDITED` with its one-line reason ("the AI usage log, cost per call, no text") — `backend/tests/db/test_crm_ai_foundations_db.py`, `backend/tests/db/test_crm_security_db.py`
- [ ] T028 [P] Types and wrappers for `ai` settings, `GET /crm/ai`, bootstrap `ai` — `frontend/lib/api/crm-types.ts`, `frontend/lib/api/crm.ts`
- [ ] T029 Settings → **AI** tab (budget, spend by job, phases, "estimate") and the budget banner on the AI and Jobs tabs — `frontend/components/crm/ai-settings.tsx`, `frontend/components/crm/ai-budget-banner.tsx`, `frontend/app/(crm)/crm/[ws]/settings/page.tsx`
- [ ] T030 Gates; commit "Add the CRM AI foundations (009 slice S1a)"

**Done when.** All gates; an Admin saves a budget and switches a phase by
hand in Settings → AI; no AI job exists to run yet.

---

## S1b — Review Queue, To review, approval through the manual paths (phase 1)

**Goal.** Suggestions are stored as review items, routed to one person, shown
in a Review page, in Today and in the daily digest, and decided by Approve,
Edit and approve or Dismiss through the same services a manual edit uses,
audited with `suggestion_id`. Items expire after 14 days and are tombstoned
after 90. Reachable through NLDW. No job produces items yet: tests seed them.

**Depends on.** S1a.

**Spec.** FR-CRMAI-REV-01 to -10; FR-CRMAI-FLAG-04; FR-CRMAI-MCP-01 (two tools), -03; FR-CRMAI-CHG-03, -09, -11 (items); spec §15 "Review Queue".

**Migration.** `061_crm_review_queue.sql`: data-model §3.2, §3.12, §4 (items).

- [ ] T031 Rebase onto `008-tasks`. **Stop and report** if `supabase/migrations/048_tasks_crm_functions.sql` is missing (`ls` it): 061 replaces `crm_purge`, `crm_merge_companies` and `crm_audit_row`, which 048 also replaces, so S1b must not start before `008` S1a is committed. List `008` migrations numbered after `050` and note any that replace a function a `009` migration replaces (plan §6 rule 1). Check whether `backend/app/domain/tasks/service.py` (`create_task`, `TaskActor`) and `backend/app/schemas/tasks.py` (`TaskCreate`) exist. Check that `008`'s `backend/tests/test_task_crm_changes.py` does not pin the `crm_*` total at 18 and that `backend/tests/test_task_privacy_sweep.py` does not require a fixture for every CRM route and tool (plan §6 rule 3b); if either does, stop and report, because S1b's whole-suite gate would fail and `009` may not edit them. Note every answer in the slice commit — read only
- [ ] T032 Write `061_crm_review_queue.sql`: `crm_review_items`; `crm_review_item_deals` (composite FKs, RLS, revoke; data-model §3.2); `audit_log.suggestion_id`; `crm_audit_row()` replaced from its latest version (adds `suggestion_id`); `crm_review_upsert`, `crm_review_claim`, `crm_review_release`, `crm_review_expire`, `crm_review_tombstone`; `crm_purge()` and `crm_merge_companies()` replaced from their latest definitions (read with `pg_get_functiondef` from the reset database, data-model §1, so `008`'s 048 bodies are the base); grants — `supabase/migrations/061_crm_review_queue.sql`
- [ ] T033 [P] Pure rules: types and labels, `owner_for()` (FR-CRMAI-REV-02), `dedupe_key()`, `commitment_fingerprint()`, `stale_fields()` (per-field compare), `expires_on()`, `may_see()` / `may_decide()` (FR-CRMAI-REV-03) — `backend/app/domain/crm/review.py`
- [ ] T034 [P] Append `ReviewItemOut`, `ReviewApproveIn`, `ReviewListQuery` — `backend/app/schemas/crm.py`
- [ ] T035 `CrmContext` gains `suggestion_id: str | None = None` as its last field; `actor(ctx)` emits the `suggestion_id` key only when it is set (every other actor unchanged); `review.approve` makes the copy with `dataclasses.replace` — `backend/app/api/crm/deps.py`, `backend/app/services/crm/context.py`
- [ ] T036 Append store functions: list and get items, RPC wrappers for the five functions, `CRM09` → "This suggestion was already decided." — `backend/app/services/crm/store.py`
- [ ] T037 Review service: `upsert()` (masks again), `list()`, `visible()`, `today_section()`, `approve()` per plan §5.4 and contracts §2.2 (next step via `deals.update`; enrichment conflict via `records.update`; deal link via the note's links; task items via `008`'s `create_task(TaskActor(user=ctx.user, kind="user", channel=ctx.channel, suggestion_id=item.id), TaskCreate(…))` with only the rule-2 fields of `008` contracts §4 listed in plan §5.4 and links de-duplicated by `(record_type, record_id)`, behind a function-level import if T031 found it missing, the fake typed against the same `TaskActor` signature), `dismiss()`, trashed-target refusal; creating an item sends no bell or message (REV-10) — `backend/app/services/crm/review.py`
- [ ] T038 Routes `GET /crm/review` (`scope`, `status`, `limit` only), `POST …/approve`, `POST …/dismiss`; add to `ROUTERS` — `backend/app/api/crm/review.py`, `backend/app/api/crm/__init__.py`
- [ ] T039 Append `TO_REVIEW` and `to_review(items)` after the existing functions (leave `SECTIONS` and `assemble` untouched) — `backend/app/domain/crm/today.py`
- [ ] T040 In `build()`, the returned `sections` becomes `rules.assemble(...) + review.today_section(ws_id, person_id)`; nothing else in the file changes — `backend/app/services/crm/today.py`
- [ ] T041 [P] `HEADINGS["to_review"] = "To review"` — `backend/app/domain/crm/digests.py`
- [ ] T042 `bootstrap` `ai.review_visible` and `open_review_count` from `review.visible()` — `backend/app/api/crm/workspaces.py`
- [ ] T043 Timeline entries pass through `suggestion_id` and `approved_suggestion` from the audit row (one field in the entry builder) — `backend/app/services/crm/records.py`; the web timeline says "approved AI suggestion" on such an entry (FR-CRMAI-REV-05; amended in S1b) — `frontend/lib/api/crm-types.ts`, `frontend/components/crm/timeline.tsx`
- [ ] T044 [P] Export `review_items` (with deal choices) through a dedicated reader and presenter branch (ids only, trashed targets left out; `_all_rows`/`present_rows` know record kinds only); extend the exact export-object set — `backend/app/services/crm/export.py`, `backend/tests/test_crm_csv_io.py`
- [ ] T045 Create `crm_ai.review_expiry` (per workspace through `@crm_job("review_expiry")`, `ignore_result=True`), register it, and add its beat entry (01:30 IST) to the `009` block — `backend/app/tasks/crm_ai.py`, `backend/app/tasks/__init__.py`, `backend/app/worker.py`
- [ ] T046 Append `crm_review_list` and `crm_review_decide` after `crm_dashboard` (contracts §3, verbatim descriptions) — `backend/app/mcp/crm.py`
- [ ] T047 Fakes of the review table and its five RPCs, mirroring the SQL — `backend/tests/crm_fake.py`
- [ ] T048 [P] Tests: routing to deal owner, commitment owner, mailbox owner, company owner, Admins for a Viewer, inactive, non-member or unknown owner; dedupe and fingerprint; per-field staleness (a Last inbound change does not refuse) — `backend/tests/test_crm_review_rules.py`
- [ ] T049 Append the FR-CRMAI-MCP-05 paragraph (contracts §3.1) to `INSTRUCTIONS`, after `008`'s tasks paragraph (plan §6 rule 3a (a), approved by the owner on 18 October 2026), and add a test that `INSTRUCTIONS` contains it. If T031's rebase does not yet find `008`'s paragraph (`008` T044, its S1b), T049 moves to the first later slice whose rebase finds it, and S5 at the latest — `backend/app/mcp/server.py`, `backend/tests/test_crm_mcp.py`
- [ ] T050 Tests: Members see only theirs, Admins all, Viewers none, another workspace's id 404; approve next step through `deals.update` (empty date refused; someone else's edit refused as changed; two open deals need `deal_id` and only the chosen one changes); edit and approve; dismiss; approving twice creates one task; `008` refusal keeps the item open with the refusal; trashed target refused; audit row names the approver and carries the item id; task assignee: an owner who is an active Member gets the task, an unknown or Viewer owner leaves the approver as assignee (REV-06); a task item whose target is a company sends that link once; trashing the target hides its items from lists and Today, restoring shows them again; creating items leaves `crm_notifications` and the notifier capture empty (REV-10) — `backend/tests/test_crm_review_routes.py`
- [ ] T051 [P] Tests: 14 days → expired, in the job log and the audit; not re-created from the same source; 90 days → tombstone keeps the dedupe key and drops text — `backend/tests/test_crm_review_expiry.py`
- [ ] T052 Extend: To review shows the count and three oldest, absent for a Viewer, present while any phase is on or items are open — `backend/tests/test_crm_today.py`; the digest carries it — `backend/tests/test_crm_digests.py`; `008`'s morning brief shows the same section (through `build()`; if `008`'s brief is not yet on `008-tasks`, T165 adds this test) — `backend/tests/test_crm_review_routes.py`
- [ ] T053 Extend the tool tests to 20 (`CALLER_LINES`, `ONE_CALL_EACH`, `PURPOSES`, the count test); the acceptance `COVERAGE` names; the redaction sweep's `WRITES` (approve, dismiss) and Viewer reads (`crm_review_list`); the isolation sweep's `/crm/review/{id}…` routes and the two tools — `backend/tests/test_crm_mcp.py`, `backend/tests/test_crm_acceptance.py`, `backend/tests/test_crm_redaction_sweep.py`, `backend/tests/test_crm_isolation_sweep.py`
- [ ] T054 [P] db tests: the open-once index; `crm_review_claim` race (two claims, one wins); `suggestion_id` lands on the audit row; expire and tombstone; `crm_purge` removes a purged record's items and its id from `deal_choices`; `crm_merge_companies` moves open items and recomputes keys; a foreign target refused; a purged deal's `crm_review_item_deals` rows go by cascade; (a 061 copy made from a pre-048 body is caught by `008`'s `TestAfterMigration`, `008` T037, which asserts no function body names `crm_task` and runs at this gate, so no second copy of that test is added here); add `crm_review_items` and `crm_review_item_deals` to `NOT_AUDITED` with one-line reasons — `backend/tests/db/test_crm_review_db.py`, `backend/tests/db/test_crm_security_db.py`
- [ ] T055 [P] Types and wrappers for review routes and the `to_review` section; `splitToReview()` — `frontend/lib/api/crm-types.ts`, `frontend/lib/api/crm.ts`, `frontend/lib/crm/review.ts`
- [ ] T056 [P] AI marker with source tooltip on hover and focus — `frontend/components/crm/ai-marker.tsx`
- [ ] T057 Review page (Mine, All for Admins, Decided for Admins) and the item card (suggestion, current value, source, deal picker, Approve, Edit and approve, Dismiss, refusal) — `frontend/app/(crm)/crm/[ws]/review/page.tsx`, `frontend/components/crm/review-item.tsx`
- [ ] T058 To review card after `<TodayList>`, which receives the five sections only (`today-list.tsx` not edited) — `frontend/components/crm/to-review-section.tsx`, `frontend/app/(crm)/crm/[ws]/page.tsx`
- [ ] T059 "Review" sidebar entry with the open count while `ai.review_visible`, as its own component, inserted into the CRM shell by one line after `008`'s My Tasks entry (nothing else in `shell.tsx` changes) — `frontend/components/crm/review-nav-item.tsx`, `frontend/components/crm/shell.tsx`
- [ ] T060 Gates; commit "Add the CRM Review Queue (009 slice S1b)"

**Done when.** All gates; a seeded item is approved by hand in the web app
and through `crm_review_decide` from Claude, and the deal's timeline says
"approved AI suggestion".

---

## S1c — Email thread summaries (phase 1)

**Goal.** With phase 1 on and Gmail live, each labelled client thread gets a
summary, asks, commitments and sentiment within one tick, shown on company,
person and deal timelines and email panels, with next-step and commitment
items in the queue. Text is read in memory only.

**Depends on.** S1b.

**Spec.** FR-CRMAI-SUM-01 to -12; FR-CRMAI-CHG-01; FR-CRMAI-MCP-01 (`crm_thread_summary`), -02 (summaries); NFR-CRMAI-01 (email), -05; spec §15 "Email summaries".

**Migration.** `062_crm_email_summaries.sql`: data-model §3.3, §4 (summaries).

- [ ] T061 Rebase onto `008-tasks`; locate the latest definitions of `crm_remove_emails`, `crm_purge`, `crm_merge_companies` (data-model §1: `pg_get_functiondef` after a locked reset); check for late `008` replacements (plan §6 rule 1) — read only
- [ ] T062 Write `062_crm_email_summaries.sql`: the two tables (RLS, revoke, `crm_check_refs()` on links); `crm_pending_threads`; `crm_store_summary`; `crm_remove_emails`, `crm_purge`, `crm_merge_companies` replaced; grants — `supabase/migrations/062_crm_email_summaries.sql`
- [ ] T063 [P] Pure rules: qualifying messages, `strip()` (quoted history, signatures), prompt parts with the member list, `items()` (next step with deal choices; dated commitments with fingerprints) — `backend/app/domain/crm/summaries.py`
- [ ] T064 [P] Append `SummaryOutput` (strict: 2–3 sentences ≤ 600, asks and commitments ≤ 200 each, sentiment literal), `ThreadSummaryOut` — `backend/app/schemas/crm.py`
- [ ] T065 [P] `message_text(mailbox, message_id)` (format full, decoded text parts only, in memory) and the amended docstring (FR-CRMAI-CHG-01) — `backend/app/services/crm/gmail.py`
- [ ] T066 Append store functions: `pending_threads()`, `store_summary()`, `summaries_for()`, `summary_by_thread()` — `backend/app/services/crm/store.py`
- [ ] T067 Summaries service: per workspace, phase 1 and Gmail live, pending threads, stored message ids only, strip, `ai.call(job="summaries")`, items through `review` domain, `store_summary`; failures counted per thread — `backend/app/services/crm/summaries.py`
- [ ] T068 `crm_ai.summaries` (`@crm_job("summaries")`) and its beat entry (every 15 min) in the `009` block — `backend/app/tasks/crm_ai.py`, `backend/app/worker.py`
- [ ] T069 `GET /crm/email-threads/{thread_key}/summary`; add to `ROUTERS` — `backend/app/api/crm/summaries.py`, `backend/app/api/crm/__init__.py`
- [ ] T070 Threads gain `thread_key` and `summary` (phase 1 on) — `backend/app/services/crm/email_sync.py` (`threads()`), `backend/app/api/crm/email.py`
- [ ] T071 `intelligence.decorate(ctx, kind, answer)` adding `summaries` and `open_review_items`; the record `GET` route passes its answer through it (one call) — `backend/app/services/crm/intelligence.py`, `backend/app/api/crm/records.py`
- [ ] T072 Append `crm_thread_summary` after `crm_review_decide` — `backend/app/mcp/crm.py`
- [ ] T073 [P] Export `email_summaries` with links through a dedicated reader and presenter branch; extend the exact export-object set — `backend/app/services/crm/export.py`, `backend/tests/test_crm_csv_io.py`
- [ ] T074 Fakes: summary tables and RPCs in the store; `FakeGmailText` (records every message id read) — `backend/tests/crm_fake.py`, `backend/tests/crm_ai_fakes.py`
- [ ] T075 [P] Tests: qualifying rules; strip removes quoted history and signature markers; items only for dated commitments; a re-summary does not repeat a commitment; owner resolution exact, ambiguous and other-workspace names → unknown — `backend/tests/test_crm_summaries_rules.py`
- [ ] T076 Tests: one tick summarises; twice → no second call, nothing changes; new message replaces the summary; label removal deletes it; paused mailbox skipped, its summaries kept; 90-day backfill makes no call; only stored message ids are read; injected instruction and link have no effect; with two open deals one item with a picker; phase 1 switched off keeps stored summaries (hidden from reads) and they return when switched on, while open items stay visible and decidable — `backend/tests/test_crm_summaries_job.py`
- [ ] T077 [P] Tests: the existing `test_only_metadata_is_asked_for` in `tests/test_crm_gmail_token.py` still passes (unchanged); new: `message_text` is referenced only by `services/crm/summaries.py` (AST scan of `app/`); the full read logs one structured line with no content (NFR-06) — `backend/tests/test_crm_gmail_token.py` (run only), `backend/tests/test_crm_gmail_full_read.py`
- [ ] T078 Tests (privacy, email part of NFR-CRMAI-01): markers in quoted history, signature and an internal-only message never reach a prompt, column, log, Celery payload or result key, including after invalid output — `backend/tests/test_crm_ai_privacy.py`
- [ ] T079 Tests: thread summary route (Viewer reads it; money fixtures masked; foreign mailbox 404; phase off 404); record `GET` carries summaries; `GET /crm/{object}/{id}/emails` returns `thread_key` and `summary` with phase 1 on and leaves `summary` out when off; a `crm_get` result's `thread_key` resolves through `crm_thread_summary`; tool count 21; redaction sweep Viewer read `crm_thread_summary`; isolation sweep `/crm/email-threads/{thread_key}/summary` and the tool — `backend/tests/test_crm_summaries_routes.py`, `backend/tests/test_crm_mcp.py`, `backend/tests/test_crm_redaction_sweep.py`, `backend/tests/test_crm_isolation_sweep.py`, `backend/tests/test_crm_acceptance.py`
- [ ] T080 [P] db tests: unique per thread; `crm_store_summary` watermark re-check; links refuse another workspace's record; `crm_remove_emails` drops the summary of an emptied thread; purge and merge of links; add `crm_email_summaries` and `crm_email_summary_links` to `NOT_AUDITED` with one-line reasons — `backend/tests/db/test_crm_summaries_db.py`, `backend/tests/db/test_crm_security_db.py`
- [ ] T081 Thread summary (summary, asks, commitments, sentiment, AI marker, plain text) in the email panel and the timeline's email entries — `frontend/components/crm/thread-summary.tsx`, `frontend/components/crm/emails-tab.tsx`, `frontend/components/crm/timeline.tsx`, `frontend/lib/api/crm-types.ts`
- [ ] T082 Gates; commit "Summarise labelled email threads (009 slice S1c)"

**Done when.** All gates; with the fake Gmail (or, after step zero, a real
mailbox) a labelled thread shows its summary on the deal.

---

## S2a — Meeting capture from Fireflies (phase 2)

**Goal.** A client call recorded in Fireflies becomes an AI meeting note on
the right company (and deal) within 30 minutes, with action items, next step
and deal-link items in the queue; internal or unmatched meetings are never
downloaded.

**Depends on.** S1c.

**Spec.** FR-CRMAI-MEET-01 to -08; NFR-CRMAI-01 (transcript), -05; spec §15 "Meetings".

**Migration.** `063_crm_meeting_notes.sql`: data-model §3.4.

- [ ] T083 Rebase onto `008-tasks` — read only
- [ ] T084 Write `063_crm_meeting_notes.sql` (`crm_notes` columns, unique index, `crm_store_meeting_note`, grants) — `supabase/migrations/063_crm_meeting_notes.sql`
- [ ] T085 [P] Pure rules: internal vs external attendees by workspace domains, match per workspace, multi-workspace skip, deal resolution, the note body layout — `backend/app/domain/crm/meetings.py`
- [ ] T086 [P] Append `MeetingOutput` (summary ≤ 5 lines, decisions, action items with owner index and date) — `backend/app/schemas/crm.py`
- [ ] T087 [P] Raw-HTTP GraphQL client: `list_since(cursor)` (metadata only) and `transcript(id)` (content), timeouts, no content in logs — `backend/app/services/crm/fireflies.py`
- [ ] T088 Append store functions: `people_by_emails(workspace_ids, emails)`, `store_meeting_note()` — `backend/app/services/crm/store.py`
- [ ] T089 Meetings service per plan §5.6 (one poll, one run per workspace, cursor in Redis) — `backend/app/services/crm/meetings.py`
- [ ] T090 `crm_ai.meetings` and its beat entry (every 15 min) — `backend/app/tasks/crm_ai.py`, `backend/app/worker.py`
- [ ] T091 Fakes: `FakeFireflies` (records every transcript fetch), meeting note RPC in the store — `backend/tests/crm_ai_fakes.py`, `backend/tests/crm_fake.py`
- [ ] T092 [P] Tests: request shape, key never logged — `backend/tests/test_crm_fireflies_client.py`
- [ ] T093 [P] Tests: matching rules, internal only, unknown externals, two workspaces, deal resolution; the note body layout (fixed headings: title, attendees, summary, decisions, action items; plain text) — `backend/tests/test_crm_meetings_rules.py`
- [ ] T094 Tests: note on the right company and deal, items queued; internal-only and unmatched → no fetch; two workspaces → nothing in either, skipped counted in each; re-poll → no second note; updated transcript updates the note until a person edits it (Q-41); phase off → skipped, no fetch; the note's audit row has actor kind `sync` (MEET-05); with phase 1 off and phase 2 on, the meeting's items appear in the Review Queue and To review and can be decided — `backend/tests/test_crm_meetings_job.py`
- [ ] T095 Extend the privacy test with a transcript marker — `backend/tests/test_crm_ai_privacy.py`
- [ ] T096 [P] db tests: unique `source_ref`; `crm_store_meeting_note` refuses to overwrite after a person's audited edit; links in workspace; `note.created` audit row carries actor kind `sync` — `backend/tests/db/test_crm_meetings_db.py`
- [ ] T097 An AI meeting note renders as plain text with the AI marker and the transcript link: in `NoteList` (`note-composer.tsx`, used by `NotesTab` on company, person, deal and campaign pages), when `note.ai_provenance` is set render the body as text nodes, otherwise keep the `Markdown` component; `timeline.tsx` shows the marker on note entries only — `frontend/components/crm/note-composer.tsx`, `frontend/components/crm/timeline.tsx`, `frontend/lib/api/crm-types.ts`
- [ ] T098 Gates; commit "Capture client meetings from Fireflies (009 slice S2a)"

---

## S2b — Company enrichment (phase 2)

**Goal.** Companies with a website or domain are queued on create or website
change by any path, and enriched in the background from their own site at
10 a minute, as AI values beside the person's fields, with promote and
conflict items. The worker cannot be pointed at a private address.

**Depends on.** S2a.

**Spec.** FR-CRMAI-ENR-01 to -08; NFR-CRMAI-01 (web page); Q-30 to Q-33; spec §15 enrichment lines.

**Migration.** `064_crm_enrichment.sql`: data-model §3.5, §4 (merge).

- [ ] T099 Rebase onto `008-tasks`; locate the latest definition of `crm_merge_companies` (data-model §1); check for late `008` replacements (plan §6 rule 1) — read only
- [ ] T100 Write `064_crm_enrichment.sql` (table, `crm_queue_enrichment()` and its two triggers, merge replaced, grants) — `supabase/migrations/064_crm_enrichment.sql`
- [ ] T101 [P] Pure rules: site normalisation, industry and size mapping to options, LinkedIn and same-site URL rules, promotable vs conflict — `backend/app/domain/crm/enrichment.py`
- [ ] T102 [P] Safe reader per plan CT-3 (resolve, refuse non-public IPs, connect to the checked IP with SNI, same-site redirects re-checked, 5 pages, 1 MB, 10 s) — `backend/app/services/crm/web_fetch.py`
- [ ] T103 [P] Append `EnrichmentOutput`, `EnrichRunIn` — `backend/app/schemas/crm.py`
- [ ] T104 Append store functions: queued companies, write enrichment row, re-queue — `backend/app/services/crm/store.py`
- [ ] T105 Enrichment service: batches of ≤ 10, chaining with `countdown=60` and the run id (email-sync pattern), failure reasons (data-model §3.5: a host the URL policy refuses is recorded "Website did not respond"), conflict items through `review.upsert` — `backend/app/services/crm/enrichment.py`
- [ ] T106 `crm_ai.enrichment` (tick) and `crm_ai.enrichment_batch`, beat entry every 15 min — `backend/app/tasks/crm_ai.py`, `backend/app/worker.py`
- [ ] T107 `POST /crm/enrichment/run` (Admin); `intelligence.decorate` adds `enrichment` for companies — `backend/app/api/crm/enrichment.py`, `backend/app/api/crm/__init__.py`, `backend/app/services/crm/intelligence.py`
- [ ] T108 Approving `enrichment_conflict` replaces the field through `records.update` — `backend/app/services/crm/review.py`
- [ ] T109 [P] Export `enrichment` through a dedicated reader and presenter branch; extend the exact export-object set — `backend/app/services/crm/export.py`, `backend/tests/test_crm_csv_io.py`
- [ ] T110 Fakes: `FakeWeb` (scripted DNS answers and pages), enrichment rows — `backend/tests/crm_ai_fakes.py`, `backend/tests/crm_fake.py`
- [ ] T111 [P] Tests: private, loopback, link-local, IPv6 private refused; rebinding (second answer different) cannot redirect the connection; cross-site redirect refused; page, size and time caps — `backend/tests/test_crm_web_fetch.py`
- [ ] T112 [P] Tests: mapping and conflict rules — `backend/tests/test_crm_enrichment_rules.py`
- [ ] T113 Tests: import does no enrichment work in the request; companies enriched in the background; values marked AI; no person-entered field written; unreadable site → failed with reason; same company and site not re-read; two companies sharing a domain enriched separately; Admin re-run; rate ≤ 10 a minute; resumable: a run stopped mid-queue (budget or switch) leaves the rest `queued` and the next tick continues without re-reading done companies (ENR-07); the website fetch logs one structured line per page with no content (NFR-06); Enrich selected route guards; promote is an audited edit; the redaction sweep classifies `POST /crm/enrichment/run` in `WRITES`; the isolation sweep covers it — `backend/tests/test_crm_enrichment_job.py`, `backend/tests/test_crm_enrichment_routes.py`, `backend/tests/test_crm_redaction_sweep.py`, `backend/tests/test_crm_isolation_sweep.py`
- [ ] T114 Extend the privacy test with a web page marker — `backend/tests/test_crm_ai_privacy.py`
- [ ] T115 [P] db tests: the trigger queues on insert through `crm_apply` (web and import), on website change and on a primary domain; no audit row and no `updated_at` change on the company; merge keeps the survivor's row; add `crm_company_enrichment` to `NOT_AUDITED` with a one-line reason — `backend/tests/db/test_crm_enrichment_db.py`, `backend/tests/db/test_crm_security_db.py`
- [ ] T116 Enrichment panel (values, status, Promote, conflict link) on the company page; **Enrich selected** for Admins on company views — `frontend/components/crm/enrichment-panel.tsx`, `frontend/app/(crm)/crm/[ws]/companies/[id]/page.tsx`, `frontend/components/crm/bulk-bar.tsx`, `frontend/lib/api/crm.ts`, `frontend/lib/api/crm-types.ts`
- [ ] T117 Gates; commit "Enrich companies from their own websites (009 slice S2b)"

---

## S3a — AI fields (phase 3)

**Goal.** Four default AI fields (ICP fit in Labs only) and Admin-defined
ones are computed per record, at most once an hour with a trailing
recompute, shown read-only on record pages only.

**Depends on.** S2b.

**Spec.** FR-CRMAI-FIELD-01 to -06; Q-12, Q-13, Q-27, Q-28, Q-38.

**Migration.** `065_crm_ai_fields.sql`: data-model §3.6, §4.

- [ ] T118 Rebase onto `008-tasks`; locate the latest definitions of `crm_purge`, `crm_merge_companies` (data-model §1); check for late `008` replacements (plan §6 rule 1) — read only
- [ ] T119 Write `065_crm_ai_fields.sql` (two tables, seeds, purge and merge replaced, grants) — `supabase/migrations/065_crm_ai_fields.sql`
- [ ] T120 [P] Pure rules: inputs (no amounts), hash, cap and trailing recompute, `depends_on`, output validation per type — `backend/app/domain/crm/ai_fields.py`
- [ ] T121 [P] Append `AiFieldOutput`, `AiFieldIn`, `AiFieldPatch` — `backend/app/schemas/crm.py`
- [ ] T122 Append store functions for fields and values — `backend/app/services/crm/store.py`
- [ ] T123 AI fields service (job and on-demand queueing) — `backend/app/services/crm/ai_fields.py`
- [ ] T124 Mark linked values dirty when a summary, meeting note or enrichment is written — `backend/app/services/crm/summaries.py`, `backend/app/services/crm/meetings.py`, `backend/app/services/crm/enrichment.py`
- [ ] T125 `crm_ai.ai_fields` and its beat entry (every 15 min) — `backend/app/tasks/crm_ai.py`, `backend/app/worker.py`
- [ ] T126 Routes for definitions and recompute; `intelligence.decorate` adds `ai_fields` — `backend/app/api/crm/ai_fields.py`, `backend/app/api/crm/__init__.py`, `backend/app/services/crm/intelligence.py`
- [ ] T127 [P] Export `ai_fields` and `ai_field_values` through dedicated reader and presenter branches; extend the exact export-object set — `backend/app/services/crm/export.py`, `backend/tests/test_crm_csv_io.py`
- [ ] T128 Fakes for the two tables — `backend/tests/crm_fake.py`
- [ ] T129 [P] Tests: inputs carry no amount; hash; one recompute an hour plus one trailing; What is blocking only when At risk or Stalled — `backend/tests/test_crm_ai_fields_rules.py`
- [ ] T130 Tests: job idempotency; a value never changes a stage, probability, flag or colour; Viewers see values; definitions Admin only; number shown on the record only (not in `VIEW_FIELDS`); a stored summary sets `dirty_at` on the linked values and the next tick recomputes; recompute route: Viewer 403, Member 200; the redaction sweep classifies `POST /crm/ai-fields` and `POST /crm/{companies|opportunities}/{id}/ai-fields/recompute` in `WRITES`; the isolation sweep resolves `PATCH /crm/ai-fields/{id}` and the recompute route — `backend/tests/test_crm_ai_fields_job.py`, `backend/tests/test_crm_ai_fields_routes.py`, `backend/tests/test_crm_redaction_sweep.py`, `backend/tests/test_crm_isolation_sweep.py`
- [ ] T131 [P] db tests: seeds per workspace (ICP fit only in Labs); options CHECK; values purge and merge; add `crm_ai_field_values` to `NOT_AUDITED` with a one-line reason (`crm_ai_fields` is audited and is not added) — `backend/tests/db/test_crm_ai_fields_db.py`, `backend/tests/db/test_crm_security_db.py`
- [ ] T132 AI fields panel on company and deal pages; Settings → **AI fields** tab — `frontend/components/crm/ai-fields-panel.tsx`, `frontend/components/crm/ai-fields-editor.tsx`, `frontend/app/(crm)/crm/[ws]/companies/[id]/page.tsx`, `frontend/app/(crm)/crm/[ws]/opportunities/[id]/page.tsx`, `frontend/app/(crm)/crm/[ws]/settings/page.tsx`, `frontend/lib/api/crm.ts`, `frontend/lib/api/crm-types.ts`
- [ ] T133 Gates; commit "Add AI fields to companies and deals (009 slice S3a)"

---

## S3b — Follow-up rules R-1 to R-4 (phase 3)

**Goal.** The four rules run as extensions of `007`'s flags and automations,
each behind its own switch (seeded off) and phase 3. Stuck in stage counts as
a flag while R-2 is on. R-1, R-3 and R-4 create review items; nothing sends
email or changes a record without approval.

**Depends on.** S3a.

**Spec.** FR-CRMAI-RULE-01 to -08; FR-CRMAI-JOB-05; FR-CRMAI-CHG-05, -06, -08; Q-14 to Q-16, Q-29, Q-39, Q-40; spec §15 "AI fields and rules" (rules).

**Migration.** `066_crm_follow_up_rules.sql`: data-model §3.7 (merges), §3.8, §3.9, §3.11.

- [ ] T134 Rebase onto `008-tasks`; locate the latest kind and job CHECKs (by name, `pg_constraint`), `crm_job_latest` and `crm_workspaces_seed_ai`, the `008` task table names used by `crm_lead_activity`, and confirm the trigger names `crm_campaign_members_touch` and `crm_campaign_members_crm_audit` — read only
- [ ] T135 Write `066_crm_follow_up_rules.sql` (settings merges for every row; `crm_workspaces_seed_ai()` replaced to also seed `automations` and `flag_thresholds` with the new keys off for later workspaces; `status_changed_at` with the backfill run while `crm_campaign_members_touch` and `crm_campaign_members_crm_audit` are disabled, then the trigger; kind CHECK; job CHECK + `followup_drafts` and `crm_job_latest()` (D-4); `crm_lead_activity`, `crm_no_reply_candidates`, grants) — `supabase/migrations/066_crm_follow_up_rules.sql`
- [ ] T136 `AUTOMATIONS` gains the four switches with their labels — `backend/app/domain/crm/settings.py`
- [ ] T137 `DEFAULT_THRESHOLDS` gains the six parameters; `thresholds_refusal()` gains a branch bounding those six to whole days 1–365 (the existing keys keep `0 ≤ v ≤ 365×24` and the amber/red pair checks); `stuck_in_stage(deal, now, t)`; `for_deal` takes the stuck flag; kind label "Stuck in stage" — `backend/app/domain/crm/flags.py`
- [ ] T138 [P] Pure detectors R-1, R-3 (options by stage, Q-40 suppression), R-4 (7 days from `status_changed_at`, no converted deal, no Open or Waiting task), Do not contact skips — `backend/app/domain/crm/followups.py`
- [ ] T139 [P] Append `FollowupDraftOutput` — `backend/app/schemas/crm.py`
- [ ] T140 Append store functions: `lead_activity()`, `no_reply_candidates()` — `backend/app/services/crm/store.py`
- [ ] T141 Flags service computes Stuck in stage only while R-2 and phase 3 are on — `backend/app/services/crm/flags.py`
- [ ] T142 `stuck_in_stage_notices()` (one per deal per stage entry; one message a day when Stale deal fires at the same level) — `backend/app/services/crm/notifications.py`
- [ ] T143 Follow-ups service: R-1 detection (enqueues `crm_ai.followup_draft` with ids), the draft from the stored summary through `ai.call(job="followup_drafts")`, R-3 and R-4 items — `backend/app/services/crm/followups.py`
- [ ] T144 `automations` job checks each switch separately and is `skipped` only when all five are off; `crm_ai.followup_draft` task, one per workspace per tick, run through `@crm_job("followup_drafts")` so drafts done, failed and skipped (budget, switch) are counted in `crm_job_runs`; `JOBS` gains `followup_drafts` (D-4) — `backend/app/tasks/crm.py`, `backend/app/tasks/crm_ai.py`, `backend/app/services/crm/jobs.py`
- [ ] T145 `JOBS["automations"]` label "Automations" — `backend/app/services/crm/jobs.py`
- [ ] T146 [P] Dashboard and weekly digest count `stuck_in_stage` — `backend/app/domain/crm/dashboard.py`, `backend/app/domain/crm/digests.py`
- [ ] T147 Approving a `rule_suggestion`: R-3 lead stage or next touch via `records.update`; R-4 qualify via `campaigns.convert`, close out via the member update; R-1 marks used — `backend/app/services/crm/review.py`
- [ ] T148 Fakes for the two fact functions and `status_changed_at` — `backend/tests/crm_fake.py`
- [ ] T149 [P] Tests with fixed clocks: Proposal deal amber at 14 days, red at 28; R-1 one item per outbound message whichever record matched, none for Do not contact; R-3 activity definition, options, suppression; R-4 measured from `status_changed_at`, none when the member has a converted deal or its company or person has an Open or Waiting task; a company whose only people are Do not contact gets nothing from R-1 or R-4; with phase 3 off and every rule switch on, no rule item, no Stuck in stage flag and no notice; with phase 1 off, or Gmail not live, R-1 creates nothing while R-3 and R-4 still run; a draft that fails or is budget-skipped shows in the `followup_drafts` run; no rule sends an email or changes a record (only items are written) — `backend/tests/test_crm_followup_rules.py`
- [ ] T150 Extend: stuck flag in levels and Follow-ups owed only with R-2 on — `backend/tests/test_crm_flags.py`; per-switch `automations` runs — `backend/tests/test_crm_automations.py`; validators — `backend/tests/test_crm_settings_lists.py`; flag counts — `backend/tests/test_crm_dashboard.py`, `backend/tests/test_crm_digests.py`; rule approvals go through pipeline rules — `backend/tests/test_crm_review_routes.py`; notices reach CRM-only owners by bell only; R-2 notice limits: the job run twice in one stage entry gives one notice, re-entering the stage gives a new one, and Stale deal plus Stuck in stage at the same level on the same day give one message — `backend/tests/test_crm_notifications.py`
- [ ] T151 Extend the privacy test: the draft task's Celery payload is ids only and no body is read; a failed draft's job-log row holds only the fixed sentence — `backend/tests/test_crm_ai_privacy.py`
- [ ] T152 [P] db tests: every existing workspace's `automations` and `flag_thresholds` gain the keys off with defaults and keep stored values; `status_changed_at` backfilled and moved only by a status change; the migration leaves every member's `updated_at` and the `audit_log` row count unchanged; a workspace inserted after 066 gets `automations` and `flag_thresholds` with every new key off; the kind CHECK accepts `stuck_in_stage` and the job CHECK `followup_drafts` (CHG-08); the two fact functions — `backend/tests/db/test_crm_followup_db.py`
- [ ] T153 Settings → **Follow-up rules** tab; Thresholds and Automations tabs keep unknown keys on save; "Stuck in stage" label on the flag dot — `frontend/components/crm/followup-rules-form.tsx`, `frontend/app/(crm)/crm/[ws]/settings/page.tsx`, `frontend/components/crm/flag-dot.tsx`, `frontend/lib/api/crm-types.ts`
- [ ] T154 Gates; commit "Add the four follow-up rules (009 slice S3b)"

---

## S4 — Contact recency (phase 4)

**Goal.** Each company and person has Recent, Fading or Lapsed, computed with
the flags job from email metadata and meeting notes, shown as a chip and a
view filter, without moving updated time or writing audit rows.

**Depends on.** S3b.

**Spec.** FR-CRMAI-REL-01 to -03; Q-17, Q-21; spec §15 "Contact recency".

**Migration.** `067_crm_contact_recency.sql`: data-model §3.10.

- [ ] T155 Rebase onto `008-tasks`; locate the latest definitions of `crm_touch_updated_at`, `crm_audit_row`, `crm_query`, `crm_query_rank`, `crm_set_flags` (data-model §1); check for late `008` replacements (plan §6 rule 1) — read only
- [ ] T156 Write `067_crm_contact_recency.sql` (columns, `crm_set_contact_recency()` modelled on `crm_set_flags` and updating only `contact_recency`, both skip lists, registry rows, `crm_contact_facts`, grants); read the rebased `crm_query` filter path for `fixed_select` and, if it needs a rank branch for an unknown column, replace `crm_query_rank` with the `contact_recency` branch — `supabase/migrations/067_crm_contact_recency.sql`
- [ ] T157 [P] Pure levels (Recent, Fading, Lapsed; meetings only) — `backend/app/domain/crm/recency.py`
- [ ] T158 [P] `VIEW_FIELDS` gains `contact_recency` (filter only) for companies and people — `backend/app/domain/crm/objects.py`
- [ ] T159 Recency service and the flags job hook (phase 4 on), written through `store.set_contact_recency()` → `crm_set_contact_recency` (never `crm_apply`); `intelligence.decorate` adds `contact_recency` — `backend/app/services/crm/recency.py`, `backend/app/services/crm/flags.py`, `backend/app/services/crm/intelligence.py`, `backend/app/services/crm/store.py`
- [ ] T160 [P] Tests with fixed clocks at 30, 31, 90, 91 days, one-sided email, company including its people, Gmail off → meetings only — `backend/tests/test_crm_contact_recency.py`
- [ ] T161 Extend the registry and sweep tests for the new view field — `backend/tests/test_crm_object_fields_registry.py`, `backend/tests/test_crm_redaction_sweep.py`, `backend/tests/test_crm_isolation_sweep.py`
- [ ] T162 [P] db tests: `crm_set_contact_recency` moves neither `updated_at` nor `updated_by` nor the audit; `crm_query` filters by it — `backend/tests/db/test_crm_contact_recency_db.py`
- [ ] T163 Chip on company and person pages; view field label and options — `frontend/components/crm/contact-recency-chip.tsx`, `frontend/app/(crm)/crm/[ws]/companies/[id]/page.tsx`, `frontend/app/(crm)/crm/[ws]/people/[id]/page.tsx`, `frontend/lib/crm/view-fields.ts`, `frontend/lib/api/crm-types.ts`
- [ ] T164 Gates; commit "Show contact recency (009 slice S4)"

---

## S5 — Hardening: acceptance, isolation, redaction, privacy, docs, amendments

**Goal.** Every line of spec §15 has a named passing test; isolation,
redaction and privacy are proven across every new route, tool and job; the
living docs and the `007` spec amendments are written.

**Depends on.** S4, and `008` merged or at least its `create_task` present on
`008-tasks` (the real task-creation test must run).

**Spec.** Spec §15 (all); NFR-CRMAI-01 to -09; FR-CRMAI-CHG-01 to -11 (amendments); FR-CRMAI-MCP-05.

**Migration.** None, unless a sweep finds a gap: then `068_crm_intelligence_fixes.sql`, the finding named in its header — `supabase/migrations/068_crm_intelligence_fixes.sql`.

- [ ] T165 Rebase onto `008-tasks`; replace any remaining fake of `create_task` with the real call and its test, and add the `008` brief test of T052 if it was deferred — `backend/app/services/crm/review.py`, `backend/tests/test_crm_review_routes.py`
- [ ] T166 One test per spec §15 line: an assertion that the covering test named in the "Acceptance — §15 line to test" table below exists and is not skipped, or, for lines the table assigns to S5, a scenario through the routes and jobs with fakes — `backend/tests/test_crm_intelligence_acceptance.py`
- [ ] T167 [P] Prompt isolation: workspace B's people and companies match the same emails and domains; nothing of B reaches A's prompts, items, notes or summaries — `backend/tests/test_crm_ai_isolation.py`
- [ ] T168 [P] Full privacy test of NFR-CRMAI-01 over every job, including the failure path — `backend/tests/test_crm_ai_privacy.py`
- [ ] T169 [P] Sweeps: every new route and tool in the isolation and redaction sweeps, and the money fixtures through every Viewer route and tool — `backend/tests/test_crm_isolation_sweep.py`, `backend/tests/test_crm_redaction_sweep.py`
- [ ] T170 [P] Re-run the security sweep and confirm `NOT_AUDITED` holds exactly the `007` tables plus the seven added in S1a–S3a (data-model §5), RLS on, no browser grants, `EXECUTE` revoked, no new `uuid[]` column; add a db test comparing the table names in the `009` bodies of `crm_purge`, `crm_merge_companies` and `crm_audit_row` with `008`'s (plan §6 rule 1) — `backend/tests/db/test_crm_security_db.py`, `backend/tests/db/test_crm_review_db.py`
- [ ] T171 [P] Architecture: the intelligence layer (worker and queue, the call wrapper, no text at rest, approval path, jobs, rules as flag extensions) — `docs/architecture/crm.md`
- [ ] T172 [P] Guides: summaries and AI markers for sellers — `docs/guides/09-crm.md`; Settings → AI, AI fields, Follow-up rules, Enrich selected — `docs/guides/10-crm-admin.md`; the three tools — `docs/guides/08-connecting-claude.md`. No names, figures or costs (NFR-CRMAI-07)
- [ ] T173 New guide "Review AI suggestions" (frontmatter `order: 12`; `008`'s `docs/guides/10-tasks.md` is order 11 and `10-crm-admin.md` order 10, plan §6 rule 3) and exactly one index line for it — `docs/guides/12-crm-intelligence.md`, `docs/README.md`
- [ ] T174 [P] Runbook: migrations 060–067 after `008`'s, the `worker-crm-ai` service (`PROCESS_TYPE=worker_crm_ai`), its secrets (Claude, Fireflies, Gmail, rates), release order, Labs first — `docs/operations/deployment.md`
- [ ] T175 `007` spec amendments, one dated line ("Amended 18 October 2026 by `009`") beside each FR named in FR-CRMAI-CHG-01 to -09 and §15 "as metadata" — `specs/007-crm/spec.md`
- [ ] T176 Confirm `/help/crm-intelligence` renders (the build drops the number from the slug) and the public help test passes — `frontend/e2e/portal.spec.ts` (run, extend only if it does not cover the new guide)
- [ ] T177 Gates; commit "Harden the CRM intelligence layer (009 slice S5)"

**Done when.**
- [ ] All gates
- [ ] `test_crm_intelligence_acceptance.py` has one passing, unskipped test per §15 line
- [ ] T049 is done: `INSTRUCTIONS` carries the FR-CRMAI-MCP-05 paragraph after `008`'s
- [ ] D-1 and D-4 are built; D-2 and D-3 are built to the minimum only (plan §11.1)

---

## Coverage — spec areas to slices

| Spec area | Slices |
|---|---|
| §5.1 Claude client and budget (LLM-01–10) | S1a; LLM-05 masking used by every job slice |
| §5.2 Per-phase switches (FLAG-01–05) | S1a; FLAG-04 S1b; FLAG-05 is an operating rule (runbook, S5) |
| §5.3 Jobs (JOB-01–05) | S1a (names, queue, safe errors); jobs in S1b (`review_expiry`), S1c (`summaries`), S2a (`meetings`), S2b (`enrichment`), S3a (`ai_fields`); JOB-05 S3b |
| §6.1 Summaries (SUM-01–12) | S1c (SUM-12 rule built in S1a's `resolve_owner`) |
| §6.2 Review Queue (REV-01–10) | S1b; item producers S1c, S2a, S2b, S3b |
| §7.1 Meetings (MEET-01–08) | S2a |
| §7.2 Enrichment (ENR-01–08) | S2b |
| §8.1 AI fields (FIELD-01–06) | S3a |
| §8.2 Rules (RULE-01–08) | S3b |
| §9.1 Contact recency (REL-01–03) | S4 |
| §10 MCP (MCP-01–05) | S1b (two tools), S1c (one tool, `crm_get` summaries), S2b–S4 (`crm_get` content); MCP-05 T049 (plan §6 rule 3a (a)) |
| §11 Changes (CHG-01–11) | CHG-01 S1c; -02 S5; -03 S1b; -04 S1c (21 tools); -05, -06, -08 S3b; -07 S1a/S3b; -09 S1b; -10 S1b; -11 S1b, S1c, S2b, S3a, S5; amendments S5 |
| §12 NFRs | 01 S1c–S3b, S5; 02 every slice, S5; 03 every read slice, S5; 04 S1a, S5; 05 S1c, S2a; 06 S1a; 07 S5; 08 plan §2.1; 09 every slice |

## MCP tools by slice (21)

| Slice | Tools | Running `crm_*` total |
|---|---|---|
| `007` | 18 | 18 |
| S1b | `crm_review_list`, `crm_review_decide` | 20 |
| S1c | `crm_thread_summary` | 21 |

## Acceptance — §15 line to test

Every spec §15 line has a covering test in a slice. T166 asserts each named
test exists and is not skipped; the lines marked **S5** are written as
scenarios in T166 itself.

| §15 line (short) | Covering test (task) |
|---|---|
| Phase 1 off or zero budget: no call, run says why | `test_crm_ai_budget.py` (T023) |
| Budget reached: stop, skipped, banner, one notice; raising it resumes without duplicates | `test_crm_ai_budget.py` (T023, resume case) |
| Budget month turns at 00:00 IST | `test_crm_ai_budget.py` (T023) |
| Spend to Admins only; no cost from any page, route or job record | `test_crm_ai_routes.py` (T026) |
| No key reaches the browser or a log line | `test_crm_ai_client.py` (T024) |
| Pre-migration workspace reads switches off and saves settings | `test_crm_ai_foundations_db.py` (T027), `test_crm_followup_db.py` (T152) |
| Phase 1 off keeps summaries; open items stay decidable | `test_crm_summaries_job.py` (T076) |
| Phase 1 off, phase 2 on: meeting and enrichment items decidable | `test_crm_meetings_job.py` (T094) |
| Stale-deal notice off does not stop R-1, R-3, R-4; `automations` skipped only when all off | `test_crm_automations.py` (T150) |
| Labelled thread summarised within one cycle, with marker | `test_crm_summaries_job.py` (T076), `test_crm_summaries_routes.py` (T079) |
| Privacy test finds no marker, including after invalid output | `test_crm_ai_privacy.py` (T078, T168) |
| Internal-only, quoted and signature markers absent | `test_crm_ai_privacy.py` (T078) |
| Twice: no call; new message replaces | `test_crm_summaries_job.py` (T076) |
| Label removal deletes the summary | `test_crm_summaries_job.py` (T076), `test_crm_summaries_db.py` (T080) |
| Paused mailbox skipped, summaries kept | `test_crm_summaries_job.py` (T076) |
| 90-day backfill makes no call | `test_crm_summaries_job.py` (T076) |
| Promised date → `task_from_commitment` item | `test_crm_summaries_rules.py` (T075) |
| Next step → item; two deals, picker, chosen deal only | `test_crm_summaries_job.py` (T076), `test_crm_review_routes.py` (T050) |
| Injected instruction and link have no effect | `test_crm_summaries_job.py` (T076) |
| Money fixtures absent from Viewer routes and tools | `test_crm_redaction_sweep.py` (T169) |
| Owner resolution exact / ambiguous / other workspace | `test_crm_summaries_rules.py` (T075) |
| Next-step approval: empty date refused; changed refused | `test_crm_review_routes.py` (T050) |
| Last inbound change does not invalidate company items | `test_crm_review_rules.py` (T048) |
| Task item creates one `008` task; twice creates one | `test_crm_review_routes.py` (T050, T165) |
| Members / Admins / Viewers / foreign id 404 | `test_crm_review_routes.py` (T050) |
| 14-day expiry in job log and audit; not re-created | `test_crm_review_expiry.py` (T051) |
| Audit names approver and carries item id | `test_crm_review_routes.py` (T050), `test_crm_review_db.py` (T054) |
| Today To review; `008`'s brief shows it | `test_crm_today.py` (T052), `test_crm_review_routes.py` (T052/T165) |
| `crm_review_list` / `crm_review_decide` through NLDW, CONFIRM FIRST | `test_crm_mcp.py` (T053) |
| Trash hides, restore shows, purge deletes, merge moves, export holds new tables | `test_crm_review_routes.py` (T050), db tests T054, T080, T115, T131, `test_crm_csv_io.py` (T044, T073, T109, T127); the end-to-end company scenario is **S5** (T166) |
| Fireflies call → note on company and deal within 30 min | `test_crm_meetings_job.py` (T094) |
| Internal-only or unmatched → nothing, no fetch | `test_crm_meetings_job.py` (T094) |
| Two workspaces → nothing, skipped in each | `test_crm_meetings_job.py` (T094) |
| Re-poll → no second note | `test_crm_meetings_job.py` (T094) |
| Updated transcript until a person edits | `test_crm_meetings_job.py` (T094), `test_crm_meetings_db.py` (T096) |
| Import does no enrichment work in the request; values AI; nothing overwritten | `test_crm_enrichment_job.py` (T113) |
| Company created by email sync is queued | `test_crm_enrichment_db.py` (T115) |
| Unreadable site → failed with reason | `test_crm_enrichment_job.py` (T113) |
| Private address refused | `test_crm_web_fetch.py` (T111) |
| Promote audited; conflict item instead of promote | `test_crm_enrichment_job.py` (T113) |
| AI fields read-only, record pages only, change nothing | `test_crm_ai_fields_job.py` (T130) |
| Stuck in stage amber 14 / red 28; one notice | `test_crm_followup_rules.py` (T149), `test_crm_notifications.py` (T150) |
| R-2 on can turn colours and Follow-ups owed; off, no flag | `test_crm_flags.py` (T150) |
| Stale deal + Stuck in stage same level same day → one message | `test_crm_notifications.py` (T150) |
| R-1/R-3/R-4 one per trigger; none after dismissal or expiry; none while off | `test_crm_followup_rules.py` (T149) |
| R-1 one item; draft ids only; Do not contact nothing | `test_crm_followup_rules.py` (T149), `test_crm_ai_privacy.py` (T151) |
| R-4 from `status_changed_at` | `test_crm_followup_rules.py` (T149), `test_crm_followup_db.py` (T152) |
| Notices to CRM-only owners by bell only | `test_crm_notifications.py` (T150) |
| No rule sends an email or changes a record | `test_crm_followup_rules.py` (T149) |
| Recency levels at 30/31/90/91, one-sided, company includes people | `test_crm_contact_recency.py` (T160) |
| Gmail off → "Meetings only" | `test_crm_contact_recency.py` (T160) |
| Recency filters views; recompute keeps updated time | `test_crm_contact_recency_db.py` (T162) |
| No workspace B data in A's prompts or output | `test_crm_ai_isolation.py` (T167) |
