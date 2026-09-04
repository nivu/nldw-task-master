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

export type Category = "wfh" | "casual" | "sick";
export type Role = "user" | "lead" | "admin";
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
  capabilities: { team_view: boolean; admin_panel: boolean };
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
}

export interface PortalUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  lead_id: string | null;
  is_active: boolean;
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
  months: Record<string, Record<Category, string>>;
}

/** §6.1 — the labels and colours the calendar and roster share. */
export const CATEGORY_LABEL: Record<Category, string> = {
  wfh: "Work from home",
  casual: "Casual leave",
  sick: "Sick leave",
};

export const CATEGORY_SHORT: Record<Category, string> = {
  wfh: "WFH",
  casual: "Casual",
  sick: "Sick",
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

export interface TimesheetEntry {
  id: string;
  project_id: string;
  project_name: string;
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
  projects: { project_id: string; project_name: string; hours: string }[];
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
