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
