"use client";

import {
  Building2,
  Calendar,
  CalendarDays,
  ChevronDown,
  Filter,
  Handshake,
  Kanban as KanbanIcon,
  ListChecks,
  Star,
  Table2,
  Trophy,
  User,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CrmSavedView } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { NATIVE_SELECT } from "@/lib/crm/view-fields";
import { cn } from "@/lib/utils";

/**
 * Saved views — spec 007 FR-CRM-VIEW-01, -05, FR-CRM-UI-02; Q-37.
 *
 * The object's views as tabs, the shared ones first in their order, then the
 * person's own; Table or Kanban for what is on screen; and the view's menu:
 * save the changes, save as a new view (a name, an icon, private or shared,
 * the list's default), move it earlier or later, pin it to the sidebar, and
 * hide a seeded view or delete one's own. A Viewer saves private views only;
 * Admins and Members manage shared ones. The server decides; a refusal is
 * shown as its sentence.
 */

export const VIEW_ICONS: Record<string, LucideIcon> = {
  "building-2": Building2,
  filter: Filter,
  handshake: Handshake,
  user: User,
  kanban: KanbanIcon,
  calendar: Calendar,
  trophy: Trophy,
  "list-checks": ListChecks,
  "calendar-days": CalendarDays,
  table: Table2,
  star: Star,
};

export function ViewIcon({ name, className }: { name: string | null | undefined; className?: string }) {
  const Icon = (name && VIEW_ICONS[name]) || Table2;
  return <Icon aria-hidden className={cn("size-4 shrink-0", className)} />;
}

export interface SaveAs {
  name: string;
  icon: string;
  visibility: "private" | "shared";
  is_default: boolean;
}

export function ViewSwitcher({
  views,
  current,
  onSelect,
  kind,
  onKindChange,
  canKanban,
  dirty,
  canShare,
  pinned,
  onSave,
  onSaveAs,
  onMove,
  onDefault,
  onRemove,
  onPin,
  onReset,
}: {
  views: CrmSavedView[];
  current: CrmSavedView | null;
  onSelect: (view: CrmSavedView) => void;
  kind: "table" | "kanban";
  onKindChange: (kind: "table" | "kanban") => void;
  /** The object has a select field to group cards by. */
  canKanban: boolean;
  /** What is on screen differs from the saved view. */
  dirty: boolean;
  /** Admins and Members (Q-37). */
  canShare: boolean;
  pinned: boolean;
  onSave: () => Promise<void>;
  onSaveAs: (view: SaveAs) => Promise<void>;
  onMove: (view: CrmSavedView, by: -1 | 1) => Promise<void>;
  onDefault: (view: CrmSavedView) => Promise<void>;
  onRemove: (view: CrmSavedView) => Promise<void>;
  onPin: () => Promise<void>;
  onReset: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const index = current ? views.findIndex((v) => v.id === current.id) : -1;
  // A seeded view keeps its kind (contracts §2.7): a change of kind is a new view.
  const kindLocked = Boolean(current?.system_key) && current?.kind !== kind;
  const canSave = Boolean(current?.can_edit) && dirty && !kindLocked;

  async function act(run: () => Promise<void>) {
    setError(null);
    try {
      await run();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Views" className="relative flex max-w-full gap-1 overflow-x-auto">
          {views.map((view) => (
            <button
              key={view.id}
              type="button"
              role="tab"
              aria-selected={view.id === current?.id}
              onClick={() => onSelect(view)}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-sm",
                view.id === current?.id
                  ? "bg-muted font-medium text-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              )}
            >
              <ViewIcon name={view.icon} />
              {view.name}
              {view.visibility === "private" && <span className="sr-only"> (private)</span>}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <div className="flex rounded-md border p-0.5" role="group" aria-label="Layout">
            {(canKanban ? (["table", "kanban"] as const) : (["table"] as const)).map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={kind === k}
                onClick={() => onKindChange(k)}
                className={cn(
                  "flex items-center gap-1 rounded px-2 py-0.5 text-sm",
                  kind === k ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {k === "table" ? <Table2 className="size-3.5" /> : <KanbanIcon className="size-3.5" />}
                {k === "table" ? "Table" : "Kanban"}
              </button>
            ))}
          </div>
          {dirty && (
            <Button size="sm" variant="ghost" onClick={onReset}>
              Reset
            </Button>
          )}
          {canSave && (
            <Button size="sm" onClick={() => act(onSave)}>
              Save view
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="sm" variant="outline" aria-label="View options" />}>
              View
              <ChevronDown className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-52">
              <DropdownMenuGroup>
                <DropdownMenuLabel>{current?.name ?? "This list"}</DropdownMenuLabel>
                {canSave && <DropdownMenuItem onClick={() => act(onSave)}>Save changes</DropdownMenuItem>}
                <DropdownMenuItem onClick={() => setSaving(true)}>Save as new view…</DropdownMenuItem>
                {current && (
                  <DropdownMenuItem onClick={() => act(onPin)}>
                    {pinned ? "Unpin from sidebar" : "Pin to sidebar"}
                  </DropdownMenuItem>
                )}
              </DropdownMenuGroup>
              {current?.can_edit && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    {current.visibility === "shared" && !current.is_default && (
                      <DropdownMenuItem onClick={() => act(() => onDefault(current))}>
                        Make the default view
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem disabled={index <= 0} onClick={() => act(() => onMove(current, -1))}>
                      Move earlier
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={index < 0 || index >= views.length - 1}
                      onClick={() => act(() => onMove(current, 1))}
                    >
                      Move later
                    </DropdownMenuItem>
                    <DropdownMenuItem variant="destructive" onClick={() => act(() => onRemove(current))}>
                      {current.system_key ? "Hide this view" : "Delete this view"}
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {kindLocked && current?.can_edit && (
        <p className="text-xs text-muted-foreground">
          A seeded view keeps its layout; save a copy to keep this one.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <SaveAsDialog
        open={saving}
        onOpenChange={setSaving}
        canShare={canShare}
        suggested={current ? `${current.name} (copy)` : ""}
        icon={current?.icon ?? "table"}
        onSave={onSaveAs}
      />
    </div>
  );
}

function SaveAsDialog({
  open,
  onOpenChange,
  canShare,
  suggested,
  icon: suggestedIcon,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canShare: boolean;
  suggested: string;
  icon: string;
  onSave: (view: SaveAs) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("table");
  const [visibility, setVisibility] = useState<"private" | "shared">("private");
  const [isDefault, setIsDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave({ name: name.trim(), icon, visibility, is_default: visibility === "shared" && isDefault });
      onOpenChange(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setName(suggested);
          setIcon(suggestedIcon);
          setVisibility("private");
          setIsDefault(false);
          setError(null);
        }
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Save as new view</DialogTitle>
          <DialogDescription>Its filters, sorts, grouping, columns and totals, as they are now.</DialogDescription>
        </DialogHeader>
        <form
          id="save-view-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="view-name">Name</Label>
            <Input id="view-name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="view-icon">Icon</Label>
            <div className="flex items-center gap-2">
              <ViewIcon name={icon} />
              <select
                id="view-icon"
                className={`${NATIVE_SELECT} flex-1`}
                value={icon}
                onChange={(e) => setIcon(e.target.value)}
              >
                {Object.keys(VIEW_ICONS).map((key) => (
                  <option key={key} value={key}>
                    {key.replace(/-/g, " ")}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium">Who sees it</legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="view-visibility"
                checked={visibility === "private"}
                onChange={() => setVisibility("private")}
              />
              Only me
            </label>
            <label className={cn("flex items-center gap-2 text-sm", !canShare && "text-muted-foreground")}>
              <input
                type="radio"
                name="view-visibility"
                disabled={!canShare}
                checked={visibility === "shared"}
                onChange={() => setVisibility("shared")}
              />
              Everyone in this workspace
            </label>
            {!canShare && <p className="text-xs text-muted-foreground">Viewers save private views only.</p>}
          </fieldset>
          {visibility === "shared" && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={isDefault} onCheckedChange={(checked) => setIsDefault(Boolean(checked))} />
              Make it the list&apos;s default
            </label>
          )}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="save-view-form" disabled={busy || !name.trim()}>
            {busy ? "Saving…" : "Save view"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
