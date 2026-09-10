# Management Layer — V3

| | |
|---|---|
| **Feature** | `003-management` |
| **Status** | Built 11 September 2026 — decisions in §8; migration `009_management.sql` |
| **Source** | Product owner, 11 September 2026 |
| **Depends on** | [`001`](../001-leave-calendar/spec.md) and [`002`](../002-timesheets/spec.md), both in production |

Four things, one of which reverses a decision made in `002`:

1. A **manager** role below admin that runs projects.
2. **Project financials** — cost rates, revenue, COGS and margin.
3. **Non-project activities** — logging time that is not for a client project.
4. A **per-person allocation timeline** — who is on what, now and next.

---

## 1. Context

`002` answered *what did people work on*. It deliberately stopped short of *what
did it cost* — Q-05 chose hours over money because money implies a cost rate
per person, which is salary-adjacent data in a system every lead can read.

The business now needs the cost answer to defend budgets, and needs delivery
heads to run projects without being admins. Both are reasonable. Both widen who
can see what, and this spec is mostly about drawing that line carefully.

### 1.1 The reversal, stated plainly

`002` §9 Q-05: **"Hours only."** Reason recorded: *"Money implies per-person
rates, which is salary-adjacent data in a system every lead can read."*

This spec reverses that. It does so by adding a **visibility tier above lead**
rather than by exposing rates to leads: cost rates and money are readable by
managers and admins, and by nobody else — not by leads, and not by the person
whose rate it is (§8, Q-01).

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — A manager can create and run projects — phases, budget, timeline, allocations — without admin rights.
- **G-2** — Per project: revenue, cost of goods sold (COGS) from logged hours × cost rates, and margin.
- **G-3** — Per person: hours, COGS and attributed revenue across projects, for managers and admins only.
- **G-4** — Everyone can log time that is not for a project — learning, internal work, admin.
- **G-5** — A manager can see every person's allocations laid out over time, and where they are over-committed.

### 2.2 Non-goals

- Invoicing, billing, payment tracking, tax. Revenue is a number an admin or manager types in; nothing is sent to a client.
- Multiple currencies. One, set in `app_settings`.
- Salary. `cost_rate_hourly` is a fully-loaded cost the company chooses to attribute; it is not, and must not be presented as, what anybody is paid.
- Task-level tracking. Reaffirmed from `002` §2.2 (§8, Q-03).
- Leave approval by managers. That stays with the reporting line (`profiles.lead_id`). A manager runs *projects*, a lead runs *people*; the two are different jobs and a person may hold both.

---

## 3. Roles

| Role | Runs | Sees |
|---|---|---|
| `user` | Their own leave and time | Their own |
| `lead` | Their reports' leave | Their reports; effort analytics for their reports; **no money** |
| `manager` | **Projects** — create, phases, budget, revenue, allocations | **All projects**, all allocations, all effort; **cost rates, COGS, margin** |
| `admin` | Everything: people, allowances, holidays, settings, backfill | Everything |

Roles remain cumulative in the sense that everyone is also a `user`. A manager
is **not** automatically a lead — they approve nobody's leave unless they are
also somebody's `lead_id`. A lead is **not** a manager — they see hours, never
money.

**Managers see every project** (§8, Q-02). This was chosen against the
recommendation, and the consequence is recorded so it is never mistaken for an
oversight: every delivery head can see every other project's margin and every
person's cost rate. The alternative — managers see only projects they own —
was offered and declined.

---

## 4. Financial model

### 4.1 Definitions

| Term | Meaning | Where it lives |
|---|---|---|
| **Cost rate** | Fully-loaded hourly cost the company attributes to a person. Set by an admin. | `profiles.cost_rate_hourly` |
| **Revenue** | What the project is worth — the contract value or internal budget. Set by a manager or admin. | `projects.revenue` |
| **COGS** | Σ (hours logged × that person's cost rate at the time of logging) | Derived — never stored |
| **Margin** | Revenue − COGS | Derived |
| **Attributed revenue** (per person) | Revenue × (their hours ÷ total hours on the project) | Derived |

### 4.2 Rules

- **Rates are captured onto each time entry when it is saved** (`time_entries.cost_rate_snapshot`). A rate change next year must not silently re-price last year's project. This is the same reasoning as `002` storing `phase_id` on the entry.
- A person with no cost rate produces COGS of **unknown**, not zero. A project total that silently omits an unrated person's hours reads as a better margin than the truth. Every financial figure carries a `complete: bool` and names who is unrated.
- Money is `numeric`, never float. Same reasoning as hours (`002` §8) and half-days (`001` §6.2).
- Revenue on a project with no logged hours has no COGS and therefore no meaningful margin; the API says so rather than reporting 100%.

### 4.3 What nobody below manager can see

Not `cost_rate_hourly`. Not `revenue`. Not COGS, margin, or attributed revenue. Not their own. **Not derivable**: no endpoint reachable by a `user` or `lead` returns any of these, and no aggregate a lead can see is computed from them.

This is enforced in three places, on purpose: the API never selects the columns for the wrong caller, the financial endpoints are guarded separately from the effort endpoints, and RLS withholds the columns from the browser's own connection.

---

## 5. Functional requirements

Keywords follow RFC 2119.

### 5.1 Roles — FR-ROLE

| ID | Requirement |
|---|---|
| FR-ROLE-01 | The system MUST support a `manager` role, between `lead` and `admin`. |
| FR-ROLE-02 | A manager MUST be able to create and edit projects, phases, budgets, revenue and allocations. |
| FR-ROLE-03 | A manager MUST NOT be able to manage people, allowances, holidays, settings, or backfill leave. |
| FR-ROLE-04 | A manager MUST NOT be able to approve or reject leave unless they are the person's `lead_id`. |
| FR-ROLE-05 | A manager MUST see all projects, all allocations and all effort analytics (§8, Q-02). |
| FR-ROLE-06 | Only an admin MAY assign or change roles. |

### 5.2 Financials — FR-FIN

| ID | Requirement |
|---|---|
| FR-FIN-01 | An admin MUST be able to set a person's hourly cost rate. |
| FR-FIN-02 | A manager or admin MUST be able to set a project's revenue. |
| FR-FIN-03 | The cost rate MUST be captured onto each time entry when saved, and COGS MUST be computed from the captured rate, never the current one. |
| FR-FIN-04 | Per project: revenue, COGS, margin, and per-person hours, COGS and attributed revenue. |
| FR-FIN-05 | Per person, across projects: hours, COGS, attributed revenue. |
| FR-FIN-06 | Any figure that omits an unrated person MUST be marked incomplete and MUST name who is unrated. |
| FR-FIN-07 | Cost rates and every derived money figure MUST be visible to managers and admins only. A user MUST NOT see their own rate or revenue (§8, Q-01). |
| FR-FIN-08 | Changes to a cost rate or a project's revenue MUST be written to the audit log. |

### 5.3 Activities — FR-ACT

| ID | Requirement |
|---|---|
| FR-ACT-01 | A time entry MUST be either for a project or for an activity, never both, never neither. |
| FR-ACT-02 | Activities are a fixed set: `learning`, `internal`, `admin`, `other`. |
| FR-ACT-03 | Activity hours MUST appear in a person's timesheet and in coverage (`002` FR-ANALYTICS-05) — a day spent learning is a logged day. |
| FR-ACT-04 | Activity hours MUST NOT appear in any project's effort or financials. They have no project. |
| FR-ACT-05 | Activity hours MUST carry no cost rate snapshot and produce no COGS. |

### 5.4 Resource timeline — FR-RES

| ID | Requirement |
|---|---|
| FR-RES-01 | A manager or admin MUST be able to see, per person, their allocations across a date range — present and future. |
| FR-RES-02 | The view MUST show each person's total allocated percentage per week and flag weeks over 100%. |
| FR-RES-03 | The view MUST show unallocated capacity — people and weeks where the total is under 100%. |
| FR-RES-04 | Approved leave MUST be visible on the timeline, so a fully-allocated fortnight that is also a holiday reads as what it is. |

### 5.5 Timesheet — FR-TIME (amendments to `002`)

| ID | Requirement |
|---|---|
| FR-TIME-11 | The note field MUST be prominent and labelled as *what was done*, since it is the only task record (§8, Q-03). |
| FR-TIME-12 | The day form MUST offer activities alongside allocated projects. |

---

## 6. Data model (indicative)

```
profiles          + role IN ('user','lead','manager','admin')
                  + cost_rate_hourly numeric(10,2)      -- admin-set; manager/admin-readable
projects          + revenue numeric(14,2)               -- manager/admin-set and -readable
time_entries      ~ project_id nullable
                  + activity text  CHECK IN (learning, internal, admin, other)
                  + CHECK ((project_id IS NULL) <> (activity IS NULL))
                  + cost_rate_snapshot numeric(10,2)    -- captured at save; NULL for activities
app_settings      + currency_code  '"INR"'
```

Two partial unique indexes replace `002`'s single one: one per (user, date,
project) where the entry is for a project, one per (user, date, activity) where
it is an activity.

---

## 7. Relationship to earlier specs

- `001` **Q-06 / NFR-05** (leave reasons are health information) is **unchanged and NOT widened**. Managers do not gain access to leave reasons; `can_view_reason` stays person / lead / admin.
- `002` **Q-08** (who sees an individual timesheet) gains managers, through a **separate** rule, `can_view_timesheet`. The two were one function until managers arrived. A timesheet note is "built the export API"; a leave reason may be "chemotherapy". Keeping them apart is what stops widening one from silently widening the other.
- `002` **§10** (not a credibility system) still governs. Financial views are **per project** and **per person across projects**; there is still no ranking, and per-person tables sort by name. A person's own attributed revenue is not shown to them (Q-01) precisely because "how much money did I make the company this month" is the leaderboard nobody asked for.
- `002` **FR-ANALYTICS-05** (coverage) counts activity days as logged.

---

## 8. Decisions

Settled 11 September 2026, before any schema was written.

| ID | Question | Decision |
|---|---|---|
| **Q-01** | Who sees cost rates and money? | **Admin and manager only.** Not leads. Not the person themselves. Reverses `002` Q-05 deliberately, by adding a tier rather than exposing rates to leads. |
| **Q-02** | Does a manager see all projects or only their own? | **All projects.** Chosen against the recommendation; consequence recorded in §3. |
| **Q-03** | "Tasks done" — structured or free text? | **Free text.** The note field, made prominent. Task tracking stays in ClickUp (`002` §2.2). |
| **Q-04** | Deploy `002` before building this? | **Yes.** Done 11 September; `002` is in production. |
| **Q-05** | How is COGS priced when rates change? | **Snapshot at save.** Each entry carries the rate in force when it was logged. Not asked; decided because the alternative silently re-prices history. |
| **Q-06** | What does "revenue per employee" compute? | **Attributed revenue** = project revenue × (their hours ÷ total project hours), per project, summed across projects. Not asked; the phrase was ambiguous and this is the reading that is both computable from the data and defensible to a finance person. |
| **Q-07** | Currency? | **One, `INR`, in `app_settings.currency_code`.** Formatting only. |

---

## 9. What this must not become

`002` §10 said a timesheet is where "not a credibility system" stops being
decorative. Money is where it stops being a slogan.

The same numbers answer "was this project profitable" and "which employee is
cheapest per hour". The product serves the first and declines the second:

- No per-person cost comparison, ranking, or "efficiency" figure, anywhere.
- Per-person tables in financial views are sorted by name.
- A person cannot see their own rate or attributed revenue (Q-01). This is not
  secrecy for its own sake — it is that the number, once seen, becomes the
  thing people optimise, and `001` §1 is about a tool people will actually use.
- `cost_rate_hourly` is labelled *cost rate* in every screen and never
  *salary*, *pay* or *rate of pay*, because it is not that and the
  distinction matters to the person it describes.
