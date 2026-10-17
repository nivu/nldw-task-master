# CRM Intelligence Layer — Plan

| | |
|---|---|
| **Feature** | `009-crm-intelligence` |
| **Branch** | `009-crm-intelligence`, in the git worktree `../nldw-task-master-009`, branched from `008-tasks` after this plan and `008`'s are committed |
| **Date** | 18 October 2026 |
| **Spec** | [`spec.md`](spec.md) (operative); [`source.md`](source.md) (owner's original, behaviour only) |
| **Companions** | [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md), [`tasks.md`](tasks.md) |
| **Builds on** | `007-crm` as built (migrations 025–045, `backend/app/{api,domain,services}/crm`, `backend/app/mcp/crm.py`, `frontend/app/(crm)`), and `008-tasks` (its task-creation interface, its migrations 046–059) |
| **Constitution** | v1.2.0 |

This is the HOW for `spec.md`. Where this file and the spec disagree, the spec
wins and this file is wrong. Every FR, NFR and Q id below is the spec's. It
follows `specs/007-crm/plan.md` in layout and conventions.

---

## 1. Summary

The intelligence layer is a set of background jobs and one review screen
inside the existing CRM. It adds no service other than one Celery worker
process, `worker-crm-ai`. Every model call happens there. Every AI output is
either stored in an AI-labelled place or waits as a Review Queue item. Every
approved change goes through the code path a person's own edit uses.

Seven design choices hold the rest together:

1. **One worker owns every model, Fireflies and website call.** A new Celery
   queue `crm-ai`, served only by `worker-crm-ai` (concurrency 1), runs every
   AI task (FR-CRMAI-JOB-04, Q-05). Beat publishes the periodic jobs straight
   onto that queue, so the shared worker never runs one. The Claude and
   Fireflies keys exist only on that service. The API never imports the
   Claude client: a test walks `app/api` and fails on the import
   (FR-CRMAI-LLM-01).
2. **One call wrapper, `services/crm/ai.py`.** Every model call goes through
   one function. It checks the phase switch and the month-to-date budget,
   builds the prompt from quoted data with phone numbers removed, calls Claude
   with forced structured output, validates the answer against the job's
   schema (retrying once), masks money in every string, logs usage without
   text, and turns any exception into a fixed sentence (FR-CRMAI-LLM-01 to
   -08, JOB-03). Jobs never call the HTTP client themselves.
3. **No raw text at rest, by construction.** Celery messages carry ids only.
   AI tasks set `ignore_result=True`. Text is fetched inside the task, passed
   to the wrapper, and goes out of scope. Errors are replaced by fixed
   sentences before they reach a log line or `crm_job_runs` (P-3,
   NFR-CRMAI-01). Generated-text columns have small length limits, so a bug
   that tried to store a body fails.
4. **Jobs work from what is missing.** Each periodic job asks the database what
   is pending: threads newer than their summary's watermark, companies queued
   for enrichment, AI field values marked dirty, open items past 14 days. A
   skipped run (switch off, budget reached) therefore loses nothing, and
   re-running changes nothing (P-5, FR-CRMAI-LLM-08).
5. **AI output never writes a record; approval does, as the person.** A
   suggestion is a `crm_review_items` row. Approving loads the target, checks
   **per field** that each value the item would replace is still what it
   recorded, then calls the existing service function the matching manual
   edit calls (`deals.update`, `records.update`, `campaigns.convert`, `008`'s
   `create_task`). That call carries the `expect_updated_at` of the row read
   during the approval, so a change made between the check and the write is
   refused. The person's own `CrmContext` is used (copied with
   `dataclasses.replace(ctx, suggestion_id=item.id)`, §5.4), so permissions,
   pipeline rules and the audit apply unchanged. For a task the approval
   builds `008`'s `TaskActor(user=ctx.user, kind="user", channel=ctx.channel,
   suggestion_id=item.id)` instead. Either actor carries `suggestion_id`, which
   the audit trigger copies to `audit_log.suggestion_id` (FR-CRMAI-REV-04, -05;
   §5.4).
6. **Rules extend `007`'s flags and automations; there is no rule engine**
   (C2). Stuck in stage is one more flag function in `domain/crm/flags.py`.
   R-1, R-3 and R-4 are detectors in a new `domain/crm/followups.py`, run by
   the existing `automations` job, each behind its own switch.
7. **Settings stay where they are.** The phase switches and budget are a new
   `ai` key in `crm_workspace_settings`, with its own validator. Rule switches
   and parameters are new flat keys in `automations` and `flag_thresholds`.
   Every existing workspace is seeded or merged **off** by migration, and a
   workspace created later is seeded by the `crm_workspaces_seed_ai` trigger
   (`ai` from 060; `automations` and `flag_thresholds` added to it in 066),
   because `automation_on` reads a missing key as on (FR-CRMAI-FLAG-02, Q-04,
   RULE-01).

### 1.1 Slices

The spec's phases become slices, in order. Phase 0 (step zero) is the owner's
and has no code. Every slice ends with the whole backend suite green, the
`CRM_DB_TESTS=1` db tests green after a fresh `db reset`, and a frontend build.
Each phase's switch is seeded **off**, so every slice can ship with nothing
running.

| Slice | Phase | Delivers | Migration |
|---|---|---|---|
| **S1a** | 1 | Foundations: `ai` setting and phase switches; Claude client and call wrapper; budget, usage log and spend RPC; money masking and prompt rules; `crm-ai` queue and `worker-crm-ai` process; new job names; Settings → AI tab; budget banner and notice | `060_crm_ai_foundations.sql` |
| **S1b** | 1 | Review Queue: items, routing, approve / edit / dismiss through the manual paths, `audit_log.suggestion_id`, task creation through `008`, expiry and tombstones, Review page, Today "To review" and the digest section, `crm_review_list`, `crm_review_decide`, record lifecycle for items | `061_crm_review_queue.sql` |
| **S1c** | 1 | Email thread summaries: summariser-only full-message Gmail read, quote and signature stripping, the `summaries` job, next-step and commitment items, summary UI on timelines and email panels, `crm_thread_summary`, `crm_get` summaries, label removal, the privacy test | `062_crm_email_summaries.sql` |
| **S2a** | 2 | Meeting capture: Fireflies client, metadata-only matching, the `meetings` job, AI meeting notes, `deal_link` / action-item / next-step items | `063_crm_meeting_notes.sql` |
| **S2b** | 2 | Company enrichment: queue trigger, network-safe site reader, the `enrichment` job, enriched values panel with promote, `enrichment_conflict` items, **Enrich selected** | `064_crm_enrichment.sql` |
| **S3a** | 3 | AI fields: definitions and four seeds, the `ai_fields` job with the hourly cap, record-page panel, Settings → AI fields tab | `065_crm_ai_fields.sql` |
| **S3b** | 3 | Follow-up rules: R-2 Stuck in stage flag and notice, R-1 draft, R-3 lead gone cold, R-4 campaign follow-through; `automations` job checking each switch; Settings → Follow-up rules tab | `066_crm_follow_up_rules.sql` |
| **S4** | 4 | Contact recency: columns, computation in the flags job, chip and view filter | `067_crm_contact_recency.sql` |
| **S5** | — | Hardening: acceptance sweep (one test per spec §15 line), isolation, redaction and privacy sweeps across every new route, tool and job, docs, `007` spec amendments | `068_crm_intelligence_fixes.sql` only if a sweep needs one |

---

## 2. Technical context

| | |
|---|---|
| **Language/Version** | Python 3.11+ (CI 3.13), TypeScript 5 strict |
| **Backend** | FastAPI (sync handlers), Pydantic v2, supabase-py (service role), Celery + Redis, MCP SDK ≥ 2.2, `httpx` (already installed through `supabase` and `mcp`; the Gmail client uses it) |
| **Frontend** | Next.js 16 App Router (read `frontend/node_modules/next/dist/docs/` before writing pages, per `frontend/AGENTS.md`), shadcn base-nova on `@base-ui/react` |
| **Storage** | Supabase Postgres: six new tables, columns on `crm_notes`, `crm_companies`, `crm_people`, `crm_campaign_members`, `audit_log`; no Storage |
| **External** | Anthropic Messages API (Claude Sonnet `claude-sonnet-5-5`, C4); Fireflies GraphQL API (C5); Gmail API full-message read (existing service-account credential, `gmail.readonly`); company websites (read-only GET) |
| **Testing** | pytest with in-memory fakes (`tests/crm_fake.py` extended), fake Claude, Fireflies, Gmail and web transports (`httpx.MockTransport`), fixed clocks; `@pytest.mark.db` tests against local Supabase |
| **Target platform** | Railway: api, worker, beat, `worker-crm-sync` (step zero), new `worker-crm-ai`; Netlify; Supabase `ap-south-1` |
| **Performance goals** | NFR-CRMAI-05: a summary within one 15-minute cycle of the message being stored; a meeting note within 30 minutes of the transcript being ready |
| **Constraints** | `worker-crm-ai` runs one task at a time; Celery `task_time_limit = 600`; enrichment at most 10 companies a minute per workspace, 5 pages × 1 MB × 10 s per company (Q-31); one model call in flight caps budget overshoot |
| **Scale/scope** | 2 workspaces, ~20 users, ~10k companies each; 5 new periodic jobs; 3 new MCP tools (21 `crm_*` in all) |

### 2.1 Dependencies (NFR-CRMAI-08, Q-42)

**No new direct dependency.** The Claude and Fireflies clients are raw HTTP on
`httpx`, as `services/crm/gmail.py` is. HTML-to-text for enrichment uses the
standard library `html.parser`. DNS checks use `socket.getaddrinfo` and
`ipaddress`. `httpx` stays an indirect dependency as it is today for Gmail;
making it direct is not needed by this feature and is left to the owner
(it would be the only `pyproject.toml` change `009` makes, and `008` adds its
own dependencies separately, coordination rule 3).

### 2.2 New configuration

| Setting | Where | Default | Meaning |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `app/config.py`, `SecretStr \| None` | unset | Claude key; Railway secret on `worker-crm-ai` **only** (C4, NFR-CRMAI-04). Never logged; never in a Celery argument |
| `FIREFLIES_API_KEY` | `app/config.py`, `SecretStr \| None` | unset | Fireflies key; `worker-crm-ai` **only** (C5) |
| `CRM_AI_INPUT_USD_PER_MTOK`, `CRM_AI_OUTPUT_USD_PER_MTOK` | `app/config.py`, `Decimal \| None` | unset | The rate table of spec §17.1, set on `worker-crm-ai` once the owner confirms it. While either is unset every AI job records `skipped` "AI rates are not set" and `GET /crm/ai` reports `rates_configured: false` (owner decision D-1 of 18 October 2026, §11.1; spec FR-CRMAI-LLM-07) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | existing | — | Also set on `worker-crm-ai` (step zero), for the summariser's full-message read |
| `CRM_AI_MODEL` | constant in `domain/crm/ai_rules.py` | `claude-sonnet-5-5` | C4, Q-01; stored with every output |
| `CRM_AI_QUEUE` | constant in `app/worker.py` | `crm-ai` | The queue `worker-crm-ai` serves (FR-CRMAI-JOB-04) |
| `PROCESS_TYPE=worker_crm_ai` | `backend/entrypoint.sh`, `backend/Procfile` | — | `celery -A app.worker worker -Q crm-ai --concurrency=1` |

Per-workspace configuration lives in `crm_workspace_settings` (`ai`,
`automations`, `flag_thresholds`), never in `app_settings` (`007` plan §2.2).

---

## 3. Constitution check

| # | Question | Answer |
|---|---|---|
| 1 | Does this serve one of the journeys? | Yes: **The Seller's Journey**. Tamil's Today list ("replies owed, deals whose next step is due, companies that have gone quiet") gains what was said and what was promised, a meeting outcome without typing, imported companies that fill themselves in, and four nudges for deals and leads that need attention. Every item traces through an FR to a sentence of the journey or to Sanjeev's workspace isolation. No feature targets TVA journeys. |
| 2 | Security-First and Clear Boundaries? | Yes, with the exceptions in §4. Keys are Railway secrets on one worker. The browser reaches the backend only through the proxy and gets no key. New tables: RLS on, no policy, no browser grants, function `EXECUTE` revoked from browser roles. Workspace isolation: every job runs per workspace, every prompt is built from rows read with `workspace_id = $ws`, every new route goes through `api/crm/deps.py`, and foreign ids answer 404. Text from outsiders is quoted as data in prompts and rendered as plain text (`004` FR-MCP-05, Q-37). Enrichment refuses private addresses and pins the checked IP (Q-30). Cost is shown to workspace Admins only. |
| 3 | Data Integrity? | Yes. Eight numbered migrations, `060`–`067`, inside the range coordination rule 1 gives `009`. Multi-table writes are single plpgsql transactions (`crm_store_summary`, `crm_store_meeting_note`, `crm_review_upsert`), except the approval path (CT-1). No generated value is written into a person-entered field without a person's approval. Seeds are written under the system actor. Cross-workspace exposure is tested at route level (fake store) and database level in every slice and swept in S5. |

**Re-check after design:** done. Nothing beyond §4.

---

## 4. Complexity tracking

| # | Exception (principle) | Why needed | Simpler alternative rejected because |
|---|---|---|---|
| CT-1 | **Approving a suggestion is not one transaction** (§III "multi-table writes MUST be transactional") | FR-CRMAI-REV-04 requires approval to go through the *same* service a manual edit uses. Those services (`deals.update`, `records.update`, `campaigns.convert`) and `008`'s `create_task` each commit their own transaction. The item is therefore claimed first (`crm_review_claim`: open → approved, under a row condition, so two approvals cannot both proceed and "approving twice creates one task"), then the change is made through the manual path, then the item records its result. If the manual path refuses, `crm_review_release` reopens the item with the refusal (FR-CRMAI-REV-06). A crash between the claim and the change leaves an approved item with no result; the item simply shows as approved (an "approved, nothing changed" state was cut, D-3, §11.1) and the person can approve the same source again only after the source changes. | A database function that re-implements each manual edit: two copies of the pipeline rules, the opposite of REV-04. Wrapping the service calls in one database transaction: supabase-py over PostgREST has no client transaction. |
| CT-2 | **The full Gmail message is read** (amends `007` Q-29 "never fetched"; constitution §I data minimisation) | A summary needs the words (FR-CRMAI-CHG-01). The read exists only in `services/crm/gmail.py` `message_text()`, is called only from `services/crm/summaries.py` on `worker-crm-ai` (pinned by an import-graph test), fetches only messages `007` stored, strips quotes and signatures before the prompt, and never stores, logs or queues the text. | Summarising subjects and participants only: does not meet G-1 (asks and commitments). |
| CT-3 | **The backend fetches addresses people typed** (§I OWASP: SSRF) | Enrichment reads a company's own website (FR-CRMAI-ENR-02). `services/crm/web_fetch.py` resolves the host, refuses private, loopback, link-local, multicast, reserved and unspecified addresses (IPv4 and IPv6), connects to the checked IP with the original host as `Host` and TLS SNI (httpx `sni_hostname` extension), follows redirects itself only within the same registrable site, re-checking each hop, and caps 5 pages × 1 MB × 10 s (Q-30, Q-31). It runs only on `worker-crm-ai`. | A third-party enrichment API: a new vendor, a cost, and the owner asked for the company's own site. |
| CT-4 | **Workspace data is sent to an external model provider** (§I; P-6) | The feature is AI summaries (C4). Each prompt carries only the fields its task needs, from one workspace, with phone numbers removed and no amounts (FR-CRMAI-LLM-04, -05, FIELD-03). The provider and model are fixed by the owner's decision C4. | No alternative meets the spec. |

**Considered, and not exceptions:** stored contact recency and the Stuck in
stage flag are derived state like `007`'s flags (`007` plan §4); the usage log
is a log, not a balance.

---

## 5. Architecture

### 5.1 Workers, queues and beat — FR-CRMAI-JOB-04

```text
beat (IST) ──publishes──▶ default queue ─▶ worker (shared)    crm.recompute_flags → flags, automations
                    │                                         (R-2 flag + notices, R-1/R-3/R-4 detection;
                    │                                          R-1 enqueues crm_ai.followup_draft(ids) ▼)
                    └──publishes──▶ crm-ai queue ─▶ worker-crm-ai (concurrency 1)
                                       crm_ai.summaries      (every 15 min)
                                       crm_ai.meetings       (every 15 min)
                                       crm_ai.enrichment     (every 15 min; self-chains batches of ≤10, 60 s apart)
                                       crm_ai.enrichment_batch, crm_ai.ai_fields (every 15 min),
                                       crm_ai.followup_draft (enqueued), crm_ai.review_expiry (daily 01:30)
```

- `app/worker.py`, in a **separate block placed last in the file, after
  `007`'s and after `008`'s `# Spec 008 — tasks` block** (coordination rule
  3): `CRM_AI_QUEUE = "crm-ai"`; `celery_app.conf.task_routes = {**celery_app.conf.task_routes, "crm_ai.*": {"queue": CRM_AI_QUEUE}}`;
  `celery_app.conf.beat_schedule.update({...})` with the five entries. `007`'s
  dict and `008`'s block are untouched. If a rebase finds both blocks at the
  end of the file, `009` moves its own block below `008`'s, never
  interleaving them.
- `app/tasks/crm_ai.py` (new): every task `ignore_result=True`; arguments are
  ids and workspace ids only. Registered by one import line in
  `app/tasks/__init__.py`.
- Each periodic task loops over workspaces through `jobs.crm_job(name)`
  (`services/crm/jobs.py`), one `crm_job_runs` row per workspace per tick. A
  failing thread or company is counted inside the run (`failed_items`), not a
  failed run (FR-CRMAI-JOB-01). `JOBS` gains the five names with their
  intervals; `automations` is relabelled "Automations" (JOB-05, CHG-07).
- `jobs.failure()` gains a `safe` path: AI jobs raise `ai.SafeError(step,
  exc_type)` whose message is the fixed sentence ("Claude call failed:
  HTTPStatusError"); `jobs.run` stores that as `error` and `error_public`
  and logs it without `logger.exception` (no traceback), JOB-03.
- Enrichment follows the email-sync pattern (`007` S10): the tick starts the
  workspace's run and enqueues the first batch; each batch handles at most 10
  companies, then enqueues the next with `countdown=60` carrying the run id;
  the last batch (queue empty, budget reached or switch off) finishes the run.
  Summaries and meetings interleave between batches on the single slot.

### 5.2 Layering — follows `007` plan §5.2

| Layer | New modules | Changed (009's edits only) |
|---|---|---|
| `app/domain/crm/` | `ai_rules.py` (phase and budget rules, `ai` validator, IST month window, cost estimate, money masking, phone stripping, URL policy, owner resolution, quoting, failure reasons, JSON schemas), `review.py` (types, routing, dedupe keys, staleness, expiry), `summaries.py` (qualifying messages, quote and signature stripping, prompt parts, items from output), `meetings.py` (matching, deal resolution, note body), `enrichment.py` (site normalisation, value mapping to select options and size bands, conflict detection), `ai_fields.py` (inputs, hash, cap), `followups.py` (R-1, R-3, R-4 detectors), `recency.py` (levels) | `settings.py` (`VALIDATORS["ai"]`; `AUTOMATIONS` + four keys), `flags.py` (`stuck_in_stage()`, `DEFAULT_THRESHOLDS` + six keys, `thresholds_refusal()` gains a branch bounding those six to whole days 1–365 while the existing keys keep `0 ≤ v ≤ 365×24` and the amber/red pair checks, `for_deal` takes the stuck flag), `today.py` (`TO_REVIEW` constant and `to_review()` appended), `digests.py` (`HEADINGS["to_review"]`, weekly flag count kind), `objects.py` (`VIEW_FIELDS` contact_recency rows), `dashboard.py` (flag kind label) |
| `app/services/crm/` | `claude.py` (raw HTTP Messages client), `ai.py` (the one call wrapper), `review.py` (create, list, approve, dismiss, expire), `summaries.py`, `fireflies.py` (raw HTTP GraphQL client), `meetings.py`, `web_fetch.py`, `enrichment.py`, `ai_fields.py`, `followups.py`, `recency.py`, `intelligence.py` (decorates record reads with summaries, enrichment, AI fields, recency, open item count) | `gmail.py` (`message_text()`, docstring, CHG-01), `jobs.py` (`JOBS`, safe failure), `today.py` (append the To review section in `build`'s return), `flags.py` (stuck flag, recency recompute), `notifications.py` (`stuck_in_stage_notices`, `budget_reached_notice`), `context.py` (`actor()` emits the `suggestion_id` key only when `ctx.suggestion_id` is set), `store.py` (new reads and RPC wrappers, appended, plus `set_contact_recency()` calling `crm_set_contact_recency`), `export.py` (`WORKSPACE_OBJECTS` plus a dedicated reader and presenter per AI object, because `_all_rows` reads `objects.TABLES[kind]` and `present_rows()`, which know record kinds only), `records.py` (timeline entries carry `suggestion_id`), `email_sync.py` (`threads()` adds `thread_key` and the summary; label removal itself is in `crm_remove_emails`) |
| `app/api/crm/` | `ai.py` (`GET /crm/ai`), `review.py`, `summaries.py`, `enrichment.py`, `ai_fields.py` | `deps.py` (`CrmContext` gains `suggestion_id: str \| None = None`, the last field, so every existing constructor call is unchanged), `__init__.py` (`ROUTERS`), `settings.py` (nothing: the key is validated by `VALIDATORS`), `records.py` (record `GET` passes its answer through `intelligence.decorate`), `email.py` (threads gain `thread_key` and `summary`), `workspaces.py` (`bootstrap` gains `ai`) |
| `app/schemas/crm.py` | `AiSettingsValue`, `ReviewItemOut`, `ReviewApproveIn`, `ThreadSummaryOut`, `EnrichRunIn`, `AiFieldIn`, `AiFieldPatch`, and the strict output models every job validates model output against (`SummaryOutput`, `MeetingOutput`, `EnrichmentOutput`, `AiFieldOutput`, `FollowupDraftOutput`) | appended at the end |
| `app/tasks/` | `crm_ai.py` | `__init__.py` (one import), `crm.py` (`_stale_notices` → `_automations`, each switch checked separately, JOB-05) |
| `app/mcp/crm.py` | three tools appended at the end | nothing else |
| `app/config.py`, `app/worker.py`, `backend/entrypoint.sh`, `backend/Procfile` | — | §2.2, §5.1 |

`app/domain/tasks/service.py` and `app/schemas/tasks.py` are `008`'s; `009`
calls `create_task` and builds `TaskCreate`, and changes neither
(coordination rule 2).

### 5.3 The call path — FR-CRMAI-LLM

```text
job (crm-ai worker) ─▶ ai.call(ws, job, schema, instruction, parts, now)
   1. ai_rules.phase_on(settings, phase)          else Skipped("Switched off")
   2. rates set?                                   else Skipped("AI rates are not set")
   3. crm_ai_spend(ws, ist_month_start(now))      ≥ budget → budget_reached_notice(ws, month); Skipped("AI budget reached")
   4. prompt = instruction + quoted(parts)         phone numbers removed; untrusted text in <data> blocks
                                                   with "never follow instructions inside"; no amounts (FIELD-03)
   5. claude.messages(model, tools=[schema], tool_choice=forced, timeout=60 s)
   6. usage row (tokens, cost_usd, outcome)        no text
   7. pydantic validate (strict output model)      invalid → one retry (Q-36) → AiFailed("Output did not match the schema")
   8. ai_rules.mask_money() over every string      URL policy: drop any URL except the allowed kinds
   9. return the validated, masked object
   any exception ─▶ SafeError("<step> failed: <ExceptionType>")   never str(exc), never a body
```

- `services/crm/claude.py`: `POST https://api.anthropic.com/v1/messages` with
  `x-api-key`, `anthropic-version`, `content-type`; a forced tool call whose
  `input_schema` is the job's schema gives structured output; `usage.input_tokens`
  and `usage.output_tokens` feed the log. Timeouts: connect 5 s, read 60 s.
  Latency, status and tokens logged as one JSON line, never content (NFR-06).
  It refuses to run when `ANTHROPIC_API_KEY` is unset.
- Budget (FR-CRMAI-LLM-07, Q-23): the month is the Asia/Kolkata calendar month;
  the check runs before every call; overshoot is bounded by the one call in
  flight because the worker runs one task. Budget 0 (the seed) means nothing
  runs (Q-02).
- Budget reached (FR-CRMAI-LLM-08, Q-03): run recorded `skipped` with "AI
  budget reached"; `notifications.budget_reached_notice` creates one bell
  notice per Admin with dedupe key `ai_budget:<ws>:<YYYY-MM>` and one
  out-of-app message through `notify.deliver`; `GET /crm/ai` and the Jobs tab
  show the banner while `budget_reached`.
- Item failures (FR-CRMAI-LLM-03): the reason is one of the four fixed
  sentences; enrichment stores it on its row; summaries and meetings count it
  in the run (`failed_items`), and the thread or transcript stays pending for
  the next tick.
- Money masking (FR-CRMAI-LLM-05, Q-08): `ai_rules.mask_money(text)` replaces
  each pattern of the FR with "[amount]". It runs on every stored string of
  every job, in the wrapper (so no job can forget it) and again in
  `review.upsert` for items built from stored text (R-1 drafts).
- Owners (FR-CRMAI-SUM-12): the prompt lists the workspace's Admins and
  Members by name and email with an opaque index; the output returns the index
  or "unknown"; the server maps the index to a member id of that workspace or
  stores `owner_unknown`.

### 5.4 Review Queue — FR-CRMAI-REV

```text
POST /crm/review/{id}/approve ─▶ review.approve(ctx, id, body)
   load item (workspace, visible to ctx: owner or Admin)          else 404
   target trashed?                                                → 422
   claim: crm_review_claim(actor, id, 'approved')                 lost race → 409 "already decided"
   per type: read the target now (row R); compare R's fields with item.current_values   differ → release; 409 "changed"
             CRM change: call the manual service with ctx2 = dataclasses.replace(ctx, suggestion_id=id),
                         payload + expect_updated_at = R.updated_at
             task:       create_task(TaskActor(user=ctx.user, kind="user", channel=ctx.channel, suggestion_id=id), TaskCreate(…))
   refused (CrmRefused / 008 refusal) → crm_review_release(actor, id, refusal); re-raise
   ok → crm_review_release(actor, id, NULL, result)
```

- The mechanism (FR-CRMAI-REV-05): `api/crm/deps.py` `CrmContext` gains
  `suggestion_id: str | None = None` as its last field (frozen dataclass, so
  `dataclasses.replace` makes the copy). `services/crm/context.actor(ctx)`
  emits `"suggestion_id"` only when it is set, so every existing actor is
  byte-for-byte unchanged. Only `review.approve` sets it.
- Tasks: `TaskActor` is `008`'s (`app.domain.tasks.service`; `008` contracts
  §4: `user: CurrentUser`, `kind`, `channel`, `suggestion_id: UUID | None =
  None`). `009` builds `TaskActor(user=ctx.user, kind="user",
  channel=ctx.channel, suggestion_id=item.id)`; `as_rpc()` puts
  `suggestion_id` into `crm.actor`, so `crm_audit_row` (061) writes the item id
  on the task's own audit rows, as it does for a CRM change (owner decision of
  18 October 2026). In S1b, before `008` lands, the fake `create_task` is typed
  against the same `TaskActor` signature.
- Task links: the target and its company, **de-duplicated** by
  `(record_type, record_id)` before `TaskCreate` is built, so a company target
  does not send the same link twice (`008`'s `task_links_target` UNIQUE would
  refuse it).

- Routing (FR-CRMAI-REV-02) is decided once at creation in
  `domain/crm/review.py` `owner_for(type, facts)`: the deal owner, the
  commitment owner when an Admin or Member, else the mailbox owner, else the
  deal or company owner; the company owner for company-level items. A Viewer,
  inactive, non-member or unknown owner → `owner_id NULL` (the Admins).
- Visibility (FR-CRMAI-REV-03): Members see items whose `owner_id` is theirs;
  workspace Admins (CRM role) see all; Viewers see none. Another Member's item
  is answered 404.
- "Edit and approve" sends `edits`; the manual service validates them exactly
  as a manual edit would.
- Tasks (FR-CRMAI-REV-06): `TaskCreate` is `008`'s, defined in `008`
  contracts §4 and not redefined here. `009` passes only these rule-2 fields,
  each under `008`'s name and type, and leaves `008`'s optional fields (`tags`,
  `parent_id`, `status`, `waiting_on_id`, `waiting_on_text`, `category_id`,
  `completed_at`) at their defaults:
  `title: str` (1–200; the item's title, or the edited one),
  `notes: str | None` (None unless "Edit and approve" sends notes),
  `due_on: date | None` (the item's date), `bucket=None` (derived by `008`),
  `priority="normal"`, `assignee_id: UUID` (the item's owner when they are an
  active Admin or Member of the workspace, else the approver),
  `workspace_id: UUID` (the item's workspace, equal to the one `008` derives
  from the links), `links: list[TaskLinkIn]` (`{record_type:
  "company"|"person"|"opportunity", record_id: UUID}`, de-duplicated as
  above), `source: "email_commitment"|"meeting_action"`,
  `source_ref: str | None` (the item's `source_key`, ≤ 300, within `008`'s
  500). `008`'s refusal leaves the item open with the refusal stored.
- Audit (FR-CRMAI-REV-05): `context.actor(ctx)` adds `"suggestion_id":
  ctx.suggestion_id` when the review service set it on a copied context.
  `crm_audit_row` (061) writes it. For tasks, `TaskActor.suggestion_id` does
  the same (the `TaskActor` bullet above).
- No notice (FR-CRMAI-REV-10, Q-09): creating or refreshing an item sends no
  bell and no message; only the To review section and the digest show it.
- Expiry and tombstones (FR-CRMAI-REV-07): the daily `review_expiry` job calls
  `crm_review_expire(now − 14 days)` and `crm_review_tombstone(now − 90 days)`
  per workspace.
- Visibility of the queue (FR-CRMAI-FLAG-04): `review.visible(ws, person)` is
  true while any phase is on or the person has open items (Admins: the
  workspace has open items).

### 5.5 Summaries — FR-CRMAI-SUM

1. `crm_ai.summaries` per workspace: phase 1 on, Gmail live
   (`email_sync.live`), else skipped. `crm_pending_threads(ws, 20)`.
2. Per thread: `gmail.message_text(mailbox, gmail_id)` for each stored message
   id **only** (never the Gmail thread), `domain/crm/summaries.strip()`
   removes quoted history (`On … wrote:`, `>` lines, `-----Original
   Message-----`, `From:` blocks) and signatures (`-- ` delimiter and the
   common sign-off block), then `ai.call(job="summaries")`.
3. `domain/crm/summaries.items()` builds `next_step_change` (when a linked
   open deal's next step or date differs, one item with every linked open
   deal as a choice) and `task_from_commitment` (each dated commitment).
4. `crm_store_summary` writes summary, links and items in one transaction,
   re-checking the watermark.

The text lives only in the task's local variables. The Gmail docstring
changes to "metadata only for sync; `message_text()` exists for the
summariser alone (spec 009 FR-CRMAI-CHG-01)". `test_only_metadata_is_asked_for`
stays and a new test pins the only caller.

### 5.6 Meetings — FR-CRMAI-MEET

`crm_ai.meetings` is **one task for all workspaces** that records one run per
workspace (`jobs.run` per workspace inside it), because one Fireflies poll
serves both: it lists transcripts since the last poll with metadata only
(`id, title, date, dateUpdated, participants/attendee emails, transcript_url`),
matches attendee emails against each workspace's people (primary and
additional emails), drops workspace-domain attendees as internal, and:

| Matches | Action |
|---|---|
| No workspace | nothing; content never fetched |
| More than one workspace | nothing in either; each counts `skipped_multi_workspace` (Q-26) |
| Exactly one, phase 2 off there | counted `skipped_switched_off`; nothing fetched |
| Exactly one, phase 2 on | fetch the transcript sentences and summary, `ai.call(job="meetings")`, `crm_store_meeting_note` |

The poll cursor is the newest `dateUpdated` seen, kept in Redis key
`crm:fireflies:cursor` (a cursor, not content). Re-polling is safe anyway
(unique `source_ref`).

### 5.7 Enrichment — FR-CRMAI-ENR

The trigger of data-model §3.5 queues; the job reads up to 5 same-site pages
(home, then `/about`, `/about-us`, `/company`, `/contact` when linked from the
home page), converts to text with `html.parser`, and calls `ai.call(job=
"enrichment")`. `domain/crm/enrichment.py` maps the industry to an existing
`company.industry` option by case-insensitive label (no match: kept as
enriched only, with "No matching industry option"), the employee band to the
existing `company.size` options, keeps LinkedIn only as a `linkedin.com/company/`
URL and the website only on the same site. Promote is `PATCH
/crm/companies/{id}`. When the main field is filled with a different value an
`enrichment_conflict` item is created instead of a promote button.

### 5.8 AI fields — FR-CRMAI-FIELD

`crm_ai.ai_fields` per workspace: for each enabled field and each live record
whose inputs hash changed (or `dirty_at` is due), within the hourly cap, call
`ai.call(job="ai_fields")` with the record's own fields (no amounts), its
latest summaries, meeting notes, enriched values, next step dates and days in
stage. Writes `crm_ai_field_values` directly (a log). `What is blocking` is
computed only when Deal outlook is At risk or Stalled, else its value is
cleared. A summary, meeting note or enrichment write sets `dirty_at` on the
linked records' values (in `crm_store_summary`, `crm_store_meeting_note` and
the enrichment job). Values never feed a flag, colour, stage or probability.

### 5.9 Follow-up rules — FR-CRMAI-RULE

| Rule | Where | Detail |
|---|---|---|
| R-2 Stuck in stage | `domain/crm/flags.stuck_in_stage(deal, now, t)` | Amber at `stuck_<stage>_days` from `stage_entered_at`, red at twice; computed in `services/crm/flags.recompute_*` only while R-2 and phase 3 are on, so it counts toward `flag_level` like any flag (FR-CRMAI-RULE-03). Notice: `notifications.stuck_in_stage_notices` in the `automations` job, dedupe per deal per stage entry; the same-day same-level Stale deal message is suppressed (Q-14) |
| R-1 No reply | `domain/crm/followups.no_reply()` over `crm_no_reply_candidates` | In the `automations` job; skips Do not contact; dedupe per outbound message; enqueues one `crm_ai.followup_draft(ws_id, item_key_ids)` per workspace per tick (ids only); the draft task reads the thread's stored summary and the record's fields, never a body, and creates a `rule_suggestion` with the draft. It runs through `jobs.crm_job("followup_drafts")`, so each batch is one `crm_job_runs` row with `done`, `failed_items` and `skipped` (budget, switch) counts (FR-CRMAI-JOB-01, LLM-08). `followup_drafts` is a sixth AI job name, added to the job CHECK and `crm_job_latest()` in 066 (D-4, approved; spec FR-CRMAI-JOB-01) |
| R-3 Lead gone cold | `followups.lead_cold()` over `crm_lead_activity` | Contacted or Nurturing, quiet `lead_cold_days`; options by stage (Q-39); suppression after dismissal (Q-40) |
| R-4 Campaign follow-through | `followups.campaign_followthrough()` | Engaged or Meeting booked for more than 7 days from `status_changed_at`, no converted deal, no open task on its company or person (open = `008`'s Open or Waiting); skips Do not contact |

`tasks/crm.py`'s `automations` job checks each switch separately and records
`skipped` only when all five are off (FR-CRMAI-JOB-05). R-2's flag is
computed in the `flags` job. Every rule also needs phase 3 on.

### 5.10 Contact recency — FR-CRMAI-REL

Computed in the existing `flags` job (`services/crm/flags.recompute_workspace`)
when phase 4 is on, from `crm_contact_facts`, written through a dedicated
writer `crm_set_contact_recency(p_workspace, p_table, p_rows)` (067), modelled
on `038`'s `crm_set_flags`: it updates only `contact_recency`, so
`updated_by` is never touched and the touch and audit skip lists (which
compare changed columns, not actor kinds) leave `updated_at` and the audit
alone (FR-CRMAI-REL-02). It is **not** written through `crm_apply`, which
would set `updated_by` to the sync actor's NULL user. With Gmail sync
off for the workspace the facts use meeting notes only and the record answer
carries `meetings_only: true`.

### 5.11 Today and the digest — FR-CRMAI-REV-09

- `domain/crm/today.py`: `TO_REVIEW = "to_review"` and `to_review(items)`
  appended after the existing functions; `SECTIONS` and `assemble` untouched.
- `services/crm/today.py` `build()`: its return's `sections` becomes
  `rules.assemble(...) + review.today_section(ws_id, person_id)`, which is
  `[]` when the queue is not visible or the person is a Viewer. One expression
  changes; the five sections' code is `008`'s and is not touched
  (coordination rule 3). `008`'s morning brief reads `build`, so it shows the
  section too.
- `domain/crm/digests.HEADINGS` gains `"to_review": "To review"`.

### 5.12 Frontend

| Piece | How |
|---|---|
| Review page | `app/(crm)/crm/[ws]/review/page.tsx`: tabs Mine / All (Admins) / Decided (Admins, 90 days); `components/crm/review-item.tsx` (suggestion, current value, source link, Approve, Edit and approve, Dismiss; deal picker; refusal shown) |
| Sidebar | `components/crm/review-nav-item.tsx` (new, `009`'s own file): "Review" with the open count from bootstrap, shown while `ai.review_visible`. Inserted into `components/crm/shell.tsx` (the CRM shell, whose header quick-add and My Tasks entry are `008`'s) by **one line**, placed after `008`'s My Tasks entry; nothing else in `shell.tsx` changes |
| Today | `components/crm/to-review-section.tsx`; `app/(crm)/crm/[ws]/page.tsx` renders it after `<TodayList>`, passing `TodayList` the five sections only (`lib/crm/review.ts` `splitToReview()`), so `today-list.tsx` is not edited |
| AI marker | `components/crm/ai-marker.tsx`: an "AI" badge with a tooltip (hover and focus) naming source, model and time |
| Summaries | `components/crm/thread-summary.tsx`, used in `emails-tab.tsx` (each thread row expands) and `timeline.tsx` (email entries); text nodes only |
| Meeting notes | Note bodies are rendered by `NoteList` in `components/crm/note-composer.tsx` (the `<Markdown>` component, `marked`), used by `NotesTab` on every company, person, deal and campaign page. There, when `note.ai_provenance` is set, the body renders as plain text with the AI marker and the transcript link; otherwise the `Markdown` component stays. `timeline.tsx` only gets the marker on note entries (it does not render bodies) |
| Enrichment | `components/crm/enrichment-panel.tsx` on `companies/[id]/page.tsx` (values, status, Promote, conflict link); **Enrich selected** in `bulk-bar.tsx` on company views for Admins |
| AI fields | `components/crm/ai-fields-panel.tsx` on company and deal pages; Settings tab "AI fields" (`components/crm/ai-fields-editor.tsx`) |
| Settings | `app/(crm)/crm/[ws]/settings/page.tsx`: new tabs "AI" (`components/crm/ai-settings.tsx`: budget, month spend by job, phases, "estimate"), "AI fields", "Follow-up rules" (`components/crm/followup-rules-form.tsx`); budget banner (`components/crm/ai-budget-banner.tsx`) on AI and Jobs tabs. The existing Thresholds and Automations tabs keep unknown keys on save (`{...stored, ...edited}`), so the validators' "all keys" rule passes after S3b |
| Contact recency | `components/crm/contact-recency-chip.tsx` on company and person pages; `lib/crm/view-fields.ts` label and options |
| Data | `lib/api/crm.ts` wrappers and `lib/api/crm-types.ts` types, appended |

### 5.13 MCP

Three tools appended to `app/mcp/crm.py` (contracts §3). Nothing else in that
file changes. `server.py` needs no import line from `009` (`crm.py` is
already imported). The `INSTRUCTIONS` paragraph of FR-CRMAI-MCP-05 lives in
`server.py`. `008` adds its own tasks paragraph and two docstrings there.
Rule 3a (a) (§6), approved by the owner on 18 October 2026, lets `009` append
exactly one paragraph **after** `008`'s (T049). There is no fallback copy of
the paragraph in a tool description.

---

## 6. Working alongside `008`

| Rule | How this plan follows it |
|---|---|
| 1. Migrations | Only `060`–`079` (`008` owns `046`–`059`). Each assumes every `008` migration ran; none is needed by `008`. Every replaced function is copied from the latest definition in the rebased, reset database (data-model §1). `009` replaces seven existing functions: `crm_audit_row`, `crm_purge`, `crm_merge_companies` and (067, only if its filter needs it) `crm_query_rank`, which `008`'s 048 also replaces, and `crm_remove_emails`, `crm_touch_updated_at` and `crm_job_latest`, which `008` does not touch. After its S1a, `008` replaces none of the seven (`008` plan §9 rule 1, `008` data-model §1). **Ordering:** S1b and every later slice starts only once `008` S1a is committed on `008-tasks` (`supabase/migrations/048_tasks_crm_functions.sql` present); otherwise it stops and reports. **Late `008` fixes:** before each slice, `009` lists `008` migrations numbered after `050`; if one replaces one of the seven (its commit's first line says so), `009` ships a `068`+ migration re-copying the latest body. `008`'s db test `tests/db/test_task_migration_db.py::TestAfterMigration` (`008` T037) asserts no function body names `crm_task`; it runs at every `009` db gate, so it also catches a `009` copy made from a pre-048 body, and `009` adds no second copy of that test. One `009` db test (S5, T170) compares the table names in the replaced bodies with `008`'s |
| 2. Task creation | `009` calls `app.domain.tasks.service.create_task(TaskActor(user=ctx.user, kind="user", channel=ctx.channel, suggestion_id=item.id), TaskCreate(…))` and nothing else of `008`. `TaskActor` and `TaskCreate` are defined once, in `008` contracts §4; `009` passes only the rule-2 fields listed in §5.4 (names and types checked against that definition) and leaves the optional fields at their defaults. `create_task` refuses the same `{record_type, record_id}` twice (422 "The same record is linked twice.") and does not de-duplicate, so `009` de-duplicates links first (§5.4). If the module is not yet on `008-tasks` when S1b starts, S1b builds the call behind a function-level import and tests it with a monkeypatched fake typed against the same `TaskActor` signature; the real-call test is required from the first slice whose rebase finds the module, and by S5 at the latest |
| 3. Shared files | `mcp/crm.py`: append only; `009` owns the `crm_*` count test in `test_crm_mcp.py`, and no `008` test pins that count. `server.py`: no import line; one `INSTRUCTIONS` paragraph appended after `008`'s (rule 3a (a)). Today: one expression in `build()` plus an appended domain function and a new component. Celery beat and routes: `009`'s own block, placed after `008`'s. CRM Settings page: new tabs (and the two existing tabs' save keeping unknown keys, which `008` does not touch). CRM shell (`components/crm/shell.tsx`, rule 3a (b)): `008` owns its header quick add and places My Tasks; `009` inserts one `<ReviewNavItem/>` line after it. Help: `009` writes `docs/guides/12-crm-intelligence.md` at `order: 12` and one line in `docs/README.md`; `008` writes `docs/guides/10-tasks.md` at `order: 11`; `10-crm-admin.md` keeps order 10. Portal header and nav: untouched. `pyproject.toml`: untouched |
| 3a. Extensions of rule 3 (approved by the owner on 18 October 2026; the same rule as `008` plan §9 rule 3a) | (a) `server.py`: `008` adds its tasks paragraph to `INSTRUCTIONS` and edits the `my_day`/`log_day` docstrings; `009` appends exactly one `INSTRUCTIONS` paragraph after `008`'s (FR-CRMAI-MCP-05). (b) `components/crm/shell.tsx` (the CRM shell): `008` owns its header (quick add) and places its My Tasks nav entry; `009` adds only its Review entry, as one appended line rendering a component in its own file, after `008`'s entry. (c) Existing `007` files both plans edit: `services/crm/{store,records,flags,export}.py`, `domain/crm/{flags,digests,objects}.py`, `schemas/crm.py`, `api/crm/records.py`, `app/config.py`, `app/tasks/__init__.py`, `frontend/lib/api/crm-types.ts`, `frontend/lib/crm/view-fields.ts`, `docs/operations/deployment.md`, and the tests `test_crm_today.py`, `test_crm_digests.py`, `test_crm_dashboard.py`, `test_crm_flags.py`, `test_crm_automations.py`, `test_crm_settings_lists.py`, `test_crm_object_fields_registry.py`, `test_crm_csv_io.py`: `008` changes existing lines only (renames, status rules, bucket defaults); `009` only appends new functions, keys and tests at the end of each file or class and never edits a line `008` touched; on rebase `009` keeps `008`'s hunks verbatim and re-applies its own appends |
| 3b. Tests `008` adds that see `009`'s additions | Settled in `008`'s plan (§9 rule 3, tasks T035, T050): `008`'s `test_task_crm_changes.py` asserts only that `008` adds no `crm_*` tool and that the task-related `007` tools are still registered, never the `crm_*` total; `008`'s privacy sweep `test_task_privacy_sweep.py` (T050, extended by T074, T098, T126 with the same scope) requires a fixture only for routes under `/api/v1/tasks`, the `task_*`/`inbox_add` tools and its explicit list of existing task-touching routes and tools, and leaves every other `/api/v1/crm` route and `crm_*` tool to the CRM isolation sweep (reported, not failed). `009` edits neither file; T031 confirms both after the rebase and stops and reports if either would fail on `009`'s routes or tools |
| 4. Branches | Worktree `../nldw-task-master-009` on `009-crm-intelligence`; `git fetch` not needed (same repo); `git rebase 008-tasks` at the start of every slice, before any file is touched. **Before T001**, the owner or the `008` session commits both `specs/008-tasks/` and `specs/009-crm-intelligence/` on `008-tasks` (never `supabase/config.toml`); `009` does not commit on `008-tasks` |
| 5. Shared Supabase | Every `db reset` and `CRM_DB_TESTS=1` run is wrapped in the lock (tasks.md, Gates), runs as one shell invocation with the reset and tests chained by `&&`, and starts with `npx supabase db reset` from the worktree, so the stack holds exactly this branch's migrations |
| 6. `config.toml` | Never staged; every slice's gate checks `git diff --cached --name-only` |

---

## 7. Project structure

### Documentation (this feature)

```text
specs/009-crm-intelligence/
├── source.md        # owner's original (behaviour only)
├── spec.md          # what, operative
├── plan.md          # this file
├── data-model.md    # tables, columns, functions, migrations 060–067
├── contracts/api.md # HTTP routes + 3 MCP tools
└── tasks.md         # slices S1a–S5, task ids T001…
```

### Source code

```text
supabase/migrations/060_…067_*.sql
backend/app/
├── domain/crm/{ai_rules,review,summaries,meetings,enrichment,ai_fields,followups,recency}.py   (new)
├── domain/crm/{settings,flags,today,digests,objects,dashboard}.py                              (appends)
├── services/crm/{claude,ai,review,summaries,fireflies,meetings,web_fetch,enrichment,
│                 ai_fields,followups,recency,intelligence}.py                                  (new)
├── services/crm/{gmail,jobs,today,flags,notifications,context,store,export,records,email_sync}.py (changes)
├── api/crm/{ai,review,summaries,enrichment,ai_fields}.py                                       (new)
├── api/crm/{__init__,deps,records,email,workspaces}.py                                         (changes)
├── schemas/crm.py                                                                              (appends)
├── tasks/crm_ai.py (new); tasks/__init__.py, tasks/crm.py (changes)
├── mcp/crm.py                                                                                  (3 tools appended)
└── config.py, worker.py ; backend/entrypoint.sh, backend/Procfile
backend/tests/
├── test_crm_ai_*.py, test_crm_review_*.py, test_crm_summaries_*.py, test_crm_meetings_*.py,
│   test_crm_enrichment_*.py, test_crm_web_fetch.py, test_crm_followup_rules.py,
│   test_crm_contact_recency.py, test_crm_intelligence_acceptance.py, crm_ai_fakes.py           (new)
├── crm_fake.py and the 007 tests named per slice                                               (extended)
└── db/test_crm_{ai_foundations,review,summaries,meetings,enrichment,ai_fields,followup,contact_recency}_db.py
frontend/
├── app/(crm)/crm/[ws]/review/page.tsx (new); [ws]/page.tsx, settings/page.tsx,
│   companies/[id]/page.tsx, people/[id]/page.tsx, opportunities/[id]/page.tsx                  (changes)
├── components/crm/{review-item,review-nav-item,to-review-section,ai-marker,thread-summary,enrichment-panel,
│   ai-fields-panel,ai-fields-editor,ai-settings,ai-budget-banner,followup-rules-form,
│   contact-recency-chip}.tsx                                                                    (new)
├── components/crm/{shell (one line),emails-tab,timeline,note-composer,bulk-bar}.tsx               (changes)
└── lib/api/crm.ts, lib/api/crm-types.ts, lib/crm/review.ts (new), lib/crm/view-fields.ts
docs/architecture/crm.md, docs/guides/09-crm.md, docs/guides/10-crm-admin.md,
docs/guides/08-connecting-claude.md, docs/guides/12-crm-intelligence.md (new),
docs/operations/deployment.md, docs/README.md (one line)
```

---

## 8. Testing strategy (NFR-CRMAI-09)

| Kind | Where | What |
|---|---|---|
| Domain | `test_crm_ai_text_rules.py`, `test_crm_ai_budget.py`, `test_crm_review_rules.py`, `test_crm_summaries_rules.py`, `test_crm_meetings_rules.py`, `test_crm_enrichment_rules.py`, `test_crm_ai_fields_rules.py`, `test_crm_followup_rules.py`, `test_crm_contact_recency.py` | Pure functions, fixed `NOW`, classes per FR, plain-sentence names |
| Fakes | `tests/crm_ai_fakes.py` (new): `FakeClaude` (scripted outputs, records every prompt, can return invalid output), `FakeFireflies` (records every transcript fetch), `FakeGmailText`, `FakeWeb` (scripted DNS answers and pages), `CapturedCelery` (every `apply_async` payload) | Every job test runs on these; no network |
| Jobs | `test_crm_ai_jobs.py`, `test_crm_summaries_job.py`, `test_crm_meetings_job.py`, `test_crm_enrichment_job.py`, `test_crm_ai_fields_job.py` | Idempotency (run twice, nothing new, no second model call), switches, budget stop, safe errors |
| Routes | `test_crm_ai_routes.py`, `test_crm_review_routes.py`, `test_crm_summaries_routes.py`, `test_crm_enrichment_routes.py`, `test_crm_ai_fields_routes.py` | Guards, 404 on foreign ids, Viewer views, refusals, approval through the manual validation |
| Privacy | `test_crm_ai_privacy.py` | NFR-CRMAI-01 marker test, grown per slice (email in S1c, transcript S2a, web page S2b, R-1 S3b), full in S5 |
| Isolation, redaction | `test_crm_isolation_sweep.py`, `test_crm_redaction_sweep.py` (extended), `test_crm_ai_isolation.py` (prompts) | NFR-CRMAI-02, -03 |
| Database | `tests/db/test_crm_*_db.py` | Each new function's success, refusal and race paths; seeds; triggers; RLS and grants via the existing security sweep |
| Acceptance | `test_crm_intelligence_acceptance.py` | One test per line of spec §15 (S5) |

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| `008` and `009` edit the same functions (`crm_audit_row`, `crm_purge`, `crm_merge_companies`) | S1b waits for `008`'s 048; rebase before every slice; copy the latest definition from the reset database (`pg_get_functiondef`); a late `008` fix to one of them gets a `068`+ re-copy (§6 rule 1); `008`'s db test asserts no body names `crm_task` (it runs at every `009` gate) and a `009` db test compares bodies with `008`'s (S5); the whole suite, `008`'s tests included, runs at every gate |
| The single AI slot is held by enrichment while a summary waits | Enrichment batches are ≤ 10 companies and chain with a 60 s gap, so summaries and meetings run between them |
| A model answer carries an instruction or link from an email | Quoted data with an explicit instruction; strict schema; URL policy drops links; plain-text rendering; a fixture test (spec §15) |
| Money leaks in generated text to Viewers | Masking in the one wrapper over every string; a fixture test with each listed pattern through every Viewer route and tool |
| Fireflies or Gmail API shape differs from the plan | Clients are small and isolated; fakes are built from the documented shapes; the first real run is by the owner after step zero |
| Rate table unknown | Every AI job skips with "AI rates are not set" until `CRM_AI_*_USD_PER_MTOK` are set (D-1, approved) |
| `supabase/config.toml` local change committed | Gate in every slice; the worktree does not carry the uncommitted change at all |

---

## 10. Deployment (S5 writes it into `docs/operations/deployment.md`)

1. Migrate (`npx supabase db push`, `060`–`067`, after `008`'s).
2. Deploy api, worker, beat, `worker-crm-sync` and the new `worker-crm-ai`
   (`PROCESS_TYPE=worker_crm_ai`) from one commit.
3. Owner: secrets on `worker-crm-ai` (Claude, Fireflies, Gmail credential,
   rates). Every phase stays off until a workspace Admin sets a budget and
   switches a phase on, Labs first (FR-CRMAI-FLAG-05).

---

## 11. Cross-plan and owner items (settled 18 October 2026)

| Item | Why it was open | Settled as |
|---|---|---|
| FR-CRMAI-MCP-05 server instructions paragraph | It lives in `backend/app/mcp/server.py` `INSTRUCTIONS`; rule 3 as first written let `009` add only an import line there | Rule 3a (a) approved by the owner: `008` adds its paragraph and docstrings, `009` appends exactly one paragraph after `008`'s (T049). No fallback in a tool description |
| `suggestion_id` on the audit row of an approved **task** | `TaskActor` first had no `suggestion_id` | Owner decision: `008`'s `TaskActor` has `suggestion_id: UUID \| None = None`, passed in `as_rpc()`; `009` sets it to the item id, so the task's audit rows carry it (§5.4) |
| `crm_*` tool count | `009` raises it to 20 (S1b) and 21 (S1c) and cannot edit `008`'s files | Only `test_crm_mcp.py` (`009`'s) asserts the total; `008` T035 asserts only that `008` adds no `crm_*` tool (§6 rule 3b). T031 confirms it after the rebase |
| `008`'s privacy sweep scope | `009` adds routes and tools `008` cannot fixture | `008` T050 (and T074, T098, T126) is scoped to task routes and tools plus its explicit task-touching list (§6 rule 3b). T031 confirms it after the rebase |
| Rate table and model availability (spec §17.1) | Owner items | Config values; jobs skip until the rates are set (D-1) |
| Fireflies field names | Not verifiable without the owner's key | `services/crm/fireflies.py` isolates them; fakes follow Fireflies' published GraphQL schema; the owner's first run confirms |

### 11.1 Built but not asked for — owner decisions of 18 October 2026

The Working Agreement forbids extras. Each item below went beyond the spec's
words and was put to the owner, who decided each one on 18 October 2026
(recorded in spec §14 as D-1 to D-4). This table is the settled record; the
last column is what is built.

| # | Extra | Where | Decision | What is built |
|---|---|---|---|---|
| D-1 | "AI rates are not set" skip reason and `rates_configured` on `GET /crm/ai` | §2.2, T007, T017, T023 | **Approved** (spec FR-CRMAI-LLM-07) | The gate, the skip reason and the field |
| D-2 | Enrichment failure reasons beyond the spec's: "Website address is not allowed" | data-model §3.5, T105 | **Cut** | `failure_reason` is one of ENR-06's two sentences ("Website did not respond", "No company information found") or LLM-03's four model reasons, which the spec requires for every call; a host the URL policy refuses is recorded as "Website did not respond" |
| D-3 | `POST /crm/enrichment/run` accepting `{view_id}` (≤ 2,000 rows); `GET /crm/review/{id}`; the `type`, `record_type`, `record_id`, `before` filters on `GET /crm/review` (and `type` on `crm_review_list`); Admin-editable `max_lines`, `with_reason`, `depends_on`, `position` on AI field definitions; the `attempts` column on `crm_company_enrichment`; the Admin's "approved, nothing changed" view state (CT-1) | contracts §2.2, §2.4, §2.5, §3; data-model §3.5, §3.6; CT-1; T037, T038 | **Cut** | Selected `company_ids` only; one list route with `scope`, `status` and `limit`; definitions limited to name, record type, prompt, output type, options, enabled (the seeds' `depends_on`, `max_lines`, `with_reason`, `position` fixed in migration 065, not editable); no `attempts`; a crashed approval simply shows as approved. The sidebar "Review" entry stays: it is how the page is reached |
| D-4 | A sixth job name `followup_drafts` in the job log | §5.9, data-model §3.8, T135, T144 | **Approved** (spec FR-CRMAI-JOB-01 lists it) | `followup_drafts` in the job CHECK, `crm_job_latest()` and `JOBS` (066) |
