# Source: TVA CRM Intelligence Layer, v0.1 (18 Oct 2026)

> Kept verbatim as the input to `spec.md`. Where `spec.md` and this file
> disagree, `spec.md` wins: it records the owner's decisions of 18 Oct 2026.

# TVA CRM: Intelligence Layer (Attio parity, required features only)

Version 0.1, 18 Oct 2026. Owner: Navaneeth. Builder: Claude Code (spec-driven).
Stack assumption: existing TVA (Supabase, Railway workers, Netlify front end, NLDW MCP). Read the existing CRM schema and the NLDW tool definitions before writing any code, and extend them. Do not create parallel tables for things that already exist.

## 1. Goal

Bring the parts of Attio that actually change how Nunnari sells into the TVA CRM, so that records keep themselves current and every deal shows what was said, without paying per seat and without storing raw email bodies.

The CRM core is already built and is out of scope: companies, people, opportunities with contact roles, lead stages (new, contacted, nurturing, qualified, disqualified), deal stages, required next step and date, notes by kind, tasks, Drive links, campaigns and members, duplicate checks, Twenty and CSV import, Gmail label sync (subjects and participants only), the Today list (replies owed, next steps due, silent clients, loose ends), the dashboard with targets and RAG colours, the Labs handoff into TVA projects, workspaces (Labs, Academy B2B) and roles (Admin, Member, Viewer).

## 2. Principles (apply to every feature)

1. **AI suggests, a person approves.** No AI output changes a deal stage, a lead stage, an owner, an amount or sends any message. AI output is either stored in a clearly AI-labelled field or placed in the Review Queue (section 8).
2. **Provenance on every AI value.** Each AI-written value stores: source (email_thread, meeting, enrichment, ai_field), model, generated_at, and a link to its source record. The UI shows a small "AI" marker; hovering shows the source.
3. **No raw bodies at rest.** Email and transcript text is processed in memory by the worker and discarded. Only the generated summary and extracted items are stored. This keeps the current privacy stance.
4. **Our own API key, no credits.** All model calls go through one server-side Claude client with per-workspace monthly token budgets in Settings. When a budget is reached, jobs pause and Admins see it in Settings, Jobs; nothing fails silently.
5. **Idempotent jobs.** Every job can re-run on the same input without creating duplicates (key on Gmail thread id, Fireflies transcript id, company domain).
6. **Workspace isolation.** Labs and Academy B2B data never cross. Prompts only receive records from the workspace being processed.
7. **Every feature is reachable through NLDW**, so it works from Claude as well as the UI.

## 3. Phasing

| Phase | Features | Why first |
|---|---|---|
| 1 | Email thread summaries (4), Review Queue (8) | Biggest gap; everything else feeds the queue |
| 2 | Meeting capture from Fireflies (5), Company enrichment (6) | Removes manual logging and fixes imported lists |
| 3 | AI fields (7), Automation rules (9) | Builds on data from phases 1 and 2 |
| 4 | Relationship strength (10), Saved views (11) | Nice to have; only if the team asks |

Ship each phase behind a per-workspace feature flag. Enable on Labs first, Academy B2B after a week.

## 4. Email thread summaries

**Problem.** Sync stores subjects and participants only, so a record shows that people talked, never what was agreed.

**Behaviour.**
- The existing Gmail label sync job gets a summarise step. For each new or updated thread matched to a CRM company or person, the worker fetches the thread, sends it to Claude, stores the result, and discards the body.
- Output per thread (structured JSON, validated before save):
  - `summary`: 2 to 3 sentences, plain language
  - `asks`: what the other side asked for (list, may be empty)
  - `commitments`: what Nunnari promised, with owner and date if stated
  - `suggested_next_step` and `suggested_next_step_on` (only if the thread implies one)
  - `sentiment`: positive, neutral, concern
- Re-summarise when a thread gets a new message; keep only the latest summary per thread, with `updated_at`.
- If a thread is linked to an open deal and `suggested_next_step` differs from the deal's current next step, create a Review Queue item (type: next_step_change). Never overwrite directly.
- Commitments with a date create a Review Queue item (type: task_from_commitment).

**Data.** New table `crm_email_summaries` (workspace_id, thread_id unique per workspace, company_id, person_ids, opportunity_id nullable, summary, asks jsonb, commitments jsonb, suggested_next_step, suggested_next_step_on, sentiment, model, generated_at). No body column.

**UI.** On company, person and deal timelines, each email thread row expands to show the summary, asks and commitments, with the AI marker.

**NLDW.** `crm_get` includes the latest summaries on the record. Add `crm_thread_summary(thread_id)`.

**Acceptance.**
- A labelled thread with a client produces a summary within one sync cycle.
- The database never contains message bodies (add a test that inspects all text columns written by the job).
- A promised date in an email shows up as a Review Queue task suggestion, not a task.

## 5. Meeting capture from Fireflies

**Behaviour.**
- A worker polls Fireflies (or receives its webhook) for new transcripts.
- Match attendees by email to CRM people; resolve their companies; pick the open deal if exactly one exists for that company, otherwise leave the deal empty and ask in the Review Queue.
- Create a note of kind `meeting` with: title, date, attendees, summary (5 lines max), decisions, action items (owner, due date if stated), and a link back to the Fireflies transcript. Do not copy the transcript.
- Action items become Review Queue items (type: task_from_meeting). A changed next step becomes a next_step_change item.
- Meetings with no CRM match are ignored (internal meetings must never enter the CRM).

**Acceptance.** A client call recorded in Fireflies appears on the right company and deal within 30 minutes, with its action items waiting in the queue.

## 6. Company enrichment

**Behaviour.**
- Triggered on company create (UI, NLDW, import) when a domain or website is present, and by an Admin action "Enrich selected" on a list view.
- An agent reads the company's own website (and public pages only) and fills: industry, sub-industry, employee band, HQ city and country, one-line description, LinkedIn URL, products or services (short list).
- Values go into dedicated `enriched_*` fields with provenance, never into fields a person has already filled. A person can promote an enriched value to the main field with one click.
- Bulk runs are rate-limited and resumable; a CSV import of 500 companies must not block the UI.
- If the site cannot be read, record `enrichment_status = failed` with a reason; do not guess.

**Acceptance.** Importing the CODISSIA or Australia list enriches companies in the background, every value is marked AI-filled, and nothing a person typed is overwritten.

## 7. AI fields

AI fields are per-workspace computed fields defined by an Admin in Settings (name, record type, prompt, output type: text, single select, number 0 to 100). They are recomputed on demand and when their inputs change (new summary, new meeting note, enrichment).

Ship these four as defaults (Admins can edit or disable):

| Field | Record | Output | Notes |
|---|---|---|---|
| ICP fit | Company | Select: Factory Brain, Academy, Services, DeepWeaver, None | Uses enrichment plus notes |
| Account brief | Company | Text, 3 lines | Who they are, what they want, where we stand |
| Deal health | Opportunity | Select: on track, at risk, stalled, plus one-line reason | Uses summaries, next step dates, time in stage |
| What is blocking | Opportunity | Text, 1 line | Only when deal health is at risk or stalled |

Rules: AI fields are read-only to people; they never feed stage or probability automatically; the dashboard may show deal health as an extra column but keeps its existing RAG logic.

## 8. Review Queue

One queue per person, plus an Admin view across the workspace.

- Item types: next_step_change, task_from_commitment, task_from_meeting, deal_link (which deal does this meeting belong to), enrichment_conflict, rule_suggestion (section 9).
- Each item shows the suggestion, the source (thread, meeting, rule) and actions: Approve, Edit and approve, Dismiss.
- Approving applies the change through the same code path as a manual edit (so audit log, validation and optimistic locking with `expect_updated_at` all apply).
- Items expire after 14 days if untouched; expiry is logged.
- The Today list gets a sixth section, "To review", with the count and the oldest items.

**NLDW.** `crm_review_list`, `crm_review_decide(item_id, action, edits?)`.

## 9. Automation rules

TVA already flags replies owed, silent clients and overdue next steps in Today. This adds a small rule engine on top, configured per workspace by Admins. Four built-in rules, each with an on/off switch and parameters:

1. **No reply after outreach**: outbound email with no inbound reply in N days (default 5) creates a Review Queue suggestion with an AI-drafted follow-up for the owner to edit and send themselves. The system never sends email.
2. **Stuck in stage**: a deal in the same stage longer than its limit (per stage, defaults: discovery 14, qualification 21, proposal 14, negotiation 21 days) turns amber at the limit and red at twice the limit, and notifies the owner once.
3. **Lead gone cold**: a company in contacted or nurturing with no activity in N days (default 30) gets a suggestion to move to nurturing or disqualified.
4. **Campaign follow-through**: a campaign member who replied but has no deal and no task after 7 days gets a suggestion to qualify or close out.

Notifications use the channel TVA already uses for nudges. No custom rule builder in this version.

## 10. Relationship strength (phase 4)

Derived, not AI: compute warm, cooling or cold per person and company from email and meeting frequency in the last 30 and 90 days, using the existing last inbound and last outbound data. Show as a chip on records and a filter on lists.

## 11. Saved views (phase 4)

Per-person saved filters on companies, people and deals (for example "My Australia leads", "Oracle channel, qualified"), with an option to share a view with the workspace.

## 12. Explicitly out of scope

- A custom-object builder UI (fields are added through specs).
- An in-CRM chat assistant (Claude over NLDW already covers this).
- Call recording (Fireflies covers it).
- Email sequences or any automatic sending (Apollo or Harvey territory).
- Contact data purchase or mobile number lookup.
- Usage credits or metering for users.

## 13. Security, privacy and cost

- Model calls only from server workers; no keys in the browser.
- Prompts include only the fields needed; strip phone numbers and anything in a "private" note.
- Log every model call (workspace, job, tokens, cost estimate) to a table that powers a Settings, AI usage panel with month-to-date spend per workspace.
- Viewers see summaries and AI fields but never amounts, matching current rules.
- Team-facing copy follows existing TVA tone guidance.

## 14. Definition of done (per phase)

- Migrations, worker jobs, UI and NLDW tools shipped behind the feature flag.
- Tests: idempotency of each job, no-body-at-rest check, workspace isolation, approval path uses existing validation.
- /help updated with one short page per feature.
- One week on Labs with real data before enabling Academy B2B.
