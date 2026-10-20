# How the sales CRM works

How the CRM works *today*. What it must do lives in
[`specs/007-crm/spec.md`](../../specs/007-crm/spec.md), the tables and
database functions in [`data-model.md`](../../specs/007-crm/data-model.md),
the routes and tools in [`contracts/api.md`](../../specs/007-crm/contracts/api.md);
why, and the rules it may not break, live in the constitution. The
intelligence layer (summaries, the Review Queue, meetings, enrichment, AI
fields, the follow-up rules, contact recency) is specified in
[`specs/009-crm-intelligence/`](../../specs/009-crm-intelligence/spec.md)
and described [below](#the-intelligence-layer). Automatic email capture
(Primary and Sent mail without a label, discovery of new leads) is specified
in [`specs/010-email-capture/`](../../specs/010-email-capture/spec.md) and
described under [Email sync and capture](#email-sync-and-capture) and
[Email capture's discovery](#email-captures-discovery-spec-010).

The CRM is a second application inside the portal: its own pages under
`/crm`, its own routes under `/api/v1/crm/`, its own `crm_*` tables, and a
narrow, deliberate set of places where it touches TVA (the time, leave and
projects side). Two workspaces exist, Nunnari Labs (`labs`, linked to TVA)
and Nunnari Academy B2B (`academy`, not linked). Nothing ever crosses from
one workspace to the other.

---

## The shape of it

```
  browser (/crm/<workspace>/…)
     │  Supabase access token, or a personal token (nunp_…)
     ▼
  Next.js  ──  /api/proxy/[...path]          ← still the only route to the backend;
     │                                          it now carries uploads ≤ 4 MB and
     │                                          CSV/JSON downloads (FR-CRM-CHG-08)
     ▼
  FastAPI  ──  app/api/deps.py               ← who you are (staff or crm_only)
     │         app/api/crm/deps.py           ← the CRM guard: workspace + role
     │         app/domain/crm/*              ← the rules, pure
     │         app/services/crm/*            ← gather rows, build a plan
     │         app/services/crm/store.py     ← the only module that touches crm_* tables
     ▼
  Postgres ──  crm_apply / crm_deal_transition / …   ← one transaction per write;
               audit trigger, workspace-scoped references, re-checks
```

Claude reaches the same routes: every `crm_*` MCP tool is one in-process call
to a `/crm` route as the token's owner (`app/mcp/crm.py`), so the web app,
the API and MCP pass through the same guard and the same rules (D3).

## The request path and the CRM guard

`app/api/deps.py` authenticates every request once (`_authenticate`: session
or personal token → an active profile) and then splits:

| Dependency | Who passes | Used by |
|---|---|---|
| `current_user` (and `LeadDep`, `ManagerDep`, `AdminDep`, …) | staff only; a `crm_only` account gets 403 "This account is for the CRM only." | every TVA route, unchanged |
| `any_account` | staff and `crm_only` | the shared allow-list only: `GET /me`, personal tokens, the OAuth consent routes |
| `crm/deps.py` `crm_context` | any member of the named workspace | every `/api/v1/crm/` route |

**Deny by default for `crm_only`.** Because the TVA dependencies kept their
names and gained the refusal, a new TVA route refuses CRM-only people without
anyone remembering to. `tests/test_crm_only_refused.py` walks `app.routes`
and fails on any route it cannot classify; the allow-list is a constant in
that test, so widening it is a visible change. Every TVA people population
(bench, resources, utilisation, team day, digests, nudges, balances, …)
leaves CRM-only accounts out because `services/supabase.list_profiles`
does unless a caller passes `include_crm_only=True`, which only the Admin
People list, the TVA admin CRM panel and the audit name lookup do.

**The workspace.** `crm_context` reads the `workspace` query parameter (a key
such as `labs`, or an id). A workspace the caller is not a member of is
answered 404 "No such workspace.", the same as one that does not exist. With
no `workspace`, a read uses the caller's last-used workspace; any other
request from a member of more than one workspace is refused 422 naming their
workspaces (D13), so a write never lands where the person did not mean it.

**The role.** Admin, Member or Viewer, per workspace. What each may do is one
pure table, `domain/crm/permissions.py` (`may(role, action, *, owns,
is_assignee)`). A TVA role grants nothing in the CRM; only a membership does.
A TVA admin who is in no workspace administers memberships from the TVA
Admin "CRM workspaces" panel (`/api/v1/crm/tva-admin/…`), guarded by the TVA
admin role, not by a membership.

**Another workspace's record is 404.** Every store read takes `workspace_id`
first and filters on it, so a record of Academy named while acting in Labs is
"not found", exactly like an id that does not exist.
`tests/test_crm_isolation_sweep.py` checks every route with an id in its
path, every list, search, Today, export, dashboard and bell, the digest
builders and every `crm_*` tool.

## The browser has no table

Every table in `public`, TVA's and the CRM's, has row-level security on and
no grant at all to `anon` or `authenticated` (025 revoked them). The browser
signs in with Supabase Auth and then reads nothing from Supabase: every byte
of data goes through the proxy to FastAPI, which holds the service-role key.
No Realtime either; the bell polls `/api/v1/crm/notifications/unread-count`.
`tests/db/test_crm_security_db.py` reads the catalogue to prove it for every
table, including ones added later.

The backend's service-role key **bypasses row-level security**. The guard in
`api/crm/deps.py` and the `workspace_id` filters in `store.py` are what keep
workspaces apart in Python; the database functions below hold the same line
again, so a Python bug cannot write across workspaces.

## The write path and the audit trigger

```
route ─▶ service: load rows ─▶ domain: decide ─▶ plan ─▶ store.rpc(fn, {p_actor, p_plan})
                                                          │  one transaction
        set_config('crm.actor', actor, true) ◀────────────┘
        INSERT / UPDATE / DELETE crm_*  ──AFTER trigger crm_audit_row──▶ audit_log
        TVA rows (projects, phases, milestones, allocations) + their own audit rows
        RAISE CRM04 / CRM09 / CRM22 ──▶ store.refusal_for ──▶ 404 / 409 / 422
```

- **Every CRM write is one database function call.** Plain creates, updates,
  soft deletes, restores and link rows go through `crm_apply(actor, ops)`;
  anything with side effects has its own function (below). The service never
  writes a `crm_*` table with a plain PostgREST insert.
- **The actor** is `{user_id, kind: user|system|import|sync, channel:
  web|mcp|null, workspace_id}`, built from the `CrmContext`, never from the
  request body. `crm_apply` stamps `workspace_id` on every insert and refuses
  an update, delete or restore of a row in another workspace.
- **The audit trigger** `crm_audit_row` (on every CRM record and config
  table) writes one `audit_log` row per changed row, in the same transaction,
  with the workspace, actor kind, channel and the before and after values. A
  write with no actor set is refused: "A CRM change must be made through the
  CRM." The logs (`crm_stage_history`, `crm_job_runs`, `crm_notifications`,
  `crm_digest_sends`, the email log, `crm_import_runs`) and the static field
  registry have no audit trigger; the security test names them.
- **Stale edits.** A plan carries the `updated_at` the person saw; the
  function refuses CRM09 "This record changed while you were editing it;
  reload and try again." if the row moved on.
- **Refusals** come back as Postgres error codes and are mapped to sentences
  written in Python (`store.refusal_for`): CRM04 → 404, CRM09 → 409, CRM22 →
  422, a unique violation → 409 with its own sentence. No Postgres message
  reaches a client; anything else is logged and becomes the generic 500.
- **The audit `channel` is a label, never a permission.** `_authenticate`
  sets it to `mcp` only when the in-process MCP bridge sends the per-process
  key (`X-Portal-Channel-Key`, compared with `hmac.compare_digest`), else
  `web`. The proxy never forwards `X-Portal-*` headers. No authorization
  decision reads the channel.

## References stay inside the workspace

Every `crm_*` table that is referenced has `UNIQUE (workspace_id, id)`, and
every reference between `crm_*` tables is a composite foreign key on the pair,
`(workspace_id, company_id) REFERENCES crm_companies (workspace_id, id)`, so
a row in Labs cannot point at a row in Academy whatever the code does. The
references that cannot be foreign keys (the polymorphic `record_type` /
`record_id` of Drive, task, note and email links, favourites and
notifications, and the `channel_ids` option array) carry the
`crm_check_refs()` trigger, which raises CRM04 when the target is missing or
in another workspace. The security test reads `pg_constraint` and `pg_trigger`
to hold every reference to one rule or the other.

## Transition functions and their re-checks

The plan is built in Python, where the rules are tested with fakes; the
function writes it atomically and **trusts none of its ids**:

| Function | Does |
|---|---|
| `crm_deal_transition` | every deal write with side effects: a stage move (history, probability, the company's lead stage, open tasks closed on a loss), the Labs project create / probability sync / confirm (the handoff) / archive and release / reopen |
| `crm_link_project` | links a deal to an existing TVA project (FR-CRM-TVA-18) |
| `crm_merge_companies` | moves people, deals, tasks, notes, links, campaign members and the project link to the survivor; the merged company goes to Trash |
| `crm_purge` | permanent delete, with what hangs off the record |
| `crm_invite`, `crm_remove_membership`, `crm_convert_account` | accounts and memberships, under row locks |

Each locks the rows it changes `WHERE workspace_id = actor.workspace_id`,
works out the deal's project from `projects.source_opportunity_id` (never
from an id in the plan), requires the workspace to be `tva_linked` for any
project action, and re-checks who may do the TVA part (next section). A
caller added later (bulk, import, MCP) therefore cannot get around a rule by
building a different plan.

## Redaction

A Viewer receives no amount anywhere: deal `amount`, `amount_in_inr`,
`weighted_amount`, the FX rates, a campaign's budget and the figures derived
from its deals, and the TVA-derived `revenue`, `suggested_inr` and
`existing_tva_revenue`. The currency code stays.

- `domain/crm/objects.py` marks the amount fields; `services/crm/present.py`
  omits them for a Viewer from records, timelines (an amount change shows as
  a change, without values), search hits, kanban group headers, table
  footers, the bell and the digests.
- A Viewer filter, sort, grouping, total or column on an amount is refused
  422 "Viewers cannot filter, sort or total by amount.", in Python and again
  inside `crm_query` (`p_redact`). A shared view with an amount condition
  opens for a Viewer with the condition removed and a notice.
- Viewers cannot open the dashboard or export at all.
- Nothing is redacted in the MCP layer: the tools return what the routes
  return, so the same rules apply.

`tests/test_crm_redaction_sweep.py` walks every answer a Viewer can get, from
every route and every read tool, with its own written-out list of amount
names, so an omission in production cannot hide itself.

## TVA integration (Labs only)

A Labs deal drives one TVA project through its life; an Academy deal never
touches TVA.

| Deal | TVA project |
|---|---|
| Moves to Proposal | a **tentative** project is created with the deal's probability (a name clash is refused, naming the project, offering rename or link) |
| Probability changes while open | the project's probability follows |
| Closed Won (the handoff) | the project is **confirmed** with its lead and phase dates; revenue and milestones (INR) only when the person is a TVA manager or admin, otherwise a "Confirm revenue and milestones" task for the Labs admins |
| Closed Lost | the project is archived and its allocations **released** (who, when, why) |
| Reopened from Lost | the project is unarchived and exactly those allocations un-released |

TVA writes made by a deal move are done as the **system actor, stamped with
the person** (D8): the TVA audit row reads "*Name* (via CRM deal)". For a
linked project, the TVA Projects page and `update_project` refuse status and
probability, and archiving while tentative: "Managed by CRM deal *name*."
(the deal's name only for Labs members).

**Who may link a project.** Linking a deal to an existing TVA project (a
name clash resolved by link, an imported deal's Link TVA project, the
linkable-projects list) is for staff TVA managers and admins only, checked in
the service and again in `crm_deal_transition` / `crm_link_project` from the
actor's profile. Without that, a Member could adopt someone's tentative
project by naming a deal after it, then archive it and free its people with a
Lost move.

**Readers.** Every TVA reader of allocations (forecast, resources, bench,
over-allocation, hiring signal, utilisation, planned cost and the P&L,
project health, the CEO dashboard, digests, Home, the allocatable picker)
counts only allocations that are not released and not on an archived project,
through `services/supabase.list_counting_allocations`. An archived tentative
project also drops out of the P&L pipeline. This applies to every archived
project, not only CRM ones.

## Capacity

`services/capacity.py` is the one capacity service (FR-CRM-CHG-17): per week,
**supply** (each staff account's contracted hours between joining and leaving,
less the holidays that apply to them and their approved leave) and **demand**
(counting allocations, tentative apart from confirmed). TVA's forecast and the
CRM's capacity check (`GET /crm/capacity`, the Pipeline panel and the
dashboard) both read it. The CRM shows tentative and free hours per week,
and hides a week with fewer than three contributors, so no one person's
availability can be read off it.

## Background jobs and queues

All on the existing Celery worker and beat, except Gmail pages and the AI
jobs ([below](#worker-and-queue)):

| Job | Schedule | Queue |
|---|---|---|
| `crm.purge_trash` (90 days) | 01:10 daily | default |
| `crm.recompute_flags` (follow-up and deal-health flags, stale-deal notices) | every 30 min | default |
| `crm.digest_tick` (daily digest at 09:00 the person's time, weekly admin digest on Mondays, task reminders) | every 15 min | default |
| `crm.email_sync_tick` | every 15 min | default; enqueues pages |
| `crm.email_sync_page` (one page of one mailbox) | enqueued | `crm_sync`, served only by the `worker-crm-sync` service |
| `crm.capture_thread` (file one conversation that became known; spec 010) | enqueued by `crm_ai.capture` and by approving a `new_lead` | `crm_sync`, served only by `worker-crm-sync` |

Every run writes `crm_job_runs`; three failures in a row alert the workspace
admins once. Lateness is checked when read: admins see stale jobs on Today,
and `GET /health/jobs` answers 503 while any job is more than twice its
interval late, for an external monitor. Digests go out exactly once per
person per day (`crm_digest_sends` is claimed before sending); out-of-app
messages use the portal's `notify.deliver` (Slack DM, email fallback).

**Gmail sync is inert until switched on.** The service-account key
(`GOOGLE_SERVICE_ACCOUNT_JSON`) lives on `worker-crm-sync` only; the API,
beat and the shared worker hold just the non-secret `CRM_GMAIL_CONFIGURED`
flag. Only subjects, participants, direction and time are stored, never a
message body. Two readers of a message's text exist, both on `worker-crm-ai`
(below): the summariser and email capture's discovery job. Each holds the
text in memory and stores none of it.

### Email sync and capture

Spec [`010`](../../specs/010-email-capture/spec.md) adds a **capture
source** to each mailbox (`crm_mailboxes.capture_source`): `labels`
(*Labels only*, the default and `007`'s behaviour) or `primary_sent`
(*Primary and Sent*), with a `capture_start_date` (default 1 April 2026).

- **Capture is a wider label set, not a second sync.**
  `domain/crm/capture.py` `scan_labels` returns the configured labels in
  `labels` mode, and adds Gmail's `CATEGORY_PERSONAL` (the Primary tab) and
  `SENT` in `primary_sent` mode. Backfill, history, removal and reconcile
  already work by label, so `email_sync.sync_page` walks the wider set with
  the code `007` built. A message is in scope when it carries one of those
  labels and is in neither Trash nor Spam (`capture.in_scope`); a message
  moved out of Primary leaves scope and its stored copy is removed unless it
  still carries a configured label, and a message put in Trash or Spam leaves
  scope whatever its labels. Updates, Social,
  Promotions and Forums are never read.
- **Backfill window.** `capture.capture_after`: the start date at 00:00 IST
  for `primary_sent`, else `007`'s 90 days. Changing the email, labels,
  capture source or start date resets the cursor. Switching back to *Labels
  only* sets `reconcile_after` to the old start date, so the reconcile
  removes unlabelled captured mail back to that day.
- **Hand labels win.** A message carrying a configured label goes through
  `007`'s path unchanged: stored, linked, automation 2, bulk mail stored with
  `is_auto`. A message reached through Primary or Sent alone gets three new
  rules in `_take`: bulk or automated mail (`List-Unsubscribe`,
  `Precedence`, `Auto-Submitted`, a no-reply sender, a delivery report, a
  calendar invitation; `capture.is_bulk`) is skipped and stores nothing;
  automation 2 does not run on it; and when no outside participant is a
  known person or company domain it is **not stored**.
- **Discovery candidates.** Unknown, unlabelled mail leaves one
  `crm_capture_candidates` row per conversation (mailbox, Gmail thread id,
  latest message id and time; no address, no subject), upserted by
  `crm_capture_mark`, which moves only forward and returns a re-opened
  conversation to `pending`. Known mail is stored and linked exactly as
  labelled mail is, with no review and no Claude call.
- **Deal documents.** At the end of each history pass on a `primary_sent`
  mailbox, `email_sync._deal_documents` runs one Gmail search per document
  kind (`capture.DEAL_DOC_QUERIES`: SOW, proposal, quotation, MSA, purchase
  order; `filename:` queries returning ids only) over Primary, Sent and the
  configured labels since `docs_checked_at` (at most 14 days back on the
  first search). Each match from a known company with an open deal becomes a
  `deal_signal` Review Queue item: sent by us suggests *Proposal, proposal
  sent on* that date, received suggests *possibly Closed Won*. A search
  error is counted (`deal_doc_failed`) and retried next sync; the page
  itself succeeds.
- Sync counts gain `skipped_bulk`, `candidates` and `deal_signals`. With
  every mailbox on `labels`, as migration 071 seeds, the sync behaves exactly
  as `007` built it.

## The MCP tools

Twenty-one `crm_*` tools (`app/mcp/crm.py`): `crm_workspaces`, `crm_today`,
`crm_search`, `crm_get`, `crm_list`, `crm_create`, `crm_update`,
`crm_move_stage`, `crm_handoff`, `crm_set_next_step`, `crm_add_task`,
`crm_complete_task`, `crm_add_note`, `crm_merge`, `crm_import`,
`crm_campaign_members`, `crm_dashboard`, `crm_run_email_sync`, and spec
009's `crm_review_list`, `crm_review_decide` and `crm_thread_summary`. Each is one
route call. Each description opens with who may call it, in capitals; every
write says CONFIRM FIRST and is annotated as a write (destructive for a
stage move, the handoff and a merge). Every tool takes an optional
`workspace`; the route applies D13. CRM routes are exempt from spec 004's
"one tool per route" rule (FR-CRM-CHG-07); `tests/test_crm_mcp.py` holds the
set to exactly these eighteen.

## The intelligence layer

Spec 009 teaches the CRM to read: labelled email threads and Fireflies
meetings become short summaries and meeting notes, companies fill in a
profile from their own website, Admin-defined AI fields describe companies
and deals, and four follow-up rules point at work that needs doing. One rule
runs through all of it: **AI suggests, a person approves.** Generated output
is kept only in an AI-labelled place (a summary, an enriched value, an AI
field value, a meeting note written by the sync actor) or as a Review Queue
item; nothing generated ever changes a person-entered field, a stage, an
owner, an amount, a next step or a task by itself, and nothing is sent to a
client.

### Worker and queue

```
beat ─▶ default queue ─▶ worker (shared)      crm.recompute_flags: flags (R-2's Stuck in stage,
                                              contact recency) and automations (R-1, R-3, R-4
                                              detection; R-1 enqueues crm_ai.followup_draft with ids)
     └▶ crm-ai queue ─▶ worker-crm-ai          crm_ai.summaries, crm_ai.meetings, crm_ai.enrichment
        (concurrency 1)                       (+ crm_ai.enrichment_batch), crm_ai.ai_fields   every 15 min
                                              crm_ai.review_expiry                             01:30 daily
                                              crm_ai.followup_draft                            enqueued
                                              crm_ai.capture (spec 010)                        hourly at :07
```

- Every model, Fireflies and full-message Gmail call runs on the
  **`worker-crm-ai`** service (`PROCESS_TYPE=worker_crm_ai`, `celery -A
  app.worker worker -Q crm-ai --concurrency=1`). `app/worker.py` routes
  `crm_ai.*` to the `crm-ai` queue in its own block after `007`'s and
  `008`'s. It is the only process holding `ANTHROPIC_API_KEY` and
  `FIREFLIES_API_KEY`; the shared worker runs no AI task, and Gmail sync
  pages stay alone on `worker-crm-sync`, so the sync lock is never held
  during a model call.
- One task at a time is deliberate: budget overshoot is bounded by the one
  call in flight. Enrichment works in batches of at most ten companies that
  chain sixty seconds apart, so summaries and meetings run between them.
- Tasks carry only ids and workspace ids and store no result
  (`ignore_result=True`), so no text reaches the broker or the result backend.
- Each job writes one `crm_job_runs` row per workspace per tick under the
  names `summaries`, `meetings`, `enrichment`, `ai_fields`, `review_expiry`,
  `followup_drafts` and spec 010's `capture`, and appears in Settings → Jobs with the usual
  failure alerts and lateness checks. One failing thread or company is a
  count inside the run (`failed_items`), not a failed run. The `automations`
  job (relabelled "Automations") checks each of its switches separately and
  is `skipped` only when all are off.
- Meetings are one task for both workspaces (one Fireflies poll), still
  recording one run per workspace. Attendee emails are matched before any
  transcript is fetched: no match, or a match in two workspaces, fetches
  nothing and creates nothing (the run counts the second case as skipped).

### The one call wrapper

Every model call of every job goes through `services/crm/ai.py` `call()`;
`services/crm/claude.py` is the raw-HTTP Messages client underneath (no
vendor SDK, timeouts set, one structured JSON log line per call with status,
latency and tokens, never content). In order, `call()`:

1. refuses unless the job's phase is on in the workspace's `ai` setting
   (skipped "Switched off");
2. refuses unless both rate settings are set (skipped "AI rates are not
   set");
3. refuses once month-to-date estimated spend, by Asia/Kolkata calendar
   month, reaches the workspace's budget (skipped "AI budget reached", with
   one bell notice and one message to the workspace's Admins that month);
   a budget of zero, the seed, means nothing runs;
4. builds the prompt from fixed rules, the job's instruction and each input
   as a quoted data block with an instruction never to follow it, phone
   numbers removed and no amounts sent;
5. makes one structured-output call and writes one `crm_ai_usage` row
   (tokens, estimated cost, outcome; no text);
6. validates the answer against the job's strict output model, asking once
   more on an invalid answer; a second invalid answer fails the item with
   one of four fixed reasons;
7. masks every money figure and drops every link except the allowed kinds
   in every string of the answer, so no job can forget to.

Any other exception becomes `SafeError`, a fixed sentence naming the step and
the exception type ("AI call failed: HTTPStatusError"), never `str(exc)`, a
response body or a traceback.

**Cost is workspace administration.** Spend lives only in `crm_ai_usage`,
which only that workspace's Admins read (`GET /crm/ai`, Settings → AI).
Job-run counts hold item counts only, so a TVA admin who sees job-run
metadata never sees cost. The rates are configuration on `worker-crm-ai`
(which estimates) and `api` (whose `GET /crm/ai` reports
`rates_configured`).

### No text at rest

Email bodies, transcripts and web pages are read by a `worker-crm-ai` task
into local variables, sent to the model and dropped. They never reach a
table, Storage, a log line, the queue, the result backend, a job-run record,
an audit row or an error message; only the validated, masked output is
stored. `gmail.message_text()` is the summariser's full-message read, called
only by the summariser (pinned by a test), for only the messages `007` stored
for the thread, with quoted history and signatures stripped before the
prompt. `gmail.thread_text()` is the second, called only from
`services/crm/capture.py` (pinned the same way), for only the messages of a
candidate conversation that survived the rules, the ignore list and the
known check; quoted and forwarded blocks are kept to their first 20 lines.
`services/crm/web_fetch.py` refuses private, loopback and link-local hosts,
connects to the address it checked, follows same-site redirects only
(re-checking each hop) and reads at most five pages of at most 1 MB each.
`tests/test_crm_ai_privacy.py` seeds a unique marker in each kind of text
the jobs must not keep and finds it nowhere afterwards.

### Where the output lives

| Table | Holds |
|---|---|
| `crm_email_summaries`, `crm_email_summary_links` | one latest summary per mailbox and Gmail thread, its watermark, and its links to companies, people and every linked open deal |
| `crm_review_items`, `crm_review_item_deals` | Review Queue items, their owner, the current value of each field they would replace, their dedupe key and status |
| `crm_company_enrichment` | enriched values and status per company, apart from the person-entered fields |
| `crm_ai_fields`, `crm_ai_field_values` | AI field definitions and their values, never on the record tables |
| `crm_ai_usage` | the usage and cost log (not exported) |
| `crm_capture_candidates` (spec 010) | one row per mailbox and Gmail thread awaiting or past discovery: latest message id and time, status, outcome, confidence, classifier version, dry-run flag; a lead's or deal signal's finding only for those two outcomes |
| `crm_capture_dry_runs` (spec 010) | each dry run: started, finished or cancelled, applied, its final counts and the recall check (thread ids and fates, no subject or address) |
| `crm_capture_ignores` (spec 010) | the workspace's ignore list: an address or a domain, who added it, the item it came from |
| `crm_notes` (existing) | Fireflies meeting notes, with a source reference and provenance |
| `crm_companies`, `crm_people` (existing) | `contact_recency`, written by `crm_set_contact_recency` only, so it moves neither `updated_at` nor the audit |

All new tables have RLS on and no browser grants, like every CRM table.
Trash, restore, purge, merge and the workspace export follow the record:
`crm_purge` and `crm_merge_companies` know the new tables.

Every value carries its provenance (source kind, model, time, a link to the
source), and the web app shows an **AI** marker that names it on hover and
focus. Generated text is always rendered as plain text.

### Email capture's discovery (spec 010)

`crm_ai.capture` (`services/crm/capture.py`) asks Claude about the
discovery candidates the sync left. It runs behind the workspace's `capture`
setting (`{"mode": "off" | "dry_run" | "live"}`, seeded off), not behind a
`009` phase: it calls `ai.call(ws, "capture", None, ...)`, so step 1 of the
wrapper is skipped and the rates and budget gates still apply. Before any
Gmail read it skips with a reason when the mode is off, Gmail sync is off,
no active mailbox is on *Primary and Sent*, a dry run is already finished,
or the Gmail key is not set on `worker-crm-ai`.

Per candidate, at most 200 a run, each step only sees what the one before
could not decide:

1. `gmail.thread_meta` (metadata only): messages out of scope are dropped;
   nothing left → `skipped`.
2. Rules: internal-only and bulk messages dropped; nothing left → `skipped`.
3. Ignore list: every outside participant on it by address or domain →
   `ignored`.
4. Known check: a participant is now a person or a company domain →
   `known`, and `crm.capture_thread` is queued on `worker-crm-sync` to file
   the conversation.
5. The gate again, then `gmail.thread_text` for the surviving message ids
   only, and one structured call (`DiscoveryOutput`): *new lead*, *deal
   signal*, *other business* or *not relevant*, with a confidence.
6. `crm_capture_record` writes the decision only if the conversation's
   latest message is still the one judged, so a newer message wins and a
   retry re-judges only `pending` rows.

**Dry run.** Started by `POST /crm/capture/dry-run` (`crm_capture_start_dry_run`):
judges every candidate since the start date, oldest first, writing decisions
and findings but no item or record, chaining itself a minute later (guarded
by the Redis key `crm:capture:next`) while candidates remain. When nothing
is pending and every capturing mailbox has finished its backfill, it
computes the **recall check**: each Sales- or Leads-labelled conversation
since the start date, judged as if it had no label, gets a fate
(`filed_known`, `found`, `judged_otherwise`, `skipped_rules`,
`outside_scope`). Any mode change through `PATCH /crm/settings/capture`
cancels an open dry run (`crm_capture_set_mode`).

**Apply and Live.** `POST /crm/capture/dry-runs/{id}/apply`, within 7 days
of the run finishing, turns its high- and medium-confidence findings into
Review Queue items without a second Claude call and sets the mode to Live
(`crm_capture_apply`). Live is refused until a dry run has been applied. In
Live the job judges newest first and each high- or medium-confidence finding
becomes an item before the decision is recorded; low confidence is recorded
only. A finding's suggested domain and name are checked against existing
companies (exact domain, a sibling domain on another public suffix, `007`'s
name match); a match makes the item *link to the existing company*. A
public webmail address never makes a company domain.

**Retention.** `crm.purge_trash` deletes every decided candidate row older
than 12 months (`crm_capture_purge`).

### The approval path

```
POST /crm/review/{id}/approve ─▶ services/crm/review.approve
   item visible to the caller (its owner, or a workspace Admin)?   else 404
   crm_review_claim: open → approved                               lost race → 409
   read the target now; a field the item would replace has moved?  release; 409 "changed"
   the same service a manual edit uses (deals, records, campaigns),
     with a copied context carrying suggestion_id                  every rule, lock and audit applies
   or 008's create_task with TaskActor(suggestion_id=item id)      for task items
   refused ─▶ crm_review_release(refusal), item open again;  ok ─▶ crm_review_release(result)
```

Items are created by the jobs through `crm_review_upsert` with a dedupe key
(source, source key, type, target): a re-run never makes a second open
item, and a dismissed or expired item is not made again from the same
source. Each item has one owner, decided at creation; Members see their
own, workspace Admins see all, Viewers have no queue, and an item owned by
a Viewer, an inactive person or nobody known goes to the Admins. Staleness
is checked per field, so email sync touching Last inbound does not refuse
an approval. The audit row of an approved change names the approver and
carries the item id in `audit_log.suggestion_id`, which `crm_audit_row`
reads from `crm.actor`. Creating an item sends no notice: Today's **To
review** section and the daily digest carry them. `review_expiry` expires
items untouched for 14 days and, after 90 days, reduces decided items to a
dedupe tombstone with no suggestion text.

Approval is deliberately **not** one transaction: the manual services each
commit their own, and the claim-then-release pair is what stops two
approvals both going through (spec 009 plan CT-1).

**Capture's two item types (spec 010).** Both are owned by the mailbox
owner and keyed on the conversation (`email_thread`,
`gmail:<mailbox_id>:<thread_id>`), with the latest judged message id as the
source version, so an expired item does not return for the same messages.
Each shows its evidence (date range, message count, whether the owner
replied, attachment names, Claude's reason) and never message text.

- **`new_lead`** has no target, or the duplicate company it links to.
  Approving it (`review._new_lead`) creates the company at New through
  `records.create` (or reuses the linked one, one already holding the
  domain, or the one an earlier partial approval made), moves it to the
  suggested lead stage through `records.update`, creates the person, and
  queues `crm.capture_thread` to store the conversation; the company and
  person audit rows carry the item id. A failure after the company exists
  releases the item with `result.company_id`, so a retry reuses it.
- **`deal_signal`** targets a known company; its deal choices are the
  company's open deals. Approving it calls `deals.create` (at Discovery) or
  `deals.move`, so Proposal asks for amount and expected close and, in Labs,
  creates the tentative TVA project; Closed Won in Labs is refused with the
  handoff sentence. A suggested stage at or behind the deal's is refused.
- **Dismissing** a `new_lead` may add the sender's address or, except for a
  public webmail domain, the whole domain to the ignore list
  (`ReviewDismissIn.ignore`; also through `crm_review_decide`). A Member who
  may decide the item may add to the list this way; editing the list
  directly is Admin only.

### Jobs, one line each

| Job | Reads | Writes |
|---|---|---|
| `summaries` (phase 1, Gmail live) | threads whose latest stored qualifying message is newer than the summary's watermark; never the 90-day backfill | the summary, `next_step_change` and `task_from_commitment` items, in `crm_store_summary` |
| `meetings` (phase 2) | Fireflies metadata, then the transcript only after a match in exactly one workspace | a `meeting` note (once per transcript; updated only until a person edits it), `task_from_meeting`, `next_step_change` and `deal_link` items, in `crm_store_meeting_note` |
| `enrichment` (phase 2) | the company's own site, queued on create, on a website change or by **Enrich selected**; at most ten a minute per workspace | enriched values or a failure reason; an `enrichment_conflict` item where a filled field differs |
| `ai_fields` (phase 3) | the record's own fields (no amounts), summaries, meeting notes, enriched values, next-step dates, time in stage; at most once an hour per record | `crm_ai_field_values` |
| `followup_drafts` (phase 3, R-1) | a thread's stored summary and the record's fields, never a body | an R-1 `rule_suggestion` with a draft for the owner to send from their own mail |
| `review_expiry` | open and decided items | expiries and tombstones |
| `capture` (spec 010; its own `capture` mode, not a phase) | pending discovery candidates: a metadata thread read, then the text of only the messages that passed the rules, the ignore list and the known check; hourly, newest first in Live, oldest first in a dry run | decisions in `crm_capture_record`; in Live, `new_lead` and `deal_signal` items; in a dry run, findings and the recall check; queues `crm.capture_thread` for a conversation that became known |
| `crm.capture_thread` (on `worker-crm-sync`, not a `JOBS` entry) | one conversation's metadata, Primary, Sent and configured labels only | the conversation's messages, stored and linked as known mail; a failure logs `crm_capture_thread_failed` |

### Rules as flag extensions

There is no rule engine. The four follow-up rules extend `007`'s flags and
switchable automations: each has a flat switch in the `automations` setting
(`no_reply_draft`, `stuck_notice`, `lead_cold`, `campaign_followthrough`)
and whole-day parameters in `flag_thresholds`, all seeded off, and every
rule also needs phase 3 on.

- **R-1 No reply after outreach** builds on the reply facts of email sync:
  the `automations` job finds the trigger and queues a draft on `crm-ai`.
- **R-2 Stuck in stage** is a deterministic flag computed by the flags job
  from the deal's stage entered time, with a per-stage limit; it counts like
  any flag toward deal and company levels, Follow-ups owed and the
  dashboard while switched on, and is absent while off. Its owner notice is
  sent once per stage entry; a deal that also gets a Stale deal notice at
  the same level the same day gets one message.
- **R-3 Lead gone cold** and **R-4 Campaign follow-through** are detectors in
  `domain/crm/followups.py` that create `rule_suggestion` items; approving
  one goes through the manual path, so the pipeline rules still apply.

Phase 4's **contact recency** is derived, not AI: computed in the flags job
from stored email metadata and meeting notes, filterable in company and
person views.

## Three things that are easy to get wrong

### 1. `crm.actor` is transaction-scoped

The audit trigger reads the actor from `current_setting('crm.actor')`, set
with `set_config(…, true)`, which lasts for the current transaction only.
Each PostgREST call is its own transaction, so setting the actor in one RPC
and writing in the next does not work: the write is refused "A CRM change
must be made through the CRM." Set it inside the function that writes, as
`crm_apply` and every transition function do. A migration that seeds or
fixes CRM rows by hand must set the system actor first in the same
statement batch (026's seeds show how).

### 2. A new function is executable by everyone until revoked

Postgres grants `EXECUTE` on a new function to `PUBLIC`. Every CRM function
therefore ends its migration with `REVOKE ALL ON FUNCTION … FROM PUBLIC,
anon, authenticated` and `GRANT EXECUTE … TO service_role`. Forget it and
anyone holding the public anon key can call the function through PostgREST
(`/rest/v1/rpc/…`), bypassing every Python check. `CREATE OR REPLACE` keeps
the grants of the function it replaces; a new signature does not. The
security test fails on any `crm_%` function `anon` or `authenticated` can
execute.

### 3. Count allocations through the counting reader

`list_allocations` returns every allocation row, released ones and those on
archived projects included; it is for the Admin allocations list, which shows
them marked. Anything that **counts** allocations (capacity, cost, bench,
forecast, health) must use `list_counting_allocations`, or a lost deal's
people stay "busy" and its planned cost stays in the P&L.
`tests/test_allocation_release_readers.py` pins every reader to it.

## Running it locally

[`specs/007-crm/quickstart.md`](../../specs/007-crm/quickstart.md) §1: local
Supabase with `db reset` (the seed adds CRM-only people and memberships), the
API, the worker and beat, and the frontend. The real-database tests run with
`cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q`; the 10,000-row timing
check is `uv run python scripts/simulations/crm_load_10k.py` (quickstart §5).
