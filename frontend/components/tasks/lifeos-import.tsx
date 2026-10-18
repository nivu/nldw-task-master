"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/portal";
import type { LifeOsApplied, LifeOsCounts, LifeOsDryRun } from "@/lib/api/task-types";
import { applyLifeOs, dryRunLifeOs } from "@/lib/api/tasks";

const MAX_BYTES = 4 * 1024 * 1024;
const COUNT_LABELS: [keyof LifeOsCounts, string][] = [
  ["rows", "Rows in the file"],
  ["open", "Open"],
  ["waiting", "Waiting"],
  ["done", "Done"],
  ["cancelled", "Cancelled"],
  ["existing", "Already imported (skipped)"],
  ["repeated", "Repeated in the file (skipped)"],
];

/**
 * The Life OS import — spec 008 FR-TASK-IMP-01 to -08, Journey 4.1. Owner
 * only; the server re-checks.
 *
 * Upload the CSV export of the Tasks tab and dry-run it. The report shows
 * the counts, the proposed owner mapping (the only part that can be edited:
 * a changed mapping is dry-run again), every status or priority value the
 * fixed mapping does not know (fixed in the sheet and the file exported
 * again), and the `D/M/YYYY` dates to check. The real run sends the same
 * file with the dry run's id; running it twice changes nothing.
 */
export function LifeOsImport() {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<LifeOsDryRun | null>(null);
  const [owners, setOwners] = useState<Record<string, string>>({});
  const [edited, setEdited] = useState(false);
  const [done, setDone] = useState<LifeOsApplied | null>(null);
  const [busy, setBusy] = useState<"dry" | "run" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tooBig = file !== null && file.size > MAX_BYTES;
  const unmappedValues = report ? [...report.unmapped.status, ...report.unmapped.priority] : [];

  function choose(next: File | null) {
    setFile(next);
    setReport(null);
    setOwners({});
    setEdited(false);
    setDone(null);
    setError(null);
  }

  async function dryRun() {
    if (!file) return;
    setBusy("dry");
    setError(null);
    setDone(null);
    try {
      const mapping = report
        ? Object.fromEntries(Object.entries(owners).map(([value, email]) => [value, email.trim() || null]))
        : undefined;
      const next = await dryRunLifeOs(file, mapping);
      setReport(next);
      setOwners(Object.fromEntries(next.owners.map((o) => [o.value, o.proposed_email ?? ""])));
      setEdited(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function run() {
    if (!file || !report) return;
    setBusy("run");
    setError(null);
    try {
      setDone(await applyLifeOs(report.run_id, file));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="lifeos-file">Life OS Tasks tab, exported as CSV</Label>
          <input
            id="lifeos-file"
            type="file"
            accept=".csv,text/csv"
            className="block max-w-full text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-transparent file:px-2 file:py-1 file:text-sm"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
          />
        </div>
        <Button onClick={dryRun} disabled={!file || tooBig || busy !== null}>
          {busy === "dry" ? "Checking…" : report ? "Dry run again" : "Dry run"}
        </Button>
      </div>
      {tooBig && (
        <p role="alert" className="text-sm text-destructive">
          This file is larger than 4 MB.
        </p>
      )}
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {report && (
        <div className="space-y-5" data-testid="lifeos-report">
          <section className="space-y-2">
            <h3 className="text-sm font-medium">What the import would do</h3>
            <table className="w-full max-w-sm text-sm">
              <tbody className="divide-y">
                {COUNT_LABELS.map(([key, label]) => (
                  <tr key={key}>
                    <td className="py-1.5 pr-3">{label}</td>
                    <td className="py-1.5 text-right tabular-nums">{report.counts[key]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.waiting_as_life_os > 0 && (
              <p className="text-xs text-muted-foreground">
                {report.waiting_as_life_os} waiting {report.waiting_as_life_os === 1 ? "row waits" : "rows wait"} on
                &ldquo;Life OS&rdquo;: the sheet does not say who or what.
              </p>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Owners</h3>
            <p className="text-xs text-muted-foreground">
              Each owner in the sheet goes to the account with this email. Change one and dry-run again.
            </p>
            <div className="space-y-2">
              {report.owners.map((o) => (
                <div key={o.value} className="flex flex-wrap items-center gap-2 text-sm">
                  <Label htmlFor={`owner-${o.value}`} className="w-32 shrink-0 font-normal">
                    {o.value}
                  </Label>
                  <Input
                    id={`owner-${o.value}`}
                    type="email"
                    className="h-8 w-full max-w-xs"
                    placeholder="Email of their account"
                    value={owners[o.value] ?? ""}
                    onChange={(e) => {
                      setOwners((m) => ({ ...m, [o.value]: e.target.value }));
                      setEdited(true);
                    }}
                  />
                  <span className="text-xs text-muted-foreground">
                    {o.mapped ? o.proposed_name : "Not mapped"}
                  </span>
                </div>
              ))}
            </div>
          </section>

          {unmappedValues.length > 0 && (
            <section className="space-y-1">
              <h3 className="text-sm font-medium">Values the import cannot map</h3>
              <ul className="list-disc pl-5 text-sm">
                {report.unmapped.status.map((v) => (
                  <li key={`s-${v}`}>Status &ldquo;{v}&rdquo;</li>
                ))}
                {report.unmapped.priority.map((v) => (
                  <li key={`p-${v}`}>Priority &ldquo;{v}&rdquo;</li>
                ))}
              </ul>
              <p className="text-sm text-muted-foreground">Fix it in the sheet and export again.</p>
            </section>
          )}

          {report.ambiguous_dates.length > 0 && (
            <section className="space-y-1">
              <h3 className="text-sm font-medium">Dates to check</h3>
              <p className="text-xs text-muted-foreground">Read day first.</p>
              <ul className="space-y-0.5 text-sm">
                {report.ambiguous_dates.map((d) => (
                  <li key={`${d.row}-${d.value}`}>
                    Row {d.row}: {d.value} is read as {d.read_as}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {report.blocking.length > 0 ? (
            <section className="space-y-1">
              <h3 className="text-sm font-medium">Before the import can run</h3>
              <ul className="list-disc pl-5 text-sm text-destructive">
                {report.blocking.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </section>
          ) : (
            !done &&
            !edited && (
              <Button onClick={run} disabled={busy !== null}>
                {busy === "run" ? "Importing…" : "Run the import"}
              </Button>
            )
          )}
          {edited && !done && (
            <p className="text-sm text-muted-foreground">You changed the owners; dry-run again to use them.</p>
          )}
        </div>
      )}

      {done && (
        <p className="text-sm" data-testid="lifeos-done">
          Imported {done.counts.created} {done.counts.created === 1 ? "task" : "tasks"}; skipped{" "}
          {done.counts.existing} already imported and {done.counts.repeated} repeated.
        </p>
      )}
    </div>
  );
}
