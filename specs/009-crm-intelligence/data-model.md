# CRM Intelligence Layer — Data model

| | |
|---|---|
| **Feature** | `009-crm-intelligence` |
| **Spec** | [`spec.md`](spec.md) §13 (logical); this file chooses tables, types and names |
| **Plan** | [`plan.md`](plan.md) |
| **Builds on** | `007-crm` migrations 025–045 (data-model of `specs/007-crm/`), and every `008-tasks` migration (046–059) |

Everything here lands as numbered files in `supabase/migrations/`, in the
order of §1, **numbers 060–079 only** (coordination rule 1: `008` owns
046–059). Existing migrations are not edited. These migrations assume every
`008` migration has run (this branch is rebased on `008-tasks` before each
slice and before merge), and `008` never needs any of them.

---

## 1. Migrations

| File | Slice | Contents |
|---|---|---|
| `060_crm_ai_foundations.sql` | S1a | `crm_workspace_settings` key CHECK (`crm_workspace_settings_key_check`) widened with `ai`; an `ai` row INSERTed in every workspace (every phase off, budget 0); trigger `crm_workspaces_seed_ai` seeding the same row for a workspace created later; `crm_ai_usage` (log); `crm_ai_spend()`; `crm_job_runs.job` CHECK (`crm_job_runs_job_check`) widened (`summaries`, `meetings`, `enrichment`, `ai_fields`, `review_expiry`) and `crm_job_latest()` replaced; `crm_notifications.kind` CHECK (`crm_notifications_kind_check`) widened with `ai_budget_reached` |
| `061_crm_review_queue.sql` | S1b | `crm_review_items`; `crm_review_item_deals` (the deal choices, §3.2); `audit_log.suggestion_id`; `crm_audit_row()` replaced (fills `suggestion_id` from `crm.actor`); `crm_review_upsert()`, `crm_review_claim()`, `crm_review_release()`, `crm_review_expire()`, `crm_review_tombstone()`; `crm_purge()` and `crm_merge_companies()` replaced (review items) |
| `062_crm_email_summaries.sql` | S1c | `crm_email_summaries`, `crm_email_summary_links`; `crm_pending_threads()`; `crm_store_summary()`; `crm_remove_emails()` replaced (a thread left with no stored message loses its summary); `crm_purge()` and `crm_merge_companies()` replaced (summary links) |
| `063_crm_meeting_notes.sql` | S2a | `crm_notes` + `source_ref`, `ai_provenance`; partial unique index on `(workspace_id, source_ref)`; `crm_store_meeting_note()` |
| `064_crm_enrichment.sql` | S2b | `crm_company_enrichment` (log); trigger function `crm_queue_enrichment()` on `crm_companies` (insert, website change) and `crm_company_domains` (primary domain); `crm_merge_companies()` replaced (enrichment) |
| `065_crm_ai_fields.sql` | S3a | `crm_ai_fields` (audited configuration), `crm_ai_field_values` (log); the four default fields seeded (ICP fit in Labs only); `crm_purge()` and `crm_merge_companies()` replaced (field values) |
| `066_crm_follow_up_rules.sql` | S3b | `automations` and `flag_thresholds` rows of **every** workspace UPDATEd by merging the new keys; `crm_workspaces_seed_ai()` replaced so a new workspace also gets `automations` and `flag_thresholds` with the new keys (§3.7); `crm_campaign_members.status_changed_at` with its backfill from `audit_log` (touch and audit triggers disabled for the backfill, §3.9) and a BEFORE UPDATE trigger; `crm_notifications.kind` CHECK widened with `stuck_in_stage`; `crm_job_runs.job` CHECK widened with `followup_drafts` and `crm_job_latest()` replaced (plan §5.9, D-4 approved); `crm_lead_activity()`; `crm_no_reply_candidates()` |
| `067_crm_contact_recency.sql` | S4 | `contact_recency` on `crm_companies` and `crm_people`; `crm_set_contact_recency()` (the only writer); `crm_touch_updated_at()` and `crm_audit_row()` replaced (skip it); `crm_object_fields` rows (filter only); `crm_contact_facts()` |
| `068`–`079` | — | Reserved. S5 adds `068_crm_intelligence_fixes.sql` only if a sweep finds something, with the finding named in its header; a `068`+ file also re-copies a function if a late `008` migration replaced one of the seven below (plan §6 rule 1) |

**Replacing a function that `007` or `008` defined.** `009` replaces seven
existing functions: `crm_audit_row`, `crm_purge`, `crm_merge_companies` and
(067, only if its filter needs it) `crm_query_rank`, which `008`'s 048 also
replaces (the task-table rename changes their table names and "open" tests),
and `crm_remove_emails`, `crm_touch_updated_at` and `crm_job_latest`, which
`008` does not touch. After its S1a, `008` replaces none of the seven (`008`
data-model §1). Each `CREATE OR REPLACE` here is written by copying the **latest
definition in the rebased tree**, never from the `007` version, and adding only
what this file lists. The body is read from the freshly reset database after
the rebase, `SELECT pg_get_functiondef('public.<name>'::regproc)`; the file it
came from is found with `grep -l "CREATE OR REPLACE FUNCTION public.<name>("
supabase/migrations/*.sql | sort | tail -1` (not `"FUNCTION public.<name>"`,
which also matches trigger attachments such as `EXECUTE FUNCTION
public.crm_audit_row()` and gives the wrong file for `crm_audit_row` and
`crm_check_refs`). The task that writes each migration says so. A CHECK widened
here (job, kind, settings key) is rebuilt from the list current after `008`,
with `009`'s values added. These three CHECKs are unnamed inline column CHECKs
(`026`:110–112, `037`:60 and :92), so they are dropped by their
auto-generated names, `crm_workspace_settings_key_check`,
`crm_job_runs_job_check` and `crm_notifications_kind_check`, verified with
`pg_constraint` in T004 (and re-checked after `008`'s migrations), then
re-added under the same names. The precedent is `041`:76
(`crm_select_options_list_key_check`).

Each file opens with `-- NNN_name.sql` and a prose rationale citing FR and Q
ids, as `025`–`045` do, and a header listing what reverses it (§8). DDL is
idempotent (`IF NOT EXISTS`, `DROP … IF EXISTS` then `CREATE`, `ON CONFLICT DO
NOTHING`). Seeds and backfills run inside a `DO` block that first sets
`crm.actor` to the system actor, as `037` does.

---

## 2. Conventions

Every new table follows `007` data-model §2 (key, `workspace_id NOT NULL`,
`UNIQUE (workspace_id, id)` where referenced, composite FKs between `crm_*`
tables, RLS on with no policy, `REVOKE ALL … FROM anon, authenticated` in the
creating migration, `EXECUTE` on every new function revoked from `PUBLIC`,
`anon`, `authenticated` and granted to `service_role`).

Two rules are new to this feature:

| Rule | Detail |
|---|---|
| **No source text at rest** (P-3, NFR-CRMAI-01) | No column holds an email body, quoted text, signature, transcript or web page text. Generated text columns carry `CHECK (char_length(x) <= N)` with small `N`, so a mistake that tried to store a body fails loudly. Generated strings are masked for money (FR-CRMAI-LLM-05) in Python before any write; the database does not re-check this. |
| **Logs, not records** | AI output tables (`crm_ai_usage`, `crm_email_summaries` and links, `crm_company_enrichment`, `crm_ai_field_values`, `crm_review_items`, `crm_review_item_deals`) carry **no** `crm_audit` trigger. `test_crm_security_db.py` lists every unaudited `crm_*` table by name in `NOT_AUDITED` and requires an exact match, so the slice that creates each one adds it there with a one-line reason (§5): they are system output, rewritten often, and their text must be removable (Q-34), which the append-only `audit_log` would defeat. They are written only by the functions below or directly by the backend's service role. People's decisions are audited: approving goes through the existing audited write paths, and `crm_review_claim` / `crm_review_expire` write explicit `audit_log` rows with **no suggestion text**. `crm_ai_fields` (Admin configuration) is audited like any setting. |

---

## 3. New tables

### 3.1 `crm_ai_usage` — FR-CRMAI-LLM-06, -07, -10 (060) — log

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK `crm_workspaces` RESTRICT |
| `job` | text | NOT NULL `CHECK (job IN ('summaries','meetings','enrichment','ai_fields','followup_drafts'))` |
| `model` | text | NOT NULL, ≤ 100 |
| `input_tokens`, `output_tokens` | integer | NOT NULL `CHECK (>= 0)` |
| `cost_usd` | numeric(12,6) | NOT NULL `CHECK (cost_usd >= 0)`; estimated from the rate table at call time (plan §2.2) |
| `outcome` | text | NOT NULL `CHECK (outcome IN ('ok','invalid_output','failed','timed_out'))` |
| `at` | timestamptz | NOT NULL DEFAULT now() |

Index `(workspace_id, at DESC)`. No prompt or output text, no record id
(NFR-CRMAI-07: cost is administration). Read only by `crm_ai_spend()` and the
Admin-only `GET /crm/ai` (contracts §2.1). Not in the workspace export
(FR-CRMAI-CHG-11).

`crm_ai_spend(p_workspace uuid, p_from timestamptz) RETURNS jsonb`, `STABLE`:
`{"total_usd": "<decimal>", "by_job": {job: "<decimal>"}}` over rows with `at
>= p_from`. Python passes the start of the current Asia/Kolkata calendar month
(FR-CRMAI-LLM-07, Q-23). PostgREST aggregates are off, hence the function.

### 3.2 `crm_review_items` — FR-CRMAI-REV (061) — log with explicit audit rows

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK; `UNIQUE (workspace_id, id)` |
| `workspace_id` | uuid | NOT NULL FK |
| `type` | text | NOT NULL `CHECK (type IN ('next_step_change','task_from_commitment','task_from_meeting','deal_link','enrichment_conflict','rule_suggestion'))` |
| `status` | text | NOT NULL DEFAULT 'open' `CHECK (status IN ('open','approved','dismissed','expired'))` |
| `owner_id` | uuid | FK profiles SET NULL. NULL means "the workspace's Admins" (FR-CRMAI-REV-02); resolved in Python at creation |
| `record_type`, `record_id` | text, uuid | NOT NULL; the target: `company`, `person`, `opportunity` or `note` (`deal_link`). `CHECK (record_type IN ('company','person','opportunity','note'))`; `crm_check_refs()` trigger (same workspace) |
| `member_id` | uuid | NULL; R-4 only. Composite FK `(workspace_id, member_id) REFERENCES crm_campaign_members (workspace_id, id) ON DELETE CASCADE` |
| `suggestion` | jsonb | NOT NULL DEFAULT '{}', `CHECK (jsonb_typeof(suggestion) = 'object' AND pg_column_size(suggestion) <= 8192)`. Per type, §3.2.1. Emptied at tombstoning |
| `current_values` | jsonb | NOT NULL DEFAULT '{}'; the value of each field the item would replace, at creation (FR-CRMAI-REV-04). Emptied at tombstoning |
| `source_kind` | text | NOT NULL `CHECK (source_kind IN ('email_thread','meeting','enrichment','rule'))` |
| `source_key` | text | NOT NULL ≤ 300: `gmail:<mailbox_id>:<thread_id>`, `fireflies:<transcript_id>`, `enrichment:<company_id>`, `rule:<R-n>:<record or member id>` |
| `source_version` | text | NOT NULL ≤ 300: what makes the source "changed" (FR-CRMAI-REV-08): the summary watermark message id, the transcript's `dateUpdated`, the enrichment `generated_at`, or the rule trigger (§3.2.2) |
| `source_link` | jsonb | NOT NULL DEFAULT '{}': `{record_type, record_id}` of the note, company or deal to open, or `{mailbox_id, thread_id}`; never a URL except the Fireflies transcript link |
| `dedupe_key` | text | NOT NULL ≤ 400: `<source_kind>|<source_key>|<type>|<target>` where target is `record_type:record_id` (plus `member_id` for R-4, plus the commitment fingerprint for `task_from_*`, §3.2.2) |
| `model`, `generated_at` | text, timestamptz | provenance (P-2); NULL for deterministic R-2, R-3, R-4 items |
| `refusal` | text | ≤ 500; the last refusal of an approval (FR-CRMAI-REV-06: "the item stays open and shows the refusal") |
| `created_at` | timestamptz | NOT NULL DEFAULT now(); expiry counts from here (14 days) |
| `decided_at`, `decided_by` | timestamptz, uuid | set by `crm_review_claim` and `crm_review_expire` (`decided_by` NULL on expiry) |
| `result` | jsonb | NOT NULL DEFAULT '{}': what approval produced (`{task_id}`, `{opportunity_id}`, `{field}`), ids only |
| `tombstoned_at` | timestamptz | set 90 days after `decided_at` (FR-CRMAI-REV-07, Q-34) |

Indexes and constraints:

- `crm_review_items_open_once UNIQUE (workspace_id, dedupe_key) WHERE status = 'open'` (FR-CRMAI-REV-08: never a second open item with the same key).
- `(workspace_id, owner_id, status, created_at)`; `(workspace_id, status, created_at)` (Admin view, expiry); `(workspace_id, record_type, record_id)` (record lifecycle).
- `CHECK ((status = 'open') = (decided_at IS NULL))`; `CHECK (tombstoned_at IS NULL OR status <> 'open')`; `CHECK (type <> 'rule_suggestion' OR source_kind = 'rule')`.

The deal picker of `next_step_change` (Q-07) and `deal_link`
(FR-CRMAI-MEET-04) is a child table, not a `uuid[]` column. The security sweep
requires every `uuid[]` on a `crm_*` table to carry a `crm_check_refs()`
trigger naming it, and `crm_check_refs` checks array elements against
`crm_select_options`, so it would refuse every deal id.

`crm_review_item_deals` (061) — log:

| Column | Type | Constraints |
|---|---|---|
| `workspace_id` | uuid | NOT NULL |
| `item_id` | uuid | NOT NULL; composite FK `(workspace_id, item_id) REFERENCES crm_review_items (workspace_id, id) ON DELETE CASCADE` |
| `opportunity_id` | uuid | NOT NULL; composite FK `(workspace_id, opportunity_id) REFERENCES crm_opportunities (workspace_id, id) ON DELETE CASCADE` |

PK `(item_id, opportunity_id)`; index `(workspace_id, opportunity_id)`. RLS on,
no policy, revoked from browser roles. `crm_review_upsert` replaces an item's
rows from the plan's `deal_choices` and checks each is an open deal of the
workspace (`CRM04`). The API still presents them as `deal_choices`
(contracts §2.2.1).

#### 3.2.1 `suggestion` and `current_values` per type

| Type | `suggestion` | `current_values` (staleness) |
|---|---|---|
| `next_step_change` | `{next_step, next_step_on}` | `{<deal_id>: {next_step, next_step_on}}` for every deal in the item's `crm_review_item_deals` rows |
| `task_from_commitment`, `task_from_meeting` | `{title (≤ 200), due_on, owner_id \| null, owner_unknown: bool}` | `{}` (creates only, no staleness) |
| `deal_link` | `{note_id}` | `{}` |
| `enrichment_conflict` | `{field: industry_id\|size_id\|city\|country\|description\|linkedin, value, label}` | `{<field>: <value at creation>}` |
| `rule_suggestion` | `{rule: "R-1"\|"R-2"\|"R-3"\|"R-4", options: [...], draft? (R-1, ≤ 2,000), to_stage?}`; options per FR-CRMAI-RULE-05/-06 (`nurturing`, `disqualified`, `next_touch`, `qualify`, `close_out`, `mark_used`) | R-3: `{lead_stage}`; R-4: `{member_status}`; R-1: `{}` |

All strings are generated text, money-masked before `crm_review_upsert` is
called.

#### 3.2.2 Dedupe and "the source changed"

`crm_review_upsert` inserts with `ON CONFLICT (workspace_id, dedupe_key) WHERE
status = 'open' DO UPDATE` of `suggestion`, `current_values`, the item's deal rows,
`source_version`, `model`, `generated_at` (a newer summary refreshes the open
item instead of adding a second). Before inserting, it skips the item when a
**decided** item with the same `dedupe_key` and the same `source_version`
exists (dismissed or expired items are not re-created from an unchanged
source). R-3 additionally checks Q-40 in Python (no new item within
`lead_cold_days` of a dismissal). The `task_from_*` target part ends with
`#<fingerprint>`: `sha256(lower(normalised title) || due_on)` truncated to 16
hex characters, so a re-summary does not repeat a commitment already
suggested on the thread (FR-CRMAI-SUM-07). R-1's key is `rule:R-1:<mailbox
id>:<thread id>:<outbound gmail message id>` (FR-CRMAI-RULE-02).

#### 3.2.3 Functions

| Function | Does |
|---|---|
| `crm_review_upsert(p_actor jsonb, p_items jsonb) RETURNS jsonb` | Inserts or refreshes items as above, all in the actor's workspace; writes each item's `crm_review_item_deals` from its `deal_choices`, checked against the workspace's open deals (`CRM04`). Returns `[{id, created: bool}]`. Actor kind `sync` |
| `crm_review_claim(p_actor jsonb, p_item_id uuid, p_status text, p_expect_status text DEFAULT 'open') RETURNS jsonb` | `UPDATE … SET status = p_status, decided_at = now(), decided_by = actor.user_id, refusal = NULL WHERE id AND workspace_id AND status = 'open'`; zero rows → `CRM09` "This suggestion was already decided." Inserts one `audit_log` row (`review.approved` / `review.dismissed`, `target_table 'crm_review_items'`, `before {status: open}`, `after {status}`, `suggestion_id` = the item). No text |
| `crm_review_release(p_actor jsonb, p_item_id uuid, p_refusal text, p_result jsonb) RETURNS void` | Either reopens a claimed item after the change was refused (`status = 'open'`, `decided_* = NULL`, `refusal = p_refusal`, audit row `review.reopened`), or records `result` when `p_refusal` is NULL |
| `crm_review_expire(p_actor jsonb, p_before timestamptz) RETURNS integer` | Open items of the workspace with `created_at < p_before` → `expired`, one `review.expired` audit row each (FR-CRMAI-REV-07) |
| `crm_review_tombstone(p_actor jsonb, p_before timestamptz) RETURNS integer` | Decided items with `decided_at < p_before` and no tombstone: `suggestion = '{}'`, `current_values = '{}'`, `source_link = '{}'`, `refusal = NULL`, `tombstoned_at = now()`; keeps `dedupe_key`, `source_version`, `status` (Q-34) |

### 3.3 `crm_email_summaries`, `crm_email_summary_links` — FR-CRMAI-SUM (062) — logs

`crm_email_summaries`:

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | `UNIQUE (workspace_id, id)` |
| `mailbox_id` | uuid | NOT NULL; composite FK `(workspace_id, mailbox_id) REFERENCES crm_mailboxes (workspace_id, id) ON DELETE CASCADE` (adds `UNIQUE (workspace_id, id)` to `crm_mailboxes` if `039` did not) |
| `thread_id` | text | NOT NULL ≤ 200 (Gmail thread id) |
| `summary` | text | NOT NULL, btrim, ≤ 600 (2–3 sentences, FR-CRMAI-SUM-04) |
| `asks` | jsonb | NOT NULL DEFAULT '[]'; array of strings ≤ 200 each, at most 10 |
| `commitments` | jsonb | NOT NULL DEFAULT '[]'; `[{text ≤ 200, owner_id uuid\|null, owner_unknown bool, due_on date\|null}]`, at most 10 |
| `next_step` | text | ≤ 500, NULL |
| `next_step_on` | date | NULL; `CHECK (next_step_on IS NULL OR next_step IS NOT NULL)` |
| `sentiment` | text | NOT NULL `CHECK (sentiment IN ('positive','neutral','concern'))` |
| `watermark_message_id` | text | NOT NULL; Gmail id of the latest qualifying message covered (FR-CRMAI-SUM-02) |
| `watermark_at` | timestamptz | NOT NULL; its `sent_at` |
| `model` | text | NOT NULL |
| `generated_at` | timestamptz | NOT NULL |
| `created_at`, `updated_at` | timestamptz | |

`UNIQUE (mailbox_id, thread_id)` (Q-06: one latest summary per thread). Index
`(workspace_id, updated_at DESC)`. The array shapes are checked in Python
(pydantic) before `crm_store_summary`.

`crm_email_summary_links`: `id`, `workspace_id`, `summary_id` (composite FK
CASCADE), `record_type CHECK IN ('company','person','opportunity')`,
`record_id`; `UNIQUE (summary_id, record_type, record_id)`; index
`(workspace_id, record_type, record_id)`; `crm_check_refs()` trigger. Links
are the thread's companies and people and **every open deal** its messages
link to (Q-07, FR-CRMAI-SUM-05).

Functions:

| Function | Does |
|---|---|
| `crm_pending_threads(p_workspace uuid, p_limit int) RETURNS jsonb`, `STABLE` | Threads `(mailbox_id, thread_id)` of active mailboxes (`is_active`, FR-CRMAI-SUM-10) in the workspace that hold a qualifying message (`NOT is_internal AND NOT is_auto`, linked to a company or person, `crm_email_messages.created_at > crm_mailboxes.backfilled_at`, FR-CRMAI-SUM-11) whose latest qualifying message is newer than the summary watermark, or that have no summary. Returns `[{mailbox_id, thread_id, message_ids: [gmail ids in order], latest: {gmail_id, sent_at}}]`, oldest first, at most `p_limit`. Ids only |
| `crm_store_summary(p_actor jsonb, p_plan jsonb) RETURNS jsonb` | One transaction: re-checks that the plan's watermark is still the thread's latest qualifying message (else `CRM09`, the job retries next tick); upserts the summary by `(mailbox_id, thread_id)`; replaces its links; calls `crm_review_upsert` with the plan's items. Actor kind `sync` |
| `crm_remove_emails()` (replaced) | As `039` (or `008`'s version), plus: a thread of the mailbox left with no stored message loses its summary in the same transaction (FR-CRMAI-SUM-02) |

### 3.4 Meeting notes — FR-CRMAI-MEET (063): columns on `crm_notes`

```sql
ALTER TABLE crm_notes
  ADD COLUMN IF NOT EXISTS source_ref text CHECK (char_length(source_ref) <= 200),
  ADD COLUMN IF NOT EXISTS ai_provenance jsonb
      CHECK (ai_provenance IS NULL OR jsonb_typeof(ai_provenance) = 'object');
CREATE UNIQUE INDEX IF NOT EXISTS crm_notes_source_ref
  ON crm_notes (workspace_id, source_ref) WHERE source_ref IS NOT NULL;
```

- `source_ref`: `fireflies:<transcript_id>` (P-5: idempotent on transcript id and workspace, FR-CRMAI-MEET-07).
- `ai_provenance`: `{source_kind: "meeting", model, generated_at, transcript_updated_at, transcript_url}`. The only URL stored (FR-CRMAI-LLM-04).
- No other note columns (Q-11). Title, attendees, summary, decisions and action items live in `body`, written as plain text with fixed headings.

`crm_store_meeting_note(p_actor jsonb, p_plan jsonb) RETURNS jsonb`: one
transaction. Looks up the note by `(workspace_id, source_ref)`. None → inserts
the note (kind `meeting`, `occurred_on` the meeting day) through the same
`crm_notes` insert path `crm_apply` uses, so the audit trigger writes
`note.created` with actor kind `sync`; inserts its `crm_note_links`
(companies, people, and the one deal when FR-CRMAI-MEET-04 found it). Found
and `p_plan.transcript_updated_at` is newer → updates `body` and
`ai_provenance` **only if** no `audit_log` row with `target_table =
'crm_notes'`, `target_id = note.id`, `actor_label = 'user'` and an action other
than `note.created` exists (a person edited it, Q-41); otherwise changes
nothing and returns `{edited_by_person: true}`. Then calls
`crm_review_upsert` with the plan's `task_from_meeting`, `deal_link` and
`next_step_change` items. Returns `{note_id, created, updated}`.

### 3.5 `crm_company_enrichment` — FR-CRMAI-ENR (064) — log

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `company_id` | uuid | NOT NULL; composite FK `(workspace_id, company_id) REFERENCES crm_companies (workspace_id, id) ON DELETE CASCADE`; `UNIQUE (workspace_id, company_id)` |
| `website` | text | NOT NULL ≤ 500; the normalised site enriched or queued (`https://host/`). With `company_id`, the idempotency key (Q-33) |
| `status` | text | NOT NULL `CHECK (status IN ('queued','done','failed'))` |
| `failure_reason` | text | `CHECK (failure_reason IS NULL OR failure_reason IN ('Website did not respond','No company information found','Output did not match the schema','Model call failed','Model call timed out','AI budget reached'))`; set iff `status = 'failed'`. ENR-06's two sentences plus LLM-03's four model reasons; nothing else (D-2 cut, plan §11.1): a host the URL policy refuses (CT-3) is recorded as 'Website did not respond' |
| `values` | jsonb | NOT NULL DEFAULT '{}': `{industry, sub_industry, employee_band, city, country, description, linkedin, products: []}`, each a masked string ≤ 300 (description ≤ 300, products ≤ 8 × 60) |
| `model`, `generated_at` | text, timestamptz | provenance |
| `queued_at` | timestamptz | NOT NULL DEFAULT now() |
| `requested_by` | uuid | FK profiles SET NULL; the Admin of **Enrich selected**, NULL for a trigger |

Index `(workspace_id, status, queued_at) WHERE status = 'queued'`. Status lives
here, never on `crm_companies`, so it neither moves `updated_at` nor writes an
audit row (FR-CRMAI-ENR-06).

`crm_queue_enrichment()` (trigger, `SECURITY INVOKER`): `AFTER INSERT OR
UPDATE OF website ON crm_companies` and `AFTER INSERT OR UPDATE OF is_primary,
domain ON crm_company_domains`. Computes the company's site (`website->>'url'`
normalised, else `https://<primary domain>/`). When there is one and the
company is not deleted, upserts `(workspace_id, company_id)`: a new row as
`queued`; an existing row whose `website` differs is reset to `queued` with
the new site (Q-32). Same site → nothing. This is the only enrichment work
inside an import or web transaction (FR-CRMAI-ENR-07); it runs whatever the
phase switch says (queuing is free; the job checks the switch).

### 3.6 `crm_ai_fields`, `crm_ai_field_values` — FR-CRMAI-FIELD (065)

`crm_ai_fields` (audited configuration; written through `crm_apply` by
Admins):

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | `UNIQUE (workspace_id, id)` |
| `key` | text | NOT NULL `^[a-z][a-z0-9_]{1,40}$`; `UNIQUE (workspace_id, key)`; seeds use `icp_fit`, `account_brief`, `deal_outlook`, `what_is_blocking` |
| `name` | text | NOT NULL, btrim, ≤ 60 |
| `record_type` | text | NOT NULL `CHECK (record_type IN ('company','opportunity'))` |
| `prompt` | text | NOT NULL ≤ 2,000 (Admin-written instruction; never source text) |
| `output_type` | text | NOT NULL `CHECK (output_type IN ('text','single_select','number'))` |
| `options` | text[] | NOT NULL DEFAULT '{}'; `CHECK ((output_type = 'single_select') = (cardinality(options) BETWEEN 2 AND 12))` |
| `max_lines` | smallint | NULL; text fields (3 for Account brief, 1 for What is blocking). This and `depends_on`, `with_reason`, `position` are set by the 065 seeds only and are not editable through the API (D-3 cut, plan §11.1); a field an Admin creates keeps their defaults |
| `depends_on` | jsonb | NULL; `{"field": "deal_outlook", "values": ["At risk","Stalled"]}` (What is blocking, FR-CRMAI-FIELD table) |
| `with_reason` | boolean | NOT NULL DEFAULT false (Deal outlook's one-line reason) |
| `enabled` | boolean | NOT NULL DEFAULT true |
| `position` | numeric | NOT NULL DEFAULT 0 |
| stamps | | `created_*`, `updated_*`, `crm_audit` trigger, `touch_updated_at` |

`crm_ai_field_values` (log):

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `field_id` | uuid | NOT NULL; composite FK CASCADE |
| `record_type`, `record_id` | text, uuid | `CHECK (record_type IN ('company','opportunity'))`; `crm_check_refs()` |
| `value_text` | text | ≤ 600, NULL |
| `value_number` | smallint | `CHECK (value_number BETWEEN 0 AND 100)`, NULL |
| `reason` | text | ≤ 200, NULL |
| `inputs_hash` | text | NOT NULL; hash of the inputs used (FR-CRMAI-FIELD-04: recompute only when they changed) |
| `model`, `generated_at` | text, timestamptz | provenance |
| `dirty_at` | timestamptz | NULL; set when an input changed inside the hour (the trailing recompute, Q-38) |
| `next_allowed_at` | timestamptz | NOT NULL; `generated_at + 1 hour` |

`UNIQUE (field_id, record_id)`; index `(workspace_id, record_type,
record_id)`; partial index `(workspace_id, dirty_at) WHERE dirty_at IS NOT
NULL`. No column on `crm_companies` or `crm_opportunities` (FR-CRMAI-FIELD-05).

Seeds (065, every workspace unless noted; each `enabled`):

| key | Name | Record | Output | Seeded in |
|---|---|---|---|---|
| `icp_fit` | ICP fit | company | single_select: Factory Brain, Academy, Services, DeepWeaver, None | `labs` only (Q-12) |
| `account_brief` | Account brief | company | text, 3 lines | both |
| `deal_outlook` | Deal outlook | opportunity | single_select: On track, At risk, Stalled; `with_reason` | both (Q-13) |
| `what_is_blocking` | What is blocking | opportunity | text, 1 line; `depends_on` Deal outlook At risk or Stalled | both |

### 3.7 Settings — FR-CRMAI-FLAG-01, -02, FR-CRMAI-RULE-01 (060, 066)

`crm_workspace_settings.key` CHECK becomes the `008` list plus `ai`. It is an
unnamed inline CHECK (`026`:110–112), so 060 drops
`crm_workspace_settings_key_check` by that auto-generated name and re-adds it
(the precedent is `041`:76, `crm_select_options_list_key_check`).

| Key | Value | Migration |
|---|---|---|
| `ai` (new) | `{"budget_usd": 0, "phases": {"1": false, "2": false, "3": false, "4": false}}` | 060: INSERT for every workspace (`ON CONFLICT DO NOTHING`); trigger `crm_workspaces_seed_ai AFTER INSERT ON crm_workspaces` inserts the same for a new workspace (FR-CRMAI-FLAG-02) |
| `automations` (merged) | `'{"no_reply_draft": false, "stuck_notice": false, "lead_cold": false, "campaign_followthrough": false}'::jsonb \|\| value` | 066: `UPDATE … SET value = defaults \|\| value` for every row, then the INSERT seed for any workspace missing the key carries all keys |
| `flag_thresholds` (merged) | `'{"stuck_discovery_days": 14, "stuck_qualification_days": 21, "stuck_proposal_days": 14, "stuck_negotiation_days": 21, "no_reply_days": 5, "lead_cold_days": 30}'::jsonb \|\| value` | 066, likewise |

**New workspaces.** `037` seeded `automations` and `flag_thresholds` only for
workspaces that existed then, and `automation_on()` reads a missing key as on.
066 therefore replaces `crm_workspaces_seed_ai()` (060) so that, besides `ai`,
it inserts `automations` (the `037` defaults plus the four new keys `false`)
and `flag_thresholds` (the `037` defaults plus the six new parameters), `ON
CONFLICT DO NOTHING`. A workspace created after 066 reads every rule switch as
off.

The merge is written `defaults || value`, so a key the row already has keeps
its stored value and only missing keys are added. For keys new to every row
this is the same as the spec's `value || defaults`, and it stays safe if the
migration is re-run after an Admin changed one.

### 3.8 Jobs and notifications (060, 066)

- `crm_job_runs.job` CHECK (`crm_job_runs_job_check`, inline in `037`:60, dropped by name): the current list plus `summaries`, `meetings`, `enrichment`, `ai_fields`, `review_expiry` (060) and `followup_drafts` (066, D-4 approved; spec FR-CRMAI-JOB-01). `crm_job_latest()` replaced: its `unnest(ARRAY[…])` gains the five names (060) and `followup_drafts` (066) (a job that never ran is still not reported, `007` FR-CRM-JOB-03).
- `crm_notifications.kind` CHECK (`crm_notifications_kind_check`, inline in `037`:92, dropped by name): the current list plus `ai_budget_reached` (060) and `stuck_in_stage` (066). Dedupe keys: `ai_budget:<workspace_id>:<YYYY-MM>`; `stuck:<deal_id>:<stage>:<stage_entered_at>`.

### 3.9 `crm_campaign_members.status_changed_at` — FR-CRMAI-RULE-06 (066)

```sql
ALTER TABLE crm_campaign_members ADD COLUMN IF NOT EXISTS status_changed_at timestamptz;
ALTER TABLE crm_campaign_members
  DISABLE TRIGGER crm_campaign_members_touch, DISABLE TRIGGER crm_campaign_members_crm_audit;
-- backfill: the latest audit row whose after-values carry a status for the member,
-- else updated_at
ALTER TABLE crm_campaign_members
  ENABLE TRIGGER crm_campaign_members_touch, ENABLE TRIGGER crm_campaign_members_crm_audit;
ALTER TABLE crm_campaign_members
  ALTER COLUMN status_changed_at SET DEFAULT now(),
  ALTER COLUMN status_changed_at SET NOT NULL;
```

The backfill runs with the table's two row triggers disabled (both created in
`041`:231–239; the trigger names are verified in T134). Otherwise the generic
`touch_updated_at()` would set every member's `updated_at` to now, and
`crm_audit_row` would write one `campaign_member.updated` row per member under
the system actor. The whole migration runs in one transaction, so the
triggers cannot stay disabled. A db test (T152) checks that every member's
`updated_at` and the `audit_log` row count are unchanged by the migration.

Trigger `crm_campaign_members_status_changed BEFORE UPDATE OF status`: sets
`NEW.status_changed_at = now()` when `NEW.status IS DISTINCT FROM OLD.status`.
Every write path (PATCH, convert, import, merge) is covered without touching
them.

### 3.10 Contact recency — FR-CRMAI-REL (067)

```sql
ALTER TABLE crm_companies ADD COLUMN IF NOT EXISTS contact_recency text
  CHECK (contact_recency IN ('recent','fading','lapsed'));
ALTER TABLE crm_people    ADD COLUMN IF NOT EXISTS contact_recency text
  CHECK (contact_recency IN ('recent','fading','lapsed'));
```

NULL until the first recompute. It is written **only** by
`crm_set_contact_recency(p_workspace uuid, p_table text, p_rows jsonb) RETURNS
integer` (067), modelled on `038`'s `crm_set_flags`: `p_table` is
`crm_companies` or `crm_people`, `p_rows` is `[{id, level}]`, and it updates
only `contact_recency` for rows of that workspace. It does not go through
`crm_apply`, which sets `updated_by` from the actor (NULL for `sync`) on every
update and would both erase "last updated by" and, because `updated_by` is
not in the touch skip set, move `updated_at`. `crm_touch_updated_at()` skips an update whose
changed columns are within `{flag_level, flags, flags_computed_at,
contact_recency}`; `crm_audit_row()` adds `contact_recency` to the same skip
set (FR-CRMAI-REL-02). `crm_object_fields` gains `('company',
'contact_recency', 'contact_recency', 'fixed_select', …)` and the same for
`person`, filterable only (not sortable, not groupable, not an amount, all
workspaces). If the rebased `crm_query()` needs a `crm_query_rank()` branch to
filter a `fixed_select` column it does not know, 067 replaces
`crm_query_rank()` with one more branch (`recent`, `fading`, `lapsed`); the
S4 migration task reads the rebased `crm_query` first and decides (tasks T156).

`crm_contact_facts(p_workspace uuid, p_now timestamptz) RETURNS jsonb`,
`STABLE`: per company and person of the workspace, whether in the last 30 days
there was an inbound counted email, an outbound counted email, a meeting note
(kind `meeting`, not deleted, `occurred_on`), and whether any counted contact
happened in the last 90 days, a company including its people's. Counted email:
stored, not internal, not auto (FR-CRMAI-REL-01).

### 3.11 Facts for the rules (066)

| Function | Returns |
|---|---|
| `crm_lead_activity(p_workspace uuid, p_company_ids uuid[]) RETURNS jsonb`, `STABLE` | Per company, the latest of: `last_contact_at`; a note linked to it; a task linked to it created or completed (the `008` task tables); a lead stage change (`audit_log` `company.updated` with `lead_stage` in the diff); activity on any of its deals (`updated_at`, stage history, linked notes and tasks) (Q-15) |
| `crm_no_reply_candidates(p_workspace uuid, p_before timestamptz) RETURNS jsonb`, `STABLE` | Outbound counted messages sent before `p_before` that are the latest counted message of their thread and have no later inbound on any linked person or company: `[{mailbox_id, thread_id, gmail_message_id, sent_at, people: [], companies: []}]`. Ids only |

### 3.12 `audit_log.suggestion_id` — FR-CRMAI-REV-05 (061)

```sql
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS suggestion_id uuid;
CREATE INDEX IF NOT EXISTS audit_log_suggestion ON audit_log (suggestion_id)
  WHERE suggestion_id IS NOT NULL;
```

No FK: `audit_log` is append-only (`005`), so `ON DELETE SET NULL` would be an
UPDATE the append-only trigger refuses, and items of a purged record are
deleted. `crm_audit_row()` writes `(current_setting('crm.actor')::jsonb ->>
'suggestion_id')::uuid`. The backend sets that key on the actor only on the
approval path (plan §5.4): on the CRM actor for a record change, and through
`008`'s `TaskActor.suggestion_id` (`008` contracts §4) for an approved task, so
the task's own audit rows carry it too.

---

## 4. Record lifecycle — FR-CRMAI-CHG-11

| Event | Effect | Where |
|---|---|---|
| Trash | Reads leave out summaries linked only to trashed records, enrichment and AI values of trashed records, and items whose target is trashed; an item whose target is trashed is refused on decide (422 "This record is in the Trash.") | services (read filters) |
| Restore | Nothing to do: the rows were never changed | — |
| Purge | `crm_purge()` (061, 062, 065 versions) also deletes `crm_review_items` targeting the record (`record_type`, `record_id`), `crm_email_summary_links` and `crm_ai_field_values` of it. A purged deal's `crm_review_item_deals` rows go by FK CASCADE. Enrichment goes by FK CASCADE with the company. Notes' `source_ref` goes with the note. Its table-to-record-type CASE is unchanged (no new purgeable table) | 061, 062, 065 |
| Company merge | `crm_merge_companies()` (061, 062, 064, 065 versions) moves summary links, open items (recomputing `dedupe_key`; an item whose new key collides with an open item on the survivor is expired), enrichment (the survivor's row wins; else the merged one is moved) and AI field values (the survivor's win; else moved, `dirty_at = now()`) | 061, 062, 064, 065 |
| Export | `GET /crm/export/workspace` gains `email_summaries` (with links), `review_items` (with their deal choices), `enrichment`, `ai_fields`, `ai_field_values`; never `crm_ai_usage`. `export.py`'s `_all_rows` reads `objects.TABLES[kind]` and `present_rows()`, which know record kinds only, so each AI object gets its own reader and presenter branch (ids only, rows whose target is trashed left out per the Trash line). `test_crm_csv_io.py`'s exact set of export objects is extended in the same slice | service |

---

## 5. Grants and security

```sql
REVOKE ALL ON FUNCTION crm_review_upsert(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION crm_review_upsert(jsonb, jsonb) TO service_role;
-- and the same for every function this file adds or replaces
```

Every new table: `ENABLE ROW LEVEL SECURITY`, no policy, `REVOKE ALL … FROM
anon, authenticated` in its creating migration. `007`'s `test_crm_security_db.py`
enumerates every table and function in `public`, so it covers RLS, grants and
`EXECUTE` for these without change. It does **not** cover them without change
in one respect: `NOT_AUDITED` names every `crm_*` table without a
`crm_audit_row` trigger and the test requires an exact match. Each slice that
creates an unaudited table adds it to `NOT_AUDITED` with a one-line reason:
`crm_ai_usage` (S1a, T027), `crm_review_items` and `crm_review_item_deals`
(S1b, T054), `crm_email_summaries` and `crm_email_summary_links` (S1c, T080),
`crm_company_enrichment` (S2b, T115), `crm_ai_field_values` (S3a, T131).
`crm_ai_fields` is audited and is not added. No new `uuid[]` column is
created, so the option-array sweep needs no change. S5 (T170) re-runs the file
and confirms the list.

---

## 6. Indexes (NFR-CRMAI-05)

| Need | Index |
|---|---|
| Pending threads per tick | `crm_email_messages (workspace_id, thread_id)` exists (039); `crm_email_summaries (mailbox_id, thread_id)` unique |
| A record's summaries | `crm_email_summary_links (workspace_id, record_type, record_id)` |
| My open items, oldest first | `crm_review_items (workspace_id, owner_id, status, created_at)` |
| Expiry | `crm_review_items (workspace_id, status, created_at)` |
| Enrichment queue | `crm_company_enrichment (workspace_id, status, queued_at) WHERE status = 'queued'` |
| Month spend | `crm_ai_usage (workspace_id, at DESC)` |
| Meeting idempotency | `crm_notes (workspace_id, source_ref) WHERE source_ref IS NOT NULL` unique |

---

## 7. Seeds summary

| What | Migration |
|---|---|
| `ai` setting, every workspace, off, budget 0; and for new workspaces | 060 |
| Four AI fields (ICP fit Labs only) | 065 |
| New `automations` and `flag_thresholds` keys merged into every row | 066 |
| `automations` and `flag_thresholds` (with the new keys off) for workspaces created later, through `crm_workspaces_seed_ai()` | 066 |
| `contact_recency` registry rows | 067 |

---

## 8. Reversibility (constitution §III)

| Migration | Reverse |
|---|---|
| 060 | `DROP TRIGGER crm_workspaces_seed_ai`; `DELETE FROM crm_workspace_settings WHERE key = 'ai'` (under a system actor); restore the key CHECK; `DROP TABLE crm_ai_usage`; `DROP FUNCTION crm_ai_spend`; restore the job and kind CHECKs (after deleting rows using the new values) and the previous `crm_job_latest()` |
| 061 | `audit_log.suggestion_id` **cannot be dropped in practice** (append-only history); the file says so. Drop the review functions, `crm_review_item_deals` and `crm_review_items`; restore the previous `crm_audit_row`, `crm_purge`, `crm_merge_companies` |
| 062 | Drop the summary functions and tables; restore the previous `crm_remove_emails`, `crm_purge`, `crm_merge_companies` |
| 063 | Drop the index, `crm_store_meeting_note`, and the two `crm_notes` columns (notes stay as plain notes) |
| 064 | Drop the triggers, `crm_queue_enrichment`, `crm_company_enrichment`; restore the previous `crm_merge_companies` |
| 065 | Drop `crm_ai_field_values`, `crm_ai_fields` (their audit rows stay); restore the previous `crm_purge`, `crm_merge_companies` |
| 066 | Remove the merged keys from `automations` and `flag_thresholds` (`value - '{…}'::text[]`); restore the 060 `crm_workspaces_seed_ai()`; drop the trigger and `status_changed_at`; restore the kind and job CHECKs (after deleting rows using the new values) and the 060 `crm_job_latest()`; drop the two fact functions |
| 067 | Delete the registry rows; restore the previous `crm_touch_updated_at`, `crm_audit_row` (and `crm_query_rank` if replaced); drop `crm_set_contact_recency`, the two columns and `crm_contact_facts` |
