/**
 * Sales CRM types — spec 007 contracts/api.md §2.1 to §2.4.
 *
 * Mirrored by hand from `backend/app/schemas/crm.py` and the CRM routes'
 * answers, as `types.ts` mirrors `schemas/__init__.py`. Change one, change
 * the other. Money and rates are decimal strings; dates are YYYY-MM-DD and
 * timestamps carry their offset.
 */

import type { MePreferences } from "@/lib/api/types";

/** Spec 007 §3.3 — one CRM role per workspace, independent of the TVA role. */
export type CrmRole = "admin" | "member" | "viewer";

export type CrmColor =
  | "gray"
  | "red"
  | "orange"
  | "amber"
  | "green"
  | "teal"
  | "blue"
  | "indigo"
  | "purple"
  | "pink";

/** GET /crm/workspaces — FR-CRM-WS-03, -04. */
export interface CrmWorkspace {
  id: string;
  /** Used in URLs (/crm/labs/…) and as the MCP `workspace` argument. */
  key: string;
  name: string;
  role: CrmRole;
  /** Only the Labs workspace is connected to TVA projects (FR-CRM-WS-05). */
  tva_linked: boolean;
  /** Companies, deals and campaigns carry an Entity (FR-CRM-WS-06). */
  has_entity: boolean;
  /** The default for reads that name no workspace (D13). */
  last_used: boolean;
}

/** One option of a select list — FR-CRM-LIST-01, -02, -09. */
export interface CrmOption {
  id: string;
  /** The fixed key rules, MCP and imports use; labels are display only. */
  system_key: string | null;
  label: string;
  color: CrmColor | null;
  position: number;
  is_default: boolean;
  /** Inactive options stay on old records and leave the pickers. */
  is_active: boolean;
  /** Stages, lead stages, task status and bucket: rename and recolour only. */
  is_fixed: boolean;
}

/** Every list this workspace has, keyed by list key, options in order. */
export type CrmLists = Record<string, CrmOption[]>;

export type StageKey =
  | "discovery"
  | "qualification"
  | "proposal"
  | "negotiation"
  | "closed_won"
  | "closed_lost";

/** FR-CRM-PIPE-11 — whole numbers from 0 to 100. */
export type StageProbabilities = Record<StageKey, number>;

/** GET /crm/bootstrap — the one call a CRM page makes on load. */
export interface CrmBootstrap {
  workspace: Omit<CrmWorkspace, "role" | "last_used">;
  role: CrmRole;
  me: {
    id: string;
    display_name: string;
    account_type: "staff" | "crm_only";
  } & MePreferences;
  lists: CrmLists;
  settings_public: { stage_probabilities: StageProbabilities | null };
  email_sync_on: boolean;
}

/** GET /crm/settings — Admin only. The allow-list is read-only here. */
export interface CrmSettings {
  settings: { stage_probabilities?: StageProbabilities } & Record<string, unknown>;
  allowed_domains: string[];
}

/** GET /crm/fx-rates — INR per one unit, for a month (FR-CRM-FX-02). */
export interface CrmFxRate {
  currency: "AUD" | "USD";
  /** YYYY-MM */
  month: string;
  /** Decimal string. */
  rate: string;
}

export type CrmActorKind = "user" | "system" | "import" | "sync";

/** GET /crm/audit — FR-CRM-AUD-03. */
export interface CrmAuditEntry {
  id: number;
  at: string;
  actor_name: string;
  actor_kind: CrmActorKind;
  channel: "web" | "mcp" | null;
  action: string;
  record_type: string;
  record_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface CrmAuditFilters {
  record_type?:
    | "workspace"
    | "membership"
    | "setting"
    | "option"
    | "fx_rate"
    | "company"
    | "company_domain"
    | "person"
    | "drive_link";
  record_id?: string;
  actor_id?: string;
  action?: string;
  /** YYYY-MM-DD, whole days in the viewer's time zone. */
  from?: string;
  to?: string;
  limit?: number;
  /** An entry id; older entries follow. */
  before?: number;
}

export interface OptionUpdate {
  label?: string;
  color?: CrmColor | null;
  position?: number;
  is_default?: boolean;
  is_active?: boolean;
}

// ---------------------------------------------------------------------------
// Members and invitations — §2.2 (FR-CRM-ACC)
// ---------------------------------------------------------------------------

/** GET /crm/members. `email` is present for admins only. */
export interface CrmMember {
  membership_id: string;
  user_id: string;
  display_name: string;
  email?: string;
  role: CrmRole;
  account_type: "staff" | "crm_only";
}

/** POST /crm/invitations — a CRM role only, never a TVA field (FR-CRM-ACC-09). */
export interface InvitationIn {
  email: string;
  display_name: string;
  role: CrmRole;
  /** Defaults to Sydney for deepweaver.ai, Kolkata otherwise (FR-CRM-ACC-15). */
  timezone?: MePreferences["timezone"];
}

/** The TVA admin panel's invitation may bypass the allow-list for an
 *  existing staff account (FR-CRM-ACC-05). */
export interface TvaInvitationIn extends InvitationIn {
  bypass_allow_list?: boolean;
}

export interface InvitationResult {
  membership_id: string;
  user_id: string;
  created_account: boolean;
  reactivated: boolean;
  /** Always shown, to share by hand (FR-CRM-ACC-08). */
  sign_in_url: string;
  /** Empty when no message could be sent. */
  delivered_via: ("slack" | "email")[];
  /** Google sign-in only (FR-CRM-ACC-07). */
  note: string;
}

// ---------------------------------------------------------------------------
// TVA admin panel — §2.4 (FR-CRM-ACC-13, FR-CRM-CHG-11)
// ---------------------------------------------------------------------------

export interface TvaCrmWorkspace {
  id: string;
  key: string;
  name: string;
  member_count: number;
  allowed_domains: string[];
}

/** Account administration only: never a record's content. */
export interface TvaCrmAuditEntry {
  id: number;
  at: string;
  workspace_key: string | null;
  actor_name: string;
  action: string;
  target_name: string | null;
  role: CrmRole | null;
}

export interface AccountTypeResult {
  id: string;
  display_name: string | null;
  account_type: "staff" | "crm_only";
  role: string | null;
  logs_time: boolean | null;
  lead_id: string | null;
  dashboard_access: boolean | null;
  changed: boolean;
}

/** Spec 007 contracts §2.8, FR-CRM-CAP — totals only; a week with fewer than
 *  3 people supplying it is hidden (Q-39). */
export type CrmCapacityWeek =
  | { week_start: string; tentative_hours: string; free_hours: string }
  | { week_start: string; hidden: string };

export interface CrmCapacity {
  weeks: CrmCapacityWeek[];
}

// ---------------------------------------------------------------------------
// Records — §2.5 (S3: companies, people, Drive links)
// ---------------------------------------------------------------------------

/** FR-CRM-LIST-02 — a company's fixed lead stages, by key. */
export type LeadStage =
  | "new"
  | "contacted"
  | "nurturing"
  | "qualified"
  | "deal_open"
  | "customer"
  | "partner"
  | "disqualified";

/** FR-CRM-WS-06 — Nunnari Labs only. */
export type CrmEntity = "nunnari_labs" | "deepweaver";

/** The objects with routes so far (contracts §2.5 "Objects in paths"). */
export type CrmObject = "companies" | "people" | "opportunities" | "tasks" | "notes";

export interface LinkValue {
  url: string;
  label?: string | null;
}

/** FR-CRM-LIST-07 "links" — a primary link and any others. */
export interface Links {
  primary?: LinkValue | null;
  others?: LinkValue[];
}

export interface Address {
  street?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  postal_code?: string | null;
}

/** Country code (default +91), number, and whether it takes WhatsApp. */
export interface Phone {
  country_code: string;
  number: string;
  whatsapp: boolean;
}

interface RecordStamps {
  created_at: string;
  created_by: string | null;
  created_by_name: string | null;
  updated_at: string;
  updated_by: string | null;
  updated_by_name: string | null;
  deleted_at: string | null;
}

/** Contracts §2.5.1 company. Option ids travel with their label. */
export interface CrmCompany extends RecordStamps {
  id: string;
  record_type: "company";
  name: string;
  domain: string | null;
  additional_domains: string[];
  entities: CrmEntity[];
  entity_labels: string[];
  lead_stage: LeadStage;
  lead_stage_label: string;
  account_type_id: string | null;
  account_type_label: string | null;
  relationship_status_id: string | null;
  relationship_status_label: string | null;
  channel_ids: string[];
  channel_labels: (string | null)[];
  owner_id: string | null;
  owner_name: string | null;
  next_touch_on: string | null;
  industry_id: string | null;
  industry_label: string | null;
  institution_type_id: string | null;
  institution_type_label: string | null;
  size_id: string | null;
  size_label: string | null;
  source_id: string | null;
  source_label: string | null;
  country: string | null;
  city: string | null;
  address: Address | null;
  website: Links | null;
  linkedin: Links | null;
  description: string | null;
  /** System-written (FR-CRM-CO-03): shown, never edited. */
  last_inbound_at: string | null;
  last_outbound_at: string | null;
  last_contact_at: string | null;
}

/** Contracts §2.5.1 person. */
export interface CrmPerson extends RecordStamps {
  id: string;
  record_type: "person";
  /** First and last name together. */
  name: string;
  first_name: string;
  last_name: string | null;
  company_id: string | null;
  company_name: string | null;
  primary_email: string | null;
  additional_emails: string[];
  phones: Phone[];
  job_title: string | null;
  city: string | null;
  linkedin: Links | null;
  owner_id: string | null;
  owner_name: string | null;
  do_not_contact: boolean;
  last_inbound_at: string | null;
  last_outbound_at: string | null;
  last_contact_at: string | null;
}

/** FR-CRM-FX-01. */
export type CrmCurrency = "INR" | "AUD" | "USD";

/**
 * Contracts §2.5.1 opportunity. Money is a decimal string. For a Viewer every
 * amount key is absent, never zero (FR-CRM-ACC-11): hence the optional ones.
 */
export interface CrmOpportunity extends RecordStamps {
  id: string;
  record_type: "opportunity";
  name: string;
  company_id: string;
  company_name: string | null;
  point_of_contact_id: string | null;
  point_of_contact_name: string | null;
  entity: CrmEntity | null;
  entity_label: string | null;
  stage: StageKey;
  stage_label: string;
  stage_entered_at: string;
  amount?: string | null;
  currency: CrmCurrency | null;
  amount_in_inr?: string | null;
  fx_rate_used?: string | null;
  fx_month: string | null;
  won_fx_rate?: string | null;
  won_fx_month: string | null;
  weighted_amount?: string | null;
  probability: number;
  expected_close_on: string | null;
  next_step: string | null;
  next_step_on: string | null;
  owner_id: string;
  owner_name: string | null;
  channel_id: string | null;
  channel_label: string | null;
  deal_type_id: string | null;
  deal_type_label: string | null;
  engagement_model_id: string | null;
  engagement_model_label: string | null;
  programme_id: string | null;
  programme_label: string | null;
  lost_reason_id: string | null;
  lost_reason_label: string | null;
  project_category: "client" | "poc" | "product" | null;
  project_category_label: string | null;
  proposal_sent_on: string | null;
  lost_notes: string | null;
  lost_from_stage: StageKey | null;
  won_on: string | null;
  lost_on: string | null;
  /** The Labs deal's TVA project (FR-CRM-TVA-02): every role sees it. */
  project: CrmProjectBlock | null;
}

/** A task's or note's link, with the linked record's name. */
export interface CrmLinkRef {
  record_type: "company" | "person" | "opportunity" | "campaign";
  record_id: string;
  name: string | null;
}

export type TaskStatus = "todo" | "in_progress" | "done";
export type TaskBucket = "today" | "this_week" | "this_month" | "later";

/** Contracts §2.5.1 task — linked to at least one record (FR-CRM-TASK-01). */
export interface CrmTask extends RecordStamps {
  id: string;
  record_type: "task";
  title: string;
  body: string | null;
  status: TaskStatus;
  status_label: string;
  due_on: string | null;
  bucket: TaskBucket | null;
  bucket_label: string | null;
  category_id: string | null;
  category_label: string | null;
  assignee_id: string;
  assignee_name: string | null;
  completed_at: string | null;
  links: CrmLinkRef[];
}

export type NoteKind = "note" | "call" | "meeting" | "whatsapp" | "other";

/** Contracts §2.5.1 note — a typed note is a logged activity (FR-CRM-NOTE-03). */
export interface CrmNote extends RecordStamps {
  id: string;
  record_type: "note";
  body: string;
  kind: NoteKind;
  kind_label: string;
  occurred_on: string | null;
  links: CrmLinkRef[];
  mentions: { id: string; name: string | null }[];
}

/** A deal's other contact and their Role in deal (spec §5.8). */
export interface CrmContact {
  id: string;
  person_id: string;
  person_name: string | null;
  job_title: string | null;
  role_id: string | null;
  role_label: string | null;
}

export type CrmRecord = CrmCompany | CrmPerson | CrmOpportunity | CrmTask | CrmNote;

/** The fields a stage move may set in the same save (contracts §2.6). */
export interface StageChanges {
  amount?: string | null;
  currency?: CrmCurrency | null;
  expected_close_on?: string | null;
  proposal_sent_on?: string | null;
  point_of_contact_id?: string | null;
  next_step?: string | null;
  next_step_on?: string | null;
  lost_reason_id?: string | null;
  lost_notes?: string | null;
  probability?: number | null;
}

/** POST …/stage/preview — what a move would do; nothing is written. */
export interface StagePreview {
  allowed: boolean;
  /** Field names, in the order the message names them. */
  missing: (keyof StageChanges | "company_id" | "owner_id")[];
  /** The one-sentence refusal, shown verbatim. */
  message: string | null;
  would: {
    probability?: number;
    company_lead_stage?: LeadStage;
    company_lead_stage_label?: string;
    tasks_closed?: number;
    /** Labs: what the move does to the deal's TVA project (FR-CRM-TVA). */
    project?: ProjectEffect | null;
  };
}

/** POST …/stage. */
export interface StageMoveResult {
  opportunity: CrmOpportunity;
  company?: CrmCompany;
  project: CrmProjectBlock | null;
  tasks_closed: number;
  allocations_released?: number;
  allocations_restored?: number;
}

// ---------------------------------------------------------------------------
// The TVA side of a Labs deal — contracts §2.6, FR-CRM-TVA
// ---------------------------------------------------------------------------

export type ProjectCategory = "client" | "poc" | "product";

/** A linked TVA project as the CRM shows it: never an amount. */
export interface CrmProjectBlock {
  id: string;
  name: string;
  status: "tentative" | "confirmed";
  is_archived: boolean;
  phases: { phase: "pre" | "delivery" | "support" | "spillover"; label: string; starts_on: string; ends_on: string }[];
}

/** `would.project` of a stage preview. */
export interface ProjectEffect {
  action:
    | "create"
    | "link"
    | "sync_probability"
    | "archive_release"
    | "unarchive_unrelease"
    | "retentative";
  name: string;
  category: string | null;
  probability: number;
  /** A project already has that name (FR-CRM-TVA-05). `linkable`: by this caller. */
  clash: { project_id: string; name: string; linkable: boolean } | null;
}

export interface PhaseSpan {
  starts_on: string;
  ends_on: string;
}

/** GET …/handoff. Revenue keys are present only for a TVA manager or admin. */
export interface HandoffPreview {
  project: {
    id?: string;
    status?: string;
    name: string;
    category: ProjectCategory | "internal";
    lead_id: string | null;
    phases: Partial<Record<"pre" | "delivery" | "support", PhaseSpan>>;
  };
  revenue: {
    suggested_inr: string | null;
    won_fx_rate: string | null;
    won_fx_month: string | null;
    existing_tva_revenue?: string;
    message?: string;
  };
  milestones: HandoffMilestone[];
  can_set_revenue: boolean;
  offer_pre_phase: boolean;
  leads: { id: string; display_name: string }[];
}

export interface HandoffMilestone {
  /** The project's existing milestone; absent on one added in the dialog. */
  id?: string;
  name: string;
  due_on: string;
  /** INR, decimal string. */
  amount: string;
}

/** POST …/handoff. */
export interface HandoffIn {
  project_name?: string;
  category?: ProjectCategory;
  lead_id: string;
  phases: Partial<Record<"pre" | "delivery" | "support", PhaseSpan>>;
  revenue?: string;
  milestones?: HandoffMilestone[];
  expect_updated_at?: string;
}

export interface HandoffResult extends StageMoveResult {
  revenue_written: boolean;
  tasks_created: { id: string; title: string; assignee: { id: string; name: string | null } }[];
}

/** GET /crm/projects/linkable — TVA managers and admins only. */
export interface LinkableProject {
  id: string;
  name: string;
  status: "tentative" | "confirmed";
  client: string | null;
}

/** FR-CRM-LINK-01 — an address and a label; never the file. */
export interface CrmDriveLink {
  id: string;
  record_type: "company" | "person" | "opportunity" | "task" | "note" | "campaign";
  record_id: string;
  url: string;
  label: string;
  created_at: string;
  created_by_name: string | null;
}

export type CrmTimelineType =
  | "create"
  | "field"
  | "stage"
  | "note"
  | "task"
  | "link"
  | "merge"
  | "handoff"
  | "email"
  | "delete";

/** FR-CRM-REC-03, -04 — every entry names its actor and channel. */
export interface CrmTimelineEntry {
  id: number;
  at: string;
  actor_name: string;
  actor_kind: CrmActorKind;
  channel: "web" | "mcp" | null;
  type: CrmTimelineType;
  summary: string;
  changes: { field: string; label: string; before: unknown; after: unknown }[];
}

/** GET /crm/{object}/{id} — the record page in one call. */
export interface CrmRecordPage<T extends CrmRecord> {
  record: T;
  related: {
    people?: CrmPerson[];
    company?: { id: string; name: string | null } | null;
    opportunities?: CrmOpportunity[];
    point_of_contact?: { id: string; name: string | null } | null;
    contacts?: CrmContact[];
    /** A Labs deal's TVA project. */
    project?: CrmProjectBlock | null;
    /** A Labs company's TVA projects (FR-CRM-CO-02). */
    projects?: CrmProjectBlock[];
  };
  timeline_head: CrmTimelineEntry[];
  links: CrmDriveLink[];
  /** Companies, people and deals: the Tasks and Notes tabs (FR-CRM-REC-03). */
  tasks?: CrmTask[];
  notes?: CrmNote[];
  /** A merged company answers with its survivor (FR-CRM-DUP-03). */
  merged_from?: { id: string; name: string };
}

export interface CrmPage<T> {
  rows: T[];
  /** Pass as `after` for the next page; null at the end. */
  next: string | null;
}

/** FR-CRM-DUP-02 — the exact clash, if any, and names alike. */
export interface CrmDuplicates {
  exact: {
    id: string;
    name: string | null;
    field: "domain" | "primary_email";
    value: string;
    in_trash: boolean;
  } | null;
  similar: { id: string; name: string; similarity: number }[];
}

/** POST /crm/{object}?check_duplicates=true answers this instead of the
 *  record when names are alike, having created nothing. */
export interface NotCreated {
  created: false;
  duplicates: CrmDuplicates;
}

/** GET /crm/trash — FR-CRM-DEL-01. */
export interface CrmTrashRow {
  id: string;
  record_type: "company" | "person" | "opportunity" | "task" | "note";
  name: string;
  deleted_at: string;
  deleted_by: string | null;
  deleted_by_name: string | null;
  days_left: number;
}

// ---------------------------------------------------------------------------
// Views — §2.7 (S6a server, S6b screens; FR-CRM-VIEW)
// ---------------------------------------------------------------------------

/** A list's object by record type, as saved views and `/crm/query` name it. */
export type ViewObjectKind = "company" | "person" | "opportunity" | "task" | "note";

/** `crm_object_fields.type` — how a field is filtered, sorted and grouped. */
export type ViewFieldType =
  | "text"
  | "long_text"
  | "number"
  | "currency"
  | "date"
  | "datetime"
  | "boolean"
  | "select"
  | "multi_select"
  | "fixed_select"
  | "person"
  | "relation"
  | "flag";

export type FilterOperator =
  | "is"
  | "is_not"
  | "contains"
  | "not_contains"
  | "is_empty"
  | "is_not_empty"
  | "any_of"
  | "none_of"
  | "is_me"
  | "on"
  | "before"
  | "after"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "within_last_days"
  | "within_next_days"
  | "this_month"
  | "this_quarter";

export interface FilterCondition {
  field: string;
  operator: FilterOperator;
  value?: unknown;
}

/** AND or OR over conditions and groups, at most three levels deep. */
export interface FilterGroup {
  op: "and" | "or";
  items: FilterNode[];
}

export type FilterNode = FilterCondition | FilterGroup;

export interface ViewSort {
  field: string;
  dir: "asc" | "desc";
}

export type AggregateFn = "count" | "sum" | "avg" | "min" | "max";

/** A count of rows has no field. */
export interface ViewAggregate {
  fn: AggregateFn;
  field?: string;
}

/** Width in pixels, 40 to 800. */
export interface ViewColumn {
  field: string;
  width?: number;
}

/** A saved view's `config` (data-model §3.15). */
export interface ViewConfig {
  filters?: FilterGroup;
  sorts?: ViewSort[];
  group_by?: string;
  kanban_field?: string;
  columns?: (ViewColumn | string)[];
  aggregates?: ViewAggregate[];
  card_fields?: string[];
}

/** GET /crm/views. For a Viewer the config has nothing about amounts, and
 *  `notice` says when something was dropped (Q-67). */
export interface CrmSavedView {
  id: string;
  object: ViewObjectKind;
  name: string;
  icon: string | null;
  kind: "table" | "kanban";
  visibility: "private" | "shared";
  owner_id: string | null;
  /** Seeded views (FR-CRM-VIEW-11): hidden, never deleted. */
  system_key: string | null;
  is_default: boolean;
  hidden: boolean;
  position: number;
  config: ViewConfig;
  can_edit: boolean;
  created_at: string;
  updated_at: string;
  notice?: string;
}

export interface CrmViewIn {
  object: ViewObjectKind;
  name: string;
  icon?: string | null;
  kind: "table" | "kanban";
  visibility: "private" | "shared";
  config: ViewConfig;
}

export interface CrmViewUpdate {
  name?: string;
  icon?: string | null;
  kind?: "table" | "kanban";
  visibility?: "private" | "shared";
  config?: ViewConfig;
  position?: number;
  is_default?: boolean;
  hidden?: boolean;
}

/** POST /crm/query `spec`; the server filters, sorts and pages. */
export interface QuerySpec {
  filters?: FilterGroup;
  sorts?: ViewSort[];
  group_by?: string;
  aggregates?: ViewAggregate[];
  q?: string;
  limit?: number;
  after?: string;
}

/** Totals keyed `count` or `<fn>_<field>`; money as decimal strings. */
export type CrmTotals = Record<string, string | number | null>;

export interface CrmQueryGroup {
  key: string | boolean | null;
  label: string | null;
  count: number;
  aggregates?: CrmTotals;
}

export interface CrmQueryResult<T> {
  rows: T[];
  /** Counting stops past ten thousand. */
  total: number | "10000+";
  next: string | null;
  group_by?: string;
  groups?: CrmQueryGroup[];
  aggregates?: CrmTotals;
  view?: CrmSavedView;
  notice?: string;
}

/** FR-CRM-UI-02 — a pinned view or record, named. */
export interface CrmFavourite {
  id: string;
  target_type: "view" | "record";
  record_type: ViewObjectKind | null;
  target_id: string;
  name: string | null;
  position: number;
}

export type BulkAction = "set" | "assign_owner" | "add_task" | "delete";

/** POST /crm/bulk — each record held to its own rules (FR-CRM-VIEW-07). */
export interface BulkResult {
  ok: string[];
  failed: { id: string; reason: string }[];
}

// ---------------------------------------------------------------------------
// Today, search, merge — §2.8 (S7)
// ---------------------------------------------------------------------------

export type TodaySectionKey = "replies_owed" | "next_steps" | "tasks" | "silent_clients" | "loose_ends";

/** One line of Today: what it is and the record it opens. */
export interface TodayItem {
  record_type: "company" | "person" | "opportunity" | "task";
  record_id: string;
  title: string;
  detail: string;
  due_on: string | null;
  level: "overdue" | "today" | null;
  /** A task line opens the first record it is linked to. */
  opens?: SearchTarget | null;
}

export interface TodaySection {
  key: TodaySectionKey;
  /** "Email sync is off" while sections 1 and 4 cannot be built (FR-CRM-TODAY-02). */
  off_reason?: string;
  items: TodayItem[];
}

/** GET /crm/today — FR-CRM-TODAY-01 to -03. */
export interface CrmToday {
  /** The person's own day, YYYY-MM-DD. */
  as_of: string;
  timezone: string;
  person: { id: string; name: string | null };
  sections: TodaySection[];
}

/** Where a task or note hit opens: the first record it is linked to. */
export interface SearchTarget {
  record_type: ViewObjectKind;
  record_id: string;
}

export interface SearchHit {
  id: string;
  record_type: ViewObjectKind;
  name: string;
  domain?: string | null;
  email?: string | null;
  company_name?: string | null;
  lead_stage_label?: string | null;
  stage_label?: string | null;
  status_label?: string | null;
  kind_label?: string | null;
  /** Absent for a Viewer (FR-CRM-ACC-11). */
  amount?: string | null;
  currency?: string | null;
  opens?: SearchTarget | null;
}

/** GET /crm/search — FR-CRM-SRCH-01, -02. */
export interface CrmSearchResult {
  companies: SearchHit[];
  people: SearchHit[];
  opportunities: SearchHit[];
  tasks: SearchHit[];
  notes: SearchHit[];
  campaigns: SearchHit[];
  views: Pick<CrmSavedView, "id" | "object" | "name" | "icon" | "kind" | "system_key">[];
}

export interface MergeField {
  field: string;
  label: string;
  a: unknown;
  b: unknown;
  a_display: string | number | boolean | null;
  b_display: string | number | boolean | null;
  default: "a" | "b";
}

/** GET /crm/companies/merge-preview — `a` survives (FR-CRM-DUP-03). */
export interface MergePreview {
  a: CrmCompany;
  b: CrmCompany;
  differing_fields: MergeField[];
  moves: { people: number; deals: number; tasks: number; notes: number; links: number; projects: number };
}

// ---------------------------------------------------------------------------
// Imports — contracts §2.9 (S8), mirrors schemas/crm.py ImportIn and the
// dry run's report.
// ---------------------------------------------------------------------------

export type ImportObject = "companies" | "people" | "opportunities" | "tasks" | "notes";

export interface ImportCounts {
  in_file: number;
  create: number;
  update: number;
  unchanged: number;
  skipped: number;
}

export interface ImportConflict {
  object: ImportObject | "attachments";
  /** 1-based, within its object in the file. */
  row: number;
  kind: string;
  detail: string;
  /** Fixed in the file, the CRM or by the fix-up before the real run. */
  blocking: boolean;
}

/** POST /crm/imports/dry-run — FR-CRM-MIG-03, -04, -05. */
export interface ImportDryRun {
  run_id: string;
  counts: Partial<Record<ImportObject, ImportCounts>>;
  lead_stages: { row: number; company: string; derived: string; derived_label: string; because: string }[];
  conflicts: ImportConflict[];
  attachments: { name: string; record_type: string | null; record: string | null; record_id: string | null }[];
  fixup_candidates: number;
  blocking: string[];
}

/** POST /crm/imports/{run_id}/apply — FR-CRM-MIG-07. */
export interface ImportApplied {
  run_id: string;
  counts: Partial<Record<ImportObject, ImportCounts>>;
  fixups_used: number;
}

/** GET /crm/imports — recent runs, never their rows. */
export interface ImportRun {
  id: string;
  kind: "twenty" | "csv";
  object: string | null;
  status: "dry_run" | "applied" | "failed";
  counts: Partial<Record<ImportObject, ImportCounts>> | null;
  created_at: string;
  applied_at: string | null;
}
