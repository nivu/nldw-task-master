# Tasks — Data model

| | |
|---|---|
| **Feature** | `008-tasks` |
| **Spec** | [`spec.md`](spec.md) §5.1, §10 (logical); this file chooses tables, types and names |
| **Plan** | [`plan.md`](plan.md) |

Everything here lands as numbered files in `supabase/migrations/`, in the
order of §1. Existing migrations are not edited. 008 owns numbers `046`–`059`
only (`009` owns `060`–`079`; plan §9).

---

## 1. Migrations

| File | Slice | Contents |
|---|---|---|
| `046_tasks_linkless_cleanup.sql` | S1a | Before the rename: every live `crm_tasks` row with no `crm_task_links` row is soft-deleted into its workspace Trash (`deleted_at = now()`, one `deleted_group` per task), under the system actor with `crm.action = 'task.deleted'`; `RAISE NOTICE` with the count (spec §8.2) |
| `047_tasks_shared_table.sql` | S1a | One transaction: rename `crm_tasks` → `tasks`, `crm_task_links` → `task_links` (and their constraints, indexes, triggers); status CHECK `open\|waiting\|done\|cancelled` with the data mapping; new columns (§2.1); `workspace_id` nullable on both; `task_links.project_id` and `tva_project`; plain `task_id` FK; deferred `tasks_workspace_check`; `tasks_workspace_fixed`; `tasks_parent_check`; `task_tags_ok()`; indexes (§4); select-list change (§3.1); saved-view rewrite (§3.2); field registry rows (§3.3) |
| `048_tasks_crm_functions.sql` | S1a | `CREATE OR REPLACE` of every CRM function that names the task tables or tests "not done", from its latest version, changing only what §5.4 lists; `crm_apply` dropped and recreated with `p_task_needs_link`; `crm_audit_row()` replaced (task workspace for link rows, no-actor exemptions for the new person links); grants |
| `049_task_write_path.sql` | S1a | `task_apply()`, `task_purge_expired()`; grants (§5.2, §5.3) |
| `050_task_rollout.sql` | S1a | `profiles.tasks_level`; `app_settings` row `tasks_default_level = 0` (§2.4) |
| `051_task_import_runs.sql` | S1d | `task_import_runs` (§2.3) |
| `052_task_delegation.sql` | S2a | `tasks.assigned_by`, `tasks.accepted_at`; `task_comments`; `task_notice_sends`; `profiles.task_nudge_days` (§2.5–2.7) |
| `053_task_recurrence.sql` | S3b | `tasks.recurrence`, `series_id`, `recurrence_spawned`; unique `(series_id, due_on)` (§2.8) |
| `054_task_voice.sql` | S4a | `voice_uploads`; Storage bucket `task-voice` (private) (§2.9) |
| `055_task_inbox_review.sql` | S4b | `inbox_items`; `profiles.tasks_review_dismissed_week` (§2.10) |
| `056_task_link_removal_lock.sql` | S1b | `tasks_workspace_check` locks the task row when a link is removed, so two concurrent removals cannot leave a CRM task with no CRM link (§5.1) |
| `057_task_link_removal_no_key_lock.sql` | post-009 review | `tasks_workspace_check` takes that lock `FOR NO KEY UPDATE` instead of `FOR UPDATE`: removals still wait for each other, but no longer deadlock with a transaction that inserted a link of the same task (whose foreign key check holds `FOR KEY SHARE`) (§5.1) |

`057`–`059` are spare, for a fix found in a later slice (a new file, never an
edit); `057` is used. **Functions 009 also replaces.** `009`'s migrations (`060`–`067`)
replace seven existing functions: `crm_audit_row`, `crm_purge`,
`crm_merge_companies` and (067, only if needed) `crm_query_rank`, which 048
also replaces, and `crm_remove_emails`, `crm_touch_updated_at` and
`crm_job_latest`, which 008 does not touch. Each is copied from the latest body
in its rebased tree, and they always run after every 008 file. So after S1a,
008 does **not** replace any of these seven functions. If a fix makes that unavoidable, the 008 commit
that adds it says so in its first line ("replaces <function>; 009 must
re-copy"), and `009` ships a `068`+ migration re-copying the new body; 008
never relies on its own later replacement surviving until that 009 migration
exists. 046–050 land together in S1a's one commit, so a 009 slice that
rebases finds all of them or none. Each file opens with `-- NNN_name.sql` and a prose rationale citing FR,
TQ and TP ids, as `001`–`045` do. DDL is idempotent where Postgres allows
(`IF NOT EXISTS`, `DROP … IF EXISTS` then `CREATE`, `ON CONFLICT DO NOTHING`);
the renames in 047 are guarded by `to_regclass('public.crm_tasks') IS NOT NULL`.

A function that touches a table arrives in, or is replaced in, the migration
that creates or renames that table or a later one (as `007` data-model §1).

---

## 2. Tables

### 2.1 `tasks` (was `crm_tasks`) — FR-TASK-DATA-01 to -15 (047; 052, 053 add columns)

Columns kept from `030`, `036`, `038` (unchanged unless noted):

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, every id kept (T1) |
| `workspace_id` | uuid | **now NULL-able**, FK `crm_workspaces` `ON DELETE RESTRICT`. Empty iff no CRM link (deferred check, §5.1) |
| `title` | text | NOT NULL, not blank, ≤ 200 |
| `body` | text | ≤ 20,000. Labelled **Notes** (TQ-08); API name `notes` on task routes, `body` on CRM routes (kept) |
| `status` | text | NOT NULL DEFAULT `'open'`, `CHECK (status IN ('open','waiting','done','cancelled'))` (T2; replaces `crm_tasks_status_check`) |
| `due_on` | date | |
| `bucket` | text | `CHECK (bucket IN ('today','this_week','this_month','later'))`; new `tasks_bucket_when_open CHECK (status NOT IN ('open','waiting') OR bucket IS NOT NULL)` (FR-TASK-DATA-08). Labelled **Bucket** (TQ-09) |
| `category_id` | uuid | composite FK `(workspace_id, category_id)` → `crm_select_options` kept (MATCH SIMPLE: skipped when `workspace_id` is null); `CHECK (workspace_id IS NOT NULL OR category_id IS NULL)` (Category is CRM-only) |
| `assignee_id` | uuid | NOT NULL FK profiles |
| `completed_at` | timestamptz | `tasks_completed CHECK ((status = 'done') = (completed_at IS NOT NULL))` kept (FR-TASK-DATA-03) |
| `position`, `twenty_id`, `flag_level`, `flags`, `flags_computed_at` | | kept; flags are written for CRM tasks only |
| `created_at`, `created_by`, `updated_at`, `updated_by`, `deleted_at`, `deleted_by`, `deleted_group` | | kept; `updated_at` is the optimistic lock |

New columns (047):

| Column | Type | Constraints |
|---|---|---|
| `priority` | text | NOT NULL DEFAULT `'normal'`, `CHECK (priority IN ('urgent','high','normal','low'))` (TQ-09) |
| `tags` | text[] | NOT NULL DEFAULT `'{}'`, `CHECK (task_tags_ok(tags))`: at most 10, each 1–40 characters after trim (TP-14, nothing stricter). `task_tags_ok(text[])` is `IMMUTABLE` SQL. Case is kept as typed; Python (`rules.normalise_tags`) trims each tag and drops an exact repeat before writing |
| `parent_id` | uuid | FK `tasks(id) ON DELETE SET NULL`; `CHECK (parent_id IS DISTINCT FROM id)`; trigger `tasks_parent_check` refuses a parent that has a parent, and a parent for a task that has children (CRM22 "A subtask cannot have subtasks.") (FR-TASK-DATA-09) |
| `waiting_on_id` | uuid | FK profiles `ON DELETE SET NULL` (the referential update is audited as the system, §5.4 `crm_audit_row`) |
| `waiting_on_text` | text | ≤ 200. Always holds the label shown: the person's display name when one was picked (a snapshot, so a later profile deletion keeps it), else free text such as "Life OS". `tasks_waiting_on CHECK (status <> 'waiting' OR btrim(coalesce(waiting_on_text,'')) <> '')` |
| `source` | text | NOT NULL DEFAULT `'manual'`, `CHECK (source IN ('manual','nldw','voice','email_commitment','meeting_action','rule','import'))` (FR-TASK-DATA-12) |
| `source_ref` | text | ≤ 500 |

Phase 2 (052): `assigned_by uuid` FK profiles `ON DELETE SET NULL` (who set
the current assignee; null for a self-assignment and for migrated rows; the
referential update is audited as the system by the 048 trigger) and
`accepted_at timestamptz` (FR-TASK-DEL-02). "New" = `assigned_by IS NOT NULL
AND assigned_by <> assignee_id AND accepted_at IS NULL`, computed on read.

Phase 3 (053): `recurrence text` (≤ 200, an RRULE body such as
`FREQ=WEEKLY;BYDAY=MO,TH`; validated in Python against the four allowed forms
of FR-TASK-RRULE-01), `series_id uuid`, `recurrence_spawned boolean NOT NULL
DEFAULT false`; `CHECK (recurrence IS NULL OR due_on IS NOT NULL)`; unique
`(series_id, due_on) WHERE series_id IS NOT NULL AND deleted_at IS NULL`
(FR-TASK-RRULE-03).

Data mapping in 047 (one transaction, under the system actor, so every change
is audited, spec §8.2):

1. `status IN ('todo','in_progress')` → `'open'`; a former `in_progress` row
   gains the tag `in progress` (TP-04).
2. **Every** open or waiting row with no bucket, live **or deleted** (rows in
   the Trash, including those 046 has just moved there), → `today` when
   `due_on <= (now() AT TIME ZONE 'Asia/Kolkata')::date`, else `later`
   (FR-TASK-DATA-08). This runs after step 1 and before
   `tasks_bucket_when_open` is added, so the CHECK never meets a bucketless
   open row and a later restore needs no bucket fix. The CHECK has no
   `deleted_at` exemption.
3. `source = 'import'` where `twenty_id IS NOT NULL`, else `'manual'`;
   `source_ref` stays null.
4. Done rows keep `completed_at`. Rows closed by a Lost move before 008 stay
   Done (FR-TASK-CHG-04).

Audited (trigger `tasks_crm_audit`, renamed from `crm_tasks_crm_audit`). RLS
on, no grants (unchanged).

### 2.2 `task_links` (was `crm_task_links`) — T3, FR-TASK-DATA-06, -13, -15 (047)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | **now NULL-able**. `task_links_workspace_kind CHECK ((record_type = 'tva_project') = (workspace_id IS NULL))` |
| `task_id` | uuid | NOT NULL. New FK `task_links_task_id (task_id) REFERENCES tasks(id) ON DELETE CASCADE` on every row; the composite `(workspace_id, task_id) → tasks(workspace_id, id) ON DELETE CASCADE` kept for CRM rows (skipped when `workspace_id` is null) |
| `record_type` | text | `CHECK (record_type IN ('company','person','opportunity','campaign','tva_project'))` |
| `record_id` | uuid | NOT NULL |
| `project_id` | uuid | FK `projects(id) ON DELETE CASCADE`; `task_links_project CHECK ((record_type = 'tva_project') = (project_id IS NOT NULL) AND (project_id IS NULL OR project_id = record_id))` |
| `created_at`, `created_by` | | kept |

Constraints and indexes: `task_links_target UNIQUE (task_id, record_type,
record_id)` (renamed); `task_links_one_project UNIQUE (task_id) WHERE
record_type = 'tva_project'` (TP-13); `task_links_record (workspace_id,
record_type, record_id)` (renamed); `task_links_project_idx (project_id) WHERE
project_id IS NOT NULL` (project panel, phase 2). Trigger `task_links_check_refs`
(renamed) runs the replaced `crm_check_refs()`, which skips `tva_project`
rows. Audited (`task_links_crm_audit`).

That an archived project is not newly linked, that the caller can see it, and
that only Labs tasks link a project are checked in Python (FR-TASK-DATA-06);
"only Labs" is also checked by `tasks_workspace_check` (§5.1).

### 2.3 `task_import_runs` — FR-TASK-IMP-01 to -08 (051)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `uploader_id` | uuid | NOT NULL FK profiles |
| `file_sha256` | text | NOT NULL `CHECK (file_sha256 ~ '^[0-9a-f]{64}$')` |
| `owner_mapping` | jsonb | NOT NULL `CHECK (jsonb_typeof(owner_mapping) = 'object')`: `{sheet value: email \| null}`; `"(blank)"` is the key of a blank cell |
| `value_mapping` | jsonb | NOT NULL: `{status: {sheet value: key \| null}, priority: {…}}`, the fixed mapping from `domain/tasks/lifeos.py` as applied to this file (a record, not editable: an unmapped value is fixed in the sheet and the file re-exported, Journey 4.1) |
| `status` | text | NOT NULL `CHECK (status IN ('dry_run','applied','failed'))` |
| `counts` | jsonb | NOT NULL: `{rows, open, waiting, done, cancelled, skipped, existing, repeated, created}` |
| `report` | jsonb | NOT NULL DEFAULT `'{}'`: unmapped values and ambiguous `D/M/YYYY` dates by row number, never a cell's text other than the owner/status/priority value being mapped |
| `dry_run_at` | timestamptz | NOT NULL DEFAULT now() |
| `applied_at` | timestamptz | |
| `created_at`, `updated_at` | | stamps |

No file content (FR-TASK-IMP-08). Index `(uploader_id, created_at DESC)`.
Audited through `task_apply` (`task_import_runs_crm_audit`; action
`task_import_run.created` / `.updated`). RLS on, no grants.

### 2.4 Phase level — FR-TASK-ROLL-01 to -05 (050)

- `profiles.tasks_level smallint NULL CHECK (tasks_level BETWEEN 0 AND 4)`.
  Null follows the global default. Changed only by `PATCH /admin/users/{id}`
  (TVA admin), audited `user.tasks_level` with before/after (a TVA row).
- `app_settings ('tasks_default_level', '0', 'Phase of 008 tasks switched on
  for everyone without their own level (0 = off, 1–4 cumulative).')`.
- Effective level = `coalesce(tasks_level, tasks_default_level)`, computed in
  `domain/tasks/rollout.level_for`.

### 2.5 `task_comments` — FR-TASK-DEL-03, -04 (052)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `task_id` | uuid | NOT NULL FK `tasks(id) ON DELETE CASCADE` |
| `workspace_id` | uuid | NULL-able; the task's workspace when written (set by Python, checked by trigger `task_comments_workspace` to equal the task's), so a CRM task's comments appear in its workspace audit and a private-era comment's audit row stays private-era |
| `author_id` | uuid | NOT NULL FK profiles |
| `body` | text | NOT NULL, not blank, ≤ 2,000 |
| `kind` | text | NOT NULL DEFAULT `'comment'`, `CHECK (kind IN ('comment','hand_back'))` |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `deleted_at`, `deleted_by` | | soft delete by the author only (Python); never edited (no `updated_at`; `task_apply` refuses `update` on this table except `soft_delete`) |

Index `(task_id, created_at)`. Audited. RLS on, no grants.

### 2.6 `task_notice_sends` — FR-TASK-DEL-07 (052) — log, no audit trigger

`id`, `task_id` (FK CASCADE), `event text CHECK (event IN
('assigned','completed','handed_back'))`, `recipient_id` (FK profiles CASCADE),
`occurred_at timestamptz NOT NULL` (the audit row's time of the event),
`sent_at timestamptz`, `channels text[]`; `UNIQUE (task_id, event,
recipient_id, occurred_at)`. Claimed with `INSERT … ON CONFLICT DO NOTHING
RETURNING id`, so a retried job sends nothing twice. Written directly by the
backend (system state, as `crm_digest_sends`). RLS on, no grants.

### 2.7 `profiles.task_nudge_days` — FR-TASK-DEL-06 (052)

`smallint NOT NULL DEFAULT 5 CHECK (task_nudge_days BETWEEN 1 AND 30)`.
Changed by the person through `PATCH /tasks/settings`.

### 2.8 Recurrence — FR-TASK-RRULE (053)

Columns in §2.1. The next occurrence is a new `tasks` row inserted in the same
`task_apply` call that sets the current one Done or Cancelled and
`recurrence_spawned = true`; its links are copied in the same call; the unique
`(series_id, due_on)` index refuses a second one in a race (mapped to 409
"This recurring task already has its next occurrence."). The same index
refuses a due-date change or a restore onto the date of another live
occurrence of the series (409 "Another occurrence of this recurring task is
already due on that date."). Closing a task through the CRM (`POST
/crm/tasks/{id}/complete`, `PATCH /crm/tasks/{id}`) inserts the next
occurrence in the same way, through `task_apply`. Removing `recurrence` from
an occurrence ends the series (FR-TASK-RRULE-04).

### 2.9 `voice_uploads` and the `task-voice` bucket — FR-TASK-VOICE (054)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | NOT NULL FK profiles CASCADE |
| `status` | text | `CHECK (status IN ('uploaded','transcribing','transcribed','failed','done'))` |
| `object_path` | text | `<user_id>/<id>`; null once the audio is deleted |
| `transcript` | text | ≤ 20,000; null once every candidate is confirmed or dropped, or after 24 h |
| `candidates` | jsonb | the split candidates with a `handled` flag each (no transcript text beyond the candidate lines) |
| `error_public` | text | a sentence safe to show, never a Groq response body |
| `created_at`, `transcribed_at` | timestamptz | |

Not audited (a transcript must never reach `audit_log`, FR-TASK-VOICE-05).
Written directly by `services/tasks/voice.py` and the worker. A candidate's
`handled` flag changes only through `voice_candidate_mark(p_upload, p_index,
p_handled) → boolean`, one conditional UPDATE that applies only while the row
is `transcribed` with its transcript and the flag is still the other way (two
confirms of one candidate cannot both pass, two requests cannot undo each
other's flag, a swept row is never written again); `voice_upload_finish(p_upload)
→ boolean` sets `done` and clears the transcript and the candidates' text once
every candidate is handled. Both service role only. Index
`(created_at) WHERE transcript IS NOT NULL OR object_path IS NOT NULL` for the
sweep. RLS on, no grants. Storage: `INSERT INTO storage.buckets (id, name,
public, file_size_limit, allowed_mime_types) VALUES ('task-voice',
'task-voice', false, 4194304, ARRAY['audio/webm','audio/ogg','audio/mpeg',
'audio/mp4','audio/wav','audio/x-m4a']) ON CONFLICT DO NOTHING`; no
`storage.objects` policy, so only the service role reaches it.

### 2.10 `inbox_items` and the weekly review — FR-TASK-INBOX, FR-TASK-REVIEW (055)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `owner_id` | uuid | NOT NULL FK profiles CASCADE (the cascade delete is audited as the system, §5.4 `crm_audit_row`) |
| `kind` | text | `CHECK (kind IN ('idea','link','note'))` |
| `title` | text | NOT NULL, not blank, ≤ 200 |
| `url` | text | `CHECK (url IS NULL OR url ~ '^https?://[^\s]+$' AND char_length(url) <= 2000)` |
| `notes` | text | ≤ 20,000 |
| `tags` | text[] | `CHECK (task_tags_ok(tags))` |
| `processed_at` | timestamptz | set by Make it a task or Archive |
| `archived` | boolean | NOT NULL DEFAULT false |
| `task_id` | uuid | FK `tasks(id) ON DELETE SET NULL`, the task it became |
| `created_at`, `updated_at` | | stamps |

Index `(owner_id, created_at DESC) WHERE processed_at IS NULL`. Audited
through `task_apply`, excluded from the TVA audit. RLS on, no grants.

`profiles.tasks_review_dismissed_week date NULL`: the Monday of the week the
person dismissed the review panel (FR-TASK-REVIEW-01).

---

## 3. Seeded and rewritten data

### 3.1 The task status list — FR-TASK-CHG-11 (047)

In both workspaces, under the system actor, in this order:

1. the CHECK `crm_select_options_keyed_active` (026) is dropped and re-added as
   `CHECK (system_key IS NULL OR is_active OR (list_key = 'task.status' AND
   system_key IN ('todo','in_progress')))`, the one stated exception;
2. `todo` and `in_progress`: `is_default = false`, `is_active = false`; never
   deleted. Clearing the default first keeps `crm_select_options_one_default`
   (one default per list) satisfied when `open` arrives as the default;
3. **label collisions are handled explicitly.** For each new row (`Open`,
   `Waiting`, `Cancelled`), if `lower(label)` already exists in that
   workspace's `task.status`: when the existing row is the now-inactive
   `todo` or `in_progress` (an Admin renamed To do to "Open"), that row's
   label gains the suffix " (old)" (audited `option.updated`); any other
   collision `RAISE EXCEPTION` naming the workspace and label, so the
   migration stops and is reported, never silently skipped;
4. insert `open|Open` (`is_default = true`, position 1), `waiting|Waiting`
   (position 2), `cancelled|Cancelled` (position 4) into `task.status`
   (`is_fixed = true`) with `ON CONFLICT (workspace_id, list_key, system_key)
   WHERE system_key IS NOT NULL DO NOTHING` (re-runs only); `done` keeps its
   row and any label an Admin gave it (position 3).

Labels for tasks with no workspace are fixed in code
(`domain/tasks/labels.py`): Open, Waiting, Done, Cancelled; Today, This week,
This month, Later; Urgent, High, Normal, Low. Priority has no option list in
any workspace; its labels come from code for every task.

### 3.2 Saved views — spec §8.2 (047)

A `DO` block walks `crm_saved_views WHERE object = 'task'` (seeded, edited
copies and views people made) and rewrites `config.filters` recursively:

| Condition on `status` | Becomes |
|---|---|
| `is todo` / `is in_progress` | `is open` |
| `any_of [... todo, in_progress ...]` | the same list with both replaced by `open`, de-duplicated |
| `none_of [... todo ...]` | the same replacement |
| `is_not done` | `none_of [done, cancelled]` |
| `none_of [done]` | `none_of [done, cancelled]` |

`card_fields` and `columns` are unchanged. Updated under the system actor
(`crm.action = 'view.updated'`). Favourites point at view ids and follow.

### 3.3 Field registry — `crm_object_fields` (047)

**Owner decision of 18 October 2026 (not asked for by the spec; the
recommended option taken).** The spec adds Priority and Source as task fields
but does not ask for them in CRM views. 047 adds `('task','priority','priority','fixed_select', …, filterable,
sortable, groupable)` and `('task','source','source','fixed_select', …)`;
mirror in `app/domain/crm/objects.py` (its registry test compares the two),
and give `services/crm/views.py` `_FIXED_LABELS` the priority and source labels
from `domain/tasks/labels.py` so a group says them in words. The priority
rank of §5.4 (`crm_query_rank`, `crm_query_rank_type`) goes with them.
`task.body` keeps its row (labelled Notes in the frontend's
`lib/crm/view-fields.ts`).

---

## 4. Indexes (NFR-TASK-01)

Renamed in 047: `crm_tasks_*` → `tasks_*` (`assignee`, `bucket`, `updated`,
`trash`, `twenty`, `flagged`), `crm_task_links_record` → `task_links_record`.

New in 047:

| Index | For |
|---|---|
| `tasks_mine ON tasks (assignee_id, status, due_on) WHERE deleted_at IS NULL` | My Tasks across workspaces (the existing `tasks_assignee` leads with `workspace_id`) |
| `tasks_created_by ON tasks (created_by, status) WHERE deleted_at IS NULL` | Assigned by me (phase 2), visibility of shared personal tasks |
| `tasks_tags ON tasks USING gin (tags)` | the tag filter (TP-15) |
| `tasks_title_trgm ON tasks USING gin (lower(title) extensions.gin_trgm_ops)` | the text filter (TP-15) |
| `tasks_parent ON tasks (parent_id) WHERE parent_id IS NOT NULL` | subtasks |
| `tasks_lifeos_key UNIQUE ON tasks (source_ref) WHERE source = 'import' AND source_ref LIKE 'lifeos:%'` | FR-TASK-DATA-12, FR-TASK-IMP-05 |
| `tasks_personal_trash ON tasks (deleted_at) WHERE workspace_id IS NULL AND deleted_at IS NOT NULL` | the 90-day purge (TP-12) |
| `tasks_done_on ON tasks (assignee_id, completed_at) WHERE status = 'done' AND deleted_at IS NULL` | day-log suggestions (phase 3) |

---

## 5. Functions and triggers

All in `public`, `LANGUAGE plpgsql`, `SET search_path = public, extensions`,
`EXECUTE` revoked from `public`, `anon`, `authenticated` (as `007` §5.12).
Error contract as `007` §5: `CRM04` → 404, `CRM09` → 409, `CRM22` → 422,
mapped in Python by `services/tasks/store.rpc()` (which reuses the CRM
mapping).

### 5.1 `tasks_workspace_check()` — deferred constraint trigger, FR-TASK-DATA-05 (047)

```text
CREATE CONSTRAINT TRIGGER tasks_workspace_check
  AFTER INSERT OR UPDATE ON tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
CREATE CONSTRAINT TRIGGER task_links_workspace_check
  AFTER INSERT OR UPDATE OR DELETE ON task_links DEFERRABLE INITIALLY DEFERRED FOR EACH ROW

for the task t (NEW.id, or the link's task_id), if it still exists and t.deleted_at IS NULL:
  crm_ws := set of workspace_id of t's links with record_type <> 'tva_project'
  IF crm_ws = {} AND t.workspace_id IS NOT NULL
     → CRM22 'A CRM task needs at least one linked record; delete it instead.'
  IF crm_ws <> {} AND crm_ws <> {t.workspace_id}
     → CRM22 'A task cannot link records of two workspaces.'
  IF t has a tva_project link AND t.workspace_id IS NOT NULL
     AND NOT (SELECT tva_linked FROM crm_workspaces WHERE id = t.workspace_id)
     → CRM22 'Only Nunnari Labs tasks can link a TVA project.'
```

Runs at commit, after the CRM writer's and `crm_deal_transition`'s task
inserts and their link inserts (spec §1 fact 2). A restore of a task with no
CRM link left fails here too (FR-TASK-DATA-05).

### 5.2 `task_apply(p_actor jsonb, p_ops jsonb) RETURNS jsonb` — TQ-02, FR-TASK-DATA-10 (049)

- `p_actor`: `{"user_id", "kind": "user"|"system"|"import", "channel":
  "web"|"mcp"|null}`, plus `"suggestion_id": uuid` only when the caller set
  one (contracts §4, `TaskActor.as_rpc()`). It has **no** `workspace_id`: a
  task write's workspace is always the row's. `task_apply` sets `crm.actor` to
  `p_actor` unchanged (transaction-local), so `crm_audit_row()` audits each
  write with the row's own workspace (for a `task_links` row, its task's;
  §5.4), null for a personal task. 008's trigger ignores `suggestion_id`;
  `009`'s replacement of `crm_audit_row` reads it from `crm.actor`.
- `p_ops`: as `crm_apply` (`007` data-model §5.2): `insert`, `update`,
  `soft_delete`, `restore`, `delete`, with `values`, `id`, `expect_updated_at`,
  `action`, `deleted_group`, `ref` and `"$ref:…"` values.
- Tables: only `tasks`, `task_links`, `task_comments`, `task_import_runs`,
  `inbox_items`, each required to exist and carry `<table>_crm_audit`
  (otherwise CRM22 "That is not a task table."). Columns from `pg_attribute`;
  `id`, `created_at`, `created_by` cannot be set by `values`. `workspace_id`
  **may** be set on `tasks` and `task_links` (the CRM link's workspace); the
  deferred check validates it.
- **Re-check the caller's right**, per op, against the row as stored (for an
  insert, as written; for a link or comment, its task):
  - row with a workspace: `p_actor.user_id` holds a membership of it (CRM04
    "No such task." otherwise; role is checked in Python);
  - `tasks` row with no workspace: the actor is its `created_by` or
    `assignee_id`, or kind `system`/`import` (CRM04 otherwise);
  - `task_import_runs`: the actor is the run's `uploader_id`;
    `inbox_items`: the actor is `owner_id`.
- `expect_updated_at` mismatch → CRM09.
- Returns `[{"ref", "id", "row"}]`.

`tasks_workspace_fixed` (BEFORE UPDATE ON tasks): `workspace_id` may change
only from NULL to a value (CRM22 "A CRM task cannot leave its workspace."),
so the rule holds for `crm_apply` too.

### 5.3 `task_purge_expired(p_actor jsonb, p_before timestamptz) RETURNS int` — TP-12 (049)

Deletes `tasks WHERE workspace_id IS NULL AND deleted_at < p_before` (links,
comments cascade), audited `.purged` under `p_actor` (kind `system`). Returns
the count. CRM tasks are never touched here (their Trash has `crm_purge`).

### 5.4 CRM functions replaced in 048 — spec §8.2

Each is copied from the migration named, with only these changes:

| Function | From | Changes |
|---|---|---|
| `crm_apply(p_actor, p_ops, p_task_needs_link boolean DEFAULT true)` | 027 | `DROP FUNCTION crm_apply(jsonb, jsonb)` first. The table guard accepts `^crm_[a-z_]+$` **or** `tasks` / `task_links`, still requiring `<table>_crm_audit`. Inserts still stamp `workspace_id = actor.workspace_id`, so it cannot write a `tva_project` link (the kind CHECK refuses it). The final link check runs only when `p_task_needs_link` and counts links with `record_type <> 'tva_project'` |
| `crm_check_refs()` | 027 | `task` → `tasks`; return early for `record_type = 'tva_project'` |
| `crm_deal_transition(p_actor, p_plan)` | 033 | `crm_tasks`/`crm_task_links` → `tasks`/`task_links`; the open-task re-check uses `status IN ('open','waiting')`; `close_tasks` → `SET status = 'cancelled', completed_at = NULL … AND status IN ('open','waiting')` (FR-TASK-CHG-04); created tasks insert `status 'open'`, `priority 'normal'`, `source 'rule'` |
| `crm_purge(p_actor, p_plan)` | 042 | table names; after removing a purged record's `task_links`, `DELETE FROM tasks` of the workspace whose live and deleted rows now have no CRM link, audited `.purged` (FR-TASK-DATA-14) |
| `crm_merge_companies(p_actor, p_plan)` | 042 | table names |
| `crm_search(p_workspace, p_q, p_limit, p_redact)` | 041 | table names |
| `crm_query(…)`, `crm_query_rank(…)` | 041 | object `task` → `tasks`. The one `status` array orders both task and campaign-member statuses (041's comment), so it becomes exactly `{open,waiting,done,cancelled,targeted,invited,registered,attended,engaged,meeting_booked,converted,not_interested,unsubscribed}` (campaign-member keys kept in their order); priority rank `{urgent,high,normal,low}` (§3.3). `test_crm_query_db` pins campaign-member status sorting |
| `crm_query_rank_type(…)` | 038 | adds `task.priority` to the ranked fixed selects (§3.3) |
| `crm_flag_facts(…)` (four-argument) | 039 | table names; open = `status IN ('open','waiting')` |
| `crm_set_flags(…)` | 038 | `p_table` allow-list `crm_tasks` → `tasks` |
| `crm_audit_row()` | 039 | (1) **Workspace of a link row.** For `TG_TABLE_NAME = 'task_links'` with a null `workspace_id` (a `tva_project` link), the audit row's workspace is the task's: `(SELECT workspace_id FROM tasks WHERE id = (r ->> 'task_id')::uuid)` (null when the task is personal or already gone in a cascade). So a Labs task's project-link history stays in the CRM record timeline (`services/crm/store.py` filters on `workspace_id`) and `history_visible` sees it in the right era. (2) **No-actor exemptions**, recorded as the system like 039's, only when the profile named is already gone: an `UPDATE` of `tasks` whose changed columns are within `{waiting_on_id, assigned_by}` and set to null (`ON DELETE SET NULL`); a `DELETE` of `inbox_items` whose `owner_id` profile no longer exists (`ON DELETE CASCADE`). Everything else is 039's body unchanged; the noun fallback gives `task`, `task_link`, `task_comment`, `task_import_run`, `inbox_item`. Replaced here, in S1a, and never again by 008 (§1). The `inbox_items` and `assigned_by` cases name columns and a table that arrive in 052/055; the trigger reads them from the row as jsonb, so it needs no later replacement |

Unchanged and pinned by db tests: `crm_remove_membership()` (040),
`crm_notify()` (037), `crm_record_import()` (036), `crm_link_project()` (033).
A db test deletes a profile referenced by `waiting_on_id` (S1a), by
`assigned_by` (S2a) and by `inbox_items.owner_id` (S4b) and asserts the delete
succeeds with a system audit row.

---

## 6. Changes to existing tables

| Table | Migration | Change |
|---|---|---|
| `crm_tasks` → `tasks` | 047 | §2.1 |
| `crm_task_links` → `task_links` | 047 | §2.2 |
| `crm_select_options` | 047 | §3.1 (CHECK and rows) |
| `crm_saved_views` | 047 | §3.2 (rows) |
| `crm_object_fields` | 047 | §3.3 (rows) |
| `profiles` | 050, 052, 055 | `tasks_level`, `task_nudge_days`, `tasks_review_dismissed_week` |
| `app_settings` | 050 | one row |
| `audit_log` | — | unchanged. Old rows keep `target_table = 'crm_tasks'` / `'crm_task_links'` (append-only, `005`); readers accept both names (FR-TASK-CHG-07) |
| `storage.buckets` | 054 | `task-voice` |

---

## 7. Reversibility (constitution §III)

Each migration's header carries its reverse, as `025`–`045` do. 047's reverse
renames back, restores the `todo|in_progress|done` CHECK after mapping
`open`/`waiting` → `todo` and `cancelled` → `done` (with the import time as
`completed_at`), deletes rows with no workspace, and drops the new columns: it
loses personal tasks and the waiting/cancelled distinction, so it is a
disaster-recovery path only, stated as such. 048's reverse re-runs the
`CREATE OR REPLACE` bodies of the migrations named in §5.4 (after 047's
reverse), `crm_audit_row` included (039's body). 049–055 drop what they add.
