"use client";

import { ChevronDown, ChevronRight, Pencil, Star } from "lucide-react";
import { createContext, useContext, useRef, useState } from "react";

import {
  cleanValue,
  FieldEditor,
  FieldValue,
  isEmpty,
  type FieldSpec,
} from "@/components/crm/field-editor";
import { FlagChip } from "@/components/crm/flag-dot";
import { useCrm } from "@/components/crm/shell";
import { TaskPanel } from "@/components/tasks/task-panel";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { addCrmFavourite, removeCrmFavourite } from "@/lib/api/crm";
import type { RecordFlags, ViewObjectKind } from "@/lib/api/crm-types";
import { errorMessage, getMe } from "@/lib/api/portal";
import type { CrmTimezone } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * The record page frame — spec 007 FR-CRM-REC-01 to -04, FR-CRM-UI-06.
 *
 * A header with the name, key fields as chips and the actions; a left panel
 * with every field, grouped and inline-editable, empty ones folded away; and
 * the tabs Timeline, Tasks, Notes, Files, Emails (1b, companies, people and
 * deals) and Related. On a phone the panel
 * stacks above the tabs. Companies, people and deals use it; campaigns reuse
 * it as they arrive. The Move stage action is a deal's, passed in `actions`.
 * A record can be pinned to the sidebar's favourites (FR-CRM-UI-02).
 *
 * Spec 008 FR-TASK-VIEW-03: with My Tasks switched on for the reader, a task
 * in the Tasks tab opens in the shared task panel
 * (`components/tasks/task-panel.tsx`), the one My Tasks uses; the tab's list
 * asks for it through `useOpenTask`. Below phase 1 the tab is as `007` built
 * it.
 */

/** Opens a task of the Tasks tab in the shared task panel; `onChanged` runs
 *  after a change there, so the tab reloads. */
type OpenTask = (taskId: string, onChanged: () => void) => void;

const OpenTaskContext = createContext<OpenTask | null>(null);

/** Inside a record page's tabs, with tasks switched on: how to open a task
 *  in the shared task panel. Null elsewhere. */
export function useOpenTask(): OpenTask | null {
  return useContext(OpenTaskContext);
}

export interface FieldGroup {
  title: string;
  fields: FieldSpec[];
}

function FieldRow({
  spec,
  value,
  canEdit,
  timeZone,
  onSave,
}: {
  spec: FieldSpec;
  value: unknown;
  canEdit: boolean;
  timeZone: CrmTimezone;
  onSave: (name: string, value: unknown) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<unknown>(value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editable = canEdit && !spec.readOnly;
  const id = `field-${spec.name}`;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave(spec.name, cleanValue(spec.type, draft));
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <div className="space-y-2 rounded-md bg-muted/40 p-2">
        <label htmlFor={id} className="text-xs text-muted-foreground">
          {spec.label}
        </label>
        <FieldEditor id={id} spec={spec} value={draft} onChange={setDraft} />
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setDraft(value);
              setError(null);
              setEditing(false);
            }}
          >
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="group grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] items-start gap-2 rounded-md px-2 py-1.5 hover:bg-muted/40">
      <span className="pt-0.5 text-xs text-muted-foreground">{spec.label}</span>
      <div className="flex min-w-0 items-start justify-between gap-1 text-sm">
        <div className="min-w-0 flex-1">
          <FieldValue spec={spec} value={value} timeZone={timeZone} />
        </div>
        {editable && (
          <button
            type="button"
            aria-label={`Edit ${spec.label}`}
            className="shrink-0 rounded p-0.5 text-muted-foreground opacity-60 hover:opacity-100 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
            onClick={() => {
              setDraft(value);
              setEditing(true);
            }}
          >
            <Pencil className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

function FieldPanel({
  groups,
  values,
  canEdit,
  timeZone,
  onSave,
}: {
  groups: FieldGroup[];
  values: Record<string, unknown>;
  canEdit: boolean;
  timeZone: CrmTimezone;
  onSave: (name: string, value: unknown) => Promise<void>;
}) {
  const [showEmpty, setShowEmpty] = useState(false);
  const empty = groups.flatMap((g) => g.fields).filter((f) => isEmpty(values[f.name])).length;

  return (
    <div className="space-y-4">
      {groups.map((group) => {
        const fields = group.fields.filter((f) => showEmpty || !isEmpty(values[f.name]));
        if (fields.length === 0) return null;
        return (
          <section key={group.title} className="space-y-0.5">
            <h3 className="px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {group.title}
            </h3>
            {fields.map((spec) => (
              <FieldRow
                key={spec.name}
                spec={spec}
                value={values[spec.name]}
                canEdit={canEdit}
                timeZone={timeZone}
                onSave={onSave}
              />
            ))}
          </section>
        );
      })}
      {empty > 0 && (
        <button
          type="button"
          className="flex items-center gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
          aria-expanded={showEmpty}
          onClick={() => setShowEmpty((s) => !s)}
        >
          {showEmpty ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {showEmpty ? "Hide empty fields" : `Show ${empty} empty field${empty === 1 ? "" : "s"}`}
        </button>
      )}
    </div>
  );
}

/** Pin or unpin a record in the sidebar's favourites (FR-CRM-UI-02). */
function PinButton({ recordType, id }: { recordType: ViewObjectKind; id: string }) {
  const { bootstrap, favourites, reloadFavourites } = useCrm();
  const [busy, setBusy] = useState(false);
  const pin = favourites.find((f) => f.target_type === "record" && f.target_id === id);

  async function toggle() {
    setBusy(true);
    try {
      const ws = bootstrap.workspace.key;
      if (pin) await removeCrmFavourite(ws, pin.id);
      else await addCrmFavourite(ws, { target_type: "record", target_id: id, record_type: recordType });
      reloadFavourites();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      variant="outline"
      size="icon"
      aria-pressed={Boolean(pin)}
      aria-label={pin ? "Unpin from favourites" : "Pin to favourites"}
      title={pin ? "Unpin from favourites" : "Pin to favourites"}
      disabled={busy}
      onClick={toggle}
    >
      <Star className={pin ? "size-4 fill-amber-400 text-amber-500" : "size-4"} />
    </Button>
  );
}

export function RecordPage({
  title,
  subtitle,
  chips,
  actions,
  notice,
  groups,
  values,
  canEdit,
  timeZone,
  onSave,
  timeline,
  tasks,
  notes,
  files,
  emails,
  related,
  pin,
  flags,
}: {
  title: string;
  subtitle?: React.ReactNode;
  chips: React.ReactNode[];
  actions: React.ReactNode;
  notice?: React.ReactNode;
  groups: FieldGroup[];
  values: Record<string, unknown>;
  canEdit: boolean;
  timeZone: CrmTimezone;
  onSave: (name: string, value: unknown) => Promise<void>;
  timeline: React.ReactNode;
  tasks?: React.ReactNode;
  notes?: React.ReactNode;
  files: React.ReactNode;
  /** 1b: the threads Gmail sync stored (FR-CRM-REC-03); no tab without it. */
  emails?: React.ReactNode;
  related: React.ReactNode;
  /** The record, to pin it to the sidebar. */
  pin?: { recordType: ViewObjectKind; id: string };
  /** 1b: its follow-up or deal-health flags, as a header chip (FR-CRM-REC-01). */
  flags?: RecordFlags & { record_type: string };
}) {
  const [tab, setTab] = useState("timeline");
  const { bootstrap } = useCrm();
  const me = useAsync(getMe, []);
  const level = me.data?.tasks.level ?? 0;
  const [openTask, setOpenTask] = useState<string | null>(null);
  const afterChange = useRef<() => void>(() => {});
  const opener: OpenTask = (taskId, onChanged) => {
    afterChange.current = onChanged;
    setOpenTask(taskId);
  };

  return (
    <div className="space-y-4">
      <header className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h1 className="break-words font-heading text-xl font-semibold">{title}</h1>
            {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {pin && <PinButton recordType={pin.recordType} id={pin.id} />}
            {actions}
          </div>
        </div>
        {(chips.length > 0 || (flags && flags.flag_level !== "none")) && (
          <div className="flex flex-wrap gap-1.5">
            {chips}
            {flags && (
              <FlagChip
                level={flags.flag_level}
                flags={flags.flags}
                computedAt={flags.flags_computed_at}
                recordType={flags.record_type}
                timeZone={timeZone}
              />
            )}
          </div>
        )}
        {notice}
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <aside className="min-w-0 lg:border-r lg:pr-4">
          <FieldPanel
            groups={groups}
            values={values}
            canEdit={canEdit}
            timeZone={timeZone}
            onSave={onSave}
          />
        </aside>
        <div className="min-w-0">
          <OpenTaskContext.Provider value={level >= 1 ? opener : null}>
            <Tabs value={tab} onValueChange={(value) => setTab(String(value))}>
              <TabsList className="max-w-full overflow-x-auto">
                <TabsTrigger value="timeline">Timeline</TabsTrigger>
                <TabsTrigger value="tasks">Tasks</TabsTrigger>
                <TabsTrigger value="notes">Notes</TabsTrigger>
                <TabsTrigger value="files">Files</TabsTrigger>
                {emails && <TabsTrigger value="emails">Emails</TabsTrigger>}
                <TabsTrigger value="related">Related</TabsTrigger>
              </TabsList>
              <TabsContent value="timeline" className="pt-4">
                {timeline}
              </TabsContent>
              <TabsContent value="tasks" className="pt-4">
                {tasks ?? <p className="text-sm text-muted-foreground">No tasks on this record.</p>}
              </TabsContent>
              <TabsContent value="notes" className="pt-4">
                {notes ?? <p className="text-sm text-muted-foreground">No notes on this record.</p>}
              </TabsContent>
              <TabsContent value="files" className="pt-4">
                {files}
              </TabsContent>
              {emails && (
                <TabsContent value="emails" className="pt-4">
                  {emails}
                </TabsContent>
              )}
              <TabsContent value="related" className="pt-4">
                {related}
              </TabsContent>
            </Tabs>
          </OpenTaskContext.Provider>
          {level >= 1 && (
            <TaskPanel
              taskId={openTask}
              onOpenChange={(o) => !o && setOpenTask(null)}
              onChanged={() => afterChange.current()}
              onOpenTask={setOpenTask}
              meId={bootstrap.me.id}
              delegation={level >= 2}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/** A key field in the header (FR-CRM-REC-01). */
export function Chip({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </span>
  );
}
