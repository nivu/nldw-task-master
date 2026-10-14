"use client";

import { useState } from "react";

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
import { linkCrmProject, listLinkableProjects } from "@/lib/api/crm";
import type { CrmOpportunity, LinkableProject, ProjectCategory } from "@/lib/api/crm-types";
import { BackendError } from "@/lib/api/backend";
import { errorMessage } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * Link TVA project — spec 007 FR-CRM-TVA-18, -05, FR-CRM-MIG-06, Q-65.
 *
 * For a Labs admin, on a deal with no project: an open deal at Proposal or
 * later is linked to an unlinked, unarchived tentative project, or gets one
 * created; a won deal is linked to an unlinked confirmed project, which
 * keeps its status, revenue, phases and milestones. It is how imported deals
 * reach their projects. Linking an existing project is for a staff TVA
 * manager or admin only: the list of projects is refused (403) to anyone
 * else, who can still create one for an open deal.
 */
export function LinkProjectDialog({
  open,
  onOpenChange,
  workspace,
  deal,
  onLinked,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  deal: CrmOpportunity;
  onLinked: () => void;
}) {
  const won = deal.stage === "closed_won";
  const status = won ? "confirmed" : "tentative";
  const { data: listed, error: listError } = useAsync<LinkableProject[] | "forbidden">(
    () =>
      open
        ? listLinkableProjects(workspace, status).catch((err: unknown) => {
            // Not a TVA manager or admin: no list, and no linking.
            if (err instanceof BackendError && err.status === 403) return "forbidden" as const;
            throw err;
          })
        : Promise.resolve([]),
    [open, workspace, status]
  );
  const projects = Array.isArray(listed) ? listed : null;
  const mayLink = projects !== null;
  const [mode, setMode] = useState<"link" | "create">(won ? "link" : "create");
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState(deal.name);
  const [category, setCategory] = useState<ProjectCategory>(deal.project_category ?? "client");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function link() {
    setBusy(true);
    setError(null);
    try {
      await linkCrmProject(
        workspace,
        deal.id,
        mode === "link" ? { mode, project_id: projectId } : { mode, name: name.trim(), category },
        deal.updated_at
      );
      onLinked();
      onOpenChange(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Link TVA project</DialogTitle>
          <DialogDescription>
            {won
              ? "Link this won deal to its existing confirmed TVA project. The project's status, revenue, phases and milestones stay as they are."
              : "Link this deal to an existing tentative TVA project, or create one for it."}
          </DialogDescription>
        </DialogHeader>
        {listError && <p className="text-sm text-destructive">{listError}</p>}
        {!won && (
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              variant={mode === "create" ? "default" : "outline"}
              onClick={() => setMode("create")}
            >
              Create a project
            </Button>
            <Button
              type="button"
              size="sm"
              variant={mode === "link" ? "default" : "outline"}
              onClick={() => setMode("link")}
            >
              Link an existing one
            </Button>
          </div>
        )}
        <form
          id="link-project-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            link();
          }}
        >
          {mode === "link" && listed === null && !listError && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {mode === "link" &&
            listed !== null &&
            (mayLink ? (
              <div className="space-y-1.5">
                <Label htmlFor="link-project">Project</Label>
                <select
                  id="link-project"
                  required
                  className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                >
                  <option value="">Choose a {status} project…</option>
                  {(projects ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.client ? ` — ${p.client}` : ""}
                    </option>
                  ))}
                </select>
                {projects?.length === 0 && (
                  <p className="text-xs text-muted-foreground">No unlinked {status} project to link.</p>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Ask a TVA manager to link it.</p>
            ))}
          {mode === "create" && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="link-name">Project name</Label>
                <Input
                  id="link-name"
                  required
                  maxLength={200}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="link-category">Category</Label>
                <select
                  id="link-category"
                  className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                  value={category}
                  onChange={(e) => setCategory(e.target.value as ProjectCategory)}
                >
                  <option value="client">Client</option>
                  <option value="poc">POC</option>
                  <option value="product">Product</option>
                </select>
              </div>
            </>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="link-project-form"
            disabled={busy || (mode === "link" && (!mayLink || !projectId))}
          >
            {busy ? "Saving…" : mode === "link" ? "Link project" : "Create project"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
