# Documentation

Living documentation. Anything here is expected to describe how the system
works **now** — if a document becomes a record of a past change instead, it
belongs in [`archive/`](archive/).

Specifications live in [`../specs/`](../specs/), not here.

## Architecture

| Document | Covers |
|---|---|
| [`architecture/leave-calendar.md`](architecture/leave-calendar.md) | How the leave calendar works: the request path, where each rule lives, the three things that are easy to get wrong (timezone, derived balances, the append-only audit log), who can see a reason, background work, and how to run it locally |

## Guides

User guides, also served in the portal at `/help`:

| Document | Covers |
|---|---|
| [`guides/01-getting-started.md`](guides/01-getting-started.md) | Signing in, the sidebar, roles |
| [`guides/02-booking-leave.md`](guides/02-booking-leave.md) | Booking leave and work-from-home days |
| [`guides/03-logging-your-day.md`](guides/03-logging-your-day.md) | Logging hours against projects and activities |
| [`guides/04-for-leads.md`](guides/04-for-leads.md) | Team view, approvals, running projects for your reports |
| [`guides/05-for-managers.md`](guides/05-for-managers.md) | Projects, categories, phases including spill-over, revenue, money by month |
| [`guides/06-for-admins.md`](guides/06-for-admins.md) | People, roles, CTC, holidays, policy |
| [`guides/07-faq.md`](guides/07-faq.md) | Common questions |
| [`guides/08-connecting-claude.md`](guides/08-connecting-claude.md) | Using the portal from Claude through MCP |

## Operations

| Document | Covers |
|---|---|
| [`operations/deployment.md`](operations/deployment.md) | Deploying to Supabase + Railway + Netlify, in order; the two settings that are easy to skip and expensive to skip (`config push` for FR-AUTH-02, the `beat` process for Q-04); and bootstrapping the first admin into an empty database |

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
