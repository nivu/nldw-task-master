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
| `sriram.nl@gmail.com` / `deepika.nl@gmail.com` / `tarun.nl@gmail.com` | user |

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
