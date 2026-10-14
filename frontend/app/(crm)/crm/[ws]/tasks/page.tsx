"use client";

import { useState } from "react";

import { useCrm } from "@/components/crm/shell";
import { TaskDialog, TaskList } from "@/components/crm/task-dialog";
import { Button } from "@/components/ui/button";
import { getCrmMembers, listCrmTasks } from "@/lib/api/crm";
import type { CrmTask } from "@/lib/api/crm-types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * Tasks — spec 007 FR-CRM-TASK-01, -02, FR-CRM-UI-02, -06; Q-57.
 *
 * The person's own open tasks by default, or everyone's, open or done. Each
 * shows the records it is linked to. A Viewer sees the tasks and may tick the
 * ones assigned to them; nothing else.
 */

type Who = "mine" | "everyone";
type Status = "open" | "done";

export default function TasksPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const canEdit = bootstrap.role !== "viewer";
  const [who, setWho] = useState<Who>("mine");
  const [status, setStatus] = useState<Status>("open");
  const [extra, setExtra] = useState<CrmTask[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const filters = { assignee_id: who === "mine" ? bootstrap.me.id : undefined, status };
  const { data, error, reload } = useAsync(async () => {
    const [page, members] = await Promise.all([listCrmTasks(ws, filters), getCrmMembers(ws)]);
    setExtra([]);
    setNext(page.next);
    return { tasks: page.rows, members };
  }, [ws, who, status]);
  const rows = [...(data?.tasks ?? []), ...extra];

  async function more() {
    if (!next) return;
    const page = await listCrmTasks(ws, { ...filters, after: next });
    setExtra((current) => [...current, ...page.rows]);
    setNext(page.next);
  }

  const toggle = <T extends string>(value: T, current: T, set: (v: T) => void, label: string) => (
    <button
      type="button"
      aria-pressed={value === current}
      onClick={() => set(value)}
      className={cn(
        "rounded-md px-2.5 py-1 text-sm",
        value === current ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground"
      )}
    >
      {label}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">Tasks</h1>
        {canEdit && <Button onClick={() => setAdding(true)}>New task</Button>}
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="flex gap-1 rounded-md border p-0.5">
          {toggle<Who>("mine", who, setWho, "Mine")}
          {toggle<Who>("everyone", who, setWho, "Everyone")}
        </div>
        <div className="flex gap-1 rounded-md border p-0.5">
          {toggle<Status>("open", status, setStatus, "Open")}
          {toggle<Status>("done", status, setStatus, "Done")}
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {!error && !data && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && (
        <TaskList
          workspace={ws}
          tasks={rows}
          bootstrap={bootstrap}
          timeZone={bootstrap.me.timezone}
          onChange={reload}
          showLinks
        />
      )}
      {next && (
        <Button variant="outline" onClick={more}>
          Load more
        </Button>
      )}

      {canEdit && data && (
        <TaskDialog
          open={adding}
          onOpenChange={setAdding}
          workspace={ws}
          bootstrap={bootstrap}
          members={data.members}
          onCreated={reload}
        />
      )}
    </div>
  );
}
