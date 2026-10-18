# How tasks work

How tasks work *today*. What they must do lives in
[`specs/008-tasks/spec.md`](../../specs/008-tasks/spec.md), the tables and
database functions in [`data-model.md`](../../specs/008-tasks/data-model.md),
the routes, tools and the interface `009` calls in
[`contracts/api.md`](../../specs/008-tasks/contracts/api.md). Read
[`crm.md`](crm.md) first: tasks reuse the CRM's write path, audit trigger
and guard, and this page only says what is different.

All four phases are built: the shared table, personal and project tasks,
privacy, My Tasks, quick add, the Life OS import and the per-person switch
(phase 1); delegation, the views and the morning brief (phase 2); day-log
suggestions and recurrence (phase 3); voice capture, the Ideas and Reading
inbox and the weekly review (phase 4). Six task tools: `task_add`,
`task_list`, `task_update`, `task_complete`, `task_brief` and `inbox_add`.

---

## One table, two writers

There is no second task table. The CRM's `crm_tasks` and `crm_task_links`
were renamed `tasks` and `task_links` (migration `047`), keeping every row,
id and link, and widened so a row can have no workspace:

| Kind | Links | `workspace_id` |
|---|---|---|
| Private | none; assignee is the creator | empty |
| Shared personal | none; assignee is someone else | empty |
| CRM | one or more CRM records of one workspace (and maybe a TVA project) | that workspace |
| Project | one TVA project (`record_type = 'tva_project'`), no CRM link | empty |

The kind is never stored; `domain/tasks/rules.kind` works it out from the
links. A task has at most one project link, and only a Labs task may carry
one alongside its CRM links.

Two database functions write the table, both in one transaction per call:

```
  CRM route ─▶ services/crm/records ─▶ crm_apply(actor, ops, p_task_needs_link := true)
                                        (048: the CRM writer, now aware of the new names)

  task route / create_task ─▶ domain/tasks/service ─▶ services/tasks/store
                          ─▶ task_apply(actor, ops)  (049: accepts rows with no workspace)
```

- **`crm_apply`** keeps writing CRM tasks as `007` built it. Its third
  parameter, `p_task_needs_link`, is the old "a task needs a linked record"
  rule, now a parameter of the writer rather than a table constraint: every
  CRM path that creates a task passes `true`.
- **`task_apply`** writes `tasks`, `task_links` and `task_import_runs` (and
  the later task tables) only. Its actor has **no** `workspace_id`: a task
  write's workspace is always the row's. For every op it re-checks, in the
  database, that the actor may touch the row as stored — an active member of
  the row's workspace, or the creator or assignee of a task with no
  workspace, or the system or an import — and answers `CRM04` ("No such
  task.") otherwise. Python has already decided the finer rule; this is the
  backstop for a caller that forgot.

Both set `crm.actor`, so the same `crm_audit_row()` trigger audits every
change to a task or link with actor, kind, channel and before and after
values (FR-TASK-DATA-10). A `task_links` row is audited with its task's
workspace (`048`), so a project link written on a Labs task is a Labs row.

## The deferred check

`tasks_workspace_check` (047, tightened by `056`) is a constraint trigger,
`DEFERRABLE INITIALLY DEFERRED`, on `tasks` and `task_links`. At **commit**,
for each live task touched, it requires:

- no CRM link → `workspace_id` empty ("A CRM task needs at least one linked
  record; delete it instead.");
- CRM links → all in one workspace, equal to the task's ("A task cannot link
  records of two workspaces.");
- a project link on a task with a workspace → that workspace is linked to
  TVA ("Only Nunnari Labs tasks can link a TVA project.").

It is deferred because every writer inserts the task before its links (the
CRM writer's apply step, the `033` deal transition). Checked per statement,
a new CRM task would fail before its first link existed. Giving a personal
task its first CRM link writes `UPDATE tasks SET workspace_id` and the link
in the same call, and the check sees both. `056` makes a link removal lock
its task row first: without it, two removals of a task's two CRM links at
once each saw the other link still there, and both committed.

Beside it, `tasks_workspace_fixed` (BEFORE UPDATE) lets `workspace_id`
change only from empty to a value, so a CRM task never becomes personal, by
either writer. Purging a CRM record (`crm_purge`) purges in the same
transaction every task of that workspace left with no CRM link
(FR-TASK-DATA-14).

## Visibility, and why there is no RLS policy

The backend uses the service-role key, which bypasses row-level security, and
no table since `010` has a browser grant. A policy keyed on `auth.uid()`
would protect nothing anyone can reach. So `tasks`, `task_links` and every
new task table have RLS **on with no policy**, `ALL` revoked from `anon` and
`authenticated`, and every task function's `EXECUTE` revoked from `public`,
`anon` and `authenticated` (plan CT-1).

Privacy is one backend function, `app/domain/tasks/visibility.py`, pure, over
two fact bundles: the `Viewer` (`services/tasks/viewer.load`: active
memberships, TVA role, every project the person is or was allocated to,
phase level) and the task. Every task route, every CRM route that returns or
changes a task, every task tool and `create_task` pass through it:

| Function | Decides |
|---|---|
| `sight` | `none`, `read_only`, `status_only` or `edit`. A CRM task: by CRM role, for active members only — creator and assignee get nothing more, and lose it when removed from the workspace. No workspace: creator and current assignee edit; a project task is read only to everyone else who can see the project (TP-02) |
| `can_see_project` | a TVA lead, manager or admin: every project; a TVA user: projects they are or were allocated to; a `crm_only` account: none. A project's lead is a label, not a permission |
| `project_field` | **CRM links win**: on a CRM task, a member who cannot see the project gets no project at all; on a project task, a creator or assignee who cannot see it gets its name only |
| `parent_field` | a parent the reader cannot see shows as `{"private": true}`, no id or title |
| `audience` | the "Visible to: …" sentence the panel and quick add show before saving |
| `history_visible` | rows written while the task had no workspace go only to its creator and to whoever was assignee at that row |

A task the caller may not see is answered **404** "No such task.", never 403.
`tests/test_task_privacy_sweep.py` builds its route list from `app.routes`
and its tool list from the MCP registry, and fails on any `/api/v1/tasks`
route, `task_*` tool, or listed task-reading CRM, TVA or admin route without
a fixture; ten kinds of account then try every fixture against a private and
a shared personal task.

The CRM side keeps its own invariant: every function of
`services/crm/store.py` that reads tasks takes `workspace_id` first and
filters on it, so a task with no workspace never reaches a CRM view, search,
export, dashboard or Today.

## Phase levels

Each phase is switched on per person (C6, TP-11):

- `profiles.tasks_level` (0–4, null = the default) and the
  `app_settings` row `tasks_default_level` (starts `0`, set with
  `update_setting`). Level *n* switches on phases 1 to *n*
  (`domain/tasks/rollout.level_for`).
- `api/tasks/deps.py` `TasksDep` (and `TasksDep2`–`4`) wraps
  `any_account`, so `crm_only` sellers pass, and answers 403 "Tasks are not
  switched on for you yet." below the route's phase. `test_crm_only_refused`
  asserts every route under `/api/v1/tasks` depends on one of them.
- A TVA admin sets a person's level on Admin → People or through
  `update_user` (`changes: {tasks_level}`), audited `user.tasks_level`.
- `GET /me` carries `tasks: {level}` for everyone; both shells show My Tasks
  and the quick-add box only at level 1 or more.
- Assigning a task with no workspace to someone below level 1 is refused
  "*Name* does not have My Tasks yet."

CRM task behaviour never reads the level: the migration applied to everyone
at once, and CRM tasks work for people with no phase switched on.

## Phases 2 to 4

Each later phase is a set of routes behind `TasksDep2`–`4` on the same
write path; nothing below runs for someone below the phase.

- **Delegation (2, `052`).** `tasks.assigned_by` and `accepted_at` make a
  task "new" to its assignee until Accept. Hand back makes the creator the
  assignee and keeps the note as a `hand_back` comment in `task_comments`
  (written through `task_apply`, insert and soft delete only, carrying the
  task's workspace when written). Assigning, completing and handing back
  enqueue `tasks.send_notice` with ids only; `task_notice_sends` is claimed
  `ON CONFLICT DO NOTHING` before sending, so a retried job sends nothing
  twice.
- **Views and the brief (2).** Waiting on, Assigned by me and a TVA project's
  task panel are reads in `services/tasks/listing.py`, each row through the
  viewer filter. `GET /tasks/brief` (and `task_brief`) adds each
  workspace's sections from `services/crm/today.build`, read and never
  recomputed. Nothing is pushed (TP-08).
- **Day-log suggestions (3).** `timesheet_service.day_for` adds
  `suggestions` for the caller's own loggable day only; accept and ignore
  are screen state, and Save day is unchanged.
- **Recurrence (3, `053`).** Completing or cancelling a recurring task
  inserts its next occurrence in the same `task_apply` call
  (`domain/tasks/recurrence.py`, `python-dateutil`); the partial unique
  index `tasks_series_due` refuses a second one in a race.
- **Voice (4, `054`).** The audio sits in the private `task-voice` bucket
  until the `tasks.transcribe_voice` worker has sent it to Groq, and is
  deleted in a `finally`; `tasks.voice_sweep` (every 30 minutes) clears
  audio and transcripts older than 23 h 30 min. The transcript lives only in
  the caller's `voice_uploads` row, never in Celery's result backend or a
  log, and is cleared when the last candidate is handled. Candidates are
  split by fixed rules (`domain/tasks/voice_split.py`) and marked handled
  by `voice_candidate_mark`, one conditional update, so a candidate is
  confirmed once. A task candidate goes through `create_task` with source
  `voice`; an inbox candidate becomes an inbox item.
- **Inbox (4, `055`).** `inbox_items` belongs to its owner alone, admins
  included: `services/tasks/inbox.py` answers 404 for anyone else's item,
  and `task_apply` re-checks the owner. Make it a task builds the task from
  the item, any fields sent replacing only what they name, and creates it
  through `create_task` with the item's `processed_at` and `task_id` (with
  its `updated_at` as read) in the same `task_apply` call: both land or
  neither does.
  `task_id` is `ON DELETE SET NULL`; deleting the owner's profile removes
  the items, audited as the system by `048`'s `crm_audit_row`.
- **Weekly review (4, `055`).** `GET /tasks/review` is computed on read
  (`domain/tasks/review.py`): shown on the caller's Monday in their own zone
  unless `profiles.tasks_review_dismissed_week` holds this week's Monday.
  No job sends it.

## The 009 interface

`backend/app/domain/tasks/service.py` `create_task(actor, payload)`, with
`payload: TaskCreate` (`backend/app/schemas/tasks.py`) and
`actor: TaskActor(user, kind, channel, suggestion_id=None)`, is the one way
in for a new task. `POST /tasks`, quick add's save, the Life OS import and
`009`'s Review Queue all call it, so they get the same validation,
visibility and audit. `TaskActor.as_rpc()` puts `suggestion_id` into
`crm.actor` only when set, so a task made from an approved AI suggestion
says so in its audit rows. The shape is pinned character for character by
`tests/test_task_create_interface.py` and does not change.

## What changed in the CRM

Every place is changed or pinned by a test (plan §5.5):

- **Statuses**: `open`, `waiting`, `done`, `cancelled`. "Open" everywhere it
  meant "not done" — Today section 3, the Task overdue, Stale deal and Loose
  end flags, reminders, open-task lists, digests — now means open or
  waiting. The status select list's `todo` and `in_progress` are deactivated
  and kept for history; saved-view filters were rewritten (`047`).
- **Old keys as input**: `crm_create`, `crm_update`, `crm_list` and
  `GET /crm/tasks` still accept `todo` and `in_progress`, read as `open`;
  `status=open` returns open and waiting tasks.
- **Closed Lost** sets a deal's open and waiting tasks to `cancelled`, not
  `done`, so they never count in Dashboard Activity, which counts `done`
  only.
- **Bucket** is required on every open or waiting task; a missing one is
  filled by `rules.default_bucket` (Today when due today or earlier, else
  Later), including on reopen and in the Twenty importer, which maps To do and
  In progress to Open.
- **Fields**: Body is labelled Notes, Priority bucket is labelled Bucket;
  Priority, Tags, Parent, Waiting on and Source are new, and Priority and
  Source are view fields.
- **Removing a task's last CRM link** is refused through CRM routes, task
  routes and `task_update`, and again by the deferred check.
- The `crm_*` tools are the same tools; only their descriptions name the new
  statuses.

## Three things that are easy to get wrong

### 1. `crm.actor` is set inside the writer, and it has no workspace for a task

As for the CRM, `crm.actor` lasts for one transaction, so it must be set by
the function that writes (`task_apply` does). A task actor carries
`user_id`, `kind` and `channel` and never a `workspace_id`: the trigger takes
the workspace from the row, or for a link from its task. A migration or
script that writes `tasks` by hand must set the system actor in the same
statement batch (`scripts/simulations/tasks_load_5k.py` shows how), or the
write is refused for having no actor.

### 2. Audit rows written before the rename keep the old table names

The audit log is append-only, so rows written before `047` still say
`crm_tasks` and `crm_task_links`. Every reader of task history reads both
names: record timelines (`services/crm/store.OLD_NAMES`), the task panel's
history (`services/tasks/store.task_history`) and the presenters. A new
reader that filters on `target_table = 'tasks'` alone loses every task's
history before 008.

### 3. The TVA audit filters task tables by name, not only by workspace

`007` kept CRM rows out of the TVA audit page and `audit_log` with
`workspace_id IS NULL`. A personal task's rows have no workspace, so that
filter alone would let them through. `services/supabase.list_audit` also
leaves out every `target_table` in `TASK_AUDIT_TABLES`; a new task table
must be added there, and `tests/test_task_audit_exclusion.py` writes a
private task and checks neither reader shows it.

## Running it locally

As the CRM (`crm.md`, "Running it locally"). A person sees My Tasks only once
their level is raised: Admin → People → Tasks, or
`UPDATE profiles SET tasks_level = 1` on the local stack. The real-database
tests run under the shared lock with the reset first (plan §8.1); the
5,000-task timing check is
`uv run python scripts/simulations/tasks_load_5k.py` with the API running.
