"use client";

import { ChevronDown, Download } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { exportCrmView, exportCrmWorkspace, saveFile } from "@/lib/api/crm";
import type { QuerySpec, ViewObjectKind } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * Export — spec 007 FR-CRM-IO-04, -05; contracts §2.10.
 *
 * The list as it is on screen (its filters, search, sorts and columns) as a
 * CSV, for Admins and Members; for Admins also this object, or the whole
 * workspace, as JSON. The file keeps the type and name the server gives it.
 * Past 5 MB the server says so in a sentence, shown here. Viewers are not
 * offered it, and the server refuses them anyway.
 */

/** The objects a workspace export takes one at a time. */
const WORKSPACE_OBJECT: Partial<Record<ViewObjectKind, string>> = {
  company: "companies",
  person: "people",
  opportunity: "opportunities",
  task: "tasks",
  note: "notes",
  campaign: "campaigns",
};

export function ExportMenu({
  workspace,
  kind,
  spec,
  columns,
  title,
  admin,
}: {
  workspace: string;
  kind: ViewObjectKind;
  spec: Pick<QuerySpec, "filters" | "sorts" | "q">;
  columns: string[];
  /** The list's name, for the menu. */
  title: string;
  admin: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(load: () => Promise<{ blob: Blob; filename: string }>) {
    setBusy(true);
    setError(null);
    try {
      saveFile(await load());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const plural = WORKSPACE_OBJECT[kind];
  return (
    <span className="inline-flex items-center gap-2">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button size="sm" variant="outline" disabled={busy} aria-label="Export" />}
        >
          <Download className="size-3.5" />
          {busy ? "Exporting…" : "Export"}
          <ChevronDown className="size-3.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-60">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{title}</DropdownMenuLabel>
            <DropdownMenuItem
              data-testid="export-view-csv"
              onClick={() => run(() => exportCrmView(workspace, { object: kind, spec, columns }))}
            >
              This list as CSV
            </DropdownMenuItem>
          </DropdownMenuGroup>
          {admin && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>Admins</DropdownMenuLabel>
                {plural && (
                  <DropdownMenuItem onClick={() => run(() => exportCrmWorkspace(workspace, plural))}>
                    All {plural} as JSON
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem onClick={() => run(() => exportCrmWorkspace(workspace))}>
                  The whole workspace as JSON
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span role="alert" className="text-sm text-destructive">
          {error}
        </span>
      )}
    </span>
  );
}
