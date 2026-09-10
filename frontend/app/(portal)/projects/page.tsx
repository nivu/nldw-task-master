"use client";

import { useState } from "react";

import { ProjectsPanel } from "@/components/portal/projects-panel";
import {
  getPeopleFinancials,
  listAllocatablePeople,
  listAllocations,
  listProjects,
} from "@/lib/api/portal";
import type { AllocatablePerson, AllocationRow, Project } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";

/**
 * Projects — spec 003 FR-ROLE-02.
 *
 * The manager's home. A manager runs projects without being an admin, so this
 * lives at its own route rather than inside the admin panel: the admin panel
 * is people, allowances, holidays and policy, none of which a manager may
 * touch (FR-ROLE-03). Admins land here too, for the same work.
 *
 * Every call below is guarded server-side by the manager tier; a lead who
 * types the URL gets the refusal rendered, not a blank page.
 */
export default function ProjectsPage() {
  const [notice, setNotice] = useState<string | null>(null);

  const { data, error, setError, reload } = useAsync<{
    projects: Project[];
    allocations: AllocationRow[];
    people: AllocatablePerson[];
    currency: string;
  }>(async () => {
    const [projects, allocations, people, financials] = await Promise.all([
      listProjects(),
      listAllocations(),
      listAllocatablePeople(),
      getPeopleFinancials(),
    ]);
    return { projects, allocations, people, currency: financials.currency };
  }, []);

  if (error && !data) {
    return (
      <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-heading text-lg font-semibold">Projects</h1>
        <p className="text-sm text-muted-foreground">
          Create projects, set their phases and revenue, and allocate people. Effort
          and money are reported under Effort.
        </p>
      </div>

      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {notice && <div className="rounded-md bg-muted p-3 text-sm">{notice}</div>}

      <ProjectsPanel
        projects={data.projects}
        allocations={data.allocations}
        people={data.people}
        currency={data.currency}
        onDone={(message) => {
          setNotice(message);
          setError(null);
          reload();
        }}
        onError={setError}
      />
    </div>
  );
}
