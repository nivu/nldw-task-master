/**
 * Calls to the sales CRM's API — spec 007 contracts/api.md §2.
 *
 * Every request goes through `backendFetch` and so through `/api/proxy`, the
 * only path from the browser to the backend (FR-CRM-ACC-10): the CRM reads
 * nothing from Supabase directly. Each workspace-scoped call names its
 * workspace, so a write never depends on the server's last-used default
 * (D13).
 */

import { backendFetch, upload } from "@/lib/api/backend";
import type {
  AccountTypeResult,
  BulkAction,
  BulkResult,
  CrmAuditEntry,
  CrmAuditFilters,
  CrmBootstrap,
  CrmCapacity,
  CrmColor,
  CrmCompany,
  CrmContact,
  CrmDriveLink,
  CrmDuplicates,
  CrmFxRate,
  CrmLists,
  CrmMember,
  CrmObject,
  CrmOpportunity,
  CrmOption,
  CrmPage,
  CrmFavourite,
  CrmJobName,
  CrmJobs,
  CrmNotification,
  CrmPerson,
  CrmQueryResult,
  CrmRecord,
  CrmRecordPage,
  CrmRole,
  CrmSavedView,
  CrmSearchResult,
  CrmSettings,
  CrmTask,
  CrmTimelineEntry,
  CrmToday,
  CrmTrashRow,
  CrmViewIn,
  CrmViewUpdate,
  CrmWorkspace,
  HandoffIn,
  HandoffPreview,
  HandoffResult,
  ImportApplied,
  ImportDryRun,
  ImportRun,
  InvitationIn,
  InvitationResult,
  LinkableProject,
  MergePreview,
  NotCreated,
  OptionUpdate,
  ProjectCategory,
  QuerySpec,
  StageChanges,
  StageKey,
  StageMoveResult,
  StagePreview,
  TaskStatus,
  TvaCrmAuditEntry,
  TvaCrmJobs,
  TvaCrmWorkspace,
  TvaInvitationIn,
  ViewObjectKind,
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

/** Companies as relation choices, searched on the server a page at a time,
 *  so a person can be linked to any live company, not only recent ones. */
export const searchCompanyChoices = (workspace: string, q: string, after: string | null) =>
  listCrmRecords<CrmCompany>(workspace, "companies", { q: q || undefined, limit: 50, after }).then(
    (page) => ({ choices: page.rows.map((c) => ({ value: c.id, label: c.name })), next: page.next })
  );

/** People as relation choices (a deal's point of contact and contacts). */
export const searchPersonChoices = (workspace: string, q: string, after: string | null) =>
  listCrmRecords<CrmPerson>(workspace, "people", { q: q || undefined, limit: 50, after }).then(
    (page) => ({
      choices: page.rows.map((p) => ({
        value: p.id,
        label: p.company_name ? `${p.name} · ${p.company_name}` : p.name,
      })),
      next: page.next,
    })
  );

/** Deals as relation choices (a task's or note's link). */
export const searchDealChoices = (workspace: string, q: string, after: string | null) =>
  listCrmRecords<CrmOpportunity>(workspace, "opportunities", { q: q || undefined, limit: 50, after }).then(
    (page) => ({ choices: page.rows.map((d) => ({ value: d.id, label: d.name })), next: page.next })
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

/** A company says what happens to its people and its deals (FR-CRM-DEL-02). */
export const deleteCrmRecord = (
  workspace: string,
  object: CrmObject,
  id: string,
  choice?: { people?: "unlink" | "delete"; deals?: "delete" | { move_to: string } }
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

// ---------------------------------------------------------------------------
// Deals, tasks — §2.5, §2.6
// ---------------------------------------------------------------------------

/** Labs: how a move into Proposal meets a project name clash (FR-CRM-TVA-05). */
export interface ProjectChoice {
  /** Name the new project something other than the deal. */
  project_name?: string;
  /** Link an existing tentative project instead (TVA managers and admins). */
  link_project_id?: string;
}

/** What a move would do, writing nothing (contracts §2.6). */
export const previewCrmStage = (
  workspace: string,
  dealId: string,
  stage: StageKey,
  changes: StageChanges = {},
  companyOnLoss?: "nurturing" | "disqualified",
  project: Pick<ProjectChoice, "project_name"> = {}
) =>
  call<StagePreview>(`/opportunities/${dealId}/stage/preview${query(workspace)}`, {
    method: "POST",
    ...body({
      stage,
      changes,
      ...(companyOnLoss ? { company_on_loss: companyOnLoss } : {}),
      ...project,
    }),
  });

/** The move and its side effects; a 422 names every missing field. */
export const moveCrmStage = (
  workspace: string,
  dealId: string,
  stage: StageKey,
  changes: StageChanges,
  expectUpdatedAt: string,
  companyOnLoss?: "nurturing" | "disqualified",
  project: ProjectChoice = {}
) =>
  call<StageMoveResult>(`/opportunities/${dealId}/stage${query(workspace)}`, {
    method: "POST",
    ...body({
      stage,
      changes,
      expect_updated_at: expectUpdatedAt,
      ...(companyOnLoss ? { company_on_loss: companyOnLoss } : {}),
      ...project,
    }),
  });

/** FR-CRM-TVA-07 — the Closed Won handoff, pre-filled. */
export const getCrmHandoff = (workspace: string, dealId: string) =>
  call<HandoffPreview>(`/opportunities/${dealId}/handoff${query(workspace)}`);

/** Closed Won and the project confirmed, in one step. */
export const confirmCrmHandoff = (workspace: string, dealId: string, handoff: HandoffIn) =>
  call<HandoffResult>(`/opportunities/${dealId}/handoff${query(workspace)}`, {
    method: "POST",
    ...body(handoff),
  });

/** FR-CRM-TVA-18 — Link TVA project (Labs admins). */
export const linkCrmProject = (
  workspace: string,
  dealId: string,
  link: { mode: "link" | "create"; project_id?: string; name?: string; category?: ProjectCategory },
  expectUpdatedAt?: string
) =>
  call<{ opportunity: CrmOpportunity; project: StageMoveResult["project"] }>(
    `/opportunities/${dealId}/link-project${query(workspace)}`,
    { method: "POST", ...body({ ...link, ...(expectUpdatedAt ? { expect_updated_at: expectUpdatedAt } : {}) }) }
  );

/** Projects a deal may be linked to; refused (403) to anyone but a TVA manager or admin. */
export const listLinkableProjects = (workspace: string, status?: "tentative" | "confirmed") =>
  call<LinkableProject[]>(`/projects/linkable${query(workspace, { status })}`);

export const addCrmContact = (
  workspace: string,
  dealId: string,
  contact: { person_id: string; role_id?: string | null }
) => call<CrmContact>(`/opportunities/${dealId}/contacts${query(workspace)}`, { method: "POST", ...body(contact) });

export const removeCrmContact = (workspace: string, dealId: string, personId: string) =>
  call<{ removed: boolean }>(`/opportunities/${dealId}/contacts/${personId}${query(workspace)}`, {
    method: "DELETE",
  });

/** The Tasks page: narrowed to an assignee and a status (`open` is not Done). */
export const listCrmTasks = (
  workspace: string,
  filters: { assignee_id?: string; status?: TaskStatus | "open"; q?: string; after?: string | null } = {}
) =>
  call<CrmPage<CrmTask>>(
    `/tasks${query(workspace, {
      assignee_id: filters.assignee_id,
      status: filters.status,
      q: filters.q,
      after: filters.after ?? undefined,
      limit: 100,
    })}`
  );

/** Q-57 — a Viewer may complete a task assigned to them. */
export const completeCrmTask = (workspace: string, taskId: string) =>
  call<CrmTask>(`/tasks/${taskId}/complete${query(workspace)}`, { method: "POST" });

// ---------------------------------------------------------------------------
// Views, favourites, bulk — §2.7, §2.5 (FR-CRM-VIEW, FR-CRM-UI-02)
// ---------------------------------------------------------------------------

/** One page of a list, filtered, sorted, grouped and totalled on the server. */
export const queryCrm = <T extends CrmRecord>(
  workspace: string,
  object: ViewObjectKind,
  spec: QuerySpec,
  view?: { view_id?: string; view?: string }
) =>
  call<CrmQueryResult<T>>(`/query${query(workspace)}`, {
    method: "POST",
    ...body({ object, spec, ...view }),
  });

export const listCrmViews = (workspace: string, object?: ViewObjectKind, includeHidden = false) =>
  call<CrmSavedView[]>(
    `/views${query(workspace, { object, include_hidden: includeHidden ? "true" : undefined })}`
  );

export const createCrmView = (workspace: string, view: CrmViewIn) =>
  call<CrmSavedView>(`/views${query(workspace)}`, { method: "POST", ...body(view) });

export const updateCrmView = (workspace: string, id: string, changes: CrmViewUpdate) =>
  call<CrmSavedView>(`/views/${id}${query(workspace)}`, { method: "PATCH", ...body(changes) });

/** A seeded view is hidden instead. */
export const deleteCrmView = (workspace: string, id: string) =>
  call<{ deleted?: boolean; hidden?: boolean }>(`/views/${id}${query(workspace)}`, {
    method: "DELETE",
  });

export const listCrmFavourites = (workspace: string) =>
  call<CrmFavourite[]>(`/favourites${query(workspace)}`);

export const addCrmFavourite = (
  workspace: string,
  pin: { target_type: "view" | "record"; target_id: string; record_type?: ViewObjectKind }
) => call<CrmFavourite>(`/favourites${query(workspace)}`, { method: "POST", ...body(pin) });

export const removeCrmFavourite = (workspace: string, id: string) =>
  call<{ deleted: boolean }>(`/favourites/${id}${query(workspace)}`, { method: "DELETE" });

export const bulkCrm = (
  workspace: string,
  object: ViewObjectKind,
  ids: string[],
  action: BulkAction,
  values: Record<string, unknown> = {}
) =>
  call<BulkResult>(`/bulk${query(workspace)}`, {
    method: "POST",
    ...body({ object, ids, action, values }),
  });

// ---------------------------------------------------------------------------
// Today, search, merge — §2.8 (FR-CRM-TODAY, FR-CRM-SRCH, FR-CRM-DUP-03)
// ---------------------------------------------------------------------------

/** The person's Today; `person` is another member's, for an Admin. */
export const getCrmToday = (workspace: string, person?: string) =>
  call<CrmToday>(`/today${query(workspace, { person })}`);

export const searchCrm = (workspace: string, q: string) =>
  call<CrmSearchResult>(`/search${query(workspace, { q })}`);

export const previewCrmMerge = (workspace: string, survivorId: string, mergedId: string) =>
  call<MergePreview>(`/companies/merge-preview${query(workspace, { a: survivorId, b: mergedId })}`);

export const mergeCrmCompanies = (
  workspace: string,
  merge: {
    survivor_id: string;
    merged_id: string;
    choices: Record<string, "a" | "b">;
    expect?: { survivor_updated_at: string; merged_updated_at: string };
  }
) => call<CrmCompany>(`/companies/merge${query(workspace)}`, { method: "POST", ...body(merge) });

// ---------------------------------------------------------------------------
// Imports — §2.9 (S8). The file goes up as multipart through the proxy,
// which carries uploads up to 4 MB (FR-CRM-CHG-08).
// ---------------------------------------------------------------------------

function importForm(file: File, fixup?: boolean): FormData {
  const form = new FormData();
  form.set("file", file);
  if (fixup !== undefined) form.set("fixup_next_step", fixup ? "true" : "false");
  return form;
}

export const dryRunCrmImport = async (workspace: string, file: File) =>
  upload<ImportDryRun>(
    `/api/v1/crm/imports/dry-run${query(workspace)}`,
    importForm(file),
    await accessToken()
  );

export const applyCrmImport = async (workspace: string, runId: string, file: File, fixup: boolean) =>
  upload<ImportApplied>(
    `/api/v1/crm/imports/${encodeURIComponent(runId)}/apply${query(workspace)}`,
    importForm(file, fixup),
    await accessToken()
  );

export const listCrmImports = (workspace: string) => call<ImportRun[]>(`/imports${query(workspace)}`);

// ---------------------------------------------------------------------------
// 1b — the bell and the job log (§2.4, §2.10; FR-CRM-NOTIF-01, FR-CRM-JOB)
// ---------------------------------------------------------------------------

/** The person's notifications, across their workspaces (no `workspace`). */
export const getCrmNotifications = (opts: { unreadOnly?: boolean; before?: string } = {}) => {
  const params = new URLSearchParams();
  if (opts.unreadOnly) params.set("unread_only", "true");
  if (opts.before) params.set("before", opts.before);
  const qs = params.toString();
  return call<CrmNotification[]>(`/notifications${qs ? `?${qs}` : ""}`);
};

export const getCrmUnreadCount = () => call<{ count: number }>("/notifications/unread-count");

export const markCrmNotificationsRead = (which: { ids: string[] } | { all: true }) =>
  call<{ updated: number }>("/notifications/read", { method: "POST", ...body(which) });

/** This workspace's job runs, with their errors; Admins only. */
export const getCrmJobs = (workspace: string, job?: CrmJobName) =>
  call<CrmJobs>(`/jobs${query(workspace, { job })}`);

/** Every workspace's job runs for the TVA admin panel: `error_public` only. */
export const getTvaCrmJobs = () => call<TvaCrmJobs>("/tva-admin/jobs");
