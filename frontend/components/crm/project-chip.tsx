"use client";

import { Badge } from "@/components/ui/badge";
import type { CrmProjectBlock } from "@/lib/api/crm-types";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";

/**
 * A linked TVA project — spec 007 FR-CRM-TVA-02, FR-CRM-CO-02,
 * FR-CRM-REC-03, FR-CRM-ACC-03.
 *
 * Its name, status, archived state and phase dates: all the CRM shows of a
 * TVA project, to every role, Viewers and CRM-only accounts included. Never
 * an amount, and no link into TVA (a CRM-only account cannot open it).
 */
export function ProjectChip({ project, timeZone }: { project: CrmProjectBlock; timeZone: CrmTimezone }) {
  return (
    <div className="space-y-1 rounded-md border p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{project.name}</span>
        <Badge variant="outline" className={project.status === "tentative" ? "border-dashed" : undefined}>
          {project.status === "tentative" ? "Tentative" : "Confirmed"}
        </Badge>
        {project.is_archived && <Badge variant="outline">Archived</Badge>}
      </div>
      {project.phases.length > 0 ? (
        <ul className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {project.phases.map((phase) => (
            <li key={phase.phase} className="rounded bg-muted px-2 py-0.5">
              {phase.label}: {formatDate(phase.starts_on, timeZone)} – {formatDate(phase.ends_on, timeZone)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">No phase dates yet.</p>
      )}
    </div>
  );
}

/** Related: the TVA projects of a Labs company or deal. */
export function ProjectsSection({
  title,
  projects,
  timeZone,
  empty,
}: {
  title: string;
  projects: CrmProjectBlock[];
  timeZone: CrmTimezone;
  empty: string;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {projects.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="space-y-2">
          {projects.map((p) => (
            <ProjectChip key={p.id} project={p} timeZone={timeZone} />
          ))}
        </div>
      )}
    </section>
  );
}
