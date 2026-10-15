"use client";

import Link from "next/link";

import { CsvImportWizard } from "@/components/crm/csv-import-wizard";
import { useCrm } from "@/components/crm/shell";
import { TwentyImport } from "@/components/crm/twenty-import";
import { Button } from "@/components/ui/button";
import { listCrmImports } from "@/lib/api/crm";
import type { ImportRun } from "@/lib/api/crm-types";
import { formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

const STATUS: Record<ImportRun["status"], string> = {
  dry_run: "Dry run",
  applied: "Imported",
  failed: "Failed",
};

/**
 * Import — spec 007 FR-CRM-MIG-01 to -08 (journey 4.6) and, from 1b, CSV
 * import of one object (FR-CRM-IO-01 to -03).
 *
 * Admins only; the server refuses everyone else whatever this page shows.
 * Below the importers, the workspace's recent runs with their counts (never
 * the file or its rows).
 */
export default function ImportPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const tz = bootstrap.me.timezone;
  const admin = bootstrap.role === "admin";
  const { data: runs, reload } = useAsync(
    () => (admin ? listCrmImports(ws) : Promise.resolve([] as ImportRun[])),
    [ws, admin]
  );

  if (!admin) {
    return <p className="text-sm text-muted-foreground">Only a workspace admin can import.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">Import from Twenty</h1>
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/crm/${ws}/settings`} />}>
          Settings
        </Button>
      </div>
      <p className="max-w-2xl text-sm text-muted-foreground">
        Every import starts as a dry run that counts what would change and lists every conflict. Records keep
        their Twenty ids, so running the same file again changes nothing. Deals already at Proposal or later
        get no TVA project; link them from the deal afterwards.
      </p>

      <TwentyImport workspace={ws} onImported={reload} />

      <div className="space-y-2 border-t pt-6">
        <h2 className="font-heading text-base font-semibold">Import a CSV</h2>
        <p className="max-w-2xl text-sm text-muted-foreground">
          One file of companies, people or deals, from a spreadsheet or an export of a view here. Map its columns,
          check it, fix any row in place, then confirm.
        </p>
        <CsvImportWizard workspace={ws} onImported={reload} />
      </div>

      {runs && runs.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Recent runs</h2>
          <ul className="space-y-1 text-sm">
            {runs.map((run) => (
              <li key={run.id} className="flex flex-wrap gap-x-3">
                <span className="tabular-nums">{formatDate(run.applied_at ?? run.created_at, tz)}</span>
                <span>{STATUS[run.status]}</span>
                <span className="text-muted-foreground">
                  {run.kind === "csv" ? `CSV of ${run.object}` : (run.object ?? "Whole export")}
                  {run.counts &&
                    ` · ${Object.values(run.counts).reduce((n, c) => n + (c?.in_file ?? 0), 0)} rows`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
