/**
 * Tasks' fixed words — spec 008 FR-TASK-CHG-11, FR-TASK-LIST-01, -03,
 * NFR-TASK-05, -06.
 *
 * The labels of a task with no workspace, as `backend/app/domain/tasks/
 * labels.py` fixes them; a task the API returns carries its own labels, so
 * these name a section, a filter or a choice before any task is loaded.
 * Overdue and priority are said in words, never by colour alone
 * (NFR-TASK-06).
 */

import type { TaskBucket, TaskLinkType, TaskPriority, TaskStatus } from "@/lib/api/task-types";

/** The four sections of My Tasks, in order (FR-TASK-LIST-01). */
export const SECTIONS: TaskBucket[] = ["today", "this_week", "this_month", "later"];

export const BUCKET_LABEL: Record<TaskBucket, string> = {
  today: "Today",
  this_week: "This week",
  this_month: "This month",
  later: "Later",
};

export const STATUS_LABEL: Record<TaskStatus, string> = {
  open: "Open",
  waiting: "Waiting",
  done: "Done",
  cancelled: "Cancelled",
};

/** Most urgent first. */
export const PRIORITIES: TaskPriority[] = ["urgent", "high", "normal", "low"];

export const PRIORITY_LABEL: Record<TaskPriority, string> = {
  urgent: "Urgent",
  high: "High",
  normal: "Normal",
  low: "Low",
};

export const LINK_TYPE_LABEL: Record<TaskLinkType, string> = {
  company: "Company",
  person: "Person",
  opportunity: "Deal",
  campaign: "Campaign",
  tva_project: "Project",
};

/** FR-TASK-LIST-03 — "N days overdue", in words. */
export function overdueText(days: number): string | null {
  if (days <= 0) return null;
  return days === 1 ? "1 day overdue" : `${days} days overdue`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** `Fri 23 Oct` for a `YYYY-MM-DD`, read as a calendar day (no time zone). */
export function dueText(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const weekday = WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${weekday} ${d} ${MONTHS[m - 1]}`;
}
