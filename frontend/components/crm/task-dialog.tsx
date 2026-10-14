"use client";

import Link from "next/link";
import { useState } from "react";

import { FieldEditor, type Choice, type RelationSearch } from "@/components/crm/field-editor";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  completeCrmTask,
  createCrmRecord,
  searchCompanyChoices,
  searchDealChoices,
  searchPersonChoices,
  updateCrmRecord,
} from "@/lib/api/crm";
import type { CrmBootstrap, CrmLinkRef, CrmMember, CrmTask, TaskBucket } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * Tasks — spec 007 FR-CRM-TASK-01, -02; Q-57.
 *
 * A task is linked to at least one company, person or deal and assigned to a
 * member. A deal's own follow-up is its next step, not a task (FR-CRM-OPP-03).
 * Anyone may tick a task assigned to them done, a Viewer included; Admins and
 * Members may tick any.
 */

const PLURAL: Record<CrmLinkRef["record_type"], string> = {
  company: "companies",
  person: "people",
  opportunity: "opportunities",
  campaign: "campaigns",
};

type Target = "company" | "person" | "opportunity";

/** A task's or note's linked records, each opening its page. */
export function LinkRefs({ workspace, links }: { workspace: string; links: CrmLinkRef[] }) {
  if (links.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
      {links.map((link) => (
        <Link
          key={`${link.record_type}-${link.record_id}`}
          href={`/crm/${workspace}/${PLURAL[link.record_type]}/${link.record_id}`}
          className="hover:underline"
        >
          {link.name ?? link.record_type}
        </Link>
      ))}
    </span>
  );
}

/** A list of tasks, each with its tick box. */
export function TaskList({
  workspace,
  tasks,
  bootstrap,
  timeZone,
  onChange,
  showLinks = false,
}: {
  workspace: string;
  tasks: CrmTask[];
  bootstrap: CrmBootstrap;
  timeZone: CrmTimezone;
  onChange: () => void;
  showLinks?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const member = bootstrap.role !== "viewer";

  async function toggle(task: CrmTask) {
    setError(null);
    try {
      if (task.status === "done") {
        await updateCrmRecord(workspace, "tasks", task.id, { status: "todo" }, task.updated_at);
      } else {
        await completeCrmTask(workspace, task.id);
      }
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (tasks.length === 0) return <p className="text-sm text-muted-foreground">No tasks.</p>;
  return (
    <div className="space-y-2">
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <ul className="divide-y rounded-md border">
        {tasks.map((task) => {
          const mine = task.assignee_id === bootstrap.me.id;
          const overdue = task.status !== "done" && task.due_on && task.due_on < new Date().toISOString().slice(0, 10);
          return (
            <li key={task.id} className="flex items-start gap-3 p-2.5">
              <Checkbox
                className="mt-0.5"
                aria-label={task.status === "done" ? `Reopen ${task.title}` : `Mark ${task.title} done`}
                checked={task.status === "done"}
                disabled={!member && !mine}
                onCheckedChange={() => toggle(task)}
              />
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className={cn("text-sm", task.status === "done" && "text-muted-foreground line-through")}>
                  {task.title}
                </p>
                <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                  <span>{task.assignee_name ?? "—"}</span>
                  {task.due_on && (
                    <span className={cn(overdue && "text-destructive")}>Due {formatDate(task.due_on, timeZone)}</span>
                  )}
                  {task.bucket_label && <span>{task.bucket_label}</span>}
                  {task.category_label && <span>{task.category_label}</span>}
                  {task.status === "in_progress" && <span>{task.status_label}</span>}
                </p>
                {showLinks && (
                  <div className="text-xs">
                    <LinkRefs workspace={workspace} links={task.links} />
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const SEARCH: Record<Target, (workspace: string) => RelationSearch> = {
  company: (ws) => (q, after) => searchCompanyChoices(ws, q, after),
  person: (ws) => (q, after) => searchPersonChoices(ws, q, after),
  opportunity: (ws) => (q, after) => searchDealChoices(ws, q, after),
};

/** New task. On a record page it is linked to that record; on the Tasks page
 *  the person picks the record. */
export function TaskDialog({
  open,
  onOpenChange,
  workspace,
  bootstrap,
  members,
  link,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  /** The record the task is for, when it is opened from one. */
  link?: { record_type: Target; record_id: string; name: string };
  onCreated: (task: CrmTask) => void;
}) {
  const [title, setTitle] = useState("");
  const [dueOn, setDueOn] = useState("");
  const [bucket, setBucket] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [assignee, setAssignee] = useState<string | null>(bootstrap.me.id);
  const [body, setBody] = useState("");
  const [targetType, setTargetType] = useState<Target>("company");
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const buckets: Choice[] = (bootstrap.lists["task.bucket"] ?? []).map((o) => ({
    value: o.system_key ?? o.id,
    label: o.label,
  }));
  const categories: Choice[] = (bootstrap.lists["task.category"] ?? [])
    .filter((o) => o.is_active)
    .map((o) => ({ value: o.id, label: o.label }));

  function close(next: boolean) {
    if (!next) {
      setTitle("");
      setDueOn("");
      setBucket("");
      setCategory("");
      setAssignee(bootstrap.me.id);
      setBody("");
      setTarget(null);
      setError(null);
    }
    onOpenChange(next);
  }

  async function create() {
    const links = link
      ? [{ record_type: link.record_type, record_id: link.record_id }]
      : target
        ? [{ record_type: targetType, record_id: target }]
        : [];
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmTask>(
        workspace,
        "tasks",
        {
          title: title.trim(),
          links,
          assignee_id: assignee,
          ...(dueOn ? { due_on: dueOn } : {}),
          ...(bucket ? { bucket: bucket as TaskBucket } : {}),
          ...(category ? { category_id: category } : {}),
          ...(body.trim() ? { body: body.trim() } : {}),
        },
        false
      );
      onCreated(made as CrmTask);
      close(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const pick = (
    id: string,
    label: string,
    value: string,
    choices: Choice[],
    onChange: (v: string) => void,
    placeholder = "None"
  ) => (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select items={choices} value={value} onValueChange={(next) => onChange(next ? String(next) : "")}>
        <SelectTrigger id={id} className="h-8 w-full">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {choices.map((c) => (
            <SelectItem key={c.value} value={c.value}>
              {c.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New task</DialogTitle>
          <DialogDescription>
            {link ? `For ${link.name}.` : "A task is linked to a company, person or deal."}
          </DialogDescription>
        </DialogHeader>
        <form
          id="task-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="task-title">Title</Label>
            <Input id="task-title" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} required />
          </div>
          {!link && (
            <div className="grid gap-2 sm:grid-cols-[1fr_2fr]">
              {pick(
                "task-target-type",
                "Linked to",
                targetType,
                [
                  { value: "company", label: "Company" },
                  { value: "person", label: "Person" },
                  { value: "opportunity", label: "Deal" },
                ],
                (v) => {
                  setTargetType((v || "company") as Target);
                  setTarget(null);
                }
              )}
              <div className="space-y-1.5">
                <Label htmlFor="task-target">Record</Label>
                <FieldEditor
                  key={targetType}
                  id="task-target"
                  spec={{
                    name: "record_id",
                    label: "Record",
                    type: "relation",
                    choices: [],
                    search: SEARCH[targetType](workspace),
                  }}
                  value={target}
                  onChange={(value) => setTarget((value as string | null) ?? null)}
                />
              </div>
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="task-due">Due date</Label>
              <Input id="task-due" type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
            </div>
            {pick("task-bucket", "Priority bucket", bucket, buckets, setBucket)}
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {pick(
              "task-assignee",
              "Assignee",
              assignee ?? "",
              members.map((m) => ({ value: m.user_id, label: m.display_name })),
              (v) => setAssignee(v || null),
              "Choose"
            )}
            {pick("task-category", "Category", category, categories, setCategory)}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="task-body">Details</Label>
            <Textarea id="task-body" rows={3} maxLength={20000} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button type="submit" form="task-form" disabled={busy || !title.trim() || (!link && !target)}>
            {busy ? "Adding…" : "Add task"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** A record's Tasks tab (FR-CRM-REC-03): its tasks and Add task. */
export function TasksTab({
  workspace,
  bootstrap,
  members,
  tasks,
  link,
  onChange,
}: {
  workspace: string;
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  tasks: CrmTask[];
  link: { record_type: Target; record_id: string; name: string };
  onChange: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const canEdit = bootstrap.role !== "viewer";
  return (
    <div className="space-y-3">
      {canEdit && (
        <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
          Add task
        </Button>
      )}
      <TaskList
        workspace={workspace}
        tasks={tasks}
        bootstrap={bootstrap}
        timeZone={bootstrap.me.timezone}
        onChange={onChange}
      />
      {canEdit && (
        <TaskDialog
          open={adding}
          onOpenChange={setAdding}
          workspace={workspace}
          bootstrap={bootstrap}
          members={members}
          link={link}
          onCreated={onChange}
        />
      )}
    </div>
  );
}
