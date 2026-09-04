# Leave Calendar — V1

| | |
|---|---|
| **Feature** | `001-leave-calendar` |
| **Status** | Implemented; open questions in §11 pending sign-off |
| **Source** | *Nunnari Employee Portal — Functional Specification* v0.1 (team sync-up, 31 August 2026) |
| **Scope** | Version 1 only — the leave calendar |
| **Review** | 7 September 2026 |

This file is the operative requirements document. The `.docx` it was derived
from is a point-in-time export and is not maintained; when the two disagree,
**this file wins**. New requirements arrive as a PR against this file.

---

## 1. Context

Nunnari runs project management on ClickUp. It works for leads and senior
engineers who have adapted to it, but it fails for interns and junior engineers,
who struggle to navigate channels, task sheets and view modes. The consistent
result is that work gets done but not recorded, so the tracking system stops
describing reality.

Separately, leave never reaches leads in time to be useful. It arrives by email
— someone mails at 08:00 to say they are unwell, the lead reads it at 12:30, and
by then the day has been planned around them being present. Work from home is
worse: nothing announces it, and the lead discovers it by walking into the
office and not finding the person.

This portal replaces the second workflow first.

### 1.1 The bar V1 must clear

Not feature parity with an HR suite. **Lower friction than sending an email.**
Marking a leave must take roughly ten to fifteen seconds and require no
arithmetic from the person doing it. A slower flow will not be adopted, and an
unadopted portal solves nothing.

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — A lead can see, at a glance, who is available today and who is not.
- **G-2** — An employee can record a leave or work-from-home day in under fifteen seconds.
- **G-3** — Leave allowances are administered by HR, not by engineers editing code.
- **G-4** — Records cannot be quietly rewritten after the fact.
- **G-5** — Company holidays are declared once and apply to everyone.

### 2.2 Non-goals for V1

Time tracking, project logging, task allocation, location capture, meeting
aggregation, payroll, and self-service profile editing. All are on the roadmap
(§10) and none are built now.

---

## 3. Roles

| Role | Who | Can |
|---|---|---|
| `user` | Every employee | See and manage their own calendar and balances; request leave |
| `lead` | Team leads | Everything a user can, plus approve/reject for their reports and see the team view |
| `admin` | Vinita | Manage users, allowances and holidays; see the whole organisation |

Roles are cumulative: a lead is also a user with their own leave to book.

---

## 4. User scenarios

### 4.1 Booking a half-day for an appointment

Deepika has a dentist appointment from 09:00 to 12:00 on the 28th, and will come
to the office afterwards. She is unreachable for the morning, so it is casual
leave, not work from home.

```gherkin
Given Deepika is signed in as a user
  And she has at least 0.5 days of casual leave remaining for the period
 When she opens the calendar, selects the 28th
  And chooses "Casual leave", duration "Half day"
  And enters the reason "Dentist appointment"
  And confirms
 Then a booking is created with status "pending"
  And her remaining casual leave decreases by 0.5 days
  And her lead is notified over Slack or email
  And the 28th shows as a half-day casual leave on her calendar
```

### 4.2 Same-day sick leave

```gherkin
Given Tarun is signed in
 When he marks today as "Sick leave", duration "Full day"
  And enters a reason
 Then the booking is created and his lead is notified immediately
  And the booking is accepted despite being made on the day itself
```

### 4.3 Correcting a work-from-home day

```gherkin
Given a work-from-home booking exists for the 1st
  And the current date is still the 1st
 When Devansh removes the booking at 21:00
 Then the booking is withdrawn
  And the day is returned to his work-from-home allowance

Given the same booking exists for the 1st
  And the current date is the 2nd or later
 When Devansh attempts to change or remove it
 Then the attempt is refused
  And the calendar shows the day as locked
```

### 4.4 Admin sets up the month

```gherkin
Given Vinita is signed in as admin
 When she declares 15 August as "Independence Day"
  And sets the work-from-home allowance for the period
 Then the holiday applies to every employee and consumes no allowance
  And every employee's allowance for that period reflects the new value
```

### 4.5 A lead plans the day

```gherkin
Given Devansh is signed in as a lead
 When he opens the team view
 Then he sees who is on casual leave, sick leave and work from home today
  And who is present
  And which absences are approved, pending, or unrecognised
```

---

## 5. Functional requirements

Keywords follow RFC 2119: MUST, MUST NOT, SHOULD, MAY.

### 5.1 Authentication and accounts — FR-AUTH

| ID | Requirement |
|---|---|
| FR-AUTH-01 | The system MUST require authentication for every page except the sign-in page. |
| FR-AUTH-02 | The system MUST NOT offer self-registration. Accounts are created by an admin only. |
| FR-AUTH-03 | An admin MUST be able to create a user with an email address, an initial password, a display name, a role and an assigned lead. |
| FR-AUTH-04 | The system MUST enforce role-based access: a user MUST NOT be able to read another user's bookings, reasons or balances. |
| FR-AUTH-05 | A user MUST be able to change their own password. |
| FR-AUTH-06 | An admin MUST be able to deactivate a user without deleting their historical records. |
| FR-AUTH-07 | Passwords MUST be stored using a salted one-way hash. Plaintext or reversible storage is prohibited. |

**Convention.** Accounts use Nunnari IDs. Where a person has none, the
convention is `firstname.nl@gmail.com` (for example `sriram.nl@gmail.com`). This
is why self-registration is disallowed — left to themselves, people sign up with
personal addresses out of habit.

### 5.2 Calendar — FR-CAL

| ID | Requirement |
|---|---|
| FR-CAL-01 | The calendar MUST be the primary view after sign-in, showing one month at a time. |
| FR-CAL-02 | The calendar MUST visually distinguish work from home, casual leave and sick leave from one another and from unbooked days. |
| FR-CAL-03 | The calendar MUST visually distinguish half-day from full-day bookings. |
| FR-CAL-04 | The calendar MUST show declared holidays, labelled with the holiday name. |
| FR-CAL-05 | The calendar MUST show non-working days (weekends) as non-bookable. |
| FR-CAL-06 | The calendar MUST indicate the booking status (pending, approved, rejected) on each booked day. |
| FR-CAL-07 | The user MUST be able to navigate to other months, forward and back. |
| FR-CAL-08 | Locked days (FR-BOOK-08) MUST be visually distinct from editable days. |

### 5.3 Booking — FR-BOOK

| ID | Requirement |
|---|---|
| FR-BOOK-01 | A user MUST be able to book a day as one of: work from home, casual leave, sick leave. |
| FR-BOOK-02 | A booking MUST carry a duration of either full day (1.0) or half day (0.5). |
| FR-BOOK-03 | A booking MUST capture a free-text reason. A reason MUST be required for casual and sick leave. |
| FR-BOOK-04 | The system MUST reject a booking on a declared holiday or a non-working day. |
| FR-BOOK-05 | The system MUST reject a booking that would take the user's remaining allowance for that category below zero. |
| FR-BOOK-06 | A day MUST hold at most one booking. Changing the category replaces the existing booking. |
| FR-BOOK-07 | A user MUST be able to change or remove their own booking while the day is unlocked. |
| FR-BOOK-08 | A booking MUST become immutable once its own date has passed. See §6.3. |
| FR-BOOK-09 | Casual leave and work from home SHOULD be bookable only for today or a future date. |
| FR-BOOK-10 | Sick leave MUST be bookable for the current day. |
| FR-BOOK-11 | Removing a booking MUST return its cost to the user's remaining allowance for that period. |
| FR-BOOK-12 | The booking form MUST show the user's remaining allowance for the selected category before they confirm. |

### 5.4 Allowances and balances — FR-BAL

| ID | Requirement |
|---|---|
| FR-BAL-01 | Allowances MUST be scoped to a calendar month, not a year. |
| FR-BAL-02 | Allowance values MUST be read from admin configuration. They MUST NOT be hardcoded. |
| FR-BAL-03 | Each category MUST have its own independent allowance. |
| FR-BAL-04 | Balances MUST support half-day granularity. |
| FR-BAL-05 | Unused allowance MUST carry forward rather than being forfeited at month end. |
| FR-BAL-06 | The system MUST display, for the current period and per category: allowance, used, and remaining. |
| FR-BAL-07 | Changing an allowance MUST apply to the period it is set for and MUST NOT retroactively invalidate bookings already approved in a closed period. |
| FR-BAL-08 | A user MUST be able to see their own consumption history for the current year. |

### 5.5 Approval — FR-APPR

| ID | Requirement |
|---|---|
| FR-APPR-01 | A new booking MUST enter the pending state and notify the user's assigned lead. |
| FR-APPR-02 | A lead MUST be able to approve or reject a pending booking for one of their reports. |
| FR-APPR-03 | A rejection MUST capture a note from the lead. |
| FR-APPR-04 | The requesting user MUST be notified of the decision. |
| FR-APPR-05 | A lead MUST NOT be able to act on bookings outside their own reports; an admin MAY act on any. |
| FR-APPR-06 | A rejected booking MUST return its cost to the user's allowance. |
| FR-APPR-07 | Every state transition MUST be recorded with actor and timestamp. |

### 5.6 Holidays — FR-HOL

| ID | Requirement |
|---|---|
| FR-HOL-01 | An admin MUST be able to declare a holiday with a date and a name. |
| FR-HOL-02 | Holidays MUST apply to all employees. |
| FR-HOL-03 | A holiday MUST NOT consume any allowance. |
| FR-HOL-04 | An admin MUST be able to edit or delete a declared holiday. |
| FR-HOL-05 | Declaring a holiday on a date where bookings already exist MUST release those bookings and return their cost to the affected users' allowances. |
| FR-HOL-06 | Affected users MUST be notified when FR-HOL-05 releases one of their bookings. |
| FR-HOL-07 | Only an admin MUST be able to modify the holiday calendar. |

### 5.7 Admin panel — FR-ADMIN

| ID | Requirement |
|---|---|
| FR-ADMIN-01 | An admin MUST be able to set per-category allowances for a given period. |
| FR-ADMIN-02 | An admin MUST be able to create, edit and deactivate users. |
| FR-ADMIN-03 | An admin MUST be able to assign each user to a lead. |
| FR-ADMIN-04 | An admin MUST be able to manage the holiday calendar. |
| FR-ADMIN-05 | An admin MUST have the organisation-wide equivalent of the lead view. |
| FR-ADMIN-06 | Administrative actions MUST be written to the audit log. |

### 5.8 Lead and team view — FR-LEAD

| ID | Requirement |
|---|---|
| FR-LEAD-01 | A lead MUST see, for any chosen date, who is on casual leave, sick leave, work from home, and who is present. |
| FR-LEAD-02 | The view MUST distinguish approved absences from pending ones. |
| FR-LEAD-03 | The view MUST surface unrecognised absences — days someone was absent without a booking. |
| FR-LEAD-04 | A lead MUST see per-person consumption for the current period. |
| FR-LEAD-05 | The default view MUST be today, reachable without navigation or filtering. |

**Note on `unrecognised`.** This state has no automatic detector in V1 — nothing
tells the system that someone was absent. It is set manually by a lead after the
fact. Automatic detection would require attendance data the portal does not have
until day-login ships.

### 5.9 Notifications — FR-NOTIF

| ID | Requirement |
|---|---|
| FR-NOTIF-01 | The system MUST notify the assigned lead when one of their reports creates a booking. |
| FR-NOTIF-02 | Slack SHOULD be the primary channel; email MUST be available as the fallback. |
| FR-NOTIF-03 | A notification MUST carry the person, date, category, duration and reason. |
| FR-NOTIF-04 | A Slack notification SHOULD allow approve and reject without leaving Slack. |
| FR-NOTIF-05 | Notification delivery failure MUST NOT fail the booking. The booking stands and the failure is logged. |

---

## 6. Business rules

### 6.1 Leave categories

| Category | Planned ahead | Same-day | Half-day | Reason required |
|---|---|---|---|---|
| Work from home | Yes | Yes | Yes | Optional |
| Casual leave | Yes | No | Yes | Yes |
| Sick leave | No | Yes | Yes | Yes |

Casual leave is planned — a family event, an anniversary, an appointment. Sick
leave is by nature unplanned and is marked the morning it happens. The system
must not force the same notice rules on both.

### 6.2 Cost

A full day costs 1.0; a half day costs 0.5. Holidays and non-working days cost
0. Balances are therefore stored and displayed to one decimal place. Integer-day
arithmetic will be wrong.

### 6.3 The edit window

A booking is editable throughout the day it applies to, and locked from the next
day onward.

```
|  the booked day itself   |      every day after
|--------------------------|--------------------------
|  editable — change or    |  locked — counted,
|  remove freely           |  no edits, no removal
```

This is the integrity rule. Without it, someone can mark a work-from-home day,
take it, and quietly remove the record afterwards to reclaim the allowance. With
it, the correction window is real but bounded: if you marked WFH and then came
into the office, you can undo it at 21:00 the same evening; on the following day
the record stands.

### 6.4 Booking states

| State | Meaning | Entered when |
|---|---|---|
| `pending` | Requested, awaiting the lead | User creates a booking |
| `approved` | Agreed and counted | Lead approves, or auto-approval at lock |
| `rejected` | Declined, with a note | Lead rejects |
| `withdrawn` | Removed by the user inside the edit window | User clears the day |
| `released` | Cancelled by a holiday declaration | Admin declares a holiday on that date |
| `unrecognised` | Absent, never booked | Flagged manually by a lead |

```
                +---------> approved --+
(new) --> pending                      +--> locked at end of day
                +---------> rejected --+

pending/approved --> withdrawn      (user, same day only)
pending/approved --> released       (admin declares a holiday)
(no booking)     --> unrecognised   (lead, after the fact)
```

Only `approved` and `pending` consume allowance. `rejected`, `withdrawn`,
`released` and `unrecognised` return it.

---

## 7. Data model

### 7.1 `profiles`

One row per `auth.users` row. Replaces the starter template's `orgs` /
`team_members` multi-tenant model, which V1 does not need (see A-11).

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | PK, FK → `auth.users(id)` |
| `email` | text | unique; the login identifier |
| `display_name` | text | |
| `role` | enum | `user` / `lead` / `admin` |
| `lead_id` | uuid | nullable FK → `profiles` — who approves this person |
| `is_active` | bool | deactivation preserves history |
| `created_at` | timestamptz | |

### 7.2 `bookings`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `user_id` | uuid | FK |
| `date` | date | calendar date, no timezone |
| `category` | enum | `wfh` / `casual` / `sick` |
| `duration` | numeric(2,1) | 1.0 or 0.5 |
| `reason` | text | required per FR-BOOK-03 |
| `status` | enum | see §6.4 |
| `created_at` | timestamptz | |
| `decided_by` | uuid | nullable FK |
| `decided_at` | timestamptz | nullable |
| `decision_note` | text | nullable, for rejections |

A partial unique index on `(user_id, date)` where `status IN ('pending','approved')`
enforces FR-BOOK-06 in the database rather than only in application code.

### 7.3 Balance ledger

Balances are **derived, not stored** as a mutable counter. A counter drifts; a
ledger can be recomputed and audited.

`allowances` — what was granted:

| Field | Type | Notes |
|---|---|---|
| `period` | text | `YYYY-MM` |
| `category` | enum | |
| `days` | numeric(3,1) | set by admin per FR-BAL-02 |
| `user_id` | uuid | nullable — null means the organisation default |

Remaining for a period is:

```
opening_balance(period, category)
  + allowance(period, category)
  - sum(duration of consuming bookings in that period)
```

`opening_balance` is a **function over the ledger**, not a stored column, so the
carry-forward policy (Q-02) can change without a migration.

### 7.4 `holidays`

| Field | Type | Notes |
|---|---|---|
| `date` | date | unique |
| `name` | text | |
| `created_by` | uuid | FK |

### 7.5 `audit_log`

Append-only, enforced at the database level. Every booking state transition and
every administrative action: actor, action, target, before/after, timestamp.
Required by FR-APPR-07 and FR-ADMIN-06.

### 7.6 `app_settings`

Key/value policy switches so the open questions in §11 can be answered by
configuration rather than a migration. Seeded with the defaults in §11.

---

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | The calendar MUST be usable on a phone. Leave is often marked from bed or in transit. |
| NFR-02 | Booking a leave MUST take no more than three interactions from the calendar: pick day, pick category and duration, confirm. |
| NFR-03 | All dates MUST be handled in Asia/Kolkata. Calendar dates MUST NOT be stored as UTC timestamps — that shifts days across midnight. |
| NFR-04 | The lock check (§6.3) MUST be enforced server-side. |
| NFR-05 | Reasons, particularly for sick leave, are personal data. Access MUST be limited to the person, their lead, and admins. |
| NFR-06 | The audit log MUST be append-only and MUST NOT be editable from the application. |
| NFR-07 | The system SHOULD hold the whole company (tens of users) comfortably; scale is not a design driver. |
| NFR-08 | Deployment MUST be reproducible from the repository — no manual configuration steps that live only on someone's machine. |

---

## 9. Build and delivery

| Item | Decision |
|---|---|
| Method | Spec-driven. Specs live in this repository; new requirements arrive as a PR against this file. |
| Stack | Next.js 16 / FastAPI / Supabase (the interview product's stack) |
| Database | Supabase |
| Hosting | Railway (backend) on a Nunnari subdomain, Netlify (frontend) |
| Rejected | Internal hosting via ngrok |

Milestone: V1 prototype review, 7 September 2026.

---

## 10. Roadmap — not V1

Recorded so the V1 data model does not preclude them. **None of this is built.**

- Day login and project time — an eight-hour day logged in ten seconds, producing hours-per-project analytics.
- Location capture at sign-in — deliberately deferred. If it ships, it ships with its stated framing: a cultural shift so the team knows who is working on what, explicitly *not* a credibility or accountability system.
- Task allocation and resource management, absorbing what ClickUp does today.
- Unified meetings calendar across Nunnari and project mail accounts.
- A team board where hovering a person surfaces their current work.
- Self-service profiles — photo, phone, secondary email, skills, qualifications.
- Payroll visibility for admins.

---

## 11. Open questions and the assumptions shipped in their place

**Every question below is answered in code by a default.** Each default is
listed with where it lives, so answering the question is a settings change or a
small diff — never a rebuild. Nothing here is silently decided.

| ID | Question | Shipped default | Where it lives | Owner |
|---|---|---|---|---|
| Q-01 | The actual monthly allowance figures per category. Three non-reconciling numbers were given on the call. | Seed values: wfh 4.0, casual 1.5, sick 1.0 per month, **clearly marked as placeholders**. Blocks the first live month, not the build. **Rollout plan (confirmed 2026-09-04):** go live as early in a month as possible, with each person's leave already taken that month entered by an admin. See A-21. | `allowances` table, seeded in `supabase/seed.sql` | Devansh & Vinita |
| Q-02 | Carry-forward: rolling into the next month, or pooling to year-end? | `rolling` — cumulative from the start of tracking, no reset. `pooling` is implemented too and resets each calendar year. | `app_settings.carry_forward_policy` | Devansh |
| Q-03 | Precise lock boundary; does approval lock earlier than midnight? | End of the booked date, 23:59:59 **Asia/Kolkata**, evaluated server-side. Approval does **not** shorten the window. | `app/domain/rules.py::is_locked` | Sriram |
| Q-04 | Fate of a pending booking that reaches its own date un-actioned. | **Auto-approve at lock.** A nightly sweep at 00:05 IST promotes them, writing `system` as the actor in the audit log. | `app/tasks/lock_sweep.py` | Devansh |
| Q-05 | Who approves a lead's own leave, and who covers an absent lead? | **A lead's leave goes to an admin.** Concretely: a booking's approver is `profiles.lead_id`, and where that is null it falls to any admin. *No provisional answer existed in the source spec — this is our default.* | `app/domain/approval.py::approver_for` | Devansh |
| Q-06 | Does the lead view show reasons, or only categories? | **Category only** in the team list; the reason is returned only on the approval screen, and only to the assigned lead or an admin. | `app/api/team.py` omits `reason` | Devansh |
| Q-07 | Is a reason mandatory for work from home? | **Optional** for WFH; required for casual and sick. | `app/domain/rules.py::REASON_REQUIRED` | Devansh |
| Q-08 | Is booking beyond the allowance blocked, or allowed and flagged? | **Blocked.** The safer default and the easier one to relax later. | `app_settings.allow_excess_booking` (false) | Devansh |
| Q-09 | Do consecutive leave days spanning a weekend consume the weekend? (The "sandwich" rule.) | **No** — weekends never consume. The setting exists and defaults to `false`; **the `true` branch is deliberately not implemented** (YAGNI — nobody has asked for it yet). Turning it on requires a small diff, not a migration. *No provisional answer existed in the source spec — this is our default.* | `app_settings.sandwich_rule` | Devansh |
| Q-10 | Confirm project names for the eventual project dropdown. | Roadmap only. Not built. | — | Devansh |

### Additional assumptions made during implementation

| ID | Assumption | Why |
|---|---|---|
| A-11 | The starter template's multi-tenant `orgs` / `team_members` model is **removed**, not extended. Migrations 001–004 were rewritten rather than added to. | Nunnari is one company; NFR-07 says scale is not a design driver; the constitution's YAGNI principle forbids designing for a hypothetical tenant. Safe because those migrations had never been applied to any database, so there was no drift to cause. |
| A-12 | Allowance resolution falls back: user-specific row for the exact period → org-default row for the exact period → **most recent org-default row for any earlier period** → 0. | Without the third step every future month starts at zero allowance until an admin acts, which would block booking next month. Makes an admin's setting a standing policy rather than a one-month grant. |
| A-13 | Weekends are **Saturday and Sunday**. Not stated in the source spec. | FR-CAL-05 says "non-working days (weekends)" without defining them. |
| A-14 | A user may not book a date more than 365 days ahead. | Unbounded future booking lets one person consume allowance for periods no admin has configured. Not in the source spec. |
| A-15 | `unrecognised` days are created by a lead against a date **in the past only**, and consume no allowance. | §6.4 lists it as non-consuming and describes it as flagged "after the fact". |
| A-16 | Deactivating a user (FR-AUTH-06) leaves their bookings intact and blocks sign-in, but does **not** cancel future bookings. | FR-AUTH-06 says history is preserved; cancelling future leave is a separate decision nobody has made. |
| A-17 | The API returns RFC 7807 problem details, and the frontend renders `detail` verbatim to the user. | Constitution V. Observability. |
| A-18 | A booking's `category` is **nullable for `unrecognised` rows only**. Every other status requires one. | FR-LEAD-03 has a lead flagging that somebody was absent without booking. The lead genuinely may not know which category it was, and forcing them to pick one would put a guess into the record. |
| A-19 | **RESOLVED — confirmed 2026-09-04.** §6.1's table (casual leave, "same-day: No") is authoritative over FR-BOOK-09. Casual leave is bookable for a **future date only** — not for today. | The two read as a conflict: FR-BOOK-09 is a *floor* (no booking in the past) while the §6.1 table is a positive statement about notice. Casual leave is planned by definition; somebody taking an unplanned day off today is describing sick leave or work from home. Confirmed by the product owner; FR-BOOK-09 should be reworded in the next revision of this file to remove the ambiguity. |
| A-20 | Sick leave cannot be booked for a **future** date. | §6.1's table, "planned ahead: No". Nobody knows they will be ill next Tuesday, and a future-dated sick day is almost always a mis-tap on casual. |
| A-21 | **OPEN — needs a decision.** Go-live happens early in a month with "current status manually added by admin". Two readings, and only one of them is currently possible: (a) the admin **adjusts each person's allowance** for that month to net off days already taken — supported today via per-user allowance rows; (b) the admin **backfills the actual bookings**, which requires creating a booking on somebody else's behalf on a **past, locked** date — not supported, and in direct tension with FR-BOOK-08 and §6.3. | Reading (a) needs no code and loses the detail of which days were taken. Reading (b) needs a deliberate, audited admin override of the integrity rule the product rests on. That override should not be added casually — it is exactly the capability §6.3 exists to prevent — so it needs an explicit decision rather than a quiet implementation. |

---

## 12. Provenance

Derived from the team sync-up of 31 August 2026 between Devansh and Sriram.
Project names spoken on the call were not transcribed reliably and are
deliberately absent rather than guessed — see Q-10.

### The prototype is not a reference implementation

The calendar prototype shown during the call was built to convey intent, before
most of these rules existed. Where it disagrees with this specification, this
specification wins:

| Prototype does | This spec requires |
|---|---|
| Annual allowances | Monthly, with carry-forward (FR-BAL-01, FR-BAL-05) |
| Allowances hardcoded | Read from admin configuration (FR-BAL-02) |
| Instant self-approval | Request, notify, lead decides (FR-APPR-01) |
| Any day editable forever | Locked once its date passes (FR-BOOK-08, §6.3) |
| Anyone edits holidays | Admin only (FR-HOL-07) |
| No authentication; hardcoded to one person | Authentication and three roles (FR-AUTH-01…07) |

Half-day bookings and the reason field are the two things it carries over
correctly — both survive into FR-BOOK-02 and FR-BOOK-03.
