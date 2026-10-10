# Timesheets and Resource Utilisation — V2

| | |
|---|---|
| **Feature** | `002-timesheets` |
| **Status** | Settled 5 September 2026 — §9 answered, ready to build |
| **Source** | Product owner, 5 September 2026 |
| **Depends on** | [`001-leave-calendar`](../001-leave-calendar/spec.md), in production |

This promotes two items that `001` §10 listed as roadmap and §2.2 listed as
explicit non-goals: *"Day login and project time"* and *"Task allocation and
resource management"*. They are no longer deferred.

---

## 1. Context

The leave calendar answered *who is absent*. It cannot answer *what the people
who are present actually did*, and that is the question the business needs to
answer to defend a budget.

`001` §1 describes ClickUp failing for interns and junior engineers, so that
"work gets done but not recorded, and the tracking system stops describing
reality". The leave calendar sidestepped that by tracking only absence. This
feature walks into it directly, and inherits the same bar: **if logging a day
takes longer than not logging it, it will not be logged, and the data will be
worse than none** — because a half-populated timesheet invites conclusions that
a visibly empty one does not.

### 1.1 The bar V2 must clear

`001` §1.1 set fifteen seconds for marking leave. The equivalent here: **a full
day logged in under thirty seconds**, from a phone, at the end of a day when
the person wants to stop working. Anything that feels like filling in a form
will be filled in on Friday for the whole week, from memory, and will be wrong.

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — An admin can record a project and the dates of each of its phases.
- **G-2** — An employee can log a day's hours against the projects they worked on, with a short note, in under thirty seconds.
- **G-3** — Hours are recorded split by where they were worked: office or home.
- **G-4** — An admin can allocate people to projects, including one person across several.
- **G-5** — A lead or admin can see, per project, the effort spent against what was expected — enough to defend or challenge a budget.
- **G-6** — A lead can see what their team is currently working on.
- **G-7** — Forecast remaining capacity from allocations, phase dates, and known leave.

### 2.2 Non-goals

- Task-level tracking. This records effort against a **project phase**, not against tickets. ClickUp keeps that job.
- Billing, invoicing, rates, or currency. Budget is expressed in **hours**, not money (§9, Q-05).
- Approval of timesheets. Nobody signs off hours (§9, Q-04).
- Start/stop timers. Hours are entered as numbers, not measured.
- Automatic time capture, screenshots, or activity monitoring. `001` §10 is explicit that location capture, if it ever ships, ships as "a small cultural shift so the team knows who is working on what — explicitly not a credibility or accountability system". **That framing governs this feature too.**

---

## 3. Concepts

### 3.1 Project

A named piece of work with a client or internal owner, and a lifecycle made of
**phases**.

### 3.2 Phase

Every project has up to four phases, each with its own start and end date:

| Phase | What it covers |
|---|---|
| `pre` | Pre-project work — scoping, estimation, pitching, setup |
| `delivery` | The project timeline proper |
| `support` | Post-delivery support |
| `spillover` | Delivery work that overran the agreed timeline, unpaid |

Effort is logged against a phase, not merely a project. Without that split,
"we spent 400 hours on this" cannot distinguish a project that overran from one
that has been in unbudgeted support for a year — which is precisely the
distinction a budget conversation turns on.

`spillover` makes the overrun explicit: people stay allocated and log against
it, their cost counts, and the project's revenue is not spread into it (spec
005 §3.2).

### 3.3 Allocation

An admin assigns a person to a project for a period, at some intended level of
effort. One person may hold several concurrent allocations.

Allocation expresses **intent**; a time entry records **fact**. Comparing the
two is the whole point of the analytics, so the two must never be conflated in
storage.

### 3.4 Time entry

One person, one date, one project phase, hours split office/home, and a short
note.

A day is therefore several entries when somebody worked on several projects,
which is the normal case.

---

## 4. User scenarios

### 4.1 Logging a day

```gherkin
Given Sriram is signed in
  And he is allocated to "Acme Portal" and "Internal Tooling"
 When he opens Today and enters 5 hours on Acme Portal (office)
  And 2 hours on Internal Tooling (home)
  And a note on each
  And confirms
 Then his day totals 7 hours
  And both entries are recorded against the phase active on that date
```

### 4.2 Logging on a day already marked as leave

```gherkin
Given Tarun has an approved full day of sick leave on the 3rd
 When he attempts to log hours on the 3rd
 Then he is warned that the day is recorded as sick leave
  And the outcome is governed by Q-03
```

### 4.3 A lead checking the week

```gherkin
Given Devansh is signed in as a lead
 When he opens the team's week
 Then he sees, per report, hours logged per day and which projects they went to
  And days with no entry at all are visibly distinct from days with zero hours
```

### 4.4 Defending a budget

```gherkin
Given "Acme Portal" has a delivery phase of 1 Jun to 31 Aug
  And a budget of 400 hours for that phase
 When Vinita opens the project's analytics
 Then she sees hours logged against that phase, by person and in total
  And how that compares to the 400
  And how much of it was worked from home
```

### 4.5 Forecasting

```gherkin
Given three people are allocated to "Acme Portal" at 50% until 31 October
 When Vinita opens the forecast
 Then she sees the capacity those allocations imply for the remaining working days
  And that figure excludes approved leave and declared holidays
```

---

## 5. Functional requirements

Keywords follow RFC 2119. §9 is settled; these reflect those decisions.

### 5.1 Projects — FR-PROJ

| ID | Requirement |
|---|---|
| FR-PROJ-01 | An admin MUST be able to create a project with a name and a client or owner. |
| FR-PROJ-02 | A project MUST support four optional phases — `pre`, `delivery`, `support`, `spillover` — each with a start and an end date. |
| FR-PROJ-03 | Phase dates MUST be editable, and a change MUST NOT invalidate time already logged. |
| FR-PROJ-04 | A project MUST be archivable without deleting its history. |
| FR-PROJ-04a | An archived project MUST NOT accept new allocations — neither a new one nor an edit that extends an existing one's dates (shortening is allowed) — and MUST NOT accept new time entries; a line already logged against it on that day may still be re-saved. Refusals are 422 "*name* is archived." The Time page MUST NOT offer an archived project except where it is already logged that day. Existing entries and allocations stay readable. |
| FR-PROJ-06 | A project MUST have one category: `client` (paid client engagement, the default), `poc` (client POC or general), `product` (Nunnari product development) or `internal` (internal tools, applications, website). |
| FR-PROJ-07 | A project MAY name one lead — the person who leads it — set on create or edit by anyone who may edit projects. The lead MUST be an existing, active person (otherwise 422). It is a label, not a permission: it changes nobody's access. The project lists (Projects, Effort → Projects, MCP) MUST show the lead's name, and the Projects page MUST offer a filter to only the projects the viewer leads. Clearing it is allowed. |
| FR-PROJ-08 | A project MUST have a status: `confirmed` (won; the default) or `tentative` (pipeline — planned, not yet won), and MAY have a probability, 0–100, of being won. Whoever may edit a project sets both, leads included: neither is money — but a project with history stays confirmed (FR-PROJ-09). A tentative project MAY have phases, revenue and allocations, and allocations to it are tentative. It MUST NOT accept time entries — refused 422 "*name* is tentative.", except that a line already logged that day may be re-saved — and the Time page MUST NOT offer it except where it is already logged that day. Tentative allocations MUST be shown, marked as such, wherever allocations are (forecast, resources, bench, timeline); over-allocation (FR-ALLOC-04) and the bench MUST count confirmed allocations only, with a separate signal for over 100% including tentative work. Its money is pipeline, outside every confirmed figure (spec 005 FR-PNL-05). |
| FR-PROJ-09 | A confirmed project MUST NOT be made tentative (FR-PROJ-08) once it has a time entry or an invoiced milestone (`006` FR-MILE-05) — refused 422 "*name* has time logged against it, so it cannot be made tentative." or "*name* has invoiced milestones, so it cannot be made tentative.", whoever asks. Logged hours are cost already spent and an invoice is money already asked for; making the project tentative would move them out of the confirmed monthly profit and the receivables after the fact, and a lead, who may set the status but has no money, would be changing the money figures. A project with neither MAY still be made tentative by anyone who may edit it. |
| FR-PROJ-05 | Only an admin MAY create or edit a project. *Superseded by spec 003 FR-ROLE-02 and FR-ROLE-07: managers and leads may too.* |

### 5.2 Allocation — FR-ALLOC

| ID | Requirement |
|---|---|
| FR-ALLOC-01 | An admin MUST be able to allocate a person to a project for a date range. |
| FR-ALLOC-02 | A person MUST be able to hold several concurrent allocations. |
| FR-ALLOC-03 | An allocation MUST carry an intended level of effort as a **percentage of capacity** (Q-02). |
| FR-ALLOC-04 | The system MUST surface when a person's concurrent allocations exceed full capacity. |
| FR-ALLOC-05 | Removing an allocation MUST NOT delete time already logged against that project. |
| FR-ALLOC-06 | An allocation's dates and percent MUST be editable (the end may not precede the start; 0 < percent ≤ 100), by whoever may allocate that person (spec 003 FR-ROLE-08). Every edit MUST be audited with the before and after values, as a removal is with the removed allocation's details. |

### 5.3 Time entry — FR-TIME

| ID | Requirement |
|---|---|
| FR-TIME-01 | A user MUST be able to log hours for a date against a project, split into hours worked from the office and hours worked from home. |
| FR-TIME-02 | A short note MUST be captured per entry. |
| FR-TIME-03 | A user MUST be able to log against several projects on the same date. |
| FR-TIME-04 | Hours MUST support half-hour granularity at minimum. |
| FR-TIME-05 | The system MUST reject a day whose total exceeds a configured maximum, default **16 hours** (Q-06). |
| FR-TIME-06 | A user MAY log time against a project they are not allocated to; such effort MUST be shown as unallocated rather than refused (Q-07). |
| FR-TIME-07 | An entry MUST record which phase was active on that date. |
| FR-TIME-08 | A user MUST be able to correct an entry until the **end of the following week** in Asia/Kolkata, and MUST NOT be able to afterwards (Q-01). |
| FR-TIME-09 | Every change to an entry MUST be recorded in the audit log with actor and timestamp. |
| FR-TIME-10 | Logging on a day with approved leave (casual, sick or comp-off; work from home is a working day and gets no warning) MUST warn and MUST NOT refuse (Q-03). The clash MUST be visible in analytics. |

### 5.4 Analytics — FR-ANALYTICS

| ID | Requirement |
|---|---|
| FR-ANALYTICS-01 | A lead MUST see hours logged by their own reports; an admin MUST see the whole organisation. Three views are company-wide for a lead too (Q-10): the per-project effort views (FR-ANALYTICS-02/03) show hours on every project from everyone who logged them, the capacity forecast (FR-ANALYTICS-06) covers every allocation, and the hours by category (FR-ANALYTICS-09) total everyone's. The forecast's per-person hours are at that person's contracted hours (`005` FR-CTC-06), so a lead can tell a part-timer's hours a week; that is deliberate — hours are not money — and the CTC they come from stays admin-only. Coverage, missing days and current work (FR-ANALYTICS-04/05) stay limited to the lead's reports. None of these shows money (`003` Q-01). |
| FR-ANALYTICS-02 | Per project and phase: total hours, hours per person, and the office/home split. |
| FR-ANALYTICS-03 | Logged hours MUST be comparable against the phase's budget, with over-run shown plainly. |
| FR-ANALYTICS-04 | A lead MUST be able to see what each report is currently working on. |
| FR-ANALYTICS-05 | Missing days MUST be visible. A timesheet that is merely incomplete MUST NOT read as a project that used few hours. |
| FR-ANALYTICS-06 | Forecast remaining capacity from allocations and remaining working days, excluding approved leave and declared holidays. Work from home is not leave: it is full capacity. |
| FR-ANALYTICS-07 | A day is *expected* (and so can be missing) only if it is a working day up to today, not a declared holiday or full day of leave (casual, sick or comp-off; work from home is a working day and is expected, and a half day of leave is still expected), and on or after the company setting `portal_start_date` (empty = no start date). A person whose `logs_time` is off is never expected to log. Coverage, the missing-days lists, the week view (`missing_days`), the nudges (`006` FR-NUDGE) and weekly sign-off (`006` FR-SIGN, `team_weeks`) all use this one definition. |
| FR-ANALYTICS-08 | A person MAY have a joining date and a leaving date, set by an admin (either may be empty = not recorded; leaving MUST NOT be before joining). No day before they joined or after they left is *expected* (FR-ANALYTICS-07), and no week wholly outside those dates is listed for sign-off or auto-confirmed (`006` FR-SIGN-05). These dates do not affect cost: that still comes from CTC periods (`005` FR-PNL-02). |
| FR-ANALYTICS-09 | Per month over a chosen range of up to 24 months (YYYY-MM; default the current calendar year), hours MUST be totalled per project category (FR-PROJ-06) in fixed category order: hours **logged**, and hours **planned** from allocations as in FR-ANALYTICS-06 (working days less that person's holidays and approved leave (not work from home), times the percent, at that person's contracted hours, `005` FR-CTC-06), except that only the holidays of the person's own location (`006` FR-LOC-02) count, where the forecast counts every location's — so with location-specific holidays the two can differ. Allocations to a tentative project (FR-PROJ-08) MUST NOT count as planned hours: they are left out, not shown separately — the forecast shows them, marked tentative. A project with no category counts as `client`. Time logged against no project (an activity, `003` FR-ACT) MUST appear as its own "not on a project" row with logged hours and no plan, never folded into a category. Each month is marked actual (ended) or planned (current and future), as `005` FR-PNL. Like FR-ANALYTICS-02/03 and -06 it is company-wide for a lead, totals categories and never people, and shows no money; the money by category is `005` FR-PNL-04 and is shown beside it to managers and admins only. A start month after the end month is refused with 422. |

**FR-ANALYTICS-05 is the one that protects every other number on the page.**
Effort totals computed over a partly-filled timesheet are not merely imprecise,
they are biased low, and they will be quoted in a budget conversation as though
they were complete.

---

## 6. Relationship to the leave calendar

This feature shares its people, its roles, its audit log and its timezone with
`001`, and MUST NOT fork any of them.

- **Roles.** Unchanged: `user`, `lead`, `admin`. Analytics visibility follows the existing reporting line (`profiles.lead_id`) and the same `can_decide` / population rules.
- **Dates.** Asia/Kolkata, calendar dates, per `001` NFR-03. The same trap applies and the same helpers must be used.
- **Reasons and notes.** A time-entry note is not health information, but it is still something a colleague wrote about their own day. Access follows the same rule as a leave reason (`001` NFR-05): the person, their lead, and admins.
- **Audit log.** The existing append-only `audit_log` (`001` migration 005) records time-entry changes too. It is not re-implemented.
- **Leave.** Capacity and utilisation are meaningless without it. A person on leave has no available hours, and the forecast must say so.

---

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | Logging a full day MUST take under thirty seconds on a phone. |
| NFR-02 | Today's form MUST pre-fill from the person's current allocations, so the common case is adjusting numbers rather than choosing projects. |
| NFR-03 | Analytics MUST remain responsive for a company of tens of people over a few years of daily entries — roughly 10⁴–10⁵ rows, not more. |
| NFR-04 | Every hours figure MUST be traceable to the entries that compose it. A total nobody can decompose will not be believed, and should not be. |

---

## 8. Data model (indicative)

```
projects        id, name, client, is_archived, lead_id,
                status(confirmed|tentative) DEFAULT confirmed,  -- FR-PROJ-08
                probability smallint NULL CHECK (0–100), created_at
profiles      + joined_on date, left_on date NULL
                CHECK (left_on ≥ joined_on)                     -- FR-ANALYTICS-08
project_phases  id, project_id, phase(pre|delivery|support|spillover),
                starts_on, ends_on, budget_hours
allocations     id, project_id, user_id, starts_on, ends_on,
                percent numeric(5,2), created_by
time_entries    id, user_id, date, project_id, phase_id,
                hours_office numeric(4,2), hours_home numeric(4,2),
                note, created_at, updated_at
```

Hours are `numeric`, never float — the same reasoning as `001` §6.2, and the
sums here are far larger.

`time_entries` carries `phase_id` rather than deriving the phase at read time.
FR-PROJ-03 allows phase dates to move, and a historical entry must keep saying
which phase it was logged against.

---

## 9. Decisions

Settled 5 September 2026. The first four changed the data model and were
answered before any schema was written; the remaining five ship with the
defaults below.

| ID | Question | Decision |
|---|---|---|
| **Q-01** | Edit window for time entries? | **Grace period, then lock.** Editable until the **end of the following week** in Asia/Kolkata, then permanently. A same-day lock like `001` §6.3 would guarantee a permanently incomplete timesheet, which FR-ANALYTICS-05 says is worse than none; always-editable would let a project's recorded effort change after the budget conversation it fed. Bounded, so history cannot be quietly rewritten months later. |
| **Q-02** | How is allocation intent expressed? | **Percentage of capacity.** "50% on Acme until 31 Oct." Capacity is working days minus approved leave and declared holidays, which `001` already knows. Work from home is not leave and does not reduce capacity. Over-allocation (FR-ALLOC-04) is then simply concurrent percentages summing above 100. |
| **Q-03** | Logging hours on an approved leave day? | **Warn, but allow.** People do work on a sick day. Refusing makes the data clean and the humans lie, and that effort vanishes from the project. The clash is surfaced in analytics so it can be questioned rather than hidden. |
| **Q-04** | Does anybody approve a timesheet? | **No.** §2.2 stands. Hours are a record, not a request; nobody signs them off. |
| **Q-05** | Budget in hours or money? | **Hours only.** Money implies per-person rates, which is salary-adjacent data in a system every lead can read. Hours answer "did this take longer than planned" without going near that. |
| **Q-06** | Maximum loggable day? | **16 hours, enforced.** A sanity check against a mistyped 80, not a position on overwork. Configurable in `app_settings`. |
| **Q-07** | Logging against a project you are not allocated to? | **Allowed, and shown as unallocated effort.** The person who helped out for an afternoon is exactly the effort a budget conversation misses. Refusing it would push that work into somebody else's project or into nothing. |
| **Q-08** | Who may see an individual's timesheet? | **The person, their lead, and admins** — the same rule as a leave reason (`001` NFR-05). Project analytics aggregate across everyone, but a named individual's day is not browsable by a colleague. |
| **Q-09** | Import historical effort? | **Start empty.** Note this interacts with Q-01: once the grace period passes, past days cannot be logged at all, so any later decision to import history needs an admin backfill path like `001` A-21. Not built. |
| **Q-10** | May a lead see effort and allocations beyond their own reports? | **Yes, for project effort and the capacity forecast** (owner decision, 4 October 2026). A lead running a project needs its whole effort picture and the company's allocations to plan, not only the slice their reports contributed. Hours only: money stays manager and admin (`003` Q-01), and coverage, missing days and an individual's week stay with the reporting line (Q-08). |

---

## 10. What this must not become

`001` §10 records the framing agreed for anything in this territory: the portal
is *"a small cultural shift so the team knows who is working on what —
explicitly not a credibility or accountability system"*, and *"the culture forms
first"*.

A timesheet is the point at which that framing stops being decorative. The same
data supports "this project needed more people than we budgeted" and "this
person logged fewer hours than that person", and the second reading arrives for
free unless the product actively declines to serve it.

Concretely, and pending sign-off:

- Analytics lead with **project** totals, not person leaderboards.
- No ranking, no per-person efficiency metric, no comparison of individuals.
- Missing days are shown as missing (FR-ANALYTICS-05), never as zero — the difference between "did not log" and "did nothing".
- Office/home split exists to understand where work happens, and `001` §10 already committed that this is not a credibility system. It MUST NOT appear in any per-person comparison.
