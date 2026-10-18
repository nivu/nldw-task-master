"use client";

import { useState } from "react";

import { PersonPicker } from "@/components/tasks/person-picker";
import { TaskPanel } from "@/components/tasks/task-panel";
import { FOCUS_CLASS } from "@/components/tasks/task-row";
import { VisibleTo } from "@/components/tasks/visible-to";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type {
  ProjectTasks as ProjectTasksData,
  TaskOut,
  TaskPerson,
} from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { createTask, getProjectTasks } from "@/lib/api/tasks";
import { dueText, overdueText } from "@/lib/tasks/labels";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * A TVA project's task panel — spec 008 FR-TASK-VIEW-03, FR-TASK-ADD-05,
 * FR-TASK-VIS-08; contracts §2 `GET /tasks/project/{project_id}`.
 *
 * The project's tasks the reader may see (the backend decides, §3.1), each
 * opening the shared task panel, and "Add task", which creates a task
 * already linked to this project and says who will see it before it is
 * saved. Shown on the project page, which only leads, managers and admins
 * open (unchanged); phase 2 of tasks must be on for the reader.
 */
export function ProjectTasks({
  projectId,
  meId = null,
}: {
  projectId: string;
  meId?: string | null;
}) {
  const [showClosed, setShowClosed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState<TaskPerson | null>(null);
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const {
    data,
    error: loadError,
    reload,
  } = useAsync<ProjectTasksData>(
    () => getProjectTasks(projectId, showClosed),
    [projectId, showClosed],
  );
  const link = { record_type: "tva_project" as const, record_id: projectId };

  async function add() {
    if (!title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createTask({
        title: title.trim(),
        links: [link],
        assignee_id: assignee?.id ?? null,
      });
      setTitle("");
      setAssignee(null);
      setAdding(false);
      reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Tasks</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              className={cn("h-4 w-4", FOCUS_CLASS)}
              checked={showClosed}
              onChange={(e) => setShowClosed(e.target.checked)}
            />
            Show done and cancelled
          </label>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setAdding((a) => !a)}
          >
            Add task
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {adding && (
          <div className="space-y-2 rounded-md border p-3">
            <Input
              aria-label="Task title"
              placeholder="What needs doing?"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") add();
              }}
            />
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">
                Assigned to {assignee ? assignee.display_name : "you"}
              </span>
              <button
                type="button"
                className={cn(
                  "underline-offset-2 hover:underline",
                  FOCUS_CLASS,
                )}
                onClick={() => setPicking((p) => !p)}
              >
                Change
              </button>
            </div>
            {picking && (
              <PersonPicker
                label="Assign to"
                projectId={projectId}
                onPick={(person) => {
                  setAssignee(person);
                  setPicking(false);
                }}
              />
            )}
            <VisibleTo
              draft={{ assignee_id: assignee?.id ?? null, links: [link] }}
            />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={add}
                disabled={saving || !title.trim()}
              >
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setAdding(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
        {loadError && (
          <p role="alert" className="text-sm text-destructive">
            {loadError}
          </p>
        )}
        {data && data.tasks.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No tasks on this project.
          </p>
        )}
        {data && data.tasks.length > 0 && (
          <ul className="space-y-1.5">
            {data.tasks.map((task: TaskOut) => {
              const closed =
                task.status === "done" || task.status === "cancelled";
              const overdue = overdueText(task.days_overdue);
              return (
                <li
                  key={task.id}
                  data-testid="task-row"
                  className="rounded-md border px-2 py-2"
                >
                  <button
                    type="button"
                    onClick={() => setOpen(task.id)}
                    className={cn(
                      "block max-w-full truncate text-left text-sm font-medium hover:underline",
                      closed && "text-muted-foreground line-through",
                      FOCUS_CLASS,
                    )}
                  >
                    {task.title}
                  </button>
                  <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                    <span>{task.assignee.name ?? "—"}</span>
                    <span>{task.status_label}</span>
                    {task.due_on && <span>Due {dueText(task.due_on)}</span>}
                    {overdue && (
                      <span className="font-medium text-destructive">
                        {overdue}
                      </span>
                    )}
                    {task.priority !== "normal" && (
                      <span>{task.priority_label} priority</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      <TaskPanel
        taskId={open}
        onOpenChange={(o) => !o && setOpen(null)}
        onChanged={() => reload()}
        onOpenTask={setOpen}
        meId={meId}
        delegation
      />
    </Card>
  );
}
