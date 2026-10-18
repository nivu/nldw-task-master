# Tasks — one task list for the whole portal

| | |
|---|---|
| **Feature** | `008-tasks` |
| **Status** | Draft 18 October 2026, revised after review — owner decisions T1–T10, C6 (tasks part) and O1 recorded in §11; proposals TP-01–TP-22 accepted by the owner on 18 October 2026 and recorded in §11 |
| **Source** | [`source.md`](source.md): *TVA Tasks: one task system for the whole portal* v0.1, Navaneeth, 18 October 2026. Where this file and the source disagree, this file wins. |
| **Depends on** | `001`–`007`, all in production. `009-crm-intelligence` depends on this spec: its Review Queue creates tasks here (O1). |

The portal gains one task list. A task is written once and shows wherever it
belongs: the person's own list, the CRM record it is linked to, the TVA project
it is linked to, and CRM Today. The CRM's task table becomes that one table
(T1). Nothing new is built where the CRM already does the job; where the source
asks for something the CRM already has, this spec says so and points at the
`007` requirement.

Built one phase at a time, starting with phase 1 (O1). Each phase is switched on
per person (C6).

| Phase | Contains |
|---|---|
| **1** | The CRM task table becomes the shared `tasks` table; new statuses; personal tasks; privacy; My Tasks with buckets; quick add; `task_*` tools; the Life OS import; per-person phase switches |
| **2** | Delegation (accept, comment, hand back, notify on done); Waiting on; Assigned by me; morning brief; task panels on record and TVA project pages |
| **3** | Day log suggestions; recurring tasks |
| **4** | Voice capture; Ideas and Reading inbox; weekly review prompt |

Identifiers: this spec's own settled items are **TQ-nn** (§11) and the
proposals the owner accepted on 18 October 2026, **TP-nn**, also settled in
§11. A bare "Q-nn" never appears; another spec's item is
always cited with its spec number, such as `003` Q-03 or `007` Q-57.

---

## 1. Context

Navaneeth keeps tasks in a Google Sheet ("Life OS"), in Apple Notes, in the CRM
and in ClickUp. None of them sees the others, and none of them sees the portal's
projects or the day log. The CRM already has a task model (`007` §5.9): title,
body, status, due date, a bucket (Today, This week, This month, Later), category,
assignee, links to companies, people, deals and campaigns, a Task overdue flag,
reminders (automation 7), a Tasks section in Today, a Tasks page, record-page
task tabs and two MCP tools. That model is good, but it can only hold tasks that
belong to a CRM workspace and a CRM record.

This spec widens the CRM's task table so that it can also hold a person's own
tasks and tasks about a TVA project, without breaking anything the CRM does
with tasks today.

Three facts about the code shape every decision below:

1. **The backend is the only gate.** The backend uses the Supabase service-role
   key, which bypasses row-level security. No table since `010` has a browser
   grant, and the frontend reads no data from Supabase (constitution §IV; `007`
   FR-CRM-ACC-10). A private task is kept private by backend code, not by an RLS
   policy (T4).
2. **Every CRM write goes through one database writer** that requires a
   workspace and refuses tables not named `crm_*`, and an audit trigger refuses
   any change made without an actor. A table renamed `tasks`, with rows that have
   no workspace, needs a write path that keeps both guarantees.
3. **The day log replaces the whole day on save** and allows one line per
   project (or activity) per day. "Accept a suggestion" can only mean "add it to
   the day you are editing", never a separate insert.

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — Within two weeks of phase 1 reaching him, the owner keeps his tasks in the portal and stops adding them to the Life OS sheet and Apple Notes.
- **G-2** — One task, one row: a task linked to a deal is the same row in the owner's list, on the deal and in CRM Today; completing it anywhere completes it everywhere.
- **G-3** — Adding a task takes one line of text, from the web app or from Claude.
- **G-4** — A private task is seen by its creator and nobody else, admins included.
- **G-5** — Every CRM task behaviour of `007` keeps working through the migration, with only the changes listed in §8.
- **G-6** *(phase 3)* — Tasks a person finished today are offered as lines when they log the day; nothing is logged for them.

### 2.2 Non-goals

- A second task table. The CRM's table is reused (T1).
- A project-management tool: no boards, Gantt charts, time estimates, story points, or subtasks deeper than one level.
- Measuring people. No productivity score, leaderboard, count of tasks per person on any team-facing page, or hours or cost on a task. **One existing exception stays**: the CRM dashboard's Activity panel (`007`, completed tasks per owner per week) keeps counting the workspace's CRM tasks as today. Tasks with no workspace (personal tasks and project tasks with no CRM link) never count there or anywhere else.
- Sync with Google Sheets, ClickUp or Apple Notes. Apple Notes content is moved by hand or pasted into quick add.
- AI in quick add or voice splitting. Quick add uses fixed rules (T7), and so does splitting a voice note into candidates (TP-22); the only outside service is speech-to-text in phase 4 (T10).

---

## 3. Who sees what

### 3.1 Kinds of task

A task's kind follows from its links; it is not a stored choice.

| Kind | Links | Workspace | Who can see it (T4, T5) |
|---|---|---|---|
| **Private** | none; assignee is the creator | empty | The creator only |
| **Shared personal** | none; assignee is someone else | empty | The creator and the current assignee |
| **CRM** | one or more CRM records of one workspace, with or without a TVA project link | that record's workspace | Every **active member** of that workspace (as today, T5), and nobody else. The creator and the assignee see it only while they are active members; otherwise it is 404 to them. A project link never widens this (FR-TASK-VIS-07). |
| **Project** | one TVA project (TP-13), no CRM link | empty | Everyone who can see that TVA project (below), plus the creator and the current assignee |

"Can see a TVA project", for tasks, is a rule this spec creates. It mirrors who
can open project pages today: every project route is guarded at TVA role lead or
higher (`backend/app/api/admin.py` `list_projects`, `backend/app/api/timesheet.py`
project list and detail, all `LeadDep`). So:

- a TVA **lead, manager or admin** can see every project;
- a TVA **user** can see the projects they are or were allocated to, and may
  link those projects (TP-01);
- a **`crm_only`** account can see no project;
- a project's `lead_id` gives no sight: it is a label, not a permission
  (migration `018`).

This rule decides task visibility only. It gives no one a project page or
project read they do not already have.

Naming someone in **Waiting on** gives them no sight of the task. A former
assignee loses sight of a shared personal or project task when it is reassigned
or handed back.

### 3.2 What being an admin changes

Nothing beyond the project rule above. A TVA admin, manager or the owner has no
extra sight of tasks, except that, like every lead, they see project tasks with
no CRM link (§3.1). A CRM workspace Admin sees that workspace's CRM tasks because
every member does (`007` §3.3), and its Trash because Trash is admin-only today
(`007` FR-CRM-DEL-01). No one can list, search, export, count or audit-read
another person's private or shared personal task.

Because linking a project exposes a task to every lead, manager and admin, and
to everyone allocated to the project, the task panel and the quick-add preview MUST show who will see the task before a
link is saved (FR-TASK-VIS-08).

### 3.3 Who may change a task

| Task | Who may do what |
|---|---|
| **With a workspace** (CRM) | `007` §3.3 alone decides: Members and Admins edit; a Member deletes what they own or created (`007` Q-36); Admins restore from Trash (`007` FR-CRM-DEL-01); a Viewer assigned the task changes its status only (`007` Q-57). Being the creator or assignee gives no rights beyond the person's CRM role. |
| **With no workspace** (private, shared personal, project) | The creator and the current assignee edit any field and link or unlink (subject to FR-TASK-DATA-06 and FR-TASK-DATA-13). Only the creator deletes and restores. |
| **Project task, seen only through the project rule** | Read only (TP-02) |

---

## 4. User journeys

### 4.1 The owner moves off Life OS (P1, phase 1)

Navaneeth exports the Life OS Tasks tab as CSV and uploads it. The dry run shows
212 rows: 180 open, 32 done; it proposes an owner mapping (Navaneeth, Timothy and
Vinitha in the sheet to the accounts of Navaneeth, Timothy Paul and Vinita),
which he confirms; and it lists two status values it cannot map. He fixes the two
cells, re-exports, runs the dry run again and then the real run with the same
file. His own tasks appear in My Tasks under Today, This week, This month and
Later; the rows owned by Timothy and Vinita are assigned to them and appear in
their My Tasks and in his Assigned by me. He sets the sheet to read-only (§15).

**Independent test**: a fixture CSV with known counts; the dry run reports the
counts, the proposed owner mapping and every unmapped value; the real run creates
exactly those tasks; a second real run of the same file changes nothing; a real
run of an edited file creates only the new rows (FR-TASK-IMP-05).

### 4.2 Capture in one line (P1, phase 1)

On Tuesday 20 October 2026, on any portal page, Navaneeth types "Call Deepak re
RDX pilot fri #sales @Timothy" into the quick-add box. Before he saves, the box
shows: title "Call Deepak re RDX pilot", due Friday 23 October, tag sales,
assigned to Timothy Paul, bucket This week, "Visible to: you and Timothy Paul".
He presses Enter. It is in his Assigned list and in Timothy's My Tasks.

**Independent test**: each rule in FR-TASK-ADD-03 gives the stated result for a
fixed date; an ambiguous date cannot be saved without a choice.

### 4.3 A private task stays private (P1, phase 1)

Navaneeth adds "Book dentist". Vinita, a TVA admin, and Timothy, a Labs CRM
Admin, cannot find it through any page, route, search, export, audit view or
tool.

**Independent test**: FR-TASK-VIS-06.

### 4.4 A CRM task, two ways in (P1, phase 1)

Timothy adds a task on the Acme deal from the deal page. It shows on the deal,
in his My Tasks, in his CRM Today when it is due, and to every Labs member who
opens the deal. He ticks it done in My Tasks; the deal shows it done.

### 4.5 A seller with only the CRM (P1, phase 1)

Tamil, a CRM-only Labs member, opens My Tasks from the CRM, adds a private
reminder and sees his CRM tasks beside it, including a task on a deal that also
links the deal's TVA project; he sees that task without any project field. He
never sees a project task that has no CRM link, and gets no day-log suggestion
(T6).

### 4.6 Claude keeps the list (P1, phase 1)

From claude.ai, Navaneeth asks "what's on today?", then "add: send the GST
filing to the CA by the 20th" and "mark the Acme call done". Claude reads, then
states each change and waits for a yes before writing.

### 4.7 Handing work over (P2, phase 2)

Navaneeth assigns "Draft the RDX pilot plan" to Timothy. Timothy gets a Slack DM,
accepts it, later hands it back with a note "needs pricing from you"; Navaneeth
is told. Navaneeth sets another task to Waiting on Sanjeev; five days later it
appears in his morning brief as "nudge?".

### 4.8 The morning brief (P2, phase 2)

At 09:00 Navaneeth opens the brief: today's and overdue tasks, waiting items
due a nudge, and the CRM Today sections of each workspace he belongs to. Claude
gives him the same through `task_brief`.

### 4.9 Logging the day (P2, phase 3)

Devansh finished two tasks his lead assigned him on the Lumen project, and one
personal task, today. When he opens Log a day, three suggestions appear. He adds
the two Lumen tasks; they merge into one Lumen line with both titles in the note
and zero hours. He enters 3.5 office hours and saves. He ignores the third.

### 4.10 A voice note (P3, phase 4)

Walking back from a meeting, Navaneeth records "follow up with Kiran on the
contract tomorrow, and an idea: a webinar on agent evals". The portal shows one
task candidate (due tomorrow) and one idea; he confirms both with one tap each.

### 4.11 Edge cases

- Someone tries to unlink a task from its last CRM record (FR-TASK-DATA-05: refused).
- A CRM record is purged while tasks link only to it (FR-TASK-DATA-14).
- A CRM task is assigned to someone who is not a member of the workspace (FR-TASK-DATA-07).
- A project task with no CRM link is assigned to a CRM-only person (FR-TASK-DATA-07).
- A workspace member who created or was assigned CRM tasks is removed from the workspace (§3.1).
- Someone without My Tasks switched on is assigned a personal task (FR-TASK-ROLL-04).
- A deal is closed Lost with open tasks (FR-TASK-CHG-04: marked Cancelled; TP-07).
- Two finished tasks on the same project on the same day (FR-TASK-LOG-04).
- A task finished on a day whose log is locked (FR-TASK-LOG-03).
- A recurring task is completed twice by mistake (FR-TASK-RRULE-03).
- A Life OS row whose owner has no portal account (FR-TASK-IMP-04).

---

## 5. Functional requirements — phase 1

Keywords follow RFC 2119.

### 5.1 The task — FR-TASK-DATA

| Field | Notes |
|---|---|
| Title | Required, at most 200 characters, not blank (as today) |
| Notes | Optional, plain text with markdown, at most 20,000 characters (today's limit, `backend/app/schemas/crm.py` task body). Today's CRM *Body*, relabelled (TQ-08) |
| Status | Open, Waiting, Done, Cancelled (T2) |
| Bucket | Today, This week, This month, Later. Required on every open or waiting task (source §4). Today's CRM field, relabelled "Bucket" (TQ-09) |
| Due date | Optional |
| Priority | Urgent, High, Normal, Low; default Normal. New (source §3; TQ-09) |
| Assignee | Required; defaults to the creator |
| Created by | Set by the system |
| Waiting on | Required when status is Waiting: a person in the portal or free text |
| Tags | Free text tags, at most 40 characters each and at most 10 per task (TP-14) |
| Parent | Optional; a parent may not itself have a parent |
| Links | Zero or more: CRM records (company, person, opportunity, campaign) of one workspace, and TVA projects (T3). At most one project per task (TP-13) |
| Workspace | Set by the system from the CRM links, empty when there are none (T1) |
| Category | CRM tasks only; the workspace's task category list, as today |
| Recurrence | *(phase 3)* |
| Source | Manual, NLDW, voice, email commitment, meeting action, rule, import |
| Source reference | Optional text (import key, thread, transcript or rule id) |
| Completed at | Set by the system when status becomes Done, cleared when it leaves Done |
| Created, updated, deleted at and by | As today; updated at is the optimistic lock |

Kept from today and not in the source: position (order within a bucket), the
stored CRM flag fields, the Twenty id, and the deletion group used by Trash.

Labels: on a task with a workspace, status and bucket labels come from that
workspace's option lists, as today. On a task with no workspace, status, bucket
and priority labels are fixed in code (FR-TASK-CHG-11).

| ID | Requirement |
|---|---|
| FR-TASK-DATA-01 | The CRM task table MUST become the shared task table, named `tasks`, keeping every row and id (T1). The CRM task links table MUST become the shared task links table, keeping every row (T3). No second task table is created. |
| FR-TASK-DATA-02 | A task MUST have the fields above. A refusal names every missing or invalid field in one sentence (RFC 7807). |
| FR-TASK-DATA-03 | Status MUST be one of Open, Waiting, Done, Cancelled. Completed at MUST be set exactly when status is Done, and the database MUST check it, as it does today. Cancelled is not done: it never counts as completed work. |
| FR-TASK-DATA-04 | "Open" in every rule, filter, count and flag that today means "not Done" MUST mean "Open or Waiting". A cancelled task is closed. The places that change are listed in §8.2. |
| FR-TASK-DATA-05 | For every task that is not deleted, the workspace MUST be empty when the task has no CRM link, and MUST equal the workspace of every CRM record it links to when it has one (T1). The database MUST check this as a **deferred check at the end of the transaction**, because tasks are inserted before their links (the CRM writer's apply step; the `033` deal transition). Linking a personal or project task's first CRM record sets its workspace. **Removing a task's last CRM link MUST be refused** (422 "A CRM task needs at least one linked record; delete it instead."), as `backend/app/services/crm/records.py` does today: a task that was ever a CRM task never becomes personal, so its workspace history never leaves the workspace. A task cannot link records of two workspaces. A deleted task keeps its workspace in Trash; restoring a deleted task that has no CRM link left is refused 422. |
| FR-TASK-DATA-06 | A TVA project link MUST name an existing project that the caller can see (§3.1); otherwise 404. A confirmed or tentative project MAY be linked; an archived project MUST NOT be newly linked (422 "That project is archived."), and existing links to a project that is later archived stay. A task whose CRM links are in a workspace other than Labs MUST NOT carry a TVA project link (422 "Only Nunnari Labs tasks can link a TVA project."; `007` FR-CRM-WS-05). |
| FR-TASK-DATA-07 | The assignee MUST be an active account. On a CRM task (any task with a workspace, whatever its project link) they MUST be an active member of its workspace (as today, `007` FR-CRM-TASK-01), and a `crm_only` member MAY be assigned it. On a project task with no CRM link they MUST NOT be a `crm_only` account (422 "A CRM-only account cannot be given a project task.") (TP-03). On any other task they MAY be anyone with phase 1 switched on (FR-TASK-ROLL-04). |
| FR-TASK-DATA-08 | Every open or waiting task MUST have a bucket. When none is given: Today if the due date is today or past, otherwise Later (source §4). |
| FR-TASK-DATA-09 | Subtasks: a task MAY have a parent that has no parent itself. A parent's visibility does not extend to its children; each is checked on its own. Completing a parent does not complete its children. Setting a parent the caller cannot see MUST be answered 404. A parent the reader cannot see is shown as "a private task", with no id or title. |
| FR-TASK-DATA-10 | Every change to a task or its links MUST be audited with actor, actor kind, channel and before and after values, through the same audit trigger as today, and MUST be made through the task write path, which sets the actor (TQ-02). |
| FR-TASK-DATA-11 | Deleting is soft. A deleted CRM task goes to the workspace Trash as today. A deleted task with no workspace can be restored by its creator from My Tasks; it is kept for 90 days, then removed by the daily purge job (TP-12). |
| FR-TASK-DATA-12 | Source and source reference identify where a task came from. Life OS import keys (source Import, reference starting `lifeos:`) MUST be unique, so a re-run cannot duplicate (FR-TASK-IMP-05). The uniqueness covers only those keys; Twenty-imported tasks (no source reference) are outside it. |
| FR-TASK-DATA-13 | Adding or removing a CRM link through the task routes or `task_*` tools MUST require the caller to be a Member or Admin of that record's workspace (`007` §3.3); a record in a workspace the caller does not belong to MUST be answered 404 (`007` FR-CRM-WS-02). A Viewer cannot turn a personal task into a CRM task. |
| FR-TASK-DATA-14 | Purging a CRM record (`crm_purge`, migration `042`) MUST, in the same transaction, purge every task of that workspace that is left with no CRM link. Such a task never becomes personal. |
| FR-TASK-DATA-15 | Every row of the shared links table MUST carry a plain foreign key `task_id → tasks(id) ON DELETE CASCADE`, so links of tasks with no workspace are checked and cascade on purge. A `tva_project` row MUST carry a real foreign key to `projects(id)`. Rows of CRM records keep the composite (workspace, task) foreign key and the same-workspace check of today (migration `030`); a `tva_project` row has no workspace. When a task gains its first CRM link, the change of workspace and the new link row are written in one transaction. |

### 5.2 Visibility — FR-TASK-VIS

| ID | Requirement |
|---|---|
| FR-TASK-VIS-01 | Whether a person may see, change or delete a task MUST be decided by one backend function, implementing §3, that every task route, every CRM route that returns or changes tasks, and every MCP tool passes through (T4). No route or tool reads tasks without it. |
| FR-TASK-VIS-02 | A task the caller may not see MUST be answered 404, never 403, so its existence is not disclosed (as `007` FR-CRM-WS-02). |
| FR-TASK-VIS-03 | The `tasks` table, the links table and every new table in this spec MUST have RLS enabled with no policy and no grant to `anon` or `authenticated`, as every table since `010` (T4; `007` FR-CRM-ACC-10). The frontend reads no task data from Supabase. |
| FR-TASK-VIS-04 | Lists, counts, searches, exports, Today, digests, notifications and the morning brief MUST contain only tasks the reader may see. CRM views, CRM search and workspace export keep returning only tasks of the workspace (tasks with no workspace never appear there). |
| FR-TASK-VIS-05 | Audit entries for tasks MUST NOT appear in the TVA audit page or the TVA `audit_log` tool (as CRM rows, `007` FR-CRM-AUD-04). A CRM task's entries appear in its workspace audit as today. Wherever a task's history is shown (record timeline, workspace audit, task panel), entries written while the task had no workspace MUST be shown only to its creator and its assignee at that time, never to workspace members or project viewers who gained sight later. |
| FR-TASK-VIS-06 | A test MUST prove that a private task and a shared personal task cannot be fetched, listed, counted, searched, exported or audit-read by any other account — a TVA admin, a manager, the owner, the creator's TVA lead, a CRM workspace Admin, a Viewer, a `crm_only` account, a former assignee after reassignment or hand-back, a person named in Waiting on, and a removed workspace member — through every task route, every CRM route, every TVA route and every MCP tool (T4; replaces the source's "RLS test"). It MUST also prove that a removed workspace member cannot read the workspace's CRM tasks they created or were assigned; that a task whose parent is private shows no parent id or title; and that the private-era history of a task later linked to a CRM record is not shown to workspace members. The test builds its route list from the FastAPI app and its tool list from the MCP registry, and fails on any route or tool that has no fixture. |
| FR-TASK-VIS-07 | **CRM links win.** A task with a CRM link is seen exactly by the workspace's active members (§3.1), whatever its project link, so no lead, manager or admin outside the workspace sees it (`007` FR-CRM-WS-02, FR-CRM-TVA-02 unchanged). On such a task, a member who cannot see the linked project (a TVA `user` not allocated to it now or before, a `crm_only` account) receives no project link at all: no id, no name. On a project task with no CRM link, a creator or assignee who cannot see the project sees the project's name only. A `crm_only` account never sees a project task that has no CRM link, unless it is that task's creator or assignee. |
| FR-TASK-VIS-08 | Before a link or assignee is saved, the task panel and the quick-add preview MUST show who will see the task: "Visible to: only you", "you and *Name*", "leads, managers, admins and people allocated to project *X*", or "*Workspace* members". |

### 5.3 My Tasks — FR-TASK-LIST

| ID | Requirement |
|---|---|
| FR-TASK-LIST-01 | **My Tasks** shows the caller's open and waiting tasks (assigned to them) in four sections: Today, This week, This month, Later; within a section, by due date (none last), then priority, then title. Done and cancelled tasks are collapsed at the bottom, newest first. |
| FR-TASK-LIST-02 | **Effective bucket** (TP-06). A task due today or earlier MUST show in Today whatever its stored bucket. Nothing is rewritten at night: there is no roll-over job. "Today" is the caller's date in their time zone (FR-TASK-CHG-06). |
| FR-TASK-LIST-03 | An overdue task MUST show "N days overdue", computed when read, by the rule of the CRM's Task overdue flag (`007` FR-CRM-FLAG). |
| FR-TASK-LIST-04 | Moving a task between sections is one drag or one tap and changes the bucket only. Changing the bucket never changes the due date. |
| FR-TASK-LIST-05 | Completing a task is one tap; it can be undone (back to Open) from the done list. |
| FR-TASK-LIST-06 | A task opens in a side panel with every field, its links (each opening its record or project, if the caller may see it), its parent and subtasks, who can see it (FR-TASK-VIS-08), and the task's history, subject to FR-TASK-VIS-05 (TP-16). |
| FR-TASK-LIST-07 | My Tasks MUST filter by tag, priority, link and text (TP-15). |
| FR-TASK-LIST-08 | My Tasks is one page reachable from the TVA sidebar and from the CRM navigation, for staff and `crm_only` accounts alike (T6). It reads and writes only through `/api/proxy`. |
| FR-TASK-LIST-09 | The CRM Tasks page, saved views (the seeded "Open tasks by owner" and "My week", and every view people made), record task tabs and Today keep working on the shared table (§8.2). |

### 5.4 Capture — FR-TASK-ADD

| ID | Requirement |
|---|---|
| FR-TASK-ADD-01 | A **quick-add box** MUST be in the header of every portal page and every CRM page, for everyone with phase 1 switched on. |
| FR-TASK-ADD-02 | Quick add MUST parse the line with fixed rules, not AI (T7), and MUST show the parsed result (title, due date with weekday, bucket, tags, assignee, who can see it) before saving. Saving is Enter or a Save button; nothing is saved while the preview shows a question. |
| FR-TASK-ADD-03 | Rules: `#word` is a tag; `@name` is the assignee, matched against the display names and emails of people the caller may assign; dates are `today`, `tomorrow`, a weekday name or its three-letter form (the next such day after today), `next week` (next Monday), `YYYY-MM-DD`, `D Mon` or `D Month`, and `D/M` (day first, as in India); the matched words are removed from the title. The bucket follows FR-TASK-DATA-08, except This week when the due date is after today and within the **current week, Monday to Sunday in the caller's time zone**. |
| FR-TASK-ADD-04 | The preview MUST ask instead of guessing (T7) when: a weekday name is today's weekday ("today or next Friday?"); `next <weekday>` is used; a date has already passed this year; `@name` matches no one or several people. For `D/M`: if both parts are 12 or less **and differ**, it MUST ask ("5 Nov or 11 May?"); it reads the date without asking only when one part is above 12 (the only valid reading) or the two parts are equal. |
| FR-TASK-ADD-05 | **From a record**: "Add task" on a CRM record (as today) and *(phase 2)* on a TVA project page pre-links the record. |
| FR-TASK-ADD-06 | A new task MUST appear in the creator's open My Tasks page without a reload, within two seconds (an optimistic update on that page). For anyone else, including the assignee, it shows on their next load of My Tasks; there is no live push or polling. |

### 5.5 Claude — FR-TASK-MCP

| ID | Requirement |
|---|---|
| FR-TASK-MCP-01 | The NLDW MCP server MUST offer, in phase 1: `task_add` (title, notes, bucket, due date, priority, tags, assignee by name or email, links by record or project id, parent), `task_list` (filters: section, status, tag, link, assigned to me or by me), `task_update` and `task_complete`. Phase 2 adds `task_brief`; phase 4 adds `inbox_add`. |
| FR-TASK-MCP-02 | Each tool acts as the token's owner through the task routes and the visibility function (FR-TASK-VIS-01), and has no access of its own. Every write says CONFIRM FIRST. Each description opens with a "who may call" line, as the `crm_*` tools do. |
| FR-TASK-MCP-03 | `task_*` tools work for `crm_only` accounts (T6). They return to them the CRM tasks of their workspaces without any project field, and no project task that has no CRM link unless they created it or are its assignee (FR-TASK-VIS-07). |
| FR-TASK-MCP-04 | `crm_add_task`, `crm_complete_task`, `crm_get`, `crm_list`, `crm_create`, `crm_update` and `crm_today` keep working on the same rows with the same rules. `008` adds no `crm_*` tool and renames none; the total number of `crm_*` tools is asserted only by `009`, which adds three. |
| FR-TASK-MCP-05 | The MCP instructions MUST describe tasks: the kinds, who sees what, the statuses and buckets, and that tasks never carry hours. |

### 5.6 Life OS import — FR-TASK-IMP

| ID | Requirement |
|---|---|
| FR-TASK-IMP-01 | The owner MUST be able to upload a CSV export of the Life OS Tasks tab (at most 4 MB, through the proxy) and run it as a dry run and then a real run, the pattern of the CRM import (`007` FR-CRM-MIG-04: the real run is offered only after a dry run of the same file; T8). Only the owner (`profiles.is_owner`) may run it, and it is web-only, with no tool (TP-10). |
| FR-TASK-IMP-02 | Columns: Task → title; Status → status; Priority → priority; Owner → assignee; Due Date → due date; Tags → tags (comma separated); Notes / Update → notes; Completed Date → completed at (source §10). Dates are read as `YYYY-MM-DD`, `D/M/YYYY` (day first), `D Mon YYYY` or `D Month YYYY`. A row with status waiting is imported as Waiting with Waiting on set to the free text "Life OS", because the sheet has no waiting-on column; the dry run reports how many rows this applies to. |
| FR-TASK-IMP-03 | The dry run MUST report counts (rows, open, waiting, done, cancelled, skipped), the proposed owner mapping, every owner value it cannot map, every status or priority value it cannot map, every `D/M/YYYY` date whose day and month are both 12 or less and differ (read day first, listed so the owner can check them), rows that already exist from an earlier run (skipped), and rows repeated within the file (the second is skipped). It writes nothing but its run record. |
| FR-TASK-IMP-04 | Owners are mapped explicitly: each distinct Owner value in the sheet (a blank cell counts as one value, "(blank)") maps to the email of an existing active account; no account is created (T8). The dry run proposes the mapping by a case-insensitive match on full display name, else on a first name unique among active accounts; more than one match, or none, leaves the value unmapped. The owner confirms or edits the mapping (so Vinitha in the sheet can map to Vinita's account). The mapping is shown in the dry run and stored with the run record. The real run MUST be refused while any owner, status or priority value is unmapped, when the uploaded file is not the same file (by hash) as the last dry run, or more than 24 hours after that dry run (TP-17). |
| FR-TASK-IMP-05 | Each row becomes one task with source Import and a source reference `lifeos:` followed by a hash of the mapped owner's account id, the normalised title (trimmed, lower-case, single spaces) and the due date. A second real run of the same file MUST change nothing. A real run of an edited file creates only rows whose key is new; a row whose title, owner or due date was edited counts as new, and the earlier task stays. |
| FR-TASK-IMP-06 | Each imported task is created by the uploader and assigned to its row's mapped owner (source §10, Owner → assignee). The uploader's own rows are private; rows of other owners are shared personal tasks, seen by the uploader and that owner. The audit records the uploader with actor kind `import`. Imported tasks are never CRM tasks, send no notification and never become day-log suggestions (TP-19; FR-TASK-LOG-01). |
| FR-TASK-IMP-07 | A done row without a Completed Date gets the import time as completed at, because the database requires completed at on every done task (FR-TASK-DATA-03); an open row gets its bucket by FR-TASK-DATA-08. |
| FR-TASK-IMP-08 | The CSV is parsed in memory and never stored. The run record keeps counts, the owner mapping and the file hash only, never file content, and is audited. The real run is a re-upload of the file, checked against the hash. |

### 5.7 Phase switches — FR-TASK-ROLL

| ID | Requirement |
|---|---|
| FR-TASK-ROLL-01 | Each phase MUST be switched on per person (C6), so the owner can switch it on for himself first, then the core team, then everyone. The switch is a cumulative level 0–4 (level *n* switches on phases 1 to *n*), with a global default that starts at 0 and is set through `update_setting` (TP-11). |
| FR-TASK-ROLL-02 | Only a TVA admin MAY switch a phase on or off for a person, from the Admin People page and the existing `update_user` tool. Changes are audited. |
| FR-TASK-ROLL-03 | The switch MUST be enforced in the backend: a task route or tool of a phase not switched on for the caller answers 403 "Tasks are not switched on for you yet." The web app hides what is not switched on. |
| FR-TASK-ROLL-04 | Assigning a task with no workspace to someone without phase 1 switched on MUST be refused 422 "*Name* does not have My Tasks yet." |
| FR-TASK-ROLL-05 | The data migration of §8 is not switched: it applies to everyone at once, and every CRM task behaviour keeps working for everyone, including people with no phase switched on. |

---

## 6. Functional requirements — phases 2 to 4

### 6.1 Delegation and waiting — FR-TASK-DEL *(phase 2)*

| ID | Requirement |
|---|---|
| FR-TASK-DEL-01 | Assigning a task to someone else MUST send them one message through the portal's existing channel (`notify.deliver`, Slack DM and email) (TQ-12). On a CRM task the existing in-app bell notice (`007` FR-CRM-NOTIF-01) is kept too. A person's own assignment to themselves sends nothing. |
| FR-TASK-DEL-02 | A task assigned by someone else shows as **New** in the assignee's My Tasks until they accept it, comment on it, change it or complete it. |
| FR-TASK-DEL-03 | The assignee MAY comment, complete, or **hand it back** with a note: the assignee becomes the creator again and the note is kept as a comment. |
| FR-TASK-DEL-04 | Comments are short texts on a task (at most 2,000 characters), visible to whoever may see the task, audited, never edited, deletable by their author. |
| FR-TASK-DEL-05 | The creator MUST be told, through the same channel, when someone else completes or hands back their task. |
| FR-TASK-DEL-06 | Setting status Waiting MUST require Waiting on. The person named in Waiting on MUST be someone the caller may assign (as `@name` in FR-TASK-ADD-03), and gains no sight of the task (§3.1). A waiting task untouched for N days (default 5, each person may change it, 1–30) MUST appear in the creator's morning brief as "nudge?". |
| FR-TASK-DEL-07 | Messages carry only ids in the job queue; the text is built when sent (as today's notification jobs). Each is sent once (a sent marker per task and event). |
| FR-TASK-DEL-08 | The CRM's due and overdue reminders (automation 7) stay as they are, for CRM tasks only. No new reminder is sent for other tasks; the morning brief carries them. |

### 6.2 Views — FR-TASK-VIEW *(phase 2)*

| ID | Requirement |
|---|---|
| FR-TASK-VIEW-01 | **Waiting on**: the caller's waiting tasks grouped by whom they wait on, each with its age in days. |
| FR-TASK-VIEW-02 | **Assigned by me**: tasks the caller created for someone else, with assignee and status. |
| FR-TASK-VIEW-03 | **On a record**: the CRM record task tab (`007`, already built) uses the shared task panel. A TVA project page MUST gain a task panel listing the project's tasks that the viewer may see (§3.1), with "Add task". Only leads, managers and admins open project pages today, and this does not change; a TVA `user` reaches a project task only through My Tasks, the task panel and the `task_*` tools, as its creator or assignee or as someone who is or was allocated to its project (TP-01). |

### 6.3 Morning brief — FR-TASK-BRIEF *(phase 2)*

| ID | Requirement |
|---|---|
| FR-TASK-BRIEF-01 | One screen: the caller's Today and overdue tasks; waiting items due a nudge; and, for each CRM workspace they belong to, that workspace's CRM Today sections (`007` FR-CRM-TODAY-01), read from the existing Today service, not recomputed. |
| FR-TASK-BRIEF-02 | `task_brief` returns the same content. |
| FR-TASK-BRIEF-03 | The brief is a page and a tool, not a pushed message (TP-08). The CRM daily digest at 09:00 (`007` FR-CRM-NOTIF-03) is unchanged. |

### 6.4 Day log suggestions — FR-TASK-LOG *(phase 3)*

| ID | Requirement |
|---|---|
| FR-TASK-LOG-01 | When a person opens Log a day, the day's read MUST include **suggestions**: tasks assigned to them, status Done, completed on that date in Asia/Kolkata (the day log's calendar, TQ-16), each with title and, when linked to a confirmed, unarchived TVA project, that project (a tentative or archived project takes no new time entries today: `backend/app/services/timesheets.py`, `002` FR-PROJ-04). Imported tasks (source Import) are never suggested (TP-19). |
| FR-TASK-LOG-02 | No suggestions for `crm_only` accounts (they cannot log time; T6) or anyone without phase 3 switched on. |
| FR-TASK-LOG-03 | Suggestions MUST NOT be offered when the day cannot be logged (future, or past the edit window); the reason the day cannot be logged is shown as today. |
| FR-TASK-LOG-04 | **Accept** adds the task to the day being edited and writes nothing: a project task adds or merges into that project's line (adding the project to the day's choices if it is not allocated); a task with no project asks the person to pick a project or activity. The title is appended to the line's note (keeping the 500-character limit). Hours start at zero. Saving the day is the only write, through the existing Save day, with every existing rule. A line still at zero hours is not saved. |
| FR-TASK-LOG-05 | **Ignore** hides a suggestion on this screen only. A suggestion whose title already appears in a note that day shows as added. No record of accepting or ignoring is stored (TP-09). |
| FR-TASK-LOG-06 | Log a day MUST warn that a note is visible to the person's lead, managers and admins (TP-20). |
| FR-TASK-LOG-07 | Suggestions MUST NOT appear in anyone else's view of a week (`someone_elses_week`, team weeks, sign-off). `my_day` returns them; `log_day` stays the only write. |
| FR-TASK-LOG-08 | A task never shows hours, cost or day-log data to anyone. |

### 6.5 Recurring tasks — FR-TASK-RRULE *(phase 3)*

| ID | Requirement |
|---|---|
| FR-TASK-RRULE-01 | A task MAY carry a recurrence (an iCalendar RRULE: daily, weekly on given days, monthly on a day, yearly), entered by picking from those choices, and MUST have a due date. |
| FR-TASK-RRULE-02 | Completing or cancelling a recurring task MUST create the next occurrence: same fields and links, the next due date after the current one, bucket by FR-TASK-DATA-08, status Open (source §9; cancelling, TP-18). |
| FR-TASK-RRULE-03 | The next occurrence is created exactly once per occurrence: reopening and completing again does not create another. |
| FR-TASK-RRULE-04 | Removing the recurrence from an occurrence ends the series. |
| FR-TASK-RRULE-05 | Starter templates on My Tasks settings, each added only when the person chooses it: monthly invoice follow-ups, weekly pipeline review, statutory dates, monthly payroll check. Statutory dates need the owner's list (§15). |
| FR-TASK-RRULE-06 | Recurrence is computed with `python-dateutil`, made a direct dependency (T9). |

### 6.6 Voice capture — FR-TASK-VOICE *(phase 4)*

| ID | Requirement |
|---|---|
| FR-TASK-VOICE-01 | A person MAY record or upload a voice note (at most 4 MB, multipart, through the proxy). |
| FR-TASK-VOICE-02 | The audio is held in a private Supabase Storage bucket only until transcribed, and deleted at once after, and in any case within 24 hours by a sweep job, including when transcription fails. A worker transcribes it with Groq (whisper-large-v3) using a key held as a Railway secret (T10); the key never reaches the browser and is never logged. |
| FR-TASK-VOICE-03 | The transcript MUST be split by fixed rules, not AI (TP-22): into sentences, and further at "and" / "also" joins; a candidate starting "idea" or "read", or containing a link, is an inbox item, and every other candidate is a task candidate. Each is parsed by the quick-add rules. |
| FR-TASK-VOICE-04 | Candidates are shown for one-tap confirm, edit or drop. Nothing is saved before confirmation. Saved tasks have source Voice. |
| FR-TASK-VOICE-05 | The transcript is held only in the caller's voice upload row (no other table, no Redis, no Celery result: the worker writes the row and returns nothing), and is deleted when the person confirms or drops the last candidate, or within 24 hours by the same sweep job, whichever is first. It never appears in logs. |

### 6.7 Ideas and Reading inbox — FR-TASK-INBOX *(phase 4)*

| ID | Requirement |
|---|---|
| FR-TASK-INBOX-01 | An inbox item has an owner, a kind (idea, link, note), title, optional URL, notes and tags, created at, processed at, and the task it became. It is always private to its owner, admins included. |
| FR-TASK-INBOX-02 | The Inbox page lists unprocessed items with **Make it a task** (creates a task, records it and marks the item processed) and **Archive**. |
| FR-TASK-INBOX-03 | `inbox_add` adds an item (CONFIRM FIRST). |

### 6.8 Weekly review — FR-TASK-REVIEW *(phase 4)*

| ID | Requirement |
|---|---|
| FR-TASK-REVIEW-01 | On Mondays, My Tasks MUST open with a review panel: This week and This month items, waiting items older than 7 days, and unprocessed inbox items, each with one-tap bucket moves. It can be dismissed for the week. Shown in the app, not sent (TP-08). |

---

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-TASK-01 | A task appears in the creator's My Tasks within seconds (source §12; FR-TASK-ADD-06). My Tasks loads in under 1 second for a person with 5,000 visible tasks, and quick add saves in under 1 second (TP-21). |
| NFR-TASK-02 | Privacy is enforced in the backend (FR-TASK-VIS-01) and proven by FR-TASK-VIS-06. |
| NFR-TASK-03 | Integrity: renaming the tables and mapping statuses is one migration in one transaction, preceded by the zero-link clean-up of §8.2; a task and its links are written atomically, and the workspace check is deferred to the end of the transaction (FR-TASK-DATA-05). Schema changes land as numbered files in `supabase/migrations/`. |
| NFR-TASK-04 | Observability: every task route and job logs as the portal does, in structured JSON, without task titles, notes or transcripts; refusals are RFC 7807. |
| NFR-TASK-05 | Tone: team-facing copy frames tasks as helping each other and growing, never monitoring. The words "Employee" and "compliance" do not appear in task UI copy. |
| NFR-TASK-06 | Accessibility: keyboard usable (quick add has a shortcut), visible focus, colour never the only signal (overdue says how many days). |
| NFR-TASK-07 | Dependencies: `python-dateutil` (phase 3), justified in the plan; the Groq call uses the existing HTTP client pattern with no vendor SDK (phase 4). Nothing else. |
| NFR-TASK-08 | Tests: every rule in §5 and §6 has a test; buckets, overdue, recurrence and suggestions are tested with fixed clocks; the existing CRM task tests are kept and updated only where §8 changes behaviour. |

---

## 8. Changes this forces on existing behaviour

### 8.1 Amendments to other specs — FR-TASK-CHG

Each amends the spec named. Everything else stands. `007` FR-CRM-WS-02,
FR-CRM-WS-05 and FR-CRM-TVA-02 are **not** amended: CRM tasks stay inside their
workspace (FR-TASK-VIS-07) and only Labs tasks link TVA projects
(FR-TASK-DATA-06).

| ID | Requirement |
|---|---|
| FR-TASK-CHG-01 | **`007` FR-CRM-TASK-01 and §5.9 amended.** Statuses become Open, Waiting, Done, Cancelled; "Body" becomes "Notes"; "Priority bucket" becomes "Bucket" and is required on open tasks; Priority, Tags, Parent, Waiting on, Source are added; a Labs task may link a TVA project. "A task with no linked record MUST be refused" becomes: **a task created through the CRM (its routes, `crm_add_task`, bulk "Add task", the importer, the deal lifecycle) MUST link at least one record of the workspace — a parameter of the CRM writer, not a table constraint — and no task may lose its last CRM link (FR-TASK-DATA-05); a task that never had a CRM link is a personal or project task (`008`)** and is not a CRM record. The assignee of a CRM task stays a workspace member. |
| FR-TASK-CHG-02 | **`007` FR-CRM-CHG-01 amended.** The shared allow-list gains the task routes (`/api/v1/tasks/`) by prefix, and the route-enumerating test's allow-list constant changes visibly. A `crm_only` caller there sees no TVA data (FR-TASK-VIS-07). Day-log suggestions stay on the TVA timesheet routes and remain refused to them. |
| FR-TASK-CHG-03 | **`004` FR-MCP-01 amended again**: "…except CRM routes, which are covered by the `crm_*` tools of `007` FR-CRM-MCP-01, **and task routes, which are covered by the `task_*` and `inbox_add` tools of `008` FR-TASK-MCP-01 (the Life OS import is web-only, TP-10)**." The coverage test excludes `/api/v1/tasks/*` by prefix, keeps the substring it asserts today, and a separate test checks exactly the `task_*` and `inbox_add` tools of the phases built. |
| FR-TASK-CHG-04 | **`007` FR-CRM-PIPE-09 and automation 9 amended** (TP-07). Closed Lost sets the deal's Open and Waiting tasks to Cancelled, not Done (with the note as today), so they stop counting in Dashboard Activity and never become day-log suggestions. Tasks closed this way before the migration stay Done. |
| FR-TASK-CHG-05 | **`007` FR-CRM-TODAY-01 amended.** Section 3 reads "my open or waiting tasks in my Today bucket, or due today or earlier". CRM Today stays workspace-scoped and shows only that workspace's tasks; tasks with no workspace appear in My Tasks and the morning brief, never in CRM Today. |
| FR-TASK-CHG-06 | **`007` FR-CRM-CHG-12 amended.** The per-person time zone also decides "today" and the current week in My Tasks and quick add, the overdue count and the morning brief. Day-log suggestions use Asia/Kolkata, as every TVA rule (`001` NFR-03). |
| FR-TASK-CHG-07 | **`007` FR-CRM-AUD-04 widened.** Task audit rows, with or without a workspace, are excluded from the TVA audit page and `audit_log` tool (FR-TASK-VIS-05). Record timelines MUST read task history under both the old and new table names, so no history is lost by the rename. |
| FR-TASK-CHG-08 | **`002` §2.2 and `003` Q-03 / FR-TIME-11 amended** *(phase 3)*. Task tracking is no longer left to ClickUp: tasks live in the portal. The day log still records effort against a project phase, and the note stays free text; it is no longer "the only task record". The day-log page header comment and the `log_day` description change to match. |
| FR-TASK-CHG-09 | **`007` FR-CRM-MIG (Twenty importer) amended.** The Twenty importer maps To do and In progress to Open. |
| FR-TASK-CHG-10 | **`007` CRM task status inputs and output.** `crm_*` tools and CRM routes return the new status keys and labels. `GET /crm/tasks` with `status=open` means Open or Waiting, and the route also accepts `waiting` and `cancelled`. The status inputs are `crm_create`, `crm_update` and `crm_list` (task fields and filters) and `GET /crm/tasks`; `crm_add_task` has no status parameter. Those inputs also accept the old keys `todo` and `in_progress` and read them as Open, so existing prompts keep working (TP-05). |
| FR-TASK-CHG-11 | **`007` FR-CRM-LIST-02 and FR-CRM-LIST-09 amended.** The task status list stays a fixed list with system keys, and its set becomes `open`, `waiting`, `done`, `cancelled`. The `todo` and `in_progress` options are deactivated and kept for history. This is a stated exception to "fixed options are never deactivated"; "options are never deleted" still holds. A label an Admin gave to Done survives; Open, Waiting and Cancelled start with those labels; labels given to To do or In progress are not carried over. Tasks with no workspace take status, bucket and priority labels from fixed labels in code, since they have no option list. |

### 8.2 What the migration touches

The plan owns the list; these are known from the code and each MUST be changed
or proven unaffected:

- **Database functions that name the CRM task tables or test "not done"**: the generic CRM writer (refuses non-`crm_` table names; requires a workspace; hard-codes "a task needs a link", which becomes a writer parameter); the reference check (unknown record type; same-workspace rule); deal transition (closes tasks, creates handoff tasks); purge (also purges tasks left with no CRM link, FR-TASK-DATA-14); company merge; flag facts (Stale deal and Loose end read open tasks); view query and rank arrays; search; flag setting.
- **Seeded data**: the task status select list in both workspaces (FR-TASK-CHG-11); the "Open tasks by owner" view's filter (becomes Open or Waiting); the field registry for views.
- **Backend**: the CRM store's table lists and filters (Today tasks, reminders, flags, open-task lists, record tasks sorted open first, Closed Lost open tasks), task create and update (default status, completed at, refusing removal of the last link), the task schemas and status literals, `GET /crm/tasks` status values (FR-TASK-CHG-10), the Twenty importer, present/timeline (labels for tasks with no workspace), digests, dashboard activity, search and merge.
- **Frontend**: task types, the task dialog (reopen goes to Open, not To do), Tasks page, task tab, Today list, view field labels.
- **Tests**: the CRM tests that pin statuses and table names, and the fake database that re-implements the writer, purge, flags and deal transition.
- **Before the rename**: every task that is not deleted, has a workspace and has no link (possible today, because `crm_purge` deletes links but leaves their tasks) is moved to its workspace Trash (soft delete, audited with the migration as actor), and the count is reported before production apply.
- **Existing data**: To do and In progress → Open (T2), and every migrated In progress task gains the tag "in progress" (TP-04); a missing bucket is filled by FR-TASK-DATA-08; existing rows get source Manual (or Import when they have a Twenty id), with no source reference.
- **Saved views**: every `crm_saved_views` config filter on task status is rewritten — `todo` and `in_progress` become `open`; "status is not Done" becomes "status is none of Done, Cancelled" — in seeded views, edited copies and views people made; favourites follow their views.

---

## 9. Where the source conflicts with the code, and how it is resolved

| Source said | The code today | Resolved by |
|---|---|---|
| §3 rename to `tasks`, workspace empty for personal tasks | The CRM writer only writes `crm_*` tables with a workspace; the audit trigger needs an actor; purge can leave a workspace task with no link | T1; a task write path that sets the actor and accepts no workspace (TQ-02); FR-TASK-DATA-05, -14; §8.2 clean-up |
| §3 statuses open/waiting/done/cancelled | todo/in_progress/done in a check, a fixed select list, schemas, importer, UI, saved views; "open" tested as "not done" in about a dozen places | T2; FR-TASK-DATA-04; FR-TASK-CHG-11; §8.2 |
| §3 one `linked_type`/`linked_id` | Many links per task (up to 20); merge can leave several; links carry a composite workspace foreign key | T3: the link table is kept and shared, plus `tva_project`; FR-TASK-DATA-15 |
| §3, §12 visibility in RLS, RLS test | Service role bypasses RLS; no browser grant on any table since `010`; no policies | T4: one backend function; an enumerating backend test (FR-TASK-VIS-06) |
| §2 "visible when linked to a shared record" | Every workspace member, Viewers included, sees every CRM task; no project read for a TVA `user` | T5: kept; stated in §3.1; CRM links win over a project link (FR-TASK-VIS-07) |
| §3 assignee defaults to creator, anyone | CRM assignee must be a workspace member | FR-TASK-DATA-07: kept for CRM tasks; free for personal ones |
| §3 priority beside bucket | The bucket is labelled "Priority bucket" | TQ-09: relabel to Bucket; Priority is new |
| §4 nightly job moves tasks to today | CRM Today already shows due and overdue tasks whatever the bucket; a nightly write would audit every task and make open edits stale | TP-06: effective bucket computed on read; no job |
| §4 overdue marker | The Task overdue flag already exists (`007` FR-CRM-FLAG) | FR-TASK-LIST-03 uses the same rule, computed on read |
| §4 owner's time zone | Per-person zone is CRM display only | FR-TASK-CHG-06 |
| §5.4 replaces the CRM task panel | Already built for companies, people, deals, campaigns | FR-TASK-VIEW-03: reused; TVA project panel is new |
| §5.6 morning brief | CRM daily digest already sends Today at 09:00 | TP-08: a page and a tool, no second message |
| §6 `task_*` tools | `crm_add_task`, `crm_complete_task` exist; coverage test counts non-`crm_` tools against routes | FR-TASK-MCP-04; FR-TASK-CHG-03 |
| §6 voice upload | Proxy carries multipart up to 4 MB | FR-TASK-VOICE-01 |
| §7 notify "through the nudge channel" | CRM sends a bell-only "assigned" notice; `notify.deliver` sends Slack and email | TQ-12: both for CRM tasks; message only for others |
| §7 reminders | Automation 7 reminds CRM tasks | FR-TASK-DEL-08: unchanged, no second reminder |
| §8 accept creates a day-log entry | Save day replaces the whole day; one line per project | FR-TASK-LOG-04: accept edits the draft; merge per project |
| §8 "allocation stays correct" | Time entries never change allocations | The project link sets the line's project (effort and phase), nothing else |
| §8 crm-only users | They cannot log time | T6; FR-TASK-LOG-02 |
| §8, `002`, `003` | "Task tracking stays in ClickUp" | FR-TASK-CHG-08 |
| §10 "same pattern as the CRM import"; owners by name | CRM import runs need a workspace; re-runs rely on Twenty ids; the admin's account is named Vinita, the sheet says Vinitha | FR-TASK-IMP-04: explicit owner mapping; FR-TASK-IMP-05: content key; its own run record |
| §11 no kanban | "My week" is a seeded kanban by bucket | TQ-15: the existing view stays; no new board |
| §12 "CRM tools return the same results" | Status keys change | FR-TASK-CHG-10: same tasks; new status keys; old keys still accepted as input (TP-05) |
| §13 feature flags | No flag mechanism exists | C6: per-person switch (FR-TASK-ROLL) |
| Constitution, The Journeys | A personal task list and inbox are not in the four journeys | §15: constitution amendment before phase 1 ships |

---

## 10. Key entities and indicative changes

Logical; the plan chooses columns, types and indexes, except names fixed by a
decision and the constraints required by FR-TASK-DATA-05 and FR-TASK-DATA-15.

```text
Renamed and widened (phase 1)
  crm_tasks       -> tasks: workspace nullable; deferred end-of-transaction
                     check: workspace empty iff no CRM link (live tasks only);
                     status open|waiting|done|cancelled, + priority, tags,
                     parent, waiting on (person or text), source, source
                     reference (unique for lifeos: keys); body kept (labelled
                     Notes); bucket required when open; indexes for assignee
                     and creator without a workspace
  crm_task_links  -> shared task links: plain FK task_id -> tasks(id) ON
                     DELETE CASCADE on every row; + record type tva_project
                     with FK to projects(id) and no workspace; composite
                     (workspace, task) FK and same-workspace check kept for
                     CRM record rows only

New
  task import run (phase 1): uploader, file hash, owner mapping, status, counts
  per-person phase switch (phase 1): cumulative level 0–4, with a global
    default set through update_setting (TP-11)
  task comment (phase 2); task notification sent-marker (phase 2)
  recurrence on tasks + series link (phase 3)
  inbox item (phase 4); voice upload row holding the transcript, with a
    private Storage bucket for audio (phase 4)

Changed
  CRM writer / reference check / deal transition / purge / merge /
    flag facts / query / search   -> new table names; open = open|waiting;
                                     "needs a link" a writer parameter;
                                     purge removes tasks left linkless
  select lists task.status        -> open, waiting, done, cancelled;
                                     todo, in_progress deactivated
  saved views                      status filters rewritten (§8.2)
  day read (/timesheet/day)        + suggestions (phase 3)
  audit readers                    task rows out of TVA audit; timelines read
                                   both names; private-era rows withheld

Routes and tools
  /api/v1/tasks/*  (any account, phase-checked; on the shared allow-list)
  task_add, task_list, task_update, task_complete; task_brief (2); inbox_add (4)
  update_user carries the per-person phase switch
```

Screens: My Tasks (new), quick-add box (both shells), task side panel (shared
with the CRM task tab), Life OS import (owner), Admin People phase switch;
phase 2 Waiting on, Assigned by me, morning brief, TVA project task panel;
phase 3 suggestions on Log a day, recurrence picker; phase 4 voice, Inbox,
weekly review panel.

---

## 11. Decisions

Settled 18 October 2026. The owner answered the questions put to him (T1–T10,
C6, O1) with "use recommended option for all the questions". The TQ items below
only make one of those decisions concrete or record a constraint the code
forces. The TP items were proposed to the owner afterwards and all accepted as
written on 18 October 2026.

| ID | Question | Source said | Decision | Changed |
|---|---|---|---|---|
| **T1** | Where do personal tasks live? | Migrate CRM tasks into `tasks` | **Rename `crm_tasks` to `tasks`, keeping ids.** Workspace empty only when there is no CRM link; a CRM-linked task carries that record's workspace, checked by the database. | Made concrete |
| **T2** | Statuses | open, waiting, done, cancelled | **Those four.** To do and In progress both become Open. | Mapping added |
| **T3** | Links | One `linked_type`/`linked_id` | **Several links per task**; the CRM link table becomes the shared one; `tva_project` added. | Changed |
| **T4** | Privacy enforcement | Supabase RLS, RLS test | **Portal pattern**: RLS on, no policy, no browser grant; one backend visibility function for every route and tool; a test that no one else, admins included, can fetch a private task through any route or tool. | Changed: backend, not RLS |
| **T5** | Shared visibility | Visible when linked to a shared record | **CRM-linked: every workspace member** (as today). **Project-linked: whoever can see the project.** | Made concrete |
| **T6** | CRM-only people | Unstated | **They use My Tasks; no day-log suggestions.** | Added |
| **T7** | Quick-add parsing | "Light parsing" | **Fixed rules, no AI; preview before save; asks on ambiguous dates.** | Made concrete |
| **T8** | Life OS import | Sheet import, dry run then real run | **Owner uploads a CSV export; dry run and real run as the CRM import; owners must already have accounts; unmatched owners reported.** | Transport fixed |
| **T9** | Recurrence engine | rrule | **`python-dateutil` allowed** (phase 3). | Dependency approved |
| **T10** | Voice | Groq whisper | **Groq key as a Railway secret; phase 4.** | Unchanged |
| **C6** | Rollout | Feature flag per phase | **Per person**: owner first, then core team, then everyone. | Mechanism added |
| **O1** | Build order | — | **008 before 009; one phase at a time, starting with phase 1.** | Added |
| **TQ-01** | Table names | `tasks` | `tasks` (T1); links table renamed to the shared task links name chosen in the plan. | — |
| **TQ-02** | How are tasks written? | Unstated | **One task write path** (a database function) that sets the actor, writes the audit, accepts no workspace and enforces FR-TASK-DATA-05; the CRM writer keeps writing CRM tasks with its link rule. Forced by the CRM writer and audit trigger (§1). The plan picks extending the CRM writer or a sibling. | Forced by code |
| **TQ-08** | Notes vs body | `notes` | The existing field, relabelled Notes. | Reuse |
| **TQ-09** | Priority vs bucket | Both | Bucket relabelled "Bucket"; Priority new. | Relabel |
| **TQ-12** | Assignment notice | Nudge channel | `notify.deliver` message for all; CRM bell kept for CRM tasks. | Made concrete |
| **TQ-15** | "No boards" vs My week kanban | No boards | Existing CRM view kept; no new board. | Clarified |
| **TQ-16** | Day-log date | Unstated | Asia/Kolkata, as the day log. | Forced by code |
| **T5 / review** | CRM link and project link on one task | Unstated | CRM link wins: workspace members only; project field withheld from members who cannot see projects (FR-TASK-VIS-07). Keeps `007` FR-CRM-WS-02 and FR-CRM-TVA-02. | Applies T5 |
| **TQ-17** | Priority and Source in CRM views | Unstated | Both become CRM view fields (filter, sort, column) on tasks; data-model §3.3. Owner took the recommended option, 18 Oct 2026. | Added |
| **TP-01** | Project tasks for a TVA `user` | "Whoever can see the project" | A TVA `user` also sees tasks of projects they are or were allocated to, and may link those projects (§3.1). | Owner accepted 18 Oct 2026 |
| **TP-02** | Changing a project task seen only through the project rule | Unstated | Read only (§3.3). | Owner accepted 18 Oct 2026 |
| **TP-03** | `crm_only` assignee on a project task | Anyone | A `crm_only` account cannot be assigned a project task with no CRM link (FR-TASK-DATA-07). | Owner accepted 18 Oct 2026 |
| **TP-04** | The In progress distinction | Collapse to open (T2) | Migrated In progress tasks become Open and are tagged "in progress" (§8.2). | Owner accepted 18 Oct 2026 |
| **TP-05** | Old CRM status keys as input | Same results | `crm_create`, `crm_update`, `crm_list` and `GET /crm/tasks` also accept `todo` and `in_progress` as Open (FR-TASK-CHG-10). | Owner accepted 18 Oct 2026 |
| **TP-06** | Moving tasks to Today | Nightly job in the owner's zone | No nightly job: a task due today or earlier shows in Today when read; no row is rewritten (FR-TASK-LIST-02). | Owner accepted 18 Oct 2026 |
| **TP-07** | Tasks of a deal closed Lost | Unstated | Set to Cancelled, not Done, so they stop counting in Dashboard Activity and never become day-log suggestions. Amends `007` FR-CRM-PIPE-09 and automation 9 (FR-TASK-CHG-04). | Owner accepted 18 Oct 2026 |
| **TP-08** | Morning brief and weekly review delivery | A brief "Claude can deliver" | A page and a tool only; no new pushed message (FR-TASK-BRIEF-03, FR-TASK-REVIEW-01). | Owner accepted 18 Oct 2026 |
| **TP-09** | Accepted and ignored suggestions | Unstated | Nothing stored: a suggestion whose title is already in a note that day shows as added; ignore lasts for the screen only (FR-TASK-LOG-05). | Owner accepted 18 Oct 2026 |
| **TP-10** | Who runs the Life OS import | The owner | Only the owner (`profiles.is_owner`); web-only, with no tool (FR-TASK-IMP-01, FR-TASK-CHG-03). | Owner accepted 18 Oct 2026 |
| **TP-11** | Form of the per-person switch | Feature flag | A cumulative level 0–4 (level *n* switches on phases 1 to *n*), with a global default that starts at 0 and is set through `update_setting` (FR-TASK-ROLL-01). | Owner accepted 18 Oct 2026 |
| **TP-12** | Keeping deleted tasks with no workspace | Unstated | Kept 90 days, then removed by the daily purge job (FR-TASK-DATA-11). | Owner accepted 18 Oct 2026 |
| **TP-13** | Project links per task | Unstated (T3 allows several links) | At most one TVA project per task (Links field, FR-TASK-LOG-01/-04). | Owner accepted 18 Oct 2026 |
| **TP-14** | Tag limits | Free tags | At most 40 characters each, at most 10 per task (Tags field). | Owner accepted 18 Oct 2026 |
| **TP-15** | My Tasks filters | Unstated | Tag, priority, link and text (FR-TASK-LIST-07). | Owner accepted 18 Oct 2026 |
| **TP-16** | History in the task panel | Unstated | Shown, subject to FR-TASK-VIS-05 (FR-TASK-LIST-06). | Owner accepted 18 Oct 2026 |
| **TP-17** | Guards on the Life OS real run | Unstated | Refused while a status or priority value is unmapped, and allowed only within 24 hours of the dry run (FR-TASK-IMP-04). | Owner accepted 18 Oct 2026 |
| **TP-18** | Cancelling a recurring task | "When completed" | Also creates the next occurrence (FR-TASK-RRULE-02). | Owner accepted 18 Oct 2026 |
| **TP-19** | Imported tasks and the day log | Unstated | Never day-log suggestions (done rows without a date get the import time, which would otherwise surface them on import day) (FR-TASK-IMP-06, FR-TASK-LOG-01). | Owner accepted 18 Oct 2026 |
| **TP-20** | Note visibility on Log a day | Unstated | Log a day warns that a note is visible to the person's lead, managers and admins (FR-TASK-LOG-06). | Owner accepted 18 Oct 2026 |
| **TP-21** | Speed targets | "Within seconds" | My Tasks loads in under 1 second for a person with 5,000 visible tasks; quick add saves in under 1 second (NFR-TASK-01). | Owner accepted 18 Oct 2026 |
| **TP-22** | Splitting a voice note | "Splits it into candidate tasks and inbox items" | Fixed rules, not AI: sentences and "and" / "also" joins; a candidate starting "idea", "read" or containing a link is an inbox item (FR-TASK-VOICE-03, phase 4). | Owner accepted 18 Oct 2026 |

---

## 12. Acceptance criteria

Fixed clock for capture examples: Tuesday 20 October 2026, Asia/Kolkata.

Phase 1 — migration and CRM
- [ ] Every CRM task exists in `tasks` with the same id, links, title, notes, dates, assignee and history; To do and In progress tasks are Open, and former In progress tasks carry the tag "in progress".
- [ ] Live workspace tasks with no link before the migration are in their workspace Trash, and their count was reported.
- [ ] Every CRM task test passes, updated only where §8 changes behaviour: Today, flags (Task overdue, Stale deal, Loose end), reminders, record tabs, views, search, merge, trash, purge, Twenty import, export, deal transition, handoff tasks.
- [ ] A cancelled task counts as closed everywhere open is tested (Today, flags, reminders, views).
- [ ] Closing a deal Lost sets its open and waiting tasks to Cancelled (FR-TASK-CHG-04); they do not count in Dashboard Activity; tasks closed this way before the migration stay Done.
- [ ] `crm_add_task`, `crm_complete_task`, `crm_get`, `crm_list`, `crm_create`, `crm_update` and `crm_today` return the same tasks before and after; `GET /crm/tasks?status=open` returns Open and Waiting tasks; `todo` and `in_progress` given as a status input to `crm_create`, `crm_update`, `crm_list` or `GET /crm/tasks` are read as Open.
- [ ] The task status list in each workspace holds open, waiting, done, cancelled as active options, with todo and in_progress deactivated; a renamed Done label survives.
- [ ] No saved view, favourite or seeded view references `todo` or `in_progress`, and no task-status filter "is not Done" remains.
- [ ] The database refuses, at commit, a live task whose workspace does not match its CRM links, and a live task with a workspace and no CRM link.
- [ ] The CRM writer refuses a task created through the CRM with no link; removing a task's last CRM link is refused through CRM routes, task routes and `task_update`.
- [ ] Purging a record purges tasks left with no CRM link; none becomes personal.
- [ ] Deleting a task cascades its link rows, including `tva_project` rows and rows of tasks with no workspace.
- [ ] An Academy task cannot link a TVA project (422); linking a project the caller cannot see answers 404; linking an archived project is refused; a second project link on a task is refused; a TVA `user` can link a project they are or were allocated to.
- [ ] Record timelines show task history written before the rename.

Phase 1 — privacy
- [ ] FR-TASK-VIS-06 passes for every route and tool, and fails when a route or tool is added without a fixture.
- [ ] A CRM task is visible to every active workspace member and to no non-member, including its creator or assignee after removal from the workspace, and including leads, managers and admins outside the workspace when it links a project.
- [ ] A `crm_only` Labs member sees a deal task that also links a TVA project, and the response has no project field.
- [ ] A project task with no CRM link is visible to every lead, manager and admin, every TVA `user` who is or was allocated to the project, its creator and its assignee, and to no other TVA `user` or `crm_only` account; a creator or assignee who cannot see the project sees only the project's name.
- [ ] Someone who sees a project task only through the project rule can read it and cannot change it.
- [ ] A Viewer assigned a CRM task can change its status only, through both the task routes and `task_update`.
- [ ] A Viewer, or a TVA user in no workspace, cannot add a CRM link to their personal task (404 or 403 by `007` rules).
- [ ] A child task whose parent is private shows no parent id or title; setting a parent the caller cannot see answers 404.
- [ ] A private task later linked to a CRM record shows its earlier history only to its creator and assignee.
- [ ] The task panel and the quick-add preview show who will see the task.
- [ ] No task audit entry appears on the TVA audit page or through `audit_log`.
- [ ] No task title, note or transcript appears in the logs of a test run that writes tasks.

Phase 1 — My Tasks, capture, tools, import, switches
- [ ] A task added by quick add, `task_add` or from a record appears in the creator's open My Tasks within two seconds, in the right section; the assignee sees it on next load.
- [ ] Quick add at the fixed clock: "fri" → Friday 23 October, This week; "sun" → Sunday 25 October, This week; "mon" → Monday 26 October, Later; "tue" → asks "today or next Tuesday?"; "5/11" → asks "5 Nov or 11 May?"; "13/11" → 13 November; "11/11" → 11 November; "1/3" → asks; a date already passed this year → asks.
- [ ] A task due today or overdue shows in Today whatever its stored bucket, with days overdue; no job rewrites buckets.
- [ ] Completing a task sets completed at; reopening clears it.
- [ ] A `crm_only` account can use My Tasks and `task_*` tools and is refused every TVA route as before.
- [ ] A `crm_only` account cannot be assigned a project task with no CRM link, and can be assigned a CRM task of its workspace.
- [ ] Assigning a task with no workspace to someone without phase 1 is refused 422 with their name.
- [ ] A deleted task with no workspace can be restored by its creator, and not by its assignee; the daily purge job removes it 90 days after deletion.
- [ ] A tag over 40 characters, or an eleventh tag on a task, is refused.
- [ ] My Tasks filters by tag, priority, link and text.
- [ ] The task panel shows the task's history, withholding entries by FR-TASK-VIS-05.
- [ ] My Tasks loads in under 1 second for a person with 5,000 visible tasks; quick add saves in under 1 second.
- [ ] Life OS dry run and real run report and create matching counts; an unmapped owner, status or priority value blocks the real run; a real run more than 24 hours after its dry run is refused; a second real run of the same file changes nothing; a real run of an edited file creates only the new or edited rows; a real run of a file other than the last dry run is refused.
- [ ] Imported rows of other owners are created by the uploader and assigned to that owner; Waiting rows carry Waiting on "Life OS".
- [ ] Only the owner can run the Life OS import; no MCP tool runs it.
- [ ] The import run record holds counts, owner mapping and file hash, and no file content.
- [ ] A person with phase 1 off gets 403 from every task route and tool; CRM tasks work for them as before.
- [ ] Switching a person's phase is audited; a person's level *n* switches on phases 1 to *n*, and the global default starts at 0 and is set through `update_setting`.

Phase 2
- [ ] Assigning to someone else sends exactly one message; completing or handing back tells the creator once.
- [ ] Waiting needs Waiting on, limited to people the caller may assign; the person named gains no sight; a waiting task idle for the set days shows "nudge?" in the brief.
- [ ] The brief shows the CRM Today sections exactly as CRM Today does, and is never pushed as a message.
- [ ] A TVA project page shows the tasks of that project that the viewer may see.

Phase 3
- [ ] Tasks completed today appear as suggestions on Log a day for staff with phase 3 switched on; none for `crm_only`, none on a locked or future day.
- [ ] Accepting two tasks on one project gives one line; saving with hours writes one normal entry through Save day; nothing is written without Save.
- [ ] Suggestions never appear in someone else's week view; imported tasks are never suggested; a suggestion whose title is already in that day's note shows as added; nothing about accepting or ignoring is stored.
- [ ] Log a day warns that a note is visible to the person's lead, managers and admins.
- [ ] Completing or cancelling a recurring task creates exactly one next occurrence with the right due date and bucket.

Phase 4
- [ ] A voice note becomes candidates shown for confirmation, split by the fixed rules of FR-TASK-VOICE-03; nothing is saved before; the audio is gone after transcription.
- [ ] The sweep job deletes the audio and transcript of a failed transcription within 24 hours.
- [ ] The transcript is deleted when the last candidate is confirmed or dropped, and is never in the Celery result backend or logs.
- [ ] Inbox items are visible only to their owner; Make it a task links the two.
- [ ] The Monday review panel shows the listed items and can be dismissed.

---

## 13. Out of scope

Kanban boards (beyond the existing CRM "My week" view), Gantt charts, time
estimates, story points; subtasks deeper than one level; two-way sync with Google
Sheets, ClickUp or Apple Notes; an Apple Notes integration; productivity scores,
leaderboards or per-person task counts on team-facing pages (beyond the existing
CRM dashboard Activity panel, §2.2); hours or cost on tasks; AI parsing in quick
add; due-date reminders for non-CRM tasks; live push of new tasks to other
people's pages; anything in `009-crm-intelligence` except the task fields it
depends on (source Email commitment and Meeting action, source reference).

---

## 14. What `009` relies on

- A task-creation call, through the task write path and visibility rules, that sets source Email commitment or Meeting action, a source reference, CRM links and an assignee who is a workspace member.
- Tasks created from the Review Queue are CRM tasks of that workspace and behave as any other.
- The creating actor may carry the id of the approved suggestion; it reaches the task's audit rows, so the task's history shows it came from an approved AI suggestion (owner decision, 18 October 2026).

---

## 15. Open items

### 15.1 Owner actions

- **Constitution.** Personal tasks and the inbox do not trace to the four journeys (Employee, Lead, Admin, Seller), and §I literally says each table "MUST have its RLS policy defined", while the practice since `010` (and T4) is RLS on with no policy. Recommended: a MINOR amendment before phase 1 ships that adds task keeping to the Employee's and Seller's journeys and words the RLS rule as practised.
- **Life OS export** — the owner exports the Tasks tab as CSV and confirms the column headers match §5.6 (T8).
- **Accounts** — Navaneeth, Timothy and Vinita must have active portal accounts before the real run (T8).
- **Archive the sheet** — the owner sets the Life OS sheet to read-only after the real run (source §10; T8).
- **Core team list** — the owner names who gets each phase after him (C6).
- **Statutory dates** — the owner supplies the GST and statutory dates for the starter template (phase 3).
- **Groq API key** — the owner creates it and sets it as a Railway secret on the worker (phase 4, T10).
- **Production SMTP sender** — still missing (`007` §14); until it exists, `crm_only` people get no assignment message outside the app (phase 2).

### 15.2 Proposals

All proposals TP-01 to TP-22 were accepted by the owner as written on 18 October 2026; they now live in §11.

---

## 16. What this must not become

- **Not a monitoring tool.** Nobody sees another person's private tasks, and no page counts tasks per person beyond the existing CRM dashboard Activity panel, which counts workspace CRM tasks only.
- **Not a timesheet.** A task never carries hours; the day log stays the only record of time, written only when the person saves.
- **Not a second CRM.** CRM tasks keep the CRM's rules and stay inside their workspace; a personal task never enters a workspace by accident, and a CRM task never leaves one.
- **Not ClickUp rebuilt.** No boards, estimates or deep hierarchies.
