# How the leave calendar works

How the system works *today*. What it must do lives in
[`specs/001-leave-calendar/spec.md`](../../specs/001-leave-calendar/spec.md);
why, and the rules it may not break, live in the constitution.

---

## The shape of it

```
  browser
     │  every call carries a Supabase access token
     ▼
  Next.js  ──  /api/proxy/[...path]        ← the ONLY route to the backend.
     │                                       BACKEND_URL never enters a client
     │                                       bundle (verified: no chunk in
     ▼                                       .next/static contains it)
  FastAPI  ──  app/api/deps.py             ← the real authorisation boundary
     │         app/domain/*                ← the rules, pure and testable
     │         app/services/*              ← the only writer
     ▼
  Supabase Postgres                        ← RLS guards the browser's own
                                             connection, not this one
```

The backend holds the service-role key and therefore **bypasses RLS entirely**.
That is the single most important thing to understand before changing anything:
a missing check in `app/api/deps.py` will not be caught by a database policy.
The policies in `004_rls_policies.sql` exist because the browser also talks to
Supabase directly for auth, and because defence in depth is cheap — but they are
not the primary mechanism.

## Where a rule actually lives

Almost every rule is a pure function in `app/domain/`, taking the facts it needs
as arguments and reading nothing. That is why 121 tests run in under a tenth of
a second with no database.

| Rule | File | Spec |
|---|---|---|
| Is this day still editable? | `domain/rules.py::is_locked` | §6.3, FR-BOOK-08 |
| May this category be booked for this date? | `domain/rules.py::check_timing` | §6.1, FR-BOOK-09/10 |
| Is there enough allowance? | `domain/rules.py::check_allowance` | FR-BOOK-05 |
| What is the balance? | `domain/ledger.py` | FR-BAL, §7.3 |
| Who may decide this? | `domain/approval.py::can_decide` | FR-APPR-05, Q-05 |
| What does a day cost? | `domain/cost.py` | §6.2, Q-09 |
| May an admin backfill this? | `domain/rules.py::check_backfill` | FR-BACK, A-21 |
| Is this timesheet line valid? | `domain/timesheets.py` | 002 FR-TIME, 003 FR-ACT |
| What did a project cost, and is that figure complete? | `domain/financials.py` | 003 §4, FR-FIN |
| Who may see somebody's timesheet? | `domain/approval.py::can_view_timesheet` | 002 Q-08, 003 §7 |

## Three things that are easy to get wrong

### 1. Dates are calendar dates, not instants

A booking's `date` is a `date` column, and every "what day is it" question goes
through `domain/calendar.py::today_in_company_tz`. Asia/Kolkata is +05:30, so a
UTC-hosted process asked for `date.today()` at 02:00 IST answers with
*yesterday*. Nothing errors; the edit window just closes early for anyone
booking in the evening, and a leave balance drifts quietly wrong.

`test_lock_window.py::TestTimezone` pins this.

### 2. Balances are derived, never stored

There is no `remaining_days` column and adding one would be a bug. Balances are
recomputed from `allowances` (what was granted) minus consuming bookings, every
time. At tens of users this costs nothing, and the alternative — a counter — is
wrong the first time any code path forgets to decrement it, with no way to tell.

The carry-forward policy (spec Q-02, still open) is a *bound on a sum*, not a
schema decision. `rolling` accumulates from the start of tracking; `pooling`
resets each January. Both are implemented; `app_settings.carry_forward_policy`
picks. This is why the question can stay open without blocking anything.

### 3. The audit log is append-only in the database, not by convention

`005_audit_triggers.sql` installs triggers that reject `UPDATE`, `DELETE` and
`TRUNCATE` on `audit_log`, and revokes those privileges. It holds against the
service role and against a superuser — verified by trying all three.

RLS could not do this, because the backend bypasses RLS. "We agreed not to
rewrite it" is not an enforcement mechanism, and §7.5 notes the log is a
prerequisite for the analytics discussed for later versions, which are only
defensible if the record is known to be untampered.

**Reasons never enter the audit log.** `services/audit.py` redacts `reason` and
`decision_note`. A sick-leave reason is health information about a named
colleague (NFR-05); the audit log is admin-readable and long-lived, so copying
one in would both widen and outlive that access. The log records that a
transition happened, not somebody's medical situation.

## The one hole in the lock

Spec A-21 / FR-BACK. An admin can record leave somebody already took, on a date
that is already locked. It exists because go-live happens partway through a
month and the leave already taken has to go in somehow.

Everything about how it is built is aimed at keeping it from growing:

* `domain/rules.py::check_backfill` is a **separate function**, not a
  `skip_lock=True` argument on `validate_booking`. An exception reachable from
  the ordinary path is one refactor away from not being an exception.
* `services/bookings.py::backfill` and `undo_backfill` are likewise their own
  pair, not privileged branches inside `create_or_replace` and `withdraw`.
* Past dates only. Today and the future are ordinary bookings.
* The undo checks `backfilled_by IS NOT NULL`. An admin can correct their own
  typo; they can never reach a record somebody made themselves. That single
  clause is what keeps §6.3 true for real bookings.
* Every backfilled row is marked, and the mark is shown on the person's own
  calendar and on their lead's roster. Nothing here produces a row that looks
  like an ordinary one.
* The allowance check deliberately does *not* apply — this records what
  happened, and at go-live the allowances usually have not been set yet.
  `test_backfill.py` asserts the absence of that parameter structurally, so
  adding it later fails a test rather than silently changing behaviour.

## Who can see a reason

Three layers, deliberately not the same:

1. **RLS** grants the *row* to the person, their lead, and admins.
2. **`domain/approval.py::can_view_reason`** answers whether a given viewer is
   entitled to the reason at all.
3. **The API decides which columns leave the building.** `GET /api/v1/team`
   omits `reason` entirely (Q-06); `GET /api/v1/team/approvals` includes it,
   because a lead cannot approve a request whose reason they cannot read.

Q-06 is a setting (`lead_view_shows_reason`, default false) so the decision is
visible in the product rather than buried in a document.

## Roles, and who can see money (spec 003)

Four roles: `user`, `lead`, `manager`, `admin`. Two properties in
`app/api/deps.py::CurrentUser` decide most things — `is_lead` (lead, manager,
admin: may see the team view and effort) and `is_manager` (manager, admin: may
run projects and see money). Three guards hang off them: `LeadDep`,
`ManagerDep`, `AdminDep`.

A manager runs *projects*; a lead runs *people*. A manager approves nobody's
leave unless they are also somebody's `lead_id` (`can_decide` is unchanged),
and does not gain access to leave reasons (`can_view_reason` is unchanged —
health data, 001 Q-06). `can_view_timesheet` is a separate rule precisely so
that widening one never silently widens the other.

Money — `cost_periods` (CTC, spec 005), `projects.revenue`, and everything
derived from them — is withheld from everyone below manager in three places,
on purpose. (`profiles.cost_rate_hourly` and `time_entries.cost_rate_snapshot`
are 003's retired rate model: still in the schema, no longer read or written.)

1. **The database.** `009_management.sql` revokes table-level SELECT on those
   three tables from the browser role and re-grants it column by column,
   without the money columns. A `select *` from the browser now fails loudly
   rather than leaking. (A column-level `REVOKE` on its own is a no-op when a
   table-level grant exists — that was the first attempt, and it withheld
   nothing.)
2. **The API.** Every money route lives behind `ManagerDep`, separately from the
   `LeadDep` effort routes, and no route below manager ever selects the
   columns. A person cannot see their own rate (003 Q-01).
3. **The arithmetic.** `domain/financials.py` and `domain/pnl.py` treat a
   missing rate as *unknown*, never zero; every figure carries `complete` and
   names who is unrated. The rate on an entry is derived from the CTC period
   covering the entry's date (`services/pnl.py::RateBook`) — past hours at
   past figures, upcoming plans at upcoming ones — so a change never
   re-prices history.

Monthly profit (spec 005 §3): a project's revenue is spread evenly over the
working days of its timeline (first phase start to last phase end); past
months attribute it by logged hours, the current and future months by
allocation; a person's monthly cost is their CTC pro-rated by covered working
days. `/analytics/pnl` and `/analytics/timeline` are manager-tier.

CTC is labelled *CTC* or *cost to company* on every screen and never *salary*.
It is a fully-loaded cost the company attributes; it is not what anybody is
paid, and the person it describes cannot see it.

**Managers see every project** (003 Q-02). This was chosen against the
recommendation; every delivery head can see every other project's margin and
every person's CTC. The consequence is recorded in the spec so it is
never mistaken for an oversight.

## MCP — the portal as a tool server (spec 004)

`app/mcp/server.py` mounts an MCP endpoint at `/mcp` on the same FastAPI
process. Every tool is a thin wrapper that calls the matching API route
**in-process** (httpx over an ASGI transport) with the caller's own bearer
token, so authorisation, validation, error wording and request logging are
the route's, verbatim. There is no second authorisation code path.

Callers authenticate with a **personal access token** (`nunp_…`, spec 004
FR-TOK). `deps.current_user` recognises the prefix and resolves it through
`api_tokens` (hash only; 90-day expiry; revocable); everything after that is
unchanged, including the deactivated-owner check. The token routes
(`/me/tokens`) are guarded by `SessionDep`: a token cannot issue, list or
revoke tokens, which is what bounds a leak. Every request logs `auth_via` as
`session` or `token`.

Two things are done to route answers before they reach the model: `reason`
is withheld at any depth (FR-MCP-03), and nothing else. `test_mcp.py` fails
if a route is added without a tool, or a write tool stops asking for
confirmation.

The MCP address shown on the Account page comes from `MCP_PUBLIC_URL` on
the backend, returned to a signed-in person by the API; it is not in the web
bundle.

## Org operations (spec 006)

Thirteen additions, all thin over the existing model:

- **Schedules** live in `app/worker.py` (Celery beat, Asia/Kolkata) and
  `app/tasks/ops.py`; the messages are composed in `services/digest.py` and
  delivered by `services/notify` (Slack DM by email; a channel post for the
  morning who-is-out). Every job is also runnable from Admin → Notifications.
- **Sign-off**: `timesheet_confirmations`, `services/confirmations.py`;
  auto-confirm runs nightly for weeks whose edit window closed.
- **Comp-off**: a fourth booking category with no allowance. Credits in
  `compoff_credits` (`services/compoff.py`, rules in `domain/compoff.py`);
  `bookings.create_or_replace` consumes them and withdraw / reject / release
  return them; a nightly task lapses expired ones.
- **Locations**: `locations`, `profiles.location_id`, `holidays.location_id`
  (NULL = everywhere). `services/holidays.py` answers per person; the calendar,
  day form, week view, utilisation and hiring use it. Org-wide effort views
  still use the union of all holidays, deliberately.
- **Money views**: `services/utilisation.py` (utilisation, bench, hiring),
  `services/health.py` (RAG from `domain/health.py`), milestones in
  `project_milestones` overriding the even spread in `services/pnl.py`.
- **Statements**: `services/statements.py`, JSON and CSV, hours only.
- **Checklists**, **reviews**, **feeds**: their own small services; the
  calendar feed is unauthenticated by design and keyed per person
  (`domain/feeds.py::feed_key`, rotated by changing `profiles.feed_salt`).
- **OAuth** (`services/oauth.py`): the SDK's authorization-server routes are
  mounted at the API origin when `MCP_PUBLIC_URL` is set; approval happens in
  the web app (`/auth/connect`) with a Supabase session and issues an ordinary
  personal token tagged with the client, plus a hashed refresh token. The MCP
  401 carries `resource_metadata` so claude.ai can discover it.

## The help guides

`docs/guides/*.md` are the public how-to pages. `frontend/lib/help.ts` reads
them at build time and `/help` serves them as static HTML, without sign-in
(`/help` is in `PUBLIC_ROUTES`). Editing a guide is editing markdown; it ships
with the next frontend deploy. Because they are public they describe how
screens work and never who is on them or what anything costs — the e2e suite
checks that no seeded name appears.

## Background work

Celery over Redis. Two things run off the request path:

- **`notifications.dispatch`** — FR-NOTIF. Takes a booking *id*, not a rendered
  message: Celery arguments are serialised into Redis, and a reason must not sit
  in a queue. Slack and email adapters are written against the real APIs and are
  inert until their credentials exist.
- **`bookings.lock_sweep`** — spec Q-04. Runs at **00:05 Asia/Kolkata**, just
  after the edit window closes, and promotes pending bookings whose date has
  passed. `decided_by` is left NULL, which is what marks the row as a `system`
  action in the audit log rather than attributing it to a lead who never
  decided. Idempotent, so a missed night is repaired by the next one.

  **This needs a `beat` process** alongside the worker (see `Procfile`). Without
  it the sweep never fires and pending past-dated bookings accumulate.

Enqueueing is wrapped in try/except everywhere (FR-NOTIF-05). The failure being
guarded is not a rejected Slack message but an unreachable broker, where
`.delay()` itself raises — somebody marking themselves sick at 08:00 must not be
blocked because Redis is down. Verified by stopping Redis and booking anyway.

## Testing it

```bash
cd backend  && uv run pytest    # 137 unit tests over the pure rules, no database
cd frontend && pnpm test:e2e    # 32 browser tests, phone + desktop viewports
```

The unit tests need nothing running. The browser tests need Supabase and the
backend up, and they sign in as the seeded people and write real rows — run
them against a local database and expect bookings left behind; `db reset` puts
it back.

One thing worth knowing about the browser suite: it **builds and serves its own
app on port 3100** every run rather than reusing whatever is on 3000. `next
start` serves the asset manifest it booted with, so rebuilding underneath a
running server makes every stylesheet 404 and the app render unstyled — which
looks like a dozen unrelated layout failures. Two separate debugging sessions
went into that hole before the config was changed to make it impossible.

## Running it

```bash
npx supabase start && npx supabase db reset   # schema + demo data
cd backend  && docker compose up -d redis
cd backend  && uv run uvicorn app.main:app --reload --port 8000
cd backend  && uv run celery -A app.worker worker --loglevel=info
cd backend  && uv run celery -A app.worker beat   --loglevel=info
cd frontend && pnpm dev
```

Demo accounts, all with password `portal123` (see `supabase/seed.sql`):

| Email | Role |
|---|---|
| `vinita@nunnari.example` | admin |
| `devansh.nl@gmail.com` | lead |
| `sriram.nl@gmail.com` | manager |
| `deepika.nl@gmail.com` / `tarun.nl@gmail.com` | user |

## Known gaps

- **Allowance figures are placeholders** (spec Q-01). wfh 4.0, casual 1.5, sick
  1.0 per month were invented so the product is usable in development. They must
  be replaced before the first live month.
- **The sandwich rule's `true` branch does not exist** (spec Q-09). The setting
  refuses to be switched on rather than silently doing nothing.
- **Notification senders have no credentials.** Both adapters are complete;
  adding `SLACK_BOT_TOKEN` (scopes: `chat:write`, `users:read.email`) plus
  `SLACK_SIGNING_SECRET`, or `SMTP_HOST`, activates them with no code change.
  Slack's interactivity request URL is `/api/v1/slack/interactions`.
(Route guarding lives in `frontend/proxy.ts` — the Next.js 16 name for what
used to be `middleware.ts`. Which routes are guarded is configured in
`lib/supabase/middleware.ts`, not in that file.)
