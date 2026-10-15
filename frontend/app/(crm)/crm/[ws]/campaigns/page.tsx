"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { FieldEditor } from "@/components/crm/field-editor";
import { ObjectList } from "@/components/crm/object-list";
import { useCrm } from "@/components/crm/shell";
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
import { createCrmRecord } from "@/lib/api/crm";
import type { CrmBootstrap, CrmCampaign } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * Campaigns — spec 007 FR-CRM-CAMP-01, -05, FR-CRM-UI-02 (1b),
 * FR-CRM-VIEW-01 to -09.
 *
 * The workspace's campaigns as a table or kanban, on any view the person
 * saves (none is seeded: the campaign's own page holds its member board).
 * Sends happen outside the CRM; what came of them is recorded on members
 * (FR-CRM-CAMP-07). A Viewer sees no budget.
 */
export default function CampaignsPage() {
  const { bootstrap } = useCrm();
  const canEdit = bootstrap.role !== "viewer";
  const [creating, setCreating] = useState(false);

  return (
    <>
      <ObjectList
        kind="campaign"
        title="Campaigns"
        actions={canEdit && <Button onClick={() => setCreating(true)}>New campaign</Button>}
      />
      {canEdit && <NewCampaignDialog open={creating} onOpenChange={setCreating} bootstrap={bootstrap} />}
    </>
  );
}

function NewCampaignDialog({
  open,
  onOpenChange,
  bootstrap,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bootstrap: CrmBootstrap;
}) {
  const router = useRouter();
  const ws = bootstrap.workspace.key;
  const [name, setName] = useState("");
  const [type, setType] = useState<string | null>(null);
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const types = (bootstrap.lists["campaign.type"] ?? [])
    .filter((o) => o.is_active)
    .map((o) => ({ value: o.id, label: o.label }));
  const planned = (bootstrap.lists["campaign.status"] ?? []).find((o) => o.is_default && o.is_active);

  function close(next: boolean) {
    if (!next) {
      setName("");
      setType(null);
      setStartsOn("");
      setEndsOn("");
      setError(null);
    }
    onOpenChange(next);
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmCampaign>(
        ws,
        "campaigns",
        {
          name: name.trim(),
          ...(type ? { type_id: type } : {}),
          ...(planned ? { status_id: planned.id } : {}),
          ...(startsOn ? { starts_on: startsOn } : {}),
          ...(endsOn ? { ends_on: endsOn } : {}),
        },
        false
      );
      if ("id" in made) router.push(`/crm/${ws}/campaigns/${made.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New campaign</DialogTitle>
          <DialogDescription>Add its budget, goal and members on its page.</DialogDescription>
        </DialogHeader>
        <form
          id="campaign-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create();
          }}
        >
          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="campaign-name">Name</Label>
            <Input
              id="campaign-name"
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="campaign-type">Type</Label>
            <FieldEditor
              id="campaign-type"
              spec={{ name: "type_id", label: "Type", type: "select", choices: types }}
              value={type}
              onChange={(value) => setType((value as string | null) ?? null)}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="campaign-starts">Start date</Label>
              <Input id="campaign-starts" type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="campaign-ends">End date</Label>
              <Input id="campaign-ends" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
            </div>
          </div>
        </form>
        <DialogFooter>
          <Button type="submit" form="campaign-form" disabled={busy || !name.trim()}>
            {busy ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
