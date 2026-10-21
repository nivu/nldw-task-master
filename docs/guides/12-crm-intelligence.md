---
title: Review AI suggestions
summary: For sellers and workspace admins — the CRM's Review Queue, what each AI suggestion does when approved, new leads and deal signals from email, who sees which, and deciding them from Claude.
order: 12
---

# Review AI suggestions

The CRM's AI reads labelled email threads, recorded client meetings and
companies' own websites, watches for deals and leads that need a nudge, and,
with email capture on, looks for new leads in your Primary and Sent mail.
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
| **New lead** | email capture: a conversation in Primary or Sent with someone the CRM does not know, which the AI thinks is a sales conversation | creates the company at the suggested lead stage with its domain, one person under it for each different sender in its conversations, and files every conversation's emails on them. Later email from that domain is filed straight away, with no AI. When the company may already be in the CRM, the item offers to link the conversations to it instead |
| **Deal signal** | email capture, or an SOW, proposal, quotation, MSA or purchase order attached to an email with a known company | moves the company's open deal to the suggested stage, or creates a deal at Discovery when it has none |

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

## New leads and deal signals from email

With email capture on (see *Running a CRM workspace*), two kinds of item
come from your email. Each shows its evidence: how many conversations it
holds, their dates, how many emails they have, whether you replied, the
names of any attachments, and the AI's one-line reason from the latest
conversation. The email's words are never shown, and never kept.

**One item per company.** All the conversations with one company make one
item, for example "3 conversations · 9 emails · Asked for pricing". The
person suggested is the one from the latest conversation; the approve
button reads **File these 3 conversations**. A new conversation with the
company joins the open item; if you had the item open at that moment,
approving is refused with "This suggestion changed; review it again.": look at it again and
approve. A company has at most one open deal signal too: a newer email or
document updates the suggested stage, an older one only joins the list. An
item that shows "Merged into another suggestion" was one of several
earlier items for one company, now combined into one.

**New lead.** Check the company name, domain, lead stage and person, and
change any of them with **Edit and approve**. A company that was never
replied to starts at New. When the person writes from a public webmail
address such as gmail.com, the company has no domain unless you add one.
When the domain came from a website mentioned in the email rather than the
sender's own address, the item says so: check it before approving. If the
company is already in the CRM, choose it to link the conversation there
instead of creating a second one. Approving creates one person for each
different sender among the conversations (someone already in the CRM is
linked to the company instead; someone under another company stays there)
and files every conversation on them.

**Course enquiries in the Academy.** In Nunnari Academy B2B, a new lead can
come from a Labs mailbox: a course or training enquiry the AI found there.
It says which Labs mailbox it came from, and has no conversation to open,
since the Academy keeps no email. Approving it creates the company and the
people in the Academy, and records the conversations as a note on them
(when, how many emails, the AI's reason); nothing is filed. Dismissing it
can add the sender or domain to the Academy's own ignore list.

**Deal signal.** Approving it goes through the pipeline rules like a move
made by hand:

- a new deal at **Discovery** needs its next step and date;
- **Proposal** needs the amount, currency and expected close date (and a
  point of contact if Qualification was skipped); in Nunnari Labs it also
  creates the tentative TVA project, and only then;
- **Closed Won** in Nunnari Labs is done through the deal's handoff, so the
  item links to it instead of offering Approve.

A deal already at or past the suggested stage is refused.

**Dismissing a new lead** gives you a choice: dismiss only, **ignore this
sender**, or **ignore this whole domain**. Ignoring adds the address or the
domain to the workspace's ignore list, and email from it never reaches the
AI again. The whole-domain choice is the sender's own domain, and is offered
only when that is also the lead's domain: a public webmail sender (or one
whose company website came from the email's text) can only be ignored by
sender. A dismissed company is not suggested again for the conversations
it held; a new conversation, or a new email in one of them, can suggest it
again unless you ignored the sender or domain. Dismissing a deal signal
changes nothing else.

## Who sees which suggestion

- Each suggestion belongs to one person: the deal's or company's owner, or,
  for a task, the person who made the promise or owns the action. New leads
  and deal signals from email belong to the owner of the mailbox they were
  found in.
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
something new happens. An expired new lead comes back only if a new email
arrives in one of its conversations. A company's item expires two weeks
after it was first made, even if new conversations kept joining it; a
conversation after that starts a new item.

## From Claude

If you have connected Claude (see *Connecting Claude*), ask it what you have
to review: it lists your suggestions, and decides one only after showing you
exactly what it will do and getting your yes. Admins can ask for the whole
workspace's queue. When Claude dismisses a new lead for you, it can also
ignore the sender or the whole domain, if you ask it to.
