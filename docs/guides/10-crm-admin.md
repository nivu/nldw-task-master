---
title: Running a CRM workspace
summary: For workspace admins — inviting, roles, lists, probabilities, FX rates, allowed domains, trash, audit, import, mailboxes, email capture, automations, targets, and the AI settings, AI fields, follow-up rules and Enrich selected.
order: 10
---

# Running a CRM workspace

This guide is for a workspace's **Admins**. Everything here lives under
**Settings** in the CRM sidebar, which only Admins see. An Admin of one
workspace has no say in the other.

A **TVA admin** (an admin of the whole portal) can do the membership parts
of this for any workspace from the portal's Admin page, under **CRM
workspaces**, without joining it.

## Inviting people

**Settings → Members → Invite.** Give the person's email address, their name
and their role. The address must be on one of the workspace's **allowed
domains** (below); the invitation says which ones if it is not.

- Someone new gets a CRM-only account: they sign in with Google at that
  address and see the CRM and nothing else of the portal. They need a Google
  account for the address. The screen tells you whether an email went out; if
  not, send them the sign-in link yourself.
- Someone who already uses the portal simply gains the workspace; their
  portal account is unchanged.
- A new person's time zone defaults from their address; you may choose
  another, and they can change it under **Account**.

You can invite only into your own workspace, and never set or change anyone's
role in the rest of the portal. Only a TVA admin may invite someone from
outside the allowed domains (an existing staff member on a personal address).

## Roles

| Role | Can |
|---|---|
| **Admin** | everything, including Settings, import, restore and permanent delete |
| **Member** | add, edit and move records; delete their own; export views |
| **Viewer** | read every record, but no amounts anywhere; complete tasks assigned to them |

Change a role, or remove someone, from **Settings → Members**. Two limits:

- A workspace always keeps at least one Admin.
- The portal owner's membership, and a TVA admin's, can be changed only by a
  TVA admin.

Removing a CRM-only person from their last workspace switches their account
off and ends their Claude tokens. A staff member keeps their portal account.

## Lists

**Settings → Lists** holds the choices people pick from: industries, sources,
channels, lost reasons, task categories, contact roles and the rest. Add an
option, rename it, colour it, reorder it, choose the default, or switch it
off. Options are never deleted, so old records keep their value.

The pipeline stages and lead stages are fixed: you can rename and colour them,
but their meaning and order stay, because the pipeline rules depend on them.

## Probabilities

**Settings → Probabilities** sets the default probability for each stage. A
deal takes its stage's default whenever it moves, unless the person moving it
sets one. Changing a default does not change existing deals.

## FX rates

**Settings → FX rates** holds, per month, how many rupees one Australian or
US dollar is worth. A deal in AUD or USD is stored in INR too, at the rate
for the month it is saved in (or the latest earlier month that has one). With
no rate at all, the save is refused and says which rate is missing. Editing a
rate later does not recompute deals already saved. When a Labs deal is won,
the handoff converts at the rate of the month it was won.

## Allowed domains

**Settings → Allowed domains** shows the email domains your workspace may
invite from. It is read-only here: a TVA admin changes it. The same list says
which addresses count as "ours" when email is synced, so an email between
colleagues is never mistaken for a client's.

## Trash

**Settings → Trash** lists what was deleted in the last 90 days. Restore a
record, or delete it permanently. After 90 days the CRM deletes it for good
on its own. Deleting a company asks what happens to its deals: move them to
another company, or delete them with it. A company or deal tied to a TVA
project cannot be deleted; the message names the deal.

## Audit

**Settings → Audit** is the workspace's history: who changed what, when, and
whether it came from the web app, Claude, an import or email sync. It cannot
be edited.

## Import

**Settings → Import.**

- **From Twenty.** Upload the export (the JSON file, or one CSV per object).
  The **dry run** writes nothing: it counts, per object, what would be
  created, updated, left alone or skipped, shows each company's derived lead
  stage, and lists every conflict. Fix what blocks the run, then run it with
  the same file. Running the same file again changes nothing. Attachments are
  listed for you to re-link from Drive; files are never copied.
- **A CSV of companies, people or deals.** Map the columns to fields (a
  mapping can be saved for next time), then dry-run. Duplicate domains and
  emails are caught before anything is written; choose per row whether to
  skip it, update the record it matches, or create a new one.

Files may be up to 4 MB.

**Export.** Any list exports as CSV from its **Export** button, with the
view's filters and columns. Admins can also export the whole workspace as
JSON.

## Mailboxes and email sync

**Settings → Mailboxes** lists the Gmail mailboxes whose labelled email the
CRM reads. Add your **own** address and the labels to read (your own Gmail
labels, not Inbox or Sent). A colleague's or shared address is added by a TVA
admin. A mailbox belongs to one workspace only.

Only an email's subject, participants, direction and time are kept, never its
body. With email summaries switched on (Settings → AI), the AI reads a
labelled message's text in the background to write the thread's summary,
and keeps only the summary. Email from a person the CRM knows lands on them and their company; from
an unknown company's domain it can create the company and the person (an
automation you can switch off).

Email sync stays off until the portal's Google credential is set up; until
then the switch says so. Once it is on, sync runs every 15 minutes, and
**Run now** runs it at once.

### Capture source and start date

Each mailbox has a **Capture** setting:

- **Labels only** (where every mailbox starts): the CRM reads only email you
  gave one of the mailbox's labels, as above. The first run reads the last
  90 days.
- **Primary and Sent**: the CRM also reads your Gmail **Primary** tab and
  your **Sent** mail, with no label needed. An email with a person or
  company the CRM already knows is filed on their record straight away, with
  no review and no AI. Newsletters, automated mail, calendar invitations and
  email among colleagues are skipped and nothing about them is kept. Email
  from people the CRM does not know is not kept either; it waits for
  **Email capture** (below) to look at it.

With Primary and Sent, the **Capture from** date (1 April 2026 unless you
change it) is where the first read starts, instead of 90 days back. Mail in
Gmail's Updates, Social, Promotions or Forums tabs is never read, even from
people the CRM knows: move it to Primary, or give it a label, if it matters.

A labelled email is always handled as before, whatever the capture setting
says, including creating its company and person when that automation is on.
Mail from an unknown sender reached only through Primary or Sent never
creates a company by itself.

Moving an email out of Primary removes it from the CRM at the next sync
unless it carries one of the mailbox's labels; deleting it (Trash) or marking
it spam removes it whatever its labels. Going back to
**Labels only** removes the captured mail that has no label, back to the
start date; mail you labelled stays.

**Deal documents.** On a Primary and Sent mailbox, each sync also looks for
emails with an SOW, statement of work, proposal, quotation, MSA or purchase
order attached. When one is with a known company that has an open deal, a
**Deal signal** appears in the Review Queue: one you sent suggests moving
the deal to Proposal; one you received suggests checking whether it is won.

## Email capture

**Settings → Email capture** finds new leads and deal signals in Primary and
Sent mail from people the CRM does not know yet. It needs email sync on, at
least one mailbox on **Primary and Sent**, and the AI rates and a budget
(Settings → AI); it does not need any AI phase switched on. Its spend counts
toward the same monthly AI budget and stops there.

Capture has three modes, and starts **Off**:

1. **Run dry run.** The AI looks at every unknown conversation since the
   mailbox's start date and writes a report, but creates nothing: no
   company, no person, no Review Queue item. It works through them in the
   background; the report fills in as it goes.
2. **Read the report.** It shows how many conversations were considered,
   skipped by the rules, skipped by the ignore list, filed as known, and
   judged by the AI; the counts by outcome and confidence; the estimated
   cost; and every lead and deal signal it found, with the company, person,
   dates, confidence and reason (low-confidence ones listed separately). The
   **Recall check** lists each conversation you labelled Sales or Leads
   since the start date and says what capture would have done with it: filed
   as known, found as a lead or deal signal, judged otherwise, skipped by
   the rules, or never seen because it was outside Primary and Sent.
3. **Apply.** Turns the dry run's high- and medium-confidence leads and deal
   signals into Review Queue items, without asking the AI again, and
   switches capture to **Live**. Only the latest dry run can be applied,
   once it has finished, and within 7 days of finishing; one that was
   stopped, or is older, is run again.

In **Live**, new conversations are looked at every hour, and anything the AI
thinks is a new lead or a deal signal waits in the Review Queue for the
mailbox's owner. Nothing is created until a person approves it. Live can be
chosen only after a dry run has been applied. Switching capture **Off**
stops it at once; switching it off during a dry run stops the dry run, and
that run can no longer be applied.

The AI only ever sees conversations that passed every earlier check: never
email among colleagues, newsletters or automated mail, email from people on
the ignore list, or email with people the CRM already knows. It reads the
email's words in the background and keeps none of them: the CRM keeps only
what it decided. A conversation is looked at again only when a new email
arrives in it. What it decided about a conversation that was not a lead is
forgotten after 12 months.

**Ignore list.** Email where everyone outside the company is on the ignore
list never reaches the AI. Add an email address or a whole domain under
**Ignore list**, or remove one. Public webmail domains such as gmail.com
cannot be ignored as a whole; ignore the address instead. Dismissing a new
lead in the Review Queue can also add to the list (see the Review Queue
guide).

Going back to the old behaviour is two switches: capture **Off** here, and
the mailbox back to **Labels only**.

## Automations and thresholds

**Settings → Automations** switches these on or off:

- creating companies and people from synced email;
- the stale-deal notice to a deal's owner (and to Admins once it turns red);
- the daily and weekly digests;
- task reminders: in the bell on the due date, a message when overdue;
- the campaign prompt when a member becomes a deal.

The pipeline rules themselves (what each stage needs, closing a lost deal's
tasks, the delivery handoff task, the TVA project) are not switchable.

**Settings → Thresholds** sets when the follow-up and deal-health dots turn
amber and red: hours before a reply is owed, days a client may stay silent,
days before a quiet deal counts as stale, and how near a close date warns.

## Targets

**Settings → Targets** sets what the workspace means to book in a month, a
quarter or the financial year (April to March), for the whole workspace or,
in Nunnari Labs, per entity. The dashboard colours bookings green, amber or
red only where a target is set.

## Jobs

**Settings → Jobs** shows the background jobs' recent runs. Three failures
in a row alert the workspace's Admins once, and a job running late shows on
Today for Admins. The AI jobs (email summaries, meeting capture, company
enrichment, AI fields, follow-up drafts, review expiry and email capture)
appear here too.
A run that did nothing on purpose says why: switched off, the AI budget
reached, or the AI rates not set yet.

## Settings → AI

The AI features are switched on here, one phase at a time, and nothing runs
until you have set a budget and switched a phase on. Only your workspace's
Admins see this page.

- **Monthly budget**, in US dollars. At zero, which is where every workspace
  starts, no AI work runs. When this month's spend reaches the budget, AI
  work stops for the rest of the month: the jobs say "AI budget reached", a
  banner shows here and under Jobs, and the Admins get one notice. Raising
  the budget, or the new month starting, lets the waiting work carry on;
  nothing is done twice. Months run on India time.
- **Spend this month**, in total and per job. It is an **estimate** worked
  out from the amount of text processed, and the page says so. Spend is shown
  to this workspace's Admins and nobody else, and never in Claude.
- **Phases.**
  1. **Email summaries** — summaries of labelled email threads, and the
     Review Queue. Needs email sync to be on.
  2. **Meetings and enrichment** — meeting notes from Fireflies, and company
     details read from companies' own websites.
  3. **AI fields and follow-up rules** (below).
  4. **Contact recency** — how recently we were in touch with each company and
     person.

  Switching a phase off stops its jobs at their next run and hides what it
  shows; what it already made is kept and comes back when it is switched on
  again. Open suggestions stay in the Review Queue until they are decided or
  expire.

If the page says the AI rates are not set yet, no AI job runs whatever the
budget and phases say; the rates are set on the server by the portal's
owner.

**Roll out Nunnari Labs first.** Switch phases on in Nunnari Labs, and in
Nunnari Academy B2B only at least a week later, once the portal's owner has
looked at what Labs produced.

## AI fields

**Settings → AI fields** lists the fields AI fills in on company and deal
pages. Four come ready-made: an ICP fit (in Nunnari Labs only) and an account
brief on companies; a deal outlook (On track, At risk or Stalled, with a
reason) and, when the outlook is At risk or Stalled, what is blocking, on
deals. Edit or switch off any of them.

**Add field** to make your own: a name, whether it is for companies or deals,
the prompt (what the AI should work out, from the record's fields, its email
summaries, meeting notes and website details; never amounts), and the output:
text, a single choice from options you list, or a number from 0 to 100.

AI fields appear only on record pages, marked AI. They cannot be columns,
filters or sorts in a list, and they never change a stage, a probability, a
flag or a colour. They are worked out again when what they read changes, at
most once an hour per record. They need phase 3 on.

## Follow-up rules

**Settings → Follow-up rules** has four rules, each with its own switch,
all off to start with. They build on the follow-up flags you already have,
and they need phase 3 on.

- **R-1 No reply after outreach.** When our latest email to a person or
  company has had no reply for the number of days you set, the AI drafts a
  follow-up for the owner to review. The owner sends it from their own mail;
  the CRM never sends email. Needs phase 1 on and email sync live.
- **R-2 Stuck in stage.** An open deal turns amber, **Stuck in stage**, after
  the number of days you set for its stage, and red at twice that. Its owner
  is told once per stage. While on, the flag counts like any other in deal
  health, Follow-ups owed and the dashboard; switched off, no deal has it.
  The Stale deal flag is unchanged.
- **R-3 Lead gone cold.** A company in Contacted or Nurturing with no activity
  for the number of days you set gets a suggestion: move it to Nurturing or
  Disqualified, or, from Nurturing, set a next touch.
- **R-4 Campaign follow-through.** A campaign member who has been Engaged or
  Meeting booked for more than a week, with no deal and no open task, gets a
  suggestion to qualify it into a deal or close it out.

Rules only ever suggest. Every suggestion waits in the Review Queue, and
nothing is suggested for a person marked Do not contact.

## Enrich selected

With phase 2 on, a company created with a website (by hand, from Claude, by
an import or from email sync) has its details read from that website in the
background, and changing the website reads it again. Nothing typed by a
person is ever overwritten: the details sit beside the company's own fields,
marked AI, for someone to promote.

To ask again, or for companies that were already there, open a **Companies**
list, tick the rows, and choose **Enrich selected**. It is for Admins, in the
web app only. Companies are worked through a few a minute in the background;
each company page shows how its enrichment went, and a site that cannot be
read says so.
