"use client";

import type { CrmJobRun } from "@/lib/api/crm-types";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * Background job runs — spec 007 FR-CRM-JOB-01.
 *
 * Used by a workspace's Settings (its own runs, with their `error`) and by
 * the TVA admin panel (every workspace's runs, `error_public` only: the
 * server never sends the other there). Outcome is written in words, never
 * colour alone.
 */

const OUTCOME: Record<CrmJobRun["outcome"], string> = {
  running: "Running",
  ok: "OK",
  failed: "Failed",
  skipped: "Skipped (switched off)",
};

function timeOf(at: string, timeZone: CrmTimezone): string {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone }).format(
    new Date(at)
  );
}

function countsText(counts: CrmJobRun["counts"]): string {
  const parts = Object.entries(counts).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`);
  return parts.length ? parts.join(", ") : "—";
}

export function JobsTable({
  runs,
  timeZone,
  showWorkspace = false,
}: {
  runs: (CrmJobRun & { workspace?: string | null })[];
  timeZone: CrmTimezone;
  showWorkspace?: boolean;
}) {
  if (runs.length === 0) {
    return <p className="p-3 text-sm text-muted-foreground">No runs recorded yet.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th className="p-2 font-medium">Started</th>
            {showWorkspace && <th className="p-2 font-medium">Workspace</th>}
            <th className="p-2 font-medium">Job</th>
            <th className="p-2 font-medium">Outcome</th>
            <th className="p-2 font-medium">Counts</th>
            <th className="p-2 font-medium">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {runs.map((run) => (
            <tr key={run.id} className="align-top">
              <td className="whitespace-nowrap p-2 text-muted-foreground">
                {formatDate(run.started_at, timeZone)} {timeOf(run.started_at, timeZone)}
              </td>
              {showWorkspace && <td className="p-2">{run.workspace ?? "All (digests)"}</td>}
              <td className="p-2">{run.label}</td>
              <td className={cn("p-2", run.outcome === "failed" && "font-medium text-destructive")}>
                {OUTCOME[run.outcome]}
                {run.alerted && <span className="block text-xs text-muted-foreground">Admins alerted</span>}
              </td>
              <td className="p-2 text-xs text-muted-foreground">{countsText(run.counts)}</td>
              <td className="max-w-sm p-2 text-xs">
                {run.error ?? run.error_public ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
