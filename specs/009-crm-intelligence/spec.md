# CRM Intelligence Layer

| | |
|---|---|
| **Feature** | `009-crm-intelligence` |
| **Status** | Draft 18 October 2026, revised after review. Owner decisions C1–C6 and O1 and the recommended defaults (Q-01 onward) are recorded in §14. The additions beyond the source (§17.2 A-01 to A-13) were accepted by the owner on 18 October 2026 and are recorded as Q-30 to Q-42 |
| **Source** | [`source.md`](source.md): *TVA CRM: Intelligence Layer (Attio parity, required features only)* v0.1, Navaneeth, 18 October 2026. Where this file and the source disagree, this file wins. |
| **Depends on** | `007-crm` (in production since 15 October 2026, migrations 025–045); `008-tasks` (built first, O1: approved task suggestions create rows in its tasks table); constitution v1.2.0 (the Seller's Journey, workspace isolation) |

The CRM learns to read. Labelled email threads and recorded client meetings
become short summaries on the record, with the asks, promises and next steps
they contain. Companies fill in their own profile from their website. A few
computed fields and four follow-up rules point at the deals and leads that need
attention. None of it changes a record by itself: every suggestion waits in a
Review Queue until a person approves it.

The source asks for this in four phases. This spec keeps the four phases, adds
a step zero that is the owner's (C3), and drops saved views, which `007`
already built (C1).

| Phase | Contains |
|---|---|
| **0** (owner) | Google service-account credential, the Gmail sync worker and switch, the AI worker, the Claude and Fireflies keys (C3, C4, C5). No code. |
| **1** | Claude client, AI budget and usage log, per-phase switches; email thread summaries; Review Queue; Today "To review" |
| **2** | Meeting capture from Fireflies; company enrichment |
| **3** | AI fields; the four follow-up rules as extensions of `007` flags and automations (C2) |
| **4** | Relationship strength, shown as **Contact recency** (Q-21) |

`007` §15 holds, with one amendment (FR-CRMAI-CHG-01): the CRM keeps labelled
mail "as metadata and AI summaries, never bodies". This is still **not a mail
archive**: email and transcript text is read in memory and discarded.

---

## 1. Context

`007` gave Nunnari a CRM that knows *that* people talked (email subjects,
participants, direction, dates) but not *what* was said. Sellers still retype
promises into tasks, copy meeting outcomes into notes by hand, and fill company
profiles from scratch after every list import (CODISSIA, the Australia list).
The source compares this with Attio, which does these things for a per-seat
price and keeps the text. This layer does the parts that change daily selling,
inside the portal, on Nunnari's own Claude key, without storing bodies.

Four facts about the system as built shape everything below:

1. **Gmail sync is off in production.** The Google service-account credential
   was never set up, the dedicated `worker-crm-sync` service does not exist and
   `CRM_GMAIL_CONFIGURED` is false. No email is stored, Today's reply and
   silence sections say "Email sync is off", and any email feature here has no
   input until the owner finishes step zero (C3, §17).
2. **Bodies have never been fetched.** `007` Q-29, FR-CRM-SYNC-06 and
   NFR-CRM-02 say bodies are not *stored*. The promise that a body is never
   *fetched* lives in the Gmail client's docstring
   (`backend/app/services/crm/gmail.py`: "no message body is ever fetched
   (Q-29)") and is pinned by `test_only_metadata_is_asked_for` in
   `backend/tests/test_crm_gmail_token.py`. A summary needs the text. This spec
   changes "never fetched" to "fetched in memory by the summariser only, never
   stored" (FR-CRMAI-CHG-01).
3. **Much of the source already exists.** Saved views (FR-CRM-VIEW-05), the
   job log (FR-CRM-JOB), workspace automation switches (FR-CRM-AUTO-02), the
   Stale deal, Client silent and Loose end flags (FR-CRM-FLAG), the meeting
   note kind (FR-CRM-NOTE-03) and most company profile fields (industry, size,
   city, country, website, LinkedIn, description) are built. This spec extends
   them and does not build parallel versions.
4. **Tasks are changing.** `008-tasks` replaces the CRM task model with one
   task system. Suggestions approved here create tasks there (O1), so `008`
   ships first.

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — A labelled client thread shows a plain summary, the client's asks
  and Nunnari's commitments on the company, person and deal, within one sync
  cycle of the message arriving.
- **G-2** — A promise with a date in an email, or an action item in a client
  meeting, becomes a task only after one tap of approval, never silently and
  never lost.
- **G-3** — A client call recorded in Fireflies lands on the right company and
  deal as a meeting note within 30 minutes, without anyone typing it.
- **G-4** — Imported companies fill in their own profile in the background, and
  nothing a person typed is ever overwritten.
- **G-5** — Admins know what AI costs per workspace this month and can cap it.
  When the cap is reached, AI work stops visibly, not silently.
- **G-6** — Everything a seller can do here (read summaries, review and decide
  suggestions) can be done through Claude over NLDW, as themselves.

### 2.2 Non-goals

- **AI that acts.** No AI output changes a deal stage, a lead stage, an owner,
  an amount, a next step or a task, and nothing sends a message to a client.
- **A mail or transcript archive.** Text is processed in memory and dropped.
- **A rule builder.** Four built-in rules, each an extension of what `007`
  already flags (C2). No custom rules.
- **A chat assistant inside the CRM.** Claude over NLDW covers this.
- **Saved views.** Built in `007` (FR-CRM-VIEW-05, migration 034). Dropped (C1).
- **Lead scoring.** `007` §13 keeps it out of scope. "ICP fit" here is a
  read-only suggestion with no effect on any stage, probability or rank. A
  number-type AI field is shown on the record page only and is never sorted,
  filtered or ranked (Q-27).

---

## 3. Principles

These apply to every requirement below. They restate source §2 as amended by
the decisions.

| ID | Principle |
|---|---|
| P-1 | **AI suggests, a person approves.** AI output is stored only in an AI-labelled place (a summary, an enriched value, an AI field) or as a Review Queue item. |
| P-2 | **Provenance on every AI value**: source kind (email thread, meeting, enrichment, AI field, rule), model, generated at, and a link to the source record. The UI shows an "AI" marker; hovering or focusing it shows the source. |
| P-3 | **No raw text at rest.** Email bodies, transcripts and web pages are read in memory by a worker and discarded. They never reach the database, Storage, logs, the Celery queue or result backend, a job-run record or an error message. Only generated output is stored. |
| P-4 | **One server-side Claude client**, Nunnari's own key, with a monthly budget per workspace (C4). No key in the browser. |
| P-5 | **Idempotent jobs**: re-running on the same input changes nothing. Keys: (mailbox, Gmail thread id) for summaries, Fireflies transcript id for meetings, company id plus website for enrichment (Q-33), record plus rule plus trigger for rule suggestions. |
| P-6 | **Workspace isolation** (`007` FR-CRM-WS-02). A prompt contains only records and source content belonging to the workspace being processed. |
| P-7 | **Reachable through NLDW**, within the web-only limits of `007` FR-CRM-MCP-10 (settings stay web-only). |

---

## 4. User journeys

### 4.1 Reading a thread (P1, phase 1)

Tamil opens a Labs deal. In the timeline, the thread "Re: Factory Brain pilot
scope" has a small AI marker. Expanded, it reads three plain sentences, the
client's two asks, and Nunnari's promise: "Send the revised SOW by 24 Oct
(Tamil)". He did not type any of it, and the email itself is not stored.

### 4.2 The review (P1, phase 1)

Tamil's Today has a sixth section, **To review (3)**. One item suggests
changing the deal's next step from "Follow up on pilot" to "Send revised SOW"
due 24 October; one suggests a task for the SOW promise. He edits the task
title and approves both. The deal's next step and the new task are saved
through the same rules as if he had typed them, with the audit showing his
name and "approved AI suggestion". He dismisses the third.

### 4.3 From Claude (P1, phase 1)

Navaneeth asks Claude "what's waiting for me to review in Labs?". Claude calls
`crm_review_list`, reads out four items, and on "approve the first two" says
exactly what will change and calls `crm_review_decide` twice after his yes.

### 4.4 The meeting (P2, phase 2)

A client call is recorded in Fireflies at 15:00. By 15:30 the company has a
meeting note with the attendees, a five-line summary, decisions, action items
and a link to the Fireflies transcript. The company has two open deals, so a
"which deal is this?" item waits in the queue, alongside two action-item task
suggestions. An internal stand-up recorded the same day never enters the CRM,
and its transcript is never downloaded.

### 4.5 The import (P2, phase 2)

Sanjeev imports 400 colleges by CSV. The import does no enrichment work of its
own. Over the next hour, colleges with a website gain an AI-marked industry,
size band, city, one-line description and LinkedIn link beside their own
fields. Where a person had already filled a field, the AI value waits beside
it; one click promotes it.

### 4.6 The cost check (P1, phase 1)

Navaneeth opens Settings, AI in Labs: month-to-date estimated spend against the
monthly budget, by job. When Academy reaches its budget, its AI jobs stop and
Academy Admins see a banner and a bell notice. Sync keeps running; only
summaries wait.

### 4.7 The nudge (P3, phase 3)

A Labs deal has sat in Proposal for 15 days. It turns amber "Stuck in stage"
and its owner gets one notice. A lead in Contacted with no activity for 30
days gets a suggestion to move it to Nurturing or Disqualified. A client who
has not answered an outreach email for five days gets a suggested follow-up
draft that the owner edits and sends from Gmail themselves.

---

## 5. Functional requirements — foundations (phase 1)

### 5.1 Claude client and budget — FR-CRMAI-LLM

| ID | Requirement |
|---|---|
| FR-CRMAI-LLM-01 | All model calls MUST go through one backend client, called only from tasks on the `crm-ai` queue (FR-CRMAI-JOB-04), never from a web request handler, a Gmail sync page task, the shared worker or the browser. The key is a Railway secret held only by the `worker-crm-ai` service (C4, Q-05). |
| FR-CRMAI-LLM-02 | Summaries, meeting notes, enrichment, AI fields and follow-up drafts use **Claude Sonnet, model id `claude-sonnet-5-5`** (C4). The model id is stored with every output (P-2). |
| FR-CRMAI-LLM-03 | Every call asks for structured output, which the backend MUST validate against a fixed schema before anything is saved. Output that fails validation saves nothing and records the item as failed with one reason from a fixed list ("Output did not match the schema", "Model call failed", "Model call timed out", "AI budget reached"); the reason never carries model output or request text. Output that fails validation is retried once; only a second failure records the item as failed (Q-36). |
| FR-CRMAI-LLM-04 | Prompts carry only the fields a task needs. Phone numbers are removed from every prompt. Text from emails, transcripts, web pages and notes is placed in the prompt as quoted data with an instruction never to follow it (`004` FR-MCP-05 applied to model input). The output schema refuses URLs except the Fireflies transcript link and an enriched LinkedIn or same-site website. All AI text is rendered as plain text, never as markdown or HTML (Q-37). |
| FR-CRMAI-LLM-05 | Generated text MUST NOT contain money amounts. The schema instruction says so, and a server check masks any currency figure that still appears before save, so AI output can be shown to Viewers without breaking `007` FR-CRM-ACC-11 (Q-08). The check applies to **every stored generated string**: summary, asks, commitments, suggested next step, meeting note body, review item suggestion and current-value text (including task titles from commitments and action items), follow-up drafts, enriched values and AI field values and reasons. It masks: currency symbols (₹, $, A$, US$, €, £) next to a number; ISO codes and words (INR, AUD, USD, Rs, rupees, dollars) next to a number; Indian units (L, lakh, lakhs, Cr, crore, crores) after a number; k, K, M, mn, bn suffixes on a number next to a currency word or symbol; and the word "budget", "price", "cost", "fee" or "quote" followed within three words by a number. |
| FR-CRMAI-LLM-06 | Every call is logged to an AI usage log: workspace, job, model, input and output tokens, estimated cost, outcome, time. The log holds no prompt or output text. |
| FR-CRMAI-LLM-07 | Each workspace has a **monthly AI budget** set by its Admins in Settings (C4), held as an **estimated spend in USD** (the currency the rate table is in, Q-23). The month runs on Asia/Kolkata calendar months. Before each call the job checks month-to-date estimated spend; at or over the budget the call is not made. Because `worker-crm-ai` runs one task at a time, overshoot is limited to the one call in flight when the budget is crossed. Until an Admin sets a budget it is zero, so no AI work runs (Q-02). Until both rates of the rate table (§17.1) are configured no call is made either: the run is recorded `skipped` with the reason "AI rates are not set", and the AI settings read reports that the rates are not set (D-1). |
| FR-CRMAI-LLM-08 | When a budget is reached: the job run is recorded `skipped` with the reason "AI budget reached" (`007` FR-CRM-JOB-01 has no "paused" outcome, Q-03); the workspace's Admins get one bell notice and one message per month; Settings, AI and Settings, Jobs show a banner. Skipped work is picked up by the next periodic tick after the budget is raised or the month turns, because every job works from what is missing: for summaries, a thread whose latest stored qualifying message is newer than its summary's watermark (FR-CRMAI-SUM-02). Nothing fails silently. |
| FR-CRMAI-LLM-09 | **Settings, AI** (workspace Admins, web only): monthly budget, month-to-date spend in total and per job, the per-phase switches (FR-CRMAI-FLAG-01). Spend is an estimate from token counts and a rate table (§17.1); the page says "estimate". |
| FR-CRMAI-LLM-10 | Cost figures are workspace administration, shown to that workspace's Admins only, never on a TVA page and never in `/help` (NFR-CRMAI-07). AI job runs in `crm_job_runs.counts` carry item counts only (threads summarised, items created, skipped); tokens and cost go only to the AI usage log, which only that workspace's Admins can read. TVA admins who see job-run metadata (`007` FR-CRM-JOB-01, FR-CRM-ACC-13) therefore never see cost. |

### 5.2 Per-phase switches — FR-CRMAI-FLAG

| ID | Requirement |
|---|---|
| FR-CRMAI-FLAG-01 | Each workspace has one on/off switch per phase (1 summaries; 2 meetings and enrichment; 3 AI fields and follow-up rules; 4 contact recency), stored in the existing `crm_workspace_settings` table (C6) under a **new key `ai`**: `{"budget_usd": <number>, "phases": {"1": false, "2": false, "3": false, "4": false}}`, with its own validator in the settings `VALIDATORS` (unknown or missing keys refused, as today). The phase switches are not put in `automations`, so phase gates and automation switches stay separate (Q-22). |
| FR-CRMAI-FLAG-02 | **Seeding.** The migration that adds the `ai` key INSERTs an `ai` row in every existing workspace with every phase off and a zero budget, widens the key CHECK of `crm_workspace_settings` (migration 026), and seeds new workspaces the same way. Off, because `automation_on` reads a missing key as on (Q-04). |
| FR-CRMAI-FLAG-03 | Admins switch phases in Settings, AI. Switching a phase off stops its jobs at the next run (recorded `skipped`, "Switched off") and hides that phase's own UI (summaries, meeting capture, enrichment, AI fields, contact recency); stored output stays and returns when switched on. |
| FR-CRMAI-FLAG-04 | **The Review Queue and Today's To review section are shown while any phase is on, or while the person has open items** (Q-24). Items of a switched-off phase stay visible and decidable until they expire. Phases are independent: phase 2, and the R-2, R-3 and R-4 rules of phase 3, may run before step zero, because they do not read Gmail. R-1 needs phase 1 on and Gmail sync live, because it drafts from stored summaries (FR-CRMAI-RULE-02). |
| FR-CRMAI-FLAG-05 | Rollout: Labs first; Academy at least one week later, after the owner has reviewed Labs output (source §3, §14). This is an operating rule, not a code check. |

### 5.3 Jobs — FR-CRMAI-JOB

| ID | Requirement |
|---|---|
| FR-CRMAI-JOB-01 | AI work is recorded in the existing CRM job log per workspace and appears in Settings, Jobs, with the same failure alerts and staleness checks (`007` FR-CRM-JOB-01 to -03). No parallel job mechanism. Because the job log is per interval, event-driven work is grouped into **periodic job names**, each added to `JOBS` with its interval, the `crm_job_runs` job CHECK and `crm_job_latest`: `summaries` (every 15 minutes: queue every thread whose summary is missing or older than its latest qualifying message), `meetings` (every 15 minutes, the Fireflies poll), `enrichment` (every 15 minutes: process queued companies within the rate limit), `ai_fields` (every 15 minutes: recompute fields whose inputs changed), `review_expiry` (daily), `followup_drafts` (each `automations` tick: the R-1 drafts when there is R-1 work, so a failed or skipped draft is counted, D-4; a tick with none records an `ok` or `skipped` run itself, so the staleness check holds). One run per workspace per tick; a single failing thread or company is counted inside the run, not logged as a failed run. *(Amended in S5.)* A task that runs every workspace in turn shares 400 seconds between them, inside Celery's 600-second limit: each workspace's run takes no more items once its share is spent, and `meetings` fetches at most 20 meetings per workspace per tick; the rest wait for the next tick. A run the budget or a switch stops part-way records the counts it had beside the reason. A follow-up draft whose save fails is counted as one failed draft. |
| FR-CRMAI-JOB-02 | Job tasks put only ids on the Celery queue, never text (P-3), as the existing notification tasks do. AI tasks store no result (`ignore_result`), so nothing reaches the Celery result backend. |
| FR-CRMAI-JOB-03 | Before an exception reaches `jobs.failure()`, `logger.exception` or any log line, AI jobs replace it with a fixed message naming the exception type and the step ("Claude call failed: HTTPStatusError", "Schema validation failed"), never `str(exc)`, a response body, model output or a traceback that may carry request content. |
| FR-CRMAI-JOB-04 | **Queues and workers** (Q-05). All AI tasks (summaries, meeting capture, enrichment, AI fields, R-1 drafts) run on a new `crm-ai` queue served by a new **`worker-crm-ai`** service with concurrency 1. Gmail sync pages stay alone on `worker-crm-sync`, so the sync lock (`SET NX EX 900`) is never held while a model call runs and sync keeps its 15-minute cadence. The shared worker, which sends leave notifications, runs no AI task: its beat ticks only enqueue ids onto `crm-ai`. |
| FR-CRMAI-JOB-05 | **The follow-up rules** (FR-CRMAI-RULE-01). The existing `automations` job checks each switch separately: the stale-deal notice, R-1, R-2's notice, R-3 and R-4 each run behind their own switch, and the run is recorded `skipped` only when every one of them is off. Its label in `JOBS` changes from "Stale-deal notices" to "Automations". R-2's flag is computed in the existing flags job. |

---

## 6. Functional requirements — phase 1

### 6.1 Email thread summaries — FR-CRMAI-SUM

| ID | Requirement |
|---|---|
| FR-CRMAI-SUM-01 | **Which threads.** A thread is summarised when it holds at least one stored message that is not internal-only and not an auto-reply, linked to a company or person in the workspace (`007` FR-CRM-SYNC-05). A thread is identified by **mailbox and Gmail thread id**, because Gmail thread ids belong to one mailbox; the source's "thread id unique per workspace" is replaced (Q-06). |
| FR-CRMAI-SUM-02 | **When.** A thread is (re)summarised by the next `summaries` tick after a sync run stores a new qualifying message on it. Each summary records a watermark (the Gmail id and time of the latest message it covered); a thread whose latest stored qualifying message is newer than the watermark is pending. Only the latest summary per thread is kept, with its updated time. A thread whose label is removed loses its summary on the same run that deletes its messages (`007` FR-CRM-SYNC-09). |
| FR-CRMAI-SUM-03 | **Reading the text.** The `worker-crm-ai` worker fetches, with the existing read-only scope, **only the messages that `007` stored for the thread** (not internal-only, not auto-reply or bounce), never the others in the Gmail thread. Before the prompt is built, quoted reply history and signatures are stripped from each message. The text goes to Claude and is discarded. This amends `007` Q-29 (FR-CRMAI-CHG-01). |
| FR-CRMAI-SUM-04 | **Output**, validated before save: summary (2 to 3 plain sentences); asks (what the other side asked for, may be empty); commitments (what Nunnari promised, each with owner and date when stated); suggested next step and date (only when the thread implies one); sentiment (positive, neutral, concern). Each ask and commitment is a paraphrase of at most 200 characters, not a quotation. No body, quote block or signature is stored. |
| FR-CRMAI-SUM-05 | **Links.** A summary links to the thread's companies, people and every open deal its messages are linked to. A message can link to several open deals of one company (`007` email rules), so a summary is not restricted to one deal (Q-07). |
| FR-CRMAI-SUM-06 | **Next step suggestion.** When the summary has a suggested next step and the thread links to an open deal whose next step or date differs, a `next_step_change` Review Queue item is created. With several open deals, the one item carries a deal picker and the approver chooses the deal. The deal is never changed directly. |
| FR-CRMAI-SUM-07 | **Commitment suggestion.** Each commitment with a stated date creates a `task_from_commitment` Review Queue item. Commitments without a date are shown in the summary only. A re-summary does not repeat an item for a commitment already suggested on that thread. |
| FR-CRMAI-SUM-08 | **Where shown.** On company, person and deal timelines and email panels, each thread row expands to show summary, asks, commitments and sentiment, with the AI marker and its provenance, rendered as plain text (FR-CRMAI-LLM-04). Visible to every member of the workspace (`007` FR-CRM-SYNC-10), Viewers included. |
| FR-CRMAI-SUM-09 | **Idempotent.** Re-running on a thread with no new message makes no model call and changes nothing. |
| FR-CRMAI-SUM-10 | **Mailbox owner left.** A paused mailbox (`007` migration 040) is not summarised; its existing summaries stay until the messages are removed. |
| FR-CRMAI-SUM-11 | **Backfill.** The 90-day backfill of a newly connected mailbox (`007` FR-CRM-SYNC-08) is not summarised. A mailbox's summarising starts from the first message stored after its backfill completes, so connecting a mailbox causes no burst of calls against the budget (Q-25). |
| FR-CRMAI-SUM-12 | **Owners.** The model is given the workspace's Admins and Members (name and email) and returns a commitment's or action item's owner only as one of them or "unknown". The server accepts an owner only as a member id of the item's workspace; anything else, including a name matching nobody or several people, is stored as "unknown" and routed as FR-CRMAI-REV-02 says. The same rule applies to meeting action items (FR-CRMAI-MEET-05). |

### 6.2 Review Queue — FR-CRMAI-REV

| Item type | Created by | Approving does |
|---|---|---|
| `next_step_change` | Summary (§6.1), meeting (§7.1) | Sets the deal's next step and date, with a deal picker when several open deals are linked |
| `task_from_commitment` | Summary (§6.1) | Creates a task in `008`'s tasks table, source `email_commitment` |
| `task_from_meeting` | Meeting (§7.1) | Creates a task in `008`'s tasks table, source `meeting_action` |
| `deal_link` | Meeting (§7.1) | Links the meeting note to the chosen deal |
| `enrichment_conflict` | Enrichment (§7.2) | Replaces the person-entered field with the enriched value |
| `rule_suggestion` | Follow-up rules (§8.2) | Applies the suggested change (stage move, task, close-out), or for a follow-up draft, marks it used |

| ID | Requirement |
|---|---|
| FR-CRMAI-REV-01 | Each item shows the suggestion, the current value it would replace, the source (thread, meeting, enrichment, rule, with a link) and three actions: **Approve**, **Edit and approve**, **Dismiss**. |
| FR-CRMAI-REV-02 | **Who sees which item.** An item belongs to one person: the deal owner (`next_step_change`, deal-level `rule_suggestion`); the commitment or action-item owner (FR-CRMAI-SUM-12) when they are an Admin or Member of the workspace, else the mailbox owner or the deal or company owner (`task_from_*`); the company owner (`deal_link`, `enrichment_conflict`, company-level `rule_suggestion`). When that person is a Viewer, inactive, not a member or "unknown", the item goes to the workspace's Admins. |
| FR-CRMAI-REV-03 | **Admin view.** A workspace Admin (the CRM role, not the TVA role, `007` FR-CRM-ACC-02) sees and may decide every item in their workspace. Members see and decide their own items. Viewers have no queue. |
| FR-CRMAI-REV-04 | **Same path as a manual edit.** Approving MUST apply the change through the same backend service a person's edit uses, so every validation, permission check, pipeline rule (`007` §5.12), audit entry and optimistic lock applies. **Staleness is checked per field, not per record**: the item records, at creation, the current value of each field it would replace (next step and date; the enriched field; the lead stage; the member status). If any of those values differs at approval, approval is refused ("This record changed after the suggestion was made.") and the person may Edit and approve against the current value. Changes to other fields (for example Last inbound or Last contact written by email sync) do not refuse it. `task_from_*` items and other items that only create records have no staleness check. Approving needs the same permission as making the change by hand. |
| FR-CRMAI-REV-05 | The audit entry of an approved change names the approving person as actor and carries the item id in a new nullable `suggestion_id` column of `audit_log`, which `crm_audit_row` fills from the `crm.actor` session setting set by the approval path; the UI shows "approved AI suggestion" when it is present (`007` FR-CRM-AUD-02). A task created by approval (FR-CRMAI-REV-06) carries the item id on its own audit rows the same way, through the `suggestion_id` of `008`'s task actor. |
| FR-CRMAI-REV-06 | **Tasks.** An approved `task_from_*` item creates a task through `008`'s create path, with source and source reference (mailbox and thread, or Fireflies transcript id), linked to the item's company, person or deal, in the item's workspace. The assignee is the commitment or action-item owner when `008` allows them, else the approver. `008`'s rules on links, assignees and visibility apply unchanged; where `008` refuses, the item stays open and shows the refusal. *(Amended in S5: when a newer item of the same dedupe key opened while the approval ran, the refused item expires instead, keeping its refusal, and the newer item stays open, so there is never a second open item of one key, FR-CRMAI-REV-08.)* |
| FR-CRMAI-REV-07 | **Expiry.** An item untouched for 14 days expires, and the expiry is logged (the `review_expiry` job run and the audit). Every decided or expired item (approved, dismissed, expired) stays readable in the Admin view for 90 days, then is reduced to a dedupe tombstone with no suggestion text, which also removes stored R-1 draft text (Q-34). |
| FR-CRMAI-REV-08 | **No duplicates.** An item has a dedupe key (source, source key, type, target). A job re-run never creates a second open item with the same key, and a dismissed or expired item (expiry counts as a dismissal) is not re-created from the same source unless the source changes (a new message, a new transcript, a new trigger). The dedupe tombstone of FR-CRMAI-REV-07 keeps the dedupe key, so this holds after the 90 days too (Q-34). |
| FR-CRMAI-REV-09 | **Today.** Today gains a sixth section, **To review**, after the five of `007` FR-CRM-TODAY-01: the count of the person's open items and the three oldest items, each opening the queue. The daily digest (`007` FR-CRM-NOTIF-03) carries the same section (Q-35). This amends FR-CRM-TODAY-01 (FR-CRMAI-CHG-03). Because `008`'s morning brief reads Today's sections from the Today service (`008` FR-TASK-BRIEF-01), To review appears in the brief too. |
| FR-CRMAI-REV-10 | No item sends a bell or out-of-app message on creation; Today and the daily digest carry them (Q-09, Q-35). |

---

## 7. Functional requirements — phase 2

### 7.1 Meeting capture from Fireflies — FR-CRMAI-MEET

| ID | Requirement |
|---|---|
| FR-CRMAI-MEET-01 | The `meetings` job polls Fireflies **every 15 minutes** for new transcripts: beat on the shared worker enqueues the tick, and the poll runs on `worker-crm-ai`, the only service holding the Fireflies API key as a Railway secret (C5). No webhook, so no new public route. |
| FR-CRMAI-MEET-02 | **Matching, before any text.** The poll reads attendee metadata only (emails, title, date). Attendee emails are matched to people in each workspace (primary or additional email). Attendees in the workspace's own domains are internal. A meeting with no external attendee matched to a CRM person in that workspace is ignored there: internal meetings never enter the CRM, and unknown external attendees create no records (unlike `007` automation 2, Q-10). Transcript content is fetched **only after** a match in exactly one workspace whose phase 2 is on. |
| FR-CRMAI-MEET-03 | **Several workspaces.** A transcript that matches people in more than one workspace is not processed: nothing is created, its content is never fetched, and each matched workspace's `meetings` run counts it as skipped (a count, no title or names) (Q-26). |
| FR-CRMAI-MEET-04 | **Deal.** The matched people's companies are resolved. If exactly one of them has exactly one open deal, the note links to it. Otherwise the note links to the companies and people only, and a `deal_link` item asks which deal. |
| FR-CRMAI-MEET-05 | **The note.** A note of the existing kind `meeting` (`007` FR-CRM-NOTE-03), dated the meeting day, with a body holding: title, attendees, summary (at most 5 lines), decisions, action items (owner per FR-CRMAI-SUM-12, due date if stated) and a link to the Fireflies transcript. The transcript is never copied. The structure lives in the note body; no new note columns beyond the source reference and provenance (Q-11). The note is written with the actor kind `sync`. |
| FR-CRMAI-MEET-06 | Each action item creates a `task_from_meeting` item; a changed next step on the linked deal creates a `next_step_change` item. |
| FR-CRMAI-MEET-07 | **Idempotent** on transcript id and workspace: a re-poll never creates a second note. An updated transcript updates the AI-written note only while no person has edited it; once a person edits the note, AI updates stop (Q-41). |
| FR-CRMAI-MEET-08 | Meeting notes are visible to every member of the workspace, as every note is. |

### 7.2 Company enrichment — FR-CRMAI-ENR

| Enriched value | Promotes to |
|---|---|
| Industry | Industry (select; must match an existing option of the workspace list) |
| Sub-industry | Nothing; shown as enriched only |
| Employee band | Size (select; mapped to the existing bands) |
| HQ city, country | City, Country |
| One-line description | Description |
| LinkedIn URL | LinkedIn |
| Products or services (short list) | Nothing; shown as enriched only |

| ID | Requirement |
|---|---|
| FR-CRMAI-ENR-01 | **Triggers.** Creating a company with a website or domain, by the web app, NLDW, CSV import, the Twenty importer or email sync (`007` automation 2, FR-CRM-SYNC-05, which always sets a domain), queues enrichment behind the same rate limit. An Admin can run **Enrich selected** as a bulk action on a company view (web only, `007` FR-CRM-MCP-10). Changing a company's website queues enrichment again (Q-32). |
| FR-CRMAI-ENR-02 | **Reading.** The worker reads the company's own website and public pages only, in memory. Page text is discarded after the call (P-3). The worker refuses hosts that resolve to private, loopback or link-local addresses, connects to the IP address it checked (no second lookup, against DNS rebinding), and follows redirects only within the same site, re-checking each hop (Q-30). It reads at most 5 pages per company and 1 MB per page, with a 10-second timeout per request (Q-31). |
| FR-CRMAI-ENR-03 | **Storing.** Values go into enriched values kept apart from the person-entered fields, in their own table (not on `crm_companies`), each with provenance. Enrichment never writes a person-entered field. |
| FR-CRMAI-ENR-04 | **Promote.** On the company page an Admin or Member can promote one enriched value to its main field with one click. Promotion is an ordinary audited edit by that person. An enriched industry with no matching option says so and offers nothing until an Admin adds the option (select lists stay Admin settings). |
| FR-CRMAI-ENR-05 | **Conflicts.** When the main field is already filled with a different value, an `enrichment_conflict` item is created instead of a promote button. |
| FR-CRMAI-ENR-06 | **Failure.** If the site cannot be read or gives nothing usable, the enrichment record holds status failed with a reason from a fixed list ("Website did not respond", "No company information found"). Nothing is guessed. Enrichment status lives in the enrichment table, never on `crm_companies`, so it neither moves the company's updated time nor writes an audit row. |
| FR-CRMAI-ENR-07 | **Bulk.** Enrichment is rate-limited to 10 companies a minute per workspace, resumable, and never runs inside an import or a web request: the import request and its transaction do no enrichment work beyond recording that companies are queued, and enrichment follows in the background on `worker-crm-ai`. |
| FR-CRMAI-ENR-08 | **Idempotent** on company id plus website: a company already enriched from the same website is not re-read unless an Admin asks again or the website changes (FR-CRMAI-ENR-01). Two companies that share a domain are enriched separately (Q-33). |

---

## 8. Functional requirements — phase 3

### 8.1 AI fields — FR-CRMAI-FIELD

| Default field | Record | Output | Seeded in |
|---|---|---|---|
| ICP fit | Company | Select: Factory Brain, Academy, Services, DeepWeaver, None | Labs only (Q-12) |
| Account brief | Company | Text, 3 lines | Both |
| Deal outlook | Opportunity | Select: On track, At risk, Stalled, plus a one-line reason | Both |
| What is blocking | Opportunity | Text, 1 line, only when Deal outlook is At risk or Stalled | Both |

| ID | Requirement |
|---|---|
| FR-CRMAI-FIELD-01 | An AI field is defined per workspace by an Admin in Settings (web only): name, record type (company or opportunity), prompt, output type (text, single select with its options, number 0–100), enabled. The four defaults above are seeded; Admins may edit or disable them. A number output is shown on the record page only, never sorted, filtered or ranked (Q-27). |
| FR-CRMAI-FIELD-02 | The source's "Deal health" is renamed **Deal outlook**, because "deal health" already means a deal's worst flag (`007` FR-CRM-FLAG-03, -05) and the dashboard's colours (Q-13). |
| FR-CRMAI-FIELD-03 | Inputs are the record's own fields, its summaries, meeting notes, enriched values, next step dates and time in stage, from the same workspace only. No amounts are sent to the model (FR-CRMAI-LLM-05). |
| FR-CRMAI-FIELD-04 | Recomputed when an input changes (a new summary, a new meeting note, enrichment) by the next `ai_fields` tick, and on demand by an Admin or Member from the record page. A record's AI fields are recomputed at most once per hour; changes inside the hour get one trailing recompute at the end of it (Q-38). |
| FR-CRMAI-FIELD-05 | AI fields are read-only to people, carry the AI marker, are shown **on record pages only** (not as view columns or filters, Q-28), and never feed a stage, probability, flag, colour or automation. Values are stored in their own table, never on the record tables. The dashboard keeps its own colours; it may show Deal outlook as an extra column. |
| FR-CRMAI-FIELD-06 | Viewers see AI fields (they hold no amounts, FR-CRMAI-LLM-05). |

### 8.2 Follow-up rules — FR-CRMAI-RULE

The source proposed a small rule engine. Under C2 there is none: each rule is
an extension of `007`'s flags (FR-CRM-FLAG) and switchable automations
(FR-CRM-AUTO-02). What is new is listed per rule.

| Rule | Builds on (`007`) | New |
|---|---|---|
| R-1 No reply after outreach | Client silent flag; reply facts from email sync | An AI-drafted follow-up for the owner, as a Review Queue item |
| R-2 Stuck in stage | Stale deal flag; stage entered time and stage history | Per-stage limits and a new Stuck in stage flag |
| R-3 Lead gone cold | Loose end flag | A suggestion to move the lead stage |
| R-4 Campaign follow-through | Campaign members; automation 8 | A suggestion to qualify or close out |

| ID | Requirement |
|---|---|
| FR-CRMAI-RULE-01 | **Switches and parameters** (Q-22). Each rule has its own switch in the existing `automations` setting under the flat keys `no_reply_draft` (R-1), `stuck_notice` (R-2), `lead_cold` (R-3) and `campaign_followthrough` (R-4). Parameters go in the existing `flag_thresholds` setting as flat whole-number keys, because `flags.thresholds()` ignores any non-integer value: `stuck_discovery_days` (14), `stuck_qualification_days` (21), `stuck_proposal_days` (14), `stuck_negotiation_days` (21), `no_reply_days` (5), `lead_cold_days` (30). Both settings stay validated as today (unknown or missing keys refused), with the validators widened to the new keys. The migration **UPDATEs every existing workspace row** by merging the new keys into the stored value (`value \|\| '{"no_reply_draft": false, "stuck_notice": false, "lead_cold": false, "campaign_followthrough": false}'` for `automations`, and the six defaults for `flag_thresholds`), not only the seeds, because the seeds use `ON CONFLICT DO NOTHING` and `automation_on` reads a missing key as on. Rules run only when phase 3 is on, in the jobs FR-CRMAI-JOB-05 names. |
| FR-CRMAI-RULE-02 | **R-1 No reply after outreach.** Needs phase 1 on and Gmail sync live. A person or company (not only an open deal, unlike Client silent) whose latest non-internal, non-auto email is outbound and older than `no_reply_days` with no later inbound is a trigger. The `automations` job only detects triggers and queues a one-item draft task (ids only) on `crm-ai`; the draft is written from the thread's stored summary and the record's fields, never from a message body, as plain text under the prompt and URL rules of FR-CRMAI-LLM-04. The dedupe key is (mailbox, Gmail thread id, outbound message id): **one `rule_suggestion` per outbound message**, whichever person or company matched it. The owner edits and sends it from their own mail; the system never sends email. No suggestion is created for a person marked Do not contact, or for a company whose only matching people are marked so (`007` FR-CRM-CAMP-03). The Client silent flag is unchanged. |
| FR-CRMAI-RULE-03 | **R-2 Stuck in stage.** A new flag on open deals: amber when time in the current stage reaches the stage's limit, red at twice the limit. Time in stage is measured from the deal's stage entered time, whatever activity happened. The Stale deal flag (activity-based) is unchanged. **The R-2 switch controls both the flag and its notice** (Q-29): while R-2 and phase 3 are on, Stuck in stage is computed by the flags job and counts like any flag — toward the deal's level (its worst flag, `007` FR-CRM-FLAG-03), and through it the company's level, the seeded Follow-ups owed view (FR-CRM-VIEW-11), the deal-health dot, the dashboard's colours and the dashboard and weekly digest flag counts (FR-CRM-DASH-02 gains "stuck in stage"). With R-2 off the flag is absent and every level is as today. |
| FR-CRMAI-RULE-04 | R-2's notice: the owner gets one bell notice and one message per deal per stage entry, when the deal first turns amber. A deal that also has a Stale deal notice at the same level the same day gets only one message (Q-14). |
| FR-CRMAI-RULE-05 | **R-3 Lead gone cold.** A company at lead stage Contacted or Nurturing with no activity for `lead_cold_days` gets a `rule_suggestion` to move it to Nurturing or Disqualified (from Nurturing, to Disqualified, or to set a next touch (Q-39)). Activity is the latest of: last contact, a note linked to it, a task linked to it created or completed, a lead stage change, and activity on any of its deals (Q-15). One open suggestion per company. A dismissed or expired suggestion is not re-created until the company has new activity and then goes cold again (FR-CRMAI-REV-08), and after a dismissal not again for `lead_cold_days` even if the company goes cold again sooner (Q-40). The Loose end flag is unchanged. |
| FR-CRMAI-RULE-06 | **R-4 Campaign follow-through.** There is no "replied" member status (`007` FR-CRM-CAMP-01). A member whose status has been Engaged or Meeting booked for more than 7 days, with no converted opportunity and no open task linked to its company or person, gets a `rule_suggestion`: qualify (create a deal, the existing conversion of FR-CRM-CAMP-04) or close out (Not interested) (Q-16). "For more than 7 days" is measured from a new `status_changed_at` column on `crm_campaign_members`, set by the member write path whenever status changes and backfilled by the migration from the audit log (falling back to `updated_at` where no audit entry exists). No suggestion is created for a person marked Do not contact, or for a company member whose only people are marked so. Automation 8 is unchanged. |
| FR-CRMAI-RULE-07 | Rule suggestions never move a stage or create a deal themselves; approving one goes through FR-CRMAI-REV-04, so pipeline rules apply (a deal still needs its next step). |
| FR-CRMAI-RULE-08 | Notices use the existing bell and out-of-app channel (`007` FR-CRM-NOTIF-02): Slack and email through the portal's notifier, the bell only for CRM-only people until an SMTP sender exists. |

---

## 9. Functional requirements — phase 4

### 9.1 Contact recency — FR-CRMAI-REL

The source calls this "relationship strength" with levels warm, cooling and
cold. The company page already has an editable **Relationship status** field
whose options include Warm and Cold (`007` §5.6). To avoid two Warm/Cold
labels with different meanings on one page, the derived value is shown as
**Contact recency** with levels **Recent**, **Fading** and **Lapsed** (Q-21).
Relationship status stays the person-set judgement; Contact recency is a
computed fact about recent contact and never changes it.

| ID | Requirement |
|---|---|
| FR-CRMAI-REL-01 | Derived, not AI. Each person and company has exactly one level, in this order: **Recent** when, in the last 30 days, there was both an inbound and an outbound email, or a meeting note; else **Fading** when there was any qualifying contact (an inbound or outbound email, or a meeting note) in the last 90 days; else **Lapsed** (Q-17). Emails count when stored and non-internal, non-auto. A company is computed over its own contacts and those of its people. Counted from stored email metadata and meeting notes, not only the last inbound and outbound times. |
| FR-CRMAI-REL-02 | Shown as a chip on company and person pages and as a **filter** on company and person views (source §10). It is stored as a `contact_recency` column on `crm_companies` and `crm_people`, registered as a fixed row in the `crm_object_fields` registry (migration 034) and in `objects.VIEW_FIELDS`, filterable only. The column is added to the skip lists of `crm_touch_updated_at` and `crm_audit_row`, so recomputing it neither moves updated time nor writes audit rows. Recomputed with the flags job. |
| FR-CRMAI-REL-03 | When Gmail sync is off for the workspace, the level is computed from meeting notes only and the chip says "Meetings only". |

### 9.2 Saved views

Dropped (C1). `007` FR-CRM-VIEW-05 already gives private and shared saved
views with defaults and favourites; the source's examples ("My Australia
leads") are views anyone can save today.

---

## 10. NLDW tools — FR-CRMAI-MCP

| ID | Requirement |
|---|---|
| FR-CRMAI-MCP-01 | Three new tools, acting as the token's owner through the CRM routes: `crm_thread_summary` (one thread's summary, asks, commitments, by the thread key `crm_get` returns); `crm_review_list` (the caller's open items, or a workspace Admin's whole queue); `crm_review_decide` (approve, edit and approve with edits, or dismiss one item). That makes **21** `crm_*` tools. |
| FR-CRMAI-MCP-02 | `crm_get` additionally returns, per record: latest thread summaries, enriched values with status, AI fields and contact recency, each with provenance, subject to FR-CRM-ACC-11. `crm_today` returns the To review section. |
| FR-CRMAI-MCP-03 | `crm_review_decide` is a write: it says CONFIRM FIRST and its description opens with WORKSPACE ADMINS AND MEMBERS (`007` FR-CRM-MCP-05, -11). `crm_thread_summary` and `crm_review_list`: ANY MEMBER OF THE WORKSPACE (`crm_review_list` returns nothing for a Viewer). |
| FR-CRMAI-MCP-04 | Promoting an enriched value is an ordinary `crm_update`. Web only: AI settings, budgets, phase switches, AI field definitions, rule parameters, bulk "Enrich selected" (consistent with `007` FR-CRM-MCP-10, Q-18). |
| FR-CRMAI-MCP-05 | The MCP server instructions describe summaries, the Review Queue and that AI suggestions need approval (`007` FR-CRM-MCP-09). |

---

## 11. Changes to existing specs and behaviour — FR-CRMAI-CHG

| ID | Requirement |
|---|---|
| FR-CRMAI-CHG-01 | **Metadata-only reads amended.** The "no message body is ever fetched (Q-29)" promise in `backend/app/services/crm/gmail.py` is amended: the sync read stays metadata-only and `test_only_metadata_is_asked_for` stays for it; a full-message read exists only for the summariser on `worker-crm-ai`, pinned by a new test that only the summariser uses it. Message text MUST NOT be stored, logged or queued. `007` Q-29, FR-CRM-SYNC-06 and NFR-CRM-02 ("bodies are not stored") stand. `007` §15's "as metadata" is amended to "as metadata and AI summaries, never bodies". |
| FR-CRMAI-CHG-02 | **`007` §12 and §13 amended**: record-level AI suggestions (summaries, enrichment, AI fields, rule drafts) move from Phase 2 into this spec; the "CRM assistant" stays out. Lead scoring stays out of scope; ICP fit is a read-only suggestion (§2.2). |
| FR-CRMAI-CHG-03 | **`007` FR-CRM-TODAY-01 amended**: a sixth section, To review (FR-CRMAI-REV-09), shown while any phase is on or the person has open items (FR-CRMAI-FLAG-04). Today's fixed section list changes with it, and so does `008`'s morning brief, which reads it. |
| FR-CRMAI-CHG-04 | **`007` FR-CRM-MCP-01, FR-CRM-CHG-07 and Q-07 amended**: 21 `crm_*` tools. `004` FR-MCP-01's CRM carve-out wording is unchanged (the new tools are `crm_*` tools under `/api/v1/crm/`). |
| FR-CRMAI-CHG-05 | **`007` FR-CRM-FLAG table and FR-CRM-DASH-02 amended**: a Stuck in stage flag (FR-CRMAI-RULE-03) with per-stage thresholds, counted toward flag levels, Follow-ups owed and the flag counts while R-2 is on; R-1 and R-3 parameters added to flag thresholds. |
| FR-CRMAI-CHG-06 | **`007` FR-CRM-AUTO-01/-02 and Q-41 amended**: four new switchable automations (R-1 to R-4), numbered 11 to 14 after the existing ten, seeded off. Automation 4 (flag computation) stays unswitchable **apart from the Stuck in stage flag**, which is computed only while R-2 and phase 3 are on. |
| FR-CRMAI-CHG-07 | **`007` FR-CRM-JOB-01 amended**: the job list gains `summaries`, `meetings`, `enrichment`, `ai_fields`, `review_expiry` and `followup_drafts` (FR-CRMAI-JOB-01); the `automations` job is relabelled "Automations" and checks each switch separately (FR-CRMAI-JOB-05). |
| FR-CRMAI-CHG-08 | **`007` FR-CRM-NOTIF-01 amended**: new bell events Stuck in stage (owner) and AI budget reached (Admins). |
| FR-CRMAI-CHG-09 | **`007` FR-CRM-AUD-02 amended**: an approved suggestion is audited as the approving person, with the item id in `audit_log.suggestion_id` (FR-CRMAI-REV-05). |
| FR-CRMAI-CHG-10 | **`008-tasks`**: task sources `email_commitment` and `meeting_action` with a source reference, as `008`'s source brief already lists, are created only through an approved Review Queue item. If `008` changes its create path or source list, this spec follows `008`. |
| FR-CRMAI-CHG-11 | **Record lifecycle** (`007` trash, restore, purge, merge, export). Summary links, review items, enrichment, AI field values and anything else that points at a company, person, deal or note follow the record: **trashing** a record hides its summaries, enrichment, AI values and items (open items are not decidable while it is trashed); **restoring** shows them again; **purging** deletes them, in `crm_purge` (including its table-to-record-type mapping, which must not fall to 'unknown'); a **company merge** (`crm_merge_companies`) moves summaries and summary links, enrichment, AI field values and open items to the surviving company and recomputes their dedupe keys, keeping the survivor's own values where both have one; the per-table reference checks (`*_check_refs`) cover the new link tables; the **full workspace export** (`007` FR-CRM-IO-04) includes email summaries, review items, enrichment and AI field definitions and values, and excludes the AI usage log (cost administration, FR-CRMAI-LLM-10). |

---

## 12. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-CRMAI-01 | **Privacy.** No column, log line, Celery payload or result, error text, job-run record or audit entry written by these jobs contains an email body, transcript or web page text. A test seeds a unique marker string in each of: an email's quoted reply history, its signature, an internal-only message on the same thread, a transcript and a web page — never in the part the model is asked to paraphrase. It runs every job, including the failure path (invalid model output), then inspects every text column the jobs write, the captured logs, the Celery payloads and the Redis result keys, and finds no marker. |
| NFR-CRMAI-02 | **Isolation.** A test builds prompts for workspace A while workspace B has matching people and companies and asserts no B record reaches A's prompt, item, note or summary; every new route answers another workspace's id 404 (`007` FR-CRM-WS-02). |
| NFR-CRMAI-03 | **Redaction.** Viewers see summaries, AI fields, meeting notes and enriched values, never an amount (`007` FR-CRM-ACC-11, FR-CRMAI-LLM-05). |
| NFR-CRMAI-04 | **Security.** Keys are Railway secrets on `worker-crm-ai` (Claude, Fireflies, Gmail credential) and `worker-crm-sync` (Gmail credential) only, never logged, never sent to the browser. The browser reaches the backend only through the proxy. New tables have RLS enabled and no browser grants in their creating migration; permissions are enforced in the backend (`007` Q-03). Schema changes land as numbered files in `supabase/migrations/`. |
| NFR-CRMAI-05 | **Timeliness.** A summary within one sync cycle (15 minutes) of the message being stored; a meeting note within 30 minutes of the transcript being ready in Fireflies. |
| NFR-CRMAI-06 | **Observability.** Every model, Gmail, Fireflies and website call is logged as structured JSON with job, workspace, status, latency and tokens, never content. *(Amended in S5: a Fireflies call carries the job `meetings` and the workspace its transcript is fetched for, `-` for the poll, which serves every workspace.)* Refusals are RFC 7807 with one plain sentence. |
| NFR-CRMAI-07 | **Tone.** Copy is plain and direct; AI output is labelled as AI wherever it appears. `/help` gains one guide for the job "Review AI suggestions", and the CRM guide is updated; guides carry no names, figures or costs. |
| NFR-CRMAI-08 | **Dependencies.** Any new direct dependency is justified in the plan (constitution §II). The Claude and Fireflies clients use no vendor SDK: they follow the existing raw-HTTP pattern of the Gmail client, with timeouts (Q-42). |
| NFR-CRMAI-09 | **Tests.** Each job is tested for idempotency (run twice, nothing new), each approval for going through the manual-edit validation, the budget stop, the switches, and the rules with fixed clocks. Model calls are faked in tests. |

---

## 13. Key entities and indicative changes

Logical; the plan chooses names and types.

```text
New (workspace-scoped, RLS on, no browser grants, audited where people decide)
  email summary        mailbox + thread key, links (companies, people, deals),
                       summary, asks, commitments, suggested next step + date,
                       sentiment, watermark, model, generated at; no body
  review item          type, owner, target record, suggestion, the current
                       value of each field it would replace (at creation),
                       source + source key, dedupe key, status (open,
                       approved, dismissed, expired), decided by / at
  AI usage log         workspace, job, model, tokens in/out, estimated cost
                       (USD), outcome, at; no text
  enrichment           per company: enriched values with provenance, status,
                       failure reason (never on crm_companies)
  AI field             definition (workspace, record type, prompt, output type,
                       options, enabled) and value (record, value, reason,
                       provenance) (never on the record tables)

Existing tables
  crm_workspace_settings   key CHECK (026) widened with `ai` {budget_usd,
                           phases}; migration INSERTs an `ai` row in every
                           workspace and UPDATEs existing `automations` and
                           `flag_thresholds` rows by merging in the new keys
  crm_job_runs             job CHECK and crm_job_latest widened (summaries,
                           meetings, enrichment, ai_fields, review_expiry)
  crm_notifications        kind list widened (stuck in stage, AI budget)
  crm_notes                + source reference (Fireflies transcript) and
                           provenance for AI-written meeting notes
  crm_opportunities/flags  + Stuck in stage flag in the stored flags
  crm_companies, crm_people + contact_recency (skipped by crm_touch_updated_at
                           and crm_audit_row)
  crm_campaign_members     + status_changed_at, backfilled from audit_log
  crm_object_fields (034)  + fixed contact_recency rows (filter only)
  audit_log                + nullable suggestion_id; crm_audit_row reads it
                           from crm.actor
  008 tasks                created by approval with source + source ref
```

Code and screens that change: a new `crm-ai` queue and `worker-crm-ai` service
(Procfile or Railway config, Celery routing); the Gmail client (a full-message
read for the summariser only, its docstring, and a new test that only the
summariser uses it; `test_only_metadata_is_asked_for` stays); the `JOBS`
registry (new jobs, the `automations` label) and the `automations` task, which
checks each switch separately; Today sections (and so `008`'s brief); record
timelines and email panels (expandable summary); company page (enriched values,
promote); Settings (AI page, AI fields, rule parameters, switches); a Review
Queue page under `/crm/[ws]`; the settings validators (`ai`, `automations`,
`flag_thresholds`); `domain/crm/flags.py` (Stuck in stage, thresholds);
`crm_query`, `objects.VIEW_FIELDS` and `test_crm_object_fields_registry.py`
(contact recency); `crm_purge`, `crm_merge_companies`, the `*_check_refs`
functions and the workspace export (FR-CRMAI-CHG-11); `crm_touch_updated_at`
and `crm_audit_row` (skip lists, `suggestion_id`).

MCP tests that pin the tool count or enumerate tools and routes, all of which
change for 21 tools: `test_crm_mcp.py`'s exact set, caller lines and read set,
`test_all_eighteen_tools_exist` (`== 18`), `ONE_CALL_EACH` (must equal the
tool set) and the FR-CRM-MCP-02 purposes map; `test_crm_acceptance.py`'s
COVERAGE map, which names `test_exactly_the_eighteen_crm_tools` and must be
edited when the "eighteen" tests are renamed; `test_crm_redaction_sweep.py`,
where every new POST route must be classified as a read or a write
(`posts == POST_READS | WRITES`) and every new read tool swept; and
`test_crm_isolation_sweep.py`'s route and tool lists. The redaction and
isolation sweeps also cover the contact recency view field.
`test_crm_only_refused.py` needs no change (all new routes are under
`/api/v1/crm/`).

Docs: `docs/architecture/crm.md`, `docs/guides/09-crm.md`,
`docs/guides/10-crm-admin.md` (Settings, AI), the connecting-Claude guide and
`docs/operations/deployment.md` (the new service and secrets).

---

## 14. Decisions

Owner decisions of 18 October 2026 are C1–C6, O1 and D-1 to D-4 (the plan's
"built but not asked for" items, plan §11.1). Q-01 onward are the
recommended defaults taken under the owner's instruction "use recommended
option for all the questions"; each is the conservative reading and can be
reversed. Q-30 to Q-42 are additions beyond the source, proposed in review as
§17.2 A-01 to A-13 and accepted by the owner as written on 18 October 2026.

| ID | Question | Source said | Decision | Changed |
|---|---|---|---|---|
| **C1** | Saved views (source §11) | Build per-person views, shareable | **Dropped**: they exist (`crm_saved_views`, migration 034, FR-CRM-VIEW-05). | Section removed |
| **C2** | Automation rules (source §9) | A small rule engine with four rules | **Extend `007` flags and automation switches** (migration 038, `domain/crm/flags.py`, FR-CRM-FLAG, FR-CRM-AUTO). New only: per-stage limits, AI follow-up draft, lead-gone-cold suggestion, campaign follow-through suggestion. | No engine |
| **C3** | Gmail sync is off in production | Assumed running | **Step zero**: the owner sets up the Google service-account credential (and the sync worker and switch) before phase 1 can run. | Prerequisite added |
| **C4** | Claude key, model, budget | Own key; per-workspace monthly **token** budgets | **New Railway secret; Claude Sonnet `claude-sonnet-5-5` for summaries and enrichment** (and every other call here, Q-01); a **monthly budget per workspace set by its Admins**; Admins see month-to-date spend in Settings. The budget is held as **estimated spend, not tokens**, so it matches the "month-to-date spend" Admins see; the estimate depends on the rate table (§17.1). | Made concrete; unit changed from tokens to estimated spend |
| **C5** | Fireflies access | Poll or webhook | **Own API key as a Railway secret; poll every 15 minutes; no webhook.** | Webhook dropped |
| **C6** | Feature flags | Per-workspace flag per phase | **Per-phase on/off switches per workspace, in the existing workspace settings.** | Stored in settings |
| **O1** | Build order | Unstated | **`008-tasks` first, then this.** Approved task items create rows in `008`'s tasks table. | Dependency added |
| **D-1** | Rates not configured (plan §11.1) | Unstated | **Approved**: until both rates are set every AI job is skipped "AI rates are not set", and the AI settings read reports it (FR-CRMAI-LLM-07). | Added |
| **D-2** | Enrichment failure reasons beyond ENR-06 (plan §11.1) | Unstated | **Cut**: a failure reason is one of FR-CRMAI-ENR-06's two or FR-CRMAI-LLM-03's four; a refused website address is recorded "Website did not respond". | Minimum built |
| **D-3** | Extra review, enrichment and AI-field options (plan §11.1) | Unstated | **Cut**: no single-item review read, no review filters beyond mine/all and open/decided, no view-wide enrichment run, AI field layout options fixed by the seeds, no enrichment attempt counter, no "approved, nothing changed" state. | Minimum built |
| **D-4** | Job name for R-1 drafts (plan §11.1) | Unstated | **Approved**: `followup_drafts`, a sixth AI job name (FR-CRMAI-JOB-01). | Added |
| Q-01 | Model for AI fields and drafts | Unstated | Same model as C4. | Added |
| Q-02 | Budget before an Admin sets one | Unstated | **Zero**: nothing runs until set. | Added |
| Q-03 | "Jobs pause on budget" | Pause | **Recorded `skipped` with a reason**, banner and one notice; no new "paused" outcome. | Mechanism |
| Q-04 | Default state of new switches | Unstated | **Seeded off**, and existing rows updated, because a missing key reads as on. | Added |
| Q-05 | Where AI work runs | Inside the sync job | **A new `crm-ai` queue on a new `worker-crm-ai` service** (concurrency 1), holding the Claude, Fireflies and Gmail keys. Sync pages stay alone on `worker-crm-sync`, so the sync lock is never held during a model call; the shared worker runs no AI task, keeping the slots `007` Q-74 protected. | Split from sync and shared worker |
| Q-06 | Thread identity | Thread id unique per workspace | **Mailbox plus thread id**; Gmail thread ids are per mailbox. | Key changed |
| Q-07 | Deal on a summary | One nullable deal | **All linked open deals**; the next-step item carries a deal picker when there are several. | Changed |
| Q-08 | Amounts in AI text vs Viewers | "Viewers never see amounts" | **No amounts in generated text**, enforced by schema and a masking check over every stored generated string, with listed patterns. | Made enforceable |
| Q-09 | Notifying new review items | Unstated | **No per-item notice**; Today carries them. | Added |
| Q-10 | Unknown meeting attendees | Ignore unmatched meetings | **Ignored; no records created; transcript never fetched.** | Kept |
| Q-11 | Meeting note structure | Title, attendees, decisions, action items | **In the note body**; only a source reference and provenance are added. | Placed |
| Q-12 | ICP fit options in Academy | Fixed Labs options | **Seeded in Labs only.** | Narrowed |
| Q-13 | "Deal health" AI field | Deal health | **Renamed Deal outlook**; "deal health" stays the flag. | Renamed |
| Q-14 | Stuck in stage vs Stale deal | New amber/red and notice | **Both flags kept**; one notice per stage entry; one message a day when both fire. | Reconciled |
| Q-15 | Company "activity" for R-3 | "No activity" | **Defined** (FR-CRMAI-RULE-05). | Defined |
| Q-16 | "Member who replied" | Replied | **Engaged or Meeting booked** for more than 7 days, measured from a new `status_changed_at` (no Replied status exists). | Changed |
| Q-17 | Warm, cooling, cold | From last inbound/outbound | **A total order over email metadata and meeting notes in 30/90 days**, companies including their people. | Defined |
| Q-18 | NLDW coverage vs settings | Every feature via NLDW | **Settings and bulk enrichment web only**, as `007` FR-CRM-MCP-10. | Limited |
| Q-19 | Private notes in prompts | Strip "private" notes | **No private-note concept exists**; nothing to strip; phone numbers still removed. | Dropped |
| Q-20 | `/help` | One page per feature | **One guide for the job** plus the CRM guide updated, per the guides convention. | Changed |
| Q-21 | Name of relationship strength | Warm, cooling, cold | **Shown as Contact recency: Recent, Fading, Lapsed**, because the company's Relationship status already uses Warm and Cold (as Q-13 did for deal health). | Renamed |
| Q-22 | Where switches and parameters live | "Per-workspace flag"; rule switches and parameters | **Phase switches and budget in a new `ai` setting** with its own validator; rule switches as flat keys in `automations`; rule parameters as flat whole-number keys in `flag_thresholds`. | Placed |
| Q-23 | Budget currency and month | Unstated | **USD** (the rate table's currency); **Asia/Kolkata calendar months**; overshoot limited to the one call in flight. | Defined |
| Q-24 | Queue visibility across phases | Queue is phase 1 | **Review Queue and To review show while any phase is on or open items exist**; phases stay independent. | Added |
| Q-25 | Summarising the 90-day backfill | Unstated | **Not summarised**; summarising starts after the backfill. | Added |
| Q-26 | Meeting matching two workspaces | Unstated | **Not processed**; skipped count only, no content fetched. | Added |
| Q-27 | Number AI fields vs lead-scoring non-goal | Number 0–100 | **Kept as the source asks, on record pages only, never sorted, filtered or ranked**: a deliberate, bounded exception to the lead-scoring non-goal. | Limited |
| Q-28 | AI fields in views | Dashboard may show deal health | **Record pages only**; Admin-defined fields cannot fit the fixed view-field registry. The dashboard may show Deal outlook as the source says. | Limited |
| Q-29 | R-2 switch vs `007` Q-41 (flags unswitchable) | Notify once | **The R-2 switch controls the Stuck in stage flag and its notice**; an explicit amendment of Q-41 (FR-CRMAI-CHG-06). | Amended |
| Q-30 | Enrichment network protection (A-01) | Unstated | **Refuse private, loopback and link-local hosts; connect to the checked IP; same-site redirects only, each hop re-checked** (FR-CRMAI-ENR-02). Reason: Security-First; the worker fetches addresses people typed in, so without this it could be pointed at internal services. | Added |
| Q-31 | Enrichment read limits (A-02) | Unstated | **At most 5 pages per company, 1 MB per page, 10-second timeout per request** (FR-CRMAI-ENR-02). Reason: Security-First and cost; a hostile or huge site cannot stall the single-slot AI worker or inflate the prompt. | Added |
| Q-32 | Website change (A-03) | Enrich on create | **Changing a company's website queues enrichment again** (FR-CRMAI-ENR-01). Reason: enriched values would otherwise describe the old site. | Added |
| Q-33 | Enrichment idempotency key (A-04) | Company domain | **Company id plus website** (P-5, FR-CRMAI-ENR-08). Reason: two companies can share a domain, and a website change needs a new key. | Key changed |
| Q-34 | Review item retention (A-05) | Unstated | **Decided and expired items readable in the Admin view for 90 days, then reduced to a dedupe tombstone with no suggestion text** (removing stored R-1 draft text) (FR-CRMAI-REV-07, -08). Reason: Security-First and P-3; generated text is not kept longer than it is useful, while dedupe still works. | Added |
| Q-35 | To review details (A-06) | Unstated | **Today shows the three oldest items; the daily digest (`007` FR-CRM-NOTIF-03) carries the same section** (FR-CRMAI-REV-09, -10). Reason: items reach people without a per-item notice (Q-09). | Added |
| Q-36 | Invalid model output (A-07) | Unstated | **Retry once before recording the item as failed** (FR-CRMAI-LLM-03). Reason: an occasional malformed response should not fail an item that a second call would complete. | Added |
| Q-37 | Prompt injection (A-08) | Unstated | **Untrusted text quoted as data with an instruction never to follow it; AI text rendered as plain text; schema refuses URLs except the Fireflies transcript link and an enriched LinkedIn or same-site website** (FR-CRMAI-LLM-04, SUM-08, RULE-02). Reason: Security-First; emails, transcripts and web pages are written by outsiders (`004` FR-MCP-05). | Added |
| Q-38 | AI field recompute cap (A-09) | Recompute on change | **At most once per record per hour, with a trailing recompute** (FR-CRMAI-FIELD-04). Reason: a busy record would otherwise spend the budget on repeated recomputes. | Added |
| Q-39 | R-3 options (A-10) | Move to Nurturing or Disqualified | **"Set a next touch" as a third option on a Nurturing company** (FR-CRMAI-RULE-05). Reason: a cold lead is often worth one more touch rather than a stage change. | Added |
| Q-40 | R-3 suppression (A-11) | Unstated | **After a dismissal, not again for `lead_cold_days` even if the company goes cold again** (FR-CRMAI-RULE-05). Reason: a dismissed suggestion should not return within the same cold period. | Added |
| Q-41 | Updated transcripts (A-12) | Unstated | **An updated transcript updates the AI-written note only while no person has edited it** (FR-CRMAI-MEET-07). Reason: P-1; AI never overwrites a person's words. | Added |
| Q-42 | Vendor SDKs (A-13) | Unstated | **No vendor SDK; raw HTTP with timeouts, as the Gmail client** (NFR-CRMAI-08). Reason: constitution §II (simplicity) and Security-First; fewer dependencies and explicit control of what is sent and logged. | Added |

---

## 15. Acceptance criteria

Foundations (phase 1)
- [ ] With phase 1 off, or with a zero budget, no model call is made and the job run says why.
- [ ] Reaching the budget stops further calls that month, records `skipped` "AI budget reached", shows the banner and sends the Admins one notice; raising the budget resumes the skipped work on the next tick without duplicates.
- [ ] With a fixed clock, a budget reached on the last day of an Asia/Kolkata month allows calls again at 00:00 IST on the first of the next month.
- [ ] Settings, AI shows month-to-date estimated spend per workspace and per job, to that workspace's Admins only; a Member, a Viewer and a TVA admin who is not a member of the workspace get no cost figure from any page, route or job-run record.
- [ ] No Claude, Fireflies or Gmail key reaches the browser or a log line.
- [ ] A workspace that existed before the migration reads every new phase and rule switch as off and can save its `ai`, `automations` and `flag_thresholds` settings straight after the migration.
- [ ] Switching phase 1 off keeps stored summaries (they return when switched on) and leaves open items visible and decidable while any phase is on or items are open.
- [ ] With phase 1 off and phase 2 on, meeting and enrichment items appear in the Review Queue and To review and can be decided.
- [ ] Turning the stale-deal notice off does not stop R-1, R-3 or R-4; the `automations` run is `skipped` only when every switch is off.

Email summaries (phase 1, after step zero)
- [ ] A labelled thread with a client produces a summary within one sync cycle, shown on the company, person and deal with the AI marker and provenance.
- [ ] The privacy test (NFR-CRMAI-01) finds no marker in any column, log, queue payload or result key, including after invalid model output.
- [ ] A thread holding an internal-only message with a unique marker produces a summary, prompt and log without that marker; markers in quoted history and signatures are absent from the prompt.
- [ ] Running the job twice on an unchanged thread makes no model call and changes nothing; a new message replaces the summary.
- [ ] Removing a thread's label deletes its summary on the run that deletes its messages.
- [ ] A paused mailbox's threads are not summarised and their existing summaries stay.
- [ ] Connecting a mailbox and running its 90-day backfill makes no model call.
- [ ] A promised date in an email appears as a `task_from_commitment` item, not a task.
- [ ] A suggested next step that differs from the deal's appears as a `next_step_change` item; the deal is unchanged until approval. With two linked open deals, one item appears with a deal picker, and approving applies to the chosen deal only.
- [ ] A fixture email with an injected instruction and a link produces neither the instruction's effect nor the link in any output (Q-37).
- [ ] With a fixture email and transcript holding ₹4.5L, 12 lakh, 1.2 Cr, $20k, AUD 15,000 and "budget of 50k", the Viewer routes and MCP responses for summaries, meeting notes, review items, enriched values and AI fields contain none of them.
- [ ] Owner resolution: an exact member match is routed to that member; an ambiguous name and a name belonging to another workspace's member are stored as "unknown" and routed to the fallback.

Review Queue (phase 1)
- [ ] Approving a next-step item changes the deal through the same validation as a manual edit: an approval with an empty date is refused, and an approval after someone else edited the deal's next step is refused as changed.
- [ ] A new synced email that updates the company's Last inbound does not invalidate an open `enrichment_conflict` or company `rule_suggestion` on it.
- [ ] Approving a task item creates one `008` task with source and source reference, linked to the record; approving twice creates one.
- [ ] Members see only their items; workspace Admins see all; Viewers see none; another workspace's item id gives 404.
- [ ] An item untouched for 14 days expires, the expiry is in the job log and audit, and the same source does not re-create it.
- [ ] An approved change's audit entry names the approver and carries the item id.
- [ ] Today shows To review with the count and the oldest items, and `008`'s brief shows the same section.
- [ ] `crm_review_list` and `crm_review_decide` work through NLDW, with CONFIRM FIRST on decide.
- [ ] Trashing a company hides its summaries and items; restoring shows them; purging deletes them; merging two companies moves summaries, enrichment, AI values and open items to the survivor; the full workspace export holds the new tables except the AI usage log.

Meetings and enrichment (phase 2)
- [ ] A client call recorded in Fireflies appears as a meeting note on the right company (and deal, when there is exactly one) within 30 minutes, with its action items in the queue and no transcript text stored.
- [ ] A meeting with only internal attendees, or with no attendee matched in the workspace, creates nothing, and the fake Fireflies client records no transcript fetch for it.
- [ ] A meeting matched in two workspaces creates nothing in either, fetches no transcript, and counts as skipped in each workspace's run.
- [ ] Re-polling the same transcript creates no second note.
- [ ] An updated transcript updates the AI-written note until a person edits it; after a person's edit, further updates do not change the note (Q-41).
- [ ] Importing the CODISSIA or Australia list does no enrichment work inside the import request or transaction; companies are enriched in the background; every value is marked AI; no person-entered field is overwritten.
- [ ] A company created by email sync is queued for enrichment.
- [ ] An unreadable site records failed with a reason and no values.
- [ ] A site address resolving to a private network is refused (Q-30).
- [ ] Promoting an enriched value is an audited edit by the person; an enriched value that differs from a filled field shows an `enrichment_conflict` item, not a promote button.

AI fields and rules (phase 3)
- [ ] AI fields are read-only, marked AI, shown on record pages only, and changing one never changes a stage, probability, flag or colour.
- [ ] With fixed clocks: a Proposal deal turns Stuck in stage amber at 14 days and red at 28, its owner gets one notice; Stale deal behaves as before.
- [ ] Switching R-2 on can turn a deal's dashboard colour and its company's level amber and put it in Follow-ups owed; with R-2 off no deal has the flag.
- [ ] A deal that gets a Stale deal and a Stuck in stage notice at the same level on the same day gets one message.
- [ ] R-1, R-3 and R-4 create one suggestion per trigger, none after a dismissal or expiry until the trigger recurs, and nothing while their switch is off.
- [ ] R-1: an outbound email matched to a person and their company produces one item; the draft task receives ids only and reads no message body; R-1 and R-4 create nothing for a person marked Do not contact.
- [ ] R-4 measures 7 days from `status_changed_at`, not from an unrelated edit.
- [ ] Notices reach CRM-only owners by bell only.
- [ ] No rule sends an email or changes a record without approval.

Contact recency (phase 4)
- [ ] With fixed clocks, the levels match FR-CRMAI-REL-01 at 30, 31, 90 and 91 days and for one-sided email (outbound only, inbound only), and a company includes its people's contacts.
- [ ] With Gmail off, the chip says "Meetings only" and uses meeting notes.
- [ ] Contact recency filters company and person views and recomputing it does not move the record's updated time.

Isolation (every phase)
- [ ] The isolation test shows no workspace B data in any workspace A prompt or output.

---

## 16. Out of scope

A rule builder; an in-CRM chat assistant; call recording; email sequences or
any automatic sending; contact data purchase or phone lookup; usage credits or
metering for users; lead scoring (`007` §13); storing email bodies,
transcripts or web pages; a Fireflies webhook (C5); saved views (C1); custom
objects; AI fields as view columns or filters (Q-28).

---

## 17. Open items

### 17.1 Owner and admin steps

| Item | Owner | Blocks |
|---|---|---|
| **Step zero.** Google service-account credential with domain-wide delegation, read-only Gmail scope, as a Railway secret on `worker-crm-sync` and `worker-crm-ai` (`007` FR-CRM-SYNC-03) | Owner | Phase 1 summaries, R-1, Contact recency from email (C3) |
| Create the `worker-crm-sync` Railway service; set `CRM_GMAIL_CONFIGURED=true` on api, beat and worker; switch email sync on per workspace | Owner | Same |
| Create the `worker-crm-ai` Railway service (queue `crm-ai`, concurrency 1) | Owner | Every AI job (Q-05) |
| Claude API key as a Railway secret on `worker-crm-ai` only | Owner | Every AI job (C4) |
| Fireflies API key as a Railway secret on `worker-crm-ai` only (beat on the shared worker schedules the 15-minute tick); name which Fireflies account it belongs to (it sees only that account's meetings) | Owner | Phase 2 meetings (C5) |
| Set each workspace's monthly AI budget; switch phases on, Labs first | Workspace Admins | Everything (Q-02, Q-04) |
| Confirm the rate table (USD per million input and output tokens) used for the spend estimate | Owner | Every AI job (skipped until set, D-1), cost display, budget (C4) |
| Production SMTP sender (still open from `007` §14) | Owner | Messages to CRM-only people |
| Confirm the model id `claude-sonnet-5-5` is available on the key | Owner, at plan time | Phase 1 |

### 17.2 Proposed additions

All thirteen (A-01 to A-13) were accepted by the owner as written on 18 October 2026; they are recorded in §14 as Q-30 to Q-42 and written into the requirements and acceptance criteria they affect.

---

## 18. What this must not become

- **Not an autopilot.** If an AI output ever changes a record without a
  person's approval, that is a bug.
- **Not a mail or meeting archive.** Summaries, not text.
- **Not a second rule engine.** New rules extend `007` flags and automations.
- **Not a scoreboard.** AI fields and Contact recency never rank sellers and
  never feed stages, probabilities or colours. The one rule that changes
  colours is R-2's Stuck in stage flag, a deterministic flag like Stale deal,
  switched by an Admin (Q-29), not an AI output.
- **Not a leak between workspaces.** A prompt sees one workspace.
