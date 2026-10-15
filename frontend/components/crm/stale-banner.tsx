"use client";

import Link from "next/link";
import { TriangleAlert } from "lucide-react";

import type { StaleJob } from "@/lib/api/crm-types";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";

/**
 * Late background jobs — spec 007 FR-CRM-JOB-03.
 *
 * A stopped scheduler cannot say so itself, so every CRM page load asks
 * (`/crm/bootstrap`) and an Admin sees this banner naming each job more than
 * twice its interval late. Everyone else gets an empty list from the server.
 */
export function StaleBanner({
  jobs,
  workspace,
  timeZone,
}: {
  jobs: StaleJob[];
  workspace: string;
  timeZone: CrmTimezone;
}) {
  if (jobs.length === 0) return null;
  return (
    <div
      role="status"
      className="mb-4 flex flex-wrap items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
    >
      <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-amber-600" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">Background jobs are late</p>
        <ul className="text-muted-foreground">
          {jobs.map((job) => (
            <li key={job.job}>
              {job.label}: last ran {formatDate(job.last_started_at, timeZone)}, about{" "}
              {Math.max(1, Math.round(job.overdue_minutes / 60))} h overdue.
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground">
          Flags, digests and reminders may be out of date until the scheduler runs again.
        </p>
      </div>
      <Link href={`/crm/${workspace}/settings?tab=jobs`} className="text-sm underline-offset-2 hover:underline">
        See the jobs
      </Link>
    </div>
  );
}
