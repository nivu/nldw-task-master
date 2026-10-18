/**
 * Tasks' API types — spec 008 contracts/api.md §0.1, §1, §1.1, §2.
 *
 * Mirrors `backend/app/schemas/tasks.py` and the `TaskOut` the backend
 * returns, by hand, as `crm-types.ts` mirrors `schemas/crm.py`.
 */

import type { TodaySection } from "@/lib/api/crm-types";

export type TaskKind = "private" | "shared_personal" | "crm" | "project";
export type TaskStatus = "open" | "waiting" | "done" | "cancelled";
export type TaskBucket = "today" | "this_week" | "this_month" | "later";
export type TaskPriority = "urgent" | "high" | "normal" | "low";
export type TaskSource =
  | "manual"
  | "nldw"
  | "voice"
  | "email_commitment"
  | "meeting_action"
  | "rule"
  | "import";
export type TaskLinkType = "company" | "person" | "opportunity" | "campaign" | "tva_project";

export interface TaskPersonRef {
  id: string | null;
  name: string | null;
}

export interface TaskLinkOut {
  id: string;
  record_type: Exclude<TaskLinkType, "tva_project">;
  record_id: string;
  name: string | null;
}

/** Contracts §0.1 — the task as returned. */
export interface TaskOut {
  id: string;
  kind: TaskKind;
  title: string;
  notes: string | null;
  status: TaskStatus;
  status_label: string;
  bucket: TaskBucket | null;
  bucket_label: string | null;
  effective_bucket: TaskBucket | null;
  /** YYYY-MM-DD */
  due_on: string | null;
  days_overdue: number;
  priority: TaskPriority;
  priority_label: string;
  tags: string[];
  assignee: TaskPersonRef;
  created_by: TaskPersonRef;
  waiting_on: { id: string | null; label: string | null } | null;
  /** `{private: true}` when the reader may not see the parent (FR-TASK-DATA-09). */
  parent: { id: string; title: string } | { private: true } | null;
  subtask_count: number;
  workspace: { id: string; key: string; name: string } | null;
  category: { id: string; label: string | null } | null;
  links: TaskLinkOut[];
  /** FR-TASK-VIS-07: absent, name only, or id and name. */
  project: { id: string; name: string } | { name: string } | null;
  source: TaskSource;
  source_ref: string | null;
  visible_to: string;
  may: { edit: boolean; status_only: boolean; delete: boolean };
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  /** Phase 2 (FR-TASK-DEL-02): given to the reader by someone else and not
   *  yet accepted. */
  new: boolean;
  accepted_at: string | null;
  /** Phase 3 (FR-TASK-RRULE-01): an RRULE body such as
   *  `FREQ=WEEKLY;BYDAY=MO,TH`, or null. */
  recurrence: string | null;
}

/** Contracts §2 — GET /tasks/{id}/comments (FR-TASK-DEL-04). */
export interface TaskComment {
  id: string;
  author: TaskPersonRef;
  body: string;
  /** A hand-back's note is kept as a comment (FR-TASK-DEL-03). */
  kind: "comment" | "hand_back";
  created_at: string;
  may_delete: boolean;
}

/** Contracts §2 — a starter template, listed from phase 3 (FR-TASK-RRULE-05). */
export interface TaskTemplate {
  key: "invoice_follow_ups" | "pipeline_review" | "statutory_dates" | "payroll_check";
  title: string;
  /** The recurrence in words; null while the template is not available. */
  repeats: string | null;
  /** False for the statutory dates until the owner supplies them. */
  available: boolean;
}

/** Contracts §2 — GET/PATCH /tasks/settings (FR-TASK-DEL-06; phase 3 lists
 *  the templates). */
export interface TaskSettings {
  nudge_days: number;
  templates?: TaskTemplate[];
}

export interface TaskHistoryEntry {
  at: string;
  actor_name: string | null;
  action: string;
  changes: { field: string; before: unknown; after: unknown }[];
}

/** GET /tasks/{id}. */
export interface TaskDetail extends TaskOut {
  subtasks: TaskOut[];
  history: TaskHistoryEntry[];
}

/** GET /tasks — My Tasks. */
export interface MyTasks {
  /** The caller's own today, YYYY-MM-DD. */
  as_of: string;
  timezone: string;
  sections: Record<TaskBucket, TaskOut[]>;
  closed: TaskOut[];
  /** `<updated_at>,<id>`, passed back as `closed_before`; null on the last page. */
  next_closed: string | null;
}

export interface MyTasksFilters {
  tag?: string;
  priority?: TaskPriority;
  link_type?: TaskLinkType;
  link_id?: string;
  q?: string;
  closed_limit?: number;
  closed_before?: string;
}

/** Contracts §2 — GET /tasks?view=waiting (FR-TASK-VIEW-01). */
export interface WaitingTask extends TaskOut {
  /** Days since the task was created, in the reader's own day. */
  age_days: number;
}

export interface WaitingView {
  groups: { waiting_on: { id: string | null; label: string }; tasks: WaitingTask[] }[];
}

/** Contracts §2 — GET /tasks?view=assigned_by_me (FR-TASK-VIEW-02). */
export interface AssignedByMeView {
  tasks: TaskOut[];
}

/** Contracts §2 — GET /tasks/project/{project_id} (FR-TASK-VIEW-03). */
export interface ProjectTasks {
  project: { id: string; name: string };
  tasks: TaskOut[];
}

/** Contracts §2 — GET /tasks/brief (FR-TASK-BRIEF-01). */
export interface TaskBrief {
  /** The reader's own today, YYYY-MM-DD. */
  as_of: string;
  today: TaskOut[];
  overdue: TaskOut[];
  /** Waiting tasks the reader created, untouched for their nudge days. */
  nudge: (TaskOut & { idle_days: number })[];
  /** Each workspace's CRM Today sections, exactly as CRM Today gives them. */
  workspaces: { workspace: { key: string; name: string }; sections: TodaySection[] }[];
}

/** GET /tasks/trash. */
export interface TrashedTask extends TaskOut {
  deleted_at: string;
  days_left: number;
}

export interface TaskLinkIn {
  record_type: TaskLinkType;
  record_id: string;
}

/** Contracts §1.1 — POST /tasks. */
export interface TaskIn {
  title: string;
  notes?: string | null;
  due_on?: string | null;
  bucket?: TaskBucket | null;
  priority?: TaskPriority;
  tags?: string[];
  assignee_id?: string | null;
  assignee?: string | null;
  links?: TaskLinkIn[];
  parent_id?: string | null;
  status?: "open" | "waiting";
  waiting_on_id?: string | null;
  waiting_on_text?: string | null;
  category_id?: string | null;
}

/** Contracts §1.1 — PATCH /tasks/{id}: only the fields sent change. */
export interface TaskPatch {
  title?: string;
  notes?: string | null;
  status?: TaskStatus;
  due_on?: string | null;
  bucket?: TaskBucket;
  priority?: TaskPriority;
  tags?: string[];
  assignee_id?: string;
  parent_id?: string | null;
  waiting_on_id?: string | null;
  waiting_on_text?: string | null;
  category_id?: string | null;
  /** Phase 3: null ends the series (FR-TASK-RRULE-04). */
  recurrence?: string | null;
  expect_updated_at?: string;
}

/** POST /tasks/audience. */
export interface TaskAudienceIn {
  assignee_id?: string | null;
  links: TaskLinkIn[];
}

/** GET /tasks/people. */
export interface TaskPerson {
  id: string;
  display_name: string;
  email: string;
}

/** GET /tasks/linkable. */
export interface TaskLinkable {
  records: {
    workspace_key: string;
    record_type: Exclude<TaskLinkType, "tva_project">;
    record_id: string;
    name: string | null;
  }[];
  projects: { id: string; name: string; status: string }[];
}

/** POST /tasks/quick-add/preview. */
export interface QuickAddIn {
  text: string;
  answers?: { date?: string; assignee_id?: string };
}

export interface QuickAddQuestion {
  kind: "date" | "assignee";
  prompt: string;
  options: { value: string; label: string }[];
}

export interface QuickAddPreview {
  draft: {
    title: string;
    due_on: string | null;
    due_weekday: string | null;
    bucket: TaskBucket;
    tags: string[];
    assignee: { id: string; name: string };
    links: TaskLinkIn[];
  };
  visible_to: string;
  /** Non-empty: Save stays disabled (FR-TASK-ADD-02). */
  questions: QuickAddQuestion[];
}

/** POST /tasks/import/dry-run — the Life OS import (FR-TASK-IMP-01 to -04). */
export interface LifeOsCounts {
  rows: number;
  open: number;
  waiting: number;
  done: number;
  cancelled: number;
  skipped: number;
  existing: number;
  repeated: number;
}

export interface LifeOsOwner {
  value: string;
  proposed_email: string | null;
  proposed_name: string | null;
  mapped: boolean;
}

export interface LifeOsDryRun {
  run_id: string;
  counts: LifeOsCounts;
  owners: LifeOsOwner[];
  unmapped: { owners: string[]; status: string[]; priority: string[] };
  owners_without_tasks: string[];
  ambiguous_dates: { row: number; value: string; read_as: string }[];
  waiting_as_life_os: number;
  /** Non-empty: the real run is refused. */
  blocking: string[];
}

/** POST /tasks/import/{run_id}/apply. */
export interface LifeOsApplied {
  run_id: string;
  counts: { created: number; existing: number; repeated: number; skipped: number };
}
