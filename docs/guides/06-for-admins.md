---
title: For admins
summary: People, roles, CTC, allowances, holidays, backfilling leave, and policy.
order: 6
---

# For admins

Everything under **Admin** is data, not code. Nothing about entitlements,
holidays or policy is fixed anywhere else.

## People

**Add someone** with their name, the Google address they sign in with, a role,
and who approves their leave. No password is set; their first Google sign-in
attaches to the account. Use the company convention for the address where the
person has no company mailbox.

In the table you can change a person's **role**, open their **CTC** history, and
**deactivate** them. Deactivation keeps every booking and hour they ever
logged; it only stops them signing in.

Untick **Logs time** for anyone who does not keep a timesheet (a partner, a
director). They are then never shown as missing time, never nudged about it,
and never in a lead's list of weeks to confirm. They can still log if they
choose to.

**Joined** and **Left** are the day someone started and the day they left.
Set the joining date when you add them, or later in the table; set the
leaving date when they go (it can still be set after you deactivate them).
No day before they joined or after they left is counted as missing time,
nudged, or put in front of a lead to sign off. Either can be left empty. They
do not change cost: that still comes from the person's CTC periods below, so
give their last CTC period an end date as well.

### Roles

- **User** — own leave and time.
- **Lead** — also approves leave for their reports, and runs projects for
  them: creates projects, sets phases and categories, allocates their own
  reports. Never sees money.
- **Manager** — also sets revenue, allocates anyone, and sees cost and
  margin. Managers see every project.

A lead's team is everyone whose **approver** is set to them under People. A
lead with nobody assigned has an empty team and cannot allocate anyone.
- **Admin** — everything.

Only an admin can change a role.

### CTC

CTC (cost to company) is the annual cost the portal attributes to a person's
time. It is entered with a **start date** and an optional **end date**, so a
person's history holds their past, current and upcoming figures at once:

- To record a change from next month, add a period starting on the 1st of
  next month. The current open-ended period closes automatically the day
  before.
- To record history, add periods with both dates.
- A wrong period is removed and re-added; periods are never edited in place.
- **Hours / week** says how many hours the CTC pays for: 40 is full time
  (the default), 10 is a contractor on ten hours a week. A part-timer's
  logged hours are costed at their real hourly rate, and their capacity,
  forecast and utilisation shrink to match. An allocation percent is a share
  of their own hours, so 100% of a 10-hour person is 10 hours a week. Going
  part-time is a new period, like any other CTC change.

Every project is costed at the CTC in force on the day each hour was logged,
and every future month is planned at the CTC in force on those days. A
person with no CTC for a date makes every figure for that date report as
**incomplete**, never cheaper. Days before a person's first CTC period are
before they joined, and days after their last period ends are after they
left: either way they cost nothing and are not flagged. A gap between two
periods is still flagged. When someone leaves, give their last period an end
date; once deactivated they still appear in the months they were paid. If
you deactivate someone without ending their CTC, they cost nothing from that
day on, but the months before are flagged **incomplete** until you give the
period its real end date. For
someone the company does not pay — a partner's staff, an unpaid intern —
record a CTC of 0 so their figures read complete. Only managers and admins see CTC; the person
never does, and it is never called salary.

## Locations

Under **Holidays**, add locations if the company works from more than one
place. A holiday can apply everywhere or to one location; a person belongs to
one location, set under People, and sees the holidays that apply to them.

## Checklists

**Checklists** holds onboarding and offboarding templates and the checklists
started from them. Start one for a person; tick items off, give each an owner
and a due date. Starting an offboarding checklist does not deactivate the
account; that stays a separate action under People.

## Notifications

**Notifications** is where Slack is proved and the schedules can be run by
hand: send yourself a test, run today's nudge, the Friday gaps, the Monday
over-allocation note, the morning who-is-out post, or the leadership digest.
Nothing sends until a Slack bot token is set on the server; the channel for
the morning post and the nudge hour are settings under Policy.

## Allowances

Set the monthly allowance per category. A row with no person is the company
default and applies from its month onwards until another row replaces it.
Nobody can book anything until a default exists.

## Holidays

Declare a holiday with a date and a name. Anyone who had booked that day gets
their days back and is told. Holidays cannot be booked and consume nobody's
allowance.

To add a year's calendar at once, use **Paste a list**: one holiday per line,
the date first and then the name, written either way:

```
2026-10-20 Ayudha Puja
20/10/2026, Ayudha Puja
```

Choose where the list applies (everywhere or one location). Before anything is
saved you see every line as it was read; a line that cannot be read is marked
with the reason and must be fixed or removed first. Dates that are already a
holiday there are skipped, not renamed, and listed as skipped afterwards.
Every holiday in the list is declared exactly as if you had added it on its
own, so anyone who had booked one of those days gets their days back and is
told. Up to 100 lines at a time.

## Backfill

Days lock at the end of the day they apply to. **Backfill** is the one place a
locked day can be changed: use it to record leave that was already taken, for
example in the first month after going live. Every backfilled day is marked as
entered by an admin on the person's calendar and on the team view, and is
listed here so it can be reviewed or undone.

## Policy

The settings that answer open questions in the product's specification live
here rather than in a document: how unused days carry forward, whether a
weekend between two leave days is consumed, whether the team view shows
reasons, and the currency used for money.

**portal_start_date** is the day the company started logging time in the
portal, for example `2026-10-01`. Days before it are never counted as missing
— not in coverage, the nudges, the Friday gaps or weekly sign-off. Empty means
no start date.

**invoice_payment_terms_days** (30) is how many days a client has to pay an
invoice. An invoice still unpaid that many days after its invoice date shows
as **payment overdue** on the managers' Invoices list. It must be a whole
number of days from 0 to 365; anything else is refused.

Settings are changed through Claude (`update_setting`), not on this page.

## Audit

Every change made through the admin panel, every decision on a request, and
every CTC period added or removed, and every change to a project's revenue, is recorded with who made it
and when. Changes nobody made by hand — a request approved overnight because
its day arrived undecided, comp-off that expired — are recorded as **System**.
