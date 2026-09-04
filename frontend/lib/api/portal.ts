/**
 * Calls to the portal API.
 *
 * Every request goes through `backendFetch`, which routes to `/api/proxy/...`
 * so the browser never learns the backend's address (constitution,
 * Security-First). This module adds the one thing the proxy cannot: the
 * caller's Supabase access token.
 */

import { backendFetch, BackendError } from "@/lib/api/backend";
import { createClient } from "@/lib/supabase/client";
import type {
  Allowance,
  AppSetting,
  BackfillEntry,
  AuditEntry,
  Balance,
  Booking,
  CalendarMonth,
  Category,
  Holiday,
  Me,
  PendingApproval,
  PersonBalances,
  PortalUser,
  TeamDay,
  YearHistory,
} from "@/lib/api/types";

export { BackendError };

async function accessToken(): Promise<string> {
  const { data } = await createClient().auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    // The session went away mid-use — usually a refresh that failed while the
    // tab was asleep. Middleware would catch this on the next navigation, but
    // a fetch can happen without one, so send them to sign in rather than
    // firing a request that is certain to 401.
    window.location.href = "/auth/login";
    throw new Error("Not signed in");
  }
  return token;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  return backendFetch<T>(`/api/v1${path}`, { ...init, token: await accessToken() });
}

function body(payload: unknown): RequestInit {
  return { body: JSON.stringify(payload) };
}

// ---------------------------------------------------------------------------
// The signed-in person
// ---------------------------------------------------------------------------

export const getMe = () => call<Me>("/me");

export const getCalendar = (period?: string) =>
  call<CalendarMonth>(`/me/calendar${period ? `?period=${period}` : ""}`);

export const getBalances = (period?: string) =>
  call<Balance[]>(`/me/balances${period ? `?period=${period}` : ""}`);

export const getHistory = (year?: string) =>
  call<YearHistory>(`/me/history${year ? `?year=${year}` : ""}`);

export const changePassword = (newPassword: string) =>
  call<{ status: string }>("/me/password", {
    method: "POST",
    ...body({ new_password: newPassword }),
  });

// ---------------------------------------------------------------------------
// Bookings
// ---------------------------------------------------------------------------

export const createBooking = (input: {
  date: string;
  category: Category;
  duration: string;
  reason?: string | null;
}) => call<Booking>("/bookings", { method: "POST", ...body(input) });

export const withdrawBooking = (id: string) =>
  call<Booking>(`/bookings/${id}`, { method: "DELETE" });

export const decideBooking = (id: string, approve: boolean, note?: string) =>
  call<Booking>(`/bookings/${id}/decision`, {
    method: "POST",
    ...body({ approve, note: note ?? null }),
  });

// ---------------------------------------------------------------------------
// Lead
// ---------------------------------------------------------------------------

export const getTeamDay = (day?: string) =>
  call<TeamDay>(`/team${day ? `?day=${day}` : ""}`);

export const getApprovals = () => call<PendingApproval[]>("/team/approvals");

export const getTeamConsumption = (period?: string) =>
  call<{ period: string; people: PersonBalances[] }>(
    `/team/consumption${period ? `?period=${period}` : ""}`
  );

export const flagUnrecognised = (input: {
  user_id: string;
  date: string;
  note?: string;
}) => call<{ id: string }>("/team/unrecognised", { method: "POST", ...body(input) });

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export const listUsers = () => call<PortalUser[]>("/admin/users");

export const createUser = (input: {
  email: string;
  password: string;
  display_name: string;
  role: string;
  lead_id: string | null;
}) => call<PortalUser>("/admin/users", { method: "POST", ...body(input) });

export const updateUser = (
  id: string,
  changes: Partial<{ display_name: string; role: string; lead_id: string | null; is_active: boolean }>
) => call<PortalUser>(`/admin/users/${id}`, { method: "PATCH", ...body(changes) });

export const listAllowances = () => call<Allowance[]>("/admin/allowances");

export const setAllowance = (input: {
  period: string;
  category: Category;
  days: string;
  user_id: string | null;
}) => call<Allowance>("/admin/allowances", { method: "PUT", ...body(input) });

export const listHolidays = () => call<Holiday[]>("/admin/holidays");

export const declareHoliday = (input: { date: string; name: string }) =>
  call<Holiday>("/admin/holidays", { method: "POST", ...body(input) });

export const deleteHoliday = (id: string) =>
  call<{ status: string }>(`/admin/holidays/${id}`, { method: "DELETE" });

export const getOrgConsumption = (period?: string) =>
  call<{ period: string; people: PersonBalances[] }>(
    `/admin/consumption${period ? `?period=${period}` : ""}`
  );

// Spec A-21 — the sanctioned override of the lock in §6.3. Admin only.
export const listBackfills = () => call<BackfillEntry[]>("/admin/backfill");

export const backfillLeave = (input: {
  user_id: string;
  date: string;
  category: Category;
  duration: string;
  reason: string | null;
  note: string;
}) => call<{ id: string }>("/admin/backfill", { method: "POST", ...body(input) });

export const undoBackfill = (id: string) =>
  call<{ id: string; status: string }>(`/admin/backfill/${id}`, { method: "DELETE" });

export const listSettings = () => call<AppSetting[]>("/admin/settings");

export const updateSetting = (key: string, value: unknown) =>
  call<AppSetting>(`/admin/settings/${key}`, { method: "PUT", ...body({ value }) });

export const listAudit = (limit = 100) => call<AuditEntry[]>(`/admin/audit?limit=${limit}`);

export const runLockSweep = () =>
  call<{ approved: number; considered: number }>("/admin/lock-sweep", { method: "POST" });

// ---------------------------------------------------------------------------

/**
 * The message to show a person when a call fails.
 *
 * A-17: the backend's `detail` is written for the requester — "Casual leave
 * must be requested before the day itself" — so it is rendered verbatim.
 * Anything else gets a sentence that does not pretend to explain.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof BackendError && error.detail) return error.detail;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Please try again.";
}
