# Sales CRM — Tasks

| | |
|---|---|
| **Feature** | `007-crm` |
| **Input** | [`spec.md`](spec.md), [`plan.md`](plan.md), [`data-model.md`](data-model.md), [`contracts/api.md`](contracts/api.md), [`quickstart.md`](quickstart.md) |
| **Shape** | Seventeen sequential build slices: S1a–S1c, S2–S5, S6a–S6b, S7–S10, S11a–S11b, S12. One engineer-agent can build and test each slice in one go. Each slice leaves `main`-quality software: every gate green, nothing half-wired. |
| **Scope** | All of Phase 1a (S1a–S8) and Phase 1b (S9–S11b), then hardening (S12) |

## How to use this file

- Build the slices **in order**. Each one lists what it depends on, and none
  depends on a later one. Split slices share their quickstart section: S1a–S1c
  use quickstart §3 S1, S6a–S6b use §3 S6, S11a–S11b use §3 S11, each checking
  the part it built.
- Migrations are numbered in build order, `025`–`043`. A slice never edits an
  earlier slice's migration once it has been applied anywhere. It adds a new
  file, or `CREATE OR REPLACE`s a function in its own file.
- Tests are named `test_<area>_<rule-in-plain-words>` inside classes per spec
  section, as `test_booking_rules.py` does. Every test module's docstring
  cites the FR ids it covers. Each slice lists its tests, mapped to FR ids
  and to the acceptance criteria (AC) of spec §11.
- `tests/crm_fake.py` (created in S1b, extended by every slice) is the
  in-memory store. It implements `services/crm/store.py`'s public names,
  including Python fakes of the RPC functions, so route tests never touch a
  database, as today. `tests/db/` holds real-database tests
  (`@pytest.mark.db`, skipped unless `CRM_DB_TESTS=1`), run in CI by the `db`
  job that S1b adds.
- Clean room (NFR-CRM-09): do not open, copy or translate Twenty's code,
  schema, migrations or UI, and do not consult the sibling `rocket-*` repos.
  The Twenty importer is built from the input shape declared in
  `contracts/api.md` §2.9.

### Gates — every slice's "done when" includes all of these

- [ ] `cd backend && uv run pytest -q` passes (the whole suite, not only the new tests)
- [ ] `cd backend && uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `cd backend && CRM_DB_TESTS=1 uv run pytest -m db -q` passes after `npx supabase db reset` (from S1b on, when the `db` CI job exists; S1a runs its db tests locally)
- [ ] `cd frontend && pnpm build` passes (strict TypeScript) and `pnpm lint` passes
- [ ] `npx supabase db reset` applies every migration from empty without error
- [ ] The slice's checks in `quickstart.md` §3 work by hand
- [ ] `git diff --cached --name-only` does not contain `supabase/config.toml` (D12)
- [ ] No file outside the slice's lists changed, except test fakes whose signatures the slice changed
- [ ] Commit message says what changed, why, and which journey it serves (constitution, Development Workflow)

---

## S1a — CRM-only accounts: the refusal, the exclusion, browser lockdown

**Goal.** A `crm_only` profile is refused by every TVA route and tool, left
out of every TVA people population, and can read no table through PostgREST.
No CRM table exists yet; this slice changes TVA only, so it can ship and be
verified on its own.

**Depends on.** Nothing.

**Spec.** FR-CRM-ACC-01, -03; FR-CRM-CHG-01, -02, -12 (columns only).

**Migrations.** `025_crm_accounts.sql`: data-model §4.0, §4.1, §5.10.

**Backend — modify.**
- `app/api/deps.py`: split into `_authenticate` (today's body, plus `request.state.channel`, set to `"mcp"` only when `X-Portal-Channel-Key` equals `app.mcp.server.CHANNEL_KEY` by `hmac.compare_digest`, else `"web"`; plan §5.1), `current_user` (refuses `crm_only`: 403 "This account is for the CRM only."), `any_account`; add `AnyAccountDep`, `AnySessionDep`.
- `app/mcp/server.py`: `CHANNEL_KEY = secrets.token_urlsafe(32)` at import; `_api` sends it as `X-Portal-Channel-Key`.
- `app/api/me.py`: `whoami` on `AnyAccountDep`; reduced shape for `crm_only` (`crm.workspaces` is `[]` until S1b); `account_type` for everyone.
- `app/api/tokens.py`: `SessionDep` → `AnySessionDep` (three routes).
- `app/api/ops.py`: `oauth_transaction`, `oauth_approve` → `AnySessionDep`. `/me/feed` routes stay on `SessionDep`.
- `app/api/slack.py`: `_resolve_actor` refuses a `crm_only` profile ("This account is for the CRM only.") before acting.
- `app/services/feeds.py`: `resolve` returns None for a `crm_only` profile.
- `app/api/admin.py`: `list_users` uses `include_crm_only=True` and returns `account_type`; `read_audit` resolves names with `include_crm_only=True`; `update_user` refuses `role`, `lead_id`, `logs_time` and `dashboard_access = true` on a `crm_only` profile (422 "Change the account type first.") and sets `deactivated_reason = 'admin'` when it sets `is_active = false` (cleared when it sets `true`); `create_project` / `update_project` use the widened `project_lead_refusal`; `create_allocation` refuses a `crm_only` person.
- `app/domain/timesheets.py`: `project_lead_refusal` refuses `crm_only` ("A CRM-only account cannot lead a project."); new `allocation_person_refusal` ("A CRM-only account cannot be allocated.").
- `app/services/supabase.py`: `list_profiles(*, active_only=False, include_crm_only=False)`; `list_reports` also excludes `crm_only`.
- **TVA people populations to verify (FR-CRM-CHG-02).** The default
  exclusion covers each of these; each gets a test, and none needs code
  unless the test shows otherwise:
  - `api/team.py:42` (team day)
  - `api/timesheet.py:111` (timesheet population) and `:268` (`/analytics/people`, the allocatable picker)
  - `services/utilisation.py:39`, `:99`, `:172` (utilisation, bench, hiring supply)
  - `services/analytics.py:140` (coverage), `:560` (people financials, CTC-missing), `:623` (resources)
  - `services/pnl.py:140`, `:430`
  - `services/dashboard.py:58` (CEO headcount)
  - `services/digest.py:56` (nothing-logged nudge), `:92` (weekly gaps), `:134`, `:157` (morning who-is-out post), `:187` (coverage), `:223` (leadership digest)
  - `services/confirmations.py:117` (sign-off)
  - `tasks/notifications.py:161` (approvers)
  - `services/feeds.py:36`, `:49`
  - `services/holidays.py:35`, `services/bookings.py:670` (location lookups)
- **Name-map callers, each kept on the default by decision (plan §5.7)**, each
  pinned by a test: `admin.py:893` (the `list_projects` lead-name map passed
  to `_present_project`), `admin.py:728` (backfills), `admin.py:1072`
  (allocations), `ops.py:50` (comp-off names), `timesheet.py:131` (project
  lead names), `analytics.py:54`, `:220`, `:325`, `:516`, `statements.py:25`,
  `checklists.py:79`. `admin.py:830` (`read_audit`) passes
  `include_crm_only=True`.
- `tests/test_api_permissions.py`, `tests/test_pipeline.py` and every other
  FakeDb: `list_profiles` gains the `include_crm_only` keyword (these fakes
  patch only the names `FakeDb` defines, so a missing keyword or method calls
  the real Supabase client).

**Frontend — modify.**
- `lib/api/types.ts` (`Me.account_type`, `Me.crm`)

(The `(portal)/layout.tsx` redirect of `crm_only` waits for S1c, when `/crm` exists.)

**MCP.** None.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_accounts.py` | `crm_only` refused by `current_user`, allowed by `any_account`; `/me` reduced shape has no role, lead or capabilities; `update_user` refuses role, lead, `logs_time` and `dashboard_access` on `crm_only`, and records `deactivated_reason = 'admin'` on deactivation; the Slack interaction handler refuses a `crm_only` actor; `channel` is `mcp` only with the right key, and a bare `X-Portal-Client: mcp` stays `web` (FR-CRM-ACC-01, CHG-01) |
| `tests/test_crm_only_refused.py` | Walks `app.routes` (every `APIRoute` and `Route`, including `include_in_schema=False` ones and the routes appended to `app.router.routes`), never `app.openapi()`. Each route outside `/api/v1/crm/` is classified as `SHARED_ALLOW_LIST`, `UNAUTHENTICATED` (constants) or refused; an unclassified route fails the test; every refused one answers 403/404 to a `crm_only` caller; the allow-list is exactly contracts §1 (FR-CRM-CHG-01; **AC** "A CRM-only account sees only the CRM") |
| `tests/test_crm_only_excluded.py` | One test per population in the list above; one per name-map caller pinning its decision; lead and allocation refusals with the spec's sentences; the feed never resolves for `crm_only` (FR-CRM-CHG-02; **AC** "appears in no TVA people list, report, reminder or job") |
| `tests/test_crm_audit_routes.py` (create) | `read_audit` names a `crm_only` actor (FR-CRM-AUD-04, name half) |
| `tests/db/conftest.py`, `tests/db/test_crm_browser_lockdown_db.py` | A JWT signed for a `crm_only` profile, with the anon key, gets a permission error or zero rows from **every** table in `public` (enumerated from `information_schema.tables`), TVA tables included; `tva_staff_only` fires on all four columns |

The `db` CI job arrives in S1b; until then the db tests run locally
(`CRM_DB_TESTS=1`), and the gate for them applies from S1b.

**Done when.**
- [ ] All gates (the db gate locally)
- [ ] A seeded `crm_only` profile reaches no TVA route or tool, by test and by hand with a personal token

---

## S1b — Workspaces, roles, the audited write path, the CRM guard

**Goal.** Workspaces, memberships, roles, settings, select lists, FX rates and
the audited write path exist, with the CRM guard and D13 in front of them.
The `db` CI job runs.

**Depends on.** S1a.

**Spec.** FR-CRM-WS-01 to -04; FR-CRM-ACC-02, -10, -11 (mechanism); FR-CRM-AUD-01 to -04 and -06 (mechanism); FR-CRM-LIST-01 to -06 (probabilities, FX; the allow-list read-only), -09; FR-CRM-FX-02; FR-CRM-CHG-07 (exclusion only), -09, -15; FR-CRM-MCP-03, -04, -09 (first paragraph).

**Migrations.**
- `026_crm_workspaces.sql`: data-model §3.1–3.5, §4.2, §5.1, §7.1–7.4.
- `027_crm_write_functions.sql`: data-model §5.2, §5.3, §5.12–§5.15.

**Backend — create.**
- `app/api/crm/__init__.py` (`ROUTERS`), `app/api/crm/deps.py` (`CrmContext`; D13 resolution; `CrmReadDep`, `CrmWriteDep`, `CrmAdminDep`, `CrmLabsDep`)
- `app/api/crm/workspaces.py` (`/crm/workspaces`, `/use`, `/bootstrap`, `/me/preferences`)
- `app/api/crm/settings.py` (settings, lists, FX rates; the allow-list read-only)
- `app/api/crm/audit.py` (`/crm/audit`)
- `app/domain/crm/__init__.py`, `permissions.py` (the §3.3 matrix as one table), `settings.py` (per-key validation)
- `app/services/crm/__init__.py`, `store.py` (reads with `workspace_id` first; `rpc()` with error mapping `CRM04/09/22`, `23505`), `context.py` (actor dict from `CrmContext`)
- `app/schemas/crm.py`
- `app/mcp/crm.py` with `crm_workspaces` (imports only from `app.mcp.server`)

**Backend — modify.**
- `app/api/me.py`: `crm.workspaces` filled.
- `app/api/admin.py`: `read_audit` uses `list_audit(tva_only=True)`; its presenter shows a `system` row with an `actor_id` as "*Name* (via CRM deal)".
- `app/domain/audit.py`: `actor_name` takes `actor_label`.
- `app/services/supabase.py`: `list_audit(limit, *, tva_only=True)` filters `.is_("workspace_id", "null")`.
- `app/services/audit.py`: `record(..., workspace_id=None, actor_label=None, channel=None)`.
- `app/main.py`: mount `app.api.crm.ROUTERS`.
- `app/mcp/server.py`: last line `from app.mcp import crm as _crm  # noqa: E402, F401`, after `mcp`, `_api` and the annotations; INSTRUCTIONS gain the CRM paragraph (workspaces, roles, D13, keys, Viewers get no amounts, CONFIRM FIRST on every `crm_*` write).
- `tests/test_mcp.py`: `test_every_operation_has_a_tool` compares `[t for t in _tools() if not t.name.startswith("crm_")]` with `{op for op in _operations() - EXCLUDED if not op[1].startswith("/api/v1/crm/")}` (plan §5.10). Filtering only `_operations()` would fail as soon as `crm_workspaces` registers.
- `.github/workflows/ci.yml`: new `db` job (`supabase/setup-cli@v1`, `supabase start`, `supabase db reset`, `uv run pytest -m db -q` with `CRM_DB_TESTS=1` and the local URL and key from `supabase status -o env`).
- `backend/pyproject.toml`: register the `db` pytest marker.

**Frontend — create.**
- `lib/api/crm.ts`, `lib/api/crm-types.ts`, `lib/crm/format.ts` (`formatDate` "14 Oct 2026" in the viewer's zone, `formatAmount`)

**MCP.** `crm_workspaces`.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_permissions.py` | Every cell of spec §3.3, from `domain/crm/permissions.py` |
| `tests/test_crm_guard.py` | `GET` without `workspace` uses last-used; a write without it from a two-workspace member is 422 listing both; non-member is 404; `use` updates last-used (FR-CRM-WS-02, -04, MCP-03, -04; **AC** "A write by a two-workspace member naming no workspace is refused") |
| `tests/test_crm_settings_lists.py` | Fixed lists rename and recolour only; system keys cannot be deactivated; one default per list; probabilities 0–100 for all six keys; FX rate positive and month-shaped; the allow-list is read-only for a workspace admin; **the seeded options per workspace**: Labs and Academy channels, account types and task categories exactly as FR-CRM-LIST-03 and -04; Institution type and Programme exist only in Academy; Delivery handoff only in Labs; the common lists of FR-CRM-LIST-05 in both; Industry and Programme empty (FR-CRM-LIST-01 to -06, -09, FX-02) |
| `tests/test_crm_audit_routes.py` (extend) | `/admin/audit` and the `audit_log` tool exclude workspace rows; a D8 `system` row reads "*Name* (via CRM deal)"; `/crm/audit` is admin-only, filtered, workspace-scoped (FR-CRM-AUD-03, -04) |
| `tests/test_audit.py` (extend) | the new `record` keyword arguments are written |
| `tests/test_crm_mcp.py` | `crm_workspaces` exists, starts with its capitals line, READ (FR-CRM-MCP-05, -11) |
| `tests/test_mcp.py` (amended) | TVA tools still equal TVA operations with `crm_workspaces` registered |
| `tests/db/test_crm_audit_db.py` | A `crm_*` insert without `crm.actor` is refused; `crm_apply` writes `audit_log` with workspace, actor kind, channel and a uuid `actor_id`; an update of `crm_workspaces.allowed_domains` through `crm_apply` is audited with `workspace_id` = the workspace's id (the trigger has no `NEW.workspace_id` there); a `last_used_at`-only update without an actor is allowed and writes no audit row; a foreign `workspace_id` → `CRM04`; a non-`crm_` table → `CRM22`; seeds are audited as system; `crm_remove_membership` deactivates (`deactivated_reason = 'crm_no_membership'`) and revokes in one transaction, and refuses the last admin under concurrent demotion; `crm_apply` refuses a role change that leaves no admin; `crm_invite` reactivates only `crm_no_membership` for a non-TVA-admin actor; `crm_convert_account` refuses the owner, clears `dashboard_access` with the rest, and refuses when an allocation commits during the change; `anon` and `authenticated` cannot `EXECUTE` any CRM function or `SELECT` any CRM table (FR-CRM-AUD-06, ACC-10, -12) |

**Done when.**
- [ ] All gates
- [ ] The `db` CI job runs and passes
- [ ] Every new table has RLS on and no browser grant (checked by the db test)

---

## S1c — Invitations, the TVA admin panel, the `/crm` shell

**Goal.** A CRM-only person can be invited, sign in with Google, land on
`/crm`, and see nothing of TVA. A staff member with a membership sees a CRM
link. TVA admins administer workspaces without joining them.

**Depends on.** S1b.

**Spec.** FR-CRM-ACC-04 to -09, -12 to -15; FR-CRM-UI-01 to -05, -07; FR-CRM-CHG-11, -14.

**Migrations.** None.

**Backend — create.**
- `app/api/crm/members.py` (members, invitations; last-admin and protected-membership refusals come from the database functions)
- `app/api/crm/tva_admin.py` (`/crm/tva-admin/*` except jobs: workspaces, members, invitations, memberships, `allowed-domains` with the public-webmail refusal, `accounts/{user_id}` through `crm_convert_account`, `audit`)
- `app/domain/crm/invites.py` (allow-list rule, who may invite where, default time zone by domain, public webmail domains)
- `app/services/crm/members.py` (invite: GoTrue pre-create, then `crm_invite`; compensation deletes the auth user only when `created_account` is true; removal through `crm_remove_membership`, then `forget_personal_tokens`)

**Backend — modify.** `app/api/crm/__init__.py` (members and TVA-admin routers).

**Frontend — create.**
- `app/(crm)/crm/page.tsx` (redirect to last-used `/crm/<key>`)
- `app/(crm)/crm/[ws]/layout.tsx` (shell from `/crm/bootstrap`)
- `app/(crm)/crm/[ws]/page.tsx` (Today heading and the CRM home; filled in S7)
- `app/(crm)/crm/[ws]/settings/page.tsx` (tabs: Members, Lists, Probabilities, FX rates, Allowed domains (read-only), Audit; admins only)
- `app/(crm)/crm/account/page.tsx` (time zone, amount format, personal tokens, connect Claude)
- `components/crm/shell.tsx`, `workspace-switcher.tsx`, `invite-dialog.tsx` (shows the sign-in link, whether a message was sent, and the Google-account note), `members-table.tsx`, `list-editor.tsx` (fixed lists: rename and recolour only), `fx-rates-form.tsx`, `audit-table.tsx`
- `components/portal/crm-workspaces-panel.tsx` (TVA Admin → CRM workspaces: members, invitations, allowed domains, account type, account-administration audit)
- `components/shared/token-manager.tsx` (moved out of `(portal)/account/page.tsx`, used by both account pages)
- `components/ui/dropdown-menu.tsx`, `components/ui/sheet.tsx` (shadcn base-nova on `@base-ui/react`)

**Frontend — modify.**
- `lib/supabase/middleware.ts` (`/crm` in `PROTECTED_ROUTES`)
- `app/auth/callback/route.ts`, `app/auth/login/page.tsx`, `components/portal/google-button.tsx`, `components/portal/password-fallback.tsx` (default `next` `/`)
- `app/help/layout.tsx` ("Open the portal" → `/`)
- `app/(portal)/layout.tsx`: `getMe()` first, no other TVA call until it resolves; `crm_only` → `router.replace('/crm')` and render nothing else; CRM link when a membership exists. `app/page.tsx` stays `redirect('/home')` (plan §5.9: no server-side backend fetch path is introduced).
- `app/(portal)/admin/page.tsx` (CRM workspaces tab; "CRM only" badge in People, left out of counts)
- `app/(portal)/account/page.tsx` (use `token-manager`)
- `e2e/helpers.ts` (`PEOPLE.crmOnly`)
- `supabase/seed.sql`: two `crm_only` people (auth user, identity, profile), the memberships of quickstart §1, **and the owner's two admin memberships**, inside a `DO` block that sets the system actor, after the profiles (local only; migration 026's owner seed finds no profile on a fresh `db reset`, data-model §7.4).

**MCP.** None.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_invitations.py` | New account pre-created as `crm_only` with `logs_time` off and no lead; existing active account gets the membership only; inactive `crm_only` with `deactivated_reason = 'crm_no_membership'` reactivated by a workspace admin, but one deactivated by a TVA admin refused 422 for a workspace admin and reactivated by a TVA admin; inactive staff 422; allow-list 403; workspace admin cannot invite into another workspace or change a TVA role; TVA admin anywhere and may bypass for existing staff; `deepweaver.ai` defaults to Sydney; when `crm_invite` fails, a newly created auth user is deleted and an existing one is not; removing the last membership deactivates and revokes tokens; the last admin cannot be demoted or removed; a workspace admin cannot change or remove the owner's or a TVA admin's membership (FR-CRM-ACC-04 to -09, -12, -15, CHG-14; **AC** "A workspace Admin can invite only into their workspace…") |
| `tests/test_crm_tva_admin.py` | Allowed domains: a workspace admin cannot widen their own allow-list (no route), a TVA admin can, and `gmail.com` is refused; account type: staff → `crm_only` refused for the owner and for someone who leads, leads a project or holds an unreleased allocation, and on success `dashboard_access` is off with the rest; `/crm/tva-admin/audit` shows membership, invitation, account-type and allow-list events with no record content (FR-CRM-ACC-06, -13, -14, CHG-11) |
| `frontend/e2e/crm.spec.ts` | CRM-only lands on `/crm/labs`; `/calendar` redirects to `/crm` with no TVA request fired before the redirect; a staff member sees the CRM link and Back to TVA; an anonymous visitor to `/crm` goes to sign-in; the owner is admin of both workspaces after `db reset` (FR-CRM-UI-03 to -05) |

**Done when.**
- [ ] All gates
- [ ] Tamil (seeded `crm_only`) reaches no TVA page, route or tool, by hand and by `test_crm_only_refused.py`
- [ ] Sanjeev-equivalent (an Academy admin) invites a rep end to end; the rep signs in locally and sees only Academy

---

## S2 — TVA readers: released allocations, archived projects, one capacity service, the capacity check

**Goal.** TVA stops counting released allocations and allocations on archived
projects, and archived tentative projects leave the pipeline. One capacity
service feeds both TVA's forecast and the CRM capacity check. This lands
before any deal can release anything, so the S5 lifecycle only has to set the
markers.

**Depends on.** S1c (the CRM guard for `/crm/capacity`, and the shell for its page).

**Spec.** FR-CRM-CHG-03, -04, -17; FR-CRM-CAP-01 to -03 (1a part: reachable
from Pipeline, which S6b links); D5 reader half.

**Migrations.** `028_allocation_release.sql` (data-model §4.4, without the FK).

**Backend — create.**
- `app/domain/capacity.py` (pure: weekly supply from contracted hours, the holidays that apply to each person, approved leave, `joined_on`/`left_on`; demand split confirmed and tentative; `contributors`)
- `app/services/capacity.py` (`weekly(start, end)`)
- `app/api/crm/capacity.py` (`GET /crm/capacity`; weeks with `contributors < 3` hidden)

**Backend — modify.**
- `app/services/supabase.py`: `list_counting_allocations(*, user_ids=None, project_id=None)` (released excluded; archived projects' allocations excluded); `get_allocation` returns the release columns.
- **Allocation readers — every one switches to `list_counting_allocations` (FR-CRM-CHG-03, -04):**
  - `services/analytics.py:210` `forecast` (and it adds `weeks` from `services/capacity.py`, computed **before** today's early return at `:211`, which returns a dict without `weeks` when there are no allocations; plan §5.7)
  - `services/analytics.py:401` `category_effort` planned hours
  - `services/analytics.py:624` `resources_timeline`
  - `services/utilisation.py:100` `bench`
  - `services/utilisation.py:174` `hiring`
  - `services/pnl.py:159` `monthly` planned cost
  - `services/pnl.py:432` `timeline`
  - `services/health.py:58` planned margin
  - `services/dashboard.py:66` CEO dashboard
  - `services/timesheets.py:112` the Time page's pre-filled projects
  - `services/digest.py:124`, `:190` and `services/home.py:115`, through `forecast` (no code change; tests prove it)
- **Archived tentative projects leave the pipeline:** `services/pnl.py` pipeline block (`~378–393`) and pipeline cells (`~210–212`); `services/dashboard.py` pipeline figures — skip `is_archived` tentative projects. Logged hours and invoices on archived projects are unchanged (`006` FR-MILE-07).
- `app/api/admin.py`: `list_allocations` returns `released: {at, reason} | null` for every row (it keeps using `list_allocations`); `update_allocation` and `remove_allocation` refuse a released row (422, contracts §1.1).
- `app/api/crm/deps.py`: `CrmCapacityDep` (Labs workspace; admin, or member whose account is staff).
- `tests/test_api_permissions.py`, `tests/test_pipeline.py`: their `FakeDb` gains `list_counting_allocations(*, user_ids=None, project_id=None)`, filtering `released_at` and archived project ids. These fixtures patch only the names `FakeDb` defines (`for name in dir(FakeDb)`), so without it the switched readers would call the real Supabase client. Every other FakeDb the suite's readers touch gets the same.
- `app/api/crm/__init__.py`: add the capacity router.

**Frontend — create.**
- `app/(crm)/crm/[ws]/capacity/page.tsx`
- `components/crm/capacity-panel.tsx` (totals only; the hidden-week text "fewer than 3 people"; no person anywhere)

**Frontend — modify.**
- `app/(portal)/analytics/page.tsx`, Forecast tab: a weekly supply / confirmed / tentative table from `weeks`
- `components/portal/projects-panel.tsx`: allocation rows show "Released (deal lost)" with the reason, with no edit or remove buttons
- `lib/api/types.ts`: `Forecast.weeks`, `Allocation.released`

**MCP.** None. The TVA tools `forecast`, `resources`, `bench`, `hiring_signal`, `monthly_pnl` and `ceo_dashboard` change through their routes.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_capacity.py` | Supply per week less per-person holidays and approved leave, zero before joining and after leaving, part-timers by contracted hours; tentative and confirmed demand; released and archived excluded; `contributors`; `analytics.forecast` still returns `weeks` with supply when every allocation is released (the empty-allocations early return) (FR-CRM-CHG-17) |
| `tests/test_allocation_release_readers.py` | One test per reader above: a released allocation and an allocation on an archived project do not count; un-releasing restores the figure exactly (FR-CRM-CHG-03, -04; **AC** "forecast, resources, bench, hiring signal, planned cost and the P&L pipeline no longer count them", data half) |
| `tests/test_pnl.py`, `tests/test_dashboard.py` (extend) | An archived tentative project is out of the P&L pipeline block and the CEO dashboard pipeline; archived confirmed projects keep their logged cost (FR-CRM-CHG-04) |
| `tests/test_crm_capacity_route.py` | Labs admin and staff member 200; Viewer 403; `crm_only` member 403; Academy 404; a week with two contributors is hidden; figures equal `analytics.forecast`'s `weeks` for the same range (FR-CRM-CAP-01 to -03) |
| `tests/test_timesheets.py` (extend) | A released allocation is not offered on the Time page |
| `tests/test_api_permissions.py` (extend) | Editing or removing a released allocation is refused |

**Done when.**
- [ ] All gates
- [ ] Every reader in the list has a test that fails if it goes back to `list_allocations`
- [ ] Forecast and `/crm/capacity` agree on a seeded week (quickstart §3 S2)

---

## S3 — Companies, people, Drive links, duplicates, trash, record pages

**Goal.** Members create and edit companies and people with every field,
attach Drive links, are warned of duplicates and refused on exact ones, and
delete to a Trash that admins restore. Each record has a page with fields,
Files, Related and a timeline built from the audit. The first MCP record tools
work.

**Depends on.** S1c.

**Spec.** FR-CRM-CO-01 to -03 (TVA projects on the company page arrive in S5);
FR-CRM-PER-01; FR-CRM-LIST-07, -08; FR-CRM-LINK-01 to -03; FR-CRM-DUP-01, -02
(web and MCP); FR-CRM-DEL-01, -02 (people choices; deal choices in S4; the TVA
refusal in S5), -04; FR-CRM-PIPE-01; FR-CRM-REC-01 to -04 (record page frame);
FR-CRM-AUD-05; FR-CRM-UI-02 (Companies, People), -06; FR-CRM-MCP-01 (`crm_get`,
`crm_create`, `crm_update`), -06, -07, -10 (companies, people, links).

**Migrations.** `029_crm_companies_people.sql` (data-model §3.6–3.8, §3.14, §5.4
first form, which touches only `crm_drive_links`; `UNIQUE (workspace_id, id)`
and the composite FKs of data-model §2; `crm_check_refs()` attached to
`crm_drive_links` and `crm_companies.channel_ids`; `CREATE EXTENSION IF NOT
EXISTS pg_trgm WITH SCHEMA extensions`).

**Backend — create.**
- `app/domain/crm/objects.py` (field registry: type, required, system-written, amount, Labs-only/Academy-only, sortable)
- `app/domain/crm/dedupe.py` (normalise domains and emails; similarity threshold 0.4; refusal sentences naming the clash)
- `app/services/crm/records.py` (generic create, update, soft delete with `deleted_group`, restore, permanent delete, list; validation from `objects.py`; membership check for owners)
- `app/services/crm/present.py` (presenters per object; `redact(row, object, role)`; timeline from `audit_log` with labels and actor names)
- `app/api/crm/records.py` (contracts §2.5 for companies, people and links; trash; duplicates, including `check_duplicates` on `POST`; `PATCH` with an optional `expect_updated_at`; timeline)
- `app/tasks/crm.py` with `crm.purge_trash` (one pass per workspace)

**Backend — modify.**
- `app/services/crm/store.py`: company, domain, person and link reads; trigram duplicate query; timeline query
- `app/tasks/__init__.py`: import `crm`
- `app/worker.py`: beat `crm-purge-trash` at 01:10 IST
- `app/mcp/crm.py`: `crm_get`, `crm_create`, `crm_update`
- `app/schemas/crm.py`: company, person and link models

**Frontend — create.**
- `app/(crm)/crm/[ws]/companies/page.tsx`, `companies/[id]/page.tsx`
- `app/(crm)/crm/[ws]/people/page.tsx`, `people/[id]/page.tsx`
- `app/(crm)/crm/[ws]/settings/trash/page.tsx` (or a Trash tab)
- `components/crm/record-page.tsx` (header chips and actions; left field panel grouped, inline-editable, empties collapsible; tabs Timeline, Tasks, Notes, Files, Related)
- `components/crm/field-editor.tsx` (one editor per field type of FR-CRM-LIST-07: text, long text with markdown preview, number, currency, date, datetime, boolean, select, multi-select, rating, emails, phones with WhatsApp, links, domain, address, relation, array)
- `components/crm/timeline.tsx` (type filter; actor and channel on every entry)
- `components/crm/drive-links.tsx` (https only; opens in a new tab; no preview)
- `components/crm/related-panel.tsx`, `components/crm/delete-company-dialog.tsx`, `components/crm/duplicate-warning.tsx`
- `components/ui/select.tsx`, `popover.tsx`, `checkbox.tsx`, `tooltip.tsx`

**Frontend — modify.** `components/crm/shell.tsx` (Companies and People in the sidebar); `lib/api/crm.ts`, `lib/api/crm-types.ts`.

**MCP.** `crm_get`, `crm_create`, `crm_update` (companies, people, Drive links).

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_companies_people.py` | Required fields; Entity outside Labs and Institution type outside Academy refused; system-written fields refused 422 through the web and MCP paths; a new company is New; exact domain or email clash 409 naming the record (including one in Trash); similar names returned as warnings (FR-CRM-CO-01 to -03, PER-01, DUP-01, -02, PIPE-01) |
| `tests/test_crm_drive_links.py` | `https://` only; no content accepted; on every record type that exists so far (FR-CRM-LINK-01 to -03) |
| `tests/test_crm_trash.py` | Member deletes own or created records only; admin restore brings back the `deleted_group`; deleted records leave lists and MCP answers; company delete asks about people (unlink or delete); purge after 90 days by the job; permanent delete admin-only (FR-CRM-DEL-01, -02, -04) |
| `tests/test_crm_timeline.py` | Create, field change, delete and restore entries with actor and channel; Viewer sees no amount values (structure test, ready for S4's amounts) (FR-CRM-REC-03, -04, AUD-05) |
| `tests/test_crm_isolation.py` (create) | Every company, person and link route with an id from the other workspace → 404; lists contain nothing of it (FR-CRM-WS-02; **AC** "another workspace's record id is answered 404") |
| `tests/test_crm_mcp.py` (extend) | The three tools exist with their capitals lines; each makes exactly one `_api` call; `crm_create` returns duplicates before creating; `crm_update` passes the caller's `expect_updated_at` and never fetches one itself |
| `tests/db/test_crm_records_db.py` | `crm_apply` insert with `$ref`, a company and its domains in one call; domain uniqueness across primary and additional; `expect_updated_at` → `CRM09`; soft delete and restore by group; `crm_purge` removes Drive links (task and note links in S4, favourites in S6a, email links in S10); a person in A pointing at a company in B, a company in A with an option id of B, and a Drive link in A to a record of B are each refused (`CRM04` / `23503`); trigram index used (`EXPLAIN`) |

**Done when.**
- [ ] All gates
- [ ] Quickstart §3 S3 by hand: create, duplicate warning, 409, delete, restore, timeline
- [ ] Record pages usable at phone width (FR-CRM-UI-06)

---

## S4 — Opportunities, tasks, notes and the pipeline rules (CRM side)

**Goal.** Deals exist with every field, contacts and roles, amounts in three
currencies stored in INR at the month's rate, and every pipeline rule on the
server: entry requirements, skipping, backwards moves, probability defaults,
company lead-stage rules, Closed Lost closing tasks with a note, reopen from
Lost and reopen of a won deal (CRM side). Tasks and notes, including Log
activity, mentions and Viewer task completion, work. Academy is complete for
deals here, since it has no TVA side. Labs deals stop before Proposal until S5
adds the TVA side, so no Labs deal reaches Proposal without a project and S5
never meets one that should have had a project created.

**Depends on.** S3.

**Spec.** FR-CRM-OPP-01 to -03; FR-CRM-TASK-01, -02; FR-CRM-NOTE-01 to -03
(mention notifications in S9); FR-CRM-LINK-01 (tasks, notes, deals);
FR-CRM-FX-01, -03, -04, -06; FR-CRM-PIPE-02 (manual moves), -03 to -12, -13 (CRM
side), -14, -15 (CRM side); FR-CRM-DEL-02 (deal choices), -04; FR-CRM-ACC-11
(deal amounts); FR-CRM-AUTO-01 automation 9; FR-CRM-UI-02 (Opportunities,
Tasks); FR-CRM-MCP-01 (`crm_set_next_step`, `crm_add_task`,
`crm_complete_task`, `crm_add_note`, `crm_move_stage`), -10.

**Migrations.**
- `030_crm_deals_tasks_notes.sql`: data-model §3.9–3.13 (with the FX integrity CHECKs, the composite FKs and `crm_check_refs()` on the task and note link tables); `CREATE OR REPLACE crm_purge()` adding task and note links (data-model §5.4).
- `031_crm_deal_transition.sql`: data-model §5.5, CRM-side branches, with re-checks 1 and 2 (opportunity and company locked in the actor's workspace; `close_tasks`, assignees and link targets in the workspace).

**Backend — create.**
- `app/domain/crm/pipeline.py`: `STAGES`, `stage_requirements(target, deal, skipped)`, `missing_sentence(stage, fields)`, `may_move(current, target, role, is_owner)`, `default_probability(stage, settings)`, `company_effect_on_open(company)`, `company_effect_on_close(company, outcome, other_open_deals, has_won, choice)`, `plan_move(...)`
- `app/domain/crm/fx.py`: `rate_for(rates, currency, on)`, `to_inr(amount, rate)` (half-up to 2 places), refusal sentence naming the missing rate
- `app/services/crm/deals.py`: create (company gate and Deal open in one `crm_apply` call), update (FX on amount or currency save; next step rule; no stage), `preview`, `move` (builds the plan, calls `crm_deal_transition`), contacts. In a `tva_linked` workspace a move into `proposal`, `negotiation` or `closed_won` is refused 422 "This move needs the TVA link, which is not built yet." (removed in S5; plan §5.6)
- `app/api/crm/deals.py`: `/stage/preview`, `/stage`, contacts

**Backend — modify.**
- `app/domain/crm/objects.py`: opportunity, task and note fields; opportunity `AMOUNT_FIELDS`
- `app/services/crm/records.py` and `app/api/crm/records.py`: tasks (links ≥ 1; `/complete`; Viewer assignee status-only), notes (kind, `occurred_on`, links, mentions of members only), opportunities (no `stage` in PATCH), company delete moves or deletes deals
- `app/services/crm/present.py`: weighted amount derived; Viewer redaction of every amount field; the TVA project block placeholder (`project: null` until S5)
- `app/mcp/crm.py`: the five tools; `crm_create`/`crm_update` cover opportunities, tasks and notes

**Frontend — create.**
- `app/(crm)/crm/[ws]/opportunities/page.tsx`, `opportunities/[id]/page.tsx`
- `app/(crm)/crm/[ws]/tasks/page.tsx`
- `components/crm/stage-dialog.tsx` (asks for exactly the missing fields; the one-sentence refusal shown verbatim)
- `components/crm/lost-dialog.tsx` (lost reason, notes; for a company at Deal open, the Nurturing or Disqualified choice)
- `components/crm/amount-field.tsx` (currency select; INR beside it in the viewer's format)
- `components/crm/contacts-panel.tsx`, `components/crm/task-dialog.tsx`, `components/crm/note-composer.tsx` (markdown rendered with `marked`; @mention picker of members; Log activity with kind and date)

**Frontend — modify.** `components/crm/record-page.tsx` (Tasks and Notes tabs live; Move stage action); `components/crm/shell.tsx` (Opportunities, Tasks).

**MCP.** `crm_set_next_step`, `crm_add_task`, `crm_complete_task`, `crm_add_note`, `crm_move_stage` (CRM-side preview and move).

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_pipeline_rules.py` | Pure, one class per rule: entry requirements per stage; skipping forward needs Discovery–Proposal requirements, Proposal sent on only for Negotiation, Lost only its reason; backwards allowed except out of Won; reopen from Lost by owner or admin to `lost_from_stage` with next step; reopen Won by admin to Negotiation; probability reset unless set in the same save; company rules for the last open deal (Won → Customer and `client`, Partner stays; Lost → Nurturing or Disqualified only without a won deal; Customer and Partner unchanged); the missing-field sentence names every field (FR-CRM-PIPE-03 to -14) |
| `tests/test_crm_fx.py` | INR at 1; the save month's rate, else the latest earlier; no rate → 422 naming currency and workspace; editing a rate does not recompute; totals use `amount_in_inr`; `to_inr` half-up equals Postgres `round(…, 2)` on the edge cases (FR-CRM-FX-01, -03, -04; **AC** "A non-INR amount stores amount in INR…") |
| `tests/test_crm_deals_routes.py` | Deal on a company below Qualified 422; creating one sets Deal open; open deal without next step refused through `POST`, `PATCH` and the MCP tool; Proposal without amount, currency and close date names all three; Lost without reason 422; Lost closes open tasks with a note (automation 9); every move writes history with its time; Academy deals move through every stage with no project effect; a Labs move into Proposal, Negotiation or Closed Won is refused until S5 (FR-CRM-PIPE-04, -07, -09, -10, -14; **AC** pipeline rules 1–5, history half of "Every stage change records its time") |
| `tests/test_crm_tasks_notes.py` | Task needs a link; Viewer may mark own task done, status only; Log activity needs a date; mentioning a non-member is 422; notes link to many records (FR-CRM-TASK-01, NOTE-01 to -03, Q-57) |
| `tests/test_crm_redaction.py` (create) | A recursive key walk over every opportunity read route (get, list, timeline, related) and `crm_get` as a Viewer finds no amount key. The walk uses its own denylist written out in the test (plan §5.5), never `AMOUNT_FIELDS` (FR-CRM-ACC-11; **AC** Viewer redaction, so far) |
| `tests/test_crm_isolation.py` (extend) | Deals, tasks, notes, contacts and stage routes with foreign ids → 404 |
| `tests/test_crm_mcp.py` (extend) | Five new tools; `crm_move_stage` DESTRUCTIVE; a two-workspace member's `crm_add_task` without `workspace` lists both |
| `tests/db/test_crm_deal_transition_db.py` | Stale `expect_stage` or `expect_updated_at` → `CRM09` and nothing written; history row on every move; company update and task closing roll back with a failed move; `crm_apply` refuses a `stage` change; the backstop CHECKs refuse an open deal without a next step and Lost without a reason; the FX CHECKs refuse an `amount_in_inr` that is not `round(amount * fx_rate_used, 2)` and an INR deal with a rate other than 1; a plan with another workspace's opportunity, company, `close_tasks` id or link target, or a non-member assignee → `CRM04` and nothing written; a deal in A pointing at a company or contact in B is refused; `crm_purge` removes task and note links |

**Done when.**
- [ ] All gates
- [ ] Quickstart §3 S4 by hand, in both workspaces
- [ ] Journey 4.2's CRM half (Discovery → Negotiation) works through the web app and through MCP in Academy; in Labs up to Qualification

---

## S5 — TVA lifecycle (Labs): Proposal, handoff, Lost release, reopen, link, CRM-controlled projects

**Goal.** A Labs deal at Proposal becomes a tentative project; a name clash
offers rename or link; the Closed Won handoff confirms the project with lead
and phases (and revenue and milestones only for a TVA manager or admin);
Closed Lost archives and releases; reopen restores exactly; imported deals can
be linked; TVA refuses status changes on CRM-linked projects. Every effect is
in the same transaction as the deal move.

**Depends on.** S2 (readers honour the markers), S4 (the transition function).

**Spec.** FR-CRM-TVA-01 to -18; FR-CRM-PIPE-09 (TVA effects), -13 (project
effect), -15; FR-CRM-FX-05; FR-CRM-DEL-02 (TVA refusal), -03; FR-CRM-CO-02
(TVA projects); FR-CRM-REC-03 (Related: TVA projects); FR-CRM-AUTO-01
automation 10; FR-CRM-CHG-05, -06, -10, -16; FR-CRM-AUD-04 (TVA rows);
FR-CRM-WS-05; D5, D8, D14, D15; FR-CRM-MCP-01 (`crm_handoff`).

**Migrations.**
- `032_crm_tva_links.sql`: data-model §4.3 and the §4.4 FK.
- `033_crm_lifecycle.sql`: data-model §5.5 (TVA branches, with re-checks 3–6: `tva_linked`, the project from `source_opportunity_id`, `link` and revenue only from a staff TVA manager or admin), §5.6, §5.4 replacement.

**Backend — create.**
- `app/domain/crm/handoff.py`: pre-fill from the deal and existing phases; the `pre` phase offered for `discovery_phase`; `spillover` never; milestones against revenue and the difference; lead must be an active staff account; revenue-task recipients (Labs admins who are TVA managers or admins, else all Labs admins); delivery-handoff assignee (the lead if a Labs member, else the deal owner, naming the lead)
- `app/services/crm/tva.py`: project actions for the plan (`create`, `link`, `sync_probability`, `confirm`, `archive_release`, `unarchive_unrelease`, `retentative`); clash detection and `linkable`; won-date FX

**Backend — modify.**
- `app/services/crm/deals.py`: remove S4's Labs refusal; attach the project action for Labs moves; a project-less Labs deal (an imported one) moving into `proposal` or `negotiation` gets the tentative project created as FR-CRM-TVA-04, with the clash rule, and into `closed_won` through the handoff (TVA-10); any Labs deal save that changes probability with a tentative project goes through the transition; reopen-won pre-check with `rules.make_tentative_refusal`; `link_project_id` accepted only from a staff TVA manager or admin (409 "Ask a TVA manager to link it."); revenue and milestones put in the plan only for a staff TVA manager or admin
- `app/api/crm/deals.py`: `GET`/`POST …/handoff` (`existing_tva_revenue` and TVA milestone amounts only for a staff TVA manager or admin; revenue from anyone else ignored with 200 `revenue_written: false`), `POST …/link-project` (`link` mode: staff TVA manager or admin), `GET /crm/projects/linkable` (same guard, else 403); Labs `closed_won` through `/stage` refused (needs the handoff)
- `app/services/crm/records.py`: deleting a deal with a project, or a company with such deals, refused 422 naming them
- `app/services/crm/present.py`: the deal's and company's `project` block (name, status, archived, phase dates), for every role including Viewers and `crm_only` (FR-CRM-ACC-03, TVA-02)
- `app/api/admin.py`: `update_project` refuses `status`, `probability`, and `is_archived` while tentative, on linked projects ("Managed by CRM deal *name*." for Labs members, else "Managed by a CRM deal."); `_present_project` adds `crm_managed` and, for Labs members only, the `crm` block; `list_projects` reads the caller's Labs membership and the linked deals and companies once per request and passes them in (no query per project)
- `app/api/timesheet.py`: `GET /analytics/projects/{project_id}` presents the same `crm_managed` and `crm` block
- `app/services/supabase.py`: `get_project_by_source_opportunity`, `list_linkable_projects`, project reads include the CRM columns
- `app/mcp/server.py`: `update_project` docstring names the CRM-controlled fields (FR-CRM-CHG-16)
- `app/mcp/crm.py`: `crm_handoff`; `crm_move_stage` description and preview carry the TVA effects

**Frontend — create.**
- `components/crm/handoff-dialog.tsx` (project name, category, lead picker of active staff, delivery dates, optional support and pre phases, revenue pre-filled and labelled INR with the existing TVA revenue beside it for managers only, milestones in INR with a running difference; revenue and milestones read-only with an explanation for non-managers)
- `components/crm/name-clash-dialog.tsx` (rename the deal or the project, or link when `linkable` and the person is a TVA manager or admin; otherwise "Ask a TVA manager to link it.")
- `components/crm/link-project-dialog.tsx` (Labs admin; link mode for TVA managers and admins)
- `components/crm/project-chip.tsx` (name, status, phase dates)

**Frontend — modify.**
- `components/crm/stage-dialog.tsx` (shows the preview's project effect; opens the handoff for Won in Labs)
- `app/(crm)/crm/[ws]/opportunities/[id]/page.tsx`, `companies/[id]/page.tsx` (project chips in Related)
- `components/portal/projects-panel.tsx` (read-only status, probability and archive while CRM-controlled, with "Managed by CRM deal …"; the CRM company and deal block for Labs members)
- `lib/api/types.ts` (`Project.crm_managed`, `Project.crm`)

**MCP.** `crm_handoff` (preview, confirm, link).

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_handoff_rules.py` | Pure: pre-fill, phase mapping, no `spillover`, milestones difference in INR, lead must be active staff (not `crm_only`), revenue only for managers, task recipients both cases, delivery task assignee both cases; the preview carries `existing_tva_revenue` for a staff manager and no such key for a TVA `user` or a `crm_only` member, with `can_set_revenue` false (FR-CRM-TVA-07 to -09) |
| `tests/test_crm_audit_routes.py` (extend) | A Proposal move by a `crm_only` seller writes TVA audit rows that `/admin/audit` shows as "*Seller* (via CRM deal)", not a raw id (TVA-03) |
| `tests/test_crm_tva_lifecycle.py` | Proposal creates exactly one tentative project with name, client, company link, category (`client` when empty) and probability; a second Proposal entry does nothing; a clash is 409 naming it, and linking proceeds only to an unlinked, tentative, non-archived project; probability changes sync while open, including after moving back out of Proposal; Won as a TVA `user` confirms without revenue and creates the admin task; Won as a manager writes INR revenue at the won-date rate and the milestones, and the deal stores the rate; skipping to Won creates the project confirmed; Lost archives and releases with reason and person; reopen from Lost unarchives and un-releases exactly those rows, even when they now overlap; reopen-won refused with time entries or invoiced milestones, else back to tentative with revenue kept; a reopened-won deal can be lost; link mode for open and won deals; linking (through a clash, `link-project` or `crm_handoff`) and the linkable list are refused to a Labs Member who is a TVA `user` or `crm_only`, and to a Labs admin who is not a TVA manager; a project-less imported deal moved into Negotiation gets its project; Academy never touches projects; the TVA audit rows carry `system` and the person (FR-CRM-TVA-01 to -18, FX-05, PIPE-13; **AC** TVA integration 1–8 and 10) |
| `tests/test_crm_project_guard.py` | `update_project` and the MCP tool refuse status, probability and tentative archive on a linked project; the deal name appears only for Labs members, on `GET /admin/projects` and `GET /analytics/projects/{project_id}` alike; `list_projects` makes a fixed number of store calls whatever the project count; a won linked project archives and unarchives as today; `_present_project` never carries an amount from the CRM (FR-CRM-CHG-05, TVA-02, -15, -17; **AC** "The Projects page and `update_project` refuse…") |
| `tests/test_crm_delete_guards.py` | Deleting a deal with a project, or a company with such a deal, is 422 naming the deals, and nothing is partly deleted (FR-CRM-DEL-02, -03; **AC** "Deleting a company with a deal linked to a TVA project is refused") |
| `tests/test_pnl.py` (extend) | A deal won by a manager makes the project count once in the P&L, with no double count of pipeline and confirmed revenue (**AC** "nothing is double-counted in the P&L") |
| `tests/db/test_crm_lifecycle_db.py` | Each project action against the real function; a clash raised inside the function rolls back the deal move; `archive_release` touches only unreleased rows of the opportunity's own project, and a plan naming another project id archives nothing else; `unarchive_unrelease` only that opportunity's rows; `retentative` re-check; `crm_link_project` re-verification; a project action from the Academy workspace → `CRM22`; `link` and `confirm` with revenue from an actor who is not a staff manager or admin → `CRM22` and nothing written; `crm_purge` of a company clears `projects.crm_company_id` |
| `frontend/e2e/crm.spec.ts` (extend) | Journey 4.2 end to end in the browser: Discovery → Proposal (project appears in Projects as tentative) → Won through the handoff |

**Done when.**
- [ ] All gates
- [ ] Journey 4.2's independent test passes twice (as a TVA user, then as a manager), by test and by hand
- [ ] Journey 4.3's independent test passes: allocate two, lose, read forecast, bench, resources, hiring and P&L pipeline, reopen, every figure as before

---

## S6a — Views, backend: `crm_query`, saved views, favourites, bulk, `crm_list`

**Goal.** The server answers every view: nested filters, multi-sort, groups,
aggregates, keyset paging, saved views (private and shared), favourites and
bulk actions. Viewers can never filter, sort or total by amount, and a shared
view opens redacted for them instead of failing. The seeded views exist.
`crm_list` works.

**Depends on.** S4 (every 1a object exists), S5 (bulk stage changes run the TVA effects).

**Spec.** FR-CRM-VIEW-02 to -05, -07, -08, -10, -11; FR-CRM-ACC-11 (filters,
sorts, aggregates, shared-view notice); FR-CRM-MCP-01 (`crm_list`), -10.

**Migrations.** `034_crm_views.sql` (data-model §3.15, §3.16, §5.8, §7.5;
`crm_check_refs()` on `crm_favourites`; `CREATE OR REPLACE crm_purge()` adding
favourites, data-model §5.4).

**Backend — create.**
- `app/domain/crm/views.py`: spec validation (operators per type, AND/OR depth ≤ 3, ≤ 30 conditions); Viewer amount refusal; `clean_shared_view` dropping amount conditions, columns, sorts, `group_by` and aggregates, with the notice; `q` with `%`, `_` and `\` escaped; `is_me` resolution; kanban field must be groupable
- `app/services/crm/views.py`: query (calls `crm_query`; resolves `view` by name or `system_key`), saved views, favourites, bulk (each record through the same service, results per id)
- `app/api/crm/views.py`: contracts §2.7 and `POST /crm/bulk`

**Backend — modify.**
- `app/services/crm/store.py`: `query()` wrapper
- `app/api/crm/records.py`: `GET /crm/{object}` stays the plain list
- `app/mcp/crm.py`: `crm_list` (one `POST /crm/query` call, passing `view` when named)

**Frontend.** None (S6b).

**MCP.** `crm_list`.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_views_spec.py` | Every operator per type; AND/OR nesting; unknown or non-filterable fields 422; Viewer amount filter, sort, group and aggregate 422; a shared view with an amount condition, column, sort, group or aggregate is cleaned for a Viewer with the notice, and the seeded `pipeline` kanban opens for a Viewer without its sum headers; a `q` of `50%_off` matches literally (FR-CRM-VIEW-02, -03, -08, ACC-11; **AC** "a Viewer filter or sort on an amount is refused") |
| `tests/test_crm_views_routes.py` | Viewers save private views only; members and admins manage shared views; seeded views exist per workspace with the spec's definitions; defaults per object; favourites; bulk returns ok and failed per id with reasons, and each record obeys the pipeline rules (FR-CRM-VIEW-05, -07, -11) |
| `tests/test_crm_object_fields_registry.py` | `crm_object_fields` (read through a fake) equals `domain/crm/objects.py` field for field, amount flags included |
| `tests/test_crm_redaction.py` (extend) | Group aggregates and footers from `/crm/query` as a Viewer carry no sums |
| `tests/test_crm_mcp.py` (extend) | `crm_list` by view name in one call; amounts absent for a Viewer |
| `tests/db/test_crm_query_db.py` | Results of filters, sorts, groups, aggregates and keyset paging against hand-computed answers; an injection attempt in a field name, operator or value is refused or bound as a literal; `p_redact` refuses amount fields; deleted and merged rows never appear; another workspace's rows never appear; a favourite pointing at another workspace's record is refused; `crm_purge` removes favourites |

**Done when.**
- [ ] All gates
- [ ] The seeded views return the right rows in both workspaces through `POST /crm/query`

---

## S6b — Views, frontend: table, kanban, inline edit, bulk bar, Pipeline

**Goal.** Every object list is a server-paged table or kanban with chosen
columns, inline edit, bulk actions, footers and column totals, on the views
S6a serves. The Pipeline page is the opportunities kanban.

**Depends on.** S6a.

**Spec.** FR-CRM-VIEW-01, -06, -09; FR-CRM-UI-02 (favourites, Pipeline);
FR-CRM-CAP-02 (capacity reachable from Pipeline); NFR-CRM-01 (drag feedback),
-06.

**Migrations.** None.

**Backend.** None.

**Frontend — create.** (`pnpm add @dnd-kit/core @dnd-kit/sortable`, justified in plan §2.1)
- `components/crm/data-table.tsx` (`@tanstack/react-table`: choose, reorder and resize columns; inline cell editing through `field-editor`; row selection; collapsible groups with counts; footers count/sum/avg/min/max; arrow keys, Enter opens, Escape closes)
- `components/crm/kanban.tsx` (dnd-kit with keyboard sensors; group by any select; column headers count, INR sum and weighted; optimistic move with rollback; a stage drag that needs more opens `stage-dialog` or the handoff)
- `components/crm/filter-builder.tsx`, `components/crm/sort-menu.tsx`, `components/crm/view-switcher.tsx` (save as, private or shared, icon, default, reorder), `components/crm/bulk-bar.tsx`, `components/crm/view-search.tsx`
- `app/(crm)/crm/[ws]/pipeline/page.tsx` (opportunities kanban on the `pipeline` view; link to Capacity for those who may see it)

**Frontend — modify.** The companies, people, opportunities and tasks list pages use the table and kanban; `components/crm/shell.tsx` (Pipeline, favourites).

**MCP.** None.

**Tests.**

| File | Covers |
|---|---|
| `frontend/e2e/crm.spec.ts` (extend) | A table view filters, sorts, groups and saves; a kanban drag moves a card and a refused drag rolls back; the shared-view notice shows for a Viewer (FR-CRM-VIEW-01, -06, -09) |

**Done when.**
- [ ] All gates
- [ ] Kanban drag works with mouse, touch (phone width) and keyboard
- [ ] The seeded views open in both workspaces with the right filters

---

## S7 — Today, Cmd+K search, company merge

**Goal.** The CRM home answers "what do I do today". Cmd/Ctrl+K searches one
workspace and jumps, creates or runs quick actions. Two companies merge
cleanly, TVA links included.

**Depends on.** S6b (saved views appear in the command menu).

**Spec.** FR-CRM-TODAY-01 to -04 (email sections say "Email sync is off");
FR-CRM-SRCH-01 to -03; FR-CRM-DUP-03, -04; FR-CRM-CHG-13 (admin views a
member's Today); FR-CRM-MCP-01 (`crm_today`, `crm_search`, `crm_merge`).

**Migrations.** `035_crm_search_merge.sql` (data-model §5.7, §5.9; the merge function's own re-checks: both companies in the workspace, not deleted, not merged, survivor ≠ merged).

**Backend — create.**
- `app/domain/crm/today.py`: section order, membership of each section, loose-end rule, the email-sync-off markers; `today` and `now` from the person's time zone
- `app/services/crm/today.py`, `app/api/crm/today.py`
- `app/services/crm/search.py`, `app/api/crm/search.py`
- `app/domain/crm/merge.py` (differing fields, survivor choices, what moves), `app/services/crm/merge.py`, `app/api/crm/merge.py`

**Backend — modify.** `app/mcp/crm.py` (three tools); `app/services/crm/present.py` (search hits redacted).

**Frontend — create.** (`pnpm add cmdk`, justified in plan §2.1)
- `components/crm/command-menu.tsx` (global Cmd/Ctrl+K inside the CRM layout: records, saved views, create, the open record's quick actions)
- `components/crm/today-list.tsx`, `components/crm/merge-dialog.tsx`

**Frontend — modify.** `app/(crm)/crm/[ws]/page.tsx` (Today; admin person picker); `app/(crm)/crm/[ws]/layout.tsx` (mount the command menu); `companies/[id]/page.tsx` (Merge action).

**MCP.** `crm_today`, `crm_search`, `crm_merge`.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_today.py` | Fixed clock: order of sections; overdue and due-today next steps; Today bucket and due-today tasks; loose ends (Nurturing, no open task, no next touch); "Email sync is off" for sections 1 and 4; a Sydney user's day boundary; an admin may pick a member and a member may not (FR-CRM-TODAY-01 to -04, CHG-13; Journey 4.1 independent test) |
| `tests/test_crm_search.py` | One workspace only; deleted and merged excluded; a Viewer's hits carry no amount; jump targets include saved views (FR-CRM-SRCH-01 to -03) |
| `tests/test_crm_merge.py` | Plan: field choices; everything moves; both timelines; TVA links kept on the survivor (FR-CRM-DUP-03, -04) |
| `tests/db/test_crm_search_db.py` | Trigram ranking; `%` and `_` in the query matched literally; under 1 s on 10k seeded rows locally (smoke; the full check is S12) |
| `tests/db/test_crm_merge_db.py` | Atomic merge; domain de-duplication; `merged_into_id` resolution; `projects.crm_company_id` moved with a TVA audit row; a merge naming a company of another workspace, a deleted or already-merged company, or the same id twice is refused and writes nothing |
| `frontend/e2e/crm.spec.ts` (extend) | Cmd+K finds a seeded company and opens it |

**Done when.**
- [ ] All gates
- [ ] Journey 4.1's independent test passes as a `crm_only` Member, including the TVA refusal half
- [ ] Journey 4.7 through MCP: `crm_today`, `crm_move_stage` with preview, `crm_add_task`, `crm_add_note`

---

## S8 — Twenty importer and the widened proxy

**Goal.** An admin dry-runs and runs a Twenty export into their workspace,
idempotently, with every link and Twenty id. The proxy carries uploads up to
4 MB and file downloads with their headers, which also fixes the effort
statement CSV.

**Depends on.** S7 (all 1a objects and merge exist; the dry run reuses
duplicate checks).

**Spec.** FR-CRM-MIG-01 to -08 (campaigns in S11a); FR-CRM-CHG-08;
FR-CRM-DUP-02 (importer); FR-CRM-PIPE-10 (through import); FR-CRM-MCP-01
(`crm_import`), -08; `006` FR-STMT-02 fix; Q-05 upload confirmation.

**Migrations.** `036_crm_imports.sql` (data-model §3.17).

**Backend — create.**
- `app/domain/crm/importer.py`: the declared Twenty shape and column aliases; stage map (NEW→discovery … LOST→closed_lost); lead-stage derivation in Q-55 order; owner mapping by email, else the importing admin; rule violations per row; opt-in "Review after import" next step dated the import day
- `app/services/crm/importer.py`: parse JSON or CSV (stdlib `json`, `csv`); `sha256`; dry-run report (a non-INR deal whose month has no FX rate is a conflict, "No USD rate for 2026-09 in Nunnari Labs.", and the apply is refused until the rate exists); apply in chunks of 500 through `crm_apply` `upsert` on `twenty_id` with actor `import`; attachments listed for re-linking, never fetched; `last_inbound_at`/`last_outbound_at` from people timestamps
- `app/api/crm/imports.py`: contracts §2.9; 4 MB cap; `UploadFile`
- `backend/tests/fixtures/twenty/labs.json`, `academy.json`, `labs_csv/*.csv` (hand-written from the declared shape, with known counts and deliberate conflicts)

**Backend — modify.** `backend/pyproject.toml` + `uv.lock`: `python-multipart` direct; `app/mcp/crm.py`: `crm_import`.

**Frontend — create.**
- `app/(crm)/crm/[ws]/settings/import/page.tsx` and `components/crm/twenty-import.tsx` (upload, dry-run report per object with conflicts and derived stages, fix-up opt-in, apply)

**Frontend — modify.**
- `app/api/proxy/[...path]/route.ts` (plan §5.12): forward only `Authorization` and a `Content-Type` of `application/json`, `multipart/form-data` or `text/csv` (else 415), never `Cookie`, `X-Portal-*` or `X-Forwarded-*`; read the body as a stream with a running byte count and refuse past 4 MB with 413 RFC 7807 whatever `Content-Length` says; call the backend with `redirect: 'manual'`; copy only status, `Content-Type` and `Content-Disposition` back; stream non-JSON responses
- `lib/api/backend.ts`: an `upload()` helper (FormData) and a `download()` helper (blob plus filename from `Content-Disposition`)
- `app/(portal)/analytics/statement/page.tsx`: use `download()`

**MCP.** `crm_import` (Twenty formats; content as text ≤ 4 MB).

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_importer_mapping.py` | Stage map; lead-stage derivation each branch and order; owner mapping; violations listed (duplicate domain or email, similar name, open deal without next step, deal below Qualified, unknown owner, a missing FX rate); fix-up counted (FR-CRM-MIG-02, -04, -05) |
| `tests/test_crm_import_routes.py` | Admin only; dry run counts match the fixture; apply needs a matching hash within 24 h; the second apply changes nothing; links (company to people, deals, tasks, notes) reproduced; Twenty ids kept; Labs deals at Proposal or later create no project; attachments listed; 4 MB cap; `crm_import` through MCP (FR-CRM-MIG-01, -03, -04, -06 to -08, MCP-08; **AC** "The Twenty importer's dry run reports counts… A second run changes nothing") |
| `tests/test_crm_deals_routes.py` (extend) | An open deal without next step is refused through import unless the fix-up is chosen (**AC** "through the web app, import, MCP or API") |
| `tests/test_statement_download.py` (create) | The statement route's `text/csv` body and `Content-Disposition` filename (already set in `api/ops.py`) are what the widened proxy must pass through (`006` FR-STMT-02) |
| `tests/db/test_crm_import_db.py` | Upsert idempotence through the real `crm_apply`; no audit rows on an unchanged re-run; an upsert that changes `amount` without `amount_in_inr` is refused by the FX CHECK; a 500-row chunk under the statement timeout |
| `frontend/e2e/crm.spec.ts` (extend) | A 3.9 MB CSV uploads through the proxy; a 4.1 MB one gets 413, and so does an oversize chunked upload with no `Content-Length`; a disallowed request `Content-Type` gets 415; a backend `Set-Cookie` or `Location` header never reaches the browser; the statement CSV downloads with its name and real content |

**Done when.**
- [ ] All gates
- [ ] Journey 4.6's independent test passes on the fixtures
- [ ] The effort-statement CSV download works in the browser
- [ ] **Phase 1a is complete**: every 1a FR in the coverage table below has its tests passing

---

## S9 — Job log, follow-up flags, the bell, digests, automations 5–7 (1b)

**Goal.** Flags are stored and fresh. Background runs are logged, alert after
three failures, and are visibly stale when the scheduler stops. People get an
in-app bell, a daily digest at 09:00 their time and (admins) a weekly digest,
each exactly once. Stale-deal notices and task reminders run behind their
switches.

**Depends on.** S8.

**Spec.** FR-CRM-FLAG-01 to -05 (email-based flags compute from email facts
once S10 supplies them; until then they are `none`); FR-CRM-JOB-01 to -03;
FR-CRM-NOTIF-01 to -05; FR-CRM-AUTO-01 automations 4 to 7, -02;
FR-CRM-NOTE-02 (mention notifications); FR-CRM-LIST-06 (thresholds,
automation switches); FR-CRM-VIEW-11 (Follow-ups owed); FR-CRM-REC-01 (flag
dot).

**Migrations.**
- `037_crm_jobs_notifications.sql`: data-model §3.18–3.20; settings keys and seeds (§7.3); `CREATE OR REPLACE crm_remove_membership()` deleting the person's notifications for the workspace (§5.3).
- `038_crm_flags.sql`: data-model §3.21; registry rows; the `follow_ups_owed` view.

**Backend — create.**
- `app/domain/crm/flags.py`: one function per row of spec §6.2, taking `now` and thresholds; `worst()`; internal and auto emails ignored
- `app/domain/crm/digests.py`: daily content (one section per current membership, each built and redacted with the person's role in that workspace), weekly admin content per workspace (pipeline movement, won and lost, flag counts), due from 09:00 local time
- `app/services/crm/flags.py`: recompute for records (called after relevant writes) and for a workspace (the job)
- `app/services/crm/jobs.py`: `@crm_job(name)`; one run row per workspace for every job but the digest tick; streak alert to workspace admins and TVA admins (in-app and `notify.deliver`); staleness (twice the interval); `error_public`; a NULL-workspace row is shown with `error_public` only and never in `/crm/jobs` or `stale_jobs`
- `app/services/crm/notifications.py`: create (dedupe key), deliver out of app through `notify.deliver`, count undeliverable; never to non-members; titles never carry amounts; list and unread count joined to current memberships
- `app/services/crm/digests.py`: daily and weekly; sent markers; recipients and sections from current memberships; `crm_only` recipients only when `settings.email_enabled`
- `app/api/crm/notifications.py`, `app/api/crm/jobs.py`

**Backend — modify.**
- `app/tasks/crm.py`: `crm.recompute_flags` (every 30 min, including the stale-deal notice), `crm.digest_tick` (every 15 min: digests and task reminders); `crm.purge_trash` wrapped in `@crm_job`
- `app/worker.py`: beat entries
- `app/main.py`: `GET /health/jobs`
- `app/api/crm/tva_admin.py`: `/crm/tva-admin/jobs`
- `app/api/crm/workspaces.py`: `bootstrap` returns `unread` and `stale_jobs`
- `app/services/crm/records.py`, `deals.py`: emit notifications (assigned, mentioned, stage changed by someone else, handoff or revenue waiting) and recompute flags after writes
- `app/services/crm/today.py`: flag levels order section 1 (red first); stale jobs for admins
- `app/api/crm/settings.py`: `flag_thresholds`, `automations`
- `tests/test_mcp.py`: exclude `("GET", "/health/jobs")`

**Frontend — create.** `components/crm/bell.tsx` (polls every 60 s and on window focus); `components/crm/flag-dot.tsx` (dot plus label; never colour alone); `components/crm/stale-banner.tsx`; settings tabs Jobs, Thresholds, Automations.

**Frontend — modify.** `components/crm/shell.tsx` (bell, banner); the data table and record header show flags; `app/(portal)/admin/page.tsx` (CRM workspaces panel: job runs).

**MCP.** None new. `crm_get` and `crm_today` now carry flags and stale jobs.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_flags.py` | Fixed `NOW`: each row of §6.2 at red, amber and none, at each threshold edge; thresholds from settings; worst flag for deals and companies; internal-only and auto emails never change a flag (FR-CRM-FLAG-01 to -04; **AC** "Reply-owed, client-silent and next-step flags match §6.2 in fixed-clock tests", "Internal-only emails never change flags") |
| `tests/test_crm_jobs.py` | Every run is recorded; trash purge writes one row per workspace; three consecutive failures alert once per streak to admins and TVA admins; the stale banner after twice the interval; `/health/jobs` has no record content; a TVA admin who is not a member sees `error_public` only; a workspace admin's `/crm/jobs` and `stale_jobs` never show a NULL-workspace row or its `error` (FR-CRM-JOB-01 to -03) |
| `tests/test_crm_notifications.py` | Each event reaches exactly the right people; never a non-member; no amount in any title; unread count; mark read; a member removed from B sees none of B's notifications in the list or the count, and they are deleted (FR-CRM-NOTIF-01, -05, NOTE-02) |
| `tests/test_crm_digests.py` | Two ticks in one window send once; 09:00 Sydney and 09:00 Kolkata each fire in their own window; weekly digest to admins on Monday, separate from TVA's; `crm_only` waits for SMTP and counts undeliverable; a Viewer's daily digest carries no amount; a person who is a Viewer in A and a Member in B gets A's section with no amount and B's with amounts; a removed membership's workspace is not in the digest (FR-CRM-NOTIF-02 to -05; **AC** "Each daily digest is delivered exactly once per person per day at 09:00 their time") |
| `tests/test_crm_automations.py` | Switches 5, 6 and 7 off → nothing sent and the run logged `skipped`; automations 9 and 10 have no switch (FR-CRM-AUTO-01, -02) |
| `tests/test_crm_redaction.py` (extend) | Notifications and digests as a Viewer contain no amount |
| `tests/db/test_crm_flags_db.py` | A flags-only update writes no audit row; the sent-marker `ON CONFLICT` guarantee |

**Done when.**
- [ ] All gates
- [ ] Quickstart §3 S9 by hand, including the forced three failures
- [ ] Beat locally fires `crm.recompute_flags` and `crm.digest_tick` on schedule

---

## S10 — Gmail label sync (1b)

**Goal.** Labelled Gmail threads land on the right people and companies within
15 minutes, idempotently, as metadata only. The sync stays off until the
service-account credential exists and an admin switches it on, and runs on its
own single-slot worker.

**Depends on.** S9 (job log, flags, notifications).

**Spec.** FR-CRM-SYNC-01 to -10; FR-CRM-PIPE-02 (New → Contacted on first
outbound, automation 3); FR-CRM-AUTO-01 automation 2; FR-CRM-FLAG-01 (email
flags live); FR-CRM-TODAY-01 sections 1 and 4, -02; FR-CRM-REC-03 (Emails tab);
FR-CRM-NOTIF-01 (reply owed turned red); NFR-CRM-02, -08 (Gmail latency
logged); FR-CRM-MCP-01 (`crm_run_email_sync`).

**Migrations.** `039_crm_email.sql` (data-model §3.22; `email_sync` setting; `crm_check_refs()` on `crm_email_links`; `CREATE OR REPLACE crm_purge()` adding email links and `CREATE OR REPLACE crm_merge_companies()` moving them, data-model §5.4, §5.7).

**Backend — create.**
- `app/services/crm/gmail.py`: service-account token for a `sub` mailbox with `pyjwt` and `httpx` (`gmail.readonly` only), cached in process memory only (never Redis, the database or Celery arguments); list by label with history cursor; message metadata (headers only); latency logged per call; the key never logged; runs only on `worker-crm-sync`
- `app/domain/crm/email_rules.py`: participants classification (our domains = the allow-list), direction, internal-only skip, auto-reply and bounce detection, match people by any email then companies by domain, what to create (company at New owned by the mailbox owner, source Email sync)
- `app/services/crm/email_sync.py`: tick (switch, credential, `SET NX EX 900` lock), page task (one page of one mailbox, resumable from the stored token), 90-day backfill, label removal deletes the stored copy, `last_*` forward-only, flags recompute, New → Contacted, reply-owed-red notifications
- `app/api/crm/email.py`: mailboxes CRUD (address is the owner's own profile email unless the caller is a TVA admin; domain in `GMAIL_DOMAINS`; no Gmail system label by id or name; address globally unique), run now, emails per record

**Backend — modify.**
- `app/config.py`: `GOOGLE_SERVICE_ACCOUNT_JSON: SecretStr | None` (set on `worker-crm-sync` only) and `CRM_GMAIL_CONFIGURED: bool` (the non-secret flag on `api`, `beat` and `worker`; plan §2.2); the tick and run-now read the flag, never the key
- `backend/pyproject.toml` + `uv.lock`: `pyjwt[crypto]` direct
- `app/worker.py`: `CRM_SYNC_QUEUE`; `task_routes` for `crm.email_sync_page`; beat `crm.email_sync_tick` every 15 min
- `app/tasks/crm.py`: `crm.email_sync_tick`, `crm.email_sync_page` (`soft_time_limit=240`, `time_limit=300`)
- `backend/entrypoint.sh`: `PROCESS_TYPE=worker_crm_sync`
- `backend/Procfile`: a `worker_crm_sync` line
- `app/services/crm/today.py`: sections 1 and 4 live when sync is on in the workspace
- `app/mcp/crm.py`: `crm_run_email_sync`

**Frontend — create.** `components/crm/emails-tab.tsx` (threads: subject, participants, direction, date; no bodies); settings tab Mailboxes (owner must be a member; labels).

**Frontend — modify.** Record pages (Emails tab for companies, people and deals); the Today page.

**MCP.** `crm_run_email_sync`.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_email_rules.py` | Pure: every rule of FR-CRM-SYNC-05; auto-reply and bounce detection; our domains from the allow-list (FR-CRM-SYNC-05, FLAG-04) |
| `tests/test_crm_email_sync.py` | Fake Gmail: a labelled message stored on the right person and company with `last_*` updated; a second run stores nothing; label removal removes it and its flag effect; an unknown external domain creates a company at New owned by the mailbox owner; backfill pages resume after an interruption; tick skipped when off, `CRM_GMAIL_CONFIGURED` false or locked, and it never reads the key; a mailbox owner must be a member; a mailbox address other than the owner's own is refused for a workspace admin and allowed for a TVA admin; an address outside `GMAIL_DOMAINS`, a system label (`INBOX`, `SENT`, `CATEGORY_PROMOTIONS`) and an address already registered in the other workspace are refused; run-now admin only (FR-CRM-SYNC-01 to -10, PIPE-02; **AC** email 1–3) |
| `tests/test_crm_gmail_token.py` | JWT claims (`iss`, `sub`, `scope` = `gmail.readonly`, `aud`); the key and token never appear in logs |
| `tests/test_crm_today.py` (extend) | Sections 1 and 4 with email facts; red first |
| `tests/test_crm_isolation.py`, `tests/test_crm_redaction.py` (extend) | Emails tab and `crm_get` emails scoped to the workspace (FR-CRM-SYNC-10) |
| `tests/db/test_crm_email_db.py` | `UNIQUE (mailbox_id, gmail_message_id)` makes a double insert a no-op; `crm_purge` removes email links; `crm_merge_companies` moves them to the survivor; an email link to another workspace's record is refused |

**Done when.**
- [ ] All gates
- [ ] With no credential set, the tick logs `skipped` and nothing else happens
- [ ] With a test credential (if available), quickstart §3 S10 by hand; otherwise the fake-Gmail tests are the evidence, and the commit message says so

---

## S11a — Campaigns (1b)

**Goal.** Campaigns with member boards and conversion to deals, campaign
records searchable and mergeable like the rest, and Twenty campaigns imported.

**Depends on.** S10 (merge and purge already handle email links), S8 (importer).

**Spec.** FR-CRM-CAMP-01 to -07; FR-CRM-PER-02; FR-CRM-MIG-01 (campaigns and
members); FR-CRM-AUTO-01 automation 8; FR-CRM-VIEW-07 (add to campaign),
-11 (Members by status); FR-CRM-UI-02 (Campaigns); FR-CRM-ACC-11 (budget,
campaign values); FR-CRM-MCP-01 (`crm_campaign_members`; `crm_create` /
`crm_update` campaigns).

**Migrations.** `041_crm_campaigns.sql`: data-model §3.23 (with the FX CHECKs
on the budget and the composite FKs); campaign lists; registry rows
(`budget`, `budget_in_inr`, `budget_fx_rate` with `is_amount = true`);
`members_by_status` view; `CREATE OR REPLACE crm_merge_companies()` moving
campaign members and `CREATE OR REPLACE crm_search()` adding campaigns
(data-model §5.7, §5.9).

**Backend — create.**
- `app/domain/crm/campaigns.py` (Do not contact; conversion sets the member's opportunity and the deal's source campaign; derived counts and cost per converted; budget in INR at the month's rate)
- `app/services/crm/campaigns.py`, `app/api/crm/campaigns.py`

**Backend — modify.**
- `app/services/crm/importer.py`: Twenty campaigns and campaign members
- `app/domain/crm/objects.py` (campaign `AMOUNT_FIELDS` per plan §5.5), `services/crm/records.py`: campaign and member objects; Drive links, tasks and notes on campaigns
- `app/services/crm/views.py`: bulk `add_to_campaign`
- `app/mcp/crm.py`: `crm_campaign_members`; `crm_create`/`crm_update` campaigns

**Frontend — create.**
- `app/(crm)/crm/[ws]/campaigns/page.tsx`, `campaigns/[id]/page.tsx` (member kanban by status; counts by status, conversion rate, pipeline created, won value, cost per converted)
- `components/crm/convert-dialog.tsx`

**Frontend — modify.** `components/crm/shell.tsx` (Campaigns); the bulk bar (add to campaign); `components/crm/command-menu.tsx` (campaigns).

**MCP.** `crm_campaign_members`.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_campaigns.py` | Fields; member needs a company or person; Do not contact skipped and history kept; conversion to new and existing deals sets both links; derived figures; Twenty campaign import from the fixture (FR-CRM-CAMP-01 to -07, PER-02, MIG-01) |
| `tests/test_crm_redaction.py` (extend) | Campaign `budget`, `budget_in_inr`, `budget_fx_rate` and the derived values absent for Viewers, checked against the test's own denylist |
| `tests/test_crm_isolation.py` (extend) | Campaign and member routes |
| `tests/test_crm_search.py` (extend) | Search finds a campaign by name |
| `tests/test_crm_mcp.py` (extend) | `crm_campaign_members` |
| `tests/db/test_crm_campaigns_db.py` | A member in A pointing at a company, person or deal in B is refused; `crm_merge_companies` moves campaign members to the survivor; `crm_search` returns a campaign |
| `frontend/e2e/crm.spec.ts` (extend) | Journey 4.9 conversion |

**Done when.**
- [ ] All gates
- [ ] Journey 4.9 by hand

---

## S11b — Dashboards, CSV import and export (1b)

**Goal.** The fixed dashboard per workspace; CSV import with mapping and fixes
before writing; CSV and JSON export; the last MCP tool.

**Depends on.** S11a.

**Spec.** FR-CRM-DASH-01 to -04; FR-CRM-IO-01 to -05; FR-CRM-LIST-06
(bookings targets); FR-CRM-CAP-02 (on the Labs dashboard); FR-CRM-VIEW-07
(export); FR-CRM-UI-02 (Dashboard); FR-CRM-ACC-11 (dashboard, export);
FR-CRM-CHG-13; FR-CRM-MCP-01 (`crm_dashboard`; `crm_import` CSV).

**Migrations.** `043_crm_dashboards_io.sql`: data-model §3.24.

**Backend — create.**
- `app/domain/crm/dashboard.py` (every panel of FR-CRM-DASH-02/03; FY April–March; colours only with a target; partners out of the funnel; time in stage and days to close from `crm_stage_history`; per-owner rows in name order)
- `app/services/crm/dashboard.py`, `app/api/crm/dashboard.py`
- `app/domain/crm/csv_io.py` (mapping, per-row validation, relations by name, domain or email, skip, update or create; formula-injection neutralising for export: a cell starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'`)
- `app/services/crm/export.py`, `app/api/crm/export.py` (view CSV with its filters and columns; workspace JSON, optionally one `object`; both capped at 5 MB with the 413 sentence of contracts §2.10; both audited)

**Backend — modify.**
- `app/services/crm/importer.py` and `app/api/crm/imports.py`: `csv` kind with mapping and saved mappings
- `app/api/crm/settings.py`: `/crm/targets`
- `app/mcp/crm.py`: `crm_dashboard`; `crm_import` CSV

**Frontend — create.**
- `app/(crm)/crm/[ws]/dashboard/page.tsx` and `components/crm/dashboard-panels.tsx` (recharts through `components/shared/charts.tsx`; tables stay beside charts; per-owner panel in name order)
- `components/crm/csv-import-wizard.tsx` (upload, map, preview, fix rows in place, choose per-row action, confirm)
- `components/crm/export-menu.tsx`
- Settings tab Targets

**Frontend — modify.** `components/crm/shell.tsx` (Dashboard); the bulk bar (export).

**MCP.** `crm_dashboard`.

**Tests.**

| File | Covers |
|---|---|
| `tests/test_crm_dashboard.py` | Every panel against a hand-built fixture; FY bounds; no colour without a target; Academy panels; member sees own numbers only; Viewer 403; per-owner name order; Labs capacity for admins and staff members only (FR-CRM-DASH-01 to -04, CAP-02; **AC** "time in stage is correct on the dashboard") |
| `tests/test_crm_csv_io.py` | Duplicates on domain and email caught before any write; skip, update and create per row; relations by name, domain or email; pipeline rules on imported rows; saved mappings; export keeps the view's filters and columns and sets the headers; a company named `=HYPERLINK("http://x","y")` exports as `'=HYPERLINK(…)`; an export past 5 MB is 413 with the sentence; workspace JSON admin-only, whole or per object; both audited (FR-CRM-IO-01 to -05; **AC** "CSV import catches duplicate domains…", "A CSV export downloads as a CSV file…") |
| `tests/test_crm_redaction.py` (extend) | Dashboard and export absent for Viewers (who cannot export or open the dashboard at all) |
| `tests/test_crm_isolation.py` (extend) | Dashboard and export routes |
| `tests/test_crm_mcp.py` (extend) | `crm_dashboard`; `crm_import` with CSV |
| `frontend/e2e/crm.spec.ts` (extend) | A view exported to CSV downloads with its name |

**As built.** Beyond the lists above: `043` also adds `crm_record_export()`
(an export's audit row; data-model §3.24); `services/crm/store.py`,
`schemas/crm.py`, `services/crm/views.py` (`view_for`), `api/crm/__init__.py`
and `tests/crm_fake.py` carry the new names; `api/crm/capacity.py` reads its
week rule from `domain/crm/dashboard.capacity_weeks`, so the dashboard and
`/crm/capacity` show the same weeks; `tests/db/test_crm_dashboards_io_db.py`
proves 043 and quickstart §3 S11 against the real database; the frontend
also touches `lib/api/backend.ts` (`download` takes a POST body),
`components/shared/charts.tsx` (`BarsByLabel`), `components/crm/object-list.tsx`
(the export menu) and `settings/import/page.tsx` (the CSV wizard beside the
Twenty importer). CSV import takes companies, people and deals (contracts
§2.9, as built).

**Done when.**
- [ ] All gates
- [ ] Journey 4.10 by hand
- [ ] **Phase 1b is complete**: every 1b FR in the coverage table has its tests passing; all 18 `crm_*` tools exist

---

## S12 — Hardening: acceptance sweep, isolation, redaction, performance, docs, spec amendments

**Goal.** Prove every acceptance criterion with a named test, prove isolation
and redaction across every route and tool, measure 10k-row performance, write
the living documentation, and amend the specs the CRM changes.

**Depends on.** S11b.

**Spec.** Spec §11 (all); NFR-CRM-01 to -11; FR-CRM-CHG-07 (amend `004`), and
the amendments of FR-CRM-CHG-01 to -17; D16 (not code: noted in docs only).

**Migrations.** None, unless the 10k check shows a missed index; then
`044_crm_performance_indexes.sql` with the measured query named in its header.

**Backend — create.**
- `tests/test_crm_acceptance.py`: one test per line of spec §11. Each runs the scenario end to end through the routes (with the fake store), or asserts that the named covering test exists and is not skipped, so the AC list cannot drift from the suite.
- `tests/test_crm_isolation_sweep.py`: generated from `app.routes`. For every `/api/v1/crm/` route with a path id, a caller in workspace A with B's id → 404. For every list, query, search, Today, export, dashboard, notification and digest builder, B's seeded marker strings never appear in A's answer. The same through every `crm_*` tool via the in-process bridge (FR-CRM-WS-02, D3).
- `tests/test_crm_redaction_sweep.py`: generated: every `GET` and `POST /query`-like CRM route and every READ tool as a Viewer; a recursive walk finds no amount key, and every amount filter or sort is refused (FR-CRM-ACC-11).
- `tests/test_crm_mcp.py::test_exactly_the_eighteen_crm_tools` (final set equality, capitals lines, CONFIRM FIRST on writes, annotations, one `_api` call per invocation, and the purposes of FR-CRM-MCP-02 each served by a named tool).
- `tests/db/test_crm_security_db.py`: every table in `public` (not only `crm_*`) has RLS on and no grants for `anon`/`authenticated`; every CRM function denies `EXECUTE` to them; the audit trigger is on every record and config table; every reference between `crm_*` tables is a composite FK on `(workspace_id, …)` or carries `crm_check_refs()` (read from `pg_constraint` and `pg_trigger`).
- `backend/scripts/simulations/crm_load_10k.py` (seed, time through the API, clean up; quickstart §5).

**Backend — modify.** Only what the sweeps or the performance check expose, each with its own test.

**Frontend — modify.** Only what the e2e sweep exposes. `e2e/crm.spec.ts`: a phone-width pass over Today, a list, a record and the kanban (FR-CRM-UI-06), and keyboard-only use of the kanban and command menu (NFR-CRM-06).

**Docs — create or modify.**
- `docs/architecture/crm.md` (create): how the CRM works today: the request path and the CRM guard, deny-by-default for `crm_only`, the browser's lack of any table grant, the write path and audit trigger, workspace-scoped references, transition functions and their re-checks, redaction, TVA lifecycle and readers (who may link a project), capacity, jobs and queues, the MCP tools, the audit `channel` as a self-reported label never used for authorization, and the three easy-to-get-wrong things (`crm.actor`, the function grants, the counting-allocations reader)
- `docs/guides/09-crm.md` (create): for sellers: signing in, Today, records, the pipeline and what each stage needs, Drive links, Claude. Public: no names, no figures, no screenshots with data (the e2e "no seeded names in /help" test must pass)
- `docs/guides/10-crm-admin.md` (create): workspace admins: inviting, roles, lists, probabilities, FX rates, the allow-list (read-only; a TVA admin changes it), trash, audit, import, mailboxes (your own address; delegates through a TVA admin), automations, targets
- `docs/guides/01-getting-started.md` (CRM link row), `docs/guides/05-for-managers.md` (CRM-managed projects; released allocations), `docs/guides/06-for-admins.md` (account types; CRM workspaces panel), `docs/guides/08-connecting-claude.md` (the `crm_*` tools by role; CRM-only people connect from `/crm/account`)
- `docs/operations/deployment.md`: migrations 025–043 and the release order (migrate; then `api`, `worker`, `beat` and `worker-crm-sync` from one commit); `GOOGLE_SERVICE_ACCOUNT_JSON` (raw or base64; `worker-crm-sync` **only**) and `CRM_GMAIL_CONFIGURED=true` on `api`, `beat` and `worker` once it is set; creating the `worker-crm-sync` Railway service with `PROCESS_TYPE=worker_crm_sync`, started only when sync is switched on (cost note, Q-74); the SMTP sender precondition for CRM-only email (Q-73); `/health/jobs` for an external monitor; the `db` CI job; the "never commit `supabase/config.toml`" rule; repointing the two "CRM follow-up sweep" Claude tasks at cutover (D16)
- `docs/README.md`: index the new documents
- Help content: `frontend/lib/help-content.generated.json` is generated by `scripts/build-guides.mjs` on `prebuild` and is gitignored. Confirm the new guides render at `/help/09-crm` and `/help/10-crm-admin`; nothing to commit for it.

**Spec amendments (dated "Amended 14 October 2026 by `007`").**
- `specs/004-mcp/spec.md` FR-MCP-01: "…except CRM routes, which are covered by the `crm_*` tools of `007` FR-CRM-MCP-01." §2 G-1: a signed-in person need not be an employee. (FR-CRM-CHG-07)
- `specs/001-leave-calendar/spec.md` FR-AUTH-02/03 (workspace admins create CRM-only accounts by invitation), FR-AUTH (signed-in people need not be employees), NFR-03 (CRM display and digest time zone exception) (CHG-01, -12, -14)
- `specs/002-timesheets/spec.md` FR-PROJ-07 (staff-only lead), FR-PROJ-08 (CRM-controlled status), FR-ALLOC (released allocations; staff-only), FR-ANALYTICS-06 (one capacity service) (CHG-02, -03, -05, -17)
- `specs/003-management/spec.md` FR-ROLE-08, FR-RES, FR-DASH (CHG-02, -03, -04)
- `specs/005-ctc-timeline/spec.md` FR-PNL, FR-PNL-05 (released and archived excluded; archived tentative out of the pipeline) (CHG-03, -04)
- `specs/006-ops/spec.md` FR-UTIL-03, FR-HIRE, FR-DIGEST, FR-STMT-02 (CSV now downloads) (CHG-03, -08)
- Each amendment is one dated line beside the FR it changes, pointing to the `007` FR id. Nothing is restated.

**Tests.** The files above. The performance script's p95 figures (quickstart
§5) for list, kanban, search, record page and Today at 10k rows, all under
1 s (NFR-CRM-01; **AC** "Table and kanban views with saved filters load in
under 1 second for 10,000 companies").

**As built.** The 10k check found no missed index, so there is no
`044_crm_performance_indexes.sql`; the security sweep found that
`crm_notifications.record_id` was the one polymorphic reference without
`crm_check_refs()`, so `044_crm_notification_refs.sql` adds it (data-model
§3.19) and `tests/crm_fake.py` keeps the same rule. The guides render at
`/help/crm` and `/help/crm-admin` (`scripts/build-guides.mjs` drops the
number from the slug). Beyond the lists above: `tests/test_mcp.py` pins the
coverage test to `004`'s amended FR-MCP-01, and `docs/guides/05-for-managers.md`
lost a seeded name the public help test forbids.

**Done when.**
- [ ] All gates
- [ ] `test_crm_acceptance.py` has one passing test per §11 line, and none is skipped (email ones run against the fake Gmail when no credential exists, and the commit says so)
- [ ] The isolation and redaction sweeps pass for every CRM route and every `crm_*` tool
- [ ] The 10k p95 figures are under 1 s and recorded in the commit message
- [ ] The docs build at `/help`, the e2e help test passes, and `docs/README.md` indexes the new files
- [ ] The spec amendments are in place, and `004`'s MCP coverage test matches its amended text
- [ ] Deployment runbook dry-read: someone following `deployment.md` alone could release the CRM

---

## Coverage — spec areas to slices

| Spec area | Slices |
|---|---|
| §5.1 Workspaces (WS-01–06) | S1b; WS-05 S5; WS-06 S3/S4 |
| §5.2 Accounts (ACC-01–15) | ACC-01, -03 S1a; -02, -10, -11 mechanism S1b; -04 to -09, -12 to -15 S1c; ACC-03 project facts S5; ACC-11 S3–S11b per object |
| §5.3 Audit (AUD-01–06) | S1b mechanism; every record slice adds its tables; AUD-05 S3 |
| §5.4 Lists (LIST-01–09) | 01–06, 09 S1b (03–05 seed values asserted per workspace); LIST-07/08 S3–S4; LIST-06 1b keys S9–S11b |
| §5.5 FX (FX-01–06) | FX-02 S1b; FX-01/03/04/06 S4; FX-05 S5 |
| §5.6–5.11 Records | S3 (companies, people, links); S4 (deals, tasks, notes) |
| §5.12 Pipeline (PIPE-01–15) | S3 (01); S4 (02–15, CRM side); S5 (TVA effects of 09, 12, 13, 15); S10 (02 email) |
| §5.13 Duplicates, merge (DUP-01–04) | S3 (01–02); S7 (03–04); S8 and S11b (02 in imports) |
| §5.14 Trash (DEL-01–04) | S3; S4 (deal choices); S5 (TVA refusals) |
| §5.15 Area (UI-01–07) | S1c; each slice adds its sidebar entries |
| §5.16 Views (VIEW-01–11) | S6a (server), S6b (UI); VIEW-07 campaign S11a, export S11b; VIEW-11 1b views S9, S11a |
| §5.17 Record pages (REC-01–04) | S3 frame; S4 tasks and notes; S5 TVA; S9 flag dot; S10 Emails |
| §5.18 Today (TODAY-01–04) | S7; email sections S10 |
| §5.19 Command menu (SRCH-01–03) | S7 |
| §5.20 TVA integration (TVA-01–18) | S5 |
| §5.21 Capacity (CAP-01–03) | S2; reachable from Pipeline S6b; dashboard S11b |
| §5.22 Twenty importer (MIG-01–08) | S8; campaigns S11a |
| §5.23 MCP (MCP-01–11) | S1b, S3–S8, S10, S11a, S11b; MCP-02 purposes checked in S12 |
| §6.1 Gmail sync (SYNC-01–10) | S10 |
| §6.2 Flags (FLAG-01–05) | S9; email facts S10 |
| §6.3 Job log (JOB-01–03) | S9 |
| §6.4 Notifications (NOTIF-01–05) | S9; reply-owed events S10 |
| §6.5 Automations (AUTO-01–02) | 1 S4–S5; 2–3 S10; 4–7 S9; 8 S11a; 9 S4; 10 S5 |
| §6.6 Campaigns (CAMP-01–07) | S11a |
| §6.7 Dashboards (DASH-01–04) | S11b |
| §6.8 CSV (IO-01–05) | S11b (proxy in S8) |
| §7 Changes to TVA (CHG-01–17) | 01, 02, 12: S1a. 09, 15: S1b. 11, 14: S1c. 03, 04, 17: S2. 05, 06, 10, 16: S5. 07: S1b (exclusion) and S12 (amendment). 08: S8. 13: S7 and S11b. Amendments to each named spec: S12 |
| §8 NFRs | 01 S6a/S6b/S7/S12; 02 S1b/S10; 03 S4/S5/S7; 04 S9/S10; 05 S11b (export); 06 S6b/S7/S12; 07 every UI slice; 08 every slice; 09 every slice; 10 every slice and S12; 11 plan §2.1 |

## MCP tools by slice (18)

| Slice | Tools | Running total |
|---|---|---|
| S1b | `crm_workspaces` | 1 |
| S3 | `crm_get`, `crm_create`, `crm_update` | 4 |
| S4 | `crm_set_next_step`, `crm_add_task`, `crm_complete_task`, `crm_add_note`, `crm_move_stage` | 9 |
| S5 | `crm_handoff` | 10 |
| S6a | `crm_list` | 11 |
| S7 | `crm_today`, `crm_search`, `crm_merge` | 14 |
| S8 | `crm_import` | 15 (all of 1a) |
| S10 | `crm_run_email_sync` | 16 |
| S11a | `crm_campaign_members` | 17 |
| S11b | `crm_dashboard` | 18 |

`crm_audit` is Phase 2 and is not built (FR-CRM-MCP-01).
