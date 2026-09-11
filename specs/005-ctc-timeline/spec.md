# CTC, Monthly Profit and the Allocation Timeline — V5

| | |
|---|---|
| **Feature** | `005-ctc-timeline` |
| **Status** | Settled 12 September 2026 — decisions in §7, being built |
| **Source** | Product owner, 12 September 2026 |
| **Depends on** | [`003`](../003-management/spec.md) (managers, money), in production |
| **Supersedes** | `003` §4.1 *cost rate* — see §3.1 |

Three things:

1. **CTC with dates** — each person's cost to company as a dated history:
   past, current and upcoming figures, so a change next month is entered now
   and every calculation uses the figure in force on the day.
2. **Monthly profit** — revenue, cost and profit percentage per employee and
   per project, month by month, for past months (from logged hours) and
   future months (from allocations).
3. **The timeline matrix** — people as rows, months (or weeks) as columns,
   each allocation a coloured bar spanning its dates, sized by its share.

---

## 1. Context

`003` priced hours with a single hourly cost rate per person, captured onto
each time entry as it was saved. That answered "what did this project cost"
but not "what does this person cost us *this month*, and what did their time
earn" — and a single current figure cannot represent a raise that starts next
quarter or the rate that applied last year.

`003` §9 still governs: these are figures for running projects, not for
comparing people. They are visible to managers and admins only, tables sort by
name, and a person never sees their own.

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — An admin records a person's annual CTC with a start date and an optional end date; several periods per person, never overlapping.
- **G-2** — Project cost (COGS) is derived from the CTC in force on each entry's date — past entries at past rates, future plans at upcoming rates.
- **G-3** — Per person and per project, per month: revenue, cost, profit and profit %, past and future.
- **G-4** — A timeline that shows at a glance who is on what, when, and how much of them.

### 2.2 Non-goals

- Payroll. CTC here is the cost the company attributes; nothing is paid from it.
- Revenue recognition for invoicing. Revenue is spread over a project's timeline for planning; finance's books are elsewhere.
- Editing a CTC period in place. Add a new one; remove a wrong one. The history is the point.

---

## 3. The model

### 3.1 CTC replaces the hourly rate (Q-02)

| Term | Meaning |
|---|---|
| **CTC period** | `annual_ctc` for a person from `starts_on` to `ends_on` (null = until further notice). Entered annually (Q-03), shown monthly. |
| **Monthly CTC** | annual ÷ 12 |
| **Daily cost** on a working day in month M | monthly CTC ÷ working days in M |
| **Hourly cost** in M | daily cost ÷ 8 |
| **COGS** of a project | Σ over entries: hours × hourly cost of the CTC period covering the entry's date |

`profiles.cost_rate_hourly` and `time_entries.cost_rate_snapshot` are
retired: not read, not written, not shown. They stay in the schema so nothing
built on `003` breaks; a later migration may drop them.

A person with no CTC period covering a date is **unrated for that date**.
`003` FR-FIN-06 still applies: every figure that omits an unrated person says
so and names them.

### 3.2 Revenue by month (Q-01)

A project's **timeline** is its earliest phase start to its latest phase end.
Its revenue is spread evenly over the working days of that timeline; a month's
share is revenue × (working days of the timeline inside the month ÷ working
days of the whole timeline). A project with revenue but no phases has no
monthly revenue, and the API says so.

### 3.3 Attribution by month

For a project P and month M, each person's share of P's revenue in M is:

- **Past months** (M ended before today): their logged hours on P in M ÷ all logged hours on P in M. *Basis: actual.*
- **Current and future months**: their allocated capacity-days on P in M ÷ all allocated capacity-days on P in M. *Basis: planned.*

If nobody logged (past) or is allocated (future), that month's revenue for P is
**unattributed** and reported as such, so the project total still reconciles.

### 3.4 Per person, per month

| | |
|---|---|
| Revenue | Σ over projects of their share of that project's month revenue |
| Cost | Their monthly CTC, pro-rated by the working days each period covers |
| Profit | Revenue − Cost |
| Profit % | Profit ÷ Revenue (blank when revenue is 0) |

Per project, per month: revenue as §3.2; cost = Σ over people of (hours × hourly cost) for past months, (allocated days × daily cost) for future months; profit and profit % likewise.

---

## 4. Rules

### 4.1 CTC — FR-CTC

| ID | Requirement |
|---|---|
| FR-CTC-01 | Only an admin MAY add or remove a CTC period. |
| FR-CTC-02 | Periods for one person MUST NOT overlap; the database refuses it. |
| FR-CTC-03 | Adding a period that starts inside an open-ended one MUST close the open one the day before — that is how "CTC changes next month" is entered. |
| FR-CTC-04 | Every add and remove MUST be audited. |
| FR-CTC-05 | CTC and every figure derived from it MUST be visible to managers and admins only (`003` FR-FIN-07). The browser role MUST have no access to the table. |

### 4.2 Monthly profit — FR-PNL

| ID | Requirement |
|---|---|
| FR-PNL-01 | Per person and per project, per month over a chosen range: revenue, cost, profit, profit %, and whether the month is actual or planned. |
| FR-PNL-02 | Incompleteness MUST be loud: a person unrated for any day in the month, or a project with revenue but no timeline, is flagged on that cell and named. |
| FR-PNL-03 | Per-person tables sort by name. No column sorts by money. |

### 4.3 Timeline — FR-TL

| ID | Requirement |
|---|---|
| FR-TL-01 | Rows are people, columns are months, switchable to weeks. |
| FR-TL-02 | Each allocation is a bar from its start to its end, labelled with the project, one colour per project. |
| FR-TL-03 | A bar's height is its percent of the person; 100% fills the row, two 50% bars stack. Over 100% overflows visibly and is flagged. |
| FR-TL-04 | Manager and admin only. |

---

## 5. Data model

```text
cost_periods   id uuid PK
               user_id uuid → profiles
               annual_ctc numeric(14,2) ≥ 0
               starts_on date NOT NULL
               ends_on date NULL                  -- until further notice
               EXCLUDE (user_id =, daterange &&)  -- FR-CTC-02
               created_by, created_at
```

RLS on, no policies, no grants to the browser role (FR-CTC-05).

---

## 6. Where money now lives

| Figure | `003` | `005` |
|---|---|---|
| Person's cost | `profiles.cost_rate_hourly` | `cost_periods`, dated |
| Rate on an entry | `time_entries.cost_rate_snapshot` | derived from the period covering `date` |
| Project COGS | hours × snapshot | hours × hourly cost (§3.1) |
| Monthly view | — | §3.2–3.4 |

---

## 7. Decisions

| ID | Question | Decision |
|---|---|---|
| **Q-01** | How is a lump-sum revenue spread over months? | **Evenly across the project timeline by working days**; past months attributed by logged hours, future by allocation. Product owner. |
| **Q-02** | CTC beside the hourly rate, or instead of it? | **Instead.** Hourly cost is derived from the dated CTC; the `003` rate and snapshot are retired. Product owner. |
| **Q-03** | Annual or monthly entry? | **Annual, shown monthly.** Product owner. |
| **Q-04** | Edit a period, or add and remove? | **Add and remove.** Not asked; an editable history is not a history. |
| **Q-05** | What about the current month? | **Planned.** Half a month of hours would understate revenue; allocation is the better estimate until the month closes. Not asked. |

---

## 8. What this must not become

`003` §9, unchanged, and one more line: **CTC is the most sensitive number in
the system.** It is cost to company, entered by an admin, seen by the manager
tier, never by the person, never labelled salary. The monthly profit table
exists to answer "is this project paying for the people on it", and the
product declines to answer "who is the least profitable person" by sorting
only by name and offering no ranking, anywhere.
