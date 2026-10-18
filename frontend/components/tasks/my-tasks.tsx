"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { TASK_CREATED_EVENT } from "@/components/tasks/quick-add";
import { TaskFilters } from "@/components/tasks/task-filters";
import { TaskPanel } from "@/components/tasks/task-panel";
import { TaskSections } from "@/components/tasks/task-sections";
import { TaskSettings } from "@/components/tasks/task-settings";
import type { MyTasks as MyTasksData, MyTasksFilters, TaskBucket, TaskOut } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { completeTask, getMyTasks, reopenTask, updateTask } from "@/lib/api/tasks";
import { PRIORITIES, SECTIONS } from "@/lib/tasks/labels";

/**
 * My Tasks — spec 008 FR-TASK-LIST-01 to -08, FR-TASK-ADD-06; plan §5.6.
 *
 * One page, mounted in both shells (`/tasks` for staff, `/crm/[ws]/my-tasks`
 * for everyone with a workspace, CRM-only sellers included). It reads and
 * writes only through `/api/proxy`. A task saved by the quick-add box is put
 * in its section at once, from the box's `tasks:created` event; nobody
 * else's page is pushed anything. With phase 2 on for the reader, the
 * panel offers Accept, Hand back and comments, and the page their nudge days
 * (FR-TASK-DEL-02 to -06).
 */

const PAGE = 20;

/** FR-TASK-LIST-01 — due date (none last), then priority, then title. */
function order(a: TaskOut, b: TaskOut): number {
  if ((a.due_on === null) !== (b.due_on === null)) return a.due_on === null ? 1 : -1;
  if (a.due_on && b.due_on && a.due_on !== b.due_on) return a.due_on < b.due_on ? -1 : 1;
  const p = PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority);
  if (p !== 0) return p;
  return a.title.toLowerCase().localeCompare(b.title.toLowerCase());
}

/** The page with `task` taken out and, when it is still the caller's, put
 *  where it now belongs. */
function placed(data: MyTasksData, task: TaskOut, mine: boolean): MyTasksData {
  const sections = Object.fromEntries(
    SECTIONS.map((s) => [s, data.sections[s].filter((t) => t.id !== task.id)])
  ) as MyTasksData["sections"];
  let closed = data.closed.filter((t) => t.id !== task.id);
  if (mine) {
    if (task.status === "done" || task.status === "cancelled") {
      closed = [task, ...closed];
    } else {
      const s = task.effective_bucket ?? task.bucket ?? "later";
      sections[s] = [...sections[s], task].sort(order);
    }
  }
  return { ...data, sections, closed };
}

export function MyTasks({
  projectsHref,
  level = 1,
  meId = null,
}: {
  projectsHref?: string;
  /** The reader's effective tasks level (`me.tasks.level`). */
  level?: number;
  /** The reader's own id (`me.id`). */
  meId?: string | null;
}) {
  const [filters, setFilters] = useState<MyTasksFilters>({});
  const [data, setData] = useState<MyTasksData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [me, setMe] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const filtered = useRef(false);

  useEffect(() => {
    filtered.current = Boolean(filters.tag || filters.priority || filters.link_type || filters.link_id || filters.q);
  }, [filters]);

  useEffect(() => {
    let cancelled = false;
    getMyTasks({ ...filters, closed_limit: PAGE })
      .then((out) => {
        if (cancelled) return;
        setData(out);
        setError(null);
        // Every task here is assigned to the caller.
        const any = [...SECTIONS.flatMap((s) => out.sections[s]), ...out.closed][0];
        if (any?.assignee.id) setMe(any.assignee.id);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [filters, reloads]);

  // FR-TASK-ADD-06 — the creator's open page shows a new task at once.
  // A filtered page (FR-TASK-LIST-07) asks again, so it shows the task only
  // when the filter matches it.
  useEffect(() => {
    function onCreated(e: Event) {
      const task = (e as CustomEvent<TaskOut>).detail;
      if (!task || task.assignee.id !== task.created_by.id) return;
      setMe(task.assignee.id);
      if (filtered.current) setReloads((n) => n + 1);
      else setData((d) => (d ? placed(d, task, true) : d));
    }
    window.addEventListener(TASK_CREATED_EVENT, onCreated);
    return () => window.removeEventListener(TASK_CREATED_EVENT, onCreated);
  }, []);

  const changed = useCallback(
    (task: TaskOut) => setData((d) => (d ? placed(d, task, me === null || task.assignee.id === me) : d)),
    [me]
  );

  async function act(task: TaskOut, write: () => Promise<TaskOut>, optimistic?: TaskOut) {
    setError(null);
    if (optimistic) changed(optimistic);
    try {
      changed(await write());
    } catch (err) {
      changed(task);
      setError(errorMessage(err));
    }
  }

  async function moreClosed() {
    if (!data?.next_closed) return;
    try {
      const out = await getMyTasks({ ...filters, closed_limit: PAGE, closed_before: data.next_closed });
      setData((d) => (d ? { ...d, closed: [...d.closed, ...out.closed], next_closed: out.next_closed } : d));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="font-heading text-lg font-semibold">My Tasks</h1>
        <p className="text-sm text-muted-foreground">What you have on, and what you are helping others with.</p>
      </div>
      <TaskFilters value={filters} onChange={setFilters} />
      {level >= 2 && <TaskSettings />}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {data && (
        <TaskSections
          sections={data.sections}
          closed={data.closed}
          hasMoreClosed={data.next_closed !== null}
          onOpen={(task) => setOpen(task.id)}
          onComplete={(task) => act(task, () => completeTask(task.id))}
          onReopen={(task) => act(task, () => reopenTask(task.id))}
          onMove={(task: TaskOut, bucket: TaskBucket) =>
            act(
              task,
              () => updateTask(task.id, { bucket, expect_updated_at: task.updated_at }),
              // A task due today or earlier stays in Today (FR-TASK-LIST-02).
              {
                ...task,
                bucket,
                effective_bucket: task.due_on !== null && task.due_on <= data.as_of ? "today" : bucket,
              }
            )
          }
          onMoreClosed={moreClosed}
        />
      )}
      <TaskPanel
        taskId={open}
        onOpenChange={(o) => !o && setOpen(null)}
        onChanged={changed}
        onOpenTask={setOpen}
        projectsHref={projectsHref}
        meId={meId}
        delegation={level >= 2}
      />
    </div>
  );
}
