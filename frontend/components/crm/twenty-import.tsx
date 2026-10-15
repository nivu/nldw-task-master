"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { applyCrmImport, dryRunCrmImport } from "@/lib/api/crm";
import type { ImportApplied, ImportCounts, ImportDryRun, ImportObject } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

const MAX_BYTES = 4 * 1024 * 1024;
const OBJECT_LABELS: Record<string, string> = {
  companies: "Companies",
  people: "People",
  opportunities: "Deals",
  tasks: "Tasks",
  notes: "Notes",
  attachments: "Attachments",
};

/**
 * The Twenty importer — spec 007 FR-CRM-MIG-01 to -07, journey 4.6.
 *
 * Upload one export (the JSON, or one object's CSV), dry-run it, read the
 * report, then run it. The real run sends the same file again with the dry
 * run's id; the server refuses another file or a dry run older than a day,
 * and anything the report marks as blocking. Open deals with no next step
 * pass only with the fix-up (Q-46). Running the same file twice changes
 * nothing. Attachments are listed to re-link from Drive, never uploaded.
 */
export function TwentyImport({ workspace, onImported }: { workspace: string; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportDryRun | null>(null);
  const [fixup, setFixup] = useState(false);
  const [done, setDone] = useState<ImportApplied | null>(null);
  const [busy, setBusy] = useState<"dry" | "run" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tooBig = file !== null && file.size > MAX_BYTES;
  const blocking = (report?.conflicts ?? []).filter(
    (c) => c.blocking && !(fixup && c.kind === "no_next_step")
  );

  function choose(next: File | null) {
    setFile(next);
    setReport(null);
    setDone(null);
    setFixup(false);
    setError(null);
  }

  async function dryRun() {
    if (!file) return;
    setBusy("dry");
    setError(null);
    setDone(null);
    try {
      setReport(await dryRunCrmImport(workspace, file));
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
      setDone(await applyCrmImport(workspace, report.run_id, file, fixup));
      onImported();
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
          <Label htmlFor="twenty-file">Twenty export (JSON, or one object&apos;s CSV)</Label>
          <input
            id="twenty-file"
            type="file"
            accept=".json,.csv,application/json,text/csv"
            className="block max-w-full text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-transparent file:px-2 file:py-1 file:text-sm"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
          />
        </div>
        <Button onClick={dryRun} disabled={!file || tooBig || busy !== null}>
          {busy === "dry" ? "Checking…" : "Dry run"}
        </Button>
      </div>
      {tooBig && (
        <p role="alert" className="text-sm text-destructive">
          This file is larger than 4 MB; export fewer records at a time.
        </p>
      )}
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {report && (
        <div className="space-y-4" data-testid="import-report">
          <CountsTable title="What the import would do" counts={report.counts} />

          {report.conflicts.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Conflicts and rule violations</h3>
              <ul className="space-y-1 text-sm">
                {report.conflicts.map((c, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-2">
                    <span className="w-28 shrink-0 text-xs text-muted-foreground">
                      {OBJECT_LABELS[c.object] ?? c.object}, row {c.row}
                    </span>
                    {c.blocking && <Badge variant="destructive">Fix first</Badge>}
                    <span className="min-w-0 flex-1">{c.detail}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {report.lead_stages.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Lead stage of each company</h3>
              <div className="overflow-x-auto">
                <table className="w-full max-w-xl text-sm">
                  <tbody className="divide-y">
                    {report.lead_stages.map((s) => (
                      <tr key={s.row}>
                        <td className="py-1.5 pr-3">{s.company}</td>
                        <td className="py-1.5 pr-3">{s.derived_label}</td>
                        <td className="py-1.5 text-xs text-muted-foreground">{s.because}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {report.attachments.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Attachments to re-link from Drive</h3>
              <p className="text-xs text-muted-foreground">
                Files are not imported. Add each one as a Drive link on its record after the import.
              </p>
              <ul className="space-y-1 text-sm">
                {report.attachments.map((a, i) => (
                  <li key={i}>
                    {a.name}
                    {a.record && <span className="text-muted-foreground"> — {a.record}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {report.fixup_candidates > 0 && (
            <div className="flex items-start gap-2 text-sm">
              <Checkbox
                id="import-fixup"
                checked={fixup}
                onCheckedChange={(checked) => setFixup(Boolean(checked))}
              />
              <Label htmlFor="import-fixup" className="font-normal">
                Give the {report.fixup_candidates} open{" "}
                {report.fixup_candidates === 1 ? "deal" : "deals"} with no next step the next step
                &ldquo;Review after import&rdquo;, dated today.
              </Label>
            </div>
          )}

          {blocking.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              Fix what is marked &ldquo;Fix first&rdquo; (in the export or in the CRM), then dry-run the file
              again.
            </p>
          ) : (
            !done && (
              <Button onClick={run} disabled={busy !== null}>
                {busy === "run" ? "Importing…" : "Run the import"}
              </Button>
            )
          )}
        </div>
      )}

      {done && (
        <div className="space-y-2" data-testid="import-done">
          <CountsTable title="Imported" counts={done.counts} />
          {done.fixups_used > 0 && (
            <p className="text-sm text-muted-foreground">
              {done.fixups_used} {done.fixups_used === 1 ? "deal was" : "deals were"} given &ldquo;Review after
              import&rdquo;.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function CountsTable({
  title,
  counts,
}: {
  title: string;
  counts: Partial<Record<ImportObject, ImportCounts>>;
}) {
  const rows = Object.entries(counts) as [ImportObject, ImportCounts][];
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full max-w-xl text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-2 font-medium">Object</th>
              <th className="py-2 text-right font-medium">In file</th>
              <th className="py-2 text-right font-medium">New</th>
              <th className="py-2 text-right font-medium">Updated</th>
              <th className="py-2 text-right font-medium">Unchanged</th>
              <th className="py-2 text-right font-medium">Skipped</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map(([object, c]) => (
              <tr key={object}>
                <td className="py-1.5">{OBJECT_LABELS[object] ?? object}</td>
                <td className="py-1.5 text-right tabular-nums">{c.in_file}</td>
                <td className="py-1.5 text-right tabular-nums">{c.create}</td>
                <td className="py-1.5 text-right tabular-nums">{c.update}</td>
                <td className="py-1.5 text-right tabular-nums">{c.unchanged}</td>
                <td className="py-1.5 text-right tabular-nums">{c.skipped}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
