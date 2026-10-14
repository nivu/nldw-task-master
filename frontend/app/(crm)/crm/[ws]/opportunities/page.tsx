"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { AmountField, type AmountValue } from "@/components/crm/amount-field";
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
import { createCrmRecord, getCrmMembers, searchCompanyChoices } from "@/lib/api/crm";
import type { CrmMember, CrmOpportunity } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import type { NumberFormat } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Opportunities — spec 007 FR-CRM-OPP-01, FR-CRM-PIPE-04, -10, FR-CRM-FX-06,
 * FR-CRM-VIEW-01 to -09, -11, FR-CRM-UI-02, -06.
 *
 * Deals on their saved views (Pipeline, Closing this month, Won and lost
 * this quarter, and any the person saves), as a table or a kanban; a stage
 * changed from a cell or a card goes through the stage form. A deal is
 * opened on a company at Qualified or later, with its next step and date;
 * the server refuses anything else in one sentence, shown as it is. A
 * Viewer sees no amounts.
 */
export default function OpportunitiesPage() {
  const { bootstrap } = useCrm();
  const canEdit = bootstrap.role !== "viewer";
  const [creating, setCreating] = useState(false);

  return (
    <>
      <ObjectList
        kind="opportunity"
        title="Opportunities"
        actions={canEdit && <Button onClick={() => setCreating(true)}>New deal</Button>}
      />
      {canEdit && (
        <NewDealDialog
          open={creating}
          onOpenChange={setCreating}
          workspace={bootstrap.workspace.key}
          me={bootstrap.me.id}
          numberFormat={bootstrap.me.number_format}
        />
      )}
    </>
  );
}

/** A new deal starts at Discovery with its next step and date (FR-CRM-PIPE-07). */
function NewDealDialog({
  open,
  onOpenChange,
  workspace,
  me,
  numberFormat,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  me: string;
  numberFormat: NumberFormat;
}) {
  const router = useRouter();
  const { data: members } = useAsync<CrmMember[]>(
    () => (open ? getCrmMembers(workspace) : Promise.resolve([])),
    [workspace, open]
  );
  const [name, setName] = useState("");
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [owner, setOwner] = useState<string | null>(me);
  const [nextStep, setNextStep] = useState("");
  const [nextStepOn, setNextStepOn] = useState("");
  const [money, setMoney] = useState<AmountValue>({ amount: null, currency: "INR" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setCompanyId(null);
    setOwner(me);
    setNextStep("");
    setNextStepOn("");
    setMoney({ amount: null, currency: "INR" });
    setError(null);
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmOpportunity>(
        workspace,
        "opportunities",
        {
          name: name.trim(),
          company_id: companyId,
          owner_id: owner,
          next_step: nextStep.trim(),
          next_step_on: nextStepOn || null,
          ...(money.amount ? { amount: money.amount, currency: money.currency } : {}),
        },
        false
      );
      router.push(`/crm/${workspace}/opportunities/${(made as CrmOpportunity).id}`);
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
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New deal</DialogTitle>
          <DialogDescription>
            It starts at Discovery. The company must be at Qualified or later.
          </DialogDescription>
        </DialogHeader>
        <form
          id="deal-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create();
          }}
        >
          {error && (
            <div role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {error}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="deal-name">Name</Label>
            <Input
              id="deal-name"
              placeholder="Client - what we sell"
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="deal-company">Company</Label>
            <FieldEditor
              id="deal-company"
              spec={{
                name: "company_id",
                label: "Company",
                type: "relation",
                required: true,
                choices: [],
                search: (q, after) => searchCompanyChoices(workspace, q, after),
              }}
              value={companyId}
              onChange={(value) => setCompanyId((value as string | null) ?? null)}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-[2fr_1fr]">
            <div className="space-y-1.5">
              <Label htmlFor="deal-next">Next step</Label>
              <Input
                id="deal-next"
                maxLength={500}
                value={nextStep}
                onChange={(e) => setNextStep(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="deal-next-on">By</Label>
              <Input
                id="deal-next-on"
                type="date"
                value={nextStepOn}
                onChange={(e) => setNextStepOn(e.target.value)}
                required
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="deal-amount">Amount (optional)</Label>
            <AmountField id="deal-amount" value={money} onChange={setMoney} numberFormat={numberFormat} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="deal-owner">Owner</Label>
            <FieldEditor
              id="deal-owner"
              spec={{
                name: "owner_id",
                label: "Owner",
                type: "relation",
                required: true,
                choices: (members ?? []).map((m) => ({ value: m.user_id, label: m.display_name })),
              }}
              value={owner}
              onChange={(value) => setOwner(value as string | null)}
            />
          </div>
        </form>
        <DialogFooter>
          <Button type="submit" form="deal-form" disabled={busy || !name.trim() || !companyId}>
            {busy ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
