# Sales CRM — V7

| | |
|---|---|
| **Feature** | `007-crm` |
| **Status** | Draft 14 October 2026 — owner decisions D1–D16 and the recommended defaults recorded in §10 |
| **Source** | [`source.md`](source.md): *TVA CRM Module: Product and Build Specification* v1.0, Navaneeth Malingan, 14 October 2026. Where this file and the source disagree, this file wins. |
| **Depends on** | `001`–`006`, all in production; constitution v1.2.0 (the Seller's Journey, CRM-only accounts, workspace isolation) |

The portal gains a sales CRM: a company becomes a deal, a deal becomes a
tentative project the capacity forecast can see, a won deal becomes a confirmed
project with milestones and allocations, and the P&L reports against it. It
replaces two Twenty CRM workspaces whose trial lapsed in October 2026.

Phase 1 is built in two halves, both in scope for this build (D10):

| Half | Contains |
|---|---|
| **1a** | Workspaces, memberships, roles, CRM-only logins, invitations; audit; companies, people, opportunities, tasks, notes, Drive links; select lists and settings; pipeline rules; trash; basic company merge; duplicate checks; table and kanban views with filters, sorts, groups, saved views, inline edit and bulk actions; record pages; Today; Cmd+K search; the TVA integration with handoff and capacity check; the Twenty importer; the core MCP tools |
| **1b** | Gmail label sync; follow-up and deal-health flags; job log and failure alerts; in-app bell and digests; built-in automations 1–10; campaigns and campaign members; fixed dashboards; CSV import and export; the remaining MCP tools |

Phases 2 and 3 are summarised in §12 and carry no requirements here.

`003` §9 and `005` §8 still govern every TVA money figure. Nothing in this spec
lets a deal amount reach a TVA team-facing page.

---

## 1. Context

Nunnari ran its pipeline in a Google Sheet until 5 October 2026, then in two
Twenty CRM cloud workspaces, Labs and Academy B2B. Twenty could not see the
portal's projects, allocations, milestones, invoices or P&L, so every won deal
was typed into the portal by hand and every lost deal left a planned project
and its pencilled-in people behind. The CRM lives inside the portal so that the
one place that knows who is free also knows what is about to be sold.

Two things make this unlike every earlier spec:

1. **People who are not employees sign in.** DeepWeaver's founders and the
   Academy's sales reps sell through the CRM but are not on Nunnari Labs'
   payroll. For them the portal is the CRM and nothing else (D1, D2).
2. **The CRM is multi-workspace.** `001` A-11 removed tenants from the portal.
   That still holds for leave, time, projects and money. Workspaces exist only
   inside the CRM, and no CRM record crosses from one to another (constitution
   v1.2.0, §I).

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — Phase 1 fully replaces Twenty for daily selling in both workspaces.
- **G-2** — A Labs deal reaching Proposal appears as a tentative project in the capacity forecast without anyone retyping it; winning confirms it with revenue in INR; losing frees its people.
- **G-3** — Each seller opens one screen in the morning and knows what to do today.
- **G-4** — A seller's daily selling — Today, search, records, Drive links, logged activity, stage moves, handoff, next steps, tasks and notes — can be done through Claude, as themselves, with the same rules. What stays web-only in Phase 1 is listed in FR-CRM-MCP-10.
- **G-5** — A CRM-only person sees no TVA page, route or tool, and is never counted in anything that is not the CRM. The only TVA facts the CRM shows them by design are a linked project's name, status and phase dates (FR-CRM-CO-02, FR-CRM-REC-03).

### 2.2 Non-goals

- A second project tool. After a deal is won, delivery, revenue and milestones are TVA's.
- A file store. The CRM keeps links to Google Drive files, never the files (D4).
- A new way to sign in. Google only, as `001` FR-AUTH-08 (D12).
- Academy project handoff. Academy deals never create TVA projects in Phase 1.
- Recognised revenue in the CRM. The CRM reports pipeline and bookings; the P&L reads TVA only.

---

## 3. Workspaces, people and roles

### 3.1 Workspaces

| Workspace | Covers | Notes |
|---|---|---|
| **Nunnari Labs** | Nunnari Labs and DeepWeaver sales, one engine | Companies, opportunities and campaigns carry an **Entity**: a company may be Nunnari Labs, DeepWeaver, or both; an opportunity and a campaign carry one (Q-61). The only workspace connected to TVA projects. |
| **Nunnari Academy B2B** | Colleges, universities, corporate training, training partners | Nunnari Academy Private Limited is a separate company. No Entity field. Its own channel, account type and task category lists (§5.4). |

### 3.2 Who uses it

| Person | Workspaces | CRM role | Account |
|---|---|---|---|
| Navaneeth | Labs, Academy | Admin in both (seeded, Q-25) | Staff (owner) |
| Timothy Paul | Labs, Academy | Member (Labs), Viewer (Academy) | Staff |
| TamilSelvan (tamil@deepweaver.ai) | Labs | Member | CRM-only |
| Other DeepWeaver people (to be invited) | Labs | Member | CRM-only |
| Sanjeev | Academy | Admin (Academy) | CRM-only unless he has a TVA account |
| Ganesh, Keerthana and the Academy reps | Academy | Member | CRM-only if they have no TVA account |
| TVA project leads (Sakthivel, Devansh and others) | Labs | Viewer | Staff |

Memberships other than the owner's are created by invitation (§5.2), not seeded.

### 3.3 Roles

Three built-in roles per workspace. A CRM role is independent of the TVA role:
a TVA manager may be a CRM Viewer, and a CRM Admin may be a TVA `user`.

| Capability | Admin | Member | Viewer |
|---|---|---|---|
| Read every record in the workspace | Yes | Yes | Yes, except amount fields |
| Create and edit records | Yes | Yes | No |
| Soft-delete records | Yes | Records they own (Q-36) | No |
| Restore from trash, permanent delete | Yes | No | No |
| See deal amounts and pipeline value | Yes | Yes | No |
| Manage views shared with the workspace | Yes | Yes | No (private views only, Q-37) |
| Settings, select lists, FX rates, members, invitations, mailboxes | Yes | No | No |
| Imports and exports | Yes | Export only | No |
| Mark a task assigned to them Done | Yes | Yes | Yes, status only (Q-57) |
| Move a deal into Proposal, Closed Won, Closed Lost | Yes | Yes; revenue is confirmed only by a TVA manager or admin (§5.20) | No |

---

## 4. User journeys

### 4.1 The morning list (P1, 1a; email sections 1b)

Tamil signs in with Google and lands on the CRM's Today: deal next steps due
or overdue, tasks for today, loose ends, and once email sync is live, replies
owed and clients gone silent. Each line opens the record. There is no leave
calendar, timesheet or colleague's salary anywhere in the view.

**Independent test**: a CRM-only Member with two overdue next steps and one
task due today sees exactly those three lines; requesting any TVA page or route
is refused.

### 4.2 A deal through every stage (P1, 1a)

Timothy creates a deal for a Qualified company with a next step and date. He
adds the point of contact and moves it to Qualification, then sets amount,
currency and close date and moves it to Proposal: a tentative project of the
same name appears in TVA with the deal's probability. At Negotiation he records
the proposal date. The client signs; he moves the deal to Closed Won and the
handoff dialog asks for project lead and phase dates. Timothy is not a TVA
manager, so the project is confirmed without revenue and a "Confirm revenue and
milestones" task goes to the Labs admins.

**Independent test**: drive one deal Discovery to Closed Won through the API;
exactly one project exists, confirmed, with lead and phases and no revenue, and
the admin task exists. Repeat as a TVA manager; the project has revenue in INR
and milestones.

### 4.3 A lost deal frees its people (P1, 1a)

A Labs deal at Negotiation is lost on price. Its tentative project is archived
and the two people allocated to it stop counting as busy in the forecast, bench
and resources views, and the project drops out of the P&L pipeline. A month
later the client returns; an admin or the deal owner reopens the deal (FR-CRM-PIPE-12), the project comes back
tentative and the same allocations are back.

**Independent test**: allocate two people to a tentative linked project, close
the deal Lost, and read the forecast, bench, resources, hiring signal and P&L
pipeline: none count it. Reopen: every figure is as before.

### 4.4 Inviting a sales team (P1, 1a)

Sanjeev, Academy admin, invites three reps from the Academy domain. Each gets a
CRM-only account and an Academy membership; they sign in with Google and see
only the Academy workspace. Sanjeev cannot invite into Labs, cannot invite a
gmail.com address, and cannot change anybody's TVA role.

**Independent test**: as an Academy admin, invite allowed and disallowed
addresses and into Labs; only the allowed Academy invite succeeds.

### 4.5 A project lead looks up a client (P2, 1a)

Sakthivel, a Labs Viewer, opens the company behind his project and sees its
people, deals and stages, with every amount absent, including in search,
kanban totals and through Claude.

**Independent test**: every read route and MCP tool, called as a Viewer,
returns no amount field.

### 4.6 Leaving Twenty (P1, 1a)

The owner loads both Twenty exports. The dry run reports counts and conflicts
per object; after fixes, the real run reproduces every company, person, deal,
task and note with their links and the Twenty ids.

**Independent test**: a fixture export with known counts; dry-run counts match;
the real run's links match; a second real run changes nothing.

### 4.7 Claude runs the morning review (P1, 1a; 1b tools)

From claude.ai, Timothy asks for his Today, moves a deal to Proposal after
seeing the project preview, adds a task and a note. Every write is confirmed
first.

### 4.8 Email shows up on its own (P2, 1b)

Navaneeth labels a client thread *Sales* in Gmail. Within 15 minutes it is on
the person and company, Last inbound is set, and if the client wrote last more
than a day ago the deal shows a red reply-owed flag.

### 4.9 A campaign (P3, 1b)

Sanjeev runs the Saturday webinar series as a campaign, drags members through
Registered and Attended, and converts one college to a deal; the deal carries
the campaign as its source.

### 4.10 The pipeline dashboard (P2, 1b)

Monday morning, workspace admins get the weekly digest and open the dashboard:
pipeline by stage, bookings against target, win rate, time in stage, health
counts, and for Labs the capacity check.

### 4.11 Edge cases

- A deal's name matches an existing project at Proposal (FR-CRM-TVA-05).
- The person moving a deal has no TVA project rights (FR-CRM-TVA-03).
- An AUD deal with no FX rate set for the month (FR-CRM-FX-03).
- A reopened Lost deal whose people have since been allocated elsewhere (FR-CRM-TVA-14).
- Reopening a won deal whose project has logged time (FR-CRM-PIPE-13).
- A member of both workspaces writing through Claude without naming one (FR-CRM-MCP-04).
- A CRM-only person's last membership is removed (FR-CRM-ACC-12).

---

## 5. Functional requirements — Phase 1a

Keywords follow RFC 2119. An item marked *(1b)* inside a 1a area ships with 1b.

### 5.1 Workspaces — FR-CRM-WS

| ID | Requirement |
|---|---|
| FR-CRM-WS-01 | The CRM MUST hold any number of workspaces, each fully isolated: its own records, select lists, settings, members, views, dashboards and FX rates. Two are seeded: Nunnari Labs and Nunnari Academy B2B (Q-25). |
| FR-CRM-WS-02 | No CRM query, search result, view, total, dashboard number, export, notification, audit entry or MCP response MAY contain data from a workspace the caller is not a member of. A record id from another workspace MUST be answered as not found (404), never forbidden, so its existence is not disclosed. |
| FR-CRM-WS-03 | A person MAY belong to several workspaces, with one CRM role in each. |
| FR-CRM-WS-04 | The CRM header MUST carry a workspace switcher listing only the caller's workspaces. The last-used workspace MUST be remembered per person, server-side, and opened on the next visit from any device. |
| FR-CRM-WS-05 | Only the Labs workspace is connected to TVA projects (§5.20). Academy deals MUST NOT create, change or link TVA projects, and the Project category field and handoff do not appear there. |
| FR-CRM-WS-06 | The Entity field (Nunnari Labs, DeepWeaver) exists on companies, opportunities and campaigns in the Labs workspace only. |

### 5.2 Accounts, invitations and permissions — FR-CRM-ACC

| ID | Requirement |
|---|---|
| FR-CRM-ACC-01 | Every person has an **account type**: `staff` (a TVA employee, every existing account) or `crm_only`. A CRM-only account is a portal profile with TVA role `user`, `logs_time` off and no lead (D2). Only a TVA admin MAY change a person's account type. |
| FR-CRM-ACC-02 | A staff member gets CRM access by a membership with a CRM role in a workspace. CRM access MUST NOT change their TVA role, and a TVA role grants no CRM access by itself. |
| FR-CRM-ACC-03 | A CRM-only account MUST be refused (403, or 404 where the route would 404) by every non-CRM page, route and MCP tool outside the shared allow-list of FR-CRM-CHG-01, and MUST NOT see leave, time, allocations, people pages, finance pages, Home or the TVA admin panel (constitution §I). The only TVA facts the CRM shows them are a linked project's name, status and phase dates; the capacity check is not shown to them (FR-CRM-CAP-02). |
| FR-CRM-ACC-04 | Invitations are by email. An invitation of someone with no portal account MUST pre-create their sign-in identity (confirmed, no password), a `crm_only` profile and the membership, as `admin.create_user` does today (D11). An invitation of an existing active account MUST only add the membership. Inviting an inactive `crm_only` account whose deactivation came from losing its last membership (FR-CRM-ACC-12) MUST reactivate it and add the membership, audited; an account a TVA admin deactivated on purpose MAY be reactivated only by a TVA admin, so a workspace Admin cannot undo an offboarding; inviting an inactive staff account, or such an account by a workspace Admin, MUST be refused 422 "This person's portal account is inactive; ask a TVA admin." (Q-75). The inviter MAY set the invitee's CRM time zone (FR-CRM-ACC-15). There is no pending state; revoking an invitation is removing the membership (FR-CRM-ACC-12). |
| FR-CRM-ACC-05 | A workspace Admin MAY invite into their own workspace only, and only addresses whose domain is on that workspace's allow-list. A TVA admin MAY invite into any workspace, and MAY bypass the allow-list for an existing staff account (for example a firstname.nl@gmail.com address). Any other invitation is refused 403 with the reason. |
| FR-CRM-ACC-06 | Each workspace has a domain allow-list, edited by TVA admins only (a workspace Admin sees it read-only: the list limits whom they may invite and is "our domains" for Gmail sync, FR-CRM-SYNC-05, so they must not widen it). Public webmail domains MUST NOT be added. Seeds: Labs `nunnarilabs.com`, `deepweaver.ai`; Academy `nunnari.academy`, `nunnarilabs.com`. The allow-list is checked when an invitation is created and never at sign-in (`001` FR-AUTH-10 unchanged). |
| FR-CRM-ACC-07 | Sign-in stays Google only and sign-up stays disabled (D12). An invitee whose address is not a Google account cannot sign in; the invitation screen MUST say so. |
| FR-CRM-ACC-08 | An invitation MUST be sent through the existing notification channel where it can reach the person (Slack DM, email fallback). The invitation screen MUST always show the sign-in link to share and say whether a message was sent. |
| FR-CRM-ACC-09 | A workspace Admin MUST NOT be able to change anybody's TVA role, account type, lead, CTC or any other TVA field, nor any membership outside their workspace, nor the membership of the owner or of a TVA admin (only a TVA admin changes those) — except the deactivation and token revocation of a `crm_only` account that FR-CRM-ACC-12 causes, and the reactivation of FR-CRM-ACC-04. |
| FR-CRM-ACC-10 | Every permission in §3.3 MUST be enforced in the backend, in one place that the web app, every API call and every MCP tool pass through (D3). New CRM tables MUST have RLS enabled with no browser grants, as every table since `010`. The CRM frontend MUST read no data from Supabase directly — no table reads, no Realtime subscriptions; every CRM read and write goes through `/api/proxy` to the backend (D1, constitution §IV). |
| FR-CRM-ACC-11 | A Viewer MUST NOT receive amount, amount in INR, weighted amount, pipeline value, campaign budget, pipeline created or won value, nor any total, sum, average or audit/timeline value derived from them, in any page, list, kanban header, search result, record, timeline entry, notification, digest, export or MCP response. The fields are omitted, not zeroed. For a Viewer the server MUST also refuse (422) any filter, sort, group or aggregate on an amount field, so an amount cannot be narrowed down by repeated filters or read from a sort order; inside a shared saved view such a condition is dropped and the view says "A condition on an amount field was removed" (Q-67). |
| FR-CRM-ACC-12 | Removing a membership ends that person's access to the workspace at once. Removing a CRM-only person's last membership MUST also deactivate the account and revoke their personal and OAuth tokens (Q-44). A staff member keeps their TVA account. |
| FR-CRM-ACC-13 | A TVA admin MUST be able to see, for every workspace, its name and member list (person, email, CRM role, account type) and invite or remove members, without becoming a member, from the TVA Admin "CRM workspaces" panel (FR-CRM-CHG-11). Workspace name, member list and job-run metadata (FR-CRM-JOB-01) are account administration, not CRM records (Q-69); they MUST NOT open any CRM record of that workspace to them. |
| FR-CRM-ACC-14 | A CRM-only person MUST be able to issue and revoke personal tokens and approve the claude.ai connector (`006` FR-OAUTH) from a CRM account page, through the shared routes of FR-CRM-CHG-01's allow-list (Q-56), and those tokens work only where the person may go: CRM routes, the allow-listed shared routes and `crm_*` tools. |
| FR-CRM-ACC-15 | Each person has CRM display preferences: time zone (`Asia/Kolkata` or `Australia/Sydney`) and amount format (Indian short such as 12 L and 1.2 Cr, or full). The person sets them on the CRM account page; the inviting workspace Admin or TVA admin MAY set the time zone at invitation. The time zone defaults to `Australia/Sydney` for a `deepweaver.ai` invitee and `Asia/Kolkata` otherwise. They affect CRM display and CRM digests only; every TVA rule stays on Asia/Kolkata (`001` NFR-03). |

### 5.3 Audit — FR-CRM-AUD

| ID | Requirement |
|---|---|
| FR-CRM-AUD-01 | Every create, update, delete, restore, permanent delete, merge, import, export, stage change, handoff, invitation, membership change and settings change MUST be written to the audit log with actor, time, workspace, record, and before and after values of the changed fields. |
| FR-CRM-AUD-02 | The actor MUST record **who** (the person, where there is one) and **what kind**: `user`, `system` (the CRM acting on a person's behalf, for example the TVA side of a deal move, D8), `import` or `sync` (email sync and scheduled jobs) (Q-35). Each entry also records its **channel** — web or MCP — so a write Claude made after CONFIRM FIRST can be told apart from one made in the web app (Q-22). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-09: a change made by approving an AI suggestion is audited as the approving person, with the suggestion's id in `audit_log.suggestion_id` (`009` FR-CRMAI-REV-05).* |
| FR-CRM-AUD-03 | A workspace Admin MUST be able to view and filter their workspace's audit (by record, person, action, period). Members and Viewers cannot. A TVA admin sees a workspace's CRM audit only by being an Admin of that workspace (Q-22). |
| FR-CRM-AUD-04 | CRM audit rows MUST NOT appear in the TVA audit page or the TVA `audit_log` tool, for anyone, TVA admins included (Q-22). TVA rows written by the deal lifecycle — the project and its phases and milestones on a handoff; allocation releases and un-releases on Closed Lost and reopen — stay TVA rows, visible there as today, with the system actor and the triggering person. *Amended 2026-10-18 by `008` FR-TASK-CHG-07: task audit rows, with or without a workspace, are excluded from the TVA audit page and `audit_log` tool (`008` FR-TASK-VIS-05). Record timelines read task history under both the old and new table names, so no history is lost by the rename.* |
| FR-CRM-AUD-05 | The record timeline (§5.17) is the user-facing view of the same events and MUST obey FR-CRM-ACC-11. |
| FR-CRM-AUD-06 | A CRM change and its audit entry MUST succeed or fail together; a change MUST NOT land unaudited. Mechanism (Q-21): every CRM mutation — ordinary field edits, bulk edits, imports and the multi-table transitions alike — goes through a database function that first sets the actor (person, actor kind, channel) as a transaction-local setting; an AFTER trigger on each CRM table writes the audit row from that setting in the same transaction, and refuses the write when no actor is set. Ordinary edits share one generic write function; the transitions have their own. The backend's `audit.record` (which runs after the write and swallows failures) is not the audit path for CRM tables. |

### 5.4 Select lists and workspace settings — FR-CRM-LIST

| ID | Requirement |
|---|---|
| FR-CRM-LIST-01 | Select and multi-select option lists are configuration, per workspace: Admins add, rename, reorder, recolour, set the default of and deactivate options in Settings, without a deployment. A deactivated option stays on old records and leaves the pickers. Options with a system key are the exception (FR-CRM-LIST-09). |
| FR-CRM-LIST-02 | Opportunity stages (Discovery, Qualification, Proposal, Negotiation, Closed Won, Closed Lost) and company lead stages (New, Contacted, Nurturing, Qualified, Deal open, Customer, Partner, Disqualified) carry server rules and MUST be fixed sets: Admins may rename and recolour them but MUST NOT add, remove, reorder or deactivate them (Q-30). Each stage and lead stage has a fixed key (`discovery`, `qualification`, `proposal`, `negotiation`, `closed_won`, `closed_lost`; `new`, `contacted`, `nurturing`, `qualified`, `deal_open`, `customer`, `partner`, `disqualified`); MCP arguments, saved-view filters and import mappings use the keys, and display names are labels only (Q-71). *Amended 2026-10-18 by `008` FR-TASK-CHG-11: the task status list is also a fixed list with system keys, and its set is `open`, `waiting`, `done`, `cancelled`. The `todo` and `in_progress` options are deactivated and kept for history, a stated exception to "fixed options are never deactivated"; options are still never deleted. A label an Admin gave to Done survives; Open, Waiting and Cancelled start with those labels; labels given to To do or In progress are not carried over. Tasks with no workspace take status, bucket and priority labels from fixed labels in code.* |
| FR-CRM-LIST-03 | Seeds, Labs: Channels — Oracle, Qualcomm, Anthropic, DeepWeaver, Skillda, Forge, CODISSIA, SICCI, GIBA, Guidance TN, Infosys, Referral, Inbound, Event, Direct outbound. Account types — Prospect, Client, Technology partner, Channel partner, Training partner, Vendor. Task categories — Lead, Sales, Partner, Campaign, Delivery handoff, Operations. |
| FR-CRM-LIST-04 | Seeds, Academy: Channels — Inbound, Referral, Event, Direct, Webinar, Forge, Anthropic, Skillda, Guidance TN, Infosys, Nunnari Labs, LinkedIn. Account types — Prospect, Client, Training partner, Technology partner, Channel partner, Vendor. Task categories — College, Corporate training, Lead, Sales, Campaign, Marketing, Curriculum, Customer, Operations. Institution type — College, University, Corporate, Training partner, Other; Programme — empty (Q-40). |
| FR-CRM-LIST-05 | Seeds common to both: every other select list in §5.6–§5.9 with the values given there; Industry starts empty. |
| FR-CRM-LIST-06 | Per-workspace settings, Admin only: default probability per stage (§5.12), FX rates (§5.5), the domain allow-list read-only (edited by TVA admins, FR-CRM-ACC-06); *(1b)* mailboxes and labels, flag thresholds, automation switches, bookings targets. |
| FR-CRM-LIST-07 | The fields of every object are those in §5.6–§5.11 and *(1b)* §6.6. Field types needed: text, long text (plain text with markdown, Q-27), number, currency (amount and code), date, date and time, boolean, select, multi-select, rating 1–5, email (primary and additional), phone (country code, default +91, a WhatsApp marker per number), links (URL and label, primary and additional), domain, address (street, city, state, country, postal code), relation (one-to-many and many-to-many), array of text, and actor. Files are Drive links (D4). Custom fields are Phase 2. |
| FR-CRM-LIST-08 | Every record carries its workspace, created at and by, updated at and by, and a soft-delete marker; objects shown on a kanban carry a manual position. |
| FR-CRM-LIST-09 | Options that a server rule writes or reads carry a fixed system key and MAY be renamed and recoloured but MUST NOT be deactivated or removed: account types `client`, `technology_partner`, `channel_partner`, `training_partner`; task category `delivery_handoff` (Labs); every Source value; every Lost reason (Q-71). *Amended 2026-10-18 by `008` FR-TASK-CHG-11: the task status options `todo` and `in_progress` are deactivated and kept for history, the one exception to "MUST NOT be deactivated"; `open`, `waiting`, `done` and `cancelled` are the active task statuses. Nothing is removed.* |

### 5.5 Currency and FX — FR-CRM-FX

| ID | Requirement |
|---|---|
| FR-CRM-FX-01 | A deal amount is in INR, AUD or USD. Every workspace's base currency is INR. |
| FR-CRM-FX-02 | Admins set a monthly rate per currency per workspace (AUD→INR, USD→INR) in Settings (D7). Rates are audited. |
| FR-CRM-FX-03 | On every save that sets or changes a deal's amount or currency, the deal MUST store **amount in INR** at the rate for the month of the save, or the most recent earlier month with a rate. If the workspace has no rate for that currency at all, the save MUST be refused 422 naming the missing rate. INR amounts convert at 1. A later edit to a month's rate MUST NOT recompute any stored amount in INR (Q-59). |
| FR-CRM-FX-04 | Every total, sum, weighted figure and dashboard number uses amount in INR. |
| FR-CRM-FX-05 | At Closed Won the handoff MUST convert at the rate in force on the won date (same fallback and refusal as FR-CRM-FX-03), re-store the deal's amount in INR at that rate, write INR only to TVA, and record on the deal the rate and month used, so CRM bookings match the revenue pre-fill. TVA stays single-currency (`003` Q-07) (Q-59). |
| FR-CRM-FX-06 | Amounts display in their own currency with the INR figure beside them; INR follows the viewer's amount format (FR-CRM-ACC-15); AUD and USD use Western grouping. |

### 5.6 Companies — FR-CRM-CO

| Field | Type | Notes |
|---|---|---|
| Name | Text | Display field, required |
| Domain | Domain | Unique per workspace when set; used to match email |
| Additional domains | Array | For companies with several email domains; also unique |
| Entity | Multi-select | Labs only |
| Lead stage | Select (fixed) | FR-CRM-LIST-02; rules in FR-CRM-PIPE-01 to -06 |
| Account type | Select | Workspace list |
| Relationship status | Select | Active, Warm, Cold, Dormant, Churned |
| Channels | Multi-select | Workspace list |
| Owner | Person | Required; a member of the workspace |
| Next touch | Date | Nurture date |
| Industry | Select | Admin-managed |
| Institution type | Select | Academy only (Q-40) |
| Size | Select | 1-10, 11-50, 51-200, 201-1000, 1000+ |
| Country, City | Text | Blank if unknown; no defaults |
| Address | Address | |
| Website, LinkedIn | Links | |
| Description | Long text | |
| Last inbound, Last outbound, Last contact | Date and time | System-written by email sync and the importer; never edited by hand |
| Follow-up flag | Stored, computed | §6.2 *(1b)* |
| Source | Select | Manual, Import, Email sync, Campaign, Web form, API |
| Twenty id | Text | Hidden; migration only |

| ID | Requirement |
|---|---|
| FR-CRM-CO-01 | A company MUST have the fields above, with the rules in their notes. |
| FR-CRM-CO-02 | A company page MUST list its people, deals, *(1b)* campaign memberships, and for Labs its TVA projects with status and phase dates (FR-CRM-TVA-01). |
| FR-CRM-CO-03 | System-written fields MUST be refused (422) on any manual or MCP write. |

### 5.7 People — FR-CRM-PER

| Field | Type | Notes |
|---|---|---|
| First name, Last name | Text | Shown as full name |
| Company | Relation | Optional; email sync links by domain |
| Emails | Email | Primary unique per workspace |
| Phones | Phone | WhatsApp marker per number |
| Job title, City | Text | |
| LinkedIn | Links | |
| Owner | Person | |
| Last inbound, Last outbound, Last contact | Date and time | System-written |
| Do not contact | Boolean | Excludes the person from campaigns and sends |
| Twenty id | Text | Hidden |

| ID | Requirement |
|---|---|
| FR-CRM-PER-01 | A person MUST have the fields above, with the rules in their notes. |
| FR-CRM-PER-02 | *(1b)* A person marked Do not contact MUST NOT be added to a campaign; existing memberships keep their history. |

### 5.8 Opportunities — FR-CRM-OPP

| Field | Type | Notes |
|---|---|---|
| Name | Text | Labs convention "Client - what we sell"; Academy "Institution - programme" |
| Company | Relation | Required |
| Point of contact | Person | Required from Qualification |
| Other contacts | People, each with Role in deal | Role in deal (Decision maker, Champion, Influencer, Technical evaluator, Procurement, User, Other) lives on the deal-contact link only, so one person can hold a different role on each deal |
| Entity | Select | Labs only; one of Nunnari Labs, DeepWeaver, for attribution |
| Stage | Select (fixed) | §5.12 |
| Amount, Currency | Currency | INR, AUD or USD |
| Amount in INR | Stored | FR-CRM-FX-03 |
| FX rate used at win | Stored | FR-CRM-FX-05 |
| Probability | Number 0–100 | Stage default, editable (FR-CRM-PIPE-11) |
| Weighted amount | Computed | Amount in INR × probability ÷ 100 |
| Expected close date | Date | Required from Proposal |
| Next step, Next step date | Text, Date | Required while open |
| Owner | Person | Required |
| Channel | Select | One, for attribution |
| Source campaign | Relation | *(1b)* Set on campaign conversion |
| Deal type | Select | New business, Expansion, Renewal, Phase follow-on, Support extension |
| Project category | Select | Labs only; client, poc, product; carried into the TVA project |
| Engagement model | Select | Discovery phase, Fixed scope, Milestone based, Forward deployed engineering, Dedicated team, Staff augmentation, AIOps annuity, Training |
| Programme | Select | Academy only (Q-40) |
| Proposal sent on | Date | Required to enter Negotiation |
| Lost reason | Select | Price, Timing, Competitor, No budget, No response, Scope mismatch, Chose to build in-house, Other; required for Closed Lost |
| Lost notes | Long text | |
| Won on, Lost on | Date | System-written |
| Stage entered at | Date and time | System-written on every stage change |
| TVA project | Relation | Set by the integration (§5.20) |
| Twenty id | Text | Hidden |

| ID | Requirement |
|---|---|
| FR-CRM-OPP-01 | An opportunity MUST have the fields above, with the rules in their notes and in §5.12. |
| FR-CRM-OPP-02 | Every stage change MUST be kept as a history (from, to, when, who) so time in stage and days to close can be reported. |
| FR-CRM-OPP-03 | A deal's follow-up is its next step, not a task. |

### 5.9 Tasks — FR-CRM-TASK

| Field | Type | Notes |
|---|---|---|
| Title | Text | Required |
| Body | Long text | |
| Status | Select | To do, In progress, Done |
| Due date | Date | |
| Priority bucket | Select | Today, This week, This month, Later |
| Category | Select | Workspace list |
| Assignee | Person | Required; a member of the workspace |
| Linked records | Companies, people, opportunities, *(1b)* campaigns | At least one |
| Completed at | Date and time | System-written when status becomes Done |

*Amended 2026-10-18 by `008` FR-TASK-CHG-01: the CRM task table is the portal's shared `tasks` table. Status is Open, Waiting, Done, Cancelled; Body is labelled Notes; Priority bucket is labelled Bucket and is required on open and waiting tasks; Priority, Tags, Parent, Waiting on and Source are added; a Labs task may link a TVA project. The assignee of a CRM task stays a workspace member.*

| ID | Requirement |
|---|---|
| FR-CRM-TASK-01 | A task MUST have the fields above. A task with no linked record MUST be refused (422). *Amended 2026-10-18 by `008` FR-TASK-CHG-01: a task created through the CRM (its routes, `crm_add_task`, bulk "Add task", the importer, the deal lifecycle) MUST link at least one record of the workspace — a parameter of the CRM writer, not a table constraint — and no task may lose its last CRM link (`008` FR-TASK-DATA-05); a task that never had a CRM link is a personal or project task (`008`) and is not a CRM record.* *Amended 2026-10-18 by `008` FR-TASK-CHG-10: CRM routes and `crm_*` tools return the new status keys and labels; `GET /crm/tasks` with `status=open` means Open or Waiting and also accepts `waiting` and `cancelled`; the status inputs (`crm_create`, `crm_update`, `crm_list` and `GET /crm/tasks`) also accept the old keys `todo` and `in_progress` and read them as Open (TP-05).* |
| FR-CRM-TASK-02 | Tasks are for work other than a deal's next step, and for leads with no deal. |

### 5.10 Notes — FR-CRM-NOTE

| ID | Requirement |
|---|---|
| FR-CRM-NOTE-01 | A note has a body in plain text with markdown (headings, lists, checklists, links) and MAY link to many companies, people, opportunities and *(1b)* campaigns (Q-27). Images are links (D4). |
| FR-CRM-NOTE-02 | A note MAY @mention workspace members and records. *(1b)* Mentioning a member notifies them (§6.4). Mentions of non-members are refused 422, because a mention notifies its target and a non-member may receive nothing from the workspace (FR-CRM-WS-02, Q-63). |
| FR-CRM-NOTE-03 | **Log activity** records a touch that did not come through email — call, meeting, WhatsApp or other — as a note with that type and a date (Q-53). |

### 5.11 Drive links — FR-CRM-LINK

| ID | Requirement |
|---|---|
| FR-CRM-LINK-01 | Any company, person, opportunity, task, note or *(1b)* campaign MAY carry Google Drive links, each a URL and a label (D4). Typical: proposals, SOWs, signed POs, NDAs, decks. |
| FR-CRM-LINK-02 | The CRM MUST NOT accept, store or proxy file content. A link MUST be an `https://` URL; the CRM stores no preview and grants no Drive access — Drive's own sharing decides who can open it. |
| FR-CRM-LINK-03 | The record page's **Files** tab lists the record's links. |

### 5.12 Pipeline rules — FR-CRM-PIPE

These rules are enforced on the server, so the web app, imports, automations,
the API and MCP all obey them. When a rule blocks an action the refusal (422)
MUST name every missing field in one sentence, not only the first.

| ID | Requirement |
|---|---|
| FR-CRM-PIPE-01 | A new company starts at New. |
| FR-CRM-PIPE-02 | New, Contacted and Nurturing are moved by hand. *(1b)* Email sync moves New to Contacted when the first outbound email is recorded; this is part of automation 1 and is not switchable (Q-41). |
| FR-CRM-PIPE-03 | Qualified means a named need, a rough size and a next step the client agreed to (guidance shown when choosing it; not machine-checked). |
| FR-CRM-PIPE-04 | Creating an opportunity MUST require the company to be at Qualified, Deal open, Customer or Partner (never Disqualified). Only a company at Qualified moves to Deal open; a Customer or Partner keeps its lead stage, so an Expansion, Renewal or co-sell deal does not wipe it (Q-60). The same rule holds whenever a deal becomes open on a company: an open deal moved to another company (by an edit, or by a company delete that moves its deals), and a reopen from Lost, where a company the loss moved to Nurturing returns to Deal open and a Disqualified one is refused until its lead stage is set back by hand. |
| FR-CRM-PIPE-05 | When the **last open deal** on a company closes (this rule governs over the source's per-stage table, Q-60): Won sets Customer and account type `client`, unless the company is at Partner, which stays Partner with its account type. Lost moves a company at Deal open to Nurturing, or Disqualified if the person chooses it, only when the company has no won deal; a company with a won deal is set to Customer. A company at Customer or Partner is unchanged by a loss. |
| FR-CRM-PIPE-06 | Companies at Partner are excluded from funnel counts and conversion metrics. |
| FR-CRM-PIPE-07 | Entering a stage requires: **Discovery** — company, owner, next step and date; **Qualification** — point of contact; **Proposal** — amount, currency, expected close date; **Negotiation** — proposal sent on; **Closed Won** — the handoff confirmed (FR-CRM-TVA-07) in Labs; **Closed Lost** — lost reason. |
| FR-CRM-PIPE-08 | A forward move that skips stages MUST also meet the requirements of Discovery to Proposal it skips; Proposal sent on is required only to enter Negotiation. Closed Lost requires only its lost reason (Q-31). |
| FR-CRM-PIPE-09 | Side effects: Discovery moves the company to Deal open by the rule of FR-CRM-PIPE-04; Proposal creates the tentative TVA project (Labs, FR-CRM-TVA-04); Closed Won sets Won on, the company rule in FR-CRM-PIPE-05, confirms the project and creates the delivery handoff task (FR-CRM-TVA-09); Closed Lost sets Lost on, the company rule in FR-CRM-PIPE-05, archives the project and releases its allocations (FR-CRM-TVA-12), and closes the deal's open tasks with a note. These are part of automation 1 and cannot be switched off (FR-CRM-AUTO-02, Q-41). *Amended 2026-10-18 by `008` FR-TASK-CHG-04: Closed Lost sets the deal's Open and Waiting tasks to Cancelled, not Done (with the note as before), so they stop counting in Dashboard Activity and never become day-log suggestions. Tasks closed this way before the migration stay Done (TP-07).* |
| FR-CRM-PIPE-10 | Next step and next step date are required on every open deal; saving an open deal without them fails, through every channel. |
| FR-CRM-PIPE-11 | Default probabilities: Discovery 10, Qualification 25, Proposal 50, Negotiation 75, Closed Won 100, Closed Lost 0; Admin-editable per workspace. On a stage change the probability resets to the new stage's default unless the same save sets it. |
| FR-CRM-PIPE-12 | Stages may move backwards, except out of Closed Won, which only the Admin reopen of FR-CRM-PIPE-13 may do. Moving out of Closed Lost is a **reopen from Lost**: a workspace Admin or the deal's owner, audited, back to the stage the deal was lost from, with a next step and date required in the same save; in Labs it has the TVA effects of FR-CRM-TVA-14 (Q-64). |
| FR-CRM-PIPE-13 | Reopening a Closed Won deal MUST be a workspace Admin action, audited, back to Negotiation. In Labs it MUST be refused 422 while the linked project has a time entry or an invoiced milestone (`002` FR-PROJ-09), saying which; otherwise the project returns to tentative with its revenue and milestones kept (Q-33). A reopened-won deal may later be lost like any open deal (FR-CRM-TVA-13). |
| FR-CRM-PIPE-14 | Every stage change stores the time it happened (FR-CRM-OPP-02). |
| FR-CRM-PIPE-15 | A stage move and all its side effects, CRM and TVA, MUST succeed or fail as one (Q-21). |

### 5.13 Duplicates and merge — FR-CRM-DUP

| ID | Requirement |
|---|---|
| FR-CRM-DUP-01 | A company domain (including additional domains) and a person's primary email MUST be unique per workspace; a clash is refused 409 naming the existing record. |
| FR-CRM-DUP-02 | Creating a company or person, in the web app and through MCP, MUST show possible duplicates by name similarity before saving; the person may continue. The Twenty importer's dry run does the same (FR-CRM-MIG-04), and *(1b)* so does CSV import (FR-CRM-IO-03). |
| FR-CRM-DUP-03 | Basic merge, companies only: an Admin or Member picks the surviving company and a value per differing field; every person, deal, task, note, Drive link and *(1b)* email and campaign membership moves to the survivor; the merged company's id resolves to the survivor (still when that survivor is merged later) and the merged company cannot be restored from the Trash; the merge is written to both timelines and the audit. Merge of people and other objects is Phase 2. |
| FR-CRM-DUP-04 | Merging two companies each linked to TVA projects keeps both links; the TVA projects are unchanged except their company link. |

### 5.14 Trash — FR-CRM-DEL

| ID | Requirement |
|---|---|
| FR-CRM-DEL-01 | Deletion is soft everywhere. Deleted records go to a per-object Trash, restorable by Admins for 90 days, then purged by a daily job. Permanent delete before then is Admin only. Deleted records leave every view, search, total and MCP answer. |
| FR-CRM-DEL-02 | Deleting a company MUST ask what to do with its people — unlink them or delete them too — and with its deals — move them to another company of the workspace, or delete them too (a deal cannot be unlinked, since Company is required) (Q-54). Deleting a company that has any deal linked to a TVA project MUST be refused 422, naming those deals, whatever was chosen; nothing is partly deleted. Restoring the company restores what was deleted with it. Permanently purging a company sets the company link of any TVA project to empty; TVA keeps showing the project's free-text client. |
| FR-CRM-DEL-03 | Deleting a deal linked to any TVA project, tentative or confirmed, MUST be refused 422 "This deal has a TVA project; mark it Closed Lost instead." (Q-32). |
| FR-CRM-DEL-04 | A Member may soft-delete only records they own (or created, where the object has no owner) (Q-36). |

### 5.15 CRM area and navigation — FR-CRM-UI

| ID | Requirement |
|---|---|
| FR-CRM-UI-01 | The CRM is a separate full-width area at `/crm` with its own sidebar and the workspace switcher, built from the portal's existing components (D9). |
| FR-CRM-UI-02 | Sidebar: Today, Pipeline, Companies, People, Opportunities, Tasks, *(1b)* Campaigns, *(1b)* Dashboards, the person's favourites (pinned views and records), and Settings for Admins. |
| FR-CRM-UI-03 | A staff member with at least one membership MUST see a CRM link in the TVA sidebar; the CRM shows them a link back to TVA. A staff member with no membership sees neither, and `/crm` refuses them. |
| FR-CRM-UI-04 | A CRM-only person MUST land on `/crm` after sign-in and MUST never see the TVA shell or a TVA link. |
| FR-CRM-UI-05 | `/crm` MUST require a signed-in session like every portal page; nothing under it renders for an anonymous visitor. |
| FR-CRM-UI-06 | Desktop first, and fully usable in a phone browser. |
| FR-CRM-UI-07 | Dates show as "14 Oct 2026"; times in the viewer's time zone (FR-CRM-ACC-15). |

### 5.16 Views — FR-CRM-VIEW

| ID | Requirement |
|---|---|
| FR-CRM-VIEW-01 | Every object list MUST offer Table and Kanban views; Kanban groups by any select field, and dragging a card changes that value, through the same rules as any edit (a stage drag that needs more opens the stage form). Calendar view is Phase 2. |
| FR-CRM-VIEW-02 | Filters on any field, with operators suited to its type (is, is not, contains, is empty, before, after, within last N days, greater than), combined in AND and OR groups. For a Viewer, conditions on amount fields are refused (FR-CRM-ACC-11). |
| FR-CRM-VIEW-03 | Sort by several fields. Group table rows by a select field, with collapsible groups and counts. |
| FR-CRM-VIEW-04 | Choose, reorder and resize visible columns or card fields. |
| FR-CRM-VIEW-05 | Saved views: name, icon, private or shared with the workspace; a default view per object; views reorderable. Members and Admins manage shared views; Viewers save private views only (Q-37). |
| FR-CRM-VIEW-06 | Inline editing of cells and cards, obeying permissions and pipeline rules. |
| FR-CRM-VIEW-07 | Bulk actions on selected rows: edit a field, assign owner, add task, delete; *(1b)* add to campaign, export. Each record in a bulk action obeys the same rules; the result says which succeeded and why the others failed. |
| FR-CRM-VIEW-08 | Aggregates: table footers (count, sum, average, min, max) and kanban column headers (count, sum of amount in INR, weighted), all subject to FR-CRM-ACC-11. |
| FR-CRM-VIEW-09 | Search within a view; keyboard navigation (arrow keys, Enter opens, Escape closes). |
| FR-CRM-VIEW-10 | Filtering, sorting and paging happen on the server, and stay fast at 50,000 records (NFR-CRM-01). |
| FR-CRM-VIEW-11 | Seeded views per workspace: Companies — All companies (table); Lead funnel (kanban by lead stage, partners hidden); Partners and channels (table, channels not empty, grouped by account type); My accounts (table, owner is me); *(1b)* Follow-ups owed (table, flag amber or red, red first). Opportunities — Pipeline (kanban by stage, open only); Closing this month (table, open, expected close in the current month); Won and lost this quarter (table, Closed Won or Closed Lost with Won on or Lost on in the current quarter, grouped by stage). Tasks — Open tasks by owner (table grouped by assignee); My week (kanban by bucket, assignee is me). *(1b)* Campaign members — Members by status (kanban, one campaign). |

### 5.17 Record pages — FR-CRM-REC

| ID | Requirement |
|---|---|
| FR-CRM-REC-01 | Header: name, key fields as chips (stage, owner, entity, *(1b)* health dot), and actions — edit, add task, add note, log activity, move stage, merge (companies), delete. Send email is Phase 2. |
| FR-CRM-REC-02 | Left panel: every field, grouped, inline-editable, empty fields collapsible. |
| FR-CRM-REC-03 | Tabs: **Timeline** (everything that happened, newest first, filterable by type: create, field change, stage change, note, task, link, merge, handoff, *(1b)* email), **Tasks**, **Notes**, **Files** (Drive links), *(1b)* **Emails** (a company shows all its people's threads; an opportunity its company's and contacts'), **Related** (people, deals, *(1b)* campaign memberships, and for Labs companies and deals the TVA projects with status and phase dates). |
| FR-CRM-REC-04 | Every timeline entry names its actor (FR-CRM-AUD-02). |

### 5.18 Today — FR-CRM-TODAY

| ID | Requirement |
|---|---|
| FR-CRM-TODAY-01 | Today answers "what do I do today", one line per item, each linking to its record, in this order: 1 replies I owe, red first *(1b)*; 2 deal next steps due today or overdue; 3 tasks in my Today bucket or due today; 4 clients gone silent on my open deals *(1b)*; 5 my loose ends (companies I own in Nurturing with no open task and no next touch). *Amended 2026-10-18 by `008` FR-TASK-CHG-05: section 3 reads "my open or waiting tasks in my Today bucket, or due today or earlier". Today stays workspace-scoped and shows only that workspace's tasks; tasks with no workspace appear in My Tasks and the morning brief, never in CRM Today.* *Amended 18 October 2026 by `009` FR-CRMAI-CHG-03: a sixth section, 6 To review (the count of my open AI suggestions and the three oldest), shown while any AI phase is on or I have open items (`009` FR-CRMAI-REV-09, FR-CRMAI-FLAG-04); the daily digest and `008`'s morning brief carry it too.* |
| FR-CRM-TODAY-02 | Until email sync is live in the workspace, sections 1 and 4 say "Email sync is off" rather than appearing empty (Q-50). |
| FR-CRM-TODAY-03 | A workspace Admin MAY pick any member and see their Today (Q-26). |
| FR-CRM-TODAY-04 | Today is the CRM home: opening `/crm` lands on it in the last-used workspace. |

### 5.19 Command menu — FR-CRM-SRCH

| ID | Requirement |
|---|---|
| FR-CRM-SRCH-01 | Cmd or Ctrl + K anywhere in the CRM opens one search across companies, people, opportunities, tasks, notes and *(1b)* campaigns in the current workspace only. |
| FR-CRM-SRCH-02 | From it the person can jump to a record or a saved view, create a record, and run the open record's quick actions. Asking the AI is Phase 2. |
| FR-CRM-SRCH-03 | Search results obey FR-CRM-WS-02 and FR-CRM-ACC-11 and return in under 1 second at 10,000 records per object. |

### 5.20 TVA integration (Labs) — FR-CRM-TVA

TVA already has projects with category, status (confirmed or tentative),
probability, revenue (managers and admins only), phases, a lead, billing
milestones, allocations, a capacity forecast that includes tentative projects,
invoices and a monthly P&L. The CRM drives that model; it does not copy it.

| ID | Requirement |
|---|---|
| FR-CRM-TVA-01 | A TVA project MAY link to one CRM company and to the one opportunity that created it; one opportunity creates at most one project, and a project has at most one source opportunity. The project's free-text client is filled from the company name when the CRM creates or links the project, and stays editable in TVA. When the deal moves to another company (an edit or a merge), its project's company and client follow it, with a TVA audit row. The name and client the CRM copies into a project become TVA data and follow TVA visibility; nothing else crosses (Q-38). |
| FR-CRM-TVA-02 | A CRM company page lists its TVA projects (FR-CRM-CO-02). A TVA project page shows its company and source deal (name, owner, won date) **only to members of the Labs workspace**; to anyone else it shows nothing of the CRM. No TVA page shows a deal amount (Q-38). |
| FR-CRM-TVA-03 | When the person moving a deal may not make the TVA change themselves, the CRM MUST make it as the `system` actor stamped with that person (D8). The system actor performs only the effects in this section, never anything else, and only revenue and milestones remain gated on the person's own TVA role (FR-CRM-TVA-08). |
| FR-CRM-TVA-04 | **Enters Proposal**: if the deal has no project, create a tentative project — name from the deal, client and company link, category from Project category (`client` when empty, Q-52), probability from the deal, no revenue. If it already has one, do nothing. |
| FR-CRM-TVA-05 | If the new project's name clashes with an existing project (`002` uniqueness, archived included), the move MUST be refused 409 naming the clashing project and offering two ways on: rename the deal (or give the project a different name), or link the deal to that project. Linking is allowed only to a project that is tentative, not archived and not linked to another opportunity (D14, Q-42), and only by a staff account that is a TVA manager or admin; anyone else is told to ask a TVA manager to link it, since a link lets a later Closed Lost archive that project and release its people. |
| FR-CRM-TVA-06 | **While open**: probability changes, including the stage-default reset of FR-CRM-PIPE-11, update the linked tentative project's probability; expected close and amount changes have no TVA effect. A deal moved back out of Proposal keeps its project, with its probability kept in sync. While the deal is open, revenue on its tentative project is set in TVA by TVA managers and admins as today (Q-70). |
| FR-CRM-TVA-07 | **Closed Won** opens the handoff, shown in the web app and offered by `crm_move_stage` as a preview first. Inputs, pre-filled where possible: project name and category; project lead (an active staff member); delivery phase start and end dates and an optional support phase, mapped onto TVA's `delivery` and `support` phases, pre-filled from the project's existing phases and creating or updating them (one per kind, `002` FR-PROJ-02); an optional `pre` phase is offered when the engagement model is Discovery phase; the CRM never sets `spillover`; revenue, pre-filled with amount in INR at the won-date rate (FR-CRM-FX-05), with the tentative project's existing TVA revenue shown beside it when one is set, to TVA managers and admins only (`003`: revenue is visible to them alone); revenue and milestones are in INR; billing milestones (name, due date, amount), which must add up to revenue or show the difference; the milestones confirmed are the project's full set (the existing ones pre-filled, matched by id), so confirming never duplicates one, and an invoiced milestone is never changed or removed by the handoff. The deal is Closed Won only when the handoff is confirmed; cancelling leaves the stage unchanged. |
| FR-CRM-TVA-08 | Revenue and milestones MUST be written only when the confirming person is a TVA manager or admin. Otherwise the project is confirmed without revenue or milestones and a task "Confirm revenue and milestones", linked to the deal, is created for each Labs workspace Admin who is a TVA manager or admin, or for every Labs workspace Admin when none is. The task says the change is made in TVA Projects by a TVA manager or admin (D8, Q-09). |
| FR-CRM-TVA-09 | On confirm: the project becomes confirmed with its lead and phases (and revenue and milestones per FR-CRM-TVA-08); a timeline event is written on the deal, and on the project the TVA audit entry stands for the source's project timeline event, which TVA does not have (Q-57); a "Delivery handoff" task (category `delivery_handoff`) to schedule kickoff goes to the project lead when the lead is a Labs member of any role — a Viewer may mark it Done (§3.3) — or otherwise to the deal owner, naming the lead (Q-57). Part of automation 1, not switchable. |
| FR-CRM-TVA-10 | **Skipping Proposal**: a deal moved straight to Closed Won with no project gets one created directly as confirmed through the same handoff (with FR-CRM-TVA-05 on a clash: rename, or a staff TVA manager or admin links the deal to the existing unlinked, unarchived tentative project, which the handoff then confirms). |
| FR-CRM-TVA-11 | Revenue and milestone changes happen in TVA, not on the deal: on a tentative linked project by TVA managers and admins as today (FR-CRM-TVA-06), and after Closed Won likewise. The P&L reads TVA only; the CRM reports pipeline and bookings, never recognised revenue. |
| FR-CRM-TVA-12 | **Closed Lost**: archive the linked tentative project and mark each of its allocations released, recording when, by whom and why, without deleting them (D5). Released allocations and the archived project stop counting everywhere (FR-CRM-CHG-03, -04). |
| FR-CRM-TVA-13 | A won deal reaches Closed Lost only after an Admin reopen (FR-CRM-PIPE-13), which has already returned its project to tentative. Losing it then archives that project and releases its allocations as FR-CRM-TVA-12; its revenue and milestones are kept but stop counting, as on every archived project (FR-CRM-CHG-04) (Q-64). |
| FR-CRM-TVA-14 | **Reopened from Lost**: unarchive the project the deal archived and un-release exactly the allocations that loss released, as they were (D5). An allocation that now overlaps other work, or belongs to someone since deactivated, is restored as it was and shows through TVA's existing over-allocation and deactivation rules (Q-43). |
| FR-CRM-TVA-15 | While the deal is open or Closed Lost, a linked project's status, probability and archived state change only through its deal (D15). Once the project is confirmed by a win, archive and unarchive happen in TVA as today (lead, manager, admin; `002` FR-PROJ-04), and the CRM shows the archived state read-only; status and probability stay CRM-controlled (Q-16). Phases, allocations, lead, revenue and milestones stay editable in TVA by whoever may edit them today (FR-CRM-TVA-06, -11). |
| FR-CRM-TVA-16 | Follow-on work (Phase follow-on, Support extension) is a new opportunity creating a new project, as FluxBooks Phase 2 and ARB Phase 1 Extended Support exist today. |
| FR-CRM-TVA-17 | Deal amounts appear only on CRM pages and to CRM roles allowed to see them. TVA's rule that finance stays off team-facing pages is unchanged. |
| FR-CRM-TVA-18 | **Link TVA project** (Labs Admin; linking an existing project also needs a staff TVA manager or admin, FR-CRM-TVA-05; audited, atomic): an open deal at Proposal or later with no project MAY be linked to an unlinked, non-archived tentative project, or MAY create one as FR-CRM-TVA-04; a Closed Won deal with no project MAY be linked to an unlinked confirmed project, without changing that project's status, revenue, phases or milestones. It is how imported deals reach their projects (FR-CRM-MIG-06), and is offered in the web app and by `crm_handoff` in link mode (Q-65). |

### 5.21 Capacity check (Labs) — FR-CRM-CAP

| ID | Requirement |
|---|---|
| FR-CRM-CAP-01 | For the next 90 days, by week, the CRM shows the hours tentative projects would need (demand from their non-released allocations) against free capacity (weekly supply less confirmed demand), both from the shared capacity service of FR-CRM-CHG-17, so staffing problems are visible before a deal is won. |
| FR-CRM-CAP-02 | It shows totals only, never a person, and is visible to Labs Admins and staff Members only — not Viewers and not `crm_only` accounts. A week's figure is hidden ("fewer than 3 people") when fewer than 3 people contribute to its supply, so one person's leave cannot be read from it (Q-39). It is reachable from Pipeline in 1a and *(1b)* sits on the Labs dashboard. |
| FR-CRM-CAP-03 | Its figures MUST come from the same service as TVA's forecast (FR-CRM-CHG-17), so the two never disagree. |

### 5.22 Twenty importer — FR-CRM-MIG

| ID | Requirement |
|---|---|
| FR-CRM-MIG-01 | An Admin can import a Twenty export (API JSON preferred, CSV accepted) into their workspace: companies, people, opportunities, tasks, notes and their links; *(1b)* campaigns and campaign members (Q-49). Both Twenty workspaces map to their CRM workspaces. *Amended 2026-10-18 by `008` FR-TASK-CHG-09: the Twenty importer maps the task statuses To do and In progress to Open.* |
| FR-CRM-MIG-02 | Stages map NEW→Discovery, SCREENING→Qualification, PROPOSAL→Proposal, NEGOTIATION→Negotiation, CUSTOMER→Closed Won, LOST→Closed Lost. Each record keeps its Twenty id. Last inbound and Last outbound are seeded from Twenty's people timestamps. Message bodies are not imported. |
| FR-CRM-MIG-03 | Twenty attachments are not imported as files (D4); the dry run lists them per record so they can be re-linked from Drive (Q-34). |
| FR-CRM-MIG-04 | Every import starts as a dry run reporting counts per object, the lead stage derived for each company (FR-CRM-MIG-05), and every conflict and rule violation per row (duplicate domain or email, possible duplicate by name similarity as FR-CRM-DUP-02, open deal with no next step, deal on a company below Qualified, unknown owner). The real run is offered only after a dry run of the same file. |
| FR-CRM-MIG-05 | Lead stages of imported companies follow the pipeline rules, first match wins: account type Technology, Channel or Training partner → Partner; any open deal → Deal open; any won deal → Customer; only lost deals → Nurturing; no deals → Twenty's own company stage where the export has one and it maps to a lead stage, otherwise New (Q-55). Owners map to members by email, else the importing Admin. An opt-in fix-up gives open deals with no next step "Review after import", dated the import day; the report counts its use (Q-46). |
| FR-CRM-MIG-06 | Imported Labs deals already at Proposal or later do not create TVA projects; an Admin links or creates them later with Link TVA project (FR-CRM-TVA-18, Q-24). |
| FR-CRM-MIG-07 | Re-running a real import with the same file MUST change nothing (matched on Twenty id). |
| FR-CRM-MIG-08 | The real run waits for the owner to supply the exports; the importer is built and tested on fixtures. |

### 5.23 MCP — FR-CRM-MCP

| ID | Requirement |
|---|---|
| FR-CRM-MCP-01 | The NLDW MCP server MUST offer these tools, each acting as the token's owner through the same CRM routes and rules as the web app (D6). **1a**: `crm_workspaces`, `crm_today`, `crm_search`, `crm_get`, `crm_list`, `crm_create`, `crm_update`, `crm_move_stage`, `crm_handoff`, `crm_set_next_step`, `crm_add_task`, `crm_complete_task`, `crm_add_note`, `crm_merge`, `crm_import` (Twenty files). **1b**: `crm_campaign_members`, `crm_dashboard`, `crm_run_email_sync`; `crm_import` gains CSV object imports. That is 18 tools (Q-07). `crm_audit` is Phase 2. *Amended 18 October 2026 by `009` FR-CRMAI-CHG-04: three more tools, `crm_review_list`, `crm_review_decide` and `crm_thread_summary`, make 21 `crm_*` tools (`009` FR-CRMAI-MCP-01).* |
| FR-CRM-MCP-02 | Tool purposes as source §15.3: workspaces and roles; Today (another member's for Admins); one search; any record with fields, related records, recent timeline, *(1b)* recent emails and health; records by filter, sort, limit or saved view name; create and update any object; move a deal, returning a TVA project preview at Proposal and Closed Won; confirm a handoff, or link a deal to a TVA project (FR-CRM-TVA-18); next step and date; tasks with bucket and category; notes on one or more records; campaign members add, status, convert; dashboard numbers with colours; run sync now and report; merge two companies; dry-run or run an import. |
| FR-CRM-MCP-03 | Every tool takes an optional workspace. Reads default to the caller's last-used workspace. |
| FR-CRM-MCP-04 | A write by a member of more than one workspace that names no workspace MUST be refused with a message listing their workspaces (D13). |
| FR-CRM-MCP-05 | Every `crm_*` tool that changes something MUST say CONFIRM FIRST and be marked as a write, as every TVA write tool (`004` FR-MCP-04, unchanged). Descriptions state who may call in capitals, as FR-CRM-MCP-11 gives them. |
| FR-CRM-MCP-06 | Tools return ISO dates (YYYY-MM-DD) and decimal strings, as every NLDW tool; timestamps carry their offset. Stages, lead stages and system options are passed and returned by their fixed keys, with the display name beside them (FR-CRM-LIST-02, -09). Text written by people is data (`004` FR-MCP-05). |
| FR-CRM-MCP-07 | Tools obey FR-CRM-WS-02 and FR-CRM-ACC-11; nothing is redacted in the MCP layer, the routes do it. |
| FR-CRM-MCP-08 | `crm_import` takes the file content as text in the call, within the same 4 MB limit as web uploads (FR-CRM-CHG-08, Q-48). |
| FR-CRM-MCP-09 | The server's instructions MUST describe the CRM, its workspaces and roles alongside the portal. |
| FR-CRM-MCP-10 | **Coverage** (Q-58). `crm_create` and `crm_update` cover companies, people, opportunities (with their contacts and Role in deal), tasks, notes (including Log activity's typed notes, FR-CRM-NOTE-03), Drive links on any of them and *(1b)* campaigns; `crm_campaign_members` covers members. `crm_list` and `crm_get` read the same objects, and `crm_list` runs any saved view the caller can see by name. **Web-only in Phase 1**: soft delete, restore and permanent delete; settings, select lists, FX rates, allow-lists, *(1b)* mailboxes, flag thresholds, automation switches and bookings targets; invitations and memberships; creating or editing saved views and favourites; bulk actions; *(1b)* CSV and full-workspace export. |
| FR-CRM-MCP-11 | **Who may call.** `crm_workspaces`, `crm_today` (own), `crm_search`, `crm_get`, `crm_list`: ANY MEMBER OF THE WORKSPACE (Admin, Member, Viewer). `crm_today` for another person: WORKSPACE ADMINS ONLY. `crm_create`, `crm_update`, `crm_move_stage`, `crm_handoff`, `crm_set_next_step`, `crm_add_task`, `crm_add_note`, `crm_merge`, `crm_campaign_members`: WORKSPACE ADMINS AND MEMBERS. `crm_handoff` in link mode (FR-CRM-TVA-18): WORKSPACE ADMINS WHO ARE TVA MANAGERS OR ADMINS, LABS. `crm_complete_task`: ADMINS AND MEMBERS, OR A VIEWER ON A TASK ASSIGNED TO THEM. `crm_dashboard`: WORKSPACE ADMINS, AND MEMBERS FOR THEIR OWN NUMBERS. `crm_import`, `crm_run_email_sync`: WORKSPACE ADMINS ONLY (Q-62). |

---

## 6. Functional requirements — Phase 1b

### 6.1 Gmail label sync — FR-CRM-SYNC

| ID | Requirement |
|---|---|
| FR-CRM-SYNC-01 | Sources are Gmail mailboxes listed per workspace in Settings. Labs starts with navaneeth@nunnarilabs.com and navaneeth@deepweaver.ai; Academy with the mailboxes Sanjeev nominates. Each mailbox MUST name an owner who is a member of the workspace (chosen in the mailbox setting); a mailbox without one is refused 422. Because the service account can read any mailbox in the Google Workspace, a mailbox's address MUST be its owner's own portal email unless a TVA admin registers it (a delegate address such as navaneeth@deepweaver.ai); its domain MUST be one the service account covers (FR-CRM-SYNC-03); Gmail system labels (Inbox, Sent, categories and the like) MUST be refused; and one address MAY be registered in one workspace only. |
| FR-CRM-SYNC-02 | Labels synced are configurable per workspace. Labs: Sales, Leads. Academy: academy/course, academy/course/college. *Amended 20 October 2026 by `010` FR-CAP-CHG-01: a mailbox syncs its configured labels and, with capture source Primary and Sent, Gmail's Primary category (`CATEGORY_PERSONAL`) and Sent mail, in neither Trash nor Spam (`010` FR-CAP-SRC-01, -02). Labels only, the default, is this requirement unchanged.* |
| FR-CRM-SYNC-03 | Access is a Google Workspace service account with domain-wide delegation and the read-only Gmail scope, per Google Workspace (nunnarilabs.com, deepweaver.ai, nunnari.academy), held as a Railway secret. Sync stays switched off until that credential exists (Q-23). |
| FR-CRM-SYNC-04 | Every 15 minutes, incrementally per mailbox; also on demand from the web app and `crm_run_email_sync`, Admins only, because a run reads every listed mailbox and mailboxes are Admin settings (Q-62). At most one run at a time, on its own Celery queue. A run is split into short tasks, each one page of one mailbox, each within a per-task time limit below the worker's 10-minute limit and resumable from the last page done. The queue is served by a dedicated Celery worker service (concurrency 1) that is started only when sync is switched on, so sync never takes the shared worker's slots from leave notifications (Q-74). |
| FR-CRM-SYNC-05 | "Our domains" are the workspace's allow-list domains (FR-CRM-ACC-06). For each labelled message: skip it if every participant is in our domains; outbound if the sender is in our domains; match outside participants to people by email, then companies by domain; create unknown companies at New and people as needed, owned by the mailbox owner, Source Email sync; store it (FR-CRM-SYNC-06); update Last inbound, Last outbound, Last contact; recompute flags. *Amended 20 October 2026 by `010` FR-CAP-CHG-02: automation 2 creates companies and people only from messages that carry a configured label. A message reached only through Primary or Sent is skipped when it is bulk or automated, is stored and linked as above when an outside participant is a known person or company, and otherwise is not stored and marks its conversation a discovery candidate (`010` FR-CAP-SRC-03 to -05).* |
| FR-CRM-SYNC-06 | Phase 1 stores subject and metadata (participants, direction, sent at, mailbox, thread, links to people, companies and opportunities), not bodies (Q-29). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-01: bodies are still not stored; beside the metadata the CRM stores an AI summary of the thread, written from the stored messages' text read in memory by the summariser only (`009` FR-CRMAI-SUM-03, -04).* |
| FR-CRM-SYNC-07 | Idempotent: re-running never duplicates a message. |
| FR-CRM-SYNC-08 | First connection backfills the last 90 days, in the same resumable page-sized tasks as FR-CRM-SYNC-04. *Amended 20 October 2026 by `010` FR-CAP-CHG-03: for a mailbox on Primary and Sent, the backfill starts at the mailbox's capture start date (default 1 April 2026), not 90 days back (`010` FR-CAP-SRC-07).* |
| FR-CRM-SYNC-09 | Removing the label from a message removes the stored copy on the next run, and its effect on flags. |
| FR-CRM-SYNC-10 | Email records are visible only to members of their workspace. |

### 6.2 Follow-up and deal-health flags — FR-CRM-FLAG

| Flag | Applies to | Red | Amber |
|---|---|---|---|
| Reply owed | Person, company, deal | Their latest email is newer than ours and older than 24 h | Same, 12–24 h |
| Client silent | Open deal | No inbound for more than 3 days after our last outbound | 2–3 days |
| Next step | Open deal | Next step date past | Today or tomorrow |
| Close date | Open deal | Expected close past | Within 7 days |
| Stale deal | Open deal | No stage change, note, task or email for 21 days | 14–21 days |
| Loose end | Company in Nurturing | No open task and no next touch | — |
| Task overdue | Task | Due date past | Due today |

*Amended 18 October 2026 by `009` FR-CRMAI-CHG-05: a Stuck in stage flag on open deals, amber when time in the current stage reaches that stage's limit and red at twice it, counted toward flag levels, Follow-ups owed and the flag counts while R-2 is on; the R-1 and R-3 day counts join the thresholds (`009` FR-CRMAI-RULE-01, -03).*

| ID | Requirement |
|---|---|
| FR-CRM-FLAG-01 | Flags MUST follow the table, with thresholds as workspace settings defaulting to those values. |
| FR-CRM-FLAG-02 | Flags are stored, recomputed on every relevant change and by a job at least every 30 minutes; each shows when it was last computed (Q-20). |
| FR-CRM-FLAG-03 | A deal's health is its worst flag; a company's is the worst across its open deals and its own flags. |
| FR-CRM-FLAG-04 | Internal-only emails never affect flags; auto-replies and bounces are ignored where detectable. |
| FR-CRM-FLAG-05 | Shown as a dot with a label on hover and in tables, filterable and sortable; colour is never the only signal. UI copy says "follow-up" or "deal health", never bare "health", to stay apart from TVA project health. |

### 6.3 Job log — FR-CRM-JOB

| ID | Requirement |
|---|---|
| FR-CRM-JOB-01 | Every CRM background run (sync, flags, digests, reminders, trash purge, automations) MUST be recorded: job, workspace, start, end, outcome, counts, error. Admins see their workspace's runs. TVA admins see every workspace's run metadata (job, start, end, outcome, counts) as account administration (Q-69); error text shown to a TVA admin who is not a member of that workspace MUST be stripped of record content (names, email addresses, subjects). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-07: the job list gains `summaries`, `meetings`, `enrichment`, `ai_fields`, `review_expiry` and `followup_drafts`; the `automations` job is labelled "Automations" and checks each switch separately, recorded `skipped` only when every one is off (`009` FR-CRMAI-JOB-01, -05).* |
| FR-CRM-JOB-02 | Three consecutive failures of a job MUST alert the workspace Admins and TVA admins in-app and through the notification channel. |
| FR-CRM-JOB-03 | Staleness is checked on read, since a stopped scheduler cannot alert about itself: every CRM page load and every `crm_today` call checks the job log, and shows Admins a banner naming any job more than twice its interval overdue. A `/health/jobs` endpoint reports the same, without record content, for an external uptime monitor (Q-72). |

### 6.4 Notifications and digests — FR-CRM-NOTIF

| ID | Requirement |
|---|---|
| FR-CRM-NOTIF-01 | An in-app bell in the CRM with unread state. Events: assigned a task or record; mentioned in a note; a deal I own changed stage by someone else; a reply owed turned red; a handoff or revenue confirmation waiting for me. The bell polls a backend route through the proxy, every 60 seconds and when the window regains focus; no Realtime and no browser grants (FR-CRM-ACC-10). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-08: new bell events: a deal I own turned Stuck in stage (owner), and AI budget reached (the workspace's Admins) (`009` FR-CRMAI-RULE-04, FR-CRMAI-LLM-08).* |
| FR-CRM-NOTIF-02 | Out-of-app messages go through the portal's existing channel, Slack DM first, email fallback (Q-18). CRM-only people are generally not in Nunnari's Slack, so for them email is the only out-of-app path, and it needs a production SMTP sender (§14). Until one is configured they get the in-app bell only, and each job run logs an "undeliverable" count (Q-73). |
| FR-CRM-NOTIF-03 | Daily digest at 09:00 in the person's time zone: their Today list. Exactly once per person per day, however often the job runs. For a `crm_only` recipient it is sent only once a production SMTP sender exists (FR-CRM-NOTIF-02). |
| FR-CRM-NOTIF-04 | Weekly digest, Monday 09:00 in each recipient's time zone, to each workspace's Admins: pipeline movement, won and lost, flag counts. Sent separately from TVA's leadership digest (Q-19), exactly once. |
| FR-CRM-NOTIF-05 | Digests obey FR-CRM-WS-02 and FR-CRM-ACC-11. |

### 6.5 Built-in automations — FR-CRM-AUTO

| ID | Requirement |
|---|---|
| FR-CRM-AUTO-01 | The built-in automations are: 1 stage side effects and TVA lifecycle (§5.12, §5.20), which includes 3, 4, 9 and 10 below; 2 create company and person from synced email; 3 New to Contacted on first outbound; 4 flag computation; 5 stale-deal notice to the owner, and to Admins at red; 6 daily and weekly digests; 7 task due reminders, in-app on the due date and a message when overdue; 8 campaign conversion prompts for a deal and writes attribution; 9 Closed Lost closes the deal's open tasks with a note; 10 Closed Won creates the delivery handoff task. Automation 7's overdue message to a `crm_only` recipient waits for a production SMTP sender (FR-CRM-NOTIF-02). *Amended 2026-10-18 by `008` FR-TASK-CHG-04: automation 9 sets the deal's Open and Waiting tasks to Cancelled, not Done (TP-07).* *Amended 18 October 2026 by `009` FR-CRMAI-CHG-06: four switchable automations follow the ten: 11 R-1 no reply after outreach, 12 R-2 Stuck in stage, 13 R-3 lead gone cold, 14 R-4 campaign follow-through, seeded off (`009` FR-CRMAI-RULE-01).* |
| FR-CRM-AUTO-02 | Automations 2, 5, 6, 7 and 8 each have an on/off switch and their thresholds in workspace Settings, and log their runs (FR-CRM-JOB-01). Automation 1 is the pipeline rules themselves and MUST NOT be switchable, and neither are 3, 4, 9 and 10, which are parts of it (FR-CRM-PIPE-02, -09, FR-CRM-TVA-09, FR-CRM-FLAG-02) and ship with their rules: 9 and 10 in 1a, 3 and 4 with sync and flags in 1b (Q-41). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-06: automations 11 to 14 each have a switch and their parameters in Settings; automation 4 stays unswitchable apart from the Stuck in stage flag, which is computed only while R-2 and `009`'s phase 3 are on (`009` FR-CRMAI-RULE-03).* |

### 6.6 Campaigns — FR-CRM-CAMP

| Campaign field | Type | Notes |
|---|---|---|
| Name | Text | |
| Type | Select | Webinar, Email sequence, Event or roundtable, LinkedIn or content, Partner co-sell, College outreach, Newsletter |
| Status | Select | Planned, Live, Completed, Cancelled |
| Channel | Select | |
| Entity | Select | Labs only; one of Nunnari Labs, DeepWeaver, as for opportunities (Q-61) |
| Owner | Person | |
| Start date, End date | Date | |
| Goal | Text | |
| Budget | Currency | Amount field (FR-CRM-ACC-11) |
| Converted deals, Pipeline created, Won value | Computed | From members' converted opportunities |

| Member field | Type | Notes |
|---|---|---|
| Campaign | Relation | Required |
| Company, Person | Relation | At least one |
| Status | Select | Targeted, Invited, Registered, Attended, Engaged, Meeting booked, Converted, Not interested, Unsubscribed |
| Last touch | Date | |
| Notes | Long text | |
| Converted opportunity | Relation | Set when status becomes Converted |

| ID | Requirement |
|---|---|
| FR-CRM-CAMP-01 | Campaigns and members have the fields above. |
| FR-CRM-CAMP-02 | A campaign's member board is a kanban by status: drag members through statuses, set last touch, add notes. |
| FR-CRM-CAMP-03 | Members are added from any company or people view (bulk action), by CSV, or by a saved filter, respecting Do not contact. |
| FR-CRM-CAMP-04 | Moving a member to Converted asks for a new or existing opportunity and sets Converted opportunity on the member and Source campaign on the deal. |
| FR-CRM-CAMP-05 | The campaign page shows member counts by status, conversion rate, pipeline created, won value and, when a budget is set, cost per converted member. |
| FR-CRM-CAMP-06 | Seeded from Twenty by the importer: CODISSIA AI for Manufacturing Roundtable (Labs); Academy weekly webinars Oct 2026–Jan 2027, Saturdays 19:00–21:00 (Academy); College MoU and course outreach (Academy). |
| FR-CRM-CAMP-07 | Sends happen outside the CRM; outcomes are recorded on members. Nurturing outside campaigns uses tasks and next touch. |

### 6.7 Dashboards — FR-CRM-DASH

| ID | Requirement |
|---|---|
| FR-CRM-DASH-01 | One fixed dashboard per workspace, for Admins, and for Members showing their own numbers. Viewers have none. Filters: entity (Labs), owner, channel, date range. Green, amber and red where a threshold applies. |
| FR-CRM-DASH-02 | Panels: open pipeline by stage (count, amount in INR, weighted); bookings won this month, quarter and financial year (April–March) against an Admin-set target per workspace and per entity (no colour when no target); won and lost this quarter with lost reasons; win rate and average deal size by entity and channel; average time in each stage and days to close; flag counts (replies owed, silent clients, overdue next steps, overdue close dates, stale deals, loose ends); lead funnel per lead stage, partners excluded, with stage conversion; pipeline by channel and by source campaign; activity — emails in and out, notes and tasks per owner per week (Q-26); Labs only, the capacity check (§5.21). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-05: the flag counts gain "stuck in stage" while R-2 is on (`009` FR-CRMAI-RULE-03).* |
| FR-CRM-DASH-03 | Academy: the same without entity and capacity, plus pipeline by institution type and by programme (Q-40). |
| FR-CRM-DASH-04 | Per-owner figures sort by name, never by value, so the per-owner panel is not a league table (Q-26). |

### 6.8 CSV import and export — FR-CRM-IO

| ID | Requirement |
|---|---|
| FR-CRM-IO-01 | CSV import per object (Admins): column-to-field mapping, saved mappings, preview, per-row validation errors fixable in the web app before confirming, relations by name, domain or email, and update-existing mode. XLSX is not supported in Phase 1 (D4b). |
| FR-CRM-IO-02 | Uploads up to 4 MB travel through the portal's proxy (FR-CRM-CHG-08); parsing happens in the backend (D4b). |
| FR-CRM-IO-03 | Duplicate detection on domain and email before anything is written, with skip, update or create per row, and possible duplicates by name similarity shown as warnings (FR-CRM-DUP-02). Every pipeline rule applies to imported rows. |
| FR-CRM-IO-04 | Any view exports to CSV with its current filters and columns (Admins and Members). Full workspace export, all objects, JSON, for Admins. Every export is audited. |
| FR-CRM-IO-05 | Downloads keep their file type and name (FR-CRM-CHG-08). |

---

## 7. Changes the CRM forces on existing TVA behaviour — FR-CRM-CHG

Each amends the spec named. Everything else in `001`–`006` stands.

| ID | Requirement |
|---|---|
| FR-CRM-CHG-01 | **CRM-only refusal.** Every TVA page, route and MCP tool MUST refuse a `crm_only` account (403/404), including the self-service routes (bookings, time, comp-off, Home). The only non-CRM routes a `crm_only` account may reach form a closed **shared allow-list** (Q-56): `/me` in a reduced form (id, name, account type, CRM memberships — nothing TVA); personal-token list, create and revoke (`004`); OAuth authorize, consent and token (`006` FR-OAUTH, `/auth/connect`); and sign-out. A test MUST enumerate every route outside `/api/v1/crm/` and assert the refusal for each one not on the allow-list, which the test holds as a constant so that adding to it is a visible change. Amends `001` FR-AUTH (a signed-in person need not be an employee) and `004` §2 G-1. *Amended 2026-10-18 by `008` FR-TASK-CHG-02: the shared allow-list gains the task routes (`/api/v1/tasks/`) by prefix, and the route-enumerating test's allow-list constant changes visibly. A `crm_only` caller there sees no TVA data (`008` FR-TASK-VIS-07). Day-log suggestions stay on the TVA timesheet routes and remain refused to them.* |
| FR-CRM-CHG-02 | **CRM-only exclusion.** Every TVA people population MUST leave CRM-only accounts out: people lists, bench, resources, allocatable people, utilisation, team day, the timesheet population, coverage, nudges, sign-off, the morning who-is-out post, the leadership and CEO dashboards and digests, CTC-missing lists, leave balances, approvals and checklists. They cannot be allocated, be a project lead, or log time: this amends `002` FR-PROJ-07 (a project lead MUST be an active staff account; otherwise 422 "A CRM-only account cannot lead a project.") and `002` FR-ALLOC / `003` FR-ROLE-08 (allocatable people are staff only; otherwise 422 "A CRM-only account cannot be allocated."), in the lead picker, the allocation routes and `update_project`. Two exceptions: the TVA admin People list shows `crm_only` accounts marked "CRM only" (leaving them out of every count), and the TVA Admin "CRM workspaces" panel lists them (FR-CRM-CHG-11). |
| FR-CRM-CHG-03 | **Released allocations.** An allocation can be marked released (when, by whom, why) and un-released. The **allocation readers** are: the forecast, resources timeline, bench, over-allocation, hiring signal, utilisation planned hours, planned cost and the P&L, project health, the CEO dashboard, digests, Home, the allocatable-people picker and the CRM capacity check. Every allocation reader MUST ignore released allocations. Amends `002` FR-ALLOC and FR-PROJ-08, `003` FR-RES, `005` FR-PNL, `006` FR-UTIL-03, FR-HIRE, FR-DIGEST, `003` FR-DASH. |
| FR-CRM-CHG-04 | **Archived projects stop counting as busy.** Allocations on an archived project MUST NOT count in any allocation reader (the list in FR-CRM-CHG-03), and an archived tentative project MUST drop out of the P&L pipeline block (`005` FR-PNL-05) and the CEO dashboard pipeline. Logged hours and invoices on archived projects are unchanged (`006` FR-MILE-07). This applies to every archived project, not only CRM ones (D5). |
| FR-CRM-CHG-05 | **CRM-controlled status.** For a project linked to an opportunity, the Projects page and MCP `update_project` MUST refuse changes to status and probability, and to archived state while the project is tentative (422 "Managed by CRM deal *name*." — the deal name shown only to Labs members, FR-CRM-TVA-02), and the page shows those fields read-only. A confirmed linked project archives and unarchives in TVA as today (FR-CRM-TVA-15). Amends `002` FR-PROJ-08 ("whoever may edit a project sets both"). |
| FR-CRM-CHG-06 | **Unarchive.** A project archived by a Closed Lost is restored by the deal's reopen (FR-CRM-TVA-14); no new TVA unarchive control is added. |
| FR-CRM-CHG-07 | **Spec `004` FR-MCP-01 amended**: "Every API operation a person can perform in the web app MUST have a tool, **except CRM routes, which are covered by the `crm_*` tools of `007` FR-CRM-MCP-01**." The `004` MCP coverage test (tools equal operations less the excluded set) MUST exclude every `/api/v1/crm/*` operation by prefix, and a separate test MUST check that exactly the 18 `crm_*` tools of FR-CRM-MCP-01 exist. Spec `004` §8 ("a tool never has more access than the person") still holds: the system actor of FR-CRM-TVA-03 acts identically for the web app and MCP. *Amended 18 October 2026 by `009` FR-CRMAI-CHG-04: the separate test checks that exactly the 21 `crm_*` tools of FR-CRM-MCP-01 as amended exist; `004` FR-MCP-01's CRM carve-out wording is unchanged.* |
| FR-CRM-CHG-08 | **Proxy** *(1a — the Twenty importer needs it)*. The portal's proxy MUST carry multipart uploads up to 4 MB and binary and CSV downloads, keeping Content-Type and Content-Disposition. 4 MB leaves headroom under Netlify's function request-body limit (about 6 MB, and a binary body may be base64-encoded, a third larger); the plan confirms it with an upload test (Q-05). This also fixes the effort-statement CSV download (`006` FR-STMT-02). It remains the only path from browser to backend. |
| FR-CRM-CHG-09 | **Audit actor widening.** The audit log gains a workspace, the actor kinds `import` and `sync` beside `user` and `system`, and a channel (web or MCP, Q-22); a `system` entry made for a person records that person. The append-only rule is unchanged. TVA audit views exclude CRM rows (FR-CRM-AUD-04). |
| FR-CRM-CHG-10 | **Projects link to the CRM** (FR-CRM-TVA-01): company and source opportunity, with the one-to-one rule. Existing projects stay unlinked. Project name uniqueness is unchanged; the CRM refuses clashes (FR-CRM-TVA-05). |
| FR-CRM-CHG-11 | **Shell and sign-in.** The TVA sidebar gains the CRM link (FR-CRM-UI-03); sign-in sends CRM-only people to `/crm` (FR-CRM-UI-04); `/crm` is a protected route. TVA Admin gains a **CRM workspaces** panel — workspace list, members, invite, remove, account type — so a TVA admin who is not a member can do FR-CRM-ACC-01 and -13 without opening `/crm`. |
| FR-CRM-CHG-12 | **Time zone exception.** A per-person time zone exists for CRM display and CRM digests only. `001` NFR-03 is unchanged for every TVA rule, lock and calendar date. *Amended 2026-10-18 by `008` FR-TASK-CHG-06: the per-person time zone also decides "today" and the current week in My Tasks and quick add, the overdue count and the morning brief. Day-log suggestions use Asia/Kolkata, as every TVA rule (`001` NFR-03).* |
| FR-CRM-CHG-13 | **No-ranking exception.** Per-owner activity counts on the CRM dashboard and an Admin viewing a member's Today are allowed for sales work only (Q-26). `002` §10, `003` §9 and `006` §16 are unchanged for TVA data, and no CRM view joins TVA hours, leave or cost to a seller. |
| FR-CRM-CHG-14 | **Account creation.** Workspace Admins may create CRM-only accounts by invitation (FR-CRM-ACC-04/05). Amends `001` FR-AUTH-02/03 ("by an admin only"); TVA staff accounts are still created by TVA admins only. |
| FR-CRM-CHG-15 | **Owner seeded.** The owner (`profiles.is_owner`) is made Admin of both workspaces by migration, as `003` FR-DASH-01 sets the owner flag. |
| FR-CRM-CHG-16 | **MCP instructions** describe the CRM (FR-CRM-MCP-09); `update_project` describes the CRM-controlled fields. |
| FR-CRM-CHG-17 | **One capacity service.** Amends `002` FR-ANALYTICS-06: a single capacity service returns, per week, **supply** (each staff account's contracted hours, active between joined on and left on, less the holidays TVA applies to them and their approved leave) and **demand** (non-released allocations on non-archived projects, tentative shown separately from confirmed). TVA's forecast and the CRM capacity check (§5.21) both read it. Today's forecast returns allocated hours per project and computes no supply, so this is new work, not a reuse (Q-66). |

---

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-CRM-01 | List views and search under 1 second at 10,000 records per object; record page under 1 second; table and kanban views with saved filters under 1 second for 10,000 companies; paging stays fast at 50,000; a kanban drag gives immediate feedback and is then confirmed or rolled back by the server. This is the portal's first scale target (`001` NFR-07 stands for TVA). |
| NFR-CRM-02 | Security: permissions in the backend (FR-CRM-ACC-10); secrets as Railway environment variables; the service-account key never logged, never sent to the browser, scoped read-only and used only for listed mailboxes; no email bodies stored in Phase 1 (Q-29). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-01: still no email bodies stored; the service-account key is also held by `worker-crm-ai`, whose summariser reads stored messages' text in memory only (`009` NFR-CRMAI-04).* |
| NFR-CRM-03 | Integrity: every multi-table transition (Proposal create, handoff, Closed Lost release, reopen, merge) is atomic (Q-21; constitution §III). |
| NFR-CRM-04 | Reliability: background jobs are idempotent and resumable, recorded in the job log, and alert on failure and staleness (§6.3). |
| NFR-CRM-05 | Backups: Supabase backups, plus the full workspace JSON export on demand. The source's weekly scheduled export is not built until the owner confirms its destination (Q-45, §14). |
| NFR-CRM-06 | Accessibility: keyboard usable, visible focus, colour never the only signal. |
| NFR-CRM-07 | Tone: CRM copy is plain and direct. Team-facing TVA pages keep their tone rules; finance stays off them. |
| NFR-CRM-08 | Observability: every CRM route and job logs as the portal does (constitution §V), including the latency of every Gmail call; refusals are RFC 7807 with one sentence naming every missing field. |
| NFR-CRM-09 | Clean room: nothing is copied, ported or translated from Twenty's code, schema, migrations or UI (Twenty is AGPL-3.0). This spec and `source.md` describe behaviour only. |
| NFR-CRM-10 | Tests: every rule in §5.12–§5.14, §5.16–§5.18, §5.20, §6.1's lead-stage effects, §6.2 and §6.4–§6.6, and every acceptance criterion in §11, has a test; flags, Today and digests are tested with fixed clocks. Workspace isolation and Viewer redaction (including amount filters and sorts) are tested across routes, MCP, export, notifications and digests (D3). How (Q-68): rule checks live in Python domain functions tested with fakes, as the suite does today, and each database function only writes an already-validated plan atomically; a small set of real-database tests covers the database functions and the audit trigger, in a new CI job that starts a local Supabase and applies `supabase/migrations/` — a new CI dependency. |
| NFR-CRM-11 | New dependencies are limited to those in Q-27, each justified in the plan. |

---

## 9. Key entities and indicative changes

Logical; the plan chooses tables, types and names.

```text
CRM (new, all workspace-scoped, RLS on, no browser grants)
  workspace, membership (person, role, last used), invitation record via audit
  select list + option, workspace setting, FX rate (workspace, currency, month)
  company, person, opportunity (+ stage history, contacts), task (+ links),
  note (+ links, mentions), drive link, saved view, favourite
  1b: email message/thread (metadata), flag, job run, notification,
      digest sent-marker, campaign, campaign member, bookings target

Existing tables
  profiles      + account type (staff | crm_only), CRM time zone, amount format
  allocations   + released at / by / reason
  projects      + CRM company, + source opportunity (unique)
  audit_log     + workspace; actor kinds widened (import, sync); channel
```

---

## 10. Decisions

Settled 14 October 2026. Q-01–Q-17 are the owner's decisions D1–D16 (D4b is Q-05). Q-18–Q-28
are the recommended defaults the owner accepted ("use recommended options").
Q-29 onward are defaults this spec chose under the same instruction; each is the
conservative reading and can be reversed by the owner.

| ID | Question | Source said | Decision | Changed |
|---|---|---|---|---|
| **Q-01** (D1) | Is the CRM in scope? | Assumed | **Yes**, constitution v1.2.0: Seller's journey, CRM-only accounts refused outside the CRM and excluded from TVA, no cross-workspace data, frontend reads no data from Supabase, Drive links only. | Constitution amended |
| **Q-02** (D2) | Where do CRM-only people live? | A new login type | **In `profiles` with an account type**, `logs_time` off; refused by every TVA route and excluded from every TVA population; a route-enumerating test. | Made concrete |
| **Q-03** (D3) | Where are permissions enforced? | "In the database layer (RLS or equivalent)" | **In backend code**, one path for web, API and MCP; new tables RLS on, no browser grants; tests prove isolation and redaction. | Changed: backend, not DB |
| **Q-04** (D4) | Attachments? | Files in Supabase Storage with previews | **Google Drive links (URL + label)**; no uploads, no Storage, no previews. | Changed |
| **Q-05** (D4b) | Import/export transport and formats? | CSV and XLSX | **Widen the proxy** (<6 MB uploads, binary/CSV downloads with headers); parsing in the backend; **CSV only** in Phase 1. The limit is set at **4 MB** for uploads (and `crm_import` text), not just under 6 MB: Netlify caps function request bodies near 6 MB and may base64-encode a binary body, a third larger; an upload test in the plan confirms the figure. The proxy change ships in 1a, for the Twenty importer. | XLSX dropped; limit narrowed to 4 MB |
| **Q-06** (D5) | Closed Lost "releases allocations" | Release (unspecified) | **Reversible release marker** honoured by every TVA reader; archived projects stop counting; archived tentative projects leave the pipeline; reopen unarchives and un-releases the same allocations. | Made concrete; TVA readers changed |
| **Q-07** (D6) | MCP shape | 19 `crm_*` tools; CONFIRM FIRST only on TVA-touching or deleting writes | **Those tools except `crm_audit`** (Phase 2): **18 tools** (15 in 1a, 3 in 1b). D6's "17" is a miscount of the source's 19 less one. `004` FR-MCP-01 amended, its coverage test excluding `/api/v1/crm/*` by prefix; **CONFIRM FIRST on every write** (TVA rule kept). *Amended 18 October 2026 by `009` FR-CRMAI-CHG-04: 21 tools, with `009`'s three.* | CONFIRM widened; crm_audit deferred; D6 count corrected |
| **Q-08** (D7) | Currency | INR/AUD/USD, workspace FX rate (open question 3) | **Monthly rates per workspace set by Admins**; amount in INR stored on save; handoff converts to INR at win, deal records the rate. | Resolved open Q3 |
| **Q-09** (D8) | Who performs TVA effects for a Member without TVA rights? | Unstated | **The CRM, as a system actor stamped with the person**; revenue only by TVA manager/admin, else confirmed without revenue + task for Labs Admins. The task goes to the Labs Admins who are TVA managers or admins (all Labs Admins when none is), since only they can complete it, and says the change is made in TVA Projects. | Added; audience narrowed within D8 |
| **Q-10** (D9) | Where does the CRM sit? | "A section inside TVA with its own navigation" | **Separate full-width `/crm` area** with its own sidebar and switcher; TVA link for members; CRM-only land at `/crm`. | Made concrete |
| **Q-11** (D10) | Phase 1 scope | One Phase 1, one week | **Split into 1a and 1b**, both in this build. | Split |
| **Q-12** (D11) | Who invites? | Invitations, domain allow-list | **Workspace Admins into their own workspace from the allow-list; TVA admins anywhere**, bypass for existing staff; invite pre-creates the account; workspace Admins cannot touch TVA roles. | Made concrete |
| **Q-13** (D12) | Sign-in method | "TVA's existing sign-in" | **Google only, sign-up off.** Local `supabase/config.toml` change never committed. | Unchanged, stated |
| **Q-14** (D13) | MCP workspace default | Last-used for every tool | **Reads default to last-used; multi-workspace writes must name it.** | Narrowed for writes |
| **Q-15** (D14) | Project name clash at Proposal | Unstated | **Refuse, naming the clash, offering rename or link.** | Added |
| **Q-16** (D15) | Who controls a linked project's status? | Unstated | **The deal only**, for status and probability; archive too while the project is tentative. Narrowing of D15: a **confirmed** (won) linked project archives and unarchives in TVA as today (`002` FR-PROJ-04), because Closed Won is terminal and no deal action archives a delivered project; the CRM shows the archived state read-only. Other fields stay TVA's. | Added; archive narrowed for won projects |
| **Q-17** (D16) | The "CRM follow-up sweep" tasks | Repointed at cutover | **Claude scheduled tasks outside this repo**; repointed at cutover; no code. | Not code |
| **Q-18** | Notification channel and time zones | Email digests; in-app bell | **The portal's channel (Slack DM, email fallback) plus the bell**; digests exactly once (sent marker); per-person time zone (Asia/Kolkata default, Australia/Sydney) for CRM display and digests only. | Email-first changed to Slack-first |
| **Q-19** | Weekly admin digest | Monday to admins | **Separate from the TVA leadership digest.** | — |
| **Q-20** | Flags stored or derived? | Computed on change and every 30 min | **Stored**, recomputed on change and every 30 min, freshness shown; copy says "follow-up"/"deal health". | Naming added |
| **Q-21** | Atomic transitions and the audit path | — | **Database functions**, so each move and its side effects commit together. Every CRM write, ordinary edits included, goes through a database function that sets the actor as a transaction-local setting; an AFTER trigger on each CRM table writes `audit_log` from it in the same transaction and refuses a write with no actor. `audit.record` is not used for CRM tables, because it runs after the write and swallows failures (FR-CRM-AUD-06). | Audit path defined |
| **Q-22** | Audit store | An audit log; recommended default "TVA `audit_log` tool excludes CRM rows unless admin" | **The portal's audit log with a workspace, widened actor and a channel (web or MCP)**; workspace Admins read their own; TVA audit views exclude CRM rows **for everyone**. The default's "unless admin" is dropped because constitution §I allows no CRM data to non-members; a TVA admin sees a workspace's CRM audit by being its Admin, through the CRM audit page. The channel column exists so Claude's confirmed writes can be reviewed apart from web ones (FR-CRM-AUD-02). | "Unless admin" dropped; channel added |
| **Q-23** | Gmail credentials | Railway secrets | **Built behind a switch**, off until the service-account secret exists; own queue, single run at a time. | — |
| **Q-24** | Twenty importer | JSON preferred, CSV accepted | **As source**; real run waits for exports; imported deals do not create projects. | Project creation on import excluded |
| **Q-25** | Seeding | Seed lists §5.10 | **Two workspaces, lists, probabilities and views seeded; owner Admin of both by migration**; other memberships by invitation. | — |
| **Q-26** | Per-owner metrics and Today picker | Asked for | **Accepted for sales work**, as a CRM-only exception to TVA's no-ranking rules (FR-CRM-CHG-13). Per-owner figures sort by name, never by value, to keep the exception from becoming a league table (FR-CRM-DASH-04). | Exception recorded; name sort added |
| **Q-27** | Dependencies and rich text | Rich text with images | **dnd-kit and cmdk allowed with justification; notes plain text/markdown; no xlsx; google-auth for sync only.** | Rich text dropped |
| **Q-28** | Formats | 14 Oct 2026; Indian style | **UI "14 Oct 2026"; MCP ISO and decimal strings; INR in Indian style by preference.** | MCP kept ISO |
| **Q-29** | Phase 1 email content | "Per the visibility level" | **Subject and metadata only, no bodies** — the source's own Phase 2 default; no at-rest encryption pattern exists to store bodies safely. *Amended 18 October 2026 by `009` FR-CRMAI-CHG-01: bodies are still never stored; "never fetched" becomes "fetched in memory by the summariser only, never stored" (`009` FR-CRMAI-SUM-03).* | Bodies not stored |
| **Q-30** | Can Admins edit stages? | Select lists are configuration | **Stage and lead-stage sets are fixed** (rename and recolour only), because server rules hang on them. | Narrowed |
| **Q-31** | Skipping stages | Unstated | **Requirements of skipped Discovery–Proposal stages apply**; Proposal sent on only for Negotiation. | Added |
| **Q-32** | Deleting a deal with a project | Blocked if project confirmed | **Blocked if any linked project**, tentative included; close it Lost instead. | Widened |
| **Q-33** | Reopening Closed Won | Admin, audited | **Workspace Admin; refused while the project has time or invoices**; else project back to tentative, revenue and milestones kept. | Project effect added |
| **Q-34** | Twenty attachments | Imported | **Not imported as files; listed for re-linking.** | Changed by D4 |
| **Q-35** | Actor kinds | user, import, sweep, workflow, API, AI | **user, system, import, sync** in Phase 1; sweeps act as a person via MCP; API, AI, workflow come with their phases. | Narrowed, renamed |
| **Q-36** | Member deletes "own" records | Own records | **Records they own, or created where there is no owner.** | Defined |
| **Q-37** | Viewer views | No shared views | **Private views allowed.** | Clarified |
| **Q-38** | Deal on the TVA project page | Shown, amounts if revenue-visible | **Shown to Labs members only; never an amount**. The deal itself is CRM data and constitution §I allows none to non-members; the project name and free-text client the CRM copies in when it creates or links a project become TVA data and follow TVA visibility, and nothing else crosses (FR-CRM-TVA-01). | Narrowed |
| **Q-39** | Capacity check audience and shape | Dashboard | **Totals only, Labs Admins and staff Members** — not Viewers, not `crm_only` accounts, since the totals are built from staff allocations, leave and holidays; a week with fewer than 3 contributing people is hidden. Same service as the forecast (FR-CRM-CHG-17); 1a from Pipeline. | Defined; crm_only excluded |
| **Q-40** | Academy "institution type" and "programme" | Dashboard asks for them; no fields | **Company Institution type and opportunity Programme selects**, Academy only. | Fields added |
| **Q-41** | Switching off automation 1 | Every automation switchable | **Automation 1 not switchable** — it is the pipeline rules — **and neither are 3, 4, 9 and 10**, which other requirements state as MUST rules (FR-CRM-PIPE-02, -09, FR-CRM-TVA-09, FR-CRM-FLAG-02). Switches remain for 2, 5, 6, 7 and 8. *Amended 18 October 2026 by `009` FR-CRMAI-CHG-06: automation 4 stays unswitchable apart from the Stuck in stage flag, switched with R-2 (`009` Q-29); switches also exist for 11 to 14.* | Narrowed |
| **Q-42** | "Link to existing" on a clash | — | **Only an unlinked, non-archived tentative project.** | Defined |
| **Q-43** | Reopen restores clashing allocations | — | **Restored as they were**; TVA's over-allocation and deactivation rules show the result. | Defined |
| **Q-44** | Removing a CRM-only person's last membership | — | **Deactivates the account and revokes tokens.** | Added |
| **Q-45** | Weekly JSON export "to storage" | Weekly to storage | **Not built until the owner names a destination**; Supabase's own backups plus the on-demand full export stand meanwhile. D4 concerns attachments and does not decide this; the open question is where a backend-only weekly file should live (a private Storage bucket, or a Drive folder through the service account, which does not exist yet). Listed in §14. | Deferred to owner |
| **Q-46** | Twenty data breaking rules | — | **Dry run lists violations; opt-in "Review after import" next step.** | Added |
| **Q-47** | Who gets job alerts | Admins | **Workspace Admins and TVA admins**, plus a staleness alert. | Defined |
| **Q-48** | `crm_import` file input | "Uploaded file" | **File content as text in the call.** | Defined |
| **Q-49** | Importing campaigns | Phase 1 | **With 1b**, when campaigns exist. | Phased |
| **Q-50** | Today before email sync | — | **Email sections say "Email sync is off".** | Added |
| **Q-51** | What Viewers read | Table: all records; §3.1: "companies and won deals tied to their projects" | **All workspace records except amounts**; narrower row rules are Phase 2. | Resolved inconsistency |
| **Q-52** | Project category empty at Proposal | — | **`client`**, TVA's default. | Added |
| **Q-53** | "Log activity" | An action, undefined | **A typed note (call, meeting, WhatsApp, other) with a date.** | Defined |
| **Q-54** | Deleting a company with deals | "Asks what to do with its people and deals" | **Asks about both**: people unlink or delete; deals move to another company or delete (a deal cannot be unlinked, Company is required). **Refused 422, naming the deals, when any deal has a TVA project**, so nothing is partly deleted. Purging a company clears projects' company link. | Defined |
| **Q-55** | Lead stage of imported companies | Unstated (§15.1 maps only deal stages) | **By the pipeline rules**: partner account types → Partner; open deal → Deal open; won → Customer; lost only → Nurturing; otherwise Twenty's company stage where mapped, else New. The dry run shows each derived stage. | Added |
| **Q-56** | Which shared routes may a `crm_only` account reach? | D2: every TVA route refuses them | **A closed allow-list**: reduced `/me`, personal tokens, OAuth authorize/consent/token, sign-out — needed so CRM-only sellers can use Claude (G-4). The route test holds the list as a constant; every other non-CRM route is asserted refused. | Carve-out from D2 recorded |
| **Q-57** | Handoff task and project timeline | Timeline event on deal and project; task to the project lead | **The project side is the TVA audit entry** (TVA has no project timeline). Project leads are usually Labs Viewers, so **any assignee, a Viewer included, may mark their own task Done** (status only); the task goes to the deal owner, naming the lead, only when the lead is not a Labs member at all. | Defined |
| **Q-58** | MCP coverage | "Every capability … usable through the MCP" | **`crm_create`/`crm_update`/`crm_list` cover the record objects, Drive links and typed notes; delete and restore, settings, FX, invitations, view editing, bulk actions and export are web-only in Phase 1**, because D6 fixes the tool set and none of its tools covers them. | Source principle narrowed, stated |
| **Q-59** | FX fallback and recompute | "On save" (§5.4, D7) | **Rate for the save's month, else the latest earlier month; no rate at all → 422 naming it.** At Closed Won amount in INR is re-stored at the won-date rate so bookings match the TVA revenue pre-fill. Editing a rate never recomputes stored amounts. | Defined |
| **Q-60** | Lead-stage effects of deals | §6.2 table: Won sets Customer; §6.1 rule 5: only when the last open deal closes | **Rule 5 governs.** Only Qualified moves to Deal open; Customer and Partner keep their stage when a deal opens, and a loss never demotes a company with a won deal; Partner is never set to Customer; a deal cannot open on Disqualified. | Conflict resolved; rules narrowed |
| **Q-61** | Campaign Entity | §2: "or both"; §5.8: a Select | **One entity, as for opportunities**, for attribution. Companies keep the multi-select. | Resolved inconsistency |
| **Q-62** | Who may call each tool; who may run sync | `crm_import` admins (§3.3); sync "from the UI and MCP" | **Per tool in FR-CRM-MCP-11.** Import and run-sync-now are **Admins only**: a run reads every listed mailbox, and mailboxes are Admin settings. | Defined |
| **Q-63** | @mentions of non-members | Unstated | **Refused**, since a mention notifies its target and a non-member may receive nothing (FR-CRM-WS-02). | Added |
| **Q-64** | Reopen from Lost; losing a reopened won deal | Journey: "an admin reopens" | **A workspace Admin or the deal owner, back to the stage it was lost from, with next step and date.** A won deal reopened by an Admin may later be lost: its tentative project is archived, revenue and milestones kept but not counted. | Defined |
| **Q-65** | How imported deals reach their projects | Unstated | **Link TVA project** (Labs Admin): open deals link to an unlinked tentative project or create one; Closed Won deals link to an unlinked confirmed project unchanged. | Added |
| **Q-66** | Where capacity figures come from | "TVA's forecast" | **One new capacity service** (supply and demand per week), amending `002` FR-ANALYTICS-06; TVA's forecast and the CRM check both read it. Today's forecast computes no supply. | TVA forecast amended |
| **Q-67** | Viewer filters and sorts on amounts | Unstated | **Refused** (dropped inside shared views, with a notice), so redaction cannot be worked around. | Added |
| **Q-68** | How database-held rules are tested | "Every rule has a test" | **Rule checks in Python, tested with fakes; database functions write a validated plan atomically**; a small real-database CI job tests the functions and the audit trigger. | New CI dependency |
| **Q-69** | What TVA admins see of a workspace they are not in | Unstated; constitution §I | **Workspace name, member list and job-run metadata are account administration**, shown to TVA admins; error text is stripped of record content for non-members. No CRM record is shown. | Carve-out recorded |
| **Q-70** | Revenue on a tentative linked project | Unstated | **Set in TVA by TVA managers and admins as today**, so the P&L pipeline block (`005` FR-PNL-05) keeps working; the CRM never writes it before the win. | Defined |
| **Q-71** | Renaming options rules depend on | Select lists are configuration | **Stages, lead stages and rule-bearing options (client and partner account types, Delivery handoff, Source values, Lost reasons) have fixed keys**; renamable and recolourable, not deactivatable; MCP, filters and imports use the keys. | Narrowed |
| **Q-72** | Detecting a stopped scheduler | "Alert on staleness" | **Checked on read** (page load and `crm_today` show Admins a banner) plus a `/health/jobs` endpoint for an external monitor, since a stopped scheduler cannot alert itself. | Mechanism defined |
| **Q-73** | Reaching CRM-only people out of app | Email digests | **Email needs a production SMTP sender**, which is not set today; until then CRM-only people get the bell only and runs log an "undeliverable" count (§14). | Precondition added |
| **Q-74** | Sync capacity | "Own queue" | **Resumable page-sized tasks under a per-task time limit, on a dedicated worker started only when sync is switched on** (Railway cost in §14). | Defined |
| **Q-75** | Re-inviting a deactivated person | Unstated | **An inactive `crm_only` account is reactivated by invitation; an inactive staff account is refused ("ask a TVA admin").** | Added |

---

## 11. Acceptance criteria (Phase 1)

Workspaces and access (1a)
- [ ] No query, search, view, dashboard, export or MCP call returns data from a workspace the caller is not a member of; another workspace's record id is answered 404.
- [ ] A CRM-only account sees only the CRM; the route-enumerating test shows every TVA page, route and tool outside the shared allow-list (FR-CRM-CHG-01) refusing it.
- [ ] A CRM-only account appears in no TVA people list, report, reminder or job (bench, resources, utilisation, team day, timesheet population, nudges, digests, CTC-missing, balances).
- [ ] A Viewer receives no amount, weighted amount or pipeline value anywhere, including MCP responses, search, kanban totals, exports, notifications and digests; a Viewer filter or sort on an amount is refused.
- [ ] A workspace Admin can invite only into their workspace and only from its allow-list, and cannot change a TVA role; a TVA admin can invite anywhere.

Pipeline rules (1a)
- [ ] An open deal cannot be saved without next step and date, through the web app, import, MCP or API.
- [ ] A deal cannot be created for a company below Qualified; creating one sets the company to Deal open.
- [ ] Moving to Proposal without amount, currency and expected close fails and names every missing field.
- [ ] Closed Lost without a lost reason fails.
- [ ] Every stage change records its time; time in stage is correct on the dashboard (1b).
- [ ] A non-INR amount stores amount in INR at the month's rate; with no rate, the save is refused naming it.

TVA integration (1a)
- [ ] Moving a Labs deal to Proposal creates exactly one tentative project that appears in TVA Projects as tentative with the deal's probability; its allocations, once added, appear in the forecast as tentative.
- [ ] A Proposal move whose name clashes with a project is refused, naming it, and can proceed by rename or link.
- [ ] Closed Won confirmed by a TVA manager or admin makes the project confirmed with revenue in INR, lead, phase dates and milestones; nothing is double-counted in the P&L.
- [ ] Closed Won by anyone else confirms the project without revenue and creates the revenue task for the Labs Admins.
- [ ] Closed Lost archives the tentative project and releases its allocations; forecast, resources, bench, hiring signal, planned cost and the P&L pipeline no longer count them.
- [ ] Reopening from Lost restores the project to tentative and the same allocations; every figure returns.
- [ ] The Projects page and `update_project` refuse status and probability changes on a linked project, and archive changes while it is tentative; a won linked project archives in TVA as today.
- [ ] An imported Closed Won deal links to its existing confirmed project with Link TVA project, and the project's status, revenue and milestones are unchanged.
- [ ] Deleting a company with a deal linked to a TVA project is refused, naming the deal.
- [ ] Academy deals never create TVA projects.

Views and data (1a; CSV 1b)
- [ ] Table and kanban views with saved filters load in under 1 second for 10,000 companies.
- [ ] The Twenty importer's dry run reports counts per object matching Twenty's, and the real run reproduces every link (company to people, deals, tasks, notes; campaign members and attribution in 1b). A second run changes nothing.
- [ ] CSV import catches duplicate domains and emails before writing and lets the person fix rows in the web app (1b).
- [ ] A CSV export downloads as a CSV file with the view's filters and columns (1b).

Email and flags (1b, once the credential exists)
- [ ] A labelled email appears on the right person and company within 15 minutes, with Last inbound or Last outbound updated.
- [ ] Running the sync twice creates no duplicates.
- [ ] An email from an unknown external domain with the Sales label creates a company at New owned by the mailbox owner.
- [ ] Internal-only emails never change flags.
- [ ] Reply-owed, client-silent and next-step flags match §6.2 in fixed-clock tests.
- [ ] Each daily digest is delivered exactly once per person per day at 09:00 their time.

MCP (1a, 1b tools)
- [ ] Claude can run the whole morning follow-up review, move a deal through every stage including the handoff, and add tasks and notes, entirely through NLDW, each write after confirmation.
- [ ] A write by a two-workspace member naming no workspace is refused with their workspaces listed.

---

## 12. Later — not this build

No requirements here; each needs its own spec when it is taken up.

**Phase 2** (after Phase 1 has run for a month): full mailbox sync with
visibility levels and blocklists; Google Calendar sync; sending email from
records; calendar view; custom fields; custom roles with object, field and row
permissions; record page layout editing; full merge for all objects;
configurable dashboards; a CRM assistant and record-level AI actions; workspace
API keys and webhooks; `crm_audit`.

*Amended 18 October 2026 by `009` FR-CRMAI-CHG-02: record-level AI suggestions (summaries, enrichment, AI fields, follow-up drafts) moved from Phase 2 into `009`; the CRM assistant stays here.*

**Phase 3**: a visual workflow builder; an AI agent step; email campaigns with a
sending domain and unsubscribes; custom objects; possibly the Academy B2C
learner pipeline.

---

## 13. Out of scope

Quotes, price books and a product catalogue; marketing automation and lead
scoring; public web-to-lead forms; telephony and call recording; WhatsApp
sending (touches are logged as notes); invoicing (TVA's milestones and invoices
remain); a mobile app; file uploads and storage (D4); XLSX (D4b); rich text with
images (Q-27); any new sign-in method (D12); Academy project handoff; storing
email bodies (Q-29); everything in §12.

*Amended 18 October 2026 by `009` FR-CRMAI-CHG-02: lead scoring stays out of scope; `009`'s ICP fit is a read-only AI suggestion with no effect on any stage, probability or rank.*

---

## 14. Open items

| Source §20 | Status |
|---|---|
| 1. DeepWeaver logins besides Tamil | The owner invites them (FR-CRM-ACC-04). |
| 2. Academy mailboxes and labels | Sanjeev nominates; set in Academy Settings (1b). |
| 3. FX source | **Resolved** by D7: monthly manual rates. |
| 4. Bookings targets | Set by Admins in Settings (1b); no colour until set. |
| 5. Academy B2C | Phase 3, if at all. |
| 6. Legal check if TVA is offered outside Nunnari | Unchanged; NFR-CRM-09 keeps the clean room. |

Also open:

- **Production SMTP sender** — needed before any email reaches a CRM-only person (invitations, digests, overdue reminders), since they are not in Nunnari's Slack; recommended: the Google Workspace SMTP relay. Until then the bell only (FR-CRM-NOTIF-02, Q-73).
- **Railway cost of the sync worker** — a second worker service, started only when Gmail sync is switched on (FR-CRM-SYNC-04, Q-74).
- **Weekly backup export destination** — the owner names one, or confirms the drop (NFR-CRM-05, Q-45).

Also waiting on the owner: the Google service-account credentials (sync stays
off until then), the Twenty exports (the importer runs on fixtures until then),
and repointing the two Claude "CRM follow-up sweep" tasks at cutover (D16).

---

## 15. What this must not become

- **Not a way into TVA.** A CRM-only account is not a lesser employee; it is not
  an employee. If a TVA page ever renders for one, that is a security bug, not a
  missing filter.
- **Not a back door around TVA permissions.** The system actor (FR-CRM-TVA-03)
  performs the deal lifecycle and nothing else. If it is ever asked to do one
  more thing, that thing needs its own decision.
- **Not a second ledger.** Revenue after the win is TVA's; the CRM never
  reports recognised revenue and never shows a deal amount on a TVA page.
- **Not a seller league table.** Per-owner counts exist to run the pipeline,
  sort by name, and never meet TVA's hours, leave or cost.
- **Not a mail archive.** It keeps what was labelled for sales, as metadata,
  and forgets it when the label goes. *Amended 18 October 2026 by `009`
  FR-CRMAI-CHG-01: "as metadata and AI summaries, never bodies".*
- **Not a file store.** Links to Drive, nothing else.
- **Not Twenty rebuilt.** Behaviour is matched where it earns its place;
  Twenty's code, schema and screens are never consulted.
