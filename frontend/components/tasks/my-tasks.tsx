"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { TodayList } from "@/components/crm/today-list";
import { TASK_CREATED_EVENT } from "@/components/tasks/quick-add";
import { TaskFilters } from "@/components/tasks/task-filters";
import { TaskPanel } from "@/components/tasks/task-panel";
import { FOCUS_CLASS } from "@/components/tasks/task-row";
import { TaskSections } from "@/components/tasks/task-sections";
import { TaskSettings } from "@/components/tasks/task-settings";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  AssignedByMeView,
  MyTasks as MyTasksData,
  MyTasksFilters,
  TaskBrief,
  TaskBucket,
  TaskOut,
  WaitingView,
} from "@/lib/api/task-types";
import { errorMessage, getMe } from "@/lib/api/portal";
import {
  completeTask,
  getAssignedByMe,
  getMyTasks,
  getTaskBrief,
  getWaitingTasks,
  reopenTask,
  updateTask,
} from "@/lib/api/tasks";
import { dueText, overdueText, PRIORITIES, SECTIONS } from "@/lib/tasks/labels";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * My Tasks — spec 008 FR-TASK-LIST-01 to -08, FR-TASK-ADD-06; plan §5.6.
 *
 * One page, mounted in both shells (`/tasks` for staff, `/crm/[ws]/my-tasks`
 * for everyone with a workspace, CRM-only sellers included). It reads and
 * writes only through `/api/proxy`. A task saved by the quick-add box is put
 * in its section at once, from the box's `tasks:created` event; nobody
 * else's page is pushed anything. With phase 2 on for the reader, the
 * panel offers Accept, Hand back and comments, and the page their nudge days
 * (FR-TASK-DEL-02 to -06), and four views chosen by `?view=`: Mine, Waiting
 * on (grouped by whom, with each task's age), Assigned by me (with assignee
 * and status) and the Brief (today, overdue, "nudge?" and each workspace's
 * CRM Today; read when opened, never sent) (FR-TASK-VIEW-01, -02,
 * FR-TASK-BRIEF-01 to -03).
 */

const PAGE = 20;

type View = "mine" | "waiting" | "assigned_by_me" | "brief";

const VIEWS: { value: View; label: string }[] = [
  { value: "mine", label: "Mine" },
  { value: "waiting", label: "Waiting on" },
  { value: "assigned_by_me", label: "Assigned by me" },
  { value: "brief", label: "Brief" },
];

function days(n: number): string {
  return n === 1 ? "1 day" : `${n} days`;
}

/** One task in a phase-2 view: its title opens the panel; `meta` says what
 *  the view is about (its age, its assignee and status, its idle days). */
function ViewRow({ task, meta, onOpen }: { task: TaskOut; meta: string[]; onOpen: (task: TaskOut) => void }) {
  const closed = task.status === "done" || task.status === "cancelled";
  const overdue = overdueText(task.days_overdue);
  return (
    <li data-testid="task-row" className="rounded-md border bg-background px-2 py-2">
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
        {meta.map((m) => (
          <span key={m}>{m}</span>
        ))}
        {task.due_on && <span>Due {dueText(task.due_on)}</span>}
        {overdue && <span className="font-medium text-destructive">{overdue}</span>}
        {task.workspace && <span>{task.workspace.name}</span>}
        {task.project && <span>Project {task.project.name}</span>}
      </div>
    </li>
  );
}

function Block({
  title,
  empty,
  count,
  children,
}: {
  title: string;
  empty: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border p-3">
      <h2 className="mb-2 flex items-baseline gap-2 text-sm font-semibold">
        {title}
        <span className="text-xs font-normal text-muted-foreground">{count}</span>
      </h2>
      {count === 0 ? (
        <p className="text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="space-y-1.5">{children}</ul>
      )}
    </section>
  );
}

function Loading({ error }: { error: string | null }) {
  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  return <p className="text-sm text-muted-foreground">Loading…</p>;
}

/** FR-TASK-VIEW-01 — the reader's waiting tasks, grouped by whom they wait on. */
function WaitingOn({ version, onOpen }: { version: number; onOpen: (task: TaskOut) => void }) {
  const { data, error } = useAsync<WaitingView>(getWaitingTasks, [version]);
  if (!data) return <Loading error={error} />;
  if (data.groups.length === 0) return <p className="text-sm text-muted-foreground">Nothing is waiting.</p>;
  return (
    <div className="space-y-3">
      {data.groups.map((g) => (
        <Block
          key={`${g.waiting_on.id ?? ""}:${g.waiting_on.label}`}
          title={`Waiting on ${g.waiting_on.label}`}
          empty=""
          count={g.tasks.length}
        >
          {g.tasks.map((t) => (
            <ViewRow key={t.id} task={t} meta={[`${days(t.age_days)} old`]} onOpen={onOpen} />
          ))}
        </Block>
      ))}
    </div>
  );
}

/** FR-TASK-VIEW-02 — tasks the reader created for someone else. */
function AssignedByMe({ version, onOpen }: { version: number; onOpen: (task: TaskOut) => void }) {
  const { data, error } = useAsync<AssignedByMeView>(getAssignedByMe, [version]);
  if (!data) return <Loading error={error} />;
  return (
    <Block title="Assigned by me" empty="You have not given anyone a task." count={data.tasks.length}>
      {data.tasks.map((t) => (
        <ViewRow key={t.id} task={t} meta={[`To ${t.assignee.name ?? "someone"}`, t.status_label]} onOpen={onOpen} />
      ))}
    </Block>
  );
}

/** FR-TASK-BRIEF-01 to -03 — one screen, read when opened, never sent. */
function Brief({
  version,
  onOpen,
  meId,
}: {
  version: number;
  onOpen: (task: TaskOut) => void;
  meId: string | null;
}) {
  const { data, error } = useAsync<TaskBrief>(getTaskBrief, [version]);
  const me = useAsync(getMe, []);
  const zone = me.data?.preferences?.timezone ?? "Asia/Kolkata";
  if (!data) return <Loading error={error} />;
  return (
    <div className="space-y-4">
      <Block title="Today" empty="Nothing for today." count={data.today.length}>
        {data.today.map((t) => (
          <ViewRow key={t.id} task={t} meta={[]} onOpen={onOpen} />
        ))}
      </Block>
      <Block title="Overdue" empty="Nothing overdue." count={data.overdue.length}>
        {data.overdue.map((t) => (
          <ViewRow key={t.id} task={t} meta={[]} onOpen={onOpen} />
        ))}
      </Block>
      <Block title="Nudge?" empty="Nothing has waited too long." count={data.nudge.length}>
        {data.nudge.map((t) => (
          <ViewRow
            key={t.id}
            task={t}
            meta={[`Waiting on ${t.waiting_on?.label ?? "someone"}`, `untouched ${days(t.idle_days)}`]}
            onOpen={onOpen}
          />
        ))}
      </Block>
      {data.workspaces.map((w) => (
        <section key={w.workspace.key} className="space-y-2">
          <h2 className="text-sm font-semibold">{w.workspace.name} — CRM Today</h2>
          <TodayList
            workspace={w.workspace.key}
            today={{
              as_of: data.as_of,
              timezone: zone,
              person: { id: meId ?? "", name: null },
              sections: w.sections,
            }}
            timeZone={zone}
          />
        </section>
      ))}
    </div>
  );
}

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
  const [version, setVersion] = useState(0);
  const filtered = useRef(false);
  const router = useRouter();
  const pathname = usePathname();
  const asked = useSearchParams().get("view");
  // The views are phase 2; below it, `?view=` is ignored.
  const view: View = level >= 2 && VIEWS.some((v) => v.value === asked) ? (asked as View) : "mine";

  function choose(next: View) {
    router.replace(next === "mine" ? pathname : `${pathname}?view=${next}`);
  }

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
    (task: TaskOut) => {
      setData((d) => (d ? placed(d, task, me === null || task.assignee.id === me) : d));
      setVersion((n) => n + 1);
      // Closing a recurring task created its next occurrence (FR-TASK-RRULE-02).
      if (task.recurrence && (task.status === "done" || task.status === "cancelled")) {
        setReloads((n) => n + 1);
      }
    },
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
      {level >= 2 && (
        <Tabs value={view} onValueChange={(value) => choose(String(value) as View)}>
          <TabsList className="max-w-full overflow-x-auto">
            {VIEWS.map((v) => (
              <TabsTrigger key={v.value} value={v.value}>
                {v.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      )}
      {view === "waiting" && <WaitingOn version={version} onOpen={(task) => setOpen(task.id)} />}
      {view === "assigned_by_me" && <AssignedByMe version={version} onOpen={(task) => setOpen(task.id)} />}
      {view === "brief" && <Brief version={version} onOpen={(task) => setOpen(task.id)} meId={meId} />}
      {view === "mine" && <TaskFilters value={filters} onChange={setFilters} />}
      {view === "mine" && level >= 2 && <TaskSettings onAdded={() => setReloads((n) => n + 1)} />}
      {view === "mine" && error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {view === "mine" && data && (
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
        recurring={level >= 3}
      />
    </div>
  );
}
