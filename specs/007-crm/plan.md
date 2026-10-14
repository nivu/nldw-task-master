# Sales CRM — Plan

| | |
|---|---|
| **Feature** | `007-crm` |
| **Branch** | `007-crm` |
| **Date** | 14 October 2026 |
| **Spec** | [`spec.md`](spec.md) (operative); [`source.md`](source.md) (owner's original, behaviour only) |
| **Companions** | [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md), [`quickstart.md`](quickstart.md), [`tasks.md`](tasks.md) |
| **Constitution** | v1.2.0 |

This is the HOW for `spec.md`. Where this file and the spec disagree, the spec
wins and this file is wrong. Every FR id below is the spec's.

`001`–`006` have no plan files; this is the repo's first. It follows their
spec style (tables, FR ids, decisions with ids) rather than the spec-kit
template's layout.

---

## 1. Summary

The CRM is a new area of the existing portal, not a new service. The same
FastAPI backend gains a `/api/v1/crm/...` router family, the same Next.js
frontend gains a full-width `/crm/[workspace]/...` area, and the same Supabase
database gains `crm_*` tables. Nothing new is deployed in 1a. In 1b, one extra
Railway worker service runs Gmail sync, and only once the Google credential
exists.

Six design choices hold the rest together:

1. **CRM-only people are `profiles` rows with `account_type = 'crm_only'`**
   (D2). `deps.current_user` refuses them by default, so every existing TVA
   route refuses them with no change to that route. A short list of shared
   routes opts in with a separate dependency.
2. **One CRM guard.** `api/crm/deps.py` resolves the workspace and the
   caller's CRM role once per request. The web app, the API and every
   `crm_*` MCP tool pass through it (D3, FR-CRM-ACC-10).
3. **Every CRM write goes through a Postgres function** that sets the actor as
   a transaction-local setting. An AFTER trigger on every CRM table writes
   `audit_log` from that setting in the same transaction, and refuses a write
   that has no actor (FR-CRM-AUD-06, Q-21). Ordinary edits use one generic
   function, `crm_apply`. Each multi-table transition (deal moves with their
   TVA effects, project link, merge, purge, membership removal) has its own
   function. These are the repo's first business RPCs.
4. **Rules are checked in Python and written by SQL.** Domain functions decide
   (pure, tested with fakes, as today). Services build a validated **plan**.
   The database function only writes that plan atomically, re-checking the
   few facts that can change in a race (Q-68).
5. **Viewer redaction is done once, in the presenter**, on the way out of
   every route. MCP passes route answers through unchanged, so it inherits the
   redaction (FR-CRM-ACC-11, FR-CRM-MCP-07).
6. **TVA readers change in one place.** `db.list_profiles()` leaves out
   CRM-only accounts by default. A new `db.list_counting_allocations()`
   leaves out released allocations and allocations on archived projects. Every
   people reader and allocation reader is switched to these (FR-CRM-CHG-02,
   -03, -04).

**MCP tool count.** D6 says 17; source §15.3 lists 19 tools, less
`crm_audit` = 18 (Q-07). The operative spec, FR-CRM-MCP-01, lists **18** (15
in 1a, 3 in 1b). This plan builds 18.

---

## 2. Technical context

| | |
|---|---|
| **Language/Version** | Python 3.11+ (CI 3.13), TypeScript 5 strict |
| **Backend** | FastAPI (sync `def` handlers), Pydantic v2, pydantic-settings, supabase-py (service role), Celery + Redis, MCP SDK ≥ 2.2 |
| **Frontend** | Next.js 16 App Router, `@supabase/ssr` (auth only), shadcn base-nova on `@base-ui/react`, `@tanstack/react-table` (already installed, unused until now), recharts |
| **Storage** | Supabase Postgres. New `crm_*` tables, `ALTER`s on `profiles`, `audit_log`, `projects`, `allocations`. Extension `pg_trgm` (in `extensions`). No Storage buckets (D4). |
| **Testing** | pytest with in-memory fakes, as today. New: a `db` marker for real-database tests run against a local Supabase in a new CI job (NFR-CRM-10, Q-68). Playwright e2e with seeded password accounts, local only. |
| **Target platform** | Railway (api, worker, beat, Redis; 1b adds `worker-crm-sync`), Netlify (frontend), Supabase `ap-south-1` |
| **Performance goals** | NFR-CRM-01: lists, search, record page, and table/kanban with saved filters under 1 s at 10,000 rows per object; paging fast at 50,000; kanban drag optimistic |
| **Constraints** | Netlify function body ≈ 6 MB, so uploads are capped at 4 MB (FR-CRM-CHG-08). PostgREST returns at most 1,000 rows per call, so CRM lists always page server-side. Celery `task_time_limit = 600`. Beat runs in IST wall-clock. |
| **Scale/scope** | 2 workspaces, about 20 users, about 10k companies per workspace at most, 6 record objects (+ campaigns, members in 1b), 18 MCP tools |

### 2.1 New dependencies (NFR-CRM-11, Q-27)

Principle II says to replace a dependency that fewer than 50 lines of clear
code could replace. None of these can be.

| Package | Where | Why it passes the 50-line rule | Slice |
|---|---|---|---|
| `@dnd-kit/core`, `@dnd-kit/sortable` | frontend | Accessible, touch-capable drag with keyboard sensors and auto-scroll for the kanban (FR-CRM-VIEW-01, NFR-CRM-06). HTML5 drag-and-drop does not work on touch screens and has no keyboard path. | S6b |
| `cmdk` | frontend | A command palette with fuzzy filtering, grouped results, ARIA combobox semantics and keyboard navigation (FR-CRM-SRCH-01/02). Getting the focus and ARIA behaviour right is well past 50 lines. | S7 |
| `python-multipart` | backend | FastAPI needs it to parse `UploadFile` (the 4 MB import upload, FR-CRM-CHG-08). It is already in `uv.lock` through `mcp`. It becomes a direct dependency so an `mcp` upgrade cannot silently remove it. | S8 |
| `pyjwt[crypto]` | backend | Gmail service-account access (FR-CRM-SYNC-03): sign an RS256 JWT with the key's `client_email`, the `sub` mailbox and the `gmail.readonly` scope, then POST it to Google's token endpoint with `httpx`. That is about 40 lines in `services/crm/gmail.py`. It is already in `uv.lock` through `mcp` and becomes direct for the same reason. | S10 |

Not added: `google-auth` and `google-api-python-client`. The decisions
allowed `google-auth`, but the token exchange is under 50 lines on `pyjwt`,
which is already installed, so Principle II prefers it. Also not added:
Tiptap or any rich-text editor (Q-27: notes are plain text with markdown,
rendered with `marked`, which is already a dependency); `xlsx` (D4b: CSV
only); `psycopg` (db tests use supabase-py against local Supabase);
`react-virtual` (server paging keeps pages at ≤ 200 rows).

### 2.2 New configuration

| Setting | Where | Default | Meaning |
|---|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `app/config.py`, `SecretStr \| None` | unset | Service-account key, raw JSON or base64 (FR-CRM-SYNC-03). Set on `worker-crm-sync` **only**: it is the one process that calls Gmail. The API, beat and the shared worker never hold it. Access tokens live only in that process's memory, never in Redis, the database or Celery arguments. |
| `CRM_GMAIL_CONFIGURED` | `app/config.py`, `bool` | `false` | Non-secret signal that the credential exists on `worker-crm-sync`. Set on `api`, `beat` and `worker`. The tick, `POST /crm/email-sync/run` and the Settings page read this flag, never the key. Sync is inert while it is false. |
| `GMAIL_DOMAINS` | constant in `domain/crm/email_rules.py` | `nunnarilabs.com`, `deepweaver.ai`, `nunnari.academy` | The Google Workspace domains the service account is delegated for (FR-CRM-SYNC-03). A mailbox outside them is refused. |
| `CRM_SYNC_QUEUE` | constant in `app/worker.py` | `crm_sync` | Celery queue served only by `worker-crm-sync` (FR-CRM-SYNC-04, Q-74) |
| `PROCESS_TYPE=worker_crm_sync` | `backend/entrypoint.sh` | — | New process type: `celery -A app.worker worker -Q crm_sync --concurrency=1` |

Per-workspace settings (probabilities, allow-list, thresholds, automation
switches, email sync on/off) live in `crm_workspace_settings`, not in
`app_settings`. `app_settings` is global, TVA-admin-only and refuses unknown
keys (`admin.py` `update_setting`), so it is the wrong home for workspace
configuration. No `app_settings` key is added.

---

## 3. Constitution check

| # | Question (constitution, Governance) | Answer |
|---|---|---|
| 1 | Does this serve one of the journeys? | Yes: **The Seller's Journey** (v1.2.0). The Today list, deal moves, the Proposal-to-project and won/lost lifecycle, and Sanjeev's invitations each map to a sentence in it. The TVA-side changes (released allocations, archived projects not counting, one capacity service) serve the Lead's and Admin's planning journeys that the Seller's Journey names ("whoever plans staffing sees the work coming"). Phase 1b items trace through FR-CRM-* to the same journey. |
| 2 | Security-First and Clear Boundaries? | Yes, with the exceptions in §4. CRM-only refusal is deny-by-default in `deps.current_user` (§5.1). Workspace isolation is enforced in the CRM guard, again in every store query (`workspace_id = $ws`), and again inside `crm_apply` (§5.3). New tables: RLS on, no browser grants. CRM functions: `EXECUTE` revoked from `public`, `anon` and `authenticated`. The browser makes no Supabase data call; the bell polls through the proxy (FR-CRM-NOTIF-01). Because a CRM-only seller holds a valid session JWT and the public anon key, the TVA tables' old browser grants (`projects_all_select`, `holidays_all_select`, `app_settings_select`, the `*_self_select` policies, the column grants of 009 and 012) would let them read TVA data through PostgREST. 025 therefore revokes every table privilege in `public` from `anon` and `authenticated` (the frontend makes no table read; a grep of `frontend/` finds no `.from(` or `.rpc(` call). RLS stays enabled. A db test proves a `crm_only` JWT reads nothing (data-model §4.0). The proxy stays the only path and is widened, not bypassed (FR-CRM-CHG-08). Drive links only, no files (D4). |
| 3 | Data Integrity: no schema changes outside migrations, no cross-tenant exposure? | Yes. Seventeen numbered migrations, `025`–`041` (data-model §1). Multi-table transitions are single plpgsql transactions (§III, NFR-CRM-03). Cross-workspace exposure is tested at route level (FakeDb) and database level (real functions) in S1b, S3 and S12. Point-in-time values (`amount_in_inr`, rate used) are stored like `cost_rate_snapshot`. Balances are untouched. |

**Re-check after design:** done. The design adds no principle exception
beyond §4.

---

## 4. Complexity tracking

Each row is a genuine exception to a constitution sentence. Each needs the
owner's explicit review (Governance, Conflicts).

| # | Exception (principle) | Why needed | Simpler alternative rejected because |
|---|---|---|---|
| CT-1 | **Dynamic SQL in plpgsql** (`crm_apply`, `crm_query`, `crm_search`), against §I "SQL injection is mitigated by the Supabase client's parameterised query builder" | FR-CRM-AUD-06 requires every write to set an actor in the same transaction as the write, which PostgREST table calls cannot do. FR-CRM-VIEW-02/03/08 need nested AND/OR filters, multi-sort, group counts and sum/avg/min/max aggregates; PostgREST aggregates are off in Supabase by default and cannot group by arbitrary columns. All three functions build SQL only with `format('%I', …)` / `format('%L', …)` and `USING` parameters, against a column registry held in the database (`crm_object_fields`). Table names must match `crm\_%` and carry the audit trigger. Python validates the spec first (`domain/crm/views.py`). No caller-supplied text is ever concatenated. | Writing each filter combination as a static function: unbounded. Compiling SQL text in Python and passing it in: an injection channel between layers. A direct Postgres connection (psycopg) with the builder: a second data-access path and a new `DATABASE_URL` secret (repo-map C1). |
| CT-2 | **CRM-only accounts appear in two TVA admin screens**, against §I "MUST NOT appear in any portal people list" | FR-CRM-CHG-02 and -11: a TVA admin must see and manage account types and memberships without being a workspace member (FR-CRM-ACC-01, -13). Shown marked "CRM only" in Admin → People and in Admin → CRM workspaces, and excluded from every count, report, reminder and job. | Hiding them from TVA admins entirely: then nobody outside a workspace can deactivate a CRM-only account, and a stranded account (its last workspace admin gone) cannot be found. |
| CT-3 | **TVA admins read a workspace's name, member list and job-run metadata without membership**, against §I "No CRM query… MAY return data from a workspace the caller is not a member of" | Q-69: these are account administration, not CRM records. They are served only by `/api/v1/crm/tva-admin/*` (TVA `AdminDep`), which never reads a `crm_*` record table, and job-run error text is stripped of record content for non-members (FR-CRM-JOB-01). The same routes also show TVA admins the account-administration audit events of every workspace (`membership.*`, `invitation`, `user.account_type`, `workspace.allowed_domains`), without record content, so their own actions do not vanish from their view (`GET /admin/audit` shows only TVA rows). | Making TVA admins workspace members to administer: grants them every CRM record, which is the larger exposure. |
| CT-4 | **The system actor performs TVA writes the person could not make** (D8, FR-CRM-TVA-03), against §IV "MUST enforce its own authorization before touching the database" and `004` §8 "a tool never has more access than the person" | A CRM Member who is a TVA `user`, or a CRM-only seller, must be able to move a deal to Proposal or Won. The authorisation is still enforced, as a CRM rule: only the effects listed in FR-CRM-TVA-04 to -14 and -18, only through `crm_deal_transition` / `crm_link_project`, which accept no other TVA write, and only revenue and milestones stay gated on the person's own TVA role (FR-CRM-TVA-08). Each TVA row written records `actor_label = 'system'` with the triggering person. Web and MCP use the same path, so `004` §8 holds as amended by FR-CRM-CHG-07. | Requiring a TVA lead to move every deal: contradicts the Seller's Journey ("Tamil moves a deal to Proposal"). Leaving the project untouched until a manager acts: re-creates the retyping the CRM exists to remove (G-2). |
| CT-5 | **An invitation is not one transaction**, against §III "All write operations that touch multiple tables MUST be transactional" | Creating the sign-in identity goes through GoTrue's admin API, which no database transaction can include. The plan follows `admin.create_user`: create the auth user, then call `crm_invite` (data-model §5.13), which inserts or reactivates the profile, inserts the membership and writes their audit rows in one transaction. Only the GoTrue step is outside it. If `crm_invite` fails, the auth user is deleted, but **only when this request created it** (`created_account = true`); an existing GoTrue user is never deleted. The constitution's "where the database supports it" covers the GoTrue step. | None available: Supabase offers no transactional create-user. |

**Considered, and not exceptions:** stored follow-up flags (FR-CRM-FLAG-02)
and digest sent-markers are derived state. §III forbids storing *balances*,
and the spec chose storage for 10k-row filtering (Q-20). Recomputation is
idempotent and every flag carries its `flags_computed_at`. Per-owner activity
counts (FR-CRM-CHG-13) amend specs, not the constitution. Workspaces are
allowed by v1.2.0.

---

## 5. Architecture

### 5.1 Who may call what — `api/deps.py`

```text
request ─▶ _authenticate(request) ─▶ profile          (token or session; unchanged logic)
              │
              ├─ current_user        (TVA)    refuses account_type='crm_only'  ─▶ every TVA route, unchanged
              ├─ any_account         (shared) allows both kinds               ─▶ the allow-list only
              └─ crm/deps.crm_context(workspace) ─▶ CrmContext(user, workspace, role)  ─▶ every /crm route
```

- `_authenticate` is today's `current_user` body (bearer → GoTrue or personal
  token → active profile). It also sets `request.state.channel`. The channel
  is **self-reported, a label for the audit only**, and no authorization
  decision ever reads it. To keep the label honest, `mcp/server.py` generates
  a random `CHANNEL_KEY = secrets.token_urlsafe(32)` at process start, and
  `_api` sends it as `X-Portal-Channel-Key` on every in-process call.
  `_authenticate` sets `"mcp"` only when that header equals the key
  (`hmac.compare_digest`), else `"web"`. The backend is public on Railway
  (claude.ai reaches `/mcp` on it), so a plain `X-Portal-Client: mcp` from a
  token holder must not be enough. The proxy forwards only `Authorization`
  and an allow-listed `Content-Type`, never `X-Portal-*`, `Cookie` or
  `X-Forwarded-*` (§5.12).
- `api/slack.py` `_resolve_actor` refuses a `crm_only` profile ("This account
  is for the CRM only."), since that route is unauthenticated by session but
  acts as the person, and CRM-only people now receive Slack DMs through
  `notify.deliver`.
- `current_user` = `_authenticate` + `if account_type == 'crm_only': 403
  "This account is for the CRM only."` `CurrentUserDep`, `LeadDep`,
  `ManagerDep`, `AdminDep`, `DashboardDep` and `SessionDep` keep their names,
  so **no TVA route changes** to gain the refusal (FR-CRM-CHG-01).
- `AnyAccountDep` / `AnySessionDep` (new) are used by exactly the shared
  allow-list: `GET /me`, `GET|POST /me/tokens`, `DELETE /me/tokens/{id}`,
  `GET /oauth/transaction/{txn}`, `POST /oauth/approve`. Sign-out is
  frontend-only, and the OAuth AS routes (`/authorize`, `/token`, `/register`)
  are unauthenticated. `GET /me` returns the reduced shape for `crm_only`
  (contracts §1).
- `/me/feed` and `/me/feed/rotate` stay on `SessionDep` and so refuse
  CRM-only people. `feeds.resolve` also returns None for a `crm_only`
  profile.
- `api/crm/deps.py`: `CrmContext(user: Authenticated, workspace:
  Workspace, role: 'admin'|'member'|'viewer', channel)`, from query param
  `workspace` (key or id). The D13 rule lives here: with no `workspace` on a
  `GET`, use the caller's last-used membership. On any other method, a
  member of more than one workspace is refused 422, naming their workspaces
  (FR-CRM-MCP-04). A workspace the caller is not a member of is answered
  **404** "No such workspace.", like a foreign record id (FR-CRM-WS-02).
  Annotated deps: `CrmReadDep` (any role), `CrmWriteDep` (admin|member),
  `CrmAdminDep`, `CrmLabsDep` (workspace `tva_linked`), `CrmCapacityDep`
  (Labs admin or a staff member, FR-CRM-CAP-02).
- The capability matrix of spec §3.3 is one pure table in
  `domain/crm/permissions.py` (`may(role, action, *, owns=False,
  is_assignee=False)`), used by the deps and the services. Tests enumerate the
  matrix (S1b).

### 5.2 Layering — follows `api` / `domain` / `services`

| Layer | New modules | Rule |
|---|---|---|
| `app/api/crm/` | `__init__.py` (`ROUTERS`), `deps.py`, `workspaces.py`, `members.py`, `settings.py`, `records.py`, `deals.py`, `views.py`, `today.py`, `search.py`, `merge.py`, `imports.py`, `audit.py`, `capacity.py`, `tva_admin.py`; 1b: `notifications.py`, `jobs.py`, `email.py`, `campaigns.py`, `dashboard.py`, `export.py` | HTTP only. Raise `ProblemDetail`. Return presenter output. Sync `def`. |
| `app/domain/crm/` | `permissions.py`, `settings.py`, `objects.py` (field registry: type, amount flag, system-written flag, Labs-only/Academy-only), `pipeline.py`, `fx.py`, `dedupe.py`, `invites.py`, `views.py`, `today.py`, `handoff.py`, `importer.py`; 1b: `flags.py`, `digests.py`, `campaigns.py`, `dashboard.py`, `csv_io.py` | Pure. Take `today` / `now` as arguments. Return `None` or a refusal sentence, or a plan dataclass. |
| `app/domain/capacity.py` | weekly supply and demand arithmetic (FR-CRM-CHG-17) | Pure, shared by TVA and CRM |
| `app/services/crm/` | `store.py` (all `crm_*` table reads and the RPC wrappers), `context.py`, `records.py`, `deals.py`, `tva.py`, `members.py`, `views.py`, `today.py`, `search.py`, `merge.py`, `importer.py`, `present.py`; 1b: `flags.py`, `notifications.py`, `digests.py`, `jobs.py`, `gmail.py`, `email_sync.py`, `campaigns.py`, `dashboard.py`, `export.py` | Gather rows, call domain, build the plan, call one RPC. Raise `CrmRefused(message, status)`. |
| `app/services/capacity.py` | one capacity service (FR-CRM-CHG-17) | Read by `analytics.forecast` and `/crm/capacity` |
| `app/schemas/crm.py` | every CRM request/response model | `extra='forbid'`, decimals as strings, `Literal` for fixed keys. A module beside `schemas/__init__.py` so that file does not triple in size. |
| `app/tasks/crm.py` | Celery tasks `crm.*` (S3 purge; 1b jobs) | Thin wrappers that return summary dicts. Registered in `app/tasks/__init__.py`. |
| `app/mcp/crm.py` | the 18 `crm_*` tools | Registered on the same `mcp` object; imported at the bottom of `mcp/server.py` |

`services/crm/store.py` follows `services/supabase.py`: thin named functions
returning dict rows, on the same module-level service-role client (`from
app.services.supabase import supabase`). Every read takes `workspace_id` as
its first argument and filters on it. There is no store function that reads a
`crm_*` record table without one. A test asserts this by introspection (S1b).
TVA table changes (`list_profiles`, `list_counting_allocations`, project and
allocation updates) stay in `services/supabase.py`.

### 5.3 The write path (FR-CRM-AUD-06, Q-21, NFR-CRM-03)

```text
route ─▶ service: load rows ─▶ domain: decide ─▶ plan ─▶ store.rpc(fn, actor, plan)
                                                         │
                     Postgres, one transaction:          ▼
                     set_config('crm.actor', actor, true)
                     set_config('crm.action', op.action, true)   per op
                     INSERT/UPDATE crm_*  ──AFTER trigger──▶ audit_log (workspace, actor, channel, before/after)
                     TVA rows (projects, phases, milestones, allocations) + explicit TVA audit rows
                     RAISE 'CRM09' / 'CRM22' / 'CRM04' on a lost race ─▶ store maps to 409/422/404
```

- `actor = {"user_id", "kind": user|system|import|sync, "channel": web|mcp|null,
  "workspace_id"}`. It comes from the `CrmContext`, never from the request
  body.
- `crm_apply(actor, ops)` handles plain creates, updates, soft deletes,
  restores, upserts by `twenty_id`, and link-row inserts and deletes. It
  refuses a table that is not `crm_%` with a `crm_audit` trigger. It stamps
  `workspace_id` on insert and requires `workspace_id = actor.workspace_id` on
  every update, delete and restore. Database-level isolation therefore does
  not depend on the service getting it right.
- **References stay inside the workspace.** Writing the row's own
  `workspace_id` is not enough: a row in A must not point at a row in B. Every
  `crm_*` parent table has `UNIQUE (workspace_id, id)`, and every reference
  between `crm_*` tables is a composite FK `(workspace_id, x_id) REFERENCES
  parent (workspace_id, id)`, including every `*_id` that points at
  `crm_select_options` (data-model §2). References that cannot be FKs (the
  polymorphic task, note, Drive and email links; `uuid[]` option arrays such
  as `channel_ids`; favourites' `target_id`) are checked by one trigger,
  `crm_check_refs()` (data-model §5.14), which raises `CRM04` when the target
  is missing or in another workspace.
- **Transition functions re-check every id they are handed.** The plan is
  built in Python, but `crm_deal_transition`, `crm_link_project` and
  `crm_merge_companies` do not trust its ids: they lock the opportunity and
  company `WHERE workspace_id = actor.workspace_id`, work out the project from
  `projects.source_opportunity_id` (never from a plan `project_id`, except the
  target of a `link`), require the workspace to be `tva_linked` for any
  project action, require `close_tasks` to belong to the deal and workspace,
  and require `create_tasks` assignees to hold a membership (data-model §5.5–
  §5.7).
- Transitions: `crm_deal_transition` (every deal write with side effects:
  stage change, probability change on a linked project, handoff, Lost
  release, reopens), `crm_link_project` (FR-CRM-TVA-18), `crm_merge_companies`
  (FR-CRM-DUP-03), `crm_purge` (FR-CRM-DEL-01/02), `crm_remove_membership`
  (FR-CRM-ACC-12), `crm_invite` (FR-CRM-ACC-04), `crm_convert_account`
  (FR-CRM-ACC-14). Signatures are in data-model §5.
- **Optimistic concurrency.** Every plan carries `expect_updated_at` for the
  row it was built from. The function refuses `CRM09` "This record changed
  while you were editing it; reload and try again." if it no longer matches.
  This is the same idea as `update_booking_if_status`.
- `audit.record` (Python) is **not** used for CRM tables. It stays for TVA
  writes made outside these functions (TVA routes, invitations' profile
  creation), and gains optional `workspace_id`, `actor_label` and `channel`
  keyword arguments for the invitation and account-type rows (FR-CRM-CHG-09).
- Log tables (`crm_stage_history`, `crm_job_runs`, `crm_notifications`,
  `crm_digest_sends`, `crm_email_messages` and its links, `crm_import_runs`)
  are history or system state, not records. They have no audit trigger and
  are written directly by the backend or by the transition functions.

### 5.4 Errors — RFC 7807 unchanged

`api/errors.py` is unchanged. Pipeline refusals are one sentence naming
**every** missing field, built by `domain/crm/pipeline.py` (`"Proposal needs
amount, currency and expected close date."`). The frontend shows `detail`
verbatim, as everywhere. Postgres messages never reach a client:
`store.rpc()` maps the codes `CRM04`, `CRM09`, `CRM22` and `23505` to sentences
written in Python. Anything else is logged and becomes the generic 500. Where
the web app needs structure (a name clash it can offer to link), it calls the
**preview** route first, which returns the structure; the 409 only names the
clash.

### 5.5 Redaction — `services/crm/present.py`

- `AMOUNT_FIELDS` per object comes from `domain/crm/objects.py`:
  opportunity `amount`, `currency` (kept), `amount_in_inr`, `fx_rate_used`,
  `won_fx_rate`, `weighted_amount`; campaign `budget`, `budget_currency`
  (kept, like opportunity `currency`), `budget_in_inr`, `budget_fx_rate`,
  `pipeline_created`,
  `won_value`, `cost_per_converted`; and the TVA-derived keys `revenue`,
  `suggested_inr` and `existing_tva_revenue`. `crm_object_fields.is_amount`
  is true for each stored one. `present(row, object, role)` **omits** them for
  a Viewer (FR-CRM-ACC-11).
- The same function cleans timeline and audit `before`/`after` values, search
  hits, kanban column headers, table footers, notifications, digests, exports
  and the dashboard (which Viewers cannot open anyway).
- `domain/crm/views.validate_spec(spec, role)` refuses 422 any Viewer filter,
  sort, group or aggregate on an amount field. `views.clean_shared_view` makes
  a shared view safe for a Viewer: it drops amount conditions, amount columns,
  amount sorts, an amount `group_by` and every amount aggregate (the seeded
  `pipeline` kanban's sum headers), with the notice "A condition on an amount
  field was removed" (Q-67). The view then opens redacted instead of failing.
- The within-view `q` search escapes `%`, `_` and `\` before it becomes a
  `LIKE` pattern.
- `crm_query` also receives `p_redact boolean` and refuses amount columns
  itself (defence in depth, CT-1).
- The redaction tests use their **own** denylist (`amount`, `amount_in_inr`,
  `fx_rate_used`, `won_fx_rate`, `weighted_amount`, `budget`,
  `budget_in_inr`, `budget_fx_rate`, `pipeline_created`, `won_value`, `cost_per_converted`,
  `revenue`, `suggested_inr`, `existing_tva_revenue`, and `sum`/`avg`/`min`/
  `max` of any of these), written out in the test, never imported from
  `AMOUNT_FIELDS`, so an omission in production cannot hide itself.

### 5.6 TVA integration (Labs) — `services/crm/tva.py`

| Spec | How |
|---|---|
| FR-CRM-TVA-01, CHG-10 | `projects.crm_company_id`, `projects.source_opportunity_id UNIQUE`. The opportunity's project is `projects WHERE source_opportunity_id = opp.id`; the deal stores no copy. |
| TVA-04, -05, -10 | The plan has `project: {action: 'create', name, client, category, probability}`. Python pre-checks the clash against `lower(btrim(name))`. The function re-checks through `projects_name_unique` (unique violation → `CRM09` naming the project). `GET …/stage/preview` returns `{clash: {project_id, name, linkable}}`. |
| TVA-06 | Any deal save that changes `probability` while a tentative linked project exists goes through `crm_deal_transition` with `project: {action: 'sync_probability'}`. |
| TVA-05, -18 (who may link) | Linking a deal to an **existing** TVA project (`link_project_id` on a stage move, `POST …/link-project` in `link` mode, and `crm_handoff` link mode) is allowed only when the caller is a staff account with TVA role manager or admin (and, for link-project, a Labs Admin). The service checks it, and `crm_deal_transition` / `crm_link_project` check it again by reading `profiles.account_type`, `is_active` and `role` of `p_actor.user_id`. Anyone else gets 409 "Ask a TVA manager to link it." `GET /crm/projects/linkable` has the same guard, so non-staff never see TVA project names or clients. Without this, a CRM Member could adopt a manager's tentative project by naming a deal after it and then archive it and release its people with a Lost move. |
| TVA-07, -08, -09 | `domain/crm/handoff.py` builds the handoff from the preview (`GET …/handoff`) and checks it: lead is an active staff account, phases map onto `pre` / `delivery` / `support` with no `spillover`, and milestones sum to revenue or the difference is shown. Revenue and milestones are **INR** (labelled so in the dialog and the MCP description), and their sum is checked against revenue in INR. Revenue and milestones go into the plan only when `user.is_manager` and the account is staff; otherwise the plan carries a "Confirm revenue and milestones" task for each Labs admin who is a TVA manager or admin (all Labs admins when none is). `crm_deal_transition` re-checks this: revenue or milestones in a plan from anyone who is not an active staff manager or admin raises `CRM22`, so a bug in a new caller (bulk, import, MCP) cannot write revenue (D8). The preview includes `existing_tva_revenue` (and any TVA milestone amounts) only for a staff manager or admin; for anyone else the key is absent and `can_set_revenue` is false (TVA rule: revenue is visible to managers and admins only, `003`). The delivery-handoff task goes to the lead if the lead is a Labs member, else to the deal owner. |
| S4 before S5 | S4 has no TVA side. Until S5 lands, a Labs deal move into `proposal`, `negotiation` or `closed_won` is refused 422 "This move needs the TVA link, which is not built yet." so no Labs deal reaches Proposal without a project. S5 lifts the refusal. In S5, a move of a project-less Labs deal (an imported one) into `proposal` or `negotiation` creates the tentative project as FR-CRM-TVA-04, with the clash rule of TVA-05; Closed Won creates it confirmed (TVA-10). |
| TVA-12, -13 | Plan `project: {action: 'archive_release'}`. The function sets `is_archived = true` and `crm_archived_at = now()`, and sets `released_at`, `released_by`, `release_reason = 'Deal lost: <reason label>'` and `released_by_opportunity_id` on every unreleased allocation of the project. |
| TVA-14 | Plan `project: {action: 'unarchive_unrelease'}`. Clears `is_archived` and `crm_archived_at`, and clears the release on rows `WHERE released_by_opportunity_id = opp`. Nothing else changes. |
| PIPE-13 | Plan `project: {action: 'retentative'}`. The function re-checks time entries and invoiced milestones (`make_tentative_refusal` already ran in Python) and refuses `CRM22` naming which. |
| TVA-15, CHG-05 | `admin.update_project` refuses `status` and `probability` on a linked project, and `is_archived` while it is tentative: 422 "Managed by CRM deal *name*." The deal name is shown only when the caller is a Labs member, else "Managed by a CRM deal." `_present_project` adds `crm_managed: true` and, for Labs members only, `crm: {company, opportunity: {id, name, owner_name, won_on}}` (FR-CRM-TVA-02). This applies to both project reads, `GET /admin/projects` and `GET /analytics/projects/{project_id}` (`timesheet.py`). `list_projects` fetches the caller's Labs membership once and the linked deals and companies in one query per request, then passes them to `_present_project`, so there is no query per project. |
| TVA-18 | `crm_link_project(actor, plan)`, Labs admin who is a staff TVA manager or admin (row above). The function re-verifies that the target is unlinked, its status fits the deal's state, and it is not archived. |
| TVA-03 | The TVA rows a transition writes carry `audit_log.actor_label = 'system'`, `actor_id = <person>`, `channel`, `workspace_id = NULL`, so they stay TVA rows (FR-CRM-AUD-04). |
| CHG-02 | `rules.project_lead_refusal` refuses a `crm_only` lead. `create_allocation` refuses a `crm_only` person (422, the spec's sentences). A database trigger, `tva_staff_only`, backs both up and also covers `time_entries.user_id`. It reads the profile `FOR SHARE`, so it waits for an account-type change in flight. |
| CHG-02, ACC-14 (account type) | The staff → `crm_only` change is one plpgsql function, `crm_convert_account` (data-model §5.15). It locks the profile `FOR UPDATE`, refuses an `is_owner` account with a sentence, re-checks inside the lock that the person leads nobody, leads no project and holds no unreleased allocation, and clears `role`, `lead_id`, `logs_time` and `dashboard_access` together, so the `profiles_crm_only_shape` CHECK never fails as a raw 500 and an allocation made at the same moment cannot slip through. `admin.update_user` refuses `role`, `lead_id`, `logs_time` and `dashboard_access = true` on a `crm_only` profile (422 "Change the account type first."). |

### 5.7 TVA readers — FR-CRM-CHG-02/03/04/17

`services/supabase.py`:

- `list_profiles(*, active_only=False, include_crm_only=False)`. The default
  leaves CRM-only accounts out, so every existing caller is excluded at once.
  The callers that pass `include_crm_only=True` are the Admin → People list
  (`admin.list_users`), the TVA admin CRM panel, and the audit name lookup
  (`admin.py:830`, `read_audit`), where a CRM-only actor must still have a
  name.
- **Name-map callers keep the default**, each by decision: `admin.py:893`
  (the `list_projects` lead-name map passed to `_present_project`; a lead is
  never `crm_only`), `admin.py:728` (backfills: user and `backfilled_by`),
  `admin.py:1072` (allocations: `user_id`), `ops.py:50` (comp-off names),
  `timesheet.py:131` (project lead names), `analytics.py:54`, `:220`, `:325`,
  `:516` and `statements.py:25` (people on time entries and allocations),
  `checklists.py:79`. Every id they resolve is a lead, an allocated person, a
  time-logger or a TVA admin, none of whom can be `crm_only` (`tva_staff_only`,
  `profiles_crm_only_shape`). No TVA screen shows `projects.created_by` or
  `allocations.released_by` as a name; the only places a CRM-only actor of a
  D8 TVA write is displayed are the audit (above) and the CRM. A test pins
  each of these callers to its decision (tasks S1a).
- `list_audit(limit, *, tva_only=True)` filters `.is_("workspace_id",
  "null")`. `read_audit` uses it. Its presenter renders a TVA row with
  `actor_label = 'system'` and an `actor_id` as "*Name* (via CRM deal)", so a
  D8 change is distinguishable from the person's own edit
  (`domain/audit.actor_name` gains the `actor_label` argument).
- `list_counting_allocations(*, user_ids=None, project_id=None)`: allocations
  `WHERE released_at IS NULL AND project_id NOT IN (archived projects)`.
  `list_allocations` keeps its meaning (every row) for the Admin allocations
  list, which shows released rows marked.
- `analytics.forecast` today returns early, without the new key, when there
  are no allocations. It computes `weeks` from `services/capacity.py` before
  that return, so an empty or fully released dataset (the state after a Lost
  release) still returns weeks with supply.

Every reader to switch is listed in tasks S2. The capacity service
(`services/capacity.py`) returns, per ISO week, `supply_hours` (contracted
hours of each active staff account between `joined_on` and `left_on`, less the
holidays that apply to them via `holidays_by_person`, less approved leave),
`confirmed_hours`, `tentative_hours` (non-released allocations on non-archived
projects), and `contributors` (people with supply that week).
`analytics.forecast` adds a `weeks` block from it. `/crm/capacity` returns
`tentative_hours` and `free_hours` (`supply − confirmed`), with the week
hidden when `contributors < 3` (FR-CRM-CAP-02).

### 5.8 Views, search, Today

- `crm_query(p_workspace, p_object, p_spec, p_redact)` returns `{rows, total,
  groups, aggregates}`. The spec carries filters (an AND/OR tree), sorts,
  group field, a page (`limit` ≤ 200, keyset cursor on `(sort…, id)`), the
  visible columns, the requested aggregates and a `q` within-view search. The
  column registry `crm_object_fields` (seeded per object, data-model §3.15)
  says which columns are filterable and sortable, their type, and whether
  each is an amount. `count(*) OVER ()` is skipped above 10,000 rows in favour
  of "10,000+", so paging stays fast at 50k (FR-CRM-VIEW-10).
- Kanban = the same query with `group = <select field>` and per-group
  aggregates (count, `sum(amount_in_inr)`, weighted), plus `position` for
  manual order. A drag calls `PATCH` (or the stage route for `stage`). The UI
  moves the card at once and rolls back on refusal (NFR-CRM-01).
- `crm_search(p_workspace, p_q, p_limit, p_redact)` is a union across
  objects using `pg_trgm` (`%` and `similarity`) on `lower(name)`, emails and
  domains, ranked, top 8 per object (FR-CRM-SRCH-01/03).
- Today (`services/crm/today.py`) is five indexed queries, one per section,
  assembled in Python (FR-CRM-TODAY-01). `today` and `now` come from the
  person's time zone (`profiles.timezone`), never the server's.

### 5.9 Frontend

| Piece | How |
|---|---|
| Route group | `app/(crm)/crm/`. `page.tsx` redirects to `/crm/<last-used key>`. `[ws]/layout.tsx` is the CRM shell: full width, its own sidebar (Today, Pipeline, Companies, People, Opportunities, Tasks; 1b Campaigns, Dashboard; favourites; Settings for admins), workspace switcher, Back to TVA (staff only), bell (1b), Cmd+K (S7). `account/page.tsx` sits outside `[ws]`. It is built from the same `components/ui`, copying the `(portal)/layout.tsx` patterns (collapsible sidebar, phone bottom bar with More sheet, `localStorage` collapse inside try/catch). |
| Guarding | `lib/supabase/middleware.ts` adds `/crm` to `PROTECTED_ROUTES` (FR-CRM-UI-05). `app/page.tsx` stays `redirect('/home')`: there is no server-side backend fetch path (`backendFetch` builds a relative `/api/proxy…` URL and `portal.ts` reads the token from the browser client), and none is introduced. `(portal)/layout.tsx` calls `getMe()` first and fires no other TVA call until it resolves; when `me.account_type === 'crm_only'` it `router.replace('/crm')` and renders nothing else. It shows a CRM link when `me.crm.workspaces.length > 0` (FR-CRM-UI-03). The callback, login page and Google button default `next` to `/` instead of `/calendar` (FR-CRM-UI-04). |
| Static segments | `/crm/account` is a static segment beside `[ws]`, so `crm_workspaces.key` may never be `account` (data-model §3.1 CHECK). |
| Data | `lib/api/crm.ts` (typed `call<T>()` wrappers to `/api/v1/crm/...`) and `lib/api/crm-types.ts` (hand-mirrored from `schemas/crm.py`). Every load uses `lib/use-async.ts`. No Supabase table reads and no Realtime (FR-CRM-ACC-10). |
| Formatting | `lib/crm/format.ts`: `formatDate` gives "14 Oct 2026" in the viewer's time zone (`Intl.DateTimeFormat('en-GB', {day:'numeric', month:'short', year:'numeric', timeZone})`); `formatAmount(value, currency, numberFormat)` gives INR as "12 L" / "1.2 Cr" (`indian_short`) or `en-IN` grouping (`full`), and AUD/USD with Western grouping (FR-CRM-FX-06, -UI-07). Amounts stay strings until display. |
| Primitives | shadcn base-nova `popover`, `select`, `dropdown-menu`, `checkbox`, `tooltip`, `sheet` are added under `components/ui/` on `@base-ui/react` (already a dependency). CRM components go in `components/crm/`. |

### 5.10 MCP — `app/mcp/crm.py`

- Each tool is one `await _api(ctx, …)` to a `/crm/...` route, with no checks
  of its own (`004` §3). A tool with a `mode` or `preview` argument picks one
  route by it, still one call. What would otherwise need a second call lives
  in the route: `POST /crm/{object}?check_duplicates=true` answers the
  possible duplicates without creating (`crm_create`); `PATCH` accepts a
  missing `expect_updated_at` from the caller, which then skips the stale
  check, and `crm_update` passes the `updated_at` of the model's own earlier
  `crm_get` when it has one, never one fetched just before the write
  (fetching it would defeat the check); `POST /crm/query` accepts `view` by
  name or `system_key` (`crm_list`). `workspace: str | None = None` on every
  tool; the route applies D13.
- `app/mcp/crm.py` imports only from `app.mcp.server` (`mcp`, `_api`, `READ`,
  `WRITE`, `DESTRUCTIVE`), never the other way at top level. `server.py` ends
  with `from app.mcp import crm as _crm  # noqa: E402, F401`, after `mcp`,
  `_api` and the annotations are defined (ruff selects `E4` and `F`).
- Docstrings open with who may call, in capitals, from FR-CRM-MCP-11. Every
  write says **CONFIRM FIRST**. Annotations: reads `READ`; writes `WRITE`;
  `crm_merge` and `crm_move_stage` `DESTRUCTIVE` (a Lost move archives a
  project).
- `INSTRUCTIONS` gain a CRM paragraph (FR-CRM-MCP-09): workspaces, roles,
  amounts in INR/AUD/USD with `amount_in_inr`, stage keys, "dates are
  YYYY-MM-DD; CRM timestamps carry their offset", and Viewers receive no
  amounts.
- `test_mcp.py::TestCoverage.test_every_operation_has_a_tool` today asserts
  `len(_tools()) == len(_operations() - EXCLUDED)`. Dropping CRM paths from
  `_operations()` alone would fail the moment `crm_workspaces` is registered,
  so both sides are filtered: the expected count compares
  `[t for t in _tools() if not t.name.startswith("crm_")]` with
  `{op for op in _operations() - EXCLUDED if not op[1].startswith("/api/v1/crm/")}`
  (FR-CRM-CHG-07). `("GET", "/health/jobs")` joins `EXCLUDED` in S9. A new
  `test_crm_mcp.py` asserts that exactly the 18 named tools exist, each with a
  capitals prefix from FR-CRM-MCP-11 and `CONFIRM FIRST` on writes.

### 5.11 Background work (1b, and the 1a purge)

| Job | Task name | Schedule (beat, IST) | Queue | Notes |
|---|---|---|---|---|
| Trash purge (S3) | `crm.purge_trash` | 01:10 daily | default | `crm_purge` per record older than 90 days, run **per workspace** (one `crm_job_runs` row each) |
| Follow-up flags | `crm.recompute_flags` | every 30 min | default | Also recomputed on change in the write path |
| Digest tick | `crm.digest_tick` | every 15 min | default | Sends the daily digest (and the weekly one on Mondays) to each person whose local time is in [09:00, 09:15), once, guarded by `crm_digest_sends`. The daily digest is one message with one section per workspace the person is **currently** a member of, each built and redacted with the person's role in that workspace (a Viewer in A and Member in B gets A's section with no amounts). The weekly admin digest is per workspace. The job run row has `workspace_id NULL`, so it carries `error_public` only (FR-CRM-JOB-01) |
| Reminders | inside `crm.digest_tick` | — | default | Task due today → in-app; overdue → message, once per task per day (unique notification key) |
| Stale-deal notice | inside `crm.recompute_flags` | — | default | Once per deal per level change |
| Email sync tick | `crm.email_sync_tick` | every 15 min | default | Checks the workspace switch and the non-secret `CRM_GMAIL_CONFIGURED` flag (never the key, which this worker does not hold). Takes a Redis `SET NX EX 900` single-flight lock, then enqueues page tasks onto `crm_sync` |
| Email page | `crm.email_sync_page` | enqueued | `crm_sync` | One page of one mailbox; `soft_time_limit = 240`, `time_limit = 300`; resumes from the stored cursor |

Every run writes `crm_job_runs` through the `@crm_job(name)` decorator in
`app/tasks/crm.py`. After three consecutive `failed` runs of one job in one
workspace, it alerts once per streak (FR-CRM-JOB-02). Staleness is checked on
read: `GET /crm/bootstrap` (one call per CRM page load) and `crm_today` return
`stale_jobs` to admins. `GET /health/jobs` reports the same without record
content (FR-CRM-JOB-03).

`notify.deliver(Message(...))` is reused unchanged for out-of-app messages
(Slack DM, email fallback). A run counts recipients for whom `deliver`
returned no channel as `undeliverable` (FR-CRM-NOTIF-02, Q-73).

Rows of `crm_job_runs` with `workspace_id NULL` are shown to nobody with
their `error`, only with `error_public`; `GET /crm/jobs` and `stale_jobs` list
only the workspace's own rows. The in-app bell lists and counts only
notifications of workspaces the person is a member of **now** (a join on
`crm_memberships`), and `crm_remove_membership` deletes that person's
notifications for the workspace, so a removed member keeps no titles that name
the workspace's records.

### 5.12 Proxy — FR-CRM-CHG-08 (S8)

`app/api/proxy/[...path]/route.ts`:

- Forward only `Authorization` and the request `Content-Type`, and that only
  when it is `application/json`, `multipart/form-data` (with its boundary) or
  `text/csv`; any other type is refused 415. Never forward `Cookie`,
  `X-Portal-*` or `X-Forwarded-*`. The body goes as bytes rather than
  `text()` when it is not JSON.
- Read the body as a stream with a running byte count and refuse with a 413
  RFC 7807 body the moment it passes 4 MB, whatever `Content-Length` says (it
  is absent with chunked encoding and can be wrong).
- Call the backend with `redirect: 'manual'`. From the response copy only the
  status, `Content-Type` and `Content-Disposition`; never `Set-Cookie`,
  `Location` or anything else. Non-JSON responses are returned as a streamed
  `Response`. JSON keeps today's behaviour.
- Netlify caps a function response at about 6 MB, whatever the streaming.
  Exports are therefore capped at 5 MB in the backend: `POST
  /crm/export/view` and `GET /crm/export/workspace` answer 413 "This export
  is larger than 5 MB; export one object, or a narrower view." past it, and
  the workspace export takes an optional `object` to export one object at a
  time (contracts §2.10).

This also fixes the effort-statement CSV (`006` FR-STMT-02). The backend
enforces the same 4 MB cap (`imports.py`). A Playwright test uploads a 3.9 MB
CSV and checks a 4.1 MB file is refused, which confirms the Netlify limit
(Q-05). It also sends an oversize chunked upload with no `Content-Length`
(413) and checks that a backend `Set-Cookie` or `Location` never reaches the
browser.

CSV exports neutralise formula injection: a cell whose text starts with `=`,
`+`, `-`, `@`, a tab or a carriage return is prefixed with `'` (OWASP CSV
injection rule), because exported values include names that Gmail sync takes
from outside senders.

### 5.13 Account administration rules (S1b, S1c, S10)

| Rule | How |
|---|---|
| Allow-list edits (FR-CRM-ACC-06, amended) | A workspace admin must not widen the list that limits whom they may invite, and the same list is "our domains" for Gmail sync. Only a TVA admin edits it: `PUT /crm/tva-admin/workspaces/{id}/allowed-domains`. It refuses public webmail domains (`gmail.com`, `googlemail.com`, `outlook.com`, `hotmail.com`, `yahoo.com`, `icloud.com`, `proton.me`, `protonmail.com`; a constant in `domain/crm/invites.py`). Workspace admins see it read-only in Settings. |
| Reactivation (FR-CRM-ACC-04, amended) | `profiles.deactivated_reason` records why an account is inactive: `crm_no_membership` (set by `crm_remove_membership`) or `admin` (set by `admin.update_user` when a TVA admin deactivates). A workspace-admin invitation reactivates only `crm_no_membership`; any other inactive account is 422 "This person's portal account is inactive; ask a TVA admin." Reactivation clears the reason. |
| Invitation write | `crm_invite` (data-model §5.13) does the profile, membership and audit rows in one transaction after the GoTrue step (CT-5). |
| At least one admin | `crm_apply` (on a `crm_memberships` role change) and `crm_remove_membership` lock the workspace's admin memberships `FOR UPDATE` and refuse `CRM22` "A workspace needs at least one admin." when the change would leave none, for `PATCH` and `DELETE` alike. |
| Protected memberships | Only a TVA admin may change or remove the membership of an `is_owner` profile or a TVA-admin profile; a workspace admin gets 403 "Ask a TVA admin to change this membership." |
| Mailboxes (FR-CRM-SYNC-01, amended) | A mailbox's address must be its owner's own profile email, unless the caller is a TVA admin (who may register a delegate address such as `navaneeth@deepweaver.ai`). Its domain must be one of `GMAIL_DOMAINS`. Labels must not be Gmail system labels (`INBOX`, `SENT`, `DRAFT`, `SPAM`, `TRASH`, `STARRED`, `IMPORTANT`, `UNREAD`, `CHAT`, any `CATEGORY_*`), by id or name, case-insensitive. A mailbox address is registered in at most one workspace (`UNIQUE (email)`), so one workspace's labelled mail never lands in the other. |

---

## 6. How each spec area maps onto existing TVA code

| Spec area | Existing code reused | Change |
|---|---|---|
| §3, §5.2 accounts, roles, invitations | `deps._verify`, `verify_personal_token`, `admin.create_user` pre-create pattern, `notify.deliver`, Google sign-in, `/auth/callback` | `account_type`; split `current_user`; `services/crm/members.py` invite (pre-create + compensation); callback default `next`; the `signup_disabled` message is unchanged |
| §5.3 audit | `audit_log`, its 005 append-only triggers, `audit.record`, `domain/audit.actor_name` | New columns; `crm_audit_row()` trigger; `GET /admin/audit` gains `WHERE workspace_id IS NULL` (so the `audit_log` MCP tool does too) |
| §5.4 lists, settings | `app_settings` pattern of seeding by migration | New `crm_select_options`, `crm_workspace_settings` (per workspace, edited by CRM admins) |
| §5.12 pipeline | `domain/rules.py` ordered-checks style, `ALLOWED_TRANSITIONS` | `domain/crm/pipeline.py`; `crm_deal_transition` |
| §5.20 TVA integration | `projects.status/probability` (020), `is_archived`, `project_phases`, `project_milestones`, `lead_id`, `rules.make_tentative_refusal`, `rules.project_lead_refusal`, `CurrentUser.is_manager`, the "Only a manager or admin can set revenue" rule | New link and release columns; TVA writes inside the transition function; `update_project` refusal |
| §5.21 capacity | `domain/timesheets.available_hours`, `services/pnl.contracted_hours`, `services/holidays.holidays_by_person`, `services/timesheets.leave_days_for` | New `services/capacity.py`; `analytics.forecast` reads it |
| §5.16 views | `@tanstack/react-table` (installed) | `crm_query`, `components/crm/data-table.tsx`, `kanban.tsx` (dnd-kit) |
| §5.23 MCP | `_api`, `READ/WRITE/DESTRUCTIVE`, docstring convention, `test_mcp.py` coverage | `mcp/crm.py`; prefix exclusion; INSTRUCTIONS |
| §6.3–6.5 jobs, notifications | `worker.py` beat, `tasks/ops.py` thin tasks, "run often, fire at the hour" (`ops.nudge_today`), `dispatch.fire_and_forget`, `notify.deliver`, pass ids not content to Celery | `crm_job_runs`, `crm_notifications`, `crm_digest_sends`; 15-minute tick |
| §6.8 CSV | `services/statements.py` CSV writer | Proxy widened; backend `csv` stdlib parser |
| Help | `docs/guides/NN-*.md` → `scripts/build-guides.mjs` → `/help` | `09-crm.md`, `10-crm-admin.md` (public: no names, no figures) |

---

## 7. Project structure

### Documentation (this feature)

```text
specs/007-crm/
├── source.md        # owner's original (behaviour only)
├── spec.md          # what, operative
├── plan.md          # this file
├── data-model.md    # tables, columns, functions, ALTERs
├── contracts/api.md # HTTP routes + 18 MCP tools
├── quickstart.md    # run locally, verify
└── tasks.md         # build slices S1a–S12
```

### Source code

```text
supabase/migrations/025_…041_*.sql
backend/app/
├── api/deps.py                      (split: _authenticate / current_user / any_account)
├── api/crm/…                        (new)
├── domain/crm/…, domain/capacity.py (new)
├── services/crm/…, services/capacity.py (new)
├── services/supabase.py             (list_profiles default, list_counting_allocations, release helpers)
├── schemas/crm.py                   (new)
├── tasks/crm.py                     (new)
├── mcp/crm.py                       (new) ; mcp/server.py (import + INSTRUCTIONS)
└── worker.py, config.py, entrypoint.sh
backend/tests/
├── test_crm_*.py, crm_fake.py       (route and domain tests, FakeDb style)
└── db/test_crm_*.py                 (@pytest.mark.db, local Supabase)
backend/scripts/simulations/crm_load_10k.py
frontend/
├── app/(crm)/crm/…                  (new area)
├── app/(portal)/layout.tsx, app/page.tsx, app/auth/*, lib/supabase/middleware.ts
├── app/api/proxy/[...path]/route.ts
├── components/crm/…, components/ui/{popover,select,dropdown-menu,checkbox,tooltip,sheet}.tsx
└── lib/api/crm.ts, lib/api/crm-types.ts, lib/crm/format.ts
docs/architecture/crm.md, docs/guides/09-crm.md, docs/guides/10-crm-admin.md, docs/operations/deployment.md
```

**Structure decision.** The existing two-app layout (`backend/`,
`frontend/`) is kept. The CRM is a package inside each layer, never a third
app, so "each layer independently deployable" (§IV) is unchanged.

---

## 8. Testing strategy (NFR-CRM-10)

| Kind | Where | What |
|---|---|---|
| Domain | `tests/test_crm_pipeline_rules.py`, `…_fx.py`, `…_permissions.py`, `…_views_spec.py`, `…_handoff.py`, `…_importer_mapping.py`, `…_flags.py` (fixed `NOW`), `…_digests.py`, `test_capacity.py` | Every rule in §5.12–5.14, 5.16–5.18, 5.20, 6.2, 6.4–6.6, as pure functions, with classes per spec section and plain-sentence test names |
| Route | `tests/test_crm_*_routes.py` with `tests/crm_fake.py` (`FakeCrmStore` implementing the store's public names, including in-Python fakes of the RPCs) and `dependency_overrides` | Guards, D13, 404 on foreign ids, Viewer redaction on every read route, refusal sentences |
| Enumeration | `tests/test_crm_only_refused.py` | Walks `app.routes` (every `APIRoute` and starlette `Route`, including `include_in_schema=False` routes such as the OAuth consent routes, `/feed/{key}.ics`, `/mcp` and the OAuth AS routes appended to `app.router.routes`), never `app.openapi()`, which misses them. Each route outside `/api/v1/crm/` is classified as `SHARED_ALLOW_LIST`, `UNAUTHENTICATED` (both constants) or refused, and every refused one answers 403/404 to a `crm_only` caller (FR-CRM-CHG-01). An unclassified new route fails the test. |
| Isolation | `tests/test_crm_isolation.py` | Two workspaces, one caller in A; every route and every `crm_*` tool with B's ids → 404; search, views, Today, export, notifications and digests contain nothing of B |
| Redaction | `tests/test_crm_redaction.py` | Every read route, every tool, search, kanban, footers, timeline, notifications, digests and export as a Viewer: no amount key anywhere in the JSON (a recursive key walk); amount filter/sort/group → 422 |
| Database | `tests/db/` (`@pytest.mark.db`, skipped unless `CRM_DB_TESTS=1`) | Each plpgsql function's success, rollback and race paths; the audit trigger writes, and refuses a write with no actor; `crm_apply` refuses a foreign workspace id and a cross-workspace reference; the transition functions refuse foreign ids; `EXECUTE` is denied to `anon` and `authenticated`; RLS denies browser roles; a `crm_only` JWT gets a permission error from every table in `public`, TVA ones included |
| CI | `.github/workflows/ci.yml` new job `db` | `supabase/setup-cli` → `supabase start` → `supabase db reset` → `uv run pytest -m db` (new CI dependency, Q-68) |
| E2E | `frontend/e2e/crm.spec.ts`; `supabase/seed.sql` gains `crm_only` accounts, their memberships and the owner's two admin memberships (local only; `db push` never seeds; migration 026's owner seed finds no `is_owner` profile on a fresh `db reset`, because migrations run before `seed.sql`) | Sign-in landing, refusal of TVA pages, a deal Discovery→Won, upload limit |
| Performance | `backend/scripts/simulations/crm_load_10k.py` | Seeds 10k companies, people and deals in local Supabase and times list, kanban, search and record page through the API. p95 < 1 s (S12). |

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| PostgREST `rpc` statement timeout on big imports | The importer applies in chunks of 500 rows per `crm_apply` call; idempotent by `twenty_id`, so a failed chunk re-runs safely |
| Audit growth from sync timestamp updates | Sync writes `last_*` only when the value moves forward; email messages themselves are not audited (§5.3) |
| Twenty's export shape is unknown (clean room) | The importer reads a declared, documented shape (contracts §4) with column aliases. Fixtures are built from that shape. The real run waits for the owner's files (FR-CRM-MIG-08). |
| `supabase/config.toml` local change committed by accident | Every slice's "done when" includes `git diff --cached --name-only` not containing it (D12) |
| Celery worker on an old commit drops new `crm.*` tasks | deployment.md: migrate, then deploy api, worker, beat (and `worker-crm-sync`) from one commit, as today |
| CRM-only invitee has no Google account | FR-CRM-ACC-07: the invite screen says so; nothing else is built |
| A whole-workspace JSON export passes Netlify's ~6 MB response limit at 10k rows | Exports cap at 5 MB with a sentence; the workspace export can be taken one object at a time (§5.12) |
| A deal amount is saved without its INR value recomputed (import upsert, bulk set, merge) | `amount_in_inr = round(amount * fx_rate_used, 2)` is a CHECK (data-model §3.9), so a wrong stored value cannot exist |
