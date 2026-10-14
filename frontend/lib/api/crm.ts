/**
 * Calls to the sales CRM's API — spec 007 contracts/api.md §2.
 *
 * Every request goes through `backendFetch` and so through `/api/proxy`, the
 * only path from the browser to the backend (FR-CRM-ACC-10): the CRM reads
 * nothing from Supabase directly. Each workspace-scoped call names its
 * workspace, so a write never depends on the server's last-used default
 * (D13).
 */

import { backendFetch } from "@/lib/api/backend";
import type {
  AccountTypeResult,
  CrmAuditEntry,
  CrmAuditFilters,
  CrmBootstrap,
  CrmColor,
  CrmFxRate,
  CrmLists,
  CrmMember,
  CrmOption,
  CrmRole,
  CrmSettings,
  CrmWorkspace,
  InvitationIn,
  InvitationResult,
  OptionUpdate,
  TvaCrmAuditEntry,
  TvaCrmWorkspace,
  TvaInvitationIn,
} from "@/lib/api/crm-types";
import type { MePreferences } from "@/lib/api/types";
import { createClient } from "@/lib/supabase/client";

async function accessToken(): Promise<string> {
  const { data } = await createClient().auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    // As in portal.ts: a session that went away mid-use goes to sign-in
    // rather than firing a request that is certain to 401.
    window.location.href = "/auth/login";
    throw new Error("Not signed in");
  }
  return token;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  return backendFetch<T>(`/api/v1/crm${path}`, { ...init, token: await accessToken() });
}

function body(payload: unknown): RequestInit {
  return { body: JSON.stringify(payload) };
}

/** `?workspace=<key>` plus any other query values that are set. */
function query(workspace: string, extra: Record<string, string | number | undefined> = {}) {
  const params = new URLSearchParams({ workspace });
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return `?${params.toString()}`;
}

// ---------------------------------------------------------------------------
// Workspaces, bootstrap, preferences — §2.1
// ---------------------------------------------------------------------------

export const getCrmWorkspaces = () => call<CrmWorkspace[]>("/workspaces");

export const selectCrmWorkspace = (key: string) =>
  call<{ ok: boolean }>(`/workspaces/${encodeURIComponent(key)}/use`, { method: "POST" });

export const getCrmBootstrap = (workspace: string) =>
  call<CrmBootstrap>(`/bootstrap${query(workspace)}`);

export const getCrmPreferences = () => call<MePreferences>("/me/preferences");

export const updateCrmPreferences = (changes: Partial<MePreferences>) =>
  call<MePreferences>("/me/preferences", { method: "PATCH", ...body(changes) });

// ---------------------------------------------------------------------------
// Members and invitations — §2.2
// ---------------------------------------------------------------------------

export const getCrmMembers = (workspace: string) =>
  call<CrmMember[]>(`/members${query(workspace)}`);

export const inviteCrmMember = (workspace: string, invitation: InvitationIn) =>
  call<InvitationResult>(`/invitations${query(workspace)}`, {
    method: "POST",
    ...body(invitation),
  });

export const updateCrmMember = (workspace: string, membershipId: string, role: CrmRole) =>
  call<{ membership_id: string; user_id: string; role: CrmRole }>(
    `/members/${membershipId}${query(workspace)}`,
    { method: "PATCH", ...body({ role }) }
  );

export const removeCrmMember = (workspace: string, membershipId: string) =>
  call<{ removed: boolean; account_deactivated: boolean }>(
    `/members/${membershipId}${query(workspace)}`,
    { method: "DELETE" }
  );

// ---------------------------------------------------------------------------
// TVA admin panel — §2.4. No `workspace` parameter: a TVA admin needs no
// membership, and the workspace is in the path.
// ---------------------------------------------------------------------------

export const getTvaCrmWorkspaces = () => call<TvaCrmWorkspace[]>("/tva-admin/workspaces");

export const getTvaCrmMembers = (workspaceId: string) =>
  call<CrmMember[]>(`/tva-admin/workspaces/${workspaceId}/members`);

export const setTvaAllowedDomains = (workspaceId: string, domains: string[]) =>
  call<{ id: string; key: string; allowed_domains: string[] }>(
    `/tva-admin/workspaces/${workspaceId}/allowed-domains`,
    { method: "PUT", ...body({ domains }) }
  );

export const tvaInviteCrmMember = (workspaceId: string, invitation: TvaInvitationIn) =>
  call<InvitationResult>(`/tva-admin/workspaces/${workspaceId}/invitations`, {
    method: "POST",
    ...body(invitation),
  });

export const tvaRemoveCrmMembership = (membershipId: string) =>
  call<{ removed: boolean; account_deactivated: boolean }>(
    `/tva-admin/memberships/${membershipId}`,
    { method: "DELETE" }
  );

export const tvaUpdateCrmMembership = (membershipId: string, role: CrmRole) =>
  call<{ membership_id: string; user_id: string; role: CrmRole }>(
    `/tva-admin/memberships/${membershipId}`,
    { method: "PATCH", ...body({ role }) }
  );

export const tvaSetAccountType = (userId: string, accountType: "staff" | "crm_only") =>
  call<AccountTypeResult>(`/tva-admin/accounts/${userId}`, {
    method: "PATCH",
    ...body({ account_type: accountType }),
  });

export const getTvaCrmAudit = (filters: { workspace_id?: string; before?: number; limit?: number } = {}) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return call<TvaCrmAuditEntry[]>(`/tva-admin/audit${qs ? `?${qs}` : ""}`);
};

// ---------------------------------------------------------------------------
// Settings, lists, FX rates, audit — §2.3
// ---------------------------------------------------------------------------

export const getCrmSettings = (workspace: string) =>
  call<CrmSettings>(`/settings${query(workspace)}`);

export const updateCrmSetting = (workspace: string, key: string, value: unknown) =>
  call<{ key: string; value: unknown }>(`/settings/${encodeURIComponent(key)}${query(workspace)}`, {
    method: "PATCH",
    ...body({ value }),
  });

export const getCrmLists = (workspace: string) => call<CrmLists>(`/lists${query(workspace)}`);

export const addCrmOption = (
  workspace: string,
  listKey: string,
  option: { label: string; color?: CrmColor | null }
) =>
  call<CrmOption>(`/lists/${encodeURIComponent(listKey)}/options${query(workspace)}`, {
    method: "POST",
    ...body(option),
  });

export const updateCrmOption = (
  workspace: string,
  listKey: string,
  optionId: string,
  changes: OptionUpdate
) =>
  call<CrmOption>(
    `/lists/${encodeURIComponent(listKey)}/options/${optionId}${query(workspace)}`,
    { method: "PATCH", ...body(changes) }
  );

export const getCrmFxRates = (workspace: string) =>
  call<CrmFxRate[]>(`/fx-rates${query(workspace)}`);

/** `month` is YYYY-MM; `rate` a decimal string, INR per one unit. */
export const setCrmFxRate = (
  workspace: string,
  currency: CrmFxRate["currency"],
  month: string,
  rate: string
) =>
  call<CrmFxRate>(`/fx-rates/${currency}/${month}${query(workspace)}`, {
    method: "PUT",
    ...body({ rate }),
  });

export const getCrmAudit = (workspace: string, filters: CrmAuditFilters = {}) =>
  call<CrmAuditEntry[]>(`/audit${query(workspace, { ...filters })}`);
