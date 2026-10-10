/**
 * The contract between the browser and FastAPI.
 *
 * Constitution, Clear Boundaries: API contracts are defined in code on both
 * sides — Pydantic in `backend/app/schemas/__init__.py`, these interfaces here.
 * The two are kept in step by hand. If you change one, change the other.
 *
 * Day counts are `string`, not `number`, everywhere. §6.2 requires half-day
 * granularity and JSON's only numeric type is a float; "0.5" survives the round
 * trip exactly, while 0.5 accumulates error once enough are summed. The UI
 * formats these for display and never does arithmetic on them — the backend
 * owns the ledger.
 */

export type Category = "wfh" | "casual" | "sick" | "compoff";
/** Spec 003 FR-ROLE-01 — `manager` sits between lead and admin. */
export type Role = "user" | "lead" | "manager" | "admin";
export type BookingStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "released"
  | "unrecognised";

export interface Me {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  lead_id: string | null;
  /** A hint for which navigation to render. Never a permission — every
   *  guarded route re-checks the role server-side. */
  capabilities: {
    team_view: boolean;
    admin_panel: boolean;
    /** Spec 003 — leads, managers and admins run projects (FR-ROLE-07). */
    manage_projects: boolean;
    financials: boolean;
    /** Spec 003 FR-DASH — the owner or someone they authorised; never a role. */
    dashboard: boolean;
    /** Spec 003 FR-DASH-03 — the owner alone grants and revokes dashboard access. */
    grant_dashboard: boolean;
  };
}

export interface Balance {
  category: Category;
  period: string;
  opening: string;
  allowance: string;
  used: string;
  remaining: string;
  /** Spec 001 FR-BAL-09 — false when no allowance was ever set up for this category. */
  configured: boolean;
}

export interface DayBooking {
  id: string;
  category: Category | null;
  duration: string;
  status: BookingStatus;
  reason: string | null;
  can_edit: boolean;
  /** Spec A-21 — entered by an admin after the fact, not requested by this
   *  person. Shown on the calendar so nobody finds leave they do not remember
   *  booking with no explanation for where it came from. */
  backfilled: boolean;
}

/**
 * One cell of the month grid. Every judgement is made by the server —
 * `bookable` and `locked` arrive already decided (NFR-04), so the browser
 * never re-derives whether a day is open.
 */
export interface DayCell {
  date: string;
  is_today: boolean;
  is_weekend: boolean;
  holiday: string | null;
  locked: boolean;
  bookable: boolean;
  booking: DayBooking | null;
}

export interface CalendarMonth {
  period: string;
  today: string;
  weeks: (DayCell | null)[][];
  balances: Balance[];
}

export interface Booking {
  id: string;
  user_id: string;
  date: string;
  category: Category | null;
  duration: string;
  status: BookingStatus;
  reason?: string | null;
  decision_note: string | null;
  locked: boolean;
  can_edit: boolean;
  backfilled?: boolean;
}

/** The roster. Note there is no `reason` — Q-06 keeps it out of this view. */
export interface TeamMemberDay {
  user_id: string;
  display_name: string;
  state: "present" | BookingStatus;
  category: Category | null;
  category_label: string | null;
  duration: string | null;
  booking_id: string | null;
  backfilled: boolean;
}

export interface TeamDay {
  date: string;
  today: string;
  is_weekend: boolean;
  holiday: string | null;
  people: TeamMemberDay[];
  summary: {
    present: number;
    wfh: number;
    casual: number;
    sick: number;
    compoff: number;
    unrecognised: number;
  };
}

/** The approval queue — the one place a reason IS returned to a lead. */
export interface PendingApproval {
  id: string;
  user_id: string;
  display_name: string;
  /** FR-APPR-08 — the requester's lead, or null when it falls to an admin. */
  approver: string | null;
  date: string;
  category: Category;
  category_label: string;
  duration: string;
  reason: string | null;
  created_at: string;
}

export interface PersonBalances {
  user_id: string;
  display_name: string;
  role?: Role;
  balances: Balance[];
}

/** Spec A-21 — a record an admin entered by hand, listed for review. */
export interface BackfillEntry {
  id: string;
  user_id: string;
  display_name: string;
  date: string;
  category: Category;
  duration: string;
  status: BookingStatus;
  note: string | null;
  entered_by: string;
}

/** FR-BACK-08/10 — what POST /admin/backfill and an absence conversion return. */
export interface AdminLeaveResult {
  id: string;
  user_id: string;
  date: string;
  category: Category;
  duration: string;
  status: BookingStatus;
  backfilled_by: string;
  /** What is left of that allowance afterwards; null for comp-off. May be negative. */
  balance_after: string | null;
  /** Backfill only — true when it replaced a day marked absent. */
  replaced_absence?: boolean;
}

export interface Holiday {
  id: string;
  date: string;
  name: string;
  released_bookings?: number;
  /** Spec 006 — null applies everywhere. */
  location_id?: string | null;
  location_name?: string | null;
}

/** Spec 001 FR-HOL-08 — what a pasted list of holidays did. */
export interface HolidayBulkResult {
  created: Holiday[];
  skipped: { date: string; name: string; location_id: string | null; reason: string }[];
  released_bookings: number;
}

export interface PortalUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  lead_id: string | null;
  is_active: boolean;
  /** Spec 006 FR-LOC-01. */
  location_id?: string | null;
  /** Spec 002 FR-ANALYTICS-07 — false: never counted as missing time.
   *  Only in the admin's user list. */
  logs_time?: boolean;
  /** Spec 002 FR-ANALYTICS-08 — no time is expected before the one or after
   *  the other. YYYY-MM-DD; null = not recorded. Only in the admin's user list. */
  joined_on?: string | null;
  left_on?: string | null;
  /** Spec 005 — the CTC (cost to company) in force today, shown monthly.
   *  Only in the admin's user list. Never labelled "salary". */
  ctc_monthly_now?: string | null;
  /** Spec 003 FR-DASH — only in the admin's user list. Changed by the owner
   *  alone; `is_owner` is read-only everywhere (set by migration 022). */
  dashboard_access?: boolean;
  is_owner?: boolean;
}

export interface Allowance {
  id: string;
  period: string;
  category: Category;
  days: string;
  user_id: string | null;
}

export interface AppSetting {
  key: string;
  value: unknown;
  description: string | null;
}

export interface AuditEntry {
  id: number;
  actor_id: string | null;
  actor_label: "user" | "system";
  action: string;
  target_table: string;
  target_id: string | null;
  at: string;
}

export interface YearHistory {
  year: string;
  /** YYYY-MM — a calendar year, or a financial year from April (spec 006). */
  start: string;
  end: string;
  months: Record<string, Record<Category, string>>;
}

/** §6.1 — the labels and colours the calendar and roster share. */
export const CATEGORY_LABEL: Record<Category, string> = {
  wfh: "Work from home",
  casual: "Casual leave",
  sick: "Sick leave",
  /** Spec 006 — draws on earned credits, not a monthly allowance. */
  compoff: "Comp-off",
};

export const CATEGORY_SHORT: Record<Category, string> = {
  wfh: "WFH",
  casual: "Casual",
  sick: "Sick",
  compoff: "Comp-off",
};

// ---------------------------------------------------------------------------
// Timesheets — spec 002
//
// Hours are strings for the same reason day counts are (see the note at the
// top): these totals get quoted in budget conversations and JSON has only
// floats. The UI formats them and never does arithmetic on them.
// ---------------------------------------------------------------------------

export type Phase = "pre" | "delivery" | "support" | "spillover";

export const PHASE_LABEL: Record<Phase, string> = {
  pre: "Pre-project",
  delivery: "Delivery",
  support: "Post-delivery support",
  spillover: "Spill-over",
};

/** Spec 002 FR-PROJ-06 — what kind of work a project is, in report order. */
export type ProjectCategory = "client" | "poc" | "product" | "internal";

export const PROJECT_CATEGORIES: ProjectCategory[] = ["client", "poc", "product", "internal"];

export const PROJECT_CATEGORY_LABEL: Record<ProjectCategory, string> = {
  client: "Paid client engagement",
  poc: "Client POC / general",
  product: "Nunnari product development",
  internal: "Internal tools / applications / website",
};

/** Spec 002 FR-PROJ-08 — `tentative` is pipeline: planned, not yet won. */
export type ProjectStatus = "confirmed" | "tentative";

/** Spec 003 FR-ACT-02 — the fixed set of non-project activities. */
export type Activity = "learning" | "internal" | "admin" | "other";

export interface TimesheetEntry {
  id: string;
  /** Exactly one of `project_id` / `activity` is set (FR-ACT-01). */
  project_id: string | null;
  project_name: string | null;
  activity: Activity | null;
  activity_name: string | null;
  phase_id: string | null;
  hours_office: string;
  hours_home: string;
  total: string;
  note: string | null;
}

export interface LoggableProject {
  id: string;
  name: string;
  client: string | null;
  /** False when the person is logging against a project they are not
   *  allocated to — allowed (Q-07), and shown as unallocated in analytics. */
  allocated: boolean;
}

export interface TimesheetDay {
  date: string;
  today: string;
  locked: boolean;
  locks_on: string;
  can_log: boolean;
  refusal: string | null;
  /** Q-03 — a warning, never a refusal. */
  leave_warning: string | null;
  max_hours: string;
  entries: TimesheetEntry[];
  projects: LoggableProject[];
  /** FR-TIME-12 — offered alongside projects, always. */
  activities: { id: Activity; name: string }[];
  total: string;
}

export interface TimesheetWeekDay {
  date: string;
  is_today: boolean;
  locked: boolean;
  holiday: boolean;
  /** Casual, sick or comp-off leave only. */
  on_leave: string | null;
  /** Work from home is a working day, not leave. */
  wfh: string | null;
  booked: { category: Category; label: string; duration: string } | null;
  entries: TimesheetEntry[];
  total: string;
}

export interface TimesheetWeek {
  week_start: string;
  days: TimesheetWeekDay[];
  total: string;
}

export interface ProjectPhase {
  id: string;
  phase: Phase;
  label?: string;
  starts_on: string;
  ends_on: string;
  budget_hours: string | null;
  logged_hours?: string;
  hours_office?: string;
  hours_home?: string;
  over_by?: string | null;
  people?: { user_id: string; display_name: string; hours: string }[];
}

export interface Project {
  id: string;
  name: string;
  client: string | null;
  is_archived: boolean;
  category?: ProjectCategory;
  /** Spec 002 FR-PROJ-07 — who leads it; a label, not a permission. */
  lead_id?: string | null;
  lead_name?: string | null;
  status?: ProjectStatus;
  /** 0–100, the chance a tentative project is won. Not money. */
  probability?: number | null;
  phases?: ProjectPhase[];
  logged_hours?: string;
  /** Spec 003 FR-FIN-02 — present only for managers and admins. */
  revenue?: string | null;
}

export interface ProjectEffort {
  project: Project;
  phases: ProjectPhase[];
  outside_any_phase: {
    logged_hours: string;
    hours_office: string;
    hours_home: string;
    people: { user_id: string; display_name: string; hours: string }[];
  };
  total: {
    budget_hours: string | null;
    logged_hours: string;
    hours_office: string;
    hours_home: string;
  };
}

/** FR-ANALYTICS-05 — the number every other number depends on. */
export interface Coverage {
  start: string;
  end: string;
  expected_days: number;
  logged_days: number;
  coverage: string | null;
  people: {
    user_id: string;
    display_name: string;
    expected_days: number;
    logged_days: number;
    missing_days: string[];
  }[];
}

export interface Forecast {
  start: string;
  end: string;
  projects: {
    project_id: string;
    project_name: string;
    tentative: boolean;
    capacity_hours: string;
    people: { user_id: string; display_name: string; percent: string; hours: string }[];
  }[];
  /** Confirmed allocations only (FR-PROJ-08). */
  over_allocated: OverAllocation[];
  /** The same with tentative allocations added. */
  over_with_tentative: OverAllocation[];
}

export interface OverAllocation {
  user_id: string;
  display_name: string;
  days: number;
  first: string;
  last: string;
  peak_percent: string;
}

export interface CurrentWork {
  user_id: string;
  display_name: string;
  projects: { project_id: string | null; project_name: string; hours: string }[];
  total: string;
  latest_note: string | null;
}

/** Spec 002 FR-ANALYTICS-09 — hours per project category per month. No money. */
export interface CategoryHoursCell {
  logged_hours: string;
  /** Null on the "activity" row: nobody is allocated to time on no project. */
  planned_hours: string | null;
}

export interface CategoryEffort {
  start: string;
  end: string;
  months: { period: string; basis: "actual" | "planned" }[];
  categories: { category: ProjectCategory | "activity"; label: string; cells: CategoryHoursCell[] }[];
  totals: CategoryHoursCell[];
}

export interface AllocationRow {
  id: string;
  project_id: string;
  project_name: string;
  user_id: string;
  display_name: string;
  starts_on: string;
  ends_on: string;
  percent: string;
}

// ---------------------------------------------------------------------------
// Management — spec 003. Manager and admin only.
//
// Money is a string for the same reason hours are. Every figure that could be
// incomplete says so (`complete`) and names who is unrated (FR-FIN-06): a
// margin that quietly omits somebody's cost is a better number than the truth.
// ---------------------------------------------------------------------------

export interface AllocatablePerson {
  id: string;
  display_name: string;
}

export interface ProjectFinancials {
  project_id: string;
  currency: string;
  revenue: string | null;
  total_hours: string;
  /** Null when anybody on the project is unrated — see `cogs_partial`. */
  cogs: string | null;
  /** What the rated people cost: a true lower bound, never the total. */
  cogs_partial: string | null;
  margin: string | null;
  margin_pct: string | null;
  complete: boolean;
  unrated: { user_id: string; display_name: string }[];
  /** Sorted by name, never by money (spec 003 §9). */
  people: {
    user_id: string;
    display_name: string;
    hours: string;
    cost_rate: string | null;
    cogs: string | null;
    attributed_revenue: string | null;
  }[];
}

export interface PeopleFinancials {
  currency: string;
  people: {
    user_id: string;
    display_name: string;
    current_monthly_ctc: string | null;
    hours: string;
    cogs: string | null;
    attributed_revenue: string | null;
    projects: number;
    complete: boolean;
  }[];
  unrated: { user_id: string; display_name: string }[];
}

export interface ResourceWeek {
  week_start: string;
  allocated_pct: string;
  over: boolean;
  with_tentative_pct: string;
  over_with_tentative: boolean;
  leave_days: string;
  working_days: number;
  projects: { project_id: string; project_name: string; percent: string; tentative: boolean }[];
}

export interface ResourcesTimeline {
  start: string;
  end: string;
  weeks: string[];
  people: { user_id: string; display_name: string; role: Role; weeks: ResourceWeek[] }[];
}

// ---------------------------------------------------------------------------
// Personal access tokens — spec 004. Issued from the Account page so an MCP
// client (Claude) can act as this person. The plaintext appears exactly once,
// in the response that created it.
// ---------------------------------------------------------------------------

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  /** Spec 006 — set when an OAuth client (claude.ai) holds the token. */
  client_id?: string | null;
  active: boolean;
}

export interface TokenList {
  /** Where to point an MCP client. Null when MCP is not enabled here. */
  mcp_url: string | null;
  tokens: ApiToken[];
}

export interface TokenCreated extends ApiToken {
  token: string;
}

// ---------------------------------------------------------------------------
// CTC, monthly profit and the timeline — spec 005. Manager and admin only.
// ---------------------------------------------------------------------------

export interface CtcPeriod {
  id: string;
  user_id: string;
  annual_ctc: string;
  monthly_ctc: string;
  starts_on: string;
  /** Null = until further notice. */
  ends_on: string | null;
  /** Spec 005 FR-CTC-06 — the hours a week this CTC pays for; "40.0" = full time. */
  hours_per_week: string;
  created_at: string | null;
}

export interface CtcList {
  periods: CtcPeriod[];
  current: CtcPeriod | null;
}

export interface PnlCell {
  revenue: string;
  cost: string | null;
  profit: string | null;
  profit_pct: string | null;
  basis: "actual" | "planned";
  complete: boolean;
}

export interface Pnl {
  currency: string;
  start: string;
  end: string;
  today: string;
  months: {
    period: string;
    first: string;
    last: string;
    working_days: number;
    basis: "actual" | "planned";
  }[];
  people: { user_id: string; display_name: string; cells: (PnlCell & { unrated_days: number })[] }[];
  projects: {
    project_id: string;
    project_name: string;
    is_archived: boolean;
    category: ProjectCategory;
    has_timeline: boolean;
    revenue: string | null;
    cells: (PnlCell & { unattributed: boolean; no_timeline: boolean })[];
  }[];
  /** Spec 005 FR-PNL-04 — totals per project category, in fixed order. */
  categories: { category: ProjectCategory; label: string; cells: PnlCell[] }[];
  totals: (PnlCell & { unattributed: string })[];
  unrated: { user_id: string; display_name: string }[];
  /** Spec 005 FR-PNL-05 — tentative projects, in none of the figures above. */
  pipeline: {
    label: string;
    projects: {
      project_id: string;
      project_name: string;
      category: ProjectCategory;
      probability: number | null;
      has_timeline: boolean;
      revenue: string | null;
      cells: (PnlCell & { unattributed: boolean; no_timeline: boolean })[];
    }[];
    /** Null weighted_revenue: a project in the month has no probability. */
    totals: (PnlCell & { weighted_revenue: string | null })[];
    unrated: { user_id: string; display_name: string }[];
  };
}

export interface TimelineBar {
  id: string;
  project_id: string;
  project_name: string;
  colour: number;
  starts_on: string;
  ends_on: string;
  percent: string;
  tentative: boolean;
}

export interface Timeline {
  start: string;
  end: string;
  projects: { project_id: string; name: string; colour: number; tentative: boolean }[];
  people: {
    user_id: string;
    display_name: string;
    allocations: TimelineBar[];
    /** Confirmed allocations only; the *_with_tentative pair adds the pipeline. */
    peak_percent: string;
    over: boolean;
    peak_with_tentative: string;
    over_with_tentative: boolean;
  }[];
}

// ---------------------------------------------------------------------------
// Org operations — spec 006
// ---------------------------------------------------------------------------

export interface Location {
  id: string;
  name: string;
  is_default: boolean;
}

export interface CompoffClaim {
  id: string;
  user_id: string;
  display_name: string;
  worked_on: string;
  days: string;
  note: string | null;
  status: "pending" | "approved" | "rejected" | "used" | "lapsed";
  expires_on: string | null;
  decision_note: string | null;
  decided_at: string | null;
}

export interface MyCompoff {
  available: string;
  valid_days: number;
  claims: CompoffClaim[];
}

export interface TeamWeek {
  week_start: string;
  people: {
    user_id: string;
    display_name: string;
    total: string;
    days: {
      date: string;
      total: string;
      holiday: boolean;
      on_leave: string | null;
      wfh: string | null;
      booked: { category: Category; label: string; duration: string } | null;
    }[];
    missing_days: string[];
    confirmation: { status: "confirmed" | "auto"; confirmed_at: string; note: string | null } | null;
  }[];
}

export interface Review {
  quarter: string;
  first: string;
  last: string;
  hours: { name: string; hours: string }[];
  total_hours: string;
  notes: { name: string; months: { period: string; entries: { date: string; note: string; hours: string }[] }[] }[];
  summary: string;
  submitted_at: string | null;
  closed_at: string | null;
}

export interface TeamReviews {
  quarter: string;
  people: (Review & { user_id: string; display_name: string })[];
}

export interface Statement {
  project: { id: string; name: string; client: string | null };
  period: string;
  first: string;
  last: string;
  days: string[];
  people: {
    user_id: string;
    display_name: string;
    days: Record<string, string>;
    total: string;
    unconfirmed_weeks: string[];
    notes: { date: string; note: string }[];
  }[];
  by_phase: { phase: string; hours: string }[];
  total_hours: string;
  unconfirmed: boolean;
}

export interface Utilisation {
  start: string;
  end: string;
  target_pct: string;
  people: {
    user_id: string;
    display_name: string;
    months: {
      period: string;
      billable: string;
      internal: string;
      activity: string;
      logged: string;
      capacity: string;
      utilisation_pct: string | null;
      below_target: boolean;
    }[];
  }[];
}

export interface Bench {
  weeks: string[];
  threshold_pct: string;
  people: {
    user_id: string;
    display_name: string;
    weeks: {
      week_start: string;
      allocated_pct: string;
      with_tentative_pct: string;
      bench: boolean;
    }[];
    bench_weeks: number;
  }[];
}

export interface Hiring {
  target_pct: string;
  annual_ctc: string;
  months: {
    period: string;
    demand_hours: string;
    /** Spec 002 FR-PROJ-08 — pipeline demand, outside the shortfall. */
    tentative_demand_hours: string;
    supply_hours: string;
    shortfall_hours: string;
    fte_needed: string;
    monthly_cost_at_ctc: string;
  }[];
}

export type Rag = "green" | "amber" | "red";

export interface ProjectHealth {
  project_id: string;
  project_name: string;
  overall: Rag;
  dimensions: { key: string; colour: Rag; detail: string; inputs: Record<string, string | boolean | null> }[];
  timeline: { starts_on: string; ends_on: string } | null;
}

export interface Milestone {
  id: string;
  project_id: string;
  name: string;
  due_on: string;
  amount: string;
  invoiced_on: string | null;
  invoice_number: string | null;
  paid_on: string | null;
}

export interface MilestoneList {
  milestones: Milestone[];
  total: string;
  invoiced: string;
  revenue: string | null;
  gap: string | null;
}

/** Spec 006 FR-MILE-06 — where a milestone stands between agreed and banked. */
export type InvoiceStatus = "upcoming" | "due" | "overdue" | "invoiced" | "payment_overdue" | "paid";

export const INVOICE_STATUSES: InvoiceStatus[] = ["overdue", "due", "upcoming", "invoiced", "payment_overdue", "paid"];

export const INVOICE_STATUS_LABEL: Record<InvoiceStatus, string> = {
  upcoming: "Upcoming",
  due: "Due",
  overdue: "Overdue",
  invoiced: "Invoiced",
  payment_overdue: "Payment overdue",
  paid: "Paid",
};

export interface InvoiceRow {
  id: string;
  project_id: string;
  project_name: string;
  client: string | null;
  category: ProjectCategory | null;
  is_archived: boolean;
  name: string;
  amount: string;
  due_on: string;
  invoice_number: string | null;
  invoiced_on: string | null;
  payment_due_on: string | null;
  paid_on: string | null;
  status: InvoiceStatus;
  days_overdue: number;
}

/** Spec 006 FR-MILE-07 — every milestone, and what is owed. */
export interface Invoices {
  currency: string;
  today: string;
  payment_terms_days: number;
  totals: {
    receivable: string;
    overdue_receivable: string;
    due_next_30_days: string;
    paid_this_month: string;
  };
  invoices: InvoiceRow[];
}

export interface ChecklistItem {
  id: string;
  position: number;
  label: string;
  owner_id: string | null;
  owner_name: string | null;
  due_on: string | null;
  done_at: string | null;
  done_by: string | null;
}

export interface Checklist {
  id: string;
  user_id: string;
  display_name: string;
  kind: "onboarding" | "offboarding";
  created_at: string;
  closed_at: string | null;
  items: ChecklistItem[];
  done: number;
  total: number;
}

export interface Checklists {
  templates: { onboarding: string[]; offboarding: string[] };
  checklists: Checklist[];
}

// ---------------------------------------------------------------------------
// Home — spec 006 FR-HOME. One call, blocks gated by capability.
// ---------------------------------------------------------------------------

export interface HomeSummary {
  today: string;
  display_name: string;
  mine: {
    today: { category: Category; label: string; status: BookingStatus; duration: string } | null;
    balances: Balance[];
    compoff_available: string;
    pending_requests: number;
    upcoming: { date: string; label: string; status: BookingStatus }[];
    week: {
      week_start: string;
      total: string;
      days: { date: string; total: string; is_today: boolean; holiday: boolean; on_leave: string | null; wfh: string | null; locked: boolean }[];
    };
    logged_today: boolean;
  };
  lead?: {
    reports: number;
    approvals_waiting: number;
    compoff_claims_waiting: number;
    out_today: { display_name: string; label: string; duration: string }[];
    gaps_this_week: { display_name: string; missing: number }[];
  };
  manager?: {
    health: { project_id: string; project_name: string; overall: Rag }[];
    over_allocated: { display_name: string; peak_percent: string }[];
    bench: string[];
    unrated: string[];
  };
  admin?: {
    open_checklists: { display_name: string; kind: string; done: number; total: number }[];
    slack_configured: boolean;
    slack_out_channel: string;
  };
}

/** Spec 003 FR-DASH — the CEO dashboard, owner-authorised only. Every figure
 *  is the one its detail page shows; complete=false means a CTC or a timeline
 *  is missing behind it. */
export interface DashboardCoverage {
  start: string;
  end: string;
  expected_days: number;
  logged_days: number;
  coverage: string | null;
}

export interface DashboardAlert {
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  link: string;
}

export interface DashboardSummary {
  today: {
    date: string;
    is_weekend: boolean;
    holiday: string | null;
    status: {
      people: number;
      present: number;
      wfh: number;
      leave: Partial<Record<Category, number>>;
      unrecognised: number;
    };
    pending_approvals: number;
    pending_compoff_claims: number;
  };
  data_health: {
    last_working_day: DashboardCoverage;
    week_to_date: DashboardCoverage;
    month_to_date: DashboardCoverage;
    missing_last_working_day: { user_id: string; display_name: string }[];
    portal_start_date: string | null;
  };
  money: {
    currency: string;
    /** FR-DASH-12 — false below manager: no categories, no per-project money. */
    breakdown: boolean;
    this_month: PnlCell & { period: string; unattributed: string };
    last_month: PnlCell & { period: string; unattributed: string };
    categories: (PnlCell & { category: ProjectCategory; label: string })[];
    pipeline: PnlCell & { label: string; period: string; weighted_revenue: string | null };
    incomplete: {
      unrated: { display_name: string }[];
      no_timeline: { project_id: string; project_name: string }[];
    };
    unattributed: { project_id: string; project_name: string }[];
  };
  cash: {
    currency: string;
    payment_terms_days: number;
    totals: { receivable: string; overdue_receivable: string; due_next_30_days: string; paid_this_month: string };
    overdue_count: number;
    uninvoiced_overdue_count: number;
  };
  delivery: {
    counts: Record<Rag, number>;
    red: { project_id: string; project_name: string; reasons: string[] }[];
    amber: { project_id: string; project_name: string; reasons: string[] }[];
    spillover: {
      project_id: string;
      project_name: string;
      starts_on: string;
      ends_on: string;
      cost: string | null;
      revenue: string | null;
      complete: boolean;
    }[];
    burn_over_80: { project_id: string; project_name: string; burn_pct: string; logged_hours: string; budget_hours: string }[];
  };
  people: {
    headcount: number;
    utilisation: { period: string; target_pct: string; billable: string; capacity: string; utilisation_pct: string | null };
    unallocated: { user_id: string; display_name: string }[];
    ending: { user_id: string; display_name: string; ends_on: string }[];
    over_allocated: { display_name: string; peak_percent: string }[];
  };
  trends: {
    period: string;
    basis: "actual" | "planned";
    revenue: string;
    cost: string | null;
    profit_pct: string | null;
    complete: boolean;
    utilisation_pct: string | null;
    coverage: string | null;
  }[];
  attention: DashboardAlert[];
}
