# Source: TVA Tasks, one task system for the whole portal, v0.1 (18 Oct 2026)

> Kept verbatim as the input to `spec.md`. Where `spec.md` and this file
> disagree, `spec.md` wins: it records the owner's decisions of 18 Oct 2026.

# TVA Tasks: one task system for the whole portal

Version 0.1, 18 Oct 2026. Owner: Navaneeth. Builder: Claude Code (spec-driven).
Stack assumption: existing TVA (Supabase, Railway workers, Netlify front end, NLDW MCP). Read the existing CRM task table, the day log (log_day) model and the NLDW tool definitions before writing code. Extend what exists; do not create a second task table.

Companion spec: TVA_CRM_Intelligence_Layer_Spec.md. Its Review Queue items of type task_from_commitment and task_from_meeting create tasks in the table defined here.

## 1. Goal

Replace the scattered task lists (Life OS Google Sheet, Apple Notes, CRM tasks, ClickUp) with one task system inside TVA. A task is written once and appears wherever it is relevant: the owner's list, the linked deal or company, the linked TVA project, and the CRM Today view.

Success looks like: Navaneeth stops using the Life OS sheet and Apple Notes for tasks within two weeks of launch, and team members' completed tasks feed their daily time logs.

## 2. Principles

1. **One table.** CRM tasks migrate into the shared `tasks` table. Existing CRM task tools and UI keep working on top of it.
2. **Private by default.** A task with no link and no assignee other than its creator is visible only to its creator. It becomes visible to others only when assigned to someone, or linked to a shared record (deal, company, person, TVA project). Admins cannot browse other people's private tasks.
3. **Capture must be instant.** Adding a task takes one line of text from anywhere; everything else is optional.
4. **Light structure.** No boards, no Gantt, no time estimates, subtasks one level deep at most.
5. **Tone.** Team-facing copy frames tasks around helping each other and growth, never monitoring. Avoid the words "Employee" and "compliance" in UI copy.

## 3. Data model

Table `tasks` (migrate the CRM task table into it, keeping ids):

| Field | Type | Notes |
|---|---|---|
| id | uuid | |
| title | text | required |
| notes | text | optional, markdown |
| status | enum | open, waiting, done, cancelled |
| bucket | enum | today, this_week, this_month, later |
| due_on | date | optional |
| priority | enum | urgent, high, normal, low (default normal) |
| created_by | user | |
| assignee | user | defaults to created_by |
| waiting_on | text or person/user ref | set when status = waiting |
| tags | text[] | free tags |
| parent_id | task | one level only |
| linked_type / linked_id | enum + id | none, company, person, opportunity, campaign, tva_project |
| workspace_id | | null for personal tasks; set when linked to a CRM record |
| recurrence | rrule text | optional |
| source | enum | manual, nldw, voice, email_commitment, meeting_action, rule, import |
| source_ref | text | thread id, transcript id, rule id |
| completed_at | timestamptz | set automatically on done (mirrors the Life OS "Completed Date" rule) |
| created_at, updated_at | | optimistic locking via updated_at |

Visibility is computed from created_by, assignee and the link, and enforced in Supabase row-level security, not only in the UI.

Table `inbox_items` (Ideas and Reading, kept separate from tasks): id, owner, kind (idea, link, note), title, url, notes, tags, created_at, processed_at, converted_task_id.

## 4. Buckets and dates

- Every open task has a bucket. Default on quick add: today if a due date is today or past, otherwise later.
- Nightly job (owner's time zone): tasks with due_on today or earlier move to today; tasks in this_week roll to today on their due date. Overdue tasks show an overdue marker with days overdue.
- Moving between buckets is drag or one tap; changing the bucket never changes the due date unless the person sets one.
- Weekly review prompt (Monday morning): shows this_week and this_month items, waiting items older than 7 days, and unprocessed inbox items.

## 5. Views

1. **My Tasks**: Today, This week, This month, Later sections; open tasks sorted by due date then priority; done tasks collapsed at the bottom, newest first.
2. **Waiting on**: tasks with status waiting, grouped by who they are waiting on, with age. This is the delegation tracker.
3. **Assigned by me**: tasks I created for others, with their status.
4. **On a record**: deal, company, person and TVA project pages show their linked tasks (replaces the current CRM task panel).
5. **Inbox**: Ideas and Reading items, with "Make it a task" and "Archive".
6. **Morning brief**: one screen combining today's and overdue tasks, waiting-on items due a nudge, and the CRM Today sections. Same content available via NLDW so Claude can deliver it.

## 6. Capture

- **Quick add** box in the TVA header on every page, with light parsing: "Call Deepak re RDX pilot fri #sales @Timothy" sets title, due date (Friday), tag and assignee. Show the parsed result before saving; never guess silently on ambiguous dates.
- **From a record**: "Add task" on any CRM record or TVA project pre-links it.
- **From Claude (NLDW)**: tools `task_add`, `task_list`, `task_update`, `task_complete`, `task_brief`, `inbox_add`. `task_add` accepts natural fields (title, bucket, due_on, assignee by name or email, link by record id).
- **Voice**: upload or record a voice note in the portal; a worker transcribes it (Groq whisper-large-v3, key held server side), splits it into candidate tasks and inbox items, and shows them for one-tap confirmation before anything is saved.
- **From the CRM intelligence layer**: approved Review Queue items create tasks with source email_commitment or meeting_action and the link to the record.

## 7. Delegation and waiting

- Assigning a task to someone else notifies them through the channel TVA already uses for nudges.
- The assignee can accept, comment, mark done, or hand it back with a note. The creator is notified on done.
- Setting status waiting requires a waiting_on value. A waiting task untouched for 5 days (configurable) appears in the creator's morning brief as "nudge?".

## 8. Day log integration

- When a person opens their daily time log, tasks they completed that day appear as suggested entries (title, linked TVA project if any). They accept, edit the hours, or ignore. Nothing is logged automatically.
- Tasks linked to a TVA project carry that project into the suggestion so allocation stays correct.
- Tasks never expose hours or cost to other team members.

## 9. Recurring tasks

- Recurrence stored as an rrule. When a recurring task is completed, the next occurrence is created with the next due date in the right bucket.
- Starter templates in Settings (each optional): monthly invoice follow-ups, weekly pipeline review, GST and statutory dates, monthly payroll check.

## 10. Migration

- One-time import of the Life OS Google Sheet (Tasks tab). Column mapping: Task to title, Status to status, Priority to priority, Owner to assignee (Navaneeth, Timothy, Vinitha by name), Due Date to due_on, Tags to tags, Notes / Update to notes, Completed Date to completed_at.
- Dry run first showing counts and unmatched owners, then the real run (same pattern as the existing CRM import).
- After import, the sheet is archived (read-only); no ongoing sync.
- Apple Notes content is moved by hand or pasted into the voice/quick-add flow; no Apple Notes integration is built.
- Existing CRM tasks migrate in place into `tasks` with their links preserved.

## 11. Out of scope

- Kanban boards, Gantt charts, time estimates, story points.
- Subtasks deeper than one level.
- Two-way sync with Google Sheets, ClickUp or Apple Notes.
- Team productivity scoring or leaderboards of any kind.

## 12. Acceptance criteria

- A task added via quick add, NLDW or voice appears in My Tasks within seconds, in the right bucket.
- A private task is invisible to every other user, including Admins, verified by a row-level security test.
- A task linked to a deal shows on the deal, in the owner's list and in CRM Today, and completing it in one place completes it everywhere.
- Completing a task sets completed_at automatically.
- Completed tasks appear as suggestions in that day's time log; accepting one creates a normal day log entry.
- Life OS import dry run and real run match counts, and re-running the import changes nothing.
- Existing CRM task tools return the same results before and after the migration.

## 13. Phasing

| Phase | Scope |
|---|---|
| 1 | Shared tasks table and CRM migration, My Tasks with buckets, quick add, NLDW task tools, privacy rules, Life OS import |
| 2 | Delegation, Waiting on view, morning brief, record task panels |
| 3 | Day log suggestions, recurring tasks |
| 4 | Voice capture, Ideas and Reading inbox, weekly review prompt |

Each phase behind a feature flag; enable for Navaneeth first, then the core team, then everyone.
