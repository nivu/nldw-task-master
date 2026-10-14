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
  CrmCapacity,
  CrmColor,
  CrmDriveLink,
  CrmDuplicates,
  CrmFxRate,
  CrmLists,
  CrmMember,
  CrmObject,
  CrmOption,
  CrmPage,
  CrmRecord,
  CrmRecordPage,
  CrmRole,
  CrmSettings,
  CrmTimelineEntry,
  CrmTrashRow,
  CrmWorkspace,
  InvitationIn,
  InvitationResult,
  NotCreated,
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

// ---------------------------------------------------------------------------
// Capacity — §2.8 (Labs; admins and staff members)
// ---------------------------------------------------------------------------

export const getCrmCapacity = (workspace: string, weeks?: number) =>
  call<CrmCapacity>(`/capacity${query(workspace, { weeks })}`);

// ---------------------------------------------------------------------------
// Records — §2.5 (companies, people, Drive links, Trash)
// ---------------------------------------------------------------------------

export const listCrmRecords = <T extends CrmRecord>(
  workspace: string,
  object: CrmObject,
  options: { q?: string; limit?: number; after?: string | null } = {}
) =>
  call<CrmPage<T>>(
    `/${object}${query(workspace, { q: options.q, limit: options.limit, after: options.after ?? undefined })}`
  );

export const getCrmRecord = <T extends CrmRecord>(workspace: string, object: CrmObject, id: string) =>
  call<CrmRecordPage<T>>(`/${object}/${id}${query(workspace)}`);

/** With `checkDuplicates`, names alike come back instead and nothing is
 *  created (FR-CRM-DUP-02); call again without it to continue. */
export const createCrmRecord = <T extends CrmRecord>(
  workspace: string,
  object: CrmObject,
  fields: Record<string, unknown>,
  checkDuplicates: boolean
) =>
  call<T | NotCreated>(
    `/${object}${query(workspace, { check_duplicates: String(checkDuplicates) })}`,
    { method: "POST", ...body(fields) }
  );

/** `expect_updated_at` is the record's `updated_at` as last read: a change
 *  made meanwhile is refused (409) rather than overwritten. */
export const updateCrmRecord = <T extends CrmRecord>(
  workspace: string,
  object: CrmObject,
  id: string,
  changes: Record<string, unknown>,
  expectUpdatedAt: string
) =>
  call<T>(`/${object}/${id}${query(workspace)}`, {
    method: "PATCH",
    ...body({ ...changes, expect_updated_at: expectUpdatedAt }),
  });

/** A company says what happens to its people (FR-CRM-DEL-02). */
export const deleteCrmRecord = (
  workspace: string,
  object: CrmObject,
  id: string,
  choice?: { people: "unlink" | "delete" }
) =>
  call<{ deleted_group: string }>(`/${object}/${id}${query(workspace)}`, {
    method: "DELETE",
    ...(choice ? body(choice) : {}),
  });

export const restoreCrmRecord = <T extends CrmRecord>(workspace: string, object: CrmObject, id: string) =>
  call<T>(`/${object}/${id}/restore${query(workspace)}`, { method: "POST" });

export const purgeCrmRecord = (workspace: string, object: CrmObject, id: string) =>
  call<{ purged: number }>(`/${object}/${id}/permanent${query(workspace)}`, { method: "DELETE" });

export const getCrmTimeline = (
  workspace: string,
  object: CrmObject,
  id: string,
  options: { types?: string; before?: number; limit?: number } = {}
) => call<CrmTimelineEntry[]>(`/${object}/${id}/timeline${query(workspace, options)}`);

export const findCrmDuplicates = (
  workspace: string,
  object: CrmObject,
  typed: { name?: string; domain?: string; email?: string }
) => call<CrmDuplicates>(`/${object}/duplicates${query(workspace)}`, { method: "POST", ...body(typed) });

export const getCrmTrash = (workspace: string, object: CrmObject, after?: string | null) =>
  call<CrmPage<CrmTrashRow>>(`/trash${query(workspace, { object, after: after ?? undefined })}`);

export const addCrmDriveLink = (
  workspace: string,
  link: { record_type: CrmDriveLink["record_type"]; record_id: string; url: string; label: string }
) => call<CrmDriveLink>(`/links${query(workspace)}`, { method: "POST", ...body(link) });

export const removeCrmDriveLink = (workspace: string, id: string) =>
  call<{ deleted_group: string }>(`/links/${id}${query(workspace)}`, { method: "DELETE" });
