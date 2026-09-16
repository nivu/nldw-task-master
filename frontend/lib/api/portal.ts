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
  AllocationRow,
  Coverage,
  CurrentWork,
  Forecast,
  Phase,
  Project,
  ProjectEffort,
  ProjectPhase,
  TimesheetDay,
  TimesheetWeek,
  Activity,
  AllocatablePerson,
  PeopleFinancials,
  ProjectFinancials,
  ResourcesTimeline,
  TokenCreated,
  TokenList,
  CtcList,
  CtcPeriod,
  Pnl,
  Timeline,
  Bench,
  Checklists,
  CompoffClaim,
  Hiring,
  Location,
  MilestoneList,
  Milestone,
  MyCompoff,
  ProjectHealth,
  Review,
  Statement,
  TeamReviews,
  TeamWeek,
  Utilisation,
  HomeSummary,
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

export const getHome = () => call<HomeSummary>("/me/home");

export const getCalendar = (period?: string) =>
  call<CalendarMonth>(`/me/calendar${period ? `?period=${period}` : ""}`);

export const getBalances = (period?: string) =>
  call<Balance[]>(`/me/balances${period ? `?period=${period}` : ""}`);

export const getHistory = (range?: { start: string; end: string }) =>
  call<YearHistory>(`/me/history${range ? `?start=${range.start}&end=${range.end}` : ""}`);

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
  display_name: string;
  role: string;
  lead_id: string | null;
}) => call<PortalUser>("/admin/users", { method: "POST", ...body(input) });

export const updateUser = (
  id: string,
  changes: Partial<{
    display_name: string;
    role: string;
    lead_id: string | null;
    is_active: boolean;
    location_id: string | null;
  }>
) => call<PortalUser>(`/admin/users/${id}`, { method: "PATCH", ...body(changes) });

export const listAllowances = () => call<Allowance[]>("/admin/allowances");

export const setAllowance = (input: {
  period: string;
  category: Category;
  days: string;
  user_id: string | null;
}) => call<Allowance>("/admin/allowances", { method: "PUT", ...body(input) });

export const listHolidays = () => call<Holiday[]>("/admin/holidays");

export const declareHoliday = (input: { date: string; name: string; location_id?: string | null }) =>
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

// ---------------------------------------------------------------------------
// Timesheets — spec 002
// ---------------------------------------------------------------------------

export const getTimesheetDay = (day?: string) =>
  call<TimesheetDay>(`/timesheet/day${day ? `?day=${day}` : ""}`);

export const saveTimesheetDay = (input: {
  date: string;
  lines: {
    project_id?: string | null;
    activity?: Activity | null;
    hours_office: string;
    hours_home: string;
    note: string | null;
  }[];
}) => call<{ date: string; entries: number; total: string }>("/timesheet/day", {
  method: "PUT",
  ...body(input),
});

export const getTimesheetWeek = (weekStart?: string) =>
  call<TimesheetWeek>(`/timesheet/week${weekStart ? `?week_start=${weekStart}` : ""}`);

export const getAnalyticsProjects = () => call<Project[]>("/analytics/projects");

export const getProjectEffort = (projectId: string) =>
  call<ProjectEffort>(`/analytics/projects/${projectId}`);

export const getCoverage = (start?: string, end?: string) =>
  call<Coverage>(
    `/analytics/coverage${start ? `?start=${start}&end=${end ?? start}` : ""}`
  );

export const getForecast = () => call<Forecast>("/analytics/forecast");

export const getCurrentWork = (days = 7) => call<CurrentWork[]>(`/analytics/current?days=${days}`);

// Projects and allocations — manager and admin (spec 003 FR-ROLE-02). The
// path still says /admin because that is where 002 put it; the guard is the
// manager tier.
export const listProjects = () => call<Project[]>("/admin/projects");

export const createProject = (input: { name: string; client: string | null; revenue?: string | null }) =>
  call<Project>("/admin/projects", { method: "POST", ...body(input) });

export const updateProject = (
  id: string,
  changes: { is_archived?: boolean; name?: string; client?: string | null; revenue?: string | null }
) => call<Project>(`/admin/projects/${id}`, { method: "PATCH", ...body(changes) });

/** Who can be allocated — name and id only, so a manager needs no /admin/users. */
export const listAllocatablePeople = () => call<AllocatablePerson[]>("/analytics/people");

// Money and resourcing — manager and admin only (spec 003 FR-FIN-07, FR-RES).
export const getProjectFinancials = (projectId: string) =>
  call<ProjectFinancials>(`/analytics/projects/${projectId}/financials`);

export const getPeopleFinancials = () => call<PeopleFinancials>("/analytics/people/financials");

export const getResources = (start?: string, end?: string) =>
  call<ResourcesTimeline>(
    `/analytics/resources${start ? `?start=${start}&end=${end ?? start}` : ""}`
  );

export const setProjectPhase = (
  projectId: string,
  input: { phase: Phase; starts_on: string; ends_on: string; budget_hours: string | null }
) => call<ProjectPhase>(`/admin/projects/${projectId}/phases`, { method: "PUT", ...body(input) });

export const listAllocations = () => call<AllocationRow[]>("/admin/allocations");

export const createAllocation = (input: {
  project_id: string;
  user_id: string;
  starts_on: string;
  ends_on: string;
  percent: string;
}) => call<{ id: string }>("/admin/allocations", { method: "POST", ...body(input) });

export const deleteAllocation = (id: string) =>
  call<{ status: string }>(`/admin/allocations/${id}`, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Personal access tokens — spec 004. Session-only routes: a token cannot
// manage tokens (FR-TOK-04).
// ---------------------------------------------------------------------------

export const listTokens = () => call<TokenList>("/me/tokens");

export const createToken = (name: string) =>
  call<TokenCreated>("/me/tokens", { method: "POST", ...body({ name }) });

export const revokeToken = (id: string) => call<ApiTokenLike>(`/me/tokens/${id}`, { method: "DELETE" });

type ApiTokenLike = { id: string; revoked_at: string | null };

// ---------------------------------------------------------------------------
// CTC, monthly profit and the timeline — spec 005
// ---------------------------------------------------------------------------

export const listCtc = (userId: string) => call<CtcList>(`/admin/users/${userId}/ctc`);

export const addCtc = (
  userId: string,
  input: { annual_ctc: string; starts_on: string; ends_on: string | null }
) => call<CtcPeriod>(`/admin/users/${userId}/ctc`, { method: "POST", ...body(input) });

export const removeCtc = (periodId: string) =>
  call<{ status: string }>(`/admin/ctc/${periodId}`, { method: "DELETE" });

export const getPnl = (start?: string, end?: string) =>
  call<Pnl>(`/analytics/pnl${start ? `?start=${start}&end=${end ?? start}` : ""}`);

export const getTimeline = (start?: string, end?: string) =>
  call<Timeline>(`/analytics/timeline${start ? `?start=${start}&end=${end ?? start}` : ""}`);

// ---------------------------------------------------------------------------
// Org operations — spec 006
// ---------------------------------------------------------------------------

export const getMyCompoff = () => call<MyCompoff>("/compoff");
export const claimCompoff = (input: { worked_on: string; days: string; note: string | null }) =>
  call<CompoffClaim>("/compoff", { method: "POST", ...body(input) });
export const getTeamCompoff = () => call<CompoffClaim[]>("/compoff/team");
export const decideCompoff = (id: string, approve: boolean, note?: string | null) =>
  call<CompoffClaim>(`/compoff/${id}/decision`, { method: "POST", ...body({ approve, note: note ?? null }) });

export const getTeamWeeks = (weekStart?: string) =>
  call<TeamWeek>(`/team/timesheets${weekStart ? `?week_start=${weekStart}` : ""}`);
export const confirmWeek = (userId: string, weekStart: string, note?: string | null) =>
  call<{ status: string }>(`/team/timesheets/${userId}/${weekStart}/confirm`, { method: "POST", ...body({ note: note ?? null }) });
export const reopenWeek = (userId: string, weekStart: string) =>
  call<{ status: string }>(`/team/timesheets/${userId}/${weekStart}/confirm`, { method: "DELETE" });

export const getMyReview = (quarter?: string) => call<Review>(`/me/review${quarter ? `?quarter=${quarter}` : ""}`);
export const saveMyReview = (input: { quarter: string; summary: string; submit: boolean }) =>
  call<Review>("/me/review", { method: "PUT", ...body(input) });
export const getTeamReviews = (quarter?: string) =>
  call<TeamReviews>(`/team/reviews${quarter ? `?quarter=${quarter}` : ""}`);
export const closeReview = (userId: string, quarter: string) =>
  call<{ status: string }>(`/team/reviews/${userId}/${quarter}/close`, { method: "POST" });

export const getMyFeed = () => call<{ url: string }>("/me/feed");
export const rotateMyFeed = () => call<{ url: string }>("/me/feed/rotate", { method: "POST" });

export const getStatement = (projectId: string, period: string) =>
  call<Statement>(`/analytics/projects/${projectId}/statement?period=${period}`);
export const statementCsvPath = (projectId: string, period: string) =>
  `/api/v1/analytics/projects/${projectId}/statement?period=${period}&format=csv`;
export const getUtilisation = (start?: string, end?: string) =>
  call<Utilisation>(`/analytics/utilisation${start ? `?start=${start}&end=${end ?? start}` : ""}`);
export const getBench = (weeks = 8) => call<Bench>(`/analytics/bench?weeks=${weeks}`);
export const getHiring = (months = 6, annualCtc = "1200000", start?: string) =>
  call<Hiring>(`/analytics/hiring?months=${months}&annual_ctc=${annualCtc}${start ? `&start=${start}` : ""}`);
export const getProjectHealth = (projectId: string) => call<ProjectHealth>(`/analytics/projects/${projectId}/health`);
export const getProjectsHealth = () =>
  call<{ project_id: string; project_name: string; overall: "green" | "amber" | "red" }[]>("/analytics/health");

export const listLocations = () => call<Location[]>("/admin/locations");
export const addLocation = (name: string) => call<Location>("/admin/locations", { method: "POST", ...body({ name }) });

export const listMilestones = (projectId: string) => call<MilestoneList>(`/admin/projects/${projectId}/milestones`);
export const addMilestone = (projectId: string, input: { name: string; due_on: string; amount: string }) =>
  call<Milestone>(`/admin/projects/${projectId}/milestones`, { method: "POST", ...body(input) });
export const updateMilestone = (id: string, changes: { invoiced_on?: string; clear_invoiced?: boolean; name?: string; due_on?: string; amount?: string }) =>
  call<Milestone>(`/admin/milestones/${id}`, { method: "PATCH", ...body(changes) });
export const removeMilestone = (id: string) => call<{ status: string }>(`/admin/milestones/${id}`, { method: "DELETE" });

export const getChecklists = () => call<Checklists>("/admin/checklists");
export const setChecklistTemplate = (kind: "onboarding" | "offboarding", labels: string[]) =>
  call<{ labels: string[] }>(`/admin/checklists/templates/${kind}`, { method: "PUT", ...body({ labels }) });
export const startChecklist = (input: { user_id: string; kind: "onboarding" | "offboarding" }) =>
  call<{ id: string }>("/admin/checklists", { method: "POST", ...body(input) });
export const updateChecklistItem = (id: string, changes: { done?: boolean; owner_id?: string | null; due_on?: string | null }) =>
  call<{ id: string }>(`/admin/checklist-items/${id}`, { method: "PATCH", ...body(changes) });
export const closeChecklist = (id: string) => call<{ status: string }>(`/admin/checklists/${id}/close`, { method: "POST" });

export const sendTestNotification = () =>
  call<{ delivered: string[]; slack_configured: boolean }>("/admin/notifications/test", { method: "POST" });
export const runNudge = (kind: "today" | "weekly_gaps" | "over_allocation" | "morning_post" | "digest") =>
  call<Record<string, unknown>>("/admin/nudges/run", { method: "POST", ...body({ kind }) });

export const getOauthTransaction = (txn: string) =>
  call<{ client_name: string | null; scopes: string[] }>(`/oauth/transaction/${txn}`);
export const approveOauth = (txn: string, approved: boolean) =>
  call<{ redirect: string }>("/oauth/approve", { method: "POST", ...body({ txn, approved }) });
