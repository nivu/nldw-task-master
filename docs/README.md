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

| Document | Covers |
|---|---|
| _(none yet)_ | |

## Operations

| Document | Covers |
|---|---|
| _(none yet)_ | |

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
