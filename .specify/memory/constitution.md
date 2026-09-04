<!--
Sync Impact Report
===================
Version change: 1.0.0 -> 1.1.0 (MINOR - product defined; RLS helpers renamed for the
  single-company role model; integrity + audit rules added)
Modified principles: I. Security-First (stakes named, RLS helper names corrected
  for the single-company role model, append-only audit rule added); III. Data
  Integrity (booking immutability and derived-balance rules added)
Added sections: What This Product Is and The Journeys are now written (were TODO)
Removed sections: None
Templates requiring updates:
  - plan-template.md: OK
  - spec-template.md: OK
  - tasks-template.md: OK
Follow-up TODOs: none outstanding.
-->

# nldw-task-master Constitution

---

## What This Product Is

The Nunnari Employee Portal is an internal tool for the people who work at
Nunnari. Version 1 does one thing: it replaces leave-by-email with a calendar.

Today, someone who wakes up unwell mails their lead at 08:00. The lead reads it
at 12:30, by which point the day has already been planned around that person
being present. Work from home is worse — nothing announces it at all, and a lead
discovers it by walking into the office and not finding the person. The
information exists; it just arrives after it could have been useful.

The portal serves three groups. **Employees** — including the interns and junior
engineers for whom the existing ClickUp workflow has proved too heavy — mark a
leave or a work-from-home day in ten to fifteen seconds, from a phone, often
from bed. **Leads** open one screen and see who is available today. **Vinita**,
as admin, administers allowances and the holiday calendar without anyone editing
code.

The bar this must clear is not feature parity with an HR suite. It is *lower
friction than sending an email*. A flow slower than that will not be adopted,
and an unadopted portal solves nothing.

This document is the highest-authority governing document for this project.
All implementation decisions, architectural choices, and tradeoffs MUST comply
with the principles described here.

---

## The Journeys

### The Employee's Journey

Deepika has a dentist appointment on the 28th and will be unreachable all
morning. She opens the portal on her phone, taps the 28th, chooses "Casual
leave" and "Half day", types "Dentist appointment", and confirms. Three
interactions. Her remaining casual-leave balance drops by half a day in front of
her before she confirms, so she never has to do the arithmetic herself. Her lead
is notified immediately.

Tarun wakes with a fever on a Friday. He marks today as sick leave, full day.
The system accepts it despite being same-day, because sick leave is by nature
unplanned and the rules for it are not the rules for a planned holiday.

Devansh marked the 1st as work from home and then went to the office anyway. At
21:00 that evening he removes the booking and the day returns to his allowance.
On the 2nd, the same attempt is refused — the day is closed. That boundary is
what makes the record trustworthy.

### The Lead's Journey

A lead opens the team view and sees today without navigating or filtering: who
is on casual leave, who is sick, who is working from home, who is present, and
which of those absences are agreed versus merely requested. When one of their
reports files a request, it reaches them where they already are — Slack, with
email as the fallback — carrying enough detail to decide without opening the
portal. They approve, or they reject with a note explaining why.

A lead is also an employee. They book their own leave the same way anyone does.

### The Admin's Journey

Vinita declares 15 August as Independence Day, once, and it applies to everyone
and consumes nobody's allowance. She sets the month's work-from-home allowance
and every employee's balance reflects it. She creates accounts — nobody signs
themselves up — assigns each person to a lead, and deactivates people who leave
without destroying the record of what they took.

---

## Core Principles

### I. Security-First (NON-NEGOTIABLE)

This product stores the stated reason for every absence. A sick-leave reason
is health information about a named colleague, written in their own words. A
leak is not an abstract compliance event — it is one person's medical situation
becoming office conversation. Reasons are readable by exactly three parties: the
person who wrote it, their assigned lead, and an admin. Nobody else, ever, and
that includes aggregate or export views.

- All user input MUST be validated and sanitized at system boundaries
- Every API route MUST enforce authentication; public endpoints are
  intentionally unauthenticated but MUST be rate-limited and validated
- Secrets MUST NOT appear in version control under any circumstances — use
  environment variables in all environments
- Supabase Row Level Security (RLS) MUST be enabled on every table; any new
  table MUST have its RLS policy defined in the same migration that creates it
- Role and reporting-line checks in RLS policies MUST go through the
  `SECURITY DEFINER` helper functions in `supabase/migrations/003_rls_helpers.sql`
  — `public.is_admin()`, `public.is_lead()`, `public.is_lead_of(uuid)`,
  `public.current_role()`. Inlining a `SELECT ... FROM profiles` subquery into a
  policy ON `profiles` causes silent recursion that PostgreSQL reports as NULL,
  which RLS treats as deny — it presents as login breaking and rows vanishing,
  and is very hard to diagnose
- The audit log is append-only at the database level, not merely by convention:
  `UPDATE` and `DELETE` are revoked and a trigger rejects them. Every booking
  state transition and administrative action MUST be written to it
- The Next.js server-side catch-all proxy (`/api/proxy/[...path]`) is the
  ONLY path through which the browser may reach the FastAPI backend — the
  backend URL (`BACKEND_URL`) is a server-side secret and MUST NOT be embedded
  in any client-side bundle or exposed via a `NEXT_PUBLIC_` variable
- OWASP Top 10 vulnerabilities MUST be actively prevented; SQL injection is
  mitigated by the Supabase client's parameterised query builder; XSS by
  React's default escaping
- Storage buckets are private by default; only service-role reads are
  permitted from the backend, and the browser receives signed URLs, never
  direct storage paths belonging to another user

### II. Simplicity (YAGNI)

The journeys described above are the product. Anything that does not serve one
of them is not the product.

- Every feature MUST trace back to a concrete need in one of the journeys
- No speculative abstractions — do not design for a hypothetical user who has
  not asked for anything
- Prefer standard framework capabilities over custom solutions
- Three similar lines of code are preferable to a premature abstraction; do
  not create a helper until you have three genuine callers
- If a new dependency can be replaced with fewer than 50 lines of clear code,
  replace it; each dependency is surface area for vulnerabilities and breaking
  changes

### III. Data Integrity

- All schema changes MUST be expressed as numbered migration files in
  `supabase/migrations/`; never make ad-hoc schema changes in the Supabase
  dashboard. Hand-applied SQL that is not committed causes production/repo
  drift, and the drift is invisible until a fresh database is built
- Migrations MUST be written to be reversible where possible; if a migration
  cannot be reversed, this MUST be explicitly noted in the file with rationale
- Existing migrations are immutable once applied
- A booking becomes immutable once its own date has passed, evaluated
  server-side in Asia/Kolkata. This is the integrity rule the whole product
  rests on: without it, a person can take a work-from-home day and then delete
  the record to reclaim the allowance. Client clocks MUST NOT be trusted for
  this check
- Balances MUST be derived from the booking ledger, never stored as a mutable
  counter. A counter drifts silently; a ledger can be recomputed and audited
- All write operations that touch multiple tables MUST be transactional where
  the database supports it
- API responses MUST NOT expose internal UUIDs of records the requesting user
  does not own, stack traces, or backend error messages verbatim

### IV. Clear Boundaries

Three layers. Three responsibilities. They do not blur.

**The Next.js Frontend** is a rendering and interaction layer. It reads from
Supabase directly (under RLS) and calls the FastAPI backend via `backendFetch`
for anything involving heavy processing or business logic. It contains no
business rules. It has no knowledge of the backend's physical address — all
backend calls go to `/api/proxy/...` and the Next.js server resolves the real
`BACKEND_URL`.

**The FastAPI Backend** owns the logic. It uses the Supabase service-role
client, which bypasses RLS, because it is trusted server-side infrastructure —
which means it MUST enforce its own authorization before touching the database.

**Supabase** is the single source of truth. PostgreSQL holds all persistent
state, Auth all identity state, Storage all files. No other database or file
store is permitted.

Each layer MUST be independently deployable. A backend deployment MUST NOT
require a simultaneous frontend deployment, or vice versa. API contracts
between layers MUST be defined in code (Pydantic models on the backend,
TypeScript interfaces on the frontend) before implementation begins.

### V. Observability

- Every API endpoint MUST log: method, path, HTTP status, duration, and the
  authenticated user's ID (never their personal data)
- Every background task MUST log its inputs, the decision it took, and the
  latency of any external call it made
- Structured JSON logging MUST be used throughout the Python backend via a
  single named logger; log lines MUST be parseable by standard aggregation
  tools
- All errors returned from the backend to clients MUST follow RFC 7807 Problem
  Details format: `type`, `title`, `status`, `detail`; stack traces MUST NOT
  appear in API responses

---

## Technology Constraints

The following stack is locked in. Do not introduce alternatives without
documented justification and explicit agreement.

### Frontend
- Next.js (App Router) with TypeScript in strict mode
- Tailwind CSS + shadcn/ui for styling and primitives
- `@supabase/ssr` for auth; session refresh happens in middleware
- Server Components by default; `"use client"` only where interaction requires it

### Backend
- Python 3.11+ with FastAPI
- Celery + Redis for background work
- Pydantic for all validation; `pydantic-settings` for configuration
- ruff for linting and formatting

### Infrastructure
- Supabase for PostgreSQL, Auth, and Storage
- Docker for the backend; Netlify for the frontend

---

## Development Workflow

Features are developed following the specify → plan → tasks → implement cycle.

- All schema changes are new, numbered migration files; existing migrations
  are immutable once applied
- Frontend MUST pass `pnpm build` (which includes strict TypeScript
  type-checking) before a PR is ready for review
- Backend MUST pass `ruff check` and `ruff format --check` before a PR is ready
- Tests MUST pass where they exist
- Every PR MUST describe what changed, why, and which journey it affects
- Code review is required before merging to main
- No direct pushes to main; no force pushes to main
- Commits MUST be atomic and descriptive; one logical change per commit

---

## Governance

This constitution is the highest-authority document for this project. All
plans, PRs, and architectural decisions MUST be evaluated against it.

**Amendments** require documented rationale and explicit agreement. Version
following semver:
- **MAJOR**: Removal or redefinition of a Core Principle
- **MINOR**: New principle, new section, or material expansion
- **PATCH**: Clarifications, wording fixes, and factual updates

**Conflicts**: When a proposed feature conflicts with a principle here, the
principle wins. If a genuine exception is required, it MUST be documented in
the relevant plan's Complexity Tracking section with a written justification
that has been explicitly reviewed.

**Compliance**: Every plan produced for a feature in this codebase MUST
include a constitution check:
1. Does this serve one of the journeys?
2. Does it comply with Security-First and Clear Boundaries?
3. Does it preserve Data Integrity — no schema changes outside migrations, no
   cross-tenant data exposure?

---

**Version**: 1.1.0 | **Ratified**: 2026-09-04 | **Last Amended**: 2026-09-04
