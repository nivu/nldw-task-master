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
  /** The bell's unread count (1b, FR-CRM-NOTIF-01). */
  unread: number;
  /** Late background jobs; Admins only, empty for everyone else (FR-CRM-JOB-03). */
  stale_jobs: StaleJob[];
  /** 009: the phase switches and the Review Queue (FR-CRMAI-FLAG-03, -04). */
  ai: CrmBootstrapAi;
  /** 009: Admins only, whether this month's AI budget is reached (FR-CRMAI-LLM-08). */
  ai_budget_reached?: boolean;
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

/** The objects with routes (contracts §2.5 "Objects in paths"); campaigns 1b. */
export type CrmObject = "companies" | "people" | "opportunities" | "tasks" | "notes" | "campaigns";

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

/** Spec 007 §6.2 — follow-up and deal-health flags (1b). */
export type FlagLevel = "none" | "amber" | "red";

export interface CrmFlag {
  kind:
    | "reply_owed"
    | "client_silent"
    | "next_step"
    | "close_date"
    | "stale_deal"
    | "loose_end"
    | "task_overdue"
    | "deal"
    /** 009 FR-CRMAI-RULE-03: only while R-2 and phase 3 are on. */
    | "stuck_in_stage";
  /** What the dot says on hover: "Reply owed", "Stale deal"… */
  label: string;
  level: "amber" | "red";
  since: string | null;
  detail: string;
  /** A company's `deal` entry names the deal. */
  record_id?: string;
}

/** FR-CRM-FLAG-02, -05 — on companies, people, deals and tasks. */
export interface RecordFlags {
  flag_level: FlagLevel;
  flags: CrmFlag[];
  flags_computed_at: string | null;
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
export interface CrmCompany extends RecordStamps, RecordFlags {
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
export interface CrmPerson extends RecordStamps, RecordFlags {
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
export interface CrmOpportunity extends RecordStamps, RecordFlags {
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
  /** 1b: the campaign it was converted from (FR-CRM-CAMP-04). */
  source_campaign_id: string | null;
  source_campaign_name: string | null;
}

/** A task's or note's link, with the linked record's name. */
export interface CrmLinkRef {
  record_type: "company" | "person" | "opportunity" | "campaign";
  record_id: string;
  name: string | null;
}

/** 008 FR-TASK-CHG-01, -10: open and waiting are open; done and cancelled closed. */
export type TaskStatus = "open" | "waiting" | "done" | "cancelled";
export type TaskBucket = "today" | "this_week" | "this_month" | "later";
export type TaskPriority = "urgent" | "high" | "normal" | "low";

/** Contracts §2.5.1 task — linked to at least one record (FR-CRM-TASK-01). */
export interface CrmTask extends RecordStamps, RecordFlags {
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
  /** 008 FR-TASK-CHG-01. */
  priority: TaskPriority;
  priority_label: string;
  tags: string[];
  source: string;
  source_label: string;
  /** FR-TASK-DATA-09: a parent the reader cannot see is `{private: true}`. */
  parent: { id: string; title: string | null } | { private: true } | null;
  waiting_on: { id: string | null; label: string | null } | null;
  /** 008 FR-TASK-VIS-07: only when the reader can see the TVA project. */
  project: { id: string; name: string } | null;
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
  /** 009 FR-CRMAI-MEET-05: `fireflies:<transcript_id>` on a meeting note the job wrote. */
  source_ref?: string | null;
  /** 009 FR-CRMAI-MEET-05: set on a meeting note the job wrote; its body is plain text. */
  ai_provenance?: CrmMeetingProvenance | null;
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

/** Spec §6.6 — a campaign member's fixed statuses, in board order. */
export type MemberStatus =
  | "targeted"
  | "invited"
  | "registered"
  | "attended"
  | "engaged"
  | "meeting_booked"
  | "converted"
  | "not_interested"
  | "unsubscribed";

/**
 * Spec §6.6 campaign (1b) with its figures (FR-CRM-CAMP-05). For a Viewer
 * the budget, its INR value and rate, pipeline created, won value and cost
 * per converted member are absent (FR-CRM-ACC-11).
 */
export interface CrmCampaign extends RecordStamps {
  id: string;
  record_type: "campaign";
  name: string;
  type_id: string | null;
  type_label: string | null;
  status_id: string | null;
  status_label: string | null;
  channel_id: string | null;
  channel_label: string | null;
  entity: CrmEntity | null;
  entity_label: string | null;
  owner_id: string;
  owner_name: string | null;
  starts_on: string | null;
  ends_on: string | null;
  goal: string | null;
  budget?: string | null;
  budget_currency: CrmCurrency | null;
  budget_in_inr?: string | null;
  budget_fx_rate?: string | null;
  member_count: number;
  member_counts: Record<MemberStatus, number>;
  converted_members: number;
  /** Converted members over all, a percentage with one decimal. */
  conversion_rate: string | null;
  converted_deals: number;
  pipeline_created?: string;
  won_value?: string;
  cost_per_converted?: string | null;
}

/** Spec §6.6 campaign member — a company, a person or both. */
export interface CrmCampaignMember {
  id: string;
  record_type: "campaign_member";
  campaign_id: string;
  campaign_name: string | null;
  company_id: string | null;
  company_name: string | null;
  person_id: string | null;
  person_name: string | null;
  person_company_name: string | null;
  do_not_contact: boolean;
  /** The company, the person, or "Person (Company)". */
  name: string | null;
  status: MemberStatus;
  status_label: string | null;
  last_touch_on: string | null;
  notes: string | null;
  converted_opportunity_id: string | null;
  converted_opportunity_name: string | null;
  position: number;
  updated_at: string;
}

/** GET /crm/campaigns/{id}/members — the board. */
export interface CampaignMembers {
  campaign_id: string;
  member_counts: Record<MemberStatus, number>;
  rows: CrmCampaignMember[];
}

/** POST /crm/campaigns/{id}/members (FR-CRM-CAMP-03, FR-CRM-PER-02). */
export interface MembersAdded {
  added: number;
  skipped_do_not_contact: number;
  skipped_existing: number;
  skipped: { id: string; name: string; reason: string }[];
}

/** POST /crm/campaign-members/{id}/convert (FR-CRM-CAMP-04). */
export interface Converted {
  member: CrmCampaignMember;
  opportunity: CrmOpportunity;
}

export type CrmRecord = CrmCompany | CrmPerson | CrmOpportunity | CrmTask | CrmNote | CrmCampaign;

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
  /** 009 FR-CRMAI-REV-05: set when the change was an approved AI suggestion. */
  suggestion_id?: string;
  approved_suggestion?: boolean;
  /** 009 FR-CRMAI-MEET-05: on a note entry for a meeting note the job wrote. */
  ai?: AiProvenance | null;
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
    /** 1b: a company's or person's campaign memberships (FR-CRM-CO-02). */
    campaign_members?: CrmCampaignMember[];
  };
  timeline_head: CrmTimelineEntry[];
  links: CrmDriveLink[];
  /** Companies, people and deals: the Tasks and Notes tabs (FR-CRM-REC-03). */
  tasks?: CrmTask[];
  notes?: CrmNote[];
  /** A merged company answers with its survivor (FR-CRM-DUP-03). */
  merged_from?: { id: string; name: string };
  /** 1b: companies, people and deals — the latest email threads (S10). */
  emails?: CrmEmailThread[];
  /** 009 S1c, phase 1 on: the latest 5 threads' summaries (FR-CRMAI-SUM-08). */
  summaries?: CrmThreadSummary[];
  /** 009 S2b, phase 2 on: a company's enriched values (FR-CRMAI-ENR-03, -04). */
  enrichment?: CrmEnrichment | null;
  /** 009 S3a, phase 3 on: a company's or deal's AI fields (FR-CRMAI-FIELD-05). */
  ai_fields?: CrmAiFieldValue[];
  /** 009 S4, phase 4 on: a company's or person's contact recency (FR-CRMAI-REL-02). */
  contact_recency?: CrmContactRecency;
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
  record_type: "company" | "person" | "opportunity" | "task" | "note" | "campaign";
  name: string;
  deleted_at: string;
  deleted_by: string | null;
  deleted_by_name: string | null;
  days_left: number;
}

// ---------------------------------------------------------------------------
// Views — §2.7 (S6a server, S6b screens; FR-CRM-VIEW)
// ---------------------------------------------------------------------------

/** A list's object by record type, as saved views and `/crm/query` name it.
 *  Campaign members are listed on their campaign's board instead. */
export type ViewObjectKind = "company" | "person" | "opportunity" | "task" | "note" | "campaign";

/** Every object the view registry knows, campaign members included (their
 *  board is the campaign page's kanban). */
export type ListKind = ViewObjectKind | "campaign_member";

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
  /** Members by status (041) is a campaign member view, opened on a campaign. */
  object: ListKind;
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

export type BulkAction = "set" | "assign_owner" | "add_task" | "delete" | "add_to_campaign";

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
  /** Due lines: overdue or today; flag lines (sections 1 and 4): red or amber. */
  level: "overdue" | "today" | "red" | "amber" | null;
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
  /** For an Admin: background jobs that are late (FR-CRM-JOB-03). */
  stale_jobs?: StaleJob[];
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
  moves: {
    people: number;
    deals: number;
    tasks: number;
    notes: number;
    links: number;
    projects: number;
    /** 1b (041). */
    campaign_members: number;
  };
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

// ---------------------------------------------------------------------------
// 1b — the job log, the bell, thresholds and automations (contracts §2.4, §2.10)
// ---------------------------------------------------------------------------

export type CrmJobName =
  | "email_sync"
  | "flags"
  | "digests"
  | "reminders"
  | "trash_purge"
  | "automations"
  | "summaries"
  | "meetings"
  | "enrichment"
  | "ai_fields"
  | "review_expiry"
  | "followup_drafts"
  | "capture";

/** A job more than twice its interval late (FR-CRM-JOB-03). */
export interface StaleJob {
  job: CrmJobName;
  label: string;
  last_started_at: string;
  overdue_minutes: number;
}

/** One run of a background job (FR-CRM-JOB-01). */
export interface CrmJobRun {
  id: string;
  job: CrmJobName;
  label: string;
  started_at: string;
  finished_at: string | null;
  outcome: "running" | "ok" | "failed" | "skipped";
  counts: Record<string, number | string>;
  /** Names no record: what anyone may see. */
  error_public: string | null;
  /** May name records: only for the workspace's own Admins. */
  error?: string | null;
  alerted: boolean;
}

/** GET /crm/jobs — this workspace's runs. */
export interface CrmJobs {
  runs: CrmJobRun[];
  stale_jobs: StaleJob[];
  jobs: { job: CrmJobName; label: string; every_minutes: number }[];
}

/** GET /crm/tva-admin/jobs — every workspace's runs, `error_public` only. */
export interface TvaCrmJobs {
  runs: (CrmJobRun & { workspace: string | null })[];
  stale_jobs: (StaleJob & { workspace: string | null })[];
}

/** GET /crm/notifications — FR-CRM-NOTIF-01. Titles never carry an amount. */
export interface CrmNotification {
  id: string;
  workspace_key: string;
  kind:
    | "assigned"
    | "mentioned"
    | "stage_changed"
    | "reply_owed_red"
    | "handoff_waiting"
    | "revenue_confirm"
    | "task_due"
    | "task_overdue"
    | "stale_deal"
    | "job_failed"
    | "invited";
  title: string;
  record_type: ViewObjectKind | null;
  record_id: string | null;
  created_at: string;
  read_at: string | null;
}

/** `flag_thresholds` (FR-CRM-FLAG-01). */
export interface FlagThresholds {
  reply_owed_red_hours: number;
  reply_owed_amber_hours: number;
  silent_red_days: number;
  silent_amber_days: number;
  close_amber_days: number;
  stale_red_days: number;
  stale_amber_days: number;
  /** 009 FR-CRMAI-RULE-01: the rule parameters, whole days 1–365. */
  stuck_discovery_days: number;
  stuck_qualification_days: number;
  stuck_proposal_days: number;
  stuck_negotiation_days: number;
  no_reply_days: number;
  lead_cold_days: number;
}

/** `automations` — the switches of automations 2, 5, 6, 7 and 8 (FR-CRM-AUTO-02). */
export interface AutomationSwitches {
  email_records: boolean;
  stale_notice: boolean;
  digests: boolean;
  task_reminders: boolean;
  campaign_conversion: boolean;
  /** 009 FR-CRMAI-RULE-01: R-1 to R-4, seeded off. */
  no_reply_draft: boolean;
  stuck_notice: boolean;
  lead_cold: boolean;
  campaign_followthrough: boolean;
}

// ---------------------------------------------------------------------------
// 1b — Gmail label sync (contracts §2.10; S10, FR-CRM-SYNC)
// ---------------------------------------------------------------------------

/** A thread on a record's Emails tab: metadata only, never a body (Q-29). */
export interface CrmEmailThread {
  thread_id: string;
  subject: string | null;
  last_at: string;
  direction: "inbound" | "outbound";
  participants: { email: string; name: string }[];
  /** The synced mailbox it came from. */
  mailbox: string | null;
  messages: number;
  /** 009 S1c: `<mailbox_id>:<gmail thread id>` (FR-CRMAI-MCP-01). */
  thread_key: string;
  /** 009 S1c: present while phase 1 is on and the thread has a summary. */
  summary?: CrmThreadSummaryBody;
}

export interface CrmEmailThreads {
  threads: CrmEmailThread[];
  /** Pass as `before` for older threads; null at the end. */
  next: string | null;
}

export interface CrmMailbox {
  id: string;
  email: string;
  owner_id: string;
  owner_name: string | null;
  labels: string[];
  is_active: boolean;
  backfilled_at: string | null;
  last_synced_at: string | null;
  updated_at: string;
  /** 010 S1 (FR-CAP-SRC-01): Labels only, or Primary and Sent as well. */
  capture_source: CrmCaptureSource;
  /** YYYY-MM-DD: where a Primary and Sent backfill starts (FR-CAP-SRC-07). */
  capture_start_date: string;
  /** The last deal-document search (FR-CAP-SRC-06); null before the first. */
  docs_checked_at: string | null;
}

/** 010 S1 — what a mailbox syncs (FR-CAP-SRC-01). */
export type CrmCaptureSource = "labels" | "primary_sent";

export interface CrmMailboxes {
  mailboxes: CrmMailbox[];
  sync: {
    /** The workspace's switch. */
    enabled: boolean;
    /** The Gmail credential exists on the sync worker (never the key itself). */
    configured: boolean;
    /** Both: sync runs every 15 minutes. */
    live: boolean;
  };
  /** The domains the service account can read (FR-CRM-SYNC-03). */
  gmail_domains: string[];
}

export interface CrmMailboxIn {
  email: string;
  owner_id: string;
  labels: string[];
  is_active?: boolean;
  /** Default "labels". */
  capture_source?: CrmCaptureSource;
  /** Default 2026-04-01; never in the future. */
  capture_start_date?: string;
}

export type EmailSyncResult =
  | { skipped: "Email sync is off" | "A sync is already running" }
  | {
      run_id: string;
      outcome: "running" | "ok" | "failed";
      mailboxes: number;
      fetched: number;
      stored: number;
      removed: number;
      matched_people: number;
      created_companies: number;
      created_people: number;
      skipped_internal: number;
    };

// ---------------------------------------------------------------------------
// 1b (S11b) — the dashboard, bookings targets, CSV import and export
// (contracts §2.9, §2.10; mirrors schemas/crm.py TargetIn, CsvMappingIn,
// ExportViewIn and services/crm/dashboard.py)
// ---------------------------------------------------------------------------

export type Colour = "green" | "amber" | "red";

/** A money total of open deals: decimal strings in INR. */
export interface PipelineFigures {
  count: number;
  amount_in_inr: string | null;
  weighted: string | null;
}

/** A booked figure against its target; a colour only when a target is set. */
export interface BookedFigure {
  value: string | null;
  target: string | null;
  colour?: Colour;
  count: number;
}

export interface BookingsRow extends BookedFigure {
  period: "month" | "quarter" | "fy";
  label: string;
  start: string;
  end: string;
  by_entity?: (BookedFigure & { entity: CrmEntity; entity_label: string })[];
}

export interface WinRateRow {
  won: number;
  lost: number;
  /** Percent, one decimal; null when nothing closed. */
  win_rate: string | null;
  average_deal_size: string | null;
}

export interface ActivityCell {
  week_start: string;
  emails_in: number;
  emails_out: number;
  notes: number;
  tasks: number;
}

/** GET /crm/dashboard — FR-CRM-DASH-01 to -04. */
export interface CrmDashboard {
  workspace: { key: string; name: string };
  today: string;
  filters: {
    entity: CrmEntity | null;
    owner_id: string | null;
    channel_id: string | null;
    from: string | null;
    to: string | null;
  };
  own_numbers_only: boolean;
  range: { from: string; to: string };
  pipeline_by_stage: {
    stages: (PipelineFigures & { stage: StageKey; stage_label: string })[];
    total: PipelineFigures;
  };
  bookings: BookingsRow[];
  won_lost_quarter: {
    start: string;
    end: string;
    won: { count: number; amount_in_inr: string | null };
    lost: { count: number; amount_in_inr: string | null };
    lost_reasons: { lost_reason_id: string | null; label: string; count: number }[];
  };
  win_rate: {
    overall: WinRateRow;
    by_entity?: (WinRateRow & { entity: CrmEntity; entity_label: string })[];
    by_channel: (WinRateRow & { channel_id: string | null; label: string })[];
  };
  time_in_stage: {
    stages: { stage: StageKey; stage_label: string; average_days: string | null; stays: number }[];
    days_to_close: { average_days: string | null; deals: number };
  };
  flags: { kind: string; label: string; value: number; colour: Colour }[];
  lead_funnel: {
    stages: { lead_stage: string; label: string; count: number; reached: number; conversion: string | null }[];
    disqualified: number;
  };
  pipeline_by_channel: (PipelineFigures & { channel_id: string | null; label: string })[];
  pipeline_by_campaign: (PipelineFigures & { campaign_id: string | null; label: string })[];
  pipeline_by_institution_type?: (PipelineFigures & { institution_type_id: string | null; label: string })[];
  pipeline_by_programme?: (PipelineFigures & { programme_id: string | null; label: string })[];
  activity: {
    weeks: string[];
    owners: {
      owner_id: string;
      name: string | null;
      weeks: ActivityCell[];
      totals: Omit<ActivityCell, "week_start">;
    }[];
  };
  /** Labs, for admins and staff members only (FR-CRM-CAP-02). */
  capacity?: CrmCapacity;
}

export interface CrmDashboardFilters {
  entity?: CrmEntity;
  owner_id?: string;
  channel_id?: string;
  from?: string;
  to?: string;
}

/** GET, PUT /crm/targets — FR-CRM-LIST-06. */
export interface CrmTarget {
  id: string;
  entity: CrmEntity | null;
  entity_label: string | null;
  period_kind: "month" | "quarter" | "fy";
  period_start: string;
  period_end: string;
  amount_inr: string;
  updated_at: string;
}

export type CrmTargetIn = Pick<CrmTarget, "entity" | "period_kind" | "period_start" | "amount_inr">;

/** The objects a CSV imports (FR-CRM-IO-01). */
export type CsvObject = "company" | "person" | "opportunity";
export type CsvMode = "skip" | "update" | "create";
/** CSV header → field, or null to leave the column out. */
export type CsvMapping = Record<string, string | null>;

export interface CrmCsvMapping {
  id: string;
  object: CsvObject;
  name: string;
  mapping: CsvMapping;
  updated_at: string;
}

export interface CsvPreviewRow {
  /** 1-based, the header not counted. */
  row: number;
  action: CsvMode;
  unchanged: boolean;
  match: { id: string; name: string; by: "id" | "domain" | "email" } | null;
  errors: string[];
  warnings: string[];
  /** The row's cells, in the header's order. */
  cells: string[];
}

/** POST /crm/imports/dry-run with format csv. */
export interface CsvDryRun extends Omit<ImportDryRun, "counts"> {
  object: CsvObject;
  counts: Partial<Record<"companies" | "people" | "opportunities", ImportCounts>>;
  headers: string[];
  mapping: CsvMapping;
  fields: { field: string; label: string }[];
  rows: CsvPreviewRow[];
}

/** POST /crm/imports/{run_id}/apply with format csv. */
export interface CsvApplied {
  run_id: string;
  counts: Partial<Record<"companies" | "people" | "opportunities", ImportCounts>>;
  created: number;
  updated: number;
  failed: { row: number; detail: string }[];
  fixups_used: number;
}

export interface CsvImportIn {
  content: string;
  filename: string;
  object: CsvObject;
  mapping?: CsvMapping;
  mapping_id?: string;
  mode?: CsvMode;
  actions?: Record<string, CsvMode>;
  fixup_next_step?: boolean;
}

// ---------------------------------------------------------------------------
// 009 S1a — the AI settings, spend and phase switches (009 contracts §1, §2.1)
// ---------------------------------------------------------------------------

/** The four phases: 1 summaries; 2 meetings and enrichment; 3 AI fields and
 *  follow-up rules; 4 contact recency (FR-CRMAI-FLAG-01). */
export type CrmAiPhase = "1" | "2" | "3" | "4";

export type CrmAiPhases = Record<CrmAiPhase, boolean>;

/** The `ai` setting, PATCH /crm/settings/ai (FR-CRMAI-FLAG-01, -LLM-07).
 *  Budget in US dollars, 0 to 10000, at most two decimals. */
export interface AiSettingsValue {
  budget_usd: number;
  phases: CrmAiPhases;
}

/** The AI jobs whose estimated spend Settings, AI shows. */
export type CrmAiSpendJob =
  | "summaries"
  | "meetings"
  | "enrichment"
  | "ai_fields"
  | "followup_drafts"
  | "capture";

/** GET /crm/ai — Admin only; the one route that returns spend (FR-CRMAI-LLM-09, -10).
 *  Amounts are decimal strings in USD. */
export interface CrmAiOverview {
  /** YYYY-MM, an Asia/Kolkata calendar month. */
  month: string;
  budget_usd: string;
  spend: { total_usd: string; by_job: Partial<Record<CrmAiSpendJob, string>> };
  budget_reached: boolean;
  phases: CrmAiPhases;
  estimate_note: string;
  /** False while either rate of the rate table is unset: every AI job is skipped (D-1). */
  rates_configured: boolean;
}

/** Bootstrap `ai`, for every role; never the budget (FR-CRMAI-FLAG-03, -04). */
export interface CrmBootstrapAi {
  phases: CrmAiPhases;
  review_visible: boolean;
  open_review_count: number;
}

// ---------------------------------------------------------------------------
// 009 S1b — the Review Queue and Today's To review (009 contracts §0, §1, §2.2)
// ---------------------------------------------------------------------------

/** Where an AI value came from (P-2). */
export type AiSourceKind = "email_thread" | "meeting" | "enrichment" | "ai_field" | "rule";

/** The provenance every AI value carries; the AI marker shows it (009 contracts §0). */
export interface AiProvenance {
  source_kind: AiSourceKind;
  /** Null for a deterministic rule (R-2, R-3, R-4). */
  model: string | null;
  generated_at: string | null;
  source?: { record_type: ViewObjectKind; record_id: string } | { thread_key: string } | null;
}

/** A meeting note's provenance (009 data-model §3.4); the transcript link is the only URL stored. */
export interface CrmMeetingProvenance extends AiProvenance {
  source_kind: "meeting";
  transcript_updated_at: string | null;
  transcript_url: string | null;
}

export type ReviewItemType =
  | "next_step_change"
  | "task_from_commitment"
  | "task_from_meeting"
  | "deal_link"
  | "enrichment_conflict"
  | "rule_suggestion"
  /** 010 S1 (FR-CAP-REV-01): a deal document or discovery on a known company. */
  | "deal_signal"
  /** 010 S3 (FR-CAP-REV-01, -02): an unknown conversation judged a lead. */
  | "new_lead";

export type ReviewItemStatus = "open" | "approved" | "dismissed" | "expired";

/** An open deal the suggestion may apply to (Q-07, FR-CRMAI-MEET-04). */
export interface ReviewDealChoice {
  id: string;
  name: string;
  next_step: string | null;
  next_step_on: string | null;
}

/** One Review Queue item — 009 contracts §2.2.1 (FR-CRMAI-REV-01).
 *  `suggestion` and `current` hold the type's fields (data-model §3.2.1);
 *  every string in them is generated plain text. */
export interface CrmReviewItem {
  id: string;
  type: ReviewItemType;
  type_label: string;
  status: ReviewItemStatus;
  /** Counts each refresh of the suggestion; an approval sends it back. */
  version: number;
  /** Null: the item belongs to the workspace's Admins (FR-CRMAI-REV-02). */
  owner: { id: string; name: string } | null;
  /** Null for a 010 `new_lead` that matched no company (contracts §1.3). */
  target: { record_type: "company" | "person" | "opportunity" | "note"; record_id: string; name: string } | null;
  deal_choices: ReviewDealChoice[];
  suggestion: Record<string, unknown>;
  current: Record<string, unknown>;
  source: {
    kind: "email_thread" | "meeting" | "enrichment" | "rule";
    label: string;
    link: {
      thread_key?: string;
      record_type?: ViewObjectKind;
      record_id?: string;
      /** 010 Revision 1 (contracts §7.4): a capture item's conversations. */
      threads?: CrmCaptureThreadRef[];
    } | null;
  };
  ai: AiProvenance | null;
  /** The last refusal of an approval; the item stays open (FR-CRMAI-REV-06). */
  refusal: string | null;
  created_at: string;
  expires_on: string;
  decided_at: string | null;
  decided_by: { id: string; name: string | null } | null;
  /** 010 Revision 1 (contracts §7.4): a capture item merged into another. */
  result?: { superseded_by?: string } | null;
}

/** 010 Revision 1 (contracts §7.4) — one conversation of a capture item;
 *  `reason` is generated plain text. */
export interface CrmCaptureThreadRef {
  ref: string;
  mailbox_id?: string;
  thread_id: string;
  message_id: string;
  first_at: string;
  latest_at: string;
  message_count: number;
  owner_replied: boolean;
  origin: string;
  reason: string;
  confidence: string;
  person_email: string;
  first_name?: string | null;
  last_name?: string | null;
}

/** GET /crm/review — a Viewer gets no items. */
export interface CrmReviewList {
  items: CrmReviewItem[];
  total: number;
}

/** GET /crm/review query: `all` and `decided` are an Admin's (D-3). */
export interface ReviewListQuery {
  scope?: "mine" | "all";
  status?: "open" | "decided";
  limit?: number;
}

/** POST /crm/review/{id}/approve. `version` is the item's as shown (a
 *  refreshed suggestion is refused 409); `edits` is "Edit and approve". */
export interface ReviewApproveIn {
  version: number;
  deal_id?: string;
  option?: string;
  edits?: Record<string, unknown>;
}

/** What approving produced, through the manual path. */
export interface ReviewApproveResult {
  item: CrmReviewItem;
  result: {
    opportunity?: CrmOpportunity;
    company?: CrmCompany;
    person?: CrmPerson;
    member?: CrmCampaignMember;
    task?: CrmTask;
    note?: CrmNote;
    used?: boolean;
    /** 010 `new_lead` (contracts §1.3). */
    company_id?: string;
    person_id?: string;
    linked?: boolean;
    filed?: "queued";
    /** 010 Revision 1 (contracts §7.4). */
    people?: number;
    threads_filed?: number;
  };
}

/** POST /crm/review/{id}/dismiss — 010 contracts §1.3: a `new_lead` may add
 *  its sender or its whole domain to the ignore list. */
export type ReviewDismissIgnore = "address" | "domain";

export interface ReviewDismissResult {
  item: CrmReviewItem;
  ignore?: { id: string; kind: ReviewDismissIgnore; value: string } | null;
}

/** One line of To review: an item, opening the queue. */
export interface ToReviewItem {
  record_type: "review_item";
  record_id: string;
  title: string;
  detail: string;
  created_at: string;
}

/** Today's sixth section (FR-CRMAI-REV-09): the count and the three oldest. */
export interface ToReviewSection {
  key: "to_review";
  count: number;
  items: ToReviewItem[];
}

// ---------------------------------------------------------------------------
// 009 S1c — email thread summaries (009 contracts §1, §2.3; FR-CRMAI-SUM)
// ---------------------------------------------------------------------------

export type ThreadSentiment = "positive" | "neutral" | "concern";

/** What Nunnari promised; the owner and date only when the thread stated them. */
export interface ThreadCommitment {
  text: string;
  owner_name: string | null;
  due_on: string | null;
}

/** A thread's summary as the Emails tab carries it. Every string is
 *  generated plain text (FR-CRMAI-LLM-04). */
export interface CrmThreadSummaryBody {
  summary: string;
  asks: string[];
  commitments: ThreadCommitment[];
  sentiment: ThreadSentiment;
  ai: AiProvenance;
}

/** GET /crm/email-threads/{thread_key}/summary, and each of a record's
 *  `summaries`. */
export interface CrmThreadSummary extends CrmThreadSummaryBody {
  thread_key: string;
  subject: string | null;
  last_at: string;
  next_step?: string | null;
  next_step_on?: string | null;
  links: { record_type: "company" | "person" | "opportunity"; record_id: string; name: string }[];
}

// ---------------------------------------------------------------------------
// 009 S2b — company enrichment (009 contracts §1, §2.4; FR-CRMAI-ENR)
// ---------------------------------------------------------------------------

export type EnrichmentStatus = "queued" | "done" | "failed";

/** The main fields an enriched value promotes to (spec 009 §7.2). */
export type EnrichmentField = "industry_id" | "size_id" | "city" | "country" | "description" | "linkedin";

/** What was read from the company's own website (data-model §3.5). Every
 *  string is generated plain text (FR-CRMAI-LLM-04). */
export interface CrmEnrichmentValues {
  industry?: string | null;
  sub_industry?: string | null;
  employee_band?: string | null;
  city?: string | null;
  country?: string | null;
  description?: string | null;
  linkedin?: string | null;
  products?: string[];
}

/** A company's `enrichment` on its record read (009 contracts §1). */
export interface CrmEnrichment {
  status: EnrichmentStatus;
  /** Set when failed, from a fixed list (FR-CRMAI-ENR-06). */
  failure_reason?: string | null;
  values: CrmEnrichmentValues;
  /** True where the main field is empty and the value may be promoted (FR-CRMAI-ENR-04). */
  promotable: Partial<Record<EnrichmentField, boolean>>;
  /** Open `enrichment_conflict` items, where the main field holds another value (FR-CRMAI-ENR-05). */
  conflicts: string[];
  ai: AiProvenance | null;
}

/** POST /crm/enrichment/run — **Enrich selected** (FR-CRMAI-ENR-01, -08). */
export interface CrmEnrichmentRun {
  queued: number;
  skipped_no_website: number;
  already_enriched: number;
}

// ---------------------------------------------------------------------------
// 009 S3a — AI fields (009 contracts §1, §2.5; FR-CRMAI-FIELD)
// ---------------------------------------------------------------------------

export type AiFieldRecordType = "company" | "opportunity";

export type AiFieldOutputType = "text" | "single_select" | "number";

/** GET /crm/ai-fields — one definition (FR-CRMAI-FIELD-01); Admins only. */
export interface CrmAiField {
  id: string;
  key: string;
  name: string;
  record_type: AiFieldRecordType;
  prompt: string;
  output_type: AiFieldOutputType;
  /** 2 to 12 for a single select; empty otherwise. */
  options: string[];
  enabled: boolean;
}

/** POST /crm/ai-fields. */
export interface CrmAiFieldIn {
  name: string;
  record_type: AiFieldRecordType;
  prompt: string;
  output_type: AiFieldOutputType;
  options?: string[];
  enabled?: boolean;
}

/** PATCH /crm/ai-fields/{id}: record type and output type are fixed after create. */
export interface CrmAiFieldPatch {
  name?: string;
  prompt?: string;
  options?: string[];
  enabled?: boolean;
}

/** One entry of a company's or deal's `ai_fields` on its record read (009 contracts §1).
 *  Text and the reason are generated plain text (FR-CRMAI-LLM-04). */
export interface CrmAiFieldValue {
  key: string;
  name: string;
  output_type: AiFieldOutputType;
  /** A number output is 0–100, shown on the record page only (Q-27). */
  value: string | number | null;
  reason?: string | null;
  ai: AiProvenance | null;
}

/** POST /crm/{companies|opportunities}/{id}/ai-fields/recompute (Q-38). */
export interface CrmAiFieldRecompute {
  queued: boolean;
  next_allowed_at: string | null;
}

// ---------------------------------------------------------------------------
// 009 S4 — contact recency (009 contracts §1; FR-CRMAI-REL)
// ---------------------------------------------------------------------------

/** Derived, not AI (FR-CRMAI-REL-01, Q-21). */
export type ContactRecencyLevel = "recent" | "fading" | "lapsed";

/** A company's or person's `contact_recency` on its record read. The level is
 *  null until the flags job first computes it (data-model §3.10). */
export interface CrmContactRecency {
  level: ContactRecencyLevel | null;
  /** Gmail sync is off for the workspace: counted from meeting notes only (FR-CRMAI-REL-03). */
  meetings_only: boolean;
}

// ---------------------------------------------------------------------------
// 010 S2 — Settings, Email capture (010 contracts §2.1, §2.2; FR-CAP-DIS-01,
// FR-CAP-RPT-01 to -03; mirrors schemas/crm.py CaptureOverviewOut)
// ---------------------------------------------------------------------------

/** The `capture` setting: Dry run is started from Email capture, never set. */
export type CrmCaptureMode = "off" | "dry_run" | "live";

export type CrmCaptureOutcome = "new_lead" | "deal_signal" | "other_business" | "not_relevant";
export type CrmCaptureConfidence = "high" | "medium" | "low";

/** An ignore-list entry (FR-CAP-DIS-03). */
export interface CrmCaptureIgnore {
  id: string;
  kind: "address" | "domain";
  value: string;
  from_item_id: string | null;
  created_by_name: string | null;
  created_at: string;
}

/** A judged lead or deal signal of every confidence; low is listed apart (FR-CAP-DIS-07).
 *  `reason` is generated plain text; no message text is ever returned. */
export interface CrmCaptureFinding {
  candidate_id: string;
  outcome: "new_lead" | "deal_signal";
  confidence: CrmCaptureConfidence;
  company_name: string | null;
  domain: string | null;
  /** The host came from the text, not an address: the reviewer confirms it. */
  domain_from_text: boolean;
  person_name: string | null;
  person_email: string | null;
  first_at: string | null;
  latest_at: string | null;
  message_count: number;
  owner_replied: boolean;
  reason: string | null;
  duplicate_company: Record<string, unknown> | null;
}

export type CrmCaptureRecallFate =
  | "filed_known"
  | "found"
  | "judged_otherwise"
  | "skipped_rules"
  | "outside_scope"
  /** Every outside person on the ignore list: never sent to Claude. */
  | "ignored"
  /** Names only people added to the CRM after it began: never sent to Claude (G-5). */
  | "known_now"
  /** Every message dropped by the rules as internal mail (FR-CAP-RPT-06). */
  | "internal_only"
  /** Every message dropped by the rules as a Google Calendar notice. */
  | "calendar"
  /** Every message dropped by the rules as automated mail. */
  | "automated"
  /** Gmail returned it empty or malformed, or Claude failed. */
  | "not_judged"
  /** Found as a course enquiry with no Academy workspace to take it. */
  | "academy_unrouted";

/** One Sales- or Leads-labelled conversation of the recall check (FR-CAP-RPT-03). */
export interface CrmCaptureRecallRow {
  thread_id: string;
  latest_at: string | null;
  /** Only for a thread already stored as captured mail. */
  subject: string | null;
  fate: CrmCaptureRecallFate;
}

/** The recall line's numbers (contracts §7.1, FR-CAP-RPT-08). */
export interface CrmCaptureRecallSummary {
  total: number;
  tested: number;
  found: number;
  /** The check stopped at 500 with labelled conversations still unchecked. */
  capped: boolean;
  untested: {
    known: number;
    ignored: number;
    internal_only: number;
    calendar: number;
    automated: number;
    outside_scope: number;
    not_judged: number;
    /** Runs before 074 only. */
    skipped_rules: number;
  };
  /** The part of `found` the Academy could not take. */
  academy_unrouted: number;
}

/** The report's counts, live while the dry run is open. */
export interface CrmCaptureCounts {
  considered: number;
  pending: number;
  skipped_rules: number;
  ignored: number;
  known: number;
  judged: number;
  failed: number;
  filed_known_threads: number;
  by_outcome: Record<CrmCaptureOutcome, Record<CrmCaptureConfidence, number>>;
}

export interface CrmCaptureDryRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  /** The mode was changed away from Dry run before it finished. */
  cancelled: boolean;
  applied_at: string | null;
  /** Finished, not cancelled, not applied, the latest, and finished within 7 days. */
  applicable: boolean;
  counts: CrmCaptureCounts;
  /** Decimal string, USD. */
  estimated_cost_usd: string;
  findings: CrmCaptureFinding[];
  recall: {
    line: string;
    rows: CrmCaptureRecallRow[];
    /** Absent only from a run the backend has not summarised. */
    summary?: CrmCaptureRecallSummary | null;
  };
}

/** GET /crm/capture — Admin only. */
export interface CrmCaptureOverview {
  mode: CrmCaptureMode;
  /** Whether Live can be chosen: a dry run of the workspace was ever applied
   *  and an active mailbox is on Primary and Sent (contracts §1.2). */
  live_allowed: boolean;
  mailboxes: {
    id: string;
    email: string;
    capture_source: CrmCaptureSource;
    capture_start_date: string;
    backfilled_at: string | null;
  }[];
  ignores: CrmCaptureIgnore[];
  /** Null when none has run. */
  dry_run: CrmCaptureDryRun | null;
}

/** POST /crm/capture/dry-run — 202. */
export interface CrmCaptureDryRunStarted {
  dry_run: { id: string; started_at: string };
}

// ---------------------------------------------------------------------------
// 010 S3 — Apply and the ignore list (010 contracts §2.3 to §2.5; FR-CAP-RPT-04,
// FR-CAP-DIS-03)
// ---------------------------------------------------------------------------

/** POST /crm/capture/dry-runs/{id}/apply — 200. */
export interface CrmCaptureApplied {
  applied_at: string;
  items_created: number;
  mode: "live";
}

/** POST /crm/capture/ignores — trimmed and lower-cased by the server. */
export interface CrmCaptureIgnoreIn {
  kind: "address" | "domain";
  value: string;
}

/** POST /crm/capture/ignores — 201. */
export interface CrmCaptureIgnoreAdded {
  ignore: { id: string; kind: "address" | "domain"; value: string; created_at: string };
}
