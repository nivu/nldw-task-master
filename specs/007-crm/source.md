# Source: TVA CRM Module - Build Spec v1.0 (14 Oct 2026)

> Kept verbatim as the input to `spec.md`. Where `spec.md` and this file
> disagree, `spec.md` wins: it records the owner's decisions of 14 Oct 2026.

# TVA CRM Module: Product and Build Specification

Version 1.0, 14 Oct 2026. Owner: Navaneeth Malingan (Nunnari Labs).

This document is the input for Claude Code working inside the TVA repository (tva.nunnarilabs.com). It describes what the CRM must do, how it must behave for Nunnari Labs, DeepWeaver and Nunnari Academy, and how it plugs into what TVA already does. It deliberately does not fix table names, column types, API routes or component structure. The planner decides those from the repo's existing patterns.

---

## 0. Instructions for the planner

1. Read the repo first. Map this spec onto TVA's existing conventions for auth, roles, Supabase migrations, row-level security, API layer, UI components, background jobs on Railway, the Netlify frontend, and the NLDW MCP server. Reuse what exists; do not introduce a second way of doing anything TVA already does.
2. Produce a plan before writing code: proposed schema, migrations, services, screens, jobs and MCP tools, mapped to the phases in section 16. Flag every change to an existing TVA table and every place where this spec conflicts with how the repo works today.
3. Build Phase 1 completely before starting Phase 2. Phase 1 must fully replace Twenty for daily use.
4. Every capability in this spec must be usable through the NLDW MCP as well as the UI. A feature is not done until its MCP tool works.
5. Clean-room rule: Twenty CRM is AGPL-3.0. Do not copy, port or translate any code, schema files, migrations or UI components from the Twenty repository. This spec describes behaviour only; implement everything fresh in TVA's own style.
6. Write tests for every rule in sections 6 to 9 and every acceptance criterion in section 17.
7. Keep existing TVA behaviour unchanged unless this spec says otherwise, in particular project revenue visibility, P&L, allocations, time logs and leave.

---

## 1. Why this exists

Nunnari ran its pipeline in a Google Sheet until 5 Oct 2026, then in two Twenty CRM cloud workspaces (Labs and Academy B2B). The Twenty trial lapsed in Oct 2026. Twenty is a capable CRM, but it cannot see TVA's projects, allocations, milestones, invoices or P&L, and it does not fit how we sell.

The goal is one system from first contact to cash: a lead becomes a deal, a deal becomes a tentative project that TVA's capacity forecast can see, a won deal becomes a confirmed project with milestones and allocations, and the P&L reports against it. The CRM should match Twenty's useful capabilities, drop what we do not use, and add the Nunnari rules that Twenty could only approximate.

---

## 2. Organisations and workspaces

The CRM is multi-workspace. A workspace is a fully isolated CRM: its own records, configuration, enums, members, views and dashboards. No record, search result, dashboard number or MCP response may cross workspaces.

| Workspace | Covers | Notes |
| --- | --- | --- |
| Nunnari Labs | Nunnari Labs and DeepWeaver sales, which run as one engine | Every company, opportunity and campaign carries an Entity tag: Nunnari Labs, DeepWeaver, or both |
| Nunnari Academy B2B | Colleges, universities, corporate training, training partners | Nunnari Academy Private Limited is a separate company. No Entity field. Its own channel and category lists (section 5.10) |

A user can belong to more than one workspace and switches between them. A workspace switcher sits in the CRM header. The last-used workspace is remembered per user.

Only the Labs workspace is connected to TVA projects (section 10). The Academy workspace has no project handoff in Phase 1.

---

## 3. Users, roles and access

### 3.1 Who uses it

| Person | Workspaces | CRM role | Notes |
| --- | --- | --- | --- |
| Navaneeth | Labs, Academy | Workspace admin in both | Owns settings and data model |
| Timothy Paul | Labs, Academy | Member (Labs), Viewer (Academy) | Owner on delegated Labs items |
| TamilSelvan (tamil@deepweaver.ai) | Labs | Member | DeepWeaver co-founder; CRM-only login |
| Other DeepWeaver people (to be decided) | Labs | Member | CRM-only login |
| Sanjeev | Academy | Workspace admin (Academy) | Head of Academy; owns every Academy college item |
| Ganesh, Keerthana and the Academy sales reps | Academy | Member | CRM-only login if they have no TVA account |
| TVA project leads (Sakthivel, Devansh and others) | Labs | Viewer | See companies and won deals tied to their projects, no amounts |

### 3.2 Login types

- Existing TVA users get CRM access by a per-workspace membership with a CRM role. CRM access does not change their TVA role.
- A new CRM-only login type for people outside Nunnari Labs (DeepWeaver, Academy). They see only the CRM. They never see leave, time logs, allocations, people pages, finance pages or the TVA admin panel.
- Invitations by email, accepted through TVA's existing sign-in. Domain allow-list per workspace (Labs: nunnarilabs.com, deepweaver.ai; Academy: nunnari.academy, nunnarilabs.com).

### 3.3 Roles and permissions

Role-based, per workspace. Three built-in roles plus custom roles in Phase 2.

| Capability | Admin | Member | Viewer |
| --- | --- | --- | --- |
| Read all records in the workspace | Yes | Yes | Yes, except amount fields |
| Create and edit records | Yes | Yes | No |
| Delete (soft) records | Yes | Own records | No |
| Restore from trash, permanent delete | Yes | No | No |
| See deal amounts and pipeline value | Yes | Yes | No |
| Manage views shared with the workspace | Yes | Yes | No |
| Settings, data model, members, mailboxes, API keys | Yes | No | No |
| Run imports and exports | Yes | Export only | No |
| Trigger TVA project handoff | Yes | Yes, needs TVA manager or admin to confirm revenue (section 10) | No |

Phase 2 adds:
- Custom roles with object-level permissions (read, create, update, delete per object).
- Field-level permissions (hide or make read-only per field per role).
- Row-level rules, at minimum "only records where Entity includes X" and "only records I own", so a DeepWeaver-only role is possible if ever needed.

All permission checks must be enforced in the database layer (row-level security or equivalent), not only in the UI, so the MCP and any API key obey the same rules.

### 3.4 Audit

Every create, update, delete, restore, merge, import, export, stage change and handoff is written to an audit log with actor, time, record, and before and after values for changed fields. Admins can view and filter it. The record timeline (section 7.3) is the user-facing view of the same events.

---

## 4. Glossary

| Term | Meaning |
| --- | --- |
| Lead | A company we are trying to sell to that has no open deal yet. Lives on the Company record via Lead stage |
| Deal or Opportunity | A specific piece of work we are trying to win, with an amount and a stage |
| Entity | Which Nunnari group company a Labs-workspace record belongs to: Nunnari Labs, DeepWeaver, or both |
| Channel | The route by which we reach or reached a company: a partner (Oracle, Qualcomm, Skillda), an association (CODISSIA), or a motion (Inbound, Referral) |
| Campaign | A planned outreach effort with a goal: a webinar series, a roundtable, a college outreach drive |
| Campaign member | One company or person in one campaign, with their own progress status |
| Next step | The single next action agreed on a deal, with a date. Deal follow-ups live here, not in tasks |
| Next touch | The nurture date on a company that has no open deal and no open task |
| Loose end | A company in Nurturing with no open task and no next touch date |
| Handoff | Creating or updating a TVA project from a deal |
| Health flag | A green, amber or red signal computed from dates and email activity (section 9) |

---

## 5. Data model (logical)

Logical objects and fields only. The planner chooses physical tables, types, indexes and naming. Every object has: unique id, workspace, created at and by, updated at and by, soft-delete marker, and a position field where manual ordering is needed (kanban).

### 5.1 Field types the system must support

Twenty supports these types; the CRM needs all of them for standard fields in Phase 1 and for admin-defined custom fields in Phase 2:

Text, Long text (rich text for notes), Number, Currency (amount plus currency code), Date, Date and time, Boolean, Select (single choice from an admin-managed list, with colours and a default), Multi-select, Rating (1 to 5), Email (primary plus additional), Phone (with country code, default +91), Links (URL with label, primary plus additional), Domain, Address (street, city, state, country, postal code), Relation (one-to-many and many-to-many through a join), Array of text, Actor (created by: user, import, sweep, workflow, API, AI), and Files (attachments).

Select and multi-select option lists are configuration, not code. Admins add, rename, reorder, recolour and deactivate options in Settings without a deployment. Deactivated options stay on old records and disappear from pickers.

### 5.2 Company

| Field | Type | Notes |
| --- | --- | --- |
| Name | Text | Main display field |
| Domain | Domain | Unique per workspace when set. Used to match email |
| Additional domains | Array | For companies with several email domains |
| Entity | Multi-select | Labs workspace only. Nunnari Labs, DeepWeaver |
| Lead stage | Select | New, Contacted, Nurturing, Qualified, Deal open, Customer, Partner, Disqualified |
| Account type | Select | Labs: Prospect, Client, Technology partner, Channel partner, Training partner, Vendor. Academy list in 5.10 |
| Relationship status | Select | Active, Warm, Cold, Dormant, Churned |
| Channels | Multi-select | Workspace-specific list (5.10) |
| Owner | Relation to user | Required |
| Next touch | Date | |
| Industry | Select | Admin-managed list |
| Size | Select | 1-10, 11-50, 51-200, 201-1000, 1000+ |
| Country, City | Text | Leave blank if unknown; no defaults |
| Address | Address | |
| Website, LinkedIn | Links | |
| Description | Long text | |
| Last inbound, Last outbound, Last contact | Date and time | System-written by email sync. Never edited by hand |
| Follow-up flag | Computed | Section 9 |
| Source | Select | How the record entered: Manual, Import, Email sync, Campaign, Web form, API |
| Twenty id | Text | Hidden; migration only |

### 5.3 Person

| Field | Type | Notes |
| --- | --- | --- |
| First name, Last name | Text | Display as full name |
| Company | Relation | Optional, but sync links by domain |
| Emails | Email | Primary unique per workspace |
| Phones | Phone | WhatsApp is the main channel in India; mark which number is on WhatsApp |
| Job title | Text | |
| City | Text | |
| LinkedIn | Links | |
| Role in deal | Select | Decision maker, Champion, Influencer, Technical evaluator, Procurement, User, Other |
| Owner | Relation to user | |
| Last inbound, Last outbound, Last contact | Date and time | System-written |
| Do not contact | Boolean | Excludes the person from campaigns and sends |
| Twenty id | Text | Hidden |

### 5.4 Opportunity (deal)

| Field | Type | Notes |
| --- | --- | --- |
| Name | Text | Convention: "Client - what we sell", for example "Unicon Engineers - Phase 1" |
| Company | Relation | Required |
| Point of contact | Relation to person | Required from Qualification |
| Other contacts | Many-to-many to person, with Role in deal | |
| Entity | Select | Labs workspace: Nunnari Labs or DeepWeaver (one, for revenue attribution) |
| Stage | Select | Discovery, Qualification, Proposal, Negotiation, Closed Won, Closed Lost |
| Amount | Currency | INR, AUD or USD |
| Amount in INR | Computed and stored | At the workspace FX rate on save; used for every total |
| Probability | Number 0-100 | Defaults by stage (6.3), editable |
| Weighted amount | Computed | Amount in INR x probability |
| Expected close date | Date | Required from Proposal |
| Next step | Text | Required while open |
| Next step date | Date | Required while open |
| Owner (Sales rep) | Relation to user | Required |
| Channel | Select | Single channel for attribution |
| Source campaign | Relation to campaign | Set automatically on campaign conversion |
| Deal type | Select | New business, Expansion, Renewal, Phase follow-on, Support extension |
| Project category | Select | client, poc, product. Carried into the TVA project |
| Engagement model | Select | Discovery phase, Fixed scope, Milestone based, Forward deployed engineering, Dedicated team, Staff augmentation, AIOps annuity, Training |
| Proposal sent on | Date | Required to enter Negotiation |
| Lost reason | Select | Price, Timing, Competitor, No budget, No response, Scope mismatch, Chose to build in-house, Other. Required for Closed Lost |
| Lost notes | Long text | |
| Won on, Lost on | Date | System-written on stage change |
| TVA project | Relation | Set by the handoff (section 10) |
| Stage entered at | Date and time | System-written on every stage change, for time-in-stage |
| Twenty id | Text | Hidden |

### 5.5 Task

| Field | Type | Notes |
| --- | --- | --- |
| Title | Text | |
| Body | Rich text | |
| Status | Select | To do, In progress, Done |
| Due date | Date | |
| Priority bucket | Select | Today, This week, This month, Later |
| Category | Select | Labs: Lead, Sales, Partner, Campaign, Delivery handoff, Operations. Academy list in 5.10 |
| Assignee | Relation to user | Required |
| Linked records | Many-to-many to company, person, opportunity, campaign | At least one |
| Completed at | Date and time | System-written |

Rule: a deal's follow-up is the deal's Next step, not a task. Tasks are for other work and for leads with no deal.

### 5.6 Note

Rich text body with headings, lists, checklists, links, images and @mentions of users and records. A note can link to many records (company, person, opportunity, campaign). Mentioning a user notifies them (section 12).

### 5.7 Attachment

Files on any record, stored in Supabase Storage with workspace-scoped access. Preview for PDF and images. Typical files: proposals, SOWs, signed POs, NDAs, decks.

### 5.8 Campaign and Campaign member

Campaign:

| Field | Type | Notes |
| --- | --- | --- |
| Name | Text | |
| Type | Select | Webinar, Email sequence, Event or roundtable, LinkedIn or content, Partner co-sell, College outreach, Newsletter |
| Status | Select | Planned, Live, Completed, Cancelled |
| Channel | Select | |
| Entity | Select | Labs workspace only |
| Owner | Relation to user | |
| Start date, End date | Date | |
| Goal | Text | |
| Budget | Currency | |
| Members | Relation | |
| Converted deals, Pipeline created, Won value | Computed | From members' converted opportunities |

Campaign member (join of campaign to company and or person):

| Field | Type | Notes |
| --- | --- | --- |
| Campaign | Relation | Required |
| Company, Person | Relation | At least one |
| Status | Select | Targeted, Invited, Registered, Attended, Engaged, Meeting booked, Converted, Not interested, Unsubscribed |
| Last touch | Date | |
| Notes | Long text | |
| Converted opportunity | Relation | Set when status becomes Converted |

### 5.9 Activity records

- Email message and thread: participants, direction, subject, body according to visibility level (section 11), sent at, mailbox, linked people, companies and opportunities.
- Calendar event (Phase 2): title, start, end, attendees, linked people and companies, meeting link.
- Timeline event: one entry per create, field change, stage change, email, meeting, note, task, file, merge, handoff, with actor.

### 5.10 Workspace-specific lists (seed values)

Labs workspace:
- Channels: Oracle, Qualcomm, Anthropic, DeepWeaver, Skillda, Forge, CODISSIA, SICCI, GIBA, Guidance TN, Infosys, Referral, Inbound, Event, Direct outbound.
- Task categories: Lead, Sales, Partner, Campaign, Delivery handoff, Operations.

Academy B2B workspace:
- Channels: Inbound, Referral, Event, Direct, Webinar, Forge, Anthropic, Skillda, Guidance TN, Infosys, Nunnari Labs, LinkedIn.
- Account types: Prospect, Client, Training partner, Technology partner, Channel partner, Vendor.
- Task categories: College, Corporate training, Lead, Sales, Campaign, Marketing, Curriculum, Customer, Operations.
- Opportunity names follow "Institution - programme", for example "KCT - FDE Track A".

### 5.11 Custom fields and objects

- Phase 2: admins add custom fields of any type in 5.1 to any object, per workspace, with name, description, default, required flag, unique flag, and show or hide in views. Deactivate instead of delete; permanent delete only from Settings after deactivation.
- Phase 3: custom objects with their own fields, relations, views and record pages.

---

## 6. Pipeline rules

These rules are enforced on the server, so the UI, imports, workflows, API and MCP all obey them. When a rule blocks an action, the error says which fields are missing.

### 6.1 Lead stage

1. A new company starts at New.
2. New, Contacted and Nurturing are moved by hand. Email sync moves New to Contacted automatically when the first outbound email is recorded.
3. Qualified means a named need, a rough size, and a next step the client agreed to.
4. Creating an opportunity requires the company to be Qualified or later, and sets it to Deal open.
5. When the last open deal on a company closes: Won sets Customer and Account type Client; Lost sets Nurturing unless the user chooses Disqualified.
6. Partner companies sit at Partner and are excluded from funnel counts and conversion metrics.

### 6.2 Opportunity stage

| Stage | Required to enter | Side effects |
| --- | --- | --- |
| Discovery | Company, owner, next step and date | Company to Deal open |
| Qualification | Point of contact | |
| Proposal | Amount, currency, expected close date | Creates the tentative TVA project (Labs workspace) |
| Negotiation | Proposal sent on | |
| Closed Won | Handoff confirmed (section 10) | Won on set; company to Customer; tentative project confirmed |
| Closed Lost | Lost reason | Lost on set; tentative project archived; open tasks on the deal closed with a note |

- Stages can move backwards except out of Closed Won (reopening a won deal requires admin and is audited).
- Every stage change stores the time it happened, so time-in-stage and velocity can be reported.
- Next step and next step date are required on every open deal. Saving an open deal without them fails.

### 6.3 Default probabilities

Discovery 10, Qualification 25, Proposal 50, Negotiation 75, Closed Won 100, Closed Lost 0. Admin-editable per workspace. The deal's probability resets to the stage default on stage change unless the user overrides it on the same save.

### 6.4 Duplicates

- Company uniqueness by domain (including additional domains); person uniqueness by primary email.
- Create and import show possible duplicates by name similarity before saving.
- Merge (Phase 2, basic merge in Phase 1 for companies): pick a surviving record, choose field values per field, move all related records, emails, notes, tasks, attachments and campaign memberships, keep the merged record's id as an alias, write the merge to the timeline and audit log.

### 6.5 Deletion

Soft delete everywhere. Deleted records go to a per-object Trash, restorable by admins for 90 days, then purged. Deleting a company asks what to do with its people and deals. Deleting a deal with a confirmed TVA project is blocked.

---

## 7. Screens and interaction

The CRM is a section inside TVA with its own navigation, styled with TVA's existing components. Desktop first, fully usable on a phone browser.

### 7.1 Navigation

Sidebar: Today, Pipeline, Companies, People, Opportunities, Tasks, Campaigns, Dashboards, plus user favourites (pinned views and records). Settings for admins. Workspace switcher at the top.

### 7.2 Views (every object list)

Matches Twenty's view model:

- View types: Table, Kanban (grouped by any select field, drag to change value), Calendar (by any date field; Phase 2).
- Filters: any field, with operators suited to the type (is, is not, contains, is empty, before, after, within last N days, greater than), combined with AND and OR groups.
- Sort by multiple fields. Group table rows by a select field with collapsible groups and group counts.
- Choose, reorder and resize visible columns or card fields.
- Saved views: name, icon, private to me or shared with the workspace; a default view per object; reorder views.
- Inline editing of cells and cards, respecting permissions and pipeline rules.
- Multi-select rows for bulk actions: edit field, assign owner, add to campaign, add task, export, delete.
- Column aggregates in table footers (count, sum, average, min, max) and kanban column headers (count, sum of Amount in INR and weighted).
- Search within the view. Keyboard navigation (arrow keys, Enter to open, Escape to close).
- Pagination or virtual scrolling that stays fast at 50,000 records.

Seeded views:

| Object | View | Type |
| --- | --- | --- |
| Companies | All companies | Table |
| Companies | Lead funnel | Kanban by Lead stage, partners hidden |
| Companies | Partners and channels | Table, Channels not empty, grouped by Account type |
| Companies | Follow-ups owed | Table, follow-up flag amber or red, sorted red first |
| Companies | My accounts | Table, owner is me |
| Opportunities | Pipeline | Kanban by Stage, open only |
| Opportunities | Closing this month | Table, expected close in current month |
| Opportunities | Won and lost this quarter | Table, grouped by Stage |
| Tasks | Open tasks by owner | Table grouped by assignee |
| Tasks | My week | Kanban by Priority bucket, assignee is me |
| Campaign members | Members by status | Kanban by Status, filtered to one campaign |

### 7.3 Record page

Header with name, key fields as chips (stage, owner, entity, health dot), and actions (edit, add task, add note, log activity, send email in Phase 2, move stage, merge, delete).

Left panel: all fields, grouped, inline-editable, empty fields collapsible.

Tabs:
- Timeline: everything that happened, newest first, filterable by type.
- Tasks, Notes, Files.
- Emails: threads linked to this record (company page shows all its people's threads; opportunity page shows its company's and contacts' threads).
- Calendar (Phase 2).
- Related: people, deals, campaign memberships, and for Labs companies, TVA projects with status and phase dates.

Admins can reorder fields and tabs per object (record page layout), Phase 2.

### 7.4 Today (CRM home)

Answers one question: what do I do today. Short, one line per item, each linking to the record. Sections in this order:
1. Replies I owe (red first).
2. Deal next steps due today or overdue.
3. Tasks in my Today bucket or due today.
4. Clients gone silent on my open deals.
5. My loose ends.

A person picker lets an admin view anyone's Today, matching the old Pipeline Desk.

### 7.5 Command menu

Cmd or Ctrl + K anywhere in the CRM: search across companies, people, opportunities, campaigns, tasks and notes in the current workspace; jump to a record or view; create a record; run quick actions on the open record; ask the AI (Phase 2).

### 7.6 Notifications

In-app bell and email digests:
- In-app: assigned a task or record, mentioned in a note, deal I own changed stage by someone else, reply owed turned red, handoff waiting for my confirmation.
- Daily email digest at 9:00 AM in the user's time zone: their Today list.
- Weekly digest on Monday to admins: pipeline movement, won and lost, health counts.

Time zones per user: Asia/Kolkata default, Australia/Sydney for DeepWeaver users.

### 7.7 Number and date display

- Workspace base currency INR. Amounts can be shown in Indian style (12 L, 1.2 Cr) or full format, per user preference.
- Dates as 14 Oct 2026. Times in the viewer's time zone.

---

## 8. Campaigns and nurturing

- A campaign's member board is the working surface for a live campaign: drag members through statuses, set last touch, add notes.
- Adding members: from any company or people view (bulk action), by CSV, or by a saved filter.
- When a member moves to Converted, the user creates or picks an opportunity; the system sets Converted opportunity on the member and Source campaign on the deal. This is the attribution chain.
- Campaign page shows member counts by status, conversion rate, pipeline created, won value, cost per converted member if budget is set.
- Day-to-day nurturing outside campaigns uses tasks and the company's next touch date.
- Seeded campaigns, imported from Twenty: CODISSIA AI for Manufacturing Roundtable (Labs), Academy weekly webinars Oct 2026 to Jan 2027 (Academy, Saturdays 7 to 9 PM), College MoU and course outreach (Academy).
- Sending campaign emails from the CRM is Phase 3 (section 13.4). Until then, sends happen from Gmail or other tools and outcomes are recorded on members.

---

## 9. Health flags and follow-up rules

Computed on every relevant change and by a scheduled job at least every 30 minutes. Shown as a coloured dot on cards and rows and counted on the dashboard. Thresholds are workspace settings; defaults below carry over from the sheet tracker rules (client silent over 3 days, our reply owed over 24 hours).

| Flag | Applies to | Red | Amber |
| --- | --- | --- | --- |
| Reply owed | Person, company, deal | Their latest email is newer than our latest and older than 24 h | Same, 12 to 24 h |
| Client silent | Open deal | No inbound for more than 3 days after our last outbound | 2 to 3 days |
| Next step | Open deal | Next step date is past | Today or tomorrow |
| Close date | Open deal | Expected close date is past | Within 7 days |
| Stale deal | Open deal | No stage change, note, task or email for 21 days | 14 to 21 days |
| Loose end | Company in Nurturing | No open task and no next touch | Not used |
| Task overdue | Task | Due date past | Due today |

A deal's overall health is its worst flag. A company's is the worst across its open deals and its own flags.

Internal-only emails (all participants in our own domains) never affect flags. Auto-replies and bounces are ignored where detectable.

---

## 10. TVA integration (Labs workspace)

TVA already has projects with category (client, poc, product, internal), status (confirmed or tentative), probability, revenue (visible only to managers and admins), phases with dates, a project lead, billing milestones, allocations, a capacity forecast that includes tentative projects, invoices and a monthly P&L. Tentative projects can be planned and allocated but take no time logs and stay out of profit. The CRM drives that existing model; it does not duplicate it.

### 10.1 Links

- A TVA project can link to a CRM company and to the opportunity that created it. The existing free-text client stays for now and is filled from the company name.
- A CRM company page lists its TVA projects; a TVA project page shows its company and source deal (name, owner, won date), without amounts unless the viewer may see revenue.

### 10.2 Lifecycle

| Deal event | TVA effect |
| --- | --- |
| Enters Proposal | Create a tentative project: name from the deal, client and company link, category from Project category, probability from the deal. No revenue set. If the deal already has a project, do nothing |
| Probability or expected close changes while open | Update the tentative project's probability |
| Amount changes while open | No change in TVA (revenue is set only at win) |
| Closed Won | Open the handoff dialog (10.3). On confirm, the project becomes confirmed |
| Closed Lost | Archive the tentative project and release its allocations; record lost reason on the deal |
| Reopened from Lost | Restore the archived tentative project |
| Deal skips Proposal and goes straight to Closed Won | Create the project directly as confirmed through the handoff dialog |

### 10.3 Closed Won handoff dialog

Shown when a deal moves to Closed Won, also callable from the MCP with a preview first.

Inputs, pre-filled where possible:
- Project name and category.
- Project lead (an active TVA person).
- Delivery phase start and end dates (and optional support phase).
- Revenue, pre-filled with Amount in INR. Only a TVA manager or admin can confirm revenue; if a member closes the deal, the project is confirmed without revenue and a task "Confirm revenue and milestones" is created for the Labs admins.
- Billing milestones: name, due date, amount; must add up to revenue or show the difference.

On confirm: the project flips to confirmed, revenue and milestones are written in TVA, a timeline event is written on both the deal and the project, and a "Delivery handoff" task goes to the project lead to schedule kickoff.

### 10.4 Rules

- One opportunity creates at most one project. Follow-ons (Phase 2, extended support) are new opportunities with Deal type Phase follow-on or Support extension, creating new projects, matching how FluxBooks Phase 2 and ARB Phase 1 Extended Support exist in TVA today.
- After Closed Won, revenue and milestone changes happen in TVA, not on the deal.
- Revenue is never double-counted: the P&L reads TVA only; the CRM dashboard reports pipeline and bookings, not recognised revenue.
- Deal amounts appear only on CRM pages and to CRM roles allowed to see them. TVA's existing rule that finance stays off team-facing pages is unchanged.

### 10.5 Capacity check

The CRM dashboard shows, for the next 90 days, hours that tentative projects would need (from their allocations) against free capacity from TVA's forecast, so we see staffing problems before a deal is won.

---

## 11. Email and calendar

### 11.1 Phase 1: label-based sync

Matches how we already work: anything sales-related in Gmail carries a label.

- Sources: Gmail mailboxes listed per workspace in Settings. Labs starts with navaneeth@nunnarilabs.com and navaneeth@deepweaver.ai. Academy starts with the Academy team mailboxes Sanjeev nominates.
- Labels synced per workspace, configurable. Labs: Sales, Leads. Academy: academy/course, academy/course/college.
- Access: a Google Workspace service account with domain-wide delegation and read-only Gmail scope, one per Google Workspace (nunnarilabs.com, deepweaver.ai, nunnari.academy). Internal to each Workspace, so no Google app verification is needed. Credentials live in Railway secrets.
- Job: every 15 minutes, incremental using Gmail history ids per mailbox. Also triggerable from the UI and MCP.
- For each labelled message: skip if every participant is in our own domains; set direction (outbound if the sender is in our domains); match outside participants to people by email, then to companies by domain; create unknown companies at New and people as needed, owned by the mailbox owner, Source Email sync; store the message per the visibility level; update Last inbound, Last outbound, Last contact; recompute flags.
- Idempotent: re-running never duplicates a message.
- Initial backfill: last 90 days on first connection.

### 11.2 Phase 2: full mailbox and calendar sync (Twenty parity)

- Per-user mailbox connection (OAuth with the same internal apps), several mailboxes per user.
- Choose folders and labels to include or exclude.
- Visibility level per mailbox: Metadata only, Subject and metadata, Full content. Default Subject and metadata.
- Exclude internal emails by default (toggle per workspace). Exclude group and distribution-list mail. Blocklist of addresses and domains per user and per workspace.
- Contact auto-creation toggle per mailbox: off, people from sent mail only, everyone.
- Threads show on person, company and opportunity pages; opening a thread shows the full conversation per visibility.
- Google Calendar sync: events with external attendees appear on people and companies, upcoming meetings on the deal page, and a meeting counts as a contact for health flags.
- Send email from a record (Gmail send-as the user, threaded replies, templates with merge fields), logged automatically.

### 11.3 Privacy

Email bodies are visible only to members of the workspace and respect the mailbox's visibility level. Personal or sensitive threads can be hidden from the CRM by removing the label (Phase 1) or by the blocklist (Phase 2); hiding removes the stored copy.

---

## 12. Automations (workflows)

### 12.1 Phase 1: built-in automations

Implemented in code, each with an on and off switch and its thresholds in Settings, each logging its runs:

1. Stage side effects and TVA lifecycle (sections 6.2 and 10.2).
2. Auto-create company and person from synced email (11.1).
3. Move company New to Contacted on first outbound email.
4. Health flag computation (section 9).
5. Stale deal detection: notify the owner, and admins at red.
6. Daily 9 AM Today digest per user; weekly Monday digest to admins.
7. Task due reminders: in-app at the due date, email for overdue.
8. Campaign conversion: setting a member to Converted prompts for a deal and writes the attribution.
9. Closed Lost cleanup: close the deal's open tasks with a note.
10. Closed Won: create the delivery handoff task for the project lead.

### 12.2 Phase 3: workflow builder (Twenty parity)

A visual builder per workspace with versioning (draft, active, archived versions), test runs, and a run history showing each step's input, output and errors.

- Triggers: record created, record updated (optionally only when chosen fields change), record deleted, schedule (cron-style), manual (from a record or a bulk selection), incoming webhook.
- Actions: create, update, upsert, delete and search records; filter and if/else branches; iterator over a list; delay (duration or until a date); send email from a connected mailbox; in-app notification; HTTP request; code step (sandboxed JavaScript); form (ask the user for input mid-run); AI agent step; create or update a TVA project through the handoff rules.
- Steps can use outputs of earlier steps. Runs respect the permissions of the workflow's owner role.

---

## 13. AI

Nunnari is an Anthropic Claude partner; use the Claude API through TVA's existing configuration. Every AI action runs with the permissions of the user or workflow that invoked it, and every AI write is attributed to actor AI with the invoking user recorded.

### 13.1 Phase 2: CRM assistant

A chat panel inside the CRM (and from the command menu) that answers questions over CRM data in the current workspace and can take actions with confirmation: "Which deals over 10 L have gone silent?", "Summarise everything with Unicon this month", "Create a follow-up task for Timothy on BestoMech for Friday".

### 13.2 Phase 2: record-level AI actions

- Summarise a company's or deal's history (emails, notes, timeline) into a short brief.
- Draft a follow-up email for a deal from its history and next step.
- Enrich a company from its website domain: industry, size, city, description, LinkedIn, with the user approving changes.
- Triage new inbound email from unknown senders: propose lead or not, company, and owner.
- Call prep: one-page brief for an upcoming meeting from the calendar event's people and company.

### 13.3 Phase 3: AI agent step in workflows

An agent step with a prompt, allowed tools (read records, write records, search), and output fields that later steps can use.

### 13.4 Phase 3: email campaigns

Send campaign emails from a verified sending domain to a filtered list of people, with templates and merge fields, scheduling, unsubscribe link and topic preferences, bounce and unsubscribe handling (setting Do not contact), and per-member status updates (Invited, Engaged). Respect Do not contact everywhere.

---

## 14. Dashboards and reporting

### 14.1 Phase 1: CRM dashboard (fixed)

One per workspace, visible to admins and to members for their own numbers. Green, amber, red where a threshold applies. Filters: entity (Labs workspace), owner, channel, date range.

- Open pipeline by stage: count, Amount in INR, weighted.
- Bookings: won this month, quarter, financial year (April to March), against an admin-set target.
- Won and lost this quarter with lost reasons.
- Win rate and average deal size by entity and channel.
- Average time in each stage and average days to close.
- Health counts: replies owed, silent clients, overdue next steps, overdue close dates, stale deals, loose ends.
- Lead funnel: companies per lead stage, partners excluded, and conversion between stages.
- Pipeline by channel and by source campaign.
- Activity: emails in and out, notes and tasks per owner per week.
- Labs workspace only: capacity check (10.5).

Academy workspace dashboard: same layout without entity and capacity, plus pipeline by institution type and by programme.

### 14.2 Phase 2: configurable dashboards (Twenty parity)

Admins and members build dashboards with tabs and widgets:
- Widget types: aggregate number (count, sum, average, min, max, ratio, with prefix and suffix), bar (vertical or horizontal, grouped or stacked), line (over time), pie, table of records, kanban of records (optionally editable), rich text, iframe.
- Each widget has its own object, filters, group by, date bucketing (day, week, month, quarter, Indian financial year) and sort.
- Dashboards private or shared. Widgets clickable through to the filtered records.

---

## 15. Data in and out

### 15.1 Import

- CSV and XLSX import per object with column-to-field mapping, saved mappings, preview, validation errors shown per row and fixable in the UI before confirming, duplicate detection on domain and email with choice to skip, update or create, relation import by name, domain or email, and update-existing mode.
- Twenty migration importer (Phase 1): reads Twenty exports (API JSON preferred, CSV accepted) for companies, people, opportunities, tasks, notes, attachments, campaigns, campaign members and their links, maps stage values (NEW to Discovery, SCREENING to Qualification, PROPOSAL to Proposal, NEGOTIATION to Negotiation, CUSTOMER to Closed Won, LOST to Closed Lost), keeps the Twenty id, and seeds Last inbound and Last outbound from Twenty's people timestamps. Message bodies are not imported. Runs as dry-run first with counts and conflicts per object, then for real. Both Twenty workspaces map to their CRM workspaces.

### 15.2 Export

Any view to CSV or XLSX with its current filters and columns. Full workspace export (all objects, JSON) for admins. No lock-in.

### 15.3 API, webhooks and MCP

- Internal API used by the TVA frontend; the planner follows the repo's existing API pattern.
- Phase 2: workspace API keys with a role and optional expiry, and outbound webhooks on record created, updated, deleted and stage changed, with signing secret, retries and a delivery log.
- NLDW MCP (Phase 1, required). Tool descriptions follow NLDW's existing style: who may call it in capitals, and CONFIRM FIRST on any write that touches a TVA project or deletes data. Every tool takes an optional workspace and defaults to the user's last-used workspace.

| Tool | Does |
| --- | --- |
| crm_workspaces | Workspaces the caller belongs to and their role in each |
| crm_today | The caller's Today list (7.4), or another person's for admins |
| crm_search | One search across all objects in a workspace |
| crm_get | Any record with its fields, related records, recent timeline, recent emails, health |
| crm_list | Records of an object with filters, sort and limit, or by saved view name |
| crm_create, crm_update | Any object, enforcing pipeline rules and permissions |
| crm_move_stage | Moves a deal; Proposal and Closed Won return a TVA project preview and need CONFIRM FIRST |
| crm_handoff | Confirms a Closed Won handoff with lead, dates, revenue and milestones |
| crm_set_next_step | Next step and date on a deal |
| crm_add_task, crm_complete_task | Tasks with bucket and category |
| crm_add_note | Note linked to one or more records |
| crm_campaign_members | Add, update status, convert |
| crm_dashboard | All dashboard numbers with health colours |
| crm_run_email_sync | Runs the sync now and reports matches and creations |
| crm_merge | Merge two companies or people (CONFIRM FIRST) |
| crm_import | Dry-run or run an import from an uploaded file |
| crm_audit | Audit entries for a record or a period (admins) |

The two existing daily "CRM follow-up sweep" scheduled tasks are repointed to crm_today and crm_dashboard at cutover.

---

## 16. Phases

### Phase 1: replace Twenty (target one week of build)

Workspaces and memberships, CRM-only logins, three built-in roles with database-level enforcement, audit log; Company, Person, Opportunity, Task, Note, Attachment, Campaign, Campaign member with all fields in section 5 and configurable select lists; pipeline rules (section 6) including basic company merge and trash; table and kanban views with filters, sorts, groups, saved views, inline edit, bulk actions; record pages with timeline, tasks, notes, files, emails, related; Today; command menu search; notifications and digests; label-based Gmail sync; health flags; built-in automations 1 to 10; TVA integration (section 10); fixed dashboards; CSV import and export; Twenty migration importer; all NLDW MCP tools in 15.3 except crm_audit.

### Phase 2: depth (after Phase 1 has run for a month)

Full mailbox sync with visibility levels, calendar sync, send email from records; calendar view; custom fields; custom roles with object, field and row permissions; record page layout editing; full merge for all objects; configurable dashboards; AI assistant and record-level AI actions; API keys and webhooks; crm_audit.

### Phase 3: platform

Visual workflow builder; AI agent step; email campaigns with sending domain and unsubscribes; custom objects; Academy B2C learner pipeline if the Academy decides to run it here (lead, counselling, offer link, instalment payment verification by QR, enrolment), as its own object set in the Academy workspace.

---

## 17. Acceptance criteria (Phase 1)

Workspaces and access
- [ ] No query, search, dashboard or MCP call returns data from a workspace the caller is not a member of.
- [ ] A CRM-only login sees only the CRM; every other TVA page and API returns not found or forbidden.
- [ ] A Viewer sees no amount, weighted amount or pipeline value anywhere, including MCP responses and exports.

Pipeline rules
- [ ] An open deal cannot be saved without next step and date, through the UI, import, MCP or API.
- [ ] A deal cannot be created for a company below Qualified; creating one sets the company to Deal open.
- [ ] Moving to Proposal without amount, currency and expected close fails and names the missing fields.
- [ ] Closed Lost without a lost reason fails.
- [ ] Every stage change records its time; time in stage is correct on the dashboard.

TVA integration
- [ ] Moving a Labs deal to Proposal creates exactly one tentative TVA project that appears in the capacity forecast with the deal's probability.
- [ ] Closed Won with the handoff confirmed by a TVA manager or admin flips that project to confirmed with revenue, lead, phase dates and milestones; nothing is double-counted in the P&L.
- [ ] Closed Won by a member confirms the project without revenue and creates the revenue task for admins.
- [ ] Closed Lost archives the tentative project and releases its allocations.
- [ ] Academy workspace deals never create TVA projects.

Email and flags
- [ ] A labelled email sent or received appears on the right person and company within 15 minutes, with Last inbound or Last outbound updated.
- [ ] Running the sync twice creates no duplicates.
- [ ] An email from an unknown external domain with the Sales label creates a company at New owned by the mailbox owner.
- [ ] Internal-only emails never change flags.
- [ ] The reply-owed, client-silent and next-step flags match the thresholds in section 9 in tests with fixed clocks.

Views and data
- [ ] Table and kanban views with saved filters load in under 1 second for 10,000 companies.
- [ ] CSV import catches duplicate domains and emails before writing and lets the user fix rows in the UI.
- [ ] The Twenty importer's dry run reports counts per object that match Twenty's, and the real run reproduces every link (company to people, deals, tasks, notes, campaign members, attribution).

MCP
- [ ] Claude can run the whole morning follow-up review, move a deal through every stage including the handoff, and add tasks and notes, entirely through NLDW.

---

## 18. Non-functional requirements

- Performance: list views and search under 1 second at 10,000 records per object; record page under 1 second; kanban drag feedback immediate with server confirmation.
- Security: all access rules in the database layer; secrets in Railway environment variables; service-account keys never exposed to the browser; email bodies encrypted at rest if the repo already has a pattern for it.
- Reliability: background jobs (sync, flags, digests) are idempotent and resumable, with failures surfaced in an admin job log and an in-app alert to admins after three consecutive failures.
- Backups: rely on Supabase backups; a full workspace JSON export runs weekly to storage.
- Accessibility: keyboard usable, visible focus, colour never the only signal for health (dot plus label on hover and in tables).
- Language and tone: CRM copy is plain and direct. Team-facing TVA pages keep their existing tone rules; finance stays off team-facing pages.

---

## 19. Out of scope

Quotes, price books and product catalogue; marketing automation and lead scoring; public web-to-lead forms (Phase 3 webhook trigger covers forms); telephony and call recording; WhatsApp sending (record WhatsApp touches as notes for now); invoicing (TVA already handles milestones and invoices); a mobile app.

---

## 20. Decisions still open (for Navaneeth, not the planner)

1. Which DeepWeaver people get Labs workspace logins besides Tamil.
2. Which Academy mailboxes and labels feed the Academy sync.
3. FX source for AUD and USD: monthly manual rates in Settings (default) or a daily feed.
4. Bookings targets per entity and per workspace for the dashboard.
5. Whether the Academy B2C learner pipeline moves into this CRM in Phase 3.
6. If TVA or Rocket is ever offered outside Nunnari, a legal check by Adithya that the clean-room approach keeps TVA clear of AGPL.
