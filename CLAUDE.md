# nldw-task-master Development Guidelines

## Active Technologies

- Python 3.11+ (backend): FastAPI, Celery, Redis
- TypeScript 5.x (frontend): Next.js (App Router), @supabase/ssr, shadcn/ui
- Database: Supabase PostgreSQL + Supabase Storage + Supabase Auth

## Project Structure

```text
frontend/          # Next.js (TypeScript, App Router)
├── app/
│   ├── api/proxy/ # Catch-all proxy to the backend — the ONLY way the
│   │              # browser reaches FastAPI
│   └── auth/
├── components/
│   ├── ui/        # shadcn/ui primitives
│   └── shared/
└── lib/
    ├── supabase/  # browser / server / anon clients + middleware guard
    └── api/       # backendFetch

backend/           # Python (FastAPI)
├── app/           # Deployed application code only
│   ├── api/       # Route handlers
│   ├── services/  # Business logic
│   ├── tasks/     # Celery tasks
│   └── worker.py
├── scripts/       # One-off scripts — NOT deployed
│   ├── ops/       # Worker/server process management
│   ├── data/      # One-off data operations
│   └── simulations/
├── tests/         # pytest suite
├── start_api.py   # Dockerfile CMD + Procfile web — must stay at root
└── pyproject.toml

docs/              # Living documentation only
├── architecture/
├── guides/
├── operations/
└── archive/       # Point-in-time reports — never current, don't add here

specs/             # spec-kit features — what the system MUST do

supabase/
└── migrations/
```

## Documentation Rules

Four homes, and the distinction is load-bearing:

| Content | Home |
|---|---|
| Principles and non-negotiables | `.specify/memory/constitution.md` |
| What a feature must do, and why | `specs/NNN-slug/spec.md` |
| How the system works today | `docs/` |
| A record of a change that shipped | the commit message (or `docs/archive/`) |

- Do NOT create `SOMETHING_FIX.md` / `*_SUMMARY.md` / `*_STATUS.md` files at
  the repo root or in `backend/`. This is how stale reports accumulate.
- **If a fix changes what the system is supposed to do, update the spec.** The
  writeup is scaffolding; the spec is the artifact.
- New feature: `.specify/scripts/bash/create-new-feature.sh "desc" --short-name "slug"`
- Backend one-off scripts go in `backend/scripts/{ops,data,simulations}/`, never
  at `backend/` root — that root is reserved for deploy-referenced entrypoints.
- Schema changes MUST land as a numbered file in `supabase/migrations/`. SQL
  applied by hand in the Supabase editor and never committed causes
  production/repo drift that stays invisible until a fresh database is built.

## Commands

```bash
# Frontend
cd frontend && pnpm dev          # Dev server on :3000
cd frontend && pnpm build        # Production build

# Backend
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd backend && uv run celery -A app.worker worker --loglevel=info

# Testing
cd backend && uv run pytest

# Database
npx supabase db push
```

## Code Style

- Python: ruff for linting/formatting, pydantic for validation
- TypeScript: ESLint + Prettier, strict mode
- All API responses: JSON, errors use RFC 7807
- Structured JSON logging in Python backend

## Constitution

See `.specify/memory/constitution.md` for full principles. Key rules:

- Security-First: RLS on all tables, validate all input, no secrets in code
- Simplicity (YAGNI): Build only what's needed
- Clear Boundaries: Frontend = UI only, Backend = logic, Supabase = data
- Observability: Structured logging

## Communication Style

When explaining anything — a bug, a fix, a decision, a concept — always provide
two layers:

1. **Technical explanation**: the precise details (what code changed, why, what
   the exact behavior is).
2. **Plain English explanation**: a simplified version immediately after,
   written as if explaining to someone who doesn't code. No jargon, no acronyms,
   just what it means in practice.

Keep both short. The plain English version should make the technical one
optional to read.

## Working Agreement

This section governs every task, every session, for the entire lifetime of this
project — not just today.

- Before starting any task or step, explain in plain, simple English what you're
  about to do and why. Wait for explicit approval before doing anything.
- Do the task — exactly as approved, nothing more.
- Before moving to the next task or step, explain in plain, simple English what
  you just did and what the actual result was (show real command output or file
  contents — not a paraphrase of what "should" have happened). Then stop and ask
  whether to proceed. Do not continue without approval.
- Never batch steps together. One step at a time: explain → approve → do →
  explain result → approve → next step.
- If something fails or looks wrong, stop immediately, explain the problem in
  plain English, and propose a fix. Do not silently retry, skip, or work around
  it without approval.
- "Plain English" means no jargon-dumping and no assuming prior context is
  remembered.
- Do exactly what is specified — nothing more, nothing extra. Do not add fields,
  files, libraries, error handling, comments, refactors, or any "best practice"
  improvement that wasn't explicitly requested, no matter how small or
  reasonable it seems. If something appears missing or worth improving, do not
  add it on your own judgment — stop, explain what you noticed, and ask whether
  it should be included.
- This working agreement overrides any instinct to move quickly or be efficient.
  Slower and fully confirmed at every step is the goal.
- Do not let speed substitute for verification. When something didn't work, the
  tempting move is the smallest possible tweak (a different import style, an env
  flag, a longer wait) and an immediate re-run — rather than pausing to ask "do
  I actually know the full state of the system right now, or am I guessing?"
  That habit is how a stale, half-finished background job sits unnoticed in a
  queue and quietly contaminates the next test run: the second run looks like a
  clean pass, but is actually failing one leftover job and trivially passing an
  unrelated new one. When something fails, report the problem and ask for
  consent before proceeding — do not guess-and-retry.
