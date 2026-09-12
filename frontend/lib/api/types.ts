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
    /** Spec 003 — the manager tier: runs projects, sees money. */
    manage_projects: boolean;
    financials: boolean;
  };
}

export interface Balance {
  category: Category;
  period: string;
  opening: string;
  allowance: string;
  used: string;
  remaining: string;
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
    unrecognised: number;
  };
}

/** The approval queue — the one place a reason IS returned to a lead. */
export interface PendingApproval {
  id: string;
  user_id: string;
  display_name: string;
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

export interface Holiday {
  id: string;
  date: string;
  name: string;
  released_bookings?: number;
  /** Spec 006 — null applies everywhere. */
  location_id?: string | null;
  location_name?: string | null;
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
  /** Spec 005 — the CTC (cost to company) in force today, shown monthly.
   *  Only in the admin's user list. Never labelled "salary". */
  ctc_monthly_now?: string | null;
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

export type Phase = "pre" | "delivery" | "support";

export const PHASE_LABEL: Record<Phase, string> = {
  pre: "Pre-project",
  delivery: "Delivery",
  support: "Post-delivery support",
};

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
  on_leave: string | null;
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
  phases?: ProjectPhase[];
  logged_hours?: string;
  /** Spec 003 FR-FIN-02 — present only on manager/admin routes. */
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
    capacity_hours: string;
    people: { user_id: string; display_name: string; percent: string; hours: string }[];
  }[];
  over_allocated: {
    user_id: string;
    display_name: string;
    days: number;
    first: string;
    last: string;
    peak_percent: string;
  }[];
}

export interface CurrentWork {
  user_id: string;
  display_name: string;
  projects: { project_id: string | null; project_name: string; hours: string }[];
  total: string;
  latest_note: string | null;
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
  leave_days: string;
  working_days: number;
  projects: { project_id: string; project_name: string; percent: string }[];
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
    has_timeline: boolean;
    revenue: string | null;
    cells: (PnlCell & { unattributed: boolean; no_timeline: boolean })[];
  }[];
  totals: (PnlCell & { unattributed: string })[];
  unrated: { user_id: string; display_name: string }[];
}

export interface TimelineBar {
  id: string;
  project_id: string;
  project_name: string;
  colour: number;
  starts_on: string;
  ends_on: string;
  percent: string;
}

export interface Timeline {
  start: string;
  end: string;
  projects: { project_id: string; name: string; colour: number }[];
  people: {
    user_id: string;
    display_name: string;
    allocations: TimelineBar[];
    peak_percent: string;
    over: boolean;
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
    days: { date: string; total: string; holiday: boolean; on_leave: string | null }[];
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
    weeks: { week_start: string; allocated_pct: string; bench: boolean }[];
    bench_weeks: number;
  }[];
}

export interface Hiring {
  target_pct: string;
  annual_ctc: string;
  months: {
    period: string;
    demand_hours: string;
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
}

export interface MilestoneList {
  milestones: Milestone[];
  total: string;
  invoiced: string;
  revenue: string | null;
  gap: string | null;
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
