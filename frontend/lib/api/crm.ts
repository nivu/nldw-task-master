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
  CrmAuditEntry,
  CrmAuditFilters,
  CrmBootstrap,
  CrmColor,
  CrmFxRate,
  CrmLists,
  CrmOption,
  CrmSettings,
  CrmWorkspace,
  OptionUpdate,
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
