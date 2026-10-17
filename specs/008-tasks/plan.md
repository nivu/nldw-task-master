# Tasks — Plan

| | |
|---|---|
| **Feature** | `008-tasks` |
| **Branch** | `008-tasks` (main checkout) |
| **Date** | 18 October 2026 |
| **Spec** | [`spec.md`](spec.md) (operative); [`source.md`](source.md) (owner's original, behaviour only) |
| **Companions** | [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md), [`tasks.md`](tasks.md) |
| **Constitution** | v1.2.0 (a MINOR amendment is an owner action before phase 1 ships, spec §15.1; tasks S1e) |
| **Built beside** | `009-crm-intelligence`, planned and built at the same time in a worktree; §9 holds the rules both plans follow |

This is the HOW for `spec.md`. Where this file and the spec disagree, the spec
wins and this file is wrong. Every FR, TQ and TP id below is the spec's. It
follows `007`'s plan in shape and conventions.

---

## 1. Summary

Tasks are not a new service and not a new table. The CRM's `crm_tasks` and
`crm_task_links` become `tasks` and `task_links` (T1, T3), widened so a row can
have no workspace. The same FastAPI backend gains an `/api/v1/tasks/...` router
family, open to every account (staff and `crm_only`) and gated per person by a
phase level. The same Next.js frontend gains one My Tasks page component,
mounted in both shells, and a quick-add box in both headers. Nothing new is
deployed until phase 4, which needs a Groq key on the worker.

Seven design choices hold the rest together:

1. **One table, two writers, one audit.** CRM routes keep writing CRM tasks
   through `crm_apply`, which now accepts the two renamed tables and takes the
   "a task needs a CRM link" rule as a parameter (FR-TASK-CHG-01). Task routes
   write through a sibling function, `task_apply`, which accepts a row with no
   workspace and re-checks the caller's right to the row in the database
   (TQ-02). Both set `crm.actor`, so the existing `crm_audit_row()` trigger
   audits every change with actor, kind and channel (FR-TASK-DATA-10).
2. **The database owns the workspace invariant.** A deferred constraint
   trigger checks, at commit, that a live task's workspace is empty exactly
   when it has no CRM link and equals the workspace of every CRM link it has
   (FR-TASK-DATA-05). A second trigger lets a task's workspace change only from
   empty to a value, so a CRM task never becomes personal.
3. **One visibility function.** `app/domain/tasks/visibility.py` implements
   spec §3 as pure functions over two fact bundles (the viewer, the task). Every
   task route, every CRM route that returns or changes a task, every task tool
   and the 009 interface pass through it (FR-TASK-VIS-01). Refusals are 404
   (FR-TASK-VIS-02). There is no RLS policy; RLS is on with no grants (T4).
4. **CRM links win.** A task with a CRM link is a CRM task: the CRM guard and
   `007` §3.3 decide, as today, and the visibility function only withholds the
   project link from members who cannot see the project (FR-TASK-VIS-07).
5. **Computed, not stored.** The effective bucket (TP-06), days overdue
   (FR-TASK-LIST-03), "New" (FR-TASK-DEL-02), the brief and day-log
   suggestions are computed on read. No nightly job rewrites tasks.
6. **Fixed rules, no AI.** Quick add (T7) and voice splitting (TP-22) are pure
   functions in `app/domain/tasks/`, tested with a fixed clock. The only outside
   service is speech-to-text in phase 4 (T10).
7. **Per-person phase level.** `profiles.tasks_level` (0–4, null = the global
   default) and `app_settings.tasks_default_level` (starts 0). One dependency,
   `TasksDep(level)`, refuses 403 on every task route of a phase not switched
   on (FR-TASK-ROLL). CRM task behaviour never reads it (FR-TASK-ROLL-05).

**The 009 interface.** `backend/app/domain/tasks/service.py`
`create_task(actor, payload)`, with `payload: TaskCreate` from
`backend/app/schemas/tasks.py`, is built in slice S1a and is the same function
the manual create route calls, so a task created by `009` gets exactly the
validation, visibility and audit of a manual one (spec §14; contracts §4).
`TaskCreate` carries the rule-2 fields plus optional fields with defaults
(`tags`, `parent_id`, `status`, `waiting_on_id`, `waiting_on_text`,
`category_id`, `completed_at` for the import only), all fixed in S1a, so
`POST /tasks`, quick add and the Life OS import create through the same
function without changing its shape later. The actor is `TaskActor(user,
kind, channel, suggestion_id=None)`; 009 builds one and passes it.

---

## 2. Technical context

| | |
|---|---|
| **Language/Version** | Python 3.11+ (CI 3.13), TypeScript 5 strict |
| **Backend** | FastAPI (sync `def` handlers), Pydantic v2, supabase-py (service role), Celery + Redis, MCP SDK ≥ 2.2 |
| **Frontend** | Next.js 16 App Router, `@supabase/ssr` (auth only), shadcn base-nova on `@base-ui/react`, `@dnd-kit` (already a dependency since `007` S6b) |
| **Storage** | Supabase Postgres: `crm_tasks` → `tasks`, `crm_task_links` → `task_links`; new `task_import_runs` (1), `task_comments`, `task_notice_sends` (2), `inbox_items`, `voice_uploads` (4); `ALTER`s on `profiles`; one `app_settings` row; `crm_audit_row()` replaced once (048). Phase 4: one private Storage bucket, `task-voice`. |
| **Testing** | pytest with in-memory fakes; `@pytest.mark.db` tests against local Supabase (`CRM_DB_TESTS=1`, the existing `db` CI job); Playwright e2e local only |
| **Target platform** | Railway (api, worker, beat), Netlify (frontend), Supabase `ap-south-1` |
| **Performance goals** | NFR-TASK-01: My Tasks under 1 s for a person with 5,000 visible tasks; quick add saves under 1 s; a new task in the creator's open page within 2 s (optimistic) |
| **Constraints** | Netlify function body ≈ 6 MB (uploads capped at 4 MB, already enforced by the proxy, `007` §5.12); PostgREST returns at most 1,000 rows per call (My Tasks pages closed tasks); beat runs in IST wall-clock |
| **Scale/scope** | ~25 people, ~5,000 tasks per heavy user, 2 workspaces, 6 tools (4 in phase 1) |

### 2.1 New dependencies (NFR-TASK-07)

| Package | Where | Why it passes the 50-line rule | Slice |
|---|---|---|---|
| `python-dateutil` | backend | `dateutil.rrule` computes the next occurrence of an RFC 5545 RRULE (FREQ, BYDAY, BYMONTHDAY, month-end clipping, leap years) (T9, FR-TASK-RRULE-06). A correct subset is well past 50 lines. It is already in `uv.lock` through `supabase`/`celery`; it becomes a direct dependency so an upgrade of those cannot remove it. | S3b |

Not added: a Groq SDK (NFR-TASK-07: the call is one `httpx` multipart POST in
`services/tasks/groq.py`), a date-parsing library for quick add (the rules are
a closed set, T7), a rich-text editor (notes stay markdown rendered with
`marked`). `pyproject.toml` gains only `python-dateutil`, in its own commented
block (§9 rule 3).

### 2.2 New configuration

| Setting | Where | Default | Meaning |
|---|---|---|---|
| `tasks_default_level` | `app_settings` row (050), edited through `update_setting` | `0` | The phase level of everyone whose `profiles.tasks_level` is null (TP-11). `update_setting` validates an integer 0–4. |
| `GROQ_API_KEY` | `app/config.py`, `SecretStr \| None` | unset | Phase 4. Set on the `worker` service **only** (T10). Never logged, never in Celery arguments or results. |
| `TASKS_VOICE_CONFIGURED` | `app/config.py`, `bool` | `false` | Phase 4. Non-secret signal, set on `api`, that the worker holds the key; `POST /tasks/voice` answers 503 "Voice capture is not set up yet." while false. The pattern of `CRM_GMAIL_CONFIGURED` (`007` §2.2). |

---

## 3. Constitution check

| # | Question (constitution, Governance) | Answer |
|---|---|---|
| 1 | Does this serve one of the journeys? | **Not yet, for personal tasks and the inbox.** CRM tasks serve the Seller's Journey as today; project tasks serve the Lead's (running projects). A private task list does not trace to any of the four journeys (spec §15.1). S1e carries the owner's MINOR amendment (task keeping added to the Employee's and Seller's journeys); phase 1 does not ship before it is agreed. |
| 2 | Security-First and Clear Boundaries? | Yes, with CT-1 and CT-2. Every new table: RLS on, no policy, `REVOKE ALL … FROM anon, authenticated` (as `025` did for every table). New functions: `EXECUTE` revoked from `public`, `anon`, `authenticated`. The browser reads no task data from Supabase; every read and write goes through `/api/proxy` (FR-TASK-VIS-03, FR-TASK-LIST-08). Voice audio is in a private bucket written only by the service role and deleted after transcription (FR-TASK-VOICE-02). The Groq key lives only on the worker. |
| 3 | Data Integrity: no schema changes outside migrations, no cross-tenant exposure? | Yes. Ten numbered migrations, `046`–`055` (data-model §1). The rename and status mapping are one migration in one transaction, preceded by the linkless clean-up (NFR-TASK-03). The workspace invariant is a deferred database check. Cross-workspace and cross-person exposure is proven by FR-TASK-VIS-06's generated sweep (S1b, S1e). |

**Re-check after design:** done. Nothing beyond §4.

---

## 4. Complexity tracking

| # | Exception (principle) | Why needed | Simpler alternative rejected because |
|---|---|---|---|
| CT-1 | **Private data is guarded by backend code, not by an RLS policy** (§I "every table MUST have its RLS policy defined") | T4 and the code: the backend uses the service role, which bypasses RLS, and no table since `010` has a browser grant. A policy would protect nothing that is reachable. The guarantee is one visibility function plus a test that enumerates every route and tool (FR-TASK-VIS-06), and `task_apply` re-checks the caller's right to the row in the database. | RLS policies keyed on `auth.uid()`: unreachable through the service role; would need a second data path with the user's JWT, which the frontend deliberately does not have (`007` §4.0). |
| CT-2 | **`app/domain/tasks/service.py` performs IO, and two `domain/tasks` modules import from the `api` and `services` layers**, against the repo's layering (domain is pure; services do IO; domain imports neither) | The 008/009 interface fixes this module path and function name (§9 rule 2). It is the task service; every pure rule lives in sibling `domain/tasks/*.py` modules, and its reads go through `app/services/tasks/store.py`. The two upward imports are deliberate and are the only ones: `domain/tasks/errors.py` subclasses `CrmRefused` from `app/services/crm/__init__.py`, so `main.py`'s existing handler renders `TaskRefused` with no new handler; `domain/tasks/service.py` types `TaskActor.user` as `app.api.deps.CurrentUser`, the user object every caller (routes, 009) already holds. No other `domain/tasks` module imports from `api` or `services`. | Putting the service in `app/services/tasks/` and re-exporting from `domain/tasks/service.py`: two names for one thing; 009 would import the re-export and a reviewer would edit the other. |
| CT-3 | **Dynamic SQL in `task_apply`** (as `007` CT-1 for `crm_apply`) | FR-TASK-DATA-10 needs the actor set in the same transaction as the write. `task_apply` builds SQL only with `format('%I')` and `USING`, against an allow-list of five table names. | PostgREST table calls cannot set the actor. |

---

## 5. Architecture

### 5.1 Who may call what

```text
request ─▶ deps._authenticate ─▶ any_account (staff and crm_only)
              │
              ├─ /api/v1/tasks/*  ─▶ api/tasks/deps.TasksDep(level n)   403 "Tasks are not switched on for you yet."
              │                        │
              │                        ▼
              │                   services/tasks/viewer.load(user) ─▶ Viewer facts (once per request)
              │                        │
              │                        ▼
              │                   domain/tasks/visibility  ─▶ 404 / read-only / full
              │
              ├─ /api/v1/crm/*    ─▶ crm/deps (unchanged) ─▶ services/crm/present.task ─▶ visibility.project_field
              └─ TVA routes       ─▶ current_user (unchanged); /timesheet/day gains suggestions (phase 3)
```

- `api/tasks/deps.py`: `TasksDep = tasks_dep(1)`, `TasksDep2`, `TasksDep3`,
  `TasksDep4`, each `AnyAccountDep` plus `rollout.level_for(profile,
  default) >= n`. The default is read once per request from `app_settings`.
- FR-TASK-CHG-02: `tests/test_crm_only_refused.py` gains
  `SHARED_PREFIXES = ("/api/v1/tasks",)` beside `SHARED_ALLOW_LIST`, a visible
  change to the constant set. Every route under it must depend on a
  `TasksDep*` (asserted by walking `app.routes`).
- `/me` (shared allow-list) gains `tasks: {level}` for everyone, so both
  shells know what to show (FR-TASK-ROLL-03, "the web app hides").
- FR-TASK-ROLL-02: `PATCH /admin/users/{id}` (TVA `AdminDep`) accepts
  `tasks_level` (0–4 or `"default"`), audited `user.tasks_level` as a TVA row;
  `update_user` MCP needs no new argument (it takes `changes`). `crm_only`
  profiles may have it set (T6), unlike `role`.

### 5.2 Layering — follows `api` / `domain` / `services`

| Layer | New modules | Rule |
|---|---|---|
| `app/api/tasks/` | `__init__.py` (`ROUTERS`), `deps.py`, `tasks.py` (S1b), `lifeos.py` (S1d); `comments.py`, `brief.py` (S2); `templates.py` (S3b); `voice.py`, `inbox.py`, `review.py` (S4) | HTTP only. Sync `def`. Raise `ProblemDetail` or let `TaskRefused` through. |
| `app/domain/tasks/` | `__init__.py`, `errors.py` (`TaskRefused(CrmRefused)`, so `main.py`'s existing handler renders it), `labels.py`, `rules.py`, `visibility.py`, `rollout.py`, `quick_add.py`, **`service.py`** (S1a); `lifeos.py` (S1d); `delegation.py`, `brief.py` (S2); `suggestions.py`, `recurrence.py` (S3); `voice_split.py`, `review.py` (S4) | Pure, except `service.py` (CT-2). Take `today` / `now` / `zone` as arguments. |
| `app/services/tasks/` | `__init__.py`, `store.py` (every read of `tasks`, `task_links`, `task_*` tables for task routes; `rpc()` with the CRM error mapping), `viewer.py`, `present.py`, `listing.py`, `people.py` (who the caller may assign or link) (S1a–S1c); `lifeos.py` (S1d); `notices.py`, `brief.py` (S2); `suggestions.py`, `templates.py` (S3); `voice.py`, `groq.py`, `inbox.py` (S4) | Gather rows, call domain, write through `task_apply`. |
| `app/schemas/tasks.py` | `TaskCreate` (the 009 interface), `TaskLinkIn`, `TaskIn`, `TaskPatch`, `QuickAddIn`, `LifeOsMapping`; phase models later | `extra='forbid'`, `Literal` for fixed keys. |
| `app/mcp/tasks.py` | `task_add`, `task_list`, `task_update`, `task_complete` (S1b); `task_brief` (S2b); `inbox_add` (S4b) | Registered on the same `mcp` object; imported by one line at the bottom of `mcp/server.py` (§9 rule 3). |
| `app/tasks/task_jobs.py` | Celery tasks `tasks.purge_deleted` (S1a), `tasks.send_notice` (S2a), `tasks.transcribe_voice`, `tasks.voice_sweep` (S4a) | Thin; pass ids only. Imported in `app/tasks/__init__.py`. Named so autodiscovery's `tasks.py` convention is not triggered by accident. |

The CRM store (`services/crm/store.py`) keeps its invariant: every function
that reads `tasks` or `task_links` takes `workspace_id` first and filters on
it, so personal and project tasks never reach a CRM view, search, export or
Today (FR-TASK-VIS-04). The task store has the opposite invariant: none of its
reads is returned by a route without passing `visibility`; S1b's sweep test
proves it end to end.

### 5.3 The write path (FR-TASK-DATA-05, -10, -15; TQ-02; NFR-TASK-03)

```text
task route / create_task ─▶ load rows + Viewer ─▶ visibility + rules ─▶ ops ─▶ store.rpc('task_apply', actor, ops)
                                                                              │
                         Postgres, one transaction:                          ▼
                         set_config('crm.actor', actor, true)
                         per op: re-check the caller's right to the row (CRM04), expect_updated_at (CRM09)
                         INSERT/UPDATE tasks / task_links ──AFTER trigger crm_audit_row──▶ audit_log
                         COMMIT ─▶ deferred tasks_workspace_check: workspace ⇔ CRM links (CRM22)

CRM route ─▶ services/crm/records ─▶ store.rpc('crm_apply', actor, ops, p_task_needs_link := true)
```

- `actor = {"user_id", "kind": user|system|import, "channel": web|mcp|null}`,
  plus `"suggestion_id"` only when the caller set one (009). No
  `workspace_id`: for a task write the workspace is the task's, never the
  request's, and the audit trigger reads it from the row (for a `task_links`
  row, from its task). Built by `TaskActor.as_rpc()` (contracts §4; the same
  shape as data-model §5.2). `crm_apply` keeps its own actor, with
  `workspace_id`, as `007` built it.
- **`task_apply` re-checks** (data-model §5.2): a row with a workspace needs an
  active membership of the actor there (role decided in Python); a row with no
  workspace needs the actor to be its creator or assignee, or a member of the
  project rule's audience for reads only (writes refused, TP-02), or kind
  `system`/`import`. Python decides the fine rule; the function is the
  backstop against a caller that forgot.
- **Links first time.** Giving a personal or project task its first CRM link
  writes `UPDATE tasks SET workspace_id` and the link insert in one call; the
  deferred check sees both (FR-TASK-DATA-15).
- **Last CRM link.** Python refuses 422 "A CRM task needs at least one linked
  record; delete it instead." through CRM routes, task routes and
  `task_update`; the deferred check refuses it again.
- **Linkless purge.** `crm_purge` removes links to a purged record and purges,
  in the same transaction, every task of that workspace left with no CRM link
  (FR-TASK-DATA-14).
- **Personal trash.** `tasks.purge_deleted` (daily 01:20 IST) calls
  `task_purge_expired(actor system, now - 90 days)`, which deletes tasks with
  no workspace deleted before then, audited `.purged` (FR-TASK-DATA-11, TP-12).
  CRM tasks keep the CRM Trash and its purge job.
- **Status and completed at.** `rules.status_values(new, was, now)` sets
  `completed_at` exactly when status becomes Done and clears it otherwise; the
  existing CHECK stays (FR-TASK-DATA-03).
- **Bucket.** `rules.default_bucket(due_on, today)` fills a missing bucket on
  every open or waiting write; a CHECK requires one (FR-TASK-DATA-08).

### 5.4 Visibility — `domain/tasks/visibility.py` (spec §3, FR-TASK-VIS)

Facts, loaded once per request by `services/tasks/viewer.py`:

```text
Viewer: id, account_type, tva_role, memberships {workspace_id: role} (active only),
        allocated_project_ids (every allocation row of theirs, released or not: "are or were", TP-01),
        tasks_level
TaskFacts: id, workspace_id, created_by, assignee_id, project_id (from the tva_project link),
           deleted_at, parent_id
```

| Function | Returns |
|---|---|
| `can_see_project(viewer, project_id)` | lead/manager/admin: true; `user`: `project_id ∈ allocated_project_ids`; `crm_only`: false (§3.1) |
| `sight(viewer, task)` | `none` / `read_only` / `edit`. CRM task (workspace set): `edit` for an admin or member, `status_only` for a viewer who is the assignee, `read_only` for another viewer, `none` for a non-member, creator and assignee included (§3.1, §3.3). No workspace: creator or current assignee → `edit`; project task seen through the project rule → `read_only` (TP-02); else `none`. A `crm_only` account sees a project task with no CRM link only as its creator or assignee (FR-TASK-VIS-07). |
| `may_delete(viewer, task)` / `may_restore` | CRM task: `007` rules (Q-36, FR-CRM-DEL-01); else creator only (§3.3) |
| `project_field(viewer, task)` | `full` / `name_only` / `absent` (FR-TASK-VIS-07): CRM task and the viewer cannot see the project → absent; personal/project task, creator or assignee who cannot see it → name only |
| `parent_field(viewer, parent)` | the parent, or `{"private": true}` with no id or title (FR-TASK-DATA-09) |
| `audience(task_draft, names)` | the "Visible to: …" sentence of FR-TASK-VIS-08 |
| `history_visible(viewer, row, era)` | private-era audit rows (written while the task had no workspace) only to the creator and to whoever was assignee at that row (FR-TASK-VIS-05); the era and the assignee at each row come from replaying the task's audit rows in order. Every row of a task carries the task's workspace at the time, `task_links` rows included (048's `crm_audit_row` takes a link row's workspace from its task, data-model §5.4), so a project link written on a Labs task is never mistaken for a private-era row |

Every route answers a task the caller may not see with 404 "No such task."
(FR-TASK-VIS-02), including a parent id and a link to a project the caller
cannot see (FR-TASK-DATA-06, -09).

### 5.5 What changes in the CRM (spec §8; FR-TASK-CHG)

Every place below is either changed or pinned by a test that proves it is
unaffected. The list is the plan's (spec §8.2 "the plan owns the list").

| Place | Change |
|---|---|
| `crm_apply` (027) | Replaced in 048: accepts `tasks`, `task_links` (triggers renamed `<table>_crm_audit`); new third parameter `p_task_needs_link boolean DEFAULT true`; the link check counts CRM-type links only |
| `crm_check_refs` (027) | Replaced in 048: `task` → `tasks`; a `tva_project` row is skipped (its `project_id` FK checks it) |
| `crm_deal_transition` (033) | Replaced in 048: table names; "open" = `status IN ('open','waiting')`; `close_tasks` sets `cancelled` with no `completed_at` (FR-TASK-CHG-04); created tasks get `status 'open'`, `priority 'normal'`, `source 'rule'` |
| `crm_purge` (042) | Replaced in 048: table names; purge linkless tasks of the workspace (FR-TASK-DATA-14) |
| `crm_merge_companies` (042), `crm_search` (041), `crm_query` + `crm_query_rank` (041), `crm_flag_facts` (039), `crm_set_flags` (038) | Replaced in 048: table names; open = open or waiting; the shared status rank array becomes `{open,waiting,done,cancelled,targeted,invited,registered,attended,engaged,meeting_booked,converted,not_interested,unsubscribed}` (campaign-member keys kept); priority rank `{urgent,high,normal,low}` (data-model §5.4) |
| `crm_audit_row` (039) | Replaced in 048 (data-model §5.4): a `task_links` row with no workspace (a project link) is audited with its task's workspace; referential `SET NULL` / `CASCADE` from a deleted profile on the new person columns is recorded as the system instead of refused. The noun fallback is unchanged (`task.created` etc.). Pinned by a db test. Not replaced again by 008 after S1a (§9) |
| Select lists, seeded views, field registry | 047: `task.status` gains `open`, `waiting`, `cancelled`, deactivates `todo`, `in_progress` (FR-TASK-CHG-11; the `crm_select_options_keyed_active` CHECK gains that exception); saved-view status filters rewritten (§8.2); `crm_object_fields` gains `task.priority` and `task.source` |
| `services/crm/store.py` | Table names in every task query, `LINK_TABLES`, the 23505 map (`task_links_target`); `tasks_for_today` (FR-TASK-CHG-05), `tasks_due_by` (reminders), open-task lists and flag rows use `status IN (open, waiting)`; `tasks_completed_between` (dashboard Activity) stays `status = done` and workspace-scoped (cancelled never counts) |
| `services/crm/records.py` | Task create/update: default status `open`, `rules.default_bucket` fills a missing bucket on create and on any update that leaves the task open or waiting (reopen included), `_status` for completed at, old keys `todo`/`in_progress` read as `open` (TP-05), refusing removal of the last CRM link; the record task tab sorts open and waiting first; PATCH link replacement never touches a `tva_project` link |
| `schemas/crm.py`, `api/crm/records.py` | `TaskFields.status` and `TaskStatus` accept `open`, `waiting`, `done`, `cancelled` and the old keys as input; `GET /crm/tasks?status=open` means open or waiting (FR-TASK-CHG-10) |
| `services/crm/present.py` | Task presenter: new keys and labels; Notes relabel; priority, tags, waiting on; project link through `visibility.project_field`; timeline reads `target_table IN ('crm_tasks','tasks')` and `('crm_task_links','task_links')` (FR-TASK-CHG-07) |
| `domain/crm/today.py`, `services/crm/today.py` | Section 3 = open or waiting tasks in the Today bucket or due today or earlier; loose ends' "no open task" = no open or waiting task (FR-TASK-CHG-05). Only the existing tasks section is touched (§9 rule 3) |
| `domain/crm/flags.py`, `services/crm/flags.py` | Task overdue, Stale deal and Loose end read open or waiting |
| `domain/crm/importer.py`, `services/crm/importer.py` | Twenty To do / In progress → `open` (FR-TASK-CHG-09); a missing bucket filled by `rules.default_bucket`; table names |
| `services/crm/views.py` | `_FIXED_LABELS` gains priority and source labels (data-model §3.3, owner decision of 18 October 2026) |
| `services/crm/deals.py` | Closed Lost closes open and waiting tasks (as `cancelled`, through the transition) |
| `services/crm/merge.py`, `search.py`, `digests`, `export.py`, `dashboard.py` | Table names; digests' task lines use open or waiting; export unchanged in shape |
| `api/admin.py` `read_audit`, `services/supabase.py` `list_audit` | Also exclude `target_table` in the task tables (`tasks`, `task_links`, `task_comments`, `task_import_runs`, `inbox_items`), so a task with no workspace never reaches the TVA audit page or `audit_log` (FR-TASK-VIS-05, FR-TASK-CHG-07) |
| `mcp/crm.py` | Only the bodies (docstrings) of `crm_add_task`, `crm_complete_task`, `crm_create`, `crm_update`, `crm_list` name the new statuses (§9 rule 3). No `crm_*` tool added or renamed (FR-TASK-MCP-04); the tool count is asserted only by `test_crm_mcp.py`, which 009 owns |
| Frontend CRM | `lib/api/crm-types.ts`, `components/crm/task-dialog.tsx` (reopen → Open; Notes; Bucket label), `today-list.tsx`, `app/(crm)/crm/[ws]/tasks/page.tsx`, `lib/crm/view-fields.ts` |

### 5.6 My Tasks, quick add and the panel (FR-TASK-LIST, -ADD)

- **List.** `GET /tasks` returns the four sections already grouped by the
  effective bucket in the caller's zone (`profiles.timezone`, FR-TASK-CHG-06),
  sorted due date (none last), priority, title; each item carries
  `days_overdue` and its kind. Closed tasks (done, cancelled) come in a
  separate page, newest first. One query on `(assignee_id, status, due_on)
  WHERE deleted_at IS NULL`, then links, then the viewer filter; at 5,000 rows
  that is three PostgREST calls (paged at 1,000).
- **Quick add.** The parser (`domain/tasks/quick_add.py`) is pure and runs in
  the backend, so the web box, voice candidates (phase 4) and tests share one
  rule set. `POST /tasks/quick-add/preview` returns the parsed draft, the
  "Visible to" sentence and any questions; the box re-previews on a 250 ms
  debounce and on each answer, and Save stays disabled while a question is
  open (FR-TASK-ADD-02, -04). `POST /tasks` saves the draft as answered. Both
  are under 1 s (NFR-TASK-01).
- **Optimistic.** The My Tasks page inserts the saved task into its section
  when `POST /tasks` returns (and the quick-add box emits a window event the
  open page listens to), so the creator sees it within 2 s without reload
  (FR-TASK-ADD-06). No polling, no push.
- **One page, two shells.** `components/tasks/my-tasks.tsx` is the page. It
  is mounted at `/tasks` inside `(portal)` for staff and at `/crm/[ws]/my-tasks`
  inside the CRM shell for everyone with a workspace, `crm_only` included,
  because `(portal)/layout.tsx` sends `crm_only` accounts to `/crm`
  (FR-TASK-LIST-08). Both read and write only through `/api/proxy`.
- **Panel.** `components/tasks/task-panel.tsx` (a `sheet`) shows every field,
  links, parent and subtasks, "Visible to" (recomputed through
  `POST /tasks/audience` before a link or assignee is saved), and history
  (FR-TASK-LIST-06). Phase 2 mounts the same panel in the CRM record Tasks tab
  and the TVA project page (FR-TASK-VIEW-03).
- **Quick-add box.** `components/tasks/quick-add.tsx` in the portal header
  (desktop sidebar top and phone header) and the CRM shell header, shown when
  `me.tasks.level >= 1`; shortcut `q` when focus is not in an editable field
  (NFR-TASK-06).

### 5.7 Claude — `app/mcp/tasks.py` (FR-TASK-MCP)

- Each tool is one `await _api(ctx, …)` to a `/tasks` route with no checks of
  its own (`004` §3). Imports only from `app.mcp.server`. `server.py` gains one
  line after the CRM import: `from app.mcp import tasks as _tasks  # noqa:
  E402, F401`.
- Docstrings open with the "who may call" line (FR-TASK-MCP-02); every write
  says CONFIRM FIRST. Annotations: reads `READ`, writes `WRITE`.
- `task_add` passes `assignee` (name or email) to `POST /tasks`, which
  resolves it; an ambiguous name is 422 naming the matches.
- `INSTRUCTIONS` (in `server.py`) gain one tasks paragraph (FR-TASK-MCP-05):
  the four kinds, who sees what, statuses and buckets, priorities, that tasks
  never carry hours, and that CRM-linked tasks follow CRM roles. This and the
  docstrings of `my_day`/`log_day` (S3a, FR-TASK-CHG-08) are 008's only other
  edits to `server.py`. This goes beyond rule 3's "one import line"; §9 rule
  3a (a), approved by the owner on 18 October 2026, allows it (008 adds its
  paragraph and the two docstrings; 009 appends exactly one paragraph after
  008's).
- `tests/test_mcp.py` (FR-TASK-CHG-03): `test_every_operation_has_a_tool`
  also leaves out tools named `task_*` or `inbox_add` and operations under
  `/api/v1/tasks`; `test_the_crm_exception_is_the_specs` keeps its asserted
  substring and gains the tasks clause. `tests/test_task_mcp.py` asserts
  exactly the task tools of the phases built.

### 5.8 Background work

008's beat entries go in their own block at the end of `app/worker.py`,
headed `# Spec 008 — tasks`, added with `celery_app.conf.beat_schedule.update({...})`
so no line of the existing dict changes (§9 rule 3). Placement is fixed: the
008 block sits directly after 007's code and before 009's block, which is
always last. 008 adds later entries (S4a's voice sweep) inside its own block;
when 009 rebases it keeps its block below 008's, never interleaved.

| Job | Task name | Schedule (IST) | Slice | Notes |
|---|---|---|---|---|
| Personal trash purge | `tasks.purge_deleted` | 01:20 daily | S1a | Tasks with no workspace deleted > 90 days ago (TP-12) |
| Assignment, completion, hand-back messages | `tasks.send_notice` | enqueued on the event | S2a | Ids only; text built at send; once per `(task, event, recipient, occurred_at)` via `task_notice_sends` (FR-TASK-DEL-07) |
| Voice transcription | `tasks.transcribe_voice` | enqueued on upload | S4a | `ignore_result=True`; writes the transcript to the upload row and returns nothing (FR-TASK-VOICE-05); deletes the audio in `finally` |
| Voice sweep | `tasks.voice_sweep` | every 30 min | S4a | Deletes audio and transcripts older than 24 h, failed ones included (FR-TASK-VOICE-02, -05) |

No roll-over job (TP-06), no reminder job for non-CRM tasks (FR-TASK-DEL-08),
no brief or review message (TP-08).

### 5.9 Day-log suggestions (phase 3, FR-TASK-LOG)

`timesheet_service.day_for` (the `GET /timesheet/day` read, staff only)
gains `suggestions` when the caller's level is ≥ 3 and the day is loggable:
tasks assigned to them, `status = done`, `completed_at` on that date in
Asia/Kolkata, `source <> 'import'`, each with the project when it is linked to
a confirmed, unarchived project. `someone_elses_week`, team weeks and sign-off
never call it (FR-TASK-LOG-07). Accept and ignore are client state on the Log
a day page; Save day is unchanged (FR-TASK-LOG-04, -05). A suggestion is shown
as added when its title already appears in a note that day. The merge of
accepted tasks into one line's note (within 500 characters, zero hours) and
the note-visibility warning live only in the frontend, with no backend copy;
they are proven by a local Playwright test (`frontend/e2e/day-log.spec.ts`,
S3a).

### 5.10 Voice (phase 4, FR-TASK-VOICE)

Upload (multipart ≤ 4 MB) → row in `voice_uploads` (status `uploaded`) and the
object in the private `task-voice` bucket under `<user_id>/<upload_id>` →
`tasks.transcribe_voice(upload_id)` posts it to Groq `whisper-large-v3` with
`httpx`, writes `transcript` and `status = 'transcribed'`, deletes the object
in a `finally` → the page polls `GET /tasks/voice/{id}` (2 s, at most 60 s)
and shows candidates from `voice_split.split()` + `quick_add.parse()` →
confirm or drop per candidate; the transcript is cleared when the last one is
handled. Nothing logs the transcript; structured logs carry the upload id only.

---

## 6. How each spec area maps onto existing code

| Spec area | Existing code reused | Change |
|---|---|---|
| §5.1 data | `crm_tasks`, `crm_task_links`, `crm_apply`, `crm_audit_row`, `crm_check_refs` | Rename, widen, deferred check, `task_apply` |
| §5.2 visibility | CRM guard (`api/crm/deps.py`), `007` §3.3 matrix (`domain/crm/permissions.py`), `CurrentUser.is_lead` | `domain/tasks/visibility.py`, `services/tasks/viewer.py` |
| §5.3 My Tasks | `007` Today's effective "due or overdue" rule, `domain/crm/flags` overdue rule, `lib/use-async.ts`, `components/ui/sheet` | `GET /tasks`, `components/tasks/*` |
| §5.4 capture | `007` task dialog, `marked` | `domain/tasks/quick_add.py`, quick-add box |
| §5.5 Claude | `_api`, annotations, docstring convention, `test_mcp.py` coverage | `mcp/tasks.py`; prefix exclusion |
| §5.6 import | `007` import's dry-run/real-run pattern, file hash, 24 h rule, the 4 MB proxy | Own run table and content key (FR-TASK-IMP-04, -05) |
| §5.7 switches | `app_settings`, `update_setting`, `update_user`, Admin People | `profiles.tasks_level`, `tasks_default_level` |
| §6.1 delegation | `notify.deliver`, `dispatch.fire_and_forget`, `crm_notify` bell (CRM tasks) | `task_comments`, `task_notice_sends`, `tasks.send_notice` |
| §6.3 brief | `services/crm/today.build` (read, not recomputed) | `GET /tasks/brief`, `task_brief` |
| §6.4 day log | `timesheet_service.day_for`, Save day, the edit window | `suggestions` key; Log a day UI |
| §6.5 recurrence | — | `python-dateutil`; `domain/tasks/recurrence.py` |
| §6.6 voice | `httpx`, the 4 MB proxy, Celery id-only pattern | `voice_uploads`, `task-voice` bucket |
| Help | `docs/guides/NN-*.md` → `scripts/build-guides.mjs` → `/help` | `docs/guides/10-tasks.md` with frontmatter `order: 11` (§9 rule 3 fixes the file name; `10-crm-admin.md` already holds order 10, and `009`'s `docs/guides/12-crm-intelligence.md` takes order 12, so `/help` order never depends on a file-name tie-break) |

---

## 7. Project structure

### Documentation (this feature)

```text
specs/008-tasks/
├── source.md        # owner's original (behaviour only)
├── spec.md          # what, operative
├── plan.md          # this file
├── data-model.md    # tables, columns, functions, ALTERs, migrations 046–055
├── contracts/api.md # /api/v1/tasks routes, changed routes, task tools, the 009 interface
└── tasks.md         # build slices S1a–S4b
```

### Source code

```text
supabase/migrations/046_…055_*.sql
backend/app/
├── api/tasks/…                       (new)
├── domain/tasks/…                    (new; service.py is the 009 interface)
├── services/tasks/…                  (new)
├── schemas/tasks.py                  (new)
├── mcp/tasks.py                      (new) ; mcp/server.py (one import line + INSTRUCTIONS paragraph)
├── tasks/task_jobs.py                (new) ; tasks/__init__.py (one import)
├── api/me.py, api/admin.py, api/timesheet.py (phase 3), main.py, config.py (phase 4), worker.py (008 block)
└── services/crm/…, domain/crm/…, schemas/crm.py, api/crm/records.py  (§5.5)
backend/tests/
├── test_task_*.py, tasks_fake.py     (new)
├── crm_fake.py and the CRM tests that pin statuses or table names (updated)
└── db/test_task_*_db.py              (new) ; db/test_crm_*_db.py (table names)
backend/scripts/simulations/tasks_load_5k.py
frontend/
├── app/(portal)/tasks/page.tsx, app/(portal)/tasks/import/page.tsx
├── app/(crm)/crm/[ws]/my-tasks/page.tsx
├── app/(portal)/layout.tsx (nav + quick add), components/crm/shell.tsx (nav + quick add)
├── app/(portal)/admin/page.tsx (phase level), app/(portal)/analytics/page.tsx (phase 2 panel), app/(portal)/timesheet/page.tsx (phase 3)
├── components/tasks/…                (new)
├── lib/api/tasks.ts, lib/api/task-types.ts, lib/tasks/labels.ts (new)
└── lib/supabase/middleware.ts (/tasks protected)
docs/architecture/tasks.md, docs/guides/10-tasks.md, docs/README.md (one line), docs/operations/deployment.md
```

**Structure decision.** The two-app layout is kept. Tasks are a package inside
each layer.

---

## 8. Testing strategy (NFR-TASK-08)

| Kind | Where | What |
|---|---|---|
| Domain | `tests/test_task_rules.py`, `test_task_visibility.py`, `test_task_quick_add.py` (fixed clock Tue 20 Oct 2026 IST), `test_task_lifeos_mapping.py`, `test_task_delegation.py`, `test_task_suggestions.py`, `test_task_recurrence.py`, `test_task_voice_split.py` | Every rule of §5 and §6 as pure functions, classes per spec section, plain-sentence names |
| Route | `tests/test_task_*_routes.py` with `tests/tasks_fake.py` (implements `services/tasks/store.py` over the **same** in-memory tables as `tests/crm_fake.py`, including Python fakes of `task_apply`, the deferred check and `task_purge_expired`) | Guards, phase 403s, 404 for unseen tasks, refusal sentences |
| CRM regression | the existing `tests/test_crm_*.py`, updated only where §8 changes behaviour | NFR-TASK-08, AC "every CRM task test passes" |
| Privacy sweep | `tests/test_task_privacy_sweep.py` | FR-TASK-VIS-06: walks `app.routes` and the MCP registry. **Scope**, so 009's new routes and tools never break it: it requires a fixture for every route under `/api/v1/tasks`, every task tool (`task_*`, `inbox_add`), and an explicit list in the test of the existing CRM, TVA and admin routes and `crm_*` tools that return, count, search, export or audit-read tasks; any other `/api/v1/crm` route and `crm_*` tool is covered by the CRM's own isolation sweep and is reported, not failed. Each of the **ten** account kinds of the FR (TVA admin, manager, owner, the creator's TVA lead, CRM workspace Admin, Viewer, `crm_only`, former assignee, Waiting-on person, removed workspace member) is tried against a private and a shared personal task. A committed self-test registers a dummy `/api/v1/tasks` route and a dummy `task_` tool and asserts the fixture check fails |
| Interface | `tests/test_task_create_interface.py` | `create_task` signature, `TaskActor` fields, `TaskCreate` fields and defaults exactly as contracts §4 (the rule-2 fields and the optional ones), same refusals and audit as `POST /tasks`, `suggestion_id` reaching `crm.actor` |
| Database | `tests/db/test_task_migration_db.py`, `test_task_write_path_db.py`, `test_task_links_db.py`, later `test_task_*_db.py` | Deferred check at commit; workspace only NULL → value; last CRM link refused; purge of linkless tasks; cascades; `tva_project` FK; RLS on and no grants; `EXECUTE` denied; audit rows with actor; the migration's data mapping from a seeded pre-008 database |
| Logs | `tests/test_task_logging.py` | No title, note or transcript in captured logs of a run that writes tasks (NFR-TASK-04) |
| Performance | `backend/scripts/simulations/tasks_load_5k.py` | Seeds 5,000 tasks for one person locally, times `GET /tasks` and `POST /tasks` through the API; p95 < 1 s (S1e). An opt-in recorded run, not an unskipped test: its output goes in the S1e commit message, and `test_task_acceptance.py` names it as the proof of NFR-TASK-01 (as `TestMapping` is for the migration-mapping line) |
| E2E | `frontend/e2e/tasks.spec.ts` (local) | Quick add at a fixed clock, My Tasks sections, CRM-only seller's My Tasks, keyboard path |

### 8.1 The database lock (§9 rule 5)

Every step that runs `npx supabase db reset` or `CRM_DB_TESTS=1` tests runs
as **one** shell invocation, from the repository root (an agent's tool calls
do not share a shell, so the lines must never be sent separately):

```bash
bash -c '
LOCK="$(git rev-parse --git-common-dir)/db-test.lock"
until mkdir "$LOCK" 2>/dev/null; do sleep 5; done
trap "rmdir \"$LOCK\"" EXIT
npx supabase db reset && (cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q)
'
```

`trap … EXIT` releases the lock when that one shell exits, on success and on
failure; `&&` means a failed reset never lets tests run against a half-reset
stack. The reset always comes first, so the shared local stack holds exactly
this branch's migrations (046–055 and nothing of 009's 060–079). The
`TASKS_MIGRATION_CHECK=1` run uses the same one-invocation form: `npx supabase
db reset --version 045 && <seed> && npx supabase migration up && (cd backend
&& TASKS_MIGRATION_CHECK=1 CRM_DB_TESTS=1 uv run pytest -m db -q
tests/db/test_task_migration_db.py) ; npx supabase db reset` inside the same
`bash -c` with the same lock and trap.

---

## 9. Coordination with `009-crm-intelligence`

Two sessions build 008 and 009 at the same time. This plan follows these
rules; `009`'s plan follows the same ones.

| # | Rule | What 008 does |
|---|---|---|
| 1 | Migrations: 008 only `046`–`059`; 009 only `060`–`079`. 009 may assume every 008 migration ran; 008 never needs a 009 one. | 008 uses `046`–`055`, leaving `056`–`059` spare for fixes. 046–050 land in S1a's one commit. After S1a, 008 does not replace any of the seven functions 009 replaces: `crm_audit_row`, `crm_purge`, `crm_merge_companies`, `crm_query_rank` (also replaced by 048) and `crm_remove_emails`, `crm_touch_updated_at`, `crm_job_latest` (009 copies each from the latest body and would override a fix); if unavoidable, the commit's first line announces it and 009 ships a `068`+ re-copy (data-model §1). No 009 slice from S1b on starts until S1a is committed (`048_tasks_crm_functions.sql` present). 008's `TestAfterMigration` (T037) asserts no function body names `crm_task`; it also runs at every 009 db gate, and 009 adds no second copy of it. |
| 2 | **Task-creation interface**: `backend/app/domain/tasks/service.py` `create_task(actor, payload)`; `payload: TaskCreate` in `backend/app/schemas/tasks.py` with `title`, `notes`, `due_on`, `bucket` (null = derived), `priority` (default normal), `assignee_id` (null = actor), `workspace_id`, `links [{record_type, record_id}]`, `source`, `source_ref`. Same validation, visibility and audit as a manual create; returns the created task. | Built in S1a, exactly as contracts §4. The rule-2 fields keep their names, types and defaults; S1a also adds optional fields with defaults (`tags`, `parent_id`, `status`, `waiting_on_id`, `waiting_on_text`, `category_id`, `completed_at`) that 008's own callers need and 009 may ignore. The actor is `TaskActor(user, kind, channel, suggestion_id=None)`; 009 builds it (contracts §4) and `suggestion_id` reaches `crm.actor`. Repeated links are refused, so 009 de-duplicates. The whole S1a shape is pinned by the interface test and never changes after S1a. Nobody else changes it. |
| 3 | Shared files. `mcp/crm.py`: 008 changes bodies of existing task-related `crm_*` tools only; 009 appends new tools at the end and owns the test that counts `crm_*` tools. 008's tools live in `mcp/tasks.py`, registered by one import line in `server.py`. Today: 008 owns changes to the existing task section; 009 appends "To review" after the five sections. Beat: each plan its own block. CRM Settings: 008 adds nothing. Help: 008 writes `docs/guides/10-tasks.md`, one line in the guides index. Portal header (quick add) and portal nav: 008. `pyproject.toml`: own dependencies only. | 008 never asserts the `crm_*` tool count anywhere (only `tests/test_crm_mcp.py`, 009's, does), never adds a `crm_*` tool, never touches the CRM Settings page, and edits `domain/crm/today.py` / `services/crm/today.py` only inside the tasks section (`rules.tasks`, `store.tasks_for_today`) and the loose-ends "open task" test. Beat: the 008 block first, 009's after it (§5.8). Help: `docs/guides/10-tasks.md` at `order: 11`; 009's `docs/guides/12-crm-intelligence.md` at `order: 12`; `10-crm-admin.md` keeps order 10. |
| 3a | **Extensions of rule 3, approved by the owner on 18 October 2026 (the same rule as `009` plan §6 rule 3a):** (a) `server.py`: 008 adds its tasks paragraph to `INSTRUCTIONS` and edits the `my_day`/`log_day` docstrings (spec FR-TASK-MCP-05, FR-TASK-CHG-08); 009 may append exactly one `INSTRUCTIONS` paragraph after 008's (its FR-CRMAI-MCP-05). (b) `components/crm/shell.tsx` (the CRM shell): 008 owns its header (quick add) and places its My Tasks nav entry; 009 adds only its Review entry, as one appended line rendering a component in its own file, after 008's entry. (c) Existing 007 files both plans edit (`services/crm/store.py`, `services/crm/records.py`, `domain/crm/flags.py`, `services/crm/flags.py`, `domain/crm/digests.py`, `domain/crm/objects.py`, `services/crm/export.py`, `schemas/crm.py`, `api/crm/records.py`, `app/config.py`, `app/tasks/__init__.py`, `frontend/lib/api/crm-types.ts`, `frontend/lib/crm/view-fields.ts`, `docs/operations/deployment.md`, and the tests `test_crm_today.py`, `test_crm_digests.py`, `test_crm_dashboard.py`, `test_crm_flags.py`, `test_crm_automations.py`, `test_crm_settings_lists.py`, `test_crm_object_fields_registry.py`, `test_crm_csv_io.py`): 008 changes existing lines only (renames, status rules, bucket defaults); 009 only appends new functions, keys and tests at the end of each file or class and never edits a line 008 touched; on rebase 009 keeps 008's hunks verbatim. | 008 edits `server.py` and `shell.tsx` only as (a) and (b) say, and in the (c) files changes existing lines and adds nothing at their ends. |
| 4 | Branches. 008 on `008-tasks` in the main checkout; 009 on `009-crm-intelligence` in `../nldw-task-master-009`, branched from `008-tasks` after these plans are committed, rebased onto `008-tasks` at the start of each 009 slice. | 008 never rebases onto 009 and never merges it. Before S1a (and before 009's T001), one step owned by the 008 session with the owner's approval commits both `specs/008-tasks/` and `specs/009-crm-intelligence/` on `008-tasks`, and nothing else (`supabase/config.toml` excluded) (tasks.md S0). |
| 5 | One local Supabase stack. Hold the lock for `db reset` and db tests; reset first. | §8.1, and every slice's gate. |
| 6 | `supabase/config.toml` (`enable_signup=true`) is never committed. | Every slice's gate checks `git diff --cached --name-only`. |

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| A function still names `crm_tasks` after the rename and fails at run time | 048 replaces every function the grep of `supabase/migrations` finds naming the old tables (§5.5); `tests/db/test_task_migration_db.py` asserts no function body in `pg_proc` contains `crm_task` (it also runs at every 009 db gate, guarding 009's copies), and calls each replaced function once |
| 047 and 048 are separate files: if 048 failed in production after 047, CRM task writes would fail | Both apply in one `supabase db push`, after `db reset` has applied them from empty in CI and locally; 048 is replace-only DDL with no data; release in a quiet hour (deployment.md) |
| Linkless live tasks exist in production | 046 moves them to their workspace Trash and raises a NOTICE with the count; deployment.md has the `SELECT count(*)` to run and report before `db push` (spec §8.2) |
| A private task leaks through a route nobody thought of | FR-TASK-VIS-06's sweep enumerates `app.routes` and the MCP registry and fails on any task route or task tool without a fixture, and on any route in its explicit task-touching list; other CRM routes are covered by the CRM isolation sweep (CRM routes filter on `workspace_id`, so a task with no workspace cannot reach them, §5.2) |
| Status keys change under existing prompts and saved views | TP-05: old keys accepted as input; 047 rewrites every saved view's status filters; a db test asserts none references `todo` or `in_progress` |
| Audit rows of private tasks reach the TVA audit | `list_audit` excludes the task tables; a test writes a private task and reads `GET /admin/audit` and `audit_log` |
| The 009 session needs `create_task` before 008 merges | It is in S1a, the first slice; 009 references it only until 008 merges (§9 rule 2) |
| `supabase/config.toml` committed by accident | Gate in every slice |
