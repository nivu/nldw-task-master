"use client";

import { useState } from "react";

import { ProjectsPanel } from "@/components/portal/projects-panel";
import {
  getMe,
  getPeopleFinancials,
  listAllocatablePeople,
  listAllocations,
  listMyReports,
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
 * Leads land here too (FR-ROLE-07/08), without money: no revenue, no
 * milestones, and only their own reports to allocate. The server enforces
 * that; this page just doesn't ask for what it would be refused.
 */
export default function ProjectsPage() {
  const [notice, setNotice] = useState<string | null>(null);

  const { data, error, setError, reload } = useAsync<{
    projects: Project[];
    allocations: AllocationRow[];
    people: AllocatablePerson[];
    leads: AllocatablePerson[];
    meId: string;
    currency: string;
    showMoney: boolean;
  }>(async () => {
    const me = await getMe();
    const showMoney = me.capabilities.financials;
    const [projects, allocations, people, financials] = await Promise.all([
      listProjects(),
      listAllocations(),
      showMoney ? listAllocatablePeople() : listMyReports(),
      showMoney ? getPeopleFinancials() : null,
    ]);
    // Spec 002 FR-PROJ-07 — who may be named a project's lead: the same
    // people list, plus the viewer, whom a lead's own-reports list leaves out.
    const leads = people.some((p) => p.id === me.id)
      ? people
      : [...people, { id: me.id, display_name: me.display_name }].sort((a, b) =>
          a.display_name.localeCompare(b.display_name)
        );
    return {
      projects,
      allocations,
      people,
      leads,
      meId: me.id,
      currency: financials?.currency ?? "",
      showMoney,
    };
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
          {data.showMoney
            ? "Create projects, set their phases and revenue, and allocate people. Effort and money are reported under Effort."
            : "Create projects, set their phases, and allocate your team."}
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
        leads={data.leads}
        meId={data.meId}
        currency={data.currency}
        showMoney={data.showMoney}
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
