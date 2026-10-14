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
  record_type?: "workspace" | "membership" | "setting" | "option" | "fx_rate";
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
