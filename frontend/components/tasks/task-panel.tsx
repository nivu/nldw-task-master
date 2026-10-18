"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LinkPicker, type PickedLink } from "@/components/tasks/link-picker";
import { PersonPicker } from "@/components/tasks/person-picker";
import { FOCUS_CLASS, SELECT_CLASS } from "@/components/tasks/task-row";
import { VisibleTo } from "@/components/tasks/visible-to";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type {
  TaskBucket,
  TaskDetail,
  TaskOut,
  TaskPatch,
  TaskPerson,
  TaskPriority,
  TaskStatus,
} from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { addTaskLink, getTask, removeTaskLink, updateTask } from "@/lib/api/tasks";
import { OBJECT_PATH } from "@/lib/crm/view-fields";
import {
  BUCKET_LABEL,
  dueText,
  LINK_TYPE_LABEL,
  overdueText,
  PRIORITIES,
  PRIORITY_LABEL,
  SECTIONS,
  STATUS_LABEL,
} from "@/lib/tasks/labels";
import { cn } from "@/lib/utils";

/**
 * The task panel — spec 008 FR-TASK-LIST-06, FR-TASK-VIS-08; TP-16; plan
 * §5.6.
 *
 * Every field, its links (each opening its record or project when the
 * reader may see it), its parent and subtasks, who can see it, and its
 * history as the backend allows it (FR-TASK-VIS-05). A new assignee or link
 * is shown with who will then see the task (`POST /tasks/audience`) before
 * it is saved. What the reader may change follows `may`: everything, the
 * status only (a CRM Viewer, `007` Q-57) or nothing (TP-02).
 */

const STATUSES: TaskStatus[] = ["open", "waiting", "done", "cancelled"];

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={htmlFor} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "nothing";
  if (Array.isArray(value)) return value.join(", ") || "nothing";
  if (typeof value === "object") return "title" in value ? String((value as { title: unknown }).title) : "a private task";
  return String(value);
}

export function TaskPanel({
  taskId,
  onOpenChange,
  onChanged,
  onOpenTask,
  projectsHref,
}: {
  taskId: string | null;
  onOpenChange: (open: boolean) => void;
  onChanged: (task: TaskOut) => void;
  onOpenTask: (id: string) => void;
  /** Where a project the reader may see opens; none when they have no page. */
  projectsHref?: string;
}) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [newAssignee, setNewAssignee] = useState<TaskPerson | null>(null);
  const [pickingAssignee, setPickingAssignee] = useState(false);
  const [newLink, setNewLink] = useState<PickedLink | null>(null);
  const [pickingLink, setPickingLink] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [waitingText, setWaitingText] = useState("");

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    getTask(taskId)
      .then((out) => {
        if (cancelled) return;
        setTask(out);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
      setTask(null);
      setNewAssignee(null);
      setPickingAssignee(false);
      setNewLink(null);
      setPickingLink(false);
      setWaiting(false);
      setWaitingText("");
    };
  }, [taskId]);

  async function settle(write: () => Promise<TaskOut>) {
    if (!task) return;
    setSaving(true);
    setError(null);
    try {
      const out = await write();
      onChanged(out);
      setTask(await getTask(task.id));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const save = (changes: TaskPatch) =>
    settle(() => updateTask(task!.id, { ...changes, expect_updated_at: task!.updated_at }));

  const edit = Boolean(task?.may.edit);
  const statusOnly = Boolean(task?.may.status_only);
  const workspaceId = task?.workspace?.id ?? null;
  const projectId = task && task.project && "id" in task.project ? task.project.id : null;
  const recordHref = (type: keyof typeof OBJECT_PATH, id: string) =>
    task?.workspace ? `/crm/${task.workspace.key}/${OBJECT_PATH[type]}/${id}` : null;

  return (
    <Sheet open={taskId !== null} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{task?.title ?? "Task"}</SheetTitle>
          <SheetDescription>
            {task ? `${task.status_label}${task.workspace ? ` · ${task.workspace.name}` : ""}` : "Loading…"}
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-4 pb-6">
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {task && (
            <>
              <VisibleTo text={task.visible_to} />
              {!edit && !statusOnly && (
                <p className="text-xs text-muted-foreground">You can read this task but not change it.</p>
              )}

              <Field label="Title" htmlFor="task-title">
                <Input
                  id="task-title"
                  key={`title-${task.updated_at}`}
                  defaultValue={task.title}
                  disabled={!edit || saving}
                  onBlur={(e) => {
                    const title = e.target.value.trim();
                    if (title && title !== task.title) save({ title });
                  }}
                />
              </Field>

              <Field label="Notes" htmlFor="task-notes">
                <Textarea
                  id="task-notes"
                  key={`notes-${task.updated_at}`}
                  rows={4}
                  defaultValue={task.notes ?? ""}
                  disabled={!edit || saving}
                  onBlur={(e) => {
                    const notes = e.target.value;
                    if (notes !== (task.notes ?? "")) save({ notes: notes || null });
                  }}
                />
              </Field>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Status" htmlFor="task-status">
                  <select
                    id="task-status"
                    className={cn(SELECT_CLASS, "w-full")}
                    value={waiting ? "waiting" : task.status}
                    disabled={(!edit && !statusOnly) || saving}
                    onChange={(e) => {
                      const status = e.target.value as TaskStatus;
                      // Waiting needs who or what first.
                      if (status === "waiting") return setWaiting(true);
                      setWaiting(false);
                      save({ status });
                    }}
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {task.status === s ? task.status_label : STATUS_LABEL[s]}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Priority" htmlFor="task-priority">
                  <select
                    id="task-priority"
                    className={cn(SELECT_CLASS, "w-full")}
                    value={task.priority}
                    disabled={!edit || saving}
                    onChange={(e) => save({ priority: e.target.value as TaskPriority })}
                  >
                    {PRIORITIES.map((p) => (
                      <option key={p} value={p}>
                        {PRIORITY_LABEL[p]}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Due date" htmlFor="task-due">
                  <Input
                    id="task-due"
                    type="date"
                    key={`due-${task.updated_at}`}
                    defaultValue={task.due_on ?? ""}
                    disabled={!edit || saving}
                    onBlur={(e) => {
                      const due = e.target.value || null;
                      if (due !== task.due_on) save({ due_on: due });
                    }}
                  />
                </Field>
                <Field label="Bucket" htmlFor="task-bucket">
                  <select
                    id="task-bucket"
                    className={cn(SELECT_CLASS, "w-full")}
                    value={task.bucket ?? ""}
                    disabled={!edit || saving || task.bucket === null}
                    onChange={(e) => save({ bucket: e.target.value as TaskBucket })}
                  >
                    {task.bucket === null && <option value="">None</option>}
                    {SECTIONS.map((b) => (
                      <option key={b} value={b}>
                        {task.bucket === b && task.bucket_label ? task.bucket_label : BUCKET_LABEL[b]}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {(task.due_on || task.days_overdue > 0) && (
                <p className="text-xs text-muted-foreground">
                  {task.due_on && `Due ${dueText(task.due_on)}`}
                  {overdueText(task.days_overdue) && (
                    <span className="ml-2 font-medium text-destructive">{overdueText(task.days_overdue)}</span>
                  )}
                </p>
              )}

              {(waiting || task.status === "waiting") && (
                <Field label="Waiting on" htmlFor="task-waiting">
                  {task.status === "waiting" && !waiting ? (
                    <p className="text-sm">{task.waiting_on?.label ?? "Someone"}</p>
                  ) : (
                    <div className="space-y-2">
                      <div className="flex gap-2">
                        <Input
                          id="task-waiting"
                          placeholder="Who or what"
                          value={waitingText}
                          onChange={(e) => setWaitingText(e.target.value)}
                        />
                        <Button
                          size="sm"
                          disabled={!waitingText.trim() || saving}
                          onClick={() => {
                            setWaiting(false);
                            save({ status: "waiting", waiting_on_text: waitingText.trim() });
                          }}
                        >
                          Save
                        </Button>
                      </div>
                      {edit && (
                        <PersonPicker
                          label="Waiting on a person"
                          workspaceId={workspaceId}
                          projectId={workspaceId ? null : projectId}
                          onPick={(p) => {
                            setWaiting(false);
                            save({ status: "waiting", waiting_on_id: p.id });
                          }}
                        />
                      )}
                    </div>
                  )}
                </Field>
              )}

              <Field label="Tags" htmlFor="task-tags">
                <Input
                  id="task-tags"
                  key={`tags-${task.updated_at}`}
                  placeholder="Separate tags with commas"
                  defaultValue={task.tags.join(", ")}
                  disabled={!edit || saving}
                  onBlur={(e) => {
                    const tags = e.target.value
                      .split(",")
                      .map((t) => t.trim().replace(/^#/, ""))
                      .filter(Boolean);
                    if (tags.join(",") !== task.tags.join(",")) save({ tags });
                  }}
                />
              </Field>

              <Field label="Assignee">
                <div className="flex items-center gap-2 text-sm">
                  <span>{task.assignee.name ?? "Someone"}</span>
                  {edit && !newAssignee && (
                    <Button variant="ghost" size="xs" onClick={() => setPickingAssignee((p) => !p)}>
                      Change
                    </Button>
                  )}
                </div>
                {pickingAssignee && !newAssignee && (
                  <PersonPicker
                    label="New assignee"
                    workspaceId={workspaceId}
                    projectId={workspaceId ? null : projectId}
                    onPick={(p) => {
                      setNewAssignee(p);
                      setPickingAssignee(false);
                    }}
                  />
                )}
                {newAssignee && (
                  <div className="space-y-2 rounded-md border p-2">
                    <p className="text-sm">Give it to {newAssignee.display_name}?</p>
                    <VisibleTo
                      draft={{
                        assignee_id: newAssignee.id,
                        links: [
                          ...task.links.map((l) => ({ record_type: l.record_type, record_id: l.record_id })),
                          ...(projectId ? [{ record_type: "tva_project" as const, record_id: projectId }] : []),
                        ],
                      }}
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        disabled={saving}
                        onClick={() => {
                          const id = newAssignee.id;
                          setNewAssignee(null);
                          save({ assignee_id: id });
                        }}
                      >
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setNewAssignee(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </Field>

              <Field label="Created by">
                <p className="text-sm">{task.created_by.name ?? "Someone"}</p>
              </Field>

              {task.category && (
                <Field label="Category">
                  <p className="text-sm">{task.category.label ?? "A category"}</p>
                </Field>
              )}

              <Field label="Links">
                <ul className="space-y-1 text-sm">
                  {task.links.length === 0 && !task.project && (
                    <li className="text-xs text-muted-foreground">No links.</li>
                  )}
                  {task.links.map((link) => {
                    const href = recordHref(link.record_type, link.record_id);
                    return (
                      <li key={link.id} className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">{LINK_TYPE_LABEL[link.record_type]}</span>
                        {href ? (
                          <Link href={href} className={cn("hover:underline", FOCUS_CLASS)}>
                            {link.name ?? "A record"}
                          </Link>
                        ) : (
                          <span>{link.name ?? "A record"}</span>
                        )}
                        {edit && (
                          <Button
                            variant="ghost"
                            size="xs"
                            aria-label={`Remove the link to ${link.name ?? "this record"}`}
                            disabled={saving}
                            onClick={() => settle(() => removeTaskLink(task.id, link.id))}
                          >
                            Remove
                          </Button>
                        )}
                      </li>
                    );
                  })}
                  {task.project && (
                    <li className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Project</span>
                      {projectId && projectsHref ? (
                        <Link href={projectsHref} className={cn("hover:underline", FOCUS_CLASS)}>
                          {task.project.name}
                        </Link>
                      ) : (
                        <span>{task.project.name}</span>
                      )}
                      {edit && projectId && (
                        <Button
                          variant="ghost"
                          size="xs"
                          aria-label={`Remove the link to ${task.project.name}`}
                          disabled={saving}
                          onClick={() => settle(() => removeTaskLink(task.id, projectId))}
                        >
                          Remove
                        </Button>
                      )}
                    </li>
                  )}
                </ul>
                {edit && !newLink && (
                  <Button variant="ghost" size="xs" onClick={() => setPickingLink((p) => !p)}>
                    Add a link
                  </Button>
                )}
                {pickingLink && !newLink && (
                  <LinkPicker
                    label="Link a record or project"
                    onPick={(picked) => {
                      setNewLink(picked);
                      setPickingLink(false);
                    }}
                  />
                )}
                {newLink && (
                  <div className="space-y-2 rounded-md border p-2">
                    <p className="text-sm">Link {newLink.name}?</p>
                    <VisibleTo
                      draft={{
                        assignee_id: task.assignee.id,
                        links: [
                          ...task.links.map((l) => ({ record_type: l.record_type, record_id: l.record_id })),
                          ...(projectId ? [{ record_type: "tva_project" as const, record_id: projectId }] : []),
                          { record_type: newLink.record_type, record_id: newLink.record_id },
                        ],
                      }}
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        disabled={saving}
                        onClick={() => {
                          const { record_type, record_id } = newLink;
                          setNewLink(null);
                          settle(() => addTaskLink(task.id, { record_type, record_id }));
                        }}
                      >
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setNewLink(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </Field>

              {task.parent && (
                <Field label="Part of">
                  {"id" in task.parent ? (
                    <button
                      type="button"
                      className={cn("text-left text-sm hover:underline", FOCUS_CLASS)}
                      onClick={() => onOpenTask((task.parent as { id: string }).id)}
                    >
                      {task.parent.title}
                    </button>
                  ) : (
                    <p className="text-sm text-muted-foreground">A private task</p>
                  )}
                </Field>
              )}

              {task.subtasks.length > 0 && (
                <Field label="Subtasks">
                  <ul className="space-y-1">
                    {task.subtasks.map((sub) => (
                      <li key={sub.id} className="flex items-center gap-2 text-sm">
                        <button
                          type="button"
                          className={cn("text-left hover:underline", FOCUS_CLASS)}
                          onClick={() => onOpenTask(sub.id)}
                        >
                          {sub.title}
                        </button>
                        <span className="text-xs text-muted-foreground">{sub.status_label}</span>
                      </li>
                    ))}
                  </ul>
                </Field>
              )}

              <Field label="History">
                {task.history.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Nothing to show.</p>
                ) : (
                  <ol className="space-y-1.5 text-xs">
                    {task.history.map((entry, i) => (
                      <li key={`${entry.at}-${i}`}>
                        <span className="text-muted-foreground">
                          {new Date(entry.at).toLocaleString()} · {entry.actor_name ?? "The system"} ·{" "}
                        </span>
                        {entry.action}
                        {entry.changes.map((c) => (
                          <span key={c.field} className="block pl-3 text-muted-foreground">
                            {c.field.replace(/_/g, " ")}: {shown(c.before)} → {shown(c.after)}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ol>
                )}
              </Field>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
