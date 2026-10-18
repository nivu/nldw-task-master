# Documentation

Living documentation. Anything here is expected to describe how the system
works **now** — if a document becomes a record of a past change instead, it
belongs in [`archive/`](archive/).

Specifications live in [`../specs/`](../specs/), not here.

## Architecture

| Document | Covers |
|---|---|
| [`architecture/leave-calendar.md`](architecture/leave-calendar.md) | How the leave calendar works: the request path, where each rule lives, the three things that are easy to get wrong (timezone, derived balances, the append-only audit log), who can see a reason, background work, and how to run it locally |
| [`architecture/crm.md`](architecture/crm.md) | How the sales CRM works: the CRM guard and deny-by-default for CRM-only accounts, the browser's lack of any table grant, the write path and audit trigger, workspace-scoped references, transition functions, redaction, the TVA lifecycle and its readers, capacity, jobs and queues, the MCP tools, and the three things that are easy to get wrong (`crm.actor`, function grants, the counting-allocations reader) |

## Guides

User guides, also served in the portal at `/help`:

| Document | Covers |
|---|---|
| [`guides/01-getting-started.md`](guides/01-getting-started.md) | Signing in, the sidebar, roles |
| [`guides/02-booking-leave.md`](guides/02-booking-leave.md) | Booking leave and work-from-home days |
| [`guides/03-logging-your-day.md`](guides/03-logging-your-day.md) | Logging hours against projects and activities |
| [`guides/04-for-leads.md`](guides/04-for-leads.md) | Team view, approvals, weekly sign-off, running projects for your reports, effort across all projects |
| [`guides/05-for-managers.md`](guides/05-for-managers.md) | Projects, categories, phases including spill-over, revenue, money by month; projects run by a CRM deal and released allocations |
| [`guides/06-for-admins.md`](guides/06-for-admins.md) | People, roles, account types, CTC, holidays, policy, the CRM workspaces panel |
| [`guides/07-faq.md`](guides/07-faq.md) | Common questions |
| [`guides/08-connecting-claude.md`](guides/08-connecting-claude.md) | Using the portal from Claude through MCP, the CRM's `crm_*` tools by role included |
| [`guides/09-crm.md`](guides/09-crm.md) | The sales CRM for sellers: signing in, Today, records, the pipeline and what each stage needs, Drive links, Claude |
| [`guides/10-crm-admin.md`](guides/10-crm-admin.md) | Running a CRM workspace: inviting, roles, lists, probabilities, FX rates, allowed domains, trash, audit, import, mailboxes, automations, targets |
| [`guides/10-tasks.md`](guides/10-tasks.md) | Your tasks: My Tasks and its buckets, quick add and its questions, who sees what, CRM and project tasks, the Life OS import (owner), Claude's `task_*` tools |

## Operations

| Document | Covers |
|---|---|
| [`operations/deployment.md`](operations/deployment.md) | Deploying to Supabase + Railway + Netlify, in order; the two settings that are easy to skip and expensive to skip (`config push` for FR-AUTH-02, the `beat` process for Q-04); bootstrapping the first admin into an empty database; and releasing the sales CRM (migrations 025–044, Gmail sync and its `worker-crm-sync` service, the SMTP precondition, `/health/jobs`) |

## Where things go

This repo follows [spec-kit](https://github.com/github/spec-kit). Four homes,
and the distinction matters:

| Content | Home |
|---|---|
| Project principles and non-negotiables | `.specify/memory/constitution.md` |
| What a feature must do, and why | `specs/NNN-slug/spec.md` |
| How the system works today | `docs/` |
| A record of a change that shipped | the commit message (or `docs/archive/`) |

Do not add point-in-time reports (`*_FIX.md`, `*_SUMMARY.md`, `*_STATUS.md`) to
the repo root or to `backend/`. If a fix changes what the system is supposed to
do, update the spec — the writeup is scaffolding, the spec is the artifact.
