"use client";

import { useDraggable } from "@dnd-kit/core";
import { GripVertical } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { TaskBucket, TaskOut } from "@/lib/api/task-types";
import { BUCKET_LABEL, dueText, overdueText, SECTIONS } from "@/lib/tasks/labels";
import { cn } from "@/lib/utils";

/**
 * One task in My Tasks — spec 008 FR-TASK-LIST-01 to -05, NFR-TASK-06.
 *
 * Done is one tap (the box); Undo puts a closed task back to Open. Moving a
 * task to another section is one drag (the handle) or one tap (the section
 * menu), and changes its bucket only (FR-TASK-LIST-04). Overdue and
 * priority are said in words, never by colour alone. A task someone else
 * gave the reader shows "New" until they act on it (FR-TASK-DEL-02).
 */

/** A native select with the visible focus ring every control here has. */
export const SELECT_CLASS =
  "h-8 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50";

/** A plain button or checkbox's visible focus ring (NFR-TASK-06). */
export const FOCUS_CLASS =
  "outline-none focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:rounded-sm";

export function TaskRow({
  task,
  onOpen,
  onComplete,
  onReopen,
  onMove,
}: {
  task: TaskOut;
  onOpen: (task: TaskOut) => void;
  onComplete?: (task: TaskOut) => void;
  onReopen?: (task: TaskOut) => void;
  /** Absent for a closed task, which sits in no section. */
  onMove?: (task: TaskOut, bucket: TaskBucket) => void;
}) {
  const closed = task.status === "done" || task.status === "cancelled";
  const canChange = task.may.edit || task.may.status_only;
  const movable = Boolean(onMove) && task.may.edit && !closed;
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: task.id,
    disabled: !movable,
  });
  const overdue = overdueText(task.days_overdue);

  return (
    <li
      ref={setNodeRef}
      data-testid="task-row"
      style={transform ? { transform: `translate(${transform.x}px, ${transform.y}px)` } : undefined}
      className={cn(
        "flex items-start gap-2 rounded-md border bg-background px-2 py-2",
        isDragging && "z-10 shadow-lg"
      )}
    >
      {movable && (
        <button
          type="button"
          aria-label={`Drag ${task.title} to another section`}
          className={cn("mt-1 cursor-grab touch-none text-muted-foreground", FOCUS_CLASS)}
          {...listeners}
          {...attributes}
        >
          <GripVertical className="size-4" />
        </button>
      )}
      {!closed && onComplete && (
        <input
          type="checkbox"
          className={cn("mt-1.5 h-4 w-4 shrink-0", FOCUS_CLASS)}
          aria-label={`Mark ${task.title} done`}
          checked={false}
          disabled={!canChange}
          onChange={() => onComplete(task)}
        />
      )}
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => onOpen(task)}
          className={cn(
            "block max-w-full truncate text-left text-sm font-medium hover:underline",
            closed && "text-muted-foreground line-through",
            FOCUS_CLASS
          )}
        >
          {task.title}
        </button>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {task.new && (
            <Badge variant="secondary" className="h-5 px-1.5 text-[11px]">
              New
            </Badge>
          )}
          {closed && <span>{task.status_label}</span>}
          {task.status === "waiting" && (
            <span>Waiting{task.waiting_on?.label ? ` on ${task.waiting_on.label}` : ""}</span>
          )}
          {task.due_on && <span>Due {dueText(task.due_on)}</span>}
          {overdue && <span className="font-medium text-destructive">{overdue}</span>}
          {task.priority !== "normal" && (
            <span className={cn(task.priority === "urgent" && "font-medium text-foreground")}>
              {task.priority_label} priority
            </span>
          )}
          {task.workspace && <span>{task.workspace.name}</span>}
          {task.links.length > 0 && (
            <span className="truncate">{task.links.map((l) => l.name ?? "A record").join(", ")}</span>
          )}
          {task.project && <span>Project {task.project.name}</span>}
          {task.tags.map((tag) => (
            <Badge key={tag} variant="outline" className="h-5 px-1.5 text-[11px]">
              #{tag}
            </Badge>
          ))}
        </div>
      </div>
      {closed && onReopen && canChange && (
        <button
          type="button"
          className={cn("text-xs text-muted-foreground underline-offset-2 hover:underline", FOCUS_CLASS)}
          onClick={() => onReopen(task)}
        >
          Undo
        </button>
      )}
      {movable && onMove && (
        <select
          aria-label={`Section for ${task.title}`}
          className={cn(SELECT_CLASS, "h-7 text-xs")}
          value={task.effective_bucket ?? task.bucket ?? "later"}
          onChange={(e) => onMove(task, e.target.value as TaskBucket)}
        >
          {SECTIONS.map((s) => (
            <option key={s} value={s}>
              {BUCKET_LABEL[s]}
            </option>
          ))}
        </select>
      )}
    </li>
  );
}
