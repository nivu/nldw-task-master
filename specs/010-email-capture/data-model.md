# Automatic Email Capture — Data model

| | |
|---|---|
| **Feature** | `010-email-capture` |
| **Spec** | [`spec.md`](spec.md) §11 (indicative); this file chooses tables, types and names |
| **Plan** | [`plan.md`](plan.md) |
| **Builds on** | migrations 001–070 as on `main` (`007` 025–045, `008` 046–059, `009` 060–070) |

Everything lands as numbered files in `supabase/migrations/`, **071–073**, in
the order of §1. Existing migrations are not edited. `071`–`079` are the
range `010`'s security sweep reads (plan PD-1).

---

## 1. Migrations

| File | Slice | Contents |
|---|---|---|
| `071_crm_capture_sync.sql` | S1 | `crm_mailboxes` + `capture_source`, `capture_start_date`, `docs_checked_at`, `reconcile_after`; `crm_capture_candidates` (log); `crm_capture_mark()`; `crm_audit_row()` replaced (skip `docs_checked_at`); `crm_review_items_type_check` + `deal_signal` |
| `072_crm_capture_discovery.sql` | S2 | `crm_workspace_settings_key_check` + `capture`; `capture` row seeded `{"mode":"off"}` in every workspace; `crm_workspaces_seed_ai()` replaced (seeds `capture`); `crm_capture_dry_runs` (log); `crm_capture_record()`, `crm_capture_start_dry_run()`, `crm_capture_set_mode()`, `crm_capture_finish_dry_run()`, `crm_capture_recall_add()`, `crm_capture_report()`; `crm_job_runs_job_check` + `capture` and `crm_job_latest()` replaced; `crm_ai_usage_job_check` + `capture` |
| `073_crm_capture_live.sql` | S3 | `crm_capture_ignores` (audited configuration); `crm_review_items`: `record_type`/`record_id` nullable for `new_lead` only, `crm_review_items_type_check` + `new_lead`; `crm_capture_apply()`; `crm_capture_purge()`; `crm_capture_supersede()` |
| `074_crm_capture_improvements.sql` | R1 | Revision 1 (spec §14): mailbox rescan columns; `crm_audit_row()` from 071; `crm_capture_group_upsert()`, `crm_capture_release_thread()`, `crm_capture_consolidate()`; `crm_review_release()` from 068; `capture_routing` settings key (key CHECK and `crm_workspaces_seed_ai()` from 072) (§9) |

**Replacing a function.** Each `CREATE OR REPLACE` is copied from its latest
definition in the tree (`grep -l "CREATE OR REPLACE FUNCTION public.<fn>("
supabase/migrations/*.sql | sort | tail -1`), with only lines marked `-- 0NN`
added, and the header says so. Today: `crm_audit_row` → `067`;
`crm_workspaces_seed_ai` → `066`; `crm_job_latest` → `066`. `crm_review_upsert`
(`069`), `crm_purge` and `crm_merge_companies` (`065`) and `crm_check_refs`
(`048`) are **not** replaced: they already pass a NULL `record_id` through
(`crm_check_refs` skips NULL; `crm_purge` matches by record; the merge
re-keys `|company:` heads only).

**Header convention** (as `060`–`070`): file name; `Spec 010 FR ids;
data-model §; plan §`; a numbered list of what the file does; the
replaced-function provenance line; a `Reversible:` paragraph (§8).

---

## 2. Conventions

- New tables: `workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON
  DELETE RESTRICT`; `CONSTRAINT <t>_workspace_id UNIQUE (workspace_id, id)`;
  composite foreign keys between `crm_*` tables; `ENABLE ROW LEVEL SECURITY`;
  `REVOKE ALL ... FROM anon, authenticated`; no policies.
- Every text column has a `char_length` cap. No body, no subject, no
  snippet column anywhere (NFR-CAP-02).
- Functions: `crm_` prefix; `LANGUAGE plpgsql` (or `sql`) `SET search_path =
  public, extensions`; refusals `RAISE EXCEPTION USING ERRCODE = 'CRM22' |
  'CRM04' | 'CRM09', MESSAGE = '<one sentence>'`. A function that writes an
  audited table sets `crm.actor` from `p_actor` itself (transaction-scoped).
  Every new or replaced function ends with `REVOKE ALL ON FUNCTION ... FROM
  PUBLIC, anon, authenticated; GRANT EXECUTE ... TO service_role;`.
- CHECK widening: `ALTER TABLE ... DROP CONSTRAINT IF EXISTS <name>; ADD
  CONSTRAINT <name> CHECK (...)` with the same name. Names are confirmed with
  `pg_constraint` before writing (tasks T101, T201, T301).
- Seeds run inside `DO $$ ... PERFORM set_config('crm.actor',
  '{"kind":"system","user_id":null,"channel":null,"workspace_id":null}', true);
  ... $$` with `ON CONFLICT DO NOTHING`.
- Logs (`crm_capture_candidates`, `crm_capture_dry_runs`) carry no audit
  trigger and join `NOT_AUDITED`; configuration (`crm_capture_ignores`) gets
  `crm_audit_row` and the touch trigger.

---

## 3. Tables and columns

### 3.1 `crm_mailboxes` — FR-CAP-SRC-01, -06, -07 (071) — audited, existing

| Column | Type | Constraints |
|---|---|---|
| `capture_source` | text | NOT NULL DEFAULT `'labels'` `CHECK (capture_source IN ('labels','primary_sent'))` |
| `capture_start_date` | date | NOT NULL DEFAULT `'2026-04-01'` |
| `docs_checked_at` | timestamptz | NULL; the deal-document search's cursor (FR-CAP-SRC-06) |
| `reconcile_after` | timestamptz | NULL; set by the mailbox PATCH when `capture_source` goes from `primary_sent` to `labels`, or when a `primary_sent` mailbox's start date moves later (the earliest start date captured from, 00:00 IST); the next reconcile on *Labels only* looks back to it, then clears it (plan §5.3, PD-3) |

- `ADD COLUMN` with defaults fills every existing mailbox with `labels` and
  `2026-04-01`: today's behaviour (spec §13 S1 line 1). The ALTER writes no
  audit row (DDL).
- `crm_audit_row()` replaced from `067`: the mailbox cursor skip becomes
  `changed <@ ARRAY['history_id','page_token','backfilled_at','last_synced_at',
  'docs_checked_at','reconcile_after']` (`-- 071`). The PATCH that sets
  `reconcile_after` also changes `capture_source`, so it is still audited. An Admin's change of `capture_source` or
  `capture_start_date` is audited as `mailbox.updated`.
- `labels` keeps `CHECK (cardinality(labels) BETWEEN 1 AND 20)` (PD-12).
- Python: `store._MAILBOX_COLUMNS` + the four columns; `MAILBOX_PROGRESS` +
  `docs_checked_at`, `reconcile_after`; `tests/crm_fake.py` `DEFAULTS["crm_mailboxes"]` +
  `capture_source: "labels"`, `capture_start_date: "2026-04-01"`,
  `docs_checked_at: None`, `reconcile_after: None`.

### 3.2 `crm_capture_candidates` — FR-CAP-SRC-05, DIS-06, -07, -11, RPT-01 (071) — log

One row per mailbox and Gmail thread that reached the sync with no known
participant and no configured label.

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK DEFAULT `gen_random_uuid()` |
| `workspace_id` | uuid | NOT NULL FK |
| `mailbox_id` | uuid | NOT NULL; `FOREIGN KEY (workspace_id, mailbox_id) REFERENCES crm_mailboxes (workspace_id, id) ON DELETE CASCADE` |
| `thread_id` | text | NOT NULL `CHECK (char_length(thread_id) BETWEEN 1 AND 200)` |
| `latest_message_id` | text | NOT NULL ≤ 200 |
| `latest_message_at` | timestamptz | NOT NULL |
| `status` | text | NOT NULL DEFAULT `'pending'` `CHECK (status IN ('pending','skipped','ignored','known','judged','failed'))` |
| `attempts` | smallint | NOT NULL DEFAULT 0 `CHECK (attempts BETWEEN 0 AND 3)` |
| `judged_message_id` | text | NULL ≤ 200; the latest message id the decision covers |
| `outcome` | text | NULL `CHECK (outcome IN ('new_lead','deal_signal','other_business','not_relevant'))` |
| `confidence` | text | NULL `CHECK (confidence IN ('high','medium','low'))` |
| `classifier_version` | text | NULL ≤ 40 |
| `judged_at` | timestamptz | NULL |
| `dry_run` | boolean | NOT NULL DEFAULT false |
| `finding` | jsonb | NULL; `CHECK (finding IS NULL OR (outcome IS NOT NULL AND outcome IN ('new_lead','deal_signal') AND jsonb_typeof(finding) = 'object' AND pg_column_size(finding) <= 4096))`; `finding_of` keeps it under 3,584 bytes of UTF-8 JSON (names at most 200 characters and 400 bytes, the last attachment names dropped until it fits), so a name in Tamil or Chinese never trips the CHECK |
| `created_at`, `updated_at` | timestamptz | NOT NULL DEFAULT now() |

Constraints:

- `crm_capture_candidates_once UNIQUE (mailbox_id, thread_id)`.
- `crm_capture_candidates_judged CHECK ((status = 'judged' AND outcome, confidence, judged_at, judged_message_id all NOT NULL) OR (status <> 'judged' AND all four NULL))`: a decision is whole on a judged row and absent on any other. The finding CHECK tests `outcome IS NOT NULL` first, since a NULL `outcome IN (...)` is NULL and a CHECK passes NULL.
- `CONSTRAINT crm_capture_candidates_workspace_id UNIQUE (workspace_id, id)`.

`finding` (lead and deal signal only; the only place an address or name is
kept, spec §11 "No addresses for skipped outcomes"):

```json
{
  "company_name": "Acme Robotics", "domain": "acme-robotics.com", "domain_from_text": false,
  "person_name": "Priya Shah", "person_email": "priya@acme-robotics.com",
  "person_title": "Head of Ops", "lead_stage": "contacted",
  "deal_stage": null, "reason": "Asked for pricing; you replied.",
  "first_at": "2026-10-01T09:12:00+05:30", "latest_at": "2026-10-02T11:40:00+05:30",
  "message_count": 2, "owner_replied": true, "attachments": ["Acme brief.pdf"],
  "duplicate_company_id": null
}
```

Strings capped in Python (names ≤ 200, reason ≤ 160, ≤ 10 attachment names of
≤ 200). `reason` is already money-masked by `ai._clean`.

Indexes: `(workspace_id, status, latest_message_at)` (the job's pending
read); `(workspace_id, dry_run, outcome)` (the report).

**`crm_capture_mark(p_workspace uuid, p_mailbox uuid, p_rows jsonb) RETURNS
integer`** — `p_rows`: `[{thread_id, message_id, sent_at}]`. For each row, in
one statement: skip `sent_at < now() - interval '365 days'` (PD-15); `INSERT
... ON CONFLICT (mailbox_id, thread_id) DO UPDATE SET latest_message_id,
latest_message_at, status = 'pending', attempts = 0, judged_message_id = NULL,
outcome = NULL, confidence = NULL, classifier_version = NULL, judged_at =
NULL, finding = NULL, dry_run = false, updated_at = now() WHERE
EXCLUDED.latest_message_at > crm_capture_candidates.latest_message_at`.
Refuses a mailbox of another workspace (CRM04). Returns rows inserted or
advanced. No actor (log).

### 3.3 `crm_review_items` — FR-CAP-REV-01, CHG-04 (071, 073) — existing log

| Change | Migration |
|---|---|
| `crm_review_items_type_check`: `type IN ('next_step_change','task_from_commitment','task_from_meeting','deal_link','enrichment_conflict','rule_suggestion','deal_signal')` | 071 |
| … `, 'new_lead')` | 073 |
| `ALTER COLUMN record_type DROP NOT NULL`, `ALTER COLUMN record_id DROP NOT NULL` | 073 |
| `crm_review_items_target CHECK ((record_type IS NULL) = (record_id IS NULL) AND (record_id IS NOT NULL OR type = 'new_lead'))` | 073 |

`record_type`'s value CHECK is unchanged (a NULL passes it). Only a
`new_lead` with no duplicate company has no target. Suggestion and evidence
shapes are in contracts §6. Python mirrors: `review.TYPES`,
`ReviewItemType`, `tests/crm_fake.py` `REVIEW_TYPES` and its `review_upsert`
target assertion.

### 3.4 `crm_workspace_settings` — FR-CAP-DIS-01 (072) — existing, audited

- `crm_workspace_settings_key_check`: `key IN ('stage_probabilities',
  'flag_thresholds','automations','email_sync','ai','capture')`.
- Seed, system actor: `INSERT INTO crm_workspace_settings (workspace_id, key,
  value) SELECT w.id, 'capture', '{"mode":"off"}' FROM crm_workspaces w ON
  CONFLICT (workspace_id, key) DO NOTHING` (audited `setting.created` under
  the system actor, as `060`'s `ai` seed).
- `crm_workspaces_seed_ai()` replaced from `066`, adding the same insert
  (`-- 072`), so a workspace created later is seeded. The trigger itself is
  unchanged.
- Value: exactly `{"mode": "off" | "dry_run" | "live"}`; validated by
  `domain/crm/capture.capture_refusal` through `VALIDATORS["capture"]`. A
  missing row reads as `off` (`capture_mode`).

### 3.5 `crm_capture_dry_runs` — FR-CAP-RPT-01 to -04 (072) — log

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK |
| `started_at` | timestamptz | NOT NULL DEFAULT now() |
| `started_by` | uuid | FK `profiles` ON DELETE SET NULL |
| `finished_at` | timestamptz | NULL |
| `cancelled` | boolean | NOT NULL DEFAULT false; true when the mode left Dry run before the run finished (`crm_capture_set_mode`) |
| `recall` | jsonb | NOT NULL DEFAULT `'[]'` `CHECK (jsonb_typeof(recall) = 'array' AND pg_column_size(recall) <= 262144)` (500 rows of about 165 bytes); `[{mailbox_id, thread_id, latest_at, fate}]`, fate ∈ `filed_known, found, judged_otherwise, skipped_rules, outside_scope, ignored, known_now` (no CHECK on the values); filled as the recall check goes, while the run is open |
| `counts` | jsonb | NULL while the run is open; `CHECK (counts IS NULL OR jsonb_typeof(counts) = 'object')`; `crm_capture_report` as it stood when the run finished or was cancelled |
| `applied_at` | timestamptz | NULL |
| `applied_by` | uuid | FK `profiles` ON DELETE SET NULL |

Constraints: `UNIQUE (workspace_id, id)`; `CHECK (applied_at IS NULL OR
finished_at IS NOT NULL)`; `CHECK (NOT cancelled OR (finished_at IS NOT NULL
AND applied_at IS NULL))`; partial unique index
`crm_capture_dry_runs_one_open ON (workspace_id) WHERE finished_at IS NULL`.
Index `(workspace_id, started_at DESC)`.

Functions (072):

- **`crm_capture_record(p_workspace uuid, p_candidate uuid, p_message_id text,
  p_values jsonb) RETURNS boolean`** — writes a decision only when the row's
  `latest_message_id = p_message_id` and `status = 'pending'`. `p_values`:
  `{status, outcome?, confidence?, classifier_version?, finding?, dry_run,
  attempts?}`; `status = 'judged'` sets `judged_message_id = p_message_id`
  and `judged_at = now()`. Returns false when nothing was written (a newer
  message arrived, or already decided). No actor (log).
- **The capture lock.** `crm_capture_start_dry_run`, `crm_capture_set_mode`
  and `crm_capture_apply` (§3.7) each begin with
  `pg_advisory_xact_lock(hashtext('crm_capture:' || ws))`, before any
  check, so a start, a mode change and an apply of one workspace run one
  after another and each sees what the other committed (an apply racing a
  start could otherwise leave the mode Live with a new run open).
- **`crm_capture_start_dry_run(p_actor jsonb) RETURNS uuid`** — actor kind
  `user` (an Admin; the route checks the role). In one transaction: refuse
  CRM09 "A dry run is already running." when an unfinished run exists;
  insert the run; `UPDATE crm_capture_candidates SET status='pending',
  attempts=0, outcome=NULL, confidence=NULL, judged_at=NULL,
  judged_message_id=NULL, classifier_version=NULL, finding=NULL,
  dry_run=false WHERE workspace_id=ws AND dry_run`; set `crm.actor` and
  update the `capture` setting to `{"mode":"dry_run"}` (audited
  `setting.updated`).
- **`crm_capture_set_mode(p_actor jsonb, p_workspace uuid, p_mode text)
  RETURNS jsonb`** — actor kind `user` (an Admin). `p_mode` ∈ `off`, `live`
  (CRM22 otherwise; `dry_run` is only started above). In one transaction:
  set `crm.actor`; update the `capture` setting (audited `setting.updated`);
  when an open run exists, `UPDATE crm_capture_dry_runs SET finished_at =
  now(), cancelled = true, recall = '[]', counts = crm_capture_report(ws,
  id) WHERE workspace_id = ws AND finished_at IS NULL`; then, for `live`,
  every `dry_run` candidate back to `pending` exactly as the start does, so
  the live job judges again what an unapplied dry run judged.
  `live` is refused CRM22 "Run and apply a dry run first." unless a run of
  the workspace has `applied_at` set (the API checks first, contracts §1.2).
  Returns `{mode, cancelled_run_id}`. The settings route calls it instead
  of the generic settings write for key `capture`.
- **`crm_capture_finish_dry_run(p_workspace uuid, p_run uuid, p_recall jsonb)
  RETURNS boolean`** — sets `finished_at = now()`, `recall`, and `counts`
  from `crm_capture_report`; false if already finished or cancelled.
- **`crm_capture_recall_add(p_workspace uuid, p_run uuid, p_rows jsonb)
  RETURNS boolean`** — appends recall rows to the open run, a
  `(mailbox_id, thread_id)` already there kept as it was (the recall check's
  progress, plan §5.7); CRM22 for a value that is not an array; false once
  the run finished or was cancelled.
- **`crm_capture_report(p_workspace uuid, p_run uuid) RETURNS jsonb`**,
  `STABLE` — `{considered, pending, skipped_rules, ignored, known, judged,
  failed, filed_known_threads, by_outcome: {outcome: {high, medium, low}}}`
  over candidates with `latest_message_at >=` each mailbox's
  `capture_start_date`, plus distinct stored threads of `primary_sent`
  mailboxes since their start date; a run with `counts` set (finished or
  cancelled) returns those instead. Counts only.
- `crm_job_runs_job_check` + `'capture'`; `crm_job_latest()` replaced from
  `066`, `'capture'` appended to the array (`-- 072`).
- `crm_ai_usage_job_check` (the inline CHECK of `060`; name confirmed in
  T201) + `'capture'`.

### 3.6 `crm_capture_ignores` — FR-CAP-DIS-03, REV-03 (073) — audited configuration

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK |
| `kind` | text | NOT NULL `CHECK (kind IN ('address','domain'))` |
| `value` | text | NOT NULL `CHECK (char_length(value) BETWEEN 3 AND 320 AND value = lower(value))`; `CHECK (kind <> 'domain' OR position('@' in value) = 0)`; `CHECK (kind <> 'address' OR position('@' in value) > 1)` |
| `from_item_id` | uuid | NULL; `FOREIGN KEY (workspace_id, from_item_id) REFERENCES crm_review_items (workspace_id, id) ON DELETE SET NULL (from_item_id)` |
| `created_by` | uuid | FK `profiles` ON DELETE SET NULL |
| `created_at`, `updated_at` | timestamptz | NOT NULL DEFAULT now() |

- `UNIQUE (workspace_id, kind, value)`; `UNIQUE (workspace_id, id)`.
- Triggers: `crm_capture_ignores_touch BEFORE UPDATE ...
  crm_touch_updated_at()`; `crm_capture_ignores_crm_audit AFTER INSERT OR
  UPDATE OR DELETE ... crm_audit_row()` (noun `capture_ignore`, from the
  table name).
- Written only through `crm_apply` (`insert`, `delete`), which accepts any
  `crm_[a-z_]+` table. The public-webmail refusal is in Python
  (`invites.PUBLIC_WEBMAIL_DOMAINS`), as `007` does for company domains.

### 3.7 Functions (073)

- **`crm_capture_apply(p_actor jsonb, p_run uuid) RETURNS jsonb`** — actor
  kind `user`. Takes the capture lock (§3.5) first. Refuses CRM22 "This dry run cannot be applied." unless the run
  is the workspace's latest, finished, not cancelled, not applied and
  `finished_at >= now() - interval '7 days'` (the report's age, not the
  start's: a run paused by the monthly budget may finish late) (the API checks first and gives the precise sentence,
  contracts §2.2). Sets `applied_at`, `applied_by`; `UPDATE
  crm_capture_candidates SET dry_run = false WHERE workspace_id = ws AND
  dry_run`; sets `capture` to `{"mode":"live"}` (audited). Returns
  `{applied_at}`. The items are upserted by Python before this call (plan
  §5.8).
- **`crm_capture_purge(p_workspace uuid, p_before timestamptz) RETURNS
  integer`** — `DELETE FROM crm_capture_candidates WHERE workspace_id = ws
  AND updated_at < p_before AND status <> 'pending'`. Called by
  `tasks/crm.py::_purge` with `now - 365 days` (FR-CAP-DIS-11, E12, PD-15).
  Rows with a finding go too: any item made from one expired after 14 days
  and was tombstoned by `009` at 90, so a lead's name and address are not
  kept without limit after a person decided (NFR-CAP-02).
- **`crm_capture_supersede(p_actor jsonb, p_source_key text, p_keep text)
  RETURNS integer`** — actor kind `sync` or `system`. Expires (`status =
  'expired'`, `decided_at = now()`, one `review.expired` audit row each, as
  `crm_review_expire` does) every open capture item of the workspace (a
  `new_lead`, or a `deal_signal` whose `suggestion -> 'evidence' ->>
  'origin' = 'discovery'`; a deal document's signal is never touched) with
  `source_kind = 'email_thread'`, that `source_key` and a dedupe key other
  than `p_keep`. Called by `services/crm/capture.py::store_items` after each
  capture item's upsert, so a conversation whose duplicate check changed (a
  company created, or trashed, after its first item) or whose lead became a
  deal signal keeps one open capture item (spec §13 S3 line 1); and by the
  live job with `p_keep = ''` when a new judgement makes no item, so the
  earlier one expires (FR-CAP-DIS-06). Returns how many.

---

## 4. Lifecycle

| Event | Effect |
|---|---|
| Mailbox deleted | candidates cascade |
| Mailbox `capture_source`/`capture_start_date`/`email`/`labels` changed | cursor reset (Python); candidates kept; a re-read never re-opens a decided one unless a later message arrives (`crm_capture_mark`) |
| Review item purged (`crm_purge`) | `crm_capture_ignores.from_item_id` set NULL |
| Company merged | open `deal_signal` and linked `new_lead` items re-keyed by `065`'s merge (`|company:` head) |
| Company purged | its items deleted by `crm_purge` (by record) |
| 12 months after any decision | deleted by `crm_capture_purge` |
| Capture mode changed through PATCH while a dry run is open | the run is cancelled (`crm_capture_set_mode`) |
| Workspace deleted | RESTRICT, as every CRM table |

---

## 5. Grants and the security sweep

- RLS on for the three tables; `REVOKE ALL ON TABLE ... FROM anon,
  authenticated`; no policies; service role only.
- `REVOKE ALL ON FUNCTION ... FROM PUBLIC, anon, authenticated; GRANT EXECUTE
  ... TO service_role` for: `crm_capture_mark`, `crm_audit_row` (071);
  `crm_workspaces_seed_ai`, `crm_capture_record`, `crm_capture_start_dry_run`, `crm_capture_set_mode`,
  `crm_capture_finish_dry_run`, `crm_capture_recall_add`, `crm_capture_report`,
  `crm_job_latest` (072);
  `crm_capture_apply`, `crm_capture_purge`, `crm_capture_supersede` (073).
- `tests/db/test_crm_security_db.py`: `_009_sql()` reads `060`–`070` only;
  new `_010_sql()` reads `071`–`079`; `NOT_AUDITED_010 = {"crm_capture_candidates",
  "crm_capture_dry_runs"}` (logs: one row per thread / per run, written by
  jobs, no person-entered value); `NOT_AUDITED == NOT_AUDITED_007 |
  NOT_AUDITED_009 | NOT_AUDITED_010`; `TestThe010Additions` asserts the tables
  made are `NOT_AUDITED_010 | {"crm_capture_ignores"}`, RLS on, no browser
  grants, `crm_capture_ignores` has the audit trigger, every function is
  `crm_` and revoked. No new `uuid[]` column (`UUID_ARRAYS` unchanged).
- `TestThe048BodiesAreKept`: `071`'s `crm_audit_row` names every table
  `048`'s does (it is a copy of `067`), and its range 060–079 includes 071.

---

## 6. Indexes

| Table | Index | Serves |
|---|---|---|
| `crm_capture_candidates` | UNIQUE `(mailbox_id, thread_id)` | mark upsert |
| | `(workspace_id, status, latest_message_at)` | pending read, purge |
| | `(workspace_id, dry_run, outcome)` | report, apply |
| `crm_capture_dry_runs` | partial UNIQUE `(workspace_id) WHERE finished_at IS NULL`; `(workspace_id, started_at DESC)` | one open run; latest |
| `crm_capture_ignores` | UNIQUE `(workspace_id, kind, value)` | lookup, no duplicates |

---

## 7. Seeds and updates to existing rows

| Migration | Rows |
|---|---|
| 071 | every `crm_mailboxes` row gains `capture_source='labels'`, `capture_start_date='2026-04-01'`, `docs_checked_at=NULL`, `reconcile_after=NULL` (column defaults) |
| 072 | one `capture` setting `{"mode":"off"}` per workspace (system actor); later workspaces by the replaced trigger function |
| 073 | none |

After a fresh `db reset`: every mailbox `labels`, every workspace `capture.mode
= off`, no candidate, no run, no ignore (tasks "Gates").

---

## 8. Reversibility

- **071**: `ALTER TABLE crm_mailboxes DROP COLUMN reconcile_after, DROP COLUMN docs_checked_at, DROP COLUMN
  capture_start_date, DROP COLUMN capture_source`; `DROP FUNCTION
  crm_capture_mark(uuid, uuid, jsonb)`; `DROP TABLE crm_capture_candidates`;
  re-run `067`'s `crm_audit_row` with its grants; delete `deal_signal` items
  and re-add `061`'s type CHECK.
- **072**: `DROP FUNCTION` the five `crm_capture_*`; `DROP TABLE
  crm_capture_dry_runs`; delete the `capture` settings rows (system actor) and
  re-add `060`'s key CHECK; re-run `066`'s `crm_workspaces_seed_ai` and
  `crm_job_latest`; delete `capture` job runs and usage rows and re-add the
  `066`/`060` CHECKs.
- **073**: `DROP FUNCTION crm_capture_apply(jsonb, uuid),
  crm_capture_purge(uuid, timestamptz), crm_capture_supersede(jsonb, text,
  text)`; `DROP TABLE crm_capture_ignores`;
  delete `new_lead` items, drop `crm_review_items_target`, restore NOT NULL on
  `record_type`/`record_id`, re-add `071`'s type CHECK.

Switching off without a rollback (NFR-CAP-05): every mailbox to `labels` and
`capture.mode` to `off`. The tables stay and nothing reads them.

---

## 9. Revision 1 — migration 074 (spec §14; plan "Revision 1")

### 9.1 Migration

| File | Contents |
|---|---|
| `074_crm_capture_improvements.sql` | `crm_mailboxes` + `invites_rescanned_at`, `invite_rescan_token`; `crm_audit_row()` replaced from **071** (both columns in the mailbox skip, `-- 074`); `crm_capture_group_upsert()` (body of `069`'s `crm_review_upsert` copied and extended, `-- 074`); `crm_capture_release_thread()`; `crm_capture_consolidate()`; `crm_review_release()` replaced from **068** (`-- 074`); `crm_workspace_settings_key_check` replaced from **072** + `capture_routing`, seeded in every workspace, `crm_workspaces_seed_ai()` replaced from **072** (`-- 074`); grants |

Header in the 071–073 style: numbered list, provenance (`crm_audit_row` →
071, `crm_review_upsert` body → 069, which is **not** replaced,
`crm_review_release` → 068, key CHECK and `crm_workspaces_seed_ai` → 072),
Reversible. No new table, no `uuid[]` column. `crm_capture_supersede` (073)
stays as it is.

### 9.2 `crm_mailboxes` (074) — FR-CAP-INV-05

| Column | Type | Constraints |
|---|---|---|
| `invites_rescanned_at` | timestamptz | NULL; set once when the invitation rescan finishes; never reset by the API |
| `invite_rescan_token` | text | NULL `CHECK (char_length(invite_rescan_token) <= 512)`; the rescan's Gmail page token between syncs |

Both are progress, not configuration: `crm_audit_row` skips them as it skips
`history_id`, `page_token`, `docs_checked_at`, `reconcile_after` (no audit
row when only they change). Python: `store._MAILBOX_COLUMNS`,
`MAILBOX_PROGRESS`; `crm_fake.DEFAULTS["crm_mailboxes"]`.

### 9.2a `crm_workspace_settings` key `capture_routing` (074) — FR-CAP-ACAD-03

- `crm_workspace_settings_key_check` re-created from 072 with
  `capture_routing` added (`-- 074`).
- Seed: `INSERT … SELECT w.id, 'capture_routing', '{"accept_routed":true}'
  FROM crm_workspaces w ON CONFLICT DO NOTHING` (system actor, audited
  `setting.updated` as 072's seed); `crm_workspaces_seed_ai()` replaced
  from 072 with the same insert, so later workspaces are seeded.
- Value exactly `{"accept_routed": boolean}`; validated by
  `VALIDATORS["capture_routing"]`; a missing row reads as `true`. Read only
  for the workspace with key `academy` (`capture._academy_target`); changed
  only by that workspace's Admins through the generic `PATCH
  /crm/settings/capture_routing`. Audited like every setting.

### 9.3 Shapes in existing jsonb columns (no schema change)

**`crm_review_items.source_link`** of a capture item (`new_lead`,
`deal_signal`):

```json
{"mailbox_id": "<latest entry's, Labs only>", "thread_id": "<latest entry's>",
 "threads": [{"ref": "gmail:<mailbox_id>:<thread_id>", "mailbox_id": "…", "thread_id": "…",
              "message_id": "…", "first_at": "…", "latest_at": "…",
              "message_count": 3, "owner_replied": true,
              "origin": "discovery" | "deal_document" | "academy_routing",
              "reason": "…", "confidence": "high" | "medium" | null,
              "person_email": "…", "first_name": "…" | null, "last_name": "…" | null}]}
```

`person_email`, `first_name`, `last_name` are the entry's sender (the
finding's person); approval makes one person per distinct `person_email`
(FR-CAP-GRP-04). A pre-074 entry built by consolidation takes them from the
suggestion. "Newest entry" is the greatest `(latest_at, message_id)`.

An Academy item has no `mailbox_id` anywhere: its top level is `{"threads":
[…]}` and each entry's `ref` is `gmail:<mailbox email>:<thread_id>`. Unique
by `ref` within an item. `source_link` has no size CHECK (061); entries are
about 300 bytes. A pre-074 item has no `threads`; its one conversation is
`source_key` (`gmail:<mb>:<thread>`) + `source_version`.

**`crm_review_items.suggestion.evidence`** gains `conversations` (integer)
and holds the totals: `first_at` (min), `latest_at` (max), `message_count`
(sum), `owner_replied` (any), and the newest entry's `reason`,
`confidence`, `attachments`; Academy items add `mailbox_email`. The 8 KB
CHECK is unaffected (no list in `suggestion`).

**`crm_review_items.result`** of a consolidated loser:
`{"superseded_by": "<survivor id>", "reason": "Merged into one suggestion
for <company>."}` (≤ 500 characters). Of an approved `new_lead`:
`people` and `threads_filed` (entries whose sender is a known person after
approval); in the Academy also `note_id`.

**Keys** (plan R1.2):

| Item | `source_key` (≤ 300) | `dedupe_key` (≤ 400) |
|---|---|---|
| `new_lead`, unmatched | `capture:domain:<d>` \| `capture:name:<n>` \| `capture:email:<e>` (`capture:sha256:<hex>` when longer) | `email_thread\|<source_key>\|new_lead\|thread` |
| `new_lead`, matched company | `capture:linked` | `email_thread\|capture:linked\|new_lead\|company:<id>` |
| `deal_signal` | `capture:deal` | `email_thread\|capture:deal\|deal_signal\|company:<id>` |

**`crm_capture_candidates.finding`** gains `business` (`labs` \| `academy`;
missing = `labs`) and, for `academy`, `routed` (boolean: true when handed to
an accepting Academy target, whatever the Academy then did; false when
there was none); `duplicate_company_id` is null for an Academy finding.
Nothing the Academy decided is written here. Still ≤ 4096 bytes.

**`crm_capture_dry_runs.recall[].fate`** values (no CHECK, 072):
`filed_known`, `known_now`, `found`, `judged_otherwise`, `ignored`,
`internal_only`, `calendar`, `automated`, `outside_scope`, `not_judged`,
`academy_unrouted`; `skipped_rules` remains in rows written before 074.
A finished run's recall may end with one marker row `{"capped": true}` (no
`thread_id`), written only when the check stopped at 500 with a labelled
conversation unchecked; readers skip rows without `thread_id`.

### 9.4 Functions (074)

All `SET search_path = public, extensions`, actor kind `sync` or `system`
(CRM22 otherwise), `crm.actor` set from `p_actor`, and first
`pg_advisory_xact_lock(hashtext('crm_capture:' || ws))` (072's capture
lock), where `ws = p_actor ->> 'workspace_id'`. Approvals and refusals do
not take that lock, so every row a function changes is first read `SELECT …
FOR UPDATE` and re-checked `status = 'open'`; a row no longer open is never
updated (plan R1.2, PD-38).

- **`crm_capture_group_upsert(p_actor jsonb, p_item jsonb, p_entry jsonb)
  RETURNS jsonb`** — `p_item` as `crm_review_upsert` takes it (`type`,
  `owner_id`, `record_type`, `record_id`, `suggestion`, `current_values`,
  `source_kind`, `source_key`, `dedupe_key`, `deal_choices`, `model`,
  `generated_at`); `p_entry` one conversation entry (§9.3). In order:
  1. **skip** with `{id: null, skipped: true}` when an item of `ws` with
     `status <> 'open'`, `type IN ('new_lead','deal_signal')`, not
     `result ? 'superseded_by'`, not (`status = 'expired' AND refusal IS
     NOT NULL`), has an entry in `source_link -> 'threads'`
     with the same `ref` and `message_id`, or has `source_key = p_entry ->>
     'ref'` and `source_version = p_entry ->> 'message_id'`;
  2. **release** `p_entry ->> 'ref'` from every other open capture item
     (as `crm_capture_release_thread` with `p_keep = dedupe_key`); not done
     at all when `p_entry.origin = 'deal_document'`, and an entry of origin
     `deal_document` is never removed (PD-39);
  3. **upsert** by `(workspace_id, dedupe_key) WHERE status = 'open'`,
     the open row read `FOR UPDATE`: none (including one claimed since) →
     insert as 069 does with `source_link = {top, threads: [p_entry]}`,
     `source_version = message_id`, `crm_review_item_deals` from
     `deal_choices`; else merge `p_entry` into the open row's `threads`
     (replace the same `ref` unless older), recompute `suggestion.evidence`
     totals, and **only when `p_entry` is the newest entry** take
     `p_item.suggestion`'s other fields, `p_item.current_values` and
     replace `crm_review_item_deals` from `deal_choices`; otherwise those
     stay as they are. Set `source_key`, `source_link` (top level from the
     newest entry), `source_version`, `revision + 1` when changed;
     `owner_id`, `record_id` unchanged on update.
  Returns `{id, created, revision, released: n}`.
- **`crm_capture_release_thread(p_actor jsonb, p_ref text, p_keep text)
  RETURNS jsonb`** — for every open capture item of `ws` whose `dedupe_key
  <> p_keep` and that lists `p_ref` (or, pre-074, has `source_key =
  p_ref`; `new_lead`, or `deal_signal` of origin `discovery` as 073), each
  read `FOR UPDATE` and skipped unless still open: remove the entry (never
  one of origin `deal_document`), recompute totals and top level from the newest
  remaining entry, `revision + 1`; an item left with none is expired
  (`status = 'expired'`, `decided_at = now()`, one `review.expired` audit
  row, as `crm_review_expire`). Returns `{removed, expired}`.
- **`crm_capture_consolidate(p_actor jsonb, p_plan jsonb) RETURNS jsonb`**
  — `p_plan`: `[{source_key, dedupe_key, record_type, record_id,
  company_label, members: [uuid]}]`, computed in Python (plan R1.2, keys
  from the candidate's finding first, PD-40). Per group: members read `FOR
  UPDATE`, kept only when still `open` in `ws` (others ignored, never
  changed); add the open item already holding `dedupe_key`, if any; none
  left → next group; survivor = newest `created_at`, then `id`; entries =
  union of members' (a pre-074 member contributes one built from
  `source_link`, `source_version`, `suggestion.evidence`); each
  non-survivor first: `status = 'expired'`, `decided_at = now()`, `result
  = {superseded_by, reason}`, audit `review.expired` with `before =
  {status: open}`, `after = {status: expired, superseded_by}`,
  `suggestion_id = id`; then the survivor: `source_key`, `dedupe_key`,
  `source_link`, totals, `revision + 1`. CRM22 for a plan that is not an
  array. Returns `{groups, expired, rekeyed}`. Idempotent: a group of one
  already on its key changes nothing.
- **`crm_review_release(p_actor, p_item_id, p_refusal, p_result)`**
  replaced from 068 (signature, actor rule and every other path unchanged,
  lines marked `-- 074`). For a capture item (`type IN
  ('new_lead','deal_signal')`, `source_kind = 'email_thread'`,
  `source_key LIKE 'capture:%' OR source_key LIKE 'gmail:%'`) with a
  refusal, it first takes the capture lock, then:
  - **expire path** (another open item holds the key): before expiring,
    merge the refused item's entries (its `threads`, or one built from a
    pre-074 item's `source_link`) into that open item, read `FOR UPDATE`,
    as the upsert's merge does (newest-entry rule, totals, `revision + 1`);
    then expire as 068 does (refusal kept, `review.expired`);
  - **reopen path**: remove from the item every entry whose `ref` another
    open capture item lists, recompute totals and top level; when none is
    left, expire it instead of reopening (refusal kept, `review.expired`
    audit row), else reopen as 068 does.

### 9.5 Grants and the sweep

`REVOKE ALL ON FUNCTION … FROM PUBLIC, anon, authenticated; GRANT EXECUTE …
TO service_role` for `crm_audit_row`, `crm_capture_group_upsert`,
`crm_capture_release_thread`, `crm_capture_consolidate`,
`crm_review_release`, `crm_workspaces_seed_ai` (as 072 granted it).
`test_crm_security_db._010_sql()` (071–079) reads 074 automatically; the
function-name assertion gains the three names; `NOT_AUDITED_010` and
`AUDITED_010` unchanged (no table); 074's `crm_review_release` keeps every
068 branch (a body-kept assertion like `TestThe048BodiesAreKept`). `TestThe048BodiesAreKept`: 074's
`crm_audit_row` names every table 071's does.

### 9.6 Lifecycle additions

| Event | Effect |
|---|---|
| Each capture tick, mode Dry run or Live | `crm_capture_consolidate` for open items not on their key (never with capture Off) |
| Conversation re-judged elsewhere, or to no item | `crm_capture_release_thread`; empty item expires |
| Labs judgement ends as a Labs item or no item, Academy target exists | `crm_capture_release_thread(<academy sync>, 'gmail:<mailbox email>:<thread>', '')`, every time, not from the earlier finding (071's reopen clears it); no-op when the Academy holds no such entry |
| Capture item refused while a newer open item holds its key | replaced `crm_review_release` merges its threads into the open item, then expires it |
| Refused capture item reopened | entries another open item holds are dropped; empty → expired, not reopened |
| Company merged (065) | linked and deal keys re-keyed (`company:` head); a collision expires the moved item as today |
| 90-day tombstone (061) | blanks `source_link` with its `threads`; the re-suggestion check then no longer sees them (accepted) |
| Rescan finished | `invites_rescanned_at` set, token null, never reset |

### 9.7 Reversibility (074)

`DROP FUNCTION crm_capture_group_upsert(jsonb, jsonb, jsonb),
crm_capture_release_thread(jsonb, text, text), crm_capture_consolidate(jsonb,
jsonb)`; re-run 071's `crm_audit_row`, 068's `crm_review_release` and 072's
`crm_workspaces_seed_ai` with their grants; delete the `capture_routing`
rows (system actor) and re-create 072's key CHECK; `ALTER TABLE
crm_mailboxes DROP COLUMN invite_rescan_token, DROP COLUMN
invites_rescanned_at`. Grouped items stay valid for 073's code (top-level
`mailbox_id`/`thread_id` kept); consolidated losers stay expired.
