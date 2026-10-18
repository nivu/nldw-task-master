---
title: Your tasks
summary: My Tasks, buckets, quick add and its questions, who sees what, CRM and project tasks, handing work over, Waiting on, Assigned by me, the morning brief, suggestions on Log a day, recurring tasks and starter templates, the Life OS import and Claude's task tools.
order: 11
---

# Your tasks

The portal keeps one task list for everyone. A task is written once and shows
wherever it belongs: in your own list, on the CRM record it is about, and in
CRM Today. Tick it done anywhere and it is done everywhere.

Tasks are switched on person by person. If you do not see **My Tasks** yet,
it has not been switched on for you; a portal admin can do that.

## My Tasks

Open **My Tasks** from the sidebar, or from the CRM's navigation if you use
the CRM. It is the same page in both places, and it works the same for people
who use only the CRM.

Your open and waiting tasks are in four sections:

| Section | For |
|---|---|
| **Today** | what you mean to do today |
| **This week** | the rest of this week, Monday to Sunday |
| **This month** | later this month |
| **Later** | everything else |

The section a task sits in is its **bucket**. Within a section, tasks are
ordered by due date (tasks with no date last), then priority, then title.

- **Due today or earlier?** A task due today or overdue always shows under
  Today, whatever its bucket, and says how many days overdue it is. Nothing
  moves your tasks overnight; the list simply reads the date.
- **Move a task** by dragging it to another section, or with the section menu
  on its row. That changes the bucket only, never the due date.
- **Complete a task** with one tap. Done and cancelled tasks are folded away
  at the bottom, newest first; **Undo** opens one again.
- **Filter** by tag, priority, a linked record or project, or words in the
  title and notes.

"Today" and "this week" are your own, in the time zone set under
**Account**.

## A task, opened

Click a task to open it in a side panel. It shows every field — title, notes,
status, bucket, due date, priority, tags, who it is assigned to — its links,
its parent and subtasks, **who can see it**, and its history.

| Field | Notes |
|---|---|
| **Status** | Open, Waiting, Done or Cancelled. Waiting needs to say who or what you are waiting on. Cancelled is not done: it never counts as finished work |
| **Bucket** | Today, This week, This month or Later. When you give none, it is Today if the task is due today or earlier, otherwise Later |
| **Priority** | Urgent, High, Normal (the default) or Low |
| **Tags** | free words, up to ten, each up to 40 characters |
| **Parent** | a task may sit under another task, one level deep |

## Quick add

Every page has a quick-add box at the top: in the sidebar on a computer, in
the header on a phone, and in the CRM's header. Press **q** anywhere outside a
text field to jump to it. Type one line, check what it understood, and press
**Enter**.

| Type | Means |
|---|---|
| `#word` | a tag |
| `@name` | assign it to someone, by name or email |
| `today`, `tomorrow` | that day |
| a weekday, or its first three letters (`fri`) | the next such day after today |
| `next week` | next Monday |
| `2026-11-05`, `5 Nov`, `5 November` | that date |
| `5/11` | day first, as in India |

The words it understood are taken out of the title. Before you save, the box
shows the title, the date with its weekday, the bucket, the tags, the
assignee and who will see the task.

**It asks instead of guessing.** Save stays off until you answer when:

- you type today's weekday ("today or next Friday?");
- you type `next` with a weekday;
- the date has already passed this year;
- `@name` matches nobody, or more than one person;
- a date like `5/11` could be read either way ("5 Nov or 11 May?"). It does
  not ask when only one reading is a real date, such as `13/11`, or when both
  parts are the same, such as `11/11`.

A task you add appears straight away in your open My Tasks page. Someone you
assign it to sees it the next time they open My Tasks.

## Who sees what

What a task is depends on what it is linked to, and that decides who can see
it. Before you save a link or an assignee, the panel and quick add say who
will see the task.

| Task | Linked to | Who sees it |
|---|---|---|
| **Private** | nothing, assigned to you | only you |
| **Shared** | nothing, assigned to someone else | you and that person |
| **CRM** | a company, person, deal or campaign | every member of that CRM workspace, and nobody else |
| **Project** | a TVA project, with no CRM link | leads, managers and admins, people who are or were allocated to the project, and the task's creator and assignee |

Being an admin gives no extra sight. Nobody — admins, managers or your lead
included — can find, list, search, export or read the history of your
private or shared tasks. Naming someone as the person you are waiting on
does not show them the task, and when a task is given to someone else, the
person it was assigned to before no longer sees it.

## CRM tasks and project tasks

A **CRM task** is a task linked to a CRM record. It is the same task you see
on the record's Tasks tab and in CRM Today, and it follows the CRM's roles:
Members and Admins change it, and a Viewer may only change the status of a
task assigned to them. If you leave a workspace, you stop seeing its tasks,
including ones you made. A CRM task always keeps at least one CRM link; to
get rid of one, delete it instead.

Linking a personal task to a CRM record makes it a CRM task of that workspace
for good. You can link only records of a workspace where you are a Member or
Admin.

A **project task** is linked to a TVA project. You can link a project you
can see: any project if you are a lead, manager or admin, or one you are or
were allocated to. Only a Nunnari Labs CRM task may also link a project, and
an archived project cannot be newly linked. Someone who sees a project task
only because they can see the project may read it but not change it.

People who use only the CRM see their CRM tasks in My Tasks, and never see a
project task unless they made it or it is assigned to them.

## Handing work over

These parts come with the second stage of tasks; if you do not see them yet,
it has not been switched on for you.

- **Giving a task to someone** sends them one message, by Slack and email. The
  task shows **New** in their list until they accept it, comment on it,
  change it or complete it.
- **Accept** takes the task up. **Hand back**, with a short note, gives it
  back to the person who made it; the note is kept on the task as a comment.
- When someone else completes or hands back a task you made, you are told
  once. You are never told about something you did yourself.
- **Comments** on a task are read by everyone who can see the task, and only
  their author can delete one.
- **Waiting** names who or what you are waiting on. A person you name must be
  someone you could give the task to, and naming them does not show them the
  task.

## Waiting on, Assigned by me and the brief

My Tasks has four views across the top:

| View | Shows |
|---|---|
| **Mine** | your tasks by section, as above |
| **Waiting on** | your waiting tasks, grouped by whom or what they wait on, each with how many days old it is |
| **Assigned by me** | tasks you made for someone else, with who has them and their status |
| **Brief** | your morning brief |

The **brief** is one screen: your tasks for today, your overdue tasks, the
waiting tasks you made that nobody has touched for a while ("Nudge?"), and,
for each CRM workspace you belong to, that workspace's CRM Today exactly as
the CRM shows it. "A while" is five days unless you change it (1 to 30 days,
on the Mine view). The brief is there when you open it; it is never sent to
you as a message. The CRM's 09:00 digest is unchanged.

A **TVA project's page** (Analytics, then the project) lists the project's
tasks you may see, with **Add task**, which links the new task to that
project. On a **CRM record**, a task in the Tasks tab opens in the same task
panel as My Tasks.

## Suggestions on Log a day

These parts come with the third stage of tasks; if you do not see them yet,
it has not been switched on for you. People who use only the CRM do not log
time, so they never see suggestions.

When you open **Log a day**, the tasks assigned to you that you completed
that day (India time) are offered as **suggestions**, each with its project
when it is linked to a confirmed, unarchived project. Tasks brought in from
the Life OS sheet are never suggested, and a day you cannot log (a future
day, or one past the edit window) has none.

- **Add** puts the task on the day you are editing and saves nothing: a
  project task joins that project's line, a task with no project asks you to
  pick a project or activity, and its title is added to the line's note
  (500 characters at most). Hours start at zero; a line still at zero hours
  is not saved. **Save day** is the only thing that writes, with every
  usual rule.
- **Ignore** hides a suggestion on this screen only. A suggestion whose title
  is already in a note that day shows as added. Nothing about adding or
  ignoring is kept.
- Notes are seen by your lead, managers and admins; Log a day says so.
  Suggestions never appear in anyone else's view of your week.

## Recurring tasks

Also part of the third stage. In a task's panel, **Repeats** picks how a task
comes back:

| Choice | Means |
|---|---|
| **Daily** | every day |
| **Weekly on chosen days** | the days you tick, such as Monday and Thursday |
| **Monthly on a day** | that day each month; a shorter month uses its last day (the 31st falls on 30 April and 28 February) |
| **Yearly on the due date** | that day and month each year; 29 February falls on the 28th outside a leap year |

A recurring task needs a due date. When you **complete or cancel** it, its
next occurrence appears: the same title, notes, priority, tags, assignee and
links, due on the next date after this one, Open, in the bucket its due date
gives it. Each occurrence makes its next one only once: reopening it and
completing it again makes no other. To stop a series, set **Repeats** to
**Does not repeat** on the latest occurrence.

**Starter templates.** Under the Mine view's settings, each template adds
one recurring task to your own list, only when you choose it:

| Template | Repeats |
|---|---|
| Follow up unpaid invoices | every month on the 5th |
| Review the sales pipeline | every week on Monday |
| Check this month's payroll | every month on the 25th |
| GST and statutory dates | not available until the owner supplies the list of dates |

## Moving from the Life OS sheet

The portal owner can bring the tasks over from the Life OS sheet, at
`/tasks/import` in the portal:

1. Export the sheet's Tasks tab as CSV and upload it. This is a **dry run**:
   it changes nothing, and shows how many rows it would bring in, the people
   each sheet owner will map to, and any status, priority or owner it cannot
   read.
2. Check the owner mapping and change any line that is wrong. A status or
   priority it cannot read must be fixed in the sheet: fix it, export again
   and run the dry run again.
3. **Run the import** with the same file, within a day of the dry run.

Each row becomes one task. Your own rows are private; rows of other people
are assigned to them and shared with you. Rows marked waiting wait on "Life
OS". Running the import again changes nothing; a file with new or edited rows
brings in only those. The file itself is never stored.

## Asking Claude

If you have connected Claude (see **Connecting Claude**), these tools keep
your list:

| Tool | Does |
|---|---|
| `task_list` | your tasks by section, with days overdue; done and cancelled ones when asked; or the tasks you gave others |
| `task_add` | adds a task, with notes, bucket, due date, priority, tags, an assignee by name or email, links and a parent |
| `task_update` | changes a task's fields |
| `task_complete` | marks a task done |
| `task_brief` | your morning brief, the same as the Brief view |

Claude tells you what it will change and waits for your yes before it adds or
changes anything. It sees exactly what you see. The Life OS import is in the
web app only.

Tasks never carry hours. Your day log stays the only record of time.
