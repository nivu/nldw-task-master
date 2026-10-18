# Tasks — Tasks

| | |
|---|---|
| **Feature** | `008-tasks` |
| **Input** | [`spec.md`](spec.md), [`plan.md`](plan.md), [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md) |
| **Shape** | Eleven sequential build slices, the spec's phases in order: phase 1 = S1a–S1e, phase 2 = S2a–S2b, phase 3 = S3a–S3b, phase 4 = S4a–S4b. One engineer-agent builds and tests each slice in one go. Each slice leaves `008-tasks` releasable: every gate green, nothing half-wired. |
| **Branch** | `008-tasks`, main checkout. `009-crm-intelligence` is built at the same time in `../nldw-task-master-009` and rebases onto this branch at the start of each of its slices (plan §9). |

## How to use this file

- Build the slices **in order**. Each lists what it depends on; none depends
  on a later one.
- Task ids `T001`… run across the whole file. Within a slice, tasks are in
  dependency order. **[P]** marks a task that touches no file another
  unfinished task of the same slice touches and needs nothing from one, so it
  can run in parallel. Every task names its files.
- Migrations are numbered in build order, `046`–`055` (008 owns `046`–`059`
  only; `009` owns `060`–`079`). A slice never edits an earlier slice's
  migration once it has been applied anywhere; it adds a new file in
  `056`–`059` or `CREATE OR REPLACE`s in its own file.
- **Each phase's switch is off by default.** The global default
  (`tasks_default_level`) starts at 0 and nobody has a level after a fresh
  `db reset`, so every route of every phase answers 403 until a TVA admin
  raises someone's level (FR-TASK-ROLL-01). CRM task behaviour never depends
  on it (FR-TASK-ROLL-05).
- Tests are named `test_<area>_<rule-in-plain-words>` in classes per spec
  section; each module's docstring cites the FR ids it covers and maps to the
  acceptance criteria (AC) of spec §12.
- `tests/tasks_fake.py` (S1a) implements `app/services/tasks/store.py` over
  the **same** in-memory tables as `tests/crm_fake.py`, with Python fakes of
  `task_apply`, the deferred workspace check and `task_purge_expired`, so
  route tests never touch a database.
- Coordination with 009 (plan §9) is part of every slice: no migration
  outside `046`–`059`; after S1a no 008 migration replaces `crm_audit_row`, `crm_purge`,
  `crm_merge_companies`, `crm_query_rank`, `crm_remove_emails`,
  `crm_touch_updated_at` or `crm_job_latest` (the seven 009 replaces) unless
  the commit announces it (data-model §1);
  `create_task`, `TaskActor` and `TaskCreate` (rule-2 fields and the optional
  fields of contracts §4) never change shape after S1a; in
  `backend/app/mcp/crm.py` only the bodies of existing task-related tools
  change; no 008 test asserts the number of `crm_*` tools
  (`tests/test_crm_mcp.py` is 009's); the CRM Settings page is never touched;
  Today changes stay inside its tasks section; the beat block (first, before
  009's) and `pyproject.toml` lines are 008's own; `server.py`,
  `components/crm/shell.tsx` and the shared 007 files follow plan §9 rule 3a.
- `{id}` path parameters are typed `UUID`, and every fixed `/tasks/...` path
  is registered before `/tasks/{id}` (contracts §1 "Route order"); each slice
  that adds a fixed path adds its route test.

### Gates — every slice's "done when" includes all of these

- [ ] `cd backend && uv run pytest -q` passes (the whole suite)
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` pass
- [ ] Under the shared database lock (plan §8.1), from the repository root,
  run **in one shell** (one tool call; never line by line):
  ```bash
  bash -c '
  LOCK="$(git rev-parse --git-common-dir)/db-test.lock"
  until mkdir "$LOCK" 2>/dev/null; do sleep 5; done
  trap "rmdir \"$LOCK\"" EXIT
  npx supabase db reset && (cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q)
  '
  ```
  The reset applies this branch's migrations from empty without error, and
  only then do every db test (007's included) run; the lock is released when
  that shell exits, on success and on failure.
- [ ] `cd frontend && pnpm build && pnpm lint` pass
- [ ] Off by default: a test shows a person with no level of their own, under the default of 0, gets `tasks.level = 0` from `/me` and 403 from every route the slice adds (from S1b on)
- [ ] `git diff --cached --name-only` does not contain `supabase/config.toml` (its local `enable_signup=true` is never committed)
- [ ] No file outside the slice's task list changed, except test fakes whose signatures the slice changed
- [ ] Commit message says what changed, why, and which journey it serves; ends with the attribution line

---

## S0 — Commit both plans (before S1a and before 009's T001)

**Owner.** The 008 session, with the owner's approval (or the owner).

- [ ] T000 On `008-tasks` in the main checkout, commit `specs/008-tasks/` and `specs/009-crm-intelligence/` and nothing else: `git add specs/008-tasks specs/009-crm-intelligence`, then check `git diff --cached --name-only` lists only those two directories (never `supabase/config.toml`). After this commit the 009 worktree may be branched (plan §9 rule 4).

**Owner decision recorded before S1a** (not asked for by the spec; approved
by the owner on 18 October 2026, the recommended option): `task.priority` and
`task.source` as CRM view fields (data-model §3.3).

---

## S1a — The shared table, the write path, visibility and `create_task`

**Goal.** `crm_tasks` and `crm_task_links` are `tasks` and `task_links`, with
the new statuses, fields, links to TVA projects and rows with no workspace.
Every CRM task behaviour of `007` works on them, changed only as spec §8 says.
The task write path, the visibility function, the phase level and the 009
interface `create_task` exist and are tested. No task route or page yet.

**Depends on.** Nothing (`007` in production).

**Spec.** FR-TASK-DATA-01 to -15; FR-TASK-VIS-01 (function), -02, -03, -05
(TVA audit half), -07 (CRM presenter); FR-TASK-ROLL-01, -02, -04, -05;
FR-TASK-CHG-01, -04, -05, -07, -09, -10, -11; FR-TASK-MCP-04; spec §8.2;
NFR-TASK-03, -04; §14 (interface).

**Switch.** Level column and default (`0`) arrive here; no route reads them yet.

**Migrations.** `046_tasks_linkless_cleanup.sql`, `047_tasks_shared_table.sql`,
`048_tasks_crm_functions.sql`, `049_task_write_path.sql`,
`050_task_rollout.sql` (data-model §1–§5).

### Database

- [ ] T001 Write `supabase/migrations/046_tasks_linkless_cleanup.sql`: soft-delete live `crm_tasks` with no `crm_task_links` row into their workspace Trash under the system actor (`crm.action = 'task.deleted'`), `RAISE NOTICE` the count (data-model §1; spec §8.2)
- [ ] T002 Write `supabase/migrations/047_tasks_shared_table.sql`: renames; status CHECK and data mapping (TP-04), filling the bucket of **every** open or waiting row, deleted ones included, before `tasks_bucket_when_open` is added; new columns, `task_tags_ok()` (count and length only, TP-14); nullable `workspace_id` on both tables; `task_links.project_id`, `tva_project`, kind CHECK, plain `task_id` FK, one-project index; `tasks_workspace_check` (deferred), `tasks_workspace_fixed`, `tasks_parent_check`; indexes; select-list change in the order of data-model §3.1 (relax `crm_select_options_keyed_active`, clear and deactivate `todo`/`in_progress`, handle label collisions, then insert with `ON CONFLICT (workspace_id, list_key, system_key) WHERE system_key IS NOT NULL DO NOTHING`); saved-view rewrite; field registry rows (owner decision of 18 October 2026, data-model §3.3; data-model §2.1, §2.2, §3, §4, §5.1)
- [ ] T003 Write `supabase/migrations/048_tasks_crm_functions.sql`: `DROP FUNCTION crm_apply(jsonb, jsonb)` and recreate with `p_task_needs_link boolean DEFAULT true`; `CREATE OR REPLACE` `crm_check_refs`, `crm_deal_transition` (Lost → `cancelled`), `crm_purge` (purge linkless tasks), `crm_merge_companies`, `crm_search`, `crm_query`, `crm_query_rank` (the full shared status array with the campaign-member keys), `crm_query_rank_type`, `crm_flag_facts`, `crm_set_flags`, `crm_audit_row` (task workspace for a link row; no-actor exemptions for `waiting_on_id`/`assigned_by` SET NULL and `inbox_items` CASCADE), each copied from the migration data-model §5.4 names and changed only as listed; revoke `EXECUTE` from `public`, `anon`, `authenticated`
- [ ] T004 Write `supabase/migrations/049_task_write_path.sql`: `task_apply()` with its per-op right re-checks and `task_purge_expired()`; grants (data-model §5.2, §5.3)
- [ ] T005 Write `supabase/migrations/050_task_rollout.sql`: `profiles.tasks_level`, the `tasks_default_level` setting row (data-model §2.4)

### Backend — create

- [ ] T006 [P] Create `backend/app/domain/tasks/__init__.py`, `backend/app/domain/tasks/errors.py` (`TaskRefused(CrmRefused)`), `backend/app/domain/tasks/labels.py` (fixed status, bucket, priority, source labels; FR-TASK-CHG-11)
- [ ] T007 [P] Create `backend/app/domain/tasks/rules.py`: `default_bucket(due_on, today)`, `effective_bucket(task, today)`, `days_overdue(task, today)` (the `007` Task overdue rule), `status_values(new, was, now)`, `normalise_tags(tags)` (TP-14: trim, drop exact repeats, keep case), `kind(task, links)`, `sort_key`, `missing_sentence(fields)` (FR-TASK-DATA-02, -03, -08, FR-TASK-LIST-02, -03)
- [ ] T008 [P] Create `backend/app/domain/tasks/visibility.py`: `Viewer`, `TaskFacts`, `can_see_project`, `sight`, `may_delete`, `may_restore`, `project_field`, `parent_field`, `audience`, `history_visible` (plan §5.4; spec §3)
- [ ] T009 [P] Create `backend/app/domain/tasks/rollout.py`: `level_for(profile, default)`, `refusal(level, needed)` ("Tasks are not switched on for you yet."), `assignee_refusal(name, level)` (FR-TASK-ROLL-01, -03, -04)
- [ ] T010 [P] Create `backend/app/schemas/tasks.py`: `TaskLinkIn`, `TaskCreate` exactly as contracts §4 — the rule-2 fields and the optional `tags`, `parent_id`, `status`, `waiting_on_id`, `waiting_on_text`, `category_id`, `completed_at` (plan §9 rule 2)
- [ ] T011 Create `backend/app/services/tasks/__init__.py` and `backend/app/services/tasks/store.py`: reads of `tasks`, `task_links`, profiles and allocations needed by the task service; `rpc()` reusing `services/crm/store.py`'s error mapping; `task_apply` and `task_purge_expired` wrappers
- [ ] T012 Create `backend/app/services/tasks/viewer.py`: `load(user) -> Viewer` (active memberships, TVA role, every allocation's project id, effective level) in at most three reads
- [ ] T013 Create `backend/app/services/tasks/present.py`: `TaskOut` (contracts §0.1) through `visibility.project_field` / `parent_field` / `audience`; labels from the workspace lists for CRM tasks, else `labels.py`
- [ ] T014 Create `backend/app/domain/tasks/service.py`: `TaskActor(user, kind, channel, suggestion_id=None)` (with `as_rpc()` giving `user_id`, `kind`, `channel`, and `suggestion_id` only when set; no `workspace_id`), `create_task(actor, payload)` implementing contracts §4 steps 1–8 (status and `completed_at` rules, repeated links refused) (FR-TASK-DATA-05 to -09, -13, -15; FR-TASK-ROLL-04; CT-2)
- [ ] T015 Create `backend/app/tasks/task_jobs.py` with `tasks.purge_deleted` (calls `task_purge_expired` with `now − 90 days`, system actor; returns a count) and import it in `backend/app/tasks/__init__.py`; append the `# Spec 008 — tasks` block with `celery_app.conf.beat_schedule.update({"tasks-purge-deleted": … crontab(hour=1, minute=20)})` at the end of `backend/app/worker.py`, after 007's code; 009's block always goes below it (TP-12; plan §5.8)

### Backend — modify (spec §8.2; plan §5.5)

- [ ] T016 Modify `backend/app/services/crm/store.py`: table names, `LINK_TABLES`, the 23505 map key `task_links_target`; `tasks_for_today` (open or waiting, Today bucket or due ≤ today; FR-TASK-CHG-05), `tasks_due_by`, open-task lists and `flag_rows` read `status IN (open, waiting)`; `tasks_completed_between` unchanged in meaning; `crm_apply` calls pass `p_task_needs_link` where a CRM path creates a task; every task insert it builds carries a bucket (`rules.default_bucket` when missing)
- [ ] T017 [P] Modify `backend/app/services/crm/records.py`: default status `open`; `rules.default_bucket` fills a missing bucket on create and on every update or reopen that leaves the task open or waiting (the new CHECK); `_status` for every status; old keys read as `open` (TP-05); refuse removing the last CRM link (422, FR-TASK-DATA-05); record task tab sorts open and waiting first; PATCH link replacement keeps a `tva_project` link; tasks restore refuses a task with no CRM link
- [ ] T018 [P] Modify `backend/app/schemas/crm.py` (`TaskFields.status` accepts `open|waiting|done|cancelled|todo|in_progress`; `priority`, `tags`, `parent_id`, `waiting_on_id`, `waiting_on_text`) and `backend/app/api/crm/records.py` (`TaskStatus`; `status=open` means open or waiting) (FR-TASK-CHG-01, -10)
- [ ] T019 [P] Modify `backend/app/services/crm/present.py`: task keys and labels, Notes, priority, tags, waiting on, project link via `visibility.project_field`; timeline reads `crm_tasks`/`tasks` and `crm_task_links`/`task_links`; and `backend/app/api/crm/audit.py`: the workspace audit leaves out a `tva_project` link row the Admin cannot see and a private `parent_id` (FR-TASK-CHG-07, FR-TASK-VIS-07, FR-TASK-DATA-09)
- [ ] T020 [P] Modify `backend/app/domain/crm/today.py` and `backend/app/services/crm/today.py`, the tasks section and the loose-ends open-task test only (FR-TASK-CHG-05; plan §9 rule 3)
- [ ] T021 [P] Modify `backend/app/domain/crm/flags.py` and `backend/app/services/crm/flags.py`: open = open or waiting; table name `tasks`
- [ ] T022 [P] Modify `backend/app/domain/crm/importer.py` (`TASK_STATUS`: To do, In progress → `open`; FR-TASK-CHG-09; the task values it builds, today at lines ~1316–1328 with no `bucket`, gain `bucket = rules.default_bucket(due_on, today)`) and `backend/app/services/crm/importer.py` (table names)
- [ ] T023 [P] Modify table names and open-task tests in `backend/app/services/crm/deals.py`, `backend/app/services/crm/merge.py`, `backend/app/services/crm/search.py`, `backend/app/domain/crm/digests.py`, `backend/app/domain/crm/objects.py` (`task.priority`, `task.source` fields, data-model §3.3), `backend/app/domain/crm/settings.py` (`task.bucket` list labelled "Bucket", FR-TASK-CHG-01), `backend/app/services/crm/views.py` (`_FIXED_LISTS`/`_FIXED_LABELS`: priority and source labels for those fields, same condition), `backend/app/services/crm/export.py`, `backend/app/services/crm/dashboard.py` (Activity counts `done` only, workspace-scoped)
- [ ] T024 [P] Modify docstrings only of `crm_add_task`, `crm_complete_task`, `crm_create`, `crm_update`, `crm_list` in `backend/app/mcp/crm.py` to name the statuses `open, waiting, done, cancelled` (plan §9 rule 3)
- [ ] T025 [P] Modify `backend/app/services/supabase.py` (`list_audit` leaves out the task tables) and `backend/app/api/admin.py` (`read_audit` unchanged in shape) (FR-TASK-VIS-05, FR-TASK-CHG-07)
- [ ] T026 Modify `backend/app/schemas/__init__.py` (`UserUpdate.tasks_level: int | Literal["default"] | None`, 0–4), `backend/app/api/admin.py` (`update_user` writes and audits `user.tasks_level`, allowed on `crm_only`; `list_users` returns it (own level or null; no effective level); `update_setting` validates `tasks_default_level`), `backend/app/api/me.py` (`tasks: {level}` in both shapes) (FR-TASK-ROLL-01, -02)

### Frontend — modify

- [ ] T027 [P] Modify `frontend/lib/api/crm-types.ts`, `frontend/components/crm/task-dialog.tsx` (statuses, reopen → Open, Notes and Bucket labels, priority), `frontend/components/crm/today-list.tsx`, `frontend/app/(crm)/crm/[ws]/tasks/page.tsx`, `frontend/lib/crm/view-fields.ts` (FR-TASK-CHG-01, -10, -11)
- [ ] T028 [P] Modify `frontend/lib/api/types.ts`: `Me.tasks: {level: number}`; `PortalUser.tasks_level: number | null` (the admin people type)

### Tests

- [ ] T029 Modify `backend/tests/crm_fake.py` (tables `tasks`, `task_links`; fakes of `crm_apply`'s new parameter, the Lost → cancelled transition, linkless purge, the deferred check) and create `backend/tests/tasks_fake.py` over the same tables
- [ ] T030 Update the CRM tests that pin statuses or table names, only where spec §8 changes behaviour: `backend/tests/test_crm_tasks_notes.py`, `test_crm_today.py`, `test_crm_flags.py`, `test_crm_automations.py`, `test_crm_deals_routes.py`, `test_crm_tva_lifecycle.py`, `test_crm_import_routes.py`, `test_crm_importer_mapping.py`, `test_crm_merge.py`, `test_crm_views_routes.py`, `test_crm_settings_lists.py`, `test_crm_object_fields_registry.py`, `test_crm_digests.py`, `test_crm_dashboard.py`, `test_crm_timeline.py`, `test_crm_accounts.py` (the `/me` `crm_only` shape gains `tasks: {level}`, T026)
- [ ] T031 [P] Create `backend/tests/test_task_rules.py` (bucket default, effective bucket with no rewrite, days overdue, completed at, tags limits (count and length only; case kept; exact repeats dropped), kinds; fixed clock; and a caller in a non-IST zone (e.g. `America/New_York` and `Pacific/Auckland`) near local midnight, so "today", the current week and days overdue follow `profiles.timezone`, FR-TASK-CHG-06)
- [ ] T032 [P] Create `backend/tests/test_task_visibility.py` (every row of spec §3.1 and §3.3; FR-TASK-VIS-07 project field; parent shown as private; history era rule, including a project link added to a Labs task, which is not private-era)
- [ ] T033 [P] Create `backend/tests/test_task_create_interface.py` (signature, `TaskActor` fields and `TaskCreate` fields/defaults exactly as contracts §4, the rule-2 fields and the optional ones; a call with only the rule-2 fields works; workspace derivation; each refusal sentence, including repeated links, `done`/`cancelled` refused for a non-import actor, `completed_at` refused outside an import Done row; audit row with actor, kind, channel; `suggestion_id` reaches `crm.actor` when set and is absent when not; the actor's own level not checked; `TaskOut` returned)
- [ ] T034 [P] Create `backend/tests/test_task_rollout.py` (level arithmetic; `update_user` audit; the `update_user` MCP tool carries `tasks_level` in `changes` to `PATCH /admin/users/{id}` (FR-TASK-ROLL-02); `update_setting` validation; `/me` shape for staff and `crm_only`)
- [ ] T035 [P] Create `backend/tests/test_task_crm_changes.py` (Lost → Cancelled and not in Activity; pre-migration Done stays; `status=open` returns open and waiting; old keys accepted through `crm_create`, `crm_update`, `crm_list` and `GET /crm/tasks`; Today section 3; cancelled is closed in Today, flags, reminders, views; last CRM link refused through CRM routes; a CRM task created or reopened through CRM routes with no bucket gets the default bucket; restoring a deleted CRM task with no CRM link left is refused 422 (FR-TASK-DATA-05); a private task due today is absent from the CRM daily digest and the reminder queries (automation 7; FR-TASK-VIS-04); a CRM task assigned to someone else still makes the CRM bell notice (FR-TASK-DEL-01); 008 registers no new `crm_*` tool and `crm_add_task`, `crm_complete_task`, `crm_create`, `crm_update`, `crm_list` are still registered under those names — no assertion of the total number of `crm_*` tools, which `test_crm_mcp.py` (009's) owns)
- [ ] T036 [P] Create `backend/tests/test_task_audit_exclusion.py` (a task with no workspace written, then `GET /admin/audit` and the `audit_log` tool show none of it) and `backend/tests/test_task_jobs.py` (`tasks.purge_deleted` calls `task_purge_expired` with now − 90 days and the system actor at a fixed clock; `"tasks-purge-deleted"` is in `celery_app.conf.beat_schedule` at 01:20) (FR-TASK-DATA-11)
- [ ] T037 Create `backend/tests/db/test_task_migration_db.py`: class `TestAfterMigration` (always: status list and the relaxed CHECK; saved views free of `todo`/`in_progress`/`is_not done`; no `pg_proc` body names `crm_task` — this one assertion also guards 009's later function copies, since it runs at every 009 db gate and 009 adds no second copy; each replaced function called once) and class `TestMapping` (only with `TASKS_MIGRATION_CHECK=1`, run as plan §8.1's one-invocation form under the db lock: `npx supabase db reset --version 045`, seed To do / In progress / Done / linkless / bucketless (live and already deleted) / Twenty tasks with links and history through `crm_apply`, rename the Done option's label (an Admin rename) and rename To do to "Open" in one workspace, `npx supabase migration up`, then assert ids, links and history kept, the mapping and the `in progress` tag, linkless → Trash, every open or waiting row (deleted ones included) has a bucket, sources, the renamed Done label kept, the To do and In progress labels not carried to Open (the colliding one suffixed " (old)"), exactly one default per status list; finish with a plain `npx supabase db reset`); `backend/tests/db/test_task_write_path_db.py` (`task_apply` right re-checks, CRM09, audit with actor and null workspace, unaudited write refused, `task_purge_expired`; `crm_apply` with `p_task_needs_link = true` refuses a task insert with no CRM link; `tasks_parent_check` refuses a parent that has a parent and a parent for a task with children; a `tva_project` link on a Labs task is audited with the task's workspace; deleting a profile referenced by `waiting_on_id` succeeds with a system audit row), `backend/tests/db/test_task_links_db.py` (deferred check at commit both ways; workspace only NULL → value; two workspaces refused; Academy + project refused; cascade on delete including `tva_project` rows and no-workspace tasks; project FK; RLS on and no grants; `EXECUTE` denied; the audit trigger on insert, update and delete of `tasks` and `task_links`, `crm_check_refs` on `task_links`, and the composite `(workspace_id, task_id)` foreign key kept)
- [ ] T038 Update `backend/tests/db/test_crm_deal_transition_db.py`, `test_crm_flags_db.py`, `test_crm_import_db.py`, `test_crm_merge_db.py`, `test_crm_query_db.py`, `test_crm_security_db.py`, `test_crm_audit_db.py`, `test_crm_seed_db.py` and `test_crm_records_db.py` for the table names and Lost → Cancelled. Specifically: the task fixtures that insert with no bucket (`test_crm_import_db.py:128`, `test_crm_flags_db.py:185`, `test_crm_merge_db.py:176`, `test_crm_deal_transition_db.py:167`) gain a bucket; `test_crm_import_db.py` and `test_crm_records_db.py` assert a CRM-created, reopened or imported task gets the default bucket; `test_crm_seed_db.py` expects `task.status` = Open, Waiting, Done, Cancelled (active) and its fixed-list check the new rows; `test_crm_query_db.py` pins sorting campaign members by status (the shared rank array); `test_crm_security_db.py` (audit trigger on every table, composite foreign keys, `crm_check_refs` coverage) and `test_crm_audit_db.py` (RLS and no browser grant) widen their `LIKE 'crm\_%'` table queries to also include `tasks`, `task_links` and every new task table; add to `test_crm_records_db.py` that purging a record purges tasks left with no CRM link (FR-TASK-DATA-14)

**Done when.**
- [ ] All gates, plus `TASKS_MIGRATION_CHECK=1` run once under the db lock (plan §8.1 one-invocation form), its output pasted in the commit message
- [ ] AC phase 1 "migration and CRM", every line, has a passing test named in its module docstring
- [ ] `create_task`, `TaskActor` and `TaskCreate` match contracts §4 character for character (the interface test pins them); migrations 046–050 are in this one commit; the commit message says 009 may now reference the interface and rebase onto 048

---

## S1b — Task routes, `task_*` tools and the privacy sweep

**Goal.** `/api/v1/tasks` serves create, read, list, change, complete, reopen,
link, unlink, delete and restore for staff and `crm_only` accounts, behind the
phase-1 guard; four `task_*` tools reach them; a generated test proves no one
else can reach a private task through any route or tool.

**Depends on.** S1a.

**Spec.** FR-TASK-LIST-01 to -05, -07 (backend); FR-TASK-MCP-01 to -05
(phase 1); FR-TASK-VIS-01 to -08; FR-TASK-ROLL-03; FR-TASK-CHG-02, -03;
FR-TASK-DATA-06, -09, -11, -13.

**Switch.** Every route depends on `TasksDep` (level ≥ 1).

**Migrations.** `056_task_link_removal_lock.sql` (data-model §5.1; added in
S1b, see T040).

### Backend

- [x] T039 Create `backend/app/api/tasks/__init__.py` (`ROUTERS`) and `backend/app/api/tasks/deps.py` (`tasks_dep(n)`, `TasksDep`, `TasksDep2`, `TasksDep3`, `TasksDep4`; `TaskActor` from the request)
- [x] T040 Extend `backend/app/domain/tasks/service.py` with `update_task`, `complete_task`, `reopen_task`, `add_link`, `remove_link`, `delete_task`, `restore_task` (every rule of spec §3.3, FR-TASK-DATA-05, -06, -09, -11, -13; TP-02 read-only); also `backend/app/services/tasks/store.py` (added in S1b because the routes need reads S1a did not have: `assigned_tasks`, `linked_task_ids`, `subtasks`, `task_history`, `deleted_personal_tasks`, `active_profiles`, `project_people`, `projects_named`) and `backend/tests/tasks_fake.py` (fakes of those reads, and the shared `TaskWorld` cast the S1b route, tool and privacy tests use); also `supabase/migrations/056_task_link_removal_lock.sql` (added in S1b because `remove_link` exposed a race in 047's `tasks_workspace_check`: two removals of different CRM links of one task, at once, each counted the other's link as still there and both committed, leaving a CRM task with no CRM link (FR-TASK-DATA-05); a link removal now locks its task row first) and `backend/app/services/tasks/present.py` (S1a's T013, changed in S1b because the routes now show subtask counts to viewers other than the owner: a count holds only the subtasks this viewer may see, each checked on its own, FR-TASK-VIS-04, -DATA-09)
- [x] T041 [P] Create `backend/app/services/tasks/listing.py` (My Tasks sections, closed page, filters tag/priority/link/q; viewer filter; one query plan per contracts §1) and `backend/app/services/tasks/people.py` (assignable people, linkable records and projects)
- [x] T042 Create `backend/app/api/tasks/tasks.py`: every phase-1 route of contracts §1 except quick-add preview and import (`GET/POST /tasks`, `/tasks/audience`, `/tasks/people`, `/tasks/linkable`, `GET/PATCH/DELETE /tasks/{id}`, `/complete`, `/reopen`, `/links`, `/restore`, `/trash`); `assignee` name resolution. Fixed paths are declared before `/tasks/{id}`; `{id}` and `{link_id}` are typed `UUID` (contracts §1 "Route order"); also `backend/app/schemas/tasks.py` (added in S1b for the request models of contracts §1.1, `TaskIn` and `TaskPatch`, and the `POST /tasks/audience` body, `TaskAudienceIn`; `TaskLinkIn` and `TaskCreate` unchanged)
- [x] T043 Modify `backend/app/main.py`: mount `app.api.tasks.ROUTERS` under `/api/v1`, in `ROUTERS` order (the `tasks.py` router always last)
- [x] T044 Create `backend/app/mcp/tasks.py` with `task_add`, `task_list`, `task_update`, `task_complete` (contracts §5); modify `backend/app/mcp/server.py`: one line `from app.mcp import tasks as _tasks  # noqa: E402, F401` after the CRM import, and the tasks paragraph in `INSTRUCTIONS` (FR-TASK-MCP-05; plan §9 rule 3a (a): 009 may append one paragraph after it)

### Tests

- [x] T045 Modify `backend/tests/test_crm_only_refused.py`: `SHARED_PREFIXES = ("/api/v1/tasks",)`; every route under it depends on a `TasksDep*` (FR-TASK-CHG-02)
- [x] T046 Modify `backend/tests/test_mcp.py`: `test_every_operation_has_a_tool` also leaves out `task_*`/`inbox_add` tools and `/api/v1/tasks` operations (FR-TASK-CHG-03)
- [x] T047 [P] Create `backend/tests/test_task_routes.py` (403 at level 0 on every route, CRM tasks still fine for that person; create/list/change/complete/reopen/delete/restore; create with tags, parent and Waiting through `POST /tasks`; restore by creator not assignee; restoring a task with no CRM link left 422; 404 for unseen; read-only sight 403 on change; Viewer assignee status only through routes; tags over 40 / eleventh tag refused; `crm_only` assignment rules; parent: a parent that has its own parent refused, a parent the caller cannot see 404, completing a parent leaves its children open (FR-TASK-DATA-09); one test per fixed path (`/tasks/trash`, `/tasks/people`, `/tasks/linkable`, `/tasks/audience`) reaching its own handler, not `/tasks/{id}`) and `backend/tests/test_task_listing_routes.py` (FR-TASK-LIST-01 order: due date with none last, then priority, then title; done and cancelled in the closed page, newest first, paged by `closed_limit`/`closed_before`; FR-TASK-LIST-07 each filter: tag, priority, link, text; FR-TASK-LIST-04 moving the bucket never changes `due_on`; sections for a caller in a non-IST zone); also `backend/tests/test_task_store_queries.py` (added in S1b because the fake store cannot show the PostgREST request itself: a link filter's ids go in batches, so a long URL never fails with 414, and a tag travels as one quoted array element) and `backend/tests/db/test_task_listing_db.py` (added in S1b because only the real database proves the closed page's `<updated_at>,<id>` cursor of contracts §1 skips none of the tasks one transaction closed together, also beside a text filter, and that a tag with a comma or quote is one tag)
- [x] T048 [P] Create `backend/tests/test_task_links_routes.py` (first CRM link sets workspace; last CRM link refused through task routes and `task_update`; Viewer or TVA user in no workspace cannot add a CRM link; Academy + project 422; unseen project 404; archived 422; a link to a project archived after linking stays (FR-TASK-DATA-06); second project 422; repeated link 422; TVA `user` links a project they were allocated to); also `backend/tests/db/test_task_links_db.py` (S1a's T037, extended in S1b with the 056 test: two removals at once cannot take a task's last CRM link)
- [x] T049 [P] Create `backend/tests/test_task_mcp.py` (exactly the four tools; caller lines; CONFIRM FIRST; one `_api` call each; `INSTRUCTIONS` contains the tasks paragraph (FR-TASK-MCP-05); `crm_only` through tools, FR-TASK-MCP-03; Viewer status-only through `task_update`)
- [x] T050 [P] Create `backend/tests/test_task_privacy_sweep.py`: routes from `app.routes`, tools from the MCP registry. Scope (so 009's new routes and tools never need an edit here): a fixture is required for every route under `/api/v1/tasks`, every `task_*`/`inbox_add` tool, and every entry of an explicit list in the test of existing CRM, TVA and admin routes and `crm_*` tools that return, count, search, export or audit-read a task (e.g. `GET /crm/tasks`, `GET /crm/{object}/{id}`, timelines, Today, search, export, `POST /crm/query`, `GET /admin/audit`, `crm_list`, `crm_get`, `crm_today`, `crm_search`, `audit_log`); any other `/api/v1/crm` route or `crm_*` tool is left to the CRM isolation sweep and listed in the test output, not failed. A private and a shared personal task are tried by the **ten** account kinds of FR-TASK-VIS-06: TVA admin, manager, the owner, the creator's TVA lead, CRM workspace Admin, Viewer, `crm_only`, former assignee (after reassignment and after hand-back), Waiting-on person, removed workspace member; marker strings never in any answer. A committed self-test registers a dummy `/api/v1/tasks` route and a dummy `task_` tool on copies of the registries and asserts the fixture check fails (FR-TASK-VIS-06; AC "Phase 1 — privacy" and "fails when a route or tool is added without a fixture")
- [x] T051 [P] Create `backend/tests/test_task_logging.py`: captured logs of a run writing tasks contain no title or note (NFR-TASK-04)

**Done when.**
- [ ] All gates
- [ ] The privacy sweep's committed self-test proves it fails when a task route or tool is added without a fixture
- [ ] By hand with a personal token at level 1: Journey 4.6 (`task_list`, `task_add`, `task_complete`) through Claude

---

## S1c — My Tasks, the task panel and quick add

**Goal.** One My Tasks page in both shells, a side panel, a quick-add box in
every header that previews and asks before saving, and the Admin People phase
control.

**Depends on.** S1b.

**Spec.** FR-TASK-LIST-01 to -08; FR-TASK-ADD-01 to -04, -06; FR-TASK-VIS-08;
FR-TASK-ROLL-02 (UI), -03 (hiding); NFR-TASK-05, -06.

**Switch.** The box, the nav links and the page show only when
`me.tasks.level >= 1`.

**Migrations.** None.

### Backend

- [x] T052 Create `backend/app/domain/tasks/quick_add.py`: `parse(text, today, people, zone) -> Parsed` with every rule and question of contracts §1.2 (T7); also `specs/008-tasks/contracts/api.md` (changed in S1c because §1.2's `D/M` row did not say how its question meets FR-TASK-ADD-04's passed-date rule: a reading already passed this year is offered as next year's, so the "5 Nov or 11 May?" question never offers a passed date)
- [x] T053 Modify `backend/app/api/tasks/tasks.py`: `POST /tasks/quick-add/preview` (contracts §1); also `backend/app/schemas/tasks.py` (changed in S1c for the preview's request body of contracts §1, `QuickAddIn` with its `answers`, `QuickAddAnswers`; the S1b models unchanged); also `backend/tests/test_task_privacy_sweep.py` (changed in S1c because T050's sweep requires a fixture for every route under `/api/v1/tasks`, so the new preview route needs one) and `backend/tests/test_task_routes.py` (changed in S1c because T047's `test_the_slice_adds_routes` pins the number of task routes, which the preview route takes from 14 to 15; every route still gets the level-0 403 test)

### Frontend

- [x] T054 [P] Create `frontend/lib/api/task-types.ts` (mirrors `schemas/tasks.py` and `TaskOut`), `frontend/lib/api/tasks.ts` (typed `call<T>()` wrappers to `/api/v1/tasks…`), `frontend/lib/tasks/labels.ts`
- [x] T055 Create `frontend/components/tasks/task-row.tsx`, `frontend/components/tasks/task-sections.tsx` (four sections, drag or tap to move a bucket with `@dnd-kit`, overdue text, collapsed done list with undo), `frontend/components/tasks/task-filters.tsx` (tag, priority, link, text)
- [x] T056 Create `frontend/components/tasks/task-panel.tsx` (every field, links that open what the viewer may see, parent and subtasks, history) with `frontend/components/tasks/visible-to.tsx`, `frontend/components/tasks/link-picker.tsx`, `frontend/components/tasks/person-picker.tsx` (FR-TASK-LIST-06, FR-TASK-VIS-08)
- [x] T057 Create `frontend/components/tasks/quick-add.tsx`: debounced preview, questions with choices, Save disabled while a question is open, Enter saves, shortcut `q`, a `tasks:created` window event (FR-TASK-ADD-02, -06; NFR-TASK-06)
- [x] T058 Create `frontend/components/tasks/my-tasks.tsx` (the page: sections, filters, panel, optimistic insert on `tasks:created`), `frontend/app/(portal)/tasks/page.tsx` and `frontend/app/(crm)/crm/[ws]/my-tasks/page.tsx` mounting it (FR-TASK-LIST-08)
- [x] T059 Modify `frontend/app/(portal)/layout.tsx` (My Tasks nav link; quick-add box in the desktop sidebar header and the phone header) and `frontend/components/crm/shell.tsx` (My Tasks link; quick-add box in its header; 008 owns that header and its nav entry, 009 appends only its Review entry after it, plan §9 rule 3a (b)), both hidden below level 1 (FR-TASK-ADD-01)
- [x] T060 [P] Modify `frontend/lib/supabase/middleware.ts`: `/tasks` in `PROTECTED_ROUTES`
- [x] T061 [P] Modify `frontend/app/(portal)/admin/page.tsx`: a "Tasks" level control per person (Default, 0–4) in People, `crm_only` rows included (FR-TASK-ROLL-02); also `frontend/lib/api/portal.ts` (changed in S1c because the control saves through `updateUser`, whose `changes` type had no `tasks_level`; it gains `tasks_level: number | "default"`, the field S1a's admin route already accepts)

### Tests

- [x] T062 [P] Create `backend/tests/test_task_quick_add.py` at the fixed clock Tue 20 Oct 2026 IST: every line of AC "Quick add at the fixed clock"; `@name` none/several asks; tag and title stripping; `next week`; `next fri` always asks; a past `YYYY-MM-DD` (e.g. `2026-10-02`) and a past `D Mon` ask; plus the same text for a caller in a non-IST zone near midnight, where "today" and the weekday follow that zone (FR-TASK-CHG-06)
- [x] T063 [P] Create `backend/tests/test_task_preview_routes.py` (preview writes nothing; `visible_to` sentences for each kind; questions block saving by contract; `/tasks/quick-add/preview` reaches its own handler, not `/tasks/{id}`)
- [x] T064 [P] Create `frontend/e2e/tasks.spec.ts` (local): Journey 4.2 at a mocked clock; a `crm_only` seller opens My Tasks from the CRM and adds a private reminder (Journey 4.5); keyboard-only quick add with a visible focus ring on each control; overdue and priority shown in words, not colour alone (NFR-TASK-06). And `backend/tests/test_task_ui_copy.py`: the words "Employee" and "compliance" do not appear in `frontend/components/tasks/**` or the task pages (NFR-TASK-05)

**Done when.**
- [ ] All gates
- [ ] Journeys 4.2, 4.4 and 4.5 work by hand in both shells at level 1; nothing of tasks shows at level 0

---

## S1d — Life OS import

**Goal.** The owner uploads the Life OS CSV, reviews a dry run with an owner
mapping he can edit (status and priority use the fixed mapping; an unmapped
value is fixed in the sheet and the file re-exported, Journey 4.1), and runs
it once; a second run changes nothing.

**Depends on.** S1c.

**Spec.** FR-TASK-IMP-01 to -08; TP-10, TP-17, TP-19; Journey 4.1.

**Switch.** Level ≥ 1 and `profiles.is_owner`.

**Migrations.** `051_task_import_runs.sql` (data-model §2.3).

- [x] T065 Write `supabase/migrations/051_task_import_runs.sql`
- [x] T066 [P] Create `backend/app/domain/tasks/lifeos.py`: column map (FR-TASK-IMP-02), date reading with the ambiguous `D/M/YYYY` list, owner proposal (full name, else unique first name), the fixed status and priority mapping (not editable), content key `lifeos:` + hash, row report, Waiting → "Life OS", done without date → import time (FR-TASK-IMP-02 to -05, -07)
- [x] T067 Create `backend/app/services/tasks/lifeos.py`: dry run (writes only its run record), real run (hash, 24 h, mapping complete, mapped owners at level ≥ 1, idempotent by key; creates through `create_task` with `TaskActor(kind="import")`, `source = import`, and the `TaskCreate` fields `status` (open, waiting, done, cancelled), `tags`, `completed_at`, `waiting_on_text = "Life OS"`; no notice) (FR-TASK-IMP-02, -04 to -07, -08); also `backend/app/services/tasks/store.py` (added in S1d because the run needs two reads S1a–S1c did not have: `existing_import_keys`, which of the file's Life OS keys a task already carries, in the Trash or not (FR-TASK-IMP-05), and `get_import_run`, the 051 run record the real run checks) and `backend/tests/test_task_store_queries.py` (changed in S1d because the fake store cannot show the PostgREST request itself: `existing_import_keys` reads the file's 71-character Life OS keys `_KEY_BATCH` (40) per request, since 100 of them make a URL that fails with 414)
- [x] T068 Create `backend/app/api/tasks/lifeos.py` (`/tasks/import/dry-run`, `/tasks/import/{run_id}/apply` with `run_id: UUID`; owner only; 4 MB; no `GET /tasks/import`, which the spec does not ask for) and add it to `backend/app/api/tasks/__init__.py` `ROUTERS` before the `tasks.py` router; also `backend/app/schemas/tasks.py` (changed in S1d for the dry run's `mapping` form field of contracts §1, `LifeOsMapping`, which plan §5.2 places there: owner edits only, `extra='forbid'` so status or priority keys are refused (FR-TASK-IMP-04); the earlier models unchanged)
- [x] T069 [P] Create `frontend/components/tasks/lifeos-import.tsx` (upload, counts, editable owner mapping only, unmapped status/priority values listed as blocking with "fix it in the sheet and export again", the real run with the same file) and `frontend/app/(portal)/tasks/import/page.tsx` (owner only); also `frontend/lib/api/tasks.ts` and `frontend/lib/api/task-types.ts` (the two upload calls and their response types, where every other task call lives)
- [x] T070 [P] Create `backend/tests/fixtures/lifeos_tasks.csv` (known counts, a "Vinitha" owner, two unmappable statuses, an ambiguous date, a repeated row, a waiting row) and `backend/tests/test_task_lifeos_mapping.py` (pure)
- [x] T071 [P] Create `backend/tests/test_task_lifeos_routes.py`: AC lines on import (counts, blocking, 24 h, re-run changes nothing, edited file creates only new rows, other file refused, uploader creates and owner assigned, owner-only, no tool, run record has no content; a file over 4 MB answers 413 (FR-TASK-IMP-01); a dry-run `mapping` with status or priority keys is refused 422; Waiting rows carry Waiting on "Life OS", Done rows their completed at, Cancelled rows none; an imported task sends no notice (FR-TASK-IMP-06); `/tasks/import/dry-run` reaches its own handler, not `/tasks/{id}`); also `backend/tests/tasks_fake.py` (the fake store gains `existing_import_keys`, `get_import_run`, the `task_import_runs` table with 051's defaults and the uploader check, and `tasks_lifeos_key`), `backend/tests/test_task_privacy_sweep.py` (its scope check requires a fixture for each new `/tasks` route) and `backend/tests/test_task_routes.py` (its route count, 15 to 17)
- [x] T072 [P] Create `backend/tests/db/test_task_import_db.py` (`tasks_lifeos_key` uniqueness; run record audited and out of the TVA audit)

**Done when.**
- [x] All gates
- [ ] Journey 4.1 by hand on the fixture

---

## S1e — Phase 1 hardening, docs and amendments

**Goal.** Prove every phase-1 acceptance criterion with a named test, measure
the 5,000-task target, write the living docs, and amend the specs 008 changes.

**Depends on.** S1d.

**Spec.** Spec §12 phase 1 (all); NFR-TASK-01 to -08; FR-TASK-CHG-01 to -11
(amendments); spec §15.1 (constitution).

**Migrations.** None, unless the 5k check shows a missed index; then
`057_task_performance_indexes.sql` naming the measured query (`056` is S1b's link-removal lock fix).

- [x] T073 Create `backend/tests/test_task_acceptance.py`: one test per phase-1 line of spec §12, running the scenario through the routes or asserting the named covering test exists and is not skipped. **Two stated exceptions**, proven by a recorded opt-in run instead of an unskipped test: the migration-mapping line (ids, links, history kept; In progress tag; linkless tasks to Trash) by `TestMapping` under `TASKS_MIGRATION_CHECK=1` (S1a, output in its commit message), and the 5,000-task speed line (NFR-TASK-01) by `tasks_load_5k.py` (T075, output in this slice's commit message). For those two, the acceptance test asserts the covering class or script exists and names where its recorded output is
- [x] T074 Extend `backend/tests/test_task_privacy_sweep.py` to the full FR-TASK-VIS-06 matrix (the ten kinds: TVA admin, manager, owner, the creator's TVA lead, CRM Admin, Viewer, `crm_only`, former assignee, Waiting-on person, removed member; removed member cannot read CRM tasks they created or were assigned; private-era history), keeping T050's scope (task routes and tools plus the explicit task-touching list; other `/api/v1/crm` routes and `crm_*` tools left to the CRM isolation sweep), so 009's additions never require an edit here
- [x] T075 [P] Create `backend/scripts/simulations/tasks_load_5k.py`: seed 5,000 tasks for one person locally, time `GET /tasks` and `POST /tasks` through the API, print p95 (run under the db lock)
- [x] T076 [P] Modify `backend/tests/test_mcp.py` `test_the_crm_exception_is_the_specs` to also find the tasks clause in `specs/004-mcp/spec.md` FR-MCP-01
- [x] T077 [P] Create `docs/architecture/tasks.md`: one table and two writers, the deferred check, the visibility function and why there is no RLS policy, the 009 interface, phase levels, what changed in the CRM, the three easy-to-get-wrong things (`crm.actor`, the old table names in audit rows, the TVA audit exclusion)
- [x] T078 [P] Create `docs/guides/10-tasks.md` (frontmatter `title: Your tasks`, `order: 11`; `10-crm-admin.md` keeps order 10 and 009's `docs/guides/12-crm-intelligence.md` takes order 12, plan §9 rule 3): My Tasks, buckets, quick add and its questions, who sees what, CRM and project tasks, Life OS import (owner), Claude's `task_*` tools. Public: no names or figures. Add exactly one line for it to the guides table in `docs/README.md`
- [x] T079 [P] Modify `docs/operations/deployment.md`: migrations 046–050 (051 with S1d), the `SELECT count(*)` of live linkless CRM tasks to report before `db push`, release order (migrate, then api, worker, beat from one commit), raising levels person by person
- [x] T080 Spec amendments, each one dated line "Amended 2026-10-NN by `008` FR-TASK-CHG-nn": `specs/007-crm/spec.md` (FR-CRM-TASK-01 and §5.9, FR-CRM-CHG-01, FR-CRM-PIPE-09 and automation 9, FR-CRM-TODAY-01, FR-CRM-CHG-12, FR-CRM-AUD-04, FR-CRM-MIG, FR-CRM-LIST-02, -09), `specs/004-mcp/spec.md` (FR-MCP-01)
- [x] T081 Constitution: prepare the MINOR amendment of `.specify/memory/constitution.md` (task keeping in the Employee's and Seller's journeys; §I's RLS sentence worded as practised) and **stop for the owner's explicit agreement** before committing it (spec §15.1)

**Done when.**
- [ ] All gates
- [ ] `test_task_acceptance.py` has one passing, unskipped test per phase-1 line of §12, except the two recorded opt-in lines named in T073
- [ ] The 5k p95 figures are under 1 s and recorded in the commit message (the script run under the db lock in plan §8.1's one-invocation form)
- [ ] `/help/tasks` renders; `docs/README.md` gained exactly one line
- [ ] The owner has agreed the constitution amendment (or phase 1 is not raised above level 0 for anyone but him)

---

## S2a — Delegation: accept, comment, hand back, notify, waiting

**Goal.** Assigning to someone else sends one message; the assignee sees New
until they act, can comment, complete or hand back; the creator is told;
Waiting on names someone the caller may assign.

**Depends on.** S1e.

**Spec.** FR-TASK-DEL-01 to -08; Journey 4.7.

**Switch.** Level ≥ 2 for the new routes; the notice is sent whatever the
assignee's level (they still need level 1 to be assigned a task with no
workspace, FR-TASK-ROLL-04).

**Migrations.** `052_task_delegation.sql` (data-model §2.1 phase 2, §2.5–2.7).

- [x] T082 Write `supabase/migrations/052_task_delegation.sql`
- [x] T083 [P] Create `backend/app/domain/tasks/delegation.py`: `is_new`, `hand_back_plan`, `notice_events(before, after, actor)`, the waiting-on rule (FR-TASK-DEL-02, -03, -05, -06)
- [x] T084 Create `backend/app/services/tasks/notices.py` (claim `task_notice_sends`, build text at send, `notify.deliver`; the CRM bell for CRM tasks stays in `services/crm/notifications.py`) and add `tasks.send_notice` to `backend/app/tasks/task_jobs.py` (ids only) (FR-TASK-DEL-01, -05, -07); also `backend/app/services/tasks/store.py` (added in S2a because the sender, the comments and the settings need reads and writes S1 did not have: `claim_notice` and `mark_notice_sent` on `task_notice_sends`, `list_comments`, `get_comment`, and `set_nudge_days` on the person's own profile) and `backend/app/services/tasks/viewer.py` (changed in S2a because the sender checks that the recipient may still see the task before it builds the text (FR-TASK-VIS-04: notifications hold only tasks the reader may see), and the recipient is not the signed-in person: `load(user)` now calls a new `of_profile(profile)`, which builds the same `Viewer` from any profile row)
- [x] T085 Modify `backend/app/domain/tasks/service.py`: set `assigned_by`/clear `accepted_at` on reassignment; set `accepted_at` on any change, comment or completion by the assignee; enqueue notices; `accept_task`, `hand_back`, `add_comment`, `delete_comment`; also `backend/app/services/tasks/present.py` (changed in S2a because `TaskOut` gains `new` and `accepted_at` from phase 2, contracts §0.1; `new` is shown in the assignee's own view once phase 2 is on for them) and `backend/tests/test_task_create_interface.py` (changed in S2a because T033's `test_returns_task_out` pins `TaskOut`'s keys, which contracts §0.1 extends by those two in phase 2) and `backend/app/services/crm/records.py` (changed in S2a because a CRM task is created, updated and completed there, not in `service.py`, and FR-TASK-DEL-01, -02 and -05 cover CRM tasks too: create sets `assigned_by`; update sets `assigned_by`/clears `accepted_at` on reassignment and sets `accepted_at` on any other change by the assignee; `complete_task` sets `accepted_at`; each calls `notices.after_write` at the actor's level, beside the existing CRM bell, which stays (FR-TASK-DEL-01); existing functions' lines only, plan §9 rule 3a(c))
- [x] T086 Create `backend/app/api/tasks/comments.py` (comments) and add `/accept`, `/hand-back`, `GET/PATCH /tasks/settings` to `backend/app/api/tasks/tasks.py` (contracts §2), `/tasks/settings` declared before `/tasks/{id}`; register `comments.py` in `ROUTERS` before the `tasks.py` router; also `backend/app/schemas/tasks.py` (added in S2a for the request bodies of contracts §2: `TaskNoteIn` for the hand-back note, `TaskCommentIn`, `TaskSettingsIn` with `nudge_days` 1–30; the earlier models unchanged), `backend/tests/test_task_routes.py` (changed in S2a because T047's `test_the_slice_adds_routes` pins the number of task routes, 17 to 24, and its fixed-path order test read any `{id}` route ahead of a fixed path as wrong, while `comments.py`'s `/tasks/{id}/comments` routes, listed first as this task says, have more segments than any fixed path and can never take one; it now checks only routes that could take a fixed path) and `backend/tests/test_task_privacy_sweep.py` (changed in S2a because T050's sweep requires a fixture for every route under `/api/v1/tasks`; its cast gets phase 2 so those routes answer 404, not 403, and the private and shared tasks carry a comment)
- [x] T087 [P] Modify `frontend/components/tasks/task-panel.tsx` (comments, Accept, Hand back with note), `frontend/components/tasks/task-row.tsx` (New badge), and create `frontend/components/tasks/task-settings.tsx` (nudge days); also `frontend/lib/api/task-types.ts` and `frontend/lib/api/tasks.ts` (the phase-2 types and calls, where every other task call lives), `frontend/components/tasks/my-tasks.tsx` (mounts `task-settings.tsx` and tells the panel whether phase 2 is on and who the reader is, so Hand back is offered to the assignee only) and `frontend/app/(portal)/tasks/page.tsx`, `frontend/app/(crm)/crm/[ws]/my-tasks/page.tsx` (each passes `me.tasks.level` and `me.id` to My Tasks)
- [x] T088 [P] Create `backend/tests/test_task_delegation.py` (pure) and `backend/tests/test_task_delegation_routes.py` (exactly one message per assignment, none to self, completion and hand-back tell the creator once, retried job sends nothing twice, Waiting on limited to assignable people and gives no sight, comments visible to whoever sees the task and deletable by author only; `PATCH /tasks/settings` refuses `nudge_days` outside 1–30 with 422 (FR-TASK-DEL-06); `GET /tasks/settings` reaches its own handler, not `/tasks/{id}`; a task with no workspace sends no reminder (FR-TASK-DEL-08); an imported task sends no notice); also `backend/tests/tasks_fake.py` (the fake store gains 052's `task_comments` (insert and soft delete only, the task's workspace) and `task_notice_sends`, and the S2a store functions; `TaskWorld` sends each message at once through the real `notices.send` and records it in `messages`)
- [x] T089 [P] Create `backend/tests/db/test_task_delegation_db.py` (`task_notice_sends` uniqueness; comment workspace trigger; comments not editable through `task_apply`; deleting a profile referenced by `assigned_by` succeeds with a system audit row)

**Done when.**
- [ ] All gates
- [ ] Journey 4.7 by hand with Slack DM in a dev workspace (or the fake channel, said in the commit)

---

## S2b — Waiting on, Assigned by me, the morning brief, task panels on records and projects

**Goal.** Two new views, the brief as a page and a tool, the TVA project task
panel, and the CRM record Tasks tab on the shared panel.

**Depends on.** S2a.

**Spec.** FR-TASK-VIEW-01 to -03; FR-TASK-BRIEF-01 to -03; FR-TASK-ADD-05
(project page); FR-TASK-MCP-01 (`task_brief`); Journey 4.8.

**Switch.** Level ≥ 2.

**Migrations.** None.

- [x] T090 [P] Modify `backend/app/services/tasks/listing.py`: `view=waiting` (grouped, age in days) and `view=assigned_by_me`; `project_tasks(viewer, project_id)`; and `backend/app/services/tasks/store.py` (`created_tasks`: the read of the tasks a person created, which Assigned by me and the brief's nudges need)
- [x] T091 [P] Create `backend/app/domain/tasks/brief.py` (today, overdue, nudge by `task_nudge_days` against `updated_at`) and `backend/app/services/tasks/brief.py` (calls `services/crm/today.build` per current membership, unchanged; FR-TASK-BRIEF-01)
- [x] T092 Create `backend/app/api/tasks/brief.py` (`GET /tasks/brief`) and add `GET /tasks/project/{project_id}` (`UUID`) to `backend/app/api/tasks/tasks.py` before `/tasks/{id}`; register `brief.py` in `backend/app/api/tasks/__init__.py` `ROUTERS` before the `tasks.py` router
- [x] T093 Modify `backend/app/mcp/tasks.py`: `task_brief`; `task_list` accepts `assigned=by_me`
- [x] T094 [P] Modify `frontend/components/tasks/my-tasks.tsx`: views Mine, Waiting on, Assigned by me, Brief (`?view=`), shown at level ≥ 2; and `frontend/lib/api/tasks.ts`, `frontend/lib/api/task-types.ts` (the calls and types of the three views, the project panel and the brief)
- [x] T095 [P] Create `frontend/components/tasks/project-tasks.tsx` and modify `frontend/app/(portal)/analytics/page.tsx` (`ProjectDetail` gains the panel with Add task pre-linked) (FR-TASK-VIEW-03, FR-TASK-ADD-05)
- [x] T096 [P] Modify `frontend/components/crm/record-page.tsx`: the Tasks tab opens tasks in `components/tasks/task-panel.tsx` (FR-TASK-VIEW-03); and `frontend/components/crm/task-dialog.tsx` (the tab's task list draws the titles, so it asks the record page to open the panel)
- [x] T097 [P] Create `backend/tests/test_task_views_routes.py` (waiting grouping and age; assigned by me; project panel lists only what the viewer may see; a TVA `user` cannot open the project page but sees the task in My Tasks; Add task from the project panel creates a task pre-linked to that project (FR-TASK-ADD-05); `/tasks/project/{id}` reaches its own handler) and `backend/tests/test_task_brief.py` (sections equal CRM Today's for each workspace; nudge after N days at a fixed clock; never pushed; `task_brief` returns the same; `/tasks/brief` reaches its own handler, not `/tasks/{id}`); and `backend/tests/tasks_fake.py` (the fake of `store.created_tasks`) and `backend/tests/test_task_routes.py` (its count of task routes gains the two new ones)
- [x] T098 Extend `backend/tests/test_task_privacy_sweep.py` and `backend/tests/test_task_mcp.py` (five tools) for the new routes and tool (same scope as T050); add the phase-2 lines to `backend/tests/test_task_acceptance.py`

**Done when.**
- [x] All gates
- [ ] Journey 4.8 by hand; `docs/guides/10-tasks.md` covers delegation, the views and the brief

---

## S3a — Day-log suggestions

**Goal.** Tasks a person finished today are offered on Log a day; accepting
edits the draft only; Save day is unchanged.

**Depends on.** S2b.

**Spec.** FR-TASK-LOG-01 to -08; FR-TASK-CHG-08; G-6; Journey 4.9.

**Switch.** Level ≥ 3; never for `crm_only` (TVA routes refuse them anyway).

**Migrations.** None (`tasks_done_on` index exists since 047).

- [ ] T099 [P] Create `backend/app/domain/tasks/suggestions.py` (which tasks; project only when confirmed and unarchived; "already added" when the title is in a note that day) (FR-TASK-LOG-01, -05). No merge code here: Accept and the merge are client state (plan §5.9) and live only in T101
- [ ] T100 Create `backend/app/services/tasks/suggestions.py` and modify `backend/app/services/timesheets.py` (`day_for` adds `suggestions` for level ≥ 3 on a loggable day) and `backend/app/api/timesheet.py` (passes the level; `/timesheet/of/{user_id}/week` and team routes untouched)
- [ ] T101 [P] Modify `frontend/app/(portal)/timesheet/page.tsx`: suggestions with Add and Ignore (screen-only state), merge of accepted tasks of one project into that line's note within 500 characters at zero hours, ask for a project or activity when none, the note-visibility warning (FR-TASK-LOG-04 to -06); and create `frontend/e2e/day-log.spec.ts` (local): accepting two tasks on one project gives one line; saving with hours writes one normal entry; nothing is written without Save; a zero-hour line is not saved; the note-visibility warning shows (phase-3 AC; FR-TASK-LOG-06)
- [ ] T102 [P] Modify docstrings of `my_day` and `log_day` in `backend/app/mcp/server.py` (FR-TASK-CHG-08, FR-TASK-LOG-07)
- [ ] T103 [P] Spec amendments: `specs/002-timesheets/spec.md` §2.2, `specs/003-management/spec.md` Q-03 / FR-TIME-11 (FR-TASK-CHG-08), one dated line each
- [ ] T104 [P] Create `backend/tests/test_task_suggestions.py` (fixed clock: completed today in IST; imported never; tentative/archived project gives no project; none for `crm_only`, below level 3, future or locked day; never in someone else's week, team weeks or sign-off; nothing stored; title already in a note shows as added) and extend `backend/tests/test_task_acceptance.py` (phase 3 suggestion lines)

**Done when.**
- [ ] All gates
- [ ] Journey 4.9 by hand; nothing is written to `time_entries` without Save

---

## S3b — Recurring tasks

**Goal.** A task may recur; completing or cancelling it creates exactly one
next occurrence; starter templates can be added.

**Depends on.** S3a.

**Spec.** FR-TASK-RRULE-01 to -06; TP-18; T9; NFR-TASK-07.

**Switch.** Level ≥ 3.

**Migrations.** `053_task_recurrence.sql` (data-model §2.8).

- [ ] T105 Write `supabase/migrations/053_task_recurrence.sql`
- [ ] T106 [P] Modify `backend/pyproject.toml`: `python-dateutil` in its own commented block (plan §2.1); `uv lock`
- [ ] T107 [P] Create `backend/app/domain/tasks/recurrence.py` (the four allowed forms; `next_due(rule, current_due)` with `dateutil.rrule`; validation sentences)
- [ ] T108 Modify `backend/app/domain/tasks/service.py`: complete or cancel of a recurring task inserts the next occurrence and sets `recurrence_spawned` in one `task_apply` call; reopen-and-complete creates none; removing `recurrence` ends the series
- [ ] T109 Create `backend/app/services/tasks/templates.py` and `backend/app/api/tasks/templates.py` (`POST /tasks/templates/{key}`; `statutory_dates` refused until the owner's list exists); register the router in `ROUTERS` before the `tasks.py` router
- [ ] T110 [P] Modify `frontend/components/tasks/task-panel.tsx` (recurrence picker: daily, weekly on days, monthly on a day, yearly) and `frontend/components/tasks/task-settings.tsx` (templates)
- [ ] T111 [P] Create `backend/tests/test_task_recurrence.py` (pure, fixed clock: month-end, leap year, weekly days) and `backend/tests/test_task_recurrence_routes.py` (exactly one next occurrence on complete and on cancel; second completion after reopen creates none; due date required; the next occurrence copies the fields and links (FR-TASK-RRULE-02); removing the recurrence ends the series (FR-TASK-RRULE-04)) and `backend/tests/test_task_templates_routes.py` (each template key creates its recurring task; `statutory_dates` answers 422 "The statutory dates are not set up yet."; `/tasks/templates/{key}` reaches its own handler) (FR-TASK-RRULE-05); `backend/tests/db/test_task_recurrence_db.py` (`(series_id, due_on)` uniqueness); extend `backend/tests/test_task_acceptance.py`

**Done when.**
- [ ] All gates
- [ ] `docs/guides/10-tasks.md` covers suggestions and recurring tasks

---

## S4a — Voice capture

**Goal.** A voice note becomes task and inbox candidates by fixed rules,
confirmed one tap each; the audio and transcript are gone within 24 hours at
most.

**Depends on.** S3b.

**Spec.** FR-TASK-VOICE-01 to -05; T10; TP-22; Journey 4.10.

**Switch.** Level ≥ 4, and `TASKS_VOICE_CONFIGURED` on `api`.

**Migrations.** `054_task_voice.sql` (data-model §2.9).

- [ ] T112 Write `supabase/migrations/054_task_voice.sql`
- [ ] T113 [P] Modify `backend/app/config.py`: `GROQ_API_KEY: SecretStr | None`, `TASKS_VOICE_CONFIGURED: bool = False`
- [ ] T114 [P] Create `backend/app/domain/tasks/voice_split.py` (sentences, then "and" / "also" joins; "idea" / "read" / a link → inbox; each candidate through `quick_add.parse`) (FR-TASK-VOICE-03)
- [ ] T115 Create `backend/app/services/tasks/groq.py` (one `httpx` multipart POST to Groq `whisper-large-v3`; no SDK; the key never logged) and `backend/app/services/tasks/voice.py` (upload to the private bucket, row, candidates, confirm, drop, transcript cleared on the last)
- [ ] T116 Modify `backend/app/tasks/task_jobs.py`: `tasks.transcribe_voice` (`ignore_result=True`; deletes the audio in `finally`) and `tasks.voice_sweep`; add `"tasks-voice-sweep"` (every 30 min) to the 008 block in `backend/app/worker.py`
- [ ] T117 Create `backend/app/api/tasks/voice.py` (contracts §2 voice routes; `upload_id: UUID`) and register it in `ROUTERS` before the `tasks.py` router
- [ ] T118 [P] Create `frontend/components/tasks/voice-capture.tsx` (record with `MediaRecorder` or upload, poll, candidates with confirm, edit, drop) and add it to `frontend/components/tasks/my-tasks.tsx` at level ≥ 4
- [ ] T119 [P] Create `backend/tests/test_task_voice_split.py` and `backend/tests/test_task_voice_routes.py` (fake Groq: nothing saved before confirm; source `voice`; audio deleted after transcription and after failure; sweep deletes within 24 h; transcript cleared on the last candidate; never in the Celery result or logs; 503 while unconfigured; a file over 4 MB answers 413 (FR-TASK-VOICE-01); `/tasks/voice` reaches its own handler); `backend/tests/db/test_task_voice_db.py` (bucket private, no `storage.objects` policy)

**Done when.**
- [ ] All gates
- [ ] Journey 4.10 by hand with a real key locally (or the fake, said in the commit); `docs/operations/deployment.md` gains `GROQ_API_KEY` (worker only) and `TASKS_VOICE_CONFIGURED` (api)

---

## S4b — Ideas and Reading inbox, `inbox_add`, the weekly review, final sweep

**Goal.** A private inbox with Make it a task and Archive, a tool to add to
it, the Monday review panel, and the acceptance sweep for phases 2–4.

**Depends on.** S4a.

**Spec.** FR-TASK-INBOX-01 to -03; FR-TASK-REVIEW-01; FR-TASK-MCP-01
(`inbox_add`); spec §12 phases 2–4.

**Switch.** Level ≥ 4.

**Migrations.** `055_task_inbox_review.sql` (data-model §2.10).

- [ ] T120 Write `supabase/migrations/055_task_inbox_review.sql`
- [ ] T121 [P] Create `backend/app/services/tasks/inbox.py` (owner-only; Make it a task through `create_task`; archive) and `backend/app/domain/tasks/review.py` (Monday in the caller's zone; This week and This month items; waiting over 7 days; unprocessed inbox; dismissed week)
- [ ] T122 Create `backend/app/api/tasks/inbox.py` and `backend/app/api/tasks/review.py` (contracts §2; `{id}` typed `UUID`) and register them in `ROUTERS` before the `tasks.py` router
- [ ] T123 Modify `backend/app/mcp/tasks.py`: `inbox_add`
- [ ] T124 [P] Create `frontend/components/tasks/inbox.tsx` and `frontend/components/tasks/weekly-review.tsx`; modify `frontend/components/tasks/my-tasks.tsx` (Inbox view; review panel on Mondays with one-tap bucket moves and Dismiss)
- [ ] T125 [P] Create `backend/tests/test_task_inbox_routes.py` (owner-only, admins included; Make it a task links the two; `/tasks/inbox` and `/tasks/review` reach their own handlers, not `/tasks/{id}`) and `backend/tests/db/test_task_inbox_db.py` (deleting a profile that owns inbox items succeeds, the cascade audited as the system) and `backend/tests/test_task_review.py` (fixed clock Monday/Tuesday; dismiss for the week; never sent)
- [ ] T126 Extend `backend/tests/test_task_privacy_sweep.py` (inbox and voice routes, `inbox_add`; same scope as T050), `backend/tests/test_task_mcp.py` (six tools), and `backend/tests/test_task_acceptance.py` to every line of spec §12 phases 2–4
- [ ] T127 [P] Modify `docs/guides/10-tasks.md` (voice, inbox, weekly review) and `docs/architecture/tasks.md` (phases 2–4)

**Done when.**
- [ ] All gates
- [ ] `test_task_acceptance.py` has one passing, unskipped test per line of spec §12
- [ ] The privacy sweep passes for every route and tool, with exactly six task tools registered

---

## Coverage — spec areas to slices

| Spec area | Slices |
|---|---|
| §3 Who sees what | S1a (function), S1b (routes, sweep), S1e (full matrix) |
| §5.1 Data (DATA-01–15) | S1a; DATA-06, -09, -11, -13 routes S1b |
| §5.2 Visibility (VIS-01–08) | S1a function and CRM presenter; S1b routes and sweep; VIS-08 UI S1c; VIS-06 complete S1e |
| §5.3 My Tasks (LIST-01–09) | backend S1b; page S1c; LIST-09 S1a |
| §5.4 Capture (ADD-01–06) | S1c; ADD-05 project page S2b |
| §5.5 Claude (MCP-01–05) | S1b; `task_brief` S2b; `inbox_add` S4b; MCP-04 S1a |
| §5.6 Life OS import (IMP-01–08) | S1d |
| §5.7 Switches (ROLL-01–05) | S1a (columns, admin, `/me`); S1b (guard); S1c (UI) |
| §6.1 Delegation (DEL-01–08) | S2a |
| §6.2 Views (VIEW-01–03) | S2b |
| §6.3 Brief (BRIEF-01–03) | S2b |
| §6.4 Day log (LOG-01–08) | S3a |
| §6.5 Recurrence (RRULE-01–06) | S3b |
| §6.6 Voice (VOICE-01–05) | S4a |
| §6.7 Inbox (INBOX-01–03) | S4b |
| §6.8 Review (REVIEW-01) | S4b |
| §7 NFRs | 01 S1e; 02 S1b/S1e; 03 S1a; 04 S1b; 05 S1c and every UI slice; 06 S1c; 07 S3b/S4a; 08 every slice |
| §8 Changes (CHG-01–11) | S1a code; CHG-02, -03 S1b; amendments S1e; CHG-08 S3a |
| §12 Acceptance | per slice; complete in S1e (phase 1) and S4b (phases 2–4) |
| §14 What 009 relies on | S1a (`create_task`, `TaskCreate`) |
| §15.1 Owner actions | constitution S1e (stop for agreement); statutory dates S3b (template refused until supplied); Groq key S4a; SMTP sender: none (deployment note) |

## Task tools by slice (6)

| Slice | Tools | Running total |
|---|---|---|
| S1b | `task_add`, `task_list`, `task_update`, `task_complete` | 4 |
| S2b | `task_brief` | 5 |
| S4b | `inbox_add` | 6 |

008 adds and renames no `crm_*` tool (FR-TASK-MCP-04) and changes only the
bodies of five of them (S1a T024). No 008 test asserts how many `crm_*` tools
exist; `tests/test_crm_mcp.py` (009's) does.
