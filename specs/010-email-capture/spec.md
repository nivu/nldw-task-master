# Automatic Email Capture

| | |
|---|---|
| **Feature** | `010-email-capture` |
| **Status** | Planned and built 20 October 2026 on branch `010-email-capture`: S1 `324ee83`, S2 `6455071`, S3 `0f7b611`, then the docs commit "Document email capture (010)" (migrations `071`–`073`). Merged to `main` as `4c3f497` and live in production. **Revision 1** (§14, 20 October 2026, branch `010-capture-improvements`, migration `074`): one suggestion per company, an honest recall line, course enquiries to the Academy workspace, and calendar invitations from outside people as lead signals. Owner decisions E1–E6 and E10–E12 are recorded in §12; E7–E9 are recommended refinements from the options analysis; E13–E16 are in §14.6. |
| **Source** | [`source.md`](source.md): the owner's brief of 19 October 2026 and the request for an options analysis on 20 October. Where this file and the source disagree, this file wins. |
| **Depends on** | `007-crm` (Gmail sync, flags, automation 2); `009-crm-intelligence` (the AI worker, the one call wrapper, the budget, the Review Queue); constitution v1.3.0 |

Today the CRM sees an email only when someone gives it the **Sales** or
**Leads** label in Gmail. In six months that caught 60 conversations. This
feature removes the labelling. The CRM reads the **Primary** tab and **Sent**
mail of the Nunnari Labs mailbox, files mail from people it already knows
straight onto their records, and asks Claude about the rest. Anything Claude
thinks is a new lead or a deal signal waits in the Review Queue for a person.

It is built in three slices, each useful alone and each behind its own switch:

| Slice | Contains | Uses Claude |
|---|---|---|
| **S1** | Known-contact capture: Primary and Sent mail with a known CRM person or company is stored and linked, with no label | No |
| **S2** | Discovery dry run: rules and Claude judge unknown conversations from 1 April 2026; a report, nothing written to records | Yes |
| **S3** | Live discovery: hourly; new leads and deal signals become Review Queue items; dismissals feed an ignore list | Yes |

`007`'s privacy rule holds: **no message body is stored**. Claude reads the
text of unknown conversations in memory on `worker-crm-ai`, as the `009`
summariser does, and the CRM stores only the outcome.

---

## 1. Context

Measured on 20 October 2026 through the CRM's read-only Gmail access, for
`navaneeth@nunnarilabs.com`, 1 April to 20 October 2026 (counts and sender
domains only):

| Measure | Count |
|---|---|
| Conversations, all mail | 8,125 |
| Inbox Primary | 1,387 (17%) |
| Inbox Updates / Social / Promotions | 5,518 / 772 / 473 |
| Conversations with mail sent from this mailbox | 249 |
| Labelled Sales / Leads by hand | 42 / 18 |
| With an SOW, proposal, quote, MSA or PO attachment | 26 |

A sample of the 300 most recent Primary conversations: 36% internal only, 7%
newsletters, no-reply or automated, 20% with a company already in the CRM,
27% with a person at an unknown company, 10% with a person on a public
webmail domain. Of the 170 conversations with an outside person, the owner
replied in 35 (21%). Labs holds 46 companies (40 domains) and 61 people
with an email address.

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** No email needs a label to reach the CRM.
- **G-2** Mail with a known client is filed on its record within one sync
  interval, without review and without Claude.
- **G-3** A new sales conversation in Primary or Sent appears as a Review
  Queue suggestion within an hour.
- **G-4** Before anything is written, the owner sees a dry-run report of six
  months, including how many of the 60 hand-labelled conversations it found.
- **G-5** Claude never sees internal, automated or known-client mail.

### 2.2 Non-goals

- **Writing labels back to Gmail** (E4). The service account stays
  `gmail.readonly`.
- **Tabs other than Primary** (E1, E10). Mail Gmail files in Updates, Social,
  Promotions or Forums is not read, not even from known contacts, unless the
  owner moves it to Primary or labels it.
- **Creating records or moving deals without approval** (E3). `009`'s "AI
  suggests, a person approves" holds.
- **Other mailboxes and the Academy workspace** (E2). The design works per
  mailbox; adding one later is a setting. *Amended by §14 (FR-CAP-ACAD,
  E15):* the Academy workspace still has no mailbox, but a course enquiry
  found in the Labs mailbox is suggested in Academy's Review Queue.
- **Real-time push** (Gmail Pub/Sub). The 15-minute sync stays.
- **Storing bodies, subjects of skipped mail, or addresses of mail that is
  not relevant.**

## 3. Principles

- **Rules before Claude, known before unknown.** Each step only sees what the
  step before could not decide.
- **Whole conversations.** A conversation is judged with all its messages,
  including the owner's replies, and judged again only when a new message
  arrives.
- **One record of every decision.** A conversation that was judged is never
  judged again for the same messages, whatever the outcome.
- **Learning by approval.** An approved lead makes its domain known, so later
  mail skips Claude. A dismissal adds the sender or domain to an ignore list.
- **Hand labels win.** A Sales or Leads label still forces a conversation in,
  with `007`'s behaviour unchanged.

## 4. User journeys

### 4.1 A client writes (S1)

A contact at Hearsight replies to a proposal. The message lands in Primary.
At the next sync it is stored against Hearsight and the person; Last inbound
moves, and Reply owed turns amber at 12 hours. No label, no review, no
Claude.

### 4.2 A stranger asks for a quote (S3)

Someone at a company the CRM has never seen asks for pricing; the owner
replies. Within the hour the Review Queue holds *"New lead: Acme Robotics ·
Priya Shah · 2 emails · asked for pricing; you replied"*. The owner approves:
the company is created at **Contacted**, the person under it, the two emails
stored and linked, and acme-robotics.com becomes a known domain.

### 4.3 A vendor pitches (S3)

A cold SaaS pitch the owner never answered is judged *other business* and
skipped. Nothing is stored but the decision. If it had been suggested and
dismissed, the sender would go on the ignore list.

### 4.4 An SOW goes out (S1, S3)

The owner sends an SOW to a known client with an open Qualification deal. The
deal-document check finds the attachment, and the Review Queue holds
*"Move to Proposal, proposal sent on 20 Oct"* for the deal's owner.

### 4.5 The dry run (S2)

An admin starts the dry run from Settings → Email capture. About 1,400
Primary conversations and the Sent mail since 1 April are judged. The report
shows counts by outcome, every suggested lead and deal signal with its
reason, the estimated cost, and a recall line such as *"Of the 18 that could
be tested, the dry run found 15. 42 could not be tested: 30 involve contacts
now in the CRM (filed automatically), 8 were internal-only, 4 are outside
Primary and Sent."* (FR-CAP-RPT-05 to -08, §14). The admin
applies it, and the findings become Review Queue items without being judged
a second time.

## 5. Functional requirements — S1 known-contact capture

| ID | Requirement |
|---|---|
| FR-CAP-SRC-01 | Each mailbox has a **capture source** setting: *Labels only* (today's behaviour, the default for every existing mailbox) or *Primary and Sent*. An Admin changes it in Settings → Mailboxes. |
| FR-CAP-SRC-02 | With *Primary and Sent*, a message is in scope when it carries Gmail's `CATEGORY_PERSONAL` label (the Primary tab, archived or not) or `SENT`, or one of the mailbox's configured labels, and is in neither Trash nor Spam (a deleted message keeps its other labels in Trash). History events that add or remove these labels behave as label events do today (`007` FR-CRM-SYNC-09), so a message the owner moves into Primary is picked up and one moved out is dropped. |
| FR-CAP-SRC-03 | `007`'s internal-only skip still applies. A message is also skipped, before matching, when its headers mark it as bulk or automated: `List-Unsubscribe`, `Precedence: bulk/list/junk`, `Auto-Submitted` other than `no`, a no-reply sender, or a calendar notification. The skip stores nothing. *Amended by §14 (FR-CAP-INV-01 to -03):* a calendar **invitation** with someone outside our domains among its participants is not bulk; it is filed as known mail or becomes a discovery candidate. Responses (Accepted, Declined, Tentative), cancellations, reminders and Google Calendar's own notifications stay bulk. |
| FR-CAP-SRC-04 | A message in scope whose outside participants include a **known** CRM person (primary or additional email) or company (a domain in `crm_company_domains`) is stored, linked and counted exactly as a labelled message is today (`007` FR-CRM-SYNC-05, -06). It needs no review and no Claude call. |
| FR-CAP-SRC-05 | A message in scope with **no** known participant is **not** stored and does **not** run automation 2, whatever that switch says. Its conversation is marked a **discovery candidate** (mailbox, Gmail thread id, latest message id and time; no addresses, no subject). Automation 2 still runs for messages that carry a configured label (FR-CAP-SRC-02), so hand labels behave as today. |
| FR-CAP-SRC-06 | **Deal documents.** Every sync, the mailbox is searched for messages since the last check whose attachments' names look like an SOW, statement of work, proposal, quotation, MSA or purchase order (Gmail search, ids only). For a known company with an open deal, a `deal_signal` Review Queue item is created (§7). Its direction gives the suggestion: sent by us means *Proposal, proposal sent on* that date; received means *possibly Closed Won, check the signed document*. |
| FR-CAP-SRC-07 | **Backfill.** Switching a mailbox to *Primary and Sent* backfills known-contact capture from the mailbox's **capture start date**, default 1 April 2026, in the same resumable pages as today's 90-day backfill (`007` FR-CRM-SYNC-08). |
| FR-CAP-SRC-08 | Removing a message from scope (moved out of Primary, deleted) removes the stored copy on the next run, as unlabelling does today. A message moved out of Primary that still carries a configured label stays; one put in Trash or Spam does not (FR-CAP-SRC-02). |

## 6. Functional requirements — S2 and S3 discovery

| ID | Requirement |
|---|---|
| FR-CAP-DIS-01 | **Switch.** Discovery has a per-workspace mode in Settings → Email capture: *Off* (default), *Dry run* or *Live*. It runs only while the mode is not Off **and** `009`'s budget and rate gates pass (FR-CRMAI-LLM-07). Phases 1–4 of `009` do not have to be on. |
| FR-CAP-DIS-02 | **Where it runs.** The discovery job runs on `worker-crm-ai`, the only service holding both the Claude key and the Gmail key with text access (`009` FR-CRMAI-CHG-01). It reads the candidates FR-CAP-SRC-05 marked; it never searches the mailbox itself. |
| FR-CAP-DIS-03 | **Ignore list.** Before any text is read (after a metadata-only read of the conversation's participants, since candidates store no addresses), a candidate is skipped when every outside participant is on the workspace's ignore list (an address or a domain). The list is edited in Settings → Email capture and grows from dismissals (FR-CAP-REV-03). |
| FR-CAP-DIS-04 | **What Claude sees.** The conversation's messages that name nobody the CRM knows (a message naming a known person or company domain is known-contact mail, filed by the sync as FR-CAP-SRC-04 decides message by message, and never sent to Claude), in order, newest last: sender and recipients, date, direction, attachment names and the plain text of each message, with quoted history and signatures beyond the first 20 lines trimmed, and at most the last 12 messages or 24,000 characters. Plus whether the mailbox owner replied. Nothing from other conversations or other records. Text is held in memory only and never logged (`009` FR-CRMAI-LLM-04). |
| FR-CAP-DIS-05 | *Amended by §14 (FR-CAP-ACAD-01): Claude also returns which business the enquiry is for, Labs or Academy.* **What Claude returns**, as a structured output: an outcome of *new lead*, *deal signal*, *other business* (vendor, hiring, partner, service provider) or *not relevant*; a confidence of high, medium or low; for a lead or deal signal, the company name, a suggested website or domain, the person's name, email and title, a suggested lead stage (Contacted or Qualified) or deal stage (Discovery, Proposal or Closed Won), and a one-line reason under 160 characters. The model is `009`'s fixed model; every call goes through `009`'s one call wrapper, so it is budgeted, logged in `crm_ai_usage` and refused when a gate is closed. |
| FR-CAP-DIS-06 | **Decision record.** Every judged candidate records mailbox, Gmail thread id, the latest message id judged, the outcome, confidence, classifier version and time. For *other business* and *not relevant* nothing else is kept. A conversation is judged again only when a later message arrives, and then from the start. A conversation has at most one open item from discovery (*amended by §14, FR-CAP-GRP-02, -07: a company has at most one, which collects its conversations; a conversation belongs to at most one*): a new judgement's item replaces an earlier one (a lead that is now a deal signal on a matched company included), and a new judgement that makes no item (not relevant, other business, low confidence) expires the earlier one. A deal-document signal (FR-CAP-SRC-06) is not discovery's and is left as it is. |
| FR-CAP-DIS-07 | **Low confidence.** A lead or deal signal judged with low confidence is recorded but creates no Review Queue item; the dry-run report lists it separately. |
| FR-CAP-DIS-08 | **Duplicates.** Before a lead becomes an item, the suggested domain and company name are checked against existing companies (domain, a sibling domain with the same name on another public suffix, and `007`'s duplicate check, FR-CRM-DUP). A match turns the item into *link to the existing company*. |
| FR-CAP-DIS-09 | **Public webmail.** A participant on a public webmail domain (`007` FR-CRM-ACC-06 list) never makes a company domain. The item proposes the company from Claude's reading, and approval creates the company without a domain unless the reviewer adds one. |
| FR-CAP-DIS-10 | **Hourly.** In Live mode the job runs every hour on new candidates. In Dry run mode it runs once over every candidate since the capture start date, then stops. |
| FR-CAP-DIS-11 | **Retention.** A decision record of *other business* or *not relevant* is deleted 12 months after it was judged, by the existing nightly purge. A later message in the conversation replaces it sooner (FR-CAP-DIS-06). |

## 7. Functional requirements — the Review Queue

| ID | Requirement |
|---|---|
| FR-CAP-REV-01 | *Amended by §14 (FR-CAP-GRP-03): a `new_lead` collects every conversation of its company, and its evidence is their totals.* Two new item types join `009`'s: **`new_lead`** (create the company, the person and link the conversation) and **`deal_signal`** (create a deal at Discovery for a known company, or move a known open deal to the suggested stage). Each shows its evidence: the conversation's date range, message count, whether the owner replied, attachment names and Claude's reason. Neither shows message text. |
| FR-CAP-REV-02 | **Approving** goes through the same services a manual edit uses, so every pipeline rule applies (`007` FR-CRM-PIPE): a deal at Discovery needs its next step and date, Proposal needs amount and expected close, Closed Won needs the Labs handoff. Missing fields are asked for in the approval form; nothing skips them. Approving a `new_lead` creates the company at the suggested lead stage (New if it was never contacted), the person under it, the domain, and stores the conversation's messages as captured mail, all with the approver as actor and the item id in the audit. *Amended by §14 (FR-CAP-GRP-04, FR-CAP-ACAD-05): every conversation the item collected is stored; an Academy item stores none and records a note instead.* |
| FR-CAP-REV-03 | **Dismissing** a `new_lead` asks *ignore this sender* or *ignore this whole domain* (public webmail domains only offer the sender) and adds the choice to the ignore list. Dismissing a `deal_signal` changes nothing else. |
| FR-CAP-REV-04 | Items are owned by the mailbox owner. Dedupe, expiry after 14 days and the tombstone after 90 follow `009` FR-CRMAI-REV. An expired `new_lead` does not return for the same messages. *Amended by §14 (FR-CAP-GRP-01, -02, -05, -06): dedupe is by company, not by conversation; an Academy item is owned as FR-CAP-ACAD-03 says.* |

## 8. Functional requirements — the dry run report

| ID | Requirement |
|---|---|
| FR-CAP-RPT-01 | A dry run writes decision records (FR-CAP-DIS-06) marked *dry run* and creates no Review Queue item and no record. |
| FR-CAP-RPT-02 | *Amended by §14 (FR-CAP-RPT-09): the report also counts course enquiries sent to the Academy, and those that could not be.* Settings → Email capture shows the latest dry run: conversations considered, skipped by rules, skipped by the ignore list, filed as known, judged by Claude; counts by outcome and confidence; the estimated cost; and a list of every lead and deal signal with company, person, dates, confidence and reason. |
| FR-CAP-RPT-03 | **Recall check.** The report lists the mailbox's conversations labelled Sales or Leads since the capture start date and says, for each, whether capture found it (filed as known, or judged a lead or deal signal), judged it otherwise, or never saw it because it was outside Primary and Sent. The check follows G-5 and NFR-CAP-02 like live discovery: a labelled conversation on the ignore list, or naming only people the CRM holds now (often created from the label itself), is not sent to Claude; the report shows it as such, counted apart, instead of a judgement. *Amended by §14 (FR-CAP-RPT-05 to -08): the line counts only conversations that could actually be tested, and gives the reason for each one that could not.* |
| FR-CAP-RPT-04 | **Apply.** An Admin can apply a dry run: its high- and medium-confidence leads and deal signals become Review Queue items as if judged live, without a second Claude call, and the mode becomes Live. A dry run that finished more than 7 days ago, or was stopped before it finished, cannot be applied; it is run again. Once any dry run was applied, an Admin may also switch back to Live without applying a later one; that later run's decisions are then judged again live, so a lead it found is not lost. |

## 9. Changes to existing specs and behaviour — FR-CAP-CHG

| ID | Change |
|---|---|
| FR-CAP-CHG-01 | **`007` FR-CRM-SYNC-02 amended**: a mailbox syncs its configured labels and, with capture source *Primary and Sent*, Gmail's Primary category and Sent mail. |
| FR-CAP-CHG-02 | **`007` FR-CRM-SYNC-05 amended**: automation 2 creates companies and people only from messages that carry a configured label; unknown participants in captured mail become discovery candidates (FR-CAP-SRC-05). |
| FR-CAP-CHG-03 | **`007` FR-CRM-SYNC-08 amended**: the backfill starts at the mailbox's capture start date, not 90 days back, for a mailbox on *Primary and Sent*. |
| FR-CAP-CHG-04 | **`009` FR-CRMAI-REV amended**: two item types, `new_lead` and `deal_signal` (FR-CAP-REV-01). |
| FR-CAP-CHG-05 | **`009` FR-CRMAI-CHG-01 amended**: besides the summariser, the discovery job on `worker-crm-ai` reads message text in memory, under the same rules: nothing stored, nothing logged. |
| FR-CAP-CHG-06 | **`009` summaries.** Captured mail linked to a record is summarised by phase 1 like labelled mail (`009` FR-CRMAI-SUM-01 already covers any stored, linked message). |

## 10. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-CAP-01 | **Cost.** The six-month dry run is estimated at under US$5 and the hourly run at under US$1 a month at the measured volume; both count toward the Labs AI budget and stop at it. |
| NFR-CAP-02 | **Privacy.** Claude sees only candidates that passed the rules, the ignore list and the known-contact check. No body, subject or address of a skipped, *other business* or *not relevant* conversation is stored. |
| NFR-CAP-03 | **Gmail quota.** Backfill and discovery stay within one mailbox's Gmail API quota, with pages and backoff as today's sync. |
| NFR-CAP-04 | **Observability.** Each run writes `crm_job_runs` with counts per step, as the other CRM jobs do; a failed run alerts the Admins after three in a row. |
| NFR-CAP-05 | **Reversibility.** Setting the capture source back to *Labels only* and discovery to *Off* restores today's behaviour; captured mail already linked stays until it leaves scope. |

## 11. Key entities and indicative changes

- `crm_mailboxes`: `capture_source` (`labels` \| `primary_sent`, default `labels`), `capture_start_date` (default 2026-04-01).
- `crm_capture_candidates`: one row per mailbox and Gmail thread awaiting or past discovery: latest message id and time, status, outcome, confidence, classifier version, judged at, dry-run flag. No addresses for skipped outcomes.
- `crm_capture_ignores`: workspace, kind (`address` \| `domain`), value, added by, from item.
- `crm_review_items.type`: add `new_lead`, `deal_signal`.
- Workspace setting `capture`: `{mode: off | dry_run | live}`.
- Gmail metadata headers fetched add `List-Unsubscribe`, `Precedence`, `Auto-Submitted`, `Content-Type`.

## 12. Decisions

| ID | Question | Source | Decision | Status |
|---|---|---|---|---|
| **E1** | Which mail | Primary only | **Primary and Sent.** Sent is needed for Reply owed, Client silent and New → Contacted to be right. | Owner, 20 Oct 2026 |
| **E2** | Which mailboxes | Nunnari Labs email | **`navaneeth@nunnarilabs.com` only.** Others are a later setting. | Owner, 20 Oct 2026 |
| **E3** | What happens to a finding | Populate the CRM | **Review Queue.** A person approves every new lead and deal signal. | Owner, 20 Oct 2026 |
| **E4** | Gmail labels | Auto-label | **Not written back.** Gmail access stays read-only; the CRM shows the outcome. | Owner, 20 Oct 2026 |
| **E5** | Known-client mail | Unstated | **Filed automatically, no review, no Claude.** | Owner, 20 Oct 2026 |
| **E6** | Six-month sweep | One shot | **Dry run first, applied after review.** | Owner, 20 Oct 2026 |
| **E7** | Two outcomes (lead, deal) | Lead or deal | **Four outcomes**, so vendors, hiring and personal mail can be told apart and skipped. | Recommended |
| **E8** | "SOW attached = deal" | As stated | **Direction decides the stage**: sent → Proposal, received → possibly Closed Won. | Recommended |
| **E9** | Every hour | As stated | **Kept for discovery**; known-contact capture keeps the 15-minute sync. | Recommended |
| **E10** | Known-contact capture in other tabs | Primary only | **Primary and Sent only, for known contacts too.** Known-client mail Gmail files in Updates, Social or Promotions is missed unless moved to Primary or labelled; accepted. | Owner, 20 Oct 2026 |
| **E11** | Capture start date | April 2026 | **1 April 2026.** | Owner, 20 Oct 2026 |
| **E12** | How long decision markers are kept | "Store which emails have been parsed" | **Until the conversation gets a new message, or 12 months** (FR-CAP-DIS-11). | Owner, 20 Oct 2026 |

## 13. Acceptance criteria

S1
- [ ] With capture source *Labels only*, sync behaves exactly as today (the existing sync tests pass unchanged).
- [ ] With *Primary and Sent*, an unlabelled Primary message from a known person is stored and linked; Last inbound and flags move.
- [ ] An unlabelled Sent message to a known company is stored as outbound; a New company moves to Contacted.
- [ ] A newsletter, an internal-only message and a calendar notification (or an invite between colleagues only) in Primary are skipped and nothing about them is stored (amended by §14; an outside person's invitation is FR-CAP-INV).
- [ ] An unknown sender's Primary message is not stored, creates no company, and leaves one discovery candidate.
- [ ] A Sales-labelled message from an unknown sender still creates the company (automation 2), as today.
- [ ] Moving a message out of Primary removes it on the next run unless it carries a configured label.
- [ ] An SOW sent to a known client with an open deal creates one `deal_signal` item suggesting Proposal.

S2
- [ ] A dry run with mode Dry run writes decision records and the report, and no Review Queue item, company or person.
- [ ] Internal, automated, known and ignored candidates never reach Claude (asserted on the call wrapper).
- [ ] The report's recall check lists every Sales or Leads conversation since the start date with its fate.
- [ ] With a zero budget or rates unset, the dry run is skipped with the reason and no Gmail text is read.

S3
- [ ] A new unknown conversation becomes at most one `new_lead` item within the hour (amended by §14: at most one per company).
- [ ] Approving a `new_lead` creates the company, person, domain and links the messages, audited with the approver and item id; later mail from that domain is filed with no Claude call.
- [ ] Dismissing with *ignore domain* stops all further candidates from that domain reaching Claude.
- [ ] Approving a `deal_signal` that would enter Proposal asks for amount and expected close and, in Labs, creates the tentative TVA project only then.
- [ ] A conversation is not judged twice for the same latest message, across retries and restarts.
- [ ] Applying a dry run creates items without new Claude calls.

---

## 14. Revision 1 (20 October 2026): capture improvements

The owner asked for four changes once `010` was live, and was away while
they were made: *one suggestion per company instead of per conversation; an
honest recall line in the report; sending course enquiries to the Academy
workspace; treating calendar invites from outside people as possible lead
signals.* For every open question the recommended option was taken (E13–E16).
Design: [`plan.md`](plan.md) "Revision 1"; data: [`data-model.md`](data-model.md)
§9 (migration `074`); payloads: [`contracts/api.md`](contracts/api.md) §7;
tasks: [`tasks.md`](tasks.md) "R1".

Why, in production terms: the first applied dry run left 31 open `new_lead`
items, nine of them for one company (HearSight), one per conversation. The
recall line said "Found N of your 60 labelled conversations" while most of
the 60 were never tested. Course enquiries from students landed in the Labs
queue. And a meeting invitation from a stranger, often the first sign of a
lead, was skipped as bulk mail.

### 14.1 One suggestion per company — FR-CAP-GRP

| ID | Requirement |
|---|---|
| FR-CAP-GRP-01 | **Company key.** Every capture `new_lead` is grouped under one company key: the suggested domain, normalised (never a public webmail domain, FR-CAP-DIS-09); else Claude's own company name, lower-cased with spaces collapsed (never a name made up from the domain, the person or the address when Claude gave none); else the sender's address. A lead linked to an existing company (FR-CAP-DIS-08) is grouped under that company record instead, so two domains that match one company make one item. |
| FR-CAP-GRP-02 | **One open item per company.** A workspace has at most one open `new_lead` from capture per company key. A new conversation for the company joins the open item instead of making a second one. The item collects, per conversation: its reference (mailbox and Gmail thread), the latest message judged, the date range, the message count and whether the owner replied. The list is kept outside the 8 KB suggestion (data-model §9.3); no conversation reference is ever dropped while the item is open. |
| FR-CAP-GRP-03 | **What the item shows.** The evidence is the totals over its conversations: the number of conversations, first and latest date, total messages, *owner replied* when he replied in any, and the latest conversation's reason, confidence and attachment names. The person suggested is the one from the latest conversation. The title stays "New lead: <company> · <person>"; the detail reads "3 conversations · 9 emails · <reason>". The approval button says "File these 3 conversations". |
| FR-CAP-GRP-04 | **Approving** creates the company exactly as FR-CAP-REV-02 says and **one person per distinct sender address** among the collected conversations (each conversation's entry keeps its sender's address and name; an address that is already a person in the workspace is linked to the company instead of created; a person already under another company stays there and is not moved; an address the approver edits replaces the latest conversation's, so the old address makes no second person), then stores **every** collected conversation as captured mail (one filing job each). Because every sender is then a known person, conversations from webmail senders, or grouped by Claude's company name, are filed too. The result counts as filed only the conversations whose sender is known after approval |
| FR-CAP-GRP-05 | **Dismissing** dismisses the company's item (with the ignore choice of FR-CAP-REV-03, from the latest sender). A conversation the dismissed item already listed, at the same latest message, never brings the company back, whatever is re-judged or re-applied. A later conversation, or a later message in a listed one, follows the existing re-suggestion rule (FR-CAP-REV-04): it makes a new open item unless the sender or domain was ignored. An item that expired because its approval was refused (FR-CAP-GRP-07) is not a decision and never blocks a conversation |
| FR-CAP-GRP-06 | **Deal signals on a known company.** A company has at most one open `deal_signal` from capture, whether it came from a deal document (FR-CAP-SRC-06) or from discovery: one per deal when the company has one open deal, one per company offering a choice of deals when it has several. A newer signal replaces the suggested stage **and the deals offered** and joins its conversation to the item's list; an older signal arriving later only joins the list, so the stage and the deal choices always come from the same, newest conversation, in whatever order signals arrive |
| FR-CAP-GRP-07 | **A conversation in one place.** A conversation belongs to at most one open capture item. When a re-judgement puts it under another company (or turns a lead into a deal signal), it leaves its old item and joins the new one; when a re-judgement makes no item, it leaves its old item. An item left with no conversation expires. An item being approved is never changed: a conversation for the same company that arrives meanwhile opens a fresh item; if that approval is then refused, the refused item's conversations join the fresh item, and a refused item that reopens leaves out every conversation another open item now holds. **Exception, deal documents:** a deal-document entry is never moved by discovery and never moves another item's entry, so one document sent to two known companies keeps one signal per company (073 behaviour); such a conversation may sit in one deal-document item per company and in one discovery item |
| FR-CAP-GRP-08 | **The items already open.** Open capture items made before this revision (one per conversation) are consolidated automatically at the start of every capture tick **in every mode except Off** (after the Off check, before the other gates, since it calls neither Gmail nor Claude; with capture Off nothing is read or changed, so Off stays the kill switch of NFR-CAP-05): per workspace and company key, the newest item (by creation, then id) survives with every member's conversations, and each of the others expires with the reason "Merged into one suggestion for <company>." and a pointer to the survivor, audited one row each. An old item's company key comes from its conversation's stored finding when the decision record still has one; otherwise from its suggestion, where a company name equal to its domain, its email or its person's name is not Claude's, so it keys by domain, then address. Decided items (approved, dismissed, expired) are never read or changed. A second run finds one item per company and changes nothing |
| FR-CAP-GRP-09 | **Expiry** stays 14 days from the item's creation (`009` FR-CRMAI-REV): a company's item does not live longer because new conversations keep arriving. A conversation after the expiry starts a new item. Every new conversation raises the item's revision, so an approval form opened earlier is refused with `009`'s "changed; review it again". |

### 14.2 An honest recall line — FR-CAP-RPT-05 to -10

| ID | Requirement |
|---|---|
| FR-CAP-RPT-05 | **Tested.** A labelled conversation counts as *tested* only when discovery actually judged it: Claude's judgement was found (a high- or medium-confidence lead or deal signal, Academy findings included, also those the Academy could not take) or otherwise. Every other conversation is *untestable* and is counted under exactly one reason |
| FR-CAP-RPT-06 | **Reasons**, decided from headers and records only, before any text is read (G-5, NFR-CAP-02). **Decision order** (the first that applies): Gmail returned it empty or malformed (*could not be judged*); no message in Primary or Sent (*outside Primary and Sent*); every message dropped by the rules (*internal-only*, *calendar notifications*, *automated mail*); on the ignore list; contacts known before the conversation began; contacts known now (both *filed automatically*); then Claude, where a failure is *could not be judged*. So a known contact's conversation outside Primary and Sent reads *outside Primary and Sent*, and an ignored known contact reads *on the ignore list*. **Display order** in the line and the list: *involve contacts now in the CRM (filed automatically)*; *are on the ignore list*; *were internal-only*; *were calendar notifications* (Google Calendar's notices, responses, cancellations and reminders; an invitation between colleagues only is internal-only); *were automated mail* (newsletters, no-reply, auto-replies); *are outside Primary and Sent*; *could not be judged*. A conversation whose messages were dropped by the rules for different reasons takes the reason that dropped most of them, calendar before automated before internal on a tie |
| FR-CAP-RPT-07 | **Wording.** With none tested: *"None of your 60 labelled conversations could be tested: 36 involve contacts now in the CRM (filed automatically), 15 were internal-only, 9 were calendar notifications."* With some tested: *"Of the 18 that could be tested, the dry run found 15. 42 could not be tested: 30 involve contacts now in the CRM (filed automatically), 8 were internal-only, 4 are outside Primary and Sent."* Reasons with a zero count are left out; "1 conversation" and "1 was" read in the singular. Course enquiries the Academy could not take (FR-CAP-ACAD-07) count as found and are named inside the found sentence: *"…the dry run found 15, of which 2 were course enquiries the Academy could not take."* The line ends "Only the first 500 were checked." only when the check stopped at the 500-conversation cap with labelled conversations still unchecked |
| FR-CAP-RPT-08 | **The page** shows the line and, under it, the same numbers as a list (tested, found, and each untestable reason with its count), and each row of the recall table names its reason ("Skipped: internal only", "Skipped: calendar notification", "Not judged: Claude failed", …). |
| FR-CAP-RPT-09 | **Academy counts.** The Labs report counts course enquiries handed to the Academy and those recorded as *academy (no workspace)* (FR-CAP-ACAD-07), from the run's findings. What the Academy then did with a handed-over enquiry (made an item, joined one, skipped it for its own ignore list or an earlier decision) is never shown or counted in Labs (FR-CAP-ACAD-04) |
| FR-CAP-RPT-10 | **Earlier runs.** A dry run that finished before this revision keeps its stored fates. Its conversations "skipped by the rules" are reported under that reason, unsplit, and the line is built with the same wording; nothing is re-computed. The honest split appears on the next dry run. |

### 14.3 Course enquiries to the Academy — FR-CAP-ACAD

| ID | Requirement |
|---|---|
| FR-CAP-ACAD-01 | **Which business.** In the Labs workspace, Claude's structured output also says whether a lead or deal signal is an **Academy** matter (an individual's or a student's course enrolment, training for students, a college or university programme) or a **Labs** one (AI services, products, software, staffing). A missing answer, and every answer in any other workspace, reads as Labs, so nothing is ever routed from Academy to Labs. |
| FR-CAP-ACAD-02 | **Not suggested in Labs.** An Academy finding of high or medium confidence makes no Labs Review Queue item; a Labs item that held its conversation earlier lets it go (FR-CAP-GRP-07). The Labs decision record keeps the outcome, confidence, that it was an Academy matter and whether it was handed over (FR-CAP-ACAD-07), and **no Academy id** and nothing the Academy decided about it |
| FR-CAP-ACAD-03 | **The Academy target.** The finding goes to the workspace with key `academy` when it exists, has at least one active Admin, and accepts enquiries from Labs: the Academy's own setting *Accept course enquiries from Labs* (Settings → Email capture in the Academy, editable only by an Academy Admin; **on by default**, the recommended option). The item is owned by the mailbox owner when he is an active Admin or Member of the Academy, otherwise by the Academy's Admins |
| FR-CAP-ACAD-04 | **Only what came from Gmail crosses.** The Academy item carries the company and person names, email, title, website, dates, message counts, whether the owner replied, attachment names, Claude's reason and confidence, and each conversation's Gmail thread id with the mailbox's address: no Labs record id, mailbox id, candidate id, dry-run id or Labs data. It is grouped by company key within the Academy (FR-CAP-GRP-01, -02), checked for duplicates against the Academy's own companies only, and skipped when every outside person is on the **Academy's** ignore list (a conversation judged live has already passed the Labs ignore list, which FR-CAP-DIS-03 reads before any text; an applied dry run reads the Academy's list alone); that skip, like every other outcome of the Academy step, is decided inside the Academy and recorded only in the Academy's structured log, never on a Labs row, report or count. Money figures are masked in every suggestion string, every conversation's reason and the note body (FR-CRMAI-LLM-05). Constitution: no CRM record crosses workspaces; this is a suggestion built from an email, judged where the mail arrived. |
| FR-CAP-ACAD-05 | **Approving in the Academy** creates the company (at the suggested lead stage) and one person per distinct sender address there, as FR-CAP-REV-02 and FR-CAP-GRP-04 say, and records the conversations as one note on the company and those people: "Email conversation in navaneeth@nunnarilabs.com: 2 emails, 1 Oct to 2 Oct 2026. Asked about the AI course for final-year students.", one line per conversation, each naming the Labs mailbox that conversation reached. No message is stored in the Academy, which has no mailbox |
| FR-CAP-ACAD-06 | **Dismissing in the Academy** follows FR-CAP-REV-03 against the Academy's ignore list, which FR-CAP-ACAD-04 reads before routing. |
| FR-CAP-ACAD-07 | **No Academy to send to.** When the workspace does not exist, has no active Admin, or does not accept enquiries from Labs (FR-CAP-ACAD-03), the finding is recorded as *academy (no workspace)*: counted in the report, suggested nowhere. Whether the Academy currently takes enquiries from Labs is therefore visible to Labs Admins; this is an **accepted disclosure** (one yes/no fact, no Academy record, member or ignore entry) |
| FR-CAP-ACAD-08 | **Budget.** The call was made for Labs and is paid from the Labs AI budget; the Academy's AI gate is not consulted and its budget is not charged. |
| FR-CAP-ACAD-09 | **Re-judged back to Labs.** Whenever a Labs judgement of a conversation ends as a Labs item or as no item, the conversation is let go from any Academy item that held it (that item expires if it was the only one), and it is suggested in Labs when it makes an item. This does not depend on the earlier judgement, which a later message clears before the conversation is re-judged |
| FR-CAP-ACAD-10 | **Dry runs.** An applied dry run routes its Academy findings the same way, checking the target again at apply time. |
| FR-CAP-ACAD-11 | **Course enquiries already in Labs.** Open Labs `new_lead` items made before this revision for course enquiries stay in the Labs queue: their decisions hold no business answer and are not re-judged (PD-29); consolidation only re-groups them within Labs. The owner dismisses them, or they expire within 14 days (FR-CAP-GRP-09). |

### 14.4 Calendar invitations from outside people — FR-CAP-INV

| ID | Requirement |
|---|---|
| FR-CAP-INV-01 | **Not bulk by its subject or sender.** A message whose subject begins *Invitation:*, *Updated invitation:* (with or without "with note") or *Invitation from Google Calendar:*, and whose participants (From, To, Cc, and Reply-To when the sender is Google Calendar's) include someone outside our domains, is not bulk because of that subject, because it comes from Google Calendar's sending address, or because of an `Auto-Submitted` header on a message from that address. **Every other bulk sign still makes it bulk**: `List-Unsubscribe`, a `Precedence` of bulk, list or junk, an auto-reply header, a delivery report, or a no-reply sender other than Google Calendar's; so a webinar or event mailing titled "Invitation: …" stays skipped. Google Calendar's own sending address never counts as that outside person |
| FR-CAP-INV-02 | **Then as any mail.** Such an invitation with a known person or company is filed as known mail (FR-CAP-SRC-04; it moves Last inbound or Last outbound and New → Contacted like any message); with nobody known it marks a discovery candidate (FR-CAP-SRC-05). |
| FR-CAP-INV-03 | **Still skipped.** Responses (*Accepted:*, *Declined:*, *Tentatively accepted:*), *Proposed new time:*, cancellations, reminders (*Notification:*), anything else from `calendar-notification@google.com`, and an invitation between colleagues only. |
| FR-CAP-INV-04 | **Claude is told.** A conversation with an invitation among its judged messages is sent with the part "Conversation type: meeting invitation", and the instruction says an invitation is a lead only when the conversation shows a sales interest. Meeting hosts (Google Meet and Calendar, Zoom, Teams, Webex) are never offered as the company's website. |
| FR-CAP-INV-05 | **One rescan.** Invitations were skipped before this revision without a marker. Once per *Primary and Sent* mailbox, the sync searches Gmail (ids only, Primary, Sent and configured labels, not Trash or Spam) for invitations since the mailbox's capture start date and passes them through the same filing as new mail, a page at a time across syncs. When it finishes, the mailbox records the time and never runs it again, whatever later changes to the mailbox (a later backfill already reads with the new rule). A failure leaves the rescan where it stopped, for the next sync; when Gmail refuses the saved page as invalid, the rescan starts again from its beginning (safe: stored messages are skipped and a candidate only moves forward). |
| FR-CAP-INV-06 | **Known limits.** Guests other than the organiser and the invitee appear only in the invitation's body, which is not read for this rule: an invitation sent by a colleague to outside guests is internal-only on the owner's copy and stays skipped. Mail older than 12 months is not marked (PD-15). |

### 14.5 Acceptance criteria

I1
- [ ] Three conversations from people at one unknown domain make one open `new_lead` listing all three, with totals 3 conversations and their summed messages; approving it queues three filings.
- [ ] Two conversations whose finding names the same company and has no domain group by name; two webmail senders with no company name stay apart.
- [ ] A dismissed company is not suggested again when one of its listed conversations is re-judged or a dry run is re-applied; a new conversation from it is suggested again.
- [ ] A conversation re-judged into another company leaves its first item; an item left empty expires.
- [ ] A deal document and a discovery deal signal for one company with one open deal make one open `deal_signal`; an older proposal signal applied after a newer closed-won one leaves the closed-won stage and its deal choices.
- [ ] One document sent to two known companies makes one signal for each.
- [ ] Two webmail senders grouped by Claude's company name both become people on approval, and both conversations are counted as filed.
- [ ] A conversation for a company whose item is being approved opens a fresh item; a refusal of that approval merges the refused item's conversations into it; nothing is left in an expired item that blocks it.
- [ ] Consolidation turns nine open HearSight-style items into one survivor (the newest) holding nine conversation references and eight expired items, each pointing at the survivor and audited; an approved or dismissed item is untouched; a second run changes nothing; with capture Off nothing is changed.

I2
- [ ] Sixty labelled conversations none of which reached Claude give "None of your 60 labelled conversations could be tested: …" with every reason counted once and the counts adding to 60.
- [ ] With some tested, the line reads "Of the N that could be tested, the dry run found F." followed by the untestable breakdown; course enquiries the Academy could not take are inside F; a mailbox with exactly 500 labelled conversations is not reported as capped.
- [ ] A Claude failure counts as *could not be judged*, never as judged otherwise.
- [ ] A finished run stored before the revision still renders, with "skipped by the rules".
- [ ] The page shows the breakdown list and each row's reason; the frontend build fails if a fate has no text.

I3
- [ ] A student's course enquiry in the Labs mailbox makes no Labs item and one Academy `new_lead`, owned by the Academy's Admins (or the mailbox owner when he is an Academy Admin or Member), holding no Labs uuid in any column.
- [ ] Approving it creates the Academy company, person and a note; nothing is filed and no Labs record changes.
- [ ] With no Academy workspace, or no active Admin, nothing is suggested and the report counts it as *academy (no workspace)*.
- [ ] Academy's ignore list stops routing; on apply the Labs ignore list is not consulted for it (live, the Labs list still applies before judging, FR-CAP-DIS-03); Labs rows look the same for an ignored and a routed enquiry.
- [ ] With the Academy's *Accept course enquiries from Labs* off, nothing is routed and the finding counts as *academy (no workspace)*.
- [ ] A conversation re-judged to Labs after a later message reopened it leaves its Academy item.
- [ ] The Labs report and decision records hold no Academy id; a Labs-only Member never sees the Academy item; the usage row is Labs'.

I4
- [ ] An *Invitation:* from an unknown outside organiser leaves a discovery candidate and is judged with "Conversation type: meeting invitation".
- [ ] An *Invitation:* from a known contact is stored and linked.
- [ ] *Accepted:*, a reminder, an internal-only invitation, and an *Invitation:* with `List-Unsubscribe` or from a no-reply address are still skipped with nothing stored.
- [ ] The rescan of a capturing mailbox feeds past invitations through the filing once, across pages, records its finish, and a second sync searches nothing; a failed page is retried on the next sync; a page token Gmail refuses as invalid restarts the rescan.

### 14.6 Decisions

| ID | Question | Decision | Status |
|---|---|---|---|
| **E13** | How suggestions are grouped, and what happens to the 31 already open | **One open item per company key** (domain, else Claude's company name, else address; a linked company by its record). Deal signals one per company. The open per-conversation items are **consolidated automatically and idempotently** in every mode but Off, newest surviving, the others expired with a pointer, audited; decided items untouched. | Owner, 20 Oct 2026, recommended option taken while away |
| **E14** | What the recall line counts | **Only conversations actually judged count as tested**; conversations about contacts already in the CRM (before or after they began) count as *filed automatically*, not tested; every other reason is named. Earlier runs keep their stored fates. | Owner, 20 Oct 2026, recommended option taken while away |
| **E15** | Where course enquiries go | **To the Academy's Review Queue**, carrying only what came from Gmail, owned by Academy Admins (or the mailbox owner if an Academy member), only when the Academy has an active Admin and its *Accept course enquiries from Labs* switch is on (default on); approval creates the company, person and a note; **Labs pays** for the call. | Owner, 20 Oct 2026, recommended option taken while away |
| **E16** | Calendar invitations | **An invitation with an outside participant is ordinary mail** (known → filed, unknown → candidate, Claude told it is an invite); notices, responses, reminders and internal invites stay skipped; **one automatic rescan per mailbox** since its capture start date. | Owner, 20 Oct 2026, recommended option taken while away |
