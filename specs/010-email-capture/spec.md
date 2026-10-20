# Automatic Email Capture

| | |
|---|---|
| **Feature** | `010-email-capture` |
| **Status** | Planned and built 20 October 2026 on branch `010-email-capture`: S1 `324ee83`, S2 `6455071`, S3 `0f7b611`, then the docs commit "Document email capture (010)" (migrations `071`–`073`). Not yet merged or released. Owner decisions E1–E6 and E10–E12 are recorded in §12; E7–E9 are recommended refinements from the options analysis. |
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
  mailbox; adding one later is a setting.
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
reason, the estimated cost, and a recall line such as *"Found N of your 60
labelled conversations; M of the rest are outside Primary and Sent."* The admin
applies it, and the findings become Review Queue items without being judged
a second time.

## 5. Functional requirements — S1 known-contact capture

| ID | Requirement |
|---|---|
| FR-CAP-SRC-01 | Each mailbox has a **capture source** setting: *Labels only* (today's behaviour, the default for every existing mailbox) or *Primary and Sent*. An Admin changes it in Settings → Mailboxes. |
| FR-CAP-SRC-02 | With *Primary and Sent*, a message is in scope when it carries Gmail's `CATEGORY_PERSONAL` label (the Primary tab, archived or not) or `SENT`, or one of the mailbox's configured labels, and is in neither Trash nor Spam (a deleted message keeps its other labels in Trash). History events that add or remove these labels behave as label events do today (`007` FR-CRM-SYNC-09), so a message the owner moves into Primary is picked up and one moved out is dropped. |
| FR-CAP-SRC-03 | `007`'s internal-only skip still applies. A message is also skipped, before matching, when its headers mark it as bulk or automated: `List-Unsubscribe`, `Precedence: bulk/list/junk`, `Auto-Submitted` other than `no`, a no-reply sender, or a calendar invitation. The skip stores nothing. |
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
| FR-CAP-DIS-04 | **What Claude sees.** The conversation's messages in order, newest last: sender and recipients, date, direction, attachment names and the plain text of each message, with quoted history and signatures beyond the first 20 lines trimmed, and at most the last 12 messages or 24,000 characters. Plus whether the mailbox owner replied. Nothing from other conversations or other records. Text is held in memory only and never logged (`009` FR-CRMAI-LLM-04). |
| FR-CAP-DIS-05 | **What Claude returns**, as a structured output: an outcome of *new lead*, *deal signal*, *other business* (vendor, hiring, partner, service provider) or *not relevant*; a confidence of high, medium or low; for a lead or deal signal, the company name, a suggested website or domain, the person's name, email and title, a suggested lead stage (Contacted or Qualified) or deal stage (Discovery, Proposal or Closed Won), and a one-line reason under 160 characters. The model is `009`'s fixed model; every call goes through `009`'s one call wrapper, so it is budgeted, logged in `crm_ai_usage` and refused when a gate is closed. |
| FR-CAP-DIS-06 | **Decision record.** Every judged candidate records mailbox, Gmail thread id, the latest message id judged, the outcome, confidence, classifier version and time. For *other business* and *not relevant* nothing else is kept. A conversation is judged again only when a later message arrives, and then from the start. |
| FR-CAP-DIS-07 | **Low confidence.** A lead or deal signal judged with low confidence is recorded but creates no Review Queue item; the dry-run report lists it separately. |
| FR-CAP-DIS-08 | **Duplicates.** Before a lead becomes an item, the suggested domain and company name are checked against existing companies (domain, a sibling domain with the same name on another public suffix, and `007`'s duplicate check, FR-CRM-DUP). A match turns the item into *link to the existing company*. |
| FR-CAP-DIS-09 | **Public webmail.** A participant on a public webmail domain (`007` FR-CRM-ACC-06 list) never makes a company domain. The item proposes the company from Claude's reading, and approval creates the company without a domain unless the reviewer adds one. |
| FR-CAP-DIS-10 | **Hourly.** In Live mode the job runs every hour on new candidates. In Dry run mode it runs once over every candidate since the capture start date, then stops. |
| FR-CAP-DIS-11 | **Retention.** A decision record of *other business* or *not relevant* is deleted 12 months after it was judged, by the existing nightly purge. A later message in the conversation replaces it sooner (FR-CAP-DIS-06). |

## 7. Functional requirements — the Review Queue

| ID | Requirement |
|---|---|
| FR-CAP-REV-01 | Two new item types join `009`'s: **`new_lead`** (create the company, the person and link the conversation) and **`deal_signal`** (create a deal at Discovery for a known company, or move a known open deal to the suggested stage). Each shows its evidence: the conversation's date range, message count, whether the owner replied, attachment names and Claude's reason. Neither shows message text. |
| FR-CAP-REV-02 | **Approving** goes through the same services a manual edit uses, so every pipeline rule applies (`007` FR-CRM-PIPE): a deal at Discovery needs its next step and date, Proposal needs amount and expected close, Closed Won needs the Labs handoff. Missing fields are asked for in the approval form; nothing skips them. Approving a `new_lead` creates the company at the suggested lead stage (New if it was never contacted), the person under it, the domain, and stores the conversation's messages as captured mail, all with the approver as actor and the item id in the audit. |
| FR-CAP-REV-03 | **Dismissing** a `new_lead` asks *ignore this sender* or *ignore this whole domain* (public webmail domains only offer the sender) and adds the choice to the ignore list. Dismissing a `deal_signal` changes nothing else. |
| FR-CAP-REV-04 | Items are owned by the mailbox owner. Dedupe, expiry after 14 days and the tombstone after 90 follow `009` FR-CRMAI-REV. An expired `new_lead` does not return for the same messages. |

## 8. Functional requirements — the dry run report

| ID | Requirement |
|---|---|
| FR-CAP-RPT-01 | A dry run writes decision records (FR-CAP-DIS-06) marked *dry run* and creates no Review Queue item and no record. |
| FR-CAP-RPT-02 | Settings → Email capture shows the latest dry run: conversations considered, skipped by rules, skipped by the ignore list, filed as known, judged by Claude; counts by outcome and confidence; the estimated cost; and a list of every lead and deal signal with company, person, dates, confidence and reason. |
| FR-CAP-RPT-03 | **Recall check.** The report lists the mailbox's conversations labelled Sales or Leads since the capture start date and says, for each, whether capture found it (filed as known, or judged a lead or deal signal), judged it otherwise, or never saw it because it was outside Primary and Sent. |
| FR-CAP-RPT-04 | **Apply.** An Admin can apply a dry run: its high- and medium-confidence leads and deal signals become Review Queue items as if judged live, without a second Claude call, and the mode becomes Live. A dry run that finished more than 7 days ago, or was stopped before it finished, cannot be applied; it is run again. |

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
- [ ] A newsletter, an internal-only message and a calendar invite in Primary are skipped and nothing about them is stored.
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
- [ ] A new unknown conversation becomes at most one `new_lead` item within the hour.
- [ ] Approving a `new_lead` creates the company, person, domain and links the messages, audited with the approver and item id; later mail from that domain is filed with no Claude call.
- [ ] Dismissing with *ignore domain* stops all further candidates from that domain reaching Claude.
- [ ] Approving a `deal_signal` that would enter Proposal asks for amount and expected close and, in Labs, creates the tentative TVA project only then.
- [ ] A conversation is not judged twice for the same latest message, across retries and restarts.
- [ ] Applying a dry run creates items without new Claude calls.
