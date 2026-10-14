"use client";

import { useState } from "react";

import { AmountField } from "@/components/crm/amount-field";
import { FieldEditor } from "@/components/crm/field-editor";
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
import { moveCrmStage, previewCrmStage, searchPersonChoices } from "@/lib/api/crm";
import type {
  CrmCurrency,
  CrmOpportunity,
  StageChanges,
  StageKey,
  StageMoveResult,
  StagePreview,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import type { NumberFormat } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Move a deal — spec 007 FR-CRM-PIPE-07 to -14, contracts §2.6.
 *
 * The dialog asks the server first (`…/stage/preview`, which writes nothing)
 * and then asks the person for exactly the fields the move lacks, nothing
 * more. What the move will do — the probability, the company's lead stage —
 * is said before it happens. A refusal is the server's one sentence, shown
 * verbatim. Closed Lost has its own dialog (`lost-dialog.tsx`).
 */

export const STAGE_ORDER: StageKey[] = [
  "discovery",
  "qualification",
  "proposal",
  "negotiation",
  "closed_won",
  "closed_lost",
];

/** The fields this dialog can ask for, in the order a refusal names them. */
const ASKABLE = [
  "point_of_contact_id",
  "money",
  "expected_close_on",
  "proposal_sent_on",
  "next_step",
  "next_step_on",
] as const;

const LABELS: Record<string, string> = {
  point_of_contact_id: "Point of contact",
  money: "Amount",
  expected_close_on: "Expected close date",
  proposal_sent_on: "Proposal sent on",
  next_step: "Next step",
  next_step_on: "Next step date",
};

function asked(missing: StagePreview["missing"]): (typeof ASKABLE)[number][] {
  const wanted = new Set(missing.map((m) => (m === "amount" || m === "currency" ? "money" : m)));
  return ASKABLE.filter((f) => wanted.has(f));
}

/** A one-line account of what the move does besides the stage. */
export function WouldLine({ would, companyName }: { would: StagePreview["would"]; companyName: string | null }) {
  const parts: string[] = [];
  if (would.probability !== undefined) parts.push(`Probability becomes ${would.probability}%.`);
  if (would.company_lead_stage_label && companyName)
    parts.push(`${companyName} will be at ${would.company_lead_stage_label}.`);
  if (would.tasks_closed)
    parts.push(
      `${would.tasks_closed} open task${would.tasks_closed === 1 ? "" : "s"} will be closed with a note.`
    );
  if (parts.length === 0) return null;
  return <p className="text-xs text-muted-foreground">{parts.join(" ")}</p>;
}

export function StageDialog({
  open,
  onOpenChange,
  workspace,
  deal,
  stage,
  stageLabel,
  numberFormat,
  onMoved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  deal: CrmOpportunity;
  stage: StageKey | null;
  stageLabel: string;
  numberFormat: NumberFormat;
  onMoved: (result: StageMoveResult) => void;
}) {
  const [changes, setChanges] = useState<StageChanges>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: preview, error: previewError } = useAsync<StagePreview | null>(
    () => (open && stage ? previewCrmStage(workspace, deal.id, stage) : Promise.resolve(null)),
    [open, stage, workspace, deal.id, deal.updated_at]
  );
  const fields = preview ? asked(preview.missing) : [];
  // A refusal no field here can fix (the TVA link, who may reopen): say it
  // and offer nothing to fill in.
  const blocked = preview !== null && !preview.allowed && fields.length === 0;

  function close(next: boolean) {
    if (!next) {
      setChanges({});
      setError(null);
    }
    onOpenChange(next);
  }

  function set(patch: StageChanges) {
    setChanges((current) => ({ ...current, ...patch }));
  }

  async function move() {
    if (!stage) return;
    setBusy(true);
    setError(null);
    try {
      const result = await moveCrmStage(workspace, deal.id, stage, changes, deal.updated_at);
      onMoved(result);
      close(false);
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
          <DialogTitle>Move to {stageLabel}</DialogTitle>
          <DialogDescription>
            {fields.length > 0
              ? "This stage needs a few more details."
              : `${deal.name} moves from ${deal.stage_label} to ${stageLabel}.`}
          </DialogDescription>
        </DialogHeader>

        {!preview && !previewError && <p className="text-sm text-muted-foreground">Checking…</p>}
        {(previewError || blocked) && (
          <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
            {previewError ?? preview?.message}
          </p>
        )}

        {preview && !blocked && (
          <form
            id="stage-form"
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              move();
            }}
          >
            {fields.map((field) => (
              <div key={field} className="space-y-1.5">
                <Label htmlFor={`stage-${field}`}>{LABELS[field]}</Label>
                {field === "point_of_contact_id" && (
                  <FieldEditor
                    id={`stage-${field}`}
                    spec={{
                      name: field,
                      label: LABELS[field],
                      type: "relation",
                      choices: [],
                      search: (q, after) => searchPersonChoices(workspace, q, after),
                    }}
                    value={changes.point_of_contact_id ?? null}
                    onChange={(value) => set({ point_of_contact_id: (value as string | null) ?? null })}
                  />
                )}
                {field === "money" && (
                  <AmountField
                    id={`stage-${field}`}
                    value={{
                      amount: changes.amount ?? deal.amount ?? null,
                      currency: (changes.currency ?? deal.currency ?? "INR") as CrmCurrency,
                    }}
                    onChange={(money) => set({ amount: money.amount, currency: money.currency })}
                    numberFormat={numberFormat}
                  />
                )}
                {field === "next_step" && (
                  <Input
                    id={`stage-${field}`}
                    maxLength={500}
                    value={changes.next_step ?? ""}
                    onChange={(e) => set({ next_step: e.target.value })}
                  />
                )}
                {(field === "expected_close_on" || field === "proposal_sent_on" || field === "next_step_on") && (
                  <Input
                    id={`stage-${field}`}
                    type="date"
                    value={changes[field] ?? ""}
                    onChange={(e) => set({ [field]: e.target.value || null })}
                  />
                )}
              </div>
            ))}
            <WouldLine would={preview.would} companyName={deal.company_name} />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </form>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          {!blocked && (
            <Button type="submit" form="stage-form" disabled={busy || !preview}>
              {busy ? "Moving…" : `Move to ${stageLabel}`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
