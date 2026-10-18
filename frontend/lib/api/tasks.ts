/**
 * Calls to the task routes — spec 008 contracts/api.md §1, §2.
 *
 * Every request goes through `backendFetch` and so through `/api/proxy`, the
 * only path from the browser to the backend: My Tasks reads nothing from
 * Supabase directly (FR-TASK-LIST-08, FR-TASK-VIS-03).
 */

import { backendFetch, upload } from "@/lib/api/backend";
import type {
  LifeOsApplied,
  LifeOsDryRun,
  MyTasks,
  MyTasksFilters,
  QuickAddIn,
  QuickAddPreview,
  TaskAudienceIn,
  TaskComment,
  TaskDetail,
  TaskIn,
  TaskLinkable,
  TaskLinkIn,
  TaskOut,
  TaskPatch,
  TaskPerson,
  TaskSettings,
  TrashedTask,
} from "@/lib/api/task-types";
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
  return backendFetch<T>(`/api/v1/tasks${path}`, { ...init, token: await accessToken() });
}

function body(payload: unknown): RequestInit {
  return { body: JSON.stringify(payload) };
}

/** `?a=1&b=2` of the values that are set; "" when none are. */
function query(values: Record<string, string | number | undefined | null>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

// ---------------------------------------------------------------------------
// My Tasks and one task
// ---------------------------------------------------------------------------

export const getMyTasks = (filters: MyTasksFilters = {}) =>
  call<MyTasks>(`${query({ ...filters })}`);

export const getTask = (id: string) => call<TaskDetail>(`/${id}`);

export const createTask = (input: TaskIn) =>
  call<TaskOut>("", { method: "POST", ...body(input) });

export const updateTask = (id: string, changes: TaskPatch) =>
  call<TaskOut>(`/${id}`, { method: "PATCH", ...body(changes) });

export const completeTask = (id: string) => call<TaskOut>(`/${id}/complete`, { method: "POST" });

export const reopenTask = (id: string) => call<TaskOut>(`/${id}/reopen`, { method: "POST" });

export const deleteTask = (id: string) =>
  call<{ deleted: true }>(`/${id}`, { method: "DELETE" });

export const restoreTask = (id: string) => call<TaskOut>(`/${id}/restore`, { method: "POST" });

export const getTaskTrash = () => call<TrashedTask[]>("/trash");

// ---------------------------------------------------------------------------
// Links
// ---------------------------------------------------------------------------

export const addTaskLink = (id: string, link: TaskLinkIn) =>
  call<TaskOut>(`/${id}/links`, { method: "POST", ...body(link) });

/** `linkId` is a `links[].id`, or for the project link the `project.id`. */
export const removeTaskLink = (id: string, linkId: string) =>
  call<TaskOut>(`/${id}/links/${linkId}`, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Handing work over — phase 2 (FR-TASK-DEL-02 to -04, -06)
// ---------------------------------------------------------------------------

export const acceptTask = (id: string) => call<TaskOut>(`/${id}/accept`, { method: "POST" });

export const handBackTask = (id: string, note: string) =>
  call<TaskOut>(`/${id}/hand-back`, { method: "POST", ...body({ note }) });

export const getTaskComments = (id: string) => call<TaskComment[]>(`/${id}/comments`);

export const addTaskComment = (id: string, text: string) =>
  call<TaskComment>(`/${id}/comments`, { method: "POST", ...body({ body: text }) });

export const deleteTaskComment = (id: string, commentId: string) =>
  call<{ deleted: true }>(`/${id}/comments/${commentId}`, { method: "DELETE" });

export const getTaskSettings = () => call<TaskSettings>("/settings");

export const updateTaskSettings = (changes: Partial<TaskSettings>) =>
  call<TaskSettings>("/settings", { method: "PATCH", ...body(changes) });

// ---------------------------------------------------------------------------
// Drafts: who sees, who may be assigned, what may be linked, quick add
// ---------------------------------------------------------------------------

export const getTaskAudience = (draft: TaskAudienceIn) =>
  call<{ visible_to: string }>("/audience", { method: "POST", ...body(draft) });

export const getTaskPeople = (
  filters: { q?: string; workspace_id?: string | null; project_id?: string | null } = {}
) => call<TaskPerson[]>(`/people${query(filters)}`);

export const getTaskLinkable = (q: string) => call<TaskLinkable>(`/linkable${query({ q })}`);

export const previewQuickAdd = (input: QuickAddIn) =>
  call<QuickAddPreview>("/quick-add/preview", { method: "POST", ...body(input) });

// ---------------------------------------------------------------------------
// The Life OS import — owner only (FR-TASK-IMP-01). The CSV goes up as
// multipart through the proxy, which carries uploads up to 4 MB.
// ---------------------------------------------------------------------------

export const dryRunLifeOs = async (file: File, owners?: Record<string, string | null>) => {
  const form = new FormData();
  form.set("file", file);
  if (owners) form.set("mapping", JSON.stringify({ owners }));
  return upload<LifeOsDryRun>("/api/v1/tasks/import/dry-run", form, await accessToken());
};

export const applyLifeOs = async (runId: string, file: File) => {
  const form = new FormData();
  form.set("file", file);
  return upload<LifeOsApplied>(
    `/api/v1/tasks/import/${encodeURIComponent(runId)}/apply`,
    form,
    await accessToken()
  );
};
