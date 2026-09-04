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

Every project has up to three phases, each with its own start and end date:

| Phase | What it covers |
|---|---|
| `pre` | Pre-project work — scoping, estimation, pitching, setup |
| `delivery` | The project timeline proper |
| `support` | Post-delivery support |

Effort is logged against a phase, not merely a project. Without that split,
"we spent 400 hours on this" cannot distinguish a project that overran from one
that has been in unbudgeted support for a year — which is precisely the
distinction a budget conversation turns on.

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
| FR-PROJ-02 | A project MUST support three optional phases — `pre`, `delivery`, `support` — each with a start and an end date. |
| FR-PROJ-03 | Phase dates MUST be editable, and a change MUST NOT invalidate time already logged. |
| FR-PROJ-04 | A project MUST be archivable without deleting its history. |
| FR-PROJ-05 | Only an admin MAY create or edit a project. |

### 5.2 Allocation — FR-ALLOC

| ID | Requirement |
|---|---|
| FR-ALLOC-01 | An admin MUST be able to allocate a person to a project for a date range. |
| FR-ALLOC-02 | A person MUST be able to hold several concurrent allocations. |
| FR-ALLOC-03 | An allocation MUST carry an intended level of effort as a **percentage of capacity** (Q-02). |
| FR-ALLOC-04 | The system MUST surface when a person's concurrent allocations exceed full capacity. |
| FR-ALLOC-05 | Removing an allocation MUST NOT delete time already logged against that project. |

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
| FR-TIME-10 | Logging on a day with approved leave MUST warn and MUST NOT refuse (Q-03). The clash MUST be visible in analytics. |

### 5.4 Analytics — FR-ANALYTICS

| ID | Requirement |
|---|---|
| FR-ANALYTICS-01 | A lead MUST see hours logged by their own reports; an admin MUST see the whole organisation. |
| FR-ANALYTICS-02 | Per project and phase: total hours, hours per person, and the office/home split. |
| FR-ANALYTICS-03 | Logged hours MUST be comparable against the phase's budget, with over-run shown plainly. |
| FR-ANALYTICS-04 | A lead MUST be able to see what each report is currently working on. |
| FR-ANALYTICS-05 | Missing days MUST be visible. A timesheet that is merely incomplete MUST NOT read as a project that used few hours. |
| FR-ANALYTICS-06 | Forecast remaining capacity from allocations and remaining working days, excluding approved leave and declared holidays. |

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
projects        id, name, client, is_archived, created_at
project_phases  id, project_id, phase(pre|delivery|support),
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
| **Q-02** | How is allocation intent expressed? | **Percentage of capacity.** "50% on Acme until 31 Oct." Capacity is working days minus approved leave and declared holidays, which `001` already knows. Over-allocation (FR-ALLOC-04) is then simply concurrent percentages summing above 100. |
| **Q-03** | Logging hours on an approved leave day? | **Warn, but allow.** People do work on a sick day. Refusing makes the data clean and the humans lie, and that effort vanishes from the project. The clash is surfaced in analytics so it can be questioned rather than hidden. |
| **Q-04** | Does anybody approve a timesheet? | **No.** §2.2 stands. Hours are a record, not a request; nobody signs them off. |
| **Q-05** | Budget in hours or money? | **Hours only.** Money implies per-person rates, which is salary-adjacent data in a system every lead can read. Hours answer "did this take longer than planned" without going near that. |
| **Q-06** | Maximum loggable day? | **16 hours, enforced.** A sanity check against a mistyped 80, not a position on overwork. Configurable in `app_settings`. |
| **Q-07** | Logging against a project you are not allocated to? | **Allowed, and shown as unallocated effort.** The person who helped out for an afternoon is exactly the effort a budget conversation misses. Refusing it would push that work into somebody else's project or into nothing. |
| **Q-08** | Who may see an individual's timesheet? | **The person, their lead, and admins** — the same rule as a leave reason (`001` NFR-05). Project analytics aggregate across everyone, but a named individual's day is not browsable by a colleague. |
| **Q-09** | Import historical effort? | **Start empty.** Note this interacts with Q-01: once the grace period passes, past days cannot be logged at all, so any later decision to import history needs an admin backfill path like `001` A-21. Not built. |

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
