"use client";

import { Download, Globe, ListPlus, Megaphone, Pencil, Trash2, UserRound, X } from "lucide-react";
import { useState } from "react";

import { cleanValue, FieldEditor } from "@/components/crm/field-editor";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { bulkCrm, exportCrmView, runCrmEnrichment, saveFile, searchCampaignChoices } from "@/lib/api/crm";
import type {
  BulkAction,
  BulkResult,
  CrmBootstrap,
  CrmEnrichmentRun,
  CrmMember,
  ViewObjectKind,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import {
  editorSpec,
  editorValue,
  NATIVE_SELECT,
  patchFor,
  relationSearch,
  TITLE_FIELD,
  type ViewFieldMeta,
} from "@/lib/crm/view-fields";

/**
 * Bulk actions — spec 007 FR-CRM-VIEW-07; contracts §2.5 `POST /crm/bulk`.
 *
 * On the selected rows: set a field, assign an owner, add a task to each,
 * (1b) add companies or people to a campaign, or move them to the Trash.
 * Each record goes through the same rules as one edit would (permissions,
 * pipeline rules, a Labs deal's TVA project, Do not contact), so some may
 * succeed and others not; the result names each one refused and why.
 * (1b, S11b) Export downloads the selected rows as CSV, with the list's
 * columns (FR-CRM-IO-04). (009, phase 2) An Admin's **Enrich selected**
 * queues the selected companies to be read from their own websites in the
 * background (FR-CRMAI-ENR-01, -08); web only.
 */

type Row = Record<string, unknown> & { id: string };
type Mode = "set" | "owner" | "task" | "campaign" | "delete" | "enrich";

const TITLES: Record<Mode, string> = {
  set: "Edit a field",
  owner: "Assign owner",
  task: "Add a task to each",
  campaign: "Add to campaign",
  delete: "Move to Trash",
  enrich: "Enrich selected",
};

export function BulkBar({
  kind,
  selected,
  rows,
  fields,
  bootstrap,
  members,
  columns = [],
  onClear,
  onDone,
}: {
  kind: ViewObjectKind;
  selected: string[];
  /** The rows shown, to name a refused record. */
  rows: Row[];
  /** The list's columns, which an export of the selection keeps. */
  columns?: string[];
  /** The fields that may be set in bulk. */
  fields: ViewFieldMeta[];
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  onClear: () => void;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [field, setField] = useState<string>("");
  const [value, setValue] = useState<unknown>(null);
  const [owner, setOwner] = useState<string>("");
  const [task, setTask] = useState({ title: "", due_on: "", assignee_id: bootstrap.me.id });
  const [people, setPeople] = useState<"unlink" | "delete">("unlink");
  const [campaign, setCampaign] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkResult | null>(null);
  const [enriched, setEnriched] = useState<CrmEnrichmentRun | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const meta = fields.find((f) => f.field === field) ?? fields[0];
  const count = selected.length;
  const ownerField = kind === "task" ? "Assignee" : "Owner";
  const nameOf = (id: string) => {
    const row = rows.find((r) => r.id === id);
    return row ? String(row[TITLE_FIELD[kind]] ?? id).slice(0, 80) : "A record";
  };

  function open(next: Mode) {
    setMode(next);
    setError(null);
    setResult(null);
    setEnriched(null);
    if (next === "set" && meta) {
      setField(meta.field);
      setValue(editorValue(meta, {}));
    }
  }

  function close() {
    setMode(null);
    if (result) {
      onDone();
      if (result.failed.length === 0) onClear();
    }
    setResult(null);
    // Enriching changes no row of the list.
    if (enriched) onClear();
    setEnriched(null);
  }

  async function enrich() {
    setBusy(true);
    setError(null);
    try {
      setEnriched(await runCrmEnrichment(bootstrap.workspace.key, selected));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (mode === "enrich") return enrich();
    let action: BulkAction;
    let values: Record<string, unknown> = {};
    if (mode === "set" && meta) {
      action = "set";
      values = patchFor(meta, cleanValue(editorSpec(meta, {}, bootstrap, members).type, value));
    } else if (mode === "owner") {
      action = "assign_owner";
      values = { owner_id: owner };
    } else if (mode === "campaign") {
      // FR-CRM-CAMP-03: Do not contact and existing members are refused, named.
      action = "add_to_campaign";
      values = { campaign_id: campaign };
    } else if (mode === "task") {
      action = "add_task";
      values = {
        title: task.title.trim(),
        assignee_id: task.assignee_id,
        ...(task.due_on ? { due_on: task.due_on } : {}),
      };
    } else {
      action = "delete";
      // A company's people are unlinked or deleted with it; its deals go
      // to the Trash with it (FR-CRM-DEL-02).
      values = kind === "company" ? { people, deals: "delete" } : {};
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await bulkCrm(bootstrap.workspace.key, kind, selected, action, values));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function exportSelected() {
    setExporting(true);
    setExportError(null);
    try {
      saveFile(await exportCrmView(bootstrap.workspace.key, { object: kind, ids: selected, columns }));
    } catch (err) {
      setExportError(errorMessage(err));
    } finally {
      setExporting(false);
    }
  }

  if (count === 0) return null;
  const canTask = kind === "company" || kind === "person" || kind === "opportunity" || kind === "campaign";
  const canCampaign = kind === "company" || kind === "person";
  // FR-CRMAI-ENR-01, -FLAG-03: Admins, while phase 2 is on.
  const canEnrich = kind === "company" && bootstrap.role === "admin" && bootstrap.ai.phases["2"];
  const ready =
    mode === "owner"
      ? Boolean(owner)
      : mode === "task"
        ? Boolean(task.title.trim() && task.assignee_id)
        : mode === "campaign"
          ? Boolean(campaign)
          : true;

  return (
    <>
      <div
        role="toolbar"
        aria-label="Bulk actions"
        className="sticky bottom-16 z-30 flex flex-wrap items-center gap-1.5 rounded-lg border bg-background p-2 shadow-md md:bottom-4"
      >
        <span className="px-1 text-sm font-medium">{count} selected</span>
        {fields.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => open("set")}>
            <Pencil /> Edit field
          </Button>
        )}
        {kind !== "note" && (
          <Button size="sm" variant="outline" onClick={() => open("owner")}>
            <UserRound /> {ownerField}
          </Button>
        )}
        {canTask && (
          <Button size="sm" variant="outline" onClick={() => open("task")}>
            <ListPlus /> Add task
          </Button>
        )}
        {canCampaign && (
          <Button size="sm" variant="outline" onClick={() => open("campaign")}>
            <Megaphone /> Add to campaign
          </Button>
        )}
        {canEnrich && (
          <Button size="sm" variant="outline" onClick={() => open("enrich")}>
            <Globe /> Enrich selected
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={exportSelected} disabled={exporting}>
          <Download /> {exporting ? "Exporting…" : "Export"}
        </Button>
        <Button size="sm" variant="destructive" onClick={() => open("delete")}>
          <Trash2 /> Delete
        </Button>
        {exportError && (
          <span role="alert" className="text-sm text-destructive">
            {exportError}
          </span>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClear} aria-label="Clear selection">
          <X />
        </Button>
      </div>

      <Dialog open={mode !== null} onOpenChange={(next) => !next && close()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{mode ? TITLES[mode] : ""}</DialogTitle>
            <DialogDescription>
              {count} {count === 1 ? "record" : "records"}. Each one is held to its own rules.
            </DialogDescription>
          </DialogHeader>

          {enriched ? (
            <div className="space-y-1 text-sm" data-testid="enrich-result">
              <p>{enriched.queued} queued to be read from their websites in the background.</p>
              {enriched.skipped_no_website > 0 && <p>{enriched.skipped_no_website} have no website or domain.</p>}
              {enriched.already_enriched > 0 && (
                <p>{enriched.already_enriched} had already been enriched from the same website.</p>
              )}
            </div>
          ) : result ? (
            <div className="space-y-2 text-sm" data-testid="bulk-result">
              <p>
                {result.ok.length} done{result.failed.length ? `, ${result.failed.length} not changed:` : "."}
              </p>
              {result.failed.length > 0 && (
                <ul className="max-h-60 space-y-1 overflow-y-auto rounded-md border p-2">
                  {result.failed.map((f) => (
                    <li key={f.id}>
                      <span className="font-medium">{nameOf(f.id)}</span> — {f.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <form
              id="bulk-form"
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                run();
              }}
            >
              {error && (
                <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                  {error}
                </p>
              )}
              {mode === "set" && meta && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="bulk-field">Field</Label>
                    <select
                      id="bulk-field"
                      className={`${NATIVE_SELECT} w-full`}
                      value={meta.field}
                      onChange={(e) => {
                        const next = fields.find((f) => f.field === e.target.value);
                        if (!next) return;
                        setField(next.field);
                        setValue(editorValue(next, {}));
                      }}
                    >
                      {fields.map((f) => (
                        <option key={f.field} value={f.field}>
                          {f.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="bulk-value">New value</Label>
                    <FieldEditor
                      id="bulk-value"
                      spec={editorSpec(meta, {}, bootstrap, members, relationSearch(meta.field, bootstrap.workspace.key))}
                      value={value}
                      onChange={setValue}
                    />
                  </div>
                  {kind === "opportunity" && meta.field === "stage" && (
                    <p className="text-xs text-muted-foreground">
                      A deal that needs more for that stage is left where it is, and says what it needs.
                    </p>
                  )}
                </>
              )}
              {mode === "owner" && (
                <div className="space-y-1.5">
                  <Label htmlFor="bulk-owner">{ownerField}</Label>
                  <select
                    id="bulk-owner"
                    className={`${NATIVE_SELECT} w-full`}
                    value={owner}
                    onChange={(e) => setOwner(e.target.value)}
                  >
                    <option value="">Choose…</option>
                    {members.map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.display_name}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {mode === "task" && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="bulk-task-title">Title</Label>
                    <Input
                      id="bulk-task-title"
                      maxLength={200}
                      value={task.title}
                      onChange={(e) => setTask({ ...task, title: e.target.value })}
                      required
                    />
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="bulk-task-due">Due</Label>
                      <Input
                        id="bulk-task-due"
                        type="date"
                        value={task.due_on}
                        onChange={(e) => setTask({ ...task, due_on: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="bulk-task-assignee">Assignee</Label>
                      <select
                        id="bulk-task-assignee"
                        className={`${NATIVE_SELECT} w-full`}
                        value={task.assignee_id}
                        onChange={(e) => setTask({ ...task, assignee_id: e.target.value })}
                      >
                        {members.map((m) => (
                          <option key={m.user_id} value={m.user_id}>
                            {m.display_name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </>
              )}
              {mode === "campaign" && (
                <div className="space-y-1.5">
                  <Label htmlFor="bulk-campaign">Campaign</Label>
                  <FieldEditor
                    id="bulk-campaign"
                    spec={{
                      name: "campaign_id",
                      label: "Campaign",
                      type: "relation",
                      choices: [],
                      search: (q, after) => searchCampaignChoices(bootstrap.workspace.key, q, after),
                    }}
                    value={campaign}
                    onChange={(v) => setCampaign((v as string | null) ?? null)}
                  />
                  <p className="text-xs text-muted-foreground">
                    People marked Do not contact are not added, and nobody is added twice.
                  </p>
                </div>
              )}
              {mode === "enrich" && (
                <p className="text-sm">
                  Each company with a website or domain is read from its own site in the background, at most 10 a
                  minute. The values are shown on the company, marked AI; no field you entered is changed.
                </p>
              )}
              {mode === "delete" && (
                <div className="space-y-2 text-sm">
                  <p>
                    They go to the Trash, where an admin can restore them for 90 days. Only an admin, or a
                    member on records they own or created, may delete.
                  </p>
                  {kind === "company" && (
                    <div className="space-y-1.5">
                      <Label htmlFor="bulk-people">Their people</Label>
                      <select
                        id="bulk-people"
                        className={`${NATIVE_SELECT} w-full`}
                        value={people}
                        onChange={(e) => setPeople(e.target.value as "unlink" | "delete")}
                      >
                        <option value="unlink">Keep them, without a company</option>
                        <option value="delete">Delete them too</option>
                      </select>
                      <p className="text-xs text-muted-foreground">Their deals go to the Trash with them.</p>
                    </div>
                  )}
                </div>
              )}
            </form>
          )}

          <DialogFooter>
            {result || enriched ? (
              <Button onClick={close}>Done</Button>
            ) : (
              <>
                <Button variant="outline" onClick={close}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  form="bulk-form"
                  variant={mode === "delete" ? "destructive" : "default"}
                  disabled={busy || !ready}
                >
                  {busy
                    ? "Working…"
                    : mode === "delete"
                      ? `Delete ${count}`
                      : mode === "enrich"
                        ? `Enrich ${count}`
                        : `Apply to ${count}`}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
