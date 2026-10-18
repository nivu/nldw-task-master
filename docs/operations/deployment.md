# Deploying the portal

NFR-08: deployment MUST be reproducible from the repository. This file is that
requirement's implementation — if you find yourself doing something here that
is not written down, write it down.

**Currently deployed:**

| | |
|---|---|
| Frontend | <https://tva.nunnarilabs.com> (custom domain, below; <https://nunnari-employee-portal.netlify.app> still works) |
| Backend | <https://api-production-9edd.up.railway.app> |
| Supabase | project `worjtvnpizpyfimwotkl`, region `ap-south-1` (Mumbai) |
| Railway | project `nunnari-employee-portal` — `api`, `worker`, `beat`, `Redis`; `worker-crm-sync` is created only when the CRM's Gmail sync is switched on (section 6) |

Three pieces, deployed in this order because each needs the previous one's URL:

```
  Supabase (hosted)   →   Railway (api + worker + beat + redis)   →   Netlify (frontend)
       schema                    BACKEND_URL comes from here            FRONTEND_URL
                                                                        goes back to Railway
```

The last step loops back: the backend needs the frontend's address for CORS
and for links in notifications, which does not exist until the frontend is
deployed. Expect to set `FRONTEND_URL` on Railway *after* Netlify is up.

---

## 1. Supabase

```bash
npx supabase login                       # personal access token, one time
npx supabase projects create nunnari-portal --region ap-south-1   # Mumbai
npx supabase link --project-ref <ref>
npx supabase db push                     # migrations only
npx supabase config push                 # auth settings — see the warning below
```

**`db push` applies migrations and NOT `supabase/seed.sql`.** That is the
correct default and must stay that way: the seed file creates five fictional
people whose password is `portal123`. Never pass `--include-seed` to a hosted
project.

> **`config push` is not optional.** FR-AUTH-02 forbids self-registration, and
> that is enforced by `enable_signup = false` in `supabase/config.toml`. A
> hosted project defaults to signups **enabled**. Skip this step and anybody
> who finds the URL can create themselves an account. Verify afterwards:
>
> ```bash
> curl -s -X POST "https://<ref>.supabase.co/auth/v1/signup" \
>   -H "apikey: <anon-key>" -H "Content-Type: application/json" \
>   -d '{"email":"probe@example.com","password":"probe-probe-probe"}'
> # must return: {"code":422,...,"error_code":"signup_disabled",...}
> ```

Then set `site_url` in `supabase/config.toml` to the Netlify address once it
exists, and push the config again. Supabase uses it as the redirect allow-list.

Collect three values from **Project Settings → API**:

| Value | Goes to |
|---|---|
| Project URL | Railway `SUPABASE_URL`, Netlify `NEXT_PUBLIC_SUPABASE_URL` |
| `anon` key | Netlify `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| `service_role` key | Railway `SUPABASE_SERVICE_ROLE_KEY` — **never** Netlify |

The service-role key bypasses Row-Level Security entirely. It belongs only to
the backend. If it ever reaches a `NEXT_PUBLIC_` variable it is in the browser
bundle and every row in the database is readable by anyone.

The backend's Supabase client (`app/services/supabase.py`) talks HTTP/1.1 on
purpose. The library default is one shared HTTP/2 connection, and routes run
in a thread pool: two threads on that one socket failed with
`ReadError: [Errno 11]` and took every in-flight request down with them. Reads
(GET) that still drop are retried once; writes never are.

## 2. Railway

Four services in one project. The three application services are built from
`backend/Dockerfile`; Redis comes from Railway's template.

All three application services run the **same image** and differ only by
`PROCESS_TYPE` (see `backend/entrypoint.sh`). Nothing needs a start command
typed into the dashboard, so a deployment is fully described by the variables
below — and a typo in `PROCESS_TYPE` fails the container loudly rather than
quietly starting a second API where a scheduler was meant to be.

| Service | `PROCESS_TYPE` | Why |
|---|---|---|
| `api` | `api` (or unset) | FastAPI. Reads `$PORT`, which Railway assigns |
| `worker` | `worker` | Notifications (FR-NOTIF) |
| `beat` | `beat` | **The Q-04 nightly sweep.** Without it, pending bookings whose date has passed stay pending forever |
| `redis` | — | Railway's Redis template. The broker |
| `worker-crm-sync` | `worker_crm_sync` | The CRM's Gmail sync pages only (queue `crm_sync`, one at a time). **Not created until sync is switched on** — section 6 |
| `worker-crm-ai` | `worker_crm_ai` | The CRM's AI jobs only (queue `crm-ai`, one at a time); the only service holding the Claude and Fireflies keys — section 8 |

Deploy each from `backend/`, so the build context contains the Dockerfile's
`COPY` sources:

```bash
cd backend
railway up . --path-as-root --service api
railway up . --path-as-root --service worker
railway up . --path-as-root --service beat
```

**`--path-as-root` is not optional.** `railway up` uploads from the *git root*,
not the working directory, so without it Railway receives the whole monorepo,
fails to recognise it, and reports `railpack prepare exited with an error` —
which reads like a build problem rather than a wrong-directory one.

Environment, on `api`, `worker` and `beat` alike — a worker missing
`SUPABASE_URL` fails silently on its first task rather than at boot:

```
PROCESS_TYPE=api | worker | beat       # differs per service; everything else matches
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
REDIS_URL=${{Redis.REDIS_URL}}         # Railway reference variable
FRONTEND_URL=https://<netlify-domain>  # set after step 3
RUN_EMBEDDED_WORKER=false              # must stay false — see below
API_DOCS=false                         # the default; true would publish /docs, /redoc, /openapi.json
```

`RUN_EMBEDDED_WORKER` must be false in production. True runs a Celery worker
inside the API process, so two pools consume the same queue and the API
container carries a worker's memory alongside uvicorn.

`API_DOCS` defaults to false, so production hides its interactive docs and
full OpenAPI route map (`001` NFR-09) without anyone setting it. Never set it
to true on `api`; `backend/.env.example` turns it on for local work. Verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<railway-domain>/docs           # 404
curl -s -o /dev/null -w '%{http_code}\n' https://<railway-domain>/openapi.json   # 404
```

Notification credentials, when they exist, go on `worker` and `beat` (which
send) and on `api` (which verifies Slack's request signature):

```
SLACK_BOT_TOKEN=          # scopes: chat:write, users:read, users:read.email
SLACK_SIGNING_SECRET=     # required for /api/v1/slack/interactions
SMTP_HOST= SMTP_PORT= SMTP_USER= SMTP_PASSWORD= SMTP_FROM=
```

Slack's interactivity request URL is `https://<railway-domain>/api/v1/slack/interactions`.

## 3. Netlify

If `pnpm build` fails inside `netlify deploy --build` with
`Cannot find matching keyid` or `ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING`, that
is corepack, not this code: an older bundled corepack cannot verify current
pnpm releases. `package.json` pins `packageManager` to stop it resolving
"latest", which is what fails; if it still happens, `npm i -g corepack@latest`.

```bash
cd frontend
netlify init          # or `netlify link` for an existing site
netlify env:set NEXT_PUBLIC_SUPABASE_URL      "https://<ref>.supabase.co"
netlify env:set NEXT_PUBLIC_SUPABASE_ANON_KEY "<anon-key>"
netlify env:set BACKEND_URL                   "https://<railway-domain>"
netlify deploy --build --prod --skip-functions-cache
```

> **Always pass `--skip-functions-cache`.** Without it the CLI may print
> "Deploying functions from cache" and reuse the previous Next.js server
> function — the thing that holds middleware, every page and the help guides —
> while uploading only static assets. The deploy then reports "live" and
> production quietly keeps the old code (seen 17 September 2026: a new route
> 404'd and a help page stayed stale). After deploying, request a route only
> the new build knows; anonymous, it must 307 to sign-in, not 404.

> **`netlify deploy --build` builds LOCALLY, using `frontend/.env`.** It is not
> a CI build. Any development-only value in that file ships straight into the
> production bundle — this was caught with
> `NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=true`, which put a password form on the
> production sign-in page. Every variable that must differ has to be set in
> Netlify **and** overridden on the build command:
>
> ```bash
> NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=false netlify deploy --build --prod --skip-functions-cache
> ```
>
> A shell variable wins over `.env`; the Netlify value alone does not, because
> the build never runs on Netlify. Verify afterwards:
>
> ```bash
> curl -s https://<netlify-domain>/auth/login | grep -c 'id="password"'   # must be 0
> ```

`BACKEND_URL` has no `NEXT_PUBLIC_` prefix, deliberately. The browser reaches
FastAPI only through `/api/proxy/[...path]`, and the constitution requires the
backend's address stay out of every client bundle. After deploying, confirm it:

```bash
curl -s https://<netlify-domain>/_next/static/chunks/*.js | grep -c '<railway-domain>'
# must be 0
```

The security headers (`X-Frame-Options: DENY`, `Content-Security-Policy:
frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`; `001` NFR-09) are
set in `next.config.ts`, not `netlify.toml`: Netlify's `[[headers]]` reach only
files its CDN serves, not pages the Next.js server function renders. Confirm
them on a rendered page:

```bash
curl -sI https://<netlify-domain>/auth/login | grep -i -E 'x-frame-options|frame-ancestors'
# both must appear
```

Then go back to Railway and set `FRONTEND_URL` to the Netlify address (in
production, the custom domain below), and to Supabase to set `site_url`.

## Auth configuration: one file, two environments

`config.toml` is pushed wholesale by `supabase config push`, and **`env()`
substitution works only on string fields** — a boolean like
`[auth.email] enable_signup` rejects it:

```
Invalid TOML document: invalid value
  enable_signup = env(SUPABASE_AUTH_EMAIL_ENABLED)
                  ^
```

So there is no way to express "password login enabled locally, disabled in
production" in a single config file. Whoever completes FR-AUTH-08 has to pick
one of these deliberately:

* **Production-correct config.** `config.toml` disables the email provider.
  Local development and the 32 browser tests then cannot sign in with a
  password and need another route to a session.
* **Local-correct config plus a production override script.** Simple locally,
  but leaves a trap: any future `supabase config push` silently re-enables
  password sign-in in production. If this route is taken, the override MUST run
  immediately after every push, from the same script.

The trap in the second option is the reason to prefer the first.

## 4. The first admin

A fresh deployment has **no users at all**, and no way to make one: FR-AUTH-02
forbids self-registration and FR-AUTH-03 requires an existing admin. Break the
deadlock exactly once:

```bash
cd backend
SUPABASE_URL=https://<ref>.supabase.co \
SUPABASE_SERVICE_ROLE_KEY=<service-role-key> \
uv run python scripts/data/bootstrap_admin.py --email vinita@nunnari.com --name "Vinita"
```

It refuses if an active admin already exists, so it cannot quietly create a
second privileged account. Everything after this goes through the admin panel,
which is audited.

Then, signed in as that account:

1. **Allowances** — set the real monthly figures (spec Q-01). Until an
   allowance exists nobody can book anything, because every balance is zero.
2. **People** — create everyone and assign their leads.
3. **Holidays** — declare the year.
4. **Backfill** — record leave already taken this month (spec A-21).

## 5. Checks after going live

```bash
curl -s https://<railway-domain>/health                       # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' https://<netlify-domain>/calendar   # 307 to /auth/login
```

- Sign-up is refused (the curl in step 1).
- Password sign-in is refused — FR-AUTH-08:

  ```bash
  curl -s -X POST "https://<ref>.supabase.co/auth/v1/token?grant_type=password" \
    -H "apikey: <anon-key>" -H 'Content-Type: application/json' \
    -d '{"email":"someone@known.address","password":"anything"}'
  # must return: {"code":422,"error_code":"email_provider_disabled",...}
  ```
- The sign-in page shows the Google button and **no** password field.
- Sign in as the admin; the calendar loads.
- `railway logs --service beat` shows the scheduler running. The Q-04 sweep
  fires at **00:05 Asia/Kolkata**; a deployment whose beat process is not
  running looks completely healthy until pending bookings start piling up.
- The audit log has one `admin.bootstrapped` entry and nothing else.

### MCP (spec 004)

Set `MCP_PUBLIC_URL` on the Railway **api** service to the API's public
address plus `/mcp`, e.g. `https://<railway-domain>/mcp`. Until it is set the
Account page says MCP is not enabled and issues no tokens. Verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<railway-domain>/mcp   # 401 without a token
```

### Custom domain

The portal is served at `https://tva.nunnarilabs.com` (added in Netlify domain
management, 29 September 2026); the `*.netlify.app` address keeps working.
Three places must know a frontend address, and all three must change together
when it does:

- Supabase Auth `site_url` and the redirect allow-list (`supabase/config.toml`,
  `[auth]`). Google sign-in returns to `<origin>/auth/callback`; an origin that
  is not listed is sent to `site_url` instead, so people land on the wrong
  domain signed out.
- `FRONTEND_URL` on the Railway api, worker and beat services: links in Slack
  messages and the OAuth consent page for claude.ai.
- Nothing in Google Cloud: Google redirects to Supabase, not to the portal.

### Org operations (spec 006)

- **Slack.** Set `SLACK_BOT_TOKEN` (scopes `chat:write`, `users:read`, `users:read.email`,
  and `chat:write.public` or invite the bot to the channel) and
  `SLACK_SIGNING_SECRET` on the api, worker and beat services. Then Admin →
  Notifications → *Send me a test*. Set `slack_out_channel` under Admin →
  Policy (e.g. `#general`) to turn on the morning who-is-out post.
- **Schedules** need the `beat` service running: nudge at `nudge_hour` on
  weekdays, gaps Friday 17:00, over-allocation Monday 09:00, morning post
  09:00 weekdays, digest Monday 09:05, auto-confirm 00:15, comp-off lapse
  00:20 (all Asia/Kolkata).
- **OAuth for claude.ai** needs nothing beyond `MCP_PUBLIC_URL` and
  `FRONTEND_URL` being correct on the api service: the issuer is the API
  origin and the consent page is `FRONTEND_URL/auth/connect`. Verify:

  ```bash
  curl -s https://<railway-domain>/.well-known/oauth-authorization-server | head -c 200
  ```

- **Calendar feeds** use `FEED_SECRET` if set, otherwise a value derived from
  the service key. Changing either invalidates every feed address at once.

### After the management layer (spec 003) is deployed

Nothing in the new tier works until an admin does two things by hand, under
Admin → People:

- Set the role of each delivery head to **Manager**. A manager runs projects
  and sees money; they still do not approve leave unless they are also
  somebody's approver. Until this is done nobody but the admin can create a
  project, and there is no Projects link in anybody's navigation.
- Record a **CTC period** (annual, with a start date; Admin → People → CTC)
  for everybody who will log project hours, and set revenue plus at least
  one phase on each project so monthly revenue exists. Every figure is
  marked *incomplete* until every person on it has a CTC for the days in
  question — a missing CTC is an unknown cost, not a free hour. CTC is
  dated, so it can be entered for past months after the fact; nothing is
  priced until it exists.

Verify the guard, because it is the point of the release: signed in as a lead,
`/projects` must show *Only a manager or admin can do that*, and the Effort
page must have no Money or Resources tab.

### After expected days and sign-off rules (002 FR-ANALYTICS-07, 006 FR-SIGN) are deployed

- Set `portal_start_date` (e.g. `2026-10-01`) under Admin → Policy. Until it
  is set, every working day before go-live counts as missing in coverage,
  the nudges and sign-off.
- Untick **Logs time** under Admin → People for anyone who keeps no
  timesheet.

## 6. The sales CRM (spec 007)

The CRM ships in the same image, the same three services and the same
frontend as the rest of the portal. Releasing it is a normal release
(below) with migrations `025` to `044`, plus the settings here. Read
[`docs/architecture/crm.md`](../architecture/crm.md) first; it explains what
each piece is for.

### First release

1. **Migrations `025`–`044`** with `npx supabase db push`, as every release.
   `026` makes the owner (`profiles.is_owner`) an Admin of both workspaces,
   Nunnari Labs and Nunnari Academy B2B, and seeds their select lists, stage
   probabilities and saved views. Nobody else is a member until invited.
   `025` revokes every table privilege from `anon` and `authenticated`: the
   browser reads nothing from Supabase directly any more, only through the
   proxy.
2. **`api`, `worker` and `beat` from the same commit.** The CRM adds Celery
   tasks (`crm.*`) and beat entries (the trash purge, flags every 30 minutes,
   the digest and email-sync ticks every 15); a worker left on an older commit
   drops them silently.
3. **Netlify**, with `--skip-functions-cache`. The proxy now carries uploads
   up to 4 MB and CSV/JSON downloads.
4. **Check**, signed in as the owner: the sidebar shows **CRM**; `/crm/labs`
   and `/crm/academy` open on Today; Settings → Members lists the owner as
   Admin. Then:

   ```bash
   curl -s https://<railway-domain>/health/jobs        # {"status":"ok",…}; 503 while a job is late
   ```

   A CRM job is "late" only once beat has had time to run it (up to a day for
   the trash purge), so expect `ok` once the first ticks have run.
5. **Invite the sales team** from Settings → Members in each workspace (spec
   §4.4). Each invitee signs in with Google at the invited address.

### Settings, by service

Nothing new is needed on `api`, `worker` or `beat` for the CRM to work.
Gmail sync stays **off**, and says so in the CRM, until both of these exist:

| Variable | Service | Value |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `worker-crm-sync` **only** | The service-account key: the raw JSON, or base64 of it. Never on `api`, `worker` or `beat`; never in Netlify |
| `CRM_GMAIL_CONFIGURED` | `api`, `beat` and `worker` | `true` once the key is set on `worker-crm-sync`. A non-secret flag: the API, the tick and Settings read it instead of the key |

The service account needs domain-wide delegation in Google Workspace for the
`https://www.googleapis.com/auth/gmail.readonly` scope only, in each domain
the CRM syncs (`nunnarilabs.com`, `deepweaver.ai`, `nunnari.academy`). It
reads only the mailboxes and labels listed in CRM Settings, and stores
subjects and participants, never bodies.

### Switching Gmail sync on

1. Create the `worker-crm-sync` service in the Railway project from the same
   repository and `backend/Dockerfile`, with `PROCESS_TYPE=worker_crm_sync`
   and the same base variables as `worker` (`SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `REDIS_URL`, `FRONTEND_URL`). Deploy it like
   the others:

   ```bash
   cd backend && railway up . --path-as-root --service worker-crm-sync
   ```

   It is a second always-on worker, so it adds to the Railway bill (spec Q-74);
   that is why it is created only now.
2. Set `GOOGLE_SERVICE_ACCOUNT_JSON` on `worker-crm-sync`.
3. Set `CRM_GMAIL_CONFIGURED=true` on `api`, `beat` and `worker`, and redeploy
   them (from the same commit as `worker-crm-sync`).
4. In each workspace, an Admin adds the mailboxes and labels under Settings →
   Mailboxes, then turns sync on. **Run now** checks it end to end; the run
   appears under Settings → Jobs.

To switch it off: turn it off in each workspace, set `CRM_GMAIL_CONFIGURED`
back to `false`, and stop `worker-crm-sync`.

### Email to CRM-only people

CRM-only sellers are generally not in Nunnari's Slack, so the only way to
reach them out of the app is email, which needs a production SMTP sender
(`SMTP_*` on `api`, which sends invitations, and on `worker` and `beat`, which
send digests and reminders; spec Q-73; the Google Workspace SMTP relay
is the recommendation). Until it is set, they get the in-app bell only, their
invitation link has to be sent by hand, and each digest run counts them as
*undeliverable* in Settings → Jobs.

### Monitoring

Point an external uptime monitor at `GET https://<railway-domain>/health/jobs`.
It answers 503 while any CRM job is more than twice its interval late, which
is how a stopped `beat` shows itself. It carries no counts, errors or record
content.

### The database tests in CI

The `db` job in `.github/workflows/ci.yml` starts a local Supabase, applies
every migration with `supabase db reset` and runs `pytest -m db` against it:
grants, triggers, constraints and the database functions. It never touches
the hosted project (the tests refuse any non-loopback URL).

### Never commit `supabase/config.toml` local changes

The local password sign-in script (`backend/scripts/ops/local_password_auth.sh`)
edits `supabase/config.toml`. That edit must never be committed: a later
`supabase config push` would enable password sign-in in production. Before
every commit, `git diff --cached --name-only | grep -c config.toml` must
print `0`.

### Cutover from the old CRM

Two scheduled Claude tasks named "CRM follow-up sweep" live outside this
repository and read the old CRM. At cutover, repoint them at this portal's
`crm_*` tools (a morning `crm_today` per workspace) or retire them (spec
D16). The Twenty import itself is Settings → Import, dry run first.

## 7. Tasks (spec 008)

Tasks ship in the same image, services and frontend. The CRM's task table
becomes the shared `tasks` table, so this release changes how every CRM task
is stored; read [`docs/architecture/tasks.md`](../architecture/tasks.md)
first. Release in a quiet hour.

### First release (phase 1)

1. **Report the linkless CRM tasks first.** Migration `046` moves every live
   CRM task that has no linked record into its workspace Trash before the
   rename (spec §8.2). Count them in the Supabase SQL editor before
   `db push`, and report the number to the owner:

   ```sql
   SELECT count(*) FROM crm_tasks t
   WHERE t.deleted_at IS NULL
     AND NOT EXISTS (SELECT 1 FROM crm_task_links l WHERE l.task_id = t.id);
   ```

   `046` prints the same count as a NOTICE when it runs.
2. **Migrations `046`–`051` and `056`** with `npx supabase db push`, as every
   release: `046` the linkless clean-up, `047` the rename, new statuses and
   fields, `048` the CRM functions on the new names, `049` the task write
   path, `050` the phase level (everyone starts at 0), `051` the Life OS
   import's run records, `056` the lock on link removal. `047` and `048`
   must apply in the same push: between them, CRM task writes fail.
3. **`api`, `worker` and `beat` from the same commit**, after the
   migrations. Tasks add the Celery task `tasks.purge_deleted` and its beat
   entry (01:20 IST daily); a worker left on an older commit drops it.
4. **Netlify**, with `--skip-functions-cache`.
5. **Check**, signed in as the owner: CRM Today, a deal's Tasks tab and the
   CRM Tasks page show the same tasks as before, with To do and In progress
   now Open. Nothing of My Tasks shows yet for anyone.

### Raising levels, person by person

Every phase is off for everyone until switched on (C6). A person's level `n`
switches on phases 1 to `n`; a person with no level of their own has the
global default, which starts at 0.

1. The constitution amendment (spec §15.1) is agreed before phase 1 goes to
   anyone but the owner.
2. A TVA admin raises the owner first: Admin → People → **Tasks** → 1, or
   `update_user` with `changes: {"tasks_level": 1}`. Each change is audited.
3. Then the core team, one person at a time, the same way. A person who is
   assigned a personal task needs level 1 first ("*Name* does not have My
   Tasks yet.").
4. Only when everyone should have it, raise the default with `update_setting`
   (`tasks_default_level`). Setting a person back to **Default** makes them
   follow it again.

CRM task behaviour never depends on the level: CRM tasks work for everyone
from the release on.

### Voice capture (phase 4)

Migration `054` adds the `voice_uploads` table and the private Storage bucket
`task-voice`. The worker transcribes each note with Groq (`whisper-large-v3`)
through the Celery task `tasks.transcribe_voice`; the beat entry
`tasks-voice-sweep` (every 30 minutes) deletes any audio or transcript before
it is 24 hours old.

| Variable | Service | Value |
|---|---|---|
| `GROQ_API_KEY` | `worker` **only** | The Groq API key, created by the owner (spec §15.1). Never on `api` or `beat`; never in Netlify; never logged |
| `TASKS_VOICE_CONFIGURED` | `api` | `true` once the key is set on `worker`. A non-secret flag: while it is false, uploading a voice note answers "Voice capture is not set up yet." |

Set the key on `worker` first and redeploy it, then set the flag on `api`.

## 8. The CRM intelligence layer (spec 009)

Email summaries, the Review Queue, meeting capture from Fireflies, company
enrichment, AI fields, the follow-up rules and contact recency ship in the
same image and frontend, plus one new Railway service. Read the
"intelligence layer" section of
[`docs/architecture/crm.md`](../architecture/crm.md#the-intelligence-layer)
first. Nothing changes for anyone on release day: **every phase is off in
every workspace, and the budget is zero, until a workspace Admin sets a
budget and switches a phase on.**

### First release

1. **Migrations `060`–`068`** with `npx supabase db push`, **after every
   `008` migration** (`046`–`059`, section 7) is applied; never in the same push
   ahead of them, because several of them replace CRM functions
   (`crm_audit_row`, `crm_purge`, `crm_merge_companies`, …) starting from
   their `008` versions. They add the AI tables (RLS on, no browser
   grants), the `ai` workspace setting (seeded with every phase off and a
   zero budget), the new rule switches (seeded off) and their thresholds,
   the new job names and the contact-recency columns.
2. **Create `worker-crm-ai`** in the Railway project, from the same
   repository and `backend/Dockerfile`, with `PROCESS_TYPE=worker_crm_ai`
   (`celery -A app.worker worker -Q crm-ai --concurrency=1`) and the same
   base variables as `worker` (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `REDIS_URL`, `FRONTEND_URL`). Concurrency 1 is deliberate: it bounds
   budget overshoot to one call and keeps the Gmail sync lock free.

   ```bash
   cd backend && railway up . --path-as-root --service worker-crm-ai
   ```

   Without it, beat's AI ticks wait on the `crm-ai` queue and no AI job
   runs at all.
3. **`api`, `worker`, `beat`, `worker-crm-sync` (if it exists) and
   `worker-crm-ai` from the same commit**, after the migrations. Beat gains
   the AI ticks (summaries, meetings, enrichment and AI fields every 15
   minutes; review expiry at 01:30 IST); the shared worker gains the R-1 to
   R-4 checks in the automations job and only enqueues ids onto `crm-ai`.
4. **Netlify**, with `--skip-functions-cache`. The new help guide is
   `/help/crm-intelligence`.
5. **Check**, signed in as a workspace Admin: Settings → AI shows a zero
   budget, every phase off, and "The AI rates are not set yet" until the
   rates are set (below); Settings → Jobs lists the AI jobs as skipped
   "Switched off".

### Settings, by service

| Variable | Service | Value |
|---|---|---|
| `ANTHROPIC_API_KEY` | `worker-crm-ai` **only** | The Claude API key (secret). Never on `api`, `worker` or `beat`; never in Netlify |
| `FIREFLIES_API_KEY` | `worker-crm-ai` **only** | The Fireflies API key (secret). It sees only the meetings of the Fireflies account it belongs to; record which account that is |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `worker-crm-ai`, as well as `worker-crm-sync` | The same service-account key as sync (section 6), for the summariser's read of stored messages. Needed only for email summaries and R-1 |
| `CRM_GMAIL_CONFIGURED` | `worker-crm-ai`, as well as `api`, `beat` and `worker` | `true` once the key above is set. The summariser treats email sync as off without it |
| `CRM_AI_INPUT_USD_PER_MTOK`, `CRM_AI_OUTPUT_USD_PER_MTOK` | `worker-crm-ai` **and `api`** | The rate table the owner confirms (US dollars per million input and output tokens). Not secret. `worker-crm-ai` uses them to estimate each call's cost; `api` needs them too, or `GET /crm/ai` reports `rates_configured: false` and Settings → AI keeps saying the rates are not set |

While either rate is unset, every AI job is recorded `skipped` "AI rates are
not set", whatever the budget and phases say. While `ANTHROPIC_API_KEY` is
unset, an AI job that does run fails its items with a fixed reason and no
model call is made. The model is fixed in code (`claude-sonnet-5-5`); confirm
the key can use it before switching phase 1 on.

None of these keys is logged or reaches the browser. After setting them,
redeploy the services whose variables changed, from the same commit.

### Release order and switching on

1. Migrations, then the services, then Netlify (above).
2. Owner: set the secrets and rates above.
3. Phase 1 (email summaries and R-1) also needs Gmail sync live (section 6,
   "Switching Gmail sync on"). Phase 2, and rules R-2 to R-4 of phase 3, do
   not read Gmail and can run before it.
4. **Nunnari Labs first.** A Labs Admin sets a monthly budget under
   Settings → AI and switches phases on one at a time. Check Settings → Jobs
   after the next tick: each AI job should be `ok`, not `skipped` or
   `failed`.
5. **Nunnari Academy B2B at least one week later**, after the owner has
   reviewed what Labs produced (spec 009 FR-CRMAI-FLAG-05). This is an
   operating rule; nothing in the code enforces it.

To stop all AI work at once in a workspace, an Admin switches its phases off
or sets the budget to zero. To stop it everywhere, stop `worker-crm-ai`: the
ticks then wait on the queue, and AI jobs that have run before show as late
under Settings → Jobs and on `/health/jobs`. Stored summaries, notes and
values are kept either way.

## Releasing a change

Every release, in this order:

1. **Migrations first.** Get the database password before you start, then
   `npx supabase db push`. New code reads and writes the new columns: deployed
   ahead of its migration, creating a person fails and editing one returns an
   error after the change was saved.
2. **`api`, `worker` and `beat` from the same commit** — and `worker-crm-sync`
   and `worker-crm-ai` too, once they exist. A worker left on the old commit drops any new Celery
   task the api sends it, silently.
3. **Netlify**, with `--skip-functions-cache` (section 3), so the server
   function picks up `next.config.ts` (security headers included).
4. **Check.** The section 5 curls, plus:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://<railway-domain>/docs           # 404
   curl -s -o /dev/null -w '%{http_code}\n' https://<railway-domain>/openapi.json   # 404
   curl -sI https://<netlify-domain>/auth/login | grep -iE 'x-frame-options|frame-ancestors|nosniff'
   ```

## Rolling back

Application code: redeploy the previous commit on Railway and Netlify.

**Migrations do not roll back.** They are immutable once applied (constitution,
Data Integrity). To undo a schema change, write a new migration that reverses
it. `005_audit_triggers.sql` in particular makes the audit log append-only
against the service role and the table owner alike — there is no supported way
to delete from it, and that is the point.
