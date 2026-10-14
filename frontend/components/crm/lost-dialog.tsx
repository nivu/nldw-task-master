"use client";

import { useState } from "react";

import { WouldLine } from "@/components/crm/stage-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { moveCrmStage, previewCrmStage } from "@/lib/api/crm";
import type { CrmOpportunity, CrmOption, StageMoveResult, StagePreview } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * Close a deal as lost — spec 007 FR-CRM-PIPE-05, -07, -09, FR-CRM-AUTO-01
 * (automation 9).
 *
 * A lost reason is required; notes are optional. The deal's open tasks close
 * with a note saying why. When this is the company's last open deal and the
 * company is at Deal open with no won deal, the person chooses where it goes:
 * Nurturing (the default) or Disqualified. The server decides when that
 * applies; the preview says so.
 */
export function LostDialog({
  open,
  onOpenChange,
  workspace,
  deal,
  reasons,
  onMoved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  deal: CrmOpportunity;
  reasons: CrmOption[];
  onMoved: (result: StageMoveResult) => void;
}) {
  const [reason, setReason] = useState<string>("");
  const [notes, setNotes] = useState("");
  const [onLoss, setOnLoss] = useState<"nurturing" | "disqualified">("nurturing");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: preview, error: previewError } = useAsync<StagePreview | null>(
    () => (open ? previewCrmStage(workspace, deal.id, "closed_lost") : Promise.resolve(null)),
    [open, workspace, deal.id, deal.updated_at]
  );
  // Only the missing reason may stand between the deal and Lost.
  const blocked =
    preview !== null && !preview.allowed && !preview.missing.includes("lost_reason_id");
  const asksCompany = preview?.would.company_lead_stage === "nurturing";
  const offered = reasons.filter((o) => o.is_active);

  function close(next: boolean) {
    if (!next) {
      setReason("");
      setNotes("");
      setOnLoss("nurturing");
      setError(null);
    }
    onOpenChange(next);
  }

  async function lose() {
    setBusy(true);
    setError(null);
    try {
      const result = await moveCrmStage(
        workspace,
        deal.id,
        "closed_lost",
        { lost_reason_id: reason || null, ...(notes.trim() ? { lost_notes: notes.trim() } : {}) },
        deal.updated_at,
        asksCompany ? onLoss : undefined
      );
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
          <DialogTitle>Mark {deal.name} as lost</DialogTitle>
          <DialogDescription>A lost deal can be reopened by its owner or a workspace admin.</DialogDescription>
        </DialogHeader>
        {(previewError || blocked) && (
          <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
            {previewError ?? preview?.message}
          </p>
        )}
        {!blocked && (
          <form
            id="lost-form"
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              lose();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="lost-reason">Lost reason</Label>
              <Select
                items={offered.map((o) => ({ value: o.id, label: o.label }))}
                value={reason}
                onValueChange={(next) => setReason(next ? String(next) : "")}
              >
                <SelectTrigger id="lost-reason" className="h-8 w-full">
                  <SelectValue placeholder="Choose a reason" />
                </SelectTrigger>
                <SelectContent>
                  {offered.map((o) => (
                    <SelectItem key={o.id} value={o.id}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lost-notes">Notes</Label>
              <Textarea
                id="lost-notes"
                rows={3}
                maxLength={20000}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>
            {asksCompany && (
              <fieldset className="space-y-1.5 text-sm">
                <legend className="mb-1 font-medium">
                  This is {deal.company_name ?? "the company"}&apos;s last open deal. Move it to:
                </legend>
                <label className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="on-loss"
                    className="mt-0.5"
                    checked={onLoss === "nurturing"}
                    onChange={() => setOnLoss("nurturing")}
                  />
                  Nurturing — keep in touch
                </label>
                <label className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="on-loss"
                    className="mt-0.5"
                    checked={onLoss === "disqualified"}
                    onChange={() => setOnLoss("disqualified")}
                  />
                  Disqualified — not a fit
                </label>
              </fieldset>
            )}
            {preview && (
              <WouldLine
                would={{ ...preview.would, company_lead_stage_label: undefined }}
                companyName={deal.company_name}
              />
            )}
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
            <Button type="submit" form="lost-form" variant="destructive" disabled={busy || !preview}>
              {busy ? "Saving…" : "Mark as lost"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
