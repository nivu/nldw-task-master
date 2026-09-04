# Deploying the portal

NFR-08: deployment MUST be reproducible from the repository. This file is that
requirement's implementation — if you find yourself doing something here that
is not written down, write it down.

**Currently deployed:**

| | |
|---|---|
| Frontend | <https://nunnari-employee-portal.netlify.app> |
| Backend | <https://api-production-9edd.up.railway.app> |
| Supabase | project `worjtvnpizpyfimwotkl`, region `ap-south-1` (Mumbai) |
| Railway | project `nunnari-employee-portal` — `api`, `worker`, `beat`, `Redis` |

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
```

`RUN_EMBEDDED_WORKER` must be false in production. True runs a Celery worker
inside the API process, so two pools consume the same queue and the API
container carries a worker's memory alongside uvicorn.

Notification credentials, when they exist, go on `worker` and `beat` (which
send) and on `api` (which verifies Slack's request signature):

```
SLACK_BOT_TOKEN=          # scopes: chat:write, users:read.email
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
netlify deploy --build --prod
```

> **`netlify deploy --build` builds LOCALLY, using `frontend/.env`.** It is not
> a CI build. Any development-only value in that file ships straight into the
> production bundle — this was caught with
> `NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=true`, which put a password form on the
> production sign-in page. Every variable that must differ has to be set in
> Netlify **and** overridden on the build command:
>
> ```bash
> NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=false netlify deploy --build --prod
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

Then go back to Railway and set `FRONTEND_URL` to the Netlify address, and to
Supabase to set `site_url`.

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

## Rolling back

Application code: redeploy the previous commit on Railway and Netlify.

**Migrations do not roll back.** They are immutable once applied (constitution,
Data Integrity). To undo a schema change, write a new migration that reverses
it. `005_audit_triggers.sql` in particular makes the audit log append-only
against the service role and the table owner alike — there is no supported way
to delete from it, and that is the point.
