---
title: For managers
summary: Creating projects, phases, revenue, allocating people, and reading cost and margin.
order: 5
---

# For managers

A manager runs projects. That means creating them, setting their phases and
revenue, deciding who works on them and for what share of their time, and
reading what they cost.

A manager does not approve leave unless they are also somebody's approver, and
does not manage people, allowances or holidays. Those are admin jobs.

Leads use the same **Projects** page without the money: they can create
projects, set phases and categories, and allocate their own reports, but
never see or set revenue or milestones.

## Projects

**Projects** lists every project, grouped by category. To add one, give it a
name, a client (leave blank for internal work), a **category** and, if known,
its **revenue** — the contract value or the internal budget in the company's
currency.

The category says what kind of work the project is, and every report groups
by it:

- **Paid client engagement** — the default.
- **Client POC / general** — trials and general work for a client.
- **Nunnari product development** — our own products.
- **Internal tools / applications / website**.

Change a project's category from the dropdown beside its name.

A project can also name its **lead** — the person who leads it, say Devansh
on Hearsight. Pick one when you add the project, or change it later from the
*Lead* dropdown beside its name (*No lead* clears it). Only someone with an
active account can be named. The lead is a label: it shows on Projects and
on **Effort → Projects**, but it changes nobody's access — who may edit the
project or allocate to it stays exactly as before. Tick **Only projects I
lead** to see just the ones with your name on them.

Open **Phases, people & revenue** on a project to:

- **Set revenue** later, or change it. Every change is recorded.
- **Set a phase** — *Pre-project*, *Delivery*, *Post-delivery support* or
  *Spill-over* — with dates and, optionally, a budget in hours. Hours logged
  inside a phase's dates count against that phase, so a delivery that overran
  can be told apart from a year of unbudgeted support.
- **Spill-over** is delivery work that ran past the agreed timeline, unpaid.
  People stay allocated and log against it and their cost counts, but the
  project's revenue is **not** spread into it — so the overrun shows as lost
  profit, and the months that were sold keep their revenue.
- **Allocate** a person for a date range at a percentage of their capacity.
  Fifty percent means half of their working days in that range, after
  weekends, holidays and approved leave are taken out.
- **Edit** an allocation's dates or percentage from the same list, or remove
  it. Every change is recorded in the audit log.

A person can be allocated beyond 100%. The portal records it and then flags it
under Effort, because the honest thing is to show the over-commitment rather
than refuse to write it down.

Projects are **archived**, never deleted. The hours logged against a finished
project are exactly the history the reports exist for. An archived project
takes nothing new: it cannot be allocated, an allocation on it can be
shortened but not extended, and it is no longer offered on the Time page, so
no new hours can be logged against it. Everything already recorded stays.

### Tentative projects (pipeline)

Work that is coming but not yet won — *"a new project for the FluxBooks team
after 6 November"* — can be planned before it is signed. Set a project's
**Status** to **Tentative** when you add it, or from the dropdown beside its
name, and give its **chance of winning** as a percentage. Leads can do this
too; neither is money. A project that already has hours logged against it, or
an invoiced milestone, cannot be made tentative: its cost and invoices are
history, and moving them to the pipeline would rewrite past profit and the
receivables. A tentative project shows a dashed **Tentative** badge.

A tentative project can have phases, revenue and allocations, so you can
pencil people in. It is not offered on the Time page and takes no hours —
nobody works on work that has not been won. Everywhere allocations show, its
allocations are marked *tentative*:

- **Forecast** marks its capacity tentative. *Promised to more work than
  exists* counts confirmed work only; a separate card lists anyone who would
  be over 100% if the pipeline were won.
- **Resources** draws its bars faded and dashed, and leaves them out of the
  over-100% flag; a person over only with tentative work is noted.
- **Bench** judges free capacity on confirmed work, and shows the figure with
  the pipeline pencilled in beneath it.

Its money is kept out of every profit figure — see *By month* below. When the
work is won, set the status to **Confirmed**: from then on it counts like any
other project and hours can be logged against it.

### Milestones

Under the same panel, add **milestones**: a name, a due date and an amount, and
mark each one invoiced when it is. Once a project has milestones, its monthly
revenue follows them instead of being spread evenly over the timeline. If the
milestones do not add up to the project's revenue, the panel and the monthly
table say so.

Each milestone also tracks its invoice. Type the **invoice number** beside it
and choose **Mark invoiced** (dated today), then **Mark paid** when the client
pays. A milestone can only be paid once it has been invoiced, and a tentative
project's milestones cannot be invoiced until it is confirmed.

### Invoices

**Effort → Money → Invoices** lists every milestone on every project with its
status:

- **Upcoming** — due more than a week from now.
- **Due** — due today or within the next 7 days. Time to send the invoice.
- **Overdue** — past its due date and still not invoiced.
- **Invoiced** — sent, and still inside the payment terms.
- **Payment overdue** — invoiced and unpaid for longer than the payment terms
  (30 days unless an admin changes the `invoice_payment_terms_days` setting).
- **Paid**.

Overdue rows say how many days late they are. Filter by status with the
buttons above the table. Four totals sit at the top, over every milestone
whatever the filter: **receivable** (invoiced, not yet paid), **overdue
receivable** (the part of it past the payment terms), **due next 30 days**
(not yet invoiced) and **paid this month**. Tentative projects are left out
until they are confirmed: work not yet won bills nobody. Leads never see this.

Payment dates were not recorded before the Invoices list existed, so a
milestone invoiced earlier shows as unpaid — and, after 30 days, payment
overdue — until its payment is recorded. Ask Claude to set its `paid_on` to
the day the client actually paid; **Mark paid** would date it today and count
it in *paid this month*.

## Project health

Every project on the Effort page carries a colour. Open it to see three
dimensions, each with the numbers behind the colour: **burn** (hours logged
against budget, judged against how far along the timeline is), **margin**
(margin to date against the margin the allocations planned for) and
**schedule** (past the end date with budget unspent). Overall is the worst of
the three. A red is a prompt for a conversation, not a verdict.

## Effort statements

Open a project and choose **Effort statement for this month** for a
client-ready page: hours by person by day, totals by phase and the work notes.
Print it, or download the CSV to send with an invoice. It never contains
money. Weeks a lead has not yet confirmed are marked.

## Bench and hiring

**Bench** shows each person's allocated percent for the coming weeks and
flags weeks under the threshold as free capacity. **Hiring signal**, on the
same tab, compares the hours the confirmed allocations demand with what the
team can supply at target utilisation, month by month, and prices the
shortfall in people at an annual CTC you type in. Allocations to tentative
projects are shown beside it as **Tentative h** and are not in the shortfall:
work not yet won hires nobody until it is won. It is a planning number, not anybody's
figure. Supply and demand count each person's contracted hours, so a
part-timer adds their hours, not a full-time week; a hire is priced full time.

## Effort, with money

Managers see two extra tabs on **Effort**.

**Money** shows, per person across all projects and per project: hours,
**cost**, and **attributed revenue**.

- **Cost** is hours × that person's hourly cost, derived from the CTC in
  force on the day the hours were logged. A CTC change next month prices
  next month's hours and nothing before them. The hourly cost uses the hours
  a week that CTC pays for, so a contractor on 10 hours a week costs four
  times as much an hour as the same CTC would for someone full time.
- **Margin** is revenue minus cost.
- **Attributed revenue** shares a project's revenue among the people on it in
  proportion to their hours. It says what a person's time went into, not what
  they are worth.

If anybody on a project has no CTC for a day they logged, the figures are
marked **incomplete** and the portal names who. Their hours are an unknown
cost, not a free one. Ask an admin to record the CTC.

**Money → By month** lays revenue, cost and profit out month by month, per
person, per project (grouped under each category's total) and per
**category**. Months that have ended use the hours people logged;
the current month and the future use allocations, and are marked *planned*.
A project's revenue is spread evenly over the working days of its timeline,
from its first phase start to its last phase end, leaving out any spill-over
phase. A project with revenue but no phases shows no monthly revenue until a
phase is set. A month's share is the same whichever range of months you are
looking at. A person's monthly cost is their CTC for that month; months before
their first CTC are before they joined, and months after their last CTC ends
are after they left — both cost nothing. Someone who has left still appears in
the months they were paid. A cell marked
*incomplete* is missing somebody's CTC for some of its days.

Tentative projects are in none of those rows or totals. Below the table, a
separate **Pipeline** block lists each tentative project with its chance of
winning and, per month, its revenue and the planned cost of the people
pencilled in, then the month's pipeline total with **weighted** revenue —
revenue times the chance of winning. A month shows *? weighted* when a
tentative project in it has no chance of winning set. The pipeline cost is
already part of those people's cost above, since they are paid anyway, so it
is never added to the totals.

**Categories** on Effort shows the same hours by category that leads see —
logged and planned per month, with time on no project as its own row — and,
for you, the money by category beneath it: revenue, cost, profit and profit %
per category per month, taken from the same figures as **By month**.

Every month-based view — By month, Categories, the timeline in months,
Utilisation and the hiring signal — shows a whole year at a time. Switch between **Calendar year**
(January to December) and **Financial year** (April to March) with the buttons
on the view and step through years with the arrows; the choice is remembered
on your device and applies to every such view, including your own leave
history under Account.

**Resources** is the timeline: people as rows, months as columns, switchable
to weeks. Each allocation is a bar from its start to its end, coloured by
project and labelled with its name. A full-height bar is 100% of the person;
two half-height bars are two 50% allocations. Anything over 100% overflows
and is flagged. Bars on tentative projects are faded and dashed and are not
counted in that flag. Use the arrows to move through time.

## What the numbers are not

The same figures that answer "was this project profitable" could answer "who
is cheapest per hour". The portal serves the first question and declines the
second: tables are sorted by name, nothing ranks people, and a person is never
shown their own rate or attributed revenue.
