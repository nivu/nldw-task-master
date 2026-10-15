# How the sales CRM works

How the CRM works *today*. What it must do lives in
[`specs/007-crm/spec.md`](../../specs/007-crm/spec.md), the tables and
database functions in [`data-model.md`](../../specs/007-crm/data-model.md),
the routes and tools in [`contracts/api.md`](../../specs/007-crm/contracts/api.md);
why, and the rules it may not break, live in the constitution.

The CRM is a second application inside the portal: its own pages under
`/crm`, its own routes under `/api/v1/crm/`, its own `crm_*` tables, and a
narrow, deliberate set of places where it touches TVA (the time, leave and
projects side). Two workspaces exist, Nunnari Labs (`labs`, linked to TVA)
and Nunnari Academy B2B (`academy`, not linked). Nothing ever crosses from
one workspace to the other.

---

## The shape of it

```
  browser (/crm/<workspace>/…)
     │  Supabase access token, or a personal token (nunp_…)
     ▼
  Next.js  ──  /api/proxy/[...path]          ← still the only route to the backend;
     │                                          it now carries uploads ≤ 4 MB and
     │                                          CSV/JSON downloads (FR-CRM-CHG-08)
     ▼
  FastAPI  ──  app/api/deps.py               ← who you are (staff or crm_only)
     │         app/api/crm/deps.py           ← the CRM guard: workspace + role
     │         app/domain/crm/*              ← the rules, pure
     │         app/services/crm/*            ← gather rows, build a plan
     │         app/services/crm/store.py     ← the only module that touches crm_* tables
     ▼
  Postgres ──  crm_apply / crm_deal_transition / …   ← one transaction per write;
               audit trigger, workspace-scoped references, re-checks
```

Claude reaches the same routes: every `crm_*` MCP tool is one in-process call
to a `/crm` route as the token's owner (`app/mcp/crm.py`), so the web app,
the API and MCP pass through the same guard and the same rules (D3).

## The request path and the CRM guard

`app/api/deps.py` authenticates every request once (`_authenticate`: session
or personal token → an active profile) and then splits:

| Dependency | Who passes | Used by |
|---|---|---|
| `current_user` (and `LeadDep`, `ManagerDep`, `AdminDep`, …) | staff only; a `crm_only` account gets 403 "This account is for the CRM only." | every TVA route, unchanged |
| `any_account` | staff and `crm_only` | the shared allow-list only: `GET /me`, personal tokens, the OAuth consent routes |
| `crm/deps.py` `crm_context` | any member of the named workspace | every `/api/v1/crm/` route |

**Deny by default for `crm_only`.** Because the TVA dependencies kept their
names and gained the refusal, a new TVA route refuses CRM-only people without
anyone remembering to. `tests/test_crm_only_refused.py` walks `app.routes`
and fails on any route it cannot classify; the allow-list is a constant in
that test, so widening it is a visible change. Every TVA people population
(bench, resources, utilisation, team day, digests, nudges, balances, …)
leaves CRM-only accounts out because `services/supabase.list_profiles`
does unless a caller passes `include_crm_only=True`, which only the Admin
People list, the TVA admin CRM panel and the audit name lookup do.

**The workspace.** `crm_context` reads the `workspace` query parameter (a key
such as `labs`, or an id). A workspace the caller is not a member of is
answered 404 "No such workspace.", the same as one that does not exist. With
no `workspace`, a read uses the caller's last-used workspace; any other
request from a member of more than one workspace is refused 422 naming their
workspaces (D13), so a write never lands where the person did not mean it.

**The role.** Admin, Member or Viewer, per workspace. What each may do is one
pure table, `domain/crm/permissions.py` (`may(role, action, *, owns,
is_assignee)`). A TVA role grants nothing in the CRM; only a membership does.
A TVA admin who is in no workspace administers memberships from the TVA
Admin "CRM workspaces" panel (`/api/v1/crm/tva-admin/…`), guarded by the TVA
admin role, not by a membership.

**Another workspace's record is 404.** Every store read takes `workspace_id`
first and filters on it, so a record of Academy named while acting in Labs is
"not found", exactly like an id that does not exist.
`tests/test_crm_isolation_sweep.py` checks every route with an id in its
path, every list, search, Today, export, dashboard and bell, the digest
builders and every `crm_*` tool.

## The browser has no table

Every table in `public`, TVA's and the CRM's, has row-level security on and
no grant at all to `anon` or `authenticated` (025 revoked them). The browser
signs in with Supabase Auth and then reads nothing from Supabase: every byte
of data goes through the proxy to FastAPI, which holds the service-role key.
No Realtime either; the bell polls `/api/v1/crm/notifications/unread-count`.
`tests/db/test_crm_security_db.py` reads the catalogue to prove it for every
table, including ones added later.

The backend's service-role key **bypasses row-level security**. The guard in
`api/crm/deps.py` and the `workspace_id` filters in `store.py` are what keep
workspaces apart in Python; the database functions below hold the same line
again, so a Python bug cannot write across workspaces.

## The write path and the audit trigger

```
route ─▶ service: load rows ─▶ domain: decide ─▶ plan ─▶ store.rpc(fn, {p_actor, p_plan})
                                                          │  one transaction
        set_config('crm.actor', actor, true) ◀────────────┘
        INSERT / UPDATE / DELETE crm_*  ──AFTER trigger crm_audit_row──▶ audit_log
        TVA rows (projects, phases, milestones, allocations) + their own audit rows
        RAISE CRM04 / CRM09 / CRM22 ──▶ store.refusal_for ──▶ 404 / 409 / 422
```

- **Every CRM write is one database function call.** Plain creates, updates,
  soft deletes, restores and link rows go through `crm_apply(actor, ops)`;
  anything with side effects has its own function (below). The service never
  writes a `crm_*` table with a plain PostgREST insert.
- **The actor** is `{user_id, kind: user|system|import|sync, channel:
  web|mcp|null, workspace_id}`, built from the `CrmContext`, never from the
  request body. `crm_apply` stamps `workspace_id` on every insert and refuses
  an update, delete or restore of a row in another workspace.
- **The audit trigger** `crm_audit_row` (on every CRM record and config
  table) writes one `audit_log` row per changed row, in the same transaction,
  with the workspace, actor kind, channel and the before and after values. A
  write with no actor set is refused: "A CRM change must be made through the
  CRM." The logs (`crm_stage_history`, `crm_job_runs`, `crm_notifications`,
  `crm_digest_sends`, the email log, `crm_import_runs`) and the static field
  registry have no audit trigger; the security test names them.
- **Stale edits.** A plan carries the `updated_at` the person saw; the
  function refuses CRM09 "This record changed while you were editing it;
  reload and try again." if the row moved on.
- **Refusals** come back as Postgres error codes and are mapped to sentences
  written in Python (`store.refusal_for`): CRM04 → 404, CRM09 → 409, CRM22 →
  422, a unique violation → 409 with its own sentence. No Postgres message
  reaches a client; anything else is logged and becomes the generic 500.
- **The audit `channel` is a label, never a permission.** `_authenticate`
  sets it to `mcp` only when the in-process MCP bridge sends the per-process
  key (`X-Portal-Channel-Key`, compared with `hmac.compare_digest`), else
  `web`. The proxy never forwards `X-Portal-*` headers. No authorization
  decision reads the channel.

## References stay inside the workspace

Every `crm_*` table that is referenced has `UNIQUE (workspace_id, id)`, and
every reference between `crm_*` tables is a composite foreign key on the pair,
`(workspace_id, company_id) REFERENCES crm_companies (workspace_id, id)`, so
a row in Labs cannot point at a row in Academy whatever the code does. The
references that cannot be foreign keys (the polymorphic `record_type` /
`record_id` of Drive, task, note and email links, favourites and
notifications, and the `channel_ids` option array) carry the
`crm_check_refs()` trigger, which raises CRM04 when the target is missing or
in another workspace. The security test reads `pg_constraint` and `pg_trigger`
to hold every reference to one rule or the other.

## Transition functions and their re-checks

The plan is built in Python, where the rules are tested with fakes; the
function writes it atomically and **trusts none of its ids**:

| Function | Does |
|---|---|
| `crm_deal_transition` | every deal write with side effects: a stage move (history, probability, the company's lead stage, open tasks closed on a loss), the Labs project create / probability sync / confirm (the handoff) / archive and release / reopen |
| `crm_link_project` | links a deal to an existing TVA project (FR-CRM-TVA-18) |
| `crm_merge_companies` | moves people, deals, tasks, notes, links, campaign members and the project link to the survivor; the merged company goes to Trash |
| `crm_purge` | permanent delete, with what hangs off the record |
| `crm_invite`, `crm_remove_membership`, `crm_convert_account` | accounts and memberships, under row locks |

Each locks the rows it changes `WHERE workspace_id = actor.workspace_id`,
works out the deal's project from `projects.source_opportunity_id` (never
from an id in the plan), requires the workspace to be `tva_linked` for any
project action, and re-checks who may do the TVA part (next section). A
caller added later (bulk, import, MCP) therefore cannot get around a rule by
building a different plan.

## Redaction

A Viewer receives no amount anywhere: deal `amount`, `amount_in_inr`,
`weighted_amount`, the FX rates, a campaign's budget and the figures derived
from its deals, and the TVA-derived `revenue`, `suggested_inr` and
`existing_tva_revenue`. The currency code stays.

- `domain/crm/objects.py` marks the amount fields; `services/crm/present.py`
  omits them for a Viewer from records, timelines (an amount change shows as
  a change, without values), search hits, kanban group headers, table
  footers, the bell and the digests.
- A Viewer filter, sort, grouping, total or column on an amount is refused
  422 "Viewers cannot filter, sort or total by amount.", in Python and again
  inside `crm_query` (`p_redact`). A shared view with an amount condition
  opens for a Viewer with the condition removed and a notice.
- Viewers cannot open the dashboard or export at all.
- Nothing is redacted in the MCP layer: the tools return what the routes
  return, so the same rules apply.

`tests/test_crm_redaction_sweep.py` walks every answer a Viewer can get, from
every route and every read tool, with its own written-out list of amount
names, so an omission in production cannot hide itself.

## TVA integration (Labs only)

A Labs deal drives one TVA project through its life; an Academy deal never
touches TVA.

| Deal | TVA project |
|---|---|
| Moves to Proposal | a **tentative** project is created with the deal's probability (a name clash is refused, naming the project, offering rename or link) |
| Probability changes while open | the project's probability follows |
| Closed Won (the handoff) | the project is **confirmed** with its lead and phase dates; revenue and milestones (INR) only when the person is a TVA manager or admin, otherwise a "Confirm revenue and milestones" task for the Labs admins |
| Closed Lost | the project is archived and its allocations **released** (who, when, why) |
| Reopened from Lost | the project is unarchived and exactly those allocations un-released |

TVA writes made by a deal move are done as the **system actor, stamped with
the person** (D8): the TVA audit row reads "*Name* (via CRM deal)". For a
linked project, the TVA Projects page and `update_project` refuse status and
probability, and archiving while tentative: "Managed by CRM deal *name*."
(the deal's name only for Labs members).

**Who may link a project.** Linking a deal to an existing TVA project (a
name clash resolved by link, an imported deal's Link TVA project, the
linkable-projects list) is for staff TVA managers and admins only, checked in
the service and again in `crm_deal_transition` / `crm_link_project` from the
actor's profile. Without that, a Member could adopt someone's tentative
project by naming a deal after it, then archive it and free its people with a
Lost move.

**Readers.** Every TVA reader of allocations (forecast, resources, bench,
over-allocation, hiring signal, utilisation, planned cost and the P&L,
project health, the CEO dashboard, digests, Home, the allocatable picker)
counts only allocations that are not released and not on an archived project,
through `services/supabase.list_counting_allocations`. An archived tentative
project also drops out of the P&L pipeline. This applies to every archived
project, not only CRM ones.

## Capacity

`services/capacity.py` is the one capacity service (FR-CRM-CHG-17): per week,
**supply** (each staff account's contracted hours between joining and leaving,
less the holidays that apply to them and their approved leave) and **demand**
(counting allocations, tentative apart from confirmed). TVA's forecast and the
CRM's capacity check (`GET /crm/capacity`, the Pipeline panel and the
dashboard) both read it. The CRM shows tentative and free hours per week,
and hides a week with fewer than three contributors, so no one person's
availability can be read off it.

## Background jobs and queues

All on the existing Celery worker and beat, except Gmail pages:

| Job | Schedule | Queue |
|---|---|---|
| `crm.purge_trash` (90 days) | 01:10 daily | default |
| `crm.recompute_flags` (follow-up and deal-health flags, stale-deal notices) | every 30 min | default |
| `crm.digest_tick` (daily digest at 09:00 the person's time, weekly admin digest on Mondays, task reminders) | every 15 min | default |
| `crm.email_sync_tick` | every 15 min | default; enqueues pages |
| `crm.email_sync_page` (one page of one mailbox) | enqueued | `crm_sync`, served only by the `worker-crm-sync` service |

Every run writes `crm_job_runs`; three failures in a row alert the workspace
admins once. Lateness is checked when read: admins see stale jobs on Today,
and `GET /health/jobs` answers 503 while any job is more than twice its
interval late, for an external monitor. Digests go out exactly once per
person per day (`crm_digest_sends` is claimed before sending); out-of-app
messages use the portal's `notify.deliver` (Slack DM, email fallback).

**Gmail sync is inert until switched on.** The service-account key
(`GOOGLE_SERVICE_ACCOUNT_JSON`) lives on `worker-crm-sync` only; the API,
beat and the shared worker hold just the non-secret `CRM_GMAIL_CONFIGURED`
flag. Only subjects, participants, direction and time are stored, never a
message body.

## The MCP tools

Eighteen `crm_*` tools (`app/mcp/crm.py`): `crm_workspaces`, `crm_today`,
`crm_search`, `crm_get`, `crm_list`, `crm_create`, `crm_update`,
`crm_move_stage`, `crm_handoff`, `crm_set_next_step`, `crm_add_task`,
`crm_complete_task`, `crm_add_note`, `crm_merge`, `crm_import`,
`crm_campaign_members`, `crm_dashboard`, `crm_run_email_sync`. Each is one
route call. Each description opens with who may call it, in capitals; every
write says CONFIRM FIRST and is annotated as a write (destructive for a
stage move, the handoff and a merge). Every tool takes an optional
`workspace`; the route applies D13. CRM routes are exempt from spec 004's
"one tool per route" rule (FR-CRM-CHG-07); `tests/test_crm_mcp.py` holds the
set to exactly these eighteen.

## Three things that are easy to get wrong

### 1. `crm.actor` is transaction-scoped

The audit trigger reads the actor from `current_setting('crm.actor')`, set
with `set_config(…, true)`, which lasts for the current transaction only.
Each PostgREST call is its own transaction, so setting the actor in one RPC
and writing in the next does not work: the write is refused "A CRM change
must be made through the CRM." Set it inside the function that writes, as
`crm_apply` and every transition function do. A migration that seeds or
fixes CRM rows by hand must set the system actor first in the same
statement batch (026's seeds show how).

### 2. A new function is executable by everyone until revoked

Postgres grants `EXECUTE` on a new function to `PUBLIC`. Every CRM function
therefore ends its migration with `REVOKE ALL ON FUNCTION … FROM PUBLIC,
anon, authenticated` and `GRANT EXECUTE … TO service_role`. Forget it and
anyone holding the public anon key can call the function through PostgREST
(`/rest/v1/rpc/…`), bypassing every Python check. `CREATE OR REPLACE` keeps
the grants of the function it replaces; a new signature does not. The
security test fails on any `crm_%` function `anon` or `authenticated` can
execute.

### 3. Count allocations through the counting reader

`list_allocations` returns every allocation row, released ones and those on
archived projects included; it is for the Admin allocations list, which shows
them marked. Anything that **counts** allocations (capacity, cost, bench,
forecast, health) must use `list_counting_allocations`, or a lost deal's
people stay "busy" and its planned cost stays in the P&L.
`tests/test_allocation_release_readers.py` pins every reader to it.

## Running it locally

[`specs/007-crm/quickstart.md`](../../specs/007-crm/quickstart.md) §1: local
Supabase with `db reset` (the seed adds CRM-only people and memberships), the
API, the worker and beat, and the frontend. The real-database tests run with
`cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q`; the 10,000-row timing
check is `uv run python scripts/simulations/crm_load_10k.py` (quickstart §5).
