# Sales CRM — Data model

| | |
|---|---|
| **Feature** | `007-crm` |
| **Spec** | [`spec.md`](spec.md) §9 (logical); this file chooses tables, types and names |
| **Plan** | [`plan.md`](plan.md) |

Everything here lands as numbered files in `supabase/migrations/`, in the
order of §1. Existing migrations are not edited.

---

## 1. Migrations

| File | Slice | Contents |
|---|---|---|
| `025_crm_accounts.sql` | S1a | `profiles` + `account_type`, `timezone`, `number_format`, `deactivated_reason`, CRM-only consistency CHECK; `tva_staff_only()` trigger on `allocations.user_id`, `projects.lead_id`, `time_entries.user_id`, `profiles.lead_id`; revoke every table privilege in `public` from `anon` and `authenticated` (§4.0) |
| `026_crm_workspaces.sql` | S1b | `crm_workspaces`, `crm_memberships`, `crm_workspace_settings`, `crm_select_options`, `crm_fx_rates`; `audit_log` + `workspace_id`, widened `actor_label`, `channel`; `crm_audit_row()` and its triggers; seeds (§7.1–7.3) written under the system actor |
| `027_crm_write_functions.sql` | S1b | `crm_apply()`, `crm_check_refs()`, `crm_remove_membership()`, `crm_invite()`, `crm_convert_account()`; grants (§5.12) |
| `028_allocation_release.sql` | S2 | `allocations` + `released_at`, `released_by`, `release_reason`, `released_by_opportunity_id` (FK added in 032); index for counting reads |
| `029_crm_companies_people.sql` | S3 | `pg_trgm`; `crm_companies`, `crm_company_domains`, `crm_people`, `crm_drive_links`; trigram and filter indexes; `crm_purge()` first form (Drive links only, the one link table that exists); `crm_similar()` (the possible-duplicate query of FR-CRM-DUP-02, which PostgREST cannot express) |
| `030_crm_deals_tasks_notes.sql` | S4 | `crm_opportunities`, `crm_opportunity_contacts`, `crm_stage_history`, `crm_tasks`, `crm_task_links`, `crm_notes`, `crm_note_links`, `crm_note_mentions`; `CREATE OR REPLACE crm_purge()` adding task and note links |
| `031_crm_deal_transition.sql` | S4 | `crm_deal_transition()`, CRM-side branches only |
| `032_crm_tva_links.sql` | S5 | `projects` + `crm_company_id`, `source_opportunity_id` (unique), `crm_archived_at`; FK on `allocations.released_by_opportunity_id` |
| `033_crm_lifecycle.sql` | S5 | `CREATE OR REPLACE crm_deal_transition()` with the TVA branches; `crm_link_project()`; `CREATE OR REPLACE crm_purge()` adding `projects.crm_company_id`; trigger `crm_opportunities_project_company`: a deal's project follows its `company_id` (`crm_company_id`, `client`, TVA audit row) |
| `034_crm_views.sql` | S6a | `crm_object_fields` (column registry, seeded), `crm_saved_views` (seeded), `crm_favourites`, `crm_query()`; `CREATE OR REPLACE crm_purge()` adding favourites |
| `035_crm_search_merge.sql` | S7 | `crm_search()`, `crm_merge_companies()` |
| `036_crm_imports.sql` | S8 | `crm_import_runs`; `twenty_id` on `crm_tasks` and `crm_notes`; `crm_similar_many()` (the dry run's possible-duplicate check for many names in one call); `crm_record_import()` (a run and its audit row together) |
| `037_crm_jobs_notifications.sql` | S9 | `crm_job_runs`, `crm_notifications`, `crm_digest_sends`; settings seeds for thresholds and automations; `CREATE OR REPLACE crm_remove_membership()` deleting the person's notifications for the workspace |
| `038_crm_flags.sql` | S9 | Flag columns and indexes on companies, people, opportunities, tasks; `crm_object_fields` rows for them |
| `039_crm_email.sql` | S10 | `crm_mailboxes`, `crm_email_messages`, `crm_email_links`; `email_sync` setting seed; `crm_store_emails()`, `crm_remove_emails()` (the email log's one writer); `CREATE OR REPLACE crm_audit_row()` skipping a mailbox's cursor columns; `crm_flag_facts()` replaced by a four-argument form with people and email facts; `CREATE OR REPLACE crm_purge()` adding email links; `CREATE OR REPLACE crm_merge_companies()` moving email links |
| `040_crm_mailbox_owner_left.sql` | S10 | `CREATE OR REPLACE crm_remove_membership()` also pausing the removed person's mailboxes in the workspace (`is_active = false`, audited `mailbox.updated`) |
| `041_crm_campaigns.sql` | S11a | `crm_campaigns`, `crm_campaign_members`; `crm_opportunities.source_campaign_id` FK; campaign select lists (the `list_key` CHECK widened), field registry rows and seeded member view; `CREATE OR REPLACE crm_merge_companies()` moving campaign members; `CREATE OR REPLACE crm_search()` adding campaigns; (as built) `CREATE OR REPLACE crm_query()`, `crm_query_rank()` for the two lists and `crm_purge()` for campaigns and their members |
| `042_crm_campaign_conversions_kept.sql` | S11a | `CREATE OR REPLACE crm_merge_companies()` and `crm_purge()` keeping a Converted campaign membership when two memberships of one campaign collide (§3.23) |
| `043_crm_dashboards_io.sql` | S11b | `crm_bookings_targets`, `crm_csv_mappings`; (as built) `crm_record_export()`, the audit row of an export |
| `044_crm_notification_refs.sql` | S12 | (as built, found by the security sweep) trigger `crm_notifications_check_refs`: `crm_check_refs()` on a notification's `record_type` / `record_id`, on insert and on an update of those columns |

A function that touches a table always arrives in, or is replaced in, the
migration that creates that table or a later one. PL/pgSQL resolves table
names at execution time, so a function naming a table that does not exist yet
would fail every call with "relation does not exist".

Each file opens with `-- NNN_name.sql` and a prose rationale citing FR and Q
ids, as `001`–`024` do. DDL is idempotent (`IF NOT EXISTS`, `DROP … IF EXISTS`
then `CREATE`, `ON CONFLICT DO NOTHING`).

---

## 2. Conventions for every `crm_*` table

| Rule | Detail |
|---|---|
| Key | `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`. Link tables have one too, with a `UNIQUE` on the natural pair, so one generic audit trigger and `crm_apply` fit every table. |
| Workspace | `workspace_id uuid NOT NULL REFERENCES crm_workspaces(id) ON DELETE RESTRICT` (workspaces are never deleted). Indexed, leading every composite index. |
| References inside the workspace | Every `crm_*` table that is referenced by another has `UNIQUE (workspace_id, id)`. Every reference between `crm_*` tables is a **composite** FK on the pair, so a row in workspace A cannot point at a row in B: e.g. `FOREIGN KEY (workspace_id, company_id) REFERENCES crm_companies (workspace_id, id) ON DELETE SET NULL (company_id)` (PG15 column-list form, so `workspace_id` is never nulled). This covers `crm_people.company_id`, `crm_opportunities.company_id` and `point_of_contact_id`, `crm_opportunity_contacts.opportunity_id` and `person_id`, `crm_companies.merged_into_id`, `crm_company_domains.company_id`, the link tables' `task_id` / `note_id` / `message_id`, `crm_note_mentions.note_id`, `crm_stage_history.opportunity_id`, `crm_campaign_members.campaign_id`, `company_id`, `person_id` and `converted_opportunity_id`, `crm_opportunities.source_campaign_id`, `crm_email_messages.mailbox_id`, and **every `*_id` that points at `crm_select_options`** (`account_type_id`, `relationship_status_id`, `industry_id`, `institution_type_id`, `size_id`, `source_id`, `channel_id`, `deal_type_id`, `engagement_model_id`, `programme_id`, `lost_reason_id`, `role_id`, `category_id`, `type_id`, `status_id`). References that cannot be FKs (polymorphic `record_type` / `record_id` links, `uuid[]` option arrays such as `channel_ids`, `crm_favourites.target_id`) are checked by the `crm_check_refs()` trigger (§5.14). That an option belongs to the right `list_key` is checked in Python. |
| People | Every person reference is `uuid REFERENCES profiles(id) ON DELETE SET NULL` (`created_by`, `updated_by`, `owner_id`, …), except where noted `NOT NULL`. That an owner, assignee or mention is a member of the workspace is checked in Python (FR-CRM-CO-01 "a member of the workspace"), since membership can end later without rewriting history. |
| Stamps | `created_at timestamptz NOT NULL DEFAULT now()`, `created_by`, `updated_at timestamptz NOT NULL DEFAULT now()`, `updated_by`. `<table>_touch BEFORE UPDATE … EXECUTE FUNCTION touch_updated_at()` (001). `crm_apply` stamps `created_by` and `updated_by` from the actor. |
| Soft delete | Record tables (companies, people, opportunities, tasks, notes, drive links, campaigns, members): `deleted_at timestamptz`, `deleted_by uuid`, `deleted_group uuid` (shared by everything deleted in one action, so a restore brings it back together, FR-CRM-DEL-02). Every list index is partial `WHERE deleted_at IS NULL`. |
| Kanban order | Objects shown on a kanban carry `position numeric NOT NULL DEFAULT 0` (fractional; a drop takes the midpoint of its neighbours). |
| Text | `CHECK (btrim(x) <> '')` on required text; `CHECK (char_length(x) <= N)` on short text (name 200, label 60, next step 500); long text ≤ 20,000. |
| Closed lists | Fixed keys are `text` + inline `CHECK (x IN (…))`, as TVA does. Admin-editable lists are rows in `crm_select_options`, referenced by `uuid` (single) or `uuid[]` (multi; validated in Python, GIN-indexed). |
| Money | `numeric(14,2) CHECK (x >= 0)`; rates `numeric(12,6) CHECK (x > 0)`. Decimal strings at the API. |
| Security | `ALTER TABLE … ENABLE ROW LEVEL SECURITY;` with no policy, and `REVOKE ALL ON … FROM authenticated, anon;` in the creating migration (constitution §I, as 010+). |
| Audit | Record and configuration tables get `CREATE TRIGGER <table>_crm_audit AFTER INSERT OR UPDATE OR DELETE … FOR EACH ROW EXECUTE FUNCTION crm_audit_row()`. Log tables (§3 notes which) do not. |

---

## 3. New tables

### 3.1 `crm_workspaces` — FR-CRM-WS-01, -05, -06, FR-CRM-ACC-06 (026)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `key` | text | NOT NULL UNIQUE, `CHECK (key ~ '^[a-z][a-z0-9_]{1,30}$' AND key NOT IN ('account'))`. Seeds `labs`, `academy`; used in URLs (`/crm/labs/…`) and the MCP `workspace` argument. `account` is the static `/crm/account` segment, which would shadow such a workspace; any later static `/crm/<segment>` joins this list |
| `name` | text | NOT NULL UNIQUE, btrim, ≤ 80 |
| `tva_linked` | boolean | NOT NULL DEFAULT false. Partial unique index `crm_workspaces_one_tva_linked ON (tva_linked) WHERE tva_linked` (FR-CRM-WS-05) |
| `has_entity` | boolean | NOT NULL DEFAULT false (FR-CRM-WS-06) |
| `allowed_domains` | text[] | NOT NULL DEFAULT '{}', `CHECK (cardinality(allowed_domains) <= 50)`. Lower-case, validated in Python |
| `created_at`, `updated_at`, `updated_by` | | stamps |

Audited. TVA admins update `allowed_domains` only, through
`/crm/tva-admin/workspaces/{id}/allowed-domains` (plan §5.13; public webmail
domains refused in Python); `name` and `key` are changed by migration.

### 3.2 `crm_memberships` — FR-CRM-WS-03, -04, FR-CRM-ACC-02, -12 (026)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK |
| `user_id` | uuid | NOT NULL `REFERENCES profiles(id) ON DELETE CASCADE` |
| `role` | text | NOT NULL `CHECK (role IN ('admin','member','viewer'))` |
| `last_used_at` | timestamptz | NULL. The caller's last-used workspace is their membership with the greatest value (FR-CRM-WS-04, D13). Written by `POST /crm/workspaces/{key}/use`, outside the audit trigger's interest (the trigger skips an update that changes only this column) |
| stamps | | |

`UNIQUE (workspace_id, user_id)`; index `(user_id)`. Audited
(`membership.invited`, `membership.role_changed`, `membership.removed`).

### 3.3 `crm_workspace_settings` — FR-CRM-LIST-06, FR-CRM-PIPE-11 (026; keys added in 037, 039)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK |
| `key` | text | NOT NULL `CHECK (key IN ('stage_probabilities','flag_thresholds','automations','email_sync'))` (037 and 039 widen it by drop-and-add) |
| `value` | jsonb | NOT NULL, `CHECK (jsonb_typeof(value) = 'object')`. Shape validated per key in `domain/crm/settings.py` |
| `updated_at`, `updated_by` | | |

`UNIQUE (workspace_id, key)`. Audited (`setting.updated`).

### 3.4 `crm_select_options` — FR-CRM-LIST-01, -02, -09 (026; lists added in 041)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK |
| `list_key` | text | NOT NULL, `CHECK (list_key IN (…))`, one of §3.4.1 |
| `system_key` | text | NULL, `CHECK (system_key ~ '^[a-z][a-z0-9_]*$')`. The fixed key a rule, an MCP argument or an import mapping uses (Q-71) |
| `label` | text | NOT NULL, btrim, ≤ 60 |
| `color` | text | NULL, `CHECK (color IN ('gray','red','orange','amber','green','teal','blue','indigo','purple','pink'))`; mapped to CSS tokens in the frontend |
| `position` | integer | NOT NULL |
| `is_default` | boolean | NOT NULL DEFAULT false |
| `is_active` | boolean | NOT NULL DEFAULT true |
| `is_fixed` | boolean | NOT NULL DEFAULT false. True for stages, lead stages, task status, task bucket (no add, remove, reorder or deactivate, Q-30) |
| stamps | | |

Constraints: `UNIQUE (workspace_id, list_key, system_key)` (partial, `WHERE
system_key IS NOT NULL`); `UNIQUE (workspace_id, list_key, lower(label))`;
`UNIQUE (workspace_id, list_key) WHERE is_default` (one default per list);
`CHECK (NOT is_fixed OR system_key IS NOT NULL)`; `CHECK (system_key IS NULL
OR is_active)` (rule-bearing options cannot be deactivated, FR-CRM-LIST-09).
Options are never deleted. Audited (`option.created`, `option.updated`).

#### 3.4.1 Lists

| `list_key` | Stored on the record as | Fixed | Workspaces |
|---|---|---|---|
| `company.lead_stage` | text key | yes | both |
| `opportunity.stage` | text key | yes | both |
| `task.status` | text key | yes | both |
| `task.bucket` | text key | yes | both |
| `company.account_type` | uuid; rule keys `client`, `technology_partner`, `channel_partner`, `training_partner` | no | both |
| `company.relationship_status` | uuid | no | both |
| `channel` | uuid (company: uuid[]; opportunity, campaign: uuid) | no | both |
| `company.industry` | uuid | no | both (empty) |
| `company.institution_type` | uuid | no | Academy |
| `company.size` | uuid | no | both |
| `source` | uuid; every option keyed (`manual`, `import`, `email_sync`, `campaign`, `web_form`, `api`) | no | both |
| `opportunity.deal_type` | uuid | no | both |
| `opportunity.engagement_model` | uuid; rule key `discovery_phase` (offers the `pre` phase, FR-CRM-TVA-07) | no | both |
| `opportunity.programme` | uuid | no | Academy (empty) |
| `opportunity.lost_reason` | uuid; every option keyed | no | both |
| `opportunity.contact_role` | uuid | no | both |
| `task.category` | uuid; rule key `delivery_handoff` (Labs) | no | both |
| `campaign.type`, `campaign.status` (041) | uuid | no | both |
| `campaign_member.status` (041) | text key | yes | both |

### 3.5 `crm_fx_rates` — FR-CRM-FX-02, -03 (026)

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `workspace_id` | uuid | NOT NULL FK |
| `currency` | text | NOT NULL `CHECK (currency IN ('AUD','USD'))` (INR converts at 1) |
| `month` | date | NOT NULL `CHECK (extract(day FROM month) = 1)` |
| `rate` | numeric(12,6) | NOT NULL `CHECK (rate > 0)`; INR per 1 unit |
| stamps | | |

`UNIQUE (workspace_id, currency, month)`. The lookup is `month <= $m ORDER BY
month DESC LIMIT 1`, served by that index. Audited (`fx_rate.set`).

### 3.6 `crm_companies` — FR-CRM-CO-01 (029; flags 038)

| Column | Type | Constraints / notes |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `name` | text | NOT NULL, ≤ 200 |
| `entities` | text[] | NOT NULL DEFAULT '{}', `CHECK (entities <@ ARRAY['nunnari_labs','deepweaver']::text[])`; Labs only (refused 422 elsewhere, in Python) |
| `lead_stage` | text | NOT NULL DEFAULT 'new', `CHECK (lead_stage IN ('new','contacted','nurturing','qualified','deal_open','customer','partner','disqualified'))` |
| `account_type_id` | uuid | FK `crm_select_options` |
| `relationship_status_id` | uuid | FK |
| `channel_ids` | uuid[] | NOT NULL DEFAULT '{}' |
| `owner_id` | uuid | NOT NULL FK profiles |
| `next_touch_on` | date | |
| `industry_id`, `institution_type_id`, `size_id`, `source_id` | uuid | FK `crm_select_options` |
| `country`, `city` | text | ≤ 100, no default |
| `address` | jsonb | `{street, city, state, country, postal_code}`, `CHECK (address IS NULL OR jsonb_typeof(address) = 'object')` |
| `website`, `linkedin` | jsonb | Links: `{"primary": {"url","label"}, "others": [...]}`; every URL `https://` or `http://`, checked in Python |
| `description` | text | ≤ 20,000 |
| `last_inbound_at`, `last_outbound_at`, `last_contact_at` | timestamptz | System-written only (FR-CRM-CO-03) |
| `merged_into_id` | uuid | FK `crm_companies(id)`. Set on the merged-away company; `GET /crm/companies/{id}` follows it (FR-CRM-DUP-03) |
| `twenty_id` | text | `UNIQUE (workspace_id, twenty_id) WHERE twenty_id IS NOT NULL` |
| `position` | numeric | kanban |
| stamps, soft delete | | |

Indexes (all `WHERE deleted_at IS NULL` unless said): `(workspace_id,
lower(name))`; GIN `(lower(name) extensions.gin_trgm_ops)`; `(workspace_id,
lead_stage, position)`; `(workspace_id, owner_id)`; `(workspace_id,
updated_at DESC)`; GIN `(channel_ids)`; `(workspace_id, deleted_at) WHERE
deleted_at IS NOT NULL` (Trash).

### 3.7 `crm_company_domains` — FR-CRM-DUP-01 (029)

The primary domain and the additional domains, in one table so one unique
index covers them all.

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `company_id` | uuid | NOT NULL FK `crm_companies ON DELETE CASCADE` |
| `domain` | text | NOT NULL `CHECK (domain = lower(domain) AND domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$')` |
| `is_primary` | boolean | NOT NULL DEFAULT false |
| `created_at`, `created_by` | | |

`UNIQUE (workspace_id, domain)`: a trashed company still holds its domains, so
a clash names it "(in Trash)". `UNIQUE (company_id) WHERE is_primary`. GIN
`(domain extensions.gin_trgm_ops)`. Audited.

### 3.8 `crm_people` — FR-CRM-PER-01 (029; flags 038)

| Column | Type | Constraints / notes |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `first_name` | text | NOT NULL, ≤ 100 |
| `last_name` | text | ≤ 100 |
| `company_id` | uuid | FK `crm_companies` (nullable) |
| `primary_email` | text | `CHECK (primary_email = lower(primary_email))`; EmailStr-validated in Python. `UNIQUE (workspace_id, primary_email) WHERE primary_email IS NOT NULL` |
| `additional_emails` | text[] | NOT NULL DEFAULT '{}'; GIN index (email-sync match) |
| `phones` | jsonb | NOT NULL DEFAULT '[]', array of `{country_code: "+91", number, whatsapp: bool}` |
| `job_title`, `city` | text | ≤ 200, ≤ 100 |
| `linkedin` | jsonb | Links |
| `owner_id` | uuid | FK profiles |
| `last_inbound_at`, `last_outbound_at`, `last_contact_at` | timestamptz | System-written |
| `do_not_contact` | boolean | NOT NULL DEFAULT false |
| `twenty_id` | text | unique per workspace when set |
| `position` | numeric | |
| stamps, soft delete | | |

Indexes: GIN trigram on `lower(first_name || ' ' || coalesce(last_name,''))`
and on `primary_email`; `(workspace_id, company_id)`; `(workspace_id,
owner_id)`; `(workspace_id, updated_at DESC)`.

### 3.9 `crm_opportunities` — FR-CRM-OPP-01, FR-CRM-FX, FR-CRM-PIPE (030; flags 038; campaign FK 041)

| Column | Type | Constraints / notes |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `name` | text | NOT NULL, ≤ 200 |
| `company_id` | uuid | NOT NULL FK `crm_companies` |
| `point_of_contact_id` | uuid | FK `crm_people`; required from Qualification (Python) |
| `entity` | text | `CHECK (entity IN ('nunnari_labs','deepweaver'))`, Labs only |
| `stage` | text | NOT NULL DEFAULT 'discovery', `CHECK (stage IN ('discovery','qualification','proposal','negotiation','closed_won','closed_lost'))` |
| `stage_entered_at` | timestamptz | NOT NULL DEFAULT now(); system-written |
| `amount` | numeric(14,2) | `>= 0` |
| `currency` | text | `CHECK (currency IN ('INR','AUD','USD'))`; `CHECK ((amount IS NULL) = (currency IS NULL))` |
| `amount_in_inr` | numeric(14,2) | stored on every amount or currency save (FR-CRM-FX-03); `CHECK ((amount IS NULL) = (amount_in_inr IS NULL))` |
| `fx_rate_used` | numeric(12,6) | rate behind `amount_in_inr` (1 for INR) |
| `fx_month` | date | month of that rate |
| `won_fx_rate`, `won_fx_month` | numeric(12,6), date | set at Closed Won (FR-CRM-FX-05) |
| `probability` | smallint | NOT NULL `CHECK (probability BETWEEN 0 AND 100)` |
| `expected_close_on` | date | required from Proposal (Python) |
| `next_step` | text | ≤ 500 |
| `next_step_on` | date | |
| `owner_id` | uuid | NOT NULL FK profiles |
| `channel_id`, `deal_type_id`, `engagement_model_id`, `programme_id`, `lost_reason_id` | uuid | FK `crm_select_options` |
| `project_category` | text | `CHECK (project_category IN ('client','poc','product'))`, Labs only |
| `proposal_sent_on` | date | required to enter Negotiation |
| `lost_notes` | text | ≤ 20,000 |
| `lost_from_stage` | text | stage the deal was lost from (FR-CRM-PIPE-12 reopen target); same CHECK as `stage` minus closed values |
| `won_on`, `lost_on` | date | system-written |
| `source_campaign_id` | uuid | (041) FK `crm_campaigns ON DELETE SET NULL` |
| `twenty_id` | text | unique per workspace when set |
| `position` | numeric | |
| stamps, soft delete | | |

Backstop CHECKs (the rules are enforced in Python first; these make "through
every channel" true, FR-CRM-PIPE-10):

```sql
CHECK (deleted_at IS NOT NULL OR stage IN ('closed_won','closed_lost')
       OR (next_step IS NOT NULL AND btrim(next_step) <> '' AND next_step_on IS NOT NULL)),
CHECK (stage <> 'closed_lost' OR lost_reason_id IS NOT NULL),
CHECK (stage NOT IN ('proposal','negotiation','closed_won') OR deleted_at IS NOT NULL
       OR (amount IS NOT NULL AND expected_close_on IS NOT NULL) OR twenty_id IS NOT NULL)
```

The last CHECK lets imported deals through: the dry run reports the violation
(FR-CRM-MIG-04) and the owner fixes it later.

FX integrity CHECKs (FR-CRM-FX-03). Python computes `amount_in_inr`, but a
path that sets `amount` without recomputing (import upsert, bulk set, merge)
must not be able to store a wrong INR value that every total then uses:

```sql
CHECK ((amount IS NULL) = (fx_rate_used IS NULL)),
CHECK (amount IS NULL OR amount_in_inr = round(amount * fx_rate_used, 2)),
CHECK (currency IS NULL OR (currency = 'INR') = (fx_rate_used = 1))
```

`round(numeric, 2)` rounds half away from zero, which equals Python's
half-up for these non-negative amounts (`domain/crm/fx.to_inr`). The importer
reports a deal whose currency has no rate for its month as a conflict in the
dry run ("No USD rate for 2026-09 in Nunnari Labs.") and refuses the apply
until the rate exists (FR-CRM-MIG-04).

There is no TVA project column: the deal's project is `projects WHERE
source_opportunity_id = id` (§4.3). Weighted amount is derived on read
(`amount_in_inr * probability / 100`), never stored.

Indexes: `(workspace_id, stage, position) WHERE deleted_at IS NULL`;
`(workspace_id, owner_id, next_step_on) WHERE stage NOT IN closed AND
deleted_at IS NULL` (Today §2); `(workspace_id, company_id)`;
`(workspace_id, expected_close_on)`; `(workspace_id, won_on)`, `(workspace_id,
lost_on)`; GIN trigram `lower(name)`.

### 3.10 `crm_opportunity_contacts` — "Other contacts … Role in deal" (030)

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `opportunity_id` | uuid | NOT NULL FK `ON DELETE CASCADE` |
| `person_id` | uuid | NOT NULL FK `crm_people ON DELETE CASCADE` |
| `role_id` | uuid | FK `crm_select_options` (`opportunity.contact_role`) |
| `created_at`, `created_by` | | |

`UNIQUE (opportunity_id, person_id)`. Audited.

### 3.11 `crm_stage_history` — FR-CRM-OPP-02, FR-CRM-PIPE-14 (030) — log, no audit trigger

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `opportunity_id` | uuid | NOT NULL FK `ON DELETE CASCADE` |
| `from_stage` | text | NULL on create |
| `to_stage` | text | NOT NULL |
| `at` | timestamptz | NOT NULL DEFAULT now() |
| `by` | uuid | FK profiles |
| `actor_kind` | text | `CHECK (actor_kind IN ('user','system','import','sync'))` |

Index `(workspace_id, opportunity_id, at)`. Written only by
`crm_deal_transition` and `crm_apply` (deal insert), never by the backend
directly. Time in stage and days to close are derived from it (FR-CRM-DASH-02).

### 3.12 `crm_tasks`, `crm_task_links` — FR-CRM-TASK (030; flags 038)

`crm_tasks`

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `title` | text | NOT NULL, ≤ 200 |
| `body` | text | ≤ 20,000 |
| `status` | text | NOT NULL DEFAULT 'todo' `CHECK (status IN ('todo','in_progress','done'))` |
| `due_on` | date | |
| `bucket` | text | `CHECK (bucket IN ('today','this_week','this_month','later'))` |
| `category_id` | uuid | FK `crm_select_options` |
| `assignee_id` | uuid | NOT NULL FK profiles |
| `completed_at` | timestamptz | `CHECK ((status = 'done') = (completed_at IS NOT NULL))` |
| `twenty_id` | text | (036) unique per workspace when set, so a re-import matches it (FR-CRM-MIG-07) |
| `position` | numeric | |
| stamps, soft delete | | |

`crm_task_links`: `id`, `workspace_id`, `task_id` (FK CASCADE), `record_type
text CHECK (record_type IN ('company','person','opportunity','campaign'))`,
`record_id uuid NOT NULL`, `created_at`, `created_by`; `UNIQUE (task_id,
record_type, record_id)`; index `(workspace_id, record_type, record_id)`. The
link is polymorphic, so it has no FK. The service checks that the target
exists in the same workspace, the `crm_check_refs()` trigger (§5.14) checks it
again in the database, and `crm_purge` removes links to a purged record. "At least one link" (FR-CRM-TASK-01) is checked in Python and
re-checked by `crm_apply` for task inserts.

Indexes: `(workspace_id, assignee_id, status, due_on) WHERE deleted_at IS
NULL` (Today §3); `(workspace_id, bucket, position)`.

### 3.13 `crm_notes`, `crm_note_links`, `crm_note_mentions` — FR-CRM-NOTE (030)

`crm_notes`: `id`, `workspace_id`, `body text NOT NULL` (≤ 20,000, markdown),
`kind text NOT NULL DEFAULT 'note' CHECK (kind IN
('note','call','meeting','whatsapp','other'))`, `occurred_on date` (required
when `kind <> 'note'`, `CHECK`), `twenty_id text` (036; unique per workspace
when set, FR-CRM-MIG-07), stamps, soft delete. Index `(workspace_id,
created_at DESC)`.

`crm_note_links`: as `crm_task_links` (`note_id`).

`crm_note_mentions`: `id`, `workspace_id`, `note_id` (FK CASCADE),
`member_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE`,
`created_at`; `UNIQUE (note_id, member_id)`. Record mentions are stored as
note links. A member mention of a non-member is refused 422 in Python
(FR-CRM-NOTE-02).

### 3.14 `crm_drive_links` — FR-CRM-LINK (029)

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `record_type` | text | `CHECK (record_type IN ('company','person','opportunity','task','note','campaign'))` |
| `record_id` | uuid | NOT NULL |
| `url` | text | NOT NULL `CHECK (url ~ '^https://[^\s]+$' AND char_length(url) <= 2000)` |
| `label` | text | NOT NULL, ≤ 200 |
| stamps, soft delete | | |

Index `(workspace_id, record_type, record_id) WHERE deleted_at IS NULL`. No
file content, preview or Drive permission is stored (FR-CRM-LINK-02).

### 3.15 `crm_object_fields` — column registry for views (034)

Read by `crm_query()` and mirrored in `domain/crm/objects.py`. A test compares
the two (S6a).

| Column | Type | Constraints |
|---|---|---|
| `object` | text | `CHECK (object IN ('company','person','opportunity','task','note','campaign','campaign_member'))` |
| `field` | text | the API field name |
| `column_sql` | text | the column (or a fixed expression from a closed set, such as `weighted_amount`, `full_name`, `primary_domain`) |
| `type` | text | `CHECK (type IN ('text','long_text','number','currency','date','datetime','boolean','select','multi_select','fixed_select','person','relation','flag'))` |
| `is_amount` | boolean | NOT NULL DEFAULT false |
| `filterable`, `sortable`, `groupable`, `aggregatable` | boolean | |
| `workspace_scope` | text | `CHECK (workspace_scope IN ('all','tva_linked','not_tva_linked'))` (Entity / Project category vs Institution type / Programme) |

`PRIMARY KEY (object, field)`. Not workspace data. RLS on, no grants, no
audit (changed by migration only).

### 3.16 `crm_saved_views`, `crm_favourites` — FR-CRM-VIEW-05, -11, FR-CRM-UI-02 (034)

`crm_saved_views`

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `object` | text | as `crm_object_fields.object` |
| `name` | text | NOT NULL, ≤ 80 |
| `icon` | text | ≤ 40, a lucide icon name |
| `kind` | text | `CHECK (kind IN ('table','kanban'))` |
| `visibility` | text | `CHECK (visibility IN ('private','shared'))` |
| `owner_id` | uuid | FK profiles; NULL for seeded shared views |
| `config` | jsonb | `{filters, sorts, group_by, kanban_field, columns:[{field,width}], aggregates, card_fields}`, validated by `domain/crm/views.validate_spec` |
| `system_key` | text | seeded views (`companies_all`, `lead_funnel`, …) so MCP and links can name them |
| `is_default` | boolean | `UNIQUE (workspace_id, object) WHERE is_default AND visibility = 'shared'` |
| `position` | integer | |
| stamps | | |

`UNIQUE (workspace_id, object, lower(name), coalesce(owner_id, '00000000-0000-0000-0000-000000000000'))`.
Audited.

`crm_favourites`: `id`, `workspace_id`, `user_id` (FK CASCADE), `target_type
CHECK (target_type IN ('view','record'))`, `record_type` (when a record),
`target_id uuid NOT NULL`, `position integer`, `created_at`; `UNIQUE
(workspace_id, user_id, target_type, target_id)`. Audited.

### 3.17 `crm_import_runs` — FR-CRM-MIG-04, -07, FR-CRM-IO (036) — log

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `kind` | text | `CHECK (kind IN ('twenty','csv'))` |
| `object` | text | NULL for Twenty (all objects) |
| `file_sha256` | text | NOT NULL `CHECK (file_sha256 ~ '^[0-9a-f]{64}$')` |
| `status` | text | `CHECK (status IN ('dry_run','applied','failed'))` |
| `report` | jsonb | counts per object, derived lead stages, conflicts per row, attachments to re-link; no file content |
| `options` | jsonb | `{fixup_next_step: bool, mapping_id, mode: skip|update|create}` |
| `created_by` | uuid | |
| `created_at`, `applied_at` | timestamptz | |

The real run requires a `dry_run` row of the same workspace, kind and
`file_sha256`, created within 24 hours (FR-CRM-MIG-04). Both the dry run and
the real run are audited as `import.dry_run` / `import.applied` (or
`import.failed`), with counts and no row content. They are written by
`crm_record_import(p_actor, p_run)` (036), which adds or marks the run and
writes its audit row in one transaction in the actor's workspace:
`crm_apply`'s `audit_only` op takes only the audited table's own columns as
values, so it cannot carry the counts. The imported rows themselves go
through `crm_apply` (upserts on `twenty_id`, a plain insert for a new deal so
its first stage is set, updates by id carrying only what changed).

### 3.18 `crm_job_runs` — FR-CRM-JOB-01 (037) — log

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | |
| `workspace_id` | uuid | NULL only for the digest tick, which spans workspaces. Trash purge, flags, email sync and automations write one row per workspace. A NULL-workspace row is shown to nobody with its `error`, only with `error_public`, and never in `GET /crm/jobs` or `stale_jobs`, which list the workspace's own rows |
| `job` | text | `CHECK (job IN ('email_sync','flags','digests','reminders','trash_purge','automations'))` |
| `started_at`, `finished_at` | timestamptz | |
| `outcome` | text | `CHECK (outcome IN ('running','ok','failed','skipped'))` |
| `counts` | jsonb | e.g. `{fetched, stored, created_companies, undeliverable}` |
| `error` | text | ≤ 2,000, may name records; for members only |
| `error_public` | text | ≤ 500, no names, addresses or subjects (FR-CRM-JOB-01); written by the job |
| `alerted` | boolean | NOT NULL DEFAULT false (the three-failure alert went out for this streak) |

Index `(job, workspace_id, started_at DESC)`.

### 3.19 `crm_notifications` — FR-CRM-NOTIF-01 (037) — log

| Column | Type | Constraints |
|---|---|---|
| `id`, `workspace_id` | uuid | |
| `user_id` | uuid | NOT NULL FK profiles CASCADE |
| `kind` | text | `CHECK (kind IN ('assigned','mentioned','stage_changed','reply_owed_red','handoff_waiting','revenue_confirm','task_due','task_overdue','stale_deal','job_failed','invited'))` |
| `record_type`, `record_id` | text, uuid | the record is in the notification's workspace: `crm_check_refs()` (044) |
| `title` | text | ≤ 200; no amount (FR-CRM-ACC-11) |
| `dedupe_key` | text | `UNIQUE (user_id, dedupe_key)`, e.g. `task_overdue:<id>:2026-10-14` |
| `created_at`, `read_at` | timestamptz | |

Index `(user_id, read_at, created_at DESC)`. Listing and the unread count join
`crm_memberships` on `(workspace_id, user_id)`, so only workspaces the person
belongs to now are shown. `crm_remove_membership` (037 version) deletes the
person's rows for that workspace.

### 3.20 `crm_digest_sends` — FR-CRM-NOTIF-03, -04 (037) — log

`id`, `user_id` (FK CASCADE), `workspace_id` (NULL for the daily digest, which
is one message with one section per workspace the person is a member of at
send time, each built with their role in that workspace, so a Viewer's section
carries no amounts; set for the weekly admin digest, one per workspace), `kind CHECK (kind IN
('daily','weekly'))`, `local_date date NOT NULL`, `sent_at`, `channels text[]`;
`UNIQUE (user_id, kind, local_date, coalesce(workspace_id, '0000…'))`. The
sender inserts first (`ON CONFLICT DO NOTHING RETURNING id`) and sends only
when a row came back, so a double tick sends once.

### 3.21 Flags — FR-CRM-FLAG (038)

Added to `crm_companies`, `crm_people`, `crm_opportunities`, `crm_tasks`:

| Column | Type | Notes |
|---|---|---|
| `flag_level` | text | `CHECK (flag_level IN ('none','amber','red'))` NOT NULL DEFAULT 'none'; the worst flag (FR-CRM-FLAG-03) |
| `flags` | jsonb | NOT NULL DEFAULT '[]', e.g. `[{"kind":"reply_owed","level":"red","since":"…","detail":"…"}]` |
| `flags_computed_at` | timestamptz | shown beside the dot (FR-CRM-FLAG-02) |

Index `(workspace_id, flag_level) WHERE deleted_at IS NULL AND flag_level <>
'none'`. The flag job writes through `crm_apply` with actor `sync`. The
trigger skips an update that changes only the three flag columns, so the
30-minute job does not flood the audit log.

### 3.22 Email — FR-CRM-SYNC (039)

`crm_mailboxes` (audited): `id`, `workspace_id`, `email text NOT NULL`
(lower), `owner_id uuid NOT NULL` FK profiles, `labels text[] NOT NULL`
(`cardinality >= 1`), `is_active boolean`, `history_id text` (Gmail
incremental cursor), `page_token text` (resume point), `backfilled_at
timestamptz`, `last_synced_at timestamptz`, stamps; `UNIQUE (email)`, global,
so one mailbox is registered in at most one workspace and its labelled mail
cannot land in the other. `CHECK (split_part(email, '@', 2) IN
('nunnarilabs.com','deepweaver.ai','nunnari.academy'))` (the domains the
service account is delegated for). The rules that the address is the owner's
own profile email unless a TVA admin registers it, and that no label is a
Gmail system label, are checked in Python (plan §5.13).

`crm_email_messages` (log): `id`, `workspace_id`, `mailbox_id` (FK CASCADE),
`gmail_message_id text NOT NULL`, `thread_id text NOT NULL`, `subject text`
(≤ 500), `sent_at timestamptz NOT NULL`, `direction text CHECK (direction IN
('inbound','outbound'))`, `from_email text`, `participants jsonb` (`[{email,
name, role: from|to|cc}]`), `is_internal boolean`, `is_auto boolean`
(auto-reply or bounce, FR-CRM-FLAG-04), `created_at`; `UNIQUE (mailbox_id,
gmail_message_id)` (FR-CRM-SYNC-07). No body column (Q-29). Index
`(workspace_id, thread_id)`, `(workspace_id, sent_at DESC)`.

`crm_email_links` (log): `id`, `workspace_id`, `message_id` (FK CASCADE),
`record_type CHECK IN ('company','person','opportunity')`, `record_id`;
`UNIQUE (message_id, record_type, record_id)`; index `(workspace_id,
record_type, record_id)`.

The two logs are written only by `crm_store_emails(workspace, mailbox,
messages)` (a page's messages and links, `ON CONFLICT DO NOTHING`, returning
the Gmail ids stored) and `crm_remove_emails(workspace, mailbox, ids)`
(FR-CRM-SYNC-09; returns the records the removed copies were linked to).
A mailbox's cursor (`history_id`, `page_token`, `backfilled_at`,
`last_synced_at`) is written directly by the sync: 039's `crm_audit_row()`
skips a change to those columns alone, as 038's does flags. `crm_flag_facts`
(039, four arguments: deals, companies and people) also returns each
record's latest counted inbound and outbound email (never internal, never an
auto-reply) for Reply owed, Client silent and Stale deal.

### 3.23 Campaigns — FR-CRM-CAMP (041)

`crm_campaigns`: `id`, `workspace_id`, `name` (≤ 200), `type_id`,
`status_id`, `channel_id` (option FKs), `entity text CHECK IN
('nunnari_labs','deepweaver')` (Labs only, Q-61), `owner_id`, `starts_on`,
`ends_on` (`CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >=
starts_on)`), `goal text`, `budget numeric(14,2)`, `budget_currency text CHECK
IN ('INR','AUD','USD')`, `budget_in_inr numeric(14,2)`, `budget_fx_rate
numeric(12,6)`, `twenty_id`, `position`, stamps, soft delete; the same three
FX CHECKs as §3.9 on `budget`, `budget_currency`, `budget_in_inr` and
`budget_fx_rate`. Converted deals, pipeline created and won value are derived
on read from members' converted opportunities. `budget`, `budget_in_inr` and
`budget_fx_rate` are amount fields (`crm_object_fields.is_amount = true`),
omitted for Viewers; `budget_currency` is kept, like opportunity `currency`.

`crm_campaign_members`: `id`, `workspace_id`, `campaign_id` (FK CASCADE),
`company_id`, `person_id` (FKs; `CHECK (company_id IS NOT NULL OR person_id
IS NOT NULL)`), `status text NOT NULL DEFAULT 'targeted' CHECK (status IN
('targeted','invited','registered','attended','engaged','meeting_booked','converted','not_interested','unsubscribed'))`,
`last_touch_on date`, `notes text`, `converted_opportunity_id uuid` FK
(`CHECK ((status = 'converted') = (converted_opportunity_id IS NOT NULL))`),
`twenty_id`, `position`, stamps, soft delete; `UNIQUE (campaign_id,
company_id, person_id)` with nulls not distinct (PG15+ `NULLS NOT DISTINCT`).
Index `(workspace_id, campaign_id, status, position)`.

As built (041): the company and person FKs are `ON DELETE CASCADE` and
`converted_opportunity_id` is `ON DELETE SET NULL (converted_opportunity_id)`,
so the converted CHECK is one-directional, `converted_opportunity_id IS NULL
OR status = 'converted'`: purging a converted deal leaves its member
Converted, the conversion kept in its history. That Converted is reached only
with a deal is the service's rule (Converted only through the conversion).
`crm_purge` (041) first keeps a membership that names both the purged record
and the other one (the person, or the company) on the other one, unless that
one already has its own in the campaign. `crm_merge_companies` (041) keeps
the survivor's membership when both companies are in the same campaign with
the same person (or none). In both, a Converted membership is never the one
dropped for one that is not (042): the merge then moves the merged company's
Converted membership to the survivor and drops the survivor's, and the purge
keeps the shared Converted membership on the other record and drops that
record's own, so the campaign's figures (FR-CRM-CAMP-05) keep the
conversion. A member whose company or person is in the Trash is left off the
board and out of the figures (Python) until that record is restored; its row
is unchanged. `crm_query()` and `crm_query_rank()` are replaced
in 041 for the `campaign` and `campaign_member` lists: a member's `status`
ranks after the task statuses in one array (the two share the column name
and no key), and its campaign, person and deal relations sort by name.
`crm_opportunities.source_campaign_id` is `ON DELETE SET NULL
(source_campaign_id)`. Campaign members are listed and changed through their
own routes (contracts §2.10), never the bulk bar, and a campaign's timeline
shows the campaign's own changes, tasks, notes and links, not its members'.

The record-type CHECKs of `crm_task_links`, `crm_note_links` and
`crm_drive_links` already allow `campaign` (030/029), so 041 adds no ALTER
there.

### 3.24 `crm_bookings_targets`, `crm_csv_mappings` (043)

`crm_bookings_targets` (audited): `id`, `workspace_id`, `entity` (NULL =
whole workspace), `period_kind CHECK IN ('month','quarter','fy')`,
`period_start date` (first day; FY starts 1 April), `amount_inr
numeric(14,2) NOT NULL`, stamps; `UNIQUE (workspace_id, coalesce(entity,''),
period_kind, period_start)`.

`crm_csv_mappings` (audited): `id`, `workspace_id`, `object`, `name`,
`mapping jsonb` (`{column: field}`), `created_by`, stamps; `UNIQUE
(workspace_id, object, lower(name))`.

As built (043): a target's `period_start` is held by a CHECK to its
period's first day (any month's 1st; a quarter's 1 January, 1 April,
1 July or 1 October; an FY's 1 April), and the unique index is named
`crm_bookings_targets_period`. A mapping's `object` is `company`,
`person` or `opportunity` (the objects CSV import takes) and its name is at
most 80 characters (`crm_csv_mappings_name`). Both tables have the `touch`
and audit triggers, RLS on and no browser grants. An export writes nothing
but its audit row, which `crm_apply`'s `audit_only` cannot carry (it takes
the audited table's own columns only), so 043 adds
`crm_record_export(p_actor, p_detail)`: it refuses an actor who is not a
`user` member of the workspace (CRM04) or a kind other than `view` or
`workspace` (CRM22), and writes `export.view` or `export.workspace` with the
exported table as `target_table` (the workspace itself for a whole
export) and the detail (object, columns, row count, bytes; never a value)
as `after`. `EXECUTE` is the service role's only.

---

## 4. Changes to existing tables

### 4.0 Browser privileges on every table (025) — constitution §I, D1, D2

A CRM-only seller signs in with Google, so they hold a valid session JWT, and
the anon key is public. Policies still in force would let that JWT read TVA
data through PostgREST: `projects_all_select` and `project_phases_all_select`
(008, `USING (true)`, with the column grant of 009), `holidays_all_select` and
`app_settings_select` (004), and the `*_self_select` policies on bookings,
allowances, allocations and time entries. `audit_log_admin_select` would also
let a TVA admin read CRM audit rows around the workspace scoping (CT-3). The
frontend makes no table read (no `.from(` or `.rpc(` call in `frontend/`), so
025 removes the browser's table access outright:

```sql
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
```

RLS stays enabled and the policies stay in place (harmless without a grant).
The RLS helper functions (`is_admin()` …) keep their `EXECUTE` grants.
A db test (S1a) signs a JWT for a `crm_only` profile and gets a permission
error, or zero rows, from **every** table in `public`, enumerated from
`information_schema.tables`, so a later table cannot reopen the hole
unnoticed. Reverse: re-grant the column and table privileges of 004, 008, 009
and 012.

### 4.1 `profiles` (025) — FR-CRM-ACC-01, -04, -15, FR-CRM-CHG-12

```sql
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS account_type text NOT NULL DEFAULT 'staff',
  ADD COLUMN IF NOT EXISTS timezone text NOT NULL DEFAULT 'Asia/Kolkata',
  ADD COLUMN IF NOT EXISTS number_format text NOT NULL DEFAULT 'indian_short',
  ADD COLUMN IF NOT EXISTS deactivated_reason text;
ALTER TABLE profiles
  ADD CONSTRAINT profiles_deactivated_reason_check CHECK (
    deactivated_reason IS NULL OR (is_active = false
      AND deactivated_reason IN ('crm_no_membership','admin'))),
  ADD CONSTRAINT profiles_account_type_check CHECK (account_type IN ('staff','crm_only')),
  ADD CONSTRAINT profiles_timezone_check CHECK (timezone IN ('Asia/Kolkata','Australia/Sydney')),
  ADD CONSTRAINT profiles_number_format_check CHECK (number_format IN ('indian_short','full')),
  ADD CONSTRAINT profiles_crm_only_shape CHECK (
    account_type = 'staff' OR (role = 'user' AND logs_time = false AND lead_id IS NULL
                               AND is_owner IS NOT TRUE AND dashboard_access IS NOT TRUE));
CREATE INDEX IF NOT EXISTS profiles_account_type ON profiles (account_type) WHERE account_type = 'crm_only';
```

`deactivated_reason` says why an account is inactive: `crm_no_membership`
(set by `crm_remove_membership` when a `crm_only` person's last membership
goes) or `admin` (set by `admin.update_user` when a TVA admin deactivates).
A workspace admin's invitation reactivates only `crm_no_membership`, so it
cannot undo a TVA admin's offboarding (FR-CRM-ACC-04). Existing inactive rows
keep NULL, which counts as `admin`. Reactivation clears it.

Every existing row becomes `staff` (default), so TVA behaviour is unchanged.
No column grant to `authenticated`: the browser never reads profiles. The
`timezone` and `number_format` columns affect CRM display and CRM digests only
(FR-CRM-CHG-12). Reversible: drop the constraints and columns.

`tva_staff_only()` (§5.10) is attached as `BEFORE INSERT OR UPDATE OF
user_id ON allocations`, `… OF lead_id ON projects`, `… OF user_id ON
time_entries` and `… OF lead_id ON profiles`.

### 4.2 `audit_log` (026) — FR-CRM-AUD-02, -04, FR-CRM-CHG-09

```sql
ALTER TABLE audit_log
  ADD COLUMN IF NOT EXISTS workspace_id uuid REFERENCES crm_workspaces(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS channel text;
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_actor_label_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_actor_label_check
  CHECK (actor_label IN ('user','system','import','sync'));
ALTER TABLE audit_log ADD CONSTRAINT audit_log_channel_check
  CHECK (channel IS NULL OR channel IN ('web','mcp'));
CREATE INDEX IF NOT EXISTS audit_log_workspace_target
  ON audit_log (workspace_id, target_table, target_id, at DESC) WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS audit_log_workspace_at
  ON audit_log (workspace_id, at DESC) WHERE workspace_id IS NOT NULL;
```

The append-only triggers and revokes of 005 are untouched; an `ALTER TABLE …
ADD COLUMN` is not an UPDATE of rows. Semantics:

| Row | `workspace_id` | `actor_label` | `actor_id` |
|---|---|---|---|
| CRM change by a person | set | `user` | the person |
| TVA change made by a deal move (D8) | NULL (a TVA row, FR-CRM-AUD-04) | `system` | the triggering person |
| Twenty or CSV import | set | `import` | the importing admin |
| Email sync, flags, purge | set | `sync` | NULL, or the mailbox owner for created records |
| Every existing row | NULL | unchanged | unchanged |

`GET /api/v1/admin/audit` adds `WHERE workspace_id IS NULL`, and so does the
`audit_log` MCP tool, which calls it (FR-CRM-AUD-04).

### 4.3 `projects` (032) — FR-CRM-TVA-01, FR-CRM-CHG-10, FR-CRM-TVA-14

```sql
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS crm_company_id uuid REFERENCES crm_companies(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_opportunity_id uuid REFERENCES crm_opportunities(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS crm_archived_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS projects_source_opportunity_unique
  ON projects (source_opportunity_id) WHERE source_opportunity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS projects_crm_company ON projects (crm_company_id) WHERE crm_company_id IS NOT NULL;
```

- One opportunity creates at most one project (the unique index). The
  one-project-per-opportunity rule from the other side is the same index.
- `crm_archived_at` marks a project archived by a Closed Lost, so a reopen
  unarchives only what the deal archived. It is cleared on reopen.
- `client` stays free text; the CRM writes the company name into it on create
  or link (utilisation's "billable = has a client" keeps working).
- `projects_name_unique` (008) is unchanged; the CRM refuses clashes
  (FR-CRM-TVA-05).
- No column grant to `authenticated`. Existing projects stay unlinked.

### 4.4 `allocations` (028; FK in 032) — FR-CRM-TVA-12, -14, FR-CRM-CHG-03

```sql
ALTER TABLE allocations
  ADD COLUMN IF NOT EXISTS released_at timestamptz,
  ADD COLUMN IF NOT EXISTS released_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS release_reason text,
  ADD COLUMN IF NOT EXISTS released_by_opportunity_id uuid;   -- FK added in 032
ALTER TABLE allocations ADD CONSTRAINT allocations_release_shape
  CHECK ((released_at IS NULL) = (release_reason IS NULL)
         AND (released_at IS NOT NULL OR released_by_opportunity_id IS NULL));
CREATE INDEX IF NOT EXISTS allocations_counting
  ON allocations (user_id, starts_on, ends_on) WHERE released_at IS NULL;
```

032 adds `FOREIGN KEY (released_by_opportunity_id) REFERENCES
crm_opportunities(id) ON DELETE SET NULL`. Releasing never deletes, so the
existing CHECKs (`percent > 0`, `ends_on >= starts_on`) still hold. Released
rows cannot be edited or removed in TVA (refused 422 in `admin.py`), so a
reopen restores exactly what was released.

### 4.5 Unchanged

`project_phases` (handoff maps onto `pre`/`delivery`/`support`; one per kind,
never `spillover`), `project_milestones` (INR only, TVA single-currency `003`
Q-07), `api_tokens` and `oauth_*` (CRM-only people are profiles, so their FKs
work), `app_settings` (no CRM key; §3.3 holds workspace settings).

---

## 5. Functions

All in `public`, `LANGUAGE plpgsql`, `SECURITY INVOKER`, `SET search_path =
public, extensions`. Callers are the backend (service role) only (§5.12).

Error contract: `RAISE EXCEPTION USING ERRCODE = 'CRM04' | 'CRM09' | 'CRM22',
MESSAGE = '<one sentence safe to show>'`. `store.rpc()` maps them to
404/409/422. A unique violation (`23505`) is mapped by constraint name to a
sentence written in Python. Anything else becomes the generic 500.

### 5.1 `crm_audit_row()` — trigger, FR-CRM-AUD-01, -02, -06

```text
AFTER INSERT OR UPDATE OR DELETE ON each audited crm_* table, FOR EACH ROW
  -- NEW is unassigned on DELETE and OLD on INSERT, so read rows as jsonb
  new_j  := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END
  old_j  := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END
  r      := coalesce(new_j, old_j)
  -- 1. skip first, so a skippable write never needs an actor
  diff   := changed keys only (jsonb_each(new_j) vs old_j), excluding updated_at, updated_by
  IF TG_OP = 'UPDATE' AND (diff = '{}' OR diff ⊆ {last_used_at}
       OR diff ⊆ {flag_level, flags, flags_computed_at}
       OR (diff ⊆ {position} AND TG_TABLE_NAME <> 'crm_select_options')) THEN RETURN NULL
  -- 2. then require an actor
  actor  := nullif(current_setting('crm.actor', true), '')::jsonb
  -- a referential action from deleting a profile is recorded as the system
  IF actor IS NULL AND (DELETE of crm_memberships whose user_id is no longer
     in profiles OR an UPDATE whose diff ⊆ {created_by} setting it NULL, the
     old author no longer in profiles) THEN actor := system
  IF actor IS NULL THEN RAISE EXCEPTION USING ERRCODE='CRM22',
     MESSAGE='A CRM change must be made through the CRM.'           -- refuses unaudited writes
  -- crm_workspaces has no workspace_id column: NEW.workspace_id would raise
  -- 'record "new" has no field "workspace_id"', so read it from jsonb
  ws     := coalesce(r->>'workspace_id',
                     CASE WHEN TG_TABLE_NAME = 'crm_workspaces' THEN r->>'id' END)::uuid
  action := coalesce(nullif(current_setting('crm.action', true), ''),
            <singular>.created | .updated | .deleted (deleted_at set) | .restored (deleted_at cleared) | .purged (DELETE))
  INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                         target_table, target_id, before, after)
  VALUES ((actor->>'user_id')::uuid, actor->>'kind', actor->>'channel', ws,
          action, TG_TABLE_NAME, r->>'id', before_diff, after_diff)
```

Skipped writes, which the backend makes directly without an actor:
`crm_memberships.last_used_at` (`POST /crm/workspaces/{key}/use`), kanban
`position` (ordering, not data) and the three flag columns. A select option's
`position` is not kanban order: reordering a list is an admin settings change
(FR-CRM-LIST-01) and is audited like any other. Deleting a profile, or the
auth user it cascades from, removes its memberships and clears `created_by`
on what it authored with no actor set; those referential actions are recorded
as `system` rows rather than refused, so a TVA account stays deletable. Every other change
needs an actor and is audited, including email sync's `last_inbound_at`,
`last_outbound_at` and `last_contact_at`, which go through `crm_apply` with
actor kind `sync`. db tests cover `/use` without an actor and an
`allowed_domains` update of `crm_workspaces` through `crm_apply`.

### 5.2 `crm_apply(p_actor jsonb, p_ops jsonb) RETURNS jsonb` — FR-CRM-AUD-06 (027)

- `p_actor`: `{"user_id": uuid|null, "kind": "user"|"system"|"import"|"sync",
  "channel": "web"|"mcp"|null, "workspace_id": uuid}`. Sets `crm.actor` with
  `set_config(…, true)` (transaction-local).
- `p_ops`: an array, applied in order in one transaction. Each op:
  `{"op": "insert"|"update"|"soft_delete"|"restore"|"delete"|"upsert"|"audit_only",
  "table": "crm_…", "id": uuid?, "values": {…}?, "match": {"twenty_id": …}?,
  "expect_updated_at": timestamptz?, "action": "company.merged"?,
  "deleted_group": uuid?, "ref": "a1"?}`. A later op may use `"$ref:a1"` in
  `values` to refer to the id an earlier op created (a company and its domain
  in one call).
- Guards: `table` must match `^crm_[a-z_]+$`, exist in `public`, and carry
  the trigger `<table>_crm_audit` (looked up in `pg_trigger`), or `CRM22`.
  Columns come from `pg_attribute`. Unknown keys raise `CRM22`. `id`,
  `workspace_id`, `created_at` and `created_by` cannot be set by `values`.
  Access is changed only by §5.3 and §5.13: an insert or upsert into
  `crm_memberships`, a change of its `user_id` and its delete raise `CRM22`.
  Of `crm_workspaces`, only `name` and `allowed_domains` can be updated, the
  latter only by an actor who is an active staff TVA admin; a workspace
  delete raises `CRM22`.
- Isolation: on insert `workspace_id := p_actor.workspace_id`. Update, delete,
  restore and soft delete use `WHERE id = $1 AND workspace_id = $2`; zero rows
  raises `CRM04`. `crm_workspaces` is the one table keyed by `id =
  workspace_id`.
- Concurrency: `expect_updated_at` mismatch raises `CRM09`.
- Upsert: `INSERT … ON CONFLICT (workspace_id, twenty_id) WHERE twenty_id IS
  NOT NULL DO UPDATE` (importer idempotence, FR-CRM-MIG-07). Unchanged rows
  are not rewritten (`WHERE … IS DISTINCT FROM …`), so a second run writes no
  audit rows.
- SQL is built with `format('%I', …)` and `USING` parameters only (CT-1).
- Returns `[{"ref", "id", "row": to_jsonb(row)}]`.
- Deal inserts also write a `crm_stage_history` row (`from_stage NULL`).
  Changing an opportunity's `stage` through `crm_apply` is refused `CRM22`
  "Use a stage move." Only `crm_deal_transition` moves stages.

### 5.3 `crm_remove_membership(p_actor jsonb, p_membership_id uuid) RETURNS jsonb` — FR-CRM-ACC-12, FR-CRM-SYNC-01 (027; replaced in 037, 040)

One transaction: lock the workspace's admin memberships `FOR UPDATE`; refuse
`CRM22` "A workspace needs at least one admin." if this is the last admin
(the same lock and check run in `crm_apply` for a role change on
`crm_memberships`, so two admins demoting each other at once cannot leave
none); refuse `CRM22` when the target profile is `is_owner` or a TVA admin
and the actor is not a TVA admin (re-checked from `profiles`). Then delete the
membership (audited `membership.removed`); from 037, also delete the person's
`crm_notifications` for the workspace; from 040, also set `is_active = false`
on the person's `crm_mailboxes` in the workspace (audited `mailbox.updated`
under the same actor), so the sync stops reading their Gmail there. If the
person is `crm_only` and has no
membership left: `profiles.is_active = false`, `deactivated_reason =
'crm_no_membership'`, `api_tokens.revoked_at = now()` for unrevoked tokens,
`oauth_refresh_tokens.revoked_at = now()`, plus TVA audit rows
`user.deactivated` and `token.revoked` (`actor_label` = the actor's kind,
normally `'user'`, actor = the admin; not `'system'`, which TVA's audit reads
as a D8 change "*Name* (via CRM deal)"). Returns `{deactivated: bool}`. The service then calls
`deps.forget_personal_tokens()` so cached tokens stop working on the next
request.

### 5.4 `crm_purge(p_actor jsonb, p_plan jsonb) RETURNS jsonb` — FR-CRM-DEL-01, -02 (029; replaced in 030, 033, 034, 039, 041, 042)

`p_plan = {"items": [{"table", "id"}]}`. Each item must already be
soft-deleted in the actor's workspace. It deletes the row (audited `.purged`)
and removes the polymorphic rows that point at it. Each version names only
tables that exist when it is created:

| Version | Removes, besides the row |
|---|---|
| 029 | `crm_drive_links` |
| 030 | + `crm_task_links`, `crm_note_links` |
| 033 | + purging a company sets `projects.crm_company_id = NULL` (TVA audit row `project.updated`, system actor); purging an opportunity linked to a project is refused `CRM22` (unreachable in practice: FR-CRM-DEL-03 blocks the soft delete) |
| 034 | + `crm_favourites` (`target_type = 'record'`) |
| 039 | + `crm_email_links` |
| 041 | + campaigns (their links and favourites; members by FK CASCADE); a company's or person's membership that also names the other record stays on it unless that one has its own |
| 042 | + a shared Converted membership stays on the other record and that record's own (not Converted) goes instead |

### 5.5 `crm_deal_transition(p_actor jsonb, p_plan jsonb) RETURNS jsonb` — FR-CRM-PIPE, FR-CRM-TVA (031; replaced in 033)

Applies a plan built and validated by `services/crm/deals.py`:

```json
{
  "opportunity_id": "…", "expect_updated_at": "…", "expect_stage": "negotiation",
  "opportunity": {"stage": "closed_lost", "probability": 0, "lost_on": "2026-10-14",
                  "lost_reason_id": "…", "lost_from_stage": "negotiation",
                  "stage_entered_at": "now"},
  "history": {"from": "negotiation", "to": "closed_lost"},
  "company": {"id": "…", "expect_updated_at": "…", "changes": {"lead_stage": "nurturing"}},
  "close_tasks": ["…"], "close_note": {"body": "Closed: deal lost (Price)."},
  "create_tasks": [{"title": "…", "assignee_id": "…", "category_key": "delivery_handoff",
                    "bucket": "this_week", "links": [{"record_type": "opportunity", "record_id": "…"}]}],
  "project": {"action": "archive_release", "project_id": "…", "reason": "Deal lost: Price"}
}
```

**Re-checks before anything is written (031, kept in 033).** The plan comes
from Python, but no id in it is trusted:

1. `SELECT … FOR UPDATE` the opportunity `WHERE id = plan.opportunity_id AND
   workspace_id = actor.workspace_id AND deleted_at IS NULL`, and the company
   by the **opportunity's own** `company_id` (a `company.id` in the plan must
   equal it); missing → `CRM04`.
2. Every `close_tasks` id must be a task of this workspace linked to this
   opportunity; every `create_tasks` assignee must hold a membership of this
   workspace; every link target must exist in it. Else `CRM04`.
3. Any `project` action requires `crm_workspaces.tva_linked` for the actor's
   workspace, else `CRM22`.
4. The project is always `projects WHERE source_opportunity_id =
   opportunity_id`. A `project_id` in the plan is ignored except as the
   target of `link`.
5. `link` requires the actor (`p_actor.user_id`) to be an active staff
   profile with `role IN ('manager','admin')`, read from `profiles`, else
   `CRM22` "Ask a TVA manager to link it." (FR-CRM-TVA-05, amended).
6. `revenue` or `milestones` in a `confirm` require the same, else `CRM22`
   (FR-CRM-TVA-08, D8). Python leaves them out for anyone else; this is the
   backstop against a new caller.

CRM side (031): checks `stage = expect_stage` and `updated_at =
expect_updated_at`, else `CRM09`. Updates the deal (sets `crm.action =
'opportunity.stage_changed'`), inserts history, updates the company (`CRM09`
if it changed), closes tasks (`status = 'done'`, `completed_at`), inserts the
note with its link, and inserts tasks with their links.

TVA side (033), by `project.action`. Each writes TVA audit rows with
`actor_label 'system'`, `actor_id = p_actor.user_id` and `channel`:

| `action` | Effect | Refuses |
|---|---|---|
| `create` | `INSERT projects (name, client, category, status 'tentative', probability, crm_company_id, source_opportunity_id, created_by)` | unique violation on `projects_name_unique` → `CRM09` "A project called “X” already exists." |
| `link` | `UPDATE projects SET source_opportunity_id, crm_company_id, client` | `CRM22` unless the actor is a staff TVA manager or admin (re-check 5); `CRM09` unless the target has no `source_opportunity_id`, `is_archived = false` and `status = 'tentative'` (FR-CRM-TVA-05) |
| `sync_probability` | `UPDATE projects SET probability` where linked and `status = 'tentative'` | — |
| `confirm` | Create (when there is no project, FR-CRM-TVA-10) or update to `status 'confirmed'`, `lead_id`, `category`, `name`; upsert `project_phases` per kind; when the plan carries them (FR-CRM-TVA-08), `revenue` and the plan's `project_milestones` as the project's full set, matched by `id` (update; insert one without an id; delete an un-invoiced one left out, audited `milestone.removed`), both in INR; with a `project_id` and no project of its own, adopts that project first as `link` does (re-checks 4 and 5); sets `won_fx_rate`, `won_fx_month`, `amount_in_inr` on the deal | name clash as `create`; `CRM22` revenue or milestones from someone who is not a staff manager or admin (re-check 6); `CRM22` an invoiced milestone changed or left out; `CRM04` a milestone id not of this project |
| `archive_release` | `projects.is_archived = true`, `crm_archived_at = now()`; `UPDATE allocations SET released_at = now(), released_by = actor, release_reason = reason, released_by_opportunity_id = opp WHERE project_id = p AND released_at IS NULL`, where `p` is the opportunity's own project (re-check 4) | — |
| `unarchive_unrelease` | `projects.is_archived = false, crm_archived_at = NULL` (only if `crm_archived_at IS NOT NULL`); `UPDATE allocations SET released_* = NULL WHERE released_by_opportunity_id = opp` | — |
| `retentative` | `projects.status = 'tentative'`, `probability` | `CRM22` if `EXISTS time_entries` or `EXISTS project_milestones WHERE invoiced_on IS NOT NULL` (re-check; the sentence names which) |

Returns `{opportunity, company?, project?, tasks_created: [ids],
allocations_released: n}`.

### 5.6 `crm_link_project(p_actor jsonb, p_plan jsonb) RETURNS jsonb` — FR-CRM-TVA-18 (033)

`p_plan = {opportunity_id, expect_updated_at, mode: "link"|"create",
project_id?, name?, category?}`. Runs re-checks 1 and 3 of §5.5 (opportunity
locked in the actor's workspace, workspace `tva_linked`), and requires the
actor to be a Labs workspace admin and, for `link`, an active staff TVA
manager or admin (re-check 5). Re-verifies, for an open deal at Proposal or
later: the target is unlinked, tentative and not archived, or else creates it
as `create` in §5.5. For a Closed Won deal: the target is unlinked and
confirmed, and only `source_opportunity_id`, `crm_company_id` and `client`
are set; status, revenue, phases and milestones are untouched. Audited on both
sides (`opportunity.project_linked`; TVA `project.updated` system).

### 5.7 `crm_merge_companies(p_actor jsonb, p_plan jsonb) RETURNS jsonb` — FR-CRM-DUP-03, -04 (035; replaced in 039, 041, 042)

`p_plan = {survivor_id, merged_id, expect: {survivor_updated_at,
merged_updated_at}, survivor_changes: {…}}`. One transaction. First, lock
both companies `FOR UPDATE WHERE workspace_id = actor.workspace_id`, and
refuse unless both exist, neither is deleted or merged, and `survivor_id <>
merged_id` (`CRM04` / `CRM22`); check both `expect` stamps (`CRM09`). Then:

1. Apply `survivor_changes`.
2. Move every person, deal, task link, note link and Drive link from
   `merged_id` to `survivor_id`; the 039 version also moves email links, and
   the 041 version also moves campaign members (each version names only
   tables that exist when it is created); the 042 version keeps a Converted
   membership over the survivor's one that is not (§3.23).
3. Move the domains; a duplicate pair keeps one row.
4. Set `projects.crm_company_id = survivor` where it was the merged company
   (TVA audit row; projects otherwise unchanged).
5. Set `merged_into_id` and `deleted_at` on the merged company.

Audited as `company.merged` on both ids.

### 5.8 `crm_query(p_workspace uuid, p_object text, p_spec jsonb, p_redact boolean) RETURNS jsonb` — FR-CRM-VIEW (034) — `STABLE`

- `p_spec`: `{filters: {op: "and"|"or", items: [ {field, operator, value} | {op, items} ]},
  sorts: [{field, dir}], group_by, aggregates: [{field, fn}], columns: [...],
  q, limit, after, today, tz}` (`today` and `tz` are the person's, set by
  Python). Operators by type: `is`, `is_not`, `contains`,
  `not_contains`, `is_empty`, `is_not_empty`, `before`, `after`, `on`,
  `within_last_days`, `within_next_days`, `gt`, `gte`, `lt`, `lte`,
  `any_of`, `none_of`, `is_me` (resolved in Python before the call).
- Every `field` must be in `crm_object_fields` for `p_object`, with the needed
  flag (`filterable`, `sortable`, …), or `CRM22`. With `p_redact`, any
  `is_amount` field anywhere raises `CRM22` (defence in depth; Python refused
  first). Values are bound with `format('%L')` or passed by `USING`.
- Always adds `workspace_id = p_workspace AND deleted_at IS NULL`. For
  `company`, also `merged_into_id IS NULL`.
- A day on a `datetime` field (`on`, `before`, `after`, `gt`…`lte`, the
  `within` operators, and this month or quarter once resolved) is the
  person's day in `tz`, from local midnight to the next, never the server's.
- Sorts and groups follow each list's own order: a select by its option's
  `position`, a fixed list (stages, lead stages, task status and bucket) in
  its seeded order, a person by `display_name`, a related company or person
  by name. A `group_by` is the first sort key (that order, then the key), and
  `groups` come back in the same order, so a page never shows a later group
  before an earlier one.
- Keyset paging on `(sort columns…, id)`; `limit` ≤ 200.
- Returns `{rows: [...selected columns], total: n | null (when > 10,000:
  "more"), groups: [{key, count, aggregates}], aggregates: {...}, next: cursor}`.

### 5.9 `crm_search(p_workspace uuid, p_q text, p_limit int, p_redact boolean) RETURNS jsonb` — FR-CRM-SRCH (035; replaced in 041) — `STABLE`

A union across companies (name, domains), people (full name, primary and
additional email), opportunities (name), tasks (title) and notes (first 200
characters of the body); the 041 version adds campaigns. It uses `lower(x) %
lower(p_q)` or `lower(x) LIKE <escaped lower(p_q)> || '%'` (`%`, `_` and `\`
escaped first). Ranked by `similarity()`
then `updated_at`, at most `p_limit` (≤ 8) per object. With `p_redact`, no
amount column is selected. Workspace and soft-delete filters are always
applied.

### 5.10 `tva_staff_only()` — trigger, FR-CRM-CHG-02 (025)

`BEFORE INSERT OR UPDATE OF <col>`: reads the referenced profile with
`SELECT account_type … FOR SHARE`, so it waits for a `crm_convert_account`
holding that profile's lock (§5.15) and sees its result. If the profile is
`crm_only`, raise `CRM22` with the spec's sentence ("A CRM-only account cannot be
allocated." / "A CRM-only account cannot lead a project." / "A CRM-only
account cannot log time." / "A CRM-only account cannot be anyone's lead.").
Python refuses first; this is the backstop.

### 5.11 `touch_updated_at()` (001) — reused, attached to every CRM table with `updated_at`.

### 5.12 Grants

```sql
REVOKE ALL ON FUNCTION crm_apply(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION crm_apply(jsonb, jsonb) TO service_role;
-- and the same for every crm_* function and tva_staff_only()
```

PostgREST exposes functions in `public` to `anon` and `authenticated` by
default. Without these revokes a browser holding the anon key could call
`crm_query` directly. A db test asserts the denial (S1b, S12).

### 5.13 `crm_invite(p_actor jsonb, p_plan jsonb) RETURNS jsonb` — FR-CRM-ACC-04 (027)

Called after the GoTrue step (CT-5). `p_plan = {user_id, email,
display_name, timezone, role, create_profile: bool, reactivate: bool}`. One
transaction: insert the `crm_only` profile (`logs_time = false`, no lead) when
`create_profile`; or reactivate an inactive `crm_only` profile when
`reactivate`, but only if `deactivated_reason = 'crm_no_membership'` or the
actor is a TVA admin, else `CRM22` "This person's portal account is
inactive; ask a TVA admin."; insert the membership; write the TVA audit rows
(`user.created` / `user.reactivated`, `membership.invited`). If it fails, the
service deletes the auth user only when the request created it
(`created_account = true`).

### 5.14 `crm_check_refs()` — trigger, workspace-scoped references that are not FKs (027)

`BEFORE INSERT OR UPDATE` on `crm_task_links`, `crm_note_links`,
`crm_drive_links`, `crm_email_links`, `crm_favourites` and on every table with
a `uuid[]` option column (`crm_companies.channel_ids`). For a link row, the
target `record_type` maps to its table, and `record_id` must exist there with
the row's `workspace_id`; for an option array, every element must be a
`crm_select_options` row of the same workspace. Else `CRM04` "No such
*record* in this workspace." Each creating migration attaches it.

### 5.15 `crm_convert_account(p_actor jsonb, p_user_id uuid, p_to text) RETURNS jsonb` — FR-CRM-ACC-14, FR-CRM-CHG-02 (027, 045)

`SELECT … FOR UPDATE` the profile. Staff → `crm_only`: refuse `CRM22` for an
`is_owner` profile ("The owner's account cannot be CRM-only."); re-check
inside the lock that nobody has this person as `lead_id`, no project has them
as `lead_id`, and they hold no unreleased allocation, nor (045) a booking or
comp-off claim still `pending` (a CRM-only account leaves every approval
queue), naming the first reason found; then set `account_type = 'crm_only'`, `role = 'user'`, `lead_id =
NULL`, `logs_time = false`, `dashboard_access = false` in one statement, so
`profiles_crm_only_shape` never fails as a raw 23514. `crm_only` → staff:
set `account_type = 'staff'` only. Writes the TVA audit row
`user.account_type`. Because `tva_staff_only` reads the profile `FOR SHARE`,
an allocation or lead assignment racing this change either commits first (and
is then seen by the re-check) or waits and is refused.

---

## 6. Performance indexes (NFR-CRM-01)

| Need | Index |
|---|---|
| List pages per workspace, newest first | `(workspace_id, updated_at DESC) WHERE deleted_at IS NULL` on each record table |
| Kanban by stage / lead stage / bucket / status | `(workspace_id, <field>, position) WHERE deleted_at IS NULL` |
| Owner filters ("My accounts") | `(workspace_id, owner_id) WHERE deleted_at IS NULL` |
| Search and duplicate warnings | GIN `gin_trgm_ops` on `lower(name)` (companies, opportunities, campaigns), people full name and email, `crm_company_domains.domain`; GIN on `people.additional_emails` |
| Today | §3.9 (deal next steps) and §3.12 (tasks) partial indexes |
| Timeline | `audit_log (workspace_id, target_table, target_id, at DESC)` |
| Counting allocations | `allocations (user_id, starts_on, ends_on) WHERE released_at IS NULL` |
| Flags filter | `(workspace_id, flag_level)` partial |

S12 confirms these at 10,000 rows with `EXPLAIN (ANALYZE)` on the seeded
local database and adds an index (as `044_*.sql`) only where a measured query
misses 1 s.

---

## 7. Seeds (in migrations, not `seed.sql`)

### 7.1 Workspaces (026)

| key | name | tva_linked | has_entity | allowed_domains |
|---|---|---|---|---|
| `labs` | Nunnari Labs | true | true | `nunnarilabs.com`, `deepweaver.ai` |
| `academy` | Nunnari Academy B2B | false | false | `nunnari.academy`, `nunnarilabs.com` |

### 7.2 Select lists (026; campaigns in 041)

Values exactly as spec FR-CRM-LIST-02 to -05, §5.6–5.9 and §6.6. Fixed lists
carry their keys; rule-bearing options carry the system keys of §3.4.1.
`Delivery handoff` exists in Labs task categories only. Institution type and
Programme exist in Academy only. Industry and Programme start empty. Colours
are assigned in seed order from the palette.

### 7.3 Settings (026, 037, 039)

| key | value |
|---|---|
| `stage_probabilities` | `{"discovery":10,"qualification":25,"proposal":50,"negotiation":75,"closed_won":100,"closed_lost":0}` |
| `flag_thresholds` (037) | `{"reply_owed_red_hours":24,"reply_owed_amber_hours":12,"silent_red_days":3,"silent_amber_days":2,"close_amber_days":7,"stale_red_days":21,"stale_amber_days":14}` |
| `automations` (037) | `{"email_records":true,"stale_notice":true,"digests":true,"task_reminders":true,"campaign_conversion":true}` (automations 2, 5, 6, 7, 8; FR-CRM-AUTO-02) |
| `email_sync` (039) | `{"enabled": false}` |

### 7.4 Owner memberships (026) — FR-CRM-CHG-15

```sql
INSERT INTO crm_memberships (workspace_id, user_id, role)
SELECT w.id, p.id, 'admin' FROM crm_workspaces w CROSS JOIN profiles p
WHERE p.is_owner IS TRUE
ON CONFLICT (workspace_id, user_id) DO NOTHING;
```

Seeds run inside a `DO` block that first calls `set_config('crm.actor',
'{"kind":"system","user_id":null,"channel":null,"workspace_id":null}', true)`,
so the audit trigger records them as system rows rather than refusing them.

On production the owner's profile exists, so this insert finds it. On a fresh
local `npx supabase db reset`, migrations run **before** `supabase/seed.sql`,
so no `is_owner` profile exists yet and this insert adds nothing.
`seed.sql` therefore inserts the owner's two admin memberships itself, in the
same system-actor `DO` block, after the profiles are seeded (tasks S1c).

### 7.5 Field registry and views (034; 038 and 041 add rows)

`crm_object_fields` covers every field of spec §5.6–5.9 and §6.6, plus the
derived `weighted_amount` (opportunity, amount) and `flag_level` (038).
`crm_saved_views` seeds the views of FR-CRM-VIEW-11 per workspace as shared
views with `system_key`: `companies_all`, `lead_funnel`,
`partners_channels`, `my_accounts`, `pipeline` (default for opportunities),
`closing_this_month`, `won_lost_quarter`, `open_tasks_by_owner`, `my_week`;
(038) `follow_ups_owed`; (041) `members_by_status`.

---

## 8. Reversibility (constitution §III)

| Migration | Reverse |
|---|---|
| 025, 028, 032 (ALTERs) | Drop the added constraints, indexes, triggers and columns. Data in them is lost; TVA behaviour returns to before. 025's privilege revoke is reversed by re-granting what 004, 008, 009 and 012 granted. |
| 026 audit_log changes | Columns can be dropped only after deleting CRM rows, which the append-only rules forbid. **Not reversible in practice**, and the file says so: rows written by the CRM are history. |
| 026–043 new tables and functions | `DROP FUNCTION …; DROP TABLE … CASCADE` in reverse order. Each file's header gives the drop list. |
