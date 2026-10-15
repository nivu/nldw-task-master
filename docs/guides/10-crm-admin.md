---
title: Running a CRM workspace
summary: For workspace admins — inviting, roles, lists, probabilities, FX rates, allowed domains, trash, audit, import, mailboxes, automations and targets.
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
body. Email from a person the CRM knows lands on them and their company; from
an unknown company's domain it can create the company and the person (an
automation you can switch off).

Email sync stays off until the portal's Google credential is set up; until
then the switch says so. Once it is on, sync runs every 15 minutes, and
**Run now** runs it at once.

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
Today for Admins.
