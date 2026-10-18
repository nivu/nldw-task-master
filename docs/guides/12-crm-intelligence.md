---
title: Review AI suggestions
summary: For sellers and workspace admins — the CRM's Review Queue, what each AI suggestion does when approved, who sees which, and deciding them from Claude.
order: 12
---

# Review AI suggestions

The CRM's AI reads labelled email threads, recorded client meetings and
companies' own websites, and watches for deals and leads that need a nudge.
It never changes a record by itself. Everything it would change waits in the
**Review Queue** until a person approves it.

The queue appears once your workspace's Admins have switched on an AI
feature (see *Running a CRM workspace*), and stays while you have something
waiting in it.

## Where suggestions reach you

- **Review**, in the CRM sidebar, opens the queue and shows how many items
  are waiting for you.
- **Today** has a **To review** section with the same count and your three
  oldest items. The daily digest carries it too.
- A new suggestion sends no notification of its own; Today and the digest
  are where you find them.

## What a suggestion looks like

Each item shows:

- **the suggestion** — the change, task or draft the AI proposes;
- **what it would replace** — the value on the record now, where there is
  one;
- **its source** — the email thread, meeting, website or follow-up rule it
  came from, with a link to it, and the **AI** marker.

And three choices:

- **Approve** makes the change exactly as suggested.
- **Edit and approve** lets you adjust it first (a different date, a better
  task title, another assignee), then makes it.
- **Dismiss** throws it away. The same source does not suggest it again
  unless something new happens, such as a new email on the thread.

## What approving does

| Suggestion | Comes from | Approving it |
|---|---|---|
| **Change next step** | an email summary or a meeting | sets the deal's next step and its date. When the thread or meeting is linked to several open deals, you choose which deal it applies to |
| **Task from an email commitment** | something we promised by a date in an email | creates the task, linked to the record, assigned to the person who made the promise where possible, otherwise to you |
| **Task from a meeting action** | an action item in a meeting | the same, for a meeting |
| **Link meeting to a deal** | a meeting note whose deal was not clear | links the meeting note to the deal you choose |
| **Use the enriched value** | a company's website, where the company's field already holds something different | replaces the field with the value from the website |
| **Follow-up suggestion** | one of the follow-up rules | does what the rule suggests: move a lead stage, set a next touch, qualify a campaign member into a deal or close it out. For a follow-up email draft, it marks the draft as used; you send the email yourself, from your own mail |

Approving is the same as making the change by hand. Every rule still
applies: a deal still needs its next step and date, a stage move still asks
for what the stage needs, and you need the same permission you would need to
make the change yourself. If something stops it, the item stays open and
says why.

**If the record changed in the meantime.** When someone has already changed
the field a suggestion would replace, approving is refused with "This record
changed after the suggestion was made." Look at the record, then use **Edit
and approve** against what is there now, or dismiss it. Changes to other
fields, such as an email arriving, do not get in the way.

The record's history shows the change as yours, marked **approved AI
suggestion**.

## Who sees which suggestion

- Each suggestion belongs to one person: the deal's or company's owner, or,
  for a task, the person who made the promise or owns the action.
- **Members** see and decide their own.
- **Admins** see the whole workspace: **Mine**, **All**, and **Decided**
  (what was approved, dismissed or expired recently). A suggestion whose
  person is a Viewer, has left, or could not be worked out goes to the
  Admins.
- **Viewers** have no Review Queue.

Suggestions on a record in Trash are hidden, and come back if the record is
restored.

## When nothing is done

A suggestion left untouched for two weeks **expires** and leaves the queue.
Like a dismissal, it is not suggested again from the same source unless
something new happens.

## From Claude

If you have connected Claude (see *Connecting Claude*), ask it what you have
to review: it lists your suggestions, and decides one only after showing you
exactly what it will do and getting your yes. Admins can ask for the whole
workspace's queue.
