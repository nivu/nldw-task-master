---
title: The sales CRM
summary: For sellers — signing in, Today, records, the pipeline and what each stage needs, Drive links, AI summaries and markers, and Claude.
order: 9
---

# The sales CRM

The CRM is where sales work lives: the companies and people you sell to, the
deals you are working, your follow-ups, and the notes and files that go with
them. It sits beside the rest of the portal, under **/crm**, with its own
sidebar.

There are two workspaces, **Nunnari Labs** and **Nunnari Academy B2B**. What
is in one never shows in the other. If you belong to both, the switcher at
the top of the sidebar moves between them, and the portal remembers the one
you used last.

## Signing in

Sign in with Google, as for the rest of the portal, with the address you
were invited with. An invitation comes from a workspace admin; there is no
way to sign yourself up.

- **If you only use the CRM**, you land on the CRM straight away and see
  nothing else of the portal.
- **If you also use the portal for leave and time**, a **CRM** link appears
  in your sidebar once you belong to a workspace, and **Back to TVA** takes
  you home again.

Your time zone and how amounts are written (lakhs and crores, or in full) are
under **Account** in the CRM sidebar. They change only what you see.

## Your role

Each workspace gives you one role:

- **Admin** — everything below, plus inviting people, settings and lists.
- **Member** — adds and edits companies, people, deals, tasks and notes,
  moves deals through the pipeline, and deletes the records they own.
- **Viewer** — reads every record, keeps their own private views, and
  completes tasks assigned to them. A Viewer sees **no amounts** anywhere: no
  deal values, no totals, no budgets, and cannot filter or sort by them.

## Today

**Today** is the first page, and the one to start the day with. It lists, in
this order, what needs you:

1. **Replies I owe** — people waiting for an answer from you.
2. **Deal next steps** — overdue first, then the ones due today.
3. **Tasks for today** — overdue, due today, and anything you put in Today.
4. **Clients gone silent** — open deals where the other side has not answered.
5. **Loose ends** — nurturing companies with nothing planned.
6. **To review** — how many AI suggestions are waiting for you, and the
   three oldest. It shows only while your workspace uses the AI features or
   you have something waiting (see *Review AI suggestions*).

Each line opens its record. The two email sections say **Email sync is off**
until your workspace's Gmail sync is switched on. "Today" is your own day, in
your time zone.

## Records

| Record | What it is |
|---|---|
| **Company** | An organisation you sell to, with its lead stage, owner and domains |
| **Person** | Someone at a company, with their emails and phone numbers |
| **Opportunity** (deal) | One piece of business with one company, moving through the pipeline |
| **Task** | Something to do, linked to at least one company, person or deal |
| **Note** | Anything written down; a note can also **log an activity** (a call, a meeting, a WhatsApp) on the day it happened |

A record's page shows its fields at the top, then its timeline (every change,
note and email, newest first), its tasks, notes, Drive links and related
records. Edit a field by clicking it.

**Lists.** Companies, People, Opportunities and Tasks open as a table or a
board, with saved views as tabs along the top. Filter, sort and group, then
save what you built as your own view, or (for Admins and Members) as one the
workspace shares. Tick rows to act on several at once.

**Search.** Press **Ctrl+K** (or **Cmd+K** on a Mac) anywhere in the CRM to
find any record by name, email or domain, and to start a new one.

**Duplicates.** When you add a company or a person that looks like one
already there, the CRM shows it first. Two companies that turn out to be the
same can be **merged**: everything on one moves to the other, and the other
goes to Trash.

**Trash.** A deleted record waits in Trash for 90 days, where an Admin can
restore it.

## The pipeline, and what each stage needs

A company climbs its **lead stage** (New, Contacted, Nurturing, Qualified)
before it takes a deal. A deal can be opened only on a company at **Qualified**
or later, and opening one moves the company to **Deal open**.

Every open deal always has a **next step and its date**. That is the deal's
follow-up; there is no separate reminder to set.

| Stage | To enter it, the deal needs |
|---|---|
| **Discovery** | a company, an owner, a next step and its date |
| **Qualification** | a point of contact |
| **Proposal** | an amount, its currency and an expected close date |
| **Negotiation** | the date the proposal was sent |
| **Closed Won** | in Nunnari Labs, the handoff (below) |
| **Closed Lost** | a lost reason |

You can skip stages: the move asks for everything the stages you skip would
have needed. When something is missing, the message names every missing
field at once. Moving a deal sets its probability to the stage's default
unless you set one yourself.

**Drag a card** on the Pipeline board to move it. The card moves at once; if
the move needs more, a form opens, and if it is refused, the card goes back
and says why. On a keyboard: focus the card, press **Space**, choose a column
with the arrow keys, and press **Space** again (or **Escape** to cancel).

**Closed Lost** closes the deal's open tasks with a note. When it was the
company's last open deal, choose whether the company goes back to
**Nurturing** or to **Disqualified**.

**Reopening.** A lost deal can be reopened by its owner or an Admin, back to
the stage it was lost from, with a new next step. A won deal can be reopened
by an Admin only.

**Amounts.** Deals are in INR, AUD or USD. A non-INR amount is also stored in
INR at the month's rate, which an Admin sets; if the month has no rate yet,
the save says so.

### In Nunnari Labs: the TVA project

A Labs deal is tied to a project in TVA, so delivery can plan people for it:

- At **Proposal** a **tentative** project is created with the deal's name and
  probability. If a project already has that name, the move stops and offers
  to rename, or (for TVA managers) to link the existing one.
- At **Closed Won** the **handoff** asks for the project's lead and its phase
  dates, and confirms the project. A TVA manager or admin also enters the
  revenue and milestones; for anyone else, the Labs admins get a task to
  confirm them.
- At **Closed Lost** the project is archived and the people allocated to it
  are freed. Reopening the deal brings both back.

Academy deals never create TVA projects.

## Drive links

Files stay in Google Drive. On any record, **Add a Drive link** takes an
`https://` address and a label; the CRM keeps the link, never the file.
Whoever opens the link needs access to it in Drive.

## AI summaries and the AI marker

Your workspace's Admins can switch on AI features one phase at a time, so
you may see some of these and not others. Wherever something was written by
AI, it carries a small **AI** marker. Hover over it, or reach it with the
Tab key, to see where it came from (an email thread, a meeting, the
company's website, an AI field or a follow-up rule) and when it was made.

AI never changes a record by itself. Anything it suggests changing, such as
a deal's next step or a new task, waits for a person to approve it (see
*Review AI suggestions*).

- **Email summaries.** On a company, person or deal, a labelled email thread
  has a **Summary** button. It opens a few plain sentences about the thread,
  **Their asks**, **Our commitments** and the tone of the thread (positive,
  neutral or a concern). The email itself is never kept; only the summary.
  A new message on the thread replaces the summary.
- **Meeting notes.** A client call recorded in Fireflies can arrive as a
  meeting note on the company (and the deal, when there is just one): its
  title, who attended, a short summary, decisions, action items and a link to
  the recording. You can edit it like any note; once you do, AI no longer
  updates it.
- **From the website.** A company page can show details read from the
  company's own website: industry, size, location, a one-line description,
  LinkedIn and what they offer. They sit apart from the company's own fields.
  Admins and Members can **Promote** a value into the field it belongs to;
  that is an ordinary edit, made by you. Where the field already holds
  something different, a suggestion goes to the Review Queue instead.
- **AI fields.** Companies and deals can show a few AI fields, such as an
  account brief or a deal outlook with its reason. They are read-only, appear
  on the record page only, and never change a stage, a probability, a flag or
  a colour. Admins and Members can press **Recompute** for a fresh value.
- **Contact recency.** Companies and people show how recently we were in
  touch: **Recent** (emails both ways, or a meeting note, in the last 30
  days), **Fading** (some contact in the last 90 days) or **Lapsed**. It is
  worked out from email and meeting notes, not by AI, and never changes the
  company's Relationship status. You can filter company and person lists by
  it. While email sync is off it says **Meetings only**.
- **Stuck in stage.** If your workspace uses it, an open deal that has spent
  too long in its stage gets a Stuck in stage flag, amber and then red, like
  the other follow-up flags. You are told once when it first turns amber.

AI text never contains amounts, so Viewers see summaries, meeting notes and
AI fields like everyone else.

## Using the CRM from Claude

If you have connected Claude to the portal (see *Connecting Claude*), it can
work the CRM for you with the same access you have: run your morning
review from Today, find and read records, add tasks and notes, set next
steps, and move deals through the stages, including the handoff. It can
also read a thread's summary and work through your AI suggestions. It asks
before it changes anything.

If you belong to both workspaces, Claude reads from the one you used last,
but must be told which workspace any change is for.
