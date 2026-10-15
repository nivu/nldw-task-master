"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { applyCsvImport, dryRunCsvImport, listCsvMappings, saveCsvMapping } from "@/lib/api/crm";
import type { CsvApplied, CsvDryRun, CsvMapping, CsvMode, CsvObject, CsvPreviewRow } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { NATIVE_SELECT } from "@/lib/crm/view-fields";
import { useAsync } from "@/lib/use-async";

const MAX_BYTES = 4 * 1024 * 1024;
/** Rows shown besides those with something to say. */
const PREVIEW_ROWS = 50;
const OBJECTS: { value: CsvObject; label: string; plural: "companies" | "people" | "opportunities" }[] = [
  { value: "company", label: "Companies", plural: "companies" },
  { value: "person", label: "People", plural: "people" },
  { value: "opportunity", label: "Deals", plural: "opportunities" },
];
const ACTION_LABEL: Record<CsvMode, string> = { create: "Create", update: "Update", skip: "Skip" };

/** One CSV record: a cell with a comma, quote, line break or edge space is quoted. */
function csvLine(cells: string[]): string {
  return cells
    .map((c) => (/[",\r\n]|^\s|\s$/.test(c) ? `"${c.replace(/"/g, '""')}"` : c))
    .join(",");
}

/**
 * CSV import — spec 007 FR-CRM-IO-01 to -03, FR-CRM-DUP-02.
 *
 * Upload one object's CSV, map its columns to fields (or pick a saved
 * mapping), check it, fix rows in place, choose what each row does, confirm.
 * The server reads the file (D4b) and holds every rule: a domain or email
 * already in the CRM is caught before anything is written, names alike are
 * warnings, deals open at Discovery with their next step. A row with an
 * error must be fixed or skipped. Edited cells are sent as a new file, so
 * the dry run always checks what the real run will write.
 */
export function CsvImportWizard({ workspace, onImported }: { workspace: string; onImported: () => void }) {
  const [object, setObject] = useState<CsvObject>("company");
  const [filename, setFilename] = useState("");
  const [content, setContent] = useState<string | null>(null);
  const [tooBig, setTooBig] = useState(false);
  const [mappingId, setMappingId] = useState("");
  const [mapping, setMapping] = useState<CsvMapping | null>(null);
  const [mode, setMode] = useState<CsvMode>("skip");
  const [actions, setActions] = useState<Record<string, CsvMode>>({});
  const [cells, setCells] = useState<Record<number, string[]>>({});
  const [report, setReport] = useState<CsvDryRun | null>(null);
  const [checked, setChecked] = useState<string | null>(null);
  const [checkedMapping, setCheckedMapping] = useState<string | null>(null);
  const [fixup, setFixup] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [done, setDone] = useState<CsvApplied | null>(null);
  const [busy, setBusy] = useState<"dry" | "run" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mappings = useAsync(() => listCsvMappings(workspace, object), [workspace, object, saved]);

  function reset() {
    setReport(null);
    setMapping(null);
    setActions({});
    setCells({});
    setChecked(null);
    setDone(null);
    setError(null);
  }

  async function choose(file: File | null) {
    reset();
    setContent(null);
    setTooBig(false);
    if (!file) return;
    setFilename(file.name);
    if (file.size > MAX_BYTES) {
      setTooBig(true);
      return;
    }
    setContent(await file.text());
  }

  /** The file as it will be sent: with the cells edited here. */
  function current(): string {
    if (!report || !content || Object.keys(cells).length === 0) return content ?? "";
    const lines = [csvLine(report.headers)];
    for (const row of report.rows) lines.push(csvLine(cells[row.row] ?? row.cells));
    return `${lines.join("\r\n")}\r\n`;
  }

  async function check() {
    const text = current();
    if (!text) return;
    setBusy("dry");
    setError(null);
    setDone(null);
    try {
      const next = await dryRunCsvImport(workspace, {
        content: text,
        filename: filename || "import.csv",
        object,
        ...(mapping ? { mapping } : mappingId ? { mapping_id: mappingId } : {}),
        mode,
        actions,
      });
      if (text !== content) setContent(text);
      setCells({});
      setReport(next);
      setMapping(next.mapping);
      setChecked(text);
      setCheckedMapping(JSON.stringify(next.mapping));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function run() {
    if (!report || checked === null) return;
    setBusy("run");
    setError(null);
    try {
      setDone(
        await applyCsvImport(workspace, report.run_id, {
          content: checked,
          filename: filename || "import.csv",
          object,
          mode,
          actions,
          fixup_next_step: fixup,
        })
      );
      onImported();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function saveMapping() {
    if (!mapping || !saveName.trim()) return;
    setBusy("save");
    setError(null);
    try {
      const made = await saveCsvMapping(workspace, object, saveName.trim(), mapping);
      setSaved(made.id);
      setMappingId(made.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  // The real run uses the file and the columns the last check read.
  const stale =
    report !== null &&
    (Object.keys(cells).length > 0 || current() !== checked || JSON.stringify(mapping) !== checkedMapping);
  const choices = (row: CsvPreviewRow): CsvMode[] => (row.match ? ["update", "create", "skip"] : ["create", "skip"]);
  const blocking = (report?.rows ?? []).filter(
    (r) => r.errors.length > 0 && (actions[String(r.row)] ?? r.action) !== "skip"
  );
  const fixable = report?.fixup_candidates ?? 0;
  const shown = (report?.rows ?? []).filter(
    (r, i) => i < PREVIEW_ROWS || r.errors.length > 0 || r.warnings.length > 0
  );
  const plural = OBJECTS.find((o) => o.value === object)?.plural ?? "companies";
  const counts = report?.counts[plural];

  return (
    <section className="space-y-4" aria-label="CSV import" data-testid="csv-import">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <Label htmlFor="csv-object">The file holds</Label>
          <select
            id="csv-object"
            className={NATIVE_SELECT}
            value={object}
            onChange={(e) => {
              setObject(e.target.value as CsvObject);
              setMappingId("");
              reset();
            }}
          >
            {OBJECTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="csv-file">CSV file (at most 4 MB)</Label>
          <Input
            id="csv-file"
            type="file"
            accept=".csv,text/csv"
            className="h-8 w-72"
            onChange={(e) => void choose(e.target.files?.[0] ?? null)}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="csv-mapping">Mapping</Label>
          <select
            id="csv-mapping"
            className={NATIVE_SELECT}
            value={mappingId}
            onChange={(e) => {
              setMappingId(e.target.value);
              setMapping(null);
              setReport(null);
            }}
          >
            <option value="">By the column names</option>
            {(mappings.data ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="csv-mode">A row matching a record</Label>
          <select
            id="csv-mode"
            className={NATIVE_SELECT}
            value={mode}
            onChange={(e) => setMode(e.target.value as CsvMode)}
          >
            <option value="skip">Leaves it alone</option>
            <option value="update">Updates it</option>
          </select>
        </div>
        <Button size="sm" onClick={check} disabled={!content || busy !== null}>
          {busy === "dry" ? "Checking…" : report ? "Check again" : "Check the file"}
        </Button>
      </div>
      <p className="max-w-2xl text-xs text-muted-foreground">
        A row matches a record by an ID column, a company&apos;s domain or a person&apos;s email. Options and owners
        are read by their names, a company by name or domain, a person by email or name. An empty cell never clears
        a field. Nothing is written until you confirm.
      </p>

      {tooBig && <p role="alert" className="text-sm text-destructive">The file is larger than 4 MB; split it.</p>}
      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {report && mapping && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Columns</h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {report.headers.map((header) => (
              <label key={header} className="flex items-center gap-2 text-sm">
                <span className="w-32 truncate" title={header}>
                  {header}
                </span>
                <select
                  className={`${NATIVE_SELECT} flex-1`}
                  value={mapping[header] ?? ""}
                  aria-label={`Field for ${header}`}
                  onChange={(e) => setMapping({ ...mapping, [header]: e.target.value || null })}
                >
                  <option value="">Not imported</option>
                  {report.fields.map((f) => (
                    <option key={f.field} value={f.field}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="h-8 w-56"
              placeholder="Name this mapping"
              value={saveName}
              maxLength={80}
              onChange={(e) => setSaveName(e.target.value)}
              aria-label="Mapping name"
            />
            <Button size="sm" variant="outline" onClick={saveMapping} disabled={!saveName.trim() || busy !== null}>
              Save mapping
            </Button>
          </div>
        </div>
      )}

      {report && counts && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2 text-sm" data-testid="csv-counts">
            <Badge variant="secondary">{counts.in_file} rows</Badge>
            <Badge variant="secondary">{counts.create} to create</Badge>
            <Badge variant="secondary">{counts.update} to update</Badge>
            <Badge variant="secondary">{counts.unchanged} unchanged</Badge>
            <Badge variant="secondary">{counts.skipped} skipped</Badge>
          </div>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-2 font-medium">Row</th>
                  <th className="p-2 font-medium">Does</th>
                  <th className="p-2 font-medium">Check</th>
                  {report.headers.map((h) => (
                    <th key={h} className="p-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {shown.map((row) => {
                  const action = actions[String(row.row)] ?? row.action;
                  const values = cells[row.row] ?? row.cells;
                  return (
                    <tr key={row.row} className={row.errors.length ? "bg-destructive/5" : undefined}>
                      <td className="p-2 tabular-nums">{row.row}</td>
                      <td className="p-2">
                        <select
                          className={NATIVE_SELECT}
                          aria-label={`What row ${row.row} does`}
                          value={action}
                          onChange={(e) => setActions({ ...actions, [String(row.row)]: e.target.value as CsvMode })}
                        >
                          {choices(row).map((c) => (
                            <option key={c} value={c}>
                              {ACTION_LABEL[c]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="min-w-48 p-2 text-xs">
                        {row.match && <p>Matches {row.match.name} (by {row.match.by})</p>}
                        {row.unchanged && <p className="text-muted-foreground">No changes</p>}
                        {row.errors.map((e) => (
                          <p key={e} className="text-destructive">
                            {e}
                          </p>
                        ))}
                        {row.warnings.map((w) => (
                          <p key={w} className="text-amber-700 dark:text-amber-400">
                            {w}
                          </p>
                        ))}
                      </td>
                      {values.map((value, i) => (
                        <td key={i} className="p-1">
                          <Input
                            className="h-7 min-w-28"
                            value={value}
                            aria-label={`Row ${row.row}, ${report.headers[i]}`}
                            onChange={(e) =>
                              setCells({
                                ...cells,
                                [row.row]: values.map((v, j) => (j === i ? e.target.value : v)),
                              })
                            }
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {report.rows.length > shown.length && (
            <p className="text-xs text-muted-foreground">
              {report.rows.length - shown.length} more rows have nothing to fix and are not shown.
            </p>
          )}
          {fixable > 0 && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={fixup} onCheckedChange={(v) => setFixup(Boolean(v))} />
              Give the {fixable} {fixable === 1 ? "deal" : "deals"} with no next step &ldquo;Review after import&rdquo;,
              dated today
            </label>
          )}
          {stale && (
            <p className="text-sm text-muted-foreground">You changed the file or its columns; check it again first.</p>
          )}
          {blocking.length > 0 && !stale && (
            <p className="text-sm text-destructive">
              Fix or skip the {blocking.length} {blocking.length === 1 ? "row" : "rows"} with problems, then check
              again.
            </p>
          )}
          <Button
            onClick={run}
            disabled={busy !== null || stale || (blocking.length > 0 && !(fixup && blocking.length <= fixable))}
          >
            {busy === "run" ? "Importing…" : "Confirm import"}
          </Button>
        </div>
      )}

      {done && (
        <div role="status" className="rounded-md bg-muted p-3 text-sm" data-testid="csv-done">
          <p>
            {done.created} created, {done.updated} updated
            {done.fixups_used ? `, ${done.fixups_used} given a review next step` : ""}.
          </p>
          {done.failed.length > 0 && (
            <ul className="mt-2 space-y-1">
              {done.failed.map((f) => (
                <li key={f.row} className="text-destructive">
                  Row {f.row}: {f.detail}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
