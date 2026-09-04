# Nunnari Employee Portal

An internal leave calendar for Nunnari. Employees mark a leave or work-from-home
day in about fifteen seconds from their phone; their lead is notified where they
already are and approves or rejects; admins run allowances and the holiday
calendar without anyone touching code.

Leave currently arrives by email, too late to plan around. This replaces that.

- **What it must do** — [`specs/001-leave-calendar/spec.md`](specs/001-leave-calendar/spec.md)
- **Why, and the rules it may not break** — [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
- **How it works today** — [`docs/`](docs/)

Built from [nl-sdd-app-starter](https://github.com/nunnarilabs/nl-sdd-app-starter).

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind 4, shadcn/ui |
| Backend | Python 3.11+, FastAPI, Celery, Redis |
| Database | Supabase (PostgreSQL, Auth, Row-Level Security) |
| Deploy | Docker / Railway (backend), Netlify (frontend) |

## What you get

- **Auth wired for the App Router** — browser, server, and anon Supabase
  clients, plus middleware that refreshes tokens and guards routes. The
  token-refresh path correctly clears bad cookies on a failed refresh, which
  is the part that is easy to get subtly wrong.
- **A backend proxy** — the browser never calls FastAPI directly. All calls go
  through `/api/proxy/[...path]`, so `BACKEND_URL` stays a server-side secret.
- **A FastAPI app** with structured JSON logging, CORS, a health check, and an
  explicit router registry.
- **Celery + Redis** for background work, with `docker-compose` for local dev
  and an optional embedded worker for single-process local runs.
- **Row-Level Security done safely** — `SECURITY DEFINER` helper functions that
  avoid the policy recursion that silently denies all rows.
- **Spec-driven development conventions** — `.specify/` templates, a
  filled-in constitution, and `CLAUDE.md`.

## Setup

### 1. Supabase

Local, for development — needs Docker running:

```bash
npx supabase start          # prints the API URL, anon key and service-role key
npx supabase db reset       # applies every migration, then seeds demo users
```

Against a hosted project:

```bash
npx supabase link --project-ref <your-ref>
npx supabase db push
```

Self-registration is disabled (`enable_signup = false` in `supabase/config.toml`)
because FR-AUTH-02 requires accounts to be admin-created. Do not turn it back on.

### 2. Backend

```bash
cd backend
cp .env.example .env       # fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Check it: <http://localhost:8000/health> and <http://localhost:8000/docs>

### 3. Frontend

```bash
cd frontend
cp .env.example .env       # fill in NEXT_PUBLIC_SUPABASE_URL and ANON_KEY
pnpm install
pnpm dev
```

Open <http://localhost:3000>

### 4. Background worker

Needs Redis running (`docker compose up redis` from `backend/`):

```bash
cd backend && uv run celery -A app.worker worker --loglevel=info
```

Or set `RUN_EMBEDDED_WORKER=true` in `backend/.env` to run it inside the API
process — convenient locally, but never do this in production or two worker
pools will consume the same queue.

## Where things live

| Concern | Where |
|---|---|
| Leave rules — cost, lock window, state machine | `backend/app/domain/` (pure functions, no I/O) |
| Balance ledger | `backend/app/domain/ledger.py` (pure arithmetic, no SQL) |
| API routes | `backend/app/api/`, registered in `_mount_routers` in `app/main.py` |
| Notifications | `backend/app/tasks/notifications.py`, adapters in `backend/app/services/notify/` |
| Which routes need a login | `frontend/lib/supabase/middleware.ts` |
| Screens | `frontend/app/(portal)/` |
| Browser tests | `frontend/e2e/` |
| Audit log guarantees | `supabase/migrations/005_audit_triggers.sql` |
| Schema | numbered files in `supabase/migrations/` |
| Policy switches (carry-forward, sandwich rule) | `app_settings` table, seeded in `006_settings_defaults.sql` |

## Conventions

Read `CLAUDE.md` before contributing. Three rules matter most:

1. **Schema changes are migrations.** SQL applied by hand in the Supabase
   editor and never committed causes production/repo drift that stays invisible
   until someone builds a fresh database.
2. **RLS role checks go through the `SECURITY DEFINER` helpers** in
   `supabase/migrations/003_rls_helpers.sql` — `is_admin()`, `is_lead_of()`,
   `current_app_role()`. Inlining a `SELECT ... FROM profiles` subquery into a
   policy on `profiles` recurses, and PostgreSQL reports that as NULL, which RLS
   treats as deny.
3. **A booking is immutable once its date has passed**, checked server-side in
   Asia/Kolkata. Never trust a client clock for it. This is the rule the whole
   product rests on.

## Checks

```bash
cd frontend && pnpm build      # includes strict TypeScript
cd frontend && pnpm lint
cd backend  && uvx ruff check . && uvx ruff format --check .
cd backend  && uv run pytest   # 121 unit tests, no database needed

# End-to-end, in a real browser, on phone and desktop viewports.
# Builds and serves its own app on :3100, so it never tests a stale server.
# Needs Supabase and the backend running — see Setup above.
cd frontend && pnpm test:e2e
```
