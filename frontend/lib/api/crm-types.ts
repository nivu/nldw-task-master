/**
 * Sales CRM types — spec 007 contracts/api.md §2.1, §2.3.
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
