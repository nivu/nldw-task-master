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
import type { ProjectChoice } from "@/lib/api/crm";
import type { ProjectEffect } from "@/lib/api/crm-types";

/**
 * A project name clash at Proposal — spec 007 FR-CRM-TVA-05, D14, Q-42.
 *
 * The deal's tentative project would take a name another project already
 * has (archived ones included). Two ways on: give the new project another
 * name (or rename the deal itself on its page), or link the deal to that
 * project. Linking is offered only when the server says this person may
 * (`clash.linkable`: a staff TVA manager or admin, and the project is
 * unlinked, tentative and not archived); anyone else is told to ask a TVA
 * manager, since a link lets a later Closed Lost archive that project and
 * release its people.
 */
export function NameClashDialog({
  open,
  onOpenChange,
  clash,
  dealName,
  onChoose,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clash: NonNullable<ProjectEffect["clash"]>;
  dealName: string;
  onChoose: (choice: ProjectChoice) => void;
}) {
  const [name, setName] = useState(`${dealName} (2)`);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>A project called “{clash.name}” already exists</DialogTitle>
          <DialogDescription>
            Moving to Proposal creates a tentative TVA project named after the deal. Give the new project
            another name, rename the deal, or link the deal to the existing project.
          </DialogDescription>
        </DialogHeader>
        <form
          id="clash-form"
          className="space-y-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) onChoose({ project_name: name.trim() });
          }}
        >
          <Label htmlFor="clash-name">New project name</Label>
          <Input id="clash-name" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </form>
        {clash.linkable ? (
          <p className="text-sm text-muted-foreground">
            Or link this deal to “{clash.name}”: the deal will then control its status and probability, and
            losing the deal archives it and releases its people.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Linking to “{clash.name}” is not possible for you. Ask a TVA manager to link it.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          {clash.linkable && (
            <Button variant="outline" onClick={() => onChoose({ link_project_id: clash.project_id })}>
              Link to “{clash.name}”
            </Button>
          )}
          <Button type="submit" form="clash-form" disabled={!name.trim()}>
            Use this name
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
