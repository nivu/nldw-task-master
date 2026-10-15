# Sales CRM — Quickstart

| | |
|---|---|
| **Feature** | `007-crm` |
| **For** | the engineer building a slice of [`tasks.md`](tasks.md), and whoever checks it |

How to run the portal with the CRM locally, and how to see each part working.
Production deployment stays in `docs/operations/deployment.md`; S12 adds the
CRM steps there.

> **Never commit `supabase/config.toml`.** The local password sign-in script
> edits it (D12). Before every commit: `git diff --cached --name-only | grep -c
> config.toml` must print `0`.

---

## 1. Run it

```bash
# Database: local Supabase, all migrations, seed (fake people, local only)
npx supabase start
npx supabase db reset                 # applies 001…043 and supabase/seed.sql

# Optional, for e2e and for signing in as seeded people without Google
backend/scripts/ops/local_password_auth.sh on && npx supabase stop && npx supabase start

# Backend
cd backend && uv sync
uv run uvicorn app.main:app --reload --port 8000
uv run celery -A app.worker worker --loglevel=info            # default queue
uv run celery -A app.worker beat --loglevel=info              # 1b jobs and the trash purge
uv run celery -A app.worker worker -Q crm_sync --concurrency=1 --loglevel=info   # S10, only with sync on

# Frontend
cd frontend && pnpm install && pnpm dev                       # http://localhost:3000
```

`backend/.env` needs `SUPABASE_URL=http://127.0.0.1:54321` and
`SUPABASE_SERVICE_ROLE_KEY` from `npx supabase status`. `frontend/.env` needs
`BACKEND_URL=http://localhost:8000`, the anon key, and
`NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=true` for seeded logins.

Seeded accounts after S1 (password `portal123`, local only):

| Email | Account | CRM |
|---|---|---|
| `vinita@nunnari.example` | staff admin (TVA) | none (TVA admin panel only) |
| `sriram.nl@gmail.com` | staff manager | Admin, both workspaces |
| `devansh.nl@gmail.com` | staff lead | Viewer, Labs |
| `deepika.nl@gmail.com` | staff user | Member, Labs |
| `tamil.crm@deepweaver.example` | `crm_only` | Member, Labs |
| `ganesh.crm@academy.example` | `crm_only` | Member, Academy |

(`supabase db reset` applies the migrations before `seed.sql`, so migration
026's owner seeding finds no profiles locally. `seed.sql` itself adds the two
`crm_only` profiles (auth users and identities included, as it does for the
others) and every membership above, inside a `DO` block that sets the system
actor first. No seeded person has the owner's email, so `seed.sql` marks
Sriram as the local owner; his two Admin memberships are the owner's.
`db push` never runs `seed.sql`, so none of this reaches production.)

## 2. Get a token for curl

```bash
# Sign in at http://localhost:3000, open Account → Personal tokens → New, copy nunp_…
export T=nunp_xxx
api() { curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' "http://localhost:8000/api/v1$@"; }
```

CRM-only people issue tokens from `/crm/account`.

## 3. Checks by slice

Each check is also an automated test named in `tasks.md`. These are the
by-hand versions.

### S1 — Foundation

```bash
api /me                                   # as tamil: only id, display_name, email, account_type, crm, preferences
api /me/home                              # as tamil: 403 "This account is for the CRM only."
api /crm/workspaces                       # as tamil: [labs, member]
api "/crm/members?workspace=academy"      # as tamil: 404 "No such workspace."
api "/crm/invitations?workspace=academy" -X POST -d '{"email":"rep@gmail.com","display_name":"Rep","role":"member"}'
                                          # as an Academy admin: 403 naming the allowed domains
api /analytics/people                     # as sriram: no crm_only names
```

In the browser: tamil signs in and lands on `/crm/labs`; typing `/calendar`
redirects to `/crm`. Deepika sees a **CRM** link in the TVA sidebar, and the
CRM shows **Back to TVA**. Vinita sees Admin → **CRM workspaces** and can
invite into either workspace.

Audit: `select actor_label, channel, workspace_id, action from audit_log order
by id desc limit 5;` shows `membership.invited` with the workspace.

### S2 — TVA readers and capacity

Allocate two people to a tentative project, then mark one allocation released
in SQL (`update allocations set released_at = now(), release_reason = 'test'
where id = '…';`; `allocations` is a TVA table, so no CRM actor is needed).
Forecast, bench, resources, hiring, P&L planned cost and the CEO dashboard no longer count it. Archive the project:
it leaves the P&L pipeline block. `api "/crm/capacity?workspace=labs"` as a
Labs admin returns 13 weeks; as devansh (Viewer): 403.

### S3 — Companies and people

```bash
api "/crm/companies?workspace=labs" -X POST -d '{"name":"Acme","domain":"acme.com","owner_id":"<id>"}'
api "/crm/companies?workspace=labs" -X POST -d '{"name":"Acme Two","domain":"acme.com","owner_id":"<id>"}'   # 409 naming Acme
api "/crm/companies/duplicates?workspace=labs" -X POST -d '{"name":"Acme Corp"}'                            # similar: Acme
```

Delete Acme as Deepika (owner): it is in Trash; restore as an admin. The
timeline shows created, deleted and restored with actor and channel.

### S4 — Deals, tasks, notes, pipeline

Create a deal on a `new` company: 422. Set the company to Qualified, create
the deal: the company goes to Deal open. Move to Proposal without an amount:
"Proposal needs amount, currency and expected close date." Lose it without a
reason: 422. Lose it with one: its open tasks close with a note.

### S5 — TVA lifecycle (Labs)

Move a Labs deal to Proposal: Projects shows a tentative project with the
deal's probability. As deepika (TVA `user`), Closed Won with the handoff: the
project is confirmed with lead and phases and no revenue, and each Labs admin
who is a TVA manager has "Confirm revenue and milestones". Repeat as sriram:
revenue is in INR and milestones exist. Lose a deal with two allocations:
forecast, bench and resources drop them; reopen: they return.

### S6 — Views

`/crm/labs/opportunities`: switch to Kanban, drag a card from Discovery to
Qualification without a point of contact. The stage form opens; cancelling
puts the card back. As devansh, add a filter on Amount: "Viewers cannot
filter, sort or total by amount."

### S7 — Today, search, merge

Cmd/Ctrl+K anywhere under `/crm` → type "acm" → Acme. Today shows overdue next
steps and today's tasks; sections 1 and 4 say "Email sync is off".

### S8 — Importer and proxy

```bash
api "/crm/imports/dry-run?workspace=labs" -X POST -d @backend/tests/fixtures/twenty/labs.json
```

The counts match the fixture's. Apply twice: the second apply reports `0`
changes. In the browser, Analytics → Statement → CSV downloads a real CSV
(006 FR-STMT-02 fixed). A 4.1 MB upload is refused with a 413.

### S9 — Flags, jobs, bell, digests

```bash
cd backend && uv run python -c "from app.tasks.crm import recompute_flags; print(recompute_flags())"
uv run python -c "from app.tasks.crm import digest_tick; print(digest_tick())"     # sends nobody outside 09:00 local
```

Admin → Settings → Jobs shows the runs. Force three failures (unset
`SUPABASE_URL` for one run of the task): the third alerts the admins once.
`curl localhost:8000/health/jobs`.

### S10 — Gmail sync (only with a test service account)

Set `GOOGLE_SERVICE_ACCOUNT_JSON` for the `crm_sync` worker only and
`CRM_GMAIL_CONFIGURED=true` for the API, beat and default worker, add a mailbox
(your own address) and a non-system label in Settings, switch sync on, run `POST /crm/email-sync/run`. Run it again: `stored: 0`.

### S11 — Campaigns, dashboard, CSV

Create a campaign, add members from the Companies view, convert one: the new
deal shows the campaign as its source. Dashboard panels load for an admin; a
Viewer gets 403. Export a view to CSV; import that CSV back in update mode with
no changes.

## 4. Automated checks (every slice's "done when")

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
CRM_DB_TESTS=1 uv run pytest -m db -q        # needs local Supabase with migrations applied
cd frontend && pnpm build && pnpm lint
pnpm test:e2e e2e/crm.spec.ts                # local, password login on
```

## 5. Performance check (S12)

```bash
cd backend && uv run python scripts/simulations/crm_load_10k.py --workspace labs --rows 10000
# seeds 10k companies, 10k people, 10k deals, then times through the API:
#   list (saved view), kanban (pipeline), search "acm", record page, Today
# prints p50/p95 per call; every p95 must be under 1000 ms (NFR-CRM-01)
uv run python scripts/simulations/crm_load_10k.py --cleanup
```

Write the p95 figures in the S12 commit message (not in a report file).
