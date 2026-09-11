---
title: For admins
summary: People, roles, cost rates, allowances, holidays, backfilling leave, and policy.
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

### Roles

- **User** — own leave and time.
- **Lead** — also approves leave for their reports.
- **Manager** — also creates projects, allocates people, and sees cost and
  margin. Managers see every project.
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

Every project is costed at the CTC in force on the day each hour was logged,
and every future month is planned at the CTC in force on those days. A
person with no CTC for a date makes every figure for that date report as
**incomplete**, never cheaper. Only managers and admins see CTC; the person
never does, and it is never called salary.

## Allowances

Set the monthly allowance per category. A row with no person is the company
default and applies from its month onwards until another row replaces it.
Nobody can book anything until a default exists.

## Holidays

Declare a holiday with a date and a name. Anyone who had booked that day gets
their days back and is told. Holidays cannot be booked and consume nobody's
allowance.

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

## Audit

Every change made through the admin panel, every decision on a request, and
every change to a cost rate or a project's revenue is recorded with who made it
and when.
